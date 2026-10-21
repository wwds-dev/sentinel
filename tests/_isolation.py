"""Offline and deny-by-default patches, shared by conftest.py and the quit harness.

conftest.py applies these through pytest fixtures; tests/helpers/quit_harness.py
applies the same ones inside a child process, which has no conftest. Each
function takes a MonkeyPatch-like object (anything with setattr) so the caller
decides how long the patches live.
"""

from __future__ import annotations

import os


class ForbiddenInTests(AssertionError):
    """A test reached the real network or started a real program."""


_LOOPBACK = {"localhost", "127.0.0.1", "::1", "", "0.0.0.0"}


def _is_local_address(address) -> bool:
    if isinstance(address, (str, bytes, os.PathLike)):      # AF_UNIX path
        return True
    host = address[0] if address else ""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return host in _LOOPBACK or host.startswith("127.") or host.startswith("::ffff:127.")


def offline_model_lists(patch) -> None:
    """Serve every provider's model list from its KNOWN_MODELS, never live.

    See conftest._offline_model_lists for why.
    """
    from services.anthropic_client import AnthropicClientWrapper
    from services.deepseek_client import DeepSeekClientWrapper
    from services.gemini_client import GeminiClientWrapper
    from services.kimi_client import KimiClientWrapper
    from services.ollama_client import OllamaClient
    from services.openai_client import OpenAIClientWrapper
    from services.qwen_client import QwenClientWrapper

    known = lambda self: list(self.KNOWN_MODELS)  # noqa: E731
    for client in (
        AnthropicClientWrapper, DeepSeekClientWrapper, GeminiClientWrapper,
        KimiClientWrapper, OllamaClient, OpenAIClientWrapper,
        QwenClientWrapper,
    ):
        patch.setattr(client, "list_models", known)
    patch.setattr(OllamaClient, "model_details", lambda self: {})
    # A test with a live event loop can let the startup scan fire; it must
    # not reach Hugging Face for ratings.
    from services import benchmarks

    def _no_network(url):
        raise RuntimeError("no network in tests")

    patch.setattr(benchmarks, "_get_json", _no_network)
    patch.setattr(benchmarks, "is_stale", lambda *a, **k: False)
    patch.setattr(OllamaClient, "loaded_models", lambda self: [])

    # What is "on this Mac" must not depend on this Mac. The inventory
    # starts pre-seeded from the stubbed Ollama (its KNOWN_MODELS, no
    # sizes) with LM Studio off and nothing on disk, so no test reaches a
    # socket or ~/.lmstudio, and nothing runs discovery on the UI thread.
    from types import SimpleNamespace
    from services import local_models

    lm_off = SimpleNamespace(server_up=lambda: False, model_details=lambda: [])
    real_init = local_models.LocalInventory.__init__
    real_discover = local_models.discover

    def stubbed(*a, **k):
        return real_discover(lm_off, OllamaClient(),
                             memory=lambda: (16e9, 24e9), lms_binary=lambda: None)

    def seeded(self, *a, **k):
        real_init(self, *a, **k)
        self.store(stubbed())

    # A live event loop lets the startup discovery fire; it must not read
    # this Mac either.
    patch.setattr(local_models, "discover", stubbed)

    patch.setattr(local_models.LocalInventory, "__init__", seeded)
    patch.setattr(local_models.LocalInventory, "stale", lambda self, now=None: False)

    # Building a window must never start a measurement run (the tests that
    # check the automatic trigger switch it back on), and the window's
    # rating store must not read this Mac's measured ratings: a store
    # outside the temp folder (tmp_path) is kept in memory only.
    import tempfile
    from pathlib import Path
    from services import local_ratings

    patch.setattr(local_ratings, "AUTOMATIC", False)
    real_store_init = local_ratings.RatingStore.__init__
    temp_root = str(Path(tempfile.gettempdir()).resolve())

    def memory_store(self, path):
        if path is not None and not str(Path(path).resolve()).startswith(temp_root):
            path = None
        real_store_init(self, path)

    patch.setattr(local_ratings.RatingStore, "__init__", memory_store)


def deny_network(patch) -> None:
    """Sockets may connect to loopback only; names other than localhost are not looked up."""
    import socket

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if not _is_local_address(address):
            raise ForbiddenInTests(f"test tried to connect to {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _is_local_address(address):
            raise ForbiddenInTests(f"test tried to connect to {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host not in _LOOPBACK and not str(host).startswith("127."):
            raise ForbiddenInTests(f"test tried to look up {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    patch.setattr(socket.socket, "connect", connect)
    patch.setattr(socket.socket, "connect_ex", connect_ex)
    patch.setattr(socket, "getaddrinfo", getaddrinfo)


def deny_processes(patch) -> None:
    """Starting a program (subprocess, os.system) raises."""
    import subprocess

    def popen_init(self, args, *a, **kw):
        raise ForbiddenInTests(f"test tried to start a program: {args!r}")

    def forbidden_system(command):
        raise ForbiddenInTests(f"test tried to run a shell command: {command!r}")

    patch.setattr(subprocess.Popen, "__init__", popen_init)
    patch.setattr(os, "system", forbidden_system)

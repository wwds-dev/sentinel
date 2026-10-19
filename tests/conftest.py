"""Session-wide isolation for tests that initialize the application database."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest


_TEST_ROOT: Path | None = None


@pytest.fixture(scope="session", autouse=True)
def _session_qapplication():
    """Keep one Qt application alive across modules that construct widgets."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(scope="session", autouse=True)
def _offline_model_lists():
    """Serve every provider's model list from its KNOWN_MODELS, never live.

    The app loads the real `.env`, so with a key present every panel asked the
    provider for its models at construction, and a rename on DeepSeek's side
    (deepseek-v4-flash -> deepseek-flash, Sep 2026) failed the suite with no
    local change. Ollama is the same problem on this machine: what is pulled
    decided which tests passed. Ollama here is a daemon that lists
    KNOWN_MODELS, reports no sizes and has nothing loaded.

    Session scope, because the module-scoped `win` fixtures are built before
    any function-scoped patch would apply. A test of a client's own
    `list_models` would need to undo this. Whether the recommended models
    still exist on the real APIs is `scripts/check_live_models.py`'s job.
    """
    from services.anthropic_client import AnthropicClientWrapper
    from services.deepseek_client import DeepSeekClientWrapper
    from services.gemini_client import GeminiClientWrapper
    from services.kimi_client import KimiClientWrapper
    from services.ollama_client import OllamaClient
    from services.openai_client import OpenAIClientWrapper
    from services.qwen_client import QwenClientWrapper

    known = lambda self: list(self.KNOWN_MODELS)  # noqa: E731
    with pytest.MonkeyPatch.context() as patch:
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
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolated_settings_file():
    """Point Chat's saved defaults at a copy under _TEST_ROOT, and its picks
    at an empty override there.

    Switching Chat's provider saves the selected model, and the tests switch
    providers, so they were rewriting the real config/settings.json. The app
    now writes picks to data/settings.local.json instead, but the tests still
    get neither real file: the copy keeps them on the shipped defaults, and
    the developer's own picks must not decide which tests pass. Both real
    files must end the run byte for byte as they began. Session scope for the
    same reason as the model lists: `win` is built first.
    """
    import hashlib

    import main

    real = (main.SETTINGS_FILE, main.SETTINGS_OVERRIDE_FILE)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None  # noqa: E731
    before = [digest(path) for path in real]
    copy = _TEST_ROOT / "config" / "settings.json"
    copy.parent.mkdir(parents=True, exist_ok=True)
    if main.SETTINGS_FILE.exists():
        shutil.copyfile(main.SETTINGS_FILE, copy)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main, "SETTINGS_FILE", copy)
        patch.setattr(main, "SETTINGS_OVERRIDE_FILE", _TEST_ROOT / "data" / "settings.local.json")
        # The model watch too: a real data folder's adopted models would move
        # recommendations the tests assert on, and a scan would write to it.
        patch.setattr(main, "MODEL_WATCH_FILE", _TEST_ROOT / "data" / "model_watch.json")
        # No ratings unless a test loads them: which picks they produce
        # depends on which keys the real .env holds.
        patch.setattr(main, "RATINGS_CACHE_FILE", _TEST_ROOT / "data" / "lmarena_ratings.json")
        patch.setattr(main, "RATINGS_SNAPSHOT_FILE", _TEST_ROOT / "no_snapshot.json")
        # The About box asks git for the checkout's revision; tests may not run git.
        patch.setattr(main, "build_description", lambda root=None: "Sentinel (test build)")
        yield
    assert [digest(path) for path in real] == before, f"the test run modified {real}"


@pytest.fixture(scope="session", autouse=True)
def _real_chats_and_logs_are_never_written():
    """Saved chats and audit logs go to _TEST_ROOT, and the real ones end the
    run exactly as they began.

    A full run once left three test chats in data/chats/ and "Acme" Nmap lines
    in data/logs/bug_spray_audit.jsonl: both resolve their folder from
    `user_data_base()` at call time, and only the database was redirected.
    """
    import hashlib

    from services import history_store, runtime_paths

    real_base = runtime_paths.user_data_base()

    def listing():
        found = {}
        for folder in (real_base / "data" / "chats", real_base / "data" / "logs"):
            for f in sorted(folder.glob("*")) if folder.exists() else []:
                if f.is_file():
                    found[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
        return found

    before = listing()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runtime_paths, "user_data_base", lambda: _TEST_ROOT)
        patch.setattr(history_store, "user_data_base", lambda: _TEST_ROOT)
        yield
    after = listing()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f"the test run modified the real data folder: {changed}"


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


@pytest.fixture(autouse=True)
def _deny_network_and_processes(request, monkeypatch):
    """Deny by default: a normal test may not leave this machine or run a program.

    Sockets may connect to loopback only, and names other than localhost are
    not looked up. Starting a program (subprocess, os.system) raises unless the
    test is marked `allow_process`; a test that needs a real one says so, and
    `allow_network` lifts the socket rule. Everything else is expected to use
    a fake endpoint (a loopback server) or a stub.

    Qt's own network stack (QNetworkAccessManager) does not go through Python
    sockets and is not covered here.
    """
    import socket
    import subprocess

    if request.node.get_closest_marker("allow_network") is None:
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

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    if request.node.get_closest_marker("allow_process") is None:
        real_popen_init = subprocess.Popen.__init__

        def popen_init(self, args, *a, **kw):
            raise ForbiddenInTests(f"test tried to start a program: {args!r}")

        def forbidden_system(command):
            raise ForbiddenInTests(f"test tried to run a shell command: {command!r}")

        monkeypatch.setattr(subprocess.Popen, "__init__", popen_init)
        monkeypatch.setattr(os, "system", forbidden_system)


# Every key a lookup source switches on. The app loads the real .env, so with
# the operator's keys present a lookup test would quietly call VirusTotal,
# Shodan and the rest for example.com — it did, once, before this fixture.
OSINT_SOURCE_KEYS = (
    "URLSCAN_API_KEY", "VIRUSTOTAL_API_KEY", "OTX_API_KEY", "IPINFO_API_KEY",
    "ABUSEIPDB_API_KEY", "GREYNOISE_API_KEY", "CENSYS_API_KEY", "CENSYS_ORG_ID",
    "CRIMINALIP_API_KEY", "SECURITYTRAILS_API_KEY", "HUNTER_API_KEY",
    "ADDYIO_API_KEY", "HIBP_API_KEY", "SHODAN_API_KEY", "DEHASHED_API_KEY",
    "SNUSBASE_API_KEY", "LEAKCHECK_API_KEY", "INTELX_API_KEY",
    "DOMAINTOOLS_API_KEY", "DOMAINTOOLS_API_USERNAME", "COURTLISTENER_API_KEY",
    "OPENSANCTIONS_API_KEY",
)


@pytest.fixture(scope="session", autouse=True)
def _real_env_file_is_never_written(tmp_path_factory):
    """Save Key writes to a scratch file, and the real .env must end the run
    exactly as it began.

    On 2026-10-08 a Settings test clicked Save Key while the stub meant to
    stop it patched the wrong name, and the operator's HIBP key was replaced
    with test data. The redirect stops that; the hash check makes any other
    route to the real file fail loudly instead of silently.
    """
    import hashlib

    from services.runtime_paths import user_data_base
    from ui import dialogs

    # Content and mode, for the checkout .env and the Application Support one
    # (resolved now, before the per-test home redirect below).
    reals = [
        user_data_base() / ".env",
        Path.home() / "Library" / "Application Support" / "Sentinel" / ".env",
    ]

    def state(path):
        if not path.exists():
            return None
        return (hashlib.sha256(path.read_bytes()).hexdigest(),
                stat.S_IMODE(path.stat().st_mode))

    real = reals[0]
    before = [state(p) for p in reals]
    scratch = tmp_path_factory.mktemp("env") / ".env"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(dialogs, "_osint_env_path", lambda: scratch)
        yield scratch
    after = [state(p) for p in reals]
    for path, was, now in zip(reals, before, after):
        assert now == was, f"the test run modified {path} (content or mode)"


@pytest.fixture(scope="session", autouse=True)
def _no_osint_keys_for_the_session():
    """Session scope too, so the module-scoped windows are built keyless."""
    with pytest.MonkeyPatch.context() as patch:
        for name in OSINT_SOURCE_KEYS:
            patch.delenv(name, raising=False)
        yield


@pytest.fixture(autouse=True)
def _home_is_a_scratch_directory(monkeypatch, tmp_path_factory):
    """No test can reach the real ~/Library/Application Support/Sentinel/.env."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


@pytest.fixture(autouse=True)
def _no_osint_keys(monkeypatch):
    """Per test as well: Save Key writes os.environ, and a test that sets a key
    must not hand it to the next one."""
    for name in OSINT_SOURCE_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_vpn_apps(monkeypatch):
    """Tests never ask the developer's real Mullvad/Proton what state they are in.

    A connected app on the machine running the suite would otherwise refuse the
    kill switch and add warnings to every Connect review. A test that cares
    passes its own apps, or patches `vpn_apps.detect` itself.
    """
    from services import vpn_apps

    monkeypatch.setattr(vpn_apps, "detect", lambda run=None: [])


@pytest.fixture(scope="session", autouse=True)
def _offline_osint_catalog(tmp_path_factory):
    """No OSINT Framework download, and no machine-specific cached copy.

    Trace and Bloodhound append catalogue tools to their prompts from
    `data/cache/`; a developer's cache would make prompt tests depend on the
    week it was fetched. Tests that exercise the catalogue supply their own.
    """
    from services import osint_catalog

    empty = tmp_path_factory.mktemp("osint-catalog") / "osint-framework.json"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(osint_catalog, "_cache_path", lambda: empty)
        patch.setattr(osint_catalog, "refresh_in_background", lambda: False)
        yield


def pytest_configure(config):
    """Redirect SQLite before test modules import application services.

    BASE_DIR deliberately remains the project root so a fresh test database is
    seeded from the same checked-in config as a development launch. Only the
    writable database is redirected.
    """
    global _TEST_ROOT
    _TEST_ROOT = Path(tempfile.mkdtemp(prefix="sentinel-tests-"))
    # VPN state (sites, keys, Tor dir, kill-switch files) must never touch the
    # developer's real folders, and Tunnel tests must not see each other's.
    os.environ["VPN_AGENT_STATE_DIR"] = str(_TEST_ROOT / "vpn-state")

    from services import database

    database.DB_PATH = _TEST_ROOT / "data" / "sentinel.db"
    database.init_db()


def pytest_unconfigure(config):
    global _TEST_ROOT
    if _TEST_ROOT is not None:
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
        _TEST_ROOT = None


@pytest.fixture(scope="module", autouse=True)
def _dispose_windows_a_module_leaked():
    """Destroy the top-level windows a test module created and left behind.

    Building a main window re-applies the global stylesheet to every live
    widget, so each leaked window made every later window slower (the suite
    went from ~11 minutes to stalling for minutes per test around the
    Tunnel tests). Only widgets that did not exist when the module started
    are touched, after the module's own fixtures have finished.
    """
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        yield
        return
    app = QApplication.instance()
    before = {id(w) for w in app.topLevelWidgets()} if app else set()
    yield
    app = QApplication.instance()
    if not app:
        return
    for widget in list(app.topLevelWidgets()):
        if id(widget) in before:
            continue
        try:
            if hasattr(widget, "stop_background_work"):
                widget.stop_background_work()
        except Exception:  # noqa: BLE001
            pass
        try:
            widget.hide()
            widget.deleteLater()
        except Exception:  # noqa: BLE001
            pass
    from PySide6.QtCore import QEvent
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

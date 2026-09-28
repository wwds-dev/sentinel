"""Session-wide isolation for tests that initialize the application database."""

from __future__ import annotations

import os
import shutil
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
        patch.setattr(OllamaClient, "loaded_models", lambda self: [])
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolated_settings_file():
    """Point Chat's saved provider/model defaults at a copy under _TEST_ROOT.

    Switching Chat's provider saves the selected model, and the tests switch
    providers, so they were rewriting the real config/settings.json. Session
    scope for the same reason as the model lists: `win` is built first.
    """
    import main

    copy = _TEST_ROOT / "config" / "settings.json"
    copy.parent.mkdir(parents=True, exist_ok=True)
    if main.SETTINGS_FILE.exists():
        shutil.copyfile(main.SETTINGS_FILE, copy)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main, "SETTINGS_FILE", copy)
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolated_tunnel_audit():
    """Point Tunnel's audit log at _TEST_ROOT instead of data/logs/.

    `data/logs/tunnel_audit.jsonl` is the operator's record of real
    connect/disconnect and kill-switch attempts. Any test that reaches
    `append_audit` without its own `audit_path` (the kill-switch worker tests
    did) was appending made-up "ARMED ok" / "pfctl error" lines to it. Session
    scope for the same reason as above: `win` is built first.
    """
    from services import vpn_execution

    audit = _TEST_ROOT / "data" / "logs" / vpn_execution.AUDIT_FILENAME
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(vpn_execution, "default_audit_path", lambda: audit)
        yield


def pytest_configure(config):
    """Redirect SQLite before test modules import application services.

    BASE_DIR deliberately remains the project root so a fresh test database is
    seeded from the same checked-in config as a development launch. Only the
    writable database is redirected.
    """
    global _TEST_ROOT
    _TEST_ROOT = Path(tempfile.mkdtemp(prefix="sentinel-fork-tests-"))

    from services import database

    database.DB_PATH = _TEST_ROOT / "data" / "sentinel.db"
    database.init_db()


def pytest_unconfigure(config):
    global _TEST_ROOT
    if _TEST_ROOT is not None:
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
        _TEST_ROOT = None

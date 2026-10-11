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
        # A test with a live event loop can let the startup scan fire; it must
        # not reach Hugging Face for ratings.
        from services import benchmarks

        def _no_network(url):
            raise RuntimeError("no network in tests")

        patch.setattr(benchmarks, "_get_json", _no_network)
        patch.setattr(benchmarks, "is_stale", lambda *a, **k: False)
        patch.setattr(OllamaClient, "loaded_models", lambda self: [])
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
        yield
    assert [digest(path) for path in real] == before, f"the test run modified {real}"


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

    real = user_data_base() / ".env"
    digest = lambda: hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    before = digest()
    scratch = tmp_path_factory.mktemp("env") / ".env"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(dialogs, "_osint_env_path", lambda: scratch)
        yield scratch
    assert digest() == before, f"the test run modified {real}"


@pytest.fixture(scope="session", autouse=True)
def _no_osint_keys_for_the_session():
    """Session scope too, so the module-scoped windows are built keyless."""
    with pytest.MonkeyPatch.context() as patch:
        for name in OSINT_SOURCE_KEYS:
            patch.delenv(name, raising=False)
        yield


@pytest.fixture(autouse=True)
def _no_osint_keys(monkeypatch):
    """Per test as well: Save Key writes os.environ, and a test that sets a key
    must not hand it to the next one."""
    for name in OSINT_SOURCE_KEYS:
        monkeypatch.delenv(name, raising=False)


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
    import shiboken6
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
            shiboken6.delete(widget)
        except Exception:  # noqa: BLE001
            pass
    app.processEvents()

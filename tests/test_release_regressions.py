"""Regression checks for release-blocking routing, usage, and history bugs."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


class _TextValue:
    def __init__(self, value):
        self.value = value

    def currentText(self):
        return self.value


class _CheckValue:
    def __init__(self, checked):
        self.checked = checked

    def isChecked(self):
        return self.checked


def _routing_stub(mode: str, qwen_enabled: bool):
    return SimpleNamespace(
        provider_box=_TextValue("qwen"),
        model_box=_TextValue("qwen3-max"),
        execution_mode_box=_TextValue(mode),
        allow_openai_checkbox=_CheckValue(False),
        allow_deepseek_checkbox=_CheckValue(False),
        allow_kimi_checkbox=_CheckValue(False),
        allow_gemini_checkbox=_CheckValue(False),
        allow_anthropic_checkbox=_CheckValue(False),
        allow_qwen_checkbox=_CheckValue(qwen_enabled),
    )


@pytest.mark.parametrize("mode", ["Cloud only", "Hybrid allowed"])
def test_qwen_routes_when_its_permission_is_enabled(mode):
    from main import GodAI

    assert GodAI.resolve_backend_model(_routing_stub(mode, True)) == ("qwen", "qwen3-max")


@pytest.mark.parametrize("mode", ["Cloud only", "Hybrid allowed"])
def test_qwen_is_blocked_when_its_permission_is_disabled(mode):
    from main import GodAI

    with pytest.raises(RuntimeError, match="qwen API is not enabled"):
        GodAI.resolve_backend_model(_routing_stub(mode, False))


def test_two_chats_saved_immediately_never_overwrite(tmp_path):
    from services.history_store import HistoryStore

    history = HistoryStore(tmp_path)
    first = history.save_chat("chat", "ollama", "local", "", [], "first")
    second = history.save_chat("chat", "ollama", "local", "", [], "second")

    assert first != second
    assert first.exists() and second.exists()
    assert len(history.list_chats()) == 2


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("ollama", False),
        ("openai", True),
        ("deepseek", True),
        ("kimi", True),
        ("gemini", True),
        ("anthropic", True),
        ("qwen", True),
    ],
)
def test_usage_cloud_flag_covers_every_supported_provider(tmp_path, monkeypatch, backend, expected):
    from services import usage_tracker as usage_module

    db_path = tmp_path / "usage.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE pricing (
                backend TEXT, model TEXT, input_per_1m_usd REAL,
                cached_input_per_1m_usd REAL, output_per_1m_usd REAL
            );
            CREATE TABLE usage (
                id INTEGER PRIMARY KEY, timestamp TEXT, agent TEXT, backend TEXT,
                model TEXT, project TEXT, input_tokens INTEGER,
                cached_input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER, cost_eur REAL, cost_type TEXT, cloud INTEGER
            );
        """)

    def connect():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(usage_module, "get_connection", connect)
    result = usage_module.UsageTracker().log_request(
        "chat", backend, "model", "prompt", "response"
    )

    with connect() as conn:
        stored = bool(conn.execute("SELECT cloud FROM usage").fetchone()["cloud"])
    assert result["cloud"] is expected
    assert stored is expected


def test_existing_usage_cloud_flags_are_repaired_without_touching_unknown_backends():
    from services.database import _sync_usage_cloud_flags

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage (backend TEXT, cloud INTEGER)")
    conn.executemany(
        "INSERT INTO usage VALUES (?, ?)",
        [("ollama", 1), ("anthropic", 0), ("qwen", 0), ("legacy-provider", 0)],
    )
    _sync_usage_cloud_flags(conn)
    rows = dict(conn.execute("SELECT backend, cloud FROM usage"))
    conn.close()

    assert rows == {
        "ollama": 0,
        "anthropic": 1,
        "qwen": 1,
        "legacy-provider": 0,
    }


def test_frozen_bundle_includes_tunnel_profile_seed():
    spec = (ROOT / "Sentinel.spec").read_text(encoding="utf-8")

    assert "agents/vpn_agent/config/vpn_profiles.json" in spec


def test_canonical_version_has_precise_public_format():
    from services.app_version import APP_VERSION, DISPLAY_VERSION, read_app_version

    checked_in = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[1-9]\d*\.\d{3}", checked_in)
    assert read_app_version(ROOT) == checked_in == APP_VERSION
    assert DISPLAY_VERSION == f"v{checked_in}"


def test_every_distribution_reads_the_canonical_version_file():
    spec = (ROOT / "Sentinel.spec").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_app.sh").read_text(encoding="utf-8")

    assert '("VERSION", ".")' in spec
    assert '("docs/versioning.md", "docs")' in spec
    assert 'Path(SPECPATH) / "VERSION"' in spec
    assert '${PROJECT_ROOT}/VERSION' in installer
    assert 'CFBundleShortVersionString -string "$APP_VERSION"' in installer


def test_installer_migrates_only_sentinel_fork_identity():
    installer = (ROOT / "scripts" / "install_app.sh").read_text(encoding="utf-8")

    assert '/Applications/Sentinel Fork.app' in installer
    assert 'Application Support/Sentinel Fork' in installer
    assert 'Application Support/Sentinel"' in installer
    assert '/Applications/Sentinel AI.app' not in installer
    assert 'Application Support/Sentinel AI' not in installer


def test_launcher_runs_python_inside_the_bundle_without_a_persistent_job():
    """The bundle's executable *is* the interpreter, so the process is named
    Sentinel everywhere macOS shows one. Exec'ing .venv/bin/python — the
    fork-and-exec shim this replaced — made it "python" in Activity Monitor,
    the Dock, Cmd-Tab and Force Quit."""
    installer = (ROOT / "scripts" / "install_app.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "app_launcher.c").read_text(encoding="utf-8")

    assert "xcrun clang" in installer
    assert 'CFBundleExecutable -string "${APP_NAME}"' in installer
    assert "-lpython" in installer
    assert "launchctl submit" not in installer
    assert "Py_InitializeFromConfig" in launcher
    assert "Py_RunMain" in launcher
    assert re.search(r"\bexec[lv]p?e?\(", launcher) is None
    assert "fork(" not in launcher
    assert "launchctl" not in launcher
    # A running copy is asked to quit, never killed: closeEvent must run.
    assert "pkill" not in installer
    assert "terminate" in installer
    assert "com\\.netrunner3000\\.sentinel\\.launch\\." in installer


def test_launcher_source_matches_imprints_copy():
    """One source, two apps. Skipped when Imprint is not checked out beside it."""
    other = ROOT.parent / "imprint" / "scripts" / "app_launcher.c"
    if not other.is_file():
        pytest.skip("imprint checkout not present")
    assert (ROOT / "scripts" / "app_launcher.c").read_bytes() == other.read_bytes()


def test_sidebar_uses_final_product_name():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert 'QLabel("SENTINEL")' in source
    assert 'QLabel("SENTINEL FORK")' not in source


def test_shared_panel_shutdown_prefers_join_contract_and_stops_fallbacks():
    from ui.dialogs import shutdown_panels

    calls = []

    class Joinable:
        def shutdown(self):
            calls.append("joined")

    class Stoppable:
        def is_running(self):
            return True

        def stop(self):
            calls.append("stopped")

    app = SimpleNamespace(panels={"tunnel": Joinable(), "other": Stoppable()})

    shutdown_panels(app)

    assert calls == ["joined", "stopped"]

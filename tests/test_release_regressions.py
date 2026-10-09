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
    stub = SimpleNamespace(
        provider_box=_TextValue("qwen"),
        model_box=_TextValue("qwen3-max"),
        execution_mode_box=_TextValue(mode),
        allow_openai_checkbox=_CheckValue(False),
        allow_deepseek_checkbox=_CheckValue(False),
        allow_kimi_checkbox=_CheckValue(False),
        allow_gemini_checkbox=_CheckValue(False),
        allow_anthropic_checkbox=_CheckValue(False),
        allow_qwen_checkbox=_CheckValue(qwen_enabled),
        settings={},
        notices=[],
    )
    stub._set_route_notice = lambda text: stub.notices.append(text)
    stub._local_default_model = lambda: "deepseek-r1:8b"
    return stub


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


# ── D3: Local only must never send a cloud model name to Ollama ─────────────

def _local_only_stub(provider: str, model: str, saved_ollama: str = "llama3:8b"):
    stub = _routing_stub("Local only", True)
    stub.provider_box = _TextValue(provider)
    stub.model_box = _TextValue(model)
    stub.settings = {"default_model_ollama": saved_ollama}
    stub._local_default_model = lambda: saved_ollama
    return stub


def test_local_only_with_a_cloud_pick_uses_the_saved_ollama_model():
    from main import GodAI

    stub = _local_only_stub("anthropic", "claude-sonnet-5")
    assert GodAI.resolve_backend_model(stub) == ("ollama", "llama3:8b")
    assert stub.notices and "Local only" in stub.notices[-1]
    assert "claude-sonnet-5" in stub.notices[-1]       # the user is told what was swapped


def test_local_only_with_an_ollama_pick_keeps_it_and_clears_the_notice():
    from main import GodAI

    stub = _local_only_stub("ollama", "mistral:7b")
    assert GodAI.resolve_backend_model(stub) == ("ollama", "mistral:7b")
    assert stub.notices == [""]


def test_local_only_never_returns_a_cloud_model_name():
    from main import GodAI

    for provider, model in [("openai", "gpt-4.1-mini"), ("qwen", "qwen3-max"),
                            ("gemini", "gemini-2.5-pro"), ("deepseek", "deepseek-chat")]:
        backend, resolved = GodAI.resolve_backend_model(_local_only_stub(provider, model))
        assert backend == "ollama"
        assert resolved != model


def test_blocked_route_does_not_escape_the_cost_estimate():
    """resolve_backend_model raises for an unticked provider on every
    keystroke; get_current_cost_estimate must absorb it."""
    from main import GodAI

    stub = _routing_stub("Hybrid allowed", False)          # qwen unticked
    stub.input_box = SimpleNamespace(toPlainText=lambda: "hello")
    stub.build_user_prompt = lambda raw: ("General", raw)
    stub.runbar_cost = SimpleNamespace(setText=lambda t: stub.notices.append(f"bar:{t}"),
                                       setToolTip=lambda t: None)
    stub.resolve_backend_model = lambda: GodAI.resolve_backend_model(stub)

    result = GodAI.get_current_cost_estimate(stub)

    assert result == (0.0, 0, None, None)
    assert any("not enabled" in n for n in stub.notices)


# ── One conversation, one saved file ─────────────────────────────────────────

def test_update_chat_rewrites_the_same_file_and_keeps_its_title(tmp_path):
    from services.history_store import HistoryStore
    store = HistoryStore(tmp_path)
    path = store.save_chat(agent="chat", backend="ollama", model="m", command="General Chat",
                           messages=[{"role": "user", "content": "hi"},
                                     {"role": "assistant", "content": "hello"}],
                           response="hello", project="p1")
    data = store.load_chat(str(path))
    data["title"] = "My thread"
    path.write_text(__import__("json").dumps(data))

    store.update_chat(path, messages=[{"role": "user", "content": "hi"},
                                      {"role": "assistant", "content": "hello"},
                                      {"role": "user", "content": "more"},
                                      {"role": "assistant", "content": "sure"}],
                      response="sure", backend="anthropic", model="claude")
    assert store.list_chats() == [path]                     # still one file
    after = store.load_chat(str(path))
    assert after["title"] == "My thread" and after["project"] == "p1"
    assert after["timestamp"] == data["timestamp"]
    assert after["updated"] and after["backend"] == "anthropic"
    assert len(after["messages"]) == 4 and after["response"] == "sure"
    assert not list(tmp_path.glob("*.tmp"))


def _chat_finish_stub(tmp_path):
    from services.history_store import HistoryStore
    stub = SimpleNamespace(
        history=HistoryStore(tmp_path), current_chat_path=None,
        pending_agent="chat", pending_backend="ollama", pending_model="m",
        pending_command="General Chat", pending_project=None,
        current_messages=[], listed=0, failures=[],
    )
    stub.load_history_list = lambda: setattr(stub, "listed", stub.listed + 1)
    stub._note_failure = lambda where, exc: stub.failures.append((where, exc))
    return stub


def test_a_second_turn_updates_the_first_turns_file(tmp_path):
    """The persistence tail of handle_chat_finished, isolated: turn one creates
    a file, turn two rewrites it, and the folder never holds two copies."""
    from main import GodAI
    import inspect
    src = inspect.getsource(GodAI.handle_chat_finished)
    tail = src[src.index("        current_path = getattr(self, \"current_chat_path\", None)"):
               src.index("        self.load_history_list()")]
    stub = _chat_finish_stub(tmp_path)
    ns = {"Path": __import__("pathlib").Path}

    def run_tail(self, response):
        exec("def _t(self, response):\n" + tail, ns)
        ns["_t"](self, response)

    stub.current_messages = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "a"}]
    run_tail(stub, "a")
    first = stub.current_chat_path
    assert first and len(stub.history.list_chats()) == 1

    stub.current_messages += [{"role": "user", "content": "two"}, {"role": "assistant", "content": "b"}]
    run_tail(stub, "b")
    assert stub.current_chat_path == first
    assert len(stub.history.list_chats()) == 1
    assert len(stub.history.load_chat(first)["messages"]) == 4
    assert stub.failures == []


# ── UI notices never reach the model; a tool switch replaces the system prompt ─

def test_ui_notices_are_dropped_from_backend_messages():
    from main import GodAI
    convo = [
        {"role": "system", "content": "You are Writing.", "timestamp": "t"},
        {"role": "user", "content": "hi", "timestamp": "t"},
        {"role": "system", "content": "Chat request stopped by user.", "timestamp": "t", "ui_only": True},
        {"role": "user", "content": "again", "timestamp": "t"},
    ]
    sent = GodAI._backend_messages(convo)
    assert [m["content"] for m in sent] == ["You are Writing.", "hi", "again"]
    assert all(set(m) == {"role", "content"} for m in sent)


def test_notice_helpers_mark_messages_ui_only():
    from main import GodAI
    stub = SimpleNamespace(_message_timestamp=staticmethod(lambda v=None: "2026-10-09T12:00"))
    stub._timestamped_message = lambda *a, **k: GodAI._timestamped_message(stub, *a, **k)
    notice = GodAI._ui_notice(stub, "Chat request stopped by user.")
    assert notice["ui_only"] is True and notice["role"] == "system"
    plain = GodAI._timestamped_message(stub, "user", "hi")
    assert "ui_only" not in plain
    # ...and the flag survives a save/reopen round trip through normalisation.
    back = GodAI._normalise_chat_messages(stub, [notice, plain])
    assert back[0]["ui_only"] is True and "ui_only" not in back[1]


def test_switching_the_tool_mid_chat_replaces_the_system_prompt():
    """The merge step of send_prompt, isolated."""
    from main import GodAI
    import inspect
    src = inspect.getsource(GodAI.send_prompt)
    start = src.index("            prior = list(self.current_messages)")
    end = src.index("            self.pending_messages = prior + fresh") + len("            self.pending_messages = prior + fresh")
    block = "\n".join(line[12:] for line in src[start:end].splitlines())
    stub = SimpleNamespace(_message_timestamp=staticmethod(lambda v=None: "t"))
    stub._timestamped_message = lambda *a, **k: GodAI._timestamped_message(stub, *a, **k)
    stub._normalise_chat_messages = lambda msgs, fallback=None: GodAI._normalise_chat_messages(stub, msgs, fallback)
    stub.current_messages = [
        {"role": "system", "content": "You are Writing.", "timestamp": "t"},
        {"role": "user", "content": "draft this", "timestamp": "t"},
        {"role": "assistant", "content": "Here is a draft.", "timestamp": "t"},
    ]
    ns = {"self": stub, "selected_agent": "chat",
          "messages": [{"role": "system", "content": "You are Coding."},
                       {"role": "user", "content": "now fix this bug"}]}
    exec(block, ns)
    roles = [(m["role"], m["content"]) for m in stub.pending_messages]
    assert roles[0] == ("system", "You are Coding.")           # replaced, not dropped
    assert roles.count(("system", "You are Writing.")) == 0
    assert roles[-1] == ("user", "now fix this bug")
    assert len(roles) == 4


def test_ollama_is_streamed_when_the_client_can():
    from main import GodAI
    calls = []
    class Ollama:
        def stream_chat(self, model, messages):
            calls.append(("stream", model)); yield "tok"
        def chat(self, model, messages):
            calls.append(("chat", model)); return "whole"
    stub = SimpleNamespace(ollama=Ollama(), assess_local_model=lambda m: None)
    result = GodAI.run_backend(stub, "ollama", "llama3", [{"role": "user", "content": "x"}], "x")
    assert list(result) == ["tok"]
    assert calls == [("stream", "llama3")]


# ── Reopening another agent's record must not turn it into a Chat file ──────

def test_only_chat_records_are_continued_in_place(tmp_path):
    from main import GodAI
    path = tmp_path / "x.json"
    assert GodAI._continuable_chat_path({"agent": "chat"}, path) == str(path)
    assert GodAI._continuable_chat_path({}, path) == str(path)            # legacy, no agent field
    for other in ("osint", "osint_heavy", "wifi", "sentry", "bug_bounty", "vpn", "manager"):
        assert GodAI._continuable_chat_path({"agent": other}, path) is None

"""Regressions for the open items of docs/qa_audit/shared.md section 5.

Each test names the audit item (A9, A10, ...) it pins. Fake workers only; no
network, no real model call.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QInputDialog, QLabel, QMessageBox,
    QPushButton, QTextBrowser, QWidget,
)

from services.database import get_connection

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run(run_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


class FakeChatWorker:
    instances: list = []

    def __init__(self, run_backend_func, backend, model, messages, prompt):
        from PySide6.QtCore import QObject, Signal  # noqa: F401
        self.backend, self.model = backend, model
        self.messages, self.prompt = list(messages), prompt
        self.running = False
        self.cancelled = False
        FakeChatWorker.instances.append(self)
        for name in ("status_signal", "token_signal", "finished_signal",
                     "usage_signal", "error_signal"):
            setattr(self, name, SimpleNamespace(connect=lambda *a, **k: None))

    def start(self):
        self.running = True

    def isRunning(self):  # noqa: N802
        return self.running

    def cancel(self):
        self.cancelled = True

    def terminate(self):
        self.running = False

    def wait(self, _ms=0):
        self.running = False
        return True


@pytest.fixture
def win(qapp, monkeypatch):
    import main

    FakeChatWorker.instances.clear()
    warnings: list = []
    monkeypatch.setattr(main, "ChatWorker", FakeChatWorker)
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    window = main.GodAI()
    window.warnings = warnings
    window.assess_local_model = lambda model: None
    window.agent_box.setCurrentText("chat")
    window.execution_mode_box.setCurrentText("Local only")
    idx = window.provider_box.findText("ollama")
    if idx >= 0:
        window.provider_box.setCurrentIndex(idx)
    window.new_chat()
    yield window
    window.chat_worker = None
    window.model_scan_worker = None
    window.model_pull_worker = None
    window._pending_requests.clear()
    # One window per test: without this, 20+ live windows (each with panel
    # timers) pile up and slow every later test in the session.
    try:
        window.shutdown_panels() if hasattr(window, "shutdown_panels") else None
    except Exception:  # noqa: BLE001
        pass
    window.hide()
    import shiboken6
    shiboken6.delete(window)
    QApplication.processEvents()


# ── A6: Chat refuses a keyless cloud provider before any run exists ─────────

def test_a6_chat_blocks_a_cloud_provider_with_no_key(win, monkeypatch):
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    win.provider_box.setCurrentIndex(win.provider_box.findText("openai"))
    win.allow_openai_checkbox.setChecked(True)
    monkeypatch.setattr(win, "provider_key_available", lambda p: p == "ollama")
    win.input_box.setPlainText("hello")
    win.send_prompt()
    assert FakeChatWorker.instances == []
    assert any("API key is not configured" in w for w in win.warnings)
    assert win.active_run_id is None


# ── A14: the estimate and the routing window see the history and system prompt

def test_a14_estimate_covers_system_prompt_and_conversation_so_far(win, monkeypatch):
    seen = []
    real = win.estimate_chat_cost
    monkeypatch.setattr(win, "estimate_chat_cost",
                        lambda b, m, text: seen.append(text) or real(b, m, text))
    win.current_messages = [
        {"role": "user", "content": "EARLIER-QUESTION-MARKER"},
        {"role": "assistant", "content": "EARLIER-ANSWER-MARKER"},
    ]
    win.input_box.setPlainText("the newest question")
    seen.clear()                    # typing triggers the live estimate; Send is under test
    win.send_prompt()
    assert seen, "send_prompt never estimated"
    assert "EARLIER-QUESTION-MARKER" in seen[0] and "EARLIER-ANSWER-MARKER" in seen[0]
    assert "the newest question" in seen[0]
    # And the live run-bar estimate measures the same text.
    seen.clear()
    win.input_box.setPlainText("another one")
    seen.clear()
    win.get_current_cost_estimate()
    assert seen and "EARLIER-ANSWER-MARKER" in seen[0]


def test_a14_routing_window_uses_the_whole_request(win, monkeypatch):
    captured = {}
    import main
    monkeypatch.setattr(main, "route_request",
                        lambda prompt, **kw: captured.update(kw) or (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        win.route_for_request("short", context_text="x" * 4000)
    assert captured["context_tokens"] >= 1000


# ── A10: quitting and Emergency Reset close what is still open ──────────────

class _Thread:
    def __init__(self):
        self.cancelled = False
        self.alive = True

    def isRunning(self):  # noqa: N802
        return self.alive

    def cancel(self):
        self.cancelled = True
        self.alive = False

    def wait(self, _ms=0):
        return True

    def terminate(self):
        self.alive = False


def _authorise_pending(win, request_id="req-1"):
    assert win.authorize_request("sentry", "ollama", "m", "prompt", label="x",
                                 request_id=request_id)
    return win._pending_requests[request_id]["run_id"]


def test_a10_close_event_abandons_pending_requests_and_stops_scan_and_pull(win):
    run_id = _authorise_pending(win)
    assert _run(run_id)["status"] == "running"
    win.model_scan_worker, win.model_pull_worker = _Thread(), _Thread()
    scan, pull = win.model_scan_worker, win.model_pull_worker

    event = QCloseEvent()
    win.closeEvent(event)

    assert event.isAccepted()
    assert _run(run_id)["status"] == "cancelled"
    assert win._pending_requests == {}
    assert scan.cancelled and pull.cancelled


def test_a10_emergency_reset_quiets_everything_before_it_erases(win, monkeypatch):
    from ui import dialogs
    run_id = _authorise_pending(win, "req-reset")
    win.model_scan_worker = _Thread()
    scan = win.model_scan_worker
    state = {}

    def erase():
        state["pending"] = dict(win._pending_requests)
        state["scan_cancelled"] = scan.cancelled
        state["status"] = _run(run_id)["status"]

    monkeypatch.setattr("services.portable_reset.erase_portable_user_data", erase)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("ERASE SENTINEL DATA", True)))
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: None))
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    dialogs.show_settings(win)
    dialog = opened[0]
    next(b for b in dialog.findChildren(QPushButton)
         if b.objectName() == "EmergencyPortableReset").click()
    dialog.reject()

    assert state, "erase was never reached"
    assert state["pending"] == {} and state["scan_cancelled"] is True
    assert state["status"] == "cancelled"


# ── A17: an abandoned panel run records why ─────────────────────────────────

def test_a17_abandon_with_an_error_fills_the_run_log_error_column(win):
    panel = win.panels["sentry"]
    panel._request_id = "req-err"
    assert win.authorize_request("sentry", "ollama", "m", "prompt", label="x",
                                 request_id="req-err")
    run_id = win._pending_requests["req-err"]["run_id"]
    panel.abandon(error="provider exploded: 503")
    row = _run(run_id)
    assert row["status"] == "error"
    assert row["error"] == "provider exploded: 503"


def test_a17_a_blocked_recheck_says_so(win):
    panel = win.panels["sentry"]
    panel._request_id = "req-blk"
    assert win.authorize_request("sentry", "ollama", "m", "prompt", label="x",
                                 request_id="req-blk")
    run_id = win._pending_requests["req-blk"]["run_id"]
    panel.abandon("blocked", error="Blocked by the budget re-check before sending.")
    assert "budget" in _run(run_id)["error"]


# ── A16: escaping, 4 decimals, cap format ───────────────────────────────────

def _dialog_text(monkeypatch, opener, win):
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    opener(win)
    return opened[0].findChild(QTextBrowser).toPlainText(), opened[0]


def test_a16_cost_history_escapes_html_and_shows_four_decimals(qapp, monkeypatch):
    from ui import dialogs
    host = QWidget()
    host.usage_tracker = SimpleNamespace(load_log=lambda: [{
        "timestamp": "t", "agent": "<b>EVILAGENT</b>", "backend": "openai",
        "model": "<script>alert(1)</script>", "input_tokens": 1,
        "output_tokens": 1, "total_tokens": 2, "cost_eur": 0.123456,
        "cost_type": "x",
    }])
    text, dialog = _dialog_text(monkeypatch, dialogs.show_cost_history, host)
    assert "<b>EVILAGENT</b>" in text and "<script>alert(1)</script>" in text
    assert "€0.1235" in text
    assert "Total Cost: €0.1235" in dialog.findChild(QLabel).text() or "€0.1235" in dialog.findChildren(QLabel)[1].text()


def test_a16_run_log_escapes_html(qapp, monkeypatch):
    from ui import dialogs
    host = QWidget()
    host.run_logger = SimpleNamespace(load_recent=lambda n: [{
        "timestamp": "t", "run_id": "r1", "agent": "<i>EVILRUN</i>", "tool": "t",
        "provider": "p", "model": "m", "status": "error", "input_tokens": 0,
        "output_tokens": 0, "cost_eur": 0.0, "duration_sec": 0.0,
        "error": "<b>BOOM</b>",
    }])
    text, _ = _dialog_text(monkeypatch, dialogs.show_run_log, host)
    assert "<i>EVILRUN</i>" in text and "<b>BOOM</b>" in text


@pytest.mark.parametrize("spent, cap, text, level", [
    (1.0, 7.25, "€1.00 / €7.25", "green"),
    (0.0, 0.0, "€0.00 / €0.00", "red"),
    (7.0, 7.25, "€7.00 / €7.25", "red"),
])
def test_a16_budget_meter_prints_the_cap_to_the_cent(spent, cap, text, level):
    from main import budget_meter_state
    fraction, shown, lvl, tip = budget_meter_state(spent, cap, "SESSION")
    assert shown == text and lvl == level
    if cap == 0:
        assert fraction >= 1.0 and "zero cap" in tip


# ── A9: Settings -> Pricing can add a price ─────────────────────────────────

@pytest.fixture
def settings(win, monkeypatch):
    from ui import dialogs
    opened = []
    shown = {"warning": [], "info": []}
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown["warning"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown["info"].append(a[2])))
    dialogs.show_settings(win)
    dialog = opened[0]
    dialog.shown = shown
    yield dialog
    dialog.reject()


def _price_row(dialog):
    return {n: dialog.findChild(QWidget, n) for n in
            ("AddPriceProvider", "AddPriceModel", "AddPriceInput",
             "AddPriceCached", "AddPriceOutput")}


def _price(backend, model):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pricing WHERE backend = ? AND model = ?",
            (backend, model)).fetchone()


def _save(dialog):
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Save All").click()


def test_a9_add_price_row_creates_a_pricing_row(settings):
    row = _price_row(settings)
    assert all(row.values())
    row["AddPriceProvider"].setCurrentText("openai")
    row["AddPriceModel"].setText("gpt-audit-new")
    row["AddPriceInput"].setText("2.5")
    row["AddPriceOutput"].setText("10")
    _save(settings)
    stored = _price("openai", "gpt-audit-new")
    assert stored is not None
    assert stored["input_per_1m_usd"] == 2.5 and stored["output_per_1m_usd"] == 10.0
    assert stored["cached_input_per_1m_usd"] is None
    assert not settings.shown["warning"]


@pytest.mark.parametrize("inp, out", [("0", "10"), ("2", "0"), ("nan", "1"), ("1", "")])
def test_a9_add_price_refuses_zero_or_bad_rates(settings, inp, out):
    row = _price_row(settings)
    row["AddPriceProvider"].setCurrentText("openai")
    row["AddPriceModel"].setText("gpt-audit-bad")
    row["AddPriceInput"].setText(inp)
    row["AddPriceOutput"].setText(out)
    _save(settings)
    assert _price("openai", "gpt-audit-bad") is None
    assert settings.shown["warning"]


def test_a9_a_blank_model_adds_nothing(settings):
    with get_connection() as conn:
        before = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    _save(settings)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0] == before


# ── A18: Learning Centre links and the Sentry exercise ──────────────────────

def test_a18_relative_md_link_switches_topic(win):
    from ui.learning_center import build_learning_center, LEARNING_TOPICS, TOPIC_ROLE
    dialog = build_learning_center(win)
    browser = dialog.findChild(QTextBrowser, "LearningBrowser")
    topics = dialog.findChild(type(dialog.findChildren(QWidget, "LearningTopicList")[0]), "LearningTopicList")
    start = topics.currentItem().data(TOPIC_ROLE)
    target = next(i for i, t in enumerate(LEARNING_TOPICS) if t.filename == "api_keys.md")
    assert start != target
    browser.anchorClicked.emit(QUrl("api_keys.md"))
    assert topics.currentItem().data(TOPIC_ROLE) == target
    # "../testing_roadmap.md" is a lesson too.
    browser.anchorClicked.emit(QUrl("../testing_roadmap.md"))
    assert LEARNING_TOPICS[topics.currentItem().data(TOPIC_ROLE)].filename.endswith("testing_roadmap.md")


def test_a18_topic_index_for_link():
    from ui.learning_center import topic_index_for_link, LEARNING_TOPICS
    i = topic_index_for_link(QUrl("sentry.md#foo"))
    assert LEARNING_TOPICS[i].filename == "sentry.md"
    assert topic_index_for_link(QUrl("https://example.com/x.html")) is None
    assert topic_index_for_link(QUrl("nonexistent.md")) is None


def test_a18_sentry_lesson_has_an_exercise():
    text = (ROOT / "docs" / "training" / "sentry.md").read_text(encoding="utf-8")
    assert "\n## Exercise\n" in text


# ── A12: the permission hints name the real control ─────────────────────────

def test_a12_validator_names_the_real_permission_control():
    from services.validator import Validator
    stub_registry = SimpleNamespace(
        is_agent_enabled=lambda n: True, is_tool_enabled=lambda n: True,
        agent_allows_provider=lambda a, p: True, tool_allows_provider=lambda t, p: True,
        agent_allows_tool=lambda a, t: True, get_agent_budget=lambda n: None,
        get_tool_budget=lambda n: None, agent_requires_approval=lambda n: False,
        tool_requires_approval=lambda n: False,
    )
    result = Validator(stub_registry).validate(
        agent_name="chat", tool_name=None, provider="openai", api_permissions={},
        session_cost=0.0, session_budget=5.0, daily_cost=0.0, daily_budget=5.0,
        estimated_cost=0.01,
    )
    assert not result.allowed
    assert "Paid provider access" in result.reason
    assert "API Permissions panel" not in result.reason

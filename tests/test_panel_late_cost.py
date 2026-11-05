"""A panel's stopped cloud request records its late cost (no-thread-terminate R6).

Chat already bills a request stopped after the provider answered (see
test_chat_late_cost.py). The agent panels start their ChatWorker through
AgentPanel.start_worker and used to drop that report, so a paid answer that
arrived after Stop was never billed. The cost goes through the same usage
tracker and run logger as every other request: one `cancelled-*` row at the
provider's real counts, the run row closed as cancelled keeps the cost, and the
session total moves once.
"""

from __future__ import annotations

import threading
import time

import pytest
import shiboken6
from PySide6.QtWidgets import QApplication, QMessageBox

from services import portable_reset
from services.database import get_connection
from ui import workers


def _runs(run_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def _usage_rows_after(rowid):
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT rowid, * FROM usage WHERE rowid > ? ORDER BY rowid", (rowid,))]


def _last_usage_rowid():
    with get_connection() as conn:
        return conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM usage").fetchone()[0]


def _spin(until, timeout=5.0):
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return False


class Backend:
    def __init__(self):
        self.calls = []

    def __call__(self, backend, model, messages, prompt):
        call = {"entered": threading.Event(), "gate": threading.Event(), "reply": "late"}
        self.calls.append(call)
        call["entered"].set()
        call["gate"].wait(10)
        return call["reply"]


@pytest.fixture
def win(monkeypatch):
    import main

    portable_reset._ERASING.clear()
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    window = main.GodAI()
    window.provider_key_available = lambda provider: True
    window.backend_fake = Backend()
    window.run_backend = window.backend_fake
    window.started = []
    yield window
    portable_reset._ERASING.clear()
    for call in window.backend_fake.calls:
        call["gate"].set()
    for thread in window.started:
        if shiboken6.isValid(thread):
            thread.wait(5000)
    _spin(lambda: not any(t in workers._DETACHED_WORKERS for t in window.started), 2)
    window.hide()
    shiboken6.delete(window)
    QApplication.processEvents()


def _start(win, provider="openai"):
    """Authorise and start one panel request the way a panel does; return its parts."""
    if provider == "ollama":
        win.execution_mode_box.setCurrentText("Local only")
    else:
        win.execution_mode_box.setCurrentText("Hybrid allowed")
        win.allow_openai_checkbox.setChecked(True)
    win.prepare_agent_route = lambda *a, **k: True      # routing advice is not under test
    panel = win.panels["osint"]
    panel._advice_cleared = True
    idx = panel.provider_box.findText(provider)
    assert idx >= 0
    panel.provider_box.setCurrentIndex(idx)
    prompt = "Explain hashing in one line."
    assert panel.authorize(prompt, label="Test") is True
    run_id = win._pending_requests[panel._request_id]["run_id"]
    worker = panel.start_worker([{"role": "user", "content": prompt}], prompt)
    win.started.append(worker)
    assert _spin(lambda: win.backend_fake.calls and win.backend_fake.calls[-1]["entered"].is_set())
    return panel, worker, run_id, win.backend_fake.calls[-1]


def _stop_like_a_panel(panel):
    """What the panels' Stop does: cancel the worker, close the run as cancelled."""
    panel.stop_worker()
    panel.abandon("cancelled")


def _finish_late(worker, call, reply):
    call["reply"] = reply
    call["gate"].set()
    assert worker.wait(5000)
    _spin(lambda: False, 0.3)       # let the queued reports reach the UI thread
    QApplication.processEvents()


def test_a_stopped_cloud_request_records_one_cancelled_exact_row(win):
    panel, worker, run_id, call = _start(win)
    before = _last_usage_rowid()
    session_before = win.session_cost_total
    _stop_like_a_panel(panel)
    _finish_late(worker, call, ("late text", {"input_tokens": 100, "output_tokens": 50}))
    rows = _usage_rows_after(before)
    assert len(rows) == 1 and rows[0]["cost_type"] == "cancelled-exact"
    assert rows[0]["input_tokens"] == 100 and rows[0]["output_tokens"] == 50
    run = _runs(run_id)
    assert run["status"] == "cancelled"
    assert run["input_tokens"] == 100 and run["output_tokens"] == 50
    assert run["cost_eur"] == pytest.approx(rows[0]["cost_eur"]) and run["cost_eur"] > 0
    assert win.session_cost_total == pytest.approx(session_before + rows[0]["cost_eur"])


def test_without_counts_the_row_is_cancelled_estimated(win):
    panel, worker, run_id, call = _start(win)
    before = _last_usage_rowid()
    _stop_like_a_panel(panel)
    _finish_late(worker, call, "a plain late reply with no usage counts")
    assert [r["cost_type"] for r in _usage_rows_after(before)] == ["cancelled-estimated"]
    assert _runs(run_id)["status"] == "cancelled"


def test_a_stop_that_the_panel_closes_only_afterwards_still_bills_once(win):
    """Stop cancels the worker; the panel closes the run when the cancel error arrives."""
    panel, worker, run_id, call = _start(win)
    before = _last_usage_rowid()
    panel.stop_worker()
    worker.error_signal.connect(lambda _e: panel.abandon("cancelled"))
    _finish_late(worker, call, ("late text", {"input_tokens": 7, "output_tokens": 9}))
    rows = _usage_rows_after(before)
    assert len(rows) == 1 and rows[0]["input_tokens"] == 7
    run = _runs(run_id)
    assert run["status"] == "cancelled" and run["output_tokens"] == 9


def test_a_stopped_ollama_request_records_nothing(win):
    panel, worker, run_id, call = _start(win, "ollama")
    before = _last_usage_rowid()
    _stop_like_a_panel(panel)
    _finish_late(worker, call, ("late text", {"input_tokens": 100, "output_tokens": 50}))
    assert _usage_rows_after(before) == []
    assert _runs(run_id)["cost_eur"] in (0, 0.0, None)


def test_a_completed_request_is_billed_once_and_not_again(win):
    """No double billing: the normal record() path bills it; the stopped path stays silent."""
    panel, worker, run_id, call = _start(win)
    before = _last_usage_rowid()
    worker.finished_signal.connect(lambda text: panel.record(text))
    _finish_late(worker, call, ("an answer", {"input_tokens": 11, "output_tokens": 12}))
    rows = _usage_rows_after(before)
    assert len(rows) == 1 and rows[0]["cost_type"] != "cancelled-exact"
    assert _runs(run_id)["status"] == "success"


def test_the_same_report_twice_is_billed_once(win):
    panel, worker, _run_id, call = _start(win)
    before = _last_usage_rowid()
    _stop_like_a_panel(panel)
    _finish_late(worker, call, ("late text", {"input_tokens": 5, "output_tokens": 6}))
    worker.stopped_usage_signal.emit({"usage": {"input_tokens": 5, "output_tokens": 6},
                                      "response_text": "late text"})
    QApplication.processEvents()
    assert len(_usage_rows_after(before)) == 1


def test_a_reset_in_progress_records_nothing(win):
    panel, worker, _run_id, call = _start(win)
    before = _last_usage_rowid()
    _stop_like_a_panel(panel)
    portable_reset._ERASING.set()
    _finish_late(worker, call, ("late text", {"input_tokens": 5, "output_tokens": 6}))
    assert _usage_rows_after(before) == []


def test_the_record_is_written_on_the_main_thread(win, monkeypatch):
    panel, worker, _run_id, call = _start(win)
    threads = []
    real = win.usage_tracker.log_request

    def spy(*a, **k):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)
    monkeypatch.setattr(win.usage_tracker, "log_request", spy)
    _stop_like_a_panel(panel)
    _finish_late(worker, call, ("late text", {"input_tokens": 3, "output_tokens": 4}))
    assert threads == [True]


def test_a_second_request_does_not_take_on_the_first_ones_cost(win):
    panel, first, first_run, first_call = _start(win)
    _stop_like_a_panel(panel)
    win.backend_fake.calls.clear()
    panel2, second, second_run, _second_call = _start(win)
    assert second_run != first_run
    before = _last_usage_rowid()
    _finish_late(first, first_call, ("late text", {"input_tokens": 100, "output_tokens": 50}))
    rows = _usage_rows_after(before)
    assert len(rows) == 1 and rows[0]["input_tokens"] == 100
    assert _runs(first_run)["input_tokens"] == 100
    assert _runs(second_run)["status"] == "running" and not _runs(second_run)["cost_eur"]


def test_a_fenced_worker_records_nothing(win):
    panel, worker, _run_id, call = _start(win)
    before = _last_usage_rowid()
    _stop_like_a_panel(panel)
    worker._sentinel_fenced = True          # what fence_parked() does before a Reset
    _finish_late(worker, call, ("late text", {"input_tokens": 5, "output_tokens": 6}))
    assert _usage_rows_after(before) == []


def test_the_context_belongs_to_the_request_asked_for_not_the_newest(win):
    """Two requests of one agent pending at once: each is billed to its own run."""
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    win.allow_openai_checkbox.setChecked(True)
    win.prepare_agent_route = lambda *a, **k: True
    panel = win.panels["osint"]
    panel._advice_cleared = True
    panel.provider_box.setCurrentIndex(panel.provider_box.findText("openai"))
    assert panel.authorize("first", label="Test")
    first_id = panel._request_id
    assert panel.authorize("second", label="Test")
    second_id = panel._request_id
    first = win.stopped_request_context("osint", first_id)
    second = win.stopped_request_context("osint", second_id)
    assert first["prompt"] == "first" and second["prompt"] == "second"
    assert first["run_id"] != second["run_id"]
    assert win.stopped_request_context("osint", "never-authorised") is None
    win.abandon_request("osint", "cancelled", request_id=first_id)
    win.abandon_request("osint", "cancelled", request_id=second_id)


def test_the_worker_carries_the_context_of_its_own_request(win):
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    win.allow_openai_checkbox.setChecked(True)
    win.prepare_agent_route = lambda *a, **k: True
    panel = win.panels["osint"]
    panel._advice_cleared = True
    panel.provider_box.setCurrentIndex(panel.provider_box.findText("openai"))
    assert panel.authorize("first", label="Test")
    first_id = panel._request_id
    assert panel.authorize("second", label="Test")
    second_id = panel._request_id
    panel._request_id = first_id            # the panel is running the first one
    worker = panel.start_worker([{"role": "user", "content": "first"}], "first")
    win.started.append(worker)
    assert worker._sentinel_run["prompt"] == "first"
    assert worker._sentinel_run["request_id"] == first_id
    worker.cancel()
    for call in win.backend_fake.calls:
        call["gate"].set()
    worker.wait(5000)
    win.abandon_request("osint", "cancelled", request_id=first_id)
    win.abandon_request("osint", "cancelled", request_id=second_id)


def test_a_request_with_no_id_is_not_bound_to_another_pending_run(win):
    """_pending_request_key falls back to the newest request of the agent when
    given no id; the context must not, or a late cost closes and bills someone
    else's run."""
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    win.allow_openai_checkbox.setChecked(True)
    win.prepare_agent_route = lambda *a, **k: True
    panel = win.panels["osint"]
    panel._advice_cleared = True
    panel.provider_box.setCurrentIndex(panel.provider_box.findText("openai"))
    assert panel.authorize("other", label="Test")
    other_id = panel._request_id
    assert win.stopped_request_context("osint", None) is None
    win.abandon_request("osint", "cancelled", request_id=other_id)

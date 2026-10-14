"""A stopped cloud request's late cost is recorded (no-thread-terminate, Q3, A4, task 8).

A real ChatWorker runs a fake backend that blocks until released. After Stop
the provider still answers; the worker reports what it received on
stopped_usage_signal and the window records it as the cost of the cancelled
run: one `cancelled-*` usage row, the run row keeps `cancelled` and gains the
cost. Local (Ollama) requests record nothing. The write happens on the UI
thread and is billed to the request that was stopped, never to the next one.
"""

from __future__ import annotations

import threading
import time

import pytest
import shiboken6
from PySide6.QtWidgets import QApplication, QMessageBox

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
    """run_backend stand-in: each call blocks on its own gate, then returns its reply."""

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

    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    window = main.GodAI()
    window.assess_local_model = lambda model: None
    window.provider_key_available = lambda provider: True
    window.agent_box.setCurrentText("chat")
    window.new_chat()
    window.backend_fake = Backend()
    window.run_backend = window.backend_fake
    window.started = []
    yield window
    for call in window.backend_fake.calls:
        call["gate"].set()
    for thread in window.started:
        if shiboken6.isValid(thread):
            thread.wait(5000)
    _spin(lambda: not any(t in workers._DETACHED_WORKERS for t in window.started), 2)
    window.chat_worker = None
    window.hide()
    shiboken6.delete(window)
    QApplication.processEvents()


def _use(win, provider):
    if provider == "ollama":
        win.execution_mode_box.setCurrentText("Local only")
    else:
        win.execution_mode_box.setCurrentText("Hybrid allowed")
        win.allow_openai_checkbox.setChecked(True)
    idx = win.provider_box.findText(provider)
    assert idx >= 0
    win.provider_box.setCurrentIndex(idx)


def _send(win, text="Explain hashing in one line."):
    calls_before = len(win.backend_fake.calls)
    win.input_box.setPlainText(text)
    win.send_prompt()
    worker = win.chat_worker
    assert worker is not None, "send_prompt started no worker"
    win.started.append(worker)
    deadline = time.monotonic() + 2
    while len(win.backend_fake.calls) == calls_before and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(win.backend_fake.calls) == calls_before + 1
    return worker, win.active_run_id, win.backend_fake.calls[-1]


def _finish_late(worker, call, reply):
    call["reply"] = reply
    call["gate"].set()
    assert worker.wait(5000)
    assert _spin(lambda: worker not in workers._DETACHED_WORKERS)
    QApplication.processEvents()


def test_a_stopped_cloud_request_records_one_cancelled_exact_row(win):
    _use(win, "openai")
    worker, run_id, call = _send(win)
    before = _last_usage_rowid()
    win.stop_chat_worker()
    session_before = win.session_cost_total
    _finish_late(worker, call, ("late text", {"input_tokens": 100, "output_tokens": 50}))
    rows = _usage_rows_after(before)
    assert len(rows) == 1 and rows[0]["cost_type"] == "cancelled-exact"
    assert rows[0]["input_tokens"] == 100 and rows[0]["output_tokens"] == 50
    run = _runs(run_id)
    assert run["status"] == "cancelled"
    assert run["input_tokens"] == 100 and run["output_tokens"] == 50
    assert run["cost_eur"] == pytest.approx(rows[0]["cost_eur"]) and run["cost_eur"] > 0
    assert win.session_cost_total == pytest.approx(session_before + rows[0]["cost_eur"])
    assert win.current_messages[-1]["content"].startswith("Chat request stopped")


def test_without_counts_the_row_is_cancelled_estimated(win):
    _use(win, "openai")
    worker, run_id, call = _send(win)
    before = _last_usage_rowid()
    win.stop_chat_worker()
    _finish_late(worker, call, "a plain late reply with no usage counts")
    rows = _usage_rows_after(before)
    assert [r["cost_type"] for r in rows] == ["cancelled-estimated"]
    assert _runs(run_id)["status"] == "cancelled"


def test_a_stopped_ollama_request_records_nothing(win):
    _use(win, "ollama")
    worker, run_id, call = _send(win)
    before = _last_usage_rowid()
    win.stop_chat_worker()
    _finish_late(worker, call, ("late text", {"input_tokens": 100, "output_tokens": 50}))
    assert _usage_rows_after(before) == []
    assert _runs(run_id)["cost_eur"] in (0, 0.0, None)


def test_the_record_is_written_on_the_main_thread(win, monkeypatch):
    _use(win, "openai")
    worker, _run_id, call = _send(win)
    threads = []
    real = win.usage_tracker.log_request

    def spy(*a, **k):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)
    monkeypatch.setattr(win.usage_tracker, "log_request", spy)
    win.stop_chat_worker()
    _finish_late(worker, call, ("late text", {"input_tokens": 3, "output_tokens": 4}))
    assert threads == [True]


def test_a_second_request_does_not_take_on_the_first_ones_cost(win):
    _use(win, "openai")
    first, first_run, first_call = _send(win)
    win.stop_chat_worker()
    second, second_run, _second_call = _send(win, "A different question.")
    assert second_run != first_run
    before = _last_usage_rowid()
    _finish_late(first, first_call, ("late text", {"input_tokens": 100, "output_tokens": 50}))
    rows = _usage_rows_after(before)
    assert len(rows) == 1 and rows[0]["input_tokens"] == 100
    assert _runs(first_run)["input_tokens"] == 100
    second_row = _runs(second_run)
    assert second_row["status"] == "running" and not second_row["cost_eur"]
    assert win.chat_worker is second and second.isRunning()


def test_the_worker_reports_a_stopped_stream_at_most_once():
    reports = []

    def stream(*_a):
        yield "part one "
        yield {"__usage__": {"input_tokens": 9, "output_tokens": 2}}
        yield "part two"

    worker = workers.ChatWorker(stream, "openai", "m", [], "p")
    worker.stopped_usage_signal.connect(reports.append)
    worker.token_signal.connect(lambda t: worker.cancel())      # cancel after the first token
    worker.run()                                                 # synchronously, no thread
    worker._report_stopped(None, "again")                        # a second report is ignored
    assert reports == [{"usage": None, "response_text": "part one "}]


def test_add_late_cost_touches_only_a_cancelled_row_and_keeps_its_duration():
    from services.run_logger import RunLogger
    log = RunLogger()
    running = log.start("chat", "General Chat", "openai", "m", "Hybrid", "p")
    cancelled = log.start("chat", "General Chat", "openai", "m", "Hybrid", "p")
    time.sleep(0.02)
    log.cancel(cancelled)
    duration = _runs(cancelled)["duration_sec"]
    log.add_late_cost(running, 5, 6, 0.5)
    log.add_late_cost(cancelled, 5, 6, 0.5)
    assert _runs(running)["cost_eur"] in (0, 0.0, None) and _runs(running)["status"] == "running"
    row = _runs(cancelled)
    assert (row["status"], row["input_tokens"], row["output_tokens"], row["cost_eur"]) == \
        ("cancelled", 5, 6, 0.5)
    assert row["duration_sec"] == duration


def test_a_stream_stopped_after_its_last_element_is_still_reported():
    """Review P2-1: the cancel used to be checked only at the top of the stream
    loop, so a Stop after the last element (often the usage sentinel) billed
    the reply with no record."""
    reports, finished = [], []

    def stream(*_a):
        yield "the whole reply"
        yield {"__usage__": {"input_tokens": 9, "output_tokens": 2}}

    worker = workers.ChatWorker(stream, "openai", "m", [], "p")
    worker.stopped_usage_signal.connect(reports.append)
    worker.finished_signal.connect(finished.append)
    # Cancel once the stream is exhausted: after the last element was read.
    gen = stream()

    def exhausted_then_cancel(*_a):
        yield from gen
        worker.cancel()

    worker.run_backend_func = exhausted_then_cancel
    worker.run()                                                 # synchronously, no thread
    assert reports == [{"usage": {"input_tokens": 9, "output_tokens": 2},
                        "response_text": "the whole reply"}]
    assert finished == []

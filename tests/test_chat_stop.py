"""Stop returns at once and nothing follows the notice (no-thread-terminate, M2 and M3, task 7).

A real ChatWorker runs a fake backend that blocks on an Event. Stop must
return in under 100 ms without waiting for or terminating the thread, park
it, and give Send back. When the backend is then released, whatever the
worker still emits (tokens, usage, finished) must change nothing: the notice
stays last, the run row stays cancelled and nothing is billed.

Every thread started here is released and joined in teardown: a parked,
still-running QThread at interpreter exit is the very abort this spec removes.
"""

from __future__ import annotations

import threading
import time

import pytest
import shiboken6
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox

from services.database import get_connection
from ui import workers


def _runs(run_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def _usage_count():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]


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
    """run_backend stand-in: blocks until released, then returns its reply."""

    def __init__(self, reply="late reply text"):
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.reply = reply

    def __call__(self, backend, model, messages, prompt):
        self.entered.set()
        self.gate.wait(10)
        return self.reply


@pytest.fixture
def win(monkeypatch):
    import main

    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    window = main.GodAI()
    window.assess_local_model = lambda model, provider="ollama": None
    window.agent_box.setCurrentText("chat")
    window.execution_mode_box.setCurrentText("Local only")
    idx = window.provider_box.findText("ollama")
    if idx >= 0:
        window.provider_box.setCurrentIndex(idx)
    window.new_chat()
    window.backend_fake = Backend()
    window.run_backend = window.backend_fake
    window.started = []
    yield window
    window.backend_fake.gate.set()
    for thread in window.started:
        if shiboken6.isValid(thread):
            thread.wait(5000)
    _spin(lambda: not any(t in workers._DETACHED_WORKERS for t in window.started), 2)
    window.chat_worker = None
    window.hide()
    shiboken6.delete(window)
    QApplication.processEvents()


@pytest.fixture
def no_terminate(monkeypatch):
    def refuse(self):
        raise AssertionError("terminate() was called")
    monkeypatch.setattr(QThread, "terminate", refuse)


def _send(win):
    win.input_box.setPlainText("Explain hashing in one line.")
    win.send_prompt()
    worker = win.chat_worker
    assert worker is not None
    win.started.append(worker)
    assert win.backend_fake.entered.wait(2)
    return worker, win.active_run_id


def test_m2_stop_returns_at_once_parks_and_gives_send_back(win, no_terminate):
    worker, run_id = _send(win)
    start = time.monotonic()
    win.stop_chat_worker()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"Stop took {elapsed * 1000:.0f} ms"
    assert worker.isRunning()                    # not waited for, not killed
    assert worker in workers._DETACHED_WORKERS
    assert win.chat_worker is None
    assert win.send_btn.isEnabled() and not win.send_btn.isHidden()
    assert _runs(run_id)["status"] == "cancelled"


def test_the_notice_is_local_wording_for_ollama(win, no_terminate):
    _send(win)
    win.stop_chat_worker()
    notice = win.current_messages[-1]["content"]
    assert notice.startswith("Chat request stopped.") and "bill" not in notice


def test_the_notice_names_billing_for_a_cloud_provider():
    import main
    assert "may bill" in main._stop_notice("openai")
    assert "may bill" not in main._stop_notice("ollama")


@pytest.mark.parametrize("worker_saw_the_cancel", [True, False])
def test_m3_nothing_is_shown_or_billed_after_the_stop_notice(win, no_terminate,
                                                             worker_saw_the_cancel):
    worker, run_id = _send(win)
    usage_before = _usage_count()
    win.stop_chat_worker()
    notice = win.current_messages[-1]
    if not worker_saw_the_cancel:
        # A race where the cancel flag is missed: the worker emits tokens,
        # usage and finished in full. Only the detach may stop them.
        worker._cancel_requested = False
    # A (response, usage) tuple: the worker emits tokens, then usage, then finished.
    win.backend_fake.reply = ("late reply text", {"input_tokens": 5, "output_tokens": 7})
    emitted = []
    worker.token_signal.connect(emitted.append)
    win.backend_fake.gate.set()
    assert worker.wait(5000)
    assert _spin(lambda: worker not in workers._DETACHED_WORKERS)
    QApplication.processEvents()
    assert win.current_messages[-1] is notice
    assert "late reply text" not in win.output_box.toPlainText()
    assert _runs(run_id)["status"] == "cancelled"
    assert _usage_count() == usage_before
    assert bool(emitted) is (not worker_saw_the_cancel)   # the race case really emitted


def test_stop_current_task_after_stop_reports_nothing_running(win, no_terminate):
    _send(win)
    win.stop_current_task()
    notices = sum(1 for m in win.current_messages if m.get("content", "").startswith("Chat request stopped"))
    win.stop_current_task()                      # a second Stop: no second notice
    assert sum(1 for m in win.current_messages
               if m.get("content", "").startswith("Chat request stopped")) == notices == 1


def test_m3_tokens_queued_before_stop_are_dropped(win, no_terminate):
    """R7: events the worker posted before Stop, still queued when Stop runs,
    are dropped by the detached-sender flag. The slots stay connected because
    a call delivered after disconnect() has sender() None (PySide6 6.11)."""
    queued = threading.Event()
    hold = threading.Event()

    def stream():
        yield "QUEUED-BEFORE-STOP "
        queued.set()                   # the first token has been emitted
        hold.wait(10)
        yield "after"

    win.backend_fake.reply = stream()
    worker, run_id = _send(win)
    win.backend_fake.gate.set()
    assert queued.wait(2)
    win.stop_chat_worker()             # no event processing in between
    hold.set()
    assert worker.wait(5000)
    assert _spin(lambda: worker not in workers._DETACHED_WORKERS)
    QApplication.processEvents()
    assert "QUEUED-BEFORE-STOP" not in win.output_box.toPlainText()
    assert win.current_messages[-1]["content"].startswith("Chat request stopped")
    assert _runs(run_id)["status"] == "cancelled"

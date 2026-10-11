"""The Chat request lifecycle, end to end, with a fake worker (P0-1 … P0-5).

`send_prompt` → run row opened → tokens stream → finished → usage row and
run row closed; or Stop; or a provider error; or the window closing. These
paths run every time a user presses Enter and, until this file, had no
automated test: the suite covered the pieces around them.

The worker is replaced at the class boundary so no thread starts and no
model is contacted; the test emits the worker's signals itself.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from services.database import get_connection


class FakeChatWorker(QObject):
    """ChatWorker's surface with the thread and the model call removed."""

    token_signal = Signal(str)
    status_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)
    instances: list = []

    def __init__(self, run_backend_func, backend, model, messages, prompt):
        super().__init__()
        self.run_backend_func = run_backend_func
        self.backend, self.model = backend, model
        self.messages, self.prompt = list(messages), prompt
        self.running = False
        self.cancelled = False
        self.terminated = False
        FakeChatWorker.instances.append(self)

    def start(self):
        self.running = True

    def isRunning(self):          # noqa: N802 - Qt name
        return self.running

    def cancel(self):
        self.cancelled = True

    def terminate(self):
        self.terminated = True
        self.running = False

    def wait(self, _ms=0):
        self.running = False
        return True

    # Test-side helpers: what a real worker would do.
    def stream(self, *chunks):
        for chunk in chunks:
            self.token_signal.emit(chunk)

    def finish(self, text, usage=None):
        if usage is not None:
            self.usage_signal.emit(usage)
        self.running = False
        self.finished_signal.emit(text)

    def fail(self, message):
        self.running = False
        self.error_signal.emit(message)


def _runs(run_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def _usage_count():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    """A fresh offscreen window per test, with modal dialogs stubbed and
    the real worker class replaced."""
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
    # The local-model memory pre-flight is not under test here; this machine
    # may have less RAM than the saved default model wants.
    window.assess_local_model = lambda model: None
    # Local only with an Ollama model: no permission, no cost confirmation.
    window.agent_box.setCurrentText("chat")
    window.execution_mode_box.setCurrentText("Local only")
    idx = window.provider_box.findText("ollama")
    if idx >= 0:
        window.provider_box.setCurrentIndex(idx)
    window.new_chat()
    yield window
    window.chat_worker = None


def _send(win, text="Explain hashing versus encryption in one line."):
    win.input_box.setPlainText(text)
    before = _usage_count()
    win.send_prompt()
    assert FakeChatWorker.instances, "send_prompt started no worker"
    worker = FakeChatWorker.instances[-1]
    return worker, win.active_run_id, before


# ── P0-1: Enter sends; a run is opened, streamed, closed and billed ─────────

def test_send_opens_a_run_streams_and_closes_it_billed(win):
    worker, run_id, usage_before = _send(win)

    assert worker.backend == "ollama" and worker.running
    assert win.send_btn.isHidden() and not win.stop_chat_btn.isHidden()
    assert _runs(run_id)["status"] == "running"
    assert worker.messages[-1]["role"] == "user"

    worker.stream("Hashing is one-way; ", "encryption is reversible.")
    worker.finish("Hashing is one-way; encryption is reversible.",
                  usage={"input_tokens": 12, "output_tokens": 9})

    row = _runs(run_id)
    assert row["status"] == "success"
    assert row["input_tokens"] == 12 and row["output_tokens"] == 9
    assert _usage_count() == usage_before + 1
    assert win.active_run_id is None
    assert not win.send_btn.isHidden() and win.send_btn.isEnabled()
    transcript = win.output_box.toPlainText()
    assert "YOU" in transcript and "encryption is reversible" in transcript
    assert win.current_messages[-1]["role"] == "assistant"
    assert win.current_messages[-1].get("timestamp")


# ── P0-2: Stop cancels without freezing and never bills ─────────────────────

def test_stop_cancels_keeps_partial_text_and_bills_nothing(win):
    worker, run_id, usage_before = _send(win)
    worker.stream("Partial answer so far")

    win.stop_current_task()

    assert worker.cancelled is True
    assert _runs(run_id)["status"] == "cancelled"
    assert _usage_count() == usage_before            # nothing billed
    assert win.active_run_id is None
    assert not win.send_btn.isHidden() and win.send_btn.isEnabled()
    transcript = win.output_box.toPlainText()
    assert "Partial answer so far" in transcript
    assert "stopped" in transcript.lower()

    # A straggler from the old worker must not reopen or bill anything.
    worker.finish("late answer", usage={"input_tokens": 99, "output_tokens": 99})
    assert _usage_count() == usage_before
    assert _runs(run_id)["status"] == "cancelled"

    # And the window accepts the next request.
    worker2, run_id2, _ = _send(win, "second question")
    assert run_id2 != run_id and _runs(run_id2)["status"] == "running"


# ── P0-3: a provider error closes the run correctly ─────────────────────────

@pytest.mark.parametrize("message", [
    "Connection timed out after 30 s",
    "401 Unauthorized: invalid API key",
    "429 Too Many Requests",
    "Expecting value: line 1 column 1 (char 0)",
])
def test_a_provider_error_closes_the_run_as_error_and_bills_nothing(win, message):
    worker, run_id, usage_before = _send(win)

    worker.fail(message)

    row = _runs(run_id)
    assert row["status"] == "error"
    assert message in (row["error"] or "")
    assert _usage_count() == usage_before
    assert win.active_run_id is None
    assert not win.send_btn.isHidden() and win.send_btn.isEnabled()
    assert message in win.output_box.toPlainText()


# ── P0-4: closing the window cancels in-flight work ─────────────────────────

def test_closing_the_window_cancels_the_run_and_the_worker(win):
    worker, run_id, usage_before = _send(win)
    worker.stream("half an answer")

    event = QCloseEvent()
    win.closeEvent(event)

    assert event.isAccepted()
    assert worker.cancelled is True and not worker.running
    assert _runs(run_id)["status"] == "cancelled"
    assert _usage_count() == usage_before


# ── P0-5: a disabled cloud permission blocks before anything starts ─────────

def test_an_unticked_cloud_provider_blocks_before_a_worker_or_a_run(win):
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    idx = win.provider_box.findText("openai")
    assert idx >= 0
    win.provider_box.setCurrentIndex(idx)
    win.allow_openai_checkbox.setChecked(False)
    with get_connection() as conn:
        runs_before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    usage_before = _usage_count()
    FakeChatWorker.instances.clear()

    win.input_box.setPlainText("hello cloud")
    win.send_prompt()

    assert FakeChatWorker.instances == []
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs_before
    assert _usage_count() == usage_before
    assert any("not enabled" in w for w in win.warnings)


# ── P0-9: a configured secret never leaves through the Chat path ────────────

CANARY = "CANARY-sk-7f3a9c1e5b2d4086"


def test_secret_canaries_stay_out_of_requests_logs_chats_and_the_screen(win, monkeypatch):
    """Every API key is set to a recognisable value; one full Chat round trip
    runs; the value must appear nowhere the app writes or shows."""
    import sqlite3
    from pathlib import Path

    import conftest
    from services.database import DB_PATH

    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                 "DEEPSEEK_API_KEY", "KIMI_API_KEY", "QWEN_API_KEY",
                 "SHODAN_API_KEY", "VIRUSTOTAL_API_KEY", "HIBP_API_KEY"):
        monkeypatch.setenv(name, f"{CANARY}-{name}")

    worker, run_id, _ = _send(win, "What is a salted hash?")
    worker.stream("A salted hash is ")
    worker.finish("A salted hash is a hash of password plus random salt.",
                  usage={"input_tokens": 8, "output_tokens": 12})

    # 1. what would be sent to the model
    assert CANARY not in repr(worker.messages) and CANARY not in worker.prompt
    # 2. what is on screen and in the live conversation
    assert CANARY not in win.output_box.toPlainText()
    assert CANARY not in repr(win.current_messages)
    # 3. the database, every table
    con = sqlite3.connect(str(DB_PATH))
    dump = "\n".join(con.iterdump())
    con.close()
    assert CANARY not in dump
    # 4. every file the run wrote under the isolated test root
    root = Path(conftest._TEST_ROOT)
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_size < 5_000_000:
            assert CANARY.encode() not in path.read_bytes(), f"secret written to {path}"

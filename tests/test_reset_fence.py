"""Emergency Reset fences parked workers before it erases (no-thread-terminate, Q5, A5, task 9).

A worker parked by Stop can still return while the reset erases the data
folder. Its late report writes nothing (fenced), and the one write a request
makes from its own thread (an OpenAI image) is refused once erasing starts.
"""

from __future__ import annotations

import base64
import inspect
import threading
import time
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtWidgets import QApplication, QMessageBox

from services import portable_reset
from services.database import get_connection
from services.openai_client import OpenAIClientWrapper
from services.runtime_paths import PortableRuntimeError
from ui import dialogs, workers


@pytest.fixture
def latch():
    portable_reset._ERASING.clear()
    yield portable_reset._ERASING
    portable_reset._ERASING.clear()


def _spin(until, timeout=5.0):
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return False


# ── The latch ───────────────────────────────────────────────────────────────

def test_an_image_after_the_latch_raises_and_creates_no_folder(tmp_path, latch):
    png = base64.b64encode(b"\x89PNG fake").decode()
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=png, url=None)],
                               output_format="png", usage=None)
    wrapper = OpenAIClientWrapper.__new__(OpenAIClientWrapper)
    wrapper.api_key = "test"
    wrapper.client = SimpleNamespace(images=SimpleNamespace(generate=lambda **k: response))
    target = tmp_path / "output" / "images"
    latch.set()
    with pytest.raises(PortableRuntimeError):
        wrapper.generate_image("a lighthouse", model="gpt-image-1", output_dir=target)
    assert not target.exists() and not (tmp_path / "output").exists()
    latch.clear()
    wrapper.generate_image("a lighthouse", model="gpt-image-1", output_dir=target)
    assert len(list(target.iterdir())) == 1          # allowed again without the latch


def test_the_erase_sets_the_latch_before_deleting(tmp_path, monkeypatch, latch):
    root = tmp_path / "usb"
    data = root / "Sentinel Data"
    (data / "data").mkdir(parents=True)
    (root / ".sentinel-portable").write_text("")
    seen = []
    real_rmtree = portable_reset.shutil.rmtree
    monkeypatch.setattr(portable_reset, "portable_root", lambda: root)
    monkeypatch.setattr(portable_reset, "user_data_base", lambda: data)
    monkeypatch.setattr(portable_reset.shutil, "rmtree",
                        lambda p: seen.append(portable_reset.writes_allowed()) or real_rmtree(p))
    assert portable_reset.writes_allowed()
    portable_reset.erase_portable_user_data()
    assert seen == [False] and not portable_reset.writes_allowed()


def test_a_refused_reset_leaves_writes_allowed(monkeypatch, latch):
    monkeypatch.setattr(portable_reset, "portable_root", lambda: None)
    with pytest.raises(PortableRuntimeError):
        portable_reset.erase_portable_user_data()
    assert portable_reset.writes_allowed()


def test_emergency_reset_stops_background_work_with_the_fence():
    source = inspect.getsource(dialogs)
    handler = source[source.index("def emergency_reset"):]
    handler = handler[:handler.index("erase_portable_user_data()")]
    assert "stopper(fence=True)" in handler


# ── A fenced worker's late report writes nothing ────────────────────────────

class Backend:
    def __init__(self):
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.reply = ("late text", {"input_tokens": 100, "output_tokens": 50})

    def __call__(self, *_a):
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
    window.assess_local_model = lambda model: None
    window.provider_key_available = lambda provider: True
    window.agent_box.setCurrentText("chat")
    window.execution_mode_box.setCurrentText("Hybrid allowed")
    window.allow_openai_checkbox.setChecked(True)
    window.provider_box.setCurrentIndex(window.provider_box.findText("openai"))
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


def _usage_count():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]


def test_a_fenced_workers_late_report_writes_nothing(win):
    win.input_box.setPlainText("Explain hashing in one line.")
    win.send_prompt()
    worker = win.chat_worker
    win.started.append(worker)
    assert win.backend_fake.entered.wait(2)
    run_id = win.active_run_id
    win.stop_chat_worker()
    win.stop_background_work(fence=True)          # what Emergency Reset does before erasing
    assert worker._sentinel_fenced is True
    before = _usage_count()
    win.backend_fake.gate.set()
    assert worker.wait(5000)
    assert _spin(lambda: worker not in workers._DETACHED_WORKERS)
    QApplication.processEvents()
    assert _usage_count() == before
    with get_connection() as conn:
        row = conn.execute("SELECT status, cost_eur FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row["status"] == "cancelled" and not row["cost_eur"]


# ── Review P2-2: a scan that finished inside the join writes nothing ────────

def test_a_reset_during_the_model_scan_writes_nothing(win, monkeypatch, latch):
    """The scan finishes during the reset's join, so it is not parked and not
    fenced; its result is still queued when the post-erase dialog runs its
    nested event loop. Nothing may land in the erased folder."""
    from PySide6.QtWidgets import QDialog, QInputDialog, QPushButton
    from services import model_watch

    watch_file = win.model_watch.path
    monkeypatch.setattr(model_watch, "list_live", lambda classes: [])
    scan = workers.ModelScanWorker({}, None)
    scan.finished_signal.connect(win._on_model_scan_finished)
    scan.error_signal.connect(win._on_model_scan_error)
    win.model_scan_worker = scan
    win.started.append(scan)
    scan.start()
    assert scan.wait(5000)            # its finished_signal is queued, not yet delivered

    def erase():
        portable_reset._ERASING.set()
        watch_file.unlink(missing_ok=True)

    def post_erase_dialog(*_a, **_k):
        _spin(lambda: False, 0.3)    # the information box's nested event loop

    monkeypatch.setattr(portable_reset, "erase_portable_user_data", erase)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("ERASE SENTINEL DATA", True)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(post_erase_dialog))
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: None))
    recorded = []
    real_record = win.model_watch.record_scan
    monkeypatch.setattr(win.model_watch, "record_scan",
                        lambda *a, **k: recorded.append(True) or real_record(*a, **k))
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    dialogs.show_settings(win)
    next(b for b in opened[0].findChildren(QPushButton)
         if b.objectName() == "EmergencyPortableReset").click()
    opened[0].reject()
    _spin(lambda: False, 0.2)
    assert recorded == [True]          # the queued result did arrive (R7) ...
    assert not watch_file.exists()     # ... and wrote nothing


def test_the_fence_disconnects_the_model_scan_results(win, monkeypatch):
    got = []
    scan = workers.ModelScanWorker({}, None)
    scan.finished_signal.connect(lambda *a: got.append(a))
    win.model_scan_worker = scan
    win.stop_background_work(fence=True)
    scan.finished_signal.emit([], None)
    QApplication.processEvents()
    assert got == []


@pytest.mark.parametrize("write", ["history_save", "history_update", "settings", "model_watch"])
def test_no_data_write_once_erasing_has_started(tmp_path, latch, write):
    from services import history_store, model_watch, settings_store
    store = history_store.HistoryStore(tmp_path / "chats")
    saved = store.save_chat("chat", "ollama", "m", "", [], "r")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    latch.set()
    with pytest.raises(PortableRuntimeError):
        if write == "history_save":
            store.save_chat("chat", "ollama", "m", "", [], "r")
        elif write == "history_update":
            store.update_chat(saved, messages=[], response="changed")
        elif write == "settings":
            settings_store.save_override(tmp_path / "data" / "settings.local.json", "k", "v")
        else:
            model_watch.ModelWatch(tmp_path / "data" / "model_watch.json").save()
    assert sorted(p.name for p in tmp_path.rglob("*")) == before

"""Child process for tests/test_quit_bounded.py (spec no-thread-terminate, M4, A7).

Usage: python quit_harness.py spin|blocked

Builds the real window offscreen in a scratch portable data folder (the parent
sets SENTINEL_PORTABLE_ROOT, HOME and QT_QPA_PLATFORM), sends one Chat message
to a fake backend that never returns, prints `QUIT <time.monotonic()>` once
the worker is inside the call, closes the window the way the tray's Quit does,
and then runs main's real exit path (_run_event_loop). Nothing here talks to
the network or starts a program: the same patches as tests/conftest.py apply.

  spin     the backend is a pure-Python busy loop: it holds the GIL, the
           SONAR failure.
  blocked  the backend waits on an Event that is never set: blocked in C,
           without the GIL.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
REPO = TESTS.parent
sys.path[:0] = [str(REPO), str(TESTS)]

import pytest  # noqa: E402  (MonkeyPatch only)

import _isolation  # noqa: E402

ENTER_TIMEOUT_S = 15


def _fail(reason: str, code: int = 3) -> None:
    print(f"HARNESS-FAILED {reason}", flush=True)
    os._exit(code)


def main(variant: str) -> None:
    patch = pytest.MonkeyPatch()
    _isolation.deny_network(patch)
    _isolation.deny_processes(patch)

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from services.runtime_paths import is_portable, user_data_base

    if not is_portable():
        _fail("SENTINEL_PORTABLE_ROOT is not set: refusing to touch real data")
    app = QApplication([])
    # Native QSettings on macOS may ignore $HOME; INI under the scratch folder
    # does not (the portable branch of main's __main__ block does the same).
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      str(user_data_base() / "config"))

    _isolation.offline_model_lists(patch)
    import main as app_main
    from services import osint_catalog

    patch.setattr(app_main, "build_description", lambda root=None: "Sentinel (test build)")
    patch.setattr(osint_catalog, "refresh_in_background", lambda: False)
    patch.setattr(app_main.GodAI, "start_model_scan", lambda self: None)
    for name in ("warning", "critical", "information"):
        patch.setattr(QMessageBox, name, staticmethod(
            lambda *a, _n=name, **k: print(f"[{_n}] {a[2] if len(a) > 2 else ''}",
                                           file=sys.stderr, flush=True)))
    patch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    window = app_main.GodAI()
    window.assess_local_model = lambda model: None
    window.agent_box.setCurrentText("chat")
    window.execution_mode_box.setCurrentText("Local only")
    index = window.provider_box.findText("ollama")
    if index >= 0:
        window.provider_box.setCurrentIndex(index)
    window.new_chat()

    entered = threading.Event()

    def spin(*_args):
        entered.set()
        n = 0
        while True:                    # never returns, holds the GIL between switches
            n += 1

    def blocked(*_args):
        entered.set()
        threading.Event().wait()       # never returns, waits without the GIL

    window.run_backend = {"spin": spin, "blocked": blocked}[variant]
    window.show()
    settings = QSettings("Sentinel", "Sentinel")
    started = time.monotonic()

    def poll():
        worker = window.chat_worker
        if entered.is_set() and worker is not None and worker.isRunning():
            print(f"QUIT {time.monotonic()!r}", flush=True)
            window.close()             # the tray's Quit (main.py __main__ block)
            return
        if time.monotonic() - started > ENTER_TIMEOUT_S:
            _fail("the fake backend was never entered")
        QTimer.singleShot(10, poll)

    def send():
        window.input_box.setPlainText("Explain hashing in one line.")
        window.send_prompt()
        poll()

    QTimer.singleShot(0, send)
    app_main._run_event_loop(app, window, settings)


if __name__ == "__main__":
    main(sys.argv[1])

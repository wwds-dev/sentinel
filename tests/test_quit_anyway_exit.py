"""Audit tier-2 S4 round 2: Quit anyway flushes settings and stops the Wait timer.

`os._exit` skips Qt's own shutdown, so QSettings (the saved window geometry)
must be sync()ed first, and the polling timer of an earlier "Wait" must not
fire while the window is closing.
"""

from __future__ import annotations

from types import SimpleNamespace

import main


class Settings:
    def __init__(self, log):
        self.log = log

    def setValue(self, key, value):        # noqa: N802
        self.log.append("setValue")

    def sync(self):
        self.log.append("sync")


def test_quit_anyway_syncs_the_settings_before_exiting(monkeypatch):
    log = []
    monkeypatch.setattr(main.os, "_exit", lambda code: log.append(f"exit{code}"))
    main._leave(SimpleNamespace(_quit_anyway=True), Settings(log))
    assert log == ["sync", "exit0"]


def test_a_normal_quit_also_syncs_and_exits(monkeypatch):
    """ADR 0001 (owner decision D1): every quit leaves through os._exit, not
    only Quit anyway, so Qt's teardown never meets a parked thread."""
    log = []
    monkeypatch.setattr(main.os, "_exit", lambda code: log.append(f"exit{code}"))
    main._leave(SimpleNamespace(), Settings(log))
    assert log == ["sync", "exit0"]


def test_after_a_committed_reset_nothing_is_written_back(monkeypatch):
    log = []
    monkeypatch.setattr(main.os, "_exit", lambda code: log.append("exit"))
    main._leave(SimpleNamespace(_quit_anyway=True, _portable_reset_committed=True),
                             Settings(log))
    assert log == ["exit"]                 # no sync into the folder that was just erased


def test_quit_anyway_stops_the_wait_timer(monkeypatch):
    order = []
    timer = SimpleNamespace(stop=lambda: order.append("timer-stopped"))
    win = SimpleNamespace(panels={"vpn": SimpleNamespace(has_privileged_work=lambda: True)},
                          _note_failure=lambda *a: None, _quit_wait_timer=timer,
                          stop_background_work=lambda: order.append("stop"))
    monkeypatch.setattr("ui.dialogs.ask_quit_with_privileged_work", lambda parent: True)
    main.GodAI.closeEvent(win, SimpleNamespace(accept=lambda: order.append("accept"),
                                               ignore=lambda: order.append("ignore")))
    assert order == ["timer-stopped", "stop", "accept"] and win._quit_wait_timer is None

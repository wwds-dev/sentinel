"""Quit while an administrator action runs (audit tier 2, S4).

A QThread that is terminate()d inside a privileged call can die between
"password accepted" and "state recorded". These tests pin that no privileged
worker is ever terminated: it is parked (detach_worker) and the window asks
Wait / Quit anyway first. Qt's behaviour at process exit and a real quit with
the admin dialog open are checked by hand (spec section 9, S4).
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from ui import dialogs, workers
from ui.panels import vpn_gate
from ui.panels.vpn import VpnPanel


class StuckWorker:
    """A running worker that never finishes inside the join timeout."""

    def __init__(self):
        self.cancelled = False
        self.waited = []

    def isRunning(self):          # noqa: N802
        return True

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=0):
        self.waited.append(ms)
        return False

    def terminate(self):
        raise AssertionError("a privileged worker must never be terminated")


def _panel(**overrides):
    base = dict(health_monitor=None, worker=None, _diagnostics_worker=None, _ip_worker=None,
                _dns_worker=None, _connection_worker=None, privacy_tab=None,
                servers_tab=None, apps_tab=None)
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def parked(monkeypatch):
    seen = []
    monkeypatch.setattr(workers, "detach_worker", seen.append)
    monkeypatch.setattr("ui.panels.vpn.detach_worker", seen.append, raising=False)
    monkeypatch.setattr(vpn_gate, "detach_worker", seen.append, raising=False)
    return seen


# ── detach_worker ───────────────────────────────────────────────────────────

def test_detach_worker_drops_its_slots_keeps_it_alive_and_forgets_it_when_done():
    worker = workers.VpnConnectionWorker("arm", {})
    got = []
    worker.finished_signal.connect(got.append)
    worker.error_signal.connect(got.append)

    workers.detach_worker(worker)

    worker.finished_signal.emit({"late": True})
    worker.error_signal.emit("late")
    assert got == []
    assert worker in workers._DETACHED_WORKERS
    worker.finished.emit()                      # QThread.finished: the thread ended
    assert worker not in workers._DETACHED_WORKERS


def test_the_alias_dialog_registry_is_the_shared_one():
    assert dialogs._DETACHED_ALIAS_WORKERS is workers._DETACHED_WORKERS
    calls = [n for n in ast.walk(ast.parse(inspect.getsource(workers).replace("\n    ", "\n    ")))
             if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "terminate"]
    assert calls == []            # the module that parks workers never terminates one


# ── No terminate() on a privileged worker ───────────────────────────────────

def test_panel_shutdown_parks_a_stuck_connection_worker_instead_of_terminating(parked):
    stuck = StuckWorker()
    VpnPanel.shutdown(_panel(_connection_worker=stuck), 10)
    assert stuck.cancelled is True and parked == [stuck]


def test_gated_tab_shutdown_parks_a_stuck_worker_instead_of_terminating(parked):
    stuck = StuckWorker()
    tab = SimpleNamespace(_worker=stuck)
    vpn_gate.GatedTab.shutdown(tab, 10)
    assert stuck.cancelled is True and parked == [stuck]


def test_a_connection_worker_that_finishes_in_time_is_not_parked(parked):
    class Quick(StuckWorker):
        def wait(self, ms=0):
            return True
    VpnPanel.shutdown(_panel(_connection_worker=Quick()), 10)
    assert parked == []


# ── has_privileged_work ─────────────────────────────────────────────────────

def test_panel_reports_a_running_connection_worker_as_privileged_work():
    assert VpnPanel.has_privileged_work(_panel(_connection_worker=StuckWorker())) is True
    assert VpnPanel.has_privileged_work(_panel()) is False
    idle = StuckWorker()
    idle.isRunning = lambda: False
    assert VpnPanel.has_privileged_work(_panel(_connection_worker=idle)) is False


def test_panel_reports_a_running_gated_tab_worker_as_privileged_work():
    tab = SimpleNamespace(has_privileged_work=lambda: True)
    assert VpnPanel.has_privileged_work(_panel(privacy_tab=tab)) is True
    assert vpn_gate.GatedTab.has_privileged_work(SimpleNamespace(_worker=StuckWorker())) is True
    assert vpn_gate.GatedTab.has_privileged_work(SimpleNamespace(_worker=None)) is False


def test_privileged_work_running_asks_every_panel_and_survives_a_broken_one():
    def broken():
        raise RuntimeError("boom")
    app = SimpleNamespace(panels={"a": SimpleNamespace(has_privileged_work=broken),
                                  "b": SimpleNamespace(),
                                  "c": SimpleNamespace(has_privileged_work=lambda: True)})
    assert dialogs.privileged_work_running(app) is True
    assert dialogs.privileged_work_running(SimpleNamespace(panels={})) is False
    assert dialogs.privileged_work_running(SimpleNamespace()) is False


# ── closeEvent ──────────────────────────────────────────────────────────────

def _window(running: bool, order: list):
    win = SimpleNamespace(panels={"vpn": SimpleNamespace(has_privileged_work=lambda: running)},
                          _note_failure=lambda *a: None)
    win.stop_background_work = lambda: order.append("stop")
    win._wait_for_privileged_work = lambda: order.append("wait-timer")
    return win


def _event(order):
    return SimpleNamespace(accept=lambda: order.append("accept"),
                           ignore=lambda: order.append("ignore"))


def test_close_with_a_privileged_action_running_asks_and_wait_keeps_the_window(monkeypatch):
    from main import GodAI
    order = []
    monkeypatch.setattr("ui.dialogs.ask_quit_with_privileged_work",
                        lambda parent: order.append("ask") or False)
    GodAI.closeEvent(_window(True, order), _event(order))
    assert order == ["ask", "ignore", "wait-timer"]          # nothing stopped, nothing closed


def test_close_with_quit_anyway_stops_background_work_and_accepts(monkeypatch):
    from main import GodAI
    order = []
    monkeypatch.setattr("ui.dialogs.ask_quit_with_privileged_work",
                        lambda parent: order.append("ask") or True)
    win = _window(True, order)
    GodAI.closeEvent(win, _event(order))
    assert order == ["ask", "stop", "accept"]
    # No per-quit marker any more: every quit leaves through main._leave and
    # os._exit (ADR 0001; tests/test_quit_anyway_exit.py), so nothing to set.
    assert not hasattr(win, "_quit_anyway")


def test_close_with_nothing_privileged_running_does_not_ask(monkeypatch):
    from main import GodAI
    order = []
    monkeypatch.setattr("ui.dialogs.ask_quit_with_privileged_work",
                        lambda parent: (_ for _ in ()).throw(AssertionError("asked")))
    win = _window(False, order)
    GodAI.closeEvent(win, _event(order))
    assert order == ["stop", "accept"]
    assert not getattr(win, "_quit_anyway", False)


def test_the_prompt_defaults_to_wait(monkeypatch):
    """The dialog's safe button is Wait, for Enter and for Escape alike."""
    from PySide6.QtWidgets import QMessageBox
    shown = {}

    def fake_exec(self):
        shown["default"] = self.defaultButton().text()
        shown["escape"] = self.escapeButton().text()
        shown["buttons"] = sorted(b.text() for b in self.buttons())
        self.done(0)
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert dialogs.ask_quit_with_privileged_work(None) is False
    assert shown == {"default": "Wait", "escape": "Wait", "buttons": ["Quit anyway", "Wait"]}


def test_emergency_reset_refuses_while_an_administrator_action_runs():
    source = inspect.getsource(dialogs)
    handler = source[source.index("def emergency_reset"):]
    handler = handler[:handler.index("_portable_reset_committed = True")]
    assert "privileged_work_running(app)" in handler
    assert handler.index("privileged_work_running(app)") < handler.index("stop_background_work")


def test_the_cancel_docstring_no_longer_claims_shutdown_waits_for_privileged_calls():
    doc = inspect.getdoc(workers.VpnConnectionWorker.cancel)
    assert "waits for the call to finish" not in doc
    assert "detach" in doc

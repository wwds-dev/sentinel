"""D6 regression: Tunnel's connection worker must be cancellable.

``VpnPanel.shutdown()`` calls ``cancel()`` on every running worker before the
window closes or Emergency Reset erases data. ``VpnConnectionWorker`` had no
such method, so closing Sentinel during a Connect raised ``AttributeError``
inside shutdown — and Emergency Reset, which caught only ``OSError``, aborted
half-way and reported nothing.
"""

from __future__ import annotations

import inspect

from ui import workers
from ui.panels.vpn import VpnPanel


def _worker_classes_touched_by_shutdown():
    """The worker classes VpnPanel.shutdown() may cancel and join."""
    return [
        workers.VpnConnectionWorker,
        workers.VpnDiagnosticsWorker,
        workers.IpSnapshotWorker,
        workers.DnsLeakWorker,
    ]


def test_every_tunnel_worker_has_the_shutdown_contract():
    for cls in _worker_classes_touched_by_shutdown():
        assert callable(getattr(cls, "cancel", None)), f"{cls.__name__} lacks cancel()"
        assert callable(getattr(cls, "wait", None)), f"{cls.__name__} lacks wait()"


def test_cancelled_connection_worker_drops_its_result(monkeypatch):
    from services import vpn_connection, vpn_execution

    monkeypatch.setattr(vpn_connection, "arm_killswitch", lambda profile: (True, "ARMED"))
    monkeypatch.setattr(vpn_execution, "record_killswitch", lambda *a, **k: None)
    worker = workers.VpnConnectionWorker("arm", {"endpoint": "203.0.113.7"})
    emitted = []
    worker.finished_signal.connect(emitted.append)
    worker.error_signal.connect(emitted.append)

    worker.cancel()
    worker.run()                      # synchronously, no thread

    assert emitted == []


def test_uncancelled_connection_worker_still_reports(monkeypatch):
    from services import vpn_connection, vpn_execution

    monkeypatch.setattr(vpn_connection, "arm_killswitch", lambda profile: (True, "ARMED"))
    monkeypatch.setattr(vpn_execution, "record_killswitch", lambda *a, **k: None)
    worker = workers.VpnConnectionWorker("arm", {"endpoint": "203.0.113.7"})
    emitted = []
    worker.finished_signal.connect(emitted.append)

    worker.run()

    assert emitted and emitted[0]["success"] is True


def test_panel_shutdown_uses_the_real_connection_worker_class():
    # The panel's shutdown touches whatever class it was configured with; make
    # sure the default is the real worker, so the contract test above covers it.
    assert VpnPanel.connection_worker_class is workers.VpnConnectionWorker


def test_emergency_reset_reports_any_failure():
    """A reset that fails for a non-OSError must show 'Reset refused', not
    fall through as if the data had been erased."""
    from ui import dialogs
    source = inspect.getsource(dialogs)
    handler = source[source.index("Erase the complete Sentinel Data folder"):]
    handler = handler[:handler.index("_portable_reset_committed = True")]
    assert "except Exception" in handler
    assert "Reset refused" in handler


def test_a_cancelled_sentry_pass_reports_nothing(monkeypatch):
    """Stop during a persisted pass: run_watch gets should_stop, and a result
    that arrives after cancel is dropped instead of being rendered."""
    from agents.sentry.sentry import engine
    seen = {}

    def fake_run_watch(should_stop=None, **kwargs):
        seen["should_stop"] = should_stop
        return {"baseline_established": False, "findings": [{"severity": "alert"}]}
    monkeypatch.setattr(engine, "run_watch", fake_run_watch)
    worker = workers.SentryWatchWorker(persist=True)
    emitted = []
    worker.finished_signal.connect(emitted.append)
    worker.cancel()
    worker.run()
    assert emitted == []
    assert seen["should_stop"]() is True


def test_close_joins_the_model_scan_worker(monkeypatch):
    from types import SimpleNamespace
    from main import GodAI
    joined = []

    class Thread:
        def __init__(self, name):
            self.name, self.cancelled = name, False
        def isRunning(self):
            return True
        def cancel(self):
            self.cancelled = True
        def wait(self, ms):
            joined.append(self.name); return True
        def terminate(self):
            raise AssertionError("joined cleanly, must not terminate")

    monkeypatch.setattr("ui.dialogs.shutdown_panels", lambda app: joined.append("panels"))
    win = SimpleNamespace(chat_worker=None, model_scan_worker=Thread("scan"),
                          model_pull_worker=Thread("pull"), _note_failure=lambda *a: None)
    event = SimpleNamespace(accept=lambda: joined.append("accept"))
    GodAI.closeEvent(win, event)
    assert joined == ["scan", "pull", "panels", "accept"]
    assert win.model_scan_worker.cancelled is True


def test_one_panel_failing_to_shut_down_does_not_stop_the_others():
    from types import SimpleNamespace
    from ui.dialogs import shutdown_panels
    order, noted = [], []

    def bad():
        raise RuntimeError("no cancel()")
    app = SimpleNamespace(
        panels={"vpn": SimpleNamespace(shutdown=bad),
                "bug_bounty": SimpleNamespace(shutdown=lambda: order.append("bug_bounty"))},
        _note_failure=lambda where, exc: noted.append(where),
    )
    shutdown_panels(app)
    assert order == ["bug_bounty"]
    assert noted == ["shutdown: vpn"]

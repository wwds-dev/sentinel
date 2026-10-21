"""HealthMonitor.stop never waits and never terminates (spec no-thread-terminate, M5, task 4).

stop() used to quit each check thread, wait up to 1.5 s for it, terminate it
and then wait without a limit, on the UI thread, every time the Tunnel panel
was hidden. Now a check still running is handed with its worker to the
injected park and left to end on its own, unheard.
"""

from __future__ import annotations

import inspect
import threading
import time

import pytest
import shiboken6
from PySide6.QtWidgets import QApplication

from agents.vpn_agent.services.health_monitor import HealthMonitor
from ui import workers


class FakeThread:
    def __init__(self):
        self.quit_called = False

    def isRunning(self):                       # noqa: N802
        return True

    def quit(self):
        self.quit_called = True

    def wait(self, *_a):
        raise AssertionError("stop() must never wait")

    def terminate(self):
        raise AssertionError("stop() must never terminate")


class FakeSignal:
    def __init__(self):
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True


class FakeWorker:
    def __init__(self):
        self.result = FakeSignal()


def _spin(until, timeout=3.0):
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return False


def test_a_stuck_check_is_parked_with_its_worker_and_never_waited_on():
    parked = []
    monitor = HealthMonitor(30, park=lambda *objs: parked.append(objs))
    thread, worker = FakeThread(), FakeWorker()
    monitor._threads.append((thread, worker))
    monitor.stop()
    assert parked == [(thread, worker)]
    assert thread.quit_called and worker.result.disconnected
    assert monitor._threads == []


@pytest.fixture
def gate():
    release = threading.Event()
    yield release
    release.set()


def test_stop_is_immediate_and_a_late_result_warns_nothing(gate):
    """Real _spawn wiring, real parking (R5: HealthMonitor's own deleteLater
    and the registry's release act on the same objects)."""
    warnings = []
    monitor = HealthMonitor(30, park=workers.park)
    monitor.warning.connect(warnings.append)
    monitor._last_dns = ["192.0.2.1"]
    entered = threading.Event()

    def slow_dns():
        entered.set()
        gate.wait(10)
        return ["198.51.100.7"]                 # changed: would warn if heard

    monitor._spawn(slow_dns, on_result=monitor._on_dns)
    thread, _worker = monitor._threads[0]
    assert entered.wait(2)
    start = time.monotonic()
    monitor.stop()
    assert time.monotonic() - start < 0.05
    assert thread in workers._DETACHED_WORKERS
    gate.set()
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)
    assert warnings == []
    if shiboken6.isValid(thread):
        assert thread.wait(2000)


def test_without_an_injected_park_the_monitor_keeps_the_pair_until_finished(gate):
    monitor = HealthMonitor(30)
    entered = threading.Event()

    def slow():
        entered.set()
        gate.wait(10)
        return []

    monitor._spawn(slow, on_result=monitor._on_dns)
    pair = monitor._threads[0]
    assert entered.wait(2)
    monitor.stop()
    assert monitor._parked == [pair]
    gate.set()
    assert _spin(lambda: monitor._parked == [])


def test_the_tunnel_panel_injects_the_shared_park():
    from ui.panels import vpn
    source = inspect.getsource(vpn.VpnPanel._build_health_monitor)
    assert "park=park" in source and vpn.park is workers.park


def test_a_spawned_check_delivers_its_result():
    """A11 #3: the old _spawn kept only the thread; its _Worker had no Python
    reference and was collected at once, so no check ever ran (0 of 20 in the
    build log) and each thread idled in its event loop. Holding the pair fixes it."""
    got = []
    monitor = HealthMonitor(30)
    for _ in range(5):
        monitor._spawn(lambda: ["198.51.100.7"], on_result=got.append)
    assert _spin(lambda: len(got) == 5)
    assert _spin(lambda: monitor._threads == [])

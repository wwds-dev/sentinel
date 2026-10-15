"""Panel shutdown parks stuck workers against the shared deadline (no-thread-terminate, M5, task 5).

Tunnel's diagnostic workers, Bloodhound's investigation and file search and
Bug Spray's model worker are cancelled, joined against one deadline and
parked if still running. None is ever terminated (the fakes raise if it is),
and the total wait is the deadline, not a timeout per worker.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from ui import workers
from ui.panels.bug_bounty import BugBountyPanel
from ui.panels.osint_heavy import OsintHeavyPanel
from ui.panels.vpn import VpnPanel


class Stuck:
    """A running worker whose wait(ms) sleeps ms and reports it still running."""

    def __init__(self):
        self.cancelled = False

    def isRunning(self):                       # noqa: N802
        return True

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=0):
        time.sleep(ms / 1000)
        return False

    def terminate(self):
        raise AssertionError("a worker must never be terminated")


@pytest.fixture
def parked(monkeypatch):
    seen = []
    monkeypatch.setattr(workers, "detach_worker", seen.append)
    monkeypatch.setattr("ui.panels.vpn.detach_worker", seen.append)
    return seen


def _timed(call, budget):
    start = time.monotonic()
    call(time.monotonic() + budget)
    return time.monotonic() - start


def test_tunnel_parks_its_diagnostic_workers_within_the_deadline(parked):
    stuck = [Stuck() for _ in range(4)]
    panel = SimpleNamespace(health_monitor=None, worker=stuck[0], _diagnostics_worker=stuck[1],
                            _ip_worker=stuck[2], _dns_worker=stuck[3], _connection_worker=None,
                            privacy_tab=None, servers_tab=None, apps_tab=None)
    elapsed = _timed(lambda d: VpnPanel.shutdown(panel, 2000, deadline=d), 0.2)
    assert parked == stuck and all(w.cancelled for w in stuck)
    assert elapsed < 0.35                      # one 0.2 s budget, not 4 x 2 s


def test_tunnel_without_a_deadline_still_bounds_by_its_timeout(parked):
    stuck = Stuck()
    panel = SimpleNamespace(health_monitor=None, worker=None, _diagnostics_worker=stuck,
                            _ip_worker=None, _dns_worker=None, _connection_worker=None,
                            privacy_tab=None, servers_tab=None, apps_tab=None)
    start = time.monotonic()
    VpnPanel.shutdown(panel, 100)
    assert parked == [stuck] and time.monotonic() - start < 0.3


def test_bloodhound_parks_its_investigation_and_file_search(parked):
    collector, search = Stuck(), Stuck()
    panel = SimpleNamespace(worker=collector, _file_search_worker=search, _collecting=False)
    elapsed = _timed(lambda d: OsintHeavyPanel.shutdown(panel, 2000, deadline=d), 0.2)
    assert parked == [collector, search] and collector.cancelled and search.cancelled
    assert elapsed < 0.35


def test_bug_spray_parks_its_model_worker_and_bounds_its_process_waits(parked):
    feed_waits = []
    model = Stuck()
    panel = SimpleNamespace(program_feed=SimpleNamespace(shutdown=feed_waits.append),
                            _nmap_stop_timer=lambda: None, _nmap_process=None, worker=model)
    elapsed = _timed(lambda d: BugBountyPanel.shutdown(panel, 2000, deadline=d), 0.2)
    assert parked == [model] and model.cancelled
    assert feed_waits and feed_waits[0] <= 200     # the feed's wait comes out of the budget
    assert elapsed < 0.35


def test_the_privileged_connection_join_keeps_its_own_timeout(parked):
    """D2 (owner decision): the privileged join is outside the shared budget,
    so even an expired deadline leaves it its full timeout_ms."""
    waited = []

    class Connection(Stuck):
        def wait(self, ms=0):
            waited.append(ms)
            return False

    connection = Connection()
    panel = SimpleNamespace(health_monitor=None, worker=None, _diagnostics_worker=None,
                            _ip_worker=None, _dns_worker=None, _connection_worker=connection,
                            privacy_tab=None, servers_tab=None, apps_tab=None)
    VpnPanel.shutdown(panel, 1500, deadline=time.monotonic() - 1)
    assert waited == [1500] and parked == [connection]

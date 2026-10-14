"""Closing the window joins background work against one budget (no-thread-terminate, task 6).

The sequential joins used to add up to about 25 s (more with an unbounded
wait) when nothing would stop. Now the model scan and pull and every panel's
non-privileged workers share QUIT_JOIN_BUDGET_S, and whatever outlasts it is
parked. The fakes' wait(ms) really sleeps ms, so a per-site timeout shows up
in the elapsed time.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import main
from ui import dialogs, workers
from ui.panels.bug_bounty import BugBountyPanel
from ui.panels.osint_heavy import OsintHeavyPanel
from ui.panels.vpn import VpnPanel


class Stuck:
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


class PanelStub:
    """A panel's attributes, run through the real panel class's shutdown."""

    def __init__(self, cls, **attrs):
        self.__dict__.update(attrs)
        self._cls = cls

    def shutdown(self, timeout_ms=2000, *, deadline=None):
        self._cls.shutdown(self, timeout_ms, deadline=deadline)

    def has_privileged_work(self):
        return False


@pytest.fixture
def parked(monkeypatch):
    seen = []
    monkeypatch.setattr(workers, "detach_worker", seen.append)
    monkeypatch.setattr("ui.panels.vpn.detach_worker", seen.append)
    return seen


def _window(stuck):
    vpn = PanelStub(VpnPanel, health_monitor=None, worker=stuck(), _diagnostics_worker=stuck(),
                    _ip_worker=stuck(), _dns_worker=stuck(), _connection_worker=None,
                    privacy_tab=None, servers_tab=None, apps_tab=None)
    hound = PanelStub(OsintHeavyPanel, worker=stuck(), _file_search_worker=stuck(),
                      _collecting=False)
    spray = PanelStub(BugBountyPanel, program_feed=SimpleNamespace(shutdown=lambda ms: None),
                      _nmap_stop_timer=lambda: None, _nmap_process=None, worker=stuck())
    win = SimpleNamespace(chat_worker=stuck(), active_run_id=None,
                          model_scan_worker=stuck(), model_pull_worker=stuck(),
                          panels={"vpn": vpn, "osint_heavy": hound, "bug_bounty": spray},
                          _pending_requests={}, _note_failure=lambda *a: None)
    win._detach_chat_worker = lambda worker: None
    win.stop_background_work = lambda **kw: main.GodAI.stop_background_work(win, **kw)
    return win


def test_close_event_returns_within_the_budget_and_parks_everything_stuck(parked):
    win = _window(Stuck)
    accepted = []
    start = time.monotonic()
    main.GodAI.closeEvent(win, SimpleNamespace(accept=lambda: accepted.append(True),
                                               ignore=lambda: None))
    elapsed = time.monotonic() - start
    assert accepted == [True]
    assert elapsed <= main.QUIT_JOIN_BUDGET_S + 0.3, f"{elapsed:.2f} s"
    assert len(parked) == 9                    # scan, pull, 4 Tunnel, 2 Bloodhound, 1 Bug Spray
    assert win.chat_worker is None             # the chat request is parked by _detach_chat_worker


def test_the_budget_leaves_room_inside_the_five_second_quit():
    assert 0 < main.QUIT_JOIN_BUDGET_S <= 2.5


def test_shutdown_panels_passes_the_deadline_only_where_it_is_accepted():
    seen = []

    class WithDeadline:
        def shutdown(self, timeout_ms=2000, *, deadline=None):
            seen.append(("deadline", deadline))

    class Plain:
        def shutdown(self):
            seen.append(("plain", None))

    dialogs.shutdown_panels(SimpleNamespace(panels={"a": WithDeadline(), "b": Plain()}), 123.0)
    assert seen == [("deadline", 123.0), ("plain", None)]


def test_fence_marks_parked_workers_only_when_asked(monkeypatch):
    fenced = []
    monkeypatch.setattr(main, "fence_parked", lambda: fenced.append(True))
    win = _window(lambda: None)
    main.GodAI.stop_background_work(win)
    assert fenced == []
    main.GodAI.stop_background_work(win, fence=True)
    assert fenced == [True]


def test_every_join_comes_out_of_the_given_deadline(parked):
    win = _window(Stuck)
    start = time.monotonic()
    main.GodAI.stop_background_work(win, deadline=start + 0.2)
    assert time.monotonic() - start < 0.4      # the chat join included

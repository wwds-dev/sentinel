"""The parking primitive in ui/workers.py (spec no-thread-terminate, A2, task 1).

A worker that cannot be stopped in time is parked: kept referenced until its
thread ends, then released on the UI thread. Nothing here ever terminates a
thread; every real thread a test starts is released and joined in teardown.
"""

from __future__ import annotations

import threading
import time

import pytest
import shiboken6
from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QApplication

from ui import workers


class Blocked(QThread):
    """A real thread that runs until its gate opens."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        self.entered.set()
        self.gate.wait(10)


def _spin(until, timeout=3.0):
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def threads():
    started = []
    yield started
    for thread in started:
        thread.gate.set()
        if shiboken6.isValid(thread):
            thread.wait(5000)
    _spin(lambda: not any(t in workers._DETACHED_WORKERS for t in started), 2)


@pytest.fixture
def release_spy(monkeypatch):
    seen = []
    real = workers._release_parked

    def spy(thread):
        seen.append((threading.current_thread() is threading.main_thread(),
                     thread.isRunning() if shiboken6.isValid(thread) else None))
        real(thread)
    monkeypatch.setattr(workers, "_release_parked", spy)
    return seen


def _start(threads):
    thread = Blocked()
    threads.append(thread)
    thread.start()
    assert thread.entered.wait(2)
    return thread


def test_release_runs_on_the_main_thread_after_finished_from_a_real_thread(threads, release_spy):
    thread = _start(threads)
    workers.park(thread)
    assert thread in workers._DETACHED_WORKERS
    thread.gate.set()
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)
    assert release_spy == [(True, False)]      # UI thread, and the thread had returned


def test_a_thread_that_has_not_finished_is_never_released(threads, release_spy):
    thread = _start(threads)
    workers.park(thread)
    _spin(lambda: False, 0.2)
    assert thread in workers._DETACHED_WORKERS and release_spy == []
    thread.gate.set()
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)


def test_a_running_thread_is_kept_even_if_release_is_asked_for(threads):
    thread = _start(threads)
    workers.park(thread)
    workers._release_parked(thread)            # a stray call while it still runs
    assert thread in workers._DETACHED_WORKERS
    thread.gate.set()
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)


def test_companions_are_kept_until_the_thread_is_released(threads):
    thread = _start(threads)
    companion = QObject()
    workers.park(thread, companion)
    assert workers._PARKED_COMPANIONS[id(thread)] == (companion,)
    thread.gate.set()
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)
    assert id(thread) not in workers._PARKED_COMPANIONS
    from PySide6.QtCore import QEvent
    QApplication.instance().sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(companion)    # handed to deleteLater with the thread


def test_a_thread_that_finished_before_it_was_parked_is_still_released(threads):
    thread = _start(threads)
    thread.gate.set()
    assert thread.wait(2000)
    workers.park(thread)
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)


def test_detach_worker_drops_result_slots_and_parks(threads):
    thread = Blocked()
    thread.finished_signal = None              # Blocked has no result signals; fine
    threads.append(thread)
    thread.start()
    assert thread.entered.wait(2)
    workers.detach_worker(thread)
    assert thread in workers._DETACHED_WORKERS
    thread.gate.set()
    assert _spin(lambda: thread not in workers._DETACHED_WORKERS)


# ── join_or_park ────────────────────────────────────────────────────────────

class Fake:
    """wait(ms) sleeps ms and reports the thread still running, unless quick."""

    def __init__(self, quick=False):
        self.quick = quick
        self.cancelled = False
        self.waited = []

    def isRunning(self):                       # noqa: N802
        return True

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=0):
        self.waited.append(ms)
        if self.quick:
            return True
        time.sleep(ms / 1000)
        return False

    def terminate(self):
        raise AssertionError("never terminate")


def test_join_or_park_cancels_all_shares_one_deadline_and_parks_the_stuck(monkeypatch):
    parked = []
    monkeypatch.setattr(workers, "detach_worker", parked.append)
    stuck_a, stuck_b, quick = Fake(), Fake(), Fake(quick=True)
    start = time.monotonic()
    result = workers.join_or_park([stuck_a, None, quick, stuck_b], start + 0.3)
    elapsed = time.monotonic() - start
    assert all(w.cancelled for w in (stuck_a, stuck_b, quick))
    assert parked == [stuck_a, stuck_b] and result == [stuck_a, stuck_b]
    assert elapsed < 0.45                      # one deadline, not 0.3 s per worker
    assert stuck_b.waited[0] <= 50             # the second only gets what is left


def test_join_or_park_skips_idle_workers(monkeypatch):
    parked = []
    monkeypatch.setattr(workers, "detach_worker", parked.append)
    idle = Fake()
    idle.isRunning = lambda: False
    assert workers.join_or_park([idle], time.monotonic() + 1) == []
    assert idle.cancelled is False and idle.waited == []


# ── fence_parked ────────────────────────────────────────────────────────────

def test_fence_marks_every_parked_thread_and_companion(threads):
    first, second = _start(threads), _start(threads)
    companion = QObject()
    workers.park(first)
    workers.park(second, companion)
    workers.fence_parked()
    assert first._sentinel_fenced and second._sentinel_fenced and companion._sentinel_fenced


# ── A11 #2: where a lambda without a context object runs ───────────────────

def test_a_lambda_with_no_context_is_delivered_on_the_main_thread(threads):
    """Characterisation, settling A11 #2 for PySide6 6.11.2: a lambda connected
    with no context object to a QThread's `finished` is *not* called in the
    emitting thread; PySide delivers it on the thread the QThread object lives
    in (here the main thread). So the old detach_worker's release lambda was
    already queued. Parking does not rely on this: it connects a slot of a
    main-thread QObject, which Qt itself guarantees is queued. If a PySide
    upgrade flips this test, nothing in the app depends on it."""
    seen = []
    thread = Blocked()
    threads.append(thread)
    thread.finished.connect(lambda: seen.append(threading.current_thread() is threading.main_thread()))
    thread.start()
    thread.gate.set()
    assert thread.wait(2000)
    _spin(lambda: bool(seen), 1)
    assert seen == [True]

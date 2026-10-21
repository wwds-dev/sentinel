"""Quit is bounded (spec no-thread-terminate, M4, A7).

A child process builds the real window offscreen, sends a Chat message to a
backend that never returns, and quits through the window's close, as the
tray's Quit does (tests/helpers/quit_harness.py). The process must end within
5 s of the quit, with exit code 0 and without Qt's "Destroyed while thread is
still running" abort. A run that outlives the hard timeout is killed and
reported as hung.

Modelled on SONAR's quit-mid-fetch test. `spin` holds the GIL in a pure-Python
loop (the SONAR freeze); `blocked` waits in C without it. The default suite
runs each once; the 20-run check carries the `slow` marker.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "tests" / "helpers" / "quit_harness.py"
QUIT_LIMIT_S = 5.0
HARD_TIMEOUT_S = 30
SPIN_RUNS = 20


def _run_once(tmp: Path, variant: str) -> dict:
    """One child run: returns its exit code, how long after QUIT it ended, and its output."""
    (tmp / "home").mkdir(parents=True)
    (tmp / "root").mkdir()
    env = {                                        # constructed, never inherited
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp / "home"),
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(REPO),
        "SENTINEL_PORTABLE_ROOT": str(tmp / "root"),
        "VPN_AGENT_STATE_DIR": str(tmp / "vpn-state"),
    }
    try:
        done = subprocess.run([sys.executable, str(HARNESS), variant], env=env, cwd=str(tmp),
                              capture_output=True, text=True, timeout=HARD_TIMEOUT_S)
    except subprocess.TimeoutExpired as hung:
        out = hung.stdout.decode() if isinstance(hung.stdout, bytes) else (hung.stdout or "")
        err = hung.stderr.decode() if isinstance(hung.stderr, bytes) else (hung.stderr or "")
        return {"code": None, "after_quit": None, "stdout": out, "stderr": err}
    ended = time.monotonic()
    quit_at = None
    for line in done.stdout.splitlines():
        if line.startswith("QUIT "):
            quit_at = float(line.split()[1])
    return {"code": done.returncode,
            "after_quit": None if quit_at is None else ended - quit_at,
            "stdout": done.stdout, "stderr": done.stderr}


def _check(run: dict) -> None:
    assert run["code"] is not None, f"hung: killed after {HARD_TIMEOUT_S} s\n{run['stderr'][-2000:]}"
    assert "QUIT " in run["stdout"], f"never reached the quit\n{run['stdout']}\n{run['stderr'][-2000:]}"
    assert run["code"] == 0, f"exit code {run['code']}\n{run['stderr'][-2000:]}"
    assert run["after_quit"] <= QUIT_LIMIT_S, f"took {run['after_quit']:.2f} s after the quit"
    assert "Destroyed while thread is still running" not in run["stderr"]
    assert "qFatal" not in run["stderr"]


@pytest.mark.allow_process
@pytest.mark.parametrize("variant", ["spin", "blocked"])
def test_quit_with_a_request_in_flight_ends_the_process_in_time(tmp_path, variant):
    _check(_run_once(tmp_path, variant))


@pytest.mark.slow
@pytest.mark.allow_process
def test_quit_holding_the_gil_ends_in_time_every_time(tmp_path):
    """M4 in full: 20 runs of the GIL-holding variant. On the code before the
    fix one in two or three runs hung. About 90 s; run with `-m slow` at
    Verify and Release (owner decision D5)."""
    for attempt in range(SPIN_RUNS):
        _check(_run_once(tmp_path / f"run{attempt}", "spin"))

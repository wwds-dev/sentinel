"""Audit tier-2, step S3: the Sentry background watch tells the truth.

A packaged build cannot run the plist's ``main.py --headless``, so install
refuses there; elsewhere the result is checked with `launchctl print`, not the
exit code of `launchctl load`. launchctl is always a fake here.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agents.sentry.sentry import watchd

PRINT_OK = """gui/501/com.sentinel.sentry.watch = {
\tactive count = 0
\tstate = waiting
\tlast exit code = 0
}
"""
PRINT_RUNNING = PRINT_OK.replace("state = waiting", "state = running").replace(
    "last exit code = 0", "last exit code = (never exited)")
PRINT_FAILED = PRINT_OK.replace("last exit code = 0", "last exit code = 78")


class Launchctl:
    """Records every launchctl call and answers from a table."""

    def __init__(self, load=(0, "", ""), print_=(0, PRINT_OK, ""), listed=""):
        self.calls, self.load, self.print_, self.listed = [], load, print_, listed

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        verb = argv[1] if len(argv) > 1 else ""
        if verb == "list":
            code, out, err = 0, self.listed, ""
        elif verb == "print":
            code, out, err = self.print_
        elif verb == "load":
            code, out, err = self.load
        else:
            code, out, err = 0, "", ""
        if isinstance(code, Exception):
            raise code
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr=err)

    def verbs(self):
        return [call[1] for call in self.calls]


@pytest.fixture
def launchd(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SENTRY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    fake = Launchctl()
    monkeypatch.setattr(watchd.subprocess, "run", fake)
    fake.home = tmp_path
    return fake


# ── packaged builds ─────────────────────────────────────────────────────────

def test_install_refuses_in_a_packaged_build_and_touches_nothing(launchd, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    result = watchd.install(300)
    assert result["ok"] is False
    assert "packaged app" in result["message"] and "developer mode" in result["message"]
    assert launchd.calls == []                                   # launchctl never called
    assert not (launchd.home / "Library" / "LaunchAgents" / f"{watchd.LABEL}.plist").exists()


def test_the_refusal_message_never_claims_the_watch_is_running(launchd, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "running" not in watchd.install(300)["message"].lower()


def test_status_reports_a_packaged_build(launchd, monkeypatch):
    assert watchd.status()["frozen"] is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert watchd.status()["frozen"] is True


# ── verifying a checkout install ────────────────────────────────────────────

def test_a_loaded_job_is_verified_with_launchctl_print(launchd):
    result = watchd.install(300)
    assert result["ok"] is True and result["verified"] is True
    assert launchd.verbs() == ["list", "load", "print"]
    assert launchd.calls[-1] == ["launchctl", "print", f"gui/{os.getuid()}/{watchd.LABEL}"]
    assert "loaded" in result["message"] and "last run: ok" in result["message"]


def test_a_job_that_has_not_run_yet_says_so(launchd):
    launchd.print_ = (0, PRINT_RUNNING, "")
    result = watchd.install(300)
    assert result["ok"] is True and "not yet run" in result["message"]


def test_load_exit_zero_but_job_missing_is_not_success(launchd):
    launchd.print_ = (113, "", "Could not find service")
    result = watchd.install(300)
    assert result["ok"] is False and result["verified"] is False
    assert "not loaded" in result["message"]
    assert "installed and running" not in result["message"]


def test_a_failed_last_run_is_reported_as_a_failure(launchd):
    launchd.print_ = (0, PRINT_FAILED, "")
    result = watchd.install(300)
    assert result["ok"] is False and "78" in result["message"] and "failed" in result["message"]


@pytest.mark.parametrize("print_", [
    (0, "something else entirely", ""),
    (FileNotFoundError("launchctl"), "", ""),
    (subprocess.TimeoutExpired("launchctl", 10), "", ""),
])
def test_an_unverifiable_result_says_not_verified(launchd, print_):
    launchd.print_ = print_
    result = watchd.install(300)
    assert result["ok"] is False and result["verified"] is False
    assert "not verified" in result["message"]
    assert "installed and running" not in result["message"]


def test_a_failed_load_is_still_reported(launchd):
    launchd.load = (5, "", "Load failed: 5: Input/output error")
    result = watchd.install(300)
    assert result["ok"] is False and "Load failed" in result["message"]
    assert "print" not in launchd.verbs()


def test_the_plist_test_in_the_sentry_suite_still_holds(launchd):
    plist = watchd.build_plist(300)
    assert plist["Label"] == watchd.LABEL and plist["ProgramArguments"][-1] == "--headless"

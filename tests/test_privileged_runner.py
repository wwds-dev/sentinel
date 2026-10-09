"""The privileged runner: Sentinel's gated actions use the macOS dialog, never
a cached sudo ticket, and a command that already ran is never run twice."""

from __future__ import annotations

from functools import partial

import pytest

from agents.vpn_agent.services import privileged


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(command, timeout=privileged.DEFAULT_TIMEOUT):
        seen.append(command)
        if command[0] == "sudo":
            return False, "sudo: a password is required"
        return True, "ran: " + command[-1][-40:]
    monkeypatch.setattr(privileged, "run", fake_run)
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 501)
    return seen


def test_gated_actions_skip_sudo_entirely(calls):
    ok, out = privileged.run_as_root("wg-quick up wg0", "Connect", allow_cached_sudo=False)
    assert ok
    assert [c[0] for c in calls] == ["osascript"]
    assert "with administrator privileges" in calls[0][-1]


def test_companion_default_still_tries_cached_sudo_first(calls):
    privileged.run_as_root("pfctl -f x", "Arm")
    assert [c[0] for c in calls] == ["sudo", "osascript"]


@pytest.mark.parametrize("output", [
    "wg-quick: `wg0' already exists. password was not the problem",
    "openvpn: cannot open auth-user-pass file; sudo this instead",
    "exited 1",
])
def test_a_command_that_ran_and_failed_is_never_rerun_through_the_dialog(monkeypatch, output):
    seen = []

    def fake_run(command, timeout=privileged.DEFAULT_TIMEOUT):
        seen.append(command[0])
        return (False, output) if command[0] == "sudo" else (True, "")
    monkeypatch.setattr(privileged, "run", fake_run)
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 501)
    ok, out = privileged.run_as_root("wg-quick up wg0", "Connect")
    assert ok is False and out == output
    assert seen == ["sudo"]


@pytest.mark.parametrize("refusal", [
    "sudo: a password is required",
    "sudo: sorry, you must have a tty to run sudo",
    "Sudo: A terminal is required to read the password",
])
def test_sudo_s_own_refusal_falls_back_to_the_dialog(monkeypatch, refusal):
    seen = []

    def fake_run(command, timeout=privileged.DEFAULT_TIMEOUT):
        seen.append(command[0])
        return (False, refusal) if command[0] == "sudo" else (True, "done")
    monkeypatch.setattr(privileged, "run", fake_run)
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 501)
    assert privileged.run_as_root("x", "p") == (True, "done")
    assert seen == ["sudo", "osascript"]


def test_sentinel_callers_are_wired_dialog_only():
    from services import openvpn_manager, vpn_connection
    for module in (vpn_connection, openvpn_manager):
        runner = module._default_run_as_root
        assert isinstance(runner, partial)
        assert runner.func is privileged.run_as_root
        assert runner.keywords == {"allow_cached_sudo": False}


def test_killswitch_goes_through_the_shared_runner(monkeypatch):
    from agents.vpn_agent.services import killswitch
    seen = {}

    def fake(script, prompt, timeout=None, *, allow_cached_sudo=True):
        seen.update(script=script, allow=allow_cached_sudo)
        return True, "ok"
    monkeypatch.setattr(privileged, "run_as_root", fake)
    assert killswitch._run_privileged("pfctl -d", "Disarm") == (True, "ok")
    assert seen["allow"] is False and seen["script"] == "pfctl -d"

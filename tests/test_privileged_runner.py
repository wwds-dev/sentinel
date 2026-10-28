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


def test_the_dialog_path_gives_the_person_time_to_type_the_password(monkeypatch):
    seen = {}

    def fake_run(command, timeout=privileged.DEFAULT_TIMEOUT):
        seen["timeout"] = timeout
        return True, "ok"
    monkeypatch.setattr(privileged, "run", fake_run)
    privileged.run_with_dialog("true", "Test", 30)
    assert seen["timeout"] == 30 + privileged.PASSWORD_ENTRY_SECONDS


def test_a_dialog_timeout_does_not_claim_nothing_happened(monkeypatch):
    monkeypatch.setattr(privileged, "run", lambda command, timeout=0: (False, "Timed out."))
    ok, message = privileged.run_with_dialog("wg-quick up wg0", "Test", 30)
    assert ok is False and "may still have happened" in message


# ── Audit Low 10: no gated action runs while Sentinel itself is root ──

@pytest.fixture
def as_root(monkeypatch):
    seen = []
    monkeypatch.setattr(privileged, "run",
                        lambda command, timeout=privileged.DEFAULT_TIMEOUT: seen.append(command)
                        or (True, ""))
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 0)
    return seen


@pytest.mark.parametrize("call", [
    lambda: privileged.run_as_root("pfctl -d", "Disarm", allow_cached_sudo=False),
    lambda: privileged.run_argv_as_root(["/sbin/ifconfig", "en5"], "p", allow_cached_sudo=False),
], ids=["script", "argv"])
def test_a_gated_action_is_refused_while_sentinel_runs_as_root(as_root, call):
    assert call() == (False, privileged.ROOT_REFUSAL)
    assert as_root == []                       # nothing ran, not even the dialog


def test_the_refusal_says_what_happened_and_what_to_do():
    text = privileged.ROOT_REFUSAL
    assert "running as root" in text and "Nothing was changed" in text
    assert "normal user" in text and "sudo" in text


def test_the_companion_default_still_runs_directly_as_root(as_root):
    assert privileged.run_as_root("true", "p") == (True, "")
    assert privileged.run_argv_as_root(["/usr/bin/true"], "p") == (True, "")
    assert as_root == [["bash", "-c", "true"], ["/usr/bin/true"]]


def test_connect_disconnect_and_the_kill_switch_are_refused_as_root(as_root):
    from agents.vpn_agent.services import killswitch
    from services import openvpn_manager, vpn_connection
    assert killswitch._run_privileged("pfctl -d", "Disarm") == (False, privileged.ROOT_REFUSAL)
    for module in (vpn_connection, openvpn_manager):
        assert module._default_run_as_root("wg-quick up wg0", "Connect") == (
            False, privileged.ROOT_REFUSAL)
    assert as_root == []


def test_a_mac_change_is_refused_as_root_and_leaves_no_record(as_root, monkeypatch, tmp_path):
    from agents.vpn_agent.services import macaddr
    from agents.vpn_agent.services.macaddr import Interface
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(macaddr, "boot_id", lambda: "1")
    monkeypatch.setattr(macaddr, "get_interface", lambda d: Interface(
        device=d, port="USB 10/100/1000 LAN", current="aa:bb:cc:00:00:01",
        hardware="aa:bb:cc:00:00:01"))
    monkeypatch.setattr(macaddr, "current_mac", lambda d: "aa:bb:cc:00:00:01")
    ok, message = macaddr.set_mac("en5", "02:11:22:33:44:55")
    assert ok is False and message.startswith(privileged.ROOT_REFUSAL)
    assert as_root == [] and macaddr.recorded() == {}
    assert not macaddr.record_path().exists()


def test_a_native_deploy_is_refused_as_root_before_the_installer_is_written(
        monkeypatch, tmp_path):
    import os
    from agents.vpn_agent.server import deploy
    from agents.vpn_agent.server.model import MODE_NATIVE, default_site
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    streamed = []
    monkeypatch.setattr(deploy, "_stream", lambda *a, **k: streamed.append(a))
    monkeypatch.setattr(privileged, "run_as_root",
                        lambda *a, **k: pytest.fail("the runner must not be reached"))
    lines = []
    result = deploy._run_local(default_site("home", MODE_NATIVE), "echo PRIVATE-KEY", lines.append)
    assert not result.success and result.error == privileged.ROOT_REFUSAL
    assert streamed == [] and lines == []
    assert not any((tmp_path / "st").rglob("install-*"))

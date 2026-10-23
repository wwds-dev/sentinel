"""MAC changer audit fixes (TODO "MAC changer audit fixes", Opus audit 2026-10-21).

Offline: no real root, no real interface is touched. The root helper's own
logic is run through bash with its two tools swapped for recording fakes.
"""
from __future__ import annotations

import pytest

from agents.vpn_agent.services import macaddr, privileged
from agents.vpn_agent.services.macaddr import Interface

NEW = "02:11:22:33:44:55"


def _iface(device="en5", port="USB 10/100/1000 LAN", current="aa:bb:cc:00:00:01",
           hardware="aa:bb:cc:00:00:01"):
    return Interface(device=device, port=port, current=current, hardware=hardware)


@pytest.fixture
def root(monkeypatch):
    """Record every root call macaddr makes; the address 'takes' by default."""
    calls = []
    state = {"mac": NEW, "result": (True, "")}

    def fake(argv, prompt, *a, **kw):
        calls.append({"argv": list(argv), "prompt": prompt, "kw": kw})
        return state["result"]
    monkeypatch.setattr(macaddr, "run_argv_as_root", fake)
    monkeypatch.setattr(macaddr, "current_mac", lambda d: state["mac"])
    state["calls"] = calls
    return state


# ── Medium 4: the root command is a fixed helper, never a built string ──

@pytest.mark.parametrize("device", [
    "en0; rm -rf /", "en0 ", "en0\n", "$(id)", "../en0", "EN0", "en", "e0",
    "en1234", "abcdefghi0", "", "en0`x`",
])
def test_hostile_or_malformed_device_is_refused_before_anything_runs(monkeypatch, device):
    touched = []
    monkeypatch.setattr(macaddr, "run", lambda *a, **k: touched.append(a) or (True, ""))
    monkeypatch.setattr(macaddr, "run_argv_as_root", lambda *a, **k: touched.append(a) or (True, ""))
    ok, msg = macaddr.set_mac(device, NEW)
    assert not ok and "interface name" in msg
    assert touched == []


@pytest.mark.parametrize("device", ["en0", "en12", "bridge100", "utun3"])
def test_valid_device_names(device):
    assert macaddr.valid_device(device)


def test_root_argv_keeps_device_and_address_as_separate_words(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    ok, _ = macaddr.set_mac("en5", NEW)
    assert ok
    [call] = root["calls"]
    argv = call["argv"]
    assert argv[:3] == ["/bin/bash", "-c", macaddr.ROOT_SCRIPT]
    assert argv[3:] == ["sentinel-mac", "en5", NEW, "0"]
    # The script is a constant: no value of this call is spliced into it.
    assert "en5" not in macaddr.ROOT_SCRIPT and NEW not in macaddr.ROOT_SCRIPT
    assert call["kw"] == {"allow_cached_sudo": False}


def test_wifi_flag_is_passed_not_scripted(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, port="Wi-Fi"))
    macaddr.set_mac("en0", NEW)
    assert root["calls"][0]["argv"][-1] == "1"


def test_dialog_path_quotes_every_word(monkeypatch):
    seen = []
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 501)
    monkeypatch.setattr(privileged, "run_with_dialog",
                        lambda script, prompt, timeout: seen.append(script) or (True, ""))
    privileged.run_argv_as_root(["/bin/echo", "a b", "x'; id #"], "p", allow_cached_sudo=False)
    import shlex
    assert shlex.split(seen[0]) == ["/bin/echo", "a b", "x'; id #"]


def test_root_and_sudo_paths_pass_argv_without_a_shell(monkeypatch):
    seen = []
    monkeypatch.setattr(privileged, "run", lambda argv, timeout=0: seen.append(argv) or (True, ""))
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 0)
    privileged.run_argv_as_root(["/sbin/ifconfig", "en5"], "p", allow_cached_sudo=False)
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 501)
    privileged.run_argv_as_root(["/sbin/ifconfig", "en5"], "p")
    assert seen == [["/sbin/ifconfig", "en5"], ["sudo", "-n", "/sbin/ifconfig", "en5"]]


def test_sudo_refusal_falls_back_to_dialog_but_a_ran_command_is_not_rerun(monkeypatch):
    seen = []

    def fake_run(argv, timeout=0):
        seen.append(argv[0])
        return False, "sudo: a password is required"
    monkeypatch.setattr(privileged, "run", fake_run)
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 501)
    monkeypatch.setattr(privileged, "run_with_dialog", lambda *a: seen.append("dialog") or (True, ""))
    privileged.run_argv_as_root(["/bin/true"], "p")
    assert seen == ["sudo", "dialog"]

    seen.clear()
    monkeypatch.setattr(privileged, "run", lambda argv, timeout=0: seen.append(argv[0]) or (False, "exited 1"))
    assert privileged.run_argv_as_root(["/bin/false"], "p") == (False, "exited 1")
    assert seen == ["sudo"]


# ── Medium 3: Wi-Fi is always switched back on, and a failure says where it stopped ──

def _run_script(tmp_path, *, wifi, off=0, ifconfig=0, on=0, hang=False, timeout=10):
    """Run ROOT_SCRIPT under bash with networksetup/ifconfig replaced by fakes."""
    import subprocess
    log = tmp_path / "log"
    ns = tmp_path / "networksetup"
    ns.write_text(f'#!/bin/bash\necho "ns $*" >> {log}\n'
                  f'[ "$3" = off ] && exit {off}\n[ "$3" = on ] && exit {on}\nexit 0\n')
    ifc = tmp_path / "ifconfig"
    body = "/bin/sleep 2\n" if hang else ""
    ifc.write_text(f'#!/bin/bash\necho "ifconfig $*" >> {log}\n{body}exit {ifconfig}\n')
    for f in (ns, ifc):
        f.chmod(0o755)
    script = (macaddr.ROOT_SCRIPT.replace("/usr/sbin/networksetup", str(ns))
              .replace("/sbin/ifconfig", str(ifc)).replace("/bin/sleep 1", ":"))
    argv = ["/bin/bash", "-c", script, "sentinel-mac", "en0", NEW, "1" if wifi else "0"]
    if hang:
        proc = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
        import time
        for _ in range(100):
            if log.exists() and "ifconfig" in log.read_text():
                break
            time.sleep(0.05)
        proc.terminate()
        _, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    else:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        code, err = done.returncode, done.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return code, err, lines


@pytest.mark.allow_process
def test_script_success_order(tmp_path):
    code, _, lines = _run_script(tmp_path, wifi=True)
    assert code == 0
    assert lines == ["ns -setairportpower en0 off", f"ifconfig en0 ether {NEW}",
                     "ns -setairportpower en0 on"]


@pytest.mark.allow_process
def test_script_wired_never_touches_wifi_power(tmp_path):
    code, _, lines = _run_script(tmp_path, wifi=False)
    assert code == 0 and lines == [f"ifconfig en0 ether {NEW}"]


@pytest.mark.allow_process
def test_script_ifconfig_failure_is_reported_and_wifi_comes_back(tmp_path):
    code, _, lines = _run_script(tmp_path, wifi=True, ifconfig=1)
    assert code != 0
    assert lines[-1] == "ns -setairportpower en0 on"


@pytest.mark.allow_process
def test_script_wired_ifconfig_failure_is_reported(tmp_path):
    code, _, _ = _run_script(tmp_path, wifi=False, ifconfig=1)
    assert code != 0


@pytest.mark.allow_process
def test_script_power_off_failure_skips_the_change_but_still_powers_on(tmp_path):
    code, _, lines = _run_script(tmp_path, wifi=True, off=1)
    assert code != 0
    assert not any(l.startswith("ifconfig") for l in lines)
    assert lines[-1] == "ns -setairportpower en0 on"


@pytest.mark.allow_process
def test_script_power_on_failure_fails_the_whole_change(tmp_path):
    code, err, _ = _run_script(tmp_path, wifi=True, on=1)
    assert code != 0 and "Wi-Fi" in err


@pytest.mark.allow_process
def test_script_killed_mid_change_still_powers_wifi_on(tmp_path):
    code, _, lines = _run_script(tmp_path, wifi=True, hang=True)
    assert code != 0
    assert lines[-1] == "ns -setairportpower en0 on"


def test_failed_change_reports_address_and_wifi_power(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, port="Wi-Fi"))
    monkeypatch.setattr(macaddr, "wifi_power", lambda d: "Off")
    root["result"] = (False, "Timed out waiting for the administrator dialog or the command.")
    root["mac"] = "aa:bb:cc:00:00:01"
    ok, msg = macaddr.set_mac("en0", NEW)
    assert not ok
    assert "Timed out" in msg
    assert "en0 reports aa:bb:cc:00:00:01" in msg
    assert "Wi-Fi is Off" in msg


def test_failed_wired_change_does_not_mention_wifi(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    monkeypatch.setattr(macaddr, "wifi_power", lambda d: pytest.fail("wired has no Wi-Fi power"))
    root["result"] = (False, "exited 1")
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "Wi-Fi" not in msg and "en5 reports" in msg


@pytest.mark.parametrize("output, expected", [
    ("Wi-Fi Power (en0): On", "On"),
    ("Wi-Fi Power (en0): Off", "Off"),
    ("en9 is not a Wi-Fi interface.", ""),
])
def test_wifi_power_parses(monkeypatch, output, expected):
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: (True, output))
    assert macaddr.wifi_power("en0") == expected


# ── Medium 2: restore() never reports success it did not check ──

def test_restore_fails_when_the_current_address_cannot_be_read(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, current=""))
    ok, msg = macaddr.restore("en5")
    assert not ok and "could not read" in msg
    assert root["calls"] == []


def test_restore_fails_when_the_hardware_address_is_unknown(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, hardware=""))
    ok, msg = macaddr.restore("en5")
    assert not ok and "unknown" in msg and root["calls"] == []


def test_restore_when_unchanged_is_a_checked_no_op(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    ok, msg = macaddr.restore("en5")
    assert ok and "already" in msg and root["calls"] == []


def test_restore_when_changed_sets_the_hardware_address(monkeypatch, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, current=NEW))
    root["mac"] = "aa:bb:cc:00:00:01"
    ok, _ = macaddr.restore("en5")
    assert ok and root["calls"][0]["argv"][-2] == "aa:bb:cc:00:00:01"


# ── High 1: the original address is recorded, and put back on quit or after a crash ──

ORIG = "aa:bb:cc:00:00:01"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    return tmp_path


def test_a_change_records_the_original_before_root_runs(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    seen_during = []
    inner = macaddr.run_argv_as_root

    def spy(argv, prompt, *a, **kw):
        seen_during.append(dict(macaddr.recorded()))   # a crash here must still find it
        return inner(argv, prompt, *a, **kw)
    monkeypatch.setattr(macaddr, "run_argv_as_root", spy)
    ok, _ = macaddr.set_mac("en5", NEW)
    assert ok
    assert seen_during == [{"en5": ORIG}]
    assert macaddr.recorded() == {"en5": ORIG}
    import stat
    assert stat.S_IMODE(macaddr.record_path().stat().st_mode) == 0o600


def test_a_second_change_keeps_the_first_original(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    macaddr.set_mac("en5", NEW)
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, current=NEW))
    root["mac"] = "02:99:99:99:99:99"
    macaddr.set_mac("en5", "02:99:99:99:99:99")
    assert macaddr.recorded() == {"en5": ORIG}


def test_a_failed_change_that_left_the_address_alone_forgets_the_record(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    root["result"], root["mac"] = (False, "Cancelled."), ORIG
    assert not macaddr.set_mac("en5", NEW)[0]
    assert macaddr.recorded() == {}
    assert not macaddr.record_path().exists()


def test_a_failed_change_with_an_unknown_outcome_keeps_the_record(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d))
    root["result"], root["mac"] = (False, "Timed out."), ""
    macaddr.set_mac("en5", NEW)
    assert macaddr.recorded() == {"en5": ORIG}


@pytest.mark.parametrize("hardware", [ORIG, "aa:bb:cc:00:00:77"])
def test_going_back_to_the_original_or_hardware_clears_the_record(monkeypatch, state, root, hardware):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, hardware=hardware))
    macaddr.set_mac("en5", NEW)
    assert macaddr.recorded() == {"en5": ORIG}
    monkeypatch.setattr(macaddr, "get_interface",
                        lambda d: _iface(device=d, current=NEW, hardware=hardware))
    root["mac"] = hardware
    assert macaddr.set_mac("en5", hardware)[0]
    assert macaddr.recorded() == {}


def test_setting_the_hardware_address_records_nothing(monkeypatch, state, root):
    """Restoring an address someone else changed must not make Sentinel 'restore' it back."""
    monkeypatch.setattr(macaddr, "get_interface",
                        lambda d: _iface(device=d, current="02:aa:aa:aa:aa:aa"))
    root["mac"] = ORIG
    assert macaddr.set_mac("en5", ORIG)[0]
    assert macaddr.recorded() == {}


def test_an_unreadable_current_address_refuses_the_change(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, current=""))
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not read" in msg
    assert root["calls"] == [] and macaddr.recorded() == {}


def _write_record(entries):
    import json
    path = macaddr.record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries) if not isinstance(entries, str) else entries)


@pytest.mark.parametrize("content", [
    "not json", "[]", '{"en5": 3}',
    {"en0; id": ORIG, "en5": "zz", "en6": "01:00:5e:00:00:01"},
])
def test_a_damaged_or_hostile_record_is_ignored(state, content):
    _write_record(content)
    assert macaddr.recorded() == {}


def test_pending_restores_after_a_crash(monkeypatch, state):
    _write_record({"en5": ORIG, "en6": "aa:bb:cc:00:00:06", "en7": "aa:bb:cc:00:00:07"})
    now = {"en5": NEW, "en6": "aa:bb:cc:00:00:06", "en7": ""}
    monkeypatch.setattr(macaddr, "current_mac", lambda d: now[d])
    # en6 is back (a reboot does that): forgotten. en7 is unplugged: kept, not offered.
    assert macaddr.pending_restores() == [("en5", ORIG, NEW)]
    assert macaddr.recorded() == {"en5": ORIG, "en7": "aa:bb:cc:00:00:07"}


def test_restore_all_recorded_puts_the_original_back(monkeypatch, state, root):
    _write_record({"en5": ORIG})
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, current=NEW))
    now = {"mac": NEW}
    monkeypatch.setattr(macaddr, "current_mac", lambda d: now["mac"])

    def fake(argv, prompt, *a, **kw):
        root["calls"].append({"argv": argv, "prompt": prompt, "kw": kw})
        now["mac"] = argv[-2]
        return True, ""
    monkeypatch.setattr(macaddr, "run_argv_as_root", fake)
    [(device, ok, _)] = macaddr.restore_all_recorded()
    assert (device, ok) == ("en5", True)
    assert root["calls"][0]["argv"][-3:-1] == ["en5", ORIG]
    assert "original" in root["calls"][0]["prompt"]
    assert root["calls"][0]["kw"] == {"allow_cached_sudo": False}
    assert macaddr.recorded() == {}


def test_a_declined_restore_keeps_the_record_for_next_launch(monkeypatch, state, root):
    _write_record({"en5": ORIG})
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _iface(device=d, current=NEW))
    root["result"], root["mac"] = (False, "Cancelled."), NEW
    [(_, ok, _)] = macaddr.restore_all_recorded()
    assert not ok and macaddr.recorded() == {"en5": ORIG}


def test_nothing_recorded_means_no_program_runs_at_quit(monkeypatch, state):
    monkeypatch.setattr(macaddr, "run", lambda *a, **k: pytest.fail("ran a program"))
    monkeypatch.setattr(macaddr, "run_argv_as_root", lambda *a, **k: pytest.fail("ran root"))
    assert macaddr.restore_all_recorded() == []


def test_quit_restores_before_leaving(monkeypatch):
    from types import SimpleNamespace

    import main
    log = []
    monkeypatch.setattr(macaddr, "restore_all_recorded",
                        lambda: log.append("restore") or [("en5", False, "Cancelled.")])
    monkeypatch.setattr(main.os, "_exit", lambda code: log.append("exit"))
    settings = SimpleNamespace(sync=lambda: log.append("sync"))
    main._leave(SimpleNamespace(), settings)
    assert log == ["restore", "sync", "exit"]


def test_a_restore_that_raises_never_blocks_quit(monkeypatch):
    from types import SimpleNamespace

    import main
    log = []

    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(macaddr, "restore_all_recorded", boom)
    monkeypatch.setattr(main.os, "_exit", lambda code: log.append("exit"))
    main._leave(SimpleNamespace(), SimpleNamespace(sync=lambda: log.append("sync")))
    assert log == ["sync", "exit"]


def test_an_unknown_outcome_going_to_hardware_records_nothing(monkeypatch, state, root):
    """A crash mid-restore must not leave someone else's address recorded as ours."""
    monkeypatch.setattr(macaddr, "get_interface",
                        lambda d: _iface(device=d, current="02:aa:aa:aa:aa:aa"))
    root["result"], root["mac"] = (False, "Timed out."), ""
    macaddr.set_mac("en5", ORIG)
    assert macaddr.recorded() == {}

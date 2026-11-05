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


BOOT = "1000"
REAL_BOOT_ID = getattr(macaddr, "boot_id", None)    # before _same_boot replaces it


@pytest.fixture(autouse=True)
def _same_boot(monkeypatch):
    """A record is tied to the boot it was made in; these tests stay in one."""
    monkeypatch.setattr(macaddr, "boot_id", lambda: BOOT)


REAL_ELSEWHERE = getattr(macaddr, "_changes_elsewhere", None)


@pytest.fixture(autouse=True)
def _no_other_process(monkeypatch):
    """No change is waiting in another process unless a test says so."""
    monkeypatch.setattr(macaddr, "_changes_elsewhere", lambda: set())


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
@pytest.mark.parametrize("entry", ["set_mac", "randomize", "restore"])
def test_hostile_or_malformed_device_is_refused_before_anything_runs(monkeypatch, device, entry):
    # Nothing stubbed above run(): the real listing would run if the name got through.
    touched = []
    monkeypatch.setattr(macaddr, "run", lambda *a, **k: touched.append(a) or (True, ""))
    monkeypatch.setattr(macaddr, "run_argv_as_root", lambda *a, **k: touched.append(a) or (True, ""))
    call = {"set_mac": lambda: macaddr.set_mac(device, NEW),
            "randomize": lambda: macaddr.randomize(device),
            "restore": lambda: macaddr.restore(device)}[entry]
    ok, msg = call()
    assert not ok and msg == f"{device!r} is not a valid interface name."
    assert touched == []


@pytest.mark.parametrize("device", ["en0", "en12", "bridge100", "utun3"])
def test_valid_device_names(device):
    assert macaddr.valid_device(device)


def test_root_argv_keeps_device_and_address_as_separate_words(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
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
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, port="Wi-Fi"), ""))
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
    # The companion default; a gated (dialog-only) call is refused as root (Low 10).
    privileged.run_argv_as_root(["/sbin/ifconfig", "en5"], "p")
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
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, port="Wi-Fi"), ""))
    monkeypatch.setattr(macaddr, "wifi_power", lambda d: "Off")
    root["result"] = (False, "Timed out waiting for the administrator dialog or the command.")
    root["mac"] = "aa:bb:cc:00:00:01"
    ok, msg = macaddr.set_mac("en0", NEW)
    assert not ok
    assert "Timed out" in msg
    assert "en0 reports aa:bb:cc:00:00:01" in msg
    assert "Wi-Fi is Off" in msg


def test_failed_wired_change_does_not_mention_wifi(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
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
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=""), ""))
    ok, msg = macaddr.restore("en5")
    assert not ok and "could not read" in msg
    assert root["calls"] == []


def test_restore_fails_when_the_hardware_address_is_unknown(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, hardware=""), ""))
    ok, msg = macaddr.restore("en5")
    assert not ok and "unknown" in msg and root["calls"] == []


def test_restore_when_unchanged_is_a_checked_no_op(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    ok, msg = macaddr.restore("en5")
    assert ok and "already" in msg and root["calls"] == []


def test_restore_when_changed_sets_the_hardware_address(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
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
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
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
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    macaddr.set_mac("en5", NEW)
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    root["mac"] = "02:99:99:99:99:99"
    macaddr.set_mac("en5", "02:99:99:99:99:99")
    assert macaddr.recorded() == {"en5": ORIG}


def test_a_failed_change_that_left_the_address_alone_forgets_the_record(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    root["result"], root["mac"] = (False, "Cancelled."), ORIG
    assert not macaddr.set_mac("en5", NEW)[0]
    assert macaddr.recorded() == {}
    assert not macaddr.record_path().exists()


def test_a_failed_change_with_an_unknown_outcome_keeps_the_record(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    root["result"], root["mac"] = (False, "Timed out."), ""
    macaddr.set_mac("en5", NEW)
    assert macaddr.recorded() == {"en5": ORIG}


@pytest.mark.parametrize("hardware", [ORIG, "aa:bb:cc:00:00:77"])
def test_going_back_to_the_original_or_hardware_clears_the_record(monkeypatch, state, root, hardware):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, hardware=hardware), ""))
    macaddr.set_mac("en5", NEW)
    assert macaddr.recorded() == {"en5": ORIG}
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current=NEW, hardware=hardware), ""))
    root["mac"] = hardware
    assert macaddr.set_mac("en5", hardware)[0]
    assert macaddr.recorded() == {}


def test_setting_the_hardware_address_records_nothing(monkeypatch, state, root):
    """Restoring an address someone else changed must not make Sentinel 'restore' it back."""
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="02:aa:aa:aa:aa:aa"), ""))
    root["mac"] = ORIG
    assert macaddr.set_mac("en5", ORIG)[0]
    assert macaddr.recorded() == {}


def test_an_unreadable_current_address_refuses_the_change(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=""), ""))
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not read" in msg
    assert root["calls"] == [] and macaddr.recorded() == {}


def test_a_current_address_that_could_not_be_put_back_refuses_the_change(monkeypatch, state, root):
    """Low 8 knock-on: the record only ever holds an address set_mac accepts back."""
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="00:00:00:00:00:00"), ""))
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not record" in msg
    assert root["calls"] == [] and macaddr.recorded() == {}


def test_going_back_to_the_hardware_address_needs_no_record_so_is_allowed(
        monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="00:00:00:00:00:00"), ""))
    root["mac"] = ORIG
    ok, _ = macaddr.set_mac("en5", ORIG)
    assert ok and len(root["calls"]) == 1


def _write_record(entries, *, hardware=ORIG, boot=BOOT):
    """{device: original} written in the record's own format; a str is written raw."""
    import json
    path = macaddr.record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(entries, str):
        entries = json.dumps({device: {"original": original, "hardware": hardware, "boot": boot}
                              for device, original in entries.items()})
    path.write_text(entries)


def _ports(monkeypatch, now, hardware=None):
    """The interfaces list_interfaces reports: device -> current ('' = unreadable)."""
    hardware = hardware or {}
    monkeypatch.setattr(macaddr, "list_interfaces", lambda: [
        _iface(device=d, current=c, hardware=hardware.get(d, ORIG)) for d, c in now.items()])


@pytest.mark.parametrize("content", [
    "not json", "[]", '{"en5": 3}', '{"en5": "aa:bb:cc:00:00:01"}',
    '{"en5": {"original": "aa:bb:cc:00:00:01", "hardware": "zz", "boot": "1000"}}',
    '{"en5": {"original": "aa:bb:cc:00:00:01", "hardware": "aa:bb:cc:00:00:01", "boot": 1000}}',
])
def test_a_damaged_record_is_ignored(state, content):
    _write_record(content)
    assert macaddr.recorded() == {}


def test_a_hostile_record_is_ignored(state):
    _write_record({"en0; id": ORIG, "en5": "zz", "en6": "01:00:5e:00:00:01"})
    assert macaddr.recorded() == {}


def test_the_record_holds_the_hardware_and_the_boot(monkeypatch, state, root):
    import json
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="06:aa:aa:aa:aa:aa"), ""))
    macaddr.set_mac("en5", NEW)
    assert json.loads(macaddr.record_path().read_text()) == {
        "en5": {"original": "06:aa:aa:aa:aa:aa", "hardware": ORIG, "boot": BOOT}}


def test_pending_restores_after_a_crash(monkeypatch, state):
    _write_record({"en5": ORIG, "en6": ORIG, "en7": ORIG})
    _ports(monkeypatch, {"en5": NEW, "en6": ORIG, "en7": ""})
    # en6 is back: forgotten. en7 is unplugged: kept, not offered.
    assert macaddr.pending_restores() == [("en5", ORIG, NEW)]
    assert macaddr.recorded() == {"en5": ORIG, "en7": ORIG}


def test_an_adapter_missing_from_the_list_is_kept_not_offered(monkeypatch, state):
    _write_record({"en7": ORIG})
    _ports(monkeypatch, {})
    assert macaddr.pending_restores() == [] and macaddr.recorded() == {"en7": ORIG}


# ── Re-audit 2026-10-23 (P2-1..3): the record survives unknown outcomes and dies with the boot ──

@pytest.mark.parametrize("output", [
    "Timed out waiting for the administrator dialog or the command. "
    "If you had already approved it, the change may still have happened.",
    "exited 1",
])
def test_a_failed_change_that_was_not_cancelled_keeps_the_record(monkeypatch, state, root, output):
    """P2-1: a timeout can land after the address was read back; only Cancelled is settled."""
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    root["result"], root["mac"] = (False, output), ORIG
    assert not macaddr.set_mac("en5", NEW)[0]
    assert macaddr.recorded() == {"en5": ORIG}


def test_a_late_landing_change_is_still_offered_after_a_timeout(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    root["result"], root["mac"] = (False, "Timed out."), ORIG
    macaddr.set_mac("en5", NEW)
    _ports(monkeypatch, {"en5": NEW})            # root finished after osascript was killed
    assert macaddr.pending_restores() == [("en5", ORIG, NEW)]


def test_quit_never_forgets_a_change_still_waiting_for_root(monkeypatch, state, root):
    """P2-2: Quit anyway while the dialog is open must leave the record for the next launch."""
    import threading
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    entered, release = threading.Event(), threading.Event()

    def blocked(argv, prompt, *a, **kw):
        entered.set()
        release.wait(5)
        return False, "Cancelled."
    monkeypatch.setattr(macaddr, "run_argv_as_root", blocked)
    root["mac"] = ORIG
    worker = threading.Thread(target=macaddr.set_mac, args=("en5", NEW))
    worker.start()
    try:
        assert entered.wait(5)
        _ports(monkeypatch, {"en5": ORIG})       # nothing changed yet
        monkeypatch.setattr(macaddr, "set_mac", lambda *a, **k: pytest.fail("restored in flight"))
        assert macaddr.restore_all_recorded() == []
        assert macaddr.recorded() == {"en5": ORIG}
    finally:
        release.set()
        worker.join(5)
    assert not macaddr.in_flight()


def test_a_record_from_an_earlier_boot_is_dropped(monkeypatch, state):
    """P2-3: an ifconfig change never survives a reboot, so its record must not either."""
    _write_record({"en0": "06:aa:aa:aa:aa:aa"}, boot="999")
    assert macaddr.recorded() == {}
    _ports(monkeypatch, {"en0": "0a:bb:bb:bb:bb:bb"})   # macOS picked a new private address
    assert macaddr.pending_restores() == []
    assert not macaddr.record_path().exists()


def test_an_unknown_boot_keeps_the_record(monkeypatch, state):
    """Not knowing the boot is not proof of a reboot."""
    _write_record({"en5": ORIG}, boot="999")
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    assert macaddr.recorded() == {"en5": ORIG}


def test_a_malformed_boot_is_rejected_even_when_the_boot_is_unknown(monkeypatch, state):
    _write_record('{"en5": {"original": "aa:bb:cc:00:00:01", '
                  '"hardware": "aa:bb:cc:00:00:01", "boot": 1000}}')
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    assert macaddr.recorded() == {}


def test_another_adapter_under_the_same_name_is_not_given_the_address(monkeypatch, state):
    """P3-3: the record names hardware, not just a BSD name that can be reused."""
    _write_record({"en7": ORIG}, hardware="aa:bb:cc:00:00:07")
    _ports(monkeypatch, {"en7": NEW}, hardware={"en7": "aa:bb:cc:00:00:99"})
    assert macaddr.pending_restores() == []
    assert macaddr.recorded() == {}


UUID = "1C9E3D2A-5B7F-4E21-9A0D-3F6B8C4E2A71"


@pytest.mark.parametrize("boot_output, expected", [
    (UUID, UUID),
    (UUID.lower(), UUID),
    # kern.boottime is wall-clock based: XNU shifts it on wake and on a clock
    # step within one boot, so it is never accepted as the boot's identity.
    ("{ sec = 1792350752, usec = 655426 } Sun Oct 18 20:12:32 2026", ""),
    ("garbage", ""),
    (UUID + "\n; id", ""),
])
def test_boot_id_is_the_boot_session_uuid(monkeypatch, boot_output, expected):
    """Re-audit 2 P2-A: the identity must not drift within one boot."""
    seen = []
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: seen.append(argv) or (True, boot_output))
    REAL_BOOT_ID.cache_clear()
    try:
        assert REAL_BOOT_ID() == expected
        assert seen == [["sysctl", "-n", "kern.bootsessionuuid"]]
    finally:
        REAL_BOOT_ID.cache_clear()


def test_boot_id_is_unknown_when_sysctl_fails(monkeypatch):
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: (False, UUID))
    REAL_BOOT_ID.cache_clear()
    try:
        assert REAL_BOOT_ID() == ""
    finally:
        REAL_BOOT_ID.cache_clear()


def test_a_record_made_this_boot_is_kept_under_its_real_boot_id(monkeypatch, state):
    """Re-audit 3 P3-A: the boot ID has letters, and its case must survive the record."""
    import json
    monkeypatch.setattr(macaddr, "boot_id", lambda: UUID)
    macaddr._remember("en5", ORIG, ORIG)
    assert json.loads(macaddr.record_path().read_text())["en5"]["boot"] == UUID
    assert macaddr.recorded() == {"en5": ORIG}


def test_restore_all_recorded_puts_the_original_back(monkeypatch, state, root):
    _write_record({"en5": ORIG})
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    _ports(monkeypatch, {"en5": NEW})
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
    # The original is often a private Wi-Fi address, not the hardware one.
    assert "hardware" not in root["calls"][0]["prompt"]
    assert root["calls"][0]["kw"] == {"allow_cached_sudo": False}
    assert macaddr.recorded() == {}


def test_a_declined_restore_keeps_the_record_for_next_launch(monkeypatch, state, root):
    _write_record({"en5": ORIG})
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    _ports(monkeypatch, {"en5": NEW})
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
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="02:aa:aa:aa:aa:aa"), ""))
    root["result"], root["mac"] = (False, "Timed out."), ""
    macaddr.set_mac("en5", ORIG)
    assert macaddr.recorded() == {}


# ── Medium 5: every generated address is locally administered unicast ──

def test_same_vendor_mode_is_gone():
    assert not hasattr(macaddr, "MODE_SAME_VENDOR")
    assert macaddr.MODES == (macaddr.MODE_LOCAL,)


@pytest.mark.parametrize("mode", [macaddr.MODE_LOCAL, "same-vendor", "", "nonsense"])
def test_every_generated_address_is_locally_administered_unicast(mode):
    # A saved setting naming the removed mode (or any unknown one) falls back
    # to the random mode, and even a real vendor address passed as `like` is
    # never copied.
    for _ in range(300):
        address = macaddr.random_mac(mode, like="f0:18:98:12:34:56")
        first = int(address.split(":")[0], 16)
        assert first & 0x02, address          # locally administered
        assert not first & 0x01, address      # unicast
        assert not address.startswith("f0:18:98"), address
        assert macaddr.is_valid(address)


def test_randomize_with_a_saved_same_vendor_mode_uses_the_random_mode(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, hardware="f0:18:98:00:00:01",
                                         current="f0:18:98:00:00:01"), ""))
    macaddr.randomize("en5", "same-vendor")
    sent = root["calls"][0]["argv"][-2]
    assert int(sent.split(":")[0], 16) & 0x03 == 0x02


# ── Low 8: addresses that cannot be a source address ──

@pytest.mark.parametrize("address", [
    "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01", "33:33:00:00:00:01",
    "03:11:22:33:44:55", "ab:cd:ef:01:02:03", "", "02:11:22:33:44", "02:11:22:33:44:5g",
])
def test_unusable_addresses_are_rejected(address):
    assert not macaddr.is_valid(address)


@pytest.mark.parametrize("address", ["02:11:22:33:44:55", "AA:BB:CC:00:00:01", "00:00:00:00:00:02"])
def test_ordinary_unicast_addresses_are_accepted(address):
    assert macaddr.is_valid(address)


def test_a_zero_address_never_reaches_root(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    ok, _ = macaddr.set_mac("en5", "00:00:00:00:00:00")
    assert not ok and root["calls"] == []


# ── Review of c807267, P2-1: an unrecordable current address fails closed ──

@pytest.mark.parametrize("current", ["00:00:00:00:00:00", "01:00:5e:00:00:01", "ff:ff:ff:ff:ff:ff"])
def test_a_current_address_that_cannot_be_recorded_refuses_the_change(monkeypatch, state, root, current):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=current), ""))
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not record" in msg
    assert root["calls"] == [] and macaddr.recorded() == {}


def test_going_back_to_hardware_needs_no_recordable_current(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="00:00:00:00:00:00"), ""))
    root["mac"] = "aa:bb:cc:00:00:01"
    assert macaddr.set_mac("en5", "aa:bb:cc:00:00:01")[0]


# ── Review 3 P3-1/P3-2: only a record pending_restores would act on opens the guard ──

def _unrecordable(monkeypatch):
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="00:00:00:00:00:00"), ""))


def test_a_record_for_another_device_does_not_open_the_guard(monkeypatch, state, root):
    _write_record({"en0": "06:aa:bb:cc:dd:ee"})
    _unrecordable(monkeypatch)
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not record" in msg and root["calls"] == []


def test_a_record_with_other_hardware_does_not_open_the_guard(monkeypatch, state, root):
    _write_record({"en5": "06:aa:bb:cc:dd:ee"}, hardware="aa:bb:cc:99:99:99")
    _unrecordable(monkeypatch)
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not record" in msg and root["calls"] == []


# ── Review 1 P2-2: no hardware address reaches stderr from the quit-time restore ──

ANY_MAC = r"(?i)[0-9a-f]{2}(:[0-9a-f]{2}){5}"


def test_redact_addresses_strips_the_padded_colon_form():
    text = "ifconfig: bad en5 reports 02:AA:bb:cc:dd:ee. was 00:00:00:00:00:01"
    out = macaddr.redact_addresses(text)
    assert "02:" not in out and "en5" in out and "reports" in out
    import re
    assert not re.search(ANY_MAC, out)


def test_a_failed_quit_restore_prints_no_address(monkeypatch, capsys):
    import re

    import main
    monkeypatch.setattr(macaddr, "restore_all_recorded", lambda: [
        ("en5", False, "ifconfig: ioctl (SIOCAIFADDR): Invalid argument 02:aa:bb:cc:dd:ee "
                       "en5 reports 02:aa:bb:cc:dd:ee.")])
    main._restore_changed_hardware_addresses()
    err = capsys.readouterr().err
    assert "en5" in err and "not restored" in err
    assert not re.search(ANY_MAC, err), err


def test_a_raising_quit_restore_prints_no_address(monkeypatch, capsys):
    import re

    import main

    def boom():
        raise RuntimeError("failed on 02:aa:bb:cc:dd:ee")
    monkeypatch.setattr(macaddr, "restore_all_recorded", boom)
    main._restore_changed_hardware_addresses()
    err = capsys.readouterr().err
    assert "skipped" in err and not re.search(ANY_MAC, err), err


# ── Review 2 P3-A: an unrecordable current address does not block a restore to the record ──

def test_restore_to_a_recorded_original_works_from_an_unrecordable_current(monkeypatch, state, root):
    orig = "06:aa:bb:cc:dd:ee"
    _write_record({"en5": orig})
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current="00:00:00:00:00:00"), ""))
    root["mac"] = orig
    ok, _ = macaddr.set_mac("en5", orig)
    assert ok and len(root["calls"]) == 1


# ── P3-2: a failed listing is told apart from a missing interface ──

LISTING = ("Hardware Port: USB LAN\nDevice: en5\nEthernet Address: aa:bb:cc:00:00:01\n")


def _fake_run(monkeypatch, listing_ok=True, listing=LISTING):
    def fake(argv, *a, **kw):
        if argv[0] == "networksetup":
            return (listing_ok, listing if listing_ok else "networksetup: timed out")
        return True, "\tether 02:11:22:33:44:55\n"
    monkeypatch.setattr(macaddr, "run", fake)


def test_list_interfaces_checked_reports_a_failed_listing(monkeypatch):
    _fake_run(monkeypatch, listing_ok=False)
    interfaces, error = macaddr.list_interfaces_checked()
    assert interfaces == [] and "timed out" in error
    assert macaddr.list_interfaces() == []           # the old API is unchanged


def test_list_interfaces_checked_succeeds(monkeypatch):
    _fake_run(monkeypatch)
    interfaces, error = macaddr.list_interfaces_checked()
    assert error == "" and [i.device for i in interfaces] == ["en5"]


def test_read_interface_found_gone_and_failed(monkeypatch):
    _fake_run(monkeypatch)
    iface, error = macaddr.read_interface("en5")
    assert iface.current == "02:11:22:33:44:55" and error == ""
    assert macaddr.read_interface("en9") == (None, "")      # gone, not an error
    _fake_run(monkeypatch, listing_ok=False)
    iface, error = macaddr.read_interface("en5")
    assert iface is None and "timed out" in error


def test_a_failed_listing_error_is_bounded_and_holds_no_address(monkeypatch):
    noisy = "Ethernet Address: 02:aa:bb:cc:dd:ee " + "x" * 500
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **kw: (False, noisy))
    _, error = macaddr.list_interfaces_checked()
    import re
    assert 0 < len(error) <= 200
    assert not re.search(r"(?i)[0-9a-f]{2}(:[0-9a-f]{2}){5}", error)


# ── Review of 5a81ab9 P3-4: a failed listing is not "No such interface" ──

@pytest.mark.parametrize("call", [
    lambda: macaddr.set_mac("en5", NEW),
    lambda: macaddr.randomize("en5"),
    lambda: macaddr.restore("en5"),
])
def test_a_failed_listing_is_reported_as_unreadable_not_missing(monkeypatch, root, call):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (None, "networksetup timed out"))
    ok, msg = call()
    assert not ok and "Could not read the network interfaces" in msg and "timed out" in msg
    assert "No such interface" not in msg and root["calls"] == []
    assert msg.endswith("Nothing was changed.")


@pytest.mark.parametrize("call", [
    lambda: macaddr.set_mac("en5", NEW),
    lambda: macaddr.randomize("en5"),
    lambda: macaddr.restore("en5"),
])
def test_a_missing_interface_is_still_reported_as_missing(monkeypatch, root, call):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (None, ""))
    ok, msg = call()
    assert not ok and msg == "No such interface: en5" and root["calls"] == []


def test_a_failing_networksetup_is_reported_through_the_real_listing_path(monkeypatch, root):
    """No stubbed seams: the failed command's text is redacted and the whole call refuses (review P3-2)."""
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **kw: (False, "timed out aa:bb:cc:dd:ee:ff"))
    for call in (lambda: macaddr.set_mac("en5", NEW), lambda: macaddr.randomize("en5"),
                 lambda: macaddr.restore("en5")):
        ok, msg = call()
        assert not ok and "Could not read the network interfaces" in msg and "timed out" in msg
        assert "aa:bb:cc:dd:ee:ff" not in msg and "No such interface" not in msg
    assert root["calls"] == []


def test_a_failed_listing_is_read_once_per_call(monkeypatch, root):
    """On a failed listing, one read decides both the refusal and its wording (review P3-1)."""
    reads = []
    monkeypatch.setattr(macaddr, "read_interface", lambda d: reads.append(d) or (None, "boom"))
    for call in (lambda: macaddr.set_mac("en5", NEW), lambda: macaddr.randomize("en5"),
                 lambda: macaddr.restore("en5")):
        reads.clear()
        ok, msg = call()
        assert not ok and "boom" in msg and len(reads) == 1


@pytest.mark.parametrize("call", [
    lambda: macaddr.set_mac("en5", NEW),
    lambda: macaddr.randomize("en5"),
    lambda: macaddr.restore("en5"),
])
def test_the_wording_comes_from_the_one_listing_that_decided(monkeypatch, root, call):
    """The first listing fails and any later one would succeed: a second listing taken only
    for the wording would say "No such interface" or find the interface (review 3 P3-2)."""
    listings = []

    def fake(argv, *a, **kw):
        if argv[0] == "networksetup":
            listings.append(argv)
            return (False, "networksetup: boom") if len(listings) == 1 else (True, LISTING)
        return True, "\tether 02:11:22:33:44:55\n"
    monkeypatch.setattr(macaddr, "run", fake)
    ok, msg = call()
    assert not ok and "Could not read the network interfaces: networksetup: boom" in msg
    assert "No such interface" not in msg and root["calls"] == []
    assert len(listings) == 1


def test_a_listing_timeout_is_worded_without_a_double_full_stop(monkeypatch, root):
    """privileged.run's own text ends in a full stop (review 3 P3-1)."""
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **kw: (False, "Timed out."))
    for call in (lambda: macaddr.set_mac("en5", NEW), lambda: macaddr.randomize("en5"),
                 lambda: macaddr.restore("en5")):
        ok, msg = call()
        assert not ok and ".." not in msg
        assert msg == "Could not read the network interfaces: Timed out. Nothing was changed."
    assert root["calls"] == []


def _count_listings(monkeypatch):
    listings = []

    def fake(argv, *a, **kw):
        if argv[0] == "networksetup":
            listings.append(argv)
            return True, LISTING
        return True, "\tether 02:11:22:33:44:55\n"
    monkeypatch.setattr(macaddr, "run", fake)
    return listings


def test_a_successful_call_lists_the_interfaces_once(monkeypatch, root):
    """set_mac, randomize and restore each read the listing once on success (review 3 P3-2)."""
    listings = _count_listings(monkeypatch)
    calls = {"set_mac": lambda: macaddr.set_mac("en5", NEW),
             "randomize": lambda: macaddr.randomize("en5"),
             "restore": lambda: macaddr.restore("en5")}
    for name, call in calls.items():
        listings.clear()
        call()
        assert len(listings) == 1, name


def test_a_listing_error_is_redacted_before_it_is_cut(monkeypatch):
    # An address straddling the 200-character cut: cutting first leaves "02:aa:bb"
    # behind, which the six-octet pattern no longer recognises.
    noisy = "x" * 189 + " 02:aa:bb:cc:dd:ee " + "y" * 50
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **kw: (False, noisy))
    _, error = macaddr.list_interfaces_checked()
    assert len(error) <= 200 and "02:aa:bb" not in error


# ── Review of 3389384 (P3-1, P3-3) and review 4 P3-A ──

def test_restore_passes_the_wifi_flag(monkeypatch, root):
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, port="Wi-Fi", current=NEW), ""))
    root["mac"] = ORIG
    ok, msg = macaddr.restore("en0")
    assert ok and "rejoin" in msg
    assert root["calls"][0]["argv"][-1] == "1"


def test_restore_is_not_a_success_when_the_address_did_not_take(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    root["mac"] = NEW                    # root accepted, the adapter ignored it
    ok, msg = macaddr.restore("en5")
    assert not ok and "still reports" in msg


def test_restore_refuses_an_unusable_hardware_address(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (
        _iface(device=d, current=NEW, hardware="01:00:5e:00:00:01"), ""))
    ok, msg = macaddr.restore("en5")
    assert not ok and "not a usable" in msg and root["calls"] == []


def test_a_record_from_an_earlier_boot_does_not_open_the_guard(monkeypatch, state, root):
    """The guard reads the record as pending_restores would: an earlier boot's entry is gone."""
    _write_record({"en5": "06:aa:bb:cc:dd:ee"}, boot="999")
    _unrecordable(monkeypatch)
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "could not record" in msg and root["calls"] == []


# ── Re-audit 2 P3-3 to P3-6: the record file ──

PRIVATE = "06:aa:aa:aa:aa:aa"


def test_a_failed_boot_read_is_not_cached(monkeypatch):
    """P3-4: one failed sysctl must not make every later record boot-less."""
    answers = [(False, ""), (True, UUID), (True, "changed")]
    seen = []
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: seen.append(argv) or answers.pop(0))
    REAL_BOOT_ID.cache_clear()
    try:
        assert REAL_BOOT_ID() == ""
        assert REAL_BOOT_ID() == UUID
        assert REAL_BOOT_ID() == UUID           # a known boot is read once
        assert len(seen) == 2
    finally:
        REAL_BOOT_ID.cache_clear()


def test_a_change_that_needs_a_record_is_refused_when_the_boot_is_unknown(monkeypatch, state, root):
    """P3-4: a boot-less entry would survive reboots, so none is written: fail closed."""
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    ok, msg = macaddr.set_mac("en5", NEW)
    assert not ok and "boot" in msg and "Nothing was changed" in msg
    assert root["calls"] == [] and not macaddr.record_path().exists()


def test_going_back_needs_no_boot(monkeypatch, state, root):
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    # To the hardware address: no record is written.
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    root["mac"] = ORIG
    assert macaddr.set_mac("en5", ORIG)[0]
    # To a recorded original: the entry already exists.
    _write_record({"en5": PRIVATE})
    root["mac"] = PRIVATE
    assert macaddr.set_mac("en5", PRIVATE)[0]
    assert len(root["calls"]) == 2


def test_a_boot_less_entry_is_dropped_once_the_boot_is_known(monkeypatch, state):
    _write_record({"en5": ORIG}, boot="")
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    assert macaddr.recorded() == {"en5": ORIG}      # unknown now: not proof of a reboot
    monkeypatch.setattr(macaddr, "boot_id", lambda: BOOT)
    assert macaddr.recorded() == {}


def test_a_new_adapter_under_a_reused_name_is_recorded(monkeypatch, state, root):
    """P3-3: a kept entry for another adapter must not block this one's record."""
    import json
    _write_record({"en7": ORIG}, hardware="aa:bb:cc:00:00:07")
    other = "aa:bb:cc:00:00:99"
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current=other, hardware=other), ""))
    assert macaddr.set_mac("en7", NEW)[0]
    assert json.loads(macaddr.record_path().read_text())["en7"] == {
        "original": other, "hardware": other, "boot": BOOT}


def test_a_deeply_nested_record_is_treated_as_damaged(state):
    """P3-5: json raises RecursionError, which PrivacyTab() would not survive."""
    _write_record("[" * 100_000)
    assert macaddr.recorded() == {}
    assert macaddr.pending_restores() == []


def test_forget_prunes_stale_entries(state):
    """P3-6: _forget writes the file back when it holds anything stale."""
    _write_record({"en6": ORIG}, boot="999")
    macaddr._forget("en9")
    assert not macaddr.record_path().exists()


def test_concurrent_records_are_not_lost(monkeypatch, state):
    """P3-6: the lock serialises read-modify-write of the record."""
    import threading
    import time
    inner = macaddr._read_entries

    def slow():
        entries = inner()
        time.sleep(0.1)          # a second writer would read the same old file here
        return entries
    monkeypatch.setattr(macaddr, "_read_entries", slow)
    workers = [threading.Thread(target=macaddr._remember, args=(d, ORIG, ORIG))
               for d in ("en5", "en6")]
    for w in workers:
        w.start()
    for w in workers:
        w.join(5)
    monkeypatch.setattr(macaddr, "_read_entries", inner)
    assert macaddr.recorded() == {"en5": ORIG, "en6": ORIG}


def test_a_cancelled_first_change_from_a_private_address_forgets_the_record(monkeypatch, state, root):
    """P3-6: original != hardware (a private Wi-Fi address) is settled too."""
    monkeypatch.setattr(macaddr, "read_interface",
                        lambda d: (_iface(device=d, current=PRIVATE), ""))
    root["result"], root["mac"] = (False, "Cancelled."), PRIVATE
    assert not macaddr.set_mac("en5", NEW)[0]
    assert macaddr.recorded() == {}


def test_a_restore_to_a_private_original_forgets_the_record(monkeypatch, state, root):
    _write_record({"en5": PRIVATE})
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    root["mac"] = PRIVATE
    assert macaddr.set_mac("en5", PRIVATE)[0]
    assert macaddr.recorded() == {}


# ── Re-audit 2 P3-1, P3-2: a change still waiting for root, here or in an earlier process ──

def test_the_device_is_in_flight_while_it_is_listed(monkeypatch, state, root):
    """P3-2: a quit during the listing must not restore under a change about to ask for root."""
    seen = []

    def listing(d):
        seen.append(d in macaddr.in_flight())
        return _iface(device=d, current=NEW if seen[1:] else ORIG), ""
    monkeypatch.setattr(macaddr, "read_interface", listing)
    macaddr.set_mac("en5", NEW)
    root["mac"] = ORIG
    macaddr.restore("en5")
    assert seen == [True, True] and not macaddr.in_flight()


def test_in_flight_is_a_count(monkeypatch, state, root):
    """P3-2: the first of two overlapping changes to finish must not clear the other's mark."""
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d), ""))
    with macaddr._busy("en5"):
        macaddr.set_mac("en5", NEW)
        assert macaddr.in_flight() == {"en5"}
    assert not macaddr.in_flight()


PS = ("  PID COMMAND\n/usr/bin/osascript -e do shell script \"/bin/bash -c 'dev=$1; addr=$2\n"
      "/sbin/ifconfig \\\"$dev\\\" ether \\\"$addr\\\"\n' sentinel-mac en5 02:11:22:33:44:55 1\" "
      "with prompt \"x\" with administrator privileges\n/bin/zsh -c echo sentinel-mac\n"
      "/bin/echo sentinel-mac en1234 x\n")      # not a device name: no en123


def test_changes_elsewhere_reads_the_process_list(monkeypatch):
    seen = []
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: seen.append(argv) or (True, PS))
    assert REAL_ELSEWHERE() == {"en5"}
    assert seen == [["ps", "-axww", "-o", "command="]]
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: (False, "ps: failed"))
    assert REAL_ELSEWHERE() is None


def test_an_orphaned_dialog_keeps_the_record(monkeypatch, state):
    """P3-1: a relaunch while the last process's dialog is still open must not forget it."""
    _write_record({"en5": ORIG, "en6": ORIG})
    _ports(monkeypatch, {"en5": ORIG, "en6": NEW})
    monkeypatch.setattr(macaddr, "_changes_elsewhere", lambda: {"en5", "en6"})
    assert macaddr.pending_restores() == []
    assert macaddr.recorded() == {"en5": ORIG, "en6": ORIG}


def test_an_unreadable_process_list_forgets_nothing_but_still_offers(monkeypatch, state):
    _write_record({"en5": ORIG, "en6": ORIG})
    _ports(monkeypatch, {"en5": ORIG, "en6": NEW})
    monkeypatch.setattr(macaddr, "_changes_elsewhere", lambda: None)
    assert macaddr.pending_restores() == [("en6", ORIG, NEW)]
    assert macaddr.recorded() == {"en5": ORIG, "en6": ORIG}


def test_another_adapter_is_still_dropped_when_the_process_list_is_unreadable(monkeypatch, state):
    _write_record({"en7": ORIG})
    _ports(monkeypatch, {"en7": NEW}, hardware={"en7": "aa:bb:cc:00:00:99"})
    monkeypatch.setattr(macaddr, "_changes_elsewhere", lambda: None)
    assert macaddr.pending_restores() == [] and macaddr.recorded() == {}


def test_no_process_list_is_read_without_a_record(monkeypatch, state):
    monkeypatch.setattr(macaddr, "_changes_elsewhere", lambda: pytest.fail("ps run for nothing"))
    assert macaddr.pending_restores() == []


def test_the_quit_restore_survives_its_own_module_failing_to_import(monkeypatch, capsys):
    """Review 3 P3-4: the except must not re-import what just failed, or quit raises."""
    import sys

    import main
    from agents.vpn_agent import services
    monkeypatch.delattr(services, "macaddr")      # so "from services import macaddr" imports ...
    monkeypatch.setitem(sys.modules, "agents.vpn_agent.services.macaddr", None)   # ... and fails
    main._restore_changed_hardware_addresses()          # must not raise
    err = capsys.readouterr().err
    assert "hardware address restore skipped" in err


# ── Review of the P3 fixes (docs/audit/macaddr-p3s-review-2026-11-05.md) ──

@pytest.mark.allow_process
def test_run_survives_output_that_is_not_utf8():
    """P2-1: ps passes a process's raw argv bytes through; one bad byte must not raise."""
    ok, output = privileged.run(["/usr/bin/printf", "a\\377b"])
    assert ok and output.startswith("a") and output.endswith("b")


def test_an_undecodable_process_list_counts_as_unreadable(monkeypatch):
    def bad(argv, *a, **k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(macaddr, "run", bad)
    assert REAL_ELSEWHERE() is None


def test_only_the_helpers_own_shape_counts_as_a_change_elsewhere(monkeypatch):
    """P3-4: a grep or a shell that merely mentions the helper is not a pending change."""
    real_form = ("/usr/bin/osascript -e do shell script \"/bin/bash -c 'dev=$1\\\\012"
                 "/sbin/ifconfig' sentinel-mac en6 02:aa:bb:cc:dd:ee 0\" with prompt \"p\"\n")
    noise = ("grep -r sentinel-mac en5 .\n/bin/zsh -c echo sentinel-mac en7 later\n"
             "/bin/echo sentinel-mac en8 02:aa:bb:cc:dd:ee 2\n")
    monkeypatch.setattr(macaddr, "run", lambda argv, *a, **k: (True, PS + real_form + noise))
    assert REAL_ELSEWHERE() == {"en5", "en6"}


def test_a_change_that_starts_during_the_check_is_not_forgotten(monkeypatch, state):
    """P3-2 (defence in depth): in flight is read again just before a forget."""
    _write_record({"en5": ORIG})

    def listing():
        macaddr._in_flight["en5"] = 1          # a change began after the snapshot
        return [_iface(device="en5", current=ORIG)]
    monkeypatch.setattr(macaddr, "list_interfaces", listing)
    try:
        assert macaddr.pending_restores() == []
        assert macaddr.recorded() == {"en5": ORIG}
    finally:
        macaddr._in_flight.clear()


def test_an_unknown_boot_allows_only_a_way_back(monkeypatch, state, root):
    """P3-3: an entry kept only because the boot is unknown must not stand in for a new change."""
    _write_record({"en5": PRIVATE})
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    ok, msg = macaddr.set_mac("en5", "02:99:99:99:99:99")
    assert not ok and "boot" in msg and root["calls"] == []


def test_an_unknown_boot_does_not_take_another_adapters_original_as_a_way_back(monkeypatch, state, root):
    _write_record({"en5": PRIVATE}, hardware="aa:bb:cc:00:00:99")     # a different adapter
    monkeypatch.setattr(macaddr, "boot_id", lambda: "")
    monkeypatch.setattr(macaddr, "read_interface", lambda d: (_iface(device=d, current=NEW), ""))
    ok, msg = macaddr.set_mac("en5", PRIVATE)
    assert not ok and "boot" in msg and root["calls"] == []

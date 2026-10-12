"""Audit tier-2, step S2: the kill-switch status says what it knows.

The marker file is a record of what Sentinel did, not proof of what pf holds.
It is JSON with a timestamp, is written before the privileged arm script, is
read as stale when it predates the boot, and the wording never claims more
than "recorded". Nothing here runs pfctl, sysctl, sudo or osascript.
"""

from __future__ import annotations

import json
import os
import subprocess
import types

import pytest

from agents.vpn_agent.services import killswitch as ks

BOOT = 1_760_000_000          # a fixed "boot time" (epoch seconds)


@pytest.fixture
def pf(monkeypatch, tmp_path):
    state = types.SimpleNamespace(boot=BOOT, scripts=[], result=(True, "ok"), registered=True,
                                  marker=tmp_path / "killswitch.armed", seen_at_run=None)

    def run_privileged(script, why):
        state.scripts.append(script)
        state.seen_at_run = state.marker.read_text() if state.marker.exists() else None
        return state.result

    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: state.registered)
    monkeypatch.setattr(ks, "_boot_time", lambda: state.boot)
    monkeypatch.setattr(ks, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(ks, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(ks, "_run_privileged", run_privileged)
    return state


def write_marker(state, *, at, boot=BOOT, marker_state="armed"):
    state.marker.write_text(json.dumps(
        {"state": marker_state, "at": at, "boot": boot, "ifaces": ["utun7"]}))


# ── states ──────────────────────────────────────────────────────────────────

def test_no_marker_is_disarmed(pf):
    s = ks.status()
    assert s.state == ks.STATE_DISARMED and s.armed is False


def test_a_marker_newer_than_the_boot_is_recorded_as_armed(pf):
    write_marker(pf, at=BOOT + 500)
    s = ks.status()
    assert s.state == ks.STATE_RECORDED_ARMED and s.armed is True
    assert "recorded as armed (not verified)" in s.summary().lower()
    assert "ARMED" not in s.summary()          # never an unqualified claim


def test_a_marker_older_than_the_boot_is_stale_and_not_armed(pf):
    write_marker(pf, at=BOOT - 3600, boot=BOOT - 7200)
    s = ks.status()
    assert s.state == ks.STATE_STALE and s.armed is False
    assert "restarted" in s.summary()


def test_the_legacy_marker_still_reads_as_recorded_armed(pf):
    pf.marker.write_text("armed\n")
    os.utime(pf.marker, (BOOT + 100, BOOT + 100))
    s = ks.status()
    assert s.state == ks.STATE_RECORDED_ARMED and s.armed is True


def test_a_legacy_marker_older_than_the_boot_is_stale(pf):
    pf.marker.write_text("armed\n")
    os.utime(pf.marker, (BOOT - 100, BOOT - 100))
    assert ks.status().state == ks.STATE_STALE


def test_an_unknown_boot_time_is_shown_as_unknown_not_guessed(pf):
    pf.boot = None
    write_marker(pf, at=BOOT - 99999)
    s = ks.status()
    assert s.state == ks.STATE_RECORDED_ARMED and s.boot_known is False
    assert "boot time unknown" in s.summary()


def test_a_marker_whose_anchor_registration_is_gone_is_stale(pf):
    pf.registered = False
    write_marker(pf, at=BOOT + 500)
    s = ks.status()
    assert s.state == ks.STATE_STALE and s.armed is False


def test_an_arming_marker_counts_as_recorded_even_before_registration(pf):
    pf.registered = False
    write_marker(pf, at=BOOT + 500, marker_state="arming")
    assert ks.status().state == ks.STATE_RECORDED_ARMED


def test_the_verified_states_exist_for_the_later_verify_step():
    assert ks.STATE_VERIFIED_ARMED == "verified_armed"
    assert ks.STATE_VERIFIED_EMPTY == "verified_empty"
    assert ks.KillSwitchState(True, True, True, state=ks.STATE_VERIFIED_ARMED).armed is True
    assert ks.KillSwitchState(True, False, True, state=ks.STATE_VERIFIED_EMPTY).armed is False


# ── status() is read-only; reconcile() cleans ───────────────────────────────

def test_status_never_removes_a_stale_marker(pf):
    write_marker(pf, at=BOOT - 3600, boot=BOOT - 7200)
    ks.status()
    assert pf.marker.exists()


def test_reconcile_removes_only_a_stale_marker(pf):
    write_marker(pf, at=BOOT - 3600, boot=BOOT - 7200)
    assert ks.reconcile() is True and not pf.marker.exists()
    write_marker(pf, at=BOOT + 10)
    assert ks.reconcile() is False and pf.marker.exists()


# ── the boot time ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("{ sec = 1760000000, usec = 123456 } Sun Oct 12 08:00:00 2025\n", 1760000000),
    ("{ sec = 5, usec = 0 }", 5),
    ("garbage", None), ("", None),
])
def test_boottime_output_is_parsed_defensively(text, expected):
    assert ks.parse_boottime(text) == expected


def test_boot_time_uses_sysctl_and_fails_to_unknown(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{ sec = 42, usec = 1 } x", stderr="")

    monkeypatch.setattr(ks.subprocess, "run", fake_run)
    assert ks._boot_time() == 42 and seen["cmd"][-2:] == ["-n", "kern.boottime"]
    monkeypatch.setattr(ks.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert ks._boot_time() is None


# ── arm: marker first, rollback only on a definitive failure ────────────────

def test_arm_writes_the_marker_before_the_privileged_script_runs(pf):
    ok, message = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is True
    during = json.loads(pf.seen_at_run)             # what existed when root was asked
    assert during["state"] == "arming"
    after = json.loads(pf.marker.read_text())
    assert after["state"] == "armed" and after["ifaces"] == ["utun7"]
    assert after["boot"] == BOOT and after["at"] >= BOOT
    assert "recorded as armed (not verified" in message


def test_a_cancelled_arm_removes_the_marker_it_wrote(pf):
    pf.result = (False, "Cancelled.")
    ok, _ = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and not pf.marker.exists()


def test_a_pfctl_error_removes_the_marker_it_wrote(pf):
    pf.result = (False, "pfctl: syntax error")
    ok, _ = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and not pf.marker.exists()


def test_a_failed_rearm_restores_the_previous_marker(pf):
    write_marker(pf, at=BOOT + 77)
    before = pf.marker.read_text()
    pf.result = (False, "Cancelled.")
    ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert pf.marker.read_text() == before          # a live block is not forgotten


def test_a_timed_out_arm_keeps_the_marker_as_armed(pf):
    pf.result = (False, "Timed out. If you had already approved it, the change may still have happened.")
    ok, message = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and "Disarm" in message
    assert json.loads(pf.marker.read_text())["state"] == "armed"
    assert ks.status().armed is True


def test_a_refused_arm_never_writes_a_marker(pf, monkeypatch):
    monkeypatch.setattr(ks, "validate", lambda path=None: (False, "bad rules"))
    ok, _ = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and not pf.marker.exists() and pf.scripts == []


def test_disarm_keeps_the_marker_when_it_fails_and_removes_it_on_success(pf):
    write_marker(pf, at=BOOT + 5)
    pf.result = (False, "Cancelled.")
    ks.disarm()
    assert pf.marker.exists()
    pf.result = (True, "")
    ks.disarm()
    assert not pf.marker.exists()


# ── the callers ─────────────────────────────────────────────────────────────

def test_the_disconnect_warning_reconciles_first_and_treats_recorded_as_armed(monkeypatch):
    from services import vpn_connection, vpn_execution
    calls = []
    fake = types.SimpleNamespace(
        reconcile=lambda: calls.append("reconcile"),
        status=lambda: calls.append("status") or types.SimpleNamespace(armed=True))
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    assert vpn_execution._killswitch_armed() is True
    assert calls == ["reconcile", "status"]


def test_connection_status_text_comes_from_status(monkeypatch):
    from services import vpn_connection
    fake = types.SimpleNamespace(
        is_supported=lambda: True,
        status=lambda: types.SimpleNamespace(summary=lambda: "Recorded as armed (not verified)"))
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    assert vpn_connection.killswitch_status_text() == "Recorded as armed (not verified)"
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: None)
    assert vpn_connection.killswitch_status_text() is None

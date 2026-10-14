"""Audit tier-2 S4 round 2: Disarm leaves an honest record if the app quits mid-way.

Quit anyway while root still flushes the anchor used to leave the old "armed"
record (the marker was only removed after success), so Sentinel reopened
claiming a block that might be gone. Disarm now writes a `disarming` marker
BEFORE the privileged script, and clears it only on a definitive success.
Nothing here runs pfctl, sudo or osascript.
"""

from __future__ import annotations

import json
import types

import pytest

from agents.vpn_agent.services import killswitch as ks

BOOT = 1_760_000_000


@pytest.fixture
def pf(monkeypatch, tmp_path):
    state = types.SimpleNamespace(result=(True, "ok"), seen=None, scripts=[],
                                  marker=tmp_path / "killswitch.armed", registered=True)

    def run_privileged(script, why):
        state.scripts.append(script)
        state.seen = state.marker.read_text() if state.marker.exists() else None
        if isinstance(state.result, BaseException):
            raise state.result
        return state.result

    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: state.registered)
    monkeypatch.setattr(ks, "_boot_time", lambda: BOOT)
    monkeypatch.setattr(ks, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(ks, "_run_privileged", run_privileged)
    monkeypatch.setattr(ks, "_VERIFIED", {})
    return state


def armed_marker(state):
    state.marker.write_text(json.dumps(
        {"state": "armed", "at": BOOT + 500, "boot": BOOT, "ifaces": ["utun7"]}))
    return state.marker.read_bytes()


def test_disarm_writes_the_disarming_marker_before_the_script_runs(pf):
    armed_marker(pf)
    ok, _ = ks.disarm()
    assert ok is True
    assert json.loads(pf.seen)["state"] == "disarming"          # what existed when root was asked
    assert json.loads(pf.seen)["ifaces"] == ["utun7"]


def test_a_successful_disarm_removes_the_marker(pf):
    armed_marker(pf)
    ks.disarm()
    assert not pf.marker.exists() and ks.status().state == ks.STATE_DISARMED


@pytest.mark.parametrize("output", ["Cancelled.", "pfctl: some error"])
def test_a_definitive_failure_puts_the_previous_record_back(pf, output):
    before = armed_marker(pf)
    pf.result = (False, output)
    ok, message = ks.disarm()
    assert ok is False and pf.marker.read_bytes() == before
    assert ks.recovery_command() in message


def test_a_failed_disarm_with_no_previous_record_leaves_none(pf):
    pf.result = (False, "Cancelled.")
    ks.disarm()
    assert not pf.marker.exists()


def test_a_timed_out_disarm_stays_unconfirmed(pf):
    armed_marker(pf)
    pf.result = (False, "Timed out waiting for the administrator dialog or the command. "
                        "If you had already approved it, the change may still have happened.")
    ok, message = ks.disarm()
    assert ok is False and json.loads(pf.marker.read_text())["state"] == "disarming"
    assert "unconfirmed" in message.lower() or "may still" in message.lower()


def test_a_thread_lost_during_disarm_leaves_the_disarming_record(pf):
    """Quit anyway: the process ends inside the privileged call."""
    armed_marker(pf)
    pf.result = SystemExit("process ends")
    with pytest.raises(SystemExit):
        ks.disarm()
    assert json.loads(pf.marker.read_text())["state"] == "disarming"


def test_status_reports_disarming_unconfirmed_and_still_counts_as_armed(pf):
    armed_marker(pf)
    pf.result = SystemExit()
    with pytest.raises(SystemExit):
        ks.disarm()
    s = ks.status()
    assert s.state == ks.STATE_RECORDED_ARMED and s.armed is True and s.disarming is True
    text = s.summary().lower()
    assert "disarming, unconfirmed" in text and "verify" in text and "disarm" in text
    assert "recorded as armed" not in text


def test_a_fresh_disarming_marker_is_not_dropped_by_reconcile_when_the_anchor_is_gone(pf):
    armed_marker(pf)
    pf.result = SystemExit()
    with pytest.raises(SystemExit):
        ks.disarm()
    pf.registered = False
    assert ks.reconcile() == "" and pf.marker.exists()


def test_disarm_forgets_a_verified_result_up_front(pf):
    armed_marker(pf)
    ks._VERIFIED.update(state=ks.STATE_VERIFIED_ARMED, at=1.0)
    pf.result = SystemExit()
    with pytest.raises(SystemExit):
        ks.disarm()
    assert ks._VERIFIED == {}

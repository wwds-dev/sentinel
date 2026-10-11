"""Tunnel keeps no log at all: nothing on disk, nothing retained after display.

Declined and refused attempts used to be appended to ``tunnel_audit.jsonl``.
That facility is gone, on purpose; these tests keep it gone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from services import runtime_paths, vpn_execution
from tests.test_vpn_execution import (  # noqa: F401  (the fixture is autouse there)
    Probe, Recorder, profile_for, wg_quick_installed, write_conf)

ROOT = Path(__file__).resolve().parent.parent
TUNNEL_SOURCES = [
    "services/vpn_execution.py",
    "ui/workers.py",
    "ui/panels/vpn.py",
    "ui/panels/vpn_gate.py",
    "ui/panels/vpn_privacy.py",
    "ui/panels/vpn_servers.py",
]


def test_the_audit_api_no_longer_exists():
    for name in ("append_audit", "default_audit_path", "AUDIT_FILENAME",
                 "record_refusal", "record_killswitch", "record_companion"):
        assert not hasattr(vpn_execution, name), name


def test_no_tunnel_source_refers_to_an_audit_log():
    pattern = re.compile(r"record_companion|record_refusal|record_killswitch|tunnel_audit|append_audit")
    for rel in TUNNEL_SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not pattern.search(text), rel


def test_execute_writes_nothing_for_a_refusal_or_a_run(tmp_path, monkeypatch):
    data = tmp_path / "data-root"
    data.mkdir()
    monkeypatch.setattr(runtime_paths, "user_data_base", lambda: data)

    refused = vpn_execution.execute(
        "connect", {"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
                    "interface": "wgjp", "placeholder": True},
        run_as_root=Recorder(), probe=Probe(), killswitch_armed=lambda: False)
    assert refused.ran is False

    probe = Probe(route="utun7")
    ran = vpn_execution.execute(
        "connect", profile_for(write_conf(tmp_path)),
        run_as_root=Recorder(), probe=probe, killswitch_armed=lambda: False,
        sleep=lambda s: None)
    assert ran.ran is True
    assert list(data.rglob("*")) == []


def test_the_result_says_nothing_was_logged(tmp_path):
    outcome = vpn_execution.execute(
        "connect", profile_for(write_conf(tmp_path)),
        run_as_root=Recorder(), probe=Probe(), killswitch_armed=lambda: False,
        sleep=lambda s: None)
    titles = [title for title, _body, _mono in outcome.sections()]
    assert "Logging" in titles and "Audit" not in titles
    assert "audit_path" not in outcome.as_result()
    assert "audit" not in json.dumps(outcome.as_result()).lower()


def test_bug_spray_keeps_its_own_separate_nmap_record(tmp_path):
    from ui.panels import bug_bounty

    target = tmp_path / "bug_spray_audit.jsonl"
    written = bug_bounty.record_nmap_attempt(
        "declined", "example.com", "Example program", "nmap -sV example.com",
        audit_path=target)
    assert written == target
    entry = json.loads(target.read_text().splitlines()[-1])
    assert entry["action"] == "nmap" and entry["outcome"] == "declined"


# ── OpenVPN and Tor write a start-up log; it must not outlive the start ─────

def _openvpn_in(tmp_path, monkeypatch):
    from services import openvpn_manager
    monkeypatch.setattr(openvpn_manager, "_run_dir", lambda: tmp_path)
    monkeypatch.setattr(openvpn_manager, "openvpn_binary", lambda: "/opt/homebrew/sbin/openvpn")
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    return openvpn_manager


def test_openvpn_keeps_only_the_device_name_not_the_log(tmp_path, monkeypatch):
    ovpn = _openvpn_in(tmp_path, monkeypatch)
    cfg = tmp_path / "srv.ovpn"
    cfg.write_text("client\nremote 203.0.113.7 1194\n")

    def run(script, prompt, timeout=30):
        (tmp_path / "openvpn.log").write_text(
            "TLS: Initial packet from [AF_INET]203.0.113.7:1194\nOpened utun device utun5\n")
        return True, ""
    result = ovpn.connect(str(cfg), run_as_root=run, sleep=lambda s: None)
    assert result["success"] and result["device"] == "utun5"
    assert not ovpn.log_file().exists()
    assert ovpn.device_file().read_text() == "utun5"
    assert "203.0.113.7" not in ovpn.device_file().read_text()
    monkeypatch.setattr(ovpn, "is_running", lambda: True)
    assert ovpn.tunnel_device() == "utun5"


def test_a_failed_openvpn_start_is_explained_once_and_leaves_no_log(tmp_path, monkeypatch):
    ovpn = _openvpn_in(tmp_path, monkeypatch)
    cfg = tmp_path / "srv.ovpn"
    cfg.write_text("client\nremote 203.0.113.7 1194\n")
    slept = []

    def run(script, prompt, timeout=30):
        (tmp_path / "openvpn.log").write_text("Options error: Unrecognized option\n")
        return True, ""
    result = ovpn.connect(str(cfg), run_as_root=run, sleep=slept.append)
    assert result["device"] is None
    assert "Unrecognized option" in result["output"]
    assert not ovpn.log_file().exists() and not ovpn.device_file().exists()
    assert len(slept) == ovpn.DEVICE_WAIT_ATTEMPTS - 1


def test_disconnecting_openvpn_forgets_the_device(tmp_path, monkeypatch):
    ovpn = _openvpn_in(tmp_path, monkeypatch)
    ovpn.device_file().write_text("utun5")
    monkeypatch.setattr(ovpn, "_read_pid", lambda: 4242)
    monkeypatch.setattr(ovpn, "_is_tracked_process", lambda pid: True)
    result = ovpn.disconnect(run_as_root=lambda *a, **k: (True, ""))
    assert result["success"] and not ovpn.device_file().exists()


def test_tor_deletes_its_start_up_log_once_it_has_bootstrapped(tmp_path, monkeypatch):
    from agents.vpn_agent.services import tor
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(tor, "is_installed", lambda: True)
    monkeypatch.setattr(tor, "is_running", lambda: False)
    monkeypatch.setattr(tor, "tor_binary", lambda: "/opt/homebrew/bin/tor")
    monkeypatch.setattr(tor.time, "sleep", lambda s: None)
    log = tor.data_dir() / "tor.log"

    class Process:
        pid = 4321

        def poll(self):
            return None

    def popen(argv, **kwargs):
        log.write_text("Bootstrapped 100% (done): Done\n")
        return Process()
    monkeypatch.setattr(tor.subprocess, "Popen", popen)
    ok, message = tor.start(timeout=5)
    assert ok, message
    assert not log.exists()


def test_old_log_files_are_removed_and_only_those(tmp_path, monkeypatch):
    from services import tunnel_logs
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "state"))
    from agents.vpn_agent.services import tor
    keep = [tmp_path / "data" / "logs" / "runs.jsonl",
            tmp_path / "data" / "logs" / "bug_spray_audit.jsonl",
            tmp_path / "vpn" / "openvpn.pid"]
    gone = [tmp_path / "data" / "logs" / "tunnel_audit.jsonl",
            tmp_path / "vpn" / "openvpn.log",
            tor.data_dir() / "tor.log"]
    for path in keep + gone:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    removed = tunnel_logs.purge_legacy_logs(tunnel_logs.legacy_log_paths(tmp_path))
    assert sorted(removed) == sorted(str(p) for p in gone)
    assert all(p.exists() for p in keep) and not any(p.exists() for p in gone)
    assert tunnel_logs.purge_legacy_logs(tunnel_logs.legacy_log_paths(tmp_path)) == []

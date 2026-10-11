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

"""Real VPN connect/disconnect controller — WireGuard + OpenVPN.

Every privileged call is injected, so these tests never run wg-quick, openvpn,
pfctl, sudo, or osascript. They assert the controller builds the right commands,
refuses example/placeholder profiles instead of pretending, and maps tool
results honestly.
"""

from __future__ import annotations

import pytest

from services import openvpn_manager, vpn_connection


class Recorder:
    """Fake run_as_root that records scripts and returns a canned result."""
    def __init__(self, ok=True, output="ok"):
        self.ok, self.output, self.scripts = ok, output, []

    def __call__(self, script, prompt, timeout=30):
        self.scripts.append(script)
        return self.ok, self.output


@pytest.fixture(autouse=True)
def bare_wg_quick_command(monkeypatch):
    """Pin the command shape: without this the expected strings depend on
    whether this Mac has wireguard-tools installed (see wg_quick_command)."""
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: None if name == "wg-quick" else real_which(name, *a, **k))


# ── Placeholder honesty (the owner's core fear applied to the new feature) ──

def test_placeholder_profiles_are_detected():
    assert vpn_connection.is_placeholder({"placeholder": True, "endpoint": "1.2.3.4"})
    assert vpn_connection.is_placeholder({"endpoint": "<SERVER_IP>"})
    assert vpn_connection.is_placeholder({"endpoint": "0.0.0.0"})
    assert not vpn_connection.is_placeholder({"endpoint": "203.0.113.7"})


def test_connect_refuses_a_placeholder_without_running_anything():
    run = Recorder()
    profile = {"name": "Example — Japan", "protocol": "WireGuard",
               "endpoint": "<SERVER_IP>", "interface": "wgjp", "placeholder": True}
    result = vpn_connection.connect(profile, run_as_root=run)
    assert result["success"] is False
    assert "example" in result["error"].lower() or "template" in result["error"].lower()
    assert run.scripts == []          # nothing was executed


def test_example_country_profiles_are_all_non_connectable():
    run = Recorder()
    for profile in vpn_connection.example_country_profiles():
        assert vpn_connection.is_placeholder(profile)
        assert vpn_connection.connect(profile, run_as_root=run)["success"] is False
    assert run.scripts == []


# ── WireGuard ───────────────────────────────────────────────────────────────

def test_wireguard_connect_runs_wg_quick_up(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder(ok=True, output="[#] wg-quick up wg0")
    profile = {"name": "VPS", "protocol": "WireGuard",
               "endpoint": "203.0.113.7", "port": 51820, "interface": "wg0"}
    result = vpn_connection.connect(profile, run_as_root=run)
    assert result["success"] is True
    assert result["protocol"] == "WireGuard"
    assert run.scripts == ["wg-quick up 'wg0'"]


def test_wireguard_connect_prefers_a_config_path(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder()
    profile = {"name": "Imported", "protocol": "WireGuard", "endpoint": "203.0.113.7",
               "interface": "wg0", "config_path": "/Users/me/nl.conf"}
    vpn_connection.connect(profile, run_as_root=run)
    assert run.scripts == ["wg-quick up '/Users/me/nl.conf'"]


def test_wireguard_connect_reports_tool_failure(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder(ok=False, output="wg-quick: `wg0' already exists")
    result = vpn_connection.connect(
        {"protocol": "WireGuard", "endpoint": "203.0.113.7", "interface": "wg0"},
        run_as_root=run)
    assert result["success"] is False
    assert "already exists" in result["error"]


def test_wireguard_missing_tool_is_reported(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: False)
    run = Recorder()
    result = vpn_connection.connect(
        {"protocol": "WireGuard", "endpoint": "203.0.113.7", "interface": "wg0"},
        run_as_root=run)
    assert result["success"] is False
    assert "wg-quick" in result["error"]
    assert run.scripts == []


def test_wireguard_disconnect_runs_wg_quick_down(monkeypatch):
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    run = Recorder()
    vpn_connection.disconnect(
        {"protocol": "WireGuard", "endpoint": "203.0.113.7", "interface": "wg0"},
        run_as_root=run)
    assert run.scripts == ["wg-quick down 'wg0'"]


# ── OpenVPN ─────────────────────────────────────────────────────────────────

def test_openvpn_connect_requires_a_config_path():
    run = Recorder()
    result = vpn_connection.connect(
        {"name": "OVPN", "protocol": "OpenVPN", "endpoint": "203.0.113.7"},
        run_as_root=run)
    assert result["success"] is False
    assert "config" in result["error"].lower()
    assert run.scripts == []


def test_openvpn_connect_launches_daemon(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\nremote 203.0.113.7 1194\n")
    monkeypatch.setattr(openvpn_manager, "is_openvpn_available", lambda: True)
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: False)
    run = Recorder(ok=True, output="")
    result = vpn_connection.connect(
        {"name": "JP", "protocol": "OpenVPN", "endpoint": "203.0.113.7",
         "config_path": str(cfg)},
        run_as_root=run)
    assert result["success"] is True
    assert result["protocol"] == "OpenVPN"
    assert len(run.scripts) == 1
    assert "openvpn --config" in run.scripts[0]
    assert str(cfg) in run.scripts[0]
    assert "--daemon" in run.scripts[0]


def test_openvpn_connect_refuses_when_already_running(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "is_openvpn_available", lambda: True)
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: True)
    run = Recorder()
    result = openvpn_manager.connect(str(cfg), run_as_root=run)
    assert result["success"] is False
    assert "already running" in result["error"].lower()
    assert run.scripts == []


def test_openvpn_connect_reports_missing_binary(monkeypatch, tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\n")
    monkeypatch.setattr(openvpn_manager, "is_openvpn_available", lambda: False)
    result = openvpn_manager.connect(str(cfg), run_as_root=Recorder())
    assert result["success"] is False
    assert "openvpn" in result["error"].lower()


@pytest.mark.parametrize("pid", [None, -1, 0, 1, 12345])
def test_openvpn_disconnect_refuses_unverified_process(monkeypatch, pid):
    monkeypatch.setattr(openvpn_manager, "_read_pid", lambda: pid)
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p: False)
    run = Recorder()
    result = openvpn_manager.disconnect(run_as_root=run)
    assert result["success"] is False
    assert run.scripts == []


@pytest.mark.parametrize("ok", [True, False])
def test_openvpn_disconnect_preserves_tracking_and_reports_signal_failure(monkeypatch, tmp_path, ok):
    pidfile = tmp_path / "openvpn.pid"
    pidfile.write_text("12345")
    monkeypatch.setattr(openvpn_manager, "pid_file", lambda: pidfile)
    monkeypatch.setattr(openvpn_manager, "_is_tracked_process", lambda p: p == 12345)
    run = Recorder(ok=ok, output="signal result")
    result = openvpn_manager.disconnect(run_as_root=run)
    assert result["success"] is ok
    assert run.scripts == ["kill -TERM 12345"]
    assert pidfile.exists()


@pytest.mark.parametrize("command, expected", [
    ("/opt/homebrew/sbin/openvpn --daemon sentinel-ovpn --writepid /tmp/sentinel.pid", True),
    ("/usr/bin/python --daemon sentinel-ovpn --writepid /tmp/sentinel.pid", False),
    ("openvpn --daemon other --writepid /tmp/sentinel.pid", False),
    ("openvpn --daemon sentinel-ovpn --writepid /tmp/other.pid", False),
    ("openvpn --daemon", False),
])
def test_openvpn_process_identity(monkeypatch, command, expected):
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.setattr(openvpn_manager, "pid_file", lambda: Path("/tmp/sentinel.pid"))
    monkeypatch.setattr(openvpn_manager.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=command))
    assert openvpn_manager._is_tracked_process(12345) is expected


# ── Protocol dispatch ───────────────────────────────────────────────────────

def test_unknown_protocol_is_refused():
    result = vpn_connection.connect(
        {"protocol": "carrier-pigeon", "endpoint": "203.0.113.7"},
        run_as_root=Recorder())
    assert result["success"] is False
    assert "protocol" in result["error"].lower()


@pytest.mark.parametrize("pid", [-100, -1, 0, 1])
def test_openvpn_invalid_pid_never_queries_or_signals_processes(monkeypatch, pid):
    def unexpected(*a, **k):
        pytest.fail("Invalid PID reached process inspection")
    monkeypatch.setattr(openvpn_manager.subprocess, "run", unexpected)
    assert openvpn_manager._is_tracked_process(pid) is False

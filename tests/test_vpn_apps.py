"""Mullvad and Proton detection: read-only, stubbed, never a real app."""

from __future__ import annotations

import pytest

from services import vpn_apps
from services.vpn_apps import VpnApp

SCUTIL_DISCONNECTED = (
    "Available network connection services in the current set (*=enabled):\n"
    '* (Disconnected)   21DAE34C-9193-483A-8273-7FA0B570051F VPN (ch.protonvpn.mac) '
    '"ProtonVPN"                      [VPN:ch.protonvpn.mac]\n')
SCUTIL_CONNECTED = SCUTIL_DISCONNECTED.replace("(Disconnected)", "(Connected)")
SCUTIL_OTHER = (
    "Available network connection services in the current set (*=enabled):\n"
    '* (Connected)   11111111-2222-3333-4444-555555555555 VPN (com.example.vpn) '
    '"Work VPN"   [VPN:com.example.vpn]\n')


def runner(table):
    calls = []

    def run(argv, timeout=2.0):
        calls.append(argv)
        for key, value in table.items():
            if key in " ".join(argv):
                return value
        return 127, ""
    run.calls = calls
    return run


@pytest.fixture(autouse=True)
def fake_machine(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_apps, "MULLVAD_APP", tmp_path / "Mullvad VPN.app")
    monkeypatch.setattr(vpn_apps, "MULLVAD_BUNDLED_CLI", tmp_path / "mullvad-bundled")
    monkeypatch.setattr(vpn_apps, "PROTON_APP", tmp_path / "ProtonVPN.app")
    monkeypatch.setattr(vpn_apps.shutil, "which", lambda name, *a, **k: None)


# ── Mullvad ──

@pytest.mark.parametrize("text, state", [
    ("Disconnected\n", vpn_apps.DISCONNECTED),
    ("Connected to se-got-wg-001 in Gothenburg, Sweden\n    Visible location: x\n", vpn_apps.CONNECTED),
    ("Connecting to se-got-wg-001...\n", vpn_apps.CONNECTING),
    ("Disconnecting\n", vpn_apps.DISCONNECTING),
    ("Blocked: failed to set up firewall\n", vpn_apps.BLOCKED),
    ("something new\n", vpn_apps.UNKNOWN),
    ("", vpn_apps.UNKNOWN),
])
def test_mullvad_status_is_parsed_from_the_first_line(text, state):
    assert vpn_apps.parse_mullvad_status(text)[0] == state


def test_mullvad_is_asked_through_its_cli_only(monkeypatch):
    monkeypatch.setattr(vpn_apps.shutil, "which", lambda name, *a, **k: "/usr/local/bin/mullvad")
    run = runner({"mullvad status": (0, "Connected to se-got-wg-001 in Gothenburg, Sweden\n")})
    app = vpn_apps.detect_mullvad(run)
    assert app.installed and app.state == vpn_apps.CONNECTED and app.active
    assert run.calls == [["/usr/local/bin/mullvad", "status"]]   # no login, no account, no connect


def test_mullvad_not_installed_runs_nothing():
    run = runner({})
    app = vpn_apps.detect_mullvad(run)
    assert app.installed is False and app.active is False and run.calls == []


def test_mullvad_app_without_its_cli_is_unreadable_not_assumed_off(tmp_path):
    (tmp_path / "Mullvad VPN.app").mkdir()
    app = vpn_apps.detect_mullvad(runner({}))
    assert app.installed and app.state == vpn_apps.UNKNOWN and app.unreadable


def test_mullvad_daemon_not_running_is_not_a_conflict(monkeypatch):
    monkeypatch.setattr(vpn_apps.shutil, "which", lambda name, *a, **k: "/usr/local/bin/mullvad")
    app = vpn_apps.detect_mullvad(runner({"mullvad status": (1, "")}))
    assert app.state == vpn_apps.UNKNOWN and not app.unreadable
    assert vpn_apps.killswitch_conflict([app]) is None


# ── Proton ──

def test_proton_state_comes_from_the_system_vpn_service(tmp_path):
    (tmp_path / "ProtonVPN.app").mkdir()
    run = runner({"scutil --nc list": (0, SCUTIL_CONNECTED)})
    app = vpn_apps.detect_proton(run)
    assert app.installed and app.state == vpn_apps.CONNECTED and app.active
    assert run.calls == [[vpn_apps.SCUTIL, "--nc", "list"]]     # a read; never start or stop


def test_proton_disconnected(tmp_path):
    (tmp_path / "ProtonVPN.app").mkdir()
    app = vpn_apps.detect_proton(runner({"scutil --nc list": (0, SCUTIL_DISCONNECTED)}))
    assert app.state == vpn_apps.DISCONNECTED and not app.active


def test_proton_installed_without_a_service_yet(tmp_path):
    (tmp_path / "ProtonVPN.app").mkdir()
    app = vpn_apps.detect_proton(runner({"scutil --nc list": (0, "Available network connection services in the current set (*=enabled):\n")}))
    assert app.installed and app.state == vpn_apps.DISCONNECTED and "no VPN service" in app.detail


def test_other_system_vpns_are_not_taken_for_proton():
    app = vpn_apps.detect_proton(runner({"scutil --nc list": (0, SCUTIL_OTHER)}))
    assert app.installed is False


# ── Conflicts ──

def test_a_connected_app_blocks_the_kill_switch_and_warns_on_connect():
    apps = [VpnApp("mullvad", "Mullvad VPN", True, vpn_apps.CONNECTED, "Connected to x")]
    assert "Mullvad VPN is connected" in vpn_apps.killswitch_conflict(apps)
    assert "stacks two VPNs" in vpn_apps.connect_warning(apps)


def test_disconnected_or_missing_apps_conflict_with_nothing():
    apps = [VpnApp("mullvad", "Mullvad VPN", True, vpn_apps.DISCONNECTED),
            VpnApp("proton", "ProtonVPN", False)]
    assert vpn_apps.killswitch_conflict(apps) is None
    assert vpn_apps.connect_warning(apps) is None


def test_arming_is_refused_while_an_app_is_connected(monkeypatch):
    from services import vpn_connection
    apps = [VpnApp("proton", "ProtonVPN", True, vpn_apps.CONNECTED)]
    monkeypatch.setattr(vpn_apps, "detect", lambda run=None: apps)
    called = []

    class Ks:
        def is_supported(self): return True
        def arm(self, *a, **k): called.append(a) or (True, "armed")

    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: Ks())
    ok, message = vpn_connection.arm_killswitch(
        {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7", "interface": "wg0"})
    assert ok is False and "ProtonVPN is connected" in message and called == []


def test_connection_check_lists_installed_apps_and_stays_read_only(monkeypatch):
    from services import vpn_diagnostics
    apps = [VpnApp("mullvad", "Mullvad VPN", True, vpn_apps.CONNECTED, "Connected to se-got-wg-001"),
            VpnApp("proton", "ProtonVPN", False)]
    monkeypatch.setattr(vpn_apps, "detect", lambda run=None: apps)
    monkeypatch.setattr(vpn_diagnostics, "_default_route",
                        lambda: {"interface": "en0", "gateway": "192.0.2.1"})
    report = vpn_diagnostics.collect_vpn_diagnostics()
    text = report.as_text()
    assert "Mullvad VPN: connected" in text and "ProtonVPN" not in text.split("VPN apps")[1].split("Local route")[0]
    assert "managed by that app" in report.summary or report.active_tunnels
    assert any("kill switch" in step for step in vpn_diagnostics._recommended_steps(report))

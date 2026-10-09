"""Kill-switch wiring and imported-config endpoint extraction.

The pf kill switch is mocked everywhere here — no test runs pfctl, sudo, or
osascript, and none arms a real firewall. Covers: pulling the real server
endpoint out of an imported config (so the kill switch exempts the tunnel, not
blocks it), the arm/disarm pass-throughs, and the worker's result normalisation.
"""

from __future__ import annotations

import pytest

from services import vpn_connection


# ── Endpoint extraction from imported configs ───────────────────────────────

def test_extract_wireguard_endpoint(tmp_path):
    cfg = tmp_path / "nl.conf"
    cfg.write_text("[Interface]\nAddress = 10.0.0.2/32\n[Peer]\nEndpoint = 203.0.113.7:51820\n")
    assert vpn_connection.extract_endpoint(str(cfg), "WireGuard") == ("203.0.113.7", 51820)


def test_extract_openvpn_endpoint(tmp_path):
    cfg = tmp_path / "jp.ovpn"
    cfg.write_text("client\ndev tun\nremote vpn.example.net 1194 udp\n")
    assert vpn_connection.extract_endpoint(str(cfg), "OpenVPN") == ("vpn.example.net", 1194)


def test_extract_ipv6_wireguard_endpoint(tmp_path):
    cfg = tmp_path / "v6.conf"
    cfg.write_text("[Peer]\nEndpoint = [2001:db8::1]:51820\n")
    assert vpn_connection.extract_endpoint(str(cfg), "WireGuard") == ("2001:db8::1", 51820)


def test_extract_missing_endpoint_returns_none(tmp_path):
    cfg = tmp_path / "bad.conf"
    cfg.write_text("[Interface]\nAddress = 10.0.0.2/32\n")
    assert vpn_connection.extract_endpoint(str(cfg), "WireGuard") == (None, None)


def test_imported_profile_gets_a_real_endpoint_and_is_connectable(tmp_path):
    cfg = tmp_path / "nl.conf"
    cfg.write_text("[Peer]\nEndpoint = 203.0.113.7:51820\n")
    profile = vpn_connection.profile_from_config(str(cfg))
    assert profile["endpoint"] == "203.0.113.7"
    assert profile["port"] == 51820
    assert not vpn_connection.is_placeholder(profile)   # a real endpoint, not a template


def test_imported_profile_without_endpoint_is_still_connectable(tmp_path):
    cfg = tmp_path / "x.ovpn"
    cfg.write_text("client\n")  # no remote line
    profile = vpn_connection.profile_from_config(str(cfg))
    assert profile["endpoint"] == "imported"            # marker, not a placeholder token
    assert not vpn_connection.is_placeholder(profile)


# ── arm / disarm pass-throughs ──────────────────────────────────────────────

class _FakeKS:
    def __init__(self, supported=True):
        self._supported = supported
        self.arm_calls = []
        self.disarm_calls = 0

    def is_supported(self):
        return self._supported

    def arm(self, endpoints, allow):
        self.arm_calls.append((list(endpoints), list(allow)))
        return True, "Kill switch ARMED"

    def disarm(self):
        self.disarm_calls += 1
        return True, "disarmed"


def test_arm_exempts_the_profile_endpoint_and_its_transport(monkeypatch):
    # D1 regression: an empty allow list produced an anchor with only an ICMP
    # pass for the server, so the armed switch dropped the tunnel's own UDP.
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    ok, message = vpn_connection.arm_killswitch(
        {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
         "port": 51820})
    assert ok is True and "ARMED" in message
    assert fake.arm_calls == [(["203.0.113.7"], [("udp", 51820)])]


def test_arm_reads_the_port_from_the_config_when_the_profile_has_none(tmp_path, monkeypatch):
    conf = tmp_path / "wg0.conf"
    conf.write_text("[Interface]\nPrivateKey = x\n[Peer]\nEndpoint = 203.0.113.7:4443\n")
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    ok, _ = vpn_connection.arm_killswitch(
        {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
         "config_path": str(conf)})
    assert ok is True
    assert fake.arm_calls == [(["203.0.113.7"], [("udp", 4443)])]


def test_arm_follows_openvpn_proto_and_port(tmp_path, monkeypatch):
    ovpn = tmp_path / "srv.ovpn"
    ovpn.write_text("client\nproto tcp\nremote vpn.example.net 443\n")
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    ok, _ = vpn_connection.arm_killswitch(
        {"name": "Srv", "protocol": "OpenVPN", "endpoint": "vpn.example.net",
         "config_path": str(ovpn)})
    assert ok is True
    assert fake.arm_calls == [(["vpn.example.net"], [("tcp", 443)])]


def test_arm_falls_back_to_the_registered_default_port(monkeypatch):
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    vpn_connection.arm_killswitch(
        {"name": "Srv", "protocol": "OpenVPN", "endpoint": "203.0.113.9"})
    assert fake.arm_calls == [(["203.0.113.9"], [("udp", 1194)])]


def test_arm_refuses_a_profile_with_no_endpoint(monkeypatch):
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    ok, message = vpn_connection.arm_killswitch(
        {"name": "Bare", "protocol": "WireGuard", "endpoint": ""})
    assert ok is False and message
    assert fake.arm_calls == []


def test_build_rules_passes_the_tunnel_transport_to_the_server():
    from agents.vpn_agent.services import killswitch
    rules = killswitch.build_rules(["203.0.113.7"], [("udp", 51820)], interfaces=[])
    assert "pass out quick inet proto udp from any to 203.0.113.7 port 51820" in rules
    assert "pass out quick inet proto icmp from any to 203.0.113.7" in rules
    assert rules.strip().startswith("# VPN Agent kill switch")
    assert "block drop all" in rules


def test_arm_refuses_a_placeholder(monkeypatch):
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    ok, _ = vpn_connection.arm_killswitch({"endpoint": "<SERVER_IP>", "placeholder": True})
    assert ok is False
    assert fake.arm_calls == []            # never armed for a template


def test_arm_reports_when_unsupported(monkeypatch):
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: None)
    ok, message = vpn_connection.arm_killswitch({"endpoint": "203.0.113.7"})
    assert ok is False and "unavailable" in message.lower()


def test_disarm_passes_through(monkeypatch):
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    ok, _ = vpn_connection.disarm_killswitch()
    assert ok is True and fake.disarm_calls == 1


# ── Worker normalises the (ok, message) tuple into a result dict ────────────

def test_worker_arm_normalises_result(monkeypatch):
    from ui.workers import VpnConnectionWorker
    monkeypatch.setattr(vpn_connection, "arm_killswitch", lambda profile: (True, "ARMED ok"))
    received = []
    worker = VpnConnectionWorker("arm", {"endpoint": "203.0.113.7"})
    worker.finished_signal.connect(received.append)
    worker.run()  # synchronous
    assert received and received[0]["success"] is True
    assert received[0]["protocol"] == "Kill switch"
    assert "ARMED" in received[0]["output"]


def test_worker_disarm_reports_failure(monkeypatch):
    from ui.workers import VpnConnectionWorker
    monkeypatch.setattr(vpn_connection, "disarm_killswitch", lambda: (False, "pfctl error"))
    received = []
    worker = VpnConnectionWorker("disarm", {})
    worker.finished_signal.connect(received.append)
    worker.run()
    assert received and received[0]["success"] is False
    assert received[0]["error"] == "pfctl error"


# ── P0-12: the real pf service refuses to arm when it cannot exempt the tunnel ─

@pytest.fixture
def real_ks(monkeypatch):
    from agents.vpn_agent.services import killswitch

    privileged = []
    monkeypatch.setattr(killswitch, "is_supported", lambda: True)
    monkeypatch.setattr(killswitch, "_run_privileged",
                        lambda script, why: privileged.append(script) or (True, "ok"))
    killswitch.privileged_calls = privileged
    return killswitch


def test_an_endpoint_that_does_not_resolve_is_not_armed(real_ks, monkeypatch):
    import socket

    def nxdomain(*a, **k):
        raise socket.gaierror(8, "nodename nor servname provided")
    monkeypatch.setattr(real_ks.socket, "getaddrinfo", nxdomain)
    ok, message = real_ks.arm(["vpn.invalid.example"], allow=[("udp", 51820)])
    assert ok is False
    assert "Could not resolve" in message
    assert real_ks.privileged_calls == []            # pfctl never ran


def test_the_unspecified_placeholder_address_is_not_armed(real_ks):
    ok, message = real_ks.arm(["0.0.0.0"], allow=[("udp", 51820)])
    assert ok is False and real_ks.privileged_calls == []


def test_a_failed_arm_reports_the_recovery_command(real_ks, monkeypatch):
    monkeypatch.setattr(real_ks, "_run_privileged", lambda s, w: (False, "denied"))
    monkeypatch.setattr(real_ks, "validate", lambda path: (True, ""))
    ok, message = real_ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and "denied" in message
    assert real_ks.recovery_command() in message


# ── Final review: OpenVPN tunnels and OpenVPN config spellings ──────────────

class _FakeKSWithDevices(_FakeKS):
    def arm(self, endpoints, allow, extra_interfaces=None):
        self.arm_calls.append((list(endpoints), list(allow), list(extra_interfaces or [])))
        return True, "Kill switch ARMED"


def test_arming_with_openvpn_up_passes_its_tunnel_device(monkeypatch):
    fake = _FakeKSWithDevices()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    monkeypatch.setattr(vpn_connection.openvpn_manager, "is_running", lambda: True)
    monkeypatch.setattr(vpn_connection.openvpn_manager, "tunnel_device", lambda: "utun6")
    ok, _ = vpn_connection.arm_killswitch(
        {"name": "Srv", "protocol": "OpenVPN", "endpoint": "203.0.113.9", "port": 443})
    assert ok is True
    assert fake.arm_calls == [(["203.0.113.9"], [("udp", 443)], ["utun6"])]


def test_arming_refuses_when_openvpn_is_up_but_its_device_is_unknown(monkeypatch):
    fake = _FakeKSWithDevices()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    monkeypatch.setattr(vpn_connection.openvpn_manager, "is_running", lambda: True)
    monkeypatch.setattr(vpn_connection.openvpn_manager, "tunnel_device", lambda: None)
    ok, message = vpn_connection.arm_killswitch(
        {"name": "Srv", "protocol": "OpenVPN", "endpoint": "203.0.113.9"})
    assert ok is False and "tunnel device" in message
    assert fake.arm_calls == []


@pytest.mark.parametrize("log, expected", [
    ("... Opened utun device utun4\n", "utun4"),
    ("TUN/TAP device tun0 opened\n", "tun0"),
    ("Opened utun device utun3\n...restart...\nOpened utun device utun7\n", "utun7"),
    ("nothing useful\n", None),
    ("Opened utun device utun4; pass quick all\n", "utun4"),
])
def test_the_openvpn_device_is_read_from_its_log(tmp_path, monkeypatch, log, expected):
    from services import openvpn_manager
    path = tmp_path / "openvpn.log"
    path.write_text(log)
    monkeypatch.setattr(openvpn_manager, "log_file", lambda: path)
    monkeypatch.setattr(openvpn_manager, "is_running", lambda: True)
    assert openvpn_manager.tunnel_device() == expected


@pytest.mark.parametrize("device", ["en0", "utun4 all", "lo0", "utun", "../x"])
def test_the_pf_service_refuses_a_device_that_is_not_a_tunnel(real_ks, device):
    ok, message = real_ks.arm(["203.0.113.7"], allow=[("udp", 1194)],
                              extra_interfaces=[device])
    assert ok is False and real_ks.privileged_calls == []


def test_the_pf_rules_pass_the_openvpn_device(real_ks, monkeypatch):
    seen = {}
    real_build = real_ks.build_rules
    monkeypatch.setattr(real_ks, "build_rules",
                        lambda *a, **k: seen.setdefault("rules", real_build(*a, **k)))
    monkeypatch.setattr(real_ks, "active_tunnel_interfaces", lambda: [])
    monkeypatch.setattr(real_ks, "validate", lambda path: (True, ""))
    monkeypatch.setattr(real_ks, "write_rules", lambda rules: "/tmp/never-used")
    monkeypatch.setattr(real_ks.paths, "state_dir", lambda: __import__("pathlib").Path("/tmp"))
    ok, _ = real_ks.arm(["203.0.113.7"], allow=[("tcp", 443)], extra_interfaces=["utun6"])
    assert ok is True
    assert "pass quick on utun6 all" in seen["rules"]
    assert "pass out quick inet proto tcp from any to 203.0.113.7 port 443" in seen["rules"]


@pytest.mark.parametrize("config, expected", [
    ("client\nremote vpn.example.com 443 tcp\n", [("tcp", 443)]),
    ("client\nproto tcp\nport 443\nremote vpn.example.com\n", [("tcp", 443)]),
    ("client\nproto udp\nrport 8443\nremote vpn.example.com\n", [("udp", 8443)]),
    ("client\nproto tcp-client\nremote vpn.example.com 993\n", [("tcp", 993)]),
    ("client\nremote vpn.example.com 1195 udp6\nproto tcp\n", [("udp", 1195)]),
    ("client\n# remote old.example.com 1 tcp\nremote vpn.example.com\n", [("udp", 1194)]),
])
def test_openvpn_transport_follows_openvpn_precedence(tmp_path, monkeypatch, config, expected):
    ovpn = tmp_path / "srv.ovpn"
    ovpn.write_text(config)
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    monkeypatch.setattr(vpn_connection.openvpn_manager, "is_running", lambda: False)
    profile = vpn_connection.profile_from_config(str(ovpn))
    ok, _ = vpn_connection.arm_killswitch(profile)
    assert ok is True
    assert fake.arm_calls[-1][1] == expected


def test_an_approved_arm_that_times_out_is_treated_as_armed(real_ks, monkeypatch, tmp_path):
    monkeypatch.setattr(real_ks, "validate", lambda path: (True, ""))
    monkeypatch.setattr(real_ks.paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(real_ks, "_run_privileged", lambda s, w: (
        False, "Timed out waiting for the administrator dialog or the command. "
               "If you had already approved it, the change may still have happened."))
    ok, message = real_ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and "Disarm" in message
    assert (tmp_path / "killswitch.armed").exists()


def test_a_declined_arm_is_not_recorded_as_armed(real_ks, monkeypatch, tmp_path):
    monkeypatch.setattr(real_ks, "validate", lambda path: (True, ""))
    monkeypatch.setattr(real_ks.paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(real_ks, "_run_privileged", lambda s, w: (False, "Cancelled."))
    ok, _ = real_ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and not (tmp_path / "killswitch.armed").exists()

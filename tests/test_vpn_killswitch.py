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

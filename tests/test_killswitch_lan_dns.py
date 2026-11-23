"""Readiness audit H1/H2 (spec readiness-h1-h2-killswitch-dns, owner decision 2026-11-23).

Armed, the kill switch keeps the LAN open but blocks DNS (port 53, UDP and TCP) to
it, unless "Allow local network DNS" is ticked (off by default, stored). Lookups
inside the tunnel still pass. The Connect review says when system DNS stays in use.

Nothing here runs pfctl, sudo or osascript: rules are generated and checked as
text, and a small evaluator applies pf's first-quick-match order to them.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import types

import pytest

from agents.vpn_agent.services import killswitch as ks
from services import vpn_connection, vpn_execution

ENDPOINT = "203.0.113.7"
ALLOW = [("udp", 51820)]


# ── a minimal pf evaluator for the shapes build_rules writes ──────────────────

def _net(text: str):
    return ipaddress.ip_network(text, strict=False)


def _matches(rule: str, pkt: dict) -> bool:
    dst = ipaddress.ip_address(pkt["dst"])
    src = ipaddress.ip_address(pkt["src"])
    fam = "inet6" if dst.version == 6 else "inet"
    if rule == "pass quick on lo0 all":
        return pkt["iface"] == "lo0"
    m = re.fullmatch(r"pass quick on (\S+) all", rule)
    if m:
        return pkt["iface"] == m.group(1)
    m = re.fullmatch(r"pass out quick (inet6?) proto (tcp|udp) from any to (\S+) port (\d+)", rule)
    if m:
        return (pkt["dir"] == "out" and fam == m.group(1) and pkt["proto"] == m.group(2)
                and dst == ipaddress.ip_address(m.group(3)) and pkt["dport"] == int(m.group(4)))
    m = re.fullmatch(r"pass out quick (inet6?) proto (icmp6?) from any to (\S+)", rule)
    if m:
        return pkt["proto"] == m.group(2) and dst == ipaddress.ip_address(m.group(3))
    m = re.fullmatch(r"pass quick inet proto udp from any port (\d+) to any port (\d+)", rule)
    if m:
        return (fam == "inet" and pkt["proto"] == "udp" and pkt.get("sport") == int(m.group(1))
                and pkt["dport"] == int(m.group(2)))
    m = re.fullmatch(r"pass quick (inet6?) from (\S+) to (\S+)", rule)
    if m:
        if fam != m.group(1):
            return False
        return src in _net(m.group(2)) and dst in _net(m.group(3))
    m = re.fullmatch(r"block drop out quick (inet6?) proto (tcp|udp) from any to (\S+) port (\d+)",
                     rule)
    if m:
        return (pkt["dir"] == "out" and fam == m.group(1) and pkt["proto"] == m.group(2)
                and dst in _net(m.group(3)) and pkt["dport"] == int(m.group(4)))
    raise AssertionError(f"evaluator does not know rule {rule!r}")


def verdict(rules_text: str, **pkt) -> str:
    """'pass' or 'block': the first matching quick rule wins, else the last match."""
    pkt.setdefault("dir", "out")
    pkt.setdefault("src", "192.168.1.10" if ":" not in pkt["dst"] else "fe80::10")
    last = None
    for line in ks.loadable_rules(rules_text).splitlines():
        if line == "block drop all":
            last = "block"
            continue
        if _matches(line, pkt):
            return "block" if line.startswith("block") else "pass"
    return last or "pass"


def rules(**kwargs):
    kwargs.setdefault("interfaces", ["utun4"])
    return ks.build_rules([ENDPOINT], ALLOW, **kwargs)


# ── rule generation ───────────────────────────────────────────────────────────

def test_by_default_dns_to_the_lan_is_blocked_both_protocols_every_range():
    text = rules()
    for cidr in (*ks.DEFAULT_LAN, "169.254.0.0/16"):
        for proto in ("udp", "tcp"):
            assert f"block drop out quick inet proto {proto} from any to {cidr} port 53" in text
    for proto in ("udp", "tcp"):
        assert f"block drop out quick inet6 proto {proto} from any to fe80::/10 port 53" in text


def test_the_dns_blocks_sit_after_the_tunnel_pass_and_before_the_lan_passes():
    lines = ks.loadable_rules(rules()).splitlines()
    first_dns = next(i for i, ln in enumerate(lines) if ln.endswith("port 53"))
    last_dns = max(i for i, ln in enumerate(lines) if ln.endswith("port 53"))
    assert lines.index("pass quick on utun4 all") < first_dns
    first_lan = lines.index("pass quick inet from 192.168.0.0/16 to 192.168.0.0/16")
    assert last_dns < first_lan
    assert lines.index("pass quick inet6 from fe80::/10 to fe80::/10") > last_dns


def test_the_tick_leaves_the_dns_blocks_out_and_keeps_the_lan_open():
    text = rules(allow_lan_dns=True)
    assert "port 53" not in text
    assert "pass quick inet from 192.168.0.0/16 to 192.168.0.0/16" in text


def test_without_lan_access_there_are_no_dns_rules_and_default_deny_covers_dns():
    text = rules(allow_lan=False)
    assert "port 53" not in text
    assert verdict(text, iface="en0", proto="udp", dst="192.168.1.1", dport=53) == "block"


def test_custom_lan_ranges_get_their_own_dns_blocks():
    text = rules(lan_cidrs=("10.0.0.0/8",))
    assert "to 10.0.0.0/8 port 53" in text
    assert "to 192.168.0.0/16 port 53" not in text


# ── what the generated rules do to packets (pf's first-quick-match order) ────

@pytest.mark.parametrize("proto", ["udp", "tcp"])
@pytest.mark.parametrize("dst", ["192.168.1.1", "10.0.0.1", "172.16.5.1", "169.254.1.1"])
def test_armed_dns_to_a_lan_resolver_is_dropped(proto, dst):
    src = "169.254.7.7" if dst.startswith("169.254") else (
        "10.0.0.10" if dst.startswith("10.") else
        "172.16.0.10" if dst.startswith("172.") else "192.168.1.10")
    assert verdict(rules(), iface="en0", proto=proto, src=src, dst=dst, dport=53) == "block"


@pytest.mark.parametrize("proto", ["udp", "tcp"])
def test_armed_dns_to_a_link_local_ipv6_resolver_is_dropped(proto):
    # Routers often advertise fe80::1 as the IPv6 DNS server (RDNSS).
    assert verdict(rules(), iface="en0", proto=proto, dst="fe80::1", dport=53) == "block"


@pytest.mark.parametrize("dst, dport", [("192.168.1.20", 445), ("192.168.1.1", 80),
                                        ("192.168.1.30", 631), ("10.0.0.5", 22)])
def test_the_rest_of_the_lan_stays_open(dst, dport):
    src = "10.0.0.10" if dst.startswith("10.") else "192.168.1.10"
    assert verdict(rules(), iface="en0", proto="tcp", src=src, dst=dst, dport=dport) == "pass"


def test_mdns_on_link_local_is_not_port_53_and_stays_open():
    assert verdict(rules(), iface="en0", proto="udp", src="fe80::10", dst="fe80::fb",
                   dport=5353) == "pass"


def test_dns_inside_the_tunnel_still_passes_even_to_a_private_resolver():
    # Mullvad's resolver is 10.64.0.1: inside the 10/8 LAN range, but on the tunnel.
    assert verdict(rules(), iface="utun4", proto="udp", src="10.64.0.2", dst="10.64.0.1",
                   dport=53) == "pass"


def test_with_the_tick_dns_to_the_lan_resolver_passes():
    assert verdict(rules(allow_lan_dns=True), iface="en0", proto="udp", dst="192.168.1.1",
                   dport=53) == "pass"


def test_dns_to_a_public_resolver_outside_the_tunnel_stays_blocked_either_way():
    for text in (rules(), rules(allow_lan_dns=True)):
        assert verdict(text, iface="en0", proto="udp", dst="8.8.8.8", dport=53) == "block"


def test_with_the_tunnel_down_dns_to_the_lan_is_still_dropped():
    # The tunnel dropped: no utun is exempt, the LAN is open, LAN DNS is not.
    text = ks.build_rules([ENDPOINT], ALLOW, interfaces=[])
    assert verdict(text, iface="en0", proto="udp", dst="192.168.1.1", dport=53) == "block"
    assert verdict(text, iface="en0", proto="tcp", dst="192.168.1.20", dport=445) == "pass"


# ── the grammar root loads stays closed ───────────────────────────────────────

@pytest.mark.parametrize("line", [
    "block drop out quick inet proto udp from any to 8.8.8.0/24 port 53",
    "block drop out quick inet proto udp from any to 192.168.0.0/16 port 54",
    "block drop out quick inet proto icmp from any to 192.168.0.0/16 port 53",
    "block drop out quick inet6 proto udp from any to ::/0 port 53",
    "block drop quick inet proto udp from any to 192.168.0.0/16 port 53",
])
def test_root_refuses_dns_lines_that_are_not_the_generated_shape(line):
    with pytest.raises(ValueError):
        ks.loadable_rules(line + "\n")


# ── arm() passes the choice through (pf never runs) ──────────────────────────

@pytest.fixture
def pf(monkeypatch, tmp_path):
    state = types.SimpleNamespace(scripts=[])
    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: 1)
    monkeypatch.setattr(ks, "WG_RUN_DIR", tmp_path / "wgrun")
    monkeypatch.setattr(ks, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(ks, "_run_privileged",
                        lambda script, why: state.scripts.append(script) or (True, "ok"))
    monkeypatch.setattr(ks.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(8, "no")))
    return state


def test_arm_by_default_loads_the_lan_dns_blocks(pf):
    ok, _ = ks.arm([ENDPOINT], ALLOW, extra_interfaces=["utun4"])
    assert ok
    assert "to 192.168.0.0/16 port 53" in pf.scripts[0]
    assert "port 53" in ks.rules_path().read_text()          # the recovery copy says so too


def test_arm_with_the_tick_loads_no_dns_blocks(pf):
    ok, _ = ks.arm([ENDPOINT], ALLOW, extra_interfaces=["utun4"], allow_lan_dns=True)
    assert ok and "port 53" not in pf.scripts[0]


@pytest.mark.parametrize("value", ["1", 1, "yes", object()])
def test_only_a_real_true_opens_lan_dns(pf, value):
    ks.arm([ENDPOINT], ALLOW, extra_interfaces=["utun4"], allow_lan_dns=value)
    assert "port 53" in pf.scripts[-1]


@pytest.mark.parametrize("kwargs, recorded", [
    ({}, False), ({"allow_lan_dns": True}, True), ({"allow_lan_dns": "1"}, False),
    ({"allow_lan": False, "allow_lan_dns": True}, False),
])
def test_the_armed_record_says_what_the_rules_do_with_lan_dns(pf, monkeypatch, kwargs, recorded):
    ok, _ = ks.arm([ENDPOINT], ALLOW, extra_interfaces=["utun4"], **kwargs)
    assert ok
    assert ks._read_marker()["lan_dns"] is recorded
    assert ks.armed_lan_dns() is recorded


def test_a_record_from_before_the_setting_reads_as_unknown(pf):
    ks._write_marker("armed", ["utun4"])
    assert ks._read_marker()["lan_dns"] is None and ks.armed_lan_dns() is None


def test_nothing_armed_means_no_lan_dns_mode(pf):
    assert ks.armed_lan_dns() is None


def test_an_arm_timed_out_after_approval_still_records_the_mode(pf, monkeypatch):
    monkeypatch.setattr(ks, "_run_privileged",
                        lambda script, why: (False, "Timed out; it may still have happened."))
    ks.arm([ENDPOINT], ALLOW, extra_interfaces=["utun4"])
    assert ks._read_marker()["lan_dns"] is False


def test_a_failed_arm_with_the_dns_blocks_restores_the_previous_record(pf, monkeypatch):
    monkeypatch.setattr(ks, "_run_privileged", lambda script, why: (False, "Cancelled."))
    ok, message = ks.arm([ENDPOINT], ALLOW, extra_interfaces=["utun4"])
    assert not ok and ks.recovery_command() in message
    assert not ks.marker_path().exists()


# ── the stored tick ───────────────────────────────────────────────────────────

@pytest.fixture
def setting():
    yield
    vpn_connection.set_killswitch_allow_lan_dns(False)


def test_the_tick_is_off_by_default(setting):
    from services.database import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (vpn_connection.ALLOW_LAN_DNS_SETTING,))
        conn.commit()
    assert vpn_connection.killswitch_allow_lan_dns() is False


def test_the_tick_is_stored(setting):
    vpn_connection.set_killswitch_allow_lan_dns(True)
    assert vpn_connection.killswitch_allow_lan_dns() is True
    vpn_connection.set_killswitch_allow_lan_dns(False)
    assert vpn_connection.killswitch_allow_lan_dns() is False


def test_an_unreadable_setting_fails_closed(monkeypatch):
    import services.database as database

    def broken(*a, **k):
        raise OSError("database is locked")
    monkeypatch.setattr(database, "get_setting", broken)
    assert vpn_connection.killswitch_allow_lan_dns() is False


def test_a_stored_value_other_than_1_reads_as_off(setting):
    from services.database import save_setting
    save_setting(vpn_connection.ALLOW_LAN_DNS_SETTING, "true")
    assert vpn_connection.killswitch_allow_lan_dns() is False


class _FakeKS:
    def __init__(self):
        self.calls = []

    def is_supported(self):
        return True

    def arm(self, endpoints, allow, extra_interfaces=None, allow_lan_dns=None):
        self.calls.append(allow_lan_dns)
        return True, "armed"


@pytest.mark.parametrize("stored", [False, True])
def test_arm_killswitch_passes_the_stored_tick(monkeypatch, stored):
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    monkeypatch.setattr(vpn_connection.vpn_apps, "killswitch_conflict", lambda: "")
    monkeypatch.setattr(vpn_connection.openvpn_manager, "is_running", lambda: False)
    monkeypatch.setattr(vpn_connection, "killswitch_allow_lan_dns", lambda: stored)
    ok, _ = vpn_connection.arm_killswitch(
        {"name": "VPS", "protocol": "WireGuard", "endpoint": ENDPOINT, "port": 51820})
    assert ok and fake.calls == [stored]


def test_arm_killswitch_with_openvpn_up_passes_the_tick_too(monkeypatch):
    fake = _FakeKS()
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: fake)
    monkeypatch.setattr(vpn_connection.vpn_apps, "killswitch_conflict", lambda: "")
    monkeypatch.setattr(vpn_connection.openvpn_manager, "is_running", lambda: True)
    monkeypatch.setattr(vpn_connection.openvpn_manager, "tunnel_device", lambda: "utun6")
    monkeypatch.setattr(vpn_connection, "killswitch_allow_lan_dns", lambda: False)
    ok, _ = vpn_connection.arm_killswitch(
        {"name": "Srv", "protocol": "OpenVPN", "endpoint": ENDPOINT, "port": 1194})
    assert ok and fake.calls == [False]


def test_killswitch_armed_lan_dns_is_unknown_when_the_module_fails(monkeypatch):
    class Broken:
        def armed_lan_dns(self):
            raise OSError("unreadable")
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: Broken())
    assert vpn_connection.killswitch_armed_lan_dns() is None
    monkeypatch.setattr(vpn_connection, "_killswitch", lambda: None)
    assert vpn_connection.killswitch_armed_lan_dns() is None

"""Audit tier-2 S6 round 2: nothing a profile or the filesystem supplies can add a pf rule.

`ipaddress.ip_address('fe80::1%a\\npass out quick all')` parses (scope id), and the
raw string used to be pasted into the rule text, so a hostile endpoint could add
`pass out quick all`. The generated rules are now built from re-rendered
addresses only and every line must match an allow-list grammar before root sees
it. Nothing here runs pfctl, sudo or osascript.
"""

from __future__ import annotations

import socket
import types

import pytest

from agents.vpn_agent.services import killswitch as ks

REPRO = "fe80::1%a\npass out quick all"


@pytest.fixture
def pf(monkeypatch, tmp_path):
    state = types.SimpleNamespace(scripts=[], validated=[], dns_calls=[])
    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: 1)
    monkeypatch.setattr(ks, "WG_RUN_DIR", tmp_path / "wgrun")
    monkeypatch.setattr(ks, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(ks, "_run_privileged",
                        lambda script, why: state.scripts.append(script) or (True, "ok"))

    def getaddrinfo(host, *a, **k):
        state.dns_calls.append(host)
        raise socket.gaierror(8, "no")
    monkeypatch.setattr(ks.socket, "getaddrinfo", getaddrinfo)
    return state


def no_extra_rule(text: str) -> bool:
    return "pass out quick all" not in text and "pass all" not in text


# ── endpoints ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    REPRO,
    "fe80::1%en0",                       # a scope id on its own
    "203.0.113.7\npass out quick all",
    "203.0.113.7\r\npass out quick all",
    "203.0.113.7\t",
    "203.0.113.7 ",
    " 203.0.113.7",
    "203.0.113.7\x0b",
    "203.0.113.7 pass out quick all",
    "203.0.113.٧",
    "vpn.example.com\npass out quick all",
    "vpn example.com",
])
def test_a_hostile_endpoint_is_never_resolved_or_stored(pf, host):
    addresses, unresolved = ks.resolve_endpoints([host])
    assert addresses == [] and unresolved == [host]
    assert pf.dns_calls == []                      # refused before any lookup


def test_the_reproduced_string_does_not_yield_an_extra_rule(pf):
    ok, message = ks.arm([REPRO], allow=[("udp", 51820)])
    assert ok is False and pf.scripts == []
    assert "scope" in message.lower() or "plain" in message.lower()
    assert not ks.rules_path().exists() or no_extra_rule(ks.rules_path().read_text())


def test_the_reproduced_string_next_to_a_good_endpoint_leaves_only_the_good_rules(pf):
    ok, _ = ks.arm(["203.0.113.7", REPRO], allow=[("udp", 51820)])
    assert ok is True
    (script,) = pf.scripts
    assert no_extra_rule(script) and "fe80::1%a" not in script
    assert "pass out quick inet proto udp from any to 203.0.113.7 port 51820" in script


def test_a_clean_address_is_rendered_from_the_parsed_value_not_the_input(pf):
    addresses, _ = ks.resolve_endpoints(["2001:0DB8:0000:0000:0000:0000:0000:0001"])
    assert addresses == ["2001:db8::1"]


def test_a_link_local_endpoint_with_a_scope_id_is_refused_with_a_clear_message(pf):
    ok, message = ks.arm(["fe80::1%en0"], allow=[("udp", 51820)])
    assert ok is False and "scope" in message.lower() and "fe80::1%en0" in message
    assert pf.scripts == []


def test_resolved_addresses_with_a_scope_id_are_dropped(pf, monkeypatch):
    monkeypatch.setattr(ks.socket, "getaddrinfo", lambda host, *a, **k: [
        (0, 0, 0, "", ("fe80::1%en0", 0)), (0, 0, 0, "", ("203.0.113.9", 0))])
    addresses, unresolved = ks.resolve_endpoints(["vpn.example.com"])
    assert addresses == ["203.0.113.9"]


@pytest.mark.parametrize("endpoint", [REPRO, "203.0.113.7\npass out quick all", "203.0.113.7\tpass"])
def test_a_profile_endpoint_cannot_reach_the_rules(pf, endpoint):
    from services import vpn_connection
    ok, _ = vpn_connection.arm_killswitch(
        {"name": "x", "protocol": "WireGuard", "endpoint": endpoint, "interface": "wg0", "port": 51820})
    assert ok is False and pf.scripts == []


def test_an_endpoint_from_a_profile_conf_line_cannot_reach_the_rules(pf, tmp_path):
    from services import vpn_connection
    conf = tmp_path / "wgx.conf"
    conf.write_text("[Interface]\nPrivateKey = x\n[Peer]\nPublicKey = y\n"
                    "Endpoint = fe80::1%a\tpass out quick all:51820\nAllowedIPs = 0.0.0.0/0\n")
    profile = vpn_connection.profile_from_config(str(conf))
    ok, _ = vpn_connection.arm_killswitch(profile)
    assert pf.scripts == [] or all(no_extra_rule(s) for s in pf.scripts)
    assert ok is False


# ── tunnel interface names from /var/run/wireguard ──────────────────────────

def test_a_hostile_interface_name_file_is_ignored(pf, tmp_path):
    run = tmp_path / "wgrun"
    run.mkdir()
    (run / "good.name").write_text("utun7\n")
    (run / "bad1.name").write_text("utun7 all\npass out quick all\n")
    (run / "bad2.name").write_text("en0\n")
    (run / "bad3.name").write_text("utun99999\n")
    (run / "bad4.name").write_text("utun1\x00\n")
    assert ks.active_tunnel_interfaces() == ["utun7"]


def test_a_hostile_interface_name_never_reaches_the_script(pf, tmp_path):
    run = tmp_path / "wgrun"
    run.mkdir()
    (run / "bad.name").write_text("utun7 all\npass out quick all\n")
    ok, _ = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is True and all(no_extra_rule(s) for s in pf.scripts)


# ── loadable_rules: every line must be an expected rule ─────────────────────

def good_rules():
    return ks.build_rules(["203.0.113.7", "2001:db8::1"], [("udp", 51820), ("tcp", 443)],
                          interfaces=["utun7", "tun0"])


def test_the_real_generated_rules_all_pass_the_grammar():
    text = ks.loadable_rules(good_rules())
    assert "block drop all" in text and "pass quick on utun7 all" in text


@pytest.mark.parametrize("bad", [
    "pass out quick all",
    "pass all",
    "pass out quick inet proto udp from any to 203.0.113.7 port 51820 \npass out quick all",
    "pass out quick inet proto udp from any to 203.0.113.7\rpass out quick all port 1",
    "pass out quick inet proto udp from any to 203.0.113.7 port 51820 keep state",
    "pass out quick inet proto udp from any to fe80::1%a port 51820",
    "pass out quick inet proto udp from any to 999.1.1.1 port 51820",
    "pass out quick inet proto udp from any to 203.0.113.7 port 99999",
    "pass quick on en0 all",
    "pass quick on utun7x all",
    "pass quick inet from 10.0.0.0/8 to 0.0.0.0/0",
    "block drop all\x0bpass out quick all",
])
def test_a_line_outside_the_grammar_is_refused(bad):
    text = good_rules() + bad + "\n"
    with pytest.raises(ValueError):
        ks.loadable_rules(text)


def test_an_embedded_newline_in_a_value_is_refused():
    rules = ks.build_rules(["203.0.113.7\npass out quick all"], [("udp", 1)], interfaces=["utun7"])
    with pytest.raises(ValueError):
        ks.loadable_rules(rules)


def test_arm_refuses_when_the_grammar_rejects_the_rules(pf, monkeypatch):
    monkeypatch.setattr(ks, "build_rules", lambda *a, **k: "block drop all\npass out quick all\n")
    ok, message = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and pf.scripts == [] and "not an expected rule" in message


# ── validate() looks at the text root will load ─────────────────────────────

def test_validate_runs_on_a_private_copy_of_the_exact_loaded_text(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_is_registered", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: 1)
    monkeypatch.setattr(ks, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(ks, "WG_RUN_DIR", tmp_path / "none")

    def validate(path=None):
        import os, stat
        seen["path"] = path
        seen["text"] = path.read_text()
        seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        return True, ""
    scripts = []
    monkeypatch.setattr(ks, "validate", validate)
    monkeypatch.setattr(ks, "_run_privileged", lambda s, w: scripts.append(s) or (True, "ok"))
    ok, _ = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is True
    assert seen["mode"] == 0o600 and seen["path"] != ks.rules_path()
    assert seen["text"] == ks.loadable_rules(ks.rules_path().read_text())
    assert seen["text"] in scripts[0]                 # the same text is what root gets
    assert not seen["path"].exists()                  # the private copy is removed


def test_a_rejected_validation_removes_the_private_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(ks.paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(ks, "is_supported", lambda: True)
    monkeypatch.setattr(ks, "_boot_time", lambda: 1)
    monkeypatch.setattr(ks, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(ks, "validate", lambda path=None: (False, "syntax error"))
    monkeypatch.setattr(ks, "_run_privileged", lambda s, w: (_ for _ in ()).throw(AssertionError))
    ok, message = ks.arm(["203.0.113.7"], allow=[("udp", 51820)])
    assert ok is False and "syntax error" in message
    assert [p.name for p in (tmp_path / "state").iterdir() if p.name.startswith("killswitch-check")] == []


def test_a_lan_rule_may_only_name_the_known_private_ranges():
    for net in ("0.0.0.0/8", "10.0.0.0/9", "11.0.0.0/8"):
        with pytest.raises(ValueError):
            ks.loadable_rules(good_rules() + f"pass quick inet from {net} to {net}\n")


def test_an_endpoint_whose_every_answer_is_filtered_counts_as_unresolved(monkeypatch):
    """getaddrinfo succeeds but only returns a scoped IPv6 address, which cannot go
    in a pf rule: that must refuse to arm like any unresolved name, not arm with no
    endpoint exemption."""
    scoped = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%en0", 0, 0, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: scoped)
    addresses, unresolved = ks.resolve_endpoints(["vpn.example.org"])
    assert addresses == [] and unresolved == ["vpn.example.org"]


def test_a_name_with_one_usable_answer_still_resolves(monkeypatch):
    mixed = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%en0", 0, 0, 0)),
             (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: mixed)
    assert ks.resolve_endpoints(["vpn.example.org"]) == (["203.0.113.9"], [])

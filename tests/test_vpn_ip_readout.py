"""The Tunnel panel's 'Your IP' formatting — pure display logic, no widgets."""

from ui.panels.vpn import VpnPanel


def test_local_ip_shows_lan_and_each_tunnel_interface():
    text = VpnPanel._format_local_ip({
        "primary": "192.168.1.42",
        "interfaces": {"en0": ["192.168.1.42"], "utun4": ["10.2.0.2"]},
        "tunnels": ["utun4"],
    })
    assert "192.168.1.42 (LAN)" in text
    assert "10.2.0.2 (utun4, tunnel)" in text


def test_local_ip_says_when_no_tunnel_is_up():
    text = VpnPanel._format_local_ip({
        "primary": "192.168.1.42",
        "interfaces": {"en0": ["192.168.1.42"]},
        "tunnels": [],
    })
    assert "no tunnel interface up" in text


def test_public_ip_includes_location_owner_and_privacy_flags():
    text = VpnPanel._format_public_ip({
        "ip": "203.0.113.5", "city": "Berlin", "country": "DE",
        "org": "AS64500 Example", "timezone": "Europe/Berlin",
        "privacy_flags": ["vpn", "hosting"],
    })
    assert text.startswith("203.0.113.5")
    assert "Berlin, DE" in text
    assert "AS64500 Example" in text
    assert "flagged: vpn, hosting" in text


def test_public_ip_omits_unknown_fields_and_absent_flags():
    text = VpnPanel._format_public_ip({
        "ip": "203.0.113.9", "city": "Unknown", "country": "Unknown",
        "org": "Unknown", "timezone": "Unknown",
    })
    assert text == "203.0.113.9"


def test_dns_leak_format_reports_resolvers_and_possible_leak():
    text = VpnPanel._format_dns_leak({
        "status": "ok", "resolver_count": 2, "distinct_asns": 2, "leak": True,
        "conclusion": "leaking", "resolvers": [
            {"ip": "8.8.8.8", "asn": "AS15169", "country": "US"},
            {"ip": "1.1.1.1", "asn": "AS13335", "country": "US"},
        ]})
    assert "2 resolver(s)" in text
    assert "2 network(s)" in text
    assert "possible leak" in text
    assert "8.8.8.8" in text


def test_dns_leak_format_no_leak_and_error_and_empty():
    ok = VpnPanel._format_dns_leak({
        "status": "ok", "resolver_count": 1, "distinct_asns": 1, "leak": False,
        "resolvers": [{"ip": "10.0.0.53", "asn": "AS3320"}], "conclusion": ""})
    assert "no leak detected" in ok

    err = VpnPanel._format_dns_leak({"status": "error", "detail": "bash.ws down"})
    assert "bash.ws down" in err

    empty = VpnPanel._format_dns_leak({
        "status": "ok", "resolver_count": 0, "conclusion": "No resolvers were observed."})
    assert "No resolvers" in empty

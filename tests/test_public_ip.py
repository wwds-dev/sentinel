from agents.vpn_agent.services import public_ip


_IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.42 netmask 0xffffff00 broadcast 192.168.1.255
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.10.10 netmask 0xffff0000 broadcast 169.254.255.255
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1420
\tinet 10.2.0.2 --> 10.2.0.2 netmask 0xffffffff
"""


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_parse_ifconfig_keeps_real_ipv4_and_drops_loopback_and_link_local():
    interfaces = public_ip.parse_ifconfig(_IFCONFIG)
    assert interfaces == {"en0": ["192.168.1.42"], "utun4": ["10.2.0.2"]}


def test_looks_like_tunnel_recognises_tunnels_only():
    assert public_ip.looks_like_tunnel("utun4")
    assert public_ip.looks_like_tunnel("wg0")
    assert not public_ip.looks_like_tunnel("en0")


def test_get_local_addresses_reports_primary_and_tunnels(monkeypatch):
    class _Proc:
        stdout = _IFCONFIG

    monkeypatch.setattr(public_ip.subprocess, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(public_ip, "_primary_outbound_ip", lambda: "192.168.1.42")

    result = public_ip.get_local_addresses()
    assert result["primary"] == "192.168.1.42"
    assert result["interfaces"] == {"en0": ["192.168.1.42"], "utun4": ["10.2.0.2"]}
    assert result["tunnels"] == ["utun4"]


def test_get_ip_details_uses_ipinfo_and_privacy_flags_when_key_present(monkeypatch):
    monkeypatch.setenv("IPINFO_API_KEY", "tok_123")
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["params"] = kwargs.get("params")
        return _Response({
            "ip": "203.0.113.5", "city": "Berlin", "country": "DE",
            "org": "AS64500 Example", "timezone": "Europe/Berlin",
            "privacy": {"vpn": True, "hosting": True, "proxy": False},
        })

    monkeypatch.setattr(public_ip.requests, "get", fake_get)
    result = public_ip.get_ip_details()
    assert seen["url"] == public_ip.IPINFO_SELF_URL
    # Token in the Authorization header, not the query string, so a transport
    # error cannot leak the key into result["ip"] / the run log.
    assert seen["headers"].get("Authorization") == "Bearer tok_123"
    assert not seen["params"]
    assert result["ip"] == "203.0.113.5"
    assert result["privacy_flags"] == ["vpn", "hosting"]


def test_get_ip_details_falls_back_to_ipapi_without_a_key(monkeypatch):
    monkeypatch.delenv("IPINFO_API_KEY", raising=False)
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen["url"] = url
        return _Response({"ip": "203.0.113.9", "country_name": "France",
                          "city": "Paris", "org": "AS1234 FR", "timezone": "Europe/Paris"})

    monkeypatch.setattr(public_ip.requests, "get", fake_get)
    result = public_ip.get_ip_details()
    assert seen["url"] == public_ip.IPAPI_URL
    assert result["country"] == "France"
    assert "privacy_flags" not in result


def test_get_ip_snapshot_can_skip_the_public_lookup(monkeypatch):
    monkeypatch.setattr(public_ip, "get_local_addresses",
                        lambda: {"primary": "192.168.1.42", "interfaces": {}, "tunnels": []})
    called = []
    monkeypatch.setattr(public_ip, "get_ip_details",
                        lambda: called.append(True) or {"ip": "x"})

    snapshot = public_ip.get_ip_snapshot(include_public=False)
    assert snapshot["public"] is None
    assert snapshot["local"]["primary"] == "192.168.1.42"
    assert called == []

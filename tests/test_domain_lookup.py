import pytest

from providers import domain_lookup

# Captured before the autouse fixture replaces it, so the parsing tests can
# exercise the real Criminal IP source.
_REAL_CRIMINALIP = domain_lookup._criminalip


@pytest.fixture(autouse=True)
def _no_network_sources(monkeypatch):
    """The sources a test does not name answer instantly and offline."""
    monkeypatch.setattr(domain_lookup, "_asn", lambda target: {"asn": "AS64500"})
    monkeypatch.setattr(domain_lookup, "_passive_dns", lambda target: {"records": []})
    monkeypatch.setattr(domain_lookup, "_dshield", lambda target: {"reports": 0})
    monkeypatch.setattr(domain_lookup, "_shodan_internetdb", lambda target: {"found": False})
    monkeypatch.setattr(domain_lookup, "_ipinfo", lambda target: {"country": "ZZ"})
    monkeypatch.setattr(domain_lookup, "_criminalip", lambda target: {"risk_inbound": "Safe"})
    monkeypatch.setattr(domain_lookup, "_crtsh", lambda target: {"total_unique": 0})
    monkeypatch.setattr(domain_lookup, "_wayback", lambda target: {"archived": False})
    # Criminal IP is key-gated; default the tests to "no key" so the IP source
    # list is deterministic regardless of the developer's real .env. Tests that
    # exercise it set the key themselves.
    monkeypatch.delenv("CRIMINALIP_API_KEY", raising=False)


def test_domain_lookup_reports_each_source_and_keeps_errors(monkeypatch):
    monkeypatch.setattr(domain_lookup, "_whois", lambda target: {"registrar": "R"})
    monkeypatch.setattr(domain_lookup, "_dns", lambda target: {"error": "DNS unavailable"})
    monkeypatch.setattr(
        domain_lookup, "_crtsh", lambda target: {"total_unique": 1, "sample": [target]}
    )
    monkeypatch.setattr(domain_lookup, "_wayback", lambda target: {"archived": False})
    progress = []

    result = domain_lookup.lookup(
        "https://example.com/path",
        on_progress=lambda source, status: progress.append((source, status)),
    )

    assert result["query"] == "example.com"
    assert result["whois"] == {"registrar": "R"}
    assert result["dns"] == {"error": "DNS unavailable"}
    assert result["archive"] == {"archived": False}
    assert [item["source"] for item in result["sources_contacted"]] == [
        "WHOIS", "DNS", "Team Cymru IP-to-ASN", "Mnemonic passive DNS",
        "Certificate transparency (crt.sh)", "Wayback Machine",
    ]
    assert [item["status"] for item in result["sources_contacted"]] == [
        "checked", "error", "checked", "checked", "checked", "checked",
    ]
    assert "attack_reports" not in result   # DShield is for IPs only
    assert ("WHOIS", "checking") in progress
    assert ("Certificate transparency (crt.sh)", "checked") in progress
    assert ("Wayback Machine", "checked") in progress


def test_ipinfo_source_is_included_only_when_its_key_is_set(monkeypatch):
    monkeypatch.setattr(domain_lookup, "_whois", lambda target: {})
    monkeypatch.setattr(domain_lookup, "_dns", lambda target: {})

    monkeypatch.delenv("IPINFO_API_KEY", raising=False)
    without = domain_lookup.lookup("192.0.2.1")
    assert "IPinfo" not in [item["source"] for item in without["sources_contacted"]]
    assert "ip_details" not in without

    monkeypatch.setenv("IPINFO_API_KEY", "test-key")
    withkey = domain_lookup.lookup("192.0.2.1")
    assert "IPinfo" in [item["source"] for item in withkey["sources_contacted"]]
    assert withkey["ip_details"] == {"country": "ZZ"}   # from the autouse stub


def test_cancel_between_sources_returns_partial_result(monkeypatch):
    monkeypatch.setattr(domain_lookup, "_whois", lambda target: {"country": "ZZ"})
    monkeypatch.setattr(
        domain_lookup, "_dns",
        lambda target: (_ for _ in ()).throw(AssertionError("DNS must not run")),
    )
    checks = iter((False, True))

    result = domain_lookup.lookup("192.0.2.1", should_stop=lambda: next(checks))

    assert result["cancelled"] is True
    assert result["whois"] == {"country": "ZZ"}
    assert "dns" not in result


def test_ipv6_is_preserved_and_certificate_lookup_is_skipped(monkeypatch):
    monkeypatch.setenv("IPINFO_API_KEY", "test-key")  # so the IPinfo source is included
    monkeypatch.setattr(domain_lookup, "_whois", lambda target: {})
    monkeypatch.setattr(domain_lookup, "_dns", lambda target: {"AAAA": [target]})
    monkeypatch.setattr(
        domain_lookup, "_crtsh",
        lambda target: (_ for _ in ()).throw(AssertionError("crt.sh must not run")),
    )
    monkeypatch.setattr(
        domain_lookup, "_wayback",
        lambda target: (_ for _ in ()).throw(AssertionError("Wayback must not run")),
    )

    result = domain_lookup.lookup("2001:db8::1")

    assert result["type"] == "ip"
    assert result["query"] == "2001:db8::1"
    assert "certificates" not in result
    assert "archive" not in result
    assert [item["source"] for item in result["sources_contacted"]] == [
        "WHOIS", "DNS", "Team Cymru IP-to-ASN", "SANS DShield",
        "Shodan InternetDB", "IPinfo", "Mnemonic passive DNS",
    ]


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_wayback_asks_for_the_earliest_and_the_latest_snapshot(monkeypatch):
    monkeypatch.undo()   # use the real _wayback, not the autouse stub
    seen = []

    def fake_get(url, params=None, **kwargs):
        seen.append(params)
        stamp = "19990102030405" if "timestamp" in params else "20260901000000"
        return _Response({"archived_snapshots": {"closest": {
            "available": True, "timestamp": stamp,
            "url": f"http://web.archive.org/web/{stamp}/example.com",
        }}})

    monkeypatch.setattr(domain_lookup.requests, "get", fake_get)
    result = domain_lookup._wayback("example.com")

    assert seen == [
        {"url": "example.com", "timestamp": "19960101"}, {"url": "example.com"},
    ]
    assert result["archived"] is True
    assert result["first_snapshot"]["date"] == "1999-01-02"
    assert result["latest_snapshot"]["date"] == "2026-09-01"
    assert result["all_captures"] == "https://web.archive.org/web/*/example.com"


def test_wayback_reports_a_never_archived_domain_as_a_fact_not_an_error(monkeypatch):
    monkeypatch.undo()   # use the real _wayback, not the autouse stub
    monkeypatch.setattr(
        domain_lookup.requests, "get",
        lambda *a, **k: _Response({"archived_snapshots": {}}),
    )
    result = domain_lookup._wayback("never-archived.example")
    assert result["archived"] is False
    assert "error" not in result


def test_wayback_http_failure_is_an_error(monkeypatch):
    monkeypatch.undo()   # use the real _wayback, not the autouse stub
    monkeypatch.setattr(
        domain_lookup.requests, "get", lambda *a, **k: _Response({}, status_code=503)
    )
    assert domain_lookup._wayback("example.com") == {"error": "Wayback Machine HTTP 503"}



# ── Team Cymru IP-to-ASN (over DNS) ──────────────────────────────────────────

def test_cymru_names_reverse_the_address():
    assert domain_lookup._cymru_origin_name("192.0.2.1") == "1.2.0.192.origin.asn.cymru.com"
    v6 = domain_lookup._cymru_origin_name("2001:db8::1")
    assert v6.endswith(".origin6.asn.cymru.com")
    assert v6.startswith("1.0.0.0.") and v6.count(".") == 32 + 3


class _Txt:
    def __init__(self, text):
        self.strings = [text.encode()]


def _fake_resolver(monkeypatch, answers):
    import dns.resolver

    def resolve(name, rtype, lifetime=None):
        value = answers.get((name, rtype))
        if value is None:
            raise dns.resolver.NXDOMAIN()
        return value

    monkeypatch.setattr(dns.resolver, "resolve", resolve)


def test_asn_of_a_domain_uses_its_first_ipv4_address(monkeypatch):
    monkeypatch.undo()   # use the real _asn, not the autouse stub
    _fake_resolver(monkeypatch, {
        ("example.com", "A"): ["192.0.2.10"],
        ("10.2.0.192.origin.asn.cymru.com", "TXT"):
            [_Txt("64500 | 192.0.2.0/24 | ZZ | ripencc | 2001-01-01")],
        ("AS64500.asn.cymru.com", "TXT"):
            [_Txt("64500 | ZZ | ripencc | 2001-01-01 | EXAMPLE-NET - Example Net")],
    })
    result = domain_lookup._asn("example.com")
    assert result == {
        "ip": "192.0.2.10", "announced": True, "asn": "AS64500",
        "prefix": "192.0.2.0/24", "country": "ZZ", "registry": "ripencc",
        "allocated": "2001-01-01", "as_name": "EXAMPLE-NET - Example Net",
    }


def test_an_unannounced_address_is_a_fact_not_an_error(monkeypatch):
    monkeypatch.undo()
    _fake_resolver(monkeypatch, {})
    result = domain_lookup._asn("192.0.2.1")
    assert result["announced"] is False
    assert "error" not in result


# ── Mnemonic passive DNS ─────────────────────────────────────────────────────

def test_passive_dns_dates_fall_back_for_partial_results(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **k: _Response({
        "count": 1000,
        "data": [{
            "query": "busy.example", "rrtype": "a", "answer": "192.0.2.1", "times": 8,
            "firstSeenTimestamp": 0, "lastSeenTimestamp": 0,
            "createdTimestamp": 1728394412222, "lastUpdatedTimestamp": 1730116390693,
            "flags": ["partialResult"],
        }],
    }))
    result = domain_lookup._passive_dns("192.0.2.1")
    assert result["total_records"] == 1000
    assert result["records"][0] == {
        "name": "busy.example", "type": "A", "answer": "192.0.2.1",
        "first_seen": "2024-10-08", "last_seen": "2024-10-28", "times_seen": 8,
    }
    assert "partial" in result
    assert result["note"] == "Names seen resolving to this IP."


def test_passive_dns_rate_limit_is_reported(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({}, status_code=429))
    assert "rate limit" in domain_lookup._passive_dns("example.com")["error"]


# ── SANS DShield ─────────────────────────────────────────────────────────────

def test_dshield_keeps_attack_counts_and_feeds(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **k: _Response({"ip": {
        "count": 12, "attacks": 3, "mindate": "2026-01-01", "maxdate": "2026-02-01",
        "threatfeeds": {"forumspam": {"firstseen": "2020-01-15", "lastseen": "2020-01-15"}},
        "ssh": {"attempts": 13, "start": "2021-12-11", "end": "2021-12-11"},
        "weblogs": {"count": 889, "firstseen": "2021-12-04", "lastseen": "2021-12-12"},
        "network": "192.0.2.0/24", "asname": "EXAMPLE", "asabusecontact": "abuse@example.net",
    }}))
    result = domain_lookup._dshield("192.0.2.1")
    assert (result["reports"], result["targets_attacked"]) == (12, 3)
    assert result["threat_feeds"] == [
        {"feed": "forumspam", "first_seen": "2020-01-15", "last_seen": "2020-01-15"}
    ]
    assert result["ssh_brute_force"]["attempts"] == 13
    assert result["web_attacks"]["requests"] == 889


def test_dshield_clean_ip_reports_zero_not_null(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **k: _Response({"ip": {
        "count": None, "attacks": None, "threatfeeds": {},
    }}))
    result = domain_lookup._dshield("192.0.2.1")
    assert (result["reports"], result["targets_attacked"]) == (0, 0)
    assert result["ssh_brute_force"] is None and result["web_attacks"] is None


# ── Shodan InternetDB ────────────────────────────────────────────────────────

def test_shodan_internetdb_extracts_ports_software_and_vulns(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get", lambda *a, **k: _Response({
        "ip": "192.0.2.1", "ports": [22, 443], "hostnames": ["host.example"],
        "cpes": ["cpe:/a:nginx:nginx"], "tags": ["cloud"], "vulns": ["CVE-2021-1234"],
    }))
    result = domain_lookup._shodan_internetdb("192.0.2.1")
    assert result["found"] is True
    assert result["ports"] == [22, 443]
    assert result["software"] == ["cpe:/a:nginx:nginx"]
    assert result["vulnerabilities"] == ["CVE-2021-1234"]


def test_shodan_internetdb_404_is_a_clean_result_not_an_error(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({}, status_code=404))
    result = domain_lookup._shodan_internetdb("192.0.2.1")
    assert result["found"] is False
    assert "error" not in result


def test_shodan_internetdb_other_http_status_is_an_error(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({}, status_code=502))
    assert domain_lookup._shodan_internetdb("192.0.2.1") == {
        "error": "Shodan InternetDB HTTP 502"
    }


# ── IPinfo ───────────────────────────────────────────────────────────────────

def test_ipinfo_sends_the_key_when_present_and_surfaces_privacy_flags(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("IPINFO_API_KEY", "tok_123")
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["params"] = kwargs.get("params")
        return _Response({
            "ip": "192.0.2.1", "city": "Berlin", "region": "Berlin",
            "country": "DE", "org": "AS64500 Example Net",
            "privacy": {"vpn": True, "proxy": False, "tor": False,
                        "relay": False, "hosting": True},
        })

    monkeypatch.setattr(domain_lookup.requests, "get", fake_get)
    result = domain_lookup._ipinfo("192.0.2.1")
    assert seen["url"] == "https://ipinfo.io/192.0.2.1/json"
    # The token goes in the Authorization header, never the query string, so a
    # transport error's message cannot leak it.
    assert seen["headers"].get("Authorization") == "Bearer tok_123"
    assert not seen["params"]
    assert result["country"] == "DE"
    assert result["org"] == "AS64500 Example Net"
    assert result["privacy_flags"] == ["vpn", "hosting"]


def test_ipinfo_without_a_key_self_skips_without_a_request(monkeypatch):
    monkeypatch.undo()
    monkeypatch.delenv("IPINFO_API_KEY", raising=False)
    called = []

    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: called.append(True) or _Response({}))
    result = domain_lookup._ipinfo("192.0.2.1")
    # No key: it must self-skip and make no anonymous OSINT request.
    assert called == []
    assert "IPINFO_API_KEY" in result["error"]


def test_ipinfo_rate_limit_is_reported(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("IPINFO_API_KEY", "tok_123")
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({}, status_code=429))
    assert "rate limit" in domain_lookup._ipinfo("192.0.2.1")["error"]


def test_ipinfo_reports_a_bogon_address_as_a_fact(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("IPINFO_API_KEY", "tok_123")
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({"ip": "10.0.0.1", "bogon": True}))
    result = domain_lookup._ipinfo("10.0.0.1")
    assert result["bogon"] is True
    assert "error" not in result


# ── Criminal IP (key-gated) ───────────────────────────────────────────────────

def test_criminalip_source_included_only_when_key_is_set(monkeypatch):
    monkeypatch.setattr(domain_lookup, "_whois", lambda target: {})
    monkeypatch.setattr(domain_lookup, "_dns", lambda target: {})

    monkeypatch.delenv("CRIMINALIP_API_KEY", raising=False)
    without = domain_lookup.lookup("192.0.2.1")
    assert "Criminal IP" not in [item["source"] for item in without["sources_contacted"]]
    assert "ip_reputation" not in without

    monkeypatch.setenv("CRIMINALIP_API_KEY", "cip-key")
    withkey = domain_lookup.lookup("192.0.2.1")
    assert "Criminal IP" in [item["source"] for item in withkey["sources_contacted"]]
    assert withkey["ip_reputation"] == {"risk_inbound": "Safe"}   # from the autouse stub


def test_criminalip_parses_score_flags_owner_and_ports(monkeypatch):
    monkeypatch.setenv("CRIMINALIP_API_KEY", "cip-key")
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen["url"] = url
        seen["params"] = params
        seen["headers"] = headers
        return _Response({
            "status": 200,
            "score": {"inbound": "Dangerous", "outbound": "Low"},
            "issues": {"is_vpn": True, "is_proxy": False, "is_tor": True,
                       "is_hosting": True, "is_scanner": False},
            "whois": {"data": [{"as_name": "EXAMPLE-AS", "org_name": "Example Ltd",
                                "org_country_code": "US"}]},
            "current_opened_port": {"data": [{"open_port": 443}, {"open_port": 22}]},
        })

    monkeypatch.setattr(domain_lookup.requests, "get", fake_get)
    out = _REAL_CRIMINALIP("1.2.3.4")
    assert out["risk_inbound"] == "Dangerous"
    assert out["risk_outbound"] == "Low"
    assert out["flags"] == ["vpn", "tor", "hosting"]
    assert out["as_name"] == "EXAMPLE-AS"
    assert out["org"] == "Example Ltd"
    assert out["country"] == "US"
    assert out["open_ports"] == [22, 443]
    # Key travels only in the x-api-key header, never the URL/query string.
    assert seen["headers"]["x-api-key"] == "cip-key"
    assert seen["params"] == {"ip": "1.2.3.4"}
    assert "cip-key" not in seen["url"]


def test_criminalip_rejected_key_http_401_is_clean_error(monkeypatch):
    monkeypatch.setenv("CRIMINALIP_API_KEY", "bad")
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({}, status_code=401))
    out = _REAL_CRIMINALIP("1.2.3.4")
    assert "rejected the key" in out["error"]


def test_criminalip_in_body_status_non_200_is_error(monkeypatch):
    monkeypatch.setenv("CRIMINALIP_API_KEY", "cip-key")
    monkeypatch.setattr(domain_lookup.requests, "get",
                        lambda *a, **k: _Response({"status": 401}, status_code=200))
    out = _REAL_CRIMINALIP("1.2.3.4")
    assert "rejected the key" in out["error"]


def test_criminalip_missing_key_self_skips(monkeypatch):
    monkeypatch.delenv("CRIMINALIP_API_KEY", raising=False)
    out = _REAL_CRIMINALIP("1.2.3.4")
    assert "CRIMINALIP_API_KEY" in out["error"]

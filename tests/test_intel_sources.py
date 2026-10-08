"""The key-gated threat-intelligence sources: parsing, failure, and limits.

Every test is offline: requests is replaced by a fake that returns canned
bodies, so nothing here calls a real service or needs a real key.
"""
import json

import pytest
import requests

from providers import domain_lookup, email_lookup, exposure_lookup, intel_sources


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


@pytest.fixture
def calls(monkeypatch):
    """Record each outgoing call; answer from ``calls.replies`` by URL substring."""
    class Recorder(list):
        replies: dict = {}

    rec = Recorder()

    def fake(method, url, **kwargs):
        rec.append({"method": method, "url": url, **kwargs})
        for fragment, reply in rec.replies.items():
            if fragment in url:
                return reply() if callable(reply) else reply
        return FakeResponse(500)

    def fake_get(url, **kwargs):
        return fake("GET", url, **kwargs)

    def fake_post(url, **kwargs):
        return fake("POST", url, **kwargs)

    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    return rec


def _keys(monkeypatch, *names):
    for name in names:
        monkeypatch.setenv(name, f"test-{name.lower()}")


# ── which sources run ────────────────────────────────────────────────────────

def test_nothing_runs_without_a_key():
    assert intel_sources.configured("ip") == []
    assert intel_sources.configured("domain") == []


def test_a_key_switches_on_exactly_its_sources(monkeypatch):
    _keys(monkeypatch, "VIRUSTOTAL_API_KEY")
    assert [s.label for s in intel_sources.configured("ip")] == ["VirusTotal"]
    assert [s.label for s in intel_sources.configured("domain")] == ["VirusTotal"]
    _keys(monkeypatch, "SHODAN_API_KEY")
    assert "Shodan" in [s.label for s in intel_sources.configured("ip")]
    assert "Shodan DNS" in [s.label for s in intel_sources.configured("domain")]


@pytest.fixture
def quiet_domain_lookup(monkeypatch):
    for name in ("_whois", "_dns", "_asn", "_passive_dns", "_dshield",
                 "_shodan_internetdb", "_crtsh", "_wayback", "_ipinfo", "_criminalip"):
        monkeypatch.setattr(domain_lookup, name, lambda target: {})


@pytest.mark.parametrize("target,kind", [("203.0.113.5", "ip"), ("example.com", "domain")])
def test_consent_list_and_lookup_agree(monkeypatch, quiet_domain_lookup, target, kind):
    """What Trace asks permission for is exactly what the lookup contacts."""
    every_key = {s.env for s in intel_sources.SOURCES} | {"IPINFO_API_KEY", "CRIMINALIP_API_KEY"}
    _keys(monkeypatch, *every_key)
    for source in intel_sources.SOURCES:
        monkeypatch.setattr(intel_sources, source.func, lambda t: {"ok": True})

    named = domain_lookup.keyed_labels(target)
    result = domain_lookup.lookup(target)
    contacted = [item["source"] for item in result["sources_contacted"]]
    keyed_contacted = [label for label in contacted if label in set(named)]
    assert keyed_contacted == named
    expected = [s.label for s in intel_sources.SOURCES if kind in s.kinds]
    assert [label for label in named if label not in ("IPinfo", "Criminal IP")] == expected
    for source in intel_sources.SOURCES:
        assert (source.result_key in result) == (kind in source.kinds)


# ── parsing ──────────────────────────────────────────────────────────────────

def test_abuseipdb(monkeypatch, calls):
    _keys(monkeypatch, "ABUSEIPDB_API_KEY")
    calls.replies = {"abuseipdb": FakeResponse(body={"data": {
        "abuseConfidenceScore": 87, "totalReports": 41, "numDistinctUsers": 12,
        "usageType": "Data Center/Web Hosting/Transit", "isTor": False, "countryCode": "NL"}})}
    out = intel_sources.abuseipdb("203.0.113.5")
    assert out["abuse_confidence"] == 87 and out["total_reports"] == 41
    assert calls[0]["headers"]["Key"] == "test-abuseipdb_api_key"
    assert calls[0]["params"]["ipAddress"] == "203.0.113.5"


def test_greynoise_treats_404_as_not_seen(monkeypatch, calls):
    _keys(monkeypatch, "GREYNOISE_API_KEY")
    calls.replies = {"greynoise": FakeResponse(404)}
    out = intel_sources.greynoise("203.0.113.5")
    assert out["scanning_internet"] is False and "error" not in out


def test_virustotal_counts_and_flagging_engines(monkeypatch, calls):
    _keys(monkeypatch, "VIRUSTOTAL_API_KEY")
    calls.replies = {"virustotal": FakeResponse(body={"data": {"attributes": {
        "last_analysis_stats": {"malicious": 3, "suspicious": 1, "harmless": 60, "undetected": 20},
        "last_analysis_results": {
            "EngineA": {"category": "malicious"}, "EngineB": {"category": "harmless"},
            "EngineC": {"category": "suspicious"}},
        "reputation": -12, "categories": {"x": "phishing"}}}})}
    out = intel_sources.virustotal("example.com")
    assert out["malicious"] == 3 and out["flagged_by"] == ["EngineA", "EngineC"]
    assert out["categories"] == ["phishing"]
    assert "/domains/example.com" in calls[0]["url"]
    assert calls[0]["headers"]["x-apikey"]


def test_otx_ipv4_and_domain_paths(monkeypatch, calls):
    _keys(monkeypatch, "OTX_API_KEY")
    calls.replies = {"otx.alienvault": FakeResponse(body={"pulse_info": {"count": 2, "pulses": [
        {"name": "Botnet C2", "malware_families": [{"display_name": "Mirai"}], "tags": ["c2"]},
        {"name": "Scanner list", "adversary": "APT-X"}]}})}
    out = intel_sources.otx("203.0.113.5")
    assert out["pulse_count"] == 2 and out["malware_families"] == ["Mirai"]
    assert out["adversaries"] == ["APT-X"]
    assert "/indicators/IPv4/203.0.113.5/general" in calls[0]["url"]
    intel_sources.otx("example.com")
    assert "/indicators/domain/example.com/general" in calls[1]["url"]


def test_shodan_host_and_key_never_in_error_text(monkeypatch, calls):
    _keys(monkeypatch, "SHODAN_API_KEY")
    calls.replies = {"shodan.io/shodan/host": FakeResponse(body={
        "org": "Example Hosting", "ports": [443, 22], "vulns": ["CVE-2024-0001"],
        "data": [{"port": 22, "product": "OpenSSH", "version": "9.6", "transport": "tcp"}]})}
    out = intel_sources.shodan_host("203.0.113.5")
    assert out["ports"] == [22, 443] and out["services"][0]["product"] == "OpenSSH"
    assert out["vulnerabilities"] == ["CVE-2024-0001"]

    secret = "test-shodan_api_key"

    def boom():
        raise requests.exceptions.ConnectionError(
            f"Max retries exceeded with url: /shodan/host/1.2.3.4?key={secret}")
    calls.replies = {"shodan.io": boom}
    failed = intel_sources.shodan_host("203.0.113.5")
    assert "error" in failed and secret not in failed["error"]


def test_securitytrails_flattens_current_dns(monkeypatch, calls):
    _keys(monkeypatch, "SECURITYTRAILS_API_KEY")
    calls.replies = {"securitytrails": FakeResponse(body={
        "subdomain_count": 57,
        "current_dns": {"a": {"first_seen": "2019-01-01",
                              "values": [{"ip": "203.0.113.5", "ip_organization": "Example"}]},
                        "mx": {"values": [{"hostname": "mx.example.com"}]}}})}
    out = intel_sources.securitytrails("example.com")
    assert out["subdomain_count"] == 57
    assert out["current_dns"]["A"] == ["203.0.113.5 (Example)"]
    assert out["current_dns"]["MX"] == ["mx.example.com"]
    assert calls[0]["headers"]["APIKEY"]


def test_urlscan_domain_search_uses_the_header(monkeypatch, calls):
    _keys(monkeypatch, "URLSCAN_API_KEY")
    calls.replies = {"urlscan.io": FakeResponse(body={"total": 3, "results": [
        {"page": {"url": "https://example.com/", "ip": "203.0.113.5", "country": "US"},
         "task": {"time": "2026-10-01T00:00:00Z"}}]})}
    out = intel_sources.urlscan_domain("example.com")
    assert out["total_scans"] == 3 and out["hosting_ips"] == ["203.0.113.5"]
    assert calls[0]["headers"]["API-Key"] == "test-urlscan_api_key"
    assert "test-urlscan_api_key" not in calls[0]["url"]


def test_username_search_sends_the_urlscan_key_only_when_saved(monkeypatch, calls):
    from providers import username_lookup

    monkeypatch.setattr(username_lookup, "_github", lambda u: {})
    monkeypatch.setattr(username_lookup, "_keybase", lambda u: {})
    calls.replies = {"urlscan.io": FakeResponse(body={"results": [], "total": 0})}
    username_lookup.lookup("someone")
    assert "API-Key" not in calls[0]["headers"]
    _keys(monkeypatch, "URLSCAN_API_KEY")
    username_lookup.lookup("someone")
    assert calls[-1]["headers"]["API-Key"] == "test-urlscan_api_key"


# ── people and leaked data stay out ──────────────────────────────────────────

def test_hunter_reports_role_addresses_and_counts_never_people(monkeypatch, calls):
    _keys(monkeypatch, "HUNTER_API_KEY")
    calls.replies = {
        "email-count": FakeResponse(body={"data": {
            "total": 120, "personal_emails": 110, "generic_emails": 10,
            "department": {"it": 30, "sales": 0}}}),
        "domain-search": FakeResponse(body={"data": {
            "organization": "Example Inc", "pattern": "{first}.{last}",
            "emails": [
                {"value": "security@example.com", "type": "generic", "confidence": 94},
                # If Hunter ever ignored type=generic, this must still not pass.
                {"value": "jane.doe@example.com", "type": "personal",
                 "first_name": "Jane", "last_name": "Doe"},
            ]}}),
    }
    out = intel_sources.hunter_domain("example.com")
    assert out["email_pattern"] == "{first}.{last}"
    assert out["personal_addresses_known"] == 110
    assert out["departments"] == {"it": 30}
    assert [a["address"] for a in out["role_addresses"]] == ["security@example.com"]
    assert "jane" not in json.dumps(out).lower()
    search = next(c for c in calls if "domain-search" in c["url"])
    assert search["params"]["type"] == "generic"


def test_hunter_verifier_is_skipped_without_a_key_and_runs_with_one(monkeypatch, calls):
    skipped = email_lookup.lookup("a@example.com", selected_sources={"hunter"})
    assert skipped["hunter"]["status"] == "skipped"
    assert calls == []

    _keys(monkeypatch, "HUNTER_API_KEY")
    calls.replies = {"email-verifier": FakeResponse(body={"data": {
        "status": "valid", "result": "deliverable", "score": 91, "disposable": False}})}
    ran = email_lookup.lookup("a@example.com", selected_sources={"hunter"})
    assert ran["hunter"]["status"] == "ok" and ran["hunter"]["verdict"] == "valid"
    assert ran["sources_contacted"] == [{"source": "Hunter", "status": "checked"}]


LEAKED = ("hunter2", "5f4dcc3b5aa765d61d8327deb882cf99", "198.51.100.77", "Jane Doe")


def test_snusbase_asks_for_counts_only(monkeypatch, calls):
    """/data/count returns no records, so nothing leaked is ever downloaded."""
    _keys(monkeypatch, "SNUSBASE_API_KEY")
    calls.replies = {"snusbase": FakeResponse(body={"took": 1, "results": {
        "EXAMPLE_2019": 2, "OTHER_2021": 1, "EMPTY_DB": 0,
        "ODD": [{"password": LEAKED[0]}]}})}
    out = exposure_lookup.lookup("a@example.com", "Email", selected_sources=["snusbase"])
    snus = out["snusbase"]
    assert calls[0]["url"] == "https://api.snusbase.com/data/count"
    assert calls[0]["json"] == {"terms": ["a@example.com"], "types": ["email"], "wildcard": False}
    assert snus["breach_count"] == 2 and snus["total_records"] == 3
    assert snus["breach_databases"][0] == {"database": "EXAMPLE_2019", "records": 2}
    assert LEAKED[0] not in json.dumps(out)
    assert out["summary"]["exposure_detected"] is True


def test_leakcheck_returns_breaches_dates_and_field_kinds_never_values(monkeypatch, calls):
    _keys(monkeypatch, "LEAKCHECK_API_KEY")
    calls.replies = {"leakcheck": FakeResponse(body={"success": True, "found": 2, "quota": 398,
        "result": [
            {"email": "a@example.com", "password": LEAKED[0], "ip": LEAKED[2],
             "source": {"name": "Example.com", "breach_date": "2019-03"},
             "fields": ["email", "password", "ip"]},
            {"email": "a@example.com", "name": LEAKED[3],
             "source": {"name": "Combo list", "compilation": 1}, "fields": ["email", "name"]}]})}
    out = exposure_lookup.lookup("example.com", "Domain", selected_sources=["leakcheck"])
    lc = out["leakcheck"]
    assert lc["breach_count"] == 2 and lc["quota_remaining"] == 398
    names = {b["breach"]: b for b in lc["breaches"]}
    assert names["Example.com"]["date"] == "2019-03"
    assert names["Example.com"]["leaked_fields"] == ["email", "ip", "password"]
    assert names["Combo list"]["compilation"] is True
    text = json.dumps(out)
    for value in LEAKED:
        assert value not in text
    assert calls[0]["params"]["type"] == "domain"
    assert calls[0]["headers"]["X-API-Key"] == "test-leakcheck_api_key"


def test_breach_sources_skip_company_names_and_missing_keys(calls, monkeypatch):
    out = exposure_lookup.lookup("Example Holdings", "Company",
                                 selected_sources=["snusbase", "leakcheck"])
    assert out["snusbase"]["status"] == "skipped"
    assert out["leakcheck"]["status"] == "skipped"
    _keys(monkeypatch, "SNUSBASE_API_KEY", "LEAKCHECK_API_KEY")
    out = exposure_lookup.lookup("Example Holdings", "Company",
                                 selected_sources=["snusbase", "leakcheck"])
    assert "company" in out["snusbase"]["reason"]
    assert calls == []


# ── failures read clearly ────────────────────────────────────────────────────

@pytest.mark.parametrize("status,needle", [(401, "rejected the key"), (429, "quota")])
def test_every_source_explains_a_bad_key_or_a_spent_quota(monkeypatch, calls, status, needle):
    _keys(monkeypatch, *{s.env for s in intel_sources.SOURCES})
    calls.replies = {"": FakeResponse(status)}
    for source in intel_sources.SOURCES:
        target = "203.0.113.5" if "ip" in source.kinds else "example.com"
        out = intel_sources.call(source, target)
        assert needle in out.get("error", ""), source.label


def test_censys_platform_lookup(monkeypatch, calls):
    _keys(monkeypatch, "CENSYS_API_KEY")
    calls.replies = {"api.platform.censys.io": FakeResponse(body={"result": {"resource": {
        "ip": "203.0.113.5", "service_count": 2,
        "autonomous_system": {"asn": 64500, "name": "EXAMPLE-AS"},
        "location": {"country": "Netherlands", "city": "Amsterdam"},
        "services": [
            {"port": 443, "protocol": "HTTP", "transport_protocol": "tcp",
             "software": [{"vendor": "nginx", "product": "nginx", "version": "1.25"}]},
            {"port": 22, "protocol": "SSH"}]}}})}
    out = intel_sources.censys_host("203.0.113.5")
    assert out["service_count"] == 2 and out["asn"] == 64500
    assert out["services"][0]["software"] == ["nginx nginx 1.25"]
    assert calls[0]["headers"]["Authorization"] == "Bearer test-censys_api_key"
    assert "X-Organization-ID" not in calls[0]["headers"]   # free accounts have none
    monkeypatch.setenv("CENSYS_ORG_ID", "org-1")
    intel_sources.censys_host("203.0.113.5")
    assert calls[1]["headers"]["X-Organization-ID"] == "org-1"


def test_domaintools_signs_requests_when_it_has_a_username(monkeypatch, calls):
    import hashlib
    import hmac

    monkeypatch.setenv("DOMAINTOOLS_API_KEY", "alice:s3cret")
    calls.replies = {
        "/v1/risk/": FakeResponse(body={"response": {"risk_score": 82, "components": [
            {"name": "phishing", "risk_score": 82}]}}),
        "/v1/example.com/": FakeResponse(body={"response": {
            "registrant": {"name": "Example Inc"},
            "registration": {"registrar": "Registrar LLC", "created": "1995-08-14"},
            "name_servers": [{"server": "ns1.example.com"}]}}),
    }
    out = intel_sources.domaintools("example.com")
    assert out["risk"]["risk_score"] == 82
    assert out["profile"]["registrar"] == "Registrar LLC"
    for call in calls:
        params = call["params"]
        assert params["api_username"] == "alice"
        path = call["url"].replace("https://api.domaintools.com", "")
        expected = hmac.new(b"s3cret", f"alice{params['timestamp']}{path}".encode(),
                            hashlib.sha256).hexdigest()
        assert params["signature"] == expected
        assert "s3cret" not in json.dumps(call, default=str)


def test_domaintools_reports_a_product_outside_the_plan_and_keeps_the_other(monkeypatch, calls):
    monkeypatch.setenv("DOMAINTOOLS_API_KEY", "plainkey")
    calls.replies = {"/v1/risk/": FakeResponse(403),
                     "/v1/example.com/": FakeResponse(body={"response": {
                         "registration": {"registrar": "R"}}})}
    out = intel_sources.domaintools("example.com")
    assert out["profile"]["registrar"] == "R"
    assert "not in your plan" in out["risk"]["error"]
    assert calls[0]["headers"]["X-Api-Key"] == "plainkey"


def test_a_404_keeps_the_services_own_explanation(monkeypatch, calls):
    calls.replies = {"greynoise": FakeResponse(404, {
        "noise": False, "riot": False, "message": "IP not observed scanning the internet."})}
    _keys(monkeypatch, "GREYNOISE_API_KEY")
    assert intel_sources.greynoise("203.0.113.5")["message"] == "IP not observed scanning the internet."


def test_hunter_status_codes_mean_what_hunter_says(monkeypatch, calls):
    _keys(monkeypatch, "HUNTER_API_KEY")
    calls.replies = {"hunter": FakeResponse(403)}
    assert "rate limit" in intel_sources.hunter_domain("example.com")["error"]
    calls.replies = {"hunter": FakeResponse(429)}
    assert "monthly quota" in intel_sources.hunter_domain("example.com")["error"]

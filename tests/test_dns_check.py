"""Tests for the bash.ws egress DNS-leak test (``run_dns_leak_test``).

The resolver and HTTP client are injected, so nothing touches the network.
"""

from agents.vpn_agent.services import dns_check


class _Resp:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload


def _fixture_get(id_text="testid123", result=None, id_status=200, result_status=200):
    """A session_get double: '/id' returns the token, '/dnsleak/test/…' the result."""
    def get(url, timeout=None, headers=None, params=None):
        get.calls.append(url)
        if url.endswith("/id"):
            return _Resp(text=id_text, status_code=id_status)
        return _Resp(payload=result, status_code=result_status)
    get.calls = []
    return get


def test_leak_test_parses_public_ip_resolvers_and_verdict():
    payload = [
        {"type": "ip", "ip": "203.0.113.5", "country_name": "Germany", "asn": "AS3320"},
        {"type": "dns", "ip": "9.9.9.9", "country_name": "United States", "asn": "AS19281"},
        {"type": "dns", "ip": "9.9.9.10", "country_name": "United States", "asn": "AS19281"},
        {"type": "conclusion", "ip": "DNS may be leaking."},
    ]
    resolved = []
    out = dns_check.run_dns_leak_test(
        probe_count=3,
        resolve=lambda host: resolved.append(host),
        session_get=_fixture_get(result=payload),
    )
    assert out["status"] == "ok"
    assert out["public_ip"] == {"ip": "203.0.113.5", "country": "Germany", "asn": "AS3320"}
    assert out["resolver_count"] == 2
    assert out["distinct_asns"] == 1
    assert out["conclusion"] == "DNS may be leaking."
    assert out["leak"] is True
    # The probe hostnames follow the {i}.{id}.bash.ws template.
    assert resolved == ["1.testid123.bash.ws", "2.testid123.bash.ws", "3.testid123.bash.ws"]


def test_leak_test_no_leak_verdict():
    payload = [
        {"type": "ip", "ip": "203.0.113.5", "asn": "AS3320", "country_name": "DE"},
        {"type": "dns", "ip": "203.0.113.53", "asn": "AS3320", "country_name": "DE"},
        {"type": "conclusion", "ip": "DNS is not leaking."},
    ]
    out = dns_check.run_dns_leak_test(
        probe_count=1, resolve=lambda h: None, session_get=_fixture_get(result=payload))
    assert out["leak"] is False


def test_leak_test_empty_error_object_is_reported_not_crash():
    # bash.ws answers with an OBJECT {"error": …}, not an array, when it saw no
    # resolvers — the client must branch on that and not crash.
    get = _fixture_get(result={"error": "No DNS servers found. Try again..."})
    out = dns_check.run_dns_leak_test(probe_count=1, resolve=lambda h: None, session_get=get)
    assert out["status"] == "ok"
    assert out["resolver_count"] == 0
    assert out["leak"] is None
    assert "No DNS servers" in out["conclusion"]


def test_leak_test_heuristic_when_no_conclusion():
    # No conclusion entry → fall back to ASN comparison: a resolver on a different
    # network than the exit IP indicates a leak.
    payload = [
        {"type": "ip", "ip": "203.0.113.5", "asn": "AS3320"},
        {"type": "dns", "ip": "8.8.8.8", "asn": "AS15169"},
    ]
    out = dns_check.run_dns_leak_test(
        probe_count=1, resolve=lambda h: None, session_get=_fixture_get(result=payload))
    assert out["leak"] is True


def test_leak_test_bad_id_is_a_clean_error():
    get = _fixture_get(id_text="", result=[])
    out = dns_check.run_dns_leak_test(probe_count=1, resolve=lambda h: None, session_get=get)
    assert out["status"] == "error"


def test_leak_test_cancel_stops_before_reading_result():
    get = _fixture_get(result=[])
    resolved = []
    out = dns_check.run_dns_leak_test(
        probe_count=5, resolve=lambda h: resolved.append(h),
        session_get=get, should_stop=lambda: True)
    assert out["status"] == "cancelled"
    assert resolved == []               # cancelled before any probe
    assert get.calls == ["https://bash.ws/id"]   # never fetched the result

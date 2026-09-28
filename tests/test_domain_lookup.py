from providers import domain_lookup


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
    assert [item["status"] for item in result["sources_contacted"]] == [
        "checked", "error", "checked", "checked",
    ]
    assert ("WHOIS", "checking") in progress
    assert ("Certificate transparency (crt.sh)", "checked") in progress
    assert ("Wayback Machine", "checked") in progress


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


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_wayback_asks_for_the_earliest_and_the_latest_snapshot(monkeypatch):
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
    monkeypatch.setattr(
        domain_lookup.requests, "get",
        lambda *a, **k: _Response({"archived_snapshots": {}}),
    )
    result = domain_lookup._wayback("never-archived.example")
    assert result["archived"] is False
    assert "error" not in result


def test_wayback_http_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(
        domain_lookup.requests, "get", lambda *a, **k: _Response({}, status_code=503)
    )
    assert domain_lookup._wayback("example.com") == {"error": "Wayback Machine HTTP 503"}

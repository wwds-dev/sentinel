"""Tests for the dark-web exposure provider (ransomware.live / Ahmia / IntelX).

All network access is mocked — the suite never contacts a live service, mirroring
the other provider tests.
"""

from providers import exposure_lookup


class _Response:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


# ── ransomware.live ─────────────────────────────────────────────────────────

def test_ransomware_live_flags_direct_victim_vs_mention(monkeypatch):
    payload = [
        {"victim": "Example Corp", "domain": "example.com", "group": "lockbit",
         "country": "US", "activity": "Tech", "attackdate": "2026-01-01",
         "discovered": "2026-01-02", "url": "https://r.live/id/1",
         "claim_url": "", "description": "Full network dump."},
        {"victim": "Other Inc", "domain": "other.com", "group": "clop",
         "description": "Their client example.com was also mentioned."},
    ]
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(payload))

    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("ransomware_live",))

    rl = result["ransomware_live"]
    assert rl["status"] == "ok"
    assert rl["total_results"] == 2
    assert rl["direct_victim_matches"] == 1
    # The target's own listing (victim-name / domain match) ranks first.
    assert rl["victims"][0]["victim"] == "Example Corp"
    assert rl["victims"][0]["match"] in {"victim-name", "domain"}
    assert rl["victims"][1]["match"] == "description-only"
    assert result["summary"]["on_ransomware_leak_site"] is True
    assert result["summary"]["exposure_detected"] is True
    assert result["sources_contacted"] == [
        {"source": "Ransomware.live", "status": "checked"}
    ]


def test_ransomware_live_rate_limit_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(status_code=429))
    result = exposure_lookup.lookup(
        "acme.com", "Domain", selected_sources=("ransomware_live",))
    assert result["ransomware_live"]["status"] == "error"
    assert "rate limited" in result["ransomware_live"]["detail"]
    assert result["summary"]["on_ransomware_leak_site"] is False


def test_email_target_searches_the_domain_not_the_address(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        exposure_lookup.requests, "get",
        lambda url, *a, **k: seen.setdefault("url", url) or _Response([]))
    result = exposure_lookup.lookup(
        "jane@example.com", "Email", selected_sources=("ransomware_live",))
    assert seen["url"].endswith("/searchvictims/example.com")
    assert result["keyword"] == "example.com"
    assert result["target_type"] == "email"


# ── Ahmia ────────────────────────────────────────────────────────────────────

_AHMIA_HTML = """
<html><body>
<li class="result">
  <h4><a href="/search/redirect?redirect_url=http%3A%2F%2Fabcdefghij234567.onion%2Fdump">
      Leaked DB — example.com</a></h4>
  <cite>abcdefghij234567.onion</cite>
  <p>Dump containing example.com emails and password hashes.</p>
</li>
<li class="result">
  <h4><a href="/search/redirect?redirect_url=http%3A%2F%2Fzzzz2222yyyy3333.onion">Market</a></h4>
  <p>Unrelated listing.</p>
</li>
</body></html>
"""


def test_ahmia_parses_onion_title_and_snippet(monkeypatch):
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(status_code=200, text=_AHMIA_HTML))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("ahmia",))
    ah = result["ahmia"]
    assert ah["status"] == "ok"
    assert ah["found"] is True
    assert ah["result_count"] == 2
    first = ah["results"][0]
    assert first["onion"] == "http://abcdefghij234567.onion"
    assert "Leaked DB" in first["title"]
    assert "password hashes" in first["snippet"]
    assert result["summary"]["darkweb_index_hits"] == 2


def test_ahmia_redirect_to_home_is_reported_not_an_error(monkeypatch):
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(status_code=302))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("ahmia",))
    ah = result["ahmia"]
    assert ah["status"] == "ok"
    assert ah["found"] is False
    assert "redirected" in ah["note"]
    # A redirect is still a contacted source, not an error.
    assert result["sources_contacted"] == [{"source": "Ahmia", "status": "checked"}]


# ── Intelligence X ───────────────────────────────────────────────────────────

def test_intelx_skipped_without_key(monkeypatch):
    monkeypatch.setattr(exposure_lookup, "INTELX_KEY", "")
    monkeypatch.setattr(
        exposure_lookup.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call IntelX")))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("intelx",))
    assert result["intelx"]["status"] == "skipped"
    assert result["sources_skipped"] == [
        {"source": "Intelligence X", "status": "skipped"}
    ]
    assert result["sources_contacted"] == []


def test_intelx_search_reads_index_only(monkeypatch):
    monkeypatch.setattr(exposure_lookup, "INTELX_KEY", "test-key")
    monkeypatch.setattr(exposure_lookup.time, "sleep", lambda *_a, **_k: None)

    posts, gets = [], []

    def fake_post(url, *a, **k):
        posts.append(url)
        return _Response({"id": "search-1", "status": 0})

    def fake_get(url, *a, **k):
        gets.append(url)
        assert "/file/" not in url and "/read" not in url, "must not download content"
        if "search/result" in url:
            return _Response({
                "records": [{
                    "systemid": "sid1", "name": "acme_leak.txt",
                    "date": "2026-01-01", "added": "2026-01-02",
                    "bucket": "leaks", "media": 24, "type": 1, "xscore": 90,
                }],
                "status": 1,  # no more results
            })
        return _Response({})  # terminate

    monkeypatch.setattr(exposure_lookup.requests, "post", fake_post)
    monkeypatch.setattr(exposure_lookup.requests, "get", fake_get)

    result = exposure_lookup.lookup(
        "acme.com", "Domain", selected_sources=("intelx",))
    ix = result["intelx"]
    assert ix["status"] == "ok"
    assert ix["total"] == 1
    rec = ix["records"][0]
    assert rec["name"] == "acme_leak.txt"
    assert rec["media_type"] == "text file"
    assert rec["system_id"] == "sid1"
    assert posts and posts[0].endswith("/intelligent/search")
    assert any("search/result" in u for u in gets)
    assert result["summary"]["intelx_records"] == 1


# ── orchestration ────────────────────────────────────────────────────────────

def test_cancel_before_contact_touches_nothing(monkeypatch):
    monkeypatch.setattr(
        exposure_lookup.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not contact any source")))
    monkeypatch.setattr(
        exposure_lookup.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not contact any source")))
    result = exposure_lookup.lookup(
        "example.com", "Domain", should_stop=lambda: True)
    assert result["cancelled"] is True
    assert result["sources_contacted"] == []


def test_progress_and_source_selection(monkeypatch):
    monkeypatch.setattr(exposure_lookup, "INTELX_KEY", "")
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response([]))
    events = []
    result = exposure_lookup.lookup(
        "example.com", "Domain",
        selected_sources=("ransomware_live",),
        on_progress=lambda source, status: events.append((source, status)),
    )
    # Only the one selected source is contacted.
    assert result["sources_contacted"] == [
        {"source": "Ransomware.live", "status": "checked"}
    ]
    assert "ahmia" not in result and "intelx" not in result
    assert events == [
        ("Ransomware.live", "checking"),
        ("Ransomware.live", "checked"),
    ]
    assert result["type"] == "exposure"

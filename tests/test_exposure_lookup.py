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


def test_a_similar_looking_victim_is_not_the_target(monkeypatch):
    """The target's name part was tested as a substring, so a victim called
    "Pineapple Holdings" (pineapple.com) counted as a direct match for apple.com
    and raised the red "likely breach" headline."""
    payload = [
        {"victim": "Pineapple Holdings", "domain": "pineapple.com", "group": "lockbit",
         "description": "Retail group."},
        {"victim": "Snapple Beverage", "domain": "snapple.example", "group": "akira"},
        {"victim": "Applebee Rentals", "domain": "applebee.com", "group": "play"},
    ]
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(payload))
    result = exposure_lookup.lookup(
        "apple.com", "Domain", selected_sources=("ransomware_live",))
    rl = result["ransomware_live"]
    assert rl["direct_victim_matches"] == 0
    assert {v["match"] for v in rl["victims"]} == {"keyword"}
    assert result["summary"]["on_ransomware_leak_site"] is False
    assert result["summary"]["exposure_detected"] is True   # still a lead to review


def test_a_whole_label_or_the_same_domain_is_a_direct_match(monkeypatch):
    payload = [
        {"victim": "Apple Inc.", "domain": "", "group": "a"},
        {"victim": "Some Holding", "domain": "www.apple.com", "group": "b"},
        {"victim": "Shop", "domain": "store.apple.com", "group": "c"},
        {"victim": "Elsewhere", "domain": "apple.com.evil.example", "group": "d"},
    ]
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(payload))
    result = exposure_lookup.lookup(
        "apple.com", "Domain", selected_sources=("ransomware_live",))
    matches = {v["victim"]: v["match"] for v in result["ransomware_live"]["victims"]}
    assert matches == {"Apple Inc.": "victim-name", "Some Holding": "domain",
                       "Shop": "domain", "Elsewhere": "keyword"}
    assert result["ransomware_live"]["direct_victim_matches"] == 3


def test_a_company_name_matches_a_whole_domain_label_only(monkeypatch):
    payload = [
        {"victim": "Unrelated", "domain": "acme.com", "group": "a"},
        {"victim": "Unrelated too", "domain": "acmehotels.com", "group": "b"},
    ]
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        lambda *a, **k: _Response(payload))
    result = exposure_lookup.lookup(
        "Acme", "Company", selected_sources=("ransomware_live",))
    matches = {v["victim"]: v["match"] for v in result["ransomware_live"]["victims"]}
    assert matches == {"Unrelated": "domain", "Unrelated too": "keyword"}


def test_an_empty_selection_contacts_no_exposure_service(monkeypatch):
    """None means the defaults; an empty selection used to mean all six."""
    def refuse(*args, **kwargs):
        raise AssertionError("no service may be contacted")

    monkeypatch.setattr(exposure_lookup.requests, "get", refuse)
    monkeypatch.setattr(exposure_lookup.requests, "post", refuse)
    for nothing in ((), [], set()):
        result = exposure_lookup.lookup(
            "example.com", "Domain", selected_sources=nothing)
        assert result["sources_contacted"] == [] and result["sources_skipped"] == []
        assert result["summary"]["sources_queried"] == 0


def test_no_selection_argument_still_means_the_default_sources(monkeypatch):
    called = []
    for name in ("_ransomware_live", "_ahmia", "_intelx", "_dehashed",
                 "_snusbase", "_leakcheck"):
        monkeypatch.setattr(
            exposure_lookup, name,
            lambda *a, name=name, **k: called.append(name) or {"status": "ok"})
    exposure_lookup.lookup("example.com", "Domain")
    assert len(called) == 6


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


# Ahmia's home page carries the search form with a randomised-name hidden token.
_AHMIA_HOME = (
    '<html><body><form action="/search/" method="get">'
    '<input type="search" name="q">'
    '<input type="hidden" name="1c7baf" value="255594">'
    '</form></body></html>'
)


def _ahmia_get(results_html="", results_status=200, calls=None):
    """Fake requests.get dispatching Ahmia's two-step flow: home then search."""
    def fake_get(url, *a, **k):
        if calls is not None:
            calls.append((url, k.get("params")))
        if "/search" in url:
            return _Response(status_code=results_status, text=results_html)
        return _Response(status_code=200, text=_AHMIA_HOME)  # home page
    return fake_get


def test_ahmia_submits_token_then_parses_onion_title_and_snippet(monkeypatch):
    calls = []
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        _ahmia_get(_AHMIA_HTML, 200, calls))
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
    # The search request carries the token parsed from the home page.
    search_call = next(c for c in calls if "/search" in c[0])
    assert search_call[1] == {"q": "example.com", "1c7baf": "255594"}


def test_ahmia_redirect_to_home_is_reported_not_an_error(monkeypatch):
    monkeypatch.setattr(exposure_lookup.requests, "get",
                        _ahmia_get(results_html="", results_status=302))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("ahmia",))
    ah = result["ahmia"]
    assert ah["status"] == "ok"
    assert ah["found"] is False
    assert "redirected" in ah["note"]
    # A redirect is still a contacted source, not an error.
    assert result["sources_contacted"] == [{"source": "Ahmia", "status": "checked"}]


def test_ahmia_missing_token_is_a_clean_error(monkeypatch):
    # Home page with no hidden token (layout changed) — reported, never crashes.
    monkeypatch.setattr(
        exposure_lookup.requests, "get",
        lambda url, *a, **k: _Response(status_code=200, text="<html><form></form></html>"))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("ahmia",))
    assert result["ahmia"]["status"] == "error"
    assert "token" in result["ahmia"]["detail"]


# ── Intelligence X ───────────────────────────────────────────────────────────

def test_intelx_skipped_without_key(monkeypatch):
    monkeypatch.delenv("INTELX_API_KEY", raising=False)
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
    monkeypatch.setenv("INTELX_API_KEY", "test-key")
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



def _intelx_hosts(monkeypatch, refused_by=()):
    """Fake both IntelX hosts; return the hosts each call went to."""
    monkeypatch.setenv("INTELX_API_KEY", "test-key")
    monkeypatch.setattr(exposure_lookup.time, "sleep", lambda *_a, **_k: None)
    hosts = []

    def host(url):
        hosts.append(url.split("/")[2])
        return hosts[-1]

    def fake_post(url, *a, **k):
        if host(url) in refused_by:
            return _Response({}, status_code=401)
        return _Response({"id": "search-1", "status": 0})

    def fake_get(url, *a, **k):
        host(url)
        return _Response({"records": [], "status": 1})

    monkeypatch.setattr(exposure_lookup.requests, "post", fake_post)
    monkeypatch.setattr(exposure_lookup.requests, "get", fake_get)
    return hosts


def test_intelx_paid_key_stays_on_the_paid_host(monkeypatch):
    hosts = _intelx_hosts(monkeypatch)
    assert exposure_lookup._intelx("acme.com")["status"] == "ok"
    assert set(hosts) == {"2.intelx.io"}


def test_intelx_free_key_falls_back_to_the_free_host(monkeypatch):
    """A free account's key is refused by 2.intelx.io; the search, its polling
    and its release all go to free.intelx.io instead."""
    hosts = _intelx_hosts(monkeypatch, refused_by={"2.intelx.io"})
    assert exposure_lookup._intelx("acme.com")["status"] == "ok"
    assert hosts[0] == "2.intelx.io"
    assert set(hosts[1:]) == {"free.intelx.io"} and len(hosts) > 2


def test_intelx_key_refused_on_both_hosts(monkeypatch):
    hosts = _intelx_hosts(monkeypatch, refused_by={"2.intelx.io", "free.intelx.io"})
    result = exposure_lookup._intelx("acme.com")
    assert result["status"] == "error"
    assert "paid and free hosts" in result["detail"]
    assert hosts == ["2.intelx.io", "free.intelx.io"]


def test_intelx_out_of_credits_does_not_try_the_free_host(monkeypatch):
    monkeypatch.setenv("INTELX_API_KEY", "test-key")
    posts = []
    monkeypatch.setattr(exposure_lookup.requests, "post",
                        lambda url, *a, **k: posts.append(url) or _Response({}, status_code=402))
    result = exposure_lookup._intelx("acme.com")
    assert result["status"] == "error" and "credits" in result["detail"]
    assert len(posts) == 1

# ── DeHashed (key-gated, metadata only) ──────────────────────────────────────

def test_dehashed_skipped_without_key(monkeypatch):
    monkeypatch.delenv("DEHASHED_API_KEY", raising=False)
    monkeypatch.setattr(
        exposure_lookup.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call DeHashed")))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("dehashed",))
    assert result["dehashed"]["status"] == "skipped"
    assert result["sources_skipped"] == [{"source": "DeHashed", "status": "skipped"}]
    assert result["sources_contacted"] == []


def test_dehashed_reports_breach_databases_but_never_credentials(monkeypatch):
    monkeypatch.setenv("DEHASHED_API_KEY", "test-key")
    sent = {}

    def fake_post(url, json=None, headers=None, **kwargs):
        sent["url"] = url
        sent["json"] = json
        sent["headers"] = headers
        return _Response({
            "total": 3,
            "balance": 987,
            "entries": [
                {"id": "1", "email": "jane@example.com", "database_name": "BigLeak2019",
                 "password": "hunter2", "hashed_password": "5f4dcc3b5aa", "hash_type": "md5",
                 "ip_address": "203.0.113.9", "phone": "+15551234"},
                {"id": "2", "email": "joe@example.com", "database_name": "BigLeak2019",
                 "password": "letmein", "name": "Joe Example"},
                {"id": "3", "username": "acct", "database_name": ["OtherDump"],
                 "hashed_password": "deadbeef"},
            ],
        })

    monkeypatch.setattr(exposure_lookup.requests, "post", fake_post)
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("dehashed",))

    dh = result["dehashed"]
    assert dh["status"] == "ok"
    assert dh["total_records"] == 3
    assert dh["credits_remaining"] == 987
    assert dh["breach_count"] == 2
    # Databases are counted, most records first.
    assert dh["breach_databases"][0] == {"database": "BigLeak2019", "records": 2}
    assert {"database": "OtherDump", "records": 1} in dh["breach_databases"]

    # The domain query is sent unquoted; auth header carries the key.
    assert sent["json"]["query"] == "domain:example.com"
    assert sent["headers"]["Dehashed-Api-Key"] == "test-key"

    # BOUNDARY: no leaked credential or per-record PII VALUE survives anywhere.
    blob = str(result)
    for forbidden in ("hunter2", "letmein", "5f4dcc3b5aa", "deadbeef", "md5",
                      "203.0.113.9", "+15551234", "Joe Example", "jane@example.com"):
        assert forbidden not in blob
    # And the surfaced structure carries only breach names + counts — never the
    # raw entries or any credential/PII field.
    assert set(dh) == {"source", "status", "records_on_page", "total_records",
                       "breach_count", "breach_databases", "credits_remaining", "note"}
    assert all(set(item) == {"database", "records"} for item in dh["breach_databases"])
    assert "entries" not in dh
    assert result["summary"]["breach_databases"] == 2
    assert result["summary"]["exposure_detected"] is True
    assert result["sources_contacted"] == [{"source": "DeHashed", "status": "checked"}]


def test_dehashed_email_target_uses_quoted_email_query(monkeypatch):
    monkeypatch.setenv("DEHASHED_API_KEY", "test-key")
    sent = {}
    monkeypatch.setattr(
        exposure_lookup.requests, "post",
        lambda url, json=None, **k: sent.setdefault("json", json) or _Response(
            {"entries": [], "total": 0, "balance": 5}))
    exposure_lookup.lookup(
        "jane@example.com", "Email", selected_sources=("dehashed",))
    assert sent["json"]["query"] == 'email:"jane@example.com"'


def test_dehashed_company_target_is_skipped(monkeypatch):
    monkeypatch.setenv("DEHASHED_API_KEY", "test-key")
    monkeypatch.setattr(
        exposure_lookup.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no email/domain to search")))
    result = exposure_lookup.lookup(
        "Acme Corporation", "Company", selected_sources=("dehashed",))
    assert result["dehashed"]["status"] == "skipped"
    assert "not company names" in result["dehashed"]["reason"]


def test_dehashed_rejected_key_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("DEHASHED_API_KEY", "test-key")
    monkeypatch.setattr(exposure_lookup.requests, "post",
                        lambda *a, **k: _Response(status_code=401))
    result = exposure_lookup.lookup(
        "example.com", "Domain", selected_sources=("dehashed",))
    assert result["dehashed"]["status"] == "error"
    assert "rejected the key" in result["dehashed"]["detail"]


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
    monkeypatch.delenv("INTELX_API_KEY", raising=False)
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

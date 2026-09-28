import pytest

from providers import company_lookup, email_lookup, username_lookup

_REAL_GITHUB = username_lookup._github
_REAL_KEYBASE = username_lookup._keybase


@pytest.fixture(autouse=True)
def _offline_profile_sources(monkeypatch):
    """GitHub and Keybase answer "no such user" unless a test says otherwise."""
    monkeypatch.setattr(username_lookup, "_github", lambda username: {"found": False})
    monkeypatch.setattr(username_lookup, "_keybase", lambda username: {"found": False})


def test_company_lookup_reports_gleif_records_and_progress(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "meta": {"pagination": {"total": 1}},
                "data": [{
                    "id": "LEI123",
                    "attributes": {
                        "lei": "LEI123",
                        "entity": {
                            "legalName": {"name": "Example Limited"},
                            "otherNames": [{"name": "Example"}],
                            "status": "ACTIVE",
                            "jurisdiction": "IE",
                            "registeredAs": "12345",
                            "registeredAt": {"id": "RA000000"},
                            "legalAddress": {
                                "addressLines": ["Main Street"],
                                "city": "Dublin",
                                "country": "IE",
                            },
                        },
                        "registration": {"status": "ISSUED"},
                    },
                }],
            }

    requests = []
    monkeypatch.setattr(
        company_lookup.requests,
        "get",
        lambda *args, **kwargs: requests.append((args, kwargs)) or Response(),
    )
    progress = []
    result = company_lookup.lookup(
        "Example Limited",
        on_progress=lambda source, status: progress.append((source, status)),
    )

    assert requests[0][1]["params"] == {
        "filter[fulltext]": "Example Limited", "page[size]": 10
    }
    assert result["legal_entities"]["total_matches"] == 1
    assert result["legal_entities"]["records"][0]["legal_name"] == "Example Limited"
    assert result["legal_entities"]["records"][0]["legal_address"]["country"] == "IE"
    assert result["sources_contacted"] == [
        {"source": "GLEIF Legal Entity Index", "status": "checked"}
    ]
    assert progress == [
        ("GLEIF Legal Entity Index", "checking"),
        ("GLEIF Legal Entity Index", "checked"),
    ]


def test_company_lookup_can_cancel_before_contact(monkeypatch):
    monkeypatch.setattr(
        company_lookup.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not contact GLEIF")),
    )
    result = company_lookup.lookup("Example Limited", should_stop=lambda: True)
    assert result["cancelled"] is True
    assert result["sources_contacted"] == []


def test_username_lookup_reports_urlscan_progress(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "total": 1,
                "results": [{
                    "page": {"url": "https://example.test/u/alice", "domain": "example.test"},
                    "task": {"title": "Alice", "time": "now"},
                }],
            }

    monkeypatch.setattr(username_lookup.requests, "get", lambda *a, **k: Response())
    progress = []
    result = username_lookup.lookup(
        "@alice", on_progress=lambda source, status: progress.append((source, status))
    )
    assert result["query"] == "alice"
    assert result["urlscan"]["unique_domains_found"] == 1
    assert result["sources_contacted"] == [
        {"source": "URLScan", "status": "checked"},
        {"source": "GitHub", "status": "checked"},
        {"source": "Keybase", "status": "checked"},
    ]
    assert progress[:2] == [("URLScan", "checking"), ("URLScan", "checked")]
    assert result["github"] == {"found": False}


def test_email_lookup_contacts_only_selected_source(monkeypatch):
    called = []
    monkeypatch.setattr(
        email_lookup, "_emailrep",
        lambda email: called.append("emailrep") or {"score": "high"},
    )
    monkeypatch.setattr(
        email_lookup, "_hibp",
        lambda email: called.append("hibp") or {"status": "ok"},
    )
    monkeypatch.setattr(
        email_lookup, "_breachdirectory",
        lambda email: called.append("breachdirectory") or {"status": "ok"},
    )

    result = email_lookup.lookup(
        "analyst@example.com", selected_sources={"emailrep"}
    )
    assert called == ["emailrep"]
    assert result["reputation"] == {"score": "high"}
    assert result["sources_contacted"] == [{"source": "EmailRep", "status": "checked"}]
    assert "hibp" not in result
    assert "breachdirectory" not in result


def test_hibp_without_key_is_recorded_as_skipped_not_contacted(monkeypatch):
    monkeypatch.setattr(email_lookup, "HIBP_KEY", "")
    progress = []
    result = email_lookup.lookup(
        "analyst@example.com",
        selected_sources={"hibp"},
        on_progress=lambda source, status: progress.append((source, status)),
    )
    assert result["sources_contacted"] == []
    assert result["sources_skipped"] == [
        {"source": "Have I Been Pwned", "status": "skipped"}
    ]
    assert progress[-1] == ("Have I Been Pwned", "skipped")


def test_email_lookup_cancellation_preserves_completed_sources(monkeypatch):
    monkeypatch.setattr(email_lookup, "_emailrep", lambda email: {"score": "high"})
    monkeypatch.setattr(
        email_lookup, "_breachdirectory",
        lambda email: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    checks = iter((False, True))
    result = email_lookup.lookup(
        "analyst@example.com",
        selected_sources=("emailrep", "breachdirectory"),
        should_stop=lambda: next(checks),
    )
    assert result["cancelled"] is True
    assert result["reputation"] == {"score": "high"}
    assert len(result["sources_contacted"]) == 1


class _JsonResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_gravatar_sends_only_the_address_hash(monkeypatch):
    import hashlib

    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        return _JsonResponse({
            "display_name": "Analyst", "profile_url": "https://gravatar.com/analyst",
            "verified_accounts": [
                {"service_label": "GitHub", "url": "https://github.com/analyst"},
                {"service_label": "X", "url": "https://x.com/hidden", "is_hidden": True},
            ],
        })

    monkeypatch.setattr(email_lookup.requests, "get", fake_get)
    result = email_lookup._gravatar("  Analyst@Example.com ")

    digest = hashlib.sha256(b"analyst@example.com").hexdigest()
    assert urls == [f"https://api.gravatar.com/v3/profiles/{digest}"]
    assert "example.com" not in urls[0]
    assert result["found"] is True
    assert result["verified_accounts"] == [
        {"service": "GitHub", "url": "https://github.com/analyst"}
    ]


def test_gravatar_without_a_profile_is_checked_not_failed(monkeypatch):
    monkeypatch.setattr(
        email_lookup.requests, "get",
        lambda *a, **k: _JsonResponse({"error": "Profile not found"}, status_code=404),
    )
    result = email_lookup.lookup("analyst@example.com", selected_sources={"gravatar"})
    assert result["gravatar"] == {"source": "gravatar", "status": "ok", "found": False}
    assert result["sources_contacted"] == [{"source": "Gravatar", "status": "checked"}]
    assert result["summary"]["gravatar_profile"] is False


def _urlscan_ok(monkeypatch):
    monkeypatch.setattr(
        username_lookup.requests, "get",
        lambda *a, **k: _JsonResponse({"total": 0, "results": []}),
    )


def test_username_lookup_does_not_sweep_unless_asked(monkeypatch):
    _urlscan_ok(monkeypatch)
    monkeypatch.setattr(
        username_lookup._wmn, "sweep",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not sweep")),
    )
    result = username_lookup.lookup("alice")
    assert "whatsmyname" not in result
    assert [s["source"] for s in result["sources_contacted"]] == [
        "URLScan", "GitHub", "Keybase"]


def test_username_lookup_sweep_is_one_source_with_its_own_progress(monkeypatch):
    _urlscan_ok(monkeypatch)

    def fake_sweep(username, *, on_progress=None, should_stop=None):
        on_progress(1, 2)
        on_progress(2, 2)
        return {"found_count": 1, "found": [{"site": "Example", "status": "found"}]}

    monkeypatch.setattr(username_lookup._wmn, "sweep", fake_sweep)
    counts, progress = [], []
    result = username_lookup.lookup(
        "alice", whatsmyname=True,
        on_progress=lambda source, status: progress.append((source, status)),
        on_sweep_progress=lambda done, total: counts.append((done, total)),
    )
    assert result["whatsmyname"]["found_count"] == 1
    assert result["sources_contacted"][-1] == {"source": "WhatsMyName", "status": "checked"}
    assert counts == [(1, 2), (2, 2)]
    assert progress[-2:] == [("WhatsMyName", "checking"), ("WhatsMyName", "checked")]



# ── GitHub and Keybase profile lookups ───────────────────────────────────────

def test_github_profile_is_reduced_to_public_facts(monkeypatch):
    monkeypatch.setattr(username_lookup.requests, "get", lambda *a, **k: _JsonResponse({
        "html_url": "https://github.com/alice", "name": "Alice", "type": "User",
        "company": "Example", "blog": "", "location": "Cork", "email": None,
        "bio": "hi", "twitter_username": "alice_x", "public_repos": 3,
        "followers": 7, "created_at": "2015-04-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z", "node_id": "noise",
    }))
    result = _REAL_GITHUB("alice")
    assert result["found"] is True
    assert result["blog"] is None                   # empty string is "not given"
    assert result["created"] == "2015-04-01"
    assert "node_id" not in result


@pytest.mark.parametrize("status,expected", [
    (404, {"found": False}),
    (403, {"error": "GitHub rate limit reached (60 requests/hour without a token)"}),
])
def test_github_miss_and_rate_limit(monkeypatch, status, expected):
    monkeypatch.setattr(username_lookup.requests, "get",
                        lambda *a, **k: _JsonResponse({}, status_code=status))
    assert _REAL_GITHUB("alice") == expected


def test_keybase_keeps_only_proofs_that_verify(monkeypatch):
    monkeypatch.setattr(username_lookup.requests, "get", lambda *a, **k: _JsonResponse({
        "status": {"code": 0, "name": "OK"},
        "them": [{
            "basics": {"username": "alice", "ctime": 1391653108},
            "profile": {"full_name": "Alice A", "location": "Cork", "bio": None},
            "proofs_summary": {"all": [
                {"proof_type": "github", "nametag": "alice", "state": 1,
                 "service_url": "https://github.com/alice"},
                {"proof_type": "twitter", "nametag": "old", "state": 2,
                 "service_url": "https://twitter.com/old"},
            ]},
        }],
    }))
    result = _REAL_KEYBASE("alice")
    assert result["created"] == "2014-02-06"
    assert result["verified_accounts"] == [
        {"service": "github", "name": "alice", "url": "https://github.com/alice"}
    ]


def test_keybase_unknown_user_is_not_found(monkeypatch):
    monkeypatch.setattr(username_lookup.requests, "get", lambda *a, **k: _JsonResponse(
        {"status": {"code": 0, "name": "OK"}, "them": [None]}))
    assert _REAL_KEYBASE("nobody") == {"found": False}


# ── ICIJ Offshore Leaks ──────────────────────────────────────────────────────

def _gleif_empty(monkeypatch):
    monkeypatch.setattr(company_lookup.requests, "get", lambda *a, **k: _JsonResponse(
        {"meta": {"pagination": {"total": 0}}, "data": []}))


def test_offshore_leaks_only_runs_when_asked(monkeypatch):
    _gleif_empty(monkeypatch)
    monkeypatch.setattr(
        company_lookup.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not contact ICIJ")),
    )
    result = company_lookup.lookup("Example Limited")
    assert "offshore_leaks" not in result


def test_offshore_leaks_matches_are_labelled_as_name_similarity(monkeypatch):
    _gleif_empty(monkeypatch)
    sent = []

    def fake_post(url, json=None, **kwargs):
        sent.append(json)
        return _JsonResponse({"q0": {"result": [{
            "id": "10022201", "name": "EXAMPLE LIMITED", "score": 65.2, "match": False,
            "description": "Entity node extracted from the Panama Papers data.",
            "types": [{"name": "Entity"}],
        }]}}, status_code=201)

    monkeypatch.setattr(company_lookup.requests, "post", fake_post)
    result = company_lookup.lookup("Example Limited", offshore_leaks=True)
    assert sent == [{"queries": {"q0": {"query": "Example Limited"}}}]
    match = result["offshore_leaks"]["matches"][0]
    assert match == {
        "name": "EXAMPLE LIMITED", "kind": "Entity",
        "source": "Entity node extracted from the Panama Papers data.",
        "similarity": 65, "exact": False,
        "url": "https://offshoreleaks.icij.org/nodes/10022201",
    }
    assert "not evidence of wrongdoing" in result["offshore_leaks"]["note"]
    assert result["sources_contacted"][-1] == {
        "source": "ICIJ Offshore Leaks", "status": "checked"}


# ── OpenSanctions (key-gated) ────────────────────────────────────────────────

def test_sanctions_without_a_key_is_skipped_not_contacted(monkeypatch):
    _gleif_empty(monkeypatch)
    monkeypatch.setattr(company_lookup, "OPENSANCTIONS_KEY", "")
    result = company_lookup.lookup("Example Limited", sanctions=True)
    assert result["sanctions"]["status"] == "skipped"
    assert {"source": "OpenSanctions", "status": "skipped"} in result["sources_skipped"]
    assert all(s["source"] != "OpenSanctions" for s in result["sources_contacted"])


def test_sanctions_matches_carry_listing_and_datasets(monkeypatch):
    monkeypatch.setattr(company_lookup, "OPENSANCTIONS_KEY", "test-key")
    calls = []

    def fake_get(url, params=None, headers=None, **kwargs):
        calls.append((url, params, headers))
        if "opensanctions" in url:
            return _JsonResponse({"total": {"value": 1}, "results": [{
                "id": "NK-abc", "caption": "Example Limited", "schema": "Company",
                "target": True, "datasets": ["us_ofac_sdn", "eu_fsf"],
                "properties": {"country": ["ru"]},
            }]})
        return _JsonResponse({"meta": {"pagination": {"total": 0}}, "data": []})

    monkeypatch.setattr(company_lookup.requests, "get", fake_get)
    result = company_lookup.lookup("Example Limited", sanctions=True)
    url, params, headers = [c for c in calls if "opensanctions" in c[0]][0]
    assert headers["Authorization"] == "ApiKey test-key"
    assert params["q"] == "Example Limited"
    assert result["sanctions"]["matches"] == [{
        "name": "Example Limited", "kind": "Company", "listed": True,
        "datasets": ["us_ofac_sdn", "eu_fsf"], "countries": ["ru"],
        "url": "https://www.opensanctions.org/entities/NK-abc/",
    }]
    assert {"source": "OpenSanctions", "status": "checked"} in result["sources_contacted"]


def test_a_rejected_sanctions_key_is_an_error(monkeypatch):
    monkeypatch.setattr(company_lookup, "OPENSANCTIONS_KEY", "bad")
    monkeypatch.setattr(company_lookup.requests, "get",
                        lambda *a, **k: _JsonResponse({}, status_code=401))
    assert company_lookup._opensanctions("Example") == {
        "error": "OpenSanctions rejected the API key"}

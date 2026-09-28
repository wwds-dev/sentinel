"""WhatsMyName sweep: which sites are used, how an answer is read, and the cache.

No test touches the network: responses are faked per site, and the site list
is supplied or served from a temporary cache.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from providers import whatsmyname as wmn


def site(name="Example", **overrides):
    entry = {
        "name": name,
        "uri_check": "https://example.test/u/{account}",
        "e_code": 200, "e_string": "profile-card",
        "m_code": 404, "m_string": "not found",
        "known": ["alice"], "cat": "social",
    }
    entry.update(overrides)
    return entry


class Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


# ── Which sites are used ─────────────────────────────────────────────────────

def test_unreadable_sites_are_left_out():
    data = {"sites": [
        site("Plain"),
        site("Invalid", valid=False),
        site("Guarded", protection=["cloudflare"]),
        site("Adult", cat="xx NSFW xx"),
        site("No placeholder", uri_check="https://example.test/static"),
        site("Posted", uri_check="https://api.example.test",
             post_body='{"user":"{account}"}'),
    ]}
    assert [s["name"] for s in wmn.usable_sites(data)] == ["Plain", "Posted"]


# ── How an answer is read ────────────────────────────────────────────────────

def test_a_hit_needs_both_the_status_code_and_the_marker():
    http = FakeHttp(Response(200, "<div class=profile-card>"))
    outcome = wmn.check_site(site(uri_pretty="https://example.test/@{account}"),
                             "alice", http)
    assert outcome["status"] == "found"
    assert outcome["url"] == "https://example.test/@alice"
    # Redirects are what many sites use to say "no such user", so they are
    # read, not followed.
    assert http.calls[0][2]["allow_redirects"] is False

    wrong_marker = wmn.check_site(site(), "alice", FakeHttp(Response(200, "login wall")))
    assert wrong_marker["status"] == "unclear"


class RedirectingHttp:
    """First answer is a redirect; the followed request lands on the profile."""

    def __init__(self, redirect_code=301):
        self.redirect_code = redirect_code
        self.follows = []

    def get(self, url, **kwargs):
        self.follows.append(kwargs["allow_redirects"])
        if kwargs["allow_redirects"]:
            return Response(200, "<div class=profile-card>")
        return Response(self.redirect_code)


def test_a_moved_url_is_followed_when_no_signature_is_a_redirect():
    http = RedirectingHttp()
    assert wmn.check_site(site(), "alice", http)["status"] == "found"
    assert http.follows == [False, True]


def test_a_redirect_that_means_no_such_user_is_never_followed():
    http = RedirectingHttp(redirect_code=302)
    outcome = wmn.check_site(site(m_code=302, m_string=""), "bob", http)
    assert outcome["status"] == "missing"
    assert http.follows == [False]


def test_a_confirmed_miss_and_an_unexpected_answer_are_kept_apart():
    assert wmn.check_site(site(), "bob", FakeHttp(Response(404, "user not found"))
                          )["status"] == "missing"
    unclear = wmn.check_site(site(), "bob", FakeHttp(Response(429, "slow down")))
    assert unclear == {"site": "Example", "category": "social",
                       "status": "unclear", "reason": "HTTP 429"}


def test_a_network_failure_is_inconclusive_not_a_crash():
    class Broken:
        def get(self, *a, **k):
            raise ConnectionError("refused")

    assert wmn.check_site(site(), "alice", Broken())["status"] == "unclear"


def test_post_sites_send_the_account_in_the_body_and_their_headers():
    http = FakeHttp(Response(200, '{"id": 1, "profile-card": true}'))
    entry = site(uri_check="https://api.example.test/graphql",
                 post_body='{"user":"{account}"}',
                 headers={"Content-Type": "application/json"})
    assert wmn.check_site(entry, "alice", http)["status"] == "found"
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("POST", "https://api.example.test/graphql")
    assert kwargs["data"] == b'{"user":"alice"}'
    assert kwargs["headers"]["Content-Type"] == "application/json"


def test_characters_a_site_rejects_are_stripped_and_the_rest_is_escaped():
    http = FakeHttp(Response(404, "not found"))
    wmn.check_site(site(strip_bad_char="."), "john.doe", http)
    wmn.check_site(site(), "a/b", http)
    assert http.calls[0][1] == "https://example.test/u/johndoe"
    assert http.calls[1][1] == "https://example.test/u/a%2Fb"


# ── The sweep ────────────────────────────────────────────────────────────────

def test_sweep_counts_every_outcome_and_reports_progress(monkeypatch):
    answers = {"Hit": "found", "Miss": "missing", "Odd": "unclear"}
    monkeypatch.setattr(
        wmn, "check_site",
        lambda entry, username, session=None: {
            "site": entry["name"], "category": entry["cat"],
            "status": answers[entry["name"]], "url": "https://example.test/alice",
        },
    )
    progress = []
    result = wmn.sweep("@alice", sites=[site("Hit"), site("Miss"), site("Odd")],
                       on_progress=lambda done, total: progress.append((done, total)))
    assert result["sites_checked"] == 3
    assert (result["found_count"], result["not_found_count"],
            result["inconclusive_count"]) == (1, 1, 1)
    assert [hit["site"] for hit in result["found"]] == ["Hit"]
    assert progress[-1] == (3, 3)
    assert "CC BY-SA" in result["attribution"]
    assert "cancelled" not in result


def test_widespread_connection_failures_are_called_out(monkeypatch):
    outcomes = iter([{"status": "unclear", "reason": "ConnectionError"}] * 3
                    + [{"status": "unclear", "reason": "HTTP 429"}])
    monkeypatch.setattr(wmn, "check_site", lambda *a, **k: next(outcomes))
    result = wmn.sweep("alice", sites=[site()] * 4)
    assert result["inconclusive_reasons"] == {"ConnectionError": 3, "HTTP 429": 1}
    assert result["network_warning"].startswith("3 of 4 sites could not be reached")


def test_http_answers_alone_do_not_raise_the_network_warning(monkeypatch):
    monkeypatch.setattr(wmn, "check_site",
                        lambda *a, **k: {"status": "unclear", "reason": "HTTP 403"})
    assert "network_warning" not in wmn.sweep("alice", sites=[site()] * 4)


def test_a_cancelled_sweep_is_marked_partial(monkeypatch):
    monkeypatch.setattr(wmn, "check_site", lambda *a, **k: {"status": "missing"})
    result = wmn.sweep("alice", sites=[site()] * 5, should_stop=lambda: True)
    assert result["cancelled"] is True
    assert result["sites_checked"] == 0


def test_no_site_list_is_an_error_not_an_exception(monkeypatch):
    def unavailable():
        raise RuntimeError("WhatsMyName site list unavailable: offline")

    monkeypatch.setattr(wmn, "load_sites", unavailable)
    result = wmn.sweep("alice")
    assert result["error"] == "WhatsMyName site list unavailable: offline"


# ── The cached site list ─────────────────────────────────────────────────────

@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "wmn-data.json"
    monkeypatch.setattr(wmn, "_cache_path", lambda: path)
    return path


def test_a_fresh_cache_is_used_without_downloading(cache_file, monkeypatch):
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({"sites": [site()]}))
    monkeypatch.setattr(
        wmn.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")),
    )
    sites, origin = wmn.load_sites()
    assert origin == "cache" and len(sites) == 1


def test_a_stale_cache_is_refreshed_and_rewritten(cache_file, monkeypatch):
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({"sites": [site("Old")]}))
    week_ago = time.time() - wmn.CACHE_MAX_AGE - 60
    os.utime(cache_file, (week_ago, week_ago))

    class Downloaded:
        def raise_for_status(self):
            pass

        def json(self):
            return {"sites": [site("New")]}

    monkeypatch.setattr(wmn.requests, "get", lambda *a, **k: Downloaded())
    sites, origin = wmn.load_sites()
    assert origin == "download"
    assert [s["name"] for s in sites] == ["New"]
    assert "New" in cache_file.read_text()


def test_a_failed_download_falls_back_to_a_stale_cache(cache_file, monkeypatch):
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(json.dumps({"sites": [site("Old")]}))
    week_ago = time.time() - wmn.CACHE_MAX_AGE - 60
    os.utime(cache_file, (week_ago, week_ago))

    def offline(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(wmn.requests, "get", offline)
    sites, origin = wmn.load_sites()
    assert origin == "stale cache" and sites[0]["name"] == "Old"


def test_no_cache_and_no_download_raises(cache_file, monkeypatch):
    def offline(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(wmn.requests, "get", offline)
    with pytest.raises(RuntimeError, match="unavailable"):
        wmn.load_sites()

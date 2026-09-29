"""Tests for the addy.io alias-mint helper.

Every network call is stubbed — no real alias is ever created, and the API key
is only ever asserted to travel in the Authorization header.
"""

from providers import alias_mint


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_mint_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ADDYIO_API_KEY", raising=False)
    monkeypatch.setattr(
        alias_mint.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call addy.io")))
    monkeypatch.setattr(
        alias_mint.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call addy.io")))
    result = alias_mint.mint_alias("desc")
    assert result["status"] == "skipped"
    assert "ADDYIO_API_KEY" in result["reason"]


def test_mint_creates_alias_and_sends_bearer_key(monkeypatch):
    monkeypatch.setenv("ADDYIO_API_KEY", "addy-key")
    seen = {}

    def fake_get(url, timeout=None, headers=None, **kwargs):
        seen["get_url"] = url
        return _Resp({"data": {"default_alias_domain": "anonaddy.me",
                               "default_alias_format": "random_characters"}})

    def fake_post(url, json=None, timeout=None, headers=None, **kwargs):
        seen["post_json"] = json
        seen["post_headers"] = headers
        return _Resp({"data": {
            "id": "uuid-1", "email": "abc123@anonaddy.me",
            "local_part": "abc123", "domain": "anonaddy.me", "active": True,
        }}, status_code=201)

    monkeypatch.setattr(alias_mint.requests, "get", fake_get)
    monkeypatch.setattr(alias_mint.requests, "post", fake_post)

    result = alias_mint.mint_alias("Sentinel alias")
    assert result["status"] == "ok"
    assert result["email"] == "abc123@anonaddy.me"
    assert result["local_part"] == "abc123"
    assert result["active"] is True
    # Auth and content negotiation headers, and the key only in the header.
    assert seen["post_headers"]["Authorization"] == "Bearer addy-key"
    assert seen["post_headers"]["Accept"] == "application/json"
    assert seen["get_url"].endswith("/account-details")
    # The key never appears in the request body.
    assert "addy-key" not in str(seen["post_json"])
    # Body uses the account defaults discovered from account-details.
    assert seen["post_json"]["domain"] == "anonaddy.me"
    assert seen["post_json"]["format"] == "random_characters"
    assert seen["post_json"]["description"] == "Sentinel alias"


def test_mint_uses_explicit_domain_and_skips_account_lookup(monkeypatch):
    monkeypatch.setenv("ADDYIO_API_KEY", "addy-key")
    monkeypatch.setattr(
        alias_mint.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no account lookup needed")))
    posted = {}
    monkeypatch.setattr(
        alias_mint.requests, "post",
        lambda url, json=None, **k: posted.update(json=json) or _Resp(
            {"data": {"email": "z@addy.io"}}, status_code=201))
    result = alias_mint.mint_alias(domain="addy.io", fmt="uuid")
    assert result["email"] == "z@addy.io"
    assert posted["json"] == {"domain": "addy.io", "format": "uuid"}


def test_mint_401_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("ADDYIO_API_KEY", "bad")
    monkeypatch.setattr(alias_mint.requests, "post",
                        lambda *a, **k: _Resp({"message": "Unauthenticated."}, status_code=401))
    result = alias_mint.mint_alias(domain="addy.io", fmt="uuid")
    assert result["status"] == "error"
    assert "unauthenticated" in result["detail"].lower()


def test_mint_hourly_limit_429_is_reported(monkeypatch):
    monkeypatch.setenv("ADDYIO_API_KEY", "addy-key")
    monkeypatch.setattr(alias_mint.requests, "post",
                        lambda *a, **k: _Resp({}, status_code=429))
    result = alias_mint.mint_alias(domain="addy.io", fmt="uuid")
    assert result["status"] == "error"
    assert "limit" in result["detail"].lower()


def test_account_details_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ADDYIO_API_KEY", raising=False)
    result = alias_mint.account_details()
    assert result["status"] == "skipped"

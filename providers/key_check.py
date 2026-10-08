"""Is this API key any good? One cheap call per service, for the OSINT Keys tab.

Each check asks the service about the key itself, never about a target. Where a
service has an account, status or quota endpoint, that is what is called, so
the check costs nothing. Where it has none, the check makes the cheapest real
call and says so in ``CHECKS[...].cost`` — "Check all" skips those, and a per-row check names the
cost in its tooltip before you click.

Before any call, a key whose shape cannot be right for the service (wrong
length or characters) is reported as malformed without contacting anything.

A check returns a dict:
  state   — ok | rejected | limited | malformed | unreachable | error | missing
  summary — a few words for the row's button
  detail  — one or two sentences: the plan, what is left, or what went wrong
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import requests

_UA = "Sentinel-OSINT/2.0"
_TIMEOUT = 15

OK, REJECTED, LIMITED, MALFORMED, UNREACHABLE, ERROR, MISSING = (
    "ok", "rejected", "limited", "malformed", "unreachable", "error", "missing")

SUMMARY = {
    OK: "✓ Works",
    REJECTED: "✗ Rejected",
    LIMITED: "! Limited",
    MALFORMED: "✗ Malformed",
    UNREACHABLE: "? Offline",
    ERROR: "? Error",
    MISSING: "No key",
}


@dataclass(frozen=True)
class Check:
    func: Callable[[str], dict]
    cost: str = ""            # empty: free. Otherwise what one check spends.
    shape: str = ""           # documented format: a mismatch is malformed, no call
    shape_text: str = ""
    hint: str = ""            # likely format: mentioned only if the key is refused
    hint_text: str = ""


def _result(state: str, detail: str) -> dict:
    return {"state": state, "summary": SUMMARY[state], "detail": detail}


def fingerprint(key: str) -> str:
    """A short, one-way tag for a key, so a stored result can be matched to the
    key it was made for without storing the key."""
    return hashlib.sha256(key.strip().encode()).hexdigest()[:12]


class _Answer:
    def __init__(self, status: int, body):
        self.status = status
        self.body = body if isinstance(body, dict) else {}


def _call(method: str, url: str, *, secret: str, **kwargs) -> _Answer | dict:
    """One request. Returns an _Answer, or a finished result for a network failure."""
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    headers.update(kwargs.pop("headers", {}) or {})
    try:
        resp = requests.request(method, url, headers=headers, timeout=_TIMEOUT, **kwargs)
    except requests.exceptions.Timeout:
        return _result(UNREACHABLE, "The service did not answer in time.")
    except requests.exceptions.RequestException as exc:
        text = str(exc).replace(secret, "***") if secret else str(exc)
        return _result(UNREACHABLE, f"Could not reach the service: {text[:160]}")
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return _Answer(resp.status_code, body)


def _verdict(answer, name: str, *, ok: Callable[[dict], str],
             rejected=(401, 403), limited=(429,), extra=None) -> dict:
    """Turn an answer into a result. ``ok`` builds the success detail from the body."""
    if isinstance(answer, dict):
        return answer
    code = answer.status
    if extra and code in extra:
        state, detail = extra[code]
        return _result(state, detail)
    if code == 200:
        try:
            return _result(OK, ok(answer.body))
        except Exception:
            return _result(OK, f"{name} accepted the key.")
    if code in rejected:
        return _result(REJECTED, f"{name} refused this key (HTTP {code}). Copy it again "
                                 "from your account page.")
    if code in limited:
        return _result(LIMITED, f"{name} accepted the key, but its rate limit or quota "
                                "is used up for now.")
    return _result(ERROR, f"{name} answered HTTP {code}; the key could not be confirmed.")


def _plan(*parts) -> str:
    return " ".join(p for p in parts if p)


# ── the checks ───────────────────────────────────────────────────────────────

def _virustotal(key):
    # overall_quotas takes the key as the user id and costs no quota.
    answer = _call("GET", f"https://www.virustotal.com/api/v3/users/{key}/overall_quotas",
                   secret=key, headers={"x-apikey": key})

    def quota(body):
        daily = (((body.get("data") or {}).get("api_requests_daily") or {}).get("user")) or {}
        left = None
        if daily.get("allowed") is not None and daily.get("used") is not None:
            left = daily["allowed"] - daily["used"]
        return _plan("VirusTotal accepted the key.",
                     f"{left} of {daily['allowed']} lookups left today." if left is not None else "")
    # The key is the user id here, so an unknown key is "user not found": 404.
    return _verdict(answer, "VirusTotal", ok=quota,
                    extra={404: (REJECTED, "VirusTotal does not know this key. Copy it "
                                           "again from your profile's API key page.")})

def _abuseipdb(key):
    # AbuseIPDB checks the key before the parameters, so a call without an
    # address answers 422 for a good key and 401 for a bad one, with no lookup.
    answer = _call("GET", "https://api.abuseipdb.com/api/v2/check", secret=key,
                   headers={"Key": key})
    return _verdict(answer, "AbuseIPDB", ok=lambda b: "AbuseIPDB accepted the key.",
                    extra={422: (OK, "AbuseIPDB accepted the key.")})

def _greynoise(key):
    answer = _call("GET", "https://api.greynoise.io/ping", secret=key, headers={"key": key})
    return _verdict(answer, "GreyNoise", ok=lambda b: _plan(
        "GreyNoise accepted the key.",
        f"Plan: {b.get('plan') or b.get('offering')}." if (b.get("plan") or b.get("offering")) else "",
        f"Expires {b.get('expiration')}." if b.get("expiration") else ""))

def _otx(key):
    # Only users/me proves the key: OTX's public endpoints answer a bad key too.
    answer = _call("GET", "https://otx.alienvault.com/api/v1/users/me", secret=key,
                   headers={"X-OTX-API-KEY": key})
    return _verdict(answer, "AlienVault OTX", ok=lambda b: _plan(
        "OTX accepted the key.",
        f"Signed in as {b.get('username')}." if b.get("username") else ""))

def _shodan(key):
    answer = _call("GET", "https://api.shodan.io/api-info", secret=key, params={"key": key})
    if isinstance(answer, _Answer) and answer.status == 200 and answer.body.get("error"):
        return _result(REJECTED, f"Shodan refused this key: {str(answer.body['error'])[:120]}")
    return _verdict(answer, "Shodan", ok=lambda b: _plan(
        f"Shodan accepted the key. Plan: {b.get('plan') or 'unknown'}.",
        f"{b.get('query_credits')} query credits left." if b.get("query_credits") is not None else "",
        "With no query credits, domain lookups through Shodan will fail."
        if b.get("query_credits") == 0 else ""))

def _censys(key):
    from providers.intel_sources import key as env

    org = env("CENSYS_ORG_ID")
    headers = {"Authorization": f"Bearer {key}"}
    url = (f"https://api.platform.censys.io/v3/accounts/organizations/{org}/credits" if org
           else "https://api.platform.censys.io/v3/accounts/users/credits")
    if org:
        headers["X-Organization-ID"] = org
    answer = _call("GET", url, secret=key, headers=headers)

    def credits(body):
        result = body.get("result") or {}
        return _plan("Censys accepted the token.",
                     f"{result.get('balance')} credits left." if result.get("balance") is not None else "",
                     f"Resets {str(result.get('resets_at'))[:10]}." if result.get("resets_at") else "")
    # A paid organisation's token, asked about a free user's credits, gets 404:
    # the token is fine, the organisation ID is what is missing.
    return _verdict(answer, "Censys", ok=credits,
                    extra={404: (LIMITED, "Censys accepted the token, but it belongs to a "
                                          "paid organisation: set CENSYS_ORG_ID in .env.")}
                    if not org else None)

def _securitytrails(key):
    answer = _call("GET", "https://api.securitytrails.com/v1/ping", secret=key,
                   headers={"APIKEY": key})
    result = _verdict(answer, "SecurityTrails", ok=lambda b: "SecurityTrails accepted the key.")
    if result["state"] == OK:
        usage = _call("GET", "https://api.securitytrails.com/v1/account/usage", secret=key,
                      headers={"APIKEY": key})
        if isinstance(usage, _Answer) and usage.status == 200:
            used = usage.body.get("current_monthly_usage")
            allowed = usage.body.get("allowed_monthly_usage")
            if used is not None and allowed is not None:
                result["detail"] += f" {allowed - used} of {allowed} queries left this month."
    return result

def _domaintools(key):
    from providers.intel_sources import key as env

    raw, username = key.strip(), env("DOMAINTOOLS_API_USERNAME")
    if not username and ":" in raw:
        username, raw = (part.strip() for part in raw.split(":", 1))
    path = "/v1/account/"
    params, headers = {}, {}
    if username:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"api_username": username, "timestamp": stamp,
                  "signature": hmac.new(raw.encode(), f"{username}{stamp}{path}".encode(),
                                        hashlib.sha256).hexdigest()}
    else:
        headers = {"X-Api-Key": raw}
    answer = _call("GET", f"https://api.domaintools.com{path}", secret=raw,
                   params={**params, "format": "json"}, headers=headers)

    def products(body):
        items = ((body.get("response") or {}).get("products")) or []
        names = [p.get("id") for p in items if isinstance(p, dict) and p.get("id")]
        return _plan("DomainTools accepted the key.",
                     f"Products: {', '.join(names[:8])}." if names else "")
    return _verdict(answer, "DomainTools", ok=products)


def _urlscan(key):
    answer = _call("GET", "https://urlscan.io/api/v1/quotas", secret=key,
                   headers={"API-Key": key})
    if isinstance(answer, _Answer) and answer.status == 200 \
            and answer.body.get("scope") == "ip-address":
        # urlscan answers with anonymous quotas when it did not apply the key.
        return _result(REJECTED, "URLScan did not recognise the key and answered as if "
                                 "none was sent. Copy it again from your profile.")

    def quota(body):
        day = ((body.get("limits") or {}).get("search") or {}).get("day") or {}
        return _plan("URLScan accepted the key.",
                     f"{day['remaining']} searches left today." if day.get("remaining") is not None else "")
    return _verdict(answer, "URLScan", ok=quota,
                    extra={400: (MALFORMED, "URLScan says the key is not in its format; "
                                            "keys look like 0a1b2c3d-…, 36 characters.")})

def _hunter(key):
    answer = _call("GET", "https://api.hunter.io/v2/account", secret=key,
                   headers={"X-API-KEY": key})

    def plan(body):
        data = body.get("data") or {}
        # "remaining" is what is left; "available" does not go down with use.
        searches = ((data.get("requests") or {}).get("searches")) or {}
        left = searches.get("remaining")
        return _plan(f"Hunter accepted the key. Plan: {data.get('plan_name') or 'unknown'}.",
                     f"{left} searches left" + (f" until {data['reset_date']}." if data.get("reset_date") else ".")
                     if left is not None else "")
    return _verdict(answer, "Hunter", ok=plan,
                    extra={403: (LIMITED, "Hunter accepted the key but is throttling; "
                                          "try again in a moment.")},
                    rejected=(401,))


def _snusbase(key):
    answer = _call("POST", "https://api.snusbase.com/data/count", secret=key,
                   json={"terms": ["example@example.com"], "types": ["email"],
                         "include_results": False},
                   headers={"Auth": key, "Content-Type": "application/json"})
    return _verdict(answer, "Snusbase", ok=lambda b: "Snusbase accepted the key.")


def _leakcheck(key):
    # LeakCheck checks the key before the query, and refuses a query shorter
    # than three characters without spending quota: a good key gets "too short",
    # a bad one "Invalid X-API-Key".
    answer = _call("GET", "https://leakcheck.io/api/v2/query/ab", secret=key,
                   headers={"X-API-Key": key})
    if isinstance(answer, dict):
        return answer
    message = str(answer.body.get("error") or "").lower()
    if answer.status in (400, 401) and "short" in message:
        return _result(OK, "LeakCheck accepted the key.")
    if answer.status in (400, 401) and ("key" in message or answer.status == 401):
        return _result(REJECTED, "LeakCheck refused this key. Copy it again from your "
                                 "account settings.")
    if answer.status == 403:
        return _result(LIMITED, "LeakCheck knows the key, but it has no active plan or the "
                                "plan's quota is used up.")
    return _verdict(answer, "LeakCheck", ok=lambda b: "LeakCheck accepted the key.")

def _ipinfo(key):
    answer = _call("GET", "https://ipinfo.io/me", secret=key,
                   headers={"Authorization": f"Bearer {key}"})

    def allowance(body):
        req = body.get("requests") or {}
        return _plan("IPinfo accepted the token.",
                     f"{req['remaining']} of {req.get('limit')} requests left this month."
                     if req.get("remaining") is not None else "")
    return _verdict(answer, "IPinfo", ok=allowance)

def _criminalip(key):
    # The verdict is in the body's own "status", not the HTTP code. The answer
    # also echoes the account's email and name; only the search allowance is read.
    answer = _call("POST", "https://api.criminalip.io/v1/user/me", secret=key,
                   headers={"x-api-key": key})
    if isinstance(answer, dict):
        return answer
    status = answer.body.get("status", answer.status)
    if status == 200:
        data = answer.body.get("data") or {}
        left = data.get("max_search")
        return _result(OK, _plan("Criminal IP accepted the key.",
                                 f"Search allowance: {left}." if left is not None else ""))
    if status == 401:
        return _result(REJECTED, "Criminal IP refused this key. Copy it again from "
                                 "My Information → API Key.")
    if status == 403:
        return _result(LIMITED, "Criminal IP did not confirm the key: its account check "
                                "needs the Starter plan. Lookups may still work.")
    return _verdict(answer, "Criminal IP", ok=lambda b: "Criminal IP accepted the key.")

def _hibp(key):
    answer = _call("GET", "https://haveibeenpwned.com/api/v3/subscription/status",
                   secret=key, headers={"hibp-api-key": key})
    return _verdict(answer, "Have I Been Pwned", ok=lambda b: _plan(
        f"HIBP accepted the key. Plan: {b.get('SubscriptionName') or 'unknown'}.",
        f"Renews or expires {str(b.get('SubscribedUntil'))[:10]}."
        if b.get("SubscribedUntil") else ""))


def _dehashed(key):
    answer = _call("POST", "https://api.dehashed.com/v2/search", secret=key,
                   json={"query": "domain:example.com", "page": 1, "size": 1},
                   headers={"Dehashed-Api-Key": key, "Content-Type": "application/json"})
    return _verdict(answer, "DeHashed", ok=lambda b: _plan(
        "DeHashed accepted the key.",
        f"{b.get('balance')} credits left." if b.get("balance") is not None else ""))


def _intelx(key):
    # Intelligence X ties a key to a host: 2.intelx.io for paid accounts,
    # free.intelx.io for free ones. Sentinel's searches use the paid host.
    for host, tier in (("2.intelx.io", "paid"), ("free.intelx.io", "free")):
        answer = _call("GET", f"https://{host}/authenticate/info", secret=key,
                       headers={"x-key": key})
        if isinstance(answer, dict):
            return answer
        if answer.status == 200 and tier == "paid":
            return _result(OK, "Intelligence X accepted the key.")
        if answer.status == 200:
            return _result(LIMITED, "Intelligence X accepted this as a free-tier key. "
                                    "Sentinel's searches use the paid API host, so they "
                                    "will not work with it.")
        if answer.status == 402:
            return _result(LIMITED, "Intelligence X knows the key, but its credits are "
                                    "used up or the plan has no API access.")
        if answer.status not in (401, 403):
            return _verdict(answer, "Intelligence X", ok=lambda b: "")
    return _result(REJECTED, "Intelligence X refused this key on both its paid and free "
                             "hosts. Copy it again from your account's developer tab.")

def _courtlistener(key):
    # api-usage has its own throttle and never counts against the limits.
    answer = _call("GET", "https://www.courtlistener.com/api/rest/v4/api-usage/", secret=key,
                   headers={"Authorization": f"Token {key}"})

    def usage(body):
        rows = [r for r in body.get("current_usage") or [] if isinstance(r, dict)]
        day = next((r for r in rows if r.get("remaining") is not None), {})
        return _plan("CourtListener accepted the token.",
                     f"{day['remaining']} requests left ({day.get('rate')})."
                     if day.get("remaining") is not None else "")
    return _verdict(answer, "CourtListener", ok=usage)

def _opensanctions(key):
    # /entities is never billed, and a good key asking for an entity that
    # does not exist gets 404; a bad key gets 401 before the lookup.
    answer = _call("GET", "https://api.opensanctions.org/entities/NK-sentinel-key-check",
                   secret=key, headers={"Authorization": f"ApiKey {key}"})
    return _verdict(answer, "OpenSanctions", ok=lambda b: "OpenSanctions accepted the key.",
                    extra={404: (OK, "OpenSanctions accepted the key.")})

def _addyio(key):
    answer = _call("GET", "https://app.addy.io/api/v1/account-details", secret=key,
                   headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                            "X-Requested-With": "XMLHttpRequest"})
    return _verdict(answer, "addy.io", ok=lambda b: _plan(
        "addy.io accepted the key.",
        f"Plan: {(b.get('data') or {}).get('subscription') or 'free'}."))


UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

CHECKS: dict[str, Check] = {
    # shape: documented by the service itself, so a mismatch is reported as
    # malformed without any call. hint: from key scanners such as TruffleHog,
    # so a mismatch is only mentioned when the service also refuses the key.
    "urlscan": Check(_urlscan, hint=UUID, hint_text="a 36-character ID like 0a1b2c3d-…"),
    "virustotal": Check(_virustotal, hint=r"[0-9a-f]{64}",
                        hint_text="64 hexadecimal characters"),
    "otx": Check(_otx, hint=r"[0-9a-f]{64}", hint_text="64 hexadecimal characters"),
    "ipinfo": Check(_ipinfo, hint=r"[0-9a-f]{14}", hint_text="14 hexadecimal characters"),
    "abuseipdb": Check(_abuseipdb, cost="possibly one of today's 1,000 AbuseIPDB checks",
                       hint=r"[0-9a-f]{80}", hint_text="80 hexadecimal characters"),
    "greynoise": Check(_greynoise),
    "censys": Check(_censys),
    "criminalip": Check(_criminalip),
    "securitytrails": Check(_securitytrails, hint=r"[A-Za-z0-9]{32}",
                            hint_text="32 letters and digits"),
    "hunter": Check(_hunter, hint=r"[a-z0-9_-]{40}", hint_text="40 characters"),
    "addyio": Check(_addyio),
    "hibp": Check(_hibp, shape=r"[0-9a-f]{32}", shape_text="32 hexadecimal characters"),
    "shodan": Check(_shodan, hint=r"[A-Za-z0-9]{32}", hint_text="32 letters and digits"),
    "dehashed": Check(_dehashed, cost="one DeHashed credit"),
    "snusbase": Check(_snusbase, cost="one of Snusbase's 2,048 searches per 12 hours",
                      hint=r"sb[A-Za-z0-9]{28}",
                      hint_text='"sb" and 28 more characters (keys made since September 2021)'),
    "leakcheck": Check(_leakcheck, hint=r"[A-Za-z0-9]{40}", hint_text="40 characters"),
    "intelx": Check(_intelx, shape=UUID, shape_text="an ID like 00000000-0000-0000-0000-000000000000"),
    "domaintools": Check(_domaintools),
    "courtlistener": Check(_courtlistener, hint=r"[0-9a-f]{40}",
                           hint_text="40 hexadecimal characters"),
    "opensanctions": Check(_opensanctions),
}


def check(tool_id: str, key: str) -> dict:
    """Check one key. Never raises; the result says what happened."""
    key = (key or "").strip()
    if not key:
        return _result(MISSING, "No key saved.")
    spec = CHECKS.get(tool_id)
    if spec is None:
        return _result(ERROR, "Sentinel has no check for this service.")
    if spec.shape and not re.fullmatch(spec.shape, key, flags=re.I):
        return _result(MALFORMED, f"This cannot be a working key: it should be "
                                  f"{spec.shape_text}, and it is {len(key)} characters. "
                                  "Copy it again from your account page.")
    try:
        result = spec.func(key)
    except Exception as exc:
        result = _result(ERROR, f"The check failed unexpectedly: {type(exc).__name__}.")
    if (result.get("state") == REJECTED and spec.hint
            and not re.fullmatch(spec.hint, key, flags=re.I)):
        result["detail"] += (f" It also does not look like one: these keys are usually "
                             f"{spec.hint_text}, and this one is {len(key)} characters.")
    if key in result.get("detail", ""):
        result["detail"] = result["detail"].replace(key, "***")
    return result

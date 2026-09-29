"""addy.io email-alias minting — a user-triggered helper for operational sign-ups.

This is the one deliberately *write*-capable OSINT helper: it creates a fresh
burner alias on the operator's own addy.io account so a research registration
never uses a real address. It runs only when the operator clicks "Mint alias" in
the OSINT Keys tab — never automatically, and never in response to anything read
from a web page or tool result.

ADDYIO_API_KEY is read from the environment at call time (the OSINT Keys tab
writes it into the environment when saved), is sent only in the Authorization
header to app.addy.io, and is never logged or echoed back. The account's real
recipient/forwarding addresses are not surfaced. Without a key every function
self-skips.
"""

import os

import requests
from dotenv import load_dotenv

from services.runtime_paths import user_data_base

load_dotenv(user_data_base() / ".env", override=False)

ADDYIO_BASE = "https://app.addy.io/api/v1"
_UA = "Sentinel-OSINT/2.0"


def _key() -> str:
    return os.getenv("ADDYIO_API_KEY", "").strip()


def _headers(key: str) -> dict:
    # "Accept: application/json" is load-bearing — without it addy.io answers a
    # failed auth with an HTML redirect instead of a JSON 401.
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _UA,
    }


def account_details() -> dict:
    """Read the account defaults and alias counts (no write)."""
    key = _key()
    if not key:
        return {"status": "skipped",
                "reason": "ADDYIO_API_KEY not set — add it in the OSINT Keys tab."}
    try:
        resp = requests.get(f"{ADDYIO_BASE}/account-details",
                            timeout=15, headers=_headers(key))
        if resp.status_code == 401:
            return {"status": "error", "detail": "addy.io rejected the key (unauthenticated)."}
        if resp.status_code != 200:
            return {"status": "error", "detail": f"addy.io HTTP {resp.status_code}"}
        data = (resp.json() or {}).get("data") or {}
        return {
            "status": "ok",
            "username": data.get("username"),
            "default_domain": data.get("default_alias_domain"),
            "default_format": data.get("default_alias_format"),
            "total_active_aliases": data.get("total_active_aliases"),
            "total_aliases": data.get("total_aliases"),
        }
    except requests.exceptions.Timeout:
        return {"status": "error", "detail": "addy.io did not respond within 15 seconds."}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:200]}


def mint_alias(description: str = "", *, domain: str | None = None,
               fmt: str | None = None) -> dict:
    """Create a fresh random alias and return its address.

    A user-triggered WRITE. Self-skips without ADDYIO_API_KEY. The alias domain
    and format default to the account's own defaults (read from account-details)
    when not given.
    """
    key = _key()
    if not key:
        return {"status": "skipped",
                "reason": "ADDYIO_API_KEY not set — add it in the OSINT Keys tab first."}

    if not domain or not fmt:
        details = account_details()
        if details.get("status") == "error":
            return details
        domain = domain or details.get("default_domain") or "anonaddy.me"
        fmt = fmt or details.get("default_format") or "random_characters"

    # "custom" needs a local_part we do not have; fall back to a random alias.
    if fmt == "custom":
        fmt = "random_characters"

    body = {"domain": domain, "format": fmt}
    if description:
        body["description"] = description

    try:
        resp = requests.post(f"{ADDYIO_BASE}/aliases", json=body,
                             timeout=20, headers=_headers(key))
        if resp.status_code == 401:
            return {"status": "error", "detail": "addy.io rejected the key (unauthenticated)."}
        if resp.status_code == 429:
            return {"status": "error",
                    "detail": "addy.io hourly alias-creation limit reached; try again later."}
        if resp.status_code == 422:
            message = ""
            try:
                message = (resp.json() or {}).get("message", "")
            except Exception:
                pass
            return {"status": "error",
                    "detail": f"addy.io rejected the request: {message or 'validation error'}"}
        if resp.status_code not in (200, 201):
            return {"status": "error", "detail": f"addy.io HTTP {resp.status_code}"}
        data = (resp.json() or {}).get("data") or {}
        return {
            "status": "ok",
            "email": data.get("email"),
            "id": data.get("id"),
            "local_part": data.get("local_part"),
            "domain": data.get("domain"),
            "active": data.get("active"),
        }
    except requests.exceptions.Timeout:
        return {"status": "error", "detail": "addy.io did not respond within 20 seconds."}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:200]}

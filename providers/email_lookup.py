"""
Email OSINT provider — multi-source live lookups.

Zero-cost stack:
  • emailrep.io        → reputation, breach flag, profiles, SPF/DMARC
                         (no key; limit ≈ 10 req/day without key)
  • breachdirectory    → open breach search, no key required
  • gravatar           → public profile linked to the address; only the
                         SHA-256 hash of the address is sent, never the address

Key-gated (set in .env):
  • haveibeenpwned     → HIBP_API_KEY  — breach names, data classes, pastes ($3.50/mo)

Returns a normalised dict suitable for direct injection into an LLM prompt.
"""

import hashlib
import os
import time
import requests
from dotenv import load_dotenv
from services.runtime_paths import user_data_base

load_dotenv(user_data_base() / ".env", override=False)
HIBP_KEY = os.getenv("HIBP_API_KEY", "")


def _hibp(email: str) -> dict:
    """HaveIBeenPwned v3 — breach list + pastes. Requires HIBP_API_KEY."""
    if not HIBP_KEY:
        return {
            "source": "haveibeenpwned",
            "status": "skipped",
            "reason": "HIBP_API_KEY not set in .env — get key at haveibeenpwned.com/API/Key",
        }
    headers = {
        "hibp-api-key": HIBP_KEY,
        "User-Agent": "Sentinel-OSINT/2.0",
    }
    try:
        r = requests.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
            headers=headers,
            params={"truncateResponse": "false"},
            timeout=10,
        )
        if r.status_code == 200:
            breaches = r.json()
        elif r.status_code == 404:
            breaches = []
        else:
            return {"source": "haveibeenpwned", "status": "error", "code": r.status_code}

        time.sleep(1.5)  # HIBP enforces 1 req / 1.5 s

        rp = requests.get(
            f"https://haveibeenpwned.com/api/v3/pasteaccount/{email}",
            headers=headers,
            timeout=10,
        )
        pastes = rp.json() if rp.status_code == 200 else []

        data_classes = sorted({dc for b in breaches for dc in b.get("DataClasses", [])})
        return {
            "source": "haveibeenpwned",
            "status": "ok",
            "breach_count": len(breaches),
            "breach_names": [b.get("Name") for b in breaches],
            "data_classes_exposed": data_classes,
            "paste_count": len(pastes),
        }
    except Exception as e:
        return {"source": "haveibeenpwned", "status": "error", "detail": str(e)[:200]}


def _breachdirectory(email: str) -> dict:
    """BreachDirectory open API — free, no key."""
    try:
        r = requests.get(
            "https://breachdirectory.org/api",
            params={"func": "auto", "term": email},
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            sources = data.get("result", [])
            return {
                "source": "breachdirectory",
                "status": "ok",
                "found": data.get("found", False),
                "result_count": len(sources) if isinstance(sources, list) else 0,
                "sources": sources[:10] if isinstance(sources, list) else [],
            }
        return {"source": "breachdirectory", "status": "error", "code": r.status_code}
    except Exception as e:
        return {"source": "breachdirectory", "status": "error", "detail": str(e)[:200]}


def _gravatar(email: str) -> dict:
    """Gravatar public profile for the address, looked up by its SHA-256 hash.

    Gravatar keys profiles by the hash of the trimmed, lower-cased address, so
    the address itself never leaves the machine. A 404 is a real answer (no
    public profile), not a failure.
    """
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    try:
        r = requests.get(
            f"https://api.gravatar.com/v3/profiles/{digest}",
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
            timeout=10,
        )
        if r.status_code == 404:
            return {"source": "gravatar", "status": "ok", "found": False}
        if r.status_code != 200:
            return {"source": "gravatar", "status": "error", "code": r.status_code}
        data = r.json()
        return {
            "source": "gravatar",
            "status": "ok",
            "found": True,
            "display_name": data.get("display_name"),
            "profile_url": data.get("profile_url"),
            "avatar_url": data.get("avatar_url"),
            "location": data.get("location"),
            "job_title": data.get("job_title"),
            "company": data.get("company"),
            "description": (data.get("description") or "")[:300] or None,
            "verified_accounts": [
                {"service": account.get("service_label"), "url": account.get("url")}
                for account in data.get("verified_accounts") or []
                if not account.get("is_hidden")
            ],
        }
    except Exception as e:
        return {"source": "gravatar", "status": "error", "detail": str(e)[:200]}


def _emailrep(email: str) -> dict:
    """Return EmailRep's normalized reputation payload or an error."""
    try:
        resp = requests.get(
            f"https://emailrep.io/{email}",
            timeout=10,
            headers={"User-Agent": "Sentinel-OSINT/2.0", "Accept": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            details = data.get("details", {})
            return {
                "score": data.get("reputation"),
                "suspicious": data.get("suspicious"),
                "references": data.get("references"),
                "blacklisted": details.get("blacklisted"),
                "malicious_activity": details.get("malicious_activity"),
                "credentials_leaked": details.get("credentials_leaked"),
                "credentials_leaked_recent": details.get("credentials_leaked_recent"),
                "data_breach": details.get("data_breach"),
                "first_seen": details.get("first_seen"),
                "last_seen": details.get("last_seen"),
                "domain_reputation": details.get("domain_reputation"),
                "new_domain": details.get("new_domain"),
                "days_since_domain_creation": details.get("days_since_domain_creation"),
                "suspicious_tld": details.get("suspicious_tld"),
                "spam": details.get("spam"),
                "free_provider": details.get("free_provider"),
                "disposable": details.get("disposable"),
                "deliverable": details.get("deliverable"),
                "spoofable": details.get("spoofable"),
                "spf_strict": details.get("spf_strict"),
                "dmarc_enforced": details.get("dmarc_enforced"),
                "profiles": details.get("profiles", []),
            }
        if resp.status_code == 400:
            return {"error": "emailrep.io: invalid email or bad request"}
        if resp.status_code == 429:
            return {"error": "emailrep.io rate limit reached (~10 requests/day without a key)"}
        return {"error": f"emailrep.io returned HTTP {resp.status_code}"}
    except requests.exceptions.Timeout:
        return {"error": "emailrep.io request timed out (>10 s)"}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def lookup(email: str, *, selected_sources=None, on_progress=None,
           should_stop=None) -> dict:
    """
    Return a normalised OSINT dict for an email address.

    Keys:
      type, query, valid_format, reputation (EmailRep payload), error
    """
    email = email.strip()
    domain_part = email.split("@")[-1] if "@" in email else ""
    valid_format = bool(email and "@" in email and "." in domain_part)

    result: dict = {
        "type": "email", "query": email, "valid_format": valid_format,
        "sources_contacted": [], "sources_skipped": [],
    }

    if not valid_format:
        result["error"] = "Invalid email format — skipping live lookup."
        return result

    selected = set(
        selected_sources or {"emailrep", "hibp", "breachdirectory", "gravatar"}
    )
    source_calls = [
        ("emailrep", "EmailRep", _emailrep),
        ("gravatar", "Gravatar", _gravatar),
        ("hibp", "Have I Been Pwned", _hibp),
        ("breachdirectory", "BreachDirectory", _breachdirectory),
    ]
    for key, label, source_lookup in source_calls:
        if key not in selected:
            continue
        if should_stop and should_stop():
            result["cancelled"] = True
            break
        if on_progress:
            on_progress(label, "checking")
        payload = source_lookup(email)
        if key == "emailrep" and not payload.get("error"):
            result["reputation"] = payload
        elif key == "emailrep":
            result["error"] = payload["error"]
            result["emailrep"] = payload
        else:
            result[key] = payload
        status = payload.get("status", "error" if payload.get("error") else "checked")
        status = "checked" if status == "ok" else status
        destination = "sources_skipped" if status == "skipped" else "sources_contacted"
        result[destination].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)

    # Convenience summary for Bloodhound prompt injector
    breach_hits = 0
    if result.get("hibp", {}).get("status") == "ok":
        breach_hits += result["hibp"].get("breach_count", 0)
    if (result.get("breachdirectory", {}).get("status") == "ok"
            and result["breachdirectory"].get("found")):
        breach_hits += result["breachdirectory"].get("result_count", 0)

    emailrep_suspicious = (
        result.get("reputation", {}).get("suspicious", False)
        or result.get("reputation", {}).get("credentials_leaked", False)
    )

    result["summary"] = {
        "breach_hits":    breach_hits,
        "suspicious":     emailrep_suspicious,
        "gravatar_profile": bool(result.get("gravatar", {}).get("found")),
        "sources_queried": len(result["sources_contacted"]),
        "sources_live":   sum(
            1 for s in [result.get("reputation"), result.get("hibp"), result.get("breachdirectory")]
            if s and (isinstance(s, dict) and s.get("status", "ok") not in ("skipped", "error"))
        ),
    }

    return result

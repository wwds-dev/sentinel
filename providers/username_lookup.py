"""
Username OSINT provider — URLScan.io free search API.

Zero-cost stack:
  • urlscan.io/api/v1/search  → pages that have been publicly scanned and whose
                                URL contains the username string; surfaces social
                                profiles, forum accounts, mentions, and platform
                                presence without requiring an API key.
                                (public rate-limit: ~100 searches/day)
  • GitHub users API          → the public profile for that exact handle
                                (60 requests/hour without a token)
  • Keybase lookup API        → the profile and its cryptographically signed
                                proofs of accounts on other sites

Opt-in (``whatsmyname=True``, used by Bloodhound's Deep Dive):
  • WhatsMyName site list    → the username checked against ~600 profile URLs
                                from this machine; see providers/whatsmyname.py
"""

from datetime import datetime, timezone

import requests

from providers import whatsmyname as _wmn

HEADERS = {"User-Agent": "Sentinel-OSINT/2.0"}


def _github(username: str) -> dict:
    """GitHub's public profile for exactly this handle; 404 means none."""
    try:
        resp = requests.get(f"https://api.github.com/users/{username}",
                            headers={**HEADERS, "Accept": "application/vnd.github+json"},
                            timeout=10)
        if resp.status_code == 404:
            return {"found": False}
        if resp.status_code in (403, 429):
            return {"error": "GitHub rate limit reached (60 requests/hour without a token)"}
        if resp.status_code != 200:
            return {"error": f"GitHub HTTP {resp.status_code}"}
        user = resp.json()
        return {
            "found": True,
            "profile_url": user.get("html_url"),
            "name": user.get("name"),
            "account_type": user.get("type"),
            "company": user.get("company"),
            "blog": user.get("blog") or None,
            "location": user.get("location"),
            "public_email": user.get("email"),
            "bio": user.get("bio"),
            "twitter": user.get("twitter_username"),
            "public_repos": user.get("public_repos"),
            "followers": user.get("followers"),
            "created": (user.get("created_at") or "")[:10] or None,
            "last_updated": (user.get("updated_at") or "")[:10] or None,
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _keybase(username: str) -> dict:
    """Keybase profile plus the accounts its owner has proven they control."""
    try:
        resp = requests.get(
            "https://keybase.io/_/api/1.0/user/lookup.json",
            params={"usernames": username, "fields": "basics,profile,proofs_summary"},
            headers=HEADERS, timeout=10,
        )
        if resp.status_code != 200:
            return {"error": f"Keybase HTTP {resp.status_code}"}
        data = resp.json()
        if (data.get("status") or {}).get("code") not in (0, None):
            return {"error": f"Keybase: {(data.get('status') or {}).get('name')}"}
        them = (data.get("them") or [None])[0]
        if not them:
            return {"found": False}
        basics = them.get("basics") or {}
        profile = them.get("profile") or {}
        proofs = (them.get("proofs_summary") or {}).get("all") or []
        created = basics.get("ctime")
        return {
            "found": True,
            "profile_url": f"https://keybase.io/{basics.get('username') or username}",
            "full_name": profile.get("full_name"),
            "location": profile.get("location"),
            "bio": profile.get("bio"),
            "created": (datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d")
                        if created else None),
            # state 1 = the proof currently verifies
            "verified_accounts": [
                {"service": proof.get("proof_type"), "name": proof.get("nametag"),
                 "url": proof.get("service_url")}
                for proof in proofs if proof.get("state") == 1
            ],
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}


def lookup(username: str, *, whatsmyname: bool = False, on_progress=None,
           on_sweep_progress=None, should_stop=None) -> dict:
    """
    Return a normalised OSINT dict for a username handle.

    Keys:
      type, query, urlscan (total + hits list), error, github, keybase,
      whatsmyname (only when ``whatsmyname=True``)

    ``on_sweep_progress(done, total)`` reports the WhatsMyName sweep, which
    takes up to a minute and would otherwise look stalled.
    """
    username = username.strip().lstrip("@")

    result: dict = {
        "type":  "username",
        "query": username,
        "sources_contacted": [],
    }

    if not username:
        result["error"] = "Empty username — skipping live lookup."
        return result

    if should_stop and should_stop():
        result["cancelled"] = True
        return result
    if on_progress:
        on_progress("URLScan", "checking")

    # Search for pages whose URL contains the username string.
    # URLScan stores real browser scans of public pages — hits here
    # indicate a real web presence at that URL/domain.
    try:
        resp = requests.get(
            "https://urlscan.io/api/v1/search/",
            params={
                "q":    f"page.url:*{username}*",
                "size": 30,
            },
            timeout=10,
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
        )

        if resp.status_code == 200:
            data = resp.json()
            hits = []
            for r in data.get("results", []):
                page = r.get("page", {})
                task = r.get("task", {})
                hits.append({
                    "url":       page.get("url"),
                    "domain":    page.get("domain"),
                    "ip":        page.get("ip"),
                    "country":   page.get("country"),
                    "title":     task.get("title"),
                    "scan_time": task.get("time"),
                })

            # Deduplicate by domain to surface unique platforms
            seen_domains: set = set()
            unique_hits = []
            for h in hits:
                d = h.get("domain") or ""
                if d not in seen_domains:
                    seen_domains.add(d)
                    unique_hits.append(h)

            result["urlscan"] = {
                "total_matching_scans": data.get("total", 0),
                "unique_domains_found": len(seen_domains),
                "hits": unique_hits[:20],   # cap at 20 deduped platforms
            }

        elif resp.status_code == 429:
            result["error"] = (
                "urlscan.io rate limit reached (~100 req/day without API key). "
                "Register at https://urlscan.io for a free key."
            )
        else:
            result["error"] = f"urlscan.io returned HTTP {resp.status_code}"

    except requests.exceptions.Timeout:
        result["error"] = "urlscan.io request timed out (>10 s)"
    except Exception as exc:
        result["error"] = str(exc)[:300]

    status = "error" if result.get("error") else "checked"
    result["sources_contacted"].append({"source": "URLScan", "status": status})
    if on_progress:
        on_progress("URLScan", status)

    for label, key, source_lookup in (("GitHub", "github", _github),
                                      ("Keybase", "keybase", _keybase)):
        if should_stop and should_stop():
            result["cancelled"] = True
            return result
        if on_progress:
            on_progress(label, "checking")
        payload = source_lookup(username)
        result[key] = payload
        status = "error" if payload.get("error") else "checked"
        result["sources_contacted"].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)

    if whatsmyname:
        if should_stop and should_stop():
            result["cancelled"] = True
            return result
        if on_progress:
            on_progress("WhatsMyName", "checking")
        sweep = _wmn.sweep(username, on_progress=on_sweep_progress,
                           should_stop=should_stop)
        result["whatsmyname"] = sweep
        if sweep.get("cancelled"):
            result["cancelled"] = True
        status = "error" if sweep.get("error") else "checked"
        result["sources_contacted"].append({"source": "WhatsMyName", "status": status})
        if on_progress:
            on_progress("WhatsMyName", status)

    return result

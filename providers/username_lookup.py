"""
Username OSINT provider — URLScan.io free search API.

Zero-cost stack:
  • urlscan.io/api/v1/search  → pages that have been publicly scanned and whose
                                URL contains the username string; surfaces social
                                profiles, forum accounts, mentions, and platform
                                presence without requiring an API key.
                                (public rate-limit: ~100 searches/day)

Opt-in (``whatsmyname=True``, used by Bloodhound's Deep Dive):
  • WhatsMyName site list    → the username checked against ~600 profile URLs
                                from this machine; see providers/whatsmyname.py
"""

import requests

from providers import whatsmyname as _wmn


def lookup(username: str, *, whatsmyname: bool = False, on_progress=None,
           on_sweep_progress=None, should_stop=None) -> dict:
    """
    Return a normalised OSINT dict for a username handle.

    Keys:
      type, query, urlscan (total + hits list), error,
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

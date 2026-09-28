"""
Dark-web exposure provider — "is my company, domain or email in a leak or on a
ransomware leak site?"

This is the check most Sentinel users actually care about, built entirely on
clearnet services that do their own crawling under their own legal setup. Sentinel
never touches Tor and never downloads leaked material: every source below returns
text metadata only.

Zero-cost stack (no key required):
  • ransomware.live  → victims posted on ransomware leak sites, searched by
                       keyword/domain/company. Free v2 API, rate-limited to
                       ~1 request/minute per endpoint, no authentication.
                       GET https://api.ransomware.live/v2/searchvictims/{keyword}
  • Ahmia            → clearnet search of indexed .onion sites, with abuse
                       content filtered out by Ahmia itself. Returns onion
                       addresses and snippets as text; Sentinel does not fetch
                       the onion sites. GET https://ahmia.fi/search/?q={term}

Key-gated (set in .env):
  • Intelligence X   → INTELX_API_KEY — leaks, pastes, dark-web and other
                       archived material matching a strong selector (email,
                       domain, company). Commercial/paid; Intelligence X
                       discontinued its free public API keys, so this source
                       stays skipped until a licensed key is present. Only the
                       search index is queried — the file/read (download)
                       endpoints are never called.

Returns a normalised dict suitable for direct injection into an LLM prompt and
for the Trace / Bloodhound result cards. Mirrors the shape of the other
``providers`` modules: per-source payloads plus ``sources_contacted`` /
``sources_skipped`` and a convenience ``summary``.
"""

import os
import re
import time
from urllib.parse import quote, unquote

import requests
from dotenv import load_dotenv

from services.runtime_paths import user_data_base

load_dotenv(user_data_base() / ".env", override=False)
INTELX_KEY = os.getenv("INTELX_API_KEY", "")

_UA = "Sentinel-OSINT/2.0"
# Ahmia serves its result page to browsers; a bare client UA is bounced to the
# home page. A plain desktop UA is honest about being an automated read and
# gets the results HTML.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_SOURCES = ("ransomware_live", "ahmia", "intelx")

# Intelligence X media codes → readable labels (from the IntelX SDK).
_INTELX_MEDIA = {
    0: "unknown", 1: "paste", 2: "paste user", 3: "forum", 4: "forum board",
    5: "forum thread", 6: "forum post", 7: "forum user", 8: "website screenshot",
    9: "website HTML", 13: "tweet", 14: "URL", 15: "PDF", 16: "Word document",
    17: "Excel document", 18: "PowerPoint document", 19: "picture",
    20: "audio", 21: "video", 22: "container", 23: "HTML file", 24: "text file",
}


# ── target parsing ─────────────────────────────────────────────────────────────

def _bare_host(value: str) -> str:
    """Strip scheme, path, port and userinfo from a domain/URL string."""
    host = value.strip().lower()
    host = host.split("//")[-1]           # drop scheme
    host = host.split("/")[0]             # drop path
    host = host.split("@")[-1]            # drop userinfo
    host = host.split(":")[0]             # drop port
    return host.strip("[]").rstrip(".")


def _terms(target: str, target_type: str) -> dict:
    """Derive the per-source search terms for a target.

    ``keyword`` is what the free crawlers (ransomware.live, Ahmia) search — the
    registrable domain for a domain/email, or the name for a company.
    ``intelx_term`` is the strongest selector Intelligence X will accept: the
    full email address when we have one, otherwise the same keyword.
    """
    text = (target or "").strip()
    label = (target_type or "").strip().lower()
    looks_email = "@" in text and "." in text.split("@")[-1]

    if "email" in label or looks_email:
        domain = _bare_host(text.split("@")[-1])
        return {"kind": "email", "address": text, "domain": domain,
                "keyword": domain, "intelx_term": text}
    if "company" in label or "organis" in label or "organiz" in label:
        return {"kind": "company", "name": text,
                "keyword": text, "intelx_term": text}
    if "domain" in label or "ip" in label or ("." in text and " " not in text):
        domain = _bare_host(text)
        return {"kind": "domain", "domain": domain,
                "keyword": domain, "intelx_term": domain}
    # Fall back to treating a free-text target as a company/name search.
    return {"kind": "company", "name": text,
            "keyword": text, "intelx_term": text}


def _core_label(keyword: str) -> str:
    """The distinctive token to test a leak record against.

    For "example.com" that is "example"; for a company name it is the whole
    lower-cased string. Used to tell a record where the target IS the victim
    from one that merely mentions the target in someone else's description.
    """
    kw = (keyword or "").strip().lower()
    if "." in kw and " " not in kw:
        parts = [p for p in kw.split(".") if p]
        # Drop the public suffix label ("com", "co", "uk"…) — the SLD is the name.
        if len(parts) >= 2:
            return parts[-2]
    return kw


# ── ransomware.live ─────────────────────────────────────────────────────────────

def _ransomware_live(keyword: str) -> dict:
    """Search ransomware.live victims for a keyword/domain/company.

    A hit means the name appeared in a post on a ransomware group's leak site.
    The API matches loosely (the keyword can appear in another victim's
    description), so each victim is tagged with *where* it matched — a match on
    the victim name or its domain is the target being extorted; a
    description-only match is a mention, not an exposure.
    """
    core = _core_label(keyword)
    try:
        resp = requests.get(
            f"https://api.ransomware.live/v2/searchvictims/{quote(keyword, safe='')}",
            timeout=15,
            headers={"User-Agent": _UA, "Accept": "application/json"},
        )
        if resp.status_code == 429:
            return {"source": "ransomware.live", "status": "error",
                    "detail": "rate limited (the free API allows ~1 request/minute) — try again shortly"}
        if resp.status_code == 404:
            return {"source": "ransomware.live", "status": "ok",
                    "total_results": 0, "direct_victim_matches": 0, "victims": []}
        if resp.status_code != 200:
            return {"source": "ransomware.live", "status": "error", "code": resp.status_code}

        data = resp.json()
        if not isinstance(data, list):
            data = []

        victims = []
        direct = 0
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("victim") or "").strip()
            domain = (entry.get("domain") or "").strip().lower()
            description = entry.get("description") or ""
            if core and core in name.lower():
                match = "victim-name"
            elif core and core in domain:
                match = "domain"
            elif core and core in description.lower():
                match = "description-only"
            else:
                match = "keyword"
            if match in ("victim-name", "domain"):
                direct += 1
            victims.append({
                "victim": name or None,
                "group": entry.get("group") or None,
                "domain": domain or None,
                "country": entry.get("country") or None,
                "activity": entry.get("activity") or None,
                "attack_date": entry.get("attackdate") or None,
                "discovered": entry.get("discovered") or None,
                "post_url": entry.get("url") or None,
                "claim_url": entry.get("claim_url") or None,
                "description": (description[:300] + "…") if len(description) > 300 else (description or None),
                "match": match,
            })

        # Surface the target's own listings first.
        rank = {"victim-name": 0, "domain": 1, "keyword": 2, "description-only": 3}
        victims.sort(key=lambda v: rank.get(v["match"], 4))
        return {
            "source": "ransomware.live",
            "status": "ok",
            "total_results": len(victims),
            "direct_victim_matches": direct,
            "victims": victims[:25],
        }
    except requests.exceptions.Timeout:
        return {"source": "ransomware.live", "status": "error", "detail": "request timed out (>15 s)"}
    except Exception as exc:
        return {"source": "ransomware.live", "status": "error", "detail": str(exc)[:200]}


# ── Ahmia ────────────────────────────────────────────────────────────────────

def _ahmia(term: str) -> dict:
    """Search Ahmia's clearnet index of .onion sites for a term.

    Ahmia crawls onion services and filters out abuse content on its own side.
    We read the results page as text and extract the onion address, title and
    snippet of each hit — Sentinel never contacts the onion sites themselves.

    Ahmia redirects a query it will not serve (unmatched, or a rate-limited or
    datacentre client) to its home page; that is reported as "no results" with a
    note rather than an error, because it usually succeeds from an ordinary
    connection.
    """
    try:
        resp = requests.get(
            "https://ahmia.fi/search/",
            params={"q": term},
            timeout=15,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            return {"source": "ahmia", "status": "ok", "found": False, "results": [],
                    "note": "Ahmia redirected the query to its home page (no indexed match, "
                            "or the request was rate-limited); retry from a normal connection."}
        if resp.status_code != 200:
            return {"source": "ahmia", "status": "error", "code": resp.status_code}

        results = _parse_ahmia(resp.text)
        return {
            "source": "ahmia",
            "status": "ok",
            "found": bool(results),
            "result_count": len(results),
            "results": results[:20],
        }
    except requests.exceptions.Timeout:
        return {"source": "ahmia", "status": "error", "detail": "request timed out (>15 s)"}
    except Exception as exc:
        return {"source": "ahmia", "status": "error", "detail": str(exc)[:200]}


_ONION_RE = re.compile(r"[a-z2-7]{16,56}\.onion", re.IGNORECASE)
_REDIRECT_RE = re.compile(r"redirect_url=([^\"'&\s]+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_ahmia(html: str) -> list[dict]:
    """Pull (title, onion, snippet) tuples out of an Ahmia results page.

    Written to survive markup churn: it keys off the two stable tokens on the
    page — the ``redirect_url=`` result links and bare ``*.onion`` addresses —
    rather than a specific class name or element nesting.
    """
    results: list[dict] = []
    seen: set[str] = set()

    # Split the page into per-result blocks. Ahmia wraps each hit in an <li>;
    # if that changes, the whole body is treated as one block and we still pull
    # every onion address out of it.
    blocks = re.split(r"<li\b", html, flags=re.IGNORECASE)
    blocks = blocks[1:] if len(blocks) > 1 else [html]

    for block in blocks:
        onion = None
        redirect = _REDIRECT_RE.search(block)
        if redirect:
            decoded = unquote(redirect.group(1))
            m = _ONION_RE.search(decoded)
            if m:
                onion = m.group(0).lower()
        if not onion:
            m = _ONION_RE.search(block)
            onion = m.group(0).lower() if m else None
        if not onion or onion in seen:
            continue
        seen.add(onion)

        title = None
        tmatch = re.search(r"<h[1-4][^>]*>(.*?)</h[1-4]>", block, re.IGNORECASE | re.DOTALL)
        if tmatch:
            title = _TAG_RE.sub("", tmatch.group(1)).strip() or None

        snippet = None
        pmatch = re.search(r"<p[^>]*>(.*?)</p>", block, re.IGNORECASE | re.DOTALL)
        if pmatch:
            text = _TAG_RE.sub("", pmatch.group(1)).strip()
            snippet = (text[:280] + "…") if len(text) > 280 else (text or None)

        results.append({
            "title": title,
            "onion": f"http://{onion}",
            "snippet": snippet,
        })
    return results


# ── Intelligence X ───────────────────────────────────────────────────────────

def _intelx(term: str, *, should_stop=None) -> dict:
    """Search Intelligence X for a strong selector (email/domain/company).

    Two-step API: POST a search, then poll for records until the search
    reports it is done. Only the *index* is read — file content is never
    downloaded. Requires a licensed INTELX_API_KEY.
    """
    if not INTELX_KEY:
        return {
            "source": "intelx",
            "status": "skipped",
            "reason": "INTELX_API_KEY not set in .env — Intelligence X is a paid service "
                      "(its free public API keys were discontinued); add a licensed key to enable it.",
        }

    base = "https://2.intelx.io"
    headers = {"X-Key": INTELX_KEY, "User-Agent": _UA}
    try:
        start = requests.post(
            f"{base}/intelligent/search",
            json={
                "term": term, "buckets": [], "lookuplevel": 0, "maxresults": 50,
                "timeout": 5, "datefrom": "", "dateto": "", "sort": 4,
                "media": 0, "terminate": [],
            },
            headers=headers,
            timeout=15,
        )
        if start.status_code in (401, 402, 403):
            return {"source": "intelx", "status": "error",
                    "detail": f"Intelligence X rejected the key (HTTP {start.status_code}) — "
                              "check the key and that the licence covers API search"}
        if start.status_code != 200:
            return {"source": "intelx", "status": "error", "code": start.status_code}

        payload = start.json()
        search_id = payload.get("id")
        # status 1 at start means the selector was invalid or returned nothing.
        if not search_id or payload.get("status") == 1:
            return {"source": "intelx", "status": "ok", "total": 0, "records": []}

        records: list[dict] = []
        for _ in range(6):  # poll up to ~6 rounds; status 3 = "keep trying"
            if should_stop and should_stop():
                break
            time.sleep(1.0)
            res = requests.get(
                f"{base}/intelligent/search/result",
                params={"id": search_id, "limit": 50},
                headers=headers,
                timeout=15,
            )
            if res.status_code != 200:
                break
            body = res.json()
            for rec in body.get("records", []) or []:
                media = rec.get("media")
                records.append({
                    "name": rec.get("name") or None,
                    "bucket": rec.get("bucket") or None,
                    "date": rec.get("date") or None,
                    "added": rec.get("added") or None,
                    "media_type": _INTELX_MEDIA.get(media, str(media)) if media is not None else None,
                    "relevance": rec.get("xscore"),
                    "system_id": rec.get("systemid") or None,
                })
            status = body.get("status")
            if status != 3:  # 0 = done-with-results, 1 = no more, 2 = id gone
                break

        # Best-effort: release the server-side search slot.
        try:
            requests.get(f"{base}/intelligent/search/terminate",
                         params={"id": search_id}, headers=headers, timeout=8)
        except Exception:
            pass

        # De-duplicate by system id while preserving order.
        seen: set[str] = set()
        unique = []
        for rec in records:
            sid = rec.get("system_id") or (rec.get("name"), rec.get("date"))
            if sid in seen:
                continue
            seen.add(sid)
            unique.append(rec)

        return {"source": "intelx", "status": "ok",
                "total": len(unique), "records": unique[:30]}
    except requests.exceptions.Timeout:
        return {"source": "intelx", "status": "error", "detail": "request timed out (>15 s)"}
    except Exception as exc:
        return {"source": "intelx", "status": "error", "detail": str(exc)[:200]}


# ── public interface ──────────────────────────────────────────────────────────

def lookup(target: str, target_type: str = "", *, selected_sources=None,
           on_progress=None, should_stop=None) -> dict:
    """
    Return a normalised dark-web exposure dict for a domain, company or email.

    Keys:
      type ("exposure"), query, target_type, keyword, sources_contacted,
      sources_skipped, ransomware_live, ahmia, intelx, summary
    """
    target = (target or "").strip()
    terms = _terms(target, target_type)

    result: dict = {
        "type": "exposure",
        "query": target,
        "target_type": terms["kind"],
        "keyword": terms["keyword"],
        "sources_contacted": [],
        "sources_skipped": [],
    }

    if not target:
        result["error"] = "Empty target — skipping exposure check."
        return result

    selected = set(selected_sources or DEFAULT_SOURCES)
    source_calls = [
        ("ransomware_live", "Ransomware.live", "ransomware_live",
         lambda: _ransomware_live(terms["keyword"])),
        ("ahmia", "Ahmia", "ahmia",
         lambda: _ahmia(terms["keyword"])),
        ("intelx", "Intelligence X", "intelx",
         lambda: _intelx(terms["intelx_term"], should_stop=should_stop)),
    ]

    for key, label, result_key, call in source_calls:
        if key not in selected:
            continue
        if should_stop and should_stop():
            result["cancelled"] = True
            break
        if on_progress:
            on_progress(label, "checking")
        payload = call()
        result[result_key] = payload
        status = payload.get("status", "error")
        status = "checked" if status == "ok" else status
        destination = "sources_skipped" if status == "skipped" else "sources_contacted"
        result[destination].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)

    # ── Convenience summary for the panel and the Bloodhound prompt ──────────
    rl = result.get("ransomware_live", {})
    ah = result.get("ahmia", {})
    ix = result.get("intelx", {})
    rl_ok = rl.get("status") == "ok"
    ransomware_direct = rl.get("direct_victim_matches", 0) if rl_ok else 0
    ransomware_total = rl.get("total_results", 0) if rl_ok else 0
    darkweb_hits = ah.get("result_count", 0) if ah.get("status") == "ok" else 0
    intelx_hits = ix.get("total", 0) if ix.get("status") == "ok" else 0

    result["summary"] = {
        # A direct victim/domain match is a strong claim; a bare listing, an Ahmia
        # index hit or an IntelX record is a lead to review, not proof of breach.
        "exposure_detected": bool(ransomware_total or darkweb_hits or intelx_hits),
        "on_ransomware_leak_site": ransomware_direct > 0,
        "ransomware_victim_matches": ransomware_direct,
        "ransomware_listings": ransomware_total,
        "darkweb_index_hits": darkweb_hits,
        "intelx_records": intelx_hits,
        "sources_queried": len(result["sources_contacted"]),
    }
    return result

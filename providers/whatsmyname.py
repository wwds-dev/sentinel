"""
Username sweep — the WhatsMyName site list checked from this machine.

WhatsMyName (https://github.com/WebBreacher/WhatsMyName, CC BY-SA 4.0,
Micah Hoffman and contributors) publishes, for ~700 sites, the profile URL
pattern and the response that distinguishes "account exists" from "no such
account". Sentinel downloads that list, caches it for a week, and requests
each profile URL directly; no third-party aggregator sees the username.

Deliberately excluded:
  • sites the list marks invalid;
  • sites behind bot protection (Cloudflare, captchas, …) — they answer a
    challenge page instead of the profile, so every result would be a guess;
  • the list's NSFW category.

A hit is reported only when the response matches the site's "exists"
signature exactly (status code *and* marker text). Anything else is either
a confirmed miss or counted as inconclusive; nothing is inferred.
"""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import quote

import requests

from services.runtime_paths import user_data_base

WMN_DATA_URL = (
    "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
)
CACHE_MAX_AGE = 7 * 24 * 3600
EXCLUDED_CATEGORIES = {"xx NSFW xx"}
MAX_WORKERS = 12
REQUEST_TIMEOUT = 8
SWEEP_BUDGET_SECONDS = 120
#: A refused connection is the network pushing back (a router's flood
#: protection, a VPN or firewall rate limit), not the site answering. When
#: BACKOFF_TRIGGER of the last BACKOFF_WINDOW answers are refusals, the sweep
#: halves how many requests it keeps in flight and pauses, then climbs back
#: one at a time after a clean window. Refused sites get one retry at the end.
REFUSAL_REASONS = {"ConnectionError"}
BACKOFF_WINDOW = 20
BACKOFF_TRIGGER = 5
BACKOFF_PAUSE_SECONDS = 3.0
MIN_WORKERS = 2
RETRY_WORKERS = 4
#: Above this share of refused/failed connections the sweep says so: it is
#: what a VPN or firewall rate limit looks like, and it makes misses unreliable.
NETWORK_FAILURE_WARNING_SHARE = 0.25
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def _cache_path() -> Path:
    return user_data_base() / "data" / "cache" / "wmn-data.json"


def load_sites(*, now: float | None = None) -> tuple[list[dict], str]:
    """Return (usable sites, where the list came from).

    A cache younger than a week is used as-is. Otherwise the list is
    downloaded; if that fails, a stale cache is still better than nothing.
    Raises RuntimeError only when there is neither a download nor a cache.
    """
    now = time.time() if now is None else now
    cache = _cache_path()
    cached = None
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
    if cached is not None and now - cache.stat().st_mtime < CACHE_MAX_AGE:
        return usable_sites(cached), "cache"

    try:
        resp = requests.get(WMN_DATA_URL, timeout=20,
                            headers={"User-Agent": "Sentinel-OSINT/2.0"})
        resp.raise_for_status()
        data = resp.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return usable_sites(data), "download"
    except Exception as exc:
        if cached is not None:
            return usable_sites(cached), "stale cache"
        raise RuntimeError(f"WhatsMyName site list unavailable: {exc}") from exc


def usable_sites(data: dict) -> list[dict]:
    """The sites whose answer can be read reliably (see module docstring)."""
    sites = []
    for site in (data or {}).get("sites", []):
        if not site.get("uri_check") or "{account}" not in (
                site["uri_check"] + site.get("post_body", "")):
            continue
        if site.get("valid") is False or site.get("protection"):
            continue
        if site.get("cat") in EXCLUDED_CATEGORIES:
            continue
        sites.append(site)
    return sites


def _account_for(site: dict, username: str) -> str:
    account = username
    for char in site.get("strip_bad_char") or "":
        account = account.replace(char, "")
    return account


def _as_int(value, default: int = 0) -> int:
    """Coerce a WhatsMyName status code to int. A present-but-null e_code/m_code
    (or any non-numeric value) would otherwise raise TypeError inside the
    redirect check and be miscounted as a network failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def check_site(site: dict, username: str, session=None) -> dict:
    """Request one profile and classify it as found / missing / unclear."""
    http = session or requests
    account = _account_for(site, username)
    url = site["uri_check"].replace("{account}", quote(account, safe=""))
    headers = {"User-Agent": USER_AGENT, **(site.get("headers") or {})}
    outcome = {"site": site.get("name"), "category": site.get("cat")}
    try:
        if site.get("post_body"):
            body = site["post_body"].replace("{account}", account)
            resp = http.post(url, data=body.encode("utf-8"), headers=headers,
                             timeout=REQUEST_TIMEOUT, allow_redirects=False)
        else:
            resp = http.get(url, headers=headers, timeout=REQUEST_TIMEOUT,
                            allow_redirects=False)
            # Redirects are read, not followed, because many sites answer "no
            # such user" with one. When neither signature is a redirect, a
            # redirect is only a moved URL (https, trailing slash): follow it.
            if (300 <= resp.status_code < 400
                    and not 300 <= _as_int(site.get("e_code")) < 400
                    and not 300 <= _as_int(site.get("m_code")) < 400):
                resp = http.get(url, headers=headers, timeout=REQUEST_TIMEOUT,
                                allow_redirects=True)
        text = resp.text or ""
    except Exception as exc:
        return {**outcome, "status": "unclear", "reason": type(exc).__name__}

    if resp.status_code == site.get("e_code") and site.get("e_string", "") in text:
        pretty = site.get("uri_pretty") or site["uri_check"]
        return {**outcome, "status": "found",
                "url": pretty.replace("{account}", quote(account, safe=""))}
    if resp.status_code == site.get("m_code") and site.get("m_string", "") in text:
        return {**outcome, "status": "missing"}
    return {**outcome, "status": "unclear", "reason": f"HTTP {resp.status_code}"}


def sweep(username: str, *, on_progress=None, should_stop=None,
          sites: list[dict] | None = None) -> dict:
    """Check every usable WhatsMyName site for ``username``.

    ``on_progress(done, total)`` is called as sites finish. Cancelling keeps
    the hits already found and marks the result partial.
    """
    username = username.strip().lstrip("@")
    result: dict = {
        "attribution": "WhatsMyName, CC BY-SA 4.0 — github.com/WebBreacher/WhatsMyName",
    }
    try:
        if sites is None:
            sites, origin = load_sites()
        else:
            origin = "supplied"
    except RuntimeError as exc:
        return {**result, "error": str(exc)}

    found, missing, unclear = [], 0, 0
    reasons: Counter = Counter()
    deadline = time.monotonic() + SWEEP_BUDGET_SECONDS
    stopped = timed_out = False
    queue = deque(sites)
    deferred: list[dict] = []          # refused once, waiting for their retry
    retrying = False
    limit = MAX_WORKERS
    pause_until = 0.0
    recent: deque = deque(maxlen=BACKOFF_WINDOW)
    slowdowns = recovered = retried = 0
    in_flight: dict = {}
    done_count = 0

    def record(outcome: dict) -> None:
        nonlocal done_count, missing, unclear
        done_count += 1
        if outcome["status"] == "found":
            found.append(outcome)
        elif outcome["status"] == "missing":
            missing += 1
        else:
            unclear += 1
            reasons[outcome.get("reason") or "unknown"] += 1

    # Not a `with` block: its exit waits for every in-flight request, and a
    # Stop should return at once rather than after the slowest site times out.
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        while True:
            if should_stop and should_stop():
                stopped = True
                break
            now = time.monotonic()
            if now > deadline:
                timed_out = True
                break
            if now >= pause_until:
                while queue and len(in_flight) < limit:
                    site = queue.popleft()
                    in_flight[pool.submit(check_site, site, username)] = site
            if not in_flight:
                if queue:                      # paused: wait out the back-off
                    time.sleep(min(0.2, max(pause_until - now, 0.01)))
                    continue
                if deferred and not retrying:  # one gentle retry of refused sites
                    retrying = True
                    retried = len(deferred)
                    queue.extend(deferred)
                    deferred.clear()
                    limit = min(limit, RETRY_WORKERS)
                    pause_until = time.monotonic() + BACKOFF_PAUSE_SECONDS
                    continue
                break
            finished, _ = wait(in_flight, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in finished:
                site = in_flight.pop(future)
                outcome = future.result()
                refused = outcome.get("reason") in REFUSAL_REASONS
                recent.append(refused)
                if refused and not retrying:
                    deferred.append(site)
                    continue
                if retrying and outcome["status"] in ("found", "missing"):
                    recovered += 1
                record(outcome)
            if finished and on_progress:
                on_progress(done_count, len(sites))
            if sum(recent) >= BACKOFF_TRIGGER:
                limit = max(MIN_WORKERS, limit // 2)
                pause_until = time.monotonic() + BACKOFF_PAUSE_SECONDS
                slowdowns += 1
                recent.clear()
            elif len(recent) == BACKOFF_WINDOW and limit < MAX_WORKERS and not retrying:
                limit += 1
                recent.clear()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Refused sites whose retry never finished (Stop, time budget) still count.
    # Once the retry round has begun, everything queued or in flight is one
    # of them; before it, the queue holds sites never tried, which do not.
    unfinished = len(deferred) + (len(queue) + len(in_flight) if retrying else 0)
    for _ in range(unfinished):
        record({"status": "unclear", "reason": "ConnectionError"})

    found.sort(key=lambda hit: (hit.get("category") or "", hit.get("site") or ""))
    result.update({
        "site_list": origin,
        "sites_checked": done_count,
        "sites_total": len(sites),
        "found_count": len(found),
        "not_found_count": missing,
        "inconclusive_count": unclear,
        "inconclusive_reasons": dict(reasons.most_common(5)),
        "found": found,
    })
    if slowdowns or retrying:
        result["backoff"] = {
            "slowdowns": slowdowns,
            "retried": retried,
            "recovered_on_retry": recovered,
            "final_parallel_requests": limit,
        }
    failed = sum(n for reason, n in reasons.items() if not reason.startswith("HTTP"))
    if done_count and failed / done_count > NETWORK_FAILURE_WARNING_SHARE:
        result["network_warning"] = (
            f"{failed} of {done_count} sites could not be reached. A VPN, firewall or "
            "network rate limit is the usual cause; hits are still valid, but many "
            "sites went unchecked. Retry later or on another connection."
        )
    if stopped:
        result["cancelled"] = True
    if timed_out:
        result["timed_out"] = f"Stopped after {SWEEP_BUDGET_SECONDS} s; results are partial."
    return result

"""Key-gated threat-intelligence sources for domain and IP lookups.

Each source here needs an API key saved on Settings → OSINT Keys and is left
out of a lookup entirely until one is set, so a lookup never sends a target to
a service you have not signed up for. Trace's consent prompt names exactly the
sources ``configured`` returns, and Bloodhound collects the same ones.

IP addresses:
  • AbuseIPDB      → ABUSEIPDB_API_KEY   — community abuse reports and a confidence score
  • GreyNoise      → GREYNOISE_API_KEY   — mass scanner / benign service / unknown
  • VirusTotal     → VIRUSTOTAL_API_KEY  — verdicts from dozens of security vendors
  • AlienVault OTX → OTX_API_KEY         — threat-intel reports (pulses) naming the IP
  • Shodan         → SHODAN_API_KEY      — full host record: services, banners, CVEs
  • Censys         → CENSYS_API_KEY      — services and certificates from Censys scans
                     (a Platform personal access token; CENSYS_ORG_ID for paid orgs)

Domains:
  • VirusTotal, AlienVault OTX (as above, for the domain)
  • SecurityTrails → SECURITYTRAILS_API_KEY — current DNS and subdomain count
  • DomainTools    → DOMAINTOOLS_API_KEY  — domain profile and risk score
                     (save it as username:key to have requests HMAC-signed)
  • Shodan DNS     → SHODAN_API_KEY      — subdomains and records Shodan has seen
  • URLScan        → URLSCAN_API_KEY     — recent public scans of the domain
  • Hunter         → HUNTER_API_KEY      — the company's email pattern and its
                     generic (role) addresses. Named personal addresses are never
                     requested: Sentinel does not do people-search, so only their
                     count and departments are reported.

Every function returns a plain dict for the Trace cards and the Bloodhound
prompt, with ``error`` set when the call failed. Keys travel in headers where
the service allows it; where a service only takes the key in the URL, error
text is scrubbed of it before it is returned.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

import requests

_UA = "Sentinel-OSINT/2.0"
_TIMEOUT = 15


@dataclass(frozen=True)
class KeyedSource:
    label: str        # in progress lines and the consent prompt
    result_key: str   # where the payload lands in the lookup result
    card: str         # Trace's card title
    env: str          # the .env key that switches it on
    kinds: tuple      # "ip", "domain"
    func: str         # function in this module, looked up at call time


SOURCES: tuple[KeyedSource, ...] = (
    KeyedSource("AbuseIPDB", "abuse_reports", "Abuse reports (AbuseIPDB)",
                "ABUSEIPDB_API_KEY", ("ip",), "abuseipdb"),
    KeyedSource("GreyNoise", "scanner_noise", "Internet scanner check (GreyNoise)",
                "GREYNOISE_API_KEY", ("ip",), "greynoise"),
    KeyedSource("VirusTotal", "virustotal", "Security vendor verdicts (VirusTotal)",
                "VIRUSTOTAL_API_KEY", ("ip", "domain"), "virustotal"),
    KeyedSource("AlienVault OTX", "otx", "Threat reports (AlienVault OTX)",
                "OTX_API_KEY", ("ip", "domain"), "otx"),
    KeyedSource("Shodan", "shodan_host", "Host services and banners (Shodan)",
                "SHODAN_API_KEY", ("ip",), "shodan_host"),
    KeyedSource("Censys", "censys_host", "Host services and certificates (Censys)",
                "CENSYS_API_KEY", ("ip",), "censys_host"),
    KeyedSource("SecurityTrails", "securitytrails", "Current DNS and subdomains (SecurityTrails)",
                "SECURITYTRAILS_API_KEY", ("domain",), "securitytrails"),
    KeyedSource("DomainTools", "domaintools", "Domain profile and risk (DomainTools)",
                "DOMAINTOOLS_API_KEY", ("domain",), "domaintools"),
    KeyedSource("Shodan DNS", "shodan_dns", "Subdomains Shodan has seen",
                "SHODAN_API_KEY", ("domain",), "shodan_dns"),
    KeyedSource("URLScan", "urlscan_domain", "Recent public scans (URLScan)",
                "URLSCAN_API_KEY", ("domain",), "urlscan_domain"),
    KeyedSource("Hunter", "hunter_domain", "Email pattern and role addresses (Hunter)",
                "HUNTER_API_KEY", ("domain",), "hunter_domain"),
)


def key(env: str) -> str:
    """Read a key live, so one saved on the OSINT Keys tab works without a restart."""
    return os.getenv(env, "").strip()


def configured(kind: str) -> list[KeyedSource]:
    """The sources a lookup of this kind ("ip" or "domain") will contact now."""
    return [s for s in SOURCES if kind in s.kinds and key(s.env)]


def call(source: KeyedSource, target: str) -> dict:
    return globals()[source.func](target)


# ── shared request handling ──────────────────────────────────────────────────

class _Failed(Exception):
    pass


def _scrub(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text[:300]


def _request(name: str, method: str, url: str, *, secret: str = "",
             not_found=None, ok=(200,), statuses=None, **kwargs):
    """Make one call and turn the usual failures into a readable ``_Failed``.

    ``not_found`` is returned as the parsed body for a 404, for services where
    404 means "no record of this target" rather than a broken call.
    ``statuses`` overrides the message for a code a service uses its own way
    (Hunter answers a rate limit with 403 and a spent quota with 429).
    """
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    headers.update(kwargs.pop("headers", {}) or {})
    try:
        resp = requests.request(method, url, headers=headers,
                                timeout=kwargs.pop("timeout", _TIMEOUT), **kwargs)
    except requests.exceptions.Timeout:
        raise _Failed(f"{name} did not respond in time")
    except requests.exceptions.RequestException as exc:
        raise _Failed(f"{name} could not be reached: {_scrub(str(exc), secret)}")
    if resp.status_code == 404 and not_found is not None:
        # Keep the service's own words when it sends them (GreyNoise explains
        # why an IP is unknown); the default only fills what it leaves out.
        try:
            body = resp.json()
        except ValueError:
            body = None
        return {**not_found, **body} if isinstance(body, dict) else not_found
    if statuses and resp.status_code in statuses:
        raise _Failed(f"{name} {statuses[resp.status_code]}")
    if resp.status_code in (401, 403):
        raise _Failed(f"{name} rejected the key, or the plan does not include this lookup")
    if resp.status_code == 429:
        raise _Failed(f"{name} rate limit or quota reached; try again later")
    if resp.status_code not in ok:
        raise _Failed(f"{name} HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise _Failed(f"{name} returned something other than JSON")


def _guard(func):
    """Return ``{"error": ...}`` instead of raising, like every other source."""
    def wrapper(target: str) -> dict:
        try:
            return func(target)
        except _Failed as exc:
            return {"error": str(exc)}
        except Exception as exc:  # a parsing surprise must not end the lookup
            return {"error": f"unexpected response: {type(exc).__name__}"}
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def _is_ipv6(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).version == 6
    except ValueError:
        return False


# ── IP and domain reputation ─────────────────────────────────────────────────

@_guard
def abuseipdb(ip: str) -> dict:
    """AbuseIPDB: abuse reports for an IP over the last 90 days."""
    body = _request("AbuseIPDB", "GET", "https://api.abuseipdb.com/api/v2/check",
                    params={"ipAddress": ip, "maxAgeInDays": 90},
                    headers={"Key": key("ABUSEIPDB_API_KEY")})
    data = body.get("data") or {}
    return {
        "abuse_confidence": data.get("abuseConfidenceScore"),
        "total_reports": data.get("totalReports"),
        "distinct_reporters": data.get("numDistinctUsers"),
        "last_reported": data.get("lastReportedAt"),
        "usage_type": data.get("usageType"),
        "isp": data.get("isp"),
        "domain": data.get("domain"),
        "country": data.get("countryCode"),
        "is_tor": data.get("isTor"),
        "allowlisted": data.get("isWhitelisted"),
        "note": ("Confidence is AbuseIPDB's 0–100 estimate that the address is "
                 "abusive, from community reports over 90 days."),
    }


@_guard
def greynoise(ip: str) -> dict:
    """GreyNoise Community: is this IP mass-scanning the internet, or a known service?"""
    body = _request(
        "GreyNoise", "GET", f"https://api.greynoise.io/v3/community/{ip}",
        headers={"key": key("GREYNOISE_API_KEY")},
        not_found={"noise": False, "riot": False,
                   "message": "Not observed scanning the internet, and not a known service."})
    return {
        "scanning_internet": body.get("noise"),
        "known_benign_service": body.get("riot"),
        "classification": body.get("classification"),
        "name": body.get("name"),
        "last_seen": body.get("last_seen"),
        "message": body.get("message"),
        "link": body.get("link"),
        "note": ("Noise means the address hits the whole internet, so traffic from it "
                 "is rarely aimed at you; a known benign service is a CDN, resolver or "
                 "similar."),
    }


@_guard
def virustotal(target: str) -> dict:
    """VirusTotal: vendor verdicts and community reputation for an IP or domain."""
    kind = "ip_addresses" if _is_ip(target) else "domains"
    body = _request("VirusTotal", "GET",
                    f"https://www.virustotal.com/api/v3/{kind}/{target}",
                    headers={"x-apikey": key("VIRUSTOTAL_API_KEY")},
                    not_found={"data": None})
    if body.get("data") is None:
        return {"found": False, "note": "VirusTotal has no record of this target."}
    attrs = (body.get("data") or {}).get("attributes") or {}
    stats = attrs.get("last_analysis_stats") or {}
    flagged = sorted(
        engine for engine, verdict in (attrs.get("last_analysis_results") or {}).items()
        if isinstance(verdict, dict) and verdict.get("category") in ("malicious", "suspicious")
    )
    categories = attrs.get("categories") or {}
    return {
        "found": True,
        "malicious": stats.get("malicious", 0),
        "suspicious": stats.get("suspicious", 0),
        "harmless": stats.get("harmless", 0),
        "undetected": stats.get("undetected", 0),
        "flagged_by": flagged[:15],
        "reputation": attrs.get("reputation"),
        "community_votes": attrs.get("total_votes"),
        "categories": sorted(set(categories.values()))[:10] if isinstance(categories, dict) else [],
        "tags": (attrs.get("tags") or [])[:10],
        "network_owner": attrs.get("as_owner"),
        "country": attrs.get("country"),
        "registrar": attrs.get("registrar"),
        "last_analysis": attrs.get("last_analysis_date"),
        "link": f"https://www.virustotal.com/gui/{'ip-address' if kind == 'ip_addresses' else 'domain'}/{target}",
    }


@_guard
def otx(target: str) -> dict:
    """AlienVault OTX: community threat reports (pulses) that name the target."""
    if _is_ip(target):
        section = f"IPv6/{target}" if _is_ipv6(target) else f"IPv4/{target}"
    else:
        section = f"domain/{target}"
    body = _request("AlienVault OTX", "GET",
                    f"https://otx.alienvault.com/api/v1/indicators/{section}/general",
                    headers={"X-OTX-API-KEY": key("OTX_API_KEY")},
                    not_found={"pulse_info": {"count": 0, "pulses": []}})
    info = body.get("pulse_info") or {}
    pulses = info.get("pulses") or []
    families, adversaries, tags = set(), set(), set()
    reports = []
    for pulse in pulses:
        if not isinstance(pulse, dict):
            continue
        reports.append({"name": pulse.get("name"), "created": pulse.get("created"),
                        "author": (pulse.get("author") or {}).get("username")})
        for fam in pulse.get("malware_families") or []:
            families.add(fam.get("display_name") if isinstance(fam, dict) else str(fam))
        if pulse.get("adversary"):
            adversaries.add(pulse["adversary"])
        tags.update(str(t) for t in (pulse.get("tags") or [])[:5])
    validation = [v.get("name") for v in body.get("validation") or [] if isinstance(v, dict)]
    return {
        "pulse_count": info.get("count", len(reports)),
        "reports": reports[:10],
        "malware_families": sorted(f for f in families if f)[:10],
        "adversaries": sorted(adversaries)[:10],
        "tags": sorted(tags)[:15],
        "allowlisted_by": [v for v in validation if v][:5],
        "note": ("A pulse is a threat report someone shared on OTX. Many pulses are "
                 "automated feeds, so read the names before treating a count as a verdict."),
    }


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# ── Host search engines ──────────────────────────────────────────────────────

@_guard
def shodan_host(ip: str) -> dict:
    """Shodan: the full host record — services, banners, operating system, CVEs."""
    secret = key("SHODAN_API_KEY")
    body = _request("Shodan", "GET", f"https://api.shodan.io/shodan/host/{ip}",
                    params={"key": secret, "minify": "false"}, secret=secret,
                    not_found={"_missing": True})
    if body.get("_missing"):
        return {"found": False, "note": "Shodan has no record of this address."}
    services = []
    for banner in body.get("data") or []:
        if not isinstance(banner, dict):
            continue
        services.append({
            "port": banner.get("port"),
            "transport": banner.get("transport"),
            "product": banner.get("product"),
            "version": banner.get("version"),
            "module": (banner.get("_shodan") or {}).get("module"),
            "seen": banner.get("timestamp"),
        })
    vulns = body.get("vulns") or []
    if isinstance(vulns, dict):
        vulns = list(vulns)
    return {
        "found": True,
        "org": body.get("org"),
        "isp": body.get("isp"),
        "asn": body.get("asn"),
        "os": body.get("os"),
        "country": body.get("country_name"),
        "city": body.get("city"),
        "hostnames": (body.get("hostnames") or [])[:15],
        "domains": (body.get("domains") or [])[:15],
        "ports": sorted(body.get("ports") or []),
        "services": services[:25],
        "vulnerabilities": sorted(vulns)[:30],
        "tags": body.get("tags") or [],
        "last_update": body.get("last_update"),
        "note": "What Shodan's own crawlers recorded. Sentinel does not scan the host.",
    }


@_guard
def shodan_dns(domain: str) -> dict:
    """Shodan DNS: subdomains and records Shodan has seen for a domain (1 query credit)."""
    secret = key("SHODAN_API_KEY")
    body = _request("Shodan DNS", "GET", f"https://api.shodan.io/dns/domain/{domain}",
                    params={"key": secret}, secret=secret,
                    not_found={"subdomains": [], "data": []})
    records = {}
    for row in body.get("data") or []:
        if isinstance(row, dict) and row.get("type"):
            records[row["type"]] = records.get(row["type"], 0) + 1
    subdomains = body.get("subdomains") or []
    return {
        "subdomain_count": len(subdomains),
        "subdomains": sorted(subdomains)[:50],
        "record_types": records,
        "tags": body.get("tags") or [],
    }


@_guard
def censys_host(ip: str) -> dict:
    """Censys Platform: services, software and certificates seen on a host.

    CENSYS_API_KEY is a Platform personal access token, sent as a Bearer token.
    Free accounts may call this lookup (100 credits a month, one per lookup)
    and have no organisation ID; paid ones also set CENSYS_ORG_ID. The legacy
    Search API (API ID + secret) is at end of life and is not used.
    """
    headers = {"Authorization": f"Bearer {key('CENSYS_API_KEY')}"}
    org = key("CENSYS_ORG_ID")
    if org:
        headers["X-Organization-ID"] = org
    body = _request("Censys", "GET",
                    f"https://api.platform.censys.io/v3/global/asset/host/{ip}",
                    headers=headers, not_found={"_missing": True},
                    statuses={402: "has no credits left this month",
                              422: "needs CENSYS_ORG_ID for this account"})
    if body.get("_missing"):
        return {"found": False, "note": "Censys has no record of this address."}
    host = ((body.get("result") or {}).get("resource")) or {}
    services = []
    for svc in host.get("services") or []:
        if not isinstance(svc, dict):
            continue
        software = []
        for item in svc.get("software") or []:
            if isinstance(item, dict):
                name = " ".join(str(item[k]) for k in ("vendor", "product", "version")
                                if item.get(k))
                if name:
                    software.append(name)
        cert = svc.get("cert") or {}
        services.append({
            "port": svc.get("port"),
            "protocol": svc.get("protocol"),
            "transport": svc.get("transport_protocol"),
            "software": software[:5],
            "certificate_subject": ((cert.get("parsed") or {}).get("subject_dn")
                                    if isinstance(cert, dict) else None),
        })
    asys = host.get("autonomous_system") or {}
    loc = host.get("location") or {}
    dns = host.get("dns") or {}
    return {
        "found": True,
        "service_count": host.get("service_count", len(services)),
        "services": services[:25],
        "network_owner": asys.get("name") or asys.get("description"),
        "asn": asys.get("asn"),
        "country": loc.get("country"),
        "city": loc.get("city"),
        "hostnames": ((dns.get("reverse_dns") or {}).get("names") or dns.get("names") or [])[:15],
        "note": "What Censys' own scanners recorded. Sentinel does not scan the host.",
    }


# ── Domain intelligence ──────────────────────────────────────────────────────

@_guard
def securitytrails(domain: str) -> dict:
    """SecurityTrails: current DNS and how many subdomains it knows (one query)."""
    body = _request("SecurityTrails", "GET",
                    f"https://api.securitytrails.com/v1/domain/{domain}",
                    headers={"APIKEY": key("SECURITYTRAILS_API_KEY")},
                    not_found={"_missing": True})
    if body.get("_missing"):
        return {"found": False, "note": "SecurityTrails has no record of this domain."}
    current = body.get("current_dns") or {}
    dns = {}
    for rtype, block in current.items():
        values = (block or {}).get("values") or []
        shown = []
        for value in values[:10]:
            if not isinstance(value, dict):
                continue
            item = (value.get("ip") or value.get("ipv6") or value.get("hostname")
                    or value.get("nameserver") or value.get("value"))
            if item:
                org = value.get("ip_organization") or value.get("hostname_organization")
                shown.append(f"{item} ({org})" if org else str(item))
        if shown:
            dns[rtype.upper()] = shown
    return {
        "found": True,
        "subdomain_count": body.get("subdomain_count"),
        "current_dns": dns,
        "first_seen": {rtype.upper(): (block or {}).get("first_seen")
                       for rtype, block in current.items() if (block or {}).get("first_seen")},
        "alexa_rank": body.get("alexa_rank"),
        "apex_domain": body.get("apex_domain"),
    }


def _domaintools_auth(path: str) -> tuple[dict, dict, str]:
    """Headers, query params and the secret for one DomainTools call.

    With a username (DOMAINTOOLS_API_USERNAME, or the key saved as
    ``username:key``) the request is HMAC-signed, DomainTools' recommended
    scheme, so the key itself never travels. Without one, the key goes in the
    ``X-Api-Key`` header.
    """
    import hashlib
    import hmac
    from datetime import datetime, timezone

    raw = key("DOMAINTOOLS_API_KEY")
    username = key("DOMAINTOOLS_API_USERNAME")
    if not username and ":" in raw:
        username, raw = raw.split(":", 1)
        username, raw = username.strip(), raw.strip()
    if not username:
        return {"X-Api-Key": raw}, {}, raw
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature = hmac.new(raw.encode(), f"{username}{stamp}{path}".encode(),
                         hashlib.sha256).hexdigest()
    return {}, {"api_username": username, "timestamp": stamp,
                "signature": signature}, raw


@_guard
def domaintools(domain: str) -> dict:
    """DomainTools: the domain profile and the risk score, whichever your plan has.

    DomainTools sells these separately, so each is asked for and a 403 on one
    is reported as "not in your plan" without failing the other.
    """
    out: dict = {}
    calls = (("profile", f"/v1/{domain}/", {}),
             ("risk", "/v1/risk/", {"domain": domain}))
    for name, path, params in calls:
        headers, auth, secret = _domaintools_auth(path)
        try:
            body = _request("DomainTools", "GET", f"https://api.domaintools.com{path}",
                            headers=headers, params={**params, **auth}, secret=secret,
                            statuses={403: "refused: this product is not in your plan, "
                                           "or the key/username is wrong"})
        except _Failed as exc:
            out[name] = {"error": str(exc)}
            continue
        out[name] = body.get("response") or {}
    profile = out.get("profile") or {}
    risk = out.get("risk") or {}
    if profile.get("error") and risk.get("error"):
        raise _Failed(profile["error"])
    result: dict = {}
    if profile.get("error"):
        result["profile"] = profile
    else:
        registrant = profile.get("registrant") or {}
        registration = profile.get("registration") or {}
        server = profile.get("server") or {}
        result["profile"] = {
            "registrant": registrant.get("name"),
            "registrar": registration.get("registrar"),
            "created": registration.get("created"),
            "expires": registration.get("expires"),
            "name_servers": [ns.get("server") if isinstance(ns, dict) else ns
                             for ns in profile.get("name_servers") or []][:10],
            "hosting_ip": server.get("ip_address"),
            "other_domains_on_ip": server.get("other_domains"),
            "history": profile.get("history"),
        }
    if risk.get("error"):
        result["risk"] = risk
    else:
        result["risk"] = {
            "risk_score": risk.get("risk_score"),
            "components": risk.get("components"),
            "note": "0 to 100; DomainTools treats 70 and above as high risk.",
        }
    return result


@_guard
def urlscan_domain(domain: str) -> dict:
    """URLScan: the most recent public scans of pages on the domain."""
    body = _request("URLScan", "GET", "https://urlscan.io/api/v1/search/",
                    params={"q": f"domain:{domain}", "size": 50},
                    headers={"API-Key": key("URLSCAN_API_KEY")})
    results = body.get("results") or []
    ips, countries, servers, scans = set(), set(), set(), []
    for row in results:
        page = row.get("page") or {}
        task = row.get("task") or {}
        if page.get("ip"):
            ips.add(page["ip"])
        if page.get("country"):
            countries.add(page["country"])
        if page.get("server"):
            servers.add(page["server"])
        if len(scans) < 10:
            scans.append({"url": page.get("url") or task.get("url"),
                          "time": task.get("time"), "ip": page.get("ip"),
                          "result": row.get("result")})
    return {
        "total_scans": body.get("total", len(results)),
        "hosting_ips": sorted(ips)[:20],
        "countries": sorted(countries),
        "web_servers": sorted(servers)[:10],
        "recent_scans": scans,
    }


# Hunter's own meanings: 403 is the per-second throttle, 429 the monthly quota.
_HUNTER = {401: "rejected the key",
           403: "rate limit reached; try again in a moment",
           429: "monthly quota reached; it resets with your billing month",
           451: "declined: the person asked Hunter not to process their data"}


@_guard
def hunter_domain(domain: str) -> dict:
    """Hunter: the domain's email pattern, its role addresses, and counts only.

    Two calls. ``email-count`` is free and gives totals and the department
    split. ``domain-search`` is asked for generic addresses only (info@,
    security@), so no named person's address is ever requested.
    """
    headers = {"X-API-KEY": key("HUNTER_API_KEY")}
    counts = _request("Hunter", "GET", "https://api.hunter.io/v2/email-count",
                      params={"domain": domain}, headers=headers, statuses=_HUNTER)
    search = _request("Hunter", "GET", "https://api.hunter.io/v2/domain-search",
                      params={"domain": domain, "type": "generic", "limit": 10},
                      headers=headers, statuses=_HUNTER)
    count = counts.get("data") or {}
    data = search.get("data") or {}
    departments = {name: n for name, n in (count.get("department") or {}).items() if n}
    return {
        "organization": data.get("organization"),
        "email_pattern": data.get("pattern"),
        "accepts_all": data.get("accept_all"),
        "webmail_domain": data.get("webmail"),
        "disposable_domain": data.get("disposable"),
        "addresses_known": count.get("total"),
        "personal_addresses_known": count.get("personal_emails"),
        "role_addresses_known": count.get("generic_emails"),
        "departments": departments,
        "role_addresses": [
            {"address": e.get("value"), "confidence": e.get("confidence"),
             "sources": len(e.get("sources") or [])}
            # Filtered here as well as in the request: if Hunter ever ignored
            # ``type=generic``, a named person's address still would not pass.
            for e in data.get("emails") or []
            if isinstance(e, dict) and e.get("type") == "generic"
        ],
        "note": ("Role addresses only. Hunter also knows named people's addresses; "
                 "Sentinel reports how many, never who."),
    }


@_guard
def hunter_verify(email: str) -> dict:
    """Hunter: can this address receive mail, and is it disposable or webmail?"""
    body = _request("Hunter", "GET", "https://api.hunter.io/v2/email-verifier",
                    params={"email": email},
                    headers={"X-API-KEY": key("HUNTER_API_KEY")},
                    ok=(200, 202, 222), statuses=_HUNTER)
    data = body.get("data") or {}
    if not data:
        return {"pending": True,
                "note": "Hunter is still checking the mail server; run it again in a minute."}
    return {
        # Hunter calls this "status"; renamed so it cannot be mistaken for the
        # source's own ok/skipped/error status in the email lookup.
        "verdict": data.get("status"),
        "result": data.get("result"),   # deprecated by Hunter; the verdict is the answer
        "score": data.get("score"),
        "disposable": data.get("disposable"),
        "webmail": data.get("webmail"),
        "gibberish": data.get("gibberish"),
        "mx_records": data.get("mx_records"),
        "smtp_server": data.get("smtp_server"),
        "smtp_check": data.get("smtp_check"),
        "accept_all": data.get("accept_all"),
        "blocked": data.get("block"),
        "note": ("verdict valid: the mail server accepts this address. accept_all: it "
                 "accepts every address, so this one cannot be confirmed."),
    }

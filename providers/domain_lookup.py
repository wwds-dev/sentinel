"""
Domain OSINT provider — WHOIS, DNS, network owner, passive DNS, certificate
transparency, web archive, and (for IPs) attack reports.

Zero-cost stack:
  • python-whois  → registrar, dates, nameservers, registrant org/country
  • dnspython     → A, AAAA, MX, NS, TXT, SOA records
  • Team Cymru    → IP-to-ASN: network owner, prefix, country — asked over DNS
                    (TXT records under asn.cymru.com), so no web request
  • Mnemonic PDNS → passive DNS: what a name resolved to over time, or which
                    names were seen on an IP (no key; rate-limited)
  • SANS DShield  → IP only: attack reports from the Internet Storm Center's
                    sensors, and the threat feeds that list the IP
  • Shodan InternetDB → IP only: the open ports, software and known CVEs Shodan
                    has already crawled for the address. Free, unauthenticated,
                    and passive — a lookup of Shodan's data, not a scan.
  • crt.sh JSON API → certificate transparency subdomain enumeration
  • Wayback Machine availability API → first and latest archived snapshot

Key-gated (set in .env), IP only:
  • IPinfo        → IPINFO_API_KEY — geolocation, network owner, and (on paid
                    plans) VPN/proxy/hosting/Tor flags. IPinfo refuses anonymous
                    API access, so this source is skipped unless a key is set.
  • Criminal IP   → CRIMINALIP_API_KEY — reputation score, VPN/proxy/Tor/hosting
                    flags, open ports and the network owner. Credit-metered; this
                    source is skipped unless a key is set.
"""

import ipaddress
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from urllib.parse import urlsplit

from services.runtime_paths import user_data_base

load_dotenv(user_data_base() / ".env", override=False)


# ── helpers ──────────────────────────────────────────────────────────────────

def _normalize(target: str) -> str:
    """Strip protocol, path, query, port from a domain or IP string."""
    value = target.strip().lower()
    bare = value.strip("[]")
    try:
        return str(ipaddress.ip_address(bare))
    except ValueError:
        pass
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return (parsed.hostname or bare).rstrip(".")


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _scalar(v) -> object:
    """Collapse whois list values to a single string, cap lists at 5 items."""
    if v is None:
        return None
    if isinstance(v, list):
        items = [str(x) for x in v if x]
        return items[:5] if len(items) > 1 else (items[0] if items else None)
    return str(v)


# ── individual data sources ───────────────────────────────────────────────────

def _whois(domain: str) -> dict:
    try:
        import whois  # python-whois
        w = whois.whois(domain)
        return {
            "registrar":       _scalar(w.registrar),
            "creation_date":   _scalar(w.creation_date),
            "expiration_date": _scalar(w.expiration_date),
            "updated_date":    _scalar(w.updated_date),
            "name_servers":    _scalar(w.name_servers),
            "status":          _scalar(w.status),
            "emails":          _scalar(w.emails),
            "org":             _scalar(w.org),
            "country":         _scalar(w.country),
        }
    except ImportError:
        return {"error": "python-whois not installed — run: pip install python-whois"}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _dns(domain: str) -> dict:
    try:
        import dns.resolver  # dnspython
        records: dict = {}
        for rtype in ("A", "AAAA", "MX", "NS", "TXT", "SOA"):
            try:
                ans = dns.resolver.resolve(domain, rtype, lifetime=5)
                records[rtype] = [str(r) for r in ans][:10]
            except Exception:
                pass
        return records or {"error": "no records resolved"}
    except ImportError:
        return {"error": "dnspython not installed — run: pip install dnspython"}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _crtsh(domain: str) -> dict:
    """Query crt.sh for certificate transparency records (subdomain discovery)."""
    try:
        resp = requests.get(
            "https://crt.sh/",
            params={"q": f"%.{domain}", "output": "json"},
            timeout=12,
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
        )
        if resp.status_code != 200:
            return {"error": f"crt.sh HTTP {resp.status_code}"}

        seen: set = set()
        names: list = []
        for entry in resp.json():
            for name in entry.get("name_value", "").split("\n"):
                name = name.strip().lstrip("*.")
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
        names.sort()
        return {"total_unique": len(names), "sample": names[:30]}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _snapshot(entry) -> dict | None:
    """Reduce an availability-API ``closest`` record to what the report needs."""
    if not isinstance(entry, dict) or not entry.get("available"):
        return None
    stamp = str(entry.get("timestamp") or "")
    date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) >= 8 else None
    return {"date": date, "timestamp": stamp or None, "url": entry.get("url")}


def _wayback(domain: str) -> dict:
    """First and latest Wayback Machine snapshot of a domain.

    The availability API returns the capture closest to a timestamp, so asking
    for 1996 gives the earliest capture and asking with none gives the latest.
    The CDX search API would give full capture counts but routinely takes
    longer than 30 s, which is too slow for an interactive lookup.
    """
    try:
        found: dict = {}
        for key, params in (("first_snapshot", {"timestamp": "19960101"}),
                            ("latest_snapshot", {})):
            resp = requests.get(
                "https://archive.org/wayback/available",
                params={"url": domain, **params},
                timeout=12,
                headers={"User-Agent": "Sentinel-OSINT/2.0"},
            )
            if resp.status_code != 200:
                return {"error": f"Wayback Machine HTTP {resp.status_code}"}
            closest = (resp.json().get("archived_snapshots") or {}).get("closest")
            found[key] = _snapshot(closest)
        archived = bool(found["first_snapshot"] or found["latest_snapshot"])
        return {
            "archived": archived,
            **found,
            "all_captures": f"https://web.archive.org/web/*/{domain}",
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _cymru_origin_name(ip: str) -> str:
    """The Team Cymru DNS name that answers "which network announces this IP"."""
    address = ipaddress.ip_address(ip)
    if address.version == 4:
        return ".".join(reversed(ip.split("."))) + ".origin.asn.cymru.com"
    nibbles = address.exploded.replace(":", "")
    return ".".join(reversed(nibbles)) + ".origin6.asn.cymru.com"


def _txt_fields(name: str) -> list[str] | None:
    import dns.resolver

    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None
    text = b"".join(next(iter(answer)).strings).decode("utf-8", "replace")
    return [part.strip() for part in text.split("|")]


def _asn(target: str) -> dict:
    """Network owner of an IP, or of the first IPv4 address a domain resolves to."""
    try:
        import dns.resolver  # dnspython

        ip = target
        if not _is_ip(target):
            try:
                ip = str(next(iter(dns.resolver.resolve(target, "A", lifetime=5))))
            except Exception:
                return {"error": "the domain has no IPv4 address to look up"}
        origin = _txt_fields(_cymru_origin_name(ip))
        if origin is None:
            return {"ip": ip, "announced": False,
                    "note": "No network announces this address (private, reserved, or unrouted)."}
        asn = origin[0].split()[0]
        record = {
            "ip": ip, "announced": True, "asn": f"AS{asn}",
            "prefix": origin[1] if len(origin) > 1 else None,
            "country": origin[2] if len(origin) > 2 else None,
            "registry": origin[3] if len(origin) > 3 else None,
            "allocated": origin[4] if len(origin) > 4 else None,
        }
        described = _txt_fields(f"AS{asn}.asn.cymru.com")
        if described and len(described) > 4:
            record["as_name"] = described[4]
        return record
    except ImportError:
        return {"error": "dnspython not installed — run: pip install dnspython"}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _day(epoch_ms) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _passive_dns(target: str) -> dict:
    """Mnemonic passive DNS: a name's past answers, or the names seen on an IP."""
    try:
        resp = requests.get(
            f"https://api.mnemonic.no/pdns/v3/{target}",
            params={"limit": 25},
            timeout=15,
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
        )
        if resp.status_code == 429:
            return {"error": "Mnemonic passive DNS rate limit reached; try again later"}
        if resp.status_code != 200:
            return {"error": f"Mnemonic passive DNS HTTP {resp.status_code}"}
        data = resp.json()
        rows = data.get("data") or []
        # Busy IPs come back as partial results with the seen-timestamps
        # zeroed; the record's own created/updated times are the next best.
        records = [{
            "name": row.get("query"),
            "type": (row.get("rrtype") or "").upper(),
            "answer": row.get("answer"),
            "first_seen": _day(row.get("firstSeenTimestamp") or row.get("createdTimestamp")),
            "last_seen": _day(row.get("lastSeenTimestamp") or row.get("lastUpdatedTimestamp")),
            "times_seen": row.get("times"),
        } for row in rows]
        result = {
            "total_records": data.get("count", len(records)),
            "shown": len(records),
            "records": records,
            "note": ("Names seen resolving to this IP." if _is_ip(target)
                     else "Answers this name has returned over time."),
        }
        if any("partialResult" in (row.get("flags") or []) for row in rows):
            result["partial"] = ("Mnemonic returned a partial result (a busy address); "
                                 "dates are when its records were created and updated.")
        return result
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _dshield(ip: str) -> dict:
    """SANS Internet Storm Center: has this IP attacked its sensors?"""
    try:
        resp = requests.get(
            f"https://isc.sans.edu/api/ip/{ip}",
            params={"json": ""},
            timeout=15,
            headers={"User-Agent": "Sentinel-OSINT/2.0 (desktop research tool)"},
        )
        if resp.status_code != 200:
            return {"error": f"DShield HTTP {resp.status_code}"}
        info = resp.json().get("ip") or {}
        feeds = info.get("threatfeeds") or {}
        ssh = info.get("ssh") or {}
        weblogs = info.get("weblogs") or {}
        return {
            "reports": info.get("count") or 0,
            "targets_attacked": info.get("attacks") or 0,
            "first_reported": info.get("mindate"),
            "last_reported": info.get("maxdate"),
            "threat_feeds": [
                {"feed": name, "first_seen": seen.get("firstseen"),
                 "last_seen": seen.get("lastseen")}
                for name, seen in feeds.items() if isinstance(seen, dict)
            ],
            "ssh_brute_force": {
                "attempts": ssh.get("attempts"), "first": ssh.get("start"),
                "last": ssh.get("end"),
            } if ssh else None,
            "web_attacks": {
                "requests": weblogs.get("count"), "first": weblogs.get("firstseen"),
                "last": weblogs.get("lastseen"),
            } if weblogs else None,
            "network": info.get("network") or None,
            "as_name": info.get("asname"),
            "abuse_contact": info.get("asabusecontact"),
            "comment": info.get("comment"),
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _shodan_internetdb(ip: str) -> dict:
    """Shodan InternetDB: open ports, detected software and known CVEs for an IP.

    Free and unauthenticated. It only returns what Shodan already crawled, so it
    is passive — Sentinel never scans the target. A 404 means Shodan holds
    nothing on the address, which is a clean result and not an error.
    """
    try:
        resp = requests.get(
            f"https://internetdb.shodan.io/{ip}",
            timeout=12,
            headers={"User-Agent": "Sentinel-OSINT/2.0"},
        )
        if resp.status_code == 404:
            return {"found": False, "note": "Shodan has no record of this address."}
        if resp.status_code != 200:
            return {"error": f"Shodan InternetDB HTTP {resp.status_code}"}
        data = resp.json()
        return {
            "found": True,
            "ports": data.get("ports") or [],
            "hostnames": data.get("hostnames") or [],
            "software": data.get("cpes") or [],
            "tags": data.get("tags") or [],
            "vulnerabilities": data.get("vulns") or [],
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _ipinfo(ip: str) -> dict:
    """IPinfo: geolocation, network owner and (paid plans) VPN/proxy flags.

    Reads IPINFO_API_KEY at call time and self-skips without one, so a direct
    call never performs an anonymous, unkeyed OSINT request. The token is sent
    in the Authorization header, never the query string, so a transport error's
    message cannot leak it. The response carries city/region/country and the org
    (ASN + name); the ``privacy`` object with the VPN/proxy/Tor/hosting flags is
    a paid feature, surfaced only when present.
    """
    key = os.getenv("IPINFO_API_KEY", "").strip()
    if not key:
        return {"error": "IPinfo needs an IPINFO_API_KEY (anonymous access is refused)"}
    try:
        resp = requests.get(
            f"https://ipinfo.io/{ip}/json",
            timeout=12,
            headers={"User-Agent": "Sentinel-OSINT/2.0",
                     "Authorization": f"Bearer {key}"},
        )
        if resp.status_code == 429:
            return {"error": "IPinfo rate limit reached; try again later"}
        if resp.status_code in (401, 403):
            return {"error": "IPinfo rejected the key"}
        if resp.status_code != 200:
            return {"error": f"IPinfo HTTP {resp.status_code}"}
        data = resp.json()
        if data.get("bogon"):
            return {"bogon": True, "note": "Private, reserved or unrouted address."}
        result = {
            "city": data.get("city") or None,
            "region": data.get("region") or None,
            "country": data.get("country") or None,
            "org": data.get("org") or None,
            "hostname": data.get("hostname") or None,
            "timezone": data.get("timezone") or None,
        }
        privacy = data.get("privacy")
        if isinstance(privacy, dict):
            result["privacy_flags"] = [
                name for name in ("vpn", "proxy", "tor", "relay", "hosting")
                if privacy.get(name)
            ]
        return result
    except Exception as exc:
        return {"error": str(exc)[:300]}


def _criminalip(ip: str) -> dict:
    """Criminal IP: reputation score, VPN/proxy/Tor/hosting flags and open ports.

    Reads CRIMINALIP_API_KEY at call time and uses the lightweight
    ``/asset/ip/report/summary`` endpoint. ``lookup`` only reaches here when a
    key is set. The ``score`` fields are risk categories
    (Safe/Low/Moderate/Dangerous/Critical), not numbers, and the ``issues`` flags
    (VPN/proxy/Tor/hosting) are infrastructure signals — context, not proof of
    wrongdoing. The response carries its own integer ``status``; a non-200 there,
    or an HTTP 401/403, means the call did not succeed.
    """
    key = os.getenv("CRIMINALIP_API_KEY", "").strip()
    if not key:
        return {"error": "Criminal IP needs a CRIMINALIP_API_KEY"}
    try:
        resp = requests.get(
            "https://api.criminalip.io/v1/asset/ip/report/summary",
            params={"ip": ip},
            timeout=15,
            headers={"x-api-key": key, "User-Agent": "Sentinel-OSINT/2.0"},
        )
        if resp.status_code in (401, 403):
            return {"error": "Criminal IP rejected the key"}
        if resp.status_code == 429:
            return {"error": "Criminal IP rate/credit limit reached; try again later"}
        if resp.status_code != 200:
            return {"error": f"Criminal IP HTTP {resp.status_code}"}
        data = resp.json()
        body_status = data.get("status")
        if body_status not in (200, None):
            if body_status in (401, 403):
                return {"error": "Criminal IP rejected the key"}
            return {"error": f"Criminal IP returned status {body_status}"}
        score = data.get("score") or {}
        issues = data.get("issues") or {}
        flags = [name[len("is_"):] for name in (
            "is_vpn", "is_proxy", "is_tor", "is_hosting", "is_cloud",
            "is_anonymous_vpn", "is_darkweb", "is_scanner") if issues.get(name)]
        whois_rows = ((data.get("whois") or {}).get("data")) or []
        whois = whois_rows[0] if isinstance(whois_rows, list) and whois_rows else {}
        ports_obj = data.get("current_opened_port") or data.get("port") or {}
        port_rows = ports_obj.get("data") or [] if isinstance(ports_obj, dict) else []
        ports = sorted({
            row.get("open_port") or row.get("port")
            for row in port_rows
            if isinstance(row, dict) and (row.get("open_port") or row.get("port"))
        })
        return {
            "risk_inbound": score.get("inbound"),
            "risk_outbound": score.get("outbound"),
            "flags": flags,
            "as_name": whois.get("as_name") or whois.get("org_name"),
            "org": whois.get("org_name"),
            "country": whois.get("org_country_code"),
            "open_ports": ports[:30],
            "note": ("Reputation score and infrastructure flags from Criminal IP. "
                     "VPN/proxy/Tor/hosting are context, not proof of wrongdoing."),
        }
    except requests.exceptions.Timeout:
        return {"error": "Criminal IP did not respond within 15 seconds."}
    except Exception as exc:
        return {"error": str(exc)[:300]}


# ── public interface ──────────────────────────────────────────────────────────

def lookup(domain: str, *, on_progress=None, should_stop=None) -> dict:
    """
    Return a normalised OSINT dict for a domain or IP address.

    Keys:
      type, query, whois, dns, network, passive_dns, certificates, archive   (domain)
      type, query, whois, dns, network, attack_reports, host_exposure,
        ip_details, ip_reputation, passive_dns                               (IP)
    """
    target = _normalize(domain)
    is_ip = _is_ip(target)
    result: dict = {
        "type": "ip" if is_ip else "domain",
        "query": target,
        "sources_contacted": [],
    }

    sources = [
        ("WHOIS", "whois", _whois),
        ("DNS", "dns", _dns),
        ("Team Cymru IP-to-ASN", "network", _asn),
    ]
    if is_ip:
        sources.append(("SANS DShield", "attack_reports", _dshield))
        sources.append(("Shodan InternetDB", "host_exposure", _shodan_internetdb))
        # IPinfo refuses anonymous access and Criminal IP is credit-metered, so
        # both are only worth contacting when a key is set. Unlike the exposure
        # provider (where key-gated sources run and report a "skipped" status),
        # domain_lookup has no sources_skipped list, so a keyless source is simply
        # omitted from the run rather than listed as skipped.
        if os.getenv("IPINFO_API_KEY", "").strip():
            sources.append(("IPinfo", "ip_details", _ipinfo))
        if os.getenv("CRIMINALIP_API_KEY", "").strip():
            sources.append(("Criminal IP", "ip_reputation", _criminalip))
    sources.append(("Mnemonic passive DNS", "passive_dns", _passive_dns))
    if not is_ip:
        sources.append(("Certificate transparency (crt.sh)", "certificates", _crtsh))
        sources.append(("Wayback Machine", "archive", _wayback))

    for label, key, source_lookup in sources:
        if should_stop and should_stop():
            result["cancelled"] = True
            break
        if on_progress:
            on_progress(label, "checking")
        source_result = source_lookup(target)
        result[key] = source_result
        status = "error" if isinstance(source_result, dict) and source_result.get("error") else "checked"
        result["sources_contacted"].append({"source": label, "status": status})
        if on_progress:
            on_progress(label, status)

    return result

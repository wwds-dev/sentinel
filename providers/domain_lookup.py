"""
Domain OSINT provider — WHOIS, DNS, and certificate transparency.

Zero-cost stack:
  • python-whois  → registrar, dates, nameservers, registrant org/country
  • dnspython     → A, AAAA, MX, NS, TXT, SOA records
  • crt.sh JSON API → certificate transparency subdomain enumeration
  • Wayback Machine availability API → first and latest archived snapshot
"""

import ipaddress
import requests
from urllib.parse import urlsplit


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


# ── public interface ──────────────────────────────────────────────────────────

def lookup(domain: str, *, on_progress=None, should_stop=None) -> dict:
    """
    Return a normalised OSINT dict for a domain or IP address.

    Keys:
      type, query, whois, dns, certificates, archive   (domain)
      type, query, whois, dns                          (IP — crt.sh and Wayback skipped)
    """
    target = _normalize(domain)
    is_ip = _is_ip(target)
    result: dict = {
        "type": "ip" if is_ip else "domain",
        "query": target,
        "sources_contacted": [],
    }

    sources = [("WHOIS", "whois", _whois), ("DNS", "dns", _dns)]
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

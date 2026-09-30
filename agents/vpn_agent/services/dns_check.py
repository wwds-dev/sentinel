"""
dns_check.py — Checks which DNS resolvers are currently answering queries.

``check_dns_leak`` is a quick local heuristic (it only inspects the resolver
list the system is configured with). ``run_dns_leak_test`` is the real thing: it
exercises the actual egress resolver path through bash.ws and reports the DNS
servers that traffic genuinely used, plus the public exit IP.
"""

import socket

import dns.resolver  # dnspython library
import requests

# Test domain — known to return different results depending on resolver
DNS_TEST_DOMAIN = "whoami.akamai.net"

BASHWS_BASE = "https://bash.ws"
_UA = "Sentinel-OSINT/2.0"

# Per-probe resolution time limit, in seconds.
PROBE_TIMEOUT = 5.0


def _bounded_resolve(hostname: str) -> None:
    """Resolve one probe name with a per-query time limit and no process-wide
    state change. dnspython's ``lifetime`` bounds a single hung lookup without
    the global ``socket.setdefaulttimeout`` mutation (which would race with any
    concurrent socket work in other threads). The answer is discarded — bash.ws
    only needs the configured resolvers to have asked."""
    try:
        dns.resolver.resolve(hostname, "A", lifetime=PROBE_TIMEOUT)
    except Exception:
        # NXDOMAIN / timeout / no-answer are all expected: the query was sent,
        # which is the whole point of the probe.
        pass

# Public resolvers to verify against
KNOWN_PUBLIC_RESOLVERS = {
    "8.8.8.8": "Google",
    "8.8.4.4": "Google",
    "1.1.1.1": "Cloudflare",
    "1.0.0.1": "Cloudflare",
    "9.9.9.9": "Quad9",
    "208.67.222.222": "OpenDNS",
}


def get_system_dns_servers() -> list[str]:
    """
    Return the list of DNS servers currently configured on the system.
    Uses socket to resolve a domain and captures what the OS resolves with.
    Note: this is a simplified check — for deeper inspection, parse /etc/resolv.conf
    or use scutil --dns on macOS.
    """
    try:
        resolver = dns.resolver.Resolver()
        # dnspython reads /etc/resolv.conf and system config automatically
        return list(resolver.nameservers)
    except Exception as e:
        return [f"Error: {e}"]


def check_dns_leak() -> dict:
    """
    Perform a basic DNS leak check.
    Returns a dict with:
      - servers: list of detected DNS server IPs
      - labels: mapped names for known servers
      - leak_risk: True if any server is NOT in known_public_resolvers (unusual resolver)
      - status: human-readable summary string
    """
    servers = get_system_dns_servers()
    labels = []
    unknown_count = 0

    for server in servers:
        if server in KNOWN_PUBLIC_RESOLVERS:
            labels.append(f"{server} ({KNOWN_PUBLIC_RESOLVERS[server]})")
        else:
            labels.append(f"{server} (Unknown)")
            unknown_count += 1

    # Leak risk is heuristic: if all resolvers are unknown, something may be off
    # When VPN is active, the VPN provider's resolver should appear
    leak_risk = unknown_count == len(servers) and len(servers) > 0

    if leak_risk:
        status = "Possible DNS leak — unknown resolvers only"
    elif unknown_count > 0:
        status = "Mixed resolvers — check manually"
    else:
        status = "DNS OK — known public resolvers"

    return {
        "servers": servers,
        "labels": labels,
        "leak_risk": leak_risk,
        "status": status,
    }


def resolve_test_domain() -> str:
    """
    Attempt to resolve a test domain to verify DNS is working at all.
    Returns the resolved IP or an error string.
    """
    try:
        ip = socket.gethostbyname(DNS_TEST_DOMAIN)
        return ip
    except Exception as e:
        return f"Failed: {e}"


def run_dns_leak_test(*, probe_count: int = 12, should_stop=None,
                      resolve=None, session_get=None) -> dict:
    """Run a real egress DNS-leak test via bash.ws (no API key required).

    Unlike ``check_dns_leak`` (which only inspects the configured resolver list),
    this exercises the actual resolver path:

      1. ask bash.ws for a one-off test id;
      2. resolve ``{i}.{id}.bash.ws`` for i in 1..N through the system's real
         resolver stack, so bash.ws's authoritative nameservers record every
         resolver that looked the names up;
      3. read back the observed resolvers and the public egress IP.

    Everything returned is the user's own network information; it is shown back to
    them and sent nowhere but bash.ws. ``resolve`` and ``session_get`` are
    injectable for testing (defaults: ``socket.gethostbyname`` and
    ``requests.get``).

    Returns a dict: ``status`` ("ok"/"error"/"cancelled"), ``public_ip``,
    ``resolvers`` (list of {ip, country, asn}), ``resolver_count``,
    ``distinct_asns``, ``leak`` (bool or None), ``conclusion`` and ``note``.
    """
    resolve = resolve or _bounded_resolve
    get = session_get or requests.get
    headers = {"User-Agent": _UA}
    probe_count = max(1, min(int(probe_count), 30))

    # Step 1 — obtain the opaque test id.
    try:
        id_resp = get(f"{BASHWS_BASE}/id", timeout=15, headers=headers)
        if getattr(id_resp, "status_code", 200) != 200:
            return {"status": "error",
                    "detail": f"bash.ws did not issue a test id (HTTP {id_resp.status_code})"}
        test_id = (id_resp.text or "").strip()
        if not test_id or len(test_id) > 64 or not test_id.isalnum():
            return {"status": "error", "detail": "bash.ws returned an unexpected test id"}
    except Exception as exc:
        return {"status": "error", "detail": f"could not reach bash.ws: {str(exc)[:200]}"}

    # Step 2 — force the configured resolvers to query bash.ws. Each lookup may
    # fail (NXDOMAIN/timeout); the server-side record of who asked is the point.
    # Each resolution is bounded by the resolver's own per-query timeout (see
    # _bounded_resolve), so a single hung lookup cannot wedge the sweep and no
    # process-wide socket state is touched. Cancellation is checked between the
    # (blocking) resolutions.
    for index in range(1, probe_count + 1):
        if should_stop and should_stop():
            return {"status": "cancelled"}
        try:
            resolve(f"{index}.{test_id}.bash.ws")
        except Exception:
            pass

    # Step 3 — read the aggregated result. The body is a JSON array on success,
    # or a JSON object {"error": ...} when no resolvers were observed.
    try:
        res = get(f"{BASHWS_BASE}/dnsleak/test/{test_id}", params={"json": ""},
                  timeout=15, headers=headers)
        if getattr(res, "status_code", 200) != 200:
            return {"status": "error", "detail": f"bash.ws result HTTP {res.status_code}"}
        payload = res.json()
    except Exception as exc:
        return {"status": "error", "detail": f"could not read bash.ws result: {str(exc)[:200]}"}

    if isinstance(payload, dict):
        return {"status": "ok", "public_ip": None, "resolvers": [], "resolver_count": 0,
                "distinct_asns": 0, "leak": None,
                "conclusion": payload.get("error") or "No resolvers were observed.",
                "note": ("bash.ws saw no resolver queries for this test — the probe "
                         "lookups may have been blocked. Try again.")}
    if not isinstance(payload, list):
        return {"status": "error", "detail": "bash.ws returned an unexpected result shape"}

    public_ip = None
    resolvers: list[dict] = []
    conclusion = None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "ip":
            public_ip = {"ip": entry.get("ip"), "country": entry.get("country_name"),
                         "asn": entry.get("asn")}
        elif etype == "dns":
            resolvers.append({"ip": entry.get("ip"), "country": entry.get("country_name"),
                              "asn": entry.get("asn")})
        elif etype == "conclusion":
            # bash.ws carries the human-readable verdict in the entry's ip field.
            conclusion = entry.get("ip")

    distinct_asns = len({r["asn"] for r in resolvers if r.get("asn")})

    # Prefer bash.ws's own verdict; otherwise flag a leak when a resolver sits on
    # a different network (ASN) than the exit IP.
    leak = None
    if conclusion:
        lowered = conclusion.lower()
        # Check the negative phrasings first and broadly: a verdict like
        # "No DNS leak found." must read as no-leak, not match the bare "leak".
        if any(neg in lowered for neg in
               ("not leaking", "no leak", "no dns leak", "not leak", "no leaks")):
            leak = False
        elif "leak" in lowered:
            leak = True
    if leak is None and public_ip and resolvers:
        exit_asn = public_ip.get("asn")
        # Only compare networks when the exit IP's ASN is known. Without it,
        # every resolver with an ASN would spuriously "differ" and flag a leak;
        # leave the verdict unknown ("review resolvers") instead.
        if exit_asn:
            leak = any(r.get("asn") and r["asn"] != exit_asn for r in resolvers)

    return {
        "status": "ok",
        "public_ip": public_ip,
        "resolvers": resolvers,
        "resolver_count": len(resolvers),
        "distinct_asns": distinct_asns,
        "leak": leak,
        "conclusion": conclusion or "",
        "note": ("The DNS servers your traffic actually used, as seen by bash.ws. "
                 "Resolvers on a different network than your exit IP can indicate a leak."),
    }

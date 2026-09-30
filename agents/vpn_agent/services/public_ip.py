"""
public_ip.py — the operator's own IP, inside and out.

External (public) IP comes from ipify; the geo/owner detail comes from IPinfo
when an ``IPINFO_API_KEY`` is set (it also carries the VPN/proxy/hosting flags on
paid plans) and falls back to ipapi.co otherwise. Local addresses are read from
``ifconfig`` with no network contact at all, so the panel can show the LAN and
tunnel interfaces the moment it opens and only reach out for the public IP when
the operator asks.
"""

import os
import re
import socket
import subprocess

import requests
from dotenv import load_dotenv

from services.runtime_paths import user_data_base

load_dotenv(user_data_base() / ".env", override=False)

# Primary endpoint: fast, returns JSON with just the IP
IP_SIMPLE_URL = "https://api.ipify.org?format=json"

# Detail endpoints: IPinfo when a key is present (adds privacy flags), else ipapi.co
IPINFO_SELF_URL = "https://ipinfo.io/json"
IPAPI_URL = "https://ipapi.co/json/"

# Timeout in seconds for all HTTP requests.
# Kept short so threads finish quickly when the app closes.
REQUEST_TIMEOUT = 4

# Interface name prefixes that denote a VPN/point-to-point tunnel rather than a
# physical or bridged LAN interface. WireGuard on macOS appears as utun*.
_TUNNEL_PREFIXES = ("utun", "wg", "tun", "tap", "ppp", "ipsec", "gif")


def get_public_ip() -> str:
    """Return the current public IP as a string, or an error message."""
    try:
        response = requests.get(IP_SIMPLE_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data.get("ip", "Unknown")
    except requests.exceptions.ConnectionError:
        return "No connection"
    except requests.exceptions.Timeout:
        return "Timeout"
    except Exception as e:
        return f"Error: {e}"


def get_ip_details() -> dict:
    """Return details about the caller's own public IP.

    Keys: ip, country, city, org, timezone, and — from IPinfo's paid privacy
    object when available — privacy_flags (e.g. ["vpn", "hosting"]). Uses IPinfo
    when IPINFO_API_KEY is set, otherwise ipapi.co. Returns partial data with the
    error on ``ip`` if the lookup fails.
    """
    result = {
        "ip": "Unknown",
        "country": "Unknown",
        "city": "Unknown",
        "org": "Unknown",
        "timezone": "Unknown",
    }
    key = os.getenv("IPINFO_API_KEY", "").strip()
    try:
        if key:
            # Send the token in the Authorization header, never the query
            # string: a transport error's message embeds the request URL, and
            # result["ip"] = f"Error: {e}" would then leak the key to the panel
            # and the run log.
            response = requests.get(
                IPINFO_SELF_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            result["ip"] = data.get("ip", "Unknown")
            result["country"] = data.get("country", "Unknown")
            result["city"] = data.get("city", "Unknown")
            result["org"] = data.get("org", "Unknown")
            result["timezone"] = data.get("timezone", "Unknown")
            privacy = data.get("privacy")
            if isinstance(privacy, dict):
                result["privacy_flags"] = [
                    name for name in ("vpn", "proxy", "tor", "relay", "hosting")
                    if privacy.get(name)
                ]
        else:
            response = requests.get(IPAPI_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            result["ip"] = data.get("ip", "Unknown")
            result["country"] = data.get("country_name", "Unknown")
            result["city"] = data.get("city", "Unknown")
            result["org"] = data.get("org", "Unknown")
            result["timezone"] = data.get("timezone", "Unknown")
    except Exception as e:
        result["ip"] = f"Error: {e}"
    return result


def _primary_outbound_ip() -> str | None:
    """The LAN address the OS would use to reach the internet.

    A UDP socket is "connected" to a public address, which only fixes the local
    routing choice — no packet is sent — and its local address is read back.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def looks_like_tunnel(interface: str) -> bool:
    return interface.split(":")[0].startswith(_TUNNEL_PREFIXES)


def parse_ifconfig(text: str) -> dict[str, list[str]]:
    """Map each interface to its IPv4 addresses, from ``ifconfig`` output.

    Loopback (127.0.0.1) and link-local (169.254.x) addresses are dropped; an
    interface with no usable IPv4 address is left out entirely.
    """
    interfaces: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            current = line.split(":", 1)[0].strip()
            continue
        if current is None:
            continue
        match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", line)
        if not match:
            continue
        address = match.group(1)
        if address.startswith(("127.", "169.254.")):
            continue
        interfaces.setdefault(current, []).append(address)
    return interfaces


def get_local_addresses() -> dict:
    """Local IPv4 addresses per interface, and which look like VPN tunnels.

    Purely local — reads ``ifconfig`` and a routing-table hint, never the
    network. Returns {primary, interfaces, tunnels}; ``primary`` is the LAN
    address used for outbound traffic and ``tunnels`` lists the tunnel interfaces
    that currently hold an address (empty when no tunnel is up).
    """
    try:
        proc = subprocess.run(
            ["ifconfig"], capture_output=True, text=True, timeout=5, check=False
        )
        interfaces = parse_ifconfig(proc.stdout)
    except (OSError, subprocess.TimeoutExpired):
        interfaces = {}
    tunnels = sorted(name for name in interfaces if looks_like_tunnel(name))
    return {
        "primary": _primary_outbound_ip(),
        "interfaces": interfaces,
        "tunnels": tunnels,
    }


def get_ip_snapshot(*, include_public: bool = True) -> dict:
    """A combined view for the panel: local addresses plus, when asked, the
    public IP and its detail. ``public`` is None when ``include_public`` is
    False, so the local half can render without any network contact.
    """
    return {
        "local": get_local_addresses(),
        "public": get_ip_details() if include_public else None,
    }

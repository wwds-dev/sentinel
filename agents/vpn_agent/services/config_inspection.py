"""Private-key-free WireGuard configuration inspection.

The parser intentionally returns a small summary rather than a parsed copy of
the file.  PrivateKey and PresharedKey values are counted and discarded at the
line boundary, so callers cannot accidentally render or log them.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re


MAX_CONFIG_BYTES = 1024 * 1024
_SECRET_FIELDS = {"privatekey", "presharedkey"}
_SAFE_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


@dataclass(frozen=True)
class WireGuardConfigSummary:
    source_name: str
    interface_name: str
    addresses: tuple[str, ...]
    dns_servers: tuple[str, ...]
    mtu: str
    peer_count: int
    endpoints: tuple[str, ...]
    allowed_ips: tuple[str, ...]
    route_mode: str
    secrets_discarded: int
    invalid_endpoint_count: int
    warnings: tuple[str, ...]


def _items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _endpoint_is_valid(value: str) -> bool:
    value = value.strip()
    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)]:(\d+)", value)
        if not match:
            return False
        try:
            ipaddress.IPv6Address(match.group(1).split("%", 1)[0])
        except ValueError:
            return False
        port_text = match.group(2)
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator or not host or any(char.isspace() for char in host):
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _SAFE_HOST.fullmatch(host):
                return False
    try:
        port = int(port_text)
    except ValueError:
        return False
    return 1 <= port <= 65535


def _route_mode(allowed_ips: list[str]) -> str:
    networks = set(allowed_ips)
    if "0.0.0.0/0" in networks or "::/0" in networks:
        return "Full tunnel"
    if networks:
        return "Split tunnel"
    return "Not specified"


def inspect_wireguard_config(path: str | Path) -> WireGuardConfigSummary:
    """Read one selected config and return only non-secret routing metadata."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("The selected WireGuard configuration is not a readable file.")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ValueError("The selected WireGuard configuration cannot be inspected.") from exc
    if size > MAX_CONFIG_BYTES:
        raise ValueError("The selected WireGuard configuration is larger than 1 MiB.")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("The selected WireGuard configuration is not readable UTF-8 text.") from exc
    return inspect_wireguard_text(text, source)


def inspect_wireguard_text(text: str, source: str | Path) -> WireGuardConfigSummary:
    """Summarise config text already in memory.

    The review hashes and inspects the same bytes through this entry point, so
    the SHA-256 it shows describes exactly what was inspected.
    """
    source = Path(source)
    section = ""
    addresses: list[str] = []
    dns_servers: list[str] = []
    endpoints: list[str] = []
    allowed_ips: list[str] = []
    mtu = ""
    peer_count = 0
    secrets_discarded = 0
    warnings: list[str] = []

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section == "peer":
                peer_count += 1
            elif section not in {"interface", "peer"}:
                warnings.append(f"Line {line_number}: unsupported section was ignored.")
            continue
        key, separator, value = line.partition("=")
        if not separator:
            warnings.append(f"Line {line_number}: entry has no '=' and was ignored.")
            continue
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        value = value.strip()
        if normalized_key in _SECRET_FIELDS:
            secrets_discarded += 1
            continue
        if section == "interface":
            if normalized_key == "address":
                addresses.extend(_items(value))
            elif normalized_key == "dns":
                dns_servers.extend(_items(value))
            elif normalized_key == "mtu":
                mtu = value
        elif section == "peer":
            if normalized_key == "endpoint":
                endpoints.append(value)
            elif normalized_key == "allowedips":
                allowed_ips.extend(_items(value))

    for address in addresses:
        try:
            ipaddress.ip_interface(address)
        except ValueError:
            warnings.append("An interface address is not valid IP/CIDR notation.")
    for network in allowed_ips:
        try:
            ipaddress.ip_network(network, strict=False)
        except ValueError:
            warnings.append("An AllowedIPs entry is not valid CIDR notation.")
    invalid_endpoints = sum(not _endpoint_is_valid(value) for value in endpoints)
    if invalid_endpoints:
        warnings.append(f"{invalid_endpoints} peer endpoint value(s) need attention.")
    if peer_count == 0:
        warnings.append("No [Peer] section was found.")

    return WireGuardConfigSummary(
        source_name=source.name,
        interface_name=source.stem,
        addresses=tuple(addresses),
        dns_servers=tuple(dns_servers),
        mtu=mtu,
        peer_count=peer_count,
        endpoints=tuple(endpoints),
        allowed_ips=tuple(allowed_ips),
        route_mode=_route_mode(allowed_ips),
        secrets_discarded=secrets_discarded,
        invalid_endpoint_count=invalid_endpoints,
        warnings=tuple(warnings),
    )

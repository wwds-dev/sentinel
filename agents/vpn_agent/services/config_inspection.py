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
    # DNS entries wg-quick passes on as search domains, not as servers.
    dns_search: tuple[str, ...] = ()


# The whitespace wg-quick's bash trims; str.strip() also removes U+00A0 and others.
_ASCII_SPACE = " \t\r\v\f"


def _items(value: str) -> list[str]:
    return [item.strip(_ASCII_SPACE) for item in value.split(",") if item.strip(_ASCII_SPACE)]


def _shell_items(value: str) -> list[str]:
    """wg-quick's ``${value//,/ }``, word-split by bash: commas and blanks."""
    return [item for item in re.split(r"[, \t]+", value) if item]


# wg-quick (darwin.bash) keeps a DNS entry as a server only when it matches
# (^[0-9.]+$)|(^.*:.*$); every other entry becomes a search domain.
def _is_dns_server(item: str) -> bool:
    return bool(re.fullmatch(r"[0-9.]+", item)) or ":" in item


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
    dns_search: list[str] = []
    endpoints: list[str] = []
    allowed_ips: list[str] = []
    mtu = ""
    peer_count = 0
    secrets_discarded = 0
    warnings: list[str] = []

    # Read lines as wg-quick does: split at "\n" only (splitlines() also splits
    # at U+2028 and friends, which would show an entry wg-quick never reads),
    # and drop everything from the first "#" before reading a key or a value,
    # so "DNS # = 10.64.0.1" sets no DNS server and is not shown as one.
    for line_number, raw_line in enumerate(text.split("\n"), 1):
        full = raw_line.strip(_ASCII_SPACE)
        line = full.split("#", 1)[0].strip(_ASCII_SPACE)
        if not line or line.startswith(";"):
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
            if "=" in full:
                warnings.append(f"Line {line_number}: the '=' comes after a '#', so "
                                "wg-quick reads no value from this entry; it was ignored.")
            else:
                warnings.append(f"Line {line_number}: entry has no '=' and was ignored.")
            continue
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        value = value.strip(_ASCII_SPACE)
        if normalized_key in _SECRET_FIELDS:
            secrets_discarded += 1
            continue
        if section == "interface":
            if normalized_key == "address":
                addresses.extend(_shell_items(value))
            elif normalized_key == "dns":
                for item in _shell_items(value):
                    (dns_servers if _is_dns_server(item) else dns_search).append(item)
                    if any(ch in item for ch in "*?["):
                        warnings.append(f"Line {line_number}: a DNS entry contains a "
                                        "wildcard, which wg-quick's shell may expand.")
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
        dns_search=tuple(dns_search),
        mtu=mtu,
        peer_count=peer_count,
        endpoints=tuple(endpoints),
        allowed_ips=tuple(allowed_ips),
        route_mode=_route_mode(allowed_ips),
        secrets_discarded=secrets_discarded,
        invalid_endpoint_count=invalid_endpoints,
        warnings=tuple(warnings),
    )

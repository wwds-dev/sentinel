"""
socks_client.py — A minimal SOCKS4a/SOCKS5 client, written here rather than
pulled in as a dependency.

Two things in this app need to speak SOCKS: the Tor integration, which must
verify it is really reaching the internet through Tor, and the proxy chain,
which must be testable before you trust it. Both need the same few hundred
bytes of protocol, and both need something the rest of the app does not get
from `requests`: the ability to chain hops, where each proxy is reached
*through* the previous one.

Hostnames are always resolved by the far end (SOCKS5 ATYP=domain, SOCKS4a),
never locally. Resolving here would send a DNS query straight to your ISP
naming the host you are about to visit — the exact leak the proxy exists to
prevent.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

SOCKS5 = "socks5"
SOCKS4 = "socks4"
HTTP = "http"
KINDS = (SOCKS5, SOCKS4, HTTP)

DEFAULT_TIMEOUT = 15


class SocksError(Exception):
    """A proxy refused, misbehaved, or could not be reached."""


# SOCKS5 reply codes, in the words the user needs rather than the RFC's.
_SOCKS5_ERRORS = {
    1: "general SOCKS server failure",
    2: "connection not allowed by ruleset",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused by destination",
    6: "TTL expired",
    7: "command not supported by this proxy",
    8: "address type not supported by this proxy",
}


@dataclass
class ProxyHop:
    """One proxy in a chain."""

    kind: str = SOCKS5
    host: str = "127.0.0.1"
    port: int = 1080
    username: str = ""
    password: str = ""
    label: str = ""

    def describe(self) -> str:
        name = self.label or f"{self.host}:{self.port}"
        return f"{name} ({self.kind})"

    def problems(self) -> list[str]:
        issues = []
        if self.kind not in KINDS:
            issues.append(f"unknown proxy type {self.kind!r}")
        if not self.host.strip():
            issues.append("no host")
        if not (1 <= self.port <= 65535):
            issues.append(f"port {self.port} out of range")
        if self.password and not self.username:
            issues.append("password given without a username")
        return issues

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "host": self.host, "port": self.port,
            "username": self.username, "password": self.password, "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyHop":
        known = {"kind", "host", "port", "username", "password", "label"}
        return cls(**{k: v for k, v in data.items() if k in known})


# ── Per-protocol negotiation ─────────────────────


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    """
    Read exactly count bytes.

    recv() may return short reads on a slow or congested link. Treating a short
    read as a complete reply is the classic way a SOCKS client works on a fast
    LAN and fails over a real network.
    """
    chunks = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise SocksError("proxy closed the connection mid-reply")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _negotiate_socks5(sock: socket.socket, hop: ProxyHop, host: str, port: int) -> None:
    wants_auth = bool(hop.username)
    methods = b"\x00\x02" if wants_auth else b"\x00"
    sock.sendall(bytes([0x05, len(methods)]) + methods)

    version, method = _recv_exactly(sock, 2)
    if version != 0x05:
        raise SocksError(f"not a SOCKS5 proxy (replied version {version})")
    if method == 0xFF:
        # Almost always the same cause: the proxy wants credentials, so we never
        # offered the method it needs. Saying that is more use than quoting the
        # RFC's "no acceptable methods".
        if not wants_auth:
            raise SocksError(
                "proxy rejected the connection without credentials — it likely "
                "demands a username and password"
            )
        raise SocksError("proxy rejected every authentication method offered")

    if method == 0x02:
        if not wants_auth:
            raise SocksError("proxy demands a username and password; none configured")
        user = hop.username.encode("utf-8")
        secret = hop.password.encode("utf-8")
        if len(user) > 255 or len(secret) > 255:
            raise SocksError("username or password longer than SOCKS5 allows (255 bytes)")
        sock.sendall(bytes([0x01, len(user)]) + user + bytes([len(secret)]) + secret)
        _, status = _recv_exactly(sock, 2)
        if status != 0x00:
            raise SocksError("proxy rejected the username and password")
    elif method != 0x00:
        raise SocksError(f"proxy chose an unsupported auth method ({method})")

    # ATYP 3 = domain name: the proxy resolves it, not us.
    target = host.encode("idna") if any(ord(c) > 127 for c in host) else host.encode("ascii")
    if len(target) > 255:
        raise SocksError("hostname too long for SOCKS5")
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(target)]) + target + struct.pack(">H", port))

    version, reply, _, atyp = _recv_exactly(sock, 4)
    if reply != 0x00:
        raise SocksError(_SOCKS5_ERRORS.get(reply, f"proxy refused with code {reply}"))

    # Drain the bound address so the stream is positioned at real payload.
    if atyp == 0x01:
        _recv_exactly(sock, 4 + 2)
    elif atyp == 0x03:
        length = _recv_exactly(sock, 1)[0]
        _recv_exactly(sock, length + 2)
    elif atyp == 0x04:
        _recv_exactly(sock, 16 + 2)
    else:
        raise SocksError(f"proxy replied with an unknown address type ({atyp})")


def _negotiate_socks4a(sock: socket.socket, hop: ProxyHop, host: str, port: int) -> None:
    user = hop.username.encode("utf-8")
    # 0.0.0.x with x != 0 signals SOCKS4a: the hostname follows, unresolved.
    request = b"\x04\x01" + struct.pack(">H", port) + b"\x00\x00\x00\x01"
    try:
        request += user + b"\x00" + host.encode("ascii") + b"\x00"
    except UnicodeEncodeError as exc:
        raise SocksError("SOCKS4 cannot carry a non-ASCII host name") from exc
    sock.sendall(request)

    reply = _recv_exactly(sock, 8)
    if reply[1] != 0x5A:
        raise SocksError(f"SOCKS4 proxy refused (code {reply[1]})")


def _negotiate_http_connect(sock: socket.socket, hop: ProxyHop, host: str, port: int) -> None:
    import base64

    lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
    if hop.username:
        token = base64.b64encode(
            f"{hop.username}:{hop.password}".encode("utf-8")
        ).decode("ascii")
        lines.append(f"Proxy-Authorization: Basic {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

    # Read just the headers, byte by byte — anything more would consume payload.
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        byte = sock.recv(1)
        if not byte:
            raise SocksError("proxy closed the connection during CONNECT")
        buffer += byte
        if len(buffer) > 8192:
            raise SocksError("proxy sent an implausibly long CONNECT response")

    status = buffer.split(b"\r\n", 1)[0].decode("latin-1")
    if " 200" not in status:
        raise SocksError(f"proxy refused CONNECT: {status}")


_NEGOTIATORS = {
    SOCKS5: _negotiate_socks5,
    SOCKS4: _negotiate_socks4a,
    HTTP: _negotiate_http_connect,
}


# ── Chaining ─────────────────────────────────────


def connect_through(
    hops: list[ProxyHop],
    host: str,
    port: int,
    timeout: float = DEFAULT_TIMEOUT,
) -> socket.socket:
    """
    Open a socket to host:port through every hop in order.

    The chain is built by treating each subsequent proxy as the destination of
    the one before it: connect to hop 1, ask it for hop 2, then over that same
    socket ask hop 2 for hop 3, and so on. Each hop therefore only ever learns
    the address of the next one — which is the entire point of chaining.
    """
    if not hops:
        raise SocksError("no proxies in the chain")

    sock = socket.create_connection((hops[0].host, hops[0].port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        for index, hop in enumerate(hops):
            is_last = index == len(hops) - 1
            next_host = host if is_last else hops[index + 1].host
            next_port = port if is_last else hops[index + 1].port

            negotiate = _NEGOTIATORS.get(hop.kind)
            if negotiate is None:
                raise SocksError(f"unsupported proxy type {hop.kind!r}")
            try:
                negotiate(sock, hop, next_host, next_port)
            except SocksError as exc:
                raise SocksError(f"hop {index + 1} ({hop.describe()}): {exc}") from exc
        return sock
    except Exception:
        sock.close()
        raise


def http_get_through(
    hops: list[ProxyHop],
    url_host: str,
    path: str = "/",
    port: int = 80,
    timeout: float = DEFAULT_TIMEOUT,
    tls: bool = False,
) -> str:
    """
    Fetch a small page through the chain. Used to prove a chain really works.

    Deliberately tiny rather than reaching for an HTTP library: the only thing
    it must do is confirm bytes travel end to end and come back.
    """
    sock = connect_through(hops, url_host, port, timeout=timeout)
    try:
        if tls:
            import ssl

            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=url_host)

        request = (
            f"GET {path} HTTP/1.1\r\nHost: {url_host}\r\n"
            "User-Agent: Sentinel\r\nConnection: close\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))

        chunks = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > 262144:
                break
        body = b"".join(chunks).decode("utf-8", "replace")
        return body.split("\r\n\r\n", 1)[-1] if "\r\n\r\n" in body else body
    finally:
        sock.close()

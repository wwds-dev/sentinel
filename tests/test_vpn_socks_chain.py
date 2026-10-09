"""socks_client + proxychain against local fake proxies (loopback only)."""
from __future__ import annotations

import socket
import struct
import threading

import pytest

from agents.vpn_agent.services import proxychain
from agents.vpn_agent.services.socks_client import (
    HTTP, SOCKS4, SOCKS5, ProxyHop, SocksError, connect_through, http_get_through,
)


def recv_n(c, n):
    out = b""
    while len(out) < n:
        chunk = c.recv(n - len(out))
        if not chunk:
            raise EOFError
        out += chunk
    return out


class FakeSocks5:
    """A SOCKS5 proxy that answers every CONNECT by serving a fixed HTTP body itself."""

    def __init__(self, body=b"203.0.113.77\n", user=None, password=None, reply=0x00):
        self.body, self.user, self.password, self.reply = body, user, password, reply
        self.targets = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, c):
        try:
            _, n = recv_n(c, 2)
            methods = recv_n(c, n)
            if self.user is not None:
                if 2 not in methods:
                    c.sendall(b"\x05\xff")
                    return
                c.sendall(b"\x05\x02")
                recv_n(c, 1)
                ulen = recv_n(c, 1)[0]
                user = recv_n(c, ulen).decode()
                plen = recv_n(c, 1)[0]
                pw = recv_n(c, plen).decode()
                ok = (user, pw) == (self.user, self.password)
                c.sendall(b"\x01" + (b"\x00" if ok else b"\x01"))
                if not ok:
                    return
            else:
                c.sendall(b"\x05\x00")
            recv_n(c, 4)
            hlen = recv_n(c, 1)[0]
            host = recv_n(c, hlen).decode()
            port = struct.unpack(">H", recv_n(c, 2))[0]
            self.targets.append((host, port))
            c.sendall(bytes([5, self.reply, 0, 1]) + b"\x00" * 6)
            if self.reply:
                return
            c.recv(4096)
            c.sendall(b"HTTP/1.1 200 OK\r\n\r\n" + self.body)
        except (EOFError, OSError):
            pass
        finally:
            c.close()

    def close(self):
        self.server.close()


@pytest.fixture()
def proxy():
    made = []

    def make(**kw):
        p = FakeSocks5(**kw)
        made.append(p)
        return p
    yield make
    for p in made:
        p.close()


def hop(p, **kw):
    return ProxyHop(kind=SOCKS5, host="127.0.0.1", port=p.port, **kw)


def test_socks5_no_auth_fetch(proxy):
    p = proxy()
    assert http_get_through([hop(p)], "example.test", "/", 80, timeout=3).strip() == "203.0.113.77"
    assert p.targets == [("example.test", 80)]       # the proxy resolves the name, not us


def test_socks5_auth_ok_and_rejected(proxy):
    p = proxy(user="bob", password="s3cret-pw")
    assert http_get_through([hop(p, username="bob", password="s3cret-pw")], "x.test", timeout=3)
    with pytest.raises(SocksError) as e:
        connect_through([hop(p, username="bob", password="WRONG")], "x.test", 80, timeout=3)
    assert "rejected the username and password" in str(e.value)
    assert "WRONG" not in str(e.value) and "s3cret-pw" not in str(e.value)
    with pytest.raises(SocksError) as e:
        connect_through([hop(p)], "x.test", 80, timeout=3)
    assert "demands a username and password" in str(e.value)


def test_refusal_codes_are_explained_and_hop_numbered(proxy):
    p = proxy(reply=0x05)
    with pytest.raises(SocksError) as e:
        connect_through([hop(p)], "x.test", 80, timeout=3)
    assert str(e.value).startswith("hop 1")


def test_closed_first_hop_is_an_oserror():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(OSError):
        connect_through([ProxyHop(host="127.0.0.1", port=port)], "x.test", 80, timeout=1)


def test_empty_chain_and_bad_kind():
    with pytest.raises(SocksError):
        connect_through([], "x", 80)
    assert ProxyHop(kind="ftp").problems()
    assert ProxyHop(host="h", username="", password="p").problems()
    assert ProxyHop(port=70000).problems()


def test_socks4a_non_ascii_host_is_a_socks_error(proxy):
    p = proxy()
    with pytest.raises(SocksError):
        connect_through([ProxyHop(kind=SOCKS4, host="127.0.0.1", port=p.port)], "bücher.test", 80, timeout=2)


def test_probe_reports_exit_ip_and_failure_point(proxy):
    good = proxy()
    chain = proxychain.Chain(hops=[hop(good)])
    r = proxychain.probe(chain, timeout=3)
    assert r.ok and r.exit_ip == "203.0.113.77"
    bad = proxy(reply=0x02)
    r = proxychain.probe(proxychain.Chain(hops=[hop(bad)]), timeout=3)
    assert not r.ok and r.hop_reached == 1 and "Chain failed" in r.summary()
    r = proxychain.probe(proxychain.Chain(), timeout=1)
    assert not r.ok and "no proxies" in r.error


def test_probe_rejects_unreadable_answer(proxy):
    p = proxy(body=b"x" * 100)
    r = proxychain.probe(proxychain.Chain(hops=[hop(p)]), timeout=3)
    assert not r.ok and "unreadable" in r.error


def test_proxychains_conf_content_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    c = proxychain.Chain(hops=[ProxyHop(kind=SOCKS5, host="10.0.0.1", port=9050),
                               ProxyHop(kind=HTTP, host="10.0.0.2", port=3128, username="u", password="p")],
                         mode=proxychain.STRICT)
    text = proxychain.render_proxychains_conf(c)
    assert "strict_chain" in text and "proxy_dns" in text
    assert "socks5 10.0.0.1 9050" in text and "http 10.0.0.2 3128 u p" in text
    assert "Sentinel" in text
    proxychain.save_chain(c)
    back = proxychain.load_chain()
    assert [h.host for h in back.hops] == ["10.0.0.1", "10.0.0.2"] and back.mode == "strict"
    (tmp_path / "proxychain.json").write_text("{broken")
    assert proxychain.load_chain().hops == []
    c.proxy_dns = False
    assert "DNS will leak" in proxychain.render_proxychains_conf(c)

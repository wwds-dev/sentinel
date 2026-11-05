"""socks_client + proxychain against local fake proxies (loopback only)."""
from __future__ import annotations

import socket
import stat
import struct
import threading
from pathlib import Path

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


def test_probe_runs_with_proxychains_dns_off(proxy):
    # proxy_dns is proxychains' setting; the probe hands the name to the last hop anyway.
    p = proxy()
    r = proxychain.probe(proxychain.Chain(hops=[hop(p)], proxy_dns=False), timeout=3)
    assert r.ok and r.exit_ip == "203.0.113.77"


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


DEEP = b'{"hops": ' + b"[" * 3000 + b"]" * 3000 + b"}"


@pytest.mark.parametrize("saved", [
    '{"hops": [{"host": 5}]}',
    '{"hops": [{"port": "1080"}]}',
    '{"hops": [{"port": true}]}',
    '{"hops": [{"password": null}]}',
    '{"hops": {"host": "10.0.0.1"}}',
    '{"hops": ["10.0.0.1"]}',
    '{"mode": 3}',
    '[]',
    b'{"hops": [], "name": "\xff"}',             # not UTF-8
    pytest.param(DEEP, id="nested-too-deeply"),       # RecursionError in json
])
def test_a_wrongly_typed_saved_chain_loads_as_empty(tmp_path, monkeypatch, saved):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    path = proxychain.chain_path()
    path.write_bytes(saved) if isinstance(saved, bytes) else path.write_text(saved)
    chain = proxychain.load_chain()
    assert chain.hops == [] and chain.problems()       # no exception, and nothing to probe
    chain, error = proxychain.load_chain_checked()
    assert chain.hops == [] and "moved to proxychain.json.unreadable" in error
    assert not path.exists() and path.with_name("proxychain.json.unreadable").exists()


def test_a_well_typed_saved_chain_still_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    proxychain.chain_path().write_text(
        '{"mode": "dynamic", "hops": [{"kind": "socks5", "host": "10.0.0.1", "port": 1080}]}')
    chain = proxychain.load_chain()
    assert [h.host for h in chain.hops] == ["10.0.0.1"] and chain.mode == "dynamic"


def test_probe_problems_leave_out_only_the_dns_setting():
    c = proxychain.Chain(hops=[ProxyHop(kind="DNS", host="10.0.0.1", port=1080)], proxy_dns=False)
    assert any("DNS is not being sent" in p for p in c.problems())
    assert c.probe_problems() == ["hop 1: unknown proxy type 'DNS'"]


def test_load_chain_checked_keeps_every_unreadable_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    for body in ("{one", "{two", "{three"):
        proxychain.chain_path().write_text(body)
        _, error = proxychain.load_chain_checked()
        assert "not valid JSON" in error
    assert (tmp_path / "proxychain.json.unreadable").read_text() == "{one"
    assert (tmp_path / "proxychain.json.unreadable-2").read_text() == "{two"
    assert (tmp_path / "proxychain.json.unreadable-3").read_text() == "{three"


def test_load_chain_checked_is_quiet_for_a_good_or_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    assert proxychain.load_chain_checked()[1] == ""
    proxychain.save_chain(proxychain.Chain(hops=[ProxyHop(host="10.0.0.1")]))
    chain, error = proxychain.load_chain_checked()
    assert error == "" and chain.hops[0].host == "10.0.0.1"


def test_probe_refuses_a_dns_kind_without_opening_a_socket(monkeypatch):
    # Review of 729fd02, P3-4: probe() must use probe_problems, not a text filter.
    def no_socket(*a, **k):
        raise AssertionError("probe opened a socket for a chain it should refuse")
    monkeypatch.setattr(socket, "create_connection", no_socket)
    r = proxychain.probe(proxychain.Chain(hops=[ProxyHop(kind="DNS", host="10.0.0.1")]))
    assert not r.ok and "unknown proxy type" in r.error


@pytest.mark.parametrize("saved, why", [
    (b'{"hops": [], "name": "\xff"}', "not UTF-8 text"),
    (b"{broken", "not valid JSON"),
    (b'{"hops": [{"port": "1080"}]}', "hop port must be a number"),
    pytest.param(DEEP, "nested too deeply", id="nested-too-deeply"),
])
def test_load_chain_checked_names_what_is_wrong(tmp_path, monkeypatch, saved, why):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    proxychain.chain_path().write_bytes(saved)
    assert why in proxychain.load_chain_checked()[1]


# ── Review of 380227c ──

def test_a_chain_that_cannot_be_opened_is_moved_aside(tmp_path, monkeypatch):
    # P3-2: a rename needs the folder, not the file, so this one is kept too.
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    path = proxychain.chain_path()
    path.write_text('{"hops": [{"host": "10.0.0.1", "username": "u", "password": "s3cret"}]}')
    real_read = Path.read_text

    def refuse(self, *a, **k):
        if self == path:
            raise PermissionError(13, "Permission denied")
        return real_read(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", refuse)
    chain, error = proxychain.load_chain_checked()
    assert chain.hops == [] and "could not be opened (Permission denied)" in error
    assert "moved to proxychain.json.unreadable" in error and not path.exists()
    assert "s3cret" in (tmp_path / "proxychain.json.unreadable").read_bytes().decode()


def test_a_chain_that_cannot_be_moved_is_left_in_place_with_a_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    path = proxychain.chain_path()
    path.write_text("{broken")
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError(30, "Read-only")))
    chain, error = proxychain.load_chain_checked()
    assert chain.hops == [] and "could not be moved aside" in error
    assert "will be replaced when you edit the chain" in error and path.read_text() == "{broken"


def test_a_moved_aside_copy_is_made_owner_only(tmp_path, monkeypatch):
    # P3-4: a hand edit can leave the file 0644.
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path))
    path = proxychain.chain_path()
    path.write_text('{"hops": [{"port": "1080", "password": "s3cret"}]}')
    path.chmod(0o644)
    proxychain.load_chain_checked()
    assert stat.S_IMODE((tmp_path / "proxychain.json.unreadable").stat().st_mode) == 0o600


def test_a_linked_chain_is_moved_without_touching_its_target(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(state))
    outside = tmp_path / "outside.json"
    outside.write_text("{broken")
    outside.chmod(0o644)
    proxychain.chain_path().symlink_to(outside)
    _, error = proxychain.load_chain_checked()
    aside = state / "proxychain.json.unreadable"
    assert "moved to" in error and aside.is_symlink()
    assert outside.read_text() == "{broken" and stat.S_IMODE(outside.stat().st_mode) == 0o644

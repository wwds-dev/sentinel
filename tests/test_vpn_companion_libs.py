"""Step-1 tests for the companion libraries: Tor config/identity, MAC changer
privilege path, native deploy temp file. All offline; no real root, ssh or tor."""
from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

from agents.vpn_agent.server import deploy, paths
from agents.vpn_agent.server.model import MODE_NATIVE, default_site
from agents.vpn_agent.services import macaddr, tor


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "Application Support" / "st"))
    return tmp_path


# ── Tor ──
def test_torrc_quotes_paths_with_spaces(state):
    text = tor.build_torrc()
    directory = tor.data_dir()
    assert " " in str(directory)
    for key in ("DataDirectory", "CookieAuthFile"):
        line = next(l for l in text.splitlines() if l.startswith(key))
        assert line.split(" ", 1)[1].startswith('"') and line.endswith('"')
    log = next(l for l in text.splitlines() if l.startswith("Log notice file"))
    assert str(directory / "tor.log") in log and log.endswith('"')
    assert "ClientOnly 1" in text and "ExitRelay 0" in text


def test_write_torrc_modes(state):
    path = tor.write_torrc()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_tor_binary_searches_homebrew(tmp_path, monkeypatch):
    fake = tmp_path / "tor"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(tor.shutil, "which", lambda name: None)
    monkeypatch.setattr(tor, "_TOR_SEARCH_DIRS", (str(tmp_path),))
    assert tor.tor_binary() == str(fake)
    monkeypatch.setattr(tor, "_TOR_SEARCH_DIRS", (str(tmp_path / "none"),))
    assert tor.tor_binary() is None


def _ps(monkeypatch, command, rc=0):
    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, rc, stdout=command + "\n", stderr="")
    monkeypatch.setattr(tor.subprocess, "run", fake)


def test_is_our_tor_requires_name_and_our_torrc(state, monkeypatch):
    rc = tor.torrc_path()
    _ps(monkeypatch, f"/opt/homebrew/bin/tor -f {rc}")
    assert tor._is_our_tor(123)
    _ps(monkeypatch, f"/usr/bin/python3 tor -f {rc}")
    assert not tor._is_our_tor(123)
    _ps(monkeypatch, "/opt/homebrew/bin/tor -f /etc/tor/torrc")
    assert not tor._is_our_tor(123)
    _ps(monkeypatch, "", rc=1)
    assert not tor._is_our_tor(123)


def test_stop_never_signals_a_reused_pid(state, monkeypatch):
    tor.write_torrc()
    tor._pid_path().write_text("4242")
    _ps(monkeypatch, "/usr/bin/vim notes.txt")
    killed = []
    monkeypatch.setattr(tor.os, "kill", lambda *a: killed.append(a))
    ok, msg = tor.stop()
    assert not ok and "left alone" in msg
    assert killed == []
    assert not tor._pid_path().exists()


def test_stop_signals_our_tor(state, monkeypatch):
    tor.write_torrc()
    tor._pid_path().write_text("4242")
    _ps(monkeypatch, f"tor -f {tor.torrc_path()}")
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError
    monkeypatch.setattr(tor.os, "kill", kill)
    ok, _ = tor.stop()
    assert ok and calls[0][0] == 4242


# ── MAC changer ──
class _Iface:
    def __init__(self, wifi):
        self.is_wifi = wifi


def test_set_mac_uses_dialog_and_quotes(monkeypatch):
    seen = {}
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _Iface(True))
    monkeypatch.setattr(macaddr, "current_mac", lambda d: "02:11:22:33:44:55")

    def fake(script, prompt, *a, **kw):
        seen.update(script=script, kw=kw)
        return True, ""
    monkeypatch.setattr(macaddr, "run_as_root", fake)
    ok, msg = macaddr.set_mac("en0", "02:11:22:33:44:55")
    assert ok and "rejoin" in msg
    assert seen["kw"] == {"allow_cached_sudo": False}
    assert seen["script"].startswith("/usr/sbin/networksetup -setairportpower en0 off")
    assert "/sbin/ifconfig en0 ether 02:11:22:33:44:55" in seen["script"]


def test_set_mac_rejects_hostile_device_and_bad_address(monkeypatch):
    called = []
    monkeypatch.setattr(macaddr, "run_as_root", lambda *a, **k: called.append(a) or (True, ""))
    assert not macaddr.set_mac("en0", "zz")[0]
    monkeypatch.setattr(macaddr, "get_interface", lambda d: None)
    assert not macaddr.set_mac("en0; rm -rf /", "02:11:22:33:44:55")[0]
    assert called == []


def test_set_mac_detects_silent_ignore(monkeypatch):
    monkeypatch.setattr(macaddr, "get_interface", lambda d: _Iface(False))
    monkeypatch.setattr(macaddr, "current_mac", lambda d: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(macaddr, "run_as_root", lambda *a, **k: (True, ""))
    ok, msg = macaddr.set_mac("en5", "02:11:22:33:44:55")
    assert not ok and "silently ignore" in msg


# ── Native deploy ──
def test_native_deploy_goes_through_dialog_and_removes_secret_file(state, monkeypatch):
    from agents.vpn_agent.services import privileged
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    seen = {}

    def fake(script, prompt, timeout=0, *, allow_cached_sudo=True):
        path = Path(shlex.split(script)[-1])
        seen.update(path=path, exists=path.exists(),
                    mode=stat.S_IMODE(path.stat().st_mode),
                    body=path.read_text(), flag=allow_cached_sudo)
        return True, "line one\nline two"
    monkeypatch.setattr(privileged, "run_as_root", fake)
    lines = []
    site = default_site("home", MODE_NATIVE)
    res = deploy._run_local(site, "echo PRIVATE-KEY-MATERIAL", lines.append)
    assert res.success and lines == ["line one", "line two"]
    assert seen["flag"] is False and seen["exists"] and seen["mode"] == 0o600
    assert "PRIVATE-KEY-MATERIAL" in seen["body"]
    assert not seen["path"].exists()


def test_native_deploy_removes_file_when_dialog_fails(state, monkeypatch):
    from agents.vpn_agent.services import privileged
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    holder = {}

    def boom(script, *a, **k):
        holder["p"] = Path(shlex.split(script)[-1])
        return False, "User canceled."
    monkeypatch.setattr(privileged, "run_as_root", boom)
    res = deploy._run_local(default_site("home", MODE_NATIVE), "secret", None)
    assert not res.success and not holder["p"].exists()

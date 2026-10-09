"""Tunnel › Servers: sites, peers, export, QR, backup/restore, delete."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from agents.vpn_agent.server import backup, paths, provision, store
from services import vpn_execution
from ui.panels import vpn_gate, vpn_servers
from ui.panels.vpn_servers import ServersTab
from tests.test_vpn_privacy_tab import SyncWorker

PASS = "correct horse battery staple"


class Env:
    answers: list
    asked: list


@pytest.fixture()
def env(monkeypatch, tmp_path):
    e = Env()
    e.answers, e.asked, e.warnings = [], [], []
    e.audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(vpn_execution, "default_audit_path", lambda: e.audit)
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(vpn_gate, "CallWorker", SyncWorker)

    def question(parent, title, text, *a, **k):
        e.asked.append((title, text))
        return QMessageBox.Yes if (e.answers.pop(0) if e.answers else False) else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    e.tab = ServersTab()
    e.tab.refresh_sites()
    e.qr_shown = []
    monkeypatch.setattr(ServersTab, "_show_qr_dialog", lambda self, n, p: e.qr_shown.append((n, p)))
    e.typed = []
    monkeypatch.setattr(ServersTab, "_ask_text", lambda self, t, p: e.typed.pop(0) if e.typed else None)
    e.tmp = tmp_path
    return e


def audit(e):
    return [json.loads(l) for l in e.audit.read_text().splitlines()] if e.audit.exists() else []


def make_site(e, name="home", mode="remote", peers=("phone",)):
    e.tab.new_name_input.setText(name)
    e.tab.new_mode_box.setCurrentText(mode)
    e.tab.new_endpoint_input.setText("vpn.example.org")
    e.tab.create_site()
    e.tab.site_list.setCurrentRow(0)
    for p in peers:
        e.tab.peer_name_input.setText(p)
        e.tab.add_peer()
    return store.load_site(name)


def select_peer(e, row=0):
    e.tab.peer_table.selectRow(row)


def all_text(e):
    t = e.tab
    return " ".join([t.status_label.text(), t.problems_label.text()]
                    + [t.peer_table.item(r, c).text() for r in range(t.peer_table.rowCount())
                       for c in range(4)] + [a[1] for a in e.asked])


def test_create_site_persists_private_and_lists_it(env):
    site = make_site(env)
    assert [s for s in store.list_sites()] == ["home"]
    f = paths.site_file("home")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert site.server_wg_private_key and env.tab.peer_table.rowCount() == 1
    assert audit(env)[0]["action"] == "site-create"
    # a duplicate is refused and the existing site untouched
    before = f.read_text()
    env.tab.new_name_input.setText("home")
    env.tab.create_site()
    assert f.read_text() == before and "already exists" in env.tab.status_label.text()


def test_settings_save_and_persist_and_reload(env):
    make_site(env)
    t = env.tab
    t.wg_port_input.setValue(51999)
    t.full_tunnel_check.setChecked(False)
    t.lan_input.setText("192.168.5.0/24, 10.1.0.0/16")
    t.ssh_host_input.setText("203.0.113.5")
    t.save_settings()
    s = store.load_site("home")
    assert s.wg_port == 51999 and not s.full_tunnel
    assert s.lan_routes == ["192.168.5.0/24", "10.1.0.0/16"] and s.ssh.host == "203.0.113.5"
    assert "Ready to deploy" in t.problems_label.text()


def test_endpoint_change_reissues_server_cert(env):
    s0 = make_site(env)
    env.tab.endpoint_input.setText("other.example.org")
    env.tab.save_settings()
    s1 = store.load_site("home")
    assert s1.endpoint_host == "other.example.org" and s1.server_cert_pem != s0.server_cert_pem


def test_problems_are_shown_not_hidden(env):
    make_site(env)
    env.tab.ssh_host_input.setText("")
    env.tab.save_settings()
    assert "SSH host" in env.tab.problems_label.text()


def test_rotate_declined_changes_nothing_and_confirmed_changes_keys(env):
    site = make_site(env)
    old = site.peers[0].wg_private_key
    select_peer(env)
    env.tab.rotate_peer()
    assert store.load_site("home").peers[0].wg_private_key == old
    assert "stops working" in env.asked[0][1]
    assert audit(env)[-1]["outcome"] == "declined"
    env.answers = [True]
    env.tab.rotate_peer()
    assert store.load_site("home").peers[0].wg_private_key != old
    assert audit(env)[-1]["action"] == "peer-rotate" and audit(env)[-1]["outcome"] == "succeeded"


def test_remove_peer_gate(env):
    make_site(env)
    select_peer(env)
    env.tab.remove_peer()
    assert len(store.load_site("home").peers) == 1
    env.answers = [True]
    env.tab.remove_peer()
    assert store.load_site("home").peers == []


def test_toggle_peer_persists(env):
    make_site(env)
    select_peer(env)
    env.tab.toggle_peer()
    assert store.load_site("home").peers[0].enabled is False
    assert env.tab.peer_table.item(0, 2).text() == "no"


def test_export_is_gated_and_files_are_private(env):
    make_site(env)
    select_peer(env)
    env.tab.export_btn.click()
    d = paths.exports_dir("home")
    assert not d.exists() or not any(d.iterdir())
    assert "PRIVATE" in env.asked[0][1] and audit(env)[-1]["outcome"] == "declined"
    env.answers = [True]
    env.tab.export_btn.click()
    files = list(d.iterdir())
    assert any(f.suffix == ".conf" for f in files)
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
    assert audit(env)[-1]["action"] == "export"


def test_private_keys_never_reach_screen_or_audit(env):
    site = make_site(env)
    select_peer(env)
    env.answers = [True, True]
    env.tab.export_btn.click()
    env.tab.rotate_peer()
    blob = all_text(env) + env.audit.read_text()
    secrets = [site.server_wg_private_key, site.ca_key_pem, site.tls_crypt_key,
               site.peers[0].wg_private_key, site.peers[0].wg_preshared_key,
               site.peers[0].ovpn_key_pem]
    for s in secrets:
        assert s and s not in blob
    # nothing in the saved site file is world/group readable
    assert stat.S_IMODE(paths.site_file("home").stat().st_mode) == 0o600


def test_qr_declined_shows_nothing_and_confirmed_is_not_saved(env):
    make_site(env)
    select_peer(env)
    env.tab.show_qr()
    assert env.qr_shown == [] and audit(env)[-1]["outcome"] == "declined"
    env.answers = [True]
    env.tab.show_qr()
    assert len(env.qr_shown) == 1 and not env.qr_shown[0][1].isNull()
    d = paths.exports_dir("home")
    assert not d.exists() or not any(d.iterdir())
    assert audit(env)[-1]["action"] == "export-qr" and audit(env)[-1]["outcome"] == "succeeded"


def test_backup_roundtrip_and_restore_over_existing_needs_confirm(env, monkeypatch):
    site = make_site(env)
    out = env.tmp / "home.vpnbackup"
    monkeypatch.setattr(ServersTab, "_ask_passphrase", lambda self, confirm: PASS)
    monkeypatch.setattr(ServersTab, "_ask_save_path", lambda self, d: str(out))
    env.tab.backup_site()
    assert out.exists() and stat.S_IMODE(out.stat().st_mode) == 0o600
    assert site.ca_key_pem.encode() not in out.read_bytes()
    assert audit(env)[-1]["action"] == "backup" and PASS not in env.audit.read_text()
    assert vpn_servers.has_backup("home")

    monkeypatch.setattr(ServersTab, "_ask_open_path", lambda self: str(out))
    env.tab.restore_backup()                      # exists -> asks -> declined
    assert audit(env)[-1]["outcome"] == "declined"
    env.answers = [True]
    env.tab.restore_backup()
    assert audit(env)[-1]["action"] == "restore" and audit(env)[-1]["outcome"] == "succeeded"
    assert store.load_site("home").server_wg_private_key == site.server_wg_private_key


def test_restore_wrong_passphrase_is_reported_and_audited(env, monkeypatch):
    site = make_site(env)
    out = env.tmp / "b.vpnbackup"
    backup.write_backup(site, PASS, out)
    monkeypatch.setattr(ServersTab, "_ask_open_path", lambda self: str(out))
    monkeypatch.setattr(ServersTab, "_ask_passphrase", lambda self, confirm: "wrong wrong wrong")
    env.tab.restore_backup()
    assert audit(env)[-1]["outcome"] == "failed" and "wrong wrong wrong" not in env.audit.read_text()


def test_weak_passphrase_needs_confirm(env, monkeypatch):
    make_site(env)
    monkeypatch.setattr(ServersTab, "_ask_passphrase", lambda self, confirm: "vpn")
    monkeypatch.setattr(ServersTab, "_ask_save_path", lambda self, d: str(env.tmp / "x.vpnbackup"))
    env.tab.backup_site()
    assert not (env.tmp / "x.vpnbackup").exists() and "Weak" in env.asked[0][0]


def test_delete_requires_typed_name_and_a_backup_or_acceptance(env, monkeypatch):
    make_site(env)
    env.typed = [None]
    env.tab.delete_site()
    assert store.site_exists("home")
    env.typed, env.answers = ["wrong"], [True]     # even a Yes later must not matter
    env.tab.delete_site()
    assert store.site_exists("home") and audit(env)[-1]["outcome"] == "declined"
    env.answers.clear()
    env.typed = ["home"]          # right name, no backup, acceptance declined
    env.tab.delete_site()
    assert store.site_exists("home") and audit(env)[-1]["outcome"] == "declined"
    env.typed, env.answers = ["home"], [True]
    env.tab.delete_site()
    assert not store.site_exists("home") and audit(env)[-1]["action"] == "site-delete"
    assert env.tab.site_list.count() == 0


def test_delete_with_backup_needs_no_second_prompt(env):
    make_site(env)
    vpn_servers.mark_backup("home")
    env.typed = ["home"]
    env.tab.delete_site()
    assert not store.site_exists("home") and env.asked == []


def test_corrupt_site_file_does_not_crash_the_tab(env):
    make_site(env)
    paths.site_file("home").write_text("{not json")
    env.tab.refresh_sites()
    assert env.tab.site_list.count() == 0       # skipped by list_sites
    paths.site_file("home").write_text('{"name": "home"}')
    paths.site_file("home").chmod(0o644)
    env.tab.refresh_sites()
    env.tab._select_site("home")
    assert "Could not open" in env.tab.status_label.text()

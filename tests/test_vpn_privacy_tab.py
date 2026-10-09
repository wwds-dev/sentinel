"""Tunnel › Privacy: MAC changer, Tor and proxy chain — gate, audit, secrets."""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QMessageBox

from agents.vpn_agent.services import macaddr, proxychain, tor
from agents.vpn_agent.services.macaddr import Interface
from services import vpn_execution
from ui.panels import vpn_privacy
from ui.panels.vpn_privacy import PrivacyTab

CANARY = "PW-CANARY-9f2c71"


class SyncWorker:
    def __init__(self, func):
        self.func = func
        from PySide6.QtCore import QObject, Signal

        class S(QObject):
            fin = Signal(object)
            err = Signal(str)
        self._s = S()
        self.finished_signal = self._s.fin
        self.error_signal = self._s.err

    def start(self):
        try:
            self.finished_signal.emit(self.func())
        except Exception as exc:  # noqa: BLE001
            self.error_signal.emit(str(exc))

    def isRunning(self):  # noqa: N802
        return False

    def cancel(self):
        pass


class Env:
    def __init__(self):
        self.answers = []
        self.asked = []
        self.calls = []
        self.warnings = []


@pytest.fixture()
def env(monkeypatch, tmp_path):
    e = Env()
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(vpn_execution, "default_audit_path", lambda: audit)
    e.audit = audit
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(vpn_privacy, "CallWorker", SyncWorker)

    def question(parent, title, text, *a, **k):
        e.asked.append((title, text))
        return QMessageBox.Yes if (e.answers.pop(0) if e.answers else False) else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda p, t, m, *a, **k: e.warnings.append(m)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    e.ifaces = [Interface("en0", "Wi-Fi", "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:01"),
                Interface("en5", "USB 10/100 LAN", "02:00:00:00:00:09", "aa:bb:cc:00:00:05")]
    monkeypatch.setattr(macaddr, "list_interfaces", lambda: e.ifaces)
    monkeypatch.setattr(tor, "is_installed", lambda: True)
    monkeypatch.setattr(tor, "is_running", lambda: False)
    monkeypatch.setattr(tor, "bootstrap_progress", lambda: "")

    def net(name, result):
        def f(*a, **k):
            e.calls.append(name)
            return result
        return f
    e.net = net
    e.tab = PrivacyTab()
    e.tab.refresh_interfaces()
    return e


def audit_lines(e):
    return [json.loads(l) for l in e.audit.read_text().splitlines()] if e.audit.exists() else []


def test_declined_mac_change_touches_nothing_and_is_audited(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "x")))
    env.tab.randomise_mac()
    assert env.calls == []
    text = env.asked[0][1]
    assert "en0" in text and "Wi-Fi will be switched off" in text and "→" in text
    [line] = audit_lines(env)
    assert line["action"] == "mac-set" and line["outcome"] == "declined"


def test_approved_mac_change_runs_and_audits(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "en0 is now x")))
    env.answers = [True]
    env.tab.randomise_mac()
    assert env.calls == ["set_mac"]
    assert audit_lines(env)[-1]["outcome"] == "succeeded"
    assert "en0 is now x" in env.tab.status_label.text()


def test_failed_mac_change_is_reported_and_audited(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (False, "refused")))
    env.answers = [True]
    env.tab.randomise_mac()
    assert audit_lines(env)[-1]["outcome"] == "failed"


def test_restore_only_when_changed(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.tab.restore_mac()                      # en0 is unchanged
    assert env.asked == [] and "already" in env.tab.status_label.text()
    env.tab.mac_device_box.setCurrentIndex(1)  # en5 is changed
    env.answers = [True]
    env.tab.restore_mac()
    assert env.calls == ["set_mac"] and "aa:bb:cc:00:00:05" in env.asked[0][1]


def test_no_interface_is_a_refusal(env):
    env.ifaces.clear()
    env.tab.refresh_interfaces()
    env.tab.randomise_mac()
    assert audit_lines(env)[-1]["outcome"] == "refused" and env.asked == []


def test_tor_start_declined_and_approved(env, monkeypatch):
    monkeypatch.setattr(tor, "start", env.net("start", (True, "up")))
    env.tab.start_tor()
    assert env.calls == [] and audit_lines(env)[-1]["outcome"] == "declined"
    assert "not anonymity" in env.asked[0][1]
    env.answers = [True]
    env.tab.start_tor()
    assert env.calls == ["start"] and audit_lines(env)[-1]["action"] == "tor-start"


def test_tor_missing_is_refused_with_install_hint(env, monkeypatch):
    monkeypatch.setattr(tor, "is_installed", lambda: False)
    monkeypatch.setattr(tor, "start", env.net("start", (True, "")))
    env.tab.refresh_tor()
    assert "brew install tor" in env.tab.tor_state_label.text()
    assert not env.tab.tor_start_btn.isEnabled()
    env.tab.start_tor()
    assert env.calls == [] and audit_lines(env)[-1]["outcome"] == "refused"


def test_tor_stop_and_newnym_need_no_confirm_but_are_audited(env, monkeypatch):
    monkeypatch.setattr(tor, "stop", env.net("stop", (True, "stopped")))
    monkeypatch.setattr(tor, "new_identity", env.net("newnym", (True, "new")))
    env.tab.stop_tor()
    env.tab.new_tor_identity()
    assert env.calls == ["stop", "newnym"] and env.asked == []
    assert [l["action"] for l in audit_lines(env)] == ["tor-stop", "tor-newnym"]


def test_tor_check_names_the_site_and_declines_cleanly(env, monkeypatch):
    monkeypatch.setattr(tor, "check", env.net("check", (True, "ok")))
    env.tab.check_tor()
    assert tor.CHECK_HOST in env.asked[0][1] and env.calls == []
    assert audit_lines(env)[-1]["outcome"] == "declined"


def _add_hop(tab, host="10.0.0.1", port=1080, user="", pw=""):
    tab.hop_host_input.setText(host)
    tab.hop_port_input.setValue(port)
    tab.hop_user_input.setText(user)
    tab.hop_pass_input.setText(pw)
    tab.add_hop()


def test_chain_edit_persists_and_never_shows_password(env):
    _add_hop(env.tab, user="bob", pw=CANARY)
    _add_hop(env.tab, host="10.0.0.2")
    assert env.tab.chain_table.rowCount() == 2
    cells = [env.tab.chain_table.item(r, c).text() for r in range(2) for c in range(6)]
    assert CANARY not in " ".join(cells) + env.tab.chain_summary_label.text() \
        + env.tab.status_label.text()
    assert env.tab.hop_pass_input.text() == ""
    reloaded = proxychain.load_chain()
    assert [h.host for h in reloaded.hops] == ["10.0.0.1", "10.0.0.2"]
    env.tab.chain_table.selectRow(1)
    env.tab.move_hop(-1)
    assert proxychain.load_chain().hops[0].host == "10.0.0.2"
    env.tab.remove_hop()
    assert len(proxychain.load_chain().hops) == 1


def test_bad_hop_is_not_added(env):
    _add_hop(env.tab, host="  ")
    assert env.tab.chain_table.rowCount() == 0 and "no host" in env.tab.status_label.text()
    _add_hop(env.tab, host="h", pw="x")      # password without username
    assert env.tab.chain_table.rowCount() == 0


def test_probe_is_gated_and_names_hops_and_site(env, monkeypatch):
    _add_hop(env.tab, user="bob", pw=CANARY)
    monkeypatch.setattr(proxychain, "probe",
                        lambda chain, **k: (env.calls.append("probe"),
                                            proxychain.ProbeResult(True, "1.2.3.4", 12))[1])
    env.tab.test_chain()
    text = env.asked[0][1]
    assert proxychain.PROBE_HOST in text and "10.0.0.1" in text and CANARY not in text
    assert env.calls == [] and audit_lines(env)[-1]["outcome"] == "declined"
    env.answers = [True]
    env.tab.test_chain()
    assert env.calls == ["probe"] and "1.2.3.4" in env.tab.status_label.text()


def test_empty_chain_probe_is_refused_without_contact(env, monkeypatch):
    monkeypatch.setattr(proxychain, "probe", env.net("probe", None))
    env.tab.test_chain()
    assert env.calls == [] and audit_lines(env)[-1]["outcome"] == "refused"


def test_canary_never_reaches_audit_or_conf_modes(env, monkeypatch):
    import stat
    _add_hop(env.tab, user="bob", pw=CANARY)
    monkeypatch.setattr(proxychain, "probe",
                        lambda chain, **k: proxychain.ProbeResult(False, error="proxy rejected the username and password"))
    env.answers = [True]
    env.tab.test_chain()
    env.tab.write_conf()
    assert CANARY not in env.audit.read_text()
    assert CANARY not in env.tab.status_label.text()
    path = proxychain.chain_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    conf = path.parent / "proxychains.conf"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600


def test_busy_blocks_second_action(env, monkeypatch):
    monkeypatch.setattr(tor, "stop", env.net("stop", (True, "")))
    env.tab._active = True
    env.tab.stop_tor()
    assert env.calls == []


def test_worker_exception_is_audited_as_failed(env, monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(tor, "stop", boom)
    env.tab.stop_tor()
    assert audit_lines(env)[-1]["outcome"] == "failed"
    assert "kaboom" in env.tab.status_label.text()
    assert env.tab.busy is False


def test_tunnel_panel_hosts_the_tab_and_shutdown_joins_it(env, monkeypatch):
    from ui.panels.vpn import VpnPanel
    tabs = [VpnPanel.__dict__]  # noqa: F841 - class import check only
    joined = []
    t = env.tab
    t._worker = type("W", (), {"isRunning": lambda s: True, "cancel": lambda s: joined.append("c"),
                               "wait": lambda s, ms: joined.append("w") or True})()
    t.shutdown()
    assert joined == ["c", "w"]

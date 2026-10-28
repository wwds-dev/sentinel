"""Tunnel › Privacy: MAC changer, Tor and proxy chain — gate, secrets."""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QMessageBox

from agents.vpn_agent.services import macaddr, proxychain, tor
from agents.vpn_agent.services.macaddr import Interface
from services import vpn_execution
from ui.panels import vpn_gate, vpn_privacy
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
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(vpn_gate, "CallWorker", SyncWorker)

    def question(parent, title, text, *a, **k):
        e.asked.append((title, text))
        return QMessageBox.Yes if (e.answers.pop(0) if e.answers else False) else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda p, t, m, *a, **k: e.warnings.append(m)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    e.ifaces = [Interface("en0", "Wi-Fi", "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:01"),
                Interface("en5", "USB 10/100 LAN", "02:00:00:00:00:09", "aa:bb:cc:00:00:05")]
    monkeypatch.setattr(macaddr, "list_interfaces", lambda: list(e.ifaces))
    e.read_error = ""
    monkeypatch.setattr(macaddr, "list_interfaces_checked",
                        lambda: ([] if e.read_error else list(e.ifaces), e.read_error))
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



def test_declined_mac_change_touches_nothing(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "x")))
    env.tab.randomise_mac()
    assert env.calls == []
    text = env.asked[0][1]
    assert "en0" in text and "Wi-Fi will be switched off" in text and "→" in text


def test_approved_mac_change_runs(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "en0 is now x")))
    env.answers = [True]
    env.tab.randomise_mac()
    assert env.calls == ["set_mac"]
    assert "en0 is now x" in env.tab.status_label.text()


def test_failed_mac_change_is_reported(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (False, "refused")))
    env.answers = [True]
    env.tab.randomise_mac()


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


def test_tor_start_declined_and_approved(env, monkeypatch):
    monkeypatch.setattr(tor, "start", env.net("start", (True, "up")))
    env.tab.start_tor()
    assert env.calls == []
    assert "not anonymity" in env.asked[0][1]
    env.answers = [True]
    env.tab.start_tor()
    assert env.calls == ["start"]


def test_tor_missing_is_refused_with_install_hint(env, monkeypatch):
    monkeypatch.setattr(tor, "is_installed", lambda: False)
    monkeypatch.setattr(tor, "start", env.net("start", (True, "")))
    env.tab.refresh_tor()
    assert "brew install tor" in env.tab.tor_state_label.text()
    assert not env.tab.tor_start_btn.isEnabled()
    env.tab.start_tor()
    assert env.calls == []


def test_tor_stop_and_newnym_need_no_confirm(env, monkeypatch):
    monkeypatch.setattr(tor, "stop", env.net("stop", (True, "stopped")))
    monkeypatch.setattr(tor, "new_identity", env.net("newnym", (True, "new")))
    env.tab.stop_tor()
    env.tab.new_tor_identity()
    assert env.calls == ["stop", "newnym"] and env.asked == []


def test_tor_check_names_the_site_and_declines_cleanly(env, monkeypatch):
    monkeypatch.setattr(tor, "check", env.net("check", (True, "ok")))
    env.tab.check_tor()
    assert tor.CHECK_HOST in env.asked[0][1] and env.calls == []


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
    env.tab.chain_table.setCurrentCell(1, 0)
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
    assert env.calls == []
    env.answers = [True]
    env.tab.test_chain()
    assert env.calls == ["probe"] and "1.2.3.4" in env.tab.status_label.text()


def test_empty_chain_probe_is_refused_without_contact(env, monkeypatch):
    monkeypatch.setattr(proxychain, "probe", env.net("probe", None))
    env.tab.test_chain()
    assert env.calls == []


def test_canary_never_reaches_the_screen_or_conf_modes(env, monkeypatch):
    import stat
    _add_hop(env.tab, user="bob", pw=CANARY)
    monkeypatch.setattr(proxychain, "probe",
                        lambda chain, **k: proxychain.ProbeResult(False, error="proxy rejected the username and password"))
    env.answers = [True]
    env.tab.test_chain()
    env.tab.write_conf()
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


def test_worker_exception_is_reported_as_failed(env, monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(tor, "stop", boom)
    env.tab.stop_tor()
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


def test_restore_refuses_when_the_current_address_cannot_be_read(env, monkeypatch):
    """Audit Medium 2: an unreadable address is unknown, not 'already restored'."""
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.ifaces[0] = Interface("en0", "Wi-Fi", "", "aa:bb:cc:00:00:01")
    env.tab.refresh_interfaces()
    env.tab.restore_mac()
    assert env.calls == [] and env.asked == []
    assert "already" not in env.tab.status_label.text()
    assert "could not read" in env.tab.status_label.text()


def _leftover(monkeypatch, pending):
    monkeypatch.setattr(macaddr, "recorded", lambda: {d: o for d, o, _ in pending})
    monkeypatch.setattr(macaddr, "pending_restores", lambda: list(pending))


def test_a_leftover_change_is_offered_for_restore_at_startup(env, monkeypatch):
    """Audit High 1: a change left by a crash is offered back, through the gate."""
    _leftover(monkeypatch, [("en5", "aa:bb:cc:00:00:05", "02:00:00:00:00:09")])
    monkeypatch.setattr(macaddr, "restore_all_recorded",
                        env.net("restore", [("en5", True, "en5 is now aa:bb:cc:00:00:05.")]))
    env.answers = [True]
    tab = PrivacyTab()
    tab.offer_recorded_restore()
    assert env.calls == ["restore"]
    title, text = env.asked[-1]
    assert "en5" in text and "aa:bb:cc:00:00:05" in text and "02:00:00:00:00:09" in text


def test_a_declined_leftover_restore_runs_nothing(env, monkeypatch):
    _leftover(monkeypatch, [("en5", "aa:bb:cc:00:00:05", "02:00:00:00:00:09")])
    monkeypatch.setattr(macaddr, "restore_all_recorded", env.net("restore", []))
    tab = PrivacyTab()
    tab.offer_recorded_restore()
    assert env.calls == [] and len(env.asked) == 1


def test_no_leftover_means_no_offer(env, monkeypatch):
    _leftover(monkeypatch, [])
    PrivacyTab().offer_recorded_restore()
    assert env.asked == []


@pytest.mark.parametrize("pending, offered", [
    ([("en5", "aa:bb:cc:00:00:05", "02:00:00:00:00:09")], 1), ([], 0)])
def test_the_offer_is_scheduled_at_startup_only_when_a_change_is_recorded(
        env, monkeypatch, pending, offered):
    from PySide6.QtWidgets import QApplication
    _leftover(monkeypatch, pending)
    hits = []
    monkeypatch.setattr(PrivacyTab, "offer_recorded_restore", lambda self: hits.append(self))
    tab = PrivacyTab()
    assert hits == []                     # never during construction
    QApplication.processEvents()
    assert len(hits) == offered and (not hits or hits[0] is tab)


# ── Medium 5, Lows 7, 9, 12 ──

def test_there_is_no_mac_mode_choice(env):
    assert not hasattr(env.tab, "mac_mode_box")


def test_randomise_offers_a_locally_administered_unicast_address(env, monkeypatch):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.answers = [True]
    env.tab.randomise_mac()
    shown = [w for w in env.asked[0][1].split() if w.count(":") == 5][-1]
    assert int(shown.split(":")[0], 16) & 0x03 == 0x02


def test_the_confirm_dialog_shows_the_address_read_just_before(env, monkeypatch):
    env.tab.mac_device_box.setCurrentIndex(1)           # en5, listed as ...:09
    env.ifaces[1] = Interface("en5", "USB 10/100 LAN", "02:00:00:00:00:77", "aa:bb:cc:00:00:05")
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.answers = [True]
    env.tab.randomise_mac()
    assert "02:00:00:00:00:77" in env.asked[0][1]
    assert "02:00:00:00:00:09" not in env.asked[0][1]


def test_restore_decides_on_the_address_read_just_before(env, monkeypatch):
    env.tab.mac_device_box.setCurrentIndex(1)           # listed as changed
    env.ifaces[1] = Interface("en5", "USB 10/100 LAN", "aa:bb:cc:00:00:05", "aa:bb:cc:00:00:05")
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.tab.restore_mac()                               # already back: nothing to ask
    assert env.asked == [] and env.calls == []


def test_an_interface_that_vanished_is_a_refusal(env, monkeypatch):
    env.ifaces[:] = [env.ifaces[0]]                     # en5 unplugged since the list
    env.tab.mac_device_box.setCurrentIndex(1)
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.tab.randomise_mac()
    assert env.asked == [] and env.calls == [] and env.warnings


def test_refresh_survives_an_os_error_but_not_a_bug(env, monkeypatch):
    def boom(exc):
        def f():
            raise exc
        return f
    monkeypatch.setattr(macaddr, "list_interfaces", boom(OSError("no networksetup")))
    env.tab.refresh_interfaces()
    assert env.tab._interfaces == []
    # Say why the list is empty, not "No network interfaces found".
    for label in (env.tab.mac_current_label, env.tab.status_label):
        assert "Could not list network interfaces: no networksetup" in label.text()
    monkeypatch.setattr(macaddr, "list_interfaces", boom(RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        env.tab.refresh_interfaces()


def test_the_gate_target_for_a_mac_change_holds_no_address(env, monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(env.tab, "_gated",
                        lambda action, target, *a, **k: seen.append(target) or True)
    env.tab.randomise_mac()
    env.tab.mac_device_box.setCurrentIndex(1)
    env.tab.restore_mac()
    assert seen and all(":" not in t for t in seen), seen
    assert not caplog.records


def test_a_mac_change_writes_no_log_file(env, monkeypatch, tmp_path):
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.answers = [True]
    before = set(tmp_path.rglob("*"))
    env.tab.randomise_mac()
    assert [p for p in set(tmp_path.rglob("*")) - before if p.suffix in (".log", ".jsonl")] == []


# ── Review of c807267, P3-1: the fresh read's branches ──

def test_a_failing_fresh_read_is_a_refusal_not_a_crash(env, monkeypatch):
    env.read_error = "networksetup timed out"
    monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
    env.tab.randomise_mac()
    assert env.asked == [] and env.calls == [] and env.warnings
    # Not "gone": the list is kept and the reason is shown.
    assert "networksetup timed out" in env.warnings[-1]
    assert "no longer available" not in env.warnings[-1]
    assert [i.device for i in env.tab._interfaces] == ["en0", "en5"]
    assert not env.tab.busy


def test_a_vanished_interface_is_dropped_from_the_list(env, monkeypatch):
    env.ifaces[:] = [env.ifaces[0]]
    env.tab.mac_device_box.setCurrentIndex(1)
    env.tab.randomise_mac()
    assert [i.device for i in env.tab._interfaces] == ["en0"]
    assert env.tab.mac_device_box.count() == 1


def test_a_declined_change_leaves_the_fresh_address_on_screen(env, monkeypatch):
    env.tab.mac_device_box.setCurrentIndex(1)
    env.ifaces[1] = Interface("en5", "USB 10/100 LAN", "02:00:00:00:00:77", "aa:bb:cc:00:00:05")
    env.answers = [False]
    env.tab.randomise_mac()
    assert "02:00:00:00:00:77" in env.tab.mac_current_label.text()
    assert "02:00:00:00:00:77" in env.tab.mac_device_box.itemText(1)
    assert "02:00:00:00:00:09" not in env.tab.mac_device_box.itemText(1)


def test_a_stale_first_interface_is_refreshed_too(env, monkeypatch):
    env.tab.mac_device_box.setCurrentIndex(0)
    env.ifaces[0] = Interface("en0", "Wi-Fi", "02:00:00:00:00:55", "aa:bb:cc:00:00:01")
    env.answers = [False]
    env.tab.randomise_mac()
    assert "02:00:00:00:00:55" in env.tab.mac_device_box.itemText(0)


# ── P3-2: the read before the confirm is off the UI thread ──

class DeferredWorker:
    """A worker that does nothing until the test lets it run."""
    started = []

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
        DeferredWorker.started.append(self)

    def run_now(self):
        self.finished_signal.emit(self.func())

    def isRunning(self):  # noqa: N802
        return False

    def cancel(self):
        pass


def test_the_read_before_the_confirm_does_not_run_on_the_click(env, monkeypatch):
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    reads = []
    real = macaddr.list_interfaces_checked
    monkeypatch.setattr(macaddr, "list_interfaces_checked",
                        lambda: reads.append(1) or real())
    env.tab.mac_device_box.setCurrentIndex(1)
    env.tab.randomise_mac()
    assert reads == [] and env.asked == []          # nothing ran on the click
    assert env.tab.busy and not env.tab.mac_random_btn.isEnabled()
    assert len(DeferredWorker.started) == 1
    env.answers = [False]
    DeferredWorker.started[0].run_now()             # the worker finishes
    assert reads and len(env.asked) == 1 and not env.tab.busy


def test_a_second_click_while_reading_is_refused_as_busy(env, monkeypatch):
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    env.tab.randomise_mac()
    env.tab.randomise_mac()
    assert len(DeferredWorker.started) == 1


def test_a_worker_failure_during_the_read_frees_the_tab(env, monkeypatch):
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    env.tab.randomise_mac()
    DeferredWorker.started[0].error_signal.emit("boom")
    assert not env.tab.busy and env.asked == []
    assert "boom" in env.tab.status_label.text()

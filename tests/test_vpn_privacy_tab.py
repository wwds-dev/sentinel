"""Tunnel › Privacy: MAC changer, Tor and proxy chain — gate, secrets."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

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


def test_refresh_reports_a_failed_listing_and_a_crash(env, monkeypatch):
    env.read_error = "no networksetup"
    env.tab.refresh_interfaces()
    assert env.tab._interfaces == []
    # Say why the list is empty, not "No network interfaces found".
    for label in (env.tab.mac_current_label, env.tab.status_label):
        assert "Could not list network interfaces: no networksetup" in label.text()

    def bug():
        raise RuntimeError("bug")
    env.read_error = ""
    env.tab.refresh_interfaces()
    monkeypatch.setattr(macaddr, "list_interfaces_checked", bug)
    env.tab.refresh_interfaces()       # off the UI thread a crash is reported, not raised
    assert "Failed: bug" in env.tab.status_label.text()
    assert [i.device for i in env.tab._interfaces] == ["en0", "en5"]   # the last good list stays
    assert not env.tab.busy and env.tab.mac_refresh_btn.isEnabled()


def test_refresh_lists_the_interfaces_off_the_ui_thread(env, monkeypatch):
    """Review P3-4b: networksetup runs in a worker; the tab is busy until it answers."""
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    env.ifaces.pop()
    env.tab.refresh_interfaces()
    assert len(DeferredWorker.started) == 1
    assert env.tab.busy and not env.tab.mac_refresh_btn.isEnabled()
    assert env.tab.mac_device_box.count() == 2      # nothing changes before the answer
    popups = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: popups.append(a)))
    env.tab.refresh_interfaces()                    # a second refresh while busy is dropped quietly
    assert len(DeferredWorker.started) == 1 and popups == []
    DeferredWorker.started[0].running = True        # a listing in flight is not privileged work
    assert not env.tab.has_privileged_work()
    DeferredWorker.started[0].running = False
    DeferredWorker.started[0].run_now()
    assert env.tab.mac_device_box.count() == 1


def test_refresh_keeps_the_selected_interface_and_drops_a_failed_list(env, monkeypatch):
    env.tab.mac_device_box.setCurrentIndex(1)
    env.tab.refresh_interfaces()
    assert env.tab.mac_device_box.currentData() == "en5"
    # A failed listing shows nothing, even if the command left partial rows behind.
    monkeypatch.setattr(macaddr, "list_interfaces_checked", lambda: (list(env.ifaces), "timed out"))
    env.tab.refresh_interfaces()
    assert env.tab._interfaces == [] and env.tab.mac_device_box.count() == 0
    assert not env.tab.busy and env.tab.mac_refresh_btn.isEnabled()


def test_a_finished_mac_change_refreshes_the_picker(env, monkeypatch):
    """After a gated action the listing is read again (review of 98814c5 P3)."""
    def change(*a, **k):
        env.ifaces[0] = Interface("en0", "Wi-Fi", "02:00:00:00:00:42", "aa:bb:cc:00:00:01")
        return True, "en0 is now 02:00:00:00:00:42"
    monkeypatch.setattr(macaddr, "set_mac", change)
    env.answers = [True]
    env.tab.randomise_mac()
    assert "02:00:00:00:00:42" in env.tab.mac_device_box.itemText(0)


def test_a_worker_still_running_is_parked_not_dropped(env, monkeypatch):
    """Review of 98814c5 P2: a result slot that starts the next worker must not
    drop the last reference to a thread still returning from run()."""
    parked = []
    monkeypatch.setattr(vpn_gate, "park", lambda t, *c: parked.append(t))
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    monkeypatch.setattr(macaddr, "set_mac", lambda *a, **k: (True, "ok"))
    env.answers = [True]
    env.tab.randomise_mac()                       # the fresh read
    read = DeferredWorker.started[0]
    read.running = True                           # still returning from run() when it emits
    read.run_now()                                # -> confirm -> gated set_mac worker
    gated = DeferredWorker.started[1]
    gated.running = True
    gated.run_now()                               # -> _finish -> refresh starts a listing
    assert parked == [read, gated] and len(DeferredWorker.started) == 3
    assert env.tab._worker is DeferredWorker.started[2]


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
            ended = Signal()
        self._s = S()
        self.finished_signal = self._s.fin
        self.error_signal = self._s.err
        self.finished = self._s.ended       # what ui.workers.park connects to

    def start(self):
        DeferredWorker.started.append(self)

    def run_now(self):
        self.finished_signal.emit(self.func())

    running = False

    def isRunning(self):  # noqa: N802
        return self.running

    def isFinished(self):  # noqa: N802
        return not self.running

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
    # Off before the read (Tor not ours, chain empty), so off after it.
    assert not env.tab.tor_stop_btn.isEnabled() and not env.tab.chain_test_btn.isEnabled()
    assert env.tab.mac_random_btn.isEnabled()


# ── Review of 5a81ab9 ──

def test_buttons_that_were_off_stay_off_after_a_declined_read(env, monkeypatch):
    # P2-1: Tor is installed but not running, the chain is empty.
    assert not env.tab.tor_stop_btn.isEnabled() and not env.tab.chain_test_btn.isEnabled()
    env.answers = [False]
    env.tab.randomise_mac()
    assert not env.tab.tor_stop_btn.isEnabled()
    assert not env.tab.tor_newnym_btn.isEnabled()
    assert not env.tab.chain_test_btn.isEnabled()
    assert env.tab.tor_start_btn.isEnabled() and env.tab.mac_random_btn.isEnabled()


def test_buttons_that_were_off_stay_off_after_a_failed_read(env, monkeypatch):
    env.read_error = "networksetup timed out"
    env.tab.randomise_mac()
    assert not env.tab.tor_stop_btn.isEnabled() and not env.tab.chain_test_btn.isEnabled()
    env.read_error = ""
    env.tab.mac_device_box.setCurrentIndex(0)
    env.tab.restore_mac()                              # en0 is unchanged: nothing to do
    assert not env.tab.tor_stop_btn.isEnabled()


def test_a_running_read_is_not_an_administrator_action(env, monkeypatch):
    # P3-1: quitting during a read must not claim an admin dialog is open.
    DeferredWorker.started = []
    DeferredWorker.running = True
    try:
        monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
        env.tab.randomise_mac()
        assert env.tab.busy and not env.tab.has_privileged_work()
    finally:
        DeferredWorker.running = False


def test_a_running_change_still_counts_as_privileged_work(env, monkeypatch):
    DeferredWorker.started = []
    DeferredWorker.running = True
    try:
        monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
        monkeypatch.setattr(macaddr, "set_mac", env.net("set_mac", (True, "ok")))
        env.answers = [True]
        env.tab.randomise_mac()
        DeferredWorker.started[0].finished_signal.emit((env.ifaces[0], ""))   # read done
        assert len(DeferredWorker.started) == 2           # the change itself
        assert env.tab.has_privileged_work()
    finally:
        DeferredWorker.running = False


def test_the_interface_picker_is_off_while_reading(env, monkeypatch):
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    env.tab.randomise_mac()
    assert not env.tab.mac_device_box.isEnabled()
    env.answers = [False]
    DeferredWorker.started[0].finished_signal.emit((env.ifaces[0], ""))
    assert env.tab.mac_device_box.isEnabled()


def test_test_chain_stays_off_after_an_approved_action_with_an_empty_chain(env, monkeypatch):
    # Review 2 of 45d7fe2, P3-1: _finish re-enabled every button, chain included.
    monkeypatch.setattr(tor, "start", lambda: (True, "started"))
    assert not env.tab.chain_test_btn.isEnabled()
    env.answers = [True]
    env.tab.start_tor()
    assert not env.tab.busy
    assert not env.tab.chain_test_btn.isEnabled()


# ── Review of bb4d35d ──

def test_an_action_keeps_the_selected_hop(env, monkeypatch):
    # P3-1: recomputing Test chain after an action rebuilt the table and lost the row.
    _add_hop(env.tab, host="10.0.0.1")
    _add_hop(env.tab, host="10.0.0.2")
    env.tab.chain_table.setCurrentCell(1, 0)
    monkeypatch.setattr(tor, "start", lambda: (True, "started"))
    env.answers = [True]
    env.tab.start_tor()                            # approved: _finish path
    assert env.tab.chain_table.currentRow() == 1
    env.tab.randomise_mac()                        # declined: read path
    assert env.tab.chain_table.currentRow() == 1


def test_test_chain_comes_back_on_after_an_approved_action_with_a_valid_chain(env, monkeypatch):
    # P3-2
    _add_hop(env.tab)
    assert env.tab.chain_test_btn.isEnabled()
    monkeypatch.setattr(tor, "start", lambda: (True, "started"))
    env.answers = [True]
    env.tab.start_tor()
    assert env.tab.chain_test_btn.isEnabled()
    assert env.tab.mac_random_btn.isEnabled()      # _after_finish ran with the tab free


def test_a_mode_change_while_an_action_runs_keeps_test_chain_off(env, monkeypatch):
    # P3-3 (a): the mode box is off during an action, but a mode change that
    # still arrives (setCurrentText emits on a disabled box) must not light it.
    _add_hop(env.tab)
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    monkeypatch.setattr(tor, "start", lambda: (True, "started"))
    env.answers = [True]
    env.tab.start_tor()
    assert env.tab.busy and not env.tab.chain_test_btn.isEnabled()
    assert not env.tab.chain_mode_box.isEnabled()
    env.tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)
    assert not env.tab.chain_test_btn.isEnabled()
    DeferredWorker.started[0].run_now()            # the action finishes ...
    assert not env.tab.chain_test_btn.isEnabled()  # ... and the interface listing starts
    DeferredWorker.started[1].run_now()
    assert not env.tab.busy and env.tab.chain_test_btn.isEnabled()


def test_a_chain_with_a_bad_hop_keeps_test_chain_off(env):
    # P3-3 (b): e.g. a hand-edited saved chain.
    _add_hop(env.tab)
    env.tab._chain.hops[0].host = ""
    env.tab._refresh_chain()
    assert not env.tab.chain_test_btn.isEnabled()
    assert "Problems: hop 1: no host" in env.tab.chain_summary_label.text()
    env.tab._controls_changed()
    assert not env.tab.chain_test_btn.isEnabled()


# ── Review of d5d1d35 ──

CHAIN_EDITS = ("hop_add_btn", "hop_add_tor_btn", "hop_remove_btn", "hop_up_btn",
               "hop_down_btn", "chain_mode_box")


def test_test_chain_probes_the_hops_that_were_confirmed(env, monkeypatch):
    # P2-1: the probe used the live chain, so a hop added mid-probe joined the route.
    _add_hop(env.tab, host="10.0.0.1")
    _add_hop(env.tab, host="10.0.0.2")
    seen = []
    monkeypatch.setattr(proxychain, "http_get_through",
                        lambda hops, *a, **k: (seen.append([h.host for h in hops]),
                                               "203.0.113.7\n")[1])
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    env.answers = [True]
    env.tab.test_chain()
    assert "10.0.0.1" in env.asked[0][1] and "10.9.9.9" not in env.asked[0][1]
    _add_hop(env.tab, host="10.9.9.9")             # an edit that still gets through
    env.tab.chain_table.setCurrentCell(0, 0)
    env.tab.remove_hop()
    DeferredWorker.started[0].run_now()
    assert seen == [["10.0.0.1", "10.0.0.2"]]
    assert "203.0.113.7" in env.tab.status_label.text()


def test_the_chain_cannot_be_edited_while_an_action_runs(env, monkeypatch):
    DeferredWorker.started = []
    monkeypatch.setattr(vpn_gate, "CallWorker", DeferredWorker)
    monkeypatch.setattr(tor, "start", lambda: (True, "started"))
    env.answers = [True]
    env.tab.start_tor()
    assert not any(getattr(env.tab, name).isEnabled() for name in CHAIN_EDITS)
    DeferredWorker.started[0].run_now()
    DeferredWorker.started[1].run_now()            # the interface listing after it
    assert not env.tab.busy
    assert all(getattr(env.tab, name).isEnabled() for name in CHAIN_EDITS)


def test_a_mode_change_keeps_the_selected_hop(env):
    # P3-1: _mode_changed rebuilt the table.
    _add_hop(env.tab, host="10.0.0.1")
    _add_hop(env.tab, host="10.0.0.2")
    env.tab.chain_table.setCurrentCell(1, 0)
    env.tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)
    assert env.tab.chain_table.currentRow() == 1
    assert proxychain.load_chain().mode == proxychain.DYNAMIC


def test_a_chain_without_proxychains_dns_can_still_be_tested(env, monkeypatch):
    # P3-2: the probe does not use proxychains' DNS setting, so it is no blocker.
    _add_hop(env.tab)
    env.tab._chain.proxy_dns = False
    env.tab._refresh_chain()
    assert env.tab.chain_test_btn.isEnabled()
    assert "Problems" not in env.tab.chain_summary_label.text()
    # ... and pressing it runs the probe (review of 729fd02, P3-4: test_chain's own check).
    monkeypatch.setattr(proxychain, "probe", env.net("probe", proxychain.ProbeResult(True, "1.2.3.4", 5)))
    env.answers = [True]
    env.tab.test_chain()
    assert env.calls == ["probe"]


@pytest.mark.parametrize("saved", ['{"hops": [{"host": 5}]}', '{"hops": [{"port": "1080"}]}', "[]",
                                   '{"mode": 3}'])
def test_a_malformed_saved_chain_still_builds_the_tab(env, saved):
    # P3-3: a wrongly typed field used to raise in PrivacyTab().
    path = proxychain.chain_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(saved)
    tab = PrivacyTab()
    assert tab._chain.hops == [] and not tab.chain_test_btn.isEnabled()
    assert "Could not read the saved proxy chain" in tab.status_label.text()


# ── Review of 729fd02 ──

def _save_raw(data: bytes) -> None:
    path = proxychain.chain_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_a_mode_change_refreshes_the_summary(env):
    # P3-1: a hand-edited unknown mode, then a real one picked in the box.
    _save_raw(b'{"mode": "xyz", "hops": [{"host": "10.0.0.1", "port": 1080}]}')
    tab = PrivacyTab()
    assert "unknown chain mode" in tab.chain_summary_label.text()
    assert not tab.chain_test_btn.isEnabled()
    tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)
    assert "Problems" not in tab.chain_summary_label.text()
    assert tab.chain_test_btn.isEnabled()


def test_an_unreadable_saved_chain_survives_the_first_edit(env):
    # P3-2: one bad field emptied the chain silently and the next save overwrote it.
    original = (b'{"hops": [{"host": "10.0.0.1", "username": "u", "password": "s3cret"},'
                b' {"host": "10.0.0.2", "port": 1080.0}]}')
    _save_raw(original)
    tab = PrivacyTab()
    text = tab.status_label.text()
    assert "Could not read the saved proxy chain" in text and "proxychain.json.unreadable" in text
    assert "s3cret" not in text and "1080.0" not in text
    tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)      # an edit saves the chain
    aside = proxychain.chain_path().with_name("proxychain.json.unreadable")
    assert aside.read_bytes() == original
    assert proxychain.load_chain().mode == proxychain.DYNAMIC


# ── Review of 380227c ──

def test_the_unreadable_chain_notice_outlives_the_status_line(env):
    # P3-3: a restore offer or a failed listing at start overwrites the status line.
    _save_raw(b"{broken")
    tab = PrivacyTab()
    folder = str(proxychain.chain_path().parent)
    tab._say("Not run: confirmation declined.")
    summary = tab.chain_summary_label.text()
    assert "Could not read the saved proxy chain" in summary
    assert folder in summary and "proxychain.json.unreadable" in summary
    tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)      # an edit saves a new chain
    assert "Could not read" not in tab.chain_summary_label.text()


def test_a_failed_save_keeps_the_unreadable_chain_notice(env, monkeypatch):
    _save_raw(b"{broken")
    tab = PrivacyTab()
    monkeypatch.setattr(proxychain, "save_chain",
                        lambda chain: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)
    assert "Could not read the saved proxy chain" in tab.chain_summary_label.text()
    assert "Could not save the chain" in tab.status_label.text()


# ── Review of e573ca5 ──

def test_the_unreadable_chain_notice_survives_a_restart(env):
    # P3-1: the move happens at start-up, so the next launch must still say where it went.
    _save_raw(b"{broken")
    PrivacyTab()                                                  # first launch moves it
    tab = PrivacyTab()                                            # the next launch
    folder = str(proxychain.chain_path().parent)
    expected = ("An earlier saved proxy chain could not be read and was moved to "
                f"proxychain.json.unreadable in {folder}. The chain is empty; whatever the old file "
                "held, including any proxy passwords, is still in proxychain.json.unreadable.")
    # Review of 924d1f5, P3-3: this launch read nothing, so it must not say it could not read.
    assert expected in tab.chain_summary_label.text() and expected in tab.status_label.text()
    assert "Could not read" not in tab.chain_summary_label.text()
    tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)         # the first save ends it
    assert "earlier saved proxy chain" not in tab.chain_summary_label.text()
    assert "earlier saved proxy chain" not in PrivacyTab().chain_summary_label.text()


def test_the_first_launch_after_a_bad_chain_still_says_why(env):
    # The new wording is for later launches only; the launch that moves it names the reason.
    _save_raw(b"{broken")
    summary = PrivacyTab().chain_summary_label.text()
    assert "Could not read the saved proxy chain" in summary and "not valid JSON" in summary
    assert "earlier saved proxy chain" not in summary
    assert summary.endswith("The chain is empty; whatever the old file held, including any "
                            "proxy passwords, is still in proxychain.json.unreadable.")


def test_a_new_bad_chain_beside_an_earlier_copy_still_says_why(env):
    # Review of 80dda42, P3-1: an older copy must not hide this launch's reason.
    _save_raw(b"{broken")
    PrivacyTab()                                                  # moved to .unreadable
    _save_raw(b"{broken again")
    summary = PrivacyTab().chain_summary_label.text()             # moved to -2
    assert "not valid JSON" in summary and "proxychain.json.unreadable-2" in summary
    assert "earlier saved proxy chain" not in summary
    later = PrivacyTab().chain_summary_label.text()
    assert "earlier saved proxy chain" in later and "proxychain.json.unreadable-2 in" in later


@pytest.mark.parametrize("why", ["move-fails", "too-many-copies"])
def test_a_chain_left_in_place_is_not_called_kept(env, monkeypatch, why):
    # Review of 80dda42, P3-2: only a moved copy holds the old file; one left in
    # place is replaced by the next edit, so the notice must not point at it,
    # nor at an older copy beside it.
    _save_raw(b"{broken")
    proxychain.chain_path().with_name("proxychain.json.unreadable").write_text("{older")
    if why == "move-fails":
        monkeypatch.setattr(Path, "replace",
                            lambda self, target: (_ for _ in ()).throw(OSError(30, "Read-only")))
    else:
        monkeypatch.setattr(proxychain, "MAX_UNREADABLE_COPIES", 1)
    summary = PrivacyTab().chain_summary_label.text()
    assert "will be replaced when you edit the chain" in summary
    assert summary.endswith("The chain is empty.") and "still in" not in summary


def test_an_empty_chain_file_is_not_said_to_hold_hops(env):
    # Review of 4bf546d, P3-1: a zero-byte file holds no hops; name the copy instead.
    _save_raw(b"")
    summary = PrivacyTab().chain_summary_label.text()
    assert "hops" not in summary and "still in proxychain.json.unreadable." in summary


needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="mode 000 does not stop root")


@needs_permissions
@pytest.mark.parametrize("edit", ["mode", "add", "add-tor", "remove"])
def test_an_unsearchable_chain_folder_builds_the_tab_and_never_replaces_the_chain(
        env, monkeypatch, tmp_path, edit):
    # Review of 4bf546d, P3-3: the tab must build and say why, never raise.
    # Review of 19efce1, P2-1: that chain was never read, so no edit may replace it.
    # Only the chain's folder is locked; the MAC state beside it is a separate item.
    folder = tmp_path / "chain"
    folder.mkdir()
    saved = '{"hops": [{"host": "10.0.0.1", "username": "u", "password": "s3cret"}]}'
    (folder / "proxychain.json").write_text(saved)
    monkeypatch.setattr(proxychain, "chain_path", lambda: folder / "proxychain.json")
    folder.chmod(0o000)
    try:
        tab = PrivacyTab()
        summary = tab.chain_summary_label.text()
        if edit == "mode":
            tab.chain_mode_box.setCurrentText(proxychain.DYNAMIC)
        elif edit == "add":
            tab.hop_host_input.setText("10.0.0.9")
            tab.hop_port_input.setValue(1080)
            tab.add_hop()
        elif edit == "add-tor":
            tab.add_tor_hop()
        else:
            tab._chain.hops.append(proxychain.ProxyHop(host="10.0.0.9"))
            tab._refresh_chain()
            tab.chain_table.selectRow(0)
            tab.remove_hop()
        mode_after = stat.S_IMODE(folder.stat().st_mode)
    finally:
        folder.chmod(0o700)
    assert "its folder could not be opened (Permission denied)" in summary
    assert "edits are not saved" in summary and "still in" not in summary
    assert mode_after == 0 and (folder / "proxychain.json").read_text() == saved
    assert tab.status_label.text().startswith("Not saved: the saved proxy chain could not be read")
    assert "its folder could not be opened" in tab.chain_summary_label.text()   # notice kept


def test_a_failed_save_is_not_followed_by_added(env, monkeypatch):
    # Found with review of 19efce1, P2-1: "Added ..." used to overwrite the reason.
    tab = PrivacyTab()
    monkeypatch.setattr(proxychain, "save_chain",
                        lambda chain: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    tab.hop_host_input.setText("10.0.0.9")
    tab.hop_port_input.setValue(1080)
    tab.add_hop()
    assert tab.status_label.text().startswith("Could not save the chain")
    tab.add_tor_hop()
    assert tab.status_label.text().startswith("Could not save the chain")

"""Mullvad control through its CLI: stubbed, no account number, no connect while logged out."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from services import vpn_app_control as control
from services import vpn_apps
from tests.test_vpn_privacy_tab import SyncWorker
from ui.panels import vpn_gate

ACCOUNT_OUT = ("Mullvad account:    1234 5678 9012 3456\n"
               "Expires at:         2027-03-01 12:00:00 UTC\n"
               "Device name:        gentle pigeon\n")
RELAYS = """Albania (al)
\tTirana (tia) @ 41.33°N, 19.82°E
\t\tal-tia-wg-001 (31.171.153.66) - WireGuard, hosted by x
Sweden (se)
\tGothenburg (got) @ 57.71°N, 11.97°E
\t\tse-got-wg-001 (185.213.154.66) - WireGuard
\tStockholm (sto) @ 59.33°N, 18.07°E
"""


class FakeMullvad:
    """A stateful `mullvad` CLI. Records every argv it is given."""

    def __init__(self, logged_in=True, states=("Disconnected",), account=ACCOUNT_OUT):
        self.logged_in, self.account = logged_in, account
        self.states, self.calls = list(states), []
        self.state_index = 0

    def __call__(self, argv, timeout=2.0):
        self.calls.append(argv[1:])
        sub = argv[1:]
        if sub == ["status"]:
            line = self.states[min(self.state_index, len(self.states) - 1)]
            self.state_index += 1
            return 0, line + "\n"
        if sub == ["account", "get"]:
            return (0, self.account) if self.logged_in else (0, "Not logged in on any account\n")
        if sub == ["relay", "list"]:
            return 0, RELAYS
        if sub[:3] == ["relay", "set", "location"]:
            return 0, "Relay constraints updated\n"
        if sub == ["connect"]:
            return 0, "Connecting\n"
        if sub == ["disconnect"]:
            self.states = ["Disconnected"]
            self.state_index = 0
            return 0, "Disconnecting\n"
        return 2, "unknown command"


@pytest.fixture(autouse=True)
def cli_present(monkeypatch):
    monkeypatch.setattr(vpn_apps, "mullvad_cli", lambda: "/usr/local/bin/mullvad")
    monkeypatch.setattr(vpn_apps, "MULLVAD_APP", vpn_apps.Path("/nonexistent/Mullvad VPN.app"))


# ── account: the number is never kept ──

def test_account_number_never_leaves_the_account_check():
    logged_in, summary = control.mullvad_account(FakeMullvad())
    assert logged_in is True and "2027-03-01" in summary
    assert "1234" not in summary and "9012" not in summary and "pigeon" not in summary


def test_hide_account_numbers():
    assert "1234" not in control.hide_account_numbers("account 1234 5678 9012 3456 failed")
    assert control.hide_account_numbers("Connected to se-got-wg-001") == "Connected to se-got-wg-001"


def test_logged_out_and_silent_daemon_are_told_apart():
    assert control.mullvad_account(FakeMullvad(logged_in=False))[0] is False
    assert control.mullvad_account(lambda argv, timeout=2.0: (1, ""))[0] is None


# ── locations ──

def test_locations_come_from_relay_list_countries_and_cities():
    labels = dict(control.parse_locations(RELAYS))
    assert labels["Sweden"] == ("se",)
    assert labels["Sweden › Gothenburg"] == ("se", "got")
    assert labels["Albania › Tirana"] == ("al", "tia")
    assert not any("wg-001" in k for k in labels)


@pytest.mark.parametrize("parts, ok", [
    (("se",), True), (("se", "got"), True), (("se", "got", "se-got-wg-001"), True),
    (("se; rm -rf",), False), (("",), False), (("S E",), False), ((), False),
    (("se", "got", "x-y", "extra"), False),
])
def test_only_plain_location_codes_are_accepted(parts, ok):
    assert control.valid_location(parts) is ok


# ── connect / disconnect ──

def test_connect_is_refused_while_logged_out_and_runs_no_connect():
    cli = FakeMullvad(logged_in=False)
    ok, message = control.mullvad_connect(run=cli, sleep=lambda s: None)
    assert ok is False and "block all traffic" in message
    assert ["connect"] not in cli.calls and ["relay", "set", "location", "se"] not in cli.calls


def test_connect_sets_the_location_then_connects_and_waits_for_connected():
    cli = FakeMullvad(states=("Disconnected", "Connecting to se-got-wg-001...",
                              "Connected to se-got-wg-001 in Gothenburg, Sweden"))
    slept = []
    ok, message = control.mullvad_connect(("se", "got"), run=cli, sleep=slept.append)
    assert ok and "connected" in message and "Gothenburg" in message
    order = [c for c in cli.calls if c[0] in ("relay", "connect")]
    assert order == [["relay", "set", "location", "se", "got"], ["connect"]]
    assert slept                                      # it waited, it did not assume


def test_connect_reports_a_blocked_state_instead_of_success():
    cli = FakeMullvad(states=("Disconnected", "Blocked: failed to set up firewall"))
    ok, message = control.mullvad_connect(run=cli, sleep=lambda s: None)
    assert ok is False and "blocked" in message.lower() and "not verified" in message


def test_an_invalid_location_runs_nothing():
    cli = FakeMullvad()
    ok, _ = control.mullvad_connect(("se; reboot",), run=cli, sleep=lambda s: None)
    assert ok is False and ["connect"] not in cli.calls
    assert not any(c[:1] == ["relay"] and c[1:2] == ["set"] for c in cli.calls)


def test_nothing_ever_logs_in_or_passes_an_account_number():
    cli = FakeMullvad(states=("Disconnected", "Connected to x"))
    control.mullvad_connect(("se",), run=cli, sleep=lambda s: None)
    control.mullvad_disconnect(run=cli, sleep=lambda s: None)
    assert all(c[:2] != ["account", "login"] and c[:1] != ["login"] for c in cli.calls)
    assert all(isinstance(part, str) and "1234" not in part for c in cli.calls for part in c)


def test_disconnect_waits_for_disconnected():
    cli = FakeMullvad(states=("Connected to x",))
    ok, message = control.mullvad_disconnect(run=cli, sleep=lambda s: None)
    assert ok and "disconnected" in message


# ── the tab ──

class TabEnv:
    def __init__(self):
        self.answers, self.asked, self.warnings, self.connects = [], [], [], []


@pytest.fixture()
def tab_env(monkeypatch):
    from ui.panels.vpn_apps_tab import AppsTab
    e = TabEnv()
    monkeypatch.setattr(vpn_gate, "CallWorker", SyncWorker)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, *a, **k: (e.asked.append((title, text)) or
                                              (QMessageBox.Yes if (e.answers.pop(0) if e.answers else False)
                                               else QMessageBox.No))))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda p, t, m, *a, **k: e.warnings.append(m)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    cli = FakeMullvad(states=("Disconnected", "Connected to se-got-wg-001 in Gothenburg, Sweden"))
    monkeypatch.setattr(vpn_apps, "_run", cli)
    monkeypatch.setattr(control, "mullvad_account", lambda run=None: (
        True, "Logged in; account time runs until 2027-03-01 12:00:00 UTC."))
    monkeypatch.setattr(control, "mullvad_locations", lambda run=None: control.parse_locations(RELAYS))
    monkeypatch.setattr(control, "mullvad_connect",
                        lambda location=None, **k: (e.connects.append(location) or (True, "Mullvad VPN: connected")))
    monkeypatch.setattr(control, "mullvad_disconnect", lambda **k: (e.connects.append("off") or (True, "Mullvad VPN: disconnected.")))
    monkeypatch.setattr(vpn_apps, "detect_mullvad",
                        lambda run=None: vpn_apps.VpnApp("mullvad", "Mullvad VPN", True, vpn_apps.DISCONNECTED))
    e.tab = AppsTab()
    e.tab.refresh()
    return e


def test_the_tab_lists_locations_and_never_shows_an_account_number(tab_env):
    box = tab_env.tab.mullvad_location_box
    assert box.itemText(0) == "Mullvad's current location"
    assert box.findText("Sweden › Gothenburg") >= 0
    assert "1234" not in tab_env.tab.mullvad_account_label.text()


def test_declining_the_confirmation_connects_nothing(tab_env):
    tab_env.tab.connect_mullvad()
    assert tab_env.connects == []
    title, text = tab_env.asked[0]
    assert "Mullvad" in text and "stack" in text


def test_confirming_connects_with_the_chosen_location(tab_env):
    box = tab_env.tab.mullvad_location_box
    box.setCurrentIndex(box.findText("Sweden › Gothenburg"))
    tab_env.answers = [True]
    tab_env.tab.connect_mullvad()
    assert tab_env.connects == [("se", "got")]
    assert "connected" in tab_env.tab.status_label.text().lower()


def test_logged_out_is_refused_before_any_question(tab_env, monkeypatch):
    monkeypatch.setattr(control, "mullvad_account", lambda run=None: (False, "Not logged in. Log in with the Mullvad app first."))
    tab_env.tab.connect_mullvad()
    assert tab_env.asked == [] and tab_env.connects == []
    assert "block all traffic" in tab_env.warnings[0]


def test_disconnect_asks_first(tab_env):
    tab_env.tab.disconnect_mullvad()
    assert tab_env.connects == [] and tab_env.asked
    tab_env.answers = [True]
    tab_env.tab.disconnect_mullvad()
    assert tab_env.connects == ["off"]

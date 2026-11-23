"""Tunnel's approved layout (docs/specs/agent-layouts.md, screen 3).

One status card (connection, kill switch, local and public IP, DNS leak) and
five tabs. A regrouping: every control that existed before stays reachable.
Nothing here presses Connect, Arm or any privileged control.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

# Every control the panel built before the regrouping (commit edda52e).
EXISTING = [
    "connect_profile_box", "import_config_btn", "connect_btn", "disconnect_btn",
    "connection_status_label", "arm_ks_btn", "disarm_ks_btn", "verify_ks_btn",
    "kill_switch_status_label", "local_ip_label", "refresh_local_ip_btn",
    "public_ip_label", "check_public_ip_btn", "dns_leak_label", "dns_leak_btn",
    "mode_box", "protocol_box", "host_input", "ssh_input", "lan_input", "egress_input",
    "profile_box", "profile_refresh_btn", "inspect_config_btn", "external_checks_box",
    "diagnostics_btn", "action_box", "preview_action_btn", "build_btn",
    "question_input", "run_btn", "diagnostics_view", "advisor_box", "config_box",
    "action_view", "config_inspection_view", "execution_view", "privacy_tab",
    "servers_tab", "apps_tab", "status_label", "clear_btn",
]
# Shown only while something runs, or only in one connection state.
STATEFUL = {"connect_btn", "disconnect_btn"}
OLD_BUTTON_TEXTS = {
    "Import config…", "Connect", "Disconnect", "Arm", "Disarm", "Verify", "Refresh",
    "Check public IP", "Run test", "Reload", "Inspect config…", "Stop Check",
    "Check Connection", "Preview", "Build Config", "Ask Advisor", "Stop", "Clear",
    "Auto-route",
}
SAFETY_TEXT = (
    "Connect starts WireGuard/OpenVPN; traffic protection is not verified. It may ask for your "
    "administrator password. Example country profiles are templates and will "
    "not connect until you import a real config or set a real endpoint. "
    "Diagnostics and Action Preview remain read-only."
)


@pytest.fixture(scope="module")
def win():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QMessageBox
    import main

    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    try:
        yield main.GodAI()
    finally:
        QMessageBox.warning, QMessageBox.question = saved


@pytest.fixture
def tunnel(win):
    panel = win.panels["vpn"]
    yield panel
    panel._set_tunnel_state(None)
    panel.tabs.setCurrentIndex(0)


def _bring_forward(panel, widget) -> None:
    """Open whichever tab (and inner tab) holds the widget, as a user would."""
    for index in range(panel.tabs.count()):
        page = panel.tabs.widget(index)
        if page is widget or page.isAncestorOf(widget):
            panel.tabs.setCurrentIndex(index)
            for inner in (panel.diagnose_results, panel.advisor_results):
                for j in range(inner.count()):
                    sub = inner.widget(j)
                    if sub is widget or sub.isAncestorOf(widget):
                        inner.setCurrentIndex(j)
            return


@pytest.mark.parametrize("name", EXISTING)
def test_every_existing_control_is_still_reachable(tunnel, name):
    widget = getattr(tunnel, name)
    assert tunnel.status_card.isAncestorOf(widget) or tunnel.tabs.isAncestorOf(widget) \
        or tunnel.isAncestorOf(widget)
    _bring_forward(tunnel, widget)
    if name not in STATEFUL:
        assert widget.isVisibleTo(tunnel), name


def test_no_button_text_was_lost(tunnel):
    texts = {b.text() for b in tunnel.findChildren(QPushButton)}
    assert OLD_BUTTON_TEXTS <= texts


def test_provider_and_model_are_reachable_from_change_model(tunnel):
    tunnel._fill_change_model_menu()
    titles = [a.text() for a in tunnel.change_model_menu.actions()]
    assert "Provider" in titles and "Model" in titles and "Auto-route" in titles


def test_status_card_holds_what_is_live_now(tunnel):
    for name in ("connect_profile_box", "connect_btn", "disconnect_btn",
                 "arm_ks_btn", "disarm_ks_btn", "verify_ks_btn", "local_ip_label",
                 "check_public_ip_btn", "dns_leak_btn", "safety_banner"):
        assert tunnel.status_card.isAncestorOf(getattr(tunnel, name)), name
    layout = tunnel.layout()
    assert layout.indexOf(tunnel.status_card) < layout.indexOf(tunnel.tabs)


def test_five_tabs_hold_the_rest(tunnel):
    assert [tunnel.tabs.tabText(i) for i in range(tunnel.tabs.count())] == [
        "Diagnose", "Servers", "Privacy", "VPN apps", "Advisor"]
    page = {tunnel.tabs.tabText(i): tunnel.tabs.widget(i) for i in range(tunnel.tabs.count())}
    for name in ("diagnostics_btn", "preview_action_btn", "inspect_config_btn",
                 "execution_view", "action_view", "config_inspection_view"):
        assert page["Diagnose"].isAncestorOf(getattr(tunnel, name)), name
    for name in ("mode_box", "host_input", "servers_tab"):
        assert page["Servers"].isAncestorOf(getattr(tunnel, name)), name
    assert page["Privacy"] is tunnel.privacy_tab
    assert page["VPN apps"] is tunnel.apps_tab
    for name in ("question_input", "run_btn", "build_btn", "advisor_box", "config_box"):
        assert page["Advisor"].isAncestorOf(getattr(tunnel, name)), name


@pytest.mark.parametrize("view", [
    "diagnostics_view", "action_view", "config_inspection_view", "execution_view",
    "advisor_box", "config_box",
])
def test_show_result_brings_the_view_to_the_front(tunnel, view):
    tunnel.tabs.setCurrentWidget(tunnel.privacy_tab)
    tunnel.show_result(getattr(tunnel, view))
    assert tunnel.current_view() is getattr(tunnel, view)


def test_connect_and_disconnect_follow_the_local_tunnel_view(tunnel):
    # Not known yet: both stay, so Disconnect is reachable whenever Sentinel cannot tell.
    tunnel._set_tunnel_state(None)
    assert tunnel.connect_btn.isVisibleTo(tunnel) and tunnel.disconnect_btn.isVisibleTo(tunnel)
    tunnel._on_ip_snapshot({"local": {"primary": "192.0.2.10", "tunnels": [], "interfaces": {}}})
    assert tunnel.connect_btn.isVisibleTo(tunnel)
    assert not tunnel.disconnect_btn.isVisibleTo(tunnel)
    assert tunnel.connection_state_label.text() == "No tunnel interface up"
    tunnel._on_ip_snapshot({"local": {"primary": "192.0.2.10", "tunnels": ["utun4"],
                                      "interfaces": {"utun4": ["10.64.0.2"]}}})
    assert tunnel.disconnect_btn.isVisibleTo(tunnel)
    assert not tunnel.connect_btn.isVisibleTo(tunnel)
    assert tunnel.connection_dot.property("state") == "up"
    # An interface being up is not protection; the label must not claim it.
    text = tunnel.connection_state_label.text().lower()
    assert "utun4" in text and "protect" not in text and "secure" not in text


def test_a_snapshot_without_local_data_leaves_the_state_alone(tunnel):
    tunnel._set_tunnel_state(["utun4"])
    tunnel._on_ip_snapshot({"public": {"ip": "Error: offline"}})
    assert tunnel.disconnect_btn.isVisibleTo(tunnel)


def test_one_primary_button_on_every_tab(tunnel):
    tunnel._set_tunnel_state([])
    for index in range(tunnel.tabs.count()):
        tunnel.tabs.setCurrentIndex(index)
        primaries = [b for b in tunnel.findChildren(QPushButton)
                     if b.objectName() == "PrimaryAction" and b.isVisibleTo(tunnel)]
        assert primaries == [tunnel.connect_btn], tunnel.tabs.tabText(index)


def test_safety_notice_keeps_its_claim_and_full_text(tunnel):
    banner = tunnel.safety_banner
    assert "traffic protection is not verified" in banner.text()
    assert banner.toolTip() == SAFETY_TEXT
    assert banner.isVisibleTo(tunnel)


# ── GUI review 2026-11-23: five visual points on the Tunnel render ──────────
def _scroll_body(tab):
    from PySide6.QtWidgets import QScrollArea
    return tab.findChild(QScrollArea).widget()


@pytest.mark.parametrize("name", ["privacy_tab", "servers_tab", "apps_tab"])
def test_scrolling_tabs_fit_the_centre_column(tunnel, name):
    """Privacy's hop and chain rows pushed its text past the right edge.

    The centre column is about 1020 px at 1600x1000; 800 leaves the same
    room as test_bloodhound_fits_the_centre_column.
    """
    assert _scroll_body(getattr(tunnel, name)).minimumSizeHint().width() <= 800


def test_disabled_buttons_keep_a_visible_frame(win):
    sheet = QApplication.instance().styleSheet() or win.styleSheet()
    start = sheet.index("QPushButton:disabled {")
    block = sheet[start:sheet.index("}", start)]
    assert "border: 1px solid #262d29" in block


def test_action_preview_picker_is_labelled(tunnel):
    assert tunnel.action_box_label.text() == "Action:"
    box = tunnel.action_box.parentWidget()
    assert box.isAncestorOf(tunnel.action_box_label)


def test_nested_result_tabs_are_a_lighter_strip(win):
    sheet = QApplication.instance().styleSheet() or win.styleSheet()
    assert "QTabWidget#VPNDiagnoseResults > QTabBar::tab:selected" in sheet


def test_servers_tab_scrolls_once_with_the_intro_first(tunnel):
    from PySide6.QtWidgets import QScrollArea
    servers_page = tunnel.servers_tab.parentWidget()
    # The deployment form is inside the servers tab's own scroll body ...
    body = _scroll_body(tunnel.servers_tab)
    setup = tunnel.mode_box.parentWidget()
    assert body.isAncestorOf(setup)
    # ... directly under the "Servers you own" intro, and no second scroll.
    first, second = (body.layout().itemAt(i).widget() for i in (0, 1))
    assert first.text().startswith("Servers you own")
    assert second is setup
    assert len(servers_page.findChildren(QScrollArea)) == 1


def test_port_boxes_show_a_dash_without_a_site(tunnel):
    tab = tunnel.servers_tab
    tab._set_editor_enabled(False)
    for box in (tab.wg_port_input, tab.ovpn_port_input):
        # Qt shows the special text while the value is at its minimum.
        assert box.value() == box.minimum() and box.specialValueText() == "—"
    tab._set_editor_enabled(True)
    assert tab.wg_port_input.specialValueText() == ""   # a real port shows as is
    tab._set_editor_enabled(False)


@pytest.mark.parametrize("name", ["privacy_tab", "servers_tab", "apps_tab"])
def test_tab_status_line_only_shows_a_real_status(tunnel, name):
    """"Idle" showed twice on VPN apps: in the tab and in the panel footer."""
    tab = getattr(tunnel, name)
    tab._say("Idle")
    assert not tab.status_label.isVisibleTo(tab)
    tab._say("Mullvad connected.")
    assert tab.status_label.isVisibleTo(tab)
    tab._say("Idle")

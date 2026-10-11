"""Tunnel › VPN Apps: start and stop the Mullvad app's tunnel; show ProtonVPN's state.

Tunnel drives Mullvad's own command-line tool and nothing else. Login stays in
the Mullvad app; Sentinel never sees or keeps the account number. Connect and
Disconnect change this Mac's network, so each is gated like Connect on the main
tab: a confirmation that defaults to No, an off-thread worker, and nothing
logged.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from services import vpn_app_control as control
from services import vpn_apps
from ui.panels.vpn_gate import GatedTab
from ui.widgets import MenuComboBox

MULLVAD_INFO = (
    "Drives the Mullvad app through its own command-line tool. Log in with the Mullvad "
    "app first: Sentinel never asks for, passes or keeps your account number. Connecting "
    "while logged out would make Mullvad block all traffic, so Connect refuses then. "
    "Mullvad runs its own firewall rules while connected, so Tunnel's kill switch stays "
    "off for it."
)
PROTON_INFO = (
    "Shows ProtonVPN's state only. Tunnel cannot start or stop it: macOS accepts a start "
    "request for Proton's VPN service, but Proton's extension ignores it (tested), so "
    "connect and disconnect in the ProtonVPN app. Tunnel never sees your Proton login. A "
    "Proton account is tied to your identity (an email address and a payment record). To "
    "use a Proton WireGuard or OpenVPN file yourself, download it from your Proton account "
    "and use Import config… on the main tab."
)
CURRENT_LOCATION = "Mullvad's current location"


class AppsTab(GatedTab):
    """The VPN Apps tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._build()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    def _info(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("InfoLine")
        return label

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)

        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("AppsStatus")

        box = QGroupBox("Mullvad VPN")
        v = QVBoxLayout(box)
        v.addWidget(self._info(MULLVAD_INFO))
        self.mullvad_state_label = QLabel("")
        self.mullvad_state_label.setWordWrap(True)
        self.mullvad_account_label = QLabel("")
        self.mullvad_account_label.setWordWrap(True)
        v.addWidget(self.mullvad_state_label)
        v.addWidget(self.mullvad_account_label)
        row = QHBoxLayout()
        self.mullvad_location_box = MenuComboBox()
        self.mullvad_location_box.addItem(CURRENT_LOCATION, None)
        self.mullvad_refresh_btn = QPushButton("Refresh")
        self.mullvad_refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self.mullvad_location_box, 1)
        row.addWidget(self.mullvad_refresh_btn)
        v.addLayout(row)
        row = QHBoxLayout()
        self.mullvad_connect_btn = QPushButton("Connect…")
        self.mullvad_connect_btn.clicked.connect(self.connect_mullvad)
        self.mullvad_disconnect_btn = QPushButton("Disconnect…")
        self.mullvad_disconnect_btn.clicked.connect(self.disconnect_mullvad)
        row.addWidget(self.mullvad_connect_btn)
        row.addWidget(self.mullvad_disconnect_btn)
        v.addLayout(row)
        layout.addWidget(box)

        proton_box = QGroupBox("ProtonVPN")
        p = QVBoxLayout(proton_box)
        p.addWidget(self._info(PROTON_INFO))
        self.proton_state_label = QLabel("")
        self.proton_state_label.setWordWrap(True)
        p.addWidget(self.proton_state_label)
        layout.addWidget(proton_box)

        layout.addWidget(self.status_label)
        layout.addStretch(1)

    # ── state ──
    def _set_enabled(self, enabled: bool) -> None:
        installed = getattr(self, "_mullvad_installed", False)
        for button in (self.mullvad_connect_btn, self.mullvad_disconnect_btn):
            button.setEnabled(enabled and installed)
        self.mullvad_refresh_btn.setEnabled(enabled)

    def _after_finish(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        proton = vpn_apps.detect_proton()
        self.proton_state_label.setText(proton.line())
        app = vpn_apps.detect_mullvad()
        self._mullvad_installed = bool(app.installed and vpn_apps.mullvad_cli())
        self.mullvad_state_label.setText(app.line())
        if not self._mullvad_installed:
            self.mullvad_account_label.setText(
                "Install the Mullvad app from mullvad.net to use it here."
                if not app.installed else "")
            self._set_enabled(not self.busy)
            return
        _logged_in, summary = control.mullvad_account()
        self.mullvad_account_label.setText(summary)
        if self.mullvad_location_box.count() <= 1:
            for label, args in control.mullvad_locations():
                self.mullvad_location_box.addItem(label, args)
        self._set_enabled(not self.busy)

    # ── actions ──
    def connect_mullvad(self) -> None:
        location = self.mullvad_location_box.currentData()
        where = self.mullvad_location_box.currentText()
        logged_in, account = control.mullvad_account()
        if logged_in is not True:
            self._refuse("mullvad-connect", "mullvad",
                         account + " Connecting while logged out makes Mullvad block all "
                         "traffic on this Mac.")
            return
        text = (f"Connect Mullvad VPN ({where})?\n\n"
                "Mullvad will route all of this Mac's traffic through its own tunnel and run "
                "its own firewall rules. If a Tunnel profile is connected, two VPNs will "
                "stack.\n\nTunnel only runs Mullvad's command-line tool; it does not see your "
                "account number. Traffic protection is not verified afterwards: check the exit "
                "address under Your IP & DNS.")
        self._gated("mullvad-connect", "mullvad", "Connect Mullvad", text,
                    lambda: control.mullvad_connect(location))

    def disconnect_mullvad(self) -> None:
        text = ("Disconnect Mullvad VPN?\n\nTraffic returns to your ordinary network in the "
                "clear, unless Mullvad's own lockdown mode is on (then it stays blocked until "
                "you reconnect).")
        self._gated("mullvad-disconnect", "mullvad", "Disconnect Mullvad", text,
                    control.mullvad_disconnect)

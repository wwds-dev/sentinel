"""Tunnel › Privacy: hardware address, Tor and proxy chain.

Every action here that changes the machine or contacts something is gated the
way Connect is: a review that says exactly what will happen, a confirmation
that defaults to No, an off-thread worker, and one line in the Tunnel audit log
for every attempt (declined and refused ones included). Nothing on this tab
talks to an AI provider, and proxy passwords are never shown or logged.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from agents.vpn_agent.services import macaddr, proxychain, tor
from agents.vpn_agent.services.socks_client import KINDS, ProxyHop
from services import vpn_execution
from ui.panels.vpn_gate import GatedTab

MAC_INFO = (
    "Changes the address this Mac presents on the local network only. It is one hop: "
    "your router still sees your traffic, and the change lasts until restart or Restore. "
    "On Wi-Fi, also switch Private Wi-Fi Address to Off for the network, or macOS may "
    "override it."
)
TOR_INFO = (
    "Runs a local Tor client on 127.0.0.1:9250 (client only, never a relay). Tor hides "
    "where you connect from, not who you are: logins, cookies and browser fingerprints "
    "still identify you, and Tor plus a VPN is not anonymity by itself."
)
CHAIN_INFO = (
    "An ordered list of proxies. Test chain speaks SOCKS directly, so it is a real "
    "end-to-end test. The proxychains wrapper barely works on macOS: System Integrity "
    "Protection makes Apple's own tools (such as /usr/bin/curl) ignore it, which looks "
    "identical to success. Always check the exit address."
)


class PrivacyTab(GatedTab):
    """The Privacy tab. ``busy`` is True while a companion action is running."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._interfaces: list = []
        self._chain = proxychain.load_chain()
        self._build()
        self._refresh_chain()
        self._show_mac()
        self.refresh_tor()

    def showEvent(self, event) -> None:
        # Listing interfaces and probing the Tor port touch the OS; do it when
        # the tab is first shown, not while the app is starting.
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh_interfaces()
            self.refresh_tor()

    # ── construction ──
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
        self.status_label.setObjectName("PrivacyStatus")

        # Hardware address
        mac_box = QGroupBox("Hardware address")
        ml = QVBoxLayout(mac_box)
        ml.addWidget(self._info(MAC_INFO))
        row = QHBoxLayout()
        self.mac_device_box = QComboBox()
        self.mac_device_box.currentIndexChanged.connect(self._show_mac)
        self.mac_refresh_btn = QPushButton("Refresh")
        self.mac_refresh_btn.clicked.connect(self.refresh_interfaces)
        row.addWidget(self.mac_device_box, 1)
        row.addWidget(self.mac_refresh_btn)
        ml.addLayout(row)
        self.mac_current_label = QLabel("")
        self.mac_hardware_label = QLabel("")
        ml.addWidget(self.mac_current_label)
        ml.addWidget(self.mac_hardware_label)
        row = QHBoxLayout()
        self.mac_mode_box = QComboBox()
        self.mac_mode_box.addItems(list(macaddr.MODES))
        self.mac_random_btn = QPushButton("Randomise…")
        self.mac_random_btn.clicked.connect(self.randomise_mac)
        self.mac_restore_btn = QPushButton("Restore hardware address…")
        self.mac_restore_btn.clicked.connect(self.restore_mac)
        for w in (self.mac_mode_box, self.mac_random_btn, self.mac_restore_btn):
            row.addWidget(w)
        ml.addLayout(row)
        ml.addWidget(self._info(macaddr.wifi_private_address_note()))
        layout.addWidget(mac_box)

        # Tor
        tor_box = QGroupBox("Tor")
        tl = QVBoxLayout(tor_box)
        tl.addWidget(self._info(TOR_INFO))
        self.tor_state_label = QLabel("")
        tl.addWidget(self.tor_state_label)
        row = QHBoxLayout()
        self.tor_start_btn = QPushButton("Start Tor…")
        self.tor_stop_btn = QPushButton("Stop")
        self.tor_check_btn = QPushButton("Check…")
        self.tor_newnym_btn = QPushButton("New identity")
        self.tor_start_btn.clicked.connect(self.start_tor)
        self.tor_stop_btn.clicked.connect(self.stop_tor)
        self.tor_check_btn.clicked.connect(self.check_tor)
        self.tor_newnym_btn.clicked.connect(self.new_tor_identity)
        for w in (self.tor_start_btn, self.tor_stop_btn, self.tor_check_btn,
                  self.tor_newnym_btn):
            row.addWidget(w)
        tl.addLayout(row)
        layout.addWidget(tor_box)

        # Proxy chain
        chain_box = QGroupBox("Proxy chain")
        cl = QVBoxLayout(chain_box)
        cl.addWidget(self._info(CHAIN_INFO))
        self.chain_table = QTableWidget(0, 6)
        self.chain_table.setHorizontalHeaderLabels(
            ["#", "Type", "Host", "Port", "User", "Label"])
        self.chain_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.chain_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.chain_table.setMinimumHeight(120)
        cl.addWidget(self.chain_table)
        row = QHBoxLayout()
        self.hop_kind_box = QComboBox()
        self.hop_kind_box.addItems(list(KINDS))
        self.hop_host_input = QLineEdit()
        self.hop_host_input.setPlaceholderText("host")
        self.hop_port_input = QSpinBox()
        self.hop_port_input.setRange(1, 65535)
        self.hop_port_input.setValue(1080)
        self.hop_user_input = QLineEdit()
        self.hop_user_input.setPlaceholderText("user (optional)")
        self.hop_pass_input = QLineEdit()
        self.hop_pass_input.setEchoMode(QLineEdit.Password)
        self.hop_pass_input.setPlaceholderText("password (optional)")
        self.hop_label_input = QLineEdit()
        self.hop_label_input.setPlaceholderText("label")
        for w in (self.hop_kind_box, self.hop_host_input, self.hop_port_input,
                  self.hop_user_input, self.hop_pass_input, self.hop_label_input):
            row.addWidget(w)
        cl.addLayout(row)
        row = QHBoxLayout()
        self.hop_add_btn = QPushButton("Add hop")
        self.hop_add_tor_btn = QPushButton("Add Tor as a hop")
        self.hop_remove_btn = QPushButton("Remove selected")
        self.hop_up_btn = QPushButton("Move up")
        self.hop_down_btn = QPushButton("Move down")
        self.chain_mode_box = QComboBox()
        self.chain_mode_box.addItems(list(proxychain.MODES))
        self.chain_mode_box.setCurrentText(self._chain.mode)
        self.chain_mode_box.currentTextChanged.connect(self._mode_changed)
        self.chain_test_btn = QPushButton("Test chain…")
        self.chain_conf_btn = QPushButton("Write proxychains.conf")
        self.hop_add_btn.clicked.connect(self.add_hop)
        self.hop_add_tor_btn.clicked.connect(self.add_tor_hop)
        self.hop_remove_btn.clicked.connect(self.remove_hop)
        self.hop_up_btn.clicked.connect(lambda: self.move_hop(-1))
        self.hop_down_btn.clicked.connect(lambda: self.move_hop(1))
        self.chain_test_btn.clicked.connect(self.test_chain)
        self.chain_conf_btn.clicked.connect(self.write_conf)
        for w in (self.hop_add_btn, self.hop_add_tor_btn, self.hop_remove_btn,
                  self.hop_up_btn, self.hop_down_btn, self.chain_mode_box,
                  self.chain_test_btn, self.chain_conf_btn):
            row.addWidget(w)
        cl.addLayout(row)
        self.chain_summary_label = QLabel("")
        self.chain_summary_label.setWordWrap(True)
        cl.addWidget(self.chain_summary_label)
        layout.addWidget(chain_box)

        layout.addWidget(self.status_label)
        layout.addStretch(1)

    @staticmethod
    def _info(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("InfoLine")
        return label

    def _after_finish(self) -> None:
        self.refresh_interfaces()
        self.refresh_tor()

    def _set_enabled(self, enabled: bool) -> None:
        for w in (self.mac_random_btn, self.mac_restore_btn, self.mac_refresh_btn,
                  self.tor_start_btn, self.tor_stop_btn, self.tor_check_btn,
                  self.tor_newnym_btn, self.chain_test_btn):
            w.setEnabled(enabled)

    # ── hardware address ──
    def refresh_interfaces(self) -> None:
        current = self.mac_device_box.currentData()
        try:
            self._interfaces = macaddr.list_interfaces()
        except Exception:
            self._interfaces = []
        self.mac_device_box.blockSignals(True)
        self.mac_device_box.clear()
        for iface in self._interfaces:
            self.mac_device_box.addItem(iface.describe(), iface.device)
        index = self.mac_device_box.findData(current)
        self.mac_device_box.setCurrentIndex(max(index, 0))
        self.mac_device_box.blockSignals(False)
        self._show_mac()

    def _selected_interface(self):
        device = self.mac_device_box.currentData()
        for iface in self._interfaces:
            if iface.device == device:
                return iface
        return None

    def _show_mac(self) -> None:
        iface = self._selected_interface()
        has = iface is not None
        self.mac_random_btn.setEnabled(has and not self.busy)
        self.mac_restore_btn.setEnabled(has and not self.busy)
        if not has:
            self.mac_current_label.setText("No network interfaces found.")
            self.mac_hardware_label.setText("")
            return
        self.mac_current_label.setText(
            f"In use now: {iface.current or 'unknown'}"
            + ("  (changed)" if iface.spoofed else ""))
        self.mac_hardware_label.setText(f"Hardware: {iface.hardware or 'unknown'}")

    def randomise_mac(self) -> None:
        iface = self._selected_interface()
        if iface is None:
            self._refuse("mac-set", "", "Choose a network interface first.")
            return
        new = macaddr.random_mac(self.mac_mode_box.currentText(), like=iface.hardware)
        self._set_mac(iface, new, "Randomise hardware address")

    def restore_mac(self) -> None:
        iface = self._selected_interface()
        if iface is None:
            self._refuse("mac-set", "", "Choose a network interface first.")
            return
        if not iface.hardware:
            self._refuse("mac-set", iface.device,
                         f"The permanent address of {iface.device} is unknown.")
            return
        if not iface.spoofed:
            self._say(f"{iface.device} is already using its hardware address.")
            return
        self._set_mac(iface, iface.hardware, "Restore hardware address")

    def _set_mac(self, iface, new: str, title: str) -> None:
        wifi = ("\n\nWi-Fi will be switched off and on, so you will drop off your "
                "network and may need to rejoin it.") if iface.is_wifi else ""
        text = (f"{title}?\n\nInterface: {iface.describe()}\n"
                f"{iface.current or 'unknown'}  →  {new}{wifi}\n\n"
                "macOS will ask for your administrator password. The change lasts "
                "until restart or until you restore it.")
        self._gated("mac-set", f"{iface.device} {iface.current}->{new}", title, text,
                    lambda: macaddr.set_mac(iface.device, new))

    # ── Tor ──
    def refresh_tor(self) -> None:
        installed = tor.is_installed()
        running = tor.is_running() if installed else False
        busy = self.busy
        if not installed:
            self.tor_state_label.setText(f"Tor is not installed. Install it with: {tor.install_hint()}")
        elif running:
            self.tor_state_label.setText(f"Running on 127.0.0.1:{tor.SOCKS_PORT}. {tor.bootstrap_progress()}")
        else:
            self.tor_state_label.setText("Installed, not running.")
        self.tor_start_btn.setEnabled(installed and not running and not busy)
        self.tor_stop_btn.setEnabled(running and not busy)
        self.tor_check_btn.setEnabled(running and not busy)
        self.tor_newnym_btn.setEnabled(running and not busy)

    def start_tor(self) -> None:
        if not tor.is_installed():
            self._refuse("tor-start", "tor", f"Tor is not installed. {tor.install_hint()}")
            return
        text = ("Start a local Tor client?\n\nIt listens on 127.0.0.1 only "
                f"(SOCKS {tor.SOCKS_PORT}), connects out to the Tor network, and "
                "keeps its state in Sentinel's data folder. It is a client, never a "
                "relay or exit.\n\nTor hides where you connect from, not who you are. "
                "It is not anonymity on its own.")
        self._gated("tor-start", "tor", "Start Tor", text, tor.start)

    def stop_tor(self) -> None:
        self._gated("tor-stop", "tor", "Stop Tor", None, tor.stop)

    def new_tor_identity(self) -> None:
        self._gated("tor-newnym", "tor", "New Tor identity", None, tor.new_identity)

    def check_tor(self) -> None:
        text = (f"Check that traffic really exits through Tor?\n\nThis connects through "
                f"Tor to {tor.CHECK_HOST} (run by the Tor Project) and reads one page. "
                "That site sees the Tor exit address, not yours.")
        self._gated("tor-check", tor.CHECK_HOST, "Check Tor", text, tor.check)

    # ── proxy chain ──
    def _save_chain(self) -> None:
        try:
            proxychain.save_chain(self._chain)
        except OSError as exc:
            self._say(f"Could not save the chain: {exc}")

    def _mode_changed(self, mode: str) -> None:
        self._chain.mode = mode
        self._save_chain()
        self._refresh_chain()

    def _refresh_chain(self) -> None:
        self.chain_table.setRowCount(0)
        for number, hop in enumerate(self._chain.hops, start=1):
            row = self.chain_table.rowCount()
            self.chain_table.insertRow(row)
            for col, value in enumerate(
                    [str(number), hop.kind, hop.host, str(hop.port), hop.username, hop.label]):
                self.chain_table.setItem(row, col, QTableWidgetItem(value))
        problems = [p for p in self._chain.problems() if "DNS" not in p]
        self.chain_summary_label.setText(
            f"Chain: {self._chain.describe()}"
            + (("\nProblems: " + "; ".join(problems)) if problems else ""))
        self.chain_test_btn.setEnabled(bool(self._chain.hops) and not problems and not self.busy)

    def add_hop(self) -> None:
        hop = ProxyHop(kind=self.hop_kind_box.currentText(),
                       host=self.hop_host_input.text().strip(),
                       port=self.hop_port_input.value(),
                       username=self.hop_user_input.text().strip(),
                       password=self.hop_pass_input.text(),
                       label=self.hop_label_input.text().strip())
        problems = hop.problems()
        if problems:
            self._say("Not added: " + "; ".join(problems))
            return
        self._chain.hops.append(hop)
        self.hop_pass_input.clear()
        self._save_chain()
        self._refresh_chain()
        self._say(f"Added {hop.describe()}.")

    def add_tor_hop(self) -> None:
        self._chain.hops.append(tor.hop())
        self._save_chain()
        self._refresh_chain()
        self._say("Added Tor as a hop.")

    def _selected_row(self) -> int:
        rows = self.chain_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def remove_hop(self) -> None:
        row = self._selected_row()
        if row < 0:
            self._say("Select a hop first.")
            return
        del self._chain.hops[row]
        self._save_chain()
        self._refresh_chain()

    def move_hop(self, delta: int) -> None:
        row = self._selected_row()
        target = row + delta
        if row < 0 or not (0 <= target < len(self._chain.hops)):
            return
        hops = self._chain.hops
        hops[row], hops[target] = hops[target], hops[row]
        self._save_chain()
        self._refresh_chain()
        self.chain_table.selectRow(target)

    def write_conf(self) -> None:
        try:
            path = proxychain.write_proxychains_conf(self._chain)
        except OSError as exc:
            self._say(f"Could not write the file: {exc}")
            return
        self._say(f"Wrote {path} (owner-only; it may contain proxy passwords).")

    def test_chain(self) -> None:
        problems = [p for p in self._chain.problems() if "DNS" not in p]
        if problems:
            self._refuse("chain-probe", self._chain.describe(), "; ".join(problems))
            return
        hops = "\n".join(f"  {i}. {h.describe()}" for i, h in enumerate(self._chain.hops, 1))
        text = (f"Test the proxy chain?\n\nThis sends one plain-HTTP request to "
                f"{proxychain.PROBE_HOST} through, in order:\n{hops}\n\n"
                "Every hop sees that a request is made, and the last one sees its "
                "content. The site sees the chain's exit address.")
        chain = self._chain
        self._gated("chain-probe", proxychain.PROBE_HOST, "Test chain", text,
                    lambda: (lambda r: (r.ok, r.summary()))(proxychain.probe(chain)))

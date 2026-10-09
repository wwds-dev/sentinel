"""Tunnel › Servers: VPN servers you own — sites, peers, export, backup.

Local, reversible edits (create a site, add a peer, change a port) run without a
prompt. Anything that makes issued configs stop working, writes a private key
to disk or onto the screen, or deletes a site asks first (default No), runs off
the interface thread and is audited. Private keys, the CA key and backup
passphrases are never shown in text, logged or sent to a model; the QR dialog
renders from memory and nothing it shows is saved.
"""

from __future__ import annotations

import io
import json
from collections import deque
from datetime import datetime, timezone

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from agents.vpn_agent.server import backup, deploy, export, paths, provision, render, store
from agents.vpn_agent.server.model import MODE_NATIVE, MODE_REMOTE, MODES, OBFS_MODES
from ui.panels.vpn_gate import GatedTab
from ui.widgets import MenuComboBox
from services import vpn_execution

SERVERS_INFO = (
    "Servers you own. Sentinel creates the keys and certificates locally, renders the "
    "server and client configs, and (in the next step) deploys them over SSH to a VPS "
    "or to this Mac. Private keys never leave your data folder except in a config "
    "you export; treat an exported file like a password."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _marks_path():
    return paths.state_dir() / "backup-marks.json"


def _read_marks() -> dict:
    try:
        return json.loads(_marks_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_backup(site_name: str) -> None:
    marks = _read_marks()
    marks[paths.slugify(site_name)] = _now()
    paths.ensure_private_dir(_marks_path().parent)
    paths.write_private(_marks_path(), json.dumps(marks))


def has_backup(site_name: str) -> bool:
    """True when a backup was written, or exported configs exist, for the site."""
    if paths.slugify(site_name) in _read_marks():
        return True
    d = paths.exports_dir(site_name)
    return d.is_dir() and any(d.iterdir())


class ServersTab(GatedTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._site = None
        self._loaded = False
        self._lines: deque[str] = deque()
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._drain_output)
        self._build()
        self._set_editor_enabled(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh_sites()

    # ── construction ──
    @staticmethod
    def _info(text: str) -> QLabel:
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
        layout.addWidget(self._info(SERVERS_INFO))
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)

        sites_box = QGroupBox("Sites")
        sl = QVBoxLayout(sites_box)
        self.site_list = QListWidget()
        self.site_list.setMaximumHeight(110)
        self.site_list.currentTextChanged.connect(self._select_site)
        sl.addWidget(self.site_list)
        row = QHBoxLayout()
        self.new_name_input = QLineEdit()
        self.new_name_input.setPlaceholderText("new site name")
        self.new_mode_box = MenuComboBox()
        self.new_mode_box.addItems(list(MODES))
        self.new_endpoint_input = QLineEdit()
        self.new_endpoint_input.setPlaceholderText("public address or DNS name")
        self.create_btn = QPushButton("Create site")
        self.create_btn.clicked.connect(self.create_site)
        self.restore_btn = QPushButton("Import backup…")
        self.restore_btn.clicked.connect(self.restore_backup)
        for w in (self.new_name_input, self.new_mode_box, self.new_endpoint_input,
                  self.create_btn, self.restore_btn):
            row.addWidget(w)
        sl.addLayout(row)
        layout.addWidget(sites_box)

        self.editor_box = QGroupBox("Site settings")
        form = QFormLayout(self.editor_box)
        self.endpoint_input = QLineEdit()
        self.wg_port_input = QSpinBox()
        self.wg_port_input.setRange(1, 65535)
        self.ovpn_port_input = QSpinBox()
        self.ovpn_port_input.setRange(1, 65535)
        self.openvpn_check = QCheckBox("Also offer OpenVPN (TCP fallback)")
        self.full_tunnel_check = QCheckBox("Send all traffic through the server")
        self.lan_input = QLineEdit()
        self.dns_input = QLineEdit()
        self.obfs_box = MenuComboBox()
        self.obfs_box.addItems(list(OBFS_MODES))
        self.ssh_host_input = QLineEdit()
        self.ssh_user_input = QLineEdit()
        self.ssh_port_input = QSpinBox()
        self.ssh_port_input.setRange(1, 65535)
        self.ssh_key_input = QLineEdit()
        self.ssh_key_input.setPlaceholderText("path to a private key file (optional)")
        for label, w in (("Endpoint", self.endpoint_input), ("WireGuard port", self.wg_port_input),
                         ("OpenVPN port", self.ovpn_port_input), ("", self.openvpn_check),
                         ("", self.full_tunnel_check), ("LAN routes (comma)", self.lan_input),
                         ("DNS (comma)", self.dns_input), ("Obfuscation", self.obfs_box),
                         ("SSH host", self.ssh_host_input), ("SSH user", self.ssh_user_input),
                         ("SSH port", self.ssh_port_input), ("SSH key file", self.ssh_key_input)):
            form.addRow(label, w)
        self.save_btn = QPushButton("Save settings")
        self.save_btn.clicked.connect(self.save_settings)
        form.addRow("", self.save_btn)
        self.problems_label = QLabel("")
        self.problems_label.setWordWrap(True)
        form.addRow("", self.problems_label)
        layout.addWidget(self.editor_box)

        self.peers_box = QGroupBox("Peers (devices)")
        pl = QVBoxLayout(self.peers_box)
        self.peer_table = QTableWidget(0, 4)
        self.peer_table.setHorizontalHeaderLabels(["Name", "Address", "Enabled", "OpenVPN"])
        self.peer_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.peer_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.peer_table.setSelectionMode(QTableWidget.SingleSelection)
        self.peer_table.setMinimumHeight(110)
        pl.addWidget(self.peer_table)
        row = QHBoxLayout()
        self.peer_name_input = QLineEdit()
        self.peer_name_input.setPlaceholderText("new device name")
        self.add_peer_btn = QPushButton("Add peer")
        self.toggle_peer_btn = QPushButton("Enable / disable")
        self.rotate_peer_btn = QPushButton("Rotate keys…")
        self.remove_peer_btn = QPushButton("Remove…")
        self.export_btn = QPushButton("Export files…")
        self.qr_btn = QPushButton("Show QR…")
        self.add_peer_btn.clicked.connect(self.add_peer)
        self.toggle_peer_btn.clicked.connect(self.toggle_peer)
        self.rotate_peer_btn.clicked.connect(self.rotate_peer)
        self.remove_peer_btn.clicked.connect(self.remove_peer)
        self.export_btn.clicked.connect(self.export_peer)
        self.qr_btn.clicked.connect(self.show_qr)
        for w in (self.peer_name_input, self.add_peer_btn, self.toggle_peer_btn,
                  self.rotate_peer_btn, self.remove_peer_btn, self.export_btn, self.qr_btn):
            row.addWidget(w)
        pl.addLayout(row)
        layout.addWidget(self.peers_box)

        self.deploy_box = QGroupBox("Deploy")
        dl = QVBoxLayout(self.deploy_box)
        row = QHBoxLayout()
        self.check_ssh_btn = QPushButton("Check SSH…")
        self.preview_btn = QPushButton("Preview deploy")
        self.deploy_btn = QPushButton("Deploy…")
        self.status_btn = QPushButton("Server status…")
        self.teardown_btn = QPushButton("Teardown…")
        self.profile_btn = QPushButton("Use for Connect…")
        self.check_ssh_btn.clicked.connect(self.check_ssh)
        self.preview_btn.clicked.connect(self.preview_deploy)
        self.deploy_btn.clicked.connect(self.deploy_site)
        self.status_btn.clicked.connect(self.server_status)
        self.teardown_btn.clicked.connect(self.teardown_site)
        self.profile_btn.clicked.connect(self.register_profile)
        for w in (self.check_ssh_btn, self.preview_btn, self.deploy_btn,
                  self.status_btn, self.teardown_btn, self.profile_btn):
            row.addWidget(w)
        dl.addLayout(row)
        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setMinimumHeight(140)
        self.output_view.setPlaceholderText(
            "Preview and live deploy output appear here, with keys redacted.")
        dl.addWidget(self.output_view)
        layout.addWidget(self.deploy_box)

        row = QHBoxLayout()
        self.backup_btn = QPushButton("Back up this site…")
        self.delete_btn = QPushButton("Delete site…")
        self.backup_btn.clicked.connect(self.backup_site)
        self.delete_btn.clicked.connect(self.delete_site)
        row.addWidget(self.backup_btn)
        row.addWidget(self.delete_btn)
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def _site_widgets(self):
        return (self.editor_box, self.peers_box, self.deploy_box, self.backup_btn, self.delete_btn)

    def _set_editor_enabled(self, on: bool) -> None:
        for w in self._site_widgets():
            w.setEnabled(on)

    def _set_enabled(self, enabled: bool) -> None:
        self.create_btn.setEnabled(enabled)
        self.restore_btn.setEnabled(enabled)
        self._set_editor_enabled(enabled and self._site is not None)

    def _after_finish(self) -> None:
        self.refresh_sites(keep=self._site.name if self._site else "")

    # ── site list ──
    def refresh_sites(self, keep: str = "") -> None:
        names = store.list_sites()
        self.site_list.blockSignals(True)
        self.site_list.clear()
        self.site_list.addItems(names)
        self.site_list.blockSignals(False)
        if keep in names:
            self.site_list.setCurrentRow(names.index(keep))
            self._select_site(keep)
        else:
            self._select_site("")

    def _select_site(self, name: str) -> None:
        self._site = None
        if name:
            try:
                self._site = store.load_site(name)
            except Exception as exc:   # corrupt or insecure file: show, don't crash
                self._say(f"Could not open site {name!r}: {exc}")
        self._fill_editor()

    def _fill_editor(self) -> None:
        s = self._site
        self._set_editor_enabled(s is not None and not self.busy)
        self.peer_table.setRowCount(0)
        if s is None:
            self.problems_label.setText("")
            return
        self.endpoint_input.setText(s.endpoint_host)
        self.wg_port_input.setValue(s.wg_port)
        self.ovpn_port_input.setValue(s.ovpn_port)
        self.openvpn_check.setChecked(s.enable_openvpn)
        self.full_tunnel_check.setChecked(s.full_tunnel)
        self.lan_input.setText(", ".join(s.lan_routes))
        self.dns_input.setText(", ".join(s.dns))
        self.obfs_box.setCurrentText(s.obfuscation)
        self.ssh_host_input.setText(s.ssh.host)
        self.ssh_user_input.setText(s.ssh.user)
        self.ssh_port_input.setValue(s.ssh.port)
        self.ssh_key_input.setText(s.ssh.identity_file)
        self.ssh_host_input.setEnabled(s.mode == MODE_REMOTE)
        self._refresh_peers()
        problems = s.validate()
        self.problems_label.setText(
            "Before deploying, fix: " + "; ".join(problems) if problems else "Ready to deploy.")

    def _refresh_peers(self) -> None:
        self.peer_table.setRowCount(0)
        for peer in self._site.peers:
            row = self.peer_table.rowCount()
            self.peer_table.insertRow(row)
            for col, value in enumerate([peer.name, peer.address4,
                                         "yes" if peer.enabled else "no",
                                         "yes" if peer.has_openvpn else "no"]):
                self.peer_table.setItem(row, col, QTableWidgetItem(value))

    def _selected_peer(self):
        row = self.peer_table.currentRow()
        if self._site is None or not (0 <= row < len(self._site.peers)):
            return None
        return self._site.peers[row]

    # ── local, reversible edits ──
    def create_site(self) -> None:
        name = self.new_name_input.text().strip()
        mode = self.new_mode_box.currentText()
        endpoint = self.new_endpoint_input.text().strip()
        if not name:
            self._say("Give the site a name first.")
            return
        if store.site_exists(name):
            self._say(f"A site named {name!r} already exists; it was not touched.")
            return
        self._gated("site-create", name, "Create site", None,
                    lambda: (provision.init_site(name, mode, endpoint) and True,
                             f"Created {name}. Keys and certificates were generated locally."),
                    after=lambda ok: ok and self.new_name_input.clear())

    def save_settings(self) -> None:
        s = self._site
        if s is None:
            return
        try:
            new_endpoint = self.endpoint_input.text().strip()
            s.wg_port = self.wg_port_input.value()
            s.ovpn_port = self.ovpn_port_input.value()
            s.enable_openvpn = self.openvpn_check.isChecked()
            s.full_tunnel = self.full_tunnel_check.isChecked()
            s.lan_routes = [x.strip() for x in self.lan_input.text().split(",") if x.strip()]
            s.dns = [x.strip() for x in self.dns_input.text().split(",") if x.strip()]
            s.obfuscation = self.obfs_box.currentText()
            s.ssh.host = self.ssh_host_input.text().strip()
            s.ssh.user = self.ssh_user_input.text().strip() or "root"
            s.ssh.port = self.ssh_port_input.value()
            s.ssh.identity_file = self.ssh_key_input.text().strip()
            if new_endpoint != s.endpoint_host:
                provision.set_endpoint(s, new_endpoint)   # also re-issues the server cert
            else:
                store.save_site(s)
        except Exception as exc:
            self._say(f"Not saved: {exc}")
            vpn_execution.record_companion("site-edit", "failed", str(exc), target=s.name)
            return
        vpn_execution.record_companion("site-edit", "succeeded", "settings saved", target=s.name)
        self._say("Settings saved. They apply to the next deploy.")
        self._fill_editor()

    def add_peer(self) -> None:
        s = self._site
        name = self.peer_name_input.text().strip()
        if s is None or not name:
            self._say("Choose a site and give the device a name first.")
            return
        self._gated("peer-add", f"{s.name}/{name}", "Add peer", None,
                    lambda: (bool(provision.add_peer(s, name)), f"Added {name}."),
                    after=lambda ok: ok and self.peer_name_input.clear())

    def toggle_peer(self) -> None:
        s, peer = self._site, self._selected_peer()
        if peer is None:
            self._say("Select a peer first.")
            return
        provision.set_peer_enabled(s, peer.name, not peer.enabled)
        vpn_execution.record_companion("peer-toggle", "succeeded",
                                       f"enabled={peer.enabled}", target=f"{s.name}/{peer.name}")
        self._say(f"{peer.name} is now {'enabled' if peer.enabled else 'disabled'}; "
                  "this applies at the next deploy.")
        self._refresh_peers()

    # ── gated peer actions ──
    def rotate_peer(self) -> None:
        s, peer = self._site, self._selected_peer()
        if peer is None:
            self._say("Select a peer first.")
            return
        text = (f"Rotate the keys of {peer.name}?\n\nThe config this device holds stops "
                "working after the next deploy, and the new one must be delivered to "
                "the device.")
        name = peer.name
        self._gated("peer-rotate", f"{s.name}/{name}", "Rotate peer keys", text,
                    lambda: (bool(provision.rotate_peer_keys(s, name)),
                             f"Rotated keys for {name}."))

    def remove_peer(self) -> None:
        s, peer = self._site, self._selected_peer()
        if peer is None:
            self._say("Select a peer first.")
            return
        text = (f"Remove {peer.name}?\n\nIts address is freed and the config the device "
                "holds stops working once the server is deployed again. Until then it "
                "can still connect.")
        name = peer.name
        self._gated("peer-remove", f"{s.name}/{name}", "Remove peer", text,
                    lambda: (provision.remove_peer(s, name), f"Removed {name}."))

    def export_peer(self) -> None:
        s, peer = self._site, self._selected_peer()
        if peer is None:
            self._say("Select a peer first.")
            return
        directory = paths.exports_dir(s.name)
        text = (f"Export {peer.name}'s config files?\n\nThey contain this device's PRIVATE "
                f"key and are written (owner-only) to:\n{directory}\n\nTreat them like a "
                "password and delete them once the device has imported them.")
        site, p = s, peer

        def run():
            written = export.export_peer(site, p)
            return True, "Wrote: " + ", ".join(f"{k} → {v.name}" for k, v in written.items())
        self._gated("export", f"{s.name}/{peer.name}", "Export config", text, run)

    def show_qr(self) -> None:
        s, peer = self._site, self._selected_peer()
        if peer is None:
            self._say("Select a peer first.")
            return
        text = (f"Show {peer.name}'s WireGuard QR code?\n\nIt encodes this device's PRIVATE "
                "key. Anyone who can see or photograph the screen can use it. It is drawn "
                "from memory and never saved.")
        if not self._ask("Show QR code", text):
            vpn_execution.record_companion("export-qr", "declined",
                                           "Operator declined the confirmation.",
                                           target=f"{s.name}/{peer.name}")
            self._say("Not shown: confirmation declined.")
            return
        try:
            pixmap = self._qr_pixmap(render.wg_client_config(s, peer))
        except Exception as exc:
            vpn_execution.record_companion("export-qr", "failed", str(exc),
                                           target=f"{s.name}/{peer.name}")
            self._say(f"Could not draw the QR code: {exc}")
            return
        if pixmap is None:
            vpn_execution.record_companion("export-qr", "refused", "config too large for a QR code",
                                           target=f"{s.name}/{peer.name}")
            self._say("This config is too large for a QR code. Use Export files instead.")
            return
        vpn_execution.record_companion("export-qr", "succeeded", "QR shown",
                                       target=f"{s.name}/{peer.name}")
        self._show_qr_dialog(peer.name, pixmap)

    @staticmethod
    def _qr_pixmap(config_text: str):
        import segno
        if len(config_text.encode("utf-8")) > export.QR_MAX_BYTES:
            return None
        buffer = io.BytesIO()
        segno.make(config_text, error="m").save(buffer, kind="png", scale=6, border=2)
        pix = QPixmap()
        pix.loadFromData(buffer.getvalue(), "PNG")
        return pix

    def _show_qr_dialog(self, name: str, pixmap) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"WireGuard QR — {name}")
        lay = QVBoxLayout(dialog)
        lay.addWidget(QLabel("Scan with the WireGuard app. Contains a private key; close when done."))
        image = QLabel()
        image.setPixmap(pixmap)
        lay.addWidget(image)
        dialog.exec()

    # ── backup / restore / delete ──
    def _ask_passphrase(self, confirm: bool) -> str | None:
        first, ok = QInputDialog.getText(self, "Backup passphrase", "Passphrase:", QLineEdit.Password)
        if not ok:
            return None
        if confirm:
            second, ok = QInputDialog.getText(self, "Backup passphrase", "Repeat it:", QLineEdit.Password)
            if not ok or second != first:
                return None
        return first

    def _ask_save_path(self, default: str) -> str:
        return QFileDialog.getSaveFileName(self, "Save backup", default, "Sentinel VPN backup (*.vpnbackup)")[0]

    def _ask_open_path(self) -> str:
        return QFileDialog.getOpenFileName(self, "Import backup", "", "Sentinel VPN backup (*.vpnbackup);;All files (*)")[0]

    def _ask_text(self, title: str, prompt: str) -> str | None:
        value, ok = QInputDialog.getText(self, title, prompt)
        return value if ok else None

    def backup_site(self) -> None:
        s = self._site
        if s is None:
            return
        passphrase = self._ask_passphrase(confirm=True)
        if not passphrase:
            vpn_execution.record_companion("backup", "declined", "No (matching) passphrase given.", target=s.name)
            self._say("Not backed up: no matching passphrase.")
            return
        problems = backup.passphrase_problems(passphrase)
        if problems and not self._ask("Weak passphrase", "\n".join(problems) + "\n\nUse it anyway?"):
            vpn_execution.record_companion("backup", "declined", "Weak passphrase refused.", target=s.name)
            self._say("Not backed up: choose a stronger passphrase.")
            return
        path = self._ask_save_path(f"{paths.slugify(s.name)}{backup.SUFFIX}")
        if not path:
            vpn_execution.record_companion("backup", "declined", "No file chosen.", target=s.name)
            self._say("Not backed up: no file chosen.")
            return
        site = s

        def run():
            out = backup.write_backup(site, passphrase, __import__("pathlib").Path(path))
            mark_backup(site.name)
            return True, f"Encrypted backup written to {out}."
        self._gated("backup", s.name, "Back up site", None, run)

    def restore_backup(self) -> None:
        path = self._ask_open_path()
        if not path:
            return
        passphrase = self._ask_passphrase(confirm=False)
        if not passphrase:
            vpn_execution.record_companion("restore", "declined", "No passphrase given.", target=path)
            self._say("Not restored: no passphrase.")
            return
        from pathlib import Path
        try:
            existing = backup.read_backup(Path(path), passphrase)
        except Exception as exc:
            vpn_execution.record_companion("restore", "failed", str(exc), target=path)
            self._say(f"Could not read the backup: {exc}")
            return
        overwrite = False
        if store.site_exists(existing.name):
            if not self._ask("Replace existing site",
                             f"A site named {existing.name!r} already exists.\n\nRestoring "
                             "replaces its keys and invalidates every config issued from it."):
                vpn_execution.record_companion("restore", "declined",
                                               "Operator declined to replace.", target=existing.name)
                self._say("Not restored: existing site kept.")
                return
            overwrite = True
        self._gated("restore", existing.name, "Restore site", None,
                    lambda: (bool(backup.restore(Path(path), passphrase, overwrite=overwrite)),
                             f"Restored {existing.name}."))

    def delete_site(self) -> None:
        s = self._site
        if s is None:
            return
        name = s.name
        typed = self._ask_text("Delete site",
                               f"This destroys the only copy of the server and CA private "
                               f"keys. Every config issued stops working for good.\n\n"
                               f"Type the site name ({name}) to confirm:")
        if typed != name:
            vpn_execution.record_companion("site-delete", "declined",
                                           "Name not typed or not matching.", target=name)
            self._say("Not deleted: the name was not confirmed.")
            return
        if not has_backup(name) and not self._ask(
                "No backup",
                "No backup or export exists for this site. If you delete it the keys are gone "
                "for good.\n\nI have no backup and accept this."):
            vpn_execution.record_companion("site-delete", "declined",
                                           "No backup and not accepted.", target=name)
            self._say("Not deleted: make a backup first.")
            return
        self._gated("site-delete", name, "Delete site", None,
                    lambda: (store.delete_site(name), f"Deleted {name}."))

    # ── deploy and friends ──
    def _emit_line(self, line: str) -> None:
        """Called from the worker thread; the timer shows it on the UI thread."""
        self._lines.append(vpn_execution.redact_secrets(line))

    def _drain_output(self) -> None:
        while self._lines:
            self.output_view.appendPlainText(self._lines.popleft())

    def _show_output(self, text: str) -> None:
        self.output_view.setPlainText(vpn_execution.redact_secrets(text))

    def _target_text(self, s) -> str:
        if s.mode == MODE_REMOTE:
            return f"{s.ssh.destination()} (port {s.ssh.port}) over SSH"
        return "this Mac (macOS will ask for your administrator password)"

    def _run_streamed(self, action: str, s, title: str, text: str | None, work) -> bool:
        """_gated plus a live output pane. ``work(emit)`` returns (ok, message)."""
        self._lines.clear()
        self._timer.start()

        def run():
            return work(self._emit_line)

        def after(_ok):
            self._timer.stop()
            self._drain_output()

        started = self._gated(action, s.name, title, text, run, after=after)
        if not started:
            self._timer.stop()
        return started

    def _ssh_refused(self, action: str, s) -> bool:
        """Remote-only actions: refuse with the reason, before any prompt."""
        if s.mode != MODE_REMOTE:
            self._refuse(action, s.name, "This action is for remote (VPS) sites.")
            return True
        problems = deploy.ssh_problems(s)
        if not s.ssh.is_configured():
            problems = ["No SSH host is set for this site."]
        if problems:
            self._refuse(action, s.name, "; ".join(problems))
            return True
        return False

    def check_ssh(self) -> None:
        s = self._site
        if s is None or self._ssh_refused("deploy-check", s):
            return
        text = (f"Check SSH access to {s.ssh.destination()}?\n\nThis connects to "
                f"{s.ssh.host} port {s.ssh.port} using your SSH key, never a password. If "
                "this is the first time, ssh trusts and remembers the server's host key "
                "(a changed key later is refused); the fingerprint is shown afterwards. "
                "No keys from this site are sent.")
        site = s
        self.output_view.clear()
        self._run_streamed("deploy-check", s, "Check SSH", text,
                           lambda emit: self._result(deploy.check_ssh(site), emit))

    @staticmethod
    def _result(result, emit) -> tuple[bool, str]:
        for line in (result.output or "").splitlines():
            emit(line)
        if result.error:
            emit("error: " + result.error)
        if result.success:
            return True, "Succeeded."
        return False, result.summary()

    def preview_deploy(self) -> None:
        s = self._site
        if s is None:
            return
        problems = deploy.preflight(s)
        if problems:
            self._show_output("This site cannot be deployed yet:\n- " + "\n- ".join(problems))
            self._say("Preview shows what is blocking the deploy.")
            return
        self._show_output(deploy.preview_script(s))
        self._say("Preview shown with keys redacted. Nothing was run.")

    def deploy_site(self) -> None:
        s = self._site
        if s is None:
            return
        problems = deploy.preflight(s)
        if problems:
            self._show_output("This site cannot be deployed yet:\n- " + "\n- ".join(problems))
            self._refuse("deploy", s.name, "; ".join(problems))
            return
        preview = deploy.preview_script(s)
        text = (f"Deploy {s.name}?\n\nTarget: {self._target_text(s)}\n"
                f"Installs: WireGuard{' + OpenVPN' if s.enable_openvpn else ''}"
                f"{' behind stunnel' if s.obfuscation == 'stunnel' else ''}, firewall rules "
                f"and IP forwarding, for {sum(p.enabled for p in s.peers)} enabled peer(s).\n"
                f"The installer is {len(preview.splitlines())} lines; read it with Preview "
                "deploy first. It contains the server's private keys, goes over the SSH "
                "connection (or a private temporary file removed afterwards) and is never "
                "shown unredacted or logged.")
        site = s

        def work(emit):
            return self._result(deploy.deploy(site, on_output=emit), emit)
        self._show_output(preview + "\n\n── live output ──")
        self._run_streamed("deploy", s, "Deploy", text, work)

    def server_status(self) -> None:
        s = self._site
        if s is None or self._ssh_refused("server-status", s):
            return
        text = (f"Ask {s.ssh.destination()} for its status?\n\nThis connects over SSH and "
                "runs read-only commands (wg show, systemctl, uptime). Nothing is changed.")
        site = s
        self.output_view.clear()

        def work(emit):
            st = deploy.server_status(site)
            for p in st.peers:
                emit(f"{p.name}: handshake {p.describe_handshake()}, {p.describe_transfer()}")
            return st.reachable, st.summary()
        self._run_streamed("server-status", s, "Server status", text, work)

    def teardown_site(self) -> None:
        s = self._site
        if s is None:
            return
        if s.mode == MODE_REMOTE and self._ssh_refused("teardown", s):
            return
        name = s.name
        typed = self._ask_text(
            "Teardown",
            f"This stops and removes the VPN server for {name} from {self._target_text(s)}: "
            "every device loses its VPN until you deploy again. Local keys and the site "
            f"stay. Packages are left installed.\n\nType the site name ({name}) to confirm:")
        if typed != name:
            vpn_execution.record_companion("teardown", "declined",
                                           "Name not typed or not matching.", target=name)
            self._say("Not run: the name was not confirmed.")
            return
        site = s
        self.output_view.clear()
        self._run_streamed("teardown", s, "Teardown", None,
                           lambda emit: self._result(deploy.teardown(site, on_output=emit), emit))

    def register_profile(self) -> None:
        s, peer = self._site, self._selected_peer()
        if s is None or peer is None:
            self._say("Select a peer first; its config is what Connect will use.")
            return
        directory = paths.state_dir() / "client-configs"
        text = (f"Use {peer.name} of {s.name} for Connect?\n\nThis writes the device's "
                f"WireGuard config, which contains its PRIVATE key, to {directory} "
                "(owner-only) and adds a profile so Tunnel can connect with it from this Mac. "
                "Do this only for a peer that is meant for this computer.")
        site, p = s, peer

        def work():
            from services import vpn_connection
            paths.ensure_private_dir(directory)
            index = site.peers.index(p) + 1
            stem = f"sn{index}-{paths.slugify(site.name)}"[:15].rstrip("-")
            conf = paths.write_private(directory / f"{stem}.conf",
                                       render.wg_client_config(site, p))
            profile = vpn_connection.profile_from_config(str(conf))
            profile["name"] = f"{site.name} — {p.name}"
            profile["endpoint"] = site.endpoint_host or profile.get("endpoint", "imported")
            profile["port"] = site.wg_port
            profile["notes"] = f"Built by Sentinel ({site.mode} mode)"
            vpn_connection.save_profile(profile)
            return True, f"Added profile '{profile['name']}'. Pick it in Connect."
        self._gated("register-profile", f"{s.name}/{peer.name}", "Use for Connect", text, work)

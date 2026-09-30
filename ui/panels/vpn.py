"""Tunnel — a real VPN client plus self-hosted VPN design and diagnostics.

Fifth vertical moved out of `main.py` (phase 4, `docs/refactor_plan.md`).

The panel keeps its paths visibly separate: **VPN Connection** runs a real
WireGuard/OpenVPN tunnel through ``vpn_execution``'s gate (target review, explicit
confirm, administrator prompt, post-change check, local audit, rollback steps),
**Connection Check** is read-only, **Action Preview** shows commands without
running them, **Ask Advisor** is a paid request that goes through the guard, and
**Build Config** renders WireGuard files locally.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTextBrowser,
    QVBoxLayout,
)

from agents.vpn_agent.sentinel_chat_agent import build_configs
from services.vpn_diagnostics import (
    VpnDiagnosticsReport,
    build_vpn_action_preview,
    inspect_wireguard_config,
    load_vpn_profile_catalog,
)
from services import vpn_connection, vpn_execution
from ui.panels.base import AgentPanel
from ui.widgets import MenuComboBox, SectionView
from ui.workers import (
    DnsLeakWorker, IpSnapshotWorker, VpnConnectionWorker, VpnDiagnosticsWorker,
)


class VpnPanel(AgentPanel):
    """Advise on, generate, and now actually run a VPN tunnel."""

    agent_key = "vpn"
    diagnostics_worker_class = VpnDiagnosticsWorker
    connection_worker_class = VpnConnectionWorker
    ip_worker_class = IpSnapshotWorker
    dns_leak_worker_class = DnsLeakWorker

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.setObjectName("VPNPanel")
        self._last_response = ""
        self._last_diagnostics_report = None
        self._diagnostics_worker = None
        self._ip_worker = None
        self._dns_worker = None
        self._public_ip_fetched = False
        self._build()
        self.polish_workspace()
        self.hide()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        banner = QLabel(
            "Connect starts WireGuard/OpenVPN; traffic protection is not verified. It may ask for your "
            "administrator password. Example country profiles are templates and will "
            "not connect until you import a real config or set a real endpoint. "
            "Diagnostics and Action Preview remain read-only."
        )
        banner.setWordWrap(True)
        banner.setObjectName("VPNSafetyBanner")
        banner.setStyleSheet(
            "color: #d0d8e0; background: #33240f; border: 1px solid #7a5a1e; "
            "border-radius: 6px; padding: 6px 8px; font-size: 12px;")
        layout.addWidget(banner)

        # ── Live VPN connection ──────────────────────────────────────────
        connect_group = QGroupBox("VPN Connection (live)")
        connect_group.setObjectName("VPNConnectGroup")
        connect_layout = QVBoxLayout(connect_group)

        connect_row = QHBoxLayout()
        connect_row.addWidget(QLabel("Server:"))
        self.connect_profile_box = MenuComboBox()
        self.connect_profile_box.setMinimumWidth(200)
        self.connect_profile_box.setToolTip(
            "WireGuard/OpenVPN profiles and country templates. Import a real "
            "config to add a connectable server.")
        connect_row.addWidget(self.connect_profile_box, 1)
        self.import_config_btn = QPushButton("Import config…")
        self.import_config_btn.setToolTip("Load a WireGuard .conf or OpenVPN .ovpn file.")
        self.import_config_btn.clicked.connect(self.import_vpn_config)
        connect_row.addWidget(self.import_config_btn)
        connect_layout.addLayout(connect_row)

        action_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("PrimaryAction")
        self.connect_btn.clicked.connect(self.connect_vpn)
        action_row.addWidget(self.connect_btn)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("DangerAction")
        self.disconnect_btn.clicked.connect(self.disconnect_vpn)
        action_row.addWidget(self.disconnect_btn)
        self.connection_status_label = QLabel("Connection state not checked.")
        self.connection_status_label.setStyleSheet("color: #9aa; font-size: 12px;")
        action_row.addWidget(self.connection_status_label, 1)
        connect_layout.addLayout(action_row)

        ks_row = QHBoxLayout()
        ks_row.addWidget(QLabel("Kill switch:"))
        self.arm_ks_btn = QPushButton("Arm")
        self.arm_ks_btn.setToolTip(
            "Block all traffic except the selected tunnel's endpoint, so a dropped "
            "tunnel cannot leak. Requires macOS pf and your administrator password.")
        self.arm_ks_btn.clicked.connect(self.arm_kill_switch)
        ks_row.addWidget(self.arm_ks_btn)
        self.disarm_ks_btn = QPushButton("Disarm")
        self.disarm_ks_btn.clicked.connect(self.disarm_kill_switch)
        ks_row.addWidget(self.disarm_ks_btn)
        self.kill_switch_status_label = QLabel("Kill switch not armed.")
        self.kill_switch_status_label.setStyleSheet("color: #9aa; font-size: 12px;")
        ks_row.addWidget(self.kill_switch_status_label, 1)
        connect_layout.addLayout(ks_row)

        layout.addWidget(connect_group)
        self.reload_connect_profiles()

        # ── Your IP (local now, public on request) ───────────────────────
        ip_group = QGroupBox("Your IP && DNS")
        ip_group.setObjectName("VPNIpGroup")
        ip_layout = QGridLayout(ip_group)
        ip_layout.setSpacing(6)

        ip_layout.addWidget(QLabel("Local:"), 0, 0)
        self.local_ip_label = QLabel("—")
        self.local_ip_label.setWordWrap(True)
        self.local_ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.local_ip_label.setToolTip(
            "Your LAN address and any VPN tunnel interface that currently holds "
            "one. Read locally — this contacts nothing.")
        ip_layout.addWidget(self.local_ip_label, 0, 1)
        self.refresh_local_ip_btn = QPushButton("Refresh")
        self.refresh_local_ip_btn.setToolTip("Re-read local interface addresses. Local only.")
        self.refresh_local_ip_btn.clicked.connect(self.refresh_local_ip)
        ip_layout.addWidget(self.refresh_local_ip_btn, 0, 2)

        ip_layout.addWidget(QLabel("Public:"), 1, 0)
        self.public_ip_label = QLabel("Not checked")
        self.public_ip_label.setWordWrap(True)
        self.public_ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.public_ip_label.setToolTip(
            "Your exit IP, its location and network owner, and whether it is "
            "flagged as a VPN/proxy/hosting range.")
        ip_layout.addWidget(self.public_ip_label, 1, 1)
        self.check_public_ip_btn = QPushButton("Check public IP")
        self.check_public_ip_btn.setToolTip(
            "Optional: contacts api.ipify.org and IPinfo (or ipapi.co) to read "
            "your exit IP. No AI provider is used.")
        self.check_public_ip_btn.clicked.connect(self.check_public_ip)
        ip_layout.addWidget(self.check_public_ip_btn, 1, 2)

        ip_layout.addWidget(QLabel("DNS leak:"), 2, 0)
        self.dns_leak_label = QLabel("Not checked")
        self.dns_leak_label.setWordWrap(True)
        self.dns_leak_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dns_leak_label.setToolTip(
            "Which DNS resolvers your traffic actually uses, and whether they sit "
            "outside your tunnel's network — a real egress test, not a guess.")
        ip_layout.addWidget(self.dns_leak_label, 2, 1)
        self.dns_leak_btn = QPushButton("Run test")
        self.dns_leak_btn.setToolTip(
            "Optional: runs a real DNS-leak test via bash.ws (resolves probe "
            "hostnames and reads back the resolvers seen). No AI provider is used.")
        self.dns_leak_btn.clicked.connect(self.run_dns_leak_test)
        ip_layout.addWidget(self.dns_leak_btn, 2, 2)

        layout.addWidget(ip_group)
        self.refresh_local_ip()

        # ── Deployment setup ─────────────────────────────────────────────
        setup_group = QGroupBox("Deployment")
        setup_group.setObjectName("VPNSetupGroup")
        setup_layout = QGridLayout(setup_group)
        setup_layout.setSpacing(6)

        setup_layout.addWidget(QLabel("Mode:"), 0, 0)
        self.mode_box = MenuComboBox()
        self.mode_box.addItems(["Remote (VPS)", "Native (home LAN)"])
        self.mode_box.setToolTip(
            "Remote: traffic exits at a rented VPS — hides your IP, changes your "
            "apparent country.\nNative: runs on hardware you own — an encrypted way "
            "INTO your LAN, exit IP stays your home ISP."
        )
        setup_layout.addWidget(self.mode_box, 0, 1)

        setup_layout.addWidget(QLabel("Protocol:"), 0, 2)
        self.protocol_box = MenuComboBox()
        self.protocol_box.addItems(["WireGuard", "OpenVPN 443 fallback", "Both"])
        setup_layout.addWidget(self.protocol_box, 0, 3)

        setup_layout.addWidget(QLabel("Server host:"), 1, 0)
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("VPS IP or DDNS hostname (Config Builder)")
        setup_layout.addWidget(self.host_input, 1, 1)

        setup_layout.addWidget(QLabel("SSH user:"), 1, 2)
        self.ssh_input = QLineEdit()
        self.ssh_input.setPlaceholderText("e.g. root")
        setup_layout.addWidget(self.ssh_input, 1, 3)

        setup_layout.addWidget(QLabel("LAN subnet:"), 2, 0)
        self.lan_input = QLineEdit()
        self.lan_input.setPlaceholderText("Native mode, e.g. 192.168.1.0/24")
        setup_layout.addWidget(self.lan_input, 2, 1)

        setup_layout.addWidget(QLabel("Egress iface:"), 2, 2)
        self.egress_input = QLineEdit()
        self.egress_input.setPlaceholderText("server NIC, e.g. eth0")
        setup_layout.addWidget(self.egress_input, 2, 3)

        layout.addWidget(setup_group)

        # ── Read-only connection check ─────────────────────────────────
        diagnostics_group = QGroupBox("Connection Check")
        diagnostics_group.setObjectName("VPNDiagnosticsGroup")
        diagnostics_layout = QVBoxLayout(diagnostics_group)

        diagnostics_note = QLabel(
            "Inspect installed VPN tools, active tunnels, the default route, and "
            "configured DNS. This check never connects, disconnects, or changes settings."
        )
        diagnostics_note.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_note)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Compare profile:"))
        self.profile_box = MenuComboBox()
        self.profile_box.setMinimumWidth(180)
        self.profile_box.setToolTip(
            "Compare the local connection snapshot with a VPN Agent profile. "
            "Choosing a profile here does not activate or modify it."
        )
        profile_row.addWidget(self.profile_box, 1)
        self.profile_refresh_btn = QPushButton("Reload")
        self.profile_refresh_btn.setToolTip("Reload the VPN Agent profile list from disk.")
        self.profile_refresh_btn.clicked.connect(self.reload_profiles)
        profile_row.addWidget(self.profile_refresh_btn)
        self.inspect_config_btn = QPushButton("Inspect config…")
        self.inspect_config_btn.setToolTip(
            "Choose one WireGuard file. Private and pre-shared keys are discarded "
            "before Tunnel builds its local summary."
        )
        self.inspect_config_btn.clicked.connect(self.inspect_config)
        profile_row.addWidget(self.inspect_config_btn)
        diagnostics_layout.addLayout(profile_row)

        diagnostics_row = QHBoxLayout()
        self.external_checks_box = QCheckBox("Include public IP and latency")
        self.external_checks_box.setToolTip(
            "Optional: contacts api.ipify.org and tests connectivity to 1.1.1.1. "
            "Tunnel asks again before starting."
        )
        diagnostics_row.addWidget(self.external_checks_box)
        diagnostics_row.addStretch()

        self.diagnostics_stop_btn = QPushButton("Stop Check")
        self.diagnostics_stop_btn.setObjectName("DangerAction")
        self.diagnostics_stop_btn.setVisible(False)
        self.diagnostics_stop_btn.setEnabled(False)
        self.diagnostics_stop_btn.clicked.connect(self.stop_diagnostics)
        diagnostics_row.addWidget(self.diagnostics_stop_btn)

        self.diagnostics_btn = QPushButton("Check Connection")
        self.diagnostics_btn.clicked.connect(self.run_diagnostics)
        diagnostics_row.addWidget(self.diagnostics_btn)
        diagnostics_layout.addLayout(diagnostics_row)
        layout.addWidget(diagnostics_group)

        preview_group = QGroupBox("Safe Action Preview")
        preview_layout = QHBoxLayout(preview_group)
        preview_note = QLabel("Shows what would happen; never runs the command.")
        preview_note.setWordWrap(True)
        preview_layout.addWidget(preview_note, 1)
        self.action_box = MenuComboBox()
        self.action_box.addItems(["Connect", "Disconnect", "Restart"])
        self.action_box.setToolTip("Choose the change you want to inspect.")
        preview_layout.addWidget(self.action_box)
        self.preview_action_btn = QPushButton("Preview")
        self.preview_action_btn.clicked.connect(self.preview_action)
        preview_layout.addWidget(self.preview_action_btn)
        layout.addWidget(preview_group)

        # Configuration generation is deterministic and offline; keep it apart
        # from the model-backed troubleshooting advisor below.
        builder_row = QHBoxLayout()
        self.builder_note = QLabel("Offline configuration — no model required")
        self.builder_note.setStyleSheet("color: #7f8d87; font-size: 12px;")
        builder_row.addWidget(self.builder_note)
        builder_row.addStretch()
        self.build_btn = QPushButton("Build Config")
        self.build_btn.setMinimumWidth(110)
        self.build_btn.setToolTip(
            "Render WireGuard configs and a deploy runbook locally — no LLM."
        )
        self.build_btn.clicked.connect(self.build_config)
        builder_row.addWidget(self.build_btn)
        layout.addLayout(builder_row)

        # ── Advisor question ─────────────────────────────────────────────
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText(
            "Ask the advisor — e.g. \"WireGuard won't connect on hotel wifi, what now?\""
        )
        self.question_input.returnPressed.connect(self.run)
        layout.addWidget(self.question_input)

        self.run_btn = QPushButton("Ask Advisor")
        self.run_btn.setMinimumWidth(120)
        self.run_btn.setObjectName("PrimaryAction")
        self.run_btn.clicked.connect(self.run)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setObjectName("DangerAction")
        self.stop_btn.clicked.connect(self.stop)
        self.set_busy(self.run_btn, self.stop_btn, False)

        # ── Provider / action row ────────────────────────────────────────
        provider_row_container = self.build_run_bar(
            self.run_btn,
            stop=self.stop_btn,
            context="Advisor",
        )

        layout.addWidget(provider_row_container)

        # ── Results tabs ─────────────────────────────────────────────────
        self.tabs = QTabWidget()

        self.diagnostics_view = SectionView()
        self.tabs.addTab(self.diagnostics_view, "Diagnostics")

        self.advisor_box = QTextBrowser()
        self.advisor_box.setOpenExternalLinks(False)
        self.tabs.addTab(self.advisor_box, "Advisor")

        self.config_box = QTextBrowser()
        self.config_box.setOpenExternalLinks(False)
        self.tabs.addTab(self.config_box, "Config && Commands")

        self.action_view = SectionView()
        self.tabs.addTab(self.action_view, "Action Preview")

        self.config_inspection_view = SectionView()
        self.tabs.addTab(self.config_inspection_view, "Config Inspection")

        self.execution_view = SectionView()
        self.tabs.addTab(self.execution_view, "Execution")

        self.advisor_box.setPlaceholderText(
            "Troubleshooting advice will appear after you select Ask Advisor."
        )
        self.config_box.setPlaceholderText(
            "Generated VPN configuration and commands will appear after Build Config."
        )

        layout.addWidget(self.tabs, 1)

        # ── Bottom bar ───────────────────────────────────────────────────
        bottom_row = QHBoxLayout()
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("font-size: 12px; color: #888;")
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        bottom_row.addWidget(self.clear_btn)
        layout.addLayout(bottom_row)

        self.reload_profiles()

    # ── The advisor (paid) ──────────────────────────────────────────────
    def context_prefix(self) -> str:
        """The deployment setup, phrased as context the advisor reasons from."""
        parts = [
            f"Deployment mode: {self.mode_box.currentText()}",
            f"Protocol focus: {self.protocol_box.currentText()}",
        ]
        if self.host_input.text().strip():
            parts.append(f"Server host: {self.host_input.text().strip()}")
        if self.lan_input.text().strip():
            parts.append(f"LAN subnet: {self.lan_input.text().strip()}")
        return "Context — " + "; ".join(parts) + ".\n\n"

    def run(self) -> None:
        question = self.question_input.text().strip()

        if self._diagnostics_running():
            QMessageBox.information(
                self, "Connection Check Running", "Stop the connection check before asking the advisor."
            )
            return
        if not question:
            QMessageBox.warning(self, "Missing Input", "Enter a question for the advisor.")
            return
        if not self.model:
            QMessageBox.warning(self, "No Model", "Please select a model.")
            return

        prompt = self.context_prefix() + question
        messages = self.agent().build_messages(prompt)

        if not self.authorize(prompt, label=self.mode_box.currentText()):
            return

        self._last_response = ""
        self.advisor_box.clear()
        self.tabs.setCurrentWidget(self.advisor_box)
        self.status_label.setText("Consulting advisor…")
        self.set_busy(self.run_btn, self.stop_btn, True)

        self.start_worker(
            messages, prompt,
            on_token=self._on_token,
            on_finished=self._on_finished,
            on_error=self._on_error,
        )

    def _on_token(self, token: str) -> None:
        self._last_response += token
        self.advisor_box.setPlainText(self._last_response)
        self.advisor_box.moveCursor(QTextCursor.End)

    def _on_finished(self, full_response: str) -> None:
        self._last_response = full_response
        self.record(full_response)
        self.advisor_box.setPlainText(full_response)
        self.status_label.setText("Done.")
        self.set_busy(self.run_btn, self.stop_btn, False)

    def _on_error(self, error: str) -> None:
        self.abandon()
        separator = "─" * 50
        self.advisor_box.setPlainText(f"⚠  ERROR\n{separator}\n{error}\n{separator}")
        self.status_label.setText("Error.")
        self.set_busy(self.run_btn, self.stop_btn, False)

    def stop(self) -> None:
        stopped = False
        if super().is_running():
            self.worker.cancel()
            stopped = True
        if self._diagnostics_running():
            self.stop_diagnostics()
            stopped = True
        if stopped:
            self.status_label.setText("Stopped.")
        self.set_busy(self.run_btn, self.stop_btn, False)

    # ── Read-only diagnostics ───────────────────────────────────────────
    def _diagnostics_running(self) -> bool:
        return (
            self._diagnostics_worker is not None
            and self._diagnostics_worker.isRunning()
        )

    def is_running(self) -> bool:
        return super().is_running() or self._diagnostics_running()

    def reload_profiles(self) -> None:
        """Refresh the profile picker without changing VPN Agent state."""
        catalog = load_vpn_profile_catalog()
        previous = self.profile_box.currentText()
        self.profile_box.clear()
        self.profile_box.addItem("No profile comparison", None)
        for profile in catalog.profiles:
            name = str(profile.get("name") or "Unnamed")
            self.profile_box.addItem(name, dict(profile))

        preferred = previous if self.profile_box.findText(previous) >= 0 else catalog.active_profile
        if preferred and self.profile_box.findText(preferred) >= 0:
            self.profile_box.setCurrentText(preferred)
        if catalog.error:
            self.profile_box.setToolTip(
                f"VPN profiles could not be loaded from {catalog.source}: {catalog.error}"
            )

    def selected_profile(self) -> dict | None:
        profile = self.profile_box.currentData()
        return dict(profile) if isinstance(profile, dict) else None

    # ── Live connection ─────────────────────────────────────────────────
    def reload_connect_profiles(self) -> None:
        """Populate the connect picker with full (unstripped) profiles."""
        try:
            profiles = vpn_connection.load_connectable_profiles()
        except Exception:
            profiles = []
        previous = self.connect_profile_box.currentText()
        self.connect_profile_box.clear()
        for profile in profiles:
            name = str(profile.get("name") or "Unnamed")
            country = profile.get("country")
            label = f"{name}  ·  {country}" if country and country not in name else name
            if vpn_connection.is_placeholder(profile):
                label += "  (template)"
            self.connect_profile_box.addItem(label, dict(profile))
        if previous:
            idx = self.connect_profile_box.findText(previous)
            if idx >= 0:
                self.connect_profile_box.setCurrentIndex(idx)

    def selected_connect_profile(self) -> dict | None:
        profile = self.connect_profile_box.currentData()
        return dict(profile) if isinstance(profile, dict) else None

    # ── Your IP readout ─────────────────────────────────────────────────
    @staticmethod
    def _format_local_ip(local: dict) -> str:
        primary = local.get("primary") or "unknown"
        parts = [f"{primary} (LAN)"]
        interfaces = local.get("interfaces") or {}
        for name in local.get("tunnels") or []:
            for address in interfaces.get(name, []):
                parts.append(f"{address} ({name}, tunnel)")
        if not local.get("tunnels"):
            parts.append("no tunnel interface up")
        return "   ·   ".join(parts)

    @staticmethod
    def _format_public_ip(public: dict) -> str:
        ip = public.get("ip") or "unknown"
        parts = [ip]
        location = ", ".join(
            value for value in (public.get("city"), public.get("country"))
            if value and value != "Unknown"
        )
        if location:
            parts.append(location)
        org = public.get("org")
        if org and org != "Unknown":
            parts.append(org)
        flags = public.get("privacy_flags")
        if flags:
            parts.append("flagged: " + ", ".join(flags))
        return "   ·   ".join(parts)

    def refresh_local_ip(self) -> None:
        """Refresh the local interface addresses off the UI thread.

        Local only — the exit IP is not fetched here. Reading interfaces shells
        out to ``ifconfig`` (up to a 5 s timeout), so it runs on a worker rather
        than blocking the Qt event loop, like every other IO path in this panel.
        """
        if self._ip_worker is not None and self._ip_worker.isRunning():
            return
        worker = self.ip_worker_class(include_public=False)
        worker.finished_signal.connect(self._on_ip_snapshot)
        worker.error_signal.connect(self._on_local_ip_error)
        self._ip_worker = worker
        worker.start()

    def _on_local_ip_error(self, error: str) -> None:
        self.local_ip_label.setText(f"Could not read local addresses: {error}"[:200])

    def check_public_ip(self) -> None:
        """Fetch the public exit IP (and refresh the local view) off the UI thread."""
        if self._ip_worker is not None and self._ip_worker.isRunning():
            return
        self.check_public_ip_btn.setEnabled(False)
        self.public_ip_label.setText("Checking…")
        worker = self.ip_worker_class(include_public=True)
        worker.finished_signal.connect(self._on_ip_snapshot)
        worker.error_signal.connect(self._on_ip_error)
        self._ip_worker = worker
        worker.start()

    def _on_ip_snapshot(self, snapshot: dict) -> None:
        self.check_public_ip_btn.setEnabled(True)
        local = snapshot.get("local")
        if local:
            self.local_ip_label.setText(self._format_local_ip(local))
        public = snapshot.get("public")
        if public is not None:
            self.public_ip_label.setText(self._format_public_ip(public))
            # Latch the session opt-in only on a genuine success, so a failed
            # first check does not trigger silent auto-refetches after connect.
            ip_value = str(public.get("ip") or "")
            if ip_value and ip_value not in ("Unknown",) and not ip_value.startswith("Error:"):
                self._public_ip_fetched = True

    def _on_ip_error(self, error: str) -> None:
        self.check_public_ip_btn.setEnabled(True)
        self.public_ip_label.setText(f"Error: {error}"[:200])

    # ── DNS-leak test ────────────────────────────────────────────────────
    @staticmethod
    def _format_dns_leak(result: dict) -> str:
        status = result.get("status")
        if status == "cancelled":
            return "Test stopped."
        if status != "ok":
            return f"Could not run the test: {result.get('detail', 'unknown error')}"[:200]
        count = result.get("resolver_count", 0)
        if not count:
            return result.get("conclusion") or "No resolvers were observed — try again."
        leak = result.get("leak")
        verdict = ("possible leak" if leak else "no leak detected"
                   if leak is False else "review resolvers")
        head = f"{count} resolver(s), {result.get('distinct_asns', 0)} network(s) — {verdict}"
        seen = []
        for resolver in result.get("resolvers", [])[:4]:
            label = resolver.get("ip") or "?"
            asn = resolver.get("asn")
            country = resolver.get("country")
            extra = ", ".join(part for part in (asn, country) if part)
            seen.append(f"{label} ({extra})" if extra else label)
        return head + ("   ·   " + "   ·   ".join(seen) if seen else "")

    def run_dns_leak_test(self) -> None:
        """Run the real egress DNS-leak test off the UI thread."""
        if self._dns_worker is not None and self._dns_worker.isRunning():
            return
        self.dns_leak_btn.setEnabled(False)
        self.dns_leak_label.setText("Testing… (resolving probes)")
        worker = self.dns_leak_worker_class()
        worker.finished_signal.connect(self._on_dns_leak)
        worker.error_signal.connect(self._on_dns_leak_error)
        self._dns_worker = worker
        worker.start()

    def _on_dns_leak(self, result: dict) -> None:
        self.dns_leak_btn.setEnabled(True)
        self.dns_leak_label.setText(self._format_dns_leak(result))

    def _on_dns_leak_error(self, error: str) -> None:
        self.dns_leak_btn.setEnabled(True)
        self.dns_leak_label.setText(f"Error: {error}"[:200])

    def import_vpn_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import VPN config", str(Path.home()),
            "VPN configs (*.conf *.ovpn);;All files (*)")
        if not path:
            return
        profile = vpn_connection.profile_from_config(path)
        try:
            vpn_connection.save_profile(profile)
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", f"Could not save the profile: {exc}")
            return
        self.reload_connect_profiles()
        name_idx = self.connect_profile_box.findText(profile["name"], Qt.MatchStartsWith)
        if name_idx >= 0:
            self.connect_profile_box.setCurrentIndex(name_idx)
        self.connection_status_label.setText(
            f"Imported {Path(path).name} as a {profile['protocol']} profile.")

    def connect_vpn(self) -> None:
        if self._connection_busy():
            return
        profile = self.selected_connect_profile()
        if not profile:
            QMessageBox.information(self, "No profile", "Choose a server profile first.")
            return
        if vpn_connection.is_placeholder(profile):
            QMessageBox.information(
                self, "Template profile",
                "This is an example/template. Import a real WireGuard .conf or "
                "OpenVPN .ovpn (or set a real endpoint) before connecting.")
            return
        if not self._review_and_confirm("connect", profile, "Connect VPN"):
            return
        self._start_connection("connect", profile,
                               f"Connecting {profile.get('name')}…")

    def disconnect_vpn(self) -> None:
        if self._connection_busy():
            return
        profile = self.selected_connect_profile()
        if not profile:
            QMessageBox.information(self, "No profile", "Choose the profile to disconnect.")
            return
        if not self._review_and_confirm("disconnect", profile, "Disconnect VPN"):
            return
        self._start_connection("disconnect", profile,
                               f"Disconnecting {profile.get('name')}…")

    def _review_and_confirm(self, action: str, profile: dict, title: str) -> bool:
        """Show the target review; refuse on blockers, else ask (default No)."""
        review = vpn_execution.review_execution(action, profile)
        self.execution_view.show_sections(review.sections())
        self.tabs.setCurrentWidget(self.execution_view)
        if not review.allowed:
            self.connection_status_label.setText(
                ("Refused: " + "; ".join(review.blockers))[:300])
            QMessageBox.warning(
                self, title,
                "Tunnel will not run this:\n\n"
                + "\n".join(f"• {item}" for item in review.blockers)
                + "\n\nNothing was executed.")
            return False
        confirm = QMessageBox.question(
            self, title, review.confirmation_text(),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return confirm == QMessageBox.Yes

    def _connection_busy(self) -> bool:
        worker = getattr(self, "_connection_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "Busy", "A connection action is already running.")
            return True
        return False

    def _start_connection(self, action: str, profile: dict, status: str) -> None:
        self.connection_status_label.setText(status)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        worker = self.connection_worker_class(action, profile)
        worker.finished_signal.connect(self._on_connection_finished)
        worker.error_signal.connect(self._on_connection_error)
        self._connection_worker = worker
        worker.start()

    def _on_connection_finished(self, result: dict) -> None:
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(True)
        if result.get("sections"):
            self.execution_view.show_sections(result["sections"])
            self.tabs.setCurrentWidget(self.execution_view)
        if result.get("status_line"):
            self.connection_status_label.setText(str(result["status_line"])[:300])
        elif result.get("success"):
            self.connection_status_label.setText(
                f"{result.get('protocol', 'VPN')}: command completed. "
                "Connection state and traffic protection are not verified.")
        else:
            self.connection_status_label.setText(f"Failed: {result.get('error', 'unknown')}"[:300])
        # The tunnel interface just appeared or went away. If the operator
        # already opted into the external exit-IP check this session, refresh
        # both halves together (check_public_ip also re-reads the local view);
        # otherwise refresh only the local view. Calling both would race on the
        # shared _ip_worker and drop the public refresh.
        if self._public_ip_fetched:
            self.check_public_ip()
        else:
            self.refresh_local_ip()

    def _on_connection_error(self, error: str) -> None:
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(True)
        self.connection_status_label.setText(f"Error: {error}"[:300])

    # ── Kill switch ─────────────────────────────────────────────────────
    def arm_kill_switch(self) -> None:
        if self._connection_busy():
            return
        if not vpn_connection.killswitch_supported():
            QMessageBox.information(
                self, "Kill switch",
                "The kill switch needs macOS pf (the packet filter) to be available.")
            return
        profile = self.selected_connect_profile()
        if not profile or vpn_connection.is_placeholder(profile):
            QMessageBox.information(
                self, "Kill switch",
                "Choose a real server profile first — the kill switch must exempt "
                "its endpoint, or it would block the tunnel too.")
            return
        confirm = QMessageBox.question(
            self, "Arm kill switch",
            "Arm a firewall kill switch?\n\nThis blocks all network traffic except "
            f"the selected tunnel's endpoint ({profile.get('endpoint')}). If the "
            "tunnel drops, traffic stays blocked until you disarm. It needs your "
            "administrator password, and refuses to arm if the endpoint cannot be "
            "resolved.",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self._start_killswitch("arm", profile, "Arming kill switch…")

    def disarm_kill_switch(self) -> None:
        if self._connection_busy():
            return
        self._start_killswitch(
            "disarm", self.selected_connect_profile() or {}, "Disarming kill switch…")

    def _start_killswitch(self, action: str, profile: dict, status: str) -> None:
        self.kill_switch_status_label.setText(status)
        self.arm_ks_btn.setEnabled(False)
        self.disarm_ks_btn.setEnabled(False)
        worker = self.connection_worker_class(action, profile)
        worker.finished_signal.connect(self._on_killswitch_finished)
        worker.error_signal.connect(self._on_killswitch_error)
        self._connection_worker = worker
        worker.start()

    def _on_killswitch_finished(self, result: dict) -> None:
        self.arm_ks_btn.setEnabled(True)
        self.disarm_ks_btn.setEnabled(True)
        if result.get("success"):
            self.kill_switch_status_label.setText(
                f"Kill switch: {result.get('output', 'done')}"[:200])
        else:
            self.kill_switch_status_label.setText(
                f"Kill switch failed: {result.get('error', 'unknown')}"[:300])

    def _on_killswitch_error(self, error: str) -> None:
        self.arm_ks_btn.setEnabled(True)
        self.disarm_ks_btn.setEnabled(True)
        self.kill_switch_status_label.setText(f"Kill switch error: {error}"[:300])

    def run_diagnostics(self) -> None:
        if super().is_running():
            QMessageBox.information(
                self, "Advisor Running", "Stop the advisor before checking the connection."
            )
            return
        if self._diagnostics_running():
            return

        include_external = self.external_checks_box.isChecked()
        if include_external:
            answer = QMessageBox.question(
                self,
                "Allow External Connectivity Checks?",
                "This optional check contacts api.ipify.org to read your public IP "
                "and tests latency to 1.1.1.1. No AI provider is used. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self.diagnostics_view.clear()
        self.tabs.setCurrentWidget(self.diagnostics_view)
        self.status_label.setText(
            "Checking locally and externally…" if include_external else "Checking locally…"
        )
        self.diagnostics_btn.setVisible(False)
        self.diagnostics_btn.setEnabled(False)
        self.diagnostics_stop_btn.setVisible(True)
        self.diagnostics_stop_btn.setEnabled(True)

        worker = self.diagnostics_worker_class(
            include_external=include_external,
            selected_profile=self.selected_profile(),
        )
        self._diagnostics_worker = worker
        worker.finished_signal.connect(self._on_diagnostics_finished)
        worker.error_signal.connect(self._on_diagnostics_error)
        worker.start()

    def _finish_diagnostics_ui(self) -> None:
        self.diagnostics_btn.setVisible(True)
        self.diagnostics_btn.setEnabled(True)
        self.diagnostics_stop_btn.setVisible(False)
        self.diagnostics_stop_btn.setEnabled(False)

    def _on_diagnostics_finished(self, report: VpnDiagnosticsReport) -> None:
        self._finish_diagnostics_ui()
        self._last_diagnostics_report = report
        self.diagnostics_view.show_sections(report.sections(), raw=report.as_text())
        self.tabs.setCurrentWidget(self.diagnostics_view)
        self.status_label.setText("Connection check stopped." if report.cancelled else "Connection check complete.")

    def _on_diagnostics_error(self, error: str) -> None:
        self._finish_diagnostics_ui()
        self.diagnostics_view.show_sections(
            [("Connection check could not finish", error, False)], raw=error
        )
        self.tabs.setCurrentWidget(self.diagnostics_view)
        self.status_label.setText("Connection check error.")

    def stop_diagnostics(self) -> None:
        if self._diagnostics_running():
            self._diagnostics_worker.cancel()
            self.status_label.setText("Stopping connection check…")
            self.diagnostics_stop_btn.setEnabled(False)

    def inspect_config(self) -> None:
        """Summarise one selected WireGuard file without retaining its secrets."""
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Inspect WireGuard Configuration",
            "",
            "WireGuard configurations (*.conf);;All files (*)",
        )
        if not path:
            return
        try:
            inspection = inspect_wireguard_config(path, self._last_diagnostics_report)
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration Could Not Be Inspected", str(exc))
            return
        self.config_inspection_view.show_sections(
            inspection.sections(), raw=inspection.as_text()
        )
        self.tabs.setCurrentWidget(self.config_inspection_view)
        self.status_label.setText(
            "Configuration inspected locally — key material discarded."
        )

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Cancel and join Tunnel workers before their widgets are destroyed."""
        workers = [self.worker, self._diagnostics_worker, self._ip_worker,
                   self._dns_worker, getattr(self, "_connection_worker", None)]
        for worker in workers:
            if worker is not None and worker.isRunning():
                worker.cancel()
        for worker in workers:
            if worker is None or not worker.isRunning() or not hasattr(worker, "wait"):
                continue
            if not worker.wait(timeout_ms) and hasattr(worker, "terminate"):
                # Every diagnostic command is bounded, but application shutdown
                # must not destroy a live QThread if an OS call ignores cancellation.
                worker.terminate()
                worker.wait(500)

    # ── Deliberately non-executing action preview ─────────────────────
    def preview_action(self) -> None:
        preview = build_vpn_action_preview(
            self.action_box.currentText(),
            self.selected_profile(),
            self._last_diagnostics_report,
        )
        self.action_view.show_sections(preview.sections(), raw=preview.as_text())
        self.tabs.setCurrentWidget(self.action_view)
        self.status_label.setText(
            "Action preview ready — nothing executed."
            if preview.valid else "Action preview unavailable."
        )

    # ── The config builder (offline) ────────────────────────────────────
    def build_config(self) -> None:
        """Render WireGuard configs + a deploy runbook. Deterministic, offline."""
        text = build_configs(
            mode=self.mode_box.currentText(),
            protocol=self.protocol_box.currentText(),
            server_host=self.host_input.text().strip(),
            ssh_user=self.ssh_input.text().strip(),
            lan_subnet=self.lan_input.text().strip(),
            egress_iface=self.egress_input.text().strip() or "eth0",
        )
        self.config_box.setPlainText(text)
        self.tabs.setCurrentWidget(self.config_box)
        self.status_label.setText("Config rendered.")

    def clear(self) -> None:
        self.diagnostics_view.clear()
        self.advisor_box.clear()
        self.config_box.clear()
        self.action_view.clear()
        self.config_inspection_view.clear()
        self.execution_view.clear()
        self.question_input.clear()
        self.status_label.setText("Idle")
        self._last_response = ""
        self._last_diagnostics_report = None

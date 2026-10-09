"""Sentry — Sentinel's read-only network anomaly watch panel.

The panel drives three things:
  * a one-off watch pass (snapshot → diff against the trusted baseline), run off
    the UI thread by ``SentryWatchWorker``;
  * an optional AI read of the findings, using the shared request guard exactly
    like the other specialist panels (authorize → stream → record / abandon);
  * the continuous background watch (a launchd StartInterval agent) managed via
    ``agents.sentry.sentry.watchd``.

Nothing here observes the network directly — collection lives in the sentry
package, and every command it runs is read-only.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QTextBrowser, QVBoxLayout,
)

from agents.sentry.sentry import watchd
from ui.panels.base import AgentPanel
from ui.workers import SentryWatchWorker

_SEVERITY_COLOR = {
    "alert": "#ff5c5c",
    "warning": "#ffb347",
    "notice": "#4db8ff",
    "info": "#8a8a8a",
}
_SEVERITY_LABEL = {
    "alert": "ALERT", "warning": "WARNING", "notice": "NOTICE", "info": "INFO",
}


class SentryPanel(AgentPanel):
    """Continuous, read-only network monitoring with an optional AI read."""

    agent_key = "sentry"
    default_provider = "anthropic"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.setObjectName("SentryPanel")
        self._last_response = ""
        self._last_summary: dict = {}
        self.watch_worker: SentryWatchWorker | None = None
        self._build()
        self.polish_workspace()
        self.refresh_watch_status()
        self.hide()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        intro = QLabel(
            "Sentry watches your own network, read-only: the neighbour (ARP/NDP) "
            "table, the default gateway, this Mac's listening services and its "
            "outbound connections. It records a trusted baseline on the first "
            "pass, then flags what is new — a fresh device, ARP-spoofing / gateway "
            "changes, an unexpected service, or a new outbound connection. It "
            "sends no packets and changes nothing."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(intro)

        # ── Baseline / watch state ────────────────────────────────────────
        watch_group = QGroupBox("Network Watch")
        watch_group.setObjectName("SentryWatchGroup")
        watch_layout = QVBoxLayout(watch_group)
        self.state_label = QLabel("No baseline yet — run a pass to record one.")
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("font-size: 12px;")
        watch_layout.addWidget(self.state_label)

        controls = QHBoxLayout()
        self.dry_run_btn = QPushButton("Dry run")
        self.dry_run_btn.setToolTip("Compare against the baseline without updating it.")
        self.dry_run_btn.clicked.connect(lambda: self.run_pass(persist=False))
        self.reset_btn = QPushButton("Reset baseline")
        self.reset_btn.setToolTip("Forget what has been seen and record a fresh baseline next pass.")
        self.reset_btn.clicked.connect(self.reset_baseline)
        controls.addWidget(self.dry_run_btn)
        controls.addWidget(self.reset_btn)
        controls.addStretch()
        watch_layout.addLayout(controls)
        layout.addWidget(watch_group)

        # ── Continuous background watch ───────────────────────────────────
        bg_group = QGroupBox("Continuous Background Watch")
        bg_group.setObjectName("SentryBackgroundGroup")
        bg_layout = QGridLayout(bg_group)
        bg_layout.setSpacing(6)
        bg_layout.addWidget(QLabel("Check every:"), 0, 0)
        self.interval_box = QSpinBox()
        self.interval_box.setRange(1, 720)
        self.interval_box.setValue(5)
        self.interval_box.setSuffix(" min")
        bg_layout.addWidget(self.interval_box, 0, 1)
        self.install_btn = QPushButton("Install background watch")
        self.install_btn.clicked.connect(self.install_background)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_background)
        bg_layout.addWidget(self.install_btn, 0, 2)
        bg_layout.addWidget(self.remove_btn, 0, 3)
        self.bg_status_label = QLabel("")
        self.bg_status_label.setWordWrap(True)
        self.bg_status_label.setStyleSheet("font-size: 11px; color: #888;")
        bg_layout.addWidget(self.bg_status_label, 1, 0, 1, 4)
        layout.addWidget(bg_group)

        # ── Run bar (primary = run a watch pass) ──────────────────────────
        self.run_btn = QPushButton("Run watch pass")
        self.run_btn.setMinimumWidth(140)
        self.run_btn.setObjectName("PrimaryAction")
        self.run_btn.clicked.connect(lambda: self.run_pass(persist=True))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("DangerAction")
        self.stop_btn.clicked.connect(self.stop)
        self.set_busy(self.run_btn, self.stop_btn, False)
        layout.addWidget(self.build_run_bar(
            self.run_btn, stop=self.stop_btn, context="Network watch",
        ))

        ai_row = QHBoxLayout()
        self.ai_checkbox = QCheckBox("Explain findings with AI")
        # Off by default: findings carry LAN IPs, MAC addresses and process
        # names, and the selected provider may be a paid cloud one (D5).
        self.ai_checkbox.setChecked(False)
        self.ai_checkbox.setToolTip(
            "After a pass with findings, send them — LAN IPs, MAC addresses and "
            "process names included — to the selected provider and model for a "
            "calibrated read. Off by default; nothing is sent when there is "
            "nothing to report."
        )
        ai_row.addWidget(self.ai_checkbox)
        ai_row.addStretch()
        layout.addLayout(ai_row)

        # ── Findings + AI output ──────────────────────────────────────────
        self.findings_box = QTextBrowser()
        self.findings_box.setObjectName("SentryFindings")
        self.findings_box.setOpenExternalLinks(False)
        self.findings_box.setPlaceholderText(
            "Findings from the latest watch pass will appear here."
        )
        layout.addWidget(self.findings_box, 1)

        self.stream_box = QTextBrowser()
        self.stream_box.setObjectName("SentryStream")
        self.stream_box.setOpenExternalLinks(False)
        self.stream_box.setVisible(False)
        layout.addWidget(self.stream_box, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(self.status_label)

    # ── Watch passes ─────────────────────────────────────────────────────
    def run_pass(self, *, persist: bool) -> None:
        if self.is_running():
            return
        self.set_busy(self.run_btn, self.stop_btn, True)
        self.stream_box.setVisible(False)
        self.status_label.setText(
            "Observing the network…" if persist else "Dry run — observing…"
        )
        self.watch_worker = SentryWatchWorker(persist=persist)
        self.watch_worker.finished_signal.connect(self._pass_finished)
        self.watch_worker.error_signal.connect(self._pass_error)
        self.watch_worker.start()

    def _pass_finished(self, summary: dict) -> None:
        self._last_summary = summary
        self._render_findings(summary)
        self.refresh_watch_status()

        findings = summary.get("findings", [])
        if summary.get("baseline_established"):
            self.status_label.setText("Baseline recorded.")
            self.set_busy(self.run_btn, self.stop_btn, False)
            return
        if not findings:
            self.status_label.setText("No new anomalies.")
            self.set_busy(self.run_btn, self.stop_btn, False)
            return

        if self.ai_checkbox.isChecked() and self.model:
            self._start_ai_pass(self._prompt_from(summary))
            self.status_label.setText("Interpreting findings…")
        else:
            self.status_label.setText(f"{len(findings)} finding(s).")
            self.set_busy(self.run_btn, self.stop_btn, False)

    def _pass_error(self, error: str) -> None:
        self.findings_box.setHtml(f"<p style='color:#ff5c5c'>[Error] {escape(error)}</p>")
        self.status_label.setText("Error running watch pass.")
        self.set_busy(self.run_btn, self.stop_btn, False)

    def _render_findings(self, summary: dict) -> None:
        counts = (
            f"{summary.get('device_count', 0)} devices · "
            f"{summary.get('listener_count', 0)} listeners · "
            f"{summary.get('connection_count', 0)} connections"
        )
        parts = [f"<p style='color:#888'>{escape(counts)}"]
        if summary.get("taken_at"):
            parts.append(f" · {escape(summary['taken_at'])}")
        parts.append("</p>")

        if summary.get("baseline_established"):
            parts.append(
                "<p>Baseline recorded. Future passes compare against this — "
                "nothing is flagged on a first pass.</p>"
            )
            self.findings_box.setHtml("".join(parts))
            return

        findings = summary.get("findings", [])
        if not findings:
            parts.append("<p style='color:#5cd65c'>No new anomalies since the last trusted baseline.</p>")
            self.findings_box.setHtml("".join(parts))
            return

        for finding in findings:
            severity = finding.get("severity", "info")
            color = _SEVERITY_COLOR.get(severity, "#8a8a8a")
            label = _SEVERITY_LABEL.get(severity, severity.upper())
            parts.append(
                f"<p style='margin:8px 0 2px 0'>"
                f"<b style='color:{color}'>[{label}]</b> "
                f"{escape(finding.get('title', ''))}</p>"
            )
            if finding.get("detail"):
                parts.append(
                    f"<p style='margin:0 0 6px 12px;color:#bbb;font-size:12px'>"
                    f"{escape(finding['detail'])}</p>"
                )
        self.findings_box.setHtml("".join(parts))

    def _prompt_from(self, summary: dict) -> str:
        lines = [
            "Read-only network watch pass.",
            f"Devices: {summary.get('device_count', 0)}, "
            f"listeners: {summary.get('listener_count', 0)}, "
            f"connections: {summary.get('connection_count', 0)}.",
            "",
            "Findings:",
        ]
        for finding in summary.get("findings", []):
            lines.append(
                f"- [{finding.get('severity', 'info')}] {finding.get('title', '')}"
            )
            if finding.get("evidence"):
                lines.append(f"  evidence: {finding['evidence']}")
        lines.append("")
        lines.append("Interpret these findings for the operator.")
        return "\n".join(lines)

    # ── AI read (shared request guard) ───────────────────────────────────
    def _start_ai_pass(self, prompt: str) -> None:
        messages = self.agent().build_messages(prompt)
        if not self.authorize(prompt):
            self.set_busy(self.run_btn, self.stop_btn, False)
            return
        self._last_response = ""
        self.stream_box.clear()
        self.stream_box.setVisible(True)
        self.start_worker(
            messages, prompt,
            on_token=self._on_token,
            on_finished=self._on_finished,
            on_error=self._on_error,
        )

    def _on_token(self, token: str) -> None:
        self._last_response += token
        self.stream_box.setPlainText(self._last_response)
        self.stream_box.moveCursor(QTextCursor.End)

    def _on_finished(self, full_response: str) -> None:
        self.record(full_response)
        self._last_response = full_response
        self.stream_box.setPlainText(full_response)
        self.status_label.setText("Analysis complete.")
        self.set_busy(self.run_btn, self.stop_btn, False)

    def _on_error(self, error: str) -> None:
        self.abandon()
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(f"[Error] {error}")
        self.status_label.setText("Error.")
        self.set_busy(self.run_btn, self.stop_btn, False)

    # ── Baseline management ──────────────────────────────────────────────
    def reset_baseline(self) -> None:
        try:
            from agents.sentry.sentry.baseline import BaselineStore

            store = BaselineStore()
            if store.baseline_path.is_file():
                store.baseline_path.unlink()
            store.clear_findings()
        except Exception as exc:  # keep UI resilient
            self.status_label.setText(f"Could not reset baseline: {exc}")
            return
        self.findings_box.clear()
        self.status_label.setText("Baseline reset. The next pass records a fresh one.")
        self.refresh_watch_status()

    # ── Continuous background watch (launchd) ────────────────────────────
    def install_background(self) -> None:
        interval = self.interval_box.value() * 60
        result = watchd.install(interval)
        self.status_label.setText(result.get("message", ""))
        self.refresh_watch_status()

    def remove_background(self) -> None:
        result = watchd.remove()
        self.status_label.setText(result.get("message", ""))
        self.refresh_watch_status()

    def refresh_watch_status(self) -> None:
        # Baseline line
        try:
            from agents.sentry.sentry.baseline import BaselineStore

            store = BaselineStore()
            if store.has_baseline():
                baseline = store.load_baseline()
                taken = baseline.taken_at if baseline else ""
                self.state_label.setText(
                    "Baseline recorded"
                    + (f" (updated {taken})." if taken else ".")
                    + " Passes compare new activity against it."
                )
            else:
                self.state_label.setText("No baseline yet — run a pass to record one.")
        except Exception:
            pass

        # Background watch line
        try:
            status = watchd.status()
        except Exception:
            status = {"installed": False, "loaded": False}
        if status.get("loaded"):
            interval = status.get("interval")
            every = f"every {interval // 60} min" if interval else "on a schedule"
            self.bg_status_label.setText(
                f"Background watch is running {every}. Log: {status.get('log', '')}"
            )
            self.install_btn.setText("Reinstall")
        elif status.get("installed"):
            self.bg_status_label.setText(
                "Background watch is installed but not loaded. Reinstall to start it."
            )
            self.install_btn.setText("Reinstall")
        else:
            self.bg_status_label.setText(
                "Background watch is off. Install it to keep watching while the app is closed."
            )
            self.install_btn.setText("Install background watch")

    # ── Lifecycle ────────────────────────────────────────────────────────
    def is_running(self) -> bool:
        watching = self.watch_worker is not None and self.watch_worker.isRunning()
        return watching or super().is_running()

    def stop(self) -> None:
        if self.watch_worker is not None and self.watch_worker.isRunning():
            self.watch_worker.cancel()
        self.stop_worker()
        self.status_label.setText("Stopped.")
        self.set_busy(self.run_btn, self.stop_btn, False)

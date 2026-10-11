"""Bug Spray — recon, triage and a submission draft for one bug-bounty target.

Third vertical moved out of `main.py` (phase 4, `docs/refactor_plan.md`).

The nmap half runs a local process, not a paid request: it goes nowhere near the
request guard, and `kill_nmap` is separate from `stop`, which cancels the LLM
analysis. Keeping the two apart is why `stop` does not touch the scan.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import shlex
import shutil

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSplitter, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)

from ui.panels.base import AgentPanel
from ui.panels.bug_spray_feed import BugSprayFeed
from ui.widgets import MenuComboBox, SectionView

SEVERITY_COLOURS = {
    "Critical": "#ff3333", "High": "#ff7722", "Medium": "#f0c040",
    "Low": "#3cff88", "Informational": "#4db8ff",
}


_CVSS_VERSION = re.compile(r"(?i)\bv(?:ersion)?\s*\d\.\d\b")
# A bare version straight after the word: "CVSS 3.1: 7.5", "CVSS3.1 score of 7.5".
_CVSS_LEADING_VERSION = re.compile(r"(?i)^\s*(?:v(?:ersion)?\s*)?[2-4]\.[01](?![\d.])")
_CVSS_VECTOR = re.compile(r"(?i)CVSS:\s*\d\.\d/[A-Z]{1,3}:[A-Z0-9:/.]+")
_CVSS_SCORE = re.compile(r"(?<![\d.])(10(?:\.0)?|\d(?:\.\d)?)(?![\d.])")


def extract_cvss_score(text: str) -> str | None:
    """The CVSS base score in a report, or None.

    ``CVSS.*?(\\d+\\.\\d+)`` read the version out of "CVSS v3.1: 7.5" and the
    tile said 3.1 for every report that followed the prompt. Version tokens
    and vector strings are dropped first, then the first number in 0-10 after
    the word CVSS is the score.
    """
    match = re.search(r"CVSS(.{0,160})", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    tail = _CVSS_VECTOR.sub(" ", "CVSS" + match.group(1))
    # The vector, when present, took the leading "CVSS" with it.
    tail = tail[4:] if tail[:4].upper() == "CVSS" else tail
    tail = _CVSS_LEADING_VERSION.sub(" ", tail, count=1)
    tail = _CVSS_VERSION.sub(" ", tail)
    score = _CVSS_SCORE.search(tail)
    if not score:
        return None
    value = score.group(1)
    return value if "." in value else f"{value}.0"


NMAP_TIMEOUT_MS = 10 * 60 * 1000     # a scan the operator has not killed by then is stuck
NMAP_OUTPUT_LIMIT = 256 * 1024       # characters kept; enough for any single-host -sV -sC


NMAP_AUDIT_FILENAME = "bug_spray_audit.jsonl"
AUTHORISATION_STATEMENT = "I'm authorised to test this target under this program's rules"


def record_nmap_attempt(outcome: str, target: str, program: str, command: str,
                        detail: str = "", audit_path: Path | None = None) -> Path | None:
    """Append one audit line for an Nmap attempt: declined, refused or started.

    Uses the same local JSON-lines facility as Tunnel's consent audit
    (``vpn_execution.append_audit``: one line per attempt, mode 0600, never
    raises) in its own file next to it. Sentinel does not verify the scope, so
    the line records only what the operator said and entered.
    """
    from services import vpn_execution

    path = audit_path or (vpn_execution.default_audit_path().parent / NMAP_AUDIT_FILENAME)
    return vpn_execution.append_audit({
        "time": vpn_execution._now(),
        "action": "nmap",
        "protocol": "bug-spray",
        "target": vpn_execution.redact_secrets(target)[:200],
        "program": vpn_execution.redact_secrets(program)[:200],
        "command": vpn_execution.redact_secrets(command)[:500],
        "outcome": outcome,
        "detail": vpn_execution.redact_secrets(detail)[:500],
    }, path)


class BugBountyPanel(AgentPanel):
    """Triage findings into a severity, a CVSS score and a report to submit."""

    agent_key = "bug_bounty"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.setObjectName("BugBountyPanel")
        self._last_response = ""
        self._messages: list = []
        self._stopped = False
        self._nmap_process: QProcess | None = None
        self._nmap_scan_text = ""
        self._nmap_truncated = False
        self._nmap_timer: QTimer | None = None
        self._build()
        self.polish_workspace()
        self.hide()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.program_feed = BugSprayFeed(self)
        layout.addWidget(self.program_feed)

        # ── Target / program setup ───────────────────────────────────────
        setup_group = QGroupBox("Target && Program")
        setup_group.setObjectName("BBSetupBox")
        setup_layout = QGridLayout(setup_group)
        setup_layout.setSpacing(6)

        setup_layout.addWidget(QLabel("Target URL / IP:"), 0, 0)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("https://target.example.com  or  10.0.0.1")
        setup_layout.addWidget(self.target_input, 0, 1, 1, 3)

        setup_layout.addWidget(QLabel("Program:"), 1, 0)
        self.program_input = QLineEdit()
        self.program_input.setPlaceholderText("HackerOne — Acme Corp  /  Bugcrowd — Example")
        setup_layout.addWidget(self.program_input, 1, 1, 1, 3)
        self.program_feed.program_selected.connect(self.program_input.setText)

        setup_layout.addWidget(QLabel("Scope Type:"), 2, 0)
        self.scope_box = MenuComboBox()
        self.scope_box.addItems([
            "Web Application", "API / REST", "Mobile (Android)", "Mobile (iOS)",
            "Network / Infrastructure", "Source Code Review", "Cloud Config", "Other",
        ])
        setup_layout.addWidget(self.scope_box, 2, 1)

        setup_layout.addWidget(QLabel("Severity Target:"), 2, 2)
        self.severity_box = MenuComboBox()
        self.severity_box.addItems(
            ["Critical (P1)", "High (P2)", "Medium (P3)", "Low (P4)", "Informational"])
        setup_layout.addWidget(self.severity_box, 2, 3)

        scope_note = QLabel(
            "Program and Scope are declared by you and are not verified or enforced. "
            "Bug Spray drafts a report from what you enter; only stay within programs "
            "you are authorised to test."
        )
        scope_note.setWordWrap(True)
        scope_note.setStyleSheet("font-size: 11px; color: #999;")
        setup_layout.addWidget(scope_note, 3, 0, 1, 4)

        layout.addWidget(setup_group)

        # ── Nmap scan section ────────────────────────────────────────────
        nmap_group = QGroupBox("Nmap Recon Scan")
        nmap_group.setObjectName("BBNmapBox")
        nmap_layout = QVBoxLayout(nmap_group)
        nmap_layout.setSpacing(4)

        nmap_cmd_row = QHBoxLayout()
        self.nmap_cmd_input = QLineEdit()
        self.nmap_cmd_input.setPlaceholderText("nmap -sV -sC -T4 --open <target>")
        nmap_cmd_row.addWidget(self.nmap_cmd_input, 1)
        self.nmap_run_btn = QPushButton("Run Nmap")
        self.nmap_run_btn.setMinimumWidth(120)
        self.nmap_run_btn.setObjectName("PrimaryAction")
        self.nmap_run_btn.clicked.connect(self.run_nmap)
        nmap_cmd_row.addWidget(self.nmap_run_btn)
        self.nmap_stop_btn = QPushButton("Kill")
        self.nmap_stop_btn.setEnabled(False)
        self.nmap_stop_btn.setObjectName("DangerAction")
        self.nmap_stop_btn.clicked.connect(self.kill_nmap)
        nmap_cmd_row.addWidget(self.nmap_stop_btn)
        self.set_busy(self.nmap_run_btn, self.nmap_stop_btn, False)
        nmap_layout.addLayout(nmap_cmd_row)

        nmap_note = QLabel(
            "Runs this command on your machine (the first word is the program to "
            "launch), with no scope check and outside the budget/authorisation guard. "
            "Each run asks you to confirm you are authorised to test the target under "
            "the named program's rules, and needs the Program field filled in."
        )
        nmap_note.setWordWrap(True)
        nmap_note.setStyleSheet("font-size: 11px; color: #999;")
        nmap_layout.addWidget(nmap_note)

        self.nmap_output = QTextBrowser()
        self.nmap_output.setOpenExternalLinks(False)
        self.nmap_output.setMinimumHeight(130)
        self.nmap_output.setMaximumHeight(220)
        self.nmap_output.setPlaceholderText("Nmap output will appear here…")
        nmap_layout.addWidget(self.nmap_output)
        layout.addWidget(nmap_group)

        # ── Findings / Burp paste area ───────────────────────────────────
        findings_group = QGroupBox("Findings / Burp Suite Output / Notes")
        findings_group.setObjectName("BBFindingsBox")
        findings_layout = QVBoxLayout(findings_group)
        self.findings_input = QTextEdit()
        self.findings_input.setPlaceholderText(
            "Paste HTTP request/response, Burp Suite output, manual observations, "
            "error messages, source code snippets — anything in scope."
        )
        self.findings_input.setMinimumHeight(110)
        findings_layout.addWidget(self.findings_input)
        layout.addWidget(findings_group)

        self.analyse_btn = QPushButton("Analyse")
        self.analyse_btn.setMinimumWidth(130)
        self.analyse_btn.setObjectName("PrimaryAction")
        self.analyse_btn.clicked.connect(self.analyse)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setObjectName("DangerAction")
        self.stop_btn.clicked.connect(self.stop)
        self.set_busy(self.analyse_btn, self.stop_btn, False)

        # ── Provider row ─────────────────────────────────────────────────
        provider_row_container = self.build_run_bar(
            self.analyse_btn,
            stop=self.stop_btn,
            context="Analysis",
        )
        layout.addWidget(provider_row_container)

        # ── Results: tabs + sidebar ──────────────────────────────────────
        results_splitter = QSplitter(Qt.Horizontal)

        output_widget = QWidget()
        output_layout = QVBoxLayout(output_widget)
        output_layout.setContentsMargins(0, 0, 0, 0)

        # A complete answer is rendered as cards. While tokens are arriving,
        # the raw stream temporarily takes their place.
        self.stream_box = QTextBrowser()
        self.stream_box.setOpenExternalLinks(False)
        self.stream_box.setVisible(False)
        output_layout.addWidget(self.stream_box, 1)

        self.sections = SectionView()
        output_layout.addWidget(self.sections, 1)
        results_splitter.addWidget(output_widget)

        # Sidebar indicators
        indicators_widget = QWidget()
        ind_layout = QVBoxLayout(indicators_widget)
        ind_layout.setContentsMargins(8, 0, 0, 0)
        ind_layout.setSpacing(10)

        sev_group = QGroupBox("Severity")
        sev_group.setObjectName("BBSevBox")
        sev_inner = QVBoxLayout(sev_group)
        self.severity_label = QLabel("—")
        self.severity_label.setAlignment(Qt.AlignCenter)
        self.severity_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #ff5555;")
        sev_inner.addWidget(self.severity_label)
        ind_layout.addWidget(sev_group)

        cvss_group = QGroupBox("CVSS Score")
        cvss_group.setObjectName("BBCvssBox")
        cvss_inner = QVBoxLayout(cvss_group)
        self.cvss_label = QLabel("—")
        self.cvss_label.setAlignment(Qt.AlignCenter)
        self.cvss_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        cvss_inner.addWidget(self.cvss_label)
        ind_layout.addWidget(cvss_group)

        bounty_group = QGroupBox("Bounty Estimate")
        bounty_group.setObjectName("BBBountyBox")
        bounty_inner = QVBoxLayout(bounty_group)
        self.bounty_label = QLabel("—")
        self.bounty_label.setAlignment(Qt.AlignCenter)
        self.bounty_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #3cff88;")
        bounty_inner.addWidget(self.bounty_label)
        ind_layout.addWidget(bounty_group)

        ind_layout.addStretch()

        self.save_btn = QPushButton("Save Report")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save)
        ind_layout.addWidget(self.save_btn)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        ind_layout.addWidget(self.clear_btn)

        results_splitter.addWidget(indicators_widget)
        results_splitter.setSizes([700, 200])
        layout.addWidget(results_splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(self.status_label)

    # ── Recon (local, unpaid) ───────────────────────────────────────────
    def run_nmap(self) -> None:
        cmd_text = self.nmap_cmd_input.text().strip()
        if not cmd_text:
            target = self.target_input.text().strip()
            if not target:
                self.nmap_output.setPlainText(
                    "[Error] Enter a target URL/IP or nmap command first.")
                return
            # strip protocol for nmap
            host = target.replace("https://", "").replace("http://", "").split("/")[0]
            cmd_text = f"nmap -sV -sC -T4 --open {host}"
            self.nmap_cmd_input.setText(cmd_text)

        try:
            program, args = self._nmap_argv(cmd_text)
        except ValueError as exc:
            self.nmap_output.setPlainText(f"[Error] {exc}")
            return

        scan_target = self.target_input.text().strip() or (shlex.split(cmd_text) or [""])[-1]
        program_name = self.program_input.text().strip()
        if not program_name:
            reason = ("Enter the Program (for example 'HackerOne — Acme Corp') before running "
                      "Nmap. Sentinel does not verify scope, so it will not scan until you "
                      "have named the program whose rules authorise this target.")
            record_nmap_attempt("refused", scan_target, program_name, cmd_text,
                                "Program field is empty.")
            self.nmap_output.setPlainText("[Refused] Program field is empty; nothing was run.")
            self.status_label.setText("Nmap not run: enter the Program first.")
            QMessageBox.warning(self, "Program required", reason + "\n\nNothing was run.")
            return
        answer = QMessageBox.question(
            self, "Confirm authorisation",
            f"{AUTHORISATION_STATEMENT}.\n\nTarget: {scan_target}\n"
            f"Program: {program_name}\nCommand: {cmd_text}\n\n"
            "Sentinel does not verify scope. Continue only if you have checked the "
            "program's current rules.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            record_nmap_attempt("declined", scan_target, program_name, cmd_text,
                                "Operator declined the authorisation confirmation.")
            self.nmap_output.setPlainText("[Not run] Authorisation was not confirmed.")
            self.status_label.setText("Nmap not run: authorisation not confirmed.")
            return
        record_nmap_attempt("started", scan_target, program_name, cmd_text)

        self._nmap_scan_text = ""
        self._nmap_truncated = False
        self.nmap_output.setPlainText(f"[Running] {cmd_text}\n")
        self.set_busy(self.nmap_run_btn, self.nmap_stop_btn, True)

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyRead.connect(self._nmap_read)
        process.finished.connect(self._nmap_finished)
        process.errorOccurred.connect(self._nmap_error)
        self._nmap_process = process
        self._nmap_timer = QTimer(self)
        self._nmap_timer.setSingleShot(True)
        self._nmap_timer.timeout.connect(self._nmap_timed_out)
        self._nmap_timer.start(NMAP_TIMEOUT_MS)
        process.start(program, args)

    @staticmethod
    def _nmap_argv(cmd_text: str) -> tuple[str, list[str]]:
        """The scanner argv, or ValueError. Only nmap is ever started: the box
        is a scan command, not a shell, and a stray first word must not become
        a program run with the operator's privileges."""
        parts = shlex.split(cmd_text)
        if not parts:
            raise ValueError("Enter an nmap command.")
        if Path(parts[0]).name != "nmap":
            raise ValueError(
                f"Only nmap can be run from here (got '{parts[0]}').")
        program = shutil.which("nmap")
        if program is None:
            for candidate in ("/opt/homebrew/bin/nmap", "/usr/local/bin/nmap"):
                if Path(candidate).is_file():
                    program = candidate
                    break
        if program is None:
            raise ValueError("nmap is not installed (brew install nmap).")
        return program, parts[1:]

    @property
    def nmap_scan_text(self) -> str:
        """What the scanner printed, without the panel's own status markers."""
        return getattr(self, "_nmap_scan_text", "")

    def _nmap_read(self) -> None:
        data = self._nmap_process.readAll().data().decode("utf-8", errors="replace")
        room = NMAP_OUTPUT_LIMIT - len(self._nmap_scan_text)
        if room <= 0:
            return
        if len(data) > room:
            data = data[:room]
            self._nmap_truncated = True
        self._nmap_scan_text += data
        self.nmap_output.moveCursor(QTextCursor.End)
        self.nmap_output.insertPlainText(data)
        self.nmap_output.moveCursor(QTextCursor.End)
        if self._nmap_truncated:
            self.nmap_output.insertPlainText(
                f"\n[Output capped at {NMAP_OUTPUT_LIMIT // 1024} KB; stopping the scan]")
            self._nmap_process.kill()

    def _nmap_stop_timer(self) -> None:
        timer = getattr(self, "_nmap_timer", None)
        if timer is not None:
            timer.stop()

    def _nmap_finished(self, exit_code: int = 0, _status=None) -> None:
        self._nmap_stop_timer()
        self.set_busy(self.nmap_run_btn, self.nmap_stop_btn, False)
        self.nmap_output.moveCursor(QTextCursor.End)
        self.nmap_output.insertPlainText(
            "\n[Done]" if exit_code == 0 else f"\n[Done — nmap exited {exit_code}]")

    def _nmap_error(self, error) -> None:
        # QProcess never emits finished() for a start failure, which left the
        # Run button disabled for good when nmap was missing or not executable.
        from PySide6.QtCore import QProcess as _QP
        if error == _QP.FailedToStart:
            self._nmap_stop_timer()
            self.set_busy(self.nmap_run_btn, self.nmap_stop_btn, False)
            self.nmap_output.moveCursor(QTextCursor.End)
            self.nmap_output.insertPlainText(
                "\n[Error] nmap could not be started (not found or not executable).")

    def _nmap_timed_out(self) -> None:
        if self._nmap_process is not None and self._nmap_process.state() != QProcess.NotRunning:
            self.nmap_output.moveCursor(QTextCursor.End)
            self.nmap_output.insertPlainText(
                f"\n[Error] Scan stopped after {NMAP_TIMEOUT_MS // 60000} minutes; "
                "narrow the target or ports.")
            self._nmap_process.kill()

    def kill_nmap(self) -> None:
        self._nmap_stop_timer()
        if self._nmap_process is not None:
            self._nmap_process.kill()
        self.set_busy(self.nmap_run_btn, self.nmap_stop_btn, False)

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Kill a running scan and cancel the model worker before the widgets go."""
        self.program_feed.shutdown(timeout_ms)
        self._nmap_stop_timer()
        process = self._nmap_process
        if process is not None and process.state() != QProcess.NotRunning:
            process.kill()
            process.waitForFinished(timeout_ms)
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if hasattr(self.worker, "wait"):
                self.worker.wait(timeout_ms)

    # ── Analysis (paid) ─────────────────────────────────────────────────
    def analyse(self) -> None:
        target = self.target_input.text().strip()
        program = self.program_input.text().strip()
        scope_type = self.scope_box.currentText()
        findings = self.findings_input.toPlainText().strip()
        # The model gets what the scanner printed, never the panel's own
        # [Running]/[Done]/[Error] markers dressed up as scan evidence.
        nmap_output = self.nmap_scan_text.strip()

        if not target and not findings and not nmap_output:
            self.status_label.setText(
                "Enter a target, paste findings, or run a scan first.")
            return

        if not self.model:
            self.status_label.setText("Select a model first.")
            return

        messages = self.agent().build_messages(
            target, program, scope_type, findings, nmap_output,
            severity=self.severity_box.currentText())
        self._messages = messages
        self._stopped = False

        self._last_response = ""
        self._clear_results()
        self.severity_label.setText("—")
        self.cvss_label.setText("—")
        self.bounty_label.setText("—")
        self.save_btn.setEnabled(False)
        self.status_label.setText("Analysing…")
        self.set_busy(self.analyse_btn, self.stop_btn, True)

        prompt = target or "bug_bounty"
        # Authorise against the text that is really sent: findings and scan
        # output are usually kilobytes, the target a few dozen characters.
        full_text = "\n".join(str(m.get("content", "")) for m in messages)
        if not self.authorize(full_text, label=prompt):
            # The controls were already disabled above; a blocked request has to
            # put them back or the panel is stuck with a dead Analyse button.
            self.status_label.setText("Blocked before sending.")
            self.set_busy(self.analyse_btn, self.stop_btn, False)
            return

        self.start_worker(
            messages, prompt,
            on_token=self._on_token,
            on_finished=self._on_finished,
            on_error=self._on_error,
        )

    def _on_token(self, token: str) -> None:
        if self._stopped:
            return
        self._last_response += token
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(self._last_response)
        self.stream_box.moveCursor(QTextCursor.End)

    def _on_finished(self, full_response: str) -> None:
        if self._stopped:
            # A non-streaming reply can still land after Stop; the operator
            # asked for the request to end, so it is neither shown nor billed
            # as a finished analysis.
            self.abandon("cancelled")
            return
        self.record(full_response, self._messages)
        self._last_response = full_response
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)
        self._populate_sections(full_response)
        self._update_indicators(full_response)
        self.status_label.setText("Analysis complete.")
        self.set_busy(self.analyse_btn, self.stop_btn, False)
        self.save_btn.setEnabled(True)

    def _on_error(self, error: str) -> None:
        if self._stopped:
            # The worker's "Request cancelled" error is the echo of Stop, not
            # a failure: keep "Stopped." and the partial text.
            self.abandon("cancelled")
            return
        self.abandon(error=error)
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(f"[Error] {error}")
        self.status_label.setText("Error.")
        self.set_busy(self.analyse_btn, self.stop_btn, False)

    def stop(self) -> None:
        if self.stop_worker():
            self._stopped = True
        self.status_label.setText("Stopped.")
        self.set_busy(self.analyse_btn, self.stop_btn, False)

    # ── Report ──────────────────────────────────────────────────────────
    def _populate_sections(self, text: str) -> None:
        sections = self.parse_sections(text)
        self.sections.show_sections(
            [
                ("Vulnerability report", sections["vulnerability"]),
                ("Proof of concept", sections["poc"], True),
                ("Remediation", sections["remediation"]),
                ("Submission draft", sections["submission"]),
            ],
            raw=text,
        )

    @staticmethod
    def parse_sections(text: str) -> dict[str, str]:
        """Split the report into cards; absent sections stay empty.

        The system prompt asks for the PoC and the remediation as numbered
        bold items *inside* ``## VULNERABILITY REPORT`` (``5. **Proof of
        Concept** — …``), not as ``##`` headings. The parser accepted only the
        heading form, so with a model that followed the prompt those two cards
        were always empty. Both forms are accepted; an item ends at the next
        numbered bold item or the next ``##`` heading.
        """
        item_end = r"(?=\n\s*\d+\.\s*\*\*|\n##|$)"
        patterns = {
            "vulnerability": (
                r"(?:##\s*VULNERABILITY\s*REPORT|##\s*Vulnerability Details?)"
                r"(.*?)(?=\n##|$)"
            ),
            "poc": (
                r"(?:##\s*Proof of Concept\s*\n|PoC\s*Draft:?\s*\n"
                r"|\d+\.\s*\*\*Proof of Concept\*\*\s*[—:\-]?\s*)"
                r"(.*?)" + item_end
            ),
            "remediation": (
                r"(?:##\s*Remediation\s*\n|\d+\.\s*\*\*Remediation\*\*\s*[—:\-]?\s*)"
                r"(.*?)" + item_end
            ),
            "submission": (
                r"(?:##\s*SUBMISSION\s*DRAFT|Submission\s*Draft?)"
                r"(.*?)(?=\n##|$)"
            ),
        }
        result = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            result[key] = match.group(1).strip() if match else ""
        return result

    def _update_indicators(self, text: str) -> None:
        sev_m = re.search(
            r"\*\*Severity\*\*.*?(Critical|High|Medium|Low|Informational)",
            text, re.IGNORECASE)
        if sev_m:
            sev = sev_m.group(1).capitalize()
            self.severity_label.setText(sev)
            self.severity_label.setStyleSheet(
                "font-size: 20px; font-weight: bold; "
                f"color: {SEVERITY_COLOURS.get(sev, '#ffffff')};"
            )

        cvss = extract_cvss_score(text)
        if cvss is not None:
            score = float(cvss)
            color = ("#ff3333" if score >= 9 else "#ff7722" if score >= 7
                     else "#f0c040" if score >= 4 else "#3cff88")
            self.cvss_label.setText(cvss)
            self.cvss_label.setStyleSheet(
                f"font-size: 22px; font-weight: bold; color: {color};")

        bounty_m = re.search(
            r"bounty.*?(\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?|\$[\d,]+\+?)",
            text, re.IGNORECASE)
        if bounty_m:
            self.bounty_label.setText(bounty_m.group(1))

    def save(self) -> None:
        if not self._last_response:
            return
        target = (self.target_input.text().strip()
                  .replace("/", "-").replace(":", "").replace(" ", "_")) or "target"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = QFileDialog.getSaveFileName(
            self, "Save Bug Bounty Report",
            str(Path.home() / "Downloads" / f"bb_report_{target}_{ts}.md"),
            "Markdown (*.md);;Text (*.txt)",
        )[0]
        if path:
            Path(path).write_text(self._last_response, encoding="utf-8")
            self.status_label.setText(f"Saved: {path}")

    def clear(self) -> None:
        self.target_input.clear()
        self.program_input.clear()
        self.findings_input.clear()
        self.nmap_output.clear()
        self.nmap_cmd_input.clear()
        self._nmap_scan_text = ""
        self._clear_results()
        self.severity_label.setText("—")
        self.severity_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #ff5555;")
        self.cvss_label.setText("—")
        self.cvss_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        self.bounty_label.setText("—")
        self.status_label.setText("")
        self.save_btn.setEnabled(False)
        self._last_response = ""

    def _clear_results(self) -> None:
        self.sections.clear()
        self.stream_box.clear()
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)

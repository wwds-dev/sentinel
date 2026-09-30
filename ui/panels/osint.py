"""Trace — the light OSINT panel.

First vertical moved out of `main.py` (phase 4, `docs/refactor_plan.md`). The
bodies are the ones that ran in `GodAI`; what changed is where the widgets live
and how the panel reaches the application:

- widgets are the panel's own attributes, so the `osint_` prefix that kept them
  apart in a shared namespace is gone;
- the request guard, the worker and the model list go through `AgentPanel`,
  which is the whole reason the base exists.
"""

from __future__ import annotations

import json
import re

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTextBrowser,
    QVBoxLayout,
)

from ui.widgets import MenuComboBox, SectionView
from ui.panels.base import AgentPanel
from services import osint_catalog
from services.deepseek_client import is_insufficient_balance_error
from ui.workers import DomainLookupWorker, ExposureLookupWorker, IdentityLookupWorker


class OsintPanel(AgentPanel):
    """Structure a target into search queries, dorks and public sources."""

    agent_key = "osint"
    lookup_worker_class = DomainLookupWorker
    identity_lookup_worker_class = IdentityLookupWorker
    exposure_lookup_worker_class = ExposureLookupWorker

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.setObjectName("OSINTPanel")
        self._last_response = ""
        self._activity_entries: list[str] = []
        self._received_first_token = False
        self._build()
        self.polish_workspace()
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The OSINT Framework catalogue feeds this agent's source suggestions;
        # refresh it off the UI thread, at most once a week (and once a run).
        osint_catalog.refresh_in_background()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ── Target form ──────────────────────────────────────────────────
        setup_group = QGroupBox("Target")
        setup_group.setObjectName("OSINTSetupBox")
        setup_layout = QGridLayout(setup_group)
        setup_layout.setSpacing(6)

        setup_layout.addWidget(QLabel("Target:"), 0, 0)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText(
            "Enter name, username, email, domain, company, phone, or IP…"
        )
        setup_layout.addWidget(self.target_input, 0, 1, 1, 3)

        setup_layout.addWidget(QLabel("Query Type:"), 1, 0)
        self.type_box = MenuComboBox()
        self.type_box.addItems([
            "Auto-detect", "Person", "Username", "Email",
            "Domain", "Company", "Phone", "IP Address",
        ])
        setup_layout.addWidget(self.type_box, 1, 1)

        self.analyse_btn = QPushButton("Structure Query")
        self.analyse_btn.setMinimumWidth(150)
        self.analyse_btn.setObjectName("PrimaryAction")
        self.analyse_btn.clicked.connect(self.analyse)

        self.live_btn = QPushButton("Live Research")
        self.live_btn.setMinimumWidth(130)
        self.live_btn.setToolTip(
            "Check public WHOIS, DNS, certificate-transparency and web-archive "
            "sources after explicit confirmation. Supports domains, IPs, "
            "usernames, emails, and companies."
        )
        self.live_btn.clicked.connect(self.live_research)

        self.exposure_btn = QPushButton("Exposure Check")
        self.exposure_btn.setMinimumWidth(140)
        self.exposure_btn.setToolTip(
            "Dark-web exposure check: is this domain, company or email in a leak "
            "or on a ransomware leak site? Uses clearnet services (ransomware.live, "
            "Ahmia, Intelligence X) after explicit confirmation. Text results only; "
            "no onion sites are contacted and nothing is downloaded."
        )
        self.exposure_btn.clicked.connect(self.exposure_check)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setObjectName("DangerAction")
        self.stop_btn.clicked.connect(self.stop)
        self._set_trace_busy(False)

        provider_row_container = self.build_run_bar(
            self.analyse_btn,
            stop=self.stop_btn,
            secondary=(self.live_btn, self.exposure_btn),
            context="Query",
        )

        setup_layout.addWidget(provider_row_container, 2, 0, 1, 4)
        layout.addWidget(setup_group)

        # ── Persistent activity trail ───────────────────────────────────
        activity_group = QGroupBox("Activity")
        activity_group.setObjectName("OSINTActivityBox")
        activity_layout = QVBoxLayout(activity_group)
        self.activity_box = QTextBrowser()
        self.activity_box.setObjectName("OSINTActivityLog")
        self.activity_box.setOpenExternalLinks(False)
        self.activity_box.setMinimumHeight(116)
        self.activity_box.setMaximumHeight(180)
        activity_layout.addWidget(self.activity_box)
        layout.addWidget(activity_group)
        self._reset_activity()

        # ── Output ───────────────────────────────────────────────────────
        # The answer is already parsed into four sections; render it as those
        # sections rather than pouring each into its own tabbed text box. Copy
        # lives per card, so the dorks are still one click from the clipboard.
        self.stream_box = QTextBrowser()
        self.stream_box.setOpenExternalLinks(False)
        self.stream_box.setVisible(False)
        layout.addWidget(self.stream_box, 1)

        self.sections = SectionView()
        layout.addWidget(self.sections, 1)

        # ── Bottom bar ───────────────────────────────────────────────────
        bottom_row = QHBoxLayout()
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 12px; color: #888;")
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        bottom_row.addWidget(clear_btn)
        layout.addLayout(bottom_row)

    # ── Running ─────────────────────────────────────────────────────────
    def analyse(self) -> None:
        target = self.target_input.text().strip()
        query_type = self.type_box.currentText()

        if not target:
            QMessageBox.warning(self, "Missing Input", "Please enter a target.")
            return
        if not self.model:
            QMessageBox.warning(self, "No Model", "Please select a model.")
            return

        validation = self.agent().validate_target(target, query_type)
        if not validation.valid:
            self._reset_activity()
            self._append_activity(f"Target validation stopped the run: {validation.message}")
            self.status_label.setText("Check the target and try again.")
            QMessageBox.warning(self, "Invalid Target", validation.message)
            return

        effective_type = validation.query_type
        messages = self.agent().build_messages(target, effective_type)

        if not self.authorize(target, label=effective_type):
            return

        self._clear_output()
        self._last_response = ""
        self._received_first_token = False
        self._reset_activity()
        detected_note = " (auto-detected)" if query_type == "Auto-detect" else ""
        self._append_activity(
            f"Target accepted: {target} ({effective_type}{detected_note})."
        )
        if self.provider == "ollama":
            self._append_activity(
                f"Running locally with Ollama · {self.model}; the target stays on this Mac."
            )
        else:
            self._append_activity(
                f"Using {self.provider} · {self.model} after the provider permission check."
            )
        self._append_activity(
            "Scope confirmed: planning queries only. No websites or public databases "
            "are contacted by Trace in this mode."
        )
        self._append_activity(
            "Building target components, search variations, Google dorks, and a "
            "recommended public-source checklist…"
        )
        notice = getattr(self, "_route_notice", "")
        self.status_label.setText(
            f"{notice} Structuring query…" if notice else "Structuring query…"
        )
        self._route_notice = ""
        self._set_trace_busy(True)

        self.start_worker(
            messages, target,
            on_token=self._on_token,
            on_finished=self._on_finished,
            on_error=self._on_error,
        )

    def _on_token(self, token: str) -> None:
        # While tokens arrive there are no sections to show yet, so the raw
        # stream is the view; the cards replace it once the answer is whole.
        self._last_response += token
        if not self._received_first_token:
            self._received_first_token = True
            self._append_activity("Model response received; assembling the four result sections…")
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(self._last_response)
        self.stream_box.moveCursor(QTextCursor.End)

    def _on_finished(self, full_response: str) -> None:
        self._last_response = full_response
        self.record(full_response)
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)
        self._populate_sections(full_response)
        self._append_activity(
            "Completed. External sources queried: none. The displayed sources are "
            "recommendations for the user to check, not verified findings."
        )
        self.status_label.setText("Done.")
        self._set_trace_busy(False)

    def _on_error(self, error: str) -> None:
        self.abandon()
        balance_error = is_insufficient_balance_error(error)
        if balance_error:
            error = self._prepare_balance_recovery()
        self._append_activity(f"Run ended before completion: {error}")
        separator = "─" * 50
        self.stream_box.setVisible(True)
        self.sections.setVisible(False)
        self.stream_box.setPlainText(
            f"⚠  ERROR\n{separator}\n{error}\n{separator}"
        )
        if balance_error:
            self.status_label.setText(
                "Ready to retry locally."
                if self.provider == "ollama" else "Action needed."
            )
        else:
            self.status_label.setText("Error.")
        self._set_trace_busy(False)

    def _prepare_balance_recovery(self) -> str:
        """Select, but never automatically run, a local retry after a 402."""
        previous_provider = self.provider
        self.provider_box.setCurrentText("ollama")
        local_model = self.model
        unavailable = not local_model or local_model.startswith("(")
        if unavailable:
            self.provider_box.setCurrentText(previous_provider)
            return (
                "The DeepSeek cloud account has no API credit, so this request "
                "could not finish. "
                "No local model is currently available. Add DeepSeek credit or "
                "choose another provider. Trace will ask before sending the target "
                "to a different cloud service."
            )
        self._prefer_local_retry_once = True
        return (
            "The DeepSeek cloud account has no API credit, so this request could "
            "not finish. Local DeepSeek through Ollama is free. "
            f"Trace selected the local model {local_model} for a safe retry, but "
            "did not resend the target. Click Structure Query to retry on this Mac. "
            "To use another cloud provider, choose it yourself; Trace will ask "
            "before sending the target."
        )

    def stop(self) -> None:
        self.stop_worker()
        self._append_activity("Stopped by the user. No further processing was performed.")
        self.status_label.setText("Stopped.")
        self._set_trace_busy(False)

    def _set_trace_busy(self, busy: bool) -> None:
        self.set_busy(self.analyse_btn, self.stop_btn, busy)
        for name in ("live_btn", "exposure_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.setVisible(not busy)
                button.setEnabled(not busy)

    # ── Explicit live public-source research ───────────────────────────
    def live_research(self) -> None:
        target = self.target_input.text().strip()
        validation = self.agent().validate_target(target, self.type_box.currentText())
        if not validation.valid:
            QMessageBox.warning(self, "Invalid Target", validation.message)
            return
        if validation.query_type not in {
            "Domain", "IP Address", "Username", "Email", "Company"
        }:
            privacy_note = (
                "Person and phone Live Research is intentionally unavailable. "
                "Trace will not send personal identifiers to people-search, "
                "reverse-phone, or data-broker services. Use Structure Query to "
                "build a local research plan instead."
                if validation.query_type in {"Person", "Phone"}
                else "Use Structure Query for this target type."
            )
            QMessageBox.information(
                self, "Target Type Not Available",
                privacy_note,
            )
            return

        selected_sources = ()
        if validation.query_type == "Email":
            selected_sources = self._choose_email_sources(target)
            if not selected_sources:
                self.status_label.setText("Live Research cancelled before any lookup.")
                return
            labels = {
                "emailrep": "EmailRep",
                "gravatar": "Gravatar",
                "hibp": "Have I Been Pwned",
                "breachdirectory": "BreachDirectory",
            }
            sources = ", ".join(labels[source] for source in selected_sources)
        else:
            source_map = {
                "IP Address": ("WHOIS, DNS, Team Cymru IP-to-ASN, SANS DShield, "
                               "Shodan InternetDB, and Mnemonic passive DNS"),
                "Domain": ("WHOIS, DNS, Team Cymru IP-to-ASN, Mnemonic passive DNS, "
                           "crt.sh, and the Wayback Machine"),
                "Username": "URLScan, GitHub, and Keybase",
                "Company": "GLEIF Legal Entity Index and CourtListener court records",
            }
            sources = source_map[validation.query_type]
            if validation.query_type == "IP Address":
                # Mirror the key-gated IP sources that domain_lookup.lookup()
                # contacts, so the consent text names every service that will
                # actually receive the target IP.
                import os

                extra = []
                if os.getenv("IPINFO_API_KEY", "").strip():
                    extra.append("IPinfo")
                if os.getenv("CRIMINALIP_API_KEY", "").strip():
                    extra.append("Criminal IP")
                if extra:
                    sources += ", " + ", ".join(extra)
            if validation.query_type == "Company":
                from providers.company_lookup import OPENSANCTIONS_KEY

                # OpenSanctions needs a key for every call; without one it is
                # neither named here nor contacted. CourtListener always runs.
                if OPENSANCTIONS_KEY:
                    selected_sources = ("opensanctions",)
                    sources = ("GLEIF Legal Entity Index, OpenSanctions, and "
                               "CourtListener court records")
            consent = QMessageBox.question(
                self,
                "Confirm Live Research",
                f"Trace will send this target to public research services:\n\n"
                f"{target}\n\nSources: {sources}\n\n"
                "This is a real external lookup, not local model processing. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if consent != QMessageBox.Yes:
                self.status_label.setText("Live Research cancelled before any lookup.")
                return

        self._clear_output()
        self._reset_activity()
        self._append_activity(
            f"Consent recorded. Live Research target: {target} "
            f"({validation.query_type})."
        )
        self._append_activity(f"Approved external sources: {sources}.")
        self.status_label.setText("Checking public sources…")
        self._set_trace_busy(True)

        if validation.query_type in {"Domain", "IP Address"}:
            worker = self.lookup_worker_class(target)
        else:
            worker = self.identity_lookup_worker_class(
                target, validation.query_type, selected_sources
            )
        worker.progress_signal.connect(self._on_lookup_progress)
        worker.finished_signal.connect(self._on_lookup_finished)
        worker.error_signal.connect(self._on_lookup_error)
        self.worker = worker
        worker.start()

    def _choose_email_sources(self, target: str) -> tuple[str, ...]:
        """Ask separately which services may receive a complete email address."""
        from providers.email_lookup import HIBP_KEY

        dialog = QDialog(self)
        dialog.setWindowTitle("Choose Email Research Sources")
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            f"The complete address {target} will be sent only to the services "
            "selected below. Breach services are off by default."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        emailrep = QCheckBox("EmailRep — reputation and public profile signals")
        emailrep.setChecked(True)
        gravatar = QCheckBox(
            "Gravatar — public profile for the address (only its SHA-256 hash is sent)"
        )
        gravatar.setChecked(True)
        hibp = QCheckBox("Have I Been Pwned — breach and paste records (API key required)")
        hibp.setEnabled(bool(HIBP_KEY))
        breach = QCheckBox("BreachDirectory — open breach-index search")
        layout.addWidget(emailrep)
        layout.addWidget(gravatar)
        layout.addWidget(hibp)
        layout.addWidget(breach)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return ()
        selected = []
        if emailrep.isChecked():
            selected.append("emailrep")
        if gravatar.isChecked():
            selected.append("gravatar")
        if hibp.isChecked():
            selected.append("hibp")
        if breach.isChecked():
            selected.append("breachdirectory")
        if not selected:
            QMessageBox.information(
                self, "No Sources Selected", "Select at least one email research source."
            )
        return tuple(selected)

    # ── Dark-web exposure check ────────────────────────────────────────────
    _EXPOSURE_LABELS = {
        "ransomware_live": "Ransomware.live",
        "ahmia": "Ahmia",
        "intelx": "Intelligence X",
        "dehashed": "DeHashed",
    }

    def exposure_check(self) -> None:
        """Check whether a domain, company or email is in a leak or on a
        ransomware leak site, using clearnet services behind explicit consent."""
        target = self.target_input.text().strip()
        validation = self.agent().validate_target(target, self.type_box.currentText())
        if not validation.valid:
            QMessageBox.warning(self, "Invalid Target", validation.message)
            return
        if validation.query_type not in {"Domain", "Company", "Email"}:
            QMessageBox.information(
                self, "Target Type Not Available",
                "The exposure check answers 'is my company, domain or email in a "
                "leak or on a ransomware leak site?'. Choose a Domain, Company, or "
                "Email target (or let Auto-detect resolve one of those).",
            )
            return

        selected = self._choose_exposure_sources(target, validation.query_type)
        if not selected:
            self.status_label.setText("Exposure check cancelled before any lookup.")
            return
        sources = ", ".join(self._EXPOSURE_LABELS[key] for key in selected)

        self._clear_output()
        self._reset_activity()
        self._append_activity(
            f"Consent recorded. Exposure check target: {target} "
            f"({validation.query_type})."
        )
        self._append_activity(f"Approved dark-web / leak sources: {sources}.")
        self._append_activity(
            "Text metadata only — no onion sites are contacted and nothing is downloaded."
        )
        self.status_label.setText("Checking leak and dark-web sources…")
        self._set_trace_busy(True)

        worker = self.exposure_lookup_worker_class(
            target, validation.query_type, selected
        )
        worker.progress_signal.connect(self._on_lookup_progress)
        worker.finished_signal.connect(self._on_lookup_finished)
        worker.error_signal.connect(self._on_lookup_error)
        self.worker = worker
        worker.start()

    def _choose_exposure_sources(self, target: str, query_type: str) -> tuple[str, ...]:
        """Ask which dark-web / leak services may receive the target."""
        from providers.exposure_lookup import INTELX_KEY, dehashed_key
        dehashed_available = bool(dehashed_key())

        dialog = QDialog(self)
        dialog.setWindowTitle("Choose Exposure Check Sources")
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            f"'{target}' will be sent only to the clearnet services selected "
            "below. Each does its own crawling under its own legal setup; Sentinel "
            "receives text results only and never contacts an onion site."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        ransomware = QCheckBox(
            "Ransomware.live — victims posted on ransomware leak sites (free)"
        )
        ransomware.setChecked(True)
        ahmia = QCheckBox(
            "Ahmia — clearnet search of indexed .onion sites, abuse content filtered (free)"
        )
        ahmia.setChecked(True)
        intelx = QCheckBox(
            "Intelligence X — leaks, pastes and archived dark-web material (paid API key required)"
        )
        intelx.setEnabled(bool(INTELX_KEY))
        intelx.setChecked(bool(INTELX_KEY))
        if not INTELX_KEY:
            intelx.setToolTip("Set INTELX_API_KEY in .env to enable Intelligence X.")
        dehashed = QCheckBox(
            "DeHashed — which breach databases the target is in; metadata only, "
            "no leaked passwords (paid API key required)"
        )
        dehashed.setEnabled(dehashed_available)
        dehashed.setChecked(dehashed_available)
        if not dehashed_available:
            dehashed.setToolTip("Set DEHASHED_API_KEY in .env to enable DeHashed.")
        layout.addWidget(ransomware)
        layout.addWidget(ahmia)
        layout.addWidget(intelx)
        layout.addWidget(dehashed)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return ()

        selected = []
        if ransomware.isChecked():
            selected.append("ransomware_live")
        if ahmia.isChecked():
            selected.append("ahmia")
        if intelx.isChecked():
            selected.append("intelx")
        if dehashed.isChecked():
            selected.append("dehashed")
        if not selected:
            QMessageBox.information(
                self, "No Sources Selected", "Select at least one exposure source."
            )
        return tuple(selected)

    def _on_lookup_progress(self, source: str, status: str) -> None:
        if status == "checking":
            self._append_activity(f"Checking {source}…")
        elif status == "checked":
            self._append_activity(f"{source} responded successfully.")
        elif status == "skipped":
            self._append_activity(f"{source} was skipped before contact.")
        else:
            self._append_activity(f"{source} returned an error; continuing with other sources.")

    @staticmethod
    def _lookup_text(value) -> str:
        if value is None:
            return "Not checked."
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)

    def _on_lookup_finished(self, result: dict) -> None:
        self._show_lookup_result(result, save=True)

    def _show_lookup_result(self, result: dict, *, save: bool) -> None:
        """Render a live result; saved searches use this without saving again."""
        contacted = result.get("sources_contacted", [])
        skipped = result.get("sources_skipped", [])
        checked = [item["source"] for item in contacted if item.get("status") == "checked"]
        failed = [item["source"] for item in contacted if item.get("status") == "error"]
        summary = [
            f"Target: {result.get('query', '')}",
            f"Sources contacted: {len(contacted)}",
            f"Successful: {', '.join(checked) if checked else 'none'}",
            f"Errors: {', '.join(failed) if failed else 'none'}",
            f"Skipped before contact: "
            f"{', '.join(item['source'] for item in skipped) if skipped else 'none'}",
            "These are collected public-source records, not model inferences.",
        ]
        if result.get("cancelled"):
            summary.append("The run was cancelled; displayed results are partial.")

        cards = [("Research summary", "\n".join(summary))]
        if result.get("type") in {"domain", "ip"}:
            cards.extend([
                ("WHOIS", self._lookup_text(result.get("whois"))),
                ("DNS records", self._lookup_text(result.get("dns"))),
                ("Network owner (ASN)", self._lookup_text(result.get("network"))),
                ("Passive DNS history", self._lookup_text(result.get("passive_dns"))),
            ])
        if result.get("type") == "ip":
            cards.append((
                "Attack reports (DShield)",
                self._lookup_text(result.get("attack_reports")),
            ))
            cards.append((
                "Host exposure (Shodan InternetDB)",
                self._lookup_text(result.get("host_exposure")),
            ))
            if "ip_details" in result:
                cards.append((
                    "IP details (IPinfo)",
                    self._lookup_text(result.get("ip_details")),
                ))
            if "ip_reputation" in result:
                cards.append((
                    "IP reputation (Criminal IP)",
                    self._lookup_text(result.get("ip_reputation")),
                ))
        if result.get("type") == "domain":
            cards.extend([
                ("Certificate transparency",
                 self._lookup_text(result.get("certificates"))),
                ("Web archive", self._lookup_text(result.get("archive"))),
            ])
        elif result.get("type") == "username":
            cards.extend([
                ("URLScan findings", self._lookup_text(result.get("urlscan"))),
                ("GitHub profile", self._lookup_text(result.get("github"))),
                ("Keybase profile and proofs", self._lookup_text(result.get("keybase"))),
            ])
        elif result.get("type") == "email":
            cards.extend([
                ("Email reputation", self._lookup_text(
                    result.get("reputation") or result.get("emailrep")
                )),
                ("Gravatar profile", self._lookup_text(result.get("gravatar"))),
                ("Have I Been Pwned", self._lookup_text(result.get("hibp"))),
                ("BreachDirectory", self._lookup_text(result.get("breachdirectory"))),
            ])
        elif result.get("type") == "company":
            cards.append((
                "Legal entity records",
                self._lookup_text(result.get("legal_entities")),
            ))
            if "sanctions" in result:
                cards.append((
                    "Sanctions and watchlists (OpenSanctions)",
                    self._lookup_text(result.get("sanctions")),
                ))
            if "court_records" in result:
                cards.append((
                    "Court records (CourtListener)",
                    self._lookup_text(result.get("court_records")),
                ))
        elif result.get("type") == "exposure":
            summary_info = result.get("summary", {})
            if summary_info.get("on_ransomware_leak_site"):
                headline = (
                    f"⚠ On a ransomware leak site — "
                    f"{summary_info.get('ransomware_victim_matches', 0)} direct victim "
                    f"match(es). Treat as a likely breach and verify the listing."
                )
            elif summary_info.get("exposure_detected"):
                headline = (
                    "Possible exposure — matches found in dark-web / leak sources. "
                    "Review each hit below; a mention is a lead, not proof."
                )
            else:
                headline = (
                    "No exposure found in the sources that were queried. This is not a "
                    "guarantee of safety — coverage is limited to these indexes."
                )
            cards.insert(1, ("Exposure verdict", headline))
            cards.extend([
                ("Ransomware leak sites",
                 self._lookup_text(result.get("ransomware_live"))),
                ("Dark-web index (Ahmia)", self._lookup_text(result.get("ahmia"))),
                ("Intelligence X", self._lookup_text(result.get("intelx"))),
                ("Breach databases (DeHashed)", self._lookup_text(result.get("dehashed"))),
            ])
        raw = self._lookup_text(result)
        self.sections.show_sections(cards, raw=raw)
        self.sections.setVisible(True)
        self.stream_box.setVisible(False)
        self._last_response = raw
        self._append_activity(
            f"Live Research finished. Sources actually contacted: "
            f"{', '.join(item['source'] for item in contacted) if contacted else 'none'}."
        )
        if skipped:
            self._append_activity(
                "Skipped without contact: "
                + ", ".join(item["source"] for item in skipped) + "."
            )

        recorder = getattr(self.host, "record_external_research", None)
        if save and recorder is not None and (contacted or skipped):
            try:
                recorder(
                    agent="osint",
                    target=result.get("query", ""),
                    query_type={
                        "ip": "IP Address", "domain": "Domain",
                        "username": "Username", "email": "Email",
                        "company": "Company", "exposure": "Exposure",
                    }.get(result.get("type"), "Auto-detect"),
                    response=raw,
                    cancelled=bool(result.get("cancelled")),
                )
            except Exception as error:
                self._append_activity(
                    f"Results are visible, but saving the search failed: {error}"
                )
        self.status_label.setText(
            "Stopped — partial results retained."
            if result.get("cancelled") else "Live Research complete."
        )
        self._set_trace_busy(False)

    def _on_lookup_error(self, error: str) -> None:
        self._append_activity(f"Live Research failed before completion: {error}")
        self.stream_box.setPlainText(f"Live Research error\n\n{error}")
        self.stream_box.setVisible(True)
        self.sections.setVisible(False)
        self.status_label.setText("Live Research error.")
        self._set_trace_busy(False)

    # ── Output ──────────────────────────────────────────────────────────
    def clear(self) -> None:
        self._clear_output()
        self.target_input.clear()
        self._reset_activity()
        self.status_label.setText("Idle")
        self._last_response = ""

    def _reset_activity(self) -> None:
        self._activity_entries = [
            "Ready. Trace will show each processing stage here and will explicitly "
            "state whether any external source was queried."
        ]
        self._render_activity()

    def _append_activity(self, message: str) -> None:
        self._activity_entries.append(message)
        self._render_activity()

    def _render_activity(self) -> None:
        if not hasattr(self, "activity_box"):
            return
        lines = [
            f"{'✓' if index < len(self._activity_entries) - 1 else '•'} {message}"
            for index, message in enumerate(self._activity_entries)
        ]
        self.activity_box.setPlainText("\n".join(lines))
        self.activity_box.moveCursor(QTextCursor.End)

    def _clear_output(self) -> None:
        self.sections.clear()
        self.stream_box.clear()
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)

    def _populate_sections(self, text: str) -> None:
        sections = self.parse_sections(text)
        self.sections.show_sections(
            [
                ("Query structure", sections.get("structure", "")),
                ("Google dorks", sections.get("dorks", ""), True),
                ("Public sources", sections.get("sources", "")),
                ("Summary and next steps", sections.get("summary", "")),
            ],
            raw=text,
        )

    @staticmethod
    def parse_sections(text: str) -> dict:
        """Split the answer on its four `## HEADING`s. Missing ones come back empty."""
        patterns = {
            "structure": r"##\s*QUERY STRUCTURE(.*?)(?=##\s*GOOGLE DORKS|$)",
            "dorks":     r"##\s*GOOGLE DORKS(.*?)(?=##\s*PUBLIC SOURCES|$)",
            "sources":   r"##\s*PUBLIC SOURCES(.*?)(?=##\s*SUMMARY|$)",
            "summary":   r"##\s*SUMMARY.*?(.*?)$",
        }
        result = {}
        for key, pat in patterns.items():
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            result[key] = m.group(1).strip() if m else ""
        return result

"""Forge — describe an agent in prose, review the generated spec, create it.

Second vertical moved out of `main.py` (phase 4, `docs/refactor_plan.md`).

Two things differ from the other panels and are deliberate:

- Forge owns its `ManagerAgent` rather than taking it from
  `host.agent_instances`. It was never registered there, and registering it now
  would put it in the chat panel's `build_messages` path, which is a behaviour
  change and not part of a move.
- Writing the agent files is the host's job (`agent_factory`): it touches
  `agents/` and `config/` for the whole application, not for this panel.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QMessageBox, QPushButton, QTextBrowser, QTextEdit,
    QVBoxLayout,
)

from agents.manager_agent import ManagerAgent
from ui.panels.base import AgentPanel
from ui.widgets import SectionView


class ManagerPanel(AgentPanel):
    """Turn an idea into a reviewed agent spec, then write the agent."""

    agent_key = "manager"
    default_provider = "deepseek"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.setObjectName("ManagerPanel")
        self.manager_agent = ManagerAgent()
        self.pending_spec: dict | None = None
        self._last_response = ""
        self._build()
        self.polish_workspace()
        self.hide()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Idea input ───────────────────────────────────────────────────
        idea_group = QGroupBox("Describe Your Agent Idea")
        idea_group.setObjectName("ManagerIdeaBox")
        idea_layout = QVBoxLayout(idea_group)

        self.idea_input = QTextEdit()
        self.idea_input.setPlaceholderText(
            "Example: A cybersecurity agent that helps analyse logs, detect anomalies, "
            "and suggest mitigations. Should prefer local models for privacy."
        )
        self.idea_input.setMinimumHeight(100)
        self.idea_input.setMaximumHeight(160)
        idea_layout.addWidget(self.idea_input)

        self.analyze_btn = QPushButton("Analyze Idea")
        self.analyze_btn.setMinimumWidth(140)
        self.analyze_btn.setObjectName("PrimaryAction")
        self.analyze_btn.clicked.connect(self.analyze_idea)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)

        idea_run_bar = self.build_run_bar(
            self.analyze_btn,
            secondary=(self.clear_btn,),
            context="Agent Builder",
            empty_placeholder=True,
        )
        idea_layout.addWidget(idea_run_bar)
        layout.addWidget(idea_group)

        # ── Generated spec ───────────────────────────────────────────────
        spec_group = QGroupBox("Generated Spec (review before approving)")
        spec_group.setObjectName("ManagerSpecBox")
        spec_layout = QVBoxLayout(spec_group)

        self.stream_box = QTextBrowser()
        self.stream_box.setOpenExternalLinks(False)
        self.stream_box.setVisible(False)
        spec_layout.addWidget(self.stream_box, 1)

        self.sections = SectionView()
        self.sections.setMinimumHeight(180)
        spec_layout.addWidget(self.sections, 1)

        approve_row = QHBoxLayout()

        self.approve_btn = QPushButton("Approve && Create Agent")
        self.approve_btn.setEnabled(False)
        self.approve_btn.setMinimumWidth(200)
        self.approve_btn.setObjectName("PrimaryAction")
        self.approve_btn.clicked.connect(self.approve_spec)
        approve_row.addWidget(self.approve_btn)

        self.reject_btn = QPushButton("Reject / Clear Spec")
        self.reject_btn.setEnabled(False)
        self.reject_btn.clicked.connect(self.reject_spec)
        approve_row.addWidget(self.reject_btn)

        approve_row.addStretch()
        spec_layout.addLayout(approve_row)
        layout.addWidget(spec_group)

        # ── Creation log ─────────────────────────────────────────────────
        log_group = QGroupBox("Creation Log")
        log_group.setObjectName("ManagerLogBox")
        log_layout = QVBoxLayout(log_group)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(100)
        self.log.setPlaceholderText("Agent creation and validation events will appear here.")
        self.log.setStyleSheet("font-family: monospace; font-size: 12px;")
        log_layout.addWidget(self.log)
        layout.addWidget(log_group)

    # ── Running ─────────────────────────────────────────────────────────
    def analyze_idea(self) -> None:
        idea = self.idea_input.toPlainText().strip()
        if not idea:
            QMessageBox.warning(self, "No Idea", "Please describe your agent idea first.")
            return

        if self.is_running():
            QMessageBox.information(self, "Busy", "Analysis already running.")
            return

        messages = self.manager_agent.build_messages(idea)

        self._last_response = ""
        self.sections.clear()
        self.sections.setVisible(False)
        self.stream_box.setPlainText("Analyzing…")
        self.stream_box.setVisible(True)
        self.analyze_btn.setEnabled(False)
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)
        self.pending_spec = None

        if not self.authorize(idea):
            # Analyze was disabled above; a blocked request has to put it back
            # or the panel is stuck with a dead button and "Analyzing..." on
            # screen for a request that was never sent.
            self.stream_box.setPlainText("[Blocked] The request was not sent.")
            self.analyze_btn.setEnabled(True)
            return

        self.start_worker(
            messages, idea,
            on_token=self._on_token,
            on_finished=self._on_finished,
            on_error=self._on_error,
        )

    def _on_token(self, token: str) -> None:
        self._last_response += token
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(self._last_response)

    def _on_finished(self, response: str) -> None:
        self.record(response)
        self.analyze_btn.setEnabled(True)
        self._last_response = response
        spec = self.manager_agent.parse_spec(response)
        if spec is None:
            self.stream_box.setVisible(False)
            self.sections.setVisible(True)
            self.sections.show_sections(
                [("Could not structure the response", response)], raw=response
            )
            return

        self.pending_spec = spec
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)
        self._populate_sections(spec, response)
        # Validate now, not after the reviewer has already said Approve and
        # confirmed: a spec the factory will refuse is shown as such, and
        # Approve stays disabled for it.
        factory = getattr(self.host, "agent_factory", None)
        valid, reason = (factory.validate_spec(spec) if factory is not None else (True, "OK"))
        self.reject_btn.setEnabled(True)
        if valid:
            self.approve_btn.setEnabled(True)
            self.log.append("[Ready] Spec generated. Review and approve or reject.")
        else:
            self.approve_btn.setEnabled(False)
            self.log.append(f"[Invalid] This spec cannot be created as written: {reason}")
            self.sections.show_sections(
                [("Cannot be created as written", reason, False)] + self._spec_sections(spec),
                raw=response)

    def _on_error(self, error: str) -> None:
        self.abandon(error=error)
        self.analyze_btn.setEnabled(True)
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(f"[Error]\n{error}")
        self.log.append(f"[Error] {error}")

    def _spec_sections(self, spec: dict) -> list:
        """The review cards for a spec, without rendering them."""
        captured = {}
        original = self.sections.show_sections
        try:
            self.sections.show_sections = lambda sections, raw="": captured.setdefault("s", sections)
            self._populate_sections(spec, "")
        finally:
            self.sections.show_sections = original
        return list(captured.get("s", []))

    def _populate_sections(self, spec: dict, raw: str) -> None:
        providers = ", ".join(spec.get("allowed_providers", [])) or "None"
        tools = ", ".join(spec.get("allowed_tools", [])) or "None"
        budget = spec.get("budget_limit_eur")
        budget_text = "No agent-specific cap" if budget is None else f"€{budget}"
        approval = "Required" if spec.get("requires_approval") else "Not required"
        overview = "\n".join(filter(None, [
            f"Name: {spec.get('name', '—')}",
            f"Label: {spec.get('label', '—')}",
            spec.get("description", ""),
        ]))
        access = (
            f"Providers: {providers}\n"
            f"Tools: {tools}\n"
            f"Budget: {budget_text}\n"
            f"Approval: {approval}"
        )
        self.sections.show_sections(
            [
                ("Agent overview", overview),
                ("Access and safeguards", access),
                ("System instructions", spec.get("system_prompt", "")),
                ("Design reasoning", spec.get("reasoning", "")),
            ],
            raw=raw,
        )

    def stop(self) -> None:
        """The window's Stop button. Forge has no Stop of its own.

        Before the move it was not reachable from `stop_current_task` at all, so
        an analysis could only be waited out; leaving Analyze disabled after a
        cancel would strand the panel.
        """
        if self.stop_worker():
            self.log.append("[Stopped] Analysis cancelled.")
        self.analyze_btn.setEnabled(True)

    # ── The spec ────────────────────────────────────────────────────────
    def approve_spec(self) -> None:
        if not self.pending_spec:
            return

        name = self.pending_spec.get("name", "unknown")
        label = self.pending_spec.get("label", name)

        confirm = QMessageBox.question(
            self,
            "Confirm Agent Creation",
            f"Create agent '{label}' ({name})?\n\n"
            f"This will:\n"
            f"  • Write a scaffold file agents/{name}_agent.py\n"
            f"  • Add DISABLED rows to the SQLite agent and tool registries\n\n"
            f"The scaffold is a starting point, not a live agent: nothing loads or "
            f"runs it, and it will not appear in the sidebar. A developer must wire "
            f"it in by hand. Its registry rows stay disabled, and Chat's tool list "
            f"is fixed, so the generated prompt is not usable from Chat until that "
            f"wiring is done.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm != QMessageBox.Yes:
            return

        report = self.host.agent_factory.create_agent(self.pending_spec)

        if report["success"]:
            self.log.append(f"\n[Created] Agent '{name}' created successfully.")
            for f in report["files_created"]:
                self.log.append(f"  ✓ {f}")
            self.log.append(
                "\n[Info] Scaffold written and registered as DISABLED. It will not "
                "appear in the sidebar or run until a developer wires it in; Chat's "
                "tool list is fixed, so the prompt is not usable from Chat yet.")
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.pending_spec = None
            QMessageBox.information(
                self,
                "Scaffold Created",
                f"Scaffold for '{label}' written and registered (disabled).\n\n"
                f"This is a starting point, not a runnable agent: nothing loads it "
                f"and it will not appear in the sidebar. Its registry rows are "
                f"disabled until a developer wires the agent in.",
            )
        else:
            errors = "\n".join(report["errors"])
            self.log.append(f"\n[Failed] Could not create agent:\n{errors}")
            QMessageBox.warning(self, "Creation Failed", errors)

    def reject_spec(self) -> None:
        self.pending_spec = None
        self._clear_spec()
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)
        self.log.append("[Rejected] Spec cleared. You can describe a new idea.")

    def clear(self) -> None:
        self.idea_input.clear()
        self._clear_spec()
        self.pending_spec = None
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)
        self.log.append("[Cleared]")

    def _clear_spec(self) -> None:
        self._last_response = ""
        self.stream_box.clear()
        self.stream_box.setVisible(False)
        self.sections.clear()
        self.sections.setVisible(True)

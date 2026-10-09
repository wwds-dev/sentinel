"""What every agent panel shares: the provider/model row and the host wrappers.

Phase 3 of the split (`docs/refactor_plan.md`). Two things live here:

`build_provider_row` is a plain function, because the panels are still methods
on `GodAI` and cannot inherit anything yet. Every panel rebuilt the same
provider combo, model combo and reload wiring by hand — seven identical
`addItems` lists and six near-identical `*_load_models` bodies. They call this
instead, and keep owning the widgets it returns.

`AgentPanel` is the class those panels become in phase 4. It is composition,
not a mixin: a panel holds its host behind the `AgentHost` protocol rather than
sharing a namespace with it, which is what makes a panel constructible — and
therefore testable — without a whole `GodAI` window. It uses the same function,
so the two paths cannot drift.
"""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from ui.widgets import FlowLayout, MenuComboBox, ProgressiveSection, WorkspaceState
from ui.workers import ChatWorker
from services.provider_catalog import SUPPORTED_PROVIDERS

# The provider list every panel offered, written once. Order is the order shown.
PROVIDERS = SUPPORTED_PROVIDERS

# Model combos are wide enough for a dated API model id ("claude-sonnet-4-6-20260112").
PROVIDER_BOX_WIDTH = 120
MODEL_BOX_WIDTH = 220


def configure_model_controls(provider_box: QComboBox, model_box: QComboBox) -> None:
    """Give every agent view readable names and responsive sizing."""
    provider_box.setMinimumWidth(PROVIDER_BOX_WIDTH)
    model_box.setMinimumWidth(MODEL_BOX_WIDTH)
    provider_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    model_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    provider_box.setMinimumContentsLength(11)
    model_box.setMinimumContentsLength(24)
    provider_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    model_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    if isinstance(provider_box, MenuComboBox):
        provider_box.setMenuMode(MenuComboBox.PROVIDER)
    if isinstance(model_box, MenuComboBox):
        model_box.setMenuMode(MenuComboBox.MODEL)


def flow_row(parent: QWidget | None = None,
             spacing: int = 6, min_height: int = 44) -> tuple[QWidget, FlowLayout]:
    """A control row that wraps instead of pinning a minimum width on the pane.

    Returns the container and the layout to fill; the caller must keep the
    container — every widget added to the layout belongs to it, and dropping an
    unparented one takes the combos down with it ("Internal C++ object already
    deleted"), which no import or compile check can see. Passing a `parent`
    settles that at construction.
    """
    container = QWidget(parent)
    container.setMinimumHeight(min_height)
    container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    return container, FlowLayout(container, spacing=spacing)


def build_provider_row(
    host,
    layout,
    agent_key: str,
    *,
    default: str = "anthropic",
    labels: bool = True,
    model_width: int = MODEL_BOX_WIDTH,
    empty_placeholder: bool = False,
    separator: bool = False,
) -> tuple[QComboBox, QComboBox]:
    """Add "Provider: […] Model: […]" to `layout` and keep the two in step.

    The model box is filled from the selected provider immediately and again on
    every provider change, and the loader is registered with the host so the
    recommendation system can repopulate the box after it selects a provider
    programmatically.

    `labels` is off for the panels that never had them (Beacon, Tunnel);
    `empty_placeholder` is on for Forge, which shows "(no local models)" rather
    than an empty box. Both exist to keep this a refactor rather than a redesign.
    """
    provider_box = MenuComboBox()
    provider_box.addItems(PROVIDERS)
    provider_box.setCurrentText(default)
    if labels:
        layout.addWidget(QLabel("Provider:"))
    layout.addWidget(provider_box)

    model_box = MenuComboBox()
    model_box.setMinimumWidth(model_width)
    configure_model_controls(provider_box, model_box)
    if labels:
        layout.addWidget(QLabel("Model:"))
    elif separator:
        dot = QLabel("·")
        dot.setObjectName("RunBarDot")
        layout.addWidget(dot)
    layout.addWidget(model_box)

    def load() -> None:
        host.load_models_into(
            provider_box, model_box, agent_key,
            empty_placeholder=empty_placeholder,
        )

    # `_t` is the new provider text, which `load` reads off the box itself.
    provider_box.currentTextChanged.connect(lambda _t: load())
    host.register_model_loader(agent_key, load)
    load()
    return provider_box, model_box


class AgentPanel(QWidget):
    """One agent's UI, talking to the application through `AgentHost`.

    Subclasses set `agent_key` and build their own widgets; everything they need
    from the application arrives through `self.host` and is wrapped below, so a
    panel never reaches into `GodAI` attributes that are not in the protocol.

    Nothing here is Qt-specific beyond the widget base — the wrappers are thin
    on purpose, since their value is the boundary they draw, not the code they
    save.
    """

    #: Key under `AGENT_SETUP_WIDGETS`, `AGENT_RECOMMENDATIONS`, the registry
    #: and the request guard. Every subclass sets it.
    agent_key: str = ""

    #: Pre-selected provider. Panels that cost money default to a cloud model;
    #: the recommendation system may override this at startup.
    default_provider: str = "anthropic"

    def __init__(self, host, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.provider_box: QComboBox | None = None
        self.model_box: QComboBox | None = None
        self.worker = None
        self._request_id: str | None = None

    # ── Construction helpers ────────────────────────────────────────────
    def flow_row(self, spacing: int = 6, min_height: int = 44) -> tuple[QWidget, FlowLayout]:
        """A wrapping control row owned by this panel."""
        return flow_row(self, spacing, min_height)

    def build_provider_row(self, layout, **kwargs) -> tuple[QComboBox, QComboBox]:
        kwargs.setdefault("default", self.default_provider)
        self.provider_box, self.model_box = build_provider_row(
            self.host, layout, self.agent_key, **kwargs
        )
        return self.provider_box, self.model_box

    def build_run_bar(
        self,
        primary: QPushButton,
        *,
        stop: QPushButton | None = None,
        secondary: tuple[QPushButton, ...] = (),
        context: str = "",
        empty_placeholder: bool = False,
    ) -> QWidget:
        """Build the same compact provider/model/action surface used by Chat.

        Specialist panels keep their task-specific forms and results, but the
        decision immediately before execution should always read the same way:
        workflow, provider, model, optional utility, primary action. Centralising
        this also prevents provider widths and action order drifting per agent.
        """
        container = QWidget(self)
        container.setObjectName("RunBar")
        container.setAttribute(Qt.WA_StyledBackground, True)
        # A wrapping row instead of a QHBoxLayout: a fixed row of controls +
        # actions reports the sum of their widths as its minimum, which pins a
        # hard minimum width on the whole workspace (Beacon, with the most
        # buttons, hit ~880px and had to be scrolled horizontally). FlowLayout
        # lets the bar reflow onto a second line so the pane shrinks to the
        # widest single control. The trade is right-alignment of the primary
        # action, which a wrapping layout cannot express.
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        container.setSizePolicy(policy)

        row = FlowLayout(container, spacing=8)
        row.setContentsMargins(8, 8, 8, 8)

        if context:
            chip = QLabel(context)
            chip.setObjectName("WorkflowChip")
            row.addWidget(chip)

        self.build_provider_row(
            row,
            labels=False,
            separator=True,
            empty_placeholder=empty_placeholder,
        )
        self.provider_box.setObjectName("MachinePick")
        self.model_box.setObjectName("MachinePick")

        self.auto_route_btn = QPushButton("Auto-route")
        self.auto_route_btn.setObjectName("AutoRouteAction")
        self.auto_route_btn.setToolTip(
            "Choose the best available provider and model for this agent's current input"
        )
        self.auto_route_btn.clicked.connect(
            lambda _checked=False: self.auto_route(context)
        )
        row.addWidget(self.auto_route_btn)

        for button in secondary:
            row.addWidget(button)
        if stop is not None:
            row.addWidget(stop)
        row.addWidget(primary)
        return container

    def routing_text(self, context: str = "") -> str:
        """Collect only editable request fields for an explicit route decision."""
        parts = [self.agent_key, context]
        parts.extend(
            field.text().strip() for field in self.findChildren(QLineEdit)
            if field.isEnabled() and field.text().strip()
        )
        parts.extend(
            field.toPlainText().strip() for field in self.findChildren(QTextEdit)
            if not field.isReadOnly() and field.isEnabled() and field.toPlainText().strip()
        )
        return "\n".join(part for part in parts if part)

    def auto_route(self, context: str = ""):
        """Apply the shared router to this panel without starting a request."""
        prepare = getattr(self.host, "prepare_agent_route", None)
        if prepare is None:
            return None
        result = prepare(
            self.agent_key,
            self.routing_text(context),
            self.provider_box,
            self.model_box,
            tool=context,
            force=True,
        )
        if result not in (None, False):
            status = getattr(self, "status_label", None)
            if status is not None:
                status.setText(
                    f"Auto-routed to {self.provider} · {self.model}."
                )
        return result

    def build_model_override(self, *, expanded: bool = False,
                             empty_placeholder: bool = False) -> ProgressiveSection:
        """Recommended model by default; full provider controls on demand."""
        section = ProgressiveSection(
            "Model override", "Recommended model selected", expanded=expanded,
            parent=self,
        )
        row_widget, row = self.flow_row(min_height=82)
        self.build_provider_row(
            row, empty_placeholder=empty_placeholder,
        )
        section.addWidget(row_widget)

        def update_summary(*_args):
            provider = self.provider_box.currentText() if self.provider_box else ""
            model = self.model_box.currentText() if self.model_box else ""
            section.setSummary(f"Using {provider} · {model}".strip(" ·"))

        self.provider_box.currentTextChanged.connect(update_summary)
        self.model_box.currentTextChanged.connect(update_summary)
        update_summary()
        self.model_override = section
        return section

    def build_state_label(self) -> WorkspaceState:
        self.workspace_state = WorkspaceState(self)
        return self.workspace_state

    def set_workspace_state(self, state: str, message: str) -> None:
        if hasattr(self, "workspace_state"):
            self.workspace_state.setState(state, message)

    def register_results(self, widget: QWidget) -> QWidget:
        """Register the stable results region for an agent workspace.

        The region deliberately remains visible when empty. A persistent output
        destination makes the input → action → result flow legible and prevents
        large screens from turning the workspace into scattered controls.
        """
        self.results_widget = widget
        widget.setProperty("workspaceResultSurface", True)
        widget.show()
        return widget

    def show_results(self) -> None:
        if hasattr(self, "results_widget"):
            self.results_widget.show()

    def hide_results(self) -> None:
        """Return to the empty-results state without collapsing the workspace."""
        if hasattr(self, "results_widget"):
            self.results_widget.show()

    @staticmethod
    def set_busy(primary: QPushButton, stop: QPushButton, busy: bool) -> None:
        """One action per state: Run while idle, Stop while running."""
        primary.setVisible(not busy)
        primary.setEnabled(not busy)
        stop.setVisible(busy)
        stop.setEnabled(busy)

    def polish_workspace(self) -> None:
        """Apply the shared specialist-workspace visual hierarchy."""
        self.setProperty("agentWorkspace", True)
        for group in self.findChildren(QGroupBox):
            if group.parentWidget() is self:
                group.setProperty("workspaceCard", True)
                # A setup card should keep its content height. Without this,
                # QVBoxLayout gives an empty workspace's spare height to the
                # card and turns three fields into a page-sized blank panel.
                group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
                if isinstance(group.layout(), QGridLayout):
                    group.layout().setHorizontalSpacing(10)
                    group.layout().setVerticalSpacing(8)
        for tabs in self.findChildren(QTabWidget):
            tabs.setProperty("workspaceResults", True)
        outputs = self.findChildren(QTextBrowser) + self.findChildren(QTextEdit)
        for output in dict.fromkeys(outputs):
            if output.isReadOnly():
                output.setProperty("workspaceOutput", True)
        for button in self.findChildren(QPushButton):
            if button.objectName() == "PrimaryAction":
                button.setProperty("workspacePrimary", True)
        for widget in [self] + self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    # ── Current selection ───────────────────────────────────────────────
    @property
    def provider(self) -> str:
        return self.provider_box.currentText() if self.provider_box else ""

    @property
    def model(self) -> str:
        return self.model_box.currentText() if self.model_box else ""

    def load_models(self) -> None:
        """Refill the model box from the selected provider."""
        if self.provider_box is not None and self.model_box is not None:
            self.host.load_models_into(self.provider_box, self.model_box,
                                       self.agent_key)

    # ── The request guard ───────────────────────────────────────────────
    # A panel that spends money calls authorize() first and record() once the
    # response is in; abandon() on failure. Skipping them is how twelve of
    # thirteen agents came to spend outside the budget caps (TODO #1).

    def authorize(self, prompt: str, *, tool: str | None = None,
                  label: str | None = None) -> bool:
        """False means the request was blocked and must not be sent."""
        prepare_route = getattr(self.host, "prepare_agent_route", None)
        if prepare_route is not None:
            route_result = prepare_route(
                self.agent_key, prompt, self.provider_box, self.model_box,
                tool=tool or label or "",
            )
            if route_result is False:
                return False
        request_id = uuid4().hex
        allowed = self.host.authorize_request(
            self.agent_key, self.provider, self.model, prompt,
            tool=tool, label=label, request_id=request_id,
        )
        self._request_id = request_id if allowed else None
        return allowed

    def reauthorize(self, messages: list) -> bool:
        """Check the budget caps again with the messages about to be sent.

        Call it when the prompt was assembled after authorize() — otherwise
        the caps only ever saw the target string. False means the request was
        blocked and has been abandoned; the panel must not send it.
        """
        recheck = getattr(self.host, "recheck_budget", None)
        if recheck is None:
            return True
        text = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        if recheck(self.agent_key, self.provider, self.model, text,
                   request_id=self._request_id):
            return True
        self.abandon("blocked")
        return False

    def record(self, response: str, messages: list | None = None) -> None:
        request_id = self._request_id
        self.host.record_request(
            self.agent_key, response, messages, request_id=request_id,
        )
        self._request_id = None

    def abandon(self, reason: str = "error") -> None:
        request_id = self._request_id
        self.host.abandon_request(
            self.agent_key, reason, request_id=request_id,
        )
        self._request_id = None

    def note_usage(self, usage: dict) -> None:
        self.host.note_request_usage(
            self.agent_key, usage, request_id=self._request_id,
        )

    # ── Running one request ─────────────────────────────────────────────
    #: Swapped by panels that drive a tool instead of a chat request, and by
    #: tests that would rather not start a thread.
    worker_class = ChatWorker

    def start_worker(self, messages: list, prompt: str, *,
                     on_token=None, on_finished=None, on_error=None):
        """Run one authorised request in a thread and keep a handle on it.

        The usage signal is connected here rather than by each caller: real
        token counts arriving from the provider are what turn an estimate into
        a billed amount, and a panel that forgot to connect it under-reported
        its own spending in silence.
        """
        worker = self.worker_class(self.host.run_backend, self.provider,
                                   self.model, messages, prompt)
        if on_token is not None:
            worker.token_signal.connect(on_token)
        if on_finished is not None:
            worker.finished_signal.connect(on_finished)
        if on_error is not None:
            worker.error_signal.connect(on_error)
        worker.usage_signal.connect(self.note_usage)
        self.worker = worker
        worker.start()
        return worker

    def is_running(self) -> bool:
        """Whether this panel currently has a request in flight."""
        return self.worker is not None and self.worker.isRunning()

    def stop_worker(self) -> bool:
        """Cancel the in-flight model request, if there is one. True if it was running.

        Checks the model worker directly rather than is_running(): panels that
        own other workers (a file search, a Wi-Fi scan) widen is_running() to
        cover them, and this method must only ever touch self.worker.
        """
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            return True
        return False

    def stop(self) -> None:
        """Stop this panel's work and put its controls back.

        The base only cancels; a panel that has a status label and buttons to
        restore overrides this. `stop_current_task` — the window's Stop button —
        calls it on whichever panel reports itself running.
        """
        self.stop_worker()

    # ── Shared services ─────────────────────────────────────────────────
    def agent(self):
        """This panel's backend agent instance."""
        return self.host.agent_instances[self.agent_key]

    def run_backend(self, messages: list, prompt: str):
        """Execute one request with the panel's current provider and model."""
        return self.host.run_backend(self.provider, self.model, messages, prompt)

    def note_failure(self, context: str, exc: Exception, widget=None) -> None:
        self.host._note_failure(f"{self.agent_key}: {context}", exc, widget)

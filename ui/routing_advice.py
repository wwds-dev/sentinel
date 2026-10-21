"""The advice banner (mockup C), its failover form, and the status line (C′).

Byte-identical in Sentinel and Imprint (tests/test_providers_drift.py). A pure
view: it is handed an `Advice` or a `FailoverOffer` from
services/routing_advisor.py and emits which button was pressed. It never runs,
switches or sends anything itself; the app does, through its own request
guard, so a paid choice still meets the app's own confirmation.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from services import routing_advisor as ra

_STYLE = """
QFrame#routingAdvice { border: 1px solid rgba(232, 168, 56, 0.55);
    border-radius: 8px; background: rgba(232, 168, 56, 0.08); }
QFrame#routingAdvice[urge="false"] { border-color: rgba(140, 160, 190, 0.45);
    background: rgba(140, 160, 190, 0.06); }
QLabel#routingAdviceTitle { font-weight: 600; background: transparent; }
QLabel#routingAdviceDetail { background: transparent; }
"""


class AdviceBanner(QFrame):
    """Holds one job until the user answers. Hidden when there is nothing."""

    switch_requested = Signal()      # Switch for this job / Send to <provider>
    always_requested = Signal()      # Always for this kind of job
    keep_requested = Signal()        # Keep local / Keep <model> / Try smaller
    dismissed = Signal()             # the job is dropped

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("routingAdvice")
        self.setStyleSheet(_STYLE)
        self.advice: ra.Advice | None = None
        self.offer: ra.FailoverOffer | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        self.title = QLabel(self)
        self.title.setObjectName("routingAdviceTitle")
        self.title.setWordWrap(True)
        self.detail = QLabel(self)
        self.detail.setObjectName("routingAdviceDetail")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.title)
        outer.addWidget(self.detail)
        row = QHBoxLayout()
        self.switch_button = QPushButton(self)
        self.always_button = QPushButton(self)
        self.keep_button = QPushButton(self)
        self.dismiss_button = QPushButton("Cancel", self)
        for button in (self.switch_button, self.always_button, self.keep_button):
            row.addWidget(button)
        row.addStretch()
        row.addWidget(self.dismiss_button)
        outer.addLayout(row)
        self.switch_button.clicked.connect(self._switch)
        self.always_button.clicked.connect(self._always)
        self.keep_button.clicked.connect(self._keep)
        self.dismiss_button.clicked.connect(self._dismiss)
        self.hide()

    # ── Showing ─────────────────────────────────────────────────────────────
    def show_advice(self, advice: ra.Advice, job: ra.Job, selected: ra.Candidate) -> None:
        self.advice, self.offer = advice, None
        self.setProperty("urge", "true" if advice.urge or advice.action == ra.REFUSE else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.title.setText(ra.headline(advice, selected))
        self.detail.setText(ra.detail(advice, job) if advice.action != ra.REFUSE
                            else " ".join(r.text for r in advice.reasons))
        labels = ra.button_labels(advice, job, selected)
        has = advice.suggestion is not None
        self._set(self.switch_button, labels["switch"], has)
        self._set(self.always_button, labels["always"], has)
        self._set(self.keep_button, labels["keep"], bool(labels["keep"]) and has)
        self.dismiss_button.setText("Cancel" if has else "OK")
        self.show()

    def show_failover(self, offer: ra.FailoverOffer) -> None:
        self.advice, self.offer = None, offer
        self.setProperty("urge", "true")
        self.style().unpolish(self)
        self.style().polish(self)
        self.title.setText(f"{offer.failed.label} could not finish this job")
        self.detail.setText(ra.failover_text(offer))
        cloud, local = offer.cloud, offer.local
        self._set(self.switch_button,
                  f"Send to {cloud.provider.capitalize()}" if cloud else "", cloud is not None)
        self._set(self.always_button, "", False)
        self._set(self.keep_button,
                  f"Try {local.label}" if local else "", local is not None)
        self.dismiss_button.setText("Cancel")
        self.show()

    def clear(self) -> None:
        self.advice = self.offer = None
        self.hide()

    @staticmethod
    def _set(button: QPushButton, text: str, visible: bool) -> None:
        button.setText(text)
        button.setVisible(bool(visible and text))

    # ── Answers ─────────────────────────────────────────────────────────────
    def _switch(self) -> None:
        self.hide()
        self.switch_requested.emit()

    def _always(self) -> None:
        self.hide()
        self.always_requested.emit()

    def _keep(self) -> None:
        self.hide()
        self.keep_requested.emit()

    def _dismiss(self) -> None:
        self.clear()
        self.dismissed.emit()

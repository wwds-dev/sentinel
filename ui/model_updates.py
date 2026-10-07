"""The Model Updates card in the left rail, and the dialog that reviews them.

The card reports; the dialog decides. A scan never changes a recommendation
by itself — it only puts the new model in front of the user with an
assessment, and Adopt is the one action that moves a BEST FIT. See
services/model_watch.py for the rules behind the assessment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from services.model_watch import Assessment, ModelWatch, NewModel, assess
from ui.widgets import KeyValue

# Names the card's tooltip lists before it folds the rest into "…and N more".
CARD_ROWS = 3


def describe_scan_time(stamp: str, now: datetime | None = None) -> str:
    """"today 14:02", "yesterday", "3 Oct" or "never"."""
    if not stamp:
        return "never"
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return "unknown"
    now = now or datetime.now()
    days = (now.date() - when.date()).days
    if days == 0:
        return f"today {when:%H:%M}"
    if days == 1:
        return "yesterday"
    return f"{when.day} {when:%b}"


def describe_notes(notes: Mapping[str, str]) -> str:
    """One line per provider: what the last scan got from it."""
    if not notes:
        return "No scan has run yet."
    lines = []
    for provider, note in sorted(notes.items()):
        if note == "ok":
            lines.append(f"{provider}: listed")
        elif note.startswith("skipped"):
            lines.append(f"{provider}: not checked — no API key")
        else:
            lines.append(f"{provider}: {note}")
    return "\n".join(lines)


class ModelUpdatesCard(QGroupBox):
    """Last check, how many new models, Review and Check now.

    Three lines, not a list: this card shares the left rail with the agent
    list, and every row here is a row of agents scrolled out of view. The
    names, with what each would change, are one click away in the dialog.
    """

    check_requested = Signal()
    review_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("MODEL UPDATES", parent)
        self.setObjectName("RightCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(4)

        self.last_row = KeyValue("Last check", "never")
        self.new_row = KeyValue("New models", "—")
        layout.addWidget(self.last_row)
        layout.addWidget(self.new_row)

        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        links.setSpacing(12)
        self.review_btn = QPushButton("Review")
        self.review_btn.setObjectName("RailLink")
        self.review_btn.setCursor(Qt.PointingHandCursor)
        self.review_btn.clicked.connect(self.review_requested.emit)
        self.check_btn = QPushButton("Check now")
        self.check_btn.setObjectName("RailLink")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setToolTip(
            "Ask each provider with an API key which models it serves now. "
            "Listing models is free and sends no prompt. Also runs at every start."
        )
        self.check_btn.clicked.connect(self.check_requested.emit)
        links.addWidget(self.review_btn)
        links.addWidget(self.check_btn)
        links.addStretch()
        layout.addLayout(links)

    def set_checking(self, checking: bool) -> None:
        self.check_btn.setEnabled(not checking)
        self.check_btn.setText("Checking…" if checking else "Check now")

    def show_state(self, last_scan: str, notes: Mapping[str, str],
                   pending: list[NewModel]) -> None:
        checked = sum(1 for note in notes.values() if note == "ok")
        self.last_row.set(
            describe_scan_time(last_scan),
            f"{describe_notes(notes)}\n\n{checked} of {len(notes)} providers "
            "checked. A provider without a key cannot be asked." if notes else
            "No scan has run yet.",
        )
        count = len(pending)
        names = "\n".join(f"{n.provider} · {n.model}" for n in pending[:CARD_ROWS])
        if count > CARD_ROWS:
            names += f"\n…and {count - CARD_ROWS} more"
        self.new_row.set(str(count) if last_scan else "—",
                         names if count else "Nothing new since the last check.")
        self.new_row.value.setObjectName("KVValueOn" if count else "KVValueOff")
        self.new_row.value.style().unpolish(self.new_row.value)
        self.new_row.value.style().polish(self.new_row.value)
        self.review_btn.setVisible(count > 0)
        self.review_btn.setText(f"Review {count}" if count else "Review")
        self.review_btn.setToolTip(
            f"{names}\n\nSee whether each should be the best fit, then adopt or dismiss."
            if count else "")


def assessment_lines(assessment: Assessment, labels: Mapping[str, str]) -> list[str]:
    """What adopting would change, in plain words.

    Agents losing the same pick for the same reason share one line: five
    copies of "moves from claude-sonnet-5" read as five different findings.
    """
    lines = []
    grouped: dict[tuple[str, str], list[str]] = {}
    for move in assessment.agent_moves:
        grouped.setdefault((move.current, move.reason), []).append(
            labels.get(move.key, move.key))
    for (current, reason), agents in grouped.items():
        lines.append(f"BEST FIT for {', '.join(agents)} moves here from {current} "
                     f"({reason}).")
    for move in assessment.moves:
        if move.scope == "chat":
            lines.append(f"Chat · {move.key}: routes here instead of {move.current}.")
    return lines


class ModelReviewDialog(QDialog):
    """Every pending new model with its assessment and Adopt / Dismiss."""

    def __init__(self, watch: ModelWatch, labels: Mapping[str, str],
                 on_decided: Callable[[str, Assessment], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("New models")
        self.resize(640, 520)
        self._watch = watch
        self._labels = labels
        self._on_decided = on_decided

        outer = QVBoxLayout(self)
        intro = QLabel(
            "These models appeared in a provider's live list since Sentinel last "
            "looked. They are already selectable in that provider's model "
            "dropdown. Adopting one lets the router rank it and moves BEST FIT "
            "where it is a newer release of the current pick. Nothing changes "
            "until you adopt."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._rows = QVBoxLayout(body)
        self._rows.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        pending = watch.pending()
        if not pending:
            self._rows.addWidget(QLabel("Nothing new since the last check."))
        for item in pending:
            self._rows.addWidget(self._row(item))
        self._rows.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _row(self, item: NewModel) -> QFrame:
        assessment = assess(item.provider, item.model)
        frame = QFrame()
        frame.setObjectName("ModelReviewRow")
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)

        title = QLabel(f"<b>{item.provider} · {item.model}</b>")
        layout.addWidget(title)
        facts = QLabel(
            f"First seen {item.first_seen}. {assessment.inferred.description[:1].upper()}{assessment.inferred.description[1:]}.\n"
            f"Price unknown: cost estimates use {item.provider}'s default rate "
            "until you add one in Settings → Pricing."
        )
        facts.setWordWrap(True)
        layout.addWidget(facts)

        verdict = QLabel(assessment.summary)
        verdict.setWordWrap(True)
        verdict.setObjectName("SectionNote")
        layout.addWidget(verdict)
        for line in assessment_lines(assessment, self._labels):
            detail = QLabel(f"• {line}")
            detail.setWordWrap(True)
            layout.addWidget(detail)

        actions = QHBoxLayout()
        adopt = QPushButton("Adopt")
        adopt.setToolTip("Rank it from now on and move BEST FIT where listed above.")
        dismiss = QPushButton("Dismiss")
        dismiss.setToolTip("Stop listing it as new. It stays in the dropdown.")
        status = QLabel()
        actions.addWidget(adopt)
        actions.addWidget(dismiss)
        actions.addWidget(status, 1)
        layout.addLayout(actions)

        def decided(text: str) -> None:
            adopt.setEnabled(False)
            dismiss.setEnabled(False)
            status.setText(text)

        def do_adopt() -> None:
            self._watch.adopt(assessment)
            moved = [self._labels.get(m.key, m.key) for m in assessment.agent_moves]
            decided("Adopted — BEST FIT now on it for " + ", ".join(moved)
                    if moved else "Adopted.")
            self._on_decided("adopt", assessment)

        def do_dismiss() -> None:
            self._watch.dismiss(item.provider, item.model)
            decided("Dismissed.")
            self._on_decided("dismiss", assessment)

        adopt.clicked.connect(do_adopt)
        dismiss.clicked.connect(do_dismiss)
        return frame

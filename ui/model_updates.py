"""The Model Updates card in the left rail, and the dialog that reviews them.

A scan never changes a recommendation by itself — it only puts the new model
in front of the user with an assessment. Adopting is the one action that
moves a BEST FIT: **Update** on the card for the models marked there, or
**Adopt** in the dialog for one at a time. See
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
from ui.widgets import KeyValue, ScreenCard

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


class ModelUpdatesCard(ScreenCard):
    """Last check, the new models as rows you mark, Update, Check now, Review.

    Click a row to mark it (it takes a background), click again to unmark it; **Update N** adopts the
    marked ones and nothing else. The list stops growing at VISIBLE_ROWS and
    scrolls beyond that: this card shares the left rail with the agent list,
    and every row here is a row of agents pushed out of view. What each model
    would change is in the Review dialog and in each row's tooltip.
    """

    check_requested = Signal()
    review_requested = Signal()
    update_requested = Signal(list)      # [(provider, model), ...] that were marked

    VISIBLE_ROWS = 4
    ROW_HEIGHT = 24

    def __init__(self, parent=None):
        super().__init__("MODEL UPDATES", parent)
        layout = self.body

        self.last_row = KeyValue("Last check", "never")
        layout.addWidget(self.last_row)
        # Where the quality judgement comes from, credited as its licence asks.
        self.ratings_row = KeyValue("Ratings", "none")
        layout.addWidget(self.ratings_row)
        # How many are new is the header's status ("7 new"); a second row
        # saying the same number was one row of agents less in the rail.
        self.divider = QFrame()
        self.divider.setObjectName("ScreenDivider")
        self.divider.setFixedHeight(1)
        self.divider.hide()
        layout.addWidget(self.divider)

        self._scanned = False
        self._rows: dict[tuple[str, str], QPushButton] = {}
        self._marked: set[tuple[str, str]] = set()
        self._list_body = QWidget()
        self._list_layout = QVBoxLayout(self._list_body)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()
        self.list_area = QScrollArea()
        self.list_area.setObjectName("ModelPickList")
        self.list_area.setWidget(self._list_body)
        self.list_area.setWidgetResizable(True)
        self.list_area.setFrameShape(QFrame.NoFrame)
        self.list_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_area.hide()
        layout.addWidget(self.list_area)

        keys = QHBoxLayout()
        keys.setContentsMargins(0, 4, 0, 0)
        keys.setSpacing(6)
        self.update_btn = QPushButton("Update 0")
        self.update_btn.setObjectName("ModelUpdateAction")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setFixedHeight(26)
        self.update_btn.clicked.connect(self._emit_update)
        self.update_btn.hide()
        self.check_btn = QPushButton("Check now")
        self.check_btn.setObjectName("ScreenKey")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setFixedHeight(26)
        self.check_btn.setToolTip(
            "Ask each provider with an API key which models it serves now. "
            "Listing models is free and sends no prompt. Also runs at every start."
        )
        self.check_btn.clicked.connect(self.check_requested.emit)
        keys.addWidget(self.update_btn, 1)
        keys.addWidget(self.check_btn, 1)
        layout.addLayout(keys)

        self.review_btn = QPushButton("Review details →")
        self.review_btn.setObjectName("RailLink")
        self.review_btn.setCursor(Qt.PointingHandCursor)
        self.review_btn.clicked.connect(self.review_requested.emit)
        layout.addWidget(self.review_btn, 0, Qt.AlignRight)

    # -- marking ------------------------------------------------------------

    def marked(self) -> list[tuple[str, str]]:
        """Marked models in the order the list shows them."""
        return [key for key in self._rows if key in self._marked]

    def toggle(self, provider: str, model: str) -> None:
        """Mark an unmarked row or unmark a marked one (what a click does)."""
        button = self._rows.get((provider, model))
        if button is not None:
            button.click()

    def _on_row_toggled(self, key: tuple[str, str], checked: bool) -> None:
        if checked:
            self._marked.add(key)
        else:
            self._marked.discard(key)
        self._sync_update_button()

    def _sync_update_button(self) -> None:
        count = len(self._marked)
        self.update_btn.setEnabled(count > 0)
        self.update_btn.setText(f"Update {count}" if count else "Update")
        self.update_btn.setToolTip(
            "Bring in the selected models: the router can rank them, and an "
            "agent's BEST FIT moves only where one is better value than its pick."
            if count else "Click a model above to mark it; click again to unmark it.")

    def _emit_update(self) -> None:
        marked = self.marked()
        if marked:
            self.update_requested.emit(marked)

    # -- state ----------------------------------------------------------------

    def set_ratings(self, text: str, tip: str) -> None:
        self.ratings_row.set(text, tip)

    def set_checking(self, checking: bool) -> None:
        self.check_btn.setEnabled(not checking)
        self.check_btn.setText("Checking…" if checking else "Check now")
        if checking:
            self.set_status("checking", "warn")
        else:
            self._show_count()

    def _show_count(self) -> None:
        count = len(self._rows)
        if not self._scanned:
            self.set_status("not checked", "off")
        elif count:
            self.set_status(f"{count} new", "warn",
                            "Click a model below to mark it for updating.")
        else:
            self.set_status("up to date", "ok", "Nothing new since the last check.")

    def set_updating(self, updating: bool) -> None:
        self.update_btn.setEnabled(not updating and bool(self._marked))
        if updating:
            self.update_btn.setText("Updating…")
        else:
            self._sync_update_button()

    def show_state(self, last_scan: str, notes: Mapping[str, str],
                   pending: list[NewModel],
                   summaries: Mapping[tuple[str, str], str] | None = None) -> None:
        checked = sum(1 for note in notes.values() if note == "ok")
        self.last_row.set(
            describe_scan_time(last_scan),
            f"{describe_notes(notes)}\n\n{checked} of {len(notes)} providers "
            "checked. A provider without a key cannot be asked." if notes else
            "No scan has run yet.",
        )
        count = len(pending)
        self._scanned = bool(last_scan)

        # Rebuild the rows; a mark survives as long as its model is pending.
        for button in self._rows.values():
            # Hidden and detached now, deleted later: deleteLater alone left
            # the old row painted under the new one until the event loop ran.
            self._list_layout.removeWidget(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self._rows.clear()
        keys = [(item.provider, item.model) for item in pending]
        self._marked &= set(keys)
        summaries = summaries or {}
        for item in pending:
            key = (item.provider, item.model)
            button = QPushButton()
            button.setObjectName("ModelPick")
            button.setCheckable(True)
            button.setChecked(key in self._marked)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(self.ROW_HEIGHT)
            tip = f"{item.provider} · {item.model}\nFirst seen {item.first_seen}."
            if summaries.get(key):
                tip += f"\n{summaries[key]}"
            button.setToolTip(tip + "\n\nClick to mark for updating; click again to unmark.")
            self._rows[key] = button
            # No checkbox: a marked row is the one with a background (the
            # :checked rule in ui/style.py). A second click clears it.
            button.setText(item.model)
            button.toggled.connect(
                lambda checked, k=key: self._on_row_toggled(k, checked))
            self._list_layout.insertWidget(self._list_layout.count() - 1, button)

        visible = min(count, self.VISIBLE_ROWS)
        self.list_area.setFixedHeight(visible * self.ROW_HEIGHT + 2)
        self.list_area.setVisible(count > 0)
        self.divider.setVisible(count > 0)
        self.update_btn.setVisible(count > 0)
        self._sync_update_button()
        self._show_count()
        self.review_btn.setVisible(count > 0)
        self.review_btn.setToolTip(
            "See whether each new model should be the best fit, then adopt or dismiss."
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
            "dropdown. Adopting one lets the router rank it. An agent's BEST FIT "
            "moves only where the model is better value than its current pick: "
            "rated at least as well and known to cost less. Newer counts for "
            "nothing. Nothing changes until you adopt."
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

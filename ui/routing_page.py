"""Settings → Models: routing mode (mockup B), per-agent overrides, the rules
you made with *Always for this kind of job*, and the savings tally.

Byte-identical in Sentinel and Imprint (tests/test_providers_drift.py). Pure
view over a settings dict, a `RoutingRules` and a `SavingsTally`; every change
is handed to `on_change(settings)` for the app to save and apply. Nothing here
fetches.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QComboBox, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from services import routing_advisor as ra

APP_SETTING = "Use the app setting"


class RoutingModesPage(QWidget):
    def __init__(self, settings: dict, agents: dict, rules: ra.RoutingRules,
                 tally: ra.SavingsTally | None = None, on_change=None,
                 privacy_note: str = "", parent=None):
        super().__init__(parent)
        self.settings, self.agents, self.rules, self.tally = settings, agents, rules, tally
        self._on_change = on_change
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Routing mode</b>", self))
        row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode in ra.MODES:
            button = QPushButton(ra.MODE_LABELS[mode], self)
            button.setCheckable(True)
            button.setToolTip(ra.MODE_HINTS[mode])
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            row.addWidget(button)
            button.clicked.connect(lambda _c=False, m=mode: self._set_app_mode(m))
        row.addStretch()
        layout.addLayout(row)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        if privacy_note:
            note = QLabel(privacy_note, self)
            note.setWordWrap(True)
            layout.addWidget(note)

        layout.addWidget(QLabel("<b>Per agent</b>", self))
        grid = QGridLayout()
        self.agent_boxes: dict[str, QComboBox] = {}
        for i, (key, label) in enumerate(agents.items()):
            box = QComboBox(self)
            box.addItem(APP_SETTING, "")
            for mode in ra.MODES:
                box.addItem(ra.MODE_LABELS[mode], mode)
            box.currentIndexChanged.connect(lambda _i, k=key, b=box: self._set_agent_mode(k, b))
            grid.addWidget(QLabel(label, self), i // 2, (i % 2) * 2)
            grid.addWidget(box, i // 2, (i % 2) * 2 + 1)
            self.agent_boxes[key] = box
        layout.addLayout(grid)

        layout.addWidget(QLabel("<b>Remembered choices</b>", self))
        self.rules_table = QTableWidget(0, 4, self)
        self.rules_table.setHorizontalHeaderLabels(["Agent", "Kind of job", "Runs on", ""])
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.rules_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column in (0, 1, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.rules_table, 1)
        self.rules_empty = QLabel(
            "None yet. Choosing “Always for this kind of job” on a suggestion adds "
            "one here; remove it to be asked again.", self)
        self.rules_empty.setWordWrap(True)
        layout.addWidget(self.rules_empty)

        self.tally_label = QLabel(self)
        self.tally_label.setWordWrap(True)
        layout.addWidget(self.tally_label)
        self.refresh()

    # ── State ───────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        mode = ra.mode_for(self.settings)
        self.mode_buttons[mode].setChecked(True)
        self.hint.setText(ra.MODE_HINTS[mode])
        overrides = self.settings.get(ra.OVERRIDES_KEY) or {}
        for key, box in self.agent_boxes.items():
            box.blockSignals(True)
            index = box.findData(overrides.get(key, ""))
            box.setCurrentIndex(max(index, 0))
            box.blockSignals(False)
        rules = self.rules.all()
        self.rules_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            for column, text in enumerate((self.agents.get(rule.agent, rule.agent),
                                           rule.kind_label or rule.kind.replace("_", " "),
                                           f"{rule.provider} · {rule.model}")):
                self.rules_table.setItem(row, column, QTableWidgetItem(text))
            button = QPushButton("Remove", self.rules_table)
            button.clicked.connect(lambda _c=False, r=rule: self._remove(r))
            self.rules_table.setCellWidget(row, 3, button)
        self.rules_table.setVisible(bool(rules))
        self.rules_empty.setVisible(not rules)
        self.tally_label.setText(self.tally.summary() if self.tally is not None else "")

    def _set_app_mode(self, mode: str) -> None:
        ra.set_mode(self.settings, mode)
        self.hint.setText(ra.MODE_HINTS[mode])
        self._changed()

    def _set_agent_mode(self, key: str, box: QComboBox) -> None:
        ra.set_mode(self.settings, box.currentData() or "", agent=key)
        self._changed()

    def _remove(self, rule: ra.Rule) -> None:
        self.rules.remove(rule.agent, rule.kind)
        self.refresh()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change(self.settings)

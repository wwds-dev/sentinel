"""Settings → Local models (mockup D): what runs on this Mac.

Pure view: it is handed a `services.local_models.Snapshot` and never fetches.
The app's discovery worker produces snapshots; Refresh and Start server are
callbacks the caller wires to that worker.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from services import local_models
from services.local_models import Snapshot

COLUMNS = ("Model", "Source", "Size", "State", "Rating", "Used by")


class LocalModelsPage(QWidget):
    def __init__(self, on_refresh=None, on_start=None, parent=None):
        super().__init__(parent)
        self._on_refresh, self._on_start = on_refresh, on_start
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.lmstudio_status = QLabel(self)
        self.ollama_status = QLabel(self)
        self.ram_status = QLabel(self)
        self.start_button = QPushButton("Start server", self)
        self.refresh_button = QPushButton("Refresh", self)
        for widget in (self.lmstudio_status, self.ollama_status, self.ram_status):
            top.addWidget(widget)
        top.addStretch()
        top.addWidget(self.start_button)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)
        self.note = QLabel(
            "Ratings are measured automatically in a later release; until then "
            "a local model is unrated and is never picked automatically.", self)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.start_button.clicked.connect(lambda: self._on_start and self._on_start())
        self.refresh_button.clicked.connect(
            lambda: self._on_refresh and self._on_refresh())
        self.set_snapshot(local_models.EMPTY)

    def set_snapshot(self, snap: Snapshot, used_by=None) -> None:
        lines = local_models.status_lines(snap)
        self.lmstudio_status.setText(lines["lmstudio"])
        self.ollama_status.setText(lines["ollama"])
        self.ram_status.setText(lines["ram"])
        self.start_button.setVisible(not snap.lmstudio_up)
        self.hint.setText(local_models.start_server_hint(snap)
                          if not snap.lmstudio_up else "")
        self.hint.setVisible(not snap.lmstudio_up)
        rows = local_models.table_rows(snap, used_by)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            model = row["model"] + (f"  ·  {row['tag'].upper()}" if row["tag"] else "")
            values = (model, row["source"], row["size"], row["state"],
                      row["rating"], row["used_by"])
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setToolTip(row["id"])
                self.table.setItem(r, c, item)

    def rows(self) -> list[list[str]]:
        """Cell text of every row. For tests."""
        return [[self.table.item(r, c).text() for c in range(len(COLUMNS))]
                for r in range(self.table.rowCount())]

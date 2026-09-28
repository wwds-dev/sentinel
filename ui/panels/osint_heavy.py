"""Bloodhound — deep investigation, dossier, and image OSINT.

Fourth vertical moved out of `main.py` (phase 4, `docs/refactor_plan.md`).

The EXIF helpers moved as module-level functions rather than methods: they read
a file and return text, touch no widget and no host, and as functions they can
be tested against a real JPEG without building a panel at all.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFileDialog,
    QGridLayout, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout,
    QWidget,
)

from services import osint_catalog
from services.local_file_search import FileSearchFilters, FileSearchReport, normalise_extensions
from services.remote_file_search import validate_ssh_target
from services.runtime_paths import user_data_base
from ui.panels.base import AgentPanel
from ui.workers import LiveCollectionWorker, LocalFileSearchWorker, RemoteFileSearchWorker
from ui.widgets import MenuComboBox, SectionView


# ── EXIF, as plain functions ────────────────────────────────────────────────

def extract_exif(path: str) -> dict:
    """Every EXIF tag we can read, or {} — a stripped image is not an error."""
    try:
        from PIL import Image as PILImage
        from PIL.ExifTags import TAGS, GPSTAGS
        img = PILImage.open(path)
        raw = img._getexif()
        if not raw:
            return {}
        result = {}
        for tag_id, value in raw.items():
            tag = TAGS.get(tag_id, str(tag_id))
            if tag == "GPSInfo" and isinstance(value, dict):
                result["GPSInfo"] = {GPSTAGS.get(k, k): v for k, v in value.items()}
            elif isinstance(value, (str, int, float, bytes)):
                result[tag] = value
        return result
    except Exception:
        return {}


def gps_to_decimal(dms, ref: str) -> float:
    """Degrees/minutes/seconds → signed decimal degrees. 0.0 when unreadable."""
    try:
        d, m, s = float(dms[0]), float(dms[1]), float(dms[2])
        decimal = d + m / 60 + s / 3600
        return round(-decimal if ref in ("S", "W") else decimal, 6)
    except Exception:
        return 0.0


def exif_summary(path: str) -> str:
    """One line for the panel: date, device, software, coordinates."""
    exif = extract_exif(path)
    if not exif:
        return "No EXIF data found in this image."
    parts = []
    for key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
        if key in exif:
            parts.append(f"Date: {exif[key]}")
            break
    device = (str(exif.get("Make", "")) + " " + str(exif.get("Model", ""))).strip()
    if device:
        parts.append(f"Device: {device}")
    if exif.get("Software"):
        parts.append(f"Software: {str(exif['Software'])[:40]}")
    gps = exif.get("GPSInfo", {})
    if gps.get("GPSLatitude") and gps.get("GPSLongitude"):
        lat = gps_to_decimal(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
        lon = gps_to_decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
        parts.append(f"GPS: {lat}°, {lon}°")
    return "  ·  ".join(parts) if parts else "EXIF present but no key fields extracted."


def exif_for_prompt(path: str) -> str:
    """The same metadata, written for the model rather than the status line."""
    exif = extract_exif(path)
    if not exif:
        return "No EXIF metadata could be extracted (data may have been stripped)."
    lines = [f"Image file: {Path(path).name}"]
    for key in ("DateTimeOriginal", "DateTime", "Make", "Model", "Software",
                "LensMake", "LensModel", "ImageWidth", "ImageLength",
                "Orientation", "Flash", "FocalLength"):
        if key in exif:
            lines.append(f"  {key}: {exif[key]}")
    gps = exif.get("GPSInfo", {})
    if gps.get("GPSLatitude") and gps.get("GPSLongitude"):
        lat = gps_to_decimal(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
        lon = gps_to_decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
        lines.append(f"  GPS Coordinates: {lat}, {lon}")
        lines.append(f"  Google Maps link: https://maps.google.com/?q={lat},{lon}")
        if gps.get("GPSAltitude"):
            lines.append(f"  GPS Altitude: {gps['GPSAltitude']} m")
        if gps.get("GPSImgDirection"):
            lines.append(f"  Camera direction: {gps['GPSImgDirection']} degrees")
    return "\n".join(lines)


class OsintHeavyPanel(AgentPanel):
    """Investigate one target in depth and produce a dossier."""

    agent_key = "osint_heavy"
    #: Swapped by tests that would rather not start a thread.
    collection_worker_class = LiveCollectionWorker

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.setObjectName("OSINTHeavyPanel")
        self._last_response = ""
        self._image_path = ""
        self._image_osint = ""
        self._file_search_worker = None
        self._collecting = False
        self._build()
        self.polish_workspace()
        self.hide()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ── Investigation Brief ──────────────────────────────────────────
        brief_group = QGroupBox("Investigation Brief")
        brief_group.setObjectName("OSINTHeavyBriefBox")
        brief_layout = QGridLayout(brief_group)
        brief_layout.setSpacing(6)

        brief_layout.addWidget(QLabel("Target:"), 0, 0)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText(
            "Name, username, email, domain, IP, phone number, or organisation…"
        )
        brief_layout.addWidget(self.target_input, 0, 1, 1, 3)

        brief_layout.addWidget(QLabel("Target Type:"), 1, 0)
        self.type_box = MenuComboBox()
        self.type_box.addItems([
            "Person", "Username", "Email Address", "Domain / IP",
            "Organisation", "Phone Number", "Auto-detect",
        ])
        brief_layout.addWidget(self.type_box, 1, 1)

        brief_layout.addWidget(QLabel("Scope:"), 1, 2)
        self.scope_box = MenuComboBox()
        self.scope_box.addItems(["Quick Scan", "Standard Investigation", "Deep Dive"])
        self.scope_box.setCurrentText("Standard Investigation")
        brief_layout.addWidget(self.scope_box, 1, 3)

        brief_layout.addWidget(QLabel("Objective:"), 2, 0)
        self.objective_input = QTextEdit()
        self.objective_input.setPlaceholderText(
            "What are you trying to establish? e.g. verify identity, map infrastructure, "
            "check breach exposure, assess threat level…"
        )
        self.objective_input.setMinimumHeight(70)
        self.objective_input.setMaximumHeight(120)
        brief_layout.addWidget(self.objective_input, 2, 1, 1, 3)

        self.investigate_btn = QPushButton("Investigate")
        self.investigate_btn.setMinimumWidth(140)
        self.investigate_btn.setObjectName("PrimaryAction")
        self.investigate_btn.clicked.connect(self.investigate)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setObjectName("DangerAction")
        self.stop_btn.clicked.connect(self.stop)
        self.set_busy(self.investigate_btn, self.stop_btn, False)

        provider_row_container = self.build_run_bar(
            self.investigate_btn,
            stop=self.stop_btn,
            context="Investigation",
        )

        brief_layout.addWidget(provider_row_container, 3, 0, 1, 4)
        layout.addWidget(brief_group)

        # ── Target Image (optional) ──────────────────────────────────────
        image_group = QGroupBox(
            "Target Image  —  optional, enables EXIF analysis && face search links")
        image_group.setObjectName("OSINTHeavyImageBox")
        image_outer = QVBoxLayout(image_group)
        image_outer.setSpacing(4)
        image_outer.setContentsMargins(8, 4, 8, 4)
        image_top_row = QHBoxLayout()
        self.image_label = QLabel("No image selected")
        self.image_label.setStyleSheet("color: #666; font-style: italic;")
        self.image_label.setMinimumWidth(200)
        image_top_row.addWidget(self.image_label, 1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setMinimumWidth(100)
        self.browse_btn.setMaximumWidth(120)
        self.browse_btn.clicked.connect(self.browse_image)
        image_top_row.addWidget(self.browse_btn)
        self.clear_image_btn = QPushButton("Clear Image")
        self.clear_image_btn.setMinimumWidth(110)
        self.clear_image_btn.setMaximumWidth(130)
        self.clear_image_btn.clicked.connect(self.clear_image)
        image_top_row.addWidget(self.clear_image_btn)
        image_outer.addLayout(image_top_row)
        self.exif_display = QTextEdit()
        self.exif_display.setReadOnly(True)
        self.exif_display.setMinimumHeight(56)
        self.exif_display.setMaximumHeight(90)
        self.exif_display.setPlaceholderText(
            "EXIF metadata will appear here after selecting an image…"
        )
        self.exif_display.setStyleSheet(
            "font-family: monospace; font-size: 11px; color: #aaa;"
        )
        image_outer.addWidget(self.exif_display)
        self.image_details = QTextBrowser()
        self.image_details.setOpenExternalLinks(True)
        self.image_details.setMaximumHeight(190)
        self.image_details.setVisible(False)
        image_outer.addWidget(self.image_details)
        layout.addWidget(image_group)

        self._build_file_discovery(layout)

        # ── Results splitter: tabs left, indicators right ────────────────
        results_splitter = QSplitter(Qt.Horizontal)

        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)
        self.stream_box = QTextBrowser()
        self.stream_box.setOpenExternalLinks(False)
        self.stream_box.setVisible(False)
        result_layout.addWidget(self.stream_box, 1)
        self.sections = SectionView()
        result_layout.addWidget(self.sections, 1)

        results_splitter.addWidget(result_widget)

        # ── Indicators sidebar ───────────────────────────────────────────
        indicators_widget = QWidget()
        indicators_layout = QVBoxLayout(indicators_widget)
        indicators_layout.setContentsMargins(8, 0, 0, 0)
        indicators_layout.setSpacing(10)

        threat_group = QGroupBox("Threat Level (AI estimate)")
        threat_group.setObjectName("OSINTHeavyThreatBox")
        threat_layout = QVBoxLayout(threat_group)
        self.threat_bar = QProgressBar()
        self.threat_bar.setRange(0, 10)
        self.threat_bar.setValue(0)
        self.threat_bar.setTextVisible(False)
        self.threat_bar.setFixedHeight(16)
        self.threat_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #cc2200; }"
        )
        threat_layout.addWidget(self.threat_bar)
        self.threat_label = QLabel("—")
        self.threat_label.setAlignment(Qt.AlignCenter)
        threat_layout.addWidget(self.threat_label)
        indicators_layout.addWidget(threat_group)

        conf_group = QGroupBox("Confidence (AI estimate)")
        conf_group.setObjectName("OSINTHeavyConfBox")
        conf_layout = QVBoxLayout(conf_group)
        self.conf_label = QLabel("—")
        self.conf_label.setAlignment(Qt.AlignCenter)
        self.conf_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #dd88ff;")
        conf_layout.addWidget(self.conf_label)
        indicators_layout.addWidget(conf_group)

        sources_group = QGroupBox("Sources")
        sources_group.setObjectName("OSINTHeavySourcesBox")
        sources_layout = QVBoxLayout(sources_group)
        self.sources_label = QLabel("—")
        self.sources_label.setAlignment(Qt.AlignCenter)
        self.sources_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #4db8ff;")
        sources_layout.addWidget(self.sources_label)
        indicators_layout.addWidget(sources_group)

        gauge_note = QLabel(
            "Threat and Confidence are the AI's own estimate, not a measurement. "
            "Sources counts the public sources actually contacted by live collection."
        )
        gauge_note.setWordWrap(True)
        gauge_note.setStyleSheet("font-size: 10px; color: #888;")
        indicators_layout.addWidget(gauge_note)

        depth_group = QGroupBox("Depth")
        depth_group.setObjectName("OSINTHeavyDepthBox")
        depth_layout = QVBoxLayout(depth_group)
        self.depth_label = QLabel("—")
        self.depth_label.setAlignment(Qt.AlignCenter)
        self.depth_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #aaaaff;")
        depth_layout.addWidget(self.depth_label)
        indicators_layout.addWidget(depth_group)

        indicators_layout.addStretch()

        self.save_btn = QPushButton("Save Report")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save)
        indicators_layout.addWidget(self.save_btn)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        indicators_layout.addWidget(self.clear_btn)

        results_splitter.addWidget(indicators_widget)
        results_splitter.setSizes([680, 220])

        layout.addWidget(results_splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(self.status_label)

    def _build_file_discovery(self, layout: QVBoxLayout) -> None:
        group = QGroupBox("File Discovery — selected locations only")
        group.setObjectName("BloodhoundFileDiscoveryBox")
        group.setCheckable(True)
        group.setChecked(False)
        outer = QVBoxLayout(group)
        self.file_discovery_body = QWidget()
        body = QVBoxLayout(self.file_discovery_body)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(6)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Search location:"))
        self.file_source_box = MenuComboBox()
        self.file_source_box.addItems(["This Mac", "Remote SSH machine"])
        source_row.addWidget(self.file_source_box)
        source_row.addStretch()
        body.addLayout(source_row)

        self.remote_file_widget = QWidget()
        remote = QGridLayout(self.remote_file_widget)
        remote.setContentsMargins(0, 0, 0, 0)
        remote.addWidget(QLabel("SSH host / IP / alias:"), 0, 0)
        self.remote_host_input = QLineEdit()
        self.remote_host_input.setPlaceholderText("server.example.com or 192.0.2.10")
        remote.addWidget(self.remote_host_input, 0, 1)
        remote.addWidget(QLabel("User:"), 0, 2)
        self.remote_user_input = QLineEdit()
        self.remote_user_input.setPlaceholderText("SSH username")
        remote.addWidget(self.remote_user_input, 0, 3)
        remote.addWidget(QLabel("Port:"), 0, 4)
        self.remote_port_input = QLineEdit("22")
        self.remote_port_input.setMaximumWidth(80)
        remote.addWidget(self.remote_port_input, 0, 5)
        remote.addWidget(QLabel("Remote folders:"), 1, 0)
        self.remote_roots_input = QLineEdit()
        self.remote_roots_input.setPlaceholderText("/home/user/Documents, /srv/archive")
        remote.addWidget(self.remote_roots_input, 1, 1, 1, 4)
        self.open_ssh_btn = QPushButton("Open SSH Terminal")
        self.open_ssh_btn.clicked.connect(self.open_ssh_terminal)
        remote.addWidget(self.open_ssh_btn, 1, 5)
        remote_note = QLabel(
            "Uses your SSH agent, ~/.ssh/config and known_hosts. Passwords and private keys are not stored."
        )
        remote_note.setStyleSheet("font-size: 11px; color: #777;")
        remote.addWidget(remote_note, 2, 0, 1, 6)
        self.remote_file_widget.setVisible(False)
        body.addWidget(self.remote_file_widget)
        self.file_source_box.currentTextChanged.connect(self._set_file_source)

        folder_row = QHBoxLayout()
        self.file_folders = QListWidget()
        self.file_folders.setObjectName("BloodhoundSearchFolders")
        self.file_folders.setMinimumHeight(54)
        self.file_folders.setMaximumHeight(92)
        self.file_folders.setToolTip("Only folders listed here will be searched.")
        folder_row.addWidget(self.file_folders, 1)
        folder_buttons = QVBoxLayout()
        self.add_folder_btn = QPushButton("Add Folder…")
        self.add_folder_btn.clicked.connect(self.add_search_folder)
        folder_buttons.addWidget(self.add_folder_btn)
        self.remove_folder_btn = QPushButton("Remove")
        self.remove_folder_btn.clicked.connect(self.remove_search_folders)
        folder_buttons.addWidget(self.remove_folder_btn)
        folder_buttons.addStretch()
        folder_row.addLayout(folder_buttons)
        body.addLayout(folder_row)

        filters = QGridLayout()
        filters.addWidget(QLabel("File name:"), 0, 0)
        self.file_name_filter = QLineEdit()
        self.file_name_filter.setPlaceholderText("Full or partial name…")
        filters.addWidget(self.file_name_filter, 0, 1)
        self.file_name_mode = MenuComboBox()
        self.file_name_mode.addItems(["Contains", "Exact"])
        filters.addWidget(self.file_name_mode, 0, 2)
        filters.addWidget(QLabel("Type / extension:"), 0, 3)
        self.file_extension_filter = QLineEdit()
        self.file_extension_filter.setPlaceholderText("pdf, jpg, docx…")
        filters.addWidget(self.file_extension_filter, 0, 4)

        filters.addWidget(QLabel("Size (MB):"), 1, 0)
        self.file_min_size = QLineEdit()
        self.file_min_size.setPlaceholderText("Minimum")
        filters.addWidget(self.file_min_size, 1, 1)
        self.file_max_size = QLineEdit()
        self.file_max_size.setPlaceholderText("Maximum")
        filters.addWidget(self.file_max_size, 1, 2)

        self.file_after_enabled = QCheckBox("Modified after")
        filters.addWidget(self.file_after_enabled, 1, 3)
        self.file_after = QDateEdit(QDate.currentDate().addYears(-1))
        self.file_after.setCalendarPopup(True)
        self.file_after.setEnabled(False)
        self.file_after_enabled.toggled.connect(self.file_after.setEnabled)
        filters.addWidget(self.file_after, 1, 4)
        self.file_before_enabled = QCheckBox("Before")
        filters.addWidget(self.file_before_enabled, 2, 3)
        self.file_before = QDateEdit(QDate.currentDate())
        self.file_before.setCalendarPopup(True)
        self.file_before.setEnabled(False)
        self.file_before_enabled.toggled.connect(self.file_before.setEnabled)
        filters.addWidget(self.file_before, 2, 4)
        body.addLayout(filters)

        actions = QHBoxLayout()
        self.file_search_btn = QPushButton("Search Selected Folders")
        self.file_search_btn.setObjectName("PrimaryAction")
        self.file_search_btn.clicked.connect(self.start_file_search)
        actions.addWidget(self.file_search_btn)
        self.file_cancel_btn = QPushButton("Cancel")
        self.file_cancel_btn.setObjectName("DangerAction")
        self.file_cancel_btn.setEnabled(False)
        self.file_cancel_btn.clicked.connect(self.cancel_file_search)
        actions.addWidget(self.file_cancel_btn)
        self.file_clear_btn = QPushButton("Clear Results")
        self.file_clear_btn.clicked.connect(self.clear_file_results)
        actions.addWidget(self.file_clear_btn)
        actions.addStretch()
        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)
        self.file_progress.setTextVisible(False)
        self.file_progress.setFixedWidth(140)
        actions.addWidget(self.file_progress)
        self.file_status = QLabel("Choose folders to begin.")
        actions.addWidget(self.file_status)
        body.addLayout(actions)

        self.file_results = QTableWidget(0, 5)
        self.file_results.setObjectName("BloodhoundFileResults")
        self.file_results.setHorizontalHeaderLabels(
            ["Name", "Path", "Type", "Size", "Modified"]
        )
        self.file_results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_results.setSortingEnabled(True)
        self.file_results.verticalHeader().setVisible(False)
        header = self.file_results.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.file_results.setMinimumHeight(150)
        self.file_results.cellDoubleClicked.connect(self.reveal_file_result)
        body.addWidget(self.file_results)
        self.file_hint = QLabel(self._file_hint_text(remote=False))
        self.file_hint.setStyleSheet("font-size: 11px; color: #777;")
        self.file_hint.setWordWrap(True)
        body.addWidget(self.file_hint)

        outer.addWidget(self.file_discovery_body)
        self.file_discovery_body.setVisible(False)
        group.toggled.connect(self.file_discovery_body.setVisible)
        layout.addWidget(group)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The OSINT Framework catalogue feeds this agent's source suggestions;
        # refresh it off the UI thread, at most once a week (and once a run).
        osint_catalog.refresh_in_background()

    # ── Running ─────────────────────────────────────────────────────────
    def investigate(self) -> None:
        target = self.target_input.text().strip()
        target_type = self.type_box.currentText()
        scope = self.scope_box.currentText()
        objective = self.objective_input.toPlainText().strip()

        if not target:
            QMessageBox.warning(self, "Missing Input", "Please enter a target identifier.")
            return
        if not self.model:
            QMessageBox.warning(self, "No Model", "Please select a model.")
            return

        image_metadata = ""
        if self._image_path:
            image_metadata = exif_for_prompt(self._image_path)

        self._clear_displays()
        self._last_response = ""
        self.depth_label.setText(scope)
        self.set_busy(self.investigate_btn, self.stop_btn, True)
        self.save_btn.setEnabled(False)

        # The permission check comes first: live collection sends the target
        # to public sources, and that must not happen for a request the guard
        # is about to refuse.
        if not self.authorize(target):
            # Investigate was disabled above; put it back or a refused request
            # leaves the panel dead.
            self.status_label.setText("Blocked before sending.")
            self.set_busy(self.investigate_btn, self.stop_btn, False)
            return

        brief = (target, target_type, scope, objective, image_metadata)
        self._live_source_count = None
        collect = getattr(self.agent(), "collect_live", None)
        if collect is None:
            self._send_dossier_request(brief, [])
            return

        # Live public-source collection runs before the model call, on a
        # worker thread: a Deep Dive username sweep takes up to a minute. It is
        # a free, no-model step; the real number of sources contacted drives
        # the Sources gauge instead of the model's self-declared estimate.
        self.status_label.setText(
            "Collecting live public-source data (the WhatsMyName sweep on a "
            "username can take a minute)…" if scope == "Deep Dive"
            else "Collecting live public-source data…"
        )
        self._collecting = True
        worker = self.collection_worker_class(collect, target, target_type, scope)
        worker.progress_signal.connect(self._on_collection_progress)
        worker.finished_signal.connect(
            lambda results: self._on_collection_finished(worker, brief, results))
        worker.error_signal.connect(
            lambda error: self._on_collection_finished(worker, brief, []))
        self.worker = worker
        worker.start()

    def _on_collection_progress(self, message: str) -> None:
        if self._collecting:
            self.status_label.setText(f"Collecting live data — {message}")

    def _on_collection_finished(self, worker, brief: tuple, results: list) -> None:
        if not self._collecting or self.worker is not worker:
            # Stopped by the user (the request was already abandoned), or a
            # stale sweep finishing after a newer investigation started.
            return
        self._collecting = False
        # A provider failure never blocks the dossier: the model is told what
        # was collected, and gaps are reported as gaps.
        self._live_source_count = getattr(self.agent(), "last_source_count", None)
        self._send_dossier_request(brief, results)

    def _send_dossier_request(self, brief: tuple, live_results: list) -> None:
        target, target_type, scope, objective, image_metadata = brief
        messages = self.agent().build_messages(
            target, target_type, scope, objective, image_metadata,
            live_results=live_results)
        self.status_label.setText("Investigating…")
        self.start_worker(
            messages, target,
            on_token=self._on_token,
            on_finished=self._on_finished,
            on_error=self._on_error,
        )

    def _on_token(self, token: str) -> None:
        self._last_response += token
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(self._last_response)
        self.stream_box.moveCursor(QTextCursor.End)

    def _on_finished(self, full_response: str) -> None:
        self.record(full_response)
        self._last_response = full_response
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)
        self._populate_sections(full_response)
        self._update_indicators(full_response)
        self.status_label.setText("Investigation complete.")
        self.set_busy(self.investigate_btn, self.stop_btn, False)
        self.save_btn.setEnabled(True)

    def _on_error(self, error: str) -> None:
        self.abandon()
        self.sections.setVisible(False)
        self.stream_box.setVisible(True)
        self.stream_box.setPlainText(f"[Error] {error}")
        self.status_label.setText("Error.")
        self.set_busy(self.investigate_btn, self.stop_btn, False)

    def stop(self) -> None:
        self.stop_worker()
        if self._collecting:
            # Authorised but never sent: close the request so it is not billed.
            self._collecting = False
            self.abandon("cancelled")
        self.status_label.setText("Stopped.")
        self.set_busy(self.investigate_btn, self.stop_btn, False)

    # ── Local file discovery ───────────────────────────────────────────
    @staticmethod
    def _file_hint_text(remote: bool) -> str:
        if remote:
            return (
                "Read-only: Bloodhound lists file metadata over SFTP on the remote "
                "host. No file contents are transferred and nothing is sent to an AI. "
                "Double-click a result to reveal it in its folder."
            )
        return (
            "Read-only: Bloodhound checks file metadata locally. Nothing is uploaded. "
            "Double-click a result to reveal it in its folder."
        )

    def _set_file_source(self, source: str) -> None:
        remote = source == "Remote SSH machine"
        self.remote_file_widget.setVisible(remote)
        self.file_folders.setEnabled(not remote)
        self.add_folder_btn.setEnabled(not remote)
        self.remove_folder_btn.setEnabled(not remote)
        if hasattr(self, "file_hint"):
            self.file_hint.setText(self._file_hint_text(remote=remote))
        self.file_status.setText(
            "Enter an authenticated SSH machine and remote folders."
            if remote else
            (f"{self.file_folders.count()} folder(s) selected."
             if self.file_folders.count() else "Choose folders to begin.")
        )

    def add_search_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose a folder to search", str(Path.home()),
            QFileDialog.ShowDirsOnly,
        )
        if not path:
            return
        existing = {
            self.file_folders.item(i).text()
            for i in range(self.file_folders.count())
        }
        normalised = str(Path(path).resolve())
        if normalised not in existing:
            self.file_folders.addItem(normalised)
        self.file_status.setText(f"{self.file_folders.count()} folder(s) selected.")

    def remove_search_folders(self) -> None:
        for item in self.file_folders.selectedItems():
            self.file_folders.takeItem(self.file_folders.row(item))
        count = self.file_folders.count()
        self.file_status.setText(
            f"{count} folder(s) selected." if count else "Choose folders to begin."
        )

    @staticmethod
    def _megabytes(value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc
        if number < 0:
            raise ValueError(f"{label} cannot be negative.")
        return int(number * 1024 * 1024)

    def _file_filters(self) -> FileSearchFilters:
        minimum = self._megabytes(self.file_min_size.text(), "Minimum size")
        maximum = self._megabytes(self.file_max_size.text(), "Maximum size")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("Minimum size cannot be greater than maximum size.")
        after = None
        before = None
        if self.file_after_enabled.isChecked():
            date = self.file_after.date().toPython()
            after = datetime.combine(date, datetime.min.time())
        if self.file_before_enabled.isChecked():
            date = self.file_before.date().toPython()
            before = datetime.combine(date, datetime.max.time())
        if after and before and after > before:
            raise ValueError("The start date cannot be after the end date.")
        return FileSearchFilters(
            name=self.file_name_filter.text(),
            exact_name=self.file_name_mode.currentText() == "Exact",
            extensions=normalise_extensions(self.file_extension_filter.text()),
            min_size=minimum,
            max_size=maximum,
            modified_after=after,
            modified_before=before,
        )

    def start_file_search(self) -> None:
        remote = self.file_source_box.currentText() == "Remote SSH machine"
        if remote:
            roots = [
                value.strip()
                for value in self.remote_roots_input.text().split(",")
                if value.strip()
            ]
        else:
            roots = [
                self.file_folders.item(i).text()
                for i in range(self.file_folders.count())
            ]
        if not roots:
            QMessageBox.warning(
                self,
                "No Folders Selected",
                "Enter at least one absolute remote folder."
                if remote else "Choose at least one folder to search.",
            )
            return
        try:
            filters = self._file_filters()
            if remote:
                host = self.remote_host_input.text().strip()
                username = self.remote_user_input.text().strip()
                port = int(self.remote_port_input.text().strip())
                validate_ssh_target(host, username, port)
                if any(not root.startswith("/") for root in roots):
                    raise ValueError("Every remote folder must be an absolute path.")
        except ValueError as exc:
            QMessageBox.warning(self, "Check Search Filters", str(exc))
            return
        self.clear_file_results()
        self.file_progress.setRange(0, 0)
        self.file_search_btn.setEnabled(False)
        self.file_cancel_btn.setEnabled(True)
        self.add_folder_btn.setEnabled(False)
        self.remove_folder_btn.setEnabled(False)
        self.file_status.setText(
            "Searching remote host over SFTP…" if remote else "Searching locally…")
        worker = (
            RemoteFileSearchWorker(host, username, port, roots, filters)
            if remote else LocalFileSearchWorker(roots, filters)
        )
        self._file_search_worker = worker
        worker.progress_signal.connect(self._on_file_progress)
        worker.finished_signal.connect(self._on_file_search_finished)
        worker.error_signal.connect(self._on_file_search_error)
        worker.start()

    def open_ssh_terminal(self) -> None:
        try:
            host = self.remote_host_input.text().strip()
            username = self.remote_user_input.text().strip()
            port = int(self.remote_port_input.text().strip())
            validate_ssh_target(host, username, port)
        except ValueError as exc:
            QMessageBox.warning(self, "Check SSH Details", str(exc))
            return
        url = QUrl()
        url.setScheme("ssh")
        url.setUserName(username)
        url.setHost(host)
        url.setPort(port)
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(
                self,
                "Could Not Open Terminal",
                "No application is registered to open SSH links on this computer.",
            )

    def cancel_file_search(self) -> None:
        if self._file_search_worker and self._file_search_worker.isRunning():
            self._file_search_worker.cancel()
            self.file_status.setText("Stopping…")
            self.file_cancel_btn.setEnabled(False)

    def _on_file_progress(self, checked: int, found: int) -> None:
        self.file_status.setText(f"Checked {checked:,} items · found {found:,}")

    def _finish_file_search_ui(self) -> None:
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(1)
        self.file_search_btn.setEnabled(True)
        self.file_cancel_btn.setEnabled(False)
        self.add_folder_btn.setEnabled(True)
        self.remove_folder_btn.setEnabled(True)
        self._set_file_source(self.file_source_box.currentText())

    def _on_file_search_finished(self, report: FileSearchReport) -> None:
        self._finish_file_search_ui()
        self._populate_file_results(report)
        state = "Search cancelled" if report.cancelled else "Search complete"
        if report.limit_reached:
            state = "Safety limit reached"
        summary = (
            f"{state}: {len(report.matches):,} found; "
            f"{report.entries_checked:,} items checked"
        )
        if report.errors:
            summary += f"; {len(report.errors)} location error(s)"
            QMessageBox.warning(
                self,
                "Some Locations Could Not Be Read",
                "The search continued, but some locations were inaccessible:\n\n"
                + "\n".join(report.errors[:12]),
            )
        if report.limit_reached:
            summary += ". Narrow the folder or filters for more specific results."
        self.file_status.setText(summary)

    def _on_file_search_error(self, error: str) -> None:
        self._finish_file_search_ui()
        self.file_status.setText("Search could not be completed.")
        QMessageBox.critical(self, "File Search Error", error)

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.2f} GB"

    def _populate_file_results(self, report: FileSearchReport) -> None:
        self.file_results.setSortingEnabled(False)
        self.file_results.setRowCount(len(report.matches))
        for row, match in enumerate(report.matches):
            values = (
                match.name,
                match.path,
                match.extension,
                self._format_size(match.size),
                match.modified.strftime("%Y-%m-%d %H:%M"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setData(Qt.UserRole, match.size)
                self.file_results.setItem(row, column, item)
        self.file_results.setSortingEnabled(True)

    def clear_file_results(self) -> None:
        self.file_results.setRowCount(0)
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)

    def reveal_file_result(self, row: int, _column: int) -> None:
        item = self.file_results.item(row, 1)
        if not item:
            return
        path = Path(item.text())
        if not path.exists():
            QMessageBox.warning(self, "File Not Found", "This file is no longer available.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ── The dossier ─────────────────────────────────────────────────────
    def save(self) -> None:
        if not self._last_response:
            return
        target = (self.target_input.text().strip()
                  .replace(" ", "_").replace("/", "-")) or "target"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"osint_dossier_{target}_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save OSINT Dossier",
            str(user_data_base() / "data" / default_name),
            "Text files (*.txt);;All files (*)",
        )
        if path:
            Path(path).write_text(self._last_response, encoding="utf-8")
            self.status_label.setText(f"Saved to {Path(path).name}")

    def clear(self) -> None:
        self._clear_displays()
        self.target_input.clear()
        self.objective_input.clear()
        self.status_label.setText("")
        self._last_response = ""
        self.clear_image()

    def _clear_displays(self) -> None:
        self.sections.clear()
        self.stream_box.clear()
        self.stream_box.setVisible(False)
        self.sections.setVisible(True)
        if self._image_osint:
            self._populate_sections("")
        self.threat_bar.setValue(0)
        self.threat_label.setText("—")
        self.conf_label.setText("—")
        self.sources_label.setText("—")
        self.depth_label.setText("—")
        self.save_btn.setEnabled(False)

    def _populate_sections(self, text: str) -> None:
        sections = self.parse_sections(text)
        cards = [
            ("Overview", sections.get("overview", "")),
            ("Digital footprint", sections.get("footprint", "")),
            ("Infrastructure / social profile", sections.get("infra", "")),
            ("Risk and red flags", sections.get("risk", "")),
            ("Methodology and tools", sections.get("methodology", "")),
        ]
        if self._image_osint:
            cards.append(("Image OSINT", self._image_osint))
        self.sections.show_sections(cards, raw=text)

    @staticmethod
    def parse_sections(text: str) -> dict:
        """Split the dossier on its five numbered headings."""
        patterns = {
            "overview":    r"##\s*1\.\s*OVERVIEW(.*?)(?=##\s*2\.|$)",
            "footprint":   r"##\s*2\.\s*DIGITAL FOOTPRINT(.*?)(?=##\s*3\.|$)",
            "infra":       r"##\s*3\.\s*INFRASTRUCTURE.*?(.*?)(?=##\s*4\.|$)",
            "risk":        r"##\s*4\.\s*RISK.*?(.*?)(?=##\s*5\.|$)",
            "methodology": r"##\s*5\.\s*METHODOLOGY.*?(.*?)$",
        }
        result = {}
        for key, pat in patterns.items():
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            result[key] = m.group(1).strip() if m else ""
        return result

    def _update_indicators(self, text: str) -> None:
        threat_m = re.search(r"THREAT LEVEL[:\s]+(\d+)\s*/\s*10", text, re.IGNORECASE)
        if threat_m:
            level = int(threat_m.group(1))
            self.threat_bar.setValue(min(level, 10))
            self.threat_label.setText(f"{level}/10")

        conf_m = re.search(r"CONFIDENCE[:\s]+(\d+)\s*%", text, re.IGNORECASE)
        if conf_m:
            self.conf_label.setText(f"{conf_m.group(1)}%")

        sources_m = re.search(r"SOURCES REFERENCED[:\s]+(\d+)", text, re.IGNORECASE)
        real = getattr(self, "_live_source_count", None)
        if real is not None:
            # Real number of public sources contacted by live collection —
            # never the model's self-declared estimate.
            self.sources_label.setText(str(real))
            self.sources_label.setToolTip(
                "Public sources actually contacted by live collection.")
        elif sources_m:
            self.sources_label.setText(sources_m.group(1))
            self.sources_label.setToolTip(
                "AI-estimated — no live collection ran for this target type.")

    # ── Image OSINT ─────────────────────────────────────────────────────
    def browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Target Image", str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.tiff *.tif *.bmp *.webp *.heic);;All files (*)"
        )
        if not path:
            return
        self.set_image(path)

    def set_image(self, path: str) -> None:
        """Attach an image: its EXIF joins the prompt and opens local image details."""
        self._image_path = path
        self.image_label.setText(Path(path).name)
        self.image_label.setStyleSheet("color: #dd88ff; font-style: normal;")
        self.exif_display.setPlainText(exif_summary(path))
        self._populate_image_details(path)

    def clear_image(self) -> None:
        self._image_path = ""
        self.image_label.setText("No image selected")
        self.image_label.setStyleSheet("color: #666; font-style: italic;")
        self.exif_display.clear()
        self.image_details.clear()
        self.image_details.setVisible(False)

    def _populate_image_details(self, path: str) -> None:
        exif = extract_exif(path)
        fname = Path(path).name
        gps_block = ""
        gps = exif.get("GPSInfo", {})
        if gps.get("GPSLatitude") and gps.get("GPSLongitude"):
            lat = gps_to_decimal(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
            lon = gps_to_decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
            gps_block = (
                f'<h3 style="color:#f0c040;">GPS Coordinates Extracted</h3>'
                f"<p><b>Coordinates:</b> {lat}, {lon}</p>"
                f'<p><a href="https://maps.google.com/?q={lat},{lon}">Google Maps</a>'
                f' &nbsp;|&nbsp; <a href="https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=15">OpenStreetMap</a>'
                f' &nbsp;|&nbsp; <a href="https://suncalc.org/#/{lat},{lon},14/">SunCalc</a></p>'
            )
        exif_rows = ""
        for key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized",
                    "Make", "Model", "Software", "LensMake", "LensModel",
                    "ImageWidth", "ImageLength", "Orientation", "Flash", "FocalLength"):
            if key in exif:
                exif_rows += (f"<tr><td style='color:#888;padding-right:14px;'>{key}</td>"
                              f"<td>{exif[key]}</td></tr>")
        no_exif = (
            "<p style='color:#ff8888;'>No EXIF data found — the image may have been stripped "
            "(common with screenshots, social media downloads, and edited files). "
            "This itself can be a signal.</p>"
            if not exif else ""
        )
        html = (
            "<html><body style='font-family:monospace;font-size:12px;color:#ccc;background:#1a1a1a;padding:8px;'>"
            f"<h2 style='color:#dd88ff;'>Image OSINT &mdash; {fname}</h2>"
            f"{no_exif}{gps_block}"
            "<h3 style='color:#4db8ff;'>Reverse Image Search</h3>"
            "<p style='color:#aaa;'>Upload the image at each service to search for matches:</p><ul>"
            "<li><a href='https://tineye.com'>TinEye</a> &mdash; reverse image search with date history</li>"
            "<li><a href='https://images.google.com'>Google Images</a> &mdash; click the camera icon to upload</li>"
            "<li><a href='https://yandex.com/images'>Yandex Images</a> &mdash; strong face/person matching</li>"
            "<li><a href='https://www.bing.com/visualsearch'>Bing Visual Search</a> &mdash; Microsoft image search</li>"
            "</ul>"
            "<h3 style='color:#ff88aa;'>Face Recognition Services</h3>"
            "<p style='color:#aaa;'>Upload the image to search for the person across the public web:</p><ul>"
            "<li><a href='https://pimeyes.com'>PimEyes</a> &mdash; facial recognition across billions of public images</li>"
            "<li><a href='https://facecheck.id'>FaceCheck.ID</a> &mdash; face search across social media profiles</li>"
            "<li><a href='https://lenso.ai'>Lenso.ai</a> &mdash; AI-powered reverse image and face search</li>"
            "</ul>"
            "<h3 style='color:#3cff88;'>Extracted EXIF Metadata</h3>"
            + ("<table>" + exif_rows + "</table>" if exif_rows
               else "<p style='color:#888;'>No key EXIF fields found.</p>")
            + "<br><p style='color:#555;font-size:11px;'>For authorised investigative use only.</p>"
            "</body></html>"
        )
        self.image_details.setHtml(html)
        self.image_details.setVisible(True)

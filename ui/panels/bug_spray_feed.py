"""Saved public-program feed inside Sentinel's Bug Spray workspace."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTabWidget,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from agents.bug_spray.bug_spray import config
from agents.bug_spray.bug_spray.feed import read_feed, read_program
from agents.bug_spray.bug_spray.store import Store

PROJECT = Path(__file__).resolve().parents[2] / "agents" / "bug_spray"
PYTHON = PROJECT / ".venv" / "bin" / "python"


def _event_label(event: dict) -> str:
    labels = []
    if event.get("new"):
        labels.append("New")
    if event.get("returned"):
        labels.append("Returned")
    if event.get("gone"):
        labels.append("Gone")
    if event.get("status"):
        labels.append(event["status"].title())
    if event.get("scope_added") or event.get("scope_removed"):
        labels.append(f"Scope +{len(event.get('scope_added', []))}/-{len(event.get('scope_removed', []))}")
    if event.get("rewards"):
        labels.append("Reward")
    return ", ".join(labels) or "Changed"


def _reward(amount: float, currency: str, *, compact: bool = False) -> str:
    sign = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    if compact and amount >= 1_000_000:
        number = f"{amount / 1_000_000:.1f}".rstrip("0").rstrip(".") + "m"
    elif compact and amount >= 10_000:
        number = f"{amount / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    else:
        number = f"{amount:,.0f}"
    return f"{sign}{number}" + (f" {currency}" if not sign else "")


class BugSprayFeed(QGroupBox):
    """Shows saved programs and changes; only `scan` uses a child process."""

    program_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Program radar", parent)
        self.setObjectName("BugSprayFeed")
        self._started = False
        self._last_scan: dict | None = None
        self._settings = config.load()
        self._scan = QProcess(self)
        self._scan.setWorkingDirectory(str(PROJECT))
        self._scan_stderr = ""
        self._scan.readyReadStandardOutput.connect(self._drain_scan_output)
        self._scan.readyReadStandardError.connect(self._read_scan_error)
        self._scan.finished.connect(self._scan_finished)
        self._scan.errorOccurred.connect(self._scan_error)
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        self.status = QLabel("Saved public programs")
        heading.addWidget(self.status, 1)
        self.scan_button = QPushButton("Scan now")
        self.scan_button.clicked.connect(self.scan_now)
        heading.addWidget(self.scan_button)
        layout.addLayout(heading)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter saved programs and changes…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.tabs = QTabWidget()
        self.changes = QTreeWidget()
        self.changes.setHeaderLabels(["When", "Change", "Program"])
        self.changes.setRootIsDecorated(False)
        self.changes.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.changes.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.changes.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.changes.currentItemChanged.connect(self._selected)
        self.tabs.addTab(self.changes, "Recent changes")
        self.programs = QTreeWidget()
        self.programs.setHeaderLabels(["Program", "Platform", "Reward", "Scope"])
        self.programs.setRootIsDecorated(False)
        self.programs.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.programs.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.programs.currentItemChanged.connect(self._selected)
        self.tabs.addTab(self.programs, "Programs")
        layout.addWidget(self.tabs)

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMaximumHeight(95)
        self.details.setPlaceholderText("Select a program to see its saved scope and reward details.")
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.open_button = QPushButton("Open program page")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_program)
        actions.addWidget(self.open_button)
        self.use_button = QPushButton("Use in report")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use_program)
        actions.addWidget(self.use_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.setMaximumHeight(390)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        if not self._started:
            self._started = True
            self._timer.start()
        QTimer.singleShot(0, self._maybe_scan)

    def refresh(self) -> None:
        try:
            self._settings = config.load()
            problems = self._settings.validate()
            if problems:
                raise ValueError("; ".join(problems))
            data = read_feed(self._settings)
        except Exception as exc:
            self.status.setText(f"Program feed unavailable: {exc}")
            return
        self._last_scan = data["last_scan"]
        self.changes.clear()
        for change in data["changes"]:
            program = change["program"]
            item = QTreeWidgetItem([
                change["scanned_at"][:16].replace("T", " "),
                _event_label(change), program["name"],
            ])
            item.setData(0, Qt.UserRole, (program["platform"], program["slug"]))
            self.changes.addTopLevelItem(item)
        self.programs.clear()
        for program in data["programs"]:
            reward = program["reward"]
            payout = _reward(*reward, compact=True) if reward else "—"
            item = QTreeWidgetItem([
                program["name"], program["platform"], payout,
                str(program["scope_count"]) if program["scope_count"] else "unpublished",
            ])
            item.setData(0, Qt.UserRole, (program["platform"], program["slug"]))
            self.programs.addTopLevelItem(item)
        self.tabs.setTabText(0, f"Recent changes ({len(data['changes'])})")
        self.tabs.setTabText(1, f"Programs ({len(data['programs'])})")
        self._filter(self.search.text())
        stamp = self._last_scan["scanned_at"][:16].replace("T", " ") if self._last_scan else "never"
        errors = (self._last_scan or {}).get("platforms", {})
        failed = [name for name, result in errors.items() if "error" in result]
        suffix = f" · errors: {', '.join(failed)}" if failed else ""
        self.status.setText(f"Last scan: {stamp} · {len(data['programs'])} watched programs{suffix}")

    def _filter(self, text: str) -> None:
        needle = text.casefold().strip()
        for tree in (self.changes, self.programs):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                haystack = " ".join(item.text(col) for col in range(tree.columnCount())).casefold()
                item.setHidden(bool(needle and needle not in haystack))

    def _selected(self, item, previous) -> None:
        if item is None:
            return
        platform, slug = item.data(0, Qt.UserRole)
        try:
            program = read_program(self._settings, platform, slug)
        except Exception as exc:
            self.details.setPlainText(f"Could not read saved program: {exc}")
            return
        if program is None:
            return
        self._selection = program
        scope = program.scope.in_scope
        lines = [f"{program.name} · {platform} · {'open' if program.active else 'paused'}"]
        lines.append("Rewards: " + (", ".join(
            f"{r.severity} up to {_reward(r.top(), r.currency)}"
            for r in program.rewards if r.top() is not None
        ) or "no amount published"))
        lines.append(f"In scope ({len(scope)}): " + (", ".join(scope[:2]) or "not public"))
        if len(scope) > 2:
            lines[-1] += f" … and {len(scope) - 2} more"
        if program.scope.out_of_scope:
            lines.append(f"Out of scope ({len(program.scope.out_of_scope)}): "
                         f"{', '.join(program.scope.out_of_scope[:2])}")
        lines.append("Re-read the live page before testing; saved scope is not authorization.")
        self.details.setPlainText("\n".join(lines))
        url = QUrl(program.url)
        self.open_button.setEnabled(url.scheme() == "https" and bool(url.host()))
        self.use_button.setEnabled(True)

    def open_program(self) -> None:
        program = getattr(self, "_selection", None)
        if program:
            url = QUrl(program.url)
            if url.scheme() == "https" and url.host():
                QDesktopServices.openUrl(url)

    def use_program(self) -> None:
        program = getattr(self, "_selection", None)
        if program:
            self.program_selected.emit(f"{program.platform.title()} — {program.name}")

    def _tick(self) -> None:
        try:
            if self._settings.db_path.exists():
                db = Store(self._settings.db_path, readonly=True)
                try:
                    latest = db.last_scan()
                finally:
                    db.close()
                if latest != self._last_scan:
                    self.refresh()
        except Exception as exc:
            self.status.setText(f"Could not check scan status: {exc}")
        self._maybe_scan()

    def _maybe_scan(self) -> None:
        if self._scan.state() != QProcess.NotRunning or not self._settings.enabled_platforms:
            return
        if self._last_scan:
            try:
                last = datetime.fromisoformat(self._last_scan["scanned_at"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if datetime.now(UTC) < last + timedelta(minutes=self._settings.poll_interval_minutes):
                    return
            except (KeyError, TypeError, ValueError):
                pass
        self.scan_now()

    def scan_now(self) -> None:
        if self._scan.state() != QProcess.NotRunning:
            return
        if not PYTHON.is_file():
            self.status.setText("Bug Spray Python environment is missing; see its README setup instructions.")
            return
        self.scan_button.setEnabled(False)
        self._scan_stderr = ""
        self.status.setText("Scanning public program directories in the background…")
        self._scan.start(str(PYTHON), [str(PROJECT / "main.py"), "scan", "--json"])

    def _drain_scan_output(self) -> None:
        self._scan.readAllStandardOutput()

    def _read_scan_error(self) -> None:
        chunk = bytes(self._scan.readAllStandardError()).decode("utf-8", errors="replace")
        self._scan_stderr = (self._scan_stderr + chunk)[-2000:]

    def _scan_finished(self, code: int, status) -> None:
        self._read_scan_error()
        self.scan_button.setEnabled(True)
        self.refresh()
        if code != 0:
            self.status.setText(
                f"Scan finished with errors. {self._scan_stderr.strip()[-250:] or 'See the scan summary above.'}"
            )

    def _scan_error(self, error) -> None:
        self.scan_button.setEnabled(True)
        self.status.setText(f"Could not start Bug Spray scan: {self._scan.errorString()}")

"""Saved public-program feed inside Sentinel's Bug Spray workspace."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QTabWidget, QTextBrowser,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from agents.bug_spray.bug_spray import config
from agents.bug_spray.bug_spray.feed import read_feed, read_program
from agents.bug_spray.bug_spray.models import Program
from agents.bug_spray.bug_spray.store import Store
from ui.widgets import MenuComboBox

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


def _span(tier) -> str:
    low, high = tier.min_amount, tier.max_amount
    if low is None or high is None:
        return f"up to {_reward(tier.top(), tier.currency)}" if low is None else f"from {_reward(low, tier.currency)}"
    if low == high:
        return _reward(high, tier.currency)
    return f"{_reward(low, tier.currency)} – {_reward(high, tier.currency)}"


def program_html(program: Program) -> str:
    """Everything saved about one program — the app's equivalent of `bugspray show`."""
    def items(values: list[str]) -> str:
        return "".join(f"<li>{escape(v)}</li>" for v in values)

    rewards = "".join(
        f"<tr><td>{escape(r.severity)}</td><td>{escape(_span(r))}</td></tr>" for r in program.rewards
    ) or "<tr><td colspan=2>No amount published</td></tr>"
    scope = program.scope
    in_scope = f"<ul>{items(scope.in_scope)}</ul>" if scope.in_scope else \
        "<p>Not public — this platform shows the scope only to logged-in researchers.</p>"
    out_of_scope = (f"<h4>Out of scope ({len(scope.out_of_scope)})</h4><ul>{items(scope.out_of_scope)}</ul>"
                    if scope.out_of_scope else "")
    return (
        f"<h3>{escape(program.name)}</h3>"
        f"<p>{escape(program.platform)}/{escape(program.slug)} · "
        f"{'open' if program.active else 'paused'} · updated {escape(program.last_updated or 'unknown')}</p>"
        + (f"<p>Tags: {escape(', '.join(program.tags))}</p>" if program.tags else "")
        + f"<h4>Rewards</h4><table cellpadding=3>{rewards}</table>"
        f"<h4>In scope ({len(scope.in_scope)})</h4>{in_scope}{out_of_scope}"
        "<p><b>Re-read the live program page before testing: a saved scope is not authorization.</b></p>"
    )


class ProgramDetailsDialog(QDialog):
    """Full saved scope and reward table for one program."""

    def __init__(self, program: Program, parent: QWidget | None = None):
        super().__init__(parent)
        self.program = program
        self.setWindowTitle(program.name)
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(False)
        self.body.setHtml(program_html(program))
        layout.addWidget(self.body)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        open_page = buttons.addButton("Open program page", QDialogButtonBox.ActionRole)
        url = QUrl(program.url)
        open_page.setEnabled(url.scheme() == "https" and bool(url.host()))
        open_page.clicked.connect(lambda: QDesktopServices.openUrl(url))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


class WatchlistDialog(QDialog):
    """Edit the platforms and watchlist filters in Bug Spray's config.json.

    The terminal (`bugspray`) reads the same file, so a change here applies there too.
    """

    def __init__(self, settings: config.Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Bug Spray watchlist")
        layout = QVBoxLayout(self)
        note = QLabel("Filters only change what is shown. Every program is still saved, so "
                      "loosening a filter later loses nothing. The terminal uses the same settings.")
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        platforms = QHBoxLayout()
        self.platform_boxes: dict[str, QCheckBox] = {}
        for name in config.ALL_PLATFORMS:
            box = QCheckBox(name)
            box.setChecked(name in settings.enabled_platforms)
            self.platform_boxes[name] = box
            platforms.addWidget(box)
        form.addRow("Platforms to scan", platforms)
        self.keywords = QLineEdit(", ".join(settings.watchlist_keywords))
        self.keywords.setPlaceholderText("e.g. api, graphql — matches name, tags or in-scope assets")
        form.addRow("Keywords", self.keywords)
        self.tags = QLineEdit(", ".join(settings.watchlist_tags))
        self.tags.setPlaceholderText("e.g. wildcard, smart_contract — shown in a program's details")
        form.addRow("Tags", self.tags)
        self.min_reward = QDoubleSpinBox()
        self.min_reward.setRange(0, 100_000_000)
        self.min_reward.setDecimals(0)
        self.min_reward.setSingleStep(500)
        self.min_reward.setPrefix("$ ")
        self.min_reward.setSpecialValueText("no minimum")
        self.min_reward.setValue(settings.min_reward_usd)
        form.addRow("Minimum top payout", self.min_reward)
        layout.addLayout(form)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def edited(self) -> config.Settings:
        return replace(
            self._settings,
            enabled_platforms=[n for n, box in self.platform_boxes.items() if box.isChecked()],
            watchlist_keywords=_split(self.keywords.text()),
            watchlist_tags=_split(self.tags.text()),
            min_reward_usd=float(self.min_reward.value()),
        )

    def save(self) -> None:
        settings = self.edited()
        problems = settings.validate()
        if problems:
            self.error.setText("; ".join(problems))
            self.error.show()
            return
        try:
            config.save(settings)
        except OSError as exc:
            self.error.setText(f"Could not save config.json: {exc}")
            self.error.show()
            return
        self.accept()


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
        self.scan_button = QToolButton()
        self.scan_button.setText("Scan now")
        self.scan_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.scan_button.clicked.connect(lambda: self.scan_now())
        scan_menu = QMenu(self.scan_button)
        self.full_scan_action = scan_menu.addAction("Full re-scan (re-fetch every program's details)")
        self.full_scan_action.triggered.connect(lambda: self.scan_now(full=True))
        self.scan_button.setMenu(scan_menu)
        heading.addWidget(self.scan_button)
        layout.addLayout(heading)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter saved programs and changes…")
        self.search.textChanged.connect(self._filter)
        filters.addWidget(self.search, 1)
        self.platform = MenuComboBox()
        self.platform.addItem("All platforms", None)
        for name in config.ALL_PLATFORMS:
            self.platform.addItem(name, name)
        self.platform.currentIndexChanged.connect(lambda _: self._filter(self.search.text()))
        filters.addWidget(self.platform)
        self.show_all = QCheckBox("Show all")
        self.show_all.setToolTip("Ignore the watchlist filters and show every saved program")
        self.show_all.toggled.connect(lambda _: self.refresh())
        filters.addWidget(self.show_all)
        self.watchlist_button = QPushButton("Watchlist…")
        self.watchlist_button.clicked.connect(self.edit_watchlist)
        filters.addWidget(self.watchlist_button)
        layout.addLayout(filters)

        self.tabs = QTabWidget()
        self.changes = QTreeWidget()
        self.changes.setHeaderLabels(["When", "Change", "Program"])
        self.changes.setRootIsDecorated(False)
        self.changes.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.changes.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.changes.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.changes.currentItemChanged.connect(self._selected)
        self.changes.itemDoubleClicked.connect(lambda *_: self.show_details())
        self.tabs.addTab(self.changes, "Recent changes")
        self.programs = QTreeWidget()
        self.programs.setHeaderLabels(["Program", "Platform", "Reward", "Scope"])
        self.programs.setRootIsDecorated(False)
        self.programs.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.programs.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.programs.currentItemChanged.connect(self._selected)
        self.programs.itemDoubleClicked.connect(lambda *_: self.show_details())
        self.tabs.addTab(self.programs, "Programs")
        layout.addWidget(self.tabs)

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMaximumHeight(95)
        self.details.setPlaceholderText("Select a program to see its saved scope and reward details.")
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.details_button = QPushButton("Full details…")
        self.details_button.setToolTip("Whole saved scope, reward ranges and tags (or double-click a row)")
        self.details_button.setEnabled(False)
        self.details_button.clicked.connect(self.show_details)
        actions.addWidget(self.details_button)
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
        self.setMaximumHeight(420)

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
            data = read_feed(self._settings, show_all=self.show_all.isChecked())
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
        shown = "saved programs, watchlist off" if self.show_all.isChecked() else "watched programs"
        self.status.setText(f"Last scan: {stamp} · {len(data['programs'])} {shown}{suffix}")

    def _filter(self, text: str) -> None:
        needle = text.casefold().strip()
        platform = self.platform.currentData()
        for tree in (self.changes, self.programs):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                haystack = " ".join(item.text(col) for col in range(tree.columnCount())).casefold()
                wrong_platform = platform is not None and item.data(0, Qt.UserRole)[0] != platform
                item.setHidden(wrong_platform or bool(needle and needle not in haystack))

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
        self.details_button.setEnabled(True)

    def open_program(self) -> None:
        program = getattr(self, "_selection", None)
        if program:
            url = QUrl(program.url)
            if url.scheme() == "https" and url.host():
                QDesktopServices.openUrl(url)

    def show_details(self) -> None:
        program = getattr(self, "_selection", None)
        if program:
            ProgramDetailsDialog(program, self).exec()

    def edit_watchlist(self) -> None:
        dialog = WatchlistDialog(config.load(), self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()

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

    def scan_now(self, full: bool = False) -> None:
        if self._scan.state() != QProcess.NotRunning:
            return
        if not PYTHON.is_file():
            self.status.setText("Bug Spray Python environment is missing; see its README setup instructions.")
            return
        self.scan_button.setEnabled(False)
        self._scan_stderr = ""
        self.status.setText(
            "Full re-scan in the background — every program's details (a few minutes)…" if full
            else "Scanning public program directories in the background…"
        )
        args = [str(PROJECT / "main.py"), "scan", "--json"] + (["--full"] if full else [])
        self._scan.start(str(PYTHON), args)

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

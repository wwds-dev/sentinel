"""Reusable layout widgets.

Moved verbatim out of main.py (see docs/refactor_plan.md, phase 1).

`FlowLayout` exists because a QHBoxLayout reports the sum of its children as its
minimum width, which pins an impossible minimum on a pane and makes Qt compress
controls past their own minimums until the labels are chopped.
"""
from PySide6.QtCore import Qt, QRect, QPoint, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLayout,
                               QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)


class MenuComboBox(QComboBox):
    """A combo box whose choices use Sentinel's compact cascading menus.

    Qt uses a platform-native popup for some combo boxes on macOS, which made
    selectors look unrelated to Chat's Options menu.  This class keeps the
    familiar closed combo-box control while rendering every popup as a QMenu.
    Provider and model selectors can expose real hierarchy; short, unrelated
    lists stay flat instead of gaining a ceremonial extra click.
    """

    FLAT = "flat"
    PROVIDER = "provider"
    MODEL = "model"
    COST_ROLE = int(Qt.UserRole) + 41

    def __init__(self, parent=None):
        super().__init__(parent)
        self._menu_mode = self.FLAT
        self._active_menu: QMenu | None = None
        self.setProperty("sentinelMenuCombo", True)

    def setMenuMode(self, mode: str) -> None:
        if mode not in {self.FLAT, self.PROVIDER, self.MODEL}:
            raise ValueError(f"Unsupported menu mode: {mode}")
        self._menu_mode = mode

    def menuMode(self) -> str:
        return self._menu_mode

    def _add_choice(self, menu: QMenu, index: int) -> None:
        action = menu.addAction(self.itemText(index))
        action.setEnabled(bool(self.model().flags(self.model().index(index, 0)) & Qt.ItemIsEnabled))

        # Carry the recommendation/warning metadata that used to be painted by
        # the combo's item delegate into the QMenu.  The current choice wins the
        # marker (green); a different recommendation remains red; hardware
        # warnings retain their muted dot and tooltip.
        if self.itemData(index, self.COST_ROLE):
            marker = QColor("#f0c040")
        else:
            marker = QColor("#3cff88") if index == self.currentIndex() else self.itemData(
                index, Qt.ForegroundRole
            )
        if isinstance(marker, QColor) and marker.isValid():
            pixmap = QPixmap(10, 10)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(marker)
            painter.drawEllipse(1, 1, 8, 8)
            painter.end()
            action.setIcon(QIcon(pixmap))

        item_font = self.itemData(index, Qt.FontRole)
        if isinstance(item_font, QFont):
            action.setFont(item_font)
        elif index == self.currentIndex():
            current_font = QFont(self.font())
            current_font.setWeight(QFont.DemiBold)
            action.setFont(current_font)
        tip = self.itemData(index, Qt.ToolTipRole)
        if tip:
            action.setToolTip(str(tip))
            action.setStatusTip(str(tip))
        action.triggered.connect(
            lambda _checked=False, selected=index: self.setCurrentIndex(selected)
        )

    def buildMenu(self) -> QMenu:
        """Build a fresh menu from the live combo model (also useful in tests)."""
        menu = QMenu(self)
        menu.setObjectName("SelectorMenu")
        menu.setMinimumWidth(max(220, self.width()))

        if self.count() == 0:
            empty = menu.addAction("No choices available")
            empty.setEnabled(False)
            return menu

        if self._menu_mode == self.PROVIDER:
            current = menu.addAction(f"Current · {self.currentText()}")
            current.setEnabled(False)
            menu.addSeparator()
            local = menu.addMenu("Local providers")
            cloud = menu.addMenu("Cloud providers")
            local.setMinimumWidth(menu.minimumWidth())
            cloud.setMinimumWidth(menu.minimumWidth())
            for index in range(self.count()):
                target = local if self.itemText(index).strip().lower() == "ollama" else cloud
                self._add_choice(target, index)
            if not local.actions():
                local.menuAction().setVisible(False)
            if not cloud.actions():
                cloud.menuAction().setVisible(False)
        elif self._menu_mode == self.MODEL:
            # Opening the model control already supplies all the context the
            # user needs. A redundant "Available models" submenu only added a
            # second click, so model choices are listed directly.
            menu.setMinimumWidth(max(280, menu.minimumWidth()))
            for index in range(self.count()):
                self._add_choice(menu, index)
        else:
            for index in range(self.count()):
                self._add_choice(menu, index)
        return menu

    def showPopup(self) -> None:
        if self._active_menu is not None:
            self._active_menu.close()
        menu = self.buildMenu()
        self._active_menu = menu

        def release_menu() -> None:
            if self._active_menu is menu:
                self._active_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(release_menu)
        menu.popup(self.mapToGlobal(self.rect().bottomLeft()))

    def hidePopup(self) -> None:
        if self._active_menu is not None:
            self._active_menu.close()
        super().hidePopup()


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new line when it runs out of width.

    A QHBoxLayout of buttons reports the sum of their widths as its minimum, so a
    long control row pins a hard minimum width on the whole pane. Below that the
    splitter compresses the buttons past their own minimums and the labels get
    chopped ("Auto Rout", "ecomme"). Wrapping instead keeps every control at its
    natural size and lets the pane shrink to the width of the widest single item.
    """

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    # ── QLayout plumbing ────────────────────────────────────────────────
    def addWidget(self, widget, stretch=0, alignment=None):
        """Drop-in for QBoxLayout.addWidget, which takes a stretch factor.

        Stretch and alignment have no meaning once items wrap, but accepting
        them means a QHBoxLayout can be swapped for this without touching the
        call sites.
        """
        super().addWidget(widget)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def _visible_items(self):
        """Skip hidden widgets, the way QBoxLayout does.

        A run bar toggles Run/Stop by hiding one of them; without this the
        hidden button still reserves its slot, leaving a blank gap and counting
        toward the wrap width.
        """
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            yield item

    def minimumSize(self):
        size = QSize()
        for item in self._visible_items():
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    # ── placement ───────────────────────────────────────────────────────
    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        right = rect.right() - margins.right()
        x, y = left, rect.y() + margins.top()
        line_height = 0
        space = self.spacing()

        for item in self._visible_items():
            hint = item.sizeHint()
            if x + hint.width() > right and line_height > 0:   # wrap
                x = left
                y += line_height + space
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + space
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class CollapsibleSection(QWidget):
    """Modern accordion-style section with header button and toggleable content."""

    HEADER_STYLE = """
        QPushButton#CollapsibleHeader {
            text-align: left;
            padding: 4px 10px;
            background-color: transparent;
            border: none;
            color: #707070;
            font-weight: bold;
            font-size: 10px;
            letter-spacing: 1.5px;
        }
        QPushButton#CollapsibleHeader:hover {
            color: #ffffff;
        }
        QPushButton#CollapsibleHeader:checked {
            color: #999999;
        }
    """

    def __init__(self, title: str, expanded: bool = True):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._expanded = expanded
        self._title = title

        self.header_btn = QPushButton()
        self.header_btn.setObjectName("CollapsibleHeader")
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(expanded)
        self.header_btn.setStyleSheet(self.HEADER_STYLE)
        self.header_btn.clicked.connect(self._toggle)
        layout.addWidget(self.header_btn)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 4, 0, 10)
        self.content_layout.setSpacing(3)
        layout.addWidget(self.content)

        self._update_header()
        self.content.setVisible(expanded)

    def addWidget(self, widget):
        self.content_layout.addWidget(widget)

    def _toggle(self):
        self._expanded = not self._expanded
        self.content.setVisible(self._expanded)
        self._update_header()

    def _update_header(self):
        arrow = "▾" if self._expanded else "▸"
        # QPushButton reads "&" as a mnemonic marker, which silently turned
        # "Finance & Business" into "FINANCE _BUSINESS". Double it to render a
        # literal ampersand.
        title = self._title.upper().replace("&", "&&")
        self.header_btn.setText(f"  {arrow}   {title}")
        self.header_btn.setChecked(self._expanded)


class ProgressiveSection(QWidget):
    """One quiet disclosure for optional or advanced workflow controls.

    Unlike the navigation accordion above, this is content: its summary stays
    visible, while details consume space only when the user asks for them.
    """

    def __init__(self, title: str, summary: str = "", *, expanded: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("ProgressiveSection")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.setProperty("expanded", expanded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QPushButton()
        self.header.setObjectName("ProgressiveHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.clicked.connect(self.setExpanded)
        outer.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("ProgressiveBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 7, 10, 10)
        self.body_layout.setSpacing(8)
        outer.addWidget(self.body)

        self._title = title
        self._summary = summary
        self.setExpanded(expanded)

    def addWidget(self, widget, stretch=0):
        self.body_layout.addWidget(widget, stretch)

    def addLayout(self, layout):
        self.body_layout.addLayout(layout)

    def setSummary(self, summary: str) -> None:
        self._summary = summary
        self._refresh_header()

    def setExpanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self.header.setChecked(expanded)
        self.body.setVisible(expanded)
        self.setProperty("expanded", expanded)
        self._refresh_header()
        self.style().unpolish(self)
        self.style().polish(self)

    def isExpanded(self) -> bool:
        return self.header.isChecked()

    def _refresh_header(self) -> None:
        arrow = "▾" if self.header.isChecked() else "▸"
        suffix = f"   {self._summary}" if self._summary else ""
        self.header.setText(f"{arrow}  {self._title}{suffix}")


class WorkspaceState(QWidget):
    """Compact inline feedback used instead of modal dialogs and blank panes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkspaceState")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.dot = QLabel("•")
        self.dot.setObjectName("WorkspaceStateDot")
        self.message = QLabel("")
        self.message.setObjectName("WorkspaceStateText")
        self.message.setWordWrap(True)
        row.addWidget(self.dot)
        row.addWidget(self.message, 1)
        self.setState("idle", "")

    def setState(self, state: str, message: str) -> None:
        self.setProperty("state", state)
        self.message.setText(message)
        self.setVisible(bool(message))
        for widget in (self, self.dot, self.message):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class Bar(QWidget):
    """A thin proportion bar. Painted rather than styled.

    A QProgressBar would inherit the global sheet, whose heavier treatment is
    intended for task progress rather than a compact status-rail proportion.
    """

    TRACK = QColor("#242424")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction = 0.0
        self._colour = QColor("#3cff88")
        self.setFixedHeight(4)
        self.setMinimumWidth(40)

    def set(self, fraction: float, colour: str) -> None:
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self._colour = QColor(colour)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        radius = self.height() / 2
        painter.setBrush(self.TRACK)
        painter.drawRoundedRect(self.rect(), radius, radius)
        filled = int(self.width() * self._fraction)
        if filled > 0:
            painter.setBrush(self._colour)
            painter.drawRoundedRect(QRect(0, 0, max(filled, self.height()),
                                          self.height()), radius, radius)


class Meter(QWidget):
    """caption · bar · value — for anything shaped "x of y".

    Replaces sentences like "Used: 11.3 GB · Free: 9.4 GB", which cannot be read
    at a glance. That is the only thing a status rail is for.
    """

    LEVEL_COLOURS = {
        "green": "#3cff88",
        "yellow": "#e3b341",
        "red": "#f85149",
        "muted": "#5a5a5a",
    }

    def __init__(self, caption: str, tip: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.caption = QLabel(caption)
        self.caption.setObjectName("MeterCaption")
        self.bar = Bar()
        self.value = QLabel("—")
        self.value.setObjectName("MeterValue")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # A long budget figure must not set the minimum width of the entire
        # inspector.  At the narrow (230 px) rail size Qt otherwise makes the
        # scroll area's content wider than its viewport and silently clips the
        # right edge because horizontal scrolling is intentionally disabled.
        self.value.setMinimumWidth(0)
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        row.addWidget(self.caption)
        row.addWidget(self.value, 1)
        outer.addLayout(row)
        outer.addWidget(self.bar)
        if tip:
            self.setToolTip(tip)

    def set(self, fraction: float, text: str, level: str = "green",
            tip: str = "") -> None:
        self.bar.set(fraction, self.LEVEL_COLOURS.get(level, "#3cff88"))
        self.value.setText(text)
        self.value.setToolTip(text)
        if tip:
            self.setToolTip(tip)

    def set_unavailable(self, text: str = "n/a") -> None:
        self.bar.set(0.0, self.LEVEL_COLOURS["muted"])
        self.value.setText(text)


class SectionCard(QFrame):
    """One parsed section of an agent's answer.

    Title, body, and a copy button that appears because a section is the unit
    people actually want on the clipboard — a dork list, a summary — rather
    than the whole transcript.
    """

    def __init__(self, title: str, body: str, mono: bool = False,
                 badge: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("SectionCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        head.addWidget(heading)
        head.addStretch()
        if badge:
            chip = QLabel(badge.upper())
            chip.setObjectName("SectionBadge")
            head.addWidget(chip)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("SectionCopy")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy)
        head.addWidget(self.copy_btn)
        lay.addLayout(head)

        self._body = body
        text = QLabel(body)
        text.setObjectName("SectionMono" if mono else "SectionBody")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(text)

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._body)
        self.copy_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("Copy"))


class SectionView(QWidget):
    """Agent output as the sections it was already parsed into.

    Twelve `_parse_*_sections` methods existed before this and every one of them
    poured its result into a flat text box, throwing the structure away. This
    renders that same dict as cards and keeps the raw response one click away
    rather than making it the only view.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget()
        self._column = QVBoxLayout(self._holder)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(12)
        self._column.addStretch()
        self._scroll.setWidget(self._holder)
        outer.addWidget(self._scroll, 1)

        self._raw = ""
        self._raw_btn = QPushButton("▸  Raw response")
        self._raw_btn.setObjectName("RawToggle")
        self._raw_btn.setCursor(Qt.PointingHandCursor)
        self._raw_btn.clicked.connect(self._toggle_raw)
        self._raw_btn.setVisible(False)
        outer.addWidget(self._raw_btn)

        self._raw_box = QLabel()
        self._raw_box.setObjectName("SectionMono")
        self._raw_box.setWordWrap(True)
        self._raw_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._raw_box.setVisible(False)
        outer.addWidget(self._raw_box)

        self._placeholder = QLabel("No results yet.")
        self._placeholder.setObjectName("SectionEmpty")
        self._column.insertWidget(0, self._placeholder)

    def clear(self):
        while self._column.count() > 1:            # keep the trailing stretch
            item = self._column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._raw = ""
        self._raw_btn.setVisible(False)
        self._raw_box.setVisible(False)
        self._placeholder = QLabel("No results yet.")
        self._placeholder.setObjectName("SectionEmpty")
        self._column.insertWidget(0, self._placeholder)

    def show_sections(self, sections, raw: str = ""):
        """`sections` is an ordered sequence of (title, body[, mono])."""
        while self._column.count() > 1:
            item = self._column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # `deleteLater()` alone leaves the old placeholder/card painted
                # until Qt processes deferred deletes, so it can show through a
                # freshly populated result view for one frame (or in a grab()).
                widget.hide()
                widget.deleteLater()

        shown = 0
        for entry in sections:
            title, body = entry[0], entry[1]
            mono = entry[2] if len(entry) > 2 else False
            if not (body or "").strip():
                continue                            # an empty section is not a card
            self._column.insertWidget(shown, SectionCard(title, body.strip(), mono))
            shown += 1

        if shown == 0 and raw.strip():
            self._column.insertWidget(0, SectionCard("Response", raw.strip()))
            shown = 1

        self._raw = raw or ""
        words = len(self._raw.split())
        self._raw_btn.setText(f"▸  Raw response · {words:,} words")
        self._raw_btn.setVisible(bool(self._raw.strip()) and shown > 0)
        self._raw_box.setVisible(False)
        self._raw_box.setText(self._raw)

    def _toggle_raw(self):
        showing = not self._raw_box.isVisible()
        self._raw_box.setVisible(showing)
        arrow = "▾" if showing else "▸"
        words = len(self._raw.split())
        self._raw_btn.setText(f"{arrow}  Raw response · {words:,} words")


class KeyValue(QWidget):
    """A label on the left, its value right-aligned on the right.

    A status rail is scanned, not read. Sentences like "Requests Today: 0 |
    Session: 0" force you to parse punctuation to find the number; a column of
    right-aligned values lets the eye run straight down them.
    """

    def __init__(self, key: str, value: str = "—", tip: str = "", parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.key = QLabel(key)
        self.key.setObjectName("KVKey")
        self.value = QLabel(value)
        self.value.setObjectName("KVValue")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setMinimumWidth(0)
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.value.setToolTip(value)

        row.addWidget(self.key)
        row.addWidget(self.value, 1)
        if tip:
            self.setToolTip(tip)

    def set(self, value: str, tip: str = "") -> None:
        self.value.setText(value)
        self.value.setToolTip(value)
        if tip:
            self.setToolTip(tip)

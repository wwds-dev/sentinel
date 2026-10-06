"""Sentinel's menu bar item.

macOS does not deliver a plain click to a status item that owns a menu — the
menu opens instead — so there is no click-to-open gesture to write, and "Open
Sentinel" is simply the first entry.

The menu earns its place with the line above it. The question you have when the
window is behind something else is whether the thing is working and what it has
spent, and that is what the line answers. It is rebuilt when the menu opens
rather than on a timer: a status item that wakes a running app once a second to
be told nothing has changed is a cost with no reader.

Closing the window still quits Sentinel. The icon is there while Sentinel is
open, not instead of it — a security tool that keeps running invisibly after its
window is gone is the wrong default, and nothing here hides to the menu bar.

**macOS 27 aborts when this menu opens unless `ui/appkit_guard.py` is installed
first.** That is not a detail of this file; it is the reason the guard was
ported into this project at all. See its docstring.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

APP_NAME = "Sentinel"


def available() -> bool:
    """Whether this desktop has somewhere to put a status item."""
    return QSystemTrayIcon.isSystemTrayAvailable()


def glyph(resource_dir: Path | str) -> QIcon:
    """The menu bar mark, as a template image.

    `setIsMask` is what makes macOS recolour the glyph for a light or dark menu
    bar and invert it while the menu is open; without it the artwork stays black
    and disappears into a dark menu bar. The art is black-on-transparent for the
    same reason — see `scripts/make_icon.py`. Qt picks up `tray@2x.png` from
    beside the file on its own for Retina.
    """
    icon = QIcon(str(Path(resource_dir) / "assets" / "tray.png"))
    icon.setIsMask(True)
    return icon


class Tray(QObject):
    """The status item and its menu. `status` is called for the first line."""

    open_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        resource_dir: Path | str,
        status: Callable[[], str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._status = status

        self.icon = QSystemTrayIcon(glyph(resource_dir), self)
        self.icon.setToolTip(APP_NAME)

        menu = QMenu()
        self.status_action = QAction("", menu)
        self.status_action.setEnabled(False)   # a readout, not a command
        menu.addAction(self.status_action)

        menu.addSeparator()
        open_action = QAction(f"Open {APP_NAME}", menu)
        open_action.triggered.connect(self.open_requested)
        menu.addAction(open_action)

        menu.addSeparator()
        quit_action = QAction(f"Quit {APP_NAME}", menu)
        quit_action.triggered.connect(self.quit_requested)
        menu.addAction(quit_action)

        menu.aboutToShow.connect(self.refresh)

        # Held on the instance: a QMenu that only the tray icon references is
        # garbage collected out from under it, and the menu comes up empty.
        self._menu = menu
        self.icon.setContextMenu(menu)
        self.refresh()

    def refresh(self) -> None:
        """Rewrite the status line from the application's own state.

        A status line that raises on the way to the menu bar would take the menu
        with it, and the menu is the way to quit — so a failure here says so and
        leaves the rest of the menu working.
        """
        try:
            text = self._status()
        except Exception:
            text = "Status unavailable"
        self.status_action.setText(text)

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

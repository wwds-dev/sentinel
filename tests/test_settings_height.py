"""The Settings dialog must fit on a laptop screen, however long its lists get.

A QTabWidget is as tall as its tallest tab. The Pricing tab grows a row of
text fields per priced model, and once that table outgrew the screen the whole
dialog did too: the OSINT Keys list then had room for every row, so its own
scroll area never scrolled and the bottom of the list, and Save All, sat
below the screen edge. Every tab now scrolls on its own.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Shorter than a 13" MacBook Air's usable height, so a regression shows here.
LAPTOP_HEIGHT = 700


@pytest.fixture(scope="module")
def win():
    from PySide6.QtWidgets import QApplication
    import main

    QApplication.instance() or QApplication([])
    yield main.GodAI()


@pytest.fixture
def settings(win, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from ui import dialogs

    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    dialogs.show_settings(win)
    dialog = opened[0]
    yield dialog
    dialog.reject()


def test_pricing_table_is_long_enough_to_matter():
    """Guard the guard: with a short table the height test proves nothing."""
    from services.database import get_connection

    with get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    assert rows >= 20


def test_dialog_fits_a_laptop_screen(settings):
    assert settings.minimumSizeHint().height() <= LAPTOP_HEIGHT


def test_every_tab_scrolls(settings):
    from PySide6.QtWidgets import QScrollArea, QTabWidget

    tabs = settings.findChild(QTabWidget)
    for index in range(tabs.count()):
        page = tabs.widget(index)
        assert isinstance(page, QScrollArea) or page.findChild(QScrollArea), \
            f"{tabs.tabText(index)} has no scroll area"
        assert page.minimumSizeHint().height() <= LAPTOP_HEIGHT, \
            f"{tabs.tabText(index)} forces the dialog taller than the screen"

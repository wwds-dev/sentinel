"""The two themes, and the line between theme colour and semantic colour.

The red theme is derived from the green sheet by turning its hue, so the thing
worth guarding is not the arithmetic — it is the band. Widen it and a danger
button turns green; narrow it and half the chrome stops following the theme.
"""

import colorsys
import re
from pathlib import Path

import pytest

from ui import theme
from ui.style import _GREEN_STYLESHEET, global_stylesheet

ROOT = Path(__file__).resolve().parent.parent

ACCENT_GREEN = "#3cff88"
PHOSPHOR_GREEN = "#00ff41"

#: What the authored pair turns into under each of the other themes.
DERIVED = {
    theme.RED: ("#ff473c", "#ff0014"),
    theme.BLUE: ("#3cffff", "#00ffdd"),
}
ACCENT_RED, PHOSPHOR_RED = DERIVED[theme.RED]

# Colour that means something regardless of the theme, and must survive both.
SEMANTIC = {
    "#f85149": "danger",
    "#ff5555": "danger, muted",
    "#f0c040": "costs money",
    "#e3b341": "warning",
    "#4db8ff": "informational",
    "#ffffff": "plain white",
    "#5a5a5a": "true grey",
}


def _declarations() -> str:
    """The sheet with its comments stripped.

    Comments name the derived red values so the reasoning is readable at the
    rule; only what Qt actually paints should be scanned for colour.
    """
    return re.sub(r"/\*.*?\*/", "", _GREEN_STYLESHEET, flags=re.S)


def _hue(colour: str) -> float:
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360


# ── The sheet is authored once, in green ──────────────────────────────

def test_green_theme_is_the_sheet_unchanged():
    """Green must be the identity, or the authored sheet is not the truth."""
    assert global_stylesheet(theme.GREEN) == _GREEN_STYLESHEET


def test_sheet_carries_no_derived_literals():
    """A derived colour hardcoded here would not turn, and would leak into green."""
    derived = {value for pair in DERIVED.values() for value in pair}
    for literal in derived | {"#ff2d2d", "#2c1a18"}:
        assert literal not in _declarations()


@pytest.mark.parametrize("name", sorted(DERIVED))
def test_theme_replaces_every_accent(name):
    sheet = global_stylesheet(name)
    expected_accent, expected_phosphor = DERIVED[name]
    assert ACCENT_GREEN not in sheet
    assert expected_accent in sheet
    assert PHOSPHOR_GREEN not in sheet
    assert expected_phosphor in sheet


# ── The band ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("colour,meaning", sorted(SEMANTIC.items()))
def test_semantic_colour_survives_both_themes(colour, meaning):
    for name in theme.THEMES:
        assert theme.recolour(colour, name) == colour, meaning


def test_every_colour_in_the_sheet_is_theme_colour():
    """Nothing in the sheet should sit outside the band without a reason.

    A new colour that falls outside it silently stops following the theme —
    which is right for a danger red and wrong for a new shade of chrome. If
    this fails, decide which one you added and either bring it into the band's
    hue or list it in SEMANTIC above.
    """
    strays = {}
    for match in re.finditer(r"#[0-9a-fA-F]{6}\b", _declarations()):
        colour = match.group(0).lower()
        if colour in SEMANTIC or theme.recolour(colour, theme.RED) != colour:
            continue
        strays[colour] = round(_hue(colour), 1)
    assert not strays, f"outside the theme band: {strays}"


def test_every_theme_keeps_the_accent_distinct_from_the_semantic_blue():
    """Cyan is the closest the themes come to a semantic colour: 24° away.

    Close it further and an informational badge stops being distinguishable
    from a focus ring.
    """
    for name in theme.THEMES:
        gap = abs(_hue(theme.accent(name)) - _hue("#4db8ff"))
        assert gap > 20, f"{name}: accent is {gap:.1f}° from the informational blue"


def test_band_clears_the_nearest_semantic_hues():
    low, high = theme._BAND
    assert _hue("#f0c040") < low, "amber must stay out of the band"
    assert _hue("#4db8ff") > high, "the informational blue must stay out"
    assert low < _hue(ACCENT_GREEN) < high
    assert low < _hue(PHOSPHOR_GREEN) < high


# ── Mechanics ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    (theme.RED, "rgba(255, 71, 60, 0.25)"),
    (theme.BLUE, "rgba(60, 255, 255, 0.25)"),
])
def test_rgba_keeps_its_alpha(name, expected):
    assert theme.recolour("rgba(60, 255, 136, 0.25)", name) == expected


def test_accent_and_phosphor_stay_apart_in_both_themes():
    """They make different claims, so they must not collapse to one colour."""
    for name in theme.THEMES:
        assert theme.recolour(ACCENT_GREEN, name) != \
            theme.recolour(PHOSPHOR_GREEN, name)


def test_accent_helper_matches_the_sheet():
    for name in theme.THEMES:
        assert theme.accent(name) in global_stylesheet(name)


# ── Persistence ───────────────────────────────────────────────────────

def test_saved_theme_round_trips():
    try:
        theme.set_current(theme.RED)
        theme.forget()
        assert theme.current() == theme.RED
    finally:
        theme.set_current(theme.GREEN)


def test_unknown_theme_is_refused():
    with pytest.raises(ValueError):
        theme.set_current("chartreuse")


def test_current_falls_back_to_green(monkeypatch):
    """A junk value in the database must not leave the app unstyled."""
    theme.forget()
    monkeypatch.setattr("services.database.get_setting", lambda *a, **k: "chartreuse")
    assert theme.current() == theme.GREEN
    theme.forget()


# ── The live switch ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def win():
    """One offscreen window. Modal dialogs are stubbed — nothing can click them."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    import main

    QApplication.instance() or QApplication([])
    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        yield main.GodAI()
    finally:
        QMessageBox.warning, QMessageBox.question = saved
        theme.set_current(theme.GREEN)


def test_switching_theme_repaints_the_inline_sheets(win):
    """The sheets that predate ui/style.py must not be left behind.

    They are applied once at build time, so without the registry in
    `themed_sheet` the rails and the agent rows keep the old accent until the
    next launch — the one failure of this feature a screenshot would show and
    a unit test would miss.
    """
    assert win._inline_sheets, "nothing registered; themed_sheet went unused"

    for name, (expected_accent, _) in DERIVED.items():
        theme.set_current(name)
        win.apply_global_style()
        for widget, _ in win._inline_sheets:
            assert ACCENT_GREEN not in widget.styleSheet()
            assert expected_accent in widget.styleSheet()
        assert ACCENT_GREEN not in win.styleSheet()

    theme.set_current(theme.GREEN)
    win.apply_global_style()
    for widget, _ in win._inline_sheets:
        assert ACCENT_GREEN in widget.styleSheet()
    assert ACCENT_RED not in win.styleSheet()


def test_settings_picker_previews_live_and_cancel_puts_it_back(win, monkeypatch):
    """Choosing a theme repaints at once; Cancel is a real undo.

    The preview writes the setting straight away so the window can repaint, so
    the restore on `rejected` is the only thing standing between a glance at
    the other theme and being stuck in it.
    """
    from PySide6.QtWidgets import QComboBox, QDialog
    from ui import dialogs

    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)

    theme.set_current(theme.GREEN)
    win.apply_global_style()
    dialogs.show_settings(win)
    dialog = opened[0]

    picker = [box for box in dialog.findChildren(QComboBox)
              if [box.itemData(i) for i in range(box.count())] == list(theme.THEMES)]
    assert len(picker) == 1, "the theme picker is not in the Settings dialog"
    picker = picker[0]
    assert picker.currentData() == theme.GREEN

    picker.setCurrentIndex(list(theme.THEMES).index(theme.RED))
    assert theme.current() == theme.RED
    assert ACCENT_RED in win.styleSheet()

    dialog.reject()
    assert theme.current() == theme.GREEN
    assert ACCENT_GREEN in win.styleSheet()

# ── The dots ──────────────────────────────────────────────────────────

def _press(dots, index):
    """A real left-click on the centre of dot `index`."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    where = QPointF(dots._centre(index), dots.height() / 2)
    dots.mousePressEvent(QMouseEvent(
        QEvent.MouseButtonPress, where, dots.mapToGlobal(where),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_the_header_carries_one_dot_per_theme(win):
    dots = win.theme_dots
    assert dots.isVisible() or dots.parentWidget() is not None
    # Every dot has to be reachable: the hit test walks slots, so a dot the
    # geometry does not account for would silently never be clickable.
    assert {dots._at(dots._centre(i)) for i in range(len(theme.THEMES))} == \
        set(range(len(theme.THEMES)))


def test_clicking_a_dot_wears_that_theme(win):
    """The click has to reach the window, not just the setting."""
    try:
        for index, name in enumerate(theme.THEMES):
            _press(win.theme_dots, index)
            assert theme.current() == name
            assert theme.accent(name) in win.styleSheet()
    finally:
        theme.set_current(theme.GREEN)
        win.apply_global_style()


def test_clicking_the_current_dot_changes_nothing(win):
    """No repaint, no write — a click on what you already have is a no-op."""
    theme.set_current(theme.GREEN)
    win.apply_global_style()
    before = win.styleSheet()
    _press(win.theme_dots, list(theme.THEMES).index(theme.GREEN))
    assert theme.current() == theme.GREEN
    assert win.styleSheet() == before


def test_arrow_keys_step_through_the_themes(win):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    try:
        theme.set_current(theme.GREEN)
        dots = win.theme_dots
        for key, step in ((Qt.Key_Right, 1), (Qt.Key_Left, -1)):
            theme.set_current(theme.GREEN)
            expected = theme.THEMES[step % len(theme.THEMES)]
            dots.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
            assert theme.current() == expected, key
    finally:
        theme.set_current(theme.GREEN)
        win.apply_global_style()


def test_selector_menu_shows_action_tooltips():
    """D7: QMenu hides action tooltips unless setToolTipsVisible(True); the
    BEST FIT reason and the 'costs money' text on cloud entries live there."""
    from PySide6.QtWidgets import QApplication
    from ui.widgets import SelectorMenu
    QApplication.instance() or QApplication([])
    menu = SelectorMenu()
    assert menu.toolTipsVisible() is True

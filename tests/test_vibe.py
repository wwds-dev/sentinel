"""The caret and the backdrop.

Two things here are easy to get wrong in a way no screenshot would catch: the
backdrop outstaying the empty transcript, and the caret leaving Qt's own cursor
switched on underneath it, so you type against two carets at once.
"""

import os

import pytest

from ui import theme


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def edit(app):
    from PySide6.QtWidgets import QTextEdit
    from ui import vibe

    box = QTextEdit()
    box.resize(360, 90)
    box.setStyleSheet("QTextEdit { font-family: Menlo, monospace; font-size: 13px }")
    caret = vibe.install_caret(box)
    box.show()
    yield box, caret
    box.deleteLater()


# ── Cadence ───────────────────────────────────────────────────────────

def test_green_is_a_terminal_square_wave():
    from ui import vibe

    assert vibe.caret_alpha(theme.GREEN, 0) == 1.0
    assert vibe.caret_alpha(theme.GREEN, 610) == 1.0
    assert vibe.caret_alpha(theme.GREEN, 630) == 0.0
    assert vibe.caret_alpha(theme.GREEN, 1210) == 1.0, "the cycle must repeat"


def test_red_stutters_inside_its_dark_half():
    """The stutter is the whole point of red's cadence — a bad line, not a clock."""
    from ui import vibe

    assert vibe.caret_alpha(theme.RED, 650) == 0.0
    assert vibe.caret_alpha(theme.RED, 720) == 1.0, "first blip"
    assert vibe.caret_alpha(theme.RED, 800) == 0.0
    assert vibe.caret_alpha(theme.RED, 900) == 1.0, "second blip"
    assert vibe.caret_alpha(theme.RED, 1100) == 0.0


def test_blue_breathes_rather_than_switching():
    """Neon glows down and back up; it never reaches full dark."""
    from ui import vibe

    series = [vibe.caret_alpha(theme.BLUE, ms) for ms in range(0, 1700, 50)]
    assert min(series) > 0.25, "blue must not blink out"
    assert max(series) > 0.95
    steps = [abs(b - a) for a, b in zip(series, series[1:])]
    assert max(steps) < 0.1, "a hard edge anywhere means it is switching, not breathing"


def test_the_three_cadences_are_actually_different():
    from ui import vibe

    shapes = {
        name: tuple(round(vibe.caret_alpha(name, ms), 2) for ms in range(0, 1700, 50))
        for name in theme.THEMES
    }
    assert len(set(shapes.values())) == len(theme.THEMES)


# ── The caret itself ──────────────────────────────────────────────────

def test_qt_own_caret_is_switched_off(edit):
    """Otherwise you compose against two carets at once.

    This is the reason the underscore is painted rather than styled: Qt gives
    a style sheet no way at the cursor, only `setCursorWidth`.
    """
    box, _ = edit
    assert box.cursorWidth() == 0


def test_the_caret_follows_focus(edit, app):
    box, caret = edit
    box.setFocus()
    app.processEvents()
    assert caret.isVisible(), "nothing to type against"
    box.clearFocus()
    app.processEvents()
    assert not caret.isVisible(), "a caret in an unfocused field points at nothing"


def test_the_caret_sits_under_the_line_not_over_it(edit, app):
    """An underscore, which is the whole reason it replaced the block."""
    box, caret = edit
    box.setPlainText("nmap -sV 10.0.0.0/24")
    box.setFocus()
    app.processEvents()
    assert caret.height() == caret.THICKNESS <= 2
    assert caret.width() >= 6
    assert caret.geometry().bottom() >= box.cursorRect().bottom() - 1


def test_the_caret_moves_with_the_cursor(edit, app):
    from PySide6.QtGui import QTextCursor

    box, caret = edit
    box.setPlainText("nmap -sV 10.0.0.0/24")
    box.setFocus()
    app.processEvents()
    at_start = caret.geometry().left()

    cursor = box.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    box.setTextCursor(cursor)
    app.processEvents()
    assert caret.geometry().left() > at_start


# ── The backdrop ──────────────────────────────────────────────────────

@pytest.fixture()
def backdrop(app):
    from PySide6.QtWidgets import QTextEdit
    from ui import vibe

    host = QTextEdit()
    host.resize(420, 240)
    back = vibe.install_backdrop(
        host.viewport(),
        visible_while=lambda: host.document().isEmpty(),
        heading="Nothing run yet",
        subcopy="Conversation messages and generated results will appear here.",
    )
    host.show()
    app.processEvents()
    yield host, back
    host.deleteLater()


def test_the_backdrop_stops_when_there_is_something_to_read(backdrop, app):
    """The rule the whole feature rests on: texture only behind an empty pane."""
    host, back = backdrop
    back._tick()
    assert back.isVisible()

    host.setPlainText("[*] 3 hosts up, 0 findings")
    back._tick()
    assert not back.isVisible(), "a texture behind text you are reading is interference"

    host.clear()
    back._tick()
    assert back.isVisible()


@pytest.mark.parametrize("name", theme.THEMES)
def test_every_theme_paints_a_texture(name, backdrop, app):
    """Each theme must draw something, and not all the same something."""
    from PySide6.QtGui import QColor

    host, back = backdrop
    theme.set_current(name)
    try:
        for _ in range(4):
            back._tick()
            app.processEvents()
        image = host.grab().toImage()
        ground = QColor("#151816").rgb()
        lit = sum(1 for y in range(0, image.height(), 2)
                  for x in range(0, image.width(), 2)
                  if image.pixel(x, y) != ground)
        assert lit > 200, f"{name} drew nothing"
    finally:
        theme.set_current(theme.GREEN)


def test_the_backdrop_is_faint():
    """It is atmosphere. Past about a tenth it stops being that."""
    from ui import vibe

    assert 0 < vibe.BACKDROP_ALPHA <= 0.10


def test_the_backdrop_takes_no_mouse_events(backdrop):
    from PySide6.QtCore import Qt

    _, back = backdrop
    assert back.testAttribute(Qt.WA_TransparentForMouseEvents)


# ── The decode, and the brackets ──────────────────────────────────────

def test_a_heading_settles_left_to_right_and_lands_on_the_target():
    from ui import vibe

    target = "Nothing run yet"
    assert vibe.decode(target, 0) != target
    assert len(vibe.decode(target, 0)) == len(target), "the line must not reflow"
    assert vibe.decode(target, vibe.DECODE_MS) == target
    assert vibe.decode(target, vibe.DECODE_MS * 3) == target, "it settles and stays"

    # Each frame has at least as much of the real string as the one before it.
    def settled(frame):
        return sum(1 for a, b in zip(frame, target) if a == b)

    frames = [vibe.decode(target, ms) for ms in range(0, int(vibe.DECODE_MS) + 1, 100)]
    counts = [settled(frame) for frame in frames]
    assert counts == sorted(counts), "a decode must not go backwards"


def test_spaces_never_churn():
    """Scrambling the gaps turns a heading into a smear instead of a word."""
    from ui import vibe

    target = "Nothing run yet"
    for ms in range(0, int(vibe.DECODE_MS), 50):
        frame = vibe.decode(target, ms)
        assert [i for i, c in enumerate(frame) if c == " "] == \
            [i for i, c in enumerate(target) if c == " "]


def test_only_red_decodes_its_heading(backdrop, app):
    """Green and blue show the heading outright — the decode is red's detail."""
    _, back = backdrop
    try:
        for name in (theme.GREEN, theme.BLUE):
            theme.set_current(name)
            back._revealed = __import__("time").monotonic()
            assert back.heading_now() == back._heading, name

        theme.set_current(theme.RED)
        back._revealed = __import__("time").monotonic()
        assert back.heading_now() != back._heading
    finally:
        theme.set_current(theme.GREEN)


def test_the_heading_decodes_again_each_time_the_pane_empties(backdrop, app):
    import time as _time

    host, back = backdrop
    theme.set_current(theme.RED)
    try:
        back._tick()
        back._revealed = _time.monotonic() - 5      # long settled
        assert back.heading_now() == back._heading

        host.setPlainText("[*] 3 hosts up")
        back._tick()
        host.clear()
        back._tick()
        assert back.heading_now() != back._heading, "clearing is a fresh reveal"
    finally:
        theme.set_current(theme.GREEN)


@pytest.fixture()
def brackets(app):
    from PySide6.QtWidgets import QTextEdit
    from ui import vibe

    box = QTextEdit()
    box.resize(400, 110)
    marks = vibe.install_brackets(box)
    box.show()
    yield box, marks
    box.deleteLater()


def test_brackets_belong_to_blue_alone(brackets, app):
    box, marks = brackets
    box.setFocus()
    app.processEvents()
    try:
        for name in theme.THEMES:
            theme.set_current(name)
            marks.refresh()
            assert marks.isVisible() == (name == theme.BLUE), name
    finally:
        theme.set_current(theme.GREEN)


def test_brackets_follow_focus(brackets, app):
    box, marks = brackets
    theme.set_current(theme.BLUE)
    try:
        box.setFocus()
        app.processEvents()
        assert marks.isVisible()
        box.clearFocus()
        app.processEvents()
        assert not marks.isVisible(), "they mark what has focus, not what exists"
    finally:
        theme.set_current(theme.GREEN)


def test_brackets_stay_inside_the_field(brackets, app):
    """Drawn in the viewport so no layout can clip them."""
    box, marks = brackets
    theme.set_current(theme.BLUE)
    try:
        box.setFocus()
        app.processEvents()
        assert marks.geometry() == box.viewport().rect()
        assert marks.ARM * 2 < min(marks.width(), marks.height()), "corners would meet"
    finally:
        theme.set_current(theme.GREEN)

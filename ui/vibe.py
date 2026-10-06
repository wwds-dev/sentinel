"""The per-theme vibe: a caret that blinks, and a backdrop that moves.

Two rules keep this to a size the app can carry.

**The backdrop lives only in the empty transcript.** The moment there is
something to read it is gone. A texture behind text you are reading is not
atmosphere, it is interference — and an app that is about itself while you are
working is the failure mode this whole feature is one step away from.

**The caret is the same glyph in every theme.** An underscore, always; only its
cadence changes. A block caret was the first try and it was wrong: half of its
cycle is a hole punched in your own sentence, which reads as a blinking blank
space. An underscore sits under the line instead of on top of it, so nothing
you typed disappears while you are looking at it.

Why the caret is painted here at all: Qt draws a text caret itself and offers
no way to style one. A style sheet reaches the text, the selection and the
border, never the cursor. What Qt does offer is ``QTextEdit.setCursorWidth(0)``,
which removes it — so the underscore is drawn by a transparent child of the
viewport instead. ``QLineEdit`` has no such switch and no way to suppress its
bar, so single-line fields keep Qt's native caret; the underscore is for the
surfaces you actually compose in.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui import theme

#: The authored phosphor; ui.theme turns it with everything else.
PHOSPHOR = "#00ff41"

#: How far the backdrop is allowed to come up out of the background.
BACKDROP_ALPHA = 0.07


# ── The caret ─────────────────────────────────────────────────────────

def caret_alpha(name: str, elapsed_ms: float) -> float:
    """How bright the underscore is, this many milliseconds in.

    One function per theme, and the only place they differ. Green keeps a
    terminal's steady square wave; red stutters like a bad line; blue breathes,
    because neon does not switch, it glows down and back up.
    """
    if name == theme.RED:
        phase = elapsed_ms % 1500
        lit = phase < 620 or 700 <= phase < 755 or 880 <= phase < 960
        return 1.0 if lit else 0.0
    if name == theme.BLUE:
        turn = (elapsed_ms % 1700) / 1700
        return 0.3 + 0.7 * (0.5 + 0.5 * math.cos(2 * math.pi * turn))
    return 1.0 if (elapsed_ms % 1200) < 620 else 0.0


class UnderscoreCaret(QWidget):
    """A blinking underscore standing in for a QTextEdit's own caret.

    Install with `install_caret`. Lives as a child of the edit's viewport, so
    it paints over the text rather than under it, and takes no mouse events.
    """

    THICKNESS = 2
    TICK_MS = 60

    def __init__(self, edit, split: bool = True):
        super().__init__(edit.viewport())
        self._edit = edit
        self._split = split
        self._started = time.monotonic()
        self._alpha = 0.0

        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

        edit.setCursorWidth(0)          # Qt's own caret steps aside
        edit.installEventFilter(self)
        edit.cursorPositionChanged.connect(self._reposition)
        edit.textChanged.connect(self._reposition)
        edit.verticalScrollBar().valueChanged.connect(self._reposition)
        edit.horizontalScrollBar().valueChanged.connect(self._reposition)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

        if edit.hasFocus():
            self._start()

    # ── lifecycle ─────────────────────────────────────────────────────
    def _start(self) -> None:
        self._started = time.monotonic()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start()

    def _stop(self) -> None:
        self._timer.stop()
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self._edit:
            if event.type() == QEvent.FocusIn:
                self._start()
            elif event.type() == QEvent.FocusOut:
                self._stop()
            elif event.type() in (QEvent.Resize, QEvent.Show):
                self._reposition()
        return False

    # ── placement ─────────────────────────────────────────────────────
    def _reposition(self) -> None:
        if not self._edit.hasFocus():
            return
        rect = self._edit.cursorRect()
        metrics = QFontMetrics(self._edit.font())
        width = max(6, metrics.horizontalAdvance("0"))
        self.setGeometry(QRect(
            rect.left(),
            rect.bottom() - self.THICKNESS + 1,
            width,
            self.THICKNESS,
        ))

    def _tick(self) -> None:
        name = theme.current() if self._split else theme.GREEN
        elapsed = (time.monotonic() - self._started) * 1000.0
        alpha = caret_alpha(name, elapsed)
        if abs(alpha - self._alpha) > 0.01:
            self._alpha = alpha
            self.update()

    def paintEvent(self, event):
        if self._alpha <= 0.01:
            return
        colour = QColor(theme.recolour(PHOSPHOR))
        colour.setAlphaF(min(1.0, self._alpha))
        painter = QPainter(self)
        painter.fillRect(self.rect(), colour)


def install_caret(edit, split: bool = True) -> UnderscoreCaret:
    """Give `edit` an underscore caret. `split` varies the cadence by theme."""
    return UnderscoreCaret(edit, split=split)


# ── The backdrop ──────────────────────────────────────────────────────

#: Characters the red theme churns through before a heading settles.
NOISE = "@#$%&*!?/\\<>=+~^01"

#: How long a heading takes to come out of the noise.
DECODE_MS = 1100.0


def decode(target: str, elapsed_ms: float) -> str:
    """`target` resolving out of noise, left to right, by `elapsed_ms`.

    Red's detail, and the reason it is a decode rather than a glitch: 1337 and
    black-hat is about getting at something that was hidden, so text settling
    out of noise says it. An RGB-split or a tear only says broken monitor.
    """
    if elapsed_ms >= DECODE_MS:
        return target
    settled = int(len(target) * (elapsed_ms / DECODE_MS))
    churn = int(elapsed_ms / 55)
    out = []
    for index, character in enumerate(target):
        if index < settled or character == " ":
            out.append(character)
        else:
            out.append(NOISE[(index * 5 + churn * 3) % len(NOISE)])
    return "".join(out)


class VibeBackdrop(QWidget):
    """The empty transcript: a moving texture, and the copy that sits on it.

    One texture per theme, all of them slow and all of them faint: rain for
    green, a hex dump bleeding upward for red, a grid falling away to the
    horizon for blue. `visible_while` is asked on every tick whether there is
    still nothing to read; when it says no, the texture stops dead rather than
    fading, because fading under arriving text is its own distraction.

    The heading and the line under it are painted here rather than left to the
    edit's placeholder, because red needs to animate the heading and a
    placeholder is a static string. Every theme gets the same two lines; only
    red decodes its heading, once, each time the pane empties again.
    """

    TICK_MS = 100
    GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎ0123456789"
    HEX = "0123456789abcdef"
    TEXT = "#e8ece9"
    TEXT_DIM = "#a8b3ad"

    def __init__(self, parent, visible_while=None, heading="", subcopy=""):
        super().__init__(parent)
        self._visible_while = visible_while
        self._heading = heading
        self._subcopy = subcopy
        self._phase = 0.0
        self._revealed = time.monotonic()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)

        parent.installEventFilter(self)
        self._fit()
        self.lower()

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _fit(self) -> None:
        self.setGeometry(self.parentWidget().rect())

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self._fit()
        return False

    def _tick(self) -> None:
        wanted = True if self._visible_while is None else bool(self._visible_while())
        if wanted != self.isVisible():
            # Coming back is a fresh reveal: the heading decodes again, because
            # an empty pane you have just cleared is news the same way the
            # first one was.
            if wanted:
                self._revealed = time.monotonic()
            self.setVisible(wanted)
        if not wanted:
            return
        self._phase += 1.0
        self.update()

    def heading_now(self) -> str:
        """The heading as it reads this instant — decoding, under red."""
        if theme.current() != theme.RED or not self._heading:
            return self._heading
        elapsed = (time.monotonic() - self._revealed) * 1000.0
        return decode(self._heading, elapsed)

    # ── painting ──────────────────────────────────────────────────────
    def paintEvent(self, event):
        name = theme.current()
        colour = QColor(theme.recolour(PHOSPHOR))
        colour.setAlphaF(BACKDROP_ALPHA)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if name == theme.RED:
            self._paint_hex(painter, colour)
        elif name == theme.BLUE:
            self._paint_grid(painter, colour)
        else:
            self._paint_rain(painter, colour)
        self._paint_copy(painter)

    def _paint_copy(self, painter):
        """The two lines, at full strength — they are copy, not texture."""
        if not self._heading and not self._subcopy:
            return
        middle = self.height() // 2

        heading_font = QFont()
        heading_font.setPointSize(14)
        heading_font.setWeight(QFont.DemiBold)
        if theme.current() == theme.RED:
            heading_font = self._mono(13)
            heading_font.setWeight(QFont.DemiBold)
        painter.setFont(heading_font)
        painter.setPen(QPen(QColor(theme.recolour(self.TEXT))))
        painter.drawText(
            QRect(0, middle - 34, self.width(), 26),
            Qt.AlignHCenter | Qt.AlignVCenter,
            self.heading_now(),
        )

        body = QFont()
        body.setPointSize(11)
        painter.setFont(body)
        painter.setPen(QPen(QColor(theme.recolour(self.TEXT_DIM))))
        painter.drawText(
            QRect(40, middle - 2, max(0, self.width() - 80), 44),
            Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
            self._subcopy,
        )

    def _mono(self, size: int) -> QFont:
        font = QFont("Menlo", size)
        font.setStyleHint(QFont.Monospace)
        return font

    def _paint_rain(self, painter, colour):
        font = self._mono(11)
        painter.setFont(font)
        painter.setPen(QPen(colour))
        step = QFontMetrics(font).height()
        columns = max(1, self.width() // 26)
        for column in range(columns):
            speed = 0.8 + (column % 5) * 0.35
            drop = (self._phase * speed + column * 11) % (self.height() / step + 14)
            x = 13 + column * 26
            for i in range(14):
                y = int((drop - i) * step)
                if 0 <= y <= self.height():
                    index = (column * 7 + i * 13 + int(drop)) % len(self.GLYPHS)
                    painter.drawText(x, y, self.GLYPHS[index])

    def _paint_hex(self, painter, colour):
        font = self._mono(10)
        painter.setFont(font)
        painter.setPen(QPen(colour))
        step = QFontMetrics(font).height() + 5
        drift = int(self._phase * 0.4) % step
        rows = self.height() // step + 2
        for row in range(rows):
            address = (row + int(self._phase * 0.4) // step) * 16
            text = f"{address:08x}  "
            for byte in range(16):
                value = (address * 7 + byte * 61 + (byte % 7) * 11) % 256
                text += self.HEX[(value >> 4) & 15] + self.HEX[value & 15] + " "
            painter.drawText(18, row * step - drift + step, text)

    def _paint_grid(self, painter, colour):
        painter.setPen(QPen(colour, 1))
        horizon = self.height() * 0.42
        floor = self.height()
        centre = self.width() / 2

        # Spokes converging on the vanishing point.
        for i in range(-7, 8):
            painter.drawLine(int(centre + i * self.width() / 9),
                             int(floor), int(centre), int(horizon))

        # Rungs, spaced so they crowd toward the horizon, sweeping forward.
        depth = (self._phase * 0.012) % 1.0
        for i in range(14):
            t = ((i + depth) / 14.0) ** 2.4
            y = horizon + (floor - horizon) * t
            if y <= floor:
                painter.drawLine(0, int(y), self.width(), int(y))


def install_backdrop(parent, visible_while=None, heading="", subcopy="") -> VibeBackdrop:
    """Put the theme's empty state behind `parent`, shown while `visible_while()`."""
    return VibeBackdrop(parent, visible_while=visible_while,
                        heading=heading, subcopy=subcopy)


# ── HUD brackets ──────────────────────────────────────────────────────

class HudBrackets(QWidget):
    """Four corner marks around a focused field. Blue's detail, and only blue's.

    Drawn inside the viewport rather than around the widget: the brackets then
    cannot be clipped by whatever the field is laid out in, and they land in
    the field's own padding, clear of the text. They follow focus, so at most
    one field wears them.
    """

    ARM = 11
    PEN = 1.4
    INSET = 2

    def __init__(self, edit):
        super().__init__(edit.viewport())
        self._edit = edit
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

        edit.installEventFilter(self)
        edit.viewport().installEventFilter(self)
        self._fit()

    def _fit(self) -> None:
        self.setGeometry(self._edit.viewport().rect())

    def _wanted(self) -> bool:
        return theme.current() == theme.BLUE and self._edit.hasFocus()

    def refresh(self) -> None:
        """Re-ask whether the brackets belong — after a theme change."""
        self._fit()
        self.setVisible(self._wanted())
        if self.isVisible():
            self.raise_()
        self.update()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.FocusIn, QEvent.FocusOut,
                            QEvent.Resize, QEvent.Show):
            self.refresh()
        return False

    def paintEvent(self, event):
        if not self._wanted():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.accent()), self.PEN))

        left = self.INSET
        top = self.INSET
        right = self.width() - self.INSET
        bottom = self.height() - self.INSET
        arm = self.ARM
        for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1),
                             (left, bottom, 1, -1), (right, bottom, -1, -1)):
            painter.drawLine(x, y, x + dx * arm, y)
            painter.drawLine(x, y, x, y + dy * arm)


def install_brackets(edit) -> HudBrackets:
    """Give `edit` HUD corner brackets while it is focused under the blue theme."""
    return HudBrackets(edit)

"""Three themes (green, red, blue), one stylesheet.

Sentinel is authored in green. Every colour in ``ui/style.py`` — and in the
chrome that a few call sites still set inline — is written once, in the green
the app has always used. The red theme is not a second copy of that sheet kept
in step by hand; it is the same sheet with its hue turned. Two sheets drift,
and the one nobody is looking at is always the one that rots.

Rotation is confined to a band of hues (``_BAND``) wide enough to cover the
app's own greens and the green-tinted greys mixed from them, and nothing else.
Every colour outside the band is *semantic* and passes through untouched:

* the danger red of a destructive button,
* the amber of a model that costs money,
* the blue of an informational badge,
* white, black and any true grey, which have no hue to turn.

The distinction that matters is what a colour is *claiming*. A WiFi adapter
reporting "monitor mode OK" in green is making a claim about the adapter, so it
stays green under the red theme — those call sites deliberately do not route
through here. An active tab is green because the theme is green, so it turns.

``save_setting``/``get_setting`` are imported lazily: this module is pulled in
by ``ui.style`` at import time, and the database opens (and migrates) on first
touch, which is not something a stylesheet import should trigger.
"""

from __future__ import annotations

import colorsys
import re
from typing import Final

GREEN: Final[str] = "green"
RED: Final[str] = "red"
BLUE: Final[str] = "blue"
THEMES: Final[tuple[str, ...]] = (GREEN, RED, BLUE)

#: Human labels for the picker, in display order.
LABELS: Final[dict[str, str]] = {
    GREEN: "Green (Matrix)",
    RED: "Red",
    BLUE: "Blue (Cyberpunk)",
}

SETTING_KEY: Final[str] = "ui_theme"

# Every colour the app owns falls between 135° and 156° — #3cff88 at 143°, the
# phosphor at 135°, and the tinted greys mixed from them in between. The band
# is wider than that cluster so a future off-green still turns, and stops short
# of the two semantic colours nearest it: amber at 44° and the informational
# blue at 204°. Widening it past either would start repainting meaning.
_BAND: Final[tuple[float, float]] = (80.0, 185.0)

# Degrees to turn, chosen by where they land the accent rather than by being
# round numbers. -140 puts #3cff88 on a warm red and #00ff41 on a hot one;
# +36.6 puts the accent on exact cyan (#3cffff) with the phosphor an electric
# aqua behind it, which is the blue worth having and the furthest a blue can
# sit from the informational #4db8ff at 204°. That gap is 24° — the narrowest
# margin in this file, and the reason the blue theme is cyan and not azure.
_SHIFT: Final[dict[str, float]] = {GREEN: 0.0, RED: -140.0, BLUE: 36.6}

#: The accent as authored. Painter code asks for it through `accent()`.
ACCENT_GREEN: Final[str] = "#3cff88"

# Put this comment on a style-sheet line whose colour means something (an
# "ok" light, an "on" readout) and recolour() leaves that line alone.
KEEP_MARK: Final[str] = "/* keep */"

_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
_RGB = re.compile(r"\brgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?=[,)])")

_cached: str | None = None


def _rotate(r: int, g: int, b: int, shift: float) -> tuple[int, int, int]:
    """Turn one colour's hue, or hand it back if it is not ours to repaint."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if s == 0.0:
        return r, g, b
    degrees = h * 360.0
    if not _BAND[0] <= degrees <= _BAND[1]:
        return r, g, b
    turned = ((degrees + shift) % 360.0) / 360.0
    nr, ng, nb = colorsys.hsv_to_rgb(turned, s, v)
    return round(nr * 255), round(ng * 255), round(nb * 255)


def recolour(css: str, theme: str | None = None) -> str:
    """Return `css` under `theme`.

    Takes a whole style sheet or a single ``#rrggbb``; handles ``rgb()`` and
    ``rgba()`` in place, leaving the alpha argument alone.
    """
    shift = _SHIFT[theme or current()]
    if not shift:
        return css

    if KEEP_MARK in css:
        # A line carrying the marker is a status claim painted in the app's
        # own green ("ok" light, an "on" readout): it keeps its colour in
        # every theme, or under Red an OK light and an alert light would be
        # the same red.
        return "\n".join(
            line if KEEP_MARK in line else recolour(line, theme)
            for line in css.split("\n")
        )

    def hex_sub(match: re.Match[str]) -> str:
        digits = match.group(1)
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        return "#%02x%02x%02x" % _rotate(r, g, b, shift)

    def rgb_sub(match: re.Match[str]) -> str:
        r, g, b = _rotate(*(int(match.group(i)) for i in (1, 2, 3)), shift)
        return f"{match.group(0).split('(')[0]}({r}, {g}, {b}"

    return _RGB.sub(rgb_sub, _HEX.sub(hex_sub, css))


def accent(theme: str | None = None) -> str:
    """The accent under `theme`, for painter code that cannot use a sheet."""
    return recolour(ACCENT_GREEN, theme)


def current() -> str:
    """The saved theme, defaulting to green and never raising."""
    global _cached
    if _cached is None:
        try:
            from services.database import get_setting
            saved = get_setting(SETTING_KEY, GREEN)
        except Exception:
            saved = GREEN
        _cached = saved if saved in THEMES else GREEN
    return _cached


def set_current(theme: str) -> None:
    """Persist `theme` and make it the one every later call reads."""
    global _cached
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}; expected one of {THEMES}")
    from services.database import save_setting
    save_setting(SETTING_KEY, theme)
    _cached = theme


def forget() -> None:
    """Drop the cached theme so the next read goes back to the database."""
    global _cached
    _cached = None

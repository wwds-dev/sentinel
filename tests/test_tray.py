"""The menu bar item: its artwork, its menu, and the line it answers with.

The status item itself cannot be asserted on — `isSystemTrayAvailable()` is
False under the offscreen platform these tests run on, and whether macOS drew
the glyph is not a question Qt will answer. What is testable is everything that
decides whether the thing is usable once drawn: artwork macOS can recolour, a
menu in the order the hand expects, and a status line that cannot take the menu
down with it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from ui import tray

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def test_the_glyph_is_a_template_image():
    """macOS recolours a template image wholesale, so any colour in the file is
    thrown away — and a glyph that is not black is a glyph nobody can see."""
    glyph = Image.open(ASSETS / "tray.png").convert("RGBA")

    assert glyph.size == (18, 18)
    assert {pixel[:3] for pixel in glyph.get_flattened_data() if pixel[3] > 0} == {(0, 0, 0)}


def test_the_glyph_has_a_retina_twin():
    """Qt loads `tray@2x.png` from beside the file; without it the menu bar
    shows an 18px image blown up on every Mac sold in a decade."""
    retina = Image.open(ASSETS / "tray@2x.png")

    assert retina.size == (36, 36)


def test_the_glyph_has_both_ink_and_air():
    """Catches the two ways the generator can silently produce nothing: an empty
    file, and one flooded solid to the edges."""
    alpha = Image.open(ASSETS / "tray.png").convert("RGBA").getchannel("A")
    inked = sum(1 for value in alpha.get_flattened_data() if value > 0)

    assert alpha.getextrema() == (0, 255)
    assert 0.25 < inked / (18 * 18) < 0.85


def test_the_icon_is_handed_to_qt_as_a_mask():
    """setIsMask is what makes macOS invert the glyph for a dark menu bar."""
    icon = tray.glyph(ROOT)

    assert not icon.isNull()
    assert icon.isMask()


def test_a_missing_asset_does_not_raise():
    """A null icon is a status item nobody can see; it is not a failed launch."""
    assert tray.glyph(ROOT / "nowhere").isNull()


def _menu_items(item: tray.Tray) -> list[str]:
    return [action.text() if not action.isSeparator() else "---"
            for action in item._menu.actions()]


def test_the_menu_reads_status_then_open_then_quit():
    item = tray.Tray(ROOT, lambda: "Idle")

    assert _menu_items(item) == [
        "Idle", "---", "Open Sentinel", "---", "Quit Sentinel",
    ]


def test_the_status_line_cannot_be_clicked():
    """It is a readout. An enabled item that does nothing is a broken button."""
    item = tray.Tray(ROOT, lambda: "Idle")

    assert not item.status_action.isEnabled()


def test_opening_the_menu_rereads_the_status():
    answers = iter(["Idle", "Working — Trace"])
    item = tray.Tray(ROOT, lambda: next(answers))

    item._menu.aboutToShow.emit()

    assert item.status_action.text() == "Working — Trace"


def test_a_failing_status_leaves_the_rest_of_the_menu_standing():
    """The menu is the way to quit, so it has to survive its own first line."""
    def exploding() -> str:
        raise RuntimeError("the window went away")

    item = tray.Tray(ROOT, exploding)

    assert item.status_action.text() == "Status unavailable"
    assert "Quit Sentinel" in _menu_items(item)


def test_open_and_quit_report_themselves():
    item = tray.Tray(ROOT, lambda: "Idle")
    heard = []
    item.open_requested.connect(lambda: heard.append("open"))
    item.quit_requested.connect(lambda: heard.append("quit"))

    for action in item._menu.actions():
        if action.text() in ("Open Sentinel", "Quit Sentinel"):
            action.trigger()

    assert heard == ["open", "quit"]


# ── the status line itself, which main.py owns ───────────────────────────────

def _window(*, running: bool, agent: str = "osint", cost: float = 0.0):
    worker = SimpleNamespace(isRunning=lambda: running) if running else None
    return SimpleNamespace(
        chat_worker=worker,
        pending_agent=agent,
        _current_agent=agent,
        session_cost_total=cost,
    )


def test_an_idle_sentinel_still_reports_what_it_spent():
    import main

    assert main._tray_status(_window(running=False, cost=0.07)) == (
        "Idle  ·  €0.07 this session"
    )


def test_a_working_sentinel_names_the_agent():
    """The label, not the key — 'osint' is not what the app calls it anywhere."""
    import main

    assert main._tray_status(_window(running=True, agent="osint", cost=1.5)) == (
        "Working — Trace  ·  €1.50 this session"
    )


def test_an_unknown_agent_key_does_not_break_the_line():
    import main

    assert main._tray_status(_window(running=True, agent="forge-made-this")) == (
        "Working  ·  €0.00 this session"
    )


def test_a_running_panel_agent_is_not_idle():
    """Tunnel, Bloodhound and the rest run in their panels, not chat_worker."""
    import main

    window = _window(running=False, cost=0.2)
    window.panels = {
        "osint": SimpleNamespace(is_running=lambda: False),
        "vpn": SimpleNamespace(is_running=lambda: True),
    }
    assert main._tray_status(window) == "Working — Tunnel  ·  €0.20 this session"


def test_a_panel_whose_status_raises_does_not_break_the_line():
    import main

    def boom():
        raise RuntimeError("widget gone")
    window = _window(running=False)
    window.panels = {"wifi": SimpleNamespace(is_running=boom)}
    assert main._tray_status(window) == "Idle  ·  €0.00 this session"

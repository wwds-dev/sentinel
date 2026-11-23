"""Spec readiness-h4-default-mode: a fresh launch stores Local, advise per job (owner decision 2026-11-23,
reversing the earlier Local only default).

Offscreen, no network (tests/conftest.py isolation). Nothing is ticked and no
routing mode has been saved: the stored mode, Settings -> Models and the run
bar must all agree (Hybrid allowed on the run bar). A mode the user saved earlier must survive.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from services import routing_advisor as ra


def _launch(monkeypatch, tmp_path, saved_mode=None):
    import main
    from services import database

    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(main, "SETTINGS_OVERRIDE_FILE", tmp_path / "override.json")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "sentinel.db")
    database.init_db()
    if saved_mode is not None:
        monkeypatch.setattr(main.GodAI, "_routing_settings_defaults",
                            lambda self: {ra.MODE_KEY: saved_mode}, raising=False)
    return main.GodAI()


def test_fresh_launch_agrees_everywhere(monkeypatch, tmp_path):
    from ui.routing_page import RoutingModesPage

    win = _launch(monkeypatch, tmp_path)
    win.settings.pop(ra.MODE_KEY, None)          # nothing saved
    win.settings.pop(ra.OVERRIDES_KEY, None)
    win._sync_execution_mode()
    assert win.routing_mode() == ra.LOCAL_ADVISE
    assert win.execution_mode_box.currentText() == "Hybrid allowed"
    page = RoutingModesPage(win.settings, {"chat": "Chat"}, win.routing_rules,
                            on_change=win.apply_routing_settings)
    assert page.mode_buttons[ra.LOCAL_ADVISE].isChecked()
    assert not page.mode_buttons[ra.LOCAL_ONLY].isChecked()
    for name in ("anthropic", "openai", "deepseek", "kimi", "gemini", "qwen"):
        box = getattr(win, f"allow_{name}_checkbox", None)
        if box is not None:
            assert not box.isChecked()


@pytest.mark.parametrize("saved", [ra.LOCAL_ONLY, ra.LOCAL_FIRST, ra.CLOUD_ONLY])
def test_a_saved_mode_is_kept_on_launch(monkeypatch, tmp_path, saved):
    win = _launch(monkeypatch, tmp_path)
    ra.set_mode(win.settings, saved)
    win._sync_execution_mode()
    assert win.routing_mode() == saved
    assert ra.mode_for(win.settings) == saved

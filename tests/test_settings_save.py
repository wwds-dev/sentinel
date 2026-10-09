"""P0-11: Settings → Save All refuses bad money, keeps what was stored, and
never lets a rejected value reach the budgets the request guard reads."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox, QPushButton, QTabWidget


@pytest.fixture
def settings(monkeypatch):
    from PySide6.QtWidgets import QApplication
    import main
    from ui import dialogs

    QApplication.instance() or QApplication([])
    win = main.GodAI()
    opened, shown = [], {"warning": [], "info": []}
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown["warning"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown["info"].append(a[2])))
    dialogs.show_settings(win)
    dialog = opened[0]
    dialog.win, dialog.shown = win, shown
    yield dialog
    dialog.reject()


def _general_fields(dialog):
    tabs = dialog.findChild(QTabWidget)
    edits = tabs.widget(0).findChildren(QLineEdit)
    return edits[0], edits[1], edits[2]          # EUR per USD, session, daily


def _save(dialog):
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Save All").click()


@pytest.mark.parametrize("bad", ["nan", "inf", "-1", "abc", "1e999", ""])
def test_a_bad_budget_is_refused_and_the_previous_value_kept(settings, bad):
    from services.database import get_setting, save_setting

    save_setting("session_budget_eur", "5.0")
    save_setting("daily_budget_eur", "20.0")
    win = settings.win
    win.session_budget_eur, win.daily_budget_eur = 5.0, 20.0
    _, sess, daily = _general_fields(settings)
    sess.setText(bad)
    daily.setText("20")

    _save(settings)

    assert get_setting("session_budget_eur") == "5.0"
    assert get_setting("daily_budget_eur") == "20.0"
    assert win.session_budget_eur == 5.0 and win.daily_budget_eur == 20.0
    assert settings.shown["warning"] and "Previous values kept" in settings.shown["warning"][0]
    assert settings.shown["info"] == []          # no "saved successfully"


def test_a_zero_exchange_rate_is_refused(settings):
    from services.database import get_setting, save_setting

    save_setting("eur_per_usd", "0.92")
    eur, _, _ = _general_fields(settings)
    eur.setText("0")
    _save(settings)
    assert get_setting("eur_per_usd") == "0.92"
    assert settings.shown["warning"]


def test_good_values_are_stored_and_reach_the_live_budgets(settings):
    from services.database import get_setting

    eur, sess, daily = _general_fields(settings)
    eur.setText("0.90")
    sess.setText("7.25")
    daily.setText("31.5")
    _save(settings)
    assert get_setting("session_budget_eur") == "7.25"
    assert get_setting("daily_budget_eur") == "31.5"
    assert settings.win.session_budget_eur == pytest.approx(7.25)
    assert settings.win.daily_budget_eur == pytest.approx(31.5)
    assert settings.shown["info"] and not settings.shown["warning"]


def test_a_bad_pricing_row_keeps_its_previous_values(settings):
    from services.database import get_connection

    with get_connection() as conn:
        before = [tuple(r) for r in conn.execute(
            "SELECT backend, model, input_per_1m_usd, output_per_1m_usd FROM pricing "
            "ORDER BY backend, model")]
    tabs = settings.findChild(QTabWidget)
    victim = None
    for i in range(tabs.count()):
        for edit in tabs.widget(i).findChildren(QLineEdit):
            if edit.text() and edit.text().replace(".", "", 1).isdigit() and i != 0:
                victim = edit
                break
        if victim:
            break
    assert victim is not None
    victim.setText("-5")
    _save(settings)
    with get_connection() as conn:
        after = [tuple(r) for r in conn.execute(
            "SELECT backend, model, input_per_1m_usd, output_per_1m_usd FROM pricing "
            "ORDER BY backend, model")]
    assert after == before
    assert settings.shown["warning"]

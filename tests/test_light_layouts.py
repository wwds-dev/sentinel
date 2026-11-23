"""The lighter screen changes (docs/specs/agent-layouts.md, screen 4):
Beacon, Trace, Sentry and Forge. Nothing here runs a scan, a lookup or a model.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QSplitter


@pytest.fixture(scope="module")
def win():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QMessageBox
    import main

    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    try:
        yield main.GodAI()
    finally:
        QMessageBox.warning, QMessageBox.question = saved


def _primaries(panel):
    return [b for b in panel.findChildren(QPushButton)
            if b.objectName() == "PrimaryAction" and b.isVisibleTo(panel)]


# ── Beacon ──────────────────────────────────────────────────────────────────

def test_beacon_preflight_is_collapsed_until_run(win, monkeypatch):
    beacon = win.panels["wifi"]
    section = beacon.preflight_section
    assert not section.isExpanded() and section.status() == "Not run"
    assert section.isAncestorOf(beacon.preflight_box)
    import ui.panels.wifi as wifi
    monkeypatch.setattr(wifi, "build_connection_preflight",
                        lambda *_a: {"text": "Read-only preflight text", "has_internet": True})
    monkeypatch.setattr(beacon, "_read_interfaces", lambda: [], raising=False)
    beacon.run_preflight()
    assert section.isExpanded()
    assert section.status() == "Read-only check done"
    section.setExpanded(False)


def test_beacon_figures_are_one_strip_above_the_output(win):
    beacon = win.panels["wifi"]
    strip = beacon.metric_strip
    assert list(strip.cells) == ["Adapter", "Chipset", "Capabilities", "Signal (RSSI)", "Security"]
    for name in ("adapter_label", "chipset_label", "monitor_label", "inject_label",
                 "signal_bar", "signal_val_label", "security_label"):
        assert strip.isAncestorOf(getattr(beacon, name)), name
    assert beacon.findChildren(QSplitter) == []
    layout = beacon.layout()
    holder = next(layout.itemAt(i).widget() for i in range(layout.count())
                  if layout.itemAt(i).widget() is not None
                  and layout.itemAt(i).widget().isAncestorOf(beacon.sections))
    assert layout.indexOf(strip) < layout.indexOf(holder)


# ── Trace ───────────────────────────────────────────────────────────────────

def test_trace_live_checks_are_one_menu(win):
    trace = win.panels["osint"]
    assert not trace.live_btn.isVisibleTo(trace)
    assert not trace.exposure_btn.isVisibleTo(trace)
    assert trace.live_checks_btn.isVisibleTo(trace)
    titles = [a.text() for a in trace.live_checks_menu.actions()]
    assert titles == ["Live Research…", "Exposure Check…"]
    assert _primaries(trace) == [trace.analyse_btn]


def test_trace_menu_presses_the_real_buttons(win, monkeypatch):
    trace = win.panels["osint"]
    pressed = []
    trace.live_btn.clicked.disconnect()
    trace.exposure_btn.clicked.disconnect()
    trace.live_btn.clicked.connect(lambda: pressed.append("live"))
    trace.exposure_btn.clicked.connect(lambda: pressed.append("exposure"))
    try:
        trace.live_research_action.trigger()
        trace.exposure_action.trigger()
        assert pressed == ["live", "exposure"]
    finally:
        trace.live_btn.clicked.disconnect()
        trace.exposure_btn.clicked.disconnect()
        trace.live_btn.clicked.connect(trace.live_research)
        trace.exposure_btn.clicked.connect(trace.exposure_check)


def test_trace_menu_follows_the_buttons_busy_state(win):
    trace = win.panels["osint"]
    trace._set_trace_busy(True)
    try:
        trace._sync_live_checks_menu()
        assert not trace.live_research_action.isEnabled()
        assert not trace.exposure_action.isEnabled()
        assert not trace.live_checks_btn.isVisibleTo(trace)
    finally:
        trace._set_trace_busy(False)
    trace._sync_live_checks_menu()
    assert trace.live_research_action.isEnabled()


def test_trace_activity_appears_with_the_first_run_entry(win):
    import ui.panels.osint as osint
    from ui.panels.osint import OsintPanel
    fresh = OsintPanel(win)
    try:
        assert not fresh.activity_group.isVisibleTo(fresh)
        fresh._append_activity("Validating the target…")
        assert fresh.activity_group.isVisibleTo(fresh)
        fresh._reset_activity()          # a new run keeps it on screen
        assert fresh.activity_group.isVisibleTo(fresh)
    finally:
        fresh.deleteLater()


# ── Sentry ──────────────────────────────────────────────────────────────────

def test_sentry_intro_is_one_line_with_the_full_text_as_tooltip(win):
    from ui.panels.sentry import SentryPanel
    sentry = win.panels["sentry"]
    assert "sends no packets and changes nothing" in sentry.intro_label.text()
    assert sentry.intro_label.toolTip() == SentryPanel.INTRO_TEXT
    assert "ARP/NDP" in SentryPanel.INTRO_TEXT


def test_sentry_baseline_and_background_watch_share_one_row(win):
    sentry = win.panels["sentry"]
    row = {sentry.dry_run_btn.parentWidget(), sentry.reset_btn.parentWidget(),
           sentry.interval_box.parentWidget(), sentry.install_btn.parentWidget(),
           sentry.remove_btn.parentWidget()}
    assert len(row) == 1
    sentry.resize(1000, 800)
    sentry.layout().activate()
    tops = {w.geometry().center().y() for w in (
        sentry.dry_run_btn, sentry.reset_btn, sentry.install_btn, sentry.remove_btn)}
    assert len(tops) == 1


# ── Forge ───────────────────────────────────────────────────────────────────

def test_forge_spec_and_log_hide_until_they_have_content(win):
    forge = win.panels["manager"]
    assert not forge.spec_group.isVisibleTo(forge)
    assert not forge.log_group.isVisibleTo(forge)
    forge.log.append("[Ready] something happened")
    assert forge.log_group.isVisibleTo(forge)
    forge.log.clear()
    assert not forge.log_group.isVisibleTo(forge)
    forge._on_error("provider down")
    assert forge.spec_group.isVisibleTo(forge)
    forge._clear_spec()
    assert not forge.spec_group.isVisibleTo(forge)
    forge.log.clear()


def test_forge_run_bar_has_no_spare_height(win):
    forge = win.panels["manager"]
    bar = forge.analyze_btn.parentWidget()
    assert bar.objectName() == "RunBar"
    win.select_agent("manager")
    QApplication.processEvents()
    # The folded bar is one row: no taller than its tallest control plus margins.
    tallest = max(w.sizeHint().height() for w in bar.findChildren(QPushButton) if w.isVisibleTo(bar))
    assert bar.sizeHint().height() <= tallest + 20

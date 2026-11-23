"""Bug Spray's approved layout (docs/specs/agent-layouts.md, screen 2).

A target bar that is always visible, then three step tabs: Radar, Recon,
Report. The scope and Nmap warnings are shorter but keep every fact, with the
full text as the tooltip.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QSplitter, QTreeWidgetItem

SCOPE_FULL = (
    "Program and Scope are declared by you and are not verified or enforced. "
    "Bug Spray drafts a report from what you enter; only stay within programs "
    "you are authorised to test."
)
NMAP_FULL = (
    "Runs this command on your machine (the first word is the program to "
    "launch), with no scope check and outside the budget/authorisation guard. "
    "Each run asks you to confirm you are authorised to test the target under "
    "the named program's rules, and needs the Program field filled in."
)


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


@pytest.fixture
def spray(win):
    panel = win.panels["bug_bounty"]
    yield panel
    panel.target_input.clear()
    panel.program_input.clear()
    panel.steps.setCurrentIndex(0)
    panel.edit_target_btn.setChecked(True)


def test_steps_hold_radar_recon_and_report(spray):
    steps = spray.steps
    assert [steps.tabText(i) for i in range(steps.count())] == [
        "1 Radar", "2 Recon", "3 Report"]
    assert steps.widget(0) is spray.program_feed
    recon, report = steps.widget(1), steps.widget(2)
    for widget in (spray.nmap_cmd_input, spray.nmap_run_btn, spray.nmap_stop_btn,
                   spray.nmap_output, spray.nmap_note):
        assert recon.isAncestorOf(widget)
    for widget in (spray.findings_input, spray.analyse_btn, spray.stop_btn,
                   spray.severity_label, spray.cvss_label, spray.bounty_label,
                   spray.save_btn, spray.clear_btn, spray.sections, spray.stream_box):
        assert report.isAncestorOf(widget)
    assert spray.findChildren(QSplitter) == []


def test_target_bar_sits_above_the_steps_and_holds_the_fields(spray):
    for widget in (spray.target_input, spray.program_input,
                   spray.scope_box, spray.severity_box, spray.scope_badge):
        assert spray.target_bar.isAncestorOf(widget)
    assert not spray.steps.isAncestorOf(spray.target_bar)
    layout = spray.layout()
    assert layout.indexOf(spray.target_bar) < layout.indexOf(spray.steps)


def test_summary_line_follows_the_fields(spray):
    spray.target_input.setText("https://app.example.com")
    spray.program_input.setText("HackerOne — Example")
    spray.scope_box.setCurrentText("API / REST")
    spray.severity_box.setCurrentText("High (P2)")
    text = spray.target_summary.text()
    for part in ("https://app.example.com", "HackerOne — Example", "API / REST", "High (P2)"):
        assert part in text
    spray.target_input.clear()
    assert spray.target_summary.text().startswith("No target yet")


def test_edit_opens_and_closes_the_fields(spray):
    assert spray.edit_target_btn.isChecked()        # opens expanded: nothing declared
    assert spray.target_editor.isVisibleTo(spray)
    spray.edit_target_btn.setChecked(False)
    assert not spray.target_editor.isVisibleTo(spray)
    assert spray.edit_target_btn.text() == "Edit"
    # Collapsed, the badge and the one-line note stay visible.
    assert spray.scope_badge.isVisibleTo(spray) and spray.scope_note.isVisibleTo(spray)


def test_scope_warning_keeps_every_fact(spray):
    assert spray.scope_badge.text() == "Scope declared, not verified"
    line = spray.scope_note.text()
    for fact in ("Declared by you", "not verified", "enforced", "authorised"):
        assert fact in line
    assert spray.scope_badge.toolTip() == SCOPE_FULL
    assert spray.scope_note.toolTip() == SCOPE_FULL


def test_nmap_warning_keeps_every_fact(spray):
    assert spray.nmap_badge.text() == "No scope check"
    line = spray.nmap_note.text()
    for fact in ("this Mac", "outside the budget/authorisation guard",
                 "Program field", "confirm you are authorised"):
        assert fact in line
    assert spray.nmap_note.toolTip() == NMAP_FULL
    assert spray.nmap_badge.toolTip() == NMAP_FULL


@pytest.mark.parametrize("index,primary", [(1, "nmap_run_btn"), (2, "analyse_btn")])
def test_one_primary_per_step(spray, index, primary):
    page = spray.steps.widget(index)
    primaries = [b for b in page.findChildren(QPushButton)
                 if b.objectName() == "PrimaryAction" and b.isVisibleTo(page)]
    assert primaries == [getattr(spray, primary)]
    # Nothing outside the steps competes with it.
    outside = [b for b in spray.target_bar.findChildren(QPushButton)
               if b.objectName() == "PrimaryAction"]
    assert outside == []


def test_report_figures_are_a_strip_beside_the_findings(spray):
    assert list(spray.metric_strip.cells) == ["Severity", "CVSS", "Bounty estimate"]
    report = spray.steps.widget(2)
    assert report.isAncestorOf(spray.metric_strip)
    assert spray.change_model_btn.parentWidget() is spray.analyse_btn.parentWidget()


def test_picking_a_program_on_the_radar_fills_the_program_field(spray):
    spray.program_feed.program_selected.emit("Bugcrowd — Example")
    assert spray.program_input.text() == "Bugcrowd — Example"
    assert "Bugcrowd — Example" in spray.target_summary.text()


def test_recent_changes_shows_whole_rows_only(spray, win):
    """Six full rows and a seventh cut in half (GUI review 2026-11-22)."""
    win.resize(1600, 1000)
    win.select_agent("bug_bounty")
    feed = spray.program_feed
    for tree in (feed.changes, feed.programs):
        if tree.topLevelItemCount() == 0:
            for n in range(12):
                tree.addTopLevelItem(QTreeWidgetItem([f"row {n}", "x", "y"]))
    feed._fit_whole_rows()
    for tree in (feed.changes, feed.programs):
        row = tree.sizeHintForRow(0)
        visible = tree.height() - feed._chrome_height(tree)
        assert visible == feed.VISIBLE_ROWS * row, (tree, visible, row)


def test_shown_tree_viewport_is_whole_rows(win):
    """Measured on screen: the viewport itself, not a computed figure."""
    win.resize(1600, 1000)
    win.show()
    try:
        win.select_agent("bug_bounty")
        feed = win.panels["bug_bounty"].program_feed
        win.panels["bug_bounty"].steps.setCurrentIndex(0)
        for _ in range(10):
            QApplication.processEvents()
        tree = feed.changes
        if tree.topLevelItemCount() == 0:
            for n in range(12):
                tree.addTopLevelItem(QTreeWidgetItem([f"row {n}", "x", "y"]))
        feed._fit_whole_rows()
        for _ in range(5):
            QApplication.processEvents()
        assert tree.viewport().height() == feed.VISIBLE_ROWS * tree.sizeHintForRow(0)
    finally:
        win.hide()


def test_fit_is_redone_after_refresh(spray, monkeypatch):
    feed = spray.program_feed
    calls = []
    monkeypatch.setattr(feed, "_fit_whole_rows", lambda: calls.append(1))
    feed.refresh()
    assert calls

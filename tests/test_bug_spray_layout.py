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
    panel.edit_target_btn.setChecked(False)


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


def test_empty_target_shows_only_the_target_field(spray):
    """Compact empty state (GUI review 2026-11-23): one field, not the form."""
    assert spray.target_bar_state() == "quick"
    assert spray.target_input.isVisibleTo(spray)
    for widget in (spray.program_input, spray.scope_box, spray.severity_box):
        assert not widget.isVisibleTo(spray)
    assert spray.edit_target_btn.text() == "Edit"


def test_entering_a_target_folds_the_bar_to_one_line(spray):
    spray.target_input.setText("https://app.example.com")
    spray.target_input.editingFinished.emit()
    assert spray.target_bar_state() == "summary"
    assert not spray.target_editor.isVisibleTo(spray)
    assert "https://app.example.com" in spray.target_summary.text()
    # Folded, the badge and the one-line note stay visible.
    assert spray.scope_badge.isVisibleTo(spray) and spray.scope_note.isVisibleTo(spray)


def test_edit_opens_the_whole_form_and_done_closes_it(spray):
    spray.target_input.setText("https://app.example.com")
    spray.edit_target_btn.setChecked(True)
    assert spray.target_bar_state() == "editing"
    for widget in (spray.target_input, spray.program_input, spray.scope_box,
                   spray.severity_box):
        assert widget.isVisibleTo(spray)
    assert spray.edit_target_btn.text() == "Done"
    spray.edit_target_btn.setChecked(False)
    assert spray.target_bar_state() == "summary"
    # Done with the target cleared: back to the one-field empty state.
    spray.edit_target_btn.setChecked(True)
    spray.target_input.clear()
    spray.edit_target_btn.setChecked(False)
    assert spray.target_bar_state() == "quick"


def test_done_is_not_styled_like_a_primary(spray, win):
    """Checked, Edit/Done keeps the neutral look; green is for the primary."""
    spray.edit_target_btn.setChecked(True)
    assert spray.edit_target_btn.objectName() == "BBEditTarget"
    sheet = win.styleSheet() or QApplication.instance().styleSheet()
    block = sheet[sheet.index("QPushButton#BBEditTarget:checked {"):]
    block = block[:block.index("}")]
    assert "#3cff88" not in block.lower()


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


def _fill(feed):
    for tree in (feed.changes, feed.programs):
        if tree.topLevelItemCount() == 0:
            for n in range(40):
                tree.addTopLevelItem(QTreeWidgetItem([f"row {n}", "x", "y"]))


def test_recent_changes_shows_whole_rows_only(spray, win):
    """Six full rows and a seventh cut in half (GUI review 2026-11-22)."""
    win.resize(1600, 1000)
    win.select_agent("bug_bounty")
    feed = spray.program_feed
    _fill(feed)
    feed._fit_whole_rows()
    for tree in (feed.changes, feed.programs):
        row = tree.sizeHintForRow(0)
        visible = tree.maximumHeight() - feed._chrome_height(tree)
        assert visible % row == 0 and visible >= feed.MIN_ROWS * row, (tree, visible, row)


def _fake_feed(rows: int = 40) -> dict:
    """What read_feed returns, with enough rows to overflow the tab."""
    programs = [
        {"name": f"Program {n}", "platform": "hackerone", "slug": f"p{n}",
         "reward": None, "scope_count": 3}
        for n in range(rows)
    ]
    changes = [
        {"program": programs[n], "scanned_at": "2026-11-23T10:00:00", "new": True}
        for n in range(rows)
    ]
    return {"changes": changes, "programs": programs, "last_scan": None}


def test_shown_tree_viewport_is_whole_rows_and_fills_the_tab(win, monkeypatch):
    """Measured on screen: whole rows, and the list takes the tab's height
    (less than one row left below it) instead of leaving a third empty.

    Showing the feed re-reads saved programs and changes, so the data is
    pinned here: the result must not depend on what the checkout has saved
    (CI and fresh worktrees have none)."""
    import ui.panels.bug_spray_feed as feed_module

    feed = win.panels["bug_bounty"].program_feed
    monkeypatch.setattr(feed_module.config, "load", lambda *a, **k: feed_module.config.Settings())
    monkeypatch.setattr(feed_module, "read_feed", lambda *a, **k: _fake_feed())
    monkeypatch.setattr(feed, "_maybe_scan", lambda: None)
    win.resize(1600, 1000)
    win.show()
    try:
        win.select_agent("bug_bounty")
        win.panels["bug_bounty"].steps.setCurrentIndex(0)
        feed.refresh()
        assert feed.changes.topLevelItemCount() == 40, feed.status.text()
        for _ in range(10):
            QApplication.processEvents()
        feed._fit_whole_rows()
        for _ in range(5):
            QApplication.processEvents()
        tree = feed.changes
        row = tree.sizeHintForRow(0)
        assert tree.viewport().height() % row == 0
        page = tree.parentWidget()
        assert page.height() - tree.height() < row
        assert tree.viewport().height() // row > feed.VISIBLE_ROWS
        # Shrinking the window re-fits to fewer whole rows.
        win.resize(1600, 800)
        for _ in range(10):
            QApplication.processEvents()
        assert tree.viewport().height() % row == 0
        assert page.height() - tree.height() < row
    finally:
        win.hide()


def test_radar_has_no_second_frame_or_title(spray):
    feed = spray.program_feed
    assert feed.title() == "" and feed.isFlat()


def test_fit_is_redone_after_refresh(spray, monkeypatch):
    feed = spray.program_feed
    calls = []
    monkeypatch.setattr(feed, "_fit_whole_rows", lambda: calls.append(1))
    feed.refresh()
    assert calls

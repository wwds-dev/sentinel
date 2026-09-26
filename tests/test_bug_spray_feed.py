"""Bug Spray's saved feed is visible in Sentinel without a network request."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy

from agents.bug_spray.bug_spray import config
from agents.bug_spray.bug_spray.models import Program, RewardTier, Scope
from agents.bug_spray.bug_spray.store import Store
from ui.panels.bug_spray_feed import BugSprayFeed
import ui.panels.bug_spray_feed as feed_module
from ui.panels.bug_bounty import BugBountyPanel


@pytest.fixture
def panel(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = config.Settings(enabled_platforms=["hackerone"], data_dir=str(tmp_path))
    monkeypatch.setattr(config, "load", lambda path=None: settings)
    db = Store(settings.db_path)
    db.record(Program("hackerone", "acme", "Acme", "https://hackerone.com/acme", True,
                      Scope(in_scope=["api.acme.test"]), [RewardTier("high", 100, 500, "USD")]))
    db.record_scan(datetime.now(UTC).isoformat(), {"hackerone": {"programs": 1}}, [])
    db.close()
    widget = BugSprayFeed()
    widget.refresh()
    yield widget
    widget.deleteLater()


def test_saved_programs_are_shown_and_can_fill_report(panel):
    assert panel.programs.topLevelItemCount() == 1
    assert "1 watched programs" in panel.status.text()
    selected = []
    panel.program_selected.connect(selected.append)
    panel.programs.setCurrentItem(panel.programs.topLevelItem(0))
    assert "api.acme.test" in panel.details.toPlainText()
    assert "not authorization" in panel.details.toPlainText()
    panel.use_program()
    assert selected == ["Hackerone — Acme"]


def test_auto_scan_respects_interval(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(panel, "scan_now", lambda: calls.append("scan"))
    panel._maybe_scan()
    assert calls == []
    panel._last_scan = {"scanned_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}
    panel._maybe_scan()
    assert calls == ["scan"]


def test_scan_runs_off_ui_thread_and_drains_output(panel, tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("print('x' * 200000)\n")
    monkeypatch.setattr(feed_module, "PROJECT", tmp_path)
    monkeypatch.setattr(feed_module, "PYTHON", tmp_path / "python")
    (tmp_path / "python").symlink_to(sys.executable)
    panel._scan.setWorkingDirectory(str(tmp_path))
    done = QSignalSpy(panel._scan.finished)
    panel.scan_now()
    assert done.wait(5000)
    assert panel.scan_button.isEnabled()
    assert "Last scan:" in panel.status.text()


def test_selected_program_fills_sentinel_report_field(panel):
    class Host:
        def load_models_into(self, provider_box, model_box, agent_key, **kwargs):
            model_box.clear()
            model_box.addItem("test model")

        def register_model_loader(self, agent_key, loader):
            pass

    report = BugBountyPanel(Host())
    try:
        report.program_feed.refresh()
        report.program_feed.programs.setCurrentItem(report.program_feed.programs.topLevelItem(0))
        report.program_feed.use_program()
        assert report.program_input.text() == "Hackerone — Acme"
        assert report.target_input.text() == ""
    finally:
        report.deleteLater()

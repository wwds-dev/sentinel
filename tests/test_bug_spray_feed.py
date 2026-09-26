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
    QApplication.instance() or QApplication([])
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


@pytest.fixture
def two_platform_panel(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    settings = config.Settings(enabled_platforms=["hackerone"], watchlist_keywords=["acme"],
                               data_dir=str(tmp_path))
    monkeypatch.setattr(config, "load", lambda path=None: settings)
    db = Store(settings.db_path)
    db.record(Program("hackerone", "acme", "Acme", "https://hackerone.com/acme", True,
                      Scope(in_scope=["api.acme.test"]), [RewardTier("high", 100, 500, "USD")]))
    db.record(Program("bugcrowd", "acme-bc", "Acme BC", "https://bugcrowd.com/acme", True,
                      Scope(in_scope=["acme.example"]), [RewardTier("critical", None, 9000, "USD")]))
    db.record(Program("immunefi", "other", "Other", "https://immunefi.com/bug-bounty/other/", True,
                      Scope(in_scope=["0xabc"]), [RewardTier("critical", 1, 2, "USD")]))
    db.close()
    widget = BugSprayFeed()
    widget.refresh()
    yield widget
    widget.deleteLater()


def visible(tree):
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
            if not tree.topLevelItem(i).isHidden()]


def test_platform_filter_and_show_all(two_platform_panel):
    panel = two_platform_panel
    assert set(visible(panel.programs)) == {"Acme", "Acme BC"}  # watchlist hides "Other"
    panel.platform.setCurrentIndex(panel.platform.findData("bugcrowd"))
    assert visible(panel.programs) == ["Acme BC"]
    panel.platform.setCurrentIndex(0)
    panel.show_all.setChecked(True)
    assert set(visible(panel.programs)) == {"Acme", "Acme BC", "Other"}
    assert "watchlist off" in panel.status.text()


def test_full_details_show_the_whole_saved_program():
    program = Program("hackerone", "big", "Big <Co>", "https://hackerone.com/big", False,
                      Scope(in_scope=[f"host{i}.test" for i in range(12)], out_of_scope=["legacy.test"]),
                      [RewardTier("critical", 1000, 5000, "USD"), RewardTier("low", None, 100, "EUR")],
                      tags=["wildcard"])
    html = feed_module.program_html(program)
    assert all(f"host{i}.test" in html for i in range(12)) and "legacy.test" in html
    assert "$1,000 – $5,000" in html and "up to €100" in html and "wildcard" in html
    assert "Big &lt;Co&gt;" in html and "paused" in html and "not authorization" in html
    QApplication.instance() or QApplication([])
    dialog = feed_module.ProgramDetailsDialog(program)
    assert "host11.test" in dialog.body.toPlainText()
    dialog.deleteLater()


def test_details_button_follows_selection(panel):
    assert not panel.details_button.isEnabled()
    panel.programs.setCurrentItem(panel.programs.topLevelItem(0))
    assert panel.details_button.isEnabled()


def test_watchlist_dialog_saves_config(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    dialog = feed_module.WatchlistDialog(config.Settings(enabled_platforms=["hackerone"]))
    dialog.platform_boxes["immunefi"].setChecked(True)
    dialog.keywords.setText(" api, graphql ,, ")
    dialog.tags.setText("wildcard")
    dialog.min_reward.setValue(5000)
    dialog.save()
    assert dialog.result() == feed_module.QDialog.Accepted
    saved = config.load(tmp_path / "config.json")
    assert saved.enabled_platforms == ["hackerone", "immunefi"]
    assert saved.watchlist_keywords == ["api", "graphql"] and saved.watchlist_tags == ["wildcard"]
    assert saved.min_reward_usd == 5000
    dialog.deleteLater()


def test_watchlist_dialog_refuses_invalid_settings(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    dialog = feed_module.WatchlistDialog(config.Settings(poll_interval_minutes=0))
    dialog.save()
    assert not (tmp_path / "config.json").exists()
    assert not dialog.error.isHidden() and "poll_interval_minutes" in dialog.error.text()
    dialog.deleteLater()


def test_full_rescan_passes_full_flag(panel, tmp_path, monkeypatch):
    started = []
    (tmp_path / "python").touch()
    monkeypatch.setattr(feed_module, "PYTHON", tmp_path / "python")
    monkeypatch.setattr(panel._scan, "start", lambda program, args: started.append(args))
    panel.full_scan_action.trigger()
    assert started and started[0][-1] == "--full"
    assert "Full re-scan" in panel.status.text()

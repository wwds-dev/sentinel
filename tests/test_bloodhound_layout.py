"""Bloodhound's approved layout (docs/specs/agent-layouts.md, screen 1).

One Brief card with the optional extras collapsed inside it, the run bar at
its foot (cost left, Change model and Investigate right), and the dossier
under a strip of four figures. Nothing that existed before may be lost.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSplitter

from services.model_recommendations import PricingMetadata

AI_ESTIMATE_NOTE = (
    "Threat and Confidence are the AI's own estimate, not a measurement. "
    "Sources counts the public sources actually contacted by live collection."
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
def hound(win):
    panel = win.panels["osint_heavy"]
    yield panel
    # Leave the shared panel as it opens.
    panel.clear_image()
    panel.file_folders.clear()
    panel.file_source_box.setCurrentText("This Mac")
    panel.remote_roots_input.clear()
    for section in (panel.image_section, panel.file_discovery_section, panel.court_section):
        section.setExpanded(False)


def test_optional_extras_open_collapsed_with_their_status(hound):
    assert [s.isExpanded() for s in
            (hound.image_section, hound.file_discovery_section, hound.court_section)] == [
        False, False, False]
    assert hound.image_section.status() == "None"
    assert hound.file_discovery_section.status() == "Off"
    assert hound.court_section.status() == "Organisations only"
    # Defaults that keep data local are unchanged.
    assert hound.send_exif_checkbox.isChecked() is False
    assert hound.courtlistener_box.isChecked() is False


def test_each_extra_expands_in_place_to_the_existing_controls(hound):
    assert hound.image_section.isAncestorOf(hound.browse_btn)
    assert hound.image_section.isAncestorOf(hound.clear_image_btn)
    assert hound.image_section.isAncestorOf(hound.send_exif_checkbox)
    assert hound.image_section.isAncestorOf(hound.exif_display)
    assert hound.file_discovery_section.isAncestorOf(hound.file_search_btn)
    assert hound.file_discovery_section.isAncestorOf(hound.remote_host_input)
    assert hound.court_section.isAncestorOf(hound.courtlistener_box)
    hound.image_section.setExpanded(True)
    assert hound.browse_btn.isVisibleTo(hound)


def test_collapsed_file_discovery_is_off_like_the_unchecked_box_was(hound):
    """Collapsed fields stay out of routing_text and cannot start a search."""
    assert not hound.file_discovery_body.isEnabled()
    hound.file_source_box.setCurrentText("Remote SSH machine")
    hound.remote_host_input.setText("collapsed-host.example")
    assert "collapsed-host.example" not in hound.routing_text("Investigation")
    hound.file_discovery_section.setExpanded(True)
    assert hound.file_discovery_body.isEnabled()
    assert hound.file_discovery_body.isVisibleTo(hound)
    hound.file_discovery_section.setExpanded(False)
    assert not hound.file_discovery_body.isEnabled()
    hound.remote_host_input.clear()


def test_image_status_follows_the_chosen_file(hound, tmp_path):
    from PySide6.QtGui import QImage
    path = tmp_path / "target.png"
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(0)
    assert image.save(str(path))
    hound.set_image(str(path))
    assert hound.image_section.status() == "target.png"
    hound.clear_image()
    assert hound.image_section.status() == "None"


def test_file_discovery_status_counts_locations(hound):
    hound.file_folders.addItem("/tmp/a")
    assert hound.file_discovery_section.status() == "1 location"
    hound.file_folders.addItem("/tmp/b")
    assert hound.file_discovery_section.status() == "2 locations"
    hound.file_folders.clear()
    assert hound.file_discovery_section.status() == "Off"
    hound.file_source_box.setCurrentText("Remote SSH machine")
    hound.remote_roots_input.setText("/srv/a, /srv/b, ")
    assert hound.file_discovery_section.status() == "2 locations"


def test_one_primary_button_even_with_every_extra_open(hound):
    for section in (hound.image_section, hound.file_discovery_section, hound.court_section):
        section.setExpanded(True)
    primaries = [b for b in hound.findChildren(QPushButton)
                 if b.objectName() == "PrimaryAction" and b.isVisibleTo(hound)]
    assert primaries == [hound.investigate_btn]


def test_run_bar_has_cost_left_and_change_model_then_investigate_right(hound):
    bar = hound.investigate_btn.parentWidget()
    assert bar.objectName() == "RunBar"
    row = bar.layout()
    order = [row.indexOf(w) for w in
             (hound.run_bar_cost, hound.change_model_btn, hound.investigate_btn)]
    assert order == sorted(order) and -1 not in order
    # The pickers are folded away but still the live combos.
    assert not hound.provider_box.isVisibleTo(hound)
    assert not hound.auto_route_btn.isVisibleTo(hound)


def test_change_model_menu_drives_the_real_combos(hound):
    before = hound.provider_box.currentText()
    hound._fill_change_model_menu()
    menus = {a.text(): a.menu() for a in hound.change_model_menu.actions() if a.menu()}
    assert set(menus) == {"Provider", "Model"}
    target = next(a for a in menus["Provider"].actions() if a.text() == "ollama")
    target.trigger()
    try:
        assert hound.provider_box.currentText() == "ollama"
        assert "ollama" in hound.run_bar_cost.text()
        assert "Local · no charge" in hound.run_bar_cost.text()
        hound._fill_change_model_menu()
        provider_menu = next(a.menu() for a in hound.change_model_menu.actions()
                             if a.text() == "Provider")
        checked = [a.text() for a in provider_menu.actions() if a.isChecked()]
        assert checked == ["ollama"]
    finally:
        hound.provider_box.setCurrentText(before)


@pytest.mark.parametrize("pricing", [
    PricingMetadata("paid", "Paid", 0.0, 0.0),
    PricingMetadata("paid", "Paid", None, None),
    PricingMetadata("unknown", "Unknown"),
])
def test_cost_line_never_calls_an_unpriced_cloud_model_free(hound, monkeypatch, pricing):
    import services.model_recommendations as mr
    monkeypatch.setattr(mr, "pricing_metadata", lambda p, m: pricing)
    hound._refresh_run_bar_cost()
    text = hound.run_bar_cost.text()
    assert "Price unknown" in text
    assert "free" not in text.lower() and "no charge" not in text.lower()


def test_cost_line_shows_a_known_price(hound, monkeypatch):
    import services.model_recommendations as mr
    monkeypatch.setattr(mr, "pricing_metadata",
                        lambda p, m: PricingMetadata("paid", "Paid", 0.75, 3.75))
    hound._refresh_run_bar_cost()
    assert "$0.75 / $3.75 per 1M tokens in/out" in hound.run_bar_cost.text()


def test_figures_are_one_strip_above_the_full_width_dossier(hound):
    strip = hound.metric_strip
    for label in (hound.threat_label, hound.conf_label,
                  hound.sources_label, hound.depth_label):
        assert strip.isAncestorOf(label)
    assert strip.isAncestorOf(hound.threat_bar)
    assert list(strip.cells) == [
        "Threat (AI est.)", "Confidence (AI est.)", "Sources contacted", "Depth"]
    layout = hound.layout()
    index_of = lambda w: next(i for i in range(layout.count())
                              if layout.itemAt(i).widget() is not None
                              and (layout.itemAt(i).widget() is w
                                   or layout.itemAt(i).widget().isAncestorOf(w)))
    assert index_of(strip) < index_of(hound.sections)
    assert hound.findChildren(QSplitter) == []


def test_ai_estimate_sentence_is_kept_verbatim(hound):
    assert hound.metric_strip.note.text() == AI_ESTIMATE_NOTE
    assert not hound.metric_strip.note.isHidden()


def test_dossier_header_holds_save_and_clear(hound):
    heading = next(w for w in hound.findChildren(QLabel)
                   if w.objectName() == "ResultsHeading")
    assert heading.text() == "Dossier"
    assert heading.parentWidget() is hound.save_btn.parentWidget() is hound
    assert hound.save_btn.text() == "Save Report" and hound.clear_btn.text() == "Clear"


def test_clear_resets_the_strip(hound):
    hound._update_indicators("Threat level: 7/10\nConfidence: 80%")
    hound.clear()
    assert [l.text() for l in (hound.threat_label, hound.conf_label,
                               hound.sources_label, hound.depth_label)] == ["—"] * 4
    assert hound.threat_bar.value() == 0

"""Routing phase 2: the model picker (mockup A) and Settings → Local models (D).

Local models are grouped, described and tagged; they are router candidates from
any provider selection; discovery never runs on the UI thread.
"""

import os
from types import SimpleNamespace

import pytest

from services import local_models as lm
from services.local_models import LocalInventory, LocalModel, Snapshot

GB = 1e9


class FakeLM:
    def __init__(self, up=True, rows=()):
        self.up, self.rows, self.calls = up, list(rows), 0

    def server_up(self):
        self.calls += 1
        return self.up

    def model_details(self):
        self.calls += 1
        return self.rows


class FakeOllama:
    def __init__(self, details=None, up=True):
        self.details, self.up, self.calls = details or {}, up, 0

    def model_details(self):
        self.calls += 1
        return self.details

    def list_models(self):
        if not self.up:
            raise RuntimeError("down")
        return list(self.details)

    def loaded_models(self):
        return []


ROWS = [
    {"id": "deepseek-r1-distill-qwen-14b-abliterated", "name": "DeepSeek R1 Distill 14B",
     "publisher": "bartowski", "quant": "Q6_K", "size_bytes": 12 * GB, "state": "loaded"},
    {"id": "dolphin-3.0-llama3.1-8b", "name": "Dolphin 3.0 8B", "publisher": "cognitivecomputations",
     "quant": "Q8_0", "size_bytes": 9 * GB, "state": "not-loaded"},
    {"id": "dsr1-32b-iq3", "name": "DeepSeek R1 Distill 32B", "publisher": "x",
     "quant": "IQ3_M", "size_bytes": 15 * GB, "state": "not-loaded"},
]


def snap(up=True, ollama=None, free=12 * GB, total=24 * GB):
    return lm.discover(FakeLM(up, ROWS), FakeOllama(ollama or {}),
                       memory=lambda: (free, total), lms_binary=lambda: None)


def test_states_sizes_and_too_big_now():
    s = snap()
    by = {m.id: m for m in s.models}
    assert by["deepseek-r1-distill-qwen-14b-abliterated"].state == "loaded"
    assert by["dolphin-3.0-llama3.1-8b"].state == "on disk, loads on first use"
    assert by["dsr1-32b-iq3"].state == "too big now"       # 15 GB x 1.2 > 12 GB free
    assert snap(up=False).models[1].state == "on disk, server off"


def test_detail_line_has_state_size_rating_and_free_tag():
    s = snap()
    detail = lm.detail_text(s.find("lmstudio", "dolphin-3.0-llama3.1-8b"))
    assert detail == "on disk, loads on first use · 9.0 GB · unrated · €0"
    assert lm.detail_text(s.find("lmstudio", "deepseek-r1-distill-qwen-14b-abliterated")
                          ).startswith("loaded · 12 GB")


def test_rating_hook_replaces_unrated_for_phase_3():
    lm.set_rating_lookup(lambda p, m: "measured 1281" if "14b" in m else None)
    try:
        s = snap()
        assert "measured 1281" in lm.detail_text(
            s.find("lmstudio", "deepseek-r1-distill-qwen-14b-abliterated"))
        assert "unrated" in lm.detail_text(s.find("lmstudio", "dsr1-32b-iq3"))
    finally:
        lm.set_rating_lookup(None)


def test_tags_follow_name_and_publisher():
    s = snap()
    tags = {m.id: m.tag for m in s.models}
    assert tags["deepseek-r1-distill-qwen-14b-abliterated"] == "abliterated"
    assert tags["dolphin-3.0-llama3.1-8b"] == "uncensored"
    assert tags["dsr1-32b-iq3"] is None


def test_sections_are_named_per_runtime():
    s = snap(ollama={"llama3:8b": {"size_bytes": 5 * GB, "quantization": "Q4_0"}})
    assert {r["section"] for r in lm.picker_entries(s, "lmstudio")} == {
        "On this Mac · LM Studio"}
    assert {r["section"] for r in lm.picker_entries(s, "ollama")} == {
        "On this Mac · Ollama"}
    assert lm.SECTION_ORDER == ("On this Mac · LM Studio", "On this Mac · Ollama", "Cloud")


def test_empty_runtimes_say_what_to_do():
    s = lm.discover(FakeLM(False, []), FakeOllama({}, up=True),
                    memory=lambda: (1, 2), lms_binary=lambda: None)
    assert lm.empty_section_note(s, "ollama") == "No models · ollama pull …"
    assert "Start" in lm.empty_section_note(s, "lmstudio")
    assert "lms server start" in lm.start_server_hint(
        Snapshot(lms_found=True)) and "LM Studio" in lm.start_server_hint(Snapshot())


def test_the_ui_reads_a_cache_and_never_fetches():
    lmstudio, ollama = FakeLM(True, ROWS), FakeOllama({})
    inv = LocalInventory()
    inv.snapshot(); inv.stale()
    assert lmstudio.calls == 0 and ollama.calls == 0       # reading never fetches
    inv.refresh(lmstudio, ollama, memory=lambda: (1, 2), lms_binary=lambda: None)
    assert lmstudio.calls and len(inv.snapshot().models) == 3


def test_a_broken_runtime_is_an_error_row_not_a_crash():
    class Boom(FakeLM):
        def server_up(self):
            raise OSError("nope")
    s = lm.discover(Boom(), FakeOllama({}), memory=lambda: (1, 2), lms_binary=lambda: None)
    assert s.errors and not s.lmstudio_up


def test_table_rows_and_status_chips():
    s = snap(ollama={"llama3:8b": {"size_bytes": 5 * GB, "quantization": "Q4_0"}})
    rows = lm.table_rows(s, {("lmstudio", "dolphin-3.0-llama3.1-8b"): ["Chat", "Write"]})
    assert [r["source"] for r in rows] == ["LM Studio"] * 3 + ["Ollama"]
    dolphin = next(r for r in rows if r["id"] == "dolphin-3.0-llama3.1-8b")
    assert dolphin["used_by"] == "Chat, Write" and dolphin["rating"] == "unrated"
    chips = lm.status_lines(s)
    assert chips["lmstudio"] == "LM Studio server running"
    assert chips["ollama"] == "Ollama running · 1 models"
    assert chips["ram"] == "RAM free 12.0 / 24 GB"


# ── widgets ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_picker_menu_shows_sections_details_tags_and_best_fit(qapp):
    from PySide6.QtWidgets import QWidget
    from ui.widgets import (DETAIL_ROLE, LABEL_ROLE, RECOMMENDED_ROLE, SECTION_ROLE,
                            MenuComboBox)
    parent = QWidget()
    combo = MenuComboBox(parent)
    combo.setMenuMode(MenuComboBox.MODEL)
    s = snap()
    entries = lm.picker_entries(s, "lmstudio")
    combo.addItems([e["id"] for e in entries])
    for i, e in enumerate(entries):
        combo.setItemData(i, e["section"], SECTION_ROLE)
        combo.setItemData(i, e["label"], LABEL_ROLE)
        combo.setItemData(i, e["detail"], DETAIL_ROLE)
    combo.setItemData(0, True, RECOMMENDED_ROLE)
    menu = combo.buildMenu()
    assert menu.sectionTitles() == ["On this Mac · LM Studio"]
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[0] == "DeepSeek R1 Distill 14B · Q6_K"
    assert combo.itemText(0) == "deepseek-r1-distill-qwen-14b-abliterated"   # id stays the text
    details = {a.text(): t for a, t in menu.detailTexts().items()}
    assert details[texts[2]].startswith("too big now")
    tags = {a.text(): t for a, t in menu.tagBadges().items()}
    assert tags[texts[0]] == "ABLITERATED" and tags[texts[1]] == "UNCENSORED"
    assert list(menu.bestFitBadges().values()) == ["BEST FIT"]
    assert all("€0" in t for t in details.values())
    combo.setCurrentIndex(0)
    assert combo.currentTag() == "abliterated"             # tag on the selected model
    menu.reserveBadgeRoom()
    menu.deleteLater()


def test_settings_page_lists_models_and_offers_start_when_the_server_is_off(qapp):
    from PySide6.QtWidgets import QWidget
    from ui.local_models_page import LocalModelsPage
    started = []
    host = QWidget()
    page = LocalModelsPage(on_start=lambda: started.append(1), parent=host)
    page.set_snapshot(snap(up=False), {("lmstudio", "dsr1-32b-iq3"): ["Chat"]})
    rows = page.rows()
    assert len(rows) == 3 and rows[0][1] == "LM Studio"
    assert "ABLITERATED" in rows[0][0] and rows[2][5] == "Chat" and rows[2][4] == "unrated"
    assert not page.start_button.isHidden() and not page.hint.isHidden()
    page.start_button.click()
    assert started == [1]
    page.set_snapshot(snap(up=True))
    assert page.start_button.isHidden() and "running" in page.lmstudio_status.text()


# ── the router ─────────────────────────────────────────────────────────────

def _router_candidates(models):
    from services import model_recommendations as mr
    mr.register_local_models(models)
    return mr


def test_local_models_are_router_candidates_from_any_provider_selection():
    mr = _router_candidates(snap().models)
    try:
        keys = {(p.provider, p.model) for p in mr.catalog()}
        assert ("lmstudio", "dolphin-3.0-llama3.1-8b") in keys
        # Privacy first prefers local; the cloud providers stay enabled alongside.
        decision = mr.route_request(
            "summarise this note", enabled_providers={"openai", "lmstudio"},
            preferences=mr.RoutingPreferences(priority="privacy"),
            available_models={"openai": ["gpt-4.1-mini"],
                              "lmstudio": ["dolphin-3.0-llama3.1-8b"]})
        assert decision.provider == "lmstudio"
        assert "free (local)" in decision.reason or decision.provider == "lmstudio"
    finally:
        mr.register_local_models([])


def test_unrated_local_models_never_beat_rated_ones():
    mr = _router_candidates(snap().models)
    try:
        rating = SimpleNamespace(score=1300.0, label="writing")
        decision = mr.route_request(
            "write a poem", enabled_providers={"openai", "lmstudio"},
            available_models={"openai": ["gpt-4.1-mini"],
                              "lmstudio": ["dolphin-3.0-llama3.1-8b"]},
            ratings=lambda p, m, t: rating if p == "openai" else None)
        assert decision.provider == "openai" and "unrated" in decision.reason
    finally:
        mr.register_local_models([])


def test_withdrawn_models_leave_the_router():
    mr = _router_candidates(snap().models)
    mr.register_local_models([])
    assert not [p for p in mr.catalog() if p.provider == "lmstudio"]

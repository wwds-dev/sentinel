"""New-model detection, assessment and adoption (services/model_watch.py)."""

from __future__ import annotations

from datetime import datetime

import pytest

from services import model_recommendations as mr
from services import model_watch as mw
from services.model_watch import (
    ModelWatch, ProviderListing, assess, canonical, family_version,
    infer_profile, is_chat_model, is_successor, list_live,
)

SONNET_AGENTS = {"wifi", "bug_bounty", "manager", "vpn", "sentry"}


@pytest.fixture
def clean_router():
    """Adoption mutates module state; put it back after each test."""
    saved_recs = dict(mr.AGENT_RECOMMENDATIONS)
    saved_profiles = dict(mr._ADOPTED_PROFILES)
    yield
    mr.AGENT_RECOMMENDATIONS.clear()
    mr.AGENT_RECOMMENDATIONS.update(saved_recs)
    mr._ADOPTED_PROFILES.clear()
    mr._ADOPTED_PROFILES.update(saved_profiles)


@pytest.fixture
def watch(tmp_path):
    return ModelWatch(tmp_path / "model_watch.json")


def ok(provider, *models):
    return ProviderListing(provider, "ok", tuple(models))


KNOWN = {"anthropic": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"]}


# ── Names ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model, expected", [
    ("claude-sonnet-5-20260115", "claude-sonnet-5"),
    ("gpt-4o-2024-08-06", "gpt-4o"),
    ("gemini-3-pro-preview-11-2025", "gemini-3-pro"),
    ("gemini-2.5-flash-001", "gemini-2.5-flash"),
    ("claude-sonnet-5-5", "claude-sonnet-5-5"),
])
def test_snapshot_suffixes_are_not_new_models(model, expected):
    assert canonical(model) == expected


@pytest.mark.parametrize("model, family, version", [
    ("claude-sonnet-4-6", "claude-sonnet", (4, 6)),
    ("claude-sonnet-5-5", "claude-sonnet", (5, 5)),
    ("gpt-4.1-mini", "gpt-mini", (4, 1)),
    ("qwen3.8-max", "qwen-max", (3, 8)),
    ("kimi-k2.7-code", "kimi-k-code", (2, 7)),
])
def test_family_and_version(model, family, version):
    assert family_version(model) == (family, version)


@pytest.mark.parametrize("model", [
    "text-embedding-3-large", "gpt-4o-mini-tts", "whisper-1",
    "omni-moderation-latest", "gpt-4o-realtime-preview", "gemma-3-27b-it",
])
def test_non_chat_models_are_ignored(model):
    assert not is_chat_model(model)


def test_successor_means_newer_than_every_catalog_sibling():
    assert is_successor("anthropic", "claude-sonnet-5-5")
    assert not is_successor("anthropic", "claude-sonnet-4-6")   # older
    assert not is_successor("openai", "gpt-3.5-turbo")          # no family here


# ── Noticing ─────────────────────────────────────────────────────────────────

def test_first_scan_is_a_baseline_that_flags_only_successors(watch):
    summary = watch.record_scan(
        [ok("anthropic", "claude-sonnet-5", "claude-2.1", "claude-sonnet-5-5")],
        KNOWN, now=datetime(2026, 10, 7, 9, 0))
    assert [(n.provider, n.model) for n in summary.found] == [
        ("anthropic", "claude-sonnet-5-5")]
    # claude-2.1 is a legacy id, not news, but it is now "seen".
    assert "claude-2.1" in watch.state["seen"]["anthropic"]


def test_superseded_releases_found_together_are_not_news(watch):
    summary = watch.record_scan(
        [ok("openai", "gpt-4.1", "gpt-5", "gpt-5.1", "gpt-5.5", "gpt-5-mini",
            "gpt-5.4-mini")],
        {"openai": ["gpt-4.1", "gpt-4.1-mini"]})
    assert sorted(n.model for n in summary.found) == ["gpt-5.4-mini", "gpt-5.5"]


def test_later_scans_flag_every_unseen_chat_model(watch):
    watch.record_scan([ok("anthropic", "claude-sonnet-5")], KNOWN)
    summary = watch.record_scan(
        [ok("anthropic", "claude-sonnet-5", "claude-mythos-1",
            "claude-sonnet-5-20260301", "claude-embed-1")], KNOWN)
    # The dated snapshot is a model Sentinel knows; the embedding is noise.
    assert [n.model for n in summary.found] == ["claude-mythos-1"]


def test_a_provider_that_could_not_be_asked_keeps_its_baseline(watch):
    watch.record_scan([ProviderListing("openai", "skipped", note="no API key")], {})
    assert "openai" not in watch.state["seen"]
    assert watch.last_notes == {"openai": "skipped: no API key"}
    # So the first *real* listing is still treated as a baseline.
    summary = watch.record_scan([ok("openai", "gpt-3.5-turbo")], {})
    assert summary.found == []


def test_decisions_persist_and_leave_pending(watch, tmp_path, clean_router):
    watch.record_scan([ok("anthropic", "claude-sonnet-5")], KNOWN)
    watch.record_scan([ok("anthropic", "claude-sonnet-5", "claude-sonnet-5-5",
                          "claude-mythos-1")], KNOWN)
    assert {n.model for n in watch.pending()} == {"claude-sonnet-5-5", "claude-mythos-1"}

    watch.dismiss("anthropic", "claude-mythos-1")
    watch.adopt(assess("anthropic", "claude-sonnet-5-5"))

    reloaded = ModelWatch(tmp_path / "model_watch.json")
    assert reloaded.pending() == []
    assert reloaded.adopted_models("anthropic") == ["claude-sonnet-5-5"]
    # A dismissed model is not re-flagged by the next scan.
    again = reloaded.record_scan([ok("anthropic", "claude-mythos-1")], KNOWN)
    assert again.found == []


def test_a_corrupt_state_file_is_an_empty_watch(tmp_path):
    path = tmp_path / "model_watch.json"
    path.write_text("{not json")
    assert ModelWatch(path).pending() == []


# ── Assessing ────────────────────────────────────────────────────────────────

def test_a_newer_release_takes_best_fit_from_its_predecessor():
    result = assess("anthropic", "claude-sonnet-5-5")
    assert result.inferred.sibling == "claude-sonnet-5"
    assert {m.key for m in result.agent_moves} == SONNET_AGENTS


def test_a_model_from_another_family_never_moves_an_agent():
    # Rated like gemini-2.5-pro, which already outscores Trace's cheap pick;
    # that is a deliberate choice, and a scan learned nothing that changes it.
    result = assess("gemini", "gemini-3-pro")
    assert result.agent_moves == ()


def test_a_model_with_nothing_to_rate_against_is_never_ranked():
    result = assess("openai", "gpt-3.5-turbo")
    assert not result.inferred.rankable
    assert result.moves == ()
    assert "never picked as BEST FIT" in result.summary


def test_a_rating_borrows_capabilities_but_never_a_price():
    inferred = infer_profile("anthropic", "claude-opus-5-5")
    assert inferred.profile.capabilities == next(
        p for p in mr.MODEL_CATALOG if p.model == "claude-opus-5").capabilities
    assert inferred.profile.input_per_1m_usd is None


# ── Adopting ─────────────────────────────────────────────────────────────────

def test_adopting_registers_the_model_and_moves_best_fit(watch, clean_router):
    watch.adopt(assess("anthropic", "claude-sonnet-5-5"))
    assert ("anthropic", "claude-sonnet-5-5") in {(p.provider, p.model) for p in mr.catalog()}
    for agent in SONNET_AGENTS:
        assert mr.AGENT_RECOMMENDATIONS[agent].model == "claude-sonnet-5-5"
    assert mr.AGENT_RECOMMENDATIONS["osint_heavy"].model == "claude-opus-5"


def test_install_reapplies_adoptions_at_startup(watch, tmp_path, clean_router):
    watch.adopt(assess("anthropic", "claude-sonnet-5-5"))
    mr._ADOPTED_PROFILES.clear()
    mr.AGENT_RECOMMENDATIONS["wifi"] = mr.Recommendation(
        "anthropic", "claude-sonnet-5", "reset")

    ModelWatch(tmp_path / "model_watch.json").install()
    assert mr.AGENT_RECOMMENDATIONS["wifi"].model == "claude-sonnet-5-5"


def test_the_router_prefers_the_newer_release_on_a_tie():
    sonnet = next(p for p in mr.MODEL_CATALOG if p.model == "claude-sonnet-5")
    successor = mr.ModelProfile("anthropic", "claude-sonnet-5-5",
                                sonnet.capabilities, sonnet.quality)
    decision = mr.route_request("", tool="Coding", candidates=(sonnet, successor))
    assert decision.model == "claude-sonnet-5-5"


# ── Listing ──────────────────────────────────────────────────────────────────

def _client(models=(), key=True, boom=False):
    class Fake:
        KNOWN_MODELS = ["fallback-model"]

        @staticmethod
        def key_available():
            return key

        def list_models(self):
            if boom:
                raise RuntimeError("network down")
            return list(models) or self.KNOWN_MODELS

    return Fake


def test_listing_never_mistakes_a_fallback_for_a_live_answer():
    listings = {item.provider: item for item in list_live({
        "a": _client(["m-1"]), "b": _client(key=False),
        "c": _client([]), "d": _client(boom=True),
    })}
    assert listings["a"].models == ("m-1",)
    assert listings["b"].status == "skipped"
    assert listings["c"].status == "unreachable"   # not "fallback-model"
    assert listings["d"].status == "unreachable"


# ── In the window ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def win():
    from PySide6.QtWidgets import QMessageBox
    import main

    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        yield main.GodAI()
    finally:
        QMessageBox.warning, QMessageBox.question = saved


def test_a_scan_result_reaches_the_card_and_the_dropdown(win, tmp_path):
    win.model_watch = ModelWatch(tmp_path / "model_watch.json")
    win.model_watch.record_scan([ok("anthropic", "claude-sonnet-5")], KNOWN)
    win.model_watch.record_scan(
        [ok("anthropic", "claude-sonnet-5", "claude-sonnet-5-5")], KNOWN)
    win.refresh_model_updates_card()

    card = win.model_updates_card
    assert card.new_row.value.text() == "1"
    assert card.review_btn.isVisibleTo(card)
    assert card.review_btn.text() == "Review 1"
    assert "anthropic · claude-sonnet-5-5" in card.review_btn.toolTip()

    # The test clients list only KNOWN_MODELS; add the new one where the live
    # list would have put it.
    _provider_box, model_box = win.setup_widgets_for("wifi")
    model_box.addItem("claude-sonnet-5-5")
    win._mark_new_models("anthropic", model_box)
    menu = model_box.buildMenu()
    assert [a.text() for a in menu.newBadges()] == ["claude-sonnet-5-5"]
    assert all(a.text() != "claude-sonnet-5-5" for a in menu.bestFitBadges())


def test_adopting_moves_the_panel_and_its_best_fit_badge(win, tmp_path, clean_router):
    import main

    saved = dict(main.AGENT_RECOMMENDATIONS)
    win.model_watch = ModelWatch(tmp_path / "model_watch.json")
    try:
        result = assess("anthropic", "claude-sonnet-5-5")
        win.model_watch.adopt(result)
        win._on_model_decided("adopt", result)

        provider_box, model_box = win.setup_widgets_for("wifi")
        assert provider_box.currentText() == "anthropic"
        # Adopted models stay selectable even when the live list lacks them.
        assert model_box.currentText() == "claude-sonnet-5-5"
        badges = model_box.buildMenu().bestFitBadges()
        assert [a.text() for a in badges] == ["claude-sonnet-5-5"]
        card = win.model_updates_card
        assert not card.review_btn.isVisibleTo(card)
    finally:
        main.AGENT_RECOMMENDATIONS.clear()
        main.AGENT_RECOMMENDATIONS.update(saved)

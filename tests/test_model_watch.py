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
    saved_lookup = mr._PRICE_LOOKUP
    yield
    mr.set_price_lookup(saved_lookup)
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
    ("claude-sonnet-6", "claude-sonnet-6"),
])
def test_snapshot_suffixes_are_not_new_models(model, expected):
    assert canonical(model) == expected


@pytest.mark.parametrize("model, family, version", [
    ("claude-sonnet-4-6", "claude-sonnet", (4, 6)),
    ("claude-sonnet-5-5", "claude-sonnet", (5, 5)),
    ("claude-sonnet-6", "claude-sonnet", (6,)),
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
    assert is_successor("anthropic", "claude-sonnet-6")
    assert not is_successor("anthropic", "claude-sonnet-5")     # older
    assert not is_successor("openai", "gpt-3.5-turbo")          # no family here


# ── Noticing ─────────────────────────────────────────────────────────────────

def test_first_scan_is_a_baseline_that_flags_only_successors(watch):
    summary = watch.record_scan(
        [ok("anthropic", "claude-sonnet-5", "claude-2.1", "claude-sonnet-6")],
        KNOWN, now=datetime(2026, 10, 7, 9, 0))
    assert [(n.provider, n.model) for n in summary.found] == [
        ("anthropic", "claude-sonnet-6")]
    # claude-2.1 is a legacy id, not news, but it is now "seen".
    assert "claude-2.1" in watch.state["seen"]["anthropic"]


def test_superseded_releases_found_together_are_not_news(watch):
    summary = watch.record_scan(
        [ok("openai", "gpt-5.5", "gpt-6", "gpt-6.1", "gpt-6.2", "gpt-6-mini",
            "gpt-6.1-mini")],
        {"openai": ["gpt-5.5", "gpt-5.4-mini"]})
    assert sorted(n.model for n in summary.found) == ["gpt-6.1-mini", "gpt-6.2"]


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
    watch.record_scan([ok("anthropic", "claude-sonnet-5", "claude-sonnet-6",
                          "claude-mythos-1")], KNOWN)
    assert {n.model for n in watch.pending()} == {"claude-sonnet-6", "claude-mythos-1"}

    watch.dismiss("anthropic", "claude-mythos-1")
    watch.adopt(assess("anthropic", "claude-sonnet-6"))

    reloaded = ModelWatch(tmp_path / "model_watch.json")
    assert reloaded.pending() == []
    assert reloaded.adopted_models("anthropic") == ["claude-sonnet-6"]
    # A dismissed model is not re-flagged by the next scan.
    again = reloaded.record_scan([ok("anthropic", "claude-mythos-1")], KNOWN)
    assert again.found == []


def test_a_corrupt_state_file_is_an_empty_watch(tmp_path):
    path = tmp_path / "model_watch.json"
    path.write_text("{not json")
    assert ModelWatch(path).pending() == []


# ── Assessing ────────────────────────────────────────────────────────────────

def priced(**rates):
    """A Settings → Pricing table holding only `rates` (model -> (in, out))."""
    return lambda provider, model: rates.get(model)


def test_newer_alone_wins_nothing(clean_router):
    # Rated like claude-sonnet-5-5 and unpriced: not better value anywhere.
    result = assess("anthropic", "claude-sonnet-6")
    assert result.inferred.sibling == "claude-sonnet-5-5"
    assert result.agent_moves == ()
    assert "Settings → Pricing" in result.summary


def test_a_newer_release_that_costs_more_wins_nothing(clean_router):
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (3.0, 15.0)}))
    assert assess("anthropic", "claude-sonnet-6").agent_moves == ()


def test_the_same_rating_for_less_takes_best_fit(clean_router):
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (1.0, 5.0)}))
    result = assess("anthropic", "claude-sonnet-6")
    assert {m.key for m in result.agent_moves} == SONNET_AGENTS
    assert "costs less" in result.agent_moves[0].reason


def test_a_cheaper_model_never_displaces_a_pick_with_an_unknown_price(clean_router):
    # Trace's deepseek-flash has no price in the catalog; nothing can be
    # shown to be cheaper than it, so nothing moves it.
    mr.set_price_lookup(priced(**{"gemini-4-flash": (0.01, 0.01)}))
    moves = assess("gemini", "gemini-4-flash").agent_moves
    assert "osint" not in {m.key for m in moves}


def test_a_model_with_nothing_to_rate_against_is_never_ranked():
    result = assess("openai", "gpt-3.5-turbo")
    assert not result.inferred.rankable
    assert result.moves == ()
    assert "never picked as BEST FIT" in result.summary


def test_a_rating_borrows_capabilities_but_never_a_price():
    inferred = infer_profile("anthropic", "claude-opus-6")
    assert inferred.profile.capabilities == next(
        p for p in mr.MODEL_CATALOG if p.model == "claude-opus-5-5").capabilities
    assert inferred.profile.input_per_1m_usd is None


# ── Adopting ─────────────────────────────────────────────────────────────────

def test_adopting_registers_the_model_and_moves_best_fit(watch, clean_router):
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (1.0, 5.0)}))
    watch.adopt(assess("anthropic", "claude-sonnet-6"))
    assert ("anthropic", "claude-sonnet-6") in {(p.provider, p.model) for p in mr.catalog()}
    for agent in SONNET_AGENTS:
        assert mr.AGENT_RECOMMENDATIONS[agent].model == "claude-sonnet-6"
    assert mr.AGENT_RECOMMENDATIONS["osint_heavy"].model == "claude-opus-5-5"


def test_install_reapplies_adoptions_at_startup(watch, tmp_path, clean_router):
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (1.0, 5.0)}))
    watch.adopt(assess("anthropic", "claude-sonnet-6"))
    mr._ADOPTED_PROFILES.clear()
    mr.AGENT_RECOMMENDATIONS["wifi"] = mr.Recommendation(
        "anthropic", "claude-sonnet-5-5", "reset")

    ModelWatch(tmp_path / "model_watch.json").install()
    assert mr.AGENT_RECOMMENDATIONS["wifi"].model == "claude-sonnet-6"


def test_on_a_tie_the_router_picks_the_cheaper_model_not_the_newer(clean_router):
    sonnet = next(p for p in mr.MODEL_CATALOG if p.model == "claude-sonnet-5-5")
    successor = mr.ModelProfile("anthropic", "claude-sonnet-6",
                                sonnet.capabilities, sonnet.quality)
    pair = (sonnet, successor)
    # Unpriced: never the cheap one.
    assert mr.route_request("", tool="Coding", candidates=pair).model == "claude-sonnet-5-5"
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (3.0, 15.0)}))
    assert mr.route_request("", tool="Coding", candidates=pair).model == "claude-sonnet-5-5"
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (1.0, 5.0)}))
    decision = mr.route_request("", tool="Coding", candidates=pair)
    assert decision.model == "claude-sonnet-6"
    assert "cheapest" in decision.reason


def test_a_zero_price_for_a_cloud_model_is_unknown_not_free():
    profile = mr.ModelProfile("gemini", "gemini-x", mr.ModelCapabilities())
    assert mr.blended_price(profile, priced(**{"gemini-x": (0.0, 0.0)})) is None
    local = next(p for p in mr.MODEL_CATALOG if p.provider == "ollama")
    assert mr.blended_price(local) == 0.0


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
        [ok("anthropic", "claude-sonnet-5", "claude-sonnet-6")], KNOWN)
    win.refresh_model_updates_card()

    card = win.model_updates_card
    assert card.status.text() == "1 new"
    assert card.light_state() == "warn"
    assert card.review_btn.isVisibleTo(card)
    row = card._rows[("anthropic", "claude-sonnet-6")]
    assert row.text() == "claude-sonnet-6"   # no checkbox; selection is the background

    # The test clients list only KNOWN_MODELS; add the new one where the live
    # list would have put it.
    _provider_box, model_box = win.setup_widgets_for("wifi")
    model_box.addItem("claude-sonnet-6")
    win._mark_new_models("anthropic", model_box)
    menu = model_box.buildMenu()
    assert [a.text() for a in menu.newBadges()] == ["claude-sonnet-6"]
    assert all(a.text() != "claude-sonnet-6" for a in menu.bestFitBadges())


def test_adopting_moves_the_panel_and_its_best_fit_badge(win, tmp_path, clean_router):
    import main

    saved = dict(main.AGENT_RECOMMENDATIONS)
    win.model_watch = ModelWatch(tmp_path / "model_watch.json")
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (1.0, 5.0)}))
    try:
        result = assess("anthropic", "claude-sonnet-6")
        win.model_watch.adopt(result)
        win._on_model_decided("adopt", result)

        provider_box, model_box = win.setup_widgets_for("wifi")
        assert provider_box.currentText() == "anthropic"
        # Adopted models stay selectable even when the live list lacks them.
        assert model_box.currentText() == "claude-sonnet-6"
        badges = model_box.buildMenu().bestFitBadges()
        assert [a.text() for a in badges] == ["claude-sonnet-6"]
        card = win.model_updates_card
        assert not card.review_btn.isVisibleTo(card)
    finally:
        main.AGENT_RECOMMENDATIONS.clear()
        main.AGENT_RECOMMENDATIONS.update(saved)


def test_clicking_a_row_marks_it_and_clicking_again_unmarks_it(win, tmp_path):
    win.model_watch = ModelWatch(tmp_path / "model_watch.json")
    win.model_watch.record_scan([ok("anthropic", "claude-sonnet-5")], KNOWN)
    win.model_watch.record_scan(
        [ok("anthropic", "claude-sonnet-5", "claude-sonnet-6", "claude-mythos-1")], KNOWN)
    win.refresh_model_updates_card()
    card = win.model_updates_card

    assert card.marked() == [] and not card.update_btn.isEnabled()
    card.toggle("anthropic", "claude-mythos-1")
    assert card.marked() == [("anthropic", "claude-mythos-1")]
    assert card.update_btn.text() == "Update 1"
    card.toggle("anthropic", "claude-mythos-1")
    assert card.marked() == [] and not card.update_btn.isEnabled()

    # A mark survives a refresh, as long as the model is still new.
    card.toggle("anthropic", "claude-sonnet-6")
    win.refresh_model_updates_card()
    assert card.marked() == [("anthropic", "claude-sonnet-6")]
    card.toggle("anthropic", "claude-sonnet-6")
    assert card.marked() == []


def test_update_adopts_only_the_marked_models(win, tmp_path, clean_router):
    import main

    saved = dict(main.AGENT_RECOMMENDATIONS)
    win.model_watch = ModelWatch(tmp_path / "model_watch.json")
    mr.set_price_lookup(priced(**{"claude-sonnet-6": (1.0, 5.0)}))
    try:
        win.model_watch.record_scan([ok("anthropic", "claude-sonnet-5")], KNOWN)
        win.model_watch.record_scan(
            [ok("anthropic", "claude-sonnet-5", "claude-sonnet-6", "claude-mythos-1")],
            KNOWN)
        win.refresh_model_updates_card()
        card = win.model_updates_card
        card.toggle("anthropic", "claude-sonnet-6")
        card.update_btn.click()

        assert win.model_watch.adopted_models("anthropic") == ["claude-sonnet-6"]
        assert [n.model for n in win.model_watch.pending()] == ["claude-mythos-1"]
        assert card.marked() == []
        assert win.setup_widgets_for("wifi")[1].currentText() == "claude-sonnet-6"
    finally:
        main.AGENT_RECOMMENDATIONS.clear()
        main.AGENT_RECOMMENDATIONS.update(saved)


# ── Shipped prices ───────────────────────────────────────────────────────────

def test_gemini_38_flash_moves_to_its_announced_price_on_the_day(tmp_path):
    import sqlite3
    from datetime import date

    from services import database as db

    assert db._gemini_38_flash_rate(date(2026, 12, 31)) == (0.75, 3.75)
    assert db._gemini_38_flash_rate(date(2027, 1, 1)) == (1.50, 7.50)

    conn = sqlite3.connect(tmp_path / "p.db")
    conn.execute("CREATE TABLE pricing (backend TEXT, model TEXT, "
                 "input_per_1m_usd REAL, output_per_1m_usd REAL)")
    conn.executemany("INSERT INTO pricing VALUES (?,?,?,?)", [
        ("gemini", "gemini-3.8-flash", 0.75, 3.75),
    ])
    db._apply_scheduled_price_changes(conn, today=date(2026, 12, 31))
    assert conn.execute("SELECT input_per_1m_usd FROM pricing").fetchone() == (0.75,)
    db._apply_scheduled_price_changes(conn, today=date(2027, 1, 1))
    assert conn.execute("SELECT input_per_1m_usd, output_per_1m_usd FROM pricing"
                        ).fetchone() == (1.50, 7.50)

    # A price the user typed in is theirs; the switch leaves it alone.
    conn.execute("UPDATE pricing SET input_per_1m_usd = 0.9, output_per_1m_usd = 4")
    db._apply_scheduled_price_changes(conn, today=date(2027, 6, 1))
    assert conn.execute("SELECT input_per_1m_usd FROM pricing").fetchone() == (0.9,)


def test_the_shipped_catalog_knows_the_current_releases():
    shipped = {(p.provider, p.model) for p in mr.MODEL_CATALOG}
    for pair in [("anthropic", "claude-sonnet-5-5"), ("anthropic", "claude-opus-5-5"),
                 ("openai", "gpt-5.5"), ("openai", "gpt-5.4-mini"),
                 ("gemini", "gemini-3.1-pro-preview"), ("gemini", "gemini-3.8-flash")]:
        assert pair in shipped
        assert mr.pricing_metadata(*pair).input_per_1m_usd is not None

"""Per-task ratings from LMArena, and the router choosing on them."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from services import benchmarks as b
from services import model_recommendations as mr


def table(rows_by_category: dict, published="2026-10-02") -> b.RatingTable:
    """rows: {category: [(name, score, org), ...]} in the text subset."""
    data = {"published": published, "fetched": "2026-10-07T09:00:00", "categories": {
        f"text_style_control/{category}": [
            [name, score, score - 5, score + 5, 5000, org] for name, score, org in rows]
        for category, rows in rows_by_category.items()}}
    return b.RatingTable(data, origin="snapshot")


@pytest.fixture
def router_state():
    saved = (mr._PRICE_LOOKUP, mr._RATING_LOOKUP, dict(mr._RECOMMENDATION_CONTEXT))
    yield
    mr.set_price_lookup(saved[0])
    mr.set_rating_lookup(saved[1])
    mr._RECOMMENDATION_CONTEXT.update(saved[2])


def prices(**rates):
    return lambda provider, model: rates.get(model)


# ── Matching names ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, key", [
    ("claude-opus-5.5-high", ("claude-opus-5-5", True)),
    ("claude-opus-5-5", ("claude-opus-5-5", False)),
    ("gpt-4.1-2025-04-14", ("gpt-4-1", False)),
    ("claude-haiku-4-5-20251001", ("claude-haiku-4-5", False)),
    ("gemini-2.5-flash-preview-09-2025", ("gemini-2-5-flash", False)),
    ("deepseek-v4-pro-high-20260813", ("deepseek-v4-pro", True)),
    ("qwen-plus-0125", ("qwen-plus", False)),
    ("muse-spark-1.2 (xHigh)", ("muse-spark-1-2", False)),
])
def test_arena_names_normalise_to_provider_ids(name, key):
    assert b.arena_key(name) == key


def test_a_plain_entry_beats_an_effort_variant():
    t = table({"coding": [("gpt-5.5-high", 1519, "openai"), ("gpt-5.5", 1510, "openai")]})
    assert t.rating("openai", "gpt-5.5", "coding").score == 1510


def test_without_a_plain_entry_the_lowest_variant_is_used():
    # The app calls a model with default settings; it must not be credited
    # with the best effort level someone else paid for.
    t = table({"coding": [("claude-opus-5-high", 1533, "anthropic"),
                          ("claude-opus-5-max", 1530, "anthropic")]})
    assert t.rating("anthropic", "claude-opus-5", "coding").score == 1530


def test_aliases_local_models_and_other_vendors():
    t = table({"coding": [("deepseek-v4-flash", 1484, "deepseek"),
                          ("muse-glimmer", 1479, ""),
                          ("qwen3-max", 1600, "someone-else")]})
    assert t.rating("deepseek", "deepseek-flash", "coding").score == 1484
    assert t.rating("ollama", "muse-glimmer:30b-mlx", "coding") is None
    assert t.rating("qwen", "qwen3-max", "coding") is None
    assert t.rating("deepseek", "deepseek-flash", "image_generation") is None


def test_the_shipped_snapshot_rates_the_catalog():
    t = b.load(b.SNAPSHOT_FILE.with_name("missing.json"))
    assert t.origin == "snapshot" and t.published
    for provider, model in [("anthropic", "claude-sonnet-5-5"), ("openai", "gpt-5.5"),
                            ("gemini", "gemini-3.8-flash"), ("deepseek", "deepseek-v4-pro")]:
        for task in ("coding", "writing", "research", "general", "reasoning"):
            assert t.rating(provider, model, task) is not None, (model, task)


# ── Choosing on ratings ──────────────────────────────────────────────────────

RATED = {"coding": [("claude-opus-5.5-high", 1538, "anthropic"),
                    ("claude-sonnet-5.5-xhigh", 1536, "anthropic"),
                    ("gemini-3.8-flash-high", 1530, "google"),
                    ("gpt-4.1-mini-2025-04-14", 1433, "openai")]}
PRICES = prices(**{"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10),
                   "gemini-3.8-flash": (0.75, 3.75), "gpt-4.1-mini": (0.4, 1.6)})
CLOUD = {"anthropic", "gemini", "openai"}


def test_the_cheapest_good_enough_model_wins(router_state):
    mr.set_rating_lookup(table(RATED).rating)
    mr.set_price_lookup(PRICES)
    decision = mr.route_request("fix this python function", enabled_providers=CLOUD)
    assert decision.basis == "rating"
    assert decision.model == "gemini-3.8-flash"
    assert "within 20 points" in decision.reason and "claude-opus-5-5" in decision.reason


def test_cheap_but_not_good_enough_does_not_win(router_state):
    # gpt-4.1-mini is the cheapest of all, but 105 points behind.
    mr.set_rating_lookup(table(RATED).rating)
    mr.set_price_lookup(PRICES)
    assert mr.route_request("debug this code", enabled_providers={"anthropic", "openai"}
                            ).model == "claude-sonnet-5-5"


def test_quality_first_takes_the_top_rated(router_state):
    mr.set_rating_lookup(table(RATED).rating)
    mr.set_price_lookup(PRICES)
    decision = mr.route_request("debug this code", enabled_providers=CLOUD,
                                preferences=mr.RoutingPreferences(priority="quality"))
    assert decision.model == "claude-opus-5-5"


def test_an_unknown_price_is_never_the_cheap_one(router_state):
    mr.set_rating_lookup(table(RATED).rating)
    mr.set_price_lookup(prices(**{"claude-opus-5-5": (4, 20), "gemini-3.8-flash": (0, 0)}))
    decision = mr.route_request("debug this code", enabled_providers={"anthropic", "gemini"})
    assert decision.model != "gemini-3.8-flash"


def test_without_ratings_or_with_privacy_first_the_old_score_decides(router_state):
    mr.set_rating_lookup(None)
    assert mr.route_request("debug this code").basis == "score"
    mr.set_rating_lookup(table(RATED).rating)
    privacy = mr.RoutingPreferences(priority="privacy")
    assert mr.route_request("debug this code", preferences=privacy).basis == "score"


def test_agent_picks_are_derived_from_the_router(router_state):
    mr.set_rating_lookup(table(RATED).rating)
    mr.set_price_lookup(PRICES)
    derived = mr.derive_agent_recommendations(enabled_providers=CLOUD)
    assert derived["wifi"].model == "gemini-3.8-flash"           # coding: rated
    assert derived["osint"] == mr.STATIC_AGENT_RECOMMENDATIONS["osint"]  # research: unrated


def test_a_new_model_is_assessed_by_the_same_rule(router_state):
    from services.model_watch import assess

    rows = {"coding": RATED["coding"] + [("gemini-4-flash-high", 1535, "google")]}
    mr.set_rating_lookup(table(rows).rating)
    mr.set_recommendation_context(CLOUD, None)

    mr.set_price_lookup(PRICES)                       # gemini-4-flash: no price
    assert assess("gemini", "gemini-4-flash").agent_moves == ()

    mr.set_price_lookup(prices(**{**{m: PRICES("", m) for m in
                                     ("claude-opus-5-5", "claude-sonnet-5-5",
                                      "gemini-3.8-flash", "gpt-4.1-mini")},
                                  "gemini-4-flash": (0.5, 2.0)}))
    moves = assess("gemini", "gemini-4-flash").agent_moves
    assert {m.key for m in moves} == {"wifi", "bug_bounty", "manager", "vpn", "sentry"}


# ── Fetching, caching, falling back ──────────────────────────────────────────

def _page(rows, total):
    return {"num_rows_total": total, "rows": [{"row": r} for r in rows]}


def _row(name, category, org="openai", rating=1500.0):
    return {"model_name": name, "category": category, "organization": org,
            "rating": rating, "rating_lower": rating - 4, "rating_upper": rating + 4,
            "vote_count": 900, "leaderboard_publish_date": "2026-10-05"}


def _dataset(text, vision):
    """A fake row pager over two subsets; records every request it serves."""
    from urllib.parse import parse_qs, urlparse

    calls = []

    def get(url):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        rows = text if query["config"] == "text_style_control" else vision
        offset, length = int(query["offset"]), int(query["length"])
        calls.append((query["config"], offset))
        return _page(rows[offset:offset + length], len(rows))

    return get, calls


TEXT = ([_row(f"gpt-o{i}", "overall") for i in range(150)]
        + [_row(f"gpt-c{i}", "coding") for i in range(120)]
        + [_row("gpt-x", "creative_writing"), _row("gpt-x", "hard_prompts"),
           _row("gpt-x", "longer_query"), _row("gpt-y", "coding", org="mistral")]
        + [_row(f"gpt-z{i}", "industry_legal_and_government") for i in range(400)])
VISION = [_row("gpt-v", "overall")] * 5 + [_row("gpt-v", "ocr")] * 300


def test_fetch_keeps_the_needed_categories_and_stops_once_they_have_passed():
    get, calls = _dataset(TEXT, VISION)
    data = b.fetch_live(get=get, sleep=lambda s: None)
    tables = data["categories"]
    assert len(tables["text_style_control/overall"]) == 150
    assert len(tables["text_style_control/coding"]) == 120      # mistral row dropped
    assert len(tables["vision_style_control/overall"]) == 5
    assert data["published"] == "2026-10-05" and data["missing"] == []
    # Vision's overall run ends inside the first page; nothing after it is read.
    assert [c for c in calls if c[0] == "vision_style_control"] == [("vision_style_control", 0)]
    assert max(o for c, o in calls if c == "text_style_control") < len(TEXT) - 100


def test_fetch_retries_a_failing_page_and_paces_its_requests():
    get, _calls = _dataset(TEXT, VISION)
    failures = iter([True])
    sleeps = []

    def flaky(url):
        if next(failures, False):
            return {"error": "temporarily unavailable"}
        return get(url)

    b.fetch_live(get=flaky, sleep=sleeps.append)
    assert sleeps[0] == 10.0                 # the retry backoff
    assert set(sleeps[1:]) == {1.0}          # the pause between pages


def test_fetch_gives_up_with_a_reason():
    with pytest.raises(RuntimeError, match="index is loading"):
        b.fetch_live(get=lambda url: {"error": "the dataset index is loading"},
                     sleep=lambda s: None)


def test_one_failing_subset_keeps_the_others_and_the_previous_copy(tmp_path):
    get, _calls = _dataset([_row("gpt-5.5", "coding", rating=1600.0)], VISION)

    def no_vision(url):
        return {"error": "down"} if "vision" in url else get(url)

    fresh = b.refresh(tmp_path / "ratings.json", get=no_vision, sleep=lambda s: None)
    assert fresh.rating("openai", "gpt-5.5", "coding").score == 1600.0
    # Vision did not arrive; the shipped snapshot's vision table stands in.
    assert fresh.rating("openai", "gpt-5.5", "vision") is not None


def test_cache_wins_over_snapshot_and_goes_stale_after_a_day(tmp_path):
    # is_stale itself is pinned to False for the suite (tests/conftest.py),
    # so a test's event loop never starts a fetch; its logic is tested here.
    cache = tmp_path / "ratings.json"
    assert b.load(cache).origin == "snapshot"
    assert b._older_than_max_age(cache)
    get, _calls = _dataset([_row("gpt-5.5", "coding")], [_row("gpt-5.5", "overall")])
    b.refresh(cache, get=get, sleep=lambda s: None)
    assert b.load(cache).origin == "cache"
    fetched = datetime.fromisoformat(json.loads(cache.read_text())["fetched"])
    assert not b._older_than_max_age(cache, now=fetched + timedelta(hours=23))
    assert b._older_than_max_age(cache, now=fetched + timedelta(hours=25))



def test_a_rate_limit_stops_at_once_and_keeps_finished_categories(tmp_path):
    import urllib.error

    get, calls = _dataset(TEXT, VISION)

    def limited(url):
        if "text_style_control" in url and "offset=200" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return get(url)

    sleeps = []
    data = b.fetch_live(get=limited, sleep=sleeps.append)
    assert 10.0 not in sleeps                # no retry on a 429
    # overall (rows 0-149) ended inside page 2; coding did not finish.
    assert "text_style_control/overall" in data["categories"]
    assert "text_style_control/coding" in data["missing"]
    assert "text_style_control/coding" not in data["categories"]


def test_a_partial_fetch_keeps_its_publish_date(tmp_path):
    import urllib.error

    get, _calls = _dataset(TEXT, VISION)

    def limited(url):
        if "offset=200" in url or "vision" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return get(url)

    data = b.fetch_live(get=limited, sleep=lambda s: None)
    assert data["published"] == "2026-10-05"

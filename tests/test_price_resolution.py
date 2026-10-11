"""One price per model, for the bill and the router (services/price_resolution.py).

Found 2026-10-08 by billing 10k input + 2k output tokens on a fresh database:
gemini-2.5-flash and -pro billed EUR 0 (the Gemini default row was 0/0),
gpt-4o at gpt-4o-mini's rate, deepseek-v4-pro at the retired deepseek-chat
rate and claude-fable-5-1 at Sonnet 4.6's. The same function prices the
pre-flight estimate, so the budget gate let those requests through. The
router meanwhile called the same models "price unknown".
"""

from __future__ import annotations

import pytest

from services import database
from services import model_recommendations as mr
from services.anthropic_client import AnthropicClientWrapper
from services.deepseek_client import DeepSeekClientWrapper
from services.gemini_client import GeminiClientWrapper
from services.kimi_client import KimiClientWrapper
from services.openai_client import OpenAIClientWrapper
from services.price_resolution import resolve_price, resolve_price_row, table_lookup
from services.qwen_client import QwenClientWrapper
from services.usage_tracker import UsageTracker

EUR_PER_USD = 0.92
CLIENTS = {
    "anthropic": AnthropicClientWrapper, "openai": OpenAIClientWrapper,
    "gemini": GeminiClientWrapper, "deepseek": DeepSeekClientWrapper,
    "kimi": KimiClientWrapper, "qwen": QwenClientWrapper,
}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A new database, initialised exactly as a first launch does."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "sentinel.db")
    database.init_db()
    return database


def row(i, o):
    return {"input_per_1m_usd": i, "output_per_1m_usd": o}


def eur(input_usd, output_usd, *, tokens_in=10_000, tokens_out=2_000):
    return (tokens_in * input_usd + tokens_out * output_usd) / 1e6 * EUR_PER_USD


# ── The bill ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider, model, rates", [
    # Official list rates, checked 2026-10-08 (see services/database.py).
    ("gemini", "gemini-2.5-flash", (0.30, 2.50)),       # was EUR 0
    ("gemini", "gemini-2.5-pro", (1.25, 10.00)),        # was EUR 0
    ("openai", "gpt-4o", (2.50, 10.00)),                # was ~1/16
    ("deepseek", "deepseek-v4-pro", (1.32, 3.96)),      # was ~1/11
    ("anthropic", "claude-fable-5-1", (10.00, 50.00)),  # was EUR 0.055, not 0.184
])
def test_the_models_that_billed_low_bill_at_their_list_rate(fresh_db, provider, model, rates):
    cost = UsageTracker().calculate_cost_eur(provider, model, 10_000, 2_000)
    assert cost == pytest.approx(eur(*rates), abs=1e-6)


def test_a_dated_snapshot_bills_as_the_model_it_is_a_snapshot_of(fresh_db):
    tracker = UsageTracker()
    bill = lambda p, m: tracker.calculate_cost_eur(p, m, 10_000, 2_000)  # noqa: E731
    assert bill("openai", "gpt-4o-2024-08-06") == bill("openai", "gpt-4o")
    assert bill("anthropic", "claude-haiku-4-5") == bill("anthropic", "claude-haiku-4-5-20251001")
    assert bill("gemini", "gemini-3.1-pro") == bill("gemini", "gemini-3.1-pro-preview")
    # A snapshot OpenAI prices above its alias keeps its own, dearer row.
    assert bill("openai", "gpt-4o-2024-05-13") == pytest.approx(eur(5.00, 15.00), abs=1e-6)


def test_a_model_without_a_row_bills_at_the_dearest_rate_of_its_provider(fresh_db):
    """Over- rather than under-estimated: no shipped model costs more."""
    tracker = UsageTracker()
    for provider, (dearest_in, dearest_out) in database.DEFAULT_PRICES.items():
        unknown = tracker.calculate_cost_eur(provider, f"{provider}-model-from-2027", 10_000, 2_000)
        assert unknown == pytest.approx(eur(dearest_in, dearest_out), abs=1e-6), provider
        for profile in mr.MODEL_CATALOG:
            if profile.provider == provider:
                shipped = tracker.calculate_cost_eur(provider, profile.model, 10_000, 2_000)
                assert shipped <= unknown, profile.model


def test_every_shipped_model_has_a_price_of_its_own(fresh_db):
    """The bug was models with no row of their own; none may fall to a default."""
    shipped = {(p.provider, p.model) for p in mr.MODEL_CATALOG if not p.capabilities.local}
    for provider, client in CLIENTS.items():
        shipped |= {(provider, model) for model in client.KNOWN_MODELS}
    with database.get_connection() as conn:
        for provider, model in sorted(shipped):
            _row, source = resolve_price_row(conn, provider, model)
            assert source in {"exact", "alias"}, (provider, model, source)


def test_zero_means_unknown_never_free():
    assert resolve_price({"m": row(0, 0), "default": row(2, 8)}, "m") == (row(2, 8), "default")
    assert resolve_price({"m": row(0, 5)}, "m") == (None, "unknown")
    assert resolve_price({"default": row(0, 0)}, "anything") == (None, "unknown")
    assert resolve_price({}, "anything") == (None, "unknown")


def test_an_undated_alias_wins_over_a_dearer_snapshot_of_the_same_name():
    rows = {"gpt-4o": row(2.5, 10), "gpt-4o-2024-05-13": row(5, 15)}
    assert resolve_price(rows, "gpt-4o-2024-11-20") == (row(2.5, 10), "alias")
    # With no undated row, the dearest snapshot stands in.
    rows = {"m-2026-01-01": row(1, 2), "m-2026-06-01": row(3, 4)}
    assert resolve_price(rows, "m-2026-09-01") == (row(3, 4), "alias")


# ── The router agrees with the bill ──────────────────────────────────────────

@pytest.mark.parametrize("provider, model", [
    ("openai", "gpt-4o"), ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-pro"),
    ("deepseek", "deepseek-v4-pro"), ("deepseek", "deepseek-flash"),
    ("anthropic", "claude-fable-5-1"), ("openai", "gpt-4o-2024-08-06"),
    ("openai", "gpt-9"),                                 # provider default
])
def test_the_router_weighs_a_model_at_the_price_it_is_billed(fresh_db, provider, model):
    lookup = table_lookup(UsageTracker().load_pricing())
    profile = next((p for p in mr.MODEL_CATALOG if (p.provider, p.model) == (provider, model)),
                   mr.ModelProfile(provider, model, mr.ModelCapabilities()))
    routed = mr.blended_price(profile, lookup)
    # 3M input + 1M output is the router's 3:1 blend, times four.
    billed = UsageTracker().calculate_cost_eur(provider, model, 3_000_000, 1_000_000)
    assert routed is not None
    assert routed == pytest.approx(billed / EUR_PER_USD / 4, abs=1e-6)


def test_the_cost_readout_shows_the_billed_rate_and_says_when_it_is_a_default(fresh_db, monkeypatch):
    monkeypatch.setattr(mr, "_PRICE_LOOKUP", table_lookup(UsageTracker().load_pricing()))
    assert mr.pricing_metadata("openai", "gpt-4o").compact == "PAID · $2.5/$10 per 1M in/out"
    assert mr.pricing_metadata("openai", "gpt-9").compact == (
        "PAID · $10/$50 per 1M in/out (provider default)")


def test_a_route_priced_at_a_default_says_so(fresh_db):
    lookup = table_lookup(UsageTracker().load_pricing())
    caps = mr.ModelCapabilities(reasoning=3, coding=3, tool_use=True, context_window=200_000)
    decision = mr.route_request("", tool="Coding", prices=lookup,
                                candidates=(mr.ModelProfile("openai", "gpt-9", caps, 3),))
    assert "its provider's default rate" in decision.reason


# ── Correcting an existing database ──────────────────────────────────────────

def _rates(conn, backend, model):
    found = conn.execute(
        "SELECT input_per_1m_usd, output_per_1m_usd, cached_input_per_1m_usd "
        "FROM pricing WHERE backend = ? AND model = ?", (backend, model)).fetchone()
    return tuple(found) if found else None


def _as_shipped_before(conn):
    """Put back what a database from before 2026-10-08 held."""
    conn.execute("DELETE FROM settings WHERE key = 'pricing_correction_2026_10'")
    for backend, model, (old_in, old_out, old_cached), _new in database.PRICING_CORRECTIONS_2026_10:
        conn.execute("UPDATE pricing SET input_per_1m_usd = ?, output_per_1m_usd = ?, "
                     "cached_input_per_1m_usd = ? WHERE backend = ? AND model = ?",
                     (old_in, old_out, old_cached, backend, model))
    conn.execute("UPDATE pricing SET input_per_1m_usd = 0, output_per_1m_usd = 0 "
                 "WHERE backend = 'gemini' AND model = 'gemini-2.5-flash'")
    for model in ("gemini-1.5-flash", "gemini-1.5-pro"):
        conn.execute("INSERT OR REPLACE INTO pricing (backend, model, input_per_1m_usd, "
                     "output_per_1m_usd) VALUES ('gemini', ?, 0, 0)", (model,))
    conn.commit()


def test_an_existing_database_takes_the_corrected_rates(fresh_db):
    conn = database.get_connection()
    _as_shipped_before(conn)
    database._correct_pricing_2026_10(conn)
    assert _rates(conn, "gemini", "default") == (4.0, 18.0, None)
    assert _rates(conn, "openai", "default") == (10.0, 50.0, None)
    assert _rates(conn, "anthropic", "default") == (10.0, 50.0, None)
    assert _rates(conn, "deepseek", "default") == (1.32, 3.96, None)
    assert _rates(conn, "kimi", "default") == (3.0, 15.0, 0.30)
    assert _rates(conn, "qwen", "qwen3.8-max") == (2.0, 6.0, None)
    assert _rates(conn, "gemini", "gemini-2.5-flash") == (0.30, 2.50, None)
    assert _rates(conn, "gemini", "gemini-1.5-flash") is None
    assert _rates(conn, "gemini", "gemini-1.5-pro") is None


def test_a_rate_the_user_typed_is_never_corrected(fresh_db):
    conn = database.get_connection()
    _as_shipped_before(conn)
    conn.execute("UPDATE pricing SET input_per_1m_usd = 0.5, output_per_1m_usd = 1.5 "
                 "WHERE backend = 'openai' AND model = 'default'")
    conn.execute("UPDATE pricing SET input_per_1m_usd = 0.1, output_per_1m_usd = 0.4 "
                 "WHERE backend = 'gemini' AND model = 'gemini-1.5-pro'")
    database._correct_pricing_2026_10(conn)
    assert _rates(conn, "openai", "default")[:2] == (0.5, 1.5)
    assert _rates(conn, "gemini", "gemini-1.5-pro")[:2] == (0.1, 0.4)

    # And it runs once: a price set back to the old value afterwards stays.
    conn.execute("UPDATE pricing SET input_per_1m_usd = 3, output_per_1m_usd = 15 "
                 "WHERE backend = 'anthropic' AND model = 'default'")
    database._correct_pricing_2026_10(conn)
    assert _rates(conn, "anthropic", "default")[:2] == (3.0, 15.0)


def test_the_shipped_json_seeds_the_same_rates_as_the_code(fresh_db):
    """A new database reads config/pricing.json first; it must not disagree."""
    import json
    from pathlib import Path

    shipped = json.loads((Path(__file__).resolve().parents[1] / "config" / "pricing.json").read_text())
    seeded = {(b, m): (i, o) for b, m, i, o in database._seeded_prices()}
    for backend, models in shipped.items():
        if not isinstance(models, dict):
            continue
        for model, rates in models.items():
            if (backend, model) in seeded:
                assert (rates["input_per_1m_usd"], rates["output_per_1m_usd"]) == \
                    seeded[(backend, model)], (backend, model)


def test_blank_dashscope_base_url_means_the_default(monkeypatch):
    from services import qwen_client
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "")
    assert qwen_client._base_url() == qwen_client.INTL_BASE_URL
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "  ")
    assert qwen_client._base_url() == qwen_client.INTL_BASE_URL
    monkeypatch.setenv("DASHSCOPE_BASE_URL", qwen_client.CHINA_BASE_URL)
    assert qwen_client._base_url() == qwen_client.CHINA_BASE_URL

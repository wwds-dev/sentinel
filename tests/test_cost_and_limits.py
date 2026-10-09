"""
Sentinel AI — Cost & Permission Tests
=====================================
Type: Unit tests of the logic that decides whether a request may run and what
it costs.

Why this file exists: the agent scenario tests cover prompt construction, but
nothing covered the money path — the budget gate and the token/cost maths. That
is the part where a bug spends real money, so it is the part most worth pinning
down. See TODO.md #6.

`Validator` takes its registry by injection, so these tests use a stub and never
touch the database. `UsageTracker.calculate_cost_eur` does read the pricing
table, so it is asserted on invariants that hold for any pricing data rather
than on hardcoded prices.

Run with:  pytest tests/test_cost_and_limits.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.validator import Validator
from services.usage_tracker import UsageTracker


# ─────────────────────────────────────────────────────────────────────────────
# Stub registry — mirrors the methods Validator calls, nothing more.
# ─────────────────────────────────────────────────────────────────────────────
class StubRegistry:
    def __init__(self, **overrides):
        self.agent_enabled = overrides.get("agent_enabled", True)
        self.tool_enabled = overrides.get("tool_enabled", True)
        self.agent_providers = overrides.get("agent_providers", [])   # [] == all
        self.tool_providers = overrides.get("tool_providers", [])
        self.agent_tools = overrides.get("agent_tools", None)         # None == all
        self.agent_budget = overrides.get("agent_budget", None)
        self.tool_budget = overrides.get("tool_budget", None)
        self.requires_approval = overrides.get("requires_approval", False)
        self.tool_approval = overrides.get("tool_approval", False)

    def is_agent_enabled(self, name):
        return self.agent_enabled

    def is_tool_enabled(self, name):
        return self.tool_enabled

    def agent_allows_provider(self, agent, provider):
        return not self.agent_providers or provider in self.agent_providers

    def tool_allows_provider(self, tool, provider):
        return not self.tool_providers or provider in self.tool_providers

    def agent_allows_tool(self, agent, tool):
        return self.agent_tools is None or tool in self.agent_tools

    def get_agent_budget(self, agent):
        return self.agent_budget

    def get_tool_budget(self, tool):
        return self.tool_budget

    def agent_requires_approval(self, agent):
        return self.requires_approval

    def tool_requires_approval(self, tool):
        return self.tool_approval


ALL_PERMS = {
    "allow_openai": True, "allow_deepseek": True, "allow_kimi": True,
    "allow_gemini": True, "allow_anthropic": True, "allow_qwen": True,
}
NO_PERMS = {k: False for k in ALL_PERMS}


def check(registry=None, **kwargs):
    """Run validate() with sensible defaults, overridden per test."""
    args = dict(
        agent_name="chat",
        tool_name="General Chat",
        provider="openai",
        api_permissions=ALL_PERMS,
        session_cost=0.0,
        session_budget=1.0,
        daily_cost=0.0,
        daily_budget=5.0,
        estimated_cost=0.01,
    )
    args.update(kwargs)
    return Validator(registry or StubRegistry()).validate(**args)


# ─────────────────────────────────────────────────────────────────────────────
# The happy path
# ─────────────────────────────────────────────────────────────────────────────
def test_allows_a_normal_request():
    result = check()
    assert result.allowed
    assert result.reason == "OK"


def test_tool_budget_blocks_paid_request_over_cap():
    result = check(StubRegistry(tool_budget=0.005), estimated_cost=0.01)
    assert not result.allowed
    assert "Tool 'General Chat' has a budget cap" in result.reason
    assert "per paid request" in result.reason


def test_tool_budget_does_not_block_free_local_request():
    result = check(StubRegistry(tool_budget=0.0), provider="ollama", estimated_cost=99.0)
    assert result.allowed


def test_tool_approval_requirement_blocks_request():
    result = check(StubRegistry(tool_approval=True))
    assert not result.allowed
    assert "Tool 'General Chat' requires manual approval" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# Registry gates
# ─────────────────────────────────────────────────────────────────────────────
def test_disabled_agent_is_refused():
    assert not check(StubRegistry(agent_enabled=False)).allowed


def test_disabled_tool_is_refused():
    assert not check(StubRegistry(tool_enabled=False)).allowed


def test_provider_not_permitted_for_agent_is_refused():
    registry = StubRegistry(agent_providers=["ollama"])
    assert not check(registry, provider="openai").allowed
    assert check(registry, provider="ollama").allowed


def test_provider_not_permitted_for_tool_is_refused():
    assert not check(StubRegistry(tool_providers=["ollama"]), provider="openai").allowed


def test_tool_not_permitted_for_agent_is_refused():
    assert not check(StubRegistry(agent_tools=["Summarize"]), tool_name="Coding").allowed


def test_agent_requiring_approval_is_refused():
    assert not check(StubRegistry(requires_approval=True)).allowed


def test_empty_tool_name_skips_the_tool_checks():
    """Agent panels pass no tool — their mode names are not registry entries.

    Regression guard: passing the panel's mode ("Person", "Deep Scan", …) as
    tool_name made every such request fail as a disabled tool.
    """
    registry = StubRegistry(tool_enabled=False, tool_providers=["nothing"])
    assert check(registry, tool_name=None).allowed


# ─────────────────────────────────────────────────────────────────────────────
# API permission checkboxes
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "provider", ["openai", "deepseek", "kimi", "gemini", "anthropic", "qwen"]
)
def test_paid_provider_requires_its_checkbox(provider):
    assert not check(provider=provider, api_permissions=NO_PERMS).allowed
    assert check(provider=provider, api_permissions=ALL_PERMS).allowed


def test_ollama_needs_no_permission():
    """Local execution is free, so it bypasses the permission checkboxes."""
    assert check(provider="ollama", api_permissions=NO_PERMS).allowed


# ─────────────────────────────────────────────────────────────────────────────
# Budgets — the checks that actually stop money being spent
# ─────────────────────────────────────────────────────────────────────────────
def test_request_over_session_budget_is_refused():
    result = check(session_cost=0.99, session_budget=1.0, estimated_cost=0.50)
    assert not result.allowed
    assert "Session budget" in result.reason


def test_request_over_daily_budget_is_refused():
    result = check(daily_cost=4.99, daily_budget=5.0, estimated_cost=0.50)
    assert not result.allowed
    assert "Daily budget" in result.reason


def test_request_over_the_per_agent_cap_is_refused():
    result = check(StubRegistry(agent_budget=0.10), estimated_cost=0.25)
    assert not result.allowed
    assert "budget cap" in result.reason


def test_request_exactly_at_the_remaining_budget_is_allowed():
    """The rule is `estimated > remaining`, so spending the last cent is fine."""
    assert check(session_cost=0.5, session_budget=1.0, estimated_cost=0.5).allowed


def test_decimal_budget_boundary_is_not_refused_by_float_noise():
    assert check(
        session_cost=0.9,
        session_budget=1.0,
        estimated_cost=0.1,
    ).allowed
    assert check(
        daily_cost=4.9,
        daily_budget=5.0,
        estimated_cost=0.1,
    ).allowed


def test_ollama_ignores_every_budget():
    """A local request costs nothing, so an exhausted budget must not block it."""
    result = check(
        provider="ollama",
        session_cost=99.0, session_budget=1.0,
        daily_cost=99.0, daily_budget=5.0,
        estimated_cost=0.0,
    )
    assert result.allowed


# ─────────────────────────────────────────────────────────────────────────────
# Token accounting — decides what the user is billed for
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def tracker():
    return UsageTracker()


def test_missing_usage_falls_back_to_an_estimate(tracker):
    i, o, kind = tracker.normalize_usage(None, "x" * 400, "y" * 800)
    assert kind == "estimated"
    assert (i, o) == (100, 200)          # ~4 chars per token


def test_real_usage_is_reported_as_exact(tracker):
    usage = {"input_tokens": 123, "output_tokens": 456}
    assert tracker.normalize_usage(usage, "prompt", "response") == (123, 456, "exact")


@pytest.mark.parametrize("keys", [
    ("input_tokens", "output_tokens"),           # anthropic / generic
    ("prompt_tokens", "completion_tokens"),      # openai-compatible
    ("prompt_token_count", "candidates_token_count"),  # gemini
])
def test_each_sdk_token_naming_is_understood(tracker, keys):
    """Every provider names these differently; all must be read correctly."""
    usage = {keys[0]: 10, keys[1]: 20}
    assert tracker.normalize_usage(usage, "p", "r") == (10, 20, "exact")


def test_partial_usage_is_marked_mixed(tracker):
    """A provider reporting only one side must not bill the other as zero."""
    i, o, kind = tracker.normalize_usage({"input_tokens": 50}, "p" * 40, "r" * 80)
    assert (i, kind) == (50, "mixed")
    assert o > 0


def test_token_estimate_never_returns_zero(tracker):
    """Zero tokens would silently make a request look free."""
    assert tracker.estimate_tokens("", "") == (1, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Cost — asserted as invariants, so local pricing data cannot break these
# ─────────────────────────────────────────────────────────────────────────────
def test_local_execution_is_always_free(tracker):
    assert tracker.calculate_cost_eur("ollama", "deepseek-r1:8b", 10**6, 10**6) == 0.0


def test_unknown_cloud_price_is_never_free(tracker):
    """README: a rate of zero means unknown, never free, and a cloud model with
    no usable price is over- rather than under-estimated. This used to return
    0.0 and bill €0.00 for any provider whose rows were blank."""
    from services.usage_tracker import UNKNOWN_PRICE_ROW
    cost = tracker.calculate_cost_eur("no-such-backend", "no-such-model", 1_000_000, 1_000_000)
    assert cost > 0.0
    # At least the fixed ceiling (the dearest table row may be higher still).
    floor = (UNKNOWN_PRICE_ROW["input_per_1m_usd"] + UNKNOWN_PRICE_ROW["output_per_1m_usd"]) * 0.5
    assert cost >= floor


def test_unknown_cloud_price_uses_the_dearest_row_in_the_table(tracker, monkeypatch):
    from services import usage_tracker as mod
    rows = [
        {"backend": "a", "model": "cheap", "input_per_1m_usd": 0.1, "cached_input_per_1m_usd": 0, "output_per_1m_usd": 0.4},
        {"backend": "b", "model": "dear", "input_per_1m_usd": 20.0, "cached_input_per_1m_usd": 0, "output_per_1m_usd": 80.0},
        {"backend": "c", "model": "blank", "input_per_1m_usd": 0, "cached_input_per_1m_usd": 0, "output_per_1m_usd": 0},
    ]
    class Conn:
        def execute(self, sql, params=()):
            class R:
                def fetchall(s): return rows
                def fetchone(s): return None
            return R()
    assert mod.dearest_price_row(Conn())["model"] == "dear"
    assert mod.dearest_price_row(type("C", (), {"execute": lambda s, q, p=(): type("R", (), {"fetchall": lambda s: [rows[2]], "fetchone": lambda s: None})()})()) is None


def test_cost_scales_with_token_count(tracker):
    small = tracker.calculate_cost_eur("openai", "gpt-4o", 1_000, 1_000)
    large = tracker.calculate_cost_eur("openai", "gpt-4o", 100_000, 100_000)
    if small == 0.0 and large == 0.0:
        pytest.skip("no pricing row for openai/gpt-4o in this database")
    assert large > small


def test_cost_is_never_negative(tracker):
    assert tracker.calculate_cost_eur("openai", "gpt-4o", 0, 0) >= 0.0


# ── Numeric settings: finite, non-negative, or refused ──────────────────────

from services.validator import parse_money


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "-1", "-0.01", "abc", "", "1e400"])
def test_parse_money_refuses_what_float_accepts(raw):
    with pytest.raises(ValueError):
        parse_money(raw, field="Daily budget")


@pytest.mark.parametrize("raw, expected", [("5", 5.0), (" 2.50 ", 2.5), ("0", 0.0), ("1,5", 1.5), ("1e3", 1000.0)])
def test_parse_money_accepts_ordinary_values(raw, expected):
    assert parse_money(raw, field="x") == expected


def test_parse_money_blank_is_none_only_when_allowed():
    assert parse_money("", field="x", allow_blank=True) is None
    assert parse_money("   ", field="x", allow_blank=True) is None
    with pytest.raises(ValueError):
        parse_money("", field="x")


def test_parse_money_minimum_is_inclusive():
    assert parse_money("0.000001", field="rate", minimum=0.000001) == 0.000001
    with pytest.raises(ValueError):
        parse_money("0", field="rate", minimum=0.000001)


def test_budget_save_keeps_previous_values_on_bad_input(monkeypatch):
    from types import SimpleNamespace
    from main import GodAI
    from PySide6.QtWidgets import QMessageBox
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    saved = []
    monkeypatch.setattr("main.save_setting", lambda k, v: saved.append((k, v)))
    stub = SimpleNamespace(
        session_budget_eur=5.0, daily_budget_eur=20.0,
        session_budget_input=SimpleNamespace(text=lambda: "nan"),
        daily_budget_input=SimpleNamespace(text=lambda: "20"),
        update_usage_labels=lambda: None,
    )
    GodAI.save_budget_limits(stub)
    assert stub.session_budget_eur == 5.0 and stub.daily_budget_eur == 20.0
    assert saved == []
    assert warned and "Previous values kept" in warned[0]

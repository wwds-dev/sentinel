"""Phase 0 of docs/local_model_routing_plan.html: a router that can be built on."""
from types import SimpleNamespace

import pytest

from services.model_recommendations import (
    MODEL_CATALOG, resolve_available_model, resolve_model, route_request, same_model,
)
from services.provider_catalog import CLOUD_PROVIDERS, DEFAULT_LOCAL_MODEL, SUPPORTED_PROVIDERS
from services.registry import Registry

ALL = {}
for _p in MODEL_CATALOG:
    ALL.setdefault(_p.provider, []).append(_p.model)


# ── 1. allowed_providers ────────────────────────────────────────────────────

def test_route_request_never_picks_a_provider_outside_the_allow_list():
    open_choice = route_request("Refactor this Python function", available_models=ALL,
                                enabled_providers=set(ALL))
    others = set(ALL) - {open_choice.provider}
    narrowed = route_request("Refactor this Python function", available_models=ALL,
                             enabled_providers=set(ALL), allowed_providers=others)
    assert narrowed.provider in others
    assert narrowed.provider != open_choice.provider
    assert all(c.provider in others for c in narrowed.fallbacks)


def test_an_empty_allow_list_leaves_nothing_to_route_to():
    with pytest.raises(RuntimeError):
        route_request("hello", available_models=ALL, allowed_providers=set())


def test_no_allow_list_means_no_restriction():
    assert route_request("hello", available_models=ALL, allowed_providers=None).provider in ALL


def test_allow_list_beats_a_manual_choice():
    manual = next(p for p in MODEL_CATALOG if p.provider == "openai" and p.capabilities.text)
    decision = route_request("hello", available_models=ALL, manual_provider="openai",
                             manual_model=manual.model, allowed_providers={"anthropic"})
    assert decision.provider == "anthropic" and not decision.manual


def _registry(agent, tool):
    reg = Registry.__new__(Registry)
    reg.get_agent = lambda name: agent if name == "a" else None
    reg.get_tool = lambda name: tool if name == "t" else None
    return reg


def test_registry_intersects_agent_and_tool_allow_lists():
    reg = _registry({"allowed_providers": ["ollama", "openai", "gemini"]},
                    {"allowed_providers": ["ollama", "gemini", "kimi"]})
    assert reg.permitted_providers("a", "t") == {"ollama", "gemini"}
    assert reg.permitted_providers("a", "") == {"ollama", "openai", "gemini"}
    assert reg.permitted_providers("zzz", "zzz") is None          # unregistered: no restriction
    assert _registry({"allowed_providers": []}, None).permitted_providers("a", "t") is None


def test_shipped_writing_tool_excludes_deepseek_from_the_route():
    import json
    from pathlib import Path
    tools = {t["name"]: t for t in json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "registry.json").read_text())["tools"]}
    assert "deepseek" not in tools["Writing"]["allowed_providers"]
    reg = _registry(None, tools["Writing"])
    for prompt in ("Rewrite this email to sound professional", "write a poem"):
        d = route_request(prompt, tool="Writing", available_models=ALL,
                          allowed_providers=reg.permitted_providers("", "t"))
        assert d.provider != "deepseek"


def test_main_passes_the_registry_allow_list_to_the_router(monkeypatch):
    import main
    seen = {}
    monkeypatch.setattr(main, "route_request", lambda prompt, **kw: seen.update(kw) or "decision")
    stub = SimpleNamespace(
        _enabled_provider_names=lambda: {"ollama", "openai"},
        registry=SimpleNamespace(permitted_providers=lambda a, t: {"ollama"}),
        models_for_provider=lambda p: [], _routing_preferences=lambda: None)
    assert main.GodAI.route_for_request(stub, "hi", agent="chat", tool="Writing",
                                        keep_manual=False) == "decision"
    assert seen["allowed_providers"] == {"ollama"}


# ── 2. exact availability ───────────────────────────────────────────────────

@pytest.mark.parametrize("wanted,listed,same", [
    ("gpt-4o", "gpt-4o", True),
    ("GPT-4o", "gpt-4o", True),
    ("gpt-4o", "gpt-4o-mini", False),
    ("gpt-4o-mini", "gpt-4o", False),
    ("gpt-4o", "gpt-4o-2024-08-06", True),
    ("claude-sonnet-5-5", "claude-sonnet-5-5-20261001", True),
    ("claude-sonnet-5", "claude-sonnet-5-5", False),
    ("gemini-2.5-flash", "gemini-2.5-flash-lite", False),
    ("gemini-2.5-flash", "gemini-2.5-flash-latest", True),
    ("deepseek-r1:8b", "deepseek-r1:8b", True),
    ("deepseek-r1", "deepseek-r1:latest", True),
    ("deepseek-r1:8b", "deepseek-r1:14b", False),
    ("", "gpt-4o", False),
])
def test_same_model(wanted, listed, same):
    assert same_model(wanted, listed) is same


def test_a_sibling_model_does_not_make_a_model_available():
    only_mini = {"openai": ["gpt-4o-mini"]}
    with pytest.raises(RuntimeError):
        route_request("hello", available_models=only_mini, enabled_providers={"openai"},
                      candidates=[p for p in MODEL_CATALOG if p.model == "gpt-4o"])


# ── 3. substitutions are surfaced ───────────────────────────────────────────

def test_resolve_model_reports_a_substitution():
    got = resolve_model("retired-model", ["current-model", "other"])
    assert (got.model, got.substituted) == ("current-model", True)
    assert "retired-model" in got.note and "current-model" in got.note


def test_resolve_model_is_silent_for_an_exact_or_dated_match():
    assert not resolve_model("gpt-4o", ["gpt-4o-mini", "gpt-4o-2024-08-06"]).substituted
    assert resolve_model("gpt-4o", ["gpt-4o-mini", "gpt-4o-2024-08-06"]).model == "gpt-4o-2024-08-06"
    assert not resolve_model("x", []).substituted
    assert resolve_available_model("gpt-4o", ["gpt-4o-mini"]) == "gpt-4o-mini"


# ── 5. no duplicated provider lists or model ids in main.py ─────────────────

def test_main_has_no_hardcoded_default_local_model_or_provider_tuple():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "deepseek-r1:8b" not in src
    assert '("openai", "deepseek", "kimi"' not in src
    assert DEFAULT_LOCAL_MODEL == "deepseek-r1:8b"


def test_enabled_provider_names_follow_the_supported_list():
    import main
    boxes = {f"allow_{p}_checkbox": SimpleNamespace(isChecked=lambda: True) for p in CLOUD_PROVIDERS}
    stub = SimpleNamespace(**boxes)
    assert main.GodAI._enabled_provider_names(stub) == set(SUPPORTED_PROVIDERS)

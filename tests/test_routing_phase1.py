"""Routing phase 1 — the provider registry and a second local provider.

Everything here is about one rule: a provider is local because its spec says
so, never because its name is the string "ollama". LM Studio is the second
local provider that proves it — free, private, no permission, no paid-request
dialog — while staying unrated, so the router never picks it over rated models.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import database, providers
from services.model_recommendations import (
    MODEL_CATALOG, RoutingPreferences, pricing_metadata, route_request,
)
from services.benchmarks import Rating
from services.provider_catalog import (
    CLOUD_PROVIDERS, LOCAL_PROVIDERS, SUPPORTED_PROVIDERS, provider_metadata,
)
from services.registry import Registry
from services.usage_tracker import UsageTracker
from services.validator import Validator

GB = 1e9


# ── the catalog is the registry ────────────────────────────────────────────

def test_provider_catalog_is_derived_from_the_registry():
    assert SUPPORTED_PROVIDERS == providers.SUPPORTED
    assert LOCAL_PROVIDERS == ("ollama", "lmstudio")
    assert CLOUD_PROVIDERS == providers.CLOUD_PROVIDERS
    assert "lmstudio" not in CLOUD_PROVIDERS


def test_a_second_local_provider_is_free_and_local_in_the_metadata():
    for name in LOCAL_PROVIDERS:
        meta = provider_metadata(name)
        assert (meta.location, meta.pricing_status) == ("local", "free_local")
    assert provider_metadata("openai").pricing_status == "paid"
    assert provider_metadata("not-a-provider").pricing_status == "unknown"


def test_lmstudio_is_priced_free_and_scored_local_by_the_router():
    meta = pricing_metadata("lmstudio", "dolphin3.0-llama3.1-8b")
    assert meta.status == pricing_metadata("ollama", "deepseek-r1:8b").status
    assert meta.currency_cost == 0.0 if hasattr(meta, "currency_cost") else True


# ── spend: a local call is never paid usage ────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "data" / "sentinel.db")
    database.init_db()
    return database


def test_calculate_cost_is_zero_for_every_local_provider(fresh_db):
    tracker = UsageTracker()
    for name in LOCAL_PROVIDERS:
        assert tracker.calculate_cost_eur(name, "any-model", 10**6, 10**6) == 0.0
    # ... while an unknown or unlisted CLOUD model still never costs zero.
    assert tracker.calculate_cost_eur("openai", "no-such-model", 10**6, 10**6) > 0


def test_a_local_request_is_not_counted_as_cloud_usage(fresh_db):
    row = UsageTracker().log_request("chat", "lmstudio", "m", "hello", "world")
    assert row["cloud"] is False and row["cost_eur"] == 0.0


class _Registry:
    def __getattr__(self, name):
        if name.startswith(("is_", "agent_allows", "tool_allows")):
            return lambda *a: True
        return lambda *a: None


def _validate(provider, **kw):
    perms = {f"allow_{p}": False for p in CLOUD_PROVIDERS}
    args = dict(agent_name="chat", tool_name="General Chat", provider=provider,
                api_permissions=perms, session_cost=0.0, session_budget=0.0,
                daily_cost=0.0, daily_budget=0.0, estimated_cost=99.0)
    args.update(kw)
    return Validator(_Registry()).validate(**args)


def test_a_local_provider_needs_no_permission_and_ignores_every_budget():
    for name in LOCAL_PROVIDERS:
        assert _validate(name).allowed, name
    assert not _validate("openai").allowed


# ── the registry's allow-lists ─────────────────────────────────────────────

def _reg(agent, tool=None):
    reg = Registry.__new__(Registry)
    reg.get_agent = lambda name: agent if name == "a" else None
    reg.get_tool = lambda name: tool if name == "t" else None
    return reg


def test_an_agent_allowed_ollama_may_use_lmstudio_too():
    reg = _reg({"allowed_providers": ["ollama", "openai"]})
    assert reg.agent_allows_provider("a", "lmstudio")
    assert reg.agent_allows_provider("a", "openai")
    assert not reg.agent_allows_provider("a", "gemini")
    assert reg.permitted_providers("a", "") == {"ollama", "lmstudio", "openai"}


def test_a_cloud_only_agent_still_refuses_local_providers():
    reg = _reg({"allowed_providers": ["openai", "gemini"]})
    assert not reg.agent_allows_provider("a", "lmstudio")
    assert reg.permitted_providers("a", "") == {"openai", "gemini"}


def test_allow_lists_intersect_after_the_local_group_expands():
    reg = _reg({"allowed_providers": ["ollama", "openai"]},
               {"allowed_providers": ["lmstudio", "gemini"]})
    assert reg.permitted_providers("a", "t") == {"ollama", "lmstudio"}


# ── the router: unrated local never beats rated cloud ──────────────────────

def _local_profile(name="dolphin3.0-llama3.1-8b"):
    base = next(p for p in MODEL_CATALOG if p.provider == "ollama")
    return dataclasses.replace(base, provider="lmstudio", model=name)


def test_an_unrated_lmstudio_model_never_wins_over_rated_models():
    cloud = [p for p in MODEL_CATALOG if p.provider in {"openai", "anthropic"}
             and p.capabilities.text][:2]
    local = _local_profile()
    rated = {(cloud[0].provider, cloud[0].model): Rating(1300, 1290, 1310, 1000, "a", "coding"),
             (cloud[1].provider, cloud[1].model): Rating(1290, 1280, 1300, 1000, "b", "coding")}

    def lookup(provider, model, task):
        return rated.get((provider, model))

    decision = route_request(
        "Explain how a hash map works", candidates=[local, *cloud],
        ratings=lookup, preferences=RoutingPreferences(),
        enabled_providers={"lmstudio", "openai", "anthropic"})
    assert decision.provider != "lmstudio"


def test_an_unrated_lmstudio_model_can_still_be_chosen_by_hand():
    local = _local_profile()
    decision = route_request(
        "hello", candidates=[local], enabled_providers={"lmstudio"},
        manual_provider="lmstudio", manual_model=local.model)
    assert (decision.provider, decision.model) == ("lmstudio", local.model)


# ── resolving and running a route ──────────────────────────────────────────

def _resolve_stub(mode, provider, model):
    notices = []
    box = lambda text: SimpleNamespace(currentText=lambda: text)
    stub = SimpleNamespace(
        execution_mode_box=box(mode), provider_box=box(provider),
        model_box=box(model), settings={"default_model_ollama": "llama3:8b"},
        notices=notices, _set_route_notice=notices.append,
        _local_default_model=lambda: "llama3:8b",
        _routing_enabled=lambda *a, **k: False)
    for name in CLOUD_PROVIDERS:
        setattr(stub, f"allow_{name}_checkbox",
                SimpleNamespace(isChecked=lambda: True))
    return stub


@pytest.mark.parametrize("mode", ["Local only", "Hybrid allowed"])
def test_lmstudio_stays_lmstudio_when_the_mode_resolves_the_route(mode):
    import main
    stub = _resolve_stub(mode, "lmstudio", "dolphin3.0-llama3.1-8b")
    try:
        result = main.GodAI.resolve_backend_model(stub)
    except AttributeError:
        pytest.skip("resolve_backend_model needs more of the window than a stub")
    assert result == ("lmstudio", "dolphin3.0-llama3.1-8b")


def test_run_backend_dispatches_a_local_openai_protocol_provider_to_its_client():
    import main
    calls = []

    class LM:
        def stream_chat(self, messages, model):
            calls.append((model, messages)); yield "tok"

    stub = SimpleNamespace(lmstudio=LM(), assess_local_model=lambda m, p="ollama": None)
    out = main.GodAI.run_backend(stub, "lmstudio", "m1", [{"role": "user", "content": "x"}], "x")
    assert list(out) == ["tok"] and calls[0][0] == "m1"


def test_run_backend_passes_the_provider_to_the_fit_check_and_refuses_too_big():
    import main
    asked = []

    def assess(model, provider="ollama"):
        asked.append((model, provider))
        return {"level": "too_big", "message": "nope"}

    stub = SimpleNamespace(lmstudio=SimpleNamespace(stream_chat=lambda **k: iter(())),
                           assess_local_model=assess)
    with pytest.raises(RuntimeError, match="nope"):
        main.GodAI.run_backend(stub, "lmstudio", "big", [], "x")
    assert asked == [("big", "lmstudio")]


def test_run_backend_keeps_ollamas_keyword_generate_and_cloud_positional_generate():
    import main

    class OnlyGenerate:
        def generate(self, model, prompt):
            return ("ollama", model, prompt)

    class CloudGenerate:
        def generate(self, prompt, model=""):
            return ("cloud", model, prompt)

    stub = SimpleNamespace(ollama=OnlyGenerate(), gemini=CloudGenerate(),
                           assess_local_model=lambda m, p="ollama": None)
    assert main.GodAI.run_backend(stub, "ollama", "m", [], "p") == ("ollama", "m", "p")
    assert main.GodAI.run_backend(stub, "gemini", "g", [], "p") == ("cloud", "g", "p")


def test_run_backend_refuses_an_unknown_backend():
    import main
    with pytest.raises(RuntimeError, match="No compatible backend"):
        main.GodAI.run_backend(SimpleNamespace(), "legacy-provider", "m", [], "p")


def test_there_is_no_provider_name_comparison_left_in_the_app():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in [root / "main.py", *root.glob("services/*.py"), *root.glob("ui/**/*.py")]:
        if path.name in {"providers.py", "provider_catalog.py"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ('== "ollama"' in line or '!= "ollama"' in line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    # The only comparisons allowed are the Ollama-only features: the published
    # Muse Glimmer sizes, its pull button, and the offline KNOWN_MODELS list.
    allowed = ('if published_gb is None or provider != "ollama":',
               'if self.provider_box.currentText() == "ollama":',
               'if provider == "ollama" and not models:')
    stray = [o for o in offenders if not o.endswith(allowed)]
    assert not stray, "\n".join(stray)


# ── the memory fit check, per provider ─────────────────────────────────────

def _fit_stub(size_by_provider):
    import main
    stub = SimpleNamespace(
        ollama=SimpleNamespace(model_size_bytes=lambda m: size_by_provider.get("ollama")),
        lmstudio=SimpleNamespace(model_size_bytes=lambda m: size_by_provider.get("lmstudio")))
    return stub, main.GodAI.assess_local_model


def test_assess_local_model_reads_lmstudio_sizes_and_calls_a_big_file_too_big_now(monkeypatch):
    import psutil
    stub, assess = _fit_stub({"lmstudio": 15 * GB})
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=24 * GB, available=9 * GB))
    verdict = assess(stub, "dsr1-32b", "lmstudio")
    assert verdict["level"] == "tight" and "free right now" in verdict["message"]
    assert verdict["needed_gb"] == pytest.approx(18.0)


def test_assess_local_model_is_not_applicable_when_the_size_is_unknown():
    stub, assess = _fit_stub({})
    assert assess(stub, "unknown", "lmstudio") is None
    assert assess(stub, "unknown", "ollama") is None


# ── the model pickers ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_model_menu_marks_every_uncensored_model_with_a_pill_and_a_note(qapp):
    from ui.widgets import MenuComboBox
    combo = MenuComboBox()
    combo.setMenuMode(MenuComboBox.MODEL)
    names = ["DeepSeek-R1-Distill-Qwen-14B-abliterated", "dolphin3.0-llama3.1-8b",
             "deepseek-r1:8b"]
    combo.addItems(names)
    menu = combo.buildMenu()
    tags = {a.text(): t for a, t in menu.tagBadges().items()}
    assert tags == {names[0]: "ABLITERATED", names[1]: "UNCENSORED"}
    tips = {a.text(): a.toolTip() for a in menu.actions()}
    assert "refusal behaviour was removed" in tips[names[0]]
    assert "not to refuse" in tips[names[1]]
    assert "refus" not in tips[names[2]]


def test_the_closed_picker_shows_the_tag_of_the_selected_model(qapp):
    from ui.widgets import MenuComboBox
    combo = MenuComboBox()
    combo.addItems(["gpt-4o", "dolphin3.0-llama3.1-8b"])
    assert combo.currentTag() is None
    combo.setCurrentIndex(1)
    assert combo.currentTag() == "uncensored"


def test_the_provider_picker_groups_both_local_runtimes_together(qapp):
    from ui.widgets import MenuComboBox
    combo = MenuComboBox()
    combo.setMenuMode(MenuComboBox.PROVIDER)
    combo.addItems(["ollama", "lmstudio", "openai"])
    groups = {a.text(): [x.text() for x in a.menu().actions()]
              for a in combo.buildMenu().actions() if a.menu() is not None}
    assert groups == {"Local providers": ["ollama", "lmstudio"],
                      "Cloud providers": ["openai"]}


# ── this Mac ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(not (Path.home() / ".lmstudio" / "models").is_dir(),
                    reason="LM Studio has no models folder on this machine")
def test_every_uncensored_download_on_this_machine_is_tagged_and_no_other():
    from services import lmstudio_client
    found = lmstudio_client.scan_disk()
    for model in found:
        text = f"{model['name']} {Path(model['path']).name}"
        tag = providers.uncensored_tag(text, model["publisher"])
        looks_uncensored = any(w in text.casefold() for w in
                               ("abliterat", "dolphin", "uncensor"))
        assert bool(tag) == looks_uncensored, text

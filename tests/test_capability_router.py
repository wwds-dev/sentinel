import pytest

from services.model_recommendations import (
    MODEL_CATALOG, RoutingPreferences, classify_request, pricing_metadata,
    route_request,
)


IMAGE_MODEL = "gpt-image-1.5"

ALL = {}
for profile in MODEL_CATALOG:
    ALL.setdefault(profile.provider, []).append(profile.model)


@pytest.mark.parametrize("prompt,task,capability", [
    ("Hello, how are you?", "general", "text"),
    ("Refactor this Python function", "coding", "coding"),
    ("Use deep reasoning and evaluate trade-offs step by step", "reasoning", "reasoning"),
    ("Generate an image of a lighthouse", "image_generation", "image_generation"),
    ("Analyze this attached image", "vision", "vision"),
])
def test_task_classification_and_compatible_primary(prompt, task, capability):
    decision = route_request(prompt, available_models=ALL)
    assert decision.task == task
    profile = next(p for p in MODEL_CATALOG if (p.provider, p.model) == (decision.provider, decision.model))
    assert getattr(profile.capabilities, capability)


def test_general_chat_never_uses_image_only_model():
    decision = route_request("Hello, help me plan my day", available_models=ALL)
    assert decision.model != IMAGE_MODEL
    assert next(p for p in MODEL_CATALOG if p.model == decision.model).capabilities.text


def test_long_context_filters_models_that_are_too_small():
    decision = route_request("Summarize this long document", context_tokens=500_000, available_models=ALL)
    profile = next(p for p in MODEL_CATALOG if p.model == decision.model)
    assert decision.task == "long_context"
    assert profile.capabilities.context_window >= 500_000


def test_cost_and_speed_preferences_choose_cheap_fast_routes():
    cost = route_request("Give me a simple quick answer", preferences=RoutingPreferences(priority="cost"), available_models=ALL)
    speed = route_request("Give me a simple quick answer", preferences=RoutingPreferences(priority="speed"), available_models=ALL)
    cost_profile = next(p for p in MODEL_CATALOG if p.model == cost.model)
    speed_profile = next(p for p in MODEL_CATALOG if p.model == speed.model)
    assert cost_profile.capabilities.cost == 1
    assert speed_profile.capabilities.latency == 1


def test_local_private_request_never_leaves_machine():
    decision = route_request("Analyze these private notes", preferences=RoutingPreferences(local_only=True, priority="privacy"), available_models=ALL)
    assert decision.provider == "ollama"
    assert decision.mode == "Local only"


def test_manual_override_is_preserved_when_compatible():
    decision = route_request("Write a short note", manual_provider="openai", manual_model="gpt-4o-mini", available_models=ALL)
    assert decision.manual
    assert (decision.provider, decision.model) == ("openai", "gpt-4o-mini")


def test_incompatible_manual_override_falls_back():
    decision = route_request("General chat", manual_provider="openai", manual_model=IMAGE_MODEL, available_models=ALL)
    assert not decision.manual
    assert decision.model != IMAGE_MODEL
    assert "incompatible" in decision.reason


def test_unavailable_primary_has_deterministic_fallback():
    available = {"openai": ["gpt-4o-mini"], "deepseek": ["deepseek-flash"]}
    one = route_request("Refactor this code", enabled_providers=available, available_models=available)
    two = route_request("Refactor this code", enabled_providers=available, available_models=available)
    assert one == two
    assert one.fallbacks


def test_image_request_routes_to_the_current_gpt_image_model():
    decision = route_request("Generate an image of a lighthouse", available_models=ALL)
    assert (decision.provider, decision.model) == ("openai", IMAGE_MODEL)


def test_catalog_no_longer_offers_retired_dall_e():
    assert not any(p.model.startswith("dall-e") for p in MODEL_CATALOG)


def test_no_eligible_route_is_a_clear_error():
    with pytest.raises(RuntimeError, match="No available model"):
        route_request("Generate an image", enabled_providers={"ollama"}, available_models=ALL)


def test_local_model_is_explicitly_free_and_local():
    pricing = pricing_metadata("ollama", "deepseek-r1:8b")
    assert pricing.status == "free_local"
    assert pricing.compact == "FREE · LOCAL"


def test_cloud_model_is_paid_and_shows_known_rates():
    pricing = pricing_metadata("openai", "gpt-4.1-mini")
    assert pricing.status == "paid"
    assert pricing.compact == "PAID · $0.4/$1.6 per 1M in/out"


def test_unknown_provider_pricing_is_not_guessed():
    pricing = pricing_metadata("customer-hosted-gateway", "private-model")
    assert pricing.status == "unknown"
    assert pricing.compact == "UNKNOWN"


# ── Words, not substrings ────────────────────────────────────────────────────
# Each of these once imposed a hard requirement from a word inside another
# word: "draw" in "withdraw" left gpt-image-1.5 as the only eligible model,
# "script" in "transcript" and "code" in "postcode" required coding.

@pytest.mark.parametrize("prompt, task", [
    ("withdraw the claim and rewrite the email", "writing"),
    ("summarize this transcript", "summarize"),
    ("what's the postcode", "general"),
    ("improve this paragraph", "general"),                # "prove"
    ("explain photosynthesis", "general"),                # "photo"
    ("list resources for learning Rust", "general"),      # "sources"
    ("draw conclusions from this report", "general"),
    ("make a list of images in this folder", "general"),
    ("create a caption for this image", "general"),
])
def test_a_keyword_inside_another_word_is_not_that_keyword(prompt, task):
    request = classify_request(prompt)
    assert request.task == task
    assert request.required == frozenset({"text"})


def test_withdraw_no_longer_routes_to_the_image_model():
    decision = route_request("withdraw the claim and rewrite the email", available_models=ALL)
    assert decision.task == "writing"
    assert decision.model != IMAGE_MODEL


@pytest.mark.parametrize("prompt, task, capability", [
    ("Draw a cat wearing a hat", "image_generation", "image_generation"),
    ("draw me a lighthouse at dusk", "image_generation", "image_generation"),
    ("Design a logo for my bakery", "image_generation", "image_generation"),
    ("Generate an image", "image_generation", "image_generation"),
    ("Can you help with debugging this traceback?", "coding", "coding"),
    ("Refactor these functions", "coding", "coding"),
    ("Look at the attached image", "vision", "vision"),
    ("Investigate this domain", "research", "tool_use"),
    ("Prove that the sum is finite", "reasoning", "reasoning"),
])
def test_the_whole_words_and_their_forms_still_match(prompt, task, capability):
    request = classify_request(prompt)
    assert request.task == task
    assert capability in request.required


def test_summarize_is_read_before_coding():
    assert classify_request("summarize this script").task == "summarize"
    assert classify_request("summarise the code review").task == "summarize"


def test_an_agent_key_with_an_underscore_still_names_its_kind_of_work():
    # `\b` would treat osint_heavy as one word and lose "osint".
    assert classify_request("", agent="osint_heavy").required == frozenset({"text", "tool_use"})

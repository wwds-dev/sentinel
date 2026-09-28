"""Capability-based provider/model routing shared by every Sentinel agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from services.provider_catalog import provider_metadata


@dataclass(frozen=True)
class ModelCapabilities:
    text: bool = True
    reasoning: int = 1
    coding: int = 0
    vision: bool = False
    image_generation: bool = False
    tool_use: bool = False
    context_window: int = 32_000
    cost: int = 2                 # 1 cheap, 3 expensive
    latency: int = 2              # 1 fast, 3 slow
    local: bool = False
    privacy: int = 1              # 3 means content never leaves the machine


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    capabilities: ModelCapabilities
    quality: int = 2
    input_per_1m_usd: float | None = None
    output_per_1m_usd: float | None = None


@dataclass(frozen=True)
class PricingMetadata:
    status: str
    label: str
    input_per_1m_usd: float | None = None
    output_per_1m_usd: float | None = None

    @property
    def compact(self) -> str:
        if self.status == "free_local":
            return "FREE · LOCAL"
        if self.status == "unknown":
            return "UNKNOWN"
        if self.input_per_1m_usd is None or self.output_per_1m_usd is None:
            return "PAID"
        return (
            f"PAID · ${self.input_per_1m_usd:g}/${self.output_per_1m_usd:g} "
            "per 1M in/out"
        )


@dataclass(frozen=True)
class RequestProfile:
    task: str = "general"
    required: frozenset[str] = frozenset({"text"})
    context_tokens: int = 0
    complexity: int = 1


@dataclass(frozen=True)
class RoutingPreferences:
    local_only: bool = False
    cloud_only: bool = False
    priority: str = "balanced"   # balanced, cost, speed, quality, privacy


@dataclass(frozen=True)
class RouteCandidate:
    provider: str
    model: str
    score: int


@dataclass(frozen=True)
class RouteDecision:
    task: str
    provider: str
    model: str
    reason: str
    fallbacks: tuple[RouteCandidate, ...] = ()
    manual: bool = False

    @property
    def mode(self) -> str:
        return "Local only" if self.provider == "ollama" else "Cloud only"

    @property
    def pricing(self) -> PricingMetadata:
        return pricing_metadata(self.provider, self.model)

    def as_dict(self) -> dict:
        return {
            "task": self.task, "mode": self.mode, "provider": self.provider,
            "model": self.model, "reason": self.reason,
            "fallbacks": [vars(item) for item in self.fallbacks],
            "manual": self.manual,
            "pricing": vars(self.pricing),
            "cost_label": self.pricing.compact,
        }


def _caps(**kwargs) -> ModelCapabilities:
    return ModelCapabilities(**kwargs)


MODEL_CATALOG: tuple[ModelProfile, ...] = (
    ModelProfile("ollama", "muse-glimmer:30b-mlx", _caps(reasoning=3, coding=3, tool_use=True, context_window=131_000, cost=1, latency=3, local=True, privacy=3), 3),
    ModelProfile("ollama", "muse-glimmer:30b-q4_K_M", _caps(reasoning=3, coding=3, tool_use=True, context_window=131_000, cost=1, latency=3, local=True, privacy=3), 3),
    ModelProfile("ollama", "deepseek-r1:8b", _caps(reasoning=3, coding=2, context_window=64_000, cost=1, latency=2, local=True, privacy=3), 2),
    ModelProfile("ollama", "deepseek-r1:1.5b", _caps(reasoning=2, coding=1, context_window=32_000, cost=1, latency=1, local=True, privacy=3), 1),
    ModelProfile("openai", "gpt-4o-mini", _caps(reasoning=2, coding=2, vision=True, tool_use=True, context_window=128_000, cost=1, latency=1), 2, 0.15, 0.60),
    ModelProfile("openai", "gpt-4o", _caps(reasoning=3, coding=2, vision=True, tool_use=True, context_window=128_000, cost=3, latency=2), 3),
    ModelProfile("openai", "gpt-4.1-mini", _caps(reasoning=2, coding=3, vision=True, tool_use=True, context_window=1_000_000, cost=1, latency=1), 2, 0.40, 1.60),
    ModelProfile("openai", "gpt-4.1", _caps(reasoning=3, coding=3, vision=True, tool_use=True, context_window=1_000_000, cost=3, latency=2), 3, 2.0, 8.0),
    ModelProfile("openai", "gpt-image-1.5", _caps(text=False, image_generation=True, context_window=4_000, cost=3, latency=3), 3, 5.0, 32.0),
    ModelProfile("deepseek", "deepseek-flash", _caps(reasoning=2, coding=2, tool_use=True, context_window=128_000, cost=1, latency=1), 2),
    ModelProfile("deepseek", "deepseek-v4-pro", _caps(reasoning=3, coding=3, tool_use=True, context_window=128_000, cost=2, latency=2), 3),
    ModelProfile("kimi", "kimi-k3", _caps(reasoning=3, coding=2, tool_use=True, context_window=1_000_000, cost=2, latency=2), 3, 3.0, 15.0),
    ModelProfile("kimi", "kimi-k2.7-code", _caps(reasoning=3, coding=3, tool_use=True, context_window=256_000, cost=2, latency=2), 3, 0.95, 4.0),
    ModelProfile("kimi", "kimi-k2.7-code-highspeed", _caps(reasoning=2, coding=3, tool_use=True, context_window=256_000, cost=3, latency=1), 3, 1.9, 8.0),
    ModelProfile("kimi", "kimi-k2.6", _caps(reasoning=2, coding=2, tool_use=True, context_window=256_000, cost=1, latency=1), 2, 0.95, 4.0),
    ModelProfile("gemini", "gemini-2.5-flash", _caps(reasoning=2, coding=2, vision=True, tool_use=True, context_window=1_000_000, cost=1, latency=1), 2),
    ModelProfile("gemini", "gemini-2.5-pro", _caps(reasoning=3, coding=3, vision=True, tool_use=True, context_window=1_000_000, cost=3, latency=3), 3),
    ModelProfile("anthropic", "claude-opus-5", _caps(reasoning=3, coding=3, vision=True, tool_use=True, context_window=200_000, cost=3, latency=3), 3, 5.0, 25.0),
    ModelProfile("anthropic", "claude-sonnet-5", _caps(reasoning=3, coding=3, vision=True, tool_use=True, context_window=200_000, cost=2, latency=2), 3, 2.0, 10.0),
    ModelProfile("anthropic", "claude-sonnet-4-6", _caps(reasoning=3, coding=3, vision=True, tool_use=True, context_window=200_000, cost=2, latency=2), 3, 3.0, 15.0),
    ModelProfile("anthropic", "claude-haiku-4-5-20251001", _caps(reasoning=2, coding=2, vision=True, tool_use=True, context_window=200_000, cost=1, latency=1), 2, 1.0, 5.0),
    ModelProfile("qwen", "qwen3.8-max", _caps(reasoning=3, coding=3, vision=True, tool_use=True, context_window=1_000_000, cost=2, latency=2), 3, 1.65, 4.951),
    ModelProfile("qwen", "qwen3-max", _caps(reasoning=3, coding=3, tool_use=True, context_window=256_000, cost=2, latency=2), 3),
    ModelProfile("qwen", "qwen-plus", _caps(reasoning=2, coding=2, tool_use=True, context_window=128_000, cost=1, latency=1), 2),
    ModelProfile("qwen", "qwen-flash", _caps(reasoning=1, coding=1, tool_use=True, context_window=128_000, cost=1, latency=1), 1),
)


def pricing_metadata(provider: str, model: str) -> PricingMetadata:
    """Resolve display-safe billing metadata without treating missing data as free."""
    provider_info = provider_metadata(provider)
    if provider_info.pricing_status == "free_local":
        return PricingMetadata("free_local", "Free / Local", 0.0, 0.0)
    if provider_info.pricing_status == "unknown":
        return PricingMetadata("unknown", "Unknown")
    profile = next(
        (item for item in MODEL_CATALOG
         if item.provider == provider and item.model == model),
        None,
    )
    if profile is None:
        return PricingMetadata("paid", "Paid")
    return PricingMetadata(
        "paid", "Paid", profile.input_per_1m_usd, profile.output_per_1m_usd,
    )


AGENT_HINTS = {
    "osint": "research", "osint_heavy": "research", "wifi": "coding",
    "bug_bounty": "coding", "manager": "coding", "vpn": "coding",
    "sentry": "coding",
}


def classify_request(prompt: str, *, agent: str = "chat", tool: str = "", context_tokens: int = 0) -> RequestProfile:
    text = f"{agent} {tool} {prompt}".lower()
    task = AGENT_HINTS.get(agent, "general")
    required = {"text"}
    complexity = 1
    tool_task = {"writing": "writing", "rewrite": "writing", "coding": "coding", "summarize": "summarize", "general chat": "general"}.get(tool.lower())
    if tool_task:
        task = tool_task
    if any(word in text for word in ("generate an image", "create an image", "draw ", "illustrate", "text-to-image")):
        return RequestProfile("image_generation", frozenset({"image_generation"}), context_tokens, 2)
    if any(word in text for word in ("attached image", "screenshot", "photo", "what is in this image", "analyze this image", "analyse this image")):
        task, required = "vision", {"text", "vision"}
    elif any(word in text for word in ("debug", "traceback", "refactor", "code", "function", "class ", "script", "bug bounty", "wifi", "vpn")):
        task, required, complexity = "coding", {"text", "coding"}, 2
    elif any(word in text for word in ("prove", "deep reasoning", "step by step", "complex analysis", "evaluate tradeoffs", "evaluate trade-offs")):
        task, required, complexity = "reasoning", {"text", "reasoning"}, 3
    elif any(word in text for word in ("research", "osint", "investigate", "sources", "dossier")):
        task, required, complexity = "research", {"text", "tool_use"}, 2
    elif any(word in text for word in ("summarize", "summarise", "long document", "transcript")):
        task = "long_context" if context_tokens > 100_000 or "long" in text else "summarize"
    elif any(word in text for word in ("cheap", "lowest cost", "simple", "quick answer")):
        task = "simple"
    elif any(word in text for word in ("write", "rewrite", "email", "polish")):
        task = "writing"
    if context_tokens > 0:
        task = "long_context" if context_tokens > 100_000 else task
    return RequestProfile(task, frozenset(required), context_tokens, complexity)


def _compatible(model: ModelProfile, request: RequestProfile, prefs: RoutingPreferences) -> bool:
    cap = model.capabilities
    if prefs.local_only and not cap.local:
        return False
    if prefs.cloud_only and cap.local:
        return False
    if request.context_tokens and cap.context_window < request.context_tokens:
        return False
    return all(bool(getattr(cap, name, False)) for name in request.required)


def _score(model: ModelProfile, request: RequestProfile, prefs: RoutingPreferences) -> int:
    cap = model.capabilities
    score = model.quality * 20 + cap.reasoning * 4 + cap.coding * 3
    if request.task == "coding": score += cap.coding * 18
    if request.task == "reasoning": score += cap.reasoning * 20
    if request.task in {"research", "long_context", "summarize"}: score += min(cap.context_window // 50_000, 20) + cap.tool_use * 8
    if request.task == "vision": score += cap.vision * 30
    if request.task == "image_generation": score += cap.image_generation * 100
    if request.task == "simple": score += (4 - cap.cost) * 15 + (4 - cap.latency) * 12
    if request.task == "writing": score += (4 - cap.latency) * 15 + (4 - cap.cost) * 10
    if prefs.priority == "cost": score += (4 - cap.cost) * 25
    elif prefs.priority == "speed": score += (4 - cap.latency) * 25
    elif prefs.priority == "quality": score += model.quality * 25 + cap.reasoning * 8
    elif prefs.priority == "privacy": score += cap.privacy * 30
    if cap.local: score += 4
    return score


def _available(model: ModelProfile, available: Mapping[str, Sequence[str]] | None) -> bool:
    if available is None:
        return True
    models = available.get(model.provider, ())
    return model.model in models or any(item.startswith(model.model) or model.model.startswith(item) for item in models)


def route_request(prompt: str, *, agent: str = "chat", tool: str = "", context_tokens: int = 0,
                  preferences: RoutingPreferences | None = None,
                  available_models: Mapping[str, Sequence[str]] | None = None,
                  enabled_providers: Iterable[str] | None = None,
                  manual_provider: str | None = None, manual_model: str | None = None) -> RouteDecision:
    request = classify_request(prompt, agent=agent, tool=tool, context_tokens=context_tokens)
    prefs = preferences or RoutingPreferences()
    enabled = set(enabled_providers) if enabled_providers is not None else {m.provider for m in MODEL_CATALOG}
    if manual_provider and manual_model:
        match = next((m for m in MODEL_CATALOG if m.provider == manual_provider and m.model == manual_model), None)
        if manual_provider in enabled and match and _compatible(match, request, prefs) and _available(match, available_models):
            return RouteDecision(request.task, manual_provider, manual_model, "Manual provider/model override retained; it satisfies this request.", manual=True)
    eligible = [m for m in MODEL_CATALOG if m.provider in enabled and _compatible(m, request, prefs) and _available(m, available_models)]
    ranked = sorted((RouteCandidate(m.provider, m.model, _score(m, request, prefs)) for m in eligible), key=lambda c: (-c.score, c.provider, c.model))
    if not ranked:
        raise RuntimeError(f"No available model satisfies task '{request.task}' and the current privacy/provider constraints.")
    primary = ranked[0]
    fallback = tuple(item for item in ranked[1:] if (item.provider, item.model) != (primary.provider, primary.model))[:3]
    preference = prefs.priority if prefs.priority != "balanced" else "capability fit"
    reason = f"Detected {request.task}; selected the highest-scoring compatible route for {preference}."
    if manual_provider or manual_model:
        reason += " The manual choice was unavailable or incompatible, so automatic fallback was used."
    return RouteDecision(request.task, primary.provider, primary.model, reason, fallback)


# Compatibility exports for older UI/tests while callers migrate to route_request.
@dataclass(frozen=True)
class Recommendation:
    provider: str
    model: str
    reason: str


AGENT_RECOMMENDATIONS = {
    "osint": Recommendation("deepseek", "deepseek-flash", "Capability baseline for frequent structured research."),
    "osint_heavy": Recommendation("anthropic", "claude-opus-5", "Capability baseline for deep long-context synthesis."),
    "wifi": Recommendation("anthropic", "claude-sonnet-5", "Capability baseline for technical reasoning."),
    "bug_bounty": Recommendation("anthropic", "claude-sonnet-5", "Capability baseline for security and coding analysis."),
    "manager": Recommendation("anthropic", "claude-sonnet-5", "Capability baseline for code and specification generation."),
    "vpn": Recommendation("anthropic", "claude-sonnet-5", "Capability baseline for configuration reasoning."),
    "sentry": Recommendation("anthropic", "claude-sonnet-5", "Capability baseline for network-anomaly interpretation."),
}
TASK_RECOMMENDATIONS = {
    "general": Recommendation("ollama", "deepseek-r1:8b", "Private local baseline for general chat."),
    "writing": Recommendation("openai", "gpt-4.1-mini", "Fast text-capable writing baseline."),
    "coding": Recommendation("deepseek", "deepseek-v4-pro", "Coding-capable baseline."),
    "summarize": Recommendation("gemini", "gemini-2.5-flash", "Long-context, low-latency baseline."),
    "research": Recommendation("kimi", "kimi-k3", "Long-context tool-use baseline."),
}


def as_dict(rec: Recommendation, *, mode: str | None = None) -> dict:
    pricing = pricing_metadata(rec.provider, rec.model)
    return {"mode": mode or ("Local only" if rec.provider == "ollama" else "Hybrid allowed"), "provider": rec.provider, "model": rec.model, "reason": rec.reason, "pricing": pricing, "cost_label": pricing.compact}


def find_model(names: Sequence[str], wanted: str) -> int:
    """Index of `wanted` in `names`, tolerating dated API model ids, or -1.

    Providers return ids like "claude-sonnet-4-6-20260112" from the live API
    but bare names like "claude-sonnet-4-6" from the offline fallback list,
    so an exact match alone would silently miss. Tries exact, then prefix,
    then substring. This is how a panel selects its recommended model, and
    what scripts/check_live_models.py asks of the live lists.
    """
    if not wanted:
        return -1
    if wanted in names:
        return list(names).index(wanted)
    lowered = wanted.lower()
    for i, name in enumerate(names):
        if name.lower().startswith(lowered):
            return i
    for i, name in enumerate(names):
        if lowered in name.lower():
            return i
    return -1


def resolve_available_model(wanted: str, available: list[str]) -> str:
    if not available: return wanted
    if wanted in available: return wanted
    target = wanted.lower()
    for model in available:
        candidate = model.lower()
        if candidate.startswith(target) or target.startswith(candidate): return model
    return available[0]

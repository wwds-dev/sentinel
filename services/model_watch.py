"""Notice when an AI provider ships a model Sentinel has not seen, and rate it.

Every cloud client already lists its provider's live models into the dropdowns,
so a new release *appears* there on its own. What did not happen is everything
after that: the router only ranks models in `MODEL_CATALOG`, so a new model was
never priced, never considered for BEST FIT, and nothing said it had arrived.

This module closes that gap in three steps, none of which sends a prompt:

1. **Scan.** Ask each provider with a key for its model list (`list_live`). The
   listing endpoints are free. Calls run one provider at a time, never in
   parallel, because a burst of connections is what this machine's router
   refuses.
2. **Notice.** `ModelWatch.record_scan` compares the listing with what was seen
   before. The first scan of a provider is a baseline: it flags only models
   that are a *newer release of a family Sentinel already rates* (a later
   `claude-sonnet`, say), not the forty legacy ids an OpenAI listing carries.
   After that, every id that was not there last time is new.
3. **Assess, then adopt on request.** `infer_profile` rates a new model like
   its nearest catalog sibling; `assess` says where it would be the better
   value. Being newer counts for nothing: it must rate higher, or rate the
   same at a known lower price. Nothing moves until the user adopts it.
   Adopting registers the profile with the router and moves BEST FIT where
   `beats` says it wins.

A model with no sibling to be rated against is still offered in the
dropdowns, but it is never ranked: a guessed capability profile winning BEST
FIT would be a recommendation with nothing behind it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping

from services import model_recommendations as mr
from services.model_recommendations import (
    ModelProfile, Recommendation, RoutingPreferences, classify_request,
    route_request,
)

# Listing entries that are not chat models. Matched as substrings of the
# lower-cased id. Providers list embeddings, speech, moderation and media
# models beside the chat ones; none of them can sit in a chat dropdown.
NOISE_MARKERS = (
    "embed", "tts", "whisper", "transcri", "audio", "realtime", "moderation",
    "search", "dall-e", "davinci", "babbage", "instruct", "computer-use",
    "gemma", "learnlm", "imagen", "veo", "aqa", "rerank", "-live", "lyria",
)

# Suffixes that name a snapshot of a model rather than a different model:
# dated ids, previews, "-latest", Gemini's "-001". Stripped (repeatedly) before
# two ids are compared, so "claude-sonnet-5-20260115" is the model Sentinel
# already knows as "claude-sonnet-5" and not a new one.
_SNAPSHOT_SUFFIXES = (
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),
    re.compile(r"-\d{8}$"),
    re.compile(r"-(preview|exp|experimental)(-[\w.]*)?$"),
    re.compile(r"-latest$"),
    re.compile(r"-\d{3,4}$"),
)

# Name words that say which end of a provider's range a model sits at. Only
# used to describe a model with no sibling; never to rank one.
_SMALL_WORDS = ("mini", "nano", "flash", "lite", "haiku", "fast", "small")
_LARGE_WORDS = ("opus", "pro", "max", "ultra", "large")

STATE_VERSION = 1


def is_chat_model(model: str) -> bool:
    lowered = model.lower()
    return not any(marker in lowered for marker in NOISE_MARKERS)


def canonical(model: str) -> str:
    """The id with snapshot suffixes removed, lower-cased."""
    name = model.lower().strip()
    changed = True
    while changed:
        changed = False
        for pattern in _SNAPSHOT_SUFFIXES:
            stripped = pattern.sub("", name)
            if stripped != name and stripped:
                name, changed = stripped, True
    return name


def family_version(model: str) -> tuple[str, tuple[int, ...]]:
    """Split an id into its family and its version numbers.

    "claude-sonnet-4-6" -> ("claude-sonnet", (4, 6)); "gpt-4.1-mini" ->
    ("gpt-mini", (4, 1)); "qwen3.8-max" -> ("qwen-max", (3, 8)). The family is
    what stays the same between releases, so two ids in one family can be
    ordered by version.
    """
    words, numbers = [], []
    for token in re.split(r"[-_:/]", canonical(model)):
        numbers.extend(int(n) for n in re.findall(r"\d+", token))
        letters = re.sub(r"[\d.]+", "", token)
        if letters:
            words.append(letters)
    return "-".join(words), tuple(numbers)


def _known_canonicals(ids: Iterable[str]) -> set[str]:
    return {canonical(item) for item in ids}


# ── Live listing ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderListing:
    provider: str
    status: str                    # "ok", "skipped" (no key) or "unreachable"
    models: tuple[str, ...] = ()
    note: str = ""


def list_live(client_classes: Mapping[str, type]) -> list[ProviderListing]:
    """Ask each provider with a key for its live model list, one at a time.

    Every client answers a failed or empty listing with its KNOWN_MODELS.
    Emptying that list on a fresh instance turns the fallback into an empty
    answer, so "the API listed these" can never be mistaken for "we fell
    back" (scripts/check_live_models.py does the same).
    """
    listings = []
    for provider, cls in client_classes.items():
        try:
            has_key = bool(cls.key_available())
        except Exception:
            has_key = False
        if not has_key:
            listings.append(ProviderListing(provider, "skipped", note="no API key"))
            continue
        try:
            client = cls()
            client.KNOWN_MODELS = []
            models = tuple(client.list_models() or ())
        except Exception as exc:  # a client raising is a client bug, not a crash
            listings.append(ProviderListing(provider, "unreachable", note=str(exc)[:200]))
            continue
        if not models:
            listings.append(ProviderListing(
                provider, "unreachable", note="API unreachable or listed nothing"))
            continue
        listings.append(ProviderListing(provider, "ok", models))
    return listings


# ── Rating a model nobody has rated ──────────────────────────────────────────

@dataclass(frozen=True)
class InferredProfile:
    profile: ModelProfile
    basis: str                     # "sibling" or "name"
    sibling: str = ""              # the catalog model it was rated like

    @property
    def rankable(self) -> bool:
        return self.basis == "sibling"

    @property
    def description(self) -> str:
        if self.rankable:
            return f"rated like {self.sibling} (same family, newest Sentinel rates)"
        return "no sibling in Sentinel's catalog to rate it against"


def infer_profile(provider: str, model: str,
                  catalog: Iterable[ModelProfile] | None = None) -> InferredProfile:
    """Rate a model like the newest catalog model of its family.

    Prices are deliberately left unknown: a sibling's rate is not this model's
    rate, and an estimate presented as a price is worse than none. Cost
    estimates fall back to the provider's default rate in Settings → Pricing.
    """
    profiles = tuple(catalog) if catalog is not None else mr.catalog()
    family, _version = family_version(model)
    siblings = [
        p for p in profiles
        if p.provider == provider and family_version(p.model)[0] == family
        and p.model != model
    ]
    if siblings:
        sibling = max(siblings, key=lambda p: family_version(p.model)[1])
        return InferredProfile(
            ModelProfile(provider, model, sibling.capabilities, sibling.quality),
            "sibling", sibling.model,
        )

    words = family.split("-")
    if any(w in words for w in _SMALL_WORDS):
        caps = mr.ModelCapabilities(reasoning=2, coding=2, cost=1, latency=1)
        quality = 2
    elif any(w in words for w in _LARGE_WORDS):
        caps = mr.ModelCapabilities(reasoning=3, coding=2, cost=3, latency=3)
        quality = 2
    else:
        caps = mr.ModelCapabilities()
        quality = 2
    return InferredProfile(ModelProfile(provider, model, caps, quality), "name")


def is_successor(provider: str, model: str,
                 catalog: Iterable[ModelProfile] | None = None) -> bool:
    """True when `model` is a newer release than every catalog model of its family."""
    profiles = tuple(catalog) if catalog is not None else mr.catalog()
    family, version = family_version(model)
    if not version:
        return False
    versions = [
        family_version(p.model)[1] for p in profiles
        if p.provider == provider and family_version(p.model)[0] == family
    ]
    return bool(versions) and all(version > v for v in versions)


# ── Would it be the best fit? ────────────────────────────────────────────────

@dataclass(frozen=True)
class Move:
    """One place where adopting the model changes the recommendation."""
    scope: str                     # "agent" or "chat"
    key: str                       # agent key, or chat task name
    current: str                   # "provider · model" recommended today
    reason: str


@dataclass(frozen=True)
class Assessment:
    provider: str
    model: str
    inferred: InferredProfile
    moves: tuple[Move, ...] = ()

    @property
    def agent_moves(self) -> tuple[Move, ...]:
        return tuple(m for m in self.moves if m.scope == "agent")

    @property
    def summary(self) -> str:
        if not self.inferred.rankable:
            return ("Added to the dropdowns only. With nothing to rate it against, "
                    "it is never picked as BEST FIT automatically.")
        if not self.moves:
            if mr.blended_price(self.inferred.profile) is None:
                return ("Not BEST FIT anywhere yet: it rates no higher than current "
                        "picks and has no price, and an unknown price never wins. "
                        "Add its price in Settings → Pricing to have it weighed on cost.")
            return "Would not be BEST FIT anywhere; current picks are as good for less, or better."
        agents = [m for m in self.moves if m.scope == "agent"]
        chat = [m for m in self.moves if m.scope == "chat"]
        parts = []
        if agents:
            parts.append(f"BEST FIT for {len(agents)} agent{'s' if len(agents) != 1 else ''}")
        if chat:
            parts.append(f"Chat's pick for {', '.join(m.key for m in chat)}")
        return "Would become " + " and ".join(parts) + "."


# Chat tasks worth asking about, as the Tool name the router reads.
CHAT_TASKS = {
    "general": "General Chat", "writing": "Writing", "coding": "Coding",
    "summarize": "Summarize",
}


def beats(candidate: ModelProfile, current: ModelProfile | None, agent: str) -> str:
    """Why `candidate` is better value than `current` for an agent, or "".

    Better value means: rated at least as well for the agent's kind of work
    *and* known to cost less. Both prices must be known; an unknown price
    never wins and never loses a pick. A higher rating alone does not move a
    pick, because a new model's rating is copied from its sibling, and if the
    sibling did not displace the current pick, a copy of it should not. And
    only within the current pick's own model line; see the comment below.
    """
    if current is None:
        return ""
    # Only within one model line (claude-sonnet against claude-sonnet). The
    # ratings are 1-3 buckets, on which Sonnet and Opus are both a 3, so a
    # cheaper Sonnet would "rate the same" as Opus and take its pick. Across
    # lines a fair comparison needs finer ratings than the catalog has.
    if (candidate.provider != current.provider
            or family_version(candidate.model)[0] != family_version(current.model)[0]):
        return ""
    new_score, old_score = _score(candidate, agent), _score(current, agent)
    if new_score is None or old_score is None or new_score < old_score:
        return ""
    new_price, old_price = mr.blended_price(candidate), mr.blended_price(current)
    if new_price is None or old_price is None or new_price >= old_price:
        return ""
    return (f"rates {'higher' if new_score > old_score else 'the same'} for this "
            f"work and costs less: ${new_price:.2f} against ${old_price:.2f} "
            "per 1M tokens blended")


def _score(profile: ModelProfile, agent: str) -> int | None:
    """The router's score for an agent's work, or None if it cannot do it."""
    request = classify_request("", agent=agent)
    prefs = RoutingPreferences()
    if not mr._compatible(profile, request, prefs):
        return None
    return mr._score(profile, request, prefs)


def assess(provider: str, model: str) -> Assessment:
    """Where adopting `model` would move a recommendation, and why."""
    profiles = mr.catalog()
    inferred = infer_profile(provider, model, profiles)
    if not inferred.rankable:
        return Assessment(provider, model, inferred)

    family, version = family_version(model)
    candidate = inferred.profile
    moves: list[Move] = []

    # Newer is not better by itself, and neither is pricier. An agent's BEST
    # FIT moves only when the new model rates higher for that agent's work,
    # or rates the same and is known to cost less (`beats`). A copied rating
    # is never higher than its sibling's, so in practice a new model wins on
    # a known lower price; one with no price yet wins nowhere, and the review
    # says to add its price in Settings → Pricing.
    for agent, rec in mr.AGENT_RECOMMENDATIONS.items():
        current = next((p for p in profiles
                        if (p.provider, p.model) == (rec.provider, rec.model)), None)
        verdict = beats(candidate, current, agent)
        if verdict:
            moves.append(Move("agent", agent, f"{rec.provider} · {rec.model}", verdict))

    with_candidate = profiles + (candidate,)
    for task, tool in CHAT_TASKS.items():
        try:
            before = route_request("", tool=tool, candidates=profiles)
            after = route_request("", tool=tool, candidates=with_candidate)
        except RuntimeError:
            continue
        if (after.provider, after.model) == (provider, model):
            moves.append(Move("chat", task, f"{before.provider} · {before.model}",
                              "highest-scoring route for this Chat tool"))

    return Assessment(provider, model, inferred, tuple(moves))


# ── Persistent state ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NewModel:
    provider: str
    model: str
    first_seen: str

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class ScanSummary:
    checked_at: str
    listings: list[ProviderListing] = field(default_factory=list)
    found: list[NewModel] = field(default_factory=list)   # new in *this* scan

    @property
    def checked(self) -> list[str]:
        return [item.provider for item in self.listings if item.status == "ok"]


def _newest_per_family(models: Iterable[str]) -> list[str]:
    """Keep the highest version of each family, in listing order.

    A first scan of OpenAI turns up gpt-5, 5.1, 5.2, 5.4 and 5.5 together;
    only 5.5 is news; the others were superseded before Sentinel looked.
    """
    newest: dict[str, tuple[tuple[int, ...], str]] = {}
    for model in models:
        family, version = family_version(model)
        if family not in newest or version > newest[family][0]:
            newest[family] = (version, model)
    keep = {model for _version, model in newest.values()}
    return [model for model in models if model in keep]


def _key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


class ModelWatch:
    """What has been seen, what is new, and what the user decided about it.

    One JSON file in Sentinel's data folder. A missing or unreadable file is
    an empty watch, never an error: the worst case is a fresh baseline.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", STATE_VERSION)
        data.setdefault("last_scan", "")
        data.setdefault("last_notes", {})
        for name in ("seen", "new", "adopted"):
            if not isinstance(data.get(name), dict):
                data[name] = {}
        if not isinstance(data.get("dismissed"), list):
            data["dismissed"] = []
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    # -- scanning --------------------------------------------------------

    def record_scan(self, listings: Iterable[ProviderListing],
                    known: Mapping[str, Iterable[str]],
                    *, now: datetime | None = None,
                    catalog: Iterable[ModelProfile] | None = None) -> ScanSummary:
        """Fold one scan into the state and say what turned up.

        `known` is every id the app already ships per provider (catalog and
        each client's KNOWN_MODELS); those are never new.
        """
        now = now or datetime.now()
        today = now.date().isoformat()
        profiles = tuple(catalog) if catalog is not None else mr.catalog()
        summary = ScanSummary(now.isoformat(timespec="seconds"))
        notes = {}

        for listing in listings:
            summary.listings.append(listing)
            notes[listing.provider] = listing.status if not listing.note else (
                f"{listing.status}: {listing.note}")
            if listing.status != "ok":
                continue
            provider = listing.provider
            live = [m for m in listing.models if is_chat_model(m)]
            seen_before = self.state["seen"].get(provider)
            baseline = seen_before is None
            already = _known_canonicals(known.get(provider, ())) | _known_canonicals(
                seen_before or ())
            already |= {canonical(k.split("/", 1)[1]) for k in self.state["adopted"]
                        if k.startswith(provider + "/")}

            fresh = [
                model for model in live
                if canonical(model) not in already
                and not (baseline and not is_successor(provider, model, profiles))
                and _key(provider, model) not in self.state["new"]
                and _key(provider, model) not in self.state["dismissed"]
            ]
            for model in _newest_per_family(fresh):
                self.state["new"][_key(provider, model)] = {"first_seen": today}
                summary.found.append(NewModel(provider, model, today))

            self.state["seen"][provider] = sorted(set(seen_before or ()) | set(live))

        self.state["last_scan"] = summary.checked_at
        self.state["last_notes"] = notes
        self.save()
        return summary

    # -- what the tile shows --------------------------------------------

    @property
    def last_scan(self) -> str:
        return self.state.get("last_scan", "")

    @property
    def last_notes(self) -> dict:
        return dict(self.state.get("last_notes", {}))

    def pending(self) -> list[NewModel]:
        """New models nobody has adopted or dismissed yet, newest first."""
        items = [
            NewModel(*key.split("/", 1), info.get("first_seen", ""))
            for key, info in self.state["new"].items()
            if key not in self.state["adopted"] and key not in self.state["dismissed"]
        ]
        items.sort(key=lambda n: n.key)
        return sorted(items, key=lambda n: n.first_seen, reverse=True)  # stable

    def is_pending(self, provider: str, model: str) -> bool:
        key = _key(provider, model)
        return (key in self.state["new"] and key not in self.state["adopted"]
                and key not in self.state["dismissed"])

    # -- decisions ------------------------------------------------------

    def dismiss(self, provider: str, model: str) -> None:
        key = _key(provider, model)
        if key not in self.state["dismissed"]:
            self.state["dismissed"].append(key)
        self.save()

    def adopt(self, assessment: Assessment, *, today: date | None = None) -> None:
        key = _key(assessment.provider, assessment.model)
        self.state["adopted"][key] = {
            "adopted_at": (today or date.today()).isoformat(),
            "sibling": assessment.inferred.sibling,
        }
        self.save()
        install_adoption(assessment.provider, assessment.model,
                         self.state["adopted"][key])

    def adopted_models(self, provider: str) -> list[str]:
        prefix = provider + "/"
        return sorted(k[len(prefix):] for k in self.state["adopted"] if k.startswith(prefix))

    def install(self) -> None:
        """Re-apply every adoption at startup."""
        for key, info in sorted(self.state["adopted"].items()):
            provider, _, model = key.partition("/")
            install_adoption(provider, model, info)


def install_adoption(provider: str, model: str, info: Mapping) -> None:
    """Register an adopted model with the router; move a BEST FIT where it wins.

    Re-rated and re-assessed every time rather than stored, so a price added
    in Settings → Pricing, or a change to its sibling, is weighed at the next
    start. Once Sentinel ships the model in MODEL_CATALOG itself, the shipped
    profile wins (see `catalog()`).
    """
    inferred = infer_profile(provider, model, mr.MODEL_CATALOG)
    if not inferred.rankable:
        return
    mr.register_profile(inferred.profile)
    profiles = mr.catalog()
    for agent, current_rec in list(mr.AGENT_RECOMMENDATIONS.items()):
        current = next((p for p in profiles if (p.provider, p.model)
                        == (current_rec.provider, current_rec.model)), None)
        verdict = beats(inferred.profile, current, agent)
        if verdict:
            mr.AGENT_RECOMMENDATIONS[agent] = Recommendation(
                provider, model,
                f"Adopted {info.get('adopted_at', '')}: {verdict} than {current_rec.model}.",
            )


def known_models(client_classes: Mapping[str, type],
                 catalog: Iterable[ModelProfile] | None = None) -> dict[str, list[str]]:
    """Every id Sentinel already ships, per provider."""
    profiles = tuple(catalog) if catalog is not None else mr.MODEL_CATALOG
    known: dict[str, list[str]] = {}
    for provider, cls in client_classes.items():
        known[provider] = list(getattr(cls, "KNOWN_MODELS", ()))
    for profile in profiles:
        known.setdefault(profile.provider, []).append(profile.model)
    return known


def scan(watch: ModelWatch, client_classes: Mapping[str, type],
         lister: Callable[[Mapping[str, type]], list[ProviderListing]] = list_live,
         ) -> ScanSummary:
    """List, compare, record. Blocking — run it off the UI thread."""
    return watch.record_scan(lister(client_classes), known_models(client_classes))

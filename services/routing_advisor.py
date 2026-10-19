"""Routing advisor: one check per new job, before anything is sent.

Byte-identical in Sentinel and Imprint (tests/test_providers_drift.py). Pure
Python: no Qt, no network, no clock except where a caller passes one, so the
whole decision can be tested and can run on the UI thread. Everything the
advisor knows about a model arrives in a `Candidate` the app builds from what
it already has cached (ratings, prices, the local inventory snapshot). It never
fetches and never waits on anything that does.

The advisor answers one question per job: run it as selected, or hold it and
suggest one alternative with named reasons, its cost and its time. It never
switches a job by itself. The only time a different model runs without a
prompt is a remembered rule, which the user created with *Always for this kind
of job* and can remove in Settings → Models, or Local first choosing a free
local model the user's mode asked it to prefer. Nothing here ever chooses a
paid model on its own: a suggestion is shown, and the app's own paid-request
confirmation still applies after the user accepts it.

Five modes, one app setting with an override per agent:

  local_advise   Local, advise per job (default). Jobs run on the selected
                 local model; when it falls short the job is held and the
                 auto-routed recommendation is suggested.
  suggest_local  The mirror image: from a cloud selection, offer a local model
                 that is good enough, with what it would save.
  local_first    A rated local model that is good enough runs at €0; if a
                 local run fails, the next candidate is offered (never sent).
  cloud_only     As before phase 4.
  local_only     No cloud calls, ever: no cloud suggestion, no cloud fallback.

Also here: the remembered rules (agent + kind of work -> model) and the
savings tally, each a small JSON file the app keeps in its data directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# ── Modes ────────────────────────────────────────────────────────────────────
LOCAL_ADVISE = "local_advise"
SUGGEST_LOCAL = "suggest_local"
LOCAL_FIRST = "local_first"
CLOUD_ONLY = "cloud_only"
LOCAL_ONLY = "local_only"

MODES = (LOCAL_ADVISE, SUGGEST_LOCAL, LOCAL_FIRST, CLOUD_ONLY, LOCAL_ONLY)
DEFAULT_MODE = LOCAL_ADVISE

MODE_LABELS = {
    LOCAL_ADVISE: "Local, advise per job",
    SUGGEST_LOCAL: "Suggest local",
    LOCAL_FIRST: "Local first",
    CLOUD_ONLY: "Cloud only",
    LOCAL_ONLY: "Local only",
}

MODE_HINTS = {
    LOCAL_ADVISE: ("Jobs run on your local model. When one needs more than it can "
                   "give, you'll be asked before anything is sent to a paid service."),
    SUGGEST_LOCAL: ("Jobs run on the model you picked. When a local model is good "
                    "enough for a job, you'll be offered it with what it saves."),
    LOCAL_FIRST: ("A rated local model that is good enough runs for free. If a "
                  "local run fails, you're offered the next model; a paid one "
                  "still asks first."),
    CLOUD_ONLY: "Jobs run on the cloud model you picked, as before.",
    LOCAL_ONLY: ("Nothing is sent to a cloud service. If no local model can do a "
                 "job, it stops and says why."),
}

# The setting keys, in each app's settings.json.
MODE_KEY = "routing_mode"
OVERRIDES_KEY = "routing_mode_overrides"

# The app's older three-way Execution mode, which the run path still reads.
LEGACY_EXECUTION = {
    LOCAL_ADVISE: "Hybrid allowed",
    SUGGEST_LOCAL: "Hybrid allowed",
    LOCAL_FIRST: "Hybrid allowed",
    CLOUD_ONLY: "Cloud only",
    LOCAL_ONLY: "Local only",
}

# The same band as the router's Balanced rule: within this many points of the
# best rating is good enough.
BAND = 20

# Rough throughput for the time estimate. Shown as "about N s", never as a
# promise; a local model's real speed depends on what else the Mac is doing.
CLOUD_TOKENS_PER_SECOND = 60.0
CLOUD_OVERHEAD_SECONDS = 1.5
LOCAL_TOKENS_PER_SECOND = 12.0
LOCAL_PROMPT_TOKENS_PER_SECOND = 400.0
DEFAULT_OUTPUT_TOKENS = 800


def normalise_mode(value) -> str:
    """A stored mode, or a label, as a mode key; anything unknown -> default."""
    text = str(value or "").strip()
    if text in MODES:
        return text
    for key, label in MODE_LABELS.items():
        if text.lower() == label.lower():
            return key
    return DEFAULT_MODE


def mode_for(settings: Mapping | None, agent: str | None = None) -> str:
    """The mode that applies to `agent`: its override, else the app setting."""
    settings = settings or {}
    overrides = settings.get(OVERRIDES_KEY) or {}
    if agent and isinstance(overrides, Mapping) and overrides.get(agent) in MODES:
        return overrides[agent]
    return normalise_mode(settings.get(MODE_KEY))


def effective_mode(settings: Mapping | None, agent: str | None = None,
                   execution: str | None = None) -> str:
    """The mode a job runs under, given the app's older Execution mode box.

    An agent's own override wins. Otherwise the box's *Local only* and *Cloud
    only* are hard limits the app has always honoured, so they win over the
    stored mode; *Hybrid allowed* means the stored mode, or the default when
    the stored one is Local only or Cloud only.
    """
    settings = settings or {}
    overrides = settings.get(OVERRIDES_KEY) or {}
    if agent and isinstance(overrides, Mapping) and overrides.get(agent) in MODES:
        return overrides[agent]
    if execution == LEGACY_EXECUTION[LOCAL_ONLY]:
        return LOCAL_ONLY
    if execution == LEGACY_EXECUTION[CLOUD_ONLY]:
        return CLOUD_ONLY
    mode = normalise_mode(settings.get(MODE_KEY))
    if execution and mode in (LOCAL_ONLY, CLOUD_ONLY):
        return DEFAULT_MODE
    return mode


def set_mode(settings: dict, mode: str, agent: str | None = None) -> None:
    """Store the app mode, or one agent's override (None or "" clears it)."""
    if agent is None:
        settings[MODE_KEY] = normalise_mode(mode)
        return
    overrides = dict(settings.get(OVERRIDES_KEY) or {})
    if mode in MODES:
        overrides[agent] = mode
    else:
        overrides.pop(agent, None)
    settings[OVERRIDES_KEY] = overrides


def allows_cloud(mode: str, *, privacy: bool = False) -> bool:
    """May anything in this mode reach a cloud service? Sentinel's Privacy
    first priority answers no, whatever the mode."""
    return mode != LOCAL_ONLY and not privacy


# ── What the advisor is told ────────────────────────────────────────────────
@dataclass(frozen=True)
class Candidate:
    """One model as the advisor sees it, for this job."""
    provider: str
    model: str
    local: bool
    rating: float | None = None       # for this job's kind of work
    rating_source: str = ""           # "LMArena", "measured", "base model"
    cost_eur: float | None = None     # this job's estimate; 0 local; None unknown
    context_window: int | None = None
    capabilities: frozenset = frozenset({"text"})
    runnable: bool = True
    not_runnable: str = ""            # why not, in words
    tokens_per_second: float | None = None
    tag: str = ""                     # "uncensored" / "abliterated", if any
    size_gb: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.model)

    @property
    def label(self) -> str:
        return f"{self.model} (local)" if self.local else self.model

    def same(self, other: "Candidate | None") -> bool:
        return other is not None and self.key == other.key


@dataclass(frozen=True)
class Job:
    agent: str
    kind: str                         # the router's task: writing, coding, …
    kind_label: str = ""              # how a person says it: "code review"
    context_tokens: int = 0           # prompt + conversation + attachments
    output_tokens: int = DEFAULT_OUTPUT_TOKENS
    required: frozenset = frozenset({"text"})
    summary: str = ""                 # "Code review across 9 files"

    @property
    def label(self) -> str:
        return self.kind_label or KIND_LABELS.get(self.kind, self.kind.replace("_", " "))


# The router's kinds of work, as a sentence says them ("Always for code jobs").
KIND_LABELS = {
    "coding": "code", "writing": "writing", "summarize": "summarising",
    "long_context": "long-document", "simple": "quick-question",
    "general": "general", "reasoning": "reasoning", "research": "research",
    "vision": "image-reading", "image_generation": "image",
    "analysis": "analysis", "extraction": "extraction",
}


# ── What it answers ─────────────────────────────────────────────────────────
QUALITY_GAP = "quality_gap"
CONTEXT = "context"
CAPABILITY = "capability"
NOT_RUNNABLE = "not_runnable"
UNMEASURED = "unmeasured"
REASON_CODES = (QUALITY_GAP, CONTEXT, CAPABILITY, NOT_RUNNABLE, UNMEASURED)
# The limits that make a job impossible on the model, not merely worse.
HARD_LIMITS = (CONTEXT, CAPABILITY, NOT_RUNNABLE)

RUN = "run"
SUGGEST = "suggest"
REFUSE = "refuse"


@dataclass(frozen=True)
class Reason:
    code: str
    text: str


@dataclass(frozen=True)
class Rule:
    agent: str
    kind: str
    provider: str
    model: str
    created: str = ""
    kind_label: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.agent, self.kind)


@dataclass(frozen=True)
class Advice:
    action: str                       # RUN, SUGGEST or REFUSE
    run: Candidate | None             # what runs now (RUN), or if kept (SUGGEST)
    suggestion: Candidate | None = None
    reasons: tuple[Reason, ...] = ()
    urge: bool = False                # False: suggest, but don't press it
    seconds: float | None = None      # the suggestion's time estimate
    saving_eur: float | None = None   # what the local suggestion would save
    rule: Rule | None = None          # a remembered rule decided this job
    mode: str = DEFAULT_MODE
    message: str = ""                 # one line for a refusal or a rule

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(r.code for r in self.reasons)

    @property
    def to_local(self) -> bool:
        return self.suggestion is not None and self.suggestion.local


# ── Estimates ───────────────────────────────────────────────────────────────
def estimate_seconds(candidate: Candidate, job: Job) -> float:
    out = max(int(job.output_tokens or 0), 1)
    if candidate.tokens_per_second:
        speed = float(candidate.tokens_per_second)
    else:
        speed = LOCAL_TOKENS_PER_SECOND if candidate.local else CLOUD_TOKENS_PER_SECOND
    if candidate.local:
        return max(1.0, job.context_tokens / LOCAL_PROMPT_TOKENS_PER_SECOND + out / speed)
    return CLOUD_OVERHEAD_SECONDS + out / speed


def tokens_text(n: int | None) -> str:
    if not n:
        return "unknown"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def euros(value: float | None) -> str:
    if value is None:
        return "price unknown"
    if value == 0:
        return "€0"
    return f"€{value:.2f}" if value >= 0.01 else "under €0.01"


# ── The limits of one model for one job ─────────────────────────────────────
def limits(candidate: Candidate, job: Job, best: Candidate | None = None,
           band: int = BAND) -> tuple[Reason, ...]:
    """Every reason `candidate` may fall short on `job`, each named.

    `best` is the best-rated model the user could use instead, which is what
    a quality gap is measured against.
    """
    reasons: list[Reason] = []
    if not candidate.runnable:
        reasons.append(Reason(NOT_RUNNABLE, candidate.not_runnable
                              or f"{candidate.label} cannot run right now."))
    if (job.context_tokens and candidate.context_window
            and job.context_tokens > candidate.context_window):
        reasons.append(Reason(CONTEXT, (
            f"{tokens_text(job.context_tokens)} tokens, but "
            f"{'the local' if candidate.local else 'its'} context is "
            f"{tokens_text(candidate.context_window)}.")))
    missing = sorted(set(job.required) - set(candidate.capabilities))
    if missing:
        reasons.append(Reason(CAPABILITY, (
            f"Needs {', '.join(m.replace('_', ' ') for m in missing)}, which "
            f"{candidate.label} does not have.")))
    if candidate.rating is None:
        reasons.append(Reason(UNMEASURED, (
            f"{candidate.label} has no rating for {job.label} yet, so nothing "
            "shows it is good enough.")))
    elif (best is not None and best.rating is not None
          and not best.same(candidate) and best.rating - candidate.rating > band):
        gap = best.rating - candidate.rating
        reasons.append(Reason(QUALITY_GAP, (
            f"{job.label.capitalize()} rating {candidate.rating:.0f}, which is "
            f"{gap:.0f} below {best.label}.")))
    return tuple(reasons)


def _fits(candidate: Candidate, job: Job, best: Candidate | None, band: int) -> bool:
    """Good enough and able: rated within the band and no limit at all."""
    return not limits(candidate, job, best, band)


def _best(candidates: Iterable[Candidate]) -> Candidate | None:
    rated = [c for c in candidates if c.rating is not None]
    if not rated:
        return None
    return max(rated, key=lambda c: (c.rating, -(c.cost_eur if c.cost_eur is not None else 1e9)))


def best_rated(candidates: Iterable[Candidate]) -> Candidate | None:
    """The best-rated candidate (the cheaper on a tie), or None."""
    return _best(candidates)


def _cheapest_fit(candidates: Iterable[Candidate], job: Job, best: Candidate | None,
                  band: int) -> Candidate | None:
    fit = [c for c in candidates if _fits(c, job, best, band)]
    if not fit:
        return None
    # A missing price is never cheap.
    return min(fit, key=lambda c: (c.cost_eur is None, c.cost_eur or 0.0,
                                   -(c.rating or 0), c.provider, c.model))


def _usable_suggestion(candidate: Candidate | None, job: Job) -> bool:
    """A suggestion must at least be able to do the job."""
    if candidate is None:
        return False
    return not any(r.code in HARD_LIMITS for r in limits(candidate, job))


# ── The decision ────────────────────────────────────────────────────────────
def advise(job: Job, selected: Candidate, *, mode: str = DEFAULT_MODE,
           local: Sequence[Candidate] = (), cloud: Sequence[Candidate] = (),
           recommended: Candidate | None = None,
           rules: "RoutingRules | Mapping | None" = None,
           privacy: bool = False, band: int = BAND,
           budget_left_eur: float | None = None) -> Advice:
    """Run as selected, or hold the job and suggest one alternative.

    `local` and `cloud` are every model the user could use for this job;
    `recommended` is the router's auto-routed pick for it (from any provider).
    `budget_left_eur` is what today's budget still allows: a paid suggestion
    over it is not made. `privacy` (Sentinel's Privacy first) removes every
    cloud model from consideration, as Local only does.
    """
    mode = normalise_mode(mode)
    cloud_ok = allows_cloud(mode, privacy=privacy)
    cloud = tuple(cloud) if cloud_ok else ()
    if budget_left_eur is not None:
        cloud = tuple(c for c in cloud if c.cost_eur is None or c.cost_eur <= budget_left_eur)
    local = tuple(local)
    if recommended is not None and not recommended.local and not any(
            recommended.same(c) for c in cloud):
        recommended = None            # not allowed here (mode, privacy, budget)
    everyone = local + cloud
    best = _best(everyone + ((selected,) if selected.rating is not None else ()))

    if mode == CLOUD_ONLY:
        if selected.local:
            return Advice(REFUSE, None, mode=mode,
                          message="Cloud only is selected, but the model is local.")
        return Advice(RUN, selected, mode=mode)

    if mode == LOCAL_ONLY:
        return _advise_local_only(job, selected, local, best, band, mode)

    # A remembered rule is the user's own earlier answer: follow it, unless
    # the mode now forbids its target or it can no longer do the job.
    rule = _rule_for(rules, job)
    if rule is not None:
        target = next((c for c in everyone if c.key == (rule.provider, rule.model)), None)
        if target is not None and not selected.same(target) and _usable_suggestion(target, job):
            return Advice(RUN, target, rule=rule, mode=mode, message=(
                f"Your rule: {job.label} jobs in {job.agent} run on "
                f"{target.label} (Settings → Models)."))

    if not selected.local:
        if mode in (SUGGEST_LOCAL, LOCAL_FIRST):
            local_pick = _cheapest_fit(local, job, best, band)
            if local_pick is not None and mode == LOCAL_FIRST:
                # The mode asked for this: a free model that is good enough.
                return Advice(RUN, local_pick, mode=mode, message=(
                    f"Local first: {local_pick.label} is good enough for "
                    f"{job.label}, so it runs at €0."))
            if local_pick is not None:
                return Advice(SUGGEST, selected, local_pick,
                              seconds=estimate_seconds(local_pick, job),
                              saving_eur=selected.cost_eur, mode=mode)
        return Advice(RUN, selected, mode=mode)

    # The selected model is local.
    reasons = limits(selected, job, best, band)
    if not reasons:
        return Advice(RUN, selected, mode=mode)
    suggestion = recommended if (recommended is not None
                                 and not recommended.same(selected)
                                 and _usable_suggestion(recommended, job)) else None
    if suggestion is None:
        suggestion = _cheapest_fit((c for c in everyone if not c.same(selected)),
                                   job, best, band)
    if suggestion is None:
        if any(r.code in HARD_LIMITS for r in reasons):
            return Advice(REFUSE, None, reasons=reasons, mode=mode, message=(
                f"{selected.label} cannot do this job and no other model "
                "you allow can."))
        return Advice(RUN, selected, reasons=reasons, mode=mode)
    urge = any(r.code != UNMEASURED for r in reasons)
    return Advice(SUGGEST, selected if not any(r.code in HARD_LIMITS for r in reasons) else None,
                  suggestion, reasons, urge=urge,
                  seconds=estimate_seconds(suggestion, job), mode=mode)


def _advise_local_only(job, selected, local, best, band, mode) -> Advice:
    if not selected.local:
        pick = _cheapest_fit(local, job, best, band) or next(
            (c for c in local if _usable_suggestion(c, job)), None)
        return Advice(REFUSE, None, mode=mode, message=(
            f"Local only: {selected.model} is a cloud model and nothing is sent "
            "to a cloud service." + (f" {pick.label} could do it." if pick else "")))
    reasons = limits(selected, job, best, band)
    if not any(r.code in HARD_LIMITS for r in reasons):
        return Advice(RUN, selected, reasons=reasons, mode=mode)
    other = _cheapest_fit((c for c in local if not c.same(selected)), job, best, band) or next(
        (c for c in local if not c.same(selected) and _usable_suggestion(c, job)), None)
    if other is None:
        return Advice(REFUSE, None, reasons=reasons, mode=mode, message=(
            "Local only: no local model can do this job. "
            + " ".join(r.text for r in reasons if r.code in HARD_LIMITS)))
    return Advice(SUGGEST, None, other, reasons, urge=True,
                  seconds=estimate_seconds(other, job), mode=mode)


def _rule_for(rules, job: Job) -> Rule | None:
    if rules is None:
        return None
    if isinstance(rules, RoutingRules):
        return rules.get(job.agent, job.kind)
    found = rules.get((job.agent, job.kind)) if isinstance(rules, Mapping) else None
    return found if isinstance(found, Rule) else None


def saved_by_local(job: Job, cloud: Sequence[Candidate], band: int = BAND) -> float | None:
    """What running a job locally saved: the estimate for the cloud model the
    value rule would have picked (the cheapest good-enough one, else the best
    rated). None when no cloud model is priced or allowed."""
    cloud = [c for c in cloud if c.cost_eur is not None]
    if not cloud:
        return None
    best = _best(cloud)
    pick = _cheapest_fit(cloud, job, best, band) or best or min(
        cloud, key=lambda c: c.cost_eur)
    return pick.cost_eur


# ── Words for the banner (mockup C) and the status line (mockup C′) ─────────
def headline(advice: Advice, selected: Candidate) -> str:
    if advice.action == REFUSE:
        return advice.message or "This job cannot run as set up."
    if advice.suggestion is None:
        return ""
    if advice.to_local and not selected.local:
        return f"{advice.suggestion.label} is good enough for this job"
    if advice.urge:
        return f"This job needs more than {selected.label}"
    return f"{selected.label} hasn't been measured for this kind of job"


def detail(advice: Advice, job: Job) -> str:
    parts = []
    if job.summary:
        parts.append(job.summary + ".")
    parts.extend(r.text for r in advice.reasons)
    s = advice.suggestion
    if s is not None:
        cost = euros(s.cost_eur)
        time = f"about {advice.seconds:.0f} s" if advice.seconds else ""
        if advice.to_local and advice.saving_eur:
            parts.append(f"Recommended: {s.label}, which saves about "
                         f"{euros(advice.saving_eur)}" + (f", {time}" if time else "") + ".")
        else:
            parts.append(f"Recommended: {s.label}, "
                         + ("free" if s.cost_eur == 0 else f"about {cost}")
                         + (f", {time}" if time else "") + ".")
        if s.tag:
            parts.append(f"{s.label} is {s.tag}: its safety tuning was removed.")
    return " ".join(parts)


def button_labels(advice: Advice, job: Job, selected: Candidate) -> dict:
    keep = "Keep local" if selected.local else f"Keep {selected.model}"
    return {
        "switch": "Switch for this job",
        "always": f"Always for {job.label} jobs",
        "keep": keep if advice.run is not None else "",
    }


def status_line(ran: Candidate, job: Job, best: Candidate | None,
                saved_eur: float | None, today_saved_eur: float | None) -> str:
    """✓ Ran on X (local) · writing 1281, within 37 of best · saved ≈ €0.03 ·
    today €0.41 saved"""
    parts = [f"✓ Ran on {ran.label}"]
    if ran.rating is not None:
        rating = f"{job.label} {ran.rating:.0f}"
        if best is not None and best.rating is not None and not best.same(ran):
            gap = best.rating - ran.rating
            rating += (f", within {gap:.0f} of best" if gap >= 0
                       else f", {-gap:.0f} above {best.label}")
        parts.append(rating)
    else:
        parts.append(f"unrated for {job.label}")
    if saved_eur:
        parts.append(f"saved ≈ {euros(saved_eur)}")
    if today_saved_eur:
        parts.append(f"today {euros(today_saved_eur)} saved")
    return " · ".join(parts)


# ── Failover at run time ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class FailoverOffer:
    failed: Candidate
    error: str
    cloud: Candidate | None = None    # "Send to <provider>": asks, never sends
    local: Candidate | None = None    # "Try a smaller local model"

    @property
    def empty(self) -> bool:
        return self.cloud is None and self.local is None


def failover(failed: Candidate, fallbacks: Sequence[Candidate], *, mode: str,
             error: str = "", privacy: bool = False, job: Job | None = None,
             budget_left_eur: float | None = None) -> FailoverOffer:
    """What to offer when a run failed (server down, out of memory, timeout).

    `fallbacks` are the router's own next candidates (RouteDecision.fallbacks
    in Sentinel, the engine's ranked runners-up in Imprint), best first. The
    result is only an offer; the app shows it and the user chooses. Local only
    and Privacy first never offer a cloud model; Cloud only never offers a
    local one.
    """
    mode = normalise_mode(mode)
    cloud_ok = allows_cloud(mode, privacy=privacy)
    cloud = local = None
    for c in fallbacks:
        if c.same(failed) or (job is not None and not _usable_suggestion(c, job)):
            continue
        if c.local:
            if local is None and mode != CLOUD_ONLY and c.runnable and (
                    failed.size_gb is None or c.size_gb is None
                    or c.size_gb < failed.size_gb or not failed.local):
                local = c
        elif cloud is None and cloud_ok and (
                budget_left_eur is None or c.cost_eur is None
                or c.cost_eur <= budget_left_eur):
            cloud = c
    return FailoverOffer(failed, error, cloud, local)


def failover_text(offer: FailoverOffer) -> str:
    lines = [f"{offer.failed.label} failed: {offer.error or 'no answer'}."]
    if offer.cloud is not None:
        lines.append(f"Next best: {offer.cloud.label}, about {euros(offer.cloud.cost_eur)}.")
    if offer.local is not None:
        lines.append(f"Or try {offer.local.label}, free.")
    if offer.empty:
        lines.append("No other model you allow can take it.")
    return " ".join(lines)


# ── Small JSON stores ───────────────────────────────────────────────────────
def _read_json(path: Path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class RoutingRules:
    """*Always for this kind of job*, per agent and kind of work."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._rules: dict[tuple[str, str], Rule] = {}
        if self.path is not None:
            for row in _read_json(self.path, []):
                try:
                    rule = Rule(**{k: str(row.get(k, "")) for k in
                                   ("agent", "kind", "provider", "model", "created",
                                    "kind_label")})
                except (AttributeError, TypeError):
                    continue
                if rule.agent and rule.kind and rule.provider and rule.model:
                    self._rules[rule.key] = rule

    def get(self, agent: str, kind: str) -> Rule | None:
        return self._rules.get((agent, kind))

    def all(self) -> list[Rule]:
        return sorted(self._rules.values(), key=lambda r: (r.agent, r.kind))

    def add(self, agent: str, kind: str, provider: str, model: str,
            kind_label: str = "", now: datetime | None = None) -> Rule:
        rule = Rule(agent, kind, provider, model,
                    (now or datetime.now()).isoformat(timespec="seconds"), kind_label)
        self._rules[rule.key] = rule
        self._save()
        return rule

    def remove(self, agent: str, kind: str) -> bool:
        found = self._rules.pop((agent, kind), None) is not None
        if found:
            self._save()
        return found

    def _save(self) -> None:
        if self.path is not None:
            _write_json(self.path, [asdict(r) for r in self.all()])


class SavingsTally:
    """Money saved by running local, and spent on suggestions you accepted.

    `saved` for a local run is what the cloud model the router would have
    picked was estimated to cost for the same job; `spent` is the billed cost
    of a job that ran on an accepted suggestion or a rule.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._days: dict = _read_json(self.path, {}) if self.path else {}

    def _day(self, day: date | None) -> dict:
        key = (day or date.today()).isoformat()
        return self._days.setdefault(key, {"saved": 0.0, "spent": 0.0,
                                           "local_runs": 0, "accepted": 0})

    def record_local_run(self, saved_eur: float | None, day: date | None = None) -> None:
        row = self._day(day)
        row["local_runs"] = int(row.get("local_runs", 0)) + 1
        if saved_eur:
            row["saved"] = round(float(row.get("saved", 0.0)) + float(saved_eur), 6)
        self._save()

    def record_accepted(self, spent_eur: float | None, day: date | None = None) -> None:
        row = self._day(day)
        row["accepted"] = int(row.get("accepted", 0)) + 1
        if spent_eur:
            row["spent"] = round(float(row.get("spent", 0.0)) + float(spent_eur), 6)
        self._save()

    def today(self, day: date | None = None) -> dict:
        row = self._days.get((day or date.today()).isoformat()) or {}
        return {"saved": float(row.get("saved", 0.0)), "spent": float(row.get("spent", 0.0)),
                "local_runs": int(row.get("local_runs", 0)),
                "accepted": int(row.get("accepted", 0))}

    def totals(self) -> dict:
        out = {"saved": 0.0, "spent": 0.0, "local_runs": 0, "accepted": 0}
        for row in self._days.values():
            out["saved"] += float(row.get("saved", 0.0))
            out["spent"] += float(row.get("spent", 0.0))
            out["local_runs"] += int(row.get("local_runs", 0))
            out["accepted"] += int(row.get("accepted", 0))
        return out

    def summary(self, day: date | None = None) -> str:
        t, all_ = self.today(day), self.totals()
        return (f"Local routing: saved {euros(t['saved']) if t['saved'] else '€0'} today "
                f"({euros(all_['saved']) if all_['saved'] else '€0'} in all) · "
                f"spent {euros(t['spent']) if t['spent'] else '€0'} today on accepted "
                f"suggestions ({euros(all_['spent']) if all_['spent'] else '€0'} in all)")

    def _save(self) -> None:
        if self.path is not None:
            _write_json(self.path, self._days)


__all__ = [name for name in dir() if not name.startswith("_") and name not in {
    "annotations", "json", "os", "tempfile", "Path", "date", "datetime",
    "Iterable", "Mapping", "Sequence", "asdict", "dataclass", "field", "replace"}]

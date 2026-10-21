"""Local model ratings — inherited from a base model, or measured on this Mac.

This file exists twice in the lab workspace, byte for byte, with its prompt
set `config/local_rating_prompts.json` (see `tests/test_providers_drift.py`).
Port any change to both.

LMArena does not rate quantised community builds, and the router's rule is
that an unrated model never beats a rated one. So a local model gets a rating
only from evidence, of one of two kinds, and every place that shows it says
which:

* **Inherited** — "rated (base model)". The model is an unmodified build of a
  model LMArena rates (Gemma 3 27B, Qwen3 32B, ...). It takes that rating for
  each kind of work minus a fixed penalty for its quantisation
  (`QUANT_PENALTY`). A fine-tune, merge, distill or abliteration is a
  different model and inherits nothing: its weights are not the ones that
  were voted on. Those are measured instead.
* **Measured** — about twenty prompts per kind of work (writing, code,
  summarising, extraction). The local model answers each, the current best
  cloud model for that kind answers it too, and a cheap cloud model judges
  the pair, once in each order so position bias cancels. The local model's
  share of the verdicts is turned into a rating gap with the formula an
  Elo-style rating is defined by and placed below the reference's LMArena
  rating (`place_on_scale`). It is never placed above the reference: a cheap
  judge can say how far below the best a model is, not that it beats it.

Measured beats inherited for the kinds of work it covers, because it was
measured on this very file. Kinds of work neither covers stay unrated.

The engine (`measure`) does network I/O through callables the app passes in,
so it is mockable, and it runs on a worker thread only. The app's spend guard
decides beforehand whether a run may start (`estimate_cost` is what it is
asked about), and the run stops if what it spends goes past what was
authorised. A model is measured automatically once, when it first appears;
every later run is a person pressing Measure or Re-measure.

Standard library only; no Qt.
"""

from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Mapping

from services import providers

PROMPTS_FILE = Path(__file__).resolve().parents[1] / "config" / "local_rating_prompts.json"

# Off in the test suites (conftest), so building a window never starts a run.
AUTOMATIC = True

# ── Kinds of work ────────────────────────────────────────────────────────────
# Each measured kind is placed against the leaderboard category of one router
# task, and rates the router tasks listed for it. Extraction is the everyday
# structured request, so it stands for "general" chat and quick answers.
KINDS = ("writing", "code", "summarising", "extraction")
KIND_TASK = {"writing": "writing", "code": "coding",
             "summarising": "summarize", "extraction": "general"}
TASKS_FROM_KIND = {"writing": ("writing",), "code": ("coding",),
                   "summarising": ("summarize",),
                   "extraction": ("general", "simple")}
KIND_LABELS = {"writing": "writing", "code": "code",
               "summarising": "summarising", "extraction": "extraction"}

SOURCE_BASE = "rated (base model)"
SOURCE_MEASURED = "measured"

# ── Quantisation penalty ─────────────────────────────────────────────────────
# Rating points taken off a base model's rating for the copy on this Mac.
# 8-bit and wider is indistinguishable from the original in published
# perplexity and preference tests; 4-6 bit loses a little; 3 bit and below
# loses enough to show. An unknown quantisation gets the largest penalty,
# because a rating must never credit a model with precision nobody saw.
PENALTY_FULL = 0
PENALTY_MID = 10
PENALTY_LOW = 25
UNKNOWN_QUANT_PENALTY = PENALTY_LOW

_QUANT_BITS = re.compile(r"^(?:I?Q|Q)(\d)", re.I)


def quant_penalty(quant: str | None) -> int:
    """Points off a base model's rating for this quantisation."""
    q = str(quant or "").strip().upper().replace("I1-", "")
    if q in {"F32", "F16", "BF16", "FP16", "FP32"}:
        return PENALTY_FULL
    if q in {"MXFP4", "FP8", "FP4"}:
        return PENALTY_FULL if q == "FP8" else PENALTY_MID
    found = _QUANT_BITS.match(q)
    if not found:
        return UNKNOWN_QUANT_PENALTY
    bits = int(found.group(1))
    if bits >= 8:
        return PENALTY_FULL
    if bits >= 4:
        return PENALTY_MID
    return PENALTY_LOW


# ── Which LMArena model is this a copy of? ───────────────────────────────────
# Families whose unmodified releases LMArena rates, and the provider name the
# leaderboard table files them under (benchmarks.ORGANISATIONS).
BASE_FAMILIES = (("gemma", "gemini"), ("qwq", "qwen"), ("qwen", "qwen"),
                 ("deepseek", "deepseek"))
# Words that mean the weights were changed after release. Such a model is not
# the one that was voted on, so it inherits nothing and is measured instead.
_MODIFIED = re.compile(
    r"abliterat|ablit(?![a-z])|uncensor|unfilter|unalign|dolphin|hermes|"
    r"distill|merge|finetun|fine-tun|-ft(?![a-z])|lora|heretic|roleplay|rp-|"
    r"nsfw|instruct-abl|josiefied|tiger|lexi", re.I)
_PACKAGING = re.compile(
    r"(?<![a-z0-9])(?:gguf|mlx|imatrix|imat|i1|qat|latest|\d+bit|"
    r"(?:i?q\d(?:_[a-z0-9]+)*)|f16|f32|bf16|mxfp4)(?![a-z0-9])", re.I)


# A local name must state its size to inherit. Ollama's "deepseek-r1:latest"
# is an 8B distill, not the 671B model LMArena rates as "deepseek-r1"; every
# rated model a Mac can hold names its size, so requiring one costs nothing.
_SIZE = re.compile(r"\d+(?:\.\d+)?b(?![a-z])")


def _stem(model_id: str) -> str:
    text = str(model_id or "").strip()
    text = text.split("@", 1)[0]                       # LM Studio "id@q4_k_m"
    parts = [p for p in text.split("/") if p]
    if parts and parts[-1].lower().endswith(".gguf"):
        # publisher/repo/file.gguf: the repo names the model; the file adds
        # the quant and sometimes a shorter spelling.
        text = parts[-2] if len(parts) >= 2 else parts[-1][:-5]
    elif parts:
        text = parts[-1]
    text = text.lower().replace("_", "-").replace(" ", "-")
    if ":" in text:                                    # Ollama "gemma3:27b"
        name, tag = text.split(":", 1)
        text = name if tag in {"", "latest"} else f"{name}-{tag}"
    text = _PACKAGING.sub("", text)
    return re.sub(r"-{2,}", "-", text).strip("-.")


def base_candidates(model_id: str, name: str = "") -> list[tuple[str, str]]:
    """(leaderboard provider, model name) pairs this local model may be.

    Empty when the model is modified (a fine-tune, distill, abliteration ...)
    or not from a family LMArena rates.
    """
    out: list[tuple[str, str]] = []
    for raw in (model_id, name):
        if not raw:
            continue
        if _MODIFIED.search(str(raw)):
            return []
        stem = _stem(raw)
        stem = re.sub(r"^gemma(\d)", r"gemma-\1", stem)     # Ollama gemma3
        family = next((prov for prefix, prov in BASE_FAMILIES
                       if stem.startswith(prefix)), None)
        if family is None or not _SIZE.search(stem):
            continue
        bare = re.sub(r"-(it|instruct)$", "", stem)
        for candidate in (stem, bare, f"{bare}-it", f"{bare}-instruct"):
            if candidate and (family, candidate) not in out:
                out.append((family, candidate))
    return out


@dataclass(frozen=True)
class LocalRating:
    """A local model's rating for one kind of work; quacks like benchmarks.Rating."""
    score: float
    category: str
    source: str                 # SOURCE_BASE or SOURCE_MEASURED
    detail: str = ""            # "gemma-3-27b-it 1338 − 10" / "vs claude-… · 20 prompts"
    base: float | None = None   # the base model's rating before the penalty
    penalty: int = 0

    @property
    def label(self) -> str:
        return self.category


def inherited_rating(table, model_id: str, quant: str, task: str,
                     name: str = "") -> LocalRating | None:
    """The base model's rating for `task` minus the quantisation penalty."""
    if not table:
        return None
    for provider, candidate in base_candidates(model_id, name):
        try:
            rating = table.rating(provider, candidate, task)
        except Exception:
            rating = None
        if rating is None:
            continue
        penalty = quant_penalty(quant)
        return LocalRating(
            score=float(rating.score) - penalty, category=rating.label,
            source=SOURCE_BASE,
            detail=f"{rating.arena_name} {rating.score:.0f} − {penalty}",
            base=float(rating.score), penalty=penalty)
    return None


# ── Placing a measured result on the LMArena scale ──────────────────────────

def win_share(wins: int, ties: int, losses: int) -> float:
    """The local model's share of verdicts, ties half, with a half-verdict prior
    on each side so a clean sweep is a large gap rather than an infinite one."""
    n = wins + ties + losses
    return (wins + 0.5 * ties + 0.5) / (n + 1.0)


def place_on_scale(reference_score: float, wins: int, ties: int,
                   losses: int) -> float:
    """The local model's rating, given its record against the reference.

    An Elo-style rating means: a gap of d points is an expected share of
    1 / (1 + 10^(d/400)). Inverted, a share p is a gap of 400·log10(p/(1−p)).
    Capped at the reference, never above it (see the module notes).
    """
    p = win_share(wins, ties, losses)
    gap = 400.0 * math.log10(p / (1.0 - p))
    return round(min(reference_score, reference_score + gap), 1)


# ── Stored results ───────────────────────────────────────────────────────────

STATUS_MEASURED = "measured"
STATUS_REFUSED = "refused"       # the spend guard said no, or no key is set
STATUS_FAILED = "failed"         # the local model or a cloud call failed
STATUS_STOPPED = "stopped"       # spent what was authorised before finishing


def model_key(provider: str, model_id: str, quant: str = "") -> str:
    return f"{provider}|{model_id}|{str(quant or '').upper()}"


def _key_of(model) -> str:
    return model_key(model.provider, model.id, getattr(model, "quant", ""))


class RatingStore:
    """Measured ratings and cached reference answers, in one JSON file.

    Per model + quant: status, the date it was last tried, why it is unrated
    when it is, and per kind of work the measured score with the reference,
    judge, record and date it rests on. Reference answers are cached per
    reference model and prompt so a second local model does not pay for them
    again. Safe to use from any thread; every write is atomic.
    """

    VERSION = 1

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._data = {"version": self.VERSION, "models": {}, "references": {}}
        if self.path is not None:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data["models"] = dict(loaded.get("models") or {})
                    self._data["references"] = dict(loaded.get("references") or {})
            except (OSError, ValueError):
                pass

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def entry(self, model) -> dict | None:
        with self._lock:
            found = self._data["models"].get(_key_of(model))
            return json.loads(json.dumps(found)) if found else None

    def attempted(self, model) -> bool:
        """Whether a run was ever started or refused for this model + quant."""
        return self.entry(model) is not None

    def measured(self, provider: str, model_id: str, quant: str,
                 kind: str) -> dict | None:
        with self._lock:
            row = self._data["models"].get(model_key(provider, model_id, quant))
            kinds = (row or {}).get("kinds") or {}
            return dict(kinds[kind]) if kind in kinds else None

    def _update(self, model, **fields) -> None:
        with self._lock:
            row = self._data["models"].setdefault(_key_of(model), {})
            row.update(fields)
            self._save()

    def record_refusal(self, model, reason: str, when: str | None = None,
                       automatic: bool = False) -> None:
        self._update(model, status=STATUS_REFUSED, reason=str(reason),
                     attempted=when or date.today().isoformat(),
                     automatic=bool(automatic))

    def record_result(self, model, result: "MeasureResult",
                      automatic: bool = False) -> None:
        with self._lock:
            row = self._data["models"].setdefault(_key_of(model), {})
            kinds = dict(row.get("kinds") or {})
            kinds.update(result.kinds)
            row.update(status=result.status, reason=result.reason,
                       attempted=result.date, automatic=bool(automatic),
                       kinds=kinds)
            self._save()

    def reference_answer(self, reference: str, prompt_id: str,
                         set_version: int) -> str | None:
        with self._lock:
            return self._data["references"].get(
                f"{reference}|{set_version}|{prompt_id}")

    def save_reference_answer(self, reference: str, prompt_id: str,
                              set_version: int, text: str) -> None:
        with self._lock:
            self._data["references"][f"{reference}|{set_version}|{prompt_id}"] = text
            self._save()


# ── What the router and the pages read ───────────────────────────────────────

class LocalRatings:
    """Ratings for local models, read from the store and the LMArena table.

    `rating(provider, model_id, task)` has the router's RatingLookup shape.
    It reads caches only — the inventory snapshot, the store, the table — so
    it is safe on the UI thread. `progress` is set by the app while a run
    is going, so a page can say "measuring… 12/80".
    """

    def __init__(self, store: RatingStore, inventory=None,
                 table: Callable[[], object] | None = None):
        self.store = store
        self.inventory = inventory
        self.table = table or (lambda: None)
        self._lock = threading.Lock()
        self._progress: dict[str, tuple[int, int]] = {}

    def _quant(self, provider: str, model_id: str) -> tuple[str, str]:
        snap = self.inventory.snapshot() if self.inventory is not None else None
        found = snap.find(provider, model_id) if snap is not None else None
        return (found.quant, found.name) if found is not None else ("", "")

    def rating(self, provider: str, model_id: str, task: str) -> LocalRating | None:
        if not providers.is_local(provider):
            return None
        quant, name = self._quant(provider, model_id)
        for kind, tasks in TASKS_FROM_KIND.items():
            if task in tasks:
                row = self.store.measured(provider, model_id, quant, kind)
                if row and row.get("score") is not None:
                    return LocalRating(
                        score=float(row["score"]), category=KIND_LABELS[kind],
                        source=SOURCE_MEASURED,
                        detail=(f"vs {row.get('reference', '?')} · "
                                f"{row.get('n', 0)} prompts · judged by "
                                f"{row.get('judge', '?')} · {row.get('date', '')}"))
        try:
            table = self.table()
        except Exception:
            table = None
        return inherited_rating(table, model_id, quant, task, name)

    def set_progress(self, model, done: int, total: int) -> None:
        with self._lock:
            self._progress[_key_of(model)] = (int(done), int(total))

    def clear_progress(self, model) -> None:
        with self._lock:
            self._progress.pop(_key_of(model), None)

    def progress(self, model) -> tuple[int, int] | None:
        with self._lock:
            return self._progress.get(_key_of(model))

    def text(self, provider: str, model_id: str) -> str | None:
        """The short rating text a picker row and the Settings table show."""
        quant, name = self._quant(provider, model_id)
        ref = _Ref(provider, model_id, quant)
        going = self.progress(ref)
        if going is not None:
            return f"measuring… {going[0]}/{going[1]}"
        measured = []
        for kind in KINDS:
            row = self.store.measured(provider, model_id, quant, kind)
            if row and row.get("score") is not None:
                measured.append(f"{KIND_LABELS[kind]} {float(row['score']):.0f}")
        if measured:
            return "measured " + " · ".join(measured)
        try:
            table = self.table()
        except Exception:
            table = None
        inherited = inherited_rating(table, model_id, quant, "general", name)
        if inherited is not None:
            return f"base model {inherited.base:.0f} − {inherited.penalty}"
        return None

    def action(self, model) -> str:
        """'Measure', 'Re-measure' or '' (a run is going) for a Settings row."""
        if self.progress(model) is not None:
            return ""
        entry = self.store.entry(model)
        return "Re-measure" if entry and entry.get("status") == STATUS_MEASURED \
            else "Measure"

    def note(self, model) -> str:
        """Why a model is unrated, or how it was rated: the row's small print."""
        if self.progress(model) is not None:
            return "new · automatic" if not self.store.attempted(model) else "running"
        entry = self.store.entry(model)
        if not entry:
            return "not measured yet"
        if entry.get("status") == STATUS_MEASURED:
            return f"measured {entry.get('attempted', '')}".strip()
        return entry.get("reason") or entry.get("status", "")


@dataclass(frozen=True)
class _Ref:
    provider: str
    id: str
    quant: str = ""


def combined_lookup(cloud_rating, local: LocalRatings):
    """One RatingLookup for the router: local ratings for local providers,
    the LMArena table for everything else."""
    def lookup(provider, model, task):
        if providers.is_local(provider):
            return local.rating(provider, model, task)
        return cloud_rating(provider, model, task) if cloud_rating else None
    return lookup


# ── Which local models to measure automatically ─────────────────────────────

def auto_candidates(snapshot, store: RatingStore) -> list:
    """Local models never measured and runnable right now, in discovery order.

    A model that is too big for free memory, or behind an LM Studio server
    that is off, waits until it can run: trying it would only record a
    failure and use up its one automatic run.
    """
    out = []
    for model in getattr(snapshot, "models", ()):
        if store.attempted(model):
            continue
        if getattr(model, "fit", "") in {"tight", "too_big"}:
            continue
        if not getattr(model, "server_up", True):
            continue
        out.append(model)
    return out


# ── The test set ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptSet:
    version: int
    kinds: Mapping[str, tuple[tuple[str, str], ...]]   # kind -> ((id, prompt), ...)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.kinds.values())


def load_prompts(path: Path | str | None = None) -> PromptSet:
    data = json.loads(Path(path or PROMPTS_FILE).read_text(encoding="utf-8"))
    kinds = {}
    for kind in KINDS:
        rows = data.get("kinds", {}).get(kind, [])
        kinds[kind] = tuple((str(r["id"]), str(r["prompt"])) for r in rows)
    return PromptSet(int(data.get("version", 1)), kinds)


# ── Choosing the reference and the judge ────────────────────────────────────

@dataclass(frozen=True)
class Plan:
    references: Mapping[str, tuple[str, str, float]]   # kind -> (provider, model, score)
    judge: tuple[str, str]

    def cloud_models(self) -> list[tuple[str, str]]:
        seen = []
        for provider, model, _ in self.references.values():
            if (provider, model) not in seen:
                seen.append((provider, model))
        if self.judge not in seen:
            seen.append(self.judge)
        return seen


def choose_plan(cloud_models: Iterable[tuple[str, str]], rating,
                price: Callable[[str, str], float | None]
                ) -> tuple[Plan | None, str]:
    """(plan, "") or (None, why not).

    `cloud_models` are the (provider, model) pairs the app can use now (a key
    is set and the provider is allowed). The reference for each kind is the
    best-rated of them for that kind's task; the judge is the cheapest of
    them with a known price. `price` is a blended USD per 1M, None if unknown.
    """
    usable = [(p, m) for p, m in cloud_models if providers.is_cloud(p)]
    if not usable:
        return None, "no cloud API key is set, so there is nothing to compare against"
    references = {}
    for kind in KINDS:
        task = KIND_TASK[kind]
        rated = []
        for p, m in usable:
            try:
                r = rating(p, m, task)
            except Exception:
                r = None
            if r is not None:
                rated.append((float(r.score), p, m))
        if not rated:
            return None, f"no usable cloud model is rated for {KIND_LABELS[kind]}"
        score, p, m = max(rated, key=lambda t: (t[0], t[1], t[2]))
        references[kind] = (p, m, score)
    priced = []
    for p, m in usable:
        try:
            value = price(p, m)
        except Exception:
            value = None
        if value is not None and value > 0:
            priced.append((value, p, m))
    if not priced:
        return None, "no usable cloud model has a known price to judge with"
    _, jp, jm = min(priced)
    return Plan(references=references, judge=(jp, jm)), ""


# ── Running a measurement (worker thread only) ───────────────────────────────

JUDGE_PROMPT = """You are judging two answers to the same request. Judge only \
which answer serves the request better: correctness first, then completeness, \
then clarity. Ignore length unless the request sets one. Do not explain.

REQUEST:
{prompt}

ANSWER A:
{a}

ANSWER B:
{b}

Reply with exactly one word: A, B or TIE."""

# Rough token sizes for the cost estimate the spend guard is asked about.
EST_PROMPT_TOKENS = 220
EST_ANSWER_TOKENS = 350
EST_VERDICT_TOKENS = 4


_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """The answer without a reasoning model's <think> block, which the judge
    must not weigh: it is the model's working, not what a person reads."""
    return _THINK.sub("", str(text or "")).strip()


def parse_verdict(text: str) -> str:
    """'A', 'B' or 'TIE' from a judge's reply; anything unclear is a tie."""
    words = re.findall(r"[A-Za-z]+", str(text or "").upper())
    for word in words:
        if word in {"A", "B", "TIE"}:
            return word
    return "TIE"


def estimate_cost(plan: Plan, prompts: PromptSet,
                  cost: Callable[[str, str, int, int], float],
                  store: RatingStore | None = None) -> float:
    """EUR the run would cost at most: reference answers not yet cached,
    plus two judge calls per prompt. `cost(provider, model, in, out)`."""
    total = 0.0
    for kind, rows in prompts.kinds.items():
        ref = plan.references.get(kind)
        if ref is None:
            continue
        ref_name = f"{ref[0]}/{ref[1]}"
        for prompt_id, _ in rows:
            if store is None or store.reference_answer(
                    ref_name, prompt_id, prompts.version) is None:
                total += cost(ref[0], ref[1], EST_PROMPT_TOKENS, EST_ANSWER_TOKENS)
            total += 2 * cost(plan.judge[0], plan.judge[1],
                              EST_PROMPT_TOKENS + 2 * EST_ANSWER_TOKENS + 80,
                              EST_VERDICT_TOKENS)
    return round(total, 4)


class BudgetSpent(RuntimeError):
    pass


@dataclass
class MeasureResult:
    status: str
    reason: str = ""
    date: str = field(default_factory=lambda: date.today().isoformat())
    kinds: dict = field(default_factory=dict)
    spent_eur: float = 0.0


def measure(model, plan: Plan, prompts: PromptSet,
            ask: Callable[[str, str, str], str], *,
            store: RatingStore | None = None,
            on_progress: Callable[[int, int], None] | None = None,
            on_usage: Callable[[str, str, str, str], None] | None = None,
            cost: Callable[[str, str, str, str], float] | None = None,
            budget_eur: float | None = None,
            cancelled: Callable[[], bool] | None = None,
            today: str | None = None) -> MeasureResult:
    """Measure one local model on the whole prompt set. Blocking; never raises.

    `ask(provider, model, prompt)` returns the reply text. `on_usage` is
    called after every cloud call, so the app can bill it; `cost` prices one
    such call (provider, model, prompt, reply) in EUR and the run stops once
    the total passes `budget_eur`.
    """
    when = today or date.today().isoformat()
    total = prompts.total
    done = 0
    spent = 0.0
    kinds: dict = {}
    judge_name = f"{plan.judge[0]}/{plan.judge[1]}"

    def cloud(provider, name, text):
        nonlocal spent
        reply = str(ask(provider, name, text) or "")
        if on_usage is not None:
            on_usage(provider, name, text, reply)
        if cost is not None:
            spent += float(cost(provider, name, text, reply) or 0.0)
            if budget_eur is not None and spent > budget_eur + 1e-9:
                raise BudgetSpent(
                    f"spent €{spent:.3f}, more than the €{budget_eur:.3f} authorised")
        return reply

    if on_progress is not None:
        on_progress(0, total)
    try:
        for kind in KINDS:
            rows = prompts.kinds.get(kind, ())
            ref = plan.references.get(kind)
            if not rows or ref is None:
                continue
            ref_provider, ref_model, ref_score = ref
            ref_name = f"{ref_provider}/{ref_model}"
            wins = ties = losses = 0
            for prompt_id, prompt in rows:
                if cancelled is not None and cancelled():
                    return MeasureResult(STATUS_STOPPED, "cancelled", when, kinds, spent)
                try:
                    local_answer = strip_reasoning(ask(model.provider, model.id, prompt))
                except Exception as exc:
                    return MeasureResult(
                        STATUS_FAILED, f"the local model did not answer: {exc}",
                        when, kinds, spent)
                reference = (store.reference_answer(ref_name, prompt_id, prompts.version)
                             if store is not None else None)
                if reference is None:
                    reference = strip_reasoning(cloud(ref_provider, ref_model, prompt))
                    if store is not None:
                        store.save_reference_answer(ref_name, prompt_id,
                                                    prompts.version, reference)
                first = parse_verdict(cloud(*plan.judge, JUDGE_PROMPT.format(
                    prompt=prompt, a=local_answer, b=reference)))
                second = parse_verdict(cloud(*plan.judge, JUDGE_PROMPT.format(
                    prompt=prompt, a=reference, b=local_answer)))
                local_votes = (first == "A") + (second == "B")
                ref_votes = (first == "B") + (second == "A")
                if local_votes > ref_votes:
                    wins += 1
                elif ref_votes > local_votes:
                    losses += 1
                else:
                    ties += 1
                done += 1
                if on_progress is not None:
                    on_progress(done, total)
            n = wins + ties + losses
            kinds[kind] = {
                "score": place_on_scale(ref_score, wins, ties, losses),
                "reference": ref_name, "reference_score": ref_score,
                "judge": judge_name, "wins": wins, "ties": ties,
                "losses": losses, "n": n, "date": when,
                "set_version": prompts.version,
            }
    except BudgetSpent as exc:
        return MeasureResult(STATUS_STOPPED, str(exc), when, kinds, spent)
    except Exception as exc:                  # a cloud call failed
        return MeasureResult(STATUS_FAILED, f"a cloud call failed: {exc}",
                             when, kinds, spent)
    return MeasureResult(STATUS_MEASURED, "", when, kinds, spent)

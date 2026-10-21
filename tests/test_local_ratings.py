"""Local model ratings (routing phase 3): services/local_ratings.py.

Shared by Sentinel and Imprint, like the module it tests. Nothing here talks
to a network, a cloud API or LM Studio: every model call is a stub.
"""

import json
import math
import threading
from types import SimpleNamespace

import pytest

from services import local_ratings as lr
from services.local_models import LocalInventory, LocalModel, Snapshot


# ── the quantisation penalty table ──────────────────────────────────────────

@pytest.mark.parametrize("quant, points", [
    ("F16", 0), ("BF16", 0), ("F32", 0), ("Q8_0", 0), ("q8_0", 0),
    ("Q6_K", 10), ("Q5_K_M", 10), ("Q4_K_M", 10), ("Q4_0", 10), ("IQ4_XS", 10),
    ("MXFP4", 10),
    ("IQ3_M", 25), ("Q3_K_L", 25), ("Q2_K", 25), ("IQ2_XXS", 25), ("IQ1_S", 25),
    ("i1-IQ3_M", 25),
    ("", 25), (None, 25), ("weird", 25),        # unknown: the largest penalty
])
def test_quant_penalty_table(quant, points):
    assert lr.quant_penalty(quant) == points


def test_penalty_constants_live_in_one_place():
    assert (lr.PENALTY_FULL, lr.PENALTY_MID, lr.PENALTY_LOW) == (0, 10, 25)
    assert lr.UNKNOWN_QUANT_PENALTY == lr.PENALTY_LOW


# ── which LMArena model a local build inherits from ─────────────────────────

class FakeTable:
    """benchmarks.RatingTable's .rating(provider, model, task) shape."""

    def __init__(self, scores):
        self.scores = scores          # {(provider, name, task): score}

    def __bool__(self):
        return True

    def rating(self, provider, model, task):
        score = self.scores.get((provider, model, task))
        if score is None:
            return None
        return SimpleNamespace(score=score, label="creative writing",
                               arena_name=model)


TABLE = FakeTable({
    ("gemini", "gemma-3-27b-it", "writing"): 1348.7,
    ("gemini", "gemma-3-27b-it", "general"): 1340.0,
    ("qwen", "qwen3-32b", "writing"): 1303.9,
    ("deepseek", "deepseek-r1", "writing"): 1400.0,
})


@pytest.mark.parametrize("model_id, expected", [
    ("google/gemma-3-27b-it", ("gemini", "gemma-3-27b-it")),
    ("lmstudio-community/gemma-3-27b-it-GGUF/gemma-3-27b-it-Q4_K_M.gguf",
     ("gemini", "gemma-3-27b-it")),
    ("gemma3:27b", ("gemini", "gemma-3-27b-it")),           # Ollama spelling
    ("qwen3:32b", ("qwen", "qwen3-32b")),
    ("Qwen3-32B@q4_k_m", ("qwen", "qwen3-32b")),            # LM Studio server id
])
def test_unmodified_builds_map_to_their_base_model(model_id, expected):
    found = lr.inherited_rating(TABLE, model_id, "Q4_K_M", "writing")
    assert found is not None
    assert (lr.base_candidates(model_id)[0][0], found.detail.split()[0]) == expected


@pytest.mark.parametrize("model_id", [
    "deepseek-r1-distill-qwen-14b-abliterated",       # distill + abliterated
    "huihui-ai/gemma-3-27b-it-abliterated",
    "dolphin-3.0-llama3.1-8b",                         # fine-tune
    "deepseek-r1:latest",                              # Ollama's 8B distill, no size
    "deepseek-r1",
    "mistral-small-24b",                               # family LMArena rows drop
])
def test_modified_or_unsized_builds_inherit_nothing(model_id):
    assert lr.base_candidates(model_id) == [] or \
        lr.inherited_rating(TABLE, model_id, "Q8_0", "writing") is None


def test_inherited_rating_is_base_minus_quant_penalty():
    q8 = lr.inherited_rating(TABLE, "google/gemma-3-27b-it", "Q8_0", "writing")
    q4 = lr.inherited_rating(TABLE, "google/gemma-3-27b-it", "Q4_K_M", "writing")
    iq3 = lr.inherited_rating(TABLE, "google/gemma-3-27b-it", "IQ3_M", "writing")
    assert q8.score == pytest.approx(1348.7)
    assert q4.score == pytest.approx(1338.7)
    assert iq3.score == pytest.approx(1323.7)
    assert q4.source == lr.SOURCE_BASE == "rated (base model)"
    assert (q4.base, q4.penalty) == (pytest.approx(1348.7), 10)
    assert lr.inherited_rating(TABLE, "google/gemma-3-27b-it", "Q4_K_M", "coding") is None
    assert lr.inherited_rating(None, "google/gemma-3-27b-it", "Q4_K_M", "writing") is None


def test_the_real_snapshot_rates_gemma_and_qwen_bases():
    from services import benchmarks
    table = benchmarks.load(benchmarks.SNAPSHOT_FILE.with_name("nope.json"))
    assert lr.inherited_rating(table, "gemma3:27b", "Q4_K_M", "writing") is not None
    assert lr.inherited_rating(table, "qwen3:32b", "Q4_K_M", "coding") is not None


# ── placing a measured record on the LMArena scale ──────────────────────────

def test_an_even_record_places_the_model_at_the_reference():
    assert lr.place_on_scale(1400, 5, 10, 5) == pytest.approx(1400)


def test_losing_records_place_below_with_the_elo_formula():
    # 5 wins of 20, no ties: share (5 + 0.5) / 21.
    p = 5.5 / 21
    expected = 1400 + 400 * math.log10(p / (1 - p))
    assert lr.place_on_scale(1400, 5, 0, 15) == pytest.approx(expected, abs=0.1)
    assert lr.place_on_scale(1400, 0, 0, 20) < lr.place_on_scale(1400, 5, 0, 15)


def test_never_placed_above_the_reference():
    assert lr.place_on_scale(1400, 20, 0, 0) == 1400
    assert lr.place_on_scale(1400, 12, 4, 4) == 1400


def test_a_sweep_is_a_finite_gap():
    assert math.isfinite(lr.place_on_scale(1400, 0, 0, 20))


# ── the test set ────────────────────────────────────────────────────────────

def test_the_prompt_set_has_about_twenty_per_kind():
    prompts = lr.load_prompts()
    assert set(prompts.kinds) == set(lr.KINDS)
    assert all(len(rows) == 20 for rows in prompts.kinds.values())
    ids = [i for rows in prompts.kinds.values() for i, _ in rows]
    assert len(ids) == len(set(ids)) == prompts.total == 80
    assert all(prompt.strip() for rows in prompts.kinds.values() for _, prompt in rows)


def test_every_measured_kind_rates_a_router_task_with_a_category():
    from services import benchmarks
    for kind in lr.KINDS:
        assert lr.KIND_TASK[kind] in benchmarks.TASK_CATEGORIES
        assert lr.KIND_TASK[kind] in lr.TASKS_FROM_KIND[kind]


# ── choosing the reference and the judge ────────────────────────────────────

def _rating_for(scores):
    return lambda p, m, t: (SimpleNamespace(score=scores[(p, m)])
                            if (p, m) in scores else None)


def test_plan_picks_the_best_rated_reference_and_the_cheapest_priced_judge():
    models = [("anthropic", "claude-opus-5-5"), ("gemini", "gemini-3.8-flash"),
              ("openai", "gpt-zero")]
    rating = _rating_for({("anthropic", "claude-opus-5-5"): 1450,
                          ("gemini", "gemini-3.8-flash"): 1380,
                          ("openai", "gpt-zero"): 1300})
    prices = {("anthropic", "claude-opus-5-5"): 9.0,
              ("gemini", "gemini-3.8-flash"): 0.5,
              ("openai", "gpt-zero"): 0.0}       # zero is unknown, never "free"
    plan, why = lr.choose_plan(models, rating, lambda p, m: prices[(p, m)])
    assert why == ""
    assert all(ref[:2] == ("anthropic", "claude-opus-5-5")
               for ref in plan.references.values())
    assert plan.judge == ("gemini", "gemini-3.8-flash")


def test_no_cloud_key_means_no_plan():
    plan, why = lr.choose_plan([("lmstudio", "x")], _rating_for({}), lambda p, m: 1)
    assert plan is None and "key" in why
    plan, why = lr.choose_plan([], _rating_for({}), lambda p, m: 1)
    assert plan is None


def test_no_known_price_means_no_judge():
    plan, why = lr.choose_plan([("openai", "a")], _rating_for({("openai", "a"): 1}),
                               lambda p, m: None)
    assert plan is None and "price" in why


# ── running a measurement ───────────────────────────────────────────────────

LOCAL = LocalModel(provider="lmstudio", id="dolphin-3.0-llama3.1-8b",
                   name="Dolphin", quant="Q8_0")
PLAN = lr.Plan(references={k: ("anthropic", "claude-opus-5-5", 1400.0)
                           for k in lr.KINDS},
               judge=("gemini", "gemini-3.8-flash"))


def tiny_prompts(n=2):
    return lr.PromptSet(1, {k: tuple((f"{k}{i}", f"{k} prompt {i}") for i in range(n))
                            for k in lr.KINDS})


class Asker:
    """A stub for every model: the local model, the reference, the judge."""

    def __init__(self, verdicts=("B", "A"), local_fails=False, cloud_fails=False):
        self.calls, self.verdicts = [], list(verdicts)
        self.local_fails, self.cloud_fails = local_fails, cloud_fails
        self.threads = set()

    def __call__(self, provider, model, prompt):
        self.calls.append((provider, model))
        self.threads.add(threading.current_thread())
        if provider == "lmstudio":
            if self.local_fails:
                raise RuntimeError("server down")
            return "<think>long working</think>local answer"
        if self.cloud_fails:
            raise RuntimeError("HTTP 500")
        if provider == "anthropic":
            return "reference answer"
        assert "<think>" not in prompt         # reasoning is stripped before judging
        verdict = self.verdicts[0]
        self.verdicts = self.verdicts[1:] + self.verdicts[:1]
        return verdict


def test_measure_scores_every_kind_and_records_its_evidence(tmp_path):
    store = lr.RatingStore(tmp_path / "r.json")
    ask = Asker(verdicts=("B", "A"))          # the reference wins in both orders
    progress, usage = [], []
    result = lr.measure(LOCAL, PLAN, tiny_prompts(), ask, store=store,
                        on_progress=lambda d, t: progress.append((d, t)),
                        on_usage=lambda *a: usage.append(a[:2]), today="2026-10-19")
    assert result.status == lr.STATUS_MEASURED
    assert progress[0] == (0, 8) and progress[-1] == (8, 8)
    row = result.kinds["writing"]
    assert (row["wins"], row["ties"], row["losses"], row["n"]) == (0, 0, 2, 2)
    assert row["score"] == lr.place_on_scale(1400.0, 0, 0, 2)
    assert row["reference"] == "anthropic/claude-opus-5-5"
    assert row["judge"] == "gemini/gemini-3.8-flash" and row["date"] == "2026-10-19"
    # Only cloud calls are billed: 8 reference answers and 16 verdicts.
    assert all(p != "lmstudio" for p, _ in usage) and len(usage) == 24


def test_split_verdicts_are_ties_so_position_bias_cancels():
    ask = Asker(verdicts=("A",))              # the judge always says "A"
    result = lr.measure(LOCAL, PLAN, tiny_prompts(), ask)
    assert result.kinds["code"]["ties"] == 2
    assert result.kinds["code"]["score"] == pytest.approx(1400.0)


def test_reference_answers_are_cached_between_models(tmp_path):
    store = lr.RatingStore(tmp_path / "r.json")
    lr.measure(LOCAL, PLAN, tiny_prompts(), Asker(), store=store)
    second = Asker()
    lr.measure(LocalModel("ollama", "gemma3:27b", "Gemma"), PLAN, tiny_prompts(),
               second, store=store)
    assert ("anthropic", "claude-opus-5-5") not in second.calls
    cost = lambda p, m, i, o: 1.0                                  # noqa: E731
    assert lr.estimate_cost(PLAN, tiny_prompts(), cost, store) == 16.0
    assert lr.estimate_cost(PLAN, tiny_prompts(), cost) == 24.0


def test_the_run_stops_when_it_spends_what_was_authorised():
    result = lr.measure(LOCAL, PLAN, tiny_prompts(), Asker(),
                        cost=lambda *a: 0.01, budget_eur=0.05)
    assert result.status == lr.STATUS_STOPPED and "authorised" in result.reason
    assert result.spent_eur == pytest.approx(0.06)


def test_failures_are_results_not_exceptions():
    down = lr.measure(LOCAL, PLAN, tiny_prompts(), Asker(local_fails=True))
    assert down.status == lr.STATUS_FAILED and "local model" in down.reason
    cloud = lr.measure(LOCAL, PLAN, tiny_prompts(), Asker(cloud_fails=True))
    assert cloud.status == lr.STATUS_FAILED and "cloud" in cloud.reason
    stopped = lr.measure(LOCAL, PLAN, tiny_prompts(), Asker(), cancelled=lambda: True)
    assert stopped.status == lr.STATUS_STOPPED


@pytest.mark.parametrize("reply, verdict", [
    ("A", "A"), ("b", "B"), ("TIE", "TIE"), ("Answer B.", "B"), ("", "TIE"),
    ("I cannot decide", "TIE"),
])
def test_parse_verdict(reply, verdict):
    assert lr.parse_verdict(reply) == verdict


# ── the store ───────────────────────────────────────────────────────────────

def test_store_persists_results_refusals_and_attempts(tmp_path):
    path = tmp_path / "local_ratings.json"
    store = lr.RatingStore(path)
    assert not store.attempted(LOCAL)
    result = lr.measure(LOCAL, PLAN, tiny_prompts(), Asker(), today="2026-10-19")
    store.record_result(LOCAL, result, automatic=True)
    other = LocalModel("lmstudio", "dsr1-32b", "DeepSeek 32B", quant="IQ3_M")
    store.record_refusal(other, "no openai API key is set", automatic=True)
    again = lr.RatingStore(path)
    assert again.attempted(LOCAL) and again.attempted(other)
    assert again.measured("lmstudio", LOCAL.id, "Q8_0", "writing")["n"] == 2
    assert again.entry(other)["status"] == lr.STATUS_REFUSED
    # Per model AND quant: another quant of the same model is a new model.
    assert not again.attempted(LocalModel("lmstudio", LOCAL.id, "Dolphin", quant="Q4_K_M"))
    assert json.loads(path.read_text())["models"]


# ── what the router and the pages read ──────────────────────────────────────

def _inventory(*models):
    inv = LocalInventory()
    inv.store(Snapshot(models=tuple(models)))
    return inv


def test_measured_beats_inherited_and_cloud_providers_are_not_ours(tmp_path):
    gemma = LocalModel("ollama", "gemma3:27b", "Gemma 3 27B", quant="Q4_K_M")
    store = lr.RatingStore(tmp_path / "r.json")
    ratings = lr.LocalRatings(store, _inventory(gemma), lambda: TABLE)
    inherited = ratings.rating("ollama", "gemma3:27b", "writing")
    assert inherited.source == lr.SOURCE_BASE and inherited.score == pytest.approx(1338.7)
    assert ratings.text("ollama", "gemma3:27b") == "base model 1340 − 10"
    store.record_result(gemma, lr.MeasureResult(lr.STATUS_MEASURED, kinds={
        "writing": {"score": 1290.0, "n": 20, "reference": "anthropic/x",
                    "judge": "gemini/y", "date": "2026-10-19"}}))
    measured = ratings.rating("ollama", "gemma3:27b", "writing")
    assert measured.source == lr.SOURCE_MEASURED and measured.score == 1290.0
    assert ratings.text("ollama", "gemma3:27b") == "measured writing 1290"
    assert ratings.rating("ollama", "gemma3:27b", "coding") is None   # neither covers it
    assert ratings.rating("openai", "gpt-4.1", "writing") is None


def test_unrated_progress_and_actions(tmp_path):
    store = lr.RatingStore(tmp_path / "r.json")
    ratings = lr.LocalRatings(store, _inventory(LOCAL), lambda: TABLE)
    assert ratings.text(LOCAL.provider, LOCAL.id) is None
    assert ratings.action(LOCAL) == "Measure"
    ratings.set_progress(LOCAL, 12, 80)
    assert ratings.text(LOCAL.provider, LOCAL.id) == "measuring… 12/80"
    assert ratings.action(LOCAL) == "" and ratings.note(LOCAL) == "new · automatic"
    ratings.clear_progress(LOCAL)
    store.record_refusal(LOCAL, "daily budget exceeded")
    assert ratings.action(LOCAL) == "Measure" and ratings.note(LOCAL) == "daily budget exceeded"
    store.record_result(LOCAL, lr.MeasureResult(lr.STATUS_MEASURED, kinds={
        "code": {"score": 1240.0}}))
    assert ratings.action(LOCAL) == "Re-measure"


def test_combined_lookup_sends_local_to_local_and_cloud_to_the_table(tmp_path):
    gemma = LocalModel("ollama", "gemma3:27b", "Gemma 3 27B", quant="Q8_0")
    ratings = lr.LocalRatings(lr.RatingStore(None), _inventory(gemma), lambda: TABLE)
    cloud = lambda p, m, t: SimpleNamespace(score=1500.0, label="x")  # noqa: E731
    lookup = lr.combined_lookup(cloud, ratings)
    assert lookup("ollama", "gemma3:27b", "writing").score == pytest.approx(1348.7)
    assert lookup("openai", "gpt", "writing").score == 1500.0
    assert lookup("lmstudio", "nothing-known", "writing") is None


# ── which models are measured automatically ─────────────────────────────────

def test_auto_candidates_are_new_and_runnable(tmp_path):
    store = lr.RatingStore(tmp_path / "r.json")
    ready = LocalModel("lmstudio", "a", "A", quant="Q8_0")
    big = LocalModel("lmstudio", "b", "B", quant="IQ3_M", fit="tight")
    off = LocalModel("lmstudio", "c", "C", server_up=False)
    done = LocalModel("ollama", "d", "D")
    store.record_refusal(done, "no key")
    snap = Snapshot(models=(ready, big, off, done))
    assert lr.auto_candidates(snap, store) == [ready]
    store.record_refusal(ready, "budget")
    assert lr.auto_candidates(snap, store) == []         # once, never again


def test_strip_reasoning():
    assert lr.strip_reasoning("<think>a\nb</think>\n\nanswer") == "answer"
    assert lr.strip_reasoning("<think>never closed") == ""
    assert lr.strip_reasoning("plain") == "plain"

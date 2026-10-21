"""Routing phase 3 in Sentinel: rated local models in the router, and the
automatic, guarded, background measurement of a new local model.

The rating module itself is tested in tests/test_local_ratings.py (shared with
Imprint). Here: the band rule with a rated local model, the trigger that
measures a new model once, the spend guard's refusal, and that the work never
runs on the UI thread. No test reaches a network, a cloud API or LM Studio.
"""

import os
import threading
from types import SimpleNamespace

import pytest

from services import local_ratings as lr
from services import model_recommendations as mr
from services.local_models import LocalInventory, LocalModel, Snapshot

DOLPHIN = LocalModel(provider="lmstudio", id="dolphin-3.0-llama3.1-8b",
                     name="Dolphin 3.0 8B", quant="Q8_0")
GEMMA = LocalModel(provider="lmstudio", id="gemma-3-27b-it", name="Gemma 3 27B",
                   quant="Q4_K_M")


# ── the router: rated local models enter the band at a known price of 0 ─────

@pytest.fixture
def router():
    mr.register_local_models([DOLPHIN])
    try:
        yield mr
    finally:
        mr.register_local_models([])


def _cloud(score):
    return SimpleNamespace(score=score, label="creative writing")


ROUTE = dict(enabled_providers={"anthropic", "openai", "lmstudio"},
             available_models={"anthropic": ["claude-opus-5-5"],
                               "openai": ["gpt-4.1-mini"],
                               "lmstudio": [DOLPHIN.id]})


def test_a_rated_local_model_within_the_band_wins_at_price_zero(router):
    local = lr.LocalRating(1385.0, "writing", lr.SOURCE_MEASURED)
    ratings = {("anthropic", "claude-opus-5-5"): _cloud(1400.0),
               ("openai", "gpt-4.1-mini"): _cloud(1390.0),
               ("lmstudio", DOLPHIN.id): local}
    decision = router.route_request("write a poem about rain", **ROUTE,
                                    ratings=lambda p, m, t: ratings.get((p, m)))
    assert (decision.provider, decision.model) == ("lmstudio", DOLPHIN.id)
    assert decision.basis == "rating"
    assert "(measured)" in decision.reason and "free (local)" in decision.reason


def test_a_rated_local_model_outside_the_band_does_not_win(router):
    local = lr.LocalRating(1300.0, "writing", lr.SOURCE_BASE)
    ratings = {("anthropic", "claude-opus-5-5"): _cloud(1400.0),
               ("openai", "gpt-4.1-mini"): _cloud(1390.0),
               ("lmstudio", DOLPHIN.id): local}
    decision = router.route_request("write a poem about rain", **ROUTE,
                                    ratings=lambda p, m, t: ratings.get((p, m)))
    assert decision.provider == "openai"


def test_an_unrated_local_model_still_never_auto_wins(router):
    ratings = {("anthropic", "claude-opus-5-5"): _cloud(1400.0),
               ("openai", "gpt-4.1-mini"): _cloud(1390.0)}
    decision = router.route_request("write a poem about rain", **ROUTE,
                                    ratings=lambda p, m, t: ratings.get((p, m)))
    assert decision.provider != "lmstudio" and "unrated" in decision.reason


def test_zero_still_means_unknown_for_a_cloud_model(router):
    """Kind local is free; a cloud row priced 0 is unknown and sorts last."""
    ratings = {("anthropic", "claude-opus-5-5"): _cloud(1400.0),
               ("openai", "gpt-4.1-mini"): _cloud(1400.0)}
    decision = router.route_request(
        "write a poem about rain", **ROUTE,
        prices=lambda p, m: (0.0, 0.0) if p == "openai" else (4.0, 20.0),
        ratings=lambda p, m, t: ratings.get((p, m)))
    assert decision.provider == "anthropic"
    profile = next(p for p in mr.catalog() if p.provider == "lmstudio")
    assert mr.blended_price(profile) == 0.0


def test_the_app_lookup_combines_local_and_cloud_ratings(tmp_path):
    inv = LocalInventory()
    inv.store(Snapshot(models=(GEMMA,)))
    table = SimpleNamespace(
        rating=lambda p, m, t: (SimpleNamespace(score=1348.7, label="creative writing",
                                                arena_name=m)
                                if (p, m) == ("gemini", "gemma-3-27b-it") else None),
        __bool__=lambda self: True)
    local = lr.LocalRatings(lr.RatingStore(tmp_path / "r.json"), inv, lambda: table)
    lookup = lr.combined_lookup(table.rating, local)
    assert lookup("lmstudio", "gemma-3-27b-it", "writing").score == pytest.approx(1338.7)
    assert lookup("gemini", "gemma-3-27b-it", "writing").score == pytest.approx(1348.7)


# ── the app: automatic, once, guarded, off the UI thread ───────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    from PySide6.QtWidgets import QMessageBox
    import main

    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        yield main.GodAI()
    finally:
        QMessageBox.warning, QMessageBox.question = saved


class FakeWorker:
    """Stands in for LocalMeasureWorker: records what would run, runs nothing."""
    started = []

    def __init__(self, model, plan, prompts, ask, store, cost, budget):
        self.model, self.plan, self.ask, self.budget = model, plan, ask, budget
        self.progress_signal = _Signal()
        self.usage_signal = _Signal()
        self.finished_signal = _Signal()
        self._running = False

    def start(self):
        FakeWorker.started.append(self)
        self._running = True

    def isRunning(self):
        return self._running


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


@pytest.fixture
def app(win, tmp_path, monkeypatch):
    import main

    store = lr.RatingStore(tmp_path / "local_ratings.json")
    monkeypatch.setattr(win, "local_rating_store", store)
    monkeypatch.setattr(win, "local_ratings",
                        lr.LocalRatings(store, win.local_inventory, lambda: win.ratings))
    monkeypatch.setattr(win, "local_measure_worker", None)
    monkeypatch.setattr(lr, "AUTOMATIC", True)
    monkeypatch.setattr(main, "LocalMeasureWorker", FakeWorker)
    FakeWorker.started = []
    saved = win.local_inventory.snapshot()
    win.local_inventory.store(Snapshot(models=(DOLPHIN,), lmstudio_up=True))
    plan = lr.Plan(references={k: ("anthropic", "claude-opus-5-5", 1400.0)
                               for k in lr.KINDS}, judge=("gemini", "gemini-3.8-flash"))
    monkeypatch.setattr(win, "_measurement_plan", lambda: (plan, ""))
    yield win
    win.local_inventory.store(saved)
    mr.register_local_models(saved.models)


def _allow(win, monkeypatch, allowed=True, key=True):
    monkeypatch.setattr(win, "provider_key_available", lambda p: key)
    monkeypatch.setattr(win, "current_api_permissions",
                        lambda: {f"allow_{p}": allowed for p in
                                 ("openai", "deepseek", "kimi", "gemini", "anthropic", "qwen")})
    monkeypatch.setattr(win, "session_budget_eur", 100.0)
    monkeypatch.setattr(win, "daily_budget_eur", 100.0)


def test_a_new_model_is_measured_automatically_once(app, monkeypatch):
    _allow(app, monkeypatch)
    app._on_local_discovery(app.local_inventory.snapshot())
    assert len(FakeWorker.started) == 1
    worker = FakeWorker.started[0]
    assert worker.model == DOLPHIN and worker.budget > 0
    assert app.local_ratings.text(DOLPHIN.provider, DOLPHIN.id) == "measuring… 0/80"
    worker.progress_signal.emit(12, 80)
    assert app.local_ratings.text(DOLPHIN.provider, DOLPHIN.id) == "measuring… 12/80"
    # Rediscovery while it runs starts nothing more.
    app._on_local_discovery(app.local_inventory.snapshot())
    assert len(FakeWorker.started) == 1
    worker._running = False
    worker.finished_signal.emit(lr.MeasureResult(
        lr.STATUS_MEASURED, kinds={"writing": {"score": 1281.0, "n": 20}}))
    assert app.local_ratings.text(DOLPHIN.provider, DOLPHIN.id) == "measured writing 1281"
    entry = app.local_rating_store.entry(DOLPHIN)
    assert entry["automatic"] is True and entry["status"] == lr.STATUS_MEASURED
    # Measured once: a later discovery does not measure it again.
    app._on_local_discovery(app.local_inventory.snapshot())
    app.maybe_measure_local_models()
    assert len(FakeWorker.started) == 1


def test_measurement_calls_are_billed_like_any_request(app, monkeypatch):
    logged = []
    monkeypatch.setattr(app.usage_tracker, "log_request",
                        lambda **kw: logged.append(kw) or {"estimated_cost": 0.002})
    before = app.session_cost_total
    app._on_measure_usage("gemini", "gemini-3.8-flash", "judge this", "A")
    assert logged[0]["agent"] == "local_ratings" and logged[0]["backend"] == "gemini"
    assert app.session_cost_total == pytest.approx(before + 0.002)


def test_guard_refusal_leaves_the_model_unrated_with_a_measure_button(app, monkeypatch):
    _allow(app, monkeypatch, allowed=False)        # API permissions unticked
    app._on_local_discovery(app.local_inventory.snapshot())
    assert FakeWorker.started == []
    entry = app.local_rating_store.entry(DOLPHIN)
    assert entry["status"] == lr.STATUS_REFUSED and "API permissions" in entry["reason"]
    assert app.local_ratings.text(DOLPHIN.provider, DOLPHIN.id) is None   # unrated
    assert app.local_ratings.action(DOLPHIN) == "Measure"
    # Refused counts as the one automatic attempt.
    app.maybe_measure_local_models()
    assert FakeWorker.started == []
    # Measure (a person pressing it) confirms once and runs.
    assert app.start_local_measure(DOLPHIN, automatic=False)
    assert len(FakeWorker.started) == 1


def test_budget_refusal_and_no_key(app, monkeypatch):
    _allow(app, monkeypatch)
    monkeypatch.setattr(app, "daily_budget_eur", 0.0)
    assert not app.start_local_measure(DOLPHIN, automatic=True)
    assert "budget" in app.local_rating_store.entry(DOLPHIN)["reason"].lower()
    _allow(app, monkeypatch, key=False)
    assert not app.start_local_measure(DOLPHIN, automatic=True)
    assert "API key" in app.local_rating_store.entry(DOLPHIN)["reason"]
    assert FakeWorker.started == []


def test_no_plan_means_unrated_with_the_reason(app, monkeypatch):
    monkeypatch.setattr(app, "_measurement_plan",
                        lambda: (None, "no cloud API key is set, so there is nothing "
                                       "to compare against"))
    assert not app.start_local_measure(DOLPHIN, automatic=True)
    assert "no cloud API key" in app.local_rating_store.entry(DOLPHIN)["reason"]


def test_the_real_plan_needs_a_cloud_key_and_ratings(win, monkeypatch):
    from services import benchmarks
    monkeypatch.setattr(win, "ratings", benchmarks.RatingTable())
    plan, why = win._measurement_plan()
    assert plan is None and "ratings" in why
    monkeypatch.setattr(win, "ratings", benchmarks.load(
        benchmarks.SNAPSHOT_FILE.with_name("nope.json")))
    monkeypatch.setattr(win, "_usable_providers", lambda: {"lmstudio", "ollama"})
    plan, why = win._measurement_plan()
    assert plan is None and "key" in why
    monkeypatch.setattr(win, "_usable_providers",
                        lambda: {"lmstudio", "anthropic", "gemini"})
    plan, why = win._measurement_plan()
    assert why == "" and plan.references["writing"][0] in {"anthropic", "gemini"}
    rated = [win.ratings.rating(p.provider, p.model, "writing") for p in mr.catalog()
             if p.provider in {"anthropic", "gemini"}]
    best = max(r.score for r in rated if r is not None)
    assert plan.references["writing"][2] == best
    judge = next(p for p in mr.catalog() if (p.provider, p.model) == plan.judge)
    assert mr.blended_price(judge) is not None


def test_starting_a_measurement_does_no_model_work_on_the_ui_thread(app, monkeypatch):
    _allow(app, monkeypatch)
    calls = []
    monkeypatch.setattr(app, "run_backend", lambda *a, **k: calls.append(a) or "x")
    assert app.start_local_measure(DOLPHIN, automatic=False)
    assert calls == []                  # only a worker was created and started


def test_the_measure_worker_runs_the_engine_off_the_ui_thread(qapp, tmp_path):
    from ui.workers import LocalMeasureWorker
    seen = []

    def ask(provider, model, prompt):
        seen.append(threading.current_thread())
        return "A" if provider == "gemini" else "answer"

    prompts = lr.PromptSet(1, {k: (("x", "p"),) for k in lr.KINDS})
    plan = lr.Plan(references={k: ("anthropic", "claude-opus-5-5", 1400.0)
                               for k in lr.KINDS}, judge=("gemini", "g"))
    worker = LocalMeasureWorker(DOLPHIN, plan, prompts, ask,
                                lr.RatingStore(tmp_path / "r.json"), None, None)
    results = []
    worker.finished_signal.connect(results.append)
    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()
    assert seen and all(t is not threading.main_thread() for t in seen)
    assert results and results[0].status == lr.STATUS_MEASURED


def test_settings_page_shows_progress_and_a_measure_button(qapp, tmp_path):
    from PySide6.QtWidgets import QWidget
    from ui.local_models_page import LocalModelsPage

    inv = LocalInventory()
    snap = Snapshot(models=(DOLPHIN, GEMMA), lmstudio_up=True)
    inv.store(snap)
    store = lr.RatingStore(tmp_path / "r.json")
    ratings = lr.LocalRatings(store, inv, lambda: None)
    from services import local_models
    local_models.set_rating_lookup(ratings.text)
    try:
        pressed = []
        host = QWidget()
        page = LocalModelsPage(parent=host, on_measure=pressed.append, ratings=ratings)
        ratings.set_progress(DOLPHIN, 12, 80)
        store.record_refusal(GEMMA, "no anthropic API key is set")
        page.set_snapshot(snap)
        rows = page.rows()
        assert rows[0][4] == "measuring… 12/80" and page.measure_button(0) is None
        assert rows[1][4] == "unrated" and page.measure_button(1).text() == "Measure"
        assert page.table.item(1, 4).toolTip() == "no anthropic API key is set"
        page.measure_button(1).click()
        assert pressed == [GEMMA]
    finally:
        local_models.set_rating_lookup(None)

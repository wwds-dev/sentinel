"""Routing advisor (services/routing_advisor.py) — shared with Imprint.

Byte-identical in both apps (tests/test_providers_drift.py). Pure: no Qt, no
network, no LM Studio. Covers each limit the advisor names, each of the five
modes, remembered rules, the savings tally, failover's no-cloud guarantees and
the rule that nothing switches to a paid model by itself.
"""

from datetime import date, datetime

import pytest

from services import routing_advisor as ra
from services.routing_advisor import Candidate, Job

LOCAL = Candidate("lmstudio", "deepseek-r1-distill-qwen-14b", True, rating=1281,
                  rating_source="measured", cost_eur=0.0, context_window=32_000,
                  size_gb=12.0, tag="abliterated")
SMALL = Candidate("lmstudio", "dolphin3.0-llama3.1-8b", True, rating=1270,
                  rating_source="measured", cost_eur=0.0, context_window=32_000,
                  size_gb=9.0, tag="uncensored")
SONNET = Candidate("anthropic", "claude-sonnet-5-5", False, rating=1300,
                   cost_eur=0.19, context_window=1_000_000,
                   capabilities=frozenset({"text", "vision", "tool_use"}))
FLASH = Candidate("gemini", "gemini-3.8-flash", False, rating=1290, cost_eur=0.03,
                  context_window=1_000_000,
                  capabilities=frozenset({"text", "vision", "tool_use"}))
WRITING = Job("chat", "writing", context_tokens=2_000)


def advise(selected=LOCAL, job=WRITING, **kw):
    kw.setdefault("local", (LOCAL, SMALL))
    kw.setdefault("cloud", (SONNET, FLASH))
    kw.setdefault("recommended", FLASH)
    return ra.advise(job, selected, **kw)


# ── Each reason, named ───────────────────────────────────────────────────────
def test_good_enough_local_runs_without_a_word():
    advice = advise()
    assert advice.action == ra.RUN and advice.run.same(LOCAL)
    assert advice.reasons == () and advice.suggestion is None


def test_quality_gap_outside_the_band_suggests_and_urges():
    weak = Candidate(**{**LOCAL.__dict__, "rating": 1204})
    advice = advise(weak)
    assert advice.action == ra.SUGGEST and advice.codes == (ra.QUALITY_GAP,)
    assert advice.urge and advice.suggestion.same(FLASH)
    assert "96 below claude-sonnet-5-5" in advice.reasons[0].text
    assert advice.run.same(weak)          # Keep local stays possible


def test_twenty_points_is_still_inside_the_band():
    edge = Candidate(**{**LOCAL.__dict__, "rating": SONNET.rating - 20})
    assert advise(edge).action == ra.RUN


def test_context_too_long_names_both_sizes_and_cannot_be_kept():
    job = Job("chat", "coding", context_tokens=58_000, summary="Code review across 9 files")
    advice = advise(job=job, recommended=SONNET)
    assert advice.codes == (ra.CONTEXT,)
    assert "58k tokens" in advice.reasons[0].text and "32k" in advice.reasons[0].text
    assert advice.run is None and advice.suggestion.same(SONNET)
    assert "Code review across 9 files." in ra.detail(advice, job)


def test_missing_capability_is_named():
    job = Job("chat", "vision", required=frozenset({"text", "vision"}))
    advice = advise(job=job)
    assert ra.CAPABILITY in advice.codes and "vision" in advice.reasons[0].text
    assert advice.urge and advice.run is None


def test_not_runnable_is_named_with_its_cause():
    down = Candidate(**{**LOCAL.__dict__, "runnable": False,
                        "not_runnable": "The LM Studio server is off."})
    advice = advise(down)
    assert advice.codes[0] == ra.NOT_RUNNABLE
    assert "server is off" in advice.reasons[0].text and advice.run is None


def test_unmeasured_suggests_but_does_not_urge():
    unrated = Candidate(**{**LOCAL.__dict__, "rating": None})
    advice = advise(unrated)
    assert advice.action == ra.SUGGEST and advice.codes == (ra.UNMEASURED,)
    assert not advice.urge and advice.run.same(unrated)
    assert "hasn't been measured" in ra.headline(advice, unrated)


def test_several_reasons_are_all_named():
    bad = Candidate(**{**LOCAL.__dict__, "rating": 1200, "runnable": False,
                       "not_runnable": "Too big for free RAM."})
    job = Job("chat", "writing", context_tokens=50_000)
    codes = advise(bad, job=job).codes
    assert set(codes) == {ra.NOT_RUNNABLE, ra.CONTEXT, ra.QUALITY_GAP}


def test_suggestion_carries_cost_and_time():
    weak = Candidate(**{**LOCAL.__dict__, "rating": 1200})
    advice = advise(weak)
    text = ra.detail(advice, WRITING)
    assert "about €0.03" in text and "about" in text and " s" in text
    assert advice.seconds and advice.seconds > 0


def test_suggestion_never_exceeds_the_budget():
    weak = Candidate(**{**LOCAL.__dict__, "rating": 1200})
    advice = advise(weak, local=(weak,), recommended=SONNET, budget_left_eur=0.05)
    assert advice.suggestion.same(FLASH)
    assert advise(weak, local=(weak,), recommended=SONNET,
                  budget_left_eur=0.0).suggestion is None


# ── Modes ────────────────────────────────────────────────────────────────────
def test_default_mode_is_local_advise():
    assert ra.DEFAULT_MODE == ra.LOCAL_ADVISE
    assert ra.mode_for({}) == ra.LOCAL_ADVISE
    assert ra.mode_for({"routing_mode": "nonsense"}) == ra.LOCAL_ADVISE


def test_per_agent_override_wins_over_the_app_setting():
    settings = {}
    ra.set_mode(settings, ra.LOCAL_FIRST)
    ra.set_mode(settings, ra.CLOUD_ONLY, agent="osint")
    assert ra.mode_for(settings, "chat") == ra.LOCAL_FIRST
    assert ra.mode_for(settings, "osint") == ra.CLOUD_ONLY
    ra.set_mode(settings, "", agent="osint")
    assert ra.mode_for(settings, "osint") == ra.LOCAL_FIRST


def test_effective_mode_honours_the_execution_box_and_overrides():
    settings = {"routing_mode": ra.SUGGEST_LOCAL}
    assert ra.effective_mode(settings, "chat", "Hybrid allowed") == ra.SUGGEST_LOCAL
    assert ra.effective_mode(settings, "chat", "Local only") == ra.LOCAL_ONLY
    assert ra.effective_mode(settings, "chat", "Cloud only") == ra.CLOUD_ONLY
    assert ra.effective_mode({"routing_mode": ra.LOCAL_ONLY}, "chat",
                             "Hybrid allowed") == ra.DEFAULT_MODE
    ra.set_mode(settings, ra.LOCAL_FIRST, agent="chat")
    assert ra.effective_mode(settings, "chat", "Cloud only") == ra.LOCAL_FIRST
    assert ra.effective_mode({}, None, None) == ra.DEFAULT_MODE


def test_labels_round_trip():
    for mode, label in ra.MODE_LABELS.items():
        assert ra.normalise_mode(label) == mode
    assert list(ra.MODE_LABELS) == list(ra.MODES)


def test_suggest_local_offers_a_good_enough_local_model_with_the_saving():
    advice = advise(SONNET, mode=ra.SUGGEST_LOCAL, recommended=SONNET)
    assert advice.action == ra.SUGGEST and advice.to_local
    assert advice.run.same(SONNET)        # Keep claude-sonnet-5-5
    assert advice.saving_eur == pytest.approx(0.19)
    assert "saves about €0.19" in ra.detail(advice, WRITING)


def test_suggest_local_stays_quiet_when_no_local_model_is_good_enough():
    weak = Candidate(**{**LOCAL.__dict__, "rating": 1200})
    advice = advise(SONNET, mode=ra.SUGGEST_LOCAL, local=(weak,))
    assert advice.action == ra.RUN and advice.suggestion is None


def test_local_first_runs_a_good_enough_local_model_at_zero():
    advice = advise(SONNET, mode=ra.LOCAL_FIRST)
    assert advice.action == ra.RUN and advice.run.local
    assert advice.run.cost_eur == 0


def test_local_first_keeps_cloud_when_no_local_model_fits():
    job = Job("chat", "coding", context_tokens=58_000)
    advice = advise(SONNET, job=job, mode=ra.LOCAL_FIRST)
    assert advice.action == ra.RUN and advice.run.same(SONNET)


def test_cloud_only_runs_the_selection_and_refuses_a_local_one():
    assert advise(SONNET, mode=ra.CLOUD_ONLY).run.same(SONNET)
    assert advise(LOCAL, mode=ra.CLOUD_ONLY).action == ra.REFUSE


def test_local_only_never_suggests_cloud():
    job = Job("chat", "coding", context_tokens=58_000)
    big = Candidate("lmstudio", "qwen-long", True, rating=1250, cost_eur=0.0,
                    context_window=128_000, size_gb=10.0)
    advice = advise(job=job, mode=ra.LOCAL_ONLY, local=(LOCAL, big), recommended=SONNET)
    assert advice.action == ra.SUGGEST and advice.suggestion.local
    assert advice.suggestion.same(big)


def test_local_only_refuses_and_says_why_when_no_local_model_fits():
    job = Job("chat", "coding", context_tokens=58_000)
    advice = advise(job=job, mode=ra.LOCAL_ONLY, recommended=SONNET)
    assert advice.action == ra.REFUSE and advice.suggestion is None
    assert "no local model" in advice.message and "58k" in advice.message


def test_local_only_refuses_a_cloud_selection():
    advice = advise(SONNET, mode=ra.LOCAL_ONLY)
    assert advice.action == ra.REFUSE and "cloud" in advice.message


def test_privacy_removes_every_cloud_suggestion():
    weak = Candidate(**{**LOCAL.__dict__, "rating": 1200})
    advice = advise(weak, privacy=True, recommended=SONNET)
    assert advice.suggestion is None or advice.suggestion.local


def test_legacy_execution_mode_for_each_mode():
    assert ra.LEGACY_EXECUTION[ra.LOCAL_ONLY] == "Local only"
    assert ra.LEGACY_EXECUTION[ra.CLOUD_ONLY] == "Cloud only"
    assert set(ra.LEGACY_EXECUTION) == set(ra.MODES)


# ── Never switches to a paid model on its own ───────────────────────────────
@pytest.mark.parametrize("mode", ra.MODES)
@pytest.mark.parametrize("selected", [LOCAL, SMALL, SONNET, FLASH,
                                      Candidate(**{**LOCAL.__dict__, "rating": None}),
                                      Candidate(**{**LOCAL.__dict__, "rating": 1100})])
@pytest.mark.parametrize("context", [1_000, 58_000])
def test_never_runs_a_different_paid_model_without_the_user(mode, selected, context):
    job = Job("chat", "writing", context_tokens=context)
    advice = advise(selected, job=job, mode=mode, recommended=SONNET)
    if advice.action == ra.RUN and not advice.run.local:
        assert advice.run.same(selected)


# ── Remembered rules ─────────────────────────────────────────────────────────
def test_always_rule_is_followed_without_asking(tmp_path):
    rules = ra.RoutingRules(tmp_path / "rules.json")
    rules.add("chat", "coding", "anthropic", "claude-sonnet-5-5", "code review")
    weak = Candidate(**{**LOCAL.__dict__, "rating": 1200})
    advice = advise(weak, job=Job("chat", "coding"), rules=rules)
    assert advice.action == ra.RUN and advice.run.same(SONNET)
    assert advice.rule is not None and "Settings → Models" in advice.message


def test_rule_is_per_agent_and_kind(tmp_path):
    rules = ra.RoutingRules(tmp_path / "rules.json")
    rules.add("chat", "coding", "anthropic", "claude-sonnet-5-5")
    assert advise(job=Job("chat", "writing"), rules=rules).rule is None
    assert advise(job=Job("osint", "coding"), rules=rules).rule is None


def test_rule_never_overrides_local_only_or_privacy(tmp_path):
    rules = ra.RoutingRules(tmp_path / "rules.json")
    rules.add("chat", "writing", "anthropic", "claude-sonnet-5-5")
    for kw in ({"mode": ra.LOCAL_ONLY}, {"privacy": True}):
        advice = advise(rules=rules, **kw)
        assert advice.rule is None and advice.run.local


def test_rules_persist_list_and_remove(tmp_path):
    path = tmp_path / "rules.json"
    rules = ra.RoutingRules(path)
    rules.add("chat", "coding", "anthropic", "claude-sonnet-5-5",
              now=datetime(2026, 10, 19, 12, 0))
    rules.add("chat", "writing", "gemini", "gemini-3.8-flash")
    again = ra.RoutingRules(path)
    assert [(r.agent, r.kind) for r in again.all()] == [("chat", "coding"), ("chat", "writing")]
    assert again.get("chat", "coding").created == "2026-10-19T12:00:00"
    assert again.remove("chat", "coding") and not again.remove("chat", "coding")
    assert ra.RoutingRules(path).get("chat", "coding") is None


def test_corrupt_rules_file_reads_as_empty(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{not json")
    assert ra.RoutingRules(path).all() == []
    path.write_text('[{"agent": "chat"}, 3]')
    assert ra.RoutingRules(path).all() == []


# ── Status line and tally ───────────────────────────────────────────────────
def test_status_line_matches_mockup():
    line = ra.status_line(LOCAL, WRITING, Candidate(**{**SONNET.__dict__, "rating": 1318}),
                          0.03, 0.41)
    assert line == ("✓ Ran on deepseek-r1-distill-qwen-14b (local) · writing 1281, "
                    "within 37 of best · saved ≈ €0.03 · today €0.41 saved")


def test_tally_saves_and_spends_by_day(tmp_path):
    path = tmp_path / "tally.json"
    tally = ra.SavingsTally(path)
    day = date(2026, 10, 19)
    tally.record_local_run(0.03, day)
    tally.record_local_run(None, day)
    tally.record_accepted(0.19, day)
    tally.record_local_run(0.10, date(2026, 10, 18))
    again = ra.SavingsTally(path)
    assert again.today(day) == {"saved": pytest.approx(0.03), "spent": pytest.approx(0.19),
                                "local_runs": 2, "accepted": 1}
    assert again.totals()["saved"] == pytest.approx(0.13)
    assert "saved €0.03 today" in again.summary(day)
    assert "spent €0.19 today" in again.summary(day)


# ── Failover ─────────────────────────────────────────────────────────────────
def test_failover_offers_next_cloud_and_a_smaller_local():
    offer = ra.failover(LOCAL, (LOCAL, FLASH, SONNET, SMALL), mode=ra.LOCAL_FIRST,
                        error="out of memory")
    assert offer.cloud.same(FLASH) and offer.local.same(SMALL)
    assert "out of memory" in ra.failover_text(offer)
    assert "gemini-3.8-flash, about €0.03" in ra.failover_text(offer)


@pytest.mark.parametrize("kw", [{"mode": ra.LOCAL_ONLY},
                                {"mode": ra.LOCAL_FIRST, "privacy": True},
                                {"mode": ra.LOCAL_ADVISE, "privacy": True}])
def test_local_only_and_privacy_never_fall_back_to_cloud(kw):
    offer = ra.failover(LOCAL, (FLASH, SONNET, SMALL), **kw)
    assert offer.cloud is None and offer.local.same(SMALL)
    offer = ra.failover(LOCAL, (FLASH, SONNET), **kw)
    assert offer.empty


def test_cloud_only_never_falls_back_to_local():
    offer = ra.failover(SONNET, (LOCAL, FLASH), mode=ra.CLOUD_ONLY)
    assert offer.local is None and offer.cloud.same(FLASH)


def test_failover_skips_the_failed_model_and_unusable_ones():
    job = Job("chat", "coding", context_tokens=58_000)
    offer = ra.failover(LOCAL, (LOCAL, SMALL, FLASH), mode=ra.LOCAL_FIRST, job=job)
    assert offer.local is None and offer.cloud.same(FLASH)


def test_failover_respects_the_budget():
    offer = ra.failover(LOCAL, (SONNET, FLASH), mode=ra.LOCAL_FIRST, budget_left_eur=0.05)
    assert offer.cloud.same(FLASH)


def test_saved_by_local_is_the_cheapest_good_enough_cloud_estimate():
    assert ra.saved_by_local(WRITING, (SONNET, FLASH)) == pytest.approx(0.03)
    assert ra.saved_by_local(WRITING, ()) is None
    unpriced = Candidate("kimi", "k3", False, rating=1400, cost_eur=None)
    assert ra.saved_by_local(WRITING, (unpriced,)) is None


def test_kind_labels_read_as_a_sentence():
    assert Job("chat", "coding").label == "code"
    assert ra.button_labels(advise(Candidate(**{**LOCAL.__dict__, "rating": None})),
                            Job("chat", "coding"), LOCAL)["always"] == "Always for code jobs"

"""The routing advisor on the specialist panels (every agent but Chat).

Chat is covered by test_routing_phase4.py. Here a real Sentry panel stands in
for all of them, because they share one seam: AgentPanel.authorize holds the
job on a banner at the top of the panel, the answer is stored per agent and
kind in routing_rules.json, a failed run offers the next candidate, and a
finished one is tallied. A source test pins that every panel goes through that
seam with a resume callable, and the registry's allowed_providers still bound
what is suggested. No test reaches a network or a provider.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from services import model_recommendations as mr
from services import routing_advisor as ra

from tests.test_routing_phase4 import (LOCAL_ID, SONNET, _forbidden_fetch,  # noqa: F401
                                       _ratings, qapp, win)

AGENT = "sentry"
PROMPT = "Write a short note to my landlord about the heating"


@pytest.fixture
def panel(win):
    panel = win.panels[AGENT]
    panel.provider_box.setCurrentText("lmstudio")
    panel.model_box.clear()
    panel.model_box.addItems([LOCAL_ID])
    panel.model_box.setCurrentText(LOCAL_ID)
    panel.started = []
    panel.start_worker = lambda messages, prompt, **kw: panel.started.append(
        (panel.provider, panel.model))
    return panel


def _run(win, panel):
    real, win.models_for_provider = win.models_for_provider, _forbidden_fetch
    try:
        panel._start_ai_pass(PROMPT)
    finally:
        win.models_for_provider = real


def test_a_good_enough_local_model_runs_without_a_banner(win, panel):
    _ratings(1290)
    _run(win, panel)
    assert panel._advice_banner.isHidden()
    assert panel.started == [("lmstudio", LOCAL_ID)]


def test_a_quality_gap_holds_the_panel_job_on_its_own_banner(win, panel):
    _ratings(1200)
    _run(win, panel)
    banner = panel._advice_banner
    assert panel.started == [] and not banner.isHidden()
    assert banner is not win.advice_banner            # Chat's banner is untouched
    assert win.advice_banner.isHidden()
    assert banner.advice.codes == (ra.QUALITY_GAP,) and banner.advice.suggestion.key == SONNET
    assert panel.layout().indexOf(banner) == 0


def test_switch_for_this_job_restarts_the_panel_on_the_suggestion(win, panel):
    _ratings(1200)
    _run(win, panel)
    panel._advice_banner.switch_button.click()
    assert panel.started == [SONNET]
    assert len(win.questions) == 1 and "external API" in win.questions[0]
    assert win.routing_rules.all() == []
    assert panel._advice_cleared is False


def test_keep_local_runs_the_selected_local_model(win, panel):
    _ratings(1200)
    _run(win, panel)
    panel._advice_banner.keep_button.click()
    assert panel.started == [("lmstudio", LOCAL_ID)] and win.questions == []


def test_always_stores_a_rule_for_this_agent_and_kind_only(win, panel):
    _ratings(1200)
    _run(win, panel)
    panel._advice_banner.always_button.click()
    kinds = [r for r in win.routing_rules.all() if r.agent == AGENT]
    assert len(kinds) == 1 and (kinds[0].provider, kinds[0].model) == SONNET
    assert all(r.agent == AGENT for r in win.routing_rules.all())
    assert win.routing_rules.get("chat", kinds[0].kind) is None
    # The next job of that kind follows the rule without a banner, and still asks.
    panel.started.clear()
    panel.provider_box.setCurrentText("lmstudio")
    panel.model_box.setCurrentText(LOCAL_ID)
    _run(win, panel)
    assert panel._advice_banner.isHidden() and panel.started == [SONNET]
    assert len(win.questions) == 2


def test_cancel_drops_the_job(win, panel):
    _ratings(1200)
    _run(win, panel)
    panel._advice_banner.dismiss_button.click()
    assert panel.started == [] and AGENT not in win.panel_routing.held


def test_local_only_never_offers_the_cloud_on_a_panel(win, panel):
    win.settings[ra.OVERRIDES_KEY] = {AGENT: ra.LOCAL_ONLY}
    _ratings(1200)
    _run(win, panel)
    advice = panel._advice_banner.advice
    assert advice is None or advice.suggestion is None or advice.suggestion.local
    assert panel.started in ([], [("lmstudio", LOCAL_ID)]) and win.questions == []


def test_registry_allowed_providers_still_bound_the_suggestion(win, panel, monkeypatch):
    monkeypatch.setattr(win.registry, "permitted_providers",
                        lambda agent, tool=None: {"lmstudio"})
    _ratings(1200)
    _run(win, panel)
    advice = panel._advice_banner.advice
    assert advice is None or advice.suggestion is None or advice.suggestion.local


def test_a_failed_panel_run_offers_the_next_candidate_and_sends_nothing(win, panel):
    _ratings(1290)
    _run(win, panel)
    assert panel._request_id is not None
    panel.abandon("error", error="model crashed")
    banner = panel._advice_banner
    assert not banner.isHidden() and banner.offer is not None
    assert "could not finish" in banner.title.text()
    assert panel.started == [("lmstudio", LOCAL_ID)]          # nothing new was sent
    banner.switch_button.click()                              # Send to Anthropic
    assert panel.started[-1] == SONNET and len(win.questions) == 1


def test_a_stopped_panel_run_offers_no_failover(win, panel):
    _ratings(1290)
    _run(win, panel)
    panel.abandon("cancelled")
    assert panel._advice_banner.isHidden()


def test_a_finished_local_panel_run_is_tallied(win, panel):
    _ratings(1290)
    _run(win, panel)
    panel.record("done")
    today = win.savings_tally.today()
    assert today["local_runs"] == 1 and today["saved"] > 0


def test_an_accepted_paid_suggestion_is_tallied_as_spent(win, panel):
    _ratings(1200)
    _run(win, panel)
    panel._advice_banner.switch_button.click()
    panel.record("done")
    assert win.savings_tally.today()["accepted"] == 1


def test_every_panel_goes_through_the_advised_seam_with_a_resume():
    root = Path(__file__).resolve().parents[1] / "ui" / "panels"
    seen = {}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "authorize"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
                seen[path.stem] = any(k.arg == "resume" for k in node.keywords)
    assert set(seen) == {"manager", "vpn", "sentry", "bug_bounty", "osint",
                         "osint_heavy", "wifi"}
    assert all(seen.values()), [k for k, v in seen.items() if not v]


def test_trace_needs_tool_use_so_a_local_model_without_it_is_held(win):
    """Trace's router profile requires tool use: that is a named, hard reason."""
    panel = win.panels["osint"]
    panel.provider_box.setCurrentText("lmstudio")
    panel.model_box.clear()
    panel.model_box.addItems([LOCAL_ID])
    panel.target_input.setText("example.com")
    panel.start_worker = lambda *a, **k: None
    _ratings(1290)
    panel.analyse()
    advice = panel._advice_banner.advice
    assert advice is not None and ra.CAPABILITY in advice.codes


# ── H3: Local only / Privacy first never fail open (audit 2026-11-23, G2) ────
def _advisor_raises(win, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("advisor broke")
    monkeypatch.setattr(win, "_advisor_context", boom)


def _pick_cloud(panel):
    panel.provider_box.setCurrentText(SONNET[0])
    panel.model_box.clear()
    panel.model_box.addItems([SONNET[1]])
    panel.model_box.setCurrentText(SONNET[1])
    assert (panel.provider, panel.model) == SONNET


@pytest.mark.parametrize("ticked", [False, True])
def test_local_only_with_a_broken_advisor_sends_nothing_to_the_cloud(
        win, panel, monkeypatch, ticked):
    """The advisor raising used to mean "carry on": an unticked keyed cloud
    provider then met one consent dialog, and a Yes sent it despite Local only."""
    win.settings[ra.OVERRIDES_KEY] = {AGENT: ra.LOCAL_ONLY}
    win.allow_anthropic_checkbox.setChecked(ticked)
    _advisor_raises(win, monkeypatch)
    _pick_cloud(panel)
    _run(win, panel)
    assert panel.started == [] and win.questions == []
    assert panel._request_id is None
    assert "Local only" in panel.status_label.text()


def test_a_broken_advisor_still_blocks_in_advise_itself(win, panel, monkeypatch):
    """advise() is the panel's own guard, not only authorize_request()."""
    win.settings[ra.OVERRIDES_KEY] = {AGENT: ra.LOCAL_ONLY}
    _advisor_raises(win, monkeypatch)
    _pick_cloud(panel)
    assert win.panel_routing.advise(panel, PROMPT) is False
    assert "Local only" in panel.status_label.text()


def test_a_broken_advisor_lets_a_local_job_run_under_local_only(win, panel, monkeypatch):
    win.settings[ra.OVERRIDES_KEY] = {AGENT: ra.LOCAL_ONLY}
    _advisor_raises(win, monkeypatch)
    assert win.panel_routing.advise(panel, PROMPT) is True
    _run(win, panel)
    assert panel.started == [("lmstudio", LOCAL_ID)] and win.questions == []


def test_a_broken_advisor_in_a_cloud_mode_still_reaches_the_consent_dialog(
        win, panel, monkeypatch):
    """Outside Local only the old behaviour stays: advice never blocks a run,
    and the paid run still asks first."""
    _advisor_raises(win, monkeypatch)
    _pick_cloud(panel)
    assert win.panel_routing.advise(panel, PROMPT) is True
    _run(win, panel)
    assert panel.started == [SONNET] and len(win.questions) == 1


@pytest.mark.parametrize("ticked", [False, True])
def test_authorize_request_refuses_a_cloud_provider_under_local_only(win, ticked):
    win.settings[ra.OVERRIDES_KEY] = {AGENT: ra.LOCAL_ONLY}
    win.allow_anthropic_checkbox.setChecked(ticked)
    assert win.authorize_request(AGENT, *SONNET, PROMPT) is False
    assert win.questions == [] and not win._pending_requests
    # A local provider in the same mode is not refused by the mode check.
    assert win.authorize_request(AGENT, "lmstudio", LOCAL_ID, PROMPT) is True


def test_authorize_request_refuses_a_cloud_provider_under_privacy_first(win, panel):
    win.settings["routing_priority"] = "privacy"
    assert win.authorize_request(AGENT, *SONNET, PROMPT) is False
    assert win.questions == []
    assert "Privacy first" in panel.status_label.text()


def test_the_run_bar_local_only_also_refuses_a_cloud_provider(win):
    win.execution_mode_box.setCurrentText("Local only")
    assert win.authorize_request(AGENT, *SONNET, PROMPT) is False
    assert win.questions == []


def test_an_unreadable_mode_fails_closed(win, monkeypatch):
    def boom(agent="chat"):
        raise ValueError("settings corrupt")
    monkeypatch.setattr(win, "routing_mode", boom)
    assert win.cloud_allowed(AGENT) is False
    assert win.authorize_request(AGENT, *SONNET, PROMPT) is False

"""Routing phase 4 in Sentinel: the advisor on a real Chat send.

The advisor itself is tested in tests/test_routing_advisor.py (shared with
Imprint). Here, on a real offscreen window with a fake worker: the banner
holds a job and starts nothing; each answer (Switch, Always, Keep local) runs
the right model and a paid one still meets the paid-request prompt; a rule is
followed next time and can be removed; a local run is tallied and gets its
status line; a failed run offers the next candidate and never sends it; Local
only and Privacy first never offer the cloud. No test reaches a network, a
cloud API or LM Studio, and the advisor never asks a provider for its models.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from services import model_recommendations as mr
from services import routing_advisor as ra
from services.local_models import LocalModel, Snapshot

from tests.test_chat_lifecycle import FakeChatWorker

LOCAL_ID = "deepseek-r1-distill-qwen-14b"
SMALL_ID = "dolphin3.0-llama3.1-8b"
LOCAL = LocalModel(provider="lmstudio", id=LOCAL_ID, name="DeepSeek R1 Distill 14B",
                   quant="Q6_K", size_bytes=12_000_000_000, max_context=32_000)
SMALL = LocalModel(provider="lmstudio", id=SMALL_ID, name="Dolphin 3.0 8B",
                   quant="Q8_0", size_bytes=9_000_000_000, max_context=32_000)
SONNET = ("anthropic", "claude-sonnet-5-5")


def _rating(score, source=None):
    return SimpleNamespace(score=score, label="writing", source=source)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    import main

    FakeChatWorker.instances.clear()
    questions: list = []
    monkeypatch.setattr(main, "ChatWorker", FakeChatWorker)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    def question(*a, **k):
        questions.append(a[2] if len(a) > 2 else "")
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(main, "SETTINGS_OVERRIDE_FILE", tmp_path / "override.json")

    # A database of its own: the suite's session database carries spend from
    # every other test, and that spend decides whether a paid suggestion fits
    # today's budget.
    from services import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "sentinel.db")
    database.init_db()
    window = main.GodAI()
    window.questions = questions
    window.assess_local_model = lambda model, provider="ollama": None
    window.provider_key_available = lambda provider: True
    window.routing_rules = ra.RoutingRules(tmp_path / "rules.json")
    window.savings_tally = ra.SavingsTally(tmp_path / "savings.json")
    window.local_inventory.store(Snapshot(models=(LOCAL, SMALL), lmstudio_up=True,
                                          ram_free_bytes=20e9, ram_total_bytes=24e9,
                                          taken_at=1.0))
    mr.register_local_models([LOCAL, SMALL])
    window.agent_box.setCurrentText("chat")
    window.settings.pop(ra.OVERRIDES_KEY, None)
    window.settings[ra.MODE_KEY] = ra.LOCAL_ADVISE
    window.settings["routing_priority"] = "balanced"
    window._sync_execution_mode()
    for name in ("openai", "deepseek", "kimi", "gemini", "qwen"):
        getattr(window, f"allow_{name}_checkbox").setChecked(False)
    window.allow_anthropic_checkbox.setChecked(True)
    window._listed_models = {"anthropic": [SONNET[1]]}
    window.selected = ("lmstudio", LOCAL_ID)
    window.resolve_backend_model = lambda: window.selected
    window.new_chat()
    yield window
    window.chat_worker = None
    mr.register_local_models([])
    window._install_rating_lookup()


def _forbidden_fetch(*_a, **_k):
    raise AssertionError("the advisor asked a provider for its models")


def _ratings(local_score, cloud_score=1300, small_score=1205):
    table = {SONNET: _rating(cloud_score),
             ("lmstudio", LOCAL_ID): None if local_score is None
             else _rating(local_score, "measured"),
             ("lmstudio", SMALL_ID): None if small_score is None
             else _rating(small_score, "measured")}
    mr.set_rating_lookup(lambda p, m, t: table.get((p, m)))


def _send(win, text="Write a short note to my landlord about the heating"):
    win.input_box.setPlainText(text)
    # The advisor must never ask a provider for its models (UI thread).
    real, win.models_for_provider = win.models_for_provider, _forbidden_fetch
    try:
        win.send_prompt()
    finally:
        win.models_for_provider = real


def _worker():
    return FakeChatWorker.instances[-1] if FakeChatWorker.instances else None


# ── The banner holds the job ─────────────────────────────────────────────────
def test_a_good_enough_local_model_runs_without_a_banner(win):
    _ratings(1290)
    _send(win)
    assert win.advice_banner.isHidden()
    assert (_worker().backend, _worker().model) == ("lmstudio", LOCAL_ID)
    assert win.questions == []                     # nothing paid, nothing asked


def test_a_quality_gap_holds_the_job_and_starts_nothing(win):
    _ratings(1200)
    _send(win)
    assert not win.advice_banner.isHidden() and _worker() is None
    advice = win.advice_banner.advice
    assert advice.codes == (ra.QUALITY_GAP,) and advice.suggestion.key == SONNET
    assert "needs more than" in win.advice_banner.title.text()
    assert "Recommended: claude-sonnet-5-5" in win.advice_banner.detail.text()


def test_switch_for_this_job_sends_the_suggestion_through_the_paid_prompt(win):
    _ratings(1200)
    _send(win)
    win.advice_banner.switch_button.click()
    assert (_worker().backend, _worker().model) == SONNET
    assert len(win.questions) == 1 and "external API" in win.questions[0]
    assert win.routing_rules.all() == []            # one job only


def test_declining_the_paid_prompt_sends_nothing(win, monkeypatch):
    _ratings(1200)
    _send(win)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    win.advice_banner.switch_button.click()
    assert _worker() is None


def test_keep_local_runs_the_local_model(win):
    _ratings(1200)
    _send(win)
    win.advice_banner.keep_button.click()
    assert (_worker().backend, _worker().model) == ("lmstudio", LOCAL_ID)
    assert win.questions == []


def test_an_unmeasured_local_model_is_suggested_against_not_urged(win):
    _ratings(None)
    _send(win)
    advice = win.advice_banner.advice
    assert advice.codes == (ra.UNMEASURED,) and not advice.urge
    assert not win.advice_banner.keep_button.isHidden()


def test_server_off_is_named_and_local_cannot_be_kept(win):
    _ratings(1290)
    win.local_inventory.store(Snapshot(models=(LOCAL, SMALL), lmstudio_up=False,
                                       ram_free_bytes=20e9, taken_at=2.0))
    _send(win)
    advice = win.advice_banner.advice
    assert ra.NOT_RUNNABLE in advice.codes
    assert "server is off" in win.advice_banner.detail.text()
    assert win.advice_banner.keep_button.isHidden()


def test_editing_the_prompt_while_held_advises_afresh(win):
    _ratings(1200)
    _send(win)
    win.input_box.setPlainText("Something else entirely")
    _ratings(1290)
    win.advice_banner.switch_button.click()
    assert (_worker().backend, _worker().model) == ("lmstudio", LOCAL_ID)


# ── Remembered rules ─────────────────────────────────────────────────────────
def test_always_saves_a_rule_that_the_next_job_follows(win):
    _ratings(1200)
    _send(win)
    win.advice_banner.always_button.click()
    rule = win.routing_rules.get("chat", "writing")
    assert rule is not None and (rule.provider, rule.model) == SONNET
    _worker().finish("ok")
    FakeChatWorker.instances.clear()
    _send(win, "Write a thank-you letter to my neighbour")
    assert win.advice_banner.isHidden()
    assert (_worker().backend, _worker().model) == SONNET
    assert len(win.questions) == 2                  # still asked each time


def test_settings_lists_and_removes_rules(win):
    from ui.routing_page import RoutingModesPage

    win.routing_rules.add("chat", "coding", *SONNET, "code")
    page = RoutingModesPage(win.settings, {"chat": "Chat"}, win.routing_rules,
                            win.savings_tally, on_change=win.apply_routing_settings)
    assert page.rules_table.rowCount() == 1
    assert page.rules_table.item(0, 2).text() == "anthropic · claude-sonnet-5-5"
    page.rules_table.cellWidget(0, 3).click()
    assert win.routing_rules.all() == [] and page.rules_table.rowCount() == 0


def test_settings_page_sets_mode_and_override_and_syncs_execution(win):
    from ui.routing_page import RoutingModesPage

    page = RoutingModesPage(win.settings, {"chat": "Chat", "osint": "Trace"},
                            win.routing_rules, on_change=win.apply_routing_settings)
    assert page.mode_buttons[ra.LOCAL_ADVISE].isChecked()
    page.mode_buttons[ra.LOCAL_ONLY].click()
    assert win.routing_mode() == ra.LOCAL_ONLY
    assert win.execution_mode_box.currentText() == "Local only"
    box = page.agent_boxes["osint"]
    box.setCurrentIndex(box.findData(ra.CLOUD_ONLY))
    assert win.routing_mode("osint") == ra.CLOUD_ONLY
    assert win._execution_mode("osint") == "Cloud only"
    assert win._execution_mode("chat") == "Local only"


def test_the_settings_dialog_has_a_models_tab(win, monkeypatch):
    from PySide6.QtWidgets import QDialog, QTabWidget
    from ui import dialogs

    seen = {}
    monkeypatch.setattr(QDialog, "exec", lambda self: seen.setdefault(
        "tabs", [self.findChild(QTabWidget).tabText(i)
                 for i in range(self.findChild(QTabWidget).count())]))
    dialogs.show_settings(win)
    assert "Models" in seen["tabs"]


def test_the_run_bar_execution_mode_moves_the_routing_mode(win):
    win.execution_mode_box.setCurrentText("Cloud only")
    assert win.routing_mode() == ra.CLOUD_ONLY
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    assert win.routing_mode() == ra.DEFAULT_MODE


# ── Tally and status line ────────────────────────────────────────────────────
def test_a_local_run_is_tallied_with_its_status_line(win):
    _ratings(1290)
    _send(win)
    _worker().finish("done")
    today = win.savings_tally.today()
    assert today["local_runs"] == 1 and today["saved"] > 0
    assert win.chat_status_label.text().startswith(f"✓ Ran on {LOCAL_ID} (local)")
    assert "within 10 of best" in win.chat_status_label.text()
    assert "saved" in win.cost_rows["local"].toolTip() or win.cost_rows["local"]


def test_an_accepted_suggestion_is_tallied_as_spent(win):
    _ratings(1200)
    _send(win)
    win.advice_banner.switch_button.click()
    _worker().finish("done", {"input_tokens": 1000, "output_tokens": 1000})
    assert win.savings_tally.today()["accepted"] == 1


# ── Failover ─────────────────────────────────────────────────────────────────
def test_a_failed_local_run_offers_cloud_and_a_smaller_model_and_sends_nothing(win):
    _ratings(1290)
    _send(win)
    _worker().fail("model ran out of memory")
    offer = win.advice_banner.offer
    assert offer is not None and offer.cloud.key == SONNET and offer.local.model == SMALL_ID
    assert len(FakeChatWorker.instances) == 1        # offered, not sent
    assert "Send to Anthropic" == win.advice_banner.switch_button.text()


def test_send_to_cloud_after_failure_asks_and_does_not_duplicate_the_turn(win):
    _ratings(1290)
    _send(win)
    _worker().fail("timeout")
    win.advice_banner.switch_button.click()
    assert (_worker().backend, _worker().model) == SONNET
    assert len(win.questions) == 1
    users = [m for m in _worker().messages if m.get("role") == "user"]
    assert len(users) == 1


def test_try_a_smaller_local_model_after_failure(win):
    _ratings(1290)
    _send(win)
    _worker().fail("out of memory")
    win.advice_banner.keep_button.click()
    assert (_worker().backend, _worker().model) == ("lmstudio", SMALL_ID)


@pytest.mark.parametrize("setup", ["local_only", "privacy"])
def test_local_only_and_privacy_never_offer_or_send_to_cloud(win, setup):
    _ratings(1290)
    if setup == "local_only":
        win.settings[ra.MODE_KEY] = ra.LOCAL_ONLY
        win._sync_execution_mode()
    else:
        win.settings["routing_priority"] = "privacy"
    _send(win)
    _worker().fail("server went away")
    offer = win.advice_banner.offer
    assert offer is not None and offer.cloud is None and offer.local.model == SMALL_ID
    assert not any(w.backend == "anthropic" for w in FakeChatWorker.instances)


def test_privacy_never_suggests_cloud_on_a_new_job(win):
    _ratings(1200)
    win.settings["routing_priority"] = "privacy"
    _send(win)
    advice = win.advice_banner.advice
    assert advice is None or advice.suggestion is None or advice.suggestion.local


def test_stop_offers_no_failover(win):
    _ratings(1290)
    _send(win)
    win.stop_chat_worker()
    assert win.advice_banner.offer is None


# ── Nothing on the UI thread fetches ─────────────────────────────────────────
def test_advisor_methods_never_call_a_fetching_api():
    """By AST: the phase-4 methods read caches only."""
    tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text())
    names = {"_advisor_context", "_advisor_candidate", "_advise_job", "_cached_models",
             "_offer_failover", "_tally_finished_job", "_resume_held"}
    banned = {"models_for_provider", "list_models", "refresh", "discover",
              "start_local_discovery", "route_for_request", "urlopen", "request"}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                    if call.func.attr in banned:
                        found.setdefault(node.name, []).append(call.func.attr)
    assert set(found) == set(), found
    assert names <= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_applying_a_recommendation_does_not_rewrite_the_saved_mode(win):
    win.settings[ra.MODE_KEY] = ra.SUGGEST_LOCAL
    win._applying_recommendation = True
    try:
        win.execution_mode_box.setCurrentText("Local only")
    finally:
        win._applying_recommendation = False
    assert win.settings[ra.MODE_KEY] == ra.SUGGEST_LOCAL
    assert win.routing_mode() == ra.LOCAL_ONLY       # the box still limits

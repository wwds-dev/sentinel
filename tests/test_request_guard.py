"""
Sentinel AI — Request Guard Tests
==================================
Type: Unit + integration tests for the money logic.

TODO.md #6 notes that the money path is the untested part of the app. This
covers it in two layers:

  1. services/validator.py — only the edge cases that
     tests/test_cost_and_limits.py does not already cover (that file owns the
     ten gates and the token/cost maths; this one must not duplicate it).
  2. GodAI.authorize_request / record_request / abandon_request — the shared
     guard every agent panel now funnels through, which nothing else covers. These need the window, so it
     is built once headlessly and its side-effecting collaborators (usage
     tracker, chat history, run logger) are replaced with fakes — a test must
     never bill a real request or write into data/chats/.

Run with:  pytest tests/test_request_guard.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.validator import Validator, ValidationResult


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────

class FakeRegistry:
    """Registry stand-in with permissive defaults; override per test."""

    def __init__(self, **overrides):
        self.agent_enabled = True
        self.tool_enabled = True
        self.allows_provider = True
        self.tool_allows_provider_ = True
        self.allows_tool = True
        self.budget = None
        self.tool_budget = None
        self.requires_approval = False
        self.tool_approval = False
        for k, v in overrides.items():
            setattr(self, k, v)

    def is_agent_enabled(self, name):               return self.agent_enabled
    def is_tool_enabled(self, name):                return self.tool_enabled
    def agent_allows_provider(self, agent, prov):   return self.allows_provider
    def tool_allows_provider(self, tool, prov):     return self.tool_allows_provider_
    def agent_allows_tool(self, agent, tool):       return self.allows_tool
    def get_agent_budget(self, agent):              return self.budget
    def get_tool_budget(self, tool):                return self.tool_budget
    def agent_requires_approval(self, agent):       return self.requires_approval
    def tool_requires_approval(self, tool):         return self.tool_approval


ALL_ALLOWED = {
    "allow_openai": True, "allow_deepseek": True, "allow_kimi": True,
    "allow_gemini": True, "allow_anthropic": True, "allow_qwen": True,
}


def validate(registry=None, provider="openai", tool="General Chat",
             api_permissions=None, session_cost=0.0, session_budget=1.0,
             daily_cost=0.0, daily_budget=5.0, estimated_cost=0.01):
    """Run Validator with sensible, allow-everything defaults."""
    v = Validator(registry or FakeRegistry())
    return v.validate(
        agent_name="chat",
        tool_name=tool,
        provider=provider,
        api_permissions=ALL_ALLOWED if api_permissions is None else api_permissions,
        session_cost=session_cost,
        session_budget=session_budget,
        daily_cost=daily_cost,
        daily_budget=daily_budget,
        estimated_cost=estimated_cost,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Validator — edge cases only
#
# The ten gates themselves (disabled agent/tool, provider permissions, the three
# budgets, approval) are covered in tests/test_cost_and_limits.py. Only cases
# that file does not reach live here, so the two suites do not drift apart.
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorEdgeCases:

    def test_unknown_provider_is_blocked_by_default(self):
        # A provider added to the UI but not to the permissions dict must fail
        # closed — a missing key means "not permitted", never "allowed".
        result = validate(provider="somenewprovider", api_permissions={})
        assert result.allowed is False

    def test_budget_boundary_is_exact_under_decimal_comparison(self):
        # Decimal(str(value)) makes 1.00 - 0.90 exactly 0.10, so a request that
        # costs precisely the remaining budget is allowed rather than refused
        # because of binary floating-point noise.
        result = validate(session_cost=0.90, session_budget=1.00, estimated_cost=0.10)
        assert result.allowed is True

    def test_already_over_session_budget_blocks_further_spend(self):
        # Remaining budget is negative here, not merely insufficient.
        result = validate(session_cost=2.00, session_budget=1.00, estimated_cost=0.01)
        assert result.allowed is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. The shared guard on GodAI
# ─────────────────────────────────────────────────────────────────────────────

class FakeUsageTracker:
    def __init__(self):
        self.logged = []
        self.today_total = 0.0

    def calculate_cost_eur(self, backend, model, inp, out):
        return 0.0 if backend == "ollama" else 0.01

    def get_today_total(self):
        return self.today_total

    def log_request(self, agent, backend, model, prompt_text, response_text, usage=None):
        entry = {
            "agent": agent, "backend": backend, "model": model,
            "cost_eur": 0.02, "estimated_cost": 0.02,
            "input_tokens": 10, "output_tokens": 20, "usage": usage,
        }
        self.logged.append(entry)
        return entry


class FakeHistory:
    def __init__(self):
        self.saved = []

    def save_chat(self, agent, backend, model, command, messages, response):
        self.saved.append({
            "agent": agent, "backend": backend, "model": model,
            "command": command, "messages": messages, "response": response,
        })


class FakeRunLogger:
    def __init__(self):
        self.started, self.finished = [], []
        self._n = 0

    def start(self, **kw):
        self._n += 1
        run_id = f"run-{self._n}"
        self.started.append(dict(kw, run_id=run_id))
        return run_id

    def finish(self, run_id, status, **kw):
        self.finished.append(dict(kw, run_id=run_id, status=status))


@pytest.fixture(scope="session")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def _window(app):
    """Build GodAI once — constructing it costs ~10s, so per-test is unusable.

    Modal dialogs are stubbed for the whole module: a real QMessageBox would
    block forever with no one to click it.
    """
    from PySide6.QtWidgets import QMessageBox
    import main

    saved = (QMessageBox.warning, QMessageBox.question)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        w = main.GodAI()
        w.update_usage_labels = lambda: None
        w.load_history_list = lambda: None
        w.current_api_permissions = lambda: dict(ALL_ALLOWED)
        yield w
    finally:
        QMessageBox.warning, QMessageBox.question = saved


@pytest.fixture
def win(_window):
    """The shared window with fresh fakes and zeroed counters per test.

    Nothing here may touch the real usage DB, chat history or run log.
    """
    _window.usage_tracker = FakeUsageTracker()
    _window.history = FakeHistory()
    _window.run_logger = FakeRunLogger()
    _window.validator = Validator(FakeRegistry())
    # Reset per test: a test that narrowed this used to leave every later
    # test with openai disallowed, passing only when a real key in .env let
    # the request through the one-time-consent path instead.
    _window.current_api_permissions = lambda: dict(ALL_ALLOWED)
    _window.session_cost_total = 0.0
    _window.session_request_count = 0
    _window._pending_requests = {}
    return _window


class TestAuthorizeRequest:

    def test_configured_provider_can_be_allowed_once_with_one_confirmation(
            self, win, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(win, "current_api_permissions", lambda: {"allow_deepseek": False})
        monkeypatch.setattr(win, "provider_key_available", lambda provider: True)
        confirmations = []
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: confirmations.append(a) or QMessageBox.Yes,
        )

        assert win.authorize_request(
            "alpha_agent", "deepseek", "deepseek-v4-flash", "hello"
        ) is True
        assert len(confirmations) == 1
        assert len(win.run_logger.started) == 1

    def test_missing_key_blocks_before_consent_or_run(self, win, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(win, "current_api_permissions", lambda: {"allow_deepseek": False})
        monkeypatch.setattr(win, "provider_key_available", lambda provider: False)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("missing keys must not prompt for consent")
            ),
        )

        assert win.authorize_request(
            "alpha_agent", "deepseek", "deepseek-v4-flash", "hello"
        ) is False
        assert win.run_logger.started == []

    def test_authorised_request_returns_true_and_opens_a_run(self, win):
        assert win.authorize_request("alpha_agent", "openai", "gpt-4o", "hello") is True
        assert len(win.run_logger.started) == 1

    def test_authorised_request_stashes_context_for_recording(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "hello")
        assert {context["agent"] for context in win._pending_requests.values()} == {
            "alpha_agent"
        }
        request_id, context = next(iter(win._pending_requests.items()))
        assert context["request_id"] == request_id

    def test_blocked_request_returns_false(self, win):
        win.validator = Validator(FakeRegistry(agent_enabled=False))
        assert win.authorize_request("alpha_agent", "openai", "gpt-4o", "hi") is False

    def test_blocked_request_opens_no_run_and_stashes_nothing(self, win):
        win.validator = Validator(FakeRegistry(agent_enabled=False))
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "hi")
        assert win.run_logger.started == []
        assert win._pending_requests == {}

    def test_declining_the_confirmation_blocks_the_request(self, win, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
        assert win.authorize_request("alpha_agent", "openai", "gpt-4o", "hi") is False
        assert win._pending_requests == {}

    def test_label_is_used_as_descriptor_when_no_tool(self, win):
        # Agent panels pass a mode name via `label`; it must reach the run log
        # without being validated as a registry tool.
        win.authorize_request("osint", "openai", "gpt-4o", "hi", label="Deep Scan")
        assert win.run_logger.started[0]["tool"] == "Deep Scan"

    def test_descriptor_falls_back_to_dash_when_neither_given(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "hi")
        assert win.run_logger.started[0]["tool"] == "-"

    def test_ollama_is_authorised_without_a_confirmation_dialog(self, win, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        def explode(*a, **k):
            raise AssertionError("local model must not prompt for API confirmation")

        monkeypatch.setattr(QMessageBox, "question", explode)
        assert win.authorize_request("alpha_agent", "ollama", "llama3", "hi") is True


class TestRecordRequest:

    def test_records_bills_and_saves(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt text")
        win.record_request("alpha_agent", "the response")
        assert len(win.usage_tracker.logged) == 1
        assert len(win.history.saved) == 1

    def test_adds_cost_to_the_session_total(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "response")
        assert win.session_cost_total == pytest.approx(0.02)
        assert win.session_request_count == 1

    def test_saves_the_chat_under_its_own_agent_name(self, win):
        # The bug this guard fixed: every saved chat was filed as "chat:".
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "response")
        assert win.history.saved[0]["agent"] == "alpha_agent"

    def test_saved_chat_ends_with_the_assistant_response(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "the response")
        last = win.history.saved[0]["messages"][-1]
        assert last == {"role": "assistant", "content": "the response"}

    def test_supplied_messages_are_preserved(self, win):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "resp", messages=msgs)
        saved = win.history.saved[0]["messages"]
        assert saved[:2] == msgs

    def test_closes_the_run_as_success(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "response")
        assert win.run_logger.finished[0]["status"] == "success"

    def test_clears_the_pending_context(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "response")
        assert win._pending_requests == {}

    def test_recording_without_authorising_bills_nothing(self, win):
        # A stray callback must never invent a charge.
        win.record_request("alpha_agent", "response")
        assert win.usage_tracker.logged == []
        assert win.history.saved == []
        assert win.session_cost_total == 0.0

    def test_double_record_bills_only_once(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.record_request("alpha_agent", "response")
        win.record_request("alpha_agent", "response again")
        assert len(win.usage_tracker.logged) == 1
        assert win.session_cost_total == pytest.approx(0.02)

    def test_real_usage_from_the_worker_is_passed_through_to_billing(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.note_request_usage("alpha_agent", {"input_tokens": 111, "output_tokens": 222})
        win.record_request("alpha_agent", "response")
        assert win.usage_tracker.logged[0]["usage"] == {"input_tokens": 111, "output_tokens": 222}

    def test_usage_noted_without_authorisation_is_ignored(self, win):
        win.note_request_usage("alpha_agent", {"input_tokens": 1})
        assert win._pending_requests == {}


class TestAbandonRequest:

    def test_abandoned_request_is_not_billed(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.abandon_request("alpha_agent")
        assert win.usage_tracker.logged == []
        assert win.session_cost_total == 0.0

    def test_abandoned_request_is_not_saved_to_history(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.abandon_request("alpha_agent")
        assert win.history.saved == []

    def test_abandon_closes_the_run_with_its_reason(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.abandon_request("alpha_agent", reason="cancelled")
        assert win.run_logger.finished[0]["status"] == "cancelled"

    def test_abandon_clears_the_pending_context(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.abandon_request("alpha_agent")
        assert win._pending_requests == {}

    def test_abandon_without_authorisation_is_a_no_op(self, win):
        win.abandon_request("alpha_agent")
        assert win.run_logger.finished == []

    def test_recording_after_abandoning_bills_nothing(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "prompt")
        win.abandon_request("alpha_agent")
        win.record_request("alpha_agent", "late response")
        assert win.usage_tracker.logged == []


class TestConcurrentAgents:
    """Each in-flight request owns its context, even for the same agent."""

    def test_two_different_agents_do_not_interfere(self, win):
        win.authorize_request("alpha_agent", "openai", "gpt-4o", "a-prompt")
        win.authorize_request("beta_agent", "openai", "gpt-4o", "m-prompt")
        win.record_request("alpha_agent", "a-response")
        assert any(
            context["agent"] == "beta_agent"
            for context in win._pending_requests.values()
        )
        assert win.history.saved[0]["agent"] == "alpha_agent"
        win.record_request("beta_agent", "m-response")
        assert {s["agent"] for s in win.history.saved} == {"alpha_agent", "beta_agent"}

    def test_same_agent_runs_can_finish_out_of_order(self, win):
        first_id = "alpha-first"
        second_id = "alpha-second"
        win.authorize_request(
            "alpha_agent", "openai", "gpt-4o", "first",
            request_id=first_id,
        )
        win.authorize_request(
            "alpha_agent", "deepseek", "deepseek-chat", "second",
            request_id=second_id,
        )
        win.note_request_usage(
            "alpha_agent", {"input_tokens": 111}, request_id=first_id,
        )

        win.record_request(
            "alpha_agent", "second response", request_id=second_id,
        )
        assert first_id in win._pending_requests
        assert second_id not in win._pending_requests
        assert win.history.saved[0]["messages"][0]["content"] == "second"
        assert win.history.saved[0]["backend"] == "deepseek"

        win.record_request(
            "alpha_agent", "first response", request_id=first_id,
        )
        assert [saved["messages"][0]["content"] for saved in win.history.saved] == [
            "second", "first",
        ]
        assert [saved["backend"] for saved in win.history.saved] == [
            "deepseek", "openai",
        ]
        assert win.usage_tracker.logged[1]["usage"] == {"input_tokens": 111}
        assert [run["run_id"] for run in win.run_logger.finished] == [
            "run-2", "run-1",
        ]
        assert win._pending_requests == {}

    def test_agent_only_direct_calls_remain_supported(self, win):
        assert win.authorize_request(
            "alpha_agent", "openai", "gpt-4o", "legacy"
        ) is True
        win.note_request_usage("alpha_agent", {"input_tokens": 9})
        win.record_request("alpha_agent", "legacy response")
        assert win.usage_tracker.logged[0]["usage"] == {"input_tokens": 9}
        assert win._pending_requests == {}

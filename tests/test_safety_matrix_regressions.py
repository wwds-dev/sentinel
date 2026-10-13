"""Regression contracts closing gaps found while building
docs/testing_safety_matrix.md (TODO.md P0 "Testing roadmap — safety gate").

Each test pins one boundary this matrix identified as a gap and names the
fix. All fakes are local to this file (the project's own convention: helpers
are copied between test files, never imported). No network, no subprocess,
no real model call; `tests/conftest.py`'s deny-by-default guard covers the
rest.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QListWidget, QListWidgetItem, QMessageBox,
)


@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


# ═════════════════════════════════════════════════════════════════════════════
# 1. Write/delete boundary — every destructive confirmation defaults to No.
#
# Three call sites built their QMessageBox.question() without an explicit
# default button: Qt leaves Enter bound to nothing (no autoDefault) instead
# of refusing, which is not the same guarantee as the "default No" the rest
# of the app's consent dialogs give (Tunnel, Bug Spray, Trace Live Research,
# Bloodhound's source consent all pass QMessageBox.No explicitly). Fixed in
# main.py (delete_selected_chat, delete_selected_search) and
# ui/panels/manager.py (approve_spec).
# ═════════════════════════════════════════════════════════════════════════════

class TestDestructiveConfirmationsDefaultToNo:

    def test_forge_create_agent_confirmation_defaults_to_no(self, qapp, monkeypatch):
        from ui.panels.manager import ManagerPanel

        host = SimpleNamespace(
            agent_instances={}, agent_factory=None,
            load_models_into=lambda *a, **k: None,
            register_model_loader=lambda *a, **k: None,
        )
        panel = ManagerPanel(host)
        panel.pending_spec = {"name": "firewall_review", "label": "Firewall Review"}

        captured = {}

        def question(*args, **kwargs):
            captured["args"] = args
            return QMessageBox.No

        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
        panel.approve_spec()

        assert captured, "approve_spec must ask for confirmation"
        assert captured["args"][-1] == QMessageBox.No, (
            "the create-agent confirmation must default to No"
        )

    def test_delete_saved_chat_confirmation_defaults_to_no(self, qapp, monkeypatch, tmp_path):
        import main

        monkeypatch.setattr(main, "build_description", lambda root=None: "test build")
        saved = (QMessageBox.warning, QMessageBox.question, QMessageBox.information)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        try:
            window = main.GodAI()
            try:
                chat_file = tmp_path / "chat.json"
                chat_file.write_text("{}", encoding="utf-8")
                item = QListWidgetItem("a saved chat")
                item.setData(Qt.UserRole, str(chat_file))
                window.history_list.addItem(item)
                window.history_list.setCurrentItem(item)

                captured = {}

                def question(*args, **kwargs):
                    captured["args"] = args
                    return QMessageBox.No

                QMessageBox.question = staticmethod(question)
                window.delete_selected_chat()

                assert captured, "deleting a saved chat must ask for confirmation"
                assert captured["args"][-1] == QMessageBox.No, (
                    "the delete-chat confirmation must default to No"
                )
                assert chat_file.exists(), "a No-default must not have deleted anything"
            finally:
                try:
                    window.shutdown_panels()
                except Exception:
                    pass
                window.hide()
                import shiboken6
                shiboken6.delete(window)
                QApplication.processEvents()
        finally:
            QMessageBox.warning, QMessageBox.question, QMessageBox.information = saved

    def test_delete_saved_search_confirmation_defaults_to_no(self, qapp, monkeypatch, tmp_path):
        import main

        monkeypatch.setattr(main, "build_description", lambda root=None: "test build")
        saved = (QMessageBox.warning, QMessageBox.question, QMessageBox.information)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        try:
            window = main.GodAI()
            try:
                search_file = tmp_path / "search.json"
                search_file.write_text("{}", encoding="utf-8")
                item = QListWidgetItem("a saved search")
                item.setData(Qt.UserRole, str(search_file))
                window.saved_search_list.addItem(item)
                window.saved_search_list.setCurrentItem(item)

                captured = {}

                def question(*args, **kwargs):
                    captured["args"] = args
                    return QMessageBox.No

                QMessageBox.question = staticmethod(question)
                window.delete_selected_search()

                assert captured, "deleting a saved search must ask for confirmation"
                assert captured["args"][-1] == QMessageBox.No, (
                    "the delete-search confirmation must default to No"
                )
                assert search_file.exists(), "a No-default must not have deleted anything"
            finally:
                try:
                    window.shutdown_panels()
                except Exception:
                    pass
                window.hide()
                import shiboken6
                shiboken6.delete(window)
                QApplication.processEvents()
        finally:
            QMessageBox.warning, QMessageBox.question, QMessageBox.information = saved


# ═════════════════════════════════════════════════════════════════════════════
# 2. Budget/cap boundary — Trace's Structure Query re-checks the caps against
#    the assembled prompt, not just the target string.
#
# Bloodhound (`_send_dossier_request`) and Bug Spray (`analyse`) both price
# the whole request before sending; Trace's `analyse()` priced only the
# target text via `authorize()` and never called `reauthorize()` on the
# built messages (which carry the OSINT-catalogue text and can be far
# larger than the target). Fixed in ui/panels/osint.py `analyse()`.
# ═════════════════════════════════════════════════════════════════════════════

class FakeWorker(QObject):
    token_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)
    instances: list = []

    def __init__(self, run_backend_func, backend, model, messages, prompt):
        super().__init__()
        self.backend, self.model = backend, model
        self.messages, self.prompt = list(messages), prompt
        self.running = False
        FakeWorker.instances.append(self)

    def start(self):
        self.running = True

    def isRunning(self):  # noqa: N802
        return self.running

    def cancel(self):
        self.running = False


class BudgetFakeHost:
    """Just enough of AgentHost to drive Trace's Structure Query."""

    def __init__(self, recheck_result: bool = True):
        self.agent_instances = {}
        self.calls = []
        self.recheck_calls = []
        self.recheck_result = recheck_result

    def load_models_into(self, provider_box, model_box, context, empty_placeholder=False):
        model_box.clear()
        model_box.addItems(["m1"])

    def register_model_loader(self, agent_key, loader):
        pass

    def authorize_request(self, agent, provider, model, prompt, tool=None,
                          label=None, request_id=None):
        self.calls.append(("authorize", prompt))
        return True

    def recheck_budget(self, agent, provider, model, prompt, request_id=None):
        self.recheck_calls.append(prompt)
        return self.recheck_result

    def record_request(self, agent, response, messages=None, request_id=None):
        self.calls.append(("record", response))

    def abandon_request(self, agent, reason="error", request_id=None):
        self.calls.append(("abandon", reason))

    def note_request_error(self, agent, error, request_id=None):
        self.calls.append(("error", error))

    def note_request_usage(self, agent, usage, request_id=None):
        pass

    def record_external_research(self, **details):
        self.calls.append(("external", details))

    def run_backend(self, backend, model, messages, prompt):
        self.calls.append(("run",))
        return "done"

    def _note_failure(self, context, exc, widget=None):
        self.calls.append(("failure", context))


def _trace_panel(qapp, monkeypatch, host):
    from agents.osint_agent import OSINTAgent
    from ui.panels.osint import OsintPanel

    FakeWorker.instances.clear()
    monkeypatch.setattr(OsintPanel, "worker_class", FakeWorker)
    host.agent_instances["osint"] = OSINTAgent()
    panel = OsintPanel(host)
    panel.target_input.setText("acme.test")
    return panel


class TestTraceStructureQueryBudgetRecheck:

    def test_the_recheck_sees_the_assembled_prompt_not_the_bare_target(
            self, qapp, monkeypatch):
        host = BudgetFakeHost(recheck_result=True)
        panel = _trace_panel(qapp, monkeypatch, host)

        panel.analyse()

        assert host.recheck_calls, "analyse() must re-check the budget on the built prompt"
        assert host.recheck_calls[0] != "acme.test"
        assert "acme.test" in host.recheck_calls[0]
        assert len(FakeWorker.instances) == 1

    def test_a_cap_the_real_prompt_exceeds_is_blocked_before_the_worker_starts(
            self, qapp, monkeypatch):
        host = BudgetFakeHost(recheck_result=False)
        panel = _trace_panel(qapp, monkeypatch, host)

        panel.analyse()

        assert FakeWorker.instances == [], "a blocked re-check must not start a worker"
        assert ("run",) not in host.calls
        assert ("abandon", "blocked") in host.calls
        assert "budget" in panel.status_label.text().lower()


# ═════════════════════════════════════════════════════════════════════════════
# 3. Secret handling boundary — an OSINT source key never reaches the
#    activity trail, the section cards, or the saved-search file Live
#    Research writes through `record_external_research`.
#
# The providers already put every key in a header or query parameter, never
# in the parsed result (test_key_check.py, test_intel_sources.py pin that at
# the provider boundary); this closes the matching gap at the Trace panel's
# own surface, which nothing previously exercised end to end.
# ═════════════════════════════════════════════════════════════════════════════

CANARY = "CANARY-sk-9d2e7a1c4f80"


class CanaryHost(BudgetFakeHost):
    """Adds a real, isolated HistoryStore so a saved search is really written."""

    def __init__(self, folder):
        super().__init__(recheck_result=True)
        from services.history_store import HistoryStore

        self.history = HistoryStore(folder)
        self.run_logger = SimpleNamespace(
            start=lambda **k: "run-1", finish=lambda **k: None,
        )

    def load_history_list(self):
        pass

    def load_saved_searches(self):
        pass

    def record_external_research(self, **details):
        super().record_external_research(**details)
        import main
        main.GodAI.record_external_research(self, **details)


class SyncDomainWorker:
    """Runs the (patched) provider lookup synchronously, no QThread involved."""

    def __init__(self, target: str):
        self.target = target
        self.progress_signal = SimpleNamespace(connect=lambda *a, **k: None)
        self.finished_signal = SimpleNamespace(connect=lambda *a, **k: None)
        self.error_signal = SimpleNamespace(connect=lambda *a, **k: None)
        self._on_finished = None

    def start(self):
        from providers import domain_lookup
        result = domain_lookup.lookup(self.target)
        self._on_finished(result)


def test_an_osint_source_key_never_reaches_the_activity_log_or_the_saved_search(
        qapp, monkeypatch, tmp_path):
    from agents.osint_agent import OSINTAgent
    from providers import domain_lookup
    from ui.panels.osint import OsintPanel

    monkeypatch.setenv("SHODAN_API_KEY", CANARY)
    monkeypatch.setenv("CRIMINALIP_API_KEY", CANARY)
    monkeypatch.setenv("IPINFO_API_KEY", CANARY)

    def fake_lookup(target, on_progress=None, should_stop=None):
        if on_progress:
            on_progress("WHOIS", "checked")
        return {
            "type": "domain", "query": target,
            "whois": {"registrar": "Example Registrar"},
            "sources_contacted": [{"source": "WHOIS", "status": "checked"}],
        }

    monkeypatch.setattr(domain_lookup, "lookup", fake_lookup)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    host = CanaryHost(tmp_path)
    host.agent_instances["osint"] = OSINTAgent()

    class SyncWorker(SyncDomainWorker):
        pass

    monkeypatch.setattr(OsintPanel, "lookup_worker_class", SyncWorker)
    panel = OsintPanel(host)
    panel.target_input.setText("acme.test")
    panel.type_box.setCurrentText("Domain")

    # live_research wires worker.finished_signal.connect(panel._on_lookup_finished);
    # the stand-in SyncWorker calls that callback directly from start().
    original_start_build = SyncWorker.start

    def start(self):
        self._on_finished = panel._on_lookup_finished
        original_start_build(self)

    monkeypatch.setattr(SyncWorker, "start", start)

    panel.live_research()

    assert CANARY not in panel.activity_box.toPlainText()
    assert CANARY not in panel._last_response
    for label in panel.sections.findChildren(type(panel.status_label)):
        assert CANARY not in label.text()

    for path in Path(tmp_path).rglob("*"):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes(), f"secret written to {path}"

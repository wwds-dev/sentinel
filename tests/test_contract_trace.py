"""Phase 3 workflow contracts for the Trace agent (panel, agent, providers).

One section per workflow. Where it applies, each carries the same five cases:
happy path, invalid input, denied consent (no worker, no network), cancel or
provider error, and persist-and-restart (saved, then reloaded by a fresh panel
from a fresh `HistoryStore` on the same folder).

Everything is offline. Workers are faked at the class boundary exactly as in
`tests/test_ui_panels.py` (the helpers are copied, not imported, because
fixtures do not cross test files). Where a case needs the real worker's
wiring, the real worker class runs synchronously against a patched provider
`lookup`. No socket is ever opened: an autouse fixture makes a connect or a
name lookup fail the test.

Persistence goes through the application's own `GodAI.record_external_research`,
`open_selected_search`, `load_saved_searches` and `saved_search_title_from_data`,
bound to a small stand-in window so no `GodAI` has to be constructed.
"""

import json
import socket
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.history_store import HistoryStore
from ui.workers import DomainLookupWorker, ExposureLookupWorker, IdentityLookupWorker


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic model output and provider results (small, defined here, never live)
# ─────────────────────────────────────────────────────────────────────────────

PLAN = (
    "## QUERY STRUCTURE\nSTRUCT-ALPHA\n"
    "## GOOGLE DORKS\nsite:acme.test DORK-BETA\n"
    "## PUBLIC SOURCES\nSOURCE-GAMMA\n"
    "## SUMMARY & NEXT STEPS\nSUMMARY-DELTA"
)

SOURCES = [{"source": "Synthetic source", "status": "checked"}]

LIVE_RESULTS = {
    "Domain": {"type": "domain", "query": "acme.test",
               "whois": {"registrar": "Registrar-Zeta"}, "sources_contacted": SOURCES},
    "IP Address": {"type": "ip", "query": "8.8.8.8",
                   "network": {"asn": "AS64500"}, "sources_contacted": SOURCES},
    "Username": {"type": "username", "query": "researcher_1",
                 "github": {"found": True, "marker": "GH-ETA"},
                 "sources_contacted": SOURCES},
    "Company": {"type": "company", "query": "Acme Holdings",
                "legal_entities": {"records": [{"lei": "LEI-THETA"}]},
                "sources_contacted": SOURCES},
    "Email": {"type": "email", "query": "analyst@acme.test",
              "reputation": {"score": "SCORE-IOTA"}, "sources_contacted": SOURCES},
}
LIVE_TARGETS = {
    "Domain": "acme.test", "IP Address": "8.8.8.8", "Username": "@researcher_1",
    "Company": "Acme Holdings", "Email": "analyst@acme.test",
}
LIVE_TYPES = list(LIVE_RESULTS)

EXPOSURE_RESULTS = {
    "Domain": {"type": "exposure", "query": "acme.test", "target_type": "domain",
               "summary": {}, "ransomware_live": {"status": "ok"},
               "sources_contacted": SOURCES},
    "Company": {"type": "exposure", "query": "Acme Holdings", "target_type": "company",
                "summary": {}, "ransomware_live": {"status": "ok"},
                "sources_contacted": SOURCES},
    "Email": {"type": "exposure", "query": "analyst@acme.test", "target_type": "email",
              "summary": {}, "ransomware_live": {"status": "ok"},
              "sources_contacted": SOURCES},
}
EXPOSURE_TARGETS = {
    "Domain": "acme.test", "Company": "Acme Holdings", "Email": "analyst@acme.test",
}


@pytest.fixture(scope="session")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A connect or a DNS lookup anywhere in this file is a failure."""
    def refuse(*args, **kwargs):
        raise AssertionError("network touched in an offline contract test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# ─────────────────────────────────────────────────────────────────────────────
# Fakes at the class boundary (copied from tests/test_ui_panels.py)
# ─────────────────────────────────────────────────────────────────────────────

class FakeHost:
    def __init__(self, models=("m1", "m2")):
        self.models = list(models)
        self.loaders = {}
        self.calls = []
        self.agent_instances = {}
        self.authorized = True

    def load_models_into(self, provider_box, model_box, context,
                         empty_placeholder=False):
        self.calls.append(("load", provider_box.currentText(), context))
        model_box.clear()
        model_box.addItems([f"{provider_box.currentText()}-{m}" for m in self.models])

    def register_model_loader(self, agent_key, loader):
        self.loaders[agent_key] = loader

    def authorize_request(self, agent, provider, model, prompt,
                          tool=None, label=None, request_id=None):
        self.calls.append(("authorize", agent, provider, model, prompt, tool, label))
        return self.authorized

    def record_request(self, agent, response, messages=None, request_id=None):
        self.calls.append(("record", agent, response, messages))

    def abandon_request(self, agent, reason="error", request_id=None):
        self.calls.append(("abandon", agent, reason))

    def note_request_usage(self, agent, usage, request_id=None):
        self.calls.append(("usage", agent, usage))

    def record_external_research(self, **details):
        self.calls.append(("external", details))

    def run_backend(self, backend, model, messages, prompt):
        self.calls.append(("run", backend, model, messages, prompt))
        return "done"

    def _note_failure(self, context, exc, widget=None):
        self.calls.append(("failure", context))

    def show_agent_docs(self):
        self.calls.append(("docs",))

    def of(self, kind):
        return [call for call in self.calls if call[0] == kind]


class FakeWorker(QObject):
    """A ChatWorker with the threads taken out."""

    token_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)
    status_signal = Signal(str)
    instances = []

    def __init__(self, run_backend, provider, model, messages, prompt):
        super().__init__()
        self.args = (provider, model, messages, prompt)
        self.started = False
        self.cancelled = False
        self.running = True
        # A real thread has ended by the time its answer is acted on.
        self.finished_signal.connect(lambda *_: setattr(self, "running", False))
        self.error_signal.connect(lambda *_: setattr(self, "running", False))
        FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True


class FakeLookupWorker(QObject):
    progress_signal = Signal(str, str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)
    instances = []

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.running = True
        self.cancelled = False
        self.__class__.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        self.running = False


class FakeIdentityLookupWorker(FakeLookupWorker):
    instances = []

    def __init__(self, target, query_type, sources=()):
        super().__init__(target)
        self.query_type = query_type
        self.sources = tuple(sources)


class FakeExposureLookupWorker(FakeIdentityLookupWorker):
    instances = []


def all_fake_lookup_workers():
    return (FakeLookupWorker.instances + FakeIdentityLookupWorker.instances
            + FakeExposureLookupWorker.instances)


# Real worker classes with the thread taken out: ``start`` runs ``run`` inline,
# so the real provider call wiring (patched `lookup`) is exercised end to end.
class SyncDomainWorker(DomainLookupWorker):
    def start(self):
        self.run()


class SyncIdentityWorker(IdentityLookupWorker):
    def start(self):
        self.run()


class SyncExposureWorker(ExposureLookupWorker):
    def start(self):
        self.run()


class _Deferred:
    """``start`` only marks the worker alive; the test calls ``run`` later."""
    _alive = False

    def start(self):
        self._alive = True

    def isRunning(self):
        return self._alive

    def run(self):
        try:
            super().run()
        finally:
            self._alive = False


class DeferredDomainWorker(_Deferred, DomainLookupWorker):
    pass


class DeferredIdentityWorker(_Deferred, IdentityLookupWorker):
    pass


class DeferredExposureWorker(_Deferred, ExposureLookupWorker):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Persistence: the application's own save and reopen code, no GodAI built
# ─────────────────────────────────────────────────────────────────────────────

class FakeRunLogger:
    def __init__(self):
        self.started, self.finished = [], []

    def start(self, **details):
        self.started.append(details)
        return f"run-{len(self.started)}"

    def finish(self, **details):
        self.finished.append(details)


class PersistHost(FakeHost):
    """FakeHost whose saves land in a real `HistoryStore`, like `GodAI`'s do."""

    def __init__(self, folder):
        super().__init__()
        self.history = HistoryStore(folder)
        self.run_logger = FakeRunLogger()

    def load_history_list(self):
        pass

    def load_saved_searches(self):
        pass

    def record_request(self, agent, response, messages=None, request_id=None):
        super().record_request(agent, response, messages, request_id)
        _, _agent, provider, model, prompt, tool, label = self.of("authorize")[-1]
        if messages is None:
            messages = [{"role": "user", "content": prompt}]
        self.history.save_chat(
            agent=agent, backend=provider, model=model,
            command=label or tool or "-",
            messages=messages + [{"role": "assistant", "content": response}],
            response=response,
        )

    def record_external_research(self, **details):
        super().record_external_research(**details)
        import main
        main.GodAI.record_external_research(self, **details)


def _restarted_window(folder, panel):
    """The saved-search half of `GodAI`, over a fresh store on the same folder."""
    import main

    class Window:
        open_selected_search = main.GodAI.open_selected_search
        load_saved_searches = main.GodAI.load_saved_searches
        saved_search_title_from_data = main.GodAI.saved_search_title_from_data

        def __init__(self):
            self.history = HistoryStore(folder)
            self.panels = {"osint": panel}
            self.saved_search_list = QListWidget()
            self.saved_search_search = QLineEdit()
            self.selected = []
            self.failures = []

        def select_agent(self, key):
            self.selected.append(key)

        def _note_failure(self, context, exc, widget=None):
            self.failures.append(context)

    window = Window()
    window.load_saved_searches()
    return window


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def dialogs(monkeypatch):
    """Record every modal Trace can raise; ``question`` answers ``answer``."""
    log = {"warning": [], "information": [], "question": [], "answer": QMessageBox.Yes}

    def warning(parent, title, text, *a, **k):
        log["warning"].append((title, text))

    def information(parent, title, text, *a, **k):
        log["information"].append((title, text))

    def question(parent, title, text, *a, **k):
        log["question"].append((title, text))
        return log["answer"]

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(information))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return log


@pytest.fixture(autouse=True)
def _offline_catalog_and_keys(monkeypatch):
    """No catalogue cache read, no saved sanctions key changing the sources."""
    from services import osint_catalog
    from providers import company_lookup

    monkeypatch.setattr(osint_catalog, "cached_tools", lambda: [])
    monkeypatch.setattr(osint_catalog, "refresh_in_background", lambda *a, **k: None)
    monkeypatch.setattr(company_lookup, "opensanctions_key", lambda: "")


def _build_panel(qapp, monkeypatch, host, *, workers="fake"):
    from agents.osint_agent import OSINTAgent
    from ui.panels.osint import OsintPanel

    monkeypatch.setattr(OsintPanel, "worker_class", FakeWorker)
    if workers == "fake":
        classes = (FakeLookupWorker, FakeIdentityLookupWorker, FakeExposureLookupWorker)
    elif workers == "sync":
        classes = (SyncDomainWorker, SyncIdentityWorker, SyncExposureWorker)
    else:
        classes = (DeferredDomainWorker, DeferredIdentityWorker, DeferredExposureWorker)
    monkeypatch.setattr(OsintPanel, "lookup_worker_class", classes[0])
    monkeypatch.setattr(OsintPanel, "identity_lookup_worker_class", classes[1])
    monkeypatch.setattr(OsintPanel, "exposure_lookup_worker_class", classes[2])
    for cls in (FakeWorker, FakeLookupWorker, FakeIdentityLookupWorker,
                FakeExposureLookupWorker):
        cls.instances.clear()
    host.agent_instances["osint"] = OSINTAgent()
    return OsintPanel(host)


@pytest.fixture
def trace(qapp, monkeypatch, dialogs):
    panel = _build_panel(qapp, monkeypatch, FakeHost())
    panel.target_input.setText("acme.test")
    return panel


@pytest.fixture
def sync_trace(qapp, monkeypatch, dialogs):
    panel = _build_panel(qapp, monkeypatch, FakeHost(), workers="sync")
    monkeypatch.setattr(panel, "_choose_email_sources", lambda target: ("emailrep",))
    monkeypatch.setattr(panel, "_choose_exposure_sources",
                        lambda target, kind: ("ransomware_live",))
    return panel


@pytest.fixture
def deferred_trace(qapp, monkeypatch, dialogs):
    panel = _build_panel(qapp, monkeypatch, FakeHost(), workers="deferred")
    monkeypatch.setattr(panel, "_choose_email_sources", lambda target: ("emailrep",))
    monkeypatch.setattr(panel, "_choose_exposure_sources",
                        lambda target, kind: ("ransomware_live",))
    return panel


def _persisting_trace(qapp, monkeypatch, folder, *, workers="sync"):
    host = PersistHost(folder)
    panel = _build_panel(qapp, monkeypatch, host, workers=workers)
    monkeypatch.setattr(panel, "_choose_email_sources", lambda target: ("emailrep",))
    monkeypatch.setattr(panel, "_choose_exposure_sources",
                        lambda target, kind: ("ransomware_live",))
    return panel, host


def _patch_providers(monkeypatch, *, results=None, raises=None, seen=None):
    """Replace every provider `lookup` with a synthetic one (or one that fails)."""
    from providers import (company_lookup, domain_lookup, email_lookup,
                           exposure_lookup, username_lookup)

    def make(name):
        def lookup(target, *args, **kwargs):
            if seen is not None:
                seen.append((name, target, args, kwargs))
            if raises is not None:
                raise raises
            result = (results or {})[name]
            if name == "domain" and target in result:
                result = result[target]
            return json.loads(json.dumps(result))
        return lookup

    for name, module in (("domain", domain_lookup), ("username", username_lookup),
                         ("email", email_lookup), ("company", company_lookup),
                         ("exposure", exposure_lookup)):
        monkeypatch.setattr(module, "lookup", make(name))


PROVIDER_FOR = {
    "Domain": "domain", "IP Address": "domain", "Username": "username",
    "Company": "company", "Email": "email",
}


def _live_provider_results():
    """Domain and IP share one provider; it answers by what the target is."""
    results = {PROVIDER_FOR[kind]: result for kind, result in LIVE_RESULTS.items()}
    results["domain"] = {
        LIVE_TARGETS["Domain"]: LIVE_RESULTS["Domain"],
        LIVE_TARGETS["IP Address"]: LIVE_RESULTS["IP Address"],
    }
    results["exposure"] = EXPOSURE_RESULTS["Domain"]
    return results


def _labels(panel):
    return " ".join(label.text() for label in panel.sections.findChildren(QLabel))


# ─────────────────────────────────────────────────────────────────────────────
# Invalid targets, shared by every workflow
# ─────────────────────────────────────────────────────────────────────────────

INVALID = [
    ("Domain", "not a domain"),
    ("Domain", "exa\tmple.com"),
    ("Domain", "localhost"),
    ("IP Address", "999.1.1.1"),
    ("IP Address", "example.com"),
    ("Username", "x"),
    ("Username", "bad name!"),
    ("Email", "analyst@"),
    ("Email", "analyst@example"),
    ("Company", "12345"),
    ("Company", "a@b.com"),
    ("Phone", "12"),
    ("Person", "@"),
    ("Auto-detect", "x"),
    ("Auto-detect", "a" * 600),
]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Analyse (Structure Query) with a local model
# ═════════════════════════════════════════════════════════════════════════════

class TestAnalyseLocalModel:

    def test_happy_path_sends_the_real_prompt_to_the_local_worker_and_fills_four_cards(
            self, trace, monkeypatch):
        shown = {}
        monkeypatch.setattr(trace.sections, "show_sections",
                            lambda cards, raw=None: shown.update(cards=cards, raw=raw))
        trace.provider_box.setCurrentText("ollama")
        trace.type_box.setCurrentText("Domain")
        trace.analyse()

        worker = FakeWorker.instances[-1]
        provider, model, messages, prompt = worker.args
        assert provider == "ollama" and worker.started and prompt == "acme.test"
        assert messages[0]["role"] == "system"
        assert "## QUERY STRUCTURE" in messages[0]["content"]
        assert "Target (query type: Domain): acme.test" in messages[1]["content"]
        assert [c for c in trace.host.of("run")] == []      # no backend call from the panel

        worker.finished_signal.emit(PLAN)
        assert [(c[0], c[1]) for c in shown["cards"]] == [
            ("Query structure", "STRUCT-ALPHA"),
            ("Google dorks", "site:acme.test DORK-BETA"),
            ("Public sources", "SOURCE-GAMMA"),
            ("Summary and next steps", "SUMMARY-DELTA"),
        ]
        assert shown["raw"] == PLAN

    @pytest.mark.parametrize("query_type,target", INVALID)
    def test_invalid_input_stops_before_the_guard_and_names_the_problem(
            self, trace, dialogs, query_type, target):
        trace.type_box.setCurrentText(query_type)
        trace.target_input.setText(target)
        trace.analyse()
        assert trace.host.of("authorize") == []
        assert FakeWorker.instances == []
        assert dialogs["warning"] and dialogs["warning"][-1][0] == "Invalid Target"
        assert dialogs["warning"][-1][1]
        assert trace.status_label.text() == "Check the target and try again."
        assert trace.analyse_btn.isEnabled() and not trace.stop_btn.isEnabled()

    def test_a_blocked_request_leaves_no_trace_of_a_run(self, trace):
        trace.analyse()
        first = FakeWorker.instances[-1]
        first.finished_signal.emit(PLAN)
        before_cards = trace.sections._raw
        calls_before = list(trace.host.calls)

        trace.host.authorized = False
        trace.target_input.setText("other.test")
        trace.analyse()

        assert len(FakeWorker.instances) == 1                   # zero new workers
        assert [c for c in trace.host.calls[len(calls_before):] if c[0] != "authorize"] == []
        assert trace.sections._raw == before_cards              # the old answer is untouched
        assert "other.test" not in trace.activity_box.toPlainText()
        assert trace.status_label.text() == "Done."
        assert trace.analyse_btn.isEnabled() and not trace.stop_btn.isEnabled()

    def test_a_provider_error_can_be_retried_and_the_retry_lands(self, trace):
        trace.analyse()
        trace.worker.error_signal.emit("provider down")
        assert trace.host.of("record") == []
        assert trace.stream_box.toPlainText().count("provider down") == 1

        trace.analyse()
        retry = FakeWorker.instances[-1]
        assert len(FakeWorker.instances) == 2
        retry.token_signal.emit("## QUERY STRUCTURE\nok")
        retry.finished_signal.emit(PLAN)
        assert trace.status_label.text() == "Done."
        assert len(trace.host.of("record")) == 1

    def test_stop_then_a_new_run_is_not_swallowed_by_the_old_stop(self, trace):
        trace.analyse()
        old = trace.worker
        trace.stop()
        assert old.cancelled

        old.running = False
        trace.analyse()
        new = FakeWorker.instances[-1]
        assert new is not old
        new.token_signal.emit("fresh")
        new.finished_signal.emit(PLAN)
        assert trace.status_label.text() == "Done."
        assert len(trace.host.of("record")) == 1
        assert ("abandon", "osint", "cancelled") in trace.host.calls

    def test_stop_when_nothing_runs_closes_no_request(self, trace):
        trace.stop()
        assert trace.host.of("abandon") == []
        assert trace.analyse_btn.isEnabled() and not trace.stop_btn.isEnabled()
        assert FakeWorker.instances == []

    def test_persist_and_restart_reopens_the_plan_without_contacting_anything(
            self, qapp, monkeypatch, dialogs, tmp_path):
        panel, host = _persisting_trace(qapp, monkeypatch, tmp_path, workers="fake")
        panel.provider_box.setCurrentText("ollama")
        panel.model_box.setCurrentText("ollama-m2")
        panel.type_box.setCurrentText("Domain")
        panel.target_input.setText("acme.test")
        panel.analyse()
        FakeWorker.instances[-1].finished_signal.emit(PLAN)
        assert len(list(tmp_path.glob("*.json"))) == 1

        # ── restart: new host, new panel, new store over the same folder ──
        host2 = FakeHost()
        panel2 = _build_panel(qapp, monkeypatch, host2)
        window = _restarted_window(tmp_path, panel2)
        assert window.saved_search_list.count() == 1
        assert "acme.test" in window.saved_search_list.item(0).text()
        assert "Domain" in window.saved_search_list.item(0).text()

        window.open_selected_search(window.saved_search_list.item(0))
        assert window.selected == ["osint"]
        assert panel2.target_input.text() == "acme.test"
        assert panel2.type_box.currentText() == "Domain"
        assert panel2.provider == "ollama" and panel2.model == "ollama-m2"
        assert panel2.sections._raw == PLAN
        assert panel2.status_label.text() == "Saved search loaded."
        # Reopening is not a run: no guard, no worker, no second save.
        assert host2.calls == [c for c in host2.calls if c[0] == "load"]
        assert FakeWorker.instances == [] and all_fake_lookup_workers() == []
        assert len(list(tmp_path.glob("*.json"))) == 1

    def test_a_corrupt_saved_plan_is_reported_and_leaves_the_panel_alone(
            self, qapp, monkeypatch, dialogs, tmp_path):
        (tmp_path / "2026-01-01_00-00-00-000000_aa.json").write_text("{not json")
        panel = _build_panel(qapp, monkeypatch, FakeHost())
        panel.target_input.setText("keep.test")
        window = _restarted_window(tmp_path, panel)

        class Item:
            def data(self, role):
                return str(tmp_path / "2026-01-01_00-00-00-000000_aa.json")
            def text(self):
                return "corrupt"

        window.open_selected_search(Item())
        assert dialogs["warning"] and dialogs["warning"][-1][0] == "Open Failed"
        assert panel.target_input.text() == "keep.test"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Live Research, per target type
# ═════════════════════════════════════════════════════════════════════════════

class TestLiveResearch:

    @pytest.mark.parametrize("kind", LIVE_TYPES)
    def test_happy_path_runs_the_real_worker_against_the_provider_and_saves(
            self, sync_trace, monkeypatch, dialogs, kind):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(LIVE_TARGETS[kind])
        sync_trace.live_research()

        assert len(seen) == 1 and seen[0][1] == LIVE_TARGETS[kind]
        if kind == "Email":
            assert seen[0][3]["selected_sources"] == ("emailrep",)
        if kind == "Company":
            assert seen[0][3]["sanctions"] is False and seen[0][3]["court_records"] is False
        assert sync_trace.status_label.text() == "Live Research complete."
        assert sync_trace.live_btn.isEnabled() and sync_trace.analyse_btn.isEnabled()
        assert json.loads(sync_trace.sections._raw) == LIVE_RESULTS[kind]
        saved = sync_trace.host.of("external")
        assert len(saved) == 1
        assert json.loads(saved[0][1]["response"]) == LIVE_RESULTS[kind]
        assert saved[0][1]["cancelled"] is False
        assert sync_trace.host.of("authorize") == []            # not billed as a model run
        assert FakeWorker.instances == []                       # no model worker

    @pytest.mark.parametrize("query_type,target", INVALID)
    def test_invalid_input_never_asks_consent_or_starts_a_worker(
            self, sync_trace, monkeypatch, dialogs, query_type, target):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText(query_type)
        sync_trace.target_input.setText(target)
        sync_trace.live_research()
        assert seen == []
        assert dialogs["question"] == []
        assert sync_trace.host.of("external") == []
        assert sync_trace.worker is None
        assert dialogs["warning"] or dialogs["information"]

    @pytest.mark.parametrize("target", [
        "127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.3.4", "169.254.1.1",
        "::1", "fe80::1", "0.0.0.0", "224.0.0.1",
    ])
    def test_a_non_public_ip_never_contacts_anything_and_asks_no_consent(
            self, sync_trace, monkeypatch, dialogs, target):
        """QA audit must-fix #14: a private/loopback/link-local/reserved IP has
        no public footprint, so Live Research must refuse before asking
        consent or touching WHOIS/DNS/threat-intel sources."""
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText("IP Address")
        sync_trace.target_input.setText(target)
        sync_trace.live_research()
        assert seen == []
        assert dialogs["question"] == []
        assert sync_trace.host.of("external") == []
        assert sync_trace.worker is None
        assert dialogs["warning"]

    def test_a_public_ip_is_unaffected_by_the_non_public_check(
            self, sync_trace, monkeypatch, dialogs):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText("IP Address")
        sync_trace.target_input.setText("8.8.8.8")
        sync_trace.live_research()
        assert len(seen) == 1
        assert dialogs["warning"] == []

    @pytest.mark.parametrize("kind", LIVE_TYPES)
    def test_declined_consent_leaves_nothing_behind_and_a_later_yes_still_works(
            self, sync_trace, monkeypatch, dialogs, kind):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(LIVE_TARGETS[kind])
        if kind == "Email":
            monkeypatch.setattr(sync_trace, "_choose_email_sources", lambda target: ())
        dialogs["answer"] = QMessageBox.No
        sync_trace.live_research()

        assert seen == []
        assert sync_trace.worker is None
        assert sync_trace.host.calls == [c for c in sync_trace.host.calls if c[0] == "load"]
        assert sync_trace.status_label.text() == "Live Research cancelled before any lookup."
        assert sync_trace.live_btn.isEnabled() and sync_trace.exposure_btn.isEnabled()
        assert "Approved external sources" not in sync_trace.activity_box.toPlainText()

        dialogs["answer"] = QMessageBox.Yes
        monkeypatch.setattr(sync_trace, "_choose_email_sources", lambda target: ("emailrep",))
        sync_trace.live_research()
        assert len(seen) == 1

    @pytest.mark.parametrize("kind", LIVE_TYPES)
    def test_a_provider_error_is_shown_saves_nothing_and_allows_a_retry(
            self, sync_trace, monkeypatch, dialogs, kind):
        _patch_providers(monkeypatch, raises=RuntimeError("source exploded"))
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(LIVE_TARGETS[kind])
        sync_trace.live_research()

        assert sync_trace.status_label.text() == "Live Research error."
        assert "source exploded" in sync_trace.stream_box.toPlainText()
        assert "source exploded" in sync_trace.activity_box.toPlainText()
        assert sync_trace.host.of("external") == []
        assert sync_trace.live_btn.isEnabled() and sync_trace.exposure_btn.isEnabled()
        assert sync_trace.analyse_btn.isEnabled() and not sync_trace.stop_btn.isEnabled()

        _patch_providers(monkeypatch, results=_live_provider_results())
        sync_trace.live_research()
        assert sync_trace.status_label.text() == "Live Research complete."
        assert len(sync_trace.host.of("external")) == 1

    @pytest.mark.parametrize("kind", LIVE_TYPES)
    def test_stop_reaches_the_provider_as_should_stop_and_the_partial_is_saved_flagged(
            self, deferred_trace, monkeypatch, dialogs, kind):
        from providers import (company_lookup, domain_lookup, email_lookup,
                               username_lookup)
        module = {"Domain": domain_lookup, "IP Address": domain_lookup,
                  "Username": username_lookup, "Company": company_lookup,
                  "Email": email_lookup}[kind]
        observed = []

        def lookup(target, *args, should_stop=None, **kwargs):
            observed.append(should_stop())
            return {**LIVE_RESULTS[kind], "cancelled": bool(should_stop())}

        monkeypatch.setattr(module, "lookup", lookup)
        panel = deferred_trace
        panel.type_box.setCurrentText(kind)
        panel.target_input.setText(LIVE_TARGETS[kind])
        panel.live_research()
        worker = panel.worker
        assert worker.isRunning()

        panel.stop()
        assert panel.status_label.text().startswith("Stopping")
        assert not panel.live_btn.isEnabled()                   # locked until it reports
        worker.run()                                            # the source in flight returns

        assert observed == [True]
        assert panel.status_label.text() == "Stopped — partial results retained."
        assert panel.host.of("external")[-1][1]["cancelled"] is True
        assert panel.live_btn.isEnabled() and not panel.stop_btn.isEnabled()

    @pytest.mark.parametrize("kind", LIVE_TYPES)
    def test_persist_and_restart_reopens_the_result_with_type_and_target(
            self, qapp, monkeypatch, dialogs, tmp_path, kind):
        panel, host = _persisting_trace(qapp, monkeypatch, tmp_path)
        _patch_providers(monkeypatch, results=_live_provider_results())
        panel.type_box.setCurrentText(kind)
        panel.target_input.setText(LIVE_TARGETS[kind])
        panel.live_research()
        assert len(list(tmp_path.glob("*.json"))) == 1

        host2 = FakeHost()
        panel2 = _build_panel(qapp, monkeypatch, host2)
        panel2.type_box.setCurrentText("Phone")                  # something else
        window = _restarted_window(tmp_path, panel2)
        assert window.saved_search_list.count() == 1
        window.open_selected_search(window.saved_search_list.item(0))

        assert panel2.type_box.currentText() == kind
        assert panel2.target_input.text() == LIVE_RESULTS[kind]["query"]
        assert json.loads(panel2.sections._raw) == LIVE_RESULTS[kind]
        trail = panel2.activity_box.toPlainText()
        assert "stored live-source record" in trail
        assert "no external sources were queried" in trail
        assert panel2.status_label.text() == "Saved search loaded."
        assert host2.of("external") == [] and host2.of("authorize") == []
        assert len(list(tmp_path.glob("*.json"))) == 1
        marker = {"Domain": "Registrar-Zeta", "IP Address": "AS64500",
                  "Username": "GH-ETA", "Company": "LEI-THETA",
                  "Email": "SCORE-IOTA"}[kind]
        assert marker in panel2.sections._raw

    def test_a_stopped_live_run_is_logged_cancelled_and_reopens_as_partial(
            self, qapp, monkeypatch, dialogs, tmp_path):
        panel, host = _persisting_trace(qapp, monkeypatch, tmp_path, workers="deferred")
        from providers import domain_lookup
        monkeypatch.setattr(
            domain_lookup, "lookup",
            lambda target, *a, should_stop=None, **k: {
                **LIVE_RESULTS["Domain"], "cancelled": bool(should_stop())})
        panel.target_input.setText("acme.test")
        panel.type_box.setCurrentText("Domain")
        panel.live_research()
        panel.stop()
        panel.worker.run()

        assert host.run_logger.finished[-1]["status"] == "cancelled"
        panel2 = _build_panel(qapp, monkeypatch, FakeHost())
        window = _restarted_window(tmp_path, panel2)
        window.open_selected_search(window.saved_search_list.item(0))
        assert json.loads(panel2.sections._raw)["cancelled"] is True
        assert "displayed results are partial" in _labels(panel2)

    def test_a_result_with_no_source_contacted_is_shown_but_not_saved(
            self, sync_trace, monkeypatch, dialogs):
        empty = {"type": "domain", "query": "acme.test", "sources_contacted": [],
                 "sources_skipped": [], "error": "nothing ran"}
        _patch_providers(monkeypatch, results={"domain": empty})
        sync_trace.type_box.setCurrentText("Domain")
        sync_trace.target_input.setText("acme.test")
        sync_trace.live_research()
        assert sync_trace.status_label.text() == "Live Research complete."
        assert sync_trace.host.of("external") == []


# ═════════════════════════════════════════════════════════════════════════════
# 3. Exposure check
# ═════════════════════════════════════════════════════════════════════════════

class TestExposureCheck:

    @pytest.mark.parametrize("kind", list(EXPOSURE_TARGETS))
    def test_happy_path_runs_the_real_worker_with_the_approved_sources(
            self, sync_trace, monkeypatch, kind):
        seen = []
        _patch_providers(monkeypatch, results={"exposure": EXPOSURE_RESULTS[kind]}, seen=seen)
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(EXPOSURE_TARGETS[kind])
        sync_trace.exposure_check()

        assert len(seen) == 1
        assert seen[0][1] == EXPOSURE_TARGETS[kind] and seen[0][2] == (kind,)
        assert seen[0][3]["selected_sources"] == ("ransomware_live",)
        assert sync_trace.status_label.text() == "Live Research complete."
        assert "Exposure verdict" in _labels(sync_trace)
        saved = sync_trace.host.of("external")[-1][1]
        assert saved["query_type"] == "Exposure" and saved["target"] == EXPOSURE_TARGETS[kind]
        assert "Approved dark-web / leak sources: Ransomware.live" in (
            sync_trace.activity_box.toPlainText())

    @pytest.mark.parametrize("query_type,target", INVALID)
    def test_invalid_input_never_opens_the_source_picker(
            self, sync_trace, monkeypatch, dialogs, query_type, target):
        seen, picked = [], []
        _patch_providers(monkeypatch, results={"exposure": EXPOSURE_RESULTS["Domain"]},
                         seen=seen)
        monkeypatch.setattr(sync_trace, "_choose_exposure_sources",
                            lambda t, k: picked.append(t) or ("ahmia",))
        sync_trace.type_box.setCurrentText(query_type)
        sync_trace.target_input.setText(target)
        sync_trace.exposure_check()
        assert picked == [] and seen == [] and sync_trace.worker is None
        assert dialogs["warning"] or dialogs["information"]

    @pytest.mark.parametrize("query_type,target", [
        ("IP Address", "8.8.8.8"), ("Username", "researcher_1"),
        ("Person", "Jane Example"), ("Phone", "+353 1 234 5678"),
    ])
    def test_a_target_type_the_check_cannot_answer_is_explained_not_run(
            self, sync_trace, monkeypatch, dialogs, query_type, target):
        picked = []
        monkeypatch.setattr(sync_trace, "_choose_exposure_sources",
                            lambda t, k: picked.append(t) or ("ahmia",))
        sync_trace.type_box.setCurrentText(query_type)
        sync_trace.target_input.setText(target)
        sync_trace.exposure_check()
        assert picked == [] and sync_trace.worker is None
        assert dialogs["information"][-1][0] == "Target Type Not Available"

    @pytest.mark.parametrize("kind", list(EXPOSURE_TARGETS))
    def test_a_declined_picker_leaves_the_panel_clean_and_reusable(
            self, sync_trace, monkeypatch, dialogs, kind):
        seen = []
        _patch_providers(monkeypatch, results={"exposure": EXPOSURE_RESULTS[kind]}, seen=seen)
        monkeypatch.setattr(sync_trace, "_choose_exposure_sources", lambda t, k: ())
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(EXPOSURE_TARGETS[kind])
        sync_trace.exposure_check()

        assert seen == [] and sync_trace.worker is None
        assert sync_trace.host.of("external") == []
        assert "Consent recorded" not in sync_trace.activity_box.toPlainText()
        assert sync_trace.exposure_btn.isEnabled() and sync_trace.live_btn.isEnabled()

    @pytest.mark.parametrize("kind", list(EXPOSURE_TARGETS))
    def test_a_provider_error_is_reported_and_saves_nothing(
            self, sync_trace, monkeypatch, kind):
        _patch_providers(monkeypatch, raises=RuntimeError("leak index offline"))
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(EXPOSURE_TARGETS[kind])
        sync_trace.exposure_check()
        assert sync_trace.status_label.text() == "Live Research error."
        assert "leak index offline" in sync_trace.stream_box.toPlainText()
        assert sync_trace.host.of("external") == []
        assert sync_trace.exposure_btn.isEnabled()

    def test_stop_gives_an_incomplete_verdict_and_logs_the_run_cancelled(
            self, qapp, monkeypatch, dialogs, tmp_path):
        panel, host = _persisting_trace(qapp, monkeypatch, tmp_path, workers="deferred")
        from providers import exposure_lookup
        monkeypatch.setattr(
            exposure_lookup, "lookup",
            lambda target, kind, *, should_stop=None, **k: {
                **EXPOSURE_RESULTS["Domain"], "cancelled": bool(should_stop())})
        panel.target_input.setText("acme.test")
        panel.type_box.setCurrentText("Domain")
        panel.exposure_check()
        panel.stop()
        panel.worker.run()

        assert panel.status_label.text() == "Stopped — partial results retained."
        assert "Incomplete" in _labels(panel)
        assert host.run_logger.finished[-1]["status"] == "cancelled"

    @pytest.mark.parametrize("kind", list(EXPOSURE_TARGETS))
    def test_persist_and_restart_reopens_with_the_target_kind_as_query_type(
            self, qapp, monkeypatch, dialogs, tmp_path, kind):
        panel, host = _persisting_trace(qapp, monkeypatch, tmp_path)
        _patch_providers(monkeypatch, results={"exposure": EXPOSURE_RESULTS[kind]})
        panel.type_box.setCurrentText(kind)
        panel.target_input.setText(EXPOSURE_TARGETS[kind])
        panel.exposure_check()
        stored = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert stored["command"] == "Live Research · Exposure"

        host2 = FakeHost()
        panel2 = _build_panel(qapp, monkeypatch, host2)
        panel2.type_box.setCurrentText("Phone")
        window = _restarted_window(tmp_path, panel2)
        window.open_selected_search(window.saved_search_list.item(0))

        assert panel2.type_box.currentText() == kind
        assert panel2.target_input.text() == EXPOSURE_TARGETS[kind]
        assert "Exposure verdict" in _labels(panel2)
        assert "no external sources were queried" in panel2.activity_box.toPlainText()
        assert host2.of("external") == [] and len(list(tmp_path.glob("*.json"))) == 1

    def test_the_saved_title_names_the_target_and_the_exposure_kind(
            self, qapp, monkeypatch, dialogs, tmp_path):
        panel, host = _persisting_trace(qapp, monkeypatch, tmp_path)
        _patch_providers(monkeypatch, results={"exposure": EXPOSURE_RESULTS["Domain"]})
        panel.type_box.setCurrentText("Domain")
        panel.target_input.setText("acme.test")
        panel.exposure_check()
        window = _restarted_window(tmp_path, _build_panel(qapp, monkeypatch, FakeHost()))
        assert window.saved_search_list.item(0).text() == (
            "acme.test · Live Research · Exposure")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Stop, across workflows
# ═════════════════════════════════════════════════════════════════════════════

class TestStop:

    def test_stop_during_a_live_lookup_does_not_hand_back_the_controls_early(self, trace, dialogs):
        trace.type_box.setCurrentText("Domain")
        trace.live_research()
        worker = FakeLookupWorker.instances[-1]
        trace.stop()
        worker.running = True                                   # still finishing
        assert not trace.live_btn.isEnabled() and not trace.exposure_btn.isEnabled()
        assert not trace.stop_btn.isEnabled()
        assert "Stop requested" in trace.activity_box.toPlainText()
        assert trace.host.of("abandon") == []                   # lookups are never billed

    def test_stop_with_no_run_neither_cancels_nor_bills_anything(self, trace):
        assert trace.stop_worker() is False
        trace.stop()
        assert trace.host.of("record") == [] and trace.host.of("usage") == []
        assert FakeWorker.instances == [] and all_fake_lookup_workers() == []

    def test_a_lookup_that_raises_after_stop_still_hands_the_controls_back(
            self, deferred_trace, monkeypatch):
        from providers import domain_lookup

        def lookup(target, *a, should_stop=None, **k):
            raise RuntimeError("connection reset by peer")

        monkeypatch.setattr(domain_lookup, "lookup", lookup)
        panel = deferred_trace
        panel.type_box.setCurrentText("Domain")
        panel.target_input.setText("acme.test")
        panel.live_research()
        panel.stop()
        panel.worker.run()
        assert panel.live_btn.isEnabled()


# ═════════════════════════════════════════════════════════════════════════════
# CourtListener consent checkbox and "treated as" / type hints
# ═════════════════════════════════════════════════════════════════════════════

class TestCourtListenerConsent:
    def _run(self, sync_trace, monkeypatch, dialogs, tick):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText("Company")
        sync_trace.target_input.setText("Acme Holdings")
        sync_trace.courtlistener_box.setChecked(tick)
        sync_trace.live_research()
        return seen

    def test_the_box_exists_and_is_off_by_default(self, sync_trace):
        assert sync_trace.courtlistener_box.isChecked() is False

    def test_unticked_is_neither_listed_nor_contacted(self, sync_trace, monkeypatch, dialogs):
        seen = self._run(sync_trace, monkeypatch, dialogs, tick=False)
        (_, text), = dialogs["question"]
        assert "CourtListener" not in text and "GLEIF" in text
        assert len(seen) == 1 and seen[0][3]["court_records"] is False
        assert "CourtListener" not in sync_trace.activity_box.toPlainText()

    def test_ticked_is_listed_and_contacted(self, sync_trace, monkeypatch, dialogs):
        seen = self._run(sync_trace, monkeypatch, dialogs, tick=True)
        (_, text), = dialogs["question"]
        assert "CourtListener" in text
        assert seen[0][3]["court_records"] is True
        assert "CourtListener" in sync_trace.activity_box.toPlainText()

    def test_declining_with_the_box_ticked_contacts_nothing(self, sync_trace, monkeypatch, dialogs):
        dialogs["answer"] = QMessageBox.No
        seen = self._run(sync_trace, monkeypatch, dialogs, tick=True)
        assert seen == []

    def test_ticked_with_sanctions_key_lists_all_three(self, sync_trace, monkeypatch, dialogs):
        from providers import company_lookup
        monkeypatch.setattr(company_lookup, "opensanctions_key", lambda: "k")
        seen = self._run(sync_trace, monkeypatch, dialogs, tick=True)
        (_, text), = dialogs["question"]
        assert "GLEIF Legal Entity Index, OpenSanctions and CourtListener" in text
        assert seen[0][3]["sanctions"] is True and seen[0][3]["court_records"] is True

    def test_the_box_does_not_leak_into_other_types(self, sync_trace, monkeypatch, dialogs):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.courtlistener_box.setChecked(True)
        sync_trace.type_box.setCurrentText("Domain")
        sync_trace.target_input.setText("acme.test")
        sync_trace.live_research()
        (_, text), = dialogs["question"]
        assert "CourtListener" not in text


class TestTreatedAsAndHints:
    def _consent_text(self, sync_trace, monkeypatch, dialogs, kind, target):
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=[])
        sync_trace.type_box.setCurrentText(kind)
        sync_trace.target_input.setText(target)
        sync_trace.live_research()
        return dialogs["question"][-1][1]

    def test_consent_states_the_resolved_type(self, sync_trace, monkeypatch, dialogs):
        text = self._consent_text(sync_trace, monkeypatch, dialogs, "Auto-detect", "acme.test")
        assert "Treated as: Domain" in text and "Hint" not in text

    def test_dotted_name_is_hinted_but_still_treated_as_chosen(
            self, sync_trace, monkeypatch, dialogs):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText("Auto-detect")
        sync_trace.target_input.setText("john.smith")
        sync_trace.live_research()
        text = dialogs["question"][-1][1]
        assert "Treated as: Domain" in text and "use Username or Person" in text
        assert sync_trace.type_box.currentText() == "Auto-detect"   # selection untouched
        assert seen and seen[0][0] == "domain"                      # not re-routed

    def test_crypto_address_typed_as_username_is_hinted_not_rerouted(
            self, sync_trace, monkeypatch, dialogs):
        seen = []
        _patch_providers(monkeypatch, results=_live_provider_results(), seen=seen)
        sync_trace.type_box.setCurrentText("Username")
        sync_trace.target_input.setText("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")
        sync_trace.live_research()
        text = dialogs["question"][-1][1]
        assert "Treated as: Username" in text and "Ethereum address" in text
        assert sync_trace.type_box.currentText() == "Username"
        assert seen and seen[0][0] == "username"

    def test_domain_typed_as_username_suggests_domain(self, sync_trace, monkeypatch, dialogs):
        text = self._consent_text(sync_trace, monkeypatch, dialogs, "Username", "example.com")
        assert "Treated as: Username" in text and "use Domain" in text

    def test_ordinary_username_gets_no_hint(self, sync_trace, monkeypatch, dialogs):
        text = self._consent_text(sync_trace, monkeypatch, dialogs, "Username", "alice_dev")
        assert "Treated as: Username" in text and "Hint" not in text

    def test_panel_hint_label_follows_the_input_and_never_edits_the_type(self, sync_trace):
        sync_trace.type_box.setCurrentText("Username")
        sync_trace.target_input.setText("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        assert not sync_trace.type_hint_label.isHidden()
        assert "Bitcoin address" in sync_trace.type_hint_label.text()
        assert sync_trace.type_box.currentText() == "Username"
        sync_trace.target_input.setText("alice_dev")
        assert sync_trace.type_hint_label.isHidden()

    def test_email_picker_states_the_type(self, trace, monkeypatch):
        from PySide6.QtWidgets import QDialog
        seen = {}
        monkeypatch.setattr(QDialog, "exec", lambda dialog: seen.update(
            labels=" ".join(l.text() for l in dialog.findChildren(QLabel))) or QDialog.Rejected)
        assert trace._choose_email_sources("analyst@acme.test") == ()
        assert "Treated as: Email" in seen["labels"]

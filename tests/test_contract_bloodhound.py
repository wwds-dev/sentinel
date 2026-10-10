"""Phase 3 workflow contracts for Bloodhound (osint_heavy).

One class per workflow -- Investigate (live collection), image EXIF, local file
search, remote SFTP search, Stop -- and, where they apply, five cases each:
positive, invalid input, denied consent, cancel / provider error, and
persist-and-restart. Everything is offline: models answer with small synthetic
strings, public sources and SSH are fakes, and any socket use fails the test.

Existing cover is not repeated (see TestBloodhoundPanel in test_ui_panels.py,
test_osint_heavy_collection.py, test_local_file_search.py and
test_remote_file_search.py); these tests target what those leave open.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox


# ─────────────────────────────────────────────────────────────────────────────
# Offline guard + fakes (copied from test_ui_panels.py, not imported)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    attempts = []

    def refuse_connect(self, *a, **k):
        attempts.append(("connect", a))
        raise AssertionError("socket.connect called in an offline test")

    def refuse_resolve(*a, **k):
        attempts.append(("getaddrinfo", a))
        raise AssertionError("getaddrinfo called in an offline test")

    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    monkeypatch.setattr(socket, "getaddrinfo", refuse_resolve)
    yield
    assert attempts == []


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class FakeHost:
    """The whole surface a panel is allowed to touch."""

    def __init__(self, models=("m1", "m2")):
        self.models = list(models)
        self.calls = []
        self.agent_instances = {}
        self.authorized = True

    def load_models_into(self, provider_box, model_box, context,
                         empty_placeholder=False):
        model_box.clear()
        model_box.addItems([f"{provider_box.currentText()}-{m}" for m in self.models])

    def register_model_loader(self, agent_key, loader):
        pass

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

    def count(self, kind):
        return len([c for c in self.calls if c[0] == kind])


class FakeWorker(QObject):
    """A ChatWorker with the thread taken out."""

    token_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    usage_signal = Signal(dict)
    status_signal = Signal(str)
    instances = []

    def __init__(self, run_backend, provider, model, messages, prompt):
        super().__init__()
        self.args = (provider, model, messages, prompt)
        self.cancelled = False
        self.running = True
        FakeWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True


class FakeCollectionWorker(QObject):
    """Bloodhound's live-collection worker with the thread taken out."""

    progress_signal = Signal(str)
    finished_signal = Signal(list)
    error_signal = Signal(str)
    instances = []

    def __init__(self, collect, target, target_type, scope, options=None):
        super().__init__()
        self.args = (collect, target, target_type, scope)
        self.options = dict(options or {})
        self.cancelled = False
        self.running = True
        FakeCollectionWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True


class FakeAgent:
    """Records the whole brief, including the live results the panel hands over."""

    def __init__(self, live=True):
        self.calls = []
        self.collect_calls = []
        self.last_source_count = 3
        if live:
            self.collect_live = self._collect_live
            self.planned_sources = lambda t, tt, s: ["WHOIS", "crt.sh"]

    def _collect_live(self, target, target_type, scope, on_progress=None,
                      should_stop=None):
        self.collect_calls.append((target, target_type, scope))
        return []

    def build_messages(self, target, target_type, scope, objective,
                       image_metadata, live_results=None):
        self.calls.append({
            "target": target, "type": target_type, "scope": scope,
            "objective": objective, "image": image_metadata,
            "live": live_results,
        })
        return [{"role": "user", "content": f"{target}|{image_metadata}"}]


DOSSIER = (
    "## 1. OVERVIEW\nwho they are\n"
    "## 2. DIGITAL FOOTPRINT\naccounts\n"
    "## 3. INFRASTRUCTURE / SOCIAL\nhosts\n"
    "## 4. RISK & RED FLAGS\nTHREAT LEVEL: 7/10  CONFIDENCE: 82%  SOURCES REFERENCED: 14\n"
    "## 5. METHODOLOGY\nhow it was found"
)


class Dialogs:
    """Every modal the panel can raise, captured instead of shown."""

    def __init__(self):
        self.warnings, self.criticals, self.questions = [], [], []
        self.answer = QMessageBox.Yes


@pytest.fixture
def dialogs(monkeypatch):
    d = Dialogs()
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda parent, title, text, *a, **k: d.warnings.append((title, text))))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(
        lambda parent, title, text, *a, **k: d.criticals.append((title, text))))

    def question(parent, title, text, buttons=None, default=None):
        d.questions.append((title, text, default))
        return d.answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return d


def make_panel(qapp, monkeypatch, *, agent=None):
    from ui.panels.osint_heavy import OsintHeavyPanel
    monkeypatch.setattr(OsintHeavyPanel, "worker_class", FakeWorker)
    monkeypatch.setattr(OsintHeavyPanel, "collection_worker_class", FakeCollectionWorker)
    FakeWorker.instances.clear()
    FakeCollectionWorker.instances.clear()
    host = FakeHost()
    host.agent_instances["osint_heavy"] = agent if agent is not None else FakeAgent()
    panel = OsintHeavyPanel(host)
    panel.target_input.setText("acme.com")
    # File Discovery ships collapsed, and an unchecked group disables its children.
    panel.findChild(QObject, "BloodhoundFileDiscoveryBox").setChecked(True)
    return panel


@pytest.fixture
def hound(qapp, monkeypatch, dialogs):
    return make_panel(qapp, monkeypatch)


@pytest.fixture
def plain_hound(qapp, monkeypatch, dialogs):
    """An agent with no live collection: straight to the model."""
    return make_panel(qapp, monkeypatch, agent=FakeAgent(live=False))


def wait_until(condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        QTest.qWait(10)
    QApplication.processEvents()
    return condition()


def wait_for_search(panel):
    worker = panel._file_search_worker
    assert wait_until(lambda: not worker.isRunning()), "file search never finished"
    worker.wait(2000)
    QApplication.processEvents()


def table_names(panel):
    return sorted(panel.file_results.item(r, 0).text()
                  for r in range(panel.file_results.rowCount()))


def make_jpeg(path, *, gps=True):
    from PIL import Image
    exif = Image.Exif()
    exif[0x010F] = "AcmeCam"
    exif[0x0110] = "X100"
    exif.get_ifd(0x8769)[0x9003] = "2026:01:02 03:04:05"
    if gps:
        ifd = exif.get_ifd(0x8825)
        ifd[1], ifd[2] = "N", (51.0, 30.0, 0.0)
        ifd[3], ifd[4] = "W", (0.0, 7.0, 30.0)
        exif[0x8825] = ifd
    Image.new("RGB", (8, 8)).save(path, exif=exif)
    return str(path)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Investigate -- dossier with live collection
# ─────────────────────────────────────────────────────────────────────────────

class TestInvestigateContract:

    def test_positive_live_results_reach_the_model_and_the_dossier_is_recorded(self, hound):
        agent = hound.host.agent_instances["osint_heavy"]
        hound.investigate()
        live = [{"type": "domain", "sources_contacted": [{"source": "WHOIS"}]}]
        FakeCollectionWorker.instances[-1].finished_signal.emit(live)
        assert agent.calls[-1]["live"] == live           # the collected JSON is what the model sees
        assert len(FakeWorker.instances) == 1
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER)
        records = [c for c in hound.host.calls if c[0] == "record"]
        assert [r[2] for r in records] == [DOSSIER]
        assert hound.status_label.text() == "Investigation complete."
        assert hound.save_btn.isEnabled() and hound.investigate_btn.isEnabled()
        assert hound.stop_btn.isEnabled() is False
        # The gauge shows the real contact count, not the model's "14".
        assert hound.sources_label.text() == "3"
        assert "actually contacted" in hound.sources_label.toolTip()

    def test_positive_without_live_collection_the_gauge_says_ai_estimated(self, plain_hound):
        plain_hound.investigate()
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER)
        assert plain_hound.sources_label.text() == "14"
        assert "AI-estimated" in plain_hound.sources_label.toolTip()

    def test_the_real_count_of_one_run_never_leaks_into_the_next(self, hound):
        agent = hound.host.agent_instances["osint_heavy"]
        hound.investigate()
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER)
        assert hound.sources_label.text() == "3"
        del agent.collect_live                            # same panel, agent without collection
        hound.investigate()
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER)
        assert hound.sources_label.text() == "14"

    @pytest.mark.parametrize("target", ["", "   ", "\n\t "])
    def test_invalid_blank_targets_are_refused_before_anything_runs(self, hound, dialogs, target):
        hound.target_input.setText(target)
        hound.investigate()
        assert [t for t, _ in dialogs.warnings] == ["Missing Input"]
        assert hound.host.count("authorize") == 0
        assert dialogs.questions == []
        assert FakeCollectionWorker.instances == [] and FakeWorker.instances == []
        assert hound.investigate_btn.isEnabled() is True

    def test_invalid_no_model_selected_refuses_without_authorising(self, hound, dialogs):
        hound.model_box.clear()
        hound.investigate()
        assert [t for t, _ in dialogs.warnings] == ["No Model"]
        assert hound.host.count("authorize") == 0
        assert FakeCollectionWorker.instances == [] and FakeWorker.instances == []

    def test_invalid_target_is_trimmed_before_it_is_authorised_and_collected(self, hound):
        hound.target_input.setText("  acme.com \n")
        hound.investigate()
        assert [c for c in hound.host.calls if c[0] == "authorize"][0][4] == "acme.com"
        assert FakeCollectionWorker.instances[-1].args[1] == "acme.com"

    def test_denied_consent_names_sources_and_contacts_nothing(self, hound, dialogs):
        agent = hound.host.agent_instances["osint_heavy"]
        dialogs.answer = QMessageBox.No
        hound.scope_box.setCurrentText("Deep Dive")
        hound.investigate()
        (title, text, default), = dialogs.questions
        assert "WHOIS" in text and "crt.sh" in text and "acme.com" in text
        assert default == QMessageBox.No
        assert FakeCollectionWorker.instances == []       # zero collection worker
        assert FakeWorker.instances == []                 # zero model call
        assert agent.collect_calls == [] and agent.calls == []
        assert hound.host.count("run") == 0 and hound.host.count("record") == 0
        assert ("abandon", "osint_heavy", "cancelled") in hound.host.calls
        assert hound._collecting is False
        assert hound.investigate_btn.isEnabled() and not hound.stop_btn.isEnabled()
        assert hound.save_btn.isEnabled() is False

    def test_denied_consent_does_not_poison_the_next_attempt(self, hound, dialogs):
        dialogs.answer = QMessageBox.No
        hound.investigate()
        dialogs.answer = QMessageBox.Yes
        hound.investigate()
        assert len(FakeCollectionWorker.instances) == 1
        assert hound._collecting is True

    def test_denied_authorisation_asks_no_question_and_contacts_nothing(self, hound, dialogs):
        hound.host.authorized = False
        hound.investigate()
        assert dialogs.questions == []
        assert FakeCollectionWorker.instances == [] and FakeWorker.instances == []
        assert hound._collecting is False
        assert hound.investigate_btn.isEnabled() is True

    def test_provider_error_after_collection_closes_the_request_and_shows_the_error(self, hound):
        hound.investigate()
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        FakeWorker.instances[-1].error_signal.emit("provider down")
        assert ("abandon", "osint_heavy", "error") in hound.host.calls
        assert hound.host.count("record") == 0
        assert "[Error] provider down" in hound.stream_box.toPlainText()
        assert hound.status_label.text() == "Error."
        assert hound.save_btn.isEnabled() is False
        assert hound.investigate_btn.isEnabled() and not hound.stop_btn.isEnabled()

    def test_provider_error_midstream_discards_the_partial_dossier(self, hound):
        hound.investigate()
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        worker = FakeWorker.instances[-1]
        worker.token_signal.emit("## 1. OVERVIEW\npartial")
        worker.error_signal.emit("connection reset")
        assert hound.save_btn.isEnabled() is False
        assert hound.host.count("record") == 0
        assert hound.threat_label.text() == "—"

    def test_failed_collection_still_sends_an_empty_live_list_to_the_model(self, hound):
        agent = hound.host.agent_instances["osint_heavy"]
        hound.investigate()
        FakeCollectionWorker.instances[-1].error_signal.emit("sweep crashed")
        assert agent.calls[-1]["live"] == []
        assert len(FakeWorker.instances) == 1

    def test_a_collection_that_crashed_does_not_report_the_previous_runs_source_count(self, qapp, monkeypatch, dialogs):
        import agents.osint_heavy_agent as heavy
        agent = heavy.OsintHeavyAgent()
        panel = make_panel(qapp, monkeypatch, agent=agent)
        # First run: stubbed providers contact two sources.
        monkeypatch.setattr(heavy, "_run_providers", lambda *a, **k: [
            {"type": "domain", "sources_contacted": [{"source": "a"}, {"source": "b"}]}])
        agent.collect_live("acme.com", "Domain / IP", "Standard Investigation")
        assert agent.last_source_count == 2
        # Second run: the collector itself raises (the worker turns that into error_signal).
        def boom(*a, **k):
            raise RuntimeError("collector crashed")
        monkeypatch.setattr(heavy, "_run_providers", boom)
        panel.investigate()
        FakeCollectionWorker.instances[-1].error_signal.emit("collector crashed")
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER.replace("14", "9"))
        assert panel.sources_label.text() != "2"         # nothing was contacted this time

    def test_persist_and_restart_a_saved_dossier_reloads_into_a_fresh_panel(
            self, qapp, monkeypatch, dialogs, tmp_path):
        first = make_panel(qapp, monkeypatch)
        first.target_input.setText("Müller / Söhne 日本")
        first.investigate()
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        text = DOSSIER + "\nnote: Müller 日本 — café"
        FakeWorker.instances[-1].finished_signal.emit(text)
        offered = {}

        def save_dialog(parent, caption, directory, filters):
            offered["default"] = directory
            return str(tmp_path / "dossier.txt"), "Text files (*.txt)"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(save_dialog))
        first.save()
        assert "/" not in os.path.basename(offered["default"]).replace("osint_dossier_", "")
        assert (tmp_path / "dossier.txt").read_text(encoding="utf-8") == text
        assert "Saved to dossier.txt" in first.status_label.text()

        # "Restart": a brand-new panel reads the saved file back.
        second = make_panel(qapp, monkeypatch)
        saved = (tmp_path / "dossier.txt").read_text(encoding="utf-8")
        assert second.parse_sections(saved) == first.parse_sections(text)
        second._populate_sections(saved)
        second._update_indicators(saved)
        assert second.sections._raw == saved
        assert second.threat_label.text() == "7/10" and second.conf_label.text() == "82%"
        assert second.save_btn.isEnabled() is False        # nothing is "current" until a run

    def test_persist_a_cancelled_save_dialog_writes_nothing(self, hound, monkeypatch, tmp_path):
        hound.investigate()
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER)
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        before = hound.status_label.text()
        hound.save()
        assert list(tmp_path.iterdir()) == []
        assert hound.status_label.text() == before

    def test_persist_saving_with_no_dossier_never_opens_a_dialog(self, hound, monkeypatch):
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(
            lambda *a, **k: pytest.fail("save dialog opened with nothing to save")))
        hound.save()

    def test_persist_a_new_investigation_clears_the_previous_dossier_before_saving(
            self, hound, monkeypatch, tmp_path):
        hound.investigate()
        FakeCollectionWorker.instances[-1].finished_signal.emit([])
        FakeWorker.instances[-1].finished_signal.emit(DOSSIER)
        hound.investigate()                                # second run, still collecting
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(
            lambda *a, **k: pytest.fail("old dossier offered while a new run is pending")))
        assert hound.save_btn.isEnabled() is False
        assert hound.sections._raw == ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Image metadata (EXIF)
# ─────────────────────────────────────────────────────────────────────────────

class TestImageExifContract:

    def test_positive_opted_in_metadata_carries_device_time_and_gps_but_not_the_file_name(
            self, plain_hound, tmp_path):
        path = make_jpeg(tmp_path / "holiday_secret_name.jpg")
        plain_hound.set_image(path)
        plain_hound.send_exif_checkbox.setChecked(True)
        plain_hound.investigate()
        sent = plain_hound.host.agent_instances["osint_heavy"].calls[-1]["image"]
        assert "AcmeCam" in sent and "X100" in sent and "2026:01:02 03:04:05" in sent
        assert "GPS Coordinates: 51.5, -0.125" in sent
        assert "holiday_secret_name" not in sent
        assert "GPS: 51.5" in plain_hound.exif_display.toPlainText()
        assert "GPS Coordinates Extracted" in plain_hound.image_details.toHtml()

    def test_invalid_unreadable_images_degrade_to_a_note_not_an_exception(self, plain_hound, tmp_path):
        plain_hound.set_image(str(tmp_path / "does_not_exist.jpg"))
        assert plain_hound.exif_display.toPlainText() == "No EXIF data found in this image."
        corrupt = tmp_path / "corrupt.jpg"
        corrupt.write_bytes(b"\xff\xd8\xff\xe1garbage")
        plain_hound.set_image(str(corrupt))
        assert "No EXIF data found" in plain_hound.exif_display.toPlainText()
        plain_hound.send_exif_checkbox.setChecked(True)
        plain_hound.investigate()
        assert "No EXIF metadata could be extracted" in \
            plain_hound.host.agent_instances["osint_heavy"].calls[-1]["image"]

    def test_invalid_an_image_deleted_after_attaching_cannot_break_the_run(self, plain_hound, tmp_path):
        path = make_jpeg(tmp_path / "gone.jpg")
        plain_hound.set_image(path)
        plain_hound.send_exif_checkbox.setChecked(True)
        os.remove(path)
        plain_hound.investigate()
        assert len(FakeWorker.instances) == 1
        assert "No EXIF metadata could be extracted" in \
            plain_hound.host.agent_instances["osint_heavy"].calls[-1]["image"]

    def test_invalid_a_stripped_image_is_flagged_as_a_signal_in_the_details(self, plain_hound, tmp_path):
        from PIL import Image
        path = tmp_path / "stripped.jpg"
        Image.new("RGB", (4, 4)).save(path)
        plain_hound.set_image(str(path))
        assert "may have been stripped" in plain_hound.image_details.toHtml()

    def test_a_crafted_exif_value_or_file_name_cannot_inject_html_into_the_image_pane(
            self, plain_hound, tmp_path):
        """QA audit (Bloodhound) must-fix #12: the file name and EXIF text
        fields reach the image details pane as raw f-string text; a crafted
        value (e.g. an EXIF Model containing markup, or an attacker-chosen
        file name) must not inject a tag or forge a link there."""
        from PIL import Image
        payload = '<img src=x onerror=alert(1)><a href="https://evil.test">click</a>'
        exif = Image.Exif()
        exif[0x0110] = payload                       # Model
        path = tmp_path / '<img src=x onerror=alert(2)>evil.jpg'
        Image.new("RGB", (8, 8)).save(path, exif=exif)
        plain_hound.set_image(str(path))
        rendered = plain_hound.image_details.toHtml()
        assert "<img src=x onerror=" not in rendered
        assert '<a href="https://evil.test">' not in rendered
        assert "<img src=x onerror=alert(2)>" not in rendered   # from the file name

    def test_denied_consent_gps_never_reaches_the_assembled_prompt(self, qapp, monkeypatch, dialogs, tmp_path):
        from agents.osint_heavy_agent import OsintHeavyAgent
        agent = OsintHeavyAgent()
        agent.collect_live = None                          # offline; no public sources
        panel = make_panel(qapp, monkeypatch, agent=agent)
        panel.set_image(make_jpeg(tmp_path / "pic.jpg"))
        assert panel.send_exif_checkbox.isChecked() is False
        panel.investigate()
        _provider, _model, messages, prompt = FakeWorker.instances[-1].args
        everything = "\n".join(m["content"] for m in messages) + prompt
        for leak in ("51.5", "-0.125", "AcmeCam", "X100", "maps.google", "pic.jpg"):
            assert leak not in everything
        authorised = [c for c in panel.host.calls if c[0] == "authorize"][0][4]
        assert "51.5" not in authorised and "AcmeCam" not in authorised

    def test_denied_authorisation_with_opt_in_sends_no_metadata_anywhere(self, plain_hound, tmp_path):
        plain_hound.set_image(make_jpeg(tmp_path / "pic.jpg"))
        plain_hound.send_exif_checkbox.setChecked(True)
        plain_hound.host.authorized = False
        plain_hound.investigate()
        assert FakeWorker.instances == []
        assert plain_hound.host.agent_instances["osint_heavy"].calls == []
        assert plain_hound.host.count("run") == 0

    def test_denied_a_cleared_image_is_not_sent_even_if_the_box_stays_ticked(self, plain_hound, tmp_path):
        plain_hound.set_image(make_jpeg(tmp_path / "pic.jpg"))
        plain_hound.send_exif_checkbox.setChecked(True)
        plain_hound.clear_image_btn.click()
        assert plain_hound.exif_display.toPlainText() == ""
        plain_hound.investigate()
        assert plain_hound.host.agent_instances["osint_heavy"].calls[-1]["image"] == ""

    def test_provider_error_keeps_the_local_image_details_visible(self, plain_hound, tmp_path):
        plain_hound.set_image(make_jpeg(tmp_path / "pic.jpg"))
        plain_hound.send_exif_checkbox.setChecked(True)
        plain_hound.investigate()
        FakeWorker.instances[-1].error_signal.emit("provider down")
        assert "provider down" in plain_hound.stream_box.toPlainText()
        assert "51.5" not in plain_hound.stream_box.toPlainText()     # error text is not the metadata
        assert plain_hound.image_label.text() == "pic.jpg"
        assert "AcmeCam" in plain_hound.exif_display.toPlainText() or \
            "GPS" in plain_hound.exif_display.toPlainText()
        assert plain_hound.image_details.isHidden() is False

    def test_cancel_with_an_image_attached_keeps_the_image_for_a_retry(self, plain_hound, tmp_path):
        plain_hound.set_image(make_jpeg(tmp_path / "pic.jpg"))
        plain_hound.send_exif_checkbox.setChecked(True)
        plain_hound.investigate()
        plain_hound.stop()
        assert FakeWorker.instances[-1].cancelled is True
        plain_hound.investigate()
        assert "GPS Coordinates" in \
            plain_hound.host.agent_instances["osint_heavy"].calls[-1]["image"]

    def test_persist_and_restart_consent_and_image_do_not_survive_a_new_panel(
            self, qapp, monkeypatch, dialogs, tmp_path):
        path = make_jpeg(tmp_path / "pic.jpg")
        first = make_panel(qapp, monkeypatch, agent=FakeAgent(live=False))
        first.set_image(path)
        first.send_exif_checkbox.setChecked(True)
        summary = first.exif_display.toPlainText()
        second = make_panel(qapp, monkeypatch, agent=FakeAgent(live=False))
        assert second.send_exif_checkbox.isChecked() is False      # opt-in is per session
        assert second.image_label.text() == "No image selected"
        second.investigate()
        assert second.host.agent_instances["osint_heavy"].calls[-1]["image"] == ""
        # Re-attaching the same file reproduces the same local summary: extraction is deterministic.
        second.set_image(path)
        assert second.exif_display.toPlainText() == summary
        assert second.send_exif_checkbox.isChecked() is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Local file search
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def local_tree(tmp_path):
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "invoice.pdf").write_bytes(b"x" * 2048)
    (root / "sub" / "invoice-old.PDF").write_bytes(b"x" * 10)
    (root / "sub" / "notes.txt").write_text("hello")
    other = tmp_path / "private"
    other.mkdir()
    (other / "invoice-secret.pdf").write_text("do not touch")
    return SimpleNamespace(root=root, other=other, base=tmp_path)


def pick_folder(panel, monkeypatch, folder):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(folder)))
    panel.add_search_folder()


class TestLocalFileSearchContract:

    def test_positive_selected_folder_is_searched_with_filters_and_results_listed(
            self, hound, monkeypatch, local_tree):
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_name_filter.setText("invoice")
        hound.file_extension_filter.setText("pdf")
        hound.start_file_search()
        assert hound.file_search_btn.isEnabled() is False and hound.file_cancel_btn.isEnabled()
        wait_for_search(hound)
        assert table_names(hound) == ["invoice-old.PDF", "invoice.pdf"]
        assert hound.file_status.text().startswith("Search complete: 2 found")
        assert hound.file_search_btn.isEnabled() and not hound.file_cancel_btn.isEnabled()
        assert hound.add_folder_btn.isEnabled() and hound.remove_folder_btn.isEnabled()
        # A file search is nothing to do with a model.
        assert hound.host.calls == []

    def test_positive_size_filter_applies_in_megabytes(self, hound, monkeypatch, local_tree):
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_min_size.setText("0.001")                 # ~1 KB
        hound.start_file_search()
        wait_for_search(hound)
        assert table_names(hound) == ["invoice.pdf"]

    @pytest.mark.parametrize("min_text,max_text,fragment", [
        ("abc", "", "Minimum size must be a number"),
        ("", "-1", "Maximum size cannot be negative"),
        ("5", "1", "cannot be greater"),
    ])
    def test_invalid_size_filters_are_refused_before_a_worker_starts(
            self, hound, dialogs, monkeypatch, local_tree, min_text, max_text, fragment):
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_min_size.setText(min_text)
        hound.file_max_size.setText(max_text)
        hound.start_file_search()
        assert hound._file_search_worker is None
        (title, text), = dialogs.warnings
        assert title == "Check Search Filters" and fragment in text
        assert hound.file_search_btn.isEnabled() is True

    def test_invalid_a_start_date_after_the_end_date_is_refused(self, hound, dialogs, monkeypatch, local_tree):
        from PySide6.QtCore import QDate
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_after_enabled.setChecked(True)
        hound.file_before_enabled.setChecked(True)
        hound.file_after.setDate(QDate(2026, 6, 1))
        hound.file_before.setDate(QDate(2026, 1, 1))
        hound.start_file_search()
        assert hound._file_search_worker is None
        assert "start date cannot be after" in dialogs.warnings[0][1]

    def test_invalid_no_folder_chosen_means_no_search(self, hound, dialogs):
        hound.start_file_search()
        assert hound._file_search_worker is None
        assert dialogs.warnings[0][0] == "No Folders Selected"

    def test_denied_only_the_listed_folders_are_ever_searched(self, hound, monkeypatch, local_tree):
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_name_filter.setText("invoice")
        hound.start_file_search()
        wait_for_search(hound)
        assert "invoice-secret.pdf" not in table_names(hound)      # sibling folder not listed -> not searched
        assert hound.host.count("authorize") == 0 and hound.host.count("run") == 0

    def test_denied_removing_the_folder_withdraws_the_scope(self, hound, dialogs, monkeypatch, local_tree):
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_folders.setCurrentRow(0)
        hound.file_folders.item(0).setSelected(True)
        hound.remove_search_folders()
        assert hound.file_folders.count() == 0
        hound.start_file_search()
        assert hound._file_search_worker is None
        assert dialogs.warnings[0][0] == "No Folders Selected"

    def test_denied_a_duplicate_folder_is_listed_once(self, hound, monkeypatch, local_tree):
        pick_folder(hound, monkeypatch, local_tree.root)
        pick_folder(hound, monkeypatch, local_tree.root / ".." / "docs")
        assert hound.file_folders.count() == 1

    def test_cancel_stops_a_running_search_and_reports_partial_results(self, hound, monkeypatch, local_tree):
        import ui.workers as workers
        from services.local_file_search import FileMatch, FileSearchReport
        started = threading.Event()

        def slow(roots, filters, *, should_cancel, on_progress):
            started.set()
            deadline = time.time() + 5
            while not should_cancel() and time.time() < deadline:
                time.sleep(0.005)
            return FileSearchReport(
                matches=[FileMatch("a.txt", "/x/a.txt", ".txt", 1, datetime(2026, 1, 1))],
                entries_checked=7, cancelled=True)
        monkeypatch.setattr(workers, "search_files", slow)
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.start_file_search()
        assert started.wait(2)
        hound.cancel_file_search()
        assert hound.file_status.text() == "Stopping…" and not hound.file_cancel_btn.isEnabled()
        wait_for_search(hound)
        assert hound.file_status.text().startswith("Search cancelled: 1 found; 7 items checked")
        assert hound.file_search_btn.isEnabled() is True

    def test_provider_error_a_crashing_search_raises_one_dialog_and_restores_the_panel(
            self, hound, dialogs, monkeypatch, local_tree):
        import ui.workers as workers

        def crash(*a, **k):
            raise OSError("disk unplugged")
        monkeypatch.setattr(workers, "search_files", crash)
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.start_file_search()
        wait_for_search(hound)
        assert dialogs.criticals == [("File Search Error", "disk unplugged")]
        assert hound.file_status.text() == "Search could not be completed."
        assert hound.file_search_btn.isEnabled() and not hound.file_cancel_btn.isEnabled()
        assert hound.file_results.rowCount() == 0

    def test_error_an_unreadable_folder_is_reported_but_the_other_folder_still_yields_results(
            self, hound, dialogs, monkeypatch, local_tree):
        pick_folder(hound, monkeypatch, local_tree.root)
        hound.file_folders.addItem(str(local_tree.base / "vanished"))
        hound.start_file_search()
        wait_for_search(hound)
        assert "invoice.pdf" in table_names(hound)
        (title, text), = dialogs.warnings
        assert title == "Some Locations Could Not Be Read" and "vanished" in text
        assert "1 location error(s)" in hound.file_status.text()

    def test_persist_and_restart_search_changes_nothing_and_a_new_panel_starts_empty(
            self, qapp, monkeypatch, dialogs, local_tree):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in local_tree.base.rglob("*") if p.is_file()}
        first = make_panel(qapp, monkeypatch)
        pick_folder(first, monkeypatch, local_tree.root)
        first.start_file_search()
        wait_for_search(first)
        assert first.file_results.rowCount() == 3
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in local_tree.base.rglob("*") if p.is_file()}
        assert after == before
        second = make_panel(qapp, monkeypatch)                # restart: no remembered scope or results
        assert second.file_folders.count() == 0
        assert second.file_results.rowCount() == 0
        assert second.file_status.text() == "Choose folders to begin."


# ─────────────────────────────────────────────────────────────────────────────
# 4. Remote SFTP file search
# ─────────────────────────────────────────────────────────────────────────────

import stat as statmod


def _entry(name, mode, size=10):
    return SimpleNamespace(filename=name, st_mode=mode, st_size=size, st_mtime=1_700_000_000)


class FakeSftp:
    def __init__(self, tree=None):
        self.tree = tree if tree is not None else {
            "/srv/archive": [
                _entry("sub", statmod.S_IFDIR | 0o755),
                _entry("report.pdf", statmod.S_IFREG | 0o644, 2048),
                _entry("alias", statmod.S_IFLNK | 0o777),
            ],
            "/srv/archive/sub": [_entry("notes.txt", statmod.S_IFREG | 0o644, 20)],
        }
        self.closed = False
        self.listed = []

    def listdir_attr(self, folder):
        self.listed.append(folder)
        if folder not in self.tree:
            raise PermissionError("denied")
        return self.tree[folder]

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, sftp=None):
        self.sftp = sftp or FakeSftp()
        self.closed = False

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed = True


@pytest.fixture
def remote(hound, monkeypatch):
    """The panel in Remote mode with valid-looking details and a fake SSH layer."""
    from services import remote_file_search
    state = SimpleNamespace(connects=[], client=FakeClient())

    def connect(host, username, port):
        state.connects.append((host, username, port))
        return state.client
    monkeypatch.setattr(remote_file_search, "_connect", connect)
    hound.file_source_box.setCurrentText("Remote SSH machine")
    hound.remote_host_input.setText("owned-host")
    hound.remote_user_input.setText("analyst")
    hound.remote_port_input.setText("22")
    hound.remote_roots_input.setText("/srv/archive")
    state.panel = hound
    return state


class TestRemoteFileSearchContract:

    def test_positive_results_are_remote_paths_and_the_session_is_closed(self, remote):
        panel = remote.panel
        panel.file_extension_filter.setText("pdf, txt")
        panel.start_file_search()
        assert panel.file_status.text() == "Searching remote host over SFTP…"
        wait_for_search(panel)
        assert table_names(panel) == ["notes.txt", "report.pdf"]
        assert panel.file_results.item(0, 1).text().startswith("/srv/archive")
        assert remote.connects == [("owned-host", "analyst", 22)]
        assert remote.client.closed and remote.client.sftp.closed
        assert panel.host.calls == []                        # no model, no guard involved
        assert panel.file_search_btn.isEnabled()

    def test_positive_roots_are_comma_separated_and_trimmed(self, remote):
        panel = remote.panel
        panel.remote_roots_input.setText(" /srv/archive/sub ,/srv/archive/sub, ")
        panel.start_file_search()
        wait_for_search(panel)
        assert table_names(panel) == ["notes.txt"]

    @pytest.mark.parametrize("field,value,fragment", [
        ("host", "", "valid SSH hostname"),
        ("host", "bad host", "valid SSH hostname"),
        ("user", "", "valid SSH username"),
        ("user", "bad user", "valid SSH username"),
        ("port", "0", "between 1 and 65535"),
        ("port", "70000", "between 1 and 65535"),
        ("port", "ssh", "invalid literal"),
        ("port", "", "invalid literal"),
        ("roots", "relative/path", "absolute path"),
        ("roots", "/ok, ../escape", "absolute path"),
    ])
    def test_invalid_ssh_details_are_refused_before_any_connection(self, remote, dialogs, field, value, fragment):
        panel = remote.panel
        {"host": panel.remote_host_input, "user": panel.remote_user_input,
         "port": panel.remote_port_input, "roots": panel.remote_roots_input}[field].setText(value)
        panel.start_file_search()
        assert remote.connects == [] and panel._file_search_worker is None
        (title, text), = dialogs.warnings
        assert title == "Check Search Filters" and fragment in text
        assert panel.file_search_btn.isEnabled() is True

    def test_invalid_no_remote_folder_is_its_own_message(self, remote, dialogs):
        remote.panel.remote_roots_input.setText(" , ")
        remote.panel.start_file_search()
        assert dialogs.warnings[0] == ("No Folders Selected", "Enter at least one absolute remote folder.")
        assert remote.connects == []

    def test_invalid_the_ssh_terminal_button_validates_before_opening_a_url(self, remote, dialogs, monkeypatch):
        from PySide6.QtGui import QDesktopServices
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url) or True))
        remote.panel.remote_port_input.setText("99999")
        remote.panel.open_ssh_terminal()
        assert opened == [] and dialogs.warnings[0][0] == "Check SSH Details"
        remote.panel.remote_port_input.setText("2222")
        remote.panel.open_ssh_terminal()
        assert opened[0].toString() == "ssh://analyst@owned-host:2222"

    def test_denied_an_unknown_host_key_surfaces_as_an_error_and_no_password_prompt(
            self, hound, dialogs, monkeypatch, tmp_path):
        import paramiko
        from services import remote_file_search
        seen = {}

        class Client:
            def load_system_host_keys(self):
                pass

            def set_missing_host_key_policy(self, policy):
                seen["policy"] = policy

            def connect(self, **kwargs):
                seen["kwargs"] = kwargs
                raise paramiko.SSHException("Server 'owned-host' not found in known_hosts")

        monkeypatch.setattr(paramiko, "SSHClient", Client)
        monkeypatch.setattr(remote_file_search.Path, "home", staticmethod(lambda: tmp_path))  # no ~/.ssh/config
        hound.file_source_box.setCurrentText("Remote SSH machine")
        hound.remote_host_input.setText("owned-host")
        hound.remote_user_input.setText("analyst")
        hound.remote_roots_input.setText("/srv/archive")
        hound.start_file_search()
        wait_for_search(hound)
        assert isinstance(seen["policy"], paramiko.RejectPolicy)
        assert seen["kwargs"]["password"] is None
        assert dialogs.criticals and "known_hosts" in dialogs.criticals[0][1]
        assert hound.file_results.rowCount() == 0
        assert hound.file_status.text() == "Search could not be completed."
        assert hound.file_search_btn.isEnabled() and hound.host.calls == []

    def test_denied_a_folder_the_account_cannot_list_is_reported_while_others_return(self, remote, dialogs):
        panel = remote.panel
        panel.remote_roots_input.setText("/root, /srv/archive")
        panel.start_file_search()
        wait_for_search(panel)
        assert "report.pdf" in table_names(panel)
        (title, text), = dialogs.warnings
        assert "Cannot access /root" in text

    def test_denied_only_metadata_is_listed_symlinks_are_never_followed(self, remote):
        remote.panel.start_file_search()
        wait_for_search(remote.panel)
        assert "alias" not in table_names(remote.panel)
        assert set(remote.client.sftp.listed) == {"/srv/archive", "/srv/archive/sub"}

    def test_cancel_stops_an_endless_remote_walk_and_closes_the_session(self, remote):
        panel = remote.panel
        started = threading.Event()

        class Endless(FakeSftp):
            def listdir_attr(self, folder):
                started.set()
                time.sleep(0.005)
                return [_entry("d", statmod.S_IFDIR | 0o755),
                        _entry("f.txt", statmod.S_IFREG | 0o644)]

        remote.client = FakeClient(Endless())
        panel.start_file_search()
        assert started.wait(2)
        panel.cancel_file_search()
        wait_for_search(panel)
        assert panel.file_status.text().startswith("Search cancelled")
        assert remote.client.closed and remote.client.sftp.closed
        assert panel.file_search_btn.isEnabled() is True

    def test_error_a_dropped_connection_raises_one_dialog_and_restores_the_panel(self, remote, dialogs, monkeypatch):
        from services import remote_file_search

        def refuse(host, username, port):
            raise TimeoutError("timed out")
        monkeypatch.setattr(remote_file_search, "_connect", refuse)
        remote.panel.start_file_search()
        wait_for_search(remote.panel)
        assert dialogs.criticals == [("File Search Error", "timed out")]
        assert remote.panel.file_search_btn.isEnabled()
        assert remote.panel.file_cancel_btn.isEnabled() is False

    def test_persist_and_restart_nothing_about_the_connection_is_remembered(self, remote, monkeypatch, tmp_path, qapp):
        remote.panel.start_file_search()
        wait_for_search(remote.panel)
        assert remote.panel.file_results.rowCount() == 2
        fresh = make_panel(qapp, monkeypatch)
        fresh.file_source_box.setCurrentText("Remote SSH machine")
        assert fresh.remote_host_input.text() == ""
        assert fresh.remote_user_input.text() == ""
        assert fresh.remote_roots_input.text() == ""
        assert fresh.remote_port_input.text() == "22"
        assert fresh.file_results.rowCount() == 0
        assert fresh._file_results_remote is False

    def test_persist_remote_results_reveal_by_copy_never_touches_the_local_disk(self, remote, dialogs):
        from PySide6.QtGui import QGuiApplication
        panel = remote.panel
        panel.start_file_search()
        wait_for_search(panel)
        row = [r for r in range(panel.file_results.rowCount())
               if panel.file_results.item(r, 0).text() == "report.pdf"][0]
        panel.reveal_file_result(row, 1)
        assert QGuiApplication.clipboard().text() == "/srv/archive/report.pdf"
        assert dialogs.warnings == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. Stop
# ─────────────────────────────────────────────────────────────────────────────

class TestStopContract:

    def test_positive_stop_during_the_model_call_cancels_it_and_restores_the_controls(self, plain_hound):
        plain_hound.investigate()
        worker = FakeWorker.instances[-1]
        assert plain_hound.stop_btn.isEnabled() and not plain_hound.investigate_btn.isEnabled()
        plain_hound.stop()
        assert worker.cancelled is True
        assert plain_hound.status_label.text() == "Stopped."
        assert plain_hound.investigate_btn.isEnabled() and not plain_hound.stop_btn.isEnabled()

    def test_positive_stop_mid_stream_does_not_offer_the_partial_dossier_for_saving(self, plain_hound):
        plain_hound.investigate()
        FakeWorker.instances[-1].token_signal.emit("## 1. OVERVIEW\npartial")
        plain_hound.stop()
        assert plain_hound.save_btn.isEnabled() is False
        assert plain_hound.host.count("record") == 0

    def test_invalid_stop_when_idle_is_a_harmless_no_op(self, hound):
        hound.stop()
        assert hound.host.count("abandon") == 0
        assert hound.investigate_btn.isEnabled() and not hound.stop_btn.isEnabled()
        assert FakeWorker.instances == [] and FakeCollectionWorker.instances == []
        assert hound.is_running() is False

    def test_invalid_stop_twice_abandons_the_request_only_once(self, hound):
        hound.investigate()
        hound.stop()
        hound.stop()
        assert hound.host.count("abandon") == 1

    def test_denied_consent_leaves_nothing_for_stop_to_cancel(self, hound, dialogs):
        dialogs.answer = QMessageBox.No
        hound.investigate()
        hound.stop()
        assert FakeCollectionWorker.instances == [] and FakeWorker.instances == []
        assert hound.host.count("abandon") == 1               # the denial's own, not a second one

    def test_cancel_stop_cancels_collection_and_a_file_search_together(self, hound):
        class Search:
            def __init__(self):
                self.cancelled = False

            def isRunning(self):
                return not self.cancelled

            def cancel(self):
                self.cancelled = True
        search = Search()
        hound._file_search_worker = search
        hound.investigate()
        collector = FakeCollectionWorker.instances[-1]
        assert hound.is_running() is True
        hound.stop()
        assert collector.cancelled and search.cancelled
        assert hound.host.calls.count(("abandon", "osint_heavy", "cancelled")) == 1
        collector.finished_signal.emit([])                    # the sweep winds down late
        assert FakeWorker.instances == []

    def test_cancel_stopping_only_a_file_search_does_not_abandon_a_request_that_never_existed(self, hound):
        class Search:
            cancelled = False

            def isRunning(self):
                return True

            def cancel(self):
                self.cancelled = True
        hound._file_search_worker = Search()
        hound.stop()
        assert hound._file_search_worker.cancelled is True
        assert hound.host.count("abandon") == 0

    def test_cancel_a_stopped_model_request_is_closed_with_the_guard(self, plain_hound):
        plain_hound.investigate()
        plain_hound.stop()
        assert [c for c in plain_hound.host.calls if c[0] == "abandon"] != []

    def test_cancel_the_workers_own_cancelled_error_does_not_overwrite_stopped(self, plain_hound):
        plain_hound.investigate()
        plain_hound.stop()
        # The real ChatWorker reports a streaming cancel as an error signal.
        FakeWorker.instances[-1].error_signal.emit("Request cancelled by user.")
        assert plain_hound.status_label.text() == "Stopped."
        assert "[Error]" not in plain_hound.stream_box.toPlainText()

    def test_persist_and_restart_a_stopped_run_leaves_no_state_for_the_next_panel(self, qapp, monkeypatch, dialogs):
        first = make_panel(qapp, monkeypatch)
        first.investigate()
        first.stop()
        second = make_panel(qapp, monkeypatch)
        assert second.is_running() is False and second._collecting is False
        assert second.sections._raw == "" and second.save_btn.isEnabled() is False
        assert second.threat_label.text() == "—" and second.sources_label.text() == "—"
        second.investigate()
        assert len(FakeCollectionWorker.instances) == 1       # a fresh sweep; nothing inherited
        assert second._collecting is True

    def test_persist_shutdown_abandons_the_open_request_and_joins_workers(self, hound):
        hound.investigate()
        collector = FakeCollectionWorker.instances[-1]
        hound.shutdown()
        assert collector.cancelled is True
        assert ("abandon", "osint_heavy", "cancelled") in hound.host.calls


class TestCourtListenerConsent:
    """CourtListener is opt-in, like Trace: off by default, listed and contacted
    only when the box is ticked."""

    def _run(self, panel, dialogs, tick):
        panel.target_input.setText("Acme Corporation")
        panel.type_box.setCurrentText("Organisation")
        panel.courtlistener_box.setChecked(tick)
        seen = {}
        agent = panel.agent()
        agent.planned_sources = lambda t, tt, s, **kw: (
            seen.setdefault("planned", kw) and ["GLEIF"]) or ["GLEIF"]
        panel.investigate()
        return seen

    def test_box_is_off_by_default(self, hound):
        assert hound.courtlistener_box.isChecked() is False

    def test_unticked_passes_no_court_option(self, hound, dialogs):
        self._run(hound, dialogs, False)
        worker = FakeCollectionWorker.instances[-1]
        assert worker.options == {}

    def test_ticked_passes_the_option_to_dialog_and_worker(self, hound, dialogs):
        seen = self._run(hound, dialogs, True)
        assert seen["planned"] == {"court_records": True}
        assert FakeCollectionWorker.instances[-1].options == {"court_records": True}

    def test_declining_with_the_box_ticked_collects_nothing(self, hound, dialogs):
        dialogs.answer = QMessageBox.No
        self._run(hound, dialogs, True)
        assert FakeCollectionWorker.instances == []

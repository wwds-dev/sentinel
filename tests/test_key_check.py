"""The OSINT Keys tab's key check: verdicts, costs, secrecy, and the buttons.

Offline throughout: requests is replaced by a fake, so no service is called.
"""
import json
import os

import pytest
import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from providers import key_check


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


@pytest.fixture
def calls(monkeypatch):
    class Rec(list):
        reply = Reply()

    rec = Rec()

    def fake(method, url, **kwargs):
        rec.append({"method": method, "url": url, **kwargs})
        reply = rec.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests, "request", fake)
    return rec


def test_every_key_field_on_the_tab_has_a_check():
    import main
    keyed = {row[0] for row in main.GodAI.OSINT_TOOLS if row[5]}
    assert keyed == set(key_check.CHECKS)


def test_an_empty_key_is_missing_and_calls_nothing(calls):
    assert key_check.check("shodan", "  ")["state"] == key_check.MISSING
    assert calls == []


def test_a_key_in_the_wrong_documented_format_is_caught_before_any_call(calls):
    """HIBP documents 32 hex characters; the operator's saved key was 40."""
    out = key_check.check("hibp", "0123456789abcdef0123456789abcdef01234567")
    assert out["state"] == key_check.MALFORMED
    assert "32 hexadecimal" in out["detail"] and "40 characters" in out["detail"]
    assert calls == []


def test_a_likely_format_is_only_a_hint_on_a_refused_key(calls):
    """VirusTotal's format comes from key scanners, not its docs: ask anyway."""
    calls.reply = Reply(401, {})
    out = key_check.check("virustotal", "0123456789abcdef0123456789abcdef0123")
    assert out["state"] == key_check.REJECTED and len(calls) == 1
    assert "64 hexadecimal" in out["detail"] and "36 characters" in out["detail"]
    calls.reply = Reply(200, {})
    assert "hexadecimal" not in key_check.check("virustotal", "x" * 36)["detail"]


def _good_key(tool_id):
    """A key in the service's documented format, so the call is made."""
    spec = key_check.CHECKS[tool_id]
    if spec.shape == key_check.UUID:
        return "0a1b2c3d-0a1b-0a1b-0a1b-0a1b2c3d4e5f"
    if spec.shape:
        return "a" * 32
    return "a" * 64


@pytest.mark.parametrize("tool_id", sorted(key_check.CHECKS))
def test_each_service_reads_ok_rejected_and_limited(calls, tool_id):
    key = _good_key(tool_id)
    calls.reply = Reply(200, {})
    assert key_check.check(tool_id, key)["state"] == key_check.OK, tool_id
    calls.reply = Reply(401, {})
    assert key_check.check(tool_id, key)["state"] == key_check.REJECTED, tool_id
    calls.reply = Reply(429, {})
    assert key_check.check(tool_id, key)["state"] == key_check.LIMITED, tool_id


@pytest.mark.parametrize("tool_id", sorted(key_check.CHECKS))
def test_the_key_never_appears_in_a_url_or_a_message(calls, tool_id):
    key = _good_key(tool_id).replace("a", "b")
    calls.reply = requests.exceptions.ConnectionError(f"failed for https://x/?key={key}")
    out = key_check.check(tool_id, key)
    assert out["state"] == key_check.UNREACHABLE
    assert key not in out["detail"]
    # Shodan takes the key only as a query parameter, and VirusTotal's free
    # quota endpoint takes it as the user id in the path.
    if tool_id not in ("shodan", "virustotal"):
        assert key not in calls[0]["url"]
        assert key not in json.dumps(calls[0].get("params") or {})


def test_free_checks_ask_about_the_account_not_a_target(calls):
    calls.reply = Reply(200, {"plan": "Membership", "query_credits": 100})
    out = key_check.check("shodan", "k" * 32)
    assert "/api-info" in calls[0]["url"]
    assert "Membership" in out["detail"] and "100 query credits" in out["detail"]
    for tool_id, spec in key_check.CHECKS.items():
        if not spec.cost:
            assert "8.8.8.8" not in spec.func.__code__.co_consts, tool_id


def test_service_specific_codes(calls):
    calls.reply = Reply(403, {})
    assert key_check.check("hunter", "h" * 40)["state"] == key_check.LIMITED
    assert key_check.check("leakcheck", "l" * 40)["state"] == key_check.LIMITED
    calls.reply = Reply(400, {"error": "Invalid X-API-Key"})
    assert key_check.check("leakcheck", "l" * 40)["state"] == key_check.REJECTED
    calls.reply = Reply(400, {"error": "Too short query (min 3 characters)"})
    assert key_check.check("leakcheck", "l" * 40)["state"] == key_check.OK
    calls.reply = Reply(422, {})
    assert key_check.check("abuseipdb", "a" * 80)["state"] == key_check.OK
    calls.reply = Reply(404, {})
    assert key_check.check("opensanctions", "o" * 32)["state"] == key_check.OK
    assert key_check.check("censys", "c" * 32)["state"] == key_check.LIMITED
    calls.reply = Reply(404, {"error": {"code": "NotFoundError"}})
    assert key_check.check("virustotal", "a" * 64)["state"] == key_check.REJECTED
    calls.reply = Reply(200, {"scope": "ip-address"})
    assert key_check.check("urlscan", "0a1b2c3d-0a1b-0a1b-0a1b-0a1b2c3d4e5f")["state"] \
        == key_check.REJECTED
    calls.reply = Reply(200, {"status": 401})
    assert key_check.check("criminalip", "c" * 40)["state"] == key_check.REJECTED


def test_domaintools_check_is_signed_with_a_username(calls):
    calls.reply = Reply(200, {"response": {"products": [{"id": "domain-profile"}]}})
    out = key_check.check("domaintools", "alice:s3cret")
    assert calls[0]["params"]["api_username"] == "alice"
    assert "s3cret" not in json.dumps(calls[0], default=str)
    assert "domain-profile" in out["detail"]


def test_fingerprint_is_one_way_and_stable():
    assert key_check.fingerprint("abc") == key_check.fingerprint(" abc ")
    assert "abc" not in key_check.fingerprint("abc")


# ── the tab ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def win():
    from PySide6.QtWidgets import QApplication
    import main

    QApplication.instance() or QApplication([])
    yield main.GodAI()


@pytest.fixture
def tab(win, monkeypatch):
    """The settings dialog, with key checks answered synchronously."""
    from PySide6.QtWidgets import QDialog
    from services.database import save_setting
    from ui import dialogs, workers

    save_setting("osint_key_checks", "{}")
    answers = {}
    asked = []

    class InlineWorker:
        def __init__(self, items):
            self.items = list(items)
            from PySide6.QtCore import QObject, Signal

            class Bus(QObject):
                result_signal = Signal(str, dict)
                finished = Signal()
            self._bus = Bus()
            self.result_signal = self._bus.result_signal
            self.finished = self._bus.finished

        def start(self):
            for tool_id, key in self.items:
                asked.append(tool_id)
                self.result_signal.emit(tool_id, answers.get(
                    tool_id, key_check._result(key_check.OK, "fine")))
            self.finished.emit()

        def cancel(self):
            pass

    monkeypatch.setattr(workers, "KeyCheckWorker", InlineWorker)
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    dialogs.show_settings(win)
    dialog = opened[0]
    dialog.answers, dialog.asked = answers, asked
    yield dialog
    dialog.reject()


def _row(dialog, tool_id):
    from PySide6.QtWidgets import QFrame, QLineEdit, QPushButton
    button = dialog.findChild(QPushButton, f"KeyCheck_{tool_id}")
    frame = button.parentWidget()
    edit = frame.findChild(QLineEdit)
    return edit, button


def test_a_row_check_shows_the_verdict_and_remembers_it(tab):
    from services.database import get_setting
    from ui import dialogs

    edit, button = _row(tab, "shodan")
    edit.setText("k" * 32)
    tab.answers["shodan"] = key_check._result(key_check.REJECTED, "Shodan refused this key.")
    button.click()
    assert button.text() == "✗ Rejected"
    assert "Shodan refused" in button.toolTip()

    stored = json.loads(get_setting("osint_key_checks"))["shodan"]
    assert stored["fp"] == key_check.fingerprint("k" * 32)
    assert "k" * 32 not in get_setting("osint_key_checks")   # never the key itself

    # Reopening shows the stored verdict while the key is unchanged, and
    # forgets it the moment the key differs.
    dialogs._show_check(button, "shodan", stored, "k" * 32)
    assert button.text() == "✗ Rejected"
    dialogs._show_check(button, "shodan", stored, "another key")
    assert button.text() == "Check"


def test_save_key_checks_the_key_straight_away(tab):
    from PySide6.QtWidgets import QPushButton

    edit, button = _row(tab, "hibp")
    edit.setText("h" * 32)
    save = next(b for b in button.parentWidget().findChildren(QPushButton)
                if b.text() == "Save Key")
    save.click()
    assert tab.asked == ["hibp"] and button.text() == "✓ Works"
    # The write went to the suite's scratch file, never the real .env.
    from ui import dialogs
    assert "HIBP_API_KEY=" + "h" * 32 in dialogs._osint_env_path().read_text()


def test_check_all_runs_only_free_checks_and_names_the_rest(tab):
    from PySide6.QtWidgets import QLabel, QPushButton

    _row(tab, "shodan")[0].setText("k" * 32)              # free
    _row(tab, "hibp")[0].setText("h" * 32)                # free
    _row(tab, "dehashed")[0].setText("d" * 32)            # costs a credit
    tab.answers["hibp"] = key_check._result(key_check.REJECTED, "no")
    next(b for b in tab.findChildren(QPushButton) if b.text() == "Check all keys").click()

    assert set(tab.asked) == {"shodan", "hibp"}
    note = tab.findChild(QLabel, "KeyCheckAllNote").text()
    assert "Needs attention: HaveIBeenPwned." in note
    assert "DeHashed" in note and "spends a lookup or credit" in note
    assert _row(tab, "dehashed")[1].text() == "Check"

"""The OSINT Keys tab says which agent uses each key; hold that to the code.

A row that claims Trace uses a key nothing reads, or calls a key unused that a
provider does read, sends the user to pay for the wrong subscription. These
tests read the source, so the claim and the code have to change together.
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services import osint_keys
from services.agent_catalog import BUILTIN_AGENTS

ROOT = Path(__file__).resolve().parents[1]

# Where a key would be read: the providers, the agents, and the panels and
# workers that start them. Not the tab that lists the keys, nor the list itself.
READERS = [ROOT / "providers", ROOT / "agents", ROOT / "ui" / "panels"]
READER_FILES = [ROOT / "ui" / "workers.py"]


def _reader_source() -> str:
    files = list(READER_FILES)
    for folder in READERS:
        files += folder.rglob("*.py")
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


SOURCE = _reader_source()


def _tools():
    import main
    return main.GodAI.OSINT_TOOLS


def test_every_listed_service_is_explained_and_nothing_else():
    listed = {row[0] for row in _tools()}
    assert listed == set(osint_keys.OSINT_TOOL_INFO)


@pytest.mark.parametrize("row", _tools(), ids=lambda row: row[0])
def test_key_state_matches_the_code(row):
    tool_id, _, _, _, _, env_key = row
    info = osint_keys.OSINT_TOOL_INFO[tool_id]
    if not env_key:
        assert info["key"] == osint_keys.KEY_NONE
        return
    assert info["key"] != osint_keys.KEY_NONE, f"{tool_id} has a key field"
    read = env_key in SOURCE
    if info["key"] == osint_keys.KEY_UNREAD:
        assert not read, f"{env_key} is read now; say which agent uses it"
    else:
        assert read, f"{env_key} is marked {info['key']} but nothing reads it"


@pytest.mark.parametrize("tool_id", sorted(osint_keys.OSINT_TOOL_INFO))
def test_users_and_roles_are_real(tool_id):
    info = osint_keys.OSINT_TOOL_INFO[tool_id]
    for user, role in info["agents"].items():
        assert user in BUILTIN_AGENTS or user in osint_keys.USER_LABELS
        assert role in (osint_keys.ESSENTIAL, osint_keys.EXTRA)
    assert info["about"].strip()


def test_a_service_that_needs_a_key_is_never_essential_to_an_agent():
    """Essential means it runs by default, and nothing runs without its key."""
    for tool_id, info in osint_keys.OSINT_TOOL_INFO.items():
        if info["key"] != osint_keys.KEY_NEEDED:
            continue
        for user, role in info["agents"].items():
            if user in BUILTIN_AGENTS:
                assert role == osint_keys.EXTRA, f"{tool_id} for {user}"


def test_every_key_an_agent_reads_has_a_row():
    """OpenSanctions was read by Bloodhound and Trace with nowhere to enter it."""
    import re

    listed = {row[5] for row in _tools() if row[5]}
    read = set(re.findall(r'getenv\("([A-Z_]+_API_KEY)"', SOURCE))
    model_keys = {k for k in read if k.split("_")[0] in {
        "OPENAI", "ANTHROPIC", "GEMINI", "GOOGLE", "DEEPSEEK", "KIMI",
        "MOONSHOT", "QWEN", "DASHSCOPE"}}
    assert read - model_keys <= listed, read - model_keys - listed


def _vendor_word(display_name: str) -> str:
    """"Criminal IP" -> "criminalip", "URLScan.io" -> "urlscan"."""
    return display_name.lower().replace(".io", "").replace(" ", "")


def test_a_rows_hint_and_note_are_about_its_own_vendor():
    """The Criminal IP row carried the Censys placeholder and note, so its key
    field and hover told the user to paste a Censys token."""
    vendors = {row[0]: _vendor_word(row[1]) for row in _tools()}
    checked = 0
    for tool_id, info in osint_keys.OSINT_TOOL_INFO.items():
        text = " ".join(info.get(k, "") for k in ("key_hint", "note"))
        squashed = text.lower().replace(" ", "")
        if not squashed:
            continue
        named = {other for other, word in vendors.items() if word in squashed}
        assert not named - {tool_id}, f"{tool_id} text names {named - {tool_id}}"
        checked += 1
    assert checked >= 5
    # Only the Censys row says it takes a Censys token.
    assert "Censys" in osint_keys.OSINT_TOOL_INFO["censys"]["note"]
    assert osint_keys.OSINT_TOOL_INFO["censys"]["key_hint"] == "Platform personal access token"
    assert "key_hint" not in osint_keys.OSINT_TOOL_INFO["criminalip"]
    assert "Censys" not in osint_keys.explain("criminalip")
    assert "Censys" in osint_keys.explain("censys")


def test_explanation_names_agents_and_key():
    text = osint_keys.explain("ahmia")
    assert ".onion" in text and "Trace" in text and "Bloodhound" in text
    assert "No key needed" in text
    assert "Skipped until a key is saved" in osint_keys.explain("virustotal")


def test_a_row_with_no_agent_says_so(monkeypatch):
    monkeypatch.setitem(osint_keys.OSINT_TOOL_INFO, "placeholder", {
        "about": "x", "agents": {}, "key": osint_keys.KEY_UNREAD})
    assert "No agent uses" in osint_keys.explain("placeholder")


def test_every_key_on_the_tab_does_something():
    """The tab once listed twelve keys nothing read. A new row needs its code."""
    unread = [tool for tool, info in osint_keys.OSINT_TOOL_INFO.items()
              if info["key"] == osint_keys.KEY_UNREAD or not info["agents"]]
    assert unread == []



# ── Saved keys take effect without a restart ──────────────────────────

def _sent(monkeypatch, module, method):
    """Replace requests.<method> in a provider; return the headers it sent."""
    sent = []

    class Reply:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "search-1", "status": 1, "results": [], "count": 0}

    def fake(url, *a, **k):
        sent.append(k.get("headers") or {})
        return Reply()

    monkeypatch.setattr(getattr(module, "requests"), method, fake)
    return sent


@pytest.mark.parametrize("variable,call,header,expected", [
    ("HIBP_API_KEY", "email_lookup._hibp", "hibp-api-key", "{}"),
    ("INTELX_API_KEY", "exposure_lookup._intelx", "X-Key", "{}"),
    ("OPENSANCTIONS_API_KEY", "company_lookup._opensanctions", "Authorization", "ApiKey {}"),
    ("COURTLISTENER_API_KEY", "company_lookup._court_records", "Authorization", "Token {}"),
])
def test_a_key_saved_after_import_is_used(monkeypatch, variable, call, header, expected):
    """Save Key writes os.environ while the app runs. These four once read
    their key into a constant at import and kept the old one until restart."""
    import importlib

    module_name, function = call.split(".")
    module = importlib.import_module(f"providers.{module_name}")
    method = "post" if module_name == "exposure_lookup" else "get"
    sent = _sent(monkeypatch, module, method)
    if hasattr(module, "time"):  # IntelX polls between requests
        monkeypatch.setattr(module.time, "sleep", lambda *_: None)

    monkeypatch.setenv(variable, "saved-while-running")
    getattr(module, function)("example.com")
    assert sent and sent[0].get(header) == expected.format("saved-while-running")


def test_no_provider_reads_a_key_at_import():
    """A module-level `X = os.getenv("..._API_KEY")` is frozen at import, so a
    key saved in the OSINT Keys tab would not reach it until a restart."""
    import ast

    def reads_environment(value):
        return any(
            isinstance(n, (ast.Call, ast.Subscript))
            and ast.unparse(n.func if isinstance(n, ast.Call) else n.value)
            in {"os.getenv", "os.environ.get", "os.environ", "getenv"}
            for n in ast.walk(value))

    frozen = []
    for path in [*(ROOT / "providers").rglob("*.py"), *(ROOT / "ui" / "panels").rglob("*.py")]:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            value = getattr(node, "value", None)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and value is not None \
                    and reads_environment(value):
                frozen.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert frozen == []

# ── The tab ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def win():
    from PySide6.QtWidgets import QApplication
    import main

    QApplication.instance() or QApplication([])
    yield main.GodAI()


def _open(win, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from ui import dialogs

    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self) or 0)
    dialogs.show_settings(win)
    return opened[0]


def _name_label(dialog, text):
    from PySide6.QtWidgets import QLabel
    return next(l for l in dialog.findChildren(QLabel) if l.text() == text)


def _switch(dialog):
    from PySide6.QtWidgets import QCheckBox
    return next(c for c in dialog.findChildren(QCheckBox)
                if c.text() == "Explain on hover")


def test_hover_switch_turns_the_explanations_on_and_off(win, monkeypatch):
    from services.database import get_setting, save_setting

    save_setting("osint_hover_help", "1")
    dialog = _open(win, monkeypatch)
    ahmia = _name_label(dialog, "Ahmia")
    assert ".onion" in ahmia.toolTip()

    _switch(dialog).setChecked(False)
    assert ahmia.toolTip() == ""
    assert get_setting("osint_hover_help", "1") == "0"
    dialog.reject()

    dialog = _open(win, monkeypatch)
    assert not _switch(dialog).isChecked(), "the choice did not survive a reopen"
    assert _name_label(dialog, "Ahmia").toolTip() == ""
    _switch(dialog).setChecked(True)
    dialog.reject()


def test_rows_show_one_chip_per_agent(win, monkeypatch):
    from PySide6.QtWidgets import QLabel

    dialog = _open(win, monkeypatch)
    chips = {l.objectName() for l in dialog.findChildren(QLabel)
             if l.objectName().startswith("UsedBy_")}
    assert "UsedBy_osint_essential" in chips
    assert "UsedBy_osint_heavy_extra" in chips
    assert "UsedBy_vpn_extra" in chips
    assert "UsedBy_alias_button_essential" in chips
    dialog.reject()

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


def test_explanation_names_agents_and_key():
    text = osint_keys.explain("ahmia")
    assert ".onion" in text and "Trace" in text and "Bloodhound" in text
    assert "No key needed" in text
    assert "No agent uses" in osint_keys.explain("virustotal")


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

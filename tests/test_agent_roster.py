"""Consistency contract for Sentinel's security-focused agent roster.

These tests intentionally avoid importing ``main`` (and therefore PyQt).  The
UI roster is read from its syntax tree, while the database migration is tested
against an isolated temporary SQLite file.

Run with: pytest tests/test_agent_roster.py -v
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROSTER = (
    "chat",
    "osint",
    "osint_heavy",
    "wifi",
    "sentry",
    "bug_bounty",
    "vpn",
    "manager",
)
RETIRED_BUILTINS = {
    "writing",
    "coding",
    "router",
    "audiobook",
    "author",
    "manuscript",
    "music",
    "webdesign",
    "fiverr",
    "course",
    "roi",
    "investment",
    "nfl_bet",
    "health",
    "ops_identity",
}


def _literal_assignments(path: Path, variable: str) -> list[object]:
    """Return literal values assigned to *variable* anywhere in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == variable for target in targets):
            try:
                values.append(ast.literal_eval(node.value))
            except (TypeError, ValueError):
                pass
    return values


def test_json_rosters_match_the_canonical_roster():
    registry = json.loads((ROOT / "config" / "registry.json").read_text(encoding="utf-8"))

    assert not (ROOT / "config" / "agents.json").exists()
    assert registry.get("agents", []) == []
    assert registry.get("tools"), "tool policy seeds must remain available"


def test_catalog_is_the_complete_ui_roster():
    from services.agent_catalog import BUILTIN_AGENTS, BUILTIN_AGENT_ORDER

    assert tuple(BUILTIN_AGENT_ORDER) == CANONICAL_ROSTER
    assert set(BUILTIN_AGENTS) == set(CANONICAL_ROSTER)
    for metadata in BUILTIN_AGENTS.values():
        assert all(metadata.get(field) for field in ("label", "icon", "subtitle", "tooltip"))


def test_sidebar_derives_from_the_catalog():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "sidebar_agents = BUILTIN_AGENT_ORDER" in source
    assert "metadata = BUILTIN_AGENTS[name]" in source


def test_every_roster_entry_has_an_agent_implementation():
    # Chat/Trace/Bloodhound/Beacon/Forge each live under agents/<name>/ as
    # their own repo (package-ified as __init__.py). Bug Spray, Tunnel and
    # Sentry carry a package with their engine plus a sentinel_chat_agent.py,
    # so their in-app chat-agent class lives there instead of under agents/
    # — no separate agents/bug_bounty_agent.py or agents/vpn_agent.py.
    expected_paths = {
        "chat": ROOT / "agents" / "chat_agent" / "__init__.py",
        "manager": ROOT / "agents" / "manager_agent" / "__init__.py",
        "osint": ROOT / "agents" / "osint_agent" / "__init__.py",
        "osint_heavy": ROOT / "agents" / "osint_heavy_agent" / "__init__.py",
        "wifi": ROOT / "agents" / "wifi_agent" / "__init__.py",
        "sentry": ROOT / "agents" / "sentry" / "sentinel_chat_agent.py",
        "bug_bounty": ROOT / "agents" / "bug_spray" / "sentinel_chat_agent.py",
        "vpn": ROOT / "agents" / "vpn_agent" / "sentinel_chat_agent.py",
    }

    missing = [name for name, path in expected_paths.items() if not path.is_file()]
    assert not missing, f"roster entries without implementations: {missing}"


@pytest.mark.parametrize("module", ["writing_agent.py", "coding_agent.py", "router_agent.py"])
def test_retired_agent_wrappers_are_removed(module):
    assert not (ROOT / "agents" / module).exists()


@pytest.mark.parametrize("reserved_name", ["chat", "writing", "author"])
def test_forge_rejects_builtin_and_retired_names(tmp_path, reserved_name):
    from services.agent_factory import AgentFactory

    spec = {
        "name": reserved_name,
        "label": "Reserved",
        "description": "Must not be generated",
        "allowed_providers": ["ollama"],
        "allowed_tools": [],
        "budget_limit_eur": None,
        "requires_approval": True,
        "system_prompt": "Test",
    }
    valid, message = AgentFactory(tmp_path).validate_spec(spec)
    assert not valid
    assert "reserved" in message.lower()


def test_database_migration_retires_old_builtins_without_losing_history_or_dynamic_agents(
    tmp_path, monkeypatch
):
    from services import database

    base = tmp_path / "sentinel-data"
    db_path = base / "data" / "sentinel.db"
    db_path.parent.mkdir(parents=True)
    monkeypatch.setattr(database, "BASE_DIR", base)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    # An existing installation: legacy rows and user-created agents coexist,
    # and historical usage refers to a now-retired built-in.
    conn = database.get_connection()
    conn.executescript(database.SCHEMA)
    conn.execute("INSERT INTO agents (name, label) VALUES ('writing', 'Writing')")
    conn.execute("INSERT INTO agents (name, label) VALUES ('author', 'Manuscript')")
    conn.execute(
        "INSERT INTO agents (name, label, auto_generated) VALUES ('my_agent', 'My Agent', 1)"
    )
    conn.execute(
        "INSERT INTO usage (timestamp, agent, total_tokens) VALUES "
        "('2026-01-01T00:00:00Z', 'writing', 42)"
    )
    conn.commit()
    conn.close()

    database.init_db()

    conn = database.get_connection()
    names = {row["name"] for row in conn.execute("SELECT name FROM agents")}
    usage = conn.execute(
        "SELECT agent, total_tokens FROM usage WHERE agent = 'writing'"
    ).fetchone()
    conn.close()

    assert set(CANONICAL_ROSTER) <= names
    assert not names & RETIRED_BUILTINS
    assert "my_agent" in names
    assert tuple(usage) == ("writing", 42)


def test_fresh_database_omits_publishing_tables_but_legacy_data_survives(
    tmp_path, monkeypatch
):
    from services import database

    base = tmp_path / "sentinel-data"
    db_path = base / "data" / "sentinel.db"
    db_path.parent.mkdir(parents=True)
    monkeypatch.setattr(database, "BASE_DIR", base)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.init_db()
    conn = database.get_connection()
    fresh_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    conn.execute(
        "CREATE TABLE manuscript_todos (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO manuscript_todos (title) VALUES ('preserve me')")
    conn.commit()
    conn.close()

    assert not {name for name in fresh_tables if name.startswith("manuscript_")}

    database.init_db()
    conn = database.get_connection()
    title = conn.execute("SELECT title FROM manuscript_todos").fetchone()[0]
    conn.close()
    assert title == "preserve me"

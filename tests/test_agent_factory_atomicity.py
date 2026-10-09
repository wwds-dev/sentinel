"""Failure-path tests for all-or-nothing generated-agent creation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services import agent_factory as agent_factory_module
from services.agent_factory import AgentFactory


SPEC = {
    "name": "weather_brief",
    "label": "Weather Brief",
    "description": "Produces a compact weather brief.",
    "allowed_providers": ["ollama"],
    "allowed_tools": ["General Chat"],
    "budget_limit_eur": None,
    "requires_approval": False,
    "system_prompt": "Write a compact weather brief.",
}


@pytest.fixture
def factory(tmp_path, monkeypatch):
    (tmp_path / "agents").mkdir()
    (tmp_path / "config").mkdir()
    db_path = tmp_path / "sentinel.db"

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE agents (
            name TEXT PRIMARY KEY, label TEXT, enabled INTEGER, version TEXT,
            allowed_providers TEXT, allowed_tools TEXT, budget_limit_eur REAL,
            requires_approval INTEGER, description TEXT, log_path TEXT,
            auto_generated INTEGER
        );
        CREATE TABLE tools (
            name TEXT PRIMARY KEY, label TEXT, enabled INTEGER DEFAULT 1,
            version TEXT DEFAULT '1.0', allowed_providers TEXT DEFAULT '[]',
            budget_limit_eur REAL, requires_approval INTEGER DEFAULT 0,
            description TEXT DEFAULT '', system_prompt TEXT,
            recommended_provider TEXT, recommended_model TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO tools (name, label, enabled) VALUES ('General Chat', 'General Chat', 1)"
    )
    conn.commit()
    conn.close()

    def connect():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    # AgentFactory imports this function directly, so patch its module binding.
    monkeypatch.setattr(agent_factory_module, "get_connection", connect)
    return AgentFactory(tmp_path), db_path


def _rows(db_path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()


def test_registry_failure_rolls_back_the_generated_file(factory, monkeypatch):
    agent_factory, db_path = factory

    def fail_registry(_conn, _spec):
        raise sqlite3.OperationalError("registry unavailable")

    monkeypatch.setattr(agent_factory, "_update_registry", fail_registry)
    report = agent_factory.create_agent(dict(SPEC))

    assert report["success"] is False
    assert report["files_created"] == []
    assert not (agent_factory.agents_dir / "weather_brief_agent.py").exists()
    assert _rows(db_path, "agents") == []
    assert len(_rows(db_path, "tools")) == 1


def test_tool_failure_rolls_back_file_and_agent_registry_row(factory, monkeypatch):
    agent_factory, db_path = factory

    def fail_tool(_conn, _spec):
        raise sqlite3.OperationalError("tool registry unavailable")

    monkeypatch.setattr(agent_factory, "_update_tool_registry", fail_tool)
    report = agent_factory.create_agent(dict(SPEC))

    assert report["success"] is False
    assert report["files_created"] == []
    assert not (agent_factory.agents_dir / "weather_brief_agent.py").exists()
    assert _rows(db_path, "agents") == []
    assert len(_rows(db_path, "tools")) == 1


def test_success_reports_and_persists_every_created_component(factory):
    agent_factory, db_path = factory
    report = agent_factory.create_agent(dict(SPEC))

    assert report["success"] is True, report["errors"]
    assert (agent_factory.agents_dir / "weather_brief_agent.py").is_file()
    assert len(_rows(db_path, "agents")) == 1
    assert len(_rows(db_path, "tools")) == 2
    assert _rows(db_path, "agents")[0][2] == 0
    generated_tool = [row for row in _rows(db_path, "tools") if row[0] == "Weather Brief"][0]
    assert generated_tool[2] == 0
    assert len(report["files_created"]) == 3


def test_concurrent_destination_is_never_deleted(factory, monkeypatch):
    agent_factory, db_path = factory
    destination = agent_factory.agents_dir / "weather_brief_agent.py"

    def competing_link(_source, target):
        Path(target).write_text("created by another process", encoding="utf-8")
        raise FileExistsError("destination appeared concurrently")

    monkeypatch.setattr(agent_factory_module.os, "link", competing_link)
    report = agent_factory.create_agent(dict(SPEC))

    assert report["success"] is False
    assert destination.read_text(encoding="utf-8") == "created by another process"
    assert not list(agent_factory.agents_dir.glob(".*.tmp"))
    assert _rows(db_path, "agents") == []
    assert len(_rows(db_path, "tools")) == 1



def test_the_tool_row_carries_the_approved_limits(factory):
    """Enabling the tool later must not widen the spec: providers, budget and
    approval travel with it. They used to be dropped."""
    import json
    agent_factory, db_path = factory
    spec = {**SPEC, "allowed_providers": ["ollama"], "budget_limit_eur": 0.25,
            "requires_approval": True, "description": "Tiny helper."}
    report = agent_factory.create_agent(spec)
    assert report["success"], report["errors"]
    row = sqlite3.connect(db_path).execute(
        "SELECT allowed_providers, budget_limit_eur, requires_approval, description, enabled "
        "FROM tools WHERE name = ?", (spec["label"],)).fetchone()
    assert json.loads(row[0]) == ["ollama"]
    assert row[1] == 0.25 and row[2] == 1 and row[3] == "Tiny helper." and row[4] == 0

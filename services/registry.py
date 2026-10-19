import json
import re
from datetime import datetime
from services.database import get_connection


def _row_to_agent(row) -> dict:
    d = dict(row)
    d["allowed_providers"] = json.loads(d.get("allowed_providers") or "[]")
    raw_tools = d.get("allowed_tools")
    d["allowed_tools"] = json.loads(raw_tools) if raw_tools is not None else None
    d["enabled"] = bool(d.get("enabled", 1))
    d["requires_approval"] = bool(d.get("requires_approval", 0))
    d["auto_generated"] = bool(d.get("auto_generated", 0))
    return d


def _row_to_tool(row) -> dict:
    d = dict(row)
    d["allowed_providers"] = json.loads(d.get("allowed_providers") or "[]")
    d["enabled"] = bool(d.get("enabled", 1))
    d["requires_approval"] = bool(d.get("requires_approval", 0))
    return d


def _row_to_project(row) -> dict:
    d = dict(row)
    d["archived"] = bool(d.get("archived", 0))
    return d


def project_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64] or "project"


class Registry:
    def list_projects(self, include_archived: bool = False) -> list[dict]:
        query = "SELECT * FROM projects"
        params = ()
        if not include_archived:
            query += " WHERE archived = ?"
            params = (0,)
        query += " ORDER BY name COLLATE NOCASE, created_at"
        with get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_project(row) for row in rows]

    def get_project(self, project_id: str | None) -> dict | None:
        if not project_id:
            return None
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return _row_to_project(row) if row else None

    def upsert_project(self, project: dict) -> dict:
        name = str(project.get("name", "")).strip()
        if not name:
            raise ValueError("Project name is required.")
        project_id = str(project.get("id") or project_slug(name))
        existing = self.get_project(project_id)
        created_at = (
            existing.get("created_at") if existing
            else datetime.now().isoformat(timespec="seconds")
        )
        budget = project.get("budget_eur")
        budget = float(budget) if budget not in (None, "") else None
        values = (
            project_id, name, str(project.get("instructions", "")),
            str(project.get("default_agent") or "chat"),
            str(project.get("default_provider") or "ollama"),
            str(project.get("default_model") or ""), budget,
            1 if project.get("archived", False) else 0, created_at,
        )
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO projects
                  (id, name, instructions, default_agent, default_provider,
                   default_model, budget_eur, archived, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name,
                  instructions = excluded.instructions,
                  default_agent = excluded.default_agent,
                  default_provider = excluded.default_provider,
                  default_model = excluded.default_model,
                  budget_eur = excluded.budget_eur,
                  archived = excluded.archived
            """, values)
            conn.commit()
        return self.get_project(project_id)

    def archive_project(self, project_id: str, archived: bool = True) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE projects SET archived = ? WHERE id = ?",
                (1 if archived else 0, project_id),
            )
            conn.commit()
    def get_agent(self, name: str) -> dict | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE name = ?", (name,)
            ).fetchone()
        return _row_to_agent(row) if row else None

    def get_tool(self, name: str) -> dict | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tools WHERE name = ?", (name,)
            ).fetchone()
        return _row_to_tool(row) if row else None

    def list_agents(self) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return [_row_to_agent(r) for r in rows]

    def list_tools(self) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM tools ORDER BY name").fetchall()
        return [_row_to_tool(r) for r in rows]

    def is_agent_enabled(self, name: str) -> bool:
        a = self.get_agent(name)
        return bool(a and a.get("enabled", False))

    def is_tool_enabled(self, name: str) -> bool:
        t = self.get_tool(name)
        return bool(t and t.get("enabled", False))

    def agent_allows_provider(self, agent_name: str, provider: str) -> bool:
        a = self.get_agent(agent_name)
        if not a:
            return False
        allowed = a.get("allowed_providers", [])
        return not allowed or provider in allowed

    def tool_allows_provider(self, tool_name: str, provider: str) -> bool:
        t = self.get_tool(tool_name)
        if not t:
            return True
        allowed = t.get("allowed_providers", [])
        return not allowed or provider in allowed

    def permitted_providers(self, agent_name: str = "", tool_name: str = "") -> set[str] | None:
        """Providers this agent and tool both allow, or None when neither restricts.

        The router asks this before choosing, so it never picks a provider the
        Validator would reject afterwards. An unregistered agent or tool adds no
        restriction here (the Validator still refuses an unregistered agent).
        """
        permitted: set[str] | None = None
        for entry in (self.get_agent(agent_name) if agent_name else None,
                      self.get_tool(tool_name) if tool_name else None):
            allowed = (entry or {}).get("allowed_providers") or []
            if allowed:
                permitted = set(allowed) if permitted is None else permitted & set(allowed)
        return permitted

    def agent_allows_tool(self, agent_name: str, tool_name: str) -> bool:
        a = self.get_agent(agent_name)
        if not a:
            return False
        allowed_tools = a.get("allowed_tools")
        if allowed_tools is None:
            return True
        return tool_name in allowed_tools

    def get_agent_budget(self, agent_name: str) -> float | None:
        a = self.get_agent(agent_name)
        if not a:
            return None
        limit = a.get("budget_limit_eur")
        return float(limit) if limit is not None else None

    def get_tool_budget(self, tool_name: str) -> float | None:
        tool = self.get_tool(tool_name)
        if not tool:
            return None
        limit = tool.get("budget_limit_eur")
        return float(limit) if limit is not None else None

    def agent_requires_approval(self, agent_name: str) -> bool:
        a = self.get_agent(agent_name)
        return bool(a and a.get("requires_approval", False))

    def tool_requires_approval(self, tool_name: str) -> bool:
        tool = self.get_tool(tool_name)
        return bool(tool and tool.get("requires_approval", False))

import json
import math
import os
import re
import tempfile
from pathlib import Path
from services.database import get_connection
from services.agent_catalog import BUILTIN_AGENTS, RETIRED_BUILTIN_AGENTS
from services.provider_catalog import SUPPORTED_PROVIDERS


REQUIRED_SPEC_KEYS = {
    "name", "label", "description",
    "allowed_providers", "allowed_tools",
    "budget_limit_eur", "requires_approval",
    "system_prompt",
}

# Every value the model supplied is rendered with !r, so it arrives in the
# scaffold as a Python string literal whatever it contains. The docstring used
# to be interpolated raw, so a description holding \"\"\" or a trailing
# backslash produced a SyntaxError — or injected code — in a file that was
# then reported as created (D4). render_agent_source() also compiles the
# result before anything is written.
AGENT_TEMPLATE = '''\
class {class_name}Agent:
    {description!r}

    def __init__(self):
        self.name = {name!r}

    def build_messages(self, prompt: str) -> list:
        return [
            {{"role": "system", "content": {system_prompt!r}}},
            {{"role": "user", "content": prompt}},
        ]
'''


class AgentFactory:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.agents_dir = base_dir / "agents"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_spec(self, spec: dict) -> tuple[bool, str]:
        if not isinstance(spec, dict):
            return False, "Agent specification must be a JSON object."

        missing = REQUIRED_SPEC_KEYS - set(spec.keys())
        if missing:
            return False, f"Spec is missing required fields: {', '.join(sorted(missing))}"

        name = spec.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            return False, f"Agent name must be lowercase snake_case, got: {name!r}"

        if name in BUILTIN_AGENTS or name in RETIRED_BUILTIN_AGENTS:
            return False, f"Agent name '{name}' is reserved by Sentinel."

        label = spec.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80:
            return False, "Agent label must be a non-empty string of at most 80 characters."

        description = spec.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > 500:
            return False, "Agent description must be a non-empty string of at most 500 characters."

        system_prompt = spec.get("system_prompt")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            return False, "Agent system_prompt must be a non-empty string."
        if len(system_prompt) > 20_000:
            return False, "Agent system_prompt must be at most 20,000 characters."

        providers = spec.get("allowed_providers")
        if not isinstance(providers, list) or not providers:
            return False, "allowed_providers must be a non-empty list."
        if any(not isinstance(provider, str) for provider in providers):
            return False, "Every allowed provider must be a string."
        unknown_providers = sorted(set(providers) - set(SUPPORTED_PROVIDERS))
        if unknown_providers:
            return False, f"Unsupported providers: {', '.join(unknown_providers)}"
        if len(providers) != len(set(providers)):
            return False, "allowed_providers must not contain duplicates."

        tools = spec.get("allowed_tools")
        if not isinstance(tools, list) or any(not isinstance(tool, str) or not tool.strip() for tool in tools):
            return False, "allowed_tools must be a list of non-empty tool names."
        if len(tools) != len(set(tools)):
            return False, "allowed_tools must not contain duplicates."

        budget = spec.get("budget_limit_eur")
        if budget is not None and (
            isinstance(budget, bool) or not isinstance(budget, (int, float))
            or not math.isfinite(budget) or budget < 0
        ):
            return False, "budget_limit_eur must be null or a non-negative number."

        if not isinstance(spec.get("requires_approval"), bool):
            return False, "requires_approval must be true or false."

        agent_file = self.agents_dir / f"{name}_agent.py"
        if agent_file.exists():
            return False, f"Agent file already exists: {agent_file.name}"

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM agents WHERE lower(name) = lower(?) OR lower(label) = lower(?)",
                (name, label.strip()),
            ).fetchone()
            tool_row = conn.execute(
                "SELECT name FROM tools WHERE lower(name) = lower(?) OR lower(label) = lower(?)",
                (label.strip(), label.strip()),
            ).fetchone()
            if tools:
                placeholders = ",".join("?" for _ in tools)
                known_tools = {
                    item[0] for item in conn.execute(
                        f"SELECT name FROM tools WHERE name IN ({placeholders})", tuple(tools)
                    ).fetchall()
                }
            else:
                known_tools = set()
        if row:
            return False, f"Agent '{name}' already exists in registry."
        if tool_row:
            return False, f"Tool '{label.strip()}' already exists in registry."
        unknown_tools = sorted(set(tools) - known_tools)
        if unknown_tools:
            return False, f"Unknown tools: {', '.join(unknown_tools)}"

        return True, "OK"

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_agent(self, spec: dict) -> dict:
        """
        Creates all files for the approved spec.
        Returns a report dict with keys: success, files_created, errors.
        """
        report = {"success": False, "files_created": [], "errors": []}

        valid, msg = self.validate_spec(spec)
        if not valid:
            report["errors"].append(msg)
            return report

        name = spec["name"]
        class_name = "".join(part.capitalize() for part in name.split("_"))

        agent_file = self.agents_dir / f"{name}_agent.py"
        temp_file = None
        linked_by_us = False
        try:
            # The file and both registry rows are one logical operation. SQLite
            # rolls its transaction back on error; the generated file is then
            # removed so callers never see a half-created scaffold.
            temp_file = self._write_agent_temp_file(name, class_name, spec)
            with get_connection() as conn:
                self._update_registry(conn, spec)
                self._update_tool_registry(conn, spec)
                # Hard-link creation is exclusive: unlike os.replace it cannot
                # overwrite a module created between validation and commit.
                os.link(temp_file, agent_file)
                linked_by_us = True
                Path(temp_file).unlink()
                temp_file = None

            report["files_created"].append(str(agent_file.relative_to(self.base_dir)))
            report["files_created"].append("SQLite agent registry (updated)")
            report["files_created"].append("SQLite tool registry (updated)")
        except Exception as e:
            try:
                if temp_file is not None:
                    Path(temp_file).unlink(missing_ok=True)
                if linked_by_us:
                    agent_file.unlink(missing_ok=True)
            except Exception as cleanup_error:
                report["errors"].append(f"Failed to remove incomplete agent file: {cleanup_error}")
            report["errors"].append(f"Failed to create agent scaffold: {e}")

        report["success"] = len(report["errors"]) == 0
        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def render_agent_source(name: str, class_name: str, spec: dict) -> str:
        """The scaffold's source text, verified to compile before it is returned.

        Raises ValueError when the rendered module does not parse, so a hostile
        or merely unlucky spec can never be published as a broken file.
        """
        code = AGENT_TEMPLATE.format(
            class_name=class_name,
            description=str(spec.get("description", "")),
            name=name,
            system_prompt=str(spec.get("system_prompt", "You are a helpful assistant.")),
        )
        try:
            compile(code, f"<{name}_agent>", "exec")
        except SyntaxError as exc:
            raise ValueError(f"Generated scaffold does not compile: {exc.msg}") from exc
        return code

    def _write_agent_temp_file(self, name: str, class_name: str, spec: dict) -> Path:
        """Render beside the destination so the final replace is atomic."""
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        code = self.render_agent_source(name, class_name, spec)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.agents_dir,
            prefix=f".{name}_agent.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(code)
            return Path(handle.name)

    def _update_registry(self, conn, spec: dict) -> None:
        allowed_tools = spec.get("allowed_tools", ["General Chat"])
        conn.execute("""
            INSERT INTO agents
              (name, label, enabled, version, allowed_providers, allowed_tools,
               budget_limit_eur, requires_approval, description, log_path, auto_generated)
            VALUES (?,?,0,'1.0',?,?,?,?,?,'data/logs/runs.jsonl',1)
        """, (
            spec["name"],
            spec["label"].strip(),
            json.dumps(spec.get("allowed_providers", ["ollama"])),
            json.dumps(allowed_tools) if allowed_tools is not None else None,
            spec.get("budget_limit_eur"),
            1 if spec.get("requires_approval", False) else 0,
            spec.get("description", ""),
        ))

    def _update_tool_registry(self, conn, spec: dict) -> None:
        label = spec["label"].strip()
        providers = spec.get("allowed_providers", [])
        recommended_provider = providers[0] if providers else "ollama"

        conn.execute("""
            INSERT INTO tools (name, label, enabled, system_prompt, recommended_provider, recommended_model)
            VALUES (?,?,0,?,?,?)
        """, (label, label, spec.get("system_prompt", ""), recommended_provider, ""))

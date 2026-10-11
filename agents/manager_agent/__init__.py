import json
import re


MANAGER_SYSTEM_PROMPT = """\
You are a software architect assistant for Sentinel Fork — a PySide6 AI desktop app.
Your job is to convert a user's agent idea into a structured agent specification.

Always reply with a single valid JSON object. No explanation, no markdown fences, just raw JSON.

Schema (all fields required except `reasoning`):
{
  "name": "snake_case_name",
  "label": "Display Name (title case)",
  "description": "One sentence describing what this agent does.",
  "allowed_providers": ["ollama", "openai", "deepseek", "kimi", "gemini", "anthropic", "qwen"],
  "allowed_tools": ["General Chat"],
  "budget_limit_eur": null,
  "requires_approval": false,
  "system_prompt": "The system prompt that will be injected before every user message.",
  "reasoning": "Optional brief explanation of why you chose these settings."
}

Rules:
- name must be lowercase with underscores, no spaces (e.g. "cyber_security").
- allowed_providers: only include providers the agent genuinely needs. Local-only = ["ollama"].
- allowed_tools: only use tool names already available in Sentinel.
- budget_limit_eur: null means no cap. For paid agents set a reasonable EUR limit (e.g. 5.0).
- system_prompt: write a detailed, useful system prompt the agent will use every run.
- Keep requires_approval false unless the agent has real risk.
"""


class ManagerAgent:
    def build_messages(self, idea: str) -> list:
        return [
            {"role": "system", "content": MANAGER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Create an agent spec for this idea:\n\n{idea}"},
        ]

    def parse_spec(self, response: str) -> dict | None:
        # Strip markdown fences if present
        text = re.sub(r"```(?:json)?", "", response).strip()
        # Decode the first complete JSON object. A greedy r"\{.*\}" match ran to
        # the LAST '}', so trailing prose containing a brace made json.loads fail
        # and discarded an otherwise-valid leading spec. raw_decode reads one
        # value from the first '{' and ignores whatever follows.
        # Try raw_decode at each '{' in turn: if the model prepends prose that
        # contains a brace, decoding at that brace fails, so advance to the next
        # one rather than giving up on an otherwise-valid later object.
        decoder = json.JSONDecoder()
        start = text.find("{")
        while start != -1:
            try:
                obj, _ = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                start = text.find("{", start + 1)
                continue
            if isinstance(obj, dict):
                return obj
            start = text.find("{", start + 1)
        return None

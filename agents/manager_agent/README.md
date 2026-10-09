# FORGE — Agent factory

_One of Sentinel's built-in agents (`~/Documents/lab/active/sentinel/agents/manager_agent/`). Split out into its own project on 2026-09-14 — see the parent project's README.md for how Sentinel's agent roster fits together._

`key: manager` · class: `agents/manager_agent/__init__.py → ManagerAgent` · factory: `services/agent_factory.py → AgentFactory` · panel: `ui/panels/manager.py → ManagerPanel`

> **Scaffold only.** Forge writes a Python draft and inactive registry entries. Sentinel does not dynamically load it or add it to the sidebar. Review, test, and deliberately integrate the code before shipping it.

## What it does
A meta-agent that turns a plain-language idea into a reviewable agent scaffold: it asks an LLM for a structured JSON spec, you review it, and on approval the Agent Factory writes the Python draft and inserts inactive DB rows.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Idea box | Describe the agent: purpose, inputs, output sections, providers. |
| Model override | Optional provider/model change; a strong code-capable model is selected by default. |
| Analyze Idea | Draft a reviewable specification. Approve and Reject are revealed only after a valid draft exists. |
| Clear | Reset. |
| Approve & Create / Reject | Commit or discard the reviewed spec. |

## Outputs
A reviewable specification shown as **Agent Overview**, **Access and
Safeguards**, **System Instructions** and **Design Reasoning** cards, with the
raw JSON behind a disclosure. The Creation Log is separate. On approval Forge
writes a new `agents/<name>_agent.py`, an `agents` table row and a `tools` row.
It remains outside the built-in roster and sidebar until a developer integrates
it.

## How it works
`ManagerAgent` prompts the LLM to emit the JSON spec; `manager_analyze_idea()` parses/validates it into `pending_spec`; `manager_approve_spec()` hands it to `AgentFactory`, which writes the class file (with `build_messages()`) and the DB entries.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/manager_agent/__init__.py` | `ManagerAgent` — spec-generation prompt. |
| `services/agent_factory.py` | `AgentFactory` — writes files + DB rows. |
| `main.py: manager_analyze_idea()/manager_approve_spec()/manager_reject_spec()` | Review flow. |
| `services/database.py: _seed_default_agents()` | Where built-in agents are also seeded. |

## Extend it
- **Custom GUI generation**: today Forge creates standard-panel agents; extend `AgentFactory` to scaffold a `build_<name>_panel()` too.
- **Validation**: tighten spec checks (provider names, prompt length) before approval.
- **Dynamic loading**: a future, separately designed loader could import reviewed modules and expose an appropriate UI safely.

## Requirements
Provider key. Run from source when creating scaffolds, then review and test the generated code.

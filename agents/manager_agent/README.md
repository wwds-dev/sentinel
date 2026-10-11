# FORGE — Agent factory

_One of Sentinel's built-in agents (`~/Documents/lab/active/sentinel/agents/manager_agent/`). Split out into its own project on 2026-09-14 — see the parent project's README.md for how Sentinel's agent roster fits together._

`key: manager` · class: `agents/manager_agent/__init__.py → ManagerAgent` · factory: `services/agent_factory.py → AgentFactory` · panel: `ui/panels/manager.py → ManagerPanel`

> **Scaffold only.** Forge writes a Python draft and inactive registry entries. Sentinel does not dynamically load it or add it to the sidebar. Review, test, and deliberately integrate the code before shipping it. The draft holds only the agent's name, description and system prompt: no panel and no tool code.

## What it does
A meta-agent that turns a plain-language idea into a reviewable agent scaffold: it asks an LLM for a structured JSON spec, you review it, and on approval the Agent Factory writes the Python draft and inserts inactive DB rows. Forge does not write reports or run tools.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Idea box | Describe the agent: purpose, users, inputs, outputs, providers, tools, budget, approval. The spec has no fields for users, inputs or outputs, so those only land in the description or system prompt if the model puts them there. |
| Provider / Model | The two selectors in the run bar (labelled "Agent Builder") choose the model that drafts the spec. At startup the app preselects the recommended choice (when it is in the lists) and marks it BEST FIT. There is no separate "Model override" control. |
| Auto-route | Asks the shared router to pick a provider and model for the current idea text. Forge has no status line, so it shows no message either way. |
| Analyze Idea | Sends the idea to the selected model through the shared request guard (provider permission, budget caps, cost confirmation). The reply streams into a text box, then becomes the review cards. |
| Approve & Create Agent | Disabled until a spec has been parsed **and** passes the factory's checks (see below). Opens a Yes/No confirmation. |
| Reject / Clear Spec | Discards the pending spec and its cards. The idea text stays. Writes nothing. Enabled as soon as the review cards are shown, valid spec or not. |
| Clear | Empties the idea box and the review. The Creation Log is kept. Does not stop a running analysis. |
| Stop (window) | Forge has no Stop button of its own; the window's Stop cancels a running analysis. Nothing is written. |

## Outputs
A reviewable specification shown as **Agent overview** (name, label, description), **Access and safeguards** (providers, tools, budget, approval), **System instructions** and **Design reasoning** (only if the model gave one) cards, with a **Raw response** disclosure that shows the model's whole reply, not only the JSON. All card text is shown as plain characters, so markup in a reply cannot hide part of a spec. The Creation Log is separate: in memory only, lost when the app closes, with `[Ready]`, `[Invalid]`, `[Error]`, `[Stopped]`, `[Created]`, `[Info]`, `[Failed]`, `[Rejected]` and `[Cleared]` entries.

If the factory would refuse the spec, a **Cannot be created as written** card with the reason is put on top, the log gets `[Invalid]`, and Approve stays disabled. If the reply holds no JSON object, a "Could not structure the response" card shows the reply and Approve and Reject stay disabled.

## What Approve writes
Approve opens "Confirm Agent Creation". **No** changes nothing. **Yes** makes the factory check the spec again and then write, as one all-or-nothing step:

1. **One file**, `<data folder>/agents/<name>_agent.py`. Class `<CamelName>Agent` with `__init__` (sets `self.name`) and `build_messages(prompt)` (system prompt plus the user prompt). The description is its docstring. No imports, no I/O, no network or tool code. Every value from the spec is written as a Python string literal, and the finished source is compiled before anything is written; if it does not compile, the approval fails and nothing is created.
2. **One `agents` row**: the spec's label, description, providers, tools, budget and approval flag; `enabled = 0`, `auto_generated = 1`.
3. **One `tools` row**: name and label are the spec's label; `enabled = 0`; the spec's system prompt, description, providers, budget and approval flag; recommended provider is the first listed provider.

If any step fails, the database transaction is rolled back, the file is removed, and a "Creation Failed" box shows the error; the spec stays on screen. On success the cards stay visible with Approve and Reject disabled.

**Nothing else happens.** No `config/` file is written, nothing is imported or run, and the agent is not in the sidebar, the catalog or `agent_box`. Both rows stay disabled and the file is never loaded.

`<data folder>` is the repository root when run from source, `~/Library/Application Support/Sentinel` in the packaged macOS app, and `Sentinel Data` on the drive in portable mode. The confirmation and the log show only `agents/<name>_agent.py`, not that base folder.

## How it works
`ManagerAgent` prompts the LLM to emit the JSON spec. `ManagerPanel.analyze_idea()` sends it; when the reply arrives, `ManagerAgent.parse_spec()` takes the first JSON object in it, the panel fills the cards and runs `AgentFactory.validate_spec()` on it. `ManagerPanel.approve_spec()` asks for confirmation and hands the spec to `AgentFactory.create_agent()`, which validates again, renders the class file (with `build_messages()`) and writes it and the DB entries.

`validate_spec()` requires:
- all eight keys: `name`, `label`, `description`, `allowed_providers`, `allowed_tools`, `budget_limit_eur`, `requires_approval`, `system_prompt`;
- `name`: a lowercase letter first, then lowercase letters, digits and underscores, at most 64 characters; not one of the eight built-in keys or the thirteen retired keys (no other name is reserved, including `router`, `course`, `narrator` and the display names such as `forge`);
- `label` 1-80 characters, `description` 1-500, `system_prompt` 1-20,000;
- `allowed_providers`: a non-empty list without duplicates, each one of ollama, openai, deepseek, kimi, gemini, anthropic, qwen;
- `allowed_tools`: a list without duplicates; each tool must already exist in the `tools` table (a disabled or Forge-created tool counts);
- `budget_limit_eur`: null or a finite, non-negative number; `requires_approval`: true or false;
- no `agents` row with the same name or label, no `tools` row whose name or label is the new label (both compared case-insensitively), and no existing `agents/<name>_agent.py`.

Spec keys beyond these eight are not written. That includes `reasoning`, which is shown on its card and is kept only in the saved chat of the Analyze request.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/manager_agent/__init__.py` | `ManagerAgent` — `build_messages()` (the spec-generation prompt) and `parse_spec()`. |
| `ui/panels/manager.py` | `ManagerPanel` — `analyze_idea()`, `_on_finished()`, `approve_spec()`, `reject_spec()`, `clear()`, `stop()`. |
| `ui/widgets.py` | `SectionView` / `SectionCard` — the review cards and the Raw response disclosure. |
| `services/agent_factory.py` | `AgentFactory` — `validate_spec()`, `create_agent()`, `render_agent_source()` (template plus compile check), `_update_registry()`, `_update_tool_registry()`. |
| `services/agent_catalog.py` | Forge's catalog entry, and the built-in and retired keys that `validate_spec()` reserves. |
| `services/runtime_paths.py` | `user_data_base()` — where the `agents/` folder is. |
| `main.py` | Builds `ManagerPanel` and `AgentFactory(BASE_DIR)`; `authorize_request()` / `record_request()` / `abandon_request()` are the request guard Analyze goes through. |
| `services/database.py: _seed_default_agents()` | Where built-in agents are also seeded. It does not touch Forge-generated rows, and `_retire_moved_agents()` leaves `auto_generated = 1` rows alone. |
| `ui/dialogs.py` | Settings → Agents and Tools tabs, which list the generated rows. |
| `tests/test_agent_factory_validation.py`, `tests/test_agent_factory_atomicity.py`, `tests/test_ui_panels.py` (`TestForgePanel`) | Factory checks, all-or-nothing creation, panel behaviour. |

## What it does not do
- It does not load or run the generated agent, add it to the sidebar, or write a panel.
- It does not make the generated prompt usable from Chat. A generated tool row ticked in Settings → Tools appears in Chat's tool selector after a restart, but Chat's allowed tools are the fixed five, so a send with it is refused.
- It does not scan the idea or the spec for secrets. Its checks are mechanical (shape, names, limits); whether a spec is safe or sensible is for the reviewer to judge.

## Known limitations
- **Specs cannot be edited.** Approve as shown, or Reject and Analyze again. Running Analyze while a spec is pending discards it without asking.
- **Wrong-typed fields break the review.** A reply that parses but has a field of the wrong type (for example `allowed_tools: null`, or a list as `system_prompt`) leaves the review area empty ("No results yet.") or showing only the first cards, with Approve and Reject disabled and no log line. Run Analyze again. Spec validation only runs after the cards are filled, so this case never reaches it.
- **Code fences are stripped.** `parse_spec()` deletes every triple-backtick sequence in the reply (and a `json` label right after it), including inside JSON string values. A system prompt that contains a code fence is therefore altered: what you review and what is written is the stripped text, and the Raw response disclosure shows the original.
- **The prompt's example lists all seven providers.** The rules tell the model to keep only the providers the agent needs, but a model that copies the example over-grants. Check the Providers line before approving.
- **Generated rows are not marked in Settings.** Settings → Agents and Tools list them alphabetically among the built-ins, with the same checkbox and no "generated" marker. Ticking one makes nothing run.
- **Blocked requests are mostly silent.** A validator refusal shows a "Request Blocked" box. A missing API key or a declined cost confirmation leaves only "[Blocked] The request was not sent."
- **Stop is logged twice and recorded as an error.** The log shows "[Stopped]" and "[Error] Request cancelled by user.", and the run is closed with status `error`, not `cancelled`.
- **Clear does not cancel.** A reply that arrives after Clear fills the review again and can re-enable Approve for an idea that is no longer on screen. Stop the analysis before pressing Clear.
- **Budget caps see the idea only.** The cost estimate and budget check are made on the idea text, not on the instructions Forge adds to it.
- **The confirmation sets no default button**, so Enter may confirm; click the button you mean. The confirmation and the Creation Log are not forced to plain text; they quote only the key, label and error messages, never the system prompt.
- **Approval needs hard links.** The file is published with `os.link`, so on a volume without hard-link support (for example exFAT, a documented portable format) approval is expected to fail with "Failed to create agent scaffold" and write nothing.
- **A failed approval can leave a stray file.** A full disk can leave a hidden `.<name>_agent.*.tmp` in `agents/`, and a crash after the file is linked but before the rows are committed leaves an unregistered `<name>_agent.py` that blocks that name. Delete the file by hand.
- **Analyze is saved even if you reject.** Each analysis that gets a reply leaves a saved chat (the idea and the reply) under `manager`, plus a `runs` and a `usage` row. Approvals and rejections are recorded only in the in-memory Creation Log.

## Extend it
- **Custom GUI generation**: the scaffold has no panel; extend `AgentFactory` (and `AGENT_TEMPLATE`) to scaffold a `build_<name>_panel()` too.
- **Spec editing**: let the reviewer change fields before approving, and tolerate wrong-typed fields in `_populate_sections()`.
- **Settings marker**: flag `auto_generated` rows in the Agents and Tools tabs (`ui/dialogs.py`).
- **Dynamic loading**: a future, separately designed loader could import reviewed modules and expose an appropriate UI safely.

## Requirements
A way to run the model: Ollama (no key), or a provider key with its API permission ticked or confirmed at request time. To import the scaffold as part of the codebase, run Forge from a source checkout, where `agents/` is the repository's package folder; the packaged and portable builds write to the data folder above, outside the code. Then review and test the generated code.

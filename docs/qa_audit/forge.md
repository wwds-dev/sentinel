# Forge (`manager`) — requirements-traceability audit

Repo: `/home/claude/wwds-dev/sentinel` (read-only, static reading only; nothing was run, including pytest). Date: 2026-10-09.
Paths below are relative to the repo root. "R" = README.md, "M" = docs/agents/manager.md, "T" = docs/training/forge.md, "TT" = ui/tooltips.py, "MT" = tests/manual_test_cases.md section 8 (and section 9 where noted), "TR" = docs/testing_roadmap.md line 64 (Forge row). MT and TR were added as promise sources because the Forge test cases are written against them.

## 1. Verdict

Forge's core safety story holds in code: nothing is written before the confirm dialog; the key is a strict ASCII whitelist that blocks the eight built-in keys, the thirteen retired keys and existing dynamic keys; approval writes exactly one `agents/<name>_agent.py` plus one disabled `agents` row and one disabled `tools` row; disk and DB are published all-or-nothing; there is no loader anywhere (grep-verified) and the sidebar and `agent_box` are built only from the canonical catalog; the Analyze request is logged under `manager`. It is not test-ready, though, for four reasons. (1) `AGENT_TEMPLATE` interpolates the LLM-supplied `description` unescaped into a `"""` docstring, so hostile or merely awkward text (`"""`, a trailing backslash, `\U`) produces a broken scaffold or injected code that runs when a developer imports it, and success is still reported; the TR P0 line "hostile spec text" will fail. (2) The tool row Forge inserts drops the spec's providers, budget and approval, and the approval/confirm/log text tells users the prompt "can be enabled as a Chat tool from Settings → Tools", which the Validator actually blocks for every Chat send. (3) Review fidelity and robustness are weak: cards render LLM text with Qt's auto rich-text detection, spec validity is only checked after the user clicks Approve and confirms, there is no way to edit a spec, and Stop does not behave as documented (run closed as "error", and Ollama cannot be cancelled). (4) The docs disagree with the code and with each other (stale file and function map in manager.md, "reviews/builds" wording in README and catalog, workflows.md and advanced_tools.md describing Forge as a report generator, manual tests assuming seven agents). Every other Forge promise traced to code or is process advice.

## 2. Counts

| Status | Count |
|---|---|
| IMPLEMENTED | 30 |
| PARTIAL | 18 |
| STUB | 0 |
| MISSING | 2 |
| NOT CODE-VERIFIABLE | 3 |
| **Total promises traced** | **53** |

Beyond the table: 12 cross-document contradictions (b), roughly 20 undocumented behaviours (c), 15 fix items (section 5, of which 4 are blocking).

## 3. Traceability table

| # | Promise (source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| F-01 | Forge is one of eight built-ins: key `manager`, display name Forge (R:9-20) | IMPLEMENTED | services/agent_catalog.py:19-27, 84-86; main.py:1702, 1729-1740 | Sidebar and `agent_box` derive from the catalog. |
| F-02 | "Creates and reviews specifications for new agents and tools" (R:20) | PARTIAL | agents/manager_agent/__init__.py:35-39; ui/panels/manager.py:126-161, 237-292; services/agent_factory.py:221-229; services/agent_catalog.py:24 | The LLM creates the spec; "review" is a human click-through, Forge reviews nothing. "Tools" means one Chat-tool row cloned from the agent's prompt; there is no separate tool spec. Catalog text "Builds and reviews new agents and tools" is seeded into the DB (database.py:639-654) and overclaims. |
| F-03 | Scaffold is written only after review and approval (R:198, M:5, T:24) | IMPLEMENTED | ui/panels/manager.py:237-261; main.py:288 | Nothing writes before the Yes click in the confirm dialog. |
| F-04 | Registry entries are inactive (R:198, M:5, T:24-25) | IMPLEMENTED | services/agent_factory.py:206-210 (enabled literal 0, auto_generated 1), 227-228 (tools enabled 0) | Settings → Agents/Tools can flip them (ui/dialogs.py:524, 553, 1172, 1181); see F-08. |
| F-05 | Sentinel does not dynamically load the scaffold (R:198, M:5, T:26) | IMPLEMENTED | grep `importlib\|import_module\|__import__\|spec_from_file_location\|runpy\|pkgutil` over all *.py (excluding .venv): only tests/test_osint_keys.py:142-145 and tests/test_vibe.py; main.py:290-298 hard-codes `agent_instances`; `Registry.list_agents` has one caller, ui/dialogs.py:524 | `Validator.validate` is never called with a dynamic agent key. docs/honesty_audit.md:124 still lists "no loader" as an open choice. |
| F-06 | Not added to the sidebar (R:198, M:24, T:25, MT:193) | IMPLEMENTED | main.py:1702, 1729-1740, 2046-2050; services/database.py:349-355 | Only `BUILTIN_AGENT_ORDER`; dynamic rows never reach sidebar or `agent_box`. |
| F-07 | "Built-in agents come from the canonical catalog" (R:258) | IMPLEMENTED | services/agent_catalog.py:9-93; services/database.py:639-654, 709-724 (`_migrate_registry` imports only `auto_generated` rows); main.py:1731 | |
| F-08 | Forge agents use the dynamic registry and "remain separate" / are "clearly separate" in Settings (R:258, MT:206) | PARTIAL | services/agent_catalog.py:3-5; services/database.py:674-686; services/registry.py:116-124 (ORDER BY name); ui/dialogs.py:524-537, 553-564 | Separate from catalog and sidebar. In Settings → Agents and Tools they are interleaved alphabetically with built-ins, same checkbox, no "generated" marker; ticking enables a row nothing consumes. MT:206 is not met. |
| F-09 | Analyze Idea asks the LLM for a structured JSON spec (M:8, T:15) | IMPLEMENTED | ui/panels/manager.py:126-161; agents/manager_agent/__init__.py:5-39 (schema), 41-62 (parse); ui/panels/base.py:370-394 | Goes through authorize/record guard. |
| F-10 | Approve and Reject "revealed only after a valid draft exists" (M:16) | PARTIAL | ui/panels/manager.py:96-106 (always visible, disabled), 173-187; services/agent_factory.py:43 (`validate_spec` only called from :142) | Not "revealed": visible but disabled. "Valid" means "parses as any JSON object" (`{"foo":1}` enables Approve). Reserved name, bad provider etc. surface only after Approve + Yes, as a "Creation Failed" box. |
| F-11 | Clear resets (M:17) | IMPLEMENTED | ui/panels/manager.py:294-307 | Clears idea, spec, pending spec; keeps the Creation Log; does not stop a running worker, so a late result re-populates the spec and re-enables Approve for a cleared idea. |
| F-12 | "Model override": optional provider/model change; strong code-capable default (M:15) | PARTIAL | ui/panels/manager.py:31, 71-77; ui/panels/base.py:165-227, 263-285 (`build_model_override` unused by Forge); main.py:1562-1590, 1595-1608, 3154-3200; services/model_recommendations.py:573 | No control named "Model override": provider/model combos sit inline in the run bar. Default is the recommended anthropic / claude-sonnet-5 (or router-derived pick) applied at startup; panel default `deepseek` is only a fallback. With no Anthropic key, `prepare_agent_route` silently re-routes. |
| F-13 | Output as cards: Agent Overview, Access and Safeguards, System Instructions, Design Reasoning (M:19-22) | IMPLEMENTED | ui/panels/manager.py:198-223; ui/widgets.py:759-782 | Titles are sentence case in code. Design Reasoning card is omitted when `reasoning` is empty (widgets.py:775-776). |
| F-14 | Raw JSON behind a disclosure (M:21-22) | PARTIAL | ui/widgets.py:727-739, 784-796 | Toggle is "Raw response · N words" and shows the whole raw reply (prose and fences included), not the parsed JSON. |
| F-15 | Creation Log is separate (M:22) | IMPLEMENTED | ui/panels/manager.py:112-123 | In-memory `QTextEdit`; not persisted. |
| F-16 | Approval writes `agents/<name>_agent.py`, one `agents` row, one `tools` row (M:22-23) | IMPLEMENTED | services/agent_factory.py:147-166, 204-229; ui/panels/manager.py:261-281; services/runtime_paths.py:49-59 | Location is `<user_data_base>/agents`: repo root in dev, `~/Library/Application Support/Sentinel` when frozen, `Sentinel Data` in portable mode. |
| F-17 | File and function map (M:3, 28, 33-37) | PARTIAL | agents/manager_agent/__init__.py (a package, not `agents/manager_agent.py`; tests/test_agent_roster.py:96); grep `manager_analyze_idea\|manager_approve_spec\|manager_reject_spec` hits only docs/agents/manager.md:28, 35 | Real entry points: `ManagerPanel.analyze_idea/approve_spec/reject_spec` (manager.py:126, 237, 287). Correct: `ManagerAgent`, `AgentFactory`, `_seed_default_agents`. "parses/validates" is wrong: analysis only parses. |
| F-18 | AgentFactory writes a class with `build_messages()` plus DB entries (M:28) | IMPLEMENTED | services/agent_factory.py:19-31, 188-202 | |
| F-19 | "Extend it" section is accurate (M:38-41) | PARTIAL | services/agent_factory.py:19-31 (no panel), 66-70, 72-81 | "today Forge creates standard-panel agents" is false (contradicts M:5). "Tighten checks (provider names, prompt length)" is already implemented (providers must be in `SUPPORTED_PROVIDERS`, prompt at most 20,000 chars). |
| F-20 | Requires provider key; "run from source when creating scaffolds" (M:43-44) | PARTIAL | services/runtime_paths.py:49-59; main.py:91, 288; services/agent_factory.py:36-37 | Not enforced, not warned. Frozen/portable builds write to the user-data `agents/` folder, which a developer cannot import from. Ollama needs no key. |
| F-21 | Free-text idea (purpose, users, inputs, outputs, providers, tools, budget, approval) is captured in the spec (T:11-15) | NOT CODE-VERIFIABLE | agents/manager_agent/__init__.py:11-22 | LLM compliance. Schema has no users/inputs/outputs fields. Verify with five representative ideas and check each stated constraint lands in description, system_prompt, providers, tools, budget or approval. |
| F-22 | Review shows internal name, label, description, system prompt, providers, tools, budget, approval flag (T:19-20) | IMPLEMENTED | ui/panels/manager.py:198-223 | Read-only: no field can be edited, only approve or reject and re-analyse. Cards are `QLabel` auto-format (widgets.py:686); see (c). |
| F-23 | "Reject / Clear Spec" discards the draft (T:21; M:17 "Reject") | IMPLEMENTED | ui/panels/manager.py:103-106, 287-292 | No file or DB write. Idea text is kept; log gets "[Rejected] ...". Button is labelled "Reject / Clear Spec"; M calls it "Reject". |
| F-24 | Cancel writes nothing: Reject, Clear, "No" in confirm, Stop (MT:187-188) | IMPLEMENTED | ui/panels/manager.py:225-234, 258-259, 287-300; `create_agent` is reached only at :261 | No agent file, no `agents`/`tools` row on any of these paths. |
| F-25 | Literal MT:188 "Cancelling does not create files or registry records" | PARTIAL | main.py:3628 (`runs` row), 3646 (`usage` row), 3666 with services/history_store.py:13-35 (`data/chats/*.json`) | The Analyze step has already written a saved-chat JSON file plus `runs` and `usage` rows under `manager`; they remain after Reject. Test must scope "files/registry" to `agents/` and the `agents`/`tools` tables. |
| F-26 | Stop cancels the analysis and the run closes with the correct status (MT section 9; panel Stop) | PARTIAL | ui/panels/manager.py:190-196, 225-234; ui/workers.py:192-200, 221-223, 242-254; ui/panels/base.py:396-401; main.py:3745; services/ollama_client.py:194 | Streaming: cancel becomes `error_signal`, `abandon()` defaults to "error" (osint_heavy.py:619 passes "cancelled"); log shows both "[Stopped]" and "[Error] Request cancelled by user.". Non-streaming (Ollama `chat()` is non-streaming): the flag only suppresses token emits, `finished_signal` still fires (workers.py:254), so the run is recorded as success and a spec appears with Approve enabled after "[Stopped]". |
| F-27 | Generated key is valid (MT:190) | IMPLEMENTED | services/agent_factory.py:51-53; agents/manager_agent/__init__.py:25 | `re.fullmatch(r"[a-z][a-z0-9_]{0,63}")`, `isinstance(str)` first. |
| F-28 | Invalid key / path traversal rejected (TR P0) | IMPLEMENTED | services/agent_factory.py:52-53, 99, 150 | ASCII whitelist: no `/ . \ NUL`, no leading digit or underscore, at most 64 chars. File is always `<name>_agent.py` under `agents_dir`; fixed suffix means it cannot shadow a stdlib module. Trailing and double underscores are accepted (cosmetic). |
| F-29 | No collision with built-in keys (MT:190) | IMPLEMENTED | services/agent_factory.py:55-56; services/agent_catalog.py:9-82, 90-94 | All 8 built-in and 13 retired keys rejected. Gaps: tests/test_agent_roster.py:33, 40 treat `router` and `course` as retired but the code set lacks them; `narrator` (MT:9) is absent too; built-in display names (`trace`, `forge`, `tunnel`, `beacon`, `bloodhound`) are not reserved as keys. |
| F-30 | No collision with existing dynamic agents, tools, files (TR P0) | IMPLEMENTED | services/agent_factory.py:99-101, 103-124, 163; services/database.py:16 (PK) | Case-insensitive name/label vs `agents`, label vs `tools` name/label, file-exists check, exclusive `os.link` closes the race. |
| F-31 | Approval creates only the expected agent definition and dynamic registry entries (MT:192) | IMPLEMENTED | services/agent_factory.py:157-166 | Exactly one .py, one `agents` row, one `tools` row, one DB transaction. The `tools` row (a Chat-tool entry) is in M:23 but not in MT:192. |
| F-32 | Disk or DB failure leaves no half-created agent (TR P0) | IMPLEMENTED | services/agent_factory.py:151-182; tests/test_agent_factory_atomicity.py:76-124 | Order: temp file, DB inserts (uncommitted), hard-link publish, commit. On error: transaction rolls back and temp/published file is removed. Gaps: ENOSPC mid-write leaks a hidden `.<name>_agent.*.tmp` (path is returned only after the write, :188-202, so :157 never assigns it); a crash between link (:163) and commit leaves an orphan scaffold that blocks retry (:99-101); no `compile()` check; no test for COMMIT-time failure. |
| F-33 | Approval works on every supported volume (implied; docs/portable_mode.md:17) | PARTIAL | services/agent_factory.py:161-165; docs/portable_mode.md:17 | `os.link` needs hard-link support; exFAT/FAT/SMB lack it, so every approval fails closed ("Failed to create agent scaffold: ..."). exFAT is a documented portable format. Runtime check needed. |
| F-34 | UI clearly says "inactive scaffold, not added to sidebar" (MT:193) | IMPLEMENTED | ui/panels/manager.py:244-256, 267-270, 274-281 | Confirm dialog, log and success dialog all say it. Tooltip does not (F-49). |
| F-35 | UI text: "system prompt can be enabled as a Chat tool from Settings → Tools after a restart" (manager.py:253-254, 269-270, 279-280) | PARTIAL | main.py:239-243, 2079, 3373-3379; services/tool_catalog.py:52-77; services/validator.py:61-65; services/registry.py:148-155; services/agent_catalog.py:16 | After enable + restart the tool appears in Chat's selector, but every send is blocked: "Agent 'chat' does not permit tool '<label>'" (Chat's `allowed_tools` is the fixed five; no UI edits it). Not in any doc. Code-traced; confirm manually. |
| F-36 | Generated code not silently granted providers/tools/external access beyond the approved spec (MT:194) | PARTIAL | services/agent_factory.py:72-87, 206-219, 221-229; services/registry.py:141-146; agents/manager_agent/__init__.py:16 | Agent row mirrors the spec exactly. The tool row omits the spec's providers, budget and approval, so defaults apply (`allowed_providers='[]'` = every provider, no cap, no approval) if the tool ever becomes usable (latent, masked by F-35). The prompt's example pre-fills all seven providers; a model that copies it over-grants and there is no edit UI to prune. |
| F-37 | Scaffold imports nothing beyond the spec (MT:194) | IMPLEMENTED | services/agent_factory.py:19-31 | Template holds a class, `__init__` and `build_messages`; no imports, I/O, network or tool code. Subject to F-38. |
| F-38 | Malformed/hostile spec text cannot alter generated code (TR P0) | MISSING | services/agent_factory.py:19-21, 62-64, 191-196 | `description` goes unescaped into a `"""` docstring and is only checked non-empty, at most 500 chars. `"""`, a trailing `\`, `\N`/`\U`, or a trailing `"` gives a SyntaxError or injected statements that run at import; success is still reported (no `ast.parse`). `name` and `system_prompt` use `!r` and are safe. By inspection; confirm with one `compile()` test. |
| F-39 | Request logged under `manager` (MT:195) | IMPLEMENTED | ui/panels/base.py:370-387; main.py:3594-3637, 3646, 3666 | `runs`, `usage` and saved-chat rows carry agent `manager`, tool `-`. Creation, approval and rejection are not logged anywhere persistent. |
| F-40 | Unapproved providers/tools rejected (TR P0) | IMPLEMENTED | services/agent_factory.py:72-87, 112-127; tests/test_agent_factory_validation.py | Exact-match, case and whitespace sensitive. Tools must exist in `tools` (disabled and earlier Forge tools count). Empty provider list rejected; `allowed_tools: null` rejected. |
| F-41 | Dynamic rows survive restart and stay out of the sidebar (TR P1) | IMPLEMENTED | services/database.py:167-169, 674-686; main.py:1702 | By inspection (SQLite rows persist; retire/label-sync touch only built-ins or non-generated rows). Not run. |
| F-42 | Approved scaffold imports under an isolated test path (TR P1) | PARTIAL | agents/__init__.py (empty); services/agent_factory.py:19-31 | Importable as `agents.<name>_agent` in a source checkout when the description is benign (F-38). A frozen build writes outside the bundle. |
| F-43 | Best practices: keep secrets out of prompts, approval for writes/spend, test failure paths (T:30-36) | NOT CODE-VERIFIABLE | services/agent_factory.py (no secret scan, grep `secret\|api_key\|redact`: none) | Advice; Forge enforces nothing. Verify by reviewer procedure. |
| F-44 | Exercise: read-only log-summary agent defaulting to Ollama, spec has no file-write or network capability (T:37-40) | PARTIAL | agents/manager_agent/__init__.py:11-22; services/agent_factory.py:223-224 | Schema has no capability or default-provider field; "default" is `allowed_providers[0]` becoming the tool's `recommended_provider`. "No file-write/network" can only be judged from the tools list and prompt text. Run it by hand. |
| F-45 | Developer must inspect, test and integrate; restarting is not a safety review (R:198, T:26-27) | NOT CODE-VERIFIABLE | none | Process promise. Verify by procedure. |
| F-46 | Tooltips for `idea_input`, `analyze_btn`, `clear_btn`, `reject_btn` (TT:155, 158, 159, 162) | IMPLEMENTED | ui/tooltips.py:154-164; ui/panels/manager.py:54, 63, 68, 103; main.py:464-481 | Widgets exist. "Clear the form" also clears the review. |
| F-47 | Tooltip `manager.spec_display` (TT:160) | MISSING | ui/panels/manager.py (widgets are `sections`, `stream_box`; grep `spec_display`: none); main.py:473-480 | `_set_tooltips` skips missing widgets silently: dead tooltip. |
| F-48 | Tooltips `provider_box`, `model_box` (TT:156-157) | PARTIAL | main.py:339 (seed) before 346 (`install_agent_recommendations`); main.py:1535-1550 | Seeded text is overwritten by the BEST FIT tooltips. |
| F-49 | Tooltip `approve_btn`: "Forge will write the agent code and register it" (TT:161) | PARTIAL | services/agent_factory.py:19-31, 206-210 | Omits "inactive / not loaded"; "agent code" is a prompt-only stub. |
| F-50 | Tooltip `log`: "spec generation, approval, and file creation events" (TT:163); placeholder promises "validation events" | PARTIAL | ui/panels/manager.py:120, 188, 196, 233, 264-270, 284, 292, 300 | Logs Ready / Error / Stopped / Created / Failed / Rejected / Cleared only; no validation events, nothing persisted, parse failures log nothing. |
| F-51 | Every agent, Forge included, has an Auto-route button (R:71) | IMPLEMENTED | ui/panels/base.py:212-220 | Not listed in M's inputs table. |
| F-52 | Recommended choice carries a BEST FIT badge (R:57) | IMPLEMENTED | services/model_recommendations.py:573; main.py:1382-1397, 1487-1500 | |
| F-53 | Learning Centre has a Forge lesson; in-app docs button opens manager.md (R:109, 332) | IMPLEMENTED | ui/learning_center.py:64; main.py:4540-4560 | |

## 4. Cross-document findings

### (a) Promises present in one place but not the other

In UI / tooltips / training / README / manual tests, not in docs/agents/manager.md:
- Confirm-dialog, log and success-dialog claim that the prompt "can be enabled as a Chat tool from Settings → Tools after a restart" (manager.py:253-254, 269-270, 279-280). No document says it, and it does not work (F-35).
- Auto-route button (R:71) and the window-level Stop; M's input table lists neither. Stop is the only way to cancel; Forge has no Stop button (manager.py:225-234).
- Button names: "Reject / Clear Spec" and "Approve & Create Agent" (T, manager.py:96, 103); M says "Reject" and "Approve & Create".
- "Dynamic registry … remain separate from the built-in roster" (R:258) and "dynamic Forge agents are clearly separate" (MT:206).
- "Logged under `manager`" (MT:195); log tooltip "spec generation, approval, and file creation events" (TT:163).
- Training: best practices, the exercise, "Restarting alone is not a safety review", "add a suitable panel, test permissions" (T:26-36).
- README safety line "especially when it adds tools or external access" (R:198): the template cannot add either.

In docs/agents/manager.md, not in UI / training:
- A "Model override" control (M:15), "Approve and Reject revealed only after a valid draft" (M:16), "raw JSON behind a disclosure" (M:21-22).
- "Requirements: provider key; run from source" (M:44). Training never warns that packaged and portable builds write the scaffold to the user-data folder.
- The Extend-it list and the file/function map (M:30-41).

Not in any document:
- The scaffold's exact shape (class `<CamelName>Agent`, no imports), the `<name>_agent.py` location per run mode, hard-link publication, file mode 0600.
- That specs cannot be edited, that extra spec keys are dropped, and that validation only runs on Approve.

### (b) Contradictions

1. README Forge row "Creates and reviews specifications" (R:20) and catalog description "Builds and reviews new agents and tools" (agent_catalog.py:24) vs "Scaffold only" (M:5, R:198). Forge neither builds a working agent nor reviews anything.
2. manager.md "today Forge creates standard-panel agents" (M:39) vs "Scaffold only … does not add it to the sidebar" (M:5). No panel is generated (agent_factory.py:19-31).
3. manager.md "Tighten spec checks (provider names, prompt length)" (M:40) vs agent_factory.py:66-81, which already enforces both.
4. manager.md "manager_analyze_idea() parses/validates" and "main.py: manager_*()" (M:28, 35) vs code: the functions do not exist; analysis does not validate (F-10, F-17).
5. manager.md "class: agents/manager_agent.py" (M:3, 33) vs README:206 and the tree: `agents/manager_agent/__init__.py`. README is right.
6. Panel text "can be enabled as a Chat tool" vs Validator step 5 (validator.py:61-65) and Chat's fixed `allowed_tools` (agent_catalog.py:16).
7. Approve tooltip "write the agent code and register it" (TT:161) vs the inactive, scaffold-only reality stated everywhere else.
8. docs/training/workflows.md:9-13 and 74 ("Trace → Bloodhound → Forge: … a structured report", "Chat → Forge: … a concrete fix or deliverable", "Use Forge for a finished output") and docs/training/advanced_tools.md:15 ("Forge: Reporting and scripting tools") describe Forge as a report generator. The same file's Workflow 1 step 5 (workflows.md:22) says the opposite ("for an ordinary written report, use Chat's Writing tool"), and forge.md, README and manager.md say Forge only makes agent scaffolds.
9. tests/manual_test_cases.md:7, 10, 206 say seven built-ins and omit Sentry from the sidebar list; README:9, catalog and tests/test_agent_roster.py:22-31 say eight. TODO.md:20 and docs/workspace_structure.md:10 also say seven. This changes what the built-in-key collision test must cover.
10. MT:206 "dynamic Forge agents are clearly separate" vs Settings listing them interleaved and unmarked (F-08).
11. Code comments ui/host.py:38-41 and ui/panels/manager.py:11-12 say the factory edits `agents/` and `config/`; it writes no `config/` file.
12. T:19-20 and the panel's log placeholder imply validation feedback during review; validation actually happens only at approval (F-10).

### (c) Undocumented behaviour

Security / integrity:
- Template injection via `description` (F-38).
- Review cards and confirm dialog render LLM text through Qt auto-format: `SectionCard` body (widgets.py:686), the raw-response label (widgets.py:734) and `QMessageBox.question` (manager.py:244-256) use default `Qt.AutoText`, and no `setTextFormat(Qt.PlainText)` exists anywhere in ui/. If the first line holds a recognised HTML tag, the text is rendered as rich text, so comments or `display:none` spans in `system_prompt` or `description` can be hidden from the reviewer while still being written. Needs a runtime check.
- The tool row ignores the spec's providers, budget and approval (F-36).
- Extra keys in the spec (for example `network: true`, `allowed_hosts`) are silently dropped; `reasoning` is never persisted.
- `parse_spec` deletes every "```" in the reply (manager_agent:43), including inside JSON string values, which silently alters a `system_prompt` that contains a code fence. What is reviewed and written is the stripped text; "Raw response" shows the original.
- The prompt's example schema lists all seven providers (manager_agent:16); the rule says "only include providers the agent genuinely needs", but copying the example over-grants.
- A dynamic agent with `requires_approval: true` can never run: Validator step 11 blocks it and no approval UI exists.

UI / flow:
- No spec editing. The only options are approve as-is or reject and re-analyse.
- Spec validation (reserved name, unknown provider/tool, label collisions) is deferred to Approve + Yes; invalid specs enable Approve.
- A parseable but malformed spec (for example `allowed_providers` as `null` or `[1]`, `system_prompt` as a list) makes `_populate_sections` raise inside the `_on_finished` slot (manager.py:199-219, widgets.py:775). No excepthook is installed (grep `excepthook`: none). Result: blank review area, Approve and Reject stay disabled, no message, no log line.
- Parse failure shows a "Could not structure the response" card with the raw reply, disables Approve and Reject, and writes no log line (manager.py:174-180).
- Blocked requests give no reason: Forge has no `status_label`, so "API key not configured" and "Request cancelled" (main.py:3572-3590) are never shown; only "[Blocked] The request was not sent." appears (manager.py:152).
- Re-running Analyze while a spec is pending discards that spec with no confirmation (manager.py:141-146).
- Confirm dialog sets no default button (Qt picks Yes); Enter approves. The label is not escaped.
- After successful creation the spec cards stay on screen with Approve/Reject disabled (manager.py:271-273).
- The cost estimate is made on the idea only, not the idea plus the ~350-token system prompt (manager.py:148 passes `idea`; main.py:3570).
- Frozen and portable builds write the scaffold under Application Support / `Sentinel Data`; the dialog shows `agents/<name>_agent.py` without that context.

Persistence / audit:
- Each successful Analyze leaves a saved chat under agent `manager` (visible in Saved Chats) plus `runs` and `usage` rows, even if rejected (F-25).
- Approve, reject and creation are only in the in-memory Creation Log, with no persistent audit record.
- Scaffold file mode is 0600 (`NamedTemporaryFile`, then hard-linked); a hidden `.<name>_agent.<rand>.tmp` exists briefly and can leak (F-32).
- `_retire_moved_agents` explicitly protects `auto_generated = 1` rows (database.py:674-686); `_sync_agent_labels` touches built-ins only.

## 5. Must fix before test phase

Blocking (P0 in the TR sense):
1. F-38 (template injection): emit the description with `repr()` (`__doc__ = {description!r}` or a `#` comment) and run `ast.parse` or `compile()` on the rendered code before `os.link`, failing the approval if it does not compile.
2. F-35 (false Chat-tool claim): delete the "can be enabled as a Chat tool from Settings → Tools" sentences at manager.py:253-254, 269-270, 279-280 (or deliberately support it); the Validator blocks it today.
3. F-36 (tool row drops spec limits): write the spec's `allowed_providers`, `budget_limit_eur` and `requires_approval` into the `tools` INSERT at agent_factory.py:227-229, so enabling the tool can never widen the approved spec.
4. Review fidelity (c): call `setTextFormat(Qt.PlainText)` on the `SectionCard` body label, the raw-response label and the confirm dialog (or escape the text), so reviewers see exactly what will be written.

Needed so the written test cases can pass:
5. F-10 (late validation): call `agent_factory.validate_spec(spec)` in `_on_finished`, show the result in the Access and safeguards card, and keep Approve disabled when it fails.
6. F-26 (Stop): pass `abandon("cancelled")` from the cancel path and ignore `finished_signal` after a cancel flag, so Ollama's non-streaming result is dropped instead of becoming an approvable spec.
7. Malformed-spec crash (c): wrap `_populate_sections` in try/except and fall back to the "Could not structure the response" card plus a log line.
8. F-08 / MT:206: mark `auto_generated` rows (for example "generated · inactive") or list them in their own section in the Settings Agents and Tools tabs (dialogs.py:524-537, 553-564).
9. F-47 / F-49: remove `manager.spec_display` from tooltips.py or give the review widget that name; reword the approve tooltip to "write an inactive scaffold and disabled registry rows".
10. Docs (b): correct manager.md paths, function names and Extend-it list; reword R:20 and the catalog description to "Drafts agent specifications and, after approval, writes an inactive scaffold"; fix workflows.md:9-13, 74 and advanced_tools.md:15; update manual_test_cases.md:7, 10, 206 to eight agents including Sentry.

Should fix:
11. F-33: replace `os.link` with `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` plus write and fsync (or a fall-back), or document Forge as unsupported on exFAT/FAT/SMB volumes.
12. F-29: add `router`, `course`, `narrator` (and the built-in display names) to the reserved set, or state in the docs that only the eight keys and retired keys are reserved.
13. F-32: assign the temp path before writing (or catch inside `_write_agent_temp_file`) so an ENOSPC failure deletes the partial `.tmp`; add a COMMIT-failure test.
14. (c) blocked-reason: give `ManagerPanel` a `status_label` or print the reason in `stream_box` when `authorize()` returns False (manager.py:148-154).
15. F-39: record creation, approval and rejection in `runs` (or an audit log) so "logged under `manager`" covers the approval decision, not only the LLM call.

## Appendix A — Key validation walk-through (`services/agent_factory.py:43-129`)

Order of checks: spec is a dict (:44) → all eight required keys present (:47-49) → `name` is `str` and `fullmatch [a-z][a-z0-9_]{0,63}` (:51-53) → name not in `BUILTIN_AGENTS` (8 keys) or `RETIRED_BUILTIN_AGENTS` (13 names) (:55-56) → label non-blank, at most 80 (:58-60) → description non-blank, at most 500 (:62-64) → system_prompt non-blank, at most 20,000 (:66-70) → providers non-empty list of unique strings, all in `SUPPORTED_PROVIDERS` (:72-81) → tools list of unique non-blank strings (:83-87) → budget null or finite non-negative non-bool number (:89-94) → `requires_approval` is a real bool (:96-97) → `<agents_dir>/<name>_agent.py` does not exist (:99-101) → DB: no `agents` row with the same name or label (case-insensitive), no `tools` row whose name or label equals the new label (:103-111, 121-124), every listed tool exists (:112-127).
Not checked: hostile characters in label/description (F-38 for description), reserved display names, Python keywords (harmless because of the `_agent` suffix).
`validate_spec` runs only inside `create_agent` (:142), i.e. after the user confirms; the panel never calls it earlier.

## Appendix B — What is written, and where

Approve (success) writes: `<base>/agents/<name>_agent.py` (class `<CamelName>Agent`, `__init__` sets `self.name`, `build_messages(prompt)` returns system + user messages; no imports); one `agents` row (`enabled=0`, `version='1.0'`, spec providers/tools/budget/approval/description, `log_path='data/logs/runs.jsonl'`, `auto_generated=1`); one `tools` row (`name=label`, `enabled=0`, `system_prompt=spec.system_prompt`, `recommended_provider=providers[0]`, `recommended_model=''`, everything else default). `<base>` = `user_data_base()`.
Analyze (every run, regardless of outcome) writes: one `runs` row, one `usage` row, one `data/chats/<ts>_<uuid>.json` (agent `manager`).
Never written: any `config/` file, any sidebar/registry entry for the built-in roster, any loader hook.

## Appendix C — Atomicity sequence (`create_agent`, :135-182)

1. `validate_spec` (:142). 2. Render and write hidden temp file in `agents/` (:157). 3. Open DB connection, `INSERT agents`, `INSERT tools` inside one implicit transaction (:158-160). 4. `os.link(temp, final)`: exclusive, fails if the final name appeared meanwhile (:163). 5. Unlink temp (:165). 6. `with` exit commits.
On any exception: the `with` rolls back the DB; temp is unlinked if still present; the published file is unlinked only if this call linked it (:171-179), so a competing file is never deleted (tested at test_agent_factory_atomicity.py:111-124). Residual windows: crash between 4 and 6 (orphan scaffold, no rows); ENOSPC during 2 (orphan `.tmp`).

## Appendix D — Review / approve / reject UI states

| State | Trigger | Visible | Buttons (Analyze / Approve / Reject / Clear) | Log line |
|---|---|---|---|---|
| Initial | panel built | placeholder "Example: A cybersecurity agent …"; spec area "No results yet." | on / off / off / on | none (placeholder text) |
| Empty idea | Analyze with blank text | warning "No Idea" | unchanged | none |
| Busy | Analyze while running | info "Busy – Analysis already running." | unchanged | none |
| Authorising | Analyze | stream box "Analyzing…", spec area hidden, pending spec cleared | off / off / off / on | none |
| Blocked | `authorize()` false (consent declined, no key, validator) | stream box "[Blocked] The request was not sent." (validator failures also show a "Request Blocked" box) | on / off / off / on | none |
| Streaming | tokens arrive | stream box shows raw text | off / off / off / on | none |
| Parsed | reply parses as a JSON object | cards: Agent overview, Access and safeguards, System instructions, Design reasoning (if any), plus "Raw response · N words" toggle | on / **on** / **on** / on | "[Ready] Spec generated. Review and approve or reject." |
| Unparsed | no JSON object found | card "Could not structure the response" with raw reply | on / off / off / on | none |
| Error | worker error (also streaming cancel) | stream box "[Error]\<msg>" | on / off / off / on | "[Error] <msg>" |
| Stopped | window Stop | analyze re-enabled immediately | on / unchanged / unchanged / on | "[Stopped] Analysis cancelled." (+ "[Error] …" for streaming) |
| Confirm | Approve | dialog "Confirm Agent Creation" Yes/No, no default set | n/a | none; No → nothing happens |
| Created | Yes and factory success | info "Scaffold Created"; cards remain | on / off / off / on | "[Created] Agent '<name>' created successfully." + "✓ <path>" and two "SQLite … (updated)" lines + "[Info] …" |
| Creation failed | Yes and factory error | warning "Creation Failed" with errors; spec kept | on / on / on / on | "[Failed] Could not create agent:\n<errors>" |
| Rejected | Reject | spec area cleared; idea text kept | on / off / off / on | "[Rejected] Spec cleared. You can describe a new idea." |
| Cleared | Clear | idea and spec cleared | on / off / off / on | "[Cleared]" |

## Appendix E — Could a generated agent ever be loaded?

No. There is no import mechanism for `agents/<name>_agent.py` anywhere in main.py, services/, ui/ or agents/ (grep in F-05); `agent_instances` is a literal dict of seven classes (main.py:290-298; Forge's `ManagerAgent` is owned by the panel); the sidebar and `agent_box` come from `BUILTIN_AGENT_ORDER`; the registry rows are read only by the Settings dialog and by `Validator`/`Registry` helpers that no code path calls with a dynamic key. The only user-reachable effect of a generated agent is its `tools` row appearing in Chat's tool selector once enabled, and that is blocked at send time (F-35).

# Sentinel testing roadmap

Updated 2026-09-16. This is the implementation plan for testing the **shipped**
Sentinel application: all eight built-in agents, their distinct workflows, shared
controls, storage, and three distribution modes. It complements the step-by-step
[manual acceptance checklist](../tests/manual_test_cases.md); it does not replace
it. Proposed V3 features get tests when their behavior is specified and built.

## Baseline and boundaries

- The Sentinel suite currently passes **558 tests** (`.venv/bin/python -m pytest
  -q -p no:cacheprovider`). Most are unit, scenario, mocked integration, or
  offscreen Qt tests. That number is not a claim of full functional coverage.
- `agents/vpn_agent/` is a separate companion repository with **227 collected
  tests** in its own `.venv`; `agents/bug_spray/` is another repository with five
  small tests and no local `.venv` at this review. Neither suite is included by
  Sentinel's `pytest.ini` (`testpaths = tests`). Running both under Sentinel's
  interpreter also fails collection because their imports/dependencies differ.
  Test each in its own repository/environment, then test the Sentinel integration
  seam. Do not conflate companion-app behavior with Sentinel's narrower panels.
- Tests should use disposable databases, files, profiles and credentials. The
  present suite redirects SQLite, and since 2026-10-09 Chat's saved settings
  too: a baseline run once changed tracked `config/settings.json`
  (`default_model_ollama`). The app now writes picks only to the git-ignored
  `data/settings.local.json`, the suite redirects both files, and
  `tests/conftest.py` fails the run if either real file changes. Isolate the
  rest of the writable configuration the same way before expanding the suite.
  Automated
  runs never contact paid models, public research services, arbitrary websites,
  Wi-Fi targets or a VPN server. Mock at the provider/process boundary; a
  separately labelled, opt-in acceptance pass may use owned lab resources.
- A successful model call does not prove a factual investigation or a real
  vulnerability. Test structure, provenance, uncertainty, consent and logging;
  have a human review domain-specific conclusions.

## Test layers and targets

| Layer | Purpose | Target / cadence |
|---|---|---|
| Pure unit/contract | Validation, parsing, formatting, pricing, routing, scope/secret rules | Every change; exhaustive critical branches and representative edge cases |
| Integration | Agent → guard → worker → storage/UI state with fake model/network/process | Every PR; one success, refusal, failure, cancellation and retry path for each runnable workflow |
| Offscreen Qt interaction | Control states, navigation, Enter/Shift+Enter, progress, scroll/layout, shutdown | Every PR; no real network or paid request |
| Packaged-app smoke | Real launcher, frozen bundle and portable build with isolated sample data | Before release and whenever installer/runtime paths change |
| Manual owned-lab acceptance | Adapter hardware, SFTP, optional public-source/network checks, VPN observations | Before release of the affected feature, never against unowned targets |

Aim for **100% exercised decisions at permission, budget, scope, secret,
file-write/delete, external-contact and process-execution boundaries**. For
non-critical business logic, an **80–90% measured branch-coverage goal** is a
useful guide once coverage collection is added, not a substitute for the
scenario matrix below. Each user-visible workflow needs at least one positive,
invalid-input, denied-consent, cancellation/error, and persistence case where
those states apply. No percentage target is asserted for generated prose.

## Agent-by-agent matrix

`Existing` names the closest current automated tests, not proof that every
behavior in the row is covered. `Next` is the incremental work to add.

| Agent and shipped functions | Existing | Next tests / release checks |
|---|---|---|
| **Chat** — General Chat, Writing, Coding, Summarize, Rewrite; command scaffolds; multi-turn context; Enter/Shift+Enter; Stop; timestamps and scrollable transcript; saved chats and Chat Projects | `test_agents_scenarios`, `test_tool_catalog`, `test_ui_panels`, `test_release_regressions` | **P1:** table-drive all five tool prompts and command choices through the guard; verify follow-up context without cross-project leakage, partial/cancelled turn handling, long-chat scroll, save/reopen/rename/filter/assign, and usage attribution after restart. Mock each provider's stream/non-stream behavior. |
| **Trace** — target/type validation; Structure Query; consented domain/IP (WHOIS, DNS, crt.sh), username (URLScan), email (per-source selection), company (GLEIF) Live Research; person/phone no-live rule; activity trail and Saved Searches | `test_agents_scenarios`, `test_domain_lookup`, `test_identity_lookup`, `test_ui_panels` | **P0:** parameterize consent decline and cancellation **before each source** so zero unintended contact is proven; verify selected email recipients, missing HIBP key, per-source timeout/partial results and contacted-vs-skipped labels. **P1:** malformed/punycode/IPv6 cases, save/reopen/rename/delete without fresh requests, target/provider restoration and source-provenance UI. |
| **Bloodhound** — dossier scope/objective/optional EXIF; five sections and indicators; save/clear; local and strict-host-key SFTP metadata discovery, filters, limits, cancellation, reveal/SSH handoff | `test_agents_scenarios`, `test_local_file_search`, `test_remote_file_search`, `test_ui_panels` | **P0:** prove no file contents, paths, SSH keys or credentials enter model calls/logs; reject changed/unknown host keys and paths outside explicit roots; cancel between folders and at limits. **P1:** real localhost SFTP fixture or owned test host, permission failures, deep/symlink loops, Unicode names, date/size boundaries, EXIF without GPS, dossier parser fallback, and save/reopen. |
| **Beacon** — interface info, nearby scan, signal monitor, ping, USB detection, connection preflight, optional AI explanation, offline Kali command builder | `test_agents_scenarios`, `test_ui_panels` | **P0:** fake subprocess/system-profiler outputs for no adapter, disconnected route, unsupported chipset and timeout; prove preflight never changes mode/route and generated Kali commands never execute. **P1:** each live mode's parsing/progress/cancel/Save/Clear, AI opt-in data handoff, placeholder/adapter capability validation, narrow UI. **Manual:** built-in Wi-Fi plus supported USB adapter in an owned lab; verify dual-interface internet/monitor warning. |
| **Bug Spray** — scope/program/target/findings; optional local nmap; model report, CVSS/CWE/PoC/remediation/submission cards; Save/Clear | `test_agents_scenarios`, `test_ui_panels` | **P0:** scope/target validation before nmap or model, safe command construction (no shell injection), bounded scan and Stop/kill behavior, no out-of-scope fallback, no fabricated scan evidence. **P1:** nmap missing/error/partial output, real loopback-only fixture with explicit opt-in, malformed model sections/CVSS, export content and logged provenance. Companion program-monitoring CLI remains a separate test track; its unimplemented source adapters are not Sentinel functionality. |
| **Tunnel** — model-free local Connection Check, optional separately confirmed IP/latency, profile comparison, Advisor, offline remote/native WireGuard/OpenVPN config builder, non-executing Connect/Disconnect/Restart preview, private-key-free WireGuard inspection | `test_vpn_diagnostics`, `test_ui_panels`, companion `tests/` | **P0:** prove default check/preview/inspection never touches model, external network, privileged command or profile file; secret canary absent from every result/log/chat; decline external contact; stop/close/reset joins worker. **P1:** missing tools, split/full-tunnel and DNS comparison, WireGuard/OpenVPN profile edge cases, config placeholders and mode-specific routing, source/frozen/portable parity. **Manual:** owned VPN profile before/after connection; verify findings do not claim leak-proof anonymity. |
| **Forge** — idea → JSON spec → review/reject/approve → Python scaffold and inactive registry rows | `test_agents_scenarios`, `test_agent_factory_validation`, `test_agent_factory_atomicity`, `test_ui_panels` | **P0:** reject invalid keys/path traversal, collisions, unapproved providers/tools and malformed/hostile spec text; cancellation/rejection must write nothing; injected disk/DB failure must roll back without half-created active agents. **P1:** approved scaffold imports under isolated test path, survives restart, remains absent from sidebar until deliberate integration, and displays correct creation/error log. |

Do not use a live LLM as the pass/fail oracle for any of these. Keep a small
versioned set of synthetic responses for UI and parser contracts; use rubric-
based human review only for quality claims such as dossier usefulness or report
clarity.

### Separate companion-repository tracks

These are not additional Sentinel sidebar agents, but their code is imported
or related to the two built-ins. Keep their tests in their own repositories.

| Companion | Functionality to test there | Sentinel integration contract |
|---|---|---|
| `agents/bug_spray/` | Config validation, Keychain-only secrets, SQLite program snapshots/diffs, CLI self-test, source registration and failure handling. Its HackerOne adapter currently raises `NotImplementedError`; test that honestly rather than pretending live program discovery works. When real adapters arrive, add offline API fixtures, pagination, rate-limit/timeout, scope-change and paused-program cases. | Import only `sentinel_chat_agent.BugBountyAgent`; the program-monitoring CLI must not silently scan or submit from Sentinel. |
| `agents/vpn_agent/` | Its own Monitor (connection, DNS/IP/latency, health, connect/disconnect/restart and kill switch), Build Server (profiles, keys, WireGuard/OpenVPN render, SSH deployment and recovery), and Privacy (Tor, proxy chain, MAC changes) need their own unit, fake-process integration and explicitly opted-in owned-lab checks. Its 227 collected tests are a useful base, not proof of live network safety. | Sentinel imports the shared advisor/config and status/inspection helpers but exposes **read-only checks and previews**, not the companion's privileged actions. Test this API and packaging seam after companion changes. |

## Shared functionality matrix

| Surface | Required cases | Priority / evidence |
|---|---|---|
| Roster, navigation, enabled agents/tools | Exactly eight built-ins; retired history remains readable; dynamic Forge entries stay separate; panel switch preserves only intended state | **P1** — `test_agent_roster`, Qt navigation and migration tests |
| Provider/model selection and Auto-route | All listed providers (Ollama, OpenAI, DeepSeek, Kimi, Gemini, Anthropic, Qwen); model-list failure/fallback; privacy/local preference; paid marker; explicit override wins | **P1** — extend router and UI contract tests; no network in default suite |
| Request guard, permissions, budgets, cost | Missing key, disabled provider/tool/agent, exact Decimal cap, agent/session/daily limits, consent decline, two simultaneous same-agent runs, streamed usage, Kimi cache, cancellation/failure, no double billing | **P0** — `test_request_guard`, `test_cost_and_limits`, `test_kimi_cache_accounting`; fault-inject at each lifecycle transition |
| Persistence and history | SQLite migration/idempotence, saved chats/searches, run status, project assignment/filter/search, usage/cost export, restart, corrupted/old record recovery | **P1** — extend migrations and UI tests with disposable app data; never use real `data/sentinel.db` |
| Settings and global controls | General rate/budgets; Agents/Tools/Pricing enable/edit; API permission; Inspector visibility and live values; Tips/guide/model guide; Cost history CSV and Run log filters | **P1** — one UI interaction + readback/restart test per setting; malformed input cannot silently overwrite good values |
| Learning Centre and documentation | Every menu entry opens; all lessons/images/links load in source and frozen app; search, navigation, readability, screenshot freshness | **P2** — `test_learning_center` plus offscreen interaction and first-user exercise pass |
| Workers and responsiveness | Stop during start, stream, blocking I/O and shutdown; no orphan thread/process; errors visible and logged; UI remains interactive | **P0** for costly/external work; deterministic fake delays, no arbitrary sleeps |
| Installation and single-instance lifecycle | Native thin launcher, existing-window handoff, Quit stays quit, no launchd keep-alive/focus loop, missing checkout/venv error; frozen icon/resources and storage identity | **P0** release smoke; `test_release_regressions` source assertions are not a substitute for launching the installed app |
| Source, frozen and USB-portable state | Correct writable root and migration of the legacy `Sentinel Fork` data directory (the pre-2026-09-12 application name); `Sentinel AI` untouched; upgrade preserves data; unavailable/read-only/low-space drive; eject after quit; reset's two confirmations and exact deletion boundary | **P0** on runtime/packaging changes — `test_portable_*`, `test_runtime_storage_paths`, isolated volume acceptance |
| Layout/accessibility | 1280×800, 1600×1000 and large/retina windows; both Inspector states; keyboard focus/shortcuts, readable status/errors, scroll reachability and contrast | **P2** screenshot/interaction review; preserve current narrow-inspector regression |
| Multi-agent handoffs | Trace → Bloodhound, Beacon → Tunnel, Bloodhound → Chat, Chat → Forge; explicit human review and route/consent at every handoff; no automatic sensitive-data transfer | **P1** synthetic workflow tests and manual review; see [training workflows](training/workflows.md) |

## Delivery sequence

1. **P0 — safety harness and boundaries.** First isolate every writable config
   file as well as SQLite, and add a worktree-unchanged assertion. Add a
   network/process deny-by-default fixture for normal Sentinel tests, with
   named fake endpoints and a disposable app-data root. Fill the P0 rows above
   first. In particular, assert that a
   declined action produces **zero** external/model/process calls and that
   secrets never enter logs or prompts. Add the real installed-app quit/reopen
   smoke that would have caught the September launcher regression.
2. **P1 — complete workflow contracts.** Parameterize all agent modes, provider
   outcomes and shared settings. Add persisted-restart and cross-agent tests;
   publish a simple requirement-to-test map in this document as each row is
   covered. Keep the normal suite offline and fast enough for each change.
3. **P2 — release and human acceptance.** Automate source/frozen/portable smoke
   where feasible; run the [manual checklist](../tests/manual_test_cases.md)
   against fictional data and owned hardware. Review screenshots and Learning
   Centre exercises with a first-time user. Record version, OS, route, test
   data, expected/actual outcome and redacted evidence for failures.
4. **Ongoing.** Every new feature lands with a boundary test, scenario test,
   UI test if visible, failure/cancellation case, and updated manual case.
   Re-run affected companion suites from their own repos when shared imports
   change. A failed safety test blocks release; a skipped hardware test is
   reported as **not verified**, never as passed.

## Release gate

- Sentinel automated suite and affected companion suites pass in their own
  environments; no collection errors or unreviewed skips.
- All P0 consent, scope, secret, budget, reset and launcher tests pass.
- One offline success and one failure/cancel path per shipped agent workflow
  pass through UI and run logging; representative data persists across restart.
- Installer, frozen bundle and portable smoke pass for the distribution modes
  being released; the installed app stays closed after Quit.
- The manual checklist is signed off for any hardware/network features being
  claimed, using only owned/authorised targets. Unsupported or untested modes
  are called out in release notes rather than implied to work.

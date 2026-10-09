# Sentinel testing roadmap

Updated 2026-10-09. This is the implementation plan for testing the **shipped**
Sentinel application: all eight built-in agents, their distinct workflows, shared
controls, storage, and three distribution modes. It complements the step-by-step
[manual acceptance checklist](../tests/manual_test_cases.md); it does not replace
it. Proposed V3 features get tests when their behavior is specified and built.

## Baseline and boundaries

- The Sentinel suite collects about **1,356 tests** as of this update
  (`.venv/bin/python -m pytest -q -p no:cacheprovider`); the pytest summary line
  is the authoritative, current figure, because parametrised cases move it. Most
  are unit, scenario, mocked integration, or offscreen Qt tests. That number is
  not a claim of full functional coverage.
- The companion code now lives in this tree. `agents/vpn_agent/` is an ordinary
  package here with no `tests/` and no `.venv` of its own; the 227 tests it had
  as a standalone repository are not part of this checkout. `agents/bug_spray/`
  is a git submodule with its own offline suite (44 test functions; its README
  says 48 tests) and no `.venv` in a fresh checkout. Sentry's suite is in
  `agents/sentry/tests/` (25 test functions; run it from `agents/sentry/` with
  `../../.venv/bin/python -m pytest -q`). None of the three is collected by
  Sentinel's `pytest.ini` (`testpaths = tests`), so a normal run does not
  exercise them; the panels that use them are covered by `test_ui_panels`,
  `test_bug_spray_feed` and the `test_vpn_*` files. Run each companion suite from
  its own directory, then test the Sentinel integration seam. Do not conflate
  companion-app behavior with Sentinel's narrower panels.
- Tests should use disposable databases, files, profiles and credentials. The
  suite redirects SQLite, Chat's saved settings (`config/settings.json` and the
  git-ignored `data/settings.local.json`), the model-watch and ratings files,
  the `.env` save target and the Tunnel audit log, and `tests/conftest.py` fails
  the run if the real `config/settings.json`, `data/settings.local.json` or
  `.env` changes. A run therefore no longer modifies the tracked
  `config/settings.json`, which an earlier baseline run once did
  (`default_model_ollama`). Isolate the rest of the writable configuration the
  same way before expanding the suite. Automated
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
| **Chat** — General Chat, Writing, Coding, Summarize, Rewrite; command scaffolds; multi-turn context; Enter/Shift+Enter; Stop; timestamps and scrollable transcript; saved chats and Chat Projects | `test_agents_scenarios`, `test_tool_catalog`, `test_ui_panels`, `test_release_regressions` | **P1:** table-drive all five tool prompts and command choices through the guard; verify follow-up context without cross-project leakage, partial/cancelled turn handling, long-chat scroll, save/reopen/rename/filter/assign, and usage attribution after restart. Every provider has a streaming path (Ollama included), so mock the word-by-word replay path only for backends that return a finished string (OpenAI image models). Add cases for: one saved file per conversation across turns, rename and project surviving later turns, a Tool switch replacing the system prompt, ui_only notices excluded from provider messages, Local only substitution, an unticked provider refused on send and on keystroke, and opening another agent's record then continuing in Chat. |
| **Trace** — target/type validation and Auto-detect; Structure Query and Stop; consented domain (WHOIS, DNS, Team Cymru, Mnemonic, crt.sh, Wayback Machine) and IP (the same minus crt.sh and Wayback, plus DShield and Shodan InternetDB) Live Research, with key-gated threat-intelligence sources added only when their key is saved; username (URLScan, GitHub, Keybase); email (per-source selection: EmailRep, Gravatar, HIBP, BreachDirectory, Hunter); company (GLEIF, CourtListener, key-gated OpenSanctions); Exposure Check (Ransomware.live, Ahmia, key-gated Intelligence X, DeHashed, Snusbase, LeakCheck); person/phone no-live rule; activity trail and Saved Searches | `test_agents_scenarios`, `test_domain_lookup`, `test_intel_sources`, `test_identity_lookup`, `test_exposure_lookup`, `test_key_check`, `test_osint_keys`, `test_ui_panels` | **P0:** parameterize consent decline and cancellation **before each source** so zero unintended contact is proven; verify selected email recipients, missing HIBP key, per-source timeout/partial results and contacted-vs-skipped labels; Structure Query Stop (ends as an error box, nothing saved or costed) and Stop between sources of a Live Research run; the Exposure verdict when every source fails or the run is cancelled; the cost estimate and cap for a Structure Query (computed from the target text only). **P1:** malformed/punycode/IPv6 cases, save/reopen/rename/delete without fresh requests, target/provider restoration and source-provenance UI; reopen of a saved Live Research (type box, trail wording, no request); private, loopback and link-local IPs and IP DNS errors; BreachDirectory fields passed through unfiltered; Auto-detect cases (dotted handle, crypto address, IDN); a second run started while a stopped worker is still finishing; the catalogue download and its Local only behaviour. |
| **Bloodhound** — dossier scope/objective/opt-in EXIF; public-source consent and budget re-check on the assembled prompt; five sections and indicators; save/clear; local and strict-host-key SFTP metadata discovery, filters, limits, cancellation (Cancel, window Stop, quit), local reveal / remote path copy, SSH handoff | `test_agents_scenarios`, `test_osint_heavy_collection`, `test_local_file_search`, `test_remote_file_search`, `test_ui_panels` | **P0:** prove no file contents, paths, SSH keys or credentials enter model calls/logs; reject changed/unknown host keys and paths outside explicit roots; cancel between folders and at limits. **P1:** real localhost SFTP fixture or owned test host, permission failures, deep/symlink loops, Unicode names, date/size boundaries, EXIF without GPS, dossier parser fallback, and save/reopen. Also: consent decline contacts nothing and bills nothing; the EXIF checkbox gates the prompt and the Saved Chats entry; remote RejectPolicy is passed to paramiko (fake client), the remote limit and a mid-run cancel keep matches; no file-search data reaches any run_backend/run_logger call. |
| **Beacon** — interface info, nearby scan, signal monitor, ping, USB detection, connection preflight, optional AI explanation, offline Kali command builder | `test_agents_scenarios`, `test_ui_panels` | **P0:** fake subprocess/system-profiler outputs for no adapter, disconnected route, unsupported chipset and timeout; prove preflight never changes mode/route and generated Kali commands never execute. **P1:** each live mode's parsing/progress/cancel/Save/Clear, AI opt-in data handoff, placeholder/adapter capability validation, narrow UI. **Manual:** built-in Wi-Fi plus supported USB adapter in an owned lab; verify dual-interface internet/monitor warning. |
| **Sentry** — read-only collectors (`arp`, `ndp`, `route`, `lsof`), MAC-keyed baseline with report-once folding, severity-sorted findings, dry run, reset, collector-failure handling, Stop, one state directory shared with the launchd background watch, optional AI read behind the spend guard | `agents/sentry/tests/test_engine.py` (not collected by the parent suite; run it from `agents/sentry/`), `test_ui_panels::TestSentryPanel` | **P0:** panel and headless stores resolve the same directory from a real install (`SENTRY_STATE_DIR` in the plist); AI box off by default and only findings plus counts sent; Stop mid-pass saves nothing and starts no model call; a failing collector refuses a first baseline. **P1:** `watchd` plist contents and launchctl calls; UDP names containing `->`; a Mac with no established TCP connections (lsof exit 1); `watch-once` output when a collector fails; dry run with no baseline. **Manual (owned lab):** ARP-poison a client and the gateway from a second machine and record which findings fire; Remote Login on versus `sudo lsof`; install the background watch, quit Sentinel, wait two intervals, check `watch.log` and the list at next start; Install from a built .app; portable volume ejected with the agent installed. |
| **Bug Spray** — scope/program/target/findings; optional local nmap; model report, CVSS/CWE/PoC/remediation/submission cards; Save/Clear | `test_agents_scenarios`, `test_bug_spray_feed`, `test_ui_panels` | **P0:** the unenforced-scope boundary stays truthful (captions and lesson say scope is declared, not verified; nothing claims a check), only nmap can start and never through a shell, bounded scan (10 minutes / 256 KB) and Kill / quit-kill behavior, panel markers never sent to the model as scan evidence, the whole request (not the target) is priced and budget-checked, a reply in the prompt's own layout fills all four cards, the CVSS tile reads the score not the version. **P1:** nmap missing/error/partial output, real loopback-only fixture with explicit opt-in, malformed model sections/CVSS, export content and logged provenance. Known gaps to test or fix: `CVSS 3.1: 7.5` (no 'v') and a score directly after a vector string misread, `###` inside a section ends a card, Severity Target is never read, Stop shows '[Error] Request cancelled by user.' and a non-streaming reply can still land after Stop, Clear does not stop a running request or scan, no 'no platforms enabled' notice in the radar, scan summary discarded by the panel, `--full` can emit false Gone events. The companion program-monitoring CLI remains a separate test track, but the in-app radar's scan lifecycle (no platform enabled by default, first-scan baseline, scan outcome visibility, missing-environment message) is Sentinel functionality and belongs in this row. |
| **Tunnel** — model-free local Connection Check, optional separately confirmed IP/latency, profile comparison, Advisor, offline remote/native WireGuard/OpenVPN config builder, non-executing Connect/Disconnect/Restart preview, private-key-free WireGuard inspection, and the gated live Connect/Disconnect with Import config, templates, kill switch and the Your IP & DNS readout | `test_vpn_diagnostics`, `test_vpn_connection`, `test_vpn_execution`, `test_vpn_killswitch`, `test_vpn_ip_readout`, `test_vpn_worker_shutdown`, `test_ui_panels` | **P0:** default check/preview/inspection never touches model, external network, privileged command or profile file; a blocked, templated or declined action runs nothing and is audited; secret canary absent from every result, status line, audit line and chat (including a wg-quick parse-error echo); hooks are listed on Connect and need a second confirmation; every privileged call goes through the macOS dialog; close/reset during a Connect never raises; decline external contact; stop/close/reset joins worker. **P1:** missing tools, split/full-tunnel and DNS comparison, WireGuard/OpenVPN profile edge cases, config placeholders and mode-specific routing; kill-switch rule inputs per protocol/port and a pf parse check (`pfctl -n`) of build_rules on a macOS host; OpenVPN identity with a space in the data path (installed and portable); import edge cases (no endpoint, IPv6, name rejected by wg-quick); source/frozen/portable parity including the VPN Agent state folder. **Manual:** owned WireGuard and OpenVPN profiles through the checklist in manual_test_cases.md section 7, including arm/disarm/recovery, close-during-connect, and whether Connection Check recognises a Sentinel-started tunnel; verify findings do not claim leak-proof anonymity. |
| **Forge** — idea → JSON spec → review/reject/approve → Python scaffold and inactive registry rows | `test_agents_scenarios`, `test_agent_factory_validation`, `test_agent_factory_atomicity`, `test_ui_panels` | **P0:** reject invalid keys/path traversal, collisions, unapproved providers/tools and malformed/hostile spec text (the scaffold is compiled before anything is written); an invalid spec keeps Approve disabled and review cards render model text as plain text; cancellation/rejection must write nothing; injected disk/DB failure must roll back without half-created active agents. **P1:** approved scaffold imports under isolated test path (source checkout only), survives restart, remains absent from sidebar until deliberate integration, and displays correct creation/error log. |

Do not use a live LLM as the pass/fail oracle for any of these. Keep a small
versioned set of synthetic responses for UI and parser contracts; use rubric-
based human review only for quality claims such as dossier usefulness or report
clarity.

### Separate companion-repository tracks

These are not additional Sentinel sidebar agents, but their code is imported
or related to the two built-ins. Keep their tests with the companion code.

| Companion | Functionality to test there | Sentinel integration contract |
|---|---|---|
| `agents/bug_spray/` | Config validation, Keychain-only secrets (`secrets.py`; no adapter uses a token today, so there is nothing to exercise end to end), SQLite program snapshots/diffs, CLI self-test, source registration and failure handling. All five adapters (HackerOne, Bugcrowd, Intigriti, YesWeHack, Immunefi) are real HTTP adapters with offline fixtures; the endpoints are unofficial and the suite cannot verify live behavior, so keep a short opt-in live check. Still to add: false Gone events under `scan --full`, an Intigriti program turning login-walled, YesWeHack paused detection. | Import only `sentinel_chat_agent.BugBountyAgent`; Sentinel itself launches the scanner in the background once a platform is enabled (no per-scan consent) and never submits; the CLI must not submit from Sentinel. |
| `agents/vpn_agent/` | Its own Monitor (connection, DNS/IP/latency, health, connect/disconnect/restart and kill switch), Build Server (profiles, keys, WireGuard/OpenVPN render, SSH deployment and recovery), and Privacy (Tor, proxy chain, MAC changes) need their own unit, fake-process integration and explicitly opted-in owned-lab checks. The package carries no tests of its own in this tree; the Sentinel `test_vpn_*` files exercise the parts the panel reaches, and none of this proves live network safety. | Tunnel's panel reaches the advisor/config builder, status/inspection helpers, `privileged.run_as_root` and `killswitch.arm/disarm`, and runs wg-quick, openvpn and the pf kill switch through the gate in `services/vpn_execution.py`. Server provisioning, Tor, proxy-chain, MAC and health-monitor code is not reachable from the panel. Test the gate, the privileged-runner seam and the packaging seam after changes here. |

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

1. **P0 — safety harness and boundaries.** Chat's settings files, the real
   `.env` and the Tunnel audit log are already isolated and hash-checked in
   `tests/conftest.py`; isolate every remaining writable config file the same
   way, and add a worktree-unchanged assertion for the rest. Add a
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

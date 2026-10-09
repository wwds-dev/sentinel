# Requirements-traceability audit: Bug Spray (`bug_bounty`)

Audited checkout: `/home/claude/wwds-dev/sentinel` (read-only; nothing run, no pytest). `agents/bug_spray` is a git submodule gitlink (mode 160000, `7ebb2ac`, `.gitmodules:1-3`); the files in the tree are what was audited. Line numbers are from this checkout.

Legend for paths: PANEL = `ui/panels/bug_bounty.py` · FEEDUI = `ui/panels/bug_spray_feed.py` · AGENT = `agents/bug_spray/sentinel_chat_agent.py` · PKG = `agents/bug_spray/bug_spray/` · SRC = `agents/bug_spray/bug_spray/sources/` · BASE = `ui/panels/base.py` · WID = `ui/widgets.py` · WRK = `ui/workers.py` · MAIN = `main.py`.
Promise-source tags: README = `README.md` · AGENT-DOC = `docs/agents/bug_bounty.md` · TRAIN = `docs/training/bug_spray.md` · TIPS = `ui/tooltips.py` · ROADMAP = `docs/testing_roadmap.md` · MANUAL = `tests/manual_test_cases.md` §6. **`agents/bug_spray/README.md` does not exist in this tree** (see #77), so it could not be read; ROADMAP and MANUAL were added as promise sources because the test phase is driven by them.

## 1. Verdict

Bug Spray is two halves of different quality. The **radar half is real code, not stubs**: all five source adapters (HackerOne, Bugcrowd, Intigriti, YesWeHack, Immunefi) are genuine HTTP adapters with parsers, paging guards, politeness, per-platform failure isolation, SQLite snapshots, diffing and a watchlist, covered by fixture-based offline tests (live endpoint behaviour cannot be verified from code, and the endpoints are unofficial/unversioned). But the radar **cannot run from Sentinel in this tree**: `agents/bug_spray/main.py`, `README.md` and `.venv` are absent, so "Scan now" stops at "environment is missing"; and even with them, `enabled_platforms` ships empty so nothing is scanned or announced by default. The **analysis half works end to end** (prompt builder, cards, Save/Clear, cloud consent) but has verified defects: the CVSS tile will read "3.1" for the prompt's own `CVSS v3.1` wording, the PoC/Remediation cards will normally be empty because the parser and the prompt disagree on section format, the Severity Target control is dead, the budget/consent estimate is computed from the target string only, and nmap error text becomes "scan evidence". The nmap runner uses `QProcess` with no shell (no shell injection) but runs any first word, has no timeout/output cap, no start-failure handling, and is only killable by its own Kill button. **Scope/target validation does not run before nmap or the model call**; this is documented as "not enforced" in the UI and training, but the testing roadmap's P0 line assumes it, so that P0 will fail as written. Nothing in the code can submit a report externally, and the only secret handling (Keychain via `keyring`) is dead code because every adapter is anonymous. Docs contradict the code or each other in about a dozen places (collapsed Nmap, Model override, `bugspray` command name, "radar is local", `-T4` vs "conservative timing", stale "HackerOne raises NotImplementedError").

## 2. Counts

Rows: **83** (promises). IMPLEMENTED **40** · PARTIAL **27** · STUB **1** · MISSING **10** · NOT CODE-VERIFIABLE **5** (40+27+1+10+5 = 83; recomputed from the table below with grep).

Adapter sub-count (rows 18-22): 5 of 5 IMPLEMENTED as real HTTP adapters (0 stubs, 0 `NotImplementedError`), all fixture-tested only; live behaviour NOT CODE-VERIFIABLE.

## 3. Traceability table

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 1 | Radar lists saved public programs and recent changes from the saved DB; rendering makes no network call (AGENT-DOC:8-9, TRAIN:12-13) | IMPLEMENTED | FEEDUI:299-337; PKG/feed.py:10-47; PKG/store.py:73-74 (read-only open), 181-186, 214-221 | Two tabs: Recent changes, Programs. tests/test_bug_spray_feed.py:40-49 |
| 2 | Feed reads `agents/bug_spray/data/programs.sqlite3` (AGENT-DOC:33) | IMPLEMENTED | PKG/config.py:33-40 (`data_dir="data"`, `db_path`) | Path is inside the nested repo, not under `services/runtime_paths` (see #80) |
| 3 | Search by name/platform; platform menu; "Show all" ignores watchlist (TRAIN:13, 21-22) | IMPLEMENTED | FEEDUI:227-245, 339-347; PKG/feed.py:13-14 | tests/test_bug_spray_feed.py:120-128 |
| 4 | Selecting a program shows its saved scope and rewards (TRAIN:13-14) | IMPLEMENTED | FEEDUI:349-378 | Shows first 2 scope items plus a count; full list via Full details |
| 5 | Open the program's live page (AGENT-DOC:17, TRAIN:14) | IMPLEMENTED | FEEDUI:380-385, 103-106 | Only https URLs with a host open; the URL string comes from remote platform data |
| 6 | "Use in report" fills Program only, never Target (AGENT-DOC:17, TRAIN:14-15) | IMPLEMENTED | FEEDUI:397-400; PANEL:72 | tests/test_bug_spray_feed.py:75-92. Fills "Hackerone — Acme" (`str.title`) vs placeholder "HackerOne — Acme Corp" (PANEL:70): cosmetic |
| 7 | Full details… / double-click shows whole scope, per-severity rewards, tags (TRAIN:24-25) | IMPLEMENTED | FEEDUI:65-86, 89-108, 255, 264, 274-277 | All remote strings HTML-escaped (:68, 71, 80). test_full_details_show_the_whole_saved_program |
| 8 | Watchlist… edits platforms and keyword/tag/min-payout filters, saved to config.json (TRAIN:22-23) | IMPLEMENTED | FEEDUI:115-187; PKG/config.py:70-73 | Poll interval is not editable here (hand-edit config.json) |
| 9 | Filters change only what is shown; every program is still saved (UI note FEEDUI:126-127) | IMPLEMENTED | PKG/watchlist.py:1-16; PKG/cli.py:190; PKG/feed.py:13-14 | test_scan_watchlist_filters_report_not_storage |
| 10 | Terminal `bugspray` uses the same settings (TRAIN:23-24) | PARTIAL | PKG/config.py:15; PKG/cli.py:355-396; `agents/bug_spray/pyproject.toml:13` | Config is shared, but no `bugspray` command exists: pyproject defines `bug-spray`, cli.py:3-10 documents `python main.py`, and main.py is absent from this tree |
| 11 | Saved scope is a lead; re-read live page; "not authorization" (TRAIN:29-30) | IMPLEMENTED | FEEDUI:85, 373; PKG/cli.py:348 | Shown in details pane, dialog and CLI |
| 12 | Background scan starts when the last scan is older than the config interval, 60 min default (README:18, AGENT-DOC:9,33, TRAIN:15-17) | PARTIAL | FEEDUI:291-297, 416-428; PKG/config.py:32 | (i) `enabled_platforms` ships empty (config.py:22-24) and FEEDUI:417 returns silently, no UI notice; (ii) timer starts only on the panel's first `showEvent` (:291-297), not at app start, so AGENT-DOC:33 "while Sentinel is open" overstates; (iii) needs nested `.venv` + `main.py`, both absent (#78, #79) |
| 13 | "Scan now" starts one immediately in the background (TRAIN:17-18, AGENT-DOC:17) | PARTIAL | FEEDUI:216-224, 430-443; PKG/cli.py:141-145 | Refuses with a status line when PYTHON is missing (:433-435, text points at a README that does not exist). With no platforms the CLI exits 0 "nothing to scan" on stdout, which the panel discards (:445-446); the user sees "Last scan: never · 0 watched programs" |
| 14 | Full re-scan re-downloads every program's details (TRAIN:25-27) | PARTIAL | FEEDUI:221-222, 442; PKG/cli.py:150, 170-177; SRC/__init__.py:43-55 | `--full` hands adapters `known={}`, so a failed detail fetch returns `None` (program skipped), it is missing from `seen`, and `mark_gone` can emit a false "Gone" event (guard only trips above 50%) |
| 15 | Sentinel re-checks for a due scan while open (TRAIN:18) | IMPLEMENTED | FEEDUI:208-210, 402-414 | 60 s QTimer, never stopped on hide. tests/test_bug_spray_feed.py:52-59 |
| 16 | Automatic scans stop when Sentinel closes (TRAIN:30); background work is shut down on close (README:100-103) | PARTIAL | MAIN:4778-4788; `ui/dialogs.py:52-59`; BASE:435-437 | Timer dies with the window, but `BugBountyPanel` has no `shutdown()` and `is_running()` sees only the LLM worker, so the scan `QProcess` (FEEDUI:201) and the nmap `QProcess` (PANEL:260) are not explicitly stopped. Relies on Qt parent destruction; not verified |
| 17 | The directory scan never contacts a program's assets (TRAIN:18-19, AGENT-DOC:11-12, PKG/cli.py:12) | IMPLEMENTED | SRC/hackerone.py:24; bugcrowd.py:23; intigriti.py:28; yeswehack.py:21; immunefi.py:20; grep `socket\|nmap\|subprocess` in the nested package: none | Every request goes to a platform directory host |
| 18 | Source adapter: HackerOne (README:18) | IMPLEMENTED | SRC/hackerone.py:24-145 | Real: POST of a read-only GraphQL query to `hackerone.com/graphql` (:33-47, 85-89), paging and scope-paging with loop guards (:95-105, 116-141). Unofficial, unversioned site endpoint (docstring :10-13). Fixture-tested only; live behaviour NOT CODE-VERIFIABLE |
| 19 | Source adapter: Bugcrowd (README:18) | IMPLEMENTED | SRC/bugcrowd.py:22-127 | Real: `engagements.json` paging, changelog, brief; skips private/demo (:34); reuses stored copy when `publishedAt` unchanged (:116-117). Fixture-tested only |
| 20 | Source adapter: Intigriti (README:18) | IMPLEMENTED | SRC/intigriti.py:27-163 | Real: public listing plus per-program detail; login-walled programs kept from the listing with a reason tag and no detail request (:65-74, 143-145). Fixture-tested only |
| 21 | Source adapter: YesWeHack (README:18) | IMPLEMENTED | SRC/yeswehack.py:20-116 | Real. `active=True` hard-coded (:41) so "paused" can never be detected (see #27); out-of-scope not captured (docstring :8-9). Fixture-tested only |
| 22 | Source adapter: Immunefi (README:18) | IMPLEMENTED | SRC/immunefi.py:19-80 | Real single-request feed; invite-only skipped (:33-34); hidden assets kept with `assets-hidden` tag (:56-57). Fixture-tested only |
| 23 | Only bounty-paying, publicly listed programs; invite-only/login-walled not scraped (SRC/__init__.py:12-14) | IMPLEMENTED | hackerone.py:36-37; bugcrowd.py:34; intigriti.py:77-79; yeswehack.py:27-34; immunefi.py:33-34 | Intigriti login-walled programs are stored from the public listing only (by design) |
| 24 | Failed detail request keeps the last known copy, never stores an empty scope (SRC/__init__.py:15-17) | IMPLEMENTED | SRC/__init__.py:43-55 | Failure goes to `log.warning` (stderr) only, invisible in the app; broken under `--full` (#14) |
| 25 | One failing platform does not abort the others; error is recorded (SRC/__init__.py:69-72) | IMPLEMENTED | SRC/__init__.py:64-85; PKG/cli.py:157-160, 214; PKG/store.py:188-198 | test_fetch_all_isolates_a_failing_platform, test_scan_reports_platform_error_and_fails |
| 26 | Polite HTTP: descriptive UA, minimum gap, timeout, bounded retries honouring Retry-After (SRC/_http.py:1-7) | IMPLEMENTED | SRC/_http.py:17-20, 28-34, 49-71, 74-81 | 0.35 s gap per client, 30 s timeout, 3 retries, Retry-After clamped 0-60. No overall scan deadline or user-cancel |
| 27 | Change feed covers new, returned, gone, scope +/-, reward changes, paused/resumed (PKG/changes.py:1-6) | PARTIAL | PKG/changes.py:26-99; PKG/cli.py:106-132; FEEDUI:28-42 | YesWeHack can never emit paused/resumed (yeswehack.py:41). First scan per platform stores a baseline with no events (cli.py:161-168) |
| 28 | Snapshots written only on change; scan history; change events (PKG/store.py:1-12) | IMPLEMENTED | PKG/store.py:107-130, 188-198, 200-221 | `change_events` and `snapshots` are never pruned |
| 29 | Watchlist: AND across filters, OR within; min payout via approximate FX; unknown currency fails a non-zero minimum (PKG/watchlist.py:1-16) | IMPLEMENTED | PKG/watchlist.py:28-46; PKG/models.py:18, 76-81 | test_scan.py `test_watchlist` parametrised |
| 30 | If more than 50% of a platform's listed programs vanish, do not mark them gone (PKG/cli.py:33-36) | IMPLEMENTED | PKG/cli.py:36, 170-177 | Warning is stored in the scan summary as `note`; the panel surfaces only `error` keys (FEEDUI:333-335), so the guard is invisible in the app |
| 31 | GUI and CLI scans cannot overlap (PKG/cli.py:217-219) | IMPLEMENTED | PKG/cli.py:217-236 | flock on `data/scan.lock`; the panel shows only a generic "finished with errors" |
| 32 | Config validation: unknown platform, interval >= 1, filter types (PKG/config.py) | IMPLEMENTED | PKG/config.py:42-55; FEEDUI:174-187 | test_watchlist_dialog_refuses_invalid_settings |
| 33 | Inputs: Target, Program, Scope type, Findings (AGENT-DOC:18-21, TIPS:139-142) | IMPLEMENTED | PANEL:63-95, 140-150; AGENT:28-44 | 8 scope types (PANEL:75-80) |
| 34 | "Severity" is an input you enter (TRAIN:69, 77-78; AGENT-DOC does not list it) | STUB | PANEL:82-86 | `severity_box` is built but never read (grep: only lines 83-86) and is not an argument of `build_messages` (AGENT:28-30). Training worked example tells the user to fill it |
| 35 | Optional reconnaissance = collapsed Nmap controls (AGENT-DOC:22) | MISSING | PANEL:99-137 | Plain always-expanded `QGroupBox`; `build_model_override`/`ProgressiveSection` (BASE:263) is not used by this panel |
| 36 | Model override: optional provider/model change, strong security-reasoning model by default (AGENT-DOC:23) | PARTIAL | PANEL:163-169; BASE:143, 165-227; `services/model_recommendations.py:572` | Provider, model and Auto-route sit in the run bar, not a collapsed "Model override". Default is anthropic/claude-sonnet-5 described only as a "capability baseline for security and coding analysis" |
| 37 | Analyse runs the request; Stop cancels while active (AGENT-DOC:24, TIPS:147-148) | PARTIAL | PANEL:285-326, 354-357; BASE:439-444; WRK:195-249 | Cancel is cooperative. Streaming path emits "Request cancelled by user." which PANEL:346-352 renders as "[Error]" and status "Error." (overwriting "Stopped."). Non-streaming paths still emit `finished_signal` (WRK:243-249), so a report can appear and be recorded after Stop |
| 38 | Save and Clear "appear with results" (AGENT-DOC:24) | PARTIAL | PANEL:226-233, 309, 344 | Both buttons are always visible; Save is disabled until a response completes |
| 39 | Result split into four copyable cards: Vulnerability Report, Proof of Concept, Remediation, Submission Draft (AGENT-DOC:27-28, TRAIN:51-53, ROADMAP:62) | PARTIAL | PANEL:360-391; WID:652-697, 759-789; AGENT:10-22 | Cards and Copy buttons are real. The parser needs `## Proof of Concept` / `## Remediation` headings, but the prompt asks for numbered bold items inside `## VULNERABILITY REPORT` (AGENT:10-18). A compliant reply therefore yields empty PoC/Remediation cards (empty cards are dropped, WID:775-776) and one card holding everything. Any `##`/`###` inside a section ends it (lookahead stops at the next `##`, which also matches `###`). Tests use a flat `## ` fixture only (tests/test_ui_panels.py:2310-2316) |
| 40 | Raw model reply stays available behind a collapsed disclosure (AGENT-DOC:28-29) | IMPLEMENTED | WID:726-739, 784-789 | "Raw response · N words" toggle; shown only when at least one card exists |
| 41 | Severity indicator shows the parsed severity (AGENT-DOC:29-30, TRAIN:86-88) | IMPLEMENTED | PANEL:394-403 | Needs literal `**Severity**` then a severity word on the same line; first match wins. test_a_finished_report_fills_the_indicators |
| 42 | CVSS indicator shows the parsed CVSS score (AGENT-DOC:29-30, TRAIN:86-88) | PARTIAL | PANEL:405-412 | Regex `CVSS.*?(\d+\.\d+)` captures the version number in `CVSS v3.1: 7.5` and `CVSS:3.1/AV:N/...` as "3.1" (the prompt itself says "CVSS v3.1 score", AGENT:12); tile would show 3.1 in low-severity green for a high finding. The only test uses `CVSS 8.1` |
| 43 | Bounty Estimate tile shows any stated bounty estimate; TRAIN:86-88 expects it "filled in" | PARTIAL | PANEL:414-418; AGENT:10-22 | Populated only if the model volunteers a `$` amount after the word "bounty"; the prompt never asks for an estimate, so it will normally stay "—" |
| 44 | Output is CWE-classified with a CVSS v3.1 score (AGENT-DOC:12, TIPS:147) | NOT CODE-VERIFIABLE | AGENT:11-12 | Prompt-only; no CWE/CVSS validation or computation anywhere. Needs a model-output evaluation set |
| 45 | CVSS and CWE are suggestions marked for review (TRAIN:53-54, 88-89) | MISSING | grep `review`/`cwe` in PANEL and FEEDUI: only the "Source Code Review" scope label | Reminder lives only in the lesson; nothing in the UI or prompt marks the values |
| 46 | `build_messages` composes only the evidence present (AGENT-DOC:33) | IMPLEMENTED | AGENT:30-44 | Empty fields omitted. The "No data provided yet" fallback (:43-44) is unreachable from the panel because `scope_type` is always non-empty. tests/test_agents_scenarios.py:285-352 |
| 47 | Fixed report + submission format is requested (AGENT-DOC:33) | IMPLEMENTED | AGENT:8-24 | |
| 48 | "Paste-ready" / "HackerOne-ready" submission draft (AGENT-DOC:10, TIPS:147) | NOT CODE-VERIFIABLE | AGENT:20-22 | Output quality is model-dependent. Prompt names HackerOne or Bugcrowd only; no per-platform branching ("Program templates" is listed as future, AGENT-DOC:46) |
| 49 | Model must not invent evidence or claim undemonstrated impact (TRAIN:54-55, 89-90; MANUAL:129) | NOT CODE-VERIFIABLE | AGENT:24 | One prompt line only. Needs red-team prompts in the test phase |
| 50 | Report includes reproducible steps, impact, evidence placeholders, remediation (MANUAL:130) | PARTIAL | AGENT:14-18 | Steps, impact and remediation are requested; "evidence placeholders" are never requested |
| 51 | Only analyse assets in scope for an authorised program (README:196, AGENT-DOC:5, TRAIN:7-8, prompt AGENT:5-6) | PARTIAL | AGENT:5-6; PANEL:88-95, 285-326 | Not enforced by design: no in-scope attestation is collected and the model only sees Program/Target text. The boundary is prompt + caption |
| 52 | Screen states Program and Scope are declared by you, not verified or enforced (TRAIN:34-38, MANUAL:131) | IMPLEMENTED | PANEL:88-95 | Always-visible caption; wording matches the lesson |
| 53 | Scope/target validated before nmap runs (ROADMAP:62 P0) | MISSING | PANEL:244-266 | `run_nmap` only checks for empty input; no allow-list, scope check or confirmation dialog |
| 54 | Scope/target validated before the model call (ROADMAP:62 P0) | MISSING | PANEL:285-326 | Only emptiness (:292-295) and "model selected" (:297-299) |
| 55 | Missing scope/target information blocks or warns before analysis (MANUAL:128) | PARTIAL | PANEL:292-295 | Blocks only if target, findings and nmap output are all empty; an empty Program does not warn |
| 56 | Cloud request is permission/budget/consent gated with cost shown first (README:53, TRAIN:92-95) | PARTIAL | PANEL:313-319; BASE:370-387; MAIN:514-534, 3556-3637 | `authorize()` is called with `prompt = target or "bug_bounty"` (PANEL:313), so the estimate, consent dialog and budget check ignore the system prompt, findings and nmap output. Post-hoc billing uses provider usage if reported, otherwise chars/4 of the same short prompt (`log_request`, MAIN:3639-3650) |
| 57 | Analysis is logged under `bug_bounty` (MANUAL:132) | PARTIAL | MAIN:3622-3631, 3639-3685; PANEL:336 | Logged. `record()` is called without `messages`, so Saved Chats stores only the target as the user turn (MAIN:3668-3670); findings are not in history |
| 58 | Save writes the report to a file (AGENT-DOC:42, TIPS:149 says ".txt") | PARTIAL | PANEL:420-433 | Writes the raw model reply (not the cards), default name `.md` with `.md`/`.txt` filters, no `OSError` handling. Tooltip says ".txt" |
| 59 | Clear resets inputs and outputs (TIPS:150) | PARTIAL | PANEL:435-450 | Does not cancel a running analysis or nmap; late tokens repopulate the results (:328-333) |
| 60 | Human-review advice: strip secrets, reproduce once, check duplicate rules (TRAIN:40, 57-59) | NOT CODE-VERIFIABLE | none | User guidance; no redaction of findings before they go to a cloud model |
| 61 | Generated commands/findings need human review before execution or submission (README:196) | PARTIAL | PANEL:246-266 | The auto-built nmap command (:253-254) is written to the field and executed in the same click; no review step. Nothing is submitted (#73) |
| 62 | Manual nmap runner (real subprocess), separate from the directory scan (AGENT-DOC:10-11) | IMPLEMENTED | PANEL:244-266 vs FEEDUI:201, 443 | Two unrelated `QProcess` objects |
| 63 | Nmap uses its own QProcess, separate from the LLM worker (AGENT-DOC:33-34, PANEL:5-7) | IMPLEMENTED | PANEL:260, 279-282 | The window-level Stop does not reach it (`is_running()` ignores it) |
| 64 | Command is built without a shell (ROADMAP:62 P0 "no shell injection") | IMPLEMENTED | PANEL:265-266 | `QProcess.start(program, args)`; no `shell=True`, `Popen`, `os.system` in any Bug Spray file (grep). Metacharacters are passed as literal args |
| 65 | Safe and correct argument handling (ROADMAP:62 P0) | PARTIAL | PANEL:253-254, 265 | First word is any executable (admitted in caption :122-126); `str.split()` so quoted args break; a URL with a port yields `host:port`, which nmap rejects; a target with spaces becomes extra nmap arguments |
| 66 | Caption: runs on your machine, first word launches, no scope check, outside the guard (TRAIN:42-44) | IMPLEMENTED | PANEL:122-129 | Wording matches the lesson |
| 67 | Nmap run is bounded (ROADMAP:62 P0 "bounded scan") | MISSING | PANEL:268-272 | No timeout, output cap, target/port limit or timing preset; default is `-sV -sC -T4` (:107, 254) against TRAIN:45 "use conservative timing" |
| 68 | Nmap can be killed (TIPS:145, ROADMAP:62 P0 Stop/kill) | PARTIAL | PANEL:114-118, 279-282 | `kill()` is SIGKILL of the direct child only (a `sudo`/`sh` wrapper leaves descendants); not stopped on window close (#16); main Stop is deliberately separate |
| 69 | nmap missing, failing or partial output is handled (ROADMAP:62 P1) | MISSING | PANEL:260-266 | No `errorOccurred` handler (grep). `FailedToStart` emits no `finished`, so the panel stays busy on "[Running] ..." until Kill. No PATH augmentation for a Dock-launched app (grep `PATH` in MAIN, ui/, panels: none) |
| 70 | Live output is shown and feeds the analysis (AGENT-DOC:22, TIPS:146) | PARTIAL | PANEL:257, 268-277, 290; AGENT:37-38 | "[Error] ...", "[Running] ..." and "[Done]" live in the same buffer and are sent as "Nmap Scan Output". An error-only buffer alone passes the empty guard (:292) and is billed. Size unbounded |
| 71 | Nothing scans the target unless you run Nmap yourself (TRAIN:94-95) | IMPLEMENTED | PANEL:112, 244 | Only `run_nmap` touches the target host; no automatic path |
| 72 | nmap installed locally "for the scanner" (AGENT-DOC:50) | NOT CODE-VERIFIABLE | none | Ambiguous: "scanner" means the directory scanner elsewhere in the doc, nmap is needed only by the manual runner; a missing binary is unhandled (#69) |
| 73 | Nothing can submit a report to a platform (README:196, ROADMAP:78) | IMPLEMENTED | greps for submit / mutation / smtp / webbrowser / Network access manager in Bug Spray files: none; only POST is the HackerOne directory query (SRC/hackerone.py:86) | The only egress carrying user data is the model call. `openUrl` opens https program pages only (FEEDUI:106, 385) |
| 74 | Secrets live in the Keychain only (ROADMAP:78) | PARTIAL | PKG/secrets.py:11-48; PKG/config.py:3-6 | Keychain-backed via `keyring`; config.json has no secret fields. But `get`/`set` are never called (all adapters are anonymous), and model-provider keys use Sentinel's `.env`, not the Keychain (README:41) |
| 75 | Radar and typing are local; only Analyse leaves the device (TRAIN:92-95) | PARTIAL | SRC/*; FEEDUI:442 | Scan now / Full re-scan / background scans contact five public platform hosts. Free of cost, but not local |
| 76 | Companion CLI must not silently scan or submit from Sentinel (ROADMAP:78) | PARTIAL | FEEDUI:402-443 | Never submits. Does poll in the background without per-scan consent once platforms are enabled (consistent with README:18) |
| 77 | `agents/bug_spray/README.md` exists with setup and the honest-boundary section (task brief; FEEDUI:434, `pyproject.toml:5`, PKG/cli.py:13) | MISSING | `find agents/bug_spray -iname 'readme*'`: no result | Three dangling references; `hatchling` build would fail on `readme = "README.md"`. README-vs-AGENT-DOC comparison impossible |
| 78 | `agents/bug_spray/main.py` scanner entry (FEEDUI:442, PKG/cli.py:3-10) | MISSING | `find agents/bug_spray -maxdepth 2 -name main.py`: no result | Only tests/test_bug_spray_feed.py:63 fakes one; the real file is not in this tree |
| 79 | Nested `.venv` with httpx/keyring (AGENT-DOC:33, FEEDUI:25) | MISSING | `agents/bug_spray/.venv` absent; `requirements.txt` present | No documented bootstrap; ROADMAP:15 already notes "no local .venv" |
| 80 | Radar works in packaged/portable builds; data stays in the portable root (README:100-120, 260-268) | MISSING | `Sentinel.spec` datas (no `agents/bug_spray`); FEEDUI:24-25; PKG/config.py:14-15, 33-40 | Scanner, config.json and DB are resolved from `__file__` inside the bundle; no `runtime_paths` integration; not covered by Emergency Reset |
| 81 | Tooltips describe each Bug Spray control (TIPS:138-151) | PARTIAL | TIPS:139-150; MAIN:464-481 | All 12 keys resolve to real widgets. Mismatches: save says ".txt" vs `.md` default; "Target asset in scope" implies validation; no tooltips for Severity Target, Scan now, Watchlist…, Use in report |
| 82 | Architecture: `agents/bug_spray` = scanner + feed + `BugBountyAgent` (README:208-209) | IMPLEMENTED | `agents/bug_spray` tree; MAIN:68, 293 | Matches, minus the missing files in #77-79 |
| 83 | Bug Spray's own suite is separate from Sentinel's (README:323) | IMPLEMENTED | `pytest.ini:2` (`testpaths = tests`) | 44 test functions in `agents/bug_spray/tests` (TODO.md:86 says 52 cases) |

## 4. Findings by category

### (a) Promises in one source but not the other

**In UI / tooltips / training, absent from `docs/agents/bug_bounty.md`:**
- Program radar details: platform menu, Show all, Watchlist…, Full details…, double-click, Full re-scan, Recent changes / Programs tabs, "not authorization" reminders, 60-minute default in `config.json`, `bugspray` terminal command, "automatic scans stop when closed" (all TRAIN:10-30; FEEDUI).
- The always-visible "Program and Scope are declared by you and are not verified or enforced" caption (PANEL:88-95) and the nmap "no scope check / outside the guard" caption (PANEL:122-129). AGENT-DOC only has a generic warning (:5).
- The **Severity Target** control (PANEL:82-86, TRAIN:69/78); AGENT-DOC Inputs table omits it, and it does nothing.
- Named tiles "Severity / CVSS Score / Bounty Estimate", Auto-route, Inspector cost line (TRAIN:84-95).
- "CVSS and CWE ... marked for review" (TRAIN:53, 88) which the UI never shows.
- Tooltips: "Kill the running Nmap process", "Save ... .txt", "HackerOne-ready" (TIPS:145, 149, 147).

**In `docs/agents/bug_bounty.md`, absent from UI / training:**
- "Collapsed Nmap controls" (:22) and a "Model override" section (:23); neither exists as described.
- "Save and Clear appear with results" (:24).
- "Requirements: nmap installed locally for the scanner" (:50) with no mention of the nested venv, `main.py`, or that a platform must be enabled in `config.json` before anything is scanned.
- Duplicate "Under the hood" row for `bug_bounty.py` ("Export / reset", :42).
- "Extend it" items (scope-confirmation guard, auto-severity, program templates) are future work and correctly framed; none is implemented.

### (b) Contradictions

1. **`agents/bug_spray/README.md` is missing**, so the requested README-vs-AGENT-DOC comparison could not be done. Dangling references: FEEDUI:434 ("see its README setup instructions"), `pyproject.toml:5`, PKG/cli.py:13 ("honest-boundary section in README.md"), SRC/__init__.py:20 (`SUGGESTIONS.md "Recon automation"`, also not in this tree).
2. **ROADMAP:62 and :78 vs code**: "HackerOne adapter currently raises `NotImplementedError`" and "unimplemented source adapters" are false (grep `NotImplementedError`: none; five real adapters). ROADMAP:15 "five small tests" is stale (44 functions, 52 cases per TODO.md:86). ROADMAP:78 also lists "Keychain-only secrets" as a feature to test, but the Keychain code is unused.
3. **TRAIN:92-95** "radar and typing are local ... only Analyse leaves the device" vs scans contacting five platforms (TRAIN:18-19 itself says the scan reads public directories).
4. **TRAIN:45** "use conservative timing" vs the app's own default and placeholder `nmap -sV -sC -T4 --open` (PANEL:107, 254).
5. **TRAIN:53-54 / 88-89** "CVSS and CWE marked for review" vs no marker (#45).
6. **TRAIN:69, 77-78** Severity is an input vs dead control (#34).
7. **TRAIN:15-17 / README:18** background scan after opening vs `enabled_platforms=[]` default (`config.py:22-24`, "ships empty") and no first-run prompt (#12).
8. **AGENT-DOC:33** "while Sentinel is open" vs timer starting only on first panel show (FEEDUI:291-297).
9. **Command name**: TRAIN:23 `bugspray` vs `pyproject.toml:13` `bug-spray` vs PKG/cli.py:3-10 `python main.py`.
10. **TIPS:149** "Save ... to a .txt file" vs PANEL:426-430 default `.md`.
11. **README:196** generated commands need human review before execution vs auto-built nmap command executed in one click (PANEL:246-266).
12. **README:100-103** background work is shut down on close vs no `shutdown()` for the Bug Spray panel (#16).
13. **AGENT-DOC:22-23** collapsed Nmap / Model override vs PANEL:99-137, 163-169.
14. **AGENT-DOC:11-12 vs :50** "scanner" used for both the directory scanner and the nmap runner.
15. **Prompt vs UI**: the system prompt (AGENT:5) says the model only analyses targets "the user has explicitly stated are in-scope", but the UI collects no such statement; the model sees only Program and Target text.
16. **MANUAL:128** "Missing scope/target information blocks or warns" vs only an all-empty check (#55); **MANUAL:130** "evidence placeholders" vs prompt (#50).

### (c) Undocumented behaviour

- **First scan per platform is a silent baseline**: no change events are produced (PKG/cli.py:161-168); Recent changes stays empty after the first scan.
- **Mass-disappearance guard** (>50% missing => not marked gone, PKG/cli.py:36, 170-177) and per-program fallback to the stored copy exist, but their warnings are stdout/stderr only and the panel drops them (FEEDUI:445-450, 333-335).
- **Scan outcome is mostly invisible**: stdout JSON is drained and discarded (FEEDUI:445-446); on non-zero exit the status becomes "Scan finished with errors." plus the stderr tail or "See the scan summary above." (FEEDUI:456-459), pointing at a summary that is never shown, and it overwrites the per-platform error list that `refresh()` had just written.
- **Retry behaviour**: a scan that records a result (even all-platforms-failed) waits a full interval; a scan process that crashes before recording is retried on every 60 s tick with no backoff (FEEDUI:402-428). A running scan cannot be cancelled from the UI.
- **Unofficial endpoints**: HackerOne uses the website's GraphQL, others use undocumented "public" JSON; the User-Agent identifies the tool and a GitHub URL (SRC/_http.py:17). Terms-of-service and shape-change risk are unmentioned in user docs.
- **Persistence**: every completed analysis writes the full report to Saved Chats / run log / usage DB (MAIN:3639-3685) with the target as the user turn; "Save" is a separate action. Docs never say reports are stored automatically.
- **No redaction**: findings and nmap output go verbatim to the selected (possibly cloud) model; no secret scrubbing or prompt-injection hardening, although findings are pasted from untrusted targets.
- **Budget/consent estimate uses the target string only** (#56); auto-route also decides on that short text (BASE:370-387), while `routing_text()` for the Auto-route button reads every line edit and the findings box (BASE:229-240).
- **Nmap**: no PATH handling, unbounded output, one leaked `QProcess` per run (parented to the panel), command line persisted in the visible field after auto-build, no confirmation.
- **Data location**: config, DB and lock live under `agents/bug_spray/` (PKG/config.py:14-15, 33-40; cli.py:226), outside `runtime_paths` and portable/Emergency Reset handling.
- **Intigriti edge**: a program that becomes login-walled later is re-stored with an empty scope, which the diff reports as every asset removed (SRC/intigriti.py:143-145).
- **YesWeHack**: pausing is indistinguishable from delisting (listing filter drops it, `active=True` always).
- **Interval not exposed in UI** (only `config.json`), and the file's location is not stated in the lesson.
- Window-level Stop picks the first running panel in dict order (MAIN:3950-3953), not necessarily Bug Spray.

## 5. Must fix before test phase

P0 (tests cannot meaningfully pass or run without these):
1. **Radar cannot start**: restore `agents/bug_spray/main.py`, `README.md` and a documented venv bootstrap (or launch `python -m bug_spray.cli` with a configurable interpreter), and fix the dangling README pointers (#77-79).
2. **Nothing scans by default**: ship a default `config.json` enabling the platforms, or show "No platforms enabled: open Watchlist…" in the radar status and the lesson (#12, #13).
3. **CVSS tile reads the version**: make the regex skip `v3.x` / `CVSS:3.x` tokens (e.g. require "score" or take the vector-adjacent base score) and add tests for `CVSS v3.1: 7.5` and `CVSS:3.1/AV:N/...` (#42).
4. **Prompt/parser mismatch**: change the prompt to request `## Proof of Concept` and `## Remediation` headings (or teach the parser the numbered-bold format and stop truncating on `###`), with a test built from the prompt's own format (#39).
5. **Budget gate sees only the target**: pass the full outgoing text (system plus user messages) to `authorize()` so estimate, consent and budget reflect real size (#56).
6. **Nmap robustness**: add an `errorOccurred` handler, a QTimer timeout that kills, an output cap, PATH augmentation for GUI launch, and move `[Error]/[Running]/[Done]` out of the buffer that feeds the model (#67, #69, #70).
7. **Scope-gate story is inconsistent**: either add a pre-flight confirmation (authorised-program attestation before nmap and before Analyse, plus a refuse/warn on empty Program) or rewrite ROADMAP:62 P0 as "documented as unenforced" so the test phase has a truthful oracle (#51, #53-55).

P1:
8. **Dead Severity Target**: pass it into `build_messages` or remove it and fix the worked example (#34).
9. **Stop semantics**: ignore late `finished_signal` after cancel and keep "Stopped." instead of "[Error] Request cancelled" (#37).
10. **False "Gone" events under `--full`**: keep `known` as the failure fallback and add a separate "force refresh" flag to adapters (#14).
11. **Surface scan results**: stop discarding stdout, show per-platform errors and the >50% guard note, do not overwrite the refresh status with the generic message, add backoff for failed spawns (#13, #30).
12. **Shutdown contract**: add `shutdown()` to the Bug Spray panel/feed that kills nmap and the scan `QProcess` (#16, #68).
13. **History fidelity**: pass `messages` to `record()` or document that only the target is stored (#57).
14. **Doc corrections**: AGENT-DOC (collapsed Nmap, Model override, Save/Clear, Inputs incl. Severity, requirements/venv/config), TRAIN (`bugspray` name, "radar is local", Bounty tile, review marker, `-T4`), ROADMAP (adapters are real, test counts), TIPS save ".txt" (#10, #35-38, #58, #75).
15. **Packaged/portable**: route scanner, config and DB through `runtime_paths` or state in README that the radar is dev-checkout only (#80).

## 6. Count verification

grep of the status column of section 3: IMPLEMENTED 40, PARTIAL 27, STUB 1, MISSING 10, NOT CODE-VERIFIABLE 5 = 83 rows (row numbers 1-83, none skipped).
MISSING rows: 35, 45, 53, 54, 67, 69, 77, 78, 79, 80. STUB row: 34. NOT CODE-VERIFIABLE rows: 44, 48, 49, 60, 72.

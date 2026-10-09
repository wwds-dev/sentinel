# Sentinel - Shared Controls: Requirements-Traceability Audit

Repo audited: `/home/claude/wwds-dev/sentinel` (read-only; nothing was run, modified or committed; pytest was not run).
All evidence paths below are relative to that root. Line numbers are from the checkout as read on 2026-10-09 (VERSION 2.002).
Status vocabulary: IMPLEMENTED / PARTIAL / STUB / MISSING / NOT CODE-VERIFIABLE. "(visual confirm)" in a Note means the code is present but only a run on macOS can prove what the user sees.

## 1. Verdict

The shared controls are substantially built and the routing, pricing and guard core is faithful to the README: whole-word classification, the 20-point cheapest-within-margin rule, a single price-resolution path for bill and router, Decimal session/daily budget checks, one-time consent for unticked providers, ordered DB migrations with a pre-migration backup, a tightly guarded portable Emergency Reset, and a complete Learning Centre whose 18 files and 9 images all exist. It is not yet ready for a test phase, because several promises that a tester can see or a user can be hurt by do not hold. The BEST FIT reason and the "costs money" hover text almost certainly never display, since `setToolTipsVisible` is never called. The LMArena fetch runs automatically at every start with no consent or opt-out, ignores Local only mode and, on a network-denied machine, retries on every launch for up to about two minutes. The guard order differs between Chat (permission, then budget, with no key check) and the panels (key, consent, then budget), and nothing at `run_backend` enforces it, although no actual bypass was found; budget maths is Decimal only in the session and daily comparison and float everywhere else, and nan, inf, negative and zero values are accepted in every numeric field. A zero price can still bill as EUR 0.00, Local only silently sends the selected cloud model name to Ollama, a blank `DASHSCOPE_BASE_URL` in the seeded `.env` most likely breaks Qwen, the Red theme turns the OK light the same red as the alert light, the tray shows Idle while any panel agent is running, and quitting during a model scan does not stop that worker. The README is wrong that the `projects` table is unused (create, list, filter and set-active all have UI; only archive does not) and that Settings holds provider permissions (they are session-only checkboxes in Chat Options), and the Speed first, Settings -> Pricing "add a price" and "selection never changes" statements in the training docs contradict the code. Of 137 traced promises, 102 are implemented, 31 partial, 1 stub, 1 missing and 2 not verifiable without a macOS run; 19 must-fix items are listed in section 5.

## 2. Counts

| Status | Count |
|---|---|
| IMPLEMENTED | 102 |
| PARTIAL | 31 |
| STUB | 1 |
| MISSING | 1 |
| NOT CODE-VERIFIABLE | 2 |
| **Total promises traced** | **137** |

| Scope area | Rows | IMPLEMENTED | PARTIAL | STUB | MISSING | NOT CODE-VERIFIABLE |
|---|---|---|---|---|---|---|
| 1 Provider/model selection, Auto-route, BEST FIT | 21 | 15 | 6 | 0 | 0 | 0 |
| 2 Pricing and budgets | 23 | 18 | 5 | 0 | 0 | 0 |
| 3 Model Updates | 13 | 9 | 4 | 0 | 0 | 0 |
| 4 API keys | 9 | 7 | 2 | 0 | 0 | 0 |
| 5 Settings dialog | 19 | 12 | 5 | 1 | 1 | 0 |
| 6 Rails and cards | 11 | 8 | 3 | 0 | 0 | 0 |
| 7 Themes and vibe | 7 | 6 | 1 | 0 | 0 | 0 |
| 8 Menu-bar tray | 7 | 5 | 2 | 0 | 0 | 0 |
| 9 Learning Centre | 12 | 9 | 2 | 0 | 0 | 1 |
| 10 Persistence and launch modes | 15 | 13 | 1 | 0 | 0 | 1 |

Other tallies: 16 contradictions (section 4b), 21 undocumented behaviours (4c), 11 one-sided promise groups (4a), 19 must-fix items (section 5).

## 3. Traceability table

Source tags: README = README.md; QS/WS/CS/PC/AK/PT/TS = docs/training quick_start / workspace / controls_settings / privacy_cost / api_keys / portable / troubleshooting; PM = docs/portable_mode.md; VER = docs/versioning.md; TT = ui/tooltips.py.

### Area 1 - Provider/model selection, Auto-route, BEST FIT

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 1 | Provider and model controls stay explicit; model list follows provider (README "Using the app") | IMPLEMENTED | main.py:3051 `load_models`, 1107 `models_for_provider`; ui/widgets.py:135-284 `MenuComboBox` | TT provider tooltip (ui/tooltips.py:19) names only four cloud providers; Kimi and Qwen missing. |
| 2 | Every agent, Chat included, has an Auto-route button next to its run controls (README) | IMPLEMENTED | main.py:2123-2133 (Chat); ui/panels/base.py:212-220 via `build_run_bar`, used by bug_bounty.py:164, manager.py:71, osint.py:113, osint_heavy.py:188, sentry.py:130, vpn.py:334, wifi.py:175 | All eight agents covered. |
| 3 | Auto-route applies a provider and model directly to the widgets (README; CS:11) | IMPLEMENTED | main.py:3071-3084 `auto_route_agent`, 3141-3151 `_apply_route_to_widgets`; ui/panels/base.py:242 | Candidate set is providers whose permission box is ticked (main.py:3086-3092), whereas BEST FIT uses providers that have keys (main.py:1262-1270). With nothing ticked, Auto-route can only return Ollama. |
| 4 | Requests classified on whole words: "withdraw" is not "draw", "postcode" is not "code" (README) | IMPLEMENTED | services/model_recommendations.py:206-216 `_words` (lookaround on [a-z0-9]), 218-244 patterns, 247-275 `classify_request` | Matched text is `f"{agent} {tool} {prompt}"` (line 248) so the agent keys "wifi"/"vpn" always hit `_CODING` (line 238). Ordinary words ("class", "function", "simple", "long", "sources") trigger task classes. Task set is wider than README's six (adds simple, summarize, long_context, image_generation). |
| 5 | Prompt size = characters / 4, the same figure the cost estimate uses (README) | IMPLEMENTED | services/usage_tracker.py:7-14 `estimate_prompt_tokens`; main.py:523, 3105-3120 | Measures the prompt only. Chat history (`prior`, main.py:3417) and system prompt are not counted, so both context pruning and the cost estimate under-count in long conversations. |
| 6 | Models whose context window cannot hold the prompt are dropped (README) | IMPLEMENTED | services/model_recommendations.py:278-286 `_compatible` (`cap.context_window < request.context_tokens`); line 273-274 switches >100K tokens to long_context | See #5 for the under-count. |
| 7 | Ratings looked up on the public LMArena leaderboard for that kind of work (README; AK:117-120) | IMPLEMENTED | services/benchmarks.py:105 `arena_key`, 142 `RatingTable`; services/model_recommendations.py:419-433 `_rate`, 519-531 | Ollama models are never rated (documented AK:147-151). |
| 8 | Cheapest model within 20 points of the best wins (README; AK:121-125) | IMPLEMENTED | services/model_recommendations.py:418 `RATING_MARGIN`, 437-478 `_route_by_rating` (fit list then `min` by price) | Undocumented: task "simple" widens the margin to 50 (`SIMPLE_TASK_MARGIN` line 419, applied 442-443). Unknown-price models sort last (`_price_key` 477-479). |
| 9 | Cost first = cheapest within 50 points; Quality first = best-rated (CS:33-34; AK:126-128) | IMPLEMENTED | services/model_recommendations.py:418 (`cost: 50`, `quality: 0`) | Quality ties are broken by price (line 449). |
| 10 | Speed first = "Sentinel's own scoring, weighted towards fast models" (CS:35) | PARTIAL | services/model_recommendations.py:418 lists `"speed": 20` in `RATING_MARGIN`; route_request takes the rating path at 519-531 whenever ratings exist (the snapshot always provides them); the latency weight in `_score` (`prefs.priority == "speed"`, line 300) is reached only with no ratings | Doc contradicts code: in practice Speed first behaves exactly like Balanced. |
| 11 | Privacy first = own scoring weighted towards local models (CS:36; AK:149-151) | IMPLEMENTED | `"privacy"` is absent from `RATING_MARGIN` (line 418), so route_request falls through to `_score` (533+; `cap.privacy * 30` at line 302) | Needs local models present in `available`. |
| 12 | An unrated model is never chosen over rated ones, except Local only / Privacy first (README; AK:147-151) | IMPLEMENTED | services/model_recommendations.py:445-446 (pool = rated only), 467-470 (reason names the unrated count), 519-531 | Local only works because Ollama has no ratings, `rated` is empty and `_score` decides. |
| 13 | Execution mode (Local only / Hybrid / Cloud only) is honoured (README; CS:25) | PARTIAL | Auto-route honours it: `_compatible` 278-283, `_routing_preferences` main.py:3094-3103. Manual send does not: `resolve_backend_model` main.py:3226-3227 returns `("ollama", model_box text)` whatever the provider box shows; mode change only refreshes the estimate and label (main.py:2363-2364) | Default mode is "Local only" (first item, main.py:2156). Selecting a cloud provider under the default mode silently sends the cloud model name to Ollama. |
| 14 | A blocked provider produces a clear message | PARTIAL | `resolve_backend_model` raises `RuntimeError("... Tick the checkbox first")` (main.py:3237, 3246); called at main.py:3360, outside the `try` that starts at 3402, and on every keystroke via `get_current_cost_estimate` (main.py:536-551) | Uncaught exception in a Qt slot: user sees nothing. The "checkbox" lives only in Chat Options -> Paid provider access (main.py:2263-2269). |
| 15 | BEST FIT badge sits next to the entry in the dropdown (README; CS:14) | IMPLEMENTED | ui/widgets.py:33, 62-64 `markBestFit`, 106-132 pill painting, 189-194; main.py:1448-1535 (`refresh_recommendation_marks` 1503) | One badge per dropdown. NEW pill for unreviewed models (widgets.py:67-71) is documented only in CS:15. |
| 16 | BEST FIT reason shows on hover (README; CS:14) | PARTIAL | Tooltip is set on the `QAction` (ui/widgets.py:208-217) but `setToolTipsVisible` is called nowhere (grep of all *.py: 0 hits); Qt's `QMenu.toolTipsVisible` defaults to false; `setStatusTip` has no status bar because `GodAI` is a plain `QWidget` (main.py:218) | Code-evident that action tooltips never appear (visual confirm). This is the single most visible promise miss in this area. |
| 17 | Any non-Ollama selection turns the control amber (README; PC:12) | IMPLEMENTED | main.py:1399-1415 (`paidSelection` property at 1412); ui/style.py:138-140; tests/test_ui_panels.py:149,217 | Amber (hue 44 deg) is outside the theme band so it survives every theme (ui/theme.py:14, 53). |
| 18 | Every cloud entry says "this one costs money" on hover (README; PC:13-14) | PARTIAL | Text built at ui/widgets.py:212-213, `COST_ROLE` set main.py:1405-1408 | Same tooltip-visibility defect as #16. |
| 19 | BEST FIT = same assessment per agent over providers with keys; badge moves, selection never changed behind your back (AK:132-136; CS:14) | PARTIAL | Derivation: services/model_recommendations.py:603 `derive_agent_recommendations`; main.py:1271-1296 | Badge-only is true for ratings/price changes. But `_apply_adoptions` (main.py:1347-1358) switches an agent to its new BEST FIT after an Update click, and `install_agent_recommendations` (main.py:1595) preselects at start. Docs say "never". |
| 20 | Reason names the ratings and prices compared (CS:14) | IMPLEMENTED | services/model_recommendations.py:460-466 reason strings; main.py:1516-1525 tooltip body | Price quoted is blended 3:1 input:output (price_resolution.py:37-38). |
| 21 | Recommendation list differs by priority; "priority also decides every agent's BEST FIT" (CS:38) | IMPLEMENTED | main.py:2887-2898 `_routing_priority_changed` (re-derives, re-marks, saves to config/settings.json) | Saved to JSON, not the DB; in a read-only install the failure is only logged (`_note_failure`). |

### Area 2 - Pricing and budgets

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 22 | Price resolution: own row -> dated-snapshot parent -> provider default (README; CS:73-76) | IMPLEMENTED | services/price_resolution.py:41-66 `resolve_price`; services/model_watch.py:80-92 `canonical`; bill uses it at usage_tracker.py:~104; router via `table_lookup` price_resolution.py:79-90 | One code path serves bill, pre-flight estimate and router, as the docs claim. |
| 23 | Provider default row holds the dearest current rate (README; CS:75-77) | IMPLEMENTED | services/database.py:~397-404 `DEFAULT_PRICES`, 417-432 `PRICING_CORRECTIONS_2026_10`, ~436-476 flagged one-time correction | Corrections touch only rows still holding the old value, so user edits survive. |
| 24 | A rate of zero means unknown, never free (README; CS:77; AK:140-143) | PARTIAL | `_positive` (price_resolution.py:29-34) ignores zero rows. But `calculate_cost_eur` ends `if not row: return 0.0` (services/usage_tracker.py:~107) | If the user zeroes a provider's `default` row in Settings -> Pricing, every unpriced model bills and budget-checks at EUR 0.00: unknown becomes free in the guard. |
| 25 | A cloud model with no usable price is never treated as cheap by the router (README) | IMPLEMENTED | services/model_recommendations.py:477-479 `_price_key` (None sorts last); main.py:259 note | |
| 26 | EUR/USD rate converts USD prices (CS:42) | IMPLEMENTED | services/usage_tracker.py:~98-120; ui/dialogs.py:440-442, 1147-1150 | No validation: rate 0, negative, nan or inf is saved. Rate 0 makes every cost EUR 0, so no budget ever binds. |
| 27 | Kimi cached-input priced separately, roughly 80% cheaper (README:53) | IMPLEMENTED | services/usage_tracker.py:37-66 `cached_input_tokens`, 117-123 billing; services/database.py:615-636 seeds; config/pricing.json kimi rows | Actual discount per row: k2.7-code 80%, k2.6 83%, k3 and default 90%. Seeds only UPDATE rows that exist; an upgraded DB lacks the kimi-k3/k2.x rows (only `kimi/default` is seeded, database.py:600-601) and bills them at default. |
| 28 | Pre-flight estimate uses the same row as the final bill (CS:78-79) | IMPLEMENTED | main.py:514-534 `estimate_chat_cost` -> `calculate_cost_eur` | Output tokens are assumed `max(250, 1.2 x input)` (line 524), an undocumented heuristic. Ollama estimate is 0.0. |
| 29 | Permissions checked before a request (README:53) | IMPLEMENTED | services/validator.py:67-74; main.py:3364-3386 (Chat), 3572-3607 (panels) | See guard-order analysis (rows 30-32 and Section 3.1). |
| 30 | Guard order, Chat path `send_prompt` | IMPLEMENTED | main.py:3360-3400. Order: resolve backend -> estimate -> Validator [agent enabled (validator.py:39) -> tool enabled (43) -> agent allows provider (47) -> tool allows provider (54) -> agent allows tool (61) -> permission checkbox (68-74) -> agent cap (77-84) -> tool cap (87-94) -> session budget (97-104) -> daily budget (107-114) -> approval flags (117-127)] -> confirm dialog (main.py:3388) -> memory pre-flight (3399) | No API-key check anywhere on this path. A ticked provider with no key passes every gate and fails inside the client. |
| 31 | Guard order, panel agents `authorize_request` | IMPLEMENTED | main.py:3570-3613. Order: estimate -> if cloud and permission unticked: key check (3578) -> consent dialog (3585) -> one-time permission on a dict copy (3572, 3591) -> Validator (same list as #30) -> second confirm dialog only when no one-time consent (3609) | Consent dialog is shown BEFORE the budget check, so a user can approve and then get "Request Blocked". Chat and panels order their gates differently; key check exists only for panels and only when the box is unticked. |
| 32 | No path bypasses the guard | PARTIAL | Only two call sites reach `run_backend`: main.py:3450 (after `send_prompt` gates) and ui/panels/base.py:422 (`start_worker`); each of the seven panels has exactly one `authorize(` and one `start_worker(` (grep counts) | No bypass found today. Structurally the guard is by convention: `run_backend` (main.py:3733) and `start_worker` enforce nothing, and `start_worker` does not assert that `authorize()` ran. |
| 33 | Session budget checked before request, Decimal maths (README:53) | IMPLEMENTED | services/validator.py:21-24 `_money`, 96-104; inputs main.py:3599-3602 | Decimal only covers the subtraction/compare; `session_cost_total` is a float accumulated with `+=` (main.py:3660, 3865) and `get_today_total` returns `round(float(SUM),6)` (usage_tracker.py:~163). |
| 34 | Daily budget checked before request | IMPLEMENTED | services/validator.py:106-114; services/usage_tracker.py:~157-168 (`timestamp LIKE 'YYYY-MM-DD%'`) | Local calendar day, matches the "resets at midnight local time" tooltip (ui/tooltips.py:70). |
| 35 | Per-agent and per-tool caps apply per paid request (CS:53-58) | IMPLEMENTED | services/validator.py:76-94 | Float comparisons, not Decimal. Code comment at line 76 says "(daily)" but the check is per request. A "nan" cap typed in Settings is stored as NaN, which SQLite keeps as NULL, i.e. no cap (ui/dialogs.py:1167-1173). |
| 36 | Decimal vs float in budget maths | PARTIAL | Decimal: validator.py:22-24, 98-109. Float: validator.py:79, 89; main.py:3660, 3865, 4029-4030; legacy `check_budget_before_request` main.py:741-765 (never called - grep shows 0 callers) | Settings/Budget inputs accept "nan": `Decimal("nan")` ordering comparison raises `InvalidOperation` inside `authorize_request`/`send_prompt`. Negative budget blocks all paid requests; "inf" disables budgets. |
| 37 | Usage and cost recorded per run (README:53) | IMPLEMENTED | services/usage_tracker.py:~123-160 `log_request` (INSERT usage); services/run_logger.py:11-40; main.py:3639-3684 `record_request`, 3837-3880 (Chat) | Cancelled or failed cloud requests record no cost although the provider may bill them (disclosed CS:50-51). |
| 38 | Cost history with CSV export (CS:83) | IMPLEMENTED | ui/dialogs.py:62-212 (export 154-206) | Dialog is a snapshot taken at open (line 63); table shows last 200 rows only (119) and costs with `.2f` (130), so sub-cent requests read EUR 0.00; provider filter list hard-coded (74-77); file write has no try/except (177). |
| 39 | Run log shows agent, route, status, tokens, duration, cost and errors (CS:83-84) | PARTIAL | ui/dialogs.py:215-300; error text written only by the Chat path (main.py:3910 `error=error`); panel failures go through `abandon_request` (main.py:3687-3692) which stores status=reason and no message | Rows stay "running" forever after a quit mid-run (closeEvent does not close `active_run_id`). Error text is injected unescaped into HTML (dialogs.py:273). |
| 40 | Logs "may contain sensitive task labels" (CS:85) | PARTIAL | services/run_logger.py:21 stores `prompt_summary[:200]`; callers pass the full prompt (main.py:3446, 3634) | Docs understate: the DB holds the first 200 characters of every prompt, not labels. Privacy gap in PC/CS wording. |
| 41 | Budget card: light amber at 60%, red at 90% of a cap (WS:48-50) | IMPLEMENTED | main.py:4032-4054 (thresholds at 4041, 4053) | Cap shown with `.0f` (main.py:4045): a EUR 0.50 cap reads "EUR 0". A zero cap shows 0% green while every paid request is blocked (4040). |
| 42 | Budget inputs persist (Save budget) | IMPLEMENTED | main.py:767-779 `save_budget_limits` (parse before write) | Accepts negative/nan/inf. |
| 43 | Reset session spend | IMPLEMENTED | main.py:781-786 | Resets the in-memory counter only; the daily total still binds because it is read from the usage table. |
| 44 | Cost card shows latest and session totals (WS:46-47) | IMPLEMENTED | main.py:4012-4026 | Also shows today and request counts (undocumented). Two decimals only. |

### Area 3 - Model Updates

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 45 | Asks every keyed provider for its live model list at each start or Check now (README; AK:90-98) | IMPLEMENTED | main.py:359 (`QTimer.singleShot(4000, ...)`), 1178-1187; ui/workers.py:616-652 `ModelScanWorker`; services/model_watch.py:124-156 `list_live` (no-key providers skipped, lines 133-139) | Runs 4 s after launch, one provider at a time (documented). Not gated by execution mode or permission boxes. |
| 46 | LMArena fetch needs no consent and cannot be turned off | PARTIAL | ui/workers.py:~640-652 gates only on `benchmarks.is_stale` then calls `benchmarks.refresh`; services/benchmarks.py:49 (huggingface.co), 320 `urlopen(timeout=30)`; no setting, no mode check | A third-party request (to Hugging Face) happens on every start even in Local only and with every permission box off. PC/privacy docs never mention it; only AK:156-160 does. |
| 47 | Behaviour on a network-denied machine | PARTIAL | services/benchmarks.py:298-330 `_with_retries` (3 attempts, 10 s / 20 s sleeps, 429 aborts at once); failure returns a string from the worker (workers.py:~650) and nothing is written (main.py:1195-1197 only prints) | Card shows "Checking..." (main.py:1182) for up to ~2 min per start. Because `is_stale` stays true, the fetch retries on every start and every Check now. App stays usable (worker thread) and falls back to snapshot. |
| 48 | Ratings refreshed at most daily (README) | IMPLEMENTED | services/benchmarks.py:55 `MAX_AGE`, 343-356 `is_stale`, 359 `refresh` | Bound applies to successful refreshes. Check now is gated by staleness too, so it does not force a re-fetch. |
| 49 | Shipped snapshot `config/lmarena_snapshot.json` is used until a fetch lands (README) | IMPLEMENTED | services/benchmarks.py:54, 334-340 `load`; config/lmarena_snapshot.json present; main.py:261, 1251-1257; refresh via `--snapshot` at benchmarks.py:378 | |
| 50 | New models appear as clickable rows (README; AK:99-102) | IMPLEMENTED | ui/model_updates.py:56-168, 198-253 `show_state`; services/model_watch.py:442-491 `record_scan` | First scan is a baseline: only successors of known models are flagged (line 475-477). |
| 51 | Update brings in only the selected models (README; AK:100-102) | IMPLEMENTED | ui/model_updates.py:137-168 (`marked`, `_emit_update`); main.py:1301-1330 loops `marked` only; services/model_watch.py:523-531 `adopt` | |
| 52 | Adopted model is priced from Settings -> Pricing; user can "add its price" (AK:104-110, 140-146; CS) | PARTIAL | services/model_watch.py:544-566 `install_adoption` writes no pricing row; Settings -> Pricing lists only existing rows (ui/dialogs.py:568-612, no add/remove control; grep "Add" in that block: none) | An adopted model bills at provider default (safe, dearest) but the documented remedy (add its own price) cannot be done in the UI. |
| 53 | Dismiss stops a model being listed as new (AK:162) | IMPLEMENTED | services/model_watch.py:517-521; ui/model_updates.py:276-370 `ModelReviewDialog` | |
| 54 | Card says which ratings copy is in use and when published (AK:158-160) | IMPLEMENTED | main.py:1220-1250 `refresh_model_updates_card` / `set_ratings` | |
| 55 | Hover on Last check lists providers checked/skipped (AK:167-168) | IMPLEMENTED | ui/model_updates.py:41-55 `describe_notes`; services/model_watch.py:442-491 | |
| 56 | Notice when a new model arrives | IMPLEMENTED | main.py:1205-1214 | Undocumented chat-status banner. |
| 57 | Quitting while a scan runs is safe | PARTIAL | `closeEvent` (main.py:4778-4788) stops only `chat_worker` and panels; `model_scan_worker` (main.py:1178-1187) and `muse_pull_worker` (main.py:1070-1074) are never stopped or waited | The scan can run for ~2 min from 4 s after launch; destroying a running QThread aborts Qt ("QThread: Destroyed while thread is still running"). Not run-verified. |

### Area 4 - API keys

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 58 | Keys come from the process environment or a `.env` in project / Application Support / portable Sentinel Data; real env wins (README:40; AK) | IMPLEMENTED | main.py:31-33 `load_dotenv(user_data_base() / ".env")` (override=False); services/runtime_paths.py:128-138 `user_data_base` | Exactly one `.env` file is read. |
| 59 | Provider keys read once, at startup (README:40) | IMPLEMENTED | main.py:33 (import time); clients read `os.getenv` in `__init__` (e.g. services/qwen_client.py:35); card computed once main.py:2674-2686 | OSINT "Save Key" sets `os.environ` immediately (ui/dialogs.py:667) - inconsistent with "read once" but only for OSINT keys. |
| 60 | First run in frozen/portable mode seeds `.env` from `.env.example` without overwriting | IMPLEMENTED | services/runtime_paths.py:141-171 (copy at 165-170, existing files skipped at 154-155) | Copies the blank `DASHSCOPE_BASE_URL=` line (see #62). Newly shipped keys in config files are never merged into existing user copies. |
| 61 | API Keys card lists six providers, "ready"/"no key", header N/6 (WS:18-21) | IMPLEMENTED | main.py:2666-2703; ui/widgets.py:799-833 `KeyValue` | Row tooltip says "found in .env" even when the key came from the process environment (main.py:2686). |
| 62 | Qwen works when `.env.example` is copied unchanged | PARTIAL | `.env.example:16` ships `DASHSCOPE_BASE_URL=` (blank); `load_dotenv` therefore sets it to ""; services/qwen_client.py:17 `os.getenv("DASHSCOPE_BASE_URL", INTL_BASE_URL)` returns "" because the default applies only when unset; used at 35-37 | On a fresh frozen/portable install the user pastes DASHSCOPE_API_KEY, the base URL is empty, and Qwen requests/`check_connection` ("Connected to Qwen ()", line 113) are built on an empty URL. Likely defect (not run-verified). |
| 63 | `.env.example` covers every supported variable (README:42-49; AK) | PARTIAL | `.env.example:5-9, 12, 16, 21-115` cover 5 provider keys, DashScope key/base, 22 OSINT keys; services/gemini_client.py:12 also reads `GEMINI_API_KEY` | `GEMINI_API_KEY` alias is documented only in AK, not in README or `.env.example`. Header lines 1-3 still say "Sentinel Fork" and the old Application Support path. |
| 64 | `.env` and credentials are never committed (README:293) | IMPLEMENTED | .gitignore: `.env`, `*.key` | `.env.example` is tracked by design. |
| 65 | OSINT keys written to the same data root, mode 600 | IMPLEMENTED | ui/dialogs.py:374-378 `_osint_env_path`, 637-667 (`chmod 0o600` at 662) | In portable mode the file lands on the volume. |
| 66 | Key present is not permission (PC:25-26) | IMPLEMENTED | `key_available` on each client; permission boxes main.py:2170-2181; `provider_key_available` main.py:3518-3528 | |

### Area 5 - Settings dialog

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 67 | Settings reachable from Actions card / More menu | IMPLEMENTED | main.py:2655-2660, 559; ui/dialogs.py:405 `show_settings` | |
| 68 | General tab: EUR/USD rate, default session and daily budgets (CS:42-44) | IMPLEMENTED | ui/dialogs.py:440-450, 1146-1160 | Labelled "Default ... budget" but saving also overwrites the live `app.session_budget_eur` (1153). |
| 69 | Malformed input must not overwrite good values - General | IMPLEMENTED | ui/dialogs.py:1146-1160: all three floats are parsed before any `save_setting`; ValueError appends an error and writes nothing | "Valid" means any float: nan, inf, negative, zero and EUR rate 0 are accepted (see #26, #36). |
| 70 | Malformed input - Agents tab | PARTIAL | ui/dialogs.py:1164-1175: a bad row is skipped entirely (its enabled toggle too) | "nan" parses and stores NULL (no cap); a negative cap blocks the agent. |
| 71 | Malformed input - Pricing tab | PARTIAL | ui/dialogs.py:1188-1202: row parsed fully before UPDATE | Zero, negative, nan accepted; negative price would reduce recorded costs. |
| 72 | Save with errors | PARTIAL | ui/dialogs.py:1215-1219 | Good sections are already committed when "Saved with errors" appears and the dialog stays open; partial save is undocumented. |
| 73 | Theme picker repaints live; Cancel restores (README:82-85; CS:45-48) | IMPLEMENTED | ui/dialogs.py:452-486 (`preview_theme`, `restore_theme` on `rejected`); ui/theme.py:125-132 | `set_current` writes the DB on every preview, so a crash mid-preview leaves the new theme. Note text (dialogs.py:461-465) says "in both themes" though three exist. |
| 74 | Emergency Reset only in portable mode | IMPLEMENTED | ui/dialogs.py:495-507 (visible only if `is_portable()`); services/portable_reset.py:22-24 refuses otherwise | Double guard (UI hidden + function raises). |
| 75 | Emergency Reset needs two confirmations | IMPLEMENTED | ui/dialogs.py:1101-1119: typed phrase `ERASE SENTINEL DATA`, then Yes/No dialog defaulting to No | |
| 76 | Exact deletion boundary (README:96-100; PM:32; PT) | IMPLEMENTED | services/portable_reset.py:22-42: marker file required, data dir must equal `<root>/Sentinel Data` and its parent be root, deletes children only, symlinks unlinked not followed | The `Sentinel Data` folder itself survives. Not atomic and no check that a writer is idle (see #77). |
| 77 | Reset "stops running Sentinel tasks" (CS:92) | PARTIAL | ui/dialogs.py:1121-1123 stops `chat_worker` and calls `shutdown_panels` (52-60) | `ModelScanWorker` / `ModelPullWorker` are not stopped, so a scan finishing after the erase can recreate `data/model_watch.json` / `lmarena_ratings.json`. `_portable_reset_committed` does stop the geometry write (main.py:4914-4917). |
| 78 | Agents tab: enable/disable and optional cap; blank = none; disabling blocks runs (CS:55-58) | IMPLEMENTED | ui/dialogs.py:512-539, 1163-1175; services/validator.py:38-40 | History is untouched. |
| 79 | Tools tab: enable/disable; prompt preview read-only (CS:62-64) | IMPLEMENTED | ui/dialogs.py:541-566, 1178-1184; validator.py:42-44 | |
| 80 | Pricing tab edits USD per 1M input / cached / output (CS:68-71) | IMPLEMENTED | ui/dialogs.py:568-612, 1186-1202 | Edit-only; no add or delete row (see #52). |
| 81 | "Settings controls registered agents, tools, pricing, and provider permissions" (README:78) | MISSING | grep `allow_`/permission in ui/dialogs.py: nothing; checkboxes are hidden (main.py:2170-2181) and appear only in Chat run-bar Options -> Paid provider access (main.py:2263-2269) | Permissions default to unticked each launch (`setChecked(False)` 2178, no `save_setting`). Specialist panels have no Options menu (comment main.py:1993-1996) so for them only the one-time consent dialog exists. README sentence is false. |
| 82 | OSINT Keys tab with registrations and email | IMPLEMENTED | ui/dialogs.py:614-1098; services/osint_keys.py; save at 1205-1209 | Agent-level behaviour belongs to the per-agent audits. |
| 83 | Saving applies at once to the validator | IMPLEMENTED | ui/dialogs.py:1211-1213 rebuilds `Registry`/`Validator` | |
| 84 | Model Guide shows current models, pricing, recommendations (TT:22) | PARTIAL | ui/dialogs.py:1225-1477 | Static, stale text: claude-opus-4-6 "~$15/$75", Sonnet "~$3/$15" (1301-1302), Cloud-only listed with four providers, API checkbox list and System tab omit Kimi/Qwen. Contradicts live pricing table and README. |
| 85 | Realtime monitor button (TT:64 "Coming soon") | STUB | main.py:2549-2556 (`setEnabled(False)`) | Disabled placeholder in the System card. Honest tooltip, but a visible dead control. |

### Area 6 - Rails and cards

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 86 | Right rail order: Current Route, Cost, Budget, System (README:78; WS:44-51) | IMPLEMENTED | main.py:2704-2707 | |
| 87 | Left rail: API Keys, Model Updates, Actions (README:78; WS:14-25) | IMPLEMENTED | main.py:2708-2714 (agent list sits above them) | |
| 88 | Every tile is a ScreenCard: header strip, status light, short status (README; WS:14-17) | IMPLEMENTED | ui/widgets.py:835-910; ui/style.py:845-854; cards built at main.py:2532, 2561, 2586, 2612, 2643, 2666; `ModelUpdatesCard(ScreenCard)` model_updates.py:56 | |
| 89 | "Green means fine, amber means look at it, red means act" (WS:16) | PARTIAL | ui/style.py:852-854 (ok `#3cff88`, alert `#f85149`); ui/theme.py:53, 61, 72-82 | Under the Red theme the band-rotation turns ok `#3cff88` into `#ff473c` (hue 3 deg) while alert `#f85149` (hue 2.7 deg) is outside the band and stays: ok and alert lights, and "API key ready" text (style.py:934-938), become the same red. Computed from the code's own rotation; confirm with a screenshot. |
| 90 | Current Route shows the route intended for the next request; light amber when paid cloud (WS:46-47) | PARTIAL | main.py:941-962 `update_recommendation_label`, 3030-3050, 3122-3139, 3995-4001 `_show_route_screen` | The card is driven by the recommendation / auto-route decision (`Suggested`, `Model`, `Mode`, `Cost` rows), not by the provider currently selected in the dropdown, so it can read "local" while a cloud model is selected. |
| 91 | System card: segmented meters for device load (WS:49-51) | IMPLEMENTED | main.py:3957-4001 `update_resource_label`, timer 4056-4059 (1 s); services/resource_monitor.py | Battery unavailable path handled (3999-4001). Timer runs while the Inspector is hidden. |
| 92 | API Keys header shows N/6 and tooltip | IMPLEMENTED | main.py:2688-2693 | |
| 93 | Model Updates card shows last check, ratings in use, new-model rows (WS:22-24) | IMPLEMENTED | ui/model_updates.py:56-253; main.py:1220-1250 | |
| 94 | Actions card: Costs, Run log, Settings (WS:25) | IMPLEMENTED | main.py:2643-2664 | |
| 95 | Inspector button shows/hides the right rail; Tips toggles hover help (WS:36-37) | IMPLEMENTED | main.py:1998 (connect), 2492 (`toggle_inspector`), 328-333 persisted state, 403-420 tooltip suppression filter | The Tips filter suppresses `QEvent.ToolTip` only; QMenu action tooltips would use a separate path (moot while #16 stands). |
| 96 | Tooltips present for every control (TT) | PARTIAL | ui/tooltips.py:12-165; `_set_tooltips` skips missing widgets silently (main.py:473-481) | 54 bare names checked: `budget_label` and `resource_label` have no widget so their tooltips are dropped; no `allow_qwen_checkbox` tooltip; Gemini tooltip says "free tier available" (TT:29) while every doc says paid; Kimi/Qwen absent from TT:19. |

### Area 7 - Themes and vibe

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 97 | Three themes hue-rotated from one green stylesheet (README:86-93) | IMPLEMENTED | ui/theme.py:34-37, 53 (`_BAND` 80-185), 61 (`_SHIFT` 0 / -140 / +36.6), 72-104; applied main.py:2917-2940 | Docstring (theme.py:1) still says "Two themes". |
| 98 | Semantic colours preserved (danger red, amber, info blue, greys) (README:90-92) | PARTIAL | Amber 43.6 deg, info blue 203.9 deg, danger `#ff6b6b` 0 deg and `#f85149` 2.7 deg all fall outside `_BAND` and pass through | Preserved except that the app's green "ok" colours land on the danger hue under Red (see #89). Blue theme keeps a 24 deg gap to info blue (acknowledged in theme.py:55-60). |
| 99 | Theme dots painted in own accent, ring on current (README:80-82; WS:27-31) | IMPLEMENTED | ui/widgets.py:913-990 (`paintEvent` 960-990); main.py:1675 | Arrow keys also switch theme (1024-1040), undocumented. |
| 100 | Theme persisted and applied at startup; click re-themes the window | IMPLEMENTED | ui/theme.py:112-132; ui/widgets.py:993-1001; main.py:2928 `apply_global_style` | |
| 101 | Rain / hex / grid texture in the empty transcript, stops when content exists (README:93-96) | IMPLEMENTED | ui/vibe.py:185-355; main.py:2460-2465 (`visible_while` = output box empty) | Painting stops, but the 100 ms timer (vibe.py:234-246) keeps calling `toPlainText()` on the whole transcript 10x per second for the life of the app. |
| 102 | Caret blink cadence differs per theme; multi-line composer only (README:96-97) | IMPLEMENTED | ui/vibe.py:45-59 (green 1200 ms square wave, red 1500 ms stutter, blue 1700 ms cosine glow), 151 `install_caret`; main.py:2328 | |
| 103 | HUD corner brackets under blue only, on the focused composer | IMPLEMENTED | ui/vibe.py:364-427 (`_wanted` = BLUE and focus, line 392-394); main.py:2329, 2937-2938 refresh on theme change | Only the Chat composer. |

### Area 8 - Menu-bar tray item

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 104 | Shield glyph that takes the menu bar's colours (README:98-100) | IMPLEMENTED | ui/tray.py:39-50 (`setIsMask(True)`); assets/tray.png 18x18, tray@2x.png 36x36 (shield with an eye cut-out); Sentinel.spec:43-44 | Template colouring on macOS is a visual confirm. |
| 105 | Menu shows whether a request is in flight and which agent runs it | PARTIAL | main.py:4805-4820 `_tray_status` checks only `chat_worker.isRunning()` and `pending_agent` | Panel agents run through `start_worker` / `_pending_requests`, which the status line never consults: it reads "Idle" while Trace, Bloodhound, Sentry, Bug Spray, Tunnel, Beacon or Forge is working. |
| 106 | Menu shows session cost | IMPLEMENTED | main.py:4820; ui/tray.py:86 (rebuilt on `aboutToShow`, not on a timer) | |
| 107 | Open Sentinel brings the window forward | IMPLEMENTED | ui/tray.py:77-79; main.py:4823-4843, 4910 | |
| 108 | Quit cancels in-flight request and shuts background work down | PARTIAL | main.py:4911 -> `window.close()` -> `closeEvent` 4778-4788 (cancel + `terminate()` chat worker, `shutdown_panels`) | Not stopped: `ModelScanWorker`, `ModelPullWorker`; run-log row for the chat run stays "running"; `_pending_requests` not abandoned. `terminate()` is a hard kill. |
| 109 | Closing the window quits the app | IMPLEMENTED | `closeEvent` always accepts (main.py:4788); no hide-to-tray code (ui/tray.py:13-15) | Relies on Qt default `quitOnLastWindowClosed` (visual confirm). |
| 110 | App still starts without a tray / on macOS 27 | IMPLEMENTED | main.py:4908 `tray.available()`; main.py:4869 `appkit_guard.install()` before `QApplication` | The macOS 27 abort fix cannot be verified off-device. |

### Area 9 - Learning Centre

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 111 | Opens from More (...) (README:102-109) | IMPLEMENTED | main.py:2003-2010 ("Learning centre" action), 4706-4708 | Label is lower-case "centre"; docs say "Learning Centre". |
| 112 | Quick Start; workspace and Settings reference | IMPLEMENTED | ui/learning_center.py:48-51 -> docs/training/quick_start.md, workspace.md, controls_settings.md (all exist) | |
| 113 | Courses for all eight agents | IMPLEMENTED | ui/learning_center.py:57-64 -> chat, trace, bloodhound, beacon, sentry, bug_spray, tunnel, forge .md (all exist) | |
| 114 | Privacy/cost, troubleshooting, workflows | IMPLEMENTED | ui/learning_center.py:53-54, 67 -> privacy_cost.md, troubleshooting.md, workflows.md (exist) | |
| 115 | v3 roadmap and testing roadmap | IMPLEMENTED | ui/learning_center.py:68-73 -> `../testing_roadmap.md` (docs/testing_roadmap.md exists), advanced_tools.md; bundled by Sentinel.spec:52-53 | The v3 roadmap entry is titled "Advanced tools", not "v3 roadmap". |
| 116 | Practice exercises | PARTIAL | Exercise/practice sections found in trace.md:55, bloodhound.md:62, beacon.md:62, bug_spray.md:97, forge.md:37, tunnel.md:222-237 (three levels) and chat.md:50 ("Practice") | sentry.md has no exercise or practice section (headings at lines 10, 24, 44, 53 only). No standalone exercises entry. |
| 117 | Current-interface screenshots; every image resolves | PARTIAL | docs/training/images holds 9 PNGs (beacon, bloodhound, bug-spray, forge, learning-centre, trace, tunnel, tunnel-action-preview, workspace-chat); 11 references (docs/training/ beacon.md:5, bloodhound.md:5, bug_spray.md:5, chat.md:6, forge.md:5, quick_start.md:5, trace.md:5, tunnel.md:5 and 177, workspace.md:5, README.md:9) all resolve; tests/test_learning_center.py test_every_training_screenshot_reference_exists | Sentry has no screenshot. `learning-centre.png` is used only by docs/training/README.md:9. Paths are resource-root-relative (`docs/training/images/...`, learning_center.py:305), so they break when a lesson is read as plain Markdown. |
| 118 | Every entry opens; missing resource shows a readable message | IMPLEMENTED | ui/learning_center.py:116-126; tests/test_learning_center.py:17-24; first topic loaded at 332 | |
| 119 | Every link resolves | NOT CODE-VERIFIABLE | Lessons contain relative `.md` links (controls_settings.md:14, 97; troubleshooting.md:8; workspace.md:21) and no http links (grep). Browser has `setOpenExternalLinks(True)` and a search path (ui/learning_center.py:299, 303) | Such links go to `QTextBrowser.setSource`, which would render the target through Qt's native Markdown, bypassing `lesson_html` styling and not moving the topic-list selection; no back control. Verify by clicking each link in a macOS run. |
| 120 | Sidebar curriculum equals docs/training/README | IMPLEMENTED | tests/test_learning_center.py:46-60 | |
| 121 | In-lesson search | IMPLEMENTED | ui/learning_center.py:258-272, 322-324 | Searches the current lesson only (undocumented). |
| 122 | Lessons shipped in the packaged app | IMPLEMENTED | Sentinel.spec:52-53; `resource_base` services/runtime_paths.py:37-41 | |

### Area 10 - Persistence and launch modes

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| 123 | SQLite migrations preserve history (README:291) | IMPLEMENTED | services/database.py:15 (`SCHEMA_VERSION = 3`), 135-172 `init_db`, 195-207 ordered migrations, 208-310 v1-v3 | Additive, `IF NOT EXISTS`; refuses a newer-than-known DB instead of touching it (150-155); WAL (115). |
| 124 | Pre-migration backup | IMPLEMENTED | services/database.py:156-157, 175-191 (`conn.backup`, integrity check) | Only when upgrading an existing DB; backups accumulate in `data/backups` and are never pruned. |
| 125 | One-time pricing corrections spare user edits | IMPLEMENTED | services/database.py:417-476 (flag `pricing_correction_2026_10`, old-value match) | |
| 126 | Retired agent keys remain readable in history | IMPLEMENTED | services/agent_catalog.py:90-94; services/database.py:672-690 (deletes registry rows only, `auto_generated = 0`); main.py:4242-4277, 4061-4080 list chats by raw key; usage rows keep `agent` | Shown by raw key ("writing: ..."), no label. Forge-made agents protected (services/agent_factory.py:55). |
| 127 | Runtime paths for source / frozen / portable | IMPLEMENTED | services/runtime_paths.py:33-47, 49-62, 128-138; main.py:90-95 | |
| 128 | Portable volume validated (exists, writable, free space) | IMPLEMENTED | services/runtime_paths.py:88-121 (`MIN_PORTABLE_FREE_BYTES` 256 MiB, write probe) | Probe file is written on every `user_data_base()` call. |
| 129 | Portable keeps everything on the volume (README:111-118; PM:3, 24) | IMPLEMENTED | `user_data_base` -> volume; QSettings redirected to INI under CONFIG_DIR (main.py:4877-4880); `.env`, DB, chats, backups under the data root | Launcher stderr goes to `/tmp/sentinelai_launch.log` (main.py:3496 comment) outside the volume; macOS/Qt caches are outside Sentinel's control (NOT CODE-VERIFIABLE). |
| 130 | Frozen mode uses `~/Library/Application Support/Sentinel`; legacy "Sentinel Fork" data renamed in place; never from "Sentinel AI" (README:268) | IMPLEMENTED | services/runtime_paths.py:20-21, 67-86, 128-138 | `LEGACY_APP_NAMES = ("Sentinel Fork",)` only. |
| 131 | Single-instance handoff (README launch modes) | IMPLEMENTED | main.py:4790 (`sentinel.single-instance.v2`), 4846-4860 probe/raise, 4884-4903 server | Exits 0 so the launcher shows no error. Key is per user, not per data root: starting a portable copy while an installed copy runs (or two portable volumes) hands off to the first and exits - undocumented. `listen()` result unchecked. |
| 132 | VERSION file is the single source; shown beside SENTINEL (README:5-6; VER) | IMPLEMENTED | services/app_version.py:16-40 (pattern `^[1-9]\d*\.\d{3}$`), main.py:222, 1660-1670 | VERSION = 2.002 matches README and docs/versioning.md:4. |
| 133 | VERSION bundled and stamped into Info.plist (VER) | IMPLEMENTED | Sentinel.spec:12, 38, 111-113; scripts/install_app.sh:37-84 | |
| 134 | App identity: name beside the Apple logo and Dock icon (README:280-284) | NOT CODE-VERIFIABLE | ui/app_identity.py:118-200; main.py:4868-4875 | ObjC via ctypes, all failures swallowed (`_FAILURES` line 52). Verify on macOS: menu title, Dock icon, Activity Monitor name. |
| 135 | `projects` table is reachable from the UI | IMPLEMENTED | Schema services/database.py:94-110, 300-310; CRUD services/registry.py:26-100; UI main.py:1775-1795 (filter + "+" button), 2065-2075 (active-project selector), 4089-4200 (`create_chat_project` 4162 -> `upsert_project`, `refresh_project_controls` 4089 -> `list_projects` 4092) | README.md:239 "nothing in the UI reads or writes" is stale. Only `archive_project` and `get_project` have no UI caller (grep: 0 hits). The chat-to-project link is stored in the chat JSON (`project` key, main.py:4184), not in SQLite. |
| 136 | Seeded config never overwrites user config | IMPLEMENTED | services/runtime_paths.py:141-171 (`destination.exists()` skip at 154) | Side effect: defaults added in later releases never reach existing frozen/portable users (no merge). |
| 137 | Docs: README troubleshooting/launch text agrees on Dock icon | PARTIAL | README:270-278 says the double Dock icon is fixed; docs/training/troubleshooting.md:17 still lists it as a known symptom | Doc contradiction, not code. |

### 3.1 Answers to the focus items

1. Guard order before a request. Chat (`send_prompt`, main.py:3345-3400): resolve backend -> estimate -> Validator (agent, tool, provider, permission, agent cap, tool cap, session, daily, approval) -> confirm dialog -> memory check. Panels (`authorize_request`, main.py:3556-3637): estimate -> [key check -> consent] only if permission unticked -> Validator (identical list, permission re-checked against a one-time copy) -> confirm if not already consented. So the order is permission -> budget -> (no key check) for Chat, and key -> consent -> permission/budget for panels. No active bypass was found (only two `run_backend` call sites, each behind a gate; each panel has one `authorize` and one `start_worker`), but nothing in `run_backend` or `start_worker` enforces authorisation, and the legacy `check_budget_before_request` is dead code.
2. LMArena fetch without consent / on a network-denied machine. Yes to both: the fetch is automatic (4 s after start and on Check now), gated only by 24 h staleness, ignores execution mode and permissions, and a failure writes nothing so it repeats at every start; offline it costs up to ~2 minutes of a background thread and then falls back to the snapshot.
3. Decimal vs float. Decimal is used only inside `Validator._money` for the session and daily subtraction/compare. Agent and tool caps, `session_cost_total`, the daily SUM, the budget meters and every dialog parse are float. Non-finite and non-positive inputs are accepted everywhere.
4. Learning Centre resources. 18 entries; all 18 files exist; 9 images exist and all 11 references resolve; Sentry has no image and no exercise; in-lesson relative `.md` links are the one unproven item.
5. `projects` table. Reachable: create, list, set-active and filter all have UI; archive/get do not.

## 4. Sections (a), (b), (c)

### (a) Promises in UI / tooltips / training docs that are not in the README, and vice versa

In README only:
- `route_request` "in services/benchmarks.py" (it lives in services/model_recommendations.py:493).
- `python -m services.benchmarks --snapshot` (benchmarks.py:378) and `scripts/check_live_models.py`.
- Single-instance handoff, app identity, launcher internals, Sentinel Fork rename details (not in the Learning Centre).
- "projects table has full CRUD that nothing in the UI uses" (false, see #135).
- "Settings controls ... provider permissions" (no such control, see #81).

In training docs / UI only (README silent):
- Routing priority table and "priority also decides BEST FIT" (CS:30-38); the **Routing priority** menu (main.py:2261).
- NEW badge (CS:15; widgets.py:35-39), Dismiss, Last-check hover, "ratings at a higher effort are not borrowed" (AK:151-154).
- Budget light thresholds 60% / 90% (WS:49-50); header "4/6" on API Keys (WS:18).
- Provider permission is distinct from a key (PC:25-26); the four-way "API ready / permission / within budget / account funded" check (PC:37-38).
- Tooltip claims: Gemini "free tier available" (TT:29), "Refresh model list", "Export last response", "Estimate cost" buttons (TT:21, 37, 38) - not in README; Qwen and Kimi missing from TT:19.
- GEMINI_API_KEY alias (AK, gemini_client.py:12) - not in README list or `.env.example`.

### (b) Contradictions

1. README:239 (projects "nothing in the UI") vs main.py:1775-1795, 2065-2075, 4089-4200.
2. README:78 "Settings controls ... provider permissions" vs ui/dialogs.py (no such tab) and main.py:2263-2269 (Chat Options only, not persisted).
3. services/validator.py:73 "Enable it in the API Permissions panel" and main.py:3183 "Enable it in Inspector" vs the real location (Chat -> Options -> Paid provider access; absent on specialist screens).
4. CS:35 Speed first "weighted towards fast models" vs services/model_recommendations.py:418, 519 (rating path, same as Balanced).
5. AK:134-136 / CS:14 "selection never changed behind your back" vs main.py:1347-1358 and 1595.
6. AK:104-110, 143-145 "add its price in Settings -> Pricing" vs ui/dialogs.py:568-612 (no add row).
7. WS:16 and README:90-92 semantic colour claim vs Red theme ok-light = alert-light (ui/style.py:852-854, ui/theme.py:61).
8. README:270-278 (double Dock icon fixed) vs troubleshooting.md:17 (listed as live issue).
9. `.env.example:1-3` "Sentinel Fork" and old Application Support path vs README:268 and runtime_paths.py:11.
10. ui/theme.py:1 "Two themes" and ui/dialogs.py:461-465 "both themes" vs three themes.
11. Model Guide dialog (ui/dialogs.py:1225-1477) static prices/provider lists vs config/pricing.json and DEFAULT_PRICES.
12. CS:85 "sensitive task labels" vs run_logger.py:21 storing 200 characters of the prompt.
13. WS:46 "Current Route shows the provider/model intended for the next request" vs main.py:941-962 (shows the recommendation).
14. validator.py:76 comment "(daily)" vs per-request behaviour and message.
15. TT:29 "Gemini (free tier available)" vs README/PC "paid provider"; Gemini seeded at paid rates (database.py:575-584).
16. README learning-centre list "practice exercises" and "screenshots" vs sentry.md (neither).

### (c) Undocumented behaviour

- A simple/"quick answer"/"cheap" prompt widens the rating margin to 50 points (model_recommendations.py:419, 442-443).
- The agent key and tool name are part of the classifier text (line 248); "wifi"/"vpn" agents always classify as coding.
- Auto-route candidates are the permission-ticked providers, BEST FIT uses key-holding providers.
- Output-token assumption `max(250, 1.2 x input)` in every pre-flight estimate (main.py:524).
- Local-only mode ignores the provider box and runs Ollama with whatever model name is selected (main.py:3226-3227); default mode is Local only.
- Hybrid/Cloud-only with an unticked provider raises an uncaught RuntimeError instead of a message (main.py:3233-3246, 3360).
- `prepare_agent_route` (main.py:3154-3200) silently reroutes a panel's request to another (local) model when the chosen provider has no permission and no key, leaving only a status-line notice.
- Provider permissions are session-only (reset each launch) and one-time consent never ticks the box.
- Failed LMArena fetch is retried every start; Check now does not force a fetch.
- `closeEvent` hard-`terminate()`s the chat worker and leaves its run-log row "running".
- If worker construction fails after the Send button was hidden (main.py:3433-3458), the except block only shows a dialog: Send stays hidden/disabled and the run-log row stays "running".
- Single-instance key is shared by every Sentinel launch of the same user, including portable copies (main.py:4790).
- Settings theme preview persists the theme immediately (theme.py:125-132).
- Settings "Save" is partial on error and the dialog stays open (dialogs.py:1215-1219).
- Run log stores 200 characters of each prompt (run_logger.py:21); Cost history shows only the last 200 rows and two decimals.
- Budget card prints the cap with `.0f`, so fractional caps read wrongly (main.py:4045).
- Vibe backdrop timer polls the full transcript text 10x per second forever (vibe.py:234-246).
- Seeded config in frozen/portable mode is never merged forward (runtime_paths.py:156-157).
- Arrow keys switch the theme when the dots have focus (widgets.py:1024-1040); lesson search is per lesson; provider menu groups Local/Cloud (widgets.py:233-253).
- Cost-history provider filter is hard-coded to seven providers (dialogs.py:74-77).
- Run log "Errors" counter counts only status == "error" (dialogs.py:259); abandoned panel runs use other status strings and are not counted.

## 5. Must fix before test phase

1. Make dropdown hover work: call `setToolTipsVisible(True)` in `SelectorMenu.__init__` (ui/widgets.py:57) so BEST FIT reason and "costs money" text appear (#16, #18).
2. Gate the LMArena fetch behind a setting/consent that defaults to off in Local only, and write a "last attempt" stamp on failure so retries are at most daily (ui/workers.py:~650, services/benchmarks.py:343-372) (#46, #47).
3. Fix Qwen blank base URL: `os.getenv("DASHSCOPE_BASE_URL") or INTL_BASE_URL` in services/qwen_client.py:17 and ship the line commented out in `.env.example` (#62).
4. Fix the Red-theme collision: pin the ok-light, `KVValueOn` and other status greens to colours outside the rotated band (or move red's accent away from 0 deg) in ui/style.py:852, 934 / ui/theme.py:61 (#89).
5. Make `resolve_backend_model` safe: in Local only force an Ollama model (and sync the provider box) and catch `RuntimeError` in `send_prompt` with a dialog (main.py:3202-3250, 3360) (#13, #14).
6. Unify the guard: route Chat through `authorize_request` (or add the key check to `send_prompt`) and move the consent dialog after the budget check (main.py:3345-3400, 3570-3613) (#30, #31).
7. Stop `calculate_cost_eur` returning 0.0 when no row resolves: fall back to the dearest default or refuse the request; reject a zero provider default in Settings (usage_tracker.py:~107) (#24).
8. Validate every numeric field: finite, EUR rate > 0, budgets and caps >= 0 (ui/dialogs.py:1147-1201, main.py:767-779); reject "nan"/"inf" before they reach `Decimal` (#26, #36, #69-#71).
9. Add an "Add price" row (or auto-create a pricing row on adopt) in Settings -> Pricing so the documented "add its price" step is possible (ui/dialogs.py:568-612) (#52).
10. Harden shutdown: stop and wait `ModelScanWorker`/`ModelPullWorker`, finish the open run-log row and abandon `_pending_requests` in `closeEvent` and before Emergency Reset (main.py:4778-4788, ui/dialogs.py:1121-1125) (#57, #77, #108).
11. Let the tray status read panel activity (`_pending_requests` / `panel.is_running()`) as well as `chat_worker` (main.py:4805-4820) (#105).
12. Correct the contradicting docs/strings listed in section 4(b): Speed first (CS:35), README:78 and :239, troubleshooting row 17, AK/CS "never changes selection", messages "Inspector"/"API Permissions panel", Model Guide static text, `.env.example` header, theme.py docstring, Settings theme note, TT:19/29.
13. Decide and document provider-permission scope: either add the checkboxes to Settings and persist them, or change README/validator wording to "Chat -> Options -> Paid provider access (per session)" (#81).
14. Include chat history and system prompt in `estimate_chat_cost` and in the context-window prune input (main.py:514-534, 3105-3120) (#5, #6).
15. Scope the single-instance key to the data root (hash of `user_data_base()`) so a portable copy can start beside an installed one (main.py:4790) (#131).
16. Escape HTML in the Cost history and Run log dialogs, show 4 decimals in Cost history, and fix the budget cap format `.0f` and zero-cap display (ui/dialogs.py:122-133, 273; main.py:4040-4045) (#38, #39, #41).
17. Record the error message when `abandon_request` closes a panel run (main.py:3687-3692; ui/panels/base.py `abandon`) so the Run log "errors" column is true (#39).
18. Learning Centre: connect `anchorClicked` to switch topic for relative `.md` links, and add the missing Sentry exercise and screenshot (ui/learning_center.py:297-310; docs/training/sentry.md) (#116, #117, #119).
19. Test-phase prerequisites: items marked "visual confirm" or NOT CODE-VERIFIABLE (menu tooltips, tray template colour and menu, app identity, quit-during-scan crash, Red-theme lights, Learning Centre links, offline LMArena timing) need a macOS run.

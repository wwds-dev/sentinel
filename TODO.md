# Sentinel — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)

Full reasoning, measurements and verification notes for each item are kept below
under **Detail** — this checklist is the summary view.

---

## v2 — complete

V2 closed on 2026-09-12. The release checklist contains no deferred work; all
future and cross-project items are listed under V3 or in their owning project.

### Product and interface

- [x] Canonical v2.001 release identity is visible beside SENTINEL and in the window title; the thin launcher and self-contained bundle read the same `VERSION` source.
- [x] Eight-agent Sentinel roster: Chat, Trace, Bloodhound, Beacon, Sentry, Bug Spray, Tunnel and Forge; retired or moved agents no longer appear in the sidebar.
- [x] Shared panel architecture (`AgentHost` + `AgentPanel`) and specialist modules under `ui/panels/`.
- [x] Balanced sidebars, one-row run controls, compact History, three-level type/spacing scale, spend meters and paid-route highlighting.
- [x] Structured result cards for Trace, Bloodhound, Beacon, Bug Spray and Forge, with separate live-stream surfaces and collapsed raw output.
- [x] Chat sends with Enter, inserts a line with Shift+Enter, uses a larger composer, keeps the conversation scrollable and timestamps every message.
- [x] Chat Projects grouping: project picker, project/agent/text filters, project creation and assignment, restored project on open, and project attribution for chat and usage records.
- [x] Removed the obsolete READY pill and normalized specialist-panel margins to the 4/8/16/24 scale.
- [x] Offscreen renders of the nine Learning Centre screenshots were visually inspected at 1600×1000 after the final UI changes.

### Agents and local tools

- [x] Trace consent-gated Live Research for domains/IPs, usernames, email and companies, with partial results, cancellation and saved searches. Person/phone data-broker lookup remains deliberately excluded.
- [x] Bloodhound read-only local and authenticated SFTP file discovery with explicit roots, filters, safety limits, cancellation and no file-content handoff to AI.
- [x] Beacon read-only interface/adapter preflight and documented dual-interface workflow.
- [x] Tunnel phases 1–2: read-only connection diagnostics, selected-profile comparison and Connect/Disconnect/Restart command previews (Action Preview still shows commands without running them).
- [x] Tunnel phase 3a: local WireGuard configuration inspection that discards private and preshared keys at parse time and compares non-secret intent with the latest diagnostic snapshot.
- [x] Tunnel phase 4 — real VPN client: WireGuard **and** OpenVPN connect/disconnect via the macOS authorisation dialog after an explicit confirmation, `.conf`/`.ovpn` import, and country-labelled example templates that the connect path refuses until given a real endpoint/config. Privileged execution is injectable and unit-tested without `sudo` (`services/vpn_connection.py`, `services/openvpn_manager.py`, `tests/test_vpn_connection.py`).
- [x] VPN Agent code merged in-tree (formerly a git submodule): removed the 1.2 GB committed `.venv`, the standalone `app/` GUI and its build/test meta; rewrote the internal `from server import …`/`from services import …` imports to fully-qualified paths so the pf **kill switch** and `profile_store` now import under Sentinel.
- [x] Honesty-audit fixes (`docs/honesty_audit.md`): Bloodhound now runs live OSINT for every target type and drives the Sources gauge from real sources contacted (Threat/Confidence labelled AI estimates); streaming requests bill the provider's real token counts, so the budget cap is accurate; Beacon scans via `system_profiler` instead of the Apple-removed `airport` binary and the USB VID/PID matcher is fixed; honest UI captions on Tunnel, Bug Spray and Forge.
- [x] Forge creates reviewable agent specifications before any scaffold is approved.
- [x] Bug Spray's canonical home is the nested companion used by Sentinel; no duplicate top-level implementation is maintained.

### Safety, cost and reliability

- [x] Every paid-provider request goes through authorization, budget, consent, usage and run logging, keyed by unique request id.
- [x] Exact `Decimal` budget boundaries, Kimi cached-input pricing, cloud timeouts and clear provider-permission errors.
- [x] `_note_failure` remains intentionally lightweight (stderr + tooltip); a queryable general `RunLogger.note` API is not required for V2.
- [x] Portable macOS mode keeps state on the selected drive, preserves data during upgrades, excludes source secrets, handles read-only/low-space media and offers a double-confirmed Sentinel-only Emergency Reset.
- [x] Product identity is Sentinel across source, bundle, runtime paths, single-instance key, documentation and Lab Hub. Legacy `Sentinel Fork` application-support data — the pre-rename application name — is migrated; archived `Sentinel AI` data is never touched.
- [x] Full release verification: 556 Sentinel tests, the complete nested VPN Agent suite and the complete Lab Hub suite pass.
- [x] Replaced the AppleScript/launchctl thin launcher with a compiled native one-shot shim (`scripts/thin_launcher.c`): Launch Services starts it, it execs the project's `.venv` Python against `main.py` in a detached child, and the parent returns at once — no persistent launchd job, no restart-on-exit policy.
- [x] Fixed the request inspector clipping its right edge in the narrowest sidebar width: `Meter`/`KeyValue` values and the cards container no longer impose a minimum width wider than the scroll viewport (`ui/widgets.py`, `main.py`).

## v3 — later

- [x] `P1` `bug` `@ai` **Every Chat model pick dirtied the repo (2026-10-09).** `save_provider_model_preference()` and the routing-priority menu rewrote `config/settings.json` — tracked, and the checkout itself in development and under the live launcher. `config/settings.json` is now the shipped defaults only; picks go to the git-ignored `data/settings.local.json`, read back laid over the defaults (`services/settings_store.py`, also used by `database._migrate_settings` and `scripts/check_live_models.py`). `data/`, not `config/`, because `Sentinel.spec` bundles all of `config/` and would have carried a developer's picks into a handed-off build. A one-time migration at app start (checkouts only, and only while no override exists) moves values that differ from HEAD into the override and restores the tracked file; it moved this machine's `default_model_ollama` = `muse-glimmer:30b-q4_K_M`. The suite redirects both files and fails if either real one changes. `tests/test_settings_store.py`.
- [x] `P0` `bug` `@ai` **The bill, the budget gate and the router priced models differently, and low (2026-10-08).** `UsageTracker.calculate_cost_eur` took a model's exact row or its provider's `default`, with no alias matching and no zero check. For 10k input + 2k output tokens on a fresh database: gemini-2.5-flash/-pro billed €0 (the shipped Gemini default was 0/0), gpt-4o €0.0025 (the OpenAI default was gpt-4o-mini's rate, ~1/16), deepseek-v4-pro €0.0018 (the retired deepseek-chat rate, ~1/11), claude-fable-5-1 €0.055 against €0.184. The pre-flight estimate uses the same function, so the session/daily caps under-protected; the router meanwhile called the same models "price unknown". Now one resolver serves both (`services/price_resolution.py`: exact row → dated-snapshot alias → provider default, positive rates only, Imprint's order), those models have rows from the providers' pricing pages, every provider default holds its dearest current rate, and a one-time correction (`PRICING_CORRECTIONS_2026_10`) moves only rows still holding the old shipped value. Qwen is re-priced at the Singapore rates its client is actually billed at, and the Cost readout says when a price is a provider default. Supersedes the unmerged `claude/reverent-ptolemy-49a105` (`9446a3c`). `tests/test_price_resolution.py`.
- [x] `P1` `bug` `@ai` **The request classifier matched substrings (2026-10-08).** Each match imposed a hard capability requirement: "withdraw the claim and rewrite the email" needed image generation ("draw"), so gpt-image-1.5 was the only eligible model; "summarize this transcript" and "what's the postcode" needed coding; "improve" read as reasoning, "photosynthesis" as vision, "resources" as research. `classify_request` now matches whole words and reads summarize before coding. `tests/test_capability_router.py`.
- [x] `P1` `bug` `@ai` **The context-window filter never ran from the app (2026-10-08).** `route_for_request` passed no `context_tokens`, so a model too small for the prompt stayed eligible and the >100K long_context switch never fired. It now passes `estimate_prompt_tokens`, the chars/4 figure the cost estimate uses. `tests/test_ui_panels.py`.
- [x] `P1` `feature` `@ai` `agent:bug_bounty` Embed Bug Spray's saved program and change feed in Sentinel. The workspace scans public program directories in the background when due, shows searchable programs and recent changes, and can fill the program name for report drafting (2026-09-26).

- [x] `P1` `bug` `@ai` `agent:vpn` VPN audit follow-up: label command completion as unverified protection; refuse OpenVPN shutdown without verified process tracking and preserve signal failures.
- [x] `P0` `infra` `@me` **`agents/vpn_agent` was a bare gitlink — its files were tracked by nothing.** `git ls-files --stage agents/vpn_agent` showed `160000 e5a28ea3…` with no `.gitmodules` entry and no nested `.git`, so GitHub rendered it as an empty grey folder and its files were in neither history. Resolved in `db0eb4d` by `git rm --cached agents/vpn_agent && git add agents/vpn_agent` — it is now a normal in-tree directory (`040000 tree`), matching the intentional de-submodule merge of the VPN library.
- [ ] `P1` `testing` `@me` `agent:vpn` Verify live IPv4/IPv6 routing, DNS, handshake, reconnect and failure behavior against an owned VPN endpoint before relying on traffic protection. See `docs/honesty_audit.md`.

- [x] `P1` `infra` `docs` `@ai` **v2.002 release follow-up.** Shipped 2026-09-29: `VERSION` is `2.002`, its release-record entry is in `docs/versioning.md`, the OSINT source expansion below is documented in the Trace/Bloodhound/Tunnel Learning Centre lessons, and the full suite passes (858 tests). The Lab project monitor hand-off continues in the next item.
- [x] `P1` `security` `bug` `docs` `@ai` **v2.002 code-review fixes.** An ultra review of the v2.002 code and all seven agent repos found consent, key-handling and robustness defects; all are fixed and each repo's suite passes. Sentinel (`claude/dreamy-cerf-b2kah6`): Trace's IP consent now names Shodan InternetDB / IPinfo / Criminal IP and Company consent names CourtListener, so the dialog and the audit-log line match the sources actually contacted (a new `test_ui_panels` parity test guards this); the IPinfo token moves to the `Authorization` header so a transport error can't leak it and `_ipinfo` self-skips without a key; DeHashed email selector is quote-sanitised; the DNS-leak ASN heuristic and verdict parser are hardened; local-IP reads move off the Qt thread; the public-IP opt-in latches only on success; the alias-mint worker is parked, not `terminate()`d; docs (README, `versioning.md`, `roadmap.md`, `trace/tunnel/bloodhound` lessons, capability sheets) corrected to v2.002 and to the real VPN-flag/source behaviour. Agent repos, each on the same branch: `osint_agent` (bracket-crash in `validate_target`), `osint_heavy_agent` (auto-detect routes IPv6/decorated-IPv4 to the domain provider), `sentry` (arp-spoof dedup, lsof-space parsing, atomic locked writes, capped connections baseline), `bug_spray` (HackerOne pagination bound, `Retry-After` clamp, currency-switch display, Immunefi missing-slug guard, feed N+1, read-only CLI), `wifi_agent` (BSSID/channel validation, ESSID no longer dropped), `manager_agent` (`raw_decode` spec parse).
- [ ] `P1` `infra` `docs` `@ai` **v2.003 release follow-up.** When the next user-visible development milestone is ready, increment `VERSION` to `2.003`, add its release-record entry, update the documentation that describes the changed behaviour, run the release checks and create the next numbered follow-up. See `docs/versioning.md`. This explicit open item is the Lab project monitor hand-off.
- [x] `P2` `testing` `docs` `@ai` **Testing roadmap — in-app access.** The complete roadmap is a searchable Learning Centre topic and is bundled in self-contained releases.
- [ ] `P0` `testing` `security` `@ai` **Testing roadmap — safety gate.** Isolate writable config as well as SQLite (the baseline suite changed `config/settings.json`), add a deny-by-default network/process harness, cover every agent's consent, scope, secret, budget, cancellation and write/delete boundary, plus installed-app Quit-stays-quit smoke. See `docs/testing_roadmap.md`.
- [ ] `P1` `testing` `@ai` **Testing roadmap — workflow matrix.** Complete success/error/cancel/persistence contracts for all eight agents, their distinct modes, all shared controls/providers/settings, and the separate companion-repo test runs. See `docs/testing_roadmap.md`.
- [ ] `P2` `testing` `docs` `@ai` **Testing roadmap — release acceptance.** Automate source/frozen/USB smoke where feasible, run owned-lab hardware/network cases and first-user Learning Centre review, and record unverified modes explicitly. See `docs/testing_roadmap.md`.
- [x] `P1` `feature` `docs` `@ai` **Learning Centre — complete curriculum.** Searchable Quick Start, workspace tour, controls and Settings, all seven agent courses, privacy/cost, troubleshooting, multi-agent workflows and advanced-tools guidance are shipped.
- [x] `P2` `docs` `design` `@ai` **Learning Centre — reproducible screenshots.** Nine current-interface images are generated from isolated fictional/empty state by `scripts/capture_training_screenshots.py`.
- [ ] `P2` `testing` `docs` `@ai` **Learning Centre exercises and first-user validation.** Add beginner, intermediate and independent exercises per agent, then test Quick Start with new users.
- [ ] `P1` `infra` `@ai` **Shared Lab platform package.** Extract provider clients, limits, usage, registry, database, runtime paths, request guard and common UI only when the other hubs are ready to consume one version.
- [ ] `P1` `infra` `security` `@ai` **External-tool adapter framework.** Dependency checks, structured output, previews, timeouts, cancellation, local audit records, privilege/scope gates and explicit cloud-handoff consent.
- [x] `P1` `feature` `security` `@ai` **Tunnel phase 3b — gated WireGuard execution.** `services/vpn_execution.py` wraps Connect/Disconnect: target review with blockers (template, missing tool/file, a config name wg-quick rejects, already up) and warnings (split tunnel, world-readable config, clear/blocked traffic after disconnect), the review as a default-No confirmation (Disconnect now confirms too), re-review in the worker, a fresh post-change check (`/var/run/wireguard` records, tracked OpenVPN process, route interface for full tunnels), rollback steps, and a JSONL audit (`data/logs/tunnel_audit.jsonl`, mode 600, key-free) that also records kill-switch changes. OpenVPN — already live since phase 4 — gets the same review, check and audit. Also fixed: `wg-quick` is now invoked by absolute path with its directory on PATH, because the admin dialog's PATH has no Homebrew bin. Live behaviour against a real endpoint is still the `@me` item above. `tests/test_vpn_execution.py`.
- [ ] `P2` `bug` `@ai` `agent:vpn` OpenVPN has the same PATH gap `wg-quick` had: `openvpn_manager.connect` runs a bare `openvpn` through `run_as_root`, and the authorisation dialog's PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) has no Homebrew bin. Resolve it with `shutil.which` the way `vpn_connection.wg_quick_command` does.
- [x] `P2` `feature` `@ai` **OSINT Keys tab in Settings.** New "OSINT Keys" tab added to `ui/dialogs.py` `show_settings()`: operational email field with clipboard copy, live "X / 18 registered" progress bar, filter chips by category/cost, and one row per tool in `OSINT_TOOLS` (registration checkbox, Register → launcher, masked API key field with show/hide, Save Key button that writes to `.env` and `os.environ` live). Ops email and registration states persist to SQLite via `get_setting`/`save_setting`. See `SUGGESTIONS.md` for next-step ideas.
- [x] `P1` `feature` `security` `@ai` **Live OSINT source expansion (v2.002).** Ten new normalised live sources across the providers, each key-gated where paid and self-skipping without its key, all returning text metadata only. IPs: Shodan InternetDB, IPinfo (`IPINFO_API_KEY`) and Criminal IP (`CRIMINALIP_API_KEY`) in `providers/domain_lookup.py`. Company/org: CourtListener court dockets (`COURTLISTENER_API_KEY`, keyless-friendly, `type=d` so no document text/PDF is ever fetched) in `providers/company_lookup.py`, always on in Trace's company Live Research and in Bloodhound's organisation collection (Bloodhound passes `offshore_leaks=True, sanctions=True, court_records=True` at `agents/osint_heavy_agent/__init__.py:425`; ICIJ Offshore Leaks is Bloodhound-only). Exposure: DeHashed v2 (`DEHASHED_API_KEY`, metadata-only — breach names and counts, never leaked passwords/hashes) in `providers/exposure_lookup.py`. Tunnel: a native "Your IP & DNS" readout (local/tunnel address, public exit IP with VPN/hosting flags) and a real bash.ws DNS-leak test in `agents/vpn_agent/services/{public_ip,dns_check}.py`. addy.io burner-alias minting (`ADDYIO_API_KEY`, user-triggered write only) in `providers/alias_mint.py` and the OSINT Keys tab. +34 tests including metadata-only boundary tests for DeHashed and CourtListener; verified by a re-verification and an 18-agent adversarial-review workflow. `OSINT_TOOLS` and `.env.example` updated for the new keys.
- [ ] `P2` `testing` `@ai` **Unit tests for `services/osint_catalog.py`** (the OSINT Framework catalogue behind Trace's and Bloodhound's source suggestions, added 2026-09-28 in `f2dbf8c`). Parsing, per-agent filtering (Trace: free, no account, passive, no people-search/dating; Bloodhound: scope limits, ACTIVE tag), the shadow-library blocklist, weekly cache refresh and the offline fallback are only hand-checked so far; `tests/conftest.py` already isolates the cache.
- [ ] `P2` `infra` `@me` **ISP router refuses connection bursts.** The router at 192.168.0.1 (behind the GL.iNet at 192.168.10.1, so the network is double-NATed) has flood protection that refuses new connections when one device opens them fast: measured 2026-09-28, 218–293 of ~600 refused during a WhatsMyName sweep, while both routers stayed reachable. Sentinel now backs off and retries (`7e7cfea`), at the cost of ~56 s per sweep instead of ~20 s. Fix at the router: put it in modem (bridge) mode, or relax its flood/DoS protection.
- [x] `P2` `feature` `design` `@ai` **Menu bar item, and the identity macOS shows for the process (2026-10-06).** Sentinel now puts a status item in the menu bar while it is open: a template-image shield glyph (`assets/tray.png` + `@2x`, drawn by `scripts/make_icon.py` so macOS recolours it for a light or dark bar), and a menu holding a live readout — working/idle, which agent, what the session has cost — plus Open and Quit. Quit goes through the window's `closeEvent`, not `app.quit()`, so an in-flight request is still cancelled and panel background work still shut down. Closing the window quits as before; nothing hides to the menu bar. `ui/tray.py`, wired in `main.py`'s entry point behind `tray.available()`. **`ui/appkit_guard.py` ported from Lab Hub in the same change** — AGENTS.md requires it for any app in this workspace with a tray menu, because macOS 27 makes `-[NSEvent clickCount]` raise when a menu begins tracking and the process aborts; Sentinel can be mid-run and mid-spend when that menu opens. Separately, running from the checkout the application menu beside the Apple logo was titled after the script and the Dock showed the Python icon: macOS resolves identity from the running executable's path, and `.venv/bin/python` is not in a bundle. `ui/app_identity.py` writes `CFBundleName` into the main bundle's info dictionary before `QApplication()` and sets the Dock icon via `-[NSApplication setApplicationIconImage:]` after it (`QApplication.setWindowIcon` does not touch it on macOS — checked by fingerprinting the icon's TIFF data), and it absorbs the old `_activate_native_app` ctypes block so the project has one AppKit module rather than three copies of the plumbing. Measured, so it is not re-litigated later: the launcher's `fork()` is **not** the cause — a plain `exec` loses the identity too — so `scripts/thin_launcher.c` is unchanged; and the name under the Dock icon / in Force Quit stays `python` in both source modes, because Launch Services took it from the executable at launch. +23 tests (`tests/test_tray.py`, `tests/test_app_identity.py`, `tests/test_appkit_guard.py`); full suite 931 passing.
- [x] `P2` `design` `@ai` **Best fit is a badge, not a colour (2026-10-06).** Every agent's provider and model dropdown now marks its recommendation with an accent **BEST FIT** pill beside the entry, the same badge Imprint paints, with the reason on hover and on the closed control. It replaces a red `ForegroundRole`, which lost every argument it was in: the amber "costs money" dot painted over it on all six cloud providers — the recommendation was invisible in exactly the menu where it mattered — and `mark_oversized_models` writes the same role, so it had to skip any item that already carried a colour and the two markings cancelled each other out. `RECOMMENDED_ROLE` / `RECOMMENDATION_REASON_ROLE` / `RECOMMENDATION_BADGE_ROLE` in `ui/widgets.py` carry the recommendation as data; a new `SelectorMenu` paints the pill over the finished QMenu (`actionGeometry` for the row, `reserveBadgeRoom` so it cannot land on a label) and keeps hover, keyboard navigation and the stylesheet untouched. Cost keeps its own two channels — the control still turns amber while a paid route is selected, and every cloud entry says so on hover — but no longer spends the dot, which now means only "where am I" (current) and "this machine cannot run it" (grey). The red deviation border went with the red entry; the closed control's tooltip names the best fit instead. +19 tests (`TestBestFitIsPresentedAsABadge`, every agent including Sentry); full suite 950 passing.
- [x] `P1` `infra` `docs` `@ai` **The rename is finished: no "fork" left in the name (2026-10-06).** The application became Sentinel on 2026-09-12 but the checkout stayed `sentinel_fork` and the GitHub remote stayed `sentinel-ai-fork`, which is what every path, every Lab tool and every document still read. Renamed: the GitHub repository to `wwds-dev/sentinel` (GitHub redirects the old URL, so an old clone keeps working), the checkout to `~/Documents/lab/active/sentinel`, and every reference in this repo's docs, in the workspace `AGENTS.md`/`README.md`, in Lab Hub's app registry and tests, in git_autosync's seed and live config, and in the Lab Project Monitor's generator. The one thing deliberately *not* renamed is the legacy data-migration strings — `/Applications/Sentinel Fork.app`, `~/Library/Application Support/Sentinel Fork`, `Sentinel Fork Data`, `LEGACY_APP_NAMES` — because they name the directories an older install left behind and are only ever read, never written; renaming them would orphan that data rather than tidy anything up. Each is now commented to say so. The last identifier still carrying the old name, the wordmark label `fork_brand` in `main.py`, became `brand` in `d6118c9` (on screen it always read SENTINEL).
- [x] `P2` `design` `@ai` **Three colour themes, picked from three dots (2026-10-06, `89fe9a9`).** Green (Matrix), Red and Blue (Cyberpunk), chosen from `ThemeDots` in the brand row or **Settings → General → Theme** (live preview, Cancel restores). `ui/theme.py` hue-rotates the single green stylesheet inside a 80–185° band, so semantic red/amber/blue and Beacon's monitor-mode green pass through unchanged; the blue accent sits 24° from the informational blue and a test fails below 20°. `ui/vibe.py` adds a per-theme underscore caret cadence in the composer, a faint empty-transcript backdrop (rain / hex dump / grid) that stops as soon as there is text, and blue-theme focus brackets. `tests/test_theme.py`, `tests/test_vibe.py`.
- [x] `P2` `infra` `@ai` **Bug Spray gitlink points at a commit the remote has (2026-10-06, `b01de42`).** `agents/bug_spray` sat four commits behind its own `origin/main` — the v2.002 code-review fixes recorded above were merged but never pinned — and the rename commit had pinned a commit on no remote branch. Now `7ebb2ac`; Sentinel 950 and Bug Spray 52 tests pass.
- [ ] `P2` `docs` `@ai` **Re-capture the Learning Centre screenshots.** All nine images in `docs/training/images/` were last regenerated on 2026-09-28, so they predate the theme dots in the brand row, the BEST FIT badge, the per-theme empty-transcript backdrop, and the screen-style sidebar tiles with the Model Updates card (2026-10-07). Specifics: `workspace-chat.png` and `tunnel.png` still show `v2.001` and a sidebar with no Sentry row; `tunnel-action-preview.png` also shows `v2.001`; `tunnel.png` lacks the Your IP & DNS group; `trace.png` predates Trace's Exposure Check button and the Stop button that replaces the run bar while a request runs; `bloodhound.png` predates the image-metadata (EXIF) checkbox. `docs/training/trace.md` and the `tunnel.png` alt text in `docs/training/tunnel.md` carry captions saying so, to be removed once those images are re-captured. Re-run `scripts/capture_training_screenshots.py` (consider pinning the green theme so captures stay reproducible). The menu bar item is outside the window and is described in text only.
- [x] `P1` `feature` `@ai` **Model choice on value, not novelty (2026-10-07).** *Model Updates* (left rail): at every start, and on **Check now**, each provider with a key is asked for its live model list (free, no prompt, one provider at a time), and models that appeared since the last check are listed as rows; click to select, click again to clear, **Update N** brings in exactly the selected ones (`services/model_watch.py`, `ui/model_updates.py`). The first scan only reports successors of models Sentinel rates, and only the newest release per model line. *Routing:* each request is classified and every usable model looked up on the public LMArena leaderboard for that kind of work; the cheapest model within 20 points of the best wins (Cost first 50, Quality first 0), with real prices from Settings → Pricing. A cloud model with no price or a zero price is never cheap; an unrated model (including local Ollama models) is never chosen over rated ones outside Local only / Privacy first. Each agent's BEST FIT is the same assessment over the providers with keys, re-derived after a scan, a price edit, an update or a priority change, without changing selections (`services/benchmarks.py`, `route_request`, `derive_agent_recommendations`). Ratings refresh at most daily via Hugging Face's row pager — its query endpoint kept answering "index is loading" — paced at 1 s because it returns 429 after ~40 quick requests; finished categories are kept and the rest fall back to the cache or `config/lmarena_snapshot.json` (2026-10-02). Also: catalog/prices for `claude-opus-5-5`, `claude-sonnet-5-5`, `gpt-5.5`, `gpt-5.4-mini`, `gemini-3.1-pro-preview`, `gemini-3.8-flash` from the providers' own pages, with the Gemini 3.8 Flash price doubling on 2027-01-01 applied automatically; **Options → Routing priority**; every sidebar tile redrawn as a small screen (`ScreenCard`, status light, segmented meters); the "No project" picker sized to its text; Learning Centre lesson *API keys and new models*. 1,020 tests.
- [ ] `P1` `bug` `@ai` **Prices the router cannot see.** Gemini's `default` pricing row is 0/0, so Gemini 2.5 models are costed at €0 and never count against budgets, and DeepSeek's current ids (`deepseek-flash`, `deepseek-v4-pro`) have no pricing rows at all, so the router never picks them on cost. Being fixed in a separate session ("Fix Gemini priced as free in Sentinel").
- [ ] `P2` `testing` `@ai` **See a complete live ratings refresh.** In development every live attempt was cut short by Hugging Face rate limits left over from probing (three of five text categories arrived; the rest came from the snapshot, as designed). Check `data/lmarena_ratings.json` after a normal start: no `missing` tables and a current `published` date.
- [ ] `P2` `research` `@me` **Gemini 3.8 Flash is rated only at "high" thinking.** It is now the value pick for most kinds of work (1530 coding against Opus 5.5's 1538, at a fifth of the price). LMArena has no rating for its default setting, so check that Sentinel's default-setting calls hold up, or set the Gemini client's thinking level explicitly.
- [ ] `P2` `testing` `@me` **Generate one real image (gpt-image-1.5).** Image generation was rewritten on 2026-09-28 (`708a61d`, `217ad4c`): the selected model is passed through, the gpt-image `b64_json` reply is saved to `output/images/` and Chat shows the path. It is covered only by fake-client tests (`tests/test_openai_image.py`); both live attempts were refused with 429 `credit_balance_exhausted`, unbilled. After adding OpenAI credits, run one low-quality request (about a cent) from this folder:
  `.venv/bin/python -c "from dotenv import load_dotenv; load_dotenv('.env'); from services.openai_client import OpenAIClientWrapper as W; w=W(); w.client=w.client.with_options(max_retries=0); print(w.generate_image('A small red lighthouse, flat illustration', quality='low'))"` — it should print `Image saved to …/output/images/<timestamp>_gpt-image-1.5.png` and the token counts. Do this before the `gpt-image-2` item below.
- [ ] `P3` `feature` `@ai` **`gpt-image-2`** is listed by OpenAI and flagged by the scan, but not in the catalog: image generation with it was not tested (a test costs money). Verify `generate_image` with it, then add it.
- [ ] `P3` `research` `@ai` Unrated catalog models — `kimi-k2.7-code`, `kimi-k2.7-code-highspeed`, `qwen-flash` — never win against rated ones. Check whether LMArena lists them under other names and add `ALIASES` entries in `services/benchmarks.py`.
- [ ] `P2` `feature` `@ai` Automatic Ollama fallback when a cloud budget cap is reached.
- [ ] `P3` `infra` `@ai` One retry-with-backoff wrapper shared across providers.
- [ ] `P3` `feature` `@ai` Export a run—prompt, response, usage and cost—as one Markdown file.

---

# Detail

---

## 1. Paid API calls bypass every guardrail outside the chat panel  ⚠️

**22 sites construct a `ChatWorker` directly; there is 1 `validator.validate`
call and 0 usage-tracking calls in the whole app.**

Only `send_prompt()` (the chat panel's Send button) runs the guarded sequence:

    estimate cost → validator.validate (budget) → confirm_external_api_request
    → run_logger.start → ChatWorker → log_request + save_chat + run_logger.finish

Every other runner (`osint_analyse`, `roi_analyse`, `health_analyse`,
`inv_analyse`, `nfl_bet_analyse`, `music_analyse`, `webdesign_generate`,
`wifi_run`, the author/manuscript generators, …) picks a provider that may be a
paid one and calls `ChatWorker` directly. Consequences:

- the €1 session / €5 daily caps do not apply to most of the app;
- "Cost Today" and "Requests Today" stay at 0 no matter what those agents spend;
- no confirmation prompt before spending money;
- nothing is written to Saved Chats (which is why every saved chat is `chat:`).

**Fix:** two helpers on `GodAI`, and every runner calls them —

- `authorize_request(agent, tool, provider, model, prompt) -> bool`
  (estimate → validate → confirm → `run_logger.start`; `False` means blocked)
- `record_request(agent, tool, provider, model, prompt, messages, response, usage)`
  (`log_request` → session totals → `save_chat` → `run_logger.finish`)

This closes four separate defects with one change.

**Status: DONE (2026-08-12).** `authorize_request` / `record_request` /
`abandon_request` / `note_request_usage` live on `GodAI`, and all 19 previously
unguarded `ChatWorker` sites call them — 20 `authorize_request`, 19
`record_request`, 17 `abandon_request`. Verified: all 12 agent panels are
refused when the provider checkbox is off, and a completed run now bills the
session and writes a Saved Chat under its own agent name.

Two things surfaced while wiring it:

- `Registry` reads the **SQLite DB**, not `config/registry.json` (the JSON is
  only a seed via `_migrate_registry`). Both were updated.
- `chat`, `osint` and `manuscript` did not list `anthropic` or `kimi` in
  `allowed_providers`, and no tool listed them either — so picking Anthropic or
  Kimi anywhere was already being rejected as "does not permit provider" before
  any of this. Fixed in the DB and the seed.

Not wired, deliberately: `roi`, `investment` (moved to the SONAR app — see the
comment in `_seed_default_agents`) and `ops_identity`, none of which have an
agent module or panel here. **`ops_identity` is still listed in the sidebar and
`agent_titles` despite having no implementation — a dead menu entry worth
removing.**

Concurrency caveat: **resolved.** `_pending_requests` moved from being keyed
by agent name to a `request_id` (`uuid4().hex`, generated in
`authorize_request`), with a backward-compatible fallback to the agent name
when no id is passed. Two simultaneous runs of the same agent now resolve
against their own context instead of clobbering each other's. See the v2
checklist above and `tests/test_request_guard.py`.

## 2. `main.py` is too big — IN PROGRESS (phases 1–3 of 5 done)

One file holds 17 agent UIs, routing, cost logic, history and styling. The cost
is concrete: a checkbox-spacing fix had to go in the global stylesheet because
the pattern repeats everywhere, and a card-padding fix touched 6 identical
`setContentsMargins` calls. (`list_models()` ×64, `QGroupBox(` ×57,
`setContentsMargins` ×75, `provider_box.addItems` ×13.)

**Fix:** one module per agent panel (`ui/panels/osint.py`, …) plus a shared
`AgentPanel` base for the provider/model/actions row all 17 rebuild by hand.
Do this *after* #1 — the shared helper makes the seam obvious.

**Plan written: `docs/refactor_plan.md`** (2026-08-12). Measured layout, a
five-phase order that ends green at every step, and the finding that makes it
tractable: each agent vertical is ~75% self-contained and the code it reaches
outside itself is the same ~15-member interface every time (provider clients,
the request guard from #1, `run_backend`, `agent_instances`, `_note_failure`).
**Phase 1 is DONE (2026-08-12):** `ui/workers.py` (212), `ui/widgets.py` (161),
`ui/style.py` (315) and `ui/tooltips.py` (252) extracted verbatim; `main.py`
11,902 → 11,007. Verified at runtime, not just by import — the stylesheet is
applied and tooltips are live.

**Phase 2 is DONE (2026-08-12):** the four `show_*` dialogs moved to
`ui/dialogs.py` (735 lines), a net −696 in `main.py`. `GodAI` keeps four
three-line wrappers, so no call site changed. Each body is byte-identical to the
original after `self`→`app` and one dedent — diff-verified rather than eyeballed.

The trap worth carrying into Phase 3: **a missing import does not fail at import
time.** The moved bodies referenced five provider wrappers plus `Registry` and
`Validator`; `ui/dialogs.py` compiled and imported fine, and only
`show_model_guide` raised `NameError` when actually opened. Guessing the import
list from a regex missed all seven; walking the AST for `Load`ed names not bound
in the module found them at once. The check that caught it was stubbing
`QDialog.exec` and opening all four dialogs, asserting on their contents.

**Phase 3 is DONE (2026-08-20):** `ui/host.py` (90) holds the `AgentHost`
protocol and `ui/panels/base.py` (215) the `AgentPanel` base; `tests/test_ui_panels.py`
(77 tests) covers both. The design decision is settled — **composition**, so a
panel holds a host rather than sharing a namespace with it, and 20 of those tests
build a panel against a 42-line fake host with no `GodAI` and no window.

No vertical moved, which is why `main.py` only went 5,520 → 5,378. What went was
the duplication the verticals would otherwise have carried with them: seven copies
of the provider list, six hand-built provider/model rows, six `*_load_models`
methods (which had already drifted — three reported a load failure, three
swallowed it), and `AGENT_MODEL_LOADERS`, a map of method *names* resolved by
`getattr`, now a registry each panel fills in as it builds.

The trap this time, again runtime-only: **an unparented row container takes its
widgets with it.** The combos belong to the container of the layout they are added
to, so a container that falls out of scope leaves every combo raising
`RuntimeError: Internal C++ object already deleted` from lines that have nothing
to do with ownership. `flow_row(parent)` takes a parent now.

**Phase 4 is DONE.** Bundled into the `chore: initialize Sentinel AI fork`
commit (2026-08-22) that split this project out of Sentinel AI's history: all
six verticals — osint, osint_heavy, wifi, bug_bounty, vpn, manager — landed in
`ui/panels/`. `main.py` 5,378 → 3,567 in that commit (it also carried other
fork-specific reshaping — `services/registry.py`, `services/pricing.py`,
`services/model_router.py` — so the drop isn't a pure panel-move number the way
Phases 1–3 were). `main.py` has since grown to 4,082 lines on new feature work
(the Trace Live Research slices below), which is expected — the phase measured
a one-time structural move, not a ceiling.

Not part of this phase: `_pending_requests` stayed keyed by agent name at the
time of this move. It has since been fixed (see the bug item above and
`docs/refactor_plan.md`).

## 3. Other agent panels still crush when the window is narrow

`FlowLayout` (main.py) fixed the chat panel: a `QHBoxLayout` reports the sum of
its children as its minimum width, so a long control row pins an impossible
minimum on the pane and Qt compresses buttons past their own minimums —
labels get chopped to "uto Rout", "ecomme".

**Status: DONE (2026-08-12).** 13 control rows converted to `FlowLayout`:

| panel           | min width before | after |
|-----------------|-----------------:|------:|
| AuthorPanel     | 1091 | 403 |
| WiFiPanel       |  803 | 462 |
| NFLBetPanel     |  728 | 473 |
| OSINTPanel      |  728 | 257 |
| OSINTHeavyPanel |  728 | 503 |
| WebdesignPanel  |  728 | 507 |
| ManuscriptPanel |  711 | 670 |
| BugBountyPanel  |  704 | 573 |

The result that matters: the splitter's minimum width is now **985px, under the
window's own 1000px minimum**, so the three panes fit at the narrowest allowed
window and nothing can be crushed. It was 1460px when this started.

Notes for future conversions:

- Rows come in three shapes and all three needed handling: `addLayout(row)`,
  `addLayout(row, r, c, rs, cs)` into a `QGridLayout`, and `QHBoxLayout(widget)`
  built straight onto a container.
- `FlowLayout.addWidget` now accepts (and ignores) `QBoxLayout`'s stretch and
  alignment arguments, so a `QHBoxLayout` can be swapped in without touching
  call sites — the fiverr panel passes a stretch factor.
- `addStretch()` calls were dropped: a stretch has no meaning once items wrap.

Not converted: the API-key rows (771px). They live inside a scroll area, so
their width no longer drives any pane minimum.

## 4. No timeouts on any cloud client

`ollama_client` sets 10s/300s. All five paid clients — `openai_client`,
`deepseek_client`, `kimi_client`, `gemini_client`, `anthropic_client` — passed
no timeout at all, so a hung connection froze that agent with no recovery.

**Status: DONE (2026-08-12).** `services/api_limits.py` holds the shared values
(120s, 1 retry) and all five clients use them. Verified on the constructed SDK
objects, not just in source; a request to a black-hole address now raises
`APITimeoutError` instead of hanging.

Two notes for whoever tunes this: google-genai's `HttpOptions` takes
**milliseconds** while the other four SDKs take seconds, and the timeout applies
*between streamed chunks* rather than to the whole generation — so 120s does not
cap a slow model.

## 5. Silent `except: pass` blocks

Failures vanished, including around history loading and model listing — if
`load_history_list` threw, the list was silently empty and looked identical to
"you have no saved chats".

**Status: DONE (2026-08-12).** All 12 were replaced with
`except Exception as exc: self._note_failure(...)`. The helper writes
`[warn] <context>: <type>: <message>` to stderr — which the app launcher already
captures in `/tmp/sentinelai_launch.log` — and, where a widget is passed,
attaches the reason to it as a tooltip so an empty model dropdown explains
itself without reading a log.

Covered: the eight `*_load_models` methods, `apply_agent_recommendation`,
`_ops_write_env_key`, `load_history_list` and `closeEvent`. The last two are
still non-fatal on purpose (shutdown, and a key that is already saved in the
database) — they just say so now instead of disappearing.

Verified by injecting a `ConnectionError` into a provider: the failure reaches
stderr and the tooltip, the call does not raise, and the UI survives.

One `except Exception: pass` remains on purpose, inside `_note_failure` itself:
the error reporter must never raise.

`RunLogger` was not used for this — its API is run-scoped (`start`/`finish`/
`cancel`) with no general note method. Adding one would be the tidier home for
these if they ever need to be queryable.

## 6. Test coverage is inverted

The scenario tests cover agent prompt construction, but nothing covered
routing, cost estimation, validation or history — the money logic was the
untested part.

**Status: DONE (2026-08-12).** `tests/test_cost_and_limits.py` adds 31 tests
over the two things that decide whether money is spent and how much:
`Validator` (all ten rules, incl. per-agent/session/daily caps, the paid-provider
checkboxes, and ollama's exemption) and `UsageTracker` token/cost accounting
(each SDK's token naming, exact/estimated/mixed, cost invariants).

They are real tests, not decoration — verified by mutation: deleting the
session-budget check fails `test_request_over_session_budget_is_refused`, and
making the token estimate return zero fails three tests. Both mutations were
reverted and the sources confirmed byte-identical.

Design notes: `Validator` takes its registry by injection, so the gate tests use
a stub and never touch the database. `calculate_cost_eur` does read the pricing
table, so it is asserted on invariants (local is free, unknown backend is free,
cost rises with tokens, never negative) rather than hardcoded prices, which
would break whenever pricing is edited.

`authorize_request` / `record_request` / `abandon_request` are now covered too
— `tests/test_request_guard.py`, 30 tests. It builds `GodAI` once per module
(constructing it costs ~10s, so per-test was 118s and unusable) and swaps the
usage tracker, chat history and run logger for fakes, so no test bills a request
or writes into `data/chats/` — verified by comparing row and file counts either
side of a run.

What it pins is the behaviour where a bug costs money: a blocked or declined
request opens no run, recording without authorising bills nothing, double-record
bills once, and an abandoned request stays unbilled even if a late response
arrives. Mutation-verified — making `record_request` or `abandon_request` leak
their pending context fails the double-billing tests.

Deliberately not duplicated: the ten `Validator` gates and the token/cost maths
stay owned by `test_cost_and_limits.py`. `test_request_guard.py` keeps only three
edge cases that file does not reach, one of which pins a float-precision quirk —
`1.00 - 0.90 == 0.09999999999999998`, so a request estimated at exactly the
remaining budget is refused. It fails safe, and a switch to `Decimal` should
flip it.

## 7. Full GUI overhaul

The colours are right; hierarchy, density and information design are not. Three
measurements rather than opinions: the type scale was five sizes of which four
read as one; **12 `_parse_*_sections` methods** structure every agent's answer
and **70 text panes** then render it flat; and three control rows sit between
you and the input box.

Direction and mock screens: **`docs/gui_redesign.md`** plus the published
mockup (run bar, section-rendered Trace output, narrow-window reflow).

- [x] **Type and spacing scale** — three sizes, two weights, documented in
      `ui/style.py` with the 15px section-title role reserved.
- [x] **Status rail figures** — `Meter`/`Bar` in `ui/widgets.py` driving system
      and budget; exact numbers moved to tooltips.
- [x] **Flat agent list.** `CollapsibleSection` is gone from the sidebar —
      confirmed no references remain in `main.py` or `ui/panels/`.
- [x] **Run bar.** `execution_mode_box` and the provider/model tools now sit in
      a hidden combo driven from a submenu (`add_combo_submenu`) rather than a
      permanent control row.
- [x] **Section renderer, Trace.** `ui/panels/osint.py` builds a `SectionView`
      for its output. The remaining five agents are tracked separately above
      (item 2 in the v2 checklist).

Deliberately not proposed: a new palette (the one part that is not broken), and
a command palette (the sidebar was never the bottleneck — the control rows
were; revisit once the run bar exists).

---

## Smaller items

- Saved Chats: **DONE (2026-08-12)** — agent filter above the search box, and
  double-click to rename. The filter is built from the chats that exist, so it
  only offers agents actually used, and it intersects with the search box rather
  than overriding it. Rename writes the `title` field that
  `chat_title_from_data` already preferred but nothing ever wrote. One pass over
  the files serves both the filter options and the rows, so no extra disk reads.

  Next step is **Chat Projects Stage 2** — see `docs/projects_roadmap.md`. Stage
  1 (this) made the list tidier; Stage 2 turns a group of chats into a context
  bundle (instructions, defaults, optional budget), which is the part that
  earns its keep. The backend side is now in place (`projects` table, registry
  CRUD, `save_chat(project=...)`, and placeholder state in `main.py`) — what's
  left is entirely UI: a picker, a filter, and a way to create/assign a
  project. See the checklist item above.
- `BUDGET` card: `Session €` / `Daily €` could share one row (~34px saved), but
  the two label+field pairs do not fit the sidebar's ~250px inner width without
  shortening the labels.

# Sentinel — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this) · `@local` (a local model can do this, with checks; see **Local-model plan** under Detail)

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
- [ ] `P0` `testing` `security` `@ai` **Testing roadmap — safety gate.** **Done 2026-10-11:** deny-by-default network/process fixture (`tests/conftest.py`, markers `allow_network`/`allow_process`; Qt's own network stack not covered), and saved chats / audit logs redirected with a hash check on the real `data/chats` and `data/logs`. **Done 2026-10-13:** the eight-agent by six-boundary matrix is in `docs/testing_safety_matrix.md` (33 of 48 cells already covered, 5 gaps closed, 8 not applicable). **Still open:** one minor gap (Bug Spray `record_nmap_attempt` redaction canary), the Sentry baseline-reset atomicity bug recorded in the matrix, and installed-app Quit-stays-quit smoke. See `docs/testing_roadmap.md`.
- [ ] `P1` `testing` `@local` **Testing roadmap — workflow matrix.** Complete success/error/cancel/persistence contracts for all eight agents, their distinct modes, all shared controls/providers/settings, and the separate companion-repo test runs. See `docs/testing_roadmap.md`.
- [ ] `P2` `testing` `docs` `@ai` **Testing roadmap — release acceptance.** Automate source/frozen/USB smoke where feasible, run owned-lab hardware/network cases and first-user Learning Centre review, and record unverified modes explicitly. See `docs/testing_roadmap.md`.
- [x] `P1` `feature` `docs` `@ai` **Learning Centre — complete curriculum.** Searchable Quick Start, workspace tour, controls and Settings, all seven agent courses, privacy/cost, troubleshooting, multi-agent workflows and advanced-tools guidance are shipped.
- [x] `P2` `docs` `design` `@ai` **Learning Centre — reproducible screenshots.** Nine current-interface images are generated from isolated fictional/empty state by `scripts/capture_training_screenshots.py`.
- [ ] `P2` `testing` `docs` `@ai` **Learning Centre exercises and first-user validation.** Add beginner, intermediate and independent exercises per agent, then test Quick Start with new users.
- [ ] `P1` `infra` `@ai` **Shared Lab platform package.** Extract provider clients, limits, usage, registry, database, runtime paths, request guard and common UI only when the other hubs are ready to consume one version.
- [ ] `P1` `infra` `security` `@ai` **External-tool adapter framework.** Dependency checks, structured output, previews, timeouts, cancellation, local audit records, privilege/scope gates and explicit cloud-handoff consent.
- [x] `P1` `feature` `security` `@ai` **Tunnel phase 3b — gated WireGuard execution.** `services/vpn_execution.py` wraps Connect/Disconnect: target review with blockers (template, missing tool/file, a config name wg-quick rejects, already up) and warnings (split tunnel, world-readable config, clear/blocked traffic after disconnect), the review as a default-No confirmation (Disconnect now confirms too), re-review in the worker, a fresh post-change check (`/var/run/wireguard` records, tracked OpenVPN process, route interface for full tunnels), rollback steps, and a JSONL audit (`data/logs/tunnel_audit.jsonl`, mode 600, key-free) that also records kill-switch changes. OpenVPN — already live since phase 4 — gets the same review, check and audit. Also fixed: `wg-quick` is now invoked by absolute path with its directory on PATH, because the admin dialog's PATH has no Homebrew bin. Live behaviour against a real endpoint is still the `@me` item above. `tests/test_vpn_execution.py`.
- [x] `P2` `bug` `@ai` `agent:vpn` OpenVPN has the same PATH gap `wg-quick` had: `openvpn_manager.connect` runs a bare `openvpn` through `run_as_root`, and the authorisation dialog's PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) has no Homebrew bin. Resolve it with `shutil.which` the way `vpn_connection.wg_quick_command` does. Fixed 2026-10-09 in `e1255e1` (D2): `openvpn_manager.openvpn_binary()` names the client by absolute path, falling back to the Homebrew `sbin`/`bin` locations.
- [x] `P2` `feature` `@ai` **OSINT Keys tab in Settings.** New "OSINT Keys" tab added to `ui/dialogs.py` `show_settings()`: operational email field with clipboard copy, live "X / 18 registered" progress bar, filter chips by category/cost, and one row per tool in `OSINT_TOOLS` (registration checkbox, Register → launcher, masked API key field with show/hide, Save Key button that writes to `.env` and `os.environ` live). Ops email and registration states persist to SQLite via `get_setting`/`save_setting`. See `SUGGESTIONS.md` for next-step ideas.
- [x] `P2` `feature` `@ai` **API Keys tab in Settings (2026-10-11).** Paste-and-save rows for OpenAI, Anthropic, Gemini, DeepSeek, Kimi and Qwen in `ui/dialogs.py` `show_settings()`. Reuses the OSINT tab's `.env` writer, then rebuilds that provider's client on the app and refreshes the left-rail API KEYS card, so a saved key applies without a restart. No validity check on Save (the OSINT tab has one in `providers/key_check.py`); `DASHSCOPE_BASE_URL` is still `.env`-only.
- [x] `P1` `feature` `security` `@ai` **Live OSINT source expansion (v2.002).** Ten new normalised live sources across the providers, each key-gated where paid and self-skipping without its key, all returning text metadata only. IPs: Shodan InternetDB, IPinfo (`IPINFO_API_KEY`) and Criminal IP (`CRIMINALIP_API_KEY`) in `providers/domain_lookup.py`. Company/org: CourtListener court dockets (`COURTLISTENER_API_KEY`, keyless-friendly, `type=d` so no document text/PDF is ever fetched) in `providers/company_lookup.py`, always on in Trace's company Live Research and in Bloodhound's organisation collection (Bloodhound passes `offshore_leaks=True, sanctions=True, court_records=True` at `agents/osint_heavy_agent/__init__.py:425`; ICIJ Offshore Leaks is Bloodhound-only). Exposure: DeHashed v2 (`DEHASHED_API_KEY`, metadata-only — breach names and counts, never leaked passwords/hashes) in `providers/exposure_lookup.py`. Tunnel: a native "Your IP & DNS" readout (local/tunnel address, public exit IP with VPN/hosting flags) and a real bash.ws DNS-leak test in `agents/vpn_agent/services/{public_ip,dns_check}.py`. addy.io burner-alias minting (`ADDYIO_API_KEY`, user-triggered write only) in `providers/alias_mint.py` and the OSINT Keys tab. +34 tests including metadata-only boundary tests for DeHashed and CourtListener; verified by a re-verification and an 18-agent adversarial-review workflow. `OSINT_TOOLS` and `.env.example` updated for the new keys.
- [ ] `P2` `testing` `@local` **Unit tests for `services/osint_catalog.py`** (the OSINT Framework catalogue behind Trace's and Bloodhound's source suggestions, added 2026-09-28 in `f2dbf8c`). Parsing, per-agent filtering (Trace: free, no account, passive, no people-search/dating; Bloodhound: scope limits, ACTIVE tag), the shadow-library blocklist, weekly cache refresh and the offline fallback are only hand-checked so far; `tests/conftest.py` already isolates the cache.
- [ ] `P2` `infra` `@me` **ISP router refuses connection bursts.** The router at 192.168.0.1 (behind the GL.iNet at 192.168.10.1, so the network is double-NATed) has flood protection that refuses new connections when one device opens them fast: measured 2026-09-28, 218–293 of ~600 refused during a WhatsMyName sweep, while both routers stayed reachable. Sentinel now backs off and retries (`7e7cfea`), at the cost of ~56 s per sweep instead of ~20 s. Fix at the router: put it in modem (bridge) mode, or relax its flood/DoS protection.
- [x] `P2` `feature` `design` `@ai` **Menu bar item, and the identity macOS shows for the process (2026-10-06).** Sentinel now puts a status item in the menu bar while it is open: a template-image shield glyph (`assets/tray.png` + `@2x`, drawn by `scripts/make_icon.py` so macOS recolours it for a light or dark bar), and a menu holding a live readout — working/idle, which agent, what the session has cost — plus Open and Quit. Quit goes through the window's `closeEvent`, not `app.quit()`, so an in-flight request is still cancelled and panel background work still shut down. Closing the window quits as before; nothing hides to the menu bar. `ui/tray.py`, wired in `main.py`'s entry point behind `tray.available()`. **`ui/appkit_guard.py` ported from Lab Hub in the same change** — AGENTS.md requires it for any app in this workspace with a tray menu, because macOS 27 makes `-[NSEvent clickCount]` raise when a menu begins tracking and the process aborts; Sentinel can be mid-run and mid-spend when that menu opens. Separately, running from the checkout the application menu beside the Apple logo was titled after the script and the Dock showed the Python icon: macOS resolves identity from the running executable's path, and `.venv/bin/python` is not in a bundle. `ui/app_identity.py` writes `CFBundleName` into the main bundle's info dictionary before `QApplication()` and sets the Dock icon via `-[NSApplication setApplicationIconImage:]` after it (`QApplication.setWindowIcon` does not touch it on macOS — checked by fingerprinting the icon's TIFF data), and it absorbs the old `_activate_native_app` ctypes block so the project has one AppKit module rather than three copies of the plumbing. Measured, so it is not re-litigated later: the launcher's `fork()` is **not** the cause — a plain `exec` loses the identity too — so `scripts/thin_launcher.c` is unchanged; and the name under the Dock icon / in Force Quit stays `python` in both source modes, because Launch Services took it from the executable at launch. +23 tests (`tests/test_tray.py`, `tests/test_app_identity.py`, `tests/test_appkit_guard.py`); full suite 931 passing.
- [x] `P2` `design` `@ai` **Best fit is a badge, not a colour (2026-10-06).** Every agent's provider and model dropdown now marks its recommendation with an accent **BEST FIT** pill beside the entry, the same badge Imprint paints, with the reason on hover and on the closed control. It replaces a red `ForegroundRole`, which lost every argument it was in: the amber "costs money" dot painted over it on all six cloud providers — the recommendation was invisible in exactly the menu where it mattered — and `mark_oversized_models` writes the same role, so it had to skip any item that already carried a colour and the two markings cancelled each other out. `RECOMMENDED_ROLE` / `RECOMMENDATION_REASON_ROLE` / `RECOMMENDATION_BADGE_ROLE` in `ui/widgets.py` carry the recommendation as data; a new `SelectorMenu` paints the pill over the finished QMenu (`actionGeometry` for the row, `reserveBadgeRoom` so it cannot land on a label) and keeps hover, keyboard navigation and the stylesheet untouched. Cost keeps its own two channels — the control still turns amber while a paid route is selected, and every cloud entry says so on hover — but no longer spends the dot, which now means only "where am I" (current) and "this machine cannot run it" (grey). The red deviation border went with the red entry; the closed control's tooltip names the best fit instead. +19 tests (`TestBestFitIsPresentedAsABadge`, every agent including Sentry); full suite 950 passing.
- [x] `P1` `infra` `docs` `@ai` **The rename is finished: no "fork" left in the name (2026-10-06).** The application became Sentinel on 2026-09-12 but the checkout stayed `sentinel_fork` and the GitHub remote stayed `sentinel-ai-fork`, which is what every path, every Lab tool and every document still read. Renamed: the GitHub repository to `wwds-dev/sentinel` (GitHub redirects the old URL, so an old clone keeps working), the checkout to `~/Documents/lab/active/sentinel`, and every reference in this repo's docs, in the workspace `AGENTS.md`/`README.md`, in Lab Hub's app registry and tests, in git_autosync's seed and live config, and in the Lab Project Monitor's generator. The one thing deliberately *not* renamed is the legacy data-migration strings — `/Applications/Sentinel Fork.app`, `~/Library/Application Support/Sentinel Fork`, `Sentinel Fork Data`, `LEGACY_APP_NAMES` — because they name the directories an older install left behind and are only ever read, never written; renaming them would orphan that data rather than tidy anything up. Each is now commented to say so. The last identifier still carrying the old name, the wordmark label `fork_brand` in `main.py`, became `brand` in `d6118c9` (on screen it always read SENTINEL).
- [x] `P2` `design` `@ai` **Three colour themes, picked from three dots (2026-10-06, `89fe9a9`).** Green (Matrix), Red and Blue (Cyberpunk), chosen from `ThemeDots` in the brand row or **Settings → General → Theme** (live preview, Cancel restores). `ui/theme.py` hue-rotates the single green stylesheet inside a 80–185° band, so semantic red/amber/blue and Beacon's monitor-mode green pass through unchanged; the blue accent sits 24° from the informational blue and a test fails below 20°. `ui/vibe.py` adds a per-theme underscore caret cadence in the composer, a faint empty-transcript backdrop (rain / hex dump / grid) that stops as soon as there is text, and blue-theme focus brackets. `tests/test_theme.py`, `tests/test_vibe.py`.
- [x] `P2` `infra` `@ai` **Bug Spray gitlink points at a commit the remote has (2026-10-06, `b01de42`).** `agents/bug_spray` sat four commits behind its own `origin/main` — the v2.002 code-review fixes recorded above were merged but never pinned — and the rename commit had pinned a commit on no remote branch. Now `7ebb2ac`; Sentinel 950 and Bug Spray 52 tests pass.
- [ ] `P2` `docs` `@ai` **Re-capture the Learning Centre screenshots.** All nine images in `docs/training/images/` were last regenerated on 2026-09-28, so they predate the theme dots in the brand row, the BEST FIT badge, the per-theme empty-transcript backdrop, and the screen-style sidebar tiles with the Model Updates card (2026-10-07). Specifics: `workspace-chat.png` and `tunnel.png` still show `v2.001` and a sidebar with no Sentry row; `tunnel-action-preview.png` also shows `v2.001`; `tunnel.png` lacks the Your IP & DNS group; `trace.png` predates Trace's Exposure Check button and the Stop button that replaces the run bar while a request runs; `bloodhound.png` predates the image-metadata (EXIF) checkbox. `docs/training/trace.md` and the `tunnel.png` alt text in `docs/training/tunnel.md` carry captions saying so, to be removed once those images are re-captured. Re-run `scripts/capture_training_screenshots.py` (consider pinning the green theme so captures stay reproducible). The menu bar item is outside the window and is described in text only.
- [x] `P1` `feature` `@ai` **Model choice on value, not novelty (2026-10-07).** *Model Updates* (left rail): at every start, and on **Check now**, each provider with a key is asked for its live model list (free, no prompt, one provider at a time), and models that appeared since the last check are listed as rows; click to select, click again to clear, **Update N** brings in exactly the selected ones (`services/model_watch.py`, `ui/model_updates.py`). The first scan only reports successors of models Sentinel rates, and only the newest release per model line. *Routing:* each request is classified and every usable model looked up on the public LMArena leaderboard for that kind of work; the cheapest model within 20 points of the best wins (Cost first 50, Quality first 0), with real prices from Settings → Pricing. A cloud model with no price or a zero price is never cheap; an unrated model (including local Ollama models) is never chosen over rated ones outside Local only / Privacy first. Each agent's BEST FIT is the same assessment over the providers with keys, re-derived after a scan, a price edit, an update or a priority change, without changing selections (`services/benchmarks.py`, `route_request`, `derive_agent_recommendations`). Ratings refresh at most daily via Hugging Face's row pager — its query endpoint kept answering "index is loading" — paced at 1 s because it returns 429 after ~40 quick requests; finished categories are kept and the rest fall back to the cache or `config/lmarena_snapshot.json` (2026-10-02). Also: catalog/prices for `claude-opus-5-5`, `claude-sonnet-5-5`, `gpt-5.5`, `gpt-5.4-mini`, `gemini-3.1-pro-preview`, `gemini-3.8-flash` from the providers' own pages, with the Gemini 3.8 Flash price doubling on 2027-01-01 applied automatically; **Options → Routing priority**; every sidebar tile redrawn as a small screen (`ScreenCard`, status light, segmented meters); the "No project" picker sized to its text; Learning Centre lesson *API keys and new models*. 1,020 tests.
- [x] `P1` `bug` `@ai` **Prices the router cannot see.** Gemini's `default` pricing row is 0/0, so Gemini 2.5 models are costed at €0 and never count against budgets, and DeepSeek's current ids (`deepseek-flash`, `deepseek-v4-pro`) have no pricing rows at all, so the router never picks them on cost. **Done 2026-10-13:** every cloud provider has a non-zero `default` row, the missing Gemini and DeepSeek rows are seeded, and a zero rate means "unknown", never free (12 regression tests in `tests/test_price_resolution.py`).
- [ ] `P2` `testing` `@ai` **See a complete live ratings refresh.** In development every live attempt was cut short by Hugging Face rate limits left over from probing (three of five text categories arrived; the rest came from the snapshot, as designed). Check `data/lmarena_ratings.json` after a normal start: no `missing` tables and a current `published` date.
- [ ] `P2` `research` `@me` **Gemini 3.8 Flash is rated only at "high" thinking.** It is now the value pick for most kinds of work (1530 coding against Opus 5.5's 1538, at a fifth of the price). LMArena has no rating for its default setting, so check that Sentinel's default-setting calls hold up, or set the Gemini client's thinking level explicitly.
- [ ] `P2` `testing` `@me` **Generate one real image (gpt-image-1.5).** Image generation was rewritten on 2026-09-28 (`708a61d`, `217ad4c`): the selected model is passed through, the gpt-image `b64_json` reply is saved to `output/images/` and Chat shows the path. It is covered only by fake-client tests (`tests/test_openai_image.py`); both live attempts were refused with 429 `credit_balance_exhausted`, unbilled. After adding OpenAI credits, run one low-quality request (about a cent) from this folder:
  `.venv/bin/python -c "from dotenv import load_dotenv; load_dotenv('.env'); from services.openai_client import OpenAIClientWrapper as W; w=W(); w.client=w.client.with_options(max_retries=0); print(w.generate_image('A small red lighthouse, flat illustration', quality='low'))"` — it should print `Image saved to …/output/images/<timestamp>_gpt-image-1.5.png` and the token counts. Do this before the `gpt-image-2` item below.
- [ ] `P3` `feature` `@ai` **`gpt-image-2`** is listed by OpenAI and flagged by the scan, but not in the catalog: image generation with it was not tested (a test costs money). Verify `generate_image` with it, then add it.
- [ ] `P3` `research` `@ai` Unrated catalog models — `kimi-k2.7-code`, `kimi-k2.7-code-highspeed`, `qwen-flash` — never win against rated ones. Check whether LMArena lists them under other names and add `ALIASES` entries in `services/benchmarks.py`.
- [ ] `P2` `feature` `@local` Automatic Ollama fallback when a cloud budget cap is reached.
- [ ] `P3` `infra` `@local` One retry-with-backoff wrapper shared across providers.
- [ ] `P3` `feature` `@local` Export a run—prompt, response, usage and cost—as one Markdown file.
- [x] `P0` `bug` `@ai` **QA audit defects D1–D7 fixed (2026-10-09).** Found by the promise-by-promise audit in `docs/qa_audit/`. D1 `71019eb`: the kill switch now exempts the tunnel's own transport (it used to drop the tunnel's packets). D2 `e1255e1`: OpenVPN process identity is the raw `ps -ww` command line, so Disconnect works when the data path has spaces (every packaged and portable build). D3 `fbe436e`: Local only never sends a cloud model name to Ollama, and an unticked provider is reported instead of raising. D4 `bfdbd74`: Forge renders spec values as string literals and compiles the scaffold before writing. D5 `b6d3ee6`: Beacon and Sentry AI analysis off by default, with disclosure tooltips. D6 `e244466`: the Tunnel connection worker is cancellable and Emergency Reset reports any failure. D7 `786f70c`: selector menus show the BEST FIT reason and paid-route hover text.
- [x] `P1` `infra` `@ai` **Built-in agent packages in-tree (F1, F2; 2026-10-09).** `chat_agent`, `osint_agent`, `osint_heavy_agent`, `wifi_agent`, `manager_agent` and `sentry` were gitignored nested repos with no remote, so a fresh clone could not import `main.py`. They are now tracked like `vpn_agent` (`0a3c50d`, `f2e0a12`, with each package's docs and Sentry's `launchd/` template); the old nested `.git` directories are in the ignored `_archive/nested_git/`. `keyring` (imported by the Bug Spray companion) added to `requirements.txt`.
- [ ] `P2` `docs` `@ai` Re-capture `docs/training/images/beacon.png`: it shows **AI Analysis** ticked, but the checkbox has been off by default since `b6d3ee6` (D5). Can be done with the full re-capture item above.
- [ ] `P2` `bug` `@ai` The Local only substitution notice (D3) is written to `route_result_label`, which `main.py` creates hidden, so the user sees it only as the run-bar cost tooltip and its `· local only` suffix. Show it in the Current Route card or the route line.
- [x] `P2` `bug` `@ai` `agent:vpn` **`VpnPanel.shutdown()` no longer `terminate()`s (2026-10-14, `17890cb`, branch `fix/no-thread-terminate`).** The diagnostic, IP, DNS and panel workers join against the window's shared quit deadline and are parked if they outlast it; the connection worker keeps its own 2 s join and is detached (S4). `tests/test_panel_shutdown_parks.py`.
- [x] `P1` `bug` `@ai` **No `QThread.terminate()` anywhere (2026-10-14, branch `fix/no-thread-terminate`, `docs/specs/no-thread-terminate.md`, ADR 0001).** Stop and Quit park workers instead of killing them: chat Stop/close, the shutdown loop, VPN, Bloodhound and Bug Spray panels, `vpn_agent` HealthMonitor; every quit leaves through `_leave` and `os._exit`; Quit joins within 2.5 s; a stopped cloud request's late usage is recorded as a cancelled run. Evidence: AST gate `tests/test_no_thread_terminate.py`; Stop under 100 ms (`tests/test_chat_stop.py`); Quit 20 of 20 in time (`tests/test_quit_bounded.py -m slow`); full suite 2616 passed on macOS at Verify.
- [x] `P2` `testing` `@me` Push `fix/no-thread-terminate` and run CI on Linux (2026-10-14, PR #6, run 38609806454): macOS green; Linux 2619 passed, 1 failed in code this branch does not touch (next item).
- [ ] `P1` `bug` `@ai` `agent:vpn` Linux CI fails `tests/test_vpn_execution.py::test_review_surfaces_hooks_in_sections_confirmation_and_warnings` (`review(...).allowed` is False for a `PostUp = /opt/hook.sh up` profile) since `cdc79c9` on `qa/phase-a-fixes`; macOS passes. Merging `fix/no-thread-terminate` (PR #6) waits for this fix (owner, 2026-10-14).
- [ ] `P2` `bug` `@ai` Emergency Reset does not fence every background writer: Sentry's baseline and findings, the WhatsMyName cache and the OSINT catalogue cache can still write into the data folder mid-reset (found by reading, `docs/specs/no-thread-terminate.md` A11 #7). Gate each on `portable_reset.writes_allowed()`.
- [ ] `P2` `testing` `@local` CI skips the `slow` 20-run quit test (`addopts = -m "not slow"`). Run it at Verify and Release (`pytest -m slow tests/test_quit_bounded.py`), or add a scheduled CI job.
- [ ] `P3` `research` `@ai` Verify the google-genai client is safe when a parked request and a new one share it (spec R1); if not, give each request its own client.
- [ ] `P3` `bug` `@ai` Panels' cancelled cloud requests still record no cost (`ui/panels/base.py:453`, spec R6); extend the chat late-cost path to them.
- [x] `P1` `security` `@ai` `agent:vpn` **Audit tier-2 S1-S3 built (2026-10-12, branch `fixes/audit-tier2-2026-10-12`).** S1 review gate (confirmed review travels to `execute(confirmed=)`, config SHA-256, full hooks, disconnect hook scan, OpenVPN blockers, user-writable hook programs block); S2 honest kill-switch status (recorded/stale, JSON marker, marker before the arm script); S3 Sentry refuses in the packaged app and verifies with `launchctl print`. See `docs/specs/audit-tier2-privileged-layer.md` ("Steps S1-S3 built").
- [x] `P1` `security` `@ai` `agent:vpn` **Audit tier-2 round 2 (2026-10-13):** independent review fixes for S1-S3: OpenVPN scanner reads lines like OpenVPN (quoted/escaped names and ambiguous inline blocks block), every path word of a hook is checked, interface-only WireGuard profiles are hashed and scanned, `SaveConfig = true` blocks, Disconnect gets a "Disconnect anyway" risk path instead of blocking, kill-switch stale logic and wording tightened, Sentry reports a failed last run, ungated `wireguard_manager` sudo helpers disabled. Deferred: `launchctl print` still runs on the UI thread. See the spec's "Round 2".
- [ ] `P1` `testing` `@me` `agent:vpn` Run the S2 and S3 items of the owner checklist in `docs/specs/audit-tier2-privileged-layer.md` section 9 on a real Mac (reboot with the switch armed; Sentry Install in the packaged app and from the checkout); S1 needs a real wg-quick Connect with a PostUp hook and a script in a user directory.
- [x] `P1` `security` `@ai` `agent:vpn` **Audit tier-2 S4-S6 built (2026-10-13, branch `fixes/audit-tier2-2026-10-12`).** S4: no `QThread.terminate()` on privileged workers (`detach_worker`), Wait / Quit anyway prompt on close, Emergency Reset waits; S5: explicit kill-switch Verify (administrator dialog every time, read-only `pfctl -s info` / `-s rules`, `verified_armed` / `verified_empty` / could-not-verify, memory only); S6: root loads the validated pf rule text by stdin with a hash-checked root temp fallback, and `pfctl -E` only after a successful load. Round 2 (2026-10-13) fixed the independent review's findings: pf rule injection through scoped/newline endpoints (allow-list grammar), root-only fallback temp path, Verify requires a registered anchor and the full block set, Quit anyway syncs settings, Disarm leaves a `disarming` record. See the spec's section 13 for deviations, limits and the real-Mac checklist (pfctl formats and `pfctl -f -` are UNVERIFIED).
- [ ] `P1` `security` `@ai` `agent:vpn` Audit tier-2 S7-S8 (WireGuard root copy + hash with `vpn_rootscript.py`, OpenVPN run dir/manifest, pid/log relocation) are designed in the spec but not built; S7/S8b need an ADR for `/var/run/sentinel-vpn` first (D8). Until S7, the hash in the review is checked in-process only: root still reads the user's file.
- [ ] `P1` `security` `bug` `@local` `agent:vpn` **MAC changer audit fixes** (Opus audit of `agents/vpn_agent/services/macaddr.py`, 2026-10-21). High: the original address is never saved or restored on quit or crash. Medium: `restore()` can report success without checking; Wi-Fi can stay off after a failed change; the root command is string-built; same-vendor mode ignores the locally-administered rule; verification can go stale after the network rejoins. Plus the missing tests. Tier C: the local model fixes one finding at a time, Opus re-audits only the changed parts. See **Local-model plan**.
- [ ] `P1` `testing` `@me` `agent:vpn` Arm the kill switch with a live WireGuard tunnel and a live OpenVPN tunnel (UDP and TCP) up, and confirm traffic keeps flowing through the tunnel and stops when it drops. D1 is covered only by rule-generation tests (`tests/test_vpn_killswitch.py`).
- [ ] `P2` `testing` `@local` The root `pytest` does not collect `agents/sentry/tests/` (`pytest.ini` has `testpaths = tests`) now that Sentry is in-tree. Add it to `testpaths` or to the release checks.
- [ ] `P2` `docs` `@me` Triage the remaining Narrower and Stub/missing rows in `docs/qa_audit/*.md` beyond D1–D7: for each, decide whether to change the code or reword the promise. The audit tables predate the D1–D7 fixes.
- [x] `P2` `feature` `@ai` `agent:vpn` **Mullvad control and Proton status in Tunnel (2026-10-11).** New **VPN Apps** tab. Mullvad: connect, disconnect and city choice through the official `mullvad` CLI, no credentials, refuses while logged out. Proton: state only. A real `scutil --nc start` on this Mac was accepted by macOS and ignored by Proton's extension (the service stayed Disconnected), so the Proton connect/disconnect buttons were removed. Detection (`services/vpn_apps.py`) feeds Connection Check, the kill-switch guard and the Connect warning. Tests: `tests/test_vpn_apps.py`, `tests/test_vpn_app_control.py`.
- [ ] `P1` `testing` `@me` `agent:vpn` Try the VPN Apps tab against a real Mullvad app: log in with the app, then Connect with and without a chosen city, check the exit address under Your IP & DNS, Disconnect, and confirm Connect refuses while logged out. It is covered only by stubs (Mullvad is not installed on the dev Mac); the `relay list` and `account get` output formats are assumed from Mullvad's help pages.
- [x] `P1` `security` `@ai` `agent:vpn` **Tunnel keeps no logs (2026-10-11).** Three commits. (1) `tunnel_audit.jsonl` and every writer behind it are gone: Connect/Disconnect, refusals, declined confirmations, kill-switch results and the Privacy/Servers gate; the Execution tab says nothing was logged; Bug Spray keeps its own Nmap record. (2) OpenVPN's start-up log is read for the tunnel device, which is stored as `vpn/openvpn.device`, then deleted; Tor's start-up log is deleted when Start has an answer; `services/tunnel_logs.py` removes the old `tunnel_audit.jsonl`, `openvpn.log` and `tor.log` at launch. (3) Advisor requests create no Saved Chats entry and no Run log row (`NO_CONTENT_LOG_AGENTS` in `main.py`); the usage row stays because the budget caps are computed from it (agent, provider, model, tokens, price, no text). Kept on purpose: macOS's own records, and the provider's own records of an Advisor question. `tests/test_tunnel_keeps_no_logs.py` keeps it gone.
- [ ] `P1` `testing` `@me` `agent:vpn` Verify the OpenVPN device capture against a real server: Connect with an `.ovpn`, confirm `vpn/openvpn.log` is gone within ~15 s, `vpn/openvpn.device` holds the right `utunN`, and Arm still works for it. It is covered only by injected tests; the "Opened utun device" line is assumed from OpenVPN's output at `--verb 3`.
- [x] `P1` `feature` `@ai` `agent:vpn` **Third-party VPN app detection (2026-10-11).** `services/vpn_apps.py` reads Mullvad (`mullvad status`) and ProtonVPN (`scutil --nc list`) without logging in or connecting; Connection Check shows a "VPN apps" card and summary, the kill switch refuses to arm while an app is connected (or Mullvad's state cannot be read), and the Connect review warns about stacked VPNs. Verified against this Mac's real Proton service; Mullvad is covered by stubs only. `tests/test_vpn_apps.py`.
- [ ] `P2` `docs` `@ai` Retake Tunnel screenshots for the new VPN Apps tab: `tunnel.png` predates the tab (and the Your IP & DNS group), and no image shows the Mullvad/ProtonVPN cards or the Connection Check "VPN apps" card. Re-run `scripts/capture_training_screenshots.py vpn` with stubbed apps, then drop the "predates" caption from the `tunnel.png` alt text in `docs/training/tunnel.md`.
- [ ] `P2` `feature` `@local` `agent:vpn` Provider-aware exit check: when a provider is detected or selected, confirm the exit address really belongs to it (for Mullvad, its public check endpoint) and show the result next to **Check public IP**. Name the destination in the consent text like the existing checks.
- [ ] `P2` `feature` `security` `@local` Network-posture gate for the other agents: before live lookups in Trace, Bloodhound and Bug Spray, read Tunnel's last verified state and warn or block if the exit address is not the expected one. Opt-in per agent, default off, no network contact of its own.
- [ ] `P2` `feature` `@local` Per-agent route profiles (direct, via Tor on `127.0.0.1:9250`, via the proxy chain) selectable per agent. Today no Sentinel agent uses the Tor client or the chain; each must be tested for leaks (DNS in particular) before it claims to use them.
- [ ] `P2` `feature` `@local` `agent:vpn` In-panel **How-to** button in the Tunnel panel that opens the "How-to: get a working tunnel" steps, the "Scenarios: which functions to use when" section (VPN app running, no VPN, own server, provider config) and the command list from `docs/training/tunnel.md`, with a copy button per command. Render from that lesson so the panel and the Learning Centre cannot drift apart. Add a test that the panel text matches the lesson.
- [ ] `P2` `feature` `@ai` `agent:vpn` Add Nym (mixnet) to Tunnel alongside Tor, proxy chain and multi-hop: drive the official NymVPN client (mixnet mode), show its status in Connection Check, and include it in the leak/DNS test. First confirm the current NymVPN CLI/daemon interface; state in the UI that mixnet mode is slower than Tor and has a smaller anonymity set.
- [x] `P1` `security` `@ai` **The `.env` is kept owner-only and tightened at launch, not at import (2026-10-12, `b56f5ab`, `a78a5d5`).** Key writes are atomic `0600`, and `tighten_env_files()` re-restricts each `.env` Sentinel may read (checkout, Application Support, portable) to `0600` at startup, skipping symlinks and reporting by path only. Moved from import into the `__main__` entry point so importing `main` in the test suite no longer chmods the real key files. `services/runtime_paths.py`, `tests/test_env_permissions.py`, `tests/test_key_check.py`.
- [x] `P2` `security` `@ai` `agent:wifi` **Beacon declines injection operations on adapters that cannot inject (`1ac989e`).** When the selected adapter's chipset lacks packet injection, `build_kali_commands()` returns an explanation naming the limitation and a supported-adapter hint instead of emitting a command sequence; passive operations are unaffected. README and the Beacon lesson now say so.
- [x] `P2` `feature` `@ai` `agent:bug_bounty` **Bug Spray passes an optional Severity Target to the report-draft request (`0e89aee`).** The hint reaches the model prompt as an explicitly unverified operator input ("Reporter's severity expectation (unverified; rate from the evidence)"); nothing in Bug Spray confirms or enforces it.

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


---

## Local-model plan (added 2026-10-21)

Owner tag `@local` = a local model drafts it (LM Studio/Bionic on the owner's Mac), then the checks below decide whether it is accepted. The tier is a prior about how risky the task is; it is not a probability that the code is correct. Only the checks establish that.

**Tiers**
- **A (high suitability):** mechanical or fully specified, small blast radius, verifiable by tests. Checks: tests pass, `ruff` clean, diff skimmed.
- **B (medium):** UI or broad test-writing. Checks: tests pass, mutation Verify kills the mutants, Sonnet reviews the diff.
- **C (low, draft only):** leak-, cost- or privilege-sensitive. Checks: tests including failure cases, Opus audit (high effort) before merge, and re-audit of every changed part.

**Models (from the library)**
- Tier A/B: Qwen3 Coder 30B A3B (MLX), fast, tool-capable.
- Tier C drafts: Qwen3.8 27B (MLX), slower but stronger.
- Abliterated models (Huihui Qwen3 Coder 30B, Huihui Qwen3.8 27B): only if the standard model declines a task. Not expected for these items.

**Rules**
- One item or one function per task; keep context to about 16k, never feed whole big files (`main.py`, `killswitch.py`, `vpn_execution.py`, `database.py`).
- Commit trailers: `drafted-by: <model>` and `verified-by: tests, ruff, <mutants|sonnet|opus>`; no `verified-by` line means unverified.
- Record the result on the item when done: `Verified: tests ✓ · ruff ✓ · mutants ✓ · review ✓`.
- Two failed fix rounds on a Tier B/C item: escalate it to `@ai`.

| Line item | Tier | Model |
|---|---|---|
| Unit tests for `services/osint_catalog.py` | A | Qwen3 Coder 30B |
| Add `agents/sentry/tests` to pytest `testpaths` | A | Qwen3 Coder 30B |
| Run the `slow` quit test at Verify/Release | A | Qwen3 Coder 30B |
| Shared retry-with-backoff wrapper | A | Qwen3 Coder 30B |
| Export a run as one Markdown file | A | Qwen3 Coder 30B |
| Testing roadmap: workflow matrix | B | Qwen3 Coder 30B |
| Tunnel panel How-to button | B | Qwen3 Coder 30B |
| Automatic Ollama fallback at budget cap (cost gate) | C | Qwen3.8 27B |
| Provider-aware exit check | C | Qwen3.8 27B |
| Network-posture gate for Trace/Bloodhound/Bug Spray | C | Qwen3.8 27B |
| Per-agent route profiles (Tor, proxy chain; DNS leaks) | C | Qwen3.8 27B |
| MAC changer audit fixes (`macaddr.py`) | C | Qwen3.8 27B |


**MAC changer findings (Opus, 2026-10-21; line numbers as reported, not yet re-checked)**
- High 1: no restore on exit or crash. The app exits through `os._exit` (ADR 0001), so cleanup must run in `main.py` `_leave`. Write device + original address to a small state file after a successful change; restore from it in `_leave`; check for a leftover entry at startup and offer to restore.
- Medium 2: `restore()` returns success when `current_mac` is empty (`macaddr.py:104-106, 221-222`, same check in `vpn_privacy.py` `restore_mac`). Treat empty as unknown and fail.
- Medium 3: Wi-Fi can be left off after a timeout in the root command (`macaddr.py:176-192`); steps joined with `; ` hide the `ifconfig` error. Use `&&` for the address step, always run power-on last, report power state and address on failure.
- Medium 4: string-built root command (`macaddr.py:176-188`). Validate the device name against `^[a-z]{2,8}[0-9]{1,3}$` first; better, a fixed root helper taking device and address as separate arguments.
- Medium 5: same-vendor mode skips the locally-administered bit and silently falls back (`macaddr.py:138-141`, `vpn_privacy.py:98`). Remove it, or warn and report the mode actually used.
- Medium 6: verification at `macaddr.py:194` can go stale when macOS rejoins the network. Re-check after rejoin and word the status accordingly.
- Low 7-12: stale interface data in the confirm dialog; `is_valid` accepts `00:00:00:00:00:00`; catch-all `except` in `vpn_privacy.py` `refresh_interfaces`; no dialog when already root (`privileged.py:111-112`); unused `subprocess` import; action log keeps old and new addresses (keep it local).
- Test gaps: `random_mac` bit handling, `is_valid` edge cases, `restore()` including the empty-address case, root command failing or timing out partway, Wi-Fi left off, restore on exit or crash. The existing hostile-device test stubs `get_interface`, so it never exercises quoting or name validation.

**Stay `@ai`:** S7-S8 privileged layer, failing Linux CI test, Emergency Reset writer fencing, cancelled-request cost recording, Nym (needs web research), platform package, adapter framework, `main.py` refactor.
**Stay `@me`:** every live-hardware check (routing/DNS, kill switch with live tunnels, Mullvad app, OpenVPN device capture, S2/S3 checklist).
**Seen so far with cloud models:** a Sonnet 5.5 session was flagged `[cyber]` on a plain git merge of `fix/no-thread-terminate` into `main` (2026-10-20); no specific item has been refused. Opus audited `agents/vpn_agent/services/macaddr.py` on 2026-10-21 with no refusal. If a Sentinel session is flagged, rewind and rerun it on Opus (Fable has the most cyber safeguards, so avoid it here). Do merges, commits and test runs without a model where possible.

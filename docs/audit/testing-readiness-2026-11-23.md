# Testing readiness audit — 2026-11-23

Promise-by-promise readiness check of Sentinel v2.002 at `22b965d` (`main`, after the agent-layouts merge),
before the formal testing phase. Foundry phase: **Verify (7)** for the whole app. Follows
`docs/qa_audit/readiness-2026-11-18.md` (at `e7f53a0`) and the 2026-10-09 per-agent files in `docs/qa_audit/`;
this file re-states each agent's headline promises against the code and the tests, rechecks every open finding
of 2026-11-18, and adds what is new.

Read-only: no application code changed, nothing ran as root, no tunnel, kill-switch, MAC, pf, Wi-Fi or
network-posture action was taken. Method: four read-only reviewers (Tunnel; Chat, Trace and Bloodhound;
Sentry; Bug Spray and Forge) read docs, code and tests; the author re-read every new High before it went in
(H7 and the CI failure were reproduced or read at the cited lines). **Beacon was not rechecked**: the automated
reviewer was stopped by the model provider's safeguards, so Beacon's rows carry the 2026-10-09 / 2026-11-18
status forward (§4.7). Line numbers are at `22b965d` as the reviewers read them.

Status words: **Kept+tested** — code does it and a test targets it. **Kept, no test** — code does it, nothing
tests it. **Narrower** — code does less than the text says (how is stated). **Missing** — no code.
**Live only** — code exists and is unit-tested with fakes, but the promise can only be confirmed on real
hardware, accounts or networks.

---

## 1. Verdict

**No-go today.** Five things stand between the app and a testing phase whose results mean something:

| # | Blocker | Owner | Size |
|---|---|---|---|
| G1 | **CI is red on `main`** (new). `tests/test_bug_spray_layout.py::test_shown_tree_viewport_is_whole_rows_and_fills_the_tab` fails on a clean checkout: CI macOS and Ubuntu on `22b965d` and `2c05b24`, and this worktree (3 of 3 runs). It passes in the main checkout at the same commit, so it leans on local untracked state (`sizeHintForRow(0)` is `-1`: the tree has no laid-out row). The Definition of done needs CI green | @ai | S |
| G2 | **H3: Local only fails open in agent panels** (still open). An advisor exception turns Local only into "cloud after one Yes" | @ai | S |
| G3 | **H7: Tunnel's "TUNNEL DROPPED" warning never fires for WireGuard** (new). The monitor watches the profile's interface name; the source returns `utunN` socket names | @ai | S |
| G4 | **Owner decisions H1/H2 (kill switch and DNS) and H4 (default mode)** (still open, P0 in TODO). Each changes what "pass" means for a tester | @me | decision |
| G5 | **Expected results are wrong in the docs** for several agents (§5): some promise more than the code does, several describe the code as weaker than it now is. Testers would fail correct behaviour or pass wrong behaviour | @ai after G4 | M |

Fixed since 2026-11-18: **B1** (no cloud model listing at launch, `f1fbac8`), **B2** (venv now matches
`requirements.txt`: pillow 13.0.0, certifi 2026.10.23, paramiko 5.1.0, keyring 25.7.0), **B3** (reset fence,
`68089cd` + `fee5ec9`, PR #52 closed as reworked).

What holds up: every privileged action still goes through the macOS dialog with no cached sudo; Tunnel keeps
no logs (tested); Bloodhound File Discovery never reaches a model (tested); Forge creation is all-or-nothing
(tested); Bug Spray only ever starts nmap, with no shell (tested). Of the 159 promises below,
**109 are Kept+tested** and 22 can only be confirmed live (§2).

## 2. Counts (this audit)

| Agent | Promises | Kept+tested | Kept, no test | Narrower | Missing | Live only |
|---|---|---|---|---|---|---|
| Chat | 21 | 16 | 2 | 3 | 0 | 0 |
| Trace | 19 | 15 | 1 | 2 | 0 | 1 |
| Bloodhound | 21 | 14 | 1 | 3 | 2 | 1 |
| Sentry | 20 | 16 | 0 | 2 | 0 | 2 |
| Bug Spray | 20 | 14 | 2 | 3 | 0 | 1 |
| Forge | 18 | 14 | 3 | 1 | 0 | 0 |
| Tunnel | 40 | 20 | 0 | 2 | 1 | 17 |
| **Total (7 agents)** | **159** | **109** | **9** | **16** | **3** | **22** |
| Beacon | — | not rechecked (§4.7) | | | | |

Rows that are Kept+tested offline but need a live check for the real endpoint are counted as Kept+tested. Bloodhound's two **Missing** are disclosed gaps (private-IP
refusal M11, untrusted-text labelling L9). Tunnel's **Missing** is an IPv6/WebRTC leak *check*, which no text
promises as a feature (only the Advisor talks about it).

## 3. Prior findings (2026-11-18) at `22b965d`

| ID | Item | Status | Evidence now |
|---|---|---|---|
| B1 | Cloud model listing at launch / Local only | **Fixed** | `main.py:1270-1309`; `tests/test_no_cloud_listing_on_ui_thread.py` (forces Local only, so the default launch state is not covered; socket test still open, TODO) |
| B2 | Stale venv | **Fixed** | `importlib.metadata` check, 2026-11-23 |
| B3 | Reset fence (PR #52) | **Fixed** | `68089cd`, `fee5ec9` |
| H1 | Kill switch keeps LAN open, so DNS to the home router leaks | Open | `agents/vpn_agent/services/killswitch.py:58,299,363-369,661,696`; `services/vpn_connection.py:302-303`; tooltip `ui/panels/vpn.py:155-159` still says "cannot leak" |
| H2 | System DNS kept (OpenVPN pushed DNS ignored; WG without `DNS =`) with no warning | Open | `services/vpn_execution.py:1593-1595` info line only; tests lock the text in (`tests/test_vpn_review_gate.py:1222,1459`) |
| H3 | Local only fails open in agent panels | Open | `ui/panel_routing.py:56-61`; `main.py:3685-3686`, `4558-4639` (no mode check); no test |
| H4 | Default mode is "Hybrid allowed"; docs say Local only | Open, **with a twist** | `services/routing_advisor.py:64,93`; `main.py:2842,3860-3869`; tests lock the default (`tests/test_routing_advisor.py:116-117`, `tests/test_routing_phase4.py:228`). New, not run: at launch `install_agent_recommendations` (`main.py:462,2056-2064`) moves Chat's box to the recommended route's mode (`main.py:1050-1057`); with every box unticked that is Local only (`services/model_recommendations.py:94-95`). So the run bar probably *shows* Local only while Settings shows "Local, advise per job". README.md:103 calls `local_advise` the default, contradicting README.md:76-80 |
| H5 | Sentry: connected UDP rows parsed as listeners | Open | `agents/sentry/sentry/collectors.py:27,191-212,294`; reported as "bound to loopback only" (`engine.py:125-139`); no UDP fixture |
| H6 | Sentry: unreadable baseline silently replaced | Open | `baseline.py:160-166,182-188,199-208`; `engine.py:254-266`; a contract test locks it in (`tests/test_contract_beacon_sentry.py:706`) |
| M1 | README says ProtonVPN started/stopped via `scutil` | Open | README.md:20, 302-303, 310-311 |
| M2 | OpenVPN pid/device paths in docs and TODO are old | Open | TODO.md:75, `docs/agents/vpn.md:63,89`, `docs/training/tunnel.md:159-160` vs `services/vpn_rootscript.py:216-217` |
| M3 | IPv4-only config labelled "Full tunnel" | Open | `agents/vpn_agent/services/config_inspection.py:116-121`; only test uses `0.0.0.0/0, ::/0` |
| M4 | Nmap dialog "Target" is not what nmap scans; any option passes | Open | `ui/panels/bug_bounty.py:427,439-445,490` |
| M5 | No check of target against stored program scope | Open | `ui/panels/bug_bounty.py:408-469`; feed loads scope only for display (`ui/panels/bug_spray_feed.py:440-446`) |
| M6 | Doc says nmap starts "with no review step" | Open (docs) | `docs/agents/bug_bounty.md:47`, `docs/training/bug_spray.md:96-99`, README.md:327 |
| M7 | Root PATH starts with user-writable Homebrew folder | Open (owner-deferred D10) | `services/vpn_rootscript.py:72` |
| M8 | D12 hooks folder unbuilt; WG hook interpreter unchecked | Open | no `Library/Sentinel` in code; `_interpreter` only at `vpn_execution.py:925` |
| M9 | Trace live checks and OSINT catalogue ignore Local only | Open in code; now stated in `docs/agents/osint.md:15,66`, `osint_heavy.md:28`; not in `ui/tooltips.py:25` or training |
| M10 | Bloodhound collection failure becomes an empty result | Open | `ui/panels/osint_heavy.py:621-627`; a test locks it in (`TestInvestigateContract::test_failed_collection_still_sends_an_empty_live_list_to_the_model`) |
| M11 | Bloodhound has no private-IP refusal | Open | `non_public_ip_reason` only at `ui/panels/osint.py:439` |
| M12 | Sentry Reset during a pass | Open | `engine.py:244` vs `:252`; Reset never disabled (`ui/panels/base.py:438-443`) |
| M13 | `SentryPanel.shutdown()` missing; `transaction()`/`mkdir` skip `WRITE_GUARD` | Open (save/append guard fixed and tested) | `baseline.py:58,111,121,128,146-157,169-173`; panel `:469-480` |
| M14 | Forge tests never import a scaffold or call `build_messages` | Open (one contract test runs the module body) | `tests/test_agent_factory_validation.py:150-174`; `tests/test_contract_spray_forge.py:314-322` |
| L1 | Nmap starts if its audit write fails; file mode set after write | Open | `ui/panels/bug_bounty.py:105-113,452` |
| L2 | MAC record decode error deletes the record | Open | `agents/vpn_agent/services/macaddr.py:326-327,389-391,453-455` |
| L3 | Two root-runner tests skip | Open | `tests/test_vpn_review_fixes.py:151-152` |
| L4 | `profile_store.py` unused | Open | referenced only from itself |
| L5 | Emergency Reset leaves Sentry's launch agent | Open | `services/portable_reset.py:36-69` |
| L6 | Sentry/Beacon/Forge docs lag code | Open, grown (§5) | |
| L7 | Beacon (see 2026-11-18 §3) | Not rechecked | §4.7 |
| L8 | Commands on the UI thread (Sentry `launchctl`, Beacon) | Open for Sentry (`ui/panels/sentry.py:55,356-365,428`; `watchd.py:90-104`); Beacon not rechecked. **Tunnel adds two** (§4.6 notes) |
| L9 | Bloodhound calls third-party text "confirmed fact" | Open | `agents/osint_heavy_agent/__init__.py:600` (disclosed at `osint_heavy.md:87`) |
| TODO:92 | `load_models` asks Ollama on the UI thread | Open | `main.py:459,3560-3569` |

## 4. Promises, per agent

### 4.1 Chat

| # | Promise (source) | Code | Test | Status |
|---|---|---|---|---|
| CH1 | Starts in Local only, every paid box unticked (README.md:76-80; `docs/agents/chat.md:14,16`; `ui/tooltips.py:25`) | `services/routing_advisor.py:64,93`; `main.py:2842,3860-3869`, `462→2056-2064→1050-1057` | `test_routing_phase4.py` asserts the default is `LOCAL_ADVISE`; nothing checks the box after launch | **Narrower** (H4) |
| CH2 | Local only runs a cloud pick on Ollama or LM Studio; chip says "local only" (chat.md:16) | `main.py:3744-3756,3793-3797` | `test_route_notice_visible.py::test_substitution_notice_is_shown_in_the_route_card`, `::test_lmstudio_only_mac_gets_a_loaded_lmstudio_model` | Kept+tested |
| CH3 | Unticked provider in Hybrid/Cloud only → "Request blocked" | `main.py:3760-3779,4323-4327` | `test_chat_lifecycle.py::test_an_unticked_cloud_provider_blocks_before_a_worker_or_a_run` | Kept+tested |
| CH4 | Every cloud request asks "Confirm External API Request" (chat.md:22) | `main.py:4385-4391,4465-4485` | `test_contract_tunnel_chat.py::TestChatSave::test_denied_declining_the_paid_api_prompt_sends_saves_and_bills_nothing` | Kept+tested |
| CH5 | Permission and session/daily/agent caps checked | `main.py:4370-4383` | `test_cost_and_limits.py` (3 cap tests) | Kept+tested |
| CH6 | Cloud provider without a key refused before a run | `main.py:4348-4354` | `test_audit_must_fix.py::test_a6_chat_blocks_a_cloud_provider_with_no_key` | Kept+tested |
| CH7 | "Model Too Large" / "Run it anyway?" fit check | `main.py:846-869,4396-4397,4784-4792` | worker side only (`test_routing_phase1.py`) | Kept, no test (dialog) |
| CH8 | No cloud contact at launch or in Local only (README.md:97-101) | `main.py:1270-1309,1591-1622` | `test_no_cloud_listing_on_ui_thread.py` (3); `test_model_watch.py` | Kept+tested (socket-level test open) |
| CH9 | Typing makes no network request (chat.md:49) | `main.py:3614-3636`; Ollama `main.py:3560-3569` at `:459` | `test_no_cloud_listing_on_ui_thread.py::test_keystroke_routing_lists_nothing_on_the_ui_thread` | **Narrower**: Ollama still asked on the UI thread at construction |
| CH10 | Auto-route picks only ticked providers | `main.py:3595-3612` | `test_routing_phase0.py::test_enabled_provider_names_follow_the_supported_list` | Kept+tested (Local-only part untested) |
| CH11 | Enter/Shift+Enter/Ctrl+Return/Esc | `main.py:495-501` | `test_ui_panels.py` composer tests | Kept+tested (Esc untested) |
| CH12 | Every provider streams | `main.py:4796-4797` | `test_streaming_usage.py` (stubs) | Kept+tested; real providers Live only |
| CH13 | Stop returns at once, keeps partial text, never terminates a thread | `main.py:5051-5081,6063-6068` | `test_chat_stop.py`, `test_chat_lifecycle.py::test_stop_cancels_keeps_partial_text_and_bills_nothing` | Kept+tested |
| CH14 | A stopped cloud reply is billed once (exact or estimated) | `main.py:4450-4459,4949` | `test_chat_late_cost.py` (2) | Kept+tested |
| CH15 | One file per conversation; errors/stops save nothing | `main.py:4891-4912` | `TestChatSave` (2), `TestChatReopen` | Kept+tested |
| CH16 | Reopen restores transcript and project, not Tool/model | not read | `TestChatReopen::test_positive_reopen_restores…` | **Narrower** (documented limitation) |
| CH17 | Rename, assign, delete with default-No | `main.py:5655-5659` | `TestChatRename` (6), `TestChatDelete`, `test_safety_matrix_regressions.py` | Kept+tested |
| CH18 | Tool sets the system prompt; notices never sent | `main.py:4208,4262,4443` | `TestChatTools`, `TestChatContextAndProjects` | Kept+tested |
| CH19 | Export current report to `data/reports` | `main.py:5671-5683` | `TestChatExport` (2) | Kept+tested |
| CH20 | Cost to the project; one Run log row per request | `main.py:4432-4439,4862-4889` | `TestChatUsageAttribution` | Kept+tested |
| CH21 | New chat stops a running request and keeps Tool/Command/project | `main.py:5661-5669` | none | Kept, no test |

### 4.2 Trace

| # | Promise (source) | Code | Test | Status |
|---|---|---|---|---|
| TR1 | Target validated offline first (`docs/agents/osint.md:11`) | `ui/panels/osint.py:234-240`; `agents/osint_agent/__init__.py:255-287` | `test_contract_trace.py` (2) | Kept+tested |
| TR2 | Structure Query contacts no source; cloud only after confirmation | `osint.py:245,257-268`; `main.py:4577-4615` | `TestAnalyseLocalModel`, `test_request_guard.py` | Kept+tested |
| TR3 | €2.00 per-request cap | `services/agent_catalog.py:35`; `osint.py:280-287`; `main.py:4641-4667` | `test_safety_matrix_regressions.py::TestTraceStructureQueryBudgetRecheck` | Kept+tested (doc understates: full prompt is re-checked) |
| TR4 | Local only never reaches the cloud (README.md:103; tooltip) | `ui/panel_routing.py:45-79`; `main.py:3685-3686,4558-4615` | `test_routing_panels.py::test_local_only_never_offers_the_cloud_on_a_panel` (local selection only) | **Narrower** (H3) |
| TR5 | Live Research consent: default No, names sources and "Treated as" | `osint.py:482-527` | `TestLiveResearch`, `TestTreatedAsAndHints` | Kept+tested (default button untested) |
| TR6 | Keyed sources named only with a key; rest listed as skipped | `osint.py:491-499,798-799` | `tests/test_intel_sources.py` | Kept+tested |
| TR7 | Email: only ticked services; breach off; Gravatar gets a hash | `osint.py:552-613` | `TestTracePanel` (2), `test_identity_lookup.py::test_gravatar_sends_only_the_address_hash` | Kept+tested |
| TR8 | Person and Phone never sent live | `osint.py:450-465` | `TestTracePanel::test_person_and_phone_live_research_stays_local` | Kept+tested (type-dependent, documented) |
| TR9 | Private/loopback IPs — docs say **not** filtered (osint.md:37; training/trace.md:86) | `osint.py:438-449`; agent `:343-368` refuse them | `TestLiveResearch::test_a_non_public_ip_never_contacts_anything_and_asks_no_consent` | Kept+tested; **docs wrong** (§5) |
| TR10 | CourtListener off by default | `osint.py:510-512` | `TestCourtListenerConsent` (6) | Kept+tested |
| TR11 | Exposure Check: picker, metadata only, Incomplete/Not checked | `osint.py:625-671,877-914` | `TestExposureCheck`, `test_exposure_lookup.py` | Kept+tested |
| TR12 | Live checks call no model; logged €0.00 | `main.py:4733-4770` | `TestLiveResearch::test_happy_path…` | Kept+tested |
| TR13 | Stop waits for the source in flight, saves partial as cancelled | `osint.py:380-392,941-962` | `TestLiveResearch`, `TestStop` | Kept+tested |
| TR14 | Stop Structure Query keeps partial text, bills nothing | `osint.py:325-334,393-400` | `TestAnalyseLocalModel` | Kept+tested (tooltip `ui/tooltips.py:110` says discarded) |
| TR15 | "Still finishing the previous request" refusal | `osint.py:212-219` | none | Kept, no test |
| TR16 | Saved searches reopen offline; delete default-No | `main.py:5437-5470` | `TestAnalyseLocalModel`, `TestLiveResearch`, `test_safety_matrix_regressions.py` | Kept+tested |
| TR17 | Execution mode does not gate live checks | `osint.py:430-550,625-671` | none | **Narrower** (M9: the Local-only tooltip says otherwise) |
| TR18 | OSINT catalogue weekly, no target, not skipped in Local only | `osint.py:57-61`; `services/osint_catalog.py:132-175` | `test_osint_catalog.py` (3) | Kept+tested |
| TR19 | Real WHOIS/DNS, BreachDirectory, OpenSanctions | `providers/*` | recorded responses | Live only |

### 4.3 Bloodhound

| # | Promise (source) | Code | Test | Status |
|---|---|---|---|---|
| BH1 | Permission check before any source (`docs/agents/osint_heavy.md:27`) | `ui/panels/osint_heavy.py:557-565` | `TestBloodhoundPanel`, `test_contract_bloodhound.py::TestInvestigateContract` | Kept+tested |
| BH2 | "Contact public sources?" defaults to No | `osint_heavy.py:574-581,607-619` | `TestInvestigateContract::test_denied_consent_names_sources_and_contacts_nothing` | Kept+tested |
| BH3 | Consent list matches what runs | agent `:339-349,355,365` | `test_osint_heavy_collection.py::test_bloodhound_consent_names_exactly_what_domain_lookup_contacts` | **Narrower**: domain/IP fixed (doc stale); email names HIBP/Hunter without a key, crypto names both explorers |
| BH4 | Phone and spaced names contact nothing | agent `:358-363,480-485`; panel `:611-612` | `test_phone_target_contacts_zero_sources`, `TestBloodhoundPanel` | Kept+tested |
| BH5 | Local only not consulted; the dialog is the control | `osint_heavy.py:533-600,526-530` | none | Kept, no test (model call inherits H3) |
| BH6 | Budget re-check on the full prompt | `osint_heavy.py:644-655` | `TestBloodhoundPanel::test_the_assembled_prompt_is_budget_checked_before_the_model_call` | Kept+tested |
| BH7 | EXIF to the model only when ticked; file and name never sent | `osint_heavy.py:96-117,216-224,547-549` | `TestImageExifContract` (3) | Kept+tested |
| BH8 | EXIF from JPEG/PNG/WebP only | `osint_heavy.py:42-60` | `TestImageExifContract` | **Narrower** (documented); HEIC on Pillow 13 Live only |
| BH9 | Private/loopback IPs refused (as Trace) | none | none | **Missing** (M11) |
| BH10 | Failed provider flagged to the model | agent `:411-478,610-611`; panel `:621-627` | `test_failed_collection_still_sends_an_empty_live_list_to_the_model` (locks the gap in) | **Narrower** (M10) |
| BH11 | Third-party text treated as untrusted | agent `:600` | none | **Missing** (L9, disclosed) |
| BH12 | Stop during collection: no bill, no model call | `osint_heavy.py:633-637,708-722` | `TestStopContract` | Kept+tested |
| BH13 | Stop during the model call | `osint_heavy.py:682-691,708-714` | `TestStopContract` | Kept+tested (code better than doc) |
| BH14 | Sources gauge is the real count | agent `:310-320`; panel `:621-627,641` | `test_real_source_count_sums_contacted_sources` + 1 | Kept+tested |
| BH15 | Save Report writes only the dossier | `osint_heavy.py:1001-1015` | `TestInvestigateContract` (2) | Kept+tested |
| BH16 | File Discovery: only added folders, only on Search | `osint_heavy.py:831-880` | `TestLocalFileSearchContract` (2) | Kept+tested |
| BH17 | Metadata only; symlinks skipped; nothing written | `services/local_file_search.py:114-133`; `services/remote_file_search.py:107-121` | `test_local_file_search.py` (2), `test_remote_file_search.py` | Kept+tested |
| BH18 | File results never reach a model; connection not saved | `osint_heavy.py:831-880` | `TestLocalFileSearchContract`, `TestRemoteFileSearchContract` | Kept+tested |
| BH19 | SFTP strict known_hosts, no password, 12 s timeouts | `remote_file_search.py:43-66` | `test_unknown_host_keys_are_rejected_and_no_password_is_used` | Kept+tested; real host Live only |
| BH20 | Limits 5,000 / 250,000; partial results kept | `local_file_search.py:12-13`; `osint_heavy.py:703-738,903-907` | `test_cancellation_and_safety_limits_return_partial_reports`, `TestStopContract` | Kept+tested |
| BH21 | Real collection, WhatsMyName, CourtListener | `providers/*` | stubs | Live only |

### 4.4 Sentry

| # | Promise (source) | Code | Test | Status |
|---|---|---|---|---|
| SE1 | Six read-only collectors, argv lists, 15 s limit (`docs/agents/sentry.md:52-63`) | `agents/sentry/sentry/collectors.py:261-302` | contract `NetWorld.run`; `test_engine.py::test_a_missing_collector_binary_is_recorded` | Kept+tested (timeout untested) |
| SE2 | First pass records a baseline, refuses if a collector failed | `engine.py:254-266` | `test_engine.py` (2), contract (1) | Kept+tested |
| SE3 | New device is a NOTICE, MAC is identity | `models.py:31-35`; `engine.py:46-61` | `test_new_device_is_a_notice` | Kept+tested |
| SE4 | ARP: one IP claimed by several MACs is WARNING/ALERT | `engine.py:66-94` | `test_gateway_impersonation_is_an_alert` + 1 | **Live only**: `arp -an` output cannot produce the duplicate (`parse_arp` dedupes, `collectors.py:100-102`), so it may never fire on a Mac |
| SE5 | Gateway MAC change is an ALERT, reported once | `engine.py:97-115,221-222` | `test_gateway_mac_change_is_an_alert` + contract | Kept+tested |
| SE6 | New listening service keyed by proto/addr/port; WARNING if exposed | `collectors.py:27,191-212,294`; `engine.py:119-147` | `test_parse_listeners_reads_protocol_addr_port` + 1 | **Narrower** (H5) |
| SE7 | New outbound TCP: NOTICE public, INFO private | `collectors.py:215-239`; `engine.py:150-177` | `test_new_public_connection_reported_local_chatter_ignored` + 1 | Kept+tested (INFO branch untested) |
| SE8 | Reported once then folded in; connections capped at 4096 | `engine.py:193-228,268-270` | `test_second_run_reports_then_folds_into_baseline` + contract | Kept+tested (cap untested) |
| SE9 | Dry run saves nothing | `ui/workers.py:852-876`; panel `:202-205,237-255` | contract (2) | Kept+tested |
| SE10 | Reset → next pass records a fresh baseline | `baseline.py:48-63,133-144`; panel `:343-353` | contract (2) | **Narrower** (M12) |
| SE11 | Unreadable baseline treated as none (documented) | `baseline.py:160-166` | contract `:706` | Kept+tested as documented (H6 weakness) |
| SE12 | Stop cancels; stopped pass saves nothing; late result starts no paid request | panel `:188-190,474-480`; `engine.py:246-247` | `test_a_cancelled_pass_persists_nothing`, `TestSentryStop` (3) | Kept+tested |
| SE13 | AI read off by default; sends counts and evidence only | panel `:143,210-221,287-304` | `TestSentryPanel`, contract (2) | Kept+tested |
| SE14 | Failed collector shown amber; quiet message says so | `collectors.py:261-277`; panel `:210-213,241-243` | `test_engine.py` (3), contract | Kept+tested |
| SE15 | Log keeps 500; panel shows 25 newest | `baseline.py:31,207`; panel `:367-404` | contract (2), `test_ui_panels.py` | Kept+tested |
| SE16 | State folder shared with the launch agent via `SENTRY_STATE_DIR` | `baseline.py:99-122`; `watchd.py:64-81` | `test_engine.py` (2) | Kept+tested; launchd run Live only (O9) |
| SE17 | Install confirmed with `launchctl print` | `watchd.py:142-162,191-221` | `test_sentry_watchd.py` (2) | Kept+tested (docs stale, §5) |
| SE18 | Packaged build: Install refused, button greyed | `watchd.py:148-149`; panel `:431-444` | `test_sentry_watchd.py`, `test_ui_panels.py` | Kept+tested (docs stale, §5) |
| SE19 | Status line: running / installed not loaded / off | panel `:445-467` | `test_ui_panels.py` | Kept+tested |
| SE20 | Other users' sockets need root; ARP may read empty | `collectors.py:280-302` | none | Live only |

### 4.5 Bug Spray and Forge

| # | Promise (source) | Code | Test | Status |
|---|---|---|---|---|
| BS1 | Radar reads five public directories, no account, never contacts assets (`docs/agents/bug_bounty.md:10,42`) | `agents/bug_spray/bug_spray/sources/__init__.py:64-84`; `_http.py:28-33` | `agents/bug_spray/tests/test_sources.py` (fixtures) | Kept+tested; live endpoints Live only |
| BS2 | No platform enabled → no process, says so | `ui/panels/bug_spray_feed.py:498-500,517-520` | `test_bug_spray_feed.py` (2), contract | Kept+tested |
| BS3 | Scan scheduling, one at a time, missing venv message | `bug_spray_feed.py:224-226,365-372,495-531` | `test_bug_spray_feed.py`, contract (3) | Kept+tested |
| BS4 | Polite client: ≥0.35 s spacing, User-Agent, bounded retries | `_http.py:17-20,49-81` | only `test_retry_after_clamps_negative_to_zero` | Kept, no test |
| BS5 | One failing platform isolated; <50% listing marks nothing Gone | `sources/__init__.py:43-55,76-80`; `cli.py:34-36,175-177,214` | bug_spray `test_sources.py`, `test_scan.py` | Kept+tested |
| BS6 | Programs store: snapshots on change, Gone/Back, lock, survives restart | `store.py:72-74,107,132`; `cli.py:226-233` | bug_spray `test_scan.py` (5), contract | Kept+tested |
| BS7 | Watchlist saves platforms/filters | `bug_spray_feed.py:166-188,471-474` | `test_bug_spray_feed.py` (3) | Kept+tested |
| BS8 | Saved scope is "a lead, not authorisation"; https only; fills Program only | `bug_spray_feed.py:446-479`; `bug_bounty.py:242` | contract (3), `test_bug_spray_layout.py` | Kept+tested |
| BS9 | "Scope declared, not verified" disclosure | `bug_bounty.py:136-140,169-188` | `test_bug_spray_layout.py`, contract (2) | Kept+tested as disclosure; a real check is Missing (M5) |
| BS10 | Only nmap, no shell, binary from PATH/Homebrew | `bug_bounty.py:469-490` | `test_ui_panels.py` (2), contract (2) | Kept+tested (all options pass, M4) |
| BS11 | Empty command builds `nmap -sV -sC -T4 --open <host>` "with no review step" (`bug_bounty.md:47`) | `bug_bounty.py:410-419` | contract | **Narrower**: only http(s) schemes stripped; there *is* a review step (doc wrong, M6) |
| BS12 | Program required; default-No dialog names target, program, command | `bug_bounty.py:427-451` | `TestNmapAuthorisation` (3) | **Narrower** (M4: "Target" is the field, not the scan) |
| BS13 | Every attempt logged refused/declined/started, redacted | `bug_bounty.py:76-113,433,447,452` | contract (2), `test_tunnel_keeps_no_logs.py` (2) | **Narrower** (L1; doc also says it shares Tunnel's audit facility — Tunnel keeps none) |
| BS14 | Nmap killed at 10 min / 256 KB; Kill; quit kills | `bug_bounty.py:68-69,497-564` | contract (6) | Kept+tested |
| BS15 | Only scanner text reaches Analyse | `bug_bounty.py:492-495,574` | contract, `test_ui_panels.py` | Kept+tested |
| BS16 | Report drafted by the selected model, whole request priced | `bug_bounty.py:567-616,634`; `ui/panels/base.py:583-584` | contract (4), `test_ui_panels.py` | Kept+tested with fakes; real call Live only |
| BS17 | Severity/CVSS/Bounty tiles | `bug_bounty.py:45-65,664-738` | `test_bug_spray_feed.py` (3), `test_ui_panels.py` | Kept+tested |
| BS18 | Stop ignores late reply; Save raw; never submits | `bug_bounty.py:618-661,740-771` | contract (4), `test_ui_panels.py` | Kept+tested |
| BS19 | Tokens only in the macOS Keychain | `agents/bug_spray/bug_spray/secrets.py:11-48` (unused by adapters) | only `backend_name` | Kept, no test |
| BS20 | Radar only from a source checkout | `bug_spray_feed.py:24-25`; not in `Sentinel.spec` | none | Live only |
| FO1 | Analyze goes through the advisor and guard; empty/second run refused (`docs/agents/manager.md:16`) | `ui/panels/manager.py:136-173` | `test_ui_panels.py` (3), contract, `test_routing_panels.py` | Kept+tested |
| FO2 | JSON spec parsed; fences stripped | `agents/manager_agent/__init__.py:5-62` | contract (3), `test_agents_scenarios.py` | Kept+tested |
| FO3 | Plain-text review cards; "Could not structure" | `manager.py:182-198,236-261` | `test_ui_panels.py` (2) | Kept+tested |
| FO4 | Refused spec: "Cannot be created as written", Approve off | `manager.py:199-214` | contract (3), `test_ui_panels.py` | Kept+tested |
| FO5 | `validate_spec`: keys, names, providers, tools, collisions | `services/agent_factory.py:49-135` | `test_agent_factory_validation.py`, `test_agent_factory_atomicity.py`, `test_agent_roster.py`, contract | Kept+tested |
| FO6 | Length limits and no duplicates | `agent_factory.py:65-76,86-93` | none | Kept, no test |
| FO7 | Approve confirmation defaults to No; creates once | `manager.py:275-298` | `test_safety_matrix_regressions.py`, contract (2) | Kept+tested (manager.md:86 stale) |
| FO8 | Scaffold: one class with `__init__` and `build_messages`, no imports/I/O | `agent_factory.py:25-37,153-174` | contract runs the module body only | **Narrower** in tests (M14) |
| FO9 | Spec values are string literals | `agent_factory.py:19-37,201-206` | `test_agent_factory_validation.py` (2), contract | Kept+tested |
| FO10 | Compiled before save | `agent_factory.py:207-216` | `test_create_agent_refuses_a_scaffold_that_would_not_compile` | Kept+tested |
| FO11 | Disabled registry rows with the reviewed limits | `agent_factory.py:224-255` | contract, `test_agent_factory_atomicity.py` (2) | Kept+tested (recommended provider unchecked) |
| FO12 | All or nothing; exclusive hard link | `agent_factory.py:159-188`; `manager.py:322-325` | `test_agent_factory_atomicity.py` (4), contract | Kept+tested (exFAT Live only) |
| FO13 | Generated agent never loaded or shown | no loader exists | `test_agent_roster.py`, `test_ui_panels.py` | Kept+tested indirectly |
| FO14 | Reject/Clear | `manager.py:327-342` | contract (2), `test_ui_panels.py` (2) | Kept+tested |
| FO15 | Stop/cancel/error leave nothing to approve | `manager.py:155-157,216-223,263-272` | `test_ui_panels.py`, contract (4) | Kept+tested |
| FO16 | One primary button (2c05b24) | `manager.py:97-100,344-352` | `test_light_layouts.py::test_forge_has_exactly_one_primary_through_the_spec_lifecycle` | Kept+tested |
| FO17 | Analyze saved to Saved Chats even when rejected | `manager.py:183` | none | Kept, no test |
| FO18 | Disclosed limits (Clear doesn't cancel; wrong-typed fields) | `manager.py:335-342,237-238` | none | Kept, no test (as disclosed) |

### 4.6 Tunnel

| # | Promise (source) | Code | Test | Status |
|---|---|---|---|---|
| TU1 | Connect/Disconnect: review → default-No → macOS dialog; one action at a time (`docs/agents/vpn.md:24-25`) | `ui/panels/vpn.py:764-861`; `services/vpn_execution.py:1893-1941` | `test_ui_panels.py`, `test_contract_tunnel_chat.py` (2) | Kept+tested |
| TU2 | WG runs a hash-checked root copy; changed file → nothing runs | `services/vpn_connection.py:113-192`; `services/vpn_rootscript.py:69-111` | `test_vpn_rootscript.py` (2, fake wg-quick, not root) | Live only |
| TU3 | WG Disconnect uses the kept copy; template not blocked | `vpn_connection.py:195-208` | `test_vpn_rootscript.py`, `test_vpn_execution.py` | Live only |
| TU4 | OpenVPN copies `.ovpn` and siblings as root; pid/device in root's folder | `services/openvpn_manager.py:92-94,259`; `vpn_rootscript.py:215-217` | `test_vpn_connection.py` (2) | Live only (TODO S8 checklist) |
| TU5 | OpenVPN Disconnect stops only Sentinel's process | `openvpn_manager.py:346` | `test_vpn_connection.py` (2) | Kept+tested |
| TU6 | Blockers run nothing; Disconnect warns | `vpn_execution.py:1494-1763`; `vpn.py:820-828` | `test_vpn_execution.py` (4) | Kept+tested |
| TU7 | Root-run config lines listed; second default-No | `vpn.py:829-850`; `vpn_execution.py:1566-1586` | `test_ui_panels.py` (2), `test_vpn_review_gate.py` (239) | Kept+tested (README.md:233 stale) |
| TU8 | Post-change check Verified / Not verified / Unknown | `vpn_execution.py:1768-1870` | `test_vpn_execution.py` (3) | Live only (run-record layout unconfirmed on a Mac) |
| TU9 | Always the dialog, never cached sudo; refuses as root | `vpn_connection.py:48`; `openvpn_manager.py:52`; `agents/vpn_agent/services/privileged.py:24,133-163`; `killswitch.py:957` | `test_privileged_runner.py` (3) | Kept+tested |
| TU10 | No user cancel of Connect; quit/Reset drop the result | `ui/workers.py:615-653`; `vpn.py:1192-1217` | `test_vpn_worker_shutdown.py`, contract | Kept+tested |
| TU11 | Import `.ovpn`/WG, remember the path | `vpn.py:745-762`; `vpn_connection.py:401-464` | `test_vpn_killswitch.py`, contract | Kept+tested |
| TU12 | Template profiles refused | `vpn_connection.py:56-74,168-169,467-494` | `test_vpn_connection.py`, `test_ui_panels.py` | Kept+tested |
| TU13 | Private keys discarded; readable config warned | `config_inspection.py:4,16` | `test_vpn_execution.py` (2), contract | Kept+tested |
| TU14 | Tool output scrubbed of keys | `vpn_execution.py:209,1937-1939`; `ui/panels/vpn_gate.py:123` | `test_vpn_execution.py` (2) | Kept+tested (kill-switch messages skip `redact_secrets`, `ui/workers.py:640-646`; they hold no secrets) |
| TU15 | No logs of attempts or results; Advisor leaves no chat/run row | `vpn_execution.py:1869`; `main.py:157,4630,4694` | `test_tunnel_keeps_no_logs.py` (2), `test_vpn_privacy_tab.py` | Kept+tested |
| TU16 | Arm: default-No, recovery command, refusals | `vpn.py:904-942`; `vpn_connection.py:276-303`; `killswitch.py:657-713` | `test_ui_panels.py`, `test_vpn_killswitch.py`, `test_killswitch_inputs.py` | Live only |
| TU17 | "…a dropped tunnel cannot leak" (tooltip `vpn.py:155-159`; README.md:259-265; training/tunnel.md:189-194) | `killswitch.py:58,299,363-369` | none checks DNS | **Narrower** (H1) |
| TU18 | Kill switch exempts the stored OpenVPN device | `vpn_connection.py:294-302`; `killswitch.py:690-695` | `test_vpn_killswitch.py` (2) | Live only (tooltip and vpn.md:80 still say WG only) |
| TU19 | Arm refused while Mullvad/Proton connected | `services/vpn_apps.py:168-193`; `vpn.py:919-924` | `test_vpn_apps.py` (2), `test_ui_panels.py` | Kept+tested |
| TU20 | Disarm/Verify; label is a record | `killswitch.py:769-876`; `vpn.py:944-1014` | `test_killswitch_disarm.py` (9), `_verify.py` (22), `_status.py` (29) | Live only |
| TU21 | Recovery command; first Arm edits `/etc/pf.conf` with backup | `killswitch.py:168-176,714-717,921-944` | `test_vpn_killswitch.py`, `test_killswitch_stdin.py` | Live only |
| TU22 | DNS leak test via bash.ws | `agents/vpn_agent/services/dns_check.py:18,115` | `test_dns_check.py` (2), contract (8) | Live only |
| TU23 | IPv6/WebRTC leak check | none (Advisor text only; IP readouts IPv4 only, `public_ip.py:25`) | none | **Missing** as a check (not promised as a feature; testers should not expect one) |
| TU24 | Local IP contacts nothing, off the UI thread | `vpn.py:221,652-668`; `public_ip.py:109-153` | `test_public_ip.py`, `test_vpn_ip_readout.py` | Kept+tested |
| TU25 | Check public IP via IPinfo or ipapi.co | `public_ip.py:25-105`; `vpn.py:670-699,893-896` | `test_public_ip.py` (2) | Live only (api.ipify.org contacted first, not in vpn.md:186; auto-recheck untested) |
| TU26 | Connection Check read-only; external checks default-No | `vpn.py:1022-1061`; `services/vpn_diagnostics.py:541` | contract (2) | Kept+tested |
| TU27 | Action Preview / Build Config offline | `vpn.py:1220-1246` | `test_ui_panels.py` (2) | Kept+tested |
| TU28 | **Health monitor: "TUNNEL DROPPED" within 30 s** (README.md:293-298; training/tunnel.md:436-441) | `vpn.py:1132-1139` watches `profile["interface"]`; `vpn.py:1122-1130` + `agents/vpn_agent/services/wireguard_manager.py:93-107` report `/var/run/wireguard/*.sock` names, which on macOS are `utunN` (`vpn_execution.py:1248-1258`); `health_monitor.py:177-199` compares the two | contract `test_a_dropped_tunnel_warns_and_logs_nothing` injects `"wgtest"` on both sides | **Narrower — H7 (new)**: a WireGuard drop never matches, so no warning. OpenVPN and DNS-change warnings work. Read, not run on hardware |
| TU29 | Tor: local client 127.0.0.1:9250, asks first, log deleted | `agents/vpn_agent/services/tor.py:44,91-212`; `ui/panels/vpn_privacy.py:483-492` | `test_vpn_privacy_tab.py`, `test_vpn_companion_libs.py`, `test_tunnel_keeps_no_logs.py` | Live only |
| TU30 | Tor Stop own process only; Check asks first; New identity | `tor.py:215-368`; `vpn_privacy.py:494-504` | `test_vpn_companion_libs.py`, `test_vpn_privacy_tab.py` (stubs) | Live only (`check`/`new_identity` untested) |
| TU31 | Proxy chain: 0600, passwords hidden, Test chain asks | `proxychain.py:55,261-265,340`; `vpn_privacy.py:556-637` | `test_vpn_privacy_tab.py` (2), `test_vpn_socks_chain.py` | Kept+tested |
| TU32 | MAC: locally administered unicast, validated, reviewed | `macaddr.py:199-215,533-591`; `vpn_privacy.py:450-460` | `test_macaddr.py` (2), `test_vpn_privacy_tab.py` | Live only |
| TU33 | MAC original recorded first; refused if unreadable | `macaddr.py:280-295,387-413,542-583` | `test_macaddr.py` (3) | Kept+tested (L2) |
| TU34 | MAC restored at quit / next launch / after crash | `main.py:6128-6150`; `macaddr.py:433-505`; `vpn_privacy.py:104-108,397-448` | `test_macaddr.py` (3), `test_vpn_privacy_tab.py` | Live only |
| TU35 | Servers: Preview deploy redacted, runs nothing | `ui/panels/vpn_servers.py:642-652`; `server/deploy.py:231` | `test_vpn_servers_tab.py` | Kept+tested |
| TU36 | Deploy needs recorded host key, asks first, key-only SSH | `vpn_servers.py:618-684`; `deploy.py:36-37,111-184,354-358` | `test_vpn_servers_tab.py` (2), `test_vpn_companion_libs.py` | Live only (L3) |
| TU37 | Teardown needs typed name; status read-only; Use for Connect 0600 | `vpn_servers.py:686-749`; `deploy.py:519,621` | `test_vpn_servers_tab.py` (3) | Live only |
| TU38 | Keys local 0600; AES-GCM/scrypt backup; keys never shown | `vpn_servers.py:396-570`; `server/backup.py:82-213` | `test_vpn_servers_tab.py` (4) | Kept+tested |
| TU39 | ProtonVPN: state only | `vpn_apps.py:118-154`; `ui/panels/vpn_apps_tab.py:100-125` | `test_vpn_app_control.py` (2) | Kept+tested (README still says start/stop, M1) |
| TU40 | Mullvad connect/disconnect (§6) | `services/vpn_app_control.py`; `vpn_apps_tab.py` | `test_vpn_app_control.py`, `test_vpn_apps.py` (44 passed, fake output) | Live only |

Tunnel notes: two more commands run on the UI thread (the VPN Apps refresh, up to ~12 s worst case, and
`mullvad account get` at the Connect click; the kill-switch conflict check at `vpn.py:919`). Add to L8.

### 4.7 Beacon — not rechecked

The automated reviewer for Beacon was stopped by the model provider's safeguards before it returned anything,
and this session did not repeat that review by hand. Status carried forward, unverified at `22b965d`:
2026-10-09 `docs/qa_audit/beacon.md` (50 promises: 33 kept, 4 narrower, 9 stub/missing, 4 not
code-verifiable), 2026-11-18 rows L6 (docs lag), L7 and L8 (still open then). The agent-layouts commit
`019d489` changed Beacon's layout since. A Beacon recheck is an owner-routed item (TODO).

## 5. Docs that must match the code before testing (expected results)

Promise more than the code does:
- Kill switch "cannot leak" (tooltip `ui/panels/vpn.py:155-159`, README.md:259-265, training/tunnel.md:189-194) — H1, wording depends on G4.
- README.md:20, 302-303, 310-311 — ProtonVPN start/stop (M1). README.md:190-191 and vpn.md:66 — "never connects either app" (it controls Mullvad).
- `ui/tooltips.py:25` and training — Local only covers every request (M9: Trace/Bloodhound live checks run in any mode).
- README.md:76-80, `docs/training/controls_settings.md:40-47`, `ui/tooltips.py:25` vs README.md:103 — default mode (H4).
- `docs/agents/bug_bounty.md:51`, `docs/training/bug_spray.md:93-94` — "every attempt is written" (not invalid commands, not a failed write; no shared Tunnel audit).
- "Your IP & DNS" group no longer exists: README.md:200, `ui/panels/vpn_apps_tab.py:157`, `services/vpn_app_control.py:127`, TODO.md:74. It is the status card's **Public IP → Check public IP**.

Describe the code as weaker or older than it is:
- Trace private IPs "not filtered" (`docs/agents/osint.md:37`, training/trace.md:86): refused now.
- Trace cap "checked on the target alone" (osint.md:14): full prompt re-checked.
- Trace Stop tooltip "partial text discarded" (`ui/tooltips.py:110`): kept.
- Bloodhound IP consent mismatch (osint_heavy.md:33), streamed Stop "ends as an error" (:37), Sources gauge "previous run" (:90), BreachDirectory "unfiltered" (:91, README.md:395-396): all fixed.
- Nmap "no review step" (bug_bounty.md:47, training/bug_spray.md:96-99, README.md:327): a default-No dialog shows the command (M6).
- Forge "no default button, Enter may confirm" (manager.md:86): defaults to No.
- Sentry: `sentry.md:65-69,179-181` (Install verified by `launchctl print`), `:185-189` (packaged build refuses Install), `:260` and README.md:150-151 (root pytest collects `agents/sentry/tests`; no scoped conftest).
- Tunnel: vpn.md:11,228 (Privacy tab has Tor/chain/MAC controls), :80 and the Arm tooltip (OpenVPN handled), :86-92 (state is in `<data>/vpn`), :231 (Servers tab makes keys), :63,89 and training/tunnel.md:159-160 (M2 paths), training/tunnel.md:138-140 (OpenVPN copy protection exists), README.md:233 (hooks listed on Disconnect).

Tests that lock in the weaker behaviour and will need changing with the fix: H2 (`test_vpn_review_gate.py:1222,1459`),
H4 (`test_routing_advisor.py:116-117`, `test_routing_phase4.py:228`), H6 (contract `:706`), M10
(`test_failed_collection_still_sends_an_empty_live_list_to_the_model`), L2 (`test_macaddr.py:402,1024`).

## 6. Manual test step: Tunnel › VPN Apps against a real Mullvad app (owner-run)

In scope by the owner's decision of 2026-11-05; a Tunnel testing-phase item (TODO.md:74). Only stubs cover it
today, and the `relay list` / `account get` output formats are assumptions. Sentinel runs only these commands
(argv, no shell; `mullvad` from PATH or `/Applications/Mullvad VPN.app/Contents/Resources/mullvad`):
`status` (2 s), `account get` (3 s), `relay list` (5 s, first refresh only), `relay set location <cc> [<city>]`
(5 s, only when a place is picked), `connect` (10 s, then waits ≤15 s for Connected), `disconnect` (10 s, then
waits ≤5 s for Disconnected), plus `scutil --nc list` for the Proton card. It never logs in, never touches
lockdown mode, and keeps no record.

Before you start: Tunnel's kill switch **disarmed**, no other Sentinel tunnel up, note your home public IP.
Record each result as Pass / Fail / Note in `docs/qa_audit/` (and the raw output formats asked for in MV2).

| Step | Do | Expect (from the code) |
|---|---|---|
| MV0 | Mullvad app not installed (or CLI missing): open Tunnel › VPN apps | State line says not installed/unreadable; "Install the Mullvad app from mullvad.net…"; Connect and Disconnect greyed |
| MV1 | App installed, **logged out**. Open the tab, press **Connect…** | Account line says not logged in; a warning refuses **before** any question; `mullvad status` still Disconnected |
| MV2 | Log in **in the Mullvad app**. Press **Refresh** | Account line "Logged in; account time runs until …"; no account number anywhere on screen. Location list shows countries and "Country › City". In Terminal save `mullvad account get` (blank out the number) and `mullvad relay list \| head -30` so the test fixtures can be corrected |
| MV3 | Location "Mullvad's current location". **Connect…** → **No** | Question defaults to No, warns VPNs stack and protection is not verified; No changes nothing |
| MV4 | **Connect…** → **Yes** | Result "Connected" within ~15 s; `mullvad status` agrees. Status card **Public IP → Check public IP** shows a Mullvad exit (compare with `https://am.i.mullvad.net` in a browser). Tunnel has no Mullvad-specific check (TODO.md:222) |
| MV5 | While connected: Tunnel › **Arm** kill switch; then open a WireGuard profile's Connect review (do not confirm) | Arm refused before any dialog; the review warns VPNs will stack |
| MV6 | **Disconnect…** → **Yes** | Default-No question says traffic goes out in the clear unless lockdown mode is on; result "Disconnected" within ~5 s; Check public IP shows your home IP |
| MV7 | Pick a city (e.g. Sweden › Gothenburg), **Connect…** → Yes | `mullvad relay get` shows that place; the reported relay and Check public IP location match the city |
| MV8 | While connected to MV7's city, pick a different city, **Connect…** → Yes | Untested path: the wait stops at the first Connected read, which may still name the old relay. Note which relay is reported and which is used |
| MV9 | Press **Connect…** twice quickly | Second action says Busy |
| MV10 | Turn on lockdown mode in the Mullvad app, connect, then **Disconnect…** from Tunnel | Untested: if `mullvad status` reads Blocked, Tunnel reports a failure ("still reports Blocked"). Record what happens; turn lockdown off afterwards |
| MV11 | If available: an expired account | Code treats it as logged in; Connect would end as "Blocked" failure. Note the message |

Pass for the testing phase: MV0-MV7 and MV9 behave as listed; MV8, MV10, MV11 are recorded, not pass/fail.

## 7. Checks only the owner can run

Updated from 2026-11-18 §7. Run after G1-G3 are fixed and G4 is decided; none on a network that matters.

- **O1** Launch with every allow box unticked in Local only under Little Snitch or `nettop`: no cloud model API (B1); note any huggingface.co / raw.githubusercontent.com (H4, M9).
- **O2** Tunnel items under "Before testing" (TODO.md:69-75), with M2's root paths.
- **O3** Kill switch DNS: armed, drop a WireGuard tunnel, `dig example.com` and **Run test**; repeat with OpenVPN and an IPv4-only config plus `curl -6` (H1, H2, M3).
- **O4** Privileged-layer checklists S2/S3/S7 and S8 (TODO.md:70-72).
- **O5** Mullvad: §6.
- **O6** Throwaway VPS deploy, Tor, MAC changer on hardware; private Wi-Fi address question (TODO.md:76,78).
- **O7** The two skipped root-runner tests once, as root, in a throwaway VM (L3).
- **O8** Routing advisor with a real LM Studio model on every agent (TODO.md:68, P0) and the Local-only LM Studio fallback (TODO.md:89).
- **O9** Sentry background watch from the checkout (`launchctl print gui/$UID/com.sentinel.sentry.watch`, `watch.log`).
- **O10** Bloodhound: one JPEG and one HEIC on Pillow 13 (TODO.md:79).
- **O11 (new)** After the H7 fix: Connect a WireGuard profile, stop the server or pull its route, expect "TUNNEL DROPPED" within ~30 s while the Tunnel panel is visible.
- **O12 (new)** Beacon recheck (§4.7), by whichever route the owner chooses.

## 8. Test run

- Main suite in this worktree at `22b965d` (`QT_QPA_PLATFORM=offscreen`, main checkout's `.venv`):
  **1 failed, 4334 passed, 14 skipped, 1 deselected** in 288 s. Failed: G1. Skipped: 11 Imprint drift
  tests and 1 Imprint regression (Imprint is not beside a worktree, so the copied-code drift check did not run
  here), 2 root-runner tests (L3).
- Same test file in the main checkout at the same commit: 17 passed (G1 leans on local state).
- CI `tests` workflow on `main`: **failure** on `22b965d` and `2c05b24` (macOS 1 failed / 4333 passed;
  Ubuntu 1 failed / 4207 passed), same test.
- Reviewers' focused runs: `agents/sentry/tests` 25 passed; Sentry-related parent tests 133 passed;
  `tests/test_vpn_app_control.py` + `tests/test_vpn_apps.py` 44 passed.
- Bug Spray's own suite (`agents/bug_spray/tests`, own venv): **not run** this session.
- No test reached the network, root, pf or a real tunnel. VPN, kill switch, DNS, Mullvad, LM Studio, SFTP and
  public-source paths have no live coverage (§7).

## 9. Go / no-go for the owner

**Must be fixed before testing starts (no-go until done):**
1. G1 — make `test_shown_tree_viewport_is_whole_rows_and_fills_the_tab` independent of local data; CI green.
2. G2 — H3: refuse a cloud provider under Local only in `authorize_request`; block in `advise` when the advisor raises under Local only. Add the missing test.
3. G3 — H7: make the health monitor compare like with like (map the profile name to its `utunN` via wg-quick's `<name>.name` record, or watch the device); add a test using the real socket layout.
4. G4 — owner decisions: H1/H2 (kill-switch DNS behaviour and the "system DNS stays in use" warning) and H4 (is the default Local only?). Then implement or reword.
5. G5 — correct the §5 texts after G4, so every tester's expected result is the code's.

**Owner-run, part of the test plan (not blockers to start, blockers to sign Tunnel off):** O2-O7, O11 and
§6 Mullvad. O8 (LM Studio routing) is already a P0 owner item.

**Can be tested as is, with these known gaps written into the test plan:** Chat (CH9 Ollama on UI thread),
Trace (M9), Bloodhound (M10, M11, L9), Sentry (H5 false NOTICEs for connected UDP, H6, M12, M13), Bug Spray
(M4, M5, L1), Forge (M14).

**Not assessed this session:** Beacon (§4.7) and Bug Spray's own suite.

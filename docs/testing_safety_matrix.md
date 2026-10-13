# Safety coverage matrix

Built for the TODO.md P0 item "Testing roadmap — safety gate" (still-open
part). For each of the 8 agents, the six boundaries the task names:

1. **Consent** — a default-No dialog; declining makes zero external/model
   calls.
2. **Scope/target validation** — bad input is refused before anything runs.
3. **Secret handling** — API keys/credentials never reach logs, prompts,
   saved chats or audit files.
4. **Budget/cap** — a request over a cap is blocked before it is sent.
5. **Cancellation** — Stop leaves no late result and closes the run row.
6. **Write/delete** — a destructive action confirms, is atomic, and writes
   nothing when declined.

A cell names the test(s) that pin it today. `GAP (closed)` marks a hole this
pass closed with a new regression test (`tests/test_safety_matrix_regressions.py`,
unless noted). `GAP (open)` is a real hole nothing exercises yet.  `BUG` is a
defect found while building this matrix, left unfixed per the task's
"small and clear fixes only" rule, with `file:line` and a one-line
description. `N/A` means the boundary does not apply to that agent, with the
reason.

Existing test names are not proof every behaviour in the row is covered end
to end — see `docs/testing_roadmap.md` for the fuller per-agent narrative.
This matrix is the boundary-coverage index the roadmap pointed at.

## Chat

| Boundary | Status |
|---|---|
| Consent | `tests/test_chat_lifecycle.py::test_an_unticked_cloud_provider_blocks_before_a_worker_or_a_run`; `tests/test_request_guard.py::TestAuthorizeRequest::test_declining_the_confirmation_blocks_the_request` |
| Scope/target validation | `tests/test_contract_tunnel_chat.py::TestChatTools::test_invalid_a_tool_name_not_in_the_registry_is_blocked`, `test_denied_a_tool_the_registry_forbids_starts_no_worker_run_file_or_bill` |
| Secret handling | `tests/test_chat_lifecycle.py::test_secret_canaries_stay_out_of_requests_logs_chats_and_the_screen` (9 provider/OSINT keys set to a canary; checked against the worker's own messages, the screen, a full SQLite `iterdump`, and every file under the isolated test root) |
| Budget/cap | `tests/test_cost_and_limits.py::test_request_over_session_budget_is_refused`, `test_request_over_daily_budget_is_refused`, `test_request_over_the_per_agent_cap_is_refused`; `tests/test_contract_tunnel_chat.py::TestChatTools::test_denied_a_per_tool_budget_cap_blocks_a_paid_request_before_the_prompt` |
| Cancellation | `tests/test_chat_lifecycle.py::test_stop_cancels_keeps_partial_text_and_bills_nothing`, `::test_closing_the_window_cancels_the_run_and_the_worker`; `tests/test_contract_tunnel_chat.py::TestChatReviewRegressions::test_a_signal_queued_before_stop_is_ignored_after_it` |
| Write/delete | `tests/test_contract_tunnel_chat.py::TestChatDelete` (positive/invalid/denied/error/persist), `TestChatRename` (same five); **GAP (closed)** — `delete_selected_chat`'s confirmation had no explicit default button (Qt leaves Enter bound to nothing rather than refusing); fixed in `main.py` and pinned by `tests/test_safety_matrix_regressions.py::TestDestructiveConfirmationsDefaultToNo::test_delete_saved_chat_confirmation_defaults_to_no` |

## Trace (osint)

| Boundary | Status |
|---|---|
| Consent | `tests/test_ui_panels.py::TestDeclinedConsentContactsNothing` (Live Research, email picker cancelled, exposure picker cancelled, every source unticked); `tests/test_contract_trace.py::TestLiveResearch::test_declined_consent_leaves_nothing_behind_and_a_later_yes_still_works`; `TestCourtListenerConsent::test_unticked_is_neither_listed_nor_contacted` |
| Scope/target validation | `tests/test_ui_panels.py::TestTracePanel::test_an_invalid_typed_target_never_reaches_the_guard`; `tests/test_domain_lookup.py`, `tests/test_identity_lookup.py` (per-type `validate_target`); **BUG** `providers/domain_lookup.py` (IP branch) — DNS lookups ask only forward record types of the address string and nothing filters a private/loopback/link-local target before the provider call; the panel's own `non_public_ip_reason` gate (`ui/panels/osint.py::live_research`) catches the common cases first, so this is a defence-in-depth gap, not a live hole (`docs/qa_audit/code_followups.md` #15) |
| Secret handling | `tests/test_key_check.py::test_the_key_never_appears_in_a_url_or_a_message`, `tests/test_intel_sources.py::test_shodan_host_and_key_never_in_error_text` pin the provider boundary; **GAP (closed)** — nothing exercised the panel's own surface (activity trail, section cards, the saved-search file `record_external_research` writes) end to end; closed by `tests/test_safety_matrix_regressions.py::test_an_osint_source_key_never_reaches_the_activity_log_or_the_saved_search` |
| Budget/cap | **GAP (closed)** — `analyse()` priced only the bare target string via `authorize()` and never re-checked the caps against the assembled prompt (which carries the OSINT-catalogue text), unlike Bloodhound and Bug Spray. Fixed in `ui/panels/osint.py::analyse()` (added a `reauthorize(messages)` call before `start_worker`); pinned by `tests/test_safety_matrix_regressions.py::TestTraceStructureQueryBudgetRecheck` (both the recheck-sees-the-real-prompt case and the blocked-before-the-worker-starts case) |
| Cancellation | `tests/test_contract_trace.py::TestStop` (during a live lookup, nothing running, a lookup that raises after stop); `TestLiveResearch::test_stop_reaches_the_provider_as_should_stop_and_the_partial_is_saved_flagged`; **BUG** `ui/panels/osint.py` / `ui/workers.py` — a Structure Query Stop still renders as the red "Request cancelled by user." text path before the panel's own `_stopped` check intercepts it for billing purposes (status/billing are correct; the raw error string briefly reaches `_on_error` — `docs/qa_audit/code_followups.md` #12, already accounted for by `test_a_stopped_plan_keeps_its_partial_text_and_is_not_an_error`) |
| Write/delete | `tests/test_ui_panels.py::TestTracePanel::test_saved_trace_search_can_be_filtered_and_reopened`; **GAP (closed)** — same missing-default-button class as Chat's delete, in `delete_selected_search`; fixed in `main.py`, pinned by `tests/test_safety_matrix_regressions.py::TestDestructiveConfirmationsDefaultToNo::test_delete_saved_search_confirmation_defaults_to_no` |

## Bloodhound (osint_heavy)

| Boundary | Status |
|---|---|
| Consent | `tests/test_contract_bloodhound.py::TestInvestigateContract::test_denied_consent_names_sources_and_contacts_nothing`, `test_denied_authorisation_asks_no_question_and_contacts_nothing`; `TestImageExifContract::test_denied_consent_gps_never_reaches_the_assembled_prompt`, `test_denied_authorisation_with_opt_in_sends_no_metadata_anywhere` |
| Scope/target validation | `TestInvestigateContract::test_invalid_blank_targets_are_refused_before_anything_runs`, `test_invalid_target_is_trimmed_before_it_is_authorised_and_collected`; `TestRemoteFileSearchContract::test_invalid_ssh_details_are_refused_before_any_connection` (host/user/port), `services/remote_file_search.py::validate_ssh_target` + the panel's own "every remote folder must be an absolute path" check |
| Secret handling | `TestImageExifContract::test_denied_consent_gps_never_reaches_the_assembled_prompt` (location, not a credential); `TestRemoteFileSearchContract::test_denied_an_unknown_host_key_surfaces_as_an_error_and_no_password_prompt` — SSH uses agent/known_hosts, no password is ever held; **BUG** (known, not fixed) `services/remote_file_search.py` passes no content back, only metadata, so there is no file-content leak path to test; `docs/qa_audit/code_followups.md` #34 notes the SSH config User/Port override and missing timeout, which is a robustness gap, not a secret-handling one |
| Budget/cap | `tests/test_ui_panels.py::TestBloodhoundPanel::test_the_assembled_prompt_is_budget_checked_before_the_model_call`, `test_the_guard_runs_before_any_public_source_is_contacted` |
| Cancellation | `tests/test_contract_bloodhound.py::TestStopContract` (model call, mid-stream, idle, twice, with consent pending, together with a file search, a stopped model request closed with the guard) |
| Write/delete | `tests/test_contract_bloodhound.py::TestInvestigateContract::test_persist_a_cancelled_save_dialog_writes_nothing`, `test_persist_saving_with_no_dossier_never_opens_a_dialog`, `test_persist_a_new_investigation_clears_the_previous_dossier_before_saving` — the dossier save dialog already passes an explicit default (`QFileDialog`, not a `QMessageBox`, so the Qt default-button gap does not apply here) |

## Beacon (wifi)

| Boundary | Status |
|---|---|
| Consent | `tests/test_contract_beacon_sentry.py::TestBeaconAiAnalysisOptIn::test_unticked_box_makes_zero_model_calls_for_every_scan_mode`, `test_denied_consent_starts_no_model_and_restores_the_controls`, `test_kali_mode_never_reaches_the_model_even_with_a_stale_tick` |
| Scope/target validation | `tests/test_contract_beacon_sentry.py::TestBeaconScan::test_whitespace_only_ping_target_is_rejected_and_nothing_runs`, `test_empty_adapter_data_shows_the_honest_error_not_invented_networks`; the Kali command builder's own MAC/channel/ESSID validators (`agents/wifi_agent/__init__.py`) are exercised by `tests/test_wifi_scan.py` |
| Secret handling | N/A — Beacon holds no API keys or credentials of its own; the only "secret" surface is the selected cloud provider's key, covered under Chat/the shared guard |
| Budget/cap | N/A direct two-stage recheck — the AI pass authorizes against the full scan-output prompt already (`_start_ai_pass`), so there is no short-estimate/long-prompt gap to re-check; the shared caps are `tests/test_cost_and_limits.py` |
| Cancellation | `tests/test_contract_beacon_sentry.py::TestBeaconStop` (during a scan, during the AI pass, idle, a scan result arriving after Stop, a run after Stop works normally) |
| Write/delete | `tests/test_contract_beacon_sentry.py::TestBeaconScan::test_save_writes_the_scan_and_a_cancelled_dialog_writes_nothing`, `test_save_with_nothing_to_save_is_a_noop` — uses `QFileDialog`, not a `QMessageBox` confirmation, so no default-button gap applies |
| Other (noted, not a named boundary) | **BUG** `agents/wifi_agent/__init__.py` — one Kali Command Builder operation is generated for a non-injecting adapter exactly as for an injecting one (only one other operation is refused for lacking injection support); `docs/qa_audit/code_followups.md` #38. Generation is local and non-executing either way (`test_the_kali_builder_is_offline`), so nothing is actually run by this gap. |

## Sentry

| Boundary | Status |
|---|---|
| Consent | `tests/test_contract_beacon_sentry.py::TestSentryAiAnalysisOptIn::test_unticked_pass_with_findings_makes_zero_model_calls`, `test_denied_consent_starts_no_model_and_keeps_the_findings_visible`, `test_ticked_with_nothing_to_report_never_asks_for_consent` |
| Scope/target validation | N/A — no user-supplied target; Sentry's collectors (`arp`, `ndp`, `route`, `lsof`) are fixed, read-only commands with no operator input to validate |
| Secret handling | N/A — no API keys or credentials; findings (LAN IPs/MACs/process names) are operational data, gated by the AI-opt-in consent boundary above, not a secret-handling boundary |
| Budget/cap | N/A two-stage recheck — `_start_ai_pass` authorizes against the full findings-based prompt already; shared caps per `tests/test_cost_and_limits.py` |
| Cancellation | `tests/test_contract_beacon_sentry.py::TestSentryStop` (a running watch pass, before snapshot persists nothing, a cancelled dry run, an existing baseline untouched, during the AI pass, idle, a result after Stop) |
| Write/delete | `tests/test_contract_beacon_sentry.py::TestSentryBaselineCreate::test_reset_removes_the_baseline_and_the_findings_log`, `test_reset_with_nothing_recorded_is_harmless`, `test_a_corrupt_baseline_file_reads_as_missing_not_as_a_crash`, `test_reset_failure_is_reported_and_leaves_the_panel_usable`; **N/A** for the "confirmation" half — `reset_baseline` (`ui/panels/sentry.py`) deliberately asks nothing (documented: `docs/agents/sentry.md:76`, "There is no confirmation"), so there is no declined-path to test. The two-file delete (baseline, then findings log) is not wrapped as a single transaction — a crash between the two unlinks could leave one deleted and not the other — **BUG**, `ui/panels/sentry.py::reset_baseline` / `docs/qa_audit/code_followups.md` #48, left unfixed: wrapping it needs a `BaselineStore.transaction()` the store does not have today, which is more than a small, clear fix. |

## Bug Spray (bug_bounty)

| Boundary | Status |
|---|---|
| Consent | `tests/test_contract_spray_forge.py::TestNmapAuthorisation::test_dialog_names_target_and_program_and_defaults_to_no`, `test_declining_starts_no_process_and_is_recorded`, `test_accepting_starts_nmap_and_records_started` |
| Scope/target validation | `tests/test_contract_spray_forge.py::TestBugSprayNmap::test_other_programs_are_refused`, `test_shell_metacharacters_are_never_interpreted`, `test_malformed_command_starts_nothing`; `TestNmapAuthorisation::test_empty_program_refuses_without_asking_or_starting`, `test_invalid_command_is_not_audited_as_started_or_asked`. **N/A** for authorisation scope itself — Sentinel does not verify bounty-program scope by design (CLAUDE.md: "Sentinel does not verify scope"); the dialog text says so and `TestBugSprayAnalyse::test_an_unlabelled_scope_claim_is_not_trusted_as_authorisation` pins that nothing treats the free-text scope field as a check |
| Secret handling | Bug Spray's companion CLI uses Keychain only and "no adapter uses a token today" per `docs/testing_roadmap.md`, so there is nothing to exercise end to end from Sentinel's side; `tests/test_contract_spray_forge.py::TestBugSprayAuditFixes::test_panel_markers_never_reach_the_model` pins that the panel's own `[Running]`/`[Done]` markers (not a secret, but the same "never-sent" shape) stay out of the model call. The audit log redacts via `services.vpn_execution.redact_secrets` (`ui/panels/bug_bounty.py::record_nmap_attempt`); the function itself is pinned by `tests/test_vpn_execution.py::test_redact_secrets_scrubs_each_shape`, but nothing sets a canary through `record_nmap_attempt` directly — **GAP (open)**, low value given there is no real secret flowing through that path today |
| Budget/cap | `tests/test_contract_spray_forge.py::TestBugSprayAuditFixes::test_authorize_sees_system_prompt_findings_and_scan_not_just_target`; `tests/test_ui_panels.py::TestBugSprayPanel::test_authorisation_prices_the_whole_request` — the whole assembled text (not just the target) is priced in one step, so there is no two-stage gap here |
| Cancellation | `tests/test_contract_spray_forge.py::TestBugSprayAuditFixes::test_late_finished_after_stop_is_ignored`, `test_cancel_echo_after_stop_keeps_stopped_status`, `test_late_token_after_stop_is_not_shown`, `test_a_new_analysis_after_stop_works_normally`; `TestBugSprayAnalyse::test_stop_then_cancel_error_leaves_a_usable_panel`, `test_stop_does_not_kill_a_running_scan` (Stop and Kill are deliberately separate — the scan is local, not billed) |
| Write/delete | `tests/test_contract_spray_forge.py::TestBugSprayAnalyse::test_saved_report_is_written_where_the_user_chose`, `test_cancelling_the_save_dialog_writes_nothing`, `test_saving_with_no_report_opens_no_dialog` — `QFileDialog`, no `QMessageBox` default-button gap |

## Tunnel (vpn)

| Boundary | Status |
|---|---|
| Consent | `tests/test_contract_tunnel_chat.py::TestConnectDisconnect::test_denied_no_means_zero_privileged_calls_and_logs_nothing`, `test_denied_root_hooks_get_their_own_prompt_and_declining_it_runs_nothing`; `TestKillSwitch::test_denied_no_arms_nothing`; `tests/test_ui_panels.py::TestTunnelPanel::test_the_confirmation_shows_the_review_and_defaults_to_no`, `test_a_config_with_root_hooks_needs_a_second_confirmation` |
| Scope/target validation | `tests/test_contract_tunnel_chat.py::TestConnectDisconnect::test_invalid_interface_name_is_blocked_before_any_prompt`, `test_invalid_connecting_an_interface_that_is_already_up_is_refused`, `test_invalid_a_missing_config_file_and_no_selection_run_nothing`; `TestKillSwitch::test_invalid_template_profile_and_missing_profile_never_reach_pf`, `test_invalid_unresolvable_endpoint_is_not_armed`; `TestConnectionCheck::test_invalid_a_hostile_interface_name_is_never_put_in_a_command` |
| Secret handling | `tests/test_vpn_execution.py::test_redact_secrets_scrubs_each_shape`, `test_redact_secrets_leaves_ordinary_output_alone`; `tests/test_vpn_privacy_tab.py::test_canary_never_reaches_the_screen_or_conf_modes`; `tests/test_vpn_servers_tab.py::test_private_keys_never_reach_the_screen`, `test_preview_shows_no_secret`; `tests/test_vpn_diagnostics.py::test_report_discards_secret_profile_fields`; `tests/test_contract_tunnel_chat.py::TestInspectConfig::test_positive_shows_routing_intent_and_discards_every_key` |
| Budget/cap | advisor `run()` authorizes against the full context-prefixed question already (`ui/panels/vpn.py::run`), so there is no two-stage gap; shared caps per `tests/test_cost_and_limits.py` |
| Cancellation | `TestConnectionCheck::test_cancel_stop_asks_the_worker_and_the_stopped_report_is_labelled`; `tests/test_ui_panels.py::TestTunnelPanel::test_stop_cancels_a_connection_check`, `test_shutdown_joins_a_connection_check`, `test_stop_restores_the_controls` |
| Write/delete | `tests/test_contract_tunnel_chat.py::TestConnectDisconnect::test_a_declined_confirmation_runs_nothing_and_writes_nothing`, `test_an_accepted_confirmation_writes_no_log` (Tunnel keeps no connection log by product decision); `TestKillSwitch::test_a_failed_disarm_must_not_forget_that_the_switch_is_still_armed` (atomicity of in-memory armed state) |

## Forge (manager)

| Boundary | Status |
|---|---|
| Consent | `tests/test_contract_spray_forge.py::TestForgeDescribeToCreate::test_declining_the_confirmation_creates_nothing_and_keeps_the_proposal`, `test_declined_then_accepted_creates_exactly_once` |
| Scope/target validation | `tests/test_contract_spray_forge.py::TestForgeDescribeToCreate::test_invalid_names_show_the_invalid_card_and_cannot_be_approved`, `test_unknown_provider_and_tool_are_invalid_before_approval`, `test_negative_budget_is_invalid`, `test_missing_fields_are_invalid_and_named`, `test_non_json_reply_gives_no_proposal_and_creates_nothing`, `test_hostile_text_in_the_spec_stays_a_literal_in_the_scaffold` |
| Secret handling | N/A — a spec's `system_prompt` is user-authored text, not a credential; nothing in the Forge path reads or forwards an API key |
| Budget/cap | `analyse_idea` authorizes against the whole idea text already; shared caps per `tests/test_cost_and_limits.py` |
| Cancellation | `tests/test_contract_spray_forge.py::TestForgeDescribeToCreate::test_stop_mid_stream_then_cancel_error_creates_nothing`, `test_stop_with_nothing_running_is_harmless` |
| Write/delete | `tests/test_contract_spray_forge.py::TestForgeDescribeToCreate::test_a_factory_failure_at_approval_rolls_everything_back`, `test_a_rejected_proposal_leaves_no_trace_after_restart`, `test_after_restart_proposing_the_same_agent_is_invalid`; **GAP (closed)** — the create-agent confirmation (`ui/panels/manager.py::approve_spec`) had no explicit default button; fixed and pinned by `tests/test_safety_matrix_regressions.py::TestDestructiveConfirmationsDefaultToNo::test_forge_create_agent_confirmation_defaults_to_no` |

## Summary

48 cells (8 agents × 6 boundaries). Primary status per cell:

- **Covered** (existing test(s) already pin the cell): 33.
- **GAP, closed this pass** (new regression test added, each verified to
  fail when the fix it pins was hand-reverted, then restored): 5 — Trace
  secret handling (panel surface), Trace budget/cap, Chat write/delete
  (delete-chat default button), Trace write/delete (delete-search default
  button), Forge write/delete (approve-agent default button). All five
  live in `tests/test_safety_matrix_regressions.py`.
- **GAP, left open**: 1 — Bug Spray's `record_nmap_attempt` redaction path
  has no canary-through test (low value: no real secret flows through it
  today).
- **N/A** (boundary does not apply to that agent, reason given inline): 8.
- **BUG, recorded as the cell's primary status, not fixed**: 1 — Sentry's
  two-file baseline reset is not transactional.

Four bugs were found in total while building this matrix; the other three
are noted inline inside an otherwise-covered or N/A cell rather than as the
cell's primary status: `providers/domain_lookup.py` IP record-type/private-
address gap (Trace scope/target validation, defence-in-depth only — the
panel's own non-public-IP check already blocks the common case), the
Structure-Query-Stop error-path cosmetic (Trace cancellation, already
covered behaviourally), and Beacon's Kali builder injection check for one
operation (local, non-executing text generation). Each cites
`docs/qa_audit/code_followups.md` by item number for the fuller writeup;
none was fixed in this pass because each is broader than a small, clear
change.

## Fixes made in this pass

1. `main.py::delete_selected_chat` — the confirmation now defaults to No
   (`QMessageBox.Yes | QMessageBox.No, QMessageBox.No`).
2. `main.py::delete_selected_search` — same fix.
3. `ui/panels/manager.py::approve_spec` — same fix.
4. `ui/panels/osint.py::analyse` — added a `reauthorize(messages)` call
   before `start_worker`, so Structure Query's budget caps are checked
   against the assembled prompt, not just the bare target string (matching
   Bloodhound's `_send_dossier_request` and Bug Spray's `analyse`).

None of these touch `agents/vpn_agent`.

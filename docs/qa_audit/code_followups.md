# Code and asset follow-ups from the Phase B documentation reconciliation

Branch `qa/phase-a-fixes`. These are behaviour changes, strings in files outside the edit list, or image
regeneration, so none was applied. Line numbers come from the per-agent proposals and drift: locate by the
quoted text. Each item is numbered once; the count at the bottom is the number of items.

## Screenshots (regenerate with `scripts/capture_training_screenshots.py`; not attempted)

1. `docs/training/images/workspace-chat.png` — shows `v2.001`, a sidebar with no Sentry row, an idle cost chip of `~€0.00 · 0 tok` (now `—`, then `free · N tok`) and the older Cost card; used by training/chat.md, workspace.md and quick_start.md.
2. `docs/training/images/trace.png` — predates the Exposure Check button and the Stop button that replaces the run bar; remove the caption at `docs/training/trace.md:7` after recapture.
3. `docs/training/images/bloodhound.png` — predates the image-metadata (EXIF) opt-in checkbox; `python scripts/capture_training_screenshots.py osint_heavy`.
4. `docs/training/images/tunnel.png` — shows `v2.001` and has no Your IP & DNS group; remove the caption in the alt text at `docs/training/tunnel.md:5` after recapture.
5. `docs/training/images/tunnel-action-preview.png` — shows `v2.001`.

## Chat

6. `main.py:2186-2267`, `ui/tooltips.py` — `command_box`, `execution_mode_box`, `allow_*_checkbox`, `recommend_setup_btn`, `auto_recommend_checkbox`, `estimate_btn`, `export_btn`, `refresh_models_btn`, `model_guide_btn` and `docs_btn` are `.hide()`n, so their seeded tooltips never show; attach the text to the matching Options-menu actions (`main.py:2292-2338`) or delete the entries (I reworded the strings only).
7. `ui/workers.py` / `main.py` `stop_current_task` — Stop is a cooperative flag plus `QThread.terminate()`, so a worker can still be ended mid-call; consider a cancel that waits for the call.

## Trace

8. `ui/panels/osint.py:90-94` — `live_btn` tooltip is wrong for username, email and company and silent on keys; replacement text is in proposals_trace.md item 10.
9. `ui/panels/osint.py:99-103` — `exposure_btn` tooltip names three of the six sources; replacement in proposals_trace.md item 11.
10. `ui/panels/osint.py:558` — Intelligence X checkbox text points at `.env`; should read "Save INTELX_API_KEY on Settings → OSINT Keys to enable Intelligence X."
11. `ui/panels/osint.py:566` — DeHashed checkbox text points at `.env`; same fix with `DEHASHED_API_KEY`.
12. `ui/panels/osint.py`, `ui/workers.py` — Structure Query Stop turns the plan into the red error "Request cancelled by user." and drops the partial text.
13. `ui/panels/osint.py` — pre-flight cost estimate and cap see only the target text; Trace was not covered by the budget re-check that Bloodhound and Bug Spray got. **Fixed 2026-10-13:** `analyse()` now calls `reauthorize(messages)` before the worker starts.
14. `providers/exposure_lookup.py` and the panel — the Exposure verdict ignores errors and cancellation; "direct victim" is a substring test of the domain's name part.
15. `providers/domain_lookup.py` — IP DNS asks forward record types of the address string (no PTR lookup) and nothing filters private, loopback or link-local addresses.
16. `ui/panels/osint.py` Auto-detect — dotted handles resolve to Domain, crypto addresses to Username, IDN domains fail validation.
17. `ui/panels/osint.py` — the Live Research and Exposure Check consent dialogs do not show the resolved query type (the Structure Query trail does).
18. `ui/panels/osint.py` — reopening a saved Live Research does not restore the query type, the trail says "stored query-planning result", and saved Activity lines are not restored.
19. `providers/email_lookup.py`, `providers/exposure_lookup.py` — `lookup()` treats an empty `selected_sources` as every default source; the panel never sends an empty list, but a caller that did would contact every service.
20. `ui/panels/osint.py` — the three action buttons have no `is_running` guard, so after Stop re-enables them a second run replaces the first worker without waiting for it.
21. `ui/panels/osint.py` — the Summary card keeps the rest of the "## SUMMARY & NEXT STEPS" line, so the card begins "& NEXT STEPS".
22. `services/osint_keys.py` — the `criminalip` and `censys` rows carry swapped hint and note text.
23. `providers/email_lookup.py:93-99` — BreachDirectory's reply is passed through unfiltered, and whether its keyless endpoint still works is unconfirmed.
24. `services/osint_catalog.py` — the people-search filter works on branch names only; the shadow-library block covers catalogue picks, not model suggestions.

## Bloodhound

25. `agents/osint_heavy_agent/__init__.py:338-346` — `planned_sources()` for an IP omits DShield and Shodan InternetDB (the lookup is IP-aware, `domain_lookup.py:505-525`); for an email it names HIBP and Hunter without a key; for crypto it names both explorers.
26. `ui/panels/osint_heavy.py:1063-1069` — `clear_image()`/`set_image()` never reset `send_exif_checkbox`, so one ticked box applies to every later image.
27. `ui/panels/osint_heavy.py:647-653`, `ui/workers.py:221-223` — Stop in the model phase still ends as "Error." with `abandon("error")`, and non-streaming replies still fire `finished_signal` after Stop.
28. `ui/panels/osint_heavy.py:575-577, 598-607` — a collection-level exception becomes `[]`, the model is not told, and `last_source_count` keeps the previous run's value.
29. `ui/panels/osint_heavy.py:37-55` — `_getexif()` exists only for JPEG/PNG/WebP, so TIFF/BMP/HEIC (all offered by the file dialog) report "No EXIF"; `gps_to_decimal()` returns 0.0 for an unreadable tag and the prompt block sends it.
30. `ui/panels/osint_heavy.py:1091-1101` — file name and EXIF values are interpolated unescaped into HTML for a `QTextBrowser` with `setOpenExternalLinks(True)`.
31. `ui/panels/osint_heavy.py` — the execution-mode box ("Local only") is not consulted; live collection runs once the consent dialog is answered Yes.
32. `main.py:3729-3755` (`recheck_budget`), `main.py:3782-3783` — the pending record's `prompt` is overwritten with the assembled system+user text and saved as the Saved Chats user message, so history now stores the full request including collected JSON and any EXIF block; confirm this is intended.
33. `main.py:3660` (`authorize_request`) — the confirmation dialog's cost figure for Bloodhound is still the target-only estimate; only the caps are re-checked on the real prompt.
34. `services/remote_file_search.py:40-52, 97-150`, `services/local_file_search.py:74,148` — config User/Port override the panel, no SFTP timeout, `SSHException`/`EOFError` and out-of-range mtimes abort the whole search and discard matches, and the client is not closed if `connect()` fails.
35. `ui/panels/osint_heavy.py:926-927` — the Size column sorts as text (`UserRole` is ignored by `QTableWidgetItem.__lt__`).
36. `providers/email_lookup.py:93-99`, `providers/whatsmyname.py:157` — BreachDirectory's `sources[:10]` is unfiltered; an empty `e_string` makes a status-code-only match a hit.

## Beacon

37. `ui/panels/wifi.py:97-99` — `interface_box` is read nowhere; wire it into the commands or remove it (the tooltip now says it is for reference only).
38. `agents/wifi_agent/__init__.py:362-381` — "Handshake Capture" is generated for an adapter with `inject: False` exactly as for an injecting one; only "Deauth Attack" is refused. Warn or refuse, then add the manual check.

## Sentry

39. `ui/panels/sentry.py:64-71` and `:42` — panel tooltip says it "changes nothing" and flags "an unexpected service"; proposed wording is in proposals_sentry.md B1 (also the module docstring).
40. `ui/learning_center.py:61` — Sentry description reads "…continuous background monitoring."; proposed "Read-only network anomaly watch with an optional background watch."
41. `agents/sentry/sentinel_chat_agent.py:12` — the system prompt says the model receives the neighbour table, gateway, listeners and connections; it receives counts and findings with evidence. Lines 30 and 39-45 are unreachable from the panel.
42. `agents/sentry/sentry/cli.py:24-47, 50-63` — headless output hides collector failures and prints "No new anomalies" when a first baseline was refused; the dry-run `scan` prints "Baseline established" lines it should not.
43. `agents/sentry/sentry/collectors.py:273` — lsof exits 1 when it lists nothing, which may record a collector failure on a Mac with no established connections (unverified on macOS).
44. `agents/sentry/sentry/collectors.py:27, 151-158, 192-212` — a connected UDP socket NAME containing `->` parses into a bogus listener; skip those rows.
45. `agents/sentry/sentry/engine.py:72-94` — nothing flags an IP whose MAC differs from the baseline's for that IP (poisoning a non-gateway host); add the check, test in an owned lab, then drop the "Known limitation" sentence from docs/agents/sentry.md, docs/training/sentry.md and agents/sentry/README.md.
46. `agents/sentry/sentry/watchd.py:24, 39-44` — in a frozen build `sys.executable` is the Sentinel GUI binary and `main.py` is not shipped; disable Install with an explanation or route to a headless flag.
47. `agents/sentry/launchd/com.sentinel.sentry.watch.plist.template` — lacks `WorkingDirectory` and `EnvironmentVariables` (`SENTRY_STATE_DIR`) that `build_plist` writes.
48. `ui/panels/sentry.py:334-347` — Reset baseline has no confirmation and takes no lock, and now also resets the background watch.
49. `ui/panels/sentry.py:361-400` — recorded findings are read only at start and after Install/Remove/Reset, not when the panel is shown.

## Bug Spray

50. `ui/panels/bug_bounty.py:157-161` — the Nmap caption still says "(the first word is the program to launch)"; suggested text in proposals_bug_spray.md code item 1. `:288-290` also starts an auto-built command in one click, against the README review rule.
51. `ui/panels/bug_bounty.py:53` — `extract_cvss_score` slices `[4:]` after the vector substitution, so `CVSS:3.1/AV:N/…/A:N 7.5` returns 5.0 and the parenthesised form returns None.
52. `ui/panels/bug_bounty.py:37` — `_CVSS_VERSION` needs a leading "v", so `CVSS 3.1: 7.5` returns 3.1.
53. `ui/panels/bug_bounty.py:117-121` — `severity_box` ("Severity Target") is built but never read; pass it to `build_messages` or remove it.
54. `ui/panels/bug_bounty.py:438-442` — the run log's `prompt_summary` is the start of the system prompt, and Saved Chats stores the whole request including the system prompt as the user turn; pass `messages=messages` to `record()`.
55. `ui/panels/bug_bounty.py:474-485` — a streamed cancel arrives as `error_signal("Request cancelled by user.")`, so "Stopped." becomes "Error."
56. `ui/panels/bug_bounty.py:288` — the auto-built host keeps a `:port`, which nmap does not accept as `host:port`.
57. `ui/panels/bug_spray_feed.py:66, 118` — docstrings mention a `bugspray` command; the entry points are `python main.py …` and `bug-spray`.
58. `ui/panels/bug_spray_feed.py:416-428, 445-459` — no "no platforms enabled" notice, the scanner's stdout (including the >50% guard note) is discarded, and a non-zero exit overwrites the per-platform error line with a pointer to a summary never shown.
59. `ui/panels/bug_spray_feed.py` `__init__` — add `setToolTip` for `scan_button`, `watchlist_button` and `use_button` (`ui/tooltips.py` resolves one dotted level, so it cannot reach them); proposed text in proposals_bug_spray.md.
60. `ui/panels/bug_bounty.py` `shutdown()` — the radar's directory-scan child process is not stopped explicitly when Sentinel quits.

## Tunnel

61. `ui/panels/vpn.py:176-178` — the "Run test" inline tooltip begins "Optional:" and lacks "Starts immediately; the verdict is a clue, not proof." (left unedited because only three inline strings were allowed).
62. `services/vpn_connection.py:235-236, 260` — "Kill switch is unavailable until the VPN module is merged into Sentinel (pending cleanup)" is stale; use "Kill switch module could not be loaded."
63. `services/vpn_connection.py:109-110, 380` — "(or provision your own server)": no provisioning is reachable from Tunnel; drop the phrase.
64. `agents/vpn_agent/services/killswitch.py:14-17` — docstring says rules live in a private anchor and never the main ruleset, but `arm()` appends an anchor block to `/etc/pf.conf` and reloads it.
65. `agents/vpn_agent/services/dns_check.py:122-130` — docstring says probes use the system resolver stack / `socket.gethostbyname`; the code uses dnspython (`_bounded_resolve`).
66. `ui/panels/vpn.py:472-473` — `VpnPanel.is_running()` ignores the connection worker, so the window-level Stop and agent-switch logic do not see an in-flight Connect.
67. `ui/panels/vpn.py:939-953` — `shutdown()` calls `terminate()` on a worker still running after 2 s, which can end the Python thread inside a privileged `subprocess.run`.
68. `ui/panels/vpn.py:795-815`, `agents/vpn_agent/services/killswitch.py:101-122` — arming for an OpenVPN profile is not refused and passes no OpenVPN tun device, so the tunnel's own traffic is blocked.
69. `services/vpn_execution.py:55` — `WG_HOOK_KEYS` omits `SaveConfig`; hooks are scanned on Connect only although `PreDown`/`PostDown` run on Disconnect (and should show on Disconnect and in Inspect config).
70. `agents/vpn_agent/sentinel_chat_agent.py:165-176` — the Build Config kill-switch sketch (`pass on utun+ all`, `pass out to <SERVER_IP> port …` without `proto`) differs from the real rules and has not been parse-checked.
71. Candidate SUGGESTIONS entries from the Tunnel audit (not added to SUGGESTIONS.md): OpenVPN-aware kill switch or refusal to arm; move imported profiles and kill-switch state under `user_data_base()` (today `~/Library/Application Support/VPN Agent/`); a live kill-switch status instead of a static label; Connection Check resolving a Sentinel-started tunnel through `/var/run/wireguard/NAME.name`.

## Forge

72. `ui/panels/manager.py` `_on_finished` — `_populate_sections` runs before `validate_spec`, so a wrong-typed field (`allowed_tools: null`, a list `system_prompt`) raises inside the slot and leaves a blank review area; wrap it and fall back to the "Could not structure the response" card.
73. `ui/panels/manager.py` — the confirmation `QMessageBox.question` still uses Qt's auto text format and sets no default button, and the Creation Log is not escaped. **Default button fixed 2026-10-13** (now No); the Creation Log escaping is not re-verified.
74. `services/agent_factory.py` `create_agent` — `os.link` fails closed on volumes without hard links (exFAT is a documented portable format).
75. `services/agent_factory.py` — `RETIRED_BUILTIN_AGENTS` lacks `router`, `course` and `narrator`, and built-in display names are not reserved; `tests/test_agent_roster.py:33,40` treat `router` and `course` as retired.

## Documentation sync (not code, outside the edit list)

76. `agents/chat_agent/README.md`, `agents/osint_agent/README.md`, `agents/osint_heavy_agent/README.md`, `agents/wifi_agent/README.md` — still copies of the pre-fix `docs/agents/*.md`; rebuild each as `agents/manager_agent/README.md` now is (first four header lines plus the docs page from line 3).
77. `docs/agents/chat.md:60,65` — says `config/tool_prompts.json` seeds the `tools` table; the JSON is migrated only into a new database and `_seed_default_tools` uses `services/tool_catalog.BUILTIN_TOOLS`.
78. `services/database.py:639-654` — `_seed_default_agents` uses `INSERT OR IGNORE`, so the new `agent_catalog.py` descriptions reach only new databases; an existing `agents` row keeps its old description text.
79. `docs/training/advanced_tools.md` — add the Sentry row and staged-candidate note (proposals_sentry.md A8); also Tunnel's "Tunnel next" paragraph (lines 43-45) now describes delivered work.
80. `docs/training/workflows.md:42-44` — Tunnel step 4 should read "Apply changes with a recovery path: Tunnel's gated Connect/Disconnect, or your normal VPN tool."; `docs/training/README.md:71-76` is fine as written.
81. `docs/honesty_audit.md` "Standalone vpn_agent: merge/delete analysis" — still says `agents/vpn_agent` is a git submodule with a committed `.venv`; it is in-tree with no `.venv`. Line 57 (Trace provider list) was left as written, as a dated audit.
82. `docs/roadmap.md` V3 item 7 and `SUGGESTIONS.md` #7 — "Evaluate Chat streaming" / "Streaming responses in the chat panel" are open items although every provider has a `stream_chat`; `SUGGESTIONS.md` Done rows for Tunnel Connection Check and previews still say "16 focused tests"; `docs/roadmap.md` "Delivered" has no Sentry bullet.
83. `TODO.md:66, 72` and `SUGGESTIONS.md:47, 59` — "seven agents" / "seven-agent roster" remain in completed, dated entries (left as history).

Total items: 83

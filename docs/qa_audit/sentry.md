# Sentry (`sentry`) requirements-traceability audit

Audited tree: `/home/claude/wwds-dev/sentinel` (read-only; nothing run, no pytest). Date: 2026-10-09.
Method: promises extracted from README.md (row 17), docs/agents/sentry.md (AGT), docs/training/sentry.md (TRN), agents/sentry/README.md (PKG), the panel's own tooltips/labels (PNL, ui/panels/sentry.py) and the sidebar tooltip (agent_catalog.py). Implementation read in full: agents/sentry/{main.py, sentinel_chat_agent.py, sentry/*.py}, ui/panels/sentry.py, SentryWatchWorker (ui/workers.py:568-613), the wiring and shutdown path in main.py / ui/dialogs.py / ui/panels/base.py, Sentinel.spec, services/runtime_paths.py.

Abbreviations: AGT = docs/agents/sentry.md, TRN = docs/training/sentry.md, PKG = agents/sentry/README.md, PNL = ui/panels/sentry.py (user-visible text), `col` = agents/sentry/sentry/collectors.py, `eng` = engine.py, `base` = baseline.py, `wd` = watchd.py, `mod` = models.py.

NOTE: ui/tooltips.py contains NO Sentry entries at all (grep for sentry|watch|baseline|anomal|ARP|launchd = 0 hits). The only Sentry tooltips are the sidebar tooltip (services/agent_catalog.py:59) and three inline `setToolTip` calls in the panel (ui/panels/sentry.py:87, 90, 137-140) plus the shared Auto-route tooltip (ui/panels/base.py:214).

---

## 1. Verdict

The Sentry engine itself is real, honest and read-only: six unprivileged commands (`arp -an`, `ndp -an`, `route -n get default`, three `lsof` forms) are the only observation, there is no active scanning, no packet capture and no `sudo` anywhere, the MAC-keyed baseline diff, report-once folding, severity ordering, gateway-MAC alert and the shared spend guard all exist and match the docs. The weaknesses are in the integration and in how far the claims are carried. (1) The headline background-watch promise ("the panel shows what the background watcher found while the app was closed") is not delivered: no UI code ever reads the findings log, and by code reading the launchd watcher cannot import `services.runtime_paths`, so it writes a different baseline/log directory (`agents/sentry/data`) from the one the panel uses, which also defeats the cross-process locking; in a packaged .app the plist would launch the GUI binary and point at an unbundled `main.py`. (2) Stop does not stop a watch pass, can raise `AttributeError` when no AI request has run yet, and on app close that exception aborts `shutdown_panels`, so Bug Spray and Tunnel are never shut down. (3) "ARP spoofing" detection only fires when one IP shows several MACs in a single snapshot or the gateway MAC changes; a classic cache-poisoning of any non-gateway host surfaces only as a "new device" notice. (4) The AI read is ticked by default (docs say "tick to enable"), and the prompt carries IPs, MACs and process names to a cloud model and into saved chat history; "Dry run" with no baseline reports "Baseline recorded"; unprivileged `lsof` very likely cannot see root-owned listeners, which undercuts "unexpected services"; collector failures are silent. (5) Docs drift: PKG lists a `launchd/` directory that does not exist, points at a non-existent SUGGESTIONS.md entry and says the panel reads findings.json; there are no Sentry tooltips in ui/tooltips.py. Recommendation: do not start the test phase until the Section 5 P0 items are fixed or the claims are softened.

## 2. Counts

67 promises traced (rows 1-67 in Section 3; tallied from the Status column).

| Status | Count | Rows |
|---|---|---|
| IMPLEMENTED | 47 | 1-4, 6-9, 11-18, 20, 21, 23-27, 31-33, 35-37, 39-44, 46, 47, 52, 54-57, 59-62, 64 |
| PARTIAL | 10 | 5, 10, 22, 34, 38, 50, 51, 53, 63, 67 |
| STUB | 0 | - |
| MISSING | 5 | 48, 49, 58, 65, 66 |
| NOT CODE-VERIFIABLE | 5 | 19, 28, 29, 30, 45 |
| **Total** | **67** | |

Of the 5 MISSING, two are functional (48 panel never shows background findings; 49 watcher and panel do not share state - code-read, high confidence, not executed) and three are documentation/tooltip gaps (58, 65, 66).

---

## 3. Traceability table

| # | Promise (short, with source) | Status | Evidence (file:line) | Note |
|---|---|---|---|---|
| **A. Scope and safety** | | | | |
| 1 | Observation is read-only, unprivileged commands only (README:17; AGT:9-10; PKG:3; sidebar tooltip) | IMPLEMENTED | col:244-249 (`_run`: argv list, no shell, 15 s timeout); commands at col:255, 261, 265, 266, 274. Grep of agents/sentry (non-test) for `socket\|requests\|urllib\|http\|nmap\|ping\|sudo\|tcpdump\|scapy\|sniff\|Popen\|shell=True\|os.system` = only docstring/prompt text | The only non-read commands are `launchctl load/unload` (wd:136,149), which alter the user's launchd state, not the network |
| 2 | Never scans/probes other hosts; no port scans (AGT:31; PKG:20; TRN:6-7; AGT:5-6) | IMPLEMENTED | Same grep: no connect/send/ping/nmap/arping anywhere; no address iteration | `arp`/`ndp` use `-n` (no reverse DNS packets) |
| 3 | No packet capture, no sudo, no BPF (AGT:27-30, 74; PKG:17-19; TRN:20-22) | IMPLEMENTED | No pcap/tcpdump/scapy/sudo strings; col:1-11 docstring consistent with code | Whether each command succeeds unprivileged on a real macOS is row 30 |
| 4 | Collectors use `arp`/`ndp`, `route`, `lsof` (AGT:51-53; PKG:17-19, 33; TRN:12-18) | IMPLEMENTED | col:255 (`arp -an`, `ndp -an`), col:261 (`route -n get default`), col:265 (`lsof -nP -iTCP -sTCP:LISTEN`), col:266 (`lsof -nP -iUDP`), col:274 (`lsof -nP -iTCP -sTCP:ESTABLISHED`) | Full inventory in Appendix A. Docs never mention `launchctl`; chat-agent prompt (sentinel_chat_agent.py:12) omits `ndp` |
| 5 | "Changes nothing" on the system / no interface, route or remote state modified (AGT:32; PKG:21; TRN:7-8; PNL:70) | PARTIAL | True for network state. But: writes baseline.json/findings.json/lock/tmp files (base:40, 51-72, 119-152); `wd:29-32` mkdirs `~/Library/LaunchAgents` on every `plist_path()` call incl. `status()` at panel construction (sentry.py:55,349); optional install writes a plist and runs `launchctl load -w` (wd:106-118); Reset deletes files (sentry.py:301-311) | TRN:7-8 and the panel intro ("changes nothing") overstate; say "changes no network settings; stores its own state and, if you opt in, one LaunchAgent" |
| **B. Baseline** | | | | |
| 6 | First pass records a trusted baseline and reports nothing (AGT:9-10, 67; TRN:26-28; PNL:216-222) | IMPLEMENTED | eng:240-251 (`load_baseline() is None` -> `save_baseline(current)`, findings `[]`); cli.py:31-34; sentry.py:184-187 | Adopts whatever is on the network at that moment as trusted, including a hostile LAN or an already-exposed listener. Also reached silently when baseline.json is unreadable/corrupt (base:116-117 returns None) - see (c) |
| 7 | Later passes report only what is new or anomalous (AGT:10-11; TRN:27-28) | IMPLEMENTED | eng:176-186 `diff` (device + listener + connection findings); eng:253 | Pure function, no I/O |
| 8 | Each finding is reported once, then folded into the baseline (AGT:67-69; TRN:40-41) | IMPLEMENTED | eng:253-255 (diff -> append_findings -> save_baseline(merge)); eng:196-224 `merge_into_baseline` (union by key) | "Reported once" = trusted forever (an attacker MAC/service is blessed after one pass). Connections capped at 4096 (eng:193) so eviction can re-report; devices/listeners never pruned |
| 9 | A router swap is reported once as a gateway-MAC change, then settles (AGT:68-70) | IMPLEMENTED | eng:93-111 (needs same gateway IP and both MACs non-empty); eng:217-218 (gateway taken from current pass) | During the swap both MACs can briefly sit in the ARP table and also raise an `arp_spoof` alert on the gateway IP (eng:68-77), then settle |
| 10 | Dry run compares against baseline without updating it (AGT:38; PNL tooltip sentry.py:87) | PARTIAL | Compare-only path: workers.py:590-611 (no `save_baseline`/`append_findings`). Defect: with no baseline it emits `baseline_established: True` (workers.py:594-602), and the panel then shows "Baseline recorded." (sentry.py:184-187, 216-222) although nothing was saved | Dry-run results are also never labelled as dry-run in the findings pane (the `dry_run` key is ignored by the panel) |
| 11 | Reset baseline forgets everything; next pass records a fresh baseline (AGT:39, 70-71; TRN:41-42; tooltip sentry.py:90) | IMPLEMENTED | sentry.py:301-314 (unlinks baseline.json, `clear_findings()`); base:154-156 | Also wipes the findings log (undocumented); does not take `store.transaction()`; does not reset a launchd watcher that uses a different directory (row 49) |
| 12 | JSON baseline + rolling findings log under Sentinel's normal writable base `.../data/sentry/` (AGT:57-58; PKG:51-66) | IMPLEMENTED | base:75-93 (`user_data_base()/"data"/"sentry"`), 111-123, 129-152; runtime_paths.py:126-136 (dev: `<repo>/data/sentry`; frozen: `~/Library/Application Support/Sentinel/data/sentry`; portable: `<vol>/Sentinel Data/data/sentry`) | In-app process only; the headless watcher resolves a different directory (row 49) |
| 13 | Findings log is rolling, last 500 (PKG:55) | IMPLEMENTED | base:30, 150 | |
| 14 | Baseline = devices by MAC, listeners by proto:addr:port, connections by proto:remote:port, gateway from latest pass (PKG:53-54; eng docstring) | IMPLEMENTED | mod:31-35, 48-49, 62-66; eng:196-224 | Identity keys omit process/pid: a different process taking a baselined port, or a new process to a known endpoint, is invisible |
| **C. Detection** | | | | |
| 15 | New device on the segment: "a MAC/IP not seen" (AGT:13; TRN:30; README:17 "new devices") | IMPLEMENTED | eng:42-57 (`new_device`, severity notice); identity is MAC only (mod:31-35) | Wording says MAC/IP but a new IP on a known MAC (DHCP change) is NOT reported, and a known IP on a new MAC is reported as `new_device`, not as spoof. TRN:14-15 correctly says "recently talked to"; AGT:13 does not |
| 16 | ARP spoofing: one IP claimed by several MACs (AGT:14-15; TRN:31) | IMPLEMENTED | eng:62-90 (`arp_spoof`; non-gateway = warning, gateway = alert); suppressed if all MACs already in baseline for that IP (eng:75-76) | Detects duplicates inside one snapshot only. See row 19 |
| 17 | ARP spoofing: default gateway's hardware address changing (AGT:15; TRN:32; PKG:23) | IMPLEMENTED | eng:92-111 `gateway_mac_change` (alert); col:277-281 `gateway_mac` | IPv4 only (`route -n get default`); needs gateway present in ARP cache; skipped silently if baseline or current MAC empty or gateway IP changed (eng:93-95). Under a VPN default route (utun) there is no MAC, so no check |
| 18 | Anomalies on the gateway are escalated as alerts (AGT:15-16; TRN:32-34; sentinel_chat_agent.py:28) | IMPLEMENTED | eng:77 (`alert if ip == current.gateway_ip`), eng:97 | |
| 19 | The ARP-spoof indicators actually catch a man-in-the-middle in practice (README:17 "ARP spoofing"; PKG:5-6, 23-24; TRN:31-34) | NOT CODE-VERIFIABLE | Logic: eng:62-90 (duplicates in current snapshot), eng:92-111 (gateway). No check that an IP's MAC differs from the baseline's MAC for that IP (`baseline_ip_to_macs`, eng:65-67, is used only to suppress repeats, eng:75-76) | Design caveat: BSD/macOS ARP keeps one MAC per IP per interface, so poisoning overwrites the entry rather than duplicating it; for a non-gateway host the visible result is a `new_device` notice (new MAC not in `known_macs`), for the gateway a `gateway_mac_change` alert. Verify in an owned lab: run arpspoof/bettercap from a second machine against the Mac and against a client; record which findings fire |
| 20 | New listening service: a process began accepting connections (AGT:17-18; TRN:35) | IMPLEMENTED | eng:115-143; col:191-212, 264-270 | UDP "listeners" = every UDP socket (`lsof -nP -iUDP`, no state filter, col:266); connected-UDP names `local->remote` mis-parse into a bogus address (col:27, 151-158) classified "hostname" and worded "bound to loopback only" (eng:121-135) - see (c) |
| 21 | Listener flagged higher when bound to an externally reachable address (AGT:18; TRN:36) | IMPLEMENTED | eng:121-129 (`wildcard`/`public`/`private` -> warning, else notice); col:59-80 | RFC1918/wildcard bind counts as "external"; link-local bind is only a notice |
| 22 | "Unexpected services" (README:17; sidebar tooltip; PKG:6) | PARTIAL | Implemented only as "new vs baseline" (eng:117-119); no allow-list, risky-port list or per-process expectation | First pass blesses every pre-existing exposed service (eng:240-251). Docs elsewhere (AGT:17) say "new", which is accurate; README/tooltip say "unexpected" |
| 23 | New outbound connection to a public endpoint not contacted before (AGT:19-21; TRN:37-38) | IMPLEMENTED | eng:146-173; col:215-239, 273-274 | TCP ESTABLISHED only: UDP/QUIC are never observed; short-lived connections between passes are missed. Private-IP destinations are ALSO reported (severity info, eng:156), docs say public only |
| 24 | Local chatter (loopback, link-local AirDrop/Handoff, mDNS multicast) is ignored (AGT:20-21; TRN:38) | IMPLEMENTED | eng:153-154; col:59-80 | Filter is on the remote address class only. CGNAT 100.64/10 (Tailscale etc.) classifies as "public" |
| 25 | Findings are severity-sorted (AGT:55-56) | IMPLEMENTED | eng:185 | Stable reverse sort |
| 26 | MACs normalised so a NIC keeps one identity (AGT:53-54) | IMPLEMENTED | col:32-46 (used col:96, 124); mod:35 | Randomised/private Wi-Fi MACs that rotate will read as new devices (not mentioned in TRN) |
| 27 | IPv4 ARP and IPv6 NDP merged (AGT:52, 74-76) | IMPLEMENTED | col:252-257; col:107-135 | |
| 28 | Sandbox note: IPv4 ARP cache reads empty to a child process while NDP still works (AGT:74-77) | NOT CODE-VERIFIABLE | col:252-257 merge is the only code relevant | Verify by launching the app from a sandboxed host (e.g. agent/CI sandbox on macOS) and comparing `arp -an` vs `ndp -an` counts with a normally launched app |
| 29 | New devices on a real LAN are picked up (TRN:14-15 "devices your Mac has recently talked to"; AGT:13 "on the segment") | NOT CODE-VERIFIABLE | Source of truth is the OS neighbour cache only (col:252-257) | A device that joins but never exchanges traffic with this Mac, or whose entry has aged out, is invisible. Verify on a live LAN: join a phone, ping it/not, run a pass at intervals |
| 30 | "This host's own sockets / listening services" are fully visible to unprivileged `lsof` (AGT:28; TRN:17-18; sentinel_chat_agent.py:12) | NOT CODE-VERIFIABLE | col:265-266, 274 (no `sudo`) | On macOS a non-root `lsof` normally lists only the invoking user's processes, so root/other-user daemons (sshd/Remote Login, Screen Sharing, smbd) would not appear. Verify: baseline, enable Remote Login or File Sharing, run a pass; compare with `sudo lsof -nP -iTCP -sTCP:LISTEN` |
| **D. Output, AI read, controls** | | | | |
| 31 | Findings pane shows severity tag ALERT/WARNING/NOTICE/INFO + one-line detail (AGT:45-46) | IMPLEMENTED | sentry.py:30-38, 205-244 | Detail is a multi-sentence paragraph; colours hard-coded hex, not theme tokens |
| 32 | AI read: verdict, each finding explained benign + malicious, safe checks; streams below the findings (AGT:23-24, 46-48; TRN:48-51) | IMPLEMENTED | sentinel_chat_agent.py:10-30 (prompt structure), 33-49; sentry.py:266-298 (stream into `stream_box`) | System prompt tells the model it receives the neighbour table, gateway, listeners and connections (sentinel_chat_agent.py:12) - it is only given findings + counts (row 35) |
| 33 | Nothing is sent to a model when there is nothing to report / first-pass baseline (AGT:40; TRN:51; tooltip sentry.py:137-140) | IMPLEMENTED | sentry.py:183-191 (returns before AI when `baseline_established` or no findings); tests/test_ui_panels.py:3407-3423 | |
| 34 | AI read is opt-in: "When ticked..." / "Tick Explain findings with AI" (AGT:40; TRN:47-48) | PARTIAL | sentry.py:136 `setChecked(True)`; sentry.py:45 `default_provider = "anthropic"` | Default is ON with a paid cloud provider. Consent dialog (main.py:3463-3483, 3585-3613) still appears per request unless Anthropic is persistently allowed, but it does not show the payload |
| 35 | Findings are what is sent to the model (AGT:40) | IMPLEMENTED | sentry.py:246-263: per finding `[severity] title` + `evidence` dict repr, plus device/listener/connection counts | Payload contains LAN IPs, MACs, interface, process names, PIDs, ports, remote IPs. Not stated in AGT/TRN/privacy_cost. Prompt is also stored in run log (main.py:3619-3635) and Saved Chats (main.py:3666-3673) |
| 36 | AI read goes through the shared request guard authorize -> ChatWorker -> record (AGT:63-64) | IMPLEMENTED | sentry.py:268 (`authorize`), 274 (`start_worker`), 287 (`record`), 294 (`abandon`); base.py:370-401; main.py:3556-3637 | Blocked request restores controls (sentry.py:268-270; test_ui_panels.py:3433-3444) |
| 37 | Provider / Model / Auto-route / Stop shared run-bar controls (AGT:42) | IMPLEMENTED | sentry.py:130-132; base.py:165-227 | |
| 38 | Stop works (AGT:42; tooltip) | PARTIAL | `SentryWatchWorker.cancel()` only sets a flag that `run()` never reads (workers.py:579-613). `SentryPanel.stop()` (sentry.py:375-380) -> `stop_worker()` (base.py:439-444) -> `self.is_running()` is the panel override (sentry.py:371-373), True while a pass runs -> `self.worker.cancel()` with `self.worker is None` -> AttributeError whenever no AI request has run yet this session. "Stopped." and `set_busy` are then never reached | Stop does work for the AI stream (ChatWorker). A "stopped" pass still emits `finished_signal` (sentry.py:174) and can start an AI request afterwards |
| 39 | A watch pass runs off the UI thread in SentryWatchWorker (AGT:63) | IMPLEMENTED | workers.py:568-613; sentry.py:173-176 | `watchd.status()` (runs `launchctl list`, wd:77) and install/remove run on the UI thread (sentry.py:55, 319, 324, 349) |
| 40 | Roster entry: key `sentry`, SentryAgent, SentryPanel (README:17; AGT:3) | IMPLEMENTED | services/agent_catalog.py:55-60, 85; main.py:70, 295, 2428-2429, 3023; sentinel_chat_agent.py:33-35; tests/test_agent_roster.py:100 | |
| **E. Background watch** | | | | |
| 41 | Continuous background option: Install / Remove (README:17; AGT:41; TRN:55) | IMPLEMENTED | sentry.py:109-114, 317-326; wd:106-131 | |
| 42 | User-chosen interval (AGT:41; TRN:56-57) | IMPLEMENTED | sentry.py:104-108 (spinbox 1-720 min, default 5); wd:25-26, 57 (floor 60 s, default 300) | Range not documented; CLI `watch` floor is 30 s (cli.py:84) vs 60 s here |
| 43 | launchd StartInterval agent `com.sentinel.sentry.watch` re-runs `main.py --headless`; no long-lived daemon (AGT:60-62; PKG:57) | IMPLEMENTED | wd:22, 56-68 (`ProgramArguments [python, main.py, --headless]`, `StartInterval`, `RunAtLoad: True`, log to watch.log); agents/sentry/main.py:19-20 maps `--headless` -> `watch-once` | `RunAtLoad` also runs a pass at install and at every login/reboot (undocumented) |
| 44 | Per-user agent in `~/Library/LaunchAgents/` (AGT:77) | IMPLEMENTED | wd:29-36 | |
| 45 | Keeps watching while the app is closed (README:17; AGT:41; TRN:55-56; PNL:366) | NOT CODE-VERIFIABLE | Plist content is correct (wd:56-68) | Needs macOS launchd: install, quit Sentinel, wait 2 intervals, check watch.log timestamps and that state files change |
| 46 | Remove the background watch at any time (TRN:58-59; AGT:41) | IMPLEMENTED | wd:121-131 (unload -w, unlink; idempotent); sentry.py:323-326 | |
| 47 | Panel shows background-watch state (running / installed-not-loaded / off) (PNL) | IMPLEMENTED | sentry.py:328-368; wd:85-92 | |
| 48 | The panel shows what the background watcher found while the app was closed (AGT:57-59; TRN:57-58; PKG:55-56 "read by ... the in-app panel") | MISSING | Grep `load_findings\|findings.json` over the repo (excl. tests): only cli.py:110 (`report`) and base:129-152. Panel renders only the current pass summary (sentry.py:205-244); `refresh_watch_status` shows baseline timestamp and agent status only (sentry.py:328-368) | Worse: the watcher folds its findings into the baseline (eng:255), so a later manual pass will not show them either. The user's only access is `python agents/sentry/main.py report` (last 50 of 500, cli.py:114) |
| 49 | The background watcher and the panel share one baseline and one findings log (AGT:57-59; PKG:59-62; wd:5-7 docstring; eng:237-240 lock comment) | MISSING | The plist runs `python agents/sentry/main.py --headless` with `WorkingDirectory=agents/sentry` and no `EnvironmentVariables` (wd:61, 64). Then `sys.path[0]` = agents/sentry, so `from services.runtime_paths import user_data_base` (base:77-78) raises ImportError and falls back to `agents/sentry/data` (base:79-84). The panel process resolves `<repo>/data/sentry` (dev) / Application Support (frozen) / portable volume. Repo `.venv` has no .pth adding the repo root (only a1_coverage.pth, distutils-precedence.pth) | High confidence from code reading; not executed. Two independent baselines, two lock files, and `watch.log` lands in the app's directory (wd:47-53 runs in the app process). Confirm in one minute: install, wait one pass, `find ~ -name baseline.json -path '*sentry*'`. Existing tests monkeypatch `default_state_dir` (test_ui_panels.py:3365) so this is untested |
| 50 | Background watch works in the shipped self-contained app (README: self-contained builds; PKG:59-62; panel offers Install in every build) | PARTIAL | wd:39-44: interpreter = `<root>/.venv/bin/python` else `sys.executable`; in a PyInstaller build `sys.executable` is the Sentinel GUI binary (Sentinel.spec EXE "Sentinel", console=False). `MAIN` (wd:24) = bundled `agents/sentry/main.py`, which is not in Sentinel.spec `datas` (spec:35-52); `agents/` is gitignored from this repo (.gitignore:54) | Works from a dev checkout with `.venv`. In a frozen build the plist would launch the GUI app with `main.py --headless` args each interval. Verify by building the .app and installing from it |
| 51 | Background work is shut down when Sentinel quits (README:99-102 generic) vs. watch continues while closed (AGT:41) | PARTIAL | The launchd agent intentionally survives quit and reboot (wd:63, nothing in main.py:4778-4788 / dialogs.py:52-59 touches it). The in-app worker: SentryPanel has no `shutdown()`, so `shutdown_panels` (dialogs.py:52-59) calls `panel.stop()`, which hits the AttributeError of row 38 when no AI request has run; the exception is caught in closeEvent (main.py:4786-4787) but the loop over `app.panels` is aborted, and `bug_bounty` and `vpn` are registered after `sentry` (main.py:2428-2435) | Worker thread is never `wait()`ed, so a QThread can outlive the window (up to 6 x 15 s of subprocess timeouts). README sentence needs a Sentry carve-out |
| 52 | `watch.log` holds stdout/stderr of each launchd pass (PKG:57) | IMPLEMENTED | wd:47-53, 64-66 | No rotation; ~288 appends/day at 5 min. Path is the app-side directory (row 49) |
| **F. CLI** | | | | |
| 53 | `selftest` verifies the collectors run on this host (AGT:81; PKG:71) | PARTIAL | cli.py:96-106 always `return 0`; col:244-249 swallows FileNotFoundError/TimeoutExpired and non-zero exits into `""` | Prints "selftest OK - 0 devices, 0 listeners, 0 connections" when every command is missing or failing |
| 54 | `scan` = dry-run diff, nothing persisted (AGT:82; PKG:72) | IMPLEMENTED | cli.py:50-74 | `BaselineStore()` still creates the state directory (base:91-92) |
| 55 | `watch-once` = one persisted pass, what launchd runs (AGT:83; PKG:73) | IMPLEMENTED | cli.py:77-80; main.py:19-20 | |
| 56 | `watch --interval 300` (AGT:84; PKG:74) | IMPLEMENTED | cli.py:83-93, 125-127 | Interval silently clamped to >= 30 s |
| 57 | `report` prints the findings log (AGT:85; PKG:75) | IMPLEMENTED | cli.py:109-117 | Prints only the last 50 of up to 500 records |
| **G. UI text, docs, cross-references** | | | | |
| 58 | Sentry tooltips in ui/tooltips.py (assignment; tooltips.py:1-7 "every control in the app") | MISSING | Grep over ui/tooltips.py = 0 hits; blocks exist for wifi, osint, osint_heavy, bug_bounty, manager only (tooltips.py:90-164) | Untooltipped: interval box, Install, Remove, Run watch pass, Stop, state/status labels. Present: Dry run, Reset baseline, AI checkbox (inline, sentry.py:87, 90, 137-140), sidebar (agent_catalog.py:59) |
| 59 | Sidebar tooltip: "Read-only watch of your own network for new devices, ARP spoofing, and unexpected services." | IMPLEMENTED | services/agent_catalog.py:59 | Same scope and same caveats as rows 19 and 22 |
| 60 | Panel intro: watches ARP/NDP table, gateway, listeners, outbound connections; "sends no packets" (PNL sentry.py:64-71) | IMPLEMENTED | sentry.py:64-71 vs collectors | "changes nothing" part: row 5 |
| 61 | In-app Agent guide opens the Sentry capability sheet (AGT; spec) | IMPLEMENTED | main.py:4536-4551 (falls through to key `sentry`); Sentinel.spec:51 bundles docs/agents | |
| 62 | Learning Centre lesson exists (TRN; learning_center.py:61) | IMPLEMENTED | ui/learning_center.py:61; docs/training/README.md:29 | |
| 63 | Lesson meets the stated lesson standard (training/README.md:40-60: goal, time, steps, expected result, privacy/cost, completion check; Worked example; screenshot) | PARTIAL | TRN has goal/time (3-4) but no completion check, no worked example, no screenshot (docs/training/images has none), nothing on what the AI read sends or costs | Only bug_spray.md carries a Worked example today, so this is a repo-wide gap, but Sentry is the only security agent with no privacy/cost line |
| 64 | Tests exist for engine, panel wiring, spend guard, launchd calls (AGT:88-90; PKG:78-85) | IMPLEMENTED | agents/sentry/tests/test_engine.py (17 tests); tests/test_ui_panels.py:3374-3456 `TestSentryPanel` (8 tests); pytest.ini `testpaths = tests` so agents/sentry/tests is not collected by the parent suite (as PKG:84-85 says) | Not executed (instructed). Gaps: no tests for watchd, headless state dir, Stop path, dry-run-without-baseline text, `_lsof_fields` UDP `->` names, real-command collectors. The "launchd calls" test mocks `watchd` (test_ui_panels.py:3446-3456) |
| 65 | PKG:27 "Deep inspection is a future item (SUGGESTIONS.md)" | MISSING | Grep of SUGGESTIONS.md, TODO.md, docs/roadmap.md, docs/training/advanced_tools.md for packet\|inspection\|snort\|IDS\|intrusion\|BPF: no Sentry/DPI entry | Dangling pointer |
| 66 | PKG:41 layout lists `launchd/  launch-agent plist template` | MISSING | `ls agents/sentry` = README.md, conftest.py, main.py, requirements.txt, sentinel_chat_agent.py, sentry/, tests/ (no launchd/); plist is generated in code (wd:56-68) | Doc drift |
| 67 | PKG:42, 62-64 `data/` fallback is gitignored, created on first use, empty in fresh checkout | PARTIAL | Created on first use (base:85) and absent now (ok). No `.gitignore` inside agents/sentry; the parent `.gitignore:54` ignores the whole `/agents/sentry/` instead | If agents/sentry is its own repo, a runtime baseline.json (LAN MACs, remote endpoints) is not protected from commit |

---

## 4. Findings by category

### (a) Promises that exist in only one place

In UI / tooltips / training but NOT in docs/agents/sentry.md:
- TRN:14-15: the neighbour table lists devices "your Mac has recently talked to" (the real visibility limit). AGT:13 says "on the segment".
- TRN:20-22: packet capture avoided "in v1" (AGT says deliberately not done, without the versioning).
- TRN:57-58: "the next time you open the panel you see anything that happened while you were away" (AGT:57-59 implies it; neither is implemented, row 48).
- TRN:51 "Nothing is sent to a model when there is nothing to report" (also in AGT:40 - consistent).
- PNL intro (sentry.py:64-71): "sends no packets and changes nothing"; status strings ("Baseline recorded (updated <ts>)", "Background watch is running every N min. Log: ...").
- Panel facts nowhere in AGT: interval range 1-720 min (default 5), AI checkbox default ON, "Reinstall" button state.
- agent_catalog.py:60 description "Continuous, read-only network monitoring" (in-app passes are manual; continuity is the launchd option only).

In docs/agents/sentry.md (or PKG) but NOT in the UI / TRN:
- Dry run is never mentioned in TRN; Reset baseline is (TRN:41).
- Severity vocabulary and the shared run-bar/spend guard (AGT:42, 45-48, 63-64); TRN never says the AI read costs money or asks consent.
- Router-swap "reported once then settles" (AGT:68-70).
- launchd label, plist location, CLI, test pointers, sandbox ARP note (AGT:60-62, 74-77, 79-90).
- PKG only: `watch.log`, `findings.json` readers, the fallback `data/` directory, `BaselineStore(state_dir=...)`, "honest boundary" text (PKG:11-27).

AGT vs PKG disagreements:
- PKG:55-56 says findings.json is read by "the in-app panel"; the panel never reads it (AGT:57-59 makes the same promise indirectly).
- PKG:41 `launchd/` template directory does not exist.
- PKG:27 points to SUGGESTIONS.md for a "future deep inspection" item that is not there.
- PKG:57 documents `watch.log`; AGT does not.
- PKG:62-64 explains the `data/` fallback as "app package unavailable" only; in reality the launchd watcher always takes it (row 49). AGT does not mention the fallback.
- Otherwise they agree on commands, severity, the baseline model, the launchd label and the CLI.

### (b) Contradictions

1. AI read "when ticked" / "Tick ... to get" (AGT:40, TRN:47-48) vs default checked (sentry.py:136) with Anthropic as default provider (sentry.py:45).
2. "Changes nothing on your system" (TRN:7-8, PNL:70) vs persisted baseline/log/lock files, a LaunchAgent plist, and a Reset that deletes files (row 5).
3. Panel/TRN/AGT/PKG say the panel shows background findings; the panel has no reader for the log (row 48).
4. AGT/PKG/wd docstring say panel and watcher share one baseline; headless resolves a different directory (row 49).
5. README:99-102 "background work is shut down rather than left running" vs AGT:41 background agent that keeps running after close; and the in-app worker is not actually shut down cleanly (row 51).
6. AGT:19 / TRN:37 "public endpoint" vs code also reports private destinations as INFO (eng:156).
7. AGT:13 / TRN:30 "MAC/IP" vs MAC-only identity (mod:35).
8. AGT:42 lists Stop as a working shared control vs Stop not stopping a watch pass and raising AttributeError (row 38).
9. sentinel_chat_agent.py:12 says the model receives the neighbour table, gateway, listeners and connections; sentry.py:246-263 sends only findings + counts.
10. sentinel_chat_agent.py:30 ("if the data is a first-run baseline ... say plainly") and :38-45 (empty-prompt branch) are unreachable: the panel never calls the model on a baseline pass (sentry.py:184-187) and never sends an empty prompt.
11. Dry run with no baseline shows "Baseline recorded." (sentry.py:184-187, 216-222) though nothing was recorded (workers.py:594-602).
12. tooltips.py docstring ("every control in the app") vs no Sentry block; PKG:41 `launchd/` dir; PKG:27 SUGGESTIONS.md pointer.
13. PKG:42, 62-64 "gitignored" `data/` vs no `.gitignore` in agents/sentry (only the parent ignores the whole directory).

### (c) Undocumented behaviour

State and lifecycle
- Reset baseline also deletes the findings log (sentry.py:308) and takes no lock; it cannot reset the headless watcher's separate state (row 49).
- `RunAtLoad: True` (wd:63): the agent runs at install time, at every login/reboot, and persists until Remove. The first headless pass on a machine with no baseline silently adopts the current network as trusted (eng:240-251) with no confirmation or notice.
- No alert channel at all from the background watcher: no notification, tray badge, email or push. It writes files only.
- `watch.log` is never rotated; extra files created: `baseline.json.lock`, `findings.json.lock`, `watch.lock`, `.tmp-*.json` (base:40, 51-72, 95).
- Panel construction runs `launchctl list` on the UI thread (sentry.py:55, 349; wd:77, 10 s timeout), creates `~/Library/LaunchAgents` and `data/sentry` as side effects (wd:29-32; base:85, 91-92) - at app start, before the user opens Sentry.
- In portable mode the LaunchAgent lives on the host Mac (`~/Library/LaunchAgents`), outside the USB volume and outside Emergency Reset (portable_reset.py), which conflicts with the spirit of docs/training/portable.md:37 ("confirm no new Sentinel data appeared under Application Support"); it will also fire against a vanished volume after eject.
- Install/remove call `launchctl load/unload -w` (deprecated verbs; `-w` writes the disabled-overrides db) and the result is judged by exit code only (wd:142-144) - `launchctl load` can exit 0 on failure; only `refresh_watch_status` would reveal "installed but not loaded".

Detection behaviour
- Listener identity excludes process/pid; connection identity excludes process (mod:48-49, 62-66).
- UDP "listeners" are every UDP socket; connected UDP sockets mis-parse (`local:port->remote:port` matches `_LISTEN_RE` with the whole `local:port->remote` as address, col:27, 151-158) and are then described as "bound to loopback only" (eng:121-135) with ephemeral source ports that make each one look new.
- Outbound coverage is TCP ESTABLISHED only (col:274); UDP/QUIC and sub-interval connections are never seen.
- Baseline growth: connections capped at 4096 with eviction/re-report (eng:193); devices and listeners unbounded.
- A gateway IP change (new network) silently replaces the stored gateway and raises no finding (eng:93, 217); all devices then read as new.
- A corrupt, truncated or schema-mismatched baseline.json is treated as "no baseline" and re-adopted as trusted with no finding or UI notice (base:116-117; eng:242-251). Same for any first pass in which a collector failed: `_run` returns "" on missing command/timeout/non-zero exit (col:244-249), so an empty dimension is baked in as trusted and every real item later reads as new; a mid-life collector failure yields a plain "No new anomalies." (sentry.py:226).
- `gateway_mac` takes the first matching ARP row (col:277-281); with a duplicate gateway entry the result depends on table order.
- Platform: macOS-only parsers and launchd with no platform guard; on other OSes `install()` would still create `~/Library/LaunchAgents`.

Data handling
- The model prompt (LAN IPs, MACs, process names, ports, remote IPs) is persisted in the run log and Saved Chats (main.py:3619-3635, 3666-3673); the consent dialog (main.py:3463-3483) does not show the payload. privacy_cost.md, testing_roadmap.md, advanced_tools.md and manual_test_cases.md do not mention Sentry at all.
- baseline.json is plaintext and can hold up to 4096 remote endpoints, every seen MAC/IP and listener/process names.

CLI
- `watch` interval floor 30 s (cli.py:84), launchd floor 60 s (wd:25), panel minimum 1 min; `report` prints the last 50 only; `--headless` alias exists (agents/sentry/main.py:19-20) but is documented only in the file's docstring.

---

## 5. Must fix before the test phase

P0 (blocks a meaningful test of the advertised feature set)
1. Shared state for the background watcher (rows 49, 48): have `wd.build_plist` pass the resolved state directory to the headless run (e.g. `EnvironmentVariables: {SENTRY_STATE_DIR: str(default_state_dir())}` or a `--state-dir` argument) and make `BaselineStore` honour it, then add a test that the headless and panel stores point to the same files.
2. Show background findings (row 48): on panel show and after `refresh_watch_status()`, load `BaselineStore().load_findings()` and render a "Since you last looked" list (mark as seen), or change the three docs and drop the claim.
3. Fix Stop and shutdown (rows 38, 51): make `SentryPanel.stop()` null-safe (`if self.worker is not None`), have `SentryWatchWorker.run()` check `_cancel_requested` before persisting/emitting, disconnect `finished_signal` on cancel, add `SentryPanel.shutdown()` that cancels and `wait()`s, and ensure `shutdown_panels` cannot be aborted by one panel (try/except per panel in ui/dialogs.py:52-59).
4. Packaged-build background watch (row 50): in a frozen build either disable Install with an explanatory message or route the plist to `Sentinel.app/Contents/MacOS/Sentinel --sentry-headless` handled early in main.py, and bundle/resolve the correct entry point; never use `sys.executable` blindly.
5. AI default and disclosure (rows 34, 35): set `ai_checkbox` unchecked by default (or fix the docs), add a one-line "sends IPs/MACs/process names to <provider>" note next to it and in TRN/privacy_cost.md, and prefer a local provider by default for this panel.

P1 (will cause confusing test results)
6. Dry run without a baseline (row 10): honour `summary["dry_run"]` in `_pass_finished`/`_render_findings` ("Dry run: no baseline exists, nothing was saved") and label dry-run output.
7. Make ARP-spoof detection match its name (rows 16, 19): add a check that flags an IP whose MAC differs from the baseline's MAC(s) for that IP, then test in an owned lab with arpspoof/bettercap; until then state the limit in AGT/TRN.
8. Fix UDP handling (row 20): skip UDP rows whose NAME contains `->` (connected sockets) and consider limiting UDP to bound-unconnected sockets, to stop bogus "listeners" and notices.
9. Surface collector failures (rows 53, `_run`): return (text, ok) from `_run`, make `selftest` exit non-zero when a collector fails, show a warning in the panel when any collector returned nothing, and refuse to adopt a first-pass baseline from a failed collector.
10. Validate privilege coverage (row 30): run the Remote-Login test on a real Mac; if root listeners are invisible, state it in AGT/TRN ("your user's processes only") or add an explicit opt-in elevated mode.
11. Add the missing tooltips (row 58): a `sentry.*` block in ui/tooltips.py (and object names/`_set_tooltips` keys) for interval, install, remove, run, stop, state and status labels.
12. Correct the docs: PKG:41 (`launchd/`), PKG:27 (SUGGESTIONS.md), PKG:55-56 (panel reads log), AGT:13/TRN:30 (MAC-only), AGT:19 (private destinations are reported as INFO), TRN:7-8 and sentry.py:70 ("changes nothing"), README:99-102 (Sentry LaunchAgent persists), add a completion check + worked example + privacy/cost line to TRN, and add Sentry to testing_roadmap.md, privacy_cost.md and advanced_tools.md.

P2 (hardening)
13. Make Reset baseline atomic and complete: wrap in `store.transaction()`, document that it clears the findings log, and also reset the headless watcher's state.
14. Record a notice finding when an unreadable baseline is re-adopted (base:116-117, eng:242-251) instead of silently trusting.
15. Rotate `watch.log` and avoid `launchctl list` / mkdir on the UI thread at panel construction (sentry.py:55; wd:29-32, 77).
16. Add `.gitignore` inside agents/sentry for `data/`, and tests for watchd (plist content), the headless state dir, the Stop path and the UDP `->` parse.

---

## Appendix A. Command inventory (everything Sentry executes)

| # | Command | Where | Purpose | Active scanning? |
|---|---|---|---|---|
| 1 | `arp -an` | col:255 | IPv4 neighbour cache (no DNS: `-n`) | No - reads kernel cache |
| 2 | `ndp -an` | col:255 | IPv6 neighbour cache | No |
| 3 | `route -n get default` | col:261 | default gateway + interface (routing-socket query) | No - sends no packets |
| 4 | `lsof -nP -iTCP -sTCP:LISTEN` | col:265 | TCP listeners | No |
| 5 | `lsof -nP -iUDP` | col:266 | all UDP sockets (no state filter) | No |
| 6 | `lsof -nP -iTCP -sTCP:ESTABLISHED` | col:274 | established TCP | No |
| 7 | `launchctl list` | wd:77 | is the agent loaded (panel status, UI thread) | No - reads launchd |
| 8 | `launchctl load -w <plist>` | wd:136 | install/start background watch | Changes user launchd state only |
| 9 | `launchctl unload -w <plist>` | wd:149 | remove/reload | Changes user launchd state only |

No nmap, ping, arping, tcpdump, curl, sudo, sockets or HTTP calls exist anywhere under agents/sentry (non-test).

## Appendix B. How the baseline is built, stored, compared

- Build: first pass `run_watch` (eng:227-251) saves the raw snapshot as baseline.json. Later passes `merge_into_baseline` (eng:196-224): devices keyed by MAC, listeners by `proto:addr:port`, connections by `proto:remote:port` (cap 4096), unions with re-seen items moved to the tail; gateway ip/mac taken from the current pass.
- Store: baseline.json + findings.json (last 500) + lock files in one directory; writes are `fcntl.flock`-serialised and atomic (`mkstemp` + `fsync` + `os.replace`, base:33-72); whole pass wrapped in `store.transaction()` (eng:240; base:97-108).
- Compare: `diff(baseline, current)` (eng:176-186) is pure. New device = MAC not in baseline (eng:42-57). `arp_spoof` = one IP with >1 MAC in the current snapshot and not already known in the baseline (eng:62-90; gateway IP -> alert). `gateway_mac_change` = same gateway IP, both MACs known, MACs differ (eng:92-111). New listener = key not in baseline (warning if wildcard/public/private bind else notice). New connection = key not in baseline, remote class not loopback/link-local/multicast (notice if public else info).
- "ARP spoofing" therefore means exactly: (i) duplicate MACs per IP inside one snapshot, (ii) gateway MAC differs from the stored gateway MAC. Nothing compares per-IP MAC bindings across passes for non-gateway hosts.

## Appendix C. AI data flow

Panel (sentry.py:246-263) builds: "Read-only network watch pass." + counts of devices/listeners/connections + for each finding `- [severity] title` and `evidence: {dict repr}` (IP, MAC, interface, process, pid, protocol, address, port, remote address, reach class) + "Interpret these findings for the operator." Wrapped with the SentryAgent system prompt (sentinel_chat_agent.py:10-30). Sent to the selected provider only after `authorize()` (budget + external-API consent dialog, main.py:3556-3637), only when findings exist and a model is selected (sentry.py:183-193). Stored afterwards in the run log and Saved Chats. Not sent: finding `detail` text, the baseline, the raw ARP/NDP table, listener and connection lists.

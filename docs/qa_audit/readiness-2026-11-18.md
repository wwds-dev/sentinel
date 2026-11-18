# Readiness audit before the testing phase — 2026-11-18

Code-level promise audit of Sentinel v2.002 at `e7f53a0` (`main`, clean, fetched 2026-11-18), the follow-up to
the 2026-10-09 per-agent audits in this folder and to the doc-only sweep of the same day. Foundry phase:
Verify (7) with a Secure (8) read of the privileged layer. Read-only: no application code was changed, nothing
ran as root, no tunnel, MAC, pf or network-posture change was made. Chat, Trace and Bloodhound (one reviewer)
and Beacon, Sentry and Forge (another) were rechecked by read-only subagents; every High and Blocker they
reported was re-read before it went in here, and one (Sentry H3) was downgraded after that read.

Status words: **Confirmed** = the code path was read at the cited lines. **Unconfirmed** = needs a run or
live hardware; the reason is given. Line numbers are at `e7f53a0`.

---

## 1. Verdict

**Not safe to start testing yet.** Three blockers, all small:

1. **B1. Sentinel contacts a cloud provider at launch and in Local only**, against the README and the app's
   own invariant ("Local-only never contacts a cloud provider"). Every agent panel defaults to `anthropic`
   and lists its models live, on the UI thread, ungated by the allow boxes or the mode.
2. **B2. The local venv is not the one `requirements.txt` describes** (11 packages behind or missing,
   including the Pillow 13 and certifi security bumps and `paramiko>=5.1`). Tests and any live launch from
   this checkout run old dependencies.
3. **B3. PR #52 is still open**, so two local-model stores still write into a folder an Emergency Reset is
   erasing.

Fix those, then decide the four High items in the Tunnel and Local-only areas (H1-H4) before the matching
live checks, because each one changes what a tester should expect to see. Everything else can be tested as
is if the tester knows the Medium items below.

What holds up well: the privileged layer is careful. Every Sentinel root action goes through the macOS
dialog with `allow_cached_sudo=False` (Connect/Disconnect `services/vpn_connection.py:48`,
`services/openvpn_manager.py:52`; kill switch `agents/vpn_agent/services/killswitch.py:957`; MAC
`agents/vpn_agent/services/macaddr.py:586-590`; server installer `agents/vpn_agent/server/deploy.py:354-358`,
which also hashes a root-owned copy before running it), root runs verified copies with `env -i`, and the
budget gate bills an unknown price at the dearest rate (`services/usage_tracker.py:133-139`). The 2026-10-09
Tunnel defects are fixed: `VpnConnectionWorker.cancel()` exists (`ui/workers.py:615`), and the OpenVPN pid
now lives in root's `/private/var/run/sentinel-vpn/ovpn/` (`services/vpn_rootscript.py:216`), so the
space-in-path identity bug is gone.

## 2. Counts

| | Blocker | High | Medium | Low | Total |
|---|---|---|---|---|---|
| New findings (this audit) | 3 | 6 | 14 | 9 | 32 |

Doc-sweep leads checked: 14 (10 confirmed, 1 refuted as a gap, 1 partly addressed here, 2 not
reproduced or partly unconfirmed). Recheck of the 2026-10-09 rows and code follow-ups: 173 rows, 80 fixed,
47 still open, 30 changed (limitation now documented), 16 unconfirmed; the Tunnel and shared rows were not
re-read row by row.

Test suites (run once, §6): main 4208 passed, 2 skipped, 1 deselected (`slow`, run separately: passed);
Bug Spray 52 passed; Sentry's 25 are inside the main run.

---

## 3. Findings, ranked

### Blocker for testing

| # | Promise (where) | What the code does | Evidence | Smallest fix | Status |
|---|---|---|---|---|---|
| B1 | "with the boxes unticked, as they are at every launch, Sentinel contacts no cloud provider at all" (README.md:96-99); app-context invariant "Local-only never contacts a cloud provider"; AGENTS Qt rule (no fetch on the UI thread) | `AgentPanel.default_provider = "anthropic"`; building the provider row calls `load()` at once, which reaches `models_for_provider`, whose cloud branch calls `client.list_models()` with no mode or permission check. With a key set, that is `models.list()` to api.anthropic.com (DeepSeek/Kimi/etc. likewise), on the UI thread, at panel construction, on every provider change, and via `route_for_request` for every ticked provider on each keystroke. Tests hide it by patching `list_models` | `ui/panels/base.py:143,112-121`; `main.py:1299-1300`, `main.py:3573-3575`, `main.py:1077-1081`; `services/anthropic_client.py:39-47`; `tests/_isolation.py:49` | In `models_for_provider` return the cached list (`_cached_models`, `main.py:3855-3863`) for cloud providers; leave live listing to the gated `ModelScanWorker` (`scan_plan`). Add a socket-recording launch test | Confirmed (read); not run |
| B2 | "Install deps: pip install -r requirements-dev.txt" (app-context Commands); TODO "dependency advisories are fixed on `main`" | `.venv` has pillow 12.2.0 (want 13.0.0), certifi 2026.2.25 (want 2026.10.23), paramiko 3.5.1 (want `>=5.1,<6`), urllib3, idna, anyio, httpx2/httpcore2, typing-extensions/inspection behind; `keyring` missing. app-context still says `paramiko>=3.4,<4` | `requirements.txt:33`; offline `importlib.metadata` comparison, 2026-11-18 | Rebuild with `_Admin/rebuild_envs.sh` before any test or live launch; fix the paramiko line in app-context | Confirmed |
| B3 | Emergency Reset "permanently deletes the contents of Sentinel Data … and quits" (docs/portable_mode.md:30) | `RatingStore._save` and `routing_advisor._write_json` do not check `writes_allowed()`; a parked rating run can write into the folder being erased. PR #52 adds both checks and a test; open, checks green 2026-11-12 | `services/local_ratings.py:270-272`; `services/routing_advisor.py:622`; `gh pr view 52` (OPEN) | Merge PR #52 | Confirmed |

### High

| # | Promise (where) | What the code does | Evidence | Smallest fix | Status |
|---|---|---|---|---|---|
| H1 | Kill switch: "…DHCP, and your local network, so a dropped tunnel cannot leak" (docs/training/tunnel.md:186-191; same list README.md:257-262, docs/agents/vpn.md:73); CLAUDE.md "No DNS … leaks; all DNS goes through the tunnel" | `arm()` always passes `allow_lan=True` (no UI toggle), so the anchor passes all traffic between private ranges. A Mac whose resolver is the home router (the usual case) keeps sending DNS to it through `pass quick inet from 192.168.0.0/16 to 192.168.0.0/16` after the tunnel drops; the router forwards it in the clear. The LAN exemption is disclosed; the "cannot leak" conclusion is not true for DNS | `agents/vpn_agent/services/killswitch.py:58,299,363-369,661,696`; `services/vpn_connection.py:302-303` | Block port 53 (UDP/TCP) to LAN ranges in the anchor unless the user opts in, or add an "Allow local network" tick that is off by default; until then reword to "…cannot leak, except DNS to a resolver on your local network" | Rule-level confirmed; live effect needs the owner check O3 |
| H2 | Same DNS rule; Tunnel "real WireGuard/OpenVPN client" (app-context, README) | OpenVPN: pushed DNS is ignored (README.md:243 says "not supported"), so DNS stays on the system resolver while the tunnel is up; the Connect review shows no DNS line for OpenVPN and gives no warning. WireGuard without a `DNS =` line: the review says "Not specified — system DNS stays in use" as an info line, not a warning. With H1, neither leak is stopped by the kill switch | `services/vpn_execution.py:1590-1595` (WG only); docs/agents/vpn.md:61 ("no routing/DNS intent for OpenVPN") | Make "system DNS stays in use" a warning on the Connect review for both protocols; name it in the kill-switch text | Confirmed (read) |
| H3 | Local only "No cloud calls, ever" (`services/routing_advisor.py:28-29`; README Local-only section) | For agent panels, Local only is enforced only by the routing advisor. `prepare_agent_route` lets an unticked cloud provider with a key through (`return None`); `authorize_request` never checks the mode and offers the one-time consent dialog; and `panel_routing.advise` returns True (proceed) when the advisor raises. So any advisor exception turns Local only into "cloud after one Yes". Chat itself is safe (`main.py:3719-3733`) | `ui/panel_routing.py:56-61`; `main.py:3655-3661`; `main.py:4524-4543`; `ui/panels/base.py:380-397` | Refuse a cloud provider in `authorize_request` when the agent's mode is Local only (or Privacy first); in `advise`, return False when the mode is Local only and the advisor raised | Confirmed (read) |
| H4 | "Chat starts in Local only mode … with every paid provider unticked" (README.md:76-80; `docs/training/controls_settings.md:40-47`; `ui/tooltips.py:25`) | Default `routing_mode` is `local_advise`, which the run bar maps to "Hybrid allowed"; neither settings file sets `routing_mode`. Cloud picks with an unticked box are blocked (not substituted), and the LMArena refresh runs at start-up because it skips only in Local only | `services/routing_advisor.py:53,81-87`; `main.py:2831,3811-3820,1584-1591`; `config/settings.json` (no key) | Decide the intent: default `routing_mode` to `local_only`, or reword README, training and tooltip | Confirmed |
| H5 | Sentry: "New listening service … a socket on this Mac not in the baseline" (docs/agents/sentry.md) | `parse_lsof_listeners` feeds connected UDP rows (`192.168.1.5:5353->1.2.3.4:53`) to `^(.*?):(\d+)$`, producing a "listener" at `192.168.1.5:5353->1.2.3.4`, port 53; each new remote is a new NOTICE. Reproduced with the parser by the reviewer | `agents/sentry/sentry/collectors.py:27,191-212,294`; `engine.py:125-128` | Skip names containing `->`; add a connected-UDP fixture to `test_engine.py` | Confirmed (parser run) |
| H6 | Sentry: "a trusted baseline … reports only what is new" (docs/agents/sentry.md) | A present-but-unreadable `baseline.json` is treated as absent and silently replaced by the current snapshot (`baseline_established`, no finding); a corrupt findings log is replaced by the new records only. Documented as behaviour, but it lets anything that can write the state folder reset what Sentry trusts without a trace | `agents/sentry/sentry/baseline.py:160-166,182-208`; `engine.py:254-266` | Distinguish absent from unreadable; for unreadable, emit a WARNING finding and keep the file as `.corrupt` | Confirmed (read) |

### Medium

| # | Promise (where) | What the code does | Evidence | Smallest fix | Status |
|---|---|---|---|---|---|
| M1 | "ProtonVPN is started and stopped through its macOS system VPN service (`scutil --nc start|stop`)" (README.md:301-305) | The VPN Apps tab shows Proton's state only; the panel text and docs/agents/vpn.md:67 say a real test showed `scutil --nc start` is ignored | `ui/panels/vpn_apps_tab.py:1,28-33,100-123` | Reword README.md:301-305 (and app-context "Mullvad/ProtonVPN app control") | Confirmed |
| M2 | Owner check "confirm `vpn/openvpn.log` is gone … `vpn/openvpn.device` holds the right `utunN`" (TODO.md:69); "pid file in Sentinel's data folder (`vpn/`) … `vpn/openvpn.device`" (docs/agents/vpn.md:61) | Since S8a the pid, device and start-up log live in root's base: `/private/var/run/sentinel-vpn/ovpn/openvpn.pid`, `ovpn/device`, log under `ovpn/private/`. The owner checklist points at files that no longer exist | `services/vpn_rootscript.py:213-219`; `services/openvpn_manager.py:92-99` | Update TODO.md:69 and docs/agents/vpn.md:61 to the root paths (`sudo ls -l /private/var/run/sentinel-vpn/ovpn/`) | Confirmed |
| M3 | Connect review "Routing: Full tunnel" (`config_inspection._route_mode`) | `0.0.0.0/0` alone is labelled "Full tunnel", so an IPv4-only config reads as full while IPv6 bypasses the tunnel (unless the kill switch is armed) | `agents/vpn_agent/services/config_inspection.py:116-121` | Label "Full tunnel (IPv4 only: IPv6 leaves outside the tunnel)" and warn | Confirmed (read) |
| M4 | Nmap "Confirm authorisation" names the target (docs/agents/bug_bounty.md:47) | The dialog's "Target" and the audit line's `target` come from the Target field, or the command's last word, not from what nmap will scan; any nmap option passes (`-iR` random internet hosts, `-iL` file lists, `--script` NSE code, `-oN/-oX` file writes) | `ui/panels/bug_bounty.py:351,363-376,396-414` | Show the parsed targets in the dialog; refuse `-iR`, `-iL`, `--script*`, `-o*`, `--resume`, `--datadir` (or list them as needing a second Yes) | Confirmed (read) |
| M5 | Bug Spray: "scope … declared, not enforced" (honest); bug_spray TODO P1 scope-confirmation gate | No comparison of the target with the stored program scope (`programs.sqlite3` already holds in-scope assets). The P1 lives only in `agents/bug_spray/TODO.md:39,63`, not in Sentinel's TODO | `ui/panels/bug_bounty.py:332-393` | Add the gate: warn or refuse when the target matches no stored in-scope asset of the named program | Confirmed |
| M6 | docs/agents/bug_bounty.md:43 "…starts it in the same click, with no review step" | Contradicted by line 47 and the code: Program required, then a default-No confirmation | `ui/panels/bug_bounty.py:353-375` | Reword line 43 | Confirmed |
| M7 | Root runs only reviewed code (ADR 0004) | D10 still open by owner decision: root's PATH starts with wg-quick's own folder (`/opt/homebrew/bin`, owned by the user here: `drwxrwxr-x as admin`); openvpn likewise. The review warns, does not block | `services/vpn_rootscript.py:72`; `services/vpn_execution.py:1634-1643,1720-1724` | Tracked (TODO D10 P2, P3 item); the fix is a root-owned helper | Confirmed |
| M8 | D12 hooks only from `/Library/Sentinel/hooks/` (spec §16) | Not built: no reference to that path anywhere; WireGuard hook `#!` interpreters are not checked (OpenVPN's are) | grep: no `Library/Sentinel` in `services/`, `agents/vpn_agent/`, `ui/`; `_interpreter` called only from `services/vpn_execution.py:925` (OpenVPN) | Tracked (TODO P1 D12, P3 WG interpreter) | Confirmed |
| M9 | Trace Live Research / Exposure Check under Local only (Local-only tooltip) | They run their public-source lookups in any mode, which Trace's docs do not say (Bloodhound's do); showing either panel downloads the OSINT Framework catalogue from GitHub with no mode check | `ui/panels/osint.py:57-62`; `services/osint_catalog.py:132-160` | Say so in docs/agents/osint.md and the Local-only tooltip, or gate `refresh_in_background` on the mode | Confirmed |
| M10 | Bloodhound live collection "collects, then the model writes the dossier" | A collection exception becomes an empty result; the prompt does not say collection failed, so a paid model can write "no findings" as a clean result | `ui/panels/osint_heavy.py:611-617`; `agents/osint_heavy_agent/__init__.py:593-612` | Put "live collection failed: <reason>" into the prompt and status line | Confirmed by reviewer, read |
| M11 | Trace refuses private or loopback IPs | Bloodhound has no such guard; a 10.x or 127.0.0.1 target is sent to external IP services after consent | `agents/osint_agent/__init__.py:343-360` (Trace only) | Reuse `non_public_ip_reason` in Bloodhound | Confirmed by reviewer |
| M12 | Sentry Reset baseline: "the next pass records a fresh baseline" | A pass collects before taking the lock, so a Reset during collection is followed by the pre-reset snapshot saved as the new baseline; Reset is not disabled while a pass runs | `agents/sentry/sentry/engine.py:244,252,265`; `ui/panels/sentry.py:338-348` | Disable Reset while running, or collect inside the lock (TODO.md:141, P3) | Confirmed by reviewer |
| M13 | Emergency Reset write latch covers Sentry | `transaction()`, `_file_lock` and the state-dir `mkdir` skip `WRITE_GUARD`; `SentryPanel` has no `shutdown()`, so its worker is not joined before erasing; `run_watch` reports "baseline established" when the guard dropped the save | `agents/sentry/sentry/baseline.py:48-69,108-131,146-157`; `engine.py:265-266` | Guard `transaction()` and `mkdir`; add `SentryPanel.shutdown()` that cancels and waits (TODO.md:188 P3-2 covers part) | Confirmed by reviewer |
| M14 | Forge scaffolds "compile before save" (honest); docs/testing_roadmap.md:76 P1 "approved scaffold imports under isolated test path" | Tests only `ast.parse`/`compile` the generated source; nothing imports a generated file or calls its `build_messages` | `tests/test_agent_factory_validation.py:150-174` | One test that writes a scaffold, imports it with `importlib.util.spec_from_file_location` and checks `build_messages` | Confirmed by reviewer |

### Low

| # | Promise (where) | What the code does | Evidence | Smallest fix | Status |
|---|---|---|---|---|---|
| L1 | Bug Spray audit "each attempt is recorded" (docs/agents/bug_bounty.md:47) | If the audit write fails, the scan still starts (fail-open); the file is created with the default umask and chmodded after the write; no `writes_allowed()` check | `ui/panels/bug_bounty.py:103-113,376` | Refuse to start when `record_nmap_attempt` returns None; open with `os.open(..., 0o600)` | Confirmed |
| L2 | MAC changer "record the original address" (CLAUDE.md) | A record that fails JSON/UTF-8 decoding is replaced by an empty one on the next `pending_restores()` | `agents/vpn_agent/services/macaddr.py:326-327,453-455` | Tracked (TODO.md:195): move it aside instead | Confirmed |
| L3 | Root-runner installer swap refusal (deploy.py comment) | The two tests that exercise the real runner skip unless run as root, so the hash-swap refusal has never run in CI or locally | `tests/test_vpn_review_fixes.py:151-155` (2 skips) | Run once in a throwaway VM as root at Verify; record the result | Confirmed (skip seen) |
| L4 | Companion code is live | `agents/vpn_agent/services/profile_store.py` is imported by nothing outside its own module | grep over `ui/`, `services/`, `main.py` | Delete or mark legacy | Confirmed |
| L5 | Emergency Reset stops Sentinel work | It never removes Sentry's launch agent. Reachable only when portable mode is forced from a source checkout (`SENTINEL_PORTABLE`-style env, `services/runtime_paths.py:44-47`), since Install refuses in packaged builds; the reviewer's "recreates the erased data" does not follow because the launchd job does not carry the portable variable | `services/portable_reset.py:36-69`; `agents/sentry/sentry/watchd.py:142-150` | Call `watchd.remove()` in the reset when installed, or note it in docs/portable_mode.md | Narrowed after re-read |
| L6 | Sentry docs | Several Sentry, Beacon and Forge docs now lag code that was fixed (Handshake Capture refused, Install verifies with `launchctl print`, Forge dialog defaults to No, `agents/sentry/tests` collected by the root `pytest.ini`) | reviewer notes, L9 | Update docs/agents/sentry.md, wifi.md, manager.md, agents/sentry/README.md:151 | Confirmed by reviewer |
| L7 | Beacon "passive operations are unaffected" for a non-injecting adapter (README.md:328) | WPS Audit and PMKID commands are generated for an adapter marked `inject: False`; reaver/bully/hcxdumptool transmit (domain knowledge, not from the repo) | `agents/wifi_agent/__init__.py:363-431` | Treat both as injection-class, or reword | Unconfirmed on driver behaviour |
| L8 | UI thread never waits (AGENTS Qt rule) | Sentry runs `launchctl` (up to ~40 s on Install) and Beacon runs `system_profiler`/`networksetup`/`route` (up to ~26 s) on the UI thread | `ui/panels/sentry.py:55,351`; `ui/panels/wifi.py:303,360,415` | Move to workers | Confirmed by reviewer |
| L9 | Bloodhound prompt hygiene | Third-party text (bios, page titles, WHOIS) goes into the prompt under "Treat every value above as a confirmed fact" | `agents/osint_heavy_agent/__init__.py:595-601` | Label the block as untrusted data | Exploitation unconfirmed |

Further Low items from the two reviews (dead duplicate `get_recommended_setup` at `main.py:916`, lost code
indentation in the Chat transcript, stale Trace tooltips and `.env` hints, EXIF on TIFF/BMP/HEIC, Size-column
sort, SFTP robustness, Sentry ARP-conflict check that `arp -an` cannot trigger, headless watcher always exits
0, Forge rich-text in the confirmation) are the still-open rows in §5; each is already a `code_followups.md`
item or a row of the per-agent files.

## 4. Leads from the doc sweep

| Lead | Verdict | Evidence |
|---|---|---|
| VPN routing, DNS, reconnect and kill switch covered only by stubs or injected tests; seven owner live checks open | Confirmed. Seven `agent:vpn` P1 testing items under "Before testing" (TODO.md:63-69); no live coverage exists. Two of them name stale paths (M2) | `tests/test_vpn_*` use injected runners |
| Root runs Homebrew `wg-quick`, `wg`, `wireguard-go`, `openvpn` and Sentinel from user-writable paths (D10, ADR 0004 R1) | Confirmed; warned, not blocked, by owner decision | M7 |
| D12 (hooks only from `/Library/Sentinel/hooks/`) unbuilt; WireGuard hook `#!` interpreters unchecked | Confirmed | M8 |
| PR #52 unmerged | Confirmed | B3 |
| Routing advisor never ran against a real LM Studio model | Confirmed as untested (all advisor tests use fakes); owner check O8 | TODO.md:62 |
| Automatic Ollama fallback at a budget cap is unbuilt | Confirmed as unbuilt, but **no doc promises it**: it is a backlog idea only (TODO.md:130). Not a gap | grep README, docs/agents, docs/training |
| qa_audit "Narrower" and "Stub/missing" rows untriaged | Partly done here: §5 rechecks 191 rows; the decisions (change code or reword) stay with the owner (TODO.md:71) | §5 |
| Bug Spray scope declared, not enforced; scope-confirmation gate is an open P1 | Confirmed; app-context's "nmap in one click with no scope check" is out of date (Program and a default-No confirmation are required now) | M4, M5 |
| Forge only checks that scaffolds compile | Confirmed; the Forge docs are honest, the testing roadmap's P1 is unmet | M14 |
| Sentry baseline race | Confirmed | M12 |
| MAC record deleted on a decode error | Confirmed | L2 |
| Orphaned `head -c` processes from root-script tests | Not reproduced: `pgrep -fl '/usr/bin/head -c'` found none after the full run. The trigger is a mutation run (TODO.md:192), which this audit did not do; the item stands | — |
| Lint/type CI not configured | Confirmed: `tests.yml` runs pytest only; `ruff.toml` exists, no CI step | `.github/workflows/tests.yml:31-40` |
| `paramiko` advisory unfixed; public repo with no LICENSE | LICENSE: confirmed absent, repo PUBLIC (`gh repo view`). paramiko: `requirements.txt` now asks `>=5.1,<6`, which may already contain the fix, but the local venv has 3.5.1 (B2) and `pip-audit` was not run here (needs network): unconfirmed | `requirements.txt:33`; `.github/workflows/security.yml:22-26` (non-blocking) |

## 5. Recheck of the 2026-10-09 rows

| Scope | Rechecked | Fixed | Still open | Changed (documented) | Unconfirmed |
|---|---|---|---|---|---|
| Chat rows | 27 | 12 | 8 | 5 | 2 |
| Trace rows | 24 | 13 | 5 | 3 | 3 |
| Bloodhound rows | 22 | 7 | 5 | 1 | 9 |
| code_followups 6-36 | 31 | 15 | 11 | 5 | 0 |
| Beacon, Sentry, Forge rows and follow-ups | 65 | 31 | 16 (Sentry 8, Forge 8) | 16 | 2 |
| Tunnel headline defects (tunnel.md §1) | 4 | 2 (worker cancel, OpenVPN pid identity) | 2 (kill switch scope: H1/H2; refusal audit not re-read) | 0 | 0 |
| Tunnel and shared rows | not re-read | | | | |

Still open, by agent (the per-agent files keep their row numbers):
- **Chat:** rows 6/8 (per-tool recommended models are dead config; duplicate `get_recommended_setup`
  `main.py:916`), 7, 13 (code indentation lost, `main.py:4188`), 19 (ChatAgent stub, documented), 27 (Stop
  hidden when idle), 34 (reopen restores transcript and project only), 45 (project settings UI deferred),
  63 (image/speech models in the OpenAI dropdown, `main.py:3342-3348`).
- **Trace:** rows 8 (model override control unused, `ui/panels/base.py:264`), 88 (Exposure dialog
  pre-ticks breach sources), 90/101 (tooltips), follow-ups 8-11 (tooltips, `.env` hints at
  `osint.py:677,685`).
- **Bloodhound:** rows 5 (EXIF via `_getexif`), 8 (default model is paid Opus), 14 (M10), 61 (ssh config
  overrides the panel), 67 (Size sort), 87 (EXIF consent is the generic dialog); follow-ups 26, 29, 32 (full
  assembled prompt stored as the saved user message, `main.py:4616,4654`), 34 (SFTP: no timeout,
  `SSHException` aborts the search), 35.
- **Sentry:** row 51 (no `shutdown()`), follow-ups 39, 40 (panel intro and Learning Centre wording), 41, 42
  (headless output), 44 (H5), 45 (IP-MAC change check), 47 (launchd template).
- **Forge:** F-08, F-26 (streaming cancel ends as an error), F-33, F-35, F-42 (M14), F-48, follow-ups 72
  (wrong-typed spec fields raise in the slot, fails closed), 73b, 74, 75.

## 6. Test run

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -rs` from the repo root, 2026-11-18, with the stale
venv of B2:

- **Main suite** (`testpaths = tests agents/sentry/tests`, `-m "not slow"`): **4208 passed, 2 skipped,
  1 deselected, 0 failed** in 305 s. Skipped: `tests/test_vpn_review_fixes.py:151` ×2, "runs the root runner
  for real; needs root and shasum" (L3).
- **Slow** (`-m slow tests/test_quit_bounded.py`): 1 passed in 168 s.
- **Bug Spray** (`agents/bug_spray`, its own `.venv`): 52 passed.
- Sentry's 25 tests are collected by the main run (`pytest.ini:2`); run alone they also pass (25 in 0.2 s).

No test reached the network, root, pf or a real tunnel (the suite's isolation fixtures forbid it). Read the
counts as unit and contract coverage only: the VPN, kill switch, DNS-leak, Mullvad, LM Studio, SFTP and
public-source paths have no live coverage (§7).

## 7. Checks only the owner can run (real hardware, real accounts)

Run after B1-B3 are fixed and the venv is rebuilt. None should run on a machine whose network matters.

- **O1.** Launch with Little Snitch (or `nettop`) and every allow box unticked, in Local only: confirm no
  connection to any cloud model API (B1), and note any to huggingface.co or raw.githubusercontent.com (H4, M9).
- **O2.** The seven Tunnel items under "Before testing" (TODO.md:63-69), with M2's corrected paths for the
  OpenVPN device check (`sudo ls -l /private/var/run/sentinel-vpn/ovpn/`).
- **O3.** Kill switch DNS: with a WireGuard tunnel up and the switch armed, drop the tunnel (stop the server
  or pull its route) and run `dig example.com` and Tunnel's **Run test**. Expect blocked; with the home router
  as resolver, H1 predicts an answer. Repeat with an OpenVPN tunnel up (H2) and with an IPv4-only WireGuard
  config, checking `curl -6 https://ifconfig.co` with the switch disarmed (M3).
- **O4.** S2, S3 and S7 of the privileged-layer checklist (`docs/specs/audit-tier2-privileged-layer.md` §9)
  and the S8 checklist (spec §15), as TODO.md:64-66 list them.
- **O5.** Mullvad through the VPN Apps tab (testing-phase blocker per CLAUDE.md), TODO.md:68.
- **O6.** Throwaway VPS deploy, Tor and MAC changer on hardware (manual cases §15-16, TODO.md:70), plus the
  macOS private-Wi-Fi-address question (TODO.md:72).
- **O7.** Once, in a throwaway VM as root, run the two skipped installer-swap tests (L3).
- **O8.** Routing advisor against a real LM Studio model on every agent (TODO.md:62).
- **O9.** Sentry background watch from the checkout: Install, wait one tick, then `launchctl print
  gui/$UID/com.sentinel.sentry.watch` and `watch.log` (whether launchd may run Python from `~/Documents`
  without a Files and Folders grant is unconfirmed).
- **O10.** Bloodhound: open one JPEG and one HEIC on the rebuilt venv (Pillow 13; TODO.md:73).

## 8. Must fix before testing

1. B1: cache-only model lists for cloud providers in `models_for_provider`.
2. B2: rebuild `.venv` from `requirements*.txt`; correct the paramiko line in app-context.
3. B3: merge PR #52.
4. Decide H1-H4 (code change or reworded promise) before O1 and O3, so the testers know what "pass" means.
5. Correct M1 and M2 so the README and the owner checklist describe the code.

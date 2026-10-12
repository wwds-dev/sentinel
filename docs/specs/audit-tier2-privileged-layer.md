# Spec: audit tier-2 fixes, the privileged layer (2026-10-12)

Source: security P1-2, P2-1, P2-2; code-review P1-1, P1-2, P1-3, P1-4 (HQ `audits/sentinel/`). Design only; no code changed.
Prerequisite state: tier-1 (`docs/specs/audit-tier1-fixes.md`) is separate and untouched.
Inputs not available: `docs/company/app-context.md` does not exist in this worktree (AGENTS.md names it; not found under `lab/` either), so hot spots were taken from the audits and the code. Architect playbook is `status: baseline` (last-reviewed 2026-10-10, 2 days old): its practice section is empty, so this design rests on the code and the audits.
"UNVERIFIED" = not provable from the code; each is also in the owner checklist (section 9).

## 0. Principles
- One Sentinel gated action = one admin prompt = one root script. The script re-verifies, from inside root, that what it runs is what the user confirmed.
- The bytes the user reviewed are hashed in-process; that hash travels with the review object to the worker and into the root script (same trick as `agents/vpn_agent/server/deploy.py:_run_local`, which copies to a root-owned `mktemp` file, checks SHA-256, runs, removes).
- The root script never trusts a path in a user-writable directory after the hash check; everything root writes goes to a root-owned directory.
- Honest status over reassuring status: unknown is shown as unknown.

## 1. (A) Root runs user-writable configs (security P1-2, code-review P1-4, security P2-1)

### 1.1 Approach
1. `ExecutionReview` gains `config_sha256: str = ""` and `includes: tuple[tuple[str, str], ...] = ()` (path, sha256), and a `fingerprint()` over action, protocol, target, interface, command, full `root_hooks`, `config_sha256`, `includes`. Warnings/blockers are excluded (probe state may legitimately change). Defaults keep the frozen dataclass constructible by existing tests.
2. `review_execution` reads the config bytes once (size cap 256 KiB, regular file, `lstat` not a symlink else warning), hashes those bytes and runs the hook scan and `inspect_config_summary` on that same in-memory text, so hash and review describe the same bytes. (`inspect_wireguard_config` takes a path today, `vpn_execution.py:305`; add a text entry point.)
3. `_review_and_confirm` (`ui/panels/vpn.py:~721`) returns the confirmed `ExecutionReview` (or None). `VpnConnectionWorker(action, profile, confirmed)` (`ui/workers.py:~395`) hands it to `vpn_execution.execute(..., confirmed=review)`. Both `connect_vpn` and `disconnect_vpn` pass it.
4. `execute()`: re-review, then `if confirmed is None or fresh.fingerprint() != confirmed.fingerprint(): abort`, `ran=False`, error "The config, command or target changed since you confirmed it; nothing was run. Review again." No admin prompt. `confirmed=None` fails closed (chat/other callers: only the worker calls `execute`, grep-confirmed within `ui services agents main.py`; UNVERIFIED for `agents/vpn_agent/sentinel_chat_agent.py` paths beyond a grep).
5. The runner receives the fresh review's `config_sha256` (equal to the confirmed one) and builds the root script (section 1.2). Because root re-hashes against that value, a swap after step 2 is also caught at the last moment.

### 1.2 Who copies, and how (WireGuard)
New pure module `services/vpn_rootscript.py` builds shell text; `vpn_connection.connect/disconnect` call it instead of `wg_quick_command(target)`. Connect script, all inside the single osascript prompt:
`umask 077`; create `/var/run/sentinel-vpn` and `wg/<iface>` as `root:wheel 0700` (`install -d`; refuse if the path is a symlink or not root-owned); copy with `/bin/cat -- SRC > "$d/<iface>.conf"`; `shasum -a 256` of the COPY must equal H else `rm -rf "$d"`, `exit 98`; `wg-quick up "$d/<iface>.conf"`; on non-zero remove `$d`. `<iface>` is already constrained by `WG_INTERFACE_NAME` (`vpn_execution.py:44`) so it is safe in a path. The copy keeps the `<iface>.conf` name, so wg-quick names the interface as before and `/var/run/wireguard/<iface>.name` probes are unchanged.
- Disconnect: wg-quick down re-reads the config and runs PreDown/PostDown, so the copy is KEPT while the tunnel is up. `wg-quick down "$d/<iface>.conf"`, then `rm -rf "$d"`. Those hooks were the ones the user confirmed at connect. If no stored copy exists (tunnel started by an older Sentinel, by hand, or after reboot) disconnect uses the same copy-hash flow on the user file with a fresh review whose hook list includes PreDown/PostDown.
- Bare interface names (`/etc/wireguard`, `/opt/homebrew/etc/wireguard`; the latter is user-owned on Apple silicon, UNVERIFIED): Sentinel resolves the concrete file (the first dir in `WG_CONFIG_DIRS` that has it; wg-quick's own search order is UNVERIFIED) and treats it as a path, so the same flow applies.
- `SaveConfig = true` makes wg-quick rewrite the config on down. With the copy, it rewrites the root copy and is discarded; add a warning line saying so.
- As root: if `os.geteuid() == 0` the existing branch in `privileged.run_as_root` (`bash -c`) works unchanged.

### 1.3 OpenVPN
- Hook set: keep `OPENVPN_HOOK_KEYS` (it already lists `learn-address`), plus new BLOCKERS (not warnings): `config` (nested include hides hooks), `management*` (local control socket), `log`, `log-append`, `writepid`, `status`, `tmp-dir`, `cd`, `chroot`, `setenv` (not `setenv-safe`), `daemon`, `syslog`. `--` prefixed and case variants are normalised (the scanner already `lstrip("-")`). Unknown directives are listed as one warning ("not on the reviewed list"), not blocked, to avoid breaking real profiles (owner decision D4).
- Data references: `ca cert key dh tls-auth tls-crypt tls-crypt-v2 pkcs12 crl-verify extra-certs auth-user-pass <file> askpass secret http-proxy-user-pass`. Inline `<ca>`/`<key>` blocks live in the hashed config and need nothing. File refs go into the `includes` manifest (sha256, regular file, no symlink, <= 1 MiB, <= 16 files, no `..`). Relative refs are copied into the root run dir keeping their relative layout and openvpn is started with `--cd <rundir>`. Absolute refs are NOT copied; root verifies each file's hash immediately before start. Residual: a data-only swap (ca/cert) between that check and openvpn's read; this is MITM-class (same-user malware can already edit the profile), not code execution. Documented, not fixed.
- Which cwd openvpn resolves relative refs against today is UNVERIFIED (osascript `do shell script` normally starts in `/`), so relative-ref profiles may already fail; the manifest makes them work, which is a behaviour change to note in the CHANGELOG.
- Executables (`up`, `down`, `route-up`, `tls-verify`, `plugin`, `auth-user-pass-verify`, `client-connect`, ...; WireGuard `PreUp/PostUp/...` whose first word is an absolute path): the review resolves the file; BLOCKER unless it is root-owned and not group/other-writable (Sentinel checks `os.stat` of file and parents at review; the root script re-checks with `/usr/bin/stat -f '%u %Sp'`). Inline one-line shell commands in WireGuard hooks are protected by the config hash.
- Cleanup: run dir `/var/run/sentinel-vpn/ovpn/` holds the config copy and data copies from connect until Disconnect (script: `kill -TERM <pid>; rm -rf` the dir) or failed connect (script removes it). Whether openvpn needs the files after daemonising (SIGUSR1 restart re-reads the files) is UNVERIFIED, so they are kept while up. Stale dirs with no tracked process are swept by the next connect script.

### 1.4 Hook text (security P2-1)
Drop `HOOK_VALUE_LIMIT` truncation in the model. A line over 4000 chars is a blocker. The confirm text shows the first 300 chars per hook and `QMessageBox.setDetailedText` carries all of it; `sections()` shows all. `config_root_hooks` also returns PreDown/PostDown for disconnect (remove the `action == "connect"` guard at `vpn_execution.py:299` and the OpenVPN equivalent; OpenVPN disconnect runs `down` hooks inside the daemon already confirmed at connect, so for OpenVPN disconnect only a note is added).

### 1.5 Alternatives rejected
- Refuse configs not root-owned/0600: breaks every normal user import (files live in `~/Downloads`); no.
- Import-time copy into a private Sentinel dir: still user-writable; does not close the hole; useful later for hygiene only.
- Pass config text through the osascript command: puts private keys in the process list (`privileged.py` header forbids it).
- Hash only, no copy (verify then run in place): re-opens the check/use race. Rejected.
- Compare only blockers (today): the audit's finding.

### 1.6 Failure modes
Hash mismatch: exit 98, nothing run, output "refused: config changed". `mktemp`/`install` failure: exit 97. Disk/dir not root-owned: abort. Dialog cancelled: unchanged ("Cancelled."). Timeout after approval: root may have started the tunnel; existing "may still have happened" text applies and the post-change probe decides. Crash between up and a kept copy: next disconnect falls back to the fallback flow. Reboot clears `/var/run` (UNVERIFIED on macOS) together with the tunnel, so no orphan.

### 1.7 Migration
No profile or state-file change: `config_path` keeps pointing at the user's file (`vpn_connection.profile_from_config`, `ui/panels/vpn.py:~660`). Old tunnels disconnect through the fallback. Profiles that used relative OpenVPN refs may change behaviour (see 1.3).

### 1.8 Tests
Unit (runner-boundary mocks already in `tests/test_vpn_execution.py`, `tests/test_privileged_runner.py`): fingerprint differs for added hook / changed command / changed target / changed hash and `execute` returns `ran=False` with the mock runner never called; `confirmed=None` fails closed; hooks scanned on disconnect (REPLACE `test_disconnect_review_does_not_rescan_hooks`, `tests/test_vpn_execution.py:371`, with its inverse and add PreDown); full-length hook kept; each blocker directive; script text contains the hash, `install -d`, and no user path after the hash check (string assertions). Shell-level: add `@pytest.mark.allow_process` tests that run the generated script under real `/bin/bash` in `tmp_path` with the base dir and the `-o root -g wheel` options injected as parameters (empty in tests) and a fake `wg-quick` on PATH: hash mismatch exits 98 and runs nothing; match runs the fake on the copy; failure removes the dir. This is NOT root-gated (unlike `tests/test_vpn_review_fixes.py:164`, skip unless euid==0, so the installer guard has never run for the owner; QA P2). Real wg-quick/openvpn/ownership checks: owner checklist.
Risk: hook gate Low; WireGuard copy Medium; OpenVPN manifest High (most behaviour change).

## 2. (B) Honest kill-switch status (code-review P1-1)
Approach. Replace the boolean with a state: `disarmed`, `recorded_armed` (marker present, anchor registered, marker newer than boot), `stale` (marker older than boot or anchor block gone), `verified_armed`, `verified_empty` (Verify found no rules).
- Marker becomes JSON `{"state":"armed","at":<epoch>,"boot":<kern.boottime sec>,"ifaces":[...]}` at `paths.state_dir()/killswitch.armed`. Legacy "armed\n" marker is read as `recorded_armed` with `at` = mtime and boot unknown, compared by mtime. No migration step.
- Boot time: `sysctl -n kern.boottime` (no root); parse `sec = N` defensively; failure = boot unknown, show "recorded as armed (boot time unknown)". Compare stored boot to current, fallback mtime < boot. A stale marker is reported as `stale` ("Not armed: the Mac restarted since arming") and removed by `reconcile()` (called at panel show and before the disconnect review), never by `status()` (stays read-only).
- Verify action (new button, explicit, never automatic since it costs a password): one privileged script `pfctl -s info; echo ---; pfctl -a vpn-agent-killswitch -s rules` via `privileged.run_as_root(..., allow_cached_sudo=True)` because it is read-only (owner decision D6: or dialog-only for consistency). Parse: "Status: Enabled" and non-empty anchor rules containing `block` -> `verified_armed`; rules empty -> `verified_empty` and offer to clear the marker; pf disabled with rules present -> "rules loaded but pf is off" (not armed). Output format of `pfctl -s rules` on this macOS is UNVERIFIED, so parse by "any rule line present", not exact text. Verified result is memory-only with a timestamp.
- UI wording: label never says ARMED unqualified. "Recorded as armed (not verified): Verify". The panel label today is static "Kill switch not armed." (`ui/panels/vpn.py:~131`) and ignores `status()`; initialise it from `status()` on show, which also fixes the false "not armed" after restart with live pf. `vpn_execution._killswitch_armed` (`:246`) treats `recorded_armed` and `verified_armed` as armed for the disconnect warning ("recorded as armed ... stays blocked"): fail-safe direction. Update `tests/test_contract_tunnel_chat.py:770` (`"ARMED" in label`).
- Alternatives rejected: a root helper/daemon querying pf (no installed helper; large attack surface); unprivileged `pfctl -s rules` (needs root, per the code comment at `killswitch.py:303`); auto-verify at startup (surprise password prompt); delete the marker on any doubt (hides a live block).
- Failure modes: boot-time parse failure (shown unknown); dialog cancelled (state unchanged); sleep/wake does not clear pf (not treated as stale); manual `pfctl -F all` leaves the marker, so status stays "recorded" until Verify (stated in the wording; not a regression).
- Tests: unit with tmp state dir and a mocked `sysctl`/`_run_privileged`: stale-by-boot, legacy marker, unknown boot, verify parsers on canned outputs (empty, rules, disabled). The real pfctl output format: owner checklist. Risk Low (status), Low-Medium (Verify).

## 3. (C) Quit during privileged work (code-review P1-2)
Approach, two layers:
1. Marker before script: in `killswitch.arm()` write the marker (state `arming`) BEFORE `_run_privileged`; roll back (unlink) only on a definitive failure (validation refused, "Cancelled.", non-timeout pfctl error); keep on timeout (already the rule, `killswitch.py:~375`). A thread lost mid-arm then leaves "recorded as armed" and Disarm available. Disarm keeps its current rule (marker removed only after success).
2. No `terminate()` on privileged workers: `VpnPanel.shutdown` (`ui/panels/vpn.py:~1014`), `ui/panels/vpn_gate.py:86` (shared gate shutdown, same pattern; whether it hosts Tor/MAC privileged calls: UNVERIFIED). New helper `ui/workers.py:detach_worker(worker)` generalising `ui/dialogs.py:813` (`_DETACHED_ALIAS_WORKERS` + `_discard_detached_worker`): disconnect signals, keep a module-level reference, drop it on `finished`. Because a QThread destroyed while running aborts at process exit, `MainWindow.closeEvent` (`main.py:5117`) first asks the VPN panel `has_privileged_work()`; if true it shows "An administrator action is still running. Wait / Quit anyway" (default Wait, bounded by `PASSWORD_ENTRY_SECONDS + timeout` = about 120 s). Quit anyway = detach + let the process end with `os._exit` after the other shutdown work (owner decision D5; if the owner prefers simplicity: Wait-only with a hard cap). Fix the `cancel()` docstring (`ui/workers.py:~410`) that claims shutdown waits.
Alternatives rejected: keep terminate with a longer wait (still kills mid-subprocess); pure detach without the quit prompt (Qt abort at exit, UNVERIFIED); a separate privileged helper process (too large for tier 2).
Failure modes: user force-quits during the dialog: root command may finish later with no UI (documented by the existing timeout text); Emergency Reset calls `shutdown` too, so it must also honour Wait (it erases data under a running worker).
Tests: unit: fake worker whose `wait` returns False is parked, never `terminate`d (monkeypatch `terminate` to raise); `arm` writes marker before the runner is called (assert ordering with the mock runner) and removes it on "Cancelled."; update `tests/test_vpn_worker_shutdown.py`. The Qt exit behaviour and a real quit with the admin dialog open: owner checklist. Risk Medium (quit path, Qt lifetime).

## 4. (D) Sentry background watch in packaged builds (code-review P1-3)
Facts: `build_plist` uses `sys.executable` + `agents/sentry/main.py` (`watchd.py:42-67`); `Sentinel.spec` bundles no sentry `main.py` and the frozen `main.py` has no `--headless` handling (grep of `main.py` found no argv handling). `sys.frozen` is used elsewhere (`services/runtime_paths.py:35`).
Decision: refuse, do not ship a headless entry point in tier 2. `watchd.install()` returns `{"ok": False, "message": "The background watch is not available in the packaged app; run Sentinel from the checkout (developer mode)."}` when `getattr(sys, "frozen", False)`; panel greys the Install button and says why. Shipping a headless entry is a feature (entry point, spec datas, launchd with the bundle path surviving app moves/updates) and goes to the backlog.
Verification: after `launchctl load` (exit code is not proof), run `launchctl print gui/$(id -u)/com.sentinel.sentry.watch` and require `state` present and no "last exit code" failure; report "installed, loaded, last run: ok/failed/not yet run". `is_loaded` (`launchctl list`) stays as the cheap check. `launchctl print` output format is UNVERIFIED across macOS versions, so parse loosely and fall back to the old message text with "not verified".
Also: a frozen build with an already-written plist (previous version) is reported as "installed but cannot run: remove it", `remove()` stays available.
Alternatives rejected: build a headless entry now (scope), silently pick `.venv` (the existing `_python_executable` already does when a checkout venv exists; keep this for the checkout case).
Tests: unit: monkeypatch `sys.frozen`, assert refusal and that `launchctl` is never called; mock `subprocess.run` for `print` parsing; plist test at `agents/sentry/tests/test_engine.py:370` unchanged. Real launchd run: owner checklist. Risk Low.

## 5. (E) Root writes into user-controlled directories (security P2-2)
- pf rules: today Python writes `state_dir/killswitch.pf` (user-owned) and root runs `pfctl -a ... -f <that path>` after `validate` (`killswitch.py:256-263,364-367`). Change: Sentinel keeps the file for recovery/reading (documented purpose) but the root script loads the VALIDATED TEXT, not the path: `printf '%s' '<rules>' | pfctl -a vpn-agent-killswitch -f -`. Rules contain no secrets and endpoints come through `ipaddress`/`getaddrinfo`, interface names are regex-checked; the text is quoted by `_q_str`. Fallback if `pfctl -f -` is unsupported (UNVERIFIED): copy to a root-owned `mktemp`, check SHA-256 of the exact validated text, load, remove (the deploy pattern). `validate()` is then run on the same text bytes (hash computed from them).
- OpenVPN pid/log/device: root-owned run dir `/var/run/sentinel-vpn/` (`root:wheel 0755`, files `0644`, config copies in a `0700` subdir as in 1.3). `--log`/`--writepid` point there; the connect script itself waits (up to 15 s) for the "Opened utun device" line, writes only the device name to `device` and deletes the log, replacing `_settle_log`'s user-side deletion (the user cannot unlink in a root-owned dir). Sentinel only READS pid/device. `_is_tracked_process` (`openvpn_manager.py:229-258`) must match the new `--writepid` path; during the migration window `is_running()` also checks the legacy `<data>/vpn/openvpn.pid` with the legacy command match, so a tunnel started by an older Sentinel can still be seen and disconnected. A root-owned pid file also stops same-user spoofing of the PID that `kill -TERM` receives (identity check remains).
- User dirs: `_run_dir()` (`openvpn_manager.py:67-70`) uses `ensure_private_dir` (0700) for what stays user-side.
- Alternatives rejected: `O_NOFOLLOW` in Python (root, not Python, does the write; irrelevant); symlink checks in the shell (racy); `/tmp` (world-writable, sticky games).
- Failure modes: `/var/run` not writable or pre-existing non-root dir/symlink: script aborts 97 ("refusing"); unknown pf stdin behaviour: fallback; log wait expires: device unknown (existing behaviour: arming OpenVPN is refused with a message).
- Tests: script-string unit tests, the same bash-in-`tmp_path` harness as 1.8, legacy pid detection test with `tmp_path`. Risk Medium-High for the OpenVPN part (pid-identity coupling), Medium for pf.

## 6. Implementation order (each step ships and reviews on its own)
1. S1 Review gate (A, no privilege change): full hook text, OpenVPN blockers, disconnect hook scan, `fingerprint`/`confirmed` abort. Replaces the pinned test. Unit tests only. Risk Low.
2. S2 Honest status + marker-before-arm (B status part, C layer 1): boot-time check, JSON marker, wording, `reconcile`. Unit tests. Risk Low.
3. S3 Sentry frozen refusal + `launchctl print` (D). Needs owner check on a packaged build. Risk Low.
4. S4 Quit handling (C layer 2): `detach_worker`, quit prompt, docstring. Needs owner GUI check. Risk Medium.
5. S5 Kill-switch Verify (B verify part). Needs owner sudo/pf check. Risk Low-Medium.
6. S6 pf rules via stdin / hashed copy (E part 1). Needs owner pf check. Risk Medium.
7. S7 WireGuard root copy + hash (A part 1, 1.2) with `vpn_rootscript.py` and the bash harness. Needs owner wg-quick check. Risk Medium.
8. S8 OpenVPN run dir, manifest, pid/log relocation (A part 3 + E part 2). Needs owner openvpn check. Risk High; may be split into S8a (run dir/pid/log) and S8b (config copy + manifest).
Hard dependency only: S7 and S8b need S1 (confirmed review carries the hash). Everything else is independent. A candidate ADR for the owner's gate: "root runs only verified copies under `/var/run/sentinel-vpn`" (layout is expensive to reverse; not written here because this task allowed one file).

## 7. Non-goals
Notarised helper tool / SMJobBless / launchd root daemon (the real fix for same-user to root, and out of scope); Developer ID signing (security P2-4); import-time private copy of profiles; kill-switch auto re-arm on reconnect (code-review P2-5); `agents/bug_spray`; the live `active/sentinel` tree.

## 8. Cross-cutting test notes
`tests/conftest.py` blocks `Popen`/`os.system` unless `@pytest.mark.allow_process`; the new bash harness uses that marker, runs only `/bin/bash` and a stub in `tmp_path`, and must set `VPN_AGENT_STATE_DIR`/data base to tmp so the real state dir is untouched. Root-gated tests: none required; anything that needs real root is in the checklist below, documented in `docs/` as a manual test with the exact command and expected output, results recorded by the owner in the PR, never in CI.

## 9. Owner checklist (real macOS; the agents cannot run these)
Use a throwaway profile and a VPN you can lose; keep Terminal open with `sudo pfctl -a vpn-agent-killswitch -F all && sudo pfctl -F all -f /etc/pf.conf` ready.
- S2: arm, reboot, open Sentinel: shows "not armed: Mac restarted" (and `pfctl -s rules` is empty). Arm, run the recovery command by hand: label says "recorded as armed".
- S3: in the packaged app, Sentry Install shows the refusal and writes no plist; from the checkout, Install then `launchctl print gui/$(id -u)/com.sentinel.sentry.watch` shows the job and the panel text matches.
- S4: click Arm, leave the admin dialog open, quit: you get the Wait/Quit prompt; Wait, enter the password, quit completes, status matches `pfctl`. Repeat with Quit anyway (no crash report in Console, marker present, Disarm offered). Same for Connect.
- S5: Verify with pf armed, after manual flush, with pf disabled (`sudo pfctl -d`): three different texts; paste the real `pfctl -s info` and `-s rules` output into the PR so the parser can be checked.
- S6: Arm with the stdin loader (and the fallback if stdin fails): `sudo pfctl -a vpn-agent-killswitch -s rules` shows the expected rules; swap `killswitch.pf` during the dialog and confirm the loaded rules are unchanged.
- S7: Connect a WireGuard profile: `ls -ld /var/run/sentinel-vpn/wg/*` is root:wheel 0700, interface name unchanged, `wg show` ok; edit the .conf (add a PostUp) while the dialog is open: refused with exit 98 text, no tunnel; Disconnect uses the stored copy and the dir disappears; test a tunnel started by the old build.
- S8: Connect an OpenVPN profile with inline certs, then one with relative `ca`/`key` files and one with an `up` script; verify pid/device files, kill switch arm with the OpenVPN device, Disconnect, no files left in `/var/run/sentinel-vpn`, and the legacy-pid case with a tunnel started by the previous build.
- Every step: Cancel the dialog once and let it time out once; confirm the messages are truthful.

## 10. Decisions wanted from the owner
- D1: OK to make hook-executable files that are user-writable a hard blocker (1.3)?
- D2: Accept the documented residual (absolute-path OpenVPN data files verified, not copied)?
- D3: `config`/`management`/`log`/`setenv` as blockers: yes? (could break unusual profiles)
- D4: Unknown OpenVPN directives: warn (proposed) or block?
- D5: Quit with a running admin action: Wait-only, or Wait plus "Quit anyway" with `os._exit`?
- D6: Verify may use a cached sudo ticket (read-only), or always the dialog?
- D7: Sentry in packaged builds: refuse now (proposed) and ship a headless entry later?
- D8: Write the ADR for the `/var/run/sentinel-vpn` layout before S7?

## 11. UNVERIFIED (consolidated)
Real behaviour of wg-quick with a copy under `/var/run`, the wg-quick bare-name search order, whether `/opt/homebrew/etc/wireguard` is user-owned, `/var/run` cleanup on reboot, osascript's cwd, whether openvpn re-reads files after daemonising, `pfctl -f -` on stdin, `pfctl -s rules`/`-s info` text, `sysctl kern.boottime` format, `launchctl print` format, Qt abort when a running QThread outlives the app, whether `vpn_gate.py` hosts privileged workers, other `execute()` callers in the companion chat agent.

## 12. Steps S1-S3 built (2026-10-12, branch `fixes/audit-tier2-2026-10-12`, three commits)

Owner decisions applied: D1 (user-writable hook programs block), D2 (relevant at S8), D3 (blockers `config`, `management*`, `log`, `log-append`, `writepid`, `status`, `tmp-dir`, `cd`, `chroot`, `setenv`), D4 (unknown OpenVPN directives warn once), D7 (refuse the background watch when frozen). D5, D6, D8 and S4-S8 are not built.

### S1 review gate (`services/vpn_execution.py`, `ui/workers.py`, `ui/panels/vpn.py`)
- `ExecutionReview.config_sha256` and `fingerprint()` (action, protocol, target, interface, command, full hooks, hash; warnings and blockers excluded). The config is read once (256 KiB cap, symlink = warning), so hash, hook scan and inspection describe the same bytes; `inspect_wireguard_text()` is the new in-memory entry point in the companion `config_inspection.py`.
- `_review_and_confirm` returns the confirmed review or None; `VpnConnectionWorker(action, profile, confirmed)`; `execute(..., confirmed=)` re-reviews and aborts with no admin prompt on any fingerprint difference ("changed since you confirmed it") and fails closed when `confirmed` is None. The abort is shown as a blocker in the outcome.
- Hooks keep their full text; the dialog body shows 300 characters per hook with "N more characters"; when any hook is shortened the dialog is a real `QMessageBox` with `setDetailedText` carrying all hooks and the hash (otherwise the static `question`/`warning` calls are kept). A hook line over 4000 characters is a blocker.
- Disconnect now scans PreDown/PostDown (WireGuard) and hashes the file. `test_disconnect_review_does_not_rescan_hooks` pinned the old gap and was replaced by its inverse (`test_disconnect_review_scans_predown_and_postdown`, `tests/test_vpn_review_gate.py`), as the owner approved. OpenVPN disconnect only gets a note.
- Hook programs: an absolute first word of a WireGuard hook or an OpenVPN executable directive (`up down route-up route-pre-down ipchange client-connect client-disconnect learn-address tls-verify auth-user-pass-verify plugin`) is a blocker unless root owns it and every directory above it, none group/other-writable. Bare commands and inline shell are not path-checked (the config hash covers them). A missing program is a warning.
- OpenVPN inline blocks (`<ca>...</ca>`) are skipped by the scanner (data, not directives).

### S2 honest kill-switch status (`agents/vpn_agent/services/killswitch.py`, `services/vpn_connection.py`, panel)
- `KillSwitchState.state` in `disarmed | recorded_armed | stale | verified_armed | verified_empty` (the last two only defined, for S5); `armed` stays a bool (recorded or verified). Marker is JSON `{state, at, boot, ifaces}`; the old `armed\n` marker reads by mtime. Stale = marker older than the boot (5 s tolerance; `sysctl -n kern.boottime`), or the pf.conf anchor block gone. Unknown boot time is shown as "boot time unknown". `reconcile()` removes stale markers (panel show, and `vpn_execution._killswitch_armed` before a disconnect review); `status()` stays read-only.
- `arm()` writes the marker (state `arming`) before the privileged script, then `armed`. Definitive failure: the previous marker is restored (or removed if there was none); timeout: kept as armed (as before).
- The panel label is initialised from `vpn_connection.killswitch_status_text()` (falls back to the old text when unavailable) and refreshed on show; the arm result now reads "Kill switch recorded as armed (not verified; ...)". No Verify button.

### S3 Sentry (`agents/sentry/sentry/watchd.py`, `ui/panels/sentry.py`)
- `install()` returns `{"ok": False, "verified": False, "message": ...}` when `sys.frozen` is set, before writing a plist or calling launchctl. Otherwise `launchctl print gui/<uid>/com.sentinel.sentry.watch` decides: no job = "not loaded", no parsable state = "not verified", nonzero last exit code = failed, else loaded with "last run: ok / not yet run". Results carry `ok` and `verified`.
- Panel: Install is disabled in a packaged build with the reason; a leftover plist there reads "cannot run ... press Remove".

### Deviations from the design
- `ExecutionReview.includes` (OpenVPN data-file manifest) is not added: nothing uses it before S8.
- The OpenVPN blockers `daemon` and `syslog` from section 1.3 are not included; the owner's D3 list did not name them.
- The runner is not yet handed the hash and root still reads the user's file: the hash check closes the dialog-to-worker gap in-process only. The check-to-root-read race stays open until S7 (root copy + hash), as the spec's order intends.
- Dialog detail uses a real `QMessageBox` only when a hook is shortened, so the static-call path (and its tests) is unchanged for ordinary configs.
- Existing `execute()` callers in tests now pass the review they would have confirmed, because `execute()` fails closed; no assertion was changed. The contract test that asserted `"ARMED"` in the label now asserts the "recorded as armed (not verified" wording (the spec called for this).
- `docs/company/app-context.md` does not exist in this worktree and the engineer playbook is `status: baseline` (last-reviewed 2026-10-10), so no app-specific bar beyond AGENTS.md and the spec was available.

### What the owner must verify on a real Mac (spec section 9)
- S1: Connect a WireGuard profile with a `PostUp` hook (dialog shows the hook, the second prompt appears); add a `PostUp` after the dialog opens and confirm "changed since you confirmed it" with no password prompt; a hook that names a script in your home directory is refused; a hook longer than 300 characters shows the "details" pane with the full text; Disconnect of a config with `PreDown` now shows it. OpenVPN profile with `config`/`log`/`up` in a user directory is refused; one with unknown directives still connects with one warning. (Real wg-quick/openvpn behaviour was not run.)
- S2: arm, reboot, open Sentinel: "Not armed: the Mac restarted since arming" (and `pfctl -s rules` empty); arm, run the recovery command by hand: label still says "recorded as armed (not verified)". Check that `sysctl -n kern.boottime` prints `{ sec = N, usec = M } ...`; with an armed switch quit during the admin dialog and confirm the marker exists afterwards (full quit handling is S4).
- S3: in the packaged app Install shows the refusal and writes no plist; from the checkout Install, then `launchctl print gui/$(id -u)/com.sentinel.sentry.watch` shows the job and the panel text matches (including the real `state =` / `last exit code =` lines, whose format is UNVERIFIED).

### Round 2 (2026-10-13): fixes after the independent code and security review of S1-S3

What changed
- OpenVPN scanner: lines split on `"\n"` only and tokenised the way OpenVPN does (quotes, backslashes, `#`/`;` starting a token). A directive name written with a quote or backslash, an unbalanced quote, an unterminated inline block, or a block whose end is not exactly `</tag>` (a line starting `</` that is anything else, an indented close tag) is a BLOCKER ("ambiguous"): OpenVPN may close such a block early, so the scanner does not guess. CRLF is handled; Unicode line separators do not split lines.
- Hook programs: every absolute or `~` path word in a hook is checked, in every `;`/`&&`/`||`/`|`/`&` segment, after leading `VAR=` assignments. The command word, and every path handed to an interpreter or wrapper (sh, bash, zsh, python*, perl, ruby, node, osascript, env, sudo, nohup, ...), must be root-owned in root-owned, non-group/other-writable directories, else BLOCKER. A user-writable data argument of a root-owned program is a warning. `~` paths are never accepted.
- Interface-only WireGuard profiles: the `.conf` is resolved from `/etc/wireguard`, `/opt/homebrew/etc/wireguard`, `/usr/local/etc/wireguard` (first existing) and hashed and scanned like an imported one; not found or unreadable on Connect is a blocker ("import the .conf"). `SaveConfig = true` (any case) is a blocker.
- Disconnect never strands the user: on Disconnect the unreadable/oversize/non-UTF-8 config and every hook finding (user-writable programs, over-long line, SaveConfig) become `risks`, shown in the dialog and requiring a second, default-No "Disconnect anyway?" confirmation. `risks` are part of the fingerprint, so a mismatch between the confirmed review and `execute()` still aborts with no prompt. Connect is unchanged (they block).
- Kill switch: a marker is stale only when written before this boot (5 s tolerance) or its anchor block is gone (the "stored boot differs by > 60 s" clause is removed). `reconcile()` returns the reason and refuses to delete a marker whose bytes changed since it was read; the panel shows "Not armed: the Mac restarted since arming" before the stale marker disappears. `sysctl` is called as `/usr/sbin/sysctl` and cached for the process; the state directory is created private (0700); an `arming` marker reads "Arming, unconfirmed" and claims no protection (it still counts as armed for the disconnect warning, fail-safe).
- Sentry: exit codes parse with `-?\d+` (negative or suffixed codes are failures); `state =` and `last exit code =` are read from the job's own block only; `status()` carries `last_run` / `last_exit_code` and the panel says "loaded, but its last run failed" instead of "running". The `launchctl print` call still runs on the UI thread when the panel refreshes (there is no worker for it); this is deferred.
- Honesty: the hash is verified when the password prompt opens, not when root reads the file; the dialog line, the module docstring and `execute()` say so. The audit finding about the check-to-use race is NOT fixed by S1; only S7 closes it.
- `wireguard_manager.connect/disconnect/restart` (an ungated `sudo wg-quick`, no callers) now raise `RuntimeError` pointing to `services.vpn_execution.execute`.

Limits that remain
- Script contents are outside the config hash. Only programs owned by root in root-owned directories are accepted; a root-owned script that itself reads user-writable files is not analysed.
- Root still reads the user's config after the prompt opens (S7 root-owned verified copy). OpenVPN data files, run dir and pid/log relocation (S8) and the pf stdin loader (S6), quit handling (S4) and Verify (S5) are not built. `launchctl print` and `sysctl kern.boottime` formats remain UNVERIFIED on real macOS.
- Interface-only profiles: the file the review hashes is the first existing one in our search order; wg-quick's own order is UNVERIFIED.

Corrected owner checklist
- S1: a profile with a quoted or escaped directive name (`"up" /x`) is refused with "quote or backslash"; a profile whose inline block ends with `</ca>x` is refused as ambiguous; a hook `PostUp = sh ~/x.sh` or `env FOO=1 /Users/you/x.sh` is refused; an interface-only profile with a `PostUp` in `/etc/wireguard/<name>.conf` shows the hook; with SaveConfig = true it is refused. Disconnect a tunnel whose config has a user-owned `PostDown` script, with the kill switch armed: the dialog lists the risk and asks "Disconnect anyway?" (default No); confirm and the tunnel comes down. Same for a config you made unreadable (`chmod 000`).
- S2: arm, reboot, open Sentinel: the label reads "Not armed: the Mac restarted since arming" and the marker file is gone (before this round the text was unreachable). Quit during the admin dialog, reopen: "Arming, unconfirmed" with Disarm available. Check `sysctl -n kern.boottime` output shape.
- S3: as before, plus force a failing run (break the plist's Python path) and confirm the panel says the last run failed rather than running.

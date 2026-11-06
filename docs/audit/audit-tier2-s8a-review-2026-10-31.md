# Independent review — audit tier-2 S8a (OpenVPN pid/device/log in root's run dir)

**Scope:** uncommitted diff vs HEAD `c1c5178` in worktree `intelligent-archimedes-e15a0e` — `services/openvpn_manager.py`, `services/vpn_rootscript.py`, `services/vpn_connection.py`, `services/vpn_execution.py` and the matching tests. S8b (config copy / includes manifest) out of scope by instruction.
**Reviewer:** code-reviewer, read-only. No files edited, nothing run as root.
**Playbook:** `docs/playbooks/code-reviewer.md` is `status: current`, `last-reviewed: 2026-10-14` (17 days; not stale).
**App context:** `docs/company/app-context.md` does not exist in this worktree (commit c4ac6a3 deliberately untracked it); I read the copy in the main checkout (`/Users/as/Documents/lab/active/sentinel/docs/company/app-context.md`).
**Moving tree:** while reviewing, new uncommitted edits appeared in `CHANGELOG.md`, `TODO.md`, `docs/agents/vpn.md`, `docs/company/activity.jsonl` (not in the snapshot I was given — another session is editing this worktree). I checked those only for claims the code does not support (P3-6).

*(Author's note: those edits were the author's own S8a doc updates, made while the review ran.)*

## Verdict

**No P0 or P1. One P2, seven P3s.** S8a meets its main goal: a same-user process can no longer make root signal anything other than the openvpn Sentinel started *through Sentinel's own pid file* — that file is in a root-only directory, Python believes it only as a root-owned regular file, and root re-checks both the pid against its own pid file and the process identity before `kill -TERM`. The bash is correctly quoted, glob characters in paths are matched literally, and every value arrives as argv after double validation. The P2 is a privacy regression rather than a privilege one: the legacy user-side OpenVPN log is now never deleted. The P3s are a harness gap (the device-wait loop is never exercised; a mutant removing it survives), a few legacy-path and dialog-output rough edges, and one doc claim the code does not back.

## Findings

### P2-1 — Legacy OpenVPN log is now never deleted (privacy regression)
`services/openvpn_manager.py:173-209` (`connect`), `:262-272` (`disconnect`), `:145-170` (`tunnel_device`); removed code: `_forget(log)` at the top of the old `connect` (`git diff HEAD -- services/openvpn_manager.py`).
Failure scenario: before c26c656 (2026-10-11) Sentinel kept the full OpenVPN log in `<data>/vpn/openvpn.log` and only deleted it at the next Connect (`git show b04bdad:services/openvpn_manager.py`, lines 114-117); from c26c656 until now a crash/Quit during the ~15 s settle window leaves the same file. That log contains the server address and pushed routes (`TLS: Initial packet from [AF_INET]203.0.113.7:1194`, etc.). Before S8a, every Connect deleted it. After S8a nothing ever does: `connect` no longer touches the legacy folder, a legacy `disconnect` only removes `openvpn.device`, and `tunnel_device` even reads the log. A user who used OpenVPN on a pre-2026-10-11 build keeps a connection log with IPs in their data folder (Portable mode: on a USB drive) indefinitely — against "no traffic or IP logging beyond local diagnostics", and a behaviour that used to self-heal.
Suggested fix: in `connect` (before running root) and in `is_running`/`_tracked` once the legacy pid is not tracked, `_forget` `legacy_log_file()`, `legacy_device_file()` and `legacy_pid_file()` (user-owned folder, so the user can unlink even the root-created pid file). After a legacy Disconnect that observed exit (see P3-3), delete all three. Add a test: legacy log present, no legacy process tracked, `connect(...)` → log gone. Adjust the module docstring and `docs/agents/vpn.md` ("read only", "nothing writes that folder any more") — deleting is the one write that should happen.

### P3-1 — Device-wait loop is untested; a mutant removing it survives
`services/vpn_rootscript.py` OVPN_SCRIPT up branch (the `while :; do … [ "$i" -lt "$tries" ] || break; /bin/sleep 0.5; done` loop); test stub `tests/test_vpn_rootscript.py` `_OVPN_STUB`.
Failure scenario: the stub writes the device line into the log *before* returning, so the first `grep` always finds it. Real OpenVPN writes "Opened utun device" seconds after `--daemon` returns, so the loop is the whole feature. I replaced `[ "$i" -lt "$tries" ] || break` with `break` (one poll, no waiting) at runtime and all 50 `-k ovpn` tests still passed. A regression that stops waiting would ship green and every real Connect would report no device → kill switch cannot be armed for OpenVPN.
Suggested fix: add a stub mode `late` that writes the line from a background subshell after ~1 s; assert `tries=6` records the device and `tries=1` prints the start-up tail with no `device` file.

### P3-2 — Legacy branch hands a user-writable pid to root, indefinitely
`services/openvpn_manager.py:117-133` (`_read_legacy_pid`, `_tracked`); root script Down legacy branch (`pf=$legacy`).
Failure scenario: in legacy mode root's only check is the `ps` substring match, and `ps` prints argv unquoted, so any process (any owner) whose argv[0] basename is `openvpn` and whose command line contains ` --daemon sentinel-ovpn ` and ` --writepid <legacy path> ` anywhere — e.g. a root-run openvpn from another client whose `--config` path text the user chooses to contain those strings — passes when a same-user process writes its pid into `<data>/vpn/openvpn.pid`. Disconnect then shows it as Sentinel's tunnel and root SIGTERMs it after the user approves. Low impact (contrived argv, user approves the dialog, user could stop that VPN anyway), but the branch is permanent: nothing retires it although a pre-S8a tunnel cannot survive a reboot.
Suggested fix: ignore the legacy pid file when its mtime is older than `kern.boottime` (so the branch goes dead after the first restart — keeps migration, closes the path), and plan removal in a later release. (A uid==0 check is *not* a safe extra: a config with `user nobody` makes openvpn drop root.)

### P3-3 — Legacy Disconnect forgets the device even when OpenVPN is still running
`services/openvpn_manager.py:268-271`.
Failure scenario: root's Down exits 0 both for "OpenVPN stopped." and "Signal sent; OpenVPN is still running."; in legacy mode Python then `_forget(legacy_device_file())` regardless. If the old tunnel stays up, `tunnel_device()` falls back to the legacy log (usually already gone) and returns `None`, so arming the kill switch is refused with "did not learn its tunnel device… Reconnect" — misleading. Fails closed, but inconsistent with the non-legacy path, which keeps `device` in that case (`[ -n "$legacy" ] || forget` only after exit).
Suggested fix: only forget the legacy device when `not _is_tracked_process(pid, legacy)` after the call (or give "still running" its own exit code/marker).

### P3-4 — Under the macOS dialog, a stderr warning is lost on success and the log tail arrives with CR line breaks
`services/vpn_rootscript.py:220` and `:224-225`; `agents/vpn_agent/services/privileged.py` `run_with_dialog`.
Measured: `osascript -e 'do shell script "echo out1; echo out2; echo err >&2"' | od -c` → `o u t 1 \r o u t 2 \n` (stderr dropped on success; newlines become `\r`); on exit 3 the error is `0:64: execution error: err1\rerr2 (3)`.
Failure scenarios: (a) if the device line is seen but writing `device.new` fails, root prints "Tunnel: could not record the tunnel device." to stderr and exits 0 — in production (dialog path) the user sees plain success, no device and no reason, until arming fails later. (b) The no-device start-up tail, formerly assembled in Python with `\n`, now reaches the panel as one `\r`-joined line.
Suggested fix: send the "could not record" message to stdout when `rc` is 0; normalise `\r` → `\n` in `run_with_dialog`'s output (or add `without altering line endings` to the AppleScript), which fixes it for WireGuard output too.

### P3-5 — An openvpn binary path containing a space is never tracked → Connect can start a second daemon
`services/vpn_rootscript.py` `tracked()` (`first=${cmd%% *}`); `services/openvpn_manager.py:236-238`; `_ovpn_argv` accepts any absolute path.
Failure scenario: if `openvpn_binary()` returns e.g. `/Applications/My VPN/openvpn` (possible via `shutil.which` with a custom PATH), both identity checks take the first space-separated word `/Applications/My` and never recognise the running tunnel: `is_running()` is False, a second Connect passes Python, root's `tracked "$old"` fails, `forget` deletes the live tunnel's pid file and a second openvpn starts; Disconnect can never stop the first. Unlikely (GUI PATH and the fixed candidates have no spaces), but fails open.
Suggested fix: reject whitespace in the openvpn path in `_ovpn_argv` (and in bash with `[[ $ovpn != *[[:space:]]* ]]`), with a "Nothing was run" message.

### P3-6 — Doc claim: files "removed … at the next start-up of the Mac"
`docs/agents/vpn.md:80` (edit appeared during review).
The code removes stale `ovpn/` files only when the *next root script* runs after a reboot (boot-ID check in `_PROLOGUE`); whether macOS clears `/var/run` at boot is listed as unverified in ADR 0004 (R3). Until the next Connect/Disconnect the old root-owned pid file stays and Python reads it (harmless — the identity check fails — but the sentence is not backed by code).
Suggested fix: "removed on Disconnect, and on the first Connect or Disconnect after the Mac restarts".

### P3-7 — Small check-then-kill window; a process that exits in it is reported as a failure
`services/vpn_rootscript.py:242-244`.
Failure scenario: the process passes `tracked`, then exits on its own before `/bin/kill -TERM`; `kill` fails with ESRCH and the script `refuse 1 "could not signal OpenVPN"`, leaving pid and device files and reporting Disconnect as failed. Pid reuse inside that window is theoretically possible (no pidfd on macOS); the window is microseconds and the process is root's own openvpn, so this is a residual, not an exploit.
Suggested fix: on `kill` failure, re-run `tracked`; if gone, take the normal "stopped" cleanup path. Note the pid-reuse residual in ADR 0004.

### P3-8 — ADR and code disagree on what "Sentinel's openvpn" means
`docs/adr/0004-sentinel-vpn-run-dir.md:149-151` says the `ps` command must be "the configured openvpn binary"; both `tracked()` and `_is_tracked_process` (`openvpn_manager.py:236-238`) only check the basename `openvpn`, and Down is never told the configured binary.
Suggested fix: amend the ADR to "a program named openvpn" (and why: the binary can change between Connect and Disconnect), or pass `openvpn_binary()` to Down and compare the first word exactly. Cheaper: amend the ADR.

### Outside S8a (seen, not graded)
- ADR 0004 §7 says "Sentinel turns each [exit code] into one plain sentence"; nothing maps 96-99 — the user sees `0:NNN: execution error: Tunnel: … (96)`. Already true for WireGuard in S7.
- `openvpn_binary()` may still resolve to a user-writable Homebrew binary — known residual R1 with a D10 warning.

## Verified

1. **Bash pattern quoting.** `[[ "$cmd " == *" --writepid $2 "* ]]` with `$2='/a b/[x]*?.pid'` matches only that literal path, not `/a b/xyz.pid`, and `$2='/a b/*'` does not match `/a b/xyz.pid` (scratch script, `/bin/bash` 3.2.57). Glob characters and spaces in the legacy path are safe.
2. **Script syntax.** `bash -n` on the extracted `OVPN_SCRIPT` → OK. (shellcheck not installed; not run.)
3. **No string-built commands.** No `shell=True`, no command assembled from strings; `OVPN_SCRIPT` is a constant (test asserts `argv[5] is rs.OVPN_SCRIPT`); every value is validated in Python (`_absolute`, pid `1 < pid < 10**10` rejecting bool, `tries` 1-99, verb) and again in bash (regexes for pid/tries, absolute paths, verb).
4. **Clean, dialog-only root path.** Starts under `env -i PATH=...`; both modules' `_default_run_argv_as_root` are `partial(run_argv_as_root, allow_cached_sudo=False)` (`tests/test_privileged_runner.py`).
5. **Non-legacy Down requires both** root's own pid file == pid and the identity check; harness shows a twin lookalike refused with 98 and left alive, a stale pid reused by `/bin/sleep` refused, and all four lookalike kinds refused with nothing signalled.
6. **Modes and cleanup.** `ovpn/` 0755, pid/device 0644, `private/` 0700, log unlinked; failed start leaves no pid/device/private; earlier-boot files swept by the prologue.
7. **`read_root_file`** trusts only an lstat regular file owned by uid 0; tests flip `_OWNER_UID` to prove user-owned files are ignored for pid and device; `stored_sha256` behaviour preserved.
8. **Callers updated.** `vpn_connection` connect/disconnect go through `_argv_runner`; `vpn_execution` imports `vpn_rootscript` and shows the root pid path in the review; `ui/panels/vpn.py:1054` only uses `is_running()`; kill-switch arming (`vpn_connection.py:301-304`) uses `tunnel_device()`. Grep: no remaining references to `log_file`, `device_file`, `_settle_log`, `DEVICE_WAIT_ATTEMPTS`, `_SIDECAR_BASE` or `_default_run_as_root` in `services/`, `ui/`, `agents/`, `tests/`.
9. **Config cannot redirect pid/log.** `log`, `log-append`, `writepid`, `status`, `cd`, `chroot`, `config` are review blockers (`vpn_execution.py:82-83`), and the fixed `--daemon/--log/--writepid/--verb` options come after `--config`, so they would also win.
10. **Log tail** is shown once, goes through `redact_secrets` (`vpn_execution.py:1056`) and is not persisted (outcome "Tunnel keeps no log"); the server IP can appear only when no device was seen — same as before S8a.
11. **ps sees root's args.** `ps -ww -o user=,command=` as the user shows full argv of root processes (`/usr/sbin/systemstats --daemon`), so Python's pre-check works against root's openvpn.
12. **osascript behaviour** measured (commands in P3-4).
13. **Tests run:**
    - `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_rootscript.py tests/test_vpn_connection.py tests/test_tunnel_keeps_no_logs.py tests/test_vpn_killswitch.py tests/test_privileged_runner.py` → **260 passed**.
    - `tests/test_vpn_execution.py` → **39 passed**.
    - Full suite `.venv/bin/python -m pytest -q -p no:cacheprovider` → **3277 passed, 1 failed, 14 skipped**; the failure is `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` (LM Studio `lms` subprocess), the known unrelated one.
14. **Mutation check** (runtime patch of `OVPN_SCRIPT` via a scratchpad pytest plugin, no repo files touched), 6/7 killed:

    | Mutant | Result |
    |---|---|
    | identity check before kill removed | killed (6 failures) |
    | `--writepid` match removed | killed (2) |
    | program-name check removed | killed (1) |
    | tail always to stdout | killed (1) |
    | recorded-pid check removed | killed (1) |
    | up refusal 96 removed | killed (1) |
    | device wait removed | **survived** (P3-1) |

    Baseline with the plugin loaded, no mutant: 50 passed.
15. **Old vs new tests** (`git diff HEAD -- tests/`): removed `test_openvpn_keeps_only_the_device_name_not_the_log` / `test_a_failed_openvpn_start_is_explained_once_and_leaves_no_log` are replaced by real-process harness tests (stronger, except P3-1); `test_openvpn_writes_nothing_in_the_user_folder` asserts only script strings (weaker on its own; behaviour covered by the harness); removed `test_openvpn_connect_names_the_binary_by_absolute_path` is replaced by the builder pin and the relative-path refusal tests; the D2 spaces-in-path test is kept and now also asserts the legacy path is not accepted as root's. No test was weakened to get green.

## Not verified
- Real OpenVPN behaviour: whether `--log` takes over stdout/stderr before `--daemon` (so `do shell script` does not hang), the exact device-line format with tuntaposx (`TUN/TAP device /dev/tun0 opened` may match neither regex — pre-existing), whether openvpn removes its pid file on exit, and timing within the 30 s command timeout.
- Real root run through the authorisation dialog; `/var/run` clean-up at boot (owner real-Mac checklist, ADR R3).

## Resolution (author, 2026-10-31)

| Finding | Status | Change | Test |
|---|---|---|---|
| P2-1 | fixed | `_forget_legacy()` deletes the legacy pid, device and log once the legacy process is not tracked (every status read, so also before Connect) and after a legacy Disconnect that saw it exit; docstring and `docs/agents/vpn.md` updated | `test_an_older_sentinels_log_is_deleted_once_its_tunnel_is_gone`, `test_connect_deletes_an_older_sentinels_leftover_log`, `test_a_tunnel_an_older_sentinel_started_is_still_tracked_and_stopped`, `test_disconnecting_an_older_sentinels_openvpn_leaves_nothing_behind` |
| P3-1 | fixed | stub mode `late` writes the device line after ~1 s | `test_ovpn_up_waits_for_a_device_line_that_comes_late`, `test_ovpn_up_gives_up_on_the_device_after_its_wait` |
| P3-2 | fixed, differently | not `kern.boottime` (XNU shifts it on wake, so a legacy tunnel up across a sleep would be dropped). The legacy pid file is believed only if written within 60 s of the process starting (`ps -o etime`), which is what `--writepid` does and what a pid pasted in later is not | `test_a_legacy_pid_must_be_written_when_its_process_started`, `test_process_age_reads_ps_etime`, `test_a_legacy_pid_written_after_its_process_started_is_not_believed` |
| P3-3 | fixed | legacy files are forgotten only when the process is no longer tracked after Down | `test_an_older_sentinels_tunnel_still_running_keeps_its_device` |
| P3-4 | fixed | "could not record the tunnel device" goes to stdout; `run_with_dialog` turns `\r` back into `\n` (WireGuard output too) | `test_dialog_output_keeps_its_line_breaks` |
| P3-5 | fixed | whitespace in the openvpn path refused by the builder and by root (99) | `test_ovpn_path_with_a_space_is_refused_before_a_prompt`, `test_ovpn_script_refuses_a_path_with_a_space_itself` |
| P3-6 | fixed | `docs/agents/vpn.md` now says "on the first Connect or Disconnect after the Mac restarts" | docs |
| P3-7 | fixed | a `kill` that fails while the process is no longer tracked takes the stopped path; pid-reuse window recorded as ADR residual R4 | `test_ovpn_down_treats_a_process_that_exits_before_the_signal_as_stopped` |
| P3-8 | fixed (ADR amended) | ADR §5: "a program named openvpn", with the reason | docs |
| Outside S8a: exit-code mapping | open | ADR §7's plain-sentence mapping is not built (WireGuard too); noted in spec §16 limits | — |

Not re-reviewed by a third agent. Mutation check: see spec §16.

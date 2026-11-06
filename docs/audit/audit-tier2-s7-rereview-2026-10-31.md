# Audit tier-2 S7 round 2 — independent re-review (2026-10-31)

Reviewer: code-reviewer agent (read-only; did not write this code). Scope: commit `c1c5178` (S7 incl. round-2 fixes) against the first review `docs/audit/audit-tier2-s7-review-2026-10-29.md`, ADR 0004 and spec §14-15. Uncommitted S8a changes in the worktree ignored.

Process notes: `docs/playbooks/code-reviewer.md` is `status: current`, last-reviewed 2026-10-14 (17 days, not stale). **`docs/company/app-context.md` does not exist** (neither in the worktree nor in `c1c5178`), so its domain-risk list could not be checked; CLAUDE.md's Tunnel rules were used instead.

## Verdict
All three P1s are fixed, and each fix is pinned by a test that fails if the fix is reverted (mutation-checked). P2-4, P3-7 and P3-8 are fixed and pinned the same way. No P0. **P2-5 is only partly fixed**: `env -i` cleans the *inner* `/bin/bash`, but the *outer* `/bin/sh -c` that `do shell script` runs first is bash 3.2 on this Mac and, as my user, honoured an inherited `SHELLOPTS=xtrace` + `PS4='$(…)'` and executed the command substitution before `env -i` ever ran — whether that reaches root depends on the still-unverified question of whether `do shell script` forwards Sentinel's environment, so the spec's "Either way S7 is covered by `env -i`" is wrong. One new parser gap of the same family as P1-2: a NUL byte in a hook key hides the hook from the review (bash 4+ side unverified, graded P2, see N2). The deferred items (P2-6, P3-9) are honestly described as open in TODO and spec §15; one older spec line (:268) still contradicts P2-6. Committed tree: 364 passed; working copy with the requested command: 322 passed.

## Round-1 findings, re-checked

| ID | First review | Verdict | Evidence |
|---|---|---|---|
| P1-1 socket-based liveness | P1 | **fixed** | `vpn_rootscript.py:103` requires `[ -S "$wgstate/$name.sock" ]`; test `test_a_name_file_without_wireguard_gos_socket_is_not_live` (Up proceeds with a `.name` and no socket, a failed Down forgets the copy). Mutant "drop socket check" → KILLED by that test. Live tests now use a real `AF_UNIX` socket (`make_live`, test file :151-157). Gap: the `/sbin/ifconfig` half of `live()` is no longer pinned (N4). |
| P1-2 non-UTF-8 Disconnect runs hidden hooks | P1 | **fixed** (but see N2, same class) | `vpn_execution.py:743-748` sets `config_sha256 = ""` when `text is None` on Disconnect; `:787-801` then uses the stored copy (risk) or D9 blocker plus the read reason. Root gets `want=""` so only case 3 (stored copy hash) can run (`vpn_rootscript.py:147`). Tests `test_a_non_utf8_config_on_disconnect_without_a_stored_copy_is_refused`, `test_a_non_utf8_config_on_disconnect_falls_back_to_the_connect_copy` (asserts argv `["", STORED]`). Mutants "keep hash" and "drop read reason" → both KILLED. A stored copy is always UTF-8 (Connect blocks non-UTF-8; case 2 only stores a hash it decoded), so the fallback never reintroduces undecodable bytes. |
| P1-3 harness macOS-only vs Ubuntu CI | P1 | **fixed** (Linux run not verified) | `on_macos` skipif at `tests/test_vpn_rootscript.py:148`; all 25 `allow_process` harness tests carry it (counted). `/sbin/ifconfig` substituted by a stub in `run()` (:204). The only other `WG_SCRIPT` reference, `tests/test_vpn_connection.py:75,99`, compares argv only and runs no bash. CI matrix `.github/workflows/tests.yml` still has `ubuntu-latest`; not run on Linux by me. |
| P2-4 D9 tests read real `/private/var/run` | P2 | **fixed** | `_SIDECAR_BASE` read at call time (`vpn_rootscript.py:225,234`); autouse fixture `tests/conftest.py:131-137`; `test_the_sidecar_base_is_read_at_call_time` KILLS the "bind at import" mutant. Minor weakness: `raising=False` (N6). |
| P2-5 caller environment reaches root bash | P2 | **partly fixed** | `CLEAN_ENV` (`vpn_rootscript.py:35`, argv `:198`); `test_the_root_bash_starts_with_an_empty_environment` alone KILLS the "no env -i" mutant (BASH_ENV + PERL5OPT). Not covered: the outer `/bin/sh -c` of `do shell script` — see N1 with reproduction. |
| P2-6 no per-interface lock / unbounded FIFO read | P2 | **open (deferred), honestly described** | TODO P2 item; spec :323 now says correctly the FIFO case outlives the dialog. Spec :268 still claims "Two Connects for the same interface cannot race" (N5). |
| P3-7 `keep_stage` can nest the new copy | P3 | **fixed** | `vpn_rootscript.py:125`; `test_a_previous_copy_that_cannot_be_removed_is_refused_not_nested` (0500 subdir makes `rm -rf` fail) KILLS the mutant (without the check the stub ran on the old, now-missing copy). |
| P3-8 control characters in paths | P3 | **fixed** | `_absolute` refuses `ord<32` and `0x7F` (`vpn_rootscript.py:162`); parametrised ESC/DEL/TAB cases test :72-76. Mutant "NUL/CR/LF only" KILLED by the `\x1b` case. Fails closed after the review dialog but before any password prompt (same as P3-9). |
| P3-9 `~`/relative `config_path` | P3 | **open (deferred), honestly described** | TODO P3 item and spec :349. Code unchanged. |
| P3-10 doc claims | P3 | **partly fixed** | Spec :263 (exit codes not mapped), :323 (FIFO), `docs/agents/vpn.md` (copy removal "not yet confirmed") corrected; remaining stale claims in N5. |
| P3-11 test gaps | P3 | **fixed as stated** | Boot-wipe test now checks `wg-sha256/`, `wg-new/` (:316-323); migration test `test_down_case_2_with_no_stored_copy_runs_the_reviewed_file`; partial-failure paths openly listed as untested. |

## New findings

### N1 (P2) — `env -i` leaves the outer `/bin/sh` exposed: inherited `SHELLOPTS`/`PS4` runs code before it
- **Where:** `agents/vpn_agent/services/privileged.py:36` (`subprocess.run` without `env=`, so osascript inherits Sentinel's environment) and `:88-96` (`do shell script` runs `/bin/sh -c "<shlex.join(argv)>"`); `services/vpn_rootscript.py:32-35` comment claims the root bash starts clean; spec :343, :352 ("Either way S7 is covered by `env -i`").
- **Failure scenario:** a same-user process runs `launchctl setenv SHELLOPTS xtrace` and `launchctl setenv PS4 '$(/tmp/x)'`, then Sentinel is restarted. If `do shell script … with administrator privileges` forwards the environment (unverified — checklist item 8), root's `/bin/sh` expands `PS4` while tracing the first command and runs `/tmp/x` as root, before `env -i` and before any hash check. Applies equally to the MAC changer and kill-switch root scripts.
- **Evidence (as my user, exact production string):** `/private/var/select/sh -> /bin/bash` (3.2.57). `SHELLOPTS=xtrace PS4='$(touch …/PWNED)' /bin/sh -c "$(shlex.join(rs._wg_argv('up', …)))"` → `rc 1 PWNED exists: True`. `BASH_FUNC_x%%` also imports into `/bin/sh` but cannot shadow the absolute `/usr/bin/env`, so harmless here. Root behaviour: not verified (never run as root).
- **Suggested fix:** in `privileged.run_with_dialog` (and `run`), pass `env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}` to the osascript subprocess — covers every root script at the one place Sentinel controls. Keep `CLEAN_ENV` as a second layer. Test: capture `subprocess.run`'s `env` kwarg and assert `SHELLOPTS`, `PS4`, `BASH_ENV`, `PERL5OPT` absent. Correct spec :343/:352 and ADR §3.2.

### N2 (P2; P1 if bash 4+ behaviour confirmed) — a NUL byte inside a hook key hides the hook from the review
- **Where:** `services/vpn_execution.py:296-297` (UTF-8 decode accepts `\x00`); `_scan` `:205-213` then reads key `Post\x00Down`, not in `WG_HOOK_KEYS`.
- **Failure scenario:** a same-user process writes `Post\x00Down = /tmp/evil-hook` (or `Post\x00Up`) into the user's `.conf` before the review. The review shows no hook and no blocker on Connect/Disconnect; S7 then faithfully runs exactly those hashed bytes. wg-quick on macOS requires bash ≥ 4; bash ≥ 4.3 `read` silently skips NUL bytes (I believe — "skip NUL bytes in input" in `read.def`), so wg-quick would see `PostDown = /tmp/evil-hook` and run it as root.
- **Evidence:** `printf '[Interface]\nPost\000Down = /tmp/evil-hook\n'` → `hooks_from_text` `[]`, `_hook_findings` `([], [], [])` (run here). `/bin/bash` 3.2 truncates the line at NUL (`[Post]`, run here), so the old bash is safe; bash ≥ 4 is not installed on this Mac, so wg-quick's actual parse is **not verified** (`brew install bash` then run wg-quick's `read -r line` loop on the file).
- **Suggested fix:** treat any C0 control other than `\t\r\n` like non-UTF-8: Connect blocker; Disconnect drops the hash (stored copy or D9). Add tests for both actions.

### N3 (P3) — Owner checklist item 8 can give a false "not forwarded"
- **Where:** spec :352.
- **Failure scenario:** `launchctl setenv` only reaches apps launched afterwards; the probe runs `osascript` from an already-running Terminal, whose environment lacks the marker, so it never appears regardless of what `do shell script` does. The owner records "not forwarded" and N1 looks closed.
- **Suggested fix:** use `env SENTINEL_ENV_PROBE=1 osascript -e 'do shell script "env" with administrator privileges'` (the question is about osascript's own environment), and drop "either way S7 is covered".

### N4 (P3) — the `/sbin/ifconfig` half of `live()` is not pinned by any test
- **Where:** `vpn_rootscript.py:104`; the stub `tests/test_vpn_rootscript.py:185-187` always exits 0.
- **Failure scenario:** replacing the line with `true` passes every test (mutant SURVIVED, 255 passed). A `.name` + leftover socket for a utun that no longer exists would then count as live and refuse Connect with 96.
- **Suggested fix:** make the stub's exit code configurable; add a test with the socket present and the stub exiting 1 → Up proceeds / a failed Down forgets the copy.

### N5 (P3) — docs and copy claims the code does not support
- Spec :268 "Two Connects for the same interface cannot race" — contradicts :323 and deferred P2-6.
- Spec :313 "real `ifconfig` on an existing utun; skipped if none" — contradicts :341 (the harness now stubs it); test count at :307 "63 after round 2" vs 64 collected.
- Spec :303 and CHANGELOG say the D9 refusal tells the user to run `sudo wg-quick down <name>`; the blocker text (`vpn_execution.py:798-801`) does not, and the Disconnect rollback (`:821`) suggests `sudo wg-quick up`.
- The stored-copy risk line (`vpn_execution.py:792-795`) and ADR §6 case 3 say "confirmed at Connect"; after a case-2 Disconnect that fails with the tunnel still live (`vpn_rootscript.py:144-153`), the kept copy is the one confirmed at that Disconnect. Still reviewed bytes; wording only.
- `TODO.md:129` still says S7 "not yet committed" (it is in `c1c5178`).
- **Suggested fix:** correct the text, or add the `sudo wg-quick down <iface>` hint to the D9 blocker.

### N6 (P3) — isolation fixture fails open on a rename
- **Where:** `tests/conftest.py:137` `raising=False`.
- **Failure scenario:** if `_SIDECAR_BASE` is renamed, the fixture silently does nothing and the D9 tests depend on the owner's real `/private/var/run/sentinel-vpn` again — P2-4 returns with no test noticing.
- **Suggested fix:** `raising=True`.

## What I verified, and how
- Read `git diff 0d534b9 c1c5178` (code, tests, docs) and `git show c1c5178:` for `vpn_rootscript.py`, `vpn_execution.py`, `privileged.py`, the spec, ADR, `test_vpn_rootscript.py`, `test_vpn_review_gate.py`.
- Committed state: `git archive c1c5178` into the session scratchpad (`c1/`) with the worktree's `agents/bug_spray` submodule copied in, then `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_rootscript.py tests/test_vpn_review_gate.py tests/test_vpn_execution.py tests/test_vpn_connection.py tests/test_contract_tunnel_chat.py` → **364 passed in 59.60s**.
- Working copy (includes uncommitted S8a), requested command → **322 passed in 13.21s**.
- Mutation check on the exported copy (scratchpad `tools/mut.py`): 10/11 killed (P1-1 socket, P1-2 ×2, P2-4, P2-5 — killed by the builder pin and separately by the behavioural env test alone, P3-7, P3-8, utun regex, case-3 hash compare, D9 risk line); 1 survived (ifconfig check, N4).
- N1 reproduced as my user with `/bin/sh -c` and the production argv string; N2 Python parser side run, bash 3.2 behaviour observed.
- **Not verified:** anything as root; whether `do shell script` forwards the environment; wg-quick/bash ≥ 4 behaviour (not installed); the Linux CI job; real wg-quick under `env -i` with a copy under `/private/var/run` (owner checklist).
- No worktree file modified, nothing committed, nothing run as root.

## Resolution (author, 2026-10-31)

| Finding | Status | Change | Test |
|---|---|---|---|
| N1 (P2) | fixed | `privileged.run_with_dialog` starts osascript with `privileged.dialog_env()` (`PATH`, `HOME` only), covering every gated root script; ADR §3.2 and spec checklist item 8 corrected | `test_the_dialog_starts_osascript_with_a_minimal_environment`, `test_the_direct_runner_keeps_the_environment_unless_told` (`tests/test_privileged_runner.py`) |
| N2 (P2) | fixed | `_read_config` treats any control character other than tab/CR/LF like non-UTF-8 (Connect blocker; Disconnect drops the hash → stored copy or D9). Applies to OpenVPN too | `test_a_control_byte_in_a_config_blocks_connect` (NUL, ESC, DEL, VT), `test_tabs_and_crlf_line_ends_are_still_reviewable`, `test_a_nul_hidden_hook_on_disconnect_*` (`tests/test_vpn_review_gate.py`) |
| N3 (P3) | fixed | checklist item 8 sets the probe on the osascript command and adds a dialog smoke check | docs |
| N4 (P3) | fixed | harness `ifconfig` stub reads its exit code from a file | `test_a_name_and_socket_for_a_utun_that_no_longer_exists_is_not_live` |
| N5 (P3) | fixed | spec :268, :307, :313 corrected; D9 blocker names `sudo wg-quick down <interface>`; stored-copy risk line says "at Connect (or at the last Disconnect that did not finish)"; TODO S7 line names `c1c5178` | existing D9 tests |
| N6 (P3) | fixed | `raising=True` (default) on the isolation fixture, now `_READ_BASE` | fixture fails loudly on a rename |

Not re-reviewed by a third agent. Mutation check: see spec §16.

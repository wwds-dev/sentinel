# S7 Opus audit, branch `fix/s7-root-copy-rebased`, 2026-11-06

Auditor: code-reviewer agent on Opus (read-only; did not write this code). Saved by the orchestrating session from the agent's report. Scope: `git diff origin/main` (`f14a19a`), code and tests, three commits plus the working tree. Playbook `code-reviewer.md` current (2026-10-14). `docs/company/app-context.md` does not exist; reviewed against AGENTS.md, CLAUDE.md Tunnel rules, spec 1.1-1.2 and 14, ADR 0004.

## Verdict
Merge-ready on code: no P0, P1 or P2. Conditions: the working-tree changes land with the three commits (rebase review P2-1; the committed tip alone is a regression); the two rebase review files are committed (P3-2); TODO.md's own gate "Needs the S7 owner check before merge" (real-Mac checklist). All five post-rebase-review fixes hold and are mutation-checked (security P2-A reviewed `wg_quick` runs; code P3-1 and P3-2 Disconnect wording; code P3-4 kept-copy symlink test; `cd /` on Disconnect).

## Evidence
- 7 in-scope test files: 689 passed, 2 skipped. Full suite: 1 failed (known `test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`), 3757 passed, 14 skipped.
- 18 hand mutants in a copy of the worktree (`start_new_session`, group kill on timeout, sources restored): 15 killed (execute drops `wg_quick`; fingerprint drops it; P3-1 summary block; kept-copy warning; unsafe-wg-quick warning; failed up does not exit; `umask`; `head` stderr shown; `env=DIALOG_ENV`; path sent without hash; stale copy kept on not-up down; `.conf`-name blocker; `-L` on kept copy; `cd /` on down). Survived: F (20 s watchdog removed), G (`chmod` in `make_dir` removed), I (`wait || refused` loosened to `wait`).
- Every line of `ROOT_SCRIPT` read: only absolute-path tools and builtins after PATH is set; every `rm -rf` relative to the checked base; interface re-checked in the script; control characters dropped by `applescript_string` can only change the source path, which then fails the hash (fail closed).

## Findings

### P3-1 Two root runs for the same interface can delete the copy kept for a live tunnel
- Where: `services/vpn_rootscript.py:100` (`rm -rf "$d"` in `copy_verified`), `:114` (`up_now` unlocked), `:116` (failed up `rm -rf`), `:76` (mkdir race).
- Failure: Connect A has passed `up_now` and is in `wg-quick up` (no `.name` yet); Connect B for the same interface passes `up_now`, replaces the copy, its `wg-quick up` fails, its failure path removes the folder. A's tunnel is up with no kept copy: Disconnect silently verifies and runs the user's current file instead of "the copy you connected with"; if that file is missing/unreadable/non-UTF-8, exit 96 and the tunnel is stranded. First-run mkdir race: one script exits 97 (fail closed). Realistic route: a dialog timeout ("may still have happened") then Connect again; the UI otherwise allows one worker per panel and one instance.
- Evidence: `scratchpad/opus/probes/race.py` (fake wg-quick 1 s, fails if up): B +0.3 s -> `A rc 0 / B rc 1 / tunnel up: True | kept copy exists: False / Disconnect without a hash: rc 96`; B +0 s -> `B rc 97 mkdir: wg/wgt: File exists`. Real wg-quick timing not verified.
- Fix: serialise per interface (`/usr/bin/lockf -k wg/.<iface>.lock` or a mkdir lock after `cd -P`, released by a trap), re-check `up_now` under the lock, harness test with two overlapping Connects.

### P3-2 Deferred security P3-A/B/C recorded in no tracked file; two statements contradict them
- Where: spec `docs/specs/audit-tier2-privileged-layer.md:285`, ADR `docs/adr/0004-root-runs-verified-copies.md:45`, no TODO row, `docs/audit/s7-rebase-*-2026-11-06.md` untracked.
- P3-A: spec :285 says "the physical-path check catches a rename that happened before it"; the security probe shows rename + re-create of the same name passes `pwd -P`; the second half (wg-quick re-reads `$base/...` by absolute path, `vpn_rootscript.py:87,116,131`) is recorded nowhere. P3-B (stalling stat before the cut-off) recorded nowhere. P3-C confirmed by probe: Sentinel's review treats `.name` without `.sock` as not up (`services/vpn_execution.py:881-882`), root treats `.name` alone as up (`vpn_rootscript.py:88`): the review allows Connect, root refuses 94, Disconnect fails and keeps the key-bearing copy, contradicting ADR :45 ("a stale `wg/<iface>/` is replaced by the next connect"). The security review's P2-A note on wg-quick's shebang interpreter is neither fixed nor listed (not verified; wg-quick not installed).
- Evidence: `scratchpad/opus/probes/stale.py`: `review probe: {'up': False, 'device': 'utun9', 'stale': True}` / `Connect: rc 94 refused: wgt is already up` / `Disconnect: rc 1` / `root copy (holds the key) still there: True`.
- Fix: commit the rebase reviews; spec 14 lists P3-A (both halves), P3-B, P3-C and the interpreter residual as open and corrects :285; a TODO row. P3-C is one line if wanted now: `up_now` mirrors `wireguard_interface_state` (`.name` and `[ -S "$wgrun/$n.sock" ]`).

### P3-3 The FIFO test leaks an orphan `head` (root in real use) when the guard breaks; TODO's fix is not on this branch
- Where: `tests/test_vpn_rootscript.py:251-256` (`subprocess.run(..., timeout=10)` kills only bash); TODO.md :171 says the fix (`3216834 c348487 764dc89`, `claude/rootscript-fifo-writer`) lands with that line, not on this branch.
- Failure: with `[ -f "$src" ]` broken the test fails at 10 s, before the 20 s watchdog, so `head` stays blocked on the FIFO (ppid 1).
- Evidence: mutated sandbox `1 failed in 11.26s`, leftover `/usr/bin/head -c 262145 -- .../test_a_fifo_is_refused_at_once0/home/wgt.conf` (killed). Older orphans from this test still running: pids 65579 (1 day), 98776 (25 min); 43270 from another session's probe. Left alone.
- Fix: port the held-writer pattern (`os.open(fifo, O_RDWR|O_NONBLOCK)` for the run), `start_new_session=True` and group kill on timeout.

### P3-4 Three ADR/spec claims have no test that fails when the code breaks
- Where: `services/vpn_rootscript.py:79` (`chmod` on an existing dir), `:105` (20 s watchdog), `:108` (`wait "$reader" || refused`).
- G: dropping `chmod` breaks no test, though the ADR amendment says existing directories are normalised; testable without root (pre-create base 0777 as the test user, expect 0755). F: watchdog untested (spec says so). I: `wait || refused` untested, and load-bearing in the watchdog case (a FIFO writer that sends the reviewed bytes and holds the pipe leaves a copy that hashes equal; only the non-zero `wait` refuses it); the spec does not say so.
- Fix: chmod test; for F and I a test with a small cut-off (function split), or name the residual in spec 14.

### P3-5 A `config_path` with no `/` and no `.conf` passes the review, then fails in the runner
- Where: `services/vpn_execution.py:1124` (`interface_for(target)` reads `"wgnl"` as a bare name) vs `services/vpn_connection.py:126-127` (made absolute, `interface_for("/cwd/wgnl")` is None).
- Failure: a hand-edited or legacy profile `config_path: "wgnl"` gets an allowed review ("copy 'wgnl' to ..."), then "Nothing was run: invalid WireGuard interface name ''. Review the profile again." and the same allowed review again: a loop. Fails closed, no prompt.
- Evidence: `scratchpad/opus/probes/bare_path.py`: `review allowed: True | blockers: ()` / `ran: True | root called: False | error: Nothing was run: invalid WireGuard interface name ''.`
- Fix: in the review treat any `resolve_config_path` value as a path and block when `CONFIG_NAME_RE.fullmatch(Path(config_path).name)` fails.

## Prior findings, checked
Security P2-A resolved (`vpn_execution.py:944-953` fingerprint, `:1473` handoff, no fresh lookup in the runner; residual: shebang interpreter, P3-2). Code P3-1/P3-2/P3-4 resolved. Disconnect stranding: none in single-run use (no kept copy -> intact file verified and run; missing/unscannable file and no copy -> 96, stated honestly); new routes are P3-1 and P3-C. Deferrals P3-A/B/C reasonable on impact but not yet recorded (P3-2). Docs left to the owner per CLAUDE.md.

## Not verified
Real wg-quick; behaviour as root incl. `chown`/`chmod`; admin osascript under `DIALOG_ENV`; `/var/run` across a reboot; real `wg-quick up` duration (the P3-1 window).

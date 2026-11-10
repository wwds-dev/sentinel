# S7 rebase review (`fix/s7-root-copy-rebased`), 2026-11-06

Reviewer: code-reviewer agent (read-only; did not write this code). Saved by the orchestrating session from the agent's report. Scope: `git diff origin/main` (`f14a19a`) in the worktree: rebased commits `1ce5017`, `ae6c82b`, `0b2cd91` (originally `7bca617`/`d7bf25e`/`c908334`) plus the uncommitted working tree. Playbook `code-reviewer.md` is `status: current`, last-reviewed 2026-10-14. `docs/company/app-context.md` does not exist; reviewed against AGENTS.md, CLAUDE.md (Tunnel rules), spec 1.1-1.2 and 14, ADR 0004.

## Verdict
Approve the working tree, conditional on P2-1. No P0/P1. The rebase lost nothing from main's PRs #30-#37 once the uncommitted changes land with the three commits; the three commits alone are a regression.

## Evidence
- Full suite (worktree): 1 failed (known `test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`), 3773 passed, 14 skipped. The 7 VPN test files: 680 passed, 2 skipped.
- Conflict resolution vs main, file by file: `services/vpn_execution.py` lost only docstrings and the two `wg_quick_command` -> `wg_root_summary` call sites (15 lines); `_which_privileged` / `_PRIVILEGED_PATH` identical to main (a new test pins them to `vpn_rootscript.SYSTEM_PATH`); NUL stripping/blocking, the hook allowlist, the DNS wildcard blocker and SaveConfig intact. `services/openvpn_manager.py`: two comment lines. What `wg_quick_command` guaranteed now holds in `ROOT_SCRIPT`: `env -i`, PATH = wg-quick's folder then the system folders, `HOME=/var/root` (`vpn_rootscript.py:66`), `(cd / && exec "$wgq" ...)` (`:116`, `:131`), absolute config and wg-quick (`vpn_connection.py:125`, `:128`).
- Main's isolation tests are ported, not dropped: `test_vpn_rootscript.py::test_wg_quick_runs_from_root_with_only_home_and_path[up|down]` runs the script unprivileged and checks cwd `/`, `HOME=/var/root`, the pinned PATH and no other keys; the relative-file, `link/../x` and relative-PATH tests go through `vpn_connection.connect` to root's argv.
- Spec 1.2 conformance: met on every point (root-owned 0700 copy under `/private/var/run/sentinel-vpn/wg/<iface>`; symlinked base, symlinked `wg`, foreign owner, moved base -> 97; hash of the copy else `rm -rf` + 98; up on the copy, removed on failure; Disconnect on the kept copy then removed; fallback verify without one; bare names resolved from `WG_CONFIG_DIRS` and `execute()` runs the reviewed file; `execute()` passes the fresh review's `config_sha256`, equal to the confirmed one by the fingerprint check), each pinned by a real-`/bin/bash` harness test.
- Mutation: 18/20 hand mutants killed (every uncommitted change among them: HOME export, `cd /` up and down, relative source, relative wg-quick, `env=DIALOG_ENV`, undecodable hash kept; plus hash not handed over, hash comparison removed, failed down removes copy, Disconnect skips verify without copy, `export PATH`, `env -i`, failed up keeps copy, interface-only run as a bare name, regular-file check, up-state check, `..` in the bad-interface case). Survivors M14 and M18: see P3-2 and P3-4. Mutation ran in scratch copies; the worktree was left as found.

## Findings

### P2-1 Committed tip loses main's #35/#36 isolation; only the uncommitted tree restores it
- Where: `0b2cd91` (HEAD). `HEAD:services/vpn_rootscript.py` has no HOME export and runs `"$wgq" up "$run"` without `cd /`; `HEAD:services/vpn_connection.py` has no `_absolute`/`SYSTEM_PATH` and does not make source or wg-quick absolute.
- Failure: merging the three commits without the working tree. A relative `config_path` reaches root relative, root has `cd`'d into the base, `[ -f "$src" ]` fails, Connect exits 98; a relative wg-quick makes `wireguard_argv` raise; wg-quick and hooks run with the base as cwd and no HOME; main's suite goes red.
- Evidence: `git archive HEAD` + `bug_spray` submodule, pytest on 4 VPN files: `5 failed, 208 passed` (the five #35/#36 isolation tests; one AttributeError on `vpn_connection.SYSTEM_PATH`).
- Fix: commit all modified source and test files together (by name), ideally folded into `0b2cd91`; rerun the suite at the new tip before pushing.

### P3-1 Disconnect promises "copy and check" when no hash will be sent
- Where: `services/vpn_execution.py:1107` builds `command` from `wg_root_summary(..., target)` before the read; `:1148` then clears `config_sha256`; `vpn_connection.py:98-111` does not know.
- Failure: Disconnect of a non-UTF-8 or deleted `.conf` with no kept copy: the dialog says "without one, copy '<path>' and check its SHA-256 first", root gets `""` `""` and refuses with 96.
- Evidence: `review_execution('disconnect', ...)` on a `b'\xff'` file printed that wording with `config_sha256 == ''`; same for a missing file (which also carries no risk line).
- Fix: rebuild the summary after the read with the interface-only wording when the hash is empty, or pass a flag; assert `r.command` in the test.

### P3-2 Relative-path Disconnect says "nothing runs" but root verifies and runs the file
- Where: `services/vpn_connection.py:107` tests `isabs(expanduser(target))`, while `_wg_root_run` (`:125`) makes the path absolute and sends it with its hash.
- Failure: `config_path="sub/wg0.conf"`, no kept copy: the review says "without one, nothing runs", root copies, verifies and runs `<cwd>/sub/wg0.conf` with its PreDown/PostDown. Mutant M14 (`target.startswith('/')`) survived: the `~/` branch is untested.
- Fix: decide on "is a config path" (`"/" in target or target.endswith(".conf")`, the `interface_for` rule), with tests for the relative and `~/` cases.

### P3-3 Spec, CHANGELOG and user docs not updated for the uncommitted changes
- Spec 14 still says `$1`-`$7` and does not mention HOME/`cd /` in `ROOT_SCRIPT`, `privileged.DIALOG_ENV`, or the cleared hash; CHANGELOG lacks the osascript environment change; `docs/agents/vpn.md:45` quotes "config changed after you confirmed it; nothing was run.", the script prints "...changed after you confirmed it, or could not be read; nothing was run."
- Fix: spec 14 follow-up paragraph, CHANGELOG security line, correct the quote, owner-checklist item for a real Connect with `DIALOG_ENV` (dialog still appears, non-ASCII output still renders without HOME/LANG/TMPDIR; not verified).

### P3-4 The kept-copy symlink check has no test
- Where: `services/vpn_rootscript.py:126` `[ -L "$copy" ] || [ ! -f "$copy" ]`. Mutant M18 (drop `-L`) survived. Low risk: only root writes into `wg/<iface>` (0700); defence in depth.
- Fix: harness test planting a symlink as the kept copy: expect the fallback verify, or 96 without a source.

## Not verified
Real wg-quick, real root ownership, `/private/var/run` across a reboot, osascript with an empty environment (owner checklist).

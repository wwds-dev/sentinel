# Independent review of e573ca5 (unopenable chain moved aside, notice kept under the chain, CHANGELOG)

Reviewer: Opus 5.5, read-only (`code-reviewer` + `security-engineer`), 2026-11-05.
Scope: `e573ca5` on `claude/chain-snapshot` (parent `560a232`, which holds the previous review; branch base `c7786ed` = `origin/main`). The commit fixes P3-1 to P3-6 from `docs/audit/chain-snapshot-review-2-2026-11-05.md`. Files: `agents/vpn_agent/services/proxychain.py`, `ui/panels/vpn_privacy.py`, `tests/test_vpn_privacy_tab.py`, `tests/test_vpn_socks_chain.py`, `CHANGELOG.md`, `TODO.md`. Tier C review: the reviewer did not write the commit.

Playbooks and context:
- `docs/playbooks/code-reviewer.md`: `last-reviewed: 2026-10-14`, `status: current`.
- `docs/playbooks/security-engineer.md`: still **`status: baseline`** (`last-reviewed: 2026-10-10`). Under AGENTS.md that makes it **stale**.
- `docs/company/app-context.md` **does not exist in this worktree** and was not read this time. The invariants used are the ones the previous review quoted from the main checkout's copy: never print or copy credentials; do not imply that Tunnel keeps logs.

**Verdict: ready to merge. No P0, P1 or P2 findings.**
- All six findings from the previous review are closed. 6 of the 97 test cases in the two chain files fail on the parent's code; the other 3 new tests pin branches that were already correct, and mutants show they hold them.
- 21 hand mutants: 19 killed, 1 caught only by a hang (the counter increment, R6), 1 survivor of low value (R12).
- Five P3s remain: the notice does not survive a restart and the move happens at app start (P3-1); "readable only by you" is overstated (P3-2); chmod hardening (P3-3); the counter regression hangs instead of failing (P3-4); wording (P3-5).

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Targeted suites | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_vpn_privacy_tab.py tests/test_vpn_socks_chain.py tests/test_macaddr.py tests/test_quit_privileged.py` | 236 passed in 6.02 s |
| Full suite | `.venv/bin/python -m pytest -p no:cacheprovider -q` at HEAD `e573ca5` | 3211 passed, 14 skipped, 1 deselected, 1 failed (`test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`, `ForbiddenInTests` on `lms server status`; known and unrelated), 219.6 s. Matches the commit message (which leaves out the 1 deselected). |
| New tests against the parent's code | `git show 560a232:<file> > <file>` for `proxychain.py` and `vpn_privacy.py`, then the two chain test files, then `git checkout --` | 6 failed, 91 passed (97 collected) |
| Hand mutants | 21, planted one at a time by a scratch script (each replacement asserted to match exactly once, restored with `git checkout -- <file>`); runner: the 4 targeted files in `subprocess.run(timeout=90)`, since `timeout(1)` and pytest-timeout are both missing | 19 killed, 1 hang (R6), 1 survived (R12) |
| Loader probes | Scratch script with `VPN_AGENT_STATE_DIR` in the scratchpad: a real `chmod 000` file, a hard link to an outside file, `os.chmod(follow_symlinks=False)` on a file and on a link, a file deleted between `is_file()` and the read | see "Security and behaviour" |
| UI probes | A temporary test file using the real `env` fixture (deleted afterwards): two constructions in a row (a "restart"), `macaddr.recorded()` true with `show()` and the event loop, a failed listing, every edit path, a failed move, the label width with a long path | see "Security and behaviour" |
| Tree clean at end | `git status --short`; `git diff --stat` | both empty |

No live MAC, Wi-Fi, root, tunnel or network action was run. Every probe used the mocked fixture (`SyncWorker`, faked `macaddr` and `tor`) or a scratch state directory. No `.env` value was read.

New tests that fail on the parent: `test_the_unreadable_chain_notice_outlives_the_status_line`; `test_a_failed_save_keeps_the_unreadable_chain_notice`; `test_a_wrongly_typed_saved_chain_loads_as_empty[nested-too-deeply]`; `test_load_chain_checked_names_what_is_wrong[nested-too-deeply]`; `test_a_chain_that_cannot_be_opened_is_moved_aside`; `test_a_moved_aside_copy_is_made_owner_only`.

Three pass on the parent, as expected, because they pin behaviour that was already correct: the `-3` assertion in `test_load_chain_checked_keeps_every_unreadable_copy` (kills R6, by hanging), `test_a_chain_that_cannot_be_moved_is_left_in_place_with_a_warning` (kills R5) and `test_a_linked_chain_is_moved_without_touching_its_target` (kills R8 and R9).

## Each fix against its finding

| Finding (review of 380227c) | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P3-1: deeply nested JSON stopped the panel from being built | `RecursionError` caught in `load_chain` (`proxychain.py:137`) and `load_chain_checked` (`:162`, "it is nested too deeply") | Yes | `…loads_as_empty[nested-too-deeply]`, `…names_what_is_wrong[nested-too-deeply]` (R1, R2, R14) |
| P3-2: an unopenable or unmovable chain was overwritten by the first edit | The `OSError` branch (`:156-157`) falls through to the move; only a failed `replace` (`:171-174`) leaves the file and warns | Yes. A real `chmod 000` file (not mocked) is moved with "it could not be opened (Permission denied); it was moved to proxychain.json.unreadable"; the copy is 0o600 and readable again by the owner. | `test_a_chain_that_cannot_be_opened_is_moved_aside` (R3, R4, R13), `…cannot_be_moved_is_left_in_place…` (R5) |
| P3-3: the notice could be overwritten before it was seen | `_chain_notice` set before `_build()` (`vpn_privacy.py:53-55`), appended by `_show_chain_summary` (`:450-455`), cleared only after a successful `save_chain` (`:423-429`) | Yes within one session. Probe: with `macaddr.recorded()` true and the event loop run, and after a failed listing, the summary still holds the notice. Every edit path (add, add Tor, remove, move, mode) clears it after a successful save; a failed save and "Select a hop first." keep it. **It does not survive a restart (P3-1 below).** | `…notice_outlives_the_status_line` (U1, U3, U4), `…failed_save_keeps…` (U2, U6, U7) |
| P3-4: the moved copy kept a 0644 mode | `os.lstat` + `S_ISREG`, then `os.chmod(aside, 0o600)`, `OSError` ignored (`:175-179`) | Yes for a regular file you own. Silent when the chmod fails, and a hard link is chmodded (P3-2, P3-3 below). | `test_a_moved_aside_copy_is_made_owner_only` (R7, R10, R11), `test_a_linked_chain…` (R8, R9) |
| P3-5: three untested branches | third copy, both `OSError` branches, mode, link | Yes. R6 is now caught, but as a hang (P3-4 below). | as above |
| P3-6: no CHANGELOG entry | Three Fixed entries (`CHANGELOG.md:47-57`) | Yes, with wording issues (P3-2 below) | n/a |

## Security and behaviour

| Concern | Evidence | Result |
|---|---|---|
| Moved or chmodded outside the state dir? | `aside = path.with_name(...)`, always the same directory; `rename(2)` follows no link; the linked-file test and mutant R9 (`os.stat`) show a symlink's target is never chmodded. **A hard link is:** probe with `proxychain.json` hard-linked to an outside 0644 file, which became 0o600 after the load. | Nothing is moved outside. A hard-linked outside inode is tightened to 0600 (P3-3). |
| TOCTOU between `lstat` and `chmod` | `os.chmod` follows links. Swapping the aside for a symlink between `:176` and `:177` needs a process with the owner's rights writing into the owner's 0700 directory, and chmod of a file the owner does not own fails `EPERM`. Worst case: tightening one of the owner's own files to 0600. | No escalation. Hardening option in P3-3. |
| Better primitive on macOS? | `os.chmod in os.supports_follow_symlinks` is `True` (Python 3.11.15, `lchmod` present). Probe: `os.chmod(file, 0o600, follow_symlinks=False)` gives 0o600, and on a link leaves the target at 0o644. An fd approach (`os.open(aside, O_RDONLY\|O_NOFOLLOW)` + `fchmod`) **cannot open the mode-000 file** this commit now moves. | `follow_symlinks=False` is the right tool (P3-3) |
| Can the notice leak contents or secrets? | Reason texts unchanged from the previous review (fixed strings, `strerror` only, `str(exc)` only for fixed-string `TypeError`s). New parts: the folder path and "it is nested too deeply". The mode-000 probe file held `"password": "s3cret"`; the reason did not contain it. | No content |
| Folder path in the UI | It shows the home path, and with it the macOS user name. Sentinel already shows paths in UI text (`server/paths.py:84-86`). Nothing is logged: `_say` only does `setText` (`vpn_gate.py:28-29`). | Not a secret; acceptable |
| Moving a file that could not be opened | macOS file locks are advisory and do not make `read` fail. A Finder-locked (`uchg`) file fails the rename, then `write_private`'s replace also fails: "Could not save the chain" and the notice is kept (reasoned, not run). A transient `EIO` or an evicted iCloud/network file is moved aside and the chain starts empty until renamed back: strictly better than the previous overwrite, the cost is discoverability (P3-1). A root-owned 0600 file in your 0700 dir: the rename succeeds and the chmod fails silently (reasoned, not verified). | Moving is the right default |
| A file deleted between `is_file()` and the read | Probe: "it could not be opened (No such file or directory), and it could not be moved aside, so it will be replaced when you edit the chain". Misleading (nothing to replace) but harmless; needs a concurrent delete. | Not a finding |
| `_chain_notice` exists before use? | Set at `vpn_privacy.py:53`, before `_build()` (`:56`). `_build` sets the mode box before connecting `_mode_changed` (`:179-180`), so no save fires during construction. | Yes, on every path |
| Length and fit | Worst case seen: 340 characters (failed move, pytest scratch path); it wraps. With a long unbroken path the label's `minimumSizeHint` is 833 px, but the tab's minimum width is unchanged. The real path has short tokens. | Fits |
| Thread safety | One small local read plus a rename and a chmod on the UI thread at construction. No new thread, lock or network access. | No change |

## Accuracy

- **Commit message.** Suite counts match. "12/12 hand mutants killed" are not named, so not verified; this review's 21 left one survivor and one hang. "Never through a link" is true for symlinks, but a hard link is chmodded. "A restore offer or a failed listing cannot hide it" is true within a session.
- **CHANGELOG.md:47-57.** "Test chain tests exactly the hops you confirmed … hop buttons and the chain mode are now off": true (`vpn_privacy.py:220-229`). "Changing the chain mode no longer clears the selected hop": true. "Test chain is no longer switched on, or left on, against an empty or invalid chain": true against v2.002 (fixed in bb4d35d/d5d1d35, on `main` after v2.002, so it belongs under Unreleased). "No longer stops the Tunnel panel from opening" is an understatement: `VpnPanel` is built unguarded inside the main window's construction (`main.py:2854-2886`), so the old exception most likely stopped Sentinel from starting (not verified by launching). "Made readable only by you" and "says so under the chain until you edit it" are overstated (P3-1, P3-2).
- **Docstring `proxychain.py:143-149`.** "Made owner-only" holds only when the chmod succeeds. The comment at `:179` ("the folder is owner-only") is false under an unhardened `VPN_AGENT_STATE_DIR` override (0o755 in the previous review's probe).
- **TODO.md:139.** Accurate; correctly says "not yet independently reviewed". It should now cite this report.

## Mutant results

Runner: the 4 targeted files under a 90 s subprocess timeout; each mutant restored by `git checkout --` before the next.

| # | Mutant | Result | Killed by |
|---|---|---|---|
| R1 | `load_chain` drops `RecursionError` | **Killed** | `…loads_as_empty[nested-too-deeply]` |
| R2 | `load_chain_checked` drops the `RecursionError` clause | **Killed** | same + `…names_what_is_wrong[nested-too-deeply]` |
| R3 | `OSError` branch returns early again (no move) | **Killed** | `test_a_chain_that_cannot_be_opened_is_moved_aside` |
| R4 | read `except OSError` narrowed to `FileNotFoundError` | **Killed** | same |
| R5 | move `except OSError` narrowed to `FileNotFoundError` | **Killed** | `…cannot_be_moved_is_left_in_place_with_a_warning` |
| R6 | `number += 1` removed | **Hang** (killed by the 90 s timeout) | the `-3` assertion; a hang, not a failure (P3-4) |
| R7 | chmod removed | **Killed** | `test_a_moved_aside_copy_is_made_owner_only` |
| R8 | `S_ISREG(lstat)` check replaced by `True` | **Killed** | `test_a_linked_chain_is_moved_without_touching_its_target` |
| R9 | `os.lstat` → `os.stat` | **Killed** | same |
| R10 | mode 0o600 → 0o640 | **Killed** | `…made_owner_only` |
| R11 | chmod `path` (gone) instead of `aside` | **Killed** | same |
| R12 | chmod `except OSError` narrowed to `PermissionError` | Survived, low value: needs a chmod failure other than `EPERM` after a successful rename | none |
| R13 | `OSError` reason uses `str(exc)` | **Killed** | `…cannot_be_opened_is_moved_aside` |
| R14 | recursion reason replaced by the generic text | **Killed** | `…names_what_is_wrong[nested-too-deeply]` |
| U1 | notice never cleared after a save | **Killed** | `…notice_outlives_the_status_line` |
| U2 | no `return` after a failed save | **Killed** | `…failed_save_keeps…` |
| U3 | summary leaves the notice out | **Killed** | both new UI tests |
| U4 | notice leaves the folder out | **Killed** | `…outlives_the_status_line` |
| U5 | notice not sent to the status line | **Killed** | 4 malformed-chain cases + `…survives_the_first_edit` |
| U6 | notice cleared before the save is attempted | **Killed** | `…failed_save_keeps…` |
| U7 | `_mode_changed` clears the notice regardless of the save | **Killed** | same |

## Findings

### P3-1: the notice is lost on the next launch, and the move happens at app start even if the Privacy tab is never opened
`ui/panels/vpn_privacy.py:50-55` (the notice exists only in memory), `main.py:2886` → `ui/panels/vpn.py:382` (the tab is built at start-up) and `proxychain.py:151-152` (the next load sees no file and returns no reason).

Scenario: `proxychain.json` (with proxy passwords) gets corrupted. The user starts Sentinel for something else and never opens Tunnel › Privacy. At start-up the file is moved to `proxychain.json.unreadable`, and the notice sits in a tab nobody looks at. On the next launch the chain is simply empty and nothing mentions the copy. Probe: the second `PrivacyTab()` shows `Chain: (empty)` / `Problems: the chain has no proxies in it` and status `Idle`, while the folder holds `proxychain.json.unreadable`. No data is lost, but the user is never told where the chain went, and the CHANGELOG's "says so under the chain until you edit it" does not hold across a restart. This also covers the transient-`EIO` case, where a readable file is moved.

Suggestion: derive the notice from the disk, not the session. When `proxychain.json` is absent and a `proxychain.json.unreadable*` exists, have `load_chain_checked` return a reason such as "an earlier saved chain could not be read and is kept as <newest name>"; the first successful save creates `proxychain.json` and ends it. Test with two `PrivacyTab()` constructions in a row.

### P3-2: "made readable only by you" is overstated, and the chmod comment relies on a folder mode that may not be set
`CHANGELOG.md:53`, the docstring at `proxychain.py:143-149`, and `:178-179` (`except OSError: pass  # moved; the folder is owner-only`).

The chmod fails silently for a file owned by another user (for example root; `EPERM`, reasoned, not run). A symlinked chain is moved as a link, and its target keeps its mode, by design. A portable data folder on a volume without Unix permissions is not verified. Under a `VPN_AGENT_STATE_DIR` override not yet saved to, the folder is not hardened (0o755 in the previous review's probe), so the comment's fallback is false there.

Suggestion: in the CHANGELOG, say "made owner-only where possible", or drop the clause; in the docstring and comment, say the chmod is best-effort. Optionally call `paths.ensure_private_dir(path.parent)` before the move, as `save_chain` does. Consider "no longer stops Sentinel from starting" once checked by launching.

### P3-3: the chmod follows a hard link out of the folder and has a small lstat/chmod window
`proxychain.py:176-177`.

Probe: `proxychain.json` hard-linked to an outside 0644 file left that outside file at 0o600 after `load_chain_checked()`. Separately, swapping the aside for a symlink between `lstat` and `chmod` would chmod the link's target. Both need a hand-made link or same-user write access to the state dir, and both only tighten a file the owner owns, so there is no escalation; still, it is the one place this code changes something outside the state dir.

Suggestion: `st = os.lstat(aside)`; if `S_ISREG(st.st_mode) and st.st_nlink == 1`, call `os.chmod(aside, 0o600, follow_symlinks=False)` (supported here; the probe shows it leaves a link's target alone). Do not switch to `O_NOFOLLOW` + `fchmod`: it cannot open the mode-000 file this commit now moves. Add a hard-link case beside `test_a_linked_chain…`.

### P3-4: a regression in the counter hangs the suite instead of failing it
`proxychain.py:168-170`, test `tests/test_vpn_socks_chain.py:235`.

Mutant R6 (`number += 1` removed) is caught only because the run never ends. pytest-timeout is not installed, so this shows up as a wedged suite (the failure mode this lab's AGENTS.md warns costs hours) rather than a named failure.

Suggestion: bound the loop (for example `for number in range(2, 1000)` with a fallback reason "too many unreadable copies; it will be replaced when you edit the chain"), so a broken increment fails the `-3` assertion. Alternatively run that test's loader in a thread with `join(timeout=5)`.

### P3-5: commit message and TODO.md wording
The "12/12 hand mutants" are not named (not verified); this review's 21 gave 19 killed, 1 hang and 1 survivor. "Never through a link" is true for symlinks only (P3-3). Point `TODO.md:139` at this report, and carry over P3-1 to P3-4 if deferred.

### Not a finding
- **Notice clearing.** Cleared exactly when a save succeeds, on all five edit paths (probe). A failed save and a no-op remove keep it. Mode-box signals are connected after the initial `setCurrentText`, so construction never saves.
- **Reason text.** Never contains file contents.
- **The ENOENT race message.** Misleading but harmless.
- **Out of scope, observed.** Orphaned `/usr/bin/head -c … pipe.conf` and `…/ca.crt` processes, some 7 days old, are left by `test_a_fifo_source_is_refused` and `test_a_data_file_that_is_not_a…` runs from other sessions. Not this commit, and none came from this review's runs. A test reading a FIFO through `head` with no writer leaks a process. Worth a separate TODO item.
- **Not verified:** a file really owned by root or another user; a state dir on exFAT, a network volume or iCloud; a real Finder-locked (`uchg`) file; launching the app to confirm the old exception stopped all of Sentinel; the commit's own 12 mutants; the 18-file VPN set. `docs/company/app-context.md` is missing from this worktree, so its invariants were not re-read.

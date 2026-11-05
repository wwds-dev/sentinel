# Independent review of 924d1f5 (unreadable-chain notice survives a restart; bounded, link-safe move aside)

Reviewer: Opus 5.5, read-only (`code-reviewer`), 2026-11-05. Scope: `924d1f5` on `claude/chain-notice-cbf13a` (parent `6280c8f` = `main`). It closes P3-1 to P3-5 of `docs/audit/chain-snapshot-review-3-2026-11-05.md`. Files changed: `agents/vpn_agent/services/proxychain.py`, `tests/test_vpn_socks_chain.py`, `tests/test_vpn_privacy_tab.py`, `CHANGELOG.md`, `TODO.md`. The reviewer did not write this commit. (The reviewer ran read-only and returned this report; the author's session saved it verbatim, with one correction marked below.)

Playbooks and context:
- `docs/playbooks/code-reviewer.md`: `last-reviewed: 2026-10-14`, `status: current` (22 days old).
- `docs/playbooks/security-engineer.md`: still `status: baseline` (2026-10-10), so it is stale under AGENTS.md.
- `docs/company/app-context.md` **does not exist in this worktree** and was not read. I used the invariants from CLAUDE.md's Tunnel rules: fail closed, never print secrets or `.env` values, and no live MAC, Wi-Fi, root, tunnel or network actions.

**Verdict: ready to merge. No P0, P1 or P2 findings.**
- 12 new test cases. 10 fail on the parent, as the commit says.
- 18 hand mutants: 16 killed, 2 survived. M15 matters as a test gap (P3-2); M16 has low value.
- Four P3s.

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Targeted suites | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_vpn_socks_chain.py tests/test_vpn_privacy_tab.py` | 109 passed in 1.55 s |
| Wider VPN set | the two files above + `test_tunnel_keeps_no_logs`, `test_vpn_app_control`, `test_vpn_review_fixes`, `test_vpn_servers_tab`, `test_macaddr`, `test_quit_privileged`, in `subprocess.run(timeout=280)` | 363 passed, 2 skipped, 22.2 s |
| New tests on the parent's code | `git show 6280c8f:…/proxychain.py > …/proxychain.py`, run both files, `git checkout --` | 10 failed, 99 passed. The 2 that pass are the lchmod-fallback tests, as the commit says. |
| Hand mutants | 18, one at a time, each replacement asserted to match exactly once; the 2 files run with `-x` in `subprocess.run(timeout=90)`; `git checkout --` after each | 16 killed, 2 survived, 0 hangs |
| Loader probes | scratch script, `VPN_AGENT_STATE_DIR` in the session scratchpad | see "Security and behaviour" |
| Full suite | not run (change is local to these files) | Commit's "3223 passed … 1 failed (known)" **not verified** |
| Tree clean at end | `git status --short` | empty |

No live MAC, Wi-Fi, root, tunnel or network action was run. No `.env` value was read. No process was killed.

## Each fix against its finding

| Finding (review 3) | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P3-1: notice lost on the next launch | `proxychain.py:177-181`: no regular `proxychain.json` + a copy → "it was moved to <newest> earlier" until the first save | Yes. Two `PrivacyTab()` in a row keep the notice in the summary and on the status line. A save ends it. | `test_the_unreadable_chain_notice_survives_a_restart`, `test_a_missing_chain_names_the_newest_earlier_copy[×3]` (M3, M4, M14) |
| P3-2: "readable only by you" overstated; folder may be 0755 | `ensure_private_dir(path.parent)` before the move (`:199-202`); CHANGELOG `:53` and docstring say "where possible"; comment `:218` says "best-effort" | Yes. `_harden_dir` already swallows `OSError` (`server/paths.py:196-204`), so the extra `try` is belt and braces. | `test_the_folder_is_made_owner_only_before_the_move` (M11) |
| P3-3: chmod follows a hard link; lstat/chmod window | `S_ISREG and st_nlink == 1` (`:212`), then `follow_symlinks=False` where supported (True here) | Yes. Probe: a hard link to an outside 0644 file is moved and the outside file stays 0o644. Plain chmod remains on Linux, as documented. | `test_a_hard_linked_chain…` (M8), `test_the_chmod_never_follows…` (M9), `test_without_lchmod_…` ×2 (M10, M18) |
| P3-4: broken counter hangs the suite | `max(existing)+1` (`:194`), cap `MAX_UNREADABLE_COPIES` (`:195-197`), `lexists` before rename (`:204-205`) | Yes. No loop is left. Removing the cap or the `lexists` check fails by name, not by hanging. | `test_the_number_of_unreadable_copies_is_bounded` (M5, M6, M17), `…numbered_above_the_newest_even_after_a_gap` (M13), `test_an_existing_copy_name_is_never_replaced` (M7) |
| P3-5: wording | CHANGELOG, docstring, comment | Yes | n/a |

**Design choices, judged:**
- **Notice derived from disk:** right. Nothing deletes `proxychain.json` on its own (`git grep` shows only `chain_path`, `load_chain*`, `save_chain`), so "missing + copy" reliably means "moved and not saved since".
- **Not a regular file + copy:** a directory, FIFO or dangling link at `proxychain.json` with a copy beside it gives "it was moved to … earlier". That is true and harmless. A directory then makes the save fail with "Could not save the chain", which is pre-existing and reported separately. A FIFO is never opened, so nothing hangs. Untested (P3-2).
- **max+1 rather than first free gap:** right. It keeps "highest = newest", and the `lexists` check covers a listing that failed.
- **`lexists` then `replace`:** a same-user window remains between the two calls. It cannot be closed portably from Python without `renamex_np(RENAME_EXCL)`, and it needs same-user write access to a 0700 folder. Not a finding.
- **chmod conditions:** right. A hard-linked copy keeps its mode, but it now sits in a 0700 folder.

## Security and behaviour (probes, scratch state dir)

| Probe | Result |
|---|---|
| `proxychain.json.unreadable-²` present (missing chain, or broken chain) | **`ValueError: invalid literal for int() … '²'` escapes `load_chain_checked`** (P3-1). `load_chain()` is unaffected. |
| Directory at `proxychain.json` + copy | "it was moved to proxychain.json.unreadable earlier"; `save_chain` → `IsADirectoryError` (pre-existing) |
| FIFO at `proxychain.json` + copy | same notice; returns at once, no read |
| Dangling symlink + `-4` copy | "…unreadable-4 earlier" |
| A directory named `…unreadable-7` + broken chain | moved to `-8`; the directory is untouched |
| A single hand-made `…unreadable-1000` + broken chain | "too many earlier unreadable copies"; the file is left in place. The cap counts the highest number, not how many copies exist; a hand-made name only. |
| `…unreadable-02` + broken chain | read as 2; moved to `-3` |
| Mode-000 file holding `"password": "s3cret"` | moved, copy is 0o600, reason does not contain the secret |
| Hard link to an outside 0644 file | moved; outside file stays 0o644 |

The reason text still never contains file contents. Copy names are local file names chosen by Sentinel or the owner.

## Accuracy

- **Commit message.** "12 new cases; 10 fail on the parent" is verified. "15/15 hand mutants" are not named, so not verified; this review's 18 left 2 survivors. The full-suite counts were not verified.
- **CHANGELOG.md:50-56.** Accurate. "One past the 1,000th copy" means the highest number, which matches `:195`.
- **Docstring `proxychain.py:166-175`.** Accurate.
- **UI wording (`ui/panels/vpn_privacy.py:53-55`).** On a later launch the summary reads: "Could not read the saved proxy chain in <folder>: it was moved to proxychain.json.unreadable earlier. The chain starts empty." Readable, but this launch tried no read, and the original reason is gone (P3-3).
- **TODO.md:156 (leaked `head`).** Mostly accurate, with three errors (P3-4):
  - **The guard part is correct.** The scripts' `[ -f ]` guard keeps `head` off a FIFO. A live orphan, `bash` pid 65541 (ppid 1), carries the mutant `true || unverified "$p is not a regular file…"` and is blocked in `head -c 1048577 -- …/abs.crt` (pid 65542). The 7-day `head -c 262145 -- …/pipe.conf` orphans have no surviving parent, so the mutant is inferred for them, not seen.
  - **"Both tests live on the unmerged branch":** only `test_a_fifo_source_is_refused_without_reading_it` is on the branch. `test_a_data_file_that_is_not_a_small_regular_file_starts_nothing` exists only at that worktree's detached HEAD `03b2d57`, past the branch tip `bf59b41` (the reviewer wrote 9 commits; `git rev-list --count bf59b41..03b2d57` gives 28 — author's correction). `git branch -a --contains 03b2d57` returns nothing, so that commit is on no branch.
  - **"`subprocess.run(timeout=…)` kills only bash":** true for the old orphans, but in the live case `bash` itself survived (ppid 1, same pgid 43010, 1 h 31 m old). Pytest was killed before the helper's 60 s timeout fired, most likely by an outer mutation runner. The proposed `start_new_session` + `killpg` + finaliser fix does not cover a pytest killed by SIGKILL.
  - The first sentence still says the tests leak "each run", which contradicts the diagnosis.

## Mutant results

| # | Mutant | Result | Killed by |
|---|---|---|---|
| M1 | `isdigit()` suffix check dropped | **Killed** | `…names_the_newest_earlier_copy[names0]` |
| M2 | `number >= 2` → `>= 1` (accept `-1`) | **Killed** | same |
| M3 | oldest copy named (`min`) | **Killed** | `…[names1]` |
| M4 | restart notice never returned | **Killed** | `…[names0]` |
| M5 | cap `>` → `>=` | **Killed** | `…copies_is_bounded` |
| M6 | cap removed | **Killed** | same |
| M7 | `lexists` check removed | **Killed** | `test_an_existing_copy_name_is_never_replaced` |
| M8 | `st_nlink == 1` dropped | **Killed** | `test_a_hard_linked_chain…` |
| M9 | `follow_symlinks=False` dropped | **Killed** | `test_the_chmod_never_follows…` |
| M10 | no-lchmod chmod removed | **Killed** | `test_without_lchmod_a_regular_copy…` |
| M11 | `ensure_private_dir` removed | **Killed** | `test_the_folder_is_made_owner_only…` |
| M12 | first copy named `-1` | **Killed** | `test_a_wrongly_typed_saved_chain_loads_as_empty[…]` |
| M13 | `len(copies)+1` instead of `max+1` | **Killed** | `…numbered_above_the_newest_even_after_a_gap` |
| M14 | notice only when more than one copy | **Killed** | `…[names0]` |
| M15 | `not path.is_file()` → `not path.exists()` in `load_chain_checked` | **Survived** (P3-2) | none |
| M16 | `ensure_private_dir` `except OSError` → `PermissionError` | Survived, low value: `_harden_dir` already swallows | none |
| M17 | cap reason loses "will be replaced…" | **Killed** | `…copies_is_bounded` |
| M18 | `S_ISREG` dropped | **Killed** | `test_without_lchmod_a_linked_chain…` |

## Findings

### P3-1: a copy name with a non-ASCII digit means the Privacy tab is not built
`agents/vpn_agent/services/proxychain.py:156-157`, called from `:178` and `:194`, unguarded at `ui/panels/vpn_privacy.py:50`.

`str.isdigit()` is true for `'²'`, but `int('²')` raises `ValueError`. Probe: with `proxychain.json.unreadable-²` in the state folder, `load_chain_checked()` raises in both branches (missing chain, and broken chain). This is a new crash path: the parent's loop only called `exists()`. It is the same failure class review 2 P3-1 fixed for `RecursionError`. If the tab still opens inside the main window's construction (`main.py:2854-2886`, per the earlier review), Sentinel may not start (not verified by launching). Reaching it needs a hand-made file in the owner's 0700 folder.

Fix: accept only ASCII digits (`s = name[len(base)+1:]; if s.isascii() and s.isdigit():`), or wrap the `int()` in `try/except ValueError: continue`. Add `"proxychain.json.unreadable-²"` to the ignored names in `test_a_missing_chain_names_the_newest_earlier_copy`.

### P3-2: no test covers a non-regular `proxychain.json` with a copy beside it
`proxychain.py:177`; mutant M15.

Changing `is_file()` to `exists()` passes every test. With a FIFO at `proxychain.json`, that mutant would block `read_text()` on the UI thread at start-up, because opening a FIFO with no writer never returns. A directory would be moved aside as if it were a chain. Today's code is right; nothing pins it.

Fix: parametrize a test over a directory, a FIFO (`os.mkfifo`) and a dangling symlink at `chain_path()`, plus a `.unreadable` copy. Assert the "…earlier" reason, that the copy is untouched, and that nothing was moved. Run the FIFO case in a thread with `join(timeout=5)` so a regression fails instead of hanging.

### P3-3: the notice on a later launch is slightly off
`ui/panels/vpn_privacy.py:53-55` with `proxychain.py:180`.

It reads: "Could not read the saved proxy chain in <folder>: it was moved to proxychain.json.unreadable earlier. The chain starts empty." This launch did not try a read, and the original reason (for example "not valid JSON") is gone.

Fix (optional): give the restart case its own sentence, for example "An earlier saved proxy chain could not be read and was moved to <name> in <folder>. The chain starts empty until you edit it." That means returning a distinguishable reason, or a third value, from `load_chain_checked`.

### P3-4: the leaked-`head` TODO diagnosis is wrong in three places
`TODO.md:156`.

(a) "Both tests live on the unmerged branch `claude/intelligent-archimedes-e15a0e`": the data-file test exists only at detached `03b2d57` in that worktree, which no branch contains. The fix target is that worktree's HEAD, and those commits are only reachable from a detached HEAD.

(b) "`subprocess.run(timeout=…)` kills only bash": the live orphan `bash` 65541 (ppid 1) is still alive with its `head` child, so pytest itself was killed first. `killpg` in the helper plus a fixture finaliser would not catch that.

(c) The opening sentence still says "each run", which contradicts the diagnosis.

Suggested fix, which covers both ways of being killed: have each FIFO test hold a writer end open (`os.open(fifo, os.O_RDWR | os.O_NONBLOCK)`) and close it at teardown. A `head` that gets past a mutated guard then sees EOF when the test ends or its process dies. Also have mutation runners start pytest with `start_new_session=True` and `os.killpg` the group on timeout. Reword (a) to (c). The existing orphans were left running.

### Not a finding
- The `lexists`/`replace` window (same user only).
- A hard-linked copy keeps its mode (it is now in a 0700 folder).
- The cap counts the highest number rather than how many copies exist (a hand-made name only).
- `-02` is read as 2.
- The redundant `try` around `ensure_private_dir` (M16).

**Not verified:** the full suite; the commit's 15 mutants; Linux (no lchmod) behaviour beyond the monkeypatched tests; launching the app with the P3-1 file; the contents of `docs/company/app-context.md`, which is missing from this worktree.

---

# Addendum: independent review of a571747 (answers P3-1, P3-2 and P3-4 above; P3-3 deferred)

Reviewer: Opus 5.5, read-only (`code-reviewer`), 2026-11-05; a different agent from the author. Saved by the author's session from the reviewer's report.

**Verdict: ready to merge. No P0, P1 or P2 findings.** P3-1, P3-2 and P3-4 are closed. One new P3 (TODO wording), fixed in the commit that adds this addendum.

| Check | Method | Result |
|---|---|---|
| Target suites | `pytest -q tests/test_vpn_socks_chain.py tests/test_vpn_privacy_tab.py` | 113 passed in 1.97 s |
| FIFO unblock path | `-k "not_a_regular and fifo"` under the `exists()` mutant | `Failed: load_chain_checked() opened a FIFO` in 6.4 s; no ENXIO, no thread-exception warning, no leftover thread; the writer fd is closed at once |
| Other names that can make the loader raise | read `_unreadable_copies` | None: after `isascii() and isdigit()`, `int()` only sees `[0-9]+` (at most ~228 digits, under the 4300-digit limit); empty suffix, non-UTF-8 (surrogate-escaped) names, `-0` and `-1` are skipped |
| TODO.md:156 facts | `git grep`, `git branch -a --contains`, `git worktree list`, `ps` (read-only) | Accurate except the pinned SHA (P3-1 below); orphan `bash` 65541 (ppid 1) still carries the `true \|\| unverified` mutant with its `head` blocked |
| TODO.md:139 | against the report and diff | Accurate |
| Tree at end | `git status --short` | empty |

No live MAC, Wi-Fi, root, tunnel or network action was run; no `.env` read; no process killed.

| # | Mutant | Result | Killed by |
|---|---|---|---|
| A | `isascii()` dropped | Killed | `test_a_hand_made_copy_name_never_stops_the_loader` (alone); `…names_the_newest_earlier_copy[names0]` with `-x` |
| B | `isdigit()` dropped | Killed | `…names_the_newest_earlier_copy[names0]` |
| C | `is_file()` → `exists()` | Killed | `[directory]`, `[fifo]` (FIFO in ~6 s by `pytest.fail`) |
| D | `is_file()` → `os.path.lexists()` | Killed | `[directory]`, `[fifo]`, `[dangling-link]` |
| E | `exists() or is_dir()` | Killed | `[fifo]` only (6.8 s), so the FIFO case has value of its own |
| F | `int(suffix) if suffix.isdigit() else 0` | Survived, equivalent (the guard above filters) | none |

### P3-1 (addendum): TODO.md:156 pinned the other worktree's detached HEAD at a SHA that had moved on
The worktree `.claude/worktrees/intelligent-archimedes-e15a0e` was at `3ca1f6c` (parent `03b2d57`); neither is on any branch, and the worktree is in active use. Fix: say "only on the detached HEAD of that worktree (from `03b2d57` on), which no branch contains", and note that removing the worktree without a branch loses those commits. **Fixed** in the commit that adds this addendum.

### Checked, not findings
- The TODO's suggested fix (a held-open `O_RDWR | O_NONBLOCK` writer end) is sound: `head`'s open succeeds and its read sees EOF at teardown or when pytest dies, even by SIGKILL.
- A FIFO-test worker that raises instead of hanging still fails the test (`IndexError`), less clearly; the raise path is pinned on the main thread by `test_a_hand_made_copy_name…`.
- The unblock `os.open` would raise for the directory or dangling-link cases, but is only reached when the worker is alive, which cannot happen for those.
- No CHANGELOG entry needed: the `²` crash came in with 924d1f5 on this unmerged branch and never shipped.
- `Path.is_file()` can still raise `PermissionError` on 3.11 when the state folder cannot be searched; pre-existing, folder is 0700 and owner-owned. Out of scope.

**Not verified:** the full suite (run by the author); Linux behaviour; launching the app.

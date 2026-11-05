# Review: unreadable MAC record (`0ece5cf`) and failed hop add (`c862b42`)

Reviewer: Sonnet 5.5, read-only (`code-reviewer`), 2026-11-05; a different agent from the author
(Opus 5.5). Saved by the author's session from the reviewer's report. Branch
`claude/tunnel-open-items-a16b38`, base `b6866d1`.

Scope: TODO "An unsearchable Tunnel state folder still stops the Privacy tab from being built"
(`0ece5cf`) and `docs/audit/chain-notice-review-2026-11-05.md` addendum 5 P3-5 (`c862b42`).

Housekeeping: `docs/company/app-context.md` does not exist in this repo; reviewed against
`CLAUDE.md`, `AGENTS.md` and the brief. Python 3.11.15 only, so the 3.14 behaviour of `exists()`
(answers False) was not exercised; the fix no longer calls it.

## Verdicts
- **0ece5cf: accept.** No P0, P1 or P2.
- **c862b42: accept.** No P0, P1 or P2.

## Findings

### P3-1: Restore hardware address with an unreadable record leaves the record behind
`macaddr.py` settle block in `_change`, and `_forget`. With the folder locked, Restore hardware
address succeeds but `_forget` cannot drop the entry. Once the folder is readable again, the next
launch sees `current == hardware != original` and, when the original was a private Wi-Fi address,
offers to "restore" that address. Fails safe: the offer can be declined. But the notice's "Restore
hardware address still works" leaves this out. Fix: say so in the notice, or forget it later.

### P3-2: `_remember` can still raise `RecordUnreadable` out of `set_mac`
`macaddr._remember`, `main.py` `_restore_changed_hardware_addresses`. If the folder is locked
between the guard read in `_change` and `_remember`, `set_mac` raises instead of returning
`(False, message)`. Fail closed (root never runs, nothing is overwritten); the tab shows
"Failed: [Errno 13] …". Narrow window, untested. Fix: catch it around `_remember` and return the
same refusal.

### P3-3: the "unsaved edit stays in memory" class remains in the sibling chain handlers
`vpn_privacy.py` `_mode_changed`, `remove_hop`, `move_hop`. A refused or failed save still
changes the in-memory chain (a removed hop leaves the table, Test chain and Write conf, but not the
disk; a restart brings it back). No password is lost. Fix: snapshot the chain before the edit and
restore it when the save fails.

### P3-4 (pre-existing, not a regression): a damaged record is deleted
`macaddr._read_entries` returns `({}, True)` for a record that fails JSON/UTF-8 decoding;
`pending_restores` then calls `_save_record({})`, which unlinks it, so a partly corrupt file
holding a real original is erased. Not pinned by a test (reviewer mutant M6 survived). Fix: a
test pinning it, or move the file aside as `.corrupt` instead of deleting it.

## Caller audit (all fail closed on `RecordUnreadable`)
| Caller | Behaviour | Result |
|---|---|---|
| `main.py` `_restore_changed_hardware_addresses` | caught by the generic handler, redacted log line, quit proceeds, nothing forgotten | OK |
| `PrivacyTab()` | catches it, notice in the Hardware address box and on the status line, no restore offer | OK |
| tab offer read | catches `OSError`: "Could not read the record…" | OK |
| tab `restore_all` (in the gate) | raises; the worker's error signal shows "Failed:" | OK |
| `macaddr._forget` | returns, record untouched | OK |
| `macaddr._remember` | raises before root runs (P3-2) | OK, fail closed |
| `macaddr._change` guard | refuses anything but the hardware address | OK |
| `macaddr._change` settle read | caught; `_forget` no-ops (P3-1) | OK |
| `dialogs.reset_mac_note` | catches `OSError`, says the record could not be read | OK |

Notes: no MAC address appears in any new message (file name, folder and `strerror` only; the
refusal test asserts it). Record reads and writes in `_forget`, `_remember` and `pending_restores`
stay under `_lock`; the unlocked guard and settle reads in `_change` are unchanged, and a race ends
in a refusal, never an overwrite. No `exists()`/`is_file()` remains on the MAC record path; the
only other reader, `scripts/release_smoke.py`, already treats an unreadable record as present.

## Reviewer's mutants
Baseline `tests/test_macaddr.py` + `tests/test_vpn_privacy_tab.py`: 276 passed. Each mutant was
run with `-x` and the file restored; `git status` clean afterwards.

| Mutant | Result |
|---|---|
| `_forget` lets the error propagate | killed |
| `_forget` treats unreadable as empty and stale (would erase) | killed |
| `_change` allows a change with an unreadable record | killed |
| `PermissionError` read as no record | killed |
| `NotADirectoryError` dropped from "absent" | killed |
| damaged JSON: stale True → False | survived (P3-4, pre-existing) |
| tab does not put the notice on the status line | killed |
| notice label always hidden | killed |
| reset note returns `""` on unreadable (first form) | equivalent as written (`'' or (...)`); replaced by the next |
| reset note returns `""` before the text | killed |
| settle read uses `applied` as the original | survived, equivalent (`_forget` no-ops when unreadable) |
| settle read unguarded | killed |
| `pending_restores` swallows the error, returns `[]` | killed |
| tab offer read stops catching `OSError` | killed |
| `add_hop` clears the password when not saved | killed |
| `add_tor_hop` rollback removed | killed |

Author's own mutants: 0ece5cf 11/11 killed; c862b42 4/5 killed, the survivor (skipping the
table refresh after the rollback) equivalent.

## Resolutions (author's session; not re-reviewed)
- **P3-1 fixed (wording):** the notice now ends "Restore hardware address still works, but if
  Sentinel later offers to put back an earlier address, you can say no."
- **P3-2 fixed:** `_change` catches `RecordUnreadable` from `_remember` and returns the same
  refusal (`_unread_refusal`); test `test_a_record_locked_after_the_check_still_refuses_the_change`.
- **P3-3 deferred:** TODO.md item (Remove, Move and the mode box roll back on a failed save).
- **P3-4 deferred (pre-existing):** TODO.md item (move a damaged record aside instead of deleting it).
- The full suite found `tests/test_reset_fence.py::test_the_final_confirmation_survives_an_unreadable_record`
  pinning the old silent note. It now runs with an `OSError` (the note says the record could not
  be read) and a `RuntimeError` (no note); both still reach the one confirmation. This changes
  what the test asserts rather than weakening it: the old silence is the behaviour item 1 removes.
- Author's mutants for these: 3/3 killed (`_remember` unguarded, reset note silent on `OSError`,
  reset note on any error).

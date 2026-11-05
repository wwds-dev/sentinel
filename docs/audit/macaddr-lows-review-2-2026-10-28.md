# MAC changer review 2: `06e2806` (review fixes) and `e4caccd` (Low 10 root refusal)

- **Date:** 2026-10-28
- **Reviewer:** Claude Opus 5.5, independent session (`code-reviewer` + `security-engineer`).
  I wrote neither commit.
- **Branch / worktree:** `claude/macaddr-lows-2-18b3f7` at `0b7170b`. Diffs reviewed:
  `git show 06e2806`, `git show e4caccd`, `git range-diff` against `6d66d67`, and
  `git diff afb92bd e4caccd` (the other resolution of the same cherry-pick, on
  `fix/macaddr-root-refusal`).
- **Mode:** read-only. No source edited, nothing committed. Mutants and probes ran on a copy of
  the worktree in the session scratchpad. No MAC, Wi-Fi, root or tunnel action was run; every
  privileged call in the probes was stubbed.
- **Tier:** C (Local-model plan in `TODO.md`). This is the independent review it needs.

## Verdict

**Ready to merge after one documentation fix (P2-1, one sentence in `docs/agents/vpn.md`).**
No P0 or P1.

- `06e2806` closes what it says it closes. The P2-1 guard is correct for every *new* change, and
  each of its tests fails when its fix is reverted.
- `e4caccd` does what the owner decided. Every gated caller turns the refusal into a clean failure
  with a clear message. Nothing is half-applied: the kill-switch marker is put back, the MAC
  record is kept or dropped correctly, and the native installer is never written.
- The Tunnel fail-closed rules still hold. A refused Disarm leaves the block up. A refused Connect
  leaves traffic where it was. A refused restore keeps the record.
- The cherry-pick resolution is right in substance. It keeps two duplicate tests (P3-4).
- Nothing from `7c84dca` or `c807267` regressed. Full suite: **3123 passed, 14 skipped, 1 failed**.
  The one failure is the known unrelated one, `test_providers_registry`, which starts the installed
  LM Studio CLI. Mutants: **15 of 16 killed**. The one survivor is a test gap (P3-2), not a defect.

## Evidence

### Suite

```
../../../.venv/bin/python -m pytest -q -p no:cacheprovider        (at 0b7170b)
FAILED tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing
1 failed, 3123 passed, 14 skipped, 1 deselected in 221.16s
```

The failure is `ForbiddenInTests: test tried to start a program: ['/Users/as/.lmstudio/bin/lms', ...]`.
It is the known one and has nothing to do with these commits. `ruff` is not installed, so it was not run.

### Mutants (scratch copy; `tests/test_macaddr.py`, `test_privileged_runner.py`, `test_vpn_privacy_tab.py`)

| # | Mutant | Result | Killed by |
|---|---|---|---|
| 1 | P2-1 guard removed (`if False:`) | killed | 4 tests: `test_a_current_address_that_cannot_be_recorded_refuses_the_change[×3]` (06e2806) and `test_a_current_address_that_could_not_be_put_back_refuses_the_change` (e4caccd) |
| 2 | P2-1 hardware exemption dropped | killed | `test_going_back_to_hardware_needs_no_recordable_current`, `test_going_back_to_the_hardware_address_needs_no_record_so_is_allowed` |
| 3 | P2-1 guard checks syntax only (`MAC_RE`) | killed | the 06e2806 parametrised test |
| 4 | P3-1: combo item text not refreshed | killed | `test_a_declined_change_leaves_the_fresh_address_on_screen` |
| 5 | P3-1: `except OSError` in `_fresh_interface` made unreachable | killed | `test_a_failing_fresh_read_is_a_refusal_not_a_crash` |
| 6 | P3-1: vanished interface not refreshed out | killed | `test_a_vanished_interface_is_dropped_from_the_list` |
| 7 | L10: `run_as_root` refusal removed | killed | `test_a_gated_action_is_refused_while_sentinel_runs_as_root[script]` |
| 8 | L10: `run_argv_as_root` refusal removed | killed | `...[argv]` |
| 9 | L10: condition inverted in both | killed | `test_root_and_sudo_paths_pass_argv_without_a_shell` |
| 10 | L10: `ROOT_REFUSAL` not a settled outcome in `set_mac` | killed | `test_a_mac_change_is_refused_as_root_and_leaves_no_record` |
| 11 | L10: a root refusal forgets the record unconditionally | **survived** | (P3-2) |
| 12 | L10: native deploy as root streams the installer again | killed | `test_a_native_deploy_is_refused_as_root_before_the_installer_is_written` |
| 13 | L9: listing error not shown | killed | `test_refresh_survives_an_os_error_but_not_a_bug` |
| 14 | regress M5 (`c807267`): generated first octet may be multicast | killed | `test_every_generated_address_is_locally_administered_unicast` |
| 15 | regress L8 (`c807267`): all-zero accepted | killed | P2-1 tests |
| 16 | regress High 1 (`7c84dca`): no record before root | killed | `test_a_change_records_the_original_before_root_runs` |

## 1. `06e2806`: the author's fixes for the first review

**Numbering note.** `06e2806`'s labels (P2-1 = unrecordable current address; P3-1 = fresh-read
tests; P3-3 = comment; P3-4 = DEHASHED; P3-5 = test count) do not match the review document
committed at `0b7170b`. In that document P2-1 is the DEHASHED corruption, P2-2 is the stderr leak,
P3-1 is the comment, and so on. The fixes match a different version of that review. Each claim below
was checked against the code, not against the labels. See P3-5.

- **P2-1 (unrecordable current address): fixed for new changes.**
  `agents/vpn_agent/services/macaddr.py:381-386` refuses before `_remember` and before root when the
  target is not the hardware address and `interface.current` fails `is_valid`. That covers the
  all-zero, multicast and broadcast addresses that `_valid_entry` (`:238-242`) would silently drop.
  The guard and the `_remember` condition (`:394`) both use `address != interface.hardware`, so the
  exemption is exactly the case that needs no record. Tests: `tests/test_macaddr.py:665-680`. Mutants
  1-3 killed. One edge case is left: a *restore* to a recorded original (P3-1).
- **Fresh-read tests (the commit's "P3-1"): fixed.** `ui/panels/vpn_privacy.py:271-273` now
  refreshes the combo item's text as well as the labels. The three new tests in
  `tests/test_vpn_privacy_tab.py:383-405` cover the `OSError`, vanished-interface and
  declined-dialog branches. Mutants 4-6 killed.
- **Comment (the commit's "P3-3"): superseded.** The rewritten comment in `privileged.py` was
  dropped by the cherry-pick resolution, correctly (see §2). Nothing is left to check.
- **DEHASHED (the commit's "P3-4"): fixed.** `TODO.md:92` says `DEHASHED_API_KEY` again; the diff
  touches that one token.
- **Test count (the commit's "P3-5"):** the TODO line now cites 3114 for `06e2806`. This run
  measured 3123 at `0b7170b`, which includes `e4caccd`'s tests. Consistent.

## 2. `e4caccd`: refuse gated actions when Sentinel runs as root

### Every gated caller

All privileged execution goes through `agents/vpn_agent/services/privileged.py`
(`git grep -E "osascript|administrator privileges|\"sudo\""` finds no other path outside it).
Every caller with `allow_cached_sudo=False`:

| Caller | Outcome as root | Half-applied state? |
|---|---|---|
| WireGuard Connect/Disconnect, `services/vpn_connection.py:146,168` (via `_default_run_as_root`, `:48`) | `{"success": False, "error": ROOT_REFUSAL}` | No. Nothing written. |
| OpenVPN Connect, `services/openvpn_manager.py:214` | `error = ROOT_REFUSAL`. `_settle_log` returns at once because the log does not exist (`:150`), so there is no 15 s wait | It deletes a stale log and device file first (`:205-206`). Same as any failed start, and `is_running()` was false |
| OpenVPN Disconnect, `openvpn_manager.py:271` | `error = ROOT_REFUSAL`; tracking kept | No |
| Kill switch Arm, `killswitch.py:729` | not ok, no "may still have happened", so the previous marker is restored (`:741-745`) | No. The user-readable recovery rules file is written first, which is harmless |
| Kill switch Disarm, `killswitch.py:786` | marker put back (`:796-799`); "Failed to disarm … Run manually: <recovery>" | No. The block stays up, which fails closed |
| Kill switch Verify, `killswitch.py:890` | `VERIFY_UNKNOWN`, nothing recorded | No |
| MAC change / restore, `macaddr.py:398` | `False, ROOT_REFUSAL + " <dev> reports <addr>"`. The refusal counts as settled (`:409`). A fresh change's own record is dropped because the address is unchanged. A pre-existing record is kept, since the current address is neither the hardware nor the original. Probed (below) | No |
| Native server install, `server/deploy.py:325-329` | `DeployResult(False, error=ROOT_REFUSAL)` before the installer is written or streamed | No (test asserts no `install-*` file) |

Probe on the scratch copy (`root` stub returns `ROOT_REFUSAL`, record `{en5: 3a:11:22:33:44:55}`,
current `02:11:22:33:44:55`):

```
RESULT False Sentinel is running as root, ... Nothing was changed. ... en5 reports 02:11:22:33:44:55. {'en5': '3a:11:22:33:44:55'}
```

The record survives a root-refused restore, which is the behaviour the Tunnel rule needs. No test
pins it (P3-2).

No caller crashes. Every message is `ROOT_REFUSAL`, which says what happened, that nothing changed,
and what to do instead (`test_the_refusal_says_what_happened_and_what_to_do`).

### What must run as root and is now blocked

- **Quit-time MAC restore** (`main.py:5997-6011` → `restore_all_recorded` → `set_mac`). It is now
  refused as root. This is not silent data loss: the record stays (probe above), the next normal
  launch offers it again, and a reboot clears the change anyway. A root Sentinel cannot create such
  a record, because its own changes are refused. So this only happens when an earlier, non-root
  session crashed or had its restore declined, and Sentinel was then started with `sudo`. At quit
  the only notice is a line on stderr (P3-3).
- **Kill-switch teardown:** there is none at quit (`grep -i killswitch main.py` finds nothing). A
  refused Disarm keeps the block, which is the fail-closed direction.
- **Companion default** (`allow_cached_sudo=True`): still runs directly as root
  (`test_the_companion_default_still_runs_directly_as_root`). No caller uses it today
  (`git grep allow_cached_sudo`).

### Tunnel rules (`CLAUDE.md`)

- Fail closed: held (see the table). No refusal turns into a clear-net fallback.
- Least privilege: strengthened. A root GUI now runs nothing gated.
- Argument lists: unchanged. `run_argv_as_root` still passes argv; the root branch adds no shell.
- No logging: no new writer. The refusal text holds no secret. The `" <dev> reports <addr>"` suffix
  still reaches stderr at quit. That is the open P2-2 from the first review (unredacted address on
  stderr), which now also fires on a root refusal.
- Tests including failure cases: yes for the refusal itself. One gap: P3-2.

### Cherry-pick resolution

- **`privileged.py`:** taking `6d66d67`'s refusal and dropping `06e2806`'s "Consent is the
  caller's job …" comment is right. The comment described the old as-root behaviour that `e4caccd`
  removes. The docstring (`:116-118`) now carries the reasoning.
- **`macaddr.py`:** the guard appears once (`:381-386`) with `06e2806`'s wording. Both tests assert
  "could not record", and both pass.
- **Tests:** the resolution kept `6d66d67`'s two P2-1 tests (`tests/test_macaddr.py:344-359`)
  beside `06e2806`'s (`:665-680`). They test the same thing (P3-4). The other resolution of the same
  cherry-pick, `afb92bd` on `fix/macaddr-root-refusal`, dropped them. `git diff afb92bd e4caccd`
  shows that is the *only* difference between the two. Merge one of the two branches, not both.

### Regressions from `7c84dca` / `c807267`

None found. High 1 (record before root), M5 (locally administered unicast) and L8 (zero and
multicast rejected) mutants are killed (#14-16). The full suite is green apart from the known failure.

## P0 / P1

None.

## P2

**P2-1 · `docs/agents/vpn.md:45` · the agent page states the opposite of the new behaviour.**
It says: "If Sentinel itself is running as root there is nothing to ask for and the command runs
directly". `e4caccd` updated `README.md:221-224` and `docs/training/tunnel.md:56-58` but missed this
page, which is Tunnel's own agent documentation. The rule describes a security boundary, so the page
should not contradict it. Fix: replace that sentence with the README's wording (refused, nothing
runs, quit and reopen as your normal user). The historical `docs/specs/audit-tier2-privileged-layer.md:29`
and `docs/qa_audit/tunnel.md` rows describe the code as it was then and can stay.

## P3

- **P3-1 · `agents/vpn_agent/services/macaddr.py:381` · the P2-1 guard also blocks a restore to a
  recorded original when the current address is all-zero or multicast.** `restore_all_recorded`
  calls `set_mac(device, original)`. When the original is a private Wi-Fi address (not the
  hardware), `address != interface.hardware`, so the guard applies even though a record already
  exists. `_remember` would skip it anyway ("the first original wins", `:301`). Probe (record
  `{en5: 3a:11:22:33:44:55}`, current `00:00:00:00:00:00`):
  `False "Sentinel could not record the address en5 is using now (00:00:00:00:00:00) for restoring.
  Nothing was changed." [] {'en5': '3a:11:22:33:44:55'}`. This fails safe: nothing runs and the
  record stays. But the message is wrong (there *is* a record), and `pending_restores` keeps offering
  a restore that can never run while the interface reports that address. It needs an unusual state
  to trigger. Fix: also skip the guard when `device in recorded()`, and add a test.
- **P3-2 · `tests/test_privileged_runner.py:155-167` · no test shows that a root-refused *restore*
  keeps an existing record.** Mutant 11 (`if output == ROOT_REFUSAL or (...)`, which forgets the
  record on any refusal) passes all 154 tests in the three files. The current code is right (probe
  in §2), but the property the quit-time path depends on is unpinned. Add a test like
  `test_a_failed_change_that_was_not_cancelled_keeps_the_record` with `ROOT_REFUSAL` and a
  pre-existing record.
- **P3-3 · `main.py:6009`, `ui/panels/vpn_privacy.py:320-325` · as root, the restore is refused
  and the user is told late or not at all.** At quit, the only notice is stderr, and that line also
  carries the address (the open P2-2). At launch, the leftover-restore offer still says "macOS will
  ask for your administrator password". The user says Yes, and only then sees the refusal. This is
  not a defect against the owner's decision. A one-line `os.geteuid() == 0` check before the offer
  ("Sentinel is running as root; open it as your normal user to restore …") would make it honest.
- **P3-4 · `tests/test_macaddr.py:344-359` · duplicate tests from the cherry-pick.**
  `test_a_current_address_that_could_not_be_put_back_refuses_the_change` is the zero case of
  `test_a_current_address_that_cannot_be_recorded_refuses_the_change[...]` (`:667`).
  `test_going_back_to_the_hardware_address_needs_no_record_so_is_allowed` is
  `test_going_back_to_hardware_needs_no_recordable_current` (`:674`) with one extra
  `len(calls) == 1`. Keep one of each, as `afb92bd` does.
- **P3-5 · traceability.**
  (a) `e4caccd`'s message still describes `6d66d67`'s tree. It says the commit adds the
  "Low 8 knock-on" guard, which `06e2806` already added here, and its `verified-by` (3116 passed,
  9/9 mutants) was measured on `6d66d67`, not on this tree.
  (b) `06e2806`'s finding labels and the `TODO.md:130` open list ("P3-2 off-thread interface read
  and P3-6 unredacted address on stderr") use a numbering that is not the one in the review
  committed at `0b7170b`. By that document, its P2-2 (stderr leak), P3-2 (`MAC_RE` ends in `$`,
  still true at `macaddr.py:41`), P3-3 (UI-thread read), P3-4 (unused `Qt`/`vpn_execution` imports,
  still at `vpn_privacy.py:11,20`) and P3-6 (`tests/manual_test_cases.md:289` still mentions
  `tunnel_audit.jsonl`) are open. The TODO line should name them by that document's numbers. Also,
  `manual_test_cases.md` has no step for "start with `sudo`; Connect, Arm and Randomise are refused".
  It is worth adding, since this behaviour can only be tested by hand on a real Mac.

## Not findings (checked)

- `TODO.md` on this branch does not mention `e4caccd`. Known (`a7df4d6` not cherry-picked).
- The OpenVPN connect refusal deletes a stale log and device file. That predates this change and
  is identical for a cancelled dialog.
- `refresh_interfaces` shows `OSError` text in the status label (`vpn_gate.py:28`, label only, never
  written to disk).

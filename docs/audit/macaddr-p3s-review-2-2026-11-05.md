# Independent review 2: MAC changer P3 fixes, branch `fix/macaddr-p3s` (1a51b23, b264aed)

- Date: 2026-11-05. Reviewer: code-reviewer (Opus 5.5). Read-only Tier C review. I did not write this code.
- Checkout: worktree `/Users/as/Documents/lab/active/sentinel/.claude/worktrees/macaddr-p3s`, branch `fix/macaddr-p3s`, HEAD `b264aed`.
- Scope, two commits only:
  - `1a51b23`: `main.py` `_restore_changed_hardware_addresses` and its test.
  - `b264aed`: the answers to `docs/audit/macaddr-p3s-review-2026-11-05.md` (P2-1, P3-1 to P3-5) in `privileged.py`, `macaddr.py`, `ui/panels/vpn_privacy.py`, `ui/dialogs.py` and three test files.
- Playbook: `docs/playbooks/code-reviewer.md`, `last-reviewed: 2026-10-14`, `status: current`. It is 22 days old, so not stale.
- `docs/company/app-context.md` is missing from the worktree. I read it from the main checkout, the same as the previous review did.

## Verdict

**Mergeable.**
- All six findings from the previous review are closed. Each closing change has a test that fails without it.
- `1a51b23` closes review 3 P3-4, and its test fails without it.
- No new P0, P1 or P2 problem was found.
- Three P3s remain: two small test gaps (surviving mutants) and one leftover piece of the unknown-boot case that existed before this branch.

Counts: **P0 0 · P1 0 · P2 0 · P3 3.**

**Working tree note:** `git status --short` was empty when I started. During the review, `TODO.md` in this worktree was changed by someone else at 14:30:12 (4 lines in the MAC changer item, marking the re-audit 2 P3s fixed). My mutation script never touches `TODO.md`. I left the file as it is. Whoever owns that edit should commit it.

## Evidence

| Check | Command | Result |
|---|---|---|
| MAC test set (7 files) | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -p no:cacheprovider -q --no-header tests/test_macaddr.py tests/test_vpn_privacy_tab.py tests/test_reset_fence.py tests/test_vpn_companion_libs.py tests/test_quit_privileged.py tests/test_audit_must_fix.py tests/test_privileged_runner.py` | **332 passed** in 21.9 s |
| Does the tighter regex still match the real dialog argv? | Scratch probe `probe_ps.py`: capture the real `run_with_dialog(shlex.join(argv), …)` osascript argv, give it to a sleeping `python -c` child, read the real `ps -axww -o command=` through `macaddr._changes_elsewhere()`. Two more children were given `do shell script`'s `sh -c <shlex string>` form and the root helper's own `/bin/bash -c ROOT_SCRIPT sentinel-mac …` form. A `grep -r "sentinel-mac en96 " .` child was the noise. | All three real forms match: `['en97', 'en98', 'en99']`. The noise (`en96`) does not. ps prints the script's newlines as `\012`. `shlex.join` leaves `sentinel-mac`, the device, the lowercase address and the flag unquoted (all are in shlex's safe set). The osascript form ends `… 02:ab:cd:ef:01:23 1" with prompt …`, so `[01]\b` holds before the closing quote. `_change` lowercases the address before building argv (`macaddr.py:485`), so `[0-9a-f]` cannot miss an uppercase address. |
| Does `errors="replace"` change other callers? | `git grep` for `privileged` users outside tests; read each output consumer | Callers: `macaddr` (`run`, `run_argv_as_root`), `killswitch.py:957`, `server/deploy.py:354`, `services/openvpn_manager.py:34`, `services/vpn_connection.py:45` (all `run_as_root`), and `main.py:5889` (constants only). Every comparison is with ASCII text: `"User canceled"` / `(-128)` → `"Cancelled."`, `_sudo_refused`'s `sudo:` lines, `"Timed out."`, `ROOT_REFUSAL`. `ok` still comes only from the return code. No caller catches `UnicodeDecodeError` or `ValueError` from these calls (grep). **Net effect:** a root command that succeeds but prints a non-UTF-8 byte now returns `(True, …)`. Before, it raised after the privileged action had already happened. That is an improvement, not a regression. |
| Is holding `_lock` across `_forget` safe? | Read `macaddr.py:246, 326-346, 379-396, 409-439`; `git grep "in_flight()\|_busy("` | `_lock` is an RLock, and `_forget` re-entering it is fine. The new outer `with _lock` adds only an in-memory dict check: `_forget` already took the lock and called `_read_entries` → `boot_id()` (sysctl, only when no good read is cached). No new lock is held across a subprocess. Nothing on the UI thread takes `_lock`: `in_flight()` has no non-test caller, and `recorded()` takes no lock. No lock ordering issue (one lock). |
| Can the `_offer_pending` refresh collide with `_gated` or loop? | Read `vpn_gate.py:40-110`, `vpn_privacy.py:241-245, 349-395` | `then(result)` runs after `release()` (`vpn_gate.py:99-101`), so `_gated` never sees the tab busy here and never shows "Busy". `refresh_interfaces` returns early while busy, which covers a restore that `_gated` started. Its result goes to `_interfaces_listed`, which never re-offers, so there is no loop. On the error path, `_say(error)` is not overwritten: `_read_off_thread` writes no status, and `_interfaces_listed` writes one only on a listing error. |
| Is the reset wording accurate? | Read `ui/dialogs.py:1316-1336`, `macaddr.py:255-256, 317-323` | Yes. The record lives in `paths.state_dir()`, which the reset erases. `recorded()` does not check whether the device is still changed, which is why the new "if it is still changed" is right. The note names devices only, never addresses (the test asserts `aa:bb:cc` is absent). |
| Hand mutants | Scratch `mutate.py` (below) | 19 planted, 14 killed, 5 survived (two are test gaps, see P3-1 and P3-2) |

## Each fix against its finding

| Finding | Fix | Status |
|---|---|---|
| Review 3 P3-4: the quit handler re-imports the module that just failed | `main.py:6012-6018`: an inner try, falling back to `type(exc).__name__` | **Closed** (Q1 killed by `test_the_quit_restore_survives_its_own_module_failing_to_import`, `tests/test_macaddr.py:1142`). The "no text" half is unpinned: see P3-1. |
| P2-1: a non-UTF-8 argv anywhere makes ps raise | `privileged.py:38` (`errors="replace"`), plus `macaddr.py:355-358` (`except ValueError: return None`) | **Closed** (P1 and P2 each killed by their own test; the two layers are tested separately). No other caller changes behaviour, as shown above. |
| P3-1: a tab shown during the offer read stays empty | `vpn_privacy.py:366-374`: refresh when nothing ran and `_loaded` | **Closed** (U1 and U2 killed). U4 (refresh even when the tab was never shown) is killed too, so the lazy first listing is kept. |
| P3-2: a forget after an in-flight snapshot (macaddr half) | `macaddr.py:432-436`: re-check `_in_flight` under `_lock` just before `_forget` | **Closed for the stated half** (L1 killed by `test_a_change_that_starts_during_the_check_is_not_forgotten`, `:1181`). The UI half (reserving the tab before `_ask`) is deferred by the author as latent. I agree: the only caller of the offer is still the 0 ms timer at construction. L2 (lock removed) survives, see Mutants. |
| P3-3: an earlier boot's entry stands in for a new change while the boot is unknown | `macaddr.py:497-504`: only hardware, or the recorded original of an entry for the same adapter | **Closed for the reported sequence** (B1, B2, B3 killed by `:1196` and `:1205`). Leftover: P3-3 below. |
| P3-4: the ps regex matches any mention | `macaddr.py:251-252`: full tail (device, address, `[01]\b`) | **Closed** (R1, R2, R4 killed; real argv forms verified live, above). Two parts of the shape are unpinned: P3-2. The second half of the suggested fix (naming at quit the devices skipped as busy) was not done, which the commit message says openly. The record is kept, so this still fails safe. |
| P3-5: reset guard untested; wording overclaims | `tests/test_reset_fence.py` new test raising `OSError`; `ui/dialogs.py:1324-1327` | **Closed** (D1 killed by the reworded test). D3 was not re-planted: I read the new test, and it raises `OSError` through `recorded()`, which a guard narrowed to `ImportError` would let escape. |

## Findings

### P3-1 The quit fallback's "no text" promise has no test
- **Where:** `main.py:6017` (`detail = type(exc).__name__  # no text: it may hold an address`); test `tests/test_macaddr.py:1142-1150`.
- **Failure scenario:** mutant Q2 (`detail = str(exc)`) passes all 332 tests, because the test asserts only that `"hardware address restore skipped"` is in stderr. A later edit that prints the exception text in that branch would not be caught. Today the risk is small. The fallback runs only when the import (or `redact_addresses`) fails, and an `ImportError` message names a module, not an address. But the comment states a rule that nothing enforces.
- **Fix:** in the same test, assert `err.strip().endswith("ImportError")` (or that `"halted"` / `"None in sys.modules"` is absent).

### P3-2 Two parts of the new ps shape are unpinned
- **Where:** `macaddr.py:251-252`; test `tests/test_macaddr.py:1171-1179`.
- **Failure scenario:**
  - R3: address part loosened to `\S+`. Survives, because no noise line has a 0/1 flag after a non-address, so `echo sentinel-mac en5 x 1` would again count as a pending change.
  - R5: trailing `\b` dropped. Survives, because no noise line has `… <address> 10`.
  - Either loosening brings back the false "busy" (P3-4 of the previous review), which skips a device at quit without saying so. That still fails safe: the record is kept.
- **Fix:** add `/bin/echo sentinel-mac en9 x 1` and `/bin/echo sentinel-mac en10 02:aa:bb:cc:dd:ee 10` to the `noise` string. The expected set `{"en5", "en6"}` stays the same.

### P3-3 While the boot is unknown, the "way back" exception may still apply an earlier boot's address (existed before this branch, now narrowed)
- **Where:** `macaddr.py:497-498` together with `:309-313` (an unknown boot keeps every entry) and `:526-527` (forget once applied equals the recorded original).
- **Failure scenario:**
  1. `kern.bootsessionuuid` cannot be read at launch.
  2. An earlier boot left `{"en5": original X, same hardware}`. In this boot en5 is on its own address Y (for example this boot's private Wi-Fi address), which Sentinel never changed.
  3. `pending_restores` offers `en5: Y → X`. If the user accepts, `set_mac("en5", X)` passes the new guard as "a way back", root sets X, and the entry is forgotten.
  4. Result: en5 carries an address from an earlier boot, and nothing records Y.
- **Before this branch** the same sequence ran (`restorable` alone opened it), so this is not a regression. `b264aed` removes the worse case (a random address replacing the record). It needs a sysctl failure at launch, the user's yes on a dialog that shows both addresses, and an address that is this Mac's own. Very low impact.
- **Fix (optional):** while `boot_id()` is `""`, have `pending_restores` keep entries but offer none whose `boot` is non-empty. Or say "may be from an earlier session" in the offer text. Otherwise record the leftover in TODO.md and close it.

## Not findings (checked)
- **`_offer` returning `_gated`'s bool:** mutant U3 (drop the `return`) is equivalent. When `_gated` starts the restore, the tab is busy and `refresh_interfaces` returns at once. The bool is explicit but has no observable effect. Harmless.
- **An extra listing:** when the tab was shown and listed before the offer read began, a no-op offer lists the interfaces a second time. This happens off-thread, once, at startup with a record present. A cost, not a defect.
- **Leftover race in P3-2:** a change that both starts and finishes between `list_interfaces()` and the re-check would still be forgotten against a stale listing. That window is milliseconds, and every change waits on the administrator dialog (`allow_cached_sudo=False`; a root Sentinel is refused). Not reachable.
- **Tunnel rules:** ps and the root helper are argument lists only; no `shell=True` or shell string was added. Root paths are unchanged. No address appears in kept output: the reset note names devices, and the quit stderr is redacted or shows a type name. Fail closed is kept: an unreadable ps gives `None`, so nothing is forgotten and changed devices are still offered.

## Mutants

Each mutant was planted on its own after asserting exactly one match in the source. The 7-file MAC set then ran with `-x`, and the file was restored with `git checkout -- <file>`.

| # | Mutant | Result (killing test) |
|---|---|---|
| Q1 | quit handler back to the bare re-import | killed (`test_the_quit_restore_survives_its_own_module_failing_to_import`) |
| Q2 | fallback `detail = str(exc)` | **survived** (P3-1) |
| P1 | `privileged.run` without `errors="replace"` | killed (`test_run_survives_output_that_is_not_utf8`) |
| P2 | `_changes_elsewhere` catches `TypeError` instead of `ValueError` | killed (`test_an_undecodable_process_list_counts_as_unreadable`) |
| R1 | regex back to `sentinel-mac (dev) ` | killed (`test_only_the_helpers_own_shape_counts_as_a_change_elsewhere`) |
| R2 | regex without the `[01]\b` flag | killed (same) |
| R3 | address part loosened to `\S+` | **survived** (P3-2) |
| R4 | flag `[0-9]` | killed (same) |
| R5 | flag without trailing `\b` | **survived** (P3-2) |
| L1 | re-check without `device not in _in_flight` | killed (`test_a_change_that_starts_during_the_check_is_not_forgotten`) |
| L2 | re-check not under `_lock` | **survived**. It needs two threads to observe. By reading, the lock is what makes the check and the forget atomic against `_busy` → `_remember`, so keep it. |
| B1 | boot guard back to `not restorable` | killed (`test_an_unknown_boot_allows_only_a_way_back`) |
| B2 | guard without `restorable and` (any original) | killed (`test_an_unknown_boot_does_not_take_another_adapters_original_as_a_way_back`) |
| B3 | guard without `address == entry["original"]` | killed (`test_an_unknown_boot_allows_only_a_way_back`) |
| U1 | no refresh after a no-op offer | killed (`test_a_tab_shown_during_the_offer_read_still_lists_its_interfaces`) |
| U2 | no refresh after a declined offer | killed (`test_a_declined_offer_on_a_shown_tab_lists_the_interfaces`) |
| U3 | `_offer` returns None | **survived** (equivalent, see Not findings) |
| U4 | refresh even when the tab was never shown | killed (`test_the_startup_offer_reads_off_the_ui_thread`) |
| D1 | reset note without "until this Mac restarts" | killed (`test_the_final_confirmation_says_a_changed_address_stays_changed[recorded0-True]`) |

14 of 19 killed. Of the 5 survivors: Q2, R3 and R5 are test gaps (P3-1, P3-2); L2 needs two threads to test; U3 is equivalent.

## Commands run
- `git status --short` (start: empty), `git log --oneline -4`, `git show --stat 1a51b23 b264aed`, `git show 1a51b23 -- main.py tests/test_macaddr.py`, `git show b264aed -- agents/ ui/`, `git show b264aed -- tests/`.
- Read: `docs/audit/macaddr-p3s-review-2026-11-05.md`; `privileged.py:1-164`; `macaddr.py:240-585`; `ui/panels/vpn_gate.py`; `ui/panels/vpn_privacy.py:60-90, 224-300, 330-420`; `main.py:5997-6019`; `ui/dialogs.py:1305-1375`; `server/deploy.py:318-375`; `killswitch.py:950-970`.
- `git grep` for `privileged` callers, `UnicodeDecodeError|except ValueError` in the VPN code, `in_flight()|_busy(`, and the `sentinel-mac` fixtures.
- The MAC set above: 332 passed. I did not rerun the full suite; the author's run was 3290 passed with 1 known unrelated failure.
- Scratchpad `probe_ps.py`: harmless sleeping `python -c` children plus a real `ps` read. No root, MAC, Wi-Fi, tunnel or network action was run.
- Scratchpad `mutate.py`: 19 mutants, each restored with `git checkout --`.
- Final `git status --short`: ` M TODO.md` only. Someone else edited it at 14:30:12 during this review; it is not my change and I left it in place.

## Follow-up (author, 2026-11-05)
- P3-1 and P3-2 closed by tests: the quit fallback test asserts only the type name is printed, and the noise lines `sentinel-mac en9 x 1` and `… 02:aa:bb:cc:dd:ee 10` were added. Q2, R3 and R5 re-planted: all killed.
- P3-3 recorded as open in TODO.md (pre-existing, very low impact).
- The TODO.md edit noted above was the author's.

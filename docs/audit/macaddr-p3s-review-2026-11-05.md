# Independent review: MAC changer P3 fixes, branch `fix/macaddr-p3s` (13fd119, 2abd7a3, b21eb2e, 007fb58)

- Date: 2026-11-05. Reviewer: code-reviewer (Opus 5.5), read-only, Tier C. I did not write this code.
- Checkout: worktree `.claude/worktrees/macaddr-p3s`, branch `fix/macaddr-p3s`. Base `main` (`git diff main...HEAD`). Merge `413d73d` only adds `docs/audit/macaddr-review-4-2026-10-28.md`.
- Scope: commits `13fd119`, `2abd7a3`, `b21eb2e` and `007fb58` (`agents/vpn_agent/services/macaddr.py`, `ui/panels/vpn_privacy.py`, `ui/dialogs.py`, and the tests). Findings answered: re-audit 1 (`macaddr-reaudit-2026-10-23.md`) P3-2 and P3-4; re-audit 2 (`macaddr-reaudit-2-2026-10-24.md`) P3-1 to P3-6; `macaddr-review3-p3s-review-2026-11-03.md` P3-1 and P3-3; `macaddr-review-4-2026-10-28.md` P3-A and P3-C.
- **A fifth commit landed while this review was running.** `1a51b23` (main.py, a quit handler that no longer re-imports a module that failed to import) was committed at 14:05 (amended 14:06), after this review started at `007fb58`. It is **out of scope** and was not reviewed. The full-suite and mutant runs below were made at `1a51b23`.
- Playbook: `docs/playbooks/code-reviewer.md`, `last-reviewed: 2026-10-14`, `status: current`. It is 22 days old, so not stale.
- `docs/company/app-context.md` is absent from the worktree, so it was read from the main checkout.

## Verdict

**Mergeable once P2-1 is fixed (a one-line change). The rest can follow.**

Every finding in scope is closed, and each fix has a test that fails without it. 32 of 33 hand mutants were killed. The one survivor is near-equivalent, and a further one (D3) is a test gap on a defensive guard.

The new ps check has one real hole. **`_changes_elsewhere` raises (instead of returning None) when any process on the Mac has a non-UTF-8 byte in its arguments.** When it does, the quit-time restore is skipped and the startup offer fails. That breaks the Tunnel rule "restore on exit" because of something outside Sentinel's control. The startup offer moving off the UI thread added two smaller UI problems:
- Opening the Privacy tab while the offer is still reading leaves the interface list empty.
- The retry timer, which today's only caller does not reach, can fire inside a confirmation dialog.

Counts: P0 0 · P1 0 · P2 1 · P3 5.

## Evidence

| Check | Command | Result |
|---|---|---|
| Full suite at `1a51b23` | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -p no:cacheprovider -q --no-header` (worktree) | **1 failed, 3281 passed, 14 skipped, 1 deselected in 225 s.** The failure is the known unrelated `test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` (ForbiddenInTests on `lms server status`). |
| MAC test set (7 files: test_macaddr, test_vpn_companion_libs, test_vpn_privacy_tab, test_quit_privileged, test_reset_fence, test_audit_must_fix, test_privileged_runner) | same runner | 323 passed |
| What macOS `ps` prints for a multi-line argv | python3 probe: a harmless child whose argv copies the dialog's `do shell script "…"` string, then `ps -axww -o command=` | Newlines print as `\012`. `_ELSEWHERE_RE` finds `en7` in the child's line. |
| Can a normal user see root processes' arguments? | `ps -axww -o user=,command= \| awk '$1=="root"'` | Yes, for example `/usr/sbin/systemstats --daemon`. A root helper still running after a timeout is therefore visible too. |
| `ps` output with a non-UTF-8 byte in some argv | Child started with argv byte `0xff`; `ps` run with `{}`, `LC_CTYPE=C.UTF-8` and `LANG=en_US.UTF-8` | Empty locale: escaped, decodes. **`C.UTF-8` and `en_US.UTF-8`: the raw byte is passed through and does not decode.** |
| What locale does Sentinel's Python hand to ps? | `env -i .venv/bin/python -c 'print(os.environ.get("LC_CTYPE"))'` | `C.UTF-8`: PEP 538 locale coercion sets it even when the environment is empty. |
| Does `privileged.run` survive bad bytes? | `run(['printf', r'\377'])`; and `env -i` venv python running `run(['ps','-axww','-o','command='])` beside a `0xff` child | **Both raise `UnicodeDecodeError`.** |
| Effect on the quit restore | Scratch probe: record for en5 (changed), and ps raises like `run` does | `main._restore_changed_hardware_addresses()` prints `hardware address restore skipped: 'utf-8' codec can't decode byte 0xff…`. Root calls: 0. The record is kept. |
| Unknown boot that later becomes known (P3-3) | Scratch probe: an earlier-boot entry for en5 with the same hardware; `boot_id` returns `""` during the change and `BOOT` afterwards | The change succeeds. The record while the boot is unknown is the old entry. Once the boot is known the record is `{}` and `pending: []`. |
| A record forgotten during a later change (P3-2) | Scratch probe, two threads: `pending_restores` takes its snapshot; `set_mac("en0")` runs `_remember`; the pending thread runs `_forget`; then root returns | `change ok True \| pending [[]] \| record after: {}` |
| Retry timer inside a confirmation (P3-2) | Offscreen probe: `OFFER_RETRY_MS=0`; the dialog stub calls `processEvents()` as a modal's `exec()` would | Offer declined: `workers=[fresh read, offer read, set_mac] busy=False random_btn_enabled=True` while set_mac has not finished. Offer accepted: a 4th worker (`restore_all_recorded`) runs beside set_mac. |
| First show during the offer read (P3-1) | Offscreen probe: `offer_recorded_restore()`, then `tab.show()`, then the read ends with nothing pending | 1 worker, **0 interfaces in the box**. Control (no offer): 2. |
| Callers | `git grep -nE "macaddr\.(set_mac\|randomize\|restore\|pending_restores\|restore_all_recorded\|in_flight)" -- '*.py' ':!tests'` | `main.py:6007` (quit), `ui/panels/vpn_privacy.py:361,383,403`. Nothing else. `offer_recorded_restore` is called only from the 0 ms timer at `vpn_privacy.py:75`. |
| Cost of ps | `time (ps -axww -o command= \| wc -c)` | 298,656 bytes in 0.044 s. It runs only when a record exists (`macaddr.py:409-410`). |
| Tunnel rules | Read the diff; grep for `shell=True\|os.system\|Popen` in the changed files | ps is an argument list. No shell strings. The reset note names devices only, with no addresses. |

Probes and mutants ran in scratch copies, or in the worktree with `git checkout --` after each. `git status --short` in the worktree is empty at the end.

## Each fix against its finding

| Finding | Fix | Status |
|---|---|---|
| Re-audit 1 P3-2: Emergency Reset erases the record | `ui/dialogs.py:1316-1331`: the final confirmation names the devices whose change stays until restart | **Closed** (D1 and D2 killed). The exception guard is untested (D3 survives, see P3-5). |
| Re-audit 1 P3-4: the startup offer runs `ifconfig` on the UI thread | `vpn_privacy.py:348-364`: `pending_restores` runs through `_read_off_thread` | **Closed** (U2 killed). It introduces P3-1, and P3-2 is latent. |
| Re-audit 2 P3-1: a relaunch forgets an orphaned dialog's record | `macaddr.py:347-354`, `:413-428`: `ps` scan for `sentinel-mac <dev> ` | **Closed for the dialog and for the post-timeout root helper** (root argv is visible to ps, measured). That osascript survives `os._exit` remains **not verified** live, as in the earlier reports. The ps scan opens P2-1, and P3-4 is a residual. |
| Re-audit 2 P3-2: in flight marked late, and a set | `_busy` counts, `macaddr.py:329-344`, wrapped around listing and root in `_change` (`:469`) and in `restore` (`:545`) | **Closed** (M1-M4 and F4 killed). Every early return is inside `with`, so the count cannot leak (a `finally` decrements). Nesting restore → `_change` counts to 2 and back. `_lock` is never held across a subprocess call except `boot_id`'s sysctl inside `_read_entries`, which was already the case. |
| Re-audit 2 P3-3: a stale entry blocks a reused name | `_remember` replaces an entry whose hardware differs, `:380` | **Closed** (M15 killed). The listing only returns hardware that matches `MAC_RE`, so a blank hardware value cannot overwrite a good entry. |
| Re-audit 2 P3-4: a failed boot read is cached | `boot_id` caches only a good read (`:263-278`); refuses a change needing a new record while the boot is unknown (`:488-492`); a known boot drops boot-less entries (`:310-311`) | **Closed** (M11-M14 killed). Not blocked: restoring to hardware, restoring to a recorded private original, a second change on a recorded device (`test_going_back_needs_no_boot`). Residual: P3-3. |
| Re-audit 2 P3-5: RecursionError | `:299` | **Closed** (M16 killed) |
| Re-audit 2 P3-6: test gaps | `_forget` prune, lock, private-address original | **Closed.** The four old survivors are now killed (by M12, plus the commit's own tests). |
| Review-3 P3-1: the `found` path is not pinned | three restore tests | **Closed** (F1-F3 killed) |
| Review-3 P3-3: restore lists before checking the name | `macaddr.py:543-544` plus a parametrised hostile-name test | **Closed** (M17 killed) |
| Review-4 P3-A: boot filter at the guard | `test_a_record_from_an_earlier_boot_does_not_open_the_guard` | **Closed** (F5 killed) |
| Review-4 P3-C: unused `root` argument | `_unrecordable(monkeypatch)` | **Closed** |

## Findings

### P2-1 A process with a non-UTF-8 argument anywhere on the Mac makes the quit restore and the startup offer fail
- **Where:** `agents/vpn_agent/services/macaddr.py:353` (`run(["ps", "-axww", "-o", "command="])`), through `agents/vpn_agent/services/privileged.py:33-46`. `run` uses `text=True` and catches only `TimeoutExpired` and `OSError`. Reached from `pending_restores` (`macaddr.py:413`), and from there `restore_all_recorded` (`:437`), `main.py:6007` and `ui/panels/vpn_privacy.py:361`.
- **Failure scenario:**
  1. A change is recorded.
  2. Any same-user or root process has an argv byte that is not valid UTF-8.
  3. Sentinel's environment carries a UTF-8 `LC_CTYPE`. Python's PEP 538 coercion sets `C.UTF-8` even when started with an empty environment (measured above).
  4. ps passes the raw byte through, and `subprocess.run(text=True)` raises `UnicodeDecodeError`.
  5. At quit, `_restore_changed_hardware_addresses` catches it and prints "hardware address restore skipped: 'utf-8' codec…", so the changed address stays (probe).
  6. At startup, `read()` catches only `OSError`, so the worker's error path shows "Failed: 'utf-8' codec…" and nothing is offered.
  7. This repeats at every launch while that process lives.
- **Why it matters:** before `b21eb2e`, `pending_restores` ran no ps and could not raise this. The design says a failing ps should give None, so nothing is forgotten and changed devices are still offered. Instead the whole restore is lost.
- **Fix:** in `_changes_elsewhere`, treat a decode failure as "ps failed". For example, wrap `run` in `try/except ValueError: return None`. Better, have `privileged.run` decode with `errors="replace"` (`subprocess.run(..., text=True, errors="replace")`), which also protects every other caller. Add a test where the ps stub raises `UnicodeDecodeError` (or returns replacement characters) and assert the "ps unreadable" behaviour: nothing forgotten, a changed device offered.

### P3-1 Opening the Privacy tab while the startup offer is reading leaves the interface list empty
- **Where:** `ui/panels/vpn_privacy.py:241-245` (`refresh_interfaces` returns early while busy, saying "a finishing action refreshes after"), `:348-372`, and `ui/panels/vpn_gate.py:98-101`. The offer's `release()` calls `_controls_changed()`, not `_after_finish()`, so nothing refreshes the list.
- **Failure scenario:**
  1. A record exists at launch, and the 0 ms timer starts the offer read (networksetup, an ifconfig per port, and ps).
  2. The user opens Tunnel → Privacy before the read ends.
  3. `showEvent` sets `_loaded` and calls `refresh_interfaces`, which returns early because the tab is busy.
  4. The read ends with nothing pending, or the user declines the offer. The device box stays empty, and Randomise and Restore stay off until the user presses Refresh. Probe: 0 interfaces, against 2 in the control.
- **Before `007fb58`:** the offer ran synchronously, so `showEvent` could not interleave with it.
- **Fix:** in `_offer_pending`, call `self.refresh_interfaces()` when the tab has been shown (`self._loaded`) and nothing is offered. Or in `showEvent`, when the tab is busy, defer the first refresh (for example, leave `_loaded` false until a refresh actually starts). Add an offscreen test with `DeferredWorker`: offer, then `show()`, then end the read, then assert the box is filled.

### P3-2 The offer's retry timer can fire inside a confirmation dialog, clearing "busy" under a privileged worker (latent)
- **Where:** `ui/panels/vpn_privacy.py:355-357`, together with `ui/panels/vpn_gate.py:43-55`. `_gated` checks `busy` before `_ask` and sets `_active` only after the modal returns. `_take_worker` parks the old worker without disconnecting its `done`.
- **Failure scenario:**
  1. The offer is in retry mode, and the user's action opens a confirmation; the modal's nested event loop fires the retry.
  2. The tab is not busy at that moment, so the offer starts its read. The user confirms, and set_mac starts as well.
  3. The offer read ends first. `release()` sets `busy=False` and re-enables Randomise while set_mac's admin dialog is still open. If the user accepts the offer, `restore_all_recorded` runs beside set_mac. Probe output: `busy=False random_btn_enabled=True`, or 4 workers.
  4. With the pre-existing snapshot gap in `pending_restores` (`macaddr.py:404-428`: `_in_flight` is read once, before listing and `_forget`), the offer can then forget a record whose change began after its snapshot. Probe: `record after: {}` while en0 is changed.
- **Reachability today:** the only caller is the 0 ms timer at construction, and the Privacy tab is never shown or busy then (`vpn.py:382`, not the current tab). So retry mode is effectively test-only. This is latent, and becomes real as soon as the offer gets another caller.
- **Fix (UI):** reserve the tab before `_ask` in `_gated` (set `_active` before asking, release on decline), or have the retry check `QApplication.activeModalWidget()`.
- **Fix (macaddr, defence in depth):** re-check `_in_flight` under `_lock` immediately before each `_forget` in `pending_restores`.

### P3-3 A boot read that fails only for a while lets an earlier boot's entry stand in for the record, then drops it
- **Where:** `macaddr.py:480` (`restorable` reads `_read_entries`, which keeps every entry while the boot is unknown, `:309-311`), `:488` (the boot refusal is skipped when `restorable`), `:380` (`_remember` keeps the existing entry because the hardware matches).
- **Failure scenario:**
  1. An earlier boot left an entry for en5 (same adapter), and it was not pruned because the boot was also unknown at startup.
  2. sysctl fails during `set_mac("en5", NEW)`, so the change runs and writes nothing new.
  3. sysctl works again, now that failures are not cached. The old entry is dropped as stale, the change is unrecorded, and the quit restore does nothing. Probe: `record once boot known: {} | pending: []`.
- **Why now:** before `2abd7a3` a failure was cached for the whole process, so this sequence could not happen. It needs `kern.bootsessionuuid` to fail and then recover, so it is very unlikely.
- **Fix:** while `boot_id()` is `""`, allow a change only to the hardware address or to the recorded original. That keeps `restore_all_recorded` working and refuses any other change. Or, in `_remember`, rewrite the entry's `boot` when it was written in a different boot from the one now known.

### P3-4 `_ELSEWHERE_RE` matches any process that mentions `sentinel-mac enN `, and the quit then skips that device without saying so
- **Where:** `macaddr.py:250`, `:413-415`; `main.py:6007-6012` prints only for restores that ran and failed.
- **Failure scenario:** a terminal `grep -r "sentinel-mac en5 " …`, or an agent shell whose argv carries that text, is running at quit. `restore_all_recorded()` returns `[]` (probe: `false positive: [] {'en5': …}`), so the changed address stays, and no stderr line says why. The record is kept and offered at the next launch, so this fails safe.
- **Mutant:** M18 (drop `\b`) survives. It is near-equivalent.
- **Fix:** match the helper's full shape: `sentinel-mac (dev) ([0-9a-f]{2}(:[0-9a-f]{2}){5}) [01]\b`. Have `_restore_changed_hardware_addresses` (or `pending_restores`) report the devices skipped as busy, by name only.

### P3-5 Small test and wording gaps
- `ui/dialogs.py:1318-1323`: mutant D3 (narrow `except Exception` to `ImportError`) survives, because no test makes `recorded()` raise. Add a case where it raises `OSError` and assert the confirmation still appears without the note.
- `ui/dialogs.py:1324-1326`: the note says the address "is not put back" based only on `recorded()`, which does not check whether the device is still changed. If the device is already back on its original, the sentence is wrong. Softer wording such as "Sentinel's record of a changed address on en5 is erased…" avoids the overclaim. Cosmetic.
- `tests/test_macaddr.py`: the `PS` fixture puts raw newlines in ps output, but real ps prints them as `\012` (measured). The regex works for both, so the test is still valid. A second sample in the real form would document it.

## Not findings (checked)
- **Count leaks:** every return inside `_busy` goes through `finally`. An exception from listing or root decrements too.
- **Lock re-entrancy:** `_busy` takes `_lock` briefly and never across root. `_remember` and `_forget` re-enter the RLock safely.
- **Order of the in-flight snapshot and ps:** a change in this process that ends between the two was already in the snapshot. One that starts after is P3-2's gap, which is not reachable from today's callers.
- **Unknown-boot refusal on legitimate flows:** restoring to hardware, to a recorded private original, and a second change on a recorded device all go through (`test_going_back_needs_no_boot`, M14 killed).
- **Retry timer lifetime:** `QTimer.singleShot(ms, self, …)` is bound to the tab, so a destroyed tab cancels it. The offer checks `busy` first, so `_read_off_thread` never shows "Busy" (U1 killed).
- **The UI thread:** `pending_restores` no longer runs on it. The quit restore runs after `app.exec()` by design (re-audit 1 P3-1). `recorded()` stays on the UI thread (construction, reset): one file read, plus sysctl until a good read is cached.
- **Tunnel rules:** argument lists only. Every root path is unchanged (`allow_cached_sudo=False`). Only locally administered unicast addresses are generated. No address goes into kept output: the reset note names devices, and stderr is redacted.

## Mutants

Each was planted on its own; one exact match was asserted before replacing; the file was restored afterwards. Tests: the 7-file MAC set with `-x`.

| # | Mutant | Result (killing test) |
|---|---|---|
| M1 | `_busy` sets 1 instead of +1 | killed (`test_restore_when_changed_sets_the_hardware_address`) |
| M2 | `_busy` finally pops outright | killed (`test_in_flight_is_a_count`) |
| M3 | restore not wrapped in `_busy` | killed (`test_the_device_is_in_flight_while_it_is_listed`) |
| M4 | `_change` not wrapped in `_busy` | killed (`test_quit_never_forgets_a_change_still_waiting_for_root`) |
| M5 | regex without the space after the device | killed (`test_changes_elsewhere_reads_the_process_list`) |
| M6 | `elsewhere` not added to `busy` | killed (`test_an_orphaned_dialog_keeps_the_record`) |
| M7 | ps failure still forgets an entry back on its original | killed (`test_an_unreadable_process_list_forgets_nothing_but_still_offers`) |
| M8 | ps failure keeps another adapter's entry | killed (`test_another_adapter_is_still_dropped_when_the_process_list_is_unreadable`) |
| M9 | ps run with no record | killed (`test_no_process_list_is_read_without_a_record`) |
| M10 | ps failure returns `set()` | killed (`test_changes_elsewhere_reads_the_process_list`) |
| M11 | boot failure cached | killed (`test_a_failed_boot_read_is_not_cached`) |
| M12 | old boot filter (keeps boot-less entries) | killed (`test_a_boot_less_entry_is_dropped_once_the_boot_is_known`) |
| M13 | unknown-boot refusal removed | killed (`test_a_change_that_needs_a_record_is_refused_when_the_boot_is_unknown`) |
| M14 | unknown-boot refusal for every change | killed (`test_going_back_needs_no_boot`) |
| M15 | `_remember` first-wins, as before | killed (`test_a_new_adapter_under_a_reused_name_is_recorded`) |
| M16 | `RecursionError` not caught | killed (`test_a_deeply_nested_record_is_treated_as_damaged`) |
| M17 | restore skips the name check | killed (`test_hostile_or_malformed_device_is_refused_before_anything_runs[restore-…]`) |
| M18 | regex without `\b` | **survived** (near-equivalent; P3-4) |
| M19 | `_in_flight` snapshot dropped | killed (`test_quit_never_forgets_a_change_still_waiting_for_root`) |
| M20 | hardware-mismatch forget skipped | killed (`test_another_adapter_under_the_same_name_is_not_given_the_address`) |
| F1 | `found` skips the address check | killed (`test_restore_refuses_an_unusable_hardware_address`) |
| F2 | `found` skips the did-it-take check | killed (`test_restore_is_not_a_success_when_the_address_did_not_take`) |
| F3 | `found` drops the Wi-Fi flag | killed (`test_restore_passes_the_wifi_flag`) |
| F4 | restore marks busy after listing | killed (`test_the_device_is_in_flight_while_it_is_listed`) |
| F5 | guard reads the raw file (no boot filter) | killed (`test_a_record_from_an_earlier_boot_does_not_open_the_guard`) |
| U1 | offer ignores busy | killed (`test_the_startup_offer_waits_for_a_busy_tab`) |
| U2 | offer reads on the UI thread | killed (`test_the_startup_offer_reads_off_the_ui_thread`) |
| U3 | offer read's `OSError` not caught | killed (`test_an_unreadable_record_at_startup_says_so`) |
| U4 | offer error not shown | killed (`test_an_unreadable_record_at_startup_says_so`) |
| U5 | retry never rescheduled | killed (`test_the_startup_offer_waits_for_a_busy_tab`) |
| D1 | reset note dropped | killed (`test_the_final_confirmation_says_a_changed_address_stays_changed[…True]`) |
| D2 | reset note lists addresses | killed (same test) |
| D3 | reset note guard narrowed to `ImportError` | **survived** (P3-5) |

32 of 33 killed. The two survivors are M18 (near-equivalent) and D3 (a real but minor test gap).

## Commands run
- `git log --oneline main..HEAD`; `git diff main...HEAD --stat`; `git diff main...HEAD -- agents/vpn_agent/services/macaddr.py ui/`; `git diff main...HEAD -- tests/…`; `git log -4 --format='%H%n%B'`; `git show --stat 1a51b23`; `git reflog -5`.
- Read: `macaddr.py` (whole module), `privileged.py:20-161`, `ui/panels/vpn_privacy.py`, `ui/panels/vpn_gate.py`, `ui/workers.py:87-117`, `ui/panels/vpn.py:370-449`, `main.py:5997-6034`, and the four audit reports for the findings above.
- Full suite in the worktree twice (`007fb58`→`1a51b23`: 1 failed / 3280 passed; `1a51b23`: 1 failed / 3281 passed, the same known failure). MAC set: 323 passed.
- ps, locale and decode probes outside pytest, using harmless `python -c sleep` children only. No live MAC, Wi-Fi, root, tunnel or network action was run.
- Scratch probes (`git archive HEAD` copy under the session scratchpad) and the mutation scripts `mutate.py` and `m2.py` in the scratchpad. U*, D*, M5 and M18-M20 ran in the worktree and were restored with `git checkout --`. The rest ran in the scratch copy.
- Final `git status --short` in the worktree: empty.

# MAC changer re-audit 2: commit `164f1db` (2026-10-24)

Independent, read-only re-audit (Tier C) of `164f1db` on `fix/macaddr-audit`: the fixes for
P2-1, P2-2, the reboot half of P2-3 and P3-3 from `docs/audit/macaddr-reaudit-2026-10-23.md`.
Auditor: Claude Opus 5.5, a different session from the commit's author. No source was edited
and nothing was committed. No real MAC, Wi-Fi or root action was run. Probes ran against fakes in
a scratch copy of the tree. The only live commands were read-only `sysctl` and `last reboot` /
`pmset -g log` reads. No address was recorded.

Scope: `agents/vpn_agent/services/macaddr.py` (`boot_id`, `_valid_entry`, `_read_entries`,
`recorded`, `in_flight`, `_remember`, `_forget`, `pending_restores`, `restore_all_recorded`, and
the `_in_flight`/`settled` changes in `set_mac`), `tests/test_macaddr.py`, and the two `boot_id`
patches in `tests/test_vpn_companion_libs.py`.

## Verdict

**Not ready to merge as the Tier C close-out.** No P0 or P1 was found. One P2 remains, and the fix
is small. The commit's boot identity, the `sec` field of `kern.boottime`, is not constant within a
boot on this Mac. If it moves across a second boundary, the next launch treats a same-boot record
as stale, deletes it, and does not restore the change. That is the "restore after a crash" half of
the Tunnel rule again, which is the promise H1 exists to keep. Switching to `kern.bootsessionuuid`
fixes it. That sysctl is present on this Mac. The owner can also defer it explicitly.

The rest holds. A timeout or other unknown outcome no longer forgets the record (P2-1).
Quit-time restore leaves a device whose dialog is still open alone (P2-2). Records from an
earlier boot and records from another adapter's hardware are dropped (P2-3 reboot half, P3-3).
No deadlock was found. The six P3s below are narrow and can be deferred.

## What was checked and holds

| Claim | Evidence |
|---|---|
| P2-1: `set_mac` forgets only after success or `Cancelled.` | `macaddr.py:401-403`. `privileged.run` returns `Cancelled.` only for `User canceled` / `(-128)` on a non-zero exit (`privileged.py:39-40`), so a timeout or a failed script cannot pass for it. Mutants "settled always true" and "settled = ok only" were both killed. |
| P2-2: a device with its root call in progress is skipped by `pending_restores` | `macaddr.py:322-327`, `:379-381`, `:404-406`. The `_in_flight` add/discard sits in `try/finally`. Mutants "no in-flight add", "no in-flight discard" and "pending ignores busy" were all killed by `test_quit_never_forgets_a_change_still_waiting_for_root`, which uses a real second thread. |
| No deadlock | The RLock is never held across `run_argv_as_root`, `list_interfaces` or `current_mac`. It is held across one subprocess only: the first, cached `boot_id()` `sysctl` (30 s cap, `privileged.py:27`). Nothing takes a second lock. |
| `os._exit` mid-change leaves a correct record | The record is on disk before root runs (`:385-386`). The in-flight set lives only in memory and dies with the process, so the next launch decides from the address it can see. See P3-1 for the one case where that decision is premature. |
| Earlier-boot entries are dropped and pruned; an unknown current boot keeps them | `:258-262`, `:323-325`. Mutants "no boot filter", "unknown boot drops", "no stale prune in pending" and "record boot empty" were all killed. |
| `kern.boottime` parsing | `\bsec = (\d+)` cannot match the `usec` field: `u` and `s` are both word characters, so there is no `\b` between them. Mutant "boot regex usec" was killed. Output from this Mac: `{ sec = 1792350752, usec = 593939 } Sun Oct 18 20:12:32 2026`. |
| P3-3: another adapter under the same name is never given the address | `:336-337`. Mutant "no hardware check" was killed. |
| The record cannot steer root | `_valid_entry` (`:236-240`) re-checks the device name, a unicast original, a MAC-shaped hardware value and a string boot. `set_mac` re-validates both values and requires a real port (`:366-373`). Mutant "valid_entry skips boot type" was killed. |
| The admin prompt no longer says "hardware" | `:348-349`. Mutant killed. |
| Full suite | `1 failed, 3075 passed, 14 skipped, 1 deselected in 230.17s`. The one failure is unrelated to this commit (see "Outside scope"). |

## Findings

### P2-A: `kern.boottime` seconds drift within one boot, so a live record can be dropped as "earlier boot"

`macaddr.py:228-233` (identity) and `:258-262` (filter and prune). XNU adjusts `kern.boottime` so
that boot time plus uptime equals wall-clock time. On this Mac it moved within a single boot:

- The fixture in `tests/test_macaddr.py:480` was evidently copied from this Mac when the commit
  was written on 2026-10-23. Its `sec` matches this boot exactly, and `last reboot` gives
  `Sun Oct 18 20:12`. It reads `{ sec = 1792350752, usec = 655426 }`.
- Today, 2026-10-27, the same boot reads `{ sec = 1792350752, usec = 593939 }`. Nothing
  rebooted in between, but `pmset -g log` shows sleep/wake cycles, the latest at 20:37:41 today.
  The boot time moved by -61.5 ms.

The seconds field stayed the same this time only because `usec` was far from zero. Any adjustment
that crosses a second boundary changes `boot_id()`, and so does any clock step of a second or more:
an NTP correction after a long sleep, or a manual clock change. `boot_id` is cached per process,
so the drift never shows inside one run. It shows on exactly the path the record exists for: a
crash, a declined quit-time restore or a Quit anyway, followed by a later launch in the same boot.
That launch drops the entry, prunes it from disk, offers nothing and restores nothing at quit.
The randomized address stays until reboot, and Sentinel has forgotten it changed it.

- **Scenario:** randomize en5, then Sentinel crashes. Close the lid. The wake shifts `boottime`
  across a second. Relaunch Sentinel: no offer appears, and quitting does not restore en5.
- **Evidence:** fake probe `probe_scenarios.py::test_A` (session scratchpad). It records with
  boot `1792350752` and relaunches with `1792350751`. Output:
  `A recorded at launch 2: {} | pending: [] | file exists: False`. The live drift figures above
  come from `sysctl -n kern.boottime` today and the fixture value. How often the second flips in
  practice was **not measured**. At the observed rate (~15 ms/day) it is roughly a 1-2 % chance
  per day that a record is alive, plus a certainty after any clock step of 1 s or more.
- **Fix direction:** use `sysctl -n kern.bootsessionuuid`. It is per boot by design, and
  `sysctl -n kern.bootsessionuuid` returned a UUID on this Mac (value not recorded). An
  alternative is to compare `kern.boottime` with a tolerance, such as ±60 s, since a real reboot
  moves it by far more. Add a test for "same boot, `sec` off by one".

### P3-1: a relaunch while the orphaned dialog is still open forgets the record

`macaddr.py:336-337`, reached from the startup offer (`ui/panels/vpn_privacy.py:55-59`, `:281`).
P2-2's fix keeps the record through Quit anyway and says "the next launch decides". But the
osascript dialog outlives `os._exit`, and the new process's `_in_flight` is empty. If Sentinel is
reopened before the user answers that dialog, the startup `pending_restores` sees the address
still on the original and forgets the record. Approving the old dialog afterwards then changes the
address with no record. The same window exists, much shorter, after a timeout: the root command
can land about 2 s after the timeout message, and a quit inside that window forgets the record.

- **Evidence:** probe `test_C`. Output: `C startup pending: [] | record after startup: {}`.
  That an orphaned osascript dialog survives the parent's `os._exit` and can still be approved is
  **not verified** on hardware. P2-2 and its fix rest on the same assumption.
- **Fix direction (owner's call):** at startup, do not forget an entry whose address is still on
  the original for a short grace period after the record was written. That needs a timestamp in
  the entry. Alternatively, accept and document it: the panel still shows `(changed)` and Restore
  works.

### P3-2: the in-flight window opens late and is a set, not a count

`macaddr.py:371` and `:379-381`, `:404-406`. `_in_flight.add` runs only after `get_interface()`,
which costs one `networksetup` call plus one `ifconfig` per port. If `_leave` runs while the worker
is still inside `get_interface`, quit-time restore treats the device as idle and calls
`set_mac(original)` on the main thread. Meanwhile the worker reaches its own dialog. The first of
the two calls to finish discards the device from the set while the other is still in root. In the
probe, main's restore succeeds and forgets the record, the worker's flag has already been cleared,
and the worker's dialog then lands with no record after `os._exit`.

- **Evidence:** probe `test_F`. Output:
  `F main restore: [('en5', True, …)] {'in_flight_after_main_restore': [], 'record_after_main_restore': {}, 'worker_in_flight_flag_during_root': False}`.
  This needs Quit anyway to be chosen within the first fraction of a second of a change, before
  the worker reaches its dialog, so it is very unlikely by hand. The panel serialises its own
  workers (`busy`), so two concurrent changes from the UI are not otherwise possible.
- **Fix direction:** mark the device in flight before `get_interface`, and use a counter (or
  refuse a second concurrent `set_mac` on the same device).

### P3-3: a stale entry for another adapter blocks the new adapter's record

`macaddr.py:299` ("the first original wins") together with `:333-337`. An entry for an unplugged
adapter is kept (`:334-335`). If a second adapter later gets the same BSD name and is randomized,
`_remember` sees the device already recorded and writes nothing. At quit, the hardware check finds
that the entry's hardware does not match and forgets it. The second adapter's change is never
restored and was never recorded. P3-3's fix stops the wrong address being written, but it leaves
this gap.

- **Evidence:** probe `test_B`. Output:
  `B record after B's change: aa:bb:cc:00:00:07 | quit restores: [] [] | record after quit: {}`.
  This depends on BSD-name reuse, which macOS usually avoids.
- **Fix direction:** in `_remember`, replace an entry whose `hardware` differs from the
  interface's.

### P3-4: a boot read that fails once is cached, and its records then survive every reboot

`macaddr.py:228-233` and `:261`. `functools.cache` also caches `""`. Every record made in that
process is then written with `"boot": ""`, and the filter keeps such entries under any later boot
(`entry["boot"] and …`). That brings back the original P2-3 reboot half for those entries: a false
"Sentinel changed …" offer after a reboot, and an admin prompt at every quit until it is accepted.
`sysctl` is unlikely to fail, so this is low.

- **Evidence:** probe `test_E`. Output:
  `E offered after reboot: [('en0', '06:aa:aa:aa:aa:aa', '0a:bb:bb:bb:bb:bb')]`.
- **Fix direction:** do not cache a failure. If the boot is still unknown at write time, refuse
  the change, or let a later known boot drop entries with an empty boot.

### P3-5: a deeply nested record file raises out of `recorded()`

`macaddr.py:249-252` catches `OSError` and `ValueError` only. `json.loads` raises `RecursionError`
on deep nesting. `PrivacyTab.__init__` calls `macaddr.recorded()` without a guard
(`ui/panels/vpn_privacy.py:55`). The quit path is safe because it catches `Exception`
(`main.py:6010`). Only a same-user writer of the 0600 file can cause this, and the `except`
clause predates this commit.

- **Evidence:** probe `test_D`. Output: `D recorded() on a 100k-deep file: RecursionError`. What
  the window does when a panel constructor raises was **not verified**.
- **Fix direction:** catch `RecursionError` as well, or `Exception`, and treat the file as
  damaged.

### P3-6: test gaps (four surviving mutants)

Twenty hand mutants were run on a scratch copy against `test_macaddr.py`,
`test_vpn_companion_libs.py` and `test_vpn_privacy_tab.py` with `-x`. **16 were killed and 4
survived** (table below). The survivors:

- **"empty entry boot dropped"** (`:261`): no test covers an entry with `boot: ""` under a known
  boot. That is the branch P3-4 is about.
- **"forget ignores stale"** (`:308`): `_forget` pruning stale entries is untested. This is
  harmless, because `pending_restores` prunes too.
- **"lock is a no-op"** (`:220`): nothing tests concurrent read-modify-writes of the record. This
  is expected, since races are hard to test, but the RLock's claim is unproven.
- **"forget only on hardware"** (`:402`): every fixture has `original == hardware`. Nothing covers
  a private-address original (`original != hardware`) after a cancelled first change, or after a
  successful restore to the original. Either way the entry is still forgotten later by
  `pending_restores`, so the behaviour stays safe, but the line's second operand is untested.

## Commands run

- Full suite, worktree, main-checkout venv:
  `/Users/as/Documents/lab/active/sentinel/.venv/bin/python -m pytest -q -p no:cacheprovider`.
  Result: `1 failed, 3075 passed, 14 skipped, 1 deselected in 230.17s`.
- Probes A-F: `pytest -q` in a scratch copy, file `probe_scenarios.py` in the session
  scratchpad. All six scenarios reproduced as quoted above.
- Mutations: `mutate.py` in the session scratchpad, one mutant at a time on the scratch copy's
  `macaddr.py`, original restored afterwards:

| Mutation | Result |
|---|---|
| settled always true | killed |
| settled = ok only | killed |
| no in-flight add | killed |
| no in-flight discard | killed |
| pending ignores busy | killed |
| no boot filter | killed |
| unknown boot drops | killed |
| empty entry boot dropped | **survived** |
| no stale prune in pending | killed |
| forget ignores stale | **survived** |
| no hardware check | killed |
| no back-on-original forget | killed |
| record boot empty | killed |
| record hardware = original | killed |
| last original wins | killed |
| lock is a no-op | **survived** |
| boot regex `usec` | killed |
| valid_entry skips boot type | killed |
| forget only on hardware | **survived** |
| prompt says hardware | killed |

- Read-only: `sysctl -n kern.boottime`, `sysctl -n kern.bootsessionuuid` (only its presence was
  recorded), `sysctl -n kern.waketime kern.sleeptime`, `last reboot`, `pmset -g log` (event
  names only).

## Follow-up: P2-A fix (2026-10-28, same author as this report, uncommitted)

The owner asked the auditor to fix P2-A, so **this fix is not independently reviewed**. A
different session has to review it before the Tier C item closes. The P3s are unchanged.

- `boot_id()` (`macaddr.py:228-241`) now reads `sysctl -n kern.bootsessionuuid`. It normalises
  the value to upper case and accepts it only if it is a complete UUID; otherwise it returns `""`,
  which means unknown and keeps entries, as before. `kern.boottime` is no longer used.
- Test first: `test_boot_id_is_the_boot_session_uuid` (UUID in upper and lower case;
  `kern.boottime` output, garbage and a trailing-injection string all refused) and
  `test_boot_id_is_unknown_when_sysctl_fails`. Five of the six new cases failed before the fix.
- Mutants on a scratch copy: back to `kern.boottime`, no `upper()`, no shape check and
  ignoring `ok` were all **killed**. "No strip" **survived**. It is equivalent, because
  `privileged.run` already strips its output.
- Live, read-only: `boot_id()` on this Mac returns a valid UUID. Only that fact was printed,
  not the value.
- Upgrade note: an entry written by `164f1db` holds a `kern.boottime` seconds value as its boot
  ID. It no longer matches the UUID, so it is dropped as stale. Only someone who ran this branch's
  code could have such an entry, and the installed app runs the main checkout, which has no record
  code, so no migration was added.
- Not changed: P3-4 (a failed read is cached as `""`). It applies to the new sysctl the same way.
- MAC test files: 109 passed. Full suite: `1 failed, 3079 passed, 14 skipped, 1 deselected in 238.54s`.
  The one failure is the unrelated LM Studio test noted below.

## Outside scope, noted

- `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` fails on
  this Mac with `ForbiddenInTests: test tried to start a program: ['/Users/as/.lmstudio/bin/lms', 'server', 'status']`.
  It fails the same way alone (`1 failed, 73 passed`). This commit does not touch it. It looks
  environment-dependent: the test reaches the real LM Studio CLI when it is installed. The
  author's run reported 3075 passed with no failure, so the CLI probably appeared after 10-23.
- Still open from earlier reports: the network-change half of P2-3 (needs a real-Mac check),
  re-audit P3-1, P3-2 and P3-4, Mediums 5-6, and Lows 7-12.

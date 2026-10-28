# MAC changer re-audit: commit `1aff0eb` (2026-10-23)

Independent, read-only re-audit (Tier C) of `1aff0eb` on `fix/macaddr-audit`. The audit covers
High 1 and Mediums 2-4 of the 2026-10-21 MAC changer audit (`TODO.md`, "MAC changer findings").
Mediums 5-6 and Lows 7-12 are out of scope. Auditor: Claude Opus 5.5, which did not write the
commit. No source was edited. No real MAC, Wi-Fi or root action was run. Probes ran against fakes
in a scratch copy of the tree, plus two read-only commands: `ifconfig en0` and
`networksetup -listallhardwareports`.

## Verdict

**Not ready to merge as the Tier C close-out, but close.** The root helper (M4), the
quoting, Wi-Fi power-on (M3) and the M2 checks hold up. No P0 or P1 was found. Three P2s
remain, all in the H1 record logic, and each one breaks part of the Tunnel rule "record the
original, restore on exit and on crash" that H1 was written to meet:

- the record is forgotten when the outcome is unknown;
- the record is forgotten by the quit-time restore while a change is still in flight;
- the record is not tied to a boot or a network, so a stale private Wi-Fi address can be "restored".

These are small, local fixes. The owner could also merge now and defer them explicitly (the
definition of done allows that only for P0/P1). The P2s should still be closed before the formal
testing phase, because H1 is the promise a tester will exercise.

## What was checked and holds

| Claim | Evidence |
|---|---|
| M4: no value is spliced into a root command string | `ROOT_SCRIPT` is a constant. Values go in as `$1-$3` (`macaddr.py:46-70`, `:325-328`). The test asserts that the argv shape and the constant hold no values (`tests/test_macaddr.py:56-66`). |
| Dialog path quoting (`shlex.join`, then `applescript_string`, then `do shell script`, then `/bin/sh`) is exact | A scratch probe ran `shlex.join(argv)` through `/bin/sh -c` with `printf` standing in for bash, and ran the AppleScript literal through `osascript -e 'return …'`. Output: `sh round-trip exact: True`, `applescript literal round-trip exact: True`, `single quotes in ROOT_SCRIPT: False`. Newlines in the script survive both layers. The prompt comes from a validated device name. |
| Device allow-list runs before anything | `valid_device` is checked first in `set_mac` (`macaddr.py:305`). Twelve hostile names refuse with no `run` call (`test_macaddr.py:38-48`). The mutation `\Z`→`$` was killed. |
| A record entry cannot inject | `recorded()` keeps only `valid_device` + `is_valid` entries (`:216-232`). `set_mac` re-validates both and requires a real hardware port from `networksetup` (`:305-315`). A record can only choose a syntactically valid unicast address on a port that exists. See P3-3 for what it *can* still do. |
| `allow_cached_sudo=False` on every MAC root call | Asserted for change and restore (`test_macaddr.py:66`, `:373`). |
| M3: Wi-Fi power-on runs after a failure, after power-off fails, and after SIGTERM; the first failure is the exit status; a power-on failure fails the change | Run for real under `/bin/bash`, the same binary production uses, with fake tools (`test_macaddr.py:147-192`). Mutation "power-on failure not fatal" was killed. |
| M2: an empty current address is "unknown", not "already restored" | `restore()` (`:369-371`), `set_mac()` (`:316-318`) and the panel's `restore_mac` each refuse, and each has a test. |
| H1: the record is written 0600 before root runs, `_leave` restores before `settings.sync`, a declined restore stays recorded, and startup offers it | `test_macaddr.py:264-279, 377-415`; `test_vpn_privacy_tab.py` startup tests. Mutations "remember skipped", "pending keeps matched" and "restore_all ignores prompt" were all killed. |
| Quit cannot be blocked by a restore exception | `main.py:5997-6011` catches `Exception`. `test_a_restore_that_raises_never_blocks_quit`. |

## Findings

### P2-1: a timed-out change forgets the record although the change may still land

`macaddr.py:332-334`. After *any* failure, `set_mac` reads the address once and forgets the
record if it still equals the original. On a timeout, `privileged.py:91-96` says the opposite is
possible: "Once the password is accepted the command runs as root on its own; stopping osascript
does not undo it." If the password is approved near the end of the 120 s window
(`DEFAULT_TIMEOUT + PASSWORD_ENTRY_SECONDS`), osascript is killed. At that point bash is still in
power-off plus `sleep 1`, so the read returns the original and the record is deleted. `ifconfig`
then applies the new address. Nothing restores it on quit or at the next launch.

- **Scenario:** Randomize en0, type the password at about second 119, see "Timed out … may still
  have happened. en0 reports <original>". The address changes a second later. Quit does no restore.
- **Evidence:** fake probe (`scratchpad/scenarios.py`, case A). Output:
  `A timeout: record after set_mac = {} | pending at next launch = []`. The existing test
  `test_a_failed_change_with_an_unknown_outcome_keeps_the_record` only covers an *unreadable*
  address (`mac=""`), not a readable unchanged one. Whether a killed osascript really leaves the
  root child running was **not verified** on this Mac (no root actions); the codebase's own
  comment asserts it.
- **Fix direction:** on a timeout (or any `not ok` that is not `Cancelled.`), keep the record.
  `pending_restores()` at the next quit or launch forgets it safely once the address is seen
  back on the original.

### P2-2: quit-time restore forgets the record of a change still waiting in the dialog ("Quit anyway")

`main.py:6026` → `macaddr.py:264-277`. `set_mac` writes the record and then blocks in the admin
dialog on the Privacy tab's worker. When the user picks **Quit anyway** (`main.py:5907-5919`),
`_leave` calls `restore_all_recorded()` → `pending_restores()`. That sees the address still on
the original and calls `_forget`. `os._exit` leaves osascript running, because the child is not
killed. When the dialog is approved afterwards, the change lands with no record. That is the exact
crash-safety case "record before root runs" exists for, and the quit path undoes it.
`_remember`/`_forget` are also unsynchronised read-modify-writes, shared between the worker
thread and the quit path.

- **Evidence:** fake probe, case B. Output: `B quit-time restore results: [] | record after: {}`,
  then `B pending at next launch: []`. No test covers quit during an in-flight change.
- **Fix direction:** `_leave` should not forget entries while a privileged MAC worker is running.
  Alternatively, `pending_restores()` should only forget at startup and never on the quit path,
  so the next launch decides.

### P2-3: the record is not tied to a boot or a network, so a stale address is "restored"

`macaddr.py:251-277, 283-288`. The "original" is whatever `ifconfig` showed before the first change
(`:322`). On current macOS that is usually a **Private Wi-Fi Address**, which is per-network and
can rotate, not the hardware address. On this Mac, read-only, `en0 current==hardware: no` and
`current locally-administered bit: 1`. That is most likely a private address; it was not
confirmed whether macOS or something else set it. `pending_restores()` forgets a record only when
the current address equals the recorded one exactly, and the docstring claims "a reboot resets
it". With a private address it does not:

- **After a crash or a declined restore, followed by a reboot:** macOS comes back on a new private
  address. The launch offer then says "Sentinel changed … and did not put it back", which is false.
  If the user declines, the admin dialog reappears at *every* quit until accepted.
- **After a network change while Sentinel's address is in place:** if macOS applies network B's
  private address, the quit-time "restore" puts network A's private address on network B. That
  links the user across networks, which is exactly what per-network private addresses prevent.
  It also interacts with the still-open Medium 6. Whether macOS overrides an `ifconfig`-set
  address on rejoin was **not verified** on hardware.
- The admin prompt reads "Put back the original **hardware** address of en0" (`:288`) even when
  the target is not the hardware address.
- **Evidence:** fake probe, case C. Output:
  `C offered after reboot: [('en0', '06:aa:aa:aa:aa:aa', '0a:bb:bb:bb:bb:bb')]`.
- **Fix direction:** store the boot time (`sysctl kern.boottime`) and the hardware address with
  each entry. Drop entries from an earlier boot, since an `ifconfig` change never survives a
  reboot. Refuse an entry whose hardware no longer matches. Word the prompt as "original address".

### P3-1: quit can stall for minutes, and asks for a password at logout or shutdown

`main.py:6026`. The restore runs synchronously after `app.exec()` returns, so no event loop is
running. Each recorded device costs `list_interfaces` (one `networksetup` and one `ifconfig` per
port, 30 s timeout each) plus up to 120 s of dialog. Every wait has a timeout, so it never hangs
indefinitely. The window sits unpainted meanwhile, and a quit sent by logout or restart holds the
logout behind an admin dialog whose change the restart would undo anyway. **Not verified** live.

### P3-2: Emergency Reset erases the record before the quit restore runs

`ui/dialogs.py:1357-1365` erases the data folder, `vpn/mac_original.json` included, and then quits
through `_leave`. That finds nothing to restore, so a changed address stays changed until reboot.
Related privacy note: the record persists the pre-change address, often the real hardware address,
in the portable data folder (possibly a removable drive) for as long as a change is in place.
Both are acceptable if documented; the reset could also restore first.

### P3-3: the record is keyed by BSD name only

`macaddr.py:216-232`. If a different USB or Thunderbolt adapter later gets the same `enN`, the quit
restore writes the first adapter's address onto it. A same-user writer of the 0600 file can also
pick any valid unicast address for any real port, and the admin dialog will call it "the original
hardware address". Storing the hardware address with each entry (P2-3) closes both.

### P3-4: the startup offer runs `ifconfig` on the UI thread

`ui/panels/vpn_privacy.py:55-59, 281`. The comment says reading the record "runs nothing". That is
true of `recorded()`, but `offer_recorded_restore` then calls `pending_restores()`, which runs
`ifconfig` per entry (30 s timeout) and may write the file, all on the UI thread. It is bounded and
fast in practice. The workspace rule "nothing on the UI thread may fetch" is SONAR's, but the same
reasoning applies.

### P3-5: test gaps (the claims themselves are mostly right)

- **Mutation "drop `trap "exit 143" TERM INT HUP`" survives** (90 passed). Bash 3.2 runs the EXIT
  trap on a fatal signal anyway, so the TERM trap is redundant and the M3 claim still holds.
  `test_script_killed_mid_change_still_powers_wifi_on` proves the EXIT trap, not the TERM trap.
- **Mutation "drop `os.chmod(tmp, 0o600)`" survives.** `mkstemp` already creates 0600, so this is
  harmless. The 0600 test passes for that reason.
- No test for P2-1 (timeout with a readable, unchanged address), P2-2 (quit while in flight) or a
  record from an earlier boot.
- The dialog-path test checks `shlex.split` only. The full AppleScript plus `/bin/sh` round trip
  was verified by hand above, not by a test.

## Follow-up: P2 fixes (same day, same author as this report, uncommitted)

The owner asked the auditor to fix the P2s, so **these fixes are not independently reviewed**.
A different session must re-audit them before the Tier C item closes. All changes are in
`macaddr.py` and its tests. Tests came first; each new test failed before the fix.

- **P2-1:** `set_mac` forgets the record only after a known outcome, meaning success or
  `Cancelled.`. After a timeout or any other failure, the record stays until `pending_restores()`
  sees the original back. Tests: `test_a_failed_change_that_was_not_cancelled_keeps_the_record`
  (timeout and `exited 1`) and `test_a_late_landing_change_is_still_offered_after_a_timeout`.
- **P2-2:** a device is "in flight" from the record write until the root command returns
  (`_in_flight`, under an `RLock` that also guards every read-modify-write of the record).
  `pending_restores()` skips such devices, so a quit-time restore neither forgets nor restores a
  change still waiting in the dialog. Test: `test_quit_never_forgets_a_change_still_waiting_for_root`,
  which uses a real second thread blocked in the root call.
- **P2-3 (reboot half) and P3-3:** each entry is now `{original, hardware, boot}`, where `boot` is
  the `sec` of `sysctl -n kern.boottime`, cached per process. Entries from an earlier boot are
  dropped and pruned from disk. An unknown boot keeps them, because it is no proof of a reboot.
  `pending_restores()` now reads `list_interfaces()` once and forgets an entry whose hardware
  address no longer matches. The admin prompt says "original address". Tests:
  `test_a_record_from_an_earlier_boot_is_dropped`, `test_an_unknown_boot_keeps_the_record`,
  `test_another_adapter_under_the_same_name_is_not_given_the_address`,
  `test_the_record_holds_the_hardware_and_the_boot`, `test_boot_id_parses_kern_boottime`,
  plus damaged-record cases for the new format.
- **Not fixed: P2-3 network-change half.** Within one boot, if macOS replaces the address after a
  network change, the quit restore can still put network A's private address on network B.
  Fixing it needs one fact checked on a real Mac first: does macOS re-apply a per-network private
  address over an `ifconfig`-set one on rejoin? This belongs with the open Medium 6.
- P3-1, P3-2 and P3-4 are unchanged. P3-4 costs a little more now: when a record exists, the startup
  offer runs `networksetup` once, not just `ifconfig`, still on the UI thread.
- **Evidence:** `tests/test_macaddr.py` 71 passed. The MAC test files total 104 passed. 14 new
  hand mutations of the record logic were all killed (one survived at first; a test was added
  for it). Full suite: `3075 passed, 14 skipped, 1 deselected`. That run started before the
  last test (`test_a_malformed_boot_is_rejected_even_when_the_boot_is_unknown`) was added; that
  test passes in the 71. ruff: not run (not installed).

## Commands run

- Full suite, worktree, main checkout venv: `/Users/as/Documents/lab/active/sentinel/.venv/bin/python -m pytest -q -p no:cacheprovider`. Result: `3061 passed, 14 skipped, 1 deselected in 217.71s`. This matches the author's count.
- Mutations (scratch copy, three test files, `-x`):

| Mutation | Result |
|---|---|
| no TERM trap | survived |
| no chmod | survived |
| forget when the address is unreadable | killed |
| power-on failure not fatal | killed |
| `\Z`→`$` | killed |
| dialog `' '.join` | killed |
| pending keeps matched | killed |
| restore ignores prompt | killed |
| remember skipped | killed |

- Quoting probe and scenario probes A-C: scripts in the session scratchpad. Outputs are quoted above.
- Read-only: `networksetup -listallhardwareports`, `ifconfig en0`. Only equality and the
  locally-administered bit were recorded, never the addresses.
- ruff: not run (not installed).

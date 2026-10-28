# MAC changer: independent review 3 of `ef322d1` (2026-10-28)

- **Commit:** `ef322d1` "fix(macaddr): no hardware address on stderr at quit-time restore (review 1 P2-2,
  review 2 P3-A/P3-B)", drafted by Sonnet 5.5. Parent `01d3580`, whose tree is identical to `main`'s
  `13ed526` (`git diff 13ed526 01d3580` is empty).
- **Roles:** `code-reviewer` and `security-engineer`, Tier C review (author does not review its own work).
- **Reviewer:** Claude Opus 5.5, worktree `macaddr-lows-3-3e2b15`.
- **Mode:** read-only. No source edited, nothing committed. Mutants and probes ran on a scratch copy
  outside the repo. No live MAC, Wi-Fi, root or tunnel action was run. The only command that touched
  this machine's interfaces was a read of `ifconfig -a` that printed octet widths, never addresses.

## Verdict

**Ready to merge.** No P0, P1 or P2. The stderr leak (review 1 P2-2, Low 12) is closed for every message
that can reach that line today, and the P3-A and P3-B changes do what they say. Nothing from `13ed526`
regressed. Five P3s below; P3-1 and P3-2 are worth fixing before the testing phase, the rest whenever.

- Full suite: **3128 passed, 14 skipped, 1 deselected, 1 failed** in 211 s. The failure is the known
  unrelated `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` (starts
  the installed LM Studio CLI). This matches the author's report. (The first run hit 7 collection
  errors because `agents/bug_spray` was empty; `git submodule update --init` fixed it.)
- Mutants: **13 of 16 killed**. None of the three survivors is a defect in this commit: M8 is a test
  gap (P3-2), M14 is the open review 2 P3-2, and M16 is a near-equivalent that redacts more (P3-3).

## P2-2 / Low 12: redaction at quit-time restore. **Fixed.**

`main.py:6009-6014` passes both the failure message and `str(exc)` through
`macaddr.redact_addresses` (`agents/vpn_agent/services/macaddr.py:188-191`, pattern at `:42`).

**What can reach that line.** `restore_all_recorded` returns `set_mac`'s message. Every address in a
`set_mac` message comes from `interface.current`, `applied` (`current_mac`, the `ether` field of
`ifconfig`) or a recorded original (validated by `MAC_RE` in `_valid_entry`). All are padded colon
form. This was checked on this Mac: 12 `ether` lines, every octet two characters wide, 8 octets with a
leading zero. So `ifconfig` pads, even though libc's `ether_ntoa` does not (`2:0:a:bb:0:1` for the same
bytes). `osascript` errors carry the command's stderr, and `ifconfig`'s errors carry no address.

**Formats (probe, `redact_addresses`):**

| Input | Result |
|---|---|
| `02:AA:bb:cc:dd:ee` (mixed case) | redacted |
| `en5:02:aa:bb:cc:dd:ee`, `(…)`, `…\n`, `….` | redacted |
| `02:aa:bb:cc:dd:ee:ff` | `<address>:ff` |
| `02-aa-bb-cc-dd-ee` (dash) | **not redacted** |
| `02aa.bbcc.ddee` (Cisco dotted) | **not redacted** |
| `02aabbccddee` (no separator) | **not redacted** |
| `2:a:bb:c:dd:e` (unpadded) | **not redacted** |
| `x02:aa:bb:cc:dd:ee`, `_02:…` (glued to a word character) | **not redacted** |

None of the unredacted forms can reach the line today (see above), so there is no leak. But the
function and its test promise more than they do: see P3-3.

**Non-string input.** `None` gives `""` (probe: `hardware address of en5 not restored: `). `bytes` or
`int` raise `TypeError`; inside the loop that is caught by the outer `except`, and
`restore_all_recorded` has already run every restore, so only the report is lost. `set_mac` always
returns `str`. Not a finding.

**Does redaction hide a useful error?** It removes the address in "en5 reports …" and keeps the device,
the `ifconfig`/`osascript` reason and the Wi-Fi state. The record stays, and the launch-time offer in
the Privacy tab shows the full state on screen. Acceptable.

**Other paths to stderr, logs or files.** None found. `grep` for `print(`, `logging`, `stderr`,
`FileHandler`, `faulthandler` and `excepthook` across `macaddr.py`, `privileged.py`,
`ui/panels/vpn.py`, `vpn_privacy.py`, `vpn_gate.py` and `main.py`: the only MAC-related output is the
two redacted lines. The Privacy tab shows addresses only in labels and the combo box. The gate target
and the dialog prompt carry the device name only. The only file holding addresses is the 0600 record.
`profile_store.py` prints, but holds no MAC data.

Mutants M1-M5 (case-sensitive pattern, first match only, identity, either `main.py` line unredacted)
are all killed.

## P3-A (review 2 P3-1): a restore from an unrecordable current address. **Fixed, with one gap (P3-1).**

`macaddr.py:388-389` now refuses only when `device not in recorded()`. For the case it targets, a record
whose hardware matches: the change runs, `_remember` is a no-op (the first original wins, `:308`), and
`pending_restores` still offers the recorded original. Correct. `test_restore_to_a_recorded_original_…`
(`tests/test_macaddr.py:722`) kills M6 (clause removed) and M7 (inverted).

The brief's two abuse checks:

- **A record that `pending_restores` would drop still counts. Defect (P3-1).** `recorded()` (`:274`)
  filters by boot and validity, not by hardware. `pending_restores` also drops an entry whose hardware no
  longer matches (`:345`).
- **A record from an earlier boot.** It does not count while `boot_id()` can be read. When it cannot
  (`""`), every old entry counts. That is the open re-audit 2 P3 (4), which this commit inherits rather
  than introduces.

## P3-B: test for a stale first interface. **Fixed.**

`tests/test_vpn_privacy_tab.py:409` selects index 0, makes the fresh read differ, declines the dialog
and checks the combo item text. Mutant M9 (`vpn_privacy.py:273` `index >= 0` → `index > 0`) is killed
only by this test. Without it, M9 survived. The test guards what it claims.

## Regression check against `13ed526`. **None.**

| Invariant | Mutant | Result |
|---|---|---|
| Record written before root runs | M15: `_remember` removed | killed (`test_a_change_records_the_original_before_root_runs`) |
| Dialog only, no cached sudo | M10: `allow_cached_sudo=True` | killed |
| Device-name validation (`\Z`) | M11: `$` | killed (`[en0\n]`) |
| `ROOT_SCRIPT` EXIT trap | M12: trap removed | killed (`test_script_success_order`) |
| Medium 5: no vendor OUI | M13: OUI copied from `like` | killed |
| Root refusal keeps the record | M14: refusal forgets it | **survived**: open review 2 P3-2, unchanged |

The diff does not touch `ROOT_SCRIPT`, `privileged.py`, `ROOT_REFUSAL`, `_remember`, `_forget` or
`random_mac`. As root, the quit-time line now reads `ROOT_REFUSAL` + " en5 reports <address>.".

## Findings

### P0 / P1 / P2

None.

### P3

- **P3-1 · `agents/vpn_agent/services/macaddr.py:388` · the P3-A gate counts a record that describes
  another adapter.** The gate asks `device not in recorded()`, but `recorded()` ignores the entry's
  `hardware`. Suppose a kept entry names en5 with hardware H_OLD: an unplugged adapter, kept on purpose
  by `pending_restores`. A different adapter now holds en5, with current `00:00:00:00:00:00`. The gate
  then lets a change run. `_remember` skips because an entry exists, and `pending_restores` later drops
  the entry for the hardware mismatch, so nothing restores the change. Probe (scratch):
  `SET: True en5 is now 02:11:22:33:44:55. root calls: 1`, record unchanged
  `{'original': '06:aa:…', 'hardware': 'aa:bb:cc:00:00:01'}`, `PENDING: []`. Before this commit the
  change was refused. This is the open re-audit 2 P3 (3), "a kept entry for an unplugged adapter blocks
  the record of a new adapter", reaching one more input. It is rare: a BSD name reused within one boot,
  plus an unreadable-as-address current. Fix: open the gate only for an entry whose `hardware` equals
  `interface.hardware`, the same predicate as `:345`. For example, read `_read_entries()[0].get(device)`
  and compare.
- **P3-2 · `tests/test_macaddr.py:722` · no test pins which record opens the gate.** Mutant M8
  (`device not in recorded()` → `not recorded()`: any device's record opens it) survives the focused
  suite. Under M8, a record for en0 lets an all-zero en5 change run, and `_remember` then writes an entry
  that `_valid_entry` drops, so nothing is restorable. Add two cases: a record for another device, and a
  record for this device with other hardware (the second also pins P3-1).
- **P3-3 · `macaddr.py:42,188-191`, `tests/test_macaddr.py:686` · "every hardware address" covers one
  format.** The pattern matches only padded colon form at word boundaries (table above). There is no
  leak today, because every source is padded colon form. But the docstring says "every hardware
  address", the test is named `…_strips_every_form` and tests one form, and a future message source
  (`arp`, libc `ether_ntoa`, a dashed address in an exception) would pass through. Mutant M16 (drop
  `\b`) survives, so the boundaries are unpinned. Since redaction should fail toward hiding, consider
  `(?<![0-9a-f])[0-9a-f]{1,2}([:-])(?:[0-9a-f]{1,2}\1){4}[0-9a-f]{1,2}(?![0-9a-f])` plus the dotted and
  12-hex forms, and a parametrised test per form. Or narrow the docstring and test name to what is
  matched.
- **P3-4 · `main.py:6013` · the except handler depends on the module it guards.** It re-imports
  `macaddr`. If that import had failed, the handler raises, and `_leave` (`:6029`) skips `settings.sync`
  and `os._exit`, which breaks "It must never stop the quit" (ADR 0001). Probe with the import made to
  fail: `ESCAPED: ModuleNotFoundError`; the parent commit's handler could not raise this way. It cannot
  happen in the app today, because `main.py:253` imports `ui.panels.vpn` → `vpn_privacy` → `macaddr` at
  startup, so a failing `macaddr` stops the launch first. Hardening: catch inside the handler and fall
  back to printing only the exception type.
- **P3-5 · `TODO.md:130,502`, commit message · traceability.** "P3-A" and "P3-B" do not exist in
  review 2, which numbers P3-1 to P3-5. P3-A is review 2 P3-1, and P3-B has no source finding in either
  review. `TODO.md:502` lists "Open: P3-2 (interface read … UI thread)". That is review 1's P3-3, and the
  line drops review 1's other open P3s (P3-2 `MAC_RE` `$`, P3-4 unused imports, P3-6
  `manual_test_cases.md`) and review 2's P3-2 to P3-5. `TODO.md:130` still says "P3-6 unredacted address
  on stderr at quit-time restore stay[s] open" and "P3s 1-5 open", which now contradicts `:502`.

## Not findings (checked)

- `None`/`bytes` messages (see above). `set_mac` always returns `str`.
- Redacting the address in "en5 reports …" loses no information the user needs. The record stays, and
  the launch-time offer shows it on screen.
- `/tmp/sentinel-launch.log` is set by `scripts/install_app.sh:66`. Its mode was not re-checked here;
  0644 is taken from review 1.
- The `recorded()` read at `:388` happens outside `_lock` before `_remember` takes it. A concurrent
  `_forget` between them is the same as the record being absent. It is not exploitable beyond P3-1.

## Mutants (scratch copy; `tests/test_macaddr.py`, `test_vpn_privacy_tab.py`, `test_privileged_runner.py`)

| # | Mutant | Result |
|---|---|---|
| M1 | redaction pattern case-sensitive | killed |
| M2 | redact first match only | killed |
| M3 | redaction is identity | killed |
| M4 | `main.py` failure line unredacted | killed |
| M5 | `main.py` exception line unredacted | killed |
| M6 | P3-A clause removed | killed |
| M7 | P3-A clause inverted | killed |
| M8 | P3-A gate opened by any device's record | **survived** (P3-2) |
| M9 | `index >= 0` → `index > 0` | killed (only by the P3-B test) |
| M10 | cached sudo allowed | killed |
| M11 | `DEVICE_RE` `\Z` → `$` | killed |
| M12 | EXIT trap removed | killed |
| M13 | vendor OUI copied | killed |
| M14 | root refusal forgets the record | **survived** (open review 2 P3-2) |
| M15 | record not written before root | killed |
| M16 | redaction `\b` removed | **survived** (near-equivalent: redacts more; P3-3) |

Probes: `test_probe_review3.py` in the session scratchpad (import failure in the quit handler,
`None` message, record for other hardware, unknown boot). Not part of the repo.

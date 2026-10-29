# MAC changer review 4: independent review of `8092386` (2026-10-28)

**Scope.** `8092386` on `claude/jolly-cerf-3b0292` (reviewed in worktree `claude/macaddr-4-beca31`), parent
`b3b0d64`, which `main`'s `b12f150` contains (`git log b12f150..8092386` lists only `8092386`). It claims to
fix review 3's (`docs/audit/macaddr-lows-review-3-2026-10-28.md`) P3-1, P3-2 and P3-3. Tier C review:
the author (`claude-sonnet-5-5`) did not review it. Roles: `code-reviewer` and `security-engineer`,
read-only. Nothing in the repo was changed except this file.

**Method.** Read the diff and the record code (`macaddr.py:209-445`). Probed `set_mac` and
`pending_restores` with every root call, interface read and boot ID mocked (scratch `probe4.py`). Planted
23 mutants in a scratch copy of the tree against `tests/test_macaddr.py`, `test_vpn_privacy_tab.py`,
`test_privileged_runner.py` and `test_vpn_companion_libs.py` (scratch `mutants4.py`). Ran the full suite.
No live MAC, Wi-Fi, root or tunnel action was run. The only commands touching the machine were read-only
`ifconfig` and `networksetup -listallhardwareports`, piped to `grep -c` so no address was printed.

## P3-1: the guard opens only for a record `pending_restores` would act on. **Fixed.**

`macaddr.py:392` now reads `restorable = (_read_entries()[0].get(device) or {}).get("hardware") ==
interface.hardware`. That is the same predicate `pending_restores` applies (`:345`), over the same
boot- and validity-filtered entries (`_read_entries`, `:254-273`). Both sides compare lowercase: the record
is lowercased on read (`:266`), `interface.hardware` on parse (`:132`). With no entry, `None == "aa:…"` is
false, so the guard stays closed.

Probe results (all-zero current unless noted; "refused" means 0 root calls):

| Case | Result |
|---|---|
| No record | refused |
| Record for en0, same hardware | refused |
| Record for en5, other hardware (review 3's scenario) | refused; `pending_restores` → `[]` |
| Record for en5 from an earlier boot, boot ID readable | refused |
| Record for en5 from an earlier boot, boot ID unreadable (`""`) | **runs**; see "Carried" |
| Record for en5 whose original is multicast (dropped by `_valid_entry`) | refused |
| Record for en5, hardware stored uppercase | runs (normalised; correct) |
| Restore to the recorded original (the legitimate case) | runs, record cleared afterwards |
| Random address from all-zero, matching record | runs; record keeps the first original |
| Multicast current, matching record | runs |
| Record file not a dict | refused |

So a stale record (other hardware), another device's record, an earlier boot's record and an entry
`pending_restores` would drop cannot open the guard, and the legitimate restore still runs. The one way in
is an unreadable boot ID. In that case `pending_restores` also keeps and offers the entry, so the guard
still matches its contract. That is the open re-audit 2 P3 (4), not something this commit adds.

## P3-2: a test pins which record opens the guard. **Fixed.**

`tests/test_macaddr.py:688-699` adds a record for another device and a record for this device with
other hardware. Mutants G1–G4 below each die on one of them. Review 3's M8 (`not recorded()`) is G2 here
and is now killed.

## P3-3: redaction formats. **Closed by narrowing, not by testing more formats.**

The brief for this review says the commit tests `redact_addresses` on more than one format. It does not.
It took review 3's second option: the docstring (`macaddr.py:189-192`) and the test name
(`test_redact_addresses_strips_the_padded_colon_form`, `tests/test_macaddr.py:707`) now say what is
matched. The test still uses one string with two padded colon addresses (one mixed-case). Probe:

| Form | Lower | Upper |
|---|---|---|
| `02:aa:bb:cc:dd:ee` (padded colon) | redacted | redacted |
| `02-aa-bb-cc-dd-ee` (dash) | **kept** | **kept** |
| `02aa.bbcc.ddee` (dotted) | **kept** | **kept** |
| `02aabbccddee` (no separator) | **kept** | **kept** |
| `2:a:b:c:d:e` (unpadded) | kept | — |

This matches the new docstring, and there is no leak today. Every address that reaches the quit-time
stderr line comes from `ifconfig` (`current_mac`, `:140-148`), `networksetup` (`:128-133`) or the record,
all in padded colon form. On this Mac, 12/12 `ifconfig` `ether` lines and 6/6 `networksetup` address lines
are padded colon form (counts only). See P3-B for what remains.

## Regression check against `b12f150`. **None.**

The diff touches only `set_mac`'s guard, a docstring, two tests, a test name and `TODO.md`. Mutants on
the invariants (table below): R1 original recorded before root, R2 dialog only, R3 device-name `\Z`,
R4 `ROOT_SCRIPT` EXIT trap, R5 no vendor OUI, R6 same-vendor mode gone, R7 `ROOT_REFUSAL` as root,
R8–R11 Low 12 redaction (module and both `main.py:6011/6014` lines). All killed.

## Findings

### P0 / P1 / P2

None.

### P3

- **P3-A · `tests/test_macaddr.py:688-699` · no test pins the boot filter at the guard.** Mutant G9 makes
  the guard read the raw record file, skipping `_read_entries`' boot and validity filter. It survives all
  four test files. The code is right: probe "earlier boot, boot ID readable" is refused. But the brief's
  "an earlier boot's record can't open it" holds only through `_read_entries`, and no test would catch a
  refactor that reads the record another way at `:392`. Fix: one more case in the same block, with
  `_write_record({"en5": …}, boot="999")` and an all-zero current, asserting refused with no root calls.
- **P3-B · `macaddr.py:42,188-193` · redaction is still colon-only and `\b`-bounded.** This is the
  residual of review 3 P3-3, now documented instead of hidden. Dash, dotted and no-separator forms pass
  through. An address glued to a word (`ether02:aa:…`) is not matched, because `\b` needs a boundary. An
  8-group colon run (`02:aa:bb:cc:dd:ee:ff`) leaves `:ff`. Mutant R12 (drop `\b`) still survives. None of
  these is reachable from today's sources, so this is acceptable. If a new message source is ever
  redacted (an exception text from another library, `arp`), broaden the pattern then, failing toward
  hiding.
- **P3-C · `tests/test_macaddr.py:683` · `_unrecordable(monkeypatch, root)` never uses `root`.** Cosmetic.
  The two new tests also do not assert that the record file is unchanged after the refusal; the probe
  shows it is.
- **P3-D · `TODO.md:130` · traceability.** The line now credits P3-1 to P3-3 to "the follow-up commit"
  without a hash. Its "Verified" clause still says "full suite 3079 passed", while this commit's trailer
  and this review both measure 3130. Review 3 P3-5 (the labels) stays open, as the line says.

### Carried, not introduced here

- Re-audit 2 P3 (4): with an unreadable boot ID, an earlier boot's entry counts. The guard inherits it
  (probe above) and stays consistent with `pending_restores`.
- Review 3 P3-4 (`main.py:6013` re-imports `macaddr` in the handler that guards it): untouched, still open.

## Not findings (checked)

- The guard reads the record outside `_lock`, and `_remember` takes the lock later (`:392` vs `:407`).
  A concurrent `_forget` in that gap would let `_remember` record the all-zero current, which
  `_valid_entry` then drops. Review 3 already noted this window and judged it not exploitable beyond
  its P3-1. This commit neither widens nor closes it. Not re-verified which callers can overlap.
- An extra `_read_entries()` per `set_mac` replaces the `recorded()` read: same file, same cost.
- Mixed-case hardware in the record file is normalised on read, so it opens the guard correctly.

## Mutants (scratch copy; the four test files above)

| # | Mutant | Result |
|---|---|---|
| G1 | guard back to `device in recorded()` (pre-fix code) | killed (other-hardware test) |
| G2 | any device's entry opens it | killed (other-device test) |
| G3 | hardware comparison dropped | killed (other-hardware test) |
| G4 | any entry with this hardware opens it | killed (other-device test) |
| G5 | compared to `interface.current` | killed (restore-to-original test) |
| G6 | always restorable | killed |
| G7 | never restorable | killed (restore-to-original test) |
| G8 | `==` → `!=` | killed |
| G9 | guard reads the raw file, no boot/validity filter | **survived** (P3-A) |
| G10 | `and not restorable` → `or not restorable` | killed |
| R1 | `_remember` before root removed | killed |
| R2 | `allow_cached_sudo=True` | killed |
| R3 | `DEVICE_RE` `\Z` → `$` | killed |
| R4 | EXIT trap removed | killed |
| R5 | vendor OUI copied from `like` | killed |
| R6 | same-vendor mode back in `MODES` | killed |
| R7 | `run_argv_as_root` runs as root instead of refusing | killed |
| R8 | redaction is identity | killed |
| R9 | redaction case-sensitive | killed |
| R10 | `main.py` failure line unredacted | killed |
| R11 | `main.py` exception line unredacted | killed |
| R12 | redaction `\b` removed (review 3 M16) | **survived** (near-equivalent: redacts more; P3-B) |
| R13 | `ROOT_REFUSAL` no longer counts as settled | killed |

21/23 killed. G9 is a real gap; R12 is near-equivalent.

## Tests

- `tests/test_macaddr.py`: 107 passed (matches the commit trailer).
- The four focused files: 172 passed.
- Full suite (`.venv/bin/python -m pytest -q`, after `git submodule update --init`):
  **1 failed, 3130 passed, 14 skipped, 1 deselected** in 222 s. The failure is
  `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`
  (`ForbiddenInTests`: starts `~/.lmstudio/bin/lms`), the known unrelated one.
- `ruff`: not run (not installed).

## Verdict

**Ready to merge.** P3-1 and P3-2 are fixed and mutation-checked. P3-3 is closed honestly by narrowing the
claim, with no leak from any current source. No P0–P2, and no regression against `b12f150`. P3-A (one
test for the boot filter at the guard) is worth adding before or soon after the merge. P3-B to P3-D can
wait.

# Review: the four open MAC P3s (uncommitted diff on `claude/macaddr-open-p3s`)

- Date: 2026-11-05. Reviewer: `code-reviewer` (Opus 5.5), read-only, a different agent from the author
  (Opus 5.5). Saved by the author's session from the reviewer's report (the reviewer has no write tool).
- Checkout: worktree `fix-screenshot-test-7cb7e3`, base `f643a6a`. Diff: `agents/vpn_agent/services/macaddr.py`,
  `ui/panels/vpn_gate.py`, `tests/test_macaddr.py`, `tests/test_vpn_privacy_tab.py`.
- Playbook: `docs/playbooks/code-reviewer.md`, `status: current`, `last-reviewed: 2026-10-14` (22 days, not stale).
  `docs/company/app-context.md` is absent from the worktree; read from the main checkout.
- The worktree was unchanged by the review; mutants and probes ran in a scratch copy.

Findings answered:
1. `macaddr-p3s-review-2026-11-05.md` P3-2, UI half: `_gated` reserves the tab only after its confirmation.
2. Same review, P3-4 second half: devices skipped at quit as busy are not named.
3. `macaddr-p3s-review-2-2026-11-05.md` P3-3: while the boot is unknown, an earlier boot's entry can be offered.
4. `macaddr-review-4-2026-10-28.md` P3-B: redaction is colon-only and `\b`-bounded.

## Verdict
**Mergeable after a small fix to P3-1. No P0, P1 or P2.** All four findings are closed for their scenarios,
each with a test that fails without its fix. Counts: **P0 0 · P1 0 · P2 0 · P3 4.**

## Evidence
| Check | Method | Result |
|---|---|---|
| MAC + VPN tab tests (9 files) | `QT_QPA_PLATFORM=offscreen pytest` on the MAC, Privacy, Servers, Apps, review-gate, worker-shutdown and quit-privileged tests | 521 passed, 2 skipped |
| Full suite | whole `tests/` | 3341 passed, 14 skipped; `test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` deselected: it fails on base `f643a6a` too (LM Studio CLI, environment) |
| Callers of the changed shapes | grep `restore_all_recorded`, `pending_restores`, `redact_addresses` outside tests | `main.py:6007-6016` (quit), `vpn_privacy.py:397` (offer read, shape unchanged), `vpn_privacy.py:423-427` (accepted offer, now gets held rows: P3-1), `macaddr.py:132` (networksetup error) |
| Who reads `busy` during a dialog | grep `.busy` / `_active` in `ui/` | Only a timer or `showEvent` can run inside the modal. `refresh_interfaces` returns early; `refresh_tor` may turn Tor buttons off; both are put right after (No: `_offer_pending` → `refresh_interfaces`; Yes: `_after_finish`). `has_privileged_work` reads `isRunning()`, so the quit prompt is unchanged |
| Lock discipline in `_pending` | read | Unchanged: record and `_in_flight` under `_lock`; ps, `boot_id()` and listing outside it; the re-check before `_forget` still under it |
| Addresses in new messages | `HELD_BUSY` / `HELD_NO_BOOT`; `test_the_quit_prints_the_held_device_without_an_address` | Constant text, no address; main prints device + redacted message |
| Probe A: accepted offer with one device held | record en5 + en6 changed, `_changes_elsewhere → {"en5"}` | status: "a change to it was still waiting … next launch en6 is now …", `ok` False (P3-1) |
| Probe B: boot read fails, then succeeds | en5 `boot="OLD-BOOT"`, `boot_id` answers `""` then `BOOT` | the earlier boot's entry is offered (P3-2) |
| Hand mutants | scratch runner, subprocess `timeout=300`, `-x`, file restored | 19 planted, 16 killed, 3 survived |

## Each fix against its finding
| Finding | Fix | Status |
|---|---|---|
| P3-2 UI half | `vpn_gate.py`: `_active=True` around `_ask`, released in `finally` | **Closed.** G1 killed. Every `_gated` caller (Privacy ×8, Servers ×9, Apps ×2) uses only its bool, unchanged. Release on exception untested (P3-3) |
| P3-4 second half | `macaddr._pending` returns held rows; `restore_all_recorded` reports `(device, False, HELD_BUSY)`; main prints them | **Closed.** M1, M2, M6, M7 killed. The changed old test pinned exactly the silence the finding described. A busy device is reported even if already back or unplugged: matches "outcome unknown" |
| Review 2 P3-3 | nothing offered while `boot_id()` is `""`; changed entries held as `HELD_NO_BOOT` | **Closed for the reported sequence.** M3-M5 killed. Entries back on their original or of another adapter still forgotten. Leftover: P3-2 |
| Review 4 P3-B | colon/dash runs of 6+ groups of 1-2 hex, no boundaries; dotted; 12 bare hex | **Closed.** R1-R6, R8 killed; no tail of a longer run (R3); ordinary text kept (R9 killed). Over-redacts IPv6 groups and a UUID's tail, as documented; only kept-output users are the quit stderr and the networksetup error |

## Findings

### P3-1 The tab's accepted offer shows a held device's message unnamed, and marks the result failed
- **Where:** `ui/panels/vpn_privacy.py:422-427`, fed by held rows from `macaddr.restore_all_recorded`.
- **Scenario:** at launch en6 is changed after a crash; an orphaned dialog still holds en5. The offer lists en6
  only; on Yes the status reads "a change to it was still waiting …, offered again at the next launch en6 is now …":
  "it" names nothing, the device was never offered, sentences run together, and `ok` is False. Wording only;
  the record is kept. `HELD_NO_BOOT` cannot reach this path (an offer needs a known boot, which is then cached).
- **Fix:** name held rows (`f"{device}: {message}"`), compute `ok` over offered devices; add a tab test.

### P3-2 `_pending` reads the boot ID twice, so an earlier boot's entry can still be offered
- **Where:** `macaddr._read_entries` filters by `boot_id()`; `_pending` reads `boot_id()` again for `known_boot`.
- **Scenario:** sysctl fails in `_read_entries` (every entry kept, one with `boot="OLD"`), then succeeds;
  `known_boot` is True and the old entry is offered (Probe B). Needs a fail-then-succeed within milliseconds and the
  user's yes to a dialog showing both addresses.
- **Fix:** after the boot is known, drop entries whose `boot` differs; add Probe B as a test.

### P3-3 Test gap: the dialog's `finally` release is unpinned
- **Where:** `ui/panels/vpn_gate.py`. G2 (release only on decline) and G3 (no `finally`) survive. If `_ask` raised,
  the tab would stay busy until relaunch.
- **Fix:** a case where `QMessageBox.question` raises; assert it propagates and `not tab.busy`.

### P3-4 Tracked items not updated
- TODO.md still lists all four as open; no CHANGELOG entry. Update both; record P3-2 as the narrowed leftover.

## Not findings (checked)
- R7 (bare-12 without lookarounds) survives: near-equivalent, errs toward hiding.
- Between the `finally` and `_active=True` before the worker starts, nothing spins the event loop.
- The retry timer re-arms every 500 ms while a confirmation is open: harmless; it runs once the tab is free.
- Tunnel rules: no subprocess, shell or root-path change; held devices stay recorded; no address in kept output.

## Mutants (reviewer)
| # | Mutation | Result |
|---|---|---|
| G1 | no reserve before the dialog | killed |
| G2 | release only on decline | **survived** (P3-3) |
| G3 | no `finally` | **survived** (P3-3) |
| M1 | `held = []` | killed |
| M2 | `restore_all_recorded` ignores held | killed |
| M3 | drop the unknown-boot branch | killed |
| M4 | `known_boot = True` | killed |
| M5 | unknown boot skipped silently | killed |
| M6 | held reported `ok=True` | killed |
| M7 | held only for other processes | killed |
| R1 | dash form dropped | killed |
| R2 | padded groups only | killed |
| R3 | exactly 6 groups | killed |
| R4 | dotted form dropped | killed |
| R5 | bare-12 form dropped | killed |
| R6 | `\b` restored on the colon form | killed |
| R7 | bare-12 without lookarounds | **survived** (near-equivalent) |
| R8 | case-sensitive | killed |
| R9 | any run of 4+ hex | killed |

Author's own mutants before the review: 13/13 killed (G1, G2, H1, H2, B1-B3, R1-R6).

## Resolutions (author's session; not re-reviewed)
- **P3-1 fixed (naming):** the accepted offer prefixes a held row with its device (`en5: a change to it was still
  waiting … launch.`). Test `test_an_accepted_offer_names_a_held_device`; mutant
  "held not named" killed. The `ok` half was not changed: the offer's `after` ignores `ok` and always shows the
  status, so a mutant computing `ok` over offered devices only survived as equivalent and was reverted.
- **P3-2 fixed:** `_pending` reads the boot once more and skips an entry whose `boot` differs from it (kept while the
  boot was unknown). Test `test_an_earlier_boots_entry_kept_by_a_failed_read_is_not_offered`; mutant killed.
- **P3-3 fixed:** `test_a_confirmation_that_raises_does_not_leave_the_tab_busy` calls `_gated` with a raising
  dialog; mutant "no `finally`" killed.
- **P3-4 fixed:** TODO.md and CHANGELOG updated in the same commit.

# Independent review of 45d7fe2 (fixes for the review of 5a81ab9, MAC changer review-1 P3-2)

Reviewer: Opus 5.5, read-only (`code-reviewer` + `security-engineer`), 2026-10-29.
Scope: `45d7fe2` on `claude/macaddr-p32` (parent `5a81ab9`), checked against `main` at `185356f`.
Tier C review: the author (Sonnet) did not review this.

**Verdict: ready to merge.** No P0, P1 or P2. Each fix closes its finding and has a test
that fails without it. Three P3s remain: one is a defect already on `main` and two are test gaps.

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Full suite on the branch | `.venv/bin/python -m pytest -q` in this worktree (HEAD `45d7fe2`) | 3140 passed, 14 skipped, 1 failed (`test_providers_registry::test_server_off_falls_back_to_the_disk_listing`, starts the LM Studio CLI; known and unrelated) |
| Merge with `main` | `git merge-tree --write-tree HEAD 185356f` | no conflicts (tree `134537a`) |
| MAC/gate tests on the merged tree | the tree archived into a scratch directory, with `agents/bug_spray` copied in and run from that directory | `test_macaddr.py`, `test_vpn_privacy_tab.py`, `test_quit_privileged.py`: 168 passed |
| Full suite on the merged tree | same scratch copy, `pytest -q` | 3142 passed, 14 skipped, 1 failed (the same LM Studio test) |
| Hand mutants | 13 planted one at a time, each followed by those three files, then restored | 10 killed, 3 survived (1 equivalent, 2 test gaps: P3-2, P3-3) |
| Reviewer probes | 6 temporary tests (deleted afterwards), plus the same probes on an archive of `185356f` | see "Button state" |

No live MAC, Wi-Fi, root or tunnel action was run. Every probe and mutant runs against the
mocked `env` fixture.

## Each fix against its finding

| Finding | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P2-1: after a read, `release()` switched every button on | `release()` calls `_controls_changed()` after `_set_enabled(True)` (`ui/panels/vpn_gate.py:85-88`); `PrivacyTab._controls_changed` runs `_show_mac`, `refresh_tor` and `_refresh_chain` (`ui/panels/vpn_privacy.py:203-206`) | Yes, for the read path | M1 (no call), M2 (call before `_set_enabled(True)`), M3 (no `refresh_tor`) and M4 (no `_refresh_chain`) are all killed by `test_buttons_that_were_off_stay_off_after_a_declined_read` |
| P3-1: quitting during a read claimed an administrator action | `_read_only` flag set by `_read_off_thread` (`vpn_gate.py:82`), cleared by `_gated` (`:53`), checked in `has_privileged_work` (`:124`) | Yes | M6 and M7 killed by `test_a_running_read_is_not_an_administrator_action`; M8 (`_gated` leaves the flag set) killed by `test_a_running_change_still_counts_as_privileged_work` |
| P3-2: `networksetup` error neither redacted nor bounded | `redact_addresses(...)[:200]` (`agents/vpn_agent/services/macaddr.py:126`) | Yes | M10 (no redaction) and M11 (no bound) killed by `test_a_failed_listing_error_is_bounded_and_holds_no_address` |
| P3-3: interface picker usable during the read | `mac_device_box` added to `_set_enabled` (`vpn_privacy.py:213`) | Yes | M9 killed by `test_the_interface_picker_is_off_while_reading` |

## Button state, path by path

| Path | Expected | Observed |
|---|---|---|
| Read succeeds, confirm declined | MAC buttons on; Tor Stop, Check, New identity off (Tor not ours); Test chain off (empty chain) | Correct (author's test, and M1–M4) |
| Read succeeds, confirm accepted, change finishes | as above | **Test chain on** (P3-1, already on `main`) |
| Listing fails (`networksetup` error) | as for "declined"; refused with "Could not read" | Correct (author's test) |
| Interface gone during the read | en0 still selectable, Tor and chain off | Correct (probe) |
| Worker raises during the read (`error_signal`) | as for "declined" | Correct in the code (probe), **not pinned by any test** (P3-2) |
| Second click while reading | refused as busy; every action button and the picker stay off; `busy` stays True | Correct (probe: Randomise, Restore, Tor Start/Stop, Test chain and the picker all off) |
| Tab closed mid-read | `shutdown()` cancels, so `CallWorker.run` drops the result and nothing reaches the tab; past 2 s the worker is detached with its slots dropped; no `terminate()` | Correct (`ui/workers.py:674-684`, `:103-114`; probe on the real `CallWorker`) |
| Quit while reading | no Wait/Quit prompt about an administrator action | Correct (P3-1 fix) |

## Thread safety

- The worker runs only `macaddr.read_interface(device)`, with `device` captured as a `str`
  (`vpn_privacy.py:299`). It touches no widget. `read_interface` → `list_interfaces_checked` →
  `privileged.run` takes no lock: `_lock` (`macaddr.py:241`) guards only the record and
  `_in_flight`, and the read path never reaches it. No lock is held across the read, so
  the SONAR freeze (the UI thread waiting on a lock held across a fetch) cannot happen here.
- `_read_only`, `_worker` and `_active` are read and written on the UI thread only (signals
  are delivered there; `has_privileged_work` is called from the quit path on the UI thread).
- No `QThread.terminate()` was added.
- `_controls_changed` does run `refresh_tor()` on the UI thread after every read: a 0.4 s
  localhost connect, plus `ps` (timeout 3 s) when a pid file exists, plus a full read of
  `tor.log` (`agents/vpn_agent/services/tor.py:153-163, 215-239, 305-313`). That is the same work
  `_after_finish` and `showEvent` already do on the UI thread, so it is not a new class of
  blocking. In practice it is milliseconds, and it holds no lock. It is listed under P3-1's
  suggestion rather than as its own finding.

## Regressions against `main` (`185356f`)

`45d7fe2` changes one line of `macaddr.py` (`:126`). It does not touch `ROOT_SCRIPT`
(`macaddr.py:53`), the `trap power_on EXIT` (`:65`), the `ROOT_REFUSAL` handling (`:437`), the
original-address record, the record guard or `redact_addresses`. The branch is behind `main` by
`8092386`, `0d534b9`, `e4af1d1` and `8bda584` (record guard, docs, review 4). The merge is clean,
and on the merged tree `main`'s record-guard tests (including
`test_a_record_with_other_hardware_does_not_open_the_guard`) and the Low 12 redaction tests pass
next to this branch's tests. `set_mac` and `restore` still go through `get_interface` →
`list_interfaces`, whose return value is unchanged.

## Findings

### P3-1: Test chain is switched on after any approved action, even with an empty chain (already on `main`)
`ui/panels/vpn_gate.py:103-107` (`_finish`) and `ui/panels/vpn_privacy.py:208-210` (`_after_finish`).
`_finish` calls `_set_enabled(True)` and then `_after_finish`, which recomputes the MAC buttons
(through `refresh_interfaces` → `_show_mac`) and the Tor buttons, but not `chain_test_btn`. This is
the P2-1 pattern on the `_gated` path, which `45d7fe2` did not set out to fix.
Evidence: with an empty chain, after an approved Randomise or Tor Start, `chain_test_btn.isEnabled()`
is True (probe). The same probe on an archive of `185356f` fails the same way, so this is not a
regression. Impact is cosmetic: `test_chain` re-checks `problems()` (`vpn_privacy.py:488-492`),
and an empty chain ("the chain has no proxies in it", `proxychain.py:71-72`) is refused before
anything is sent, so there is no clear-net request.
Suggestion: have `PrivacyTab._after_finish` call `_refresh_chain()` (or `_controls_changed()`),
and add a test for the accepted path.

### P3-2: The worker-error branch of the read is not pinned to recompute the buttons
`ui/panels/vpn_gate.py:94-96`. Mutant M12 (`failed` re-enables without `_controls_changed`)
**survived** all 166 targeted tests: `test_a_worker_failure_during_the_read_frees_the_tab`
(`tests/test_vpn_privacy_tab.py:481`) checks `busy` and the status but not the buttons. The code
is right today, because `failed` calls `release()`. The path can be reached: `privileged.run`
catches only `TimeoutExpired`/`OSError`, so a `UnicodeDecodeError` from `text=True` would reach
`error_signal`.
Suggestion: assert `not tor_stop_btn.isEnabled()` and `not chain_test_btn.isEnabled()` in that test.

### P3-3: The test does not pin the order of redaction and truncation
`agents/vpn_agent/services/macaddr.py:126`. Mutant M13 (`redact_addresses(...[:200])`, truncate
first) **survived**. The code redacts first, which is correct. Truncating first would cut an
address that straddles character 200 and let its first octets through unredacted, because
`_ANY_MAC_RE` needs all six. The test (`tests/test_macaddr.py:768`) puts the address at the start,
so it cannot tell the two orders apart.
Suggestion: add a case with the address at offset ~190.

### Not a finding
- M5 (`_controls_changed` without `_show_mac`) survived, but it is equivalent. A read only starts
  when an interface is selected (`vpn_privacy.py:276-279`), and the list cannot change during the
  read because Refresh and the picker are off. So after `_set_enabled(True)` the MAC buttons are
  already right on every path that does not call `_show_mac` itself.
- The read's `failed` message is passed through `redact_secrets`, not `redact_addresses`
  (`vpn_gate.py:96`). It only reaches the status label, nothing keeps it, and no read path
  formats an address into an exception. No action needed.
- Review 1's P3-4 (`refresh_interfaces` still on the UI thread; `set_mac`/`restore` say
  "No such interface" for a failed listing) is still open, as `TODO.md` records.

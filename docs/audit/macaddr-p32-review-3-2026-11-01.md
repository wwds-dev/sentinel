# Independent review 3: MAC changer `9b8ab47` + `28d0f4a` (review of `31162a9` P3-1/P3-2)

- Date: 2026-11-01. Reviewer: code-reviewer (Opus 5.5), read-only, Tier C.
- Checkout: scratch clone detached at `bb37dfc` (local `main` tip), submodule initialised.
- Scope: `9b8ab47` fix(macaddr): one listing decides the interface and the refusal wording; `28d0f4a` test(macaddr): stub `read_interface` in two merged guard tests; merges `8f5aa96` and `bb37dfc` checked only for MAC-changer damage.
- Playbook: `docs/playbooks/code-reviewer.md` `last-reviewed: 2026-10-14`, `status: current` (18 days old, not stale).
- `docs/company/app-context.md` is gitignored (`.gitignore:66`), so it is absent from the scratch checkout; read from the main checkout instead.

## Verdict

**Merge-ready.** No P0, P1 or P2. Four P3s (one cosmetic wording bug, one test-scope gap, two documentation/process items). The single-read logic is correct on all three paths (listing fails, interface missing, present) for `set_mac`, `randomize` and `restore`; no caller of `get_interface` remains in code; the merges dropped nothing from `macaddr.py`, its callers or the tests.

Counts: P0 0 · P1 0 · P2 0 · P3 4.

## What was checked

### Single-read logic (`agents/vpn_agent/services/macaddr.py:168-187`, `:405-407`, `:472-474`, `:480-482`)
`_find_interface` calls `read_interface` exactly once and derives both the interface and the refusal from that one result:
- listing failed -> `(None, error)` -> "Could not read the network interfaces: {error}. Nothing was changed."
- listing ok, device absent -> `(None, "")` -> "No such interface: {device}"
- present -> `(interface, "")`.
`read_interface` never returns both an interface and an error, so there is no fourth case. `set_mac` still validates the device name and the address before any listing (`:400-403`), so hostile names never reach a subprocess. Every refusal path returns before `_in_flight`/`_remember`/`run_argv_as_root`; tests assert `root["calls"] == []` on each.

### Removed `get_interface`
`grep -rn "get_interface\|_missing_interface"` over the whole tree (including `agents/bug_spray`, `agents/sentry`, `ui/`, `main.py`): no code or test references remain. Only prose remains: `TODO.md:502` (historical, correct), `TODO.md:509-510` (stale, see P3-3) and older `docs/audit/*` reports (historical). `ui/panels/vpn_privacy.py:299` already used `read_interface`; the panel is unchanged since `45d7fe2` and never calls `randomize`/`restore` (it calls `set_mac` directly, `:362`).

### Tunnel rules (CLAUDE.md)
Unchanged by these commits and still held: the original is recorded before root runs (`:429-430`); `random_mac` mints locally administered unicast (`:206`); root argv is a fixed list (`:434-438`); a failed listing fails closed with no root call. No new subprocess, logging or address output.

### Merges
- `8f5aa96` (9b8ab47 + main `c805d31`): brought main's `8092386` guard (`restorable` from `_read_entries`, `macaddr.py:413-417`) and `redact_addresses` docstring intact. It also brought two tests whose helper `_unrecordable` stubbed the now-removed `get_interface`, so `8f5aa96` itself is a red commit (monkeypatch `raising=True`); `28d0f4a` fixes exactly those two lines (`tests/test_macaddr.py:684-685`) and nothing else. Its TODO resolution left a contradiction (P3-3).
- `bb37dfc` (28d0f4a + main `b1274fe`): versus `28d0f4a`, only TODO.md's release-smoke line changed; `macaddr.py`, its callers and the MAC tests are byte-identical to the branch side. TODO.md:130 (MAC changer P1, unticked) is intact.

## Findings

### P3-1 Double full stop in the "could not read" refusal on a listing timeout
- Where: `agents/vpn_agent/services/macaddr.py:174` (carried from `31162a9` into `_find_interface`), with `privileged.run` returning `"Timed out."` (`agents/vpn_agent/services/privileged.py:38`).
- Scenario: `networksetup -listallhardwareports` times out -> `set_mac`/`randomize`/`restore` return `"Could not read the network interfaces: Timed out.. Nothing was changed."` Reproduced with stubbed `run` (no real command): all three calls print the `..`.
- Fix: `error.rstrip('.')` before formatting (or format as `"...: {error} Nothing was changed."` only when it already ends in punctuation); add a case with `"Timed out."` to `test_a_failed_listing_is_reported_as_unreadable_not_missing`.

### P3-2 The "one read" tests pin the seam, not the race, and the once-test overclaims
- Where: `tests/test_macaddr.py:828-837` (`test_a_failing_networksetup_is_reported_through_the_real_listing_path`), `:840-848` (`test_each_call_lists_the_interfaces_once`).
- Evidence (mutants, see table): mutant B (single read for presence, a second `list_interfaces_checked()` for the wording: the exact P3-1 race) and mutant A (the pre-fix code) are killed only because the second listing hits `tests/_isolation.py` `deny_processes` (`ForbiddenInTests`), not by an assertion about wording; the "real listing path" test passes under B because its `run` fails on every call, so both reads agree. The once-test counts `read_interface` calls only and only on the failing path: mutant H (an extra `read_interface` on `set_mac`'s success path) survives, and in the shipped code `restore` and `randomize` each list twice on success (measured with a counting `run` stub: `set_mac` 1, `restore` 2, `randomize` 2), via their own `_find_interface` and then `set_mac`'s. The wording stays consistent with whichever read produced it, so this is not a behaviour bug, but the test name and the commit message ("pin one read per call") say more than is pinned.
- Fix: add a test at the `run` seam where the first `networksetup -listallhardwareports` fails and any later one succeeds, asserting "Could not read" and exactly one listing per `set_mac`; rename the once-test to "...on a failed listing" (or count `networksetup` calls on success too and accept 2 for `restore`/`randomize`). Optionally drop `randomize`'s own read: `random_mac` ignores `like` (`:201-208`), so `randomize` could be `return set_mac(device, random_mac(mode))` with identical messages (mutant C, equivalent, survives), saving a full `networksetup` + `ifconfig` pass per click.

### P3-3 TODO.md MAC changer paragraph contradicts itself after the `8f5aa96` merge; stale `get_interface` names
- Where: `TODO.md:502`, `:509`, `:510`.
- Scenario: line 502 now says, mid-paragraph, "P3-4 open: `refresh_interfaces` still on the UI thread, and `set_mac`/`restore` call a failed listing "No such interface")" (main's side kept by the merge resolution) and then, at the end, "P3-4a fixed (`31162a9` ...)". A reader stops at the first and believes the wording bug is open. Lines 509 ("the in-flight mark starts after `get_interface`") and 510 ("The existing hostile-device test stubs `get_interface`") name a function that no longer exists (it is `_find_interface`/`read_interface` now; the in-flight ordering point itself is still true).
- Fix: in line 502 cut ", and `set_mac`/`restore` call a failed listing "No such interface"" from the earlier P3-4 sentence (or mark it "4a since fixed"); rename `get_interface` in 509/510.

### P3-4 The review of `31162a9` that this commit answers is not in `docs/audit/`
- Where: `TODO.md:502` ("its review found two P3s, both fixed in `9b8ab47`"); no matching file in `docs/audit/` at `bb37dfc`, on any local branch (`git log --all --grep=31162a9` finds only `9b8ab47`), or in the session scratchpad.
- Why it matters: AGENTS.md "Write to files, not chat. A finding that is not in a file did not happen." The fixes cannot be checked against the original finding text.
- Fix: commit that review (and this one) under `docs/audit/` and cite the paths in TODO.md like the earlier MAC reviews.

## Commands run and results

| Command | Result |
|---|---|
| `git show 9b8ab47`, `git show 28d0f4a`, `git diff <parent> <merge>` for both parents of `8f5aa96` and `bb37dfc` (macaddr, vpn_privacy, main.py, MAC tests, TODO.md) | as described above |
| `grep -rn "get_interface\|_missing_interface" .` and `git -C agents/bug_spray grep get_interface` | prose only (TODO.md, docs/audit) |
| Full suite: `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (QT_QPA_PLATFORM=offscreen) at `bb37dfc` | **1 failed, 3160 passed, 14 skipped, 1 deselected in 215 s**; the failure is the known unrelated `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` (ForbiddenInTests on `lms server status`) |
| MAC test set baseline (`test_macaddr.py test_privileged_runner.py test_vpn_companion_libs.py test_vpn_privacy_tab.py`) | 192 passed |
| Stubbed `run` returning `"Timed out."` -> `set_mac`/`randomize`/`restore` | `'Could not read the network interfaces: Timed out.. Nothing was changed.'` x3 |
| Counting stub on success paths | listings: `set_mac` 1, `restore` 2, `randomize` 2 |

### Mutants (applied to `macaddr.py` in the scratch checkout, each restored with `git checkout --`)

| Mutant | Result | Killed by |
|---|---|---|
| A: pre-fix shape (presence via `list_interfaces`, wording via second read) | killed (42 failed) | many read_interface-stub tests, via the process guard |
| B: second `list_interfaces_checked()` for the wording (the P3-1 race) | killed (7 failed) | failed-listing / missing-interface / once tests, all via `ForbiddenInTests`, not a wording assertion; the "real path" test passes |
| C: `randomize` skips its own read | survived | equivalent (`random_mac` ignores `like`) |
| D: `if error` inverted | killed (8) | wording tests |
| E: `restore` ignores the listing error | killed (3) | failed-listing[restore], real-path, once |
| F: `set_mac` reads twice on failure | killed (1) | once-test |
| G: `read_interface` drops its error check | killed (3) | read_interface, real-path, privacy-tab fresh-read |
| H: extra `read_interface` on `set_mac` success path | survived | none (P3-2) |

6/8 killed; 1 equivalent; 1 survivor (H) reported as P3-2. Final `git -C <scratch> status --short`: clean.

## Not verified
- No live MAC, Wi-Fi, root or tunnel action was run; all checks used stubs. Behaviour of real `networksetup` error text other than `Timed out.` was not sampled.
- Bug Spray and Sentry suites (separate) were not run; neither references `macaddr`.

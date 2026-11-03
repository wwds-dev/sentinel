# Independent review: MAC changer `3389384` (review 3 P3-1 to P3-3)

- Date: 2026-11-03. Reviewer: code-reviewer (Opus 5.5), read-only, Tier C.
- Checkout: worktree `scratchpad/p3fix`, branch `fix/macaddr-review3-p3s` at `3389384`, base `dc79b8a`.
- Scope: `3389384` (macaddr.py, tests/test_macaddr.py, TODO.md); findings answered: `docs/audit/macaddr-p32-review-3-2026-11-01.md` P3-1, P3-2, P3-3 (P3-4 not in scope).
- Playbook: `docs/playbooks/code-reviewer.md` `last-reviewed: 2026-10-14`, `status: current` (20 days, not stale).
- `docs/company/app-context.md` is gitignored and absent from the worktree; read from the main checkout.

## Verdict

**Merge-ready.** No P0, P1 or P2. Three P3s: a test-coverage gap on the new `found` path, TODO.md prose (one renamed sentence is now false, and the review-3 P3s are still listed as open), and an order inconsistency in `restore` that the `randomize` change made visible. The `_change` refactor is behaviour-preserving on every path of `set_mac`, `randomize` and `restore`. Every refusal still returns before `_in_flight`, `_remember` and `run_argv_as_root`. The Tunnel rules hold.

Counts: P0 0 · P1 0 · P2 0 · P3 3.

## What was checked

### The `_change` refactor (`agents/vpn_agent/services/macaddr.py:391-495`)
- `set_mac` is now `_change(device, address, prompt, None)` (`:400`). The body is unchanged apart from `:412`: `found if found is not None else _find_interface(device)`. With `None` this is the old `set_mac` line for line.
- `restore` (`:483-495`) does one `_find_interface` and checks hardware, empty current and `spoofed`. It then calls `_change(device, interface.hardware, "", (interface, ""))`. Inside `_change`, `found` skips only the listing. Every later check still runs on the same `interface`: `valid_device` and `is_valid(address)` (`:407-410`), the empty-current check (`:415`), the record gate (`:422-428`), `_remember` (`:436-437`), the Wi-Fi flag (`:443`), `current_mac` after root, `_forget`, and post-change verification (`:466`).
- Is `found` stale? No. No I/O and no lock sits between restore's listing and `_change`, so `found` is fresher than the second listing the old code took. For restore, `address == interface.hardware`, so the gate and `_remember` are skipped. They were skipped in the old code too, because `address != interface.hardware` is false. `current` matters there only through the empty check, which both functions run. Mutant M9 confirms this: rebuilding `found` with `current=hardware` is equivalent.
- `prompt=""` gives the default "Change the hardware address of {device}", the same as the old `set_mac(device, interface.hardware)`.
- `randomize` (`:478-480`) is `set_mac(device, random_mac(mode))`. `random_mac` ignores `mode` and `like` (`:189-208`) and always mints a locally administered unicast address that is never zero (first byte `& 0xFE | 0x02`). The result is the same address distribution as before, with one listing.
- Measured with a stubbed `run` and root (no real command): listings on success are `set_mac` 1, `randomize` 1, `restore` 1 (review 3 measured 1/2/2). A timeout gives `'Could not read the network interfaces: Timed out. Nothing was changed.'`.

### Refusals before record and root
Every early return in `_change` (`:407-428`) comes before `_in_flight.add` (`:429-430`), `_remember` and `run_argv_as_root`. `restore`'s own refusals (`:486-494`) come before `_change`. Tests assert `root["calls"] == []` on the failed-listing, missing-interface, timeout, seam-race, hostile-name and unrecordable-current paths.

### Tunnel rules (CLAUDE.md)
These lines are untouched by the commit: the record is written before root (`:436-437`), `random_mac` mints locally administered unicast only, the root argv is a fixed list with `ROOT_SCRIPT` constant (`:441-445`), and no `shell=True`, `os.system` or `Popen` appears in `macaddr.py`/`privileged.py` (grep). A failed listing fails closed with no root call. `ui/`, `main.py` and `privileged.py` are unchanged (`git diff dc79b8a 3389384 --stat`).

### The `randomize` wording change
`git grep` finds no production caller of `randomize` or `macaddr.restore`. The Privacy tab calls `set_mac` with `macaddr.random_mac()` directly (`ui/panels/vpn_privacy.py:303`, `:362`). `main.py:6007` and `vpn_privacy.py:342` use `restore_all_recorded`, which calls `set_mac`. No UI text, doc or test matches the old randomize wording for a bad name. The change is invisible to users and safe.

### TODO.md
The word-level diff touches exactly three phrases: line 502 drops `, and \`set_mac\`/\`restore\` call a failed listing "No such interface"`; line 509 changes `get_interface` to `_find_interface`; line 510 changes `get_interface` to `read_interface`. The lines stay single bullets/paragraphs, with no checklist or format change (`diff` = 3 changed lines). For the content, see P3-2.

## Findings

### P3-1 The `found` path through `_change` is not pinned by any restore test: four restore-specific mutants survive
- Where: `agents/vpn_agent/services/macaddr.py:403-412` (the new `found` parameter), `:495`; tests `tests/test_macaddr.py:238-261` (the only restore success test is `test_restore_when_changed_sets_the_hardware_address`, `:257`, Ethernet, the address takes).
- Scenario: someone later short-circuits checks on the "caller already listed" branch, which is the natural next edit once `found` exists. For example, `if found is None and applied.lower() != address.lower()` (M10). Root accepts, the adapter ignores the change, and `restore("en5")` returns `(True, "en5 is now <spoofed address>.")`: a false success that leaves the spoofed address in place. Likewise skipping `valid_device`/`is_valid` for `found` (M3) would send a listed but multicast or zero "hardware" address to root. Dropping the Wi-Fi flag for `found` (M11) would run the change without the power cycle. All three survive the MAC test set (197 passed). The shipped code is correct; the tests do not hold it there.
- Fix: parametrize three existing `set_mac` tests over `restore`: the Wi-Fi argv flag (`test_wifi_flag_is_passed_not_scripted`, `:79`), "command accepted but still reports" (root ok, `root["mac"]` left on the spoofed address, expect `not ok` and "still reports"), and an unusable listed hardware address (e.g. `hardware="01:00:5e:00:00:01"`, `current=NEW`, expect `not ok` and `root["calls"] == []`). Optional simplification: pass `interface: Interface | None` instead of the `(interface, refusal)` tuple. Today `_change(..., (None, ""))` would return `(False, "")`, an empty refusal. No caller does this, but the tuple shape allows it.

### P3-2 TODO.md: the renamed sentence on line 510 is now false, and line 502 still lists the fixed P3s as open
- Where: `TODO.md:510`, `TODO.md:502` (end of paragraph).
- Evidence: line 510 now reads "The existing hostile-device test stubs `read_interface`, so it never exercises quoting or name validation." The hostile-device test (`tests/test_macaddr.py:47-56`) stubs `run` and `run_argv_as_root`, not `read_interface`, and it asserts `"interface name" in msg` with nothing run. So it does exercise name validation. The rename turned a historical note (the 2026-10-21 audit's test-gap list) into a present-tense claim that is wrong. Line 502 still ends "Its four P3s are open: P3-1 ... P3-2 ... P3-3 ...", but this commit fixes P3-1 to P3-3. That is acceptable if the status line is updated after this review, but a reader of the branch sees them as open.
- Fix: on line 510, either restore the original name and mark it historical ("stubbed `get_interface` at the time; since `9b8ab47` the test stubs `run` and asserts nothing runs"), or drop the clause. On line 502, mark review-3 P3-1 to P3-3 fixed in `3389384` (with this report's path) once it is filed, and leave P3-4 open.

### P3-3 `restore` lists before it validates the device name, unlike `set_mac` and now `randomize`
- Where: `agents/vpn_agent/services/macaddr.py:483-486` against `:407-408`.
- Scenario: `restore("x;y")` runs a full `networksetup -listallhardwareports` plus an `ifconfig` per port, then returns `"No such interface: x;y"`. `randomize("x;y")` (after this commit) and `set_mac("x;y", ...)` return `"'x;y' is not a valid interface name."` and run nothing (measured: listings 1 against 0, root 0 for both). There is no security impact: the name is only compared against listed names and never reaches a subprocess. The behaviour is the same as before this commit for restore, but the three entry points now disagree, and `_change`'s own `valid_device` check runs only after restore has already listed.
- Fix: add `if not valid_device(device): return False, f"{device!r} is not a valid interface name."` at the top of `restore`, and add `restore` and `randomize` to `test_hostile_or_malformed_device_is_refused_before_anything_runs` (`tests/test_macaddr.py:47-56`). That test also pins the new randomize wording, which nothing tests today.

## Commands run and results

| Command | Result |
|---|---|
| `git show 3389384`; `git diff --word-diff=porcelain dc79b8a 3389384 -- TODO.md`; `git diff --stat dc79b8a 3389384 -- ui main.py agents/vpn_agent/services/privileged.py` | as described; TODO 3 phrases; ui/main/privileged unchanged |
| `git grep` for callers of `randomize(` / `macaddr.restore(` / old wording over the repo (excluding docs/audit) | no production caller; UI uses `set_mac` + `random_mac` |
| `grep shell=True\|os.system\|Popen` in macaddr.py, privileged.py | none |
| Stubbed-run probe (own process; `run`, `run_argv_as_root`, `record_path`, `boot_id` replaced; no real command) | listings on success 1/1/1; `randomize("x;y")` 0 listings, `restore("x;y")` 1 listing; timeout wording has a single full stop |
| Full suite: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` in the worktree | **1 failed, 3165 passed, 14 skipped, 1 deselected in 214.81 s**; the failure is the known unrelated `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` (ForbiddenInTests on `lms server status`). Matches the commit's `verified-by`. |
| MAC test set (`test_macaddr.py test_privileged_runner.py test_vpn_companion_libs.py test_vpn_privacy_tab.py`) | 197 passed |

### Mutants (planted in the worktree's `macaddr.py` one at a time by `scratchpad/mut_p3s.py`, each restored with `git -C <worktree> checkout --`, MAC test set run each time)

| Mutant | Result | Killed by / note |
|---|---|---|
| M1 `restore` passes `None` (lists twice) | killed | `test_a_successful_call_lists_the_interfaces_once` |
| M2 `randomize` back to its own read | killed | same |
| M3 `_change` skips `valid_device`/`is_valid` when `found` is given | **survived** | P3-1 (restore with an unusable listed hardware address is untested) |
| M4 `rstrip('.')` dropped | killed | `test_a_listing_timeout_is_worded_without_a_double_full_stop` |
| M5 wording from a second listing (review-3 mutant B, the race) | killed | `test_the_wording_comes_from_the_one_listing_that_decided[0-2]` by assertion (run stubbed, so not via ForbiddenInTests), plus 7 seam tests. Closes review 3 P3-2. |
| M6 `_change` skips the empty-current check when `found` is given | survived | equivalent: `restore` checks it first (`:490`) |
| M7 `restore` drops its own empty-current check | killed | `test_restore_fails_when_the_current_address_cannot_be_read` |
| M8 `_change` skips `_remember` when `found` is given | survived | equivalent: restore's address is the hardware address, so it never records |
| M9 `restore` passes `found` with `current=hardware` (stale `current`) | survived | equivalent: shows `current` staleness cannot change restore's outcome |
| M10 `_change` skips post-change verification when `found` is given | **survived** | P3-1 (false success on restore) |
| M11 `_change` drops the Wi-Fi flag when `found` is given | **survived** | P3-1 (no restore Wi-Fi test) |

6/11 killed, 3 equivalent (M6, M8, M9), 3 real survivors (M3, M10, M11), all on hypothetical `found`-gated edits and reported as P3-1. Final `git -C <worktree> status --short`: clean.

## Not verified
- No live MAC, Wi-Fi, root or tunnel action was run; every check used stubs.
- Bug Spray and Sentry suites were not run; neither references `macaddr`.
- The commit's "mutants killed 5/5" could not be checked: the mutants are not named in the commit.

# MAC changer re-audit 3: the P2-A fix (`d3718cc`)

- **Date:** 2026-10-28
- **Reviewer:** Claude Opus 5.5, independent session. I did not write `d3718cc` or re-audit 2.
- **Branch / worktree:** `fix/macaddr-audit` at `ae2f332`, `.claude/worktrees/macaddr-audit`
- **Mode:** read-only. No source edited, nothing committed. Mutants and probes ran on a scratch
  copy outside the repo. No MAC, Wi-Fi or root action was run.
- **Scope:** `BOOT_UUID_RE` and `boot_id()` (`agents/vpn_agent/services/macaddr.py:228-241`),
  how they meet `_read_entries`' boot filter (`:263-269`) and `_remember` (`:309`), the upgrade
  effect on records written by `164f1db`, and the tests `test_boot_id_is_the_boot_session_uuid`
  (`tests/test_macaddr.py:482-500`) and `test_boot_id_is_unknown_when_sysctl_fails` (`:503-510`).
- **Out of scope (unchanged):** re-audit 2 P3-1..P3-6, the network-change half of P2-3,
  Mediums 5-6, Lows 7-12.

## Verdict

**Ready to merge as the Tier C close-out for P2-A.** The fix is correct. `kern.bootsessionuuid`
is generated once per boot and is documented to stay the same across sleep, wake and hibernate,
which is what P2-A needs. The parsing fails toward "unknown boot, keep entries", the rule the
record already relies on. The upgrade effect is real but reaches nobody today. No P0, P1 or P2
findings. There is one P3 test gap: the tests never use a boot value that contains letters, so
nothing pins down that the record keeps the boot ID's case (P3-A). It does not block the merge,
and it is a one-line test. One P3 note on the upgrade path does not need a change (P3-B).

## What was verified

| Claim | Evidence |
|---|---|
| The value is created once per boot, in upper case | XNU (`apple-oss-distributions/xnu`, `main`), `iokit/Kernel/IOPMrootDomain.cpp:4165-4175`: `initializeBootSessionUUID()` calls `uuid_generate` then `uuid_unparse_upper` into `bootsessionuuid_string`. Its only caller is `IOPMrootDomain::start` (`:1790`, inside `start` at `:1620`), which runs once per kernel boot. |
| Nothing rewrites it within a boot | `bsd/kern/kern_sysctl.c:2921-2923`: `SYSCTL_STRING(_kern, OID_AUTO, bootsessionuuid, CTLFLAG_RD \| CTLFLAG_LOCKED, …)`, read-only. Every other mention in the tree (`gh search code bootsessionuuid_string --repo apple-oss-distributions/xnu`: `kern_exec.c:6521`, `osfmk/arm/model_dep.c:546-547,732-734`) only reads it (a SHA input and the panic log). |
| It survives sleep, wake and hibernate | `iokit/IOKit/pwr_mgt/IOPM.h:236-243`, `kIOPMBootSessionUUIDKey`: "remains valid till shutdown … will remain same across sleep/wake/hibernate cycle". Hibernate restores kernel memory, so an `ifconfig ether` change survives it too, and keeping the record across it is right. |
| The source matches this Mac's format | Read-only probe, value not printed: `sysctl -n kern.bootsessionuuid` → `rc 0 stderr_empty True raw_matches_upper_regex True trailing_newline_only True two_reads_equal True`. `sysctl -d` gives no description. |
| Not verified | That the value changes across a real reboot on macOS 27 (no reboot was done), and that the shipped macOS 27 kernel matches the published `main` source. The source generates a fresh UUID at every `IOPMrootDomain::start`, so a change on reboot follows from it. |
| Parsing fails safe | `:239-241`: a failed `run`, an empty string, `kern.boottime` output, or anything that is not a complete UUID gives `""`. `""` keeps every entry (`:269`), the existing "unknown boot is not proof of a reboot" rule. `privileged.run` combines stdout and stderr (`privileged.py:36`), so a warning on stderr would also give `""`. That is the safe direction. |
| Normalisation is consistent | `boot_id()` upper-cases the value. `_remember` writes `boot_id()` unchanged (`:309`). `_read_entries` keeps `boot` as written, while it lowercases `original` and `hardware` (`:263`). Write and compare therefore use the same case. |
| `strip()` is redundant, as the author said | `privileged.py:36` already does `.strip()`. The author's surviving "no strip" mutant is equivalent. |
| Upgrade from `164f1db` records | Probe (scratch copy): a record with `"boot": "1792350752"` under a UUID boot → `recorded: {} pending: []`, and the file is pruned. See P3-B. |
| Nobody has such a record | `/Applications/Sentinel.app/Contents/Resources/project_root.txt` names the main checkout. That checkout's `macaddr.py` (both `main` and the checked-out `docs/todo-and-training-updates`) has 0 matches for `boot_id\|mac_original`. `git branch -a --contains 164f1db` lists only `fix/macaddr-audit`. `find ~/Library/Application Support/Sentinel* -maxdepth 3 -name mac_original.json` found 0 files. |

## Findings

### P3-A: no test uses a boot ID with letters, so the case of the stored boot is unpinned

`macaddr.py:263` stores `boot` as written and lowercases only `original` and `hardware`.
`boot_id()` returns upper case (`:240`). Every record test uses `BOOT = "1000"`
(`tests/test_macaddr.py:21`, `:343`), and `"1000".lower() == "1000"`. A refactor that
lowercases every key the same way (`entry[key].lower()`), or one that lowercases inside
`boot_id`, would compare `"1c9e…"` with `"1C9E…"`. The record would then be dropped as
"earlier boot" on **every** relaunch. That is P2-A's failure, made certain instead of rare:
after a crash, no offer, no restore at quit, and the random address stays until reboot.

- **Scenario:** a future cleanup changes `:263` to `{key: entry[key].lower() for key in …}`.
  The suite stays green. Randomise en5, Sentinel crashes, relaunch: the record is gone.
- **Evidence:** mutant M1 (`entry[key].lower() if key != "boot" else entry[key]` →
  `entry[key].lower()`) **survived** the three MAC test files: `109 passed`. A probe with
  `boot_id` returning a UUID and a record written with the same UUID gives
  `PROBE recorded: {'en5': 'aa:bb:cc:00:00:01'}` unmutated and `PROBE recorded: {}` under M1.
- **Fix direction:** set `BOOT` in `tests/test_macaddr.py` to an upper-case UUID, or add one
  round-trip test (`_remember` then `recorded()` with `boot_id` returning a UUID). Either one
  kills M1.

### P3-B (note, no change needed): a `164f1db` record is dropped after upgrading within the same boot

`macaddr.py:269`. A record written by `164f1db` holds `kern.boottime` seconds as its boot. Under
`d3718cc` the boot is a UUID, so the two never match and the entry is dropped and pruned. If a
user ran `164f1db`, randomised an address, crashed, and then ran `d3718cc` in the same boot,
Sentinel would forget the change. The address returns at the next reboot. This is the drop
direction, not a wrong restore, so no root action can be steered.

- **Evidence:** probe `test_probe_old_boottime_record_after_upgrade`:
  `recorded: {} pending: []`. Exposure: the table above shows that no installed or checked-out
  copy has ever run record code, and no record file exists on this Mac.
- **Why no change:** the author's upgrade note (re-audit 2, "Follow-up") is accurate. A
  migration that accepted all-digit boot values would bring back the P2-A drift for exactly
  those records. Recommend only that the commit or CHANGELOG states that pre-merge branch
  records are discarded.

## Do the tests prove the claim?

Partly, as unit tests can. `test_boot_id_is_the_boot_session_uuid` pins the sysctl name (the
argv assertion kills the `kern.bootuuid` mutant), upper-casing, refusal of `kern.boottime`
output and garbage, and the end anchor. `test_boot_id_is_unknown_when_sysctl_fails` pins the
`ok` check. The claim "constant within a boot, new on reboot" is a property of the kernel and
cannot be proved by a mocked unit test. It rests on the XNU source cited above. The "same boot,
`sec` off by one" test that re-audit 2 suggested no longer applies, because no time value is
used.

### Mutants (scratch copy, `-x`, `test_macaddr.py` + `test_vpn_companion_libs.py` + `test_vpn_privacy_tab.py`, baseline `109 passed`)

| # | Mutant | Result |
|---|---|---|
| M1 | boot key lowercased with the others (`:263`) | **survived**: P3-A |
| M2 | `\Z` → `$` | survived, equivalent (input is already stripped) |
| M3 | end anchor removed | killed (`1 failed`) |
| M4 | class `[0-9A-Fa-f]` in the last group | survived, equivalent (input is upper-cased) |
| M5 | last group `{1,12}` (accepts a truncated UUID) | survived. The kernel always writes 36 characters, so the risk is negligible and not raised |
| M6 | `match` → `search` | survived, equivalent (pattern starts with `^`) |
| M7 | unknown boot drops entries | killed |
| M8 | entry with empty boot dropped under a known boot | survived, already reported as re-audit 2 P3-6 (the P3-4 branch) |
| M9 | `kern.bootsessionuuid` → `kern.bootuuid` | killed |
| M10 | no `upper()` | killed |
| M11 | `functools.cache` removed | killed |

## Suite

`/Users/as/Documents/lab/active/sentinel/.venv/bin/python -m pytest -q -p no:cacheprovider`
from the worktree: `1 failed, 3079 passed, 14 skipped, 1 deselected in 220.45s`. The one failure
is `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`, the
LM Studio CLI test that re-audit 2 already recorded as unrelated. The count matches the author's
run.

## Commands run (all read-only)

- `git show d3718cc`, `git branch -a --contains 164f1db`, `git worktree list`
- XNU source: `gh search code bootsessionuuid[_string] --repo apple-oss-distributions/xnu`, and
  `curl` of the files cited above into the session scratchpad
- `sysctl -n kern.bootsessionuuid` (only shape and equality booleans printed), `sysctl -d kern.bootsessionuuid`
- `find ~/Library/Application Support/Sentinel* -maxdepth 3 -name mac_original.json` (count only)
- Mutants and probes: `rsync` copy of the worktree to the scratchpad, mutants applied there with
  single-occurrence string replacement, and the three MAC test files run against each one

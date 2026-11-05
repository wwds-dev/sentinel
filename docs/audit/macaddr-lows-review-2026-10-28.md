# MAC changer review: Medium 5 and Lows 7-12 (`c807267`, `89ecdd2`)

- **Date:** 2026-10-28
- **Reviewer:** Claude Opus 5.5, independent session (`code-reviewer` + `security-engineer`).
  I did not write `c807267` or `89ecdd2`.
- **Branch / worktree:** `claude/macaddr-lows-a0e2c9` at `89ecdd2`,
  `.claude/worktrees/macaddr-lows-a0e2c9`. Diff reviewed: `git diff 7c84dca..89ecdd2`.
- **Mode:** read-only. No source edited, nothing committed. Mutants ran on a scratch copy outside
  the repo. No MAC, Wi-Fi or root action was run; the one probe of the quit-time restore stubbed
  every subprocess and pointed the record at a scratch file.
- **Tier:** C (Local-model plan in `TODO.md`). This is the independent review it needs.

## Verdict

**Ready to merge, with one documentation fix first (P2-1, a one-word revert in `TODO.md`).**
Medium 5 and Lows 7, 8, 9 and 11 are fixed and each fix is pinned by a test that fails when it is
reverted (14 of 14 behavioural mutants killed). Nothing from `7c84dca` regressed. No P0 or P1.

Two things should not be recorded as done as written:

- **P2-1:** `89ecdd2` corrupted an unrelated `TODO.md` line: `DEHASHED_API_KEY` became
  `DEc807267ED_API_KEY`. That is the env var name a user would copy. Fix before merge.
- **P2-2 (Low 12 only partly closed):** the gate-target change is real but changes no behaviour,
  because `GatedTab` never stores `target`. The one place a MAC address is still written to a file
  is elsewhere and predates this commit: a failed quit-time restore prints the interface's current
  address to stderr, which the installed app sends to `/tmp/sentinel-launch.log` (mode 0644).
  This does not block the merge, but Low 12 should stay open until it is fixed.

Low 10 (no code change) is acceptable in substance, but the new comment overstates the consent
story (P3-1).

## Findings by item

### Medium 5: same-vendor mode removed. **Fixed.**

- **Code:** the branch that copied the first three octets of `like` is gone. `random_mac` always
  returns `(randbelow(256) & 0xFE) | 0x02` followed by five random octets
  (`agents/vpn_agent/services/macaddr.py:181-184`), so the result is always locally administered
  and unicast. `MODES = (MODE_LOCAL,)` (`:75-76`); `MODE_SAME_VENDOR` no longer exists.
- **UI:** `mac_mode_box` is gone (`ui/panels/vpn_privacy.py:101-108`). `randomise_mac` calls
  `macaddr.random_mac()` with no mode (`:278`).
- **Saved setting:** there never was one. At `7c84dca` the combo box held its value in memory only.
  No `QSettings`, `save_setting` or JSON key mentioned it (`git show 7c84dca:ui/panels/vpn_privacy.py`
  has no persistence near `mac_mode_box`). The only remaining way to pass the old value is the
  API (`random_mac(mode, like)` / `randomize(device, mode)`), and both ignore it
  (`macaddr.py:169-184`, `:429-433`).
- **Other references:** a repo-wide search for `same.vendor`, `MODE_SAME`, `mac_mode` and `OUI`
  outside `docs/audit/` and the tests finds only the comment at `macaddr.py:73-74` and the docstring.
- **Tests:** `test_every_generated_address_is_locally_administered_unicast` (300 draws × 4 modes,
  including `"same-vendor"` with a real Apple OUI as `like`), `test_same_vendor_mode_is_gone`,
  `test_randomize_with_a_saved_same_vendor_mode_uses_the_random_mode` (checks the address that
  reaches root), `test_there_is_no_mac_mode_choice`,
  `test_randomise_offers_a_locally_administered_unicast_address`. Mutants M5-a..d all killed.
- **Scope note (not a defect):** `set_mac` still accepts any unicast, non-zero address
  (`macaddr.py:370`). That is correct. Restore has to set the globally administered hardware address
  and the recorded original. Only *generated* addresses must be locally administered, and they are.

### Low 7: the confirm dialog reads the interface again. **Fixed.**

`_fresh_interface()` (`vpn_privacy.py:250-268`) calls `macaddr.get_interface(device)` on the click.
It swaps the fresh entry into `_interfaces` and redraws the labels. If the adapter has gone, it
refreshes the list and returns `None`, and the caller refuses with "no longer available". Both
`randomise_mac` (`:270-279`) and `restore_mac` (`:281-300`) build the dialog from the fresh entry,
and `restore_mac` also decides "already on hardware" from it. `set_mac` re-reads the interface
again in the worker (`macaddr.py:373`), so the record still holds the true original even if the
user waits in the dialog. Tests: `test_the_confirm_dialog_shows_the_address_read_just_before`,
`test_restore_decides_on_the_address_read_just_before` and `test_an_interface_that_vanished_is_a_refusal`.
Mutants L7-a, L7-b and L7-c killed.

The click now runs `networksetup -listallhardwareports` and one `ifconfig` per port on the UI
thread (P3-3).

### Low 8: `is_valid`. **Fixed.**

`is_valid` (`macaddr.py:191-197`) rejects the multicast bit, which also covers broadcast
`ff:ff:ff:ff:ff:ff`, and now rejects the all-zero address. Tests:
`test_unusable_addresses_are_rejected` (zero, broadcast, IPv4 and IPv6 multicast, other odd first
octets, malformed) and `test_ordinary_unicast_addresses_are_accepted`;
`test_a_zero_address_never_reaches_root` shows that nothing runs. Mutants L8-a and L8-b killed.
The stricter check also drops a zero `original` from the record (`_valid_entry`, `:238-242`). That is
the right direction.

`MAC_RE` still ends in `$`, not `\Z`, so `"02:11:22:33:44:55\n"` passes `is_valid` (P3-2,
predates this commit).

### Low 9: `refresh_interfaces` catches `OSError` only. **Fixed; it cannot crash the tab.**

- Everything `list_interfaces` runs goes through `privileged.run` (`privileged.py:27-41`). That
  function already turns `TimeoutExpired` and `OSError` into `(False, text)`. The parsing that
  follows uses only `str.split(..., 1)[1]` on lines that contain the separator, and
  `split()[1]` on a line that starts with `"ether "`, so it cannot index out of range.
  `except OSError` at `vpn_privacy.py:218` therefore guards nothing that can happen today. It is
  harmless and documents intent.
- What could still escape is a `UnicodeDecodeError` from `text=True`, if a command printed bytes
  that are not valid in the locale's encoding. That is a `ValueError`, not an `OSError`. For
  `networksetup`/`ifconfig` output that is theoretical. Even then it would not crash the tab: there
  is no `sys.excepthook` in the repo (searched), and PySide6 prints an exception raised in a slot
  or in `showEvent` and carries on. The test `test_refresh_survives_an_os_error_but_not_a_bug`
  pins the intended behaviour. Mutant L9 killed.

### Low 10: no dialog when already root. **Acceptable; comment overstates (P3-1).**

When the process is root, the macOS dialog cannot add consent: the process can already do anything
the dialog would authorise. Skipping it is the only behaviour that makes sense
(`privileged.py:112-116`, `:141-143`). That is consistent with the `CLAUDE.md` rule ("root-level
actions stay in a small separate path with the macOS confirmation dialog"), whose aim is that an
unprivileged app never escalates silently. A root app is outside that model.

The comment claims that "Tunnel's tabs ask before any privileged call". That is not true for every
caller. The quit-time restore (`main.py:5997-6011`, called from `_leave`) has no in-app
confirmation. Its only consent step is the administrator dialog. As root it would therefore run
with no prompt at all. It only puts back the original address, which is the direction the Tunnel
rule asks for, so the behaviour is fine. The comment is not (P3-1).

### Low 11: unused import. **Fixed.**

`import subprocess` is gone from `macaddr.py`. An AST scan finds no other unused import in
`macaddr.py` or `privileged.py`. No test can pin this (the planted mutant survives: 137 passed),
and `ruff` is not installed, so it rests on review. `vpn_privacy.py` still imports `Qt` and
`vpn_execution` without using them (P3-4, predates this commit).

### Low 12: no MAC address kept or logged. **Partly closed (P2-2).**

- **Gate target:** now `iface.device` only (`vpn_privacy.py:338-341`); the leftover-restore offer
  passes device names only (`:327`). But `GatedTab._refuse` and `_gated` never read `target`
  (`ui/panels/vpn_gate.py:35-66`), so this changes no behaviour. The old target with addresses was
  not stored anywhere either. The test `test_the_gate_target_for_a_mac_change_holds_no_address`
  pins the string, not the absence of storage. It is still a sensible guard for the day the gate
  starts recording.
- **Action log / audit trail:** none. `tunnel_audit.jsonl` and its writers were removed on
  2026-10-11; `tests/test_tunnel_keeps_no_logs.py` keeps it gone.
- **State files:** `mac_original.json` holds `{original, hardware, boot}` per device (0600,
  `macaddr.py:281-295`). That record is required by the Tunnel rule ("record the original address,
  restore it on exit and on crash"). It is state, not a log, and it is deleted once the address is
  back.
- **The leak (P2-2):** see below.
- `test_a_mac_change_writes_no_log_file` does not wait for the worker and stubs `set_mac`, so it
  cannot observe a file being written (P3-5).

## P0 / P1

None.

## P2

**P2-1 · `TODO.md:92` · `89ecdd2` corrupted the DeHashed env var name.**
Evidence: `git diff 7c84dca..89ecdd2 -- TODO.md` changes `DEHASHED_API_KEY` to
`DEc807267ED_API_KEY` in the v2.002 OSINT line. `git log -S DEc807267ED -- TODO.md` points to
`89ecdd2`. It looks like a `HASH` → commit-SHA substitution that matched inside the word. No code
reads `TODO.md`, but the line documents a key the user has to set, and the Lab Project Monitor
shows it. Revert that one token.

**P2-2 · `main.py:6009` · a failed quit-time restore writes a MAC address to a world-readable
file.** Predates this commit (`1aff0eb`), but it is the open half of Low 12.
`_restore_changed_hardware_addresses` prints `message` from `set_mac` to stderr. On failure that
message ends with "`<dev>` reports `<current address>`" (`macaddr.py:412-415`). Probe with every
subprocess stubbed:

```
hardware address of en5 not restored: ifconfig: ioctl (SIOCAIFADDR): Invalid argument en5 reports 02:aa:bb:cc:dd:ee.
```

Where stderr ends up: the installed app's launcher redirects it to `LOG_PATH`
(`scripts/app_launcher.c:17`, `:49`, opened `0644`). `scripts/install_app.sh:66` sets that to
`/tmp/sentinel-launch.log`. A Lab Hub launch sends it to `$TMPDIR/lab-hub-launch-sentinel.log`.
The address in the line is the one Sentinel set, which is exactly what the change was meant to
hide. The Tunnel rule allows local diagnostics only if redacted. Fix: print the device and a
reason without the address. For example, strip `MAC_RE` matches before printing, or have `set_mac`
return the "reports …" detail separately. Add a test that runs `_restore_changed_hardware_addresses`
with a failing stub and asserts that stderr contains no address.

## P3

- **P3-1 · `agents/vpn_agent/services/privileged.py:113-115`.** The comment says the consent gate
  is "the caller's own default-No confirmation (Tunnel's tabs ask before any privileged call)". The
  quit-time MAC restore (`main.py:5997-6011`) and the recorded-restore path have no in-app
  confirmation besides the dialog. Reword it, for example: "As root there is nothing for the
  dialog to authorise; callers that need consent must ask for it themselves. The quit-time MAC
  restore runs without a prompt as root, which is the safe direction." Optional and outside this
  item: warn at startup when Sentinel runs as root, since least privilege is better served by not
  running the GUI as root at all.
- **P3-2 · `macaddr.py:41`.** `MAC_RE` ends in `$`, so a trailing `\n` passes `is_valid` and
  `_valid_entry`. It is not exploitable: the address goes to root as a separate argv word, and
  `set_mac` would report "still reports …". But Low 8 was about `is_valid` edge cases, and
  `DEVICE_RE` already uses `\Z` for exactly this reason. Predates this commit.
- **P3-3 · `vpn_privacy.py:259-260`.** `_fresh_interface` runs `list_interfaces()` on the UI thread
  at every Randomise/Restore click: one `networksetup` plus one `ifconfig` per port, each with a
  30 s timeout. `refresh_interfaces` already does the same on show and after every action, so this
  adds no new kind of risk. It does mean a hung `networksetup` now freezes the window on a click,
  where before it froze it only on refresh.
- **P3-4 · `ui/panels/vpn_privacy.py:11, 20`.** `Qt` and `vpn_execution` are imported and unused.
  Both were already unused at `7c84dca`. Same class as Low 11.
- **P3-5 · `tests/test_vpn_privacy_tab.py:370-375`.**
  `test_a_mac_change_writes_no_log_file` stubs `set_mac` and does not wait for the `CallWorker`,
  so it would pass even if a writer were added. `test_the_gate_target_for_a_mac_change_holds_no_address`
  replaces `_gated`, so `caplog` can never see anything. Neither is wrong, but they do not show "no
  log anywhere". The test that would is the P2-2 one.
- **P3-6 · `tests/manual_test_cases.md:289`.** It still says "`tunnel_audit.jsonl` has `mac-set`
  lines for each". That file was removed on 2026-10-11, and the step now contradicts Low 12. It
  should say that no file under `data/logs/` or `vpn/` mentions the addresses.
- **P3-7 · `CHANGELOG.md`.** There is no entry for the removal of the mode choice, which users can
  see, nor for any of the MAC changer fixes merged in `7c84dca`. The definition of done in
  `AGENTS.md` requires one.
- **P3-8 · `TODO.md:130`.** The "Verified" tail still quotes the 3079-test run from `d3718cc`. The
  author's 3107 run for `c807267` and this review's run are not recorded there.
- **P3-9 · test isolation, predates this commit.** Running `tests/test_macaddr.py` directly before
  `tests/test_tunnel_keeps_no_logs.py` makes three tests in the latter error with
  `ForbiddenInTests: ['sysctl', '-n', 'kern.bootsessionuuid']`. It reproduces identically at
  `7c84dca`. The `test_boot_id_*` tests call `REAL_BOOT_ID.cache_clear()` (`tests/test_macaddr.py:495-509`),
  so a later real `boot_id()` runs `sysctl`. The full suite's alphabetical order does not hit it
  (see below), but a `-k` or file-subset run can. Fix in `conftest.py` by stubbing `boot_id`
  session-wide, or by restoring the cache in those tests.

## Regression check against `7c84dca`

`git diff 7c84dca..89ecdd2 -- agents/vpn_agent/services/macaddr.py` touches only the import, the
mode constants, `random_mac` and `is_valid`. The following are byte-identical and their tests still
fail when broken:

| Invariant | Where | Mutant | Result |
|---|---|---|---|
| Original recorded before root runs | `macaddr.py:387-388` | `_remember` skipped | killed (`test_a_change_records_the_original_before_root_runs`) |
| `ROOT_SCRIPT` constant, EXIT trap powers Wi-Fi on | `macaddr.py:52-71` | `trap power_on EXIT` removed | killed (`test_script_success_order`) |
| Device-name validation with `\Z` | `macaddr.py:44` | `\Z` → `$` | killed (`test_hostile_or_malformed_device_is_refused_before_anything_runs[en0\n]`) |
| argv to root, no shell string | `macaddr.py:392-397`, `privileged.py:130-153` | n/a (unchanged; existing tests pass) | — |

## Mutation testing

Run on a scratch copy of the worktree, one mutant at a time, against `tests/test_macaddr.py`,
`tests/test_vpn_privacy_tab.py` and `tests/test_vpn_companion_libs.py` (the kill for each is named
by the first failing test). Script: `mutants.py` in the session scratchpad.

| Mutant | Result |
|---|---|
| M5-a `random_mac` drops the locally administered bit | killed |
| M5-b `random_mac` drops the unicast mask | killed |
| M5-c same-vendor branch put back | killed |
| M5-d `MODES` regains a second mode | killed |
| L8-a zero check removed | killed |
| L8-b multicast check removed | killed |
| L7-a Randomise uses the listed interface | killed |
| L7-b `_fresh_interface` returns the stale entry | killed |
| L7-c Restore uses the listed interface | killed |
| L9 `except Exception` put back | killed |
| L12 addresses put back in the gate target | killed |
| L11 `import subprocess` put back | survives (lint-only; expected) |
| REG ×3 (above) | killed |

**14/14 behavioural mutants killed**; the one survivor is the unused import, which no test can see.

## Full suite

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` in this worktree at `89ecdd2` (after
`git submodule update --init` for `agents/bug_spray`):

- with `tests/test_providers_registry.py` deselected: **3034 passed, 14 skipped, 75 deselected**
  (217 s);
- unfiltered: **1 failed, 3107 passed, 14 skipped, 1 deselected** (221 s). The one failure is
  the known, unrelated `test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`
  (`ForbiddenInTests`: it starts the installed LM Studio CLI). That matches the author's 3107.

`ruff`: not run (not installed).

## What to do before merge

1. Revert `DEc807267ED_API_KEY` → `DEHASHED_API_KEY` in `TODO.md:92` (P2-1).
2. In `TODO.md`, keep Low 12 open (or move it to a new item) pointing at P2-2, rather than marking it
   fixed.

Both are documentation edits; neither needs another Tier C review. P2-2 itself and the P3s can
follow as separate `@ai` items.

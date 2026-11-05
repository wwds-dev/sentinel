# Independent review of bb4d35d (fixes for review 2 of 45d7fe2, MAC changer P3s)

Reviewer: Opus 5.5, read-only (`code-reviewer` + `security-engineer`), 2026-11-03.
Scope: `bb4d35d` on `claude/macaddr-p3-followups` (parent `b1274fe` = `origin/main`; the branch is one commit ahead and already contains `origin/main`). Tier C review: the reviewer did not write this commit.

Playbooks: `docs/playbooks/code-reviewer.md` is `last-reviewed: 2026-10-14`, `status: current`. `docs/playbooks/security-engineer.md` is **`status: baseline`** (`last-reviewed: 2026-10-10`), so it is **stale under AGENTS.md's rule**. `docs/company/app-context.md` is not tracked in this worktree (`c4ac6a3` keeps it local); the main checkout's copy was read, read-only.

**Verdict: ready to merge.** No P0, P1 or P2 findings. Each fix closes its finding, and each new or changed test fails without its fix (R1 to R3). There are four P3s. The first is a small UX side effect of the P3-1 fix: an approved action now clears the selected hop in the chain table. The other three are a test gap, older gaps in the same function, and wording in TODO.md.

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Targeted suites | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_vpn_privacy_tab.py tests/test_macaddr.py tests/test_quit_privileged.py` | 170 passed |
| Full suite | `.venv/bin/python -m pytest -p no:cacheprovider -q` (HEAD `bb4d35d`) | 3154 passed, 14 skipped, 1 deselected, 1 failed (`test_providers_registry::test_server_off_falls_back_to_the_disk_listing`, `ForbiddenInTests` on `lms server status`; known and unrelated). Matches the commit message. |
| Every gate-related test file | the three files above plus `test_tunnel_keeps_no_logs.py`, `test_vpn_app_control.py`, `test_vpn_review_fixes.py`, `test_vpn_servers_tab.py` | 282 passed, 2 skipped, 3 errors in `test_tunnel_keeps_no_logs.py` (`Popen` refused at setup). The same 3 errors appear with and without every mutant. That file passes on its own (29 passed), in pairs, and in the full suite. Ordering-dependent; the cause was not investigated, and none of its code is touched by this commit. Mutant verdicts below compare against these 3 errors as the baseline. |
| Hand mutants | 7 planted one at a time with a scratch script (each replacement asserted to match exactly once), then `git checkout -- <file>` | The brief's three are killed. Of the reviewer's four, one is killed only by the Servers-tab suite and three survive every gate-related test file. |
| Reviewer probes | 6 temporary tests using the real `env` fixture, deleted afterwards; also run against the parent's `vpn_privacy.py` | see "Side effects of the P3-1 fix" |
| Tree clean at end | `git status --short`; `git diff --stat` | both empty |

No live MAC, Wi-Fi, root or tunnel action was run. Every probe and mutant used the mocked `env` fixture (`SyncWorker` / `DeferredWorker`, faked `macaddr` and `tor`).

## Each fix against its finding

| Finding (review 2) | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P3-1: Test chain turned on after an approved action with an empty chain | `PrivacyTab._after_finish` calls `self._refresh_chain()` (`ui/panels/vpn_privacy.py:211`) | Yes | R1 is killed by `test_test_chain_stays_off_after_an_approved_action_with_an_empty_chain` (`tests/test_vpn_privacy_tab.py:553`) |
| P3-2: the read's worker-error branch was not pinned to recompute the buttons | asserts that `tor_stop_btn` and `chain_test_btn` are off and `mac_random_btn` is on (`tests/test_vpn_privacy_tab.py:488-490`) | Yes | R2 is killed by `test_a_worker_failure_during_the_read_frees_the_tab` |
| P3-3: nothing pinned redaction before truncation | `test_a_listing_error_is_redacted_before_it_is_cut` (`tests/test_macaddr.py:798-804`) | Yes | R3 (`redact_addresses(...[:200])`) is killed |

### Does the P3-3 fixture really discriminate?
Fixture: `"x"*189 + " 02:aa:bb:cc:dd:ee " + "y"*50`. `.strip()` changes nothing. The address occupies indices 190 to 206, so the 200-character cut falls inside it.
- **Redact first (current code, `macaddr.py:126`):** a space sits on each side of the address, so both `\b` boundaries of `_ANY_MAC_RE` (`macaddr.py:42`) match. The result contains `<address>`, cut at 200, with no `02:aa:bb`. The test passes.
- **Truncate first (R3):** the cut keeps `02:aa:bb:c`, which has fewer than six octets, so the regex does not match and the fragment leaks. The test fails.

The fixture discriminates. It pins the order only at the current cut of 200, since it checks for the literal `02:aa:bb`.

### Side effects of the P3-1 fix (`_refresh_chain()` in `_after_finish`)

| Concern | Evidence | Result |
|---|---|---|
| Blocking I/O on the UI thread | `_refresh_chain` (`vpn_privacy.py:421-433`) rebuilds the table and calls `ProxyChain.problems()` / `describe()` (`agents/vpn_agent/services/proxychain.py:67-85`) and `ProxyHop.problems()` (`agents/vpn_agent/services/socks_client.py:64-74`). All of it is field validation and string formatting, with no socket, file or subprocess. | No new blocking |
| Called twice | Probe counting calls during one approved Tor Start: 1 on HEAD, 0 on the parent | Single call |
| Valid chain after an approved action | Probe: one valid hop, approved Tor Start, then `chain_test_btn.isEnabled()` is True and `busy` is False | Correct, but **no test pins it** (P3-2) |
| Busy interaction | `_finish` clears `_active` (`vpn_gate.py:104`) before `_after_finish` (`:107`), so the `not self.busy` guard (`vpn_privacy.py:433`) sees not-busy. Probe: changing the mode while a deferred action runs keeps Test chain off, and it comes back on after the action finishes. | Correct |
| Hop selection | Probe: select row 1 of two hops, then approve Tor Start. `chain_table.currentRow()` is -1 on HEAD and 1 on the parent. A declined Randomise (the read path, through `_controls_changed`, since 45d7fe2) also leaves -1. | **Side effect** (P3-1) |
| `refresh_interfaces` raising a non-`OSError` | Probe: `list_interfaces` raises `UnicodeDecodeError` during `_after_finish`. Afterwards `busy` is False and the buttons are all on, identical to the parent. | Not a regression |

## Mutant results

"3 files" is the three targeted files. "7 files" adds the four other gate-related files and is judged against their 3 baseline errors.

| # | Mutant | Suite | Result | Killed by |
|---|---|---|---|---|
| R1 | Remove `self._refresh_chain()` from `_after_finish` | 3 files | **Killed** | `test_test_chain_stays_off_after_an_approved_action_with_an_empty_chain` |
| R2 | Read `failed`: `self._active=False; self._set_enabled(True)` instead of `release()` | 3 files | **Killed** | `test_a_worker_failure_during_the_read_frees_the_tab` |
| R3 | Truncate before redact (`macaddr.py:126`) | 3 files | **Killed** | `test_a_listing_error_is_redacted_before_it_is_cut` |
| R4 | `_after_finish`: `self.chain_test_btn.setEnabled(False)` instead of `_refresh_chain()` | 7 files | **Survived** | none (P3-2) |
| R5 | `_refresh_chain` drops `and not self.busy` | 7 files | **Survived** | none (P3-3, already on `main`) |
| R6 | `_finish` clears `_active` after `_after_finish` instead of before | 7 files | Killed, only by `test_vpn_servers_tab.py::test_export_is_gated_and_files_are_private`; survives the 3 Privacy-tab files | (P3-3) |
| R7 | `_refresh_chain` ignores `problems` | 7 files | **Survived** | none (P3-3, already on `main`) |

## Findings

### P3-1: An approved action now clears the hop selected in the chain table
`ui/panels/vpn_privacy.py:211` (new call) and `:422` (`self.chain_table.setRowCount(0)`).
`_refresh_chain` rebuilds the table from scratch, which drops the current row. It used to run only after a chain edit, which then sets the row itself (`move_hop`, `:479`). Now it also runs after every gated action, and since 45d7fe2 after every read, through `_controls_changed` (`:206`).
Scenario: the user selects hop 2, approves Test chain, Tor Start or a MAC change, then presses Remove selected and gets "Select a hop first." The parent kept row 1 selected; HEAD shows -1 (probe).
Impact: cosmetic, and it fails safe: a lost selection refuses the action and never removes the wrong row.
Suggestion: separate button state from the table rebuild. Add a `_chain_buttons()` method that only sets `chain_test_btn.setEnabled(bool(hops) and not problems and not busy)`, and call it from `_after_finish` and `_controls_changed`. Keep `_refresh_chain` for edits. Alternatively, save and restore `currentRow()` inside `_refresh_chain`.

### P3-2: Test chain coming back on after an approved action with a valid chain is not pinned
`tests/test_vpn_privacy_tab.py:553` only covers the empty chain. Mutant R4 survived all seven gate-related files. The code is correct today (probe).
Suggestion: add the valid case. `_add_hop(env.tab)`, approve `start_tor`, then assert `chain_test_btn.isEnabled()`.

### P3-3: `_refresh_chain`'s busy and problems guards, and `_finish`'s ordering, are not pinned on the Privacy tab (already on `main`)
`ui/panels/vpn_privacy.py:433` and `ui/panels/vpn_gate.py:103-107`.
- R5 (no busy guard) survived. If the user changes the chain mode while an action runs (`chain_mode_box` is not in `_set_enabled`, `:213-218`), Test chain would turn on mid-action. The click would be refused as busy by `_gated` (`vpn_gate.py:43`), so nothing is sent.
- R7 (problems ignored) survived. A chain with a bad hop would light Test chain, and `test_chain` would refuse it (`:490-493`).
- R6 (`_after_finish` while still busy) leaves every Privacy button off after any action. Only the Servers-tab test catches it.

None of these is a security failure, because every action re-checks. These are test gaps in code this commit now relies on.
Suggestion: add two short Privacy-tab tests: (a) a deferred action plus a mode change keeps Test chain off; (b) a hop that fails `problems()` keeps Test chain off. Optionally (c): after an approved action, Randomise is on (pins R6 on this tab).

### P3-4: TODO.md describes the mutants and the suite count inaccurately
`TODO.md:502`. Review 2's survivors were M5 (equivalent), M12 and M13; M14 is new in this commit, so it did not survive review 2. The added sentence is followed by "Full suite 3140 passed…", which reads as this commit's result; this commit's run is 3154 passed.
Suggestion: reword, and cite this report once it is saved.

### Not a finding
- **`refresh_interfaces` raising a non-`OSError`.** `_refresh_chain` and `refresh_tor` are then skipped and the buttons stay all-on. This is the same on the parent, and deliberate: a bug is meant to surface (`test_refresh_survives_an_os_error_but_not_a_bug`). Calling `_refresh_chain()` before `refresh_interfaces()` would narrow it for free.
- **The `\b` boundaries in `_ANY_MAC_RE`.** An address glued to a word character (for example `x02:aa:bb:cc:dd:ee`) is left unredacted. This was already on `main`, and `redact_addresses` documents the forms it covers (`macaddr.py:208-213`). Real `networksetup` error output producing that form was not seen; not verified.
- **Thread safety.** The new call runs on the UI thread inside a slot, takes no lock, does no I/O and adds no `QThread.terminate()`.
- **Security surface.** No subprocess, privilege, secret, logging or network path changed. The table never shows passwords (columns: number, kind, host, port, user, label; `vpn_privacy.py:427`).
- **Not verified:** the cause of the ordering-dependent `test_tunnel_keeps_no_logs.py` setup errors in the seven-file run.

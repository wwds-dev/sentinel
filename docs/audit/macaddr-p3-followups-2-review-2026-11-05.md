# Independent review of d5d1d35 (fixes for the review of bb4d35d, MAC changer follow-up P3s)

Reviewer: Opus 5.5, read-only (`code-reviewer` + `security-engineer`), 2026-11-05.
Scope: `d5d1d35` on `claude/macaddr-p3-followups-2` (parent `dd27bdf` = `origin/main`). Files: `ui/panels/vpn_privacy.py`, `tests/test_vpn_privacy_tab.py`, `TODO.md`. Tier C review: the reviewer did not write this commit.

Playbooks:
- `docs/playbooks/code-reviewer.md` is `last-reviewed: 2026-10-14`, `status: current`.
- `docs/playbooks/security-engineer.md` is **`status: baseline`** (`last-reviewed: 2026-10-10`). Under AGENTS.md's rule that makes it **stale**.
- `docs/company/app-context.md` is not tracked in this worktree, so the main checkout's copy was read (read-only).

**Verdict: ready to merge.** The diff has no P0 or P1 findings and no regression. All three findings from the review of bb4d35d are closed. The new P3-1 test fails without its fix. R4, R5, R6 and R7 are now killed by the Privacy-tab tests alone. The diff leaves two new test gaps (P3-2). Behaviour checks found one P2 that was already on `main` before this commit: hops can be edited while Test chain is running, and the probe then uses hops the user never confirmed. It is not caused or made worse by d5d1d35 and does not block this merge, but it should be filed.

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Targeted suites | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_vpn_privacy_tab.py tests/test_macaddr.py tests/test_quit_privileged.py` | 187 passed (`test_vpn_privacy_tab.py` alone: 48 passed) |
| Full suite | `.venv/bin/python -m pytest -p no:cacheprovider -q` at HEAD `d5d1d35` | 3171 passed, 14 skipped, 1 deselected, 1 failed. The failure is `test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing` (`ForbiddenInTests` on `lms server status`), known and unrelated. This matches the commit message and TODO.md. |
| Seven gate-related files | the three above plus `test_tunnel_keeps_no_logs.py`, `test_vpn_app_control.py`, `test_vpn_review_fixes.py`, `test_vpn_servers_tab.py` | 299 passed, 2 skipped, 3 errors in `test_tunnel_keeps_no_logs.py`. These are the same 3 ordering-dependent setup errors as in the 2026-11-03 review, and the same with every mutant. They are the baseline. |
| New tests against the parent's code | `git show dd27bdf:ui/panels/vpn_privacy.py > ui/panels/vpn_privacy.py`, run `tests/test_vpn_privacy_tab.py`, then `git checkout --` | 1 failed (`test_an_action_keeps_the_selected_hop`), 47 passed. This is expected: the other three tests cover behaviour that was already correct (P3-2/P3-3 were test gaps). |
| Hand mutants | 12, planted one at a time by a scratch script (each replacement asserted to match exactly once), then `git checkout -- <file>` | 9 killed, 1 equivalent, 2 survived (see the mutant table) |
| Reviewer probes | 12 temporary tests using the real `env` fixture, `DeferredWorker` and `_add_hop`; deleted afterwards | see "Behaviour" |
| Tree clean at end | `git status --short`; `git diff --stat` | both empty |

No live MAC, Wi-Fi, root, tunnel or network action was run. Every probe and mutant used the mocked fixture (`SyncWorker`/`DeferredWorker`, faked `macaddr`, `tor`, `proxychain.probe`/`http_get_through`).

## Each fix against its finding

| Finding (review of bb4d35d) | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P3-1: an approved action or a read cleared the selected hop | `_controls_changed` and `_after_finish` call `_show_chain_test()` (`ui/panels/vpn_privacy.py:206`, `:211`). It only sets the button state (`:438-441`). `_refresh_chain` stays for chain edits. | Yes. Probe: `_refresh_chain` is called 0 times and `_show_chain_test` once, on both the approved path and the declined-read path. | `test_an_action_keeps_the_selected_hop` (`tests/test_vpn_privacy_tab.py:565`). It fails on the parent, and kills Ma and Mb. |
| P3-2: Test chain coming back on with a valid chain was not pinned | `test_test_chain_comes_back_on_after_an_approved_action_with_a_valid_chain` (`:578`) | Yes | Kills R4 and R6 |
| P3-3: busy guard, problems guard and `_finish` ordering not pinned on the Privacy tab | `test_a_mode_change_while_an_action_runs_keeps_test_chain_off` (`:589`), `test_a_chain_with_a_bad_hop_keeps_test_chain_off` (`:604`), and the `mac_random_btn` assertion at `:586` | Yes | R5 by `:589`; R7 by `:604`; R6 by `:578` and `:589`. All are now killed by the Privacy-tab file alone. |
| P3-4: TODO wording | TODO.md:502 rewritten | Yes. The counts now match measured results (see "Accuracy"). | n/a |

## Behaviour

| Concern | Evidence | Result |
|---|---|---|
| Summary label stale after an action or read | `chain_summary_label` depends only on `self._chain.describe()` and `problems()` (`vpn_privacy.py:429-432`). `self._chain` is assigned only in `__init__` (`:50`). Its hops and mode change only through `add_hop`, `add_tor_hop`, `remove_hop`, `move_hop` and `_mode_changed`, and each calls `_refresh_chain` (`:419`, `:457`, `:463`, `:476`, `:486`). `tor.start/stop/check/new_identity`, `macaddr.*` and `proxychain.probe` do not write to the chain (`proxychain.py:205-236`). | Not stale |
| Does an action change the chain? | No action does. The **user** can change it while an action runs: the hop-edit buttons and `chain_mode_box` are not in `_set_enabled` (`:213-218`). See P2-1. Each such edit rebuilds the table itself, so the table never goes stale. | Not stale (but see P2-1) |
| Selection survives: approved action | in the new test (`:571-572`) | Row 1 kept |
| declined read | in the new test (`:574-575`) | Row 1 kept |
| failed action (`tor.start` → `(False, …)`) | probe | Row 1 kept |
| worker exception (`_gated.failed`) | probe | Row 1 kept, status "Failed…" |
| read error (`env.read_error`) | probe | Row 1 kept |
| interface gone (`_fill_interfaces` path) | probe; warning "no longer available" | Row 1 kept |
| approved Test chain | probe (`proxychain.probe` faked) | Row 1 kept |
| chain mode change | probe: row is -1 afterwards | **Dropped** (already on `main`, P3-1) |
| Busy guard on the read path | `release()` sets `_active=False` before `_set_enabled(True)` and `_controls_changed()` (`vpn_gate.py:85-88`), so `_show_chain_test` sees not-busy. If `then` goes on to call `_gated`, `_set_enabled(False)` switches the button off again. Mutant Md is killed by three read-path tests. | Correct |
| Blocking I/O on the UI thread | `_show_chain_test` → `Chain.problems()` → `ProxyHop.problems()` is field validation only (`proxychain.py:67-81`, `socks_client.py:64-74`) | None |
| Security surface | No subprocess, privilege, secret, logging or network path changed. The table never shows passwords (`:427`). | No change |

## Test quality

- **`test_a_chain_with_a_bad_hop_keeps_test_chain_off` (`:604`).** `ProxyHop` is a plain, non-frozen `@dataclass` (`socks_client.py:49`), so `hops[0].host = ""` is legal. The state is also realistic: a chain saved with `ProxyHop(host="")` loads through `load_chain` → `ProxyHop.from_dict`, which does no validation (`socks_client.py:82-85`); the tab builds with Test chain off and the summary reads "Problems: hop 1: no host" (probe). The test mutates the chain in memory rather than loading it from disk, which is a weaker but sufficient pin; it kills R7. Related gap: P3-3.
- **`test_a_mode_change_while_an_action_runs_keeps_test_chain_off` (`:589`).** It does reach `_mode_changed` while busy: `chain_mode_box.isEnabled()` is True during an action (probe), so the scenario is reachable for a real user, and R5 is killed only by this test. A probe also showed that `setCurrentText` on a **disabled** `MenuComboBox` still emits `currentTextChanged`, so the test would keep passing if the box were later disabled during actions, and the claim in its comment ("stays usable") is not asserted. Folded into P3-2.
- `DeferredWorker.started = []` rebinds a class attribute without monkeypatch. The file's other tests do the same; consistent and harmless.

## Accuracy

- **Commit message.** "Seven hand mutants … including R4, R5 and R7 … are all killed" fits what was measured: R4 to R7 are killed by `test_vpn_privacy_tab.py` alone. The message does not name the seven, so the exact set is not verified. The full-suite count matches (it leaves out the 1 deselected). "the chain table is no longer rebuilt and the selected hop is kept" matches the probes. The test list matches `:586`.
- **TODO.md:502.** Accurate. "Not yet independently reviewed" should now cite this report. "after an action or a read only Test chain's state is recomputed" means the chain section; `_after_finish` still refreshes interfaces and Tor. No finding.

## Mutant results

"3 files" means the three targeted files. Survivors were re-run against the seven files and judged against the 3 baseline errors.

| # | Mutant | Suite | Result | Killed by |
|---|---|---|---|---|
| R4 | `_after_finish`: `chain_test_btn.setEnabled(False)` instead of `_show_chain_test()` | 3 files | **Killed** | `…comes_back_on…valid_chain`, `…mode_change_while_an_action_runs…` |
| R5 | `_show_chain_test` drops `and not self.busy` | 3 files | **Killed** | `…mode_change_while_an_action_runs…` |
| R6 | `_finish` clears `_active` after `_after_finish` (`vpn_gate.py:103-107`) | 3 files | **Killed** | `…comes_back_on…valid_chain`, `…mode_change_while_an_action_runs…` |
| R7 | `_show_chain_test` ignores `_chain_problems()` | 3 files | **Killed** | `test_a_chain_with_a_bad_hop_keeps_test_chain_off` |
| Ma | `_after_finish` calls `_refresh_chain()` again | 3 files | **Killed** | `test_an_action_keeps_the_selected_hop` |
| Mb | `_controls_changed` calls `_refresh_chain()` again | 3 files | **Killed** | `test_an_action_keeps_the_selected_hop` |
| Mc | `_after_finish` does not touch the chain | 3 files | **Killed** | `test_test_chain_stays_off_after_an_approved_action_with_an_empty_chain` |
| Md | `_controls_changed` does not touch the chain | 3 files | **Killed** | 3 read-path tests |
| Me | `_refresh_chain` no longer calls `_show_chain_test()` | 3 files | **Killed** | 3 tests incl. `…bad_hop…` |
| Mf | `_chain_problems` stops filtering "DNS" | 7 files | **Survived** | none (P3-2) |
| Mg | summary label ignores problems (`problems = []` in `_refresh_chain`) | 7 files | **Survived** | none (P3-2) |
| Mh | `_show_chain_test` drops `bool(self._chain.hops)` | 7 files | Survived, **equivalent** | An empty chain always yields "the chain has no proxies in it" (`proxychain.py:71-72`), which contains no "DNS" |

## Findings

### P2-1 (already on `main`, not a regression): editing the chain while Test chain runs changes the route the probe uses
`ui/panels/vpn_privacy.py:507-509` (`chain = self._chain`: the worker shares the live object), `:213-218` (`_set_enabled` leaves `hop_add_btn`, `hop_add_tor_btn`, `hop_remove_btn`, `hop_up_btn`, `hop_down_btn` and `chain_mode_box` on), and `agents/vpn_agent/services/socks_client.py:236-238` (`connect_through` re-reads `len(hops)` and `hops[index + 1]` on every iteration).

Scenario: the chain is 10.0.0.1 → 10.0.0.2. The user approves Test chain, whose confirmation lists those two hops, then presses Add hop with 10.9.9.9 while the probe is still running. The probe then goes 10.0.0.1 → 10.0.0.2 → 10.9.9.9.
- Probe result: the confirmation did not list 10.9.9.9; the probe used `['10.0.0.1', '10.0.0.2', '10.9.9.9']`. Shown with `DeferredWorker`. With the real `CallWorker`, the same live list is read concurrently for up to 15 to 20 s of socket timeouts; that concurrent case was reasoned from the code and not run.
- Removing or reordering a hop mid-probe (reasoned, not run): the probe may report success for a shorter chain than was confirmed, or raise `IndexError` at `hops[index + 1]`, which reaches the user as "Failed: list index out of range".
- This breaks the tab's promise of "a review that says exactly what will happen" (`vpn_privacy.py:3-5`) and the CLAUDE.md gate rules.
- There is no clear-net path: the socket is always opened to the original first hop first.

Suggestion: snapshot the chain at confirmation (for example `proxychain.Chain.from_dict(self._chain.to_dict())` or `copy.deepcopy`) before `_gated`. Also consider putting the hop-edit buttons in `_set_enabled`. Add a test: with a `DeferredWorker` probe, add a hop while busy, then assert that `http_get_through` received only the confirmed hops.

### P3-1 (already on `main`): changing the chain mode still clears the selected hop
`ui/panels/vpn_privacy.py:416-419`. `_mode_changed` calls `_refresh_chain()`, which runs `setRowCount(0)` (`:422`). Same symptom as the P3-1 just fixed, through the remaining path. Probe: select row 1, set the mode to `dynamic`, and `currentRow()` is -1. Nothing in the table or summary depends on the mode. Cosmetic and fails safe.

Suggestion: `_mode_changed` calls `self._show_chain_test()` instead of `_refresh_chain()`, or `_refresh_chain` saves and restores `currentRow()` when still in range.

### P3-2: the new `_chain_problems()` filter and the summary's Problems line are not pinned; the filter exists three times
`ui/panels/vpn_privacy.py:435-436` (new helper), `:498` (`test_chain` keeps its own copy), and `agents/vpn_agent/services/proxychain.py:212` (`probe` has a third).
- Mutants Mf and Mg survive all seven gate-related files.
- Mf scenario: a chain with `proxy_dns=False` (hand-edited file only). Today Test chain stays on, as intended (probe). With the filter dropped it would turn off silently and no test would notice.
- Because `test_chain` does not use `_chain_problems()`, the button and the action can drift apart (fails safe, but needless duplication).
- The mode-change test's comment claims the box stays usable during an action, but the test does not assert it.

Suggestion: `test_chain` uses `self._chain_problems()`; add a test that a valid hop with `proxy_dns = False` keeps Test chain on; assert `"Problems: hop 1: no host"` in the summary in the bad-hop test; add `assert env.tab.chain_mode_box.isEnabled()` in the mode-change test.

### P3-3 (already on `main`): a malformed saved chain stops the Privacy tab from being built
`agents/vpn_agent/services/proxychain.py:116-123` and `agents/vpn_agent/services/socks_client.py:68-71`, `:82-85`. `ProxyHop.from_dict` accepts any value types. `load_chain` catches only `OSError`, `JSONDecodeError` and `TypeError` around `from_dict`, which raises none of them for wrong types; the failure comes later, in `problems()`.
- Probe: `proxychain.json` = `{"hops": [{"host": 5}]}`, then `PrivacyTab()` raises `AttributeError: 'int' object has no attribute 'strip'` from `_refresh_chain` in `__init__` (`vpn_privacy.py:52`).
- A string port (`"1080"`) would raise `TypeError` from `1 <= self.port` in the same place (not run).
- `PrivacyTab()` is built inside `VpnPanel` (`ui/panels/vpn.py:382`), built with the main window (`main.py:2886`), so this likely stops Sentinel from starting (not verified).
- The file is owner-only and has to be hand-edited.

Suggestion: coerce or validate types in `ProxyHop.from_dict`, or fall back to an empty `Chain()` in `load_chain` on `(AttributeError, TypeError, ValueError)`. Add a load test for each.

### Not a finding
- **Thread safety.** `_show_chain_test` runs on the UI thread inside slots, takes no lock, does no I/O, adds no `QThread.terminate()`.
- **"DNS" substring filter.** A hand-edited hop of kind `"DNS"` would have its "unknown proxy type" problem filtered out, and `connect_through` would then refuse it after connecting to hop 1 (`socks_client.py:241-243`). Already on `main`, contrived, fails closed; P3-2's single helper would be the place to match on something sturdier.
- **Not verified:** the cause of the 3 ordering-dependent `test_tunnel_keeps_no_logs.py` setup errors in the seven-file run; the exact seven mutants the commit message counts; P2-1 under a real concurrent `CallWorker`.

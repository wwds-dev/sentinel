# Independent review of 729fd02 (Test chain snapshot, chain edit lock, probe_problems, typed from_dict)

Reviewer: Opus 5.5, read-only (`code-reviewer` + `security-engineer`), 2026-11-05.
Scope: `729fd02` on `claude/chain-snapshot` (parent `c7786ed` = `origin/main`). Files: `ui/panels/vpn_privacy.py`, `agents/vpn_agent/services/proxychain.py`, `agents/vpn_agent/services/socks_client.py`, `tests/test_vpn_privacy_tab.py`, `tests/test_vpn_socks_chain.py`, `TODO.md`. Tier C review: the reviewer did not write this commit.

Playbooks:
- `docs/playbooks/code-reviewer.md` is `last-reviewed: 2026-10-14`, `status: current`.
- `docs/playbooks/security-engineer.md` is still **`status: baseline`** (`last-reviewed: 2026-10-10`). Under AGENTS.md's rule that makes it **stale**.
- `docs/company/app-context.md` is not tracked in this worktree, so the main checkout's copy was read (read-only).

**Verdict: ready to merge.** No P0, P1 or P2 findings.
- All four findings from the review of d5d1d35 (P2-1, P3-1, P3-2, P3-3) are closed. 16 of the new or changed test cases fail on the parent's code.
- 20 hand mutants: 15 killed, 2 survivors equivalent, 3 survivors are real test gaps (P3-4).
- One small regression introduced by this commit: a mode change no longer refreshes the summary's Problems line (P3-1).
- Two residual load-path issues, both needing a hand-edited or corrupted `proxychain.json` (P3-2, P3-3).

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Targeted suites | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_vpn_privacy_tab.py tests/test_vpn_socks_chain.py tests/test_macaddr.py tests/test_quit_privileged.py` | 219 passed. The first two files alone: 80 passed. |
| Full suite | `.venv/bin/python -m pytest -p no:cacheprovider -q` at HEAD `729fd02` | 3194 passed, 14 skipped, 1 deselected, 1 failed (`test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`, `ForbiddenInTests` on `lms server status`; known and unrelated). Matches the commit message and TODO.md. |
| All VPN/Tunnel test files (18: `test_vpn*.py`, `test_tunnel*.py`, `test_macaddr.py`, `test_quit_privileged.py`) | same runner | 609 passed, 2 skipped, 3 errors in `test_tunnel_keeps_no_logs.py`: the same 3 ordering-dependent setup errors as in the last two reviews, the same with every mutant, so they are the baseline. |
| New tests against the parent's code | `git show c7786ed:<file> > <file>` for the three source files, then the two chain test files, then `git checkout --` | 16 failed, 64 passed (list below) |
| Hand mutants | 20, planted one at a time by a scratch script (each replacement asserted to match exactly once), each followed by `git checkout -- <file>` | 15 killed, 2 equivalent, 3 survived |
| Reviewer probes | 15 temporary tests using the real `env` fixture, `DeferredWorker` and `_add_hop`; deleted afterwards | see "Behaviour" |
| Tree clean at end | `git status --short`; `git diff --stat` | both empty |

No live MAC, Wi-Fi, root, tunnel or network action was run. Every probe and mutant used the mocked fixture (`SyncWorker`/`DeferredWorker`, faked `macaddr`, `tor`, `proxychain.probe`/`http_get_through`) or the local fake SOCKS proxy.

New or changed tests that fail on the parent:
- `test_a_mode_change_while_an_action_runs_keeps_test_chain_off`, through its new `isEnabled` assertion
- `test_test_chain_probes_the_hops_that_were_confirmed`
- `test_the_chain_cannot_be_edited_while_an_action_runs`
- `test_a_mode_change_keeps_the_selected_hop`
- all 4 cases of `test_a_malformed_saved_chain_still_builds_the_tab`
- 7 of the 8 cases of `test_a_wrongly_typed_saved_chain_loads_as_empty` (the `{"mode": 3}` case passes on the parent because the crash was in the tab, not in `load_chain`; the tab-level case covers it)
- `test_probe_problems_leave_out_only_the_dns_setting`

These pass on the parent, as expected, because they pin behaviour that was already correct: `test_a_chain_without_proxychains_dns_can_still_be_tested`, `test_probe_runs_with_proxychains_dns_off`, `test_a_well_typed_saved_chain_still_loads`.

## Each fix against its finding

| Finding (review of d5d1d35) | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P2-1: the probe used the live chain, and hop edits stayed on during an action | `test_chain` probes `Chain.from_dict(self._chain.to_dict())` (`ui/panels/vpn_privacy.py:515`). `_set_enabled` also covers the five hop buttons and `chain_mode_box` (`:218-221`). | Yes | `test_test_chain_probes_the_hops_that_were_confirmed` (`tests/test_vpn_privacy_tab.py:695`) kills M1 and M2. `test_the_chain_cannot_be_edited_while_an_action_runs` (`:716`) kills M4 and M5. |
| P3-1: a mode change cleared the selected hop | `_mode_changed` calls `_show_chain_test()` (`vpn_privacy.py:422-425`) | Yes for the selection, but it introduced a stale summary (P3-1 below) | `test_a_mode_change_keeps_the_selected_hop` (`:729`) kills M6 |
| P3-2: the filter existed three times, was not pinned, and matched on the text "DNS" | `Chain.probe_problems()` (`proxychain.py:76-87`), used at `vpn_privacy.py:442`, `:504` and `proxychain.py:225`; `problems()` = `probe_problems()` + the DNS warning (`:67-74`) | Yes | Tab button (M15), summary (M20), the `problems()` split (M17) and `probe()` using `problems()` (M14) are killed. `test_chain` itself (M16) and `probe()`'s use of the new filter (M19) are not pinned (P3-4). |
| P3-3: a wrongly typed `proxychain.json` raised in `PrivacyTab()` | Type and shape checks in `Chain.from_dict` (`proxychain.py:105-111`) and `ProxyHop.from_dict` (`socks_client.py:83-92`); `load_chain` already catches `TypeError` (`proxychain.py:135`) | Yes for wrong types and shapes. A non-UTF-8 file still raises (P3-3 below). | `test_a_malformed_saved_chain_still_builds_the_tab` (`:750`) and `test_a_wrongly_typed_saved_chain_loads_as_empty` (`tests/test_vpn_socks_chain.py:203`) kill M8 to M12 and M18 |

## Behaviour

| Concern | Evidence | Result |
|---|---|---|
| Is the snapshot an independent copy? | `to_dict` builds new dicts (`proxychain.py:94-101`, `socks_client.py:76-80`); `from_dict` builds a new list and new `ProxyHop`s. Probe: `copy.hops is not c.hops`, every hop is a distinct object, `copy.to_dict() == c.to_dict()`. All fields are `str`/`int`/`bool`. | Fully independent. The worker now touches only its own copy, which also removes the cross-thread read of the live list. |
| Can the new validation raise `TypeError` in `test_chain` on the UI thread? | Every UI path yields `str` and `int` fields: `add_hop` uses `currentText()`/`text()`, `QSpinBox.value()` returns `int`, `tor.hop()` sets `port=SOCKS_PORT = 9250` (`tor.py:44`, `:319-321`) and `label="Tor"`, the mode comes from `currentText()`, the name is never edited, and loaded chains already passed the same validation. Probe: hops at ports 1 and 65535 (one with user and password), a Tor hop, each of the three modes, then `from_dict(to_dict())` and an approved `test_chain`: no exception. | Not reachable from the UI |
| Does the confirm text show the hops the copy holds? | The text (`:508`) and the copy (`:515`) are both built from `self._chain` in the same call, before `_ask`; no chain edit can run during the modal question. Probe: the faked `probe` received the same `describe()` as the live chain. | Same hops |
| Edit controls re-enabled on every path | `_set_enabled(True)` runs in `_finish` (`vpn_gate.py:115`) for approved, failed and worker-error results, and in `release()` (`:96`) for read success and read error. Declined and refused paths return before `_set_enabled(False)` (`:46-48`; `test_chain`'s refuse at `:505-507`). Nothing else disables them. Probe: approved Tor start plus the follow-up listing: all six on afterwards. | Re-enabled on every path |
| Locked during unrelated actions and reads | Probe: on first show, the off-thread interface listing turns all six off until it lands, then on again; the same after every action and during `_with_fresh_interface`. Hop input fields, `Write proxychains.conf` and table selection stay usable; `write_conf` writes `self._chain`, which cannot change while busy. | Brief and consistent with the other buttons; never sticks. Not a finding. |
| Do the types `save_chain` writes still load? | `to_dict` writes `str` fields, `port` as `int`, and `enabled`/`proxy_dns` as `bool` (those two are only `bool()`-coerced). Missing keys keep defaults, extra keys are ignored, unicode is fine (probe: label "Zürich ✓"). | No file written by `save_chain` is rejected. Formats from before `650b5d3` were not checked. |
| What the checks now reject | Probe: a float port `1080.0`, `"label": null`, `"name": null` and `"hops": null` each load as an empty chain. `"enabled": "false"` loads as `enabled=True` (existing coercion; `enabled` is never read). | See P3-2 |
| Other callers of `from_dict` | grep: `proxychain.py:118`, `:134`, `vpn_privacy.py:515` only | None affected |
| `probe_problems` vs the old `"DNS" not in p` filter | The old filter dropped any problem containing "DNS": the `proxy_dns` warning, and also `unknown proxy type 'DNS…'` or `unknown chain mode '…DNS…'`. Host, port and password messages never contain "DNS". | Same blocking set, except that a kind or mode containing "DNS" now blocks. Deliberate and stricter. |
| A non-text mode used to raise in `setCurrentText` | Probe: `MenuComboBox().setCurrentText(3)` raises `TypeError` | Confirmed |
| Security surface | No subprocess, privilege or logging path changed. The new `TypeError` messages name the field, never its value (`socks_client.py:92`, `proxychain.py:108-111`). The snapshot carries the password only in memory, to the worker. | No change |

## Accuracy

- **Commit message.** Every claim matches what was measured (the copy at the confirm, the six controls off while any action runs, the kept selection, the single filter, the `TypeError` and empty chain, the `setCurrentText` crash, the suite counts, which leave out the 1 deselected). "Off while any action runs" also covers read-only work (the interface listing and the fresh-interface read). "10/10 hand mutants" are not named, so the set is not verified. "No longer rebuilds the table" is true, but it also stopped refreshing the summary (P3-1).
- **TODO.md:139 and :503.** Accurate and correctly say "not yet independently reviewed". They should now cite this report.
- **The previous review was wrong on one point.** Its P3-1 note said "Nothing in the table or summary depends on the mode". The summary's Problems line does, through `unknown chain mode`. That is the gap behind P3-1 below.

## Mutant results

"4 files" means `test_vpn_privacy_tab.py`, `test_vpn_socks_chain.py`, `test_macaddr.py` and `test_quit_privileged.py`. Survivors were re-run against all 18 VPN/Tunnel files and judged against the 3 baseline errors; none was killed there either.

| # | Mutant | Result | Killed by |
|---|---|---|---|
| M1 | no snapshot: `chain = self._chain` | **Killed** | `test_test_chain_probes_the_hops_that_were_confirmed` |
| M2 | copy shares the hop list: `Chain(hops=self._chain.hops, …)` | **Killed** | same |
| M3 | shallow copy: `Chain(hops=list(self._chain.hops), …)` | Survived, **equivalent**: the UI never mutates a `ProxyHop` in place (`move_hop` swaps list entries) | none |
| M4 | the six edit controls left out of `_set_enabled` | **Killed** | `…cannot_be_edited_while_an_action_runs`, `…mode_change_while_an_action_runs…` |
| M5 | only `chain_mode_box` left out | **Killed** | same two |
| M6 | `_mode_changed` calls `_refresh_chain()` again | **Killed** | `test_a_mode_change_keeps_the_selected_hop` |
| M7 | `_mode_changed` recomputes nothing | **Survived** (18 files) | none (P3-4) |
| M8 | `ProxyHop.from_dict` type checks removed | **Killed** | malformed-chain tests (6 cases) |
| M9 | bool port accepted | **Killed** | `…loads_as_empty[{"hops": [{"port": true}]}]` |
| M10 | `ProxyHop.from_dict` non-dict check removed | **Killed** | `…loads_as_empty[{"hops": ["10.0.0.1"]}]` |
| M11 | `Chain.from_dict` shape check removed | **Killed** | `…[[]]` in both files |
| M12 | `Chain.from_dict` name/mode check removed | **Killed** | `…still_builds_the_tab[{"mode": 3}]` |
| M13 | only `mode` checked, not `name` | Survived, **equivalent**: `name` is used only in `to_dict` and the conf comment f-string (`proxychain.py:157`), where any type renders | none |
| M14 | `probe()` uses `problems()` | **Killed** | `test_probe_runs_with_proxychains_dns_off` |
| M15 | `_chain_problems` uses `problems()` | **Killed** | `test_a_chain_without_proxychains_dns_can_still_be_tested` |
| M16 | `test_chain` uses `self._chain.problems()` | **Survived** (18 files); killed by a reviewer probe that presses Test chain with `proxy_dns=False` | none (P3-4) |
| M17 | `problems()` drops the DNS warning | **Killed** | `test_probe_problems_leave_out_only_the_dns_setting` |
| M18 | `load_chain` stops catching `TypeError` | **Killed** | 11 cases |
| M19 | `probe()` goes back to the `"DNS" not in p` text filter | **Survived** (18 files) | none (P3-4) |
| M20 | summary uses `problems()` | **Killed** | `test_a_chain_without_proxychains_dns_can_still_be_tested` |

## Findings

### P3-1 (introduced by this commit): after a mode change the summary still lists the old mode problem
`ui/panels/vpn_privacy.py:422-425` (`_mode_changed` now only calls `_show_chain_test()`), `:435-438` (the summary is written only by `_refresh_chain`), and `:172` (`setCurrentText` of an unknown mode leaves the box on "strict").

Scenario: `proxychain.json` has `"mode": "xyz"` and one valid hop (a hand edit; a `str` passes the new type check). On start the box shows "strict", the chain mode is `xyz`, the summary reads "Problems: unknown chain mode 'xyz'", and Test chain is off. The user picks "dynamic": the chain mode becomes `dynamic` and Test chain turns on, but the summary still says "Problems: unknown chain mode 'xyz'" (probe). Before this commit `_refresh_chain` rewrote the summary here. Cosmetic: the button and the probe use the correct state.

Suggestion: move the summary text into its own method (for example `_show_chain_summary()`, called by `_refresh_chain` and `_mode_changed`). Add a test that loads an unknown mode, changes it, and asserts that "Problems" is gone; that also kills M7.

### P3-2 (made wider by this commit): one bad field drops the whole saved chain without a word, and the next edit overwrites the file
`agents/vpn_agent/services/proxychain.py:129-136` (`load_chain` returns `Chain()` silently), `socks_client.py:88-92` (one wrongly typed field rejects every hop), and `ui/panels/vpn_privacy.py:416-420`, `:424`, `:462` (every edit saves).

Scenario: a hand-edited file holds hop 1 `{"host": "10.0.0.1", "username": "u", "password": "secret"}` and hop 2 with `"port": 1080.0`. The tab builds with an empty chain; nothing says the file was ignored. The user changes the mode, and `proxychain.json` is rewritten with `"hops": []`; hop 1 and its password are gone (probe). Before this commit a float port or a `null` label loaded (a `null` label probably crashed the tab). A `JSONDecodeError` already fell back the same way. `save_chain` never writes these types, so only a hand edit or an import gets here, but silently losing proxy credentials is worth a line.

Suggestion: have `load_chain` report a failed parse; the tab says "Could not read the saved chain (…); it will be replaced when you edit the chain" and copies the file aside (for example `proxychain.json.unreadable`) before the first save. Add a test that the original bytes survive the first edit.

### P3-3 (already on `main`, a residual of the fixed P3-3): a non-UTF-8 `proxychain.json` still stops the tab from being built
`agents/vpn_agent/services/proxychain.py:134-135`. `read_text(encoding="utf-8")` raises `UnicodeDecodeError`, a `ValueError` outside the caught `(OSError, json.JSONDecodeError, TypeError)`. Probe: raw bytes `{"hops": [], "name": "\xff"}` make `load_chain()` raise, which reaches `PrivacyTab.__init__` (`vpn_privacy.py:50`). `write_private` writes atomically (`server/paths.py:181-186`), so only a hand edit or disk corruption gets here.

Suggestion: catch `ValueError` (covering both `UnicodeDecodeError` and `JSONDecodeError`) in `load_chain`, and add the case to `test_a_wrongly_typed_saved_chain_loads_as_empty`.

### P3-4: three new branches are not pinned
- **M7.** Nothing asserts that a mode change recomputes Test chain's state; the P3-1 test would cover it.
- **M16.** `test_a_chain_without_proxychains_dns_can_still_be_tested` (`tests/test_vpn_privacy_tab.py:739`) asserts the button is on but never presses it. `test_chain` going back to `problems()` would refuse a chain the button offers (fails safe, but it is the drift the last review's P3-2 was about).
- **M19.** `test_probe_problems_leave_out_only_the_dns_setting` (`tests/test_vpn_socks_chain.py:218`) tests `probe_problems()`, not `probe()`. If `probe()` went back to the text filter, a hand-edited hop of kind "DNS" would reach `connect_through`, which opens a socket to hop 1 before refusing the unknown kind (`socks_client.py:240-250`).

Suggestion: in the M16 test, press Test chain with an approving answer and assert the faked probe ran. Add `assert not proxychain.probe(Chain(hops=[ProxyHop(kind="DNS", host="10.0.0.1")])).ok` with `socket.create_connection` patched to fail the test if called.

### Not a finding
- **No busy guard in the edit methods themselves.** `add_hop`, `remove_hop` and `move_hop` still run when called directly while busy (the P2-1 test relies on that). With the buttons off this is unreachable for a user, and the snapshot makes it harmless.
- **The chain locks during reads.** Brief, consistent with the rest of the tab, and never sticks (probe).
- **Thread safety.** The snapshot removes the only cross-thread access to `self._chain`. No `QThread.terminate()`, lock or UI-thread I/O was added.
- **Not verified:** the cause of the 3 ordering-dependent `test_tunnel_keeps_no_logs.py` setup errors; the exact 10 mutants the commit message counts; chain files written by vpn_agent versions from before `650b5d3`; behaviour under a real concurrent `CallWorker`.

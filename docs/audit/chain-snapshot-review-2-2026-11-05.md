# Independent review of 380227c (unreadable saved chain moved aside, summary on mode change)

Reviewer: Opus 5.5, read-only (`code-reviewer` + `security-engineer`), 2026-11-05.
Scope: `380227c` on `claude/chain-snapshot` (parent `dd7fb3b`, which holds the previous review; branch base `c7786ed` = `origin/main`). The commit fixes P3-1 to P3-4 from `docs/audit/chain-snapshot-review-2026-11-05.md`. Files: `agents/vpn_agent/services/proxychain.py`, `ui/panels/vpn_privacy.py`, `tests/test_vpn_privacy_tab.py`, `tests/test_vpn_socks_chain.py`, `TODO.md`. Tier C review: the reviewer did not write the commit.

Playbooks and context:
- `docs/playbooks/code-reviewer.md`: `last-reviewed: 2026-10-14`, `status: current`.
- `docs/playbooks/security-engineer.md`: still **`status: baseline`** (`last-reviewed: 2026-10-10`). Under AGENTS.md that makes it **stale**.
- `docs/company/app-context.md` is not in this worktree; the main checkout's copy was read (read-only). Its invariants that apply here: never print or copy credentials; do not imply Tunnel keeps logs.

**Verdict: ready to merge. No P0, P1 or P2 findings.**
- All four findings from the previous review are closed. 20 new or changed test cases fail on the parent's code.
- 20 hand mutants: 16 killed, 4 survived. Three survivors are real test gaps (P3-5); the fourth is low value.
- Six P3s remain: three residual paths in the new load-and-move code (P3-1, P3-2, P3-4), a notice that can be overwritten before the user sees it (P3-3), test gaps (P3-5) and docs (P3-6). Most need a hand-edited, corrupted or permission-changed `proxychain.json`.

## Evidence

| Check | Command / method | Result |
|---|---|---|
| Targeted suites | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_vpn_privacy_tab.py tests/test_vpn_socks_chain.py tests/test_macaddr.py tests/test_quit_privileged.py` | 228 passed. The first two files alone: 89 passed. |
| Full suite | `.venv/bin/python -m pytest -p no:cacheprovider -q` at HEAD `380227c` | 3203 passed, 14 skipped, 1 deselected, 1 failed (`test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`, `ForbiddenInTests` on `lms server status`; known and unrelated). Matches the commit message and TODO.md. |
| New tests against the parent's code | `git show dd7fb3b:<file> > <file>` for `proxychain.py` and `vpn_privacy.py`, then the two chain test files, then `git checkout --` | 20 failed, 69 passed |
| Hand mutants | 20, planted one at a time by a scratch script (each replacement asserted to match exactly once, then `git checkout -- <file>`); runner: the 4 targeted files | 16 killed, 4 survived |
| Loader probes | Scratch script with `VPN_AGENT_STATE_DIR` in a scratchpad directory: file modes, symlinks, deep nesting, permission denied, the text of every exception reason, a read-only directory | see "Security and behaviour" |
| UI probes | A temporary test file using the real `env` fixture: show the tab, a failed interface listing, a recorded MAC restore at start, the worst-case message length, the summary after every edit path; deleted afterwards | see "Security and behaviour" |
| Tree clean at end | `git status --short`; `git diff --stat` | both empty |

No live MAC, Wi-Fi, root, tunnel or network action was run. Every probe used the mocked fixture (`SyncWorker`, faked `macaddr`/`tor`/`proxychain.probe`) or a scratch state directory. No `.env` value was read.

New or changed tests that fail on the parent: `test_a_mode_change_refreshes_the_summary`; `test_an_unreadable_saved_chain_survives_the_first_edit`; all 4 cases of `test_a_malformed_saved_chain_still_builds_the_tab` (new status-line assertion); all 9 cases of `test_a_wrongly_typed_saved_chain_loads_as_empty` (new `load_chain_checked` assertions; the `\xff` case also through `load_chain`); `test_load_chain_checked_keeps_every_unreadable_copy`; `test_load_chain_checked_is_quiet_for_a_good_or_missing_file`; all 3 cases of `test_load_chain_checked_names_what_is_wrong`.

Two pass on the parent, as expected, because they pin behaviour that was already correct: `test_a_chain_without_proxychains_dns_can_still_be_tested` (its new press of Test chain) and `test_probe_refuses_a_dns_kind_without_opening_a_socket`. Mutants N19 and N20 show both now pin their branch.

## Each fix against its finding

| Finding (review of 729fd02) | Fix | Closed? | Test that fails without it |
|---|---|---|---|
| P3-1: after a mode change the summary still listed the old mode problem | `_mode_changed` calls the new `_show_chain_summary()` and then `_show_chain_test()` (`ui/panels/vpn_privacy.py:424-430`); `_refresh_chain` calls both (`:440-441`) | Yes. A probe over add, add, move, mode, remove and add Tor compared the summary and the button with `probe_problems()` after every step; all matched. | `test_a_mode_change_refreshes_the_summary` (`tests/test_vpn_privacy_tab.py:773`) kills N1, N2, N3 |
| P3-2: one bad field dropped the whole saved chain silently, and the next edit overwrote the file | `load_chain_checked()` (`proxychain.py:139-170`) moves a file it cannot parse to `proxychain.json.unreadable[-N]` and returns a reason; the tab shows it (`vpn_privacy.py:50`, `:53-54`) | Yes for files that can be opened. Not for a file that cannot be opened or moved (P3-2 below). The notice can be overwritten before it is seen (P3-3). | `test_an_unreadable_saved_chain_survives_the_first_edit` (`:784`), `…keeps_every_unreadable_copy`, `…names_what_is_wrong`, plus the status assertion in the malformed-chain tests; together they kill N4-N7, N10, N11, N13-N15, N18 |
| P3-3: a non-UTF-8 file raised in `PrivacyTab()` | `load_chain` catches `ValueError` (`proxychain.py:135`); `load_chain_checked` names the UTF-8 problem (`:155-156`) | Yes for UTF-8. A different exception class still escapes both loaders (P3-1 below). | the `\xff` case of `test_a_wrongly_typed_saved_chain_loads_as_empty` kills N12 |
| P3-4: three branches were not pinned | Test chain pressed with `proxy_dns=False`; `probe()` refuses a kind "DNS" hop with `socket.create_connection` patched to fail the test | Yes. Previous M7/M16/M19 are now N2/N19/N20, all killed. | `test_a_mode_change_refreshes_the_summary`, `test_a_chain_without_proxychains_dns_can_still_be_tested`, `test_probe_refuses_a_dns_kind_without_opening_a_socket` |

## Security and behaviour

| Concern | Evidence | Result |
|---|---|---|
| Mode of the moved file | `save_chain` writes through `write_private` (mkstemp, `fchmod 0600`, `os.replace`; `server/paths.py:169-193`). `rename` keeps the inode and its mode. Probe: a saved file corrupted in place is 0o600, and its `.unreadable` copy is 0o600. | Kept for files Sentinel wrote. A hand-edited 0644 file stays 0644 (P3-4). |
| Can the reason quote file contents or a password? | The `TypeError` texts are fixed strings (`socks_client.py:85`, `:91`; `proxychain.py:108`, `:111`). Probe with `["secret"]`, `{"a":"secret"}`, `"secret"` and `{"password":5}`: only field names. `UnicodeDecodeError` text (which quotes the byte and position), `JSONDecodeError` text and other `ValueError`s are replaced by fixed text. `OSError` gives `strerror` only. Nothing is re-raised or chained. | No content in the reason. The only open-ended part is `str(exc)` for `TypeError`, safe today because every reachable `TypeError` is a fixed string. |
| Is the status text stored anywhere? | `_say` only calls `setText(text[:600])` (`vpn_gate.py:28-29`); no logging in `vpn_privacy.py` or `vpn_gate.py` | Shown only, never stored |
| Message length | Probe: worst case with 119 existing copies is 156 characters; the longest reason (move failed) is about 85 | Well inside 600 |
| Symlink at `proxychain.json` | Probe: pointing outside the state dir at a bad file, the link itself is renamed; the outside file is untouched | Safe |
| Symlink at `.unreadable` | Probe: a link to an existing outside file makes `exists()` true, so `-2` is used; a dangling link is replaced in our own directory and nothing is created outside it. `rename(2)` follows neither side. | Safe |
| Counter loop | Ends after at most one iteration per existing `.unreadable*` entry; a directory at the name is skipped (probe). A TOCTOU window between `exists()` and `replace()` needs another process writing into the owner's 0700 state dir. | Bounded; not a finding |
| Deep nesting | Probe: `{"hops": [[[…]]]}` about 1000 levels deep (about 2 KB) raises `RecursionError` from `json.loads` in both loaders | Escapes (P3-1) |
| Permission denied on read | Probe: after `chmod 000` the message is "it could not be opened (Permission denied), so it will be replaced when you edit the chain"; the file is not moved, and the next `save_chain` replaced it with no copy | Data-loss path with a warning (P3-2) |
| Notice visible after the first show? | Probe: still there after `show()` with a successful listing. With a failed listing it becomes "Could not list network interfaces: …"; with `macaddr.recorded()` true it becomes "Not run: confirmation declined." (or the restore result) | Lost in two start-up cases (P3-3) |
| Other callers of `load_chain()` | grep: no production caller left; only tests | None to migrate |
| Emergency Reset | `erase_portable_user_data` deletes every child of the data dir (`services/portable_reset.py:57-64`), `.unreadable` copies included | Covered |
| Thread safety | One small local file read on the UI thread at construction; no `QThread`, lock or network access added | No change |

## Accuracy

- **Commit message.** Suite counts match (they leave out the 1 deselected). "Without quoting the file" is true. "No copy is overwritten" is true apart from the TOCTOU window and a dangling link, neither of which is a copy. "Moves an unreadable file aside … before any edit can save over it" overclaims: a file that cannot be opened, or cannot be moved, is not moved and is replaced on the next edit (the status message says so; the commit message does not; P3-2). "10/10 hand mutants" are not named, so not verified; this review's 20 left 4 survivors.
- **TODO.md:139.** Same claims, same overclaim. Correctly says "not yet independently reviewed"; should now cite this report.
- **Docstring at `proxychain.py:143-145`.** "Keeping its owner-only mode" is true only if the file was owner-only to begin with (P3-4).

## Mutant results

Runner: the 4 targeted files, each mutant restored by `git checkout --` before the next.

| # | Mutant | Result | Killed by |
|---|---|---|---|
| N1 | `_mode_changed` drops `_show_chain_summary()` | **Killed** | `test_a_mode_change_refreshes_the_summary` |
| N2 | `_mode_changed` drops `_show_chain_test()` | **Killed** | same |
| N3 | `_refresh_chain` drops `_show_chain_summary()` | **Killed** | same, plus `test_a_chain_with_a_bad_hop_keeps_test_chain_off` |
| N4 | the tab uses `load_chain()` with an empty error | **Killed** | 4 malformed-chain cases and the survive test |
| N5 | the chain error is never shown | **Killed** | same |
| N6 | no move (`path.replace` removed) | **Killed** | 11 tests |
| N7 | no counter loop (always `.unreadable`) | **Killed** | `test_load_chain_checked_keeps_every_unreadable_copy` |
| N8 | `number += 1` removed | **Survived**. Real gap: the third bad copy hangs `load_chain_checked` forever (probe, killed after 8 s) | none (P3-5) |
| N9 | `except OSError` narrowed to `FileNotFoundError` (a `PermissionError` escapes, the tab does not build) | **Survived** | none (P3-5) |
| N10 | `TypeError` reason replaced by a generic text | **Killed** | `…names_what_is_wrong[port]` |
| N11 | `UnicodeDecodeError` clause removed | **Killed** | `…names_what_is_wrong[\xff]` |
| N12 | `load_chain` back to `JSONDecodeError` | **Killed** | `…loads_as_empty[\xff]` |
| N13 | copy instead of move | **Killed** | 9 tests |
| N14 | message leaves out the aside's name | **Killed** | 10 tests |
| N15 | `JSONDecodeError` clause removed | **Killed** | 2 tests |
| N16 | `OSError` reason uses `str(exc)` (full path in the status line) | Survived, low value: a path is not file content or a secret | none |
| N17 | move-failure `except OSError` narrowed (a failed rename escapes, the tab does not build) | **Survived** | none (P3-5) |
| N18 | first copy named `-2` | **Killed** | 11 tests |
| N19 | `test_chain` uses `problems()` | **Killed** | `test_a_chain_without_proxychains_dns_can_still_be_tested` |
| N20 | `probe()` back to the `"DNS" not in p` text filter | **Killed** | `test_probe_refuses_a_dns_kind_without_opening_a_socket` |

## Findings

### P3-1 (already on `main`, a residual of the fixed P3-3): deeply nested JSON still stops the Tunnel panel from being built
`agents/vpn_agent/services/proxychain.py:133-136` and `:150-160`. `json.loads` raises `RecursionError` (a `RuntimeError`, not a `ValueError`) at about 1000 levels of nesting. Neither loader catches it, so it reaches `PrivacyTab.__init__` (`ui/panels/vpn_privacy.py:50`) and then `ui/panels/vpn.py:382`, which has no guard. Probe: a 2 KB `{"hops": [[[…]]]}` makes both loaders raise; the Privacy tab, and with it the VPN panel, fails to build. Same on the parent and on `main`.

Suggestion: catch `RecursionError` in both loaders; in `load_chain_checked` use a fixed reason such as "it is nested too deeply" and fall through to the move-aside. Add the case to `test_a_wrongly_typed_saved_chain_loads_as_empty` and `…names_what_is_wrong`.

### P3-2: a chain that cannot be opened, or cannot be moved, is still overwritten by the first edit
`proxychain.py:152-154` (the `OSError` branch returns without moving) and `:166-169` (a failed move), with `save_chain` at `:173-177`.

Scenario: `proxychain.json` holds hops with passwords and is mode 000 (a `chmod`, a backup restore, or owned by another user). The tab says "it could not be opened (Permission denied), so it will be replaced when you edit the chain". The user picks a mode and `write_private`'s `os.replace` swaps in an empty chain; the original is gone with no copy (probe). A rename needs write permission on the directory, not read permission on the file, so in the usual 0700 state dir this file could have been moved aside like the others. It is the same credential loss the previous P3-2 set out to close, and P3-3 can hide the warning.

Suggestion: on `OSError`, also try the move-aside (same counter, reason with `strerror`), and keep "will be replaced when you edit" only for when the move itself fails. Add tests that patch `Path.read_text` to raise `PermissionError` and `Path.replace` to raise `OSError`; these also kill N9 and N17.

### P3-3: the only notice of the moved file can be overwritten before the user sees it
`ui/panels/vpn_privacy.py:53-54` (the notice goes to the shared status line), `:57-61` (`offer_recorded_restore` on `QTimer.singleShot(0)`), `ui/panels/vpn_gate.py:47`, `:49` (that offer overwrites the status), and `vpn_privacy.py:233-240` (a failed listing on first show overwrites it).

Scenario: Sentinel crashed with a changed MAC address recorded, and `proxychain.json` was corrupted. At start the restore offer appears; whether declined or approved, the status line now reads "Not run: confirmation declined." or the restore result (probe). Same when `networksetup` fails on the first show. The chain then shows as empty, and nothing says a copy holding proxy passwords was moved to `proxychain.json.unreadable`. No data is lost, but the user is never told where their chain went. Rare: it needs two faults at once.

Suggestion: also put the notice somewhere that keeps it (a line in `chain_summary_label` until the next chain edit, or a one-time `QMessageBox.warning` posted after construction), and name the folder as well as the file. Add a test that shows the tab with `macaddr.recorded()` true and checks the notice is still visible.

### P3-4: a moved-aside copy keeps whatever mode the hand edit gave it, and the docstring says owner-only
`proxychain.py:143-145` (docstring) and `:167` (the `replace`).

Scenario: an editor that saves by writing a new file (umask 022, so 0644) breaks a field. Probe: the original and its `.unreadable` copy are both 0o644 and hold the passwords. Before this commit the next save replaced that file with a 0600 one; now the 0644 copy stays. In normal use the impact is small, because `state_dir()` hardens the vpn directory to 0700 (`server/paths.py:65-68`), but with a `VPN_AGENT_STATE_DIR` override and no save yet the directory is not hardened (the probe directory stayed 0o755).

Suggestion: after the move, `lstat` the copy and, if it is a regular file and not a link, `os.chmod(aside, 0o600)`. Correct the docstring. Add a test that writes a 0644 bad file and asserts the copy is 0o600.

### P3-5: three branches of the new loader are not pinned
- **N8.** If the counter stopped incrementing, the third bad copy would hang `load_chain_checked` forever inside `PrivacyTab.__init__` on the UI thread. The copies test writes only two copies.
- **N9 and N17.** Neither `OSError` branch has a test.

Suggestion: extend the copies test to three files and assert `-3`; add the two `OSError` tests from P3-2.

### P3-6: no CHANGELOG entry for a user-visible change
`CHANGELOG.md` `## Unreleased` has nothing for this branch: not the status-line notice, not the new `proxychain.json.unreadable` file, not 729fd02's Test chain changes. `git log -- CHANGELOG.md` shows nothing since `ddfb09d`. AGENTS.md's definition of done requires an entry.

Suggestion: one line under Unreleased (an unreadable saved proxy chain is kept as `proxychain.json.unreadable` in the vpn data folder and the Privacy tab says so; Test chain probes the hops you confirmed). Point TODO.md:139 at this report.

### Not a finding
- **The reason text is free of contents.** `str(exc)` for `TypeError` relies on every `TypeError` keeping a fixed message; a fixed lookup would keep that promise without depending on it.
- **Symlinks and the counter loop.** `rename` follows no link; the dangling-link case only removes our own dangling link; the loop is bounded.
- **`load_chain()` has no production caller now.** Harmless; it can stay as the quiet loader for tests.
- **Not verified:** the commit's own "10/10" mutant set; a file really owned by another user (only simulated with `chmod 000`); a state dir on an external or encrypted volume; the cause of the 3 ordering-dependent `test_tunnel_keeps_no_logs.py` setup errors from earlier reviews (the 18-file VPN set was not run this time).

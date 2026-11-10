# Independent review: audit tier-2 S8b (OpenVPN config and data files copied, hashed, `--cd`)

**Scope:** commit `bd6b27f` (`git diff bd6b27f~1 bd6b27f`) in worktree `intelligent-archimedes-e15a0e`: `services/vpn_execution.py`, `services/vpn_rootscript.py`, `services/openvpn_manager.py`, `services/vpn_connection.py` and the tests that go with them. The spec is ADR 0004 §1, §3 and §7 plus spec §1.3, §14 and §16 (read at `bd6b27f`). I read the uncommitted §17 draft in the working tree only to check the author's claims.
**Reviewer:** code-reviewer, read-only. I edited nothing except this report and ran nothing as root. I read all code from the commit, never from the working tree, because a mutation check was rewriting it.
**Not run:** pytest (as instructed). I ran only my own read-only probes in the scratchpad:
- bash 3.2 checks of `set -u` with an empty `"$@"` and of the `shift $(( $# < 8 ? $# : 8 ))` line;
- the commit's `_openvpn_includes` / `_hook_findings`, called through a `git archive` extract on configs in a temp folder (below "probe");
- a `[ ! -e ]` collision check.
**Playbook:** `docs/playbooks/code-reviewer.md`: `status: current`, `last-reviewed: 2026-10-14` (22 days old, so not stale).
**App context:** `docs/company/app-context.md` is untracked (c4ac6a3), so I read the copy in the main checkout.

## Verdict

**No P0. One P1 (it predates this commit and sits outside the diff, but it defeats the S8b goal), two P2s and six P3s.**

The S8b machinery itself is sound:
- Values travel as argv to a constant script, under `env -i`, and every tool is named by absolute path.
- Both the builder and root validate the hash. Root re-checks every manifest path on `/`+path, which also catches `..`, `.`, `//`, a trailing `/` and an empty path.
- Copies go only into a freshly created root `0700` `private/cfg`, and `mkdir -p` runs only below it. A copy is kept only if both its size and its hash match. Any mismatch removes `private/` and exits 98 before openvpn runs.
- `--cd` comes before `--config`, and the config copy sits outside `cfg/`, so a data file cannot collide with it.
- The pair-rotation loops are correct in bash 3.2 under `set -u`: `set -- "$@" p h` with an empty `$@` works (measured), and `$#` stays constant across iterations. Argument shifting with fewer than 8 values leaves `want` empty, so Up refuses with 99 and Down accepts.
- I found no path through the S8b code where a same-user process makes root write outside the run directory, or makes openvpn read unreviewed bytes for a file that is **in** the manifest (relative files: copy plus hash; absolute files: hash plus the accepted residual D2).

The problems are about **what is not in the manifest**:
- Three directives that load shared libraries (`providers`, `engine`, `pkcs11-providers`) let root run code from a user-writable path with no hash, and two of them draw no warning at all (P1-1).
- `<connection>` blocks, `socks-proxy`'s auth file and `capath` are never scanned for files (P2-1).
- `replay-persist` still lets root write anywhere (P2-2).

The P3s cover:
- case-insensitive duplicates that pass the review and fail at root;
- the file-count check made only after reading every file;
- concurrent Connects sharing one copy path;
- full hashes of password files in the dialog;
- an ADR "cannot hang" claim;
- doc edits not in the commit.

## Findings

### P1-1: Library-loading directives give root unreviewed code, with no hash and (for two of them) no warning
`services/vpn_execution.py:88-90` (`OPENVPN_EXECUTABLE_KEYS`), `:93-118` (`OPENVPN_KNOWN_KEYS` contains `engine` at :111 and `providers` at :113), `:458-460` (only unknown keys are warned about; `_program_findings` runs only for hook keys). This predates the S8b diff, but S8b's goal is "root runs only the reviewed OpenVPN files", and spec §1.3's executables bullet ("`plugin` ... BLOCKER unless it is root-owned") covers this class.

Evidence (probe, commit code): every config is `client` plus the line shown.
- `providers /Users/x/evil.dylib`: manifest `[]`, blockers `[]`, warnings `[]`.
- `engine /Users/x/evil.dylib`: manifest `[]`, blockers `[]`, warnings `[]`.
- `pkcs11-providers /Users/x/evil.dylib`: manifest `[]`, blockers `[]`, plus only the generic "directives not on the reviewed list" warning.

On the OpenVPN side (from the OpenVPN 2.6 manual and OpenSSL 3 docs; **not run here**, because openvpn is not installed on this Mac):
- `--pkcs11-providers` takes "provider library names" and dlopens them.
- `--providers` calls `OSSL_PROVIDER_load`, which accepts "a path to a provider module".
- `--engine` falls back to OpenSSL's dynamic engine with `SO_PATH` = the name. This is from my memory of `crypto_openssl.c` `try_load_engine` and is the least certain of the three.

Failure scenario: a profile carries `providers legacy /Users/me/vpn/legacy.dylib`, which looks like the common `providers legacy default`. The review shows no warning, and the dialog lists no file. After the user confirms, a same-user process replaces the dylib, and root's openvpn loads it, which is arbitrary code as root. Residual R1 (Homebrew's user-writable openvpn) gives the same power, but only in a Homebrew install, and it is disclosed by `_writable_tool_warnings`. With a root-owned openvpn, this is the remaining undisclosed route.

Suggested fix:
- Give these keys a dedicated check in `_hook_findings`:
  - `providers` and `engine`: refuse any word containing `/` (keep bare names such as `legacy default`), or refuse `engine` outright.
  - `pkcs11-providers`: blocker unless every word passes `_unsafe_executable_reason` (root-owned, not group/other-writable).
- Remove `providers`/`engine` from the "reviewed" known list until that check exists.
- Tests: one per key, for a path value and for a bare-name value.

### P2-1: The manifest misses files openvpn reads (`<connection>` blocks, `socks-proxy` auth file, `capath`)
`services/vpn_execution.py:226-243` (`_scan` treats any `<tag>` line as an opaque inline block and skips every line up to `</tag>`), `:122-126` (`OPENVPN_FILE_KEYS`: no `socks-proxy`, no `capath`), `:471-505`.

Evidence (probe, commit code):
- `<connection>\nremote … 1194\nhttp-proxy proxy.example 8080 auth.txt\ntls-crypt tc.key\n</connection>`: manifest `[]`, blockers `[]`, warnings `[]`.
- `socks-proxy 127.0.0.1 1080 auth.txt`: manifest `[]`, no warning (`socks-proxy` is a known key).
- `capath certs`: manifest `[]`, generic unknown-directive warning only.

OpenVPN parses a `<connection>` block's contents as directives. Per the manual, `http-proxy` and `socks-proxy` are allowed there, and (2.5+, not verified here) so are `tls-auth`, `tls-crypt` and `tls-crypt-v2`. `socks-proxy server port authfile` reads an auth file at word index 2, the same position as `http-proxy`.

Failure scenarios:
- **Relative reference:** root never copies the file, so openvpn under `--cd private/cfg` cannot find it. Connect fails after the password prompt, with a review that said nothing.
- **Absolute reference:** openvpn reads it from the user-writable location with no hash, no listing and no D2 warning.

Either way the dialog's heading "Files the config reads (root copies those … and checks each hash …)" (`:686-688`) and the commit message ("lists every data file the .ovpn reads") over-claim.

Suggested fix:
- Have `_scan` treat `<connection>` as a directive container: emit its lines as directives (with a marker), so file keys, hooks and blockers apply inside it.
- Add `"socks-proxy": 2` to `OPENVPN_FILE_KEYS`.
- Make `capath` a blocker, because directories are not copied, like `crl-verify … dir`.
- Tests: an `http-proxy` auth file and a `tls-crypt` key inside `<connection>`, the `socks-proxy` auth file, and `capath`.

### P2-2: `replay-persist` still lets root write any file
`services/vpn_execution.py:85-86` (`OPENVPN_BLOCKER_KEYS`), `:458-467`. This predates the diff, but the review brief asks about "write outside the run dir".

Evidence: the probe of `replay-persist /etc/x` gives blockers `[]` and only the generic unknown-directive warning. Per the OpenVPN manual it "persist[s] replay-protection state across sessions using file to save and reload the state". My reading of the source is that it opens the file `O_CREAT|O_RDWR` and rewrites it (**not verified** here).

Failure scenario: a profile with `replay-persist /etc/pf.conf` or `/private/etc/sudoers.d/x` makes root create or overwrite that file with binary packet-id state. Under the new `--cd`, a relative value lands harmlessly in `cfg/`, but an absolute one does not. This is the same class as `status`, `writepid` and `log`, which are blockers ("make root write … somewhere the review cannot see", `:82-84`).

Suggested fix: add `replay-persist` to `OPENVPN_BLOCKER_KEYS`, with a test. Owner decision D4 keeps unknown directives as warnings, so say in spec §1.3 that this one is promoted, and why.

### P3-1: Case-variant duplicates pass the review, then root refuses after the password
`services/vpn_execution.py:503` (deduplicates by exact string); `services/vpn_rootscript.py:405-420` (`_includes`, exact-string `seen`); OVPN_SCRIPT up `[ ! -e "$cfgd/$p" ] || unverified "$p is listed twice …"` (`vpn_rootscript.py:252`).

Evidence: in a scratch folder (APFS, case-insensitive like the default Data volume that holds `/private/var/run`), the commit's `_openvpn_includes` on `ca ca.crt\nextra-certs CA.crt\n` returns manifest `['ca.crt', 'CA.crt']`, both the same file, with no blocker. The bash check `[ ! -e "$cfgd/CA.crt" ]` after copying `ca.crt` reports it as existing. The same applies to NFC/NFD spellings of one name.

Failure scenario: a profile that spells one file two ways shows green and asks for the password. Root then fails with exit 98 and "CA.crt is listed twice", which the user cannot match to anything in their config. It fails closed, so this is a usability issue, not a security one.

Suggested fix: in the review, deduplicate by `(st_dev, st_ino)` of the opened file, or block with "named twice with different spelling; use one". Add a test.

### P3-2: The 16-file limit is checked only after every named file has been read
`services/vpn_execution.py:508-544`. The loop opens and hashes up to 1 MiB of **every** distinct name, and only then tests `len(names) > MAX_INCLUDES` (`:542`).

Failure scenario: a 256 KiB config can name thousands of distinct paths, for example hard links to one 1 MiB file. The review hashes all of them (gigabytes) before blocking, which stalls the review that runs when the dialog opens, and again in the worker. It is self-inflicted and fails closed, but it is avoidable.

Suggested fix: check `len(names) > MAX_INCLUDES` before the read loop, and return the blocker straight away.

### P3-3: Two concurrent OpenVPN Connects share one copy path with no lock
OVPN_SCRIPT up (`vpn_rootscript.py:226-270`): the 96 check, then `forget`, then copying into the fixed `$copy` and `$cfgd`, then `"$ovpn" … --config "$copy"`.

Failure scenario: two approved Connects (two dialogs) both pass the 96 check before either openvpn has written its pid.
- B's `forget` deletes A's `private/` and pid file, then B re-copies.
- A's openvpn opens `$copy` while B's `head … > "$copy"` is mid-write, so it reads B's (prefix of a) reviewed config, or nothing.
- Or both daemons start, and A's is untracked because B's `forget` removed A's pid.

Spec §14.2 already defers this class for WireGuard (review P2-6). S8b widens it to the config copy. I did **not verify** whether the UI can issue two OpenVPN Connects at once.

Suggested fix: take an atomic `mkdir "$od/lock"` for the duration of Up (refuse with 96 if it is held, remove it on exit), or copy into a per-run staging directory and `mv` it into place. At minimum, record the OpenVPN case under P2-6.

### P3-4: Full SHA-256 of password files is shown in the dialog and the Execution tab
`services/vpn_execution.py:690-691` (`_include_lines`), `:677-678`, `:735-738`; `ui/panels/vpn.py:744`.

Failure scenario: `auth-user-pass creds.txt`, `askpass` and `http-proxy-user-pass` files typically hold `user\npassword\n`. An unsalted SHA-256 of that is guessable offline for a weak password, and the full digest is displayed and copyable. (The config hash is less of a concern: a WireGuard key or inline PEM has high entropy.) I did not find it persisted ("Tunnel keeps no log of this attempt."), so the exposure is screen and clipboard only.

Suggested fix: show a short prefix (for example the first 12 hex characters) in the dialog and `sections()`, and keep the full hash in the fingerprint and the argv. Or show the prefix for these three directives only.

### P3-5: ADR §3 says a root read "can neither hang on a pipe"; a swap between `[ -f ]` and `head` still can
`docs/adr/0004-sentinel-vpn-run-dir.md` §3 step 3; `vpn_rootscript.py:238-239` (`take`) and `:262-264` (absolute check). The same gap exists in S7's `verified_stage`.

Failure scenario: after `[ -f "$1" ]` succeeds, a same-user process replaces the file with a FIFO, so root's `head` blocks in `open()` with no writer. The dialog's command timeout expires and Sentinel reports "may still have happened". If the attacker later writes the reviewed bytes into the FIFO, the hash passes and openvpn starts after Sentinel has finished reporting. The bytes are still the reviewed ones, so this is not a privilege issue, but the ADR sentence is not backed by the code. S8b raises the number of such reads from 1 to up to 17.

Suggested fix: reword the ADR ("`[ -f ]` refuses a FIFO that is there at check time; a swap afterwards can only delay root, and the hash still decides"). Or bound each read (background `head`, `kill` after N seconds).

### P3-6: The commit's docs contradict its code, and the fixes are uncommitted
At `bd6b27f`:
- `docs/adr/0004-sentinel-vpn-run-dir.md:48` places the config copy inside `cfg/`, but the code uses `private/config.ovpn` (`vpn_rootscript.py:182`).
- `docs/agents/vpn.md:48` still says "OpenVPN still reads your `.ovpn` directly."
- ADR "Consequences" requires a CHANGELOG entry for the change in how relative references resolve, and there is none.

The working tree holds edits that fix all three (ADR amendment, `vpn.md`, a CHANGELOG "Behaviour change" line, spec §17), but they are not in the reviewed commit.

Suggested fix: commit them with the S8b follow-up. When P1-1 and P2-1 land, §17's file list and "Limits" need the same updates.

## Test coverage notes (no separate finding)
- The new tests pin the builder, root's own rejection of every non-plain path, size caps, FIFO, directory and missing files, relative and absolute changes after review, clean-up on failure and on Disconnect, `--cd` before `--config`, the user's folder being absent from openvpn's argv, the manifest inside `fingerprint()`, and `execute()` handing over the confirmed hash and manifest. They are good.
- No pre-existing test was weakened. The edits only add the new arguments. `test_vpn_execution.py:332` now accepts `**kwargs` instead of naming `run_argv_as_root`, which is looser but not about S8b behaviour. Two assertions were inverted to match the new truth (`test_the_hash_line_says_when_it_was_checked`, `test_the_module_and_execute_docstrings_do_not_overclaim`).
- Gaps, which match the findings above: no test for `<connection>` contents, `socks-proxy` or `capath` (P2-1); none for case-variant names (P3-1); and no root-side test of an **absolute** path with `..` (the `/`+path check covers it by construction).

## Not verified
- All OpenVPN-side behaviour cited above: `providers`/`engine`/`pkcs11-providers` loading, `<connection>` allowed directives, `replay-persist` opening with `O_CREAT`, and `--cd` ordering (ADR R3). openvpn is not installed on this Mac, so I relied on the manual and source as I remember them. Check against `man openvpn` before fixing.
- Whether the panel can start two OpenVPN Connects at once (P3-3).
- The test suite itself (not run, as instructed). The mutation check is the author's (spec §17 "Round 1").

## Resolution

| Finding | Resolution | Commit |
|---|---|---|
| P1-1 | Fixed: `providers`/`engine`/`pkcs11-providers` are administrator-run lines (fingerprint); `_library_findings` blocks a non-root-owned or relative library path; bare names warn. Tests: `test_a_library_in_a_user_folder_is_a_blocker`, `test_a_root_owned_library_and_bare_provider_names_are_listed_not_blocked`, `test_a_relative_library_path_is_a_blocker`. | 04ad750 |
| P2-1 | Fixed: `_scan` reads `<connection>` lines as directives (nested inline block blocks); `socks-proxy` (word 2) and `capath` added to `OPENVPN_FILE_KEYS` (capath blocks as not a regular file). Tests: connection files and hook, nested block, socks-proxy, capath. | 04ad750 |
| P2-2 | Fixed: `replay-persist` in `OPENVPN_BLOCKER_KEYS`; spec §1.3 records the promotion out of D4. Test: `test_replay_persist_is_a_blocker`. | 04ad750 |
| P3-1 | Fixed: names differing only in case block at review. Test: `test_two_spellings_of_one_file_name_are_a_blocker`. | 04ad750 |
| P3-2 | Fixed: count checked before any file is opened. Test: `test_too_many_files_are_refused_before_any_is_read`. | 04ad750 |
| P3-3 | Deferred into backlog P2-6 (TODO.md), now naming OpenVPN's shared `ovpn/private/`; spec §17 Limits. | docs (this round) |
| P3-4 | Fixed: dialog and sections list paths only; hashes stay in the fingerprint and argv. Test: `test_the_dialog_and_sections_list_each_data_file_but_not_its_hash`. | 04ad750 |
| P3-5 | Fixed in docs: ADR §3 step 3 reworded (a later FIFO swap can only delay root; the hash decides). | docs (this round) |
| P3-6 | Fixed: ADR layout, `docs/agents/vpn.md`, CHANGELOG and spec §17 committed with this round. | docs (this round) |

Author's note: round-2 fixes are author-verified (tests, 14/14 mutants, full suite 3468 passed); they have not been re-reviewed independently.

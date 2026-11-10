# Re-review: audit tier-2 S8b security fixes (rounds 3 and 4)

**Scope:** commits `2c1ec81` (security round 3: P1-1, P1-2, P1-3, P2-1, P2-2) and `846f7a6` (P3-1, P3-3, P3-5) on `claude/intelligent-archimedes-e15a0e`, i.e. `git diff e7d5d4d~2..846f7a6 -- services tests`: `services/vpn_execution.py`, `services/vpn_rootscript.py` (comment only), `services/openvpn_manager.py`, `services/vpn_connection.py`, `tests/test_vpn_review_gate.py`, `tests/test_vpn_connection.py`. Also checked the claims the docs commits `e7d5d4d` and `03b2d57` make about them (CHANGELOG, ADR 0004 §3, spec §17 rounds 3-4, `docs/agents/vpn.md`, the Resolution table of `audit-tier2-s8b-security-2026-11-05.md`).
**Read against:** `audit-tier2-s8b-security-2026-11-05.md` (the findings being answered), `audit-tier2-s8b-review-2026-11-05.md`, ADR 0004 §3, spec §1.3 and §17, the Tunnel rules in `CLAUDE.md`.
**Reviewer:** code-reviewer, independent of the author, read-only. No privileged, root, tunnel, kill-switch, MAC or network action was taken; no `.env` was read; no code was changed. The only file written is this report.
**Playbook:** `docs/playbooks/code-reviewer.md`, `status: current`, `last-reviewed: 2026-10-14` (22 days old; not stale).
**App context:** `docs/company/app-context.md` is not present in this worktree (untracked in the main checkout), so I read the main checkout's copy.

## What was run
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_review_gate.py tests/test_vpn_execution.py tests/test_vpn_connection.py tests/test_vpn_rootscript.py`: **515 passed in 19.96 s**.
- Probes in the session scratchpad, importing the worktree code under `python -I`, on configs written to temp folders. They call only `_scan`, `_hook_findings`, `hooks_from_text`, `_openvpn_includes`, `_unsafe_executable_reason` and `review_execution` (with `openvpn_binary` stubbed and the test suite's `Probe` shape). Nothing reaches `execute` or a root runner.
- The same `_hook_findings` probes against the pre-fix tree (`git archive 6a4cfa3`, the parent of `2c1ec81`, extracted into the scratchpad) to tell regressions from pre-existing gaps.
- openvpn is not installed on this Mac. Every statement below about what OpenVPN does is from its source as I recall it and is marked NOT VERIFIED where it matters.

## Verdict

**No P0. Two P1, no P2, six P3.** The round-3 and round-4 fixes close the exact shapes the security review reported, and the tests pin those shapes. Two gaps remain in the same class (root runs a program the user can replace, with no blocker), and one of them was opened by these commits:

- A relative path in an OpenVPN hook is never checked, and under `--cd` a `../` path reaches any folder, so the "missing program blocks" and "copied file blocks" fixes do not cover hooks named relatively (R-P1-1, pre-existing but not closed).
- `2c1ec81`'s correct second split turns `sh -c '<shell line>'` into one opaque token, so a user-owned script inside it is no longer flagged. The pre-fix code flagged it (R-P1-2, regression).

What holds (checked by probe):
- BOM: stripped on line 1 only, also when followed by whitespace. A BOM on a later line, a second BOM, a zero-width character or a Cyrillic look-alike in a name all block.
- Quotes: `"a"b`, `a"b"`, `x'y'`, and the three probe lines from the security report all block. In a value, a zero-width character or a non-breaking space glued to the program makes the program "not exist" and blocks. An escaped quote inside a quoted value and a single-quoted path in the second split are both seen, and a user-owned path is blocked.
- New hooks and blockers: `dns-updown`, `tls-crypt-v2-verify` and `client-crresponse` with an absolute user path are soft blockers. `server`, `server-bridge`, `tls-server`, `--tls-server`, `mode "server"`, the five file-writing directives, and `dev-node` other than `utun[0-9]{0,3}` all block.
- P2-2: an absolute data file in a user-owned folder blocks.
- P3-1: a planted openvpn changes the fresh review's fingerprint, and root gets the reviewed path.
- P3-3: `x.sh`, `./x.sh`, `x.sh/`, `X.SH`, `.//x.sh` and `sub/../sub/y.sh` all block.
- P3-5: NFC and NFD spellings of one name block.
- No regressions for ordinary profiles: a typical provider profile passes, also with a BOM and with CRLF. It had `dev tun`, two `remote` lines (one quoted), quoted `verify-x509-name` (double and single quotes), `auth-user-pass auth.txt` and `ca ca.crt` beside the config, `providers legacy default` (warning only), three `pull-filter ignore "…"` lines, `dev-node utun`, two `<connection>` blocks and an inline `<tls-auth>`.
- WireGuard: the WireGuard branches of `_scan` and `_segments` are unchanged. The only shared edit is `Path(path).exists()` → `_exists(path)`, the same `stat` semantics through the test indirection.

## Findings

### R-P1-1: A relative program or interpreter argument in an OpenVPN hook is never checked, and `../` under `--cd` reaches any user folder
**Location:**
- `services/vpn_execution.py:395-400`: `_path_word` returns None for any word that does not start with `/` or `~`.
- `:475-481`: so the ownership check and the new "does not exist" blocker (P1-2) never see a relative word.
- `:668-686`: the P3-3 check matches only manifest names.
- `services/vpn_rootscript.py:269`: openvpn runs with `--cd "$cfgd"` (`/private/var/run/sentinel-vpn/ovpn/private/cfg`).

**Evidence:** `review_execution("connect", …)` on the current code (probe 5) returned `allowed= True`, `blockers= ()` for:
- `script-security 2` / `up "/bin/sh ../../../../../../../../Users/as/evil.sh"`

`_hook_findings` returned no blocker, soft blocker or warning for each of the following (probe 1):
- `up "../../../../../../../../Users/as/evil.sh"`
- `up "/usr/bin/env ../../../../../../../Users/as/evil"`
- `up evil.sh`
- `plugin ../../../../../../../Users/as/evil.dylib`
- `dns-updown ../../../../../../../Users/as/evil.sh`

The pre-fix tree (`6a4cfa3`) gives the same empty result for the first six, so this is pre-existing. It is not closed by:
- P1-2's "a missing OpenVPN hook program … blocks": only absolute paths reach that check.
- P1-3: `dns-updown` "with the ownership check". `disable`/`force` pass only because no relative value is checked.
- P3-3: only exact manifest names are matched.

`_library_findings` (`:510-512`) does block a relative library path containing `/` for `providers`/`engine`. `plugin` goes through `_program_findings` instead and does not.

**Failure scenario:** a same-user process adds the two lines above to the profile. The review shows `up: "/bin/sh ../../../../../../../../Users/as/evil.sh"` among the administrator lines, with no blocker and no "a normal user could replace it" message. The user confirms. Root's openvpn chdirs to `cfg/` (`--cd` is processed before `--config`; `--daemon` keeps that directory when `--cd` is given). The `..` components resolve to `/`, and root runs `/Users/as/evil.sh`, which the malware can rewrite at any time. That is the round-2 P1-1 / security P1-2(b) outcome by another spelling. The OpenVPN-side behaviour (`--cd` chdir at parse time, `daemon(nochdir=1)` with `--cd`, relative script paths resolved against the cwd, `plugin` relative names resolved against cwd or `PLUGIN_LIBDIR`) is NOT VERIFIED.

**Suggested fix:**
- In `_program_findings` for OpenVPN, the program word (after `VAR=` assignments) must be absolute. Otherwise raise a soft blocker: "name the program by its absolute path; under `--cd` a relative name resolves in root's copy folder or, through `..`, anywhere".
- Allow `dns-updown disable|force` by name, explicitly.
- For every other relative token that contains `/`, and for every relative token handed to a `_INTERPRETER` command, resolve `posixpath.normpath(posixpath.join(vpn_rootscript.ovpn_copy_dir(), token))`. Block if the result leaves `cfg/`. If it stays inside, apply the P3-3 rule. Blocking every relative interpreter argument outright is simpler and also fine.
- Give `plugin` the `_library_findings` rule for relative paths.
- Tests: the lines above. The pre-fix and post-fix trees both pass the current suite with this hole, so nothing pins it today.

### R-P1-2: Regression from `2c1ec81`: inline shell code (`sh -c '…'`) in an OpenVPN hook is no longer inspected
**Location:** `services/vpn_execution.py:383-390` (`_segments` re-splits each word with `_ovpn_tokens`, so the `-c` argument becomes one token) and `:395-400` (`_path_word` only looks at a token's start).

**Evidence:**

| Hook (`script-security 2` set) | `6a4cfa3` (before) | `846f7a6` (now) |
|---|---|---|
| `up "/bin/sh -c 'cd /Users/as && ./evil.sh'"` | soft: "runs /Users/as as administrator, and /Users/as is not owned by root" | **nothing** |
| `up "/bin/sh -c '. ~/x'"` | soft: "runs ~/x … in a user's home directory" | **nothing** |
| `up "/bin/sh -c /Users/as/evil.sh"` | soft | soft (unchanged) |

End to end, `review_execution` on the first line now returns `allowed= True`, `blockers= ()` (probe 5). The old code caught it by accident (`word.split()` broke the quoted script at spaces), and no test pinned it. The second split is right about OpenVPN (`argv_parse_cmd` reuses `parse_line`, so `'…'` is one argv: NOT VERIFIED). What is missing is that this one argv is itself a shell line.

**Failure scenario:** the malware writes `up "/bin/sh -c 'cd /Users/as/.cache && ./x'"`. The review lists it under the administrator lines with no blocker and no warning. After confirmation, root runs `./x` from a user-owned folder, which can be replaced at any time. The same shape works with `bash -lc`, `/usr/bin/env sh -c …` and `zsh -c`.

**Suggested fix:**
- When the command (after `env`/wrapper words) matches `_INTERPRETER`, also analyse every later token as a WireGuard-style shell line (`_SHELL_SPLIT`, whitespace split, `_path_word`), and take the union of both readings' findings. Over-approximating is the safe direction.
- Alternatively, raise a soft blocker for inline code (`-c`, `-e`, `-S`) in OpenVPN hooks, since it cannot be checked.
- Tests: the two lines in the table, so that a future tokeniser change cannot silently drop them again.

### R-P3-1: The P3-3 check is bypassed by any relative spelling that does not normalise to the manifest name
**Location:** `services/vpn_execution.py:668-686`.

**Evidence:** with `askpass x.sh` in the profile (so root copies `x.sh` into `cfg/`), each of these is `allowed= True` with no blocker (probes 2 and 5):
- `up "/bin/sh ../cfg/x.sh"`: `normpath` keeps the `..`, so it never equals `x.sh`, yet from `cfg/` it is the same copy.
- `up "/bin/sh ../config.ovpn"`: root's copy of the config itself (`$priv/config.ovpn`, `vpn_rootscript.py:181`), run as a shell script. It is never a manifest name.
- `up "/usr/bin/env -S 'sh x.sh'"`: one token, `sh x.sh`.

**Failure scenario:** root runs code that the review showed only as a file name (the security review's P3-3 case), so the Resolution table's "a hook word that names a file the profile copies … blocks" is true only for the literal forms. These files are hash-bound, so there is no swap after confirmation. That is why this is P3, as the original was.

**Suggested fix:** the R-P1-1 fix covers it: resolve against `ovpn_copy_dir()` and block anything inside `private/` that a hook names, the config copy included. Add `../cfg/x.sh` and `../config.ovpn` to `test_a_hook_that_runs_a_copied_file_is_a_blocker`.

### R-P3-2: "Root owns it all the way up" walks the lexical parents, not the real ones, so a symlinked folder hides a user-owned ancestor
**Location:** `services/vpn_execution.py:421-435` (`for candidate in (path, *path.parents)`, with `os.stat` following links per call). It is used for hook programs (`:475`), libraries (`:505`) and, since P2-2, absolute data files (`:642`). `_openvpn_includes` refuses only a symlink in the **final** component (`:629-632`, `lstat`).

**Evidence:** probe 4 builds a tree where only `home_dir` is user-owned (fake `_stat` keyed on the real path):
- `rootdir/link` → `home_dir/sub`, where `sub` and `sub/prog` are root-owned.
- `_unsafe_executable_reason(rootdir/link/prog)` returns **None**, after statting `rootdir/link/prog`, `rootdir/link`, `rootdir`, … `/`. It never stats `home_dir`.
- The same file by its real path returns "home_dir is not owned by root".
- A hook program that is itself such a link (`rootdir/plink` → `home_dir/sub/prog`) also returns None.

**Failure scenario:** the owner once linked a root-owned folder (`/etc/openvpn` → `~/vpn/keys`, where `keys` is root-owned but `~/vpn` is not). A profile names `/etc/openvpn/auth.txt` or a hook under it, and the review accepts it. After root's in-place hash check, the malware renames `~/vpn/keys` and puts its own folder there. openvpn then reads, or runs, the user's file. That is the D2 swap P2-2 was meant to close. The precondition (a root-owned link into a user-owned tree) cannot be created by the attacker, hence P3. The in-app warning (`:694-696`, "Root owns them, so your user cannot change them"), ADR 0004 §3 and the CHANGELOG state the guarantee without this caveat.

**Suggested fix:** walk `Path(os.path.realpath(path))` and its parents (and keep the lexical walk too, or refuse a path whose `realpath` differs from its `normpath`). Test with a fake `_stat` like probe 4.

### R-P3-3: Two server and path shapes outside the new lists (NOT VERIFIED against OpenVPN)
**Location:** `services/vpn_execution.py:92` (`OPENVPN_SERVER_KEYS`), `:549-552` (only `dev-node` is constrained); `dev` is in `OPENVPN_KNOWN_KEYS`.

**Evidence:**
- `server-ipv6 fd00::/64` (with `client`) gives only the generic "not on the reviewed list" warning. In 2.4+ `server-ipv6` without `server` sets server mode by itself (helper.c, from memory).
- `dev-type tap` / `dev ../../Users/as/x` gives no blocker and no warning at all (probe 1).
- On macOS a TAP device goes through `open_tun_generic`, which opens `/dev/<dev>` read-write as root when the name has a digit. So `dev-type tap`, `dev ../private/etc/<file>1`, plus the two `-noexec` lines, is the `dev-node` case of P2-1 by another directive (from memory).

**Failure scenario:** as in security P2-1 (root opens an existing file read-write and streams it to the server, or writes packets into it), and a server profile passing the "Tunnel runs only client profiles" rule.

**Suggested fix:**
- Add `server-ipv6` to `OPENVPN_SERVER_KEYS`.
- Allow `dev` only as `(tun|tap|utun)[0-9]{0,3}`, and block anything else, like `dev-node`.

### R-P3-4: `_ovpn_tokens` still differs from OpenVPN's `parse_line` on backslashes, and from `argv_parse_cmd`'s fallback
**Location:** `services/vpn_execution.py:178-223` (docstring: "Split a line the way OpenVPN does").

**Evidence:** these differences are from the source as I recall it, NOT VERIFIED:
- OpenVPN tests for `#`/`;` before it looks at a pending backslash, so `\#x` at a token's start begins a comment. Sentinel keeps it as the token `#x` (probe: `up /usr/bin/true \#x` gives Sentinel words `['/usr/bin/true', '#x']`).
- OpenVPN rejects any backslash escape other than `\\`, `\"` and backslash-space ("Bad backslash usage") and drops or fails the line. Sentinel accepts `\q` as `q`.
- In `argv_parse_cmd`, a failed second parse makes OpenVPN run the **whole string** as `argv[0]`. Sentinel instead sees tokens, for example `up "/bin/sh \q/Users/as/e.sh"` → Sentinel: `/bin/sh`, `q/Users/as/e.sh`; OpenVPN: the one path `/bin/sh \q/Users/as/e.sh`.

**Failure scenario:** I found no exploit beyond R-P1-1. The extra Sentinel tokens over-report, and the whole-string `argv[0]` stays under the same root-owned folder as Sentinel's token 0, so it does not exist and fails closed. But the docstring's claim is not true, and the next fix that relies on it could be wrong.

**Suggested fix:** return None (a blocker) for any backslash not followed by `\`, `"` or whitespace, and for a backslash directly before `#`/`;` at a token's start. Then both parsers either agree or refuse.

### R-P3-5: P3-1 leaves a second lookup path for the openvpn binary
**Location:** `services/openvpn_manager.py:246` (`openvpn or openvpn_binary() or ""`), `services/vpn_execution.py:1106` and `:1114` (the review resolves `program` with one lookup and decides "installed" with a second, `is_openvpn_available()`).

**Evidence:** if the first lookup returns None and the second finds a binary, the review is allowed with `program = ""`, and `connect` falls back to a fresh lookup after the password. The only caller is `execute` (`git grep`), and both the confirmed and the fresh review would have to hit that window, so this is theoretical. But it contradicts "root runs this path and no other" (`:781-783`, CHANGELOG "runs exactly that one").

**Suggested fix:** in the review, block on `not program` instead of calling `is_openvpn_available()`. In `openvpn_manager.connect`, refuse an empty `openvpn` ("Nothing was run") instead of looking it up.

### R-P3-6: Docs and the Resolution table claim more than the code does
**Location and evidence:**
- `services/vpn_execution.py:27-31` (module docstring) still says "residual D2: a swap after the check can change a certificate, not a command" (superseded by P2-2) and "only programs that root owns are accepted" (false per R-P1-1 and R-P1-2).
- Security report Resolution table:
  - P1-2: "a missing OpenVPN hook program or library blocks". Only absolute ones do.
  - P1-3: "`disable`/`force` pass". They pass because no relative value is checked, not by an allow-list.
  - P3-3: the literal forms only (R-P3-1).
- `docs/agents/vpn.md:48` ("a hook program or library that does not exist" is refused) and the CHANGELOG ("a hook that would run one of the profile's copied files is refused") carry the same over-claims.
- The in-app warning `:694-696`, ADR 0004 §3 and spec §17 "Limits" (D2 "left only to an actor that already has root or `daemon`-group rights") omit R-P3-2.

**Failure scenario:** the owner checklist and the testing phase rely on these statements. A tester reading "only programs that root owns are accepted" would not try a relative hook.

**Suggested fix:** correct each statement when R-P1-1, R-P1-2 and R-P3-2 are fixed. If any is deferred, state the limit in spec §17 "Limits".

## Tests
- **Pinned well:** every shape the security report named (BOM before `config`/`management`/`replay-persist`/`up`; the five name shapes; the six quote shapes; the missing hook, plugin and library; the three new hook keys; server mode; the five file-writing directives; seven `dev-node` values; the user-owned absolute file for three directives; the planted openvpn; the five P3-3 shapes; NFC/NFD).
- **Two pre-existing tests changed, and neither was weakened:**
  - `test_a_properly_closed_block_with_crlf_is_fine` changed its input from `/nonexistent-dir/x.sh` (now a blocker by design) to `/usr/bin/true`. It keeps asserting `allowed` and the `\r`-free hook value, and now prints the blockers on failure.
  - `test_an_absolute_file_is_named_and_its_residual_is_shown` became `…_root_owns_is_named_and_checked_in_place`, with `_stat` faked to root. The opposite case is covered by the new `test_an_absolute_file_the_user_could_swap_is_a_blocker`.
- **Missing:**
  - Relative and `../` hook programs or arguments, including `plugin` and `dns-updown` (R-P1-1).
  - `sh -c` with a compound line (R-P1-2): its absence is why the regression went unnoticed.
  - `../cfg/x.sh` and `../config.ovpn` (R-P3-1).
  - A symlinked parent (R-P3-2).
  - `test_dns_updown_keywords_are_listed_but_not_blocked` pins an outcome that holds for the wrong reason. It would still pass if `dns-updown ../../x` were accepted, so pair it with a negative case.

## Not verified
- All OpenVPN behaviour cited: `parse_line` backslash and comment order, `argv_parse_cmd`'s whole-string fallback, `--cd` chdir timing and `daemon(nochdir)`, relative `up`/`plugin`/`dns-updown` resolution, `server-ipv6` implying server mode, `open_tun_generic` opening `/dev/<dev>`, and whether "Bad backslash usage" and an over-long line (`OPTION_LINE_SIZE`, raised as not verified by the security review and still unaddressed) are fatal. openvpn is not installed here.
- The root path was not exercised beyond what the four test files do (bash harness, as the user, in temp folders). No mutation run of my own. The author reports 16/16 and 10/10.
- The full suite was not run, only the four files above.
- I did not run `log_activity.py`: writing outside this report was out of scope.

## Resolution

| Finding | Resolution | Commit |
|---|---|---|
| R-P1-1 | Fixed: an OpenVPN hook's first word must be an absolute path (OpenVPN execs it directly; relative names resolve under --cd). Tests: relative program, `../` escapes, `plugin ../`, `dns-updown ../`. | 2f39932 |
| R-P1-2 | Fixed: an OpenVPN hook may not run through an interpreter or wrapper (`_INTERPRETER`), so `sh -c`, `env -S`, `python -c`, `perl -e` and relative interpreter arguments are refused. WireGuard unchanged (test pins it). | 2f39932 |
| R-P3-1 | Fixed by the R-P1 rule: `../cfg/x.sh`, `../config.ovpn` and `env -S 'sh x.sh'` are refused. Tests included. | 2f39932 |
| R-P3-2 | Fixed: `_unsafe_executable_reason` walks the written path's parents and, when links lead elsewhere, the real path's parents too. Tests: a root-owned link into a user-owned folder, for a program and for an absolute data file. | e06b423 |
| R-P3-3 | Fixed (OpenVPN behaviour NOT VERIFIED): `server-ipv6` is a server key; `dev` must be `(tun|tap|utun)[0-9]{0,3}`. Tests: allowed and refused values. | e06b423 |
| R-P3-4 | Fixed: `_ovpn_tokens` refuses any backslash except `\\`, `\"` and backslash-space, inside or outside double quotes, and a trailing backslash, so the parsers agree or the line is refused; the docstring says so. Tests: `\#`, `\q`, trailing, inside quotes, and in a second split; plus the escapes that still work. | e06b423 |
| R-P3-5 | Fixed: the review blocks when it resolves no program (no second lookup), and `openvpn_manager.connect` runs nothing without the reviewed program. Tests: review and Connect. | e06b423 |
| R-P3-6 | Fixed: module and helper docstrings, the security report's resolutions, `docs/agents/vpn.md`, CHANGELOG and spec §17 corrected to what the code does. | 2f39932 |

Follow-up: R-P1-1 and R-P1-2 held only for lowercase, listed interpreter names, and R-P3-2 for one link hop; reopened by `docs/audit/audit-tier2-s8b-review-r5r6-2026-11-05.md` (R5-P1-1, R5-P3-1) and fixed there.

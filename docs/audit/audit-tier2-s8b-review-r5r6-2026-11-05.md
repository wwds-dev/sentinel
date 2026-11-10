# Review: audit tier-2 S8b, rounds 5-6 fixes (`2f39932`, `e06b423`)

**Scope:** `git show 2f39932 e06b423`: `services/vpn_execution.py`, `services/openvpn_manager.py` and five test files. I also checked the claims in the docs commits `417160a` and `dc65fd2`: CHANGELOG, `docs/agents/vpn.md`, ADR 0004 §3, spec §17 rounds 5-6, and the Resolution tables of the re-review and security reports.
**Read against:** `audit-tier2-s8b-rereview-2026-11-05.md`, `audit-tier2-s8b-security-2026-11-05.md`, spec §17, ADR 0004 §3, and the Tunnel rules in `CLAUDE.md`.
**Reviewer:** code-reviewer, independent of the author, read-only. No privileged, root, tunnel, kill-switch or network action was taken. No `.env` file was read. (The reviewer returned this report as text; the author saved it here unchanged.)
**Playbook:** `docs/playbooks/code-reviewer.md`, `status: current`, `last-reviewed: 2026-10-14` (22 days old, not stale).
**App context:** `docs/company/app-context.md` is not in this worktree, so I read the main checkout's copy.

## What was run
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_review_gate.py tests/test_vpn_execution.py tests/test_vpn_connection.py tests/test_vpn_rootscript.py tests/test_tunnel_keeps_no_logs.py`: **582 passed in 23.75 s**.
- Probes in the session scratchpad (`python -I`, worktree on `sys.path`). They called `review_execution` with `openvpn_binary` stubbed and a fake probe, plus `_hook_findings`, `_ovpn_tokens` and `_unsafe_executable_reason` with a fake `_stat` on temp trees. Nothing reached `execute` or a root runner.
- Harmless user-level runs to confirm macOS behaviour:
  - `/bin/SH -c 'echo …'` and `/bin/ſh -c 'echo …'` (U+017F, long s) both ran.
  - `ls -la /bin/SH /usr/bin/PERL /usr/bin/Python3` shows root-owned files: the system volume is case-insensitive.
  - `/usr/bin/arch -arm64 /usr/bin/true`, `/usr/bin/caffeinate -i /usr/bin/true` and `/usr/bin/lockf -t 0 <file> /usr/bin/true` each ran their argument.
- openvpn is not installed here. Every claim about OpenVPN's behaviour is marked NOT VERIFIED.

## Verdict
**No P0. One P1, two P2 (one pre-existing and outside the diff), three P3.**
- **Holds:** R-P3-1, R-P3-3, R-P3-4 (parsing) and R-P3-5.
- **Reopen:** R-P1-1 and R-P1-2. They hold only for lowercase, exactly-listed program names. The `_INTERPRETER` basename denylist is case-sensitive on a case-insensitive filesystem and misses many root-owned programs that run their arguments. Each of these brings back the exact R-P1-2 or R-P1-1 outcome, with `allowed=True` and no blockers (R5-P1-1).
- **Partly holds:** R-P3-2. It misses a link reached through a second link (R5-P3-1).

What holds (checked by probe or code):
- **R-P1-1 (lowercase forms):** a relative program, a `../` program, `../cfg/x.sh`, `../config.ovpn` and `plugin ../x` all block. The `dns-updown disable|force` exemption is now explicit (`vpn_execution.py:485-486`).
- **R-P1-2 (lowercase forms):** `/bin/sh`, `/bin//sh`, `/bin/./sh`, `/private/var/select/sh`, `/usr/bin/../../bin/sh`, `env -S`, `python3 -c`, `perl -e`, `nohup` and `expect` all block.
- **R-P3-3:** `server-ipv6` blocks. `dev` other than `(tun|tap|utun)[0-9]{0,3}` blocks. `dev tap0` with `dev-type tap` passes.
- **R-P3-4:** `\q`, `\#` and a trailing backslash are refused both inside and outside double quotes, and in the second split. `\\`, `\"` and `\ ` still work.
- **R-P3-5:** no remaining path where root runs an openvpn the review did not name: the review blocks on an empty `program` (`vpn_execution.py:1156-1159`); `program` is in the fingerprint (`:844`) and `execute` passes it (`:1385`); `openvpn_manager.connect` refuses an empty value (`openvpn_manager.py:246-248`); the only caller is `vpn_connection.connect:161`; the root script requires an absolute path (`vpn_rootscript.py:221`, `:430`). `connect` still calls `is_openvpn_available()` (`openvpn_manager.py:233`), but that can only refuse, never choose a program.
- **No regressions for real profiles:** Nord-, Proton-, Mullvad-, PIA- and Surfshark-style profiles and a Tunnelblick-style `up /usr/bin/true` all give `allowed=True` with no blockers (inline `<ca>`/`<tls-auth>`/`<tls-crypt>`/`<crl-verify>`, commented-out `up` lines, `auth-user-pass` and `ca` beside the config, `tun-ipv6`, `tun-mtu-extra`, `ping-timer-rem`, `disable-occ`). WireGuard is unchanged apart from the shared R-P3-2 walk.
- **Changed tests are inputs, not weakened assertions:** `openvpn=` or an `openvpn_binary` patch was added; the `is_running` and missing-binary tests still reach their branch; `test_openvpn_connect_without_the_reviewed_program_runs_nothing` is a new negative case.

## Findings

### R5-P1-1: The OpenVPN "no interpreter or wrapper" rule is a case-sensitive name denylist; case spellings, versioned names and non-listed wrappers bring back R-P1-2 and R-P1-1
**Location:** `services/vpn_execution.py:391-393` (`_INTERPRETER`, case-sensitive full match on the basename); `:495` (match on `program.rsplit("/", 1)[-1]` as written, no `realpath`, case folding or normalisation); `:508-528` (after a non-interpreter program, an absolute user-owned argument is only a warning, `:525-528`, and a relative argument is never looked at, `:509-511`).

**Evidence:** `review_execution("connect", …)` returned `allowed=True` and `blockers=()` for each of the following, each with `script-security 2`:
- **Case and case-fold spellings** (all exist and run on the case-insensitive system volume): `up "/bin/SH -c 'cd /Users/as && ./evil.sh'"` (the exact R-P1-2 line with one letter changed), `up "/bin/Bash -c './x'"`, `up "/BIN/SH -c x"`, `up "/bin/ſh -c x"`, `up "/usr/bin/PERL -e 'exec q(../x)'"`, `up "/usr/bin/Python3 -c 'import os'"`, `up "/usr/bin/ENV -S 'sh x'"`.
- **Versioned names and links not in the list:** `/usr/bin/perl5.34 -e …`, `/usr/bin/tclsh8.5 ../…/Users/as/evil.sh`, `/usr/bin/wish8.5 …`; `vi`, `ex`, `view` link to `vim`, and `/usr/bin/vim -es -c '!../x'` is allowed.
- **Root-owned programs that run an argument, with a `../` escape:** `/usr/bin/arch ../…`, `/usr/bin/caffeinate -i ../…`, `/usr/bin/script -q /dev/null ../…`, `/usr/bin/lockf /tmp/l ../…`, `/usr/bin/sandbox-exec -n no-internet ../…`, `/usr/bin/stdbuf -o0 ../…`, `/usr/bin/xcrun ../…`, `/usr/bin/find /tmp -maxdepth 0 -exec ../… {} +`, `/usr/sbin/chroot / ../…`, `/usr/bin/su root -c ../…`, `/usr/bin/m4 ../…`, `/usr/bin/ssh -o ProxyCommand=../x h`, `/usr/bin/sqlite3 :memory: '.shell ../x'`.
- **Same shape with an absolute user path:** `up "/usr/bin/arch -arm64 /Users/as/evil.sh"` gives a warning only.

Confirmed at user level that `/bin/SH`, `/bin/ſh`, `arch -arm64 <prog>`, `caffeinate -i <prog>` and `lockf <file> <prog>` execute. The new parametrised test (`tests/test_vpn_review_gate.py:1335`) covers only lowercase names on the list.

**Failure scenario:** a same-user process writes `up "/bin/SH -c 'cd /Users/as/.cache && ./x'"` (or `up "/usr/bin/caffeinate -i ../../../../../../../../Users/as/x"`) into the profile. The review lists it with no blocker and the user confirms. Root's openvpn execs `/bin/SH` (NOT VERIFIED: OpenVPN execve with no shell), which runs `./x` from a user-owned folder that can be rewritten at any time. This is the R-P1-2 outcome again (and R-P1-1's `../` escape through a wrapper). The R-P1-1/R-P1-2 rows of the re-review Resolution table should be reopened.

**Suggested fix:** stop relying on the program's spelled name.
1. Interpreter test by identity: compare `unicodedata.normalize("NFD", basename(os.path.realpath(program))).casefold()` with any `[0-9.]+$` suffix removed, or compare `(st_dev, st_ino)` with the resolved interpreter list.
2. Program location (more robust): OpenVPN client hooks are purpose-built scripts. Refuse any OpenVPN hook program whose real path is under `/bin`, `/sbin`, `/usr/bin`, `/usr/sbin`, `/usr/libexec` or `/System`, apart from an explicit allow-list (`/usr/bin/true`, if the test fixture needs it).
3. Arguments of every OpenVPN hook: a word containing `/`, or starting with `.` or `~`, must be an absolute root-owned path (soft blocker otherwise, not a warning); a relative word containing `/` is refused.
4. Tests: every line above, and the NFD/case variants.

### R5-P2-1: `plugin` arguments are never checked; the standard down-root plugin runs them as root (NOT VERIFIED)
**Location:** `services/vpn_execution.py:508-528`. `plugin` goes through `_program_findings`, which treats the words after the `.so` as data.

**Evidence:** `plugin /usr/bin/true ../../../../../../../Users/as/evil.sh` and `plugin /usr/bin/true "/bin/sh -c ./x"` give no blocker, soft blocker or warning (`/usr/bin/true` stands in for a root-owned plugin path). A plugin's arguments go to the plugin; `openvpn-plugin-down-root.so` executes them as a command, as root, at down (NOT VERIFIED).

**Failure scenario:** with a root-owned OpenVPN plugin directory (MacPorts `/opt/local`, the official installer, Tunnelblick's bundle; Homebrew's is user-owned and already refused), a profile adds `plugin /opt/local/lib/openvpn/plugins/openvpn-plugin-down-root.so "../../../../../Users/as/x"`. Root runs the user's file on Disconnect.

**Suggested fix:** for `plugin`, refuse arguments outright, or apply the hook-program rule (R5-P1-1) to the words after the module path. Test with a fake root-owned plugin path.

### R5-P2-2 (pre-existing, outside the diff): WireGuard hooks accept a relative program after `cd`, and treat case-variant interpreters as data
**Location:** `services/vpn_execution.py:483` (the same case-sensitive `_INTERPRETER` sets `runs_code`), `:499-507` (only bare words are resolved), `:509-511` (`./x` is ignored).

**Evidence** (`_hook_findings`, WireGuard):

| Hook | Result |
|---|---|
| `PostUp = cd /Users/as && ./evil.sh` | warning only ("hands /Users/as to a program") |
| `PostUp = /bin/SH /Users/as/evil.sh` | warning only |
| `PostUp = /bin/sh /Users/as/evil.sh` | soft blocker |
| `PostUp = Bash -c ./evil` | nothing |

`2f39932` leaves WireGuard unchanged and pins it with `test_wireguard_hooks_keep_their_shell_line_rules` (`tests/test_vpn_review_gate.py:1350`), so this predates these commits.

**Failure scenario:** as R-P1-1, for WireGuard: root's bash runs a user-replaceable `./evil.sh`, and the user sees a warning rather than a soft blocker.

**Suggested fix:** share the case-folded interpreter test from R5-P1-1; in a WireGuard segment treat a command word containing `/` that is not absolute as unsafe; treat `cd` (or `pushd`) to a path that is not root-owned as unsafe.

### R5-P3-1: The R-P3-2 walk checks the written path and the final real path, but not the folder of an intermediate link
**Location:** `services/vpn_execution.py:446-449`.

**Evidence:** probe tree `rootdir/l1 → home/l2 → rootdir2/sub/prog`, only `home` user-owned (fake `_stat`, as `_linked_tree`): `_unsafe_executable_reason(rootdir/l1/prog)` returns **None**; `realpath` is `rootdir2/sub/prog`, and neither walk includes `home`. The one-hop shape in the new test (`tests/test_vpn_review_gate.py:1377`) is caught.

**Failure scenario:** a root-owned link points to a user-owned link; after the review the user retargets `home/l2`, and root reads or runs their file (D2 swap). The precondition matches R-P3-2 (a root-owned link the attacker cannot create), hence P3. The Resolution table's "Fixed" holds for one hop only.

**Suggested fix:** resolve the path one component at a time and check the directory containing every link met on the way, or refuse any path whose `realpath` differs from its `normpath`. Add a two-hop test.

### R5-P3-2: A refused backslash is reported as a quote problem
**Location:** `services/vpn_execution.py:302-306` and `:474-476`.

**Evidence:** `verify-x509-name my\qname name` gives "line 4: a quote in 'verify-x509-name' is unbalanced or touches other text; put spaces around quoted values". There is no quote in that line. The second-split path says "has a quote that OpenVPN may split differently".

**Suggested fix:** have `_ovpn_tokens` return why it refused, and word the backslash case accordingly. Add an assertion on the text to the parametrised test (`:1408`).

### R5-P3-3: Docs and Resolution tables claim more than the code does, and two statements are stale
- `CHANGELOG.md:55-57` and `docs/agents/vpn.md:48` say "running it through `sh`, `env` … or another interpreter or wrapper … is refused"; false per R5-P1-1.
- Re-review Resolution table (`audit-tier2-s8b-rereview-2026-11-05.md:183`): "so `sh -c` … are refused". The R-P3-2 row says "Fixed" but holds for one hop only (R5-P3-1).
- Spec §17 round 5 (`docs/specs/audit-tier2-privileged-layer.md:454`) presents `_INTERPRETER` as the complete rule.
- Stale: the `_library_findings` docstring (`services/vpn_execution.py:533-537`) still says "links are not followed; S8b re-review R-P3-2"; since `e06b423` they are.
- Stale: `audit-tier2-s8b-security-2026-11-05.md:235` still says "(re-review R-P3-2, open)".

**Suggested fix:** correct these when R5-P1-1 and R5-P3-1 are fixed; if either is deferred, state the limit in spec §17 "Limits".

## Tests
- **Pinned:** lowercase relative and interpreter hooks, the `dns-updown` exemption, plain root-owned arguments, the one-hop link, `server-ipv6`, nine `dev` values, five backslash refusals plus three accepted escapes, and the review and Connect refusing without a program.
- **Missing:** case and Unicode spellings, versioned names and wrapper programs (R5-P1-1); plugin arguments (R5-P2-1); WireGuard `cd … && ./x` (R5-P2-2); a two-hop link (R5-P3-1); blocker wording for a backslash refusal (R5-P3-2).

## Not verified
- All OpenVPN behaviour cited: execve of the hook's first word with no shell or PATH, `argv_parse_cmd`, how `parse_line` treats backslashes (including inside single quotes, which Sentinel reads literally), down-root executing its plugin arguments, `open_tun_generic` for TAP names, and `server-ipv6` implying server mode. openvpn is not installed here.
- The full suite was not run, only the five files above. No mutation run of the reviewer's own; the author reports 4/4 and 10/10.
- `log_activity.py` was not run (read-only).

## Resolution

| Finding | Resolution | Commit |
|---|---|---|
| R5-P1-1 | Fixed: an OpenVPN hook may not be a macOS system program (`/bin`, `/sbin`, `/usr/bin`, `/usr/sbin`, `/usr/libexec`, `/usr/lib`, `/System`, `/private/var/select`, `/Library/Apple`, compared case-folded on the written and real path; only `/usr/bin/true` and `/usr/bin/false` allowed), nor an interpreter or wrapper judged by its written and real name with case, Unicode form and version suffix ignored (`_INTERPRETER` extended with the wrappers found). Every argument that names a path must be absolute and root-owned. Tests: every probe line plus argument shapes. | 5f2c083 |
| R5-P2-1 | Fixed: `plugin` down-root arguments get the hook-program rule; other plugins' arguments the path-argument rule. Tests with a fake root-owned module. | 5f2c083 |
| R5-P2-2 | Fixed: WireGuard hooks refuse a relative program path, `cd`/`pushd` into a folder that is relative or not root-owned, and a relative path argument to an interpreter; the interpreter test is the shared case-folded identity. Inline `sh -c 'echo hi'` still passes. | 5f2c083 |
| R5-P3-1 | Fixed: `_resolution_steps` replaces one link at a time and every intermediate path's parents are checked. Test: a two-hop chain. | 5f2c083 |
| R5-P3-2 | Fixed: `_ovpn_split` returns why it refused; a backslash is reported as a backslash. Test asserts the wording. | 5f2c083 |
| R5-P3-3 | Fixed: CHANGELOG, `docs/agents/vpn.md`, spec §17, the `_library_findings` docstring, the re-review's and security report's Resolution notes corrected. | 5f2c083 |

Follow-up: R5-P1-1 and R5-P2-2 were only partly fixed by `5f2c083` (hidden macOS path prefixes, Command Line Tools, bash grammar); see `docs/audit/audit-tier2-s8b-review-r7-2026-11-05.md`, where they are completed.

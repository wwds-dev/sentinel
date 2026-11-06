# Review: audit tier-2 S8b, review R5 fixes (`5f2c083`, docs `ee1e1e7`)

**Scope:** `git show 5f2c083`, which changes `services/vpn_execution.py` and `tests/test_vpn_review_gate.py`. Also checked the claims in `ee1e1e7`: CHANGELOG, `docs/agents/vpn.md`, spec §17 "Round 7", and the Resolution table of `audit-tier2-s8b-review-r5r6-2026-11-05.md`.
**Read against:** `audit-tier2-s8b-review-r5r6-2026-11-05.md`, spec §17 (including Limits, R1 and D2 as narrowed), and the Tunnel rules in `CLAUDE.md`.
**Reviewer:** code-reviewer, independent of the author, read-only. No privileged, root, tunnel, kill-switch or network action was taken. No `.env` file was read. Nothing in the repo was edited. (The reviewer returned this report as text; the author saved it unchanged.)
**Playbook:** `docs/playbooks/code-reviewer.md`, `status: current`, `last-reviewed: 2026-10-14` (22 days old, not stale).
**App context:** `docs/company/app-context.md` is not in this worktree, so the main checkout's copy was read.

## What was run
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_review_gate.py tests/test_vpn_execution.py tests/test_vpn_connection.py tests/test_vpn_rootscript.py tests/test_tunnel_keeps_no_logs.py`: **625 passed in 22.20 s**.
- Probes in the session scratchpad (`python -I`, worktree on `sys.path`, the same stubs as the test fixture `tools_installed`): `review_execution("connect", …)`, `_resolution_steps` and `_unsafe_executable_reason`, with a fake `_stat` where root ownership had to be simulated. Nothing reached `execute` or a root runner.
- Before/after comparison: `5f2c083^` extracted with `git archive` into the scratchpad; the same probes run against both trees.
- Mutation run: 13 hand-written mutants of the new code, on a scratch copy of the worktree, against `tests/test_vpn_review_gate.py`. The repo was not touched.
- Harmless user-level checks of macOS behaviour:
  - `/.vol/16777232/1152921500312607361 -c 'echo …'`, `/.nofollow/bin/sh -c …`, `/.resolve/1/bin/sh -c …` and `/.resolve/0/bin/sh -c …` all ran `/bin/sh`.
  - `/.nofollow/usr/bin/git -c 'alias.x=!echo${IFS}…' x` and `/Library/Developer/CommandLineTools/usr/bin/git -c 'alias.x=!echo${IFS}…' x` both ran the alias's shell command.
  - `hdiutil create` and `attach` of a 1 MB image gave `/Volumes/sentinelprobe` owned by `as` (detached and deleted afterwards).
  - A 33-hop symlink chain gives `ELOOP` from `stat`; 32 hops work.
- openvpn is not installed here, and no wg-quick hook was run. Every statement about how OpenVPN or wg-quick behaves is marked NOT VERIFIED.

## Verdict
**No P0. One P1, three P2 (one pre-existing), three P3.**
- **Holds:** R5-P3-1 (link chains: relative two-hop links, `..` after a link, `..` inside a second link's target, a dangling link into a user folder are all caught; a loop or a chain over 32 hops is unusable to the kernel and refused as "does not exist") and R5-P3-2 (backslash wording).
- **Holds for the written forms tested:** R5-P2-1 (plugin arguments). The down-root decision is by written name only, so the R7-P1-1 path spellings defeat it.
- **Reopen:** R5-P1-1. The location rule compares path prefixes; macOS path prefixes the code never normalises (`/.nofollow/`, `/.resolve/N/`, `/.vol/<dev>/<ino>`) reach every system program, `/bin/sh` included, with `allowed=True` and no blockers (R7-P1-1). Apple's Command Line Tools folder is not on the list (R7-P2-1).
- **Partly holds:** R5-P2-2. The literal forms tested are caught, but ordinary bash grammar (`(cd …)`, `$(…)`, `{ …; }`, `builtin cd`, `HOME=… cd`, `source`/`.`, variables) gets around them (R7-P2-2).
- **Regressions:** two ordinary hook shapes that `5f2c083^` allowed are now refused (R7-P3-1). No refusal of plain system-program WireGuard hooks: `/sbin/route … 10.0.0.0/8 …`, `/usr/sbin/networksetup …`, `/sbin/pfctl -a com.apple/wireguard -f /etc/pf.conf`, `/usr/bin/true`, `printf … | scutil` and `/usr/bin/curl -s https://…` all give `allowed=True`.

## Findings

### R7-P1-1: Special macOS path prefixes get around the system-program rule (and, with `/.vol`, the identity rule), so R5-P1-1 comes back
**Location:** `services/vpn_execution.py:412-414` (`_SYSTEM_PROGRAM_DIRS`, a plain prefix list); `:433-437` (`_system_program` compares `normpath` and `realpath`, neither removes these prefixes); `:422-430` (`_runs_code` judges by basename, and a `/.vol` basename is an inode number); `:563` (down-root recognised by the written basename only).

**Evidence:** `review_execution("connect", …)` with `script-security 2` returned `allowed=True, blockers=()` for:
- `up "/.vol/16777232/1152921500312607361 -c 'cd ~as;sh evil.sh'"`: `/bin/sh` by device and inode; the same line with `/bin/sh` is refused. The argument rule never fires (the `-c` string has no `/` and does not start with `.` or `~`).
- `up "/.nofollow/usr/bin/git -c alias.x=!cd${IFS}~as;sh${IFS}evil x"`, and the same with `/.resolve/1/usr/bin/git`. At user level the alias's shell command ran.
- `up /.nofollow/usr/bin/say`: any system program at all.
- `plugin <root-owned module whose basename lacks "down-root"> "/bin/sh -c 'cd ~as;sh evil'"` (fake root-owned `/opt/sentinel-test/dr.so`): `allowed=True`. A `/.vol` spelling of the real `openvpn-plugin-down-root.so` has that shape.
- WireGuard: `PostUp = /.vol/<dev>/<ino of /bin/sh> /Users/…/evil.sh` gives only a warning; with `/bin/sh` it is a soft blocker.

`/.nofollow` and `/.resolve` are root-owned folders and `/.resolve/1` cannot be `stat`ed, so `_unsafe_executable_reason` passes all three spellings.

**Failure scenario:** a same-user process writes the `/.vol` line into the profile; the review shows no blocker; root's openvpn execs `/bin/sh` (NOT VERIFIED: execve with no shell), which `cd`s into the user's home and runs a file the user can rewrite at any time. The `/.vol` device number can change across reboots; the `/.nofollow` spelling cannot.

**Suggested fix:** refuse any hook, plugin or argument path whose first component starts with `.` (deny by shape, not by known prefix); decide down-root by the folded real-path basename as well as the written one; tests for each spelling, with `/bin/sh` and with a program not on the name list.

### R7-P2-1: Apple's Command Line Tools are root-owned programs that run code, outside the system folder list
**Location:** `services/vpn_execution.py:412-413`.

**Evidence:** `up "/Library/Developer/CommandLineTools/usr/bin/git -c alias.x=!cd${IFS}~as;sh${IFS}evil x"` gives `allowed=True, blockers=()`. The folder is `root:wheel 755` here, and the alias ran at user level. The same folder holds `gnumake`, `pydoc3`, `pip3`, `easy_install-3.8`, `git-shell`, `swift-run` and others not on `_INTERPRETER`. Xcode under `/Applications` is already refused by ownership (`/Applications` is `root:admin 775`).

**Failure scenario:** the R7-P1-1 scenario with the CLT path written out. The Round 7 Limits cover "a root-owned interpreter installed elsewhere under a new name"; this is Apple's own tool under its own name, on most developer Macs.

**Suggested fix:** add `"/library/developer/"` to `_SYSTEM_PROGRAM_DIRS`, or state this case in spec §17 Limits for the owner to accept.

### R7-P2-2: The WireGuard `cd` and interpreter rules (R5-P2-2) are bypassed by ordinary bash grammar
**Location:** `services/vpn_execution.py:573` (`cd`/`pushd` recognised only as the segment's command word, after `VAR=` prefixes); `:580-585` and `:403-407` (`source` and `.` are not interpreters); `:600-615` (a path word starts at `index`, so `VAR=` values are never checked, and a user-owned path given to a non-interpreter is only a warning).

**Evidence** (`review_execution`, WireGuard, `H` = a user-owned temp folder holding `evil.sh`):

| `PostUp =` | Result |
|---|---|
| `cd H && sh evil.sh` (control) | refused |
| `(cd H && sh evil.sh)` | allowed, warning only |
| `echo $(cd H; sh evil.sh)` | allowed, warning only |
| `` echo `cd H; sh evil.sh` `` | allowed, warning only |
| `{ cd H; sh evil.sh; }` | allowed, warning only |
| `builtin cd H; sh evil.sh` | allowed, warning only |
| `HOME=H cd; sh evil.sh` | allowed, **no finding** |
| `source H/evil.sh` | allowed, warning only |
| `. H/evil.sh` | allowed, warning only |
| `. <H without leading />/evil.sh` | allowed, **no finding** (resolves against the hook's cwd, NOT VERIFIED) |
| `X=H/evil.sh; sh $X` | allowed, **no finding** |
| `sh H/evil.sh` (control) | refused |

**Failure scenario:** as R5-P2-2. Root's bash runs a file the user can replace after the review, and the review shows at most a warning (NOT VERIFIED: wg-quick runs hooks with `bash -c`).

**Suggested fix:** check every path word in a WireGuard segment, including `VAR=` values; make a path that is not root-owned a soft blocker, not a warning, for WireGuard; recognise `cd`/`pushd` after stripping `(`, `$(`, `` ` ``, `{`, `builtin` and `command`, and treat `HOME=` before a bare `cd` as its target; add `source` and `.` to the interpreter identity; a test for each row above.

### R7-P2-3 (pre-existing, outside the diff): a path that does not exist at review is judged by its nearest existing parent, and a normal user can create `/Volumes/<name>`
**Location:** `services/vpn_execution.py:525` (`continue  # not there yet: judged by what exists`); `:653-671` (OpenVPN arguments have no existence check); `:600-615` (in WireGuard, a missing path is at most a warning).

**Evidence:** `hdiutil attach` as user `as` produced `/Volumes/sentinelprobe`, owned by `as`; `/Volumes` itself is `root:wheel 755`, so before the mount every existing parent of `/Volumes/sentinelprobe/x` is root-owned.
- WireGuard `cd /Volumes/sentinelprobe && sh x`: allowed, no finding.
- WireGuard `sh /Volumes/sentinelprobe/x`: allowed, no finding.
- WireGuard `/Volumes/sentinelprobe/x`: allowed, warning "does not exist".
- OpenVPN `up "/usr/bin/true /Volumes/sentinelprobe/x"`: allowed. An OpenVPN hook *program* that does not exist is correctly refused.
- `5f2c083^` behaves the same for all four.

**Failure scenario:** the user confirms a hook naming a missing `/Volumes/<name>/…`; a same-user process then attaches a disk image with that volume name, and root's bash runs its contents at Connect (review-to-run TOCTOU). D2 trusts root-owned files; this file is user-owned by the time it runs.

**Suggested fix:** in `_unsafe_executable_reason`, treat a missing candidate as unsafe when its nearest existing ancestor is a mount root (`/Volumes`, `/net`, `/Network`), or simply when it is missing; in WireGuard, require a hook's program and interpreter arguments to exist.

### R7-P3-1: Regressions: ordinary hook lines that `5f2c083^` allowed are now refused, with wrong wording
**Location:** `services/vpn_execution.py:580-585` (any word containing `/` given to an interpreter); `:668-670` (an OpenVPN `--opt=/abs` value called a relative path).

**Evidence** (each allowed by `5f2c083^`, refused now):
- WireGuard `PostUp = sh -c 'route -q -n add -inet 10.0.0.0/8 -interface %i'`: "hands '10.0.0.0/8', a relative path, to sh, which runs it".
- WireGuard `PostUp = bash -c "ifconfig %i inet 10.8.0.2/32 10.8.0.1 alias"`: the same for `10.8.0.2/32`.
- OpenVPN `up "/usr/bin/true --conf=/etc/hosts"` (a root-owned file): "hands '--conf=/etc/hosts', a relative path".

`cd /etc && ./x` is also refused now, but spec §17 states that on purpose.

**Failure scenario:** a profile with CIDRs inside `sh -c` cannot connect; the message points at a non-existent file problem.

**Suggested fix:** do not treat an address or CIDR word (`^[0-9A-Fa-f:.]+/[0-9]{1,3}$`) as a path; for `--opt=value`, judge the value with the absolute-path rule; add both shapes as "still allowed" tests.

### R7-P3-2: Tests do not isolate the system-program rule, and the Resolution's test claim overstates
**Location:** `tests/test_vpn_review_gate.py:1434-1451`, `:1586-1591`; spec §17 Round 7 ("Mutation check: 19/19 … five tests now isolate each rule"); Resolution R5-P1-1 ("Tests: every probe line").

**Evidence:** reviewer's mutants against `test_vpn_review_gate.py`: M1 `_system_program` without `_fold`: survived; M2 without the `realpath` form: survived; M6 `"/usr/libexec/"` removed: survived; M3-M5, M7-M13 killed.
- The param line `up /usr/libexec/x` is refused only because the file does not exist.
- Case spellings in system folders are caught by the identity rule, so nothing pins the case-folded location check (which catches a non-listed system wrapper such as `/USR/BIN/GIT`).
- The only isolating test (`/usr/bin/say`) is skipped on Linux CI, where most param lines are refused because the file does not exist.
- The R5 probe lines `sandbox-exec`, `stdbuf`, `/usr/bin/m4` and `/usr/bin/su` are not in the tests.
- No test was weakened; the one changed input (`/bin/echo up` → `/usr/bin/true up`) keeps its assertion and purpose.

**Suggested fix:** add `up /USR/BIN/SAY`, an existing `/usr/libexec` program, and a fake root-owned link outside the system folders pointing at `/usr/bin/say`, each asserting "system program"; add the four missing probe lines; correct the mutation and test claims in spec §17.

### R7-P3-3: Docs and the Resolution table claim more than the code does
- `CHANGELOG.md:57` and `docs/agents/vpn.md:48`: "an interpreter or wrapper in any spelling … is refused"; false per R7-P1-1 and R7-P2-1.
- `CHANGELOG.md:60` and `vpn.md:48`: "WireGuard hooks now refuse `cd` into a folder your user owns"; false per R7-P2-2.
- `CHANGELOG.md:58`: "an argument naming a path your user could change is refused"; false per R7-P2-3.
- The r5r6 Resolution table marks R5-P1-1 and R5-P2-2 "Fixed"; should read "Partly fixed; see R7".
- Spec §17 Round 7 Limits names only "an interpreter … under a new name"; it should also name path-prefix spellings and CLT, or record them as fixed.

**Suggested fix:** correct these when R7-P1-1, R7-P2-1 and R7-P2-2 are fixed; record anything deferred in spec §17 Limits for the owner.

## Not verified
- All OpenVPN behaviour: execve of the hook's first word, `argv_parse_cmd`, down-root running its arguments, the hook's cwd under `--cd`. openvpn is not installed here.
- How wg-quick runs hooks (`bash -c`) and the cwd of the privileged shell, which the `. relative` row depends on.
- That root's exec of `/.vol/…` and `/.nofollow/…` paths behaves like the user-level runs.
- The full suite; only the five files were run.
- `log_activity.py` was not run (read-only).

## Resolution

| Finding | Resolution | Commit |
|---|---|---|
| R7-P1-1 | Fixed: `_unsafe_executable_reason` refuses any absolute path whose first component starts with `.` (`/.vol`, `/.nofollow`, `/.resolve`, future ones), so every rule built on it (hook programs, arguments, libraries, plugin modules, data files, WireGuard paths) refuses them; data files are refused before they are read. down-root is recognised by the written or the real name. Tests: each spelling, WireGuard, a data file, a down-root link. | 6de7c8f |
| R7-P2-1 | Fixed: `/Library/Developer/` (Command Line Tools) is a system folder. Test: location detection without the file existing. | 6de7c8f |
| R7-P2-2 | Fixed, by refusing what the review cannot follow rather than patching each form: a WireGuard hook with `$`, backticks, parentheses or braces, or using `source`, `.`, `eval`, `exec`, `builtin` or `command`, is refused; every path word, `VAR=` values included, must exist and be root-owned (a user-owned file handed to a root program now blocks; it was a warning). Tests: every row of the table. | 6de7c8f |
| R7-P2-3 | Fixed: a path that does not exist blocks, for WireGuard path words and OpenVPN arguments (OpenVPN programs already did). Tests: `/Volumes/<missing>` shapes. | 6de7c8f |
| R7-P3-1 | Fixed: an address or network word (`_CIDR`) is not a path; an OpenVPN `--opt=value` is judged by its value; `/dev/null` is exempt. Tests: the reported hooks and ordinary macOS WireGuard hooks still pass. | 6de7c8f |
| R7-P3-2 | Fixed: location detection is tested on its own without the files (`/USR/LIBEXEC/x`, `/usr/bin/../libexec/x`, `/bin/ſh`, CLT), existing system programs including `/usr/libexec/PlistBuddy`, `/USR/BIN/SAY`, the four missing probe lines, and a link outside the system folders to `/usr/bin/say`. | 6de7c8f |
| R7-P3-3 | Fixed: CHANGELOG, `docs/agents/vpn.md`, the r5r6 Resolution notes and spec §17 corrected. | 6de7c8f |

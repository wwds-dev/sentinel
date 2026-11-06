# Second independent review of PR #32 (hidden WireGuard hooks), 2026-11-06

Reviewer: Opus, read-only. Scope: `services/vpn_execution.py` at `05b1d75`
(`fix/wg-hidden-hooks`), `agents/vpn_agent/services/config_inspection.py`. Every
finding below was already on `main` before #32; #32 itself was merge-ready and
was merged as `a46c797`. Fixed on `fix/wg-strict-hooks`.

Owner's decision (2026-11-06): WireGuard hooks must be plain commands with full
paths. Shell syntax, unresolved bare commands and non-plain bare commands are
refused.

## Findings

| ID | Sev | Finding | Location (at 05b1d75) | Resolution |
|---|---|---|---|---|
| P1-1 | P1 | Hook words are checked literally, but wg-quick runs `(eval "$hook")`. Verified under `LC_ALL=C bash` to run a world-writable `/tmp/evil.sh` as root while `review()` allowed it: `echo $(/tmp/evil.sh)`, `${CONFIG_FILE%/*}/up.sh`, `sh</tmp/evil.sh` (no finding); `/tm"p"/evil.sh`, `/t\mp/evil.sh`, `/{tmp,x}/evil.sh`, `/tm?/evil.sh` (only "does not exist"); `. /tmp/evil.sh`, `source …`, `eval …`, `! …`, `{ …; }`, `if …; then :; fi` (only a "hands … to a program" warning). | `:340-345` `_path_word`, `:459-498`, `:323-325` `_INTERPRETER` | Plain-commands rule (`_wg_hook_findings`): shell characters ``$ ` \ ' " * ? [ ] { } < > ( ) ! ~ |``, control characters, leading assignments, bash keywords/builtins and wg-quick's functions, relative paths are soft blockers with "Tunnel cannot check what this hook runs; write plain commands with full paths." `;` `&&` `||` `&` stay allowed (each segment checked); a pipe is refused (it feeds data to a program that may run it as code); ` #` ends the hook as bash's comment does. An interpreter or wrapper may be given only options and numbers before the full path of its script/program. |
| P1-2 | P1 | Bare command words that do not resolve now, or contain a character outside `[A-Za-z0-9_.+-]`, are never checked: `PostUp = nosuchcmd-zz9`, `hook-%I`, `up:zz` pass with no finding. wg-quick's PATH ends with its own (user-writable Homebrew) folder (`darwin.bash:14`), so a user can create the program after the review. | `:467-475` | A bare name must be plain and resolve in `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin` to a root-owned program; anything else is refused ("write its full path"). |
| P2-1 | P2 | DNS leak hidden from the review: `DNS # = 10.64.0.1` yields `dns_servers=('10.64.0.1',)`, but wg-quick reads key `DNS` from the line cut at `#` and gets no server, so system DNS stays in use while the review prints "DNS: 10.64.0.1". | `config_inspection.py:126-139` | Keys and values read from the line cut at its first `#` (as wg-quick's `stripped`); a line with `=` only after its `#` is warned about; lines split at `\n` only; ASCII whitespace only. `[Peer] # note` now counts its peer. |
| P3-1 | P3 | EACCES on `_stat` fails open; `os.path.islink` returns False on any OSError. | `:429-432`, `:398` | Only ENOENT/ENOTDIR are "not there yet"; any other error makes the program unsafe. The walk uses `os.lstat` (`_lstat`) and propagates errors other than ENOENT/ENOTDIR. |
| P3-2 | P3 | UI freeze ~4 s: a 256 KiB config of 65 `PostUp` lines of `a;` × 1990 took 4.06 s; `shutil.which("wg-quick")` runs per segment, and the review runs on the UI thread (`ui/panels/vpn.py:743`). CHANGELOG claimed "a very long hook no longer freezes the window". | `:357` | No wg-quick lookup in the hook review at all; `HOOK_CHECK_LIMIT = 200` commands and paths per config, refused above it (hard blocker). The same config now reviews in well under 1 s (test). CHANGELOG line corrected. |
| P3-3 | P3 | `_which_privileged` searches wg-quick's folder first; wg-quick puts the system folders first. | `:353-363` | Searches only `/usr/bin:/bin:/usr/sbin:/sbin`, wg-quick's order; a name found only later on its PATH is refused. Docstring fixed. |
| P3-4 | P3 | Test gaps: mutants survived for "placeholder check only at word position 0" and "a bare hook's value shown as empty". | tests | `sh /etc/wireguard/%I.up` is refused; a bare `PreUp` shows as `PreUp: PreUp`. |

## Hand mutants (tests/test_vpn_review_gate.py, tests/test_vpn_execution.py)

First round, 34 of 34 killed (the independent review below found that three
other mutants survived; the second round adds them): each refused shell character removed on its own (`$`, `\`, `"`, `?`, `{`, `<`, `(`, `!`, `~`, `|`), `||` not exempted, control characters allowed, no comment cut, assignments allowed, shell words allowed, `.` dropped from the shell words, relative path allowed, any bare name plain, unresolved bare allowed, resolved bare not checked, interpreter arguments unchecked, `find` hands over, a nested interpreter hands over, `%I` a plain interpreter argument, ceiling off by one, ceiling not reported, wg-quick's folder searched, stat EACCES skipped, lstat EACCES skipped, placeholder check only at position 0, bare hook value empty, inspection not cut at `#`, no `#` warning, `splitlines()` restored.

## Independent review of PR #33 (Opus code-reviewer, read-only, c4bb12f)

| ID | Sev | Finding | Resolution |
|---|---|---|---|
| P0-1 | P0 | Interpreters matched by exact, case-sensitive name: on case-insensitive APFS `SH -c evilprog`, `/bin/SH -c evilprog`, `/usr/bin/ENV evilprog`, `/usr/bin/perl5.34 -eexec+evilprog` were allowed; bash 3.2 ran `evilprog`. | WireGuard: owner chose the allowlist route (below), so no interpreter list is consulted. OpenVPN: `_runs_code` matches the lowercased name with a version suffix. |
| P1-1 | P1 | The interpreter list missed many root-owned programs that run a named program (taskpolicy, xcrun, ssh -oProxyCommand=, ssh-agent, screen, dtrace -c, rsync -e, lldb, tar --use-compress-program=, zip -TT=, sandbox-exec; installer, make -f, sqlite3 -init, lockf only warned). | Owner's allowlist: `route`, `ifconfig`, `networksetup`, `pfctl`, `scutil`, `killall`, `dscacheutil`, `wg`, `sysctl`, `ipconfig`, `logger` (name as written and where a link leads, lowercased) may take plain words; any other command gets only options, numbers, full paths (checked as code) and `%i`/`%I`. `%i`/`%I` only when the program receiving them is outside the system folders (deviation from the owner's wording, stated in the PR: `dtrace -c %I` would make root run a program named after the config). OpenVPN's list gained these names. |
| P2-1 | P2 | Docs and docstring overclaimed ("exactly as the shell will"). | Rewritten; the limit is stated (options are not interpreted). |
| P2-2 | P2 | DNS still differed: wg-quick keeps an entry as a server only for `^[0-9.]+$` or one containing `:`, others are search domains; it splits on blanks. | `dns_search` added to the summary, shown as "DNS search domains"; split on commas and blanks; a wildcard in a DNS entry is warned about. |
| P2-3 | P2 | Mutants survived: `_WG_COMMENT = "#"`, `&` removed from the separators, `_items` back to `str.strip()`. | Tests for a `#` inside a word, a command after `&`, a non-ASCII space in AllowedIPs; all three now killed. |
| P3-1 | P3 | `/usr/bin/true;# note` refused although bash reads a comment. | A `#` after a blank, `;` or `&` ends the hook. |
| P3-2 | P3 | CHANGELOG wording about options before the script. | Rewritten for the allowlist. |
| P3-3 | P3 | A hook of 1990 bad commands produced 200 refusals (~94,000 characters) in the dialog. | One unsafe finding per hook. |

Second round of hand mutants: 48 of 48 killed (the 30 still applicable from the
first round, plus: allowlist always true, allowlist ignores the link target,
allowlist case-sensitive, `%i` to a system program allowed, program not updated
by a full-path argument, any argument plain, paths off the allowlist treated as
data, many findings per hook, OpenVPN case-sensitive, OpenVPN without version
suffix, comment cut only after a blank, comment cut anywhere, `&` not a
separator, every DNS entry a server, DNS split on commas only, DNS wildcard not
warned, `_items` strips Unicode spaces, search domains not shown). The
reviewer's probe set (28 hooks) is now refused except `perl5.34 -e 1`, a no-op.

### Sourcery review of PR #34 (e387e51)

| Comment | Sev | Finding | Resolution |
|---|---|---|---|
| 4488194035 | Critical | `/bin/sh 1`: a number passed to an interpreter names a file (cwd, or PATH). | Numbers refused for any command off the allowlist. |
| 4488194048 | Critical | `%I` given to a root-owned interpreter outside the system folders (e.g. MacPorts `/opt/local/bin/python3 %I`) runs a file named after the config. | `%i`/`%I` refused for any command off the allowlist; the system-folder exception is gone. |
| 4488194058 | Critical | `_in_system_folder` compared case-sensitively, so `/USR/SBIN/dtrace -c %I` passed. | Removed with the exception above; the allowlist path compare is lowercased. |
| 4488194065 | Critical | A root-owned wrapper named `route` elsewhere got unrestricted arguments. | Each macOS tool is trusted only at its own path (as written and where a link leads, any case); `wg` by name, also judged by where a link leads. |
| 4488194070 | Critical | `/usr/sbin/dtrace -o /etc/hosts -c %I`: any full-path argument became "the program", so `%I` passed. | Gone with the exception above. |
| 4488194079 | Medium | A wildcard DNS entry may expand (extglob is on); it was classified literally. | Listed as neither server nor search domain, with a warning; extglob `@( +( !(` included. |
| 4488194087 | Medium | Thousands of bad Address/DNS tokens gave thousands of warnings in the dialog. | Config warnings deduplicated and capped at 20 plus a count. |

Third round of hand mutants: 53 of 53 killed (adds numbers plain, placeholders
plain, allowlist path not pinned, allowlist ignores the link target, allowlist
path and name case-sensitive, DNS glob and extglob not detected, warnings not
capped, warnings not deduplicated).

### Independent review of fbf36d5 (Opus code-reviewer, read-only)

| ID | Sev | Finding | Resolution |
|---|---|---|---|
| P0 | P0 | PR #34 was merged (0f77e90) before fbf36d5 was pushed; `main` accepts `/bin/sh 1`, `/usr/bin/env /etc/wireguard/up.sh %I`, `/USR/SBIN/dtrace -c %I`. | New PR from this branch. |
| P1-1 | P1 | The allowlist pins `/sbin/route` by realpath; on merged-/usr Linux (CI) it resolves to `/usr/sbin/route`. | `_realpath` indirection; `tests/conftest.py` pins a Mac's layout only when the host moves the pinned paths. Checked with a merged-/usr simulation plugin: 857 passed. The pin is unchanged. |
| P1-2 | P1 | Allowed hooks still ran user files as root: `/bin/zsh …` (~/.zshenv), `/bin/bash -l`, `/usr/bin/python3` (.pth), `/usr/bin/make -s` (./Makefile in the checkout the launcher chdirs to). | Owner's choice: `wg_quick_command` is `cd / && /usr/bin/env -i HOME=/var/root PATH=… wg-quick …`; a relative config path is made absolute. A test runs the exact prefix unprivileged and sees only `/`, HOME, PATH. |
| P2-1 | P2 | `DNS = 10.64.0.*` allowed, shown as "system DNS stays in use". | A DNS wildcard is a Connect blocker; the line says "Unknown — an entry has a wildcard". |
| P2-2 | P2 | 25 junk lines pushed the wildcard warning past the cap. | Ignored lines are one warning per kind with line numbers; the "'=' after '#'" warning comes first. |
| P3-1 | P3 | `pfctl -f rules.conf` reads a file in the user-owned working directory. | Closed by `cd /`. |
| P3-2 | P3 | `--opt=value` refusal untested. | `/usr/bin/tar --use-compress-program=evilprog` alone is a test case. |
| P3-3 | P3 | Realpath-pin mutants survived. | Test with `_realpath` mapped both ways. |
| P3-4 | P3 | `wg` trusted by name alone. | Documented (root must have installed it); not pinned. |
| P3-5 | P3 | `DNS_`/`D-NS` counted as DNS. | [Interface] keys compared trimmed and lowercased, as wg-quick does; [Peer] keys with blanks removed, as `wg setconf` does. |
| P3-6 | P3 | `Address` globs not flagged. | Warning. |
| P3-7 | P3 | Ordinary hooks now refused (`/etc/wireguard/up.sh %i`, `/bin/kill -HUP 123`, `/sbin//route`). | Documented in `docs/agents/vpn.md`. |
| — | — | Not verified by the reviewer: search domains without a server. | Checked in `darwin.bash` (line 468: `set_dns` only when `${#DNS[@]} -gt 0`); the review marks them "not applied". |

Fourth round of hand mutants: 64 of 64 killed (adds: no `cd /`, env not cleared,
HOME kept, relative target kept, DNS wildcard not a blocker, wildcard says
system DNS, search not-applied note gone, Address glob not warned, DNS key
loose, peer key keeps blanks, "'#'" warning not first).

### Residual after the allowlist

What a program does with its options is not interpreted: an option that takes a
program name (`dtrace -c -x`)
still names a program found on wg-quick's PATH. The only folder on that
PATH a normal user might write is wg-quick's own (`${SELF%/*}`), and a user who
can write it can already replace `wg-quick` itself, which runs as root; both
are closed by the root-owned copy (audit step S7, TODO.md). OpenVPN is run the
same way since `fix/openvpn-env-scrub` (`openvpn_manager.start_command`: `cd / &&
/usr/bin/env -i HOME=/var/root PATH=…`, every path absolute). OpenVPN `execve`s its
scripts with only its own variables (`run_command.c`, `make_env_array`), so the
scrub reaches openvpn itself (`OPENSSL_CONF`/`OPENSSL_MODULES`) and the working
directory its scripts inherit (`daemon(1, …)` keeps it): a bare `up up.sh` now
resolves to `/up.sh`, which only root can create, not to the checkout. Its scripts
otherwise keep the earlier rules; the review still does not check a relative
OpenVPN program (TODO.md).

## Not verified

No real wg-quick and no bash 5 on this Mac (bash 3.2 only); the rule was
checked against wireguard-tools' `darwin.bash` source (PATH on line 14,
`unstripped_value` for hooks, `stripped` for DNS, `extglob` on,
`(eval "$hook")` after `%i`/`%I` substitution), not by running it. No live
tunnel was brought up.

## Residual, not fixed here

- A root-owned program that runs one of its *data* arguments (`/usr/bin/find
  … -exec`, `launchctl`, a script that execs `$1`) still only draws a warning
  for a user-writable path argument; only the interpreter/wrapper list treats
  arguments as code.
- OpenVPN directives keep the earlier rules (OpenVPN runs the program without a
  shell); the plain-commands rule is WireGuard only.

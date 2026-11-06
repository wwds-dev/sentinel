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

34 of 34 killed: each refused shell character removed on its own (`$`, `\`, `"`, `?`, `{`, `<`, `(`, `!`, `~`, `|`), `||` not exempted, control characters allowed, no comment cut, assignments allowed, shell words allowed, `.` dropped from the shell words, relative path allowed, any bare name plain, unresolved bare allowed, resolved bare not checked, interpreter arguments unchecked, `find` hands over, a nested interpreter hands over, `%I` a plain interpreter argument, ceiling off by one, ceiling not reported, wg-quick's folder searched, stat EACCES skipped, lstat EACCES skipped, placeholder check only at position 0, bare hook value empty, inspection not cut at `#`, no `#` warning, `splitlines()` restored.

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

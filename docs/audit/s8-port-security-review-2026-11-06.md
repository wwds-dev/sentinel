# Security review: S8a/S8b port (branch `fix/s8-openvpn-root-copy`, uncommitted, base `9f42ff1`)

Reviewer: security-engineer (independent, read-only). Date 2026-11-06.
Playbook `docs/playbooks/security-engineer.md` is `status: baseline` (last-reviewed 2026-10-10): stale per AGENTS.md.

## Verdict
**Not ready to merge as is: 2 P1 and 2 P2.** The root script itself holds up. I found no way
for user bytes, paths, symlinks, FIFOs or argv to make `OVPN_SCRIPT` run, load or write
something outside the copies, and no way to get a signal sent to a process that is not
Sentinel's openvpn or the attacker's own. Both P1s are gaps in the review, the model of
OpenVPN that decides what root may run:
- a line longer than OpenVPN's 255-byte read buffer can end an inline block early and hide
  directives;
- `show-pkcs11-ids` loads a library as root and is on no list.

The P2s are root code paths that the review neither checks nor tracks:
- the interpreter on a hook script's `#!` line;
- what openvpn loads implicitly. This one was an owner deferral on the old line and was lost
  in the port.

## Threat model (short)
- **Assets:** root code execution and root file writes; the integrity of the reviewed config
  and its files; key and password material in the copies; kill-switch correctness.
- **Trust boundaries:** the user's processes (same uid as Sentinel) and the root bash started
  by the admin dialog (`env -i`, constant script, positional argv); the root-owned base
  `/private/var/run/sentinel-vpn` and the user's profile folder; the VPN peer and the network
  path and openvpn's log, which root parses.
- **Entry points:** the `.ovpn` and its data files (user-writable until root copies them);
  the review (`vpn_execution.review_execution`/`execute`); `OVPN_SCRIPT` argv; root's pid and
  device files; legacy user-side pid, device and log; `ps` output; openvpn's own implicit
  loads (dylibs, `openssl.cnf`, modules, 2.7 `dns-updown`).

## Findings

### P1-1: A line over 255 bytes inside an inline block hides directives from the review (OpenVPN side NOT VERIFIED by execution)
**Location:** `services/vpn_execution.py:344-351`. Inside a block, `_scan` closes only on an
exact `</tag>` line and refuses any other line that *starts* with `</`. No line length is
checked anywhere, and `HOOK_LINE_LIMIT` applies to hook keys only.

**OpenVPN side:** sourced, not run. `read_inline_file` reads each line into a fixed
`OPTION_LINE_SIZE` (256) buffer with `fgets`, and ends the block when a chunk, after leading
spaces, starts with the close tag (`strncmp` prefix). Sources: OpenVPN doxygen
`options_parse.c` and the in-repo comment at `_scan` ("OpenVPN may close such a block
early"). The S8b security review already listed the 256-byte question as unverified.

**Attack:** a process running as the user edits the user's `.ovpn` before the user presses
Connect. It adds:
```
<ca>
<valid PEM>
AAAA…(255 × A)</ca>
script-security 2
up /Users/<user>/evil.sh
<ca>
<valid PEM>
</ca>
```
- **What the review sees:** one `<ca>` block. The probe (`_hook_findings`, `hooks_from_text`,
  `_openvpn_file_names`) returned no blockers, no hooks and no warnings, so the dialog shows
  no "RUNS COMMANDS AS ADMINISTRATOR" section.
- **What OpenVPN reads:** chunk 1 is the 255 A's and chunk 2 is `</ca>`, so the block ends.
  `script-security 2` and `up …` are live directives. The second `<ca>` replaces the first,
  last one wins.
- **What root does:** it copies and hashes exactly these bytes, and the hash matches. `programs`
  does not contain `evil.sh`, so root's re-check never looks at it. openvpn runs `evil.sh` as
  root at tunnel up.

The same applies to `<connection>` blocks, which use the same reader. A top-level variant
(`setenv-safe X AAAA…up /…`) also passes the review. It works only if OpenVPN does not treat an
over-long top-level line as fatal (NOT VERIFIED; the source warns at that length).

**Impact:** a same-user process gets root code execution behind a dialog that shows no hooks.

**Fix:** in `_scan`, for OpenVPN, block any line whose UTF-8 byte length, including `\r`,
is 255 bytes or more (keep a margin, for example over 250). Apply it inside inline and
`<connection>` blocks too. Count bytes, not characters. PEM lines are 64 bytes, so real
profiles are unaffected. Add a test for each shape.

### P1-2: `show-pkcs11-ids <library>` loads a library into root's openvpn and only gets the generic "not on the reviewed list" warning (OpenVPN side NOT VERIFIED by execution)
**Location:** `services/vpn_execution.py:132` (`OPENVPN_CODE_KEYS = ("engine",)`), `:997`,
`:1031`. `_openvpn_programs` does not list it either, so root never re-checks it
(`services/vpn_rootscript.py:369`).

**OpenVPN side:** sourced, not run. The option takes `[provider] [cert_private]` behind
`VERIFY_PERMISSION(OPT_P_GENERAL)`, so it is allowed in a config file. OpenVPN acts on it
while parsing (`show_pkcs11_ids` adds the provider through pkcs11-helper, which `dlopen`s it,
then exits). Homebrew's openvpn is built with pkcs11-helper. With no argument it falls back to
`DEFAULT_PKCS11_MODULE` where one is compiled in.

**Attack:** the same pre-review edit adds `show-pkcs11-ids /Users/<user>/evil.dylib`. The
probe gave blockers `[]`, soft `[]`, and one warning: "OpenVPN directives not on the
reviewed list … show-pkcs11-ids". If the user confirms, root loads the dylib before
`--daemon` takes effect.

**Impact:** the same class as S8b security P1-3 and S8b review P1-1, which put
`engine`/`dns-updown` and friends on lists.

**Fix:** add `show-pkcs11-ids` to `OPENVPN_CODE_KEYS` (always block; it is a standalone
diagnostic, never part of a client profile). Consider blocking every standalone `show-*`,
`test-crypto` and `genkey`-style mode by name.

### P2-1: A root-owned hook script's `#!` interpreter (and a hook binary's linked libraries) is never checked; root runs it
**Location:** `services/vpn_execution.py:857-894` (`_ovpn_program_findings` checks the
program file, the system-program location and interpreter names) and
`services/vpn_rootscript.py:333-343,369-374` (`root_owned` re-checks the same file only).
Nothing reads the first line of a hook program or `otool -L` of a binary or plugin.

**Probe:** `up /…/up.sh`, where `up.sh` is reported as root-owned through an injected
`_stat`, starts `#!/opt/homebrew/bin/python3`, and has `script-security 2`. Result:
`_hook_findings` returned `([], [], [])` and `_openvpn_programs` returned `['…/up.sh']`.
`/opt/homebrew/bin/python3` here is owned by `as` (it links into the Cellar).

**Attack:** the owner keeps a root-owned hook such as `/usr/local/libexec/vpn-up` whose `#!`
names a Homebrew interpreter (bash 5, python3). A same-user process replaces that
interpreter. At the next approved Connect, or at Disconnect's down hook, root runs it. The
review says nothing and root's re-check passes.

**Impact:** same-user to root, with no warning. This breaks the review's stated rule that
only programs root owns, in folders only root can write, run as root.

**Fix:** in the review, read up to 256 bytes of each hook program. If it starts with `#!`,
apply `_unsafe_executable_reason` to the interpreter, and to the program `env` would resolve
on root's PATH. Refuse or warn on `env -S` and on interpreters outside the system folders.
For Mach-O hooks and plugins, at least warn when `otool -L` lists a path that is not
root-owned. Mirror the interpreter check in `root_owned` so root's re-check matches.

### P2-2: What openvpn loads implicitly is unchecked, and the owner's deferral of it (old S8b security P3-2, "D10 follow-up", P2 `@me`) did not survive the port
**Location:** `services/vpn_execution.py:1628-1635` (the warning looks at the openvpn
executable only). `:136` accepts `providers legacy` and `fips`, which OpenSSL loads as
modules from `MODULESDIR`, and the comment presents this as safe. `TODO.md`, spec §15
"Limits that remain" and the ADR amendment do not mention OpenSSL. The old line's
`TODO.md:141` (branch `claude/s8-r7-detached`) did.

**Evidence:**
- On this Mac `openssl version -d -m` reports `OPENSSLDIR "/opt/homebrew/etc/openssl@3"`
  (owner `as`, and `openssl.cnf` is owned by `as`) and a `MODULESDIR` in the user-owned
  Cellar.
- OpenVPN's `crypto_init_lib` calls `OPENSSL_init_crypto(OPENSSL_INIT_LOAD_CONFIG, NULL)`
  outside Windows. It was disabled on Windows because a writable `openssl.cnf` gave
  local-to-SYSTEM.
- `env -i` clears `OPENSSL_CONF`, but not the compiled-in paths.
- OpenVPN 2.7 also runs its default `dns-updown` script from the install's `libexec` when DNS
  options are present (NOT VERIFIED here).

**Attack:** the user follows the openvpn warning and makes openvpn root-owned (for example
`sudo chown root` on the keg, or a root-owned build linked to Homebrew's OpenSSL), and the
warning disappears. A same-user process then adds a `[provider_sect]` with
`module = /Users/<user>/evil.dylib` to `openssl.cnf`, or replaces `legacy.dylib` or
`libssl.3.dylib`. Root loads it at the next Connect.

**Impact:** same-user to root with no warning in exactly the setup the warning steers toward.
For a plain Homebrew openvpn the existing warning covers this risk class, but its text ("not
the tool") does not say so.

**Fix:**
- Restore the deferral in `TODO.md` and spec §15 "Limits that remain".
- Widen the openvpn warning: check `otool -L` paths, `OPENSSLDIR/openssl.cnf`, `MODULESDIR`
  and 2.7's `dns-updown` with `_unsafe_executable_reason`, or at least name them in the
  warning text.
- Drop `legacy` and `fips` from `OPENVPN_PROVIDERS`, or tie them to that check.

### P3-1: Peer-controlled log text can choose the tunnel device the kill switch passes
**Location:** `services/vpn_rootscript.py:383-392` (an unanchored `grep -Eo` for
`(Opened utun device|TUN/TAP device)\s+(u?tun)N` anywhere in the log; the loop stops at the
first match) and `services/vpn_connection.py:297-301` (`extra_interfaces=[device]`).

**Probe:** I ran `OVPN_SCRIPT` under bash as my user with a stub openvpn. The stub's log first
holds `VERIFY ERROR: depth=0, error=…: CN=TUN/TAP device utun0, serial=1`, then 1.2 s later
`Opened utun device utun7`. The recorded device was **`utun0`**.

OpenVPN logs the peer's certificate subject at verb 1-3 for both VERIFY OK and VERIFY ERROR,
and pushed options at verb 3 (sourced, not run). That puts the text in the hands of the
server, or of a network MITM when the profile has no `tls-auth`/`tls-crypt`.

**Impact:** pf passes an unrelated utun and blocks the real one. That fails closed, so this
is a denial of service plus a wrong "armed" state, not a clear-net leak.

**Related:** the legacy path (`services/openvpn_manager.py:215-226`) takes the device from a
user-writable file or log, with the same effect.

**Fix:** match only openvpn's own line, `^(YYYY-MM-DD HH:MM:SS )?(Opened utun device utunN|TUN/TAP device tunN opened)$`,
allowing for `suppress-timestamps`. For legacy tunnels, prefer `ifconfig` or `route` evidence
over the user-side file.

### P3-2: The other owner deferral, S8b security P3-4 (manifest hashes visible to `ps`), was also dropped from tracking
**Location:** `services/vpn_rootscript.py:512-513` (hashes in argv) and
`agents/vpn_agent/services/privileged.py:9-11` (the header still says nothing secret passes
this way). The old line's `TODO.md:132` P3 item is not in this branch's TODO or spec.

**Impact:** an offline guess of a low-entropy `auth-user-pass` file is possible while the
dialog is open and for up to 15 s of the start. This is unchanged from the old line, and only
the tracking was lost.

**Fix:** restore the TODO item and correct the `privileged.py` header.

### P3-3: The 20 s read cut-off is per file, so an approved start can land minutes later (NOT VERIFIED; from reading the code)
**Location:** `services/vpn_rootscript.py:320-330,352-368`. The script can wait up to 30 s
on `lockf`, then make up to 17 reads of up to 20 s each, then wait 15 s for the device. That
is about 6.5 min against the dialog path's 30 s command timeout.

**Attack:** the profile folder is on a filesystem the user controls (an SMB, WebDAV or FUSE
mount), and each read is answered after 19 s.

**Impact:** Sentinel reports "Timed out… may still have happened" and the tunnel comes up
later with the reviewed bytes. There is no integrity loss, only state confusion. This is the
S7 P3-B residual multiplied by the file count.

**Fix:** use one deadline for all reads, for example a start timestamp checked in `grab`.

## Prior P1/P2 fixes checked in this port (all by code reading; the ones marked "probe" were also exercised)
| Prior finding | State here |
|---|---|
| S8b-sec P1-1 BOM | Ported (`_scan` skips U+FEFF; name regex). |
| S8b-sec P1-2 tokeniser | Ported (`_ovpn_split` refuses quote/backslash shapes; hook values re-split). |
| S8b-sec P1-3 dns-updown etc. | Ported (hook and executable keys; server keys block). |
| S8b-sec P2-1 dev-node/genkey/… | Ported (blockers; `dev`/`dev-node` regexes). |
| S8b-sec P2-2 absolute files | Ported (`_openvpn_includes` requires root-owned all the way up; root hashes in place). |
| S8b-sec P3-2 / D10 | **Not carried over** (P2-2 above). |
| S8b-sec P3-4 | **Not carried over** (P3-2 above). |
| S8b review P1-1 library directives | Ported (`engine` blocks; `providers` allowlist; `pkcs11-providers`/`plugin` checked). `show-pkcs11-ids` missed (P1-2). |
| S8b review P2-1 manifest gaps | Ported (`<connection>` lines scanned; socks/http-proxy auth word; `capath` dir blocks as non-regular). |
| S8b review P2-2 replay-persist | Ported (blocker). |
| Re-review R-P1-1/R-P1-2 | Ported (hook program absolute, existing, root-owned, not system, not interpreter; relative/`..` arguments refused). |
| R5-P1-1, R5-P2-1, R5-P3-1 | Ported (`_fold`, versioned names, location rule; down-root args judged as a command; `_paths_touched` walks link folders). |
| R7-P1-1, R7-P2-1, R7-P2-3 | Ported (`/.` prefixes unsafe; `/library/developer/` in system dirs; programs, args and libraries must exist). |
| R5-P2-2, R7-P2-2 (WireGuard) | Not ported, documented in spec §15 as superseded by main's allowlist. Accepted as documented, not re-reviewed. |
| S8a P2-1, P3-2, P3-4, P3-5 | Ported (`_forget_legacy`; 60 s legacy rule; CR fix in `privileged.py`; spaces refused in the openvpn path). |

## What I verified
- Both suites pass: `pytest tests/test_vpn_rootscript.py tests/test_vpn_review_gate.py`, 727 passed in 32 s.
- Probes (scratch dir outside the worktree, run as my user, nothing as root, no real openvpn):
  - the long-line smuggle and `show-pkcs11-ids` against the review functions;
  - the shebang hook with injected root ownership;
  - the device-line injection through `OVPN_SCRIPT` with a stub openvpn.
- `OVPN_SCRIPT` by reading:
  - argv validation (exit 99);
  - path normalisation (`//`, `..`, `.`, `/.`);
  - the copy stays inside `cfg/`;
  - the regular-file check plus hash means a symlink, FIFO or device swap ends in exit 98;
  - `env -i` with its own PATH and HOME;
  - the `lockf` re-exec;
  - identity checks in `tracked`/`recorded_pid`;
  - the legacy pid path cannot be chosen by the user (Python derives it).
- `ps` spoofing only lets root signal the attacker's own process. A fake `openvpn … --writepid <root pid file>` process can at most make Connect refuse with 94 (DoS).
- The dialog path passes `DIALOG_ENV` only (`privileged.py:30`), so SHELLOPTS, PS4 and BASH_ENV from `launchctl setenv` do not reach root's `/bin/sh`.
- OpenVPN-file directives that write as root (`status`, `log*`, `writepid`, `replay-persist`, `tmp-dir`, `management*`, `chroot`, `cd`, `genkey`, `tls-export-cert`, `ifconfig-pool-persist`) all block.
- Re-read files (`crl-verify`, `auth-user-pass`, tls keys on SIGUSR1) resolve to root's copies under `--cd`, or to root-owned absolute files.
- No root-owned path under `/Library`, `/usr/local`, `/opt`, `/private/etc`, `/private/var` or `/Applications` (depth 2) carries an ACL that grants write, so the review ignoring ACLs is not exploitable there today.
- Dependencies: the change touches no manifest. `pip-audit` and `osv-scanner` are not installed, so no CVE scan was run.

## NOT VERIFIED
- All OpenVPN behaviour: the inline-reader chunking (P1-1), `show-pkcs11-ids` handled at parse time (P1-2), log lines at verb 3 (P3-1), `--cd`/`--config` ordering, SIGUSR1 re-reads, 2.7 `dns-updown`, and whether OpenVPN loads `openssl.cnf` on macOS builds. openvpn is not installed on this Mac. Check each against `man openvpn` and the 2.6/2.7 source, or a VM, before or with the fix.
- Behaviour under `do shell script … with administrator privileges` (stdin type, environment). A side note from the harness: when stdin is a socket, the `lockf` re-exec of bash sources `$HOME/.bashrc`, which is `/var/root/.bashrc` (root-owned in production, so not a finding).
- P3-3 timing on a real slow filesystem.

## Resolution (author, 2026-11-06; details in spec §15 "Round 2")
| Finding | Status |
|---|---|
| P1-1 | Fixed: OpenVPN lines of 250 bytes or more (UTF-8 bytes, inline blocks included) block; tests incl. the `<ca>` smuggle. |
| P1-2 | Fixed: `show-pkcs11-ids` in `OPENVPN_CODE_KEYS`. |
| P2-1 | Fixed for OpenVPN: a hook script's `#!` interpreter (or `env`'s name in the system folders) must be root-owned and is re-checked by root. WireGuard and linked libraries: TODO.md (P3). |
| P2-2 | Deferred by the owner on the old line (2026-11-05); restored to TODO.md as the D10 follow-up (`@me`) and named under "Not protected" in `docs/agents/vpn.md`. |
| P3-1 | Fixed: `--suppress-timestamps` and whole-line device match; tests with server-controlled text. Legacy logs keep the loose pattern (spec §15). |
| P3-2 | Restored to TODO.md (deferred by the owner 2026-11-05). |
| P3-3 | Fixed: one 30 s deadline across all reads; test with a shortened deadline. |


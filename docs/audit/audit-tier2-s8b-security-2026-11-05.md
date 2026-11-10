# Security review: audit tier-2 S8b (root runs only copies of the reviewed OpenVPN files)

**Scope:** commits `bd6b27f`, `04ad750` and `dce6b19` on `claude/intelligent-archimedes-e15a0e`: `services/vpn_rootscript.py` (`OVPN_SCRIPT`, `include_path`, `_includes`, `_ovpn_argv`, `ovpn_up_argv`), `services/vpn_execution.py` (`_scan`, `_ovpn_tokens`, `_openvpn_includes`, `_library_findings`, `_hook_findings`, `ExecutionReview.fingerprint`/`includes`, `execute`), `services/openvpn_manager.py` `connect`, `services/vpn_connection.py` `connect`, and the dialog path in `agents/vpn_agent/services/privileged.py`. Read against ADR 0004 (§1, §3, §7, Residuals), spec §1.3 and §17, and the independent review `audit-tier2-s8b-review-2026-11-05.md`, whose findings are not repeated here unless a fix is shown to be incomplete.
**Threat model:** a process running as the logged-in user that can edit any user file (including the `.ovpn` before the user opens the dialog) and race the administrator dialog; the goal it must not reach is root running, loading or reading anything the review did not bind.
**Reviewer:** security-engineer, read-only. Nothing was run as root, no tunnel, kill-switch, MAC or network action was taken, and no code was changed. The only file written is this report.
**Playbook:** `docs/playbooks/security-engineer.md`: `status: baseline`, `last-reviewed: 2026-10-10` (26 days old). **Stale by status**: it is still the seeded baseline and has never been verified by a radar run.
**App context:** `docs/company/app-context.md` is untracked in this worktree, so I read the main checkout's copy.

## What was run
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_rootscript.py tests/test_vpn_review_gate.py tests/test_vpn_connection.py tests/test_vpn_execution.py`: **461 passed in 18.76 s**.
- Probes in the session scratchpad. Each imports the worktree code under `python -I` and runs it on configs written to a temp folder:
  - `_hook_findings`, `hooks_from_text`, `_read_config` and `_openvpn_includes` on crafted configs (outputs quoted in the findings).
  - `OVPN_SCRIPT` under real `/bin/bash` 3.2 as the user, with a stub openvpn and `base` in a temp folder (the same harness shape as the tests).
  - The dialog quoting path: `shlex.join` → `privileged.applescript_string` → `osascript -e 'do shell script …'` **without** `with administrator privileges`, under `dialog_env()`, with `/usr/bin/printf` as the program.
- Read-only facts about this Mac: `ls -l /bin/ps` gives `-rwsr-xr-x root wheel` (setuid). `ps -ax -o user=,command=` as the user prints the arguments of root processes (for example `/usr/sbin/securityd -i`). `ls -l /usr/lib/ssh-keychain.dylib` gives `root wheel 0755`. `ls -l /opt/homebrew/etc/openssl@3/openssl.cnf` gives `as admin` (owned by the user). openvpn is **not installed** here.
- Dependencies: the three commits change no dependency manifest (`git diff --stat ed21dc6..dce6b19 -- 'requirements*' Sentinel.spec` is empty). No audit tool is installed (`pip-audit`, `osv-scanner` not found), and one would query the network, so no CVE scan was run.

## Verdict

**No P0. Three P1, two P2, five P3.**

The S8b machinery holds:
- Constant script, argv values, `env -i`, absolute tool paths.
- Copies go into a fresh root `0700` tree; size and hash are checked; any failure removes `private/` and exits 98 before openvpn runs.
- `--cd` comes before `--config`.
- No fail-open path: every Python error raises before the prompt, and every root check ends in `unverified`/`refuse`, which `exit`s from the main shell.

The dialog path is clean. Every value tested came back from `do shell script` **byte-identical**, and nothing executed: spaces, `'`, `"`, `$(…)`, backticks, a leading `-`/`--`, `*`, backslashes, NFC and NFD `café`, U+2028, U+0085, U+00A0, `;&&|`, `!`, `~`, `${HOME}`. Names with a leading dash, quotes and `$(…)` were copied correctly by root (`-x y'$(id)`.crt` landed in `cfg/` verbatim, exit 0).

What breaks the goal is the **review**: it still reads a different config than OpenVPN parses.
- A UTF-8 byte-order mark hides the first directive (P1-1).
- A quote placed against other text splits into two tokens for OpenVPN and one for Sentinel (P1-2).
- Several directives that run programs (P1-3) or open and write files (P2-1) are on no list, or on the "known" list.
- Residual D2 is wider than "certificates or keys" (P2-2).

Each gives root a program, library or file the review never bound, and none needs a race against the copy.

## Findings

### P1-1: A UTF-8 byte-order mark hides the first line's directive (including `config`) from the review
**Location:** `services/vpn_execution.py:325` (`data.decode("utf-8")`, which keeps U+FEFF), `:302` (`_HIDDEN_CONTROL` does not cover U+FEFF), `:268` (the key is the first token, BOM included), `:493-503` (blocker and hook lookup by exact key).

**Evidence:**
- Probe: the file bytes start `ef bb bf` and read `config /Users/as/other.ovpn`. `_read_config` gives no blocker; `_hook_findings` gives `blockers [] soft []`, and the only warning is `OpenVPN directives not on the reviewed list …: ﻿config`. The BOM is invisible in the dialog, so this renders as "…: config".
- The same file without the BOM is blocked: "The OpenVPN directive 'config' can hide commands…".
- With `﻿pkcs11-providers /Users/as/evil.dylib`: no blocker and no soft blocker. The line is not listed under "RUNS COMMANDS AS ADMINISTRATOR".
- OpenVPN skips a BOM at the start of a config file. This dates from a 2013 options.c commit and is still present in the current `read_config_file` in `options_parse.c` ([doxygen source](https://build.openvpn.net/doxygen/options__parse_8c_source.html), [2013 patch](https://sourceforge.net/p/openvpn/mailman/message/30452339/)). Not run here: openvpn is not installed.

**Attack scenario:** the malware prepends `EF BB BF` and `config /Users/me/.cache/x.ovpn` to the user's profile. The user clicks Connect and sees one generic "not on the reviewed list: config" line. They confirm and type the password. Root's openvpn reads the nested config straight from the user's folder: never hashed, never shown, editable at any time. With `script-security 2` and `up /Users/me/.cache/x.sh` in it, that is arbitrary code as root. The same prefix hides any single blocker or hook: `management`, `log`, `status`, `replay-persist`, `pkcs11-providers`, `up`, …

**Suggested fix:**
- Mirror OpenVPN: strip one leading U+FEFF before scanning (hash the raw bytes as now).
- Independently, block any directive name that does not match `^-{0,2}[A-Za-z0-9][A-Za-z0-9-]*$`. Then no invisible or confusable character can rename a directive.
- Tests: a BOM before `config`, before `pkcs11-providers` and before `up`, plus a zero-width character inside a name.

### P1-2: Sentinel's tokeniser and OpenVPN's disagree on quotes against other text, which defeats the library, program and file checks
**Location:**
- `services/vpn_execution.py:191`: `_ovpn_tokens` opens a quote in the middle of a token and keeps one token after the closing quote, as a shell would.
- `:352-353`: `_segments` splits an OpenVPN command on whitespace only.
- `:394`: `_unsafe_executable_reason` skips missing path components, so a made-up path under root-owned directories is judged safe.
- `:454-455`: `_library_findings`.
- `:525-530`: `_openvpn_includes` takes the file from a fixed word index.

**Evidence:** OpenVPN's `parse_line` recognises a quote only at the start of a token, and a closing quote ends the token (`STATE_READING_QUOTED_PARM` → `STATE_DONE`) whether or not a space follows ([doxygen source](https://build.openvpn.net/doxygen/options__parse_8c_source.html), [options.c](https://github.com/OpenVPN/openvpn/blob/cdc65ea0f1f94974f55352d794627561c78c4151/src/openvpn/options.c)). Script strings are re-split by the same `parse_line` (`argv_parse_cmd`; from source as I recall it, not run). Probe outputs, on the commit code:

a. `pkcs11-providers "/usr/lib/ssh-keychain.dylib"/Users/as/evil.dylib`
   - Sentinel sees one word, `/usr/lib/ssh-keychain.dylib/Users/as/evil.dylib`, and reports `findings: ([], [], [])`: no blocker and no warning.
   - OpenVPN sees two providers: `/usr/lib/ssh-keychain.dylib`, a real root-owned PKCS#11 module on this Mac, then `/Users/as/evil.dylib`. pkcs11-helper dlopens both as root.
   - This bypasses the round-2 P1-1 fix.

b. `up '"/bin/sh"/Users/as/evil.sh'` (with `script-security 2`)
   - Sentinel gives `blockers [] soft []` and only the warning "The up hook names /bin/sh"/Users/as/evil.sh, which does not exist."
   - OpenVPN's second parse gives `/bin/sh /Users/as/evil.sh`, so root runs a script in the user's home.
   - The same value without the quotes, `up '/bin/sh /Users/as/evil.sh'`, is correctly a soft blocker.

c. `http-proxy "127.0.0.1"8080 /Users/as/creds basic`
   - Sentinel's words are `['127.0.0.18080', '/Users/as/creds', 'basic']`. The manifest is `['ca.crt', 'basic']`, with no absolute-path (D2) warning.
   - OpenVPN reads server `127.0.0.1`, port `8080`, authfile `/Users/as/creds`, method `basic`.
   - Root's openvpn therefore reads an unlisted, unhashed file from the user's folder.

**Attack scenario:**
- (a) and (b): the malware edits the profile so that the line looks like a known-safe provider or wrapper. After confirmation it drops any dylib or script at the second path. Root loads or runs it. The review's stated rule, "only programs that root owns are accepted", is not enforced.
- (c): see P2-2 for what an unbound credential file gives the attacker. Here it comes without even the D2 warning.

**Suggested fix:**
- Make `_ovpn_tokens` reproduce `parse_line` exactly: quotes only at the start of a token, end the token at the closing quote. Better still, make any quote that is not at the start of a token, or that is followed by a non-space character, a **blocker** ("write it with spaces around the quotes"), the same way quoted directive names are blocked today.
- Parse OpenVPN script values a second time with the same tokeniser instead of `str.split()`.
- In `_unsafe_executable_reason`, `_program_findings` and `_library_findings`, treat a program or library path that does not exist as a blocker, not a warning. It would fail at start anyway, and "does not exist" is exactly the signature of a parse mismatch.
- Tests: the three lines above, plus `"a"b` splitting in a file directive at index 0, which should give an OpenVPN error rather than a different file.

### P1-3: Directives that make root run a program are on no list (`dns-updown`, `tls-crypt-v2-verify`, `client-crresponse`)
**Location:** `services/vpn_execution.py:88-90` (`OPENVPN_EXECUTABLE_KEYS`), `:69-72` (`OPENVPN_HOOK_KEYS`), `:506-509` (unknown keys are only warned about).

**Evidence:** the probe gives:
- `script-security 2` + `dns-updown /Users/as/evil.sh`: hooks `['script-security: 2']`, `blockers [] soft []`, and the warning "not on the reviewed list …: dns-updown".
- `mode server` / `tls-server` / `script-security 2` / `tls-crypt-v2-verify /Users/as/evil.sh`: the same, with "…: mode, tls-server, tls-crypt-v2-verify".

On the OpenVPN side:
- `--dns-updown cmd` exists in OpenVPN 2.7 (released; it ships a default per-platform script for macOS) ([Changes.rst 2.7.0](https://sources.debian.org/src/openvpn/2.7.0-1/Changes.rst), [script-options.rst](https://sources.debian.org/src/openvpn/2.7.0-1/doc/man-sections/script-options.rst)).
- `--tls-crypt-v2-verify` and `--client-crresponse` (2.6) are server-side script hooks.

None of these was run here.

**Attack scenario:**
- The program is never ownership-checked, and it is not shown in the "RUNS COMMANDS AS ADMINISTRATOR" list (only `script-security: 2` is), so the malware swaps `/Users/as/evil.sh` after confirmation.
- For `tls-crypt-v2-verify`, the malware then connects to the local server as a client to trigger the script.
- This is the same class as the round-2 P1-1 (a root code path with only the generic warning), which was rated P1.

**Suggested fix:**
- Add `dns-updown`, `tls-crypt-v2-verify` and `client-crresponse` to both `OPENVPN_HOOK_KEYS` and `OPENVPN_EXECUTABLE_KEYS`. For `dns-updown`, allow the values `disable` and `force` without a program check.
- Consider blocking `mode server` / `server` / `tls-server` outright: Tunnel is a client, and a server profile multiplies the hook surface.
- Longer term, invert the model for OpenVPN: an allow-list of client directives with everything else a blocker. D4 chose warnings to avoid breaking profiles, but each gap found so far (P1-1 of the code review, P1-3 and P2-1 here) has been in the "warn" set.

### P2-1: Directives that make root open or write arbitrary paths pass with no warning (`dev-node`) or the generic one (`genkey`, `ifconfig-pool-persist`, `tls-export-cert`)
**Location:** `services/vpn_execution.py:97` (`"dev-node"` is in `OPENVPN_KNOWN_KEYS`), `:85-86` (`OPENVPN_BLOCKER_KEYS`).

**Evidence:** probe:
- `dev tun` / `dev-node /etc/sudoers` / `ifconfig-noexec` / `route-noexec` gives no hooks, no blockers and **no warnings**.
- `genkey secret /etc/sudoers.d/x` gives only "not on the reviewed list: genkey".

On the OpenVPN side, from source as I recall it, NOT VERIFIED:
- On macOS a `dev-node` not starting with `utun` goes through `open_tun_generic`, which calls `open(dev_node, O_RDWR)` as root.
- `ifconfig-noexec` and `route-noexec` (both "known") skip the `ifconfig` call that would otherwise fail fatally.
- The fd is then read as tunnel packets (sent to the server) and written with packets from the server.
- `genkey <type> <file>` writes a key file as root and exits.
- `ifconfig-pool-persist` and `tls-export-cert` write files in server mode.

**Attack scenario:** the malware adds `dev-node <root file>` plus the two `-noexec` lines. The review is silent. Root's openvpn then streams that file's bytes to the configured server, and the review does not show the OpenVPN `remote`, because `config_lines` is filled only for WireGuard. It also writes server-chosen bytes into the file. `genkey` creates or clobbers a file as root (for example a broken `/etc/sudoers.d/x`, which disables sudo). These are the same class as `status`, `writepid` and `replay-persist`, which are blockers.

**Suggested fix:**
- Move `dev-node` out of the known list. Allow only a value matching `^utun[0-9]{0,3}$` (or `tun`); block anything else.
- Add `genkey`, `ifconfig-pool-persist`, `tls-export-cert`, `client-config-dir` and `auth-gen-token-secret` to `OPENVPN_BLOCKER_KEYS`.
- Tests for each.

### P2-2: Residual D2 is wider than the review says: credential files named by absolute path give root an arbitrary-file read, for the tunnel's whole lifetime
**Location:** `services/vpn_rootscript.py:256-265` (absolute files are checked in place with `[ -f ]`, which follows links, and not copied), `services/vpn_execution.py:600-604` (the warning text), ADR 0004 §3 "data-only swap … MITM-class", spec §17 "Limits": "can change a certificate or key, not a command".

**Evidence:**
- The manifest accepts absolute `auth-user-pass`, `askpass`, `http-proxy-user-pass`, `http-proxy` and `socks-proxy` auth files (`OPENVPN_FILE_KEYS`, `:125-128`). Root hashes each one once, just before start, then openvpn opens the user's path itself.
- OpenVPN sends the first two lines of `auth-user-pass` to the server, and the proxy auth file to the proxy (HTTP Basic, cleartext; SOCKS5 username and password).
- OpenVPN also re-reads files after start, all NOT VERIFIED here but documented in the manual:
  - key files on every `SIGUSR1` or `ping-restart` unless `persist-key` is set;
  - the auth file on renegotiation with `auth-nocache` (a "known" key);
  - the `crl-verify` file whenever it changes.
- So the window is not "just before start" but the life of the tunnel.

**Attack scenario:**
- A profile names `http-proxy 127.0.0.1 8080 /Users/me/vpn/proxy-auth.txt`. The review lists the path with the D2 warning, the user confirms, and root's check passes.
- The malware then replaces `proxy-auth.txt` with a symlink to a root-only file, for example `/etc/wireguard/wg0.conf`, whose second line is a `PrivateKey`, or another account's SSH key. It also listens on 127.0.0.1:8080, after stopping the user's own proxy if one is there.
- Root's openvpn reads the file through the link and sends its first two lines as proxy credentials to the malware.
- With `auth-user-pass` the lines go to the configured VPN server, which the same malware may have chosen, since the OpenVPN review shows no `remote`.
- This is not MITM-class; it is a root file read.
- P1-2(c) gives the same result with no warning at all.

**Suggested fix:**
- Treat an absolute data file like a hook program: a **blocker** unless the file and every directory above it are root-owned and not group- or other-writable (`_unsafe_executable_reason`). A file root owns cannot be swapped by the user, which closes D2. Everything else must sit beside the config, where root already copies it.
- If the owner keeps D2, at least block absolute paths for the five credential directives, and reword the warning and ADR §3 to say "a swap after the check can make root read any file and send its first lines to the server or proxy, and openvpn re-reads these files while the tunnel is up".

### P3-1: The openvpn binary root runs is resolved twice and is not bound to the review
**Location:** `services/vpn_execution.py:1034` (the review's warning resolves `openvpn_binary()`), `:1007-1011` (the command shows bare `openvpn`), `:698-708` (the fingerprint has no binary path), `services/openvpn_manager.py:244` (`connect` resolves `openvpn_binary()` again, after the confirmation), `:58-72` (`shutil.which`, then `/opt/homebrew/sbin`, `/usr/local/sbin`, … in that order).

**Attack scenario:** a user has a root-owned openvpn in `/usr/local/sbin` and also has Homebrew, whose `/opt/homebrew/sbin` is user-writable. The review raises no R1 warning. After the user confirms, the malware creates `/opt/homebrew/sbin/openvpn`. The worker's fresh review would warn, but warnings are not in the fingerprint and are not shown again. `connect` resolves the planted file, and root runs it after the password.

**Suggested fix:** resolve the binary once in the review, show it in `command`, put it in `fingerprint()`, and pass the reviewed path to `connect`. Optionally pin its `(st_dev, st_ino)` as well.

### P3-2: The R1 warning looks only at the openvpn executable, not at what it loads
**Location:** `services/vpn_execution.py:826-838` (`_writable_tool_warnings`).

**Evidence:** `/opt/homebrew/etc/openssl@3/openssl.cnf` is owned by `as`. OpenSSL ≥ 1.1.1 loads its default config at initialisation, and that config can activate a provider by `module = <path>` (NOT VERIFIED with openvpn). `env -i` clears `OPENSSL_CONF`, `OPENSSL_MODULES` and `OPENSSL_ENGINES`, but the compiled-in `OPENSSLDIR`, `MODULESDIR` and linked dylibs remain. OpenVPN 2.7 also runs its default `dns-updown` script from the install's `libexec` when DNS options are pushed (NOT VERIFIED on macOS Homebrew).

**Impact:** for a Homebrew openvpn the existing warning fires and is honest ("the config check does not cover the tool itself"). For a root-owned openvpn linked against Homebrew's OpenSSL, nothing is said, yet a user-writable `openssl.cnf` gives the same root code path.

**Suggested fix:** extend the check to `otool -L` library paths, to OpenSSL's `OPENSSLDIR/openssl.cnf` and module directory (`openssl version -d -m`), and to 2.7's `dns-updown` script. Or say in the warning that these are covered by R1 too.

### P3-3: Under `--cd`, relative arguments in a hook now run user-supplied (hashed but never shown) files
**Location:** `services/vpn_execution.py:358-363` (`_path_word` ignores relative words), `services/vpn_rootscript.py:269` (`--cd "$cfgd"`).

**Evidence:** probe of `askpass run.sh` / `script-security 2` / `up "/bin/sh run.sh"`: `_hook_findings` gives `([], [], [])`, and the manifest is `[('run.sh', '760d…')]`. Before S8b, `run.sh` resolved against `/`, which is root-owned. Now it resolves to root's copy of the user's `run.sh`.

**Impact:** there is no TOCTOU, since the copy is hash-bound. But root runs code the user was shown only as a file name, and the review's stated rule, "only programs that root owns are accepted", does not hold.

**Suggested fix:** in `_program_findings`, treat a relative argument to an interpreter or wrapper (`_INTERPRETER`) as a soft blocker ("names a file beside the config; root would run it"), or block a manifest path that also appears in a hook value.

### P3-4: Hashes of password files are still visible to every local account through the process table
**Location:** `services/vpn_rootscript.py:447-449` (manifest hashes as argv), `agents/vpn_agent/services/privileged.py:169` and `:176` (the whole `shlex.join` is in osascript's argv), `:9-11` (the docstring promises nothing secret travels this way).

**Evidence:** `/bin/ps` is setuid root here, and as the user it prints the full arguments of root processes. Those arguments include:
- osascript's argument, for the whole dialog (up to 90 + 30 s);
- the root bash's argv, during copy and the 15 s device wait.

The round-2 P3-4 fix removed the hashes from the dialog and `sections()` because "a password file's hash can be guessed offline", but the same hashes stay readable by any other local user.

**Suggested fix:** either document this honestly as the remaining exposure, or send a per-run random salt `s` with `SHA-256(s‖bytes)` for the credential directives (root computes `{ printf %s "$s"; cat "$copy"; } | shasum`). Salting stops precomputed tables, though not a targeted guess during the window.

### P3-5: The round-2 fix for case-duplicates misses NFC/NFD spellings; root still refuses after the password
**Location:** `services/vpn_execution.py:540-546` (`casefold()` comparison only), `services/vpn_rootscript.py:251`.

**Evidence:** on this APFS volume, the NFD name `os.path.exists` is True when only the NFC file exists. `_openvpn_includes` on `ca café.crt` / `extra-certs café.crt` (NFC, then NFD) returns both entries and no blocker. Running `OVPN_SCRIPT` as the user with that manifest gives exit **98**, "café.crt is listed twice; nothing was run", with openvpn not started.

**Impact:** fails closed, so this is usability only, but the code review's P3-1 named this case and it is still open.

**Suggested fix:** compare `unicodedata.normalize("NFD", name).casefold()`, or deduplicate by `(st_dev, st_ino)`.

## Observations (no finding)
- Server-pushed options: with `pull`, a pushed option cannot set `up`, `plugin`, `config`, `dev-node` or any file directive; those are not push-permitted option classes. So in 2.6 a server cannot name a file or program. In 2.7 a pushed `dns` option triggers the default `dns-updown` script, which falls under R1 (P3-2). CVE-2025-10680 (injection into that script) affected only the 2.7 pre-releases up to beta1. All of this is from OpenVPN sources and advisories, NOT VERIFIED here.
- Secrets at rest: copies of keys and password files live in `ovpn/private/cfg` (`0700`/`0600` root) while the tunnel is up. They are removed on a failed start, on Disconnect and on the next boot. An openvpn that dies on its own leaves them until the next Connect or reboot (ADR §5, documented).
- The start-up log is unlinked after the device line is seen, but openvpn keeps writing to the unlinked inode until it exits. That is root-only and freed on exit; I record it only so that "the log is deleted" is not read as "nothing is written".
- `daemon` and `syslog` are not blockers despite spec §1.3. This is a documented owner deviation (spec §12, D3), and `--daemon`/`--log` follow `--config` on the command line and win.
- Root reads relative sources through any symlinks in the user's folder (`take "$srcd/$p"`, `:252`). A swap can only produce a hash mismatch (98). The one bit it leaks, whether a root-only file matches a chosen hash, costs a password prompt per try.

## Not verified
- All OpenVPN-side behaviour: BOM skipping (sourced, not run), `parse_line` and `argv_parse_cmd` splitting (sourced or recalled, not run), pkcs11-helper loading every listed provider, `dev-node` on macOS, `genkey` in a config file and whether it truncates, `dns-updown` and `script-security`, file re-reads after start, and OpenSSL loading `openssl.cnf` inside openvpn. openvpn is not installed on this Mac. Check each against `man openvpn` and the 2.6 or 2.7 source before fixing.
- `do shell script` **with** administrator privileges. I tested the same quoting path without that clause; the elevated path may differ in environment, not in string handling.
- Whether openvpn 2.6 treats a config line longer than `OPTION_LINE_SIZE` (256) as fatal or splits it. If a build splits it, the tail would be a directive Sentinel never sees. Blocking lines over 255 bytes outside inline blocks would be a cheap guard.
- No dependency CVE scan (no tool installed, and S8b changes no manifest).
- I did not run the activity logger (`log_activity.py`): writing outside this report was out of scope.

## Resolution

| Finding | Resolution | Commit |
|---|---|---|
| P1-1 | Fixed: leading U+FEFF skipped before scanning (OpenVPN only); directive names outside `-{0,2}[A-Za-z0-9][A-Za-z0-9-]*` block. Tests: BOM before `config`/`management`/`replay-persist`/`up`, zero-width and look-alike names. | 2c1ec81 |
| P1-2 | Fixed: `_ovpn_tokens` returns None for a quote opening mid-token or closing against other text (blocker); OpenVPN program values re-split with the same tokeniser; a missing OpenVPN hook program or library blocks. Tests: the three probe lines and three more quote shapes, missing hook/plugin/library. | 2c1ec81 |
| P1-3 | Fixed: `dns-updown`, `tls-crypt-v2-verify`, `client-crresponse` in hook and executable keys (`disable`/`force` pass); `server`, `server-bridge`, `tls-server`, `mode server` block. Allow-list inversion of D4 not done (owner decision). | 2c1ec81 |
| P2-1 | Fixed: `genkey`, `ifconfig-pool-persist`, `tls-export-cert`, `client-config-dir`, `auth-gen-token-secret` block; `dev-node` only `utun[0-9]{0,3}`. | 2c1ec81 |
| P2-2 | Fixed: an absolute data file blocks unless it and every folder above it are root-owned and not group/other-writable; ADR 0004 §3 and spec §17 narrow D2. | 2c1ec81 |
| P3-1 | Fixed: the review resolves openvpn once (`ExecutionReview.program`), shows the full path in `command`, and both are in the fingerprint; `execute()` hands it to Connect, which runs it instead of looking it up again. A program planted after confirmation changes the fresh review, so nothing runs. Tests: review names it, planted program runs nothing, root gets the reviewed path, `openvpn_manager.connect(openvpn=)`. | 846f7a6 |
| P3-2 | Deferred (owner, 2026-11-05) into the D10 backlog item, widened to OpenSSL's `openssl.cnf`, module folder and libraries. |  |
| P3-3 | Fixed: a hook word that names a file the profile copies beside the config (after normalising, ignoring case and Unicode form) blocks: under `--cd` root would run that copy. Tests: five hook shapes, plus ordinary arguments still pass. | 846f7a6 |
| P3-4 | Deferred (owner, 2026-11-05): backlog P3, moving the manifest off argv changes the dialog path. |  |
| P3-5 | Fixed: duplicate names compare after Unicode NFD and case folding (`_same_name`). Test: NFC and NFD spellings of one name block. | 846f7a6 |

Author's note: fixes are author-verified (full suite 3511 passed, 14 skipped, known unrelated failure deselected; 16/16 round-3 mutants killed); not re-reviewed independently. OpenVPN behaviour behind P1-1 to P1-3 remains NOT VERIFIED against a real openvpn.

Correction after the independent re-review (`docs/audit/audit-tier2-s8b-rereview-2026-11-05.md`): P1-2's "missing program blocks" covered absolute paths only, P1-3's `disable`/`force` exemption held only because relative values were never checked, and P3-3 covered literal forms only. All three are closed by its R-P1-1/R-P1-2 fix (OpenVPN hooks name a program directly by absolute path, no interpreter or wrapper), and P2-2's ownership is checked along the path as written (re-review R-P3-2, fixed in `e06b423` and extended to link chains by review R5-P3-1).

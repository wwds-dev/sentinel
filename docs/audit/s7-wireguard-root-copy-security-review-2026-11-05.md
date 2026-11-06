# Security review: S7 WireGuard root copy (commit 7bca617, branch fix/s7-wireguard-root-copy)

Date: 2026-11-05 · Reviewer: security-engineer (independent, read-only) · Checkout: scratch worktree of `fix/s7-wireguard-root-copy`

Playbook note: `docs/playbooks/security-engineer.md` is `status: baseline` (last-reviewed 2026-10-10). Its practice section is empty, so this review follows AGENTS.md, CLAUDE.md "VPN Agent (Tunnel) rules", ADR 0004 and the spec.

**Verdict: no P0 or P1.** For WireGuard configs the commit closes the check-to-use race it set out to close. Root now reads the user's file exactly once, into a fresh root-only directory (`wg/` 0700, copy 0600). It hashes that copy and runs only the copy. Quoting holds end to end, and OpenVPN behaviour is unchanged and documented honestly. There are two P2s: (1) root's shell and `shasum` honour inherited environment variables before the hash check; (2) `wg-quick`, which is user-writable, still runs as root. Neither is new as a class. P2-1 becomes P1 if the owner's real-Mac check shows that `do shell script … with administrator privileges` passes the caller's environment to root. ADR 0004 overclaims on P2-2. Five P3s follow.

## Threat model (short)
- **Assets:** code execution as root; the WireGuard private key (now also in the root copy); routing, DNS and kill-switch state.
- **Trust boundaries:**
  - user → root through `osascript do shell script … with administrator privileges` (`agents/vpn_agent/services/privileged.py:85-103`).
  - user-writable inputs: the `.conf`, the Homebrew prefix that holds `wg-quick`, and Sentinel's own checkout and `.venv`, which `Sentinel.app` runs.
  - root-owned: `/private/var/run/sentinel-vpn`.
- **Entry points:** `vpn_execution.execute` → `vpn_connection.connect/disconnect` → `_wg_root_run` (`services/vpn_connection.py:180-202`) → `vpn_rootscript.wireguard_command` → `ROOT_SCRIPT` (`services/vpn_rootscript.py:43-88`).
- **What bounds every same-user finding:** `Sentinel.app` runs the checkout's `main.py` through a user-writable `.venv`. A same-user process that can edit `services/vpn_rootscript.py` gets root at the next approved prompt, so same-user → root stays open by design until the privileged helper (spec §7 non-goal). S7 removes one path. It does not remove the class, and the docs should say so (P2-2).

## Findings

### P2-1: Root's shell and `shasum` honour inherited `BASH_ENV`, `PERL5LIB`/`PERL5OPT`, `SHELLOPTS`/`PS4`, and both run before the hash check
- **Where:**
  - `services/vpn_rootscript.py:121`: argv is `/bin/bash -c ROOT_SCRIPT …` with no environment scrub.
  - `services/vpn_rootscript.py:70`: `/usr/bin/shasum` is a Perl script (`file /usr/bin/shasum`: "Perl script text executable").
- **Attack:** a same-user process sets the variable in Sentinel's environment, for example `launchctl setenv BASH_ENV ~/x.sh` in the GUI domain (picked up by apps launched afterwards), a shell rc file, or a launcher. It then waits for the next approved Connect or Disconnect. If the administrator path passes the environment through, `bash` sources `$BASH_ENV` as root before line 1 of the script. `shasum` would likewise load a Perl module from `PERL5LIB` with `PERL5OPT=-MEvil`.
- **Demonstrated as the user (not as root):**
  - The real `ROOT_SCRIPT` sourced a `BASH_ENV` file.
  - `shasum` loaded a module from `PERL5LIB`/`PERL5OPT=-MEvil`.
  - Non-admin `do shell script` on this Mac (macOS 27) inherits the caller's whole environment (`BASH_ENV`, `PERL5OPT`, a marker variable, the full user `PATH`) and its working directory.
  - Not verified: whether the administrator variant passes the environment through as well. bash skips `BASH_ENV` only when uid ≠ euid, and Perl turns on taint mode only for setuid.
- **Impact:** root code execution that does not depend on the config file at all. The class is pre-existing: `wg-quick` is itself a bash script and would read `BASH_ENV`. But this commit adds two new consumers ahead of the hash check, and the hardening is cheap.
- **Fix:**
  - Start root with a clean environment: `["/usr/bin/env", "-i", "/bin/bash", "-c", ROOT_SCRIPT, …]`. The script already exports its own `PATH`.
  - Hash with a Mach-O tool: `/sbin/sha256 -q` or `/usr/bin/openssl dgst -sha256 -r` (both present, root:wheel).
  - Add a harness test that sets `BASH_ENV`, `PERL5OPT`/`PERL5LIB` and `SHELLOPTS=xtrace` + `PS4` and asserts none of them is honoured.
  - Owner check: `SENTINEL_PROBE=1 osascript -e 'do shell script "env" with administrator privileges'` (it only prints the environment).

### P2-2: `wg-quick`, and the directory placed first on root's PATH, are user-writable. ADR 0004 says no user-writable path is used after the hash check (pre-existing, unchanged by this commit)
- **Where:**
  - `services/vpn_connection.py:192`: `shutil.which("wg-quick")` in Sentinel's own `PATH`.
  - `services/vpn_rootscript.py:46` (`PATH="${wgq%/*}:/usr/bin:…"`) and `:77`/`:83` (`"$wgq" up/down`).
  - `docs/adr/0004-root-runs-verified-copies.md:39` ("No path in a user-writable directory is used after the hash check").
- **Attack:** on this Mac `/opt/homebrew` is `as:admin` 0755 and `/opt/homebrew/bin` is `as:admin` 0775. A same-user process replaces `wg-quick`, or its bash interpreter, `wg` or `wireguard-go` in that prefix. Root runs the replacement at the next approved Connect or Disconnect, whatever the config hash says. If `shutil.which` sees a user `PATH`, a planted `~/.local/bin/wg-quick` would win too; on this Mac that directory is first in the shell `PATH`.
- **Impact:** same-user → root, unchanged by S7. It is not new: the old `wg_quick_command` ran the same executable with the same directory first. The hook scanner blocks user-writable hook programs (`_unsafe_executable_reason`, `services/vpn_execution.py:349`) but never applies that check to `wg-quick` itself.
- **Side note (improvement):** root's `PATH` is now fixed in the script, so `_which_privileged` (`services/vpn_execution.py:337-346`: wg-quick's directory, then `/usr/bin:/bin:/usr/sbin:/sbin`) now matches what root uses exactly. Before, the root shell appended an inherited `"$PATH"`, which on macOS 27 may have been the user's full `PATH`.
- **Fix:**
  - Run `_unsafe_executable_reason` on the resolved `wg-quick` and its shebang interpreter at review time. Make it a blocker on Connect and a soft blocker on Disconnect, like hook programs.
  - Amend ADR 0004 decision 3 and spec §14 "Limits that remain" to name this.
  - Add one line to `docs/agents/vpn.md` and `docs/training/tunnel.md` beside "administrator rights never run your own `.conf`": the copy protects the config, not the tools or Sentinel itself.

### P3-1: A FIFO or blocking device as the source lets the approved run finish later, at a time the attacker picks
- **Where:** `services/vpn_rootscript.py:69` (`head -c … -- "$src"` follows anything).
- **Attack:** after the review, swap the `.conf` for a FIFO. Root's `head` blocks. Sentinel times out (30 s + 90 s) and reports "may still have happened". The attacker later writes the reviewed bytes into the FIFO, and root runs `wg-quick up` with its PostUp hooks minutes or hours later. Demonstrated in the bash harness: the script stayed blocked for 3 s, then ran the fake `wg-quick` (rc 0) once the bytes were written. A tty device as the source would make root consume terminal input instead; the input lands in the root-only copy and is deleted, with no leak.
- **Impact:** only the reviewed content runs, but at an attacker-chosen time after the UI said it failed. Spec §14 says "nothing runs, but the attempt hangs", which is inaccurate.
- **Fix:** refuse a source that is not a regular file before reading (`[ -f "$src" ] && [ ! -L "$src" ]`). Also bound the read with a watchdog (`head … & p=$!; (sleep 20; kill $p) &`), because the pre-check is racy. Correct the spec line.

### P3-2: Exit codes and error text are an existence and content-guess oracle for root-only paths
- **Where:** `services/vpn_rootscript.py:69-72`.
- **Attack:** swap the reviewed `.conf` for a symlink or hard link (macOS allows hard links to files the user cannot read on the same volume) to a root-only path.
  - The path does not exist: exit 97, and `head: <path>: No such file or directory` is returned to Sentinel and its audit output.
  - It exists with other content: exit 98.
  - It exists with exactly the guessed content: `wg-quick` runs.
  Each probe needs the owner to approve a prompt.
- **Impact:** low; a one-bit oracle per approved prompt. No content leaks: the copy is 0600 in a 0700 directory and removed on mismatch.
- **Fix:** report a read failure with the same message as a mismatch. Optionally pass the user's uid and refuse a source not owned by it (racy, but adds friction).

### P3-3: Lifetime and semantics of the kept copy
- **Where:** `services/vpn_rootscript.py:68`, `:82-84`.
- **Issues:**
  - (a) On Disconnect with a kept copy, the confirmation lists PreDown/PostDown from the user's current file, but root runs the kept copy's. The summary line (`wg_root_summary`) and `docs/agents/vpn.md` disclose this; the hook list the owner approves is still not what runs.
  - (b) If the tunnel was taken down outside Sentinel, `wg-quick down` fails, `|| exit` keeps the copy, and the private key stays on disk until the next Connect for that name or a reboot. Whether a reboot clears it is unverified. A later Disconnect for a same-named interface started from another config then runs the old copy's teardown and hooks.
  - (c) A Connect for an interface that is already up runs `rm -rf "$d"` on the live tunnel's copy before `wg-quick up` fails. Disconnect then falls back to the user's current file.
- **Fix:**
  - In `down`, remove the copy when the interface is not up (`/var/run/wireguard/<iface>.name` absent).
  - In `up`, refuse before deleting when that file exists.
  - Have the Disconnect review say that the hooks shown may differ from the copy kept since Connect.

### P3-4: The `/private/var/run` parent has no sticky bit, and the checks look only at the last path component
- **Where:** `services/vpn_rootscript.py:50-62`.
- **Attack:** `/private/var/run` is root:daemon 0775 with no sticky bit. A daemon-group process could rename `sentinel-vpn` and plant a symlink between `check_dir "$base"` and later uses of `$base/wg` and `$d`. `check_dir` tests `-L` only on the final component.
- **Impact:** outside the same-user model. The owner's account is not in `daemon` (verified with `id -Gn`).
- **Also:** for an existing base only the uid is checked; its gid is never normalised.
- **Fix:** `cd -P -- "$base"`, compare `pwd -P` with `$base`, then work with relative paths. Optionally check the gid too.

### P3-5: Documentation and code drift
- ADR 0004 decision 3 still says `install -d -o root -g wheel` and `/bin/cat -- SRC`, and makes the "no user-writable path" claim from P2-2. The code uses `mkdir -m` + `chown` and `head -c`. Spec §14 lists the deviations; the accepted ADR does not.
- `services/vpn_execution.py:333` and `:340` comments still name `wg_quick_command` as the source of root's `PATH`; it is now `ROOT_SCRIPT` line 46.
- `services/vpn_connection.py:87` `wg_quick_command` has no production caller left (only tests, and a comment in `services/openvpn_manager.py:50`). A live helper that runs the user's file is a trap for reintroduction. Delete it or rename it to make clear it is display-only.

## Questions asked, answered
- **TOCTOU in base, `wg/` and `<iface>`:** base is 0755 root, `wg/` 0700 root. A non-daemon user cannot create, rename, symlink or hard-link anything inside, so `rm -rf -- "$d"` and the `>` redirect act on root-only paths. `iface` is validated in Python (`INTERFACE_RE`) and again in the script (`''`, `.`, `..`, `*/*` refused). The only race window belongs to the daemon group (P3-4).
- **What root reads with `head`:**
  - Symlinks and hard links are followed. A wrong target gives 97 or 98, and a matching target runs only the reviewed bytes, so nothing runs that was not reviewed (oracle: P3-2).
  - `/dev/zero` is capped at 262145 bytes, then 98.
  - A FIFO hangs and can finish later (P3-1).
  - The copy is 0600 under 0700. No file contents are echoed; only `head`'s own error text with the path.
- **`wg-quick` and PATH:** pre-existing escalation, not new (P2-2). The `PATH` set in the script now matches `_which_privileged`.
- **Inherited environment:** P2-1.
- **Argument injection:** none found. `shlex.join` then `applescript_string` round-trips a path containing `'`, `"`, `$(…)`, backticks, `\`, `;` and a newline through a real (non-admin) `osascript do shell script`; the fake ran on the copy and no injected command ran. The script's apostrophe ("user's") is quoted correctly. Control characters are dropped by `applescript_string`; that changes the path root reads and so fails closed on the hash.
- **Secrets in exit codes or messages:** none beyond P3-2. The hash and paths travel on the command line; config text and keys do not.
- **Disconnect on the kept copy:** a same-user process cannot write the copy. The issues are the semantic and lifetime ones in P3-3.
- **OpenVPN:** `connect` and `disconnect` OpenVPN branches unchanged (`services/vpn_connection.py:163-171`, `:191-193`); `openvpn_manager.py` and `agents/` are untouched by the diff. CHANGELOG, `docs/agents/vpn.md`, `docs/training/tunnel.md`, the `vpn_execution` docstrings and TODO (new S8 P2 item) all say OpenVPN still reads the user's `.ovpn` after the check. That is honest.
- **Overclaims and underclaims:**
  - User-facing docs and CHANGELOG are accurate about the config file but silent on the tools and the checkout (P2-2).
  - ADR 0004 overclaims (P2-2, P3-5).
  - Spec §14 says "nothing runs" for a FIFO, which is inaccurate (P3-1).

## What was verified
- `git show 7bca617`, reading the full diff of the services, docs, ADR, spec §0/1.2/1.6/7/14 and TODO.
- `pytest -p no:cacheprovider -q tests/test_vpn_rootscript.py tests/test_vpn_connection.py tests/test_vpn_execution.py tests/test_vpn_review_gate.py tests/test_contract_tunnel_chat.py`: **327 passed** (61.8 s).
- Probes (as the user, fake `wg-quick`, temporary base, own uid as owner; scripts in the session scratchpad, not committed):
  - `BASH_ENV` sourced: yes.
  - `PERL5LIB`/`PERL5OPT=-MEvil` loaded by `shasum`: yes.
  - Missing path → 97 with `head` error text; other content → 98; `/dev/zero` → 98; FIFO → blocks, then completes rc 0 once the bytes are written.
  - Modes: base 0755, `wg` 0700, `<iface>` 0700, copy 0600.
  - Hostile-path quoting round-trip through non-admin `osascript`: no injection.
- Non-admin `osascript do shell script "env; pwd"` inherits `BASH_ENV`, `PERL5OPT`, a marker variable, the full user `PATH` and the working directory.
- Permissions (`ls -ld`): `/private/var/run` root:daemon 0775 with no sticky bit; `/opt/homebrew` as:admin; `/opt/homebrew/bin` as:admin 0775; `wg-quick` not installed on this Mac; `/bin/sh` → bash; `id -Gn` has no `daemon`.
- Dependencies: the commit adds none, so no audit tool was run.

## Not verified (needs a real Mac as root; owner checklist)
- Whether `do shell script … with administrator privileges` passes the caller's environment and working directory to root (decides P2-1 between P2 and P1).
- Whether bash 3.2 and Perl honour `BASH_ENV` and `PERL5OPT` when uid = euid = 0 in that context.
- Real ownership and modes under `/private/var/run/sentinel-vpn`, and whether a reboot empties `/var/run` (ADR 0004 already lists this as unverified).
- Real `wg-quick up` and `down` on the copy, including the kept-copy Disconnect and the stale-copy cases in P3-3.
- The owner and interpreter of a Homebrew-installed `wg-quick` (not installed here).
- The daemon-group rename race in P3-4 (rename semantics on APFS).

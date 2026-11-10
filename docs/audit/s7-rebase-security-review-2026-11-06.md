# Security review: S7 after the rebase (branch fix/s7-root-copy-rebased, base origin/main f14a19a)

Date: 2026-11-06 · Reviewer: security-engineer (independent, read-only; did not write this code) · Scope: `git diff origin/main` in the scratch worktree (commits 1ce5017, ae6c82b, 0b2cd91 plus uncommitted changes to `privileged.py`, `vpn_rootscript.py`, `vpn_connection.py`, `vpn_execution.py`, `openvpn_manager.py` and tests).

Playbook note: `docs/playbooks/security-engineer.md` is `status: baseline` (last-reviewed 2026-10-10) and its practice section is empty, so it is stale by the AGENTS.md rule. This review follows CLAUDE.md "VPN Agent (Tunnel) rules", ADR 0004 and spec §1.1-1.2 and §14. `docs/company/app-context.md` does not exist. `log_activity.py` was not run because this report is the only file the brief allows me to write.

**Verdict: no P0 or P1. The rebase kept the S7 protection, and main's hook-allowlist rules (#30-#37) are intact.**
- Root copies the config into a root-only directory, hashes the copy with `/sbin/sha256` and runs only that copy.
- Root's inner shell starts from `env -i`. `osascript` now starts with `PATH` only, so the outer `/bin/sh` gets nothing the user set.
- I found one new P2: the `wg-quick` that root runs is looked up again after the confirmation, so the review's warning about a user-writable `wg-quick` can be dodged.
- Four P3s: three hardening or robustness issues and one documentation drift introduced by the rebase.

## Threat model (short)
- **Assets:** code execution as root; the WireGuard private key (also in the root copy while the tunnel is up); routing, DNS and kill-switch state.
- **Trust boundaries:**
  - **User → root:** `osascript do shell script … with administrator privileges` (`agents/vpn_agent/services/privileged.py:96-115`), which runs `/usr/bin/env -i /bin/bash -c ROOT_SCRIPT …` (`services/vpn_rootscript.py:170`).
  - **Inputs the user can write:** the `.conf`, Sentinel's `PATH` and working directory, the Homebrew prefix that holds `wg-quick`, and Sentinel's checkout and `.venv`.
  - **Root-owned:** `/private/var/run/sentinel-vpn` (its parent `/private/var/run` is `root:daemon 0775`, no sticky bit; only `root` is a member of `daemon` and only the `daemon` account has it as primary group).
- **Entry points:** `ui/workers.py:633` → `vpn_execution.execute` (`services/vpn_execution.py:1425`) → `vpn_connection.connect`/`disconnect` → `_wg_root_run` (`services/vpn_connection.py:111-138`) → `ROOT_SCRIPT`.
- **Standing bound (unchanged):** `Sentinel.app` runs the user-writable checkout through a user-writable `.venv`. Same-user → root therefore stays open by design until a privileged helper exists (spec §7). The findings below are judged against what S7 claims to close.

## Status of the 2026-11-05 findings after the rebase

| Earlier | Status | Evidence |
|---|---|---|
| P2-1 inherited env (`BASH_ENV`, `PERL5OPT`, `SHELLOPTS`/`PS4`) | **Resolved.** Inner bash: `env -i` (`vpn_rootscript.py:170`). Hash: `/sbin/sha256` (`:109`). Outer `/bin/sh`: `DIALOG_ENV` (`privileged.py:30`, `:107`). | Probe 1 and 2 below; `test_root_ignores_an_inherited_environment` passes. |
| P2-2 `wg-quick` user-writable | **Accepted risk (owner: warn, not block).** The warning is at `vpn_execution.py:1203-1211`; ADR 0004 is amended. The warning can be bypassed (new P2-A), and one user doc still overclaims (P3-D). | Probe 3. |
| P3-1 FIFO holds the run open | **Resolved.** Regular-file check (`:101`) and a 20 s cut-off (`:102-108`). One residual: a stat that stalls before the cut-off starts (P3-B). | Probe 4: the race was hit once, cut off at 21.2 s with exit 98; only the reviewed bytes ever ran. |
| P3-2 exit-code/path oracle | **Resolved.** One exit (98) and one message for read failure and mismatch; `head` stderr goes to `/dev/null` (`:91`, `:102`). What is left is inherent: "runs" against 98 for a guess of the exact content. | Covered by tests; code read. |
| P3-3 kept-copy lifetime | **Mostly resolved.** Connect refuses 94 before touching the copy (`:114`). Disconnect of a not-up interface removes the copy (`:119-122`). The review discloses that the kept copy runs. Residual: P3-C. | Tests pass; code read. |
| P3-4 daemon-group rename | **Partly resolved.** `cd -P` plus a physical-path check (`:84-85`) and relative paths for root's own work. But ownership is not re-checked after the `cd`, and `wg-quick` still gets an absolute path through the base (P3-A). | Probe 5. |
| P3-5 doc/code drift | **Mostly resolved.** `wg_quick_command` removed; comments repointed; ADR amended. The rebase brings in new drift from main's #33-#35 text (P3-D). | grep. |

## Findings

### P2-A: The `wg-quick` that root runs is resolved again after the confirmation, so the review's warning does not cover it
- **Where:**
  - `services/vpn_connection.py:127-128` resolves `exe = shutil.which("wg-quick")` at run time.
  - The review's own check is a separate lookup (`services/vpn_execution.py:1203-1204`).
  - The executable is not in the review: `command` is the prose `wg_root_summary`, and `fingerprint()` (`:940-949`) never sees the path.
- **Attack:** the owner has a root-owned `wg-quick`, so the review shows no "wg-quick runs as administrator from …" warning, and Sentinel's `PATH` has an earlier entry that the user can write or create. That entry could be set with `launchctl setenv PATH` before Sentinel starts; a `~/.local/bin` or a relative entry works the same way. After the owner confirms, a process running as the user drops `wg-quick` into that entry. `_wg_root_run` then sends root the planted path. Root puts its folder first on `PATH` and runs it: root code execution with no warning shown. In the common Homebrew setup the warning is already shown, so this mainly defeats the case where the warning claims safety.
- **Evidence:** script `scratchpad/s7probe/wgq_probe.py`. `PATH=A:B`, with `wg-quick` only in B during the review; `A/wg-quick` is created after `review_execution`; then `execute(confirmed=review)` runs with a capturing `run_as_root`.
  ```
  allowed: True | wg-quick warning: ['wg-quick runs as administrator from <B>/wg-quick ...']
  ran: True | root's wg-quick: ['wg-quick', '<probe>/wgq/A/wg-quick']
  ```
  The fingerprint matched, so `execute` ran with a different executable from the one the review judged.
- **Impact:** same-user → root through a gated Connect or Disconnect, with the review's safety signal bypassed. The severity is bounded by the standing same-user bound above, which is why this is P2 and not P1.
- **Fix:**
  - Resolve `wg-quick` once in `review_execution` and store it in `ExecutionReview`, for example a `wg_quick` field. Include it in `fingerprint()` and show it in the command line.
  - Have `_wg_root_run` use the reviewed path rather than calling `shutil.which` again.
  - Optionally re-run `_unsafe_executable_reason` on it in `execute` and refuse if the verdict changed.
  - While there, apply the same check to `wg-quick`'s shebang interpreter and to `wg` and `wireguard-go` in its folder. A root-owned `wg-quick` whose interpreter is Homebrew's user-owned bash gets no warning today. This is code-read only; `wg-quick` is not installed on this Mac.
  - Add a test: plant an earlier `PATH` entry after the review and assert either that nothing runs or that the reviewed path is what reaches `wireguard_argv`.

### P3-A: After `cd -P` into the base, ownership is not re-checked, and `wg-quick` reads the copy through the absolute base path
- **Where:**
  - `services/vpn_rootscript.py:84-85` compares only the physical path after the `cd`.
  - `:87` builds `run="$base/$copy"`, passed to `wg-quick` at `:116` and `:131`.
- **Attack:** this is outside the same-user model; it needs a writer of `/private/var/run`, which means group `daemon`.
  - **Swap after the check:** between `check_dir "$base"` and the `cd`, the attacker renames `sentinel-vpn` and creates their own directory at the same name. `pwd -P` still equals `$base`, so the check passes, and root works inside the attacker-owned directory. The attacker can then swap `wg/<iface>/<iface>.conf` after the hash check.
  - **Absolute read:** independently, `wg-quick` resolves `$run` again from `/`. A rename after root's last check redirects `wg-quick` to attacker content.
- **Evidence:** `scratchpad/s7probe/rename.py`. The fake `wg-quick` stands in for the attacker's moment: it renames the base and plants `PostUp = EVIL` before reading `$2`.
  ```
  rc 0 | wg-quick read: PostUp = EVIL
  ```
  This was not verified as root or with a real `daemon`-group process. No running process has group `daemon` (`ps -axo user,group,comm`).
- **Fix:**
  - After the `cd`, add `check_dir .` (uid of `.`) and `chown`/`chmod .`.
  - Pass `wg-quick` a path it cannot be redirected through, for example run it with `cd` into the root-only `wg/<iface>` and a `./<iface>.conf` argument. Weigh this against the "hooks run from /" rule: the folder is root-only, so the cwd leaks nothing of the user's. Alternatively accept and document the daemon-group window in ADR 0004; today the spec mentions only the window before the `cd`.

### P3-B: A stat that stalls on a source the user controls can still delay the approved run until a time the attacker picks
- **Where:** `services/vpn_rootscript.py:101`. `[ -f "$src" ]` runs in the foreground, before the 20 s watchdog starts at `:102-107`. After `kill -9`, `wait` (`:108`) waits for a process that may be in an uninterruptible filesystem call.
- **Attack:** the reviewed config sits on a filesystem the user can mount and whose server they control, for example an SMB share served from localhost. The server stalls `stat` past Sentinel's 30 s + 90 s timeout ("may still have happened"), then answers. `head` then reads the reviewed bytes and root runs them later. Only reviewed content runs; the timing is the attacker's, which is what P3-1 set out to remove.
- **Evidence:** not verified; needs a stalling network or FSKit server.
- **Fix:** move the regular-file test into the watched background job, e.g. `( [ -f "$src" ] && exec /usr/bin/head -c … -- "$src" ) > "$copy" 2>/dev/null &`. Also consider refusing a source whose `stat -f %Sd` is not a local device, or note the residual in spec §14.

### P3-C: A stale `/var/run/wireguard/<iface>.name` wedges the interface and keeps the private-key copy indefinitely
- **Where:** `services/vpn_rootscript.py:88` (`up_now` = the `.name` file exists), `:114` and `:131`.
- **Scenario:** `wireguard-go` dies, or the machine reboots and `/var/run` is not emptied (unverified per ADR 0004), leaving `<iface>.name`.
  - Connect refuses with 94 ("already up").
  - Disconnect calls `wg-quick down`, which fails because there is no real interface. `|| exit` then keeps the root copy holding the private key.
  - The only way out is a manual `sudo rm`.

  As far as I recall, wg-quick's own `get_real_interface` (darwin.bash) also checks the `.sock` file and the mtimes, so it treats a stale name as down. Sentinel's check is stricter than the tool's.
- **Impact:** availability, plus the private key at rest in a root-only file. A same-user process cannot cause it, because it cannot write `/var/run/wireguard` or kill root's `wireguard-go`.
- **Evidence:** not verified (code read; `wg-quick` not installed here).
- **Fix:** mirror wg-quick's test (`.name` exists **and** the named `.sock` exists), or remove `$d` in `down` when `wg-quick down` fails and the interface has no socket. Add it to the owner checklist.

### P3-D: User docs merged from main still describe the pre-S7 state and overclaim
- **Where:**
  - `docs/agents/vpn.md:42` says "a user who can write it can already replace wg-quick itself (closed by the root-owned copy, audit step S7)". The copy does **not** close this; ADR 0004's amendment and the new section in the same file say so.
  - The same line says "a change after that point is not caught until the privileged copy of the config is built (audit step S7)". It is built now.
  - The same line cites the old command shape (`cd / && /usr/bin/env -i …`).
  - `docs/agents/vpn.md:58` says "the same gap as the openvpn program itself (audit step S7)"; that is S8, or not covered at all.
  - `docs/training/tunnel.md:99-101` says "a change after that is not caught yet (audit step S7)".
- **Impact:** the user-facing docs contradict each other and overstate what the copy protects. CLAUDE.md requires never claiming a protection without a test.
- **Fix:** reword those sentences to match the new "What runs as administrator: a verified copy" section. These are text-only edits.

## Questions from the brief, answered
- **TOCTOU on the copy:** the read happens once, into `wg/<iface>/` (0700, root), and the hash is taken of the copy. A swap to a FIFO or device after the `-f` check is cut off by the watchdog (probe 4). A symlink or hard link to another file gives 98, or runs only if the bytes are identical to the reviewed ones. Base, `wg` and `<iface>` are safe against the same user; against group `daemon`, see P3-A.
- **Oversize:** the review refuses more than 256 KiB with no hash (`vpn_execution.py:378-380`). Root copies 262145 bytes, so the hashes can never match.
- **Stored-copy decision on Disconnect:** made inside root-only `wg/<iface>` (`:126`); a same-user process cannot influence it. With no copy and no hash the script exits 96.
- **Cleanup paths:**
  - Mismatch or read failure: `rm -rf "$d"`, exit 98.
  - Failed `up`: `rm -rf "$d"` and wg-quick's exit code.
  - Disconnect of a not-up interface: `rm` and exit 94.
  - Failed `down`: the copy is kept (see P3-C).
  - All `rm` calls use relative paths inside the checked base.
- **Exit codes 94/96/97/98:** no Python code branches on them (grep). They only surface as text, so a colliding wg-quick code cannot be misread.
- **`HOME=/var/root` and `(cd / && exec "$wgq" …)`:** probe 2 shows `wg-quick` gets cwd `/` and only `PATH`, `PWD`, `SHLVL`, `HOME` and `_`. This is equivalent to main's #35 `cd / && env -i HOME=/var/root PATH=…`, and main's test is preserved as `test_wg_quick_runs_from_root_with_only_home_and_path` (both verbs).
- **`DIALOG_ENV`:** probe 1 shows osascript's `do shell script` sees only `PATH` and `__CF_USER_TEXT_ENCODING` even with `BASH_ENV`, `SHELLOPTS` and `PS4` set in the caller. It also fixes a side issue: with `env=` given, Python resolves `osascript` from `DIALOG_ENV`'s `PATH`, so a planted `osascript` earlier on the user's `PATH` is no longer run (the same probe without `env=` ran the fake). Not verified: the administrator variant as root, and whether the dialog still appears with `HOME`, `USER` and `TMPDIR` absent. If it fails, it fails closed (nothing runs). This is the owner checklist item.
- **`sudo -n`:** not relevant for gated actions. `ui/workers.py:633` passes no runner, so `vpn_connection._default_run_as_root` is `allow_cached_sudo=False` (`vpn_connection.py:48`) and goes straight to `run_with_dialog`. A Sentinel running as root gets `ROOT_REFUSAL` (`privileged.py:132-134`). No production caller passes `allow_cached_sudo=True` for WireGuard; `wireguard_manager.connect`/`disconnect` are disabled stubs.
- **`config_sha256` and unscanned bytes:** none found.
  - Undecodable bytes: the hash is cleared (`vpn_execution.py:1145-1148`).
  - Oversize, missing or unreadable: the hash is `""`.
  - Symlink: the hash covers the bytes read through it, and root reads the same path.
  - Interface-only profile: `review.target` is the concrete `WG_CONFIG_DIRS` file and `execute` passes it as `config_path` (`:1455-1459`).
  - Relative path or `link/..`: both sides use `Path(…).expanduser()`, and `_absolute` joins `os.getcwd()` without lexical normalisation, so the kernel resolves `..` the same way on both sides.
  - In every case root re-hashes, so any substitution fails closed.
- **Argument validation and quoting:** `wireguard_argv` (`vpn_rootscript.py:156-169`) refuses a bad verb or interface, a relative `wg-quick` or source, a path without a hash, a bad hash, and a bad base, owner or run directory. The script re-checks the interface (`:67`). `ROOT_SCRIPT` holds no control or non-ASCII characters, so `applescript_string` passes it unchanged. Probe 2 ran the full `shlex.join` → `applescript_string` → non-admin `osascript` chain with a source path containing `'`, `"`, `$(…)`, backticks, `;`, `\` and a newline: it ran on the copy and no injected command ran. Control characters dropped from a path change the file root reads, so the hash check fails closed. A leading `-` in an interface name is harmless: every use is prefixed with `wg/` or an absolute path.
- **Main's hook allowlist (#30-#37):** intact. `git diff origin/main -- services/vpn_execution.py` removes only docstrings, the import line, two `wg_quick_command` calls, the hooks heading and the runner call. No hook-scanning code is touched, and `agents/vpn_agent/services/config_inspection.py` is untouched. The three removed `wg_quick_command` tests are replaced by argv-level and harness tests.
- **Dependencies:** the diff adds or changes none, so no audit tool was run.

## What was run (as the user; nothing as root; no wg-quick, sudo, admin osascript or pfctl)
- `pytest -q tests/test_vpn_rootscript.py tests/test_vpn_connection.py tests/test_vpn_execution.py tests/test_vpn_review_gate.py tests/test_vpn_review_fixes.py tests/test_privileged_runner.py tests/test_contract_tunnel_chat.py`: **680 passed, 2 skipped** (69.8 s).
- The VPN-related tests from main's #30-#37 test files (`-k "vpn or wg or hook or tunnel or openvpn or wireguard"`): **639 passed**.
- **Probe 1:** `BASH_ENV=… SHELLOPTS=xtrace PS4='$(echo PWNED)' env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin osascript -e 'do shell script "env; pwd"'` printed `__CF_USER_TEXT_ENCODING`, `PATH`, `PWD`, `SHLVL` and `_`, with no `PWNED`. Through `privileged.run(..., env=DIALOG_ENV)` with a fake `osascript` first on `PATH`, the real one ran and saw the clean env; without `env=`, the fake ran.
- **Probe 2 (`scratchpad/s7probe/e2e.py`):** the end-to-end non-admin `osascript` round trip with a hostile source path and `BASH_ENV`, `SHELLOPTS` and `PS4` set. `wg-quick` received `up <base>/wg/wgt/wgt.conf`, cwd `/`, env `PATH`, `PWD`, `SHLVL`, `HOME=/var/root` and `_`, and the reviewed content. No `PWNED*` file anywhere.
- **Probe 3 (`scratchpad/s7probe/wgq_probe.py`):** P2-A.
- **Probe 4 (`scratchpad/s7probe/fifo.py`):** 12 runs while a thread swapped the source between the reviewed file and a FIFO. Results:
  ```
  [(0,0.3),(98,0.0),(98,0.0),(98,21.2),(0,0.1),(0,0.2),(0,0.2),(0,0.2),(0,0.2),(98,0.0),(0,0.1),(0,0.2)]
  ```
  Content that ran: only `PostUp = GOOD`. The 21.2 s run is the watchdog firing; spec §14 lists this as "not covered by a test".
- **Probe 5 (`scratchpad/s7probe/rename.py`):** P3-A.
- **Permissions:**
  - `/private/var/run`: `root:daemon` `drwxrwxr-x`.
  - `/sbin/sha256`, `/usr/bin/head`, `/usr/bin/stat`: `root:wheel` 0755.
  - `dscl . -read /Groups/daemon GroupMembership`: `root`.

## Not verified (owner checklist, real Mac)
- `osascript -e 'do shell script "env" with administrator privileges'` as Sentinel's `DIALOG_ENV` would send it: what root's outer `/bin/sh` sees, and that the dialog still appears with `PATH` as the only variable.
- Real `wg-quick up`/`down` on the copy. Whether a stale `.name` survives a crash or a reboot (P3-C). The interpreter and owner of a Homebrew `wg-quick` (P2-A, last bullet of the fix).
- `chown`/`chmod` results as root under `/private/var/run/sentinel-vpn`. The daemon-group race on APFS (P3-A). A stalling-stat source (P3-B).

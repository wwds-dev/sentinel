# S8a/S8b port: independent code review (2026-11-06)

**Scope:** uncommitted diff in worktree `scratchpad/s8` (branch `fix/s8-openvpn-root-copy`, base `origin/main` 9f42ff1): `services/vpn_rootscript.py`, `openvpn_manager.py`, `vpn_execution.py`, `vpn_connection.py`, `privileged.py`, the docs, spec §15 and the ADR 0004 amendment.

**Playbook:** `docs/playbooks/code-reviewer.md` is `status: current`, last reviewed 2026-10-14 (23 days ago), so it is not stale.

**Verdict:** no P0 or P1. The core of `OVPN_SCRIPT` holds up. Two P2s should be fixed, or deferred by the owner, before merge.

What was checked and found correct:
- **Argument parsing and lock re-exec:** `args=("$@")` is captured before `shift 9`, and the `lockf` re-exec passes `"$0" locked "${args[@]}"`.
- **Lock does not leak:** a daemonised child does not inherit the lock. Measured: `lockf -k ./lk bash -c '(sleep 6 &)'`, then `lockf -t 0 ./lk echo` got the lock at once.
- **Variable scoping:** `grab` and `root_owned` use `local`; the globals `c` and `f` that `tracked` sets are not used anywhere else.
- **Cleanup on Connect failures:** every refusal in Up, and a non-zero openvpn exit, calls `forget`.
- **Pattern in `tracked`:** `"$2"` sits inside double quotes, so it matches literally. Measured with paths containing `[2]` and `*`.
- **Fingerprint:** it covers `includes`, `program` and `programs`, and `execute()` passes exactly those fields to root.
- **Config folder:** the review and root derive it from the same `_absolute()` path, without normalising it.
- **WireGuard:** `ROOT_SCRIPT` and `wireguard_argv` are unchanged in the diff. The only WireGuard behaviour change is P3-7.

**Tests:**
- The VPN suites (rootscript, review_gate, connection, execution, killswitch, keeps_no_logs): 979 passed.
- Full suite with `QT_QPA_PLATFORM=offscreen`: 4163 passed, 14 skipped, 1 failed. The failure is the known LM Studio one, `tests/test_providers_registry.py::test_server_off_falls_back_to_the_disk_listing`.

## P2

### P2-1: an absolute data file that only root can read always blocks Connect
**Location:** `services/vpn_execution.py:1121-1143`
- **Scenario:** a profile has `key /etc/openvpn/client.key`, the usual secure layout (root:wheel 0600, folder root 0755). The `lstat` and the root-ownership check pass, then `os.open` fails with EACCES. The review blocks with "The config reads /etc/openvpn/client.key, which cannot be read."
- **Why it matters:**
  - On main, root read that file and Connect worked.
  - The rule asks for a file that root owns yet your account can read. The only ways through are `chmod 644` on a private key, which exposes it to every local account, or moving it into your folder.
  - It is not documented.
- **Fix:**
  - The root-owned-all-the-way-up rule already rules out a same-user swap. So send such files to root as an ownership-checked list, the way `programs` works (`root_owned`), instead of requiring a hash computed as your user.
  - At minimum, give an actionable message and document it.
  - Add a test.

### P2-2: a status poll deletes the files that track a legacy tunnel after any failed check, including a transient one
**Location:** `services/openvpn_manager.py:179-183`
- **Cause:** `_forget_legacy()` runs whenever `_is_tracked_process` (`:296-302`) or `_process_age` (`:140-151`) returns False or None. That includes a `ps` timeout (3 s) or a non-zero exit.
- **Scenario:**
  1. A tunnel started by the previous build is up.
  2. The health monitor polls `is_running()` (`ui/panels/vpn.py:1054`, `services/vpn_connection.py:216,294`).
  3. One `ps` call times out under load.
  4. The legacy pid, device and log files are unlinked.
  5. From then on `_tracked()` returns None. Disconnect says "Cannot verify a Sentinel-owned OpenVPN process". `tunnel_device()` returns None, so the kill switch loses its device. Connect is allowed to start a second openvpn alongside the first.
- **Docs:** CHANGELOG and spec say the files are deleted "once it is gone", which the code does not limit itself to.
- **Fix:**
  - Forget only after positive evidence: ESRCH from `os.kill(pid, 0)`, or `ps` succeeds and shows a different program.
  - Never forget on a timeout or None.
  - Do it in `connect` and `disconnect`, not on read paths the UI polls.

## P3

### P3-1: key copies, pid and device outlive the tunnel
- **Path 1:** Down's "Signal sent; OpenVPN is still running." exits 0 without `forget` (`vpn_rootscript.py:427-429`).
- **Path 2:** an openvpn that exits on its own after Connect (auth failure, `ping-exit`). After that `_tracked()` is None, so Disconnect never calls root again. `ovpn/private/cfg` keeps the key copies until the next Connect, or a reboot (not verified).
- **Docs that state removal unconditionally:** `docs/agents/vpn.md` ("waits up to ten seconds, and then removes the copies"), the ADR amendment and spec §15.
- **Fix:** when root's pid file exists but no tracked process holds it, still run Down; it already sweeps on "not tracked" (`:415-419`). Correct the docs.

### P3-2: the review and root disagree about root-owned symlinks with mode 0777
- **Where:** `root_owned` uses `stat -f '%u %Lp'`, which is an lstat (`vpn_rootscript.py:333-344`). `_unsafe_executable_reason` uses `os.stat`, which follows the link (`vpn_execution.py:449,682-692`).
- **Measured:** a link made under `umask 000` gives `%Lp` = 777, while `os.stat` gives 0o100755.
- **Scenario:** a hook reached through such a link passes the review, then root exits 98 after the password. It fails closed; the cost is usability.
- **Fix:** skip the mode test for symlinks in `root_owned` (macOS ignores link modes), or have Python use lstat on links.

### P3-3: the review does not block an openvpn path containing whitespace, which the builder refuses
- **Where:** `openvpn_argv` raises (`vpn_rootscript.py:509`), but the review only blocks an empty `program` (`vpn_execution.py:1602`).
- **Scenario:** you confirm the review, then get "Nothing was run: The openvpn path must not contain spaces."
- **Fix:** add the same check as a review blocker.

### P3-4: `capath` and `crl-verify <dir> dir` now always block
- **Where:** both are in `OPENVPN_FILE_KEYS` (`:152`), and `_openvpn_includes` requires a regular file (`:1118`), so the review says "not a regular file".
- **Regression:** an absolute `capath /etc/ssl/certs` worked on main.
- **Fix:** handle directory-valued directives explicitly, either a clear block or a root-owned directory checked by ownership. List it under the CHANGELOG behaviour change.

### P3-5: the port dropped two residuals the owner had deferred
- **Source:** the old line's `TODO.md` (`git show claude/s8-r7-detached:TODO.md`, lines 132 and 141) carried:
  - S8b security P3-4: manifest hashes, including those of password files, travel as argv and are visible to `ps`. This also contradicts the `privileged.py` header, "Nothing secret is ever passed through here".
  - S8b security P3-2: Homebrew's `openssl.cnf`, module folder and linked libraries, which openvpn loads as root.
- **Now:** neither appears in this `TODO.md`, in spec §15 "Limits that remain", or in the "Not protected" note in `docs/agents/vpn.md`.
- **Fix:** add both back to the backlog and name them in the docs.

### P3-6: exit codes do not match their documentation
- **Docs:** `vpn_rootscript.py:224` says "as ROOT_SCRIPT, plus 99", and the module docstring (`:27-33`) defines 98 as "the config changed" and says nothing ran for any of the listed codes.
- **What `OVPN_SCRIPT` also does:**
  - 98 for a hook program that is not root-owned (`:373`) and for a changed recorded pid (`:413`).
  - 1 for a failed signal (`:421`).
  - 0 for "signal sent; still running" (`:429`).
- **Measured:** a file count of `08` aborts bash with exit 1 (`/bin/bash: 08: value too great for base`), not 99. It still fails closed.
- **Consumers:** none. The `EXIT_*` constants are defined but nothing in Python reads them.
- **Fix:** write an OpenVPN exit-code table, and use the regex `^(0|[1-9][0-9]?)$` for the count.

### P3-7: one WireGuard behaviour change is not in the CHANGELOG or `vpn.md`
- **What changed:** the hidden-prefix rule in `_unsafe_executable_reason` (`vpn_execution.py:668-672`) is shared with the WireGuard hook checks (`:730,800,819`) and the wg-quick warning (`:1554`). A WireGuard hook path such as `/.vol/...`, or any path whose first component starts with ".", is now refused.
- **Assessment:** it is a safe tightening, and spec §15 mentions it, but the CHANGELOG and `vpn.md` mention it only for OpenVPN.
- **Fix:** one line in the CHANGELOG.

## Not verified
- Anything involving a real openvpn, which is not installed: `--cd` before `--config`, file descriptors after `--daemon` and `--log`, when the pid file is written, the device line, and whether a SIGUSR1 restart re-reads `cfg/`.
- Any run as root, or anything under `/private/var/run`. Neither was attempted.
- `/bin/realpath` on macOS versions before 13.
- ACLs, which neither the review nor root checks.
- What happens when the root script is killed between starting openvpn and deleting the log (the log is presumably left in `private/` until the next `forget`). Reasoned only.
- Timing inside the dialog's 30 + 90 s window.
- The spec's mutation counts (39/39 and 66/66).

## Resolution (author, 2026-11-06; details in spec §15 "Round 2")
| Finding | Status |
|---|---|
| P2-1 | Fixed: a root-only absolute file joins the ownership re-check (`root_only`), with a review warning; tests in `test_vpn_review_gate.py`. |
| P2-2 | Fixed: `_tracked()` deletes nothing; `_sweep_legacy()` in Connect/Disconnect only on `ESRCH` or `ps` showing another program; tests for a `ps` timeout and a vanished process. |
| P3-1 | Fixed: Disconnect asks root to sweep when root's pid is no longer Sentinel's openvpn; docs corrected ("still running" keeps the copies on purpose). |
| P3-2 | Fixed: `root_owned` ignores a link's own mode; harness test with `lchmod 777`. |
| P3-3 | Fixed: review blocker for an openvpn path with a space. |
| P3-4 | Fixed: clear "is a folder" blocker; CHANGELOG. |
| P3-5 | Fixed: both deferrals back in TODO.md and `docs/agents/vpn.md`. |
| P3-6 | Fixed: exit-code table above `OVPN_SCRIPT`; count regex `^(0|[1-9][0-9]?)$`; test. |
| P3-7 | Fixed: CHANGELOG line. |

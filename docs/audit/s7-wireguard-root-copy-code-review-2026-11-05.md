# Code review: S7, the root-owned verified copy of a WireGuard config (commit 7bca617)

**Verdict: changes needed.** One P1 is old behaviour that this change does not fix, but its documents say it does. The owner either fixes it or defers it. There are also two P2s. The shell script itself is correct: no logic error was found in its exit-status handling, its quoting or the macOS tools it calls.

Reviewer: independent `code-reviewer`, read-only, 2026-11-05. No edits, nothing staged.
Checkout: scratch worktree of `fix/s7-wireguard-root-copy` @ 7bca617.
Inputs:
- Playbook `docs/playbooks/code-reviewer.md`: `status: current`, `last-reviewed: 2026-10-14` (22 days old, not stale).
- `docs/company/app-context.md` is not in this checkout. It has been untracked since c4ac6a3, so it was read from the main checkout.
- The worktree already showed `M docs/company/activity.jsonl` before the review started (phase logging, not the reviewer's).

---

## Findings

### P1-1: root still runs wg-quick (and its whole directory on PATH) from a folder the user can write to. The spec and ADR say this never happens.
- **Where:**
  - `services/vpn_rootscript.py:46`: `PATH="${wgq%/*}:/usr/bin:..."`
  - `services/vpn_rootscript.py:77,83`: `"$wgq" up|down "$copy"`
  - `services/vpn_connection.py:127`: `exe = shutil.which("wg-quick")`
- **What the docs claim:**
  - ADR 0004 lines 38-39: "No path in a user-writable directory is used after the hash check."
  - Spec line 11: "The root script never trusts a path in a user-writable directory after the hash check."
  - CHANGELOG.md:43: "runs only the copy".
- **Evidence:** `ls -ld /opt/homebrew/bin` returns `drwxrwxr-x as admin`. On Apple silicon, Homebrew's bin folder belongs to the user. `shutil.which` returns `/opt/homebrew/bin/wg-quick`, and the script then puts that folder first on PATH. So root also picks up `wg`, `wireguard-go`, and `bash` (if wg-quick starts with an `env bash` line) from a folder the user can write to.
- **Failure scenario:** S7 defends against a process running as the user. That process does not need to win the race on the `.conf` any more. At any time it can replace `/opt/homebrew/bin/wg-quick`, or drop a `wg` there. At the next Connect or Disconnect the hash check passes and root runs the attacker's file. This is not a regression: the old code did the same. But S7's stated guarantee does not hold, and review already applies this exact rule to hook programs (owner decision D1, `_unsafe_executable_reason`).
- **Fix (owner decision):**
  - Option (a): apply `_unsafe_executable_reason` to the resolved wg-quick and to its folder, since that folder goes first on PATH. Then either block, or warn, in review. Blocking would stop every Homebrew user, which is why this needs the owner.
  - Option (b), the minimum: reword ADR decision 3, spec line 11 and the CHANGELOG to name this remaining gap. Add a TODO linked to the non-goal in spec §7 (a privileged helper).

### P2-1: the Disconnect dialog describes the user's current file, but root runs the copy kept since Connect
- **Where:**
  - `services/vpn_execution.py:561-564` (`_hash_line`).
  - `services/vpn_execution.py:705-718`: hooks and risks come from the user's file on Disconnect.
  - `services/vpn_rootscript.py:82`: when a kept copy exists, the script ignores `$want` (the hash).
- **Failure scenario:** the user connects with config A. While connected, they edit the file to B, which adds `PostDown = /usr/bin/true`. Then they press Disconnect. Reproduced:
  - The dialog shows `RUNS COMMANDS AS ADMINISTRATOR — this config executes: • PostDown: /usr/bin/true`.
  - It shows `Config SHA-256: d506… (checked again by root on its own copy, which is what runs)`.
  - Root actually runs A's copy. B's hash is never checked and B's hook never runs. A's down hooks, which may differ, are not shown.
  - The reverse case also happens: the user deletes a hook they regret, the dialog lists no hooks, and root runs the old hook.
  - The same mismatch drives the "Disconnect anyway?" risk prompt, which is based on hooks that will not run.
- **Why this matters:** `docs/agents/vpn.md` and the training page explain the behaviour correctly. The confirmation dialog, which is the security-critical text, does not.
- **Spec gap:** spec §1.2 says the hook list in a fresh review applies only when no copy exists. But Sentinel cannot see into the 0700 folder (spec §14), so the review cannot know which case applies.
- **Fix:**
  - For a WireGuard Disconnect, make the hash line and the hooks heading conditional. For example: "Your file now (used only if Sentinel has no copy from Connect; otherwise the copy you connected with runs)".
  - Have the script print which path it took, e.g. `echo "used the copy kept since Connect"`, so the outcome reports it.
  - Add a test for the Disconnect wording. Today `test_the_hash_line_says_when_it_was_checked` only covers Connect.

### P2-2: nothing tests the PATH line root depends on; a mutant that deletes it survives
- **Where:**
  - `services/vpn_rootscript.py:46`.
  - Old assertion removed: `assert r.command.startswith("PATH=")`, previously in `tests/test_vpn_execution.py` `test_rollback_is_a_command_a_person_can_type`.
  - The only remaining PATH test, `tests/test_vpn_execution.py:194-200`, tests `wg_quick_command`, which production code no longer calls.
- **Evidence:** a mutant patching `rs.ROOT_SCRIPT` in memory and running `tests/test_vpn_rootscript.py`:
  - `PATH="${wgq%/*}:/usr/bin` changed to `PATH="/usr/bin`: **SURVIVED**, 41 passed.
  - Control mutant (`up "$copy"` changed to `up "$src"`): KILLED. This confirms the harness works.
- **Failure scenario:** someone tidies line 46 and drops the wg-quick folder. The administrator shell has no `/opt/homebrew/bin`, so wg-quick's calls to `wg` and `wireguard-go` fail with "command not found". Every real Connect breaks while the suite stays green. `wg_quick_command`'s docstring says this failure has happened before.
- **Fix:** have the fake wg-quick record `$PATH` (or `command -v wg`) and assert that its first entry is the wg-quick folder. Then delete the dead `wg_quick_command` test, or point it at the script.
- **Same gap, lower risk:** the `chown` and `chmod` lines also survive mutation (`drop chown`: SURVIVED). The harness runs with the user's own uid as owner, so those lines can only be checked as root, which the owner checklist S7 covers. Add `stat -f '%Su:%Sg %Lp'` on the base and `wg/` folders to that checklist, expecting `root:wheel 755` and `700`.

### P3-1: a WireGuard config without `.conf` passes review, then fails with a message that sends the user in a loop
- **Where:**
  - `services/vpn_execution.py:624-629`: review takes the interface from `Path.stem`.
  - `services/vpn_rootscript.py` `interface_for`: requires `.conf`.
  - `services/vpn_connection.py:126-134`.
- **Evidence:** with `config_path=/…/wg0.txt`, which `profile_from_config` (vpn_connection.py:421-437) imports as WireGuard:
  - Review: `allowed: True`.
  - Dialog command: `…/wg/<interface>/<interface>.conf`.
  - Execute: `ran: True`, no root call, error `Nothing was run: invalid WireGuard interface name ''. Review the profile again.`
  - Reviewing again gives the same allowed review, so the user loops. With `ran=True` the result section reads "The command failed: Nothing was run…".
- **Fix:** take review's interface from `vpn_rootscript.interface_for(target)` and block on `None` with the existing "rename the file" message. Then the error path in `_wg_root_run` cannot be reached from the UI.

### P3-2: exit 97 means two things, and failing to read the source is untested
- **Where:** `services/vpn_rootscript.py:69-70`. The docstring at lines 13-15 says 97 means "a directory Sentinel owns is not what it should be".
- **Evidence:** run through osascript `do shell script` without administrator rights, with the source missing, the user sees `execution error: head: /…/wgt.conf: No such file or directory (97)`. No "nothing was run" text appears, and no test covers this branch.
- **Failure scenario:** the user's file is deleted or made unreadable between the dialog and root's read. The error is technically correct, but the exit-code contract in the docstring is wrong.
- **Fix:** use a separate code, or print `refused: could not read the config; nothing was run.` before `exit 97`, update the docstring, and add a harness test.

### P3-3: the Disconnect summary for an interface-only profile tells the user to copy a file that does not exist
- **Where:** `services/vpn_connection.py:111-112`.
- **Failure scenario:** take an interface-only profile with no `.conf` in `WG_CONFIG_DIRS`. The review target is the bare name `wg0`. The dialog says "without one, copy 'wg0' and check its SHA-256 first", but there is nothing to copy, and with no kept copy the script exits 96.
- **Fix:** when the target is not an absolute path, say "the copy kept since Connect; without one, nothing runs".

### P3-4: `wg_quick_command` is now unused, and comments still point to it
- **Where:** `services/vpn_connection.py:87-99`. Production code no longer calls it. `services/vpn_execution.py:333,340` (`_which_privileged`) still names it as the source of root's PATH, which now comes from `vpn_rootscript.py:46`. `openvpn_manager.py:50` also mentions it.
- **Fix:** remove it, or mark it as test-only, and point the comments at `vpn_rootscript`. That matters because `_which_privileged` has to match the script's PATH order, and today nothing checks that.

### P3-5: ADR 0004 still describes `install -d` and `/bin/cat`; the code does something else
- **Where:** `docs/adr/0004-root-runs-verified-copies.md:33,36` versus the code (`mkdir -m` + `chown`, `head -c 262145`).
- **Why it matters:** the deviations are recorded only in spec §14. The ADR is the record of the decision and calls itself "on-disk contract".
- **Fix:** add a short amendment line to the ADR that points to spec §14.

---

## Checked and found correct
- **`rc=$?` inside `|| { … }` and `|| exit`:** both keep wg-quick's exit status under bash 3.2.57, the system `/bin/bash` here. The harness's rc=3 and rc=1 tests pass.
- **No `set -e`:** every failure that matters exits explicitly. A failing `rm -rf` after a successful `down` would only change the reported status. That is negligible, so not listed.
- **BSD tools:** `/usr/bin/head -c N -- file`, `/usr/bin/shasum -a 256 -- file` and `${got%% *}` parsing all work on macOS 27.0.1 (26A434). All of `/usr/bin/shasum` (perl), `/usr/sbin/chown`, `/usr/bin/stat`, `/bin/{mkdir,chmod,rm,bash}` exist and are owned by root:wheel. shasum puts a `\` in front of the hash when a filename contains `\` or a newline. The interface rule excludes both, and the base folder is a constant, so this cannot happen.
- **Quoting chain:** `shlex.join` → `applescript_string` → `do shell script`, checked end to end through osascript without administrator rights, using a source path containing a space and `'`, config text containing `"`, `\` and `$HOME`, and the `'` inside the script's own comment. All values arrived intact and the copy matched byte for byte.
- **Fail-closed checks:**
  - Connect with no hash: ValueError, so the administrator prompt never opens.
  - A relative path: refused.
  - A `~` path: expanded in the same way as review.
  - `.` and `..` as interface names: refused by the script.
  - Symlinked base or `wg/` folder, or a base owned by another user: exit 97.
- **Execute:** the extra `config_sha256` keyword is harmless for OpenVPN and the VPN apps. The only caller of `execute` is `ui/workers.py:633`.
- **Tests weakened against main:** only the `startswith("PATH=")` line (see P2-2). The other rewrites are equivalent or stronger.

## What was run
- `pytest -p no:cacheprovider -q tests/test_vpn_rootscript.py tests/test_vpn_connection.py tests/test_vpn_execution.py tests/test_vpn_review_gate.py tests/test_contract_tunnel_chat.py`: **327 passed** (61s).
- Full suite, with the two known main failures deselected: **3275 passed, 14 skipped, 3 deselected** (230s).
- osascript end-to-end harness (non-admin). Up: 0. Down using the kept copy: 0. Down with no copy: 96 + message. Changed file: 98 + message. Missing source: 97 with head's error.
- Mutants on `ROOT_SCRIPT`. PATH line: survived. chown: survived. chmod: survived. Control: killed.
- Review edge cases: non-`.conf` path; Disconnect dialog text.
- `ls -ld /opt/homebrew /opt/homebrew/bin /private/var/run`, `sw_vers`, `/bin/bash --version`.

The probe scripts live in the session scratchpad and are not committed.

## Not verified
- The real run as root through the administrator dialog. No privileged action was run.
- Real ownership of `/private/var/run/sentinel-vpn` after `chown`.
- Real wg-quick on the copy. wireguard-tools is not installed on this Mac, so wg-quick's shebang and how it resolves `wg` and `wireguard-go` were not seen.
- Whether `/var/run` is emptied at reboot.
- Disconnecting a tunnel that the pre-S7 build started.
- The FIFO hang that spec §14 lists.
- Linux CI: the bash harness is skipped off macOS (`on_macos`), so the ubuntu job never runs the script. CI was not run.

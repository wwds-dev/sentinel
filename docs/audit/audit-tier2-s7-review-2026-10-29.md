# Audit tier-2 S7 — independent code and security review (2026-10-29)

Reviewer: code-reviewer agent (read-only; did not write this code). Scope: the uncommitted S7 changes in worktree `intelligent-archimedes-e15a0e`. Saved verbatim by the author's session; the author's resolution is appended at the end.

Process notes: `docs/playbooks/code-reviewer.md` is `status: current` (last-reviewed 2026-10-14), not stale. **`docs/company/app-context.md` does not exist in this worktree**, so the domain-risk list it is meant to hold could not be checked.

## Verdict
S7 closes the race it set out to close: on every path where root runs wg-quick, it gets the copy under the run dir, never the user's path (Up `vpn_rootscript.py:125-127`; Down case 1 re-hashed `:133`; case 2 staged and hashed `:136-137`; case 3 re-hashed against the stored hash `:138`). Quoting through `shlex.join` → `applescript_string` survives a hostile path (reproduced), the fingerprint covers `stored_sha256`, `execute()` hands root the hash of the review whose fingerprint matched the confirmed one, and gated actions under a root-run Sentinel are still refused. **No P0. Three P1s**: (1) root's liveness check is weaker than both the Python probe and wg-quick's own, so a stale `.name` file locks Connect out and Disconnect cannot clear it; (2) a non-UTF-8 config passes the Disconnect review with its hooks hidden, and root then runs them; (3) the new bash-harness tests depend on macOS tools but CI also runs on `ubuntu-latest`. The suite I was asked to run passes:

```
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vpn_rootscript.py tests/test_vpn_review_gate.py tests/test_vpn_connection.py tests/test_vpn_execution.py tests/test_contract_tunnel_chat.py
354 passed in 59.60s
```

No weakened tests: every changed assertion follows the new argv shape (stricter, e.g. `str(path) not in r.command`) or is one of the D9 inversions listed in spec section 15.

## Findings

### P1-1. `live()` counts any existing utun as live: a stale record blocks Connect and Disconnect cannot clear it
- Where: `services/vpn_rootscript.py:91-97` (live), `:123` (exit 96 on Up), `:144` (`live || forget`).
- Root decides "live" from `<wgstate>/<iface>.name` plus `/sbin/ifconfig utunN` succeeding. The Python probe treats a `.name` whose `<utun>.sock` is missing as stale (`services/vpn_execution.py:473-474`). This Mac has `utun0`..`utun9` up for system services (`ifconfig -l`).
- Scenario: wireguard-go dies without `wg-quick down` (or `/var/run/wireguard` survives a reboot; the ADR marks boot clean-up UNVERIFIED), leaving `wg0.name` naming a utun some other service now owns. The review says not up and allows Connect; root exits 96 "already up; Disconnect it first". Disconnect runs wg-quick down, which fails; `live()` is still true, so the copy is kept and Connect is refused again. The user is stuck until root deletes the `.name` file by hand. Before S7, wg-quick up made its own (socket-based) decision and proceeded.
- Evidence (harness repro, my user, tmp dirs, stub wg-quick):
  ```
  python probe: {'up': False, 'device': 'utun3', 'stale': True}
  root up rc: 96  Tunnel: wg0 is already up; Disconnect it first.
  down rc: 1 wg-quick: `wg0' is not a WireGuard interface      (stub simulating real wg-quick; real behaviour UNVERIFIED, wg-quick not installed here)
  up rc: 96 ...      copy kept: True
  ```
- Fix: in `live()` also require `[ -S "$wgstate/$name.sock" ]` (as the Python probe does), ideally plus wg-quick's sock/name mtime-proximity rule; add a harness test where the name is an existing utun with no `.sock` and the result is not live. The existing live tests must then create a socket.

### P1-2. Disconnect with a non-UTF-8 config runs PreDown/PostDown the dialog never showed
- Where: `services/vpn_execution.py:299` (non-UTF-8 returns `text=None` with a digest), `:742-743` (`elif config_sha256: risks.extend(read_blockers)`).
- Scenario: a same-user process adds one invalid byte (e.g. in a comment) and `PostDown = /tmp/evil-hook` to the user's `.conf`. The Disconnect review shows only the risk "The selected configuration is not readable UTF-8 text."; `root_hooks` is empty and the hook is not in the dialog text. After "Disconnect anyway", root verifies the hash (which matches the attacker's bytes) and runs the hook as root. A UTF-8 file with the same hook would have it listed.
- Evidence (scratchpad repro):
  ```
  allowed: True | blockers: ()
  risks: ('The selected configuration is not readable UTF-8 text.',)
  root_hooks shown: () | sha set: True
  hook in confirmation text: False
  ```
- Older than S7 in shape (it was `elif source.exists()`), but S7 rewrote this branch, and it contradicts the D9 rationale ("a same-user attacker can arrange exactly that state") and the CHANGELOG line "root runs only the WireGuard config you reviewed". On a Homebrew install R1 already gives such an attacker a root path; once R1 is closed, this is the bypass.
- Fix: decode with `errors="replace"` for hook extraction and `_hook_findings` so hooks are listed and soft-blocked as for UTF-8, or treat non-UTF-8 like unreadable (stored copy as risk, else the D9 blocker). Add a test.

### P1-3. The bash-harness tests are macOS-only, but CI runs ubuntu-latest too
- Where: `tests/test_vpn_rootscript.py:194-417` (all `allow_process` tests). The script hard-codes `/usr/sbin/chown` (`vpn_rootscript.py:50`), BSD `stat -f '%u:%g:%Lp'` (`:54`; on GNU `stat -f` means file-system status), `sysctl -n kern.bootsessionuuid` (`:59`) and `/sbin/ifconfig` (`:96`). `.github/workflows/tests.yml:13` runs `os: [ubuntu-latest, macos-latest]`.
- Scenario: on Linux the prologue exits 97 in nearly every harness test, so the Ubuntu job goes red; AGENTS.md's definition of done requires CI green. UNVERIFIED by run (no Linux here), but each path is missing or behaves differently on stock Ubuntu. The spec (14.3) says "stub `wg-quick`/`ifconfig`/`sysctl` on PATH", which cannot work with absolute paths, and the tests in fact use the real tools. `tests/test_macaddr.py:138` substitutes tool paths in `ROOT_SCRIPT` instead.
- Fix: `pytestmark = pytest.mark.skipif(sys.platform != "darwin", ...)` on the harness tests, or substitute fakes for `/usr/sbin/sysctl`, `/usr/bin/stat`, `/usr/sbin/chown` and `/sbin/ifconfig` as `test_macaddr` does (also makes the live and boot tests deterministic).

### P2-4. The D9 tests read the real `/private/var/run`, so they flip once the owner has a real copy
- Where: `services/vpn_rootscript.py:216` (`base: str = RUN_DIR` bound at definition), called without override from `vpn_execution.py:781`; the "no stored copy" tests do not patch it (`tests/test_vpn_execution.py:177`, `tests/test_vpn_review_gate.py:548`, `:568`). No conftest fixture.
- Scenario: the owner runs S7 checklist step 1 with a real `wg0` and leaves it up (or a Disconnect fails), leaving a root-owned `wg-sha256/wg0`; `test_disconnect_with_no_readable_config_and_no_stored_copy_is_refused` then fails. Machine-state dependent.
- Fix: an autouse conftest fixture redirecting the sidecar base to `tmp_path`, or a module variable read at call time.

### P2-5. Caller's environment may reach the root bash (`BASH_ENV`, `PERL5OPT`) — UNVERIFIED link
- Where: `agents/vpn_agent/services/privileged.py:36` (`subprocess.run` without `env=`), `vpn_rootscript.py:187` (`/bin/bash -c`), `:98` (`/usr/bin/shasum` is `#!/usr/bin/perl`).
- Shown here: a non-POSIX `bash -c` sources `BASH_ENV` while the outer `/bin/sh` does not:
  ```
  BASH_ENV=$S/benv.sh /bin/sh -c "/bin/bash -c 'true' x"   -> BASH_ENV-SOURCED
  BASH_ENV=$S/benv.sh /bin/sh -c 'true'                    -> (nothing)
  ```
- Not verified: whether `do shell script ... with administrator privileges` forwards the caller's environment to root. If it does, a same-user process that sets `BASH_ENV`/`PERL5OPT` for GUI apps (`launchctl setenv`) gets code run as root before the hash check, defeating S7 even with a root-owned wg-quick. The same exposure already existed for wg-quick (a bash script) and the MAC changer's `ROOT_SCRIPT`.
- Fix (cheap): build argv as `["/usr/bin/env", "-i", "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "/bin/bash", "-c", WG_SCRIPT, ...]`. Owner-checklist step: set a marker variable in Sentinel's environment and see whether `do shell script "env" with administrator privileges` shows it.

### P2-6. No mutual exclusion between two root scripts for the same interface; a hung copy keeps root blocked indefinitely
- Where: `vpn_rootscript.py:102-119` (shared `wg-new/<iface>`, `wg/<iface>`, `wg-sha256/<iface>`, no lock); `privileged.py:98-100` documents that root keeps running after osascript times out.
- Scenario A (FIFO): swap the source to a FIFO between `[ -f ]` and `head` (`:105-108`); root's `head` blocks. At osascript timeout the root script keeps waiting (spec line 323 "makes root wait until the dialog's timeout" is inaccurate). When a writer later opens the FIFO, the old script resumes and hashes whatever is at `$stage/$iface.conf` at that moment, possibly a later run's staging file, which it can then `mv` away (97 there).
- Scenario B (slow run): a slow wg-quick up outlives the 120 s dialog wait, the user presses Connect again, and the two scripts interleave `forget`/`keep_stage` on the same paths. The tunnel may come up after the UI reported failure; sidecar and copy can disagree.
- Every wg-quick run still uses bytes that matched some confirmed review's hash (by my reading), so this is state corruption, not unreviewed code execution.
- Fix: a per-interface lock in root (e.g. `mkdir "$base/lock-$iface"` released by a trap, a lock from an earlier boot treated as stale, or `/usr/bin/shlock`); bound the copy (e.g. `head` in the background, killed after N seconds). Correct spec line 323.

### P3-7. `keep_stage` does not check that `rm -rf "$dir"` worked; `mv` can nest the new copy and the old copy runs
- Where: `vpn_rootscript.py:116-117`, `:120`. If `$dir` survives the `rm` (immutable flag, file-system error; root-only causes), `mv "$stage" "$dir"` moves the stage into `$dir/$iface`, and `run_wgq` runs the old `$copy`: verified at an earlier review, not this one.
- Fix: `[ ! -e "$dir" ] || { forget; refuse 97 ...; }` before the `mv`, and/or re-hash `$copy` against `$want` right before `run_wgq`.

### P3-8. Control characters in a path are dropped on the dialog path, so root reads a different path than the review did
- Where: `vpn_rootscript.py:151` rejects only NUL/CR/LF; `privileged.py:81` drops every other C0 except tab. Repro: `builder accepts ESC in path: True | dialog string keeps it: False`. Fails closed (hash must match), but the user gets "the config changed after you confirmed it" for an unchanged file.
- Fix: in `_absolute`, reject `ord(ch) < 32` and `0x7f`.

### P3-9. A `~` or relative `config_path` passes review, then fails after confirmation
- The review reads `Path(config_path).expanduser()`, but `vpn_connection.py:181/:212` pass the raw string and `_absolute` raises. A hand-edited profile with `config_path: "~/vpn/wg0.conf"` is hashed and confirmed, then fails with "Nothing was run: The config must be an absolute path". Fails closed; imports always store absolute paths (`vpn_connection.py:449`).
- Fix: normalise once (absolute, expanded) for the review target and `connect`/`disconnect`, or make a non-absolute path a review blocker.

### P3-10. Docs claims the code does not support
- Spec `:263` "Exit codes 96-99 map to sentences in `vpn_connection`": no mapping exists; the `EXIT_*` constants are used only in tests. The user sees raw osascript error text.
- `docs/agents/vpn.md:48`: the copy "is removed on Disconnect or at the next restart". In code the boot wipe happens at the first root script after a restart; until then a key-bearing copy stays under `/private/var/run` unless macOS clears it at boot (UNVERIFIED, ADR R3).
- Spec line 323 (see P2-6); spec 14.3 stubs-on-PATH (see P1-3).
- Spec section 15 says 55 tests; 56 are collected. Its list of deliberately changed tests omits two that gained the `stored_copy` fixture (`test_verified_disconnect`, `test_the_confirmation_dialog_lists_the_risks_of_a_disconnect`); both consistent with D9, not weakened.

### P3-11. Test gaps
- The boot-wipe test checks only `wg/`, not that `wg-sha256/` and `wg-new/` are removed.
- No test for Down case 2 succeeding with no stored copy at all (the migration case in ADR "Consequences").
- Partial-failure paths (`mv` fails, sidecar write fails, `vpn_rootscript.py:117-118`) untested; needs root or file flags, acceptable, but say so in the spec.
- A mutant changing `elif config_sha256:` to `elif source.exists():` (`vpn_execution.py:742`) survives; its only effect is a duplicate risk line.
- I did not mutation-test the new branches myself.

## Checked and found sound
- AppleScript + `sh -c` round trip with the path `a'b"c$(touch PWNED)\`id\`\d e<TAB>f`: equal to `shlex.join`, wg-quick received only `up <copy>`, no PWNED file. `WG_SCRIPT` holds no control characters besides `\n`.
- `ensure_dir`: no symlinks, create-or-refuse; the explicit `chown` covers BSD's new-directory-takes-parent's-group rule (`root:daemon`). Under the base, only root can race.
- `rm -rf` targets are limited by the double interface check (no `.`/`..`, no `/`).
- `set -u`: all eight arguments are always passed. The boot-ID regex fits this Mac.
- Sidecar trust (`lstat`, regular file, uid 0, strict hex) is correct.
- On Disconnect `stored_sha256` is used only when the review has no file hash (ADR section 6, case 3).
- The production worker (`ui/workers.py:633`) uses the default dialog argv runner. OpenVPN behaviour is unchanged apart from the D10 warning.

## Resolution (author's session, 2026-10-29)
Fixed in round 2 (spec section 15, "Round 2"); not re-reviewed by an independent agent.

| Finding | Status | Where it is pinned |
|---|---|---|
| P1-1 | fixed: `live()` requires `<utun>.sock` | `test_a_name_file_without_wireguard_gos_socket_is_not_live`; the live tests use a real Unix socket |
| P1-2 | fixed: non-UTF-8 on Disconnect drops the hash (stored copy, or the D9 blocker) | `test_a_non_utf8_config_on_disconnect_*` (2) |
| P1-3 | fixed: harness skipped off macOS; `/sbin/ifconfig` is a stub | `on_macos` marker |
| P2-4 | fixed: `_SIDECAR_BASE` is read at call time; an autouse conftest fixture redirects it | `test_the_sidecar_base_is_read_at_call_time` |
| P2-5 | fixed: `CLEAN_ENV` (`env -i`) before the root bash | `test_the_root_bash_starts_with_an_empty_environment`; the forwarding question is owner checklist item 8 |
| P2-6 | deferred to TODO (P2) | none |
| P3-7 | fixed: refuse (97) if the old copy survives `rm -rf` | `test_a_previous_copy_that_cannot_be_removed_is_refused_not_nested` |
| P3-8 | fixed: control characters refused | builder parametrisation |
| P3-9 | deferred to TODO (P3) | none |
| P3-10 | fixed (spec, ADR, `docs/agents/vpn.md`) | none |
| P3-11 | boot wipe and migration case added; partial-failure paths still untested (they need root or file flags) | `test_files_from_an_earlier_boot_are_dropped`, `test_down_case_2_with_no_stored_copy_runs_the_reviewed_file` |

Mutation check after round 2: 35 of 35 mutants killed, covering the S7 logic and every round-2 fix (script: `scratchpad/mutate.py` in the session; the mutants are listed in the session transcript).

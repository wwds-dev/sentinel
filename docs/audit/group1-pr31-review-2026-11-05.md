# Review: PR #31 (Group 1 testing-readiness items), merge 46f4cc1

Reviewer: code-reviewer (independent, read-only). Date 2026-11-05. Playbook `code-reviewer.md` last-reviewed 2026-10-14, status current.
Scope: the 13 commits in PR #31. `services/vpn_execution.py` is excluded (another reviewer has it; this PR does not touch it anyway).

## Evidence
- New/changed tests in a scratch clone at 46f4cc1: 79 passed (test_panel_late_cost, test_reset_fence_writers, test_route_notice_visible, test_osint_catalog, agents/sentry/tests).
- `cd agents/sentry && pytest`: 25 passed (the pytest.ini `pythonpath = .` replacement for conftest.py works).
- Full root suite with QT_QPA_PLATFORM=offscreen: 3431 passed, 14 skipped. One failure is outside the PR and comes from this machine: `test_providers_registry::test_server_off_falls_back_to_the_disk_listing` fails because `~/.lmstudio/bin/lms` exists here and the isolation guard refuses to spawn it.
- No `QThread.terminate` was added. No fetch or blocking wait was added on the UI thread. The new writes in `record_stopped_request` run on the UI thread, which is the same single writer as every other usage row.

Mutants were planted in the scratch clone only:

| # | Mutant | Result |
|---|---|---|
| M1 | base.py: drop `signal.connect(self._record_stopped_cost)` | killed (6 fail) |
| M2 | main.py: drop the `abandon_request(... "cancelled" ...)` in record_stopped_request | killed |
| M3 | baseline.py: drop both post-lock `_writes_allowed()` re-checks | **survived** |
| M4 | main.py: drop the `writes_allowed()` check in record_stopped_request | killed |
| M5 | main.py: drop the `request_id is None -> None` guard | killed |
| M6 | osint_catalog.py: drop the reset guard | killed |
| M7 | main.py: route notice always hidden | killed |

## Findings

### P2-1: the Current Route card keeps a stale "Local only" notice once Auto-route is on
- Code: `main.py:3690-3700`. The auto-recommend branch of `resolve_backend_model` returns before any `_set_route_notice` call. Until this PR that notice went only to hidden labels and a tooltip. Now `route_notice_label` (`main.py:3042`) shows it inside the card.
- Reproduced in the scratch clone: Local only + openai picked gives the notice "Local only mode: using ollama · muse-glimmer:30b-mlx instead of openai…". Then Cloud only + Auto-route on: `resolve_backend_model()` returns `('anthropic', 'claude-sonnet-5')`, and the card still shows the Local-only notice, visible.
- Why it matters: the card claims a cloud request runs locally. That contradicts the Local-only invariant in app-context. The paid-request dialog still names the provider, so nothing is sent silently, which is why this is P2 and not P1.
- Fix: call `self._set_route_notice("")` in the auto branch before returning, or set the router's own reason there. Add a test that switches Auto-route on after a substitution and asserts the label is hidden.

### P3-1: the post-lock re-checks in the Sentry writers are untested (M3 survived)
- `agents/sentry/sentry/baseline.py:172-173` and `:201-202`. The guard checked before taking the lock catches every test case, so deleting the checks taken inside the lock changes no result.
- Fix: add a test that sets the latch from inside the lock. For example, wrap `_file_lock` or patch `load_findings` to call `latch.set()`, then assert nothing was written.

### P3-2: two Sentry paths are still unfenced (hardening, small window)
- `BaselineStore.__init__` and `default_state_dir()` call `mkdir(parents=True)` (`baseline.py:111-127`). `transaction()` opens `watch.lock` with O_CREAT (`baseline.py:157`). Neither asks `WRITE_GUARD`.
- `run_watch` (`engine.py:~246`) checks `should_stop` once, after the snapshot. If the cancel lands between that check and `store.transaction()`, the parked worker can recreate `data/sentry/watch.lock` while the reset is erasing. If `rmtree` is mid-directory at that moment, it can also turn the reset into a PartialEraseError.
- Commit 147898d4 says "no Sentry lock files mid-erase", but that holds only for the per-file locks.
- Fix: check `_writes_allowed()` at the top of `transaction()` and raise or return a cancelled result. Optionally guard the mkdir as well.

### P3-3: Forge docs now misstate how a stopped cloud run is closed
- Files: `docs/agents/manager.md:82`, `agents/manager_agent/README.md:84`. Both say a Stop "is closed with status `error`".
- For a cloud streaming request this is no longer true. The worker emits `stopped_usage_signal` before `error_signal` (`ui/workers.py:~410`), and `record_stopped_request` closes the run as `cancelled` first, so the later `abandon(error)` does nothing to the run. The failover offer can still appear, because `AgentPanel.abandon` calls `offer_panel_failover` on reason `error` whatever the host did.
- The doc claim still holds for local requests.
- Fix: reword it ("a local run is closed as error; a cloud run as cancelled; both can show the failover offer") or fix the TODO item it points to.

### P3-4: a stale comment on `ChatWorker.stopped_usage_signal`
- `ui/workers.py:362-364` still says "so only Chat records a stopped request's cost". Panels now connect to it too (`ui/panels/base.py:488-496`).
- Fix: update the comment.

### P3-5: the smoke script can SIGTERM the owner's real Sentinel
- `scripts/smoke_installed_quit.py:152-157` and the `finally` block at `:281`. `kill_leftovers(set())` sends signal 15 to every process that matches `PROC_RE`. That includes a real Sentinel the owner opens during the 2-3 minute run, or a checkout's `python …/sentinel/main.py`.
- SIGTERM skips `closeEvent`. The workspace rule is to quit through `terminate`, never `pkill`.
- Fix: kill only the PIDs this run launched (the tree recorded in `cycle()`), or match on `SENTINEL_PORTABLE_ROOT=<tmp>` in the process environment or arguments.

## Checked, no finding
- Panel late billing: the context is captured at `start_worker` and keyed by `request_id`. A no-id request is never bound (M5). Each worker bills at most once. A fenced worker is skipped. Local requests are not billed. A completed request is not billed twice. `add_late_cost` only touches rows already marked `cancelled`, and `record_stopped_request` closes the run as `cancelled` before it adds the cost, so the ordering is correct.
- Reset fence: `_ERASING` is set before the first delete. Late queued signals from workers that were joined but not parked hit `writes_allowed()` (M4).
- WhatsMyName and the OSINT catalogue: the guard sits on the download-write path only, and the sweep still gets its list.
- `main.py` wires `agents.sentry.sentry.baseline.WRITE_GUARD`. Every in-app importer (`ui/workers.py:842`, `ui/panels/sentry.py`) uses that same module path, so the guard reaches the module that actually writes.
- Root `testpaths` now includes `agents/sentry/tests`. The `sys.path.append` used instead of a prepend avoids shadowing `main.py`.

## Verdict
Approve as merged, with one follow-up. There are no P0 or P1 findings. Fix P2-1 before the testing phase: it is a copy claim the code does not support, on the privacy path. The P3 items can be batched.

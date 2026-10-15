# Spec: never force-kill a worker thread (2026-10-14)

Phase: Define. No code changed. Priority **P1** (a quit that can hang or abort blocks the testing phase).
Sources: `docs/qa_audit/chat.md:154` (finding 16), `docs/qa_audit/code_followups.md:18`, `TODO.md:105`, HQ handoff `audits/sentinel/HANDOFF.md:27` ("`terminate()` remains for non-privileged chat/model workers"), spec `audit-tier2-privileged-layer.md` section 13 ("Terminate calls left in place"), radar note `docs/company/radar-notes/2026-10-14-feature-no-thread-terminate.md`.
Playbook: `product-manager.md` is `status: current`, last-reviewed 2026-10-14. Its two practices (EU AI Act Art. 50, CRA) do not apply: nothing here publishes AI content or makes the app commercially available.

## 1. Problem
Stop and Quit still end worker threads with `QThread.terminate()`. Qt calls this "dangerous and discouraged": the thread stops wherever it is, without releasing locks. In Python one of those locks is the GIL. If the killed thread holds it, every Python thread blocks for good, the UI thread included. The window goes blank and ignores Close. The sibling app SONAR shipped this exact freeze (lab `AGENTS.md`, SONAR "won't close", fourth cause, 2026-09-19).

Live call sites (all verified 2026-10-14 on `qa/phase-a-fixes`):

| Site | What it kills | When |
|---|---|---|
| `main.py:4195` `stop_chat_worker` | the chat request | Stop button / Esc, then a **blocking `wait(2000)` on the UI thread** (`:4196`) |
| `main.py:5083` `stop_background_work` | the chat request | Quit and Emergency Reset, then `wait(1000)` |
| `main.py:5102` same | model scan / model pull | Quit, if the thread is still running after a 2 s join |
| `ui/panels/vpn.py:1143` `VpnPanel.shutdown` | diagnostics, IP, DNS and the panel's own worker | Quit, after 2 s each. The connection worker is already detached (S4, `:1147`), so `TODO.md:105` is partly out of date |
| `ui/panels/osint_heavy.py:726` | Bloodhound investigation, file search (SFTP) | Quit, after 2 s each |
| `agents/vpn_agent/services/health_monitor.py:85` `HealthMonitor.stop` | IP/DNS/tunnel check threads | Quit (called from `vpn.py:1127`) and when monitoring is switched off (`vpn.py:1094`) |

**Who it affects:** the owner, as the single operator. The failure looks the same to them whatever the cause: a white window that will not close, a force-quit, and possibly a crash report. That is also the first thing a formal tester would write up.

**Already fixed, to reuse:** `ui/workers.py:35 detach_worker` (S4) drops the result slots and parks the worker in `_DETACHED_WORKERS` until `finished`. The chat path already has `_detach_chat_worker` (`main.py:4168`), and every chat handler ignores a detached sender (`main.py:4049-4158`). So the "late text after the stop notice" part of QA finding 16 may already be fixed. That is not verified, and a test must prove it (section 3, M3).

**Why a cooperative stop is not enough on its own:** `ChatWorker.cancel()` (`ui/workers.py:236`) only sets a flag. The flag is read between tokens. The blocking call `run_backend_func(...)` (`:249`) cannot be interrupted, so a non-streamed Ollama reply or a slow first byte keeps the thread busy until the call returns. Without `terminate()` the thread has to be **parked** while it finishes, not dropped. Destroying a running QThread aborts the process (radar note, row 3).

## 2. Goal and target user
The owner can press Stop or Quit at any moment and always get control back: Stop at once, Quit within a fixed time. No hang, no crash, and no text or charge that pretends the request never happened.

## 3. Success measures (each one is a test or a number)
- **M1, the gate.** An AST test (`tests/test_no_thread_terminate.py`) finds **zero** `.terminate` attribute uses in `main.py`, `ui/`, `services/`, `providers/`, `agents/vpn_agent/` and `agents/sentry/`. That covers calls, `getattr(x, "terminate")` and `hasattr(x, "terminate")`. Ruff TID251 cannot see method calls (radar note), so the test is the gate. Any future `subprocess.Popen.terminate()` needs an explicit allow-list entry, with file, line and reason, in the test. Mutation check: add one `worker.terminate()` and the test must fail.
- **M2, Stop does not block.** With a fake backend that blocks on an `Event` that is never set, `stop_chat_worker()` returns in **under 100 ms** (measured with `time.monotonic` in the test). The send button is enabled again, the worker is in `_DETACHED_WORKERS`, and `terminate` was never called (monkeypatched to raise).
- **M3, nothing after the stop notice.** Set up as in M2, then release the backend so it emits tokens, `usage` and `finished`. Process events. The transcript's last message is still "Chat request stopped by user." and the run-log row stays `cancelled`.
- **M4, Quit is bounded.** A subprocess test, modelled on SONAR's quit-mid-fetch test, does the following. It starts the window offscreen (`QT_QPA_PLATFORM=offscreen`) with a fake backend blocked for good, sends a chat message, and then quits. The process must exit within **5 s** of the quit, with exit code 0 and no `QThread: Destroyed while thread is still running` on stderr. The test runs 20 times with a hard timeout of 30 s. The old code must hang or fail that test: this is the failure-first check.
- **M5, the other sites.** These are unit tests with fake workers whose `wait()` returns False. The model scan/pull, VpnPanel diagnostics, Bloodhound and HealthMonitor threads are each parked, never terminated. For HealthMonitor, the fake thread object is parked rather than dropped.
- **M6.** The full suite stays green under offscreen Qt on macOS and Linux (CI), with no test weakened.

## 4. What the user sees
| | Now | After |
|---|---|---|
| Stop (chat) | UI freezes for up to 2 s, then "Chat request stopped by user." Rarely, the whole app deadlocks | Immediate. The notice reads: "Chat request stopped. The model may still finish in the background. A cloud provider may bill for it; Sentinel will not show the reply." (exact text is Q2) |
| Quit during a chat or model request | Up to about 3 s of joins and then terminate. Rarely a hang (white window) or an abort | Closes within 5 s. Any request still running is abandoned and the process ends with `os._exit` after `aboutToQuit`, the same exit S4 uses for Quit anyway |
| Quit during an administrator action | Wait / Quit anyway (S4) | Unchanged |

No new buttons, dialogs or settings.

## 5. Scope
- Replace each call in section 1 with cancel, then a short join, then park. Use `detach_worker`, extended so it also drops the chat signals (`token`, `status`, `usage`). Alternatively the chat path keeps `_detach_chat_worker` and only borrows the parking.
- Remove the blocking `wait(2000)` from Stop.
- On Quit, if any parked worker is still running after `aboutToQuit`, end the process with `os._exit` (generalise `_finish_quit_anyway`, which syncs QSettings first).
- Emergency Reset: it must not erase `data/` underneath a parked chat worker that might still write. Whether `run_backend` writes anything itself is UNVERIFIED, and Architect decides. Refusing while a request runs, as S4 does for administrator work, is the cheap option.
- Add the AST test. Correct `TODO.md:105`, mark the HANDOFF item when done, and update `docs/qa_audit/chat.md` finding 16.

## 6. Non-goals
- No new UI, no progress or cancel indicator for background requests.
- No interruptible HTTP. Closing a provider's socket from another thread is a bigger change; it goes in SUGGESTIONS if it is wanted at all.
- No change to S4 behaviour for privileged workers (Wait / Quit anyway, `has_privileged_work`), apart from reusing its helpers.
- Not `agents/bug_spray` (it is a submodule with its own suite; a grep found no `.terminate()` there; recheck at Build).
- No change to the cost gate's rules. Q3 below only asks how a late result is recorded.

## 7. Owner decisions (2026-10-14)
- **Q1:** `agents/vpn_agent/` HealthMonitor is in scope. (It is tracked in Sentinel's own repo; there is no nested `.git`.)
- **Q2:** The Stop notice is accepted; the "may bill" clause appears only for cloud providers.
- **Q3:** A stopped cloud request's late usage is recorded as the cost of a cancelled run. Architect confirms it is safe after Stop.
- **Q4:** A 5 s Quit limit is accepted.
- **Q5:** Emergency Reset cancels and parks the chat worker, *fences* it (its late finish writes nothing: no text, no run-log line, no usage, because the data it would write into is being erased), then resets. Refusing is rejected: `run_backend_func` cannot be interrupted, so a hung request would block an emergency action indefinitely. Architect confirms `run_backend` writes nothing to `data/` itself.

## 8. Risks
- Calling `os._exit` with a parked worker skips every Python `finally` and atexit hook. Anything not written through is lost. It is the same trade S4 and SONAR accepted, but Architect must list what is written at exit (QSettings is synced; check the usage log and chat snapshot saves).
- A parked chat worker still holds its `messages` and keeps its network connection open until the provider answers. Ollama can stay busy too, so the next local request may queue behind it. This should be stated in the notice or docs, not hidden.
- Queued signals that are already posted when Stop disconnects are filtered by the sender check, not by the disconnect. Qt's delivery rule for these is UNVERIFIED, which is why M3 is required.
- HealthMonitor's `_Worker` is moved with `moveToThread`, so `quit()` only takes effect after the running check returns. Parking has to keep both the thread and the worker alive.

## 9. Pipeline
Define (this spec, **GATE**). Design: minimal, just the Stop notice text (Q2), and the PM can settle it with the owner without a UX pass. Architect: **GATE**, because the change touches concurrency and process exit (`os._exit` policy, park-list ownership, Emergency Reset, late usage); record the radar constraints. Plan, Build, Review, Verify (M1-M6, mutation-check the AST test and the park helper). Secure: **not required**. The change touches no secrets, auth, untrusted input, privileged code or packaging, and makes no new network call; a request that continues after Stop already exists today. Q3 is a cost-gate question for Review, not a security one. Document (CHANGELOG/versioning row, chat docs and Learning Centre text if the Stop notice changes). Release: **GATE**, with an owner check on a real Mac. Press Stop during a slow local model, then quit during a cloud request. Expect no beachball, no Console crash report, and a reopen with `data/` intact.

## Architecture

**Status: APPROVED by the owner 2026-10-14 (D1-D6 all yes).** Build on branch `fix/no-thread-terminate`. Architect pass of 2026-10-14 on `qa/phase-a-fixes`. Playbook `architect.md` is `status: current`, last-reviewed 2026-10-14. Its two practices apply here. First, the whole hazard analysis assumes the **GIL build** of CPython: a thread killed while it holds the GIL starves every other thread. A free-threaded build would change the failure, but it would not make `terminate()` safe. Second, no `lazy import` of PySide6 or shiboken6 in any module touched here: top-level imports only. The radar note `docs/company/radar-notes/2026-10-14-feature-no-thread-terminate.md` is honoured row by row:
- Parking keeps a reference until `finished` and then calls `deleteLater`. It never drops the reference.
- Nothing deletes a running QThread.
- No `QThread.create()`.
- An AST test is the gate.

Decision record for the process-wide part: `docs/adr/0001-park-workers-and-leave-with-os-exit.md`.

### A1. Approach in one paragraph
Each forced kill becomes three steps: **cancel → join against a deadline → park**. Two places use a deadline: closing the window and Emergency Reset. Stop and HealthMonitor never join. There is **one** parking registry, in `ui/workers.py`. Every quit, normal or not, leaves through **one exit function**. That function syncs QSettings (unless a reset just erased its folder), flushes stdio and calls `os._exit(code)`. So Qt's teardown never meets a running thread, and nobody has to keep a list of every thread the app owns. A stopped chat worker keeps one live connection: it reports a stopped cloud request's cost into a recorder on the UI thread. That recorder never touches the transcript or the run's controls. Emergency Reset *fences* every parked worker before it erases anything. A latch in `services/portable_reset.py` refuses the one write that `run_backend` makes from the worker thread itself.

### A2. Decision 1: one park helper, owned by `ui/workers.py`
**Decision.** Keep the single registry `_DETACHED_WORKERS` in `ui/workers.py:23`. The name and the list type stay, because `ui/dialogs.py:40-46` aliases it and `tests/test_quit_privileged.py:74-80` asserts its identity. Add one primitive and build everything on it:
- `park(thread, *companions)` keeps `thread` and any companion objects in the registry. It connects `thread.finished` to a slot on a module-level `_Parking(QObject)`. That object is created when the module is imported, in the main thread. The connection is `AutoConnection`, so a `finished` emitted from the worker thread is **queued onto the UI thread**, and the existing test that emits `finished` from the main thread (`test_quit_privileged.py:75`) still sees the release at once. The release slot does three things. It calls `thread.wait()`, which returns at once because `finished` has fired. It calls `deleteLater()` on the thread and each companion, guarded by `shiboken6.isValid`, since HealthMonitor already wires its own `deleteLater` (`health_monitor.py:105-106`). Only then does it drop the references.
- `detach_worker(worker)` (`ui/workers.py:35`) becomes "drop `_RESULT_SIGNALS` slots + `park(worker)`". S4's callers are unchanged: `ui/panels/vpn.py:1147`, `ui/panels/vpn_gate.py:95`, `ui/dialogs.py:840` and `:1143`.
- `join_or_park(workers, deadline)` calls `cancel()` on every worker that has one, then `wait(remaining_ms)` on each against a **shared** monotonic deadline, then `detach_worker`s whatever is still running. It replaces the `wait(...) / terminate()` pairs.
- `fence_parked()` sets `_sentinel_fenced = True` on every parked object (Decision 4).

**Why this changes S4's internals but not its behaviour.** Today `detach_worker` releases through a lambda with no context object (`ui/workers.py:55`). Qt calls such a functor *directly, in the emitting thread*. So the last Python reference to a parentless, Python-owned QThread is dropped on the worker thread, inside its own `finished` emission, before the thread has fully returned. That is the "deleting a running QThread" case the radar note forbids. Whether PySide6 6.11 follows Qt's direct-call rule for lambdas is UNVERIFIED. The new parking test records the release thread and settles it. Privileged behaviour does not change: the worker is still cancelled, still joined for S4's 2 s and still parked until it ends. Only the thread that releases it changes.

**Chat keeps `_detach_chat_worker` (`main.py:4168`) and borrows `park`.** It does two things `detach_worker` cannot do:
1. It sets `_sentinel_detached`, which `_from_detached_chat_worker` (`main.py:4160-4166`) needs to filter events queued before Stop.
2. It disconnects only the five chat slots, leaving the new `stopped_usage_signal` connection in place (Decision 3).

`detach_worker` disconnects *all* receivers of `finished_signal`/`error_signal`, which is right for panels and wrong for this. After parking, `self.chat_worker` is set to **None**. "The current request" and "a parked request" are then never confused. Without that, `stop_current_task` (`main.py:4222`), `open_selected_chat` (`:4735`), `new_chat` (`:4787`), Emergency Reset (`dialogs.py:1281`) and the tray status (`main.py:5186-5188`) would all see a running worker after Stop. A second Stop would append a second notice, and the tray would say "Working".

**HealthMonitor** (`agents/vpn_agent/services/health_monitor.py`). Each check runs as a parentless `QThread` (`:100`) plus a `_Worker` QObject moved into it (`:101-102`). Today only the thread is kept (`:108`), and the worker's only Python reference is a local variable. Whether PySide keeps it alive through the `started → worker.run` connection is UNVERIFIED, and holding it removes the question. The decision:
- `_threads` holds `(thread, worker)` pairs.
- `stop()` stops the timer and disconnects `worker.result` from the monitor, so a late result cannot warn into a hidden or destroyed panel. It calls `thread.quit()`, which only takes effect after `run()` returns; `worker.finished → thread.quit` is already wired at `:104`. Then it hands each pair to an **injected** `park` callable and clears the list. **It never waits.**
- `HealthMonitor(..., park=None)`: `VpnPanel` passes `ui.workers.park`. If no `park` is injected (the monitor's own tests, standalone use), the monitor keeps the pairs in a private list until `finished` and never drops them.

Why injection and not an import: nothing in `agents/vpn_agent/` imports `ui/` today (grep: no matches). The service-to-UI direction stays closed, which matches how Tunnel already injects privileged execution. This also removes a UI-thread freeze that has nothing to do with Quit. `VpnPanel.hideEvent` (`ui/panels/vpn.py:1093-1095`) calls `stop()` every time the Tunnel panel is hidden. That costs up to 1.5 s per check thread, followed by an **unbounded** `thread.wait()` after `terminate()` (`health_monitor.py:83-86`). The spec says `:1094` is "when monitoring is switched off"; it is actually `hideEvent`.

**`requestInterruption()`: not used.** Every QThread subclass in `ui/workers.py` already has `cancel()`, except `ModelPullWorker` (`:336`) and `ModelScanWorker` (`:732`). None of them runs a loop that could poll `isInterruptionRequested()`. Their blocking part is a single library call, and HealthMonitor's `_Worker.run` (`health_monitor.py:21-26`) is one function call. A second, Qt-only cancel vocabulary would buy nothing (radar row 2: "watch"). Revisit if a worker with a loop and no `cancel()` appears.

### A3. Decision 2: the quit sequence and the 5 s bound
**Today's joins add up far past 5 s.** All are sequential on the UI thread, so the worst case is their sum:
- Chat: 1 s join (`main.py:5088`).
- Model scan and model pull: 2 s join then 1 s after terminate, each (`main.py:5105-5107`).
- VpnPanel: HealthMonitor 1.5 s per check thread, up to 3 (`health_monitor.py:84`), plus an unbounded wait. Four workers at 2 s each plus 0.5 s after terminate (`ui/panels/vpn.py:1140-1144`). Three tabs at 2 s each (`vpn_gate.py:91`, privileged only). The connection worker, 2 s (`vpn.py:1146`, privileged only).
- Bloodhound: 2 s plus 2 s after terminate, for each of 2 workers (`ui/panels/osint_heavy.py:723-727`).
- Bug Spray: 2 s QProcess (`bug_spray_feed.py:462-463`), 2 s QProcess and 2 s worker (`bug_bounty.py:476-485`). `bug_bounty.py:485` then leaves a still-running worker referenced. It is not on the spec's list, but it is part of this sum.

The non-privileged total is about 25 s, or more than 40 s with the unbounded wait. **Per-site timeouts therefore cannot meet Q4. One shared deadline does.**

**Sequence when the owner quits with no administrator action running.** S4's Wait / Quit anyway path is unchanged and outside this bound: its privileged joins keep their fixed 2 s. That matches spec section 4, row 3.
1. `closeEvent` (`main.py:5143`): the S4 privileged check is unchanged. Then `deadline = monotonic() + QUIT_JOIN_BUDGET_S`, with **2.5 s** as a module constant.
2. `stop_background_work(deadline=deadline)` (`main.py:5078`):
   1. **Chat:** cancel, `_detach_chat_worker` (flag, five disconnects, `park`), `chat_worker = None`. **No wait.** Then `run_logger.cancel(active_run_id)`, a synchronous SQLite commit (`run_logger.py:32-40`).
   2. **Model scan and pull** (`:5100-5107`): `join_or_park([scan, pull], deadline)`.
   3. **Panels:** `shutdown_panels(app, deadline)` (`ui/dialogs.py:54`) passes the deadline through. `VpnPanel.shutdown(timeout_ms=2000, *, deadline=None)` stops the HealthMonitor (no wait) and runs `join_or_park` on its four non-privileged workers against the deadline. Its tabs and connection worker keep S4's `timeout_ms`; with no privileged work running they return at once. Bloodhound and Bug Spray use `join_or_park` against the deadline as well.
   4. Pending requests are abandoned (`main.py:5110-5118`), each a synchronous SQLite commit.
3. `event.accept()`. With no `setQuitOnLastWindowClosed(False)` anywhere (grep), closing the last window quits. `aboutToQuit` stores the geometry (`main.py:5341-5344`) and `app.exec()` returns.
4. **`_leave(window, settings, code)`**, which replaces `_finish_quit_anyway` (`main.py:5209-5226`) and keeps its body. It is called unconditionally after `app.exec()`:
   - `settings.sync()` unless `_portable_reset_committed`;
   - flush stdout and stderr;
   - **`os._exit(code)`**.

   This is where `os._exit` is chosen: **always**, not only when something is parked. The reason is in the ADR. Parts of the app's threads are not parked or even joined today: panel ChatWorkers are only cancelled (`ui/panels/base.py:463-473`), as are Sentry's watch (`ui/panels/sentry.py:472-475`) and Wi-Fi's scan (`ui/panels/wifi.py:532-535`). A conditional exit would need a complete inventory of threads to be safe; an unconditional one does not.

**The bound:** step 2 is at most 2.5 s of joins plus a few SQLite commits measured in milliseconds; steps 3 and 4 take well under a second. That leaves at least 2 s of margin on Q4's 5 s.

**What must be written before `os._exit`.** Verified by reading and grep:
- **QSettings** (window geometry): set in `aboutToQuit` (`main.py:5341-5344`). It needs an explicit `sync()`, which `_leave` already does (`main.py:5219-5223`). It is skipped after a reset, as S4 decided.
- **Usage log:** this is the SQLite `usage` table. It is written and committed synchronously on the UI thread in `handle_chat_finished` (`main.py:4077-4085` → `services/usage_tracker.py:166-180`). `data/usage_log.json` is only read once, for migration (`services/database.py:818-819`). WAL mode (`database.py:115`): a committed transaction survives process death. **Nothing to flush.**
- **Run log:** the `runs` table, committed per call (`services/run_logger.py:17-23`, `:32-40`). The cancel happens in step 2.1, before `accept`. **Nothing to flush.**
- **Chat saves:** `history_store.save_chat` / `update_chat` (`services/history_store.py:13-37`) and `_write_json_atomic` (`main.py:217-225`). All are synchronous, on the UI thread, inside `handle_chat_finished` (`main.py:4108-4128`) or a user action (`:4456`, `:4653`, `:4724`). A grep for timers finds no debounced or deferred saves. **Nothing to flush.** A stopped chat's partial transcript is not saved today and still is not.
- **`data/settings.local.json`:** `settings_store.save_override`, synchronous (`main.py:2987-2990`).
- **stdout/stderr:** flushed by `_leave`. They feed `/tmp/sentinelai_launch.log` (`main.py:3674-3676`).
- **atexit / finalizers:** none registered in `main.py`, `ui/`, `services/`, `providers/`, `agents/vpn_agent/` or `agents/sentry/` (grep for `atexit`, `weakref.finalize`, `__del__`: no matches). Third-party atexit hooks are UNVERIFIED, but none holds Sentinel data.
- **Single-instance socket:** left behind by `os._exit`. The next launch already removes it (`QLocalServer.removeServer`, the "socket left by a crash" comment in the `__main__` block).

### A4. Decision 3: late usage after Stop (Q3)
**Today there is no late usage to record.** `ChatWorker.run` returns *before* emitting `usage_signal` on every path that sees the cancel flag: mid-stream (`ui/workers.py:262-264`), tuple (`:279-283`) and string (`:286-290`). Q3 therefore needs a worker change as well as a recorder.

1. **Worker.** Add `stopped_usage_signal = Signal(dict)` to `ChatWorker`. It is emitted **at most once**, and only when the cancel flag was seen *after* `run_backend_func` returned or while streaming. The payload is `{"usage": <provider counts or None>, "response_text": <full or partial text>}`. It is a new signal, not `usage_signal`, because panel ChatWorkers connect `usage_signal` to `note_usage` (`ui/panels/base.py:453`), and their cancelled cost is out of this spec's scope (risk R6).
2. **Run metadata is captured at send time.** At `main.py:3619-3637` the window sets `worker._sentinel_run = {run_id, agent, tool, backend, model, prompt, project}` and connects `stopped_usage_signal → self._record_stopped_cost`. The recorder must **never** read `pending_*`: the next send overwrites them (`main.py:3593-3602`), so a late report would be billed to the wrong request.
3. **Where it lands and on which thread.** `_record_stopped_cost` is a `GodAI` method. The receiver lives on the main thread and the emitter is the worker thread, so `AutoConnection` queues it: **the write happens on the UI thread**. That is the same single writer as every other usage write. The recorder:
   - returns at once if the sender is fenced (Decision 4), its backend is not in `CLOUD_PROVIDERS` (`services/provider_catalog.py:9`), or it has already recorded;
   - calls `usage_tracker.log_request(...)` with `cost_type_override` set to `"cancelled-exact"`, or `"cancelled-estimated"` when there are no counts. `normalize_usage` already honours an override and estimates from the text (`usage_tracker.py:47-61`);
   - calls a new `RunLogger.add_late_cost(run_id, input_tokens, output_tokens, cost_eur)`: `UPDATE runs SET input_tokens, output_tokens, cost_eur WHERE run_id=? AND status='cancelled'`. It is not `finish()`, which would reset `duration_sec` to 0 (`run_logger.py:30-31`);
   - adds the cost to `session_cost_total` and calls `update_usage_labels()`. These are session-level figures that the session budget reads, not the run's UI.

   It does **not** touch `current_messages`, the transcript, the timer, the Send/Stop buttons or the chat file. So the run row stays `cancelled` (M3) and nothing is revived.
4. **Safety.** The record is idempotent per worker. It is keyed to a run that is already closed, and it is written by the same thread and code path as a normal completion. Local Ollama requests record nothing, so `tests/test_chat_lifecycle.py:173-176` ("a straggler bills nothing", Ollama) stays green unchanged.
5. **On Quit a late report is lost.** Queued events are not delivered after `app.exec()` returns. A cloud request abandoned by Quit can therefore be billed without a record. The cost is unknowable at that point; document it (R3).

**Behaviour change for the owner** (D3): with this design, a Stop *mid-stream* on a cloud provider also records an estimated cost from the text received. Today it records nothing (`:262-264`). That follows Q3's intent, but it is new.

### A5. Decision 4: the Emergency Reset fence (Q5)
**Does `run_backend` write anything itself? FALSE for image models, VERIFIED. TRUE (nothing written) for text, VERIFIED by grep.**
- `run_backend` (`main.py:3949`) routes an OpenAI image model to `generate_image` (`main.py:3970-3972`). On the worker thread, that function writes the image to `user_data_base()/output/images` (`services/openai_client.py:35-37`, `:137-143`). In portable mode that path is inside the folder Reset erases (`services/portable_reset.py:28-41`). **The spec's assumption in Q5 does not hold for this path.**
- Text paths: a grep of all seven `services/*_client.py` for `open(`, `write_text`, `write_bytes`, `json.dump`, `mkdir`, `sqlite`, `log_request` and `run_logger` matches only `openai_client.py:139`/`:143`. `assess_local_model` (`main.py:722-766`) only reads (an Ollama HTTP size query and psutil). Provider SDK internals (openai, anthropic, google-genai) writing into the data folder: UNVERIFIED, not expected.
- Other workers that move from *terminated* to *parked* under this spec also write from their own thread: `ModelScanWorker` → `benchmarks.refresh` / `note_attempt` write `data/lmarena_ratings.json` (`services/benchmarks.py:379`, `:399`; path `main.py:111`). Writes from other panel workers (for example Sentry's `run_watch` persist) are UNVERIFIED. Those workers are not terminated today either, so they carry no new risk from this change.

**Design.**
1. `emergency_reset` (`ui/dialogs.py:1252`) calls `stop_background_work(deadline=…, fence=True)`. That calls `ui.workers.fence_parked()`, which marks **every** parked worker, not just the current chat worker. A worker stopped a minute earlier may still be parked and would otherwise write into the erased database.
2. **Text, run log and usage** are written only on the UI thread, by handlers that already drop detached senders (`main.py:4050-4158`) or by the recorder (A4), which returns for a fenced sender.
3. **Worker-thread writes:** `erase_portable_user_data` sets a module-level latch (`threading.Event`) *before* it deletes anything. `openai_client.generate_image` checks `portable_reset.writes_allowed()` right before `mkdir`/`write_bytes`, and so do `benchmarks.refresh` and `note_attempt` before their temp write. If it is false they raise, and the parked worker's error goes nowhere because it was detached. One residual window remains: a write that has passed the check and is mid-`write_bytes` while `rmtree` runs. It is microseconds, and accepted (R4).
4. **Then the reset quits.** `_leave` skips the sync and calls `os._exit`. Today that same path would end in Qt's teardown with a running worker and abort.

### A6. Decision 5: the AST gate (`tests/test_no_thread_terminate.py`)
**Scope.** A filesystem walk over `main.py`, `ui/**/*.py`, `services/**/*.py`, `providers/**/*.py` and `agents/**/*.py`, excluding `agents/bug_spray/**` (the submodule, `.gitmodules`) and `__pycache__`. Not `git ls-files`: `tests/conftest.py`'s `_deny_network_and_processes` forbids starting git. The walk is a superset of the tracked files: all of `agents/sentry` and `agents/vpn_agent` is tracked here, with no nested `.git`. Untracked strays are therefore checked too, which errs on the strict side.

**What it flags:**
1. any `ast.Attribute` whose `attr == "terminate"`, whether called or merely referenced (`f = t.terminate`);
2. any call to `getattr` / `hasattr` / `setattr` / `delattr` / `operator.methodcaller` / `QMetaObject.invokeMethod` that has the string constant `"terminate"` as an argument.

It does not flag a `def terminate`, a dict key or a URL. Today that means `providers/exposure_lookup.py:428` (`"terminate": []`) and `:483` (an f-string URL) are correctly ignored. Comments and docstrings are invisible to the AST.

**Findings today** (`git grep`, 2026-10-14): `main.py:4195`, `:5087`, `:5106`; `ui/panels/vpn.py:1140` (hasattr), `:1143`; `ui/panels/osint_heavy.py:723` (hasattr), `:726`; `agents/vpn_agent/services/health_monitor.py:85`. There are **no** `Popen.terminate()` or other process `.terminate()` uses: QProcess is ended with `kill()` (`bug_bounty.py:481`, `bug_spray_feed.py:462`). **The allow-list is empty today.** The mechanism exists for future process handles. Each entry is `(path, enclosing function qualname, reason)`. The spec asks for file:line, but a line key breaks on every unrelated edit, so this is a deliberate deviation for the owner to confirm. The test fails if an entry matches nothing (stale), or matches more uses than it declares.

**Making it mutation-checkable:**
1. The scanner is a pure function, `find_terminate_uses(source, path)`, with positive fixtures (`w.terminate()`, `self.chat_worker.terminate()`, `getattr(w, "terminate")()`, `hasattr(w, "terminate")`, `f = w.terminate`) and negative ones (dict key, URL, `def terminate`, a comment).
2. A mutation test adds `worker.terminate()` to the source text of the real `main.py` in memory and asserts the scanner finds exactly one more use.
3. A vacuity guard asserts that more than 100 files were scanned and that `main.py` and `health_monitor.py` were among them, so a broken glob cannot pass on an empty scope.
4. During the build the gate lands **early**, with the current sites in the allow-list marked `"removed by task N"`. Each later task deletes its entry, and the stale-entry rule forces that. The final task asserts that no allow-list entry names a thread.

### A7. Decision 6: the M4 subprocess quit test
**Problem.** The `__main__` block (`main.py:5269-5346`) cannot be run in a test as it is:
- `_hand_off_to_running_instance` connects to the owner's *running* Sentinel. It would raise that window and exit 0, which is a false pass.
- `tighten_env_files`, `migrate_tracked_defaults` and `tunnel_logs.purge_legacy_logs` touch real files.
- `appkit_guard` and the tray are irrelevant offscreen.

**Design.**
1. Extract from the `__main__` block, keeping its behaviour, `_run_event_loop(app, window, settings) -> NoReturn`. It holds the `aboutToQuit` hookup, `app.exec()` and `_leave(...)`. Both `__main__` and the harness call it, so the test exercises the real exit path.
2. A harness, `tests/helpers/quit_harness.py`, run by `subprocess.run([sys.executable, harness, variant])` under `@pytest.mark.allow_process`. That marker is already registered in `pytest.ini`.
3. **Isolation.** The child gets a **constructed** environment, not an inherited one: `PATH`, `HOME=<tmp>/home`, `QT_QPA_PLATFORM=offscreen`, `PYTHONPATH=<repo>`, `SENTINEL_PORTABLE_ROOT=<tmp>/root`, and no provider keys.
   - The portable root sends `user_data_base()` (`services/runtime_paths.py:129-132`, env var `:13`, `:45-53`) to `<tmp>/root/Sentinel Data/`. That covers the database (`database.py:13-14`), `.env` (`main.py:32`), chats, `config/` and `data/`.
   - The harness sets `QSettings` to `IniFormat` under that folder, as the portable branch of `__main__` does. Native-format QSettings on macOS may ignore `$HOME` (UNVERIFIED), so it is not trusted.
   - It applies the same offline patches as `tests/conftest.py:27-70` and the same deny-network guard as `:162-213`. Both move into a shared `tests/_isolation.py` that conftest also calls: a test-only refactor with unchanged behaviour.
   - It stubs `GodAI.start_model_scan`, which otherwise fires after 4 s (`main.py:407`), plus `assess_local_model` and `osint_catalog.refresh_in_background`.
   - It never calls the single-instance or tray code.
4. **Flow, all on QTimers.** Build `GodAI`. Select chat, Local only and ollama, as `tests/test_chat_lifecycle.py:111-116` does. Replace `window.run_backend` with the fake. Call `send_prompt()`. Poll every 10 ms until the fake signals that it has entered and `chat_worker.isRunning()`. Print `QUIT <monotonic>` and flush. Call `window.close()`, the tray's Quit path (`main.py:5338`).
5. **Fake backends**, as a parameter:
   - `spin`: a pure-Python busy loop that never returns. It holds the GIL, which is the SONAR failure.
   - `blocked`: `threading.Event().wait()`, blocked in C without the GIL.
6. **Assertions.** Exit code 0. Exit within 5 s of the `QUIT` line, measured by the parent from reading that line to process end. Hard `timeout=30`; on expiry, kill and fail as "hung". Stderr contains neither `QThread: Destroyed while thread is still running` nor `qFatal`.
7. **Runs.** 20 iterations of `spin` as the spec asks, plus 1 of `blocked`. The total runtime is UNVERIFIED, because constructing `GodAI` takes an unmeasured time. If it exceeds about 60 s, the owner chooses between a `slow` marker and fewer default runs (D5).
8. **Failure first.** The harness lands *before* the fix as `xfail(strict=True)`, and its result on the old code is recorded. Strict means the fixing commit must remove the marker. **It is UNVERIFIED that the old code fails it.** The GIL hang only reproduces if PySide6's `QThread.wait()` releases the GIL while the main thread waits, so that the spinning worker holds the GIL when it is killed. If neither variant fails on the old code, M4 cannot be shown failure-first. The failure-first evidence then rests on M1 (AST) and M2 (`terminate` monkeypatched to raise), and the owner is told.

**In-process tests that start real threads** (M2, M3, parking) must release their fake backend and `wait()` the thread in fixture teardown. A parked, still-running QThread at interpreter exit is the very abort this spec removes, and it would make the suite flaky. That is SONAR's 2026-09-19 lesson: find what the thread is blocked on, do not raise the wait.

### A8. Migration of existing state
No persisted state changes. There is no schema change: `RunLogger.add_late_cost` is an `UPDATE` on existing columns, and `cost_type` is already free text, shown verbatim in Cost History (`ui/dialogs.py:177`). New `cost_type` values (`cancelled-exact`, `cancelled-estimated`) appear only in new rows. QSettings keys are unchanged. Nothing touches `data/sentinel.db` beyond normal writes.

### A9. Build tasks, in order (each one a small commit, with its test)
1. **Parking primitive.** `ui/workers.py`: `_Parking`, `park`, `detach_worker` rebuilt on it, `join_or_park`, `fence_parked`. Test `tests/test_thread_parking.py`: release runs on the main thread after `finished` from a real thread; the thread `isRunning()` is False at release; companions are kept until then; a thread that has not finished is never released. Mutation: release on the emitting thread, or drop the companion → fails. `test_quit_privileged.py` stays green unchanged.
2. **AST gate.** `tests/test_no_thread_terminate.py` with the scanner, fixtures, mutation and vacuity tests. The allow-list holds today's eight sites, each marked with the task that removes it.
3. **M4 harness, failure first.** `tests/_isolation.py` (a conftest refactor; the suite is unchanged), the `_run_event_loop` extraction, `tests/helpers/quit_harness.py`, and `tests/test_quit_bounded.py` as `xfail(strict=True)`. Record the old code's result.
4. **HealthMonitor.** Pairs, a non-blocking `stop()`, an injected `park`, and VpnPanel injecting it. Tests (M5): a fake thread whose `wait` returns False is parked with its worker and never terminated; `stop()` returns in under 50 ms; a late `result` after `stop()` emits no warning. Remove the allow-list entry for `health_monitor.py:85`.
5. **Panels.** VpnPanel's non-privileged workers, Bloodhound and Bug Spray's worker move to `join_or_park` and accept a `deadline`. Tests (M5): stuck fakes are parked, `terminate` raises if called, and S4's privileged tests are unchanged. Remove the `vpn.py` and `osint_heavy.py` entries.
6. **Window shutdown budget.** `QUIT_JOIN_BUDGET_S`; `stop_background_work(deadline, fence)` with model scan and pull via `join_or_park`; `shutdown_panels(app, deadline)`. Tests: fakes whose `wait(ms)` sleeps `ms` → `closeEvent` returns in at most budget + 0.3 s; existing `test_audit_must_fix.py:182` stays green. Remove the `main.py:5087` and `:5106` entries.
7. **Stop.** `stop_chat_worker` cancels, detaches, parks and sets `chat_worker = None` with no wait. The notice follows Q2 (the cloud clause from `_sentinel_run["backend"]`). Tests: M2 (real `ChatWorker` blocked on an Event, under 100 ms, `terminate` monkeypatched to raise) and M3 (release, process events, the transcript's last line is the notice, the run row stays `cancelled`). Remove the `main.py:4195` entry. The allow-list is now empty and the final assertion is switched on.
8. **Late cost (Q3).** `ChatWorker.stopped_usage_signal`, `_sentinel_run`, `_record_stopped_cost`, `RunLogger.add_late_cost`. Tests:
   - a stopped cloud request records exactly one `cancelled-*` usage row and the run row keeps `cancelled` with the cost;
   - Ollama records nothing;
   - the write runs on the main thread (a spy checks `threading.current_thread()`);
   - a second request sent before the late report does not take on its cost.

   Mutation: drop the cloud filter → the Ollama test fails.
9. **Reset fence (Q5).** `fence=True`, `fence_parked`, the `portable_reset` latch, and checks in `generate_image` and `benchmarks`. Tests: a fenced worker's late report writes nothing; `generate_image` after the latch raises and creates no folder; the existing `test_audit_must_fix.py` reset test stays green.
10. **Exit policy (ADR 0001).** `_leave` runs unconditionally. Update `tests/test_quit_anyway_exit.py:33-37`: today it asserts that a normal quit neither syncs nor exits, and after this a normal quit syncs and exits. This is a deliberate change of specified behaviour, not a weakened test, and it needs the owner's yes at this gate. Remove M4's `xfail`; M4 must pass 20 of 20.
11. **Docs** (Document phase): `TODO.md:105`, `docs/qa_audit/chat.md` finding 16, the HANDOFF item, a versioning/CHANGELOG row, a README or Learning Centre line saying that Ollama may stay busy after Stop and that a cloud request abandoned by Quit can bill without a record.

### A10. Risks
- **R1. Two requests at once on one client.** After Stop, a parked request and the next one can run concurrently through the same `self.openai` / `self.ollama` / … objects (`main.py:3949-4020`). Today `terminate()` mostly prevented that overlap. The openai, anthropic and httpx clients and `requests` are documented as thread-safe for this use; **google-genai is UNVERIFIED.** Ollama serialises requests, so the next local reply can queue behind the parked one (spec section 8).
- **R2. `os._exit` skips every `finally` and atexit hook.** Any future code that relies on a destructor, `finally` or atexit to persist state at quit will silently lose it. The ADR makes write-through a rule. Whatever must happen at quit goes into `_leave` before `os._exit`.
- **R3. A late cloud cost is lost on Quit** (A4.5). This is documented, not fixable without waiting for the provider.
- **R4. Reset latch window:** a write already past the check while `rmtree` runs. Microseconds; accepted.
- **R5. HealthMonitor's `deleteLater` and the registry's release** both act on the same objects. Guarded by `shiboken6.isValid`, and task 4's test runs the real `_spawn` wiring.
- **R6. Panels' cancelled cloud requests** still record no cost (`ui/panels/base.py:453` and `note_request_usage`, `main.py:3728-3733`, which drops usage once a request is abandoned). The same Q3 gap outside Chat; a follow-up for SUGGESTIONS.
- **R7. Qt's delivery of events queued before a disconnect** stays UNVERIFIED, as the spec says. M3 is the proof, and the sender flag is the guard either way.

### A11. Still UNVERIFIED (to settle at Build)
1. Whether PySide6 6.11 releases the GIL in `QThread.wait()`, and so whether M4 fails on the old code (A7.8).
2. Whether a PySide6 lambda with no context object runs in the emitting thread (A2; task 1's test settles it).
3. Whether `HealthMonitor`'s `_Worker` survives today without a Python reference (A2; made moot by holding the pair).
4. Thread-safety of the google-genai client (R1).
5. Whether native-format QSettings honours `$HOME` (A7; avoided by using IniFormat).
6. M4's runtime at 20 iterations (D5).
7. In-thread writes by other panel workers into the data folder after Reset (A5, beyond chat, model scan and image generation).

### A12. Decisions for the owner at this gate
- **D1.** Exit policy: always `os._exit` after `app.exec()` (ADR 0001), instead of the spec's "only if a parked worker is still running". This changes the expected result of `tests/test_quit_anyway_exit.py:33-37`.
- **D2.** The 5 s bound covers a quit with no administrator action running. Quit anyway keeps S4's fixed 2 s privileged joins and is outside the bound, which matches spec section 4, row 3.
- **D3.** A Stop mid-stream on a cloud provider now records an *estimated* cost, `cancelled-estimated`, where today it records nothing.
- **D4.** Q5 rests on a false premise for OpenAI image models. Accept the `portable_reset` latch (it also covers the model scan's ratings cache), or restrict the fix to text.
- **D5.** The AST allow-list is keyed by file + function + reason, not by line. M4 runs 20 times, or uses a `slow` marker if it exceeds about 60 s.
- **D6.** Bug Spray's `bug_bounty.py:476-485` join joins the shared deadline, although it is not in the spec's table. Without that, the 5 s bound is not met.

### Build log (2026-10-14)
Engineer, branch `fix/no-thread-terminate`. Playbook `engineer.md` is `status: current`, last-reviewed 2026-10-14. `docs/company/app-context.md` is **not on disk** (untracked since c4ac6a3 and meant to stay local); read from git history (`c4ac6a3^`). Stopped after task 7 on finding R7; resumed after the owner's decisions below and completed tasks 8 to 10 (see "Build log, resumed"). A9.11 is the Document phase's.

| Task | Commit | Tests added | Mutants (planted / killed), file:line at the time |
|---|---|---|---|
| 1 Parking primitive | `ded95c6` | `tests/test_thread_parking.py` (11) | 7 / 7: `ui/workers.py:93` DirectConnection (release on the emitting thread), `:96` drop companions, `:56` release a running thread, `:97` no release when already finished, `:147` per-worker timeout instead of shared, `:148` never park, `:160` fence threads only |
| 2 AST gate | `bad9109` | `tests/test_no_thread_terminate.py` (18) | 6 / 6: `tests/test_no_thread_terminate.py:56` attribute check, `:27` reflective calls, `:24` empty scope (vacuity), `:36` stale count, `:25` submodule exclusion; `ui/workers.py:120` planted `worker.terminate()` (the M1 mutation) |
| 3 M4 harness, failure first | `f527174` | `tests/_isolation.py` (conftest refactor), `tests/helpers/quit_harness.py`, `tests/test_quit_bounded.py` (`spin` x20 as strict xfail, `blocked` x1) | Failure-first on the old code, below |
| 4 HealthMonitor | `fa5729e` | `tests/test_health_monitor_stop.py` (5) | 5 / 5: `agents/vpn_agent/services/health_monitor.py:100` wait in stop, `:94` no result disconnect (killed by the late-warning test on its own too), `:102` park without the worker, `:130` `_spawn` drops the worker, `:104` default path keeps nothing |
| 5 Panels | `17890cb` | `tests/test_panel_shutdown_parks.py` (4) | 6 / 6: `ui/panels/vpn.py:1128` ignore the deadline, `:1140` no join_or_park; `ui/panels/osint_heavy.py:725` no join_or_park; `ui/panels/bug_bounty.py:482` feed wait outside the budget, `:488` cancel only; `ui/workers.py:130` `shutdown_deadline` ignores the deadline |
| 6 Window budget | `6f55c87`, follow-up `985df7f` | `tests/test_quit_budget.py` (5) | 6 / 6: `main.py:5175` budget 4.0, `:5115` `shutdown_panels` without deadline, `:5113` scan/pull own timeout, `:5116` fence dropped, `:5101` chat join 2000 ms (killed after adding the explicit-deadline test); `ui/dialogs.py:80` deadline never passed |
| 7 Stop | `fad6c68` | `tests/test_chat_stop.py` (7, one strict xfail) | 7 planted / 6 killed: `main.py:4205` wait(2000) back in Stop, `:4205` chat_worker kept, `:4204` no detach, `:5180` cloud clause inverted, `:4192` no park, `:4206` terminate() back; `tests/test_no_thread_terminate.py:30` a thread entry in the allow-list. **Survived:** `main.py:4177` (`_sentinel_detached` not set). It is only "killed" by the R7 test, which fails anyway; see the finding |

**M4 result.** Old code (task 3, before any fix): `spin` **hung** (killed at the 30 s hard timeout) in 3 of 6 recorded runs (1 of 3 by hand; the first run of each of two pytest runs); `blocked` passed 4 of 4, exiting 0.49-0.52 s after the quit. Each child run takes about 4.3 s, so 20 `spin` runs plus 1 `blocked` take about 90 s when all pass (D5: over 60 s; no `slow` marker added, owner's call). On the task-7 state both variants **abort** (exit -6, `QThread: Destroyed while thread is still running`) 2.5-3.0 s after the quit: parking without task 10's unconditional `os._exit` hands a running thread to Qt's teardown. `test_quit_bounded[blocked-1]` therefore fails on this branch until task 10.

**Full suite at the task-7 state** (`.venv/bin/python -m pytest -q`, before `985df7f`): 2 failed, 2573 passed, 2 skipped, 2 xfailed in 271.5 s. Failures: `test_quit_bounded[blocked-1]` (expected until task 10) and `test_vpn_worker_shutdown.py::test_close_joins_the_model_scan_worker` (its `shutdown_panels` fake took only `app`; fixed in `985df7f` by giving the fake the new `deadline` parameter, asserted order unchanged).

**A11 answers.**
1. M4 fails on the old code: **yes**, through the GIL variant (`spin` hangs). So PySide6 6.11.2's `QThread.wait()` lets the spinning worker hold the GIL when `terminate()` lands. `blocked` does not fail on the old code.
2. A lambda with no context object connected to a QThread's `finished`: **not** run in the emitting thread in PySide6 6.11.2; it is delivered on the main thread (`tests/test_thread_parking.py`, characterisation test). The release still goes through a main-thread `QObject` slot, which Qt itself guarantees.
3. HealthMonitor's `_Worker` **does not survive** without a Python reference: on the old code 0 of 20 spawned checks delivered a result (the worker was collected at once, with or without `gc.collect()`), and each thread idled in its event loop until `stop()`. With the pair held, 20 of 20. So the Tunnel health checks have never run in the shipped app; after task 4 they do (DNS and tunnel checks every 30 s while the panel is visible). Regression test `test_a_spawned_check_delivers_its_result`.
4. google-genai thread safety: not settled (no network in Build).
5. Native QSettings and `$HOME`: avoided; the harness uses IniFormat under the scratch folder. Not measured.
6. M4 runtime: about 4.3 s per child run; about 90 s for 20 + 1 when all pass; a hang costs 30 s and ends the loop.
7. In-thread writes by other panel workers after Reset: not settled (task 9 not reached).

**Finding R7 (stops the build).** PySide6 6.11.2 delivers a call that was queued *before* `disconnect()`, and inside that delivery `sender()` returns **None** (measured with a minimal QThread/QObject probe: connected, `sender()` is the worker; disconnected before processing, the call still arrives with `sender()` None). `_from_detached_chat_worker` reads the flag from `sender()`, so it cannot filter exactly the events it was written for. `tests/test_chat_stop.py::test_m3_tokens_queued_before_stop_are_dropped` shows a token queued before Stop being rendered after the notice (strict xfail). This is the pre-existing QA finding 16, not fixed. It contradicts A2 (point 1: the flag filters events queued before Stop; point 2: disconnect the five slots) and A5.2 (Reset relies on the same handlers dropping detached senders; a queued `finished` could write into the database being erased). Probe: not disconnecting the five chat slots, keeping the flag as the only guard, makes the R7 test and `test_chat_stop.py`, `test_chat_lifecycle.py` and `test_audit_must_fix.py` pass (38 passed). Not applied: it changes an approved decision.

**Deviations from the architecture.**
- `closeEvent` does not compute the deadline; `stop_background_work(deadline=None)` starts the 2.5 s budget itself. `test_quit_privileged.py` and `test_quit_anyway_exit.py` replace `stop_background_work` with zero-argument lambdas, and they must stay unchanged.
- Quit joins the chat worker out of the shared budget after parking it (A3 2.1 says "no wait"). Without it `test_chat_lifecycle.py::test_closing_the_window_cancels_the_run_and_the_worker` fails, and the join also ends a streamed reply cleanly. The bound is unchanged.
- `_detach_chat_worker` parks only a real `QThread`; the Chat tests' `FakeChatWorker` is a `QObject`.
- The AST allow-list value holds a use count, `(count, reason)`, so "more uses than declared" can be checked.
- Bug Spray's two `QProcess` waits come out of the deadline too, as `min(timeout_ms, remaining)` (D6).

### Owner decisions after task 7 (2026-10-14)
- **R7:** accepted. `_detach_chat_worker` keeps the chat slots connected and filters late deliveries by the `_sentinel_detached` flag (with `sender()` intact); A2's "drop result slots" does not apply to the chat worker. A5.2 builds on the same flag.
- **`985df7f`** (the `deadline=None` fake in `test_vpn_worker_shutdown.py`): accepted as an interface adaptation, not a weakened test.
- **D5 runtime:** M4 runs one iteration of each variant in the default suite; the 20-iteration run carries a `slow` marker and is run at Verify and Release.

### Build log, resumed (2026-10-14)
After the owner's decisions above. `docs/company/app-context.md` is back on disk (gitignored, not staged).

| Step | Commit | Tests added or changed | Mutants (planted / killed), file:line at the time |
|---|---|---|---|
| R7 fix | `e18001a` | `tests/test_chat_stop.py`: the queued-before-Stop test is no longer xfail and passes (stable 3 of 3 reruns) | 2 / 2: `main.py:4183` flag not set (the mutant that survived at task 7, now killed), `main.py:4167` `_from_detached_chat_worker` always False |
| 8 Late cost | `5456994` | `tests/test_chat_late_cost.py` (7) | 7 / 7: `main.py:4183` cloud filter dropped (Ollama test fails), `:4199` cost billed to `active_run_id` (second-request test fails), `:4191` always `cancelled-exact`, `:4202` session total not raised; `ui/workers.py:363` report-once guard, `:420` tuple path never reports; `services/run_logger.py:55` status filter dropped |
| 9 Reset fence | `f5640ca` | `tests/test_reset_fence.py` (5) | 6 / 6: `main.py:4183` recorder ignores the fence; `services/openai_client.py:142` no check before mkdir; `services/portable_reset.py:52` latch never set, `:39` latch set before the safety checks, `:28` `require_writes_allowed` never raises; `ui/dialogs.py:1300` reset without `fence=True` |
| 10 Exit policy | `d960f44` | `tests/test_quit_anyway_exit.py` (D1 change at the old :33-37; the other cases call `_leave` by name); `tests/test_quit_bounded.py` xfail removed, 1 run per variant by default, 20-run `spin` under `slow`; `pytest.ini` registers `slow` and deselects it by default | 5 / 5: `main.py:5290` `_leave` conditional again (M4 aborts), `:5311` `sys.exit` instead of `_leave` (M4 aborts), `:5297` exit code 1, `:5291` no sync; `main.py:5147` `terminate()` planted in the quit path (AST gate) |

Totals over the whole build: 64 mutants planted, 63 killed. The one survivor (task 7, the detach flag) was planted again after the R7 fix and is now killed (counted in the 64).

**Task 8 note.** `send_prompt` connects `stopped_usage_signal` only when the worker has it: the Chat tests' `FakeChatWorker` (a `QObject` stand-in, unchanged) has none. The first commit attempt without that guard broke three `test_chat_lifecycle.py` tests and was amended before going further.

**Task 9 deviation (deferred, owner to decide).** The latch check in `services/benchmarks.py` (`refresh`, `note_attempt`) is **not** applied. That module is copied into Imprint, and `imprint/tests/test_benchmarks_drift.py` fails on any difference other than its listed local ones. Lab `AGENTS.md` says a change must land in both copies in the same session, and this build was scoped to Sentinel. The options:
- (a) A generic, app-agnostic write-guard hook in both copies; Imprint never sets it.
- (b) A check in `ModelScanWorker` before `refresh` / `note_attempt`. This leaves a window as long as the fetch, not microseconds.
- (c) Accept the risk. The model scan is not terminated today either (A5: no new risk), and it only writes `data/lmarena_ratings.json`.

**Task 10 deviations.**
- `tests/test_quit_anyway_exit.py`: besides the sanctioned expectation, the two other tests in the file now call `main._leave` instead of `main._finish_quit_anyway`, a rename with unchanged expectations.
- `_leave` exits with the event loop's return code (0 on a normal quit), as `_finish_quit_anyway` exited 0.
- The `slow` marker is deselected through `addopts = -m "not slow"`. `pytest -m slow` overrides it, and CI's command line does not pass `-m`, so CI also skips the slow run.

**M4 on the final code.** Default suite: `spin` and `blocked` pass. Measured once by hand, each exits with code 0: `blocked` 2.53 s after the quit, `spin` 2.75 s; most of that is the 2.5 s join budget spent on the parked chat request. Slow run (`pytest -m slow tests/test_quit_bounded.py`): **20 of 20 passed in 160.2 s**, about 8 s per child run.

**Full suite on the final code** (`.venv/bin/python -m pytest -q -p no:cacheprovider`): **2589 passed, 2 skipped, 1 deselected (the slow M4) in 309.1 s**, no failures. The TestDnsLeak flake did not occur. Not run: `agents/bug_spray/tests` and `agents/sentry/tests` (neither touched), CI on Linux.

**A11, final.**
- #1 yes.
- #2 no: queued on the main thread in 6.11.2.
- #3 the worker did not survive; the checks never ran before task 4.
- #4 google-genai thread safety: not verified.
- #5 avoided, not measured.
- #6 about 2.6 s per child run in the default suite (2 runs), 160 s for the 20-run slow check.
- #7 by grep, not by running: other worker-thread writes into the data folder exist and are not latched. Sentry's baseline and findings (`agents/sentry/sentry/baseline.py:136`, `:166`, from `SentryWatchWorker` with persist), the WhatsMyName cache (`providers/whatsmyname.py:90`), the OSINT catalogue cache (`services/osint_catalog.py:153`, background refresh) and the model watch state (`services/model_watch.py:437`, written on the UI thread). None is terminated today, so this is no new risk (A5), but each could recreate a file during a reset.

**For Review.**
- Since task 4 the Tunnel health checks really run: DNS and tunnel state every 30 s while the panel is visible. Before, they silently never did.
- D3 is live: a cloud Stop mid-stream records a `cancelled-estimated` row.


### Build log, review fixes (2026-10-14)
Fixes for the code review (`software_company/audits/sentinel/no-thread-terminate-review.md`: P0 0, P1 0, P2 3, P3 5). `d1c32b2` (owner option a, benchmarks `WRITE_GUARD` mirrored in Imprint) was already on the branch from another session. P3-5 is left for the Document phase.

| Finding | Commit | Tests | Mutants (planted / killed), file:line at the time |
|---|---|---|---|
| P2-1 stream stopped after its last element | `54c786a` | `test_chat_late_cost.py::test_a_stream_stopped_after_its_last_element_is_still_reported` | 3 / 3: `ui/workers.py:410` end-of-loop check removed, `:413` usage dropped from the report, `:413` no return (finished emitted) |
| P2-2 Reset during a scan that finished inside the join | `789ee39` | `test_reset_fence.py`: Reset during the scan with the post-erase dialog's nested loop (the queued result does arrive, R7, and writes nothing); fence disconnects the scan's results; no write once erasing starts (history save, history update, settings, model watch) | 5 / 5: `services/model_watch.py:436`, `services/settings_store.py:55`, `services/history_store.py:34`, `:68` gate removed; `main.py:5168` scan results not dropped |
| P2-3 `muse_pull_worker` | `9b7dadc` | three tests and one fixture teardown now set `muse_pull_worker` (they set the dead `model_pull_worker`); new `test_the_pull_the_window_starts_is_the_one_quit_joins`, `test_the_fence_disconnects_the_muse_pull_results` | 2 / 2: `main.py:5160` dead name back, `:5168` pull results not dropped (survived the first test set; killed after adding the pull-disconnect test) |
| P3-1 AST gate bypasses | `fd1e29c` | `test_no_thread_terminate.py`: one positive fixture for each of the five bypasses plus five relatives (keyword argument, `__getattr__`, `vars()[...]`, `vars().get`, constant f-string); negatives for `getattr(w, 'kill')`, another `__dict__` key and a run-time name | 7 / 7: `tests/test_no_thread_terminate.py:29` dunder lookups, `:28` attrgetter, `:39` bytes, `:42` `+` folding, `:56` `vars()`, `:86` keyword args, `:94` subscripts |
| P3-2 failed erase leaves the latch set | `b68169f` | `test_reset_fence.py`: a failed erase clears the latch and raises `PartialEraseError`; the refusal dialog says data may already be erased | 3 / 3: `services/portable_reset.py:68` latch not cleared, `:65` no exception caught, `ui/dialogs.py:1309` no partial-erase sentence |
| P3-3 `WRITE_GUARD` set lazily in the worker | `4560604` | `test_benchmarks.py::test_sentinel_installs_the_write_guard_at_startup_not_in_the_worker` | 2 / 2: `main.py:93` startup assignment removed, `ui/workers.py:913` lazy assignment back |
| P3-4 dead `_quit_anyway` | `19541a9` | `test_quit_privileged.py`: the assertion that the flag is set becomes an assertion that no such marker exists (the exit is covered by `test_quit_anyway_exit.py`) | 1 / 1: `main.py:5217` flag set again |

Totals for the review fixes: 23 mutants planted, 23 killed. One was killed only after a test was added: the pull-results mutant in P2-3.

**Test-side changes to note for Verify.**
- `tests/conftest.py` gains an autouse fixture that clears the reset latch after each test. `test_portable_reset.py` runs the real erase, and since P2-2 a latch left set refuses every later data write in the session.
- `drop_result_slots` silences PySide's `RuntimeWarning` for disconnecting a signal that had no receivers.
- P3-4 changed one assertion in `test_quit_privileged.py`, as above.

**Results.**
- Full suite (`.venv/bin/python -m pytest -q -p no:cacheprovider`): **2616 passed, 2 skipped, 1 deselected, 302.6 s**, no failures.
- Slow M4 (`-m slow tests/test_quit_bounded.py`): **20 of 20 passed, 165.3 s**.

**Not run.** `agents/bug_spray/tests` and `agents/sentry/tests` (not touched), CI on Linux, Imprint's drift test (`services/benchmarks.py` unchanged by these fixes).

### Build log, Verify gaps (2026-10-14)
Test-only commit for the eight mutants that survived Verify (`software_company/audits/sentinel/no-thread-terminate-verify.md`, section 3). No production change, and no test exposed a bug. Commit: see `git log` ("Tests for the eight mutants Verify left alive").

| Survivor | file:line | Test added | Re-planted |
|---|---|---|---|
| W6 park re-entry guard | `ui/workers.py:92` | `test_thread_parking.py::test_parking_a_thread_twice_keeps_one_entry_and_one_release` | killed |
| L2 usage handler's detached check | `main.py:4171` | `test_chat_late_cost.py::test_late_usage_from_a_stopped_worker_is_not_billed_to_the_next_request` (missed-cancel race; second run's counts are not the first's) | killed |
| L3 recorder once-per-worker | `main.py:4192` | `test_chat_late_cost.py::test_the_recorder_bills_each_worker_at_most_once` | killed |
| L4 mid-stream counts | `ui/workers.py:411` | `test_chat_late_cost.py::test_a_mid_stream_stop_after_the_usage_element_reports_its_counts` | killed |
| D1 `remaining_ms` tenfold smaller | `ui/workers.py:141` | `test_thread_parking.py::test_a_worker_that_finishes_inside_the_budget_is_joined_not_parked` | killed |
| D5 privileged join timeout (D2) | `ui/panels/vpn.py:1142` | `test_panel_shutdown_parks.py::test_the_privileged_connection_join_keeps_its_own_timeout` | killed (planted as the shared deadline's remaining ms) |
| G2 `providers` dropped from the scan | `tests/test_no_thread_terminate.py:24` | `test_no_thread_terminate.py::test_the_scan_covers_every_directory_the_spec_names` | killed |
| G4 qualname stack never popped | `tests/test_no_thread_terminate.py:70` | `test_no_thread_terminate.py::test_the_scanner_names_a_function_after_a_nested_sibling` | killed |

L4 and G4 were not on the coordinator's list but were Verify survivors, so they got tests too. The stale name in `test_quit_privileged.py` is now `test_close_with_quit_anyway_stops_background_work_and_accepts`.

**Run.** `.venv/bin/python -m pytest -q tests/test_chat_late_cost.py tests/test_thread_parking.py tests/test_panel_shutdown_parks.py tests/test_no_thread_terminate.py tests/test_quit_privileged.py`: **76 passed in 13.0 s**. Full suite not re-run (test-only change).

### Document log (2026-10-14)
Tech writer. Playbook `tech-writer.md` is `status: current`, last-reviewed 2026-10-14. A9.11 and review P3-5 done:
- User-facing text for Stop, Quit, Emergency Reset and the Tunnel health checks: `README.md`; Learning Centre `docs/training/chat.md`, `workspace.md`, `controls_settings.md`, `portable.md`, `tunnel.md`; developer reference `docs/agents/chat.md`, `vpn.md`, `osint_heavy.md`; `tests/manual_test_cases.md` (the old "stopped by user" notice).
- `CHANGELOG.md` created (Keep a Changelog, `## Unreleased` plus the current v2.002). `VERSION` not bumped; Release does that, and adds the `docs/versioning.md` row.
- `docs/qa_audit/chat.md` finding 16 marked resolved; `TODO.md` items updated; ADR 0001 status set to Accepted.
- Not done here: the HQ handoff item (`software_company/audits/sentinel/HANDOFF.md:27`) lives in another repo.

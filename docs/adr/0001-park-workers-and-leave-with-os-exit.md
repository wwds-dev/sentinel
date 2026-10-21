# ADR 0001: Park worker threads, never terminate them; every quit leaves through `os._exit`

- Status: **Accepted** by the owner at the Architect gate, 2026-10-14 (D1-D6, recorded in `docs/specs/no-thread-terminate.md`, section "Architecture"). Built on branch `fix/no-thread-terminate`.
- Date: 2026-10-14
- Spec: `docs/specs/no-thread-terminate.md`, section "Architecture" (A2, A3, A5)
- Radar: `docs/company/radar-notes/2026-10-14-feature-no-thread-terminate.md`

## Context
Sentinel ends worker threads with `QThread.terminate()` on Stop, on Quit and when the Tunnel panel is hidden. Those calls are in `main.py:4195`, `:5087`, `:5106`, `ui/panels/vpn.py:1143`, `ui/panels/osint_heavy.py:726` and `agents/vpn_agent/services/health_monitor.py:85`. In the GIL build of CPython, which the architect playbook names as the target, a thread killed while it holds the GIL leaves every other Python thread blocked for good. The result is a white window that will not close. The sibling app SONAR shipped this exact bug (lab `AGENTS.md`, 2026-09-19).

Removing `terminate()` leaves threads that cannot be interrupted. `run_backend_func` is one blocking call (`ui/workers.py:249`). Qt aborts when a running QThread is destroyed. So these threads have to be **parked**: kept referenced until `finished`, then released.

Parking alone does not make Quit safe. Qt's normal teardown and Python's finalisation would still meet the parked threads, plus every thread nobody parks: panel ChatWorkers are only cancelled (`ui/panels/base.py:463-473`), as are Sentry's watch and Wi-Fi's scan. Today's sequential joins also add up to more than 25 s, against a 5 s limit the owner set (Q4).

## Decision
1. **No thread is ever terminated.** An AST test (`tests/test_no_thread_terminate.py`) fails the build on any `.terminate` attribute use, or on a reflective `"terminate"` lookup, in `main.py`, `ui/`, `services/`, `providers/` and `agents/` (except the `bug_spray` submodule). Process handles need an allow-list entry naming the file, the function and the reason.
2. **One parking registry**, `_DETACHED_WORKERS` in `ui/workers.py`, with one primitive, `park(thread, *companions)`. It is released on the UI thread after `finished`: `wait()`, then `deleteLater()`, then the reference is dropped. Code outside `ui/` that cannot import it receives `park` by injection (HealthMonitor).
3. **Quit joins against one shared deadline** (`QUIT_JOIN_BUDGET_S = 2.5`) and parks whatever outlives it. S4's privileged joins stay as they are and are not part of this budget.
4. **Every quit ends the process with `os._exit(code)` after `app.exec()`** through one function, `_leave`. First it syncs QSettings (unless an Emergency Reset just erased their folder) and flushes stdio. This is unconditional. It does not depend on whether something is still parked.
5. **State is written through.** No code may rely on an atexit hook, a destructor, a `finally` at quit, or Qt's teardown to persist anything. Whatever truly has to run at quit goes into `_leave`, before `os._exit`.

## Alternatives rejected
- **Keep `terminate()` with longer joins.** It does not remove the GIL deadlock; it only makes it rarer. Longer joins also break Q4.
- **`os._exit` only when the registry holds a running thread** (the spec's wording). That needs a complete inventory of the app's threads. Today's code does not have one (panel workers, Sentry, Wi-Fi), and every new worker would have to remember to register, or Quit could abort. One missed thread brings back the crash. The unconditional exit needs no inventory.
- **`QThread.create()`.** Since Qt 6.3 such threads can be deleted while running, but the deletion blocks until they finish, which brings back the Quit hang (radar row 4).
- **`requestInterruption()` as the cancel mechanism.** No worker runs a loop that could poll it, and each already has `cancel()` (radar row 2: watch).
- **Interruptible HTTP** (closing a provider's socket from another thread). A larger change to every client; a non-goal of the spec.
- **Python `threading.Thread(daemon=True)` instead of QThread.** It would mean rewriting every worker's signal plumbing, and daemon threads at finalisation have their own crash modes.

## Consequences
- **Good:**
  - Quit is bounded and deterministic.
  - Qt's teardown never meets a running thread.
  - A new worker cannot bring back the exit abort even if nobody parks it.
  - Emergency Reset with a request in flight no longer ends in Qt's teardown.
- **Bad:**
  - `finally` blocks, atexit hooks and Qt destructors do not run at quit. Today this costs nothing: the usage log, run log, chat saves and `settings.local.json` are all synchronous writes on the UI thread; QSettings is synced explicitly; and no atexit hook is registered (verified, spec A3). It becomes a standing rule for future code (Decision 5).
  - Teardown bugs that a normal exit would surface, such as a crash in a destructor, will no longer show up at quit.
  - A cloud request abandoned by Quit may be billed by the provider without a local record. The cost cannot be known without waiting for the reply.
  - A parked request and a new request can use the same provider client at the same time. That is thread-safe for openai, anthropic, httpx and requests; google-genai is UNVERIFIED.
- **Tests that change:** `tests/test_quit_anyway_exit.py:33-37` asserts that a normal quit neither syncs nor exits. It will assert that it does both. This changes specified behaviour, on the owner's approval of this ADR.
- **Reversal cost:** small in code (make `_leave` conditional again). It grows with time, because once this is in place new code may legitimately assume teardown never runs at quit. Reversing it would need an audit of every write added after this ADR.
- **Free-threaded CPython** (not a target, per the playbook) would not make `terminate()` safe. This decision stands either way.

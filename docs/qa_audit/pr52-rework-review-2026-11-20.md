# Review: PR #52 reworked (reset latch on the local-model stores)

Date: 2026-11-20. Reviewer: an independent read-only review agent (not the author). Scope: the
uncommitted change in `main.py`, `services/local_ratings.py`, `services/routing_advisor.py` and
`tests/test_reset_fence.py`, and the two shared files as copied to Imprint.

## Why a rework
PR #52 made the two shared files import `services.portable_reset`. They must stay byte-identical
with Imprint's copies (`tests/test_providers_drift.py`), and Imprint has no such module. Test-merged
onto `main` at `f1fbac8`: 4217 passed, 2 failed, both in that drift test. The rework uses the
`WRITE_GUARD` hook that `services/benchmarks.py` already has in both apps.

## How it landed
Two chats worked this item at the same time on 2026-11-20. The hook in the two shared files, its
wiring in `main.py` and PR #52's fence change reached `main` as `68089cd` (Imprint `2c6a61b`) from the
other chat, with byte-identical shared files. The commit that carries this report adds finding 1's
fix and the tests for the hook, the wiring and the usage slot. The review below covered both parts.

## Findings
| # | Severity | Where | Finding | Outcome |
|---|---|---|---|---|
| 1 | P1 | `main.py` `_on_measure_usage`; `ui/workers.py` `_RESULT_SIGNALS` | The measurement worker's `usage_signal` is not a result signal, so the fence does not disconnect it. A parked run's cloud call returning during a reset queues the slot; the post-erase dialog's event loop delivers it and `usage_tracker.log_request` recreates `sentinel.db` in the erased folder. | Fixed in this change: the slot returns when its sender is fenced or the latch is set. Two tests; 3 of 3 mutants killed. |
| 2 | P2 | `services/local_ratings.py` `_save`; `services/routing_advisor.py` `_write_json` | The latch is checked once, before `mkdir` and the write, with no lock shared with the erase. `benchmarks.py` has the same gap. | Not fixed here. `TODO.md` P2. |

No P0. Nothing else at P1.

## What the review confirmed
- The only disk writers in the two shared files are `RatingStore._save` and `_write_json`; every
  store method goes through them, and the guard returns before `mkdir` in both.
- No other Sentinel module writes the three files, and no entry point builds these stores without
  importing `main`, which sets the hook.
- Reading a module attribute and `Event.is_set()` from a worker thread is safe. Callers ignore
  whether the write happened; after a refused reset the in-memory state is written on the next save.
- Imprint sets no hook and its behaviour is unchanged.
- The new tests are not vacuous. Minor: the no-guard test does not cover `SavingsTally`.

## Checks run by the author
- Sentinel full suite on the final change: 4223 passed, 2 skipped, 1 deselected (offscreen).
- Mutation: 9 of 9 killed on the guards, the hook wiring and the fence loop; 3 of 3 on the usage
  guard.
- The two shared files are byte-identical in Sentinel and Imprint.

## Not checked by the reviewer
It ran no tests. It did not read the PR #52 commit, the drift test, or `progress_signal` handlers
beyond their wiring.

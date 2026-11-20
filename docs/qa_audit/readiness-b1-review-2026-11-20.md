# Readiness B1: review and verification (2026-11-20)

Change: `GodAI.models_for_provider` no longer calls `client.list_models()` for a cloud
provider; model boxes and the router read `_cached_models` (the last model check, else the
shipped lists). Live listing stays in `ModelScanWorker`, gated by `scan_plan`.

## Verify
- Full suite on the final change: 4203 passed, 14 skipped, 1 deselected (`.venv/bin/python -m pytest -q`, offscreen).
- Mutation checks: restoring the live listing in `models_for_provider` fails 5 tests
  (`tests/test_no_cloud_listing_on_ui_thread.py` x3, `TestLoadModelsInto` x2); reloading the
  boxes only when a model is new fails
  `test_a_finished_check_reaches_the_model_boxes_even_with_nothing_new`.

## Review (independent agent, read-only, not the author): no P0, no P1
- P2, fixed: model boxes were reloaded after a check only when it found a new model, so a
  retired model stayed on offer. `_on_model_scan_finished` now reloads whenever a provider
  listed anything; two tests added.
- P2, fixed in the README: until a provider is ticked and **Check now** is pressed, and
  always in Local only, cloud model boxes show the shipped lists.
- P2, open (TODO): the audit asked for a socket-recording launch test; the new tests spy on
  `list_models` only, so another cloud contact at construction would not be caught by them.
- P3, fixed: stale comment in `load_provider_models`; README says "cloud provider".
- P3, open (TODO): `load_models` calls `self.ollama.list_models()` on the UI thread at
  construction (loopback, or wherever `OLLAMA_HOST` points). Not introduced by this change.
- P3, left: `context` and `widget` are unused parameters of `models_for_provider`.
- Checked and clean: no other caller of a cloud `list_models` in `main.py`, `ui/` or
  `agents/`; `scan_plan` returns nothing in Local only; `start_model_scan` is the only
  creator of `ModelScanWorker`; no Qt threading change.

Authorship: the fix and `tests/test_no_cloud_listing_on_ui_thread.py` were written by the
B1 fix session on 2026-11-18 and left uncommitted; a second session rebuilt them on `main`,
replaced the two obsolete tests and applied the review fixes.

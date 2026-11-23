# Spec: Local only falls back to any running local runtime

Needs: define, build, review, verify, docs
Status: approved by the owner 2026-11-22 ("Approve and merge"). Owner go-ahead to fix: "Yes, fix it here" (2026-11-22).

## Problem
In Chat, with Execution mode **Local only** and a cloud provider picked in the run bar,
`resolve_backend_model` (`main.py`) always substitutes `ollama` with the saved Ollama default
model. On a Mac where only LM Studio runs, that request fails although a usable local model
exists. Sentinel supports two local runtimes (ADR 0001); this path ignores the second.

## Behaviour
When the substitution applies, pick the local route in this order, from the cached local
inventory (`LocalInventory.snapshot()`, never a fetch on the UI thread):
1. Ollama with its saved default (`default_model_ollama`, else `DEFAULT_LOCAL_MODEL`) when
   Ollama is up, or when the inventory has no data yet (unchanged behaviour).
2. Otherwise, when the LM Studio server is up: the saved LM Studio default
   (`default_model_lmstudio`) if the inventory lists it and it fits in memory; else the first
   loaded LM Studio model that fits; else the first LM Studio model that fits.
3. Otherwise unchanged: Ollama with its saved default (the run then fails as today, with the
   runtime's own error).
The notice keeps its wording with the chosen route: `Local only mode: using <provider> · <model>
instead of …`. A cloud provider is never contacted in any branch.

## Out of scope
Agent panels (readiness H3), routing advisor, Hybrid and Cloud only modes, starting the LM
Studio server.

## Success measure
Tests in `tests/test_route_notice_visible.py`: Ollama down + LM Studio up → `lmstudio` with the
saved default / loaded model; too-big models skipped; both down or no inventory data → `ollama`
(old behaviour); the notice names the chosen route.

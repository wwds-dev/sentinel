# ADR 0001: One provider registry; "local" is a property of the spec

Status: proposed (2026-10-19, architect, for the owner's gate). Plan: `docs/local_model_routing_plan.html` in the lab root, section 3, phase 1.

## Context
Sentinel asked "is this Ollama?" by comparing the provider name to the string "ollama" in about 30 places: pricing, privacy score, the paid-request dialog, usage tracking, budget gates. A second local runtime (LM Studio, where the owner's DeepSeek R1 distills and Dolphin models live) would have been priced as unknown, scored as a cloud call and put behind the paid-request dialog. Ollama's address was also hardcoded.

## Decision
- `services/providers.py` holds one `ProviderSpec` per provider (name, kind local|cloud, protocol openai|ollama|native, base URL, key name). It is a byte-identical copy in Sentinel and Imprint, with `services/lmstudio_client.py`; `tests/test_providers_drift.py` fails the build if they differ, and there are no allowed local differences.
- Every provider-name comparison asks `is_local(name)`. A zero price stays *unknown* for cloud providers only; a local provider's cost is known and zero.
- A list that allows one local runtime (`allowed_providers: ["ollama"]`) allows every local runtime (`expand_allowed`); cloud providers are never expanded.
- `run_backend` looks the client up by provider name and calls stream, then chat, then generate; local providers are fit-checked first. The Ollama address comes from `OLLAMA_HOST`.
- The uncensored/abliterated tag is derived from the model name and publisher by `uncensored_tag()`, never stored, so a new download is tagged by construction.

## Consequences
- A third local runtime is one registry row plus a client, not a hunt for string comparisons.
- `tests/test_routing_phase1.py` fails if a new `== "ollama"` comparison appears outside the allowed Ollama-only features.
- Local models stay unrated until phase 3, so the router cannot pick them by itself yet (the value rule is unchanged).
- Rejected: a shared Python package (the two apps already share code by drift-tested copies; a package adds a release step for two consumers).

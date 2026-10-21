# 0003 — Hybrid routing: an advisor per job, never an automatic paid switch

Date: 2026-10-19 · Status: accepted (owner decisions of 19 Oct 2026 in
`docs/local_model_routing_plan.html`, lab root)

## Context
Phases 1–3 made local models first-class and rated. The owner chose
*Local, advise per job* as the default: jobs run local, and each new job is
checked against the auto-routed recommendation. Sentinel's value rule and
Imprint's never-auto-switch rule stay.

## Decision
- One pure module, `services/routing_advisor.py`, byte-identical in Imprint and
  drift-tested, decides per job: run, suggest one alternative (with named
  reasons, cost and time), or refuse. It never switches by itself; the app
  acts only on the user's answer, and a paid answer still passes the validator
  and the external-API confirmation.
- The only runs on a model other than the selection without a banner: a rule
  the user made with *Always for this kind of job* (removable in Settings →
  Models), and *Local first* picking a free, rated, good-enough local model.
  Neither ever picks a paid model the user did not choose.
- Five modes, an app setting plus per-agent overrides; the old three-way
  Execution mode is derived from them so the run path did not have to change.
- Failover is an offer built from `RouteDecision.fallbacks`, not a retry.
  Local only and Privacy first never offer, suggest or follow a rule to the cloud.
- The advisor reads caches only (ratings, prices, local inventory, last model
  scan), so it may run on the UI thread.

## Consequences
- Only Chat is advised in this phase; panels keep their own model (TODO).
- Time estimates use fixed throughput until measured (SUGGESTIONS #34).

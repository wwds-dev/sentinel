# Sentinel roadmap

Updated 2026-10-09. Sentinel is the security and intelligence hub inside the
Lab workspace. This roadmap covers Sentinel only; Create & Publish, SONAR,
Backup & Sync and Lab Hub keep their work in their own repositories.

Current tracked release: **v2.002**. Sentinel uses a monotonic
`vMAJOR.SEQUENCE` policy with one canonical `VERSION` file; see
[`versioning.md`](versioning.md). The next release increment is kept as an open,
numbered item in `TODO.md` so the Lab project monitor can surface it.

## V2 — released

V2 establishes the eight-agent product: Chat, Trace, Bloodhound, Beacon, Sentry,
Bug Spray, Tunnel and Forge. The product, the installed application, the repository
and the checkout directory are all named **Sentinel**; the `sentinel_fork`
checkout and the `sentinel-ai-fork` GitHub repository were renamed to match on
2026-10-06.

Delivered:

- one shared panel architecture and a balanced three-column workspace;
- compact run controls, visible route/cost state and exact budget enforcement;
- structured result sections for the five analysis-oriented agents;
- Chat projects for grouping, filtering, assignment and spend attribution;
- consent-gated Trace research and scoped local/remote Bloodhound discovery;
- Beacon adapter preflight and a documented two-interface workflow;
- Tunnel connection checks, profile comparison, safe action previews,
  private-key-free WireGuard configuration inspection, and gated
  WireGuard/OpenVPN Connect/Disconnect with Import config, a pf kill switch, a
  local audit log and the Your IP & DNS readout;
- source, packaged and USB-portable runtime paths with safe migration of the
  legacy `Sentinel Fork` application data — the old application name, kept
  literal because it names what is migrated *from* — that never reads or
  overwrites `Sentinel AI`;
- an in-app Learning Centre with a reproducible nine-image screenshot set.

Release verification at the V2 release: 556 Sentinel tests and the complete Lab
Hub suite passed (the VPN Agent is now in this tree and has no separate suite).
The final interface was rendered at 1600×1000 and inspected rather than
inferred from source code. The current test count and companion-suite layout
are in [`testing_roadmap.md`](testing_roadmap.md).

## V3 — proposed sequence

1. Validate the Learning Centre with first-time users and add graded exercises.
2. Extract a shared Lab platform package only when at least one other hub is
   ready to consume it in the same change.
3. Build the guarded external-tool adapter: dependency checks, scope review,
   previews, cancellation, timeouts, local audit records and privilege gates.
4. Extend Bloodhound with metadata and rule matching.
5. Verify Tunnel's gated Connect/Disconnect and kill switch on real hardware,
   move imported profiles and kill-switch state under Sentinel's data folder,
   then protected key backup/recovery and a source/frozen/portable parity audit.
6. Add further public-source Trace adapters, authorised Bug Spray assessment
   and passive Beacon analysis.
7. Evaluate Chat streaming, automatic local-model budget fallback, shared
   retry/backoff and single-file run export.
8. If normal use shows value beyond grouping, add Chat Project instructions,
   defaults, project budgets and management UI.

V3 continues to exclude denial of service, credential theft, stealth,
persistence and uncontrolled exploitation.

## Related plans

- `docs/projects_roadmap.md` — optional later Chat Project context features.
- `docs/workspace_structure.md` — ownership across the Lab hubs.
- `docs/refactor_plan.md` — completed panel extraction history.
- `docs/portable_mode.md` — supported portable distribution and data rules.

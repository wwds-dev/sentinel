# Sentinel roadmap

Updated 2026-09-22. Sentinel is the security and intelligence hub inside the
Lab workspace. This roadmap covers Sentinel only; Create & Publish, SONAR,
Backup & Sync and Lab Hub keep their work in their own repositories.

Current tracked release: **v2.002**. Sentinel uses a monotonic
`vMAJOR.SEQUENCE` policy with one canonical `VERSION` file; see
[`versioning.md`](versioning.md). The next release increment is kept as an open,
numbered item in `TODO.md` so the Lab project monitor can surface it.

## V2 — released

V2 establishes the seven-agent product: Chat, Trace, Bloodhound, Beacon, Bug
Spray, Tunnel and Forge. The old `sentinel_fork` repository key remains stable
for paths and Lab automation, but the public product and installed application
are named **Sentinel**.

Delivered:

- one shared panel architecture and a balanced three-column workspace;
- compact run controls, visible route/cost state and exact budget enforcement;
- structured result sections for the five analysis-oriented agents;
- Chat projects for grouping, filtering, assignment and spend attribution;
- consent-gated Trace research and scoped local/remote Bloodhound discovery;
- Beacon adapter preflight and a documented two-interface workflow;
- Tunnel connection checks, profile comparison, safe action previews and
  private-key-free WireGuard configuration inspection;
- source, packaged and USB-portable runtime paths with safe legacy
  `Sentinel Fork` migration that never reads or overwrites `Sentinel AI`;
- an in-app Learning Centre with a reproducible nine-image screenshot set.

Release verification: 556 Sentinel tests, the complete nested VPN Agent suite
and the complete Lab Hub suite pass. The final interface was rendered at
1600×1000 and inspected rather than inferred from source code.

## V3 — proposed sequence

1. Validate the Learning Centre with first-time users and add graded exercises.
2. Extract a shared Lab platform package only when at least one other hub is
   ready to consume it in the same change.
3. Build the guarded external-tool adapter: dependency checks, scope review,
   previews, cancellation, timeouts, local audit records and privilege gates.
4. Extend Bloodhound with metadata and rule matching.
5. Add Tunnel's separately confirmed WireGuard execution, then protected key
   backup/recovery and a source/frozen/portable parity audit.
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

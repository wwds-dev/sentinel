# Lab workspace structure

Updated 2026-10-09. Lab Hub is the front door; each product has one canonical
repository under `/Users/as/Documents/lab/active/`.

## Product ownership

| product | purpose | canonical contents |
|---|---|---|
| **Sentinel** (`sentinel`) | security and intelligence | Chat, Trace, Bloodhound, Beacon, Sentry, Bug Spray, Tunnel, Forge |
| **SONAR** (`sonar`) | markets and wagering | Oracle direction and Playmaker |
| **Create & Publish** | creative and publishing | Website, vidforge, Fiverr, Maestro, Manuscript, Publisher |
| **Backup & Sync** | maintenance | Backup Control Center and git autosync |
| **Lab Hub** (`lab_hub`) | launcher and project monitor | product tiles and small Lab utilities |

`sentinel_ai` is an archived, separate application and data set. Sentinel does
not overwrite it, import from it or use its bundle identity. The name is
**Sentinel** throughout — application, bundle, repository, checkout directory
and Lab tooling key. The checkout was `sentinel_fork` and the GitHub repository
`sentinel-ai-fork` until 2026-10-06; both were renamed, and GitHub redirects
the old URL.

## Sentinel V2 state

- The public window, bundle, runtime data directory, single-instance key,
  documentation and Lab Hub tile use **Sentinel**.
- The installed launcher points to the canonical Lab checkout, not a Codex
  workspace copy.
- Existing `Sentinel Fork` application-support data — the old application
  name, kept literal here because it names the directory being migrated *from*
  — is renamed to `Sentinel` when safe. If both directories exist, neither is
  automatically combined.
- The nested `agents/vpn_agent/` repository owns Tunnel's reusable VPN logic;
  Sentinel imports it instead of keeping a second copy.
- Bug Spray follows the same nested-companion rule. It is an agent in Sentinel,
  not a second independent workspace implementation.
- Refactor phases 1–4 are complete. Shared UI and guardrail code is stable in
  Sentinel; extracting a cross-product platform package is V3 work and must be
  coordinated with another consumer.

## Data boundaries

Source mode stores Sentinel state in the canonical project checkout. A frozen
build uses `~/Library/Application Support/Sentinel/`. Portable mode uses only
the validated `Sentinel Data` directory beside its marker. The portable builder
does not copy the checkout's real `.env`.

The installer may replace `/Applications/Sentinel Fork.app` with
`/Applications/Sentinel.app` and rename its application-support directory. It
must never remove `/Applications/Sentinel AI.app` or
`~/Library/Application Support/Sentinel AI/`.

## Later Lab-wide work

A common platform package can eventually hold provider clients, request guards,
budgets, usage, registry/database services, runtime paths and shared UI. Do that
only as one coordinated migration; keeping the code inside Sentinel for V2 is
safer than creating an unused fifth package that immediately drifts.

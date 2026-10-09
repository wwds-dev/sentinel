# Sentinel promise audit — 2026-10-09

Requirements-traceability audit of Sentinel v2.002 at commit c84e53c (plus the
uncommitted `config/settings.json` change). One file per built-in agent and one
for the shared controls. Each file holds a verdict, counts, the full
promise-by-promise table with file:line evidence, cross-document findings
(one-sided promises, contradictions, undocumented behaviour) and a must-fix list.

| File | Scope | Promises | Kept | Narrower | Stub/missing | Not code-verifiable |
| --- | --- | --- | --- | --- | --- | --- |
| chat.md | Chat | 69 | 39 | 21 | 4 | 5 |
| trace.md | Trace (osint) | 114 | 82 | 24 | 4 | 4 |
| bloodhound.md | Bloodhound (osint_heavy) | 91 | 63 | 22 | 2 | 4 |
| beacon.md | Beacon (wifi) | 50 | 33 | 4 | 9 | 4 |
| sentry.md | Sentry | 67 | 47 | 10 | 5 | 5 |
| bug_spray.md | Bug Spray (bug_bounty) | 83 | 40 | 27 | 11* | 5 |
| tunnel.md | Tunnel (vpn) | 114 | 77 | 30 | 5 | 2 |
| forge.md | Forge (manager) | 53 | 30 | 18 | 2 | 3 |
| shared.md | Routing, pricing, Settings, Model Updates, rails, themes, tray, Learning Centre, persistence | 137 | 102 | 31 | 2 | 2 |

\* bug_spray.md rows 77–80 ("main.py / README / venv absent", "no platforms enabled")
are artefacts of the audit copy, which lacked those files; the Mac checkout has
them and `config.json` enables all five platforms. Ignore those four rows.

Known-wrong claim: sentry.md row 38 says `SentryPanel.stop()` raises
`AttributeError` before the first AI request; `ui/panels/base.py:150` initialises
`self.worker = None`, so it does not. The rest of that item (cancel flag never
read, no `shutdown()`) stands.

The consolidated view, confirmed defects D1–D7 and the revised plan are in the
"Sentinel QA Test Plan" doc.

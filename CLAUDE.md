# Sentinel — instructions for Claude

@AGENTS.md

`AGENTS.md` is the company pipeline (installed from `software_company`); app facts are in `docs/company/app-context.md`. The rules below are this app's own and win on conflict.

## Task lists
- `TODO.md` and `SUGGESTIONS.md` in this folder feed the Lab Projects Monitor. Keep their existing format exactly.
- TODO items look like: - [ ] `P1` `bug` `@me` Task text. Priority P0-P3, category from the legend at the top of TODO.md, owner `@me` (needs the user) or `@ai` (Claude can do it). Longer reasoning goes under the **Detail** section, not in the checklist line. Mark finished items - [x].
- SUGGESTIONS use the status tags IDEA, CONSIDERING, PLANNED, DONE, REJECTED.
- When the user mentions a new open item, offer to add it to TODO.md instead of keeping a separate list.

## Working rules
- After changing code, finish with a short list of README, TODO.md or SUGGESTIONS.md entries that are now out of date. Update them only if the user says so.
- Flag bugs, security issues or risky changes you notice in code you touch. Do not run a full review unless asked.
- Before big refactors or changes across many files, state the scope and a cheaper alternative if one exists.
- Edit only the parts that need changing; do not regenerate whole files.
- Never print or copy values from `.env`.

## Current goal
Prepare Sentinel for a formal testing phase: professional level, with functionality at 100% of what its agents promise before testing starts.

## Decisions already made
- The VPN Agent companion features (Tor, proxy chain, MAC changer, server provisioning) stay in Sentinel and must be fully functional. Do not propose removing them.

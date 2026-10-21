# Sentinel — instructions for Claude

@AGENTS.md

`AGENTS.md` is the company pipeline (installed from `foundry`); app facts are in `docs/company/app-context.md`. The rules below are this app's own and win on conflict.

## Task lists
- `TODO.md` and `SUGGESTIONS.md` in this folder feed the Lab Projects Monitor. Keep their existing format exactly.
- TODO items look like: - [ ] `P1` `bug` `@me` Task text. Priority P0-P3, category from the legend at the top of TODO.md, owner `@me` (needs the user), `@ai` (Claude can do it) or `@local` (a local model drafts it; the tiers and required checks are in the Local-model plan under Detail in TODO.md). Longer reasoning goes under the **Detail** section, not in the checklist line. Mark finished items - [x].
- SUGGESTIONS use the status tags IDEA, CONSIDERING, PLANNED, DONE, REJECTED.
- When the user mentions a new open item, offer to add it to TODO.md instead of keeping a separate list.

## Working rules
- After changing code, finish with a short list of README, TODO.md or SUGGESTIONS.md entries that are now out of date. Update them only if the user says so.
- Flag bugs, security issues or risky changes you notice in code you touch. Do not run a full review unless asked.
- Before big refactors or changes across many files, state the scope and a cheaper alternative if one exists.
- Edit only the parts that need changing; do not regenerate whole files.
- Never print or copy values from `.env`.

## VPN Agent (Tunnel) rules
Applies to Tor, the proxy chain, the MAC changer, the kill switch and server provisioning. These run on the owner's own Mac and provision only servers the owner owns or rents. What Sentinel is: `docs/company/app-context.md`.
- Fail closed: if a tunnel, Tor circuit or proxy hop drops, traffic is blocked. No clear-net fallback.
- No DNS, IPv6 or WebRTC leaks; all DNS goes through the tunnel. The kill switch must work and be covered by a test.
- MAC changer: record the original address, restore it on exit and on crash, validate the address, use locally administered unicast addresses, touch only the interface the user selected.
- Tor control port: localhost only, authenticated.
- Subprocess calls use argument lists. No `shell=True`, no string-built commands. Validate and allow-list interface names and user input.
- Least privilege: root-level actions stay in a small separate path with the macOS confirmation dialog.
- Secrets (SSH keys, API tokens) live in the OS keychain. Never log, commit or print them.
- Provisioning: dry-run mode, confirmation before creating or destroying paid resources, idempotent, with teardown.
- No traffic or IP logging beyond local diagnostics, and redact those.
- Every feature ships with tests, including failure cases (hop down, DNS failure, crash mid-change). Never claim a feature works without a passing test.

## Local-model work
- One module or function per task, using the spec and only the files it needs (small context).
- Commit trailers: `drafted-by: <model>` and `verified-by: <checks that passed>`. No verified-by line means unverified.
- Never run live MAC, kill-switch, tunnel or leak actions on this machine. Tests use mocks; real checks happen in a VM or on a throwaway network.
- Done means tests pass, TODO.md is updated, and for Tier C items an Opus audit is finished. Tiers and checks: Local-model plan in TODO.md.

## Current goal
Prepare Sentinel for a formal testing phase: professional level, with functionality at 100% of what its agents promise before testing starts.

## Decisions already made
- The VPN Agent companion features (Tor, proxy chain, MAC changer, server provisioning) stay in Sentinel and must be fully functional. Do not propose removing them.

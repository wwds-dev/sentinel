# Sentinel — Suggestions

Ideas not yet committed to. Status: `IDEA` · `CONSIDERING` · `PLANNED` · `DONE` · `REJECTED`

---

## v3 — bigger swings

| # | Suggestion | Category | Effort | Status |
|---|---|---|---|---|
| 20 | OSINT Keys tab: add key-validation ping (HEAD request to each provider's API) so the tab can show green/red key health next to each Save Key button | feature | S | IDEA |
| 21 | Surface the BreachDirectory provider inside Bloodhound's email collection. It is already a live, keyless lookup in Trace's email path (`providers/email_lookup.py`); this is about adding it to Bloodhound, not wiring a key — BreachDirectory takes no key. | feature | S | CONSIDERING |
| 22 | OSINT Keys tab: Learning Centre topic explaining the ops-email strategy, HIBP vs BreachDirectory tradeoffs, and which keys to prioritise first | docs | S | IDEA |

| # | Suggestion | Category | Effort | Status |
|---|---|---|---|---|
| 7 | Streaming responses in the chat panel rather than wait-then-dump | feature | L | IDEA |
| 8 | Local model provider (Ollama) as a zero-cost fallback when the budget cap is hit | feature | L | IDEA |
| 9 | Retry-with-backoff wrapper shared by every provider client, instead of per-client handling | infra | M | IDEA |
| 10 | Export a run (prompt + response + usage + cost) as a single markdown file for archiving | feature | S | IDEA |
| 11 | Add intermediate and independent exercises to the existing practical course for every agent | docs/feature | M | PLANNED |
| 12 | Add optional numbered graphical callouts to the reproducible, current screenshot set | docs/design | M | CONSIDERING |
| 14 | Build a common adapter layer for selected Kali tools: availability checks, previews, scope gates, cancellation, logs and structured results | infra/security | XL | PLANNED |
| 15 | Bloodhound adapters for ExifTool/YARA and optional OpenVPN config inspection alongside Tunnel's delivered private-key-free WireGuard parser | feature/security | L | CONSIDERING |
| 16 | Trace public-source adapters, followed by authorised Bug Spray and passive Beacon integrations | feature/security | XL | CONSIDERING |
| 17 | Per-agent cost breakdown in cost history, so a daily-cap spike can be traced to its source | feature | M | IDEA |
| 18 | Chat Project instructions, defaults, budgets and management after grouping has been tested in normal use | feature | L | CONSIDERING |
| 19 | Gated WireGuard actions and local key/recovery lifecycle, building on Tunnel's read-only config inspection | feature/security | XL | PLANNED |

## Done

| Suggestion | When |
|---|---|
| Live OSINT source expansion (v2.002): Shodan InternetDB, IPinfo and Criminal IP for IPs; CourtListener court dockets (metadata-only) for companies/orgs; DeHashed breach metadata for exposure — all key-gated where paid and metadata-only | Sep 2026 |
| Tunnel "Your IP & DNS" readout (local/tunnel + public exit IP with VPN/hosting flags) and a real bash.ws DNS-leak test | Sep 2026 |
| addy.io burner-alias minting in the OSINT Keys tab (user-triggered write) | Sep 2026 |
| Saved Chats: agent filter and rename | Aug 2026 |
| `authorize_request` / `record_request` guard applied to all 19 unguarded `ChatWorker` sites | Aug 2026 |
| `FlowLayout` on 13 control rows — panels no longer crush when narrow | Aug 2026 |
| Timeouts on all cloud clients | Aug 2026 |
| Agent panel split: shared `AgentHost`, `AgentPanel`, and specialist panel modules | Aug 2026 |
| Canonical seven-agent roster; removed dead `ops_identity` sidebar entry | Aug 2026 |
| UI modules: workers, widgets, style, tooltips, and dialogs | Aug 2026 |
| Sidebar rebalance — right rail is now request-lifecycle-ordered live state only (Current Route/Cost/Budget/System); API Keys and Actions moved to the left rail with global setup | Sep 2026 |
| Key `_pending_requests` by request id instead of agent name — two runs of the same agent no longer clobber each other's context | Sep 2026 |
| Kimi prompt caching modelled in the pricing table — cached input billed at ~20% of the base rate ($0.19/1M) | Sep 2026 |
| Auto-route button on every agent panel, applying the router's recommendation directly | Sep 2026 |
| Paid-route highlighting — any non-Ollama provider/model marked amber on the dropdown | Sep 2026 |
| Chat composer overhaul — Enter-to-send/Shift+Enter, taller input, per-message timestamps, "Conversation" relabeling | Sep 2026 |
| Learning Centre foundation — searchable Quick Start, Chat, agent workflows and advanced-tools lessons | Sep 2026 |
| Exhaustive Learning Centre — workspace/Settings reference, seven agent courses, privacy, troubleshooting, expanded workflows and nine current screenshots | Sep 2026 |
| Tunnel Connection Check — read-only tools/tunnels/route/DNS cards with separately confirmed public-IP and latency checks; 16 focused tests | Sep 2026 |
| Tunnel profile comparison and safe action previews — secret-field filtering, profile/protocol-aware findings and remediation with no execution path; 26 focused tests | Sep 2026 |
| Tunnel private-key-free WireGuard config inspection and live-intent comparison | Sep 2026 |
| Budget card spend meters with editing kept in Settings | Sep 2026 |
| Structured result cards for Trace, Bloodhound, Beacon, Bug Spray and Forge | Sep 2026 |
| Chat Projects grouping, assignment and spend attribution | Sep 2026 |
| Sentinel product rename and legacy Sentinel Fork data migration, without touching Sentinel AI | Sep 2026 |
| Native one-shot thin launcher (`scripts/thin_launcher.c`), replacing the AppleScript/launchctl applet | 2026-09-15 |
| Inspector no longer clips at the narrowest sidebar width | 2026-09-15 |

## Portable privacy boundaries

- Keep **Emergency Reset** limited to the validated `Sentinel Data`
  directory. Do not expand it into whole-drive formatting or macOS log removal.
- Consider encrypted APFS provisioning and a guided backup/restore verifier for
  stronger data-at-rest protection without making trace-free claims.
- A future status page could list which Sentinel-owned categories will be
  removed before confirmation and verify afterward that the data folder is empty.

## Rejected

| Suggestion | Why |
|---|---|
| Fork the ROI / investment agents back in | They moved to SONAR on purpose; two homes for the same logic is worse than one |

# Advanced tools and v3 direction

Sentinel provides a guided interface. Specialist tools can go deeper, but they
also require more knowledge and care. The v3 goal is to integrate selected,
well-established capabilities through visible commands, scope checks,
cancellation, logs and structured results—not to copy Kali Linux wholesale.

| Agent | Possible v3 integrations | What they add |
|---|---|---|
| Trace | SpiderFoot, theHarvester, Sherlock | Broader authorised public-source collection |
| Bloodhound | ExifTool, YARA, Autopsy | Metadata, rule-based matching and forensic review |
| Beacon | Kismet, Wireshark | Passive wireless discovery and packet analysis |
| Bug Spray | Nmap, OWASP ZAP, Nuclei, Nikto, WhatWeb | Authorised service and website assessment |
| Tunnel | WireGuard, OpenVPN, tcpdump | VPN management and connection diagnosis |
| Forge | None; it runs no external tools | It drafts reviewable agent scaffolds, not reports or scripts |

## Where Sentinel currently stops

Sentinel can explain, structure, run a small set of built-in diagnostics and
prepare reviewed commands/configuration. It is not a replacement for a full
security distribution, forensic workstation or administrator terminal. A tool
listed here is a roadmap candidate, not proof that it is installed or integrated.

## Proposed v3 integration contract

Every adapter should expose the same understandable lifecycle:

1. **Availability** — show whether the dependency and supported version exist.
2. **Scope** — record the exact host, network, file set or program authorised.
3. **Preview** — show the operation and important limits before execution.
4. **Approval** — require confirmation for privileged, active or cloud work.
5. **Run** — use timeouts, cancellation, bounded output and conservative defaults.
6. **Normalise** — convert output into evidence with the original raw record retained.
7. **Review** — let the user select what moves to another agent or model.
8. **Audit** — record time, tool, version, scope, settings, result and error.

## Staged candidates

**Bloodhound first:** ExifTool for richer metadata and YARA for user-selected,
read-only rule matching. A future Autopsy handoff can open a controlled forensic
case without giving an AI unrestricted disk access.

**Tunnel next:** detect WireGuard/OpenVPN availability, validate configuration,
summarise status and capture bounded diagnostics. Applying firewall, routing or
VPN changes remains an explicit privileged action with a rollback plan.

**Trace:** adapters for public-source tools should declare each destination,
identifier shared, rate limit and terms. Results must preserve source URLs and
collection times.

**Bug Spray:** Nmap, WhatWeb, ZAP, Nuclei and Nikto adapters require an owned or
authorised target, conservative presets and clear separation between detection
and verified impact. No automatic exploitation.

**Beacon:** begin with passive Kismet/Wireshark collection and analysis. Active
wireless operations remain isolated-lab features with explicit hardware,
channel, BSSID and test-window confirmation.

## Tool name warning

Sentinel's **Bloodhound** is its investigation and file-discovery agent.
SpecterOps **BloodHound** is a separate Active Directory security product. The
similar name does not mean that product is included.

## Safe escalation

Start with Sentinel. Move to a specialist tool only when the task requires it.
Preview privileged or active operations, confirm the target and scope, use
conservative limits, and retain an audit record. Sensitive findings remain
local unless the user explicitly approves a cloud handoff.

Sentinel v3 should exclude destructive actions, credential theft, stealth or
persistence features, denial-of-service tooling, and uncontrolled exploitation.

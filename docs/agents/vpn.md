# TUNNEL — self-hosted VPN design & config builder

`key: vpn` · class: `agents/vpn_agent/sentinel_chat_agent.py → VpnAgent` · panel: `ui/panels/vpn.py → VpnPanel`

> Defensive, self-hosted infrastructure only — a VPN you own end to end, on hosts you are authorised to run.

## What it does
Brings the domain knowledge of the standalone **VPN Agent** app into Sentinel as one agent in the list. Five paths share one workspace:
1. **Connection Check** — a read-only, model-free snapshot of installed VPN tools, active WireGuard/OpenVPN state, the default route, and configured DNS. It can compare that snapshot with a selected VPN Agent profile and turn mismatches into plain-language next steps. WireGuard peer keys are not displayed and private key material is never requested. Optional public-IP and latency checks require a separate confirmation.
2. **Advisor** — an LLM that reasons from a structured system prompt about remote vs native topology, WireGuard vs the OpenVPN 443 fallback, the fail-closed kill switch, and DNS/IPv6/WebRTC leaks. Answers as SUMMARY · TOPOLOGY · SECURITY & LEAKS · COMMANDS/CONFIG · RECOMMENDATIONS.
3. **Config & Deploy Builder** — deterministic and offline: renders a WireGuard **server** + **client** config plus a numbered stand-up runbook, an optional OpenVPN TCP/443 fallback, and (remote mode) a macOS kill-switch pf snippet. No LLM, no network, no crypto dependency — keys are clearly-marked placeholders next to the exact `wg genkey` commands that fill them.
4. **Safe Action Preview** — shows the expected effects, pre-flight checklist, and proposed `wg-quick` commands for Connect, Disconnect, or Restart. It is display-only: Sentinel does not run the command, request administrator access, or change network state.
5. **Config Inspection** — reads one WireGuard file the user explicitly selects, discards `PrivateKey` and `PresharedKey` values during parsing, and reports only routing, DNS, interface and endpoint metadata. It can compare those non-secret values with the latest Connection Check without copying the file into chat or logs.

## Remote vs Native (the choice the agent keeps you honest about)
| | Remote (VPS) | Native (home LAN) |
|---|---|---|
| Runs on | a rented VPS, over SSH | hardware you own on your LAN |
| Traffic exits at | the server | your own home ISP |
| Hides your IP | yes | **no** |
| Changes apparent country | yes | **no** |
| Default routing | full tunnel (`0.0.0.0/0`) | split tunnel (LAN subnet) |

Native mode is an encrypted way **into** your network (NAS, printer, router), not a new way out.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Mode | `Remote (VPS)` or `Native (home LAN)`. |
| Protocol | `WireGuard` · `OpenVPN 443 fallback` · `Both`. |
| Server host | VPS IP / DDNS hostname → the client config `Endpoint`. |
| SSH user | Used in the remote deploy runbook. |
| LAN subnet | Native mode split-tunnel `AllowedIPs`. |
| Egress iface | Server NIC for the NAT `MASQUERADE` rule (default `eth0`). |
| Question | Free-text for the Advisor. |
| I want to… | Choose troubleshooting advice or offline configuration generation; only the relevant controls remain visible. |
| Check Connection | Runs the local read-only diagnostic snapshot. No provider or model is used. |
| Compare profile | Reads the companion VPN Agent profile list without selecting, creating, or changing a profile. The saved active profile is preselected when available. |
| Include public IP and latency | Off by default. When enabled, a confirmation names `api.ipify.org` and `1.1.1.1` before either is contacted. |
| Your IP & DNS → Local | Reads LAN and tunnel-interface addresses from `ifconfig` off the UI thread. No exit IP is fetched and no network is contacted. |
| Your IP & DNS → Check public IP | Contacts an address service — IPinfo when `IPINFO_API_KEY` is set, otherwise `ipapi.co` — for the exit IP, location and network owner. A VPN/proxy/hosting flag appears only with an IPinfo key on a plan that returns it. Once run, it refreshes automatically after each connect/disconnect this session. |
| Your IP & DNS → Run test | Runs a real DNS-leak test through `bash.ws`: resolves probe hostnames with the configured resolvers and reports which resolvers answered and whether any leave the tunnel's network. |
| Safe Action Preview | Choose Connect, Disconnect, or Restart and inspect a non-executing plan for a WireGuard profile. OpenVPN, unknown protocols, and unsafe or missing interface names produce no command. |
| Inspect config… | Choose one local WireGuard configuration for private-key-free routing and DNS inspection. No model, network request, or configuration change is involved. |
| Ask Advisor / Build Config / Stop | LLM answer · offline render · cancel while active. Use the shared Help button for docs. |

## Outputs
Tabs: **Diagnostics** (structured connection and profile-comparison cards), **Advisor** (LLM answer), **Config & Commands** (rendered configs + runbook), **Action Preview** (display-only change plan), and **Config Inspection** (non-secret file summary). The deployment setup is passed to the advisor as context, so a question inherits the mode/protocol/host you picked.

## How it works
- `VpnAgent.build_messages(prompt)` → system prompt + the context-prefixed question; runs through `ChatWorker` like the other agents.
- `build_configs(mode, protocol, server_host, ssh_user, lan_subnet, egress_iface)` returns the whole config + runbook as text — pure string assembly, so it is instant and safe to run offline.
- `load_vpn_profile_catalog()` reads the live companion profile file when present and otherwise reads its bundled seed. It never seeds, edits, or changes the active profile, and only carries the non-secret name, endpoint, port, interface, notes, and protocol fields into Sentinel.
- `collect_vpn_diagnostics(include_external=False, selected_profile=...)` coordinates the companion app's status helpers in a background worker. It compares the selected interface and handshake/route expectations with observed state, while checking that the saved endpoint and port are usable profile values. It does not query or verify the live peer endpoint. The default path performs only local reads. External IP/latency calls cannot run unless the user selects the option and confirms it.
- `build_vpn_action_preview(action, profile, report)` validates the interface and returns sections and copyable commands. It has no subprocess or privileged execution path.
- `inspect_wireguard_config(path, report)` calls the companion-owned parser, receives only its non-secret summary, and cautiously compares intended full/split routing and DNS with the most recent Connection Check. It does not resolve or contact the saved endpoint.
- `VpnPanel.shutdown()` cancels an active diagnostics worker and waits for it to finish before the panel is destroyed. Normal app close and Portable Emergency Reset both use the same shutdown path, preventing an in-flight check from outliving the UI.
- Packaged builds include the non-secret starter profile catalog. If no live companion profile file exists, Tunnel can still open with the same safe baseline instead of depending on source-tree files.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/vpn_agent/sentinel_chat_agent.py` | Shared `SYSTEM_PROMPT`, `build_configs()` helpers, and `VpnAgent`; this is the single implementation used by both products. |
| `agents/vpn_agent/services/` | Existing profile, WireGuard, DNS, public-IP, latency and connection helpers owned by the standalone companion. |
| `agents/vpn_agent/services/config_inspection.py` | Canonical bounded parser that discards private and pre-shared key values before returning a summary. |
| `services/vpn_diagnostics.py` | Sentinel-specific read-only orchestration, selected-profile comparison, recommendations, and non-executing action previews. |
| `ui/workers.py: VpnDiagnosticsWorker` | Keeps system and optional network checks off the UI thread and supports cancellation. |
| `ui/panels/vpn.py` | Workflow chooser, panel, results, and request lifecycle. |
| `ui/dialogs.py: shutdown_panels()` and `main.py: closeEvent()` | Share orderly worker shutdown between Portable Emergency Reset and normal app close. |
| `services/database.py: _seed_default_agents()` | Registers the `vpn` agent row. |
| `Sentinel.spec` | Bundles the starter profile catalog into frozen releases. |

## Extend it
- **Real keys**: swap the placeholder key material for locally-generated X25519 keys (the standalone VPN Agent does this via `cryptography`); keep them out of chat logs.
- **More topologies**: add site-to-site or multi-peer variants to `build_configs()`.
- **OpenVPN inspection**: add an equivalent secret-filtering adapter before accepting OpenVPN files; the current inspector is WireGuard-only.
- **Controlled execution**: if execution is added later, keep preview, explicit confirmation, administrator authorization, rollback and post-change verification as separate gates.

## Verification state

- 26 focused Tunnel tests cover diagnostics, profile filtering/comparison,
  action-preview validation, cancellation and shutdown behavior.
- The Learning Centre screenshots are generated from isolated sample state; the
  capture script never reads or displays the user's real profile names.
- Source and packaged-app paths are both covered: the companion remains the
  implementation owner, while Sentinel carries only the bundled non-secret
  starter catalog needed when no live companion state exists.

## Diagnostic boundaries

- A detected interface or OpenVPN process is evidence that software is active,
  not proof that every application is routed through it.
- The configured DNS-server list is useful context, not a complete DNS-leak test.
- Public IP and latency reveal connectivity only. They do not prove anonymity,
  correct firewall policy, or protection against application-level leaks.
- Connection Check never deploys, connects, disconnects, edits routes, changes
  firewall rules, or reads a WireGuard private key.

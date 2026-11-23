# Audit: kill switch blocks LAN DNS, Connect review warns on system DNS (2026-11-23)

Spec slug `readiness-h1-h2-killswitch-dns` · Foundry phases Build (5), Review (6), Secure (8) ·
Tier C (VPN Agent rules). Branch `claude/great-swanson-f4b56c`.

- **Owner decision:** 2026-11-23 (`docs/foundry/approvals.jsonl`): "Block LAN DNS by default (Recommended)".
- **Connect-review warning (H2):** proposed alongside that decision, not separately confirmed by the owner.
- **Reviewer:** an independent Opus security-engineer agent that is not the author. It worked read-only and ran no pf, sudo or network commands.

## What changed
- **pf rules** (`agents/vpn_agent/services/killswitch.py`, `build_rules`, `arm`):
  - With the LAN kept open, `block drop out quick` rules drop port 53 (UDP and TCP) to 192.168/16, 10/8, 172.16/12, 169.254/16 and fe80::/10.
  - They sit after the tunnel's `pass quick on utunN` and before the LAN passes, so a lookup inside the tunnel still passes, even to a private resolver such as 10.64.0.1.
  - `allow_lan_dns=True` leaves them out, and only a real `True` counts.
  - The grammar root loads gained two fixed shapes, and their range must be one of `LAN_RANGES`.
  - The arm marker records `lan_dns`, and `armed_lan_dns()` reads it back.
- **Setting** (`services/vpn_connection.py`):
  - "Allow local network DNS" is stored as `killswitch_allow_lan_dns` in the settings table.
  - Only the stored value `"1"` means on, and a failed read is off (fail closed).
  - `arm_killswitch` passes the setting for WireGuard and for OpenVPN.
- **Connect review** (`services/vpn_execution.py`):
  - It shows a "System DNS stays in use" warning for WireGuard without `DNS =` and for every OpenVPN config. For OpenVPN it notes an `up`, `--up` or `route-up` script that may set DNS.
  - It always shows a "Kill switch, local network DNS (port 53)" line. When the armed rules differ from the tick, or predate it, the line says "Arm again".
- **UI** (`ui/panels/vpn.py`):
  - New tick under the kill switch.
  - The Arm dialog states which way the tick is set.
  - The tooltip no longer says "cannot leak". It names the DNS exception and that OpenVPN's device is exempt too.
  - A save that fails springs the tick back and says so.
  - Changing the tick while armed says "press Arm again".

## Findings and outcome
| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | P2 | Changing the tick while armed made the review and the tick misstate the live rules (marker did not record the mode) | **Fixed**: marker records `lan_dns`; review line and panel say "Arm again" on a mismatch or an older record; tests `test_the_armed_record_says_…`, `test_the_review_says_when_the_armed_switch_differs_…`, `test_changing_the_tick_while_armed_says_arm_again` |
| 2 | P3 | pf states created before a re-arm (allowed to blocked) survive; UDP about 60 s, a TCP DNS flow longer | **Documented** in the tooltip ("lookups already in flight may finish"); state kill deferred to TODO (it would need to target port 53 only, not all LAN states) |
| 3 | P3 | `--up` spelling missed by the up-script hint | **Fixed** (directive key normalised; `route-up` counted); parametrised test |
| 4 | P3 | Warning too broad for a public system resolver (full or split tunnel) | **Fixed** wording: "If one is on your local network…"; "A public DNS server goes through the tunnel only if the tunnel routes its address" |
| 5 | P3 | DNS over TLS/HTTPS to a LAN resolver is not port 53 | **Documented** ("port 53 only") in the tooltip and review line; blocking 853 left to TODO as an owner choice |
| 6 | P3 | Mutations that would survive: `is True` in the review, the up-script branch, the dialog text when ticked | **Fixed**: tests for a truthy non-bool tick, the up-script hint and the ticked dialog |

The reviewer checked these and found them correct:
- **Rule order.** Public, IPv6 ULA and global resolvers are left to `block drop all`. The endpoint pass deliberately comes first.
- **pf grammar.** Checked by reading only: `pfctl -n` was not run.
- **Closed allow-list.** The new shapes can only block.
- **Fail-closed reads.**
- **The UI's error path.**

## Evidence
- **New tests:** `tests/test_killswitch_lan_dns.py` (all mocked). A small evaluator applies pf's first-quick-match order to the generated text and covers:
  - LAN DNS dropped over UDP and TCP, IPv4 and link-local IPv6.
  - The rest of the LAN open.
  - mDNS open.
  - Tunnel DNS passing.
  - Tunnel down.
  - The grammar refusing variants.
  - The arm record and the stored tick, including failure cases.
- **Extended tests:** review-gate tests in `tests/test_vpn_review_gate.py`; panel tests in `tests/test_ui_panels.py`.
- **Mutation check (manual):** each of these mutations failed at least one test:
  - Removing the DNS block loop.
  - Moving the DNS blocks after the LAN passes.
  - Changing `is True` to `bool()` in `arm`.
  - Making `killswitch_allow_lan_dns` default to on.
  - Dropping `dnsnet` validation.
- **Full suite** (offscreen, this worktree, `bug_spray` borrowed read-only from the main checkout): 4375 passed, 14 skipped, 3 failed. All 3 failures come from the empty `bug_spray` submodule in a worktree, not from this change:
  - `test_agent_roster` checks the folder on disk.
  - Two `test_quit_bounded` cases import `main.py` in a subprocess.

## Not verified
- **Live pf on macOS:** parsing (`pfctl -n`), enforcement and state lifetimes. Not run on this machine, by rule. This belongs to the VM or throwaway-network check in the testing phase.
- **Real Mullvad or OpenVPN DNS behaviour.**

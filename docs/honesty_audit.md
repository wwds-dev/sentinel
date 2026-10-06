# Sentinel — honesty audit and remediation

_Audit date: 2026-09-21 · Scope: does every agent/tool really do what it claims,
or only appear to? Method: per-subsystem claim-vs-code verification with an
adversarial re-check pass, plus independent spot-checks._

## Current verification — 2026-09-22

The original audit below is historical; its preview-only Tunnel verdict was
superseded by the real connection implementation. The checkout already contains
Bloodhound dispatch, Anthropic/Kimi usage, Beacon scanning and Forge wording fixes.
Baseline automated verification: **608 tests passed**. After the corrections,
**619 tests passed** in the full suite; the final targeted VPN run passed **29
tests**, including four additional invalid-PID cases. These tests use simulated
provider/VPN results and do not establish successful external operations.

Follow-up corrections:

- Tunnel starts in **Connection state not checked**, rather than claiming it is
  disconnected without a measurement. Successful commands explicitly leave
  connection state and traffic protection **unverified**. Starting a daemon or
  creating an interface does not prove a handshake, correct routing or leak safety.
- OpenVPN no longer falls back to stopping every process named `openvpn`.
  Missing, malformed, or unverified tracking refuses to stop anything. A tracked
  process must have the OpenVPN executable name, Sentinel daemon name and expected
  PID-file argument. Signal failures are reported, not hidden with `|| true`, and
  tracking remains after a termination request. Ambiguous process arguments are
  refused. This is a fail-closed check, not an atomic process-identity guarantee;
  a privileged supervisor remains the stronger long-term design.

**Not yet established:** live WireGuard/OpenVPN connectivity; selected-profile
identity; IPv4/IPv6 and split-route behavior; DNS leak behavior; reconnect and
server-failure behavior; firewall enforcement; packaged-app operation. Test these
on an owned VPN endpoint before relying on Sentinel for protection. No live VPN
or firewall change was made during this follow-up.

Remaining review priorities include privileged command resolution, imported-config
hooks, root-owned runtime files, OpenVPN profile ownership and daemon lifecycle,
stream cancellation accounting, estimate-based budget limits, and complete test
isolation. These are review targets, not claims that each is an exploitable defect.

## Historical initial assessment

No subsystem fabricates data and presents it as live results — the owner's
primary fear (e.g. "a tunnel that only pretends to tunnel") is **not** present in
the data layers. The real machinery is real: OSINT providers hit real sources,
file search reads real metadata, VPN diagnostics read real system state, model
clients call real endpoints, and the permission/budget guard genuinely blocks.
The defects were in **wiring, labelling and one billing path** — features that
looked like they did more than they did. Most are fixed (see below).

## Per-subsystem verdict

| Subsystem | Verdict | Notes |
|---|---|---|
| File discovery (local + SSH) | Does what it claims | Metadata-only, read-only, no symlink follow, strict SSH `RejectPolicy` (fail-closed), no creds stored, nothing sent to AI. Cleanest subsystem. |
| OSINT providers (Trace + lookups) | Real | Live WHOIS/DNS/crt.sh/emailrep/HIBP/BreachDirectory/urlscan/GLEIF; honest failure; no invented findings. |
| Trust backbone (guard/cost/clients) | Real, with one billing bug (fixed) | Guard blocks on both paths; clients hit real endpoints; Kimi cache math correct. Streaming billed an estimate (fixed). |
| Tunnel / VPN | Connection control added after initial audit | Executes VPN commands; traffic protection remains unverified. See current verification above. |
| Bug Spray | Honest but limited | Prompt+report builder; Nmap really runs; "in-scope" not enforced (now captioned). |
| Forge (agent factory) | Safe, but messaging oversold (fixed) | Generated code cannot run (no dynamic loader). Dialog/messages corrected. |
| Beacon / Wi-Fi | Two flagship modes were dead on macOS 14.4+ (fixed) | `airport` removed by Apple; replaced with `system_profiler`. |

## Findings and remediation

### Fixed in this session

1. **Bloodhound live collection silently no-opped for the two most common target
   types.** The panel passed combobox labels ("Email Address", "Domain / IP",
   "Auto-detect") verbatim while the dispatcher matched short tokens
   ("email", "domain"). Fixed with a label→token normaliser and auto-detect in
   [agents/osint_heavy_agent/__init__.py](../agents/osint_heavy_agent/__init__.py);
   `build_messages` is now offline and a separate `collect_live()` runs the
   lookups. Organisation targets now use the GLEIF company provider instead of
   WHOIS-on-a-company-name. Tests: [tests/test_osint_heavy_collection.py](../tests/test_osint_heavy_collection.py).

2. **Bloodhound Threat/Confidence/Sources gauges presented model-invented
   numbers as measurements.** "Sources" now shows the real count of public
   sources actually contacted (`real_source_count`), falling back to the scraped
   value only when no live collection ran; Threat/Confidence are relabelled
   "(AI estimate)" with a caption. [ui/panels/osint_heavy.py](../ui/panels/osint_heavy.py).

3. **Streaming requests billed a char/4 estimate, not real tokens — and the
   budget cap enforced against that estimate.** The Anthropic and Kimi streaming
   clients now surface the provider's real token usage after the text stream;
   the worker captures it (falling back to the estimate only when no counts are
   reported). This also makes the Kimi cached-input discount apply on the
   streaming path. [services/anthropic_client.py](../services/anthropic_client.py),
   [services/kimi_client.py](../services/kimi_client.py), [ui/workers.py](../ui/workers.py).
   Tests: [tests/test_streaming_usage.py](../tests/test_streaming_usage.py).
   _Note: OpenAI/DeepSeek/Qwen/Gemini streaming still estimate; same pattern applies._

4. **Beacon "Scan Networks"/"Signal Monitor" were dead on macOS 14.4+** (they
   called the removed `airport` binary). Replaced with `system_profiler
   SPAirPortDataType -json`, parsed into a readable report (current + nearby
   networks, signal, security). macOS no longer exposes per-network BSSIDs to
   unprivileged tools, and the report says so. The USB adapter VID/PID matcher
   was also fixed (system_profiler appends the vendor name to the id, so no
   adapter matched). [agents/wifi_agent/__init__.py](../agents/wifi_agent/__init__.py),
   [ui/panels/wifi.py](../ui/panels/wifi.py). Tests: [tests/test_wifi_scan.py](../tests/test_wifi_scan.py).
   README claim corrected in [agents/wifi_agent/README.md](../agents/wifi_agent/README.md).

5. **Honest UI captions added** where the interface implied more than the code
   does: a Tunnel banner ("does not route or protect your traffic"), a Bug Spray
   note ("Program/Scope are declared by you and not enforced"; Nmap "runs on your
   machine … outside the guard"), and corrected Forge dialog/log text (it writes
   a disabled scaffold + SQLite rows, not `config/*.json`, and cannot be
   "activated" by restart). [ui/panels/vpn.py](../ui/panels/vpn.py),
   [ui/panels/bug_bounty.py](../ui/panels/bug_bounty.py), [ui/panels/manager.py](../ui/panels/manager.py).

6. **File-discovery wording fixed** so remote SSH mode no longer says "locally".
   [ui/panels/osint_heavy.py](../ui/panels/osint_heavy.py).

### Not yet addressed (recommended follow-ups)

- OpenAI/DeepSeek/Qwen/Gemini streaming still bill the char/4 estimate — apply
  the same usage-sentinel pattern.
- Bloodhound has no per-source activity log like Trace; add one so "0 sources
  contacted" is visible during a run, not only at the end.
- `ui/panels/base.py` exposes a `run_backend()` convenience that skips
  `authorize()` (tests only today) — remove or route through the guard.
- Dead code: `main.py check_budget_before_request` (never called);
  `essid_val` in the Kali builder.
- Forge writes a scaffold `.py` that nothing ever loads; either add a real
  registry-driven loader or stop writing the file.
- Pre-existing, unrelated to this audit: 2 failing tests
  (`test_ui_panels.py::TestRecommendationsStillReachThePanels[osint]`) — the
  recommended model for the `osint` agent isn't in its model list.

## VPN reality — Path B implemented (real VPN client)

At audit time Sentinel's Tunnel was preview-only and did not route traffic. That
has now been changed on request: Tunnel is a **real VPN client**.

- New service layer that actually connects/disconnects:
  [services/vpn_connection.py](../services/vpn_connection.py) (WireGuard + OpenVPN
  dispatch, refuses placeholder profiles) and
  [services/openvpn_manager.py](../services/openvpn_manager.py) (new OpenVPN
  client — only WireGuard connect existed before). All privileged execution goes
  through `privileged.run_as_root` (macOS authorisation dialog) and is injectable,
  so tests never run real `sudo`. Tests: [tests/test_vpn_connection.py](../tests/test_vpn_connection.py).
- Panel: a "VPN Connection (live)" group with a server picker, **Connect /
  Disconnect** (explicit confirm + admin prompt, run off the UI thread via
  `VpnConnectionWorker`), **Import config…** for `.conf`/`.ovpn`, and status.
  The banner now states it runs a real tunnel. [ui/panels/vpn.py](../ui/panels/vpn.py).
- "A few countries": honest country-labelled **templates** (`example_country_profiles`)
  that are explicit placeholders — the connect layer refuses them until you import
  a real config or set a real endpoint. No fabricated foreign servers.

**Honest limitations / follow-ups for this feature:**

- Real foreign exits require real configs: import a provider's `.conf`/`.ovpn`,
  or provision your own server (the submodule's `server/*` provisioning does this
  on a VPS you rent). Sentinel cannot conjure working servers in other countries.
- The pf **kill switch** works in the standalone app but its module uses an
  absolute `from server import paths` that only resolves once the VPN code is
  merged into Sentinel's tree; until then `arm/disarm` degrade gracefully with a
  clear message. Same for `profile_store`. This is the remaining reason to do the
  de-submodule merge.
- OpenVPN support is a real but basic client (daemonised, pid/log tracked); auth
  prompts, pushed-DNS handling and live connection-status polling are follow-ups.
- Connect/Disconnect updates status from the tool's result; there is no live
  status poll yet.

## Standalone vpn_agent: merge/delete analysis

`agents/vpn_agent` is a **git submodule** (github.com/Netrunner3000/vpn_agent) and
also ships a committed `.venv` (bloat). Sentinel imports only a small part of it:

- `sentinel_chat_agent` (`VpnAgent`, `build_configs`) — main.py, Tunnel panel
- `services.config_inspection`, `services.{dns_check, latency, public_ip, wireguard_manager}` — diagnostics
- `server.paths`

It does **not** import `app/*`, `main.py`, most of `server/*`, or the extra
`services/*` (killswitch, privileged, tor, proxychain, socks_client, health_monitor,
macaddr, profile_store). So it cannot simply be deleted — but it can be collapsed
into a small in-repo library. The right merge scope depends on whether Tunnel
should stay preview-only or become a real connect/disconnect controller (which
would keep `wireguard_manager`/`killswitch`/`privileged` and wire execution +
privilege + kill-switch into the panel). That is a deliberate capability decision.

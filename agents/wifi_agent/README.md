# BEACON — Wi-Fi reconnaissance & Kali command builder

_One of Sentinel's built-in agents (`~/Documents/lab/active/sentinel/agents/wifi_agent/`). Split out into its own project on 2026-09-14 — see the parent project's README.md for how Sentinel's agent roster fits together._

`key: wifi` · class: `agents/wifi_agent/__init__.py → WiFiAgent` · panel: `ui/panels/wifi.py → WifiPanel`

> ⚠️ Only test networks you own or have written authorisation to assess.

## What it does
Two capabilities in one panel:
1. **Live macOS diagnostics** — uses the Mac's built-in Wi-Fi for interface, nearby-network, signal and reachability checks via `system_profiler SPAirPortDataType` (`networksetup` for interfaces, `ping` for reachability). No external adapter and no `sudo` are required. Note: macOS no longer exposes per-network BSSIDs or a full active RSSI sweep to unprivileged tools, so scans show SSID, channel, signal (where macOS reports it) and security, but not BSSID. The obsolete `airport` binary (removed by Apple in macOS 14.4) is no longer used.
2. **Kali lab planning** — detects supported USB Wi-Fi adapters and generates reviewable **Kali Linux** command sequences for authorised testing. Actual monitor/injection work generally runs in Kali and requires a compatible external adapter, driver and USB passthrough when Kali is virtualised. Sentinel does not execute these sequences.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Mode | `Interface Info` · `Scan Networks` · `Signal Monitor` · `Ping Test` · `Kali Command Builder`. |
| Interface | Network interface (e.g. `en0`). |
| Target Host | Used by Ping Test. |
| Kali sub-form (hidden unless Kali mode) | Operation (`Handshake Capture` / `Deauth Attack` / `WPS Audit` / `PMKID Attack`), Adapter, BSSID, Channel, ESSID. |
| AI interpretation | Optional collapsed section, off by default. Enable it to send subprocess output to the selected LLM. |
| Detect Adapters | Scan USB for known adapters. |
| Run Preflight | Read interfaces, default route and known USB adapters; assign suggested roles and show connection risks without changing anything. |
| Run / Stop | Execute, or cancel while work is active. Results reveal Save and Clear controls. Use the shared Help button for docs. |

## Outputs
Results appear as readable cards. Local commands show their raw findings;
optional AI interpretation is split into **Summary**, **Network Findings**,
**Security Observations** and **Recommendations**; Kali planning shows a
reviewable command sequence. Raw model text stays available behind a collapsed
disclosure. The side indicators show adapter, chipset, monitor/injection
capabilities, signal and security.

## How it works
- `detect_usb_adapters()` parses `system_profiler SPUSBDataType -json` against `KNOWN_ADAPTERS` (VID/PID → chipset, monitor/inject support, Kali iface, driver notes).
- `network_interface_status()` and `build_connection_preflight()` identify the routed internet/control interface and dedicated monitor adapter. They are read-only.
- `build_kali_commands(operation, adapter, bssid, channel, essid)` returns a numbered, commented command block; refuses injection ops on adapters that can't inject.
- Live modes run via `SubprocessWorker` (QThread). AI Analysis routes raw output through `ChatWorker` + `WiFiAgent.build_messages()`.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/wifi_agent/__init__.py` | `KNOWN_ADAPTERS`, `AIRPORT` path, `detect_usb_adapters()`, `build_kali_commands()`, `WiFiAgent`. |
| `ui/panels/wifi.py` | Panel and dispatch by Mode (subprocess vs Kali build vs optional AI). |
| `main.py: SubprocessWorker` | Runs shell commands off the UI thread. |

## Extend it
- **Add an adapter**: add a `(vid, pid): {...}` entry to `KNOWN_ADAPTERS`.
- **Add a Kali operation**: add a branch in `build_kali_commands()` (respect the `inject` capability check).
- **Add a live mode**: add a Mode option and a subprocess command in `wifi_run()`.

## Requirements
macOS with `system_profiler`, `networksetup`, `ping` and `route` (all built in; no `sudo`). Kali commands assume Kali plus a compatible external adapter (TL-WN722N, AWUS036ACH, or TL-WN725N V3). A single adapter in monitor mode cannot remain an ordinary managed Wi-Fi connection. Keep built-in Wi-Fi or Ethernet for internet/control and dedicate the USB adapter to Kali monitor mode. Passing USB through to a VM detaches it from macOS; success depends on the hypervisor, guest driver and chipset. AI Analysis needs a provider key.

# BEACON — Wi-Fi reconnaissance & Kali command builder

`key: wifi` · class: `agents/wifi_agent/__init__.py → WiFiAgent` · panel: `ui/panels/wifi.py → WifiPanel`

> ⚠️ Only test networks you own or have written authorisation to assess.

## What it does
Two capabilities in one panel:
1. **Live macOS diagnostics** — uses the Mac's built-in Wi-Fi for interface, nearby-network, signal and reachability checks. No external adapter is required.
2. **Kali lab planning** — detects supported USB Wi-Fi adapters and generates reviewable **Kali Linux** command sequences for authorised testing. Actual monitor/injection work generally runs in Kali and requires a compatible external adapter, driver and USB passthrough when Kali is virtualised. Sentinel does not execute these sequences.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Mode | `Interface Info` · `Scan Networks` · `Signal Monitor` · `Ping Test` · `Kali Command Builder`. |
| Interface | Lists interface names (e.g. `en0`). **Known limitation:** no mode reads this selection — Interface Info, Scan Networks, Signal Monitor and Ping Test all run their fixed command regardless of what is chosen here. |
| Target Host | Used by Ping Test. |
| Kali sub-form (hidden unless Kali mode) | Operation (`Handshake Capture` / `Deauth Attack` / `WPS Audit` / `PMKID Attack`), Adapter, BSSID, Channel, ESSID. |
| AI interpretation | A plain checkbox, unchecked by default. Enable it to send the raw subprocess output to the selected provider/model. The provider dropdown still defaults to Sentinel's standard provider, which may be a paid cloud model — switch it to a local model yourself before ticking the box if you want this data to stay off the network; nothing redacts it for you. |
| Detect Adapters | Scan USB for known adapters. |
| Run Preflight | Read interfaces, default route and known USB adapters; assign suggested roles and show connection risks without changing anything. |
| Run / Stop | Execute, or cancel while work is active. Save and Clear are visible the whole time; Save becomes enabled once there is output to write. **Known limitation:** there is no Help button on this panel — use this doc instead. |

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
- `build_kali_commands(operation, adapter, bssid, channel, essid)` returns a numbered, commented command block; refuses to generate "Deauth Attack" commands on an adapter without injection support. **Known limitation:** "Handshake Capture" also includes an injection-class step but is generated for any adapter regardless of its inject capability — check the adapter's capability indicator before using it.
- A malformed BSSID, channel or ESSID is not rejected with a message; it is silently swapped for a placeholder (`TARGET_BSSID`, `CHANNEL`, `ESSID`) in the generated command header, so check that header before relying on the fields you typed.
- Live modes run via `SubprocessWorker` (`ui/workers.py`, a `QThread`). AI Analysis routes raw output through `ChatWorker` + `WiFiAgent.build_messages()`.
- Kali Command Builder re-runs Connection Preflight on every click of Run, even if you already ran it yourself; Save (in Kali mode) stores only the generated commands, not the Preflight text shown above them.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/wifi_agent/__init__.py` | `KNOWN_ADAPTERS`, `detect_usb_adapters()`, `build_kali_commands()`, `network_interface_status()`, `build_connection_preflight()`, `WiFiAgent`. |
| `ui/panels/wifi.py` | `WifiPanel`, its own `KALI_ADAPTERS` dict, and dispatch by Mode (subprocess vs Kali build vs optional AI). |
| `ui/workers.py` | `SubprocessWorker` — runs shell commands off the UI thread. |

## Extend it
- **Add an adapter**: add a `(vid, pid): {...}` entry to `KNOWN_ADAPTERS` (`agents/wifi_agent/__init__.py`, used for detection) **and** a matching entry to `KALI_ADAPTERS` (`ui/panels/wifi.py`, used by the Kali dropdown) — the two dicts are maintained separately and nothing keeps them in sync.
- **Add a Kali operation**: add a branch in `build_kali_commands()`. If it is injection-based, add its own explicit `inject` capability check — follow the "Deauth Attack" branch as the model; "Handshake Capture" is the counter-example that currently skips this check.
- **Add a live mode**: add a Mode option and a subprocess command in `run()` (`ui/panels/wifi.py`).

## Requirements
macOS tools already on the system: `system_profiler` (Wi-Fi and USB scans), `networksetup` (interface list), `route` (default-route lookup) and `ping`. No external adapter is required for the four live diagnostic modes, and no `airport` binary is used — Apple removed it in macOS 14.4 and Beacon's scans run on `system_profiler` alone. Kali commands assume Kali plus a compatible external adapter (TL-WN722N, AWUS036ACH, or TL-WN725N V3). A single adapter in monitor mode cannot remain an ordinary managed Wi-Fi connection. Keep built-in Wi-Fi or Ethernet for internet/control and dedicate the USB adapter to Kali monitor mode. Passing USB through to a VM detaches it from macOS; success depends on the hypervisor, guest driver and chipset. AI Analysis needs a provider key.

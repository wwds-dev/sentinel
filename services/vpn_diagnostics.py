"""Read-only connection diagnostics for Sentinel's Tunnel panel.

The operational implementation remains in the nested ``vpn_agent`` project.
This module only coordinates its existing status helpers into a small,
structured report that Sentinel can render.  It never connects, disconnects,
changes routes, edits firewall rules, or reads private key material.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable

from agents.vpn_agent.services.config_inspection import (
    WireGuardConfigSummary,
    inspect_wireguard_config as inspect_companion_wireguard_config,
)
from agents.vpn_agent.server import paths as vpn_paths
from agents.vpn_agent.services import dns_check, latency, public_ip, wireguard_manager
from services import vpn_apps
from services.runtime_paths import resource_base


CancelCheck = Callable[[], bool]
PROFILE_SEED = (
    resource_base()
    / "agents"
    / "vpn_agent"
    / "config"
    / "vpn_profiles.json"
)
SAFE_INTERFACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=-]{0,14}$")
SUPPORTED_PREVIEW_ACTIONS = ("Connect", "Disconnect", "Restart")
PUBLIC_PROFILE_FIELDS = ("name", "endpoint", "port", "interface", "notes", "protocol")


def _never_cancel() -> bool:
    return False


def _public_profile(profile: dict) -> dict:
    """Keep only fields needed for comparison; discard keys and other secrets."""
    return {
        field: profile[field]
        for field in PUBLIC_PROFILE_FIELDS
        if field in profile
    }


def _profile_protocol(profile: dict) -> str:
    raw = profile.get("protocol")
    if raw is None or not str(raw).strip():
        return "WireGuard"  # legacy profile schema had no protocol field
    compact = re.sub(r"[^a-z0-9]", "", str(raw).lower())
    if "wireguard" in compact:
        return "WireGuard"
    if "openvpn" in compact:
        return "OpenVPN"
    return "Unknown"


@dataclass(frozen=True)
class VpnProfileCatalog:
    """Read-only view of the companion VPN Agent's profile catalog."""

    profiles: tuple[dict, ...]
    active_profile: str | None
    source: str
    error: str = ""


def load_vpn_profile_catalog() -> VpnProfileCatalog:
    """Load live VPN Agent profiles without seeding or changing their state."""
    live_path = vpn_paths.profiles_file()
    source_path = live_path if live_path.is_file() else PROFILE_SEED
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("profile catalog must be a JSON object")
        raw_profiles = data.get("profiles", [])
        if not isinstance(raw_profiles, list):
            raise ValueError("profile catalog 'profiles' must be a list")
        profiles = tuple(
            _public_profile(profile)
            for profile in raw_profiles
            if isinstance(profile, dict)
        )
        active = data.get("active_profile")
        return VpnProfileCatalog(
            profiles=profiles,
            active_profile=str(active) if active else None,
            source=str(source_path),
        )
    except (OSError, ValueError, TypeError) as exc:
        return VpnProfileCatalog((), None, str(source_path), str(exc))


@dataclass(frozen=True)
class DiagnosticFinding:
    level: str
    title: str
    detail: str
    next_step: str = ""

    def line(self) -> str:
        text = f"{self.level.upper()} — {self.title}: {self.detail}"
        return f"{text}\nNext: {self.next_step}" if self.next_step else text


@dataclass(frozen=True)
class VpnActionPreview:
    """A display-only plan. No command in this object is executed by Sentinel."""

    action: str
    profile_name: str
    interface: str
    commands: tuple[str, ...]
    effects: tuple[str, ...]
    checklist: tuple[str, ...]
    error: str = ""

    @property
    def valid(self) -> bool:
        return not self.error and bool(self.commands)

    def sections(self) -> list[tuple[str, str, bool]]:
        if self.error:
            return [
                ("Preview unavailable", self.error, False),
                (
                    "Safety boundary",
                    "Nothing was executed. Tunnel previews commands but never runs them.",
                    False,
                ),
            ]
        return [
            (
                "Preview only",
                f"{self.action} profile '{self.profile_name}' on interface "
                f"'{self.interface}'. Nothing has been executed.",
                False,
            ),
            ("Expected changes", "\n".join(f"• {item}" for item in self.effects), False),
            ("Check before running", "\n".join(f"• {item}" for item in self.checklist), False),
            ("Proposed commands", "\n".join(self.commands), True),
            (
                "Safety boundary",
                "This screen is a copyable plan, not an execution control. It does not "
                "request administrator access or change the tunnel, routes, DNS, or firewall.",
                False,
            ),
        ]

    def as_text(self) -> str:
        lines = [f"Tunnel action preview · {self.action}"]
        for title, body, _mono in self.sections():
            lines.extend(("", title, body))
        return "\n".join(lines)


@dataclass(frozen=True)
class VpnConfigInspection:
    """Non-secret config summary plus cautious comparison with a live snapshot."""

    summary: WireGuardConfigSummary
    comparison: tuple[DiagnosticFinding, ...]

    def sections(self) -> list[tuple[str, str, bool]]:
        summary = self.summary
        overview = [
            f"File: {summary.source_name}",
            f"Interface name: {summary.interface_name or 'Not available'}",
            f"Intended routing: {summary.route_mode}",
            f"Interface addresses: {', '.join(summary.addresses) or 'Not specified'}",
            f"DNS: {', '.join(summary.dns_servers) or 'Not specified'}",
            f"Peers: {summary.peer_count}",
        ]
        if summary.mtu:
            overview.append(f"MTU: {summary.mtu}")
        routing = [
            "Endpoints: " + (", ".join(summary.endpoints) or "Not specified"),
            "Allowed IPs: " + (", ".join(summary.allowed_ips) or "Not specified"),
        ]
        sections: list[tuple[str, str, bool]] = [
            ("Configuration summary", "\n".join(overview), True),
            ("Peer routing", "\n".join(routing), True),
        ]
        if self.comparison:
            sections.append((
                "Comparison with latest Connection Check",
                "\n\n".join(item.line() for item in self.comparison),
                False,
            ))
        if summary.warnings:
            sections.append(("Values needing attention", "\n".join(summary.warnings), False))
        sections.append((
            "Privacy boundary",
            f"{summary.secrets_discarded} private or pre-shared key field(s) were "
            "discarded while reading. Tunnel keeps only this non-secret summary; "
            "the original configuration is not copied into chat, logs, or results.",
            False,
        ))
        return sections

    def as_text(self) -> str:
        lines = [f"Tunnel configuration inspection · {self.summary.source_name}"]
        for title, body, _mono in self.sections():
            lines.extend(("", title, body))
        return "\n".join(lines)


def inspect_wireguard_config(
    path: str | Path,
    report: "VpnDiagnosticsReport | None" = None,
) -> VpnConfigInspection:
    """Inspect one user-selected config without retaining private key material."""
    summary = inspect_companion_wireguard_config(path)
    findings: list[DiagnosticFinding] = []
    if report is None:
        findings.append(DiagnosticFinding(
            "info",
            "No current snapshot",
            "The configuration was inspected safely, but no Connection Check result is available.",
            "Run Connection Check to compare its route and DNS intent with this Mac.",
        ))
        return VpnConfigInspection(summary, tuple(findings))

    default_interface = str(report.route.get("interface") or "Unknown")
    if summary.route_mode == "Full tunnel":
        if default_interface in report.active_tunnels:
            findings.append(DiagnosticFinding(
                "ready", "Full-tunnel route is plausible",
                f"The current default interface {default_interface} is a detected WireGuard interface.",
            ))
        else:
            findings.append(DiagnosticFinding(
                "check", "Full-tunnel route is not visible",
                f"The configuration includes a default route, but the current default interface is {default_interface}.",
                "Connect through your trusted VPN client, then run a fresh Connection Check.",
            ))
    elif summary.route_mode == "Split tunnel":
        findings.append(DiagnosticFinding(
            "info", "Split-tunnel routing",
            "The configuration routes only the listed networks, so the ordinary default interface may remain unchanged.",
        ))
    else:
        findings.append(DiagnosticFinding(
            "check", "No routing intent found", "No AllowedIPs value was found in a peer section.",
            "Review the peer's AllowedIPs before using this configuration.",
        ))

    if summary.dns_servers:
        overlap = sorted(set(summary.dns_servers) & set(report.dns_servers))
        if overlap:
            findings.append(DiagnosticFinding(
                "ready", "Configured DNS is visible",
                "The current DNS list includes: " + ", ".join(overlap),
            ))
        else:
            findings.append(DiagnosticFinding(
                "check", "Configured DNS is not visible",
                "None of the DNS values in this configuration appear in the current system DNS list.",
                "Confirm the tunnel is active and inspect DNS in the VPN client before treating this as a leak.",
            ))

    profile = report.selected_profile or {}
    profile_endpoint = str(profile.get("endpoint") or "").strip()
    profile_port = str(profile.get("port") or "").strip()
    if profile_endpoint and profile_port and summary.endpoints:
        expected = f"{profile_endpoint}:{profile_port}"
        if expected in summary.endpoints:
            findings.append(DiagnosticFinding(
                "ready", "Profile endpoint matches the file",
                "The selected profile and inspected configuration contain the same saved endpoint value.",
            ))
        else:
            findings.append(DiagnosticFinding(
                "check", "Profile endpoint differs from the file",
                "The selected profile's saved endpoint is not present in this configuration.",
                "Confirm that the profile and file describe the same tunnel. This does not query the live peer.",
            ))
    return VpnConfigInspection(summary, tuple(findings))


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _format_age(timestamp: int | None) -> str:
    if not timestamp:
        return "no completed handshake"
    seconds = max(0, int(time.time()) - timestamp)
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _run(command: list[str], timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _default_route() -> dict[str, str]:
    route = shutil.which("route")
    if route is None and Path("/sbin/route").is_file():
        route = "/sbin/route"
    if route is None:
        return {"interface": "Unknown", "gateway": "Unknown", "error": "route tool not found"}
    try:
        proc = _run([route, "-n", "get", "default"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"interface": "Unknown", "gateway": "Unknown", "error": str(exc)}
    values: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, separator, value = line.strip().partition(":")
        if separator and key in {"interface", "gateway"}:
            values[key] = value.strip()
    error = proc.stderr.strip() if proc.returncode else ""
    return {
        "interface": values.get("interface", "Unknown"),
        "gateway": values.get("gateway", "Unknown"),
        "error": error,
    }


def _openvpn_running() -> bool:
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return False
    try:
        return _run([pgrep, "-x", "openvpn"], timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _wg_interfaces() -> tuple[list[str], list[str]]:
    """Return WireGuard interfaces without requesting key material."""
    interfaces = set(wireguard_manager.list_active_tunnels())
    errors: list[str] = []
    if wireguard_manager.is_wg_available():
        try:
            proc = _run(["wg", "show", "interfaces"])
            if proc.returncode == 0:
                interfaces.update(proc.stdout.split())
            elif proc.stderr.strip():
                errors.append(f"WireGuard status: {proc.stderr.strip()}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"WireGuard status: {exc}")
    return sorted(interfaces), errors


def _peer_stats(interface: str) -> tuple[dict[str, int | None], list[str]]:
    """Aggregate peer health without displaying public or private keys."""
    stats: dict[str, int | None] = {
        "peers": 0,
        "latest_handshake": None,
        "received": 0,
        "sent": 0,
    }
    errors: list[str] = []
    try:
        handshakes = _run(["wg", "show", interface, "latest-handshakes"])
        if handshakes.returncode == 0:
            stamps = []
            for line in handshakes.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[1].isdigit():
                    stamps.append(int(fields[1]))
            stats["peers"] = len(stamps)
            stats["latest_handshake"] = max(stamps, default=0) or None
        elif handshakes.stderr.strip():
            errors.append(f"{interface} handshake status: {handshakes.stderr.strip()}")

        transfer = _run(["wg", "show", interface, "transfer"])
        if transfer.returncode == 0:
            for line in transfer.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 3 and fields[1].isdigit() and fields[2].isdigit():
                    stats["received"] = int(stats["received"] or 0) + int(fields[1])
                    stats["sent"] = int(stats["sent"] or 0) + int(fields[2])
        elif transfer.stderr.strip():
            errors.append(f"{interface} transfer status: {transfer.stderr.strip()}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"{interface} status: {exc}")
    return stats, errors


@dataclass
class VpnDiagnosticsReport:
    checked_at: str
    tools: dict[str, str]
    active_tunnels: list[str]
    tunnel_stats: dict[str, dict[str, int | None]]
    route: dict[str, str]
    dns_servers: list[str]
    openvpn_running: bool
    selected_profile: dict | None = None
    profile_findings: list[DiagnosticFinding] = field(default_factory=list)
    vpn_apps: list = field(default_factory=list)
    external_checked: bool = False
    public_ip: str | None = None
    latency_result: dict | None = None
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "Connection check stopped. Partial local results are shown."
        if self.active_tunnels:
            names = ", ".join(self.active_tunnels)
            return f"WireGuard interface detected: {names}. Confirm the route below."
        managed = [a for a in self.vpn_apps if a.active]
        if managed:
            names = ", ".join(f"{a.name} ({a.state})" for a in managed)
            return (f"{names} — managed by that app, not by Tunnel. "
                    "Confirm the exit address under Your IP & DNS.")
        if self.openvpn_running:
            return "An OpenVPN process is running. Confirm the route below."
        return "No active WireGuard interface or OpenVPN process was detected."

    def sections(self) -> list[tuple[str, str, bool]]:
        tool_lines = [f"{name}: {state}" for name, state in self.tools.items()]
        if self.active_tunnels:
            tunnel_lines = []
            for interface in self.active_tunnels:
                stats = self.tunnel_stats.get(interface)
                if not stats:
                    tunnel_lines.append(f"{interface}: detected; detailed status unavailable")
                    continue
                tunnel_lines.append(
                    f"{interface}: {stats['peers']} peer(s) · latest handshake "
                    f"{_format_age(stats['latest_handshake'])} · received "
                    f"{_format_bytes(int(stats['received'] or 0))} · sent "
                    f"{_format_bytes(int(stats['sent'] or 0))}"
                )
        else:
            tunnel_lines = ["No WireGuard interface detected."]
        tunnel_lines.append(f"OpenVPN process: {'running' if self.openvpn_running else 'not detected'}")

        route_lines = [
            f"Default interface: {self.route.get('interface', 'Unknown')}",
            f"Default gateway: {self.route.get('gateway', 'Unknown')}",
            "Configured DNS servers: " + (", ".join(self.dns_servers) or "Unknown"),
        ]
        if self.route.get("error"):
            route_lines.append(f"Route note: {self.route['error']}")

        sections: list[tuple[str, str, bool]] = [
            ("Connection summary", self.summary, False),
            ("Installed VPN tools", "\n".join(tool_lines), True),
            ("Detected tunnels", "\n".join(tunnel_lines), True),
        ]
        installed_apps = [a for a in self.vpn_apps if a.installed]
        if installed_apps:
            sections.append(("VPN apps (managed by the app, not Tunnel)",
                             "\n".join(a.line() for a in installed_apps), True))
        sections.append(("Local route and DNS", "\n".join(route_lines), True))
        if self.selected_profile:
            profile = self.selected_profile
            identity = [
                f"Name: {profile.get('name') or 'Unnamed'}",
                f"Interface: {profile.get('interface') or 'Not set'}",
                f"Endpoint: {profile.get('endpoint') or 'Not set'}:{profile.get('port') or 'Not set'}",
            ]
            if profile.get("notes"):
                identity.append(f"Notes: {profile['notes']}")
            findings = "\n\n".join(item.line() for item in self.profile_findings)
            sections.append((
                "Selected profile comparison",
                "\n".join(identity) + (f"\n\n{findings}" if findings else ""),
                False,
            ))
        if self.external_checked:
            latency_text = "Not available"
            if self.latency_result:
                if self.latency_result.get("latency_ms") is not None:
                    latency_text = (
                        f"{self.latency_result['latency_ms']} ms via "
                        f"{self.latency_result.get('method', 'unknown')}"
                    )
                else:
                    latency_text = self.latency_result.get("status", "Unreachable")
            sections.append((
                "Optional external checks",
                f"Public IP: {self.public_ip or 'Unavailable'}\n"
                f"Connectivity latency: {latency_text}",
                True,
            ))
        if self.errors:
            sections.append(("Checks that could not finish", "\n".join(self.errors), True))
        next_steps = _recommended_steps(self)
        if next_steps:
            sections.append((
                "Recommended next steps",
                "\n".join(f"{index}. {step}" for index, step in enumerate(next_steps, 1)),
                False,
            ))
        sections.append((
            "How to read this",
            "This is a read-only snapshot. A detected tunnel does not prove that every "
            "application uses it, and the configured DNS list is not a full DNS-leak "
            "test. Tunnel did not connect, disconnect, edit routes, or change firewall rules.",
            False,
        ))
        return sections

    def as_text(self) -> str:
        lines = [f"Tunnel connection check · {self.checked_at}"]
        for title, body, _mono in self.sections():
            lines.extend(("", title, body))
        return "\n".join(lines)


def collect_vpn_diagnostics(
    *,
    include_external: bool = False,
    selected_profile: dict | None = None,
    should_cancel: CancelCheck = _never_cancel,
) -> VpnDiagnosticsReport:
    """Collect a bounded diagnostic snapshot, external calls only when opted in."""
    tools = {
        "WireGuard app": "installed" if Path("/Applications/WireGuard.app").exists() else "not detected",
        "wg status tool": "installed" if wireguard_manager.is_wg_available() else "not detected",
        "wg-quick control tool": "installed" if wireguard_manager.is_wg_quick_available() else "not detected",
        "OpenVPN command": "installed" if shutil.which("openvpn") else "not detected",
    }
    active_tunnels, errors = _wg_interfaces()
    tunnel_stats: dict[str, dict[str, int | None]] = {}
    if wireguard_manager.is_wg_available():
        for interface in active_tunnels:
            if should_cancel():
                break
            stats, stat_errors = _peer_stats(interface)
            tunnel_stats[interface] = stats
            errors.extend(stat_errors)

    try:
        apps = vpn_apps.detect()
    except Exception as exc:  # a broken probe must not hide the rest of the check
        apps = []
        errors.append(f"VPN apps: {exc}")

    route = _default_route()
    try:
        dns_servers = list(dns_check.get_system_dns_servers())
    except Exception as exc:
        dns_servers = []
        errors.append(f"DNS configuration: {exc}")

    report = VpnDiagnosticsReport(
        checked_at=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        tools=tools,
        active_tunnels=active_tunnels,
        tunnel_stats=tunnel_stats,
        route=route,
        dns_servers=dns_servers,
        openvpn_running=_openvpn_running(),
        vpn_apps=apps,
        selected_profile=_public_profile(selected_profile) if selected_profile else None,
        cancelled=should_cancel(),
        errors=errors,
    )
    if report.selected_profile:
        report.profile_findings = _compare_profile(report.selected_profile, report)
    if not include_external or report.cancelled:
        return report

    report.external_checked = True
    report.public_ip = public_ip.get_public_ip()
    if should_cancel():
        report.cancelled = True
        return report
    report.latency_result = latency.measure_latency()
    report.cancelled = should_cancel()
    return report


def _compare_profile(profile: dict, report: VpnDiagnosticsReport) -> list[DiagnosticFinding]:
    """Explain how a selected profile compares with the read-only snapshot."""
    findings: list[DiagnosticFinding] = []
    interface = str(profile.get("interface") or "").strip()
    endpoint = str(profile.get("endpoint") or "").strip()
    protocol = _profile_protocol(profile)
    try:
        port = int(profile.get("port") or 0)
    except (TypeError, ValueError):
        port = 0

    if protocol == "Unknown":
        findings.append(DiagnosticFinding(
            "check", "Unsupported protocol",
            f"Tunnel does not recognize the profile protocol '{profile.get('protocol')}'.",
            "Correct the profile protocol before using comparison or action previews.",
        ))
    elif protocol == "OpenVPN":
        if report.openvpn_running:
            findings.append(DiagnosticFinding(
                "ready", "OpenVPN process detected",
                "An OpenVPN process is running, although this snapshot cannot prove it uses the selected profile.",
            ))
        else:
            findings.append(DiagnosticFinding(
                "check", "OpenVPN is not running",
                "No OpenVPN process was detected for the selected OpenVPN profile.",
                "Open the profile in your trusted OpenVPN client and review its local connection log.",
            ))
    elif not interface:
        findings.append(DiagnosticFinding(
            "check", "Interface is missing", "The profile does not name a WireGuard interface.",
            "Add the interface used by its WireGuard configuration, for example wg0.",
        ))
    elif not SAFE_INTERFACE.fullmatch(interface):
        findings.append(DiagnosticFinding(
            "check", "Interface name is not safe", f"'{interface}' is not a valid preview target.",
            "Use a name that starts with a letter or number and contains at most 15 letters, numbers, dots, underscores, equals signs, or hyphens.",
        ))
    elif interface in report.active_tunnels:
        findings.append(DiagnosticFinding(
            "ready", "Selected tunnel is active", f"The detected interface matches {interface}.",
        ))
        stats = report.tunnel_stats.get(interface, {})
        if stats.get("peers") and not stats.get("latest_handshake"):
            findings.append(DiagnosticFinding(
                "check", "No completed handshake", "A peer is configured but has not completed a handshake.",
                "Check the endpoint, UDP reachability, server availability, and that both peers use the correct keys.",
            ))
        elif stats.get("latest_handshake"):
            findings.append(DiagnosticFinding(
                "ready", "Handshake detected", f"The latest handshake was {_format_age(int(stats['latest_handshake']))}.",
            ))
    else:
        findings.append(DiagnosticFinding(
            "check", "Selected tunnel is not active",
            f"The profile expects {interface}, but that exact interface was not detected. "
            "The macOS WireGuard app may expose imported tunnels under a utun name.",
            "Confirm the tunnel name in the WireGuard app; if it is stopped, review the Connect preview before starting it.",
        ))

    if not endpoint or endpoint in {"0.0.0.0", "<SERVER_IP>", "<SERVER_PUBLIC_IP_OR_DDNS>"}:
        findings.append(DiagnosticFinding(
            "check", "Endpoint needs attention", "The profile has no usable server address.",
            "Replace the placeholder with the VPN server's IP address or DNS name.",
        ))
    elif not 1 <= port <= 65535:
        findings.append(DiagnosticFinding(
            "check", "Port needs attention", f"The configured port '{profile.get('port')}' is not valid.",
            f"Use the {protocol} port configured on the server.",
        ))
    else:
        findings.append(DiagnosticFinding(
            "info", "Endpoint is present",
            f"The profile lists {endpoint}:{port}. The live peer endpoint is not queried or verified by this check.",
        ))

    if protocol == "WireGuard" and interface and interface in report.active_tunnels:
        route_interface = report.route.get("interface", "Unknown")
        if route_interface == interface:
            findings.append(DiagnosticFinding(
                "ready", "Default route matches", "The system's default route uses the selected interface.",
            ))
        elif route_interface != "Unknown":
            findings.append(DiagnosticFinding(
                "info", "Default route differs",
                f"The default route uses {route_interface}, not {interface}. This can be normal for a split tunnel.",
                "If this should be a full tunnel, verify AllowedIPs and run the optional public-IP check.",
            ))
    return findings


def _recommended_steps(report: VpnDiagnosticsReport) -> list[str]:
    steps: list[str] = []
    protocol = _profile_protocol(report.selected_profile) if report.selected_profile else None
    if protocol in {None, "WireGuard"} and report.tools.get("wg status tool") != "installed":
        steps.append("Install WireGuard tools before expecting detailed peer and handshake status.")
    if protocol == "OpenVPN" and report.tools.get("OpenVPN command") != "installed":
        steps.append("Install or open a trusted OpenVPN client before checking this profile's connection log.")
    for finding in report.profile_findings:
        if finding.next_step and finding.next_step not in steps:
            steps.append(finding.next_step)
    if any(app.active for app in report.vpn_apps):
        steps.append("A VPN app is carrying your traffic. Use Your IP & DNS to confirm the exit "
                     "address and resolvers, and leave Tunnel's kill switch off: use the app's own "
                     "kill switch setting.")
    if not report.dns_servers:
        steps.append("Confirm which DNS resolver the VPN profile intends to use; the system list could not be read.")
    if report.external_checked and not report.public_ip:
        steps.append("Repeat the external check after confirming internet access and DNS resolution.")
    return steps[:5]


def build_vpn_action_preview(
    action: str,
    profile: dict | None,
    report: VpnDiagnosticsReport | None = None,
) -> VpnActionPreview:
    """Build a copyable action plan without invoking a shell or changing state."""
    normalized = action.strip().title()
    if normalized not in SUPPORTED_PREVIEW_ACTIONS:
        return VpnActionPreview(
            normalized or "Unknown", "", "", (), (), (),
            "Choose Connect, Disconnect, or Restart.",
        )
    if not profile:
        return VpnActionPreview(
            normalized, "", "", (), (), (),
            "Choose a VPN profile before previewing an action.",
        )

    name = str(profile.get("name") or "Unnamed")
    protocol = _profile_protocol(profile)
    if protocol == "OpenVPN":
        return VpnActionPreview(
            normalized, name, "", (), (), (),
            "Safe action previews currently support WireGuard profiles only. "
            "No OpenVPN command was generated.",
        )
    if protocol == "Unknown":
        return VpnActionPreview(
            normalized, name, "", (), (), (),
            f"The profile protocol '{profile.get('protocol')}' is not supported. "
            "No command was generated.",
        )
    interface = str(profile.get("interface") or "").strip()
    if not SAFE_INTERFACE.fullmatch(interface):
        return VpnActionPreview(
            normalized, name, interface, (), (), (),
            "The selected profile has no safe WireGuard interface name. No command preview was generated.",
        )

    command_map = {
        "Connect": (f"sudo wg-quick up {interface}",),
        "Disconnect": (f"sudo wg-quick down {interface}",),
        "Restart": (
            f"sudo wg-quick down {interface}",
            f"sudo wg-quick up {interface}",
        ),
    }
    effects_map = {
        "Connect": (
            "Start the selected WireGuard interface.",
            "Routes and DNS may change according to that profile's local configuration.",
        ),
        "Disconnect": (
            "Stop the selected WireGuard interface.",
            "Traffic may return to the ordinary network unless a kill switch is already active.",
        ),
        "Restart": (
            "Stop and then start the selected WireGuard interface.",
            "There will be a brief loss of tunnel connectivity between commands.",
        ),
    }
    checklist = [
        "Confirm this is your profile and that changing it will not interrupt required remote access.",
        f"Confirm /etc/wireguard/{interface}.conf exists and contains the intended routes and DNS.",
        "Run a fresh Connection Check after any manual change.",
    ]
    if report and normalized == "Connect" and interface in report.active_tunnels:
        checklist.insert(0, "The interface already appears active; connecting again may fail or be unnecessary.")
    if report and normalized in {"Disconnect", "Restart"} and interface not in report.active_tunnels:
        checklist.insert(0, "The interface was not active in the latest snapshot; refresh before acting.")

    return VpnActionPreview(
        normalized,
        name,
        interface,
        command_map[normalized],
        effects_map[normalized],
        tuple(checklist),
    )

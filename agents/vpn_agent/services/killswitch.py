"""
killswitch.py — Fail closed when the tunnel drops.

Without this, a tunnel that dies takes your protection with it and says
nothing: macOS falls back to the ordinary route and your traffic carries on
over the ISP, unencrypted, looking exactly like it did a second earlier. The
Monitor tab notices and warns, but by then packets have already left.

Armed, the rule is inverted — anything that is not the tunnel is dropped, so a
dead tunnel means no traffic rather than unprotected traffic.

Three deliberate choices, all of them about not bricking your network:

  Rules live in a named pf anchor, never the main ruleset. Apple owns
  /etc/pf.conf and ships its own anchors in it; loading into `vpn-agent-killswitch`
  means flushing ours can never disturb theirs.

  The switch does not survive a reboot. A kill switch that comes back on its
  own after a restart is one you cannot escape without knowing pfctl — the
  machine boots with no network and no explanation. Rebooting is a deliberate
  act, so failing open there is the safer trade.

  Endpoint hostnames are resolved to addresses at arm time. DNS is blocked once
  armed, so a rule naming a hostname could never be evaluated; pinning the
  addresses is what lets the tunnel be rebuilt while the switch is on.

If anything goes wrong, recovery is one command and it is printed everywhere
this module reports state — see recovery_command().
"""

from __future__ import annotations

import re

import ipaddress
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agents.vpn_agent.server import paths

ANCHOR = "vpn-agent-killswitch"
PF_CONF = Path("/etc/pf.conf")
MARKER_START = "# >>> vpn-agent-killswitch >>>"
MARKER_END = "# <<< vpn-agent-killswitch <<<"

# Private ranges treated as "the LAN" when local access is kept.
DEFAULT_LAN = ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")

# macOS keeps a handful of utun devices for its own use (iCloud Private Relay,
# Handoff). Passing every utun would quietly exempt those from the switch, so
# the active WireGuard device is looked up instead and only that one passes.
WG_RUN_DIR = Path("/var/run/wireguard")

TIMEOUT = 30


@dataclass
class KillSwitchState:
    available: bool
    armed: bool
    registered: bool                       # anchor referenced from /etc/pf.conf
    pf_enabled: bool = False
    interfaces: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if not self.available:
            return f"Unavailable: {self.error}"
        if self.armed:
            where = ", ".join(self.interfaces) or "no tunnel detected"
            return f"ARMED — only {where} may carry traffic"
        return "Disarmed — traffic uses whatever route macOS picks"


def is_supported() -> bool:
    """pf is a BSD facility; this is macOS-only."""
    return os.uname().sysname == "Darwin" and shutil.which("pfctl") is not None


def rules_path() -> Path:
    return paths.state_dir() / "killswitch.pf"


def recovery_command() -> str:
    """
    The one command that undoes everything this module does.

    Printed with every arm, every failure, and in the docs. If the app dies
    while armed, this is the way back and it must be discoverable without the
    app running.
    """
    return f"sudo pfctl -a {ANCHOR} -F all && sudo pfctl -F all -f /etc/pf.conf"


# ── Discovering what to allow ────────────────────


def active_tunnel_interfaces() -> list[str]:
    """
    Real utun devices belonging to WireGuard tunnels.

    wg-quick on macOS creates a utun with a kernel-assigned number and records
    the mapping in /var/run/wireguard/<name>.name. Reading that is the only way
    to tell our tunnel from Private Relay's.
    """
    found: list[str] = []
    if not WG_RUN_DIR.is_dir():
        return found
    try:
        for entry in WG_RUN_DIR.glob("*.name"):
            try:
                device = entry.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if device and device not in found:
                found.append(device)
    except OSError:
        pass
    return found


def resolve_endpoints(hosts: list[str]) -> tuple[list[str], list[str]]:
    """
    Turn endpoint hosts into literal addresses.

    Returns (addresses, unresolved). An endpoint that cannot be resolved is
    reported rather than skipped silently — arming without it would leave the
    tunnel unable to reach its own server.
    """
    addresses: list[str] = []
    unresolved: list[str] = []

    for host in hosts:
        host = (host or "").strip()
        if not host:
            continue
        try:
            parsed = ipaddress.ip_address(host)
            # 0.0.0.0 is the placeholder the shipped example profile carries.
            # As a pf destination it means "unspecified", so a rule naming it
            # protects nothing while looking like it does.
            if parsed.is_unspecified:
                unresolved.append(host)
                continue
            if host not in addresses:
                addresses.append(host)
            continue
        except ValueError:
            pass

        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            unresolved.append(host)
            continue
        for info in infos:
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)

    return addresses, unresolved


# ── Rule generation ──────────────────────────────


def build_rules(
    endpoints: list[str],
    allow: list[tuple[str, int]],
    *,
    interfaces: list[str] | None = None,
    allow_lan: bool = True,
    lan_cidrs: tuple[str, ...] = DEFAULT_LAN,
) -> str:
    """
    Render the pf anchor.

    Default deny, then a short list of things that must still work. `block` is
    written first for readability; the `pass quick` rules below short-circuit,
    so a packet matching one is passed immediately and anything unmatched falls
    through to the block.
    """
    interfaces = interfaces if interfaces is not None else active_tunnel_interfaces()

    lines = [
        "# VPN Agent kill switch — generated, do not edit by hand.",
        f"# Undo everything with:  {recovery_command()}",
        "",
        "# Default deny, for both address families.",
        "block drop all",
        "",
        "# Loopback. Blocking this breaks local IPC in ways that look like",
        "# unrelated application bugs.",
        "pass quick on lo0 all",
        "",
    ]

    if interfaces:
        lines.append("# The tunnel. Traffic already inside it is protected.")
        for interface in interfaces:
            lines.append(f"pass quick on {interface} all")
    else:
        lines += [
            "# No WireGuard tunnel is up. Nothing is exempt, which is the point:",
            "# armed with no tunnel means no traffic leaves.",
        ]
    lines.append("")

    if endpoints:
        lines.append("# Reaching the VPN server itself — without this the tunnel")
        lines.append("# could never be re-established while the switch is armed.")
        lines.append("# Each protocol is paired with its own port rather than opening")
        lines.append("# every combination: WireGuard is UDP, the fallback is TCP, and")
        lines.append("# TCP/51820 or UDP/443 would be holes serving nothing.")
        for address in endpoints:
            family = "inet6" if ":" in address else "inet"
            for proto, port in allow:
                lines.append(
                    f"pass out quick {family} proto {proto} from any to {address} port {port}"
                )
            # The Monitor tab pings this same endpoint to measure latency. Left
            # blocked, arming the switch would make the app report its own
            # server as unreachable.
            icmp = "icmp6" if family == "inet6" else "icmp"
            lines.append(f"pass out quick {family} proto {icmp} from any to {address}")
        lines.append("")

    lines += [
        "# DHCP. Without it the physical link cannot renew its lease and the",
        "# Mac silently loses its address after a while.",
        "pass quick inet proto udp from any port 68 to any port 67",
        "pass quick inet proto udp from any port 67 to any port 68",
        "",
    ]

    if allow_lan:
        lines.append("# Local network — printers, NAS, the router's admin page.")
        for cidr in lan_cidrs:
            lines.append(f"pass quick inet from {cidr} to {cidr}")
        # Link-local covers mDNS/Bonjour discovery on the same segment.
        lines.append("pass quick inet from 169.254.0.0/16 to 169.254.0.0/16")
        lines.append("pass quick inet6 from fe80::/10 to fe80::/10")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_rules(text: str) -> Path:
    path = rules_path()
    paths.ensure_private_dir(path.parent)
    # World-readable is fine and helps recovery: these are firewall rules, not
    # secrets, and a user digging themselves out should be able to read them.
    path.write_text(text, encoding="utf-8")
    path.chmod(0o644)
    return path


# ── Applying ─────────────────────────────────────


def validate(rules_file: Path | None = None) -> tuple[bool, str]:
    """
    Parse-check the rules without loading them.

    `pfctl -n` parses, reports errors, and applies nothing — and it needs no
    privileges to do so, so this can run on every arm without provoking an
    authorisation prompt. pfctl warns on stderr that -f *could* flush the main
    ruleset; with -n it does not, and that line is filtered out below so it
    cannot be mistaken for an error.
    """
    path = rules_file or rules_path()
    if not path.is_file():
        return False, f"No rules file at {path}"

    ok, output = _run(["pfctl", "-n", "-f", str(path)])
    noise = ("could result in flushing", "present in the main ruleset", "See /etc/pf.conf")
    cleaned = "\n".join(
        line for line in output.splitlines()
        if line.strip() and not any(n in line for n in noise)
    )
    if ok:
        return True, "Rules parse cleanly."
    return False, cleaned or "pf rejected the rules."


def status(endpoints: list[str] | None = None) -> KillSwitchState:
    """Report whether the switch is currently armed. Read-only, needs no root."""
    if not is_supported():
        return KillSwitchState(
            available=False, armed=False, registered=False,
            error="pf is macOS-only; no kill switch on this platform.",
        )

    registered = _is_registered()

    # `pfctl -a <anchor> -s rules` needs root, so armed-ness is inferred from
    # our own marker file instead. It is written under Application Support and
    # removed on disarm, so it cannot drift from the anchor unless someone
    # flushes pf by hand — which is the documented recovery anyway.
    armed_marker = paths.state_dir() / "killswitch.armed"

    return KillSwitchState(
        available=True,
        armed=armed_marker.exists() and registered,
        registered=registered,
        interfaces=active_tunnel_interfaces(),
        endpoints=endpoints or [],
    )


def arm(
    endpoints: list[str],
    allow: list[tuple[str, int]],
    *,
    allow_lan: bool = True,
    extra_interfaces: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Block everything that is not the tunnel.

    ``extra_interfaces`` names tunnel devices that are not WireGuard's (an
    OpenVPN utun); each must look like ``utunN``/``tunN``.

    Resolves endpoints, writes the anchor, registers it in /etc/pf.conf if
    needed, and loads it. Returns (ok, message); the message always carries the
    recovery command on failure.
    """
    if not is_supported():
        return False, "Kill switch requires macOS pf."

    addresses, unresolved = resolve_endpoints(endpoints)
    if unresolved and not addresses:
        return False, (
            f"Could not resolve any endpoint ({', '.join(unresolved)}). Arming now "
            "would block the tunnel from ever reconnecting. Fix the endpoint first."
        )

    interfaces = active_tunnel_interfaces()
    for device in extra_interfaces or []:
        if not re.fullmatch(r"(?:utun|tun)\d{1,4}", str(device)):
            return False, f"Refusing to arm — {device!r} is not a tunnel device name."
        if device not in interfaces:
            interfaces.append(device)
    rules = build_rules(addresses, allow, interfaces=interfaces, allow_lan=allow_lan)
    path = write_rules(rules)

    # Never hand pf something that does not parse. A half-applied ruleset is
    # the one failure mode that could leave the machine unable to talk to
    # anything, and this check costs nothing and needs no privileges.
    ok, detail = validate(path)
    if not ok:
        return False, f"Refusing to arm — the generated rules do not parse:\n{detail}"

    script = "; ".join([
        _ensure_registered_command(),
        f"pfctl -a {ANCHOR} -f {_q(path)}",
        "pfctl -E",
    ])
    ok, output = _run_privileged(script, "Arm the VPN Agent kill switch")

    if not ok:
        if "may still have happened" in output:
            # Approved, then timed out: pf may hold the rules now. Recording it
            # as armed keeps Disarm offered; disarming rules that never loaded
            # is harmless, while showing "Disarmed" over a live block is not.
            (paths.state_dir() / "killswitch.armed").write_text("armed\n", encoding="utf-8")
            return False, (
                f"{output}\nTreating the kill switch as armed so Disarm stays available. "
                f"If the network is now broken, press Disarm or run:\n  {recovery_command()}"
            )
        return False, (
            f"Failed to arm: {output}\n\nIf the network is now broken, run:\n  {recovery_command()}"
        )

    (paths.state_dir() / "killswitch.armed").write_text("armed\n", encoding="utf-8")

    detail = ", ".join(interfaces) if interfaces else "no tunnel is currently up"
    warning = ""
    if unresolved:
        warning = f"\nWarning: could not resolve {', '.join(unresolved)} — those endpoints are not exempt."
    if not interfaces:
        warning += (
            "\nWarning: no tunnel device detected, so nothing is exempt and "
            "traffic is blocked now. Connect a tunnel, then re-arm."
        )

    return True, (
        f"Kill switch ARMED (passing: {detail}).{warning}\n"
        f"Undo at any time with:\n  {recovery_command()}"
    )


def disarm() -> tuple[bool, str]:
    """Flush our anchor and let traffic route normally again."""
    if not is_supported():
        return False, "Kill switch requires macOS pf."

    ok, output = _run_privileged(
        f"pfctl -a {ANCHOR} -F rules", "Disarm the VPN Agent kill switch"
    )
    if not ok:
        # The anchor may still be loaded (a cancelled authorisation, a pfctl
        # error), so the marker stays: status() must keep saying "armed" until
        # the rules are really gone, never report a block that is still live.
        return False, f"Failed to disarm: {output}\n\nRun manually:\n  {recovery_command()}"

    marker = paths.state_dir() / "killswitch.armed"
    if marker.exists():
        marker.unlink()
    return True, "Kill switch disarmed — traffic routes normally again."


# ── /etc/pf.conf registration ────────────────────


def _is_registered() -> bool:
    try:
        return MARKER_START in PF_CONF.read_text(encoding="utf-8")
    except OSError:
        return False


def _ensure_registered_command() -> str:
    """
    Shell that adds our anchor to /etc/pf.conf, once.

    Apple owns this file and a major macOS update rewrites it, dropping our
    block — which fails open rather than closed, so the failure mode is a kill
    switch that stops working rather than a Mac that will not talk to anything.
    """
    if _is_registered():
        return "true"
    block = (
        f"{MARKER_START}\\n"
        f'anchor "{ANCHOR}"\\n'
        f"{MARKER_END}"
    )
    return (
        f"cp /etc/pf.conf /etc/pf.conf.vpn-agent.bak 2>/dev/null; "
        f"printf '%b\\n' {_q_str(block)} >> /etc/pf.conf; "
        f"pfctl -f /etc/pf.conf"
    )


# ── Privilege escalation ─────────────────────────


def _run_privileged(script: str, prompt: str) -> tuple[bool, str]:
    """
    Run a command as root, through the macOS authorisation dialog.

    Arming and disarming pf is a Sentinel-gated action, so it never rides on a
    cached sudo ticket; the shared runner in ``privileged`` owns the policy.
    Nothing secret is ever passed here — these are firewall rules and file
    paths — so the command being briefly visible in the process list costs
    nothing.
    """
    from agents.vpn_agent.services import privileged
    return privileged.run_as_root(script, prompt, TIMEOUT, allow_cached_sudo=False)


def _run(command: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for authorisation."
    except OSError as exc:
        return False, str(exc)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        return True, output
    if "User canceled" in output or "(-128)" in output:
        return False, "Cancelled."
    return False, output or f"exited {proc.returncode}"


def _q(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _q_str(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"

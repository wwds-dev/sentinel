"""
killswitch.py — Fail closed when the tunnel drops.

Without this, a tunnel that dies takes your protection with it and says
nothing: macOS falls back to the ordinary route and your traffic carries on
over the ISP, unencrypted, looking exactly like it did a second earlier. The
Monitor tab notices and warns, but by then packets have already left.

Armed, the rule is inverted — anything that is not the tunnel is dropped, so a
dead tunnel means no traffic rather than unprotected traffic.

Three deliberate choices, all of them about not bricking your network:

  Rules live in a named pf anchor, not spliced into Apple's rules. Apple owns
  /etc/pf.conf and ships its own anchors in it; loading into `vpn-agent-killswitch`
  means flushing ours can never disturb theirs. The one change to the main
  file is a marked block that registers the anchor (a backup is kept as
  /etc/pf.conf.vpn-agent.bak); it stays after Disarm and is harmless without
  rules in the anchor.

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
import json
import os
import shutil
import socket
import subprocess
import time
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

# What Sentinel knows about the switch. Only the marker file is read without
# root, so "armed" is a record of what Sentinel did, never proof of what pf holds.
STATE_DISARMED = "disarmed"                # no marker
STATE_RECORDED_ARMED = "recorded_armed"    # marker present, newer than boot, anchor registered
STATE_STALE = "stale"                      # marker older than the boot, or the anchor block is gone
STATE_VERIFIED_ARMED = "verified_armed"    # set only by the explicit Verify step (privileged pfctl read)
STATE_VERIFIED_EMPTY = "verified_empty"    # Verify found no rules
_ARMED_STATES = (STATE_RECORDED_ARMED, STATE_VERIFIED_ARMED)
VERIFY_UNKNOWN = "unknown"                 # Verify could not reach a conclusion (not a switch state)
# Printed between the two pfctl reads so one output can be split; chosen so pf's
# own text cannot contain it.
VERIFY_SEPARATOR = "--- sentinel-verify ---"
# kern.boottime can move by a few seconds when the clock is corrected.
BOOT_TOLERANCE = 5

MARKER_NAME = "killswitch.armed"

# The last Verify result ({"state", "at"}); memory only, never written to disk.
_VERIFIED: dict = {}


@dataclass
class KillSwitchState:
    available: bool
    armed: bool
    registered: bool                       # anchor referenced from /etc/pf.conf
    pf_enabled: bool = False
    interfaces: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    error: str = ""
    state: str = STATE_DISARMED
    boot_known: bool = True
    stale_reason: str = ""
    arming: bool = False                   # marker written, arm not yet confirmed
    verified_at: float = 0.0               # when Verify last concluded (memory only)

    def __post_init__(self) -> None:
        # Older callers build the state with armed=True only.
        if self.armed and self.state == STATE_DISARMED:
            self.state = STATE_RECORDED_ARMED
        self.armed = self.state in _ARMED_STATES

    def summary(self) -> str:
        if not self.available:
            return f"Unavailable: {self.error}"
        where = ", ".join(self.interfaces) or "no tunnel detected"
        if self.state == STATE_VERIFIED_ARMED:
            return (f"Verified armed ({_clock(self.verified_at)}) — only {where} may carry "
                    "traffic. Verify again to re-check.")
        if self.state == STATE_RECORDED_ARMED and self.arming:
            return (f"Arming, unconfirmed — the arm was started but not seen to finish; "
                    f"if it did, only {where} may carry traffic. Disarm clears it.")
        if self.state == STATE_RECORDED_ARMED:
            note = "" if self.boot_known else " (boot time unknown)"
            return (f"Recorded as armed (not verified){note} — only {where} "
                    "may carry traffic")
        if self.state == STATE_STALE:
            return f"Not armed: {self.stale_reason or 'the record is out of date'}"
        if self.state == STATE_VERIFIED_EMPTY:
            return (f"Not armed: no rules loaded ({_clock(self.verified_at)}). "
                    "Disarm clears the record.")
        return "Disarmed — traffic uses whatever route macOS picks"


def _clock(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch)) if epoch else "time unknown"


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


def parse_boottime(text: str) -> int | None:
    """Seconds since the epoch from `sysctl -n kern.boottime` ("{ sec = N, usec = M } …")."""
    match = re.search(r"\bsec\s*=\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


_BOOT_CACHE: list[int] = []        # one entry once read; the boot time cannot change


def _boot_time() -> int | None:
    """When this Mac booted, or None when it cannot be read (shown as unknown).
    Read once per process, by absolute path, so a PATH entry cannot stand in."""
    if _BOOT_CACHE:
        return _BOOT_CACHE[0]
    try:
        proc = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                              capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001 - unknown boot time is a state, not a crash
        return None
    value = parse_boottime(proc.stdout) if proc.returncode == 0 else None
    if value is not None:
        _BOOT_CACHE.append(value)
    return value


def marker_path() -> Path:
    return paths.state_dir() / MARKER_NAME


def _read_marker() -> dict | None:
    """The marker as a dict (state, at, boot, ifaces), or None if there is none.
    The first-generation marker ("armed\\n") reads as armed at its mtime."""
    path = marker_path()
    try:
        raw = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        at = data.get("at")
        return {"state": str(data.get("state") or "armed"),
                "at": float(at) if isinstance(at, (int, float)) else mtime,
                "boot": data.get("boot") if isinstance(data.get("boot"), int) else None,
                "ifaces": [str(x) for x in data.get("ifaces") or []]}
    return {"state": "armed", "at": mtime, "boot": None, "ifaces": []}


def _write_marker(state: str, interfaces: list[str]) -> None:
    path = marker_path()
    paths.ensure_private_dir(path.parent)
    payload = {"state": state, "at": int(time.time()), "boot": _boot_time(),
               "ifaces": list(interfaces)}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _classify(marker: dict | None, registered: bool) -> tuple[str, bool, str]:
    """(state, boot_known, stale_reason) for a marker."""
    if marker is None:
        return STATE_DISARMED, True, ""
    boot = _boot_time()
    if boot is not None:
        # Only "written before this boot" counts; a differing boot field alone is
        # not evidence enough to drop a record of a possibly live block.
        if marker["at"] < boot - BOOT_TOLERANCE:
            return STATE_STALE, True, "the Mac restarted since arming"
    if marker["state"] != "arming" and not registered:
        return STATE_STALE, boot is not None, "the pf anchor registration is gone"
    return STATE_RECORDED_ARMED, boot is not None, ""


def status(endpoints: list[str] | None = None) -> KillSwitchState:
    """Report what Sentinel knows about the switch. Read-only, needs no root.

    `pfctl -a <anchor> -s rules` needs root, so this reads Sentinel's own
    marker and the pf.conf registration, and says so: a marker is a record, not
    a measurement. It is older than the boot (stale) or its anchor block is
    gone when the record can no longer be right. Removing a stale marker is
    reconcile()'s job, never status()'s.
    """
    if not is_supported():
        return KillSwitchState(
            available=False, armed=False, registered=False,
            error="pf is macOS-only; no kill switch on this platform.",
        )

    registered = _is_registered()
    marker = _read_marker()
    state, boot_known, reason = _classify(marker, registered)
    arming = bool(marker and marker["state"] == "arming" and state == STATE_RECORDED_ARMED)
    verified_at = 0.0
    if state == STATE_RECORDED_ARMED and not arming and _VERIFIED:
        # The last explicit Verify, kept in memory only: it can be out of date
        # (pf flushed by hand since), so it carries its time and arm/disarm drop it.
        state, verified_at = _VERIFIED["state"], _VERIFIED["at"]

    return KillSwitchState(
        available=True,
        armed=state in _ARMED_STATES,
        registered=registered,
        interfaces=active_tunnel_interfaces(),
        endpoints=endpoints or [],
        state=state,
        boot_known=boot_known,
        stale_reason=reason,
        arming=arming,
        verified_at=verified_at,
    )


def reconcile() -> str:
    """Remove a stale marker (older than the boot, or its anchor block gone).
    Returns why it was removed ("" when nothing was), so the panel can say so
    instead of silently showing "disarmed". Called when the panel shows and
    before a disconnect review; never from status(). A marker that changed since
    it was read (a new arm landed) is left alone."""
    if not is_supported():
        return ""
    path = marker_path()
    try:
        before = path.read_bytes()
    except OSError:
        return ""
    marker = _read_marker()
    state, _, reason = _classify(marker, _is_registered())
    if state != STATE_STALE:
        return ""
    try:
        if path.read_bytes() != before:
            return ""
        path.unlink()
    except OSError:
        return ""
    return reason or "the record is out of date"


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
    # Record the intent BEFORE root runs: if this thread is lost mid-arm (the
    # app quits during the administrator dialog) the record already says the
    # switch may be live, and Disarm stays offered. The previous record is kept
    # so a failed re-arm does not erase the memory of a block that is still up.
    marker = marker_path()
    try:
        previous = marker.read_bytes()
    except OSError:
        previous = None
    _VERIFIED.clear()
    _write_marker("arming", interfaces)
    ok, output = _run_privileged(script, "Arm the VPN Agent kill switch")

    if not ok:
        if "may still have happened" in output:
            # Approved, then timed out: pf may hold the rules now. Recording it
            # as armed keeps Disarm offered; disarming rules that never loaded
            # is harmless, while showing "Disarmed" over a live block is not.
            _write_marker("armed", interfaces)
            return False, (
                f"{output}\nTreating the kill switch as armed so Disarm stays available. "
                f"If the network is now broken, press Disarm or run:\n  {recovery_command()}"
            )
        # A definitive failure (declined, pfctl error): the rules did not load,
        # so put the record back as it was.
        if previous is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_bytes(previous)
        return False, (
            f"Failed to arm: {output}\n\nIf the network is now broken, run:\n  {recovery_command()}"
        )

    _write_marker("armed", interfaces)

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
        f"Kill switch recorded as armed (not verified; passing: {detail}).{warning}\n"
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

    _VERIFIED.clear()
    marker = marker_path()
    if marker.exists():
        marker.unlink()
    return True, "Kill switch disarmed — traffic routes normally again."

# ── Verify (explicit, read-only) ─────────────────


_PF_NOISE = ("no altq", "altq related", "altq", "warning", "#")
_PF_STATUS = re.compile(r"^\s*Status:\s*(\w+)", re.IGNORECASE | re.MULTILINE)


def parse_verify_output(text: str) -> tuple[str, str]:
    """Map the output of the Verify script to (state, message).

    The real text of `pfctl -s info` / `-s rules` on macOS is UNVERIFIED, so this
    reads loosely: the "Status:" word, and whether any rule line is present and
    any of them says `block`. Anything it cannot place is VERIFY_UNKNOWN, never
    a guess in either direction.
    """
    unknown = ("Could not verify: {} (the pfctl output format is UNVERIFIED on this Mac; "
               "run `sudo pfctl -a {} -s rules` yourself to check).")
    if VERIFY_SEPARATOR not in (text or ""):
        return VERIFY_UNKNOWN, unknown.format("the output was not in the expected shape", ANCHOR)
    info, rules = text.split(VERIFY_SEPARATOR, 1)
    lines = [ln.strip() for ln in rules.splitlines()
             if ln.strip() and not ln.strip().lower().startswith(_PF_NOISE)]
    for line in lines:
        if line.lower().startswith("pfctl:"):
            return VERIFY_UNKNOWN, f"Could not verify: pfctl said \"{line[:160]}\"."
    status = _PF_STATUS.search(info)
    word = status.group(1).lower() if status else ""
    enabled = True if word == "enabled" else False if word == "disabled" else None
    now = _clock(time.time())
    if not lines:
        return STATE_VERIFIED_EMPTY, f"Not armed: no rules loaded in the anchor (verified {now})."
    if enabled is None:
        return VERIFY_UNKNOWN, unknown.format("pf's Status line was not recognised", ANCHOR)
    if not enabled:
        return STATE_VERIFIED_EMPTY, (f"Not armed: rules are loaded but pf is switched off "
                                      f"(verified {now}).")
    if not any(re.search(r"\bblock\b", line) for line in lines):
        return STATE_VERIFIED_EMPTY, (f"Not armed: the anchor holds rules but none of them blocks "
                                      f"traffic (verified {now}).")
    return STATE_VERIFIED_ARMED, (f"Verified armed ({now}): pf is on and the anchor holds "
                                  "blocking rules. This was true when checked.")


def verify() -> tuple[str, str]:
    """Ask pf what it holds (one administrator prompt) and report it.

    Read-only: two `pfctl -s` queries and nothing that loads, flushes, enables
    or disables anything. It always goes through the administrator dialog (see
    _run_privileged), never a cached sudo ticket. The result is kept in memory
    only. Returns (state, message); state is STATE_VERIFIED_ARMED,
    STATE_VERIFIED_EMPTY or VERIFY_UNKNOWN (cancelled, failed or unreadable,
    in which case nothing about the recorded state changes).
    """
    if not is_supported():
        return VERIFY_UNKNOWN, "Could not verify: the kill switch needs macOS pf."
    script = f"pfctl -s info; echo '{VERIFY_SEPARATOR}'; pfctl -a {ANCHOR} -s rules"
    ok, output = _run_privileged(script, "Check (read-only) the VPN Agent kill switch")
    if not ok:
        return VERIFY_UNKNOWN, f"Could not verify: {' '.join(output.split())[:240] or 'no answer'}"
    state, message = parse_verify_output(output)
    if state == VERIFY_UNKNOWN:
        return state, message
    _VERIFIED.update(state=state, at=time.time())
    return state, message


def clear_record() -> bool:
    """Forget the armed record (the marker file and any verified result).
    For after Verify found no rules; changes nothing in pf."""
    _VERIFIED.clear()
    try:
        marker_path().unlink(missing_ok=True)
    except OSError:
        return False
    return True


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

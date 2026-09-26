"""Gated execution for Tunnel's live Connect/Disconnect (phase 3b).

``vpn_connection`` knows *how* to bring a tunnel up or down. This module decides
*whether* it may, and records what happened:

1. **Target review** — before anything runs, name the exact target, the command,
   the non-secret routing/DNS intent of the config, and every reason to refuse.
   Blockers stop execution outright; warnings are shown in the confirmation.
2. **Explicit confirmation** happens in the panel, from ``review.confirmation_text()``.
3. **Administrator authorisation** stays inside ``vpn_connection`` (injected
   ``run_as_root``), so tests never touch ``sudo``/``osascript``.
4. **Fresh post-change check** — after the command, re-read local state (the
   ``/var/run/wireguard`` records, the OpenVPN process, the route to a public
   address) instead of trusting the command's exit status.
5. **Local audit** — one JSON line per privileged attempt, refused ones
   included. Only non-secret fields are written; config key material is never
   read into this module (``inspect_wireguard_config`` discards it at parse time).
6. **Rollback guidance** travels with every review and every outcome.

The review is re-run inside ``execute`` so a file changed between the dialog and
the worker is still caught.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agents.vpn_agent.services.config_inspection import (
    inspect_wireguard_config as inspect_config_summary,
)
from services import openvpn_manager, vpn_connection

# wg-quick's own interface-name rule (wg-quick(8)); a config file whose stem
# fails it is refused by wg-quick, so refuse before the password prompt instead.
WG_INTERFACE_NAME = re.compile(r"^[a-zA-Z0-9_=+.-]{1,15}$")
WG_RUN_DIR = Path("/var/run/wireguard")
# A routing-table lookup only — `route get` sends no packets to this address.
ROUTE_PROBE_ADDRESS = "1.1.1.1"
AUDIT_FILENAME = "tunnel_audit.jsonl"
OUTPUT_LIMIT = 2000
ACTIONS = ("connect", "disconnect")


# ── Local state probes (read-only, no root) ─────────────────────────────────

def wireguard_interface_state(name: str, run_dir: Path = WG_RUN_DIR) -> dict:
    """Whether wg-quick currently has ``name`` up, from its own run records.

    On macOS wg-quick maps the configured name to a kernel-numbered utun in
    ``<run_dir>/<name>.name`` and serves ``<utun>.sock``. The name record can be
    unreadable to a normal user, so its presence is the signal and the device is
    reported only when it can be read.
    """
    record = run_dir / f"{name}.name"
    try:
        present = record.exists()
    except OSError:
        present = False
    if not present:
        return {"up": False, "device": None}
    try:
        device = record.read_text(encoding="utf-8").strip() or None
    except OSError:
        return {"up": True, "device": None}
    if device and not (run_dir / f"{device}.sock").exists():
        return {"up": False, "device": device, "stale": True}
    return {"up": True, "device": device}


def route_interface(address: str = ROUTE_PROBE_ADDRESS) -> str | None:
    """The interface macOS would use for ``address`` (routing lookup only)."""
    route = shutil.which("route") or ("/sbin/route" if Path("/sbin/route").is_file() else None)
    if route is None:
        return None
    try:
        proc = subprocess.run([route, "-n", "get", address],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key == "interface":
            return value.strip() or None
    return None


def _default_probe() -> dict:
    return {
        "wireguard": wireguard_interface_state,
        "openvpn_running": openvpn_manager.is_running,
        "route_interface": route_interface,
    }


# ── Target review ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionReview:
    action: str
    protocol: str
    profile_name: str
    target: str
    interface: str
    command: str
    route_mode: str = ""
    config_lines: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    rollback: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.blockers

    def confirmation_text(self) -> str:
        verb = "Start" if self.action == "connect" else "Stop"
        lines = [
            f"{verb} a real {self.protocol} tunnel for '{self.profile_name}'?",
            "",
            f"Target: {self.target}",
            f"Command (as administrator): {self.command}",
        ]
        if self.config_lines:
            lines += [""] + list(self.config_lines)
        if self.warnings:
            lines += ["", "Check first:"] + [f"• {item}" for item in self.warnings]
        lines += ["", "To undo:"] + [f"• {item}" for item in self.rollback]
        lines += ["", "Tunnel will re-check local state afterwards and record this "
                      "attempt in its local audit log. Traffic protection is not "
                      "verified by this check."]
        return "\n".join(lines)

    def sections(self) -> list[tuple[str, str, bool]]:
        head = [f"Action: {self.action.title()}", f"Protocol: {self.protocol}",
                f"Profile: {self.profile_name}", f"Target: {self.target or 'None'}"]
        if self.interface:
            head.append(f"Interface: {self.interface}")
        sections: list[tuple[str, str, bool]] = [
            ("Target review", "\n".join(head), True),
        ]
        if self.command:
            sections.append(("Command (runs as administrator)", self.command, True))
        if self.config_lines:
            sections.append(("Configuration intent (keys discarded)",
                             "\n".join(self.config_lines), True))
        if self.blockers:
            sections.append(("Refused — nothing was run",
                             "\n".join(f"• {item}" for item in self.blockers), False))
        if self.warnings:
            sections.append(("Check before running",
                             "\n".join(f"• {item}" for item in self.warnings), False))
        if self.rollback:
            sections.append(("Rollback", "\n".join(f"• {item}" for item in self.rollback), False))
        return sections


def _wg_interface_for(profile: dict) -> tuple[str, str]:
    """(target passed to wg-quick, interface name wg-quick will create)."""
    config_path = vpn_connection.resolve_config_path(profile)
    if config_path:
        return config_path, Path(config_path).stem
    interface = str(profile.get("interface") or "").strip()
    return interface, interface


def _killswitch_armed() -> bool:
    ks = vpn_connection._killswitch()
    if ks is None:
        return False
    try:
        return bool(ks.status().armed)
    except Exception:
        return False


def review_execution(action: str, profile: dict | None, *,
                     probe: dict | None = None,
                     killswitch_armed: Callable[[], bool] | None = None) -> ExecutionReview:
    """Everything the user must see — and every reason to refuse — before running."""
    probe = probe or _default_probe()
    killswitch_armed = killswitch_armed or _killswitch_armed
    profile = dict(profile or {})
    action = str(action).strip().lower()
    name = str(profile.get("name") or "Unnamed")
    protocol = vpn_connection.resolve_protocol(profile)
    if action not in ACTIONS:
        return ExecutionReview(action, protocol, name, "", "", "",
                               blockers=(f"Unknown action '{action}'.",))
    if not profile:
        return ExecutionReview(action, protocol, name, "", "", "",
                               blockers=("Choose a server profile first.",))

    blockers: list[str] = []
    warnings: list[str] = []
    config_lines: list[str] = []
    route_mode = ""
    ks_armed = killswitch_armed()

    if action == "connect" and vpn_connection.is_placeholder(profile):
        blockers.append("This profile is an example/template, not a real server. "
                        "Import a WireGuard .conf or OpenVPN .ovpn first.")

    if protocol == "WireGuard":
        target, interface = _wg_interface_for(profile)
        command = (vpn_connection.wg_quick_command("up" if action == "connect" else "down", target)
                   if target else "")
        if not vpn_connection.wireguard_manager.is_wg_quick_available():
            blockers.append("wg-quick not found — install wireguard-tools "
                            "(`brew install wireguard-tools`).")
        if not target:
            blockers.append("This WireGuard profile has no interface or config file.")
        elif not WG_INTERFACE_NAME.fullmatch(interface):
            blockers.append(
                f"'{interface}' is not a valid WireGuard interface name (letters, digits "
                "and _=+.- only, at most 15 characters). wg-quick names the interface "
                "after the config file, so rename the file and import it again.")
        config_path = vpn_connection.resolve_config_path(profile)
        if config_path and action == "connect":
            source = Path(config_path).expanduser()
            if not source.is_file():
                blockers.append(f"The config file {source} no longer exists.")
            else:
                try:
                    summary = inspect_config_summary(source)
                except ValueError as exc:
                    blockers.append(str(exc))
                else:
                    route_mode = summary.route_mode
                    config_lines = [
                        f"Routing: {summary.route_mode}",
                        "Endpoints: " + (", ".join(summary.endpoints) or "Not specified"),
                        "DNS: " + (", ".join(summary.dns_servers) or "Not specified — system DNS stays in use"),
                        f"Peers: {summary.peer_count}",
                    ]
                    warnings.extend(summary.warnings)
                    if summary.peer_count == 0 or not summary.endpoints:
                        blockers.append("The config has no peer endpoint to connect to.")
                    if route_mode == "Split tunnel":
                        warnings.append("Split tunnel: only the listed networks use the VPN; "
                                        "other traffic keeps your normal route.")
                try:
                    mode = source.stat().st_mode
                    if mode & (stat.S_IRGRP | stat.S_IROTH):
                        warnings.append("The config file (which holds your private key) is "
                                        "readable by other users on this Mac — consider "
                                        "`chmod 600` on it.")
                except OSError:
                    pass
        if interface and WG_INTERFACE_NAME.fullmatch(interface):
            state = probe["wireguard"](interface)
            if action == "connect" and state.get("up"):
                blockers.append(f"'{interface}' is already up — Disconnect it first.")
            if action == "disconnect" and not state.get("up"):
                warnings.append(f"'{interface}' is not currently recorded as up; "
                                "wg-quick down will probably report that.")
        # For a person at a terminal: sudo can refuse a leading PATH=… assignment,
        # and their shell already finds wg-quick.
        shown = vpn_connection._quote(target) if target else "…"
        up_cmd, down_cmd = f"wg-quick up {shown}", f"wg-quick down {shown}"
        if action == "connect":
            rollback = [f"Press Disconnect, or run: sudo {down_cmd}"]
        else:
            rollback = [f"Press Connect, or run: sudo {up_cmd}"]
    elif protocol == "OpenVPN":
        interface = ""
        target = vpn_connection.resolve_config_path(profile) or ""
        command = (f"openvpn --config {vpn_connection._quote(target)} --daemon sentinel-ovpn …"
                   if action == "connect" else "kill -TERM <pid of Sentinel's tracked openvpn>")
        if action == "connect":
            if not openvpn_manager.is_openvpn_available():
                blockers.append("openvpn client not found — install it, e.g. "
                                "`brew install openvpn`.")
            if not target:
                blockers.append("This OpenVPN profile has no config file.")
            elif not Path(target).expanduser().is_file():
                blockers.append(f"The config file {target} no longer exists.")
            if probe["openvpn_running"]():
                blockers.append("An OpenVPN process is already running — Disconnect it first.")
            rollback = ["Press Disconnect (only a process Sentinel started and "
                        "tracks will be stopped)."]
        else:
            rollback = ["Press Connect to start the tunnel again."]
    else:
        interface, target, command = "", "", ""
        blockers.append(f"Unsupported protocol '{profile.get('protocol')}'.")
        rollback = []

    if action == "disconnect":
        if ks_armed:
            warnings.append("The kill switch is armed: after the tunnel stops, all "
                            "traffic stays blocked until you Disarm it.")
        else:
            warnings.append("After the tunnel stops, traffic returns to your ordinary "
                            "network in the clear.")
    elif ks_armed:
        rollback.append("The kill switch is armed; Disarm restores ordinary traffic.")

    return ExecutionReview(
        action=action, protocol=protocol, profile_name=name, target=target,
        interface=interface, command=command, route_mode=route_mode,
        config_lines=tuple(config_lines), blockers=tuple(blockers),
        warnings=tuple(warnings), rollback=tuple(rollback),
    )


# ── Post-change check ───────────────────────────────────────────────────────

def post_change_check(review: ExecutionReview, probe: dict | None = None, *,
                      attempts: int = 3, delay: float = 0.5,
                      sleep: Callable[[float], None] = time.sleep) -> tuple[bool | None, str]:
    """Re-read local state after the command. (verified, human-readable detail).

    ``True``/``False`` say whether the expected state was observed; ``None`` means
    it could not be determined. A short retry absorbs the moment wg-quick or
    openvpn takes to write its records.
    """
    probe = probe or _default_probe()
    want_up = review.action == "connect"
    if review.protocol == "WireGuard" and review.interface:
        state: dict = {}
        for attempt in range(max(1, attempts)):
            state = probe["wireguard"](review.interface)
            if bool(state.get("up")) == want_up:
                break
            if attempt + 1 < attempts:
                sleep(delay)
        up = bool(state.get("up"))
        device = state.get("device")
        if want_up and not up:
            return False, (f"'{review.interface}' is not recorded as up after the command. "
                           "Run Connection Check before relying on the tunnel.")
        if not want_up and up:
            return False, f"'{review.interface}' is still recorded as up."
        if not want_up:
            return True, f"'{review.interface}' is no longer recorded as up."
        detail = f"'{review.interface}' is up" + (f" on {device}." if device else ".")
        if review.route_mode == "Full tunnel":
            via = probe["route_interface"]()
            if device and via == device:
                detail += f" Public traffic routes via {device}."
            elif via:
                detail += (f" Public traffic still routes via {via}, not the tunnel"
                           + (f" ({device})." if device else "."))
                return False, detail
            else:
                detail += " The route to the internet could not be read."
        detail += " Handshake, DNS and leak behaviour are not verified by this check."
        return True, detail
    if review.protocol == "OpenVPN":
        running = False
        for attempt in range(max(1, attempts)):
            running = bool(probe["openvpn_running"]())
            if running == want_up:
                break
            if attempt + 1 < attempts:
                sleep(delay)
        if running == want_up:
            return True, ("An OpenVPN process is running." if running
                          else "No OpenVPN process is running.")
        return False, ("No OpenVPN process was found after the command." if want_up
                       else "An OpenVPN process is still running.")
    return None, "No post-change check exists for this protocol."


# ── Local audit ─────────────────────────────────────────────────────────────

def default_audit_path() -> Path:
    from services import runtime_paths
    return runtime_paths.user_data_base() / "data" / "logs" / AUDIT_FILENAME


def append_audit(entry: dict, path: Path | None = None) -> Path | None:
    """Append one JSON line. Auditing must never break the action it records."""
    target = Path(path) if path else default_audit_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target
    except OSError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def record_killswitch(action: str, ok: bool, message: str, *,
                      audit_path: Path | None = None) -> Path | None:
    return append_audit({
        "time": _now(), "action": f"killswitch-{action}", "protocol": "pf",
        "outcome": "succeeded" if ok else "failed",
        "detail": str(message or "")[:OUTPUT_LIMIT],
    }, audit_path)


# ── Execution ───────────────────────────────────────────────────────────────

@dataclass
class ExecutionOutcome:
    review: ExecutionReview
    ran: bool
    success: bool
    output: str = ""
    error: str | None = None
    verified: bool | None = None
    verification: str = ""
    audit_path: str | None = None
    checked_at: str = field(default_factory=_now)

    @property
    def status_line(self) -> str:
        if not self.ran:
            return "Refused: " + "; ".join(self.review.blockers)
        if not self.success:
            return f"Failed: {self.error or 'the command reported an error.'}"
        verb = "connect" if self.review.action == "connect" else "disconnect"
        if self.verified is True:
            return f"{self.review.protocol} {verb}: verified locally — {self.verification}"
        if self.verified is False:
            return f"{self.review.protocol} {verb}: command completed but NOT verified — {self.verification}"
        return f"{self.review.protocol} {verb}: command completed; state not verified."

    def sections(self) -> list[tuple[str, str, bool]]:
        if not self.ran:
            result = "Nothing was executed and no administrator access was requested."
        elif self.success:
            result = "The command completed."
        else:
            result = f"The command failed: {self.error or 'no detail'}"
        sections = [("Result", f"{result}\nChecked: {self.checked_at}", False)]
        if self.ran:
            label = {True: "Verified", False: "Not verified", None: "Unknown"}[self.verified]
            sections.append((f"Post-change check — {label}", self.verification, False))
        sections += self.review.sections()
        if self.output:
            sections.append(("Tool output", self.output[:OUTPUT_LIMIT], True))
        sections.append((
            "Audit",
            f"Recorded in {self.audit_path}." if self.audit_path
            else "This attempt could not be written to the local audit log.",
            False,
        ))
        return sections

    def as_result(self) -> dict:
        """The dict shape the panel and older callers already understand."""
        return {
            "success": self.ran and self.success,
            "protocol": self.review.protocol,
            "action": self.review.action,
            "output": self.output,
            "error": self.error if self.ran else "; ".join(self.review.blockers),
            "verified": self.verified,
            "verification": self.verification,
            "rollback": list(self.review.rollback),
            "status_line": self.status_line,
            "sections": self.sections(),
            "audit_path": self.audit_path,
        }


def execute(action: str, profile: dict, *,
            run_as_root: vpn_connection.RunAsRoot | None = None,
            probe: dict | None = None,
            killswitch_armed: Callable[[], bool] | None = None,
            audit_path: Path | None = None,
            sleep: Callable[[float], None] = time.sleep) -> ExecutionOutcome:
    """Review again, run only if nothing blocks, check afterwards, and audit."""
    probe = probe or _default_probe()
    review = review_execution(action, profile, probe=probe, killswitch_armed=killswitch_armed)
    kwargs = {"run_as_root": run_as_root} if run_as_root is not None else {}
    if not review.allowed:
        outcome = ExecutionOutcome(review, ran=False, success=False,
                                   error="; ".join(review.blockers))
    else:
        runner = vpn_connection.connect if review.action == "connect" else vpn_connection.disconnect
        result = runner(profile, **kwargs)
        outcome = ExecutionOutcome(
            review, ran=True, success=bool(result.get("success")),
            output=str(result.get("output") or "")[:OUTPUT_LIMIT],
            error=result.get("error"),
        )
        outcome.verified, outcome.verification = post_change_check(review, probe, sleep=sleep)

    written = append_audit({
        "time": outcome.checked_at,
        "action": review.action,
        "protocol": review.protocol,
        "profile": review.profile_name,
        "target": review.target,
        "interface": review.interface,
        "command": review.command,
        "outcome": ("refused" if not outcome.ran
                    else "succeeded" if outcome.success else "failed"),
        "blockers": list(review.blockers),
        "warnings": list(review.warnings),
        "verified": outcome.verified,
        "verification": outcome.verification,
        "error": (str(outcome.error)[:OUTPUT_LIMIT] if outcome.error else None),
    }, audit_path)
    outcome.audit_path = str(written) if written else None
    return outcome

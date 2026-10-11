"""Other VPN apps on this Mac (Mullvad, Proton), read-only.

Tunnel can run a WireGuard or OpenVPN profile itself, but many people already
run a commercial VPN through that provider's own app. This module only *looks*:
whether the app is installed and what state it reports. It never connects,
disconnects, logs in or reads an account.

* **Mullvad VPN** is asked through its official command-line tool
  (``mullvad status``). The daemon keeps the account; Tunnel never sees it.
* **ProtonVPN** registers as a macOS system VPN service, so its state comes from
  ``scutil --nc list``. Tunnel never sees a Proton login either.

Every command is injected (``run``) so tests never touch a real app.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str], float], tuple[int, str]]

MULLVAD_APP = Path("/Applications/Mullvad VPN.app")
MULLVAD_BUNDLED_CLI = MULLVAD_APP / "Contents" / "Resources" / "mullvad"
PROTON_APP = Path("/Applications/ProtonVPN.app")
SCUTIL = "/usr/sbin/scutil"

# States as this module reports them.
CONNECTED, CONNECTING, DISCONNECTING = "connected", "connecting", "disconnecting"
DISCONNECTED, BLOCKED, UNKNOWN = "disconnected", "blocked", "unknown"
ACTIVE_STATES = {CONNECTED, CONNECTING, DISCONNECTING, BLOCKED}


def _run(argv: list[str], timeout: float = 2.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 127, ""
    return proc.returncode, (proc.stdout or "")


@dataclass(frozen=True)
class VpnApp:
    key: str                  # "mullvad" | "proton"
    name: str                 # what to call it in the UI
    installed: bool
    state: str = UNKNOWN
    detail: str = ""

    @property
    def active(self) -> bool:
        """It is carrying (or about to carry) traffic, or its firewall is up."""
        return self.installed and self.state in ACTIVE_STATES

    @property
    def unreadable(self) -> bool:
        """Installed and possibly running, but its state could not be read."""
        return (self.installed and self.state == UNKNOWN
                and "did not answer" not in self.detail)

    def line(self) -> str:
        if not self.installed:
            return f"{self.name}: not installed"
        text = f"{self.name}: {self.state}"
        return f"{text} — {self.detail}" if self.detail else text


# ── Mullvad ─────────────────────────────────────────────────────────────────

def mullvad_cli() -> str | None:
    found = shutil.which("mullvad")
    if found:
        return found
    return str(MULLVAD_BUNDLED_CLI) if MULLVAD_BUNDLED_CLI.is_file() else None


_MULLVAD_STATES = (
    ("disconnecting", DISCONNECTING),
    ("disconnected", DISCONNECTED),
    ("connecting", CONNECTING),
    ("connected", CONNECTED),
    ("blocked", BLOCKED),
)


def parse_mullvad_status(text: str) -> tuple[str, str]:
    """``(state, detail)`` from the first line of ``mullvad status``."""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    lowered = first.lower()
    for prefix, state in _MULLVAD_STATES:
        if lowered.startswith(prefix):
            return state, first[len(prefix):].strip(" :-")[:120]
    return UNKNOWN, first[:120]


def detect_mullvad(run: Runner = _run) -> VpnApp:
    cli = mullvad_cli()
    installed = bool(cli) or MULLVAD_APP.exists()
    if not installed:
        return VpnApp("mullvad", "Mullvad VPN", False)
    if not cli:
        return VpnApp("mullvad", "Mullvad VPN", True, UNKNOWN,
                      "its command-line tool was not found, so its state cannot be read")
    code, out = run([cli, "status"], 2.0)
    if code != 0 and not out.strip():
        return VpnApp("mullvad", "Mullvad VPN", True, UNKNOWN,
                      "its daemon did not answer (is the app running?)")
    state, detail = parse_mullvad_status(out)
    return VpnApp("mullvad", "Mullvad VPN", True, state, detail)


# ── Proton (a macOS system VPN service) ─────────────────────────────────────

_SERVICE = re.compile(
    r'^\s*\*?\s*\((?P<state>[A-Za-z ]+)\)\s+(?P<uuid>[0-9A-Fa-f-]{36})\s+.*?"(?P<name>[^"]*)"(?P<rest>.*)$')


def parse_scutil_services(text: str) -> list[dict]:
    """The system VPN services from ``scutil --nc list``."""
    services = []
    for line in text.splitlines():
        match = _SERVICE.match(line)
        if match:
            services.append({
                "name": match.group("name"),
                "state": match.group("state").strip().lower(),
                "uuid": match.group("uuid"),
                "provider": line,
            })
    return services


def _proton_state(raw: str) -> str:
    return {"connected": CONNECTED, "connecting": CONNECTING,
            "disconnecting": DISCONNECTING, "disconnected": DISCONNECTED}.get(raw, UNKNOWN)


def detect_proton(run: Runner = _run) -> VpnApp:
    code, out = run([SCUTIL, "--nc", "list"], 2.0)
    services = [s for s in parse_scutil_services(out if code == 0 else "")
                if "protonvpn" in s["provider"].lower()]
    installed = PROTON_APP.exists() or bool(services)
    if not installed:
        return VpnApp("proton", "ProtonVPN", False)
    if not services:
        return VpnApp("proton", "ProtonVPN", True, DISCONNECTED,
                      "no VPN service is set up in macOS yet")
    service = services[0]
    return VpnApp("proton", "ProtonVPN", True, _proton_state(service["state"]),
                  f'system VPN service "{service["name"]}"')


# ── Together ────────────────────────────────────────────────────────────────

def detect(run: Runner = _run) -> list[VpnApp]:
    """Every supported app, installed or not (callers show only installed ones)."""
    return [detect_mullvad(run), detect_proton(run)]


def installed_apps(apps: list[VpnApp] | None = None) -> list[VpnApp]:
    return [a for a in (detect() if apps is None else apps) if a.installed]


def killswitch_conflict(apps: list[VpnApp] | None = None) -> str | None:
    """Why Tunnel's kill switch must not be armed right now, or None.

    An app that is connected carries its own tunnel (and, for Mullvad, its own
    firewall rules). Tunnel's pf rules only exempt the *selected profile's*
    server, so arming would block that app's tunnel traffic, and two rule sets
    can block each other. An app whose state cannot be read is treated the same.
    """
    for app in installed_apps(apps):
        if app.active or app.unreadable:
            what = ("is " + app.state) if app.state != UNKNOWN else "could not be read"
            return (f"{app.name} {what}. Its own tunnel and firewall rules are in use, and "
                    "Tunnel's kill switch would block that traffic or fight those rules. "
                    "Use the app's own kill switch setting instead, or disconnect it and "
                    "arm Tunnel's kill switch against a profile you connect from Tunnel.")
    return None


def connect_warning(apps: list[VpnApp] | None = None) -> str | None:
    """A warning for connecting a Tunnel profile while another VPN app is active."""
    for app in installed_apps(apps):
        if app.active:
            return (f"{app.name} is {app.state}. A second tunnel on top of it stacks two "
                    "VPNs and can break routing or DNS; disconnect it first unless you "
                    "want exactly that.")
    return None

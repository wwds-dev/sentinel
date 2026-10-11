"""Real VPN connect/disconnect for Sentinel's Tunnel.

This is the executing counterpart to ``vpn_diagnostics`` (which is read-only). It
brings a tunnel up or down using WireGuard (``wg-quick``) or OpenVPN, always via
an injected ``run_as_root`` so privilege is requested through the macOS
authorisation dialog and tests never touch real ``sudo``. It also refuses to
"connect" an example/placeholder profile rather than pretending to — a real
endpoint and, for OpenVPN, a real config file are required.

Kill-switch arming/disarming is exposed as explicit, separate actions (a pf
anchor via the companion ``killswitch`` service); connect does not silently
change the firewall.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable

from agents.vpn_agent.server import paths as vpn_paths
from agents.vpn_agent.services import wireguard_manager
from services import openvpn_manager
from services.vpn_diagnostics import PROFILE_SEED, _profile_protocol


def _killswitch():
    """Lazily load the pf kill-switch service.

    It currently lives in the companion VPN module and uses an absolute
    ``from server import paths`` that only resolves when that module is merged
    into Sentinel's tree, so importing it can fail; callers degrade gracefully.
    """
    try:
        from agents.vpn_agent.services import killswitch  # noqa: WPS433
        return killswitch
    except Exception:
        return None

try:  # pragma: no cover - real prompt in the app, mocked in tests
    from functools import partial as _partial
    from agents.vpn_agent.services.privileged import run_as_root as _privileged_run_as_root
    # Sentinel's gated actions always go through the macOS dialog (never a
    # cached sudo ticket) — that is what the README and the review promise.
    _default_run_as_root = _partial(_privileged_run_as_root, allow_cached_sudo=False)
except Exception:  # pragma: no cover
    def _default_run_as_root(script: str, prompt: str, timeout: float = 30):
        return False, "Privileged execution is unavailable."

RunAsRoot = Callable[..., tuple[bool, str]]

# Endpoint values that are templates, not real servers.
PLACEHOLDER_ENDPOINTS = {
    "", "0.0.0.0", "<server_ip>", "<server_public_ip_or_ddns>",
    "server.example.com", "example.com",
}


def resolve_protocol(profile: dict) -> str:
    """'WireGuard' | 'OpenVPN' | 'Unknown' (shared with the diagnostics view)."""
    return _profile_protocol(profile or {})


def is_placeholder(profile: dict) -> bool:
    """True when a profile is an example/template, not a connectable server."""
    if not profile:
        return True
    if profile.get("placeholder"):
        return True
    endpoint = str(profile.get("endpoint") or "").strip().lower()
    return endpoint in PLACEHOLDER_ENDPOINTS


def resolve_config_path(profile: dict) -> str | None:
    """The .conf/.ovpn to load, if the profile names one."""
    path = str(profile.get("config_path") or "").strip()
    return path or None


def _quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


def wg_quick_command(verb: str, target: str) -> str:
    """The shell line for ``wg-quick <verb> <target>``, runnable as root.

    The macOS authorisation dialog (``do shell script``) and ``sudo``'s
    secure_path both drop Homebrew's bin directory, so a bare ``wg-quick`` is not
    found there — and wg-quick itself shells out to ``wg`` and ``wireguard-go``.
    Use the absolute path and put its directory first on PATH for that one call.
    """
    exe = shutil.which("wg-quick")
    if not exe:
        return f"wg-quick {verb} {_quote(target)}"
    bindir = os.path.dirname(exe)
    return f'PATH={_quote(bindir)}:"$PATH" {_quote(exe)} {verb} {_quote(target)}'


def _placeholder_result(action: str, profile: dict) -> dict:
    return {
        "success": False,
        "protocol": resolve_protocol(profile),
        "output": "",
        "error": (
            f"'{profile.get('name', 'This profile')}' is an example/template, not a "
            "real server. Import a WireGuard .conf or OpenVPN .ovpn (or provision "
            "your own server) and set its endpoint before you can "
            f"{action}."
        ),
    }


def connect(profile: dict, *, run_as_root: RunAsRoot = _default_run_as_root) -> dict:
    """Bring the profile's tunnel up. Reports what the tool actually returned."""
    protocol = resolve_protocol(profile)
    if is_placeholder(profile):
        return _placeholder_result("connect", profile)

    if protocol == "OpenVPN":
        config_path = resolve_config_path(profile)
        if not config_path:
            return {"success": False, "protocol": protocol, "output": "",
                    "error": "This OpenVPN profile has no config file. Set its "
                             "config_path to a .ovpn file."}
        return {**openvpn_manager.connect(config_path, run_as_root=run_as_root),
                "protocol": protocol}

    if protocol != "WireGuard":
        return {"success": False, "protocol": protocol, "output": "",
                "error": f"Unsupported protocol '{profile.get('protocol')}'."}

    # WireGuard
    if not wireguard_manager.is_wg_quick_available():
        return {"success": False, "protocol": protocol, "output": "",
                "error": "wg-quick not found — install wireguard-tools "
                         "(`brew install wireguard-tools`)."}
    config_path = resolve_config_path(profile)
    interface = str(profile.get("interface") or "").strip()
    target = config_path if config_path else interface
    if not target:
        return {"success": False, "protocol": protocol, "output": "",
                "error": "This WireGuard profile has no interface or config file."}
    ok, output = run_as_root(
        wg_quick_command("up", target),
        "Sentinel needs administrator access to start the VPN.")
    return {"success": ok, "protocol": protocol, "output": output,
            "error": None if ok else (output or "wg-quick up failed.")}


def disconnect(profile: dict, *, run_as_root: RunAsRoot = _default_run_as_root) -> dict:
    """Bring the profile's tunnel down."""
    protocol = resolve_protocol(profile)
    if protocol == "OpenVPN":
        return {**openvpn_manager.disconnect(run_as_root=run_as_root),
                "protocol": protocol}
    if protocol != "WireGuard":
        return {"success": False, "protocol": protocol, "output": "",
                "error": f"Unsupported protocol '{profile.get('protocol')}'."}
    config_path = resolve_config_path(profile)
    interface = str(profile.get("interface") or "").strip()
    target = config_path if config_path else interface
    if not target:
        return {"success": False, "protocol": protocol, "output": "",
                "error": "This WireGuard profile has no interface or config file."}
    ok, output = run_as_root(
        wg_quick_command("down", target),
        "Sentinel needs administrator access to stop the VPN.")
    return {"success": ok, "protocol": protocol, "output": output,
            "error": None if ok else (output or "wg-quick down failed.")}


def connection_status() -> dict:
    """Real snapshot of what is currently up (reuses the read-only helpers)."""
    ks = _killswitch()
    return {
        "wireguard_interfaces": list(wireguard_manager.list_active_tunnels()),
        "openvpn_running": openvpn_manager.is_running(),
        "killswitch_supported": bool(ks and ks.is_supported()),
    }


# ── Kill switch (explicit, separate actions) ────────────────────────────────
# The killswitch service manages its own privilege prompt (a pf anchor), so it
# is not parameterised by run_as_root here; these are thin, honest pass-throughs.

def killswitch_supported() -> bool:
    ks = _killswitch()
    return bool(ks and ks.is_supported())


DEFAULT_TUNNEL_PORTS = {"WireGuard": 51820, "OpenVPN": 1194}


def tunnel_allow_rules(profile: dict) -> list[tuple[str, int]]:
    """The (proto, port) pairs the kill switch must leave open for this profile.

    pf's anchor is default-deny, so without a pass rule for the tunnel's own
    transport the encrypted packets to the server are dropped on the physical
    interface and arming the switch kills the tunnel it is meant to protect.
    WireGuard is always UDP. OpenVPN follows the proto on its ``remote`` line,
    then its ``proto`` line (UDP when absent). The port comes from the profile, else the config file, else
    the protocol's registered default.
    """
    protocol = resolve_protocol(profile)
    config_path = resolve_config_path(profile)
    port = profile.get("port")
    try:
        port = int(port) if port else None
    except (TypeError, ValueError):
        port = None
    if not port and config_path:
        _, port = extract_endpoint(config_path, protocol)
    if not port:
        port = DEFAULT_TUNNEL_PORTS.get(protocol, 51820)
    proto = "udp"
    if protocol == "OpenVPN" and config_path:
        try:
            text = Path(config_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        proto = parse_openvpn_remote(text)[2] or "udp"
    return [(proto, port)]


def arm_killswitch(profile: dict):
    """Arm a leak-blocking pf anchor that exempts this profile's endpoint so the
    tunnel can still reach its server while everything else is blocked."""
    ks = _killswitch()
    if ks is None:
        return False, "Kill switch is unavailable: its module could not be loaded."
    if not ks.is_supported():
        return False, "Kill switch needs pf (macOS)."
    if is_placeholder(profile):
        return False, "Choose a real server profile before arming the kill switch."
    endpoint = str(profile.get("endpoint") or "").strip()
    if not endpoint:
        return False, ("This profile has no server endpoint, so the kill switch "
                       "could not exempt the tunnel. Import a real config first.")
    allow = tunnel_allow_rules(profile)
    if resolve_protocol(profile) == "OpenVPN" and openvpn_manager.is_running():
        # pf only knows WireGuard's utun by itself. An OpenVPN tunnel that is up
        # must be passed by its own device, or arming drops everything inside it.
        device = openvpn_manager.tunnel_device()
        if not device:
            return False, ("OpenVPN is running but its tunnel device could not be read "
                           "from its log, so arming now would block traffic inside the "
                           "tunnel. Reconnect, then arm again.")
        return ks.arm([endpoint], allow=allow, extra_interfaces=[device])
    return ks.arm([endpoint], allow=allow)


def killswitch_recovery_command() -> str:
    """The Terminal command that undoes the pf anchor, for the Arm dialog."""
    ks = _killswitch()
    try:
        return ks.recovery_command() if ks is not None else "sudo pfctl -d"
    except Exception:  # noqa: BLE001 - the dialog must open even if this fails
        return "sudo pfctl -d"


def disarm_killswitch():
    ks = _killswitch()
    if ks is None:
        return False, "Kill switch is unavailable until the VPN module is merged."
    return ks.disarm()


# ── Example country profiles (honest templates, never auto-connectable) ─────

def _profiles_file() -> Path:
    live = vpn_paths.profiles_file()
    return live if live.is_file() else PROFILE_SEED


def load_connectable_profiles(include_examples: bool = True) -> list[dict]:
    """Full profile dicts (with config_path/country) for the connect UI.

    Unlike the diagnostics catalog, this does not strip fields — connect needs
    ``config_path``. Falls back to just the example templates if no file exists.
    """
    profiles: list[dict] = []
    try:
        data = json.loads(_profiles_file().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            profiles = [p for p in data.get("profiles", []) if isinstance(p, dict)]
    except (OSError, ValueError, TypeError):
        profiles = []
    if include_examples:
        existing = {p.get("name") for p in profiles}
        profiles += [p for p in example_country_profiles() if p["name"] not in existing]
    return profiles


def _openvpn_proto(value: str | None) -> str | None:
    """Normalise OpenVPN's proto spellings (tcp-client, tcp4, udp6…) to tcp/udp."""
    if not value:
        return None
    value = value.lower()
    if value.startswith("tcp"):
        return "tcp"
    if value.startswith("udp"):
        return "udp"
    return None


def parse_openvpn_remote(text: str) -> tuple[str | None, int | None, str | None]:
    """The first ``remote`` of an OpenVPN config as (host, port, proto).

    OpenVPN's own precedence: ``remote host [port] [proto]`` wins; a remote
    without a port takes ``rport``/``port``, then 1194 (left to the caller);
    a remote without a proto takes the ``proto`` line, then UDP (left to the
    caller). Only the first remote is read — a config with failover remotes
    reaches the others only while the kill switch is disarmed.
    """
    remote = re.search(r"(?im)^[ \t]*remote[ \t]+(\S+)(?:[ \t]+(\d+))?(?:[ \t]+(\S+))?", text)
    if not remote:
        return None, None, None
    host = remote.group(1)
    port = int(remote.group(2)) if remote.group(2) else None
    proto = _openvpn_proto(remote.group(3))
    if port is None:
        default_port = (re.search(r"(?im)^[ \t]*rport[ \t]+(\d+)", text)
                        or re.search(r"(?im)^[ \t]*port[ \t]+(\d+)", text))
        if default_port:
            port = int(default_port.group(1))
    if proto is None:
        line = re.search(r"(?im)^[ \t]*proto[ \t]+(\S+)", text)
        proto = _openvpn_proto(line.group(1)) if line else None
    return host, port, proto


def extract_endpoint(config_path: str, protocol: str) -> tuple[str | None, int | None]:
    """Read the server host/port from a WireGuard `.conf` or OpenVPN `.ovpn`.

    The kill switch needs the real endpoint to exempt (otherwise arming would
    block the tunnel from ever reaching its server). Returns (host, port) or
    (None, None) when it cannot be determined.
    """
    try:
        text = Path(config_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    if protocol == "OpenVPN":
        host, port, _ = parse_openvpn_remote(text)
        return host, port
    # WireGuard: Endpoint = host:port  (also handle [IPv6]:port)
    match = re.search(r"(?im)^\s*Endpoint\s*=\s*(.+?)\s*$", text)
    if not match:
        return None, None
    value = match.group(1).strip()
    ipv6 = re.match(r"^\[(.+)\]:(\d+)$", value)
    if ipv6:
        return ipv6.group(1), int(ipv6.group(2))
    if ":" in value:
        host, _, port = value.rpartition(":")
        if port.isdigit():
            return host, int(port)
    return value or None, None


def profile_from_config(path: str) -> dict:
    """Build a connectable profile from an imported WireGuard/OpenVPN file."""
    p = Path(path)
    protocol = "OpenVPN" if p.suffix.lower() == ".ovpn" else "WireGuard"
    host, port = extract_endpoint(str(p), protocol)
    profile = {
        "name": f"Imported — {p.stem}",
        "protocol": protocol,
        "config_path": str(p),
        "interface": p.stem if protocol == "WireGuard" else "",
        # A real host lets the kill switch exempt the tunnel; fall back to a
        # non-placeholder marker so connect still works via the config file.
        "endpoint": host or "imported",
        "notes": f"Imported from {p.name}",
    }
    if port:
        profile["port"] = port
    return profile


def save_profile(profile: dict) -> None:
    """Persist a profile to the writable profiles file (replacing same-name)."""
    live = vpn_paths.profiles_file()
    try:
        data = json.loads(live.read_text(encoding="utf-8")) if live.is_file() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    kept = [p for p in data.get("profiles", [])
            if isinstance(p, dict) and p.get("name") != profile.get("name")]
    kept.append(profile)
    data["profiles"] = kept
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(json.dumps(data, indent=4), encoding="utf-8")


def example_country_profiles() -> list[dict]:
    """A few country-labelled templates. They are explicit placeholders: the
    connect layer refuses them until the user fills in a real endpoint/config."""
    countries = [
        ("Netherlands", "NL", "wgnl"),
        ("United States", "US", "wgus"),
        ("Japan", "JP", "wgjp"),
        ("Germany", "DE", "wgde"),
        ("Switzerland", "CH", "wgch"),
    ]
    return [
        {
            "name": f"Example — {name}",
            "country": name,
            "country_code": code,
            "protocol": "WireGuard",
            "endpoint": "<SERVER_IP>",
            "port": 51820,
            "interface": iface,
            "placeholder": True,
            "notes": (
                f"Template for a {name} exit. Import a real WireGuard .conf "
                "or OpenVPN .ovpn (or provision your own server) and replace the "
                "endpoint to make it connectable."
            ),
        }
        for name, code, iface in countries
    ]

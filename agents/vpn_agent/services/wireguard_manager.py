"""
wireguard_manager.py — macOS-safe WireGuard tunnel control.

On macOS, WireGuard tunnels managed by the WireGuard app live in:
  /var/run/wireguard/<interface>.name

Control options on macOS:
  1. wg-quick (if installed via Homebrew: brew install wireguard-tools)
  2. WireGuard.app (manages tunnels via its own daemon — limited CLI)
  3. networksetup / scutil (system-level, not WireGuard-specific)

This module drives wg-quick directly, so it needs wireguard-tools present and
sudo for anything that changes state. Every function reports a missing binary as
an error rather than raising, because the GUI shows these results in a panel.

This is the *client* side. Creating the server a tunnel connects to is the
server/ package, which shares nothing with this module — it never shells out to
wg at all, so a server can be built from a Mac with no WireGuard installed.
"""

import subprocess
import os
import shutil

# wg-quick's and the WireGuard app's run directory (sockets and name records).
WG_RUN_DIR = "/var/run/wireguard"


def is_wg_quick_available() -> bool:
    """Return True if wg-quick is installed and accessible in PATH."""
    return shutil.which("wg-quick") is not None


def is_wg_available() -> bool:
    """Return True if the wg CLI tool is installed (for status reads)."""
    return shutil.which("wg") is not None


def get_tunnel_status(interface: str) -> dict:
    """
    Check whether a WireGuard interface is currently active.
    Uses `wg show <interface>` — requires wg to be installed.
    Returns dict with: interface, active (bool), details (str), error (str or None)
    """
    result = {"interface": interface, "active": False, "details": "", "error": None}

    if not is_wg_available():
        result["error"] = "wg CLI not found — install wireguard-tools via Homebrew"
        return result

    try:
        proc = subprocess.run(
            ["wg", "show", interface],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            result["active"] = True
            result["details"] = proc.stdout.strip()
        else:
            # Non-zero exit usually means interface does not exist / not active
            result["active"] = False
            result["details"] = proc.stderr.strip()
    except subprocess.TimeoutExpired:
        result["error"] = "wg show timed out"
    except Exception as e:
        result["error"] = str(e)

    return result


def connect(interface: str, config_path: str | None = None) -> dict:
    """Disabled. This ran ``sudo wg-quick up`` with no review or confirmation."""
    raise RuntimeError(
        "Ungated sudo wg-quick is disabled: use services.vpn_execution.execute, which "
        "reviews the config and asks for confirmation first.")


def disconnect(interface: str) -> dict:
    """Disabled. This ran ``sudo wg-quick down`` with no review or confirmation."""
    raise RuntimeError(
        "Ungated sudo wg-quick is disabled: use services.vpn_execution.execute, which "
        "reviews the config and asks for confirmation first.")


def restart(interface: str) -> dict:
    """Disconnect then reconnect a tunnel."""
    down = disconnect(interface)
    if not down["success"] and down["error"]:
        return {"success": False, "output": down["output"], "error": f"Down failed: {down['error']}"}
    up = connect(interface)
    return up


def list_active_tunnel_names(run_dir: str | None = None) -> list[str]:
    """
    The wg-quick names (the config's name, e.g. ``wg0``) of tunnels that are up.

    On macOS the socket in the run directory is named after the kernel's utun
    (``utun4.sock``), not the config, so list_active_tunnels() never contains a
    profile's name. wg-quick records the mapping in ``<name>.name``: a name is
    up while that record exists and its utun's socket does. A record that
    cannot be read still counts as up (its presence is the signal, as in
    services/vpn_execution.wireguard_interface_state). Reads files only.
    """
    run_dir = run_dir or WG_RUN_DIR
    try:
        entries = os.listdir(run_dir)
    except OSError:
        return []
    names: list[str] = []
    for entry in entries:
        if not entry.endswith(".name"):
            continue
        name = entry[: -len(".name")]
        try:
            with open(os.path.join(run_dir, entry), encoding="utf-8") as handle:
                device = handle.read().strip()
        except OSError:
            device = ""
        if device and not os.path.exists(os.path.join(run_dir, f"{device}.sock")):
            continue        # stale record: the tunnel's device is gone
        if name and name not in names:
            names.append(name)
    return names


def list_active_tunnels() -> list[str]:
    """
    Return a list of currently active WireGuard interface names.
    Reads /var/run/wireguard/ — WireGuard app and wg-quick both write sockets here.
    Returns empty list if the directory doesn't exist or is empty.
    """
    wg_run_dir = WG_RUN_DIR
    if not os.path.isdir(wg_run_dir):
        return []
    try:
        # Socket files end in .sock — each represents one active tunnel
        return [
            f.replace(".sock", "")
            for f in os.listdir(wg_run_dir)
            if f.endswith(".sock")
        ]
    except Exception:
        return []

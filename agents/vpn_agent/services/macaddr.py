"""
macaddr.py — Changing this Mac's hardware address.

Worth being blunt about the scope, because this is the most over-estimated
privacy measure in common use:

  A MAC address travels exactly one hop. The café's access point sees it, your
  home router sees it, and nothing beyond that ever does — it is stripped and
  replaced at the first router. It has no bearing on what a website sees, on
  what your ISP sees, or on anything the VPN is for.

  What it is genuinely good for: not being trackable *by the network you are
  joining*. Venue wifi that logs MACs can otherwise recognise the same laptop
  across visits, and across venues under the same operator.

  On recent macOS, Wi-Fi already does this for you. "Private Wi-Fi Address" is
  on by default and gives every SSID its own stable random address, which is a
  better design than one address you change by hand. Check Settings › Wi-Fi
  before assuming this adds anything. Ethernet and USB adapters get no such
  treatment, and that is where this earns its place.

  The change does not survive a reboot, and re-associating is required for it
  to take effect on Wi-Fi.
"""

from __future__ import annotations

import re
import secrets
import shlex
import subprocess
from dataclasses import dataclass

from agents.vpn_agent.services.privileged import run, run_as_root

MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9A-Fa-f]{2}$")

MODE_LOCAL = "locally-administered"
MODE_SAME_VENDOR = "same-vendor"
MODES = (MODE_LOCAL, MODE_SAME_VENDOR)


@dataclass
class Interface:
    device: str                 # en0
    port: str = ""              # "Wi-Fi"
    current: str = ""           # what it is using now
    hardware: str = ""          # what it was born with

    @property
    def is_wifi(self) -> bool:
        return "wi-fi" in self.port.lower() or "airport" in self.port.lower()

    @property
    def spoofed(self) -> bool:
        return bool(self.current and self.hardware
                    and self.current.lower() != self.hardware.lower())

    def describe(self) -> str:
        label = f"{self.device} ({self.port})" if self.port else self.device
        return f"{label} — {self.current or 'no address'}" + (
            "  [changed]" if self.spoofed else ""
        )


# ── Discovery ────────────────────────────────────


def list_interfaces() -> list[Interface]:
    """
    Enumerate hardware ports and their addresses.

    `networksetup -listallhardwareports` reports the *permanent* address burned
    into the hardware, while `ifconfig` reports what is in use right now. Both
    are needed: the difference between them is how we know an interface has
    already been changed, and the permanent one is what "restore" means.
    """
    interfaces: list[Interface] = []
    ok, output = run(["networksetup", "-listallhardwareports"])
    if not ok:
        return interfaces

    port = ""
    device = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
            device = ""
        elif line.startswith("Device:"):
            device = line.split(":", 1)[1].strip()
        elif line.startswith("Ethernet Address:") and device:
            hardware = line.split(":", 1)[1].strip()
            if MAC_RE.match(hardware):
                interfaces.append(
                    Interface(device=device, port=port, hardware=hardware.lower(),
                              current=current_mac(device))
                )
            device = ""
    return interfaces


def current_mac(device: str) -> str:
    ok, output = run(["ifconfig", device])
    if not ok:
        return ""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ether "):
            return stripped.split()[1].lower()
    return ""


def get_interface(device: str) -> Interface | None:
    for interface in list_interfaces():
        if interface.device == device:
            return interface
    return None


# ── Generating an address ────────────────────────


def random_mac(mode: str = MODE_LOCAL, like: str = "") -> str:
    """
    Generate an address.

    MODE_LOCAL sets the locally-administered bit and clears the multicast bit,
    which is the correct way to mint an address that cannot collide with a real
    manufacturer's. It is also, by construction, visibly not a real vendor —
    a network operator looking for spoofed clients can spot it.

    MODE_SAME_VENDOR keeps the first three octets of the existing address and
    randomises the rest, so the interface still looks like the same make of
    hardware. Less conspicuous, at the cost of using an OUI that is not ours to
    use.
    """
    if mode == MODE_SAME_VENDOR and MAC_RE.match(like or ""):
        prefix = like.lower().split(":")[:3]
        suffix = [f"{secrets.randbelow(256):02x}" for _ in range(3)]
        return ":".join(prefix + suffix)

    # Bit 0 clear = unicast; bit 1 set = locally administered.
    first = (secrets.randbelow(256) & 0xFE) | 0x02
    rest = [f"{secrets.randbelow(256):02x}" for _ in range(5)]
    return ":".join([f"{first:02x}", *rest])


def is_valid(address: str) -> bool:
    if not MAC_RE.match(address or ""):
        return False
    first = int(address.split(":")[0], 16)
    # A multicast address as a source is invalid and some drivers reject it.
    return not (first & 0x01)


# ── Applying it ──────────────────────────────────


def set_mac(device: str, address: str) -> tuple[bool, str]:
    """
    Change an interface's address.

    Wi-Fi has to be disassociated first — the driver will not accept a new
    address while joined to a network, and does so quietly enough that it looks
    like it worked. Cycling power on the interface is the reliable way to force
    it on current macOS, where the old `airport -z` tool no longer exists.
    """
    if not is_valid(address):
        return False, f"{address!r} is not a usable unicast MAC address."

    interface = get_interface(device)
    if interface is None:
        return False, f"No such interface: {device}"

    dev = shlex.quote(device)
    steps = []
    if interface.is_wifi:
        steps.append(f"/usr/sbin/networksetup -setairportpower {dev} off")
        steps.append("sleep 1")
    steps.append(f"/sbin/ifconfig {dev} ether {shlex.quote(address)}")
    if interface.is_wifi:
        steps.append(f"/usr/sbin/networksetup -setairportpower {dev} on")

    # Sentinel's gated actions always use the macOS dialog, never a cached
    # sudo ticket left by some unrelated command.
    ok, output = run_as_root(
        "; ".join(steps), f"Change the hardware address of {device}",
        allow_cached_sudo=False,
    )
    if not ok:
        return False, output or "The change was refused."

    applied = current_mac(device)
    if applied.lower() != address.lower():
        return False, (
            f"The command was accepted but {device} still reports {applied or 'nothing'}. "
            "Some adapters and virtual interfaces silently ignore the change."
        )

    note = ""
    if interface.is_wifi:
        note = " Wi-Fi was cycled, so you may need to rejoin your network."
    return True, f"{device} is now {applied}.{note}"


def randomize(device: str, mode: str = MODE_LOCAL) -> tuple[bool, str]:
    interface = get_interface(device)
    if interface is None:
        return False, f"No such interface: {device}"
    return set_mac(device, random_mac(mode, like=interface.hardware))


def restore(device: str) -> tuple[bool, str]:
    """Put back the address the hardware shipped with."""
    interface = get_interface(device)
    if interface is None:
        return False, f"No such interface: {device}"
    if not interface.hardware:
        return False, f"The permanent address of {device} is unknown."
    if not interface.spoofed:
        return True, f"{device} is already using its hardware address."
    return set_mac(device, interface.hardware)


def wifi_private_address_note() -> str:
    """
    Guidance rather than detection.

    Whether "Private Wi-Fi Address" is on is per-network and lives in a
    preference store that is not reliably readable without elevated access, so
    the honest thing is to say where to look rather than to guess.
    """
    return (
        "macOS already gives each Wi-Fi network its own random address when "
        "'Private Wi-Fi Address' is on (Settings › Wi-Fi › your network › Details). "
        "That is stable per network and harder to correlate than one address you "
        "change by hand — so for Wi-Fi, leave it on and use this for Ethernet or "
        "USB adapters, which get no such treatment."
    )

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

import functools
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from agents.vpn_agent.server import paths
from agents.vpn_agent.services.privileged import run, run_argv_as_root

MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9A-Fa-f]{2}$")
# BSD interface names: a short lowercase driver name and a unit number. \Z,
# not $, so a trailing newline is not let through.
DEVICE_RE = re.compile(r"^[a-z]{2,8}[0-9]{1,3}\Z")

# The whole root side of a change. A constant: the interface, the address and
# whether it is Wi-Fi arrive as $1-$3, so nothing a caller passes is ever
# parsed as shell. Wi-Fi power-on is the EXIT trap, so it runs last whether
# the change worked, failed or the shell was told to stop; the exit status is
# the first failure (ifconfig's error is no longer hidden behind power-on), and
# a power-on failure fails a change that otherwise worked.
ROOT_SCRIPT = """\
dev=$1; addr=$2; wifi=$3
power_on() {
  rc=$?
  if [ "$wifi" = 1 ]; then
    /usr/sbin/networksetup -setairportpower "$dev" on || {
      echo "Wi-Fi could not be switched back on." >&2
      [ "$rc" -eq 0 ] && rc=98
    }
  fi
  exit "$rc"
}
trap power_on EXIT
trap "exit 143" TERM INT HUP
if [ "$wifi" = 1 ]; then
  /usr/sbin/networksetup -setairportpower "$dev" off || exit
  /bin/sleep 1
fi
/sbin/ifconfig "$dev" ether "$addr"
"""

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


def wifi_power(device: str) -> str:
    """'On', 'Off', or '' when it cannot be read (or the device is not Wi-Fi)."""
    ok, output = run(["networksetup", "-getairportpower", device])
    if not ok:
        return ""
    word = output.strip().rsplit(":", 1)[-1].strip() if ":" in output else ""
    return word if word in ("On", "Off") else ""


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


def valid_device(device: str) -> bool:
    return bool(DEVICE_RE.match(device or ""))


def is_valid(address: str) -> bool:
    if not MAC_RE.match(address or ""):
        return False
    first = int(address.split(":")[0], 16)
    # A multicast address as a source is invalid and some drivers reject it.
    return not (first & 0x01)


# ── The record of what Sentinel changed ──────────
#
# Tunnel rule: record the original address, put it back on exit and after a
# crash. The app leaves through os._exit (ADR 0001), so nothing in memory
# survives a quit, let alone a crash: the original is written to disk *before*
# the root command runs, and main._leave and the next launch read it back.
#
# Each entry also names the interface's hardware address and the boot it was
# made in. An ifconfig change never survives a reboot, and on current macOS
# the "original" is usually a private Wi-Fi address that the next boot
# replaces, so a record from an earlier boot describes nothing still in place
# and is dropped. A BSD name can be reused by another adapter, so an entry
# whose hardware no longer matches is dropped too.

_lock = threading.RLock()            # the record file and _in_flight
_in_flight: set[str] = set()         # devices whose root command has not returned


def record_path() -> Path:
    return paths.state_dir() / "mac_original.json"


@functools.cache
def boot_id() -> str:
    """kern.boottime's seconds, or '' when it cannot be read (then nothing is dropped)."""
    ok, output = run(["sysctl", "-n", "kern.boottime"])
    match = re.search(r"\bsec = (\d+)", output) if ok else None
    return match.group(1) if match else ""


def _valid_entry(device, entry) -> bool:
    return (isinstance(device, str) and valid_device(device) and isinstance(entry, dict)
            and isinstance(entry.get("original"), str) and is_valid(entry["original"])
            and isinstance(entry.get("hardware"), str) and bool(MAC_RE.match(entry["hardware"]))
            and isinstance(entry.get("boot"), str))


def _read_entries() -> tuple[dict[str, dict[str, str]], bool]:
    """(the valid entries of this boot, whether the file holds anything else).

    Entries that are not a valid interface name and addresses are dropped:
    the file must never be a way to steer the root helper.
    """
    try:
        data = json.loads(record_path().read_text())
    except (OSError, ValueError):
        return {}, record_path().exists()
    if not isinstance(data, dict):
        return {}, True
    entries = {device: {key: entry[key].lower() if key != "boot" else entry[key]
                        for key in ("original", "hardware", "boot")}
               for device, entry in data.items() if _valid_entry(device, entry)}
    if entries:
        boot = boot_id()
        entries = {device: entry for device, entry in entries.items()
                   if not (boot and entry["boot"] and entry["boot"] != boot)}
    return entries, len(entries) != len(data)


def recorded() -> dict[str, str]:
    """device -> the address it had before Sentinel first changed it, this boot.

    Reads one file (and, when it holds entries, the boot time), so a quit with
    no change pending costs nothing.
    """
    return {device: entry["original"] for device, entry in _read_entries()[0].items()}


def in_flight() -> frozenset[str]:
    with _lock:
        return frozenset(_in_flight)


def _save_record(entries: dict[str, dict[str, str]]) -> None:
    path = record_path()
    if not entries:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mac_original.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(entries, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _remember(device: str, original: str, hardware: str) -> None:
    with _lock:
        entries, _ = _read_entries()
        if device not in entries:          # the first original wins
            entries[device] = {"original": original.lower(), "hardware": hardware.lower(),
                               "boot": boot_id()}
            _save_record(entries)


def _forget(device: str) -> None:
    with _lock:
        entries, stale = _read_entries()
        if entries.pop(device, None) is not None or stale:
            _save_record(entries)


def pending_restores() -> list[tuple[str, str, str]]:
    """(device, original, current) for each recorded change still in place.

    An interface already back on its original is forgotten, and so is one
    whose hardware no longer matches (another adapter under the same name).
    One that cannot be read now (an unplugged adapter) is kept but not
    offered: there is nothing to restore it on. A device whose own change is
    still waiting for root (a Quit anyway during the dialog) is left alone:
    its outcome is unknown, and the next launch decides.
    """
    with _lock:
        entries, stale = _read_entries()
        if stale:
            _save_record(entries)
        busy = set(_in_flight)
    entries = {device: entry for device, entry in entries.items() if device not in busy}
    if not entries:
        return []
    ports = {interface.device: interface for interface in list_interfaces()}
    pending = []
    for device, entry in entries.items():
        port = ports.get(device)
        if port is None or not port.current:
            continue
        if port.hardware != entry["hardware"] or port.current == entry["original"]:
            _forget(device)
        else:
            pending.append((device, entry["original"], port.current))
    return pending


def restore_all_recorded() -> list[tuple[str, bool, str]]:
    """Put every recorded original back; a refused one stays recorded."""
    results = []
    for device, original, _ in pending_restores():
        # "original", not "hardware": it is often a private Wi-Fi address.
        ok, message = set_mac(device, original,
                              prompt=f"Put back the original address of {device}")
        results.append((device, ok, message))
    return results


# ── Applying it ──────────────────────────────────


def set_mac(device: str, address: str, *, prompt: str = "") -> tuple[bool, str]:
    """
    Change an interface's address.

    Wi-Fi has to be disassociated first — the driver will not accept a new
    address while joined to a network, and does so quietly enough that it looks
    like it worked. Cycling power on the interface is the reliable way to force
    it on current macOS, where the old `airport -z` tool no longer exists.
    """
    if not valid_device(device):
        return False, f"{device!r} is not a valid interface name."
    if not is_valid(address):
        return False, f"{address!r} is not a usable unicast MAC address."

    interface = get_interface(device)
    if interface is None:
        return False, f"No such interface: {device}"
    if not interface.current:
        return False, (f"Sentinel could not read the address {device} is using now, so it "
                       "could not record it for restoring. Nothing was changed.")

    address = address.lower()
    with _lock:
        _in_flight.add(device)
    try:
        # Recorded before root runs: a crash after the change must still find
        # it. Going back to the hardware address needs no record (and must not
        # record someone else's change as ours to put back).
        if address != interface.hardware:
            _remember(device, interface.current, interface.hardware)

        # Sentinel's gated actions always use the macOS dialog, never a cached
        # sudo ticket left by some unrelated command.
        ok, output = run_argv_as_root(
            ["/bin/bash", "-c", ROOT_SCRIPT, "sentinel-mac", device, address,
             "1" if interface.is_wifi else "0"],
            prompt or f"Change the hardware address of {device}",
            allow_cached_sudo=False,
        )
        applied = current_mac(device)
        # Only a finished command or a cancelled dialog is a known outcome. A
        # timeout can land after this read (root keeps running once the
        # password is in), so the record stays until pending_restores sees
        # the original back.
        settled = ok or output == "Cancelled."
        if settled and applied and applied in (interface.hardware, recorded().get(device)):
            _forget(device)
    finally:
        with _lock:
            _in_flight.discard(device)
    if not ok:
        # Timed out, refused or failed partway: say what the interface is
        # doing now, since the root side may still have run.
        state = f" {device} reports {applied or 'no address'}"
        if interface.is_wifi:
            state += f"; Wi-Fi is {wifi_power(device) or 'in an unknown state'}"
        return False, f"{output or 'The change was refused.'}{state}."

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
    if not interface.current:
        # Empty is unknown, not "unchanged": `spoofed` is False for it.
        return False, f"Sentinel could not read the address {device} is using now."
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

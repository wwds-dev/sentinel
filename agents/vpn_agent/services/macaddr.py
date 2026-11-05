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

import contextlib
import json
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from agents.vpn_agent.server import paths
from agents.vpn_agent.services.privileged import ROOT_REFUSAL, run, run_argv_as_root

MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9A-Fa-f]{2}$")
# Anything address-shaped, failing toward hiding: six or more colon or dash
# groups of one or two hex digits (padded or not, glued to a word or not),
# Cisco's dotted form, or twelve bare hex digits.
_ANY_MAC_RE = re.compile(
    r"(?i)[0-9a-f]{1,2}(?:[:-][0-9a-f]{1,2}){5,}"
    r"|(?<![0-9a-f])[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}(?![0-9a-f])"
    r"|(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
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

# The only mode. A "same-vendor" mode (keep the real OUI) was removed on
# 2026-10-28: it broke the rule that every address is locally administered.
MODE_LOCAL = "locally-administered"
MODES = (MODE_LOCAL,)


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
    """The interfaces, or [] when they cannot be listed (see list_interfaces_checked)."""
    return list_interfaces_checked()[0]


def list_interfaces_checked() -> tuple[list[Interface], str]:
    """
    Enumerate hardware ports and their addresses: (interfaces, error).

    ``error`` is empty on success and says why when the listing itself failed,
    so "could not look" is not mistaken for "nothing there".

    `networksetup -listallhardwareports` reports the *permanent* address burned
    into the hardware, while `ifconfig` reports what is in use right now. Both
    are needed: the difference between them is how we know an interface has
    already been changed, and the permanent one is what "restore" means.
    """
    interfaces: list[Interface] = []
    ok, output = run(["networksetup", "-listallhardwareports"])
    if not ok:
        return interfaces, redact_addresses((output or "networksetup failed").strip())[:200]

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
    return interfaces, ""


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


def _find_interface(device: str) -> tuple[Interface | None, str]:
    """(interface, refusal): one listing decides both, so the wording cannot disagree with it."""
    interface, error = read_interface(device)
    if interface is not None:
        return interface, ""
    if error:
        return None, f"Could not read the network interfaces: {error.rstrip('.')}. Nothing was changed."
    return None, f"No such interface: {device}"


def read_interface(device: str) -> tuple[Interface | None, str]:
    """(the interface, error): ``(None, "")`` means it is not there, a
    non-empty error means the listing failed and nothing is known."""
    interfaces, error = list_interfaces_checked()
    if error:
        return None, error
    for interface in interfaces:
        if interface.device == device:
            return interface, ""
    return None, ""


# ── Generating an address ────────────────────────


def random_mac(mode: str = MODE_LOCAL, like: str = "") -> str:
    """
    Generate a locally administered unicast address.

    Sets the locally-administered bit and clears the multicast bit, which is
    the correct way to mint an address that cannot collide with a real
    manufacturer's. It is also, by construction, visibly not a real vendor.

    ``mode`` and ``like`` are accepted only so an old caller or saved setting
    that still names the removed "same-vendor" mode falls back to this one:
    neither changes the result, and a real OUI is never copied.
    """
    # Bit 0 clear = unicast; bit 1 set = locally administered.
    first = (secrets.randbelow(256) & 0xFE) | 0x02
    rest = [f"{secrets.randbelow(256):02x}" for _ in range(5)]
    return ":".join([f"{first:02x}", *rest])


def redact_addresses(text: str) -> str:
    """``text`` with every hardware address replaced, for output that is kept
    (stderr goes to a launch log file). The device name and the reason stay.
    Matches the colon and dash forms (padded or not, any run of six or more
    groups), the dotted form and twelve bare hex digits; something else that
    looks like one (part of an IPv6 address, the tail of a UUID) is hidden too."""
    return _ANY_MAC_RE.sub("<address>", text or "")


def valid_device(device: str) -> bool:
    return bool(DEVICE_RE.match(device or ""))


def is_valid(address: str) -> bool:
    if not MAC_RE.match(address or ""):
        return False
    first = int(address.split(":")[0], 16)
    if first & 0x01:        # multicast, and so broadcast: invalid as a source
        return False
    return int(address.replace(":", ""), 16) != 0     # all zeros is not an address


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
_in_flight: dict[str, int] = {}      # device -> changes listing or waiting for root
# The root helper's whole argument tail ($0, device, address, Wi-Fi flag) in
# any process (the dialog's osascript carries it); a change from a process
# that has since quit. The full shape, so a grep that mentions it is not one.
_ELSEWHERE_RE = re.compile(
    r"\bsentinel-mac ([a-z]{2,8}[0-9]{1,3}) [0-9a-f]{2}(?::[0-9a-f]{2}){5} [01]\b")


def record_path() -> Path:
    return paths.state_dir() / "mac_original.json"


class RecordUnreadable(OSError):
    """The record (or its folder) is there but cannot be read.

    Never "nothing recorded": it may hold an original still to be put back,
    so nothing reads past it and nothing saves over it.
    """


BOOT_UUID_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\Z")


_boot: list[str] = []                # the boot ID once read; a failed read is not kept


def boot_id() -> str:
    """kern.bootsessionuuid, or '' when it cannot be read (then nothing is dropped).

    Not kern.boottime: XNU shifts that on wake and on a clock step to keep
    boot time plus uptime equal to the wall clock, so within one boot its
    seconds can change and a live record would be taken for a stale one.
    Only a good read is cached: a cached failure would make every later
    record boot-less, and a boot-less entry survives reboots.
    """
    if not _boot:
        ok, output = run(["sysctl", "-n", "kern.bootsessionuuid"])
        uuid = output.strip().upper() if ok else ""
        if not BOOT_UUID_RE.match(uuid):
            return ""
        _boot.append(uuid)
    return _boot[0]


boot_id.cache_clear = _boot.clear    # type: ignore[attr-defined]


def _valid_entry(device, entry) -> bool:
    return (isinstance(device, str) and valid_device(device) and isinstance(entry, dict)
            and isinstance(entry.get("original"), str) and is_valid(entry["original"])
            and isinstance(entry.get("hardware"), str) and bool(MAC_RE.match(entry["hardware"]))
            and isinstance(entry.get("boot"), str))


def _read_entries() -> tuple[dict[str, dict[str, str]], bool]:
    """(the valid entries of this boot, whether the file holds anything else).

    Entries that are not a valid interface name and addresses are dropped:
    the file must never be a way to steer the root helper. Raises
    RecordUnreadable when the file or its folder cannot be read; asking
    ``exists()`` instead raised for a folder that cannot be searched (3.11-3.13)
    or answered False (3.14), which read as nothing recorded.
    """
    path = record_path()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, NotADirectoryError):
        return {}, False
    except OSError as exc:
        raise RecordUnreadable(
            exc.errno, f"could not read {path.name} in {path.parent}: "
            f"{exc.strerror or type(exc).__name__}") from exc
    except (ValueError, RecursionError):              # deep nesting: damaged
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    entries = {device: {key: entry[key].lower() if key != "boot" else entry[key]
                        for key in ("original", "hardware", "boot")}
               for device, entry in data.items() if _valid_entry(device, entry)}
    if entries:
        # A known boot drops every entry not made in it, a boot-less one too
        # (older versions wrote those); an unknown boot is no proof of a reboot.
        boot = boot_id()
        entries = {device: entry for device, entry in entries.items()
                   if not (boot and entry["boot"] != boot)}
    return entries, len(entries) != len(data)


def recorded() -> dict[str, str]:
    """device -> the address it had before Sentinel first changed it, this boot.

    Reads one file (and, when it holds entries, the boot ID), so a quit with
    no change pending costs nothing. Raises RecordUnreadable.
    """
    return {device: entry["original"] for device, entry in _read_entries()[0].items()}


def in_flight() -> frozenset[str]:
    with _lock:
        return frozenset(_in_flight)


@contextlib.contextmanager
def _busy(device: str):
    """Mark ``device`` in flight, from its listing until root has answered.

    A count, not a set: the first of two overlapping changes to finish must
    not clear the mark of the other.
    """
    with _lock:
        _in_flight[device] = _in_flight.get(device, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _in_flight[device] -= 1
            if not _in_flight[device]:
                del _in_flight[device]


def _changes_elsewhere() -> set[str] | None:
    """Devices with a change waiting in any process, None when ps fails.

    The admin dialog outlives os._exit: a Quit anyway leaves it open, and the
    next launch's _in_flight knows nothing of it.
    """
    try:
        ok, output = run(["ps", "-axww", "-o", "command="])
    except ValueError:              # undecodable output: as unreadable as a failure
        return None
    return set(_ELSEWHERE_RE.findall(output)) if ok else None


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
        # The first original wins, unless the entry is another adapter's
        # under a reused name: pending_restores would drop it, leaving this
        # change unrecorded.
        if (entries.get(device) or {}).get("hardware") != hardware.lower():
            entries[device] = {"original": original.lower(), "hardware": hardware.lower(),
                               "boot": boot_id()}
            _save_record(entries)


def _forget(device: str) -> None:
    with _lock:
        try:
            entries, stale = _read_entries()
        except RecordUnreadable:
            return              # left as it is: saving would replace the unread record
        if entries.pop(device, None) is not None or stale:
            _save_record(entries)


HELD_BUSY = ("a change to it was still waiting for an answer, so it was left as it is; "
             "it stays recorded and is offered again at the next launch")
HELD_NO_BOOT = ("Sentinel could not read this Mac's boot ID, so it cannot tell whether the "
                "record is from before a restart; it stays recorded and is offered again "
                "once the boot ID can be read")


def pending_restores() -> list[tuple[str, str, str]]:
    """(device, original, current) for each recorded change still in place.

    An interface already back on its original is forgotten, and so is one
    whose hardware no longer matches (another adapter under the same name).
    One that cannot be read now (an unplugged adapter) is kept but not
    offered: there is nothing to restore it on. A device whose change is
    still listing or waiting for root, in this process or in one that quit
    with its dialog open, is left alone: its outcome is unknown, and the next
    launch decides. While the boot ID cannot be read, nothing is offered: an
    entry kept then may be an earlier boot's, and putting its address back
    would replace one Sentinel never changed. Raises RecordUnreadable, and
    then forgets nothing.
    """
    return _pending()[0]


def _pending() -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """pending_restores, plus (device, why) for each changed device it held back."""
    with _lock:
        entries, stale = _read_entries()
        if stale:
            _save_record(entries)
        busy = set(_in_flight)
    if not entries:
        return [], []
    # Unknown (None): an orphaned dialog may still land, so nothing back on
    # its original is forgotten; a changed one is still offered.
    elsewhere = _changes_elsewhere()
    busy |= elsewhere or set()
    held = [(device, HELD_BUSY) for device in sorted(entries) if device in busy]
    entries = {device: entry for device, entry in entries.items() if device not in busy}
    if not entries:
        return [], held
    boot = boot_id()            # read again: the record's read may have found none
    ports = {interface.device: interface for interface in list_interfaces()}
    pending = []
    for device, entry in entries.items():
        port = ports.get(device)
        if port is None or not port.current:
            continue
        if port.hardware != entry["hardware"]:
            _forget(device)
        elif port.current == entry["original"]:
            with _lock:
                # Again, just before forgetting: a change may have begun here
                # since the snapshot, and its record is the one about to go.
                if elsewhere is not None and device not in _in_flight:
                    _forget(device)
        elif not boot:
            held.append((device, HELD_NO_BOOT))
        elif entry["boot"] != boot:
            continue            # kept while the boot was unknown: an earlier boot's
        else:
            pending.append((device, entry["original"], port.current))
    return pending, held


def restore_all_recorded() -> list[tuple[str, bool, str]]:
    """Put every recorded original back; a refused one stays recorded.

    A changed device held back (its change still waiting, or the boot ID
    unreadable) is reported as not restored, with why, so a quit names it.
    Raises RecordUnreadable: an unread record is never taken for an empty one.
    """
    pending, held = _pending()
    results = [(device, False, why) for device, why in held]
    for device, original, _ in pending:
        # "original", not "hardware": it is often a private Wi-Fi address.
        ok, message = set_mac(device, original,
                              prompt=f"Put back the original address of {device}")
        results.append((device, ok, message))
    return results


# ── Applying it ──────────────────────────────────


def _unread_refusal(exc: RecordUnreadable, device: str) -> str:
    return (f"Sentinel {exc.strerror}, so it cannot tell whether {device} still has an "
            "address to put back, and could not record the one it uses now. "
            "Nothing was changed.")


def set_mac(device: str, address: str, *, prompt: str = "") -> tuple[bool, str]:
    """
    Change an interface's address.

    Wi-Fi has to be disassociated first — the driver will not accept a new
    address while joined to a network, and does so quietly enough that it looks
    like it worked. Cycling power on the interface is the reliable way to force
    it on current macOS, where the old `airport -z` tool no longer exists.
    """
    return _change(device, address, prompt, None)


def _change(device: str, address: str, prompt: str,
            found: tuple[Interface | None, str] | None) -> tuple[bool, str]:
    """set_mac's body. ``found`` is a listing the caller already took (restore),
    so one call reads the interfaces once; None means list them here."""
    if not valid_device(device):
        return False, f"{device!r} is not a valid interface name."
    if not is_valid(address):
        return False, f"{address!r} is not a usable unicast MAC address."

    with _busy(device):
        interface, refusal = found if found is not None else _find_interface(device)
        if interface is None:
            return False, refusal
        if not interface.current:
            return False, (f"Sentinel could not read the address {device} is using now, so it "
                           "could not record it for restoring. Nothing was changed.")

        address = address.lower()
        # Only a record pending_restores would still act on opens the guard: one
        # for this device whose hardware is this interface's (it drops the rest).
        try:
            entry = _read_entries()[0].get(device) or {}
        except RecordUnreadable as exc:
            if address != interface.hardware:
                # The unread record may hold this device's first original;
                # recording what it uses now (perhaps a changed address) would
                # save over it. Going back to the hardware needs no record.
                return False, _unread_refusal(exc, device)
            entry = {}
        restorable = entry.get("hardware") == interface.hardware
        if (address != interface.hardware and not restorable
                and not is_valid(interface.current)):
            # What it uses now is the "original" a restore would put back; an
            # all-zero or multicast one cannot be recorded (the record drops it),
            # so a change would leave nothing to restore. Fail closed.
            return False, (f"Sentinel could not record the address {device} is using now "
                           f"({interface.current}) for restoring. Nothing was changed.")
        if (address != interface.hardware and not boot_id()
                and not (restorable and address == entry["original"])):
            # A record must name its boot, or it would outlive the change; and
            # an entry kept only because the boot is unknown may be an earlier
            # boot's, so it cannot stand in for this change's. Only a way back.
            return False, (f"Sentinel could not read this Mac's boot ID, so it could not "
                           f"record the address {device} is using now for restoring. "
                           "Nothing was changed.")

        # Recorded before root runs: a crash after the change must still find
        # it. Going back to the hardware address needs no record (and must not
        # record someone else's change as ours to put back).
        if address != interface.hardware:
            try:
                _remember(device, interface.current, interface.hardware)
            except RecordUnreadable as exc:     # locked since the read above
                return False, _unread_refusal(exc, device)

        # Sentinel's gated actions always use the macOS dialog, never a cached
        # sudo ticket left by some unrelated command.
        ok, output = run_argv_as_root(
            ["/bin/bash", "-c", ROOT_SCRIPT, "sentinel-mac", device, address,
             "1" if interface.is_wifi else "0"],
            prompt or f"Change the hardware address of {device}",
            allow_cached_sudo=False,
        )
        applied = current_mac(device)
        # Only a finished command, a cancelled dialog or a refusal to run as
        # root is a known outcome. A timeout can land after this read (root
        # keeps running once the password is in), so the record stays until
        # pending_restores sees the original back.
        settled = ok or output in ("Cancelled.", ROOT_REFUSAL)
        if settled and applied:
            try:
                original = recorded().get(device)
            except RecordUnreadable:
                original = None         # unknown: only the hardware address settles it
            if applied in (interface.hardware, original):
                _forget(device)
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
    # random_mac ignores `like`, so set_mac's own listing is the only one needed.
    return set_mac(device, random_mac(mode))


def restore(device: str) -> tuple[bool, str]:
    """Put back the address the hardware shipped with."""
    if not valid_device(device):       # before the listing, as set_mac does
        return False, f"{device!r} is not a valid interface name."
    with _busy(device):
        interface, refusal = _find_interface(device)
        if interface is None:
            return False, refusal
        if not interface.hardware:
            return False, f"The permanent address of {device} is unknown."
        if not interface.current:
            # Empty is unknown, not "unchanged": `spoofed` is False for it.
            return False, f"Sentinel could not read the address {device} is using now."
        if not interface.spoofed:
            return True, f"{device} is already using its hardware address."
        return _change(device, interface.hardware, "", (interface, ""))


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

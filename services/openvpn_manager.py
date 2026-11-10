"""OpenVPN client control for Sentinel's Tunnel.

WireGuard already had a manager (``agents/vpn_agent/services/wireguard_manager``);
this is its OpenVPN counterpart. OpenVPN needs root to add routes and open a tun
device, so every state change goes through an injected ``run_as_root`` (the real
one raises the macOS authorisation dialog). The client is started daemonised with
a pid file so the GUI stays responsive and can report real status.

Root runs only its own copy of the reviewed config and of every data file the
config names beside it, and starts openvpn in the copies' folder (audit tier-2
step S8b, ``vpn_rootscript.OVPN_SCRIPT``). The pid file, the device name and
the start-up log live in root's run directory too, so nothing running as the
user can point Disconnect's ``kill`` at another process (S8a). Sentinel only
reads them, and believes a file only if root owns it; root re-checks the
process before it signals it.

Sentinel keeps no connection log. OpenVPN has to write one for a few seconds,
because the tunnel device it opens ("utun4") is only reported there and the kill
switch needs that name. The root script reads the device from it, stores just
the device name, and deletes the log; OpenVPN keeps writing to the unlinked
file, which no longer has a name on disk.

A tunnel started by a Sentinel from before S8a keeps its pid in the user's data
folder. It is still found there and stopped (root checks its identity the same
way) while its pid file is the one that process wrote at start. Its pid, device
and log files there are deleted by Connect or Disconnect once that process has
positively gone, never by a status read. Nothing else writes that folder.

Nothing here fabricates a connection: ``connect`` reports what OpenVPN actually
returned, and ``is_running``/``status`` read the real pid file and process table.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from services import vpn_rootscript
from services.runtime_paths import user_data_base

# Injected in tests; the default raises the real admin prompt.
try:  # pragma: no cover - exercised via the real app, mocked in tests
    from functools import partial as _partial
    from agents.vpn_agent.services.privileged import run_as_root as _privileged_run_as_root
    # Sentinel's gated actions always go through the macOS dialog (never a
    # cached sudo ticket) — that is what the README and the review promise.
    _default_run_as_root = _partial(_privileged_run_as_root, allow_cached_sudo=False)
except Exception:  # pragma: no cover
    def _default_run_as_root(script: str, prompt: str, timeout: float = 30):
        return False, "Privileged execution is unavailable."

RunAsRoot = Callable[..., tuple[bool, str]]


def _absolute(path: str) -> str:
    """``path`` from the current directory, left as written otherwise.

    Not ``os.path.abspath``: that drops ``link/..`` without following ``link``,
    so root would open a different file from the one this process (and the
    review) opened through the link. Joining keeps the kernel's own walk.
    """
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def openvpn_binary() -> str | None:
    """Absolute path of the OpenVPN client, or None.

    Homebrew installs it under ``sbin``, which the macOS authorisation dialog's
    shell and sudo's secure_path both drop, so the privileged script must name
    the binary by its full path (the WireGuard root script does the same, ``services/vpn_rootscript.py``).
    """
    found = shutil.which("openvpn")
    if found:
        return _absolute(found)     # a relative PATH entry; root runs from /
    for candidate in ("/opt/homebrew/sbin/openvpn", "/usr/local/sbin/openvpn",
                      "/opt/homebrew/bin/openvpn", "/usr/local/bin/openvpn"):
        if Path(candidate).is_file():
            return candidate
    return None


def is_openvpn_available() -> bool:
    """True when an OpenVPN client binary can be found."""
    return openvpn_binary() is not None


def pid_file() -> Path:
    """Root's pid file, as openvpn's command line names it (``--writepid``)."""
    return Path(vpn_rootscript.ovpn_path(vpn_rootscript.OVPN_PID))


def _legacy_dir() -> Path:
    """Where a Sentinel from before S8a kept the pid, device and log. Read only."""
    return Path(_absolute(str(user_data_base() / "vpn")))


def legacy_pid_file() -> Path:
    return _legacy_dir() / "openvpn.pid"


def legacy_log_file() -> Path:
    return _legacy_dir() / "openvpn.log"


def legacy_device_file() -> Path:
    return _legacy_dir() / "openvpn.device"


def _forget(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _parse_pid(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() and len(text) <= 10 else None


def _read_pid() -> int | None:
    """The pid root recorded; a file root does not own is not believed."""
    return _parse_pid(vpn_rootscript.read_root_file(vpn_rootscript.OVPN_PID))


def _read_legacy_pid() -> int | None:
    try:
        return _parse_pid(legacy_pid_file().read_text(encoding="utf-8")[:16])
    except (OSError, ValueError):
        return None


LEGACY_PID_SLACK = 60     # s between an older Sentinel's openvpn starting and writing its pid


def _process_age(pid: int) -> int | None:
    """Seconds since ``pid`` started (``ps -o etime``: [[dd-]hh:]mm:ss), or None."""
    try:
        proc = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "etime="],
                              capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    match = re.fullmatch(r"(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)", (proc.stdout or "").strip())
    if proc.returncode != 0 or not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _legacy_pid_is_its_own(pid: int) -> bool:
    """The legacy pid file was written when that process started, as openvpn
    --writepid does. The file is in the user's folder: a pid written into it
    later, naming some other openvpn that happens to look like Sentinel's, is
    not believed (S8a review P3-2). A tunnel that predates S8a still passes."""
    age = _process_age(pid)
    try:
        written = legacy_pid_file().stat().st_mtime
    except OSError:
        return False
    return age is not None and abs((time.time() - age) - written) <= LEGACY_PID_SLACK


def _forget_legacy() -> None:
    """An older Sentinel's leftovers once its process is gone. The start-up log
    can name the server, so it must not outlive the tunnel (S8a review P2-1)."""
    for path in (legacy_log_file(), legacy_device_file(), legacy_pid_file()):
        _forget(path)


def _legacy_is_gone() -> bool:
    """Whether the process an older Sentinel's pid file names has positively
    gone: no such process, or ``ps`` answers and shows something that is not
    Sentinel's openvpn. A ``ps`` that times out or fails proves nothing, so the
    files that track a running tunnel are never dropped on it (code review P2-2)."""
    pid = _read_legacy_pid()
    if pid is None or pid <= 1:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass                                       # EPERM: alive, and root's
    try:
        proc = subprocess.run(["/bin/ps", "-ww", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return False
    return not _is_tracked_process(pid, legacy_pid_file())


def _sweep_legacy() -> None:
    if legacy_pid_file().exists() and _legacy_is_gone():
        _forget_legacy()


def _tracked() -> tuple[int, Path | None] | None:
    """``(pid, legacy pid file or None)`` of Sentinel's running openvpn, or None.
    Read only: the UI polls it, so it never deletes anything."""
    pid = _read_pid()
    if pid is not None and _is_tracked_process(pid):
        return pid, None
    legacy = _read_legacy_pid()
    if (legacy is not None and _is_tracked_process(legacy, legacy_pid_file())
            and _legacy_pid_is_its_own(legacy)):
        return legacy, legacy_pid_file()
    return None


def is_running() -> bool:
    """Whether the tracked Sentinel process exists; not a tunnel-health check."""
    return _tracked() is not None


_DEVICE_LINE = re.compile(
    r"(?:Opened utun device|TUN/TAP device)\s+(utun\d+|tun\d+)\b")
_DEVICE_NAME = re.compile(r"(?:utun|tun)\d+")


def _root_device() -> str | None:
    stored = vpn_rootscript.read_root_file(vpn_rootscript.OVPN_DEVICE)
    return stored if _DEVICE_NAME.fullmatch(stored) else None


def tunnel_device() -> str | None:
    """The utun/tun device of the tracked OpenVPN tunnel, or None.

    The root script stored the device OpenVPN reported ("Opened utun device
    utun4", or "TUN/TAP device tun0 opened"). The kill switch must pass traffic
    on exactly that device; the name is validated so nothing else can reach a
    pf rule. A tunnel an older Sentinel started is read from its own files.
    """
    tracked = _tracked()
    if tracked is None:
        return None
    if tracked[1] is None:
        return _root_device()
    try:
        stored = legacy_device_file().read_text(encoding="utf-8").strip()
    except OSError:
        stored = ""
    if _DEVICE_NAME.fullmatch(stored):
        return stored
    try:
        text = legacy_log_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = _DEVICE_LINE.findall(text)
    return matches[-1] if matches else None


def connect(config_path: str, *, config_sha256: str = "",
            includes: tuple[tuple[str, str], ...] = (), openvpn: str = "",
            programs: tuple[str, ...] = (),
            run_as_root: RunAsRoot = _default_run_as_root) -> dict:
    """Start an OpenVPN client from a .ovpn file, daemonised, as root.

    Root runs only its own copy of the config, hashing to ``config_sha256``
    (the confirmed review's), and of each file in ``includes`` (path, SHA-256)
    that the review named, and re-checks that each of ``programs`` (the hook
    programs and libraries the review accepted) is still root-owned; without a
    valid hash nothing runs. ``openvpn`` is the program the review resolved;
    root runs that path, and without it nothing runs.

    Returns a structured result; ``success`` means OpenVPN launched, not that the
    tunnel finished negotiating — callers poll ``is_running``. ``device`` is the
    tunnel device OpenVPN reported, when it did within about fifteen seconds
    (the root script waits for it and prints the log's last lines if it never
    came).
    """
    result = {"success": False, "output": "", "error": None,
              "device": None, "protocol": "OpenVPN"}
    if not is_openvpn_available():
        result["error"] = ("openvpn client not found — install it, e.g. "
                            "`brew install openvpn`.")
        return result
    cfg = Path(config_path).expanduser()
    if not cfg.is_file():
        result["error"] = f"OpenVPN config not found: {config_path}"
        return result
    if is_running():
        result["error"] = "An OpenVPN process is already running. Disconnect first."
        return result
    _sweep_legacy()

    try:
        if not openvpn:
            # Root runs the program the review named, never a fresh lookup.
            raise ValueError("no reviewed openvpn program.")
        script = vpn_rootscript.openvpn_command(
            "up", config=_absolute(str(cfg)), sha256=config_sha256, openvpn=openvpn,
            includes=includes, programs=programs)
    except ValueError as exc:
        result["error"] = f"Nothing was run: {exc}"
        return result
    ok, output = run_as_root(script, "Sentinel needs administrator access to start the VPN.")
    result["output"] = output
    result["success"] = ok
    if ok:
        result["device"] = _root_device()
    else:
        result["error"] = output or "OpenVPN failed to start."
    return result


def _is_tracked_process(pid: int, pidfile: Path | None = None) -> bool:
    """Fail closed unless this PID names Sentinel's own OpenVPN command.

    A PID file alone is not identity: PIDs can be stale or reused. The command
    line is compared as the raw string ``ps`` prints (``-ww`` so it is never
    truncated) rather than re-tokenised: ``ps`` does not quote arguments, so a
    pid-file path containing a space — every packaged build kept the legacy one
    under ``~/Library/Application Support`` and every portable one under
    ``Sentinel Data`` — would split into two tokens and the real process would
    be refused forever (D2). ``pidfile`` is the ``--writepid`` path to expect:
    root's own by default, the user-side one for an older Sentinel's tunnel.
    """
    if pid <= 1:
        return False
    try:
        proc = subprocess.run(
            ["/bin/ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    command = (proc.stdout or "").strip()
    if proc.returncode != 0 or not command:
        return False
    first, _, _ = command.partition(" ")
    if Path(first).name != "openvpn":
        return False
    return (
        " --daemon sentinel-ovpn " in f"{command} "
        and f" --writepid {pidfile or pid_file()} " in f"{command} "
    )


def disconnect(*, run_as_root: RunAsRoot = _default_run_as_root) -> dict:
    """Request termination only for a verified Sentinel OpenVPN process.

    Root checks the process identity again before it sends SIGTERM, waits up to
    ten seconds for it to exit, and then removes the copies, pid and device."""
    result = {"success": False, "output": "", "error": None, "protocol": "OpenVPN"}
    tracked = _tracked()
    if tracked is None:
        _sweep_legacy()
        stale = _read_pid()
        if stale is not None and stale > 1:
            # Root still records a pid, but no Sentinel openvpn holds it (it
            # exited on its own): let root remove the copies of the keys it kept
            # for that tunnel. Root re-checks and signals nothing (code review P3-1).
            ok, output = run_as_root(vpn_rootscript.openvpn_command("down", pid=stale),
                                     "Sentinel needs administrator access to remove the "
                                     "files of a VPN connection that has ended.")
            result["output"] = output
        result["error"] = (
            "Cannot verify a Sentinel-owned OpenVPN process. Nothing was stopped. "
            "Use the VPN client that started the connection to disconnect it."
        )
        return result
    pid, legacy = tracked
    try:
        script = vpn_rootscript.openvpn_command("down", pid=pid,
                                                legacy_pid_file=str(legacy or ""))
    except ValueError as exc:
        result["error"] = f"Nothing was stopped: {exc}"
        return result
    ok, output = run_as_root(script, "Sentinel needs administrator access to stop the VPN.")
    result["success"] = ok
    result["output"] = output
    # Keep tracking until exit is observed; sending a signal does not prove exit.
    if not ok:
        result["error"] = output or "Could not stop OpenVPN."
    elif legacy is not None:
        _sweep_legacy()
    return result

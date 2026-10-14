"""Install / remove / query the continuous background watch (launchd).

The background watcher is a launchd *StartInterval* agent — launchd re-runs
``main.py --headless`` (one watch pass) every N seconds rather than us keeping a
long-lived daemon alive. Each pass reuses the same baseline and findings log the
in-app panel reads, so the app shows whatever the background watcher found while
it was closed.

Everything here is guarded: launchctl output is parsed defensively, and every
call returns a plain result dict the panel can render rather than raising into
the UI thread.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

LABEL = "com.sentinel.sentry.watch"
PROJECT = Path(__file__).resolve().parents[1]  # agents/sentry
MAIN = PROJECT / "main.py"
MIN_INTERVAL = 60
DEFAULT_INTERVAL = 300
FROZEN_MESSAGE = ("The background watch is not available in the packaged app; "
                  "run Sentinel from the checkout (developer mode).")


def is_frozen() -> bool:
    """True in a packaged build, where the plist's main.py is not bundled."""
    return bool(getattr(sys, "frozen", False))


def _launch_agents_dir() -> Path:
    path = Path.home() / "Library" / "LaunchAgents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plist_path() -> Path:
    return _launch_agents_dir() / f"{LABEL}.plist"


def _python_executable() -> str:
    """Prefer the project's own venv so the watcher matches the app's imports."""
    venv = PROJECT.parents[1] / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable or "python3"


def _log_path() -> Path:
    try:
        from .baseline import default_state_dir

        return default_state_dir() / "watch.log"
    except Exception:
        return PROJECT / "data" / "watch.log"


def build_plist(interval: int) -> dict:
    interval = max(MIN_INTERVAL, int(interval))
    log = str(_log_path())
    from .baseline import STATE_DIR_ENV, default_state_dir

    return {
        "Label": LABEL,
        "ProgramArguments": [_python_executable(), str(MAIN), "--headless"],
        "StartInterval": interval,
        "RunAtLoad": True,
        "WorkingDirectory": str(PROJECT),
        # The headless process must read and write the same baseline and
        # findings as the panel, whatever its working directory can import.
        "EnvironmentVariables": {STATE_DIR_ENV: str(default_state_dir())},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def is_installed() -> bool:
    return plist_path().is_file()


def is_loaded() -> bool:
    try:
        proc = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return LABEL in (proc.stdout or "")


def _last_run() -> tuple[str | None, str | None]:
    """(last run, exit code) of the loaded job, or (None, None) if unknown."""
    try:
        proc = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if proc.returncode != 0:
        return None, None
    state, code = parse_job(proc.stdout or "")
    return (last_run(code), code) if state else (None, None)


def status() -> dict:
    loaded = is_loaded()
    # `launchctl print` is a second blocking call on whoever asks (the panel's UI
    # thread today); it is only made for a loaded job. Moving both off the UI
    # thread is deferred: the panel has no worker for it yet.
    last, code = _last_run() if loaded else (None, None)
    return {
        "installed": is_installed(),
        "loaded": loaded,
        "last_run": last,
        "last_exit_code": code,
        "interval": _installed_interval(),
        "frozen": is_frozen(),
        "plist": str(plist_path()),
        "log": str(_log_path()),
    }


def _installed_interval() -> int | None:
    path = plist_path()
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            return int(plistlib.load(handle).get("StartInterval", 0)) or None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def install(interval: int = DEFAULT_INTERVAL) -> dict:
    """Write the plist and load it. Reloads cleanly if already installed.

    Refuses in a packaged build (the plist runs ``main.py --headless``, which the
    bundle does not contain). Success is judged by `launchctl print`, not by the
    exit code of `launchctl load`."""
    if is_frozen():
        return {"ok": False, "verified": False, "message": FROZEN_MESSAGE}
    path = plist_path()
    if is_loaded():
        _launchctl_unload(path)
    try:
        with path.open("wb") as handle:
            plistlib.dump(build_plist(interval), handle)
    except OSError as exc:
        return {"ok": False, "message": f"Could not write launch agent: {exc}"}
    result = _launchctl_load(path)
    if result["ok"]:
        result = _verify_loaded()
    result["interval"] = max(MIN_INTERVAL, int(interval))
    return result


def remove() -> dict:
    """Unload and delete the launch agent. Idempotent."""
    path = plist_path()
    if is_loaded():
        _launchctl_unload(path)
    try:
        if path.is_file():
            path.unlink()
    except OSError as exc:
        return {"ok": False, "message": f"Could not remove launch agent: {exc}"}
    return {"ok": True, "message": "Background watch removed."}


def _launchctl_load(path: Path) -> dict:
    try:
        proc = subprocess.run(
            ["launchctl", "load", "-w", str(path)],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": f"launchctl unavailable: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "message": proc.stderr.strip() or "launchctl load failed."}
    return {"ok": True, "message": "launchctl accepted the launch agent."}


def _verify_loaded() -> dict:
    """Ask launchd about the job. `launchctl load` can exit 0 without a loaded
    job, so the answer comes from `launchctl print gui/<uid>/<label>`; the
    output format varies by macOS version, so it is parsed loosely."""
    target = f"gui/{os.getuid()}/{LABEL}"
    check = f"Check with: launchctl print {target}"
    try:
        proc = subprocess.run(
            ["launchctl", "print", target], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "verified": False,
                "message": f"Launch agent written and load accepted, but not verified "
                           f"(launchctl print unavailable: {exc}). {check}"}
    out = proc.stdout or ""
    if proc.returncode != 0:
        return {"ok": False, "verified": False,
                "message": "Launch agent written, but launchd reports the job is not loaded "
                           f"({(proc.stderr or '').strip() or 'launchctl print failed'}). {check}"}
    state, code = parse_job(out)
    if not state:
        return {"ok": False, "verified": False,
                "message": f"Launch agent written and load accepted, but not verified "
                           f"(launchctl print gave no job state). {check}"}
    ran = last_run(code)
    if ran == "failed":
        return {"ok": False, "verified": True,
                "message": f"Background watch is loaded (state: {state}) but its last "
                           f"run failed (exit {code}). See the log, then Reinstall."}
    return {"ok": True, "verified": True,
            "message": f"Background watch is loaded (state: {state}); last run: {ran}."}


def parse_job(output: str) -> tuple[str, str]:
    """(state, last exit code) of the job itself from `launchctl print` output.
    Nested blocks (endpoints, properties) have their own `state =` lines; only
    the job's top level counts. Either value is "" when absent."""
    state = code = ""
    depth = 0
    for raw in output.splitlines():
        line = raw.strip()
        if line.endswith("{"):
            depth += 1
            continue
        if line.startswith("}"):
            depth = max(0, depth - 1)
            continue
        if depth > 1:
            continue
        match = re.match(r"(state|last exit code)\s*=\s*(.+)$", line)
        if match:
            if match.group(1) == "state" and not state:
                state = match.group(2).strip()
            elif match.group(1) == "last exit code" and not code:
                code = match.group(2).strip()
    return state, code


def last_run(code: str) -> str:
    """"ok", "failed" or "not yet run" from a launchd `last exit code` value.
    Negative or suffixed codes ("-9", "78 (x)") are failures, never "ok"."""
    match = re.match(r"-?\d+", code or "")
    if not match:
        return "not yet run"
    return "ok" if int(match.group(0)) == 0 and re.fullmatch(r"0(\s.*)?", code) else "failed"


def _launchctl_unload(path: Path) -> None:
    try:
        subprocess.run(
            ["launchctl", "unload", "-w", str(path)],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

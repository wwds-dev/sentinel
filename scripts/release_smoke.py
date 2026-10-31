#!/usr/bin/env python3
"""
release_smoke.py — launch the installed Sentinel.app and check its lifecycle.

The P0 release smoke in docs/testing_roadmap.md (installation and
single-instance lifecycle). tests/test_release_regressions.py asserts the
source; this launches what /Applications actually holds and checks that:

1. the bundle is the live launcher: its executable is named Sentinel and its
   project_root.txt names a checkout with main.py and a venv;
2. a launch starts exactly one process, and macOS names it Sentinel, never
   python;
3. a second launch (Launch Services, then a forced new instance) hands off to
   the running copy: no second process stays;
4. Quit, sent the way the install script sends it
   (-[NSRunningApplication terminate], never a signal), ends the process within
   the owner's 5-second limit, and nothing starts it again afterwards;
5. no launchd job or LaunchAgent names Sentinel (an old installer once left
   keep-alive jobs behind);
6. a copy of the launcher pointed at a checkout that is not there exits 1 and
   says why in the launch log, instead of failing silently.

It refuses to run while Sentinel is open (its Quit would end your session) or
while a hardware-address change is recorded (quitting would then ask for the
administrator password to put the address back). It runs no VPN, kill-switch
or MAC action: the app is only opened and quit.

Usage: python3 scripts/release_smoke.py [--app PATH] [--stay SECONDS]
Exit status: 0 every check passed, 1 a check failed, 2 refused to run.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APP = Path("/Applications/Sentinel.app")
APP_NAME = "Sentinel"
LAUNCH_LOG = Path("/tmp/sentinel-launch.log")         # LOG_PATH in scripts/install_app.sh
SINGLE_INSTANCE_KEY = "sentinel.single-instance.v2"   # main.SINGLE_INSTANCE_KEY
QUIT_LIMIT_S = 5.0          # the owner's limit for a whole quit (main.py, spec no-thread-terminate Q4)
START_TIMEOUT_S = 60.0
SETTLE_S = 5.0              # let the window finish starting, as a person would
HANDOFF_TIMEOUT_S = 20.0
STAY_GONE_S = 30.0
MISSING_CHECKOUT = "cannot start: the Lab checkout or its Python environment is missing"
MISSING_ROOT_FILE = "cannot start: the bundled project path is missing or unreadable"


@dataclass
class Check:
    name: str
    status: str             # PASS, FAIL or SKIP
    detail: str = ""


# ---- pure helpers (tests/test_release_smoke.py) --------------------------------

def parse_ps(text: str) -> list[tuple[int, int, str]]:
    """Rows of `ps -axww -o pid=,ppid=,command=` as (pid, ppid, command)."""
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def sentinel_roots(rows, app: Path, project_root: Path | None, own_pid: int) -> list[int]:
    """PIDs of running Sentinel copies, not counting their own children.

    A copy is the bundle's executable or a python running the checkout's
    main.py (the same match as scripts/install_app.sh). A child started with
    sys.executable carries the bundle path too, so only processes whose parent
    is not itself a match count as a copy.
    """
    markers = [f"{app}/Contents/MacOS/"]
    if project_root is not None:
        markers.append(f"{project_root}/main.py")
    matches = {pid: ppid for pid, ppid, command in rows
               if pid != own_pid and any(marker in command for marker in markers)}
    return sorted(pid for pid, ppid in matches.items() if ppid not in matches)


def refusal_reasons(running: list[int], records: list[Path]) -> list[str]:
    """Why the smoke must not run now; empty when it may."""
    reasons = []
    if running:
        reasons.append(f"Sentinel is running (pid {', '.join(map(str, running))}); "
                       "quit it first, this smoke would quit your session")
    for record in records:
        reasons.append(f"a hardware-address change is recorded in {record}; quitting "
                       "would ask for the administrator password to restore it. "
                       "Restore it from Tunnel > Privacy first")
    return reasons


def socket_path(temp_dir: str) -> Path:
    """Where QLocalServer puts the single-instance socket (QDir::tempPath)."""
    return Path(temp_dir.rstrip("/")) / SINGLE_INSTANCE_KEY


def launchd_mentions(labels: list[str], agents: dict[str, bytes], app: Path) -> list[str]:
    """launchd labels and LaunchAgent plists that name Sentinel or its bundle.

    `application.<bundle id>.…` labels are Launch Services' own record of an
    app it opened, not a job that restarts one, and are left out.
    """
    found = [f"launchctl job {label}" for label in labels
             if "sentinel" in label.lower() and not label.startswith("application.")]
    needle = str(app).encode()
    for name, content in agents.items():
        if "sentinel" in name.lower() or needle in content:
            found.append(f"LaunchAgent {name}")
    return found


def format_table(checks: list[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = [f"{check.status:<4}  {check.name:<{width}}  {check.detail}".rstrip()
             for check in checks]
    return "\n".join(lines)


# ---- the Mac -------------------------------------------------------------------

def run(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def running_copies(app: Path, project_root: Path | None) -> list[int]:
    rows = parse_ps(run(["ps", "-axww", "-o", "pid=,ppid=,command="]).stdout)
    return sentinel_roots(rows, app, project_root, os.getpid())


def accounting_name(pid: int) -> str:
    """The name Activity Monitor, the Dock and Force Quit show for a process."""
    return run(["ps", "-p", str(pid), "-o", "ucomm="]).stdout.strip()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int) -> None:
    """Ask the app to quit, the way scripts/install_app.sh does: never a signal."""
    run(["osascript", "-l", "JavaScript", "-e",
         "ObjC.import('AppKit');"
         f"const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier({pid});"
         "app.isNil() ? false : app.terminate"])


def user_temp_dir() -> str:
    """The TMPDIR launchd gives an app opened from Finder or `open`."""
    return run(["getconf", "DARWIN_USER_TEMP_DIR"]).stdout.strip() or tempfile.gettempdir()


def wait_for(predicate, timeout: float, step: float = 0.25):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)


def recorded_mac_changes(project_root: Path | None) -> list[Path]:
    """mac_original.json files with an entry (live launcher and frozen data dirs)."""
    candidates = [Path.home() / "Library" / "Application Support" / APP_NAME / "vpn"]
    if project_root is not None:
        candidates.insert(0, project_root / "vpn")
    found = []
    for directory in candidates:
        record = directory / "mac_original.json"
        try:
            text = record.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except OSError:
            found.append(record)            # unreadable: assume something is in it
            continue
        if text not in ("", "{}"):
            found.append(record)
    return found


def read_bundle(app: Path) -> tuple[str, Path | None]:
    """(CFBundleExecutable, checkout named by project_root.txt or None)."""
    with open(app / "Contents" / "Info.plist", "rb") as handle:
        executable = plistlib.load(handle).get("CFBundleExecutable", "")
    pointer = app / "Contents" / "Resources" / "project_root.txt"
    try:
        root = Path(pointer.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        root = None
    return executable, root


def launcher_refuses(executable: Path, project_root_text: str | None) -> tuple[int, str]:
    """Run a copy of the launcher in a scratch bundle; (exit code, launch log)."""
    with tempfile.TemporaryDirectory(prefix="sentinel-smoke-") as scratch:
        bundle = Path(scratch) / "Smoke.app" / "Contents"
        (bundle / "MacOS").mkdir(parents=True)
        (bundle / "Resources").mkdir()
        copy = bundle / "MacOS" / executable.name
        shutil.copy2(executable, copy)
        if project_root_text is not None:
            (bundle / "Resources" / "project_root.txt").write_text(project_root_text)
        try:
            LAUNCH_LOG.unlink()
        except FileNotFoundError:
            pass
        try:
            code = run([str(copy)], timeout=20).returncode
        except subprocess.TimeoutExpired:
            code = -1
        try:
            log = LAUNCH_LOG.read_text(encoding="utf-8", errors="replace").strip()
        except FileNotFoundError:
            log = ""
    return code, log


# ---- the smoke -----------------------------------------------------------------

def smoke(app: Path, stay_gone: float) -> tuple[list[Check], list[str]]:
    checks: list[Check] = []

    executable_name, project_root = read_bundle(app)
    executable = app / "Contents" / "MacOS" / executable_name
    refusals = refusal_reasons(running_copies(app, project_root),
                               recorded_mac_changes(project_root))
    if refusals:
        return checks, refusals

    # 1. The bundle.
    if project_root is None:
        checks.append(Check("bundle is the live launcher", "SKIP",
                            "no project_root.txt: a frozen build, not the live launcher"))
    else:
        venv_python = project_root / ".venv" / "bin" / "python"
        ok = (executable_name == APP_NAME and executable.is_file()
              and (project_root / "main.py").is_file() and venv_python.exists())
        checks.append(Check("bundle is the live launcher", "PASS" if ok else "FAIL",
                            f"executable {executable_name!r}, checkout {project_root}"))
    checks.append(Check("no launchd job keeps it alive (before)",
                        *launchd_status(app)))

    # 2. One process, named Sentinel.
    temp_dir = user_temp_dir()
    sock = socket_path(temp_dir)
    started = time.time()
    opened = run(["open", str(app)])
    pids = wait_for(lambda: running_copies(app, project_root), START_TIMEOUT_S)
    if opened.returncode != 0 or not pids:
        checks.append(Check("launch starts one process", "FAIL",
                            (opened.stderr.strip() or "no process within "
                             f"{START_TIMEOUT_S:.0f} s") + launch_log_note()))
        return checks, []
    first = pids[0]
    ready = wait_for(lambda: sock.exists() and sock.stat().st_mtime >= started - 1,
                     START_TIMEOUT_S)
    time.sleep(SETTLE_S)
    pids = running_copies(app, project_root)
    checks.append(Check("launch starts one process", "PASS" if pids == [first] else "FAIL",
                        f"pids {pids}" + ("" if ready else "; single-instance socket never appeared")))
    name = accounting_name(first)
    checks.append(Check("process is named Sentinel, not python",
                        "PASS" if name == APP_NAME else "FAIL", f"ps ucomm {name!r}"))

    # 3. Second launches hand off.
    run(["open", str(app)])
    time.sleep(3)
    pids = running_copies(app, project_root)
    checks.append(Check("second open reuses the running copy",
                        "PASS" if pids == [first] else "FAIL", f"pids {pids}"))
    run(["open", "-n", str(app)])
    # The second copy has to be seen starting, or this would pass on a launch
    # that never happened.
    both = wait_for(lambda: [pid for pid in running_copies(app, project_root) if pid != first],
                    10, step=0.1)
    settled = wait_for(lambda: running_copies(app, project_root) == [first], HANDOFF_TIMEOUT_S)
    pids = running_copies(app, project_root)
    checks.append(Check("forced new instance hands off and exits",
                        "PASS" if both and settled and alive(first) else "FAIL",
                        f"second copy {both or 'never seen'}, then pids {pids}"))

    # 4. Quit stays quit.
    for pid in pids:
        terminate(pid)
    asked = time.monotonic()
    gone = wait_for(lambda: not alive(first), 30)
    took = time.monotonic() - asked
    if not gone:
        checks.append(Check("Quit ends the process", "FAIL",
                            f"pid {first} still running after 30 s; quit it by hand"))
        return checks, []
    checks.append(Check(f"Quit ends the process within {QUIT_LIMIT_S:.0f} s",
                        "PASS" if took <= QUIT_LIMIT_S else "FAIL", f"{took:.1f} s"))
    back = wait_for(lambda: running_copies(app, project_root), stay_gone, step=1)
    checks.append(Check(f"stays quit for {stay_gone:.0f} s",
                        "FAIL" if back else "PASS", f"relaunched as pid {back}" if back else ""))
    checks.append(Check("no launchd job keeps it alive (after)", *launchd_status(app)))

    # 6. A missing checkout is reported, not silent.
    if project_root is None:
        checks.append(Check("missing checkout is reported", "SKIP", "frozen build"))
    else:
        kept = LAUNCH_LOG.read_bytes() if LAUNCH_LOG.exists() else None
        try:
            with tempfile.TemporaryDirectory(prefix="sentinel-no-checkout-") as empty:
                code, log = launcher_refuses(executable, empty)
            checks.append(Check("missing checkout is reported",
                                "PASS" if code == 1 and MISSING_CHECKOUT in log else "FAIL",
                                f"exit {code}, log {log!r}"))
            code, log = launcher_refuses(executable, None)
            checks.append(Check("missing project_root.txt is reported",
                                "PASS" if code == 1 and MISSING_ROOT_FILE in log else "FAIL",
                                f"exit {code}, log {log!r}"))
        finally:
            if kept is None:
                LAUNCH_LOG.unlink(missing_ok=True)
            else:
                LAUNCH_LOG.write_bytes(kept)
    return checks, []


def launchd_status(app: Path) -> tuple[str, str]:
    labels = [line.split("\t")[-1] for line in run(["launchctl", "list"]).stdout.splitlines()[1:]]
    agents: dict[str, bytes] = {}
    for folder in (Path.home() / "Library" / "LaunchAgents", Path("/Library/LaunchAgents")):
        for plist in sorted(folder.glob("*.plist")) if folder.is_dir() else []:
            try:
                agents[str(plist)] = plist.read_bytes()
            except OSError:
                continue
    found = launchd_mentions(labels, agents, app)
    return ("FAIL", "; ".join(found)) if found else ("PASS", "")


def launch_log_note() -> str:
    try:
        text = LAUNCH_LOG.read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return ""
    return f"; launch log: {text[:200]!r}" if text else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--stay", type=float, default=STAY_GONE_S,
                        help="seconds to watch for a relaunch after Quit")
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print("release_smoke: macOS only", file=sys.stderr)
        return 2
    if not (args.app / "Contents" / "Info.plist").is_file():
        print(f"release_smoke: no app bundle at {args.app}", file=sys.stderr)
        return 2

    print(f"Sentinel release smoke: {args.app}")
    checks, refusals = smoke(args.app, args.stay)
    if refusals:
        for reason in refusals:
            print(f"REFUSED  {reason}", file=sys.stderr)
        return 2
    print(format_table(checks))
    failed = [check for check in checks if check.status == "FAIL"]
    print(f"\n{len(checks) - len(failed)} of {len(checks)} checks did not fail"
          f"{'' if not failed else f'; {len(failed)} FAILED'}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

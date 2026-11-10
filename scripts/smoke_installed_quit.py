#!/usr/bin/env python3
"""Installed-app Quit-stays-quit smoke (docs/testing_roadmap.md, delivery step 1).

Launches the real Sentinel.app bundle's executable, waits for it to be ready,
sends a normal macOS Quit, and checks that every process is gone within the
limit and that nothing relaunches for 15 s. Then it relaunches once, checks the
data folder survived, and quits again.

Safety: the app runs with SENTINEL_PORTABLE_ROOT pointing at a fresh temp
directory, so its writable state (data/sentinel.db, chats, logs) is never the
real one. The real data/ and ~/Library/Application Support/Sentinel are
fingerprinted before and after and must be unchanged. No privileged actions.
Refuses to start if a Sentinel is already running (the single-instance socket
would hand the launch to it).

Exit: 0 all checks passed, 1 a check failed, 2 could not run (NOT VERIFIED).

    python3 scripts/smoke_installed_quit.py [--app PATH] [--build]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ID = "com.netrunner3000.sentinel"
SOCKET_GLOB = "sentinel.single-instance.v2"      # main.py SINGLE_INSTANCE_KEY
# Anything that is Sentinel itself: the bundle executable or the checkout's main.py.
PROC_RE = re.compile(r"(Sentinel\.app/Contents/MacOS/Sentinel|/sentinel/main\.py)")
RESULTS: list[tuple[str, str, str]] = []        # (status, name, detail)


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def sentinel_pids() -> list[int]:
    out = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                         capture_output=True, text=True).stdout
    me = os.getpid()
    pids = []
    for line in out.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if pid_s.isdigit() and int(pid_s) != me and PROC_RE.search(cmd) \
                and "smoke_installed_quit" not in cmd:
            pids.append(int(pid_s))
    return pids


def children(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    kids = [int(x) for x in out.split()]
    return kids + [g for k in kids for g in children(k)]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie we have not reaped still answers kill(0).
    state = subprocess.run(["ps", "-p", str(pid), "-o", "state="],
                           capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def fingerprint(paths: list[Path]) -> dict[str, str]:
    fp: dict[str, str] = {}
    for p in paths:
        if p.is_file():
            fp[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            fp[str(p)] = "|".join(sorted(
                f"{c.name}:{c.stat().st_size}" for c in p.iterdir()))
    return fp


def socket_present() -> bool:
    tmp = os.environ.get("TMPDIR", "/tmp")
    return bool(glob.glob(os.path.join(tmp, "**", SOCKET_GLOB), recursive=True)
                or glob.glob(os.path.join(tmp, SOCKET_GLOB)))


def wait_ready(proc, db: Path, limit: float) -> tuple[bool, str]:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False, f"process exited early or never started ({proc.open_error or 'no Sentinel process'})"
        if db.exists() and socket_present():
            return True, f"database and single-instance socket present after {limit - (end - time.monotonic()):.1f}s"
        time.sleep(0.5)
    return False, f"not ready within {limit:.0f}s (db={db.exists()}, socket={socket_present()})"


def send_quit() -> tuple[bool, str]:
    r = subprocess.run(
        ["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'],
        capture_output=True, text=True, timeout=20)
    # Qt answers the quit event with -128 "User cancelled" even when the app then
    # exits (seen 2026-11-05), so the verdict is the process going away, not rc.
    return (r.returncode == 0 or "(-128)" in r.stderr), (r.stderr.strip() or "ok")


class Launched:
    """The app started the way the Dock does (LaunchServices), so Apple events reach it."""

    def __init__(self, app: Path, root: Path) -> None:
        r = subprocess.run(["open", "-n", "--env", f"SENTINEL_PORTABLE_ROOT={root}",
                            "-a", str(app)], capture_output=True, text=True)
        self.open_error = r.stderr.strip() if r.returncode else ""
        self.pid = 0

    def poll(self):
        if self.open_error:
            return 1
        pids = sentinel_pids()
        if pids:
            self.pid = pids[0]
            return None
        return None if self.pid == 0 and time.monotonic() < getattr(self, "grace", 0) else (1 if self.pid else None)


def launch(app: Path, root: Path) -> Launched:
    h = Launched(app, root)
    h.grace = time.monotonic() + 10
    return h


def wait_gone(pids: set[int], limit: float) -> tuple[bool, float, list[int]]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        left = [p for p in pids if alive(p)] + sentinel_pids()
        if not left:
            return True, time.monotonic() - t0, []
        time.sleep(0.25)
    return False, limit, sorted({p for p in pids if alive(p)} | set(sentinel_pids()))


def kill_leftovers(pids: set[int]) -> None:
    for p in sorted(pids | set(sentinel_pids())):
        try:
            os.kill(p, 15)
        except ProcessLookupError:
            pass


def cycle(label: str, app: Path, root: Path, db: Path,
          ready_limit: float, quit_limit: float, stay_quit: float) -> bool:
    """Launch, quit, verify gone and no relaunch. True when quit could be tested."""
    proc = launch(app, root)
    ok, detail = wait_ready(proc, db, ready_limit)
    record("PASS" if ok else "FAIL", f"{label}: app launches and becomes ready", detail)
    if not ok:
        kill_leftovers(set())
        return False
    time.sleep(2)                       # let the window finish building
    tree = {proc.pid, *children(proc.pid), *sentinel_pids()} - {0}
    sent, why = send_quit()
    if not sent:
        record("NOT VERIFIED", f"{label}: Quit sent",
               f"osascript refused ({why}); grant Automation access to this terminal and rerun")
        kill_leftovers(tree)
        return False
    gone, took, left = wait_gone(tree, quit_limit)
    record("PASS" if gone else "FAIL", f"{label}: every process gone within {quit_limit:.0f}s",
           f"{took:.1f}s" if gone else f"still running: {left}")
    if not gone:
        kill_leftovers(tree)
        return True
    seen = []
    end = time.monotonic() + stay_quit
    while time.monotonic() < end:
        seen = sentinel_pids()
        if seen:
            break
        time.sleep(0.5)
    record("PASS" if not seen else "FAIL", f"{label}: stays quit for {stay_quit:.0f}s",
           "no process appeared" if not seen else f"relaunched as {seen}")
    if seen:
        kill_leftovers(set(seen))
    return True


def db_state(db: Path) -> tuple[bool, set[str], str]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        return integrity == "ok", tables, integrity
    except sqlite3.Error as exc:
        return False, set(), str(exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app", help="bundle to test (default: /Applications/Sentinel.app, "
                                  "else the build_app.sh output)")
    ap.add_argument("--build", action="store_true",
                    help="run scripts/build_app.sh (build only, no --install) first")
    ap.add_argument("--ready-limit", type=float, default=90)
    ap.add_argument("--quit-limit", type=float, default=10)
    ap.add_argument("--stay-quit", type=float, default=15)
    a = ap.parse_args()

    if sys.platform != "darwin":
        print("NOT VERIFIED: macOS only")
        return 2
    if a.build:
        if subprocess.run([str(ROOT / "scripts/build_app.sh")]).returncode != 0:
            print("NOT VERIFIED: build_app.sh failed")
            return 2
    candidates = [Path(a.app)] if a.app else [
        Path("/Applications/Sentinel.app"),
        *sorted(ROOT.glob("dist*/Sentinel.app")),
    ]
    app = next((c for c in candidates if (c / "Contents/MacOS").is_dir()), None)
    if app is None:
        print("NOT VERIFIED: no Sentinel.app installed or built "
              "(run scripts/build_app.sh or pass --app)")
        return 2
    print(f"app under test: {app}")

    running = sentinel_pids()
    if running:
        print(f"NOT VERIFIED: Sentinel is already running (pids {running}); "
              "quit it first so the smoke cannot touch or hand off to it")
        return 2

    real = [ROOT / "data" / "sentinel.db",
            Path.home() / "Library/Application Support/Sentinel"]
    before = fingerprint(real)
    tmp = Path(tempfile.mkdtemp(prefix="sentinel-smoke-"))
    data = tmp / "Sentinel Data" / "data"
    db = data / "sentinel.db"
    print(f"temp data root: {tmp}")

    try:
        tested = cycle("run 1", app, tmp, db, a.ready_limit, a.quit_limit, a.stay_quit)
        if tested and db.exists():
            ok1, tables1, why = db_state(db)
            record("PASS" if ok1 and tables1 else "FAIL",
                   "run 1: database intact after Quit", f"{len(tables1)} tables, integrity {why}")
            canary = data / "smoke_canary.txt"
            canary.write_text("sentinel-smoke\n")
            tested = cycle("run 2 (relaunch)", app, tmp, db,
                           a.ready_limit, a.quit_limit, a.stay_quit)
            ok2, tables2, why2 = db_state(db)
            record("PASS" if ok2 and tables1 <= tables2 else "FAIL",
                   "relaunch: database intact, no table lost",
                   f"integrity {why2}, missing {sorted(tables1 - tables2)}")
            record("PASS" if canary.exists() and canary.read_text() == "sentinel-smoke\n"
                   else "FAIL", "relaunch: data/ files survived")
        elif tested:
            record("FAIL", "run 1: data/sentinel.db created in the temp root")
        else:
            record("NOT VERIFIED", "relaunch and data-intact checks", "run 1 did not complete")
        after = fingerprint(real)
        record("PASS" if after == before else "FAIL",
               "real data/ and Application Support/Sentinel untouched",
               "" if after == before else "fingerprint changed")
        record("NOT VERIFIED", "window visible on screen",
               "readiness is database + single-instance socket; no Accessibility check")
    finally:
        kill_leftovers(set())
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nSummary:")
    for status in ("PASS", "FAIL", "NOT VERIFIED"):
        print(f"  {status}: {sum(1 for r in RESULTS if r[0] == status)}")
    if any(r[0] == "FAIL" for r in RESULTS):
        return 1
    if any(r[0] == "NOT VERIFIED" and "window" not in r[1] for r in RESULTS):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

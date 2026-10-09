"""
privileged.py — Running one command as root, from a GUI.

Both the kill switch and MAC randomisation need root for a moment. A GUI has
nowhere to show a terminal password prompt, and blocking on one invisibly looks
exactly like a hang, so this tries cached sudo credentials first and falls back
to the native macOS authorisation dialog.

Nothing secret is ever passed through here — firewall rules, interface names,
file paths. That matters because the command is briefly visible in the process
list, which would be unacceptable for anything carrying a key.
"""

from __future__ import annotations

import os
import subprocess

DEFAULT_TIMEOUT = 30


def run(command: list[str], timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """Run a command directly, returning (ok, combined output)."""
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "Timed out."
    except OSError as exc:
        return False, str(exc)

    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        return True, output
    if "User canceled" in output or "(-128)" in output:
        return False, "Cancelled."
    return False, output or f"exited {proc.returncode}"


SUDO_REFUSALS = (
    "a password is required",
    "sorry, you must have a tty",
    "no tty present",
    "a terminal is required",
)


def _sudo_refused(output: str) -> bool:
    """True only when sudo itself declined to run the command non-interactively.

    sudo prints its own refusals as lines starting with ``sudo:``. Anything
    else is the command's output: it ran, and must not be run a second time
    through the dialog just because it happened to mention "password".
    """
    for line in output.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("sudo:") and any(text in stripped for text in SUDO_REFUSALS):
            return True
    return False


def run_with_dialog(script: str, prompt: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """Run a shell snippet as root through the macOS authorisation dialog only."""
    escaped = script.replace("\\", "\\\\").replace('"', '\\"')
    return run(
        [
            "osascript", "-e",
            f'do shell script "{escaped}" with prompt "{prompt}" '
            "with administrator privileges",
        ],
        timeout,
    )


def run_as_root(script: str, prompt: str, timeout: float = DEFAULT_TIMEOUT, *,
                allow_cached_sudo: bool = True) -> tuple[bool, str]:
    """
    Run a shell snippet as root.

    With ``allow_cached_sudo`` (the companion tools' default) a cached sudo
    ticket is tried first, because it is silent and does not steal focus.
    Sentinel's gated actions pass ``False``: the README promises that a
    Connect, Disconnect or kill-switch change goes through the macOS
    authorisation dialog, and a ticket left by an unrelated ``sudo`` must not
    turn that promise into a silent privileged run.
    """
    if os.geteuid() == 0:
        return run(["bash", "-c", script], timeout)

    if not allow_cached_sudo:
        return run_with_dialog(script, prompt, timeout)

    ok, output = run(["sudo", "-n", "bash", "-c", script], timeout)
    if ok:
        return True, output
    if not _sudo_refused(output):
        # The command itself ran under sudo and failed; never re-run it.
        return False, output
    return run_with_dialog(script, prompt, timeout)

"""Saved settings: the shipped defaults, plus this machine's own picks.

`config/settings.json` is the shipped defaults. It is tracked in git and
bundled by `Sentinel.spec`, and the app never writes it. What the user picks
in the app (each provider's Chat model, the routing priority) goes to
`data/settings.local.json`, which git ignores and the bundle never carries.
Reads lay the override over the defaults, so a key the user never touched
follows the shipped value.

In development and through the installed live launcher the writable base is
the checkout, so writing picks into the tracked file dirtied the repo with
every model change. A frozen or portable build keeps both files in its own
user-data folder (`runtime_paths.user_data_base()`): `ensure_seeded()` copies
the defaults there, and the override is created on the first pick.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

DEFAULTS_RELPATH = Path("config") / "settings.json"
OVERRIDE_RELPATH = Path("data") / "settings.local.json"


def defaults_path(base: Path) -> Path:
    return Path(base) / DEFAULTS_RELPATH


def override_path(base: Path) -> Path:
    return Path(base) / OVERRIDE_RELPATH


def read_settings_file(path: Path) -> dict:
    """The JSON object in `path`, or {} when it is missing, unreadable or not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings(defaults: Path, override: Path) -> dict:
    """The shipped defaults with this machine's picks laid over them."""
    merged = read_settings_file(defaults)
    merged.update(read_settings_file(override))
    return merged


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_override(override: Path, key: str, value) -> None:
    """Record one pick in the override. Only that key is written; the
    defaults are never copied in, so a later change to them still applies to
    every key the user has not picked."""
    current = read_settings_file(override)
    current[key] = value
    _write_json_atomic(Path(override), current)


def _committed_bytes(path: Path) -> bytes | None:
    """`path` as HEAD has it, or None outside a git checkout or when untracked."""
    path = Path(path).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=5, check=False,
        )
        if top.returncode != 0:
            return None
        root = Path(top.stdout.decode().strip()).resolve()
        rel = path.relative_to(root).as_posix()
        shown = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{rel}"],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return shown.stdout if shown.returncode == 0 else None


def migrate_tracked_defaults(defaults: Path, override: Path) -> dict | None:
    """One time: move picks an older Sentinel wrote into the tracked defaults.

    Runs only in a git checkout and only while the override does not exist
    yet. Every key whose value differs from HEAD's committed defaults moves
    into the override, so the user's current picks survive, and the tracked
    file goes back to its committed content. The override is then created
    even when nothing moved: it marks the migration as done, so a later
    hand edit of the shipped defaults is never mistaken for a user pick.

    Returns the moved values, or None when there was nothing to migrate.
    """
    defaults, override = Path(defaults), Path(override)
    if override.exists() or not defaults.exists():
        return None
    committed = _committed_bytes(defaults)
    if committed is None:
        return None
    current = defaults.read_bytes()
    try:
        committed_values = json.loads(committed)
        current_values = json.loads(current)
    except ValueError:
        # Not something this app wrote; leave a broken hand edit for a human.
        return None
    if not isinstance(committed_values, dict) or not isinstance(current_values, dict):
        return None
    missing = object()
    moved = {
        key: value for key, value in current_values.items()
        if committed_values.get(key, missing) != value
    }
    _write_json_atomic(override, moved)
    if current != committed:
        defaults.write_bytes(committed)
    return moved

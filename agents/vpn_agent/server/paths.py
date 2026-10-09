"""
paths.py — Where server state lives on disk.

Key material must never sit inside the repo (it would be one `git add -A` away
from a public push) and never inside a frozen .app bundle (that breaks the code
signature and a reinstall wipes it). Everything goes to the per-user
application-support directory with restrictive permissions.

Override with VPN_AGENT_STATE_DIR — useful for tests and for keeping a site on
an encrypted volume.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

APP_NAME = "VPN Agent"
ENV_STATE_DIR = "VPN_AGENT_STATE_DIR"

DIR_MODE = 0o700
FILE_MODE = 0o600


def legacy_state_dir() -> Path:
    """Where the standalone VPN Agent kept its state before Sentinel owned it."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "vpn-agent"


_prepared: set[str] = set()
_notice: str = ""
MIGRATION_MARKER = ".migrated-from-vpn-agent"


def state_dir() -> Path:
    """Root directory for all VPN state that must survive reinstalls.

    Inside Sentinel this is ``<Sentinel data>/vpn``, so portable mode,
    Emergency Reset and Sentinel backups cover sites, keys, the Tor data dir,
    the proxy chain and the kill-switch state. ``VPN_AGENT_STATE_DIR`` still
    overrides it (tests, or a site kept on an encrypted volume).
    """
    override = os.environ.get(ENV_STATE_DIR)
    if override:
        return Path(override).expanduser()

    from services.runtime_paths import user_data_base

    root = user_data_base() / "vpn"
    key = str(root)
    if key not in _prepared:
        _prepared.add(key)
        _prepare(root)
    return root


def _prepare(root: Path) -> None:
    """Create the folder privately and bring over the standalone app's state once."""
    global _notice
    root.mkdir(parents=True, exist_ok=True)
    _harden_dir(root)
    marker = root / MIGRATION_MARKER
    legacy = legacy_state_dir()
    if marker.exists() or not legacy.is_dir() or legacy.resolve() == root.resolve():
        return
    copied = _copy_tree_no_overwrite(legacy, root)
    try:
        write_private(marker, f"copied {copied} file(s) from {legacy}\n")
    except OSError:
        pass
    if copied:
        _notice = (f"Copied {copied} file(s) of VPN state (sites, keys, profiles) from "
                   f"{legacy} into Sentinel's data folder. The original was left untouched; "
                   "delete it yourself once you have checked everything works.")


def migration_notice() -> str:
    """One-line message about the copy made on first run, or '' if none."""
    return _notice


def _copy_tree_no_overwrite(source: Path, dest: Path) -> int:
    """Copy files that do not exist at the destination; never move or delete."""
    import shutil

    copied = 0
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        target = dest / relative
        if item.is_symlink():
            continue
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _harden_dir(target)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            _harden_dir(target.parent)
            shutil.copyfile(item, target)
            os.chmod(target, FILE_MODE)
            copied += 1
    return copied


def sites_dir() -> Path:
    """Directory holding one JSON file per VPN site."""
    return state_dir() / "sites"


def site_file(site_name: str) -> Path:
    """Path to a single site's state file."""
    return sites_dir() / f"{slugify(site_name)}.json"


def exports_dir(site_name: str) -> Path:
    """Directory where generated client configs for a site are written."""
    return state_dir() / "exports" / slugify(site_name)


def profiles_file() -> Path:
    """
    The client-side profile list, in a location the app may actually write to.

    This file is written at runtime — selecting a profile persists it, and
    registering a built server appends to it. The copy shipped in config/ is a
    read-only seed: once frozen it lives inside the .app bundle, where writing
    would break the code signature and a reinstall would silently discard every
    profile the user had added.
    """
    return state_dir() / "vpn_profiles.json"


def slugify(name: str) -> str:
    """Reduce a display name to something safe for a filename or interface."""
    out = []
    for ch in name.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_.":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "site"


def ensure_private_dir(path: Path) -> Path:
    """Create a directory (and parents) that only this user can read."""
    path.mkdir(parents=True, exist_ok=True)
    _harden_dir(path)
    # Parents inside our own state dir get hardened too; anything above is the
    # user's own home layout and is left alone.
    root = state_dir()
    for parent in path.parents:
        if parent == root or root in parent.parents:
            _harden_dir(parent)
        if parent == root:
            break
    return path


def write_private(path: Path, text: str) -> Path:
    """
    Write a file containing secrets so that only this user can read it.

    The content goes into a new 0600 temp file (created exclusively, never
    following a link) beside the target and is then renamed over it, so an
    existing looser-mode file or a planted symlink never receives the secrets.
    """
    import tempfile

    path = Path(path)
    ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _harden_dir(path: Path) -> None:
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != DIR_MODE:
            os.chmod(path, DIR_MODE)
    except OSError:
        # A directory we do not own (or a race with another process) is not
        # worth crashing over — the file mode is the real protection.
        pass

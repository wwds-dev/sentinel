"""
store.py — Reading and writing site state.

Site files contain private keys, so every write goes through paths.write_private
and every read verifies the file is not group- or world-readable. A site file
that has become readable by others is reported loudly rather than silently
loaded: by the time you notice, the key material may already have been copied.
"""

from __future__ import annotations

import json
import os
import stat

from . import paths
from .model import Site


class InsecurePermissions(Exception):
    """Raised when a site file is readable by anyone but its owner."""


def list_sites() -> list[str]:
    """Return the display names of every stored site, sorted."""
    directory = paths.sites_dir()
    if not directory.is_dir():
        return []

    names = []
    for entry in sorted(directory.glob("*.json")):
        try:
            with entry.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            names.append(data.get("name") or entry.stem)
        except (OSError, json.JSONDecodeError):
            continue
    return names


def site_exists(name: str) -> bool:
    return paths.site_file(name).is_file()


def load_site(name: str, *, strict_permissions: bool = True) -> Site:
    """Load a site by display name."""
    path = paths.site_file(name)
    if not path.is_file():
        raise FileNotFoundError(f"No site named {name!r} at {path}")

    if strict_permissions:
        _assert_private(path)

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    try:
        site = Site.from_dict(data)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Site file {path.name} is malformed: {exc}") from exc
    site.check_strict()
    return site


def save_site(site: Site) -> None:
    """Persist a site, replacing any previous version atomically."""
    site.check_strict()
    path = paths.site_file(site.name)
    payload = json.dumps(site.to_dict(), indent=2, sort_keys=False)

    # Write to a sibling temp file first so an interrupted write cannot leave a
    # half-written site file — which would mean losing every peer's keys.
    tmp = path.with_suffix(".json.tmp")
    paths.write_private(tmp, payload)
    os.replace(tmp, path)
    os.chmod(path, paths.FILE_MODE)


def delete_site(name: str) -> bool:
    """
    Remove a site's state file and exported configs.

    This destroys the only copy of the server and CA private keys. Every client
    config issued by this site stops working, and there is no way to reissue
    them — deploying again produces a different server identity.
    """
    path = paths.site_file(name)
    removed = False
    if path.is_file():
        path.unlink()
        removed = True

    exports = paths.exports_dir(name)
    if exports.is_dir():
        for child in exports.iterdir():
            if child.is_file():
                child.unlink()
        exports.rmdir()
    return removed


def _assert_private(path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InsecurePermissions(
            f"{path} is readable beyond its owner (mode {mode:o}). It holds "
            f"private keys. Fix with: chmod 600 {path}"
        )

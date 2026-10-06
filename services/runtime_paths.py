"""Resolve bundled resources and writable state for normal and portable runs."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Mapping

APP_NAME = "Sentinel"
PORTABLE_ENV = "SENTINEL_PORTABLE_ROOT"
PORTABLE_MARKER = ".sentinel-portable"
PORTABLE_DATA_DIR = "Sentinel Data"

# Names the app used to ship under. They are not spellings of the product any
# more — they are the directories an older install left on disk, and they are
# only ever read, never written, so renaming them here would orphan that data
# rather than tidy anything up. The product itself is `APP_NAME` everywhere.
LEGACY_APP_NAMES = ("Sentinel Fork",)
LEGACY_PORTABLE_DATA_DIRS = ("Sentinel Fork Data",)
MIN_PORTABLE_FREE_BYTES = 256 * 1024 * 1024


class RuntimeDataError(RuntimeError):
    """Sentinel cannot safely resolve or migrate its writable state."""


class PortableRuntimeError(RuntimeDataError):
    """A portable volume cannot safely hold Sentinel's writable state."""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_base() -> Path:
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def portable_root_from_environment(env: Mapping[str, str] | None = None) -> Path | None:
    value = (env or os.environ).get(PORTABLE_ENV, "").strip()
    return Path(value).expanduser().resolve() if value else None


def portable_root() -> Path | None:
    """Return an explicit/marked portable root, never a guessed USB path."""
    configured = portable_root_from_environment()
    if configured is not None:
        return configured
    if not is_frozen():
        return None
    executable = Path(sys.executable).resolve()
    for candidate in list(executable.parents)[:5]:
        if (candidate / PORTABLE_MARKER).is_file():
            return candidate
    return None


def portable_data_base(root: Path) -> Path:
    return root / PORTABLE_DATA_DIR


def _migrate_legacy_directory(
    parent: Path, current_name: str, legacy_names: tuple[str, ...]
) -> Path:
    """Rename one legacy state directory when the new location is still absent."""
    current = parent / current_name
    if current.exists():
        return current
    for legacy_name in legacy_names:
        legacy = parent / legacy_name
        if legacy.exists():
            try:
                legacy.rename(current)
            except OSError as exc:
                raise RuntimeDataError(
                    f"Sentinel could not migrate its data folder from '{legacy.name}' "
                    f"to '{current.name}'. Close other copies and check folder permissions."
                ) from exc
            break
    return current


def validate_portable_volume(
    root: Path, min_free_bytes: int = MIN_PORTABLE_FREE_BYTES, *, probe: bool = True
) -> Path:
    """Validate presence, free space and actual write access."""
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise PortableRuntimeError(
            f"Portable volume is unavailable or has been ejected: {root}"
        )
    try:
        data = _migrate_legacy_directory(
            root, PORTABLE_DATA_DIR, LEGACY_PORTABLE_DATA_DIRS
        )
        data.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free < min_free_bytes:
            need = min_free_bytes // (1024 * 1024)
            have = free // (1024 * 1024)
            raise PortableRuntimeError(
                f"Portable volume is low on space ({have} MiB free; {need} MiB required)."
            )
        if probe:
            fd, name = tempfile.mkstemp(prefix=".sentinel-write-test-", dir=data)
            os.close(fd)
            Path(name).unlink()
    except RuntimeDataError as exc:
        if isinstance(exc, PortableRuntimeError):
            raise
        raise PortableRuntimeError(str(exc)) from exc
    except OSError as exc:
        raise PortableRuntimeError(
            f"Portable data folder is read-only or unavailable: {data} ({exc})"
        ) from exc
    return data


def is_portable() -> bool:
    return portable_root() is not None


def user_data_base() -> Path:
    root = portable_root()
    if root is not None:
        return validate_portable_volume(root)
    if is_frozen():
        parent = Path.home() / "Library" / "Application Support"
        parent.mkdir(parents=True, exist_ok=True)
        data = _migrate_legacy_directory(parent, APP_NAME, LEGACY_APP_NAMES)
        data.mkdir(parents=True, exist_ok=True)
        return data
    return Path(__file__).resolve().parent.parent


def ensure_seeded() -> None:
    """Seed missing defaults without replacing user configuration or secrets."""
    if not is_frozen() and not is_portable():
        return
    ub = user_data_base()
    rb = resource_base()
    try:
        dst_config = ub / "config"
        dst_config.mkdir(parents=True, exist_ok=True)
        src_config = rb / "config"
        if src_config.exists():
            for source in src_config.iterdir():
                destination = dst_config / source.name
                if destination.exists():
                    continue
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
        (ub / "data" / "chats").mkdir(parents=True, exist_ok=True)
        (ub / "data" / "logs").mkdir(parents=True, exist_ok=True)
        env_file = ub / ".env"
        if not env_file.exists():
            example = rb / ".env.example"
            if example.exists():
                shutil.copy2(example, env_file)
            else:
                env_file.write_text("# Add API keys here. This file stays local.\n", encoding="utf-8")
    except OSError as exc:
        if is_portable():
            raise PortableRuntimeError(
                f"Portable storage became unavailable while preparing Sentinel data: {exc}"
            ) from exc
        raise

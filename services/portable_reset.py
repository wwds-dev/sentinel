"""Narrow, guarded deletion of Sentinel-owned portable state."""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from services.runtime_paths import (
    PORTABLE_DATA_DIR, PORTABLE_MARKER, PortableRuntimeError,
    portable_root, user_data_base,
)

CONFIRMATION_PHRASE = "ERASE SENTINEL DATA"


class PartialEraseError(PortableRuntimeError):
    """Erasing started and then failed: some data may already be gone."""

# Set once an Emergency Reset starts deleting, and never cleared in this
# process (the reset ends by quitting). A parked worker thread that would still
# write into the data folder checks it first (spec no-thread-terminate, A5).
_ERASING = threading.Event()


def writes_allowed() -> bool:
    """False once an Emergency Reset has started erasing the data folder."""
    return not _ERASING.is_set()


def require_writes_allowed() -> None:
    """Raise instead of writing into a data folder that is being erased."""
    if _ERASING.is_set():
        raise PortableRuntimeError("Sentinel's data is being erased; nothing was written.")


def erase_portable_user_data() -> Path:
    """Delete only the marked portable data directory's contents.

    This is deliberately unavailable for development and normal installed
    builds. It does not format a volume or attempt to erase operating-system,
    network, provider, swap, or forensic records.
    """
    root = portable_root()
    if root is None:
        raise PortableRuntimeError("Emergency Reset is available only in USB-portable mode.")
    root = root.resolve()
    if not (root / PORTABLE_MARKER).is_file():
        raise PortableRuntimeError("Portable marker is missing; reset was refused.")
    data = user_data_base().resolve()
    expected = (root / PORTABLE_DATA_DIR).resolve()
    if data != expected or data.parent != root:
        raise PortableRuntimeError("Portable data path failed its safety check; reset was refused.")
    if not data.is_dir():
        raise PortableRuntimeError("Portable data folder is unavailable; reset was refused.")

    _ERASING.set()                 # before the first deletion; stays set once it succeeds
    try:
        for child in tuple(data.iterdir()):
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    except Exception as exc:
        # The app keeps running after a refused reset, so it must be able to
        # write again; the user is told the folder may be half erased.
        _ERASING.clear()
        raise PartialEraseError(f"Erasing stopped part-way: {exc}") from exc
    return data

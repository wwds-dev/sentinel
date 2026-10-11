"""Remove the log files an older Tunnel left behind.

Tunnel keeps no log. Earlier versions wrote ``tunnel_audit.jsonl`` (every
attempt, refused and declined ones included), ``vpn/openvpn.log`` and Tor's
``tor.log``. Those files sit on disk until something deletes them, so Sentinel
removes them once at start-up. Only these exact names are touched.
"""

from __future__ import annotations

from pathlib import Path


def legacy_log_paths(base: Path | None = None) -> list[Path]:
    if base is None:
        from services import runtime_paths
        base = runtime_paths.user_data_base()
    paths = [Path(base) / "data" / "logs" / "tunnel_audit.jsonl",
             Path(base) / "vpn" / "openvpn.log"]
    try:
        from agents.vpn_agent.services import tor
        paths.append(Path(tor.data_dir()) / "tor.log")
    except Exception:  # Tor support missing or its state folder unreadable
        pass
    return paths


def purge_legacy_logs(paths: list[Path] | None = None) -> list[str]:
    """Delete the old log files that exist. Returns what was removed."""
    removed: list[str] = []
    for path in (paths if paths is not None else legacy_log_paths()):
        try:
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        except OSError:
            continue
    return removed

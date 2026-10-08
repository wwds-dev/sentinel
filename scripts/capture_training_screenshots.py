#!/usr/bin/env python3
"""Render current Sentinel screens for the Learning Centre.

The capture uses an isolated temporary database and Qt's off-screen platform,
so it never exposes real chat history, keys, targets or local paths.
"""

from __future__ import annotations

import os
import sys
import tempfile
import argparse
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from services import database


OUTPUTS = {
    "chat": "workspace-chat.png",
    "osint": "trace.png",
    "osint_heavy": "bloodhound.png",
    "wifi": "beacon.png",
    "bug_bounty": "bug-spray.png",
    "vpn": "tunnel.png",
    "manager": "forge.png",
}


def settle(app: QApplication, widget) -> None:
    widget.layout().activate()
    for _ in range(8):
        app.processEvents()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "screens",
        nargs="*",
        metavar="SCREEN",
        help=(
            "Optional screen keys to capture: "
            + ", ".join([*OUTPUTS, "learning-centre"])
        ),
    )
    args = parser.parse_args()
    requested = args.screens or [*OUTPUTS, "learning-centre"]
    unknown = sorted(set(requested) - {*OUTPUTS, "learning-centre"})
    if unknown:
        parser.error("unknown screen(s): " + ", ".join(unknown))

    output_dir = ROOT / "docs" / "training" / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sentinel-training-") as tmp:
        database.DB_PATH = Path(tmp) / "sentinel.db"
        # Tunnel must never expose or depend on the operator's live VPN
        # profiles in documentation. With an isolated state directory it
        # uses the checked-in sample catalog instead.
        os.environ["VPN_AGENT_STATE_DIR"] = str(Path(tmp) / "vpn-agent")
        database.init_db()

        import main as sentinel_main

        # Agent selection saves the preferred model during a normal run.
        # A documentation capture must not change the developer's picks,
        # and should show the shipped defaults rather than those picks.
        sentinel_main.SETTINGS_OVERRIDE_FILE = Path(tmp) / "settings.local.json"
        from ui.learning_center import build_learning_center

        app = QApplication.instance() or QApplication([])
        window = sentinel_main.GodAI()
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.resize(1600, 1000)
        window.show()

        captured = 0
        for agent, filename in OUTPUTS.items():
            if agent not in requested:
                continue
            window.select_agent(agent)
            settle(app, window)
            if not window.grab().save(str(output_dir / filename)):
                raise RuntimeError(f"Could not save {filename}")
            captured += 1
            if agent == "vpn":
                # Action previews are deterministic and cannot execute, so
                # this is a useful second state to teach from safely.
                panel = window.panels["vpn"]
                panel.action_box.setCurrentText("Connect")
                panel.preview_action()
                settle(app, window)
                preview_name = "tunnel-action-preview.png"
                if not window.grab().save(str(output_dir / preview_name)):
                    raise RuntimeError(f"Could not save {preview_name}")
                captured += 1

        if "learning-centre" in requested:
            learning = build_learning_center(window)
            learning.setAttribute(Qt.WA_DontShowOnScreen, True)
            learning.resize(1120, 760)
            learning.show()
            settle(app, learning)
            if not learning.grab().save(str(output_dir / "learning-centre.png")):
                raise RuntimeError("Could not save learning-centre.png")
            learning.close()
            learning.deleteLater()
            captured += 1
        window.close()
        window.deleteLater()
        for _ in range(4):
            app.processEvents()

    print(f"Captured {captured} training screenshot(s) in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

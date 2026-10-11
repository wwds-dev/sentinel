#!/usr/bin/env python3
"""Sentry — entry point for standalone / headless use.

    python main.py selftest      verify the read-only collectors run on this host
    python main.py scan          dry-run comparison against the baseline
    python main.py watch-once    one persisted watch pass (what launchd calls)
    python main.py watch         foreground watch loop
    python main.py report        print the recorded findings log
    python main.py --headless    alias for watch-once (background watcher entry)
"""

import sys
from pathlib import Path

# Sentinel's own packages (services.runtime_paths above all) live two levels
# up; launchd starts this file with agents/sentry as the working directory.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sentry.cli import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    # launchd runs the watcher headless; map the shared flag to one watch pass.
    if "--headless" in argv:
        argv = ["watch-once"]
    sys.exit(main(argv))

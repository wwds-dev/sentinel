"""Private investigation output must never be committed.

Asks git itself whether the ignore rules cover the paths the app writes.
Needs git and a repository checkout; skips cleanly otherwise.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PRIVATE_PATHS = [
    "data/reports/x.txt",
    "data/chats/x.json",
    "data/sentinel.db",
]


@pytest.mark.allow_process
def test_private_data_paths_are_git_ignored():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")

    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", *PRIVATE_PATHS],
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = set(result.stdout.split())
    missing = [p for p in PRIVATE_PATHS if p not in ignored]
    assert not missing, f"not git-ignored: {missing}"

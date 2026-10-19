"""Provider registry — drift guard
=================================
Type: Cross-repository consistency check.

`services/providers.py` (the registry), `services/lmstudio_client.py`,
`services/local_models.py` and `services/local_ratings.py` (with its prompt set
and its tests) exist twice in this workspace, byte for byte, in Sentinel and in
Imprint, and so does the Settings → Local models page. A change to
one that is not made to the other would let the two apps disagree about which
providers are local, what is free, or which models carry the uncensored tag.

The test skips when the sibling is not checked out beside this repository
(a clone or a worktree). Set SIBLING_REPO_DIR to the sibling's folder to
compare against a checkout elsewhere.

**If this fails**, port the change to the other copy. Do not add exceptions:
unlike benchmarks.py, these two files have no allowed local differences.
"""

import difflib
import os
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
MINE = _HERE.parents[1]
SELF = MINE.name.split("-")[0]
OTHER = "imprint" if SELF == "sentinel" else "sentinel"
FILES = ("services/providers.py", "services/lmstudio_client.py",
         "services/local_models.py", "services/local_ratings.py",
         "config/local_rating_prompts.json", "tests/test_local_ratings.py",
         "ui/local_models_page.py")


def _sibling() -> Path | None:
    override = os.environ.get("SIBLING_REPO_DIR")
    for candidate in (Path(override) if override else None,
                      MINE.parent / OTHER, MINE.parent / "active" / OTHER):
        if candidate and (candidate / FILES[0]).is_file():
            return candidate
    return None


@pytest.mark.parametrize("relative", FILES)
def test_provider_modules_have_not_drifted(relative):
    other = _sibling()
    if other is None:
        pytest.skip(f"{OTHER} is not checked out beside this repository")
    a = (MINE / relative).read_text(encoding="utf-8").splitlines()
    b = (other / relative).read_text(encoding="utf-8").splitlines()
    if a != b:
        diff = "\n".join(list(difflib.unified_diff(
            a, b, fromfile=SELF, tofile=OTHER, lineterm="", n=1))[:60])
        pytest.fail(f"{relative} has drifted between {SELF} and {OTHER}.\n\n"
                    f"{diff}\n\nPort the change to both copies.")

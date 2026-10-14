"""The gate: no thread is ever terminated (spec no-thread-terminate, M1, A6).

QThread.terminate() stops a thread wherever it is. A thread killed while it
holds the GIL leaves every other Python thread, the UI thread included,
blocked for good: a white window that will not close. Ruff's banned-api rule
cannot see a method called on an instance, so this AST scan is the gate.

It flags any attribute named `terminate` (called or merely referenced) and any
reflective lookup of the string "terminate" (getattr, hasattr, setattr,
delattr, operator.methodcaller, QMetaObject.invokeMethod). A future process
handle that genuinely needs `.terminate()` (subprocess.Popen) gets an
ALLOWED entry naming its file, enclosing function and reason. An entry that
matches nothing, or fewer uses than are found, fails the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("ui", "services", "providers", "agents")
EXCLUDED = (ROOT / "agents" / "bug_spray",)     # a git submodule with its own suite

_REFLECTIVE = {"getattr", "hasattr", "setattr", "delattr", "methodcaller", "invokeMethod"}

# (path relative to the repo, enclosing function qualname) -> (uses, reason)
ALLOWED: dict[tuple[str, str], tuple[int, str]] = {
    ("main.py", "GodAI.stop_chat_worker"): (1, "thread: removed by task 7"),
    ("main.py", "GodAI.stop_background_work"): (2, "thread: removed by task 6"),
    ("ui/panels/vpn.py", "VpnPanel.shutdown"): (2, "thread: removed by task 5"),
    ("ui/panels/osint_heavy.py", "OsintHeavyPanel.shutdown"): (2, "thread: removed by task 5"),
    ("agents/vpn_agent/services/health_monitor.py", "HealthMonitor.stop"):
        (1, "thread: removed by task 4"),
}


class _Scanner(ast.NodeVisitor):
    def __init__(self):
        self.stack: list[str] = []
        self.found: list[tuple[str, int]] = []

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    def _note(self, node):
        self.found.append((".".join(self.stack) or "<module>", node.lineno))

    def visit_Attribute(self, node):
        if node.attr == "terminate":
            self._note(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in _REFLECTIVE and any(
                isinstance(arg, ast.Constant) and arg.value == "terminate" for arg in node.args):
            self._note(node)
        self.generic_visit(node)


def find_terminate_uses(source: str, path: str = "<string>") -> list[tuple[str, int]]:
    """Every `terminate` use in `source`, as (enclosing qualname, line)."""
    scanner = _Scanner()
    scanner.visit(ast.parse(source, filename=path))
    return scanner.found


def _scope_files() -> list[Path]:
    files = [ROOT / "main.py"]
    for name in SCAN_DIRS:
        for path in sorted((ROOT / name).rglob("*.py")):
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            if any(path.is_relative_to(ex) for ex in EXCLUDED):
                continue
            files.append(path)
    return files


def _all_uses() -> dict[tuple[str, str], list[int]]:
    uses: dict[tuple[str, str], list[int]] = {}
    for path in _scope_files():
        rel = path.relative_to(ROOT).as_posix()
        for qualname, line in find_terminate_uses(path.read_text(encoding="utf-8"), rel):
            uses.setdefault((rel, qualname), []).append(line)
    return uses


# ── The scanner itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize("source", [
    "w.terminate()",
    "self.chat_worker.terminate()",
    "getattr(w, 'terminate')()",
    "hasattr(w, 'terminate')",
    "f = w.terminate",
    "operator.methodcaller('terminate')(w)",
    "QMetaObject.invokeMethod(w, 'terminate')",
])
def test_the_scanner_finds(source):
    assert len(find_terminate_uses(source)) == 1


@pytest.mark.parametrize("source", [
    "x = {'terminate': []}",
    "url = f'{base}/intelligent/search/terminate'",
    "def terminate(self):\n    pass",
    "# worker.terminate()\nx = 1",
    "'''worker.terminate()'''",
    "w.kill()",
])
def test_the_scanner_ignores(source):
    assert find_terminate_uses(source) == []


def test_the_scanner_names_the_enclosing_function():
    source = "class A:\n    def stop(self):\n        self.t.terminate()\n"
    assert find_terminate_uses(source) == [("A.stop", 3)]


def test_one_more_terminate_in_main_is_found():
    """The mutation check the spec asks for: plant `worker.terminate()` in the
    real main.py, in memory, and the scanner must count exactly one more."""
    real = (ROOT / "main.py").read_text(encoding="utf-8")
    planted = real + "\n\ndef _planted(worker):\n    worker.terminate()\n"
    assert len(find_terminate_uses(planted)) == len(find_terminate_uses(real)) + 1


def test_the_scan_is_not_vacuous():
    files = _scope_files()
    rels = {p.relative_to(ROOT).as_posix() for p in files}
    assert len(files) > 100
    assert "main.py" in rels and "agents/vpn_agent/services/health_monitor.py" in rels
    assert not any(r.startswith("agents/bug_spray/") for r in rels)


# ── The gate ────────────────────────────────────────────────────────────────

def test_no_thread_is_terminated_outside_the_allow_list():
    uses = _all_uses()
    unexpected = {key: lines for key, lines in uses.items() if key not in ALLOWED}
    assert not unexpected, f"terminate used (never terminate a thread; park it): {unexpected}"
    over = {key: lines for key, lines in uses.items()
            if key in ALLOWED and len(lines) > ALLOWED[key][0]}
    assert not over, f"more terminate uses than the allow-list declares: {over}"


def test_no_allow_list_entry_is_stale():
    uses = _all_uses()
    stale = {key for key, (count, _) in ALLOWED.items() if len(uses.get(key, [])) != count}
    assert not stale, f"allow-list entries that no longer match their uses (remove them): {stale}"

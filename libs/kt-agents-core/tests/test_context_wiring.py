"""AST-based regression test: every AgentContext() and GraphEngine() call
in workflow/pipeline files must pass ``qdrant_client``.

This catches the class of bug where a new workflow helper constructs an
AgentContext or GraphEngine without forwarding the Qdrant client, which
silently disables vector-based deduplication and search.
"""

from __future__ import annotations

import ast
import pathlib

# Root of the repository
_REPO = pathlib.Path(__file__).resolve().parents[3]

# Files that construct AgentContext or GraphEngine and MUST pass qdrant_client.
# Test files and conftest are excluded — they legitimately omit services.
_SCAN_DIRS = [
    _REPO / "services",
    _REPO / "libs" / "kt-hatchet",
]

# Files explicitly allowed to omit qdrant_client (e.g. test helpers).
_ALLOWED_MISSING: set[str] = set()


def _find_python_files(dirs: list[pathlib.Path]) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for d in dirs:
        files.extend(d.rglob("*.py"))
    return sorted(files)


def _has_keyword(call: ast.Call, name: str) -> bool:
    """Check if an ast.Call node has a keyword argument with the given name."""
    return any(kw.arg == name for kw in call.keywords)


def _callee_name(call: ast.Call) -> str | None:
    """Extract the simple name of the function/class being called."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_test_file(path: pathlib.Path) -> bool:
    parts = path.parts
    return "tests" in parts or "test_" in path.name or path.name == "conftest.py"


class _Violation:
    def __init__(self, file: pathlib.Path, line: int, cls: str) -> None:
        self.file = file
        self.line = line
        self.cls = cls

    def __repr__(self) -> str:
        rel = self.file.relative_to(_REPO)
        return f"{rel}:{self.line} — {self.cls}() missing qdrant_client"


def test_all_agent_context_calls_pass_qdrant_client() -> None:
    """Every AgentContext() call in non-test service/lib code must include qdrant_client."""
    violations: list[_Violation] = []

    for path in _find_python_files(_SCAN_DIRS):
        if _is_test_file(path):
            continue
        rel = str(path.relative_to(_REPO))
        if rel in _ALLOWED_MISSING:
            continue

        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node)
            if name == "AgentContext" and not _has_keyword(node, "qdrant_client"):
                violations.append(_Violation(path, node.lineno, "AgentContext"))

    assert not violations, "AgentContext() calls missing qdrant_client:\n" + "\n".join(f"  {v}" for v in violations)


def test_all_graph_engine_calls_pass_qdrant_client() -> None:
    """Every GraphEngine() call in non-test service/lib code must include qdrant_client."""
    violations: list[_Violation] = []

    for path in _find_python_files(_SCAN_DIRS):
        if _is_test_file(path):
            continue
        rel = str(path.relative_to(_REPO))
        if rel in _ALLOWED_MISSING:
            continue

        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node)
            if name == "GraphEngine" and not _has_keyword(node, "qdrant_client"):
                violations.append(_Violation(path, node.lineno, "GraphEngine"))

    assert not violations, "GraphEngine() calls missing qdrant_client:\n" + "\n".join(f"  {v}" for v in violations)

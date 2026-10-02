"""The cursor is settable from N places; this test is the discipline.

History: docs/evidence/resolve_test_history.md#test_cursor_discipline.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

SCOPES = ("library", "bin")

#: file -> (expected site count, owner note). Capability probes are
#: explicitly leased because each probe changes the shared cursor.
CURSOR_SETTERS = {
    # The one implementation site; its runtime guard requires exclusive.
    "library/tools/resolve_lock.py": (
        1, "exclusive: assert_current_timeline runtime guard"),
    # Capability probes take the default exclusive @under_lease.
    "library/steps/step_6_01_render/probe_resolve_capabilities.py": (
        2, "exclusive: capability checks only"),
}

METHOD = "SetCurrentTimeline"


def _sites(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == METHOD]


def _call_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _explicitly_exclusive(value) -> bool:
    return (value is None
            or (isinstance(value, ast.Constant) and value.value is True))


def _is_exclusive_decorator(node) -> bool:
    if not isinstance(node, ast.Call) or _call_name(node.func) != "under_lease":
        return False
    exclusive = next((keyword.value for keyword in node.keywords
                      if keyword.arg == "exclusive"), None)
    return _explicitly_exclusive(exclusive)


def _exclusive_context(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    if name == "cursor_fence":
        return True
    if name != "resolve_lease":
        return False
    exclusive = next((keyword.value for keyword in node.keywords
                      if keyword.arg == "exclusive"), None)
    return _explicitly_exclusive(exclusive)


class _CursorWriterVisitor(ast.NodeVisitor):
    """Find direct cursor/project switches outside exclusive sections."""

    methods = {"SetCurrentTimeline", "SetCurrentProject"}

    def __init__(self, relative_path):
        self.relative_path = relative_path
        self.function = ""
        self.exclusive = False
        self.unprotected = []

    def visit_FunctionDef(self, node):
        previous_function, previous_exclusive = self.function, self.exclusive
        self.function = node.name
        self.exclusive = any(_is_exclusive_decorator(item)
                             for item in node.decorator_list)
        for statement in node.body:
            self.visit(statement)
        self.function, self.exclusive = previous_function, previous_exclusive

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_With(self, node):
        previous = self.exclusive
        if any(_exclusive_context(item.context_expr) for item in node.items):
            self.exclusive = True
        for statement in node.body:
            self.visit(statement)
        self.exclusive = previous

    def visit_Call(self, node):
        method = _call_name(node.func)
        guarded_runtime_site = (
            self.relative_path == "library/tools/resolve_lock.py"
            and self.function == "assert_current_timeline")
        if (method in self.methods and not self.exclusive
                and not guarded_runtime_site):
            self.unprotected.append((node.lineno, method, self.function))
        self.generic_visit(node)


def test_every_cursor_setter_is_registered():
    """No shipped file sets the cursor without a registry row."""
    seen = {}
    for scope in SCOPES:
        root = REPO_ROOT / scope
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            sites = _sites(path)
            if sites:
                seen[path.relative_to(REPO_ROOT).as_posix()] = sites
    unregistered = sorted(set(seen) - set(CURSOR_SETTERS))
    assert unregistered == [], (
        f"new {METHOD} call sites with no registry row: "
        + ", ".join(f"{name} (lines {seen[name]})"
                    for name in unregistered)
        + ". Route the establishment through library/tools/resolve_lock.py "
          "(assert_current_timeline in a lease, cursor_fence for a guarded "
          "section, cursor_excursion for move-and-return), or register the "
          "site in CURSOR_SETTERS with a reason."
    )


def test_cursor_and_project_switches_are_exclusive():
    """Every direct current-project/timeline switch is exclusive.

    `assert_current_timeline` is the sole runtime-guarded implementation
    site. The guard itself must keep its explicit exclusive-mode check;
    every other caller must be inside an exclusive lease or fence.
    """
    unprotected = []
    for scope in SCOPES:
        root = REPO_ROOT / scope
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            visitor = _CursorWriterVisitor(
                path.relative_to(REPO_ROOT).as_posix())
            visitor.visit(tree)
            unprotected.extend(
                f"{path.relative_to(REPO_ROOT)}:{line} "
                f"{method} in {function or '<module>'}"
                for line, method, function in visitor.unprotected)

    guard_path = REPO_ROOT / "library/tools/resolve_lock.py"
    guard_tree = ast.parse(guard_path.read_text(encoding="utf-8"))
    guard = next(node for node in guard_tree.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "assert_current_timeline")
    checks_exclusive = any(
        isinstance(node, ast.Call)
        and _call_name(node.func) == "exclusive_held"
        for node in ast.walk(guard))
    assert checks_exclusive, (
        "assert_current_timeline is exempt from the syntactic section "
        "check only because it enforces an exclusive lease at runtime")
    assert unprotected == [], (
        "current project/timeline setters must run inside an exclusive "
        "lease or fence: " + "; ".join(unprotected))

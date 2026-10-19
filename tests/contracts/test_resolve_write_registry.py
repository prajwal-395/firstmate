"""Every statically dispatched Resolve mutation is present in the P0.1 registry.

The registry is keyed by file, enclosing function, method, and occurrence within
that function. Line numbers are excluded from identity so routine edits do not
churn the inventory. Dynamic dispatch is listed separately in the inventory.
"""
import ast
import json
from pathlib import Path

from library.tools.ast_cache import parse_file

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "library"
REGISTRY = Path(__file__).with_name("resolve_write_registry.json")

# Resolve's scripting API uses these verbs for state changes.  This is an
# inventory detector, not an API ban: new names fail until classified.
MUTATION_PREFIXES = (
    "Add", "Apply", "Append", "Create", "Delete", "Import", "Insert",
    "Load", "Move", "Remove", "Rename", "Set", "Update", "Write",
)
IGNORED_GENERIC = {"append", "add", "update", "write", "remove"}


def _function_name(parents):
    function = "<module>"
    for parent in reversed(parents):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = parent.name
            break
    return function


def _sites():
    calls = []
    for path in LIBRARY.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = parse_file(path)
        def visit(node, parents, source_path):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith(MUTATION_PREFIXES)
                    and node.func.attr not in IGNORED_GENERIC):
                calls.append((source_path.relative_to(ROOT).as_posix(),
                              node.lineno, node.col_offset,
                              _function_name(parents), node.func.attr))
            for child in ast.iter_child_nodes(node):
                visit(child, [*parents, node], source_path)
        visit(tree, [], path)
    occurrences = {}
    found = set()
    for path, _line, _column, function, method in sorted(calls):
        base = (path, function, method)
        occurrence = occurrences.get(base, 0) + 1
        occurrences[base] = occurrence
        found.add((*base, occurrence))
    return found


def test_production_resolve_mutations_are_registered():
    expected = {tuple(row) for row in json.loads(REGISTRY.read_text())}
    actual = _sites()
    missing = sorted(actual - expected)
    stale = sorted(expected - actual)
    assert not missing and not stale, (
        "Resolve write inventory changed. Classify each new call site and "
        "regenerate docs/RESOLVE_WRITE_INVENTORY.md; remove stale rows. "
        f"Unregistered={missing}; stale={stale}")

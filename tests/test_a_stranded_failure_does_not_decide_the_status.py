"""A recorded failure of a step with no DAG node is reported, never
dropped, and does not decide the run status.

History: docs/evidence/run_status.md.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUN_PIPELINE = REPO / "library" / "processes" / "edit_video" / "run_pipeline.py"


def _run_function() -> ast.FunctionDef:
    tree = ast.parse(RUN_PIPELINE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and any(
                isinstance(sub, ast.Name) and sub.id == "stranded_failures"
                for sub in ast.walk(node)):
            return node
    raise AssertionError("nothing in run_pipeline.py names stranded_failures")


def _assignment(func: ast.FunctionDef, name: str) -> ast.AST:
    for node in ast.walk(func):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name):
            return node.value
    raise AssertionError(f"{name} is never assigned")


def test_the_status_is_decided_on_the_half_the_dag_still_contains():
    """The whole point: every recorded failure lands in exactly one half
    (nothing dropped by the split), and a stranded entry is reported and
    does not fail the run."""
    func = _run_function()
    stranded = ast.unparse(_assignment(func, "stranded_failures"))
    outstanding = ast.unparse(_assignment(func, "outstanding_failures"))
    assert stranded == (
        "[n for n in recorded_failures if n not in nodes]")
    assert outstanding == (
        "[n for n in recorded_failures if n in nodes]")
    decisions = [ast.unparse(node.test) for node in ast.walk(func)
                 if isinstance(node, ast.If)
                 and "outstanding_failures" in ast.unparse(node.test)
                 and "FAILED" in ast.unparse(node)]
    assert decisions == ["outstanding_failures or failed"], decisions
    for node in ast.walk(func):
        if isinstance(node, ast.If) and "FAILED" in ast.unparse(node):
            assert "stranded_failures" not in ast.unparse(node.test)

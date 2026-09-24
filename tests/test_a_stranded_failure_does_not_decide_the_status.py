"""A recorded failure of a step the pipeline no longer runs.

`failed_steps` is current state, not a log (AGENTS.md section 3): an
entry is removed when that step SUCCEEDS.  So a recorded failure naming
a step with no DAG node can never be cleared - the step never runs, and
the only code path that clears an entry is unreachable for it.

That is exactly what unwiring `prosody_analysis` (#F5,
docs/PROSODY_MEASURED.md) left on project 001, whose `failed_steps` reads
`['validate', 'prosody_analysis']`.  Holding every future run of that
project at FAILED on a step this pipeline has stopped running is not a
verdict about the run.

So the runner PARTITIONS the recorded failures against the DAG's own
nodes.  The stranded half is reported by name, first, saying no run can
clear it - never dropped, because going quiet about a recorded failure is
the trap `failed_steps` exists to avoid - and it does not decide
`status`.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUN_PIPELINE = REPO / "library" / "processes" / "edit_video" / "run_pipeline.py"
DAG = REPO / "library" / "processes" / "edit_video" / "dag.json"


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


def test_the_two_halves_partition_the_recorded_failures():
    """Every recorded failure lands in exactly one half, so nothing can
    be dropped by the split itself."""
    func = _run_function()
    stranded = ast.unparse(_assignment(func, "stranded_failures"))
    outstanding = ast.unparse(_assignment(func, "outstanding_failures"))
    assert stranded == (
        "[n for n in recorded_failures if n not in nodes]")
    assert outstanding == (
        "[n for n in recorded_failures if n in nodes]")


def test_the_status_is_decided_on_the_half_the_dag_still_contains():
    """The whole point: a stranded entry is reported and does not fail
    the run."""
    func = _run_function()
    decisions = [ast.unparse(node.test) for node in ast.walk(func)
                 if isinstance(node, ast.If)
                 and "outstanding_failures" in ast.unparse(node.test)
                 and "FAILED" in ast.unparse(node)]
    assert decisions == ["outstanding_failures or failed"], decisions
    for node in ast.walk(func):
        if isinstance(node, ast.If) and "FAILED" in ast.unparse(node):
            assert "stranded_failures" not in ast.unparse(node.test)


def test_a_stranded_failure_is_still_named_in_the_summary():
    """Reported, not dropped. The line says why it cannot be cleared."""
    source = RUN_PIPELINE.read_text(encoding="utf-8")
    assert "if stranded_failures:" in source
    assert "no longer a step of this pipeline" in source
    assert "does not decide the status" in source





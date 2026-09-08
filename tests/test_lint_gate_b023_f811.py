"""B023 and F811 are enforced with no exceptions, and the fixed sites stay fixed.

The staged gate in `ruff-ci-gate.toml` used to grandfather 24 B023
(a closure over loop variables, all in `run_pipeline.py`) and 1 F811
(a redefined import) by file.  A file listed under `per-file-ignores`
is exempt from that rule ENTIRELY, so each listed line was weaker than
it read: a NEW unbound closure in `run_pipeline.py` would still have
passed.  The lines are deleted, which is what makes the gate real -
and these tests are what keep them deleted, without needing ruff
itself on the machine that runs the suite.

The code-shape halves pin the three sites directly: a reintroduction
fails here even before CI's ruff half runs.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_CONFIG = REPO_ROOT / "ruff-ci-gate.toml"
RUN_PIPELINE = REPO_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"
CAPTION_CASE_TEST = REPO_ROOT / "tests" / "test_caption_case.py"
SUBJECT_FRAMING_TEST = REPO_ROOT / "tests" / "test_subject_framing.py"


def _deferred_files(code: str) -> list[str]:
    config = tomllib.loads(GATE_CONFIG.read_text(encoding="utf-8"))
    return sorted(
        path
        for path, codes in config["lint"]["per-file-ignores"].items()
        if code in codes
    )


def test_b023_is_enforced_with_no_exceptions():
    """No file is exempt from the unbound-loop-closure rule.

    All 24 findings were one closure in `run_pipeline.py`; it now binds
    its loop variables, so the deferral line is deleted rather than kept.
    """
    assert _deferred_files("B023") == [], (
        "ruff-ci-gate.toml still exempts files from B023, which means a "
        "new closure over a loop variable in a listed file passes the gate."
    )


def test_f811_is_enforced_with_no_exceptions():
    """No file is exempt from the redefined-while-unused rule."""
    assert _deferred_files("F811") == [], (
        "ruff-ci-gate.toml still exempts files from F811, which means a "
        "new redefined import in a listed file passes the gate."
    )


def _names_bound_in_loop(loop: ast.For) -> set[str]:
    bound: set[str] = set()

    def visit_target(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            bound.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                visit_target(elt)
        elif isinstance(target, ast.Starred):
            visit_target(target.value)

    visit_target(loop.target)
    for node in ast.walk(loop):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def _free_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    loaded = {
        node.id for node in ast.walk(func)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    bound = {a.arg for a in (*func.args.args, *func.args.kwonlyargs)}
    if func.args.vararg is not None:
        bound.add(func.args.vararg.arg)
    if func.args.kwarg is not None:
        bound.add(func.args.kwarg.arg)
    bound.update(
        node.id for node in ast.walk(func)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )
    return loaded - bound


def test_execute_step_once_binds_its_loop_variables():
    """The retry closure cannot see a later iteration's step.

    `execute_step_once` is defined inside `for node_id in steps_to_run`
    and closed over the loop-assigned `impl`, `inputs` and `node_id`.
    It is called immediately today, so the defect was latent - but a
    closure that escapes its iteration (a deferred retry, a logged
    partial) would run the WRONG step with no error.  The loop variables
    must arrive as bound arguments, whatever the call timing.
    """
    tree = ast.parse(RUN_PIPELINE.read_text(encoding="utf-8"))
    funcs = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "execute_step_once"
    ]
    assert len(funcs) == 1, (
        "expected exactly one `execute_step_once` in run_pipeline.py - "
        "it moved or was renamed, and this pin must move with it."
    )
    func = funcs[0]
    loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.For) and func in ast.walk(node)
    ]
    assert loops, "`execute_step_once` is no longer defined inside a loop"
    loop_bound = _names_bound_in_loop(loops[0])
    unbound = _free_names(func) & loop_bound
    assert not unbound, (
        f"`execute_step_once` closes over loop variables without binding "
        f"them: {sorted(unbound)}. Bind each as a default argument so the "
        f"closure keeps its own iteration's values."
    )


def test_no_module_level_import_binds_twice():
    """The `import os` redefinition in test_caption_case.py stays gone.

    A name bound twice by module-level imports keeps only the second
    binding; the first reads as a dependency that is not one.
    """
    tree = ast.parse(CAPTION_CASE_TEST.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    for node in tree.body:
        bound: list[str] = []
        if isinstance(node, ast.Import):
            bound = [(a.asname or a.name.split(".")[0]) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            bound = [(a.asname or a.name) for a in node.names]
        for name in bound:
            assert name not in seen, (
                f"{CAPTION_CASE_TEST.name} binds {name!r} by import on line "
                f"{node.lineno}, already bound on line {seen[name]}."
            )
            seen[name] = node.lineno


def test_no_dict_literal_repeats_a_key():
    """The swallowed `temporal_event_indices` entry stays gone.

    The reproducer dict repeated its key, so Python kept only the second
    entry and the first - the one proving the other input shape - was a
    comment wearing a test's clothes.  No dict literal in this file may
    repeat a constant key.
    """
    tree = ast.parse(SUBJECT_FRAMING_TEST.read_text(encoding="utf-8"))
    repeated: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        repeated.extend((node.lineno, k) for k in dupes)
    assert not repeated, (
        f"{SUBJECT_FRAMING_TEST.name} repeats dict keys "
        f"(line, key): {repeated} - the first entry is silently discarded."
    )

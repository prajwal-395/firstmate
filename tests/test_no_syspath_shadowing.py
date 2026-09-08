"""Collection order must not decide which module a bare name binds to.

Incident: PR 624 added a test doing a bare ``import step``. Run solo the
file passed; run under full-directory collection its tests failed, because
``test_audio_mix_delivery`` had already inserted ``step_6_01_render`` at
``sys.path[0]`` at import time, so 624's own ``if STEP_DIR not in
sys.path`` guard declined to re-insert its directory and ``import step``
bound ``step_5_04``'s ``step.py`` instead. Every step package contains a
module named ``step.py``, so the collision is guaranteed rather than
unlucky - and it is invisible to every scoped run, which is why it
survived until the integration gate.

Class rule, two halves:

1. No test module may put a non-root directory on ``sys.path`` at import
   time (the mechanism). The repo root is the only import-time entry a
   test module may add; ``tests/conftest.py`` is the single sanctioned
   owner of every other entry, because it is imported before every test
   module, deterministically, so collection order cannot change what it
   establishes.
2. No test module may bind a bare name that also exists as a repo-local
   module (the trigger). Reach step bodies, bridges and sibling helpers
   by absolute package path (``library.steps.<step>.resolve_build_timeline``)
   or by the ``tests.<module>`` namespace - never by a bare name that
   resolves through whatever directory another module inserted first.

Both halves are derived, not listed: the shadowable names are the stems
of every ``.py`` file under the step, process, execution, tool and test
trees, and each ``sys.path`` entry is judged by evaluating the file's own
expression with ``__file__`` bound to that file.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

# Trees whose ``.py`` stems are repo-local module names a bare import
# could bind. ``library/tools/fusion/tests`` is a packaged root with its
# own import semantics and is covered by its own suite, not by this guard.
_LOCAL_TREES = (
    "library/steps/**/*.py",
    "library/processes/**/*.py",
    "library/tools/**/*.py",
    "tests/**/*.py",
)


def _shadowable_names() -> set[str]:
    """Bare names that exist as repo-local modules (minus the stdlib)."""
    stems: set[str] = set()
    for pattern in _LOCAL_TREES:
        for path in REPO_ROOT.glob(pattern):
            stems.add(path.stem)
    return stems - set(sys.stdlib_module_names)


def _test_files() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _bare_import_violations() -> list[str]:
    """Files binding a bare repo-local name at any depth."""
    shadowable = _shadowable_names()
    violations = []
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "." not in alias.name and alias.name in shadowable:
                        violations.append(
                            f"{path.name}:{node.lineno}: bare "
                            f"`import {alias.name}`"
                        )
            elif isinstance(node, ast.ImportFrom):
                if (
                    node.level == 0
                    and node.module is not None
                    and node.module in shadowable
                ):
                    violations.append(
                        f"{path.name}:{node.lineno}: bare "
                        f"`from {node.module} import ...`"
                    )
    return violations


def _import_time_path_calls(tree: ast.AST):
    """Top-level ``sys.path.insert/append`` calls (through if/for/try).

    Returns ``(call, extra_args)`` pairs: for a call inside a
    ``for <var> in (<lit>, ...)`` loop whose path argument is that loop
    variable, every loop element must be judged, so the elements are
    returned alongside the call.
    """
    found = []

    def is_path_call(stmt) -> ast.Call | None:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            func = call.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("insert", "append")
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"
                and call.args
            ):
                return call
        return None

    def visit(statements, loop_var: str | None = None,
              loop_elts: list | None = None) -> None:
        for stmt in statements:
            call = is_path_call(stmt)
            if call is not None:
                judged = [call.args[-1]]
                if (
                    loop_var is not None
                    and loop_elts is not None
                    and isinstance(call.args[-1], ast.Name)
                    and call.args[-1].id == loop_var
                ):
                    judged = list(loop_elts)
                found.append((call, judged))
            elif isinstance(stmt, (ast.If, ast.While, ast.With, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(stmt, field, []), loop_var, loop_elts)
                if isinstance(stmt, ast.Try):
                    for handler in stmt.handlers:
                        visit(handler.body, loop_var, loop_elts)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                target = stmt.target.id if isinstance(
                    stmt.target, ast.Name) else None
                elts = None
                if isinstance(stmt.iter, (ast.Tuple, ast.List)):
                    elts = list(stmt.iter.elts)
                visit(stmt.body, target, elts)
                visit(stmt.orelse, loop_var, loop_elts)

    visit(tree.body)
    return found


def _resolve_added_dir(path: Path, tree: ast.AST, call: ast.Call,
                     judged_args: list) -> list[str]:
    """Evaluate the file's own path expressions; fail closed on dynamic.

    Returns every directory the call can add: the call's own argument,
    or each element of an enclosing ``for`` loop when the argument is
    that loop's variable.
    """
    args = list(judged_args)
    assignments: dict[str, ast.Assign] = {}
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
        ):
            assignments[stmt.targets[0].id] = stmt
    sandbox: dict = {
        "os": os,
        "sys": sys,
        "Path": Path,
        "__file__": str(path),
    }
    for _ in range(len(assignments) + 1):
        progressed = False
        for name, stmt in assignments.items():
            if name in sandbox:
                continue
            names_in_value: set[str] = set()
            for child in ast.walk(stmt.value):
                if isinstance(child, ast.Name) and isinstance(
                        child.ctx, ast.Load):
                    names_in_value.add(child.id)
            if any(n not in sandbox for n in names_in_value):
                continue
            sandbox[name] = eval(  # noqa: S307 - test-only, local tree
                compile(ast.Expression(stmt.value), str(path), "eval"),
                dict(sandbox),
            )
            progressed = True
        if not progressed:
            break
    resolved = []
    for arg in args:
        try:
            value = eval(  # noqa: S307 - test-only, local tree
                compile(ast.Expression(arg), str(path), "eval"),
                dict(sandbox),
            )
        except Exception as exc:
            raise ValueError(
                f"unresolvable sys.path expression: {exc}") from exc
        resolved.append(os.path.realpath(os.fspath(value)))
    return resolved


def _path_violations() -> list[str]:
    """Files adding a non-root directory to sys.path at import time."""
    violations = []
    want = os.path.realpath(REPO_ROOT)
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call, judged_args in _import_time_path_calls(tree):
            try:
                resolved = _resolve_added_dir(path, tree, call, judged_args)
            except ValueError as exc:
                violations.append(f"{path.name}:{call.lineno}: {exc}")
                continue
            for directory in resolved:
                if directory != want:
                    violations.append(
                        f"{path.name}:{call.lineno}: adds {directory} "
                        f"(only {want} may be added at import time)"
                    )
    return violations


def test_no_bare_import_of_a_repo_local_module():
    violations = _bare_import_violations()
    assert not violations, (
        "bare imports of repo-local modules bind whichever directory "
        "another test module inserted first under full collection; use "
        "an absolute package path instead:\n" + "\n".join(violations)
    )


def test_no_import_time_syspath_beyond_the_repo_root():
    violations = _path_violations()
    assert not violations, (
        "sys.path is process-global and these run at import time, so "
        "collection order decides what later imports bind; move non-root "
        "entries to tests/conftest.py:\n" + "\n".join(violations)
    )

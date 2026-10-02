"""One fast static check phase over the repository's own source text.

The plan's item 8: policies about what the source may SAY - no absolute
home paths, no direct Resolve access, no creative defaults in bridge
code, no sys.path games, no second implementation beside a step - lived one
per test file, each re-walking the tree with its own scanner.  They are
closer to custom lint rules than to behavioural tests, so they run
together here, in one pass, with one `problems() == []` assertion.

What lives here, by section
---------------------------
1. `home_paths` - no machine-specific absolute path (`/Users/<name>`,
   `/home/<name>`) in a code string literal.  `Path.home()` and
   `expanduser` are the sanctioned spellings; docstrings are prose,
   not configuration, and are not read. Applies to production and test
   Python; the safety test that must read the sandboxed constant is its
   one documented exception below.
2. `resolve_access` - Resolve is reached through
   `resolve_locale.scriptapp_preserving_locale`, never `dvr.scriptapp`
   directly (AGENTS.md 9: the call resets `LC_CTYPE` down in
   Blackmagic's library).  One call site, in that module, and the
   FakeResolve stand-in beside it.
3. `syspath` - no test module puts a non-root directory on `sys.path`
   at import time, and no test module binds a bare repo-local name
   (the planted cases live in `tests/test_static_check.py`).
4. `operation_resolution` - every operation's `run` resolves to its
   owning step's own directory, or to a `library/tools/` module that
   step already imports (Ruling 1, documented in
   `docs/evidence/operations.md`).
5. `creative_code_markers` - removed floor shapes reappearing in
   bridge code (inject_default_*, plan-size recommendations, density
   scalers). The scanned step set comes from capabilities marked
   `model_decides_quantity`; Prompt TEXT is deliberately not scanned:
   the quota vocabulary fails correct output on validity language
   (mesh_spine's "at least one content block" is a validity floor, not a
   quota), so prompt text stays covered by the registry creative policy
   and sparse-plan behaviour.
6. `project_root_reads` - tests may not read the configured projects
   root constant. The end-to-end sandbox test is exempt because it must
   compare that runtime value with the temporary root installed by
   `conftest.py`.

What does NOT live here
-----------------------
Behavioural proofs - a sparse plan really passing, a bridge really
emitting only declared keys - stay as scenario tests.  The contract
graph (producers, consumers, requirements, scopes, effects) lives in
`library/tools/contract_audit.py`.

    python3 -m library.tools.static_check --check
"""

from __future__ import annotations

import argparse
import ast
import functools
import inspect
import json
import os
import operator
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS_ROOT = REPO_ROOT / "library" / "steps"
TOOLS_ROOT = REPO_ROOT / "library" / "tools"
TESTS_ROOT = REPO_ROOT / "tests"

#: The one module allowed to call `.scriptapp(` for real, plus the test
#: colony under `library/tools/fusion/tests/` which never touches
#: Resolve (it parses `.comp` text and needs no connection).
RESOLVE_LOCALE = TOOLS_ROOT / "resolve_locale.py"


def _rel(path: Path) -> str:
    """Repo-relative where inside the checkout, plain path otherwise."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return os.fspath(path)


def _python_files(*roots: Path):
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _docstring_lines(tree: ast.AST) -> set:
    """Line numbers belonging to docstrings, so prose is not policed."""
    lines = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                end = getattr(body[0], "end_lineno", None)
                if end:
                    lines.update(range(body[0].lineno, end + 1))
    return lines


# ── 1. home paths ────────────────────────────────────────────────────

#: Machine-specific home roots.  Kept in one constant so the vocabulary
#: lives once; a literal that IS one of these prefixes (this table
#: itself) is not a path and is not flagged.
HOME_ABSOLUTE_PREFIXES = ("/Users/", "/home/")


def home_paths(roots=None) -> list:
    """Machine-specific absolute paths in code string literals."""
    roots = (
        (REPO_ROOT / "library", REPO_ROOT / "ren", REPO_ROOT / "tests")
        if roots is None
        else tuple(roots)
    )
    out = []
    for path in _python_files(*roots):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        prose = _docstring_lines(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.lineno not in prose
                and node.value not in HOME_ABSOLUTE_PREFIXES
                and any(p in node.value for p in HOME_ABSOLUTE_PREFIXES)
            ):
                out.append(
                    f"{_rel(path)}:{node.lineno}: "
                    f"absolute home path in code "
                    f"{node.value[:70]!r} - use Path.home() or "
                    f"expanduser so no checkout names its machine"
                )
    return out


def project_root_reads(files=None) -> list:
    """Test source that reads the configured real-projects root constant.

    The end-to-end project sandbox test necessarily reads the value it
    is asserting about, so it is excluded here; its decoy-root runtime
    check remains the stronger proof that collection cannot reach real
    project files.
    """
    exempt = (TESTS_ROOT / "test_tests_never_reach_real_projects.py").resolve()
    candidates = _test_files() if files is None else files
    out = []
    for path in candidates:
        if path.resolve() == exempt:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "PROJECTS_ROOT":
                out.append(f"{path.name}:{node.lineno}: .PROJECTS_ROOT")
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == "PROJECTS_ROOT" for alias in node.names):
                    out.append(
                        f"{path.name}:{node.lineno}: from {node.module} import PROJECTS_ROOT"
                    )
    return out


# ── 2. Resolve access ────────────────────────────────────────────────


def resolve_access(roots=None) -> list:
    """`.scriptapp(` calls outside the locale-preserving wrapper."""
    roots = (
        (REPO_ROOT / "library", REPO_ROOT / "ren") if roots is None else tuple(roots)
    )
    out = []
    for path in _python_files(*roots):
        if path == RESOLVE_LOCALE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if ".scriptapp(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "scriptapp"
            ):
                out.append(
                    f"{_rel(path)}:{node.lineno}: "
                    f"dvr.scriptapp called directly - reach Resolve "
                    f"through resolve_locale.scriptapp_preserving_locale, "
                    f"which restores LC_CTYPE afterwards"
                )
    return out


# ── 3. sys.path discipline in tests/ ─────────────────────────────────
#
# Ported from tests/test_no_syspath_shadowing.py: PR 624's incident, in
# which a bare `import step` bound whichever step directory another test
# module had inserted first, so collection order decided the binding.


_LOCAL_TREES = (
    "library/steps/*.py",
    "library/steps/*/*.py",
    "library/processes/*/*.py",
    "library/tools/*.py",
    "library/tools/*/*.py",
    "tests/*.py",
)


def _shadowable_names() -> set:
    stems: set = set()
    for pattern in _LOCAL_TREES:
        for path in REPO_ROOT.glob(pattern):
            stems.add(path.stem)
    return stems - set(sys.stdlib_module_names)


def _test_files(root=None) -> list:
    return sorted((TESTS_ROOT if root is None else root).glob("test_*.py"))


def _bare_import_violations(files=None) -> list:
    shadowable = _shadowable_names()
    violations = []
    for path in _test_files() if files is None else files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "." not in alias.name and alias.name in shadowable:
                        violations.append(
                            f"{path.name}:{node.lineno}: bare `import {alias.name}`"
                        )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and node.module in shadowable
            ):
                violations.append(
                    f"{path.name}:{node.lineno}: bare `from {node.module} import ...`"
                )
    return violations


def _import_time_path_calls(tree: ast.AST):
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

    def visit(statements, loop_var=None, loop_elts=None) -> None:
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
                target = stmt.target.id if isinstance(stmt.target, ast.Name) else None
                elts = None
                if isinstance(stmt.iter, (ast.Tuple, ast.List)):
                    elts = list(stmt.iter.elts)
                visit(stmt.body, target, elts)
                visit(stmt.orelse, loop_var, loop_elts)

    visit(tree.body)
    return found


def _static_value(node: ast.AST, namespace: dict):
    """Resolve the small, side-effect-free expression subset used for paths."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in namespace:
            return namespace[node.id]
        raise ValueError(f"unknown name {node.id!r}")
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [_static_value(item, namespace) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {
            _static_value(key, namespace): _static_value(value, namespace)
            for key, value in zip(node.keys, node.values)
        }
    if isinstance(node, ast.Set):
        return {_static_value(item, namespace) for item in node.elts}
    if isinstance(node, ast.Attribute):
        owner = _static_value(node.value, namespace)
        if owner is os and node.attr == "path":
            return os.path
        if owner is sys and node.attr == "path":
            return sys.path
        if owner is os.path and node.attr in {"abspath", "dirname", "join"}:
            return getattr(os.path, node.attr)
        if isinstance(owner, Path) and node.attr in {"parent", "parents"}:
            return getattr(owner, node.attr)
        if isinstance(owner, Path) and node.attr == "resolve":
            return owner.resolve
        raise ValueError(f"unsupported attribute {node.attr!r}")
    if isinstance(node, ast.Subscript):
        owner = _static_value(node.value, namespace)
        index = _static_value(node.slice, namespace)
        try:
            return owner[index]
        except (IndexError, KeyError, TypeError) as exc:
            raise ValueError(f"cannot resolve subscript: {exc}") from exc
    if isinstance(node, ast.BinOp):
        left = _static_value(node.left, namespace)
        right = _static_value(node.right, namespace)
        operation = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
        }.get(type(node.op))
        if operation is None:
            raise ValueError(f"unsupported operator {type(node.op).__name__}")
        return operation(left, right)
    if isinstance(node, ast.UnaryOp):
        value = _static_value(node.operand, namespace)
        operation = {
            ast.UAdd: operator.pos,
            ast.USub: operator.neg,
            ast.Not: operator.not_,
        }.get(type(node.op))
        if operation is None:
            raise ValueError(f"unsupported operator {type(node.op).__name__}")
        return operation(value)
    if isinstance(node, ast.Call):
        function = _static_value(node.func, namespace)
        args = [_static_value(arg, namespace) for arg in node.args]
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        if function is str and len(args) == 1:
            return str(args[0])
        if function is Path and len(args) == 1:
            return Path(args[0])
        if function in {os.path.abspath, os.path.dirname, os.path.join}:
            return function(*args)
        if (
            getattr(function, "__self__", None) is not None
            and getattr(function, "__name__", None) == "resolve"
            and not args
        ):
            return function()
        raise ValueError("unsupported path expression call")
    raise ValueError(f"unsupported expression {type(node).__name__}")


def _resolve_added_dir(path: Path, tree: ast.AST, judged_args: list) -> list:
    args = list(judged_args)
    assignments: dict = {}
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
        "str": str,
        "__file__": str(path),
    }
    pending = {
        child.id
        for arg in args
        for child in ast.walk(arg)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        and child.id not in sandbox
    }
    for _ in range(len(assignments) + 1):
        progressed = False
        for name in tuple(pending):
            stmt = assignments.get(name)
            if stmt is None:
                continue
            names_in_value = {
                child.id
                for child in ast.walk(stmt.value)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
            if any(n not in sandbox for n in names_in_value):
                continue
            sandbox[name] = _static_value(stmt.value, dict(sandbox))
            pending.remove(name)
            pending.update(names_in_value - sandbox.keys())
            progressed = True
        if not progressed:
            break
    resolved = []
    for arg in args:
        try:
            value = _static_value(arg, dict(sandbox))
        except Exception as exc:
            raise ValueError(f"unresolvable sys.path expression: {exc}") from exc
        resolved.append(os.path.realpath(os.fspath(value)))
    return resolved


def _path_violations(files=None) -> list:
    violations = []
    want = os.path.realpath(REPO_ROOT)
    for path in _test_files() if files is None else files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call, judged_args in _import_time_path_calls(tree):
            try:
                resolved = _resolve_added_dir(path, tree, judged_args)
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


def syspath(files=None) -> list:
    """Import-time path games and bare repo-local imports in tests/."""
    out = [
        f"{v} - bare imports bind whichever directory another test "
        f"module inserted first; use an absolute package path"
        for v in _bare_import_violations(files)
    ]
    out += [
        f"{v} - sys.path is process-global and this runs at import "
        f"time, so collection order decides later bindings; move "
        f"non-root entries to tests/conftest.py"
        for v in _path_violations(files)
    ]
    return out


# ── 4. Ruling 1: an operation owns no logic ──────────────────────────
#
# every `Operation.run` resolves to the owning step's own directory, or
# to a `library/tools/` module that step already imports.


def _tools_imported_by(step_dir: Path) -> set:
    mods = set()
    for path in step_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in ("tools", "library.tools"):
                    for alias in node.names:
                        if alias.name == "*":
                            mods.add("tools")
                        elif (TOOLS_ROOT / f"{alias.name}.py").is_file() or (
                            TOOLS_ROOT / alias.name
                        ).is_dir():
                            mods.add(f"tools.{alias.name}")
                        else:
                            mods.add("tools")
                    continue
                named = [node.module]
            elif isinstance(node, ast.Import):
                named = [a.name for a in node.names]
            else:
                continue
            for module in named:
                if module.startswith("library.tools."):
                    mods.add(module[len("library.") :])
                elif module.startswith("tools."):
                    mods.add(module)
                elif module in ("tools", "library.tools"):
                    mods.add("tools")
    return mods


def _source_file(run: Callable) -> Path:
    while isinstance(run, functools.partial):
        run = run.func
    found = inspect.getsourcefile(inspect.unwrap(run))
    assert found, "cannot locate the source of this run callable"
    return Path(found).resolve()


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(REPO_ROOT / "library").with_suffix("").parts)


def _prompt_violation(op) -> str | None:
    owner = STEPS_ROOT / op.owning_dir
    if not owner.is_dir():
        return f"owning_dir {op.owning_dir!r} is not a step directory"
    prompt = owner / op.body
    if not prompt.is_file():
        return f"{op.name} names prompt {op.body!r} but {prompt} is not a file"
    try:
        manifest = json.loads((owner / "manifest.json").read_text(encoding="utf-8"))
    except OSError:
        return (
            f"{op.name} names a prompt but {op.owning_dir} has no "
            f"manifest declaring it one"
        )
    declared = (manifest.get("implementation") or {}).get("default") or {}
    if declared.get("runtime") != "llm" or declared.get("entry_point") != op.body:
        return (
            f"{op.name} names prompt {op.body!r} but "
            f"{op.owning_dir}/manifest.json does not declare it"
        )
    if op.attr:
        return f"{op.name} is a prompt capability yet names attr {op.attr!r}"
    return None


def operation_violation(op) -> str | None:
    """None if the operation obeys Ruling 1; otherwise why it does not."""
    if getattr(op, "is_prompt", False):
        return _prompt_violation(op)
    source = _source_file(op.run)
    owner = STEPS_ROOT / op.owning_dir
    if not owner.is_dir():
        return f"owning_dir {op.owning_dir!r} is not a step directory"
    if source == TOOLS_ROOT / "operations.py":
        return "run is defined in operations.py - the registry owns logic"
    steps_root = REPO_ROOT / "library" / "steps"
    if steps_root in source.parents:
        if source.parent != owner:
            return (
                f"run lives in {source.parent.name} but the operation "
                f"is owned by {op.owning_dir}"
            )
        return None
    if TOOLS_ROOT in source.parents or source.parent == TOOLS_ROOT:
        if _dotted(source) in _tools_imported_by(
            owner
        ) or "tools" in _tools_imported_by(owner):
            return None
        return (
            f"run resolves to {_dotted(source)}, which {op.owning_dir} does not import"
        )
    return (
        f"run resolves to {source}, which is neither library/steps/ nor library/tools/"
    )


def operation_resolution(registry=None) -> list:
    """Every registered operation obeys Ruling 1."""
    from library.tools import operations

    registry = operations.all() if registry is None else registry
    out = []
    for op in registry:
        found = operation_violation(op)
        if found:
            out.append(f"{op.name}: {found}")
    return out


# ── 5. creative floors in bridge code ─────────────────────────────────
#
# Static half of the creative quantity policy: a function that injects
# defaults the plan did not name, a hardcoded plan-size recommendation,
# or a density scaler judging taste by a constant. Bridge files are
# selected from tagged capabilities; removed shared helpers are checked
# by their AST names across production code.
#
# Deliberately NOT a prompt-text scan: `find_floors` over handoff.md
# fails correct output - step_2_05_mesh_spine's "at least one content
# block (a spine of only gaps is no spine)" is a validity floor, not a
# quota, and a text scan cannot tell them apart. Prompt text stays under
# the capability registry policy and sparse-plan behaviour.

_BRIDGE_MARKERS = (
    (r"\bdef\s+inject_default_", "inject_default_* function"),
    (r"recommended is \d+", "hardcoded plan-size recommendation"),
    (r"\bdef\s+scale_\w+_density\b", "density scaling function"),
)


def creative_code_markers(registry=None, repo_root=REPO_ROOT) -> list:
    """Removed floor shapes in capabilities whose model owns quantity."""
    from library.tools import capabilities

    import re

    registry = capabilities.all() if registry is None else tuple(registry)
    step_dirs = {
        (repo_root / spec.executor.path).resolve().parent
        for spec in registry
        if spec.creative_policy == capabilities.MODEL_DECIDES_QUANTITY
    }
    compiled = [(re.compile(p), label) for p, label in _BRIDGE_MARKERS]
    out = []

    # These helpers once altered creative plans from outside the bridges.
    # AST function names keep this guard out of incident prose and source
    # comments while covering the shared utility module where they lived.
    for path in _python_files(
        repo_root / "library" / "tools", repo_root / "library" / "steps"
    ):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (
                node.name.startswith("inject_default_")
                or re.fullmatch(r"scale_\w+_density", node.name)
                or node.name == "align_sfx_to_prosody"
            ):
                out.append(
                    f"{_rel(path)}:{node.lineno}: removed creative helper "
                    f"{node.name} was reintroduced"
                )

    for step in sorted(step_dirs):
        for script in ("bridge.py", "post_bridge.py"):
            path = step / script
            if not path.is_file():
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                code = line.split("#")[0]
                for pattern, label in compiled:
                    if pattern.search(code):
                        out.append(
                            f"{_rel(path)}:{lineno}: "
                            f"{label} ({pattern.pattern}) - a bridge "
                            f"completes, scales or recommends the plan's "
                            f"count from a constant"
                        )
    return out


# ── the phase ────────────────────────────────────────────────────────


def problems() -> list:
    """Every static source-policy violation.  Empty is clean."""
    out = []
    out += [f"home_paths: {p}" for p in home_paths()]
    out += [f"resolve_access: {p}" for p in resolve_access()]
    out += [f"syspath: {p}" for p in syspath()]
    out += [f"project_root_reads: {p}" for p in project_root_reads()]
    out += [f"operation_resolution: {p}" for p in operation_resolution()]
    out += [f"creative_code_markers: {p}" for p in creative_code_markers()]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.static_check",
        description="One fast static check phase over the repo source.",
    )
    parser.add_argument(
        "--check", action="store_true", help="print every static-policy violation"
    )
    parser.parse_args(argv)
    found = problems()
    print("\n".join(found) or "clean")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

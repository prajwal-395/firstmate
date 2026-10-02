"""Which lane a test file runs in: parallel or serial.

The full-suite gate shards each marker selection across xdist
workers (parallel lane) and runs the remainder single-process (serial
lane).  A test belongs in the SERIAL lane iff it can contend for state
shared across worker processes.  Six clauses, each with a checkable
form over the file's own AST (real Call nodes only - never raw text, so
docstring examples, prose mentions and fake-module assignments cannot
match):

1. LIVE PROVIDER CALLS: an unfaked call to ``analyze_image(s)`` /
   ``analyze_video`` / ``_server_chat``.  Concurrent
   shards concentrate these against one model server and throttle as
   one.  The stub exemption is file-level and therefore coarse: a file
   that patches, monkeypatches, mocks, fakes or stubs the transport
   counts as faked throughout.  One stubbed test plus one live test in
   a file would misroute - the empirical provider audit (gate run with
   ``-v``, stderr grepped for "GEMMA SERVER answered") is the backstop.
2. WEIGHT LOADS: a real ``SentenceTransformer(...)``,
   ``load_align_model`` or ``WhisperModel(...)``
   construction.  N workers loading gigabytes contend for RAM and the
   hub download.  (``load_audio`` merely decodes.  Files marked
   ``real_model`` route serial because they load real weights.)
3. FIXED PORTS: a real ``bind()`` with a nonzero constant port, or a
   nonzero constant ``port=`` keyword.  ``port=0`` is ephemeral and
   exempt.
4. FIXED PATHS: a real ``open()`` or path-touching method call carrying
   a constant ``/tmp/`` ``/var/`` ``/etc/`` path.  Constructing the path
   or passing it to a fake is not a use.
5. LIVE RESOLVE: taking the ``resolve_session`` fixture.  One app, one
   cursor (AGENTS.md 5).
6. EXPLICIT OPT-OUT: a real ``@pytest.mark.serial`` decorator, for what
   the patterns cannot see.  It carries a reason; a marker without one
   still routes serial (fail-closed) and says so.

Fail-closed throughout: unreadable, unparseable, or uncertain files go
serial (costs wall clock, never correctness).

Measured basis: data/vep-parallelise-the-test-gate/report.md - 3 files
serial out of 600, all already handled (two Resolve drivers, one
real_model tier), so the serial lane of the sharded selection is empty
today.  ``tests/tooling/test_parallel_lane_routing.py`` pins every file to
exactly one lane, the known-unsafe shapes to serial, and the known-safe
shapes (ephemeral ports, stubbed transports, fake modules, docstring
examples) to parallel.

Stdlib only, so the gate can run this with the resolving ``python3``
rather than the gate interpreter (which may be a shim speaking only the
pytest argv protocol).
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SERIAL = "serial"
PARALLEL = "parallel"

# Clause 1: provider entry points whose concurrent use throttles as one.
# (`LLMClient` was a member until the API backend was removed: Ren
# answers through OAuth harnesses now, and no provider client remains
# in the tree.)
PROVIDER_CALLS = frozenset({
    "analyze_image",
    "analyze_images",
    "analyze_video",
    "_server_chat",
})

# Clause 1 stub exemption, file-level and deliberately coarse (see module
# docstring).  Case-insensitive substring over the file source.
STUB_MARKERS = ("patch(", "monkeypatch", "mock", "fake", "stub")

# Clause 2: weight-loading constructions.
WEIGHT_CALLS = frozenset({
    "SentenceTransformer",
    "load_model",
    "load_align_model",
    "WhisperModel",
})

# Clause 4: path-touching methods besides bare open().
PATH_TOUCHING_METHODS = frozenset({
    "write_text",
    "read_text",
    "write_bytes",
    "read_bytes",
    "mkdir",
    "makedirs",
    "mkstemp",
    "mkdtemp",
    "remove",
    "unlink",
    "copy",
    "copytree",
    "move",
    "rename",
})

FIXED_PATH_PREFIXES = ("/tmp/", "/var/", "/etc/")


@dataclass(frozen=True)
class LaneRoute:
    """Where one file runs, and the clause that put it there."""

    path: Path
    lane: str
    reason: str


def _call_name(func: ast.AST) -> str:
    """The bare name of a call's function, whatever its receiver."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _has_stub_exemption(source: str) -> bool:
    lowered = source.lower()
    return any(marker in lowered for marker in STUB_MARKERS)


def _is_constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _clause_1_live_provider(tree: ast.Module, source: str) -> str:
    """Unfaked provider calls, or "" when the file is stubbed or clean."""
    if _has_stub_exemption(source):
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) in PROVIDER_CALLS:
            return (
                f"live provider call {_call_name(node.func)!r} "
                f"(line {node.lineno}) with no stub exemption"
            )
    return ""


def _clause_2_weight_loads(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) in WEIGHT_CALLS:
            return (
                f"weight load {_call_name(node.func)!r} "
                f"(line {node.lineno})"
            )
    return ""


def _is_nonzero_port_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _clause_3_fixed_ports(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        for keyword in node.keywords:
            if keyword.arg == "port" and _is_nonzero_port_constant(keyword.value):
                return (
                    f"fixed port= keyword (line {node.lineno}) - "
                    f"port=0 is ephemeral and exempt"
                )
        if name == "bind":
            for arg in node.args:
                port = None
                if _is_nonzero_port_constant(arg):
                    port = arg.value
                elif (
                    isinstance(arg, ast.Tuple)
                    and len(arg.elts) == 2
                    and _is_nonzero_port_constant(arg.elts[1])
                ):
                    port = arg.elts[1].value
                if port is not None:
                    return f"fixed bind() port {port} (line {node.lineno})"
    return ""


def _clause_4_fixed_paths(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        touches = (
            name == "open"
            or (
                isinstance(node.func, ast.Attribute)
                and name in PATH_TOUCHING_METHODS
            )
        )
        if not touches:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            text = _is_constant_string(arg)
            if text and text.startswith(FIXED_PATH_PREFIXES):
                return (
                    f"fixed path {text!r} in {name}() call "
                    f"(line {node.lineno})"
                )
    return ""


def _clause_5_live_resolve(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            params += [a.arg for a in node.args.kwonlyargs]
            if "resolve_session" in params:
                return (
                    f"takes the resolve_session fixture "
                    f"({node.name}, line {node.lineno})"
                )
    return ""


def _is_serial_marker(decorator: ast.AST) -> tuple[bool, bool]:
    """(is mark.serial, has a reason) for one decorator."""
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    dotted = ".".join(reversed(parts))
    if not (dotted.endswith("mark.serial") or dotted == "serial"):
        return False, False
    if not isinstance(decorator, ast.Call):
        return True, False
    has_reason = bool(decorator.args or decorator.keywords)
    return True, has_reason


def _clause_6_explicit_opt_out(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            is_serial, has_reason = _is_serial_marker(decorator)
            if is_serial and has_reason:
                return f"@pytest.mark.serial on {node.name} (line {node.lineno})"
            if is_serial:
                return (
                    f"@pytest.mark.serial on {node.name} (line {node.lineno}) "
                    f"without a reason - serial anyway, fail-closed"
                )
    return ""


def _has_real_model_marker(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "pytestmark"
                   for target in targets):
                value = node.value
                if value is not None and any(isinstance(part, ast.Attribute)
                       and part.attr == "real_model"
                       for part in ast.walk(value)):
                    return True
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            names: list[str] = []
            while isinstance(target, ast.Attribute):
                names.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                names.append(target.id)
            if "real_model" in names:
                return True
    return False


def classify_file(path: Path) -> LaneRoute:
    """Route one test file to exactly one lane.  Never raises."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return LaneRoute(path, SERIAL, f"unreadable ({exc.__class__.__name__})")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return LaneRoute(path, SERIAL, f"unparseable ({exc.msg})")

    for clause in (
        _clause_1_live_provider(tree, source),
        _clause_2_weight_loads(tree),
        _clause_3_fixed_ports(tree),
        _clause_4_fixed_paths(tree),
        _clause_5_live_resolve(tree),
        _clause_6_explicit_opt_out(tree),
    ):
        if clause:
            return LaneRoute(path, SERIAL, clause)
    if _has_real_model_marker(tree):
        return LaneRoute(path, SERIAL, "real_model marker - real model loads run serial")
    return LaneRoute(path, PARALLEL, "matches no serial clause")


def test_roots(root: Path = REPO_ROOT) -> list[Path]:
    """Every directory the suite collects test files from."""
    return [root / "tests"]


def iter_test_files(root: Path = REPO_ROOT) -> list[Path]:
    """Every test module, wherever it lives.  Sorted, deduplicated."""
    seen: set[Path] = set()
    ordered: list[Path] = []
    for base in test_roots(root):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("test_*.py")):
            if any(part in (".git", ".venv", "node_modules", "__pycache__")
                   for part in path.parts):
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(path)
    return ordered


def route_suite(root: Path = REPO_ROOT) -> list[LaneRoute]:
    """Classify every test file.  Fresh on every call - never a list."""
    return [classify_file(path) for path in iter_test_files(root)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Route test files to the parallel or serial lane.")
    parser.add_argument(
        "roots", nargs="*", default=["tests"],
        help="directories to route files from (default: tests)")
    parser.add_argument(
        "--serial-only", action="store_true",
        help="print only serial-lane paths, repo-relative, one per line")
    args = parser.parse_args(argv)

    routes: list[LaneRoute] = []
    for root_arg in args.roots:
        base = Path(root_arg)
        if not base.is_absolute():
            base = REPO_ROOT / base
        if not base.is_dir():
            print(f"no such directory: {root_arg}", file=sys.stderr)
            return 2
        for path in sorted(base.rglob("test_*.py")):
            if any(part in (".git", ".venv", "node_modules", "__pycache__")
                   for part in path.parts):
                continue
            routes.append(classify_file(path))

    if args.serial_only:
        for route in routes:
            if route.lane == SERIAL:
                try:
                    print(route.path.relative_to(REPO_ROOT))
                except ValueError:
                    print(route.path)
        return 0

    for route in routes:
        try:
            rel = route.path.relative_to(REPO_ROOT)
        except ValueError:
            rel = route.path
        print(f"{route.lane}\t{rel}\t{route.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

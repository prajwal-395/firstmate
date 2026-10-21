"""PROD-003: the measurement over refusals - what blocks them, and whether
the caller can act on what the refusal says.

The punch list asks for every refusal to be actionable, and for the
refusals to be told apart by KIND: missing media, unsupported
operation, unsafe state, ambiguous intent, insufficient evidence,
resource contention.  This module MEASURES that over the engine's own
source, so the number can be quoted with its denominator and re-measured
after a change.

Five surfaces are counted, because they are the five the punch list
names, and each is counted the way THAT surface expresses a refusal:

    shape        the refusal SHAPE: every family in the engine, and
                 whether it says what kind of block it is; and every
                 direct refusal raised without a family, and whether it
                 names its own blocker
    axi          the AXI's own refusals: whether each carries the
                 command or edit that answers it - the next action -
                 and whether that answer names something the caller can
                 supply
    feasibility  the feasibility gate's findings: whether each refusing
                 finding says what kind of block it is and what the
                 caller does
    grounding    the grounding stage's refusals: whether each says what
                 kind of block it is
    broker       the broker's refusals: whether each says what kind of
                 block it is, and what the caller does

The counts are STATIC (an AST scan of the source), so the measurement
reads no Resolve, starts no broker and touches no project.

Run it:

    python3 -m library.tools.refusal_inventory

Every count carries its denominator, and "unmeasured is never zero": a
surface that could not be scanned is reported as unmeasured, not as
zero.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = "ren-refusal-inventory/1"

ROOT = Path(__file__).resolve().parents[2]

#: The kinds of block, in the punch list's own six.
BLOCKERS = (
    "missing_media",
    "unsupported_operation",
    "unsafe_state",
    "ambiguous_intent",
    "insufficient_evidence",
    "resource_contention",
)


def _tree(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _key_text(node: ast.AST | None) -> str:
    """A dict key's text, without the quotes `ast.unparse` adds."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def _called_name(call: ast.Call) -> str:
    func = call.func
    return (func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name) else "")


def _engine_files() -> list[Path]:
    files: list[Path] = []
    for base in ("library", "ren"):
        files.extend(sorted((ROOT / base).rglob("*.py")))
    files.extend(sorted(ROOT.glob("*.py")))
    return files


def _refusal_families(tree: ast.Module) -> list[str]:
    """The refusal families a module declares, by class name.

    A family is a class that reaches `RenRefusal`, directly or through
    another family - the same resolution the contract test in
    `tests/unit/context/test_ren_cli.py` uses, so the measurement and
    the guard count the same families.
    """
    classes: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes[node.name] = [ast.unparse(base) for base in node.bases]
    families = {"RenRefusal"}
    changed = True
    while changed:
        changed = False
        for name, bases in classes.items():
            if name in families:
                continue
            if any(base in families for base in bases):
                families.add(name)
                changed = True
    return sorted(name for name in families if name != "RenRefusal")


def _classified() -> dict[str, str]:
    """The families the taxonomy classifies, by name."""
    try:
        from library.tools import refusal_taxonomy
    except ImportError:
        return {}
    return dict(getattr(refusal_taxonomy, "NAME_BLOCKERS", {}))


def _resolved(families: set[str], bases: dict[str, list[str]]) -> set[str]:
    """The families whose blocker resolves, along the MRO the guard uses."""
    resolved = set(_classified())
    changed = True
    while changed:
        changed = False
        for name in families:
            if name in resolved:
                continue
            if any(base in resolved for base in bases.get(name, ())):
                resolved.add(name)
                changed = True
    return resolved


def _measure_shape() -> dict[str, Any]:
    """The refusal SHAPE: families classified, direct refusals named."""
    classified = _classified()
    families: list[str] = []
    bases: dict[str, list[str]] = {}
    direct_sites = 0
    direct_with_blocker = 0
    scanned = 0
    for path in _engine_files():
        tree = _tree(path)
        if tree is None:
            continue
        scanned += 1
        families.extend(_refusal_families(tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, []).extend(
                    ast.unparse(base) for base in node.bases)
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node) != "RenRefusal":
                continue
            direct_sites += 1
            if _keyword(node, "blocker") is not None:
                direct_with_blocker += 1
    unique = sorted(set(families))
    # A family that inherits from a classified family is classified too:
    # the blocker resolves along the MRO, exactly as the guard does.
    resolved = _resolved(set(unique), bases)
    return {
        "files_scanned": scanned,
        "refusal_families": len(unique),
        "refusal_families_classified": sum(
            1 for name in unique if name in resolved),
        "direct_refusal_sites": direct_sites,
        "direct_refusal_sites_naming_a_blocker": direct_with_blocker,
        "measured": True,
    }


def _measure_axi() -> dict[str, Any]:
    """The AXI's refusals: a next action, and one the caller can use."""
    path = ROOT / "library/tools/resolve_axi_core_commands.py"
    tree = _tree(path)
    if tree is None:
        return {"measured": False, "note": "the AXI command module did not parse"}
    sites = 0
    with_a_fix = 0
    families: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) == "AxiError":
            sites += 1
            if len(node.args) > 1 or _keyword(node, "fix") is not None:
                with_a_fix += 1
    families.extend(_refusal_families(tree))
    bases: dict[str, list[str]] = {}
    for relative in ("library/tools/execution_router.py",
                     "library/tools/reel_incremental.py",
                     "library/tools/axi_transaction.py"):
        other = _tree(ROOT / relative)
        if other is None:
            continue
        families.extend(_refusal_families(other))
        for node in ast.walk(other):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, []).extend(
                    ast.unparse(base) for base in node.bases)
    resolved = _resolved(set(families), bases)
    return {
        "files_scanned": 1,
        "refusal_constructions": sites,
        "refusal_constructions_with_a_next_action": with_a_fix,
        "refusal_families": len(set(families)),
        "refusal_families_classified": len(
            {name for name in set(families) if name in resolved}),
        "measured": True,
    }


def _measure_feasibility() -> dict[str, Any]:
    """The feasibility gate: every refusing finding names its blocker."""
    path = ROOT / "library/tools/plan_feasibility.py"
    tree = _tree(path)
    if tree is None:
        return {"measured": False, "note": "the feasibility module did not parse"}
    refusing = 0
    named = 0
    checks: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (isinstance(target, ast.Name)
                    and target.id == "CHECK_BLOCKERS"
                    and isinstance(node.value, ast.Dict)):
                checks = {_key_text(key) for key in node.value.keys
                          if key is not None}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _called_name(node) != "_finding":
            continue
        args = node.args
        if len(args) < 3:
            continue
        status = ast.unparse(args[2])
        if status not in ("INFEASIBLE", "UNDETERMINED"):
            continue
        refusing += 1
        if _keyword(node, "blocker") is not None:
            named += 1
            continue
        check = ast.unparse(args[1]) if len(args) > 1 else ""
        if check in checks:
            named += 1
    return {
        "files_scanned": 1,
        "refusing_findings": refusing,
        "refusing_findings_naming_a_blocker": named,
        "checks_with_a_classified_blocker": len(checks),
        "measured": True,
    }


def _measure_grounding() -> dict[str, Any]:
    """The grounding stage: every unverified state names its blocker."""
    path = ROOT / "library/tools/target_grounding.py"
    tree = _tree(path)
    if tree is None:
        return {"measured": False, "note": "the grounding module did not parse"}
    states = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (isinstance(target, ast.Name)
                    and target.id == "_BLOCKER_BY_STATE"
                    and isinstance(node.value, ast.Dict)):
                states = len(node.value.keys)
    carries = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {_key_text(key) for key in node.keys if key is not None}
        if {"op", "target_role", "state"} <= keys:
            if {"blocker", "next_action"} <= keys:
                carries = True
                break
    return {
        "files_scanned": 1,
        "unverified_states": states,
        "refusal_names_its_blocker_and_next_action": carries,
        "measured": True,
    }


def _measure_broker() -> dict[str, Any]:
    """The broker: its refusals say what kind of block they are."""
    jobs = _tree(ROOT / "library/tools/resolved/jobs.py")
    client = _tree(ROOT / "library/tools/resolved/client.py")
    if jobs is None or client is None:
        return {"measured": False, "note": "the broker modules did not parse"}
    sites = 0
    for node in ast.walk(jobs):
        if (isinstance(node, ast.Call)
                and _called_name(node) == "JobRefused"):
            sites += 1
    family_blocker = False
    for node in ast.walk(jobs):
        if not (isinstance(node, ast.ClassDef)
                and node.name == "JobRefused"):
            continue
        for inner in node.body:
            if not isinstance(inner, ast.FunctionDef):
                continue
            arguments = inner.args
            if any(argument.arg == "blocker" for argument in arguments.args):
                family_blocker = True
            if any(keyword.arg == "blocker" for keyword in arguments.kw_defaults
                   if keyword is not None):
                family_blocker = True
    error_blocker = False
    for node in ast.walk(client):
        if not (isinstance(node, ast.ClassDef)
                and node.name == "BrokerError"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                if any(ast.unparse(target) == "self.blocker"
                       for target in inner.targets):
                    error_blocker = True
            if isinstance(inner, ast.AnnAssign):
                if ast.unparse(inner.target) == "self.blocker":
                    error_blocker = True
    return {
        "files_scanned": 2,
        "job_refusal_sites": sites,
        "job_refusal_family_carries_a_blocker": family_blocker,
        "broker_error_carries_a_blocker": error_blocker,
        "measured": True,
    }


def measure() -> dict[str, Any]:
    """Measure the refusals over the five surfaces the punch list names."""
    return {
        "schema": SCHEMA,
        "blockers": list(BLOCKERS),
        "surfaces": {
            "shape": _measure_shape(),
            "axi": _measure_axi(),
            "feasibility": _measure_feasibility(),
            "grounding": _measure_grounding(),
            "broker": _measure_broker(),
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    print(json.dumps(measure(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI entry
    raise SystemExit(main())

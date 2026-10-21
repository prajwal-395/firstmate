"""AXI-004: the transaction representations, and the front doors that read them.

Ren had two internal models for one user-driven edit: the canonical
`EditRequest` (``edit_request``) and the legacy typed `AxiTransaction`
(``resolve_axi_core``). Two semantic front doors read the same request
through one each, and a request that reached both could be written in
two orders - or lose a preservation constraint - because the two models
did not agree on what a request means.

This module is ONE enumeration of the representations and of the front
doors that interpret a request with one of them, so the question
"how many models can interpret one edit?" has a single answer that a
test can hold still:

- ``REPRESENTATIONS`` names every transaction representation the
  production paths use, and what each one is FOR.
- ``FRONT_DOORS`` names every semantic front door - a place a user or
  an agent hands the engine an edit - and the model that door reads the
  request with.
- ``construction_sites`` measures, from the tree itself, where each
  representation is BUILT in production code.

``CANONICAL`` is the model a front door must read a request with. A
front door that names another model is a door that can interpret the
same request differently, which is what AXI-004 refuses.

The durable write record (``EditPatch``) and the compiled form
(``CompiledEdit``) are representations too, but they are not rival
MEANINGS: the patch is what a write leaves behind, and the compiled
edit is what one request becomes for a backend. They are listed so the
count of representations is honest, and they are not front-door models.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: The repository root, resolved from this file (never from the cwd).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The model every semantic front door must read a request with.
CANONICAL = "EditRequest"

#: The production trees a representation is measured in.
PRODUCTION_ROOTS = ("library", "ren")


@dataclass(frozen=True)
class Representation:
    """One internal model a transaction can be carried in."""

    name: str
    module: str
    role: str

    def as_dict(self) -> dict:
        return {"name": self.name, "module": self.module,
                "role": self.role}


#: Every transaction representation the production paths use.
REPRESENTATIONS: tuple[Representation, ...] = (
    Representation("EditRequest", "library.tools.edit_request",
                   "the canonical semantic meaning of one edit"),
    Representation("CompiledEdit", "library.tools.edit_request",
                   "one request compiled for a backend"),
    Representation("EditPatch", "library.tools.edit_patch",
                   "the durable record of one write"),
    Representation("AxiTransaction", "library.tools.resolve_axi_core",
                   "the legacy typed carrier of a request"),
)

#: The representations that are rival MEANINGS of one request: a front
#: door may read a request with one of these, and two of them mean the
#: same request can be interpreted two ways.
SEMANTIC_MODELS = ("EditRequest", "AxiTransaction")


@dataclass(frozen=True)
class FrontDoor:
    """One place a user or an agent hands the engine an edit.

    ``model`` is the internal representation the door READS the request
    with - the thing AXI-004 consolidates. ``input_shape`` is the shape
    the door's caller hands over, which is a separate question: a door
    may still take the legacy patch shape while reading it with the
    canonical model.
    """

    name: str
    entry: str
    model: str
    input_shape: str = "edit_request"

    def as_dict(self) -> dict:
        return {"name": self.name, "entry": self.entry,
                "model": self.model, "input_shape": self.input_shape}


#: Every semantic front door, and the model it reads a request with.
FRONT_DOORS: tuple[FrontDoor, ...] = (
    FrontDoor("mcp.resolve_axi_edit_request",
              "ren/mcp_server.py:call_axi_edit_request", "EditRequest"),
    FrontDoor("mcp.resolve_axi_edit_request_plan",
              "ren/mcp_server.py:call_axi_edit_request_plan", "EditRequest"),
    FrontDoor("mcp.resolve_axi_transaction",
              "ren/mcp_server.py:call_axi_transaction", "EditRequest",
              "edit_patch"),
    FrontDoor("mcp.resolve_axi_transaction_submit",
              "ren/mcp_server.py:call_axi_transaction_submit", "EditRequest",
              "edit_patch"),
    FrontDoor("mcp.resolve_axi_transaction_plan",
              "ren/mcp_server.py:call_axi_transaction_plan", "EditRequest",
              "edit_patch"),
    FrontDoor("ren.revise", "library/tools/revise.py", "EditRequest"),
    FrontDoor("assignment.edit_apply",
              "library/tools/assignment_edit_workflow.py", "EditRequest"),
)


def front_doors() -> tuple[FrontDoor, ...]:
    """Every semantic front door."""
    return FRONT_DOORS


def models_in_use() -> tuple[str, ...]:
    """The distinct semantic models the front doors read requests with."""
    return tuple(sorted({door.model for door in FRONT_DOORS}))


def input_shapes_in_use() -> tuple[str, ...]:
    """The distinct shapes the front doors' callers hand over."""
    return tuple(sorted({door.input_shape for door in FRONT_DOORS}))


def non_canonical_front_doors() -> tuple[FrontDoor, ...]:
    """The front doors that read a request with a model but the canonical."""
    return tuple(door for door in FRONT_DOORS if door.model != CANONICAL)


def _python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in PRODUCTION_ROOTS:
        base = root / name
        if not base.is_dir():
            continue
        files.extend(sorted(base.rglob("*.py")))
    return files


def construction_sites(root: Path | None = None) -> dict[str, list[str]]:
    """Where each representation is BUILT in production code.

    A construction site is a CALL of the representation's own name - the
    place a production path builds one rather than the place the type is
    declared. The measurement is taken from the tree, by parsing it, so
    it cannot drift away from the code the way a hand-written count does.

    The file is reported relative to the repository root, so the same
    measurement is readable in a diff.
    """
    base = Path(root or REPO_ROOT)
    sites: dict[str, list[str]] = {rep.name: [] for rep in REPRESENTATIONS}
    names = {rep.name for rep in REPRESENTATIONS}
    for path in _python_files(base):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        relative = str(path.relative_to(base))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                called = func.id
            elif isinstance(func, ast.Attribute):
                called = func.attr
            else:
                continue
            if called in names:
                sites[called].append(f"{relative}:{node.lineno}")
    return sites


def reference_sites(root: Path | None = None) -> dict[str, list[str]]:
    """Where each representation is READ in production code.

    A reference is any mention of the representation's name in a
    production module - an import, a call, an annotation. It is the
    other half of the measurement: a representation that is read in
    production is a model that can still interpret a request.
    """
    base = Path(root or REPO_ROOT)
    sites: dict[str, list[str]] = {rep.name: [] for rep in REPRESENTATIONS}
    names = {rep.name for rep in REPRESENTATIONS}
    for path in _python_files(base):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        relative = str(path.relative_to(base))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in names:
                sites[node.id].append(f"{relative}:{node.lineno}")
            elif (isinstance(node, ast.Attribute)
                  and node.attr in names):
                sites[node.attr].append(f"{relative}:{node.lineno}")
            elif isinstance(node, ast.alias) and node.name in names:
                sites[node.name].append(f"{relative}:{node.lineno}")
    return sites


def measurement(root: Path | None = None) -> dict:
    """The measurement, with its denominators.

    ``front_doors`` is the denominator for every front-door count, and
    ``production_files`` is the denominator for the representation
    counts: how many production files read or write a representation at
    all.
    """
    sites = construction_sites(root)
    references = reference_sites(root)
    return {
        "front_doors": len(FRONT_DOORS),
        "semantic_models_in_use": len(models_in_use()),
        "models_in_use": list(models_in_use()),
        "input_shapes_in_use": list(input_shapes_in_use()),
        "canonical_model": CANONICAL,
        "non_canonical_front_doors": len(non_canonical_front_doors()),
        "non_canonical_front_door_names": [
            door.name for door in non_canonical_front_doors()],
        "production_files": len(_python_files(Path(root or REPO_ROOT))),
        "construction_sites": {name: len(rows)
                                for name, rows in sites.items()},
        "construction_site_locations": sites,
        "reference_sites": {name: len(rows)
                             for name, rows in references.items()},
        "reference_site_locations": references,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.transaction_representations",
        description="Report the transaction representations and the "
                    "semantic front doors that read them.")
    parser.add_argument("--json", action="store_true",
                        help="print the measurement as JSON")
    parser.add_argument("--root", default=None,
                        help="the repository root to measure")
    args = parser.parse_args(argv)

    report = measurement(Path(args.root) if args.root else None)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"semantic front doors: {report['front_doors']}")
    print(f"semantic models in use: {report['semantic_models_in_use']} "
          f"({', '.join(report['models_in_use'])})")
    print(f"front doors not reading the canonical model: "
          f"{report['non_canonical_front_doors']}"
          + (f" ({', '.join(report['non_canonical_front_door_names'])})"
             if report['non_canonical_front_door_names'] else ""))
    for name, count in sorted(report["construction_sites"].items()):
        print(f"construction sites for {name}: {count}")
        for location in report["construction_site_locations"][name]:
            print(f"  {location}")
    for name, count in sorted(report["reference_sites"].items()):
        print(f"reference sites for {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

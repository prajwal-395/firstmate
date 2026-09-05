"""The named things an operator can ask the pipeline to do, and nothing else.

Ruling 1, which this module exists to obey
------------------------------------------
**An operation is a named, scoped entry point into an existing step's own
code.  It owns no logic.**

`Operation.run` POINTS AT the step's own function.  It never wraps it,
never reimplements it, and never adds a rule the step does not already
have.  `tests/test_operations_add_no_second_implementation.py` enforces
that by resolving every `run` back to the file it is defined in and
refusing anything that is not the owning step's own body, or a
`library/tools/` module that step already imports.

The alternative - a verb layer over `library/tools/` - loses for exactly
this reason: it violates Ruling 1 the moment a verb does a step's work,
and the 38 tools that already have a `__main__` block prove that
reachable is not the same as addressable.

Why `owning_node` is not decoration
-----------------------------------
The runner's per-step loop wraps a step body in eighteen services, and
sixteen of them are keyed by the DAG node id: the two ledgers, the run
status, the review gate, the marker routing, the step export, the three
run-level collectors.  An operation that did not declare its owning node
would lose its place in all of them - it would run, and nothing would
record that it had.  So an operation names the node whose decision it
is, and `test_every_owning_node_is_a_real_dag_node` checks the name is
real.

The two services that are NOT node-keyed are the handbrake, which is
keyed by project, and the provenance snapshot, which takes no key at all
because it attributes by diffing the tree.  Provenance is therefore the
one service that already generalises to an operation, which is why the
design threads it first.

What an operation RETURNS, and why it is a type
----------------------------------------------
`OperationResult` is defined here, once, because four of the six hook
conditions in increment 6 are operation-derived - `operation_completed`,
`operation_refused`, `requirement_unsatisfied`, `output_empty` - and not
one of them can be built from a bare dict.  A hook handed a dict cannot
tell a refusal from a completion, cannot name WHICH requirement refused
or who produces it, and cannot tell produced-nothing from
produced-something.  That last one is the empty-side-passes shape the
design exists to stop, so a result type that cannot express it would
reintroduce it at the hook layer.

Defining it anywhere else means increment 6 inventing its own - a second
implementation, which Ruling 1 forbids - so it is here, and it is one
type for every operation.

**A refusal names a producer.**  `unsatisfied` carries the Requirement
objects themselves, not strings, so `refusal_reason()` can say what is
missing AND which step makes it.  A refusal that only says "missing" is
the prose prerequisite in a new costume.

What this module does NOT do yet
--------------------------------
`Operation.requires` is declared and empty.  Executable requirements are
`library/tools/requirements.py` (increment 3), and this file must not
grow a second requirement vocabulary while waiting for it - a prose
prerequisite here would be the exact defect the refactor removes.  When
that module lands, populate `requires`; do not invent a checker here.

Reachability
------------
    python3 -m library.tools.operations --list
    python3 -m library.tools.operations subtitles.render --project <p> --region 45.0-72.0
    python3 -m library.tools.operations --emit-skill

Same registry, same scope vocabulary, same refusal, whether it is called
from a shell, from Python, or by the runner.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from library.tools import scope as scope_mod
from library.tools.scope import CLIP, PROJECT, REGION, Scope

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS_ROOT = REPO_ROOT / "library" / "steps"


class OperationError(Exception):
    """The registry cannot serve this request."""


class UnknownOperation(OperationError):
    """No operation by that name."""


class ScopeNotSupported(OperationError):
    """This operation does not run at that scope, and says which it does."""


COMPLETED = "completed"
REFUSED = "refused"
STATUSES = (COMPLETED, REFUSED)


@dataclass(frozen=True)
class OperationResult:
    """What one operation did.  The one shape hooks and the runner read.

    `unsatisfied` holds Requirement objects from
    `library/tools/requirements.py`, never strings: a refusal has to be
    able to name the step that PRODUCES what is missing, and only the
    requirement knows that.  Each is expected to carry `.name` and
    `.produced_by`; anything else is rendered by `repr` rather than
    guessed at.

    `hollow` is the paths a hollow-output check found.  The judgement
    belongs to `run_pipeline.check_output_is_real`, which already owns it
    for steps - this field CARRIES that verdict rather than recomputing
    it, so there is one hollow-output rule in the tree and not two.
    """

    operation: str
    owning_node: str
    scope: Scope
    status: str
    payload: Any = None
    unsatisfied: Tuple[Any, ...] = field(default_factory=tuple)
    hollow: Tuple[str, ...] = field(default_factory=tuple)
    artifacts: Tuple[str, ...] = field(default_factory=tuple)
    error: str = ""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise OperationError(
                f"unknown status {self.status!r}; known: "
                f"{', '.join(STATUSES)}")
        if self.status == REFUSED and not (self.unsatisfied or self.error):
            raise OperationError(
                f"{self.operation} is REFUSED but names neither an "
                f"unsatisfied requirement nor an error; a refusal that "
                f"does not say why is the defect this type prevents")

    @property
    def completed(self) -> bool:
        return self.status == COMPLETED

    @property
    def refused(self) -> bool:
        return self.status == REFUSED

    @property
    def produced_nothing(self) -> bool:
        """Did this produce nothing usable?

        The `output_empty` hook condition. A refusal produced nothing by
        definition; a completion did if its payload is empty or the
        hollow check found something.
        """
        return self.refused or not self.payload or bool(self.hollow)

    def refusal_reason(self) -> str:
        """Why it refused, naming a producer for each missing requirement."""
        if not self.refused:
            return ""
        parts = []
        for req in self.unsatisfied:
            name = getattr(req, "name", None) or repr(req)
            producer = getattr(req, "produced_by", None)
            parts.append(f"{name} (produced by {producer})" if producer
                         else f"{name} (nothing declares a producer)")
        if self.error:
            parts.append(self.error)
        return f"{self.operation} refused: " + "; ".join(parts)

    def as_record(self) -> dict:
        """The flat form for a log line or a hook payload."""
        return {
            "operation": self.operation,
            "owning_node": self.owning_node,
            "scope": str(self.scope),
            "status": self.status,
            "produced_nothing": self.produced_nothing,
            "unsatisfied": [getattr(r, "name", repr(r))
                            for r in self.unsatisfied],
            "hollow": list(self.hollow),
            "artifacts": list(self.artifacts),
            "error": self.error,
        }


# ── Loading a step body ─────────────────────────────────────────────
#
# Eight step files import a sibling by bare name (`from
# resolve_build_timeline import ...`, `from generate_remotion_props
# import ...`), so the step's OWN directory has to be on `sys.path` for
# it to import at all, and two directories import library/tools under
# the short `tools.` spelling, which needs `library/` there too.
#
# The module is registered in `sys.modules` before it executes because
# `@dataclass` inside a body loaded by path fails without it - it looks
# its own module up by name to resolve annotations, and gets None.

_LOADED: Dict[str, Any] = {}


def load_step_module(step_dir: str, filename: str = "step.py"):
    """Import one step body, once, without running the DAG."""
    key = f"{step_dir}/{filename}"
    if key in _LOADED:
        return _LOADED[key]

    path = STEPS_ROOT / step_dir / filename
    if not path.is_file():
        raise OperationError(f"no such step body: {path}")

    for entry in (str(REPO_ROOT), str(REPO_ROOT / "library"),
                  str(path.parent)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    name = f"_op_step_{step_dir}_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _LOADED[key] = module
    return module


@dataclass(frozen=True)
class Operation:
    """One named entry point into a step's own code."""

    name: str
    summary: str
    owning_node: str            # the DAG node whose decision this is
    owning_dir: str             # its directory under library/steps/
    body: str                   # step.py | bridge.py | post_bridge.py
    attr: str                   # the function's name in that body
    scopes: Tuple[str, ...] = (PROJECT,)
    # Executable prerequisites land here in increment 3
    # (library/tools/requirements.py).  Empty is honest; prose is not.
    requires: Tuple = field(default_factory=tuple)

    @property
    def run(self) -> Callable:
        """The step's own function.  Resolved on demand, never wrapped.

        `run` is DERIVED from `owning_dir`, not stored beside it, and
        that is the load-bearing choice in this dataclass: it makes the
        worst Ruling 1 violation - an operation owned by one step whose
        code lives in another - unrepresentable rather than merely
        tested for.  Measured while writing the gate: planting that
        violation in the registry was impossible without also changing
        this property.

        Do NOT turn `run` back into a plain field holding a callable.
        The moment it is one, a wrapper defined in this module type-checks
        and the registry starts owning logic.
        `tests/test_operations_add_no_second_implementation.py` still
        plants all three shapes, because a future edit here would make
        them reachable again.
        """
        return getattr(load_step_module(self.owning_dir, self.body),
                       self.attr)

    def supports(self, scope: Scope) -> bool:
        return scope.kind in self.scopes

    def call_identity(self) -> dict:
        """The TWO names every call site must pass: operation and step.

        Not style.  Increment 2's provenance refuses an operation that
        arrives without an owning node -

            ProvenanceError: operation subtitles.plan was given no owning
            step ... the step ledgers, the run status and the run-level
            collectors are all keyed by node id

        - because sixteen of the runner's eighteen per-step services are
        node-keyed.  An operation that passed only its own name would run
        and be recorded by nothing.  Both names, always, and the refusal
        is loud rather than silent if one is missed.
        """
        return {"operation_id": self.name, "step_id": self.owning_node}

    def check_scope(self, scope: Scope) -> None:
        if not self.supports(scope):
            raise ScopeNotSupported(
                f"{self.name} does not run at {scope.kind} scope; it runs "
                f"at: {', '.join(self.scopes)}")


# ── The registry ────────────────────────────────────────────────────
#
# Every entry points at a public function this repository already has.
# Adding an operation means naming work that exists; if the name you
# want has no function behind it, split the body first - that is what
# increment 4a did for ten of them.

_REGISTRY: Tuple[Operation, ...] = (
    Operation(
        name="sfx_library.validate",
        summary="Check the SFX library can actually serve a run",
        owning_node="validate_sfx_library",
        owning_dir="step_0_01_validate_sfx_library", body="step.py",
        attr="validate_sfx_library",
    ),
    Operation(
        name="semantics.analyse",
        summary="Run the v3 vision pass over clips without a profile",
        owning_node="semantic_analysis",
        owning_dir="step_1_03_semantic_analysis", body="step.py",
        attr="analyse_semantics",
    ),
    Operation(
        name="prosody.analyse",
        summary="Measure pitch, pace, voice quality and intensity per clip",
        owning_node="prosody_analysis",
        owning_dir="step_1_05_prosody_analysis", body="step.py",
        attr="analyse_prosody",
    ),
    Operation(
        name="ocr.extract",
        summary="Extract on-screen text from the footage",
        owning_node="ocr_extraction",
        owning_dir="step_1_07_ocr_extraction", body="step.py",
        attr="extract_ocr",
    ),
    Operation(
        name="duration_zone.build",
        summary="Resolve the project's target duration into the band the model is shown",
        owning_node="mesh_spine",
        owning_dir="step_2_05_mesh_spine", body="bridge.py",
        attr="build_duration_zone",
    ),
    Operation(
        name="music.analyse",
        summary="Analyse the selected track for beat grid, BPM, key and structure",
        owning_node="music_analysis",
        owning_dir="step_2_06_music_analysis", body="step.py",
        attr="analyse_music",
    ),
    Operation(
        name="subtitles.plan",
        summary="Generate subtitle entries from the spine's own word timestamps",
        owning_node="plan_subtitles",
        owning_dir="step_4_01_plan_subtitles", body="step.py",
        attr="generate_subtitles",
        # REGION is not offered YET: 4.01 numbers its cards from a
        # run-global counter, so a region-scoped plan would renumber
        # every card after the region.  Offering the scope before the id
        # policy exists would be a name for something that does the
        # wrong thing.
        #
        # This is TEMPORARY and increment 5 lifts it.  The captain's
        # approved worked example requires `subtitles.plan --region`, and
        # block-local caption ids (increment 1, PR #540) are what remove
        # the renumbering.  Add REGION here and delete
        # `test_plan_subtitles_does_not_yet_offer_region`.
    ),
    Operation(
        name="subtitles.render",
        summary="Render one ProRes 4444 overlay per captioned spine block",
        owning_node="render_subtitles",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="render_subtitle_overlays",
        # REGION is offered because re-rendering is idempotent per
        # segment and carries no run-global identity: a segment's name
        # comes from its own speaker, timeline and source span
        # (subtitle_segment_id), not from a counter. Contrast
        # subtitles.plan above.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="subtitles.render_segment",
        summary="Render ONE subtitle segment - the per-segment unit a region-scoped redo reaches",
        owning_node="render_subtitles",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="render_one_segment",
        # The unit increment 5's region-scoped redo reaches: one segment,
        # so REGION is the scope that means anything here, and CLIP is
        # not offered because a segment is addressed by its timeline
        # span rather than by the clip it was cut from.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="motion_graphics.render",
        summary="Render the planned motion graphics, bookends and timed text",
        owning_node="render_motion_graphics",
        owning_dir="step_4_06_render_motion_graphics", body="post_bridge.py",
        attr="render_motion_graphics",
    ),
    Operation(
        name="color_grade.resolve",
        summary="Join the colourist's answer to the measured clips as one CDL each",
        owning_node="color_grade",
        owning_dir="step_5_01_color_grade", body="post_bridge.py",
        attr="resolve_color_grade",
    ),
    Operation(
        name="validation.resolve",
        summary="Combine the deterministic checks and the model's reading into one verdict",
        owning_node="validate",
        owning_dir="step_6_02_validate_output", body="post_bridge.py",
        attr="resolve_validation",
    ),
)


def all() -> Tuple[Operation, ...]:
    """Every registered operation, in registry order."""
    return _REGISTRY


def names() -> Tuple[str, ...]:
    return tuple(op.name for op in _REGISTRY)


def get(name: str) -> Operation:
    for op in _REGISTRY:
        if op.name == name:
            return op
    raise UnknownOperation(
        f"unknown operation {name!r}. Known: {', '.join(names())}")


def by_node(node_id: str) -> Tuple[Operation, ...]:
    """Everything owned by one DAG node."""
    return tuple(op for op in _REGISTRY if op.owning_node == node_id)


# ── Presentation: the CLI, and the skill that is a projection of it ──


def describe() -> str:
    width = max(len(op.name) for op in _REGISTRY)
    lines = [f"{len(_REGISTRY)} operations",
             "",
             f"{'name'.ljust(width)}  {'owning node'.ljust(22)}  scopes"]
    lines.append("-" * (width + 46))
    for op in _REGISTRY:
        lines.append(f"{op.name.ljust(width)}  "
                     f"{op.owning_node.ljust(22)}  {','.join(op.scopes)}")
        lines.append(f"{' ' * width}  {op.summary}")
    return "\n".join(lines)


def emit_skill() -> str:
    """The registry, rendered as an agent skill.

    A projection, never a source: a hand-written skill would state its
    prerequisites in prose, which is the defect being removed.  Generated,
    the prose cannot drift, and the registry stays the only contract.
    """
    out = [
        "---",
        "name: pipeline_operations",
        "description: >-",
        "  The named things the video pipeline can be asked to do, and the",
        "  scope each runs at. GENERATED from library/tools/operations.py -",
        "  do not edit; run `python3 -m library.tools.operations --emit-skill`.",
        "---",
        "",
        "# Pipeline operations",
        "",
        "An operation is a named entry point into an existing step's own code,",
        "run at a declared scope. It owns no logic of its own.",
        "",
        "## Scopes",
        "",
    ]
    for kind in scope_mod.KINDS:
        out.append(f"- `{kind}` - {scope_mod.KIND_LEGEND[kind]}")
    out += ["", "## Operations", "",
            "| operation | owning node | scopes | what it does |",
            "|---|---|---|---|"]
    for op in _REGISTRY:
        out.append(f"| `{op.name}` | `{op.owning_node}` | "
                   f"{', '.join(op.scopes)} | {op.summary} |")
    out += ["", "## Calling one", "",
            "```",
            "python3 -m library.tools.operations --list",
            "python3 -m library.tools.operations <name> --project <path> "
            "[--region 45.0-72.0 | --clip clip_007]",
            "```", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.operations",
        description=("The named things the pipeline can be asked to do. "
                     "An operation is a step's own code, run at a scope."))
    parser.add_argument("operation", nargs="?", help="operation to run")
    parser.add_argument("--list", action="store_true",
                        help="list every operation and exit")
    parser.add_argument("--emit-skill", action="store_true",
                        help="print the registry as an agent skill")
    parser.add_argument("--project", default="",
                        help="project folder the operation runs against")
    parser.add_argument("--region", default="",
                        help="an interval of the TIMELINE, e.g. 45.0-72.0")
    parser.add_argument("--clip", default="", help="a clip id, e.g. clip_007")
    parser.add_argument("--json", action="store_true",
                        help="print the operation's result as JSON")
    args = parser.parse_args(argv)

    if args.emit_skill:
        print(emit_skill())
        return 0
    if args.list or not args.operation:
        print(describe())
        return 0

    try:
        op = get(args.operation)
        where = scope_mod.from_cli(region_text=args.region, clip_id=args.clip)
        op.check_scope(where)
    except (OperationError, scope_mod.ScopeError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    # Running an operation needs its inputs, and where those come from
    # is increment 3's `requirements.py` and increment 5's state splice.
    # Resolving them here would be this module growing the logic Ruling 1
    # forbids, so it reports what it WOULD run and stops.
    print(f"{op.name}: {op.summary}", file=sys.stderr)
    print(f"  owning node : {op.owning_node}", file=sys.stderr)
    print(f"  scope       : {where}", file=sys.stderr)
    print(f"  entry point : library/steps/{op.owning_dir}/{op.body}"
          f"::{op.attr}", file=sys.stderr)
    print("  REFUSED: an operation cannot gather its own inputs yet - "
          "that is requirements.py (increment 3). The entry point above "
          "is resolvable and callable in-process today.", file=sys.stderr)
    if args.json:
        print(json.dumps({
            "operation": op.name, "owning_node": op.owning_node,
            "scope": str(where),
            "entry_point": f"library/steps/{op.owning_dir}/{op.body}::{op.attr}",
        }, indent=2))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

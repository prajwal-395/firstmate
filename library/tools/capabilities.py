"""What Ren can do, keyed by CAPABILITY ID - the DAG node is legacy metadata.

The target shape (the captain's punch list, 2026-10-01): agent goal ->
typed scope -> capability selection -> typed artifacts -> executor ->
measured verification -> receipt.  This module is the identity half of
that: one `CapabilitySpec` per thing the engine can be asked to do, with
a stable id and everything a selector needs to choose it, and the DAG
node it used to be addressed by carried as optional `legacy` metadata.

Derived, never hand-written
---------------------------
Every field is DERIVED from a declaration that already exists, so there
is no second vocabulary to keep in sync:

    id, summary, scopes, executor,  library/tools/operations.py (the registry)
    produces
    requires                        library/tools/requirements.py, through
                                    the DAG adapter (still node-keyed)
    effects                         the requirements whose key, at the
                                    capability's node, it `produces`
    exclusion                       library/tools/concurrency_routing.py
    artifact_areas                  library/tools/project_layout.AREAS
    legacy                          library/tools/processes.py
    cost_class                      the heavy-work lock sites, below

The cost rule
-------------
`cost_class` is one of three, decided in this order, and `cost_basis`
names the evidence:

    HEAVY   it holds the machine-wide heavy-work lock
            (`library/tools/heavy_work_lock.py`) - the repository's own
            declaration that work competes for local CPU, memory or GPU.
            One at a time, machine-wide; minutes, not seconds.
    MODEL   it needs a host model's answer (`needs_model_answer`): the
            latency and the spend are a model call's.
    LIGHT   neither: local Python on files and state.

Which capability reaches which lock site cannot be read off the import
graph - measured, nearly every step reaches `reel_build` transitively
through pure helpers - so `HEAVY_LOCK_SITES` CITES the site each heavy
capability reaches, and `problems()` fails when a lock site in
`library/` is cited by neither a capability nor `UNCAPABLE_LOCK_SITES`,
so a new heavy path cannot read LIGHT by omission.  The test tiers
(`heavy`, `heavy_ml` in `pyproject.toml`) measure TESTS, not
capabilities, and are not evidence here.

The DAG is reached ONLY through `library/tools/dag_adapter.py`.  The
invariants a registry must hold are `problems()`, pinned by
`tests/test_capabilities.py`.

    python3 -m library.tools.capabilities --list
"""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from library.tools import dag_adapter

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS_ROOT = REPO_ROOT / "library" / "steps"

FUNCTION = "function"
PROMPT = "prompt"

HEAVY = "heavy"
MODEL = "model"
LIGHT = "light"
COST_CLASSES = (HEAVY, MODEL, LIGHT)

HEAVY_LOCK_SITES: dict = {
    # capability id -> the `module:function` lock sites it runs through.
    "semantics.analyse": (
        "library.tools.analysis.vision_pipeline_v3:run_pipeline",),
    "render.build": (
        "library.steps.step_6_01_render.resolve_build_timeline:build_timeline",
        "library.tools.execution.resolve_render:render_timeline"),
    "reel.build": ("library.tools.reel_build:rebuild_reels_in_project",),
}

UNCAPABLE_LOCK_SITES: dict = {
    "library.tools.footage_analysis:analyze":
        "`ren analyze` - footage intelligence, a ren verb rather than a "
        "registered capability",
    "library.tools.span_verification:verify_spans":
        "`ren search` span verification - a ren verb rather than a "
        "registered capability",
}

_EXCLUSION_STRENGTH = ("free", "declaration", "resolve_read",
                       "resolve_cursor")


@dataclass(frozen=True)
class Executor:
    """What runs when the capability is executed."""

    kind: str           # FUNCTION | PROMPT
    path: str           # repo-relative file under library/steps/
    attr: str           # the function's name; empty for a prompt


@dataclass(frozen=True)
class LegacyNode:
    """The DAG node a capability is attributed to, kept for compatibility.

    `step_outputs`, the step ledgers, the review gates, the derived
    requirements and provenance's `step_id` are still keyed by it.
    """

    node_id: str
    process: str


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    summary: str
    scopes: tuple
    executor: Executor
    requires: tuple
    """Requirement names it refuses without."""
    effects: tuple
    """Requirement names running it satisfies - derived from `produces`."""
    produces: tuple
    """The state keys its result is, DECLARED on the capability
    (`Operation.produces`); its node's effect is derived from these."""
    consumes: tuple
    """The state keys it reads, DECLARED (`Operation.consumes`); its
    `requires` is derived from these."""
    assumes_machine: tuple
    """The subset of `requires` that is a fact about the machine."""
    assumes_outside: tuple
    """The subset of `requires` no step produces: a person's approval, a
    binding the project declares, a file a tool outside the DAG writes."""
    needs_model_answer: bool
    """Executing it needs a model's answer it cannot produce itself (a
    prompt, or a post-bridge resolving the model's plan)."""
    caller_supplied: bool
    """Its arguments come from a caller, not from gathering."""
    exclusion: str | None
    """Its `concurrency_routing` class, or None where no row covers its
    step - NOT the same claim as free: the table names what constrains a
    dispatch, and its silence about a body says nothing about it."""
    artifact_areas: tuple
    """Project-relative directories the layout declares its node writes."""
    legacy: LegacyNode | None
    cost_class: str = LIGHT
    """HEAVY | MODEL | LIGHT - the module docstring's cost rule."""
    cost_basis: str = ""
    """The evidence `cost_class` was decided from."""


def _cost_of(capability_id: str, needs_model_answer: bool) -> tuple:
    sites = HEAVY_LOCK_SITES.get(capability_id)
    if sites:
        return HEAVY, "holds the heavy-work lock at " + ", ".join(sites)
    if needs_model_answer:
        return MODEL, "needs a host model's answer"
    return LIGHT, "holds no heavy-work lock and needs no model answer"


def _exclusion_for(owning_dir: str) -> str | None:
    from library.tools import concurrency_routing
    prefix = f"library.steps.{owning_dir}"
    found = [row.exclusion for row in concurrency_routing.OPERATIONS
             if row.entry_point == prefix
             or row.entry_point.startswith(prefix + ".")]
    if not found:
        return None
    return max(found, key=_EXCLUSION_STRENGTH.index)


def spec_of(op) -> CapabilitySpec:
    """The capability a registry entry IS."""
    from library.tools import processes
    from library.tools import requirements as req_mod
    from library.tools.project_layout import AREAS

    node = dag_adapter.node_of(op)
    requires = dag_adapter.requirements_consumed(op)
    # The registry's own test for what the model owes a body, asked of an
    # empty dict so it names every declared model output. A
    # caller-supplied unit is handed its arguments, never the answer.
    needs_model = op.is_prompt or (
        not op.caller_supplied and bool(op.missing_model_answer({})))
    cost_class, cost_basis = _cost_of(op.name, needs_model)
    return CapabilitySpec(
        id=op.name,
        summary=op.summary,
        scopes=tuple(op.scopes),
        executor=Executor(
            kind=PROMPT if op.is_prompt else FUNCTION,
            path=f"library/steps/{op.owning_dir}/{op.body}",
            attr=op.attr),
        requires=tuple(r.name for r in requires),
        effects=tuple(r.name for r in dag_adapter.requirements_produced(op)),
        produces=tuple(op.produces),
        consumes=tuple(op.consumes),
        assumes_machine=tuple(r.name for r in requires
                              if r.kind == req_mod.KIND_ENVIRONMENT),
        assumes_outside=tuple(r.name for r in requires
                              if r.kind != req_mod.KIND_ENVIRONMENT
                              and not r.produced_by),
        needs_model_answer=needs_model,
        caller_supplied=op.caller_supplied,
        exclusion=_exclusion_for(op.owning_dir),
        artifact_areas=tuple(spec.relpath for spec in AREAS.values()
                             if spec.step == node),
        legacy=LegacyNode(node_id=node,
                          process=processes.process_of(node) or ""),
        cost_class=cost_class,
        cost_basis=cost_basis,
    )


@lru_cache(maxsize=1)
def all() -> tuple:
    """Every capability, in registry order."""
    from library.tools import operations
    return tuple(spec_of(op) for op in operations.all())


def ids() -> tuple:
    return tuple(c.id for c in all())


def producers_of(requirement_name: str) -> tuple:
    """The capability ids that DECLARE producing a requirement.

    The capability-keyed answer to "what makes this?" - the requirement's
    own `produced_by` is its legacy node tuple, kept for the runner,
    which executes nodes.  Empty when only a node with no capability
    (`dag_adapter.legacy_only_nodes`) or nothing at all produces it.
    """
    return dag_adapter.requirement_index()[1].get(requirement_name, ())


def consumers_of(requirement_name: str) -> tuple:
    """The capability ids that refuse without a requirement."""
    return dag_adapter.requirement_index()[0].get(requirement_name, ())


def get(capability_id: str) -> CapabilitySpec:
    for c in all():
        if c.id == capability_id:
            return c
    from library.tools import operations
    operations.get(capability_id)      # raises UnknownOperation, by name
    raise AssertionError("unreachable")


# ── The invariants a registry must hold ─────────────────────────────


def _top_level_defs(path: Path) -> frozenset:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return frozenset(n.name for n in tree.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


_LOCK_NAMES = ("heavy_work_lock", "heavy_work_locked")


def _names_the_lock(node) -> bool:
    func = node.func if isinstance(node, ast.Call) else node
    return (isinstance(func, ast.Name) and func.id in _LOCK_NAMES) or (
        isinstance(func, ast.Attribute) and func.attr in _LOCK_NAMES)


@lru_cache(maxsize=1)
def heavy_lock_sites() -> frozenset:
    """Every `module:function` in library/ that takes the heavy-work lock.

    A site is a top-level function decorated with `heavy_work_locked` or
    containing a `with heavy_work_lock(...)`.  The lock's own module is
    not a site.
    """
    out = set()
    for path in (REPO_ROOT / "library").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "heavy_work_lock" not in text or path.name == "heavy_work_lock.py":
            continue
        module = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        for fn in ast.parse(text).body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            locked = any(_names_the_lock(d) for d in fn.decorator_list) or any(
                isinstance(w, (ast.With, ast.AsyncWith)) and any(
                    isinstance(i.context_expr, ast.Call)
                    and _names_the_lock(i.context_expr) for i in w.items)
                for w in ast.walk(fn))
            if locked:
                out.add(f"{module}:{fn.name}")
    return frozenset(out)


def unregistered_step_dirs() -> tuple:
    """Step directories nothing can execute and nothing explains.

    A directory is REGISTERED when a capability's executor lives in it,
    when a process node runs it (through the DAG adapter), or when
    `project_layout.STEPS` declares why no node does.
    """
    from library.tools import operations, processes
    from library.tools.project_layout import STEPS

    on_disk = {p.name for p in STEPS_ROOT.iterdir()
               if p.is_dir() and p.name.startswith("step_")}
    reached = ({op.owning_dir for op in operations.all()}
               | set(processes.step_dirnames().values()))
    # A reason this short is a placeholder written to satisfy
    # `StepDir.__post_init__`, not an explanation.
    explained = {f"step_{s.dirname}" for s in STEPS
                 if not s.wired and len(s.unwired_reason.strip()) > 20}
    return tuple(sorted(on_disk - reached - explained))


@lru_cache(maxsize=None)
def _manifest_outputs(owning_dir: str) -> frozenset:
    path = STEPS_ROOT / owning_dir / "manifest.json"
    try:
        interface = json.loads(path.read_text(encoding="utf-8"))["interface"]
    except (OSError, ValueError, KeyError):
        return frozenset()
    return frozenset(o["name"] for o in interface.get("outputs", ()))


def problems(registry=None) -> list:
    """Every way the registry breaks an invariant.  Empty when sound.

    1. every capability has a unique, stable id;
    2. every executable thing is registered - each executor resolves to
       a real body, and each step directory is reached or explained;
    3. every producer has stable identity - a legacy node is a real node
       and every requirement producer is one;
    4. every artifact has an owner - each step-owned layout area names a
       real node;
    5. a capability that produces nothing says why (`EMPTY_EFFECT_REASONS`);
    6. every heavy-work lock site is cited (`HEAVY_LOCK_SITES`);
    7. effects and reads are declared per capability and true to the
       step: every key a capability `consumes` is one a requirement of its
       node reads, every key it `produces` is an output its step's
       manifest declares, and every requirement a node with capabilities is a
       producer of is produced by one of them - so deriving a node's
       effect from its capabilities (`dag_adapter.node_effects`) loses
       no production.

    "Requirements are declared" - a contract derived from
    `requirements.py`, never hand-written - is
    `tests/test_operations_execute.py::test_no_operation_hand_writes_a_requirement`.
    """
    from library.tools import operations, requirements
    from library.tools.project_layout import AREAS

    registry = operations.all() if registry is None else registry
    all_reqs = requirements.all_requirements()
    out = []
    nodes = dag_adapter.node_ids()

    seen = set()
    for op in registry:
        if op.name in seen:
            out.append(f"capability id {op.name!r} is registered twice")
        seen.add(op.name)
        if not op.name or op.name != op.name.strip() or " " in op.name:
            out.append(f"capability id {op.name!r} is not a stable token")

        body = STEPS_ROOT / op.owning_dir / op.body
        if not body.is_file():
            out.append(f"{op.name}: executor {body.relative_to(REPO_ROOT)} "
                       f"does not exist")
        elif op.is_prompt:
            if op.attr:
                out.append(f"{op.name}: a prompt names no function, "
                           f"yet attr is {op.attr!r}")
        elif op.attr not in _top_level_defs(body):
            out.append(f"{op.name}: {op.body} defines no top-level "
                       f"{op.attr!r}")

        node = getattr(op, "owning_node", "")
        if node and node not in nodes:
            out.append(f"{op.name}: legacy node {node!r} is not a node of "
                       f"any process")

        declared = _manifest_outputs(op.owning_dir)
        for key in getattr(op, "produces", ()):
            if key not in declared:
                out.append(f"{op.name}: produces {key!r}, which "
                           f"{op.owning_dir}/manifest.json declares no "
                           f"output for")
        read_at_node = {r.consumed_key for r in all_reqs
                        if node in r.consumers and r.consumed_key}
        for key in getattr(op, "consumes", ()):
            if key not in read_at_node:
                out.append(f"{op.name}: consumes {key!r}, which no "
                           f"requirement of {node!r} reads")

        # A capability that satisfies nothing must say why: the composer
        # works backwards from effects, so an unexplained empty effect is
        # a capability nothing can ever select.
        excused = op.name in operations.EMPTY_EFFECT_REASONS
        if not op.effect and not excused:
            out.append(f"{op.name}: produces no requirement and "
                       f"EMPTY_EFFECT_REASONS does not say why")
        elif op.effect and excused:
            out.append(f"{op.name}: EMPTY_EFFECT_REASONS excuses an empty "
                       f"effect, but it produces {len(op.effect)}")

    for name in operations.EMPTY_EFFECT_REASONS:
        if name not in seen:
            out.append(f"EMPTY_EFFECT_REASONS names {name!r}, which is not "
                       f"a capability")

    cited = {site for sites in HEAVY_LOCK_SITES.values() for site in sites}
    actual = heavy_lock_sites()
    for site in sorted(actual - cited - set(UNCAPABLE_LOCK_SITES)):
        out.append(f"heavy-work lock site {site} is cited by no capability "
                   f"in HEAVY_LOCK_SITES and not excused in "
                   f"UNCAPABLE_LOCK_SITES, so what reaches it reads LIGHT")
    for site in sorted((cited | set(UNCAPABLE_LOCK_SITES)) - actual):
        out.append(f"cited lock site {site} does not take the heavy-work "
                   f"lock")
    for name in HEAVY_LOCK_SITES:
        if name not in seen:
            out.append(f"HEAVY_LOCK_SITES names {name!r}, which is not a "
                       f"capability")

    for d in unregistered_step_dirs():
        out.append(f"step directory {d} is reached by no capability and "
                   f"no process node, and STEPS declares no reason")

    with_capabilities = {getattr(op, "owning_node", "") for op in registry}
    produced_at: dict = {}
    for op in registry:
        for r in op.effect:
            produced_at.setdefault(getattr(op, "owning_node", ""),
                                   set()).add(r.name)
    for r in all_reqs:
        for producer in r.produced_by:
            if producer not in nodes:
                out.append(f"requirement {r.name} names producer "
                           f"{producer!r}, which is not a node")
            elif (producer in with_capabilities
                  and r.name not in produced_at.get(producer, ())):
                out.append(f"requirement {r.name} is produced by "
                           f"{producer!r} at key {r.key_at(producer)!r}, "
                           f"and none of that node's capabilities "
                           f"declares producing it")

    for spec in AREAS.values():
        if spec.step and spec.step not in nodes:
            out.append(f"area {spec.relpath} is owned by {spec.step!r}, "
                       f"which is not a node")
    return out


def describe() -> str:
    lines = [f"{len(all())} capabilities", ""]
    for c in all():
        legacy = c.legacy.node_id if c.legacy else "-"
        model = " model" if c.needs_model_answer else ""
        lines.append(f"{c.id:<32} {','.join(c.scopes):<15} "
                     f"{c.exclusion or '?':<15} {c.cost_class:<6} "
                     f"legacy={legacy}{model}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.capabilities",
        description="Every capability, by id; the DAG node is legacy.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="print every invariant the registry breaks")
    args = parser.parse_args(argv)
    if args.check:
        found = problems()
        print("\n".join(found) or "sound")
        return 1 if found else 0
    print(describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""What Ren can do, keyed by CAPABILITY ID - the DAG node is legacy metadata.

The target shape (the captain's punch list, 2026-10-01): agent goal ->
typed scope -> capability selection -> typed artifacts -> executor ->
measured verification -> receipt. This is the derived view of the
capability registry: one `CapabilitySpec` per thing the engine can be
asked to do, with the DAG node it used to be addressed by carried as
optional `legacy` metadata.

Derived, except the capability-owned execution contract
------------------------------------------------------
Identity and pipeline effects are derived from the operation registry,
requirements, layout and DAG adapter. `Operation.execution` is the one
authored declaration for resource demand, Resolve mode, locality,
freshness and EditPatch composition; routing and scheduler profiles are
computed from it:

    id, summary, scopes, executor,  library/tools/operations.py (the registry)
    produces
    requires                        library/tools/requirements.py, through
                                    the DAG adapter (still node-keyed)
    effects                         the requirements whose key, at the
                                    capability's node, it `produces`
    artifact_areas                  library/tools/project_layout.AREAS
    legacy                          library/tools/processes.py
    execution                       the capability's execution contract

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

Each resource phase cites the heavy-work lock site it owns. The audit
compares those declarations with the source and refuses an uncited site
unless it has a reason in `UNCAPABLE_LOCK_SITES`.

The DAG is reached ONLY through `library/tools/dag_adapter.py`.  The
invariants a registry must hold are `problems()`, pinned by
`tests/contracts/test_capabilities.py`.

    python3 -m library.tools.capabilities --list
"""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from library.tools import dag_adapter
from library.tools.operations import PatchSemantics

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS_ROOT = REPO_ROOT / "library" / "steps"

FUNCTION = "function"
PROMPT = "prompt"

HEAVY = "heavy"
MODEL = "model"
LIGHT = "light"
COST_CLASSES = (HEAVY, MODEL, LIGHT)

MODEL_DECIDES_QUANTITY = "model_decides_quantity"
CREATIVE_POLICIES = {
    # These capabilities carry a creative plan or decision whose count is
    # the model/editor's. Deterministic bridges may validate and resolve
    # a sparse answer, but may not complete it to an engine-chosen quota.
    "speech.enrich": MODEL_DECIDES_QUANTITY,
    "spine.mesh": MODEL_DECIDES_QUANTITY,
    "broll.resolve": MODEL_DECIDES_QUANTITY,
    "broll.splice": MODEL_DECIDES_QUANTITY,
    "reel.select": MODEL_DECIDES_QUANTITY,
    "transitions.resolve": MODEL_DECIDES_QUANTITY,
    "transitions.splice": MODEL_DECIDES_QUANTITY,
    "vfx.resolve": MODEL_DECIDES_QUANTITY,
    "vfx.splice": MODEL_DECIDES_QUANTITY,
    "sfx.resolve": MODEL_DECIDES_QUANTITY,
    "sfx.splice": MODEL_DECIDES_QUANTITY,
    "motion_graphics.render": MODEL_DECIDES_QUANTITY,
    "color_grade.resolve": MODEL_DECIDES_QUANTITY,
    "audio_mix.resolve": MODEL_DECIDES_QUANTITY,
}

UNCAPABLE_LOCK_SITES: dict = {
    "library.tools.footage_analysis:analyze":
        "`ren analyze` - footage intelligence, a ren verb rather than a "
        "registered capability",
    "library.tools.span_verification:verify_spans":
        "`ren search` span verification - a ren verb rather than a "
        "registered capability",
}

@dataclass(frozen=True)
class Executor:
    """What runs when the capability is executed."""

    kind: str           # FUNCTION | PROMPT
    path: str           # repo-relative file under library/steps/
    attr: str           # the function's name; empty for a prompt


@dataclass(frozen=True)
class LegacyNode:
    """The DAG node a capability is attributed to, kept for compatibility.

    The step ledgers, the review gates, the derived requirements and
    provenance's `step_id` are still keyed by it.
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
    execution: object
    """The authored execution contract from `Operation.execution`."""
    artifact_areas: tuple
    """Project-relative directories the layout declares its node writes."""
    legacy: LegacyNode | None
    cost_class: str = LIGHT
    """HEAVY | MODEL | LIGHT - the module docstring's cost rule."""
    cost_basis: str = ""
    """The evidence `cost_class` was decided from."""
    creative_policy: str | None = None
    """Machine-readable owner of creative choice cardinality, when any."""

    @property
    def exclusion(self) -> str | None:
        """Compatibility view of the first dispatch route's exclusion."""
        phase = next((p for p in self.execution.phases if p.entry_points), None)
        if phase is None:
            return None
        return {
            "none": "free", "shared": "resolve_read",
            "exclusive": "resolve_cursor",
        }[phase.resolve_mode]

    @property
    def patch_semantics(self):
        """The capability's EditPatch declaration, when it authors one."""
        return self.execution.patch


def _cost_of(execution, needs_model_answer: bool) -> tuple:
    sites = tuple(site for phase in execution.phases
                  for site in phase.lock_sites)
    if sites:
        return HEAVY, "holds the heavy-work lock at " + ", ".join(sites)
    if needs_model_answer:
        return MODEL, "needs a host model's answer"
    return LIGHT, "holds no heavy-work lock and needs no model answer"


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
    cost_class, cost_basis = _cost_of(op.execution, needs_model)
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
        execution=op.execution,
        artifact_areas=tuple(spec.relpath for spec in AREAS.values()
                             if spec.step == node),
        legacy=LegacyNode(node_id=node,
                          process=processes.process_of(node) or ""),
        cost_class=cost_class,
        cost_basis=cost_basis,
        creative_policy=CREATIVE_POLICIES.get(op.name),
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


def run_order(process_id: str) -> tuple:
    """Every capability of one process, in the order a run drives them.

    Each comes after every capability of the process that produces a
    requirement it refuses without (`producers_of`); registry order
    breaks ties.  The process is the one its legacy node belongs to.
    Caller-supplied capabilities are in the order too - a runner is not
    their caller and skips them by name, so the skip stays visible.
    """
    specs = [c for c in all() if c.legacy and c.legacy.process == process_id]
    ids_in = {c.id for c in specs}
    after = {c.id: {p for r in c.requires for p in producers_of(r)
                    if p in ids_in and p != c.id}
             for c in specs}
    order: list = []
    done: set = set()
    while len(order) < len(specs):
        ready = [c for c in specs if c.id not in done and after[c.id] <= done]
        if not ready:
            raise ValueError(
                f"the capabilities of {process_id!r} require each other in "
                f"a cycle: {sorted(ids_in - done)}")
        order.append(ready[0])
        done.add(ready[0].id)
    return tuple(order)


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


def _lock_profile(call) -> str:
    """The static capability-phase label passed to a heavy-work lock."""
    if len(call.args) > 1:
        value = call.args[1]
    else:
        value = next((kw.value for kw in call.keywords
                      if kw.arg == "profile"), None)
    if value is None:
        return "machine"
    return value.value if isinstance(value, ast.Constant) and isinstance(
        value.value, str) else "<dynamic>"


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


@lru_cache(maxsize=1)
def heavy_lock_profiles() -> dict:
    """Every top-level lock site and the profile labels it requests."""
    out = {}
    for path in (REPO_ROOT / "library").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "heavy_work_lock" not in text or path.name == "heavy_work_lock.py":
            continue
        module = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        for fn in ast.parse(text).body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [node for node in ast.walk(fn)
                     if isinstance(node, ast.Call) and _names_the_lock(node)]
            if calls:
                out[f"{module}:{fn.name}"] = frozenset(
                    _lock_profile(call) for call in calls)
    return out


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


@cache
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
    3. every producer has stable identity - a capability's executor
       directory is the step of exactly one node (its legacy node is
       derived from it) and every requirement producer is a node;
    4. every artifact has an owner - each step-owned layout area names a
       real node;
    5. a capability that produces nothing says why (`EMPTY_EFFECT_REASONS`);
    6. every execution phase is internally sound and agrees with its
       heavy-work lock site and routing surface;
    7. effects and reads are declared per capability and true to the
       step: every key a capability `consumes` is one a requirement of its
       node reads, every key it `produces` is an output its step's
       manifest declares, and every requirement a node with capabilities is a
       producer of is produced by one of them - so deriving a node's
       effect from its capabilities (`dag_adapter.node_effects`) loses
       no production;
    8. every node's run is recorded - each node of each process has a
       capability its output is filed under (`dag_adapter.run_capability`),
       so `capability_outputs` holds every key a run returns.

    "Requirements are declared" - a contract derived from
    `requirements.py`, never hand-written - holds by construction:
    `Operation.requires` is a property of a frozen dataclass, so a
    registry entry passing `requires=` cannot be constructed.
    """
    from library.tools import operations, requirements, resource_scheduler
    from library.tools.project_layout import AREAS

    registry = operations.all() if registry is None else registry
    all_reqs = requirements.all_requirements()
    out = []
    nodes = dag_adapter.node_ids()
    actual_lock_profiles = heavy_lock_profiles()
    declared_lock_profiles = {}
    declared_routes = {}

    seen = set()
    for op in registry:
        if op.name in seen:
            out.append(f"capability id {op.name!r} is registered twice")
        seen.add(op.name)
        if not op.name or op.name != op.name.strip() or " " in op.name:
            out.append(f"capability id {op.name!r} is not a stable token")

        execution = getattr(op, "execution", None)
        if not isinstance(execution, operations.ExecutionPolicy):
            out.append(f"{op.name}: has no valid execution policy")
            execution = operations.ExecutionPolicy()
        phase_names = set()
        for phase in execution.phases:
            if not isinstance(phase, operations.ExecutionPhase):
                out.append(f"{op.name}: has an invalid execution phase "
                           f"{phase!r}")
                continue
            if phase.name in phase_names:
                out.append(f"{op.name}: execution phase {phase.name!r} is "
                           "declared twice")
            phase_names.add(phase.name)
            if phase.resolve_mode not in operations.RESOLVE_MODES:
                out.append(f"{op.name}:{phase.name}: unknown Resolve mode "
                           f"{phase.resolve_mode!r}")
            if phase.locality not in operations.LOCALITIES:
                out.append(f"{op.name}:{phase.name}: unknown locality "
                           f"{phase.locality!r}")
            if phase.freshness not in operations.FRESHNESS_REQUIREMENTS:
                out.append(f"{op.name}:{phase.name}: unknown freshness "
                           f"requirement {phase.freshness!r}")
            if phase.freshness == operations.FRESHNESS_TIMELINE_GENERATION:
                if phase.locality != operations.LOCALITY_TIMELINE:
                    out.append(f"{op.name}:{phase.name}: timeline-generation "
                               "freshness requires timeline locality")
                if phase.resolve_mode != operations.RESOLVE_EXCLUSIVE:
                    out.append(f"{op.name}:{phase.name}: timeline-generation "
                               "freshness requires exclusive Resolve access")
            if (phase.resolve_mode == operations.RESOLVE_NONE
                    and phase.locality != operations.LOCALITY_NONE):
                out.append(f"{op.name}:{phase.name}: locality requires "
                           "Resolve access")
            resource_names = [name for name, _amount in phase.resources]
            if len(resource_names) != len(set(resource_names)):
                out.append(f"{op.name}:{phase.name}: resource demand "
                           "declares a resource more than once")
            if "resolve_cursor" in resource_names:
                out.append(f"{op.name}:{phase.name}: resolve_cursor demand "
                           "is derived from Resolve mode and must not be "
                           "declared separately")
            if phase.resources and not phase.lock_sites:
                out.append(f"{op.name}:{phase.name}: resource demand has no "
                           "heavy-work lock site")
            for resource, amount in phase.resources:
                if resource not in resource_scheduler.RESOURCES:
                    out.append(f"{op.name}:{phase.name}: unknown resource "
                               f"{resource!r}")
                if not isinstance(amount, int) or isinstance(amount, bool) \
                        or amount <= 0:
                    out.append(f"{op.name}:{phase.name}: resource {resource!r} "
                               f"has invalid demand {amount!r}")
            if phase.resources and not phase.resource_basis.strip():
                out.append(f"{op.name}:{phase.name}: resource demand has no "
                           "measurement or declared basis")
            for site in phase.lock_sites:
                if not phase.resources:
                    out.append(f"{op.name}:{phase.name}: lock site {site} "
                               "has no resource demand")
                if site in declared_lock_profiles:
                    out.append(f"heavy-work lock site {site} is declared by "
                               "more than one execution phase")
                declared_lock_profiles[site] = f"{op.name}:{phase.name}"
            for entry_point in phase.entry_points:
                prior = declared_routes.get(entry_point)
                if prior is not None:
                    out.append(f"route {entry_point} is declared by both "
                               f"{prior} and {op.name}:{phase.name}")
                declared_routes[entry_point] = f"{op.name}:{phase.name}"

        if execution.patch is not None:
            apply_phase = execution.phase("apply")
            if apply_phase is None:
                out.append(f"{op.name}: EditPatch semantics have no apply phase")
            elif (apply_phase.resolve_mode != operations.RESOLVE_EXCLUSIVE
                  or apply_phase.locality != operations.LOCALITY_TIMELINE
                  or apply_phase.freshness !=
                  operations.FRESHNESS_TIMELINE_GENERATION):
                out.append(f"{op.name}: EditPatch semantics disagree with its "
                           "execution phase; patches require exclusive Resolve, "
                           "timeline locality and timeline-generation freshness")

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

        try:
            node = dag_adapter.node_of(op)
        except dag_adapter.NoLegacyNode as exc:
            # Nothing below can be asked of it: its contract is derived
            # from the node it does not have.
            out.append(f"{op.name}: {exc}")
            continue

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

    valid_creative_policies = {MODEL_DECIDES_QUANTITY}
    for capability_id, policy in CREATIVE_POLICIES.items():
        if policy not in valid_creative_policies:
            out.append(f"{capability_id}: unknown creative policy {policy!r}")
            continue
        op = next((item for item in registry if item.name == capability_id), None)
        if op is None:
            out.append(f"creative policy names unknown capability {capability_id!r}")
            continue
        needs_model_answer = op.is_prompt or (
            not op.caller_supplied and bool(op.missing_model_answer({}))
        )
        if not needs_model_answer:
            out.append(
                f"{capability_id}: {policy} requires a model-reaching capability"
            )

    for name in operations.EMPTY_EFFECT_REASONS:
        if name not in seen:
            out.append(f"EMPTY_EFFECT_REASONS names {name!r}, which is not "
                       f"a capability")

    actual = heavy_lock_sites()
    for site in sorted(actual - set(declared_lock_profiles)
                       - set(UNCAPABLE_LOCK_SITES)):
        out.append(f"heavy-work lock site {site} is cited by no capability "
                   "execution phase and not excused in "
                   "UNCAPABLE_LOCK_SITES, so its resource demand is unknown")
    for site in sorted((set(declared_lock_profiles)
                        | set(UNCAPABLE_LOCK_SITES)) - actual):
        out.append(f"declared heavy-work lock site {site} does not take the "
                   "heavy-work lock")
    for site, expected_profile in sorted(declared_lock_profiles.items()):
        actual_profiles = actual_lock_profiles.get(site, frozenset())
        if actual_profiles != {expected_profile}:
            out.append(f"heavy-work lock site {site} requests "
                       f"{sorted(actual_profiles)}, but its execution phase "
                       f"declares {expected_profile!r}")

    for d in unregistered_step_dirs():
        out.append(f"step directory {d} is reached by no capability and "
                   f"no process node, and STEPS declares no reason")

    with_capabilities: set = set()
    produced_at: dict = {}
    for op in registry:
        try:
            node = dag_adapter.node_of(op)
        except dag_adapter.NoLegacyNode:
            continue        # reported above, by name
        with_capabilities.add(node)
        for r in op.effect:
            produced_at.setdefault(node, set()).add(r.name)
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

    if registry is operations.all():
        for node in sorted(nodes):
            if dag_adapter.run_capability(node) is None:
                out.append(f"node {node!r} has no capability its run is "
                           f"recorded under, so its output would reach "
                           f"no record")

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

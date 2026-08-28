"""One enumeration for scoping a run: what to run, what to leave out, and
the refusal when the two do not fit together.

The problem this exists to remove
---------------------------------
`--step` runs exactly one step and `--from` runs a suffix.  Neither can
express "run these and nothing else", neither can express "run everything
except this", and neither consults the DAG about the selection itself.
So a selection that dropped a producer failed forty minutes in, inside
`gather_step_inputs`, rather than in the second before the run began.

The captain's ask (2026-08-28, #250): "we should have that ability to be
able to quickly deselect and select what specific steps we want to fire
off for a run - like for example if i just need the roughcut with
subtitles and nothing else then many intermediate steps can just be
skipped instead of having unecessary processing wasted."

What a selection is
-------------------
Three inputs, and they compose:

* **target** - a NAME for a destination, resolved here.  A target names
  its GOAL steps and nothing else; the step list is the goals' dependency
  closure, walked off the DAG on every run.  A target that carried a
  hand-written step list would be wrong the first time a step was
  inserted, and a target nobody trusts is a target nobody uses.
* **only** - goal steps given directly, for the case no target covers.
* **skip** - steps this run declines, whatever else selected them.

`with_steps` re-selects a step that is off by default (below).

How a selection resolves
------------------------
1.  Goals are the target's goals, or `only`, or - when neither is given -
    the whole selectable universe.
2.  The run set is the goals' closure over HARD edges.  Soft edges are
    not followed, so a step whose consumers can all live without it is
    not dragged in by a target that does not want it.
3.  Anything in `skip` comes out of the run set.
4.  Every remaining step's hard parents must be IN the run set or already
    SATISFIED - recorded in the project ledger with an output on file.
    Anything else is a refusal, named before the run starts.

A hard edge and a soft edge
---------------------------
The DAG's `data_mapping` says which of a producer's output keys reach a
consumer.  The consumer's manifest says which of its inputs are optional.
An edge is HARD when it carries at least one key the consumer does NOT
declare optional - that is exactly the condition under which
`run_pipeline.gather_step_inputs` raises.  Nothing here guesses: the
refusal and the crash it prevents read the same two declarations.

Excluding a producer refuses its consumers; it does not silently drop them
--------------------------------------------------------------------------
#250 left this open.  Deciding it from what actually happens: excluding a
producer whose output nothing has on file makes `gather_step_inputs`
raise mid-run.  There is no "let downstream cope" - the consumer has no
code path for an absent required input, by design (AGENTS.md section
10.1: never `.get()` a default for a key a contract promises).  So a
selection that strands a consumer is REFUSED, and the refusal says the
two ways out: skip the consumer too, or run the producer once so its
output is on file.

"I just want the rough cut" is expressed by naming a GOAL, not by
excluding twelve steps - which is what targets are for.

A prerequisite is a condition on STATE, not on lineage
------------------------------------------------------
The captain, #260, 2026-08-28: "we need to refactor the system to have
the pipeline be customizable with prereqs ... so we can continue to have
strong contract enforcment but still have the pipeline configuration
ability".

So a step declares what must EXIST for it to run - a `Prerequisite`,
one per required key - and this asks whether that state exists, by any
of three means: a step in this run makes it, a previous run recorded it,
or the captain supplied it from outside and it CHECKED OUT
(`library/tools/external_inputs.py`).  Which step would normally have
made it is one of the three answers, not the question.

That is also why a producer that finished and recorded a DIFFERENT key
does not satisfy: `gather_step_inputs` raises on the key.

A cached artifact satisfies an excluded dependency
--------------------------------------------------
That is what makes a scoped re-run fast, and it is the ledger's job
already: a step recorded complete WITH an output under `step_outputs` is
satisfied, and `gather_step_inputs` will find that output whether or not
this run re-computes it.  Both halves are required - a ledger entry with
no output is not an artifact.  Staleness is the ledger's business
(source fingerprints, `--rerun`), not this module's.

Steps that are off by default
-----------------------------
`DESELECTED_BY_DEFAULT` is the one place a step is declared wired but not
run.  It is not a second unwired list: the step IS in the DAG, it runs
whenever it is named, and turning it on is `--with <id>`.  A step may
only be here if nothing hard-depends on it, or every default run would
refuse; `tests/test_run_scope.py` checks that.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


class ScopeError(ValueError):
    """A selection that cannot be run, refused before the run starts."""


# ── Named targets ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Target:
    """A destination, named once, resolved from the DAG every run.

    `goals` are the steps that must have run for the target to have been
    reached.  Everything else about the target - which steps it needs and
    which it leaves out - is derived, so a step inserted upstream is
    picked up without anybody editing this table.
    """

    name: str
    goals: Tuple[str, ...]
    description: str


TARGETS: Dict[str, Target] = {
    # The captain named exactly this one: "if i just need the roughcut
    # with subtitles and nothing else".  A rendered cut with subtitles on
    # it is `render` - step 6.01 - so `render` is the goal and the rest
    # is whatever the DAG says `render` cannot do without.
    #
    # What that leaves out is derived, not declared, and on the DAG as it
    # stands it is: the SFX-library validation (nothing routes it),
    # motion graphics and creative cohesion (both reach
    # `compile_manifest` on OPTIONAL inputs), and the render QA that
    # judges a master nobody is shipping yet.
    "rough_cut_subtitles": Target(
        name="rough_cut_subtitles",
        goals=("render",),
        description=(
            "A rendered cut with subtitles burned in - the thing you "
            "scrub to see whether the edit works. Everything "
            "`compile_manifest` can be handed as an optional input, and "
            "the render QA that judges a finished master, is left out."
        ),
    ),
}


# ── Steps that are wired but off by default ──────────────────────────

DESELECTED_BY_DEFAULT: Dict[str, str] = {
    # #245, the captain's ruling on 2026-08-28: "i want you to finish
    # flushing it out and then simply deselect it from the pipeline for
    # now."
    #
    # The step works.  Measured on 001 (17 clips, 807s of footage) it
    # costs 445 seconds and returns 367 tracked texts across 15 of 17
    # clips, 83 of them above 0.5 confidence, including street names and
    # storefront signs.  It is off because NOTHING READS IT: the step
    # writes `ocr_extraction`, and the field the vision documents leave
    # empty is `objects[].readable_text`, owned by step 1.03.  Two keys,
    # never designed to meet.
    #
    # The vision model reads on-screen text SPARSELY, not never: on the
    # 2026-08-26 run of 001 it filled `readable_text` on 10 of 159
    # objects - `Chattahoochee Ave NW`, `SCUFFLEWA BREWING CO` and eight
    # more - and left 149 null.  A recorded claim that it "provably
    # cannot" read text was wrong and is corrected here and in
    # `project_layout.STEPS`.
    #
    # Turn it on for a project whose footage has signage worth reading:
    #     manage_project.py run <slug> --with ocr_extraction
    "ocr_extraction": (
        "Costs 445s on 001 and no step consumes its `ocr_extraction` "
        "output. The vision model already reads signage sparsely "
        "(10 of 159 objects on 001's 2026-08-26 run), so the 445s buys "
        "little until something reads the key. Captain's ruling on "
        "#245, 2026-08-28. Turn on with --with ocr_extraction."
    ),
}


# ── Reading the DAG ──────────────────────────────────────────────────

_DAG_PATH = (Path(__file__).resolve().parents[2]
             / "library" / "processes" / "edit_video" / "dag.json")
_LIBRARY_ROOT = Path(__file__).resolve().parents[1]


def load_dag(path=None) -> dict:
    return json.loads(Path(path or _DAG_PATH).read_text(encoding="utf-8"))


def load_manifests(dag: dict) -> Dict[str, dict]:
    """`{node_id: manifest}` read straight off disk, same as the runner."""
    out: Dict[str, dict] = {}
    for node in dag.get("nodes", []):
        manifest_path = _LIBRARY_ROOT / node["step_ref"] / "manifest.json"
        try:
            out[node["id"]] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out[node["id"]] = {}
    return out


def optional_inputs(manifest: Optional[dict]) -> Set[str]:
    """Input names the step declares it can run without.

    The same reading `run_pipeline.gather_step_inputs` does, so a scope
    refusal and the mid-run crash it prevents cannot disagree.
    """
    names = set()
    for inp in ((manifest or {}).get("interface") or {}).get("inputs", []) or []:
        if not inp.get("required", True):
            names.add(inp.get("name", ""))
    return names


@dataclass(frozen=True)
class Prerequisite:
    """One thing that must EXIST for a step to run.

    Named after the state it is about rather than the step that usually
    makes it, because that is the captain's model (#260): "given that all
    necessary prerequisties have been fulfilled".  `producer` is where
    the DAG says the value normally comes from, and it is one of three
    ways the condition can be met - not the condition itself.
    """

    consumer: str
    producer: str
    state_key: str
    """The key under `step_outputs[producer]`.  Empty for an edge with no
    `data_mapping`, which merges the producer's whole output: there is no
    key to name, so nothing but the producer can satisfy it."""

    input_name: str
    """What the consumer's manifest calls it.  Empty for the whole-output
    case, for the same reason."""

    @property
    def names_a_key(self) -> bool:
        return bool(self.state_key)


def prerequisites(dag: dict,
                  manifests: Mapping[str, dict]) -> List[Prerequisite]:
    """Every HARD condition in the pipeline, one per required key.

    Hard means exactly what `run_pipeline.gather_step_inputs` raises on:
    a mapped key the consumer did not declare optional, or an unmapped
    edge, where there is no key to judge.  Nothing here guesses - the
    refusal and the crash it prevents read the same two declarations.
    """
    out: List[Prerequisite] = []
    for edge in dag.get("edges", []):
        consumer, producer = edge.get("to"), edge.get("from")
        mapping = edge.get("data_mapping") or {}
        optional = optional_inputs(manifests.get(consumer))
        if not mapping:
            out.append(Prerequisite(consumer, producer, "", ""))
            continue
        for source_key, destination in mapping.items():
            if destination not in optional:
                out.append(Prerequisite(consumer, producer, source_key,
                                        destination))
    return out


def hard_requirements(dag: dict,
                      manifests: Mapping[str, dict]) -> Dict[str, Dict[str, Set[str]]]:
    """`{consumer: {producer: {input keys the consumer cannot run without}}}`.

    The same facts as `prerequisites`, collapsed to producers - which is
    what the closure walks. Derived rather than computed a second time,
    so the two readings of "hard" cannot drift apart.
    """
    out: Dict[str, Dict[str, Set[str]]] = {}
    for need in prerequisites(dag, manifests):
        keys = out.setdefault(need.consumer, {}).setdefault(
            need.producer, set())
        if need.input_name:
            keys.add(need.input_name)
    return out


def topological_order(dag: dict) -> List[str]:
    """The DAG in run order - the same Kahn walk the runner does."""
    nodes = [n["id"] for n in dag.get("nodes", [])]
    in_degree = {n: 0 for n in nodes}
    adjacency: Dict[str, List[str]] = {n: [] for n in nodes}
    for edge in dag.get("edges", []):
        src, dst = edge["from"], edge["to"]
        if src in adjacency and dst in in_degree:
            adjacency[src].append(dst)
            in_degree[dst] += 1
    queue = deque(n for n in nodes if in_degree[n] == 0)
    order: List[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbour in adjacency[node]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)
    if len(order) != len(nodes):
        raise ScopeError("DAG has cycles")
    return order


# ── What the project already has on file ─────────────────────────────

def recorded_outputs(state: Optional[Mapping]) -> Dict[str, Mapping]:
    """`{node_id: what it recorded}` for steps a run can still be handed.

    BOTH halves are required.  A ledger entry says the step finished; the
    `step_outputs` entry is the artifact `gather_step_inputs` will
    actually read.  A ledger entry with no output would let a selection
    pass here and die in the runner, which is the whole failure this
    module exists to move earlier.

    The output is returned rather than just the name, because a
    prerequisite names a KEY: a step that finished and recorded
    something else is not a step that recorded THIS.
    """
    if not state:
        return {}
    from library.tools import step_ledger

    outputs = state.get("step_outputs") or {}
    completed = step_ledger.all_completed(state)
    return {node_id: (outputs[node_id] if isinstance(outputs[node_id], dict)
                      else {})
            for node_id in completed if node_id in outputs}


def satisfied_steps(state: Optional[Mapping]) -> Set[str]:
    """The names alone, for callers that only need "did it finish"."""
    return set(recorded_outputs(state))


IN_THIS_RUN = "in this run"
RECORDED = "recorded by a previous run"
SUPPLIED = "supplied from outside the pipeline"


def satisfaction(need: Prerequisite,
                 run_set: Set[str],
                 recorded: Mapping[str, Mapping],
                 external: Mapping[str, object]) -> Optional[str]:
    """How this prerequisite is met, or None.

    Three ways, asked of the STATE rather than of the lineage, which is
    the whole of #260's second half.  An unmapped edge merges a
    producer's whole output and so names no key; nothing but that
    producer can stand in for it, and the refusal says so.
    """
    if need.producer in run_set:
        return IN_THIS_RUN
    if not need.names_a_key:
        return RECORDED if need.producer in recorded else None
    if need.state_key in (recorded.get(need.producer) or {}):
        return RECORDED
    if need.state_key in external:
        return SUPPLIED
    return None


# ── The selection ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Selection:
    """What the captain asked for, before the DAG is consulted."""

    target: Optional[str] = None
    only: Tuple[str, ...] = ()
    skip: Tuple[str, ...] = ()
    with_steps: Tuple[str, ...] = ()

    @property
    def is_scoped(self) -> bool:
        """True when this selection is anything other than a full run."""
        return bool(self.target or self.only or self.skip)


@dataclass(frozen=True)
class ResolvedScope:
    """A selection the DAG has agreed to."""

    steps_to_run: Tuple[str, ...]
    """In run order. What this invocation will attempt."""

    universe: Tuple[str, ...]
    """In run order. Every step a DEFAULT run of this pipeline would
    attempt - the whole DAG minus what is off by default. This is what
    the run summary measures completeness against, so a step that is off
    by default does not hold the run at PARTIAL forever."""

    skipped: Tuple[str, ...]
    """In run order. In the universe, left out of this run."""

    reasons: Dict[str, str] = field(default_factory=dict)
    """`{node_id: why it was skipped}`, for every entry in `skipped`."""

    from_cache: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    """`{skipped producer: (consumers it is still feeding from file,)}`.
    The evidence that leaving a step out was safe."""

    from_external: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    """`{state key supplied from outside: (consumers reading it,)}`.
    The other evidence, and the one a reader is least likely to expect,
    so it is reported on every run that uses it."""

    default_off: Tuple[str, ...] = ()
    """Steps that are in the DAG and off by default, so they are not in
    `universe` at all. Reported on EVERY run, including a plain full one:
    a step that exists and silently never runs is the trap AGENTS.md
    section 3 exists to stop."""

    selection: Selection = field(default_factory=Selection)

    @property
    def is_scoped(self) -> bool:
        return list(self.steps_to_run) != list(self.universe)


_SKIP_REQUESTED = "excluded by --skip"
_SKIP_DEFAULT = "off by default"
_SKIP_UNNEEDED = "not needed by the selected goals"


def resolve(selection: Selection,
            dag: Optional[dict] = None,
            manifests: Optional[Mapping[str, dict]] = None,
            state: Optional[Mapping] = None,
            always_include: Iterable[str] = (),
            invalidated: Iterable[str] = (),
            project_folder: Optional[str] = None,
            external: Optional[Mapping[str, object]] = None) -> ResolvedScope:
    """Turn a selection into a step list, or raise `ScopeError`.

    `always_include` is for a step named by a flag that predates this
    module - `--step ocr_extraction` names one step outright, and naming
    a step is a stronger statement than any default.

    `invalidated` names steps whose recorded output this run is about to
    discard - the `--rerun` targets.  They cannot satisfy an excluded
    dependency, because by the time the run reaches the consumer the
    output will be gone.

    `project_folder` is read for VERIFIED external state - values the
    captain produced outside the pipeline and put under `external/`.
    Each is checked at the moment this asks, and a file that does not
    check out raises here rather than being ignored
    (`library/tools/external_inputs.py`).  `external` passes an already
    verified mapping in instead, which is what the runner does so the
    checks run once per run rather than once per step.
    """
    dag = dag if dag is not None else load_dag()
    manifests = manifests if manifests is not None else load_manifests(dag)
    order = topological_order(dag)
    rank = {node_id: i for i, node_id in enumerate(order)}
    known = set(order)

    _reject_unknown(selection, known, always_include)

    needs = prerequisites(dag, manifests)
    recorded = {node_id: output
                for node_id, output in recorded_outputs(state).items()
                if node_id not in set(invalidated)}
    if external is None:
        from library.tools import external_inputs
        folder = project_folder or (state or {}).get("project_folder", "")
        external = {key: entry.value for key, entry
                    in external_inputs.load(folder, state).items()}

    # Which default-off steps this run turns back on. Naming a step in
    # --only, --with or --step is an explicit selection and outranks the
    # default; --skip on the same step is a contradiction and is refused
    # in _reject_unknown.
    re_selected = (set(selection.with_steps) | set(selection.only)
                   | set(always_include))
    off_by_default = {node_id for node_id in DESELECTED_BY_DEFAULT
                      if node_id in known and node_id not in re_selected}

    universe = [node_id for node_id in order if node_id not in off_by_default]

    # 1. The goals.
    if selection.target:
        target = TARGETS.get(selection.target)
        if target is None:
            raise ScopeError(
                f"unknown target {selection.target!r}. Known targets: "
                f"{', '.join(sorted(TARGETS)) or '(none)'}."
            )
        goals = set(target.goals)
    elif selection.only:
        goals = set(selection.only)
    else:
        goals = set(universe)
    goals |= set(always_include)

    # 2. The closure over hard edges. Soft parents are left behind: a
    #    consumer that declares an input optional has said it can run
    #    without it, and a target should not drag in a step nothing it
    #    wants actually needs.
    #
    #    A producer whose every hard output for this consumer is SUPPLIED
    #    is left behind too, and that is the point of supplying it: the
    #    captain who cut the rough on the timeline by hand did it so the
    #    pipeline would not cut it again. A recorded output does NOT do
    #    this - history is not a request - so a re-run still rebuilds
    #    what it is asked to.
    needs_by_consumer: Dict[str, Dict[str, List[Prerequisite]]] = {}
    for need in needs:
        needs_by_consumer.setdefault(need.consumer, {}).setdefault(
            need.producer, []).append(need)

    wanted: Set[str] = set()
    queue = deque(goals)
    while queue:
        node_id = queue.popleft()
        if node_id in wanted:
            continue
        wanted.add(node_id)
        for producer, producer_needs in needs_by_consumer.get(
                node_id, {}).items():
            if all(need.names_a_key and need.state_key in external
                   for need in producer_needs):
                continue
            if producer not in wanted:
                queue.append(producer)

    # 3. Subtract what this run declines.
    excluded = set(selection.skip) | off_by_default
    stranded_goals = sorted(goals & excluded, key=lambda n: rank[n])
    if stranded_goals and selection.target:
        raise ScopeError(
            f"target {selection.target!r} cannot be reached: it needs "
            f"{', '.join(stranded_goals)}, which this run excludes. Drop "
            f"the --skip, or drop the --target."
        )
    run_set = {node_id for node_id in wanted if node_id not in excluded}

    # 4. Refuse a selection that strands a consumer.
    _assert_dependencies_met(run_set, needs, recorded, external, excluded,
                             order)

    steps_to_run = tuple(sorted(run_set, key=lambda n: rank[n]))
    skipped = tuple(node_id for node_id in universe if node_id not in run_set)

    reasons = {}
    for node_id in skipped:
        if node_id in selection.skip:
            reasons[node_id] = _SKIP_REQUESTED
        elif node_id in off_by_default:
            reasons[node_id] = (f"{_SKIP_DEFAULT} - "
                                f"{DESELECTED_BY_DEFAULT[node_id]}")
        else:
            reasons[node_id] = _SKIP_UNNEEDED

    from_cache: Dict[str, Tuple[str, ...]] = {}
    from_external: Dict[str, List[str]] = {}
    for need in needs:
        if need.consumer not in run_set or need.producer in run_set:
            continue
        how = satisfaction(need, run_set, recorded, external)
        if how == RECORDED:
            from_cache.setdefault(need.producer, [])
            if need.consumer not in from_cache[need.producer]:
                from_cache[need.producer].append(need.consumer)
        elif how == SUPPLIED:
            from_external.setdefault(need.state_key, [])
            if need.consumer not in from_external[need.state_key]:
                from_external[need.state_key].append(need.consumer)

    return ResolvedScope(
        steps_to_run=steps_to_run,
        universe=tuple(universe),
        skipped=skipped,
        reasons=reasons,
        from_cache={producer: tuple(sorted(consumers, key=lambda n: rank[n]))
                    for producer, consumers in from_cache.items()},
        from_external={key: tuple(sorted(consumers, key=lambda n: rank[n]))
                       for key, consumers in from_external.items()},
        default_off=tuple(node_id for node_id in order
                          if node_id in off_by_default),
        selection=selection,
    )


def _reject_unknown(selection: Selection, known: Set[str],
                    always_include: Iterable[str]) -> None:
    """A misspelled step id is refused by name, not silently ignored."""
    for label, values in (("--only", selection.only),
                          ("--skip", selection.skip),
                          ("--with", selection.with_steps)):
        for value in values:
            if value not in known:
                raise ScopeError(
                    f"{label} {value!r} is not a step in this pipeline. "
                    f"Known steps: {', '.join(sorted(known))}."
                )
    contradicted = sorted(
        (set(selection.skip) & (set(selection.only) | set(selection.with_steps)
                                | set(always_include))))
    if contradicted:
        raise ScopeError(
            f"{', '.join(contradicted)} is both selected and skipped. "
            f"Say it once."
        )


def _assert_dependencies_met(run_set: Set[str],
                             needs: Sequence[Prerequisite],
                             recorded: Mapping[str, Mapping],
                             external: Mapping[str, object],
                             excluded: Set[str],
                             order: Sequence[str]) -> None:
    """Refuse before the run starts, naming the missing state.

    A run that begins and dies forty minutes in because a producer was
    excluded is worse than one that refuses in a second.

    The question asked of each prerequisite is whether the STATE exists -
    from a step in this run, from a recorded output, or from a verified
    external supply - and not which step is scheduled to make it.  That
    is why a producer that ran, recorded an output and did not record
    THIS KEY is refused here: `gather_step_inputs` raises on the key, so
    accepting the step's name would move the crash back into the run.
    """
    rank = {node_id: i for i, node_id in enumerate(order)}
    unmet: Dict[Tuple[str, str], Set[str]] = {}
    for need in needs:
        if need.consumer not in run_set:
            continue
        if satisfaction(need, run_set, recorded, external) is not None:
            continue
        unmet.setdefault((need.consumer, need.producer), set()).add(
            need.state_key)

    if not unmet:
        return

    lines = ["This selection cannot run. Refusing before the run starts.", ""]
    for (consumer, producer), keys in sorted(
            unmet.items(), key=lambda kv: (rank.get(kv[0][0], 0),
                                           rank.get(kv[0][1], 0))):
        named = sorted(key for key in keys if key)
        what = ", ".join(named) if named else "its whole output"
        state_of_producer = ("excluded by this run"
                             if producer in excluded
                             else "not in this run")
        recorded_but_not_this = (
            producer in recorded and named
            and not any(key in recorded[producer] for key in named))
        if recorded_but_not_this:
            has = sorted(recorded[producer]) or ["nothing"]
            lines.append(
                f"  {consumer} needs {what} from {producer}, which is "
                f"{state_of_producer}. Its recorded output has "
                f"{', '.join(has)} and not {what}."
            )
        else:
            lines.append(
                f"  {consumer} needs {what} from {producer}, which is "
                f"{state_of_producer} and has no recorded output in this "
                f"project."
            )
    stranded = sorted({consumer for consumer, _ in unmet},
                      key=lambda n: rank.get(n, 0))
    producers = sorted({producer for _, producer in unmet},
                       key=lambda n: rank.get(n, 0))
    suppliable = sorted({key for keys in unmet.values() for key in keys
                         if key in _checkable_keys()})
    lines += [
        "",
        "Either:",
        f"  - also skip the consumers (and then theirs, until the "
        f"selection closes): --skip {' --skip '.join(stranded)}",
        f"  - or run the producers once so their output is on file: "
        f"--only {' --only '.join(producers)}",
    ]
    if suppliable:
        lines.append(
            f"  - or supply the state yourself, if you already have it: "
            f"{', '.join(suppliable)} can be put under "
            f"<project>/external/ and is CHECKED before it counts. See "
            f"library/tools/external_inputs.py.")
    raise ScopeError("\n".join(lines))


def _checkable_keys() -> Set[str]:
    """State keys that CAN be supplied from outside. Read off the check
    table, so the refusal never advertises a route that does not exist."""
    from library.tools import external_inputs

    return set(external_inputs.CHECKS)


# ── Reporting ────────────────────────────────────────────────────────

def describe(scope: ResolvedScope) -> List[str]:
    """The lines a run prints about its own scope. One list, so the
    runner, `--dry-run` and the tests all say the same thing."""
    lines = []
    selection = scope.selection
    if selection.target:
        target = TARGETS[selection.target]
        lines.append(f"  Target: {target.name} - {target.description}")
    lines.append(f"  Selected: {len(scope.steps_to_run)} of "
                 f"{len(scope.universe)} steps")
    for node_id in scope.default_off:
        lines.append(f"  Off by default: {node_id} - "
                     f"{DESELECTED_BY_DEFAULT[node_id]}")
    if scope.skipped:
        lines.append(f"  Skipped:  {len(scope.skipped)} steps")
        for node_id in scope.skipped:
            lines.append(f"    - {node_id}: {scope.reasons[node_id]}")
    for producer, consumers in scope.from_cache.items():
        lines.append(f"  Satisfied from a previous run: {producer} "
                     f"(feeding {', '.join(consumers)})")
    for key, consumers in scope.from_external.items():
        lines.append(f"  Satisfied from outside the pipeline: {key} "
                     f"(feeding {', '.join(consumers)}) - verified, see "
                     f"the lines above")
    return lines


def estimated_seconds(scope: ResolvedScope, dag: Optional[dict] = None,
                      steps: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """`{'selected': s, 'skipped': s}` off the DAG's own estimates.

    An estimate, and named as one. The real number is what the ledger
    recorded on the last run.

    `steps` is the list the run will actually attempt, which is narrower
    than the scope when `--step` or `--from` has trimmed it further.
    Reading the scope instead reported the whole pipeline's cost for a
    one-step run.
    """
    dag = dag if dag is not None else load_dag()
    estimate = {n["id"]: int(n.get("estimated_duration_seconds") or 0)
                for n in dag.get("nodes", [])}
    selected = set(scope.steps_to_run if steps is None else steps)
    return {
        "selected": sum(estimate.get(n, 0) for n in selected),
        "skipped": sum(estimate.get(n, 0) for n in scope.universe
                       if n not in selected),
    }


# ── The CLI half ─────────────────────────────────────────────────────

def add_scope_arguments(parser) -> None:
    """The four scoping flags, registered from here by BOTH CLIs.

    `manage_project.py run` forwards to
    `library/processes/edit_video/run_pipeline.py`, so the two have to
    accept the same words. Registering them once is what stops the
    wrapper growing a flag the runner does not know, or the reverse.

    This module imports nothing heavy, so `manage_project.py` can call it
    without dragging the ML stack in at import time (AGENTS.md section 9).
    """
    targets = ", ".join(sorted(TARGETS)) or "(none defined)"
    default_off = ", ".join(sorted(DESELECTED_BY_DEFAULT)) or "none"
    parser.add_argument(
        "--target", metavar="NAME",
        help=f"Run only what a named destination needs. Known: {targets}. "
             f"The step list is resolved from the DAG on every run, never "
             f"stored, so it stays right as the pipeline changes.")
    parser.add_argument(
        "--only", action="append", metavar="STEP", default=[],
        help="Run this step and whatever it cannot run without. "
             "Repeatable.")
    parser.add_argument(
        "--skip", action="append", metavar="STEP", default=[],
        help="Leave this step out. Repeatable. Refuses before starting if "
             "something still in the run needs it and the project has no "
             "recorded output for it.")
    parser.add_argument(
        "--with", action="append", metavar="STEP", default=[],
        dest="with_steps",
        help=f"Turn on a step that is off by default ({default_off}). "
             f"Repeatable.")

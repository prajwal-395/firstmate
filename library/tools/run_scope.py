"""One enumeration for scoping a run: what to run, what to leave out, and
the refusal when the two do not fit together.

`--step` runs one step and `--from` runs a suffix; neither consults the
DAG about the selection.  This resolves a selection against the DAG
BEFORE the run starts, so a selection that strands a consumer is refused
in the first second rather than inside `gather_step_inputs` mid-run.
Both CLIs register its flags from `add_scope_arguments`.

What a selection is
-------------------
Three inputs, and they compose (`Selection`):

* **target** - a NAME for a destination (`TARGETS`).  A target names its
  GOAL steps and nothing else; the step list is the goals' dependency
  closure, walked off the DAG on every run.
* **only** - goal steps given directly, for the case no target covers.
* **skip** - steps this run declines, whatever else selected them.

`with_steps` re-selects a step that is off by default (below).

How a selection resolves
------------------------
`resolve` returns a `ResolvedScope` or raises `ScopeError`:

1.  Goals are the target's goals, or `only`, or - when neither is given -
    the whole selectable universe.
2.  The run set is the goals' closure over HARD edges.  Soft edges are
    not followed.
3.  Anything in `skip` comes out of the run set.
4.  Every remaining step's hard parents must be IN the run set or already
    SATISFIED.  Anything else is a refusal, named before the run starts.

An edge is HARD when it carries at least one `data_mapping` key the
consumer's manifest does NOT declare optional (`hard_requirements`,
`optional_inputs`) - exactly the condition under which
`run_pipeline.gather_step_inputs` raises.  The refusal and the crash it
prevents read the same two declarations.

Excluding a producer refuses its consumers
------------------------------------------
A consumer has no code path for an absent required input (AGENTS.md
10.1), so a selection that strands one is REFUSED, and the refusal names
the two ways out: skip the consumer too, or run the producer once so its
output is on file.  "I just want the rough cut" is expressed by naming a
GOAL, not by excluding twelve steps.

A prerequisite is a condition on STATE, not on lineage
------------------------------------------------------
A step declares what must EXIST for it to run - a `Prerequisite`, one per
required key (`prerequisites`) - and `satisfaction` asks whether that
state exists by one of three means: a step in this run makes it
(`IN_THIS_RUN`), a previous run recorded it (`RECORDED`), or it was
supplied from outside and CHECKED OUT (`SUPPLIED`,
`library/tools/external_inputs.py`).  A producer that recorded a
DIFFERENT key does not satisfy.  An edge with no `data_mapping` merges the
producer's whole output and names no key, so only that producer can
stand in for it (`unmappable_producers`).

A recorded output (`recorded_outputs`, `satisfied_steps`) needs both
halves: a ledger entry AND an output under `step_outputs`.  Staleness is
the ledger's business (source fingerprints, `--rerun`), not this module's.

What the captain already has is not made again
----------------------------------------------
A value under `<project>/external/` is a REQUEST, not a record.  A step
every one of whose routed outputs (`routed_state_keys`) has been SUPPLIED
does not run, on any run shape: `supplied_producers` names them and
`resolve` takes them out of the universe the way `DESELECTED_BY_DEFAULT`
is taken out.

* ALL of a producer's routed keys, never some of them.  A producer with
  an unmapped edge names no keys and can never be fully supplied.
* Naming the step is REFUSED, not obeyed.  `--only`, `--with` and
  `--step` outrank a default, but a supplied value is also a request and
  running the step would overwrite it, so the pair is refused by name
  (`_reject_supplied_and_selected`), like `--skip X --only X`.

The three entry points this serves are measured in
`docs/ENTRY_POINTS_MEASURED.md`.

Steps that are off by default
-----------------------------
`DESELECTED_BY_DEFAULT` is the one place a step is declared wired but not
run.  The step IS in the DAG, runs whenever it is named, and turning it on
is `--with <id>`.  A step may only be here if nothing hard-depends on it,
or every default run would refuse; `tests/test_run_scope.py` checks that.

`describe` is the one list of lines a run prints about its scope, shared
by the runner, `--dry-run` and the tests; `estimated_seconds` is the DAG's
estimate for what will actually be attempted.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/run_scope.py`, and both CLIs register its flags from it.
- **A selection is resolved against the DAG before the run starts, or refused.** A selection that strands a consumer names the consumer, the producer and the missing output keys.
- **A prerequisite is a condition on STATE, not on lineage.** `run_scope.Prerequisite` is one required KEY, and the resolver asks whether that key exists by any of three means: a step in this run makes it, a previous run recorded it, or it was supplied from outside and CHECKED.
- **An edge is HARD when it carries a key the consumer does not declare optional** - the same condition `gather_step_inputs` raises on. Soft parents are not pulled in by a target.
- **Excluding a producer REFUSES its consumers; it never drops them silently.** There is no "let downstream cope": a required input has no absent-value code path (section 10.1). Say "I just want the rough cut" by naming a GOAL, not by excluding twelve steps.
- **A recorded output satisfies an excluded dependency** - ledger entry, a `step_outputs` value, AND the KEY inside it.   A `--rerun` target is about to be discarded, so it satisfies nothing.
- **A recorded output does not remove a step from the run; a SUPPLIED one does.** History is not a request. The captain putting a value under `external/` is saying "do not make this", so the closure stops at that producer.
- **A target names its GOAL steps and nothing else.** The step list is walked off the DAG every run, so inserting a step upstream keeps the target right without anybody editing it. `rough_cut_subtitles` is the one target; add another only on evidence.
- **A step that is off by default is reported on every run**, including a plain full one, and is not counted as never-completed - a step that exists and silently never runs is the trap this file's step-directory check exists to stop.
- `tests/test_run_scope.py`.

The measurements and rulings behind these rules (#250, #260, the
measured shadowing of a supplied value): docs/evidence/run_scope.md.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from library.tools.ren_refusal import RenRefusal


class ScopeError(RenRefusal):
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

    # select_reels requires a timeline_transcript, which is produced
    # by `python3 -m library.tools.timeline_transcript <project> --write`
    # outside the pipeline (it needs Resolve open and WhisperX).  Running
    # it by default would crash any pipeline run that hasn't produced one.
    # Turn on with --with select_reels once the transcript exists.
    "select_reels": (
        "Requires a timeline_transcript produced outside the pipeline "
        "(needs Resolve open and WhisperX). Running it by default would "
        "crash on the missing transcript. Build the transcript first, "
        "then turn on with --with select_reels."
    ),

    # `judge_reels` reads step 3.04's chosen moments and the same
    # transcript, so it is off for exactly the reason 3.04 is: a default
    # run has no transcript and there would be no reels to read either.
    # `--only judge_reels` pulls 3.04 in with it (it cannot run without
    # it); `--with select_reels --with judge_reels` runs the pair.
    "judge_reels": (
        "Reads the reels select_reels chose, from the same "
        "timeline_transcript produced outside the pipeline. A default "
        "run has neither. Turn on with --with judge_reels beside "
        "--with select_reels, or --only judge_reels, which pulls the "
        "selector in with it."
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


def routed_state_keys(dag: dict) -> Dict[str, Set[str]]:
    """`{producer: every state key it hands to another node}`.

    Read off the SAME `data_mapping` edges `prerequisites` reads, so the
    two cannot disagree about what a step gives the rest of the pipeline.

    Deliberately NOT filtered to hard edges.  `prerequisites` drops a key
    the consumer declared optional, and a producer left out because its
    hard keys were supplied would take its optional ones with it,
    silently.  `select_broll` is the live case: `b_roll_assignments` is
    hard and `b_roll_interjections` is optional at every consumer, and a
    cut whose cutaways quietly vanished would look like a cut nobody
    asked for.  Both keys or neither.

    A producer with an edge that carries no `data_mapping` at all merges
    its WHOLE output into the consumer, so there is no set of keys that
    covers it.  `unmappable_producers` names those.
    """
    routed: Dict[str, Set[str]] = {}
    for edge in dag.get("edges", []):
        for source_key in (edge.get("data_mapping") or {}):
            routed.setdefault(edge["from"], set()).add(source_key)
    return routed


def unmappable_producers(dag: dict) -> Set[str]:
    """Nodes with an outgoing edge that names no keys at all.

    Such a node cannot be stood in for by supplying state, because the
    consumer is handed its whole output and nothing enumerates what that
    is.  Named rather than silently missing: "this one can never be
    supplied" is a real answer.
    """
    return {edge["from"] for edge in dag.get("edges", [])
            if not (edge.get("data_mapping") or {})}


def supplied_producers(dag: dict,
                       external: Mapping[str, object]
                       ) -> Dict[str, Tuple[str, ...]]:
    """`{node: the keys}` for every node whose ENTIRE output was supplied.

    The captain put those values under `external/` so the pipeline would
    not make them again (see the module docstring), and the check that
    they are real has already run - `external` only ever holds values
    `external_inputs.verify` accepted.

    A node emitting nothing onto an edge is never here: there is no set
    of keys to satisfy, so "all of them are supplied" would be vacuously
    true and the step would disappear from every run that happened to
    have an `external/` directory.  That is the empty-side-passes shape
    (AGENTS.md 10.4), and it is refused by construction rather than
    tested for.
    """
    unmappable = unmappable_producers(dag)
    out: Dict[str, Tuple[str, ...]] = {}
    for node_id, keys in routed_state_keys(dag).items():
        if node_id in unmappable or not keys:
            continue
        if keys <= set(external):
            out[node_id] = tuple(sorted(keys))
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
        raise ScopeError(
            "the pipeline DAG has cycles",
            "a step that needs its own output can never be ordered",
            "fix the cycle in library/processes/edit_video/dag.json")
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

    supplied: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    """`{node: the state keys of its that were SUPPLIED}` - the steps
    this run does not perform because the captain handed in what they
    would have made.

    Out of `universe` for the same reason `default_off` is: the work is
    done, so a step that correctly did not run must not hold the run at
    PARTIAL. Reported on every run for the same reason too - a step that
    silently never runs is the trap, and "you gave me this, so I did not
    make it" is the single most surprising thing this resolver does."""

    pulled_in: Tuple[str, ...] = ()
    """Steps that are off by default and are running anyway, because a
    step this run NAMED cannot run without them.

    Reported for the mirror of the reason `default_off` is: a step that
    is off by default and turns itself on silently is the same trap from
    the other side. `--only judge_reels` running `select_reels` too is
    `--only`'s own contract - "run this step and whatever it cannot run
    without" - and the run says which steps that turned out to be."""

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

    # The hard edges, indexed by consumer. Built HERE rather than at
    # step 2 because `re_selected` below has to walk them: a default-off
    # step that a named step cannot run without is turned on with it.
    needs_by_consumer: Dict[str, Dict[str, List[Prerequisite]]] = {}
    for need in needs:
        needs_by_consumer.setdefault(need.consumer, {}).setdefault(
            need.producer, []).append(need)

    # Which default-off steps this run turns back on. Naming a step in
    # --only, --with or --step is an explicit selection and outranks the
    # default; --skip on the same step is a contradiction and is refused
    # in _reject_unknown.
    named = (set(selection.with_steps) | set(selection.only)
             | set(always_include))

    # AND whatever a named step CANNOT RUN WITHOUT, where that is itself
    # off by default.
    #
    # `--only <step>` is documented as "run this step and whatever it
    # cannot run without", and until 2026-09-06 it did not do that across
    # a default-off producer: it left the producer excluded and then
    # refused the selection for the gap it had just made. It never showed
    # because `ocr_extraction` was the only default-off step and nothing
    # consumes it. `judge_reels` consumes `select_reels`, both are off by
    # default, and `--only judge_reels` was refused with advice to run
    # `--only select_reels` first - so the step was reachable only by
    # running the whole pipeline.
    #
    # `--skip` still wins: a step named there is left out and the
    # selection is refused for the gap, which is the honest answer to
    # "run this, but not the thing it needs".
    pulled_in: Set[str] = set()
    queue = deque(named)
    seen: Set[str] = set()
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        for producer, producer_needs in needs_by_consumer.get(
                node_id, {}).items():
            if all(need.names_a_key and need.state_key in external
                   for need in producer_needs):
                continue
            if producer in DESELECTED_BY_DEFAULT and producer not in named \
                    and producer not in set(selection.skip):
                pulled_in.add(producer)
            queue.append(producer)

    re_selected = named | pulled_in
    off_by_default = {node_id for node_id in DESELECTED_BY_DEFAULT
                      if node_id in known and node_id not in re_selected}

    # A step whose every routed output the captain SUPPLIED. Not a
    # default this run may outrank: naming it and supplying it are two
    # contradictory requests, so the pair is refused rather than one of
    # them silently winning.
    supplied = supplied_producers(dag, external)
    # Asked of what was NAMED, not of `re_selected`: the refusal's own
    # words are "was named on the command line", and a step the closure
    # pulled in was not. It cannot arise anyway - the walk above skips a
    # producer whose hard needs are all external, and a node is only in
    # `supplied_producers` when every routed key of it is - but a guard
    # that would print a false sentence if it ever did fire is worth
    # narrowing to the thing it is actually about.
    _reject_supplied_and_selected(supplied, named, selection)

    universe = [node_id for node_id in order
                if node_id not in off_by_default and node_id not in supplied]

    # 1. The goals.
    if selection.target:
        target = TARGETS.get(selection.target)
        if target is None:
            raise ScopeError(
                f"unknown target {selection.target!r}",
                f"known targets: {', '.join(sorted(TARGETS)) or '(none)'}",
                f"pass --target "
                f"{' / --target '.join(sorted(TARGETS)) or '(none)'}, or "
                f"drop --target and scope with --only/--skip"
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
    wanted: Set[str] = set()
    queue = deque(goals)
    while queue:
        node_id = queue.popleft()
        if node_id in wanted:
            continue
        wanted.add(node_id)
        # Everything this node would have produced is already on file, so
        # nothing it needs matters: the walk stops rather than dragging
        # in a whole preflight to feed a step that will not run.
        if node_id in supplied:
            continue
        for producer, producer_needs in needs_by_consumer.get(
                node_id, {}).items():
            if all(need.names_a_key and need.state_key in external
                   for need in producer_needs):
                continue
            if producer not in wanted:
                queue.append(producer)

    # 3. Subtract what this run declines.
    excluded = set(selection.skip) | off_by_default | set(supplied)
    # A goal whose output was SUPPLIED is REACHED, not stranded - the
    # thing the target names as its destination is already on file. Only
    # a goal this run declines, or one that is off by default, strands
    # the target.
    stranded_goals = sorted(goals & (set(selection.skip) | off_by_default),
                            key=lambda n: rank[n])
    if stranded_goals and selection.target:
        raise ScopeError(
            f"target {selection.target!r} cannot be reached",
            f"it needs {', '.join(stranded_goals)}, which this run excludes",
            "drop the --skip, or drop the --target"
        )
    run_set = {node_id for node_id in wanted if node_id not in excluded}

    # 4. Refuse a selection that strands a consumer.
    _assert_dependencies_met(run_set, needs, recorded, external, excluded,
                             order)

    # The predicate, environment and coverage kinds are NOT asked here,
    # and that is deliberate.
    #
    # Step 4 can only ever say "a key is present" - it is derived from
    # `inputs[].required` and `data_mapping`, which is the whole of what
    # those two declarations can express. The other three kinds live in
    # `library/tools/requirements.py`, and they are asked by the RUNNER,
    # against the steps that will actually EXECUTE.
    #
    # Asking them here would ask the wrong set: `--from` and `--step`
    # narrow the step list after this function has already returned, so a
    # requirement refused here could belong to a step the run was never
    # going to reach. `run_pipeline` asks once, after that narrowing.
    # See the module docstring of `requirements.py`.

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
        supplied={node_id: supplied[node_id] for node_id in order
                  if node_id in supplied},
        pulled_in=tuple(node_id for node_id in order
                        if node_id in pulled_in),
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
                    f"{label} {value!r} is not a step in this pipeline",
                    f"known steps: {', '.join(sorted(known))}",
                    f"spell it as one of the known steps - see "
                    f"`ren edit --help` for the scoping flags"
                )
    contradicted = sorted(
        (set(selection.skip) & (set(selection.only) | set(selection.with_steps)
                                | set(always_include))))
    if contradicted:
        raise ScopeError(
            f"{', '.join(contradicted)} is both selected and skipped",
            "one run cannot both run a step and leave it out",
            "say it once: keep it under --only/--with or under --skip"
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

    lines = []
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
    fixes = [
        f"also skip the consumers (and then theirs, until the "
        f"selection closes): --skip {' --skip '.join(stranded)}",
        f"or run the producers once so their output is on file: "
        f"--only {' --only '.join(producers)}",
    ]
    if suppliable:
        fixes.append(
            f"or supply the state yourself, if you already have it: "
            f"{', '.join(suppliable)} can be put under "
            f"<project>/external/ and is CHECKED before it counts. See "
            f"library/tools/external_inputs.py.")
    raise ScopeError(
        "this selection cannot run",
        "Refusing before the run starts: a run that begins and dies "
        "because a producer was excluded is worse than one that "
        "refuses in a second:\n" + "\n".join(lines),
        "either:\n  - " + "\n  - ".join(fixes))


def _reject_supplied_and_selected(supplied: Mapping[str, Tuple[str, ...]],
                                  re_selected: Set[str],
                                  selection: Selection) -> None:
    """Refuse naming a step whose output this project already supplies.

    The two are contradictory REQUESTS, not a request and a default.
    `--with ocr_extraction` outranks `DESELECTED_BY_DEFAULT` because a
    default is what happens when nobody said anything; a file under
    `external/` is somebody saying something.  Letting the flag win would
    run the step, write `step_outputs[node]`, and shadow the supplied
    value for every later reader - `gather_step_inputs` reads the step's
    own output before it reads `external` - so the captain's hand-made
    work would be silently discarded by a run they asked for.

    Refused rather than resolved either way, and the message says both
    ways out, because guessing which one they meant is the whole class of
    defect this resolver exists to remove.
    """
    clash = sorted(set(supplied) & set(re_selected))
    if not clash:
        return
    detail = []
    for node_id in clash:
        keys = ", ".join(supplied[node_id])
        detail.append(
            f"  {node_id} was named on the command line, and this project "
            f"supplies its whole output from outside the pipeline "
            f"({keys}). Running it would overwrite what you supplied: a "
            f"step's own output is read before external state, so the "
            f"file under external/ would still be on disk and nothing "
            f"would read it again.")
    raise ScopeError(
        "this selection cannot run",
        "naming a step whose output this project already supplies is two "
        "contradictory requests, not a request and a default:\n"
        + "\n".join(detail),
        "either:\n"
        f"  - drop the flag naming "
        f"{', '.join(clash)}, and the supplied value stands;\n"
        f"  - or remove "
        f"{', '.join(f'external/{key}.json' for node in clash for key in supplied[node])}"
        f" and let the pipeline make it.")


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
    for node_id, keys in scope.supplied.items():
        lines.append(f"  Not run, its output was supplied: {node_id} - "
                     f"{', '.join(keys)} came from <project>/external/ "
                     f"and was verified, so this step is not asked to "
                     f"make it again")
    for node_id in scope.pulled_in:
        lines.append(f"  Off by default and running anyway: {node_id} - "
                     f"a step this run named cannot run without it")
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
    parser.add_argument(
        "--override", action="append", metavar="REQUIREMENT", default=[],
        dest="overrides",
        help="Proceed past a requirement that refuses, deliberately. "
             "Repeatable. Only a requirement that declares itself "
             "overridable may be named; anything else is refused. The "
             "override is RECORDED in the run's own outputs, so a later "
             "reader can see what was overridden and why it refused. "
             "See library/tools/requirements.py.")

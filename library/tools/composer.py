"""Resolve a goal backwards to the shortest capability set that reaches it.

Given a goal - one requirement NAME, in the vocabulary `Operation.requires`
and `Operation.effect` already share - work backwards through effect and
requires to the capability set that reaches it, shortest first.  A goal no
capability produces is REFUSED BY NAME - "no capability produces X" -
rather than run and failed.  That refusal is the deliverable as much as
the resolution is: the operator learns the goal is unreachable before
anything runs.

What this reads, and what it never does
---------------------------------------
This reads ONLY the derived contracts: `requirements.all_requirements()`
for the vocabulary, `Operation.requires` for preconditions and
`Operation.effect` for what satisfies them.  It runs nothing, writes
nothing, and owns no step logic - Ruling 1 (`library/tools/operations.py`)
holds because a plan only NAMES operations; executing one is still
`Operation.execute`'s own refusal-or-run.

The blindness, stated rather than patched around
------------------------------------------------
The composer can only select capabilities - registered operations - so it
inherits both limits of the layer underneath, and both are TRUE rather
than gaps:

* THE TWO.  Two operations have a deliberately empty derived effect
  (`operations.EMPTY_EFFECT_REASONS`: two ARTIFACTS written to disk
  rather than state).  A composer working backwards from requirements
  alone can never select them, because artifact productions are
  invisible as goals.  No plan this module returns ever names one, and
  `tests/test_composer.py` pins that.  (`reel.gate_stills`, the third
  artifact, shares `verify_reels`' verdict effect by the node
  granularity `Operation.effect` declares - and is still never
  planned: that test pins it explicitly, and the route-selection
  guard covers the selector naming `reel.verify`.)
*
  THE VERDICTS, WHICH ARE NO LONGER BLIND.  The tree was checked
  against a third kind - VERDICT, the effect that is pass/fail rather
  than a state key - and the captain ruled on it 2026-09-23: a verdict
  is expressible as a goal AS ITS OWN requirement kind, not by
  loosening what a requirement means.  `requirements.VERDICTS` is that
  kind, so `sfx_library.validate`, `reel.verify` and
  `validation.resolve` derive non-empty effects and compose as
  `verdict.*` goals.  Whether each one REACHES is measured per goal,
  not promised by the kind: a verdict whose own preconditions strand
  still refuses, naming what stops it.
*
  THE OPTIONALS, WHICH ARE NO LONGER BLIND EITHER.  The tree was
  checked against a fourth kind - OPTIONAL, the edge that may or may
  not carry state - and the captain ruled on it 2026-09-23, board
  answer "add-optional": ADD OPTIONALITY TO THE VOCABULARY, as its
  own kind and not by loosening `produced_by` or widening an existing
  kind.  `requirements.OPTIONALS` is that kind, so `prosody.analyse`,
  `color_grade.resolve`, `ocr.extract`, `transitions.resolve`,
  `sfx.resolve` and `vfx.resolve` derive non-empty effects and compose
  as `optional.*` goals - the three mechanism-B edges
  (`transition_spec`, `sfx_spec`, `enhancement_spec` for
  `compile_manifest`) alongside the four mechanism-A productions.  The
  seventh node, `creative_cohesion`, owns no registered operation, so
  its goal refuses by name with the producer to run in the DAG - the
  middle-of-the-DAG answer below, not a failure of the kind.  Whether
  each one REACHES is measured per goal, not promised by the kind.
* THE MIDDLE OF THE DAG.  Thirteen producer nodes have no registered
  operation at all (`scan`, `catalog`, `speech_sequence`,
  `review_rough_cut`, `music_selection` and the
  rest).  A goal whose chain passes through one is unreachable BY
  CAPABILITIES ALONE - reaching it would mean adding a capability, which
  this module will not do on the caller's behalf.  It refuses instead,
  naming the deepest requirement nothing reaches and which step produces
  it, so the operator knows the DAG run (or the outside supply) that the
  plan would need first.

  Measured at introduction: exactly two goals close through capabilities
  alone (`state.judge_reels.reel_selection`,
  `state.verify_reels.reel_build`).  Everything else refuses.  That
  proportion is a finding about registry coverage, not about this
  module: as operations are registered for middle-DAG nodes, more goals
  resolve with nothing changed here.  `creative_cohesion` is the live
  instance: `optional.compile_manifest.cohesion_review` names its
  production as a goal, and the composition refuses naming the node -
  the edge is expressed, the capability is not registered.

What a precondition without a producer becomes
----------------------------------------------
A requirement nothing produces cannot be reached by running, but it can
still HOLD at run time - and `Operation.execute` checks exactly that
before it runs.  So a producer-less precondition is an ASSUMPTION the
plan reports, never a refusal:

* `environment` kind - a machine fact (`env.npx`, `env.resolve_scripting`
  and the rest).  The requirement layer reports these rather than
  refusing on them, and so does the plan: `assumes_machine`.
* anything else producer-less - satisfiable only from outside the
  pipeline (the captain's approval, `project.yaml`, the transcript the
  runner reads off disk).  `assumes_outside`, quoting the requirement's
  own `describe`, which already says what must be supplied.

Asymmetry, deliberately: as a GOAL, a producer-less requirement still
refuses - no sequence of capabilities makes approval, a binding or a
tool on PATH exist.  As a PRECONDITION it is something the operator
supplies once and the plan runs under.  That mirrors `run_scope`: a goal
whose output was SUPPLIED is REACHED, not stranded.

Reachability
------------
    python3 -m library.tools.composer state.verify_reels.reel_build
    python3 -m library.tools.composer --list
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass

COMPLETED = "completed"
REFUSED = "refused"
STATUSES = (COMPLETED, REFUSED)

POST_BRIDGE = "post_bridge.py"
"""The body a standalone run cannot satisfy without the model's answer.

`Operation.missing_model_answer` refuses a post-bridge run unless the
caller supplies what the model owes.  A composed plan supplies nothing -
it only names capabilities - so where sibling operations share one
effect, the representative is the half that runs on gathered inputs
alone.  This is a tie-break between equivalents, never a second
vocabulary: every sibling has identical `requires` and `effect`.
"""


class ComposerError(Exception):
    """The composer cannot serve this request."""


@dataclass(frozen=True)
class Composition:
    """What the composer decided about one goal.

    A refusal carries the requirement nothing reaches (`blocker`), the
    DAG nodes that produce it if any (`blocker_producers`), and the
    chain of requirements from the goal down to it (`chain`) - so
    `refusal_reason()` names the unreachable thing AND the way out,
    rather than just the goal that stranded.
    """

    goal: str
    status: str
    operations: tuple[str, ...] = ()
    """Capability names, in run order: every producer before its consumer."""
    assumes_machine: tuple[str, ...] = ()
    """Producer-less `environment` preconditions the machine must provide."""
    assumes_outside: tuple[str, ...] = ()
    """Producer-less anything-else preconditions the operator supplies."""
    unknown_goal: bool = False
    blocker: str = ""
    blocker_producers: tuple[str, ...] = ()
    blocker_is_machine: bool = False
    """Whether the blocker is an `environment` requirement - a machine
    fact the machine provides, rather than something a step writes.
    Carried (not re-derived by name prefix) so the remedy quotes the
    registry's own classification."""
    chain: tuple[str, ...] = ()
    detail: str = ""
    """The requirement's own `describe`, for the one line the refusal
    quotes.  Empty on a completed plan and on an unknown goal."""
    selection: tuple[RouteSelection, ...] = ()
    """The post-composition route choice, one entry per planned node.

    Empty on every `compose` plan - the context-free path names the
    representative and narrates nothing - and populated by
    `compose_with_change`, where the selector may have preferred a
    sibling.  `describe_plan` renders it; `as_record` carries it only
    when non-empty."""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ComposerError(
                f"unknown status {self.status!r}; known: "
                f"{', '.join(STATUSES)}")
        if self.status == REFUSED and not self.blocker:
            raise ComposerError(
                f"refused composition for {self.goal!r} names no blocker; "
                f"a refusal that does not say what nothing produces is "
                f"the defect this type prevents")

    @property
    def completed(self) -> bool:
        return self.status == COMPLETED

    @property
    def refused(self) -> bool:
        return self.status == REFUSED

    def refusal_reason(self) -> str:
        """Why it refused, naming what no capability produces."""
        if not self.refused:
            return ""
        head = f"no capability produces {self.blocker}"
        if self.unknown_goal:
            return (f"{head}: {self.goal!r} is not a requirement any "
                    f"operation speaks - a goal has to name one, because "
                    f"effects and preconditions are matched by name.")
        if self.blocker != self.goal:
            head = (f"cannot reach {self.goal}: {head}")
        remedy: str
        if not self.blocker_producers:
            if self.blocker_is_machine:
                remedy = ("it is a machine fact the machine provides, "
                          "not something a step writes")
            else:
                remedy = ("nothing in the registry produces it - "
                          "satisfy it from outside the pipeline")
                if self.detail:
                    remedy += f": {self.detail}"
        else:
            who = ", ".join(self.blocker_producers)
            remedy = (f"produced by {who}, which "
                      f"{'has' if len(self.blocker_producers) == 1 else 'have'} "
                      f"no registered operation - no capability reaches "
                      f"it. Run the step in the DAG, then compose from "
                      f"there")
        lines = [f"{head} - {remedy}."]
        if self.chain:
            lines.append("Needed via: " + " <- ".join(self.chain))
        return "\n".join(lines)

    def as_record(self) -> dict:
        """The flat form for a log line or a hook payload."""
        record = {
            "goal": self.goal,
            "status": self.status,
            "operations": list(self.operations),
            "assumes_machine": list(self.assumes_machine),
            "assumes_outside": list(self.assumes_outside),
            "unknown_goal": self.unknown_goal,
            "blocker": self.blocker,
            "blocker_producers": list(self.blocker_producers),
            "blocker_is_machine": self.blocker_is_machine,
            "chain": list(self.chain),
        }
        if self.selection:
            record["selection"] = [s.as_record() for s in self.selection]
        return record


def representative(node_id: str) -> str:
    """The one operation a plan names for an owning node.

    Siblings share `requires` and `effect` (both derive from the owning
    node), so any of them closes the same goals at the same length - the
    choice is a deterministic tie-break, stated here rather than spread
    across the search: prefer PROJECT scope (the composer plans at
    project scope, and a REGION-only splice without a region is a
    promise the plan cannot keep), then a body that runs on gathered
    inputs alone (a post-bridge standalone refuses without the model's
    answer), then registry order.
    """
    from library.tools import operations as ops_mod

    owned = ops_mod.by_node(node_id)
    if not owned:
        raise ComposerError(
            f"no registered operation is owned by {node_id!r}; the "
            f"composer plans capabilities, not bare nodes")
    from library.tools.scope import PROJECT

    # `owned` is in registry order, so its index IS registry order.
    def rank(indexed) -> tuple:
        index, op = indexed
        return (PROJECT not in op.scopes, op.body == POST_BRIDGE, index)

    return min(enumerate(owned), key=rank)[1].name


def _plan_maps():
    """The three maps `_closure` searches, keyed by CAPABILITY id.

    `all_producers` stays node-keyed on purpose: it is the DAG remedy a
    refusal names ("produced by X - run the step in the DAG"), the one
    place a plan still speaks nodes.  `capable` names one capability per
    producing node - its `representative` - so siblings sharing an
    effect never tie inside the search; `select_operation` chooses among
    them afterwards.
    """
    from library.tools import dag_adapter
    from library.tools import operations as ops_mod
    from library.tools import requirements as req_mod

    by_name = {r.name: r for r in req_mod.all_requirements()}
    reps = {node: representative(node)
            for node in dag_adapter.nodes_with_capabilities()}
    all_producers = {n: tuple(sorted(set(r.produced_by)))
                     for n, r in by_name.items()}
    capable = {n: tuple(reps[node] for node in sorted(set(r.produced_by)
                                                      & set(reps)))
               for n, r in by_name.items()}
    # The live registry, not the cached `capabilities.all()`: an
    # operation's name IS its capability id, and its `requires` is the
    # same derivation the spec carries.
    needs = {op.name: tuple(r.name for r in op.requires)
             for op in ops_mod.all()}
    return by_name, all_producers, capable, needs


def _closure(goal: str,
             all_producers: Mapping[str, tuple[str, ...]],
             capable: Mapping[str, tuple[str, ...]],
             needs: Mapping[str, tuple[str, ...]],
             ) -> tuple[tuple[str, ...] | None, str, tuple[str, ...],
                        tuple[str, ...]]:
    """Shortest producer-first capability list closing `goal`, or the strand.

    Pure over the three maps, so tests can drive it without the real
    registry (`_plan_maps` builds the real ones):

    * `all_producers`: requirement name to every DAG node producing it -
      the legacy remedy a strand names.
    * `capable`: requirement name to the capabilities producing it, in
      the order ties break - the only producers a plan may select.
    * `needs`: capability id to the requirement names it asks.

    Returns `(planned, blocker, producers, chain)` - `planned` is None when
    nothing closes the goal, and then `blocker` is the requirement
    nothing reaches that the depth-first traversal met first,
    `producers` the nodes producing it (empty when no node does), and
    `chain` the requirement path from `goal` down to `blocker`.

    A requirement nothing produces at all closes with no nodes: it is a
    leaf the operator supplies (the machine, the captain, the runner's
    own injection), and the caller reports it as an assumption.  Only a
    requirement produced by nodes WITHOUT a registered operation
    strands - running cannot reach it, and neither can supplying it,
    because a step owns it.
    """
    # Successes memoize: a closed requirement closes the same way
    # whatever path reached it.  Strands do not - a strand through a
    # cycle depends on the stack above it - and the graph is small
    # enough that recomputing them costs nothing.
    memo: dict[str, tuple[str, ...]] = {}

    def close_requirement(
            name: str, stack: tuple[str, ...]
    ) -> tuple[tuple[str, ...] | None, str, tuple[str, ...],
               tuple[str, ...]]:
        if name in memo:
            return (memo[name], "", (), ())
        if name in stack:
            # A producer path that needs its own goal is unproductive -
            # skip it here; the caller tries the next producer, and if
            # none is left the requirement strands naming itself.
            return (None, name, all_producers.get(name, ()), (name,))
        producers = all_producers.get(name, ())
        if not producers:
            # A leaf: nothing writes it, so nothing in any plan needs to
            # - the caller reports it as an assumption instead.
            return ((), "", (), ())
        options = capable.get(name, ())
        if not options:
            return (None, name, tuple(sorted(set(producers))), (name,))
        winner: tuple[tuple[str, ...], str, tuple[str, ...],
                      tuple[str, ...]] | None = None
        strand: tuple[tuple[str, ...] | None, str, tuple[str, ...],
                      tuple[str, ...]] | None = None
        for capability in options:
            sub = close_capability(capability, stack + (name,))
            if sub[0] is not None and (
                    winner is None or len(sub[0]) < len(winner[0])):
                winner = (sub[0], "", (), ())
            elif sub[0] is None and strand is None:
                strand = sub
        if winner is not None:
            memo[name] = winner[0]
            return winner
        assert strand is not None
        _, blocker, owners, tail = strand
        return (None, blocker, owners, (name,) + tail)

    def close_capability(
            capability: str, stack: tuple[str, ...]
    ) -> tuple[tuple[str, ...] | None, str, tuple[str, ...],
               tuple[str, ...]]:
        ordered: list[str] = []
        for req in needs.get(capability, ()):
            sub, blocker, producers, tail = close_requirement(req, stack)
            if sub is None:
                return (None, blocker, producers, tail)
            for have in sub:
                if have not in ordered:
                    ordered.append(have)
        ordered.append(capability)
        return (tuple(ordered), "", (), ())

    return close_requirement(goal, ())


def compose(goal: str) -> Composition:
    """Resolve `goal` backwards to the shortest capability set.

    Returns a COMPLETED plan or a REFUSED composition - never raises
    for an unreachable goal, because the refusal is the deliverable.
    Blank goals raise: that is a caller error, not an unreachable goal.
    """
    from library.tools import requirements as req_mod

    name = (goal or "").strip()
    if not name:
        raise ComposerError(
            "a goal has to name a requirement, e.g. "
            "state.verify_reels.reel_build")

    by_name, all_producers, capable, needs = _plan_maps()

    req = by_name.get(name)
    if req is None or not all_producers.get(name):
        return Composition(
            goal=name, status=REFUSED, unknown_goal=req is None,
            blocker=name, blocker_producers=(),
            blocker_is_machine=req is not None and req.kind
            == req_mod.KIND_ENVIRONMENT,
            detail="" if req is None else req.describe)

    planned, blocker, producers, chain = _closure(name, all_producers,
                                                  capable, needs)
    if planned is None:
        by_blocker = by_name.get(blocker)
        return Composition(
            goal=name, status=REFUSED, blocker=blocker,
            blocker_producers=producers,
            blocker_is_machine=by_blocker is not None
            and by_blocker.kind == req_mod.KIND_ENVIRONMENT,
            chain=() if len(chain) < 2 else chain,
            detail="" if by_blocker is None else by_blocker.describe)

    ordered_ops = tuple(planned)
    kinds = {n: r.kind for n, r in by_name.items()}
    machine: list[str] = []
    outside: list[str] = []
    for capability in planned:
        for req_name in needs.get(capability, ()):
            if all_producers.get(req_name):
                continue
            target = (machine if kinds.get(req_name)
                      == req_mod.KIND_ENVIRONMENT else outside)
            if req_name not in target:
                target.append(req_name)
    return Composition(
        goal=name, status=COMPLETED, operations=ordered_ops,
        assumes_machine=tuple(sorted(machine)),
        assumes_outside=tuple(sorted(outside)))


# ── Post-composition selection between equivalent routes ──────────
#
# `_closure` plans over capabilities, one `representative` per legacy
# node by a static tie-break, so the search itself can
# never prefer the cheap route: two siblings with identical `requires`
# and `effect` are indistinguishable at that layer, correctly.  The
# selector below runs AFTER composition, where runtime context exists:
# the asked change (structured) and the gate's verdict over a live
# track read.  It steers no declaration - every sibling keeps
# declaring the same effect - it only names which sibling serves the
# asked change.
#
# `compose` stays the context-free path (representative throughout, a
# pure function of the registry); `compose_with_change` is the entry
# point that consults the selector.


@dataclass(frozen=True)
class RouteSelection:
    """Which sibling serves one planned node, and why.

    `decided_by` is `"selector"` when runtime context chose, or
    `"representative"` when the static tie-break stands (no change
    spec, no track read, a node with no change gate, or a gate
    refusal falling back to the full route).  `gate_class` is the
    `reel_touchup.qualify` verdict (`"composed"`,
    `"composed_with_rederivation"`, or `"refused"`) and empty when
    the gate was never consulted.  `reason` narrates the choice for
    the plan record: which route won, what the other route would
    have done differently, and - when the rebuild is the fallback -
    that the rebuild is not an always-available slow path.  `style`
    echoes the style context the call arrived with (None when the
    video declares none): the seam follow-up selectors branch on,
    recorded here rather than inferred later.
    """

    node: str
    operation: str
    decided_by: str
    gate_class: str = ""
    reason: str = ""
    alternatives: tuple[str, ...] = ()
    measured_basis_cited: bool = False
    style: dict | None = None

    def as_record(self) -> dict:
        """The flat form for a log line or a hook payload."""
        return {
            "node": self.node,
            "operation": self.operation,
            "decided_by": self.decided_by,
            "gate_class": self.gate_class,
            "reason": self.reason,
            "alternatives": list(self.alternatives),
            "measured_basis_cited": self.measured_basis_cited,
        }


SELECTOR = "selector"
REPRESENTATIVE_FALLBACK = "representative"

MEASURED_TOUCHUP_BASIS = {
    "edit_classes": ("swap_pixels",),
    "reel": 26,
    "date": "2026-09-22",
    "cache": "warm Fusion cache",
    "touchup_seconds": 2.37,
    "rebuild_seconds": 208,
    "ratio": "~88x",
}
"""The ONE measured cost basis the selector may cite, quoted verbatim.

One reel (Reel 26), one edit class (the ending swap: `swap_pixels`),
2026-09-22, warm Fusion cache: 2.37s touchup vs 208s rebuild, ~88x.
Tie-breaking justification in the plan record, never arithmetic to
optimise over - and cited ONLY for the class it was measured on
(risk 5: it testifies about nothing else, so entry motion and
property setting never borrow it)."""

DIVERGENCE_NOTE = (
    "same state key, different pixel truth: a rebuild re-derives "
    "captions, grades and overlays from current state, while a "
    "touchup deliberately does not (no Fusion pass, no caption "
    "re-render) - it keeps the approved body pixels as they stand.")
"""Why the cheap route is not just the slow route done faster."""

REBUILD_FALLBACK_CAVEAT = (
    "the rebuild is not an always-available slow path: on the "
    "measured reel (Reel 26) it refuses at verify today on an "
    "unrelated body-caption finding, so a refused touchup routed "
    "to the rebuild risks a ~208s refusal, not a ~208s success.")
"""Why falling back to the rebuild narrates its own viability."""


def _select_build_reels(node_id: str, owned: tuple,
                         change_spec, tracks,
                         style=None) -> RouteSelection:
    """Choose among `build_reels`' change-serving siblings.

    Candidates are the default full route (`representative`, today
    `reel.build`) plus every caller-supplied sibling - `reel.touchup`,
    `reel.entry_motion`, `reel.set_properties`.  `reel.ask` shares the
    derived effect but takes no change spec and writes asks, not reel
    state, so it is out of the candidate set by its own contract,
    not by a bent declaration.  The gate (`reel_touchup.qualify`,
    pure over the track read) decides: `composed` routes to the
    narrowest operation covering the spec's edit kinds, a refusal
    routes to the rebuild with the refusal and the fallback caveat
    surfaced.  `style` is carried, not read: the seam a follow-up
    selector branches on, echoed on the selection.  The reel identity
    a follow-up matches it against is the change spec's `reel` - the
    same identity `build_style_context` resolves the context with -
    so the two meet without a second address.
    """
    from library.tools import operations as ops_mod

    default = representative(node_id)
    siblings = sorted(op.name for op in owned if op.caller_supplied)
    candidates = [default] + [s for s in siblings if s != default]

    if change_spec is None:
        return RouteSelection(
            node=node_id, operation=default,
            decided_by=REPRESENTATIVE_FALLBACK,
            reason=("no change spec supplied: the static tie-break "
                    f"stands ({default}). The cheap route is chosen "
                    "only when a change is actually asked."),
            alternatives=tuple(candidates[1:]))
    if tracks is None:
        return RouteSelection(
            node=node_id, operation=default,
            decided_by=REPRESENTATIVE_FALLBACK,
            reason=("a change was asked but no live track read was "
                    "supplied: the gate reads tracks, not prose, so "
                    f"the static tie-break stands ({default})."),
            alternatives=tuple(candidates[1:]))

    from library.tools import reel_touchup as touchup_mod

    try:
        qualification = touchup_mod.qualify(tracks, change_spec)
    except touchup_mod.TouchupRefused as refused:
        return RouteSelection(
            node=node_id, operation=default,
            decided_by=SELECTOR, gate_class="refused",
            reason=(f"the touchup gate refused ({refused}). "
                    f"Falling back to {default}. {DIVERGENCE_NOTE} "
                    f"{REBUILD_FALLBACK_CAVEAT}"),
            alternatives=tuple(candidates[1:]),
            style=dict(style) if style is not None else None)

    edits = [e.get("op") for e in (change_spec.get("edits") or ())
             if isinstance(e, Mapping)]
    if edits and all(op == "set_properties" for op in edits):
        chosen = "reel.set_properties"
        why_narrow = ("every edit writes properties in place (no "
                      "delete, no place)")
    elif edits and all(op == "entry_motion" for op in edits):
        chosen = "reel.entry_motion"
        why_narrow = ("every edit animates an overlay entry in place")
    else:
        chosen = "reel.touchup"
        why_narrow = ("the spec spans the touchup's mixed edit kinds")
    if chosen not in ops_mod.names():
        raise ComposerError(
            f"selector chose {chosen!r} for {node_id!r}, which the "
            f"registry no longer names; refusing rather than "
            f"planning a route nothing owns")
    gate_class = qualification.gate_class
    basis = ""
    cited = False
    if (gate_class == touchup_mod.COMPOSED
            and edits and all(op == "swap_pixels" for op in edits)):
        cited = True
        basis = (f" Measured basis (cited, not optimised over): "
                 f"{MEASURED_TOUCHUP_BASIS['touchup_seconds']}s "
                 f"touchup vs {MEASURED_TOUCHUP_BASIS['rebuild_seconds']}s "
                 f"rebuild "
                 f"({MEASURED_TOUCHUP_BASIS['ratio']}) on Reel "
                 f"{MEASURED_TOUCHUP_BASIS['reel']}, "
                 f"{MEASURED_TOUCHUP_BASIS['date']}, "
                 f"{MEASURED_TOUCHUP_BASIS['cache']}.")
    elif gate_class == touchup_mod.COMPOSED_WITH_REDERIVATION:
        basis = (f" {qualification.cost_statement}")
    else:
        basis = (" No measured cost basis is cited for this edit "
                 "class: the 88x figure was an ending swap "
                 "(`swap_pixels`) on Reel 26, 2026-09-22, and "
                 "testifies about nothing else.")
    return RouteSelection(
        node=node_id, operation=chosen, decided_by=SELECTOR,
        gate_class=gate_class,
        reason=(f"gate {gate_class}: {why_narrow}, so {chosen} "
                f"serves the change.{basis} {DIVERGENCE_NOTE}"),
        alternatives=tuple(c for c in candidates if c != chosen),
        measured_basis_cited=cited,
        style=dict(style) if style is not None else None)


def _select_verify_reels(node_id: str, owned: tuple,
                         change_spec, tracks,
                         style=None) -> RouteSelection:
    """The verdict route stands; the sibling serves no change.

    `verify_reels` owns two operations sharing one derived effect, and
    the sharing is the node granularity `Operation.effect` declares -
    not two routes to the verdict.  `reel.verify` runs the conformance
    verifier over the built reels on gathered inputs alone.
    `reel.gate_stills` is out of the candidate set by its own contract
    (`caller_supplied=True`): the gate hands it a reel label, timeline
    name and frames, and it grabs stills for the gate to judge rather
    than producing any verdict.  No change gate reads tracks for this
    node, so a change spec does not route here either - the static
    tie-break stands, by design rather than by inheritance.
    `style` is carried, not read: the seam a follow-up selector
    branches on, echoed on the selection.
    """
    default = representative(node_id)
    assert default == "reel.verify", (
        f"the verdict route is reel.verify, not {default!r}: the "
        f"selector below names it, so a tie-break that moved must fail "
        f"here rather than plan the stills grab as the verdict")
    others = sorted(op.name for op in owned if op.name != default)
    return RouteSelection(
        node=node_id, operation=default,
        decided_by=REPRESENTATIVE_FALLBACK,
        reason=("the verdict route stands (reel.verify): it runs the "
                "conformance verifier over the built reels on gathered "
                "inputs alone. reel.gate_stills is out of the candidate "
                "set by its own contract - caller-supplied, it grabs "
                "stills for the gate to judge rather than producing any "
                "verdict. No change gate reads tracks for this node, so "
                "a change spec does not route here."),
        alternatives=tuple(others),
        style=dict(style) if style is not None else None)


def _select_representative_fallback(node_id: str, owned: tuple,
                                     change_spec,
                                     tracks,
                                     style=None) -> RouteSelection:
    """Explicit stand-pat for a node with no change gate.

    Several nodes own sibling operations with one shared effect but
    no sibling takes a structured change - there is no gate to ask
    and no track read to ask it over - so the static tie-break
    stands.  The entry exists so the coverage guard can tell
    "considered, nothing to select" apart from "never considered":
    adding a caller-supplied sibling here must update this entry,
    not silently inherit it.  `style` is carried, not read: the seam
    a follow-up selector branches on, echoed on the selection.
    """
    default = representative(node_id)
    others = sorted(op.name for op in owned if op.name != default)
    return RouteSelection(
        node=node_id, operation=default,
        decided_by=REPRESENTATIVE_FALLBACK,
        reason=(f"no change gate for {node_id}: the static tie-break "
                f"stands ({default}). A future caller-supplied "
                f"sibling must give this node a real selector."),
        alternatives=tuple(others),
        style=dict(style) if style is not None else None)


_SELECTORS = {
    "build_reels": _select_build_reels,
    "plan_subtitles": _select_representative_fallback,
    "render_subtitles": _select_representative_fallback,
    "select_reels": _select_representative_fallback,
    "temporal_index": _select_representative_fallback,
    "verify_reels": _select_verify_reels,
}
"""Every routable multi-operation node, mapped to its selector.

"Routable" means owning more than one operation with a non-empty
derived effect - the set `compose` can actually name.  Empty-effect
operations (THE TWO) are composer-blind by design and never reach
selection, so they need none.  The risk-4 guard
(`tests/test_ren_selection_between_equivalent_routes.py`) fails the
moment a node outgrows this map: a new route must arrive
chosen-by-design, never inheriting the tie-break in silence.
`verify_reels` is the case that proved it: the verdict kind gave the
node a derived effect, so its two siblings - the verdict route and
the caller-supplied stills grab - arrived here, and the entry records
that only one of them serves a change."""


def selector_coverage() -> tuple[str, ...]:
    """Every node with an explicit selector entry, sorted."""
    return tuple(sorted(_SELECTORS))


def select_operation(node_id: str, change_spec=None,
                      tracks=None, style=None) -> RouteSelection:
    """Name the sibling serving `node_id` for the asked change.

    Single-route nodes return the representative without consulting
    anything.  Multi-route nodes go through their `_SELECTORS`
    entry; a node that outgrew the map REFUSES rather than silently
    inheriting the tie-break.  A non-mapping change spec is a caller
    error and raises: garbage must never route to a 208s rebuild in
    silence.  `style` is the style context
    (`video_prefs.build_style_context`: the `style` id, the merged
    preferences, and the addressed `reel` where the caller has one -
    the change spec's `reel` on the reels path, absent on a
    single-video project), or None when the video declares none -
    the context a follow-up selector branches on.  It reaches every
    handler and is echoed on the selection; routing today reads
    nothing from it, so a video with no style plans exactly what it
    always planned.
    """
    from library.tools import operations as ops_mod

    owned = ops_mod.by_node(node_id)
    if not owned:
        raise ComposerError(
            f"no registered operation is owned by {node_id!r}; the "
            f"composer plans capabilities, not bare nodes")
    effects: dict[tuple, list] = {}
    for op in owned:
        key = tuple(r.name for r in op.effect)
        if key:
            effects.setdefault(key, []).append(op.name)
    if not any(len(group) > 1 for group in effects.values()):
        default = representative(node_id)
        others = sorted(op.name for op in owned if op.name != default)
        return RouteSelection(
            node=node_id, operation=default,
            decided_by=REPRESENTATIVE_FALLBACK,
            reason=(f"sole route to its effect: the static tie-break "
                    f"stands ({default})."),
            alternatives=tuple(others),
            style=dict(style) if style is not None else None)
    if change_spec is not None and not isinstance(change_spec, Mapping):
        raise ComposerError(
            f"a change spec has to be a mapping naming edits "
            f"(e.g. {{'reel': 26, 'edits': [...]}}), not "
            f"{change_spec!r}; refusing rather than routing prose "
            f"to a rebuild")
    handler = _SELECTORS.get(node_id)
    if handler is None:
        contenders = sorted(
            name for group in effects.values() if len(group) > 1
            for name in group)
        raise ComposerError(
            f"no selector covers {node_id!r}, whose siblings "
            f"{contenders} share one effect; refusing rather than "
            f"letting a new route inherit the tie-break in silence")
    return handler(node_id, owned, change_spec, tracks, style)


def compose_with_change(goal: str, change_spec=None,
                         tracks=None, style=None) -> Composition:
    """Resolve `goal` as `compose` does, then select the route.

    `_closure` plans the same representatives (shortest capability set,
    same refusals by name) and a refusal returns unchanged - there is no
    route to choose.  On a completed plan each legacy node goes through
    `select_operation`: with a change spec and a track read the gate
    may prefer a cheap sibling; without either the representative
    stands, so `compose_with_change(goal)` with no change plans
    exactly what `compose(goal)` plans.  `style` is the style context
    (`video_prefs.build_style_context`), carried to every selection
    and echoed on it; routing reads nothing from it yet.

    `change_spec` is the structured change (`{"reel": N, "edits":
    [...], "exclude": ...}`); `tracks` is the live track read the
    gate qualifies it over - the same `reel_read.read_tracks` rows
    `timeline_oracle.snapshot_live_rows` projects (the gate reads
    the tracks, not the projection).  Neither is measured here.
    """
    from library.tools import requirements as req_mod

    name = (goal or "").strip()
    if not name:
        raise ComposerError(
            "a goal has to name a requirement, e.g. "
            "state.verify_reels.reel_build")

    by_name, all_producers, capable, needs = _plan_maps()

    req = by_name.get(name)
    if req is None or not all_producers.get(name):
        return Composition(
            goal=name, status=REFUSED, unknown_goal=req is None,
            blocker=name, blocker_producers=(),
            blocker_is_machine=req is not None and req.kind
            == req_mod.KIND_ENVIRONMENT,
            detail="" if req is None else req.describe)

    planned, blocker, producers, chain = _closure(name, all_producers,
                                                  capable, needs)
    if planned is None:
        by_blocker = by_name.get(blocker)
        return Composition(
            goal=name, status=REFUSED, blocker=blocker,
            blocker_producers=producers,
            blocker_is_machine=by_blocker is not None
            and by_blocker.kind == req_mod.KIND_ENVIRONMENT,
            chain=() if len(chain) < 2 else chain,
            detail="" if by_blocker is None else by_blocker.describe)

    # Sibling choice is still per legacy node: the selectors route among
    # the capabilities one node owns, by the asked change.
    from library.tools import dag_adapter
    from library.tools import operations as ops_mod
    selections = tuple(
        select_operation(dag_adapter.node_of(ops_mod.get(c)), change_spec,
                         tracks, style)
        for c in planned)
    ordered_ops = tuple(s.operation for s in selections)
    kinds = {n: r.kind for n, r in by_name.items()}
    machine: list[str] = []
    outside: list[str] = []
    for capability in planned:
        for req_name in needs.get(capability, ()):
            if all_producers.get(req_name):
                continue
            target = (machine if kinds.get(req_name)
                      == req_mod.KIND_ENVIRONMENT else outside)
            if req_name not in target:
                target.append(req_name)
    return Composition(
        goal=name, status=COMPLETED, operations=ordered_ops,
        assumes_machine=tuple(sorted(machine)),
        assumes_outside=tuple(sorted(outside)),
        selection=selections)


def reachable_goals() -> tuple[str, ...]:
    """Every requirement name at least one capability produces.

    The composer's addressable set: composing one either resolves or
    refuses naming a deeper blocker, but never with "not a requirement".
    """
    from library.tools import operations as ops_mod
    return tuple(sorted({r.name for op in ops_mod.all() for r in op.effect}))


def describe_plan(comp: Composition) -> str:
    """The composition, rendered for the operator who asked."""
    if comp.refused:
        return f"REFUSED: {comp.goal}\n{comp.refusal_reason()}"
    from library.tools import requirements as req_mod

    by_name = {r.name: r for r in req_mod.all_requirements()}
    lines = [f"goal: {comp.goal}", "",
             "run, in order:"]
    for i, op in enumerate(comp.operations, 1):
        lines.append(f"  {i}. {op}")
    if comp.assumes_machine or comp.assumes_outside:
        lines.append("")
        lines.append("must already hold (checked before anything runs):")
        for req_name in comp.assumes_machine + comp.assumes_outside:
            said = by_name.get(req_name)
            what = f" - {said.describe}" if said is not None else ""
            lines.append(f"  - {req_name}{what}")
    for sel in comp.selection:
        lines.append("")
        lines.append(f"route for {sel.node}: {sel.operation} "
                     f"({sel.decided_by})")
        lines.append(f"  {sel.reason}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.composer",
        description=("Resolve a goal backwards to the shortest "
                     "capability set that reaches it, or refuse it by "
                     "name."))
    parser.add_argument("goal", nargs="?",
                        help="a requirement name, e.g. "
                        "state.verify_reels.reel_build")
    parser.add_argument("--list", action="store_true",
                        help="list every goal a capability produces")
    parser.add_argument("--change-spec", default=None,
                        help=("inline JSON change spec, e.g. "
                              "'{\"reel\": 26, \"edits\": [...]}': "
                              "select the route serving the asked "
                              "change instead of the representative"))
    parser.add_argument("--tracks-file", default=None,
                        help=("path to a JSON track read "
                              "(`reel_read.read_tracks`) the change "
                              "gate qualifies over; required with "
                              "--change-spec"))
    args = parser.parse_args(argv)

    if args.list:
        goals = reachable_goals()
        print(f"{len(goals)} goals a capability produces")
        for goal in goals:
            print(f"  {goal}")
        return 0

    if not args.goal:
        print("REFUSED: a goal has to name a requirement, e.g. "
              "state.verify_reels.reel_build",
              file=sys.stderr)
        return 2

    change_spec = None
    tracks = None
    if args.change_spec is not None:
        import json

        try:
            change_spec = json.loads(args.change_spec)
        except json.JSONDecodeError as exc:
            print(f"REFUSED: --change-spec is not JSON ({exc})",
                  file=sys.stderr)
            return 2
        if args.tracks_file is None:
            print("REFUSED: --change-spec needs --tracks-file: the "
                  "gate reads tracks, not prose",
                  file=sys.stderr)
            return 2
        try:
            with open(args.tracks_file, encoding="utf-8") as handle:
                tracks = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"REFUSED: cannot read --tracks-file ({exc})",
                  file=sys.stderr)
            return 2

    try:
        if change_spec is None:
            comp = compose(args.goal)
        else:
            comp = compose_with_change(args.goal, change_spec, tracks)
    except ComposerError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    if comp.refused:
        print(describe_plan(comp), file=sys.stderr)
        return 1
    print(describe_plan(comp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

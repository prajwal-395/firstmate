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

* THE NINE.  Nine operations have a deliberately empty derived effect
  (`operations.EMPTY_EFFECT_REASONS`: three VERDICTS whose effect is a
  judgement, three ARTIFACTS written to disk rather than state, and three
  ANALYSIS whose edges are optional or absent).  A composer working
  backwards from requirements alone can never select them, because
  verdicts and optional productions are invisible as goals.  No plan this
  module returns ever names one, and `tests/test_composer.py` pins that.
* THE MIDDLE OF THE DAG.  Fourteen producer nodes have no registered
  operation at all (`scan`, `catalog`, `speech_sequence`,
  `review_rough_cut`, `creative_direction`, `music_selection` and the
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
  resolve with nothing changed here.

* THE TWO OPTIONAL-EDGE ONES.  `prosody.analyse` and `color_grade.resolve`
  write real state with real readers and stay invisible only because
  their consumers declare those inputs OPTIONAL, which
  `run_scope.prerequisites` deliberately excludes.  If the blind set ever
  needs shrinking, modelling optional edges is the cheapest route - not
  widening the effect vocabulary.  (`ocr.extract`, the ninth, is not a
  modelling gap at all: nothing reads its output.)

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
        return {
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

    def rank(op) -> tuple:
        return (PROJECT not in op.scopes,
                op.body == POST_BRIDGE,
                ops_mod.names().index(op.name))

    return min(owned, key=rank).name


def _closure(goal: str,
             all_producers: Mapping[str, tuple[str, ...]],
             capable: Mapping[str, tuple[str, ...]],
             needs: Mapping[str, tuple[str, ...]],
             ) -> tuple[tuple[str, ...] | None, str, tuple[str, ...],
                        tuple[str, ...]]:
    """Shortest producer-first node list closing `goal`, or the strand.

    Pure over the three maps, so tests can drive it without the real
    registry:

    * `all_producers`: requirement name to every DAG node producing it.
    * `capable`: requirement name to the producing nodes that own a
      registered operation - the only producers a plan may select.
    * `needs`: owning node to the requirement names its operation asks.

    Returns `(nodes, blocker, producers, chain)` - `nodes` is None when
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
        options = sorted(set(producers) & set(capable.get(name, ())))
        if not options:
            return (None, name, tuple(sorted(set(producers))), (name,))
        winner: tuple[tuple[str, ...], str, tuple[str, ...],
                      tuple[str, ...]] | None = None
        strand: tuple[tuple[str, ...] | None, str, tuple[str, ...],
                      tuple[str, ...]] | None = None
        for node in options:
            sub = close_node(node, stack + (name,))
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

    def close_node(
            node: str, stack: tuple[str, ...]
    ) -> tuple[tuple[str, ...] | None, str, tuple[str, ...],
               tuple[str, ...]]:
        ordered: list[str] = []
        for req in needs.get(node, ()):
            sub, blocker, producers, tail = close_requirement(req, stack)
            if sub is None:
                return (None, blocker, producers, tail)
            for have in sub:
                if have not in ordered:
                    ordered.append(have)
        ordered.append(node)
        return (tuple(ordered), "", (), ())

    return close_requirement(goal, ())


def compose(goal: str) -> Composition:
    """Resolve `goal` backwards to the shortest capability set.

    Returns a COMPLETED plan or a REFUSED composition - never raises
    for an unreachable goal, because the refusal is the deliverable.
    Blank goals raise: that is a caller error, not an unreachable goal.
    """
    from library.tools import operations as ops_mod
    from library.tools import requirements as req_mod

    name = (goal or "").strip()
    if not name:
        raise ComposerError(
            "a goal has to name a requirement, e.g. "
            "state.verify_reels.reel_build")

    by_name = {r.name: r for r in req_mod.all_requirements()}
    op_nodes = {op.owning_node for op in ops_mod.all()}
    all_producers = {n: tuple(sorted(set(r.produced_by)))
                     for n, r in by_name.items()}
    capable = {n: tuple(sorted(set(r.produced_by) & op_nodes))
               for n, r in by_name.items()}
    needs = {op.owning_node: tuple(r.name for r in op.requires)
             for op in ops_mod.all()}

    req = by_name.get(name)
    if req is None or not all_producers.get(name):
        return Composition(
            goal=name, status=REFUSED, unknown_goal=req is None,
            blocker=name, blocker_producers=(),
            blocker_is_machine=req is not None and req.kind
            == req_mod.KIND_ENVIRONMENT,
            detail="" if req is None else req.describe)

    nodes, blocker, producers, chain = _closure(name, all_producers,
                                                   capable, needs)
    if nodes is None:
        by_blocker = by_name.get(blocker)
        return Composition(
            goal=name, status=REFUSED, blocker=blocker,
            blocker_producers=producers,
            blocker_is_machine=by_blocker is not None
            and by_blocker.kind == req_mod.KIND_ENVIRONMENT,
            chain=() if len(chain) < 2 else chain,
            detail="" if by_blocker is None else by_blocker.describe)

    ordered_ops = tuple(representative(n) for n in nodes)
    kinds = {n: r.kind for n, r in by_name.items()}
    machine: list[str] = []
    outside: list[str] = []
    for node in nodes:
        for req_name in needs.get(node, ()):
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


def reachable_goals() -> tuple[str, ...]:
    """Every requirement name at least one capability produces.

    The composer's addressable set: composing one either resolves or
    refuses naming a deeper blocker, but never with "not a requirement".
    """
    from library.tools import operations as ops_mod
    from library.tools import requirements as req_mod

    op_nodes = {op.owning_node for op in ops_mod.all()}
    return tuple(sorted(
        r.name for r in req_mod.all_requirements()
        if set(r.produced_by) & op_nodes))


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

    try:
        comp = compose(args.goal)
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

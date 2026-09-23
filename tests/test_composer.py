"""The composer resolves a goal backwards - or refuses it by name.

The captain's intent: Ren reaches a goal without him naming the steps,
and a goal it cannot reach is refused BEFORE anything runs - "no
capability produces X" - rather than by running and failing.

Two halves, and the second is the deliverable as much as the first:

**Resolution.**  The two goals that close through capabilities alone are
pinned exactly - operations in run order, machine assumptions and
outside assumptions split.  Either half alone (operations without the
assumptions, or the split collapsed) would keep passing while the plan
stopped being runnable-or-honest.

**Refusal.**  Every other goal refuses naming what nothing produces: a
producer-less goal names itself, a goal behind a step with no operation
names the deepest requirement the traversal met and which step owns it.
`test_no_plan_names_a_blind_capability` pins the converse - the nine
deliberately empty-effect operations are never selected, no matter how
many capabilities the composer learns. The ever-selected SET is
deliberately not pinned: it grows with every coverage lane by design,
and a snapshot of it would fail each remaining lane on a literal.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import composer as C
from library.tools import operations as O
from library.tools import requirements as R

# ── Refusal: the deliverable ──────────────────────────────────────────

PRODUCER_LESS_GOALS = (
    # External: the captain, project.yaml, the runner's own injection.
    "reel_plan.approved",
    "resolve.timeline_binding",
    "timeline_transcript.on_file",
    # Machine facts, not something a step writes.
    "env.npx",
    "env.resolve_scripting",
)


@pytest.mark.parametrize("goal", PRODUCER_LESS_GOALS)
def test_producer_less_goal_is_refused_by_name(goal):
    """A goal nothing produces refuses naming itself, before any run."""
    comp = C.compose(goal)
    assert comp.refused
    assert comp.blocker == goal
    assert f"no capability produces {goal}" in comp.refusal_reason()


def test_unknown_goal_is_refused_by_name():
    """A goal no operation speaks refuses rather than closing empty.

    Closing empty would read as "nothing to do" - the plan that runs
    nothing and reports success, which is what `produced_nothing` on
    `OperationResult` exists to stop at the execution layer.
    """
    comp = C.compose("no.such.requirement")
    assert comp.refused
    assert comp.unknown_goal
    assert ("no capability produces no.such.requirement"
            in comp.refusal_reason())


def test_blank_goal_raises_rather_than_refusing():
    """Blank is a caller error, not an unreachable goal."""
    with pytest.raises(C.ComposerError):
        C.compose("   ")


def test_goal_behind_a_step_with_no_operation_names_the_blocker():
    """The refusal names what nothing reaches AND which step owns it.

    `state.mesh_spine.creative_direction` is produced by
    `creative_direction`, which has no registered operation - reaching
    it would mean adding a capability, which the composer will not do
    on the caller's behalf. `catalog` was the example here until the
    intake lane registered `footage.catalog`; `creative_direction` is
    the stable replacement because it is Class B - it waits on the
    captain's design call, so no coverage lane takes it.
    """
    comp = C.compose("state.mesh_spine.creative_direction")
    assert comp.refused
    assert comp.blocker == "state.mesh_spine.creative_direction"
    assert comp.blocker_producers == ("creative_direction",)
    reason = comp.refusal_reason()
    assert "no capability produces state.mesh_spine.creative_direction" in reason
    assert "creative_direction" in reason


def test_deep_strand_names_the_blocker_and_the_chain():
    """A goal several capabilities down refuses at the requirement that
    stranded, with the path back to the goal - not just the goal.

    This asserts the INVARIANT, not the frontier: which requirement
    strands moves every time a coverage lane lands (it was
    `state.mesh_spine.speech_sequence` until `speech.enrich` was
    registered, `state.semantic_analysis.raw_footage_files` after),
    so naming the blocker here would fail each of the remaining
    coverage lanes on a literal that is not an assertion about
    correctness. What is asserted is the shape every deep refusal
    must have: a named blocker, the chain of requirements from the
    goal down to it, and a refusal reason quoting both. The shallow
    cases - blocker is the goal itself, empty chain - already have
    their own tests above; this one pins that a DEEP strand still
    names where it stranded and how it got there."""
    comp = C.compose("state.compile_manifest.subtitle_overlay")
    assert comp.refused
    assert comp.blocker, (
        "a refused deep strand names no blocker")
    assert len(comp.chain) >= 2, (
        f"a deep strand carries the path from goal to blocker, "
        f"not an empty one: {comp.chain!r}")
    assert comp.chain[0] == "state.compile_manifest.subtitle_overlay"
    assert comp.chain[-1] == comp.blocker
    reason = comp.refusal_reason()
    assert "no capability produces" in reason
    assert comp.blocker in reason
    for producer in comp.blocker_producers:
        assert producer in reason, (
            f"the refusal names the blocker but not its producer "
            f"{producer!r}: {reason!r}")


# ── Resolution: the two goals that close ──────────────────────────────

def test_reel_selection_plan_is_exact():
    """`reel.candidates` - the bridge half, which runs on gathered inputs
    alone - represents `select_reels`, over the post-bridge sibling that
    would refuse without the model's answer."""
    comp = C.compose("state.judge_reels.reel_selection")
    assert comp.completed
    assert comp.operations == ("reel.candidates",)
    assert comp.assumes_machine == ()
    assert comp.assumes_outside == ("timeline_transcript.on_file",)


def test_reel_build_plan_is_exact():
    """`reel.build` wins registry order over `reel.ask`: same contract,
    and the build is the whole the ask is a part of."""
    comp = C.compose("state.verify_reels.reel_build")
    assert comp.completed
    assert comp.operations == ("reel.build",)
    assert comp.assumes_machine == (
        "env.face_detector",
        "env.reel_build_libraries",
        "env.resolve_scripting",
    )
    assert comp.assumes_outside == (
        "reel_plan.approved",
        "resolve.timeline_binding",
        "timeline_transcript.on_file",
    )


def test_completed_plan_is_closed():
    """Every operation's preconditions are produced earlier in the plan
    or assumed - no plan step refuses on the plan's own ordering."""
    by_node = {op.name: op for op in O.all()}
    for goal in ("state.judge_reels.reel_selection",
                 "state.verify_reels.reel_build"):
        comp = C.compose(goal)
        assert comp.completed
        produced: set[str] = set()
        for op_name in comp.operations:
            op = by_node[op_name]
            for req in op.requires:
                if req.name in produced:
                    continue
                assert req.name in (comp.assumes_machine
                                    + comp.assumes_outside), (
                    f"{op_name} needs {req.name}, which no earlier plan "
                    f"step produces and the plan does not assume")
            produced.update(r.name for r in op.effect)


# ── The blindness, pinned ─────────────────────────────────────────────

def test_no_plan_names_a_blind_capability():
    """Across every requirement in the registry, no completed plan names
    one of the nine deliberately empty-effect operations.

    This asserts the INVARIANT the name claims - a plan naming a
    capability nothing can reach would be a plan that cannot run -
    and deliberately NOT the ever-selected set. That set moves with
    every coverage lane by design (`reel.build` and `reel.candidates`
    were merely the first two), so pinning it here would fail each of
    the remaining lanes on a snapshot of a frontier this work moves.
    The disjointness is the STRONGER statement: it holds no matter
    how many capabilities the composer learns, and it still fails the
    day a plan selects what no requirement produces.
    """
    seen: set[str] = set()
    for req in R.all_requirements():
        comp = C.compose(req.name)
        if comp.completed:
            seen.update(comp.operations)
    assert seen.isdisjoint(O.EMPTY_EFFECT_REASONS), (
        f"a plan selected a capability with no derived effect: "
        f"{sorted(seen & set(O.EMPTY_EFFECT_REASONS))}")


def test_representative_prefers_the_runnable_project_half():
    """Sibling operations share one contract, so the choice is a
    tie-break - and it lands on the half that runs: PROJECT scope over
    REGION-only, a gathered-inputs body over a post-bridge."""
    assert C.representative("select_reels") == "reel.candidates"
    assert C.representative("build_reels") == "reel.build"
    assert C.representative("plan_subtitles") == "subtitles.plan"
    assert C.representative("render_subtitles") == "subtitles.render"
    with pytest.raises(C.ComposerError):
        C.representative("no.such.node")


def test_reachable_goals_are_exactly_the_capability_produced():
    """`--list` shows every requirement at least one capability
    produces - each composable to a plan or a named deeper blocker,
    never to "not a requirement"."""
    op_nodes = {op.owning_node for op in O.all()}
    expected = sorted(r.name for r in R.all_requirements()
                      if set(r.produced_by) & op_nodes)
    assert list(C.reachable_goals()) == expected
    assert "state.verify_reels.reel_build" in expected
    assert "reel_plan.approved" not in expected
    assert "env.npx" not in expected


# ── The search itself, on synthetic maps ──────────────────────────────
#
# Real data resolves only single-operation plans, so shortest-choice,
# shared subplans, ordering and cycles are driven through `_closure`
# directly - the pure seam over (all_producers, capable, needs).

def test_shortest_choice_wins():
    """Two producer nodes close the goal; the one-step closure beats
    the two-step one."""
    nodes, blocker, _, _ = C._closure(
        "g",
        all_producers={"g": ("n_long", "n_short"),
                       "a": ("m",), "leaf": ()},
        capable={"g": ("n_long", "n_short"), "a": ("m",)},
        needs={"n_long": ("a",), "n_short": ("leaf",),
               "m": ("leaf",)})
    assert blocker == ""
    assert nodes == ("n_short",)


def test_shared_subplan_runs_once():
    """Two preconditions closed by one node name it once, first."""
    nodes, blocker, _, _ = C._closure(
        "g",
        all_producers={"g": ("n",), "a": ("m",), "b": ("m",),
                       "leaf": ()},
        capable={"g": ("n",), "a": ("m",), "b": ("m",)},
        needs={"n": ("a", "b"), "m": ("leaf",)})
    assert blocker == ""
    assert nodes == ("m", "n")


def test_producers_order_before_consumers():
    """A three-deep chain plans bottom-up: the run order IS the
    dependency order."""
    nodes, blocker, _, _ = C._closure(
        "g",
        all_producers={"g": ("n3",), "r2": ("n2",), "r1": ("n1",),
                       "leaf": ()},
        capable={"g": ("n3",), "r2": ("n2",), "r1": ("n1",)},
        needs={"n3": ("r2",), "n2": ("r1",), "n1": ("leaf",)})
    assert blocker == ""
    assert nodes == ("n1", "n2", "n3")


def test_circular_producers_strand_by_name():
    """A producer cycle terminates and strands naming the requirement
    it circled on - it must never spin, and never close empty."""
    nodes, blocker, producers, chain = C._closure(
        "g",
        all_producers={"g": ("n1",), "r1": ("n2",)},
        capable={"g": ("n1",), "r1": ("n2",)},
        needs={"n1": ("r1",), "n2": ("r1",)})
    assert nodes is None
    assert blocker == "r1"
    assert producers == ("n2",)
    assert chain[0] == "g" and chain[-1] == "r1"


def test_fallback_producer_tried_after_strand():
    """The first producer strands on a blocker no capability owns, but
    the second closes - the refusal of one path is not the refusal of
    the goal."""
    nodes, blocker, _, _ = C._closure(
        "g",
        all_producers={"g": ("n_bad", "n_good"), "void": ("bare",),
                       "leaf": ()},
        capable={"g": ("n_bad", "n_good")},
        needs={"n_bad": ("void",), "n_good": ("leaf",)})
    # `void` IS produced (by `bare`) but `bare` owns no capability, so
    # `n_bad` strands while `n_good` closes on the leaf alone.
    assert blocker == ""
    assert nodes == ("n_good",)


# ── CLI ───────────────────────────────────────────────────────────────

def test_cli_resolves_refuses_and_misuses(capsys):
    assert C.main(["state.verify_reels.reel_build"]) == 0
    out = capsys.readouterr().out
    assert "reel.build" in out
    assert C.main(["reel_plan.approved"]) == 1
    err = capsys.readouterr().err
    assert "no capability produces reel_plan.approved" in err
    assert C.main([]) == 2

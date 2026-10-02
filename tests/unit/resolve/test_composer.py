"""The composer resolves a goal backwards - or refuses it by name.

History: docs/evidence/resolve_test_history.md#test_composer.
"""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import composer as C
from library.tools import dag_adapter, processes
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


def test_a_goal_nothing_produces_is_refused_by_name():
    """A goal nothing produces refuses naming itself, before any run.

    So does a goal no operation speaks: closing empty would read as
    "nothing to do" - the plan that runs nothing and reports success,
    which is what `produced_nothing` on `OperationResult` exists to stop
    at the execution layer.
    """
    for goal in PRODUCER_LESS_GOALS:
        comp = C.compose(goal)
        assert comp.refused, goal
        assert comp.blocker == goal
        assert f"no capability produces {goal}" in comp.refusal_reason()
    comp = C.compose("no.such.requirement")
    assert comp.refused
    assert comp.unknown_goal
    assert ("no capability produces no.such.requirement"
            in comp.refusal_reason())


# ── Refusal subjects the tests own ──────────────────────────────────
#
# The two tests below used to borrow their subjects from the product's
# coverage frontier - `state.mesh_spine.clip_catalog`, then the intake
# lane's retarget to `creative_direction`, then
# `state.compile_manifest.subtitle_overlay` - and every coverage lane
# that registered the missing capability turned the refusal into a
# completion. Both then died on `assert comp.refused`, which is the
# PRECONDITION above the invariants rather than an invariant itself,
# so the invariant assertions never ran at all. A subject borrowed
# from the frontier drifts by design, so both tests now compose
# SYNTHETIC goals in the reserved `synthetic.*` namespace, which no
# coverage lane will ever register: a goal no operation produces
# refuses forever by construction, and each test machine-checks that
# premise against `reachable_goals()` instead of assuming it. The one
# real goal that still refuses anywhere is deliberately NOT used as a
# subject - aiming at it would repeat the frontier-pinning mistake
# with a smaller target.

def _synthetic_requirement(name, produced_by, consumers, describe):
    """A requirement-shaped fixture the composer can strand on.

    `state_key` kind, so the constructor asks nothing beyond a
    non-empty describe and consumers. The check never runs inside
    `compose`, which reads only names, producers and consumers.
    """
    def _never_satisfies(ctx):
        return R.UNSATISFIED(
            "a synthetic test fixture is never satisfied",
            missing=name, produced_by=tuple(produced_by))
    return R.Requirement(
        name=name, kind=R.KIND_STATE_KEY, describe=describe,
        produced_by=tuple(produced_by), consumers=tuple(consumers),
        produced_keys=tuple((p, name) for p in produced_by),
        check=_never_satisfies,
        refuting_context=lambda: R.Context(),
        satisfying_context=lambda: R.Context())


def _with_synthetic_requirements(monkeypatch, *reqs):
    """Append fixture requirements to the vocabulary the composer reads.

    `Operation.requires` and `Operation.effect` derive live from
    `requirements.all_requirements()`, so this one seam moves the whole
    vocabulary `compose` plans over; the composer itself is unpatched
    and computes the refusal. Each test asserts its premise
    (`reachable_goals`) so a fixture that failed to land fails the
    test instead of passing it vacuously.
    """
    real = R.all_requirements
    monkeypatch.setattr(
        R, "all_requirements",
        lambda *args, **kwargs: [*real(*args, **kwargs), *reqs])


SYNTHETIC_UNPRODUCED_GOAL = "synthetic.composer_test.unproduced_goal"
SYNTHETIC_UNWIRED_STEP = "synthetic_composer_test_unwired_step"


def test_goal_behind_a_step_with_no_operation_names_the_blocker(
        monkeypatch):
    """The refusal names what nothing reaches AND which step owns it.

    This version SUPERSEDES the intake lane's retarget of the same
    test to `creative_direction` (PR 1335, branch
    fm/vep-cover-the-intake-nodes): that subject completed the hour
    the captain answered the design call it was waiting on, which is
    exactly the drift this synthetic subject removes. A test whose
    stability rests on a captain decision staying open breaks the
    moment the captain decides; a goal in the reserved `synthetic.*`
    namespace refuses no matter what the captain decides next.
    """
    _with_synthetic_requirements(monkeypatch, _synthetic_requirement(
        SYNTHETIC_UNPRODUCED_GOAL, (SYNTHETIC_UNWIRED_STEP,),
        ("synthetic_composer_test_consumer",),
        "a synthetic test goal nothing in the pipeline writes"))
    assert SYNTHETIC_UNPRODUCED_GOAL not in C.reachable_goals(), (
        "the test premise failed: a capability now produces the "
        "synthetic goal, so it no longer refuses by construction")
    comp = C.compose(SYNTHETIC_UNPRODUCED_GOAL)
    assert comp.refused
    assert comp.blocker == SYNTHETIC_UNPRODUCED_GOAL
    assert comp.blocker_producers == (SYNTHETIC_UNWIRED_STEP,)
    reason = comp.refusal_reason()
    assert f"no capability produces {SYNTHETIC_UNPRODUCED_GOAL}" in reason
    assert SYNTHETIC_UNWIRED_STEP in reason


SYNTHETIC_DEEP_GOAL = "synthetic.composer_test.deep_goal"
SYNTHETIC_DEEP_MID = "synthetic.composer_test.mid_requirement"
SYNTHETIC_DEEP_TOP_STEP = "synthetic_composer_test_top_step"
SYNTHETIC_DEEP_MID_STEP = "synthetic_composer_test_mid_step"


def test_deep_strand_names_the_blocker_and_the_chain(monkeypatch):
    """A goal several capabilities down refuses at the requirement that
    stranded, with the path back to the goal - not just the goal.

    This asserts the INVARIANT, not the frontier: naming the blocker
    here would fail each remaining coverage lane on a literal that is
    not an assertion about correctness. What is asserted is the shape
    every deep refusal must have: a named blocker, the chain of
    requirements from the goal down to it, and a refusal reason
    quoting both. The shallow cases - blocker is the goal itself,
    empty chain - already have their own tests above; this one pins
    that a DEEP strand still names where it stranded and how it got
    there.

    The depth is synthetic but the traversal is real: the goal's
    owning step holds the test's one capability, whose precondition is
    produced by a step holding none, so `compose` must descend one
    level and strand there. A synthetic goal that refused immediately
    would silently turn this into a third shallow test - the
    `len(chain) >= 2` assertion is what keeps it deep.
    """
    _with_synthetic_requirements(
        monkeypatch,
        _synthetic_requirement(
            SYNTHETIC_DEEP_MID, (SYNTHETIC_DEEP_MID_STEP,),
            (SYNTHETIC_DEEP_TOP_STEP,),
            "a synthetic mid-chain requirement its owning step never "
            "gets an operation for"),
        _synthetic_requirement(
            SYNTHETIC_DEEP_GOAL, (SYNTHETIC_DEEP_TOP_STEP,),
            ("synthetic_composer_test_consumer",),
            "a synthetic goal behind a capable step with a stranded "
            "precondition"))
    real_ops = O.all
    real_dirs = processes.step_dirnames
    monkeypatch.setattr(processes, "step_dirnames", lambda: {
        **real_dirs(), SYNTHETIC_DEEP_TOP_STEP: "step_9_99_synthetic_fixture"})
    dag_adapter._nodes_by_dir.cache_clear()
    monkeypatch.setattr(
        O, "all", lambda: (*real_ops(), O.Operation(
            name="synthetic.composer_test.op",
            summary="the test's one capability, closing the goal's "
            "first step but stranded on its precondition",
            owning_dir="step_9_99_synthetic_fixture",
            body="step.py", attr="run",
            produces=(SYNTHETIC_DEEP_GOAL,), consumes=())))
    assert SYNTHETIC_DEEP_MID not in C.reachable_goals(), (
        "the test premise failed: a capability now produces the "
        "synthetic mid-chain requirement, so the strand is gone")
    comp = C.compose(SYNTHETIC_DEEP_GOAL)
    assert comp.refused
    assert comp.blocker, (
        "a refused deep strand names no blocker")
    assert len(comp.chain) >= 2, (
        f"a deep strand carries the path from goal to blocker, "
        f"not an empty one: {comp.chain!r}")
    assert comp.chain[0] == SYNTHETIC_DEEP_GOAL
    assert comp.chain[-1] == comp.blocker
    reason = comp.refusal_reason()
    assert "no capability produces" in reason
    assert comp.blocker in reason
    for producer in comp.blocker_producers:
        assert producer in reason, (
            f"the refusal names the blocker but not its producer "
            f"{producer!r}: {reason!r}")


# ── Resolution: the two goals that close ──────────────────────────────


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
                  "state.verify_reels.reel_build",
                  "verdict.validate_sfx_library.sfx_library_status",
                  "verdict.verify_reels.reel_verification"):
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


# ── Verdicts, which are no longer blind ───────────────────────────────
#
# The captain's ruling 2026-09-23: a verdict is expressible as a goal AS
# ITS OWN requirement kind.  These pin the MEASUREMENT, not the
# mechanism - whether each of the three verdict operations is now
# reachable, and what stops the one that is not.


def test_validation_verdict_goal_closes_through_the_manifest_compile():
    """`validate` needs `state.validate.render_output`, which needs
    `state.render.assembly_manifest`.  That stranded while
    `compile_manifest` owned no capability; `manifest.compile` closes
    it, so the verdict goal is a plan through the compile and the
    render."""
    comp = C.compose("verdict.validate.validation_result")
    assert comp.completed, comp.refusal_reason()
    ops = comp.operations
    assert ops.index("manifest.compile") < ops.index("render.build") \
        < ops.index("validation.resolve")


# ── The blindness, pinned ─────────────────────────────────────────────

def test_no_plan_names_a_blind_capability():
    """Across every requirement in the registry, no completed plan names
    one of the five deliberately empty-effect operations.

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
    # `reel.gate_stills` is the one capability that LOOKS reachable
    # without being plannable: it shares `verify_reels`' verdict effect
    # by the node granularity `Operation.effect` declares, but it grabs
    # stills for the gate on caller-supplied arguments and produces no
    # verdict.  The route selector names `reel.verify`; no plan may
    # name the stills grab.
    assert "reel.gate_stills" not in seen, (
        "a plan named the stills grab for a verdict goal - the "
        "node-granular effect leaked into selection")


def test_reachable_goals_are_exactly_the_capability_produced():
    """`--list` shows every requirement at least one capability
    produces - each composable to a plan or a named deeper blocker,
    never to "not a requirement"."""
    op_nodes = {op.legacy_node for op in O.all()}
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

def test_the_closure_search_chooses_dedupes_and_orders():
    """Rows: two producers close the goal and the one-step closure beats
    the two-step one; two preconditions closed by one node name it once,
    first; a three-deep chain plans bottom-up (the run order IS the
    dependency order); and a first producer that strands on a blocker
    no capability owns falls back to the second - the refusal of one
    path is not the refusal of the goal (`void` IS produced, by `bare`,
    but `bare` owns no capability)."""
    rows = (
        (dict(all_producers={"g": ("n_long", "n_short"),
                             "a": ("m",), "leaf": ()},
              capable={"g": ("n_long", "n_short"), "a": ("m",)},
              needs={"n_long": ("a",), "n_short": ("leaf",),
                     "m": ("leaf",)}),
         ("n_short",)),
        (dict(all_producers={"g": ("n",), "a": ("m",), "b": ("m",),
                             "leaf": ()},
              capable={"g": ("n",), "a": ("m",), "b": ("m",)},
              needs={"n": ("a", "b"), "m": ("leaf",)}),
         ("m", "n")),
        (dict(all_producers={"g": ("n3",), "r2": ("n2",), "r1": ("n1",),
                             "leaf": ()},
              capable={"g": ("n3",), "r2": ("n2",), "r1": ("n1",)},
              needs={"n3": ("r2",), "n2": ("r1",), "n1": ("leaf",)}),
         ("n1", "n2", "n3")),
        (dict(all_producers={"g": ("n_bad", "n_good"), "void": ("bare",),
                             "leaf": ()},
              capable={"g": ("n_bad", "n_good")},
              needs={"n_bad": ("void",), "n_good": ("leaf",)}),
         ("n_good",)),
    )
    for maps, expected in rows:
        nodes, blocker, _, _ = C._closure("g", **maps)
        assert (nodes, blocker) == (expected, ""), maps


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


# ── CLI ───────────────────────────────────────────────────────────────

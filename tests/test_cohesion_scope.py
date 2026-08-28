"""A cohesion finding is applicable where it runs, or it is an observation.

Step 5.03's one finding on project 001's 2026-08-26 run was
`speech_sequence.segment_order`, and step 5.04 refused it: re-ordering the
narrative would invalidate every downstream timing.  `applied_adjustments`
was empty on that run and on every run before it.  A recommendation nobody
can apply, reported in an array named `adjustments`, reads as a change that
was made (#238).

The review cannot move upstream of what it reviews - it reads the
transition, SFX and VFX plans, all made in steps 4.02 to 4.04 - so it is
scoped where it runs instead.  `library/tools/cohesion_scope.py` is that
scope, and these tests hold the two halves of it against the code that
really acts:

- every pair the enumeration calls ACTIONABLE is really applied by
  `apply_cohesion_adjustments`, driven here rather than believed
  (AGENTS.md section 10.2);
- every pair it calls OWNED_UPSTREAM is really refused by it, with the
  enumeration's own sentence;
- a pair in neither list raises rather than becoming an adjustment that
  is then silently dropped.
"""
import pytest

from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion
from library.steps.step_5_04_compile_manifest.step import apply_cohesion_adjustments
from library.tools import cohesion_scope
from library.tools.cohesion_scope import (
    ACTIONABLE_AT_COHESION,
    OWNED_UPSTREAM,
    UnknownCohesionFinding,
)


def test_the_two_lists_do_not_overlap():
    assert not set(ACTIONABLE_AT_COHESION) & set(OWNED_UPSTREAM)


def test_neither_list_is_empty():
    """An empty ACTIONABLE list would mean the step has nothing to say
    where it runs, which is a withdrawal and not a rescope."""
    assert ACTIONABLE_AT_COHESION
    assert OWNED_UPSTREAM


@pytest.mark.parametrize("target,field", sorted(ACTIONABLE_AT_COHESION))
def test_an_actionable_pair_is_really_applied(target, field):
    """Drive the real applier. A pair declared actionable that the
    compiler has no branch for is the exact defect this file exists to
    stop, one level up."""
    transitions = [{"transition_type": "defocus", "duration_frames": 15}]
    record = apply_cohesion_adjustments(transitions, {"adjustments": [{
        "target_step": target,
        "field": field,
        "suggested_value": 10,
        "target_index": 0,
    }]})
    assert record["not_applied"] == [], (
        f"{target}.{field} is declared actionable at 5.03, and "
        f"apply_cohesion_adjustments refused it")
    assert len(record["applied"]) == 1


@pytest.mark.parametrize("target,field", sorted(OWNED_UPSTREAM))
def test_an_upstream_pair_is_really_refused(target, field):
    """The other direction: an entry that the compiler would in fact
    apply is a stale refusal, and would leave the review silent about a
    change it could have asked for."""
    record = apply_cohesion_adjustments(
        [{"transition_type": "defocus", "duration_frames": 15}],
        {"adjustments": [{
            "target_step": target,
            "field": field,
            "suggested_value": "whatever",
            "target_index": 0,
        }]})
    assert record["applied"] == []
    assert record["not_applied"][0]["reason"] == OWNED_UPSTREAM[
        (target, field)].reason


@pytest.mark.parametrize("target,field", sorted(OWNED_UPSTREAM))
def test_every_upstream_entry_names_a_step_directory_that_exists(
        target, field, request):
    from pathlib import Path
    steps = Path(request.config.rootpath) / "library" / "steps"
    owner = OWNED_UPSTREAM[(target, field)].owner_step
    assert (steps / owner).is_dir(), (
        f"{target}.{field} is owned by {owner}, which is not a step")


@pytest.mark.parametrize("target,field", sorted(OWNED_UPSTREAM))
def test_every_upstream_entry_says_how_to_act_on_it(target, field):
    """An observation a reader cannot act on is a different kind of dead
    end from the one this ticket closed."""
    how = OWNED_UPSTREAM[(target, field)].how_to_act
    assert "manage_project.py run" in how and "--rerun" in how


def test_an_undeclared_pair_raises_rather_than_defaulting():
    with pytest.raises(UnknownCohesionFinding):
        cohesion_scope.owner_of("something_new", "whatever")
    with pytest.raises(UnknownCohesionFinding):
        cohesion_scope.split([{"target_step": "something_new",
                               "field": "whatever"}])


def test_the_compiler_still_refuses_an_undeclared_pair_out_loud():
    """The compiler is the last line, so it never raises on one - a
    cohesion_review from an older run must be refused, not crash the
    compile."""
    record = apply_cohesion_adjustments([], {"adjustments": [{
        "target_step": "something_new", "field": "whatever",
        "suggested_value": 1}]})
    assert record["applied"] == []
    assert record["not_applied"][0]["reason"]


# ── The step's own output obeys the split ────────────────────────────

SPINE_60S = {"structure": [
    {"block_type": "speech", "position": 0,
     "timeline_start": 0.0, "timeline_end": 60.0}]}


def _review_with_every_finding():
    """One input that fires all three findings at once."""
    return review_creative_cohesion({
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [
            {"transition_type": "defocus", "duration_frames": 30}],
        "sfx_spec": [],
        "audio_spine": SPINE_60S,
        "speech_sequence": {
            "hook_segment": {
                "clip_id": "clip_002", "source_start": 1.0, "source_end": 3.0,
                "engagement": {"rank": 3, "composite": 55, "basis": "a tease"}},
            "body_sequence": [
                {"clip_id": "clip_016", "source_start": 48.065,
                 "source_end": 52.0,
                 "engagement": {"rank": 1, "composite": 92,
                                "basis": "the floor of the piece"}}],
        },
    })


def test_every_adjustment_the_step_emits_is_one_the_compiler_applies():
    """The invariant #238 asks for, driven end to end: what 5.03 puts in
    `adjustments` is what 5.04 applies, with nothing left over."""
    review = _review_with_every_finding()
    assert review["adjustments"], "the fixture stopped firing anything"
    record = apply_cohesion_adjustments(
        [{"transition_type": "defocus", "duration_frames": 30}],
        review)
    assert record["not_applied"] == [], (
        f"5.03 emitted an adjustment 5.04 will not apply: "
        f"{record['not_applied']}")
    assert len(record["applied"]) == len(review["adjustments"])


def test_the_findings_it_cannot_apply_are_still_reported():
    """Rescoping must not be a way to go quiet: both upstream-owned
    findings are still in `warnings` and in `observations`."""
    review = _review_with_every_finding()
    owners = {o["state_key"]: o for o in review["observations"]}
    assert set(owners) == {"sfx_spec", "speech_sequence"}
    for observation in owners.values():
        assert observation["finding"] in review["warnings"]
        assert observation["owner_step"]
        assert observation["reason"]
        assert observation["how_to_act"]


def test_an_observation_carries_no_suggested_value():
    """`"front_loaded"` was a word the step invented about an ordering it
    never computed - a creative value substituted for a decision no step
    made (AGENTS.md 10.5)."""
    review = _review_with_every_finding()
    assert all("suggested_value" not in o for o in review["observations"])


def test_the_review_never_reports_an_empty_applied_list():
    """`applied_adjustments` was always empty and always would be: the
    step emits only `cohesion_review`, so the mutations it recorded never
    left the process."""
    review = _review_with_every_finding()
    assert "applied_adjustments" not in review

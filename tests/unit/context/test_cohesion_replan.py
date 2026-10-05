"""A cohesion finding triggers a bounded re-plan of the owning step.

The loop: a finding that names an upstream owner is delivered to that
planner as a typed input, the planner re-runs for the affected region,
and the finding is re-checked.  The loop is bounded (one re-plan per
finding per run) and the editor can accept a finding as-is.

The defect this prevents: a cohesion finding ("the edit opens with the
passage ranked weakest") was reported to the captain as a note; nothing
sent it back to `speech_sequence` automatically.
"""
import pytest
from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion)
from library.tools import cohesion_replan
from library.tools.cohesion_replan import ReplanRequest


def _engagement(rank):
    return {"rank": rank, "basis": "a judgement of this passage"}


def _passage(clip_id, start, rank):
    return {
        "clip_id": clip_id,
        "source_start": start,
        "source_end": start + 3.0,
        "engagement": _engagement(rank),
    }


def _inputs(**kw):
    base = {
        "creative_direction": {"target_energy": "moderate"},
        "project_config": {"target_duration_seconds": 60},
        "transition_spec": [],
        "sfx_spec": [],
        "audio_spine": {"structure": [
            {"block_type": "speech", "position": 0,
             "timeline_start": 0.0, "timeline_end": 60.0}]},
    }
    base.update(kw)
    return base


def _speech_with_buried_peak():
    """The sequence opens on a passage the model ranked third and its
    strongest is last - two ORDERINGS disagreeing."""
    return {"body_sequence": [
        _passage("clip_002", 1.0, rank=3),
        _passage("clip_007", 20.0, rank=2),
        _passage("clip_016", 48.065, rank=1),
    ]}


def _speech_with_opening_peak():
    """The sequence opens on the strongest passage - no finding."""
    return {"body_sequence": [
        _passage("clip_016", 48.065, rank=1),
        _passage("clip_007", 20.0, rank=3),
        _passage("clip_009", 30.0, rank=2),
    ]}


def test_a_buried_peak_produces_a_replan_request():
    """The cohesion review emits a re-plan request for the finding."""
    review = review_creative_cohesion(
        _inputs(speech_sequence=_speech_with_buried_peak()))

    assert review["replan_requests"], (
        "the finding produced no re-plan request")
    req = review["replan_requests"][0]
    assert req["state_key"] == "speech_sequence"
    assert req["field"] == "segment_order"
    assert req["owner_step"] == "step_2_02_speech_sequence"
    assert "ranked strongest" in req["finding"]


def test_an_opening_peak_produces_no_replan_request():
    """No finding, no re-plan request."""
    review = review_creative_cohesion(
        _inputs(speech_sequence=_speech_with_opening_peak()))

    assert review["replan_requests"] == []


def test_the_replan_loop_closes_when_the_rerun_reorders():
    """The finding reaches speech_sequence, the re-run reorders, and
    the re-check passes.

    This is the fidelity test: given an edit whose opening passage is
    ranked weakest, the cohesion review produces a finding, the finding
    reaches `speech_sequence`, the re-run reorders, and the re-check
    passes.
    """
    state = {"speech_sequence": _speech_with_buried_peak()}

    # The finding the review produces.
    review = review_creative_cohesion(_inputs(**state))
    finding = review["replan_requests"][0]

    # The re-plan: deliver the finding to speech_sequence, which
    # re-orders the body_sequence so the strongest passage opens.
    def replan_fn(request, current_state):
        # Simulate speech_sequence re-ordering: the strongest passage
        # (rank 1) moves to the front.
        body = current_state["speech_sequence"]["body_sequence"]
        ranked = sorted(body, key=lambda p: p["engagement"]["rank"])
        current_state["speech_sequence"]["body_sequence"] = ranked
        return current_state

    # The re-check: run the cohesion review against the updated state.
    def recheck_fn(updated_state):
        review = review_creative_cohesion(_inputs(**updated_state))
        return len(review["replan_requests"]) == 0

    result = cohesion_replan.replan_from_finding(
        finding=finding,
        state=state,
        owner_step="step_2_02_speech_sequence",
        replan_fn=replan_fn,
        recheck_fn=recheck_fn,
    )

    assert result.triggered, "the re-plan was not triggered"
    assert result.resolved, (
        f"the finding was not resolved: {result.detail}")
    assert result.attempts == 1, "the loop is bounded at one re-plan"

    # The re-run really did reorder: the strongest passage opens.
    body = state["speech_sequence"]["body_sequence"]
    assert body[0]["clip_id"] == "clip_016"


def test_the_replan_loop_is_bounded_at_one_per_finding():
    """A finding that persists after its one re-plan is reported, not
    re-planned again."""
    state = {"speech_sequence": _speech_with_buried_peak()}

    review = review_creative_cohesion(_inputs(**state))
    finding = review["replan_requests"][0]

    # The re-plan does nothing - the finding persists.
    def replan_fn(request, current_state):
        return current_state

    # The re-check always fails.
    def recheck_fn(updated_state):
        return False

    result = cohesion_replan.replan_from_finding(
        finding=finding,
        state=state,
        owner_step="step_2_02_speech_sequence",
        replan_fn=replan_fn,
        recheck_fn=recheck_fn,
    )

    assert result.triggered
    assert not result.resolved
    assert not result.accepted
    assert result.attempts == 1, (
        "the loop re-planned more than once per finding")
    assert "persists" in result.detail


def test_the_editor_can_accept_a_finding_as_is():
    """A finding the re-plan cannot resolve can be accepted by the
    editor."""
    state = {"speech_sequence": _speech_with_buried_peak()}

    review = review_creative_cohesion(_inputs(**state))
    finding = review["replan_requests"][0]

    def replan_fn(request, current_state):
        return current_state

    def recheck_fn(updated_state):
        return False

    def accept_fn(request):
        return True

    result = cohesion_replan.replan_from_finding(
        finding=finding,
        state=state,
        owner_step="step_2_02_speech_sequence",
        replan_fn=replan_fn,
        recheck_fn=recheck_fn,
        accept_fn=accept_fn,
    )

    assert result.triggered
    assert not result.resolved
    assert result.accepted
    assert "editor accepted" in result.detail


def test_a_replan_request_is_typed():
    """The re-plan request is a typed input, not a note."""
    req = ReplanRequest(
        finding="the edit opens on the passage ranked weakest",
        state_key="speech_sequence",
        field="segment_order",
        owner_step="step_2_02_speech_sequence",
    )
    assert req.state_key == "speech_sequence"
    assert req.field == "segment_order"
    assert req.owner_step == "step_2_02_speech_sequence"
    assert req.region == "0.0-60.0"

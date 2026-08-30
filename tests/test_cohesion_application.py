"""Every cohesion adjustment is applied or refused out loud.

The applier honoured `transition_spec` entries carrying a `target_index`
and dropped everything else without a word, while creative_cohesion's own
`applied_adjustments` stayed empty - so the acting step and the recording
step both misdescribed what happened.
"""
from library.steps.step_5_04_compile_manifest.step import apply_cohesion_adjustments


def _review(*adjustments):
    return {"adjustments": list(adjustments)}


def test_a_transition_adjustment_is_applied_and_recorded():
    transitions = [{"transition_type": "defocus", "duration_frames": 15}]
    record = apply_cohesion_adjustments(transitions, _review({
        "target_step": "transition_spec",
        "field": "duration_frames",
        "suggested_value": 10,
        "target_index": 0,
    }))
    assert transitions[0]["duration_frames"] == 10
    assert record["applied"] == ["transition[0].duration_frames: 15 -> 10"]
    assert record["not_applied"] == []


def test_an_sfx_density_adjustment_is_refused_with_a_reason():
    """In the reference run this vanished silently."""
    record = apply_cohesion_adjustments([], _review({
        "target_step": "sfx_spec",
        "field": "density",
        "suggested_value": "dense",
    }))
    assert record["applied"] == []
    assert len(record["not_applied"]) == 1
    assert "step_4_04_plan_sfx" in record["not_applied"][0]["reason"]


def test_a_speech_reorder_is_refused_with_a_reason():
    record = apply_cohesion_adjustments([], _review({
        "target_step": "speech_sequence",
        "field": "segment_order",
        "suggested_value": "front_loaded",
    }))
    assert "step_2_02" in record["not_applied"][0]["reason"]


def test_an_out_of_range_target_index_is_refused_not_ignored():
    record = apply_cohesion_adjustments([], _review({
        "target_step": "transition_spec",
        "field": "duration_frames",
        "suggested_value": 10,
        "target_index": 7,
    }))
    assert "outside" in record["not_applied"][0]["reason"]


def test_an_unknown_target_step_is_refused_not_ignored():
    record = apply_cohesion_adjustments([], _review({
        "target_step": "something_new",
        "field": "whatever",
        "suggested_value": 1,
    }))
    assert record["not_applied"][0]["reason"]


def test_every_adjustment_is_accounted_for():
    adjustments = [
        {"target_step": "transition_spec", "field": "duration_frames",
         "suggested_value": 10, "target_index": 0},
        {"target_step": "sfx_spec", "field": "density",
         "suggested_value": "dense"},
        {"target_step": "speech_sequence", "field": "segment_order",
         "suggested_value": "front_loaded"},
        {"target_step": "transition_spec", "field": "duration_frames",
         "suggested_value": 10},
    ]
    record = apply_cohesion_adjustments(
        [{"duration_frames": 15}], _review(*adjustments))
    assert len(record["applied"]) + len(record["not_applied"]) == len(adjustments)


def test_no_review_is_an_empty_record():
    # `basis` is empty too: with no review there is nothing that could
    # have stated why the list is empty, and an invented basis would be
    # exactly the thing the field exists to stop.
    assert apply_cohesion_adjustments([], {}) == {
        "applied": [], "not_applied": [], "observed": [], "basis": {}}


def test_the_record_carries_why_the_review_asked_for_nothing():
    """An empty `applied` beside an empty `not_applied` is not a verdict.

    On every run this project has made, `adjustments` has been empty
    because the review proposed nothing - step 5.03 is a pure observer
    (#272) - and the manifest's record said only that nothing was
    applied. Which absence it is now travels with it.
    """
    from library.tools.cohesion_scope import adjustments_basis

    review = {"adjustments": [], "observations": [],
              "adjustments_basis": adjustments_basis([], [], [])}
    record = apply_cohesion_adjustments([], review)
    assert record["basis"]["basis"] == "no_proposal_was_made"
    assert "pure OBSERVER" in record["basis"]["means"]
    assert "no producer" in record["basis"]["channel_note"]


def test_the_review_step_names_who_applies_its_adjustments():
    from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion
    review = review_creative_cohesion({
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [],
        "sfx_spec": [],
    })
    assert review["applied_by"] == "step_5_04_compile_manifest"


def test_the_reviews_observations_reach_the_manifest_record():
    """A finding the compiler cannot act on still has to be visible in
    the manifest, or removing it from `adjustments` would just hide
    it somewhere quieter."""
    record = apply_cohesion_adjustments([], {
        "adjustments": [],
        "observations": [{
            "finding": "the strongest passage is not the hook",
            "state_key": "speech_sequence",
            "field": "segment_order",
            "owner_step": "step_2_02_speech_sequence",
        }],
    })
    assert record["observed"] == [{
        "finding": "the strongest passage is not the hook",
        "state_key": "speech_sequence",
        "field": "segment_order",
        "owner_step": "step_2_02_speech_sequence",
    }]


def test_the_refusal_reasons_come_from_the_one_enumeration():
    """The reason a reader is given at 5.03 and the reason logged at
    5.04 are the same string, or they drift."""
    from library.tools import cohesion_scope
    for (target, field), owner in cohesion_scope.OWNED_UPSTREAM.items():
        record = apply_cohesion_adjustments([], _review({
            "target_step": target, "field": field,
            "suggested_value": "whatever",
        }))
        assert record["applied"] == []
        assert record["not_applied"][0]["reason"] == owner.reason

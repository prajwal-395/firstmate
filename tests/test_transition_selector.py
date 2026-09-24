from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import NATIVE_TYPES, PLANNABLE_TYPES
from library.tools.native_ops import NativeTransitionRefused
import pytest


def test_same_source_clip_is_a_jump_cut():
    """A cut inside one take has no second angle to move to."""
    clip = {"clip_id": "clip_001"}
    res = select_transition(clip, dict(clip), {}, {})
    assert res["type"] == "jump_cut"
    assert res["duration_ms"] == 0


def test_a_scene_change_the_plan_did_not_decorate_is_a_hard_cut():
    """No request means nothing is drawn.

    This used to answer `defocus` once every twenty seconds, and
    `fade_to_black` or `flash` off the incoming block's type - taste
    chosen by a constant for a cut nobody asked to decorate. See
    `WITHDRAWN_SCENE_CHANGE_DEFAULTS`, and
    tests/test_no_creative_floors.py.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002", "timeline_start": 5.0},
        {"transition_duration_ms": 600}, {},
    )
    assert res["type"] == "hard_cut"
    assert res["duration_ms"] == 0


def test_no_signal_on_the_incoming_block_draws_a_transition():
    """Neither a block type nor a music behaviour invents one now."""
    for to_clip in (
        {"clip_id": "clip_002", "timeline_start": 25.0,
         "music_behavior": "step_up"},
        {"clip_id": "clip_002", "timeline_start": 25.0,
         "block_type": "breather"},
        {"clip_id": "clip_002", "timeline_start": 25.0,
         "block_type": "transition_slot"},
    ):
        res = select_transition({"clip_id": "clip_001"}, to_clip, {}, {})
        assert res["type"] == "hard_cut", to_clip


def test_an_explicit_request_outranks_the_heuristic():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="zoom_blur",
    )
    assert res["type"] == "zoom_blur"
    assert res["requested_type"] == "zoom_blur"
    assert res["downgrade_reason"] == ""


def test_a_granted_native_request_resolves_to_the_native_route():
    """`cross_dissolve` is drawn by Resolve itself, not downgraded."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="cross_dissolve",
    )
    assert res["type"] == "cross_dissolve"
    assert res["requested_type"] == "cross_dissolve"
    assert res["downgrade_reason"] == ""


def test_an_undrawable_request_becomes_a_hard_cut_with_a_reason():
    """Never quietly swapped for a different creative transition."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="wipe",
    )
    assert res["type"] == "hard_cut"
    assert res["requested_type"] == "wipe"
    assert "wipe" in res["downgrade_reason"]


def test_a_measured_refusal_is_refused_by_name_not_downgraded():
    """A whip that ships as a hard cut is a plan the picture disobeyed
    without saying so (measured empty on Resolve 21.1, 2026-09-24)."""
    with pytest.raises(NativeTransitionRefused, match="whip_pan"):
        select_transition(
            {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
            {}, {}, requested_type="whip_pan",
        )


def test_a_measured_refusal_ships_the_stated_fallback_only():
    """The nearest granted native transition ships only when the plan
    states it in `fallback_type` - never substituted by the engine."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="whip_pan",
        fallback_type="cross_dissolve",
    )
    assert res["type"] == "cross_dissolve"
    assert "fallback" in res["downgrade_reason"]


def test_an_aliased_request_resolves_to_its_canonical_type():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="Dip_To_Black",
    )
    assert res["type"] == "fade_to_black"


def test_brand_types_no_route_can_draw_are_rejected(capsys):
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["cut", "dissolve", "wipe"]},
        {"target_energy": "high"},
    )
    # "cut" and the native "dissolve" survive the filter; "wipe" is
    # rejected loudly. No request means nothing is drawn either way -
    # and none is invented.
    assert res["type"] == "hard_cut"
    err = capsys.readouterr().err
    assert "wipe" in err and "dissolve" not in err


def test_a_brand_allowing_a_drawn_type_still_does_not_draw_it_unasked():
    """The allow-list is a permission, not an instruction.

    The final line used to be `settle(preferred_types[0])`, so a brand
    template whose list happened to start with a drawn type got that type
    on every undecorated cut.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["defocus", "hard_cut"]}, {},
    )
    assert res["type"] == "hard_cut"


def test_a_brand_range_is_a_bound_and_not_a_length():
    """A brand file writes {min, max}, and a RANGE names no length.

    Answering `max` here is what overruled the plan: project 001's two
    drawn transitions were planned "quick" and "medium" and both were
    held for 500 ms. A range now yields bounds and no duration, so the
    plan's own `duration_feel` decides inside them.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": {"min": 200, "max": 500}}, {},
        requested_type="defocus",
    )
    assert res["duration_ms"] is None
    assert res["duration_bounds_ms"] == (200, 500)


def test_a_brand_scalar_is_a_declared_length():
    """One number is the template author saying how long, exactly."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": 600}, {}, requested_type="defocus",
    )
    assert res["duration_ms"] == 600
    assert res["duration_bounds_ms"] == (600, 600)


def test_no_brand_duration_invents_no_length():
    """`_resolve_duration_ms` ended `return default` with default=500."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"}, {}, {},
        requested_type="defocus",
    )
    assert res["duration_ms"] is None
    assert res["duration_bounds_ms"] == (None, None)


def test_every_outcome_is_a_drawable_type():
    cases = [
        ({}, {}, ""),
        ({"transition_types": ["macro", "light_leak"]}, {}, "j_cut"),
        ({"transition_types": []}, {"target_energy": "calm"}, "nonsense_type"),
        ({}, {}, "cross_dissolve"),
    ]
    for brand, creative, requested in cases:
        res = select_transition(
            {"clip_id": "a"}, {"clip_id": "b"}, brand, creative,
            requested_type=requested,
        )
        assert res["type"] in PLANNABLE_TYPES + NATIVE_TYPES, res


def test_a_measured_refusal_never_lands_in_the_outcome_set():
    """`whip_pan` is not an outcome at all - it refuses by name."""
    with pytest.raises(NativeTransitionRefused):
        select_transition(
            {"clip_id": "a"}, {"clip_id": "b"},
            {"transition_types": ["macro", "light_leak"]},
            {"target_energy": "high"}, requested_type="whip_pan",
        )


from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import PLANNABLE_TYPES


def test_same_source_clip_is_a_jump_cut():
    """A cut inside one take has no second angle to move to."""
    clip = {"clip_id": "clip_001"}
    res = select_transition(clip, dict(clip), {}, {})
    assert res["type"] == "jump_cut"
    assert res["duration_ms"] == 0


def test_scene_change_defaults_to_hard_cut_if_under_cap():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002", "timeline_start": 5.0},
        {"transition_duration_ms": 600}, {}, last_drawn_time=0.0
    )
    assert res["type"] == "hard_cut"
    assert res["duration_ms"] == 0


def test_music_step_up_yields_flash():
    """`music_behavior` step up gives a flash."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002", "timeline_start": 25.0, "music_behavior": "step_up"},
        {}, {},
    )
    assert res["type"] == "flash"


def test_an_explicit_request_outranks_the_heuristic():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="zoom_blur",
    )
    assert res["type"] == "zoom_blur"
    assert res["requested_type"] == "zoom_blur"
    assert res["downgrade_reason"] == ""


def test_an_undrawable_request_becomes_a_hard_cut_with_a_reason():
    """Never quietly swapped for a different creative transition."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="cross_dissolve",
    )
    assert res["type"] == "hard_cut"
    assert res["requested_type"] == "cross_dissolve"
    assert "cross_dissolve" in res["downgrade_reason"] or "mix" in res["downgrade_reason"]


def test_an_aliased_request_resolves_to_its_canonical_type():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="Dip_To_Black",
    )
    assert res["type"] == "fade_to_black"


def test_brand_types_the_renderer_cannot_draw_are_rejected(capsys):
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["cut", "dissolve", "wipe"]},
        {"target_energy": "high"},
    )
    # Only "cut" survives the filter, so there is no creative transition
    # this brand permits - and none is invented.
    assert res["type"] == "hard_cut"
    err = capsys.readouterr().err
    assert "dissolve" in err and "wipe" in err


def test_brand_duration_accepts_the_min_max_shape():
    """default_brand.yaml writes {min, max}; every reader wanted a scalar.

    Wiring the brand template in without this raised a TypeError on the
    first non-cut transition.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": {"min": 200, "max": 500}}, {},
    )
    assert res["duration_ms"] == 500


def test_every_outcome_is_a_plannable_type():
    cases = [
        ({}, {}, ""),
        ({"transition_types": ["macro", "light_leak"]}, {}, "whip_pan"),
        ({}, {"target_energy": "high"}, "j_cut"),
        ({"transition_types": []}, {"target_energy": "calm"}, "nonsense_type"),
    ]
    for brand, creative, requested in cases:
        res = select_transition(
            {"clip_id": "a"}, {"clip_id": "b"}, brand, creative,
            requested_type=requested,
        )
        assert res["type"] in PLANNABLE_TYPES, res

def test_drawn_transition_capped_per_20_seconds():
    # First one at 20s should draw
    res1 = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002", "timeline_start": 20.0, "block_type": "breather"},
        {}, {},
    )
    assert res1["type"] == "fade_to_black"

    # Second one at 25s should fall back to hard cut (under 20s cap from last drawn time at 0, well wait)
    # The last_drawn_time must be passed explicitly to test it
    res2 = select_transition(
        {"clip_id": "clip_002"}, {"clip_id": "clip_003", "timeline_start": 25.0, "block_type": "breather"},
        {}, {}, last_drawn_time=20.0
    )
    assert res2["type"] == "hard_cut"

    # Third one at 41s should draw again
    res3 = select_transition(
        {"clip_id": "clip_003"}, {"clip_id": "clip_004", "timeline_start": 41.0, "block_type": "breather"},
        {}, {}, last_drawn_time=20.0
    )
    assert res3["type"] == "fade_to_black"

from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import PLANNABLE_TYPES


def test_same_source_clip_is_a_jump_cut():
    """A cut inside one take has no second angle to move to."""
    clip = {"clip_id": "clip_001"}
    res = select_transition(clip, dict(clip), {}, {})
    assert res["type"] == "jump_cut"
    assert res["duration_ms"] == 0


def test_scene_change_takes_a_drawable_transition():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": 600}, {"target_energy": "reflective"},
    )
    assert res["type"] == "defocus"
    assert res["duration_ms"] == 600


def test_high_energy_scene_change_reads_target_energy():
    """`energy` was never a creative_direction key; `target_energy` is.

    The old code read `energy`, so the energy-driven branch could not fire
    on any real run.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {"target_energy": "high, building to a frantic peak"},
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

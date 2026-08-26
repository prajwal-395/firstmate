from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import PLANNABLE_TYPES


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


def test_brand_duration_accepts_the_min_max_shape():
    """default_brand.yaml writes {min, max}; every reader wanted a scalar.

    Wiring the brand template in without this raised a TypeError on the
    first non-cut transition.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": {"min": 200, "max": 500}}, {},
        requested_type="defocus",
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

def test_the_withdrawn_scene_change_defaults_are_recorded():
    """A withdrawal is only visible if the reason is written down.

    The one-per-twenty-seconds cap that used to live here is gone with
    the thing it capped: there is nothing left to rate-limit, because
    nothing is drawn unasked.
    """
    from library.tools.transition_selector import (
        WITHDRAWN_SCENE_CHANGE_DEFAULTS,
    )

    assert set(WITHDRAWN_SCENE_CHANGE_DEFAULTS) == {
        "flash", "fade_to_black", "defocus",
    }
    for name, reason in WITHDRAWN_SCENE_CHANGE_DEFAULTS.items():
        assert reason.strip(), f"{name} is withdrawn with no reason"

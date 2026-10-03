"""User instructions, rather than measurements or model suggestions, own
the scope that can reach Resolve stabilization.
"""

from library.tools.stabilization_authorization import (
    authorizations_from_timeline_notes,
    scope_matches_clip,
)


def test_shaky_measurements_select_only_targets_after_an_explicit_request():
    note = {"note_id": "note-7",
            "typed": "Please stabilize the shaky footage"}
    authorization, = authorizations_from_timeline_notes(
        {"notes": [note]})
    assert authorization["scope"] == {"kind": "shaky_footage"}

    shaky = {"assessment": {"camera_stability": "unstable"}}
    stable = {"assessment": {"camera_stability": "stable"}}
    assert scope_matches_clip(authorization, {}, semantic_document=shaky)
    assert not scope_matches_clip(authorization, {}, semantic_document=stable)

    observation = {"note_id": "note-8",
                   "typed": "The shaky footage is distracting"}
    assert authorizations_from_timeline_notes(
        {"notes": [observation]}) == []


def test_request_after_context_still_counts_but_a_question_does_not():
    requested, = authorizations_from_timeline_notes({"notes": [{
        "note_id": "note-12",
        "typed": "The handheld shot looks rough. Please stabilize it",
    }]})
    assert requested["scope"] == {"kind": "shaky_footage"}

    assert authorizations_from_timeline_notes({"notes": [{
        "note_id": "note-13",
        "typed": "Why did this clip stabilize? The shot looks shaky",
    }]}) == []


def test_whole_video_request_covers_every_picture_item():
    authorization, = authorizations_from_timeline_notes({"notes": [{
        "note_id": "note-9",
        "typed": "Please stabilize the whole video",
    }]})
    assert authorization["scope"]["kind"] == "whole_video"
    assert scope_matches_clip(authorization, {"label": "speech_1"})
    assert scope_matches_clip(authorization, {"label": "broll_2"})


def test_timeline_span_request_does_not_escape_its_interval():
    authorization, = authorizations_from_timeline_notes({"notes": [{
        "note_id": "note-10",
        "typed": "Please stabilize this span",
        "attached_to": "moment",
        "at_region": "10.000..12.000",
        "on_timeline": "Main Timeline",
    }]})
    assert scope_matches_clip(
        authorization,
        {"timeline_in": 10.0, "timeline_out": 12.0},
        timeline_name="Main Timeline",
    )
    assert not scope_matches_clip(
        authorization,
        {"timeline_in": 9.0, "timeline_out": 11.0},
        timeline_name="Main Timeline",
    )
    assert not scope_matches_clip(
        authorization,
        {"timeline_in": 10.0, "timeline_out": 11.0},
        timeline_name="Another Timeline",
    )
    assert not scope_matches_clip(
        authorization,
        {"timeline_in": 10.0, "timeline_out": 11.0},
    )


def test_edit_request_provenance_keeps_the_original_note():
    authorization, = authorizations_from_timeline_notes({"notes": [{
        "note_id": "edit-11",
        "typed": "Apply stabilization to clip_001",
        "typed_operations": [{"owner": "plan_vfx", "op": "visual_effect"}],
    }]}, [{"clip_id": "clip_001", "filename": "camera.mov"}])
    assert authorization["source"] == "edit_request"
    assert authorization["authorization_id"] == "edit-11"
    assert authorization["instruction"] == "Apply stabilization to clip_001"
    assert authorization["scope"]["clip_id"] == "clip_001"

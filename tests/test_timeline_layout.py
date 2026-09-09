"""The timeline layout has one owner, and row counts come from the material.

Defects covered here:
  1. two speakers/angles collapsed onto one video row,
  4. blank video rows created from what MIGHT be placed,
  6. rows not labeled.

`library/tools/timeline_layout.py` takes the material (which angles
exist, what the plan asks for) and returns the track plan. Nothing else
may decide a track index or a track name - the two hardcoded dicts that
lived at the end of `resolve_build_timeline.py` died here.
"""

import pytest

from library.tools.timeline_layout import (
    allocate_non_overlapping_rows,
    plan_layout,
)


def _two_angle_material(**over):
    material = {
        "angles": [
            {"key": "akshita", "label": "Akshita", "speech_name": "Akshita CH1",
             "program_channel": 1},
            {"key": "craig", "label": "Craig", "speech_name": "Craig CH1",
             "program_channel": 1},
        ],
        "has_broll": False,
        "caption_spans": [],
        "mg_spans": [],
        "has_generators": False,
        "timed_text_spans": [],
        "music_spans": [],
        "sfx_spans": [],
    }
    material.update(over)
    return material


def test_two_angles_get_two_picture_rows_and_two_speech_rows():
    """Defect 1: each camera angle is its own row, picture and speech."""
    plan = plan_layout(_two_angle_material())

    video_roles = [(t.index, t.role, t.name) for t in plan.video_tracks]
    assert video_roles == [(1, "a_roll", "Akshita"), (2, "a_roll", "Craig")]

    audio_roles = [(t.index, t.role, t.name) for t in plan.audio_tracks]
    assert audio_roles == [(1, "speech", "Akshita CH1"),
                           (2, "speech", "Craig CH1")]


def test_a_row_exists_because_something_goes_on_it():
    """Defect 4: no b-roll, captions, music or SFX asked for, none planned."""
    plan = plan_layout(_two_angle_material())

    roles = [t.role for t in plan.video_tracks + plan.audio_tracks]
    assert roles == ["a_roll", "a_roll", "speech", "speech"]
    assert len(plan.video_tracks) == 2
    assert len(plan.audio_tracks) == 2


def test_single_angle_keeps_the_legacy_shape():
    """A manifest that declares no angles builds exactly V1/A1 as before."""
    plan = plan_layout({
        "angles": [],
        "has_broll": False,
        "caption_spans": [],
        "mg_spans": [],
        "has_generators": False,
        "timed_text_spans": [],
        "music_spans": [],
        "sfx_spans": [],
    })
    assert [(t.index, t.role) for t in plan.video_tracks] == [(1, "a_roll")]
    assert [(t.index, t.role) for t in plan.audio_tracks] == [(1, "speech")]


def test_row_order_is_fixed_and_counts_come_from_material():
    """Picture rows, then captions, then decoration; speech, then bed, then SFX."""
    plan = plan_layout(_two_angle_material(
        has_broll=True,
        caption_spans=[(0, 100)],
        mg_spans=[(0, 50), (25, 75)],
        has_generators=True,
        timed_text_spans=[(0, 10)],
        music_spans=[(0, 200)],
        sfx_spans=[(0, 50), (25, 75), (100, 150)],
    ))
    video_roles = [t.role for t in plan.video_tracks]
    assert video_roles == ["a_roll", "a_roll", "b_roll", "captions",
                           "motion_graphics", "motion_graphics",
                           "generators", "timed_text"]
    assert [t.index for t in plan.video_tracks] == [1, 2, 3, 4, 5, 6, 7, 8]
    audio_roles = [t.role for t in plan.audio_tracks]
    assert audio_roles == ["speech", "speech", "music", "sfx", "sfx"]
    assert [t.index for t in plan.audio_tracks] == [1, 2, 3, 4, 5]


def test_overlapping_sfx_layers_stack_and_sequential_ones_share():
    """SFX is one row or many depending on how layered the sound is."""
    one = allocate_non_overlapping_rows([(0, 50), (60, 100)], base_index=3)
    assert sorted({row for _, row in one}) == [3]
    two = allocate_non_overlapping_rows([(0, 50), (25, 75)], base_index=3)
    assert sorted({row for _, row in two}) == [3, 4]


def test_names_come_from_the_material_never_a_constant():
    """Defect 6: every row is named, from the angle or the declared role."""
    plan = plan_layout(_two_angle_material(has_broll=True))
    for track in plan.video_tracks + plan.audio_tracks:
        assert track.name, f"track {track.index} has no name"
    assert plan.video_row_for_angle("akshita").name == "Akshita"
    assert plan.speech_row_for_angle("craig").name == "Craig CH1"
    assert plan.video_row_for_angle("nobody") is None


def test_two_picture_rows_survive_the_tv_frame_look():
    """Captain's ruling on Reel 09 (marker at frame 1674): the a-roll
    row must be two rows, one per speaker, the way the two speech rows
    already are. The frame row dresses the picture above the captions -
    it does not collapse the picture to make room for itself."""
    plan = plan_layout(_two_angle_material(
        has_frame=True,
        caption_spans=[(0, 200)],
        has_transitions=True,
        has_explainer=True,
        has_semantic=True,
    ))
    assert [(t.index, t.role, t.name) for t in plan.video_tracks] == [
        (1, "a_roll", "Akshita"), (2, "a_roll", "Craig"),
        (3, "frame", "Frame"), (4, "captions", "Subtitles"),
        (5, "transitions", "Transitions"), (6, "explainer", "Explainer"),
        (7, "semantic", "Semantic")]
    assert [(t.index, t.role, t.name) for t in plan.audio_tracks] == [
        (1, "speech", "Akshita CH1"), (2, "speech", "Craig CH1")]


def test_collapse_picture_is_gone_and_cannot_refire():
    """`collapse_picture` was the TV-frame rule PR 830 reasoned into the
    plan; the captain overruled it. The key is deleted, and a stale
    caller still passing it gets two picture rows anyway - no rule may
    quietly re-collapse the rows the next time a TV-frame look is
    declared."""
    plan = plan_layout(_two_angle_material(
        has_frame=True, collapse_picture=True))
    assert [t.name for t in plan.aroll_rows()] == ["Akshita", "Craig"]


def test_the_builders_hardcoded_layout_is_gone():
    """Defects 1/4/6, structurally: the track-index and track-name dicts
    that lived at the end of the old build decided every row as a
    constant. They must not exist anywhere in the master builder
    anymore. (Pool subfolder names like "Subtitles" are media
    organisation, not track layout, and are not covered here.)"""
    from pathlib import Path
    src = Path("library/steps/step_6_01_render/resolve_build_timeline.py").read_text()
    for remnant in ("video_labels = {", "audio_labels = {",
                    'f"SFX-{i - 2}"', "target_video_tracks"):
        assert remnant not in src, f"hardcoded layout remnant {remnant} still in builder"

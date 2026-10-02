"""A real assembly manifest carrying a multi-track, multi-section bed.

The end-to-end half of `tests/test_music_bed.py`: the REAL
`compile_manifest` compiles a real spine into a real manifest, and what is
asserted is A2 as the renderer will read it - several clips, from two
files, overlapping where a crossfade was declared - plus the volume ramps
`otio_mix` writes across each splice.

It also holds the DELIVERY end: the renderer's own track allocator puts
two overlapping bed clips on two lanes, because two clips cannot share one
Resolve audio track, and pushes SFX above whatever the bed used.
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from library.steps.step_5_04_compile_manifest.step import (  # noqa: E402
    compile_manifest,
)
from library.tools.otio_mix import (  # noqa: E402
    MIN_VOLUME_DB, mix_targets,
)


@pytest.fixture
def manifest(tmp_path):
    aroll = str(tmp_path / "aroll.mov")
    broll = str(tmp_path / "broll.mov")
    one = str(tmp_path / "one.wav")
    two = str(tmp_path / "two.wav")
    for path in (aroll, broll, one, two):
        with open(path, "w", encoding="utf-8") as f:
            f.write("dummy")

    blocks = [
        {"block_type": "speech", "position": 1, "clip_id": "clip_1",
         "source_start": 0.117, "source_end": 10.117, "timeline_start": 0.0,
         "timeline_end": 10.0, "music_behavior": "background",
         "content": {"clip_id": "clip_1", "link_group_id": "lg_1"}},
        {"block_type": "transition_slot", "position": 2, "clip_id": None,
         "source_start": None, "source_end": None, "timeline_start": 10.0,
         "timeline_end": 16.0, "music_behavior": "prominent", "content": {}},
        {"block_type": "speech", "position": 3, "clip_id": "clip_1",
         "source_start": 20.317, "source_end": 32.317, "timeline_start": 16.0,
         "timeline_end": 28.0, "music_behavior": "background",
         "content": {"clip_id": "clip_1", "link_group_id": "lg_2"}},
    ]

    inputs = {
        "a_roll_assignments": [
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": aroll, "video_in": 0.117, "video_out": 10.117,
             "timeline_start": 0.0, "timeline_end": 10.0},
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": aroll, "video_in": 20.317, "video_out": 32.317,
             "timeline_start": 16.0, "timeline_end": 28.0},
        ],
        "b_roll_assignments": [
            {"spine_block_position": 2, "block_type": "transition_slot",
             "clip_id": "clip_2", "source_file": broll,
             "video_in": 0.213, "video_out": 6.213, "duration_seconds": 6.0,
             "timeline_start": 10.0, "timeline_end": 16.0,
             "video_only": True},
        ],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []}, "transition_spec": [],
        "enhancement_spec": [], "color_grade_spec": {},
        "audio_mix_spec": {
            "track_levels": {"A2_music": {"fade_duration_seconds": 1.0}},
            "music_automation": [
                {"spine_block_position": b["position"],
                 "timeline_start": b["timeline_start"],
                 "timeline_end": b["timeline_end"],
                 "music_behavior": b["music_behavior"],
                 "target_level_db": -18 if b["music_behavior"] == "background"
                                    else -6}
                for b in blocks
            ],
        },
        "sfx_spec": [{
            "label": "sfx_001", "sfx_id": "camera soft click.wav",
            "source_file": str(tmp_path / "click.wav"),
            "source_in": 0.0, "timeline_in": 12.0, "timeline_out": 12.459,
            "volume_db": -4,
        }],
        # ── The whole point: two tracks, three pieces, one crossfade ──
        "music_selection": {
            "title": "One", "audio_path": one, "duration_seconds": 300.0,
            "tracks": [{"title": "Two", "audio_path": two,
                        "duration_seconds": 240.0}],
            "splices": [
                {"intended_use": "the sparse opening", "source_in": 12.0,
                 "source_out": 22.0},
                {"track": "Two", "intended_use": "the lift under the cutaway",
                 "source_in": 90.0, "source_out": 96.0},
                {"intended_use": "the settled body", "source_in": 180.0,
                 "source_out": 192.0},
            ],
        },
        "audio_spine": {
            "frame_rate": 30.0,
            "structure": blocks,
            "music_bed": [
                {"source_in": 12.0, "why": "the sparse opening sits under "
                                           "the first passage"},
                {"track": "Two", "source_in": 90.0, "starts_at_block": 2,
                 "crossfade_seconds": 1.5,
                 "why": "the cutaway wants the other track's lift"},
                {"source_in": 180.0, "starts_at_block": 3,
                 "crossfade_seconds": 1.0,
                 "why": "back to the first track, at its settled body"},
            ],
        },
        "clip_catalog": [
            {"clip_id": "clip_1", "path": aroll, "width": 1080, "height": 1920},
            {"clip_id": "clip_2", "path": broll, "width": 1080, "height": 1920},
        ],
        "semantic_analysis": {"semantic_analysis_documents": [
            {"clip_id": "clip_1",
             "analysis": {"motion": "Locked off.", "scene": "A speaker."},
             "assessment": {"clip_type": "a-roll"}}]},
    }
    with open(str(tmp_path / "click.wav"), "w", encoding="utf-8") as f:
        f.write("dummy")

    # A resolved selection owes its audit sidecar: the pre-render check
    # refuses without it, so the fixture stages what 2.04's post-bridge
    # writes on a real run. The compile reads the project root off
    # `out_dir`, hence the output directory under tmp_path.
    from library.tools import music_audit_trail as audit
    audit.write_audit_trail(str(tmp_path), inputs["music_selection"])

    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        return (compile_manifest(str(tmp_path / "pipeline_output")),
                one, two)


def test_the_manifest_carries_a_multi_track_spliced_bed(manifest):
    built, one, two = manifest
    clips = built["tracks"]["A2"]["clips"]
    assert len(clips) == 3, "the bed is three pieces"
    assert [os.path.basename(c["source_file"]) for c in clips] == [
        "one.wav", "two.wav", "one.wav"]
    assert [c["source_in"] for c in clips] == [12.0, 90.0, 180.0]
    # Disjoint sections of ONE track, in one bed: 12s and 180s of one.wav.
    assert clips[0]["source_file"] == clips[2]["source_file"]
    assert clips[0]["source_in"] != clips[2]["source_in"]
    # Every declared crossfade is a real overlap.
    for outgoing, incoming in zip(clips, clips[1:]):
        assert incoming["crossfade_in_seconds"] > 0
        assert outgoing["timeline_out"] > incoming["timeline_in"], (
            "a crossfade needs the two pieces to play at once")
        assert round(outgoing["timeline_out"] - incoming["timeline_in"], 3) \
            == incoming["crossfade_in_seconds"]


def test_the_ramps_across_each_splice_reach_the_mix(manifest):
    built, _, _ = manifest
    targets = [t for t in mix_targets(built, fps=30.0) if t["role"] == "music"]
    assert len(targets) == 3
    # Piece one fades OUT at its tail; pieces two and three fade IN at
    # their head; the level between the ramps is the block's own planned
    # level and nothing here revises it.
    assert targets[0]["keyframes"][max(targets[0]["keyframes"])] == MIN_VOLUME_DB
    for target in targets[1:]:
        assert target["keyframes"][0] == MIN_VOLUME_DB
    assert -6.0 in targets[1]["keyframes"].values(), (
        "the cutaway block planned `prominent`, and the piece over it "
        "carries that level between its ramps")


def test_the_renderer_puts_the_overlapping_pieces_on_separate_lanes(manifest):
    """Two clips cannot share one Resolve audio track."""
    from library.steps.step_6_01_render.resolve_build_timeline import (
        _allocate_audio_tracks,
    )

    built, _, _ = manifest
    allocations = _allocate_audio_tracks(
        built["tracks"]["A2"]["clips"], base_track_index=2, fps=30.0)
    lanes = [t for _, t in allocations]
    assert lanes == [2, 3, 2], (
        "an overlapping piece goes on a lane of its own, and the lane is "
        "reused once it is free")
    sfx = _allocate_audio_tracks(
        built["tracks"]["A3"]["clips"], base_track_index=max(lanes) + 1,
        fps=30.0)
    assert all(t > max(lanes) for _, t in sfx), (
        "SFX start above whatever the bed used")

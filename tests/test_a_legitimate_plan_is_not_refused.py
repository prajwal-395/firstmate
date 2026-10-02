"""Two refusals in `compile_manifest` that killed runs over correct plans.

A visual effect on a B-roll block lands on the V2 clip (or is DROPPED
with the reason when nothing is there), and two DIFFERENT sounds layered
at one span compile onto separate lanes. History:
docs/evidence/legitimate_plan_refusals.md
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.steps.step_5_04_compile_manifest.step import compile_manifest
from library.tools.vfx_carriers import (
    NO_PICTURE, ON_V1, ON_V2,
    picture_carriers,
)


@pytest.fixture
def media(tmp_path):
    """Four files under tmp_path.  A test never reaches a real project."""
    paths = {}
    for name in ("aroll.mov", "broll.mov", "riser.wav", "whoosh.wav"):
        p = tmp_path / name
        p.write_text("dummy")
        paths[name] = str(p)
    return paths


def _inputs(media):
    """speech(0-6) | transition_slot(6-9, cutaway on V2) | speech(9-15)."""
    return {
        "a_roll_assignments": [
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": media["aroll.mov"],
             "video_in": 0.117, "video_out": 6.043,
             "timeline_start": 0.0, "timeline_end": 6.0},
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": media["aroll.mov"],
             "video_in": 10.213, "video_out": 16.139,
             "timeline_start": 9.0, "timeline_end": 15.0},
        ],
        "b_roll_assignments": [
            {"spine_block_position": 2, "block_type": "transition_slot",
             "clip_id": "clip_2", "source_file": media["broll.mov"],
             "video_in": 1.204, "video_out": 4.086,
             "duration_seconds": 3.0,
             "timeline_start": 6.0, "timeline_end": 9.0,
             "video_only": True},
        ],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "enhancement_spec": [],
        "color_grade_spec": {},
        "audio_mix_spec": {},
        "music_selection": {},
        "audio_spine": {
            "frame_rate": 30.0,
            "structure": [
                {"block_type": "speech", "position": 1, "clip_id": "clip_1",
                 "source_start": 0.117, "source_end": 6.043,
                 "timeline_start": 0.0, "timeline_end": 6.0,
                 "content": {"clip_id": "clip_1"}},
                {"block_type": "transition_slot", "position": 2,
                 "clip_id": None, "source_start": None, "source_end": None,
                 "timeline_start": 6.0, "timeline_end": 9.0, "content": {}},
                {"block_type": "speech", "position": 3, "clip_id": "clip_1",
                 "source_start": 10.213, "source_end": 16.139,
                 "timeline_start": 9.0, "timeline_end": 15.0,
                 "content": {"clip_id": "clip_1"}},
            ],
        },
        "clip_catalog": [
            {"clip_id": "clip_1", "path": media["aroll.mov"],
             "width": 1080, "height": 1920},
            {"clip_id": "clip_2", "path": media["broll.mov"],
             "width": 1080, "height": 1920},
        ],
        "semantic_analysis": {"semantic_analysis_documents": [
            {"clip_id": "clip_1",
             "analysis": {"motion": "Locked off on a tripod.",
                          "scene": "A speaker on a city street."},
             "assessment": {"clip_type": "a-roll"}},
            {"clip_id": "clip_2",
             "analysis": {"motion": "Locked off on a tripod.",
                          "scene": "A city street."},
             "assessment": {"clip_type": "b-roll"}},
        ]},
    }


def _compile(inputs):
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        return compile_manifest("dummy")


def _inputs_with_slot(media, slot_type):
    """The same spine with the middle block re-typed.

    The report's reproduction names `transition_slot or outro block`: a
    pacing `outro` reaches V1 no more than a transition_slot does, so the
    same compile path decides it - V2 when a cutaway covers it, a
    recorded drop when nothing does.
    """
    inputs = _inputs(media)
    inputs["b_roll_assignments"][0]["block_type"] = slot_type
    inputs["audio_spine"]["structure"][1]["block_type"] = slot_type
    return inputs


def _vfx(position, timeline_start, timeline_end):
    return {
        "enhancement_spec": {
            "visual_effects": [{
                "vfx_id": "vfx_001",
                "target_block_position": position,
                "timeline_start": timeline_start,
                "timeline_end": timeline_end,
                "effect_type": "slow_zoom_in",
                "params": {"zoom_start": 1.0, "zoom_end": 1.08},
                "rationale": "a slow drift on a stationary, stable shot",
            }],
            "planning_basis": {"basis": "planned", "proposed": 1,
                               "resolved": 1, "dropped": []},
        }
    }


# ── D1: an effect on a B-roll block ───────────────────────────────────

def test_an_effect_on_a_cutaway_block_reaches_the_v2_clip(media):
    """The reproduction: it used to raise; now the comp lands on the
    cutaway, for a `transition_slot` and a pacing `outro` alike (neither
    puts a clip on V1), and the Fusion pass really visits that label."""
    from library.tools.execution.fusion_tracks import reachable_effect_labels

    for slot_type in ("transition_slot", "outro"):
        inputs = _inputs_with_slot(media, slot_type)
        inputs.update(_vfx(2, 6.0, 9.0))

        manifest = _compile(inputs)

        per_clip = manifest["fusion_effects"]["per_clip"]
        v2_labels = {c["label"] for c in manifest["tracks"]["V2"]["clips"]}
        assert set(per_clip) & v2_labels, (
            f"{slot_type}: the effect reached no V2 clip: "
            f"per_clip={per_clip}, V2={v2_labels}")
        label = next(iter(set(per_clip) & v2_labels))
        assert per_clip[label]["_preset"] == "slow_zoom_in"
        assert per_clip[label]["zoom_end"] == 1.08
        # It survived as a planned effect, not as a casualty.
        assert len(manifest["vfx"]) == 1
        assert manifest["vfx_planning_basis"]["basis"] == "planned"
        assert manifest["vfx_planning_basis"]["dropped"] == []
        assert set(per_clip) <= reachable_effect_labels(manifest)


def test_an_effect_over_nothing_is_dropped_with_a_reason(media):
    """The bar: a genuinely undeliverable entry still does not pass silently."""
    inputs = dict(_inputs(media))
    inputs.update(_vfx(9, 400.0, 402.0))

    manifest = _compile(inputs)

    assert manifest["fusion_effects"]["per_clip"] == {}
    assert manifest["vfx"] == []
    basis = manifest["vfx_planning_basis"]
    # `every_entry_dropped` is the absence of a decision, and is spelled
    # differently from `no_effects_planned` on purpose.
    assert basis["basis"] == "every_entry_dropped"
    assert len(basis["dropped"]) == 1
    drop = basis["dropped"][0]
    assert drop["reason"] == "no_clip_at_that_position"
    assert drop["effect_type"] == "slow_zoom_in"
    assert "no clip on V1 or V2" in drop["detail"]




def test_an_effect_on_a_v1_block_is_unmoved(media):
    """V1 is asked first, so every existing placement stays where it was."""
    inputs = dict(_inputs(media))
    inputs.update(_vfx(1, 0.0, 6.0))

    manifest = _compile(inputs)

    per_clip = manifest["fusion_effects"]["per_clip"]
    v1_labels = {c["label"] for c in manifest["tracks"]["V1"]["clips"]}
    assert set(per_clip) <= v1_labels


# ── D1, the other half: the planner is told which track it is on ──────

def test_the_candidate_table_states_the_track_per_block(media):
    """The `cuts_toon` shape, for the step that had no such column."""
    from library.steps.step_4_03_plan_vfx.bridge import build_vfx_candidates

    inputs = _inputs(media)
    rows = build_vfx_candidates({
        "timed_spine": inputs["audio_spine"],
        "a_roll_assignments": inputs["a_roll_assignments"],
        "b_roll_assignments": inputs["b_roll_assignments"],
        "clip_catalog": inputs["clip_catalog"],
        "semantic_analysis_documents":
            inputs["semantic_analysis"]["semantic_analysis_documents"],
    })

    by_position = {r["segment_id"]: r for r in rows}
    assert by_position[1]["picture_track"] == ON_V1
    assert by_position[2]["picture_track"] == ON_V2
    assert by_position[3]["picture_track"] == ON_V1
    # The basis names the clip the effect would draw on, not just a track.
    assert "clip_2" in by_position[2]["track_basis"]
    for row in rows:
        assert row["track_basis"], f"block {row['segment_id']} states no basis"


def test_a_block_with_no_picture_at_all_says_so():
    """The one reading that means an effect cannot be drawn."""
    rows = picture_carriers(
        [{"block_type": "transition_slot", "position": 1,
          "timeline_start": 0.0, "timeline_end": 2.0}],
        b_roll_assignments=[],
    )
    assert rows[0]["track"] == NO_PICTURE
    assert rows[0]["clip_id"] is None
    assert "no picture to draw an effect on" in rows[0]["basis"]

    # A cutaway over a speech block: the effect still lands on V1, and
    # the table says what is over it.
    rows = picture_carriers(
        [{"block_type": "speech", "position": 1, "clip_id": "clip_1",
          "timeline_start": 0.0, "timeline_end": 4.0}],
        b_roll_assignments=[{"spine_block_position": 1, "clip_id": "clip_9",
                             "timeline_start": 0.0, "timeline_end": 4.0}],
    )
    assert rows[0]["track"] == ON_V1
    assert "clip_9" in rows[0]["basis"]
    assert "behind the cutaway" in rows[0]["basis"]




# ── D2: two sounds layered at one span ────────────────────────────────

def _sfx(media, entries):
    return {"sfx_spec": {"sfx": entries}}


def test_two_sounds_at_one_span_compile_and_reach_separate_tracks(media):
    """The reproduction, end to end: the plan compiles and the builder
    gives the two layers their own physical tracks."""
    from library.steps.step_6_01_render.resolve_build_timeline import (
        _allocate_audio_tracks,
    )

    inputs = dict(_inputs(media))
    inputs.update(_sfx(media, [
        {"label": "sfx_001", "sfx_id": "impact",
         "source_file": media["whoosh.wav"],
         "timeline_in": 1.5, "timeline_out": 2.2, "volume_db": -8.0},
        # The riser and the whoosh the sound editor layered on block 2,
        # each trimmed to the block's own length - the obvious thing to
        # do, and what used to kill the run.
        {"label": "sfx_004", "sfx_id": "riser",
         "source_file": media["riser.wav"],
         "timeline_in": 6.0, "timeline_out": 9.0, "volume_db": -10.0},
        {"label": "sfx_005", "sfx_id": "whoosh",
         "source_file": media["whoosh.wav"],
         "timeline_in": 6.0, "timeline_out": 9.0, "volume_db": -12.0},
    ]))

    manifest = _compile(inputs)

    a3 = manifest["tracks"]["A3"]["clips"]
    assert len(a3) == 3
    allocations = {
        clip["label"]: track
        for clip, track in _allocate_audio_tracks(a3, base_track_index=3,
                                                  fps=30.0)
    }
    assert allocations["sfx_004"] != allocations["sfx_005"], allocations
    assert {allocations["sfx_004"], allocations["sfx_005"]} == {3, 4}


def test_the_same_sound_twice_at_one_span_is_still_refused(media):
    """The bar: layering is two DIFFERENT sounds.  One waveform played
    twice adds level and nothing else, and no lane makes it audible as a
    second sound."""
    inputs = dict(_inputs(media))
    inputs.update(_sfx(media, [
        {"label": "sfx_001", "sfx_id": "impact",
         "source_file": media["whoosh.wav"],
         "timeline_in": 1.5, "timeline_out": 2.2, "volume_db": -8.0},
        {"label": "sfx_004", "sfx_id": "riser",
         "source_file": media["riser.wav"], "source_in": 0.0,
         "timeline_in": 6.0, "timeline_out": 9.0, "volume_db": -10.0},
        {"label": "sfx_005", "sfx_id": "riser",
         "source_file": media["riser.wav"], "source_in": 0.0,
         "timeline_in": 6.0, "timeline_out": 9.0, "volume_db": -10.0},
    ]))

    with pytest.raises(ValueError, match="same sound"):
        _compile(inputs)




def test_a_single_lane_track_still_refuses_a_duplicate_position():
    """V1 and V2 are lanes, not buckets: nine B-roll clips arriving at
    (0, 0) because the reader used keys the planner never wrote is the bug
    this check was written for, and it must still fire."""
    from library.steps.step_5_04_compile_manifest.step import (
        _apply_manifest_qa_checks,
    )

    manifest = {
        "project": {"duration_seconds": 10.0},
        "subtitles": [],
        "transitions": [],
        "tracks": {"V2": {"clips": [
            {"label": "broll_1", "timeline_in": 0.0, "timeline_out": 0.0},
            {"label": "broll_2", "timeline_in": 0.0, "timeline_out": 0.0},
        ]}},
    }
    with pytest.raises(ValueError, match="only one would be visible"):
        _apply_manifest_qa_checks(manifest)




# ── D3, the whole plan as one layered moment ────────────────────────
#
# The report's case sharp: the layered moment is not one layer among
# others, it is the ONLY sound in the plan. Every position-counting check
# then sees a single position and reads collapse, even though the plan
# names one block twice - which is what the schema invites.





def test_a_layered_moment_passes_validation_on_provenance():
    """One planned position shared by every clip at one timeline position
    is a layer, even when it is the only sound in the manifest."""
    from library.tools.manifest_validator import _check_sfx_distributed

    manifest = {"tracks": {"A3": {"clips": [
        {"label": "sfx_004", "timeline_in": 6.0, "timeline_out": 9.0,
         "spine_block_position": 2},
        {"label": "sfx_005", "timeline_in": 6.0, "timeline_out": 9.0,
         "spine_block_position": 2},
    ]}}}
    assert _check_sfx_distributed(manifest) == []

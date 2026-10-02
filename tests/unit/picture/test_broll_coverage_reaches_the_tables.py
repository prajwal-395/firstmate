"""The B-roll rows of two candidate tables said nothing was measurable.

A transition_slot's covering cutaway is measured in 4.03's table and admitted
as unheard (video_only) in 4.04's - never `not measured (no source clip)`.
History: `docs/evidence/broll_coverage.md`.
"""

import importlib.util
from pathlib import Path


from library.tools.broll_coverage import (
    VIDEO_ONLY_AUDIO_READING,
    coverage_by_block,
    covering_assignment,
    picture_clip_id,
)

ROOT = Path(__file__).resolve().parents[3]


def _bridge(step: str):
    spec = importlib.util.spec_from_file_location(
        f"{step}_bridge", ROOT / "library" / "steps" / step / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 001's own shapes, trimmed.
SPINE = {"structure": [
    {"block_type": "hook", "position": "hook", "clip_id": "clip_011",
     "source_start": 0.836, "source_end": 3.234,
     "timeline_start": 0.0, "timeline_end": 2.398,
     "content": {"text": "i can feel the silent judgment"}},
    {"block_type": "transition_slot", "position": 1, "clip_id": None,
     "source_start": None, "source_end": None,
     "timeline_start": 2.398, "timeline_end": 5.398,
     "visual_note": "B-roll: the parking lot"},
]}

BROLL = [{
    "spine_block_position": 1, "block_type": "transition_slot",
    "clip_id": "clip_001", "source_file": "/raw/IMG_1806.MOV",
    "video_in": 0.0, "video_out": 3.0, "duration_seconds": 3.0,
    "timeline_start": 2.398, "timeline_end": 5.398,
}]

CATALOG = [
    {"clip_id": "clip_011", "path": "/raw/IMG_1816.MOV"},
    {"clip_id": "clip_001", "path": "/raw/IMG_1806.MOV"},
]

DOCS = [
    {"clip_id": "IMG_1816_v3", "file_path": "/raw/IMG_1816.MOV",
     "camera": [{"start": 0, "end": 90, "movement": "tilting_up",
                 "stability": "shaky"}],
     "assessment": {}},
    {"clip_id": "IMG_1806_v3", "file_path": "/raw/IMG_1806.MOV",
     "camera": [{"start": 0, "end": 4, "movement": "stationary",
                 "stability": "stable"}],
     "assessment": {}},
]


def test_coverage_by_block_keys_on_the_identifier_the_answer_names():
    coverage = coverage_by_block(BROLL)
    assert set(coverage) == {1}
    assert covering_assignment(SPINE["structure"][1], coverage)["clip_id"] \
        == "clip_001"
    assert picture_clip_id(SPINE["structure"][1], coverage) == "clip_001"
    # A speech block plays its own clip and is unaffected.
    assert picture_clip_id(SPINE["structure"][0], coverage) == "clip_011"


def test_the_vfx_table_measures_the_cutaway_it_will_show():
    rows = _bridge("step_4_03_plan_vfx").build_vfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": BROLL,
        "clip_catalog": CATALOG, "semantic_analysis_documents": DOCS,
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert "not measured" not in broll_row["vfx_suggested"]
    assert "stationary" in broll_row["vfx_suggested"]
    assert "stable" in broll_row["vfx_suggested"]
    assert "B-roll cutaway clip_001" in broll_row["vfx_suggested"]


def test_the_sfx_table_names_the_cutaway_and_counts_no_unheard_transients():
    """The cutaway's own audio never plays, so its transients are not it."""
    rows = _bridge("step_4_04_plan_sfx").build_sfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": BROLL,
        "temporal_event_indices": [
            {"clip_id": "clip_001",
             "energy_curve": {"peak_times": [0.4, 1.1, 2.2]}}],
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert broll_row["action_sfx_suggested"] == (
        f"covered by clip_001, {VIDEO_ONLY_AUDIO_READING}")
    assert "transient" not in broll_row["action_sfx_suggested"]

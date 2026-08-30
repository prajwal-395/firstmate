"""The B-roll rows of two candidate tables said nothing was measurable.

Step 4.03's and step 4.04's pre-bridges build one row per spine block and
both read `block.get("clip_id")` off the SPINE. A `transition_slot` has
none, so both wrote **`not measured (no source clip)`** on every
non-speech block - 5 of 13 rows on project 001, 38% of each table, and
precisely the rows where a cutaway effect or a whoosh would go. The
covering clip is named in `b_roll_assignments`, 4,394 bytes, in the same
prompt.

The two tables want different things from that clip and get different
answers, which is the point:

- **4.03 gets a measurement.** A cutaway has a real camera description,
  so the row now reads `3.0s, stationary, stable, B-roll cutaway
  clip_001` where it read `not measured (no source clip)`.
- **4.04 gets an admitted absence with a reason.** A cutaway is placed
  `video_only`, so its own audio is never heard and there are no
  transients to count: `covered by clip_001, video only - the cutaway's
  own audio is never heard`.
"""

import importlib.util
from pathlib import Path

import pytest

from library.tools.broll_coverage import (
    VIDEO_ONLY_AUDIO_READING,
    coverage_by_block,
    covering_assignment,
    picture_clip_id,
)

ROOT = Path(__file__).resolve().parents[1]


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


def test_a_block_with_no_clip_and_no_cutaway_is_the_only_unmeasured_one():
    assert picture_clip_id({"position": 7}, {}) is None


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


def test_the_vfx_table_still_admits_a_block_nothing_covers():
    rows = _bridge("step_4_03_plan_vfx").build_vfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": [],
        "clip_catalog": CATALOG, "semantic_analysis_documents": DOCS,
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert broll_row["vfx_suggested"] == (
        "not measured (no source clip and no cutaway over it)")


def test_the_sfx_table_names_the_cutaway_and_says_its_audio_is_not_heard():
    rows = _bridge("step_4_04_plan_sfx").build_sfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": BROLL,
        "temporal_event_indices": [],
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert broll_row["action_sfx_suggested"] == (
        f"covered by clip_001, {VIDEO_ONLY_AUDIO_READING}")


def test_the_sfx_table_does_not_count_transients_a_viewer_cannot_hear():
    """The cutaway's own audio never plays, so its transients are not it."""
    rows = _bridge("step_4_04_plan_sfx").build_sfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": BROLL,
        "temporal_event_indices": [
            {"clip_id": "clip_001",
             "energy_curve": {"peak_times": [0.4, 1.1, 2.2]}}],
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert "transient" not in broll_row["action_sfx_suggested"]

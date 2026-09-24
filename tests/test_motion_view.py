"""`view:motion`: measured motion per spine block, for cuts on action.

The cut and effect planners (4.02, 4.03) address blocks, but the
motion measurement lives per clip in the routed temporal summaries.
This view is the addressed middle: one row per block with a source
clip, carrying the clip's dominant direction and kind plus the action
onsets and apexes inside the block's own source range, each mapped to
timeline seconds. The model still decides; the measurement is context.

Peaks are in SOURCE seconds in the summaries (the clips' own clock);
the rows carry TIMELINE seconds (the cut's clock), mapped through the
spine contract - the same conversion the motion anchors resolve
with, so the view and the anchor can never disagree about when a
peak plays.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.context_views import build_view  # noqa: E402

SUMMARIES = [
    {"clip_id": "clip_001",
     "dominant_motion": "pan_right",
     "dominant_direction": "right",
     "motion_method": "farneback",
     "motion_peaks": [
         {"time": 102.0, "kind": "onset", "magnitude": 0.4},
         {"time": 102.4, "kind": "apex", "magnitude": 0.7},
         {"time": 109.9, "kind": "apex", "magnitude": 0.5},
     ]},
    {"clip_id": "clip_002",
     "dominant_motion": "static",
     "dominant_direction": "static",
     "motion_method": "farneback",
     "motion_peaks": []},
    {"clip_id": "clip_003",
     "dominant_motion": "unknown",
     "dominant_direction": "unknown",
     "motion_method": "unmeasured",
     "motion_peaks": []},
]

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_001",
     "source_start": 100.0, "source_end": 105.0,
     "timeline_start": 8.0, "timeline_end": 13.0},
    {"position": 2, "block_type": "speech", "clip_id": "clip_002",
     "source_start": 200.0, "source_end": 205.0,
     "timeline_start": 13.0, "timeline_end": 18.0},
    {"position": 3, "block_type": "speech", "clip_id": "clip_003",
     "source_start": 300.0, "source_end": 305.0,
     "timeline_start": 18.0, "timeline_end": 23.0},
    {"position": 4, "block_type": "transition_slot",
     "timeline_start": 23.0, "timeline_end": 25.0},
]}


def _view(summaries=SUMMARIES, spine=SPINE, key="temporal_event_indices"):
    return build_view("motion", {key: summaries, "timed_spine": spine})


def test_peaks_inside_the_block_range_map_to_timeline_seconds():
    view = _view()["motion"]
    row = next(r for r in view["blocks"]
               if r["block_position"] == 1)
    assert row["clip_direction"] == "right"
    assert row["clip_motion"] == "pan_right"
    assert row["motion_method"] == "farneback"
    # 102.0/102.4 source play at 10.0/10.4 timeline; the 109.9 apex
    # sits outside this block's 100-105 source range and stays out.
    assert [(p["timeline_seconds"], p["kind"]) for p in row["peaks"]] == [
        (10.0, "onset"), (10.4, "apex")]
    assert row["peaks"][1]["magnitude"] == pytest.approx(0.7)


def test_a_measured_still_block_is_a_row_not_an_absence():
    view = _view()["motion"]
    row = next(r for r in view["blocks"]
               if r["block_position"] == 2)
    assert row["clip_motion"] == "static"
    assert row["peaks"] == []


def test_unmeasured_blocks_are_named_not_silent():
    view = _view()["motion"]
    named = view["not_measured"].split(": ")[-1].split(", ")
    assert sorted(named) == ["3", "4"]


def test_the_view_reads_either_routed_name():
    assert (_view(key="temporal_index")["motion"]["blocks_measured"]
            == _view()["motion"]["blocks_measured"] == 2)


def test_no_summaries_is_no_view():
    assert build_view("motion", {"timed_spine": SPINE}) == {}
    assert build_view("motion", {}) == {}

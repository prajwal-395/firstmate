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
    # A measured still block is a row, not an absence.
    still = next(r for r in view["blocks"] if r["block_position"] == 2)
    assert still["clip_motion"] == "static"
    assert still["peaks"] == []


def test_unmeasured_blocks_are_named_not_silent():
    view = _view()["motion"]
    named = view["not_measured"].split(": ")[-1].split(", ")
    assert sorted(named) == ["3", "4"]


def test_the_view_reads_either_routed_name():
    assert (_view(key="temporal_index")["motion"]["blocks_measured"]
            == _view()["motion"]["blocks_measured"] == 2)
    # No summaries is no view.
    assert build_view("motion", {"timed_spine": SPINE}) == {}
    assert build_view("motion", {}) == {}


def test_regional_framing_is_exposed_only_on_overlapping_source_ranges():
    selected = {
        "selected_by": "gemma_action",
        "action_labels": ["gestures with a hand"],
        "source_range": [101.0, 103.0],
        "motion_sample_rate_hz": 10,
        "resolution": [640, 360],
        "face": {"box_envelope": [0.48, 0.2, 0.54, 0.4],
                 "observed_samples": 20, "mean_flow_px": 0.54},
        "motion_regions": [{
            "track_id": "motion_region_01",
            "kind": "hand_body_motion_candidate",
            "classification": "motion_only_unclassified",
            "source_range": [101.2, 102.8],
            "envelope": [0.7, 0.6, 0.9, 0.9],
            "peak_p90_motion_px": 9.4,
            "observed_samples": 12,
        }],
        "crop_suggestion": {
            "recommendation": "consider_dynamic_crop",
            "dynamic_crop_needed": True,
            "preserve_region_ids": ["largest_face", "motion_region_01"],
        },
        "keep_clear_suggestions": [{
            "region_id": "motion_region_01",
            "box": [0.7, 0.6, 0.9, 0.9],
        }],
    }
    summary = {
        **SUMMARIES[0],
        "regional_motion_status": "measured",
        "regional_motion_reason": None,
        "regional_motion_spans": [selected],
    }
    no_candidates = {
        **SUMMARIES[1],
        "regional_motion_status": "no_candidates",
        "regional_motion_reason": "No action label selected this clip.",
        "regional_motion_spans": [],
    }
    view = _view(summaries=[summary, no_candidates])
    outgoing = next(row for row in view["motion"]["blocks"]
                    if row["block_position"] == 1)
    regional = outgoing["regional_framing"]
    assert regional["status"] == "measured"
    assert regional["candidate_spans"][0]["block_overlap"] == [101.0, 103.0]
    assert regional["candidate_spans"][0]["crop_suggestion"][
        "preserve_region_ids"] == ["largest_face", "motion_region_01"]

    incoming = next(row for row in view["motion"]["blocks"]
                    if row["block_position"] == 2)
    assert incoming["regional_framing"]["status"] == "no_candidates"
    assert incoming["regional_framing"]["candidate_spans"] == []

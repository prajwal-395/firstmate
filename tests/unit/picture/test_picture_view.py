"""Every step that decides from a shot sees the whole clip, not its opening.

`view:picture` reads the vision pass's per-window action records - what
happens, in which clip, between which two seconds - across the WHOLE
clip, beside (never instead of) `analysis.scene`, keyed by the catalog
clip id. Incident (scene[] covered 46.4% of 001): `docs/evidence/picture_view.md`.
"""
import json
import sys
from pathlib import Path
import os
import pytest
import subprocess
from library.steps.step_1_03_semantic_analysis import step as semantic_step
from library.steps.step_1_03_semantic_analysis.step import (
    _analyse_missing,
    _profile_stems,
    _run_clip_vision,
)


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon

STEPS = REPO / "library" / "steps"

# One long clip described across its whole length, one clip the vision
# pass described no action for at all.
DOCS = [
    {
        "clip_id": "IMG_1816_v3",
        "duration_s": 188.578,
        "analysis": {"scene": "[0.0-18.9s] Outdoor urban area"},
        "scene": [{"start": 0.0, "end": 18.9, "location": "Outdoor urban area"}],
        "blocks": [
            {"timestamp_range": "0:00-0:10", "start": 0, "end": 10,
             "visual": "The person is looking at the camera.",
             "body_language": "smiling"},
            {"timestamp_range": "3:00-3:08", "start": 180, "end": 188.578,
             "visual": "The person is looking upwards as if speaking.",
             "body_language": "head tilted back"},
        ],
    },
    {"clip_id": "IMG_1819_v3", "duration_s": 4.7, "blocks": []},
]


def manifest(step_dir: str) -> dict:
    return json.loads((STEPS / step_dir / "manifest.json").read_text())


def test_the_view_spans_the_whole_clip():
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    rows = view["picture"]["observed"]
    assert [r["clip_id"] for r in rows] == ["IMG_1816_v3"] * 2
    assert rows[0]["start"] == 0.0
    assert rows[-1]["end"] == 188.6, (
        "the last record has to reach the end of the clip, or the step is "
        "still reading a prefix of it"
    )
    assert rows[0]["visual"] == "The person is looking at the camera."
    # A clip with no observed action is NAMED - the absence is stated.
    assert "IMG_1819_v3" in view["picture"]["not_described"]


def test_the_view_carries_neither_body_language_nor_the_raw_record():
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    rows = view["picture"]["observed"]
    assert set(rows[0]) == {"clip_id", "start", "end", "visual"}
    assert "smiling" not in json_to_toon(view)


def test_the_view_reaches_the_prompt_and_survives_a_second_projection():
    """`creative_direction` is `llm_only`, so it is projected twice.

    The second pass sees a tree the first one already took `blocks` out
    of, so a view that rebuilt itself from scratch would delete what it
    had just built.
    """
    cf = manifest("step_2_01_creative_direction")["context_fields"]
    once = project_fields({"semantic_analysis_documents": DOCS}, cf)
    twice = project_fields(once, cf)
    assert twice["picture"] == once["picture"]
    assert "The person is looking upwards as if speaking." in json_to_toon(twice)


# The join. Documents are keyed by file stem; every table a planning step
# reasons over is keyed by the catalog's synthetic id (AGENTS.md 10.1), and
# a table the step cannot join to its own spine says nothing - which is
# what step 2.02 reported about `topics_toon`.
CATALOG = [
    {"clip_id": "clip_011", "file_path": "/p/raw/IMG_1816.MOV"},
    {"clip_id": "clip_014", "file_path": "/p/raw/IMG_1819.MOV"},
]

JOINABLE_DOCS = [
    {**DOCS[0], "file_path": "/p/raw/IMG_1816.MOV"},
    {**DOCS[1], "file_path": "/p/raw/IMG_1819.MOV"},
]


def test_rows_are_keyed_by_the_clip_id_the_rest_of_the_context_uses():
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "clip_catalog": CATALOG,
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"clip_011"}
    assert "clip_014" in view["picture"]["not_described"]

    # `plan_vfx` is routed the A-roll assignments, not the catalog: they
    # serve as the clip list.
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "a_roll_assignments": [{"video_segments": [
            {"clip_id": "clip_011", "source_file": "/p/raw/IMG_1816.MOV"}]}],
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"clip_011"}

    # A document no routed clip list names keeps its own id and SAYS so:
    # a mixed-id table is the dangerous one.
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "clip_catalog": [CATALOG[1]],
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"IMG_1816_v3"}
    assert "IMG_1816_v3" in view["picture"]["not_in_the_clip_list"]


# --------------------------------------------------------------------------
# From test_motion_view.py
#
# `view:motion`: measured motion per spine block, for cuts on action.
#
# The cut and effect planners (4.02, 4.03) address blocks, but the
# motion measurement lives per clip in the routed temporal summaries.
# This view is the addressed middle: one row per block with a source
# clip, carrying the clip's dominant direction and kind plus the action
# onsets and apexes inside the block's own source range, each mapped to
# timeline seconds. The model still decides; the measurement is context.
#
# Peaks are in SOURCE seconds in the summaries (the clips' own clock);
# the rows carry TIMELINE seconds (the cut's clock), mapped through the
# spine contract - the same conversion the motion anchors resolve
# with, so the view and the anchor can never disagree about when a
# peak plays.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


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


# --------------------------------------------------------------------------
# From test_scene_coverage.py
#
# `scene[]` describes 46.4% of 001's footage, and no reader could see the gap.
#
# Each undescribed range of `scene[]` is named in the prose and recorded as
# `scene_coverage`; nothing is invented for a range the vision pass never described.
# History: `docs/evidence/scene_coverage.md`.

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO_ROOT)

from library.tools.segment_coverage import (
    coverage_gaps,
    coverage_summary,
    normalize_segments,
)
from library.tools.vision_schema_adapter import (
    camera_prose,
    scene_prose,
)


# IMG_1816's shape on the run of record: 188.578 s of footage, one
# segment describing the first 18.9 s.
PREFIX_ONLY = {
    "clip_id": "IMG_1816",
    "file_path": "/footage/IMG_1816.MOV",
    "duration_s": 188.578,
    "scene": [
        {"start": 0.0, "end": 18.9,
         "location": "Outdoor urban area",
         "type": "outdoor", "lighting": "Daylight",
         "notable_features": ["Parked cars"]},
    ],
    "camera": [
        {"start": 0.0, "end": 188.578, "mode": "handheld",
         "framing": "medium", "stability": "stable",
         "movement": "walking"},
    ],
    "actions": [],
    "objects": [],
    "assessment": {"content_type": "person_talking_to_camera"},
    "analysis_metadata": {"pipeline_version": "v3"},
}

# IMG_1814's shape: described whole (the model's own end overshoots the
# clip by 0.057 s - sloppiness, not a gap).
FULLY_DESCRIBED = {
    "clip_id": "IMG_1814",
    "file_path": "/footage/IMG_1814.MOV",
    "duration_s": 45.943,
    "scene": [
        {"start": 0.0, "end": 46.0,
         "location": "Outdoor parking lot and construction site",
         "type": "outdoor", "lighting": "Daylight",
         "notable_features": ["Construction site with steel frame"]},
    ],
    "camera": [
        {"start": 0, "end": 46, "mode": "handheld", "framing": "wide",
         "stability": "stable", "movement": "stationary"},
    ],
    "actions": [],
    "objects": [],
    "assessment": {"content_type": "scenery"},
    "analysis_metadata": {"pipeline_version": "v3"},
}


def test_prefix_only_scene_prose_names_the_undescribed_range():
    """The run-of-record failure: 18.9 s described, 169.7 s of silence."""
    prose = scene_prose(PREFIX_ONLY)
    assert "Outdoor urban area" in prose
    assert "undescribed" in prose, (
        "the prose renders the described prefix and says nothing about "
        "the other 90% - a planning step reads this as a described clip")
    assert "18.9-188.6s" in prose
    # No segment at all is the same gap, not a blank cell.
    assert scene_prose(dict(PREFIX_ONLY, scene=[])) == (
        "[0.0-188.6s] undescribed - no scene observation for this range")


def test_fully_described_clip_gains_no_marker():
    """A gate that cries gap on full coverage is no coverage either."""
    assert "undescribed" not in scene_prose(FULLY_DESCRIBED)
    assert "undescribed" not in camera_prose(FULLY_DESCRIBED)


def test_unknown_duration_renders_segments_verbatim():
    """A gap against an unknown length is a guess, so none is named."""
    doc = {k: v for k, v in PREFIX_ONLY.items() if k != "duration_s"}
    prose = scene_prose(doc)
    assert "Outdoor urban area" in prose
    assert "undescribed" not in prose


def test_normalize_sorts_clamps_and_drops_empties():
    raw = [
        {"start": 100.0, "end": 500.0, "location": "lot"},
        {"start": 0.0, "end": 18.9, "location": "street"},
        {"start": 5.0, "end": 5.0, "location": "nothing"},
        {"start": "bad", "end": 9.0, "location": "malformed"},
        "junk",
    ]
    normalized = normalize_segments(raw, 188.578)
    assert [(s["start"], s["end"]) for s in normalized] == [
        (0.0, 18.9), (100.0, 188.578)]
    # Every other key survives verbatim.
    assert normalized[0]["location"] == "street"
    assert normalized[1]["location"] == "lot"


def test_the_summary_matches_the_issue_and_ignores_slivers():
    summary = coverage_summary(PREFIX_ONLY["scene"], PREFIX_ONLY["duration_s"])
    assert summary["described_s"] == pytest.approx(18.9)
    assert summary["ratio"] == pytest.approx(18.9 / 188.578, abs=1e-4)
    assert summary["undescribed_ranges"] == [[18.9, 188.578]]
    # 46.0 on a 45.943 s clip is timestamp wobble, not a gap.
    assert coverage_gaps(FULLY_DESCRIBED["scene"], 45.943) == []
    assert coverage_summary(
        FULLY_DESCRIBED["scene"], 45.943)["undescribed_ranges"] == []


# ── The producer half: what the vision pass stores ────────────────────

class _StubAnalyzer:
    """One canned folded answer per window, no model anywhere near it."""

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass", audio=None):
        if label.startswith("Objects"):
            return [], json.dumps([]), 0.1
        result = {
            "actions": [],
            "scene": [dict(PREFIX_ONLY["scene"][0])],
            "camera": [dict(PREFIX_ONLY["camera"][0])],
            "assessment": {"content_type": "person_talking_to_camera",
                           "primary_subject_visible": [[0, 188]]},
        }
        return result, json.dumps(result), 0.1


def test_analyze_clip_stores_normalized_scene_and_its_coverage():
    """The profile records what share was described, so no later reader
    has to recompute it - or can miss it."""
    from unittest.mock import patch

    from library.tools.analysis import vision_pipeline_v3 as vp

    meta = {"clip_id": "IMG_1816", "file_path": "/footage/IMG_1816.MOV",
            "duration_s": 188.578, "fps": 30.0, "resolution": [1920, 1080]}
    # One folded window answering all four sections in clip time, the
    # way the real pass does (see `_window_segments_in_range`).
    video_clips = [{"index": 0, "start": 0.0, "end": 188.578,
                    "path": "/tmp/stub.mp4", "has_audio": False}]
    with patch.object(vp.picture_quality, "measure_soft_picture",
                      return_value=[]):
        profile = vp.analyze_clip(
            _StubAnalyzer(), meta, [], video_clips, "", None,
            "/tmp/nonexistent-cache")

    assert profile["scene"][0]["end"] == 18.9
    coverage = profile["scene_coverage"]
    assert coverage["total_s"] == 188.578
    assert coverage["ratio"] == pytest.approx(18.9 / 188.578, abs=1e-4)
    assert coverage["undescribed_ranges"] == [[18.9, 188.578]]
    json.dumps(profile)


# --------------------------------------------------------------------------
# From test_unparsed_action_windows.py
#
# An unparsed action window is not an empty one, and must not vanish.
#
# The data distinguishes an unparsed VLM window (`parse_error`) from a
# genuinely empty one, and the picture view shows it as unmeasured rather
# than omitting it. Incident: `docs/evidence/unparsed_action_windows.md`.

sys.path.insert(0, str(REPO))

from library.tools.vision_schema_adapter import (
    UNPARSED_WINDOW_VISUAL,
    _blocks_from_actions,
    adapt_semantic_document,
)


# ── Faithful reconstruction of the two known windows ─────────────────
#
# clip_004/IMG_1809: three 10s windows.  [0,10] and [20,30] parsed fine;
# [10,20] took 6.25s (fastest of the three) and returned actions:[],
# consistent with a short or empty response that failed to parse.
#
# clip_015/IMG_1820: one 10s window.  [0,10] took 73.94s (by far the
# longest), consistent with a long malformed response.

IMG_1809_PROFILE = {
    "clip_id": "IMG_1809_v3",
    "file_path": "/footage/IMG_1809.MOV",
    "duration_s": 30.0,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "",
    "scene": [{"start": 0.0, "end": 30.0, "location": "parking lot",
               "type": "outdoor", "lighting": "daylight",
               "notable_features": []}],
    "camera": [{"start": 0, "end": 30, "mode": "handheld",
                "framing": "wide", "stability": "stable",
                "movement": "stationary"}],
    "actions": [
        {
            "window": [0, 10],
            "actions": [
                {"start": 0, "end": 10,
                 "action": "The person walks across the parking lot.",
                 "speech_cue": None,
                 "body_language": "relaxed stride"},
            ],
            "analysis_time_s": 12.50,
        },
        {
            # THE UNPARSED WINDOW - parse_json_object returned {}.
            "window": [10, 20],
            "actions": [],
            "analysis_time_s": 6.25,
            "parse_error": True,
        },
        {
            "window": [20, 30],
            "actions": [
                {"start": 20, "end": 30,
                 "action": "The person stops and looks up.",
                 "speech_cue": None,
                 "body_language": "head tilted back"},
            ],
            "analysis_time_s": 14.00,
        },
    ],
    "objects": [],
    "assessment": {
        "content_type": "person_talking_to_camera",
        "usable_ranges": [[0, 30.0]],
        "usable_ranges_method": "deterministic_v1",
        "camera_stability": "stable",
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 7},
}

IMG_1820_PROFILE = {
    "clip_id": "IMG_1820_v3",
    "file_path": "/footage/IMG_1820.MOV",
    "duration_s": 10.0,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "",
    "scene": [{"start": 0.0, "end": 10.0, "location": "sidewalk",
               "type": "outdoor", "lighting": "daylight",
               "notable_features": []}],
    "camera": [{"start": 0, "end": 10, "mode": "handheld",
                "framing": "medium", "stability": "shaky",
                "movement": "walking"}],
    "actions": [
        {
            # THE UNPARSED WINDOW - 73.94s, long malformed response.
            "window": [0, 10],
            "actions": [],
            "analysis_time_s": 73.94,
            "parse_error": True,
        },
    ],
    "objects": [],
    "assessment": {
        "content_type": "scenery",
        "usable_ranges": [[0, 10.0]],
        "usable_ranges_method": "deterministic_v1",
        "camera_stability": "shaky",
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 3},
}

# A clip with genuinely empty actions (no parse error).
GENUINELY_EMPTY_PROFILE = {
    "clip_id": "IMG_1850_v3",
    "file_path": "/footage/IMG_1850.MOV",
    "duration_s": 10.0,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "",
    "scene": [{"start": 0.0, "end": 10.0, "location": "empty room",
               "type": "indoor", "lighting": "dim",
               "notable_features": []}],
    "camera": [{"start": 0, "end": 10, "mode": "mounted",
                "framing": "wide", "stability": "stable",
                "movement": "stationary"}],
    "actions": [
        {
            "window": [0, 10],
            "actions": [],
            "analysis_time_s": 8.00,
            # No parse_error - the model genuinely saw nothing.
        },
    ],
    "objects": [],
    "assessment": {
        "content_type": "scenery",
        "usable_ranges": [[0, 10.0]],
        "usable_ranges_method": "deterministic_v1",
        "camera_stability": "stable",
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 3},
}


# ── Part 1: The data distinguishes unparsed from genuinely empty ─────


# ── Part 2: The sentinel block reaches the adapted document ──────────


class TestSentinelBlock:
    """An unparsed window produces a sentinel block so it is visible."""

    def test_unparsed_window_produces_a_block_with_visual(self):
        """The block exists and carries the unmeasured marker."""
        blocks = _blocks_from_actions(IMG_1809_PROFILE)
        # Three windows: two parsed with actions, one unparsed.
        assert len(blocks) == 3
        unparsed_block = blocks[1]
        assert unparsed_block["visual"] == UNPARSED_WINDOW_VISUAL
        assert unparsed_block["start"] == 10
        assert unparsed_block["end"] == 20
        assert unparsed_block["parse_error"] is True

# ── Part 3: The picture view shows unmeasured rather than omitting ───


def _adapted_docs():
    return [
        adapt_semantic_document(IMG_1809_PROFILE),
        adapt_semantic_document(IMG_1820_PROFILE),
        adapt_semantic_document(GENUINELY_EMPTY_PROFILE),
    ]


class TestPictureView:
    """The picture view reports unparsed windows as unmeasured."""

    def test_unparsed_window_appears_in_the_view(self):
        """IMG_1809 [10,20] used to be omitted.  Now it is a row."""
        view = build_view("picture", {
            "semantic_analysis_documents": _adapted_docs()})
        rows = view["picture"]["observed"]
        img_1809_rows = [r for r in rows if r["clip_id"] == "IMG_1809_v3"]
        # Three rows: [0,10], [10,20] (unmeasured), [20,30]
        assert len(img_1809_rows) == 3
        unmeasured_row = img_1809_rows[1]
        assert unmeasured_row["start"] == 10.0
        assert unmeasured_row["end"] == 20.0
        assert unmeasured_row["visual"] == UNPARSED_WINDOW_VISUAL

        # IMG_1820 (only window unparsed) used to be listed as
        # 'not_described'. Now it has a row, so it is described.
        img_1820_rows = [r for r in rows if r["clip_id"] == "IMG_1820_v3"]
        assert len(img_1820_rows) == 1
        assert img_1820_rows[0]["visual"] == UNPARSED_WINDOW_VISUAL
        assert "IMG_1820_v3" not in view["picture"].get("not_described", "")

    def test_genuinely_empty_clip_is_still_undescribed(self):
        """A window with no actions and no parse error still produces no
        block, so the clip is listed as not_described."""
        view = build_view("picture", {
            "semantic_analysis_documents": _adapted_docs()})
        not_described = view["picture"].get("not_described", "")
        assert "IMG_1850_v3" in not_described

    def test_unparsed_windows_field_names_the_gaps(self):
        """The view explicitly reports which windows were unparsed."""
        view = build_view("picture", {
            "semantic_analysis_documents": _adapted_docs()})
        unparsed = view["picture"].get("unparsed_windows", "")
        assert "IMG_1809_v3" in unparsed
        assert "IMG_1820_v3" in unparsed
        assert "2 window(s)" in unparsed

        # Normal operation: no field clutter.
        clean = adapt_semantic_document(dict(
            GENUINELY_EMPTY_PROFILE, clip_id="clean_v3", actions=[{
                "window": [0, 10],
                "actions": [{"start": 0, "end": 10,
                             "action": "Walks forward.",
                             "speech_cue": None,
                             "body_language": "relaxed"}]}]))
        view = build_view("picture", {"semantic_analysis_documents": [clean]})
        assert "unparsed_windows" not in view["picture"]

# ── Known unknowns, stated ───────────────────────────────────────────


# --------------------------------------------------------------------------
# From test_assessment_reports_no_default_as_measured.py
#
# No assessment field reports a default as though it were measured.
#
# The family, found one field at a time:
#
# - ``camera_stability`` read back as the literal ``"unknown"`` while
#   ``camera[]`` held the answer (#273, read-side).
# - ``usable_ranges`` asserting ``[[0, duration]]`` while the three fields
#   beside it said nobody looked (#248, producer side).
# - ``speech_coverage: 0.0`` with ``speech_coverage_method:
#   "temporal_index"`` whenever the temporal index carried no speech
#   regions - and ``speech_present: False`` beside it. On project 001 that
#   is 17 of 17 clips, 10 of them talking to camera.
# - ``primary_subject_visible: []`` when the model answered without the key.
# - ``clip_type: "b_roll"`` derived from a ``content_type`` of ``"unknown"``.
#
# This file is the sweep, kept executable: the deterministic half of the
# assessment is computed with nothing to measure, and every field it
# produces has to be an admitted absence rather than a value.
#
#
# Rules relocated from AGENTS.md 10.3
# -----------------------------------
# These are the engine's rules for this module.  They lived in AGENTS.md
# until it was split by subsystem; the wording is unchanged, so each rule
# is findable by its own words, and AGENTS.md 10.3 keeps the headline
# and points here.
#
# **No assessment field reports a default as though it were measured. That is the whole rule, and it holds for every field.**
# `compute_deterministic_assessment` is where the deterministic half is decided and `tests/unit/picture/test_picture_view.py` is the sweep, kept executable: the assessment is computed with nothing to measure and every field it produces must be an admitted absence. The family was found one field at a time, so assume another exists until the sweep says otherwise. [why - the four found in #301, and what a re-run of 001 would and would not fix](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)
# - **An empty `speech_regions` list is not a measurement of silence.** `detect_speech_regions` returns `([], transcription)` both when the seam ran and heard nothing and when it raised. **`speech_present` is `True` or `None`, never `False`**, and `speech_coverage_method` says `temporal_index` only once a coverage has been computed.
#
# Every field of `compute_deterministic_assessment` with nothing to measure must be an admitted absence. [why](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)
# - **`speech_present` is `True` or `None`, never `False`.**
# - **An answer that came back without a key is not an answer of `[]`.** `primary_subject_visible` is `None` when the model omitted it and `[]` only when the model really said the subject is nowhere.
# - A rendering of an absent measurement is not a measurement either: `_derived_clip_type` returns `""` for a `content_type` of `"unknown"` rather than classifying the clip `b_roll`.
# - **A method field travels with the number it qualifies.** The four manifests routing `assessment.usable_ranges` route `usable_ranges_method` beside it, and 2.02 routes `speech_coverage_method` beside `speech_coverage`; an allow-list that selects the number alone cannot tell a measurement from a default.

try:
    from library.tools.analysis.vision_pipeline_v3 import (
        compute_deterministic_assessment,
    )
except ImportError:
    compute_deterministic_assessment = None

from library.tools.semantic_index import clip_observations
from library.tools.vision_schema_adapter import (
    UNMEASURED_SUMMARY,
)

_SECTION_4_MARK = pytest.mark.skipif(
    compute_deterministic_assessment is None,
    reason='could not import "mlx_vlm" - mlx is a macOS-only dependency',
)

# Every value that reads as "nothing measured this". A field of the
# deterministic assessment must hold one of these when nothing did.
ADMITTED_ABSENCES = (None, "unknown", "unmeasured", "", [], {})


def _index_without_speech(duration=188.578):
    """A temporal index that exists and measured no speech.

    `detect_speech_regions` returns `([], transcription)` both when the
    seam ran and heard nothing and when it raised, so `[]` is not a
    measurement of silence and nothing here may read it as one.
    """
    return {"duration_s": duration, "speech_regions": []}


# ── The sweep ───────────────────────────────────────────────────────────

@_SECTION_4_MARK
def test_no_deterministic_field_holds_a_value_when_nothing_measured():
    """The whole assessment, computed with nothing to measure."""
    result = compute_deterministic_assessment(
        _index_without_speech(), transcript="", duration=188.578)

    reported = {
        key: value for key, value in result.items()
        if value not in ADMITTED_ABSENCES
    }
    assert not reported, (
        f"these assessment fields claim a value nothing measured: {reported}"
    )


# ── speech_coverage / speech_present ────────────────────────────────────

@_SECTION_4_MARK
def test_speech_present_is_never_false():
    """False is a claim of silence, and nothing here can measure one."""
    for transcript in ("", "   ", None):
        for index in (None, {}, _index_without_speech()):
            result = compute_deterministic_assessment(
                index, transcript=transcript, duration=10.0)
            assert result["speech_present"] is not False


@_SECTION_4_MARK
def test_a_transcript_alone_measures_presence_and_not_coverage():
    result = compute_deterministic_assessment(
        _index_without_speech(duration=10.0),
        transcript="i can feel the silent judgment", duration=10.0)

    assert result["speech_present"] is True
    assert result["speech_coverage"] is None, (
        "one string of words carries no timings, so how much of the clip "
        "is speech is still unmeasured")
    assert result["speech_coverage_method"] == "unmeasured"


@_SECTION_4_MARK
def test_regions_measure_both_and_say_which_measured_them():
    index = {"duration_s": 10.0,
             "speech_regions": [{"start": 1.0, "end": 6.0}]}
    result = compute_deterministic_assessment(index, "", duration=10.0)

    assert result["speech_present"] is True
    assert result["speech_coverage"] == 0.5
    assert result["speech_coverage_method"] == "temporal_index"


# ── primary_subject_visible ─────────────────────────────────────────────

def _assessment_from_model(answer):
    from library.tools.analysis import vision_pipeline_v3 as vp
    content_type, psv = vp._merge_assessment_votes(
        [{"window": [0.0, 10.0], "assessment": answer}])
    return vp._finish_assessment(
        {"speech_present": None, "camera_stability": "unknown"},
        content_type, psv, None, 10.0, [])


@_SECTION_4_MARK
def test_an_answer_of_empty_is_kept_because_the_model_made_it():
    assessment = _assessment_from_model(
        {"content_type": "scenery", "primary_subject_visible": []})
    assert assessment["primary_subject_visible"] == []


# ── usable_ranges: the display reads the method, not the ranges ─────────

STALE_001_ASSESSMENT = {
    # Exactly the shape every one of 001's 17 documents carries.
    "usable_ranges": [[0, 3.567]],
    "unusable_ranges": [],
    "usable_ranges_method": "unmeasured",
    "usable_ranges_signals": [],
    "content_type": "scenery",
}


@_SECTION_4_MARK
def test_the_broll_candidate_cell_says_unmeasured():
    """The cell the B-roll selector cut project 001's first interjection on."""
    doc = {
        "clip_id": "clip_001",
        "vision_schema_version": "3.0",
        "scene": [{"type": "exterior", "start": 0, "end": 3.5,
                   "description": "a street corner"}],
        "camera": [{"framing": "wide", "stability": "shaky",
                    "movement": "panning_right"}],
        "actions": [],
        "objects": [],
        "assessment": dict(STALE_001_ASSESSMENT),
    }
    assert clip_observations(doc)["usable_ranges"] == UNMEASURED_SUMMARY
    # A stale usable_portions string is replaced, not deferred to.
    doc = {
        "clip_id": "clip_001",
        "vision_schema_version": "3.0",
        "scene": [], "camera": [], "actions": [], "objects": [],
        "assessment": dict(STALE_001_ASSESSMENT, usable_portions="0.0-3.5s"),
    }
    adapted = adapt_semantic_document(doc)
    assert adapted["assessment"]["usable_portions"] == UNMEASURED_SUMMARY


# ── clip_type ───────────────────────────────────────────────────────────

@_SECTION_4_MARK
def test_an_unknown_content_type_derives_no_clip_type():
    doc = {
        "clip_id": "clip_001",
        "vision_schema_version": "3.0",
        "scene": [], "camera": [], "actions": [], "objects": [],
        "assessment": {"content_type": "unknown"},
    }
    adapted = adapt_semantic_document(doc)
    assert "clip_type" not in adapted["assessment"], (
        "b_roll would classify a clip nothing classified")


# --------------------------------------------------------------------------
# From test_semantic_analysis_step.py
#
# Step 1.03 must not re-analyse footage it has already analysed.
#
# Both the "already analysed?" check and the analyser's cache key use the
# media file's stem (`clip_profile_<stem>_v3.json`). Incident (every clip
# re-analysed every run): `docs/evidence/semantic_analysis_step.md`.

def _touch(d, name):
    open(os.path.join(d, name), "w").write("{}")


def test_v3_profiles_are_recognised(tmp_path):
    """The regression. `_v3` is the suffix the analyser actually writes."""
    _touch(tmp_path, "clip_profile_IMG_1806_v3.json")
    _touch(tmp_path, "clip_profile_IMG_1812_v3.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806", "IMG_1812"}
    # Partial video-only profiles do not count, and the empty-id artifact
    # the old rename wrote (`clip_profile_.json`) is ignored.
    _touch(tmp_path, "clip_profile_IMG_1807_video_only.json")
    _touch(tmp_path, "clip_profile_.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806", "IMG_1812"}


def test_clip_vision_chatter_never_reaches_step_stdout(capfd):
    """A per-clip vision child prints progress to its own stdout
    ("Model loaded/bound in ...", window lines). Run uncaptured, that
    chatter inherits the step's stdout and is prepended to the step's
    own JSON, so the runner rejects the whole step as "Step produced
    invalid JSON" after every clip was analysed (rung-0a proof run:
    11 profiles collected, step failed). The helper captures both
    streams and forwards them to stderr, where the runner streams them
    as the step's log - the step's stdout stays empty until its own
    `json.dump`.

    `capfd`, not `capsys`: a child inherits the OS file descriptor,
    bypassing Python-level `sys.stdout` replacement, so only an
    fd-level capture sees the pollution - with `capsys` this test
    would pass against the buggy uncaptured call.
    """
    child = [
        sys.executable, "-c",
        "import sys;"
        " print('  Model loaded/bound in 0.0s');"
        " print('{\"profiles\": 1}');"
        " print('a warning line', file=sys.stderr)",
    ]
    _run_clip_vision(child)
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "Model loaded/bound in 0.0s" in captured.err
    assert '{"profiles": 1}' in captured.err
    assert "a warning line" in captured.err
    # `check` semantics are unchanged: a nonzero exit raises, so the
    # step skips the clip exactly as before.
    with pytest.raises(subprocess.CalledProcessError):
        _run_clip_vision(
            [sys.executable, "-c", "import sys; sys.exit(1)"])


def _fake_vision(analysis_dir, dies_on=()):
    """A stand-in vision child: works its --clip list in order, writes a
    profile per clip, and dies on any clip named in `dies_on`."""
    calls = []

    def run(cmd, progress=None):
        clips = cmd[cmd.index("--clip") + 1:]
        calls.append(clips)
        for clip in clips:
            stem = os.path.splitext(os.path.basename(clip))[0]
            if stem in dies_on:
                raise subprocess.CalledProcessError(1, cmd)
            _touch(analysis_dir, f"clip_profile_{stem}_v3.json")

    return run, calls


def test_missing_clips_share_one_vision_child(tmp_path):
    """The defect: one child per clip loaded Gemma once per clip
    (measured 2026-10-01, three 6s clips: three model loads). All
    missing clips now go to one child, so the model loads once."""
    run, calls = _fake_vision(str(tmp_path))
    clips = [f"/raw/A{i}.MOV" for i in range(3)]
    _analyse_missing(clips, ["vision"], str(tmp_path), run=run)
    assert calls == [clips]
    assert _profile_stems(str(tmp_path)) == {"A0", "A1", "A2"}


def test_a_clip_that_kills_the_child_costs_only_itself(tmp_path):
    """Per-clip children isolated a bad clip; the batch keeps that: the
    clip the child died on is skipped and a fresh child takes the rest."""
    run, calls = _fake_vision(str(tmp_path), dies_on={"A1"})
    clips = [f"/raw/A{i}.MOV" for i in range(4)]
    _analyse_missing(clips, ["vision"], str(tmp_path), run=run)
    assert calls == [clips, clips[2:]]
    assert _profile_stems(str(tmp_path)) == {"A0", "A2", "A3"}


def test_the_wedge_ceiling_restarts_on_each_finished_clip(monkeypatch):
    """One child now runs N clips, so the ceiling must stay per CLIP:
    a child that keeps landing profiles is never killed, one that
    stalls is."""
    monkeypatch.setattr(semantic_step, "CLIP_ANALYSIS_TIMEOUT_S", 0.6)
    ticks = iter(range(1000))
    sleeper = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    _run_clip_vision(sleeper, progress=lambda: next(ticks), poll_s=0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        _run_clip_vision(sleeper, progress=lambda: 0, poll_s=0.1)

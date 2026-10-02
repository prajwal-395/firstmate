"""`scene[]` describes 46.4% of 001's footage, and no reader could see the gap.

Each undescribed range of `scene[]` is named in the prose and recorded as
`scene_coverage`; nothing is invented for a range the vision pass never described.
History: `docs/evidence/scene_coverage.md`.
"""

import json
import os
import sys

import pytest

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

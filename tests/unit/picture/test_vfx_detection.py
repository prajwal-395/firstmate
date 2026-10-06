"""Tests for the VFX detection layer.

These tests pin behaviour that fails on origin/main: the detection module
does not exist there, so every test that imports it fails. The tests name
the defect they catch - a shot needing stabilization that is not detected,
a screen replacement candidate that is missed, a subject isolation need
that is not emitted as a plan item.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from library.tools.vfx_detection import (
    detect_object_removal_candidates,
    detect_retiming_candidates,
    detect_screen_replacement_candidates,
    detect_stabilization_needs,
    detect_subject_isolation_needs,
    detect_tracking_candidates,
    detect_vfx_needs,
)


def _temporal_index_entry(clip_id, residuals):
    """Build a temporal index entry with camera motion decomposition."""
    return {
        "clip_id": clip_id,
        "camera_motion_decomposition": {
            "sample_rate_hz": 5,
            "values": [
                {"translation_x": 0.1, "translation_y": 0.05,
                 "residual": r}
                for r in residuals
            ],
        },
    }


def test_stabilization_detects_unstable_shot():
    """A clip whose mean residual exceeds HANDHELD_BELOW is detected.

    Defect: without this detection, a shaky shot reaches the VFX planner
    with no measurement that it needs stabilization - the planner must
    guess from the VLM's prose alone.
    """
    residuals = [0.2] * 20
    entry = _temporal_index_entry("clip_shaky", residuals)
    items = detect_stabilization_needs({"temporal_event_indices": [entry]})
    assert len(items) == 1
    assert items[0]["detection_type"] == "stabilization_needed"
    assert items[0]["clip_id"] == "clip_shaky"
    assert items[0]["evidence"]["label"] == "unstable"
    assert items[0]["evidence"]["mean_residual"] == pytest.approx(0.2)
    assert items[0]["executable_by"] == "stabilization hold D5"


def test_stabilization_skips_stable_shot():
    """A clip whose mean residual is below STABLE_BELOW is not emitted.

    Defect: emitting a stable shot as needing stabilization would authorize
    a stabilization hold on footage that does not need it - the D5
    measurement shows stabilization is 57-65% of the placement hold.
    """
    residuals = [0.01] * 20
    entry = _temporal_index_entry("clip_stable", residuals)
    items = detect_stabilization_needs({"temporal_event_indices": [entry]})
    assert items == []


def test_stabilization_skips_unmeasured_clip():
    """A clip with no camera motion decomposition is not emitted.

    Defect: an absent measurement is not evidence of shake. Emitting a
    plan item for an unmeasured clip would authorize stabilization on
    footage that has not been measured.
    """
    entry = {"clip_id": "clip_unmeasured"}
    items = detect_stabilization_needs({"temporal_event_indices": [entry]})
    assert items == []


def test_screen_replacement_detects_object_with_text():
    """An object with readable text that is not a person is a candidate.

    Defect: without this detection, a screen with readable text reaches
    the VFX planner with no flag that it is a replacement candidate - the
    planner must infer it from the object label alone.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [
            {
                "label": "laptop screen",
                "category": "object",
                "readable_text": "Hello World",
                "appearances": [[0.0, 5.0]],
                "role": "background",
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_screen_replacement_candidates(semantic_docs, clip_catalog)
    assert len(items) == 1
    assert items[0]["detection_type"] == "screen_replacement_candidate"
    assert items[0]["clip_id"] == "clip_001"
    assert items[0]["evidence"]["readable_text"] == "Hello World"
    assert items[0]["executable_by"] == "mask and track ops C-11"


def test_screen_replacement_skips_person():
    """A person with readable text is not a screen replacement candidate.

    Defect: emitting a person as a screen replacement candidate would
    route a person to the mask and track ops - the category filter is
    what keeps people out of the screen replacement path.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [
            {
                "label": "person wearing a t-shirt with text",
                "category": "person",
                "readable_text": "Nike",
                "appearances": [[0.0, 5.0]],
                "role": "primary_subject",
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_screen_replacement_candidates(semantic_docs, clip_catalog)
    assert items == []


def test_screen_replacement_skips_object_without_text():
    """An object without readable text is not a replacement candidate.

    Defect: an object with no measured text has no content to replace -
    emitting it would route a textless object to the mask and track ops
    with nothing to track.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [
            {
                "label": "coffee mug",
                "category": "object",
                "readable_text": None,
                "appearances": [[0.0, 5.0]],
                "role": "background",
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_screen_replacement_candidates(semantic_docs, clip_catalog)
    assert items == []


def test_subject_isolation_detects_subject_grade():
    """A clip named in subject_grades needs isolation.

    Defect: without this detection, a subject grade reaches the compile
    with no plan item saying the clip needs a matte - the grade would
    apply to the whole frame instead of the subject.
    """
    color_grade_spec = {
        "subject_grades": [
            {
                "clip_id": "clip_001",
                "target": {"kind": "person", "role": "speaker"},
                "scope": "subject-only",
                "grade": {"gain": 1.17, "contrast": 0.0, "saturation": 1.31},
            },
        ],
    }
    items = detect_subject_isolation_needs(color_grade_spec=color_grade_spec)
    assert len(items) == 1
    assert items[0]["detection_type"] == "subject_isolation_needed"
    assert items[0]["clip_id"] == "clip_001"
    assert "subject_grade" in items[0]["reason"]
    assert items[0]["executable_by"] == "Magic Mask seed / SAM2 segmentation"


def test_subject_isolation_detects_behind_subject_overlay():
    """A clip under a behind-subject overlay needs isolation.

    Defect: without this detection, a behind-subject overlay reaches the
    compile with no plan item saying the clip needs a matte - the overlay
    would composite over the whole frame instead of behind the subject.
    """
    behind_subject_overlays = {
        "segments": [
            {
                "segment_id": "seg_001",
                "timeline_start": 0.0,
                "timeline_end": 5.0,
            },
        ],
    }
    a_roll_assignments = [
        {
            "clip_id": "clip_001",
            "timeline_start": 0.0,
            "timeline_end": 10.0,
            "video_segments": [
                {"clip_id": "clip_001", "duration_seconds": 10.0},
            ],
        },
    ]
    items = detect_subject_isolation_needs(
        behind_subject_overlays=behind_subject_overlays,
        a_roll_assignments=a_roll_assignments,
    )
    assert len(items) == 1
    assert items[0]["detection_type"] == "subject_isolation_needed"
    assert items[0]["clip_id"] == "clip_001"
    assert "behind_subject" in items[0]["reason"]


def test_detect_vfx_needs_combines_all_three():
    """The combined detection returns all three types.

    Defect: without the combined entry point, a caller must invoke each
    detection separately and merge the results by hand - the merge is
    where a detection gets dropped.
    """
    residuals = [0.2] * 20
    entry = _temporal_index_entry("clip_shaky", residuals)
    semantic_docs = [{
        "clip_id": "clip_002",
        "file_path": "/footage/clip_002.mov",
        "objects": [
            {
                "label": "monitor",
                "category": "object",
                "readable_text": "Error 404",
                "appearances": [[0.0, 3.0]],
                "role": "background",
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_002", "path": "/footage/clip_002.mov"}]
    color_grade_spec = {
        "subject_grades": [
            {
                "clip_id": "clip_003",
                "target": {"kind": "person", "role": "speaker"},
                "scope": "subject-only",
                "grade": {"gain": 1.17},
            },
        ],
    }
    result = detect_vfx_needs(
        temporal_index={"temporal_event_indices": [entry]},
        semantic_docs=semantic_docs,
        clip_catalog=clip_catalog,
        color_grade_spec=color_grade_spec,
    )
    assert len(result["detections"]) == 3
    assert len(result["by_type"]["stabilization_needed"]) == 1
    assert len(result["by_type"]["screen_replacement_candidate"]) == 1
    assert len(result["by_type"]["subject_isolation_needed"]) == 1
    assert set(result["clips_touched"]) == {"clip_shaky", "clip_002",
                                            "clip_003"}


def test_detect_vfx_needs_empty_when_nothing_detected():
    """No detections when no clip exceeds any threshold.

    Defect: emitting plan items for clips that do not need VFX would
    route them to operations that should not touch them - the empty
    result is the correct answer when nothing is detected.
    """
    result = detect_vfx_needs()
    assert result["detections"] == []
    assert result["clips_touched"] == []


def test_retiming_detects_high_motion_clip():
    """A clip whose peak motion is above 2x the project median is detected.

    Defect: without this detection, a high-motion clip reaches the VFX
    planner with no measurement that it is a retiming candidate - the
    planner must guess from the VLM's prose alone.
    """
    entries = [
        {"clip_id": "clip_calm",
         "motion_energy": {"peak_mean_abs_diff": 0.05}},
        {"clip_id": "clip_moderate",
         "motion_energy": {"peak_mean_abs_diff": 0.08}},
        {"clip_id": "clip_fast",
         "motion_energy": {"peak_mean_abs_diff": 0.35,
                           "high_motion_times": [1.0, 2.0, 3.0]}},
    ]
    items = detect_retiming_candidates({"temporal_event_indices": entries})
    assert len(items) == 1
    assert items[0]["detection_type"] == "retiming_candidate"
    assert items[0]["clip_id"] == "clip_fast"
    assert items[0]["evidence"]["peak_mean_abs_diff"] == pytest.approx(0.35)
    assert items[0]["evidence"]["threshold_source"] == \
        "2x the project median peak_mean_abs_diff"
    assert items[0]["executable_by"] == \
        "native speed effects (speed_ramp, freeze_frame)"


def test_retiming_skips_clip_at_median():
    """A clip at the project median is not a retiming candidate.

    Defect: emitting a median-motion clip as a retiming candidate would
    route a typical clip to speed effects that should not touch it.
    """
    entries = [
        {"clip_id": "clip_a",
         "motion_energy": {"peak_mean_abs_diff": 0.10}},
        {"clip_id": "clip_b",
         "motion_energy": {"peak_mean_abs_diff": 0.10}},
    ]
    items = detect_retiming_candidates({"temporal_event_indices": entries})
    assert items == []


def test_retiming_skips_unmeasured_clip():
    """A clip with no motion energy measurement is not emitted.

    Defect: an absent measurement is not evidence of motion. Emitting a
    plan item for an unmeasured clip would route it to speed effects with
    no measured justification.
    """
    entries = [
        {"clip_id": "clip_unmeasured"},
        {"clip_id": "clip_measured",
         "motion_energy": {"peak_mean_abs_diff": 0.10}},
    ]
    items = detect_retiming_candidates({"temporal_event_indices": entries})
    assert items == []


def test_object_removal_detects_passing_object():
    """An object with role 'passing' is a removal candidate.

    Defect: without this detection, a briefly-visible object reaches the
    VFX planner with no flag that it is a removal candidate - the planner
    must infer it from the object label alone.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [
            {
                "label": "bird flying through",
                "category": "object",
                "role": "passing",
                "appearances": [[2.0, 3.0]],
                "readable_text": None,
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_object_removal_candidates(semantic_docs, clip_catalog)
    assert len(items) == 1
    assert items[0]["detection_type"] == "object_removal_candidate"
    assert items[0]["clip_id"] == "clip_001"
    assert items[0]["evidence"]["role"] == "passing"
    assert items[0]["executable_by"] == "mask and track ops C-11"


def test_object_removal_skips_primary_subject():
    """A primary subject is not an object removal candidate.

    Defect: emitting a primary subject as a removal candidate would route
    the main subject of a shot to the mask and track ops for removal.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [
            {
                "label": "speaker",
                "category": "person",
                "role": "primary_subject",
                "appearances": [[0.0, 10.0]],
                "readable_text": None,
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_object_removal_candidates(semantic_docs, clip_catalog)
    assert items == []


def test_object_removal_skips_background_object():
    """A background object is not a removal candidate.

    Defect: a background object is visible but not a briefly-visible
    distraction - emitting it would route a stable background element to
    the removal ops without the measured 'passing' signal.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [
            {
                "label": "lamp",
                "category": "object",
                "role": "background",
                "appearances": [[0.0, 10.0]],
                "readable_text": None,
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_object_removal_candidates(semantic_docs, clip_catalog)
    assert items == []


def test_detect_vfx_needs_includes_retiming_and_removal():
    """The combined detection includes retiming and object removal.

    Defect: without the combined entry point including the new types,
    a caller must invoke each detection separately and merge the results
    by hand - the merge is where a detection gets dropped.
    """
    entries = [
        {"clip_id": "clip_fast",
         "motion_energy": {"peak_mean_abs_diff": 0.35}},
        {"clip_id": "clip_calm",
         "motion_energy": {"peak_mean_abs_diff": 0.05}},
        {"clip_id": "clip_moderate",
         "motion_energy": {"peak_mean_abs_diff": 0.08}},
    ]
    semantic_docs = [{
        "clip_id": "clip_002",
        "file_path": "/footage/clip_002.mov",
        "objects": [
            {
                "label": "bird",
                "category": "object",
                "role": "passing",
                "appearances": [[1.0, 2.0]],
                "readable_text": None,
            },
        ],
    }]
    clip_catalog = [{"clip_id": "clip_002", "path": "/footage/clip_002.mov"}]
    result = detect_vfx_needs(
        temporal_index={"temporal_event_indices": entries},
        semantic_docs=semantic_docs,
        clip_catalog=clip_catalog,
    )
    assert len(result["by_type"]["retiming_candidate"]) == 1
    assert len(result["by_type"]["object_removal_candidate"]) == 1
    assert "clip_fast" in result["clips_touched"]
    assert "clip_002" in result["clips_touched"]


def test_stabilization_records_missing_index_as_unmeasured(tmp_path):
    """A clip whose index file is missing is recorded as unmeasured.

    Defect: the old code silently continued when the index file did not
    exist, so a clip with an unreadable index vanished from the report
    entirely - the planner could not distinguish "measured and stable"
    from "never measured".
    """
    entry = {"clip_id": "clip_missing",
             "index_path": str(tmp_path / "nonexistent.json")}
    unmeasured = []
    items = detect_stabilization_needs(
        {"temporal_event_indices": [entry]}, unmeasured=unmeasured)
    assert items == []
    assert len(unmeasured) == 1
    assert unmeasured[0]["clip_id"] == "clip_missing"
    assert "not found" in unmeasured[0]["reason"]


def test_stabilization_records_unparseable_index_as_unmeasured(tmp_path):
    """A clip whose index file is corrupt is recorded as unmeasured.

    Defect: the old code silently continued when the index file could
    not be parsed, so a clip with an unreadable index vanished from the
    report entirely - the planner could not distinguish "measured and
    stable" from "never measured".
    """
    bad_index = tmp_path / "bad.json"
    bad_index.write_text("{not valid json", encoding="utf-8")
    entry = {"clip_id": "clip_corrupt", "index_path": str(bad_index)}
    unmeasured = []
    items = detect_stabilization_needs(
        {"temporal_event_indices": [entry]}, unmeasured=unmeasured)
    assert items == []
    assert len(unmeasured) == 1
    assert unmeasured[0]["clip_id"] == "clip_corrupt"
    assert "unreadable" in unmeasured[0]["reason"]


def test_stabilization_carries_intent_read():
    """A stabilization plan item carries the measured camera modes.

    Defect: without the intent read, a deliberate handheld shot and a
    defective shaky shot look identical to the planner - both are just
    "shake above threshold". The camera mode is the measured signal that
    separates them.
    """
    residuals = [0.2] * 20
    entry = _temporal_index_entry("clip_shaky", residuals)
    semantic_docs = [{
        "clip_id": "clip_shaky",
        "file_path": "/footage/clip_shaky.mov",
        "camera": [
            {"start": 0.0, "end": 10.0, "mode": "handheld",
             "framing": "medium", "stability": "shaky",
             "movement": "walking"},
        ],
    }]
    clip_catalog = [{"clip_id": "clip_shaky",
                     "path": "/footage/clip_shaky.mov"}]
    items = detect_stabilization_needs(
        {"temporal_event_indices": [entry]},
        clip_catalog=clip_catalog,
        semantic_docs=semantic_docs,
    )
    assert len(items) == 1
    assert items[0]["evidence"]["camera_modes"] == ["handheld"]


def test_stabilization_intent_read_absent_without_semantic_docs():
    """No camera modes in evidence when semantic docs are not provided.

    Defect: fabricating a camera mode when none was measured would give
    the planner a false intent read - the evidence carries the intent
    only where one exists.
    """
    residuals = [0.2] * 20
    entry = _temporal_index_entry("clip_shaky", residuals)
    items = detect_stabilization_needs({"temporal_event_indices": [entry]})
    assert len(items) == 1
    assert "camera_modes" not in items[0]["evidence"]


def test_tracking_detects_tracking_camera_mode():
    """A clip whose camera mode is 'tracking' is a tracking candidate.

    Defect: without this detection, a shot where the camera follows a
    subject reaches the VFX planner with no measurement that an overlay
    would need motion tracking - the planner must guess from prose.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "camera": [
            {"start": 0.0, "end": 10.0, "mode": "tracking",
             "framing": "medium", "stability": "smooth",
             "movement": "walking"},
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_tracking_candidates(semantic_docs, clip_catalog)
    assert len(items) == 1
    assert items[0]["detection_type"] == "tracking_candidate"
    assert items[0]["clip_id"] == "clip_001"
    assert items[0]["evidence"]["camera_modes"] == ["tracking"]
    assert items[0]["executable_by"] == "mask and track ops C-11"


def test_tracking_skips_non_tracking_modes():
    """Clips with stationary or mounted cameras are not tracking candidates.

    Defect: emitting a static shot as a tracking candidate would route it
    to the track ops with no measured camera movement to track.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "camera": [
            {"start": 0.0, "end": 10.0, "mode": "mounted",
             "framing": "wide", "stability": "stable",
             "movement": "stationary"},
        ],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_tracking_candidates(semantic_docs, clip_catalog)
    assert items == []


def test_tracking_skips_clips_without_camera_segments():
    """A clip with no camera segments is not a tracking candidate.

    Defect: an absent camera measurement is not evidence of tracking -
    emitting a plan item for a clip with no camera data would route it to
    the track ops with nothing to track.
    """
    semantic_docs = [{
        "clip_id": "clip_001",
        "file_path": "/footage/clip_001.mov",
        "objects": [],
    }]
    clip_catalog = [{"clip_id": "clip_001", "path": "/footage/clip_001.mov"}]
    items = detect_tracking_candidates(semantic_docs, clip_catalog)
    assert items == []


def test_detect_vfx_needs_includes_tracking_and_unmeasured():
    """The combined detection includes tracking and unmeasured stabilization.

    Defect: without the combined entry point including the new types,
    a caller must invoke each detection separately and merge the results
    by hand - the merge is where a detection gets dropped.
    """
    entries = [
        {"clip_id": "clip_missing",
         "index_path": "/nonexistent/index.json"},
    ]
    semantic_docs = [{
        "clip_id": "clip_002",
        "file_path": "/footage/clip_002.mov",
        "camera": [
            {"start": 0.0, "end": 10.0, "mode": "tracking",
             "framing": "medium", "stability": "smooth",
             "movement": "walking"},
        ],
    }]
    clip_catalog = [{"clip_id": "clip_002", "path": "/footage/clip_002.mov"}]
    result = detect_vfx_needs(
        temporal_index={"temporal_event_indices": entries},
        semantic_docs=semantic_docs,
        clip_catalog=clip_catalog,
    )
    assert len(result["by_type"]["tracking_candidate"]) == 1
    assert len(result["unmeasured_stabilization"]) == 1
    assert result["unmeasured_stabilization"][0]["clip_id"] == "clip_missing"

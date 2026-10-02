"""Step 1.06 is wired and matte-triggered: it segments ONLY the clips a
matte-needing plan names, and the trigger is testable without a GPU.

A clip is wanted when a `subject_grades` entry names its clip_id, or a
`behind_subject_overlays` segment plays over a span a placed picture
clip covers. Nothing planned, nothing segmented - the step returns an
empty trigger without loading the segmenter.
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_06_object_segmentation.step import (
    face_boxes_from_temporal_index,
    matte_trigger,
    run_step,
)


def _aroll(*rows):
    return [{"clip_id": cid, "timeline_start": start, "timeline_end": end}
            for cid, start, end in rows]


def _behind(*rows):
    return {"segments": [
        {"segment_id": sid, "timeline_start": start, "timeline_end": end}
        for sid, start, end in rows]}


def test_no_plan_names_no_clip():
    assert matte_trigger() == {}
    assert matte_trigger(
        color_grade_spec={"subject_grades": []},
        behind_subject_overlays={"segments": []},
        a_roll_assignments=_aroll(("c1", 0.0, 10.0))) == {}
    # A behind span outside every placed clip names nothing.
    assert matte_trigger(
        behind_subject_overlays=_behind(("t1", 20.0, 25.0)),
        a_roll_assignments=_aroll(("c1", 0.0, 10.0))) == {}


def test_a_subject_grade_names_its_clip():
    wanted = matte_trigger(
        color_grade_spec={"subject_grades": [
            {"clip_id": "c1", "scope": "subject-only"}]})
    assert list(wanted) == ["c1"]
    assert wanted["c1"]


def test_a_behind_segment_names_every_clip_its_span_plays_over():
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 2.0, 5.0)),
        a_roll_assignments=_aroll(("c1", 0.0, 4.0), ("c2", 4.0, 10.0)))
    assert sorted(wanted) == ["c1", "c2"]
    # B-roll cover counts as a wanted clip.
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 2.0, 5.0)),
        a_roll_assignments=_aroll(("c1", 0.0, 10.0)),
        b_roll_assignments=[{
            "clip_id": "b7", "timeline_start": 3.0, "timeline_end": 6.0}])
    assert sorted(wanted) == ["b7", "c1"]


def test_a_speech_block_names_its_video_segments_clip():
    """The real assign_aroll shape nests the clip_id.

    A speech block carries no top-level clip_id - the picture it
    plays comes from `video_segments`. Measured 2026-09-24: a behind
    title over a real speech block triggered nothing and the compile
    refused for no matte.
    """
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 0.0, 0.72)),
        a_roll_assignments=[{
            "spine_block_position": 1, "block_type": "speech",
            "timeline_start": 0.0, "timeline_end": 0.72,
            "video_segments": [{
                "clip_id": "clip_001",
                "duration_seconds": 0.72,
                "video_in": 3.32, "video_out": 4.04,
            }],
        }])
    assert list(wanted) == ["clip_001"]

    # Video-segment shares resolve within the block span.
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 5.0, 6.0)),
        a_roll_assignments=[{
            "spine_block_position": 1, "block_type": "speech",
            "timeline_start": 0.0, "timeline_end": 10.0,
            "video_segments": [
                {"clip_id": "c1", "duration_seconds": 4.0},
                {"clip_id": "c2", "duration_seconds": 6.0},
            ],
        }])
    assert list(wanted) == ["c2"]


def test_face_boxes_come_from_the_per_clip_index_files(tmp_path):
    index = tmp_path / "c1_index.json"
    index.write_text(json.dumps({
        "clip_id": "c1",
        "face_presence": {"face_boxes": [None, [0.1, 0.2, 0.3, 0.5]]},
    }), encoding="utf-8")
    boxes = face_boxes_from_temporal_index({
        "temporal_event_indices": [
            {"clip_id": "c1", "index_path": str(index)},
            {"clip_id": "c2", "index_path": str(tmp_path / "missing.json")},
        ]})
    assert boxes == {"c1": [0.1, 0.2, 0.3, 0.5]}


def test_an_untriggered_run_segments_nothing_and_loads_no_model(tmp_path):
    from library.steps.step_1_06_object_segmentation import step as mod
    monkeypatch = pytest.MonkeyPatch()
    def _boom():
        raise AssertionError("the segmenter must not load")
    monkeypatch.setattr(mod, "get_segmenter", _boom)
    try:
        result = run_step(
            [], [{"clip_id": "c1", "path": str(tmp_path / "c1.mp4")}],
            str(tmp_path / "out"))
    finally:
        monkeypatch.undo()
    assert result["object_segmentation"] == []
    assert result["matte_trigger"]["wanted_clip_ids"] == []


# ── The wiring ──────────────────────────────────────────────────────

def _dag():
    from library.tools import run_scope
    return run_scope.load_dag()


def test_the_step_runs_after_the_plans_and_before_the_compile():
    from library.tools import run_scope
    order = run_scope.topological_order(_dag())
    rank = {node_id: i for i, node_id in enumerate(order)}
    assert rank["render_motion_graphics"] < rank["object_segmentation"]
    assert rank["color_grade"] < rank["object_segmentation"]
    assert rank["object_segmentation"] < rank["compile_manifest"]

"""Regional motion runs only in selected intervals and stays advisory."""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import regional_motion as rm  # noqa: E402


def _vision(actions):
    return {"actions": [{"window": [0, 10], "actions": actions}]}


def _face_presence(boxes, rate=5):
    return {"sample_rate_hz": rate, "face_boxes": boxes}


def test_only_time_bounded_gemma_action_rows_select_spans():
    spans = rm.action_candidate_spans(_vision([
        {"start": 2, "end": 3, "action": "gestures with a hand",
         "body_language": "leans forward"},
        {"start": 3.02, "end": 4, "action": "points to the screen",
         "body_language": ""},
        {"start": 6, "end": 10, "action": "outside clip",
         "body_language": ""},
        {"action": "untimed is not a span"},
    ]), duration=6)

    assert spans == [{
        "start": 2.0,
        "end": 4.0,
        "selected_by": "gemma_action",
        "action_labels": [
            "gestures with a hand", "leans forward", "points to the screen",
        ],
    }]
    # Generic speech and static posture do not trigger a footage sweep.
    assert rm.action_candidate_spans(_vision([
        {"start": 0, "end": 10, "action": "person speaking to camera",
         "body_language": "seated, hands visible"},
    ]), duration=10) == []


def test_scene_boundaries_split_selected_spans_without_expanding_them():
    split = rm.split_at_scene_boundaries(
        [{"start": 2.0, "end": 5.0, "selected_by": "gemma_action",
          "action_labels": ["waves"]}],
        [{"time": 0.0, "type": "start"},
         {"time": 3.25, "type": "scene_change"}],
    )
    assert [(row["start"], row["end"]) for row in split] == [
        (2.0, 3.25), (3.25, 5.0)]


def test_face_box_uses_the_existing_5hz_track():
    boxes = [[0.2, 0.1, 0.4, 0.4], [0.4, 0.2, 0.6, 0.5], None]
    assert rm.face_box_at(_face_presence(boxes), 0.1) == pytest.approx(
        [0.3, 0.15, 0.5, 0.45])
    assert rm.face_box_at(_face_presence(boxes), 0.4) == boxes[1]
    assert rm.face_box_at(_face_presence(boxes), 0.6) is None


def test_moving_region_is_separate_from_the_face_and_in_crop_evidence():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    rng = np.random.default_rng(12)
    patch = rng.integers(0, 256, size=(56, 72), dtype=np.uint8)
    frames = []
    for index in range(12):
        frame = np.zeros((rm.SAMPLE_HEIGHT, rm.SAMPLE_WIDTH), dtype=np.uint8)
        x = 420 + index * 12
        frame[230:286, x:x + 72] = patch
        frames.append(frame)
    faces = _face_presence([[0.24, 0.16, 0.43, 0.46]] * 6)
    result = rm.analyze_frames(
        frames, 0.0, faces, 16 / 9,
        {"start": 0.0, "end": 1.2, "selected_by": "gemma_action",
         "action_labels": ["moves a hand across the frame"]},
    )

    assert result["face_track"]["observations"]
    assert result["motion_tracks"]
    motion = result["motion_tracks"][0]
    assert motion["kind"] == "hand_body_motion_candidate"
    assert motion["envelope"][0] > 0.6
    assert result["face_track"]["observations"][0]["box"][0] < 0.5
    assert motion["track_id"] in result["crop_suggestion"][
        "preserve_region_ids"]
    assert any(row["region_id"] == motion["track_id"]
               for row in result["keep_clear_suggestions"])
    assert result["crop_suggestion"]["recommendation"] == (
        "no_dynamic_crop_needed")


def test_only_a_face_trajectory_suggests_a_dynamic_crop():
    """A stable face says so explicitly; a one-sample face-box outlier
    does not trigger a crop; a real trajectory still can. The decision
    view's summary carries no per-sample trajectories."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")

    def analyze(n_frames, boxes, end, label):
        frames = [np.zeros((rm.SAMPLE_HEIGHT, rm.SAMPLE_WIDTH), dtype=np.uint8)
                  for _ in range(n_frames)]
        return rm.analyze_frames(
            frames, 0.0, _face_presence(boxes), 16 / 9,
            {"start": 0.0, "end": end, "selected_by": "gemma_action",
             "action_labels": [label]})

    stable = analyze(10, [[0.32, 0.14, 0.5, 0.42]] * 5, 1.0, "speaking")
    assert stable["crop_suggestion"]["recommendation"] == (
        "no_dynamic_crop_needed")
    assert stable["crop_suggestion"]["dynamic_crop_needed"] is False
    compact = rm.compact_span(stable)
    assert "observations" not in compact["face"]
    assert "observations" not in compact

    boxes = [[0.32, 0.14, 0.5, 0.42] for _ in range(20)]
    boxes[10] = [0.32, 0.38, 0.5, 0.66]
    outlier = analyze(40, boxes, 4.0, "gestures briefly")
    assert outlier["crop_suggestion"]["recommendation"] == (
        "no_dynamic_crop_needed")
    assert outlier["crop_suggestion"]["face_center_drift"] < 0.08

    moving = analyze(12, [[0.2 + index * 0.1, 0.14, 0.36 + index * 0.1, 0.42]
                          for index in range(6)],
                     1.2, "turns toward the speaker")
    assert moving["crop_suggestion"]["recommendation"] == (
        "consider_dynamic_crop")


def test_no_gemma_action_means_no_video_decode(monkeypatch):
    def fail_if_decoded(*args, **kwargs):
        raise AssertionError("unselected footage must not be decoded")

    monkeypatch.setattr(rm, "analyze_candidate_span", fail_if_decoded)
    result = rm.build_analysis(
        "/not/read/without/a/candidate.mxf", duration=30,
        semantic_document={"actions": []},
        face_presence={}, scene_boundaries=[],
    )
    assert result["measurement_status"] == "no_candidates"
    assert result["spans"] == []
    assert "not run over the footage" in result["reason"]

"""Geometry transforms and deterministic spatial-grounding scoring."""
import pytest

from library.tools.analysis.object_segmentation import check_seed_boxes
from library.tools.spatial_grounding import (
    GroundingGeometryError,
    ImageTransform,
    box_iou,
    point_hit,
    point_tracker_seed,
    score_grounding,
    tracker_seed,
)


def test_resize_transform_round_trips_points_boxes_and_normalizes_for_source():
    transform = ImageTransform.resize(3840, 2160, 960, 540)
    source = transform.prepared_box_to_source((100, 50, 300, 250))
    assert source == (400, 200, 1200, 1000)
    assert transform.source_box_to_prepared(source) == (100, 50, 300, 250)
    assert transform.prepared_point_to_source((200, 100)) == (800, 400)
    assert transform.normalized_source_box((100, 50, 300, 250)) == pytest.approx(
        [400 / 3840, 200 / 2160, 1200 / 3840, 1000 / 2160])


def test_letterbox_transform_removes_padding_and_clips_source_edges():
    transform = ImageTransform.letterbox(1920, 1080, 640, 640)
    assert transform.scale_x == pytest.approx(1 / 3)
    assert transform.offset_y == pytest.approx(140)
    assert transform.prepared_box_to_source((0, 140, 640, 500)) == pytest.approx(
        (0, 0, 1920, 1080))


def test_letterbox_padding_only_prediction_is_refused():
    transform = ImageTransform.letterbox(1920, 1080, 640, 640)
    with pytest.raises(GroundingGeometryError, match="outside"):
        transform.prepared_box_to_source((20, 0, 80, 100))


def test_transform_rejects_invalid_scale_and_nonfinite_values():
    with pytest.raises(GroundingGeometryError):
        ImageTransform(100, 100, 50, 50, 0, 0.5)
    with pytest.raises(GroundingGeometryError):
        ImageTransform(100, 100, 50, 50, 0.5, 0.5, float("nan"), 0)


def test_tracker_seed_uses_original_source_coordinates_and_existing_contract():
    transform = ImageTransform.resize(4000, 3000, 1000, 750)
    result = tracker_seed(prepared_box=(100, 100, 300, 400),
                          transform=transform, label="red mug", category="cup",
                          detector="grounder-a", instance_id="mug-2",
                          evidence_id="frame-11-grounding",
                          frame_time_seconds=5.5)
    assert result["instance_id"] == "mug-2"
    assert result["source_frame"] == {"width": 4000, "height": 3000}
    parsed = check_seed_boxes([result["seed"]])
    assert parsed[0].box == pytest.approx([0.1, 0.1333, 0.3, 0.5333])
    # The seed names the frame it was measured on, so the tracker
    # prompts that frame and not the clip's first one (D-02).
    assert parsed[0].frame_time_seconds == 5.5
    assert parsed[0].frame_width == 4000
    assert parsed[0].frame_height == 3000


def test_point_box_and_instance_metrics_are_separate():
    report = score_grounding([
        {"task": "point", "gold": {"box": [0, 0, 10, 10]},
         "prediction": {"point": [5, 5]}},
        {"task": "box", "gold": {"box": [0, 0, 10, 10]},
         "prediction": {"box": [0, 0, 5, 10]}},
        {"task": "referring_expression", "gold": {
            "box": [0, 0, 10, 10], "instance_id": "left-person"},
         "prediction": {"box": [0, 0, 10, 10], "matched_instance_id": "right-person"}},
        {"task": "spatial_relationship", "gold": {"relation": "beside"},
         "prediction": {"relation": "beside"}},
    ])
    assert report["tasks"]["point"]["accuracy"] == 1
    assert report["tasks"]["box"]["accuracy"] == 1
    assert report["tasks"]["referring_expression"]["accuracy"] == 0
    assert report["tasks"]["spatial_relationship"]["accuracy"] == 1


def test_small_object_subset_uses_gold_area_not_prediction_area():
    row = {"task": "small_object", "gold": {
        "box": [10, 10, 20, 20], "image_size": [1000, 1000]},
        "prediction": {"box": [10, 10, 20, 20]}}
    report = score_grounding([row])
    assert report["small_object"] == {
        "correct": 1, "total": 1, "accuracy": 1.0,
        "max_area_fraction": 0.01}


def test_iou_and_point_geometry_refuse_degenerate_boxes():
    assert box_iou([0, 0, 10, 10], [5, 5, 15, 15]) == pytest.approx(1 / 7)
    assert point_hit((1, 1), (0, 0, 2, 2))
    with pytest.raises(GroundingGeometryError):
        box_iou([0, 0, 0, 10], [0, 0, 1, 1])


def test_point_tracker_seed_requires_explicit_radius_and_clamps_to_frame():
    transform = ImageTransform.resize(1000, 500, 200, 100)
    result = point_tracker_seed(prepared_point=(2, 2), transform=transform,
                                source_radius_px=20, label="cup", category="cup",
                                detector="point-model", instance_id="cup-1",
                                evidence_id="frame-1",
                                frame_time_seconds=0.0)
    assert result["seed"]["box"] == pytest.approx([0, 0, 0.03, 0.06])
    with pytest.raises(GroundingGeometryError, match="radius"):
        point_tracker_seed(prepared_point=(1, 1), transform=transform,
                           source_radius_px=0, label="cup", category="cup",
                           detector="point-model", instance_id="cup-1",
                           evidence_id="frame-1", frame_time_seconds=0.0)

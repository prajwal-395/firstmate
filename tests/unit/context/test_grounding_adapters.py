"""Strict parsing and coordinate-safe preparation for grounding adapters."""
import json
import subprocess

import pytest

from library.tools.grounding_adapters import (
    COORDINATE_PROMPT,
    GroundingAdapterError,
    ImageTransform,
    coordinates_to_source,
    parse_coordinate_json,
    predict_query,
    prepare_image,
    probe_dims,
    render_prompt,
)


def test_parse_accepts_visible_box_and_point():
    parsed = parse_coordinate_json(
        '{"visible": true, "box_1000": [10, 20, 300, 400], '
        '"point_1000": [150, 200]}')
    assert parsed == {"visible": True, "box_1000": [10, 20, 300, 400],
                      "point_1000": [150, 200]}


def test_parse_accepts_point_only_grounding_output():
    assert parse_coordinate_json(
        '{"visible": true, "point_1000": [25, 75], '
        '"all_points_1000": [[25, 75], [50, 60]]}') == {
            "visible": True, "point_1000": [25, 75],
            "all_points_1000": [[25, 75], [50, 60]]}


def test_parse_accepts_invisible_without_geometry():
    assert parse_coordinate_json('{"visible": false}') == {"visible": False}


def test_parse_refuses_out_of_range_and_degenerate_boxes():
    with pytest.raises(GroundingAdapterError):
        parse_coordinate_json('{"visible": true, "box_1000": [0, 0, 2000, 10],'
                              ' "point_1000": [1, 1]}')
    with pytest.raises(GroundingAdapterError):
        parse_coordinate_json('{"visible": true, "box_1000": [50, 50, 10, 10],'
                              ' "point_1000": [1, 1]}')
    with pytest.raises(GroundingAdapterError):
        parse_coordinate_json("no coordinates here")


def test_parse_refuses_surrounding_prose_and_multiple_json_objects():
    answer = '{"visible": true, "box_1000": [0, 0, 10, 10], "point_1000": [5, 5]}'
    with pytest.raises(GroundingAdapterError):
        parse_coordinate_json("Sure: " + answer)
    with pytest.raises(GroundingAdapterError):
        parse_coordinate_json(answer + answer)


def test_coordinates_map_from_normalized_prepared_space_to_source():
    transform = ImageTransform.letterbox(1920, 1080, 640, 640)
    geometry = coordinates_to_source(
        {"visible": True, "box_1000": [250, 218.75, 750, 781.25],
         "point_1000": [500, 500]}, transform)
    assert geometry["source_box"] == pytest.approx([480, 0, 1440, 1080])
    assert geometry["source_point"] == pytest.approx([960, 540])


def test_point_only_geometry_maps_to_source_without_inventing_box():
    transform = ImageTransform.resize(1920, 1080, 640, 360)
    geometry = coordinates_to_source(
        {"visible": True, "point_1000": [500, 250]}, transform)
    assert geometry == {"visible": True, "prepared_point": [320.0, 90.0],
                        "source_point": [960.0, 270.0]}


def test_prompt_is_frozen_and_names_both_outputs():
    assert "box_1000" in COORDINATE_PROMPT
    assert "point_1000" in COORDINATE_PROMPT
    assert "1000,1000" in COORDINATE_PROMPT


def test_render_prompt_substitutes_phrase_without_touching_json_braces():
    rendered = render_prompt("the {curious} cat")
    assert "the {curious} cat" in rendered
    assert '{"visible": true/false' in rendered


def _make_png(path, size="128x96"):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=1",
         "-frames:v", "1", str(path)],
        capture_output=True, encoding="utf-8", check=False)
    assert proc.returncode == 0, proc.stderr


def test_prepare_image_downscales_and_reports_exact_transform(tmp_path):
    source = tmp_path / "source.png"
    _make_png(source, "128x96")
    assert probe_dims(str(source)) == (128, 96)
    transform = prepare_image(str(source), str(tmp_path / "prep.png"), max_side=64)
    assert isinstance(transform, ImageTransform)
    assert (transform.source_width, transform.source_height) == (128, 96)
    assert max(transform.prepared_width, transform.prepared_height) == 64
    assert transform.prepared_point_to_source((0, 0)) == (0, 0)


def _query(frame_id="f1"):
    return {"query_id": "q1", "frame_id": frame_id, "phrase": "the thing"}


def _frame():
    return {"frame_id": "f1", "width": 128, "height": 96}


def test_predict_query_freezes_answer_and_timings(tmp_path):
    source = tmp_path / "source.png"
    _make_png(source, "128x96")
    transform = prepare_image(str(source), str(tmp_path / "prep.png"), max_side=64)
    record = predict_query(
        _query(), _frame(), str(tmp_path / "prep.png"), transform,
        lambda image, phrase: '{"visible": true, "box_1000": [0, 0, 1000, 1000],'
                              ' "point_1000": [500, 500]}')
    assert record["parsed"]["visible"] is True
    assert record["geometry"]["source_box"] == [0.0, 0.0, 128.0, 96.0]
    assert record["timings_s"]["infer_s"] >= 0
    assert "malformed" not in record


def test_predict_query_records_malformed_instead_of_repairing(tmp_path):
    source = tmp_path / "source.png"
    _make_png(source, "128x96")
    transform = prepare_image(str(source), str(tmp_path / "prep.png"), max_side=64)
    record = predict_query(_query(), _frame(), str(tmp_path / "prep.png"),
                           transform, lambda image, phrase: "somewhere left")
    assert "parsed" not in record
    assert record["malformed"]["reason"]


def test_predict_query_records_inference_failure_as_data(tmp_path):
    source = tmp_path / "source.png"
    _make_png(source, "128x96")
    transform = prepare_image(str(source), str(tmp_path / "prep.png"), max_side=64)

    def boom(image, phrase):
        raise RuntimeError("model blew up")

    record = predict_query(_query(), _frame(), str(tmp_path / "prep.png"),
                           transform, boom)
    assert "inference failed" in record["malformed"]["reason"]


def test_predictions_format_round_trips_through_json():
    payload = {"format": "ren_spatial_grounding_predictions/1", "predictions": []}
    assert json.loads(json.dumps(payload))["format"].startswith("ren_spatial")

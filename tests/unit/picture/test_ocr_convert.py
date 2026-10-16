"""Converters: runner outputs join the corpus for scoring (GEO-015..017).

The eval document carries corpus labels, split assignment and the
candidate's predictions together so `ocr_eval.evaluate` sees one
schema. Boxed predictions pass through when the runner carries them
(pipeline route); text-only runs keep `predictions: []` plus
`ocr_text` and score frame/numeric without claiming box accuracy.
No models, no footage.
"""
import json

from library.tools import ocr_apple_convert, ocr_mlx_convert

CORPUS = {
    "frames": [
        {"id": "clipa-00", "split": "calibration",
         "labels": [{"text": "$1,250.00",
                     "bbox": [0.1, 0.2, 0.3, 0.1],
                     "number_kind": "currency"}]},
        {"id": "clipa-01", "split": "held_out", "labels": []},
    ]
}


def test_mlx_convert_carries_text_and_split(tmp_path):
    mlx = {"candidate": "PaddleOCR-VL-1.6 MLX",
           "model_revision": "r1", "precision": "bf16",
           "license_class": "Apache-2.0",
           "cold_load_seconds": 1.0, "peak_rss_bytes": 10,
           "serialization_seconds": 0.1,
           "frames": [{"id": "clipa-00.png", "ocr_text": "$1,250.00",
                       "inference_seconds": 2.0},
                      {"id": "clipa-01.png", "ocr_text": "",
                       "inference_seconds": 1.0}]}
    doc = ocr_mlx_convert.convert(mlx, CORPUS)
    assert [f["id"] for f in doc["frames"]] == ["clipa-00", "clipa-01"]
    assert [f["split"] for f in doc["frames"]] == [
        "calibration", "held_out"]
    assert doc["frames"][0]["labels"][0]["number_kind"] == "currency"
    assert doc["frames"][0]["ocr_text"] == "$1,250.00"


def test_mlx_convert_passes_boxed_predictions_through():
    mlx = {"candidate": "PaddleOCR-VL-1.6 (official pipeline)",
           "model_revision": "r1",
           "frames": [{"id": "clipa-00.png", "ocr_text": "$1,250.00",
                       "inference_seconds": 3.0,
                       "predictions": [{"text": "$1,250.00",
                                        "confidence": 1.0,
                                        "bbox": [0.1, 0.2, 0.3, 0.1]}]}]}
    doc = ocr_mlx_convert.convert(mlx, CORPUS)
    assert doc["frames"][0]["predictions"][0]["bbox"] == [0.1, 0.2, 0.3, 0.1]


def test_apple_convert_maps_ids_and_boxes():
    apple = {"candidate": "Apple Vision OCR", "revision": "macOS",
             "precision": "default", "cold_inference_seconds": 1.0,
             "frames": [{"id": "clipa-00.jpg", "latencySeconds": 0.5,
                         "predictions": [{"text": "$1,250.00",
                                          "confidence": 0.9,
                                          "bbox": [0.1, 0.2, 0.3, 0.1]}]}]}
    doc = ocr_apple_convert.convert(apple, CORPUS)
    assert doc["frames"][0]["id"] == "clipa-00"
    assert doc["frames"][0]["predictions"][0]["confidence"] == 0.9
    assert doc["frames"][0]["inference_seconds"] == 0.5


def test_converted_documents_score(tmp_path):
    from docs.benchmarks.ocr_eval import evaluate

    mlx = {"candidate": "PaddleOCR-VL-1.6 MLX",
           "model_revision": "r1", "precision": "bf16",
           "license_class": "Apache-2.0",
           "frames": [{"id": "clipa-00.png", "ocr_text": "$1,250.00",
                       "inference_seconds": 2.0}]}
    scored = evaluate(ocr_mlx_convert.convert(mlx, CORPUS))
    cal = scored["splits"]["calibration"]
    assert cal["frame_exact_text_accuracy"] == 1
    assert cal["numeric_exact_match_in_document"] == 1

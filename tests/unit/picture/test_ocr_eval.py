from docs.benchmarks.ocr_eval import box_iou, character_error_rate, evaluate


def test_text_normalization_and_cer_keep_digits_exact():
    assert character_error_rate("$1,250.00", "$1,250.00") == 0
    assert character_error_rate("$1,250.00", "$1,280.00") > 0


def test_box_iou_uses_normalized_xywh():
    assert box_iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    assert box_iou([0, 0, 1, 1], [2, 2, 1, 1]) == 0


def test_evaluator_keeps_calibration_and_held_out_separate():
    document = {
        "candidate": "baseline", "model_revision": "current",
        "precision": "n/a", "license_class": "incumbent",
        "timing": {"cold_seconds": 2, "warm_seconds": 1,
                   "decode_seconds": 0.1, "infer_seconds": 0.8,
                   "serialize_seconds": 0.1},
        "memory_peak_bytes": 10, "provenance": {"source": "fixture"},
        "frames": [{"split": "calibration",
                    "labels": [{"text": "USD 5", "bbox": [0, 0, 1, 1],
                                "number_kind": "currency"}],
                    "predictions": [{"text": "USD 5", "bbox": [0, 0, 1, 1]}]},
                   {"split": "held_out", "labels": [], "predictions": []}],
    }
    score = evaluate(document)
    assert score["splits"]["calibration"]["numeric_exact_match_given_box_iou50"] == 1
    assert score["splits"]["held_out"]["frames"] == 1


def test_text_only_model_is_scored_without_claiming_box_accuracy():
    document = {
        "candidate": "vlm", "model_revision": "r1", "precision": "bf16",
        "license_class": "Apache-2.0", "cold_load_seconds": 4,
        "peak_rss_bytes": 100, "provenance": {},
        "frames": [{"split": "calibration", "labels": [
            {"text": "USD 5", "bbox": [0, 0, 1, 1],
             "number_kind": "currency"}], "predictions": [],
            "ocr_text": "USD 5"}],
    }
    scores = evaluate(document)["splits"]["calibration"]
    assert scores["frame_exact_text_accuracy"] == 1
    assert scores["numeric_exact_match_in_document"] == 1
    assert scores["detection_recall_iou50"] is None

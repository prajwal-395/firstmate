"""Score OCR frame predictions against blind agent annotations.

Input JSON is a list of frames. Each frame has ``id``, ``split`` and
``labels``/``predictions`` arrays containing text and normalized xywh boxes.
Optional label fields retain number kind and layout relations without mixing
them into the recognized string.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein character error rate, with empty-reference handling."""
    ref = normalize_text(reference).replace(" ", "")
    hyp = normalize_text(hypothesis).replace(" ", "")
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, left in enumerate(ref, 1):
        current = [i]
        for j, right in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1] / len(ref)


def box_iou(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = map(float, a)
    bx, by, bw, bh = map(float, b)
    inter = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0.0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _score_frames(frames: list[dict], split: str) -> dict:
    tp = fp = fn = 0
    exact = []
    cers = []
    matched_ious = []
    numeric_exact = []
    iou50 = 0
    bbox_predictions = 0
    text_frame_exact = []
    text_frame_cer = []
    numeric_instances = []
    numeric_document_exact = []
    category_counts: dict[str, int] = {}
    layout_scores: list[dict] = []
    total_labels = total_predictions = 0
    for frame in frames:
        if frame["split"] != split:
            continue
        labels = frame["labels"]
        predictions = frame["predictions"]
        total_labels += len(labels)
        total_predictions += len(predictions)
        ordered_labels = sorted(
            labels, key=lambda row: (
                int(row.get("reading_order", 0)),
                row.get("bbox", [0, 0])[1],
                row.get("bbox", [0, 0])[0]))
        expected_text = "\n".join(label["text"] for label in ordered_labels)
        recognized_text = frame.get("recognized_text", frame.get("ocr_text"))
        if recognized_text is None:
            recognized_text = "\n".join(
                prediction["text"] for prediction in predictions)
        text_frame_exact.append(
            normalize_text(expected_text) == normalize_text(recognized_text))
        text_frame_cer.append(character_error_rate(expected_text,
                                                   recognized_text))
        numeric_instances.extend(label for label in labels
                                 if label.get("number_kind"))
        folded_recognized = normalize_text(recognized_text)
        for label in labels:
            category = label.get("kind", "unspecified")
            category_counts[category] = category_counts.get(category, 0) + 1
            if label.get("number_kind"):
                numeric_document_exact.append(
                    normalize_text(label["text"]) in folded_recognized)
        bbox_predictions += sum("bbox" in prediction
                                for prediction in predictions)
        candidates = []
        for li, label in enumerate(labels):
            for pi, prediction in enumerate(predictions):
                if "bbox" not in label or "bbox" not in prediction:
                    continue
                overlap = box_iou(label["bbox"], prediction["bbox"])
                candidates.append((overlap, li, pi))
        used_labels: set[int] = set()
        used_predictions: set[int] = set()
        for overlap, li, pi in sorted(candidates, reverse=True):
            if overlap < 0.5 or li in used_labels or pi in used_predictions:
                continue
            used_labels.add(li)
            used_predictions.add(pi)
            label, prediction = labels[li], predictions[pi]
            matched_ious.append(overlap)
            iou50 += 1
            exact_match = normalize_text(label["text"]) == normalize_text(
                prediction["text"])
            exact.append(exact_match)
            cers.append(character_error_rate(label["text"], prediction["text"]))
            if label.get("number_kind"):
                numeric_exact.append(exact_match)
        tp += len(used_labels)
        fn += len(labels) - len(used_labels)
        fp += len(predictions) - len(used_predictions)
        if any("bbox" in row for row in labels):
            layout_scores.append(score_layout(labels, predictions))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    layout_f1 = [row["f1"] for row in layout_scores
                 if row["f1"] is not None]
    layout_expected = sum(row["relations_expected"] for row in layout_scores)
    return {
        "frames": sum(frame["split"] == split for frame in frames),
        "ground_truth_boxes": total_labels,
        "predicted_boxes": total_predictions,
        "detection_precision_iou50": precision if bbox_predictions else None,
        "detection_recall_iou50": recall if bbox_predictions else None,
        "detection_f1_iou50": (2 * precision * recall / (precision + recall)
                                if bbox_predictions and precision + recall
                                else None),
        "bbox_predictions": bbox_predictions,
        "frame_exact_text_accuracy": (sum(text_frame_exact) / len(text_frame_exact)
                                       if text_frame_exact else None),
        "frame_character_error_rate": (sum(text_frame_cer) / len(text_frame_cer)
                                        if text_frame_cer else None),
        "text_exact_match_given_box_iou50": (sum(exact) / len(exact)
                                               if exact else None),
        "character_error_rate_given_box_iou50": (sum(cers) / len(cers)
                                                    if cers else None),
        "mean_box_iou_matched": (sum(matched_ious) / len(matched_ious)
                                  if matched_ious else None),
        "numeric_instances": len(numeric_instances),
        "numeric_exact_match_in_document": (
            sum(numeric_document_exact) / len(numeric_document_exact)
            if numeric_document_exact else None),
        "label_categories": category_counts,
        "numeric_exact_match_given_box_iou50": (
            sum(numeric_exact) / len(numeric_exact) if numeric_exact else None),
        "iou50_matches": iou50,
        "layout_frames_scored": len(layout_scores),
        "layout_relations_expected": layout_expected,
        "layout_mean_f1": (sum(layout_f1) / len(layout_f1)
                           if layout_f1 else None),
    }


def _layout_relations(rows: list[dict]) -> set[tuple[str, str, str]]:
    """Structural pairwise relations between labeled boxes (GEO-021).

    Relations are geometric only - left_of, above, contains - never
    semantic (no "header", "value", "title"). A chart or table is
    scored on whether its cells keep their relative positions, not on
    whether a model named what the chart means (GEO-026).
    """
    relations: set[tuple[str, str, str]] = set()
    for i, first in enumerate(rows):
        for j, second in enumerate(rows):
            if i == j or "bbox" not in first or "bbox" not in second:
                continue
            ax, ay, aw, ah = first["bbox"]
            bx, by, bw, bh = second["bbox"]
            a_name = str(first.get("layout_id", first.get("text", i)))
            b_name = str(second.get("layout_id", second.get("text", j)))
            if ax + aw <= bx:
                relations.add((a_name, "left_of", b_name))
            if ay + ah <= by:
                relations.add((a_name, "above", b_name))
            if (ax >= bx and ay >= by
                    and ax + aw <= bx + bw and ay + ah <= by + bh):
                relations.add((a_name, "inside", b_name))
    return relations


def score_layout(labels: list[dict], predictions: list[dict]) -> dict:
    """Precision/recall over structural layout relations (GEO-021)."""
    expected = _layout_relations(labels)
    got = _layout_relations(predictions)
    if not expected:
        return {"relations_expected": 0, "relations_predicted": len(got),
                "precision": None, "recall": None, "f1": None}
    tp = len(expected & got)
    precision = tp / len(got) if got else 0.0
    recall = tp / len(expected)
    return {
        "relations_expected": len(expected),
        "relations_predicted": len(got),
        "relations_matched": tp,
        "precision": precision,
        "recall": recall,
        "f1": (2 * precision * recall / (precision + recall)
               if precision + recall else 0.0),
    }


def check_separation(document: dict) -> list[str]:
    """Refuse predictions that mix recognized text with meaning (GEO-026).

    Returns a list of violations; empty means clean. Recognized rows
    may carry `text`, `bbox`, `confidence`, `number_kind`,
    `layout_id`, `kind` and `reading_order`. Anything naming meaning -
    `meaning`, `interpretation`, `sentiment`, `intent`, `summary` -
    is a violation, wherever it sits.
    """
    violations = []
    banned = {"meaning", "interpretation", "sentiment", "intent",
              "summary", "emotion", "topic"}
    for frame in document.get("frames", []):
        for row in (frame.get("predictions") or []):
            found = banned & set(row)
            if found:
                violations.append(
                    f"{frame.get('id')}: prediction carries meaning "
                    f"fields {sorted(found)}")
    return violations


def evaluate(document: dict) -> dict:
    separation = check_separation(document)
    if separation:
        raise ValueError(
            "GEO-026 separation violated: " + "; ".join(separation))
    frames = document["frames"]
    inference_values = [frame["inference_seconds"] for frame in frames
                        if "inference_seconds" in frame]
    timing = document.get("timing", {
        "cold_seconds": document.get("cold_load_seconds",
                                     document.get("cold_inference_seconds")),
        "warm_seconds": (sum(inference_values[1:]) /
                         max(1, len(inference_values) - 1)
                         if inference_values else None),
        "decode_seconds": document.get("decode_seconds"),
        "infer_seconds": (sum(inference_values) / len(inference_values)
                          if inference_values else None),
        "serialize_seconds": document.get("serialization_seconds"),
    })
    model_peak_gb = max(
        (frame.get("peak_memory_gb", 0.0) for frame in frames), default=0.0)
    result = {
        "schema": "ren_ocr_benchmark/1",
        "candidate": document["candidate"],
        "model_revision": document["model_revision"],
        "precision": document["precision"],
        "license_class": document["license_class"],
        "timing": timing,
        "memory_peak_bytes": document.get(
            "memory_peak_bytes", document.get("peak_rss_bytes")),
        "model_peak_memory_gb": model_peak_gb or None,
        "splits": {split: _score_frames(frames, split)
                   for split in ("calibration", "held_out")},
        "layout": document.get("layout", {}),
        "provenance": document.get("provenance", {}),
    }
    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.input.read_text(encoding="utf-8")))
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()

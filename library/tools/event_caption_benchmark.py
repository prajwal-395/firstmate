"""Score timestamped event captions against adjudicated agent-label events.

This module scores frozen artifacts only. It does not run models, fetch
weights, choose prompts, or expose held-out labels to a tuning run.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

FORMAT = "ren_event_caption_events/1"
IOU_THRESHOLD = 0.3


class BenchmarkError(ValueError):
    """An event-caption artifact violates the benchmark contract."""


def _read(path: Path, expected_kind: str) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot read {path}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BenchmarkError(f"{path}: expected {FORMAT}")
    if doc.get("kind") != expected_kind:
        raise BenchmarkError(f"{path}: kind must be {expected_kind!r}")
    if not isinstance(doc.get("events"), list):
        raise BenchmarkError(f"{path}: events must be a list")
    return doc


def _validate_event(event: Any, path: Path, index: int, prediction: bool) -> None:
    where = f"{path}: events[{index}]"
    if not isinstance(event, dict):
        raise BenchmarkError(f"{where} must be an object")
    for key in ("event_id", "source_id", "action"):
        if not isinstance(event.get(key), str) or not event[key].strip():
            raise BenchmarkError(f"{where}.{key} must be a non-empty string")
    interval = event.get("interval")
    if not isinstance(interval, dict):
        raise BenchmarkError(f"{where}.interval must be an object")
    start, end = interval.get("start_seconds"), interval.get("end_seconds")
    if (isinstance(start, bool) or not isinstance(start, (int, float))
            or isinstance(end, bool) or not isinstance(end, (int, float))
            or start < 0 or end <= start):
        raise BenchmarkError(f"{where}.interval must be a positive [start,end) range")
    if prediction:
        evidence = event.get("evidence")
        if not isinstance(evidence, list):
            raise BenchmarkError(f"{where}.evidence must be a list")
        for item in evidence:
            if not isinstance(item, dict) or item.get("source_id") != event["source_id"]:
                raise BenchmarkError(f"{where}: evidence must name the event source")
            times = item.get("frame_times_seconds")
            if (not isinstance(times, list) or not times
                    or any(isinstance(t, bool) or not isinstance(t, (int, float))
                           or t < 0 for t in times)):
                raise BenchmarkError(f"{where}: evidence requires sampled frame times")


def _label(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _iou(left: dict[str, Any], right: dict[str, Any]) -> float:
    a, b = left["interval"], right["interval"]
    intersection = max(0.0, min(a["end_seconds"], b["end_seconds"])
                       - max(a["start_seconds"], b["start_seconds"]))
    union = max(a["end_seconds"], b["end_seconds"]) - min(
        a["start_seconds"], b["start_seconds"])
    return intersection / union if union > 0 else 0.0


def score_events(reference_doc: dict[str, Any], prediction_doc: dict[str, Any]) -> dict[str, Any]:
    """Greedily match exact normalized actions by source and temporal IoU."""
    for doc, kind in ((reference_doc, "references"),
                      (prediction_doc, "predictions")):
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            raise BenchmarkError(f"expected {FORMAT}")
        if doc.get("kind") != kind:
            raise BenchmarkError(f"kind must be {kind!r}")
    references = reference_doc["events"]
    predictions = prediction_doc["events"]
    for index, event in enumerate(references):
        _validate_event(event, Path("reference"), index, False)
    for index, event in enumerate(predictions):
        _validate_event(event, Path("prediction"), index, True)

    candidates = []
    for pi, prediction in enumerate(predictions):
        for ri, reference in enumerate(references):
            if (prediction["source_id"] == reference["source_id"]
                    and _label(prediction["action"]) == _label(reference["action"])):
                overlap = _iou(prediction, reference)
                if overlap >= IOU_THRESHOLD:
                    candidates.append((overlap, pi, ri))
    used_predictions: set[int] = set()
    used_references: set[int] = set()
    matches = []
    for overlap, pi, ri in sorted(candidates, key=lambda row: (-row[0], row[1], row[2])):
        if pi in used_predictions or ri in used_references:
            continue
        used_predictions.add(pi)
        used_references.add(ri)
        prediction, reference = predictions[pi], references[ri]
        pred_interval, ref_interval = prediction["interval"], reference["interval"]
        matches.append({
            "prediction_id": prediction["event_id"],
            "reference_id": reference["event_id"],
            "iou": overlap,
            "start_error_seconds": abs(pred_interval["start_seconds"]
                                        - ref_interval["start_seconds"]),
            "end_error_seconds": abs(pred_interval["end_seconds"]
                                      - ref_interval["end_seconds"]),
        })

    valid_evidence = []
    for event in predictions:
        start, end = event["interval"]["start_seconds"], event["interval"]["end_seconds"]
        valid_evidence.append(any(
            item["source_id"] == event["source_id"]
            and all(start <= t < end for t in item["frame_times_seconds"])
            for item in event["evidence"]))
    nref, npred, nmatch = len(references), len(predictions), len(matches)
    backed_matches = sum(valid_evidence[pi] for pi in used_predictions)
    mean = lambda key: (sum(row[key] for row in matches) / nmatch
                        if nmatch else None)
    return {
        "reference_events": nref,
        "predicted_events": npred,
        "matched_events": nmatch,
        "event_recall": nmatch / nref if nref else None,
        "description_precision": nmatch / npred if npred else None,
        "unsupported_claim_rate": (npred - backed_matches) / npred if npred else None,
        "evidence_coverage": sum(valid_evidence) / npred if npred else None,
        "timestamp_start_mae_seconds": mean("start_error_seconds"),
        "timestamp_end_mae_seconds": mean("end_error_seconds"),
        "matched_interval_iou": mean("iou"),
        "selection_note": "report only; this evaluator does not rank versions",
        "matches": matches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True,
                        help="adjudicated agent-label JSON")
    parser.add_argument("--predictions", type=Path, required=True,
                        help="frozen model event JSON")
    parser.add_argument("--out", type=Path, help="write JSON report")
    args = parser.parse_args()
    try:
        references = _read(args.references, "references")
        predictions = _read(args.predictions, "predictions")
        result = score_events(references, predictions)
    except BenchmarkError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

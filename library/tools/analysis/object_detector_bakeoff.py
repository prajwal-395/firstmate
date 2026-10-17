"""MU-15 step one: the Mac bakeoff of open-vocabulary detectors.

One slate, one ground truth, one score: Florence-2, OWLv2 and
Grounding DINO plus the class-agnostic Vision foreground baseline
(`open_vocabulary_detect.DETECTORS`) run over the same >= 30 captain
frames against Gemma's object names, and each answers time and recall.
The winner seeds SAM 2 tracks in step two; the numbers that pick it
are the doc this module writes.

Ground truth is a JSON file: `{"frames": [{"image": <name>,
"phrases": [...], "boxes": [{"phrase": ..., "box": [x0,y0,x1,y1]}]}]}`.
Phrases are Gemma's object labels for that frame (category object /
vehicle / text - `vision_pipeline_v3`'s objects pass); boxes are
hand-labelled on the frame by looking at it. A frame whose Gemma
pass named nothing is not a bakeoff frame: detectors asked about
nothing would all score zero for reasons that say nothing.

Recall: a ground-truth box is recalled when any predicted box for its
phrase overlaps it at IoU >= `BAKEOFF_IOU_THRESHOLD` (0.5). The Vision
baseline predicts no phrase, so ANY of its instances may cover each
box. Slow detectors are not re-run to fill a table: a backend that
refuses (`DetectorNotAvailable`) is recorded NOT-VIABLE with its
reason, the way the SAM3 bakeoff recorded triton.

Heavy: each detector loads its weights and every frame is a model
pass. Run under the heavy-work lock, one detector at a time, never
beside a pipeline run or render:

    bin/vep -m library.tools.heavy_work_lock run --owner mu15-bakeoff -- \\
        python3 -m library.tools.analysis.object_detector_bakeoff run \\
        --frames <dir> --ground-truth gt.json --out docs/benchmarks/...
"""

from __future__ import annotations

import argparse
import datetime
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from library.tools.analysis import open_vocabulary_detect as ovd

RUN_COMMAND = "run"


def check_ground_truth(doc: Any) -> list[dict]:
    """Bakeoff frames as a clean list. Refuses a doc with no scorable
    frame: an empty bakeoff would print a table of zeros that reads as
    a measurement."""
    if not isinstance(doc, dict) or not isinstance(doc.get("frames"), list):
        raise ValueError("ground truth is {frames: [...]}")
    frames = []
    for entry in doc["frames"]:
        if not isinstance(entry, dict):
            continue
        image = entry.get("image")
        phrases = [str(p).strip() for p in (entry.get("phrases") or [])
                   if str(p).strip()]
        boxes = [b for b in (entry.get("boxes") or [])
                 if isinstance(b, dict) and b.get("phrase") and b.get("box")]
        if not image or not phrases or not boxes:
            continue
        timestamp = entry.get("timestamp_s")
        if timestamp is None:
            raise ValueError(
                f"frame {image!r} is missing its source timestamp_s")
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"frame {image!r} has an invalid timestamp_s") from exc
        if timestamp < 0:
            raise ValueError(
                f"frame {image!r} has a negative timestamp_s")
        frames.append({"image": str(image), "timestamp_s": timestamp,
                       "phrases": phrases,
                       "boxes": [{"phrase": str(b["phrase"]),
                                  "box": [float(v) for v in b["box"]]}
                                 for b in boxes]})
    if not frames:
        raise ValueError("ground truth holds no scorable frame "
                         "(each needs an image, phrases and boxes)")
    return frames


def score_frame(boxes: Sequence[dict], predictions: Sequence[ovd.Detection],
                phrases: Sequence[str], *,
                iou_threshold: float = ovd.BAKEOFF_IOU_THRESHOLD) -> dict:
    """Which of one frame's ground-truth boxes the predictions recall.

    Pure: the unit tests score synthetic boxes through this, never a model.
    """
    # Maximum-cardinality bipartite matching prevents one prediction from
    # recalling several overlapping labels and counts duplicate detections
    # as false positives. A phraseless class-agnostic proposal may match any
    # class; a named prediction only matches the same phrase.
    edges: list[list[tuple[float, int]]] = []
    for entry in boxes:
        phrase = str(entry["phrase"])
        gt = [float(v) for v in entry["box"]]
        candidates = [
            (ovd.box_iou(pred.box, gt), index)
            for index, pred in enumerate(predictions)
            if pred.phrase == phrase or not pred.phrase
        ]
        edges.append(sorted(
            ((iou, index) for iou, index in candidates
             if iou >= iou_threshold), reverse=True))

    prediction_to_box: dict[int, int] = {}

    def assign(box_index: int, visited: set[int]) -> bool:
        for _iou, pred_index in edges[box_index]:
            if pred_index in visited:
                continue
            visited.add(pred_index)
            previous = prediction_to_box.get(pred_index)
            if previous is None or assign(previous, visited):
                prediction_to_box[pred_index] = box_index
                return True
        return False

    for box_index in range(len(boxes)):
        assign(box_index, set())

    box_to_prediction = {box_index: pred_index
                         for pred_index, box_index
                         in prediction_to_box.items()}
    rows = []
    by_class: dict[str, dict[str, int]] = {}
    for box_index, entry in enumerate(boxes):
        phrase = str(entry["phrase"])
        matched = box_to_prediction.get(box_index)
        best_iou = (ovd.box_iou(predictions[matched].box,
                                [float(v) for v in entry["box"]])
                    if matched is not None else 0.0)
        counts = by_class.setdefault(phrase, {"true_positives": 0,
                                              "false_positives": 0,
                                              "false_negatives": 0})
        if matched is None:
            counts["false_negatives"] += 1
        else:
            counts["true_positives"] += 1
        rows.append({"phrase": phrase, "best_iou": round(best_iou, 3),
                     "recalled": matched is not None,
                     "matched_prediction": matched})

    false_positive_indices = sorted(
        set(range(len(predictions))) - set(prediction_to_box))
    for index in false_positive_indices:
        phrase = predictions[index].phrase or "<class_agnostic>"
        by_class.setdefault(phrase, {"true_positives": 0,
                                     "false_positives": 0,
                                     "false_negatives": 0})[
                                         "false_positives"] += 1

    hits = len(prediction_to_box)
    false_positives = len(false_positive_indices)
    misses = len(boxes) - hits
    precision = hits / (hits + false_positives) if hits + false_positives else None
    recall = hits / len(boxes) if boxes else None
    return {"boxes": len(boxes), "predictions": len(predictions),
            "hits": hits, "misses": misses,
            "false_positives": false_positives,
            "false_positive_indices": false_positive_indices,
            "precision": precision, "recall": recall,
            "per_class": by_class, "rows": rows,
            "phrases": list(phrases)}


def run_bakeoff(frames_dir: str | Path, frames: Sequence[dict],
                detectors: Sequence[str] | None = None, *,
                score_threshold: float = ovd.DEFAULT_SCORE_THRESHOLD,
                class_thresholds: dict[str, float] | None = None,
                iou_threshold: float = ovd.BAKEOFF_IOU_THRESHOLD,
                ) -> dict:
    """Every detector over every frame: timings plus recall. Heavy (one
    model pass per frame per detector); callers hold the heavy-work lock.
    """
    frames_dir = Path(frames_dir)
    wanted = list(detectors) if detectors else list(ovd.DETECTORS)
    thresholds = dict(class_thresholds or {})
    if not 0.0 <= score_threshold <= 1.0:
        raise ValueError("score_threshold must be between 0 and 1")
    for phrase, threshold in thresholds.items():
        if not phrase.strip() or not 0.0 <= threshold <= 1.0:
            raise ValueError("class thresholds need a phrase and a value "
                             "between 0 and 1")
    detector_floor = min([score_threshold, *thresholds.values()])
    for name in wanted:
        ovd.check_detector(name)
    total_boxes = sum(len(f["boxes"]) for f in frames)
    results: dict[str, Any] = {
        "frames": len(frames),
        "ground_truth_boxes": total_boxes,
        "score_threshold": score_threshold,
        "class_thresholds": thresholds,
        "iou_threshold": iou_threshold,
        "detectors": {},
    }
    for name in wanted:
        row: dict[str, Any] = {"detector": name}
        try:
            t0 = time.perf_counter()
            load = ovd.load_detector(name)
            row["load_seconds"] = round(time.perf_counter() - t0, 1)
            row["device"] = load.get("device")
            row["model"] = load.get("model_id")
            row["model_revision"] = (load.get("revision")
                                     or ovd.published_revision(load))
        except ovd.DetectorNotAvailable as exc:
            row["viable"] = False
            row["reason"] = str(exc)
            results["detectors"][name] = row
            continue
        except Exception as exc:  # noqa: BLE001 - a backend that blows
            # up on load is NOT-VIABLE with its error, not a crash: the
            # table still answers for every other detector.
            row["viable"] = False
            row["reason"] = f"load failed: {type(exc).__name__}: {exc}"
            results["detectors"][name] = row
            continue
        per_frame_s: list[float] = []
        scored_boxes = scored_hits = 0
        scored_false_positives = 0
        class_counts: dict[str, dict[str, int]] = {}
        frame_records: list[dict[str, Any]] = []
        failed = 0
        for entry in frames:
            image = str(frames_dir / entry["image"])
            error = None
            t0 = time.perf_counter()
            try:
                preds = ovd.detect_image(
                    name, image, entry["phrases"],
                    score_threshold=detector_floor)
                preds = [pred for pred in preds if pred.score >=
                         thresholds.get(pred.phrase, score_threshold)]
            except Exception as exc:  # noqa: BLE001 - one frame's failure is
                # recorded on the row; the rest of the frames still run.
                failed += 1
                error = f"{type(exc).__name__}: {exc}"
                preds = []
            per_frame_s.append(time.perf_counter() - t0)
            scored = score_frame(entry["boxes"], preds, entry["phrases"],
                                 iou_threshold=iou_threshold)
            scored_boxes += scored["boxes"]
            scored_hits += scored["hits"]
            scored_false_positives += scored["false_positives"]
            for phrase, counts in scored["per_class"].items():
                target = class_counts.setdefault(
                    phrase, {"true_positives": 0, "false_positives": 0,
                             "false_negatives": 0})
                for key, value in counts.items():
                    target[key] += value
            frame_records.append({
                "image": entry["image"],
                "timestamp_s": entry["timestamp_s"],
                "error": error,
                "detections": [{
                    "class": detection.phrase,
                    "bbox_xyxy_normalized": detection.box,
                    "score": detection.score,
                    "producer": detection.detector or name,
                    "producer_model": load.get("model_id"),
                    "producer_revision": row["model_revision"],
                } for detection in preds],
                "false_positive_indices": scored[
                    "false_positive_indices"],
                "ground_truth": scored["rows"],
            })
        row["viable"] = True
        row["frames_scored"] = len(frames) - failed
        row["frames_failed"] = failed
        row["median_seconds_per_frame"] = (
            round(float(sorted(per_frame_s)[len(per_frame_s) // 2]), 2)
            if per_frame_s else None)
        row["total_seconds"] = round(sum(per_frame_s), 1)
        row["recall_boxes"] = scored_boxes
        row["recall_hits"] = scored_hits
        row["false_positives"] = scored_false_positives
        row["false_negatives"] = scored_boxes - scored_hits
        row["precision"] = (round(scored_hits / (
            scored_hits + scored_false_positives), 3)
            if scored_hits + scored_false_positives else None)
        row["recall"] = (round(scored_hits / scored_boxes, 3)
                         if scored_boxes else None)
        row["per_class"] = class_counts
        row["detections_by_frame"] = frame_records
        row["cold_seconds"] = round(row["load_seconds"] +
                                    (per_frame_s[0] if per_frame_s else 0), 3)
        row["end_to_end_seconds"] = round(
            row["load_seconds"] + sum(per_frame_s), 3)
        warm = per_frame_s[1:]
        row["warm_median_seconds_per_frame"] = (
            round(float(sorted(warm)[len(warm) // 2]), 3)
            if warm else None)
        serialize_started = time.perf_counter()
        json.dumps(frame_records, separators=(",", ":"))
        serialization_seconds = time.perf_counter() - serialize_started
        row["timing_breakdown_seconds"] = {
            "decode": None, "inference": None, "postprocess": None,
            "serialization": round(serialization_seconds, 6),
            "unmeasured_reason": (
                "Current detector adapters expose only combined per-image "
                "latency. Serialization measures the normalized frame "
                "records; decode, inference and postprocess are not split."),
        }
        if len(wanted) == 1:
            # macOS reports ru_maxrss in bytes; Linux reports KiB.
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            row["peak_rss_mb"] = round(
                peak_rss / (1024 * 1024 if sys.platform == "darwin"
                            else 1024), 2)
            row["peak_rss_scope"] = "single-detector process"
        else:
            row["peak_rss_mb"] = None
            row["peak_rss_scope"] = (
                "unmeasured: run one detector per process to isolate peak RSS")
        results["detectors"][name] = row
    return results


def winner(results: dict) -> str | None:
    """The detector step two seeds SAM 2 with: highest recall among the
    viable backends, ties to the faster median frame time. None when
    nothing viable ran - step two then has no seed and says so."""
    best: str | None = None
    best_key: tuple | None = None
    for name, row in (results.get("detectors") or {}).items():
        if not row.get("viable") or row.get("recall") is None:
            continue
        key = (row["recall"],
               -(row.get("median_seconds_per_frame") or float("inf")))
        if best_key is None or key > best_key:
            best_key, best = key, name
    return best


def render_markdown(results: dict, *, frames_source: str,
                    ground_truth_source: str,
                    gemma_source: str) -> str:
    """The bakeoff doc: numbers first, method after, so a reader that
    stops at the table still leaves with the verdict."""
    picked = winner(results)
    lines = [
        "# Object detector bakeoff: open-vocabulary seed for SAM 2",
        "",
        f"Date: {datetime.date.today().isoformat()}",
        f"Frames: {results['frames']} ({frames_source})",
        f"Ground-truth boxes: {results['ground_truth_boxes']} "
        f"({ground_truth_source})",
        f"Phrases: Gemma's object names ({gemma_source})",
        f"Score floor: {results['score_threshold']}; "
        f"recall at IoU >= {results['iou_threshold']}",
        "",
        "## Verdict",
        "",
    ]
    if picked is None:
        lines.append("No viable detector: step two has no seed.")
    else:
        row = results["detectors"][picked]
        lines.append(
            f"**{picked}** seeds SAM 2 in step two: recall "
            f"{row['recall']} ({row['recall_hits']}/{row['recall_boxes']} "
            f"boxes) at {row['median_seconds_per_frame']} s/frame median "
            f"on {row['device']}.")
    lines += ["", "## Per-model numbers", "",
              "| Detector | Viable | Cold (s) | Warm median (s/frame) | "
              "Peak RSS (MB) | Precision | Recall | False positives | "
              "False negatives |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ovd.DETECTORS:
        row = results["detectors"].get(name)
        if row is None:
            continue
        if not row.get("viable"):
            lines.append(f"| {name} | no ({row.get('reason')}) | - | - | "
                         "- | - | - | - | - |")
            continue
        lines.append(
            f"| {name} ({row.get('model')}, {row.get('device')}) | "
            f"yes | {row.get('cold_seconds')} | "
            f"{row.get('warm_median_seconds_per_frame')} | "
            f"{row.get('peak_rss_mb')} | {row.get('precision')} | "
            f"{row.get('recall')} | {row.get('false_positives')} | "
            f"{row.get('false_negatives')} |")
    lines += [
        "",
        "## Method",
        "",
        "- One slate, one ground truth, one score: every viable detector "
        "ran the same frames against the same phrases.",
        "- A ground-truth box is recalled when any predicted box for its "
        "phrase overlaps it at IoU >= "
        f"{results['iou_threshold']}; the Vision baseline predicts no "
        "phrase, so any of its foreground instances may cover each box.",
        "- A prediction can match at most one label. Duplicate and unmatched "
        "predictions count as false positives; missed labels count separately "
        "as false negatives.",
        "- A backend that refuses (`DetectorNotAvailable`) or fails to "
        "load is NOT-VIABLE with its reason, never a zero: the table "
        "tells 'cannot run' from 'ran, nothing there'.",
        "- Step two (the winner's boxes seeding SAM 2 tracks, which "
        "become object entities with `ObjectClass`) is scored separately, "
        "on mask coverage of the named object.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="object_detector_bakeoff",
        description="MU-15 step one: score the open-vocabulary detector "
                    "slate on captain frames against Gemma's object names.")
    sub = parser.add_subparsers(dest="command")
    p_run = sub.add_parser(RUN_COMMAND, help="run the slate, write the doc")
    p_run.add_argument("--frames", required=True,
                       help="directory holding the frame images")
    p_run.add_argument("--ground-truth", required=True,
                       help="GT JSON ({frames: [{image, phrases, boxes}]})")
    p_run.add_argument("--out", required=True,
                       help="markdown doc to write")
    p_run.add_argument("--frames-source", default="captain frames",
                       help="provenance line for the doc")
    p_run.add_argument("--ground-truth-source", default="hand-labelled",
                       help="provenance line for the doc")
    p_run.add_argument("--gemma-source", default="1.03 objects pass",
                       help="provenance line for the doc")
    p_run.add_argument("--detector", action="append", default=None,
                       dest="detectors",
                       help="score only this detector; repeatable")
    p_run.add_argument("--score-threshold", type=float,
                       default=ovd.DEFAULT_SCORE_THRESHOLD)
    p_run.add_argument("--class-threshold", action="append", default=[],
                       metavar="PHRASE=VALUE",
                       help="apply a calibrated score floor for one class; "
                       "repeatable")
    p_run.add_argument("--iou-threshold", type=float,
                       default=ovd.BAKEOFF_IOU_THRESHOLD)
    p_run.add_argument("--json-out",
                       help="write the full machine-readable results")
    args = parser.parse_args(argv)
    if args.command != RUN_COMMAND:
        parser.print_help()
        return 2
    try:
        with open(args.ground_truth, encoding="utf-8") as handle:
            frames = check_ground_truth(json.load(handle))
    except (OSError, ValueError) as exc:
        print(f"ground truth refused: {exc}", file=sys.stderr)
        return 1
    class_thresholds = {}
    for item in args.class_threshold:
        phrase, sep, value = item.partition("=")
        if not sep:
            print(f"invalid class threshold {item!r}: expected PHRASE=VALUE",
                  file=sys.stderr)
            return 2
        try:
            class_thresholds[phrase.strip()] = float(value)
        except ValueError:
            print(f"invalid class threshold {item!r}: value is not a number",
                  file=sys.stderr)
            return 2
    try:
        results = run_bakeoff(
            args.frames, frames, args.detectors,
            score_threshold=args.score_threshold,
            class_thresholds=class_thresholds,
            iou_threshold=args.iou_threshold)
    except ValueError as exc:
        print(f"benchmark settings refused: {exc}", file=sys.stderr)
        return 2
    doc = render_markdown(results, frames_source=args.frames_source,
                          ground_truth_source=args.ground_truth_source,
                          gemma_source=args.gemma_source)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    serialized = json.dumps(results, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
        frames.append({"image": str(image), "phrases": phrases,
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
    by_phrase: dict[str, list[ovd.Detection]] = {}
    for pred in predictions:
        by_phrase.setdefault(pred.phrase, []).append(pred)
    hits = misses = 0
    rows = []
    for entry in boxes:
        phrase = str(entry["phrase"])
        gt = [float(v) for v in entry["box"]]
        candidates = by_phrase.get(phrase, [])
        if not candidates and "" in by_phrase:
            candidates = by_phrase[""]
        best = max((ovd.box_iou(p.box, gt) for p in candidates),
                   default=0.0)
        recalled = best >= iou_threshold
        hits += recalled
        misses += (not recalled)
        rows.append({"phrase": phrase, "best_iou": round(best, 3),
                     "recalled": bool(recalled)})
    return {"boxes": len(boxes), "hits": hits, "misses": misses,
            "recall": (hits / len(boxes)) if boxes else None,
            "rows": rows, "phrases": list(phrases)}


def run_bakeoff(frames_dir: str | Path, frames: Sequence[dict],
                detectors: Sequence[str] | None = None, *,
                score_threshold: float = ovd.DEFAULT_SCORE_THRESHOLD,
                iou_threshold: float = ovd.BAKEOFF_IOU_THRESHOLD,
                ) -> dict:
    """Every detector over every frame: timings plus recall. Heavy (one
    model pass per frame per detector); callers hold the heavy-work lock.
    """
    frames_dir = Path(frames_dir)
    wanted = list(detectors) if detectors else list(ovd.DETECTORS)
    for name in wanted:
        ovd.check_detector(name)
    total_boxes = sum(len(f["boxes"]) for f in frames)
    results: dict[str, Any] = {
        "frames": len(frames),
        "ground_truth_boxes": total_boxes,
        "score_threshold": score_threshold,
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
        failed = 0
        for entry in frames:
            image = str(frames_dir / entry["image"])
            try:
                t0 = time.perf_counter()
                preds = ovd.detect_image(name, image, entry["phrases"],
                                         score_threshold=score_threshold)
                per_frame_s.append(time.perf_counter() - t0)
            except Exception:  # noqa: BLE001 - one frame's failure is
                # recorded on the row; the rest of the frames still run.
                failed += 1
                continue
            scored = score_frame(entry["boxes"], preds, entry["phrases"],
                                 iou_threshold=iou_threshold)
            scored_boxes += scored["boxes"]
            scored_hits += scored["hits"]
        row["viable"] = True
        row["frames_scored"] = len(per_frame_s)
        row["frames_failed"] = failed
        row["median_seconds_per_frame"] = (
            round(float(sorted(per_frame_s)[len(per_frame_s) // 2]), 2)
            if per_frame_s else None)
        row["total_seconds"] = round(sum(per_frame_s), 1)
        row["recall_boxes"] = scored_boxes
        row["recall_hits"] = scored_hits
        row["recall"] = (round(scored_hits / scored_boxes, 3)
                         if scored_boxes else None)
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
              "| Detector | Viable | Load (s) | Median s/frame | "
              "Recall | Hit / scored |", "|---|---|---|---|---|---|"]
    for name in ovd.DETECTORS:
        row = results["detectors"].get(name)
        if row is None:
            continue
        if not row.get("viable"):
            lines.append(f"| {name} | no ({row.get('reason')}) | - | - | "
                         f"- | - |")
            continue
        lines.append(
            f"| {name} | yes ({row.get('device')}; {row.get('model')}) | "
            f"{row.get('load_seconds')} | "
            f"{row.get('median_seconds_per_frame')} | {row.get('recall')} "
            f"| {row.get('recall_hits')}/{row.get('recall_boxes')} "
            f"(+{row.get('frames_failed')} failed frames) |")
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
    p_run.add_argument("--iou-threshold", type=float,
                       default=ovd.BAKEOFF_IOU_THRESHOLD)
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
    results = run_bakeoff(args.frames, frames, args.detectors,
                          score_threshold=args.score_threshold,
                          iou_threshold=args.iou_threshold)
    doc = render_markdown(results, frames_source=args.frames_source,
                          ground_truth_source=args.ground_truth_source,
                          gemma_source=args.gemma_source)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

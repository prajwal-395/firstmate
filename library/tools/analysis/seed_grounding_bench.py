"""D-02: the seed-grounding measurement.

A detector seed is a box measured on ONE frame of a clip. This harness
measures what happens to that box between the detector and the tracker
that inherits it:

1. the detector answers a phrase on the labelled frame,
2. the box becomes a seed, and the seed becomes a SAM 2 track,
3. the track's box is scored against the hand label on the labelled
   frame (and on the other labelled frames, for persistence).

The score is bounding-box IoU, the same measure the detector bakeoff
uses (`open_vocabulary_detect.BAKEOFF_IOU_THRESHOLD`).

The seed carries the frame it was measured on and that frame's pixel
size. Before the seed contract carried them, the seeder prompted the
tracker on the clip's FIRST frame whatever frame the box came from,
and the track was born on the wrong content - the tracker inherited a
seed that was never grounded. Run this harness on both revisions to
see the difference.

Heavy: the detector and the tracker are model passes. Run under the
heavy-work lock, never beside a pipeline run or a render:

    PY=$(bin/vep | sed -n 's/^PIPELINE_PYTHON=//p')
    bin/vep -m library.tools.heavy_work_lock run --owner d02-seed -- \\
        "$PY" -m library.tools.analysis.seed_grounding_bench \\
        --clip /path/to/scratch/copy.mov \\
        --labels docs/benchmarks/2026-10-21-seed-grounding-labels.json \\
        --out /tmp/seed-grounding.json

The source media is read-only: the harness decodes frames into the
caller's scratch directory and never writes to the clip.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from library.tools.analysis import open_vocabulary_detect as ovd

SEED_GROUNDING_FORMAT = "ren_seed_grounding_measurement/1"


def check_labels(doc: Any) -> dict[str, Any]:
    """The labelled sample, or a refusal naming what is missing.

    A sample with no frame is not a sample: it would print a table of
    zeros that reads as a measurement.
    """
    if not isinstance(doc, dict):
        raise TypeError(
            f"labels must be an object, got {type(doc).__name__}")
    clip = doc.get("clip") or {}
    obj = doc.get("object") or {}
    phrase = str(obj.get("label") or "").strip()
    if not phrase:
        raise ValueError("labels need an object.label (the detector phrase)")
    try:
        sample_fps = float(clip.get("sample_fps"))
    except (TypeError, ValueError) as exc:
        raise ValueError("labels need a clip.sample_fps") from exc
    if sample_fps <= 0:
        raise ValueError("clip.sample_fps must be positive")
    frames = []
    for entry in doc.get("frames") or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry["sample_index"])
            box = [float(v) for v in entry["box"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "every labelled frame needs a sample_index and a "
                "4-number box") from exc
        if index < 0:
            raise ValueError("sample_index must not be negative")
        if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("a labelled box needs positive area")
        frames.append({"sample_index": index,
                       "time_seconds": index / sample_fps,
                       "box": box})
    if not frames:
        raise ValueError("labels hold no frame")
    frames.sort(key=lambda row: row["sample_index"])
    return {"source_id": str(clip.get("source_id") or ""),
            "sample_fps": sample_fps,
            "label": phrase,
            "category": str(obj.get("category") or "object"),
            "frames": frames}


def decode_frames(clip: str, sample_fps: float, frames_dir: Path) -> list[Path]:
    """The clip's sample frames, exactly as the tracker samples them.

    The same ffmpeg recipe the segmenter uses (`fps=` filter, JPEG),
    so a detector box measured here is a box measured on the frame the
    tracker will prompt.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("*.jpg"):
        stale.unlink()
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(clip), "-vf", f"fps={sample_fps}", "-q:v", "2",
         str(frames_dir / "%05d.jpg")],
        capture_output=True, encoding="utf-8", check=True)
    frames = sorted(frames_dir.glob("*.jpg"))
    if not frames:
        raise RuntimeError(f"no frames decoded from {clip}")
    return frames


def _iou(left: list[float], right: list[float]) -> float:
    return ovd.box_iou(left, right)


def detect_seed(frames: list[Path], row: dict[str, Any],
                labels: dict[str, Any], detector: str,
                score_threshold: float,
                ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The detector's best box for the phrase on one labelled frame.

    The seed carries the frame it was measured on (`frame`) and that
    frame's pixel size: the seed is a box in a NAMED frame, and the
    seeder needs to know which.
    """
    index = row["sample_index"]
    frame = frames[index]
    detections = [d for d in ovd.detect_image(detector, str(frame),
                                               [labels["label"]])
                  if d.score >= score_threshold
                  and d.phrase.strip().lower() == labels["label"].lower()]
    if not detections:
        return None, {"detected": False, "reason": "no detection above the "
                                                  "score floor"}
    best = max(detections, key=lambda d: d.score)
    from PIL import Image
    with Image.open(frame) as opened:
        width, height = opened.size
    seed = {
        "box": list(best.box),
        "label": labels["label"],
        "category": labels["category"],
        "detector": detector,
        "score": best.score,
        "frame": {"time_seconds": row["time_seconds"],
                  "width": int(width), "height": int(height)},
    }
    return seed, {"detected": True, "score": best.score,
                  "box": list(best.box)}


def _normalized_bbox(box: list[float], width: int, height: int) -> list[float]:
    """A segmenter bbox (x, y, w, h) as a normalized [x1, y1, x2, y2].

    The segmenter stores the box's ORIGIN and its SIZE, so the far
    edge is the origin plus the size - reading it as x2/y2 would
    shrink every box to a corner.
    """
    x, y, w, h = (float(v) for v in box)
    return [x / width, y / height, (x + w) / width, (y + h) / height]


def run_measurement(clip: str, labels: dict[str, Any],
                    frames_dir: Path, detector: str = "owlv2",
                    score_threshold: float = 0.1) -> dict[str, Any]:
    """The measurement: detector box, seed, track, and their IoUs."""
    from library.tools.analysis.object_segmentation import get_segmenter

    frames = decode_frames(clip, labels["sample_fps"], frames_dir)
    rows = []
    segmenter = get_segmenter()
    segmenter.begin_run(include_generator=False)
    try:
        for label in labels["frames"]:
            index = label["sample_index"]
            seed, detection = detect_seed(frames, label, labels, detector,
                                          score_threshold)
            row = {"sample_index": index,
                   "time_seconds": label["time_seconds"],
                   "label_box": label["box"],
                   "detection": detection}
            if seed is None:
                row["seeded"] = False
                row["track_iou_at_label"] = None
                rows.append(row)
                continue
            row["seeded"] = True
            row["seed_box"] = seed["box"]
            row["detector_box_iou"] = round(
                _iou(seed["box"], label["box"]), 4)
            started = time.perf_counter()
            result = segmenter.segment_clip(
                clip, sample_fps=labels["sample_fps"], face_box=None,
                require_face=False, seed_boxes=[seed])
            row["segment_seconds"] = round(time.perf_counter() - started, 2)
            row["seed_note"] = result.seed_note
            tracked = [obj for obj in result.objects
                       if obj.label == labels["label"]]
            if not tracked:
                row["track_iou_at_label"] = 0.0
                row["seed_inherited_iou"] = 0.0
                row["track_note"] = ("the seed produced no track, so the "
                                     "named object is not tracked on the "
                                     "frame the box was measured on")
                rows.append(row)
                continue
            obj = tracked[0]
            row["track_object_id"] = obj.object_id
            row["seed_frame"] = getattr(obj, "seed_frame", None)
            row["track_frames"] = len(obj.frames)
            height, width = result.resolution
            scores = {}
            for other in labels["frames"]:
                other_index = other["sample_index"]
                box = obj.bboxes.get(other_index)
                if box is None:
                    # The track does not reach that frame: the object is
                    # not tracked there, which scores zero rather than
                    # being left out of the average.
                    scores[str(other_index)] = 0.0
                    continue
                normalized = _normalized_bbox(box, width, height)
                scores[str(other_index)] = round(
                    _iou(normalized, other["box"]), 4)
            row["track_iou_by_label"] = scores
            row["track_iou_at_label"] = scores.get(str(index), 0.0)
            seed_box = obj.bboxes.get(index)
            if seed_box is None:
                row["seed_inherited_iou"] = 0.0
            else:
                normalized = _normalized_bbox(seed_box, width, height)
                row["seed_inherited_iou"] = round(
                    _iou(normalized, seed["box"]), 4)
            row["track_boxes_head"] = [
                {"frame": frame, "box": list(box)}
                for frame, box in list(obj.bboxes.items())[:3]]
            rows.append(row)
    finally:
        segmenter.end_run()

    measured = [r for r in rows if r.get("track_iou_at_label") is not None]
    detector_ious = [r["detector_box_iou"] for r in rows
                     if r.get("detector_box_iou") is not None]
    inherited = [r["seed_inherited_iou"] for r in rows
                 if r.get("seed_inherited_iou") is not None]
    return {
        "format": SEED_GROUNDING_FORMAT,
        "clip": labels["source_id"],
        "detector": detector,
        "score_threshold": score_threshold,
        "object": labels["label"],
        "sample_fps": labels["sample_fps"],
        "frames": rows,
        "summary": {
            "labelled_frames": len(rows),
            "seeded_frames": sum(1 for r in rows if r.get("seeded")),
            "tracked_frames": len(measured),
            "detector_box_iou_mean": (
                round(sum(detector_ious) / len(detector_ious), 4)
                if detector_ious else None),
            "seed_inherited_iou_mean": (
                round(sum(inherited) / len(inherited), 4)
                if inherited else None),
            "track_box_iou_mean": (
                round(sum(r["track_iou_at_label"] for r in measured)
                      / len(measured), 4) if measured else None),
            "track_box_iou_at_or_above_0_5": (
                sum(1 for r in measured if r["track_iou_at_label"] >= 0.5)),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """The measurement, numbers first."""
    summary = report["summary"]
    lines = [
        "# Seed grounding measurement",
        "",
        (f"Clip: {report['clip']} - object: {report['object']} - "
         f"detector: {report['detector']} (floor "
         f"{report['score_threshold']})"),
        "",
        ("| Labelled frame | Time (s) | Detector box IoU | Seed inherited "
         "IoU | Track box IoU | Track frames | Seed frame |"),
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["frames"]:
        lines.append(
            f"| {row['sample_index']} | {row['time_seconds']:.2f} | "
            f"{_fmt(row.get('detector_box_iou'))} | "
            f"{_fmt(row.get('seed_inherited_iou'))} | "
            f"{_fmt(row.get('track_iou_at_label'))} | "
            f"{row.get('track_frames', '-')} | "
            f"{row.get('seed_frame', '-')} |")
    lines += [
        "",
        (f"Detector box IoU (mean, vs the hand label): "
         f"{summary['detector_box_iou_mean']}"),
        (f"Seed inherited IoU (mean, the tracker's box vs the seed's box): "
         f"{summary['seed_inherited_iou_mean']}"),
        (f"Track box IoU at the labelled frame (mean, vs the hand label): "
         f"{summary['track_box_iou_mean']} "
         f"({summary['track_box_iou_at_or_above_0_5']}/"
         f"{summary['tracked_frames']} at IoU >= 0.5)"),
    ]
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_grounding_bench",
        description="Measure a detector seed's grounding: the box, the "
                    "seed it becomes, and the track that inherits it.")
    parser.add_argument("--clip", required=True,
                        help="the scratch copy of the clip to measure")
    parser.add_argument("--labels", required=True,
                        help="the labelled sample JSON")
    parser.add_argument("--frames-dir", required=True,
                        help="scratch directory for the decoded frames")
    parser.add_argument("--out", required=True,
                        help="where the JSON report is written")
    parser.add_argument("--markdown-out", default="",
                        help="where the markdown report is written")
    parser.add_argument("--detector", default="owlv2")
    parser.add_argument("--score-threshold", type=float,
                        default=ovd.DEFAULT_SCORE_THRESHOLD)
    args = parser.parse_args(argv)
    try:
        with open(args.labels, encoding="utf-8") as handle:
            labels = check_labels(json.load(handle))
    except (OSError, ValueError, TypeError) as exc:
        print(f"labels refused: {exc}", file=sys.stderr)
        return 1
    try:
        report = run_measurement(args.clip, labels, Path(args.frames_dir),
                                 args.detector, args.score_threshold)
    except Exception as exc:  # noqa: BLE001 - a measurement that failed
        # is reported as a failure, never as a zero.
        print(f"measurement failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n",
                              encoding="utf-8")
    markdown = render_markdown(report)
    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

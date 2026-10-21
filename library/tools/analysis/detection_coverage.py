"""D-01: measured detection coverage across object categories, with denominators.

What this measures
------------------
For one labelled frame set, how much of each object category a detector
actually finds, and how much of what it answers is right. Every number
carries its denominator, and a category with no labelled instance is
reported as UNMEASURED rather than as a zero.

The measurement is class-conditioned: the detector is asked the corpus's
query vocabulary and a detection answers one phrase. A detection matches
a labelled instance when the phrases agree and the boxes overlap at IoU
>= the manifest's threshold, one-to-one. The class-agnostic foreground
proposal names no phrase and may match any instance.

The strata D-01 asks about are MEASURED from the geometry, not asserted
by the label: an instance is SMALL when its box area is under
`SMALL_AREA_FRACTION` of the frame, and PARTIAL when its box touches a
frame edge. "Unusual footage" is a property of the frame, declared in the
manifest.

Pure functions first: `validate_manifest`, `score_frame`, `coverage_report`
and `render_markdown` never touch a model or a file, and the unit tests
score synthetic boxes through them. The commands around them do the work:

    # re-extract the labelled frames from the source clips (ffmpeg)
    python3 -m library.tools.analysis.detection_coverage extract \\
        --manifest docs/benchmarks/2026-10-21-d01-detection-coverage-manifest.json \\
        --out-dir ~/.cache/ren-d01/frames/native

    # run the detectors over those frames (heavy: loads a model per run)
    python3 -m library.tools.analysis.detection_coverage detect \\
        --manifest <manifest> --frames <dir> --detector owlv2 \\
        --out ~/.cache/ren-d01/owlv2.json

    # score what the detectors answered
    python3 -m library.tools.analysis.detection_coverage report \\
        --manifest <manifest> --detections <a.json> [--detections <b.json>] \\
        --out docs/benchmarks/2026-10-21-d01-detection-coverage.json

    # draw the labels on the frames, to look at what was labelled
    python3 -m library.tools.analysis.detection_coverage annotate \\
        --manifest <manifest> --frames <dir> --out-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "ren_detection_coverage_manifest/1"
REPORT_SCHEMA = "ren_detection_coverage_report/1"

CLASSES = ("person", "hand", "phone", "laptop", "cup", "small_object")
"""The taxonomy D-01 names. `small_object` is a LABEL class for a
discrete object that is not one of the named ones; whether an instance
is small is measured separately, from its box."""

SMALL_AREA_FRACTION = 0.01
"""An instance counts as SMALL when its box area is under 1% of the
frame. Measured, so a labeler never has to say 'small'."""

PARTIAL_EDGE_FRACTION = 0.005
"""A box within 0.5% of a frame edge counts as PARTIAL: the object runs
out of frame."""

DEFAULT_IOU_THRESHOLD = 0.5
DEFAULT_SCORE_THRESHOLD = 0.1

CLASS_AGNOSTIC_PHRASE = ""
"""The foreground proposal names no phrase, so it may answer any class."""


# ── manifest ────────────────────────────────────────────────────────────

def _box(value: Any, where: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{where}: a box is four numbers, not {value!r}")
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: a box holds numbers, not {value!r}") from exc
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise ValueError(
            f"{where}: a box is normalized 0..1 with x0<x1 and y0<y1, "
            f"not {value!r}")
    return [x0, y0, x1, y1]


def validate_manifest(doc: Any) -> list[dict]:
    """The manifest's frames, checked. Refuses a manifest that would
    produce a table of zeros reading as a measurement."""
    if not isinstance(doc, dict):
        raise TypeError("the manifest is an object")
    schema = doc.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise ValueError(f"the manifest schema is {MANIFEST_SCHEMA!r}, "
                         f"not {schema!r}")
    frames = doc.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("the manifest holds no frames")
    out: list[dict] = []
    seen: set[str] = set()
    for index, frame in enumerate(frames):
        where = f"frame {index}"
        if not isinstance(frame, dict):
            raise TypeError(f"{where}: a frame is an object")
        frame_id = frame.get("frame_id")
        if not frame_id:
            raise ValueError(f"{where}: a frame needs a frame_id")
        if frame_id in seen:
            raise ValueError(f"{where}: frame_id {frame_id!r} is used twice")
        seen.add(frame_id)
        image = frame.get("image")
        if not image:
            raise ValueError(f"{where}: a frame needs an image name")
        source = frame.get("source") or {}
        if not source.get("path") or source.get("timestamp_s") is None:
            raise ValueError(
                f"{where}: a frame needs its source path and timestamp_s, "
                "so the frame can be re-extracted")
        objects = frame.get("objects")
        if not isinstance(objects, list):
            raise TypeError(f"{where}: objects is a list")
        labelled = []
        for o_index, obj in enumerate(objects):
            spot = f"{where} object {o_index}"
            if not isinstance(obj, dict):
                raise TypeError(f"{spot}: an object is an object")
            klass = obj.get("class")
            if klass not in CLASSES:
                raise ValueError(f"{spot}: class {klass!r} is not one of "
                                 f"{CLASSES}")
            query = obj.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"{spot}: an object needs the query phrase "
                                 "it was labelled for")
            box = _box(obj.get("box"), spot)
            labelled.append({"class": klass, "query": query.strip(),
                             "box": box})
        out.append({
            "frame_id": frame_id,
            "image": str(image),
            "unusual_footage": bool(frame.get("unusual_footage", False)),
            "source": source,
            "objects": labelled,
        })
    return out


def load_manifest(path: str | Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    frames = validate_manifest(doc)
    doc["frames"] = frames
    return doc


def query_phrases(manifest: dict) -> list[str]:
    """The corpus's query vocabulary, in first-seen order."""
    phrases: list[str] = []
    for frame in manifest["frames"]:
        for obj in frame["objects"]:
            if obj["query"] not in phrases:
                phrases.append(obj["query"])
    return phrases


# ── the measured strata ─────────────────────────────────────────────────

def box_area(box: Sequence[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def is_small(box: Sequence[float], *, fraction: float = SMALL_AREA_FRACTION
             ) -> bool:
    return box_area(box) < fraction


def is_partial(box: Sequence[float], *,
               edge: float = PARTIAL_EDGE_FRACTION) -> bool:
    return (box[0] <= edge or box[1] <= edge
            or box[2] >= 1.0 - edge or box[3] >= 1.0 - edge)


def instance_strata(obj: dict) -> dict:
    """The strata one instance falls in, measured from its box."""
    return {"small": is_small(obj["box"]),
            "partial": is_partial(obj["box"])}


# ── scoring ─────────────────────────────────────────────────────────────

def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def _phrase_matches(detection_phrase: str, query: str) -> bool:
    if detection_phrase == CLASS_AGNOSTIC_PHRASE:
        return True
    return detection_phrase.strip().lower() == query.strip().lower()


def score_frame(objects: Sequence[dict], detections: Sequence[dict], *,
                iou_threshold: float = DEFAULT_IOU_THRESHOLD
                ) -> dict:
    """Which labelled instances the detections recall, one-to-one.

    A detection answers one phrase and matches only an instance whose
    query phrase is the same; the class-agnostic proposal names no phrase
    and may match any instance. Matching is maximum-cardinality, so one
    prediction cannot recall two overlapping labels, and a duplicate
    detection is a false positive rather than a second hit.

    Pure: the unit tests score synthetic boxes through this.
    """
    edges: list[list[tuple[float, int]]] = []
    for obj in objects:
        candidates = [
            (_iou(det["box"], obj["box"]), index)
            for index, det in enumerate(detections)
            if _phrase_matches(str(det.get("phrase", "")), obj["query"])
        ]
        edges.append(sorted(
            ((iou, index) for iou, index in candidates
             if iou >= iou_threshold), reverse=True))

    prediction_to_object: dict[int, int] = {}

    def assign(object_index: int, visited: set[int]) -> bool:
        for _iou_value, det_index in edges[object_index]:
            if det_index in visited:
                continue
            visited.add(det_index)
            held = prediction_to_object.get(det_index)
            if held is None or assign(held, visited):
                prediction_to_object[det_index] = object_index
                return True
        return False

    for object_index in range(len(objects)):
        assign(object_index, set())

    matched_objects = sorted(prediction_to_object.values())
    matched_detections = sorted(prediction_to_object)
    return {
        "recalled_objects": matched_objects,
        "matched_detections": matched_detections,
        "missed_objects": [i for i in range(len(objects))
                           if i not in set(matched_objects)],
        "false_positives": [i for i in range(len(detections))
                            if i not in set(matched_detections)],
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    """A ratio with its denominator beside it. None means UNMEASURED:
    no instance existed, so the number would be a zero that reads as a
    measurement."""
    if denominator <= 0:
        return None
    return numerator / denominator


def coverage_report(manifest: dict, detections_by_frame: dict[str, list],
                    *, detector: str) -> dict:
    """Per-category coverage with denominators, for one detector."""
    iou_threshold = float(
        (manifest.get("matching") or {}).get("iou_threshold",
                                              DEFAULT_IOU_THRESHOLD))
    frames = manifest["frames"]
    classes = list(CLASSES)
    totals = {name: {"instances": 0, "recalled": 0, "detections": 0,
                     "true_positives": 0} for name in classes}
    # Which class a QUERY phrase belongs to, over the whole corpus: a
    # named detection that matched nothing is a false positive of the
    # class its own phrase names, not of whichever class happens to sit
    # first in the frame.
    phrase_class = {obj["query"]: obj["class"]
                    for frame in frames for obj in frame["objects"]}
    class_agnostic = {"proposals": 0, "matched": 0, "unmatched": 0}
    unclassified_false_positives = 0
    by_query: dict[str, dict] = {
        phrase: {"instances": 0, "recalled": 0, "detections": 0,
                 "true_positives": 0}
        for phrase in phrase_class}
    strata = {name: {"instances": 0, "recalled": 0}
              for name in ("small", "partial", "unusual_footage",
                           "studio_footage")}
    per_frame = []
    for frame in frames:
        objects = frame["objects"]
        detections = list(detections_by_frame.get(frame["frame_id"], []))
        result = score_frame(objects, detections, iou_threshold=iou_threshold)
        recalled = set(result["recalled_objects"])
        for index, obj in enumerate(objects):
            hit = index in recalled
            query_bucket = by_query[obj["query"]]
            query_bucket["instances"] += 1
            query_bucket["recalled"] += 1 if hit else 0
            bucket = totals[obj["class"]]
            bucket["instances"] += 1
            bucket["recalled"] += 1 if hit else 0
            flags = instance_strata(obj)
            for name in ("small", "partial"):
                if flags[name]:
                    strata[name]["instances"] += 1
                    strata[name]["recalled"] += 1 if hit else 0
            footage = ("unusual_footage" if frame["unusual_footage"]
                       else "studio_footage")
            strata[footage]["instances"] += 1
            strata[footage]["recalled"] += 1 if hit else 0
        for det_index in result["matched_detections"]:
            phrase = str(detections[det_index].get("phrase", ""))
            if phrase == CLASS_AGNOSTIC_PHRASE:
                # A class-agnostic proposal recalls an instance, but it
                # names no class, so it is no class's TRUE POSITIVE and
                # no class's FALSE POSITIVE either.
                class_agnostic["proposals"] += 1
                class_agnostic["matched"] += 1
                continue
            klass = phrase_class.get(phrase)
            if klass is None:
                continue
            totals[klass]["detections"] += 1
            totals[klass]["true_positives"] += 1
            by_query[phrase]["detections"] += 1
            by_query[phrase]["true_positives"] += 1
        for det_index in result["false_positives"]:
            phrase = str(detections[det_index].get("phrase", ""))
            if phrase == CLASS_AGNOSTIC_PHRASE:
                class_agnostic["proposals"] += 1
                class_agnostic["unmatched"] += 1
                continue
            klass = phrase_class.get(phrase)
            if klass is None:
                unclassified_false_positives += 1
                continue
            totals[klass]["detections"] += 1
            by_query[phrase]["detections"] += 1
        per_frame.append({
            "frame_id": frame["frame_id"],
            "unusual_footage": frame["unusual_footage"],
            "instances": len(objects),
            "recalled": len(result["recalled_objects"]),
            "detections": len(detections),
            "false_positives": len(result["false_positives"]),
        })

    by_class = {}
    for name, bucket in totals.items():
        instances = bucket["instances"]
        by_class[name] = {
            "instances": instances,
            "recalled": bucket["recalled"],
            "recall": _ratio(bucket["recalled"], instances),
            "detections": bucket["detections"],
            "true_positives": bucket["true_positives"],
            "false_positives": bucket["detections"] - bucket["true_positives"],
            "precision": _ratio(bucket["true_positives"], bucket["detections"]),
            "measured": instances > 0,
        }
    stratum_report = {}
    for name, bucket in strata.items():
        stratum_report[name] = {
            "instances": bucket["instances"],
            "recalled": bucket["recalled"],
            "recall": _ratio(bucket["recalled"], bucket["instances"]),
            "measured": bucket["instances"] > 0,
        }
    query_report = {}
    for phrase, bucket in by_query.items():
        query_report[phrase] = {
            "instances": bucket["instances"],
            "recalled": bucket["recalled"],
            "recall": _ratio(bucket["recalled"], bucket["instances"]),
            "detections": bucket["detections"],
            "true_positives": bucket["true_positives"],
            "false_positives": bucket["detections"] - bucket["true_positives"],
            "precision": _ratio(bucket["true_positives"], bucket["detections"]),
            "measured": bucket["instances"] > 0,
        }
    all_instances = sum(b["instances"] for b in totals.values())
    all_recalled = sum(b["recalled"] for b in totals.values())
    all_detections = sum(b["detections"] for b in totals.values())
    all_true = sum(b["true_positives"] for b in totals.values())
    return {
        "detector": detector,
        "frames_scored": len(frames),
        "instances": all_instances,
        "recalled": all_recalled,
        "recall": _ratio(all_recalled, all_instances),
        "detections": all_detections,
        "true_positives": all_true,
        "false_positives": all_detections - all_true,
        "precision": _ratio(all_true, all_detections),
        "iou_threshold": iou_threshold,
        "class_agnostic_proposals": class_agnostic,
        "unclassified_false_positives": unclassified_false_positives,
        "by_class": by_class,
        "by_query": query_report,
        "strata": stratum_report,
        "per_frame": per_frame,
    }


def _fmt(value: float | None, *, empty: str) -> str:
    """A ratio with its denominator beside it. None is UNMEASURED: there
    was nothing to divide, and a zero would read as a measurement."""
    if value is None:
        return empty
    return f"{value:.3f}"


def render_markdown(report: dict) -> str:
    """The report as a table a reader can check against the JSON."""
    lines = []
    for detector, measured in report["detectors"].items():
        lines.append(f"## {detector}")
        lines.append("")
        lines.append(
            f"{measured['frames_scored']} frames, "
            f"{measured['instances']} labelled instances, "
            f"{measured['detections']} detections, "
            f"IoU >= {measured['iou_threshold']}.")
        lines.append("")
        lines.append("| Category | Instances | Recalled | Recall | "
                     "Detections | True positives | Precision |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for name, bucket in measured["by_class"].items():
            if not bucket["measured"]:
                lines.append(
                    f"| {name} | 0 | - | UNMEASURED (no instances) | "
                    "- | - | - |")
                continue
            lines.append(
                f"| {name} | {bucket['instances']} | {bucket['recalled']} | "
                f"{_fmt(bucket['recall'], empty='n/a')} | "
                f"{bucket['detections']} | "
                f"{bucket['true_positives']} | "
                f"{_fmt(bucket['precision'], empty='n/a (no detections)')} |")
        lines.append("")
        agnostic = measured.get("class_agnostic_proposals") or {}
        if agnostic.get("proposals"):
            lines.append(
                f"Class-agnostic proposals: {agnostic.get('proposals')} "
                f"({agnostic.get('matched')} matched an instance, "
                f"{agnostic.get('unmatched')} matched none). They name no "
                "class, so they are no class's true positive and no class's "
                "false positive.")
            lines.append("")
        if measured.get("unclassified_false_positives"):
            lines.append(
                f"False positives whose phrase names no labelled class: "
                f"{measured['unclassified_false_positives']}.")
            lines.append("")
        lines.append("| Query phrase | Instances | Recalled | Recall | "
                     "Detections | True positives | Precision |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for phrase, bucket in (measured.get("by_query") or {}).items():
            if not bucket["measured"]:
                lines.append(
                    f"| {phrase} | 0 | - | UNMEASURED (no instances) | "
                    "- | - | - |")
                continue
            lines.append(
                f"| {phrase} | {bucket['instances']} | {bucket['recalled']} | "
                f"{_fmt(bucket['recall'], empty='n/a')} | "
                f"{bucket['detections']} | {bucket['true_positives']} | "
                f"{_fmt(bucket['precision'], empty='n/a (no detections)')} |")
        lines.append("")
        lines.append("| Stratum | Instances | Recalled | Recall |")
        lines.append("|---|---:|---:|---:|")
        for name, bucket in measured["strata"].items():
            if not bucket["measured"]:
                lines.append(f"| {name} | 0 | - | UNMEASURED (no instances) |")
                continue
            lines.append(
                f"| {name} | {bucket['instances']} | {bucket['recalled']} | "
                f"{_fmt(bucket['recall'], empty='n/a')} |")
        lines.append("")
    return "\n".join(lines) + "\n"


# ── the commands ────────────────────────────────────────────────────────

def _extract_frame(source: dict, destination: Path) -> None:
    timestamp = float(source["timestamp_s"])
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{timestamp}",
        "-i", str(source["path"]), "-frames:v", "1", "-q:v", "2",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True,
                            encoding="utf-8", check=False)
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(
            f"ffmpeg could not extract {source['path']} at {timestamp}s: "
            f"{result.stderr.strip()}")


def command_extract(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for frame in manifest["frames"]:
        destination = out_dir / frame["image"]
        if destination.exists() and not args.force:
            continue
        _extract_frame(frame["source"], destination)
        print(f"extracted {destination}")
    return 0


def command_detect(args: argparse.Namespace) -> int:
    from library.tools.analysis import open_vocabulary_detect as ovd

    manifest = load_manifest(args.manifest)
    frames_dir = Path(args.frames)
    phrases = query_phrases(manifest)
    out: dict[str, list] = {}
    for frame in manifest["frames"]:
        image = frames_dir / frame["image"]
        if not image.exists():
            raise SystemExit(f"missing frame {image}; run `extract` first")
        detections = ovd.detect_image(args.detector, str(image), phrases)
        out[frame["frame_id"]] = [
            {"phrase": d.phrase, "box": list(d.box), "score": d.score}
            for d in detections]
        print(f"{frame['frame_id']}: {len(detections)} detections")
    payload = {"detector": args.detector, "phrases": phrases,
               "detections": out}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1) + "\n",
                              encoding="utf-8")
    return 0


def command_report(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    detectors = {}
    for path in args.detections:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        name = payload.get("detector") or Path(path).stem
        detectors[name] = coverage_report(manifest, payload["detections"],
                                          detector=name)
    report = {
        "schema": REPORT_SCHEMA,
        "task": "D-01",
        "generated_at": datetime.datetime.now(
            datetime.UTC).isoformat(timespec="seconds"),
        "manifest": str(args.manifest),
        "frames": len(manifest["frames"]),
        "label_source": manifest.get("label_source"),
        "query_phrases": query_phrases(manifest),
        "detectors": detectors,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(report),
                                       encoding="utf-8")
    print(render_markdown(report))
    return 0


def command_annotate(args: argparse.Namespace) -> int:
    """Draw the labels on the frames, so a reader can LOOK at what was
    labelled instead of trusting the numbers."""
    manifest = load_manifest(args.manifest)
    frames_dir = Path(args.frames)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for frame in manifest["frames"]:
        image = frames_dir / frame["image"]
        if not image.exists():
            raise SystemExit(f"missing frame {image}")
        width = int((frame["source"] or {}).get("width") or 0)
        height = int((frame["source"] or {}).get("height") or 0)
        if not width or not height:
            raise SystemExit(f"{frame['frame_id']}: the manifest carries no "
                             "source size, so the annotation cannot be drawn")
        filters = []
        for obj in frame["objects"]:
            x0, y0, x1, y1 = obj["box"]
            filters.append(
                f"drawbox=x={int(x0 * width)}:y={int(y0 * height)}:"
                f"w={int((x1 - x0) * width)}:h={int((y1 - y0) * height)}:"
                f"t=6:color=red@0.9")
        destination = out_dir / frame["image"]
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i",
                   str(image)]
        if filters:
            command += ["-vf", ",".join(filters)]
        command += ["-frames:v", "1", "-q:v", "3", str(destination)]
        result = subprocess.run(command, capture_output=True,
                                encoding="utf-8", check=False)
        if result.returncode != 0:
            raise SystemExit(f"annotation failed for "
                             f"{frame['frame_id']}: {result.stderr.strip()}")
        print(f"annotated {destination}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.analysis.detection_coverage",
        description="Measured detection coverage across object categories.")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="re-extract the labelled frames")
    extract.add_argument("--manifest", required=True)
    extract.add_argument("--out-dir", required=True)
    extract.add_argument("--force", action="store_true")
    extract.set_defaults(func=command_extract)

    detect = sub.add_parser("detect", help="run a detector over the frames")
    detect.add_argument("--manifest", required=True)
    detect.add_argument("--frames", required=True)
    detect.add_argument("--detector", required=True,
                         choices=list(ovd_detector_names()))
    detect.add_argument("--out", required=True)
    detect.set_defaults(func=command_detect)

    report = sub.add_parser("report", help="score detections with denominators")
    report.add_argument("--manifest", required=True)
    report.add_argument("--detections", action="append", required=True)
    report.add_argument("--out", required=True)
    report.add_argument("--markdown")
    report.set_defaults(func=command_report)

    annotate = sub.add_parser("annotate", help="draw the labels on the frames")
    annotate.add_argument("--manifest", required=True)
    annotate.add_argument("--frames", required=True)
    annotate.add_argument("--out-dir", required=True)
    annotate.set_defaults(func=command_annotate)
    return parser


def ovd_detector_names() -> tuple[str, ...]:
    from library.tools.analysis import open_vocabulary_detect as ovd
    return tuple(ovd.DETECTORS)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

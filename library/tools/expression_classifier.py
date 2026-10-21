"""Expression classifier: M3c (`expressions.json`) of the per-source
footage memory (`docs/SOURCE_MEMORY.md`).

A lightweight expression classifier on M3 face crops. Reads M3 face
bounding boxes and lip landmarks, crops the matching cached M2 frame,
classifies expressions, and writes expression labels to a new slot.

The classifier uses a combination of:
- Geometric features from M3 lip landmarks (mouth curvature, openness)
- Image features from the face crop (edge density in eye/nose regions)

Expressions: neutral, happy, sad, angry, surprised, fearful, disgusted

This is a per-frame measurement at the M2 cadence, so it rides the existing
M3 sample. The classifier is lightweight (< 5ms per face crop) and uses
M3 landmarks and cached M2 frame images.

    python3 -m library.tools.expression_classifier build <project>
    python3 -m library.tools.expression_classifier status <project>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from library.tools import person_measurements, source_memory

M3C_STATUS_MEASURED = "measured"
M3C_METHOD = "expression-classifier-v2-m2-crops"

CLAIM_TYPES = {
    "expression": "image.expression_frame",
}
"""The claims this producer writes, and the registry type each one is
filed under. The class of a claim is a consequence of that type
(``claim_provenance``): the producer says how the value was produced,
and no reader re-guesses it from the value's shape."""

EXPRESSIONS = ("neutral", "happy", "sad", "angry", "surprised", "fearful",
               "disgusted")

SMILE_CURVATURE_THRESHOLD = 0.015
FROWN_CURVATURE_THRESHOLD = -0.015
SURPRISE_OPENNESS_THRESHOLD = 0.35
ANGER_EYE_DENSITY_THRESHOLD = 0.15
FEAR_EYE_DENSITY_THRESHOLD = 0.20
DISGUST_NOSE_DENSITY_THRESHOLD = 0.18

CROP_PADDING = 0.2
CROP_MIN_SIZE = 32


def _round(values, places: int = 4) -> list:
    return [round(float(v), places) for v in values]


def mouth_curvature(outer_lips: list[list]) -> float:
    """Mouth curvature from outer lip landmarks.

    Positive = smile (corners up), negative = frown (corners down).
    Normalised by mouth width so the value is scale-invariant.
    """
    if len(outer_lips) < 3:
        return 0.0
    xs = [p[0] for p in outer_lips]
    ys = [p[1] for p in outer_lips]
    left = min(range(len(xs)), key=lambda i: xs[i])
    right = max(range(len(xs)), key=lambda i: xs[i])
    width = xs[right] - xs[left]
    if width < 1e-6:
        return 0.0
    corner_y = (ys[left] + ys[right]) / 2.0
    center_y = sum(ys) / len(ys)
    return (center_y - corner_y) / width


def mouth_openness(outer_lips: list[list], inner_lips: list[list]) -> float:
    """Mouth openness as height/width ratio.

    High values indicate an open mouth (surprise). Normalised by mouth
    width so the value is scale-invariant.
    """
    if not outer_lips or not inner_lips:
        return 0.0
    xs = [p[0] for p in outer_lips]
    width = max(xs) - min(xs)
    if width < 1e-6:
        return 0.0
    inner_ys = [p[1] for p in inner_lips]
    height = max(inner_ys) - min(inner_ys)
    return height / width


def _image_features(crop) -> dict:
    """Compute image features from a cached BGR crop or PNG bytes.

    Returns edge density in the eye and nose regions.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"eye_edge_density": 0.0, "nose_edge_density": 0.0}
    if isinstance(crop, np.ndarray):
        img = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
               if crop.ndim == 3 else crop)
    else:
        arr = np.frombuffer(crop, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"eye_edge_density": 0.0, "nose_edge_density": 0.0}
    h, w = img.shape
    if h < CROP_MIN_SIZE or w < CROP_MIN_SIZE:
        return {"eye_edge_density": 0.0, "nose_edge_density": 0.0}
    edges = cv2.Canny(img, 50, 150)
    eye_region = edges[:h // 2, :]
    nose_region = edges[h // 3:2 * h // 3, w // 3:2 * w // 3]
    eye_density = float(np.count_nonzero(eye_region)) / max(1, eye_region.size)
    nose_density = float(np.count_nonzero(nose_region)) / max(1, nose_region.size)
    return {"eye_edge_density": round(eye_density, 4),
            "nose_edge_density": round(nose_density, 4)}


def _crop_face_from_frame(image, box: list[float],
                          frame_pixels: list[int]):
    """Crop normalized face coordinates from their cached M2 frame."""

    if image is None:
        return None
    fw, fh = frame_pixels
    if image.shape[1] != fw or image.shape[0] != fh:
        return None
    x1, y1, x2, y2 = box
    x = int(x1 * fw)
    y = int(y1 * fh)
    w = int((x2 - x1) * fw)
    h = int((y2 - y1) * fh)
    pad_x = int(w * CROP_PADDING)
    pad_y = int(h * CROP_PADDING)
    x = max(0, x - pad_x)
    y = max(0, y - pad_y)
    right = min(fw, x + w + 2 * pad_x)
    bottom = min(fh, y + h + 2 * pad_y)
    if right - x < CROP_MIN_SIZE or bottom - y < CROP_MIN_SIZE:
        return None
    return image[y:bottom, x:right]


def classify_expression(features: dict) -> tuple[str, float]:
    """Classify an expression from geometric and image features.

    Returns (expression, confidence).
    """
    curvature = features.get("mouth_curvature", 0.0)
    openness = features.get("mouth_openness", 0.0)
    eye_density = features.get("eye_edge_density", 0.0)
    nose_density = features.get("nose_edge_density", 0.0)

    if curvature > SMILE_CURVATURE_THRESHOLD:
        confidence = min(0.95, 0.7 + curvature * 10)
        return "happy", round(confidence, 3)
    if curvature < FROWN_CURVATURE_THRESHOLD:
        confidence = min(0.95, 0.7 + abs(curvature) * 10)
        return "sad", round(confidence, 3)
    if openness > SURPRISE_OPENNESS_THRESHOLD:
        confidence = min(0.95, 0.6 + openness * 2)
        return "surprised", round(confidence, 3)
    if nose_density > DISGUST_NOSE_DENSITY_THRESHOLD:
        confidence = min(0.9, 0.5 + nose_density * 3)
        return "disgusted", round(confidence, 3)
    if eye_density > FEAR_EYE_DENSITY_THRESHOLD:
        confidence = min(0.9, 0.5 + eye_density * 3)
        return "fearful", round(confidence, 3)
    if eye_density > ANGER_EYE_DENSITY_THRESHOLD:
        confidence = min(0.9, 0.5 + eye_density * 3)
        return "angry", round(confidence, 3)
    return "neutral", 0.5


def classify_face(outer_lips: list[list], inner_lips: list[list],
                  crop) -> dict:
    """Classify one face from its lip landmarks and optional crop image."""
    features = {
        "mouth_curvature": round(mouth_curvature(outer_lips), 4),
        "mouth_openness": round(mouth_openness(outer_lips, inner_lips), 4),
    }
    if crop is not None:
        features.update(_image_features(crop))
    else:
        features["eye_edge_density"] = 0.0
        features["nose_edge_density"] = 0.0
    expression, confidence = classify_expression(features)
    return {"expression": expression, "confidence": confidence,
            "features": features}


def build_source_expressions(source_file: str, content_digest: str,
                             root: Path | None = None) -> dict:
    """M3c for one source from its M3. Heavy: callers hold the heavy-work
    lock. Refuses (raises) without a fresh M3."""
    m3 = person_measurements.read_m3(content_digest, root)
    if m3 is None:
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no M3 (persons.json); "
            f"run `python3 -m library.tools.person_measurements build` first")
    frames = m3.get("frames", [])
    m2 = source_memory.read_m2(content_digest, root)
    if (m2 is None
            or not source_memory.m2_frames_present(content_digest, m2, root)):
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no M2 (frames.index.json); "
            "run the frames lane first")
    m2_frames = {round(frame["t"], 3): frame
                 for frame in m2["frames"]}
    frame_dir = source_memory.source_dir(content_digest, root)
    instrument = m3.get("instrument", {})
    frame_pixels = instrument.get("frame_pixels", [384, 216])
    started = time.perf_counter()
    out_frames = []
    faces_total = 0
    faces_classified = 0
    expression_counts: dict = {}
    image_cache = {}
    for frame in frames:
        t = frame.get("t", 0.0)
        frame_ref = m2_frames.get(round(t, 3))
        image = None
        if frame_ref is not None:
            frame_path = frame_dir / frame_ref["file"]
            if frame_ref["file"] not in image_cache:
                try:
                    import cv2
                    image_cache[frame_ref["file"]] = cv2.imread(
                        str(frame_path), cv2.IMREAD_COLOR)
                except ImportError:
                    image_cache[frame_ref["file"]] = None
            image = image_cache[frame_ref["file"]]
        out_faces = []
        for face in frame.get("faces", []):
            faces_total += 1
            box = face.get("box", [])
            outer_lips = face.get("outer_lips", [])
            inner_lips = face.get("inner_lips", [])
            if len(box) != 4 or not outer_lips:
                continue
            crop = _crop_face_from_frame(image, box, frame_pixels)
            result = classify_face(outer_lips, inner_lips, crop)
            result["box"] = _round(box)
            out_faces.append(result)
            faces_classified += 1
            expr = result["expression"]
            expression_counts[expr] = expression_counts.get(expr, 0) + 1
        out_frames.append({"t": t, "faces": out_faces})
    elapsed = time.perf_counter() - started
    record = {
        "content_digest": content_digest,
        "size_bytes": m3.get("size_bytes"),
        "source_file": os.path.abspath(source_file),
        "status": M3C_STATUS_MEASURED,
        "frame_count": len(out_frames),
        "faces_total": faces_total,
        "faces_classified": faces_classified,
        "expression_counts": expression_counts,
        "frames": out_frames,
        "instrument": {
            "method": M3C_METHOD,
            "expressions": list(EXPRESSIONS),
            "thresholds": {
                "smile_curvature": SMILE_CURVATURE_THRESHOLD,
                "frown_curvature": FROWN_CURVATURE_THRESHOLD,
                "surprise_openness": SURPRISE_OPENNESS_THRESHOLD,
                "anger_eye_density": ANGER_EYE_DENSITY_THRESHOLD,
                "fear_eye_density": FEAR_EYE_DENSITY_THRESHOLD,
                "disgust_nose_density": DISGUST_NOSE_DENSITY_THRESHOLD,
            },
            "crop_padding": CROP_PADDING,
            "crop_min_size": CROP_MIN_SIZE,
            "ms_per_face": round(1000.0 * elapsed / max(1, faces_classified), 2),
        },
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root)
        / source_memory.SLOT_EXPRESSIONS, record)
    return {"content_digest": content_digest,
            "source_file": os.path.abspath(source_file),
            "frames": len(out_frames),
            "faces_total": faces_total,
            "faces_classified": faces_classified,
            "expression_counts": expression_counts,
            **{k: record["instrument"][k] for k in ("ms_per_face",)}}


def read_m3c(content_digest: str, root: Path | None = None) -> dict | None:
    """A digest's M3c record, or None when never measured."""
    path = (source_memory.source_dir(content_digest, root)
            / source_memory.SLOT_EXPRESSIONS)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


@source_memory._project_scoped
def build_project_expressions(project_folder: str,
                              clip_ids: list[str] | None = None,
                              root: Path | None = None) -> dict:
    """M3c for every catalog source that carries M3."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    # The digest dedup is decided serially up front, as in the person
    # lanes: two clips of one file share a digest and the second
    # reports "reused" without measuring, and a shared `seen` set
    # cannot decide that across lanes. Digest resolution is read-only;
    # the lanes then measure only first occurrences, and the assembly
    # restores catalog order - identical to the serial loop.
    jobs, skipped = [], {}
    seen = set()
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            skipped[clip_id] = {"clip_id": clip_id, "source_file": path,
                                "skipped": "media-offline"}
            continue
        digest, _ = source_memory.digest_for_clip(project_folder, clip,
                                                  recorded)
        if digest is None or digest in seen:
            skipped[clip_id] = {"clip_id": clip_id, "source_file": path,
                                "skipped": "no content digest"
                                if digest is None else "reused"}
            continue
        seen.add(digest)
        jobs.append((clip_id, path, digest))

    # Pure rule-based classification over per-call data (ffmpeg crops,
    # numpy/cv2 features, threshold vocabulary): no shared model, so
    # the lanes need no warm-up and take no lock.
    from collections import deque

    from library.tools.concurrency_routing import run_lanes

    def _one(job):
        clip_id, path, digest = job
        try:
            account = build_source_expressions(path, digest, root)
        except Exception as exc:  # noqa: BLE001 - report this source and continue
            return ({"clip_id": clip_id, "content_digest": digest,
                     "failed": f"{type(exc).__name__}: {exc}"}, clip_id)
        return ({"clip_id": clip_id, **account}, None)

    results, failed = [], []
    measured = {}
    for row, failed_id in run_lanes(_one, jobs):
        measured.setdefault(row["clip_id"], deque()).append(row)
        if failed_id is not None:
            failed.append(failed_id)
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        queue = measured.get(clip_id)
        if queue:
            results.append(queue.popleft())
        elif clip_id in skipped:
            results.append(skipped[clip_id])
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="expression_classifier",
        description="M3c: expression labels per face per frame, classified "
                    "from M3 lip landmarks and face crops. Heavy; run "
                    "under the heavy-work lock.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M3c for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M3c")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_expressions(args.project, args.clip, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        doc = read_m3c(digest, root) if digest else None
        rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                     "m3c": "measured" if doc else "missing",
                     "faces_classified": doc.get("faces_classified") if doc else 0})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

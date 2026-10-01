#!/usr/bin/env python3
"""Apple Vision measurement for step 1.04, with a working Haar fallback.

Measures all faces (boxes), face landmarks, body pose, hand pose and a
person-segmentation summary at the step's face sampling rate, through a
compiled Swift helper (`vision_helper.swift` beside this file) rather
than pyobjc: no new pip dependencies, no import-time cost on machines
without it, and the call path is the harness the eval measured (34 ms
per frame warm for all five requests). The helper binary is compiled
once per source revision into a temp cache; anything that fails -
non-macOS, no swiftc, compile error, run error - returns the fallback
document, so Vision is never the reason a run goes red.

Owned output shape (new keys beside `face_presence`, never replacing):

- `vision_faces`: per-sample faces that survived the persistence
  filter, each with box, confidence and landmarks.
- `vision_face_candidates`: the unfiltered boxes, so the filter stays
  auditable (what it removed is counted in `vision_method`).
- `vision_body_pose` / `vision_hand_pose`: raw joint measurements,
  unfiltered - later work (the deferred hand-query study) reads these.
- `vision_person_mask`: per-sample coverage + bbox summary, never masks.
- `vision_method`: engine, fallback reason, timings, persistence params.

What is deliberately NOT here: a hand-over-mouth rule or label. The
eval (`eval/results.md`, comparison `haar-vs-vision-face-pose-1-04`)
proved the stated distance rule unworkable (recall 3/5, FP 99/265,
overlapping distributions, one finger-on-lips frame with no hand
detected at all). Raw hands and landmarks are stored so that study can
happen; the rule itself is not shipped.

The persistence filter drops single-sample phantom faces (a fist
detected as a face with landmarks is the measured case): a face is kept
iff a box with IoU >= `PERSISTENCE_IOU` exists at the previous or next
sample. Measured on 595 dense-sequence frames + 300 at the step's real
5 Hz: removes the sequence phantom, drops 2 fast-motion faces at 1 Hz
sampling, 0 at 5 Hz, at 6 us/frame.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SWIFT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "vision_helper.swift")

#: What the helper stamps a measured document with. A cached index whose
#: method is anything else predates the current producer and is backfilled.
VISION_METHOD = "vision-helper-v1"

#: The step's face sampling rate. Vision runs on the same samples the
#: Haar cascade sees, so the two never describe different moments.
VISION_SAMPLE_RATE_HZ = 5

#: A face survives iff a box with at least this IoU exists at an
#: adjacent sample. 0.3 is the measured knee (module docstring): it
#: removes the sequence phantom while costing nothing at 5 Hz.
PERSISTENCE_IOU = 0.3

#: Every key a measured document carries. The fallback document carries
#: the same keys empty, so no consumer branches on availability - the
#: same contract `test_face_presence_position` pins on `face_presence`.
VISION_KEYS = (
    "vision_faces",
    "vision_face_candidates",
    "vision_body_pose",
    "vision_hand_pose",
    "vision_person_mask",
    "vision_method",
)


class VisionUnavailable(Exception):
    """The helper cannot run here; the message is the fallback reason."""


def _helper_cache_dir() -> str:
    """Where the compiled helper lives, keyed by Swift source hash.

    System temp, outside every project and checkout: one compile per
    machine per source revision. A cleaned temp just recompiles.
    """
    with open(SWIFT_SOURCE, "rb") as handle:
        digest = hashlib.sha1(handle.read()).hexdigest()[:12]
    path = os.path.join(tempfile.gettempdir(), "ren-vision", digest)
    os.makedirs(path, exist_ok=True)
    return path


def helper_available() -> tuple:
    """Whether the helper can run on this machine, and why not.

    Returns `(True, None)` or `(False, reason)`. Needs macOS and either
    swiftc (to compile) or an already-compiled binary in the cache.
    """
    if sys.platform != "darwin":
        return False, f"unavailable: platform {sys.platform} has no Vision"
    cached = os.path.join(_helper_cache_dir(), "vision_helper")
    if os.path.isfile(cached) and os.access(cached, os.X_OK):
        return True, None
    if shutil.which("swiftc") is None:
        return False, "unavailable: no swiftc to compile the helper"
    return True, None


def ensure_helper() -> tuple:
    """A runnable helper binary path, compiling on first use.

    Returns `(path, None)` or `(None, reason)`. Never raises: a failed
    compile is a fallback, not a failed run.
    """
    try:
        ok, reason = helper_available()
        if not ok:
            return None, reason
        bindir = _helper_cache_dir()
        binary = os.path.join(bindir, "vision_helper")
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary, None
        if shutil.which("swiftc") is None:
            return None, "unavailable: no swiftc to compile the helper"
        result = subprocess.run(
            ["swiftc", "-O", SWIFT_SOURCE, "-o", binary],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, check=False,
        )
        if result.returncode != 0 or not os.path.isfile(binary):
            return None, (
                "unavailable: helper compile failed: "
                f"{result.stderr.strip()[-300:]}"
            )
        return binary, None
    except Exception as e:
        return None, f"unavailable: helper setup failed: {e}"


def measure_frames(image_paths: list, helper_path: str) -> list:
    """One helper process over the whole batch; first frame pays cold.

    Returns the helper's per-image docs in input order. Raises
    `VisionUnavailable` on any failure - the caller converts that into
    the fallback document.
    """
    if not image_paths:
        raise VisionUnavailable("unavailable: no frames to measure")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, encoding="utf-8") as handle:
        handle.write("\n".join(image_paths) + "\n")
        list_path = handle.name
    try:
        result = subprocess.run(
            [helper_path, "--list", list_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace",
            timeout=max(120.0, float(len(image_paths))),
            check=False,
        )
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    if result.returncode != 0:
        raise VisionUnavailable(
            f"unavailable: helper exited {result.returncode}: "
            f"{result.stderr.strip()[-300:]}"
        )
    try:
        docs = json.loads(result.stdout)
    except ValueError as e:
        raise VisionUnavailable(
            f"unavailable: helper returned non-JSON: {e}")
    if not isinstance(docs, list) or len(docs) != len(image_paths):
        raise VisionUnavailable(
            f"unavailable: helper returned {len(docs) if isinstance(docs, list) else '?'} "
            f"docs for {len(image_paths)} frames"
        )
    return docs


def _iou(a: list, b: list) -> float:
    """Intersection over union of two [x1, y1, x2, y2] boxes."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix1 - ix0, iy1 - iy0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    union = aa + bb - inter
    return inter / union if union > 0 else 0.0


def apply_persistence(faces_per_sample: list,
                      iou_thresh: float = PERSISTENCE_IOU) -> tuple:
    """Keep faces with temporal support; drop one-sample phantoms.

    `faces_per_sample` is a list per sample of boxes. Returns
    `(kept, removed_counts)`: `kept[i][j]` says whether face j at sample
    i survives (a box with IoU >= thresh at either neighbor), and
    `removed_counts[i]` is how many sample i lost. Sequence ends consult
    their single neighbor. Pure function of boxes - no thresholds on
    confidence, which the eval proved cannot separate phantoms anyway.
    """
    n = len(faces_per_sample)
    kept = []
    removed = []
    for i, boxes in enumerate(faces_per_sample):
        row = []
        for box in boxes:
            best = 0.0
            for j in (i - 1, i + 1):
                if 0 <= j < n:
                    for other in faces_per_sample[j]:
                        support = _iou(box, other)
                        if support > best:
                            best = support
            row.append(best >= iou_thresh)
        kept.append(row)
        removed.append(sum(1 for keep in row if not keep))
    return kept, removed


def empty_vision_doc(reason: str, sample_rate_hz: int) -> dict:
    """The fallback: every key present, measurements empty, reason said.

    Written (not skipped) so the next run can tell "measured nothing"
    from "never measured" by the method stamp.
    """
    doc = {}
    for key in VISION_KEYS:
        if key == "vision_method":
            continue
        doc[key] = {"sample_rate_hz": sample_rate_hz, "samples": []}
    doc["vision_method"] = {
        "engine": "none",
        "fallback": reason,
        "input": None,
        "persistence_iou": PERSISTENCE_IOU,
        "faces_removed_by_persistence": 0,
        "samples": 0,
        "request_ms": None,
        "helper_sha": None,
    }
    return doc


def _round_box(box: list) -> list:
    return [round(float(v), 4) for v in box]


def _round_point(point: list) -> list:
    return [round(float(v), 4) for v in point]


def measure_clip_vision(video_path: str, sample_w: int, sample_h: int,
                        sample_rate_hz: int = VISION_SAMPLE_RATE_HZ) -> dict:
    """All-faces Vision measurement for one clip. Never raises.

    Samples the clip at the given geometry (the caller passes the
    step's own `face_sample_dimensions`, so Vision sees what Haar saw),
    batches one helper process over every frame, pairs landmarks to
    faces by box overlap, filters one-sample phantoms, and returns the
    six `VISION_KEYS`. Any failure - helper, ffmpeg, parse - returns
    `empty_vision_doc` with the reason, so the step stays green.
    """
    try:
        helper, reason = ensure_helper()
        if helper is None:
            return empty_vision_doc(reason, sample_rate_hz)
        with open(SWIFT_SOURCE, "rb") as handle:
            sha = hashlib.sha1(handle.read()).hexdigest()[:12]

        workdir = tempfile.mkdtemp(prefix="ren-vision-frames-")
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", video_path,
                    "-vf", f"fps={sample_rate_hz},scale={sample_w}:{sample_h}",
                    "-f", "image2", "-q:v", "2",
                    os.path.join(workdir, "f_%05d.jpg"),
                ],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600, check=False,
            )
            paths = sorted(
                os.path.join(workdir, name)
                for name in os.listdir(workdir)
                if name.endswith(".jpg")
            )
            if result.returncode != 0 or not paths:
                return empty_vision_doc(
                    "unavailable: frame sampling failed: "
                    f"{result.stderr.strip()[-200:]}",
                    sample_rate_hz,
                )
            docs = measure_frames(paths, helper)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        return _assemble(docs, sample_rate_hz, sha,
                         f"{sample_w}x{sample_h}")
    except VisionUnavailable as refused:
        return empty_vision_doc(str(refused), sample_rate_hz)
    except Exception as e:
        return empty_vision_doc(f"unavailable: {e}", sample_rate_hz)


def _assemble(docs: list, sample_rate_hz: int, sha: str,
              geometry: str) -> dict:
    """Helper docs into the six owned keys, persistence filter applied."""
    candidates = []
    for doc in docs:
        faces = []
        for face in doc.get("faces") or []:
            box = face.get("box") or []
            if len(box) != 4:
                continue
            faces.append({
                "box": _round_box(box),
                "confidence": round(float(face.get("confidence", 0.0)), 3),
            })
        candidates.append(faces)

    kept, removed = apply_persistence(
        [[face["box"] for face in sample] for sample in candidates],
    )

    # Landmarks pair to faces by box overlap (the two requests observe
    # independently, so counts can differ); an unpaired landmark set is
    # dropped rather than attached to the wrong face.
    face_samples = []
    for doc, sample, keep in zip(docs, candidates, kept):
        lm_by_box = []
        for lm in doc.get("landmarks") or []:
            box = lm.get("box") or []
            if len(box) != 4:
                continue
            regions = {}
            for region, points in lm.items():
                if region == "box" or not points:
                    continue
                regions[region] = [_round_point(p) for p in points]
            if regions:
                lm_by_box.append((_round_box(box), regions))
        faces = []
        for face, keep_face in zip(sample, keep):
            if not keep_face:
                continue
            best, best_regions = 0.0, {}
            for lm_box, regions in lm_by_box:
                overlap = _iou(face["box"], lm_box)
                if overlap > best:
                    best, best_regions = overlap, regions
            entry = {"box": face["box"], "confidence": face["confidence"]}
            if best >= 0.5 and best_regions:
                entry["landmarks"] = best_regions
            faces.append(entry)
        face_samples.append(faces)

    body_samples = []
    hand_samples = []
    mask_samples = []
    ms_totals: dict = {}
    ms_counts: dict = {}
    for doc in docs:
        bodies = []
        for person in doc.get("bodies") or []:
            joints = person.get("joints") or {}
            if not joints:
                continue
            bodies.append({
                "joints": {
                    name: [round(float(j[0]), 4), round(float(j[1]), 4),
                           round(float(j[2]), 3)]
                    for name, j in joints.items()
                    if isinstance(j, list) and len(j) == 3
                }
            })
        body_samples.append(bodies)
        hands = []
        for hand in doc.get("hands") or []:
            joints = hand.get("joints") or {}
            if not joints:
                continue
            hands.append({
                "chirality": hand.get("chirality", "unknown"),
                "confidence": round(float(hand.get("confidence", 0.0)), 3),
                "joints": {
                    name: [round(float(j[0]), 4), round(float(j[1]), 4),
                           round(float(j[2]), 3)]
                    for name, j in joints.items()
                    if isinstance(j, list) and len(j) == 3
                },
            })
        hand_samples.append(hands)
        seg = doc.get("segmentation") or {}
        mask_samples.append({
            "coverage": round(float(seg.get("coverage", 0.0)), 4),
            "bbox": (_round_box(seg["bbox"])
                     if isinstance(seg.get("bbox"), list)
                     and len(seg["bbox"]) == 4 else None),
        })
        for key in ("face_ms", "landmark_ms", "body_ms", "hand_ms",
                    "seg_ms"):
            value = doc.get(key)
            if isinstance(value, (int, float)):
                ms_totals[key] = ms_totals.get(key, 0.0) + float(value)
                ms_counts[key] = ms_counts.get(key, 0) + 1

    removed_total = sum(removed)
    return {
        "vision_faces": {
            "sample_rate_hz": sample_rate_hz,
            "samples": face_samples,
        },
        "vision_face_candidates": {
            "sample_rate_hz": sample_rate_hz,
            "samples": [
                [{"box": face["box"], "confidence": face["confidence"]}
                 for face in sample]
                for sample in candidates
            ],
        },
        "vision_body_pose": {
            "sample_rate_hz": sample_rate_hz,
            "samples": body_samples,
        },
        "vision_hand_pose": {
            "sample_rate_hz": sample_rate_hz,
            "samples": hand_samples,
        },
        "vision_person_mask": {
            "sample_rate_hz": sample_rate_hz,
            "samples": mask_samples,
        },
        "vision_method": {
            "engine": VISION_METHOD,
            "fallback": None,
            "input": geometry,
            "persistence_iou": PERSISTENCE_IOU,
            "faces_removed_by_persistence": removed_total,
            "samples": len(docs),
            "request_ms": {
                key: round(ms_totals[key] / ms_counts[key], 1)
                for key in ms_totals
            } or None,
            "helper_sha": sha,
        },
    }

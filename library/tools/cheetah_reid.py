"""InsightFace 2.1 `PersonAnalysis` (cheetah) research adapter (lane vep-mu-reid).

AUD-043 (newer InsightFace body ReID in permitted research), AUD-046
(body appearance when faces are absent) and the research half of
AUD-050. RESEARCH-ONLY: the cheetah packs' `MODEL.LICENSE` grants
non-commercial use (`cheetah_s-public-v1`), and the model registry
records `insightface.buffalo_l` as rejected for commercial workloads.
This module is benchmark-only: production (`person_entity`) never
imports it (pinned by
`tests/contracts/test_no_cheetah_in_production.py`), every entry point
requires explicit `research_ack=True`, and the downloaded weights live
outside every checkout and outside the shared insightface cache.

Runs in a scratch interpreter with `insightface>=2.1` (the shared ML
venv pins 2.0, which has no `PersonAnalysis`); `insightface` is
imported lazily so this module imports everywhere, including the test
collector. Frame decode mirrors `person_entity.extract_frame`
(`ffmpeg -ss` fast seek, same width cap) so cheetah sees
production-equivalent input; cheetah's own internal resizing is its
measured behavior, not hidden preprocessing.

Pair statistics use the same definitions as
`face_identity_study.pair_report` (FAR = different-person pairs >= t
over all different-person pairs; FRR = same-person pairs < t over all
same-person pairs; same threshold grid); the equivalence is pinned by
`tests/unit/picture/test_cheetah_reid.py` on synthetic embeddings.

Heavy (decode + CPU inference + first-use download): run under the
heavy-work lock with the scratch interpreter.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence

FRAME_INPUT_MAX_WIDTH = 1408
"""Decode width cap, identical to `person_entity.FACE_INPUT_MAX_WIDTH`,
so the research pack sees production-equivalent input."""

THRESHOLD_GRID = (
    0.10,
    0.12,
    0.15,
    0.18,
    0.20,
    0.22,
    0.25,
    0.27,
    0.30,
    0.32,
    0.35,
    0.40,
    0.45,
    0.55,
    0.65,
    0.75,
    0.85,
    0.90,
)
"""The incumbent grid extended to the vendor's body default (0.85)."""

VENDOR_FACE_THRESHOLD = 0.45
VENDOR_BODY_THRESHOLD = 0.85
"""InsightFace 2.1 `PersonAnalysis` defaults. Published, not calibrated:
evaluated here, never adopted without measurement."""


class ResearchOnlyRefused(RuntimeError):
    """cheetah weights were reached without an explicit research ack."""


def require_research(research_ack: bool) -> None:
    """Refuse unless the caller explicitly accepts research-only terms."""
    if research_ack is not True:
        raise ResearchOnlyRefused(
            "cheetah packs are non-commercial research weights "
            "(MODEL.LICENSE grant non-commercial); pass research_ack=True "
            "to benchmark them, never in a production path"
        )


def load_app(pack: str, model_root: str, research_ack: bool):
    """A CPU `PersonAnalysis` app rooted at `model_root` (scratch, never
    the shared insightface cache). First use downloads the pack there."""
    require_research(research_ack)
    from insightface.app import PersonAnalysis

    app = PersonAnalysis(name=pack, root=model_root, providers=["CPUExecutionProvider"])
    app.prepare()
    return app


def extract_frame(
    source_file: str,
    timestamp: float,
    out_path: str,
    max_width: int = FRAME_INPUT_MAX_WIDTH,
) -> bool:
    """One scaled JPEG frame at `timestamp`, fast-seek. True on success."""
    try:
        out = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                source_file,
                "-vf",
                f"scale='min({max_width},iw)':-2",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                out_path,
                "-loglevel",
                "error",
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install: brew install ffmpeg")
    return (
        out.returncode == 0
        and os.path.isfile(out_path)
        and os.path.getsize(out_path) > 0
    )


def read_bgr(path: str):
    """Decode an image file to BGR uint8. cv2 is imported here, not at top."""
    import cv2

    image = cv2.imread(path)
    if image is None:
        raise RuntimeError(f"could not decode {path}")
    return image


def sample_timestamps(
    duration_seconds: float, interval_s: float = 10.0, max_samples: int = 120
) -> list[float]:
    """Evenly spaced timestamps, margin-trimmed. Same semantics as
    `person_entity.sample_timestamps` (duplicated, not imported: this
    module runs in interpreters without the library)."""
    if not duration_seconds or duration_seconds <= 1.0:
        return []
    margin = min(1.0, duration_seconds * 0.02)
    start, end = margin, duration_seconds - margin
    if end <= start:
        return [round(duration_seconds / 2.0, 3)]
    count = min(max_samples, max(1, int((end - start) // interval_s) + 1))
    if count == 1:
        return [round((start + end) / 2.0, 3)]
    step = (end - start) / (count - 1)
    return [round(start + i * step, 3) for i in range(count)]


def source_duration(source_file: str) -> float:
    """Duration in seconds via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            source_file,
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {source_file}: {out.stderr.strip()}")
    return float(out.stdout.strip())


def observe_frame(app, image, key: str) -> list[dict]:
    """Every person cheetah finds in one frame, with face and body
    evidence kept SEPARATE (AUD-047: fusion happens in the report, never
    inside the observation). A person with neither embedding contributes
    detection only."""
    out = []
    for person in app.get(image):
        face = person.face
        if person.body_bbox is None:
            if face is None or face.embedding is None:
                continue
            out.append(
                {
                    "body_bbox": None,
                    "body_det_score": None,
                    "reid_feature": None,
                    "face_embedding": [round(float(v), 6) for v in face.embedding],
                    "face_det_score": round(float(face.det_score), 4),
                    "face_body_associated": False,
                }
            )
            continue
        out.append(
            {
                "body_bbox": [round(float(v), 1) for v in person.body_bbox],
                "body_det_score": round(float(person.det_score), 4),
                "reid_feature": (
                    [round(float(v), 6) for v in person.reid_feature]
                    if person.reid_feature is not None
                    else None
                ),
                "face_embedding": (
                    [round(float(v), 6) for v in face.embedding]
                    if face is not None and face.embedding is not None
                    else None
                ),
                "face_det_score": (
                    round(float(face.det_score), 4) if face is not None else None
                ),
                "face_body_associated": bool(face is not None),
            }
        )
    return out


def pair_stats(
    items: Sequence[tuple[str, str, Sequence[float]]],
    threshold: float,
    grid: Sequence[float] = THRESHOLD_GRID,
) -> dict:
    """FAR/FRR over `(key, person, embedding)` pairs. Same definitions as
    `face_identity_study.pair_report`; kept local so this module runs
    without the library."""
    import numpy as np

    if not items:
        return {"faces": 0, "passes": False, "reason": "no embeddings measured"}
    embeddings = np.asarray([row[2] for row in items], dtype=float)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    sims = embeddings @ embeddings.T
    same, diff = [], []
    for i, j in itertools.combinations(range(len(items)), 2):
        row = (round(float(sims[i, j]), 4), items[i][0], items[j][0])
        (same if items[i][1] == items[j][1] else diff).append(row)
    same.sort(key=lambda row: row[0])
    diff.sort(key=lambda row: row[0], reverse=True)

    def rates(t: float) -> dict:
        false_accepts = sum(1 for s, _a, _b in diff if s >= t)
        false_rejects = sum(1 for s, _a, _b in same if s < t)
        return {
            "threshold": t,
            "FAR": round(false_accepts / len(diff), 6) if diff else None,
            "FRR": round(false_rejects / len(same), 6) if same else None,
            "false_accepts": false_accepts,
            "false_rejects": false_rejects,
        }

    at = rates(threshold)
    return {
        "faces": len(items),
        "same_person_pairs": len(same),
        "different_person_pairs": len(diff),
        "weakest_same_person": list(same[0]) if same else None,
        "closest_different_person": list(diff[0]) if diff else None,
        "threshold": threshold,
        "at_threshold": at,
        "passes": bool(
            same and diff and at["false_accepts"] == 0 and at["false_rejects"] == 0
        ),
        "grid": [rates(t) for t in grid],
    }


def measure_source(
    app,
    source_file: str,
    person: str,
    extra_timestamps: Sequence[float],
    crops_dir: str | None = None,
    planned: bool = True,
) -> dict:
    """Planned sample plus extras through cheetah `get()`: face and body
    observations per frame, detection-only persons named separately.

    `planned=False` measures ONLY `extra_timestamps`: a sparse
    cross-session probe without the full production sample. `crops_dir`
    (optional scratch) saves each observed person-box crop with a
    person-label sidecar for box-matched descriptor scoring. Labels never
    leave scratch.
    """
    import shutil
    import time

    import cv2

    duration = source_duration(source_file)
    base = set(sample_timestamps(duration)) if planned else set()
    wanted = sorted(base | {float(t) for t in extra_timestamps})
    stem = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    scratch = tempfile.mkdtemp(prefix="cheetah-reid-")
    frames, persons_without_face, detection_only = [], 0, 0
    decode_s, infer_s = 0.0, 0.0
    try:
        for t in wanted:
            path = os.path.join(scratch, f"f_{t:.3f}.jpg")
            start = time.perf_counter()
            ok = extract_frame(source_file, t, path)
            try:
                image = read_bgr(path) if ok else None
            finally:
                if os.path.isfile(path):
                    os.remove(path)
            decode_s += time.perf_counter() - start
            if image is None:
                continue
            start = time.perf_counter()
            observed = observe_frame(app, image, f"{stem}@{t:.3f}")
            infer_s += time.perf_counter() - start
            frame = {"key": f"{stem}@{t:.3f}", "person": person, "persons": observed}
            frames.append(frame)
            for i, row in enumerate(observed):
                if row["face_embedding"] is None and row["reid_feature"] is not None:
                    persons_without_face += 1
                if row["face_embedding"] is None and row["reid_feature"] is None:
                    detection_only += 1
                if crops_dir is not None and row["body_bbox"] is not None:
                    height, width = image.shape[:2]
                    x1 = max(0, int(row["body_bbox"][0]))
                    y1 = max(0, int(row["body_bbox"][1]))
                    x2 = min(width, int(row["body_bbox"][2]) + 1)
                    y2 = min(height, int(row["body_bbox"][3]) + 1)
                    if x2 > x1 and y2 > y1:
                        base = f"{stem}@{t:.3f}_{i}"
                        cv2.imwrite(
                            os.path.join(crops_dir, base + ".jpg"), image[y1:y2, x1:x2]
                        )
                        with open(
                            os.path.join(crops_dir, base + ".json"),
                            "w",
                            encoding="utf-8",
                        ) as handle:
                            json.dump(
                                {
                                    "person": person,
                                    "frame": frame["key"],
                                    "box": row["body_bbox"],
                                    "face_associated": row["face_embedding"]
                                    is not None,
                                },
                                handle,
                            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {
        "source_file": source_file,
        "person": person,
        "frames": frames,
        "frames_measured": len(frames),
        "frames_with_no_person": sum(1 for f in frames if not f["persons"]),
        "persons_without_face": persons_without_face,
        "detection_only": detection_only,
        "decode_seconds": round(decode_s, 3),
        "infer_seconds": round(infer_s, 3),
    }


def run_benchmark(
    manifest: dict,
    app,
    face_threshold: float,
    body_threshold: float,
    crops_dir: str | None = None,
) -> dict:
    """Face and body margin reports over a labelled manifest. The two
    modalities are scored independently; no match() accept/reject enters
    the numbers (vendor defaults are evaluated as grid points)."""
    if crops_dir is not None:
        os.makedirs(crops_dir, exist_ok=True)
    measured = [
        measure_source(
            app,
            s["source_file"],
            s["person"],
            [
                (e["t"] if isinstance(e, dict) else e)
                for e in (s.get("timestamps") or [])
            ],
            crops_dir,
            s.get("planned", True),
        )
        for s in manifest["sources"]
    ]
    face_items, body_items, body_unassociated_items = [], [], []
    for m in measured:
        for frame in m["frames"]:
            for row in frame["persons"]:
                if row["face_embedding"] is not None:
                    face_items.append(
                        (frame["key"] + "#face", frame["person"], row["face_embedding"])
                    )
                if row["reid_feature"] is not None:
                    entry = (
                        frame["key"] + "#body",
                        frame["person"],
                        row["reid_feature"],
                    )
                    if row["face_embedding"] is not None:
                        body_items.append(entry)
                    else:
                        body_unassociated_items.append(entry)
    return {
        "split": manifest.get("split", "unspecified"),
        "face_threshold": face_threshold,
        "body_threshold": body_threshold,
        "frames_measured": sum(m["frames_measured"] for m in measured),
        "frames_with_no_person": sum(m["frames_with_no_person"] for m in measured),
        "persons_without_face": sum(m["persons_without_face"] for m in measured),
        "detection_only": sum(m["detection_only"] for m in measured),
        "decode_seconds": round(sum(m["decode_seconds"] for m in measured), 3),
        "infer_seconds": round(sum(m["infer_seconds"] for m in measured), 3),
        "face": pair_stats(face_items, face_threshold),
        "body": pair_stats(body_items, body_threshold),
        "body_unassociated": pair_stats(body_unassociated_items, body_threshold),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cheetah_reid",
        description="InsightFace 2.1 cheetah research benchmark (AUD-043/046).",
    )
    sub = parser.add_subparsers(dest="command")
    p_run = sub.add_parser("run", help="measure face+body margins")
    p_run.add_argument("manifest")
    p_run.add_argument("--pack", default="cheetah_s")
    p_run.add_argument(
        "--model-root",
        required=True,
        help="scratch insightface root (never the shared cache)",
    )
    p_run.add_argument("--out", default=None)
    p_run.add_argument("--face-threshold", type=float, default=VENDOR_FACE_THRESHOLD)
    p_run.add_argument("--body-threshold", type=float, default=VENDOR_BODY_THRESHOLD)
    p_run.add_argument(
        "--research-ack",
        action="store_true",
        help="accept non-commercial research terms",
    )
    p_run.add_argument(
        "--save-crops",
        default=None,
        help="scratch dir for person-box crops (never committed)",
    )
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 1
    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)
    app = load_app(args.pack, args.model_root, research_ack=args.research_ack)
    report = run_benchmark(
        manifest, app, args.face_threshold, args.body_threshold, args.save_crops
    )
    print(
        json.dumps(
            {
                "split": report["split"],
                "frames": report["frames_measured"],
                "no_person_frames": report["frames_with_no_person"],
                "persons_without_face": report["persons_without_face"],
                "decode_seconds": report["decode_seconds"],
                "infer_seconds": report["infer_seconds"],
                "face": {
                    k: report["face"][k]
                    for k in (
                        "faces",
                        "passes",
                        "weakest_same_person",
                        "closest_different_person",
                    )
                },
                "body": {
                    k: report["body"][k]
                    for k in (
                        "faces",
                        "passes",
                        "weakest_same_person",
                        "closest_different_person",
                    )
                },
                "body_unassociated": {
                    k: report["body_unassociated"][k]
                    for k in (
                        "faces",
                        "passes",
                        "weakest_same_person",
                        "closest_different_person",
                    )
                },
            },
            indent=2,
        )
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

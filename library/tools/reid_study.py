"""Stratified person-identity margin study (lane vep-mu-reid).

AUD-044 (face across lighting/angles/occlusion), AUD-045 (cross-camera
ReID) and AUD-051 (explicit false matches), measured on the incumbent
InsightFace `buffalo_l` production path.

A manifest labels whole source files by person (the physical fact the
original face study used: LC49xx = akshita, LCATL00xx = craig, one
person per camera) and tags extra timestamps with a condition:

    {"split": "calibration",
     "sources": [{"source_file": "/abs/LC4931.MXF", "person": "akshita",
                  "condition": "akshita-closeup",
                  "timestamps": [4938.1, {"t": 211.0,
                                           "condition": "eyes-closed"}]}]}

Frames reach ArcFace through `person_entity.frames_at` and
`person_entity.measure_face_observations`, the one path production's
faces take. A frame with other than exactly one face is excluded and
named (its label would be a guess); the exclusion counts ARE the
detection-failure accounting occlusion asks for. Pair statistics reuse
`face_identity_study.pair_report`, so thresholds and FAR/FRR definitions
match the published studies.

Split discipline: settings are chosen on calibration manifests only;
training and held-out manifests are report-only. The report records its
own `split`.

Heavy (decode + insightface): run under the heavy-work lock.

    bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-reid \\
      --profile semantics.analyse:inference -- bin/vep -m library.tools.reid_study run <manifest.json> [--out report.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

try:
    from library.tools import (
        body_appearance,
        face_identity_study,
        footage_identity,
        person_entity,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools import (
        body_appearance,
        face_identity_study,
        footage_identity,
        person_entity,
    )


def source_duration(source_file: str) -> float:
    """Duration in seconds via ffprobe. The study takes no M0 dependency."""
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


def _extra_map(raw: Sequence) -> dict[float, str]:
    """`{timestamp: condition}` for manifest extras (plain numbers carry none)."""
    mapped = {}
    for item in raw or []:
        if isinstance(item, dict):
            mapped[float(item["t"])] = str(item.get("condition") or "extra")
        else:
            mapped[float(item)] = ""
    return mapped


def measure_source(
    source_file: str, person: str, default_condition: str, extra: Sequence, root=None
) -> dict:
    """Production's planned sample plus extras, decoded and embedded.

    Returns kept `(key, person, file, condition, embedding)` faces plus
    the named exclusions. Condition of a kept frame is its extra tag, else
    the source default.
    """
    digest = footage_identity.fingerprint(source_file)["content_digest"]
    duration = source_duration(source_file)
    planned = person_entity.sample_timestamps(duration)
    extras = _extra_map(extra)
    wanted = sorted(set(planned) | set(extras))
    scratch = tempfile.mkdtemp(prefix="reid-study-")
    try:
        frames, frame_source = person_entity.frames_at(
            source_file, digest, wanted, root, scratch
        )
        observations, frame_pixels = person_entity.measure_face_observations(frames)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    by_time: dict[float, list] = {}
    for obs in observations:
        by_time.setdefault(obs.timestamp, []).append(obs)
    stem = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    faces, excluded = [], []
    for t, _path, _owned in frames:
        found = by_time.get(t, [])
        key = f"{stem}@{t:.3f}"
        if len(found) == 1:
            condition = extras.get(t) or default_condition or "planned"
            faces.append(
                {
                    "key": key,
                    "person": person,
                    "file": stem,
                    "condition": condition,
                    "embedding": list(found[0].embedding),
                    "det_score": found[0].det_score,
                }
            )
        else:
            excluded.append(
                {
                    "frame": key,
                    "faces": len(found),
                    "det_scores": [o.det_score for o in found],
                }
            )
    return {
        "source_file": source_file,
        "person": person,
        "frame_source": frame_source,
        "frame_pixels": frame_pixels,
        "frames": len(frames),
        "faces": faces,
        "excluded": excluded,
    }


def _pairs(faces: Sequence[dict]):
    """`(similarity, key_a, key_b, same)` over every pair, cosine."""
    import numpy as np

    embs = np.asarray([f["embedding"] for f in faces], dtype=float)
    embs /= np.linalg.norm(embs, axis=1, keepdims=True)
    sims = embs @ embs.T
    for i, j in itertools.combinations(range(len(faces)), 2):
        yield (
            round(float(sims[i, j]), 4),
            faces[i],
            faces[j],
            faces[i]["person"] == faces[j]["person"],
        )


def cross_condition_same_person(faces: Sequence[dict], threshold: float) -> dict:
    """Same-person pairs split by whether they share a condition/file.

    The operational cross-camera question: how much margin survives when
    the two frames come from different conditions (angle, lighting,
    session) or different files, versus the same one.
    """
    buckets: dict[str, list] = {
        "same_condition": [],
        "cross_condition": [],
        "same_file": [],
        "cross_file": [],
    }
    for sim, a, b, same in _pairs(faces):
        if not same:
            continue
        buckets[
            "same_condition" if a["condition"] == b["condition"] else "cross_condition"
        ].append(sim)
        buckets["same_file" if a["file"] == b["file"] else "cross_file"].append(sim)
    out = {}
    for name, sims in buckets.items():
        sims = sorted(sims)
        out[name] = {
            "n": len(sims),
            "min": sims[0] if sims else None,
            "mean": round(sum(sims) / len(sims), 4) if sims else None,
            "max": sims[-1] if sims else None,
            "below_threshold": sum(1 for s in sims if s < threshold),
        }
    return out


def false_match_summary(
    faces: Sequence[dict], threshold: float, limit: int = 10
) -> dict:
    """AUD-051: the closest different-person pairs, named, not just counted.

    A false merge silently corrupts a person's whole span history, so the
    report names WHO would merge with WHOM and WHERE, plus FAR at the
    operating threshold. Counts alone would hide a near-miss cluster.
    """
    diff = [(sim, a, b) for sim, a, b, same in _pairs(faces) if not same]
    diff.sort(key=lambda row: row[0], reverse=True)
    return {
        "different_person_pairs": len(diff),
        "closest": [
            {
                "similarity": sim,
                "a": f"{a['key']} ({a['person']}/{a['condition']})",
                "b": f"{b['key']} ({b['person']}/{b['condition']})",
            }
            for sim, a, b in diff[:limit]
        ],
        "at_threshold": sum(1 for sim, _a, _b in diff if sim >= threshold),
    }


def run_study(
    manifest: dict, threshold: float = person_entity.FACE_MATCH_THRESHOLD, root=None
) -> dict:
    """The stratified margin report over a labelled manifest."""
    measured = [
        measure_source(
            s["source_file"],
            s["person"],
            s.get("condition") or "planned",
            s.get("timestamps") or [],
            root,
        )
        for s in manifest["sources"]
    ]
    faces = [face for m in measured for face in m["faces"]]
    triplets = [(f["key"], f["person"], f["embedding"]) for f in faces]
    by_condition = {}
    for condition in sorted({f["condition"] for f in faces}):
        subset = [t for f, t in zip(faces, triplets) if f["condition"] == condition]
        by_condition[condition] = (
            face_identity_study.pair_report(subset, threshold=threshold)
            if len(subset) >= 2
            else {"faces": len(subset)}
        )
    return {
        "split": manifest.get("split", "unspecified"),
        "threshold": threshold,
        "instrument": person_entity.__name__,
        "sources": [
            {
                k: m[k]
                for k in (
                    "source_file",
                    "person",
                    "frame_source",
                    "frame_pixels",
                    "frames",
                )
            }
            for m in measured
        ],
        "faces": len(faces),
        "excluded": [e for m in measured for e in m["excluded"]],
        "global": face_identity_study.pair_report(triplets, threshold=threshold),
        "by_condition": by_condition,
        "same_person_strata": cross_condition_same_person(faces, threshold),
        "false_matches": false_match_summary(faces, threshold),
    }


def score_crops(
    crops_dir: str, threshold: float = 0.90, associated_only: bool = False
) -> dict:
    """Box-matched no-weights baseline over saved person crops.

    Reads `<base>.jpg` + `<base>.json` (`{"person": ...}`) pairs as
    written by `cheetah_reid --save-crops`, describes each crop with
    `body_appearance.describe_person_crop`, and scores the same
    FAR/FRR pair statistics - the SAME boxes the learned ReID saw, so
    the comparison is box-matched. Pure numpy/cv2: no model, no lock.

    `associated_only` keeps only crops whose sidecar says a face was
    associated with the body box: unassociated boxes are hands,
    partials and background (measured on the podcast session), so
    scoring them as person pairs measures detector hygiene, not the
    descriptor. The unfiltered run is still reported beside it.
    """
    import cv2

    items = []
    skipped = []
    for sidecar in sorted(os.listdir(crops_dir)):
        if not sidecar.endswith(".json"):
            continue
        base = sidecar[: -len(".json")]
        image_path = os.path.join(crops_dir, base + ".jpg")
        try:
            with open(os.path.join(crops_dir, sidecar), encoding="utf-8") as handle:
                label = json.load(handle)
            if associated_only and not label.get("face_associated", False):
                skipped.append({"crop": base, "reason": "unassociated box"})
                continue
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError("undecodable crop")
            descriptor = body_appearance.describe_person_crop(image)
        except (OSError, ValueError) as exc:
            skipped.append({"crop": base, "reason": str(exc)[:120]})
            continue
        items.append((base, label["person"], descriptor))
    triplets = [(key, person, list(descriptor)) for key, person, descriptor in items]
    grid = tuple(
        sorted(
            set(face_identity_study.THRESHOLD_GRID)
            | {0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99}
        )
    )
    report = face_identity_study.pair_report(triplets, threshold=threshold, grid=grid)
    return {
        "crops_dir": crops_dir,
        "crops": len(items),
        "skipped": skipped,
        "threshold": threshold,
        "descriptor_dim": body_appearance.STUDY_DIM,
        "margins": report,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="reid_study",
        description="Stratified person-identity margin study (AUD-044/045/051).",
    )
    sub = parser.add_subparsers(dest="command")
    p_run = sub.add_parser("run", help="measure margins over a manifest")
    p_run.add_argument("manifest")
    p_run.add_argument("--out", default=None)
    p_run.add_argument(
        "--threshold", type=float, default=person_entity.FACE_MATCH_THRESHOLD
    )
    p_crops = sub.add_parser(
        "score-crops", help="box-matched no-weights baseline over saved person crops"
    )
    p_crops.add_argument("crops_dir")
    p_crops.add_argument("--out", default=None)
    p_crops.add_argument("--threshold", type=float, default=0.90)
    p_crops.add_argument(
        "--associated-only",
        action="store_true",
        help="score only face-associated body boxes",
    )
    args = parser.parse_args(argv)
    if args.command == "score-crops":
        report = score_crops(
            args.crops_dir,
            threshold=args.threshold,
            associated_only=args.associated_only,
        )
        print(
            json.dumps(
                {
                    "crops": report["crops"],
                    "skipped": len(report["skipped"]),
                    "passes": report["margins"]["passes"],
                    "weakest_same": report["margins"]["weakest_same_person"],
                    "closest_diff": report["margins"]["closest_different_person"],
                    "grid": [
                        (row["threshold"], row["FAR"], row["FRR"])
                        for row in report["margins"]["grid"]
                    ],
                },
                indent=2,
            )
        )
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
        return 0
    if args.command != "run":
        parser.print_help()
        return 1
    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)
    report = run_study(manifest, threshold=args.threshold)
    summary = {
        "split": report["split"],
        "faces": report["faces"],
        "excluded": len(report["excluded"]),
        "passes": report["global"]["passes"],
        "weakest_same": report["global"]["weakest_same_person"],
        "closest_diff": report["global"]["closest_different_person"],
        "strata": report["same_person_strata"],
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

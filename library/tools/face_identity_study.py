"""The face-identity FAR/FRR study, measured on the frames production decodes.

`person_entity.FACE_MATCH_THRESHOLD` is a measured number: a false-accept
study over labelled faces (`data/vep-person-entity-store/eval/` in
firstmate's home). Its first pass used 4K stills while production ran
ArcFace on 384x216 M2 thumbnails. The production-path study exposed the
misses; the current path decodes from source and caps its saved frame at
the narrowest width that passed the combined production and hard-case set.

**The study reaches its frames through `person_entity.frames_at`, the
one path production's faces take, and embeds them through
`person_entity.measure_face_observations`.** A change to what production
feeds ArcFace changes what this study measures with it; the report
records the `frame_source` and `frame_pixels` it measured on.

A manifest labels whole source files by person (one camera per person,
the study's own ground truth) and may add hard-case timestamps beside
production's planned sample:

    {"sources": [{"source_file": "/abs/LCATL0011.MXF", "person": "craig",
                  "timestamps": [211, 212, 213]}]}

A frame with other than exactly one face is excluded and named - its
label would be a guess. Heavy (decode + insightface): run it under the
heavy-work lock.

    python3 -m library.tools.face_identity_study run <manifest.json> [--out report.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
import tempfile
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from library.tools import footage_identity, person_entity, source_memory
except ImportError:  # imported as `tools.*` from inside library/
    from tools import footage_identity, person_entity, source_memory

THRESHOLD_GRID = (0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.27, 0.30,
                  0.32, 0.35, 0.40)

FAILING_PAIRS_SHOWN = 20


def pair_report(faces: Sequence[Tuple[str, str, Sequence[float]]],
                threshold: float = person_entity.FACE_MATCH_THRESHOLD,
                grid: Sequence[float] = THRESHOLD_GRID) -> dict:
    """FAR/FRR over every pair of `(key, person, embedding)` faces.

    FAR = different-person pairs >= t over all different-person pairs;
    FRR = same-person pairs < t over all same-person pairs. `passes` is
    the study's bound at `threshold`: FAR=0 and FRR=0.
    """
    import numpy as np

    embeddings = np.asarray([f[2] for f in faces], dtype=float)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    sims = embeddings @ embeddings.T
    same: List[Tuple[float, str, str]] = []
    diff: List[Tuple[float, str, str]] = []
    for i, j in itertools.combinations(range(len(faces)), 2):
        row = (round(float(sims[i, j]), 4), faces[i][0], faces[j][0])
        (same if faces[i][1] == faces[j][1] else diff).append(row)
    same.sort()
    diff.sort(reverse=True)

    def rates(t: float) -> dict:
        false_accepts = sum(1 for s, _a, _b in diff if s >= t)
        false_rejects = sum(1 for s, _a, _b in same if s < t)
        return {"threshold": t,
                "FAR": round(false_accepts / len(diff), 6) if diff else None,
                "FRR": round(false_rejects / len(same), 6) if same else None,
                "false_accepts": false_accepts, "false_rejects": false_rejects}

    at = rates(threshold)
    failing = ([row for row in same if row[0] < threshold]
               + [row for row in diff if row[0] >= threshold])
    frames_failing = Counter(key for _s, a, b in failing for key in (a, b))
    return {
        "faces": len(faces),
        "same_person_pairs": len(same),
        "different_person_pairs": len(diff),
        "weakest_same_person": list(same[0]) if same else None,
        "closest_different_person": list(diff[0]) if diff else None,
        "threshold": threshold,
        "at_threshold": at,
        "passes": bool(same and diff
                       and at["false_accepts"] == 0 and at["false_rejects"] == 0),
        "grid": [rates(t) for t in grid],
        "failing_pairs": [list(row) for row in failing[:FAILING_PAIRS_SHOWN]],
        "frames_in_failing_pairs": frames_failing.most_common(),
    }


def measure_source(source_file: str, person: str, extra_timestamps: Sequence[float],
                   root=None) -> dict:
    """Production's planned sample plus `extra_timestamps`, decoded by
    `person_entity.frames_at` and embedded by production's own app."""
    digest = footage_identity.fingerprint(source_file)["content_digest"]
    m0 = source_memory.read_m0(digest, root)
    if m0 is None or not m0.get("duration_seconds"):
        raise RuntimeError(f"no M0 duration for {source_file}; run "
                           f"`python3 -m library.tools.source_memory` first")
    planned = person_entity.sample_timestamps(m0["duration_seconds"])
    wanted = sorted(set(planned) | {float(t) for t in extra_timestamps})
    scratch = tempfile.mkdtemp(prefix="face-study-")
    try:
        frames, frame_source = person_entity.frames_at(
            source_file, digest, wanted, root, scratch)
        observations, frame_pixels = person_entity.measure_face_observations(frames)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    by_time: Dict[float, list] = {}
    for obs in observations:
        by_time.setdefault(obs.timestamp, []).append(obs)
    stem = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    faces, excluded = [], []
    for t, _path, _owned in frames:
        found = by_time.get(t, [])
        key = f"{stem}@{t:.3f}"
        if len(found) == 1:
            faces.append((key, person, found[0].embedding))
        else:
            excluded.append({"frame": key, "faces": len(found),
                             "det_scores": [o.det_score for o in found]})
    return {"source_file": source_file, "person": person,
            "frame_source": frame_source, "frame_pixels": frame_pixels,
            "frames": len(frames), "faces": faces, "excluded": excluded}


def run_study(manifest: dict, root=None) -> dict:
    sources = [measure_source(s["source_file"], s["person"],
                              s.get("timestamps") or [], root)
               for s in manifest["sources"]]
    faces = [face for s in sources for face in s["faces"]]
    report = pair_report(faces)
    report["inputs"] = [{k: v for k, v in s.items() if k != "faces"}
                        for s in sources]
    return report


def format_report(report: dict) -> str:
    inputs = ", ".join(sorted({f"{s['frame_source']} {s['frame_pixels']}"
                               for s in report["inputs"]}))
    lines = [f"inputs: {inputs}",
             (f"faces {report['faces']}: {report['same_person_pairs']} same-person, "
              f"{report['different_person_pairs']} different-person pairs"),
             f"weakest same-person {report['weakest_same_person']}",
             f"closest different-person {report['closest_different_person']}",
             "", "| threshold | FAR | FRR |", "|---|---|---|"]
    lines += [f"| {r['threshold']:.2f} | {r['FAR']} ({r['false_accepts']}) "
              f"| {r['FRR']} ({r['false_rejects']}) |" for r in report["grid"]]
    verdict = "PASS" if report["passes"] else "FAIL"
    lines += ["", f"{verdict}: FAR=0 and FRR=0 at {report['threshold']}"]
    for s in report["inputs"]:
        for ex in s["excluded"]:
            lines.append(f"excluded {ex['frame']}: {ex['faces']} faces {ex['det_scores']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="face_identity_study")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="measure FAR/FRR over a labelled manifest")
    p_run.add_argument("manifest")
    p_run.add_argument("--out", help="write the full report as JSON here")
    args = parser.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    report = run_study(manifest)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print(format_report(report))
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    sys.exit(main())

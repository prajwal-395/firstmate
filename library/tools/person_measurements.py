"""Per-frame person measurements: M3 (`persons.json`) of the per-source
footage memory (`docs/SOURCE_MEMORY.md`).

Faces (box, lip landmarks) and hands (21 joints each) at the shared M2
cadence (~2 Hz on the geo-podcast cameras), measured by the SAME Apple
Vision helper step 1.04 runs (`vision_measure.measure_frames` over
`vision_helper.swift`, persistence filter included) - this module only
feeds it the M2 thumbnails instead of a decode of its own. Step 1.04 is
not touched: it keeps sampling its own frames for its own index.

**One coordinate frame.** Every point here is IMAGE-normalised, top-left
origin: x in 0..1 of the frame width, y in 0..1 of its height. Vision's
lip landmarks arrive relative to their own FACE BOX
(`VNFaceLandmarkRegion2D.normalizedPoints`) while hand joints arrive
relative to the image; this module maps the lips through the landmark's
box at write time, so no reader can compare the two frames by mistake.
That mistake is not hypothetical - it is what the Vision lane's
hand-over-mouth eval did, and it is most of why that rule read FP
99/265 (re-scored at 23/265 with the lips mapped:
`data/vep-structured-footage-query/eval/` in firstmate's home). A
distance between two points needs `frame_pixels` to be isotropic; a
reader multiplies x by width and y by height before measuring one.

Readers: `event_spans` reads faces (on_screen) and inner lips (whose
voice is whose). HANDS have no shipped reader: the hand-at-mouth rule
over them was measured and not shipped (`event_spans.UNSHIPPED`), and
they stay because that evaluation's scorer and the next attempt at the
predicate read exactly these joints - re-measuring them costs a full
Vision pass. Body pose and the person-segmentation summary are NOT kept:
the helper measures them, nothing reads them, and a declared output has
a reader (AGENTS.md 10.1).

    bin/vep -m library.tools.heavy_work_lock run --owner <lane> -- \\
        python3 -m library.tools.person_measurements build <project>
    python3 -m library.tools.person_measurements status <project>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from library.tools import source_memory

M3_STATUS_MEASURED = "measured"
M3_METHOD = "vision-helper-v1 over M2"

BATCH_FRAMES = 2000
"""Frames per helper process. One process per source would pay cold
start once, but a helper killed at frame 9,000 of 9,873 would lose
everything; 2,000 frames is ~70 s warm at the measured 34 ms/frame."""

LANDMARK_FACE_MIN_IOU = 0.5
"""Landmarks pair to a face box by overlap, as step 1.04 pairs them
(`vision_measure._assemble`): the two requests observe independently,
so an unpaired landmark set is dropped rather than attached to the
wrong face."""


def _vision():
    """Step 1.04's Vision wrapper, imported where it is used: a tool
    that only reads M3 must not pay for (or need) the step package."""
    from library.steps.step_1_04_temporal_index import vision_measure
    return vision_measure


def _round(values, places: int = 4) -> list:
    return [round(float(v), places) for v in values]


def lips_to_image(points: List[list], box: List[float]) -> List[list]:
    """Box-relative landmark points -> image-normalised points.

    `box` is the landmark observation's own `[x1, y1, x2, y2]` in image
    coordinates (top-left origin, as the helper writes it); `points`
    are `[x, y]` within that box, also top-left origin.
    """
    x1, y1, x2, y2 = box
    return [[round(x1 + px * (x2 - x1), 4), round(y1 + py * (y2 - y1), 4)]
            for px, py in points]


def _boxed_faces(doc: dict) -> List[dict]:
    """The helper's faces that carry a whole box - the one list both the
    persistence filter and `frame_record` index, so the two align."""
    return [face for face in doc.get("faces") or []
            if len(face.get("box") or []) == 4]


def frame_record(doc: dict, keep_faces: List[bool], t: float) -> dict:
    """One helper document as an M3 frame: kept faces with their lips in
    image coordinates, and every hand with its joints."""
    vm = _vision()
    landmarks = []
    for lm in doc.get("landmarks") or []:
        box = lm.get("box") or []
        if len(box) != 4:
            continue
        landmarks.append((box, lm))
    faces = []
    for face, keep in zip(_boxed_faces(doc), keep_faces):
        box = face["box"]
        if not keep:
            continue
        entry = {"box": _round(box),
                 "confidence": round(float(face.get("confidence", 0.0)), 3)}
        best, best_lm = 0.0, None
        for lm_box, lm in landmarks:
            overlap = vm._iou(box, lm_box)
            if overlap > best:
                best, best_lm = overlap, (lm_box, lm)
        if best >= LANDMARK_FACE_MIN_IOU and best_lm is not None:
            lm_box, lm = best_lm
            for region, key in (("outerLips", "outer_lips"),
                                ("innerLips", "inner_lips")):
                if lm.get(region):
                    entry[key] = lips_to_image(lm[region], lm_box)
        faces.append(entry)
    hands = []
    for hand in doc.get("hands") or []:
        joints = hand.get("joints") or {}
        if not joints:
            continue
        hands.append({
            "chirality": hand.get("chirality", "unknown"),
            "confidence": round(float(hand.get("confidence", 0.0)), 3),
            "joints": {name: [round(float(j[0]), 4), round(float(j[1]), 4),
                              round(float(j[2]), 3)]
                       for name, j in joints.items()
                       if isinstance(j, list) and len(j) == 3},
        })
    return {"t": t, "faces": faces, "hands": hands}


def measure_m2_frames(frame_paths: List[str], frame_times: List[float],
                      helper: str) -> Tuple[List[dict], dict]:
    """Vision over every M2 frame, in batches; persistence filter over
    the whole sequence (a face kept iff an IoU >= 0.3 box exists at an
    adjacent sample - the measured step 1.04 filter, unchanged)."""
    vm = _vision()
    docs: List[dict] = []
    started = time.perf_counter()
    for first in range(0, len(frame_paths), BATCH_FRAMES):
        docs.extend(vm.measure_frames(frame_paths[first:first + BATCH_FRAMES],
                                      helper))
    elapsed = time.perf_counter() - started
    kept, removed = vm.apply_persistence(
        [[face["box"] for face in _boxed_faces(doc)] for doc in docs])
    frames = [frame_record(doc, keep, t)
              for doc, keep, t in zip(docs, kept, frame_times)]
    pixels = next((doc.get("pixels") for doc in docs if doc.get("pixels")),
                  None)
    return frames, {
        "vision_seconds": round(elapsed, 1),
        "ms_per_frame": round(1000.0 * elapsed / max(1, len(docs)), 1),
        "faces_removed_by_persistence": int(sum(removed)),
        "frame_pixels": pixels,
    }


def build_source_persons(source_file: str, content_digest: str,
                         root: Optional[Path] = None) -> dict:
    """M3 for one source, read off its fresh M2 sample. Heavy: callers
    hold the heavy-work lock. Refuses (raises) without a fresh M2 - this
    module never decodes the source itself."""
    m2 = source_memory.read_m2(content_digest, root)
    if m2 is None or not source_memory.is_fresh(m2, source_file):
        raise RuntimeError(
            f"{os.path.basename(source_file)} has no fresh M2 frame sample; "
            f"run `python3 -m library.tools.source_memory frames` first")
    vm = _vision()
    helper, reason = vm.ensure_helper()
    if helper is None:
        raise RuntimeError(f"Vision helper unavailable: {reason}")
    paths = [source_memory.frame_abspath(content_digest, f, root)
             for f in m2["frames"]]
    times = [f["t"] for f in m2["frames"]]
    frames, instrument = measure_m2_frames(paths, times, helper)
    record = {
        "content_digest": content_digest,
        "size_bytes": m2.get("size_bytes"),
        "source_file": os.path.abspath(source_file),
        "status": M3_STATUS_MEASURED,
        "coordinates": "image-normalised, top-left origin",
        "frame_count": len(frames),
        "frames": frames,
        "instrument": {"method": M3_METHOD,
                       "m2_width": m2.get("width"),
                       "persistence_iou": vm.PERSISTENCE_IOU,
                       **instrument},
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root)
        / source_memory.SLOT_PERSONS, record)
    return {"source_file": os.path.abspath(source_file),
            "content_digest": content_digest, "frames": len(frames),
            "faces": sum(len(f["faces"]) for f in frames),
            "hands": sum(len(f["hands"]) for f in frames),
            **{k: instrument[k] for k in ("vision_seconds", "ms_per_frame")}}


def read_m3(content_digest: str, root: Optional[Path] = None) -> Optional[dict]:
    """A digest's M3 record, or None when never measured."""
    path = (source_memory.source_dir(content_digest, root)
            / source_memory.SLOT_PERSONS)
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


def build_project_persons(project_folder: str,
                          clip_ids: Optional[List[str]] = None,
                          root: Optional[Path] = None) -> dict:
    """M3 for every catalog source with media and a fresh M2. Same
    reporting shape as `source_memory.build_project_frames`."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    results, failed, seen = [], [], set()
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "media-offline"})
            continue
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None or digest in seen:
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "no content digest" if digest is None
                            else "reused"})
            continue
        seen.add(digest)
        try:
            account = build_source_persons(path, digest, root)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="person_measurements",
        description="M3: Apple Vision faces, lip landmarks and hands over "
                    "each source's shared M2 frame sample. Heavy; run "
                    "under the heavy-work lock.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M3 for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M3")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_persons(args.project, args.clip, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        doc = read_m3(digest, root) if digest else None
        rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                     "m3": "measured" if doc else "missing",
                     "frames": doc.get("frame_count") if doc else 0})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

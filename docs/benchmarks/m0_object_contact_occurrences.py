"""The M0 car keys: the class that measures the OBJECT, not the HAND.

**The question.** The hand-at-lips candidate measures the hand, so the
ordinal of the class is the ordinal of the HAND's contacts and not the
ordinal of the object's arrivals: on the M0 fixture the truth's second
arrival of the car keys at the lips (33.40 s, frame 1002) was the
class's FIFTH occurrence, and the class's second occurrence was the
truth's FIRST arrival.

**What this bench measures.** The two classes on the same take, the
same source frames and the same ground truth:

* ``hand_contact_onset`` - the incumbent (the HAND);
* ``object_contact_onset`` - the object lane (the KEYS), measured from
  the object's own box and the object identity.

For each class: how many occurrences it carries, which adjudicated
arrival each occurrence is nearest, the frame error of the occurrence
that IS the truth's second arrival, and what the class's SECOND
occurrence names.

The ground truth is the one written down in the earlier lane's report
and unchanged here (agent-adjudicated, still provisional):
``docs/benchmarks/2026-10-21-m0-e2e-whole-clip.md``.

    python3 docs/benchmarks/m0_object_contact_occurrences.py \\
        --project /tmp/m0-e2e/projects/m0-keys \\
        --memory-root ~/.local/share/vep/source_memory/projects/<scope>
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ADJUDICATED = (
    {"name": "A1", "seconds": 15.4, "frame": 462, "precision_frames": 7},
    {"name": "A2", "seconds": 33.40, "frame": 1002, "precision_frames": 2},
    {"name": "A3", "seconds": 46.0, "frame": 1380, "precision_frames": 7},
    {"name": "A4", "seconds": 53.0, "frame": 1590, "precision_frames": 7},
    {"name": "A5", "seconds": 61.5, "frame": 1845, "precision_frames": 7},
    {"name": "A6", "seconds": 82.5, "frame": 2475, "precision_frames": 7},
)
"""The six adjudicated arrivals of the car keys at the speaker's lips."""

HAND_CLASS = "hand_contact_onset"
OBJECT_CLASS = "object_contact_onset"


def _nearest_arrival(seconds: float) -> dict[str, Any]:
    return min(ADJUDICATED, key=lambda row: abs(row["seconds"] - seconds))


def _frame_of(span: dict[str, Any], seconds: float,
              frame_index_by_position: dict[int, int]) -> int | None:
    """The SOURCE frame the occurrence was seen at.

    The occurrence carries the coarse frame it was seen at; the source
    frame index of that coarse frame is what a frame error is measured
    in, because the truth's frames are source frames.
    """
    best = None
    for frame in span.get("frames") or []:
        if frame.get("source_frame_index") is not None:
            index = int(frame["source_frame_index"])
        else:
            index = frame_index_by_position.get(int(frame.get("index", -1)))
        if index is None:
            continue
        time_s = float(frame.get("t") or 0.0)
        if best is None or abs(time_s - seconds) < abs(best[1] - seconds):
            best = (index, time_s)
    return best[0] if best else None


def _class_report(spans: list[dict[str, Any]],
                  frame_index_by_position: dict[int, int]) -> dict[str, Any]:
    ordered = sorted(spans, key=lambda span: float(span.get("onset_seconds")
                                                    or 0.0))
    rows = []
    for position, span in enumerate(ordered, start=1):
        onset = float(span.get("onset_seconds") or 0.0)
        frame = _frame_of(span, onset, frame_index_by_position)
        arrival = _nearest_arrival(onset)
        error = (frame - arrival["frame"]) if frame is not None else None
        rows.append({
            "occurrence": position,
            "onset_seconds": round(onset, 3),
            "source_frame": frame,
            "nearest_arrival": arrival["name"],
            "seconds_from_arrival": round(onset - arrival["seconds"], 3),
            "frame_error_vs_arrival": error,
            "entities": span.get("object_entities"),
        })
    return {
        "occurrences": len(ordered),
        "rows": rows,
        "second_occurrence": rows[1] if len(rows) > 1 else None,
        "occurrences_carrying_an_arrival": sum(
            1 for row in rows
            if abs(row["seconds_from_arrival"]) <= 1.5),
    }


def _full_res_frames(source_file: str, start_s: float, end_s: float,
                     out_dir: Path) -> list[Path]:
    """The window's frames at the SOURCE's resolution, with the same
    select the dense sample uses (so the two samples are the same
    frames)."""
    from library.tools import frame_exact

    expression = (f"select='between(t\\,{start_s - 0.001:.6f}\\,"
                  f"{end_s + 0.001:.6f})'")
    pattern = str(out_dir / "%06d.jpg")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-copyts",
        "-ss", f"{max(0.0, start_s - 1.0):.6f}", "-to", f"{end_s + 0.5:.6f}",
        "-i", source_file, "-an", "-sn", "-vf", expression,
        "-fps_mode", "passthrough", "-q:v", "2", pattern,
    ]
    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            check=False)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed on the refinement window: "
                         f"{(result.stderr or '')[-300:]}")
    del frame_exact
    return sorted(out_dir.glob("*.jpg"))


def refine_occurrence(source_file: str, onset_seconds: float,
                       *, window: float = 0.5,
                       phrase: str = "car keys") -> dict[str, Any]:
    """The object's arrival, refined to a frame with the OBJECT's own
    geometry.

    The dense sample is measured twice: at the M2 width for the LIPS
    (the same Apple Vision measurement the coarse lane used) and at the
    source's own resolution for the OBJECT (the detector needs the
    pixels). The arrival is then the first frame in which the object's
    measured box is at the lips.
    """
    from library.tools import frame_exact, object_contact
    from library.tools.analysis import open_vocabulary_detect as ovd

    start = max(0.0, onset_seconds - window)
    end = onset_seconds + window
    workdir = Path(tempfile.mkdtemp(prefix="m0-object-refine-"))
    started = time.perf_counter()
    try:
        timebase = frame_exact.probe_timebase(source_file)
        dense = frame_exact.sample_interval(source_file, timebase, start, end,
                                            workdir / "dense")
        measured = frame_exact.measure_frames(
            [row["file"] for row in dense["frames"]])
        full_dir = workdir / "full"
        full_dir.mkdir(parents=True, exist_ok=True)
        images = _full_res_frames(source_file, start, end, full_dir)
        rows = []
        for position, image in enumerate(images):
            detections = ovd.detect_image("owlv2", str(image), [phrase],
                                          score_threshold=0.1)
            index = (dense["frames"][position]["frame_index"]
                     if position < len(dense["frames"]) else None)
            lips = measured[position] if position < len(measured) else {}
            distance = None
            entity = None
            for detection in detections:
                for face in lips.get("faces") or []:
                    value = object_contact.box_to_lips_distance(detection.box,
                                                                face)
                    if value is None:
                        continue
                    if distance is None or value < distance:
                        distance = value
                        entity = detection.score
            rows.append({"frame_index": index,
                         "distance": (round(distance, 4)
                                      if distance is not None else None),
                         "detections": len(detections),
                         "score": entity})
        contact = object_contact.CONTACT_DISTANCE_FACE_WIDTHS
        arrival = next((row for row in rows
                        if row["distance"] is not None
                        and row["distance"] <= contact), None)
        return {
            "window": {"start_s": round(start, 3), "end_s": round(end, 3),
                       "frames_decoded": len(rows)},
            "contact_distance": contact,
            "arrival_frame": arrival["frame_index"] if arrival else None,
            "arrival_distance": arrival["distance"] if arrival else None,
            "seconds": round(time.perf_counter() - started, 1),
            "rows": rows,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="m0_object_contact_occurrences")
    parser.add_argument("--project", required=True)
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--digest", default=None)
    parser.add_argument("--refine-occurrence", type=int, default=None,
                        help="refine this occurrence of the object class to "
                             "a frame, with the object's own geometry")
    parser.add_argument("--refine-window", type=float, default=0.5)
    args = parser.parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    root = Path(args.memory_root).expanduser()
    from library.tools import source_memory

    recorded = source_memory.load_recorded_fingerprints(str(project))
    digests = []
    for clip in source_memory.load_catalog(str(project)):
        digest, _ = source_memory.digest_for_clip(str(project), clip, recorded)
        if digest and digest not in digests:
            digests.append(digest)
    digest = args.digest or (digests[0] if digests else None)
    if digest is None:
        raise SystemExit("the project catalog names no source")
    m7 = source_memory.read_slot(
        source_memory.source_dir(digest, root) / source_memory.SLOT_EVENTS)
    m2 = source_memory.read_slot(
        source_memory.source_dir(digest, root) / source_memory.SLOT_FRAMES_INDEX)
    objects = source_memory.read_slot(
        source_memory.source_dir(digest, root) / source_memory.SLOT_OBJECTS)
    frame_index_by_position = {
        position: int(frame.get("frame_index"))
        for position, frame in enumerate((m2 or {}).get("frames") or [])
        if frame.get("frame_index") is not None}
    candidates = (m7 or {}).get("candidates") or {}
    report = {
        "content_digest": digest,
        "hand": _class_report(
            (candidates.get(HAND_CLASS) or {}).get("spans") or [],
            frame_index_by_position),
        "object": _class_report(
            (candidates.get(OBJECT_CLASS) or {}).get("spans") or [],
            frame_index_by_position),
        "objects_record": {
            "measured": bool(objects),
            "detections": len((objects or {}).get("observations") or []),
            "identities": len((objects or {}).get("identities") or []),
            "report": (objects or {}).get("report"),
        },
        "candidates_unavailable": (m7 or {}).get("candidates_unavailable"),
    }
    print(json.dumps(report, indent=2))
    if args.refine_occurrence is not None:
        rows = report["object"]["rows"]
        if not 1 <= args.refine_occurrence <= len(rows):
            raise SystemExit("no such occurrence")
        row = rows[args.refine_occurrence - 1]
        source_file = (m7 or {}).get("source_file")
        refined = refine_occurrence(source_file, row["onset_seconds"],
                                    window=args.refine_window)
        arrival = _nearest_arrival(row["onset_seconds"])
        frame = refined.get("arrival_frame")
        refined["nearest_arrival"] = arrival["name"]
        refined["arrival_frame_truth"] = arrival["frame"]
        refined["frame_error"] = (frame - arrival["frame"]
                                   if frame is not None else None)
        print(json.dumps({"refinement": refined}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

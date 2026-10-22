"""Object measurements: ONE identity per physical object, per source.

**What this measures.** The object half of the persistent-identity
lane (:mod:`library.tools.analysis.object_identity`, PR 3134): the
detector's per-frame answers about a named object, given ONE identity
per physical object, with the visibility state measured rather than
assumed. The record it writes is the per-source ``objects.json`` slot.

**Why the coarse candidate lane needs it.** The hand-at-lips candidate
measures the HAND. A speaker who holds an object at his face
throughout therefore measures a contact episode that is not an arrival
of the object, and the ordinal of the class is the ordinal of the
hand's contacts rather than the ordinal of the object's arrivals. The
object's own contact is a different measurement, and this slot is what
it is measured from: the object's BOX, per frame, with the identity
that says which physical object the box is.

**The cadence is the M2 cadence, on purpose.** The frames are the
source's own M2 sample - the frames the faces and hands were measured
on - so the object's box and the lips' geometry are measured on the
SAME source frame and can be joined by frame index. A different
cadence would be a different measurement and the two halves would not
join.

**The frames are decoded at the source's own resolution.** The M2
sample is a thumbnail (384 px wide) and a car key ring in a thumbnail
is a few pixels; the detector is asked about the SOURCE's frames, cut
at the M2 sample's own frame indices.

**What this is not.** It is not a claim that a named object is absent
from a frame the detector did not answer about: a frame with no
detection carries no measurement, and a reader is told which frames
were inspected. It is not a cross-clip identity: one source here, and
the identity is scoped to it.

    python3 -m library.tools.object_measurements build <project> \\
        --phrase "car keys"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from library.tools import project_ref, source_memory
from library.tools.ren_refusal import RenRefusal

OBJECTS_STATUS = "measured"
"""The status a measured object record carries."""

METHOD = "open-vocabulary-detection + object-identity over the M2 cadence"
"""How the object record was measured, recorded in the record itself."""

DEFAULT_DETECTOR = "owlv2"
"""The detector asked about the named object, unless the caller names one."""

DEFAULT_PHRASES = ("car keys",)
"""The phrases asked about when the caller names none.

The M0 fixture's object is the car's key ring, and the phrase names
what the detector is asked ABOUT - not what it answers: the detector's
own label travels beside the question in every row.
"""


class ObjectMeasurementError(RuntimeError):
    """The object measurement could not be taken, and says why."""


def _detector_module():
    from library.tools.analysis import open_vocabulary_detect as ovd

    return ovd


def measure_source_objects(source_file: str | os.PathLike,
                            content_digest: str, *,
                            phrases: Sequence[str] = DEFAULT_PHRASES,
                            detector: str = DEFAULT_DETECTOR,
                            score_threshold: float | None = None,
                            root: Optional[Path] = None,
                            workdir: str | os.PathLike | None = None,
                            ) -> dict:
    """Measure one source's named objects and write the slot.

    The frames are the M2 sample's own frame indices, decoded at the
    source's resolution in ONE pass; the detector answers about the
    named object on each; the identity lane gives the answers one
    identity per physical object.
    """
    from library.tools.analysis import object_identity, open_vocabulary_detect as ovd

    names = [str(name).strip() for name in phrases if str(name).strip()]
    if not names:
        raise ObjectMeasurementError(
            "a measurement names at least one object; none was named")
    m2 = source_memory.read_m2(content_digest, root)
    if m2 is None or not source_memory.is_fresh(m2, str(source_file)):
        raise ObjectMeasurementError(
            f"{os.path.basename(str(source_file))} has no fresh M2 frame "
            "sample; run `python3 -m library.tools.source_memory frames` "
            "first")
    indices = [frame.get("frame_index") for frame in m2["frames"]]
    times = [float(frame.get("t") or 0.0) for frame in m2["frames"]]
    if any(index is None for index in indices):
        raise ObjectMeasurementError(
            "the M2 sample carries no frame index; a detection needs the "
            "source frame it was measured on")
    geometry = ovd_detect_geometry(str(source_file))
    temp_dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(
        prefix="ren-object-measurements-"))
    temp_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        images = _extract_frames(str(source_file), indices, temp_dir)
        detections = _detect_frames(images, names, detector=detector,
                                    score_threshold=score_threshold)
    finally:
        if workdir is None:
            shutil.rmtree(temp_dir, ignore_errors=True)
    elapsed = time.perf_counter() - started
    frames = [
        object_identity.Frame(
            frame_id=f"frame-{int(index):06d}", clip_id=str(content_digest),
            time_s=time_s, image=str(image), width=int(geometry["width"]),
            height=int(geometry["height"]), scope=str(content_digest),
            source_path=str(source_file))
        for index, time_s, image in zip(indices, times, images)]
    observed = []
    for frame, rows in zip(frames, detections):
        for row in rows:
            box = tuple(float(value) for value in row["box"])
            if len(box) != 4:
                continue
            observed.append(object_identity.ObservedDetection(
                clip_id=frame.clip_id, frame_id=frame.frame_id,
                time_s=frame.time_s, phrase=str(row["phrase"]), box=box,
                score=float(row["score"]), image=frame.image))
    # One source: the appearance cannot decide a cross-clip identity, and
    # the frames are deleted when the measurement ends.
    resolution = object_identity.resolve(frames, observed,
                                         measure_appearance=False)
    by_frame = {frame.frame_id: (int(index), time_s)
                for frame, index, time_s in zip(frames, indices, times)}
    observations = []
    for row in resolution.observations:
        index, time_s = by_frame.get(row["frame_id"], (None, row.get("time_s")))
        observations.append({
            "frame_id": row["frame_id"],
            "frame_index": index,
            "t": round(float(time_s or 0.0), 6),
            "entity_id": row.get("entity_id"),
            "phrase": row.get("class"),
            "status": row.get("status"),
            "state": row.get("state"),
            "box": row.get("box"),
            "score": row.get("score"),
        })
    identities: dict[str, dict] = {}
    for row in observations:
        entity_id = str(row.get("entity_id") or "")
        if not entity_id:
            continue
        entry = identities.setdefault(entity_id, {
            "entity_id": entity_id, "phrase": row.get("phrase"),
            "frames_observed": 0, "first_t": row.get("t"),
            "last_t": row.get("t")})
        if row.get("status") == "observed":
            entry["frames_observed"] += 1
            entry["first_t"] = min(entry["first_t"], row.get("t"))
            entry["last_t"] = max(entry["last_t"], row.get("t"))
    record = {
        "content_digest": str(content_digest),
        "size_bytes": m2.get("size_bytes"),
        "source_file": os.path.abspath(str(source_file)),
        "status": OBJECTS_STATUS,
        "method": METHOD,
        "detector": detector,
        "phrases": names,
        "coordinates": "image-normalised, top-left origin",
        "frame_count": len(frames),
        "frame_indices": [int(index) for index in indices],
        "source_geometry": geometry,
        "observations": observations,
        "identities": sorted(identities.values(),
                             key=lambda entry: (entry.get("first_t") or 0.0)),
        "report": resolution.report,
        "cost": {"seconds": round(elapsed, 1),
                 "frames_inspected": len(frames),
                 "detections": len(observed)},
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root)
        / source_memory.SLOT_OBJECTS, record)
    return {
        "content_digest": str(content_digest),
        "source_file": record["source_file"],
        "frames_inspected": len(frames),
        "detections": len(observed),
        "identities": len(identities),
        "ambiguous_starts": resolution.report.get("ambiguous_starts"),
        "seconds": round(elapsed, 1),
    }


def ovd_detect_geometry(source_file: str) -> dict:
    """The source's frame geometry, measured with ffprobe."""
    from library.tools.analysis.object_grounding import probe_source

    return probe_source(source_file)


def _extract_frames(source_file: str, indices: Sequence[int],
                    out_dir: Path) -> list[Path]:
    """The named source frames, in ONE decode pass, at full resolution."""
    expression = "+".join(f"eq(n\\,{int(index)})" for index in indices)
    pattern = str(out_dir / "%06d.jpg")
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source_file,
        "-vf", f"select='{expression}'", "-fps_mode", "passthrough",
        "-q:v", "2", pattern,
    ]
    import subprocess

    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            check=False)
    if result.returncode != 0:
        raise ObjectMeasurementError(
            f"ffmpeg could not extract the frames of {source_file}: "
            f"{result.stderr.strip() or 'unknown error'}")
    extracted = sorted(out_dir.glob("*.jpg"))
    if len(extracted) != len(indices):
        raise ObjectMeasurementError(
            f"ffmpeg returned {len(extracted)} frames for "
            f"{len(indices)} requested indices from {source_file}")
    return extracted


def _detect_frames(images: Sequence[Path], phrases: Sequence[str], *,
                   detector: str, score_threshold: float | None
                   ) -> list[list[dict]]:
    """The detector's answer about the named object, per frame."""
    ovd = _detector_module()
    threshold = (float(score_threshold) if score_threshold is not None
                 else float(ovd.DEFAULT_SCORE_THRESHOLD))
    out: list[list[dict]] = []
    for image in images:
        try:
            found = ovd.detect_image(detector, str(image), list(phrases),
                                     score_threshold=threshold)
        except ovd.DetectorNotAvailable as exc:
            raise ObjectMeasurementError(
                f"the {detector} detector cannot run here: {exc}") from exc
        out.append([{"phrase": row.phrase, "box": list(row.box),
                     "score": float(row.score)} for row in found])
    return out


def read_objects(content_digest: str,
                 root: Optional[Path] = None) -> Optional[dict]:
    """A digest's object record, or None when never measured."""
    path = (source_memory.source_dir(content_digest, root)
            / source_memory.SLOT_OBJECTS)
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    if (not isinstance(document, dict)
            or document.get("content_digest") != content_digest):
        return None
    return document


@source_memory._project_scoped
def build_project_objects(project_folder: str, *,
                           phrases: Sequence[str] = DEFAULT_PHRASES,
                           detector: str = DEFAULT_DETECTOR,
                           score_threshold: float | None = None,
                           root: Optional[Path] = None) -> dict:
    """The object measurement for every catalog source of a project."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    rows, failed = [], []
    seen: set[str] = set()
    for clip in source_memory.load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            rows.append({"clip_id": clip_id, "skipped": "media-offline"})
            continue
        digest, _ = source_memory.digest_for_clip(project_folder, clip,
                                                  recorded)
        if digest is None:
            rows.append({"clip_id": clip_id, "skipped": "no content digest"})
            continue
        if digest in seen:
            rows.append({"clip_id": clip_id, "skipped": "reused"})
            continue
        seen.add(digest)
        try:
            rows.append({"clip_id": clip_id,
                         **measure_source_objects(path, digest,
                                                  phrases=phrases,
                                                  detector=detector,
                                                  score_threshold=score_threshold,
                                                  root=root)})
        except Exception as exc:  # noqa: BLE001 - reported per clip
            failed.append(clip_id)
            rows.append({"clip_id": clip_id,
                         "failed": f"{type(exc).__name__}: {exc}"})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": rows, "failed": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="object_measurements",
        description="Object identities per source: the named object, one "
                    "identity per physical object, on the M2 cadence. "
                    "Heavy: it decodes the source and loads a detector.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="measure a project's sources")
    p_build.add_argument("project")
    p_build.add_argument("--phrase", action="append", default=None,
                         help="the object to ask about; repeatable")
    p_build.add_argument("--detector", default=DEFAULT_DETECTOR)
    p_build.add_argument("--score-threshold", type=float, default=None)
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry objects")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    project = project_ref.require_project_state(
        project_ref.resolve_project_folder(args.project))
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        phrases = tuple(args.phrase) if args.phrase else DEFAULT_PHRASES
        report = build_project_objects(project, phrases=phrases,
                                       detector=args.detector,
                                       score_threshold=args.score_threshold,
                                       root=root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(project)
    rows = []
    for clip in source_memory.load_catalog(project):
        digest, _ = source_memory.digest_for_clip(project, clip, recorded)
        document = read_objects(digest, root) if digest else None
        rows.append({"clip_id": clip.get("clip_id"),
                     "content_digest": digest,
                     "objects": "measured" if document else "missing",
                     "identities": len((document or {}).get("identities") or []),
                     "detections": len((document or {}).get("observations")
                                       or [])})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)

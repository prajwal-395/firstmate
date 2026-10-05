"""The canonical temporal world model: M9 (`world_model.json`) of the
per-source footage memory.

Gap 1 of the footage world model gap map (the captain's top-priority
gap): Ren has strong per-lane measurement (M0-M8) but no canonical
temporal world model connecting the lanes into a unified understanding.
A query like "where does Craig gesture while speaking" cannot be
answered: M7 has speaking spans, M3 has hand geometry, but nothing
joins them into "gesture while speaking."

This module reads M0-M8 and emits a joined per-second timeline. No new
measurement: every observation in the world model comes from a lane
that already measured it. The join is pure data manipulation over the
source memory.

**The structure.** One entry per second of the source duration. Each
entry carries every observation that falls within that second, so a
query at time T is one array index:

    {
      "content_digest": ...,
      "duration_seconds": 1234.5,
      "lanes": ["m0", "m1", "m3", "m3b", "m4", "m5", "m7"],
      "timeline": [
        {
          "t": 0,
          "speech": {"words": [...], "speaker": {...} or null},
          "faces": [{"t": 0.0, "track_id": "face_001", "box": [...],
                     "lips": {...}, "hands": [...]}],
          "scene": {"location": ..., "type": ..., "lighting": ...} or null,
          "sound": {"events": [...]},
          "events": {"on_screen": [...], "speaking": [...],
                     "hand_near_mouth": [...]}
        },
        ...
      ],
      "entities": {"face_tracks": [...], "voice_tracks": [...]},
      "multicam": {"group_id": ..., "offset_to_reference_seconds": ...}
    }

**What each lane contributes:**

- M0: duration, source identity.
- M1: word timings -> `speech.words`.
- M3: faces (box, lips) and hands (joints) at the M2 cadence ->
  `faces`.
- M3b: face tracks and voice tracks -> `entities`, and the track_id
  each M3 face carries (via M7's `assign_face_tracks`).
- M4: scene segments (location, type, lighting) -> `scene`.
- M5: sound events (label, start, end, confidence) -> `sound.events`.
- M6: multicam group and offset -> `multicam`.
- M7: on_screen, speaking and hand_near_mouth spans -> `events`.
- M8: VLM verdicts on candidate spans -> `events.hand_near_mouth`
  verdicts.

A lane that was never built is ABSENT from `lanes`, never defaulted:
the world model joins what exists and says what it joined.

    python3 -m library.tools.world_model build <project>
    python3 -m library.tools.world_model status <project>
    python3 -m library.tools.world_model query <project> --t 42.5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

try:
    from library.tools import (
        conversation_clock,
        event_spans,
        expression_classifier,
        person_entity,
        person_measurements,
        source_memory,
    )
    from library.tools import span_verification
except ImportError:  # imported as `tools.*` from inside library/
    from tools import (
        conversation_clock,
        event_spans,
        person_entity,
        person_measurements,
        source_memory,
    )
    from tools import span_verification


STATUS_BUILT = "built"


def _overlap(start: float, end: float, lo: float, hi: float) -> bool:
    """Whether [start, end) overlaps [lo, hi)."""
    return start < hi and end > lo


def _words_at(m1: Optional[dict], lo: float, hi: float) -> List[dict]:
    """Words active during [lo, hi), each with its precise time."""
    if not m1:
        return []
    words = []
    for utt in m1.get("utterances") or []:
        for word in utt.get("words") or []:
            if _overlap(word["start"], word["end"], lo, hi):
                words.append(word)
    return words


def _speaker_at(m7: Optional[dict], lo: float, hi: float) -> Optional[dict]:
    """The speaking span covering [lo, hi), or None."""
    if not m7:
        return None
    for span in (m7.get("predicates") or {}).get("speaking", {}).get("spans") or []:
        if _overlap(span["start"], span["end"], lo, hi):
            return {"voice_track": span.get("voice_track"),
                    "face_track": span.get("face_track")}
    return None


def _faces_at(m3: Optional[dict], assignment: Optional[List[List[Optional[str]]]],
               lo: float, hi: float,
               m3c: Optional[dict] = None) -> List[dict]:
    """M3 faces within [lo, hi), each with its track_id from the assignment
    and its expression label from M3c when present."""
    if not m3:
        return []
    faces = []
    for i, frame in enumerate(m3.get("frames") or []):
        t = frame["t"]
        if not (lo <= t < hi):
            continue
        row = assignment[i] if assignment and i < len(assignment) else []
        m3c_frames = (m3c or {}).get("frames") or []
        m3c_faces = m3c_frames[i].get("faces") if i < len(m3c_frames) else []
        for j, face in enumerate(frame.get("faces") or []):
            track_id = row[j] if j < len(row) else None
            entry = {
                "t": t,
                "track_id": track_id,
                "box": face.get("box"),
                "lips": {"outer": face.get("outer_lips"),
                         "inner": face.get("inner_lips")},
                "hands": frame.get("hands") or [],
            }
            if j < len(m3c_faces):
                entry["expression"] = m3c_faces[j].get("expression")
                entry["expression_confidence"] = m3c_faces[j].get("confidence")
            faces.append(entry)
    return faces


def _scene_at(m4: Optional[dict], t: float) -> Optional[dict]:
    """The M4 scene segment containing time t, or None."""
    if not m4:
        return None
    for seg in m4.get("scenes") or []:
        if seg["start"] <= t < seg["end"]:
            return {k: seg.get(k) for k in
                    ("start", "end", "location", "type", "lighting",
                     "notable_features")}
    return None


def _sound_at(m5: Optional[dict], lo: float, hi: float) -> List[dict]:
    """Sound events overlapping [lo, hi)."""
    if not m5:
        return []
    return [e for e in m5.get("sound_events") or []
            if _overlap(e["start"], e["end"], lo, hi)]


def _events_at(m7: Optional[dict], m8: Optional[dict],
               lo: float, hi: float) -> dict:
    """M7 spans overlapping [lo, hi), with M8 verdicts on candidates."""
    events = {"on_screen": [], "speaking": [], "hand_near_mouth": []}
    if not m7:
        return events
    predicates = m7.get("predicates") or {}
    for span in predicates.get("on_screen", {}).get("spans") or []:
        if _overlap(span["start"], span["end"], lo, hi):
            events["on_screen"].append(span.get("face_track"))
    for span in predicates.get("speaking", {}).get("spans") or []:
        if _overlap(span["start"], span["end"], lo, hi):
            events["speaking"].append({
                "voice_track": span.get("voice_track"),
                "face_track": span.get("face_track"),
            })
    for span in (m7.get("candidates") or {}).get("hand_near_mouth", {}).get("spans") or []:
        if _overlap(span["start"], span["end"], lo, hi):
            events["hand_near_mouth"].append({
                "face_track": span.get("face_track"),
                "start": span["start"],
                "end": span["end"],
                "verdicts": _verdicts_for_span(span, m8),
            })
    return events


def _verdicts_for_span(span: dict, m8: Optional[dict]) -> List[dict]:
    """M8 verdicts whose frames match this candidate span's selected frames."""
    if not m8:
        return []
    selected = span_verification.select_frames(span)
    selected_times = [round(f["t"], 3) for f in selected]
    verdicts = []
    for verdict in m8.values():
        if not isinstance(verdict, dict):
            continue
        if [round(t, 3) for t in verdict.get("frames") or []] == selected_times:
            verdicts.append({
                "statement": verdict.get("statement"),
                "answer": verdict.get("answer"),
                "reason": verdict.get("reason"),
                "model": verdict.get("model"),
            })
    return verdicts


def _build_entities(m3b: Optional[dict]) -> dict:
    """Face tracks and voice tracks from M3b."""
    if not m3b:
        return {"face_tracks": [], "voice_tracks": []}
    return {
        "face_tracks": [
            {"track_id": t.get("track_id"), "spans": t.get("spans") or []}
            for t in m3b.get("faces") or []
        ],
        "voice_tracks": [
            {"track_id": t.get("track_id"), "spans": t.get("spans") or []}
            for t in m3b.get("voices") or []
        ],
    }


def _build_multicam(m6: Optional[dict]) -> Optional[dict]:
    """Multicam context from M6, or None when the source is in no group."""
    if not m6:
        return None
    return {k: m6.get(k) for k in
            ("group_id", "group_members", "reference_digest",
             "offset_to_reference_seconds")}


def build_timeline(duration: float, m1: Optional[dict], m3: Optional[dict],
                   m3b: Optional[dict], m4: Optional[dict],
                   m5: Optional[dict], m7: Optional[dict],
                   m8: Optional[dict],
                   m3c: Optional[dict] = None) -> list:
    """Join the lanes into a per-second timeline.

    One entry per second from 0 to floor(duration). Each entry carries
    every observation that falls within that second, so a query at
    time T is one array index.
    """
    assignment = None
    if m3 and m3b:
        assignment = event_spans.assign_face_tracks(m3, m3b)

    timeline = []
    for t in range(int(duration) + 1):
        lo, hi = float(t), float(t + 1)
        timeline.append({
            "t": t,
            "speech": {"words": _words_at(m1, lo, hi),
                       "speaker": _speaker_at(m7, lo, hi)},
            "faces": _faces_at(m3, assignment, lo, hi, m3c),
            "scene": _scene_at(m4, lo),
            "sound": {"events": _sound_at(m5, lo, hi)},
            "events": _events_at(m7, m8, lo, hi),
        })
    return timeline


def build_source_world_model(content_digest: str, source_file: str,
                             root: Optional[Path] = None) -> dict:
    """Build the world model for one source from its M0-M8 lanes.

    Light: reads the source memory, joins, writes. No decode, no model.
    """
    m0 = source_memory.read_m0(content_digest, root)
    m1_doc, _status = source_memory.read_m1(content_digest, root)
    m3 = person_measurements.read_m3(content_digest, root)
    m3b = person_entity.read_identity(content_digest, root)
    m4 = source_memory.read_scenes(content_digest, root)
    m5 = source_memory.read_sound(content_digest, root)
    m6 = conversation_clock.read_clock(content_digest, root)
    m7 = event_spans.read_m7(content_digest, root)
    m8 = span_verification.read_verdicts(content_digest, root)
    m3c = expression_classifier.read_m3c(content_digest, root)

    duration = (m0 or {}).get("duration_seconds") or 0.0
    timeline = build_timeline(duration, m1_doc, m3, m3b, m4, m5, m7, m8, m3c)

    lanes = [name for name, doc in (
        ("m0", m0), ("m1", m1_doc), ("m3", m3), ("m3b", m3b),
        ("m3c", m3c), ("m4", m4), ("m5", m5), ("m6", m6), ("m7", m7),
    ) if doc is not None]

    record = {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": STATUS_BUILT,
        "duration_seconds": duration,
        "lanes": lanes,
        "timeline": timeline,
        "entities": _build_entities(m3b),
        "multicam": _build_multicam(m6),
    }
    source_memory.write_world_model(content_digest, record, root)
    return {"content_digest": content_digest, "source_file": source_file,
            "duration_seconds": duration, "lanes": lanes,
            "timeline_seconds": len(timeline)}


def build_project_world_model(project_folder: str,
                             root: Optional[Path] = None) -> dict:
    """Build the world model for every catalog source that carries M0."""
    results, failed = [], []
    for clip_id, digest, path in event_spans.catalog_sources(project_folder):
        try:
            account = build_source_world_model(digest, path, root)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "content_digest": digest,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def query_at_time(world_model: dict, t: float) -> Optional[dict]:
    """What is happening at time T: the timeline entry for second floor(t)."""
    timeline = world_model.get("timeline") or []
    idx = int(t)
    if 0 <= idx < len(timeline):
        return timeline[idx]
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="world_model",
        description="M9: the canonical temporal world model - a joined "
                    "per-second timeline of every lane (M0-M8) in the "
                    "source memory. Light: no decode, no model.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="M9 for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--memory-root")

    p_status = sub.add_parser("status", help="which sources carry M9")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")

    p_query = sub.add_parser("query", help="what is happening at time T")
    p_query.add_argument("project")
    p_query.add_argument("--t", type=float, required=True)
    p_query.add_argument("--memory-root")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_world_model(args.project, root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    if args.command == "status":
        recorded = source_memory.load_recorded_fingerprints(args.project)
        rows = []
        for clip in source_memory.load_catalog(args.project):
            digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
            doc = source_memory.read_world_model(digest, root) if digest else None
            rows.append({"clip_id": clip.get("clip_id"), "content_digest": digest,
                         "m9": "built" if doc else "missing",
                         "lanes": doc.get("lanes") if doc else [],
                         "timeline_seconds": len(doc.get("timeline") or []) if doc else 0})
        print(json.dumps({"clips": rows}, indent=2))
        return 0
    if args.command == "query":
        recorded = source_memory.load_recorded_fingerprints(args.project)
        catalog = source_memory.load_catalog(args.project)
        if not catalog:
            print("no catalog clips", file=sys.stderr)
            return 1
        clip = catalog[0]
        digest, _ = source_memory.digest_for_clip(args.project, clip, recorded)
        if digest is None:
            print("no content digest for the first catalog clip", file=sys.stderr)
            return 1
        world_model = source_memory.read_world_model(digest, root)
        if world_model is None:
            print(f"no world model for {digest[:12]}; run `python3 -m "
                  f"library.tools.world_model build {args.project}`",
                  file=sys.stderr)
            return 1
        entry = query_at_time(world_model, args.t)
        if entry is None:
            print(f"time {args.t} is outside the source's "
                  f"{world_model.get('duration_seconds')}s duration",
                  file=sys.stderr)
            return 1
        print(json.dumps(entry, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

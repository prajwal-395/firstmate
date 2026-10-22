"""A derived one-second compatibility view: M9 (`world_model.json`) of
the per-source footage memory.

The Project Model and source-memory lanes retain authoritative source
intervals and sample timestamps. M9 joins those measurements into a
convenient per-second lookup view; its bins are never event boundaries or
the source of timing truth.
A query like "where does Craig gesture while speaking" is answered
off the join: M7 has speaking spans, M3 has hand geometry, and this
module joins them into "gesture while speaking."

This module reads M0-M8 and M10 and emits a joined per-second
timeline. No new measurement: every observation in the world model
comes from a lane that already measured it. The join is pure data
manipulation over the source memory.

**The view is a compatibility surface; the QUESTION surface is
`library/tools/world_model_query.py`.** A per-second view answers "what
is happening at time T" and nothing else: a span question and an
entity question have no route, and answering either means reading the
whole document. The query module answers both off the canonical records
the view is derived from, at the records' own timing, with a provenance
row per fact - and this module's `interval`, `entity` and `ask`
subcommands are its front door.

**The derived structure.** One entry per second of the source duration. Each
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
                     "hand_near_mouth": [...]},
          "relationships": {"holding": [...], "pointing": [...],
                            "looking_at": [...]}
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
- M10: holding, pointing and looking_at candidate spans ->
  `relationships` (geometric candidates from `relationships.py`, never
  answers).

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
        claim_provenance,
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
        claim_provenance,
        conversation_clock,
        event_spans,
        expression_classifier,
        person_entity,
        person_measurements,
        source_memory,
    )
    from tools import span_verification


STATUS_BUILT = "built"

PROVENANCE_VERSION = 1
"""The version of the provenance vocabulary a world model carries."""


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


def _speakers_at(m7: Optional[dict], lo: float, hi: float) -> List[dict]:
    """Every speaking span overlapping [lo, hi).

    All of them, not the first: two lanes may disagree about who is
    speaking in the same second, and a disagreement the join drops is
    a disagreement nobody can see.
    """
    if not m7:
        return []
    spans = []
    for span in (m7.get("predicates") or {}).get("speaking", {}).get("spans") or []:
        if _overlap(span["start"], span["end"], lo, hi):
            spans.append({
                "voice_track": span.get("voice_track"),
                "face_track": span.get("face_track"),
                "start": span.get("start"),
                "end": span.get("end"),
            })
    return spans


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


def _scenes_at(m4: Optional[dict], t: float) -> List[dict]:
    """Every M4 scene segment containing time t.

    All of them, not the first: two overlapping segments that describe
    the same second differently are a disagreement, and the join
    records it rather than keeping the first and losing the second.
    """
    if not m4:
        return []
    scenes = []
    for seg in m4.get("scenes") or []:
        if seg["start"] <= t < seg["end"]:
            scenes.append({k: seg.get(k) for k in
                           ("start", "end", "location", "type", "lighting",
                            "notable_features")})
    return scenes


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
                "start": span.get("start"),
                "end": span.get("end"),
            })
    for span in (m7.get("candidates") or {}).get("hand_near_mouth", {}).get("spans") or []:
        if _overlap(span["start"], span["end"], lo, hi):
            verdicts = _verdicts_for_span(span, m8)
            events["hand_near_mouth"].append({
                "face_track": span.get("face_track"),
                "start": span["start"],
                "end": span["end"],
                "verdicts": verdicts,
                "verification": _verification_for(verdicts),
            })
    return events


def _verification_for(verdicts: List[dict]) -> dict:
    """What the verifier said about a candidate span.

    A candidate nobody verified is not a candidate nobody refuted: the
    two states read alike when a world model carries no status, and
    that is how an unverified detection comes to look like a confirmed
    one. A verdict is a MODEL's judgement, so it records a status and
    never raises the candidate's class to a measurement.
    """
    if not verdicts:
        return {"status": "unverified", "verifier": None,
                "verifier_kind": None, "answers": []}
    answers = [str(verdict.get("answer") or "") for verdict in verdicts]
    decided = {answer for answer in answers if answer in ("yes", "no")}
    if decided == {"yes", "no"}:
        status = "disputed"
    elif decided == {"yes"}:
        status = "verified"
    elif decided == {"no"}:
        status = "refuted"
    else:
        status = "unverified"
    return {
        "status": status,
        "verifier": "library.tools.span_verification",
        "verifier_kind": "model",
        "answers": sorted(set(answers)),
        "class": claim_provenance.claim_class("verdict"),
    }


def _relationships_at(m10: Optional[dict], lo: float, hi: float) -> dict:
    """M10 relationship candidate spans overlapping [lo, hi).

    The geometric candidates `relationships.py` measured (holding,
    pointing, looking_at) - never answers, the same standing decision as
    M7's hand_near_mouth.
    """
    measured = {"holding": [], "pointing": [], "looking_at": []}
    if not m10:
        return measured
    for name in measured:
        for span in ((m10.get("relationships") or {}).get(name, {})
                     .get("spans") or []):
            if _overlap(span["start"], span["end"], lo, hi):
                measured[name].append({
                    "owners": span.get("owners"),
                    "start": span["start"],
                    "end": span["end"],
                })
    return measured


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
                "frames": verdict.get("frames") or [],
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
    block = {k: m6.get(k) for k in
             ("group_id", "group_members", "reference_digest",
              "offset_to_reference_seconds")}
    block["conflicts"] = _multicam_conflicts(m6)
    return block


def _multicam_conflicts(m6: Optional[dict]) -> List[dict]:
    """Two measurements of this source's offset that disagree.

    The audio n-gram measurement and the Resolve timeline cross-check
    are both instruments, and they do not always agree. The world model
    used to carry the n-gram offset alone, so the disagreement was
    invisible; both readings are kept and the disagreement is named.
    """
    conflicts: List[dict] = []
    if not m6:
        return conflicts
    check = m6.get("resolve_cross_check") or {}
    if check.get("agrees_with_ngram_offset") is False:
        conflicts.append({
            "quantity": "offset_to_reference_seconds",
            "detail": "the audio n-gram measurement and the Resolve "
                      "timeline cross-check disagree about this source's "
                      "offset to the reference",
            "claims": [
                {"modality": "m6_ngram_measurement",
                 "value": m6.get("offset_to_reference_seconds"),
                 "class": "measured"},
                {"modality": "resolve_timeline_cross_check",
                 "value": check.get("offset_seconds"),
                 "class": "measured"},
            ],
        })
    return conflicts


def _provenance_for(entry: dict) -> dict:
    """The provenance of every claim this entry carries.

    Only the claims that are HERE: a lane that was never built leaves
    no provenance behind, so an absent claim is absent rather than
    defaulted.
    """
    provenance: dict = {}
    speech = entry.get("speech") or {}
    events = entry.get("events") or {}
    if speech.get("words"):
        provenance["words"] = claim_provenance.tag("words")
    if speech.get("speakers") or events.get("speaking"):
        provenance["speaking_span"] = claim_provenance.tag("speaking_span")
    if entry.get("faces"):
        provenance["face_box"] = claim_provenance.tag("face_box")
    if entry.get("scene"):
        provenance["scene"] = claim_provenance.tag("scene")
    if entry.get("cinematography"):
        provenance["scene_boundary"] = claim_provenance.tag("scene_boundary")
    if (entry.get("sound") or {}).get("events"):
        provenance["sound_event"] = claim_provenance.tag("sound_event")
    if events.get("on_screen"):
        provenance["visible_span"] = claim_provenance.tag("visible_span")
    if events.get("hand_near_mouth"):
        provenance["candidate_span"] = claim_provenance.tag("candidate_span")
        if any(candidate.get("verdicts")
               for candidate in events["hand_near_mouth"]):
            provenance["verdict"] = claim_provenance.tag("verdict")
    if any(entry.get("relationships", {}).get(name)
           for name in (entry.get("relationships") or {})):
        provenance["relationship"] = claim_provenance.tag("relationship")
    return provenance


def _conflicts_for(t: int, speakers: List[dict], scenes: List[dict],
                   events: dict) -> List[dict]:
    """The observations that disagree about this second.

    Both claims stay where they are; the disagreement is recorded, so a
    reader can see that two lanes said different things rather than
    finding one of them silently gone.
    """
    conflicts: List[dict] = []
    face_tracks = {span.get("face_track") for span in speakers
                   if span.get("face_track")}
    if len(face_tracks) > 1:
        conflicts.append({
            "quantity": "who is speaking",
            "at": t,
            "detail": "the speaking predicate attributes this second to "
                      "more than one face",
            "claims": [{"modality": "m7_speaking_span",
                        "value": sorted(face_tracks)}],
        })
    scene_readings = {(scene.get("location"), scene.get("type"))
                      for scene in scenes}
    if len(scene_readings) > 1:
        conflicts.append({
            "quantity": "which scene this second is in",
            "at": t,
            "detail": "two scene segments describe this second differently",
            "claims": [{"modality": "m4_scene_segment",
                        "value": [f"{location} / {kind}"
                                  for location, kind in sorted(
                                      scene_readings, key=str)]}],
        })
    for candidate in (events.get("hand_near_mouth") or []):
        if (candidate.get("verification") or {}).get("status") == "disputed":
            conflicts.append({
                "quantity": "whether the candidate happened",
                "at": t,
                "detail": "the verifier's verdicts on this candidate "
                          "disagree with each other",
                "claims": [{"modality": "m7_candidate_span",
                            "value": {"face_track": candidate.get("face_track"),
                                      "start": candidate.get("start"),
                                      "end": candidate.get("end")}},
                           {"modality": "m8_verdict",
                            "value": candidate.get("verification", {}).get(
                                "answers")}],
            })
    return conflicts


def build_timeline(duration: float, m1: Optional[dict], m3: Optional[dict],
                   m3b: Optional[dict], m4: Optional[dict],
                   m5: Optional[dict], m7: Optional[dict],
                   m8: Optional[dict], m10: Optional[dict],
                   m3c: Optional[dict] = None) -> list:
    """Join the lanes into a per-second timeline.

    One entry per second from 0 to floor(duration). Each entry carries
    every observation that falls within that second, so a query at
    time T is one array index, and the class of every claim it carries
    under `provenance`.
    """
    assignment = None
    if m3 and m3b:
        assignment = event_spans.assign_face_tracks(m3, m3b)

    timeline = []
    for t in range(int(duration) + 1):
        lo, hi = float(t), float(t + 1)
        speakers = _speakers_at(m7, lo, hi)
        scenes = _scenes_at(m4, lo)
        events = _events_at(m7, m8, lo, hi)
        entry = {
            "t": t,
            "speech": {"words": _words_at(m1, lo, hi),
                       "speaker": ({"voice_track": speakers[0].get("voice_track"),
                                    "face_track": speakers[0].get("face_track")}
                                   if speakers else None),
                       "speakers": speakers},
            "faces": _faces_at(m3, assignment, lo, hi, m3c),
            "scene": scenes[0] if scenes else None,
            "cinematography": _cinematography_at(m4, lo),
            "sound": {"events": _sound_at(m5, lo, hi)},
            "events": events,
            "relationships": _relationships_at(m10, lo, hi),
        }
        entry["provenance"] = _provenance_for(entry)
        entry["conflicts"] = _conflicts_for(t, speakers, scenes, events)
        timeline.append(entry)
    return timeline


def _cinematography_at(m4: Optional[dict], time_seconds: float) -> Optional[dict]:
    """Expose M4's measured hierarchy at this M9 second without re-labeling it."""
    hierarchy = (m4 or {}).get("scene_cinematography")
    if not isinstance(hierarchy, dict) or hierarchy.get("status") != "measured":
        return None
    result = {"method": hierarchy["method"]}
    for key in ("scenes", "shots", "subshots", "sequences"):
        active = [item for item in hierarchy[key]
                  if item["start"] <= time_seconds < item["end"]]
        result[key] = [item["id"] for item in active]
    return result


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
    m10 = source_memory.read_relationships(content_digest, root)
    m3c = expression_classifier.read_m3c(content_digest, root)

    duration = (m0 or {}).get("duration_seconds") or 0.0
    timeline = build_timeline(duration, m1_doc, m3, m3b, m4, m5, m7, m8, m10,
                              m3c)

    lanes = [name for name, doc in (
        ("m0", m0), ("m1", m1_doc), ("m3", m3), ("m3b", m3b),
        ("m3c", m3c), ("m4", m4), ("m5", m5), ("m6", m6), ("m7", m7),
        ("m10", m10),
    ) if doc is not None]

    entities = _build_entities(m3b)
    provenance = {}
    if entities.get("face_tracks") or entities.get("voice_tracks"):
        provenance["identity"] = claim_provenance.tag("identity")
    if m6:
        provenance["multicam_offset"] = claim_provenance.tag("multicam_offset")

    record = {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": STATUS_BUILT,
        "duration_seconds": duration,
        "lanes": lanes,
        "provenance_version": PROVENANCE_VERSION,
        "provenance": provenance,
        "timeline": timeline,
        "entities": entities,
        "multicam": _build_multicam(m6),
    }
    source_memory.write_world_model(content_digest, record, root)
    return {"content_digest": content_digest, "source_file": source_file,
            "duration_seconds": duration, "lanes": lanes,
            "timeline_seconds": len(timeline)}


def build_project_world_model(project_folder: str,
                             root: Optional[Path] = None) -> dict:
    """Build the world model for every catalog source that carries M0.

    One lane per distinct digest: catalog_sources dedups serially up
    front, and each unit only reads slots, joins, and atomically writes
    its own record - no decode, no model, no lock, no warm-up. Units
    materialize once; input order is restored, identical to serial.
    """
    from collections import deque

    from library.tools.concurrency_routing import run_lanes

    def _one(job):
        clip_id, digest, path = job
        try:
            account = build_source_world_model(digest, path, root)
        except Exception as exc:
            return ({"clip_id": clip_id, "content_digest": digest,
                     "failed": f"{type(exc).__name__}: {exc}"}, clip_id)
        return ({"clip_id": clip_id, **account}, None)

    results, failed = [], []
    measured = {}
    units = event_spans.catalog_sources(project_folder)
    for row, failed_id in run_lanes(_one, units):
        measured.setdefault(row["clip_id"], deque()).append(row)
        if failed_id is not None:
            failed.append(failed_id)
    for clip_id, _digest, _path in units:
        queue = measured.get(clip_id)
        if queue:
            results.append(queue.popleft())
    return {"project_folder": os.path.abspath(project_folder),
            "clips": results, "failed": failed}


def query_at_time(world_model: dict, t: float) -> Optional[dict]:
    """Return the derived bin containing T; event timing lives on source intervals."""
    timeline = world_model.get("timeline") or []
    idx = int(t)
    if 0 <= idx < len(timeline):
        return timeline[idx]
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="world_model",
        description="M9: a derived per-second lookup view over source evidence - a joined "
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

    p_interval = sub.add_parser(
        "interval", help="the facts inside a span, at their own timing")
    p_interval.add_argument("project")
    p_interval.add_argument("--clip", required=True)
    p_interval.add_argument("--start", type=float, required=True)
    p_interval.add_argument("--end", type=float, required=True)
    p_interval.add_argument("--aspect", action="append")
    p_interval.add_argument("--memory-root")

    p_entity = sub.add_parser("entity", help="one entity's occurrences")
    p_entity.add_argument("project")
    p_entity.add_argument("--clip", required=True)
    p_entity.add_argument("--entity", required=True)
    p_entity.add_argument("--memory-root")

    p_ask = sub.add_parser("ask", help="who/what/where at a span")
    p_ask.add_argument("project")
    p_ask.add_argument("--clip", required=True)
    p_ask.add_argument("--start", type=float, required=True)
    p_ask.add_argument("--end", type=float, required=True)
    p_ask.add_argument("--question", default="what")
    p_ask.add_argument("--memory-root")

    p_search = sub.add_parser(
        "search", help="search the world for media intervals")
    p_search.add_argument("project")
    p_search.add_argument("--clip", required=True)
    p_search.add_argument("--mode", required=True)
    p_search.add_argument("--text")
    p_search.add_argument("--action")
    p_search.add_argument("--relation")
    p_search.add_argument("--expression")
    p_search.add_argument("--entity")
    p_search.add_argument("--start", type=float)
    p_search.add_argument("--end", type=float)
    p_search.add_argument("--of")
    p_search.add_argument("--ordinal")
    p_search.add_argument("--relative", choices=["after", "before"])
    p_search.add_argument("--reference-start", type=float)
    p_search.add_argument("--reference-end", type=float)
    p_search.add_argument("--verified", choices=["yes", "no"])
    p_search.add_argument("--memory-root")

    p_evidence = sub.add_parser(
        "evidence", help="the evidence behind a span, without Resolve")
    p_evidence.add_argument("project")
    p_evidence.add_argument("--clip", required=True)
    p_evidence.add_argument("--start", type=float, required=True)
    p_evidence.add_argument("--end", type=float, required=True)
    p_evidence.add_argument("--aspect", action="append")
    p_evidence.add_argument("--model", action="store_true")
    p_evidence.add_argument("--memory-root")

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
    if args.command in ("interval", "entity", "ask", "search", "evidence"):
        # The world's narrow questions. The per-second view above stays
        # exactly what it is; these read the canonical records behind it.
        from library.tools import world_model_query
        argv_rest = [args.project, "--clip", args.clip]
        if args.command == "interval":
            argv_rest += ["--start", str(args.start), "--end", str(args.end)]
            for aspect in args.aspect or []:
                argv_rest += ["--aspect", aspect]
        elif args.command == "entity":
            argv_rest += ["--entity", args.entity]
        elif args.command == "ask":
            argv_rest += ["--start", str(args.start), "--end", str(args.end),
                          "--question", args.question]
        elif args.command == "search":
            argv_rest += ["--mode", args.mode]
            for name in ("text", "action", "relation", "expression",
                         "entity", "of", "ordinal", "relative", "verified"):
                value = getattr(args, name, None)
                if value:
                    argv_rest += [f"--{name}", str(value)]
            if args.start is not None and args.end is not None:
                argv_rest += ["--start", str(args.start), "--end", str(args.end)]
            if args.reference_start is not None \
                    and args.reference_end is not None:
                argv_rest += ["--reference-start", str(args.reference_start),
                              "--reference-end", str(args.reference_end)]
        else:
            argv_rest += ["--start", str(args.start), "--end", str(args.end)]
            for aspect in args.aspect or []:
                argv_rest += ["--aspect", aspect]
            if args.model:
                argv_rest += ["--model"]
        if args.memory_root:
            argv_rest += ["--memory-root", args.memory_root]
        return world_model_query.main([args.command] + argv_rest)
    return 2


if __name__ == "__main__":
    sys.exit(main())

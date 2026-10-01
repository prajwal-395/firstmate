"""Derived event spans per person: M7 (`events.json`) of the per-source
footage memory, and the structured query `ren search --predicate` reads.

Scout basis: `data/vep-long-footage-memory-scout/report.md` §3.2-3.3 and
§5 item 5 (in firstmate's home). Retrieval is STRUCTURED FIRST: "person
x predicate x time" is answered from spans precomputed here, joined
across camera angles on the M6 conversation clock, and placed on the
timeline through the source/timeline binding `timeline_transcript`
already records. No model runs at query time.

**What each predicate is made of** (one source file at a time):

- `on_screen` - a face track's M3 frames (Apple Vision faces at the M2
  cadence, ~2 Hz) joined into spans. WHICH person a Vision face is comes
  from M3b: the face takes the ArcFace track of the time-adjacent M3b
  observation whose face sits where it sits (`assign_face_tracks`). A
  face no observation vouches for is left unassigned, never guessed.
- `speaking` - a diarized voice turn (M3b `voices`, the ECAPA clusters
  of PR #1482) attributed to a face by MOUTH MOTION: a voice is that
  face's when the face's lips move more during that voice's turns than
  outside them (`link_voices_to_faces`). Co-occurrence alone - M3b's own
  `speech_face_links` basis - attaches EVERY voice to the only face on a
  single-person angle, because the listener is on screen during the
  speaker's turns too. A voice no face on an angle speaks with is
  off-screen there; it is still that person's speech on the angle that
  shows them, and reaches every other angle through the clock.

**`hand_near_mouth` is NOT a predicate here, by measurement.** The
pre-registered span rule was evaluated on every 2 Hz frame of the
captain's four Craig angles (10,873 frames, 17 labelled events):
event recall 11/17 (bar 0.80), precision 0.50 - and the frame rule with
corrected coordinates beat it on recall (14/17) at 0.43 precision, so
neither cleared the bar fixed before measuring. Rule, labels and numbers:
`data/vep-structured-footage-query/eval/` in firstmate's home. A
predicate the engine cannot measure is REFUSED by name (`UNSHIPPED`),
never answered with an empty list that reads as "he never did".

**Names.** A person is named by the project's declaration
(`source.person_names`, `person_entity.declared_person_names`) when it
makes one; otherwise by the speaker label the saved timeline transcript
gives the speech that person's attributed voice turns overlap
(`derived_person_names`): the timeline's own per-speaker tracks, with the
measured agreement recorded beside the name. Never by appearance.

**Placement on the timeline.** A span on source S, and the same moment on
every other angle of S's multicam group (`conversation_clock.map_time`),
is looked up against the timeline transcript's items: each item has a
constant source-to-timeline offset and a RECORDED source extent (its
first to last word). A span outside every recorded extent is reported
`unplaced`, not "not on the timeline": the transcript records where
speech is, and an item can run past it.

    python3 -m library.tools.event_spans build <project>
    python3 -m library.tools.event_spans status <project>
    ren search <project> --person Craig --predicate speaking
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from library.tools import (
    conversation_clock,
    person_entity,
    person_measurements,
    source_memory,
)
from library.tools.ren_refusal import RenRefusal

PREDICATES = ("speaking", "on_screen")

UNSHIPPED = {
    "hand_near_mouth": (
        "measured and NOT shipped: on every 2 Hz frame of the four Craig "
        "angles (10,873 frames, 17 labelled hand-at-mouth events) the "
        "pre-registered span rule recalled 11/17 events at precision 0.50, "
        "under the 0.80 recall bar fixed before measuring; a single finger "
        "on the lips can carry no detected hand at all. "
        "data/vep-structured-footage-query/eval/results.md (firstmate's "
        "home) has the rule, the labels and every number"),
}
"""Predicates asked for by name that the engine refuses, and why. A
silent empty answer would read as "this never happens in the footage"."""

SPAN_GAP_FRAMES = 1
"""A run of hit frames survives this many missing frames inside it -
one dropped detection at the M2 cadence (~0.5 s) is not a new event."""

FACE_TRACK_MAX_CENTER_SHIFT = 0.75
"""A Vision face belongs to an ArcFace track when its centre lies within
this many face widths of a time-adjacent ArcFace observation's centre.
Centres, not IoU: the two detectors draw different boxes around the same
face, so an IoU floor would measure the detectors, not the person."""

VOICE_FACE_MIN_MOTION_RATIO = 1.5
"""A voice is a face's when that face's mean frame-to-frame lip motion
inside the voice's turns is at least this multiple of its motion outside
them. Checked against the timeline transcript's per-speaker ISO tracks
on geo-podcast (results.md in the eval directory named above)."""

VOICE_FACE_MIN_FRAMES = 20
"""Frames inside a voice's turns before its ratio is trusted (~10 s of
the face at the M2 cadence)."""

NAME_MIN_AGREEMENT = 0.6
"""A derived name needs this share of the person's attributed speech
seconds that overlap ANY timeline speaker label to overlap ONE label."""

STATUS_BUILT = "built"


# ── spans out of per-frame hits ─────────────────────────────────────


def frame_spacing(times: Sequence[float]) -> float:
    """The median spacing of a frame sequence (the M2 cadence)."""
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    return statistics.median(gaps) if gaps else 0.5


def runs(hits: Sequence[bool], gap: int = SPAN_GAP_FRAMES,
         min_hits: int = 1) -> List[Tuple[int, int, int]]:
    """`[(first_index, last_index, hit_count)]` of runs of True frames,
    bridging at most `gap` False frames, keeping runs with at least
    `min_hits` True frames."""
    out: List[Tuple[int, int, int]] = []
    start = last = None
    count = 0
    for i, hit in enumerate(hits):
        if not hit:
            continue
        if start is not None and i - last - 1 <= gap:
            last, count = i, count + 1
            continue
        if start is not None and count >= min_hits:
            out.append((start, last, count))
        start, last, count = i, i, 1
    if start is not None and count >= min_hits:
        out.append((start, last, count))
    return out


def span_bounds(times: Sequence[float], first: int, last: int) -> Tuple[float, float]:
    """A run's time extent: half a frame either side of its end frames -
    a sample stands for the interval around it, no further."""
    half = frame_spacing(times) / 2.0
    return round(max(0.0, times[first] - half), 3), round(times[last] + half, 3)


# ── who a Vision face is (M3 x M3b) ─────────────────────────────────


def _m3b_frame_dims(identity: dict, m3: dict) -> Tuple[float, float]:
    """Pixel size of the frames M3b drew its boxes on.

    M3b reads the shared M2 thumbnails when they are fresh
    (`instrument.frame_source`), the same frames M3 measured, so the two
    share M3's `frame_pixels`. An own-decode M3b record is at source
    resolution, which nothing here records; it cannot be joined and
    raises rather than guessing a scale.
    """
    source = (identity.get("instrument") or {}).get("frame_source")
    pixels = (m3.get("instrument") or {}).get("frame_pixels")
    if source != person_entity.FRAME_SOURCE_M2 or not pixels:
        raise ValueError(
            f"M3b frame source {source!r} is not the shared M2 sample M3 "
            f"read; rebuild M3b after `source_memory frames`")
    return float(pixels[0]), float(pixels[1])


def assign_face_tracks(m3: dict, identity: dict) -> List[List[Optional[str]]]:
    """Per M3 frame, per face: the M3b face track it is, or None.

    The ArcFace observations immediately before and after the frame in
    time (any track) are the candidates; the face takes the track of the
    nearest-in-time candidate whose centre lies within
    `FACE_TRACK_MAX_CENTER_SHIFT` of its own face width. Pure geometry
    over two measurements - no embedding is computed here.
    """
    width, height = _m3b_frame_dims(identity, m3)
    observations = []  # (t, track_id, cx, cy) normalised
    for track in identity.get("faces") or []:
        for span in track.get("spans") or []:
            x1, y1, x2, y2 = span["box"]
            observations.append(((span["start"] + span["end"]) / 2.0,
                                 track["track_id"],
                                 (x1 + x2) / 2.0 / width,
                                 (y1 + y2) / 2.0 / height))
    observations.sort()
    times = [o[0] for o in observations]
    out: List[List[Optional[str]]] = []
    for frame in m3["frames"]:
        idx = bisect.bisect_left(times, frame["t"])
        neighbours = sorted((observations[j] for j in (idx - 1, idx)
                             if 0 <= j < len(observations)),
                            key=lambda o: abs(o[0] - frame["t"]))
        row: List[Optional[str]] = []
        for face in frame["faces"]:
            x1, y1, x2, y2 = face["box"]
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            limit = FACE_TRACK_MAX_CENTER_SHIFT * (x2 - x1) * width
            row.append(next((track_id for _t, track_id, ox, oy in neighbours
                             if math.hypot((cx - ox) * width, (cy - oy) * height)
                             <= limit), None))
        out.append(row)
    return out


def on_screen_spans(m3: dict, assignment: List[List[Optional[str]]]) -> List[dict]:
    """Per face track, the runs of frames that track's face is in."""
    times = [f["t"] for f in m3["frames"]]
    spans = []
    for track_id in sorted({t for row in assignment for t in row if t}):
        hits = [track_id in row for row in assignment]
        for first, last, count in runs(hits):
            start, end = span_bounds(times, first, last)
            spans.append({"face_track": track_id, "start": start, "end": end,
                          "frames": count})
    return spans


# ── who is speaking (M3b voices x M3 lips) ──────────────────────────


def lip_opening(face: dict) -> Optional[float]:
    """Inner-lip vertical extent over face-box height, or None."""
    lips = face.get("inner_lips")
    height = face["box"][3] - face["box"][1]
    if not lips or height <= 0:
        return None
    ys = [p[1] for p in lips]
    return (max(ys) - min(ys)) / height


def _inside(t: float, turns: Sequence[Sequence[float]]) -> bool:
    return any(start <= t <= end for start, end in turns)


def lip_motion(m3: dict, assignment: List[List[Optional[str]]]
               ) -> Dict[str, List[Tuple[float, float]]]:
    """`{face_track: [(t, |opening(t) - opening(previous frame)|)]}`,
    only across CONSECUTIVE frames of the same track."""
    times = [f["t"] for f in m3["frames"]]
    motion: Dict[str, List[Tuple[float, float]]] = {}
    previous: Dict[str, Tuple[int, float]] = {}
    for i, (frame, row) in enumerate(zip(m3["frames"], assignment)):
        for face, track_id in zip(frame["faces"], row):
            opening = lip_opening(face) if track_id else None
            if opening is None:
                continue
            before = previous.get(track_id)
            if before is not None and before[0] == i - 1:
                motion.setdefault(track_id, []).append(
                    (times[i], abs(opening - before[1])))
            previous[track_id] = (i, opening)
    return motion


def link_voices_to_faces(m3: dict, assignment: List[List[Optional[str]]],
                         identity: dict) -> List[dict]:
    """Each voice track's lip-motion evidence against each face track, and
    the face it is linked to (None: no face on this angle speaks with it).

    The contrast is inside-this-voice's-turns against outside them: the
    listener's mouth is mostly still while the other person talks, the
    speaker's moves. Every ratio is recorded, linked or not.
    """
    motion = lip_motion(m3, assignment)
    links = []
    for voice in identity.get("voices") or []:
        turns = voice.get("spans") or []
        evidence = []
        for face_track, samples in sorted(motion.items()):
            inside = [m for t, m in samples if _inside(t, turns)]
            outside = [m for t, m in samples if not _inside(t, turns)]
            if len(inside) < VOICE_FACE_MIN_FRAMES or not outside:
                continue
            mean_out = statistics.fmean(outside)
            evidence.append({
                "face_track": face_track, "frames_inside": len(inside),
                "frames_outside": len(outside),
                "motion_ratio": (round(statistics.fmean(inside) / mean_out, 3)
                                 if mean_out > 0 else None)})
        scored = [e for e in evidence if e["motion_ratio"] is not None]
        best = max(scored, key=lambda e: e["motion_ratio"], default=None)
        linked = (best["face_track"] if best is not None
                  and best["motion_ratio"] >= VOICE_FACE_MIN_MOTION_RATIO else None)
        links.append({"voice_track": voice["track_id"], "face_track": linked,
                      "evidence": evidence})
    return links


def speaking_spans(identity: dict, links: List[dict]) -> List[dict]:
    """Every voice turn, carrying the face track it was linked to."""
    face_of = {link["voice_track"]: link["face_track"] for link in links}
    return [{"voice_track": voice["track_id"],
             "face_track": face_of.get(voice["track_id"]),
             "start": round(start, 3), "end": round(end, 3)}
            for voice in identity.get("voices") or []
            for start, end in voice.get("spans") or []]


# ── building M7 ─────────────────────────────────────────────────────


def build_source_events(content_digest: str, source_file: str,
                        root: Optional[Path] = None) -> dict:
    """M7 for one source from its M3 + M3b. Light: no decode, no model."""
    m3 = person_measurements.read_m3(content_digest, root)
    identity = person_entity.read_identity(content_digest, root)
    if m3 is None:
        raise RuntimeError("no M3 (persons.json); run "
                           "`python3 -m library.tools.person_measurements build`")
    if identity is None:
        raise RuntimeError("no M3b (identity.json); run "
                           "`python3 -m library.tools.person_entity build`")
    assignment = assign_face_tracks(m3, identity)
    links = link_voices_to_faces(m3, assignment, identity)
    faces_total = sum(len(row) for row in assignment)
    faces_assigned = sum(1 for row in assignment for t in row if t)
    record = {
        "content_digest": content_digest,
        "size_bytes": m3.get("size_bytes"),
        "source_file": source_file,
        "status": STATUS_BUILT,
        "frame_count": len(m3["frames"]),
        "faces_assigned": faces_assigned,
        "faces_unassigned": faces_total - faces_assigned,
        "predicates": {
            "on_screen": {
                "basis": "M3 Vision faces at the M2 cadence, assigned to M3b "
                         "ArcFace tracks by position",
                "spans": on_screen_spans(m3, assignment)},
            "speaking": {
                "basis": "M3b diarized voice turns, attributed to a face by "
                         "lip motion (M3)",
                "voice_face_links": links,
                "voice_unavailable_reason": (identity.get("instrument") or {}
                                             ).get("voice_unavailable_reason"),
                "spans": speaking_spans(identity, links)},
        },
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root) / source_memory.SLOT_EVENTS,
        record)
    return {"content_digest": content_digest, "source_file": source_file,
            "faces_assigned": faces_assigned,
            "faces_unassigned": faces_total - faces_assigned,
            **{name: len(body["spans"])
               for name, body in record["predicates"].items()},
            "voice_face_links": {link["voice_track"]: link["face_track"]
                                 for link in links}}


def read_m7(content_digest: str, root: Optional[Path] = None) -> Optional[dict]:
    """A digest's M7 record, or None when never built."""
    path = source_memory.source_dir(content_digest, root) / source_memory.SLOT_EVENTS
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


def catalog_sources(project_folder: str) -> List[Tuple[str, str, str]]:
    """`[(clip_id, digest, source_file)]`, one per distinct digest."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    seen, out = set(), []
    for clip in source_memory.load_catalog(project_folder):
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None or digest in seen:
            continue
        seen.add(digest)
        out.append((clip.get("clip_id"), digest,
                    clip.get("source_file") or clip.get("path") or ""))
    return out


def build_project_events(project_folder: str,
                         root: Optional[Path] = None) -> dict:
    """M7 for every catalog source that carries M3 and M3b."""
    results, failed = [], []
    for clip_id, digest, path in catalog_sources(project_folder):
        try:
            account = build_source_events(digest, path, root)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "content_digest": digest,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": str(Path(project_folder).resolve()),
            "clips": results, "failed": failed}


# ── the timeline side: speaker labels and placement ─────────────────


def timeline_items(project_folder: str) -> List[dict]:
    """The saved timeline transcript's items: per `resolve_item_id`, its
    source file, speaker track, constant source->timeline offset and the
    source extent its words RECORD. `[]` with no saved transcript."""
    from library.tools import timeline_transcript

    try:
        with open(timeline_transcript.transcript_path(project_folder),
                  encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return []
    items: Dict[str, dict] = {}
    for seg in doc.get("segments") or []:
        item_id = seg.get("resolve_item_id")
        if not item_id or seg.get("source_start") is None:
            continue
        item = items.setdefault(item_id, {
            "resolve_item_id": item_id, "speaker": seg.get("speaker"),
            "source_file": seg["source_file"],
            "offset": seg["timeline_start"] - seg["source_start"],
            "source_start": seg["source_start"], "source_end": seg["source_end"],
            "segments": []})
        item["source_start"] = min(item["source_start"], seg["source_start"])
        item["source_end"] = max(item["source_end"], seg["source_end"])
        item["segments"].append((seg["source_start"], seg["source_end"]))
    return list(items.values())


def _digest_of_files(project_folder: str) -> Dict[str, str]:
    return {path: digest for _c, digest, path in catalog_sources(project_folder)}


def spans_on_every_angle(digest: str, start: float, end: float,
                         root: Optional[Path] = None) -> Dict[str, Tuple[float, float]]:
    """The span on its own source plus the same moment on every other
    member of its multicam group (M6). A source in no group maps to
    itself only - never to a zero offset on another file."""
    out = {digest: (start, end)}
    starts = conversation_clock.map_time(digest, start, root)
    ends = conversation_clock.map_time(digest, end, root)
    for other, other_start in starts.items():
        if other in ends:
            out[other] = (other_start, ends[other])
    return out


def place(project_folder: str, digest: str, start: float, end: float,
          items: List[dict], files: Dict[str, str],
          root: Optional[Path] = None) -> List[dict]:
    """Timeline placements of one source span, across every angle."""
    out = []
    for angle, (a, b) in spans_on_every_angle(digest, start, end, root).items():
        for item in items:
            if files.get(item["source_file"]) != angle:
                continue
            lo, hi = max(a, item["source_start"]), min(b, item["source_end"])
            if hi <= lo:
                continue
            out.append({"resolve_item_id": item["resolve_item_id"],
                        "track_speaker": item["speaker"],
                        "via": "same source" if angle == digest else "M6 clock",
                        "source_file": item["source_file"],
                        "source_start": round(lo, 3), "source_end": round(hi, 3),
                        "timeline_start": round(lo + item["offset"], 3),
                        "timeline_end": round(hi + item["offset"], 3)})
    return out


def _overlap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def derived_person_names(project_folder: str, roster: List[dict],
                         root: Optional[Path] = None) -> Dict[str, dict]:
    """`{person_id: {name, agreement, seconds}}` from the timeline's own
    speaker labels: the person's attributed speech (their linked voices'
    turns on their own angles, carried to every angle by M6) overlapped
    with the transcript's labelled speech on the same source. A person
    whose speech does not reach `NAME_MIN_AGREEMENT` on one label gets no
    derived name."""
    items = timeline_items(project_folder)
    files = _digest_of_files(project_folder)
    labelled: Dict[str, List[Tuple[float, float, str]]] = {}
    for item in items:
        digest = files.get(item["source_file"])
        if digest and item["speaker"]:
            for a, b in item["segments"]:
                labelled.setdefault(digest, []).append((a, b, item["speaker"]))
    out = {}
    for person in roster:
        by_label: Dict[str, float] = {}
        for span in person_speaking(person, root):
            for angle, (a, b) in spans_on_every_angle(
                    span["content_digest"], span["start"], span["end"], root).items():
                for la, lb, label in labelled.get(angle, ()):
                    by_label[label] = by_label.get(label, 0.0) + _overlap((a, b), (la, lb))
        total = sum(by_label.values())
        if total <= 0:
            continue
        label, seconds = max(by_label.items(), key=lambda kv: kv[1])
        if seconds / total >= NAME_MIN_AGREEMENT:
            out[person["person_id"]] = {
                "name": label, "agreement": round(seconds / total, 3),
                "seconds": round(total, 1),
                "basis": "attributed speech vs the timeline transcript's "
                         "per-speaker tracks"}
    return out


# ── the query ───────────────────────────────────────────────────────


def _tracks_of(person: dict) -> Dict[str, set]:
    out: Dict[str, set] = {}
    for track in person.get("face_tracks") or []:
        out.setdefault(track["content_digest"], set()).add(track["track_id"])
    return out


def _predicate_spans(person: dict, predicate: str,
                     root: Optional[Path] = None) -> List[dict]:
    spans = []
    for digest, tracks in _tracks_of(person).items():
        record = read_m7(digest, root)
        if record is None:
            continue
        for span in record["predicates"][predicate]["spans"]:
            if span.get("face_track") in tracks:
                spans.append({"content_digest": digest,
                              "source_file": record["source_file"], **span})
    return sorted(spans, key=lambda s: (s["source_file"], s["start"]))


def person_speaking(person: dict, root: Optional[Path] = None) -> List[dict]:
    return _predicate_spans(person, "speaking", root)


def resolve_person(project_folder: str, query: str,
                   root: Optional[Path] = None) -> Tuple[dict, dict]:
    """`(person, naming)` by person_id, declared name or derived name,
    case-insensitive. Refuses with the roster when nothing resolves."""
    roster = person_entity.resolve_person_tracks(project_folder, root)["persons"]
    derived = derived_person_names(project_folder, roster, root)
    wanted = query.strip().lower()
    for person in roster:
        pid = person["person_id"]
        if pid.lower() == wanted:
            return person, {"by": "person_id"}
        if person.get("name") and person["name"].strip().lower() == wanted:
            return person, {"by": "declared name (source.person_names)"}
        if pid in derived and derived[pid]["name"].strip().lower() == wanted:
            return person, {"by": "derived name", **derived[pid]}
    known = ", ".join(
        f"{p['person_id']}"
        + (f" ({p['name']}, declared)" if p.get("name") else "")
        + (f" ({derived[p['person_id']]['name']}, derived)"
           if p["person_id"] in derived else "")
        for p in roster) or "none"
    raise RenRefusal(
        f"no person matching {query!r}",
        f"the project's measured roster is: {known}",
        "build M3b and M7 first (`person_entity build`, `event_spans build`), "
        "or declare `source.person_names` in project.yaml")


def query(project_folder: str, person_query: str, predicate: str,
          root: Optional[Path] = None) -> dict:
    """person x predicate -> source spans -> every angle -> timeline items."""
    if predicate in UNSHIPPED:
        raise RenRefusal(f"predicate {predicate!r} is not answered",
                         UNSHIPPED[predicate],
                         f"answerable predicates: {', '.join(PREDICATES)}")
    if predicate not in PREDICATES:
        raise RenRefusal(f"unknown predicate {predicate!r}",
                         f"M7 measures: {', '.join(PREDICATES)}",
                         f"use --predicate {' / --predicate '.join(PREDICATES)}")
    person, naming = resolve_person(project_folder, person_query, root)
    missing = [digest for digest in _tracks_of(person)
               if read_m7(digest, root) is None]
    if missing:
        raise RenRefusal(
            f"M7 is missing for {len(missing)} of this person's sources",
            "an unbuilt source would read as 'never happens there'",
            f"python3 -m library.tools.event_spans build '{project_folder}'")
    items = timeline_items(project_folder)
    files = _digest_of_files(project_folder)
    hits = []
    for span in _predicate_spans(person, predicate, root):
        hits.append({**{k: span[k] for k in ("source_file", "start", "end")},
                     "placements": place(project_folder, span["content_digest"],
                                         span["start"], span["end"], items,
                                         files, root)})
    placed = [h for h in hits if h["placements"]]
    return {
        "person": person["person_id"], "naming": naming,
        "predicate": predicate,
        "signals": {
            "person": "M3b ArcFace face tracks, unified across sources",
            "predicate": {"speaking": "M3b ECAPA voice turns, linked to the "
                                      "face by M3 lip motion",
                          "on_screen": "M3 Apple Vision faces at the M2 "
                                       "cadence, assigned to M3b tracks"}[predicate],
            "join": "M6 conversation clock",
            "placement": "timeline_transcript items (resolve_item_id, "
                         "constant offset, recorded source extent)"},
        "timeline_transcript": bool(items),
        "spans": len(hits), "spans_placed": len(placed),
        "source_seconds": round(sum(h["end"] - h["start"] for h in hits), 1),
        "hits": hits,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="event_spans",
        description="M7: derived per-person event spans (speaking, "
                    "on_screen) from M3 + M3b. Light: no decode, no model.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M7 for a project's catalog")
    p_build.add_argument("project")
    p_names = sub.add_parser("names", help="derived person names and evidence")
    p_names.add_argument("project")
    p_query = sub.add_parser("query", help="person x predicate -> timeline")
    p_query.add_argument("project")
    p_query.add_argument("--person", required=True)
    p_query.add_argument("--predicate", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        report = build_project_events(args.project)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    if args.command == "names":
        roster = person_entity.resolve_person_tracks(args.project)["persons"]
        print(json.dumps(derived_person_names(args.project, roster), indent=2))
        return 0
    if args.command == "query":
        print(json.dumps(query(args.project, args.person, args.predicate), indent=2))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)

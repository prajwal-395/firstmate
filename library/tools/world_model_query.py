"""Targeted reasoning over the world model: narrow questions, native
timing, provenance.

The world model (M9, `world_model.json`) is a derived per-second view of
the canonical source-memory records. A per-second view is a good
compatibility surface and a poor question surface: a planner that wants
to know who speaks between two seconds has to read the whole per-second
timeline, and every boundary it reads there is quantised to the second.

This module is the world's QUERY surface. It answers a question off the
canonical records the view is derived from - M1's utterances, M3/M3b's
faces and tracks, M4's scenes, M5's sound events, M7's event spans, M8's
verdicts, M10's relationship candidates - so that:

* an INTERVAL query returns the facts inside a span, at the timing the
  canonical record carries, not at the bin's edge;
* an ENTITY query returns one entity's occurrences, wherever they are;
* every fact says which lane, slot and field it came from, and whether
  that lane MEASURED it or only CANDIDATED it;
* a lane that was never built is ABSENT and is named, never defaulted.

The per-second view stays exactly as it is: `world_model.py` writes it,
`project_graph` reads it, and nothing here rewrites it. This module
does not read it either - it reads the records behind it, so a question
costs the question and not the episode.

    python3 -m library.tools.world_model_query interval <project> \
        --clip clip_003 --start 40 --end 55
    python3 -m library.tools.world_model_query entity <project> \
        --clip clip_003 --entity face_001
    python3 -m library.tools.world_model_query ask <project> \
        --clip clip_003 --start 40 --end 55
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:
    from library.tools import (
        conversation_clock,
        event_spans,
        person_entity,
        person_measurements,
        source_memory,
        span_verification,
        token_measure,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools import (
        conversation_clock,
        event_spans,
        person_entity,
        person_measurements,
        source_memory,
        span_verification,
        token_measure,
    )


# ── The aspects a question can be about ──────────────────────────────

ASPECTS = ("speech", "people", "scene", "sound", "events", "relationships")
"""The facts the world can answer about, one enumeration.

A question names the aspects it wants; a question that names none gets
every aspect. An aspect whose lane was never built contributes NOTHING
and is reported as absent - never an empty list that reads as "nothing
happened there".
"""

QUESTION_ASPECTS = {
    "who": ("people", "speech"),
    "what": ("sound", "events", "relationships", "speech", "scene"),
    "where": ("scene",),
    "when": ("events", "speech"),
}
"""The aspect set each plain question word asks for."""

MEASURED = "measured"
CANDIDATE = "candidate"
"""The standing distinction, restated: M7's `hand_near_mouth` and M10's
holding/pointing/looking_at are geometric CANDIDATES for a verifier, and
a candidate is never an answer."""

LANE_SLOT = {
    "m0": "source.json",
    "m1": "transcript.words.json",
    "m3": "persons.json",
    "m3b": "identity.json",
    "m4": "scenes.json",
    "m5": "sound.json",
    "m6": "clock.json",
    "m7": "events.json",
    "m8": "verdicts.json",
    "m10": "relationships.json",
    "vi-09": "scene_cinematography.json",
}
"""The slot each lane writes, so a fact can name the file behind it."""

LANE_READER = {
    "m0": "source_memory.read_m0",
    "m1": "source_memory.read_m1",
    "m3": "person_measurements.read_m3",
    "m3b": "person_entity.read_identity",
    "m4": "source_memory.read_scenes",
    "m5": "source_memory.read_sound",
    "m6": "conversation_clock.read_clock",
    "m7": "event_spans.read_m7",
    "m8": "span_verification.read_verdicts",
    "m10": "source_memory.read_relationships",
    "vi-09": "source_memory.source_dir/slot_scene_cinematography",
}
"""The reader each lane is read through, so provenance names the CODE
that produced the fact, not only the file it landed in."""


def _overlap(start: float, end: float, lo: float, hi: float) -> bool:
    """Whether [start, end) overlaps [lo, hi)."""
    return start < hi and end > lo


def _number_occurrences(rows: List[dict]) -> None:
    """Number each row's occurrence of ITS KIND, in time order.

    "The second time" is a question the answer has to be able to name,
    and a list of spans does not name it: the order is there, the
    ordinal is not. The number is per kind, because a face track's
    second SPEAKING turn is not its second hand-near-mouth candidate.
    """
    counters: Dict[str, int] = {}
    for row in rows:
        counters[row["kind"]] = counters.get(row["kind"], 0) + 1
        row["occurrence"] = counters[row["kind"]]


def _fact(aspect: str, kind: str, start: float, end: float,
          value: dict, lane: str, field: str,
          entity: Optional[str] = None,
          status: str = MEASURED) -> dict:
    """One fact, with the provenance that says where it came from."""
    return {
        "aspect": aspect,
        "kind": kind,
        "entity": entity,
        "start": round(float(start), 6),
        "end": round(float(end), 6),
        "value": value,
        "provenance": {
            "lane": lane,
            "slot": LANE_SLOT.get(lane),
            "reader": LANE_READER.get(lane),
            "field": field,
            "status": status,
        },
    }


class World:
    """One source's world, queried narrowly.

    The canonical records are read once, lazily, per lane - a question
    about speech never pays for M3's faces, and a question about a face
    never pays for the transcript.
    """

    def __init__(self, content_digest: str, root: Optional[Path] = None):
        self.content_digest = content_digest
        self.root = Path(root) if root is not None else None
        self._lanes: Dict[str, Optional[dict]] = {}
        self._names: Dict[str, str] = {}

    # ── The lanes ───────────────────────────────────────────────────

    def lane(self, name: str) -> Optional[dict]:
        """One canonical lane's record, or None when it was never built."""
        if name in self._lanes:
            return self._lanes[name]
        record = self._read_lane(name)
        self._lanes[name] = record
        return record

    def _read_lane(self, name: str) -> Optional[dict]:
        digest, root = self.content_digest, self.root
        if name == "m0":
            return source_memory.read_m0(digest, root)
        if name == "m1":
            document, _status = source_memory.read_m1(digest, root)
            return document
        if name == "m3":
            return person_measurements.read_m3(digest, root)
        if name == "m3b":
            return person_entity.read_identity(digest, root)
        if name == "m4":
            return source_memory.read_scenes(digest, root)
        if name == "m5":
            return source_memory.read_sound(digest, root)
        if name == "m6":
            return conversation_clock.read_clock(digest, root)
        if name == "m7":
            return event_spans.read_m7(digest, root)
        if name == "m8":
            return span_verification.read_verdicts(digest, root)
        if name == "m10":
            return source_memory.read_relationships(digest, root)
        if name == "vi-09":
            path = (source_memory.source_dir(digest, root)
                    / source_memory.SLOT_SCENE_CINEMATOGRAPHY)
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return document if isinstance(document, dict) else None
        raise ValueError(f"unknown lane {name!r}; have "
                         f"{', '.join(sorted(LANE_SLOT))}")

    def lanes(self) -> dict:
        """Which lanes this source carries, and which it does not.

        A lane that was never built is named as absent. It is never
        defaulted: an absent M7 does not mean "nobody spoke".
        """
        present, absent = [], []
        for name in sorted(LANE_SLOT):
            record = self.lane(name)
            row = {"lane": name, "slot": LANE_SLOT[name]}
            if record is None:
                row["status"] = "absent"
                absent.append(row)
            else:
                row["status"] = str(record.get("status") or "measured")
                present.append(row)
        return {"present": present, "absent": absent}

    def with_names(self, names: Dict[str, str]) -> "World":
        """Name the entities this world knows by, for the answer's readers."""
        self._names = dict(names or {})
        return self

    def _name_of(self, entity: Optional[str]) -> Optional[str]:
        if entity is None:
            return None
        return self._names.get(entity)

    # ── The facts ───────────────────────────────────────────────────

    def facts(self, start: float, end: float,
              aspects: Optional[Sequence[str]] = None) -> List[dict]:
        """Every fact inside [start, end), at its own timing.

        Each fact carries the span it occupies in the SOURCE's clock, and
        the provenance of the lane that measured (or only candidate'd) it.
        """
        lo, hi = float(start), float(end)
        if hi < lo:
            lo, hi = hi, lo
        wanted = tuple(aspects) if aspects else ASPECTS
        unknown = [name for name in wanted if name not in ASPECTS]
        if unknown:
            raise ValueError(f"unknown aspect(s) {', '.join(unknown)}; "
                             f"have {', '.join(ASPECTS)}")
        rows: List[dict] = []
        if "speech" in wanted:
            rows.extend(self._speech_facts(lo, hi))
        if "people" in wanted:
            rows.extend(self._people_facts(lo, hi))
        if "scene" in wanted:
            rows.extend(self._scene_facts(lo, hi))
        if "sound" in wanted:
            rows.extend(self._sound_facts(lo, hi))
        if "events" in wanted:
            rows.extend(self._event_facts(lo, hi))
        if "relationships" in wanted:
            rows.extend(self._relationship_facts(lo, hi))
        rows.sort(key=lambda row: (row["start"], row["end"],
                                   row["aspect"], row["kind"]))
        return rows

    def _speech_facts(self, lo: float, hi: float) -> List[dict]:
        rows: List[dict] = []
        m1 = self.lane("m1")
        if m1:
            for utterance in m1.get("utterances") or []:
                start = utterance.get("start")
                end = utterance.get("end")
                if start is None or end is None:
                    continue
                if not _overlap(float(start), float(end), lo, hi):
                    continue
                rows.append(_fact(
                    "speech", "utterance", float(start), float(end),
                    {"text": utterance.get("text"),
                     "words": len(utterance.get("words") or [])},
                    "m1", "utterances"))
        m7 = self.lane("m7")
        if m7:
            predicates = m7.get("predicates") or {}
            for span in (predicates.get("speaking") or {}).get("spans") or []:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if not _overlap(float(start), float(end), lo, hi):
                    continue
                face_track = span.get("face_track")
                rows.append(_fact(
                    "speech", "speaking", float(start), float(end),
                    {"voice_track": span.get("voice_track"),
                     "face_track": face_track,
                     "person": self._name_of(face_track)},
                    "m7", "predicates.speaking.spans",
                    entity=face_track))
        return rows

    def _people_facts(self, lo: float, hi: float) -> List[dict]:
        rows: List[dict] = []
        m7 = self.lane("m7")
        if m7:
            predicates = m7.get("predicates") or {}
            for span in (predicates.get("on_screen") or {}).get("spans") or []:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if not _overlap(float(start), float(end), lo, hi):
                    continue
                face_track = span.get("face_track")
                rows.append(_fact(
                    "people", "on_screen", float(start), float(end),
                    {"face_track": face_track,
                     "person": self._name_of(face_track)},
                    "m7", "predicates.on_screen.spans", entity=face_track))
        m3b = self.lane("m3b")
        if m3b:
            for face in m3b.get("faces") or []:
                track_id = face.get("track_id")
                for span in face.get("spans") or []:
                    start, end = span.get("start"), span.get("end")
                    if start is None or end is None:
                        continue
                    if not _overlap(float(start), float(end), lo, hi):
                        continue
                    rows.append(_fact(
                        "people", "face_track", float(start), float(end),
                        {"track_id": track_id,
                         "person": self._name_of(track_id)},
                        "m3b", "faces.spans", entity=track_id))
            for voice in m3b.get("voices") or []:
                track_id = voice.get("track_id")
                for span in voice.get("spans") or []:
                    start, end = span[0], span[1]
                    if not _overlap(float(start), float(end), lo, hi):
                        continue
                    rows.append(_fact(
                        "people", "voice_track", float(start), float(end),
                        {"track_id": track_id},
                        "m3b", "voices.spans", entity=track_id))
        return rows

    def _scene_facts(self, lo: float, hi: float) -> List[dict]:
        rows: List[dict] = []
        m4 = self.lane("m4")
        if m4:
            for segment in m4.get("scenes") or []:
                start, end = segment.get("start"), segment.get("end")
                if start is None or end is None:
                    continue
                if not _overlap(float(start), float(end), lo, hi):
                    continue
                rows.append(_fact(
                    "scene", "scene", float(start), float(end),
                    {"location": segment.get("location"),
                     "type": segment.get("type"),
                     "lighting": segment.get("lighting")},
                    "m4", "scenes"))
        hierarchy = self.lane("vi-09")
        if isinstance(hierarchy, dict):
            for key, kind in (("shots", "shot"), ("subshots", "subshot"),
                              ("sequences", "sequence"),
                              ("scenes", "hierarchy_scene")):
                for item in (hierarchy.get(key) or []):
                    start, end = item.get("start"), item.get("end")
                    if start is None or end is None:
                        continue
                    if not _overlap(float(start), float(end), lo, hi):
                        continue
                    rows.append(_fact(
                        "scene", kind, float(start), float(end),
                        {"id": item.get("id")},
                        "vi-09", f"scene_cinematography.{key}"))
        return rows

    def _sound_facts(self, lo: float, hi: float) -> List[dict]:
        rows: List[dict] = []
        m5 = self.lane("m5")
        if not m5:
            return rows
        for event in m5.get("sound_events") or []:
            start, end = event.get("start"), event.get("end")
            if start is None or end is None:
                continue
            if not _overlap(float(start), float(end), lo, hi):
                continue
            rows.append(_fact(
                "sound", "sound_event", float(start), float(end),
                {"label": event.get("label"),
                 "confidence": event.get("confidence")},
                "m5", "sound_events"))
        return rows

    def _event_facts(self, lo: float, hi: float) -> List[dict]:
        rows: List[dict] = []
        m7 = self.lane("m7")
        if not m7:
            return rows
        m8 = self.lane("m8") or {}
        candidates = m7.get("candidates") or {}
        for name, block in candidates.items():
            if not isinstance(block, dict):
                continue
            for span in block.get("spans") or []:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if not _overlap(float(start), float(end), lo, hi):
                    continue
                verdicts = self._verdicts_for_span(span, m8)
                status = CANDIDATE
                if verdicts:
                    status = ("verified"
                              if any((v.get("answer") or "").lower() == "yes"
                                     for v in verdicts)
                              else "rejected")
                face_track = span.get("face_track")
                rows.append(_fact(
                    "events", name, float(start), float(end),
                    {"face_track": face_track,
                     "person": self._name_of(face_track),
                     "verdicts": verdicts},
                    "m7", f"candidates.{name}.spans", entity=face_track,
                    status=status))
        return rows

    def _verdicts_for_span(self, span: dict, m8: dict) -> List[dict]:
        """M8's verdicts on one candidate span, matched on the frames the
        verifier saw - the same rule `world_model` joins on."""
        if not m8:
            return []
        try:
            selected = span_verification.select_frames(span)
        except (KeyError, TypeError, ValueError):
            return []
        selected_times = [round(frame["t"], 3) for frame in selected]
        verdicts = []
        for verdict in m8.values():
            if not isinstance(verdict, dict):
                continue
            if [round(float(t), 3) for t in verdict.get("frames") or []] \
                    != selected_times:
                continue
            verdicts.append({
                "statement": verdict.get("statement"),
                "answer": verdict.get("answer"),
                "reason": verdict.get("reason"),
                "model": verdict.get("model"),
            })
        return verdicts

    def _relationship_facts(self, lo: float, hi: float) -> List[dict]:
        rows: List[dict] = []
        m10 = self.lane("m10")
        if not m10:
            return rows
        for name, block in (m10.get("relationships") or {}).items():
            if not isinstance(block, dict):
                continue
            for span in block.get("spans") or []:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if not _overlap(float(start), float(end), lo, hi):
                    continue
                owners = span.get("owners") or []
                rows.append(_fact(
                    "relationships", name, float(start), float(end),
                    {"owners": owners,
                     "people": [self._name_of(owner) for owner in owners],
                     "frames": len(span.get("frames") or [])},
                    "m10", f"relationships.{name}.spans",
                    entity=owners[0] if owners else None,
                    status=CANDIDATE))
        return rows

    # ── The questions ───────────────────────────────────────────────

    def answer(self, question: dict) -> dict:
        """Answer one narrow question about a span.

        The question names the span and, optionally, the aspect
        (`who`, `what`, `where`, `when`) or the aspects it wants. The
        answer carries the facts, the lanes that answered, and the lanes
        that did not - so an absent lane is visible as absent.
        """
        start = question.get("start")
        end = question.get("end")
        if start is None or end is None:
            raise ValueError("a question needs a start and an end")
        word = question.get("aspect")
        aspects = QUESTION_ASPECTS.get(word) if isinstance(word, str) \
            else None
        if aspects is None and isinstance(word, str) and word in ASPECTS:
            aspects = (word,)
        if aspects is None:
            aspects = question.get("aspects")
        rows = self.facts(float(start), float(end), aspects)
        lanes = sorted({row["provenance"]["lane"] for row in rows})
        coverage = self.lanes()
        absent = [row["lane"] for row in coverage["absent"]]
        return {
            "question": dict(question),
            "facts": rows,
            "lanes_used": lanes,
            "lanes_absent": absent,
        }

    def who_at(self, start: float, end: float) -> dict:
        """Who is on screen and who is speaking in the span."""
        return self.answer({"aspect": "who", "start": start, "end": end})

    def what_at(self, start: float, end: float) -> dict:
        """What happens in the span: sound, events, relationships, speech."""
        return self.answer({"aspect": "what", "start": start, "end": end})

    def where_at(self, start: float, end: float) -> dict:
        """Where the picture is in the span: the scene and the shot."""
        return self.answer({"aspect": "where", "start": start, "end": end})

    # ── The entities ────────────────────────────────────────────────

    def entity(self, entity_id: str) -> dict:
        """One entity's occurrences across this source, at native timing.

        The entity is a face track (`face_001`), a voice track
        (`voice_001`) or a person id (`person_001`) the project's roster
        resolved. Every occurrence names the lane it came from.
        """
        rows: List[dict] = []
        m3b = self.lane("m3b")
        if m3b:
            for face in m3b.get("faces") or []:
                if face.get("track_id") != entity_id:
                    continue
                for span in face.get("spans") or []:
                    rows.append(_fact(
                        "people", "face_track", float(span["start"]),
                        float(span["end"]),
                        {"track_id": entity_id,
                         "person": self._name_of(entity_id)},
                        "m3b", "faces.spans", entity=entity_id))
            for voice in m3b.get("voices") or []:
                if voice.get("track_id") != entity_id:
                    continue
                for span in voice.get("spans") or []:
                    rows.append(_fact(
                        "people", "voice_track", float(span[0]),
                        float(span[1]), {"track_id": entity_id},
                        "m3b", "voices.spans", entity=entity_id))
        m7 = self.lane("m7")
        if m7:
            predicates = m7.get("predicates") or {}
            for name, key in (("on_screen", "on_screen"),
                              ("speaking", "speaking")):
                for span in (predicates.get(key) or {}).get("spans") or []:
                    face_track = span.get("face_track")
                    voice_track = span.get("voice_track")
                    if face_track != entity_id and voice_track != entity_id:
                        continue
                    rows.append(_fact(
                        "speech" if name == "speaking" else "people",
                        name, float(span["start"]), float(span["end"]),
                        {"face_track": face_track,
                         "voice_track": voice_track,
                         "person": self._name_of(face_track)},
                        "m7", f"predicates.{key}.spans",
                        entity=face_track or voice_track))
        m10 = self.lane("m10")
        if m10:
            for name, block in (m10.get("relationships") or {}).items():
                if not isinstance(block, dict):
                    continue
                for span in block.get("spans") or []:
                    owners = span.get("owners") or []
                    if entity_id not in owners:
                        continue
                    rows.append(_fact(
                        "relationships", name, float(span["start"]),
                        float(span["end"]),
                        {"owners": owners,
                         "people": [self._name_of(owner) for owner in owners]},
                        "m10", f"relationships.{name}.spans",
                        entity=entity_id, status=CANDIDATE))
        if m7:
            for name, block in (m7.get("candidates") or {}).items():
                if not isinstance(block, dict):
                    continue
                for span in block.get("spans") or []:
                    if span.get("face_track") != entity_id:
                        continue
                    rows.append(_fact(
                        "events", name, float(span["start"]),
                        float(span["end"]),
                        {"face_track": entity_id,
                         "person": self._name_of(entity_id),
                         "verdicts": self._verdicts_for_span(
                             span, self.lane("m8") or {})},
                        "m7", f"candidates.{name}.spans",
                        entity=entity_id, status=CANDIDATE))
        rows.sort(key=lambda row: (row["start"], row["end"], row["kind"]))
        _number_occurrences(rows)
        return {
            "entity": entity_id,
            "name": self._name_of(entity_id),
            "occurrences": rows,
            "lanes_used": sorted({row["provenance"]["lane"] for row in rows}),
        }

    def entities(self) -> List[dict]:
        """The entities this source knows: its face and voice tracks."""
        rows: List[dict] = []
        m3b = self.lane("m3b")
        if not m3b:
            return rows
        for face in m3b.get("faces") or []:
            rows.append({"kind": "face_track", "id": face.get("track_id"),
                         "name": self._name_of(face.get("track_id"))})
        for voice in m3b.get("voices") or []:
            rows.append({"kind": "voice_track", "id": voice.get("track_id"),
                         "name": self._name_of(voice.get("track_id"))})
        return rows


# ── The project's roster ─────────────────────────────────────────────


def person_index(project_folder: str, root: Optional[Path] = None) -> dict:
    """The project's persons, and the tracks each one owns, per source.

    A person's name comes from the project's own declaration
    (`source.person_names`); a track no person claims stays unnamed.
    Nothing here guesses a name from a face.
    """
    roster = person_entity.resolve_person_tracks(project_folder, root)
    names = person_entity.declared_person_names(project_folder)
    tracks: Dict[str, Dict[str, str]] = {}
    person_names: Dict[str, str] = {}
    for person in roster.get("persons") or []:
        person_id = person.get("person_id")
        if not person_id:
            continue
        person_names[person_id] = names.get(person_id) or person.get("name")
        for track in person.get("face_tracks") or []:
            digest = track.get("content_digest")
            track_id = track.get("track_id")
            if digest and track_id:
                tracks.setdefault(digest, {})[track_id] = person_id
        for row in person.get("voice_matches") or []:
            digest = row.get("content_digest")
            track_id = row.get("voice_track")
            if digest and track_id:
                tracks.setdefault(digest, {})[track_id] = person_id
        digest_of_clip = {}
        for row in person.get("face_tracks") or []:
            digest_of_clip[row.get("clip_id")] = row.get("content_digest")
        for link in person.get("speech_face_links") or []:
            voice_track = link.get("voice_track")
            digest = digest_of_clip.get(link.get("clip_id"))
            if voice_track and digest:
                tracks.setdefault(digest, {})[voice_track] = person_id
    return {"persons": person_names,
            "track_owner": tracks,
            "uncertain": roster.get("uncertain_persons") or []}


def names_for(world: World, index: dict) -> Dict[str, str]:
    """The names the project's roster gives this source's tracks."""
    owners = (index.get("track_owner") or {}).get(world.content_digest, {})
    names = dict(index.get("persons") or {})
    resolved = {}
    for track_id, person_id in owners.items():
        name = names.get(person_id)
        if name:
            resolved[track_id] = name
    return resolved


# ── The planning context ────────────────────────────────────────────


def planning_context(world: World, questions: Sequence[dict]) -> dict:
    """The narrow context a planning call carries for these questions.

    One entry per question, each with only the facts that question
    needs. The size of this payload is the size of the world's
    contribution to the call - measure it with `token_measure`.
    """
    answers = []
    for question in questions:
        answer = world.answer(question)
        answers.append({
            "question": answer["question"],
            "facts": answer["facts"],
            "lanes_used": answer["lanes_used"],
            "lanes_absent": answer["lanes_absent"],
        })
    coverage = world.lanes()
    return {
        "content_digest": world.content_digest,
        "questions": answers,
        "lanes_present": [row["lane"] for row in coverage["present"]],
        "lanes_absent": [row["lane"] for row in coverage["absent"]],
    }


def measure_context(payload: dict) -> dict:
    """The size of a context payload, with the tokenizer that measured it.

    `utf8_bytes` is always available and is exact; `o200k_base` is a
    stated proxy and is ABSENT when this environment cannot run it. A
    count is never published without the tokenizer that produced it.
    """
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return token_measure.measure(text)


# ── The CLI ──────────────────────────────────────────────────────────


def _world_for_clip(project_folder: str, clip_id: str,
                    root: Optional[Path] = None) -> World:
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    for clip in source_memory.load_catalog(project_folder):
        if clip.get("clip_id") != clip_id:
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            raise SystemExit(f"no content digest for clip {clip_id!r}")
        world = World(digest, root)
        index = person_index(project_folder, root)
        return world.with_names(names_for(world, index))
    raise SystemExit(f"no catalog clip {clip_id!r} in {project_folder}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="world_model_query",
        description="Targeted reasoning over the world model: narrow "
                    "questions answered off the canonical records, at "
                    "native timing, with provenance.")
    sub = parser.add_subparsers(dest="command")

    p_interval = sub.add_parser(
        "interval", help="the facts inside a span, at their own timing")
    p_interval.add_argument("project")
    p_interval.add_argument("--clip", required=True)
    p_interval.add_argument("--start", type=float, required=True)
    p_interval.add_argument("--end", type=float, required=True)
    p_interval.add_argument("--aspect", action="append",
                            choices=list(ASPECTS))
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
    p_ask.add_argument("--question", default="what",
                       choices=sorted(QUESTION_ASPECTS))
    p_ask.add_argument("--memory-root")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None

    if args.command == "interval":
        world = _world_for_clip(args.project, args.clip, root)
        facts = world.facts(args.start, args.end, args.aspect)
        payload = {"content_digest": world.content_digest,
                   "start": args.start, "end": args.end,
                   "facts": facts}
    elif args.command == "entity":
        world = _world_for_clip(args.project, args.clip, root)
        payload = world.entity(args.entity)
    elif args.command == "ask":
        world = _world_for_clip(args.project, args.clip, root)
        payload = world.answer({"aspect": args.question,
                                "start": args.start, "end": args.end})
    else:
        parser.print_help()
        return 2
    payload["size"] = measure_context(payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

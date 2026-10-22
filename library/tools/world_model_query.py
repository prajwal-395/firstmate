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
    python3 -m library.tools.world_model_query search <project> \
        --clip clip_003 --mode action --action hand_near_mouth
    python3 -m library.tools.world_model_query evidence <project> \
        --clip clip_003 --start 40 --end 55

It is also the ONE SEARCH surface: a consumer asks for media by
speech, appearance, action, identity, relation or temporal order, and
gets GROUNDED MEDIA INTERVALS back - each interval naming the source
bytes it came from (the universal source reference), the lane and field
that produced it, the epistemic class its producer earned, and either a
confidence or an explicit uncertainty. Evidence can be inspected
without Resolve: the frames, verdicts and records behind an interval
are read out of the source memory, and a reference that cannot be
placed in a file is refused with a named reason rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:
    from library.tools import (
        claim_provenance,
        conversation_clock,
        event_spans,
        person_entity,
        person_measurements,
        source_memory,
        source_reference,
        span_verification,
        token_measure,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools import (
        claim_provenance,
        conversation_clock,
        event_spans,
        person_entity,
        person_measurements,
        source_memory,
        source_reference,
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
    "m3c": "expressions.json",
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
    "m3c": "source_memory.source_dir/slot_expressions",
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

SEARCH_MODES = ("speech", "appearance", "action", "identity", "relation",
                "order")
"""The ways a consumer asks the world for media.

One enumeration, and the six modes are the six the punchlist names:
speech, appearance, action, identity, relation and temporal order. A
consumer that needs a seventh way to ask is a consumer this interface
does not serve yet, and it is named here rather than smuggled in as a
flag.
"""

MODE_LANES = {
    "speech": ("m1", "m7"),
    "appearance": ("m7", "m3b", "m3c"),
    "action": ("m7", "m8"),
    "identity": ("m3b", "m7", "m10"),
    "relation": ("m10",),
    "order": (),
}
"""The lanes each mode reads.

A mode that consults a lane which was never built says so in the
answer's `lanes_consulted_absent`, and the answer's `unanswered` names
the reason. An absent lane is never an empty result.
"""

CLAIM_KEY_BY_FACT = {
    ("speech", "utterance"): "words",
    ("speech", "speaking"): "speaking_span",
    ("people", "on_screen"): "visible_span",
    ("people", "face_track"): "identity",
    ("people", "voice_track"): "identity",
    ("people", "expression"): "expression",
    ("sound", "sound_event"): "sound_event",
    ("scene", "scene"): "scene",
}
"""The claim each kind of fact is, and so the class its producer earned.

The claim belongs to the producer, not to the reader
(`claim_provenance`), so a fact carries the class the lane's own
declaration earns - never a class this module invented.
"""

CLAIM_KEY_BY_ASPECT = {
    "events": "candidate_span",
    "relationships": "relationship",
}
"""The claim of a kind whose name varies - a candidate predicate, a
relationship predicate."""

NO_SCORE = "the lane published no score for this claim"
"""Why a confidence is absent when the lane stored no number."""

NO_TRANSCRIPT_SCORE = ("the transcript lane publishes no confidence score; "
                       "its 0.0 is a placeholder, not a measurement")
"""Why a transcribed utterance carries no confidence.

The M1 document carries `confidence: 0.0` on every utterance and its
own documentation says no arm publishes one. A 0.0 read as a score
would be a false zero: the utterance is no less certain than a
measured one, and the number says nothing at all.
"""

CANDIDATE_NOT_ANSWER = ("a geometric candidate, not an answer: the "
                         "verifier's verdict is not a probability")

PRESENCE_NOT_SCORE = ("the lane measured a presence, not a probability")
"""Why an on-screen span carries no confidence.

A person being in the picture is a measured span, not a score: the
lane stored no number and none is invented here.
"""

SINGLE_SAMPLE = ("a single measured sample: it is named by the "
                 "frame it was measured on, when that frame's index was "
                 "measured")
"""Why a one-instant measurement is referenced by a frame.

A single sample is not a span: a zero-length measurement cannot be a
half-open interval, and widening it would invent evidence. The frame
it was measured on names it instead, and a sample whose frame index
was never measured carries no reference at all - and says so.
"""

NO_FRAME_INDEX = ("the frame index of this sample was never measured, so "
                  "the sample carries no reference")
"""Why a one-instant measurement with no frame index has no reference."""

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4,
            "fifth": 5}
"""The ordinals an order may name, and the number each one means."""

NO_REFERENCE = object()
"""A row that carries NO reference, and says why.

The default is to compute the reference; this is the one case where
the measurement knows it cannot be placed and the reason is the
caller's to give.
"""



def _overlap(start: float, end: float, lo: float, hi: float) -> bool:
    """Whether [start, end) overlaps [lo, hi)."""
    return start < hi and end > lo


def _matches(text: str, query: str) -> bool:
    """Whether a text carries every term of a query.

    The match is on the words, not on the string: a query for
    "car keys" finds the utterance that says "the car keys", and a
    query for nothing matches everything.
    """
    terms = [term for term in re.split(r"\W+", str(query).lower()) if term]
    if not terms:
        return True
    body = str(text or "").lower()
    return all(term in body for term in terms)


def _turn_speaker(turn: Optional[dict]) -> Optional[str]:
    """The entity a measured speaking turn belongs to."""
    if not turn:
        return None
    return turn.get("face_track") or turn.get("voice_track")


def _turn_holds(turn: Optional[dict], entity: str) -> bool:
    """Whether a measured speaking turn belongs to an entity.

    The entity may be named by either of the turn's tracks: a face
    track and a voice track are two names for the same measured turn.
    """
    if not turn:
        return False
    return entity in (turn.get("face_track"), turn.get("voice_track"))


def _select_ordinal(rows: List[dict], ordinal) -> List[dict]:
    """The rows whose occurrence of their kind is the ordinal asked for."""
    if isinstance(ordinal, bool):
        raise ValueError("an ordinal is a name or a number")
    if isinstance(ordinal, int) or (isinstance(ordinal, str)
                                    and ordinal.isdigit()):
        want = int(ordinal)
        return [row for row in rows if row.get("occurrence") == want]
    if str(ordinal) == "last":
        latest: Dict[str, tuple] = {}
        for row in rows:
            kind = row.get("kind")
            count = row.get("occurrence") or 0
            if count >= latest.get(kind, (0, None))[0]:
                latest[kind] = (count, row)
        return [pair[1] for pair in latest.values() if pair[1] is not None]
    want = ORDINALS.get(str(ordinal))
    if want is None:
        raise ValueError(f"unknown ordinal {ordinal!r}; have "
                         f"{', '.join(sorted(ORDINALS))}, last, or a number")
    return [row for row in rows if row.get("occurrence") == want]


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


def claim_key_for(aspect: str, kind: str) -> Optional[str]:
    """The claim a kind of fact is, or None when nobody declared one.

    A kind nobody declared keeps the class `unknown`: it is never
    defaulted to a measurement, because the class is the producer's to
    declare and this module is only the reader.
    """
    key = CLAIM_KEY_BY_FACT.get((aspect, kind))
    if key is not None:
        return key
    return CLAIM_KEY_BY_ASPECT.get(aspect)


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
        if name == "m3c":
            path = (source_memory.source_dir(digest, root)
                    / source_memory.SLOT_EXPRESSIONS)
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return document if isinstance(document, dict) else None
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

    # ── The grounding ───────────────────────────────────────────────

    def grounding(self) -> dict:
        """What every answer from this source is grounded in.

        The source, its asset, its stream and the clock that counts it.
        A consumer that holds this can place any interval this world
        returns inside the original media, without Resolve and without
        the project that asked.
        """
        digest = self.content_digest
        return {
            "content_digest": digest,
            "asset_id": source_reference.asset_id_for_digest(digest),
            "stream_id": source_reference.stream_id_for_digest(digest),
            "clock_id": source_reference.clock_id_for_digest(digest),
            "ticks_per_second": source_reference.SOURCE_TICKS_PER_SECOND,
            "tick_unit": "millisecond",
        }

    def _reference(self, start: float, end: float) -> Optional[dict]:
        """The universal reference to a span of this source.

        None when the span cannot be counted on the source's clock: a
        zero-length, negative or non-finite span is refused, never
        widened into an interval nobody measured.
        """
        try:
            return source_reference.interval_for_digest(
                self.content_digest, float(start), float(end))
        except source_reference.SourceReferenceError:
            return None

    def _sample_reference(self, instant: float) -> Optional[dict]:
        """The reference to the frame a single sample was measured on.

        A measurement at one instant is not a span, so it is named by
        the frame that carries it - and only when that frame's index
        was measured.
        """
        m2 = source_memory.read_m2(self.content_digest, self.root)
        if not m2:
            return None
        frame = None
        for row in m2.get("frames") or []:
            moment = row.get("t")
            if moment is None:
                continue
            if abs(float(moment) - float(instant)) <= 0.5:
                frame = row
                break
        if frame is None:
            return None
        index = frame.get("frame_index")
        if index is None:
            return None
        try:
            return source_reference.element(
                source_reference.stream_id_for_digest(self.content_digest),
                source_reference.clock_id_for_digest(self.content_digest),
                frame_index=int(index))
        except (source_reference.SourceReferenceError, TypeError, ValueError):
            return None

    def _row(self, aspect: str, kind: str, start: float, end: float,
             value: dict, lane: str, field: str,
             entity: Optional[str] = None,
             status: str = MEASURED,
             confidence: Optional[float] = None,
             confidence_basis: Optional[str] = None,
             uncertainty_reason: Optional[str] = None,
             reference=None) -> dict:
        """One fact, grounded: the source, the class, the confidence.

        The fact keeps the shape the surface already answered with, and
        gains three things a consumer of media needs and a reader of a
        fact alone does not get:

        * `source_reference` - the universal reference to the span, so
          the answer names the bytes it came from;
        * `provenance.class` - the epistemic class the lane's own
          producer earned (`claim_provenance`), never one this module
          invented;
        * `confidence` and `uncertainty` - either a measured score or
          the named reason there is none.
        """
        row = _fact(aspect, kind, start, end, value, lane, field,
                    entity=entity, status=status)
        key = claim_key_for(aspect, kind)
        tag = claim_provenance.tag(key) if key else {
            "class": claim_provenance.UNKNOWN_CLASS, "claim": None}
        row["provenance"].update({
            "class": tag["class"],
            "claim": tag.get("claim"),
            "producer": tag.get("producer"),
            "measurement_type": tag.get("measurement_type"),
        })
        if reference is NO_REFERENCE:
            placed = None
            refused = True
        else:
            placed = self._reference(start, end)
            refused = False
        row["source_reference"] = placed
        row["confidence"] = {"value": confidence, "basis": confidence_basis}
        reason = uncertainty_reason
        if reason is None:
            if status == "candidate":
                reason = CANDIDATE_NOT_ANSWER
            elif confidence is not None:
                reason = ("the lane measured this score; the class says how "
                          "far the claim can be trusted")
            else:
                reason = NO_SCORE
        if placed is None and not refused:
            reason = ("the span could not be counted on the source's "
                      "clock, so it carries no reference")
        row["uncertainty"] = {"class": tag["class"], "status": status,
                              "reason": reason}
        return row

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
                rows.append(self._row(
                    "speech", "utterance", float(start), float(end),
                    {"text": utterance.get("text"),
                     "words": len(utterance.get("words") or [])},
                    "m1", "utterances",
                    uncertainty_reason=NO_TRANSCRIPT_SCORE))
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
                rows.append(self._row(
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
                rows.append(self._row(
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
                    rows.append(self._row(
                        "people", "face_track", float(start), float(end),
                        {"track_id": track_id,
                         "person": self._name_of(track_id)},
                        "m3b", "faces.spans", entity=track_id,
                        confidence=span.get("det_score"),
                        confidence_basis="m3b.faces[].spans[].det_score"))
            for voice in m3b.get("voices") or []:
                track_id = voice.get("track_id")
                for span in voice.get("spans") or []:
                    start, end = span[0], span[1]
                    if not _overlap(float(start), float(end), lo, hi):
                        continue
                    rows.append(self._row(
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
                rows.append(self._row(
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
                    rows.append(self._row(
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
            rows.append(self._row(
                "sound", "sound_event", float(start), float(end),
                {"label": event.get("label"),
                 "confidence": event.get("confidence")},
                "m5", "sound_events",
                confidence=event.get("confidence"),
                confidence_basis="m5.sound_events[].confidence"))
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
                rows.append(self._row(
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
                rows.append(self._row(
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
                    rows.append(self._row(
                        "people", "face_track", float(span["start"]),
                        float(span["end"]),
                        {"track_id": entity_id,
                         "person": self._name_of(entity_id)},
                        "m3b", "faces.spans", entity=entity_id))
            for voice in m3b.get("voices") or []:
                if voice.get("track_id") != entity_id:
                    continue
                for span in voice.get("spans") or []:
                    rows.append(self._row(
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
                    rows.append(self._row(
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
                    rows.append(self._row(
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
                    rows.append(self._row(
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

    # ── The search ──────────────────────────────────────────────────

    def search(self, query: dict) -> dict:
        """One search, answered as grounded media intervals.

        The query names a MODE and that mode's own selectors. The
        answer carries the intervals, the grounding they are placed in,
        the lanes the mode consulted, the lanes that were never built,
        and the reasons the answer could not be complete.

        An interval is a MEDIA interval: it names the source bytes it
        came from, the lane and field that produced it, the epistemic
        class its producer earned, and either a confidence or the named
        reason there is none. Nothing in the answer is Ren-specific,
        so an animation, sound-design or asset consumer can read it
        without a timeline, a reel or a running Resolve.
        """
        if not isinstance(query, dict):
            raise ValueError("a search is a mapping")
        mode = query.get("mode")
        if mode not in SEARCH_MODES:
            raise ValueError(f"unknown search mode {mode!r}; have "
                             f"{', '.join(SEARCH_MODES)}")
        rows, unanswered = self._search(mode, query)
        lanes = self._search_lanes(mode, query)
        absent = [lane for lane in lanes if self.lane(lane) is None]
        return {
            "query": dict(query),
            "mode": mode,
            "grounding": self.grounding(),
            "intervals": rows,
            "lanes_consulted": list(lanes),
            "lanes_consulted_absent": absent,
            "lanes_used": sorted({row["provenance"]["lane"] for row in rows}),
            "unanswered": unanswered,
        }

    def _search_lanes(self, mode: str, query: dict) -> Sequence[str]:
        """The lanes a mode consults."""
        if mode == "order":
            of = query.get("of") or "action"
            return MODE_LANES.get(of, ())
        return MODE_LANES.get(mode, ())

    def _search(self, mode: str, query: dict):
        if mode == "speech":
            return self._search_speech(query)
        if mode == "appearance":
            return self._search_appearance(query)
        if mode == "action":
            return self._search_action(query)
        if mode == "identity":
            return self._search_identity(query)
        if mode == "relation":
            return self._search_relation(query)
        return self._search_order(query)

    def _window(self, query: dict) -> Optional[tuple]:
        """The span a search is bounded by, or None for the whole source."""
        window = query.get("window")
        if window is None:
            start, end = query.get("start"), query.get("end")
            if start is None or end is None:
                return None
            window = (start, end)
        try:
            lo, hi = float(window[0]), float(window[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError("a window is a pair of seconds") from None
        if hi < lo:
            lo, hi = hi, lo
        return lo, hi

    def _search_speech(self, query: dict):
        """The words that were said, and the measured turns they fell in.

        A speech search answers two things at once: the utterances that
        carry the words, and the measured speaking turns that say who
        said them. A query with no text returns every turn in its
        window; a query with text returns the turns the matched
        utterances fall in.
        """
        text = (query.get("text") or "").strip()
        entity = query.get("entity")
        window = self._window(query)
        m1 = self.lane("m1")
        if not m1:
            return [], ["the m1 lane was never built for this source"]
        rows: List[dict] = []
        matched: List[tuple] = []
        for utterance in m1.get("utterances") or []:
            start, end = utterance.get("start"), utterance.get("end")
            if start is None or end is None:
                continue
            if window and not _overlap(float(start), float(end), *window):
                continue
            body = utterance.get("text") or ""
            if text and not _matches(body, text):
                continue
            turn = self._speaking_turn_at(float(start), float(end))
            if entity and not _turn_holds(turn, entity):
                continue
            speaker = _turn_speaker(turn)
            rows.append(self._row(
                "speech", "utterance", float(start), float(end),
                {"text": body,
                 "matched_terms": text or None,
                 "words": len(utterance.get("words") or []),
                 "speaker": speaker,
                 "speaker_basis": ("the measured speaking span that "
                                   "overlaps the utterance")
                 if speaker else None},
                "m1", "utterances", entity=speaker,
                uncertainty_reason=NO_TRANSCRIPT_SCORE))
            matched.append((float(start), float(end)))
        m7 = self.lane("m7")
        if m7:
            spans = (((m7.get("predicates") or {}).get("speaking") or {})
                     .get("spans") or [])
            for span in spans:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if window and not _overlap(float(start), float(end), *window):
                    continue
                if entity and not _turn_holds(span, entity):
                    continue
                if text and not any(_overlap(float(start), float(end),
                                             low, high)
                                    for low, high in matched):
                    continue
                face_track = span.get("face_track")
                rows.append(self._row(
                    "speech", "speaking", float(start), float(end),
                    {"voice_track": span.get("voice_track"),
                     "face_track": face_track,
                     "person": self._name_of(face_track)},
                    "m7", "predicates.speaking.spans",
                    entity=face_track or span.get("voice_track")))
        return rows, []

    def _speaking_turn_at(self, start: float, end: float) -> Optional[dict]:
        """The measured speaking turn that covers this instant."""
        m7 = self.lane("m7")
        if not m7:
            return None
        spans = (((m7.get("predicates") or {}).get("speaking") or {})
                 .get("spans") or [])
        best: Optional[dict] = None
        best_overlap = 0.0
        for span in spans:
            low, high = span.get("start"), span.get("end")
            if low is None or high is None:
                continue
            overlap = min(float(high), end) - max(float(low), start)
            if overlap > best_overlap:
                best_overlap = overlap
                best = span
        return best

    def _search_appearance(self, query: dict):
        """Where a person is in the picture, and what was measured there."""
        entity = query.get("entity")
        expression = query.get("expression")
        window = self._window(query)
        rows: List[dict] = []
        unanswered: List[str] = []
        m7 = self.lane("m7")
        if m7:
            spans = (((m7.get("predicates") or {}).get("on_screen") or {})
                     .get("spans") or [])
            for span in spans:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if window and not _overlap(float(start), float(end), *window):
                    continue
                face_track = span.get("face_track")
                if entity and face_track != entity:
                    continue
                rows.append(self._row(
                    "people", "on_screen", float(start), float(end),
                    {"face_track": face_track,
                     "person": self._name_of(face_track)},
                    "m7", "predicates.on_screen.spans", entity=face_track,
                    uncertainty_reason=PRESENCE_NOT_SCORE))
        else:
            unanswered.append("the m7 lane was never built for this source")
        m3b = self.lane("m3b")
        if m3b:
            for face in m3b.get("faces") or []:
                track_id = face.get("track_id")
                if entity and track_id != entity:
                    continue
                for span in face.get("spans") or []:
                    start, end = span.get("start"), span.get("end")
                    if start is None or end is None:
                        continue
                    if window and not _overlap(float(start), float(end),
                                               *window):
                        continue
                    rows.append(self._row(
                        "people", "face_track", float(start), float(end),
                        {"track_id": track_id,
                         "person": self._name_of(track_id),
                         "box": span.get("box")},
                        "m3b", "faces.spans", entity=track_id,
                        confidence=span.get("det_score"),
                        confidence_basis="m3b.faces[].spans[].det_score"))
        else:
            unanswered.append("the m3b lane was never built for this source")
        m3c = self.lane("m3c")
        if m3c:
            for frame in m3c.get("frames") or []:
                instant = frame.get("t")
                if instant is None:
                    continue
                for face in frame.get("faces") or []:
                    label = face.get("expression")
                    if not label:
                        continue
                    if expression and label != expression:
                        continue
                    if window and not _overlap(float(instant), float(instant),
                                               *window):
                        continue
                    sample = self._sample_reference(float(instant))
                    rows.append(self._row(
                        "people", "expression", float(instant),
                        float(instant),
                        {"expression": label,
                         "box": face.get("box"),
                         "person": self._name_of(entity)},
                        "m3c", "frames[].faces[]", entity=entity,
                        confidence=face.get("confidence"),
                        confidence_basis="m3c.frames[].faces[].confidence",
                        uncertainty_reason=SINGLE_SAMPLE if sample
                        else NO_FRAME_INDEX,
                        reference=sample if sample else NO_REFERENCE))
        else:
            unanswered.append("the m3c lane was never built for this source")
        return rows, unanswered

    def _search_action(self, query: dict):
        """The candidate actions, with the verdicts that judged them."""
        action = query.get("action")
        entity = query.get("entity")
        window = self._window(query)
        verified = query.get("verified")
        m7 = self.lane("m7")
        if not m7:
            return [], ["the m7 lane was never built for this source"]
        m8 = self.lane("m8") or {}
        rows: List[dict] = []
        for name, block in (m7.get("candidates") or {}).items():
            if action and name != action:
                continue
            if not isinstance(block, dict):
                continue
            for span in block.get("spans") or []:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if window and not _overlap(float(start), float(end), *window):
                    continue
                face_track = span.get("face_track")
                if entity and face_track != entity:
                    continue
                verdicts = self._verdicts_for_span(span, m8)
                status = CANDIDATE
                if verdicts:
                    status = ("verified"
                              if any((v.get("answer") or "").lower() == "yes"
                                     for v in verdicts)
                              else "rejected")
                if verified is True and status != "verified":
                    continue
                if verified is False and status == "verified":
                    continue
                rows.append(self._row(
                    "events", name, float(start), float(end),
                    {"face_track": face_track,
                     "person": self._name_of(face_track),
                     "verdicts": verdicts},
                    "m7", f"candidates.{name}.spans", entity=face_track,
                    status=status))
        return rows, []

    def _search_identity(self, query: dict):
        """Every occurrence of one entity, across the lanes that name it."""
        wanted = query.get("entity") or query.get("name")
        if not wanted:
            return [], ["the query named no entity"]
        tracks = self._tracks_of(str(wanted))
        if not tracks:
            return [], [
                (f"no entity named {wanted!r} was measured in this "
                 "source")]
        rows: List[dict] = []
        for track in tracks:
            rows.extend(self.entity(track)["occurrences"])
        rows.sort(key=lambda row: (row["start"], row["end"], row["kind"]))
        return rows, []

    def _tracks_of(self, wanted: str) -> List[str]:
        """The tracks a name or an id resolves to in this source."""
        tracks: List[str] = []
        m3b = self.lane("m3b")
        if m3b:
            for face in m3b.get("faces") or []:
                if face.get("track_id") == wanted:
                    tracks.append(wanted)
            for voice in m3b.get("voices") or []:
                if voice.get("track_id") == wanted:
                    tracks.append(wanted)
        if not tracks:
            for track, name in self._names.items():
                if name == wanted and track not in tracks:
                    tracks.append(track)
        return tracks

    def _search_relation(self, query: dict):
        """The relationship candidates, and who owns them."""
        relation = query.get("relation")
        entity = query.get("entity")
        window = self._window(query)
        m10 = self.lane("m10")
        if not m10:
            return [], ["the m10 lane was never built for this source"]
        rows: List[dict] = []
        for name, block in (m10.get("relationships") or {}).items():
            if relation and name != relation:
                continue
            if not isinstance(block, dict):
                continue
            for span in block.get("spans") or []:
                start, end = span.get("start"), span.get("end")
                if start is None or end is None:
                    continue
                if window and not _overlap(float(start), float(end), *window):
                    continue
                owners = span.get("owners") or []
                if entity and entity not in owners:
                    continue
                rows.append(self._row(
                    "relationships", name, float(start), float(end),
                    {"owners": owners,
                     "people": [self._name_of(owner) for owner in owners],
                     "frames": len(span.get("frames") or [])},
                    "m10", f"relationships.{name}.spans",
                    entity=owners[0] if owners else None,
                    status=CANDIDATE))
        return rows, []

    def _search_order(self, query: dict):
        """The occurrences of one thing, in order, named by their ordinal.

        An order is over another mode: the second time an action
        happened, the last thing a person said, what came after a
        given moment. The rows carry the ordinal of their own KIND, so
        "the second time" is a nameable answer.
        """
        of = query.get("of") or "action"
        if of == "order" or of not in SEARCH_MODES:
            raise ValueError("an order is over a mode; have "
                             f"{', '.join(m for m in SEARCH_MODES if m != 'order')}")
        base = {key: value for key, value in query.items()
                if key not in ("mode", "of", "ordinal", "relative",
                               "reference")}
        rows, unanswered = self._search(of, base)
        rows = list(rows)
        _number_occurrences(rows)
        ordinal = query.get("ordinal")
        if ordinal is not None:
            rows = _select_ordinal(rows, ordinal)
        relative = query.get("relative")
        if relative:
            reference = query.get("reference") or {}
            low = reference.get("start")
            high = reference.get("end")
            if low is None or high is None:
                return [], [
                    ("an order relative to another event needs that "
                     "event's start and end")]
            if relative == "after":
                rows = [row for row in rows if row["start"] >= float(high)]
            elif relative == "before":
                rows = [row for row in rows if row["end"] <= float(low)]
            else:
                raise ValueError("a relative order is 'after' or 'before'")
        return rows, unanswered

    # ── The evidence ────────────────────────────────────────────────

    def evidence(self, start: float, end: float, *,
                 aspects: Optional[Sequence[str]] = None,
                 model: Optional[dict] = None,
                 project_folder: Optional[str] = None) -> dict:
        """The evidence behind a span, inspected WITHOUT Resolve.

        The intervals are the world's own facts for the span. The
        evidence rows are what supports them and can be opened: the
        frames sampled inside the span, the verdicts on the candidate
        spans, and the lane record each fact was read from.

        A reference that cannot be placed in a file is refused with a
        named reason - and nothing here needs a running Resolve, so the
        evidence can be inspected on a machine that has none.
        """
        lo, hi = float(start), float(end)
        if hi < lo:
            lo, hi = hi, lo
        rows = self.facts(lo, hi, aspects)
        evidence: List[dict] = []
        m2 = source_memory.read_m2(self.content_digest, self.root)
        if m2:
            for frame in m2.get("frames") or []:
                instant = frame.get("t")
                if instant is None:
                    continue
                if not _overlap(float(instant), float(instant), lo, hi):
                    continue
                evidence.append({
                    "kind": "frame",
                    "t": float(instant),
                    "file": frame.get("file"),
                    "path": source_memory.frame_abspath(self.content_digest,
                                                        frame, self.root),
                    "frame_index": frame.get("frame_index"),
                    "provenance": {
                        "lane": "m2", "slot": source_memory.SLOT_FRAMES_INDEX,
                        "reader": "source_memory.read_m2",
                        "field": "frames[]", "status": "measured",
                    },
                })
        else:
            evidence.append({
                "kind": "absence",
                "reason": "the m2 lane was never built for this source, so "
                          "no frame shows what this span holds",
                "provenance": {"lane": "m2",
                               "slot": source_memory.SLOT_FRAMES_INDEX,
                               "reader": "source_memory.read_m2",
                               "field": "frames[]", "status": "absent"},
            })
        for row in rows:
            for verdict in (row.get("value") or {}).get("verdicts") or []:
                evidence.append({
                    "kind": "verdict",
                    "statement": verdict.get("statement"),
                    "answer": verdict.get("answer"),
                    "reason": verdict.get("reason"),
                    "model": verdict.get("model"),
                    "interval": {"start": row["start"], "end": row["end"]},
                    "provenance": dict(row["provenance"]),
                })
        for lane in sorted({row["provenance"]["lane"] for row in rows}):
            slot = LANE_SLOT.get(lane)
            evidence.append({
                "kind": "record",
                "lane": lane,
                "slot": slot,
                "path": str(source_memory.source_dir(self.content_digest,
                                                     self.root) / slot)
                if slot else None,
                "provenance": {"lane": lane, "slot": slot,
                               "reader": LANE_READER.get(lane),
                               "field": "the record the facts were read from",
                               "status": "measured"},
            })
        resolution = self._resolve(rows, model, project_folder)
        return {
            "span": {"start": lo, "end": hi},
            "grounding": self.grounding(),
            "intervals": rows,
            "evidence": evidence,
            "resolution": resolution,
        }

    def _resolve(self, rows: List[dict], model: Optional[dict],
                 project_folder: Optional[str]) -> dict:
        """Where the bytes behind these intervals are, or why not.

        The reference is placed against the canonical Project Model when
        one is given. A model that carries no measured frame table
        cannot place an interval in a file, and says so by name.
        """
        if model is None:
            return {"status": "not_attempted",
                    "reason": "no project model was given, so no reference "
                              "was placed in a file",
                    "resolved": 0, "refused": 0}
        from library.tools import reference_resolver
        resolved = 0
        refusals: List[dict] = []
        for row in rows:
            reference = row.get("source_reference")
            if not reference:
                continue
            try:
                placed = reference_resolver.resolve(
                    model, reference, project_folder=project_folder,
                    memory_root=self.root)
            except reference_resolver.ReferenceResolutionError as exc:
                refusals.append({"reference": reference, "reason": exc.reason,
                                  "detail": str(exc)})
                continue
            resolved += 1
            refusals.append({"reference": reference, "reason": "resolved",
                             "detail": placed.to_dict()})
        return {
            "status": "resolved" if resolved else "refused",
            "resolved": resolved,
            "refused": sum(1 for row in refusals
                           if row.get("reason") != "resolved"),
            "placements": refusals,
        }


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


def _search_query(args) -> dict:
    """The query a `search` invocation asks."""
    query: dict = {"mode": args.mode}
    for name in ("text", "action", "relation", "expression", "entity", "of",
                 "ordinal", "relative"):
        value = getattr(args, name, None)
        if value:
            query[name] = value
    if args.start is not None and args.end is not None:
        query["window"] = (args.start, args.end)
    if args.verified == "yes":
        query["verified"] = True
    elif args.verified == "no":
        query["verified"] = False
    if args.reference_start is not None and args.reference_end is not None:
        query["reference"] = {"start": args.reference_start,
                              "end": args.reference_end}
    return query


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

    p_search = sub.add_parser(
        "search", help="search the world for media intervals")
    p_search.add_argument("project")
    p_search.add_argument("--clip", required=True)
    p_search.add_argument("--mode", required=True, choices=list(SEARCH_MODES))
    p_search.add_argument("--text", help="the words to look for (speech)")
    p_search.add_argument("--action", help="the action predicate (action)")
    p_search.add_argument("--relation", help="the relation predicate")
    p_search.add_argument("--expression", help="the expression label")
    p_search.add_argument("--entity", help="a face track, a voice track or "
                                            "a declared name")
    p_search.add_argument("--start", type=float)
    p_search.add_argument("--end", type=float)
    p_search.add_argument("--of", help="the mode an order is over")
    p_search.add_argument("--ordinal", help="first, second, last, or a number")
    p_search.add_argument("--relative", choices=["after", "before"])
    p_search.add_argument("--reference-start", type=float)
    p_search.add_argument("--reference-end", type=float)
    p_search.add_argument("--verified", choices=["yes", "no"],
                          help="only the candidates a verifier judged")
    p_search.add_argument("--memory-root")

    p_evidence = sub.add_parser(
        "evidence", help="the evidence behind a span, without Resolve")
    p_evidence.add_argument("project")
    p_evidence.add_argument("--clip", required=True)
    p_evidence.add_argument("--start", type=float, required=True)
    p_evidence.add_argument("--end", type=float, required=True)
    p_evidence.add_argument("--aspect", action="append",
                            choices=list(ASPECTS))
    p_evidence.add_argument("--model", action="store_true",
                            help="place every reference in the project's "
                                 "canonical model (it is large)")
    p_evidence.add_argument("--memory-root")

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
    elif args.command == "search":
        world = _world_for_clip(args.project, args.clip, root)
        payload = world.search(_search_query(args))
    elif args.command == "evidence":
        world = _world_for_clip(args.project, args.clip, root)
        model = None
        if args.model:
            from library.tools import project_model_store
            _revision, model = project_model_store.load_project_model(
                Path(args.project).expanduser())
        payload = world.evidence(args.start, args.end, aspects=args.aspect,
                                 model=model, project_folder=args.project)
    else:
        parser.print_help()
        return 2
    payload["size"] = measure_context(payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

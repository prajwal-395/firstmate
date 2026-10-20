"""Typed temporal events: what happened, when, and how sure we are.

The pipeline MEASURES many things about time - scene cuts, aligned
words, sound events, on-screen text, tracked boxes, optical flow - and
INFERRS others (the vision model's prose about an action).  Until now
each of those lived in its own lane with its own shape, and nothing
could say "this is the SECOND time she opens the box", or "this frame
LOOKS like she is opening the box but nothing moved", or "this cut
depends on the action being right, and nobody checked".

This module is the ONE typed surface over that material.  A
``TemporalEvent`` carries:

* an EDIT-CRITICAL TYPE from the one vocabulary
  (``library/tools/edit_critical_events.py``) and, where the material
  says so, an ACTION CLASS from the one vocabulary here (VI-011);
* ONSET, the meaningful INTERMEDIATE STATES and COMPLETION, each with
  its own status and its own uncertainty (VI-012);
* its SUBJECTS and OBJECTS, and the SOURCE INTERVAL it was measured on
  (VI-013);
* its ORDINAL among repeats of the same thing, and the group it repeats
  in (VI-014);
* whether the action OCCURRED or merely looked like it happens - a
  static frame is not an occurrence (VI-016);
* the CONTINUITY of its subject or object across shots (VI-018);
* a PRIORITY, because an edit wants a cut point long before it wants a
  catalogue of everything that moved (VI-019).

Nothing here invents a judgement the footage does not carry.  An event
that cannot be typed is REPORTED, not guessed into a type.  A boundary
whose precision was never measured carries ``uncertainty_seconds=None``
and a basis saying so - never a default reported as a measurement.

The index is DERIVED, never stored beside the footage: it is rebuilt
from the project's own step outputs and, when the project carries one,
from its Project Model snapshot.  A stale copy of the index cannot
therefore disagree with the footage it describes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from library.tools import edit_critical_events

# ── The vocabularies ───────────────────────────────────────────────────

EVENT_TYPES: tuple[str, ...] = edit_critical_events.event_types()
"""The edit-critical event types, re-exported from the ONE vocabulary.

This module does not own that list: ``edit_critical_events`` does, and
a second copy here would drift.
"""


ACTION_CLASSES: tuple[str, ...] = (
    # The held-out temporal-event benchmark's frozen classes, verbatim.
    "reach", "contact", "pick_up", "use", "set_down",
    "hand_raise", "pointing", "nodding", "head_shake", "waving",
    "person_enters", "person_leaves", "opening", "closing",
    # Classes the pipeline's own material carries.
    "hand_to_mouth", "hand_to_face", "drink", "position_change",
)
"""The typed ACTION vocabulary.

An event's ``event_type`` says what KIND of editorially-relevant thing
happened (the edit-critical axis); its ``action`` says WHICH action it
was, when the material names one.  The first fourteen entries are the
benchmark's frozen classes, kept verbatim so a held-out ground-truth
interval can be scored against these events without a translation
(``tests/contracts/test_temporal_event_vocabulary.py`` pins that).
"""

ACTION_LEXICON: dict[str, tuple[str, ...]] = {
    "reach": ("reach", "reaches", "reaching", "reached",
              "stretches out", "extends toward", "extends towards"),
    "contact": ("touches", "touching", "touched", "makes contact",
                "puts a hand on"),
    "pick_up": ("pick up", "picks up", "picked up", "picking up",
                "grabs", "grab", "grabbing", "lifts", "lifting"),
    "set_down": ("put down", "puts down", "putting down", "set down",
                 "sets down", "places", "placing", "drops", "dropping"),
    "use": ("uses", "using", "used", "operates", "operating"),
    "hand_raise": ("raises", "raising", "raised", "raises a hand",
                   "raises her hand", "raises his hand", "holds up"),
    "pointing": ("points", "pointing", "pointed", "points at",
                 "points to", "gestures at", "gestures toward"),
    "nodding": ("nods", "nodding", "nodded"),
    "head_shake": ("shakes her head", "shakes his head", "shakes their head",
                   "shaking his head", "shaking her head"),
    "waving": ("waves", "waving", "waved", "waves at"),
    "person_enters": ("enters", "enter", "entering", "entered",
                      "walks in", "comes in", "steps into", "walks into"),
    "person_leaves": ("leaves", "leave", "leaving", "walks out",
                      "exits", "exiting", "steps out"),
    "opening": ("opens", "opening", "opened", "unpacks", "unpacking",
                "unwraps", "unwrapping", "pulls open", "lifts the lid"),
    "closing": ("closes", "closing", "closed", "shuts", "shutting",
                "packs away", "puts the lid down"),
    "hand_to_mouth": ("covers her mouth", "covers his mouth",
                      "covers their mouth", "covers mouth",
                      "hand to mouth", "hand over mouth",
                      "touches her mouth", "touches his mouth",
                      "wipes her mouth", "wipes his mouth"),
    "hand_to_face": ("touches her face", "touches his face",
                     "touches their face", "touches face",
                     "strokes her face", "strokes his face"),
    "drink": ("drinks", "drinking", "drank", "takes a sip", "takes a drink",
              "sips", "sipping"),
    "position_change": ("moves", "moved", "shifts", "shifted", "slides",
                        "sliding", "repositions"),
}
"""Surface terms that name each action class.

This is a MECHANICAL classifier, not a judgement: it maps words to
classes so the machinery can be reasoned about without the model
inventing a type per event.  A text that matches nothing classifies as
``None`` - an event with no action class says so rather than borrowing
its neighbour's.
"""

_EVENT_TRIGGER_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    sorted(ACTION_LEXICON.items(),
           key=lambda item: (-max(len(term.split()) for term in item[1]),
                             ACTION_CLASSES.index(item[0]))))

_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def classify_action(text: str) -> tuple[str | None, str]:
    """The action class a description names, and the basis for it.

    Returns ``(class, basis)``.  ``class`` is ``None`` when the text
    names no action in the vocabulary, and ``basis`` then says so.  A
    longer trigger wins over a shorter one that starts the same way
    ("covers her mouth" beats "mouth"), and ties are broken by the
    vocabulary's own order so the same text always classifies the same
    way.
    """
    words = _tokens(text)
    if not words:
        return None, "no text to classify"
    for action, triggers in _EVENT_TRIGGER_ORDER:
        for trigger in triggers:
            parts = _tokens(trigger)
            span = len(parts)
            for start in range(len(words) - span + 1):
                if words[start:start + span] == parts:
                    return action, f"lexicon trigger {trigger!r}"
    return None, "no lexicon trigger matched"


# ── Priorities (VI-019) ────────────────────────────────────────────────

EVENT_PRIORITY: dict[str, int] = {
    "scene_transition": 10,
    "speech_start_end": 20,
    "word_phrase_boundary": 20,
    "speaker_change": 30,
    "action_state_change": 40,
    "object_interaction": 40,
    "onscreen_text_change": 50,
    "audio_event": 50,
    "retake_repeat": 60,
    "technical_defect": 60,
}
"""How much an event type matters to an EDIT, lowest number first.

A cut lands on a scene transition or a word boundary long before it
lands on a defect.  The order is the machinery's statement about where
analysis pays off; a type absent from this table is not an edit
priority and is reported as one.
"""


def event_priority(event_type: str) -> tuple[int, str]:
    """The priority of an event type, and the basis for it.

    Raises ``KeyError`` naming an unknown type: a type the priority
    table does not know is a type this machinery cannot rank, and a
    silent default would rank it as if it were a cut point.
    """
    try:
        return EVENT_PRIORITY[event_type], "edit-critical priority table"
    except KeyError:
        raise KeyError(
            f"no edit priority for event type {event_type!r}") from None


# ── The record ─────────────────────────────────────────────────────────

BOUNDARY_STATUSES = ("exact", "estimated", "open")
MOTION_STATUSES = ("motion_measured", "static_measured", "unmeasured")
CONTINUITY_STATUSES = ("cross_shot", "single_shot", "untracked")

SUBJECT_MOTION_EPSILON = 0.01
"""The smallest SUBJECT energy that counts as the subject having moved.

The optical-flow decomposition's ``subject_energy`` is a normalized
proxy magnitude.  Zero means the pixels that changed are explained by
camera translation: the frame is a look-alike, not an occurrence.
"""

_FRAME_DURATION_FALLBACK = None
"""No fallback frame duration is invented.  A boundary whose source
frame rate was never measured carries ``uncertainty_seconds=None``."""


@dataclass(frozen=True)
class Boundary:
    """One edge of an event, with how precisely it was located."""

    seconds: float | None
    status: str
    uncertainty_seconds: float | None = None
    basis: str = ""

    def __post_init__(self) -> None:
        if self.status not in BOUNDARY_STATUSES:
            raise ValueError(
                f"unknown boundary status {self.status!r}; "
                f"expected one of {BOUNDARY_STATUSES}")
        if self.status == "exact" and self.uncertainty_seconds not in (None, 0):
            raise ValueError(
                "an exact boundary cannot carry non-zero uncertainty")
        if self.status == "open" and self.uncertainty_seconds is not None:
            raise ValueError(
                "an open boundary cannot claim a bounded uncertainty")

    def to_dict(self) -> dict[str, Any]:
        return {"seconds": self.seconds, "status": self.status,
                "uncertainty_seconds": self.uncertainty_seconds,
                "basis": self.basis}


@dataclass(frozen=True)
class MotionReading:
    """Whether the SUBJECT moved inside an event's span (VI-016).

    ``motion_measured`` means the subject's own energy rose inside the
    span: something happened.  ``static_measured`` means the pixels
    that changed are explained by camera translation: the span LOOKS
    like the action and does not contain it.  ``unmeasured`` means the
    span was never analysed - never reported as static.
    """

    status: str
    displacement: float | None = None
    sample_count: int = 0
    basis: str = ""

    def __post_init__(self) -> None:
        if self.status not in MOTION_STATUSES:
            raise ValueError(
                f"unknown motion status {self.status!r}; "
                f"expected one of {MOTION_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "displacement": self.displacement,
                "sample_count": self.sample_count, "basis": self.basis}


@dataclass(frozen=True)
class Continuity:
    """Whether an event's subject or object is tracked ACROSS shots."""

    status: str
    shots: tuple[str, ...] = ()
    basis: str = ""

    def __post_init__(self) -> None:
        if self.status not in CONTINUITY_STATUSES:
            raise ValueError(
                f"unknown continuity status {self.status!r}; "
                f"expected one of {CONTINUITY_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "shots": list(self.shots),
                "basis": self.basis}


@dataclass(frozen=True)
class StateChange:
    """One property transition belonging to an event."""

    entity_id: str
    property: str
    before: Any = None
    after: Any = None
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "property": self.property,
                "from": self.before, "to": self.after,
                "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class TemporalEvent:
    """One typed thing that happened in the footage, on one source clock."""

    event_id: str
    event_type: str
    action: str | None
    action_text: str
    asset_id: str
    clip_id: str | None
    source_file: str | None
    clock_id: str
    start_seconds: float
    end_seconds: float
    onset: Boundary
    intermediate_states: tuple[Boundary, ...]
    completion: Boundary
    subjects: tuple[str, ...] = ()
    subject_labels: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    object_labels: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    status: str = "candidate"
    certainty: str = "inferred"
    occurrence: int | None = None
    repeat_group_id: str | None = None
    continuity: Continuity = field(
        default_factory=lambda: Continuity("untracked", (), "no track"))
    motion: MotionReading = field(
        default_factory=lambda: MotionReading("unmeasured", None, 0,
                                              "no motion measurement"))
    state_changes: tuple[StateChange, ...] = ()
    priority: int = 100
    producer: str = ""
    """Which lane produced the event: `temporal_index`, `vision_model`,
    `ocr` or `project_model`."""
    basis: str = ""

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"unknown edit-critical event type {self.event_type!r}")
        if self.action is not None and self.action not in ACTION_CLASSES:
            raise ValueError(
                f"unknown action class {self.action!r}")
        if self.end_seconds < self.start_seconds:
            raise ValueError("an event's end cannot precede its start")
        if self.occurrence is not None and self.occurrence < 1:
            raise ValueError("an event's occurrence is 1-based")

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def evidence_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.evidence_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "action": self.action,
            "action_text": self.action_text,
            "asset_id": self.asset_id,
            "clip_id": self.clip_id,
            "source_file": self.source_file,
            "clock_id": self.clock_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "duration_seconds": round(self.duration_seconds, 6),
            "onset": self.onset.to_dict(),
            "intermediate_states": [state.to_dict()
                                    for state in self.intermediate_states],
            "completion": self.completion.to_dict(),
            "subjects": list(self.subjects),
            "subject_labels": list(self.subject_labels),
            "objects": list(self.objects),
            "object_labels": list(self.object_labels),
            "evidence_ids": list(self.evidence_ids),
            "status": self.status,
            "certainty": self.certainty,
            "occurrence": self.occurrence,
            "repeat_group_id": self.repeat_group_id,
            "continuity": self.continuity.to_dict(),
            "motion": self.motion.to_dict(),
            "state_changes": [change.to_dict()
                              for change in self.state_changes],
            "priority": self.priority,
            "producer": self.producer,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class TemporalEventIndex:
    """Every typed event a project holds, plus what it could not type."""

    events: tuple[TemporalEvent, ...] = ()
    unmapped_kinds: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    clip_durations: Mapping[str, float] = field(default_factory=dict)
    """Each clip's measured duration, for the clip's own source clock."""

    def by_clip(self, clip_id: str) -> tuple[TemporalEvent, ...]:
        return tuple(event for event in self.events
                     if event.clip_id == clip_id)

    def of_type(self, event_type: str) -> tuple[TemporalEvent, ...]:
        return tuple(event for event in self.events
                     if event.event_type == event_type)

    def with_action(self, action: str) -> tuple[TemporalEvent, ...]:
        return tuple(event for event in self.events
                     if event.action == action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "unmapped_kinds": list(self.unmapped_kinds),
            "sources": list(self.sources),
            "clip_durations": dict(self.clip_durations),
            "count": len(self.events),
        }


# ── Ordinals and repeat groups (VI-014) ────────────────────────────────


def assign_occurrences(events: Sequence[TemporalEvent]) -> tuple[TemporalEvent, ...]:
    """Number each event among the repeats of its own kind in its clip.

    The group is ``(clip, event_type, action)``: the second time she
    opens the box IN THAT SHOT.  An event with no action class cannot
    be told apart from another of its type, so it is left unnumbered
    and reported as such by the caller - a number here would be a lie
    about which repetition it names.
    """
    ordered = sorted(events, key=lambda event: (
        event.clip_id or "", event.start_seconds, event.event_id))
    counters: dict[tuple[str | None, str, str], int] = {}
    out: list[TemporalEvent] = []
    for event in ordered:
        if event.action is None:
            out.append(event)
            continue
        key = (event.clip_id, event.event_type, event.action)
        counters[key] = counters.get(key, 0) + 1
        group = ":".join(part for part in
                         (event.clip_id or "-", event.event_type, event.action)
                         if part)
        out.append(replace(event, occurrence=counters[key],
                           repeat_group_id=group))
    return tuple(out)


# ── Motion, continuity, and the measured reading (VI-016, VI-018) ─────


def _subject_energy_samples(index_doc: Mapping[str, Any]) -> list[tuple[float, float]]:
    """``[(seconds, subject_energy)]`` from a clip's motion decomposition.

    Falls back to the decomposition's ``residual`` when the flow pass
    did not carry a subject energy.  An empty list means the clip was
    never analysed for motion: UNMEASURED, never static.
    """
    decomposition = index_doc.get("camera_motion_decomposition") or {}
    values = decomposition.get("values") or []
    samples: list[tuple[float, float]] = []
    for row in values:
        if not isinstance(row, Mapping):
            continue
        time = row.get("time")
        if time is None:
            continue
        energy = row.get("subject_energy")
        if energy is None:
            energy = row.get("residual")
        if energy is None:
            continue
        try:
            samples.append((float(time), float(energy)))
        except (TypeError, ValueError):
            continue
    return sorted(samples)


def read_motion(index_doc: Mapping[str, Any] | None,
                start: float, end: float) -> MotionReading:
    """Whether the SUBJECT moved inside ``[start, end)`` of a clip.

    The reading is the optical-flow decomposition's subject energy: the
    part of the flow that camera translation does NOT explain.  Zero
    means the frame looks like the action because the camera moved,
    not because anything was done - which is exactly the confusion
    VI-016 names.
    """
    if index_doc is None:
        return MotionReading("unmeasured", None, 0,
                             "the clip carries no motion decomposition")
    samples = _subject_energy_samples(index_doc)
    inside = [(time, energy) for time, energy in samples
              if start <= time < end]
    if not inside:
        return MotionReading(
            "unmeasured", None, 0,
            "the motion decomposition has no sample inside this span")
    peak = max(energy for _, energy in inside)
    status = ("motion_measured" if peak > SUBJECT_MOTION_EPSILON
              else "static_measured")
    return MotionReading(
        status, round(peak, 6), len(inside),
        "peak subject energy inside the span; camera translation excluded")


def read_intermediate_states(index_doc: Mapping[str, Any] | None,
                              start: float, end: float) -> tuple[Boundary, ...]:
    """The motion peaks inside a span: the states BETWEEN onset and end.

    These are the meaningful intermediate states (VI-012) the material
    actually carries: the moments the picture itself changed most
    inside the event.  A span with no measured peak has none, and says
    so by returning nothing.
    """
    if index_doc is None:
        return ()
    motion = index_doc.get("motion_energy") or {}
    peaks = motion.get("peak_motion_times") or motion.get("high_motion_times") or []
    rate = motion.get("sample_rate_hz")
    uncertainty = (1.0 / float(rate) / 2.0) if rate else None
    states = []
    for peak in peaks:
        try:
            seconds = float(peak)
        except (TypeError, ValueError):
            continue
        if not (start <= seconds < end):
            continue
        states.append(Boundary(
            seconds, "estimated", uncertainty,
            "a measured motion peak inside the span"))
    return tuple(states)


def read_continuity(entity_shots: Mapping[str, Iterable[str]],
                    entity_id: str | None) -> Continuity:
    """Whether an entity is tracked in more than one shot (VI-018).

    ``entity_shots`` maps an entity id to the shots it was tracked in.
    A subject tracked in two shots is one subject across a cut, and an
    edit can say so.  A subject tracked in one shot is one shot's
    subject: continuity is UNMEASURED, not absent.
    """
    if not entity_id:
        return Continuity("untracked", (), "the event names no tracked entity")
    shots = tuple(sorted(entity_shots.get(entity_id, ())))
    if not shots:
        return Continuity("untracked", (),
                          f"entity {entity_id!r} carries no track")
    if len(shots) > 1:
        return Continuity("cross_shot", shots,
                          "the entity is tracked in more than one shot")
    return Continuity("single_shot", shots,
                      "the entity is tracked in one shot only")


# ── Building the index from a project's own material ───────────────────


def _clip_row(catalog: Sequence[Mapping[str, Any]], clip_id: str | None) -> Mapping[str, Any]:
    for row in catalog:
        if row.get("clip_id") == clip_id:
            return row
    return {}


def _frame_duration(clip: Mapping[str, Any]) -> float | None:
    """The clip's frame duration, from the catalog's own frame rate.

    ``None`` when the catalog never measured one: the boundary's
    uncertainty is then unknown, and reported as unknown.
    """
    nominal = clip.get("frame_rate_nominal")
    if isinstance(nominal, (int, float)) and nominal > 0:
        return 1.0 / float(nominal)
    raw = clip.get("frame_rate")
    if isinstance(raw, str) and "/" in raw:
        numerator, _, denominator = raw.partition("/")
        try:
            num = float(numerator)
            den = float(denominator)
        except ValueError:
            return None
        if num > 0 and den > 0:
            return den / num
    return None


def _asset_id(clip_id: str) -> str:
    return f"clip:{clip_id}"


def _clock_id(clip_id: str) -> str:
    return f"clock:{clip_id}"


def _events_from_temporal_index(
        clip_id: str, clip: Mapping[str, Any],
        index_doc: Mapping[str, Any]) -> list[TemporalEvent]:
    """The MEASURED events a clip's 1.04 index carries.

    Scene transitions, sound events and speech spans.  All three are
    measurements of the footage, so their certainty is ``measured``.
    """
    out: list[TemporalEvent] = []
    asset_id = _asset_id(clip_id)
    clock_id = _clock_id(clip_id)
    source_file = clip.get("source_file") or clip.get("path")
    frame = _frame_duration(clip)
    priority, _ = event_priority("scene_transition")

    boundaries = index_doc.get("scene_boundaries") or []
    for number, boundary in enumerate(boundaries):
        if isinstance(boundary, Mapping):
            time = boundary.get("time")
            score = boundary.get("score")
        else:
            time = boundary
            score = None
        if time is None:
            continue
        try:
            seconds = float(time)
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            continue
        # The cut happens inside one frame of the reported time, so the
        # edge's radius IS that frame.  An "exact" edge carries no
        # radius, which is why this is not one.
        extent = frame if frame else 0.0
        uncertainty = frame
        out.append(TemporalEvent(
            event_id=f"{clip_id}:scene_transition:{number:03d}",
            event_type="scene_transition",
            action=None,
            action_text="",
            asset_id=asset_id,
            clip_id=clip_id,
            source_file=source_file,
            clock_id=clock_id,
            start_seconds=seconds,
            end_seconds=round(seconds + extent, 6),
            onset=Boundary(seconds, "estimated", uncertainty,
                           "ffmpeg scene score on the source's frame timebase"
                           if frame else
                           "ffmpeg scene score; the source frame rate was "
                           "never measured, so the boundary's precision is "
                           "unknown"),
            intermediate_states=(),
            completion=Boundary(round(seconds + extent, 6),
                                "estimated", uncertainty,
                                "the transition's extent is one frame"
                                if frame else
                                "the source frame rate was never measured, "
                                "so the transition has no measurable extent"),
            subjects=(),
            subject_labels=(),
            objects=(),
            object_labels=(),
            evidence_ids=(f"scene_boundary:{clip_id}:{number:03d}",),
            status="verified",
            certainty="measured",
            priority=priority,
            producer="temporal_index",
            basis=(f"ffmpeg scene score {score}" if score is not None
                   else "ffmpeg scene boundary"),
        ))

    sound_events = index_doc.get("sound_events") or []
    sound_priority, _ = event_priority("audio_event")
    for number, row in enumerate(sound_events):
        if not isinstance(row, Mapping):
            continue
        start = row.get("start")
        end = row.get("end")
        if start is None or end is None:
            continue
        try:
            start_s, end_s = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if end_s <= start_s:
            continue
        label = str(row.get("label") or "")
        out.append(TemporalEvent(
            event_id=f"{clip_id}:audio_event:{number:03d}",
            event_type="audio_event",
            action=None,
            action_text=label,
            asset_id=asset_id,
            clip_id=clip_id,
            source_file=source_file,
            clock_id=clock_id,
            start_seconds=start_s,
            end_seconds=end_s,
            onset=Boundary(start_s, "estimated", None,
                           "the classifier's window; its own precision is "
                           "not recorded"),
            intermediate_states=(),
            completion=Boundary(end_s, "estimated", None,
                                 "the classifier's window; its own precision "
                                 "is not recorded"),
            subjects=(),
            subject_labels=(),
            objects=(),
            object_labels=(),
            evidence_ids=(f"sound_event:{clip_id}:{number:03d}",),
            status="verified",
            certainty="measured",
            priority=sound_priority,
            producer="temporal_index",
            basis=f"measured sound event {label!r}",
        ))

    speech_regions = index_doc.get("speech_regions") or []
    speech_priority, _ = event_priority("speech_start_end")
    for number, region in enumerate(speech_regions):
        if not isinstance(region, Mapping):
            continue
        start = region.get("start")
        end = region.get("end")
        if start is None or end is None:
            continue
        try:
            start_s, end_s = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if end_s <= start_s:
            continue
        text = str(region.get("text") or "")
        out.append(TemporalEvent(
            event_id=f"{clip_id}:speech_start_end:{number:03d}",
            event_type="speech_start_end",
            action=None,
            action_text=text,
            asset_id=asset_id,
            clip_id=clip_id,
            source_file=source_file,
            clock_id=clock_id,
            start_seconds=start_s,
            end_seconds=end_s,
            onset=Boundary(start_s, "estimated", None,
                           "the aligner's edge; its own precision is not "
                           "recorded"),
            intermediate_states=(),
            completion=Boundary(end_s, "estimated", None,
                                 "the aligner's edge; its own precision is "
                                 "not recorded"),
            subjects=(),
            subject_labels=(),
            objects=(),
            object_labels=(),
            evidence_ids=(f"speech_region:{clip_id}:{number:03d}",),
            status="verified",
            certainty="measured",
            priority=speech_priority,
            producer="temporal_index",
            basis="aligned speech region",
        ))
    return out


def _events_from_vision_profile(
        clip_id: str, clip: Mapping[str, Any],
        vision_doc: Mapping[str, Any],
        index_doc: Mapping[str, Any] | None) -> list[TemporalEvent]:
    """The INFERRED events a clip's 1.03 vision profile carries.

    The vision model reads a window and writes prose about it.  That is
    an INFERENCE, so these events say so: certainty ``inferred``, an
    estimated boundary, and no verified status.  An action the
    vocabulary cannot name keeps ``action=None`` and says why.
    """
    out: list[TemporalEvent] = []
    asset_id = _asset_id(clip_id)
    clock_id = _clock_id(clip_id)
    source_file = clip.get("source_file") or clip.get("path")
    priority, _ = event_priority("action_state_change")

    objects = [row for row in (vision_doc.get("objects") or [])
               if isinstance(row, Mapping)]
    subjects_by_label: dict[str, list[float]] = {}
    for row in objects:
        label = str(row.get("label") or "")
        role = str(row.get("role") or "")
        if role != "primary_subject" or not label:
            continue
        for span in row.get("appearances") or []:
            try:
                subjects_by_label.setdefault(label.lower(), []).append(
                    float(span[0]))
            except (TypeError, ValueError, IndexError):
                continue

    number = 0
    for window in vision_doc.get("actions") or []:
        if not isinstance(window, Mapping):
            continue
        for action in window.get("actions") or []:
            if not isinstance(action, Mapping):
                continue
            described = str(action.get("action") or "").strip()
            body = str(action.get("body_language") or "").strip()
            text = " ".join(part for part in (described, body) if part
                            and part != "None")
            span = window.get("window") or [action.get("start"),
                                            action.get("end")]
            start = action.get("start", span[0] if span else 0.0)
            end = action.get("end", span[1] if span else 0.0)
            try:
                start_s = float(start if start is not None else 0.0)
                end_s = float(end if end is not None else start_s)
            except (TypeError, ValueError):
                continue
            if end_s < start_s:
                continue
            action_class, action_basis = classify_action(text)
            subjects = tuple(sorted(
                label for label, starts in subjects_by_label.items()
                if any(start <= start_s <= end_s for start in starts)))
            out.append(TemporalEvent(
                event_id=f"{clip_id}:action_state_change:{number:03d}",
                event_type="action_state_change",
                action=action_class,
                action_text=text,
                asset_id=asset_id,
                clip_id=clip_id,
                source_file=source_file,
                clock_id=clock_id,
                start_seconds=start_s,
                end_seconds=end_s,
                onset=Boundary(start_s, "estimated", None,
                               "the vision model's action window; its own "
                               "boundary precision is not recorded"),
                intermediate_states=read_intermediate_states(
                    index_doc, start_s, end_s),
                completion=Boundary(end_s, "estimated", None,
                                     "the vision model's action window; its "
                                     "own boundary precision is not recorded"),
                subjects=(),
                subject_labels=subjects,
                objects=(),
                object_labels=(),
                evidence_ids=(f"vision_action:{clip_id}:{number:03d}",),
                status="candidate",
                certainty="inferred",
                priority=priority,
                producer="vision_model",
                basis=f"vision model action; action class from {action_basis}",
            ))
            out[-1] = replace(
                out[-1],
                motion=read_motion(index_doc, start_s, end_s))
            number += 1
    return out


def _events_from_ocr(clip_id: str, clip: Mapping[str, Any],
                      ocr_doc: Mapping[str, Any]) -> list[TemporalEvent]:
    """The MEASURED on-screen text changes a clip's OCR carries.

    A text track that appears, changes or disappears is an edit
    critical event: the moment the caption on screen changed.
    """
    out: list[TemporalEvent] = []
    asset_id = _asset_id(clip_id)
    clock_id = _clock_id(clip_id)
    source_file = clip.get("source_file") or clip.get("path")
    priority, _ = event_priority("onscreen_text_change")
    detections = ocr_doc.get("detections") or []
    spans: list[tuple[float, float, str, str]] = []
    for detection in detections:
        if not isinstance(detection, Mapping):
            continue
        track_id = str(detection.get("track_id") or "")
        text = str(detection.get("text") or "")
        intervals = (detection.get("appearance_intervals")
                     or detection.get("appearances") or [])
        for interval in intervals:
            try:
                if isinstance(interval, Mapping):
                    start = float(interval["start_seconds"])
                    end = float(interval["end_seconds"])
                else:
                    start = float(interval[0])
                    end = float(interval[1])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if end <= start:
                continue
            spans.append((start, end, track_id, text))
    spans.sort(key=lambda row: (row[0], row[1], row[2]))
    for number, (start, end, track_id, text) in enumerate(spans):
        out.append(TemporalEvent(
            event_id=f"{clip_id}:onscreen_text_change:{number:03d}",
            event_type="onscreen_text_change",
            action=None,
            action_text=text,
            asset_id=asset_id,
            clip_id=clip_id,
            source_file=source_file,
            clock_id=clock_id,
            start_seconds=start,
            end_seconds=end,
            onset=Boundary(start, "estimated", None,
                           "the OCR track's appearance edge; its own "
                           "precision is not recorded"),
            intermediate_states=(),
            completion=Boundary(end, "estimated", None,
                                 "the OCR track's appearance edge; its own "
                                 "precision is not recorded"),
            subjects=(),
            subject_labels=(),
            objects=(),
            object_labels=(),
            evidence_ids=(f"ocr_track:{clip_id}:{track_id}",),
            status="verified",
            certainty="measured",
            priority=priority,
            producer="ocr",
            basis=f"tracked on-screen text {text!r}",
        ))
    return out


# ── The Project Model adapter ──────────────────────────────────────────

MODEL_EVENT_KINDS: dict[str, tuple[str, str | None]] = {
    "hand_near_mouth": ("action_state_change", "hand_to_mouth"),
    "hand_raise": ("action_state_change", "hand_raise"),
    "nod": ("action_state_change", "nodding"),
    "object_pickup": ("action_state_change", "pick_up"),
    "person_enters": ("action_state_change", "person_enters"),
    "person_leaves": ("action_state_change", "person_leaves"),
    "pointing": ("action_state_change", "pointing"),
    "hand_object_contact": ("object_interaction", "contact"),
    "hand_to_face": ("action_state_change", "hand_to_face"),
    "pick_up": ("action_state_change", "pick_up"),
    "drink_from_cup": ("action_state_change", "drink"),
    "object_position_change": ("action_state_change", "position_change"),
    "speaking": ("speech_start_end", None),
}
"""A Project Model event's ``kind``, and the typed event it becomes.

A kind this table does not name is REPORTED, not guessed into a type:
the index says which kinds it could not type, and the caller says so.
"""

TICKS_PER_SECOND = 1_000_000


def _model_boundary(row: Mapping[str, Any], name: str, seconds: float,
                    rate: float) -> Boundary:
    """One edge of a Project Model event, with the model's own quality.

    The model records a boundary's status and its uncertainty radius;
    an "exact" edge carries no radius, so a radius on one is dropped
    rather than kept as a contradiction.
    """
    block = row.get(name)
    if not isinstance(block, Mapping):
        return Boundary(seconds, "estimated", None,
                        "the model records no boundary quality here")
    status = str(block.get("status") or "estimated")
    ticks = block.get("uncertainty_ticks")
    uncertainty = (float(ticks) / rate
                   if isinstance(ticks, (int, float)) else None)
    if status == "exact" and uncertainty:
        uncertainty = None
    return Boundary(seconds, status, uncertainty,
                    "the Project Model's own boundary quality")


def _model_events(model: Mapping[str, Any]) -> tuple[list[TemporalEvent],
                                                   list[str]]:
    """The typed events a Project Model carries, and the kinds it could not.

    The Project Model is the canonical typed representation: its
    events name their participants, their evidence and their boundary
    quality.  Where it is present, its events are the strongest
    statement Ren has about what happened.
    """
    clocks = {row["id"]: row for row in model.get("clocks") or []
              if isinstance(row, Mapping) and row.get("id")}
    assets = {row["id"]: row for row in model.get("assets") or []
              if isinstance(row, Mapping) and row.get("id")}
    entities = {row["id"]: row for row in model.get("entities") or []
                if isinstance(row, Mapping) and row.get("id")}

    entity_shots: dict[str, set[str]] = {}
    for track in model.get("tracks") or []:
        if not isinstance(track, Mapping):
            continue
        entity_id = track.get("entity_id")
        clock_id = track.get("clock_id")
        if not entity_id or not clock_id:
            continue
        entity_shots.setdefault(entity_id, set()).add(clock_id)

    events: list[TemporalEvent] = []
    unmapped: list[str] = []
    for row in model.get("events") or []:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "")
        typed = MODEL_EVENT_KINDS.get(kind)
        if typed is None:
            if kind:
                unmapped.append(kind)
            continue
        event_type, action = typed
        interval = row.get("interval") or {}
        clock_id = str(interval.get("clock_id") or "")
        clock = clocks.get(clock_id) or {}
        rate = float(clock.get("ticks_per_second") or TICKS_PER_SECOND)
        if rate <= 0:
            rate = float(TICKS_PER_SECOND)
        start = float(interval.get("start") or 0) / rate
        end = float(interval.get("end") or 0) / rate
        if end < start:
            continue
        asset_id = str((assets.get(clock.get("domain", {}).get("asset_id")
                                  if isinstance(clock.get("domain"), Mapping)
                                  else "") or {}).get("id") or "")
        participants = [pid for pid in (row.get("participant_ids") or [])
                        if pid in entities]
        subject_ids = tuple(pid for pid in participants
                            if entities[pid].get("kind") in ("person", "speaker"))
        object_ids = tuple(pid for pid in participants
                           if entities[pid].get("kind") == "object")
        subject_labels = tuple(str(entities[pid].get("label") or "")
                                for pid in subject_ids)
        object_labels = tuple(str(entities[pid].get("label") or "")
                              for pid in object_ids)

        priority, _ = event_priority(event_type)
        state_changes = tuple(
            StateChange(entity_id=str(change.get("entity_id") or ""),
                        property=str(change.get("property") or ""),
                        before=change.get("from"),
                        after=change.get("to"),
                        evidence_ids=tuple(change.get("evidence_ids") or ()))
            for change in (row.get("state_changes") or [])
            if isinstance(change, Mapping))

        events.append(TemporalEvent(
            event_id=str(row.get("id") or ""),
            event_type=event_type,
            action=action,
            action_text=kind,
            asset_id=asset_id,
            clip_id=None,
            source_file=None,
            clock_id=clock_id,
            start_seconds=start,
            end_seconds=end,
            onset=_model_boundary(row, "start_boundary", start, rate),
            intermediate_states=(),
            completion=_model_boundary(row, "end_boundary", end, rate),
            subjects=subject_ids,
            subject_labels=subject_labels,
            objects=object_ids,
            object_labels=object_labels,
            evidence_ids=tuple(row.get("evidence_ids") or ()),
            status=str(row.get("status") or "candidate"),
            certainty=str(row.get("certainty") or "inferred"),
            continuity=(read_continuity(entity_shots, subject_ids[0])
                        if subject_ids else
                        read_continuity(entity_shots, object_ids[0])
                        if object_ids else
                        Continuity("untracked", (),
                                   "the event names no tracked entity")),
            motion=MotionReading("unmeasured", None, 0,
                                 "the Project Model records no motion here"),
            state_changes=state_changes,
            priority=priority,
            producer="project_model",
            basis=f"Project Model event kind {kind!r}",
        ))
    return events, sorted(set(unmapped))


# ── The index builder ──────────────────────────────────────────────────


def _load_ocr_documents(project_folder: str,
                        catalog: Sequence[Mapping[str, Any]]) -> dict:
    out: dict[str, dict] = {}
    root = (Path(project_folder) / "pipeline_output" / "steps"
            / "1_07_ocr_extraction")
    if not root.is_dir():
        return out
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        path = root / str(clip_id) / "ocr_result.json"
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                doc = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            out[clip_id] = doc
    return out


def load_project_model(project_folder: str) -> dict | None:
    """The project's Project Model snapshot, or ``None`` when it has none.

    A project that never ran the evidence migration has no Project
    Model.  That is not a failure: the pipeline's own step outputs
    still carry measured events.  The store is only opened when the
    project already HAS one, so reading the index never creates state
    in a project that does not.
    """
    root = Path(project_folder) / "project_model"
    if not root.is_dir():
        return None
    try:
        from library.tools import project_model_store
    except ImportError:
        return None
    try:
        _revision, model = project_model_store.load_project_model(
            Path(project_folder))
    except Exception:  # noqa: BLE001 - an unread model is no model
        return None
    return model if isinstance(model, dict) else None


def build_temporal_events(project_folder: str) -> TemporalEventIndex:
    """Every typed event a project holds.

    Reads the project's OWN step outputs - the 1.04 temporal index, the
    1.03 vision profiles, the 1.07 OCR tracks - and, when the project
    carries a Project Model, its typed events too.  Nothing is written:
    the index is derived, so it cannot go stale against the footage.
    """
    from library.tools.analysis import footage_segments

    catalog = footage_segments.load_catalog(project_folder)
    temporal = footage_segments.load_temporal_index(project_folder)
    vision = footage_segments.load_vision_profiles(project_folder, catalog)
    ocr = _load_ocr_documents(project_folder, catalog)

    events: list[TemporalEvent] = []
    sources: list[str] = ["step_1_03_semantic_analysis",
                          "step_1_04_temporal_index",
                          "step_1_07_ocr_extraction"]
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        index_doc = temporal.get(clip_id)
        if index_doc:
            events.extend(_events_from_temporal_index(clip_id, clip, index_doc))
        vision_doc = vision.get(clip_id)
        if vision_doc:
            events.extend(_events_from_vision_profile(
                clip_id, clip, vision_doc, index_doc))
        ocr_doc = ocr.get(clip_id)
        if ocr_doc:
            events.extend(_events_from_ocr(clip_id, clip, ocr_doc))

    unmapped: list[str] = []
    model = load_project_model(project_folder)
    if model:
        sources.append("project_model")
        model_events, unmapped = _model_events(model)
        events.extend(model_events)

    durations: dict[str, float] = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        duration = clip.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0:
            durations[clip_id] = float(duration)

    return TemporalEventIndex(
        events=assign_occurrences(events),
        unmapped_kinds=tuple(unmapped),
        sources=tuple(sources),
        clip_durations=durations,
    )


# ── Verification (VI-017) ──────────────────────────────────────────────

REQUIRED_EVIDENCE_BY_TYPE: dict[str, tuple[str, ...]] = {
    "speech_start_end": ("speech.word",),
    "word_phrase_boundary": ("speech.word",),
    "speaker_change": ("person.voice_turn", "speech.word"),
    "action_state_change": ("event.candidate_span",),
    "object_interaction": ("object.detection", "object.visibility"),
    "scene_transition": ("video.scene_boundary",),
    "onscreen_text_change": ("image.ocr_text_track",),
    "audio_event": ("audio.sound_event",),
    "technical_defect": ("image.sharpness.laplacian_variance",),
    "retake_repeat": ("speech.word", "audio.sound_event"),
}
"""Which MEASUREMENT TYPES satisfy each edit-critical event type.

``edit_critical_events`` names the evidence each type needs; this names
the measurement type in Ren's evidence contract that carries it.  A
type whose evidence names no measurement type is reported as carrying
none, rather than being satisfied by an unrelated row.
"""

EVIDENCE_NAME_SUBSTITUTIONS: dict[str, dict[str, str]] = {
    "action_state_change": {"event_span": "event.candidate_span"},
    "onscreen_text_change": {"ocr.text_span": "image.ocr_text_track"},
}
"""The edit-critical evidence names that a REGISTERED measurement type
carries under another name.

``edit_critical_events`` names its evidence after the signal it wants;
the evidence contract names the same signal after the measurement that
produces it.  The two names are listed here so the substitution is a
declared fact rather than a silent mismatch.
"""

EVIDENCE_NAMES_WITHOUT_A_MEASUREMENT_TYPE: dict[str, tuple[str, ...]] = {
    "technical_defect": ("picture.soft_range", "camera.stability"),
}
"""The edit-critical evidence names NO measurement type carries.

Nothing in Ren's evidence contract measures a soft picture range or
camera stability, so an event of this type cannot be verified against
the requirement it was declared with.  The gap is NAMED here and
reported by :func:`verify_event`; it is not closed by substituting a
neighbouring measurement, which would verify a different claim.
"""


def verify_event(event: TemporalEvent,
                 available_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Whether an event is measured well enough to cut on (VI-017).

    ``available_evidence`` maps a measurement type to the rows that
    exist for the event's own clock.  The verdict is the edit-critical
    fidelity requirement applied to the event's boundary precision: a
    boundary located less precisely than the type tolerates is
    UNCERTAIN, never silently good enough.
    """
    requirement = edit_critical_events.fidelity_requirement(event.event_type)
    tolerance = float(requirement["temporal_tolerance_seconds"])
    reasons: list[str] = []

    for name in EVIDENCE_NAMES_WITHOUT_A_MEASUREMENT_TYPE.get(
            event.event_type, ()):
        reasons.append(f"no measurement type carries {name!r}")

    names = REQUIRED_EVIDENCE_BY_TYPE.get(event.event_type, ())
    if not names:
        reasons.append(
            f"no measurement type satisfies {event.event_type!r}")
    provided = set()
    for name in names:
        rows = available_evidence.get(name)
        if rows is None:
            rows = available_evidence.get(event.event_type)
        if isinstance(rows, bool):
            present = rows
        else:
            present = bool(rows)
        if present:
            provided.add(name)
        else:
            reasons.append(f"missing required evidence: {name}")

    worst = 0.0
    for boundary in (event.onset, event.completion, *event.intermediate_states):
        if boundary.uncertainty_seconds is None:
            continue
        worst = max(worst, float(boundary.uncertainty_seconds))
    if worst > tolerance:
        reasons.append(
            f"boundary precision {worst}s exceeds tolerance {tolerance}s")

    return {
        "status": "verified" if not reasons else "uncertain",
        "event_type": event.event_type,
        "event_id": event.event_id,
        "temporal_tolerance_seconds": tolerance,
        "boundary_precision_seconds": worst if worst else None,
        "evidence_provided": sorted(provided),
        "reasons": reasons,
    }


def evidence_types_on_clock(model: Mapping[str, Any],
                            clock_id: str) -> dict[str, int]:
    """The measurement types MEASURED on one clock, and how many.

    This is the evidence an event on that clock can be verified
    against.  The measurement type comes from the observation's own
    evidence contract, so a row that carries no contract contributes
    nothing rather than contributing a guess.
    """
    counts: dict[str, int] = {}
    for row in model.get("observations") or []:
        if not isinstance(row, Mapping):
            continue
        interval = row.get("interval") or {}
        if interval.get("clock_id") != clock_id:
            continue
        evidence = row.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        measurement_type = evidence.get("measurement_type")
        if not measurement_type:
            continue
        counts[str(measurement_type)] = counts.get(str(measurement_type), 0) + 1
    return counts


def verify_event_in_model(event: TemporalEvent,
                          model: Mapping[str, Any]) -> dict[str, Any]:
    """Verify an event against the evidence ITS OWN clock carries.

    This is the targeted half of VI-017: when a selected shot depends
    on an action being right, the check looks at the measurements on
    that shot's clock and at nothing else.  A clock with no such
    measurement reports the missing evidence rather than passing.
    """
    counts = evidence_types_on_clock(model, event.clock_id)
    available = {name: count for name, count in counts.items()}
    verdict = verify_event(event, available)
    verdict["evidence_on_clock"] = counts
    return verdict


def main(argv: list[str] | None = None) -> int:
    """Print a project's typed temporal events as JSON."""
    import argparse

    parser = argparse.ArgumentParser(
        description="List a project's typed temporal events")
    parser.add_argument("project")
    parser.add_argument("--clip", default=None,
                        help="only this clip's events")
    parser.add_argument("--type", dest="event_type", default=None,
                        help="only this edit-critical event type")
    parser.add_argument("--action", default=None,
                        help="only this action class")
    parser.add_argument("--max-priority", type=int, default=None,
                        help="only events at or above this priority")
    args = parser.parse_args(argv)

    index = build_temporal_events(args.project)
    events = index.events
    if args.clip:
        events = tuple(event for event in events if event.clip_id == args.clip)
    if args.event_type:
        events = tuple(event for event in events
                       if event.event_type == args.event_type)
    if args.action:
        events = tuple(event for event in events if event.action == args.action)
    if args.max_priority is not None:
        events = tuple(event for event in events
                       if event.priority <= args.max_priority)
    print(json.dumps({
        "project": str(args.project),
        "count": len(events),
        "unmapped_kinds": list(index.unmapped_kinds),
        "sources": list(index.sources),
        "events": [event.to_dict() for event in events],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

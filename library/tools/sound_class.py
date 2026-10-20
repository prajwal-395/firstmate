"""The sound classes an editor cuts on, and what a clip measured of each.

What this is
------------
`library/tools/sound_events.py` reads the measured non-speech events
and knows their shape. It does not know what an editor means by
"applause" or "a music change", and it cannot answer the question that
matters most: **is there any here?**

That question has two answers and they are not the same. A clip whose
sound events were MEASURED and found no laughter is a MEASURED ABSENCE
of laughter. A clip whose sound events were never measured has NO
ANSWER about laughter. The reader that gets those two the same way
serves a clip that says "there is no laughter" for a clip nobody
listened to.

This module is the roster of the classes an editor cuts on, and the
reading of each class on a clip. The three states are:

``present`` - the clip measured an event of this class.

``absent`` - the clip's sound events were MEASURED over the whole clip
and nothing of this class reached the threshold. This is a measured
absence, and it is a finding.

``uncertain`` - the clip's sound events were not measured. Nothing is
claimed about this class, and the reason the measurement did not run
travels with the answer.

The roster
----------
The labels are the AudioSet vocabulary the producer
(`library/tools/analysis/sound_event_pipeline.py`) actually emits,
verbatim. A label no clip measured is never coined here: a label the
roster does not cover is still reported, under ``unclassified``.

A class may match its labels three ways, all deterministic: exactly, by
a suffix, or by a prefix. The music class uses the suffix and the
prefix because the vocabulary carries dozens of genre labels
("Pop music", "Music of Africa") and listing them one by one would be
a list that goes stale the moment the vocabulary grows.

Music changes
-------------
A music CHANGE is a transition, not a label: the music class starting
where there was none, or stopping where there is none. The music class
is measured as spans, so the changes are read off the spans. A music
span that runs to the end of the clip has no measured stop, and one
that starts at the first instant has no measured start, because the
recording does not say what was there before it.

The section change WITHIN a continuous music bed is a different
measurement and lives elsewhere: `library/tools/music_section.py` and
`library/tools/audio_interval_evidence.py` carry the structure.

``tests/unit/audio/test_sound_class.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from library.tools import sound_events

STATE_PRESENT = "present"
STATE_ABSENT = "absent"
STATE_UNCERTAIN = "uncertain"

CLASS_STATES = (STATE_PRESENT, STATE_ABSENT, STATE_UNCERTAIN)
"""Every state a sound class can be in on a clip."""

CHANGE_STARTED = "music_started"
CHANGE_STOPPED = "music_stopped"


@dataclass(frozen=True)
class SoundClass:
    """One class of non-speech sound, and the labels that carry it."""

    name: str
    summary: str
    labels: tuple[str, ...] = ()
    label_suffixes: tuple[str, ...] = ()
    label_prefixes: tuple[str, ...] = ()

    def matches(self, label: Any) -> bool:
        """Whether this class covers `label`."""
        if not isinstance(label, str) or not label:
            return False
        if label in self.labels:
            return True
        if any(label.endswith(suffix) for suffix in self.label_suffixes):
            return True
        return any(label.startswith(prefix) for prefix in self.label_prefixes)


CLASS_IMPACT = "impact"
CLASS_APPLAUSE = "applause"
CLASS_LAUGHTER = "laughter"
CLASS_MUSIC = "music"
CLASS_ENVIRONMENTAL = "environmental"

SOUND_CLASSES = (
    SoundClass(
        name=CLASS_IMPACT,
        summary="a physical collision or impulse: a slam, a bang, a "
                "crash, a break, a thud",
        labels=("Slam", "Bang", "Smash, crash", "Breaking", "Crunch",
                "Thump, thud", "Explosion", "Gunshot, gunfire",
                "Fireworks", "Firecracker", "Squish", "Gush"),
    ),
    SoundClass(
        name=CLASS_APPLAUSE,
        summary="hands clapping, and the crowd that answers them",
        labels=("Applause", "Clapping"),
    ),
    SoundClass(
        name=CLASS_LAUGHTER,
        summary="laughter, in every size",
        labels=("Laughter", "Baby laughter", "Giggle", "Belly laugh",
                "Chuckle, chortle"),
    ),
    SoundClass(
        name=CLASS_MUSIC,
        summary="a music bed, whatever kind it is",
        labels=("Music", "Musical instrument", "Jingle (music)"),
        label_suffixes=(" music",),
        label_prefixes=("Music of ",),
    ),
    SoundClass(
        name=CLASS_ENVIRONMENTAL,
        summary="the sound of the place: weather, water, wildlife, "
                "traffic, and the room around the speakers",
        labels=(
            "Wind", "Wind noise (microphone)", "Rain", "Raindrop",
            "Rain on surface", "Thunderstorm", "Thunder", "Fire",
            "Water", "Stream", "Waterfall", "Ocean", "Waves, surf",
            "Bird", "Bird vocalization, bird call, bird song", "Insect",
            "Cricket", "Frog", "Animal", "Dog", "Cat",
            "Traffic noise, roadway noise", "Motor vehicle (road)",
            "Vehicle", "Car", "Car passing by", "Truck", "Bus", "Train",
            "Aircraft", "Aircraft engine", "Jet engine", "Engine",
            "Tools", "Drill", "Jackhammer", "Vacuum cleaner",
            "Crowd", "Children playing",
            "Hubbub, speech noise, speech babble",
            "Door", "Doorbell", "Sliding door", "Dishes, pots, and pans",
            "Sink (filling or washing)", "Water tap, faucet",
            "Toilet flush", "Microwave oven", "Sewing machine", "Typing",
            "Computer keyboard"),
    ),
)
"""The classes an editor cuts on. One enumeration, spelled once.

The labels are the producer's own vocabulary, verbatim. A label the
roster does not cover is still measured and still reported, under
``unclassified``: the roster is a reading, not a filter."""

CLASS_NAMES = tuple(entry.name for entry in SOUND_CLASSES)
"""The class names, in roster order."""


def class_of(label: Any) -> str | None:
    """The class that covers `label`, or None when none does."""
    for entry in SOUND_CLASSES:
        if entry.matches(label):
            return entry.name
    return None


def _summary_reason(summary: Any) -> str | None:
    """Why the clip's sound events were not measured, or None."""
    if not isinstance(summary, dict):
        return "no measurement"
    method = summary.get("sound_event_method")
    if isinstance(method, str) and method:
        if method.startswith(sound_events.UNMEASURED_PREFIX):
            return method
        return None
    return "the clip carries no sound-event method"


def clip_duration(summary: Any) -> float | None:
    """The clip's duration in seconds, when the summary carries one."""
    if not isinstance(summary, dict):
        return None
    for key in ("duration", "duration_seconds"):
        value = summary.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def class_reading(summary: Any, *, source_start: float | None = None,
                  source_end: float | None = None) -> dict:
    """What this clip measured of each sound class.

    The three states are `present`, `absent` (measured, and nothing
    reached the threshold) and `uncertain` (the measurement did not
    run, and the reason travels with the answer).

    `source_start` and `source_end` narrow the question to a span of
    the clip. The absence is still a MEASURED absence: the measurement
    covered the whole clip, and therefore covered the span.
    """
    measured = sound_events.measured(summary)
    reason = None if measured else _summary_reason(summary)
    if not measured:
        events = []
    elif source_start is not None and source_end is not None:
        events = sound_events.events_in_block(summary, float(source_start),
                                              float(source_end))
    else:
        events = sound_events.events(summary)

    classes: dict[str, dict] = {}
    unclassified: list[dict] = []
    for entry in SOUND_CLASSES:
        rows = [event for event in events
                if class_of(event["label"]) == entry.name]
        if not measured:
            classes[entry.name] = {
                "state": STATE_UNCERTAIN,
                "summary": entry.summary,
                "events": [],
                "labels": [],
                "seconds": 0.0,
            }
            continue
        classes[entry.name] = {
            "state": STATE_PRESENT if rows else STATE_ABSENT,
            "summary": entry.summary,
            "events": rows,
            "labels": sorted({row["label"] for row in rows}),
            "seconds": round(sum(row["end_seconds"] - row["start_seconds"]
                                 for row in rows), 3),
        }
    for event in events:
        if class_of(event["label"]) is None:
            unclassified.append(event)

    return {
        "measured": measured,
        "method": (summary or {}).get("sound_event_method")
        if isinstance(summary, dict) else None,
        "reason": reason,
        "classes": classes,
        "unclassified": {
            "labels": sorted({row["label"] for row in unclassified}),
            "events": len(unclassified),
        },
    }


def _music_spans(events: Sequence[dict]) -> list[dict]:
    """The music class's spans, merged where they touch."""
    spans: list[dict] = []
    for event in events:
        if class_of(event["label"]) != CLASS_MUSIC:
            continue
        start = event["start_seconds"]
        end = event["end_seconds"]
        if spans and start <= spans[-1]["end"]:
            spans[-1]["end"] = max(spans[-1]["end"], end)
            spans[-1]["labels"].append(event["label"])
            continue
        spans.append({"start": start, "end": end, "labels": [event["label"]]})
    return spans


def music_changes(summary: Any) -> list[dict]:
    """The measured transitions of the music class.

    A change is the music class starting where there was none, or
    stopping where there is none. A span that runs to the end of the
    clip has no measured stop; a span that starts at the first instant
    has no measured start, because the recording does not say what was
    there before it.

    Nothing is returned for a clip whose sound events were never
    measured: that is the absence of an answer, and it is not the same
    as "the music never changed".
    """
    if not sound_events.measured(summary):
        return []
    spans = _music_spans(sound_events.events(summary))
    if not spans:
        return []
    duration = clip_duration(summary)
    changes: list[dict] = []
    for index, span in enumerate(spans):
        if index == 0 and span["start"] > 0.0:
            changes.append({
                "kind": CHANGE_STARTED,
                "at_seconds": round(span["start"], 3),
                "from_seconds": None,
                "to_seconds": round(span["end"], 3),
                "labels": sorted(set(span["labels"])),
            })
        if index + 1 < len(spans):
            changes.append({
                "kind": CHANGE_STOPPED,
                "at_seconds": round(span["end"], 3),
                "from_seconds": round(span["start"], 3),
                "to_seconds": None,
                "labels": sorted(set(span["labels"])),
            })
            changes.append({
                "kind": CHANGE_STARTED,
                "at_seconds": round(spans[index + 1]["start"], 3),
                "from_seconds": None,
                "to_seconds": round(spans[index + 1]["end"], 3),
                "labels": sorted(set(spans[index + 1]["labels"])),
            })
    last = spans[-1]
    if duration is not None and last["end"] < duration:
        changes.append({
            "kind": CHANGE_STOPPED,
            "at_seconds": round(last["end"], 3),
            "from_seconds": round(last["start"], 3),
            "to_seconds": None,
            "labels": sorted(set(last["labels"])),
        })
    changes.sort(key=lambda row: (row["at_seconds"], row["kind"]))
    return changes

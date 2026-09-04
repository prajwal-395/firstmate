"""A rough cut that already exists, read off a LIVE Resolve timeline.

The gap this closes
-------------------
`library/tools/external_inputs.py` made it possible to satisfy a
prerequisite from outside the pipeline by SUPPLYING a value that is then
CHECKED.  What it lacked was a producer: the captain's rough cut lives on
a Resolve timeline, and nothing turned that timeline into a value.  Its
`WITHDRAWN` entry said so, and gave the reason:

    "reading it means copying that database and opening it as SQLite
    (AGENTS.md section 5), and nothing maps its clips back onto a typed
    pipeline key"

**That is true of a CLOSED project and false of a LIVE one, and the
distinction is the whole of this module.**  A closed project really is a
database on disk that has to be copied before it is opened and really
does lack a typed mapping.  A live one answers, per timeline item:

    GetMediaPoolItem().GetClipProperty("File Path")  absolute source path
    GetSourceStartTime() / GetSourceEndTime()        the range inside it
    GetStart() / GetEnd()                            where it lands
    GetUniqueId()                                    a stable handle

which is every field `_check_a_roll_assignments` already demands.  So the
contract does not weaken to accept a hand-built cut; it gains a producer,
and the artifact this module writes goes through `verify()` like any
other.

Measured on the GEO Podcast field test, 2026-09-04: a 44.3-minute
two-speaker timeline, 167 clips across two picture tracks, 7 source
files, every path resolving on disk.

Why seconds and not frames
--------------------------
`GetSourceStartTime()`/`GetSourceEndTime()` are used, never
`GetLeftOffset()`.  Two reasons, both measured:

- **They disagree.**  On the field-test timeline `GetLeftOffset()` and
  `GetSourceStartFrame()` differ by exactly one frame on roughly a third
  of items (1960 vs 1959, 5361 vs 5360, 117 vs 116) and agree on the
  rest.  A frame of drift on a third of the clips is a transcript that
  does not line up with its own audio.  `GetSourceStartTime()` is
  consistent with `GetSourceStartFrame()`, so the time pair is the one
  that agrees with itself.
- **Seconds sidestep the audio frame rate.**  AGENTS.md section 5:
  "Resolve audio pool items report 24fps regardless of the timeline."
  A frame number read off an audio item therefore means something
  different from the same number on a video item.  A time in seconds
  means the same thing on both.

Speaker is a MEASUREMENT, not a guess
-------------------------------------
Where an edit gives each speaker their own track, speaker identity is a
fact of the edit rather than something to infer, and reading it off the
track is stronger than diarization: it cannot misattribute.  This module
takes the speaker from the TRACK NAME and nothing else.  A project may
map track names to display names in `project.yaml`; absent a map the
track name is used verbatim.  Nothing here invents a speaker for a track
that does not name one - such a clip carries `speaker: None`, which
downstream reads as "this edit does not say".

What this module does NOT do
----------------------------
It never writes to Resolve.  Every call it makes is a getter.  A caller
that wants to build something drives `resolve_build_timeline` as usual;
this side only reads.

    python3 -m library.tools.timeline_ingest <project_folder> --write

`tests/test_timeline_ingest.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


class TimelineIngestError(RuntimeError):
    """Reading the timeline could not be completed honestly."""


# ── The records ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimelineClip:
    """One clip on one track, with its ground truth in the raw footage."""

    resolve_item_id: str
    """`GetUniqueId()`. Stable across reads, so a specific clip can be
    re-indexed later by NAME rather than by position - which is what
    makes re-indexing one portion of a cut possible at all."""

    track_type: str
    track_index: int
    track_name: str
    speaker: Optional[str]

    source_file: str
    """Absolute path to the raw footage this clip plays."""

    source_in: float
    source_out: float
    """The range inside `source_file`, in seconds. Ground truth."""

    timeline_start: float
    timeline_end: float
    """Where it lands on the timeline, in seconds."""

    name: str

    @property
    def duration(self) -> float:
        return self.timeline_end - self.timeline_start


@dataclass(frozen=True)
class TimelineSnapshot:
    """Everything read off one timeline in one pass."""

    project_name: str
    timeline_name: str

    fps: float
    """The rate Resolve COMPUTES with - exact, so positions and source
    times agree. See `exact_frame_rate`."""

    reported_fps: float
    """The rate Resolve REPORTS, kept so a reader can see both and the
    difference is never mistaken for a bug in this module."""

    width: int
    height: int
    start_frame: int
    end_frame: int
    clips: Tuple[TimelineClip, ...]

    @property
    def duration(self) -> float:
        return (self.end_frame - self.start_frame) / self.fps if self.fps else 0.0

    def picture_clips(self) -> List[TimelineClip]:
        return [c for c in self.clips if c.track_type == "video"]

    def speakers(self) -> List[str]:
        seen = []
        for c in self.clips:
            if c.speaker and c.speaker not in seen:
                seen.append(c.speaker)
        return seen


# ── The frame rate Resolve actually computes with ────────────────────

NTSC_RATES = {
    23.976: 24000 / 1001,
    29.97: 30000 / 1001,
    47.952: 48000 / 1001,
    59.94: 60000 / 1001,
    119.88: 120000 / 1001,
}
"""Reported rate -> the exact rational rate Resolve computes with.

Measured on the field test, 2026-09-04, and this is not cosmetic.
`GetSetting('timelineFrameRate')` returns the ROUNDED display rate
`23.976`, while `GetSourceStartTime()` is computed at the exact
`24000/1001`.  For item 0 of the field-test timeline:

    GetSourceStartFrame()          3151
    3151 / 23.976                  131.42308975642308   <- wrong
    3151 * 1001 / 24000            131.42295833333333
    GetSourceStartTime() returned  131.42295833333333   <- Resolve

So a timeline position derived from the reported rate sits on a
different clock from the source times read off the same item.  The
disagreement is small - 2.7ms over the full 63694-frame timeline - and
it is exactly the kind of small that survives review and then does not
line up.  Both numbers in a record must come off one clock.
"""


def exact_frame_rate(reported: float) -> float:
    """The rate Resolve computes with, given the rate it reports.

    A rate that is not an NTSC fractional one is returned unchanged:
    24, 25, 30, 50 and 60 are exact already.
    """
    return NTSC_RATES.get(round(float(reported), 3), float(reported))


# ── The naming trap ──────────────────────────────────────────────────

def resolve_project_exactly(project_manager, name: str):
    """The project named EXACTLY `name`, or a refusal saying why.

    Measured on the field test, 2026-09-04, and the reason this is a
    function rather than a line of code at the call site: the captain's
    expendable copy is `Podcast (field test)` and its INTERNAL
    `SM_Project.ProjectName` is `Podcast (Copy)`, while the untouchable
    original sitting beside it in the same folder is called `Podcast`.

    So a match on the internal name finds neither, and any `startswith`,
    `in` or "closest name" match on `"Podcast"` finds the ORIGINAL.  The
    only safe address is the exact string as Resolve's own project list
    reports it, and the only safe response to its absence is to refuse.
    """
    listed = project_manager.GetProjectListInCurrentFolder() or []
    if name in listed:
        current = project_manager.GetCurrentProject()
        if current is not None and current.GetName() == name:
            return current
        open_name = current.GetName() if current else None
        raise TimelineIngestError(
            f"{name!r} is in the current folder but is not the open "
            f"project (open: {open_name!r}). This module never opens a "
            f"project - opening one is a write to the captain's "
            f"session. Open it in Resolve and re-run.")

    near = [other for other in listed
            if name.lower() in other.lower() or other.lower() in name.lower()]
    hint = (f" Names that merely RESEMBLE it, which is exactly what must "
            f"not be substituted: {near}." if near else "")
    raise TimelineIngestError(
        f"No project named exactly {name!r} in the current folder. "
        f"Resolve lists {listed}.{hint} Refusing rather than guessing: a "
        f"near match here lands on somebody else's project.")


def timeline_named(project, name: str):
    """The timeline named exactly `name` on `project`, or a refusal."""
    found = []
    for i in range(1, (project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(i)
        if timeline is None:
            continue
        found.append(timeline.GetName())
        if timeline.GetName() == name:
            return timeline
    raise TimelineIngestError(
        f"No timeline named exactly {name!r} on project "
        f"{project.GetName()!r}. It has: {found}.")


# ── Reading ──────────────────────────────────────────────────────────

def _setting_int(timeline, key: str, label: str) -> int:
    raw = timeline.GetSetting(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise TimelineIngestError(
            f"timeline setting {key!r} read back as {raw!r}, which is not "
            f"a {label}. Judge a Resolve call by what it RETURNS "
            f"(AGENTS.md section 5).")


def snapshot_timeline(timeline, project_name: str,
                      speaker_map: Optional[Mapping[str, str]] = None,
                      ) -> TimelineSnapshot:
    """Read one live timeline. Every call here is a getter.

    `speaker_map` maps a TRACK NAME to a speaker label.  A track absent
    from the map keeps its own name; a track with no name at all yields
    `speaker: None` rather than an invented label.
    """
    speaker_map = dict(speaker_map or {})

    try:
        reported_fps = float(timeline.GetSetting("timelineFrameRate"))
    except (TypeError, ValueError):
        raise TimelineIngestError(
            f"timelineFrameRate read back as "
            f"{timeline.GetSetting('timelineFrameRate')!r}, not a number.")
    if reported_fps <= 0:
        raise TimelineIngestError(
            f"timelineFrameRate is {reported_fps}, not a rate.")
    # Resolve REPORTS 23.976 and COMPUTES at 24000/1001. Timeline
    # positions have to come off the same clock as the source times read
    # off the same items, or the two disagree. See NTSC_RATES.
    fps = exact_frame_rate(reported_fps)

    clips: List[TimelineClip] = []
    for track_type in ("video", "audio"):
        count = timeline.GetTrackCount(track_type) or 0
        for index in range(1, count + 1):
            track_name = timeline.GetTrackName(track_type, index) or ""
            speaker = speaker_map.get(track_name) or (track_name or None)
            for item in (timeline.GetItemListInTrack(track_type, index) or []):
                pool_item = item.GetMediaPoolItem()
                if pool_item is None:
                    # A generator, a title or a compound clip: real, but
                    # it plays no raw footage, so it has no ground truth
                    # to record.  Skipped deliberately and counted by the
                    # caller through the item totals, never invented.
                    continue
                source_file = pool_item.GetClipProperty("File Path") or ""
                if not source_file:
                    continue
                clips.append(TimelineClip(
                    resolve_item_id=item.GetUniqueId(),
                    track_type=track_type,
                    track_index=index,
                    track_name=track_name,
                    speaker=speaker,
                    source_file=source_file,
                    source_in=float(item.GetSourceStartTime()),
                    source_out=float(item.GetSourceEndTime()),
                    timeline_start=item.GetStart() / fps,
                    timeline_end=item.GetEnd() / fps,
                    name=item.GetName() or "",
                ))

    # Video first, then audio: a reader of this snapshot is reading the
    # cut, and the picture is the cut. Within a kind, by track then by
    # position on the timeline.
    clips.sort(key=lambda c: (0 if c.track_type == "video" else 1,
                              c.track_index, c.timeline_start))

    return TimelineSnapshot(
        project_name=project_name,
        timeline_name=timeline.GetName(),
        fps=fps,
        reported_fps=reported_fps,
        width=_setting_int(timeline, "timelineResolutionWidth", "width"),
        height=_setting_int(timeline, "timelineResolutionHeight", "height"),
        start_frame=timeline.GetStartFrame(),
        end_frame=timeline.GetEndFrame(),
        clips=tuple(clips),
    )


# ── Turning a snapshot into checkable pipeline state ─────────────────

def to_a_roll_assignments(snapshot: TimelineSnapshot) -> List[dict]:
    """The picture cut, in the shape `_check_a_roll_assignments` demands.

    Field for field: `source_file` absolute and on disk, `video_in` /
    `video_out` the range inside it, `timeline_start` / `timeline_end`
    where it lands.  The extra keys are additive - the check ignores what
    it does not name, and a reader that wants the ground truth back needs
    `resolve_item_id`.
    """
    out = []
    for clip in snapshot.picture_clips():
        out.append({
            "source_file": clip.source_file,
            "video_in": clip.source_in,
            "video_out": clip.source_out,
            "timeline_start": clip.timeline_start,
            "timeline_end": clip.timeline_end,
            "resolve_item_id": clip.resolve_item_id,
            "track_index": clip.track_index,
            "track_name": clip.track_name,
            "speaker": clip.speaker,
            "source_filename": os.path.basename(clip.source_file),
        })
    out.sort(key=lambda e: (e["timeline_start"], e["track_index"]))
    return out


def to_speech_sequence(snapshot: TimelineSnapshot) -> dict:
    """The cut's SPOKEN order, read off the timeline as a measurement.

    The captain's ruling (2026-09-04, Q9): a sequence they cut by hand is
    a fact to be read, not taste to be invented.  So every field here is
    a measurement of the timeline, and nothing is scored, ranked or
    selected.  What makes it checkable is that every claim it makes is
    about a file on disk and a range inside it - the same standard
    `_check_a_roll_assignments` meets.

    `segments` are in timeline order, which for a conversation cut IS the
    order the dialogue is spoken - the "one bit of dialogue comes after
    another" the captain asked to be able to read back.  A segment's
    `previous_segment_id`/`next_segment_id` make that order explicit
    rather than implied by list position, so a re-indexed subset still
    knows where it sat.
    """
    picture = sorted(snapshot.picture_clips(),
                     key=lambda c: (c.timeline_start, c.track_index))
    segments = []
    for order, clip in enumerate(picture):
        segments.append({
            "segment_id": clip.resolve_item_id,
            "order": order,
            "speaker": clip.speaker,
            "source_file": clip.source_file,
            "source_start": clip.source_in,
            "source_end": clip.source_out,
            "timeline_start": clip.timeline_start,
            "timeline_end": clip.timeline_end,
            "previous_segment_id": (picture[order - 1].resolve_item_id
                                    if order else None),
            "next_segment_id": (picture[order + 1].resolve_item_id
                                if order + 1 < len(picture) else None),
        })
    return {
        "derived_from": {
            "project": snapshot.project_name,
            "timeline": snapshot.timeline_name,
            "fps": snapshot.fps,
            "duration_seconds": snapshot.duration,
        },
        "measurement": (
            "Read off a live Resolve timeline: every segment's source "
            "file, source range and timeline position are what the "
            "timeline reports. Nothing was selected, scored or ordered "
            "by this pipeline."),
        "speakers": snapshot.speakers(),
        "segment_count": len(segments),
        "segments": segments,
    }


# ── Comparing two timelines ──────────────────────────────────────────

def structural_signature(snapshot: TimelineSnapshot) -> dict:
    """Everything that makes a timeline the cut that it is.

    Deliberately EXCLUDES `resolve_item_id`: duplicating a timeline
    assigns fresh item ids, so comparing them would report every copy as
    different and make the comparison useless for the one question it
    exists to answer.  Everything a viewer would see - which clip, from
    where, to where, on which named track, at what size and rate - is
    included.
    """
    return {
        "fps": snapshot.fps,
        "width": snapshot.width,
        "height": snapshot.height,
        "start_frame": snapshot.start_frame,
        "end_frame": snapshot.end_frame,
        "clip_count": len(snapshot.clips),
        "tracks": sorted({(c.track_type, c.track_index, c.track_name)
                          for c in snapshot.clips}),
        "clips": [
            (c.track_type, c.track_index, c.track_name, c.source_file,
             round(c.source_in, 6), round(c.source_out, 6),
             round(c.timeline_start, 6), round(c.timeline_end, 6))
            for c in sorted(snapshot.clips,
                            key=lambda c: (c.track_type, c.track_index,
                                           c.timeline_start))
        ],
    }


def compare_structure(left: TimelineSnapshot,
                      right: TimelineSnapshot) -> List[str]:
    """Every respect in which two timelines differ. Empty means duplicate.

    Returns DIFFERENCES rather than a boolean, because the decision this
    feeds - the captain's conditional authorisation to delete a duplicate
    - turns on being able to SHOW what was compared and what matched.
    A boolean cannot be evidence.
    """
    a, b = structural_signature(left), structural_signature(right)
    differences: List[str] = []

    for key in ("fps", "width", "height", "start_frame", "end_frame",
                "clip_count"):
        if a[key] != b[key]:
            differences.append(
                f"{key}: {left.timeline_name}={a[key]!r} "
                f"{right.timeline_name}={b[key]!r}")

    if a["tracks"] != b["tracks"]:
        only_left = [t for t in a["tracks"] if t not in b["tracks"]]
        only_right = [t for t in b["tracks"] if t not in a["tracks"]]
        differences.append(
            f"tracks differ: only in {left.timeline_name}={only_left}, "
            f"only in {right.timeline_name}={only_right}")

    if a["clips"] != b["clips"]:
        for index, (x, y) in enumerate(zip(a["clips"], b["clips"])):
            if x != y:
                differences.append(f"clip[{index}]: {x!r} != {y!r}")
        extra_a, extra_b = a["clips"][len(b["clips"]):], b["clips"][len(a["clips"]):]
        for x in extra_a:
            differences.append(f"only in {left.timeline_name}: {x!r}")
        for y in extra_b:
            differences.append(f"only in {right.timeline_name}: {y!r}")

    return differences


# ── Writing the artifacts ────────────────────────────────────────────

def external_payload(key: str, value, snapshot: TimelineSnapshot) -> dict:
    """One `<project>/external/<key>.json` body.

    `source` is a sentence a reader of `RUN-TRACEBACK.md` needs in order
    to know the value did not come from a step.  It is RECORDED, never
    trusted - the verdict comes from the check.
    """
    return {
        "key": key,
        "source": (f"read from the live Resolve timeline "
                   f"{snapshot.timeline_name!r} in project "
                   f"{snapshot.project_name!r} by "
                   f"library/tools/timeline_ingest.py"),
        "value": value,
    }


def write_external(project_folder, snapshot: TimelineSnapshot,
                   keys: Sequence[str] = ("a_roll_assignments",
                                          "speech_sequence"),
                   ) -> Dict[str, Path]:
    """Write the supplied values into the project's EXTERNAL_STATE area.

    `Area.EXTERNAL_STATE` is an INPUT area - `ProjectLayout.write_dir`
    raises for it and no step may write there, because it exists to hold
    what the captain supplied rather than what a run produced.  This
    module is not a step; it is the captain's own tool for turning their
    timeline into a supplied value, so it writes there directly and
    creates the directory if the captain has not yet.
    """
    from library.tools.external_inputs import external_dir, SUFFIX

    builders = {
        "a_roll_assignments": to_a_roll_assignments,
        "speech_sequence": to_speech_sequence,
    }
    unknown = [k for k in keys if k not in builders]
    if unknown:
        raise TimelineIngestError(
            f"nothing here builds {unknown}. This module supplies "
            f"{sorted(builders)}.")

    directory = external_dir(project_folder)
    directory.mkdir(parents=True, exist_ok=True)

    written: Dict[str, Path] = {}
    for key in keys:
        payload = external_payload(key, builders[key](snapshot), snapshot)
        path = directory / f"{key}{SUFFIX}"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written[key] = path
    return written


def snapshot_to_dict(snapshot: TimelineSnapshot) -> dict:
    """The whole snapshot as plain data, for a report or a test fixture."""
    body = asdict(snapshot)
    body["clips"] = [asdict(c) for c in snapshot.clips]
    body["duration_seconds"] = snapshot.duration
    return body

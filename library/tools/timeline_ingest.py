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

Frames, not `GetSourceStartTime` - and this cost a real defect
---------------------------------------------------------------
**`GetSourceStartFrame()`/`GetSourceEndFrame()` are FILE-RELATIVE.
`GetSourceStartTime()`/`GetSourceEndTime()` are TIMECODE-ABSOLUTE.**
They are not two spellings of one number, and using the wrong pair puts
the range outside the file.

This module originally used the TIME pair, on the reasoning that it
"agrees with `GetSourceStartFrame()`".  It does - on a file whose Start
Timecode is `00:00:00:00`.  The field test has seven source files and
exactly ONE of them, `LC4930.MXF`, has a zero start timecode; it was the
file the check was run against, so the one case that could not reveal the
bug is the case that was measured.

Measured properly, 2026-09-04, over all 167 clips of the field-test
timeline:

    GetSourceEndTime()  past the end of its file:  91 clips,
                                                   worst overshoot 9094.5s
    GetSourceEndFrame() past the end of its file:   0 clips

For `LCATL0013.MXF` - Start TC `01:26:05:21`, 98292 frames - a clip at
file frame 95 reports `GetSourceStartTime()` of 5169s, in a file 4099s
long.  Extracting that span yields an EMPTY file, and ffmpeg exits 0
while doing it.

So: read FRAMES, convert with `exact_frame_rate`.  `GetLeftOffset()` is
also file-relative but disagrees with `GetSourceStartFrame()` by a frame
on about a third of items, so the frame pair is used for both ends.

**A range is CHECKED against the media, never assumed** - see
`verify_against_media`.  A claim about a file is checkable, so it is
checked; that is the same standard `external_inputs` holds a supplied
value to, and it is what would have caught this on day one.

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

from library.tools.resolve_lock import under_lease

import json
import os
from dataclasses import dataclass, asdict, field
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
    """The range inside `source_file` that ACTUALLY PLAYS, in seconds.

    `source_out` is `source_in` plus the clip's TIMELINE duration, not
    `GetSourceEndFrame()`. Measured 2026-09-04: on 50 of the field
    test's 167 clips those two disagree by exactly one frame, in both
    directions. The timeline duration is what Resolve renders - the same
    PLAYED-versus-SOURCE distinction `library/tools/fusion/played_window.py`
    already states for comp time - so it is what a caller extracting the
    audio must use. Anything laying spans end to end by source length
    while positioning them by timeline gaps drifts a frame per
    disagreement."""

    source_in_frame: int
    source_out_frame: int
    """Resolve's RAW frame report, file-relative and kept verbatim.
    `source_out_frame` is the one that disagrees with the timeline
    duration by a frame on about a third of clips; it is retained
    because the exact bounds check is a frame comparison against
    `source_frames`, and because a raw reading should be recoverable."""

    source_frames: Optional[int]
    """How many frames the MEDIA POOL says the source file holds. The
    denominator of the only exact bounds check available without
    re-probing the file."""

    timeline_start: float
    timeline_end: float
    """Where it lands on the timeline, in seconds."""

    name: str

    transform: Mapping = field(default_factory=dict)
    """`TimelineItem.GetProperty()` verbatim - Pan, Tilt, ZoomX/Y, the
    Crop* family and the rest, exactly as Resolve reports them.

    Read because the PICTURE a clip puts on the frame is not in any of
    the fields above: a 3840x2160 source on a 1080x1920 timeline can
    deliver a full frame or a 31.6% strip with identical timings, and
    until this was carried nothing downstream could tell which. The
    consumer is `library/tools/reel_framing.py`.

    Empty when Resolve declines the call, which is not the same as
    Resolve reporting an identity transform - `reel_framing.IDENTITY`
    treats the two alike deliberately, because the renderer's own
    `_apply_conform` returns without setting anything for a letterbox."""

    @property
    def duration(self) -> float:
        """How long this clip occupies the timeline - the PLAYED length,
        and the authoritative one for extracting its audio."""
        return self.timeline_end - self.timeline_start

    @property
    def source_length_disagrees(self) -> bool:
        """True when Resolve's source frame count is not the played one."""
        return (self.source_out_frame - self.source_in_frame) != round(
            self.duration * 24000 / 1001) and self.source_frames is not None


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

    IT LOOKS IN THE PROJECT MANAGER'S *CURRENT FOLDER* and does not
    navigate, so where the manager is parked is part of the address.
    `project.yaml`'s `resolve.folder` names where a project lives
    (`geo-podcast`: `Lucie Content/Social Media/Podcast`) - park the
    manager there before building.

    Measured 2026-09-16: a read-only lane finished with
    `pm.GotoRootFolder()`, which is what "leave the instance as found"
    asks for, and the next reel build could not find
    `Podcast (field test)` at all.  The two conventions collide, and
    this side refuses rather than guessing - which is safe, and is why
    the collision shows up as a puzzling refusal rather than as damage.
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

def _pool_frames(pool_item) -> Optional[int]:
    """`Frames` off a media pool item, or None if it does not say.

    Judged by what it RETURNS (AGENTS.md section 5) - the property is a
    string on Resolve's proxies, and a missing one is the empty string
    rather than an absent key.
    """
    try:
        return int(pool_item.GetClipProperty("Frames"))
    except (TypeError, ValueError):
        return None


def _item_transform(item) -> dict:
    """`GetProperty()` off a timeline item, or `{}` if it does not say.

    Called with NO ARGUMENT, which AGENTS.md section 5 requires before
    trusting any property name: with an argument Resolve answers for
    names it does not have, and the whole-dict form is the only reading
    that says what the item really carries.

    Anything that is not a dict - including `None`, and including the
    `False` Resolve returns rather than raising - is `{}`. That reads
    downstream as "Resolve did not say", never as an identity transform
    somebody measured.
    """
    try:
        props = item.GetProperty()
    except Exception:            # Resolve raises bare Exceptions here
        return {}
    return dict(props) if isinstance(props, dict) else {}


def _setting_int(timeline, key: str, label: str) -> int:
    raw = timeline.GetSetting(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise TimelineIngestError(
            f"timeline setting {key!r} read back as {raw!r}, which is not "
            f"a {label}. Judge a Resolve call by what it RETURNS "
            f"(AGENTS.md section 5).")


@under_lease("snapshot a timeline", exclusive=False)
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
                # FRAMES, converted here - never GetSourceStartTime(),
                # which is timecode-absolute and lands outside the file
                # for any clip whose media has a non-zero start
                # timecode. See the module docstring.
                clips.append(TimelineClip(
                    resolve_item_id=item.GetUniqueId(),
                    track_type=track_type,
                    track_index=index,
                    track_name=track_name,
                    speaker=speaker,
                    source_file=source_file,
                    source_in=item.GetSourceStartFrame() / fps,
                    # The PLAYED range: in-point plus the timeline
                    # duration. Never GetSourceEndFrame(), which
                    # disagrees by a frame on about a third of clips.
                    source_out=(item.GetSourceStartFrame() / fps
                                + (item.GetEnd() - item.GetStart()) / fps),
                    source_in_frame=int(item.GetSourceStartFrame()),
                    source_out_frame=int(item.GetSourceEndFrame()),
                    source_frames=_pool_frames(pool_item),
                    timeline_start=item.GetStart() / fps,
                    timeline_end=item.GetEnd() / fps,
                    name=item.GetName() or "",
                    # Judged by what it RETURNS (AGENTS.md 5): Resolve
                    # hands back a dict of transform values, and anything
                    # else - including None - is recorded as "it did not
                    # say" rather than as an identity transform.
                    transform=_item_transform(item),
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


# ── Checking the snapshot against the media it names ─────────────────

def verify_against_media(snapshot: TimelineSnapshot) -> List[str]:
    """Every complaint about a clip's range, measured against its file.

    Returns COMPLAINTS rather than raising, so a caller can report all of
    them at once; `write_external` raises on a non-empty result, because
    supplying a range that is not inside its file is supplying a false
    ground truth.

    This is the check that would have caught the
    `GetSourceStartTime()` defect immediately, and it is cheap: one
    `ffprobe` per DISTINCT source file, not per clip.  A claim about a
    file on disk is checkable, so it gets checked.
    """
    from library.steps.step_1_02_catalog_footage.step import extract_metadata

    durations: Dict[str, Optional[float]] = {}
    complaints: List[str] = []
    for clip in snapshot.clips:
        # FIRST, and exactly: the frame range against the media pool's own
        # frame count. No ffprobe, no floats.
        #
        # This is the check that catches the case the duration check
        # CANNOT. When source ranges were read with GetSourceStartTime()
        # they were displaced by the media's start timecode; for
        # LC4932.MXF that is 512.9s inside a 4941s file, so 74 of 167
        # clips asked for a real, in-bounds range holding COMPLETELY
        # DIFFERENT speech. They extracted cleanly and passed every
        # output-shaped guard. Only comparing the frame numbers to the
        # file's own frame count finds them.
        if clip.source_frames is not None:
            if clip.source_out_frame > clip.source_frames:
                complaints.append(
                    f"{clip.resolve_item_id} plays "
                    f"{os.path.basename(clip.source_file)} to frame "
                    f"{clip.source_out_frame}, and the media pool reports "
                    f"only {clip.source_frames} frames in it")
            if clip.source_in_frame < 0:
                complaints.append(
                    f"{clip.resolve_item_id} starts at frame "
                    f"{clip.source_in_frame}, before the file begins")
        path = clip.source_file
        if path not in durations:
            if not os.path.isfile(path):
                durations[path] = None
            else:
                measured = extract_metadata(path) or {}
                durations[path] = measured.get("duration_seconds")
        duration = durations[path]
        if duration is None:
            complaints.append(
                f"{clip.resolve_item_id} plays {path}, which ffprobe "
                f"cannot measure")
            continue
        if clip.source_out > duration + 0.05:
            complaints.append(
                f"{clip.resolve_item_id} on {clip.track_type}"
                f"{clip.track_index} plays {os.path.basename(path)} to "
                f"{clip.source_out:.2f}s, and the file is "
                f"{duration:.2f}s long")
        if clip.source_in < -0.05:
            complaints.append(
                f"{clip.resolve_item_id} starts at {clip.source_in:.2f}s, "
                f"before the beginning of "
                f"{os.path.basename(path)}")
    return complaints


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

    # Never supply a range that is not inside its own file. A supplied
    # value is CHECKED, and this is the half only the producer can
    # check - `_check_a_roll_assignments` cross-checks durations only
    # when a catalog is on file, and a timeline-entry run has none.
    complaints = verify_against_media(snapshot)
    if complaints:
        shown = "\n  - ".join(complaints[:10])
        more = (f"\n  ... and {len(complaints) - 10} more"
                if len(complaints) > 10 else "")
        raise TimelineIngestError(
            f"{len(complaints)} clip range(s) are not inside the file "
            f"they name, so this snapshot would supply a false ground "
            f"truth:\n  - {shown}{more}")

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


# ── CLI ──────────────────────────────────────────────────────────────

def connect(project_name: str = "", timeline_name: str = ""):
    """`(snapshot, project, timeline)` for the live Resolve session.

    Both names come from the project's own `project.yaml` unless
    overridden.  Nothing is opened: `resolve_project_exactly` refuses a
    project that is not already the open one, because opening one is a
    write to the captain's session.
    """
    # Through `marker_feedback.connect_resolve`, which owns the module
    # path and already goes through `resolve_locale` (AGENTS.md 9 lists
    # it as one of the two migrated call sites). Duplicating the setup
    # here would add a ninth unmigrated one.
    from library.tools.marker_feedback import ResolveUnavailable, connect_resolve

    try:
        resolve = connect_resolve()
    except ResolveUnavailable as exc:
        raise TimelineIngestError(
            f"cannot reach Resolve: {exc} This reads a LIVE project, and a "
            f"closed one is the case that stays withdrawn (see "
            f"external_inputs.WITHDRAWN).") from exc
    manager = resolve.GetProjectManager()
    project = resolve_project_exactly(manager, project_name)
    timeline = timeline_named(project, timeline_name)
    return snapshot_timeline(timeline, project.GetName()), project, timeline


def resolve_binding(project_folder: str):
    """`(resolve project name, master timeline name)` a project DECLARES.

    ONE reader, because a project's Resolve binding is addressed by its
    EXACT listed name and a near match lands on another project
    (AGENTS.md 5) - so a second spelling of "which project" is a second
    chance to open the wrong one.  This module is where that rule lives,
    which is why the reader is here.

    Never invents: a project.yaml that names neither comes back as two
    empty strings, and the CALLER decides what an empty binding means.
    An unreadable or absent project.yaml is the same answer for the same
    reason - the file not being there is not a different kind of "this
    project declares no binding", and raising would make every caller
    write the same try/except.

    Read as RAW YAML rather than through `load_project_config`, and that
    is not laziness.  The validator demands a whole valid `ProjectConfig`
    - a `slug`, a source block - and raises `ValueError` for a project
    that is missing any of them.  `reel_build.rebuild_reels_in_project`
    has always read this block with a plain `yaml.safe_load`, so a
    reader that went through the validator would refuse projects the
    build itself builds happily: a gate that FAILS correct input, which
    is no more coverage than one that cannot fail (AGENTS.md 10.4).  The
    question here is narrow - does the project DECLARE these two names -
    and it must be answerable without the rest of the file being well
    formed.
    """
    try:
        import yaml
    except ImportError:                                   # pragma: no cover
        return "", ""
    path = os.path.join(project_folder or "", "project.yaml")
    try:
        with open(path, encoding="utf-8") as handle:
            declared = yaml.safe_load(handle) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return "", ""
    if not isinstance(declared, dict):
        return "", ""
    binding = declared.get("resolve") or {}
    if not isinstance(binding, dict):
        return "", ""
    return (str(binding.get("project_name") or ""),
            str(binding.get("timeline_name") or ""))


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Read a live Resolve timeline as pipeline state.")
    parser.add_argument("project_folder")
    parser.add_argument("--project", default="",
                        help="Resolve project name; default from project.yaml")
    parser.add_argument("--timeline", default="",
                        help="timeline name; default from project.yaml")
    parser.add_argument("--write", action="store_true",
                        help="write the supplied values into <project>/external/")
    args = parser.parse_args(argv)

    project_name, timeline_name = resolve_binding(args.project_folder)
    snapshot, _project, _timeline = connect(args.project or project_name,
                                            args.timeline or timeline_name)

    print(f"{snapshot.project_name!r} / {snapshot.timeline_name!r}")
    print(f"  {len(snapshot.clips)} clips, {snapshot.duration / 60:.1f} min, "
          f"{snapshot.width}x{snapshot.height} @ {snapshot.reported_fps} "
          f"(computed at {snapshot.fps})")
    print(f"  speakers: {snapshot.speakers()}")

    complaints = verify_against_media(snapshot)
    if complaints:
        print(f"  REFUSED: {len(complaints)} clip range(s) fall outside "
              f"the file they name:")
        for line in complaints[:10]:
            print(f"    - {line}")
        return 1
    print(f"  every clip range verified inside its own source file")

    if args.write:
        for key, path in write_external(args.project_folder, snapshot).items():
            print(f"  wrote {key} -> {path}")
    else:
        print("  (dry run; pass --write to supply these to the pipeline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

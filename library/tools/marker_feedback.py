"""Collect the captain's typed timeline notes out of DaVinci Resolve.

The captain reviews a built timeline inside Resolve and drops markers on
it carrying natural language: what looks wrong, what to change, what to
go and find out.  This module READS those notes and writes them somewhere
a re-render cannot reach.  It does not interpret them, does not answer
them, and has no vocabulary of marker colours - the typed text is the
signal.

── What was measured, and on what ──────────────────────────────────────

Every claim below was established against a real running DaVinci Resolve
Studio 21.0.0b.28 on macOS, on throwaway timelines (`MARKERPROBE_*`) in
the captain's own `Pipeline_Edit` project, on 2026-08-28.  This file used
to be exercised only by `unittest.mock` fakes that returned whatever the
test had just told them to, and four of its assumptions were wrong.

`hasattr` is useless on Resolve's scripting proxies - every attribute
lookup succeeds, including invented ones (`hasattr(item, 'GetTotallyMadeUpThing')`
is True; calling it raises `TypeError: 'NoneType' object is not callable`).
Nothing here guards on `hasattr`; the calls below were run and are judged
by what they returned.

MARKERS CARRY TWO PIECES OF TEXT, AND BOTH ARE TYPED BY A HUMAN.
`GetMarkers()` returns `{int frame: {"color", "duration", "name", "note",
"customData"}}`.  `name` is Resolve's Add Marker dialog **Name** field -
the one the cursor lands in - and `note` is its **Notes** field.  Reading
only `note` silently loses everything typed into Name, and the marker
still looks present on the timeline.  This project's own `Pipeline_Edit`
timeline already carries such a marker: name `Master Limiter: -1.0dBTP`,
note `Set the master track limiter to this threshold`.  Both fields are
kept verbatim here, and `MarkerNote.text` joins whichever are non-empty.

`Timeline.AddMarker` REFUSES A MARKER WITH AN EMPTY NAME, honestly.
Measured returns: name-only True, note-only **False**, both True, both
empty **False**, a frame already carrying a marker **False**.  A marker
that failed to land is not on the timeline at all - so a writer that
discards the return value reports feedback it never recorded.

TIMELINE MARKER FRAMES ARE RELATIVE TO `Timeline.GetStartFrame()`.
`TimelineItem.GetStart()` is ABSOLUTE.  On a timeline started at
01:00:00:00 (Resolve's default, `GetStartFrame() == 108000` at 30fps),
comparing a marker's key against clip bounds directly finds no clips at
any frame.  Absolute frame is `GetStartFrame() + key`.  Neither
`Timeline.AddMarker` nor `TimelineItem.AddMarker` bounds-checks the frame:
markers past the end of the timeline were accepted and read back.

`TimelineItem.GetMarkers()` IS KEYED IN SOURCE FRAMES - the same space as
`GetLeftOffset()`, not clip-relative and not timeline frames.  Proof: a
marker added to the MEDIA POOL item at source frame 150 appears, key 150
unchanged, on two timeline items cut from that file at `GetLeftOffset()`
100 and 300; a clip-relative key space would have rebased it to 50 and
dropped it respectively.  Media pool marker frames are in turn proved to
be source frames because Resolve bounds-checks them against the file:
on a 4174-frame clip, `AddMarker` returned True for 0, 4100 and 4173 and
**False** for 4174 and 9999.  So:

    timeline_frame = item.GetStart() + (key - item.GetLeftOffset())

and it is only meaningful while `left_offset <= key < left_offset + duration`.
The old `start + max(0, key - left_offset)` clamp put every out-of-range
marker on the clip's first frame, inventing a position for it.

`GetLeftOffset()` IS REAL and returns the source in-point (100 and 500
for clips appended with `startFrame` 100 and 500).  The `hasattr` guard
that used to sit around it was a no-op, but the call itself is sound.

A TIMELINE ITEM'S MARKER SET IS ITS OWN, SEEDED FROM THE MEDIA POOL AT
PLACEMENT TIME.  A pool marker that exists when a clip is placed is COPIED
onto that timeline item - onto every item cut from the file, including
ones that never play the frame it sits on: the clip playing source
300..399 reported the pool's marker at source 150.  A pool marker added
AFTER placement reaches no existing item, and deleting the pool's markers
does not remove the copies.  Neither do the copies share sideways: a
marker added to one item did not appear on a sibling, on the pool item,
or on an item on another timeline, and `DeleteMarkerAtFrame` on a sibling
returned False.  So a pool marker is collected once per FILE here, mapped
onto every placement whose played range contains it, and an item's copy
of it is not reported again - unless its TEXT has since diverged from the
pool's, in which case both are real and both are kept.

`TimelineItem.GetProperty("Comments")` IS ALWAYS None.  A timeline item's
property dictionary (read with no argument, per AGENTS.md 5) holds 26
transform keys - Pan, Tilt, ZoomX, CropLeft, Opacity and so on - and no
"Comments".  Setting a comment on the media pool item and reading it back
through the timeline item still returned None.  Clip comments are a MEDIA
POOL property and are read here as
`item.GetMediaPoolItem().GetClipProperty("Comments")`.

Verified working as expected: `Timeline.GetMarkers`, `Timeline.AddMarker`,
`Timeline.DeleteMarkerAtFrame` (True for a present frame, False for an
absent one), `Timeline.GetMarkerByCustomData`, `TimelineItem.GetMarkers`,
`TimelineItem.GetStart`/`GetEnd` (end is EXCLUSIVE: a clip at 0 with
duration 99 reports GetEnd 99 and the next clip GetStart 99),
`GetDuration`, `GetLeftOffset`, `GetSourceStartFrame`, `GetMediaPoolItem`,
`GetClipColor`/`SetClipColor`, `GetFlagList`/`AddFlag`,
`MediaPoolItem.GetClipProperty("File Path")`.

── What this module does NOT do ────────────────────────────────────────

No colour vocabulary.  The captain chose typed notes over colour codes,
so colour is recorded as data and read by nothing.  No acknowledgement
marker is written back and no marker is deleted: this module is a reader
plus a durable writer to disk, and the timeline is the captain's.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

The captain reviews a built timeline **inside DaVinci Resolve** and drops markers on it carrying
natural language - what looks wrong, what to change, what to go and find out.
One enumeration, `library/tools/marker_feedback.py`, which reads them and writes them to disk.
It is proved against a real running Resolve by `tests/test_marker_feedback_against_resolve.py`;
its recorded per-call findings are in the module docstring, in the shape `neural_engine.py` uses.
- **A MARKER CARRIES TWO PIECES OF TYPED TEXT AND BOTH ARE READ.** `GetMarkers()` returns `name`
  (the Add Marker dialog's **Name** field, where the cursor lands) and `note` (its **Notes**
  field). Reading only `note` loses everything typed into Name, silently, with the marker still
  on the timeline. Both are kept verbatim; nothing is summarised, truncated or normalised.
  `Timeline.AddMarker` REFUSES a marker whose name is empty.
- **A timeline marker's frame is relative to `Timeline.GetStartFrame()`; `TimelineItem.GetStart()`
  is absolute.** A clip marker's frame is a SOURCE frame, the same space as `GetLeftOffset()`, so
  `timeline_frame = item.GetStart() + (key - item.GetLeftOffset())` and only inside the range the
  clip plays. Resolve bounds-checks neither. A key outside that range is kept UNPLACED with the
  reason, never clamped to the clip's head.
- **`TimelineItem.GetProperty("Comments")` is always None.** Clip comments are a MEDIA POOL
  property. A timeline item's property dict holds transform keys only - read it with no argument
  (§5) before trusting a name.
- **The record goes to `<project>/marker_feedback/`, and that is why `Kind.CAPTURED` exists.**
  Everything under `pipeline_output/` is `Kind.OUTPUT` - safe to delete because a re-run
- **The build path REFUSES to delete a timeline carrying uncollected notes.** `guard_timeline_deletion` fails the build, naming the notes. `PIPELINE_DISCARD_TIMELINE_MARKERS=1` is the override.
- **The reader READS, and the two things that write to a marker write only `customData`** -
  the capture button and the decision stamp. Neither creates a marker, touches `name`/`note`/colour/duration, or deletes one.
  No colour vocabulary; no acknowledgement marker is written back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import marker_payload  # noqa: E402
from library.tools.marker_capture import (  # noqa: E402
    project_folder_from_timeline,
)
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.resolve_locale import (  # noqa: E402
    scriptapp_preserving_locale,
)

RESOLVE_SCRIPT_API = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting"
)
RESOLVE_SCRIPT_LIB = (
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/"
    "Libraries/Fusion/fusionscript.so"
)

PULL_FILE_SUFFIX = ".markers.json"
PULL_FORMAT = "marker_feedback/1"


class ResolveUnavailable(RuntimeError):
    """Resolve is not running, or its scripting API is not on this machine.

    Raised rather than returning an empty list.  "No markers" and "I could
    not look" are different answers, and a caller that cannot tell them
    apart will report the captain's notes as absent.
    """


class UnpulledMarkers(RuntimeError):
    """A timeline carrying notes nobody has collected was about to go."""

    def __init__(self, message: str, notes: list) -> None:
        super().__init__(message)
        self.notes = notes


# ── Connecting ──────────────────────────────────────────────────────


def connect_resolve():
    """The running Resolve, or raise.  See `ResolveUnavailable`."""
    modules = os.path.join(RESOLVE_SCRIPT_API, "Modules")
    if modules not in sys.path:
        sys.path.append(modules)
    os.environ.setdefault("RESOLVE_SCRIPT_API", RESOLVE_SCRIPT_API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", RESOLVE_SCRIPT_LIB)
    try:
        import DaVinciResolveScript as dvr
    except ImportError as exc:
        raise ResolveUnavailable(
            f"DaVinci Resolve's scripting module is not importable from "
            f"{modules}. Install Resolve, or set RESOLVE_SCRIPT_API and "
            f"RESOLVE_SCRIPT_LIB (AGENTS.md 9)."
        ) from exc
    # Through the wrapper, never `dvr.scriptapp` directly: the call
    # resets LC_CTYPE and every later read of a UTF-8 file without an
    # explicit encoding would raise. See `resolve_locale`.
    resolve = scriptapp_preserving_locale(dvr)
    if not resolve:
        raise ResolveUnavailable(
            "Resolve is not running, or has not finished loading. Open it "
            "with the project you want to read, then try again."
        )
    return resolve


def current_timeline(resolve=None):
    """The timeline Resolve has open, plus its project.  Raises if none."""
    resolve = resolve or connect_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise ResolveUnavailable("Resolve has no project open.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ResolveUnavailable(
            f"Resolve project {project.GetName()!r} has no timeline open. "
            f"Open the timeline you want to read."
        )
    return timeline, project


# ── The record ──────────────────────────────────────────────────────


@dataclass
class ClipPlacement:
    """One clip sitting under a note's frame."""

    name: str
    track_type: str
    track_index: int
    timeline_start: int
    timeline_end: int
    source_start: int
    source_end: int
    source_file: str = ""
    clip_color: str = ""
    flags: list = field(default_factory=list)


@dataclass
class Attachment:
    """A file this note points at, that a reader can open.

    Two things reach this list and they are told apart, never merged.
    `custom_data` is a file a WRITER attached - the capture button's
    still - recorded in the marker's `customData` where the UI cannot
    show it.  `note_text` is a path the captain TYPED into the marker
    themselves, which worked before this and goes on working: it is
    reported here so a reader has one list of openable things rather
    than two, and the note text itself is never rewritten or stripped.

    `exists` is measured on disk at read time.  A path that is not there
    is reported as absent, not dropped - the captain naming a file that
    has moved is something the reader should say out loud.
    """

    kind: str
    """`still` for a written record, `typed_path` for one they typed."""

    origin: str
    """`custom_data` or `note_text` - which of the two above."""

    path: str
    """As recorded.  Project-relative for a written record (see
    `marker_payload`); exactly as typed for a typed one."""

    resolved_path: str = ""
    exists: bool = False
    record_id: str = ""
    writer: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class MarkerNote:
    """One thing the captain typed, with enough context to start from.

    `name` and `note` are the two text fields of Resolve's marker dialog,
    kept exactly as typed - not summarised, truncated or normalised.
    `text` is derived from them for convenience; the two fields are what
    is authoritative.
    """

    source: str
    """`timeline_marker`, `clip_marker`, `media_pool_marker` or
    `clip_comment` - which surface the captain typed it on."""

    name: str
    note: str
    text: str

    frame: Optional[int]
    """Absolute timeline frame, or None when the note cannot be placed on
    this timeline. None is never rounded to a nearby frame."""

    timecode: Optional[str]
    frame_in_timeline_space: Optional[int] = None
    """The frame as Resolve reported it, before any conversion - a
    timeline marker's key is relative to `GetStartFrame()`, a clip
    marker's is a SOURCE frame."""

    unplaced_reason: str = ""
    color: str = ""
    duration_frames: int = 1
    custom_data: dict = field(default_factory=dict)
    custom_data_raw: str = ""
    attachments: list = field(default_factory=list)
    """Files this note points at - see `Attachment`. A note WITH one and a
    note without are deliberately different: the whole point of the
    capture button is that a reader stops guessing which of four stacked
    clips the captain meant."""

    clips: list = field(default_factory=list)
    """Every clip playing at `frame`. CONTEXT, and for a note typed at a
    MOMENT it is all there is. It is deliberately NOT the same field as
    `attached_clip`: reading "what was on screen" as "what the captain
    selected" hands a note typed on a V2 cutaway to whatever was on V1
    underneath it."""

    attached_clip: Optional[dict] = None
    """The placement the note was typed ON, for the three clip-attached
    sources - `clip_marker`, `media_pool_marker` and `clip_comment`.
    None for a `timeline_marker`, which is about a MOMENT and is attached
    to no clip. The loop that reads each of those three already holds the
    placement; this records it, so a reader does not have to recover it
    from the frame arithmetic. `marker_routing.resolve_target` still can,
    for pull files written before this field existed."""

    read_at: str = ""


# ── Reading ─────────────────────────────────────────────────────────


def _timeline_fps(timeline) -> float:
    for key in ("timelineFrameRate", "timelinePlaybackFrameRate"):
        raw = timeline.GetSetting(key)
        try:
            fps = float(raw)
        except (TypeError, ValueError):
            continue
        if fps > 0:
            return fps
    return 0.0


def frames_to_timecode(frame: Optional[int], fps: float) -> Optional[str]:
    """Non-drop timecode for an absolute timeline frame.

    None rather than a guess when the frame or the rate is unknown: a
    timecode nobody can scrub to is worse than an admitted absence.
    Drop-frame rates are rendered non-drop and the frame number beside it
    is the authority.
    """
    if frame is None or fps <= 0:
        return None
    rate = int(round(fps))
    total = int(frame)
    sign = "-" if total < 0 else ""
    total = abs(total)
    f = total % rate
    s = (total // rate) % 60
    m = (total // (rate * 60)) % 60
    h = total // (rate * 3600)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _text_of(marker: dict) -> tuple:
    """(name, note, joined).  Both fields verbatim; nothing is dropped."""
    name = marker.get("name") or ""
    note = marker.get("note") or ""
    joined = "\n\n".join(part for part in (name, note) if part)
    return name, note, joined


def _parse_custom_data(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _resolve_attachment_path(raw: str, project_folder) -> tuple:
    """(absolute path or "", exists).  Never invents a location."""
    if not raw:
        return "", False
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if not project_folder:
            return "", False
        path = Path(project_folder) / path
    return str(path), path.exists()


def read_attachments(name: str, note: str, custom_data: dict,
                     project_folder=None) -> list:
    """Every file this note points at, written or typed.

    Written ones come out of the `marker_payload` envelope and are
    project-relative; typed ones are absolute paths appearing in the text
    the captain typed, matched conservatively (`marker_payload.typed_paths`)
    so prose is not mistaken for a filename.
    """
    out: list = []
    for record in marker_payload.attachments_of(custom_data):
        raw = str(record.get("path") or "")
        resolved, exists = _resolve_attachment_path(raw, project_folder)
        if not resolved and record.get("path_absolute"):
            resolved, exists = _resolve_attachment_path(
                str(record["path_absolute"]), project_folder)
        out.append(Attachment(
            kind=str(record.get("kind") or "file"),
            origin="custom_data",
            path=raw,
            resolved_path=resolved,
            exists=exists,
            record_id=str(record.get("id") or ""),
            writer=str(record.get("writer") or ""),
            detail={k: v for k, v in record.items()
                    if k not in ("path", "path_absolute")},
        ))
    written = {a.resolved_path for a in out if a.resolved_path}
    for typed in marker_payload.typed_paths("\n".join((name or "", note or ""))):
        resolved, exists = _resolve_attachment_path(typed, project_folder)
        if resolved in written:
            continue  # the captain typed the path the button had written
        out.append(Attachment(
            kind="typed_path", origin="note_text", path=typed,
            resolved_path=resolved, exists=exists,
        ))
    return out


def _placement(item, track_type: str, track_index: int) -> ClipPlacement:
    pool_item = item.GetMediaPoolItem()
    return ClipPlacement(
        name=item.GetName() or "",
        track_type=track_type,
        track_index=track_index,
        timeline_start=int(item.GetStart()),
        timeline_end=int(item.GetEnd()),
        source_start=int(item.GetLeftOffset()),
        source_end=int(item.GetLeftOffset()) + int(item.GetDuration()),
        source_file=(pool_item.GetClipProperty("File Path") or "") if pool_item else "",
        clip_color=item.GetClipColor() or "",
        flags=list(item.GetFlagList() or []),
    )


def _all_placements(timeline) -> list:
    """Every clip on the timeline, with its own frame arithmetic resolved."""
    out = []
    for track_type in ("video", "audio"):
        count = timeline.GetTrackCount(track_type) or 0
        for index in range(1, count + 1):
            for item in timeline.GetItemListInTrack(track_type, index) or []:
                out.append((item, _placement(item, track_type, index)))
    return out


def _clips_at(placements: list, frame: Optional[int]) -> list:
    if frame is None:
        return []
    return [
        p for _, p in placements
        if p.timeline_start <= frame < p.timeline_end
    ]


def read_notes(timeline, project_folder=None) -> list:
    """Every typed note on `timeline`, in timeline order.

    Takes the Resolve timeline object so a caller can drive this against a
    scratch timeline without changing what Resolve has open.

    `project_folder` is what an attachment's project-relative path is
    resolved against.  Left out, it is MEASURED off the timeline's own
    footage the same way the capture button measures it, so `show` and
    `pull` report the same paths without the caller having to say.
    """
    read_at = datetime.now(timezone.utc).isoformat()
    fps = _timeline_fps(timeline)
    start_frame = int(timeline.GetStartFrame())
    placements = _all_placements(timeline)
    if project_folder is None:
        project_folder = project_folder_from_timeline(timeline)
    notes: list = []

    def add(**kw):
        frame = kw.get("frame")
        notes.append(MarkerNote(
            timecode=frames_to_timecode(frame, fps),
            clips=[asdict(c) for c in _clips_at(placements, frame)],
            attachments=[asdict(a) for a in read_attachments(
                kw.get("name", ""), kw.get("note", ""),
                kw.get("custom_data") or {}, project_folder)],
            read_at=read_at,
            **kw,
        ))

    # 1. Timeline markers.  Keys are relative to GetStartFrame().
    for key, marker in (timeline.GetMarkers() or {}).items():
        name, note, text = _text_of(marker)
        add(
            source="timeline_marker",
            name=name, note=note, text=text,
            frame=start_frame + int(key),
            frame_in_timeline_space=int(key),
            color=marker.get("color") or "",
            duration_frames=int(marker.get("duration") or 1),
            custom_data=_parse_custom_data(marker.get("customData") or ""),
            custom_data_raw=marker.get("customData") or "",
        )

    # 2. Media pool markers, once per FILE.  Inherited by every placement
    #    cut from that file, including ones that never play the frame.
    pool_seen: dict = {}
    for item, placement in placements:
        pool_item = item.GetMediaPoolItem()
        if not pool_item:
            continue
        path = placement.source_file or placement.name
        if path in pool_seen:
            continue
        pool_seen[path] = pool_item.GetMarkers() or {}

    for path, pool_markers in pool_seen.items():
        for key, marker in pool_markers.items():
            name, note, text = _text_of(marker)
            source_frame = int(key)
            hits = [
                p for _, p in placements
                if p.source_file == path
                and p.source_start <= source_frame < p.source_end
            ]
            common = dict(
                source="media_pool_marker",
                name=name, note=note, text=text,
                frame_in_timeline_space=source_frame,
                color=marker.get("color") or "",
                duration_frames=int(marker.get("duration") or 1),
                custom_data=_parse_custom_data(marker.get("customData") or ""),
                custom_data_raw=marker.get("customData") or "",
            )
            if not hits:
                add(
                    frame=None,
                    unplaced_reason=(
                        f"media pool marker at source frame {source_frame} of "
                        f"{path or 'an unnamed file'}; no clip on this "
                        f"timeline plays that frame"
                    ),
                    **common,
                )
                continue
            for placement in hits:
                add(
                    frame=placement.timeline_start
                    + (source_frame - placement.source_start),
                    attached_clip=asdict(placement),
                    **common,
                )

    # 3. Timeline-item markers.  Keys are SOURCE frames; a key the clip
    #    does not play is kept, unplaced, rather than clamped to its head.
    for item, placement in placements:
        inherited = {
            int(k): _text_of(v)[:2]
            for k, v in pool_seen.get(
                placement.source_file or placement.name, {}).items()
        }
        for key, marker in (item.GetMarkers() or {}).items():
            if inherited.get(int(key)) == _text_of(marker)[:2]:
                continue  # the pool's own copy, already collected above
            name, note, text = _text_of(marker)
            source_frame = int(key)
            in_range = (
                placement.source_start <= source_frame < placement.source_end
            )
            add(
                source="clip_marker",
                name=name, note=note, text=text,
                frame=(
                    placement.timeline_start
                    + (source_frame - placement.source_start)
                ) if in_range else None,
                frame_in_timeline_space=source_frame,
                unplaced_reason="" if in_range else (
                    f"marker at source frame {source_frame}, outside the "
                    f"{placement.source_start}..{placement.source_end} this "
                    f"clip plays"
                ),
                color=marker.get("color") or "",
                duration_frames=int(marker.get("duration") or 1),
                custom_data=_parse_custom_data(marker.get("customData") or ""),
                custom_data_raw=marker.get("customData") or "",
                attached_clip=asdict(placement),
            )

    # 4. Clip comments - a MEDIA POOL property, once per file.
    for item, placement in placements:
        pool_item = item.GetMediaPoolItem()
        if not pool_item:
            continue
        comment = pool_item.GetClipProperty("Comments") or ""
        if not comment:
            continue
        if any(
            n.source == "clip_comment" and n.text == comment
            and n.frame == placement.timeline_start for n in notes
        ):
            continue
        add(
            source="clip_comment",
            name="", note=comment, text=comment,
            frame=placement.timeline_start,
            frame_in_timeline_space=placement.timeline_start,
            duration_frames=placement.timeline_end - placement.timeline_start,
            attached_clip=asdict(placement),
        )

    notes.sort(key=lambda n: (n.frame is None, n.frame or 0, n.source, n.text))
    return notes


def read_current_timeline_notes(project_folder=None) -> tuple:
    """(notes, timeline_name, project_name) for whatever Resolve has open."""
    timeline, project = current_timeline()
    return (read_notes(timeline, project_folder), timeline.GetName(),
            project.GetName())


# ── Writing a reply ───────────────────────────────────────────────
#
# The captain asks for a green marker at the same position as each piece
# of feedback addressed, stating what was asked and what was done. This
# is the ONLY writer in this module: it writes one marker and nothing
# else - never deletes, never alters, never answers on the captain's
# own markers. The two measured behaviours in the module docstring are
# enforced here rather than trusted: an empty name is refused by
# Resolve (honestly, returns False), and a past-the-end frame is
# ACCEPTED (returns True), so the bounds check is ours and the return
# value alone proves nothing - every write is read back.


class MarkerWriteError(RuntimeError):
    """A reply marker that did not land as written."""


REPLY_RECORD_KIND = "reply"
"""The `marker_payload` record a reply of OURS carries.

Why a reply must be machine-identifiable, measured 2026-09-11: every
marker on `lucie/geo-podcast` carried an EMPTY `customData`, our own
green replies included, so nothing downstream could tell a question the
captain typed from an answer we wrote back. A reader that has to guess
guesses on colour, and this module has no vocabulary of marker colours
by design - the typed text is the signal, and the captain may type any
colour they like.

So the ONE thing that can be stated mechanically is stated: the writer
of a reply is us, and a record saying so is written at the moment of
writing. It also carries WHAT IT ANSWERS - the durable identity from
`feedback_ledger.durable_identity` plus the captain's words verbatim - so
the question survives its own marker being removed. That is the shape
the 2026-09-11 rounds needed and did not have: a blue marker became a
green reply and the question was gone.
"""

REPLY_WRITER = "marker_feedback"
REPLY_WRITER_VERSION = 1


def reply_record(answers: str = "", answers_text: str = "",
                 summary: str = "") -> dict:
    """One `marker_payload` record stating this marker is our reply.

    `answers` is the answered note's durable identity and `answers_text`
    the captain's own words. Both are optional and BOTH are recorded
    when given: an identity is exact and a rebuild cannot move it, and
    the words are what a human reads when they find this marker a month
    later with no ledger to hand.
    """
    from library.tools import marker_payload

    record = {
        "kind": REPLY_RECORD_KIND,
        "writer": REPLY_WRITER,
        "writer_version": REPLY_WRITER_VERSION,
        "id": marker_payload.new_id("reply"),
        "at": marker_payload.utc_now(),
    }
    if answers:
        record["answers"] = str(answers)
    if answers_text:
        record["answers_text"] = str(answers_text)
    if summary:
        record["summary"] = str(summary)
    return record


def reply_custom_data(existing: str = "", answers: str = "",
                      answers_text: str = "", summary: str = "") -> str:
    """The `customData` string for a reply, merged into what is there.

    Merged rather than replaced, through `marker_payload.parse`, which
    keeps a foreign or future payload under `foreign` rather than
    destroying it - a marker's customData may already carry another
    writer's records.
    """
    from library.tools import marker_payload

    envelope = marker_payload.parse(existing or "")
    marker_payload.merge_record(
        envelope, reply_record(answers, answers_text, summary))
    return marker_payload.dumps(envelope)


def reply_records_in(custom_data) -> list:
    """Every reply record in a marker's `customData`. `[]` for none.

    Accepts the raw string or an already-parsed envelope, because
    `read_notes` reports both `custom_data` and `custom_data_raw`.
    """
    from library.tools import marker_payload

    if isinstance(custom_data, dict):
        envelope = custom_data if marker_payload.is_envelope(custom_data) \
            else marker_payload.parse("")
    else:
        envelope = marker_payload.parse(custom_data or "")
    return [r for r in marker_payload.records_of(envelope, REPLY_RECORD_KIND)
            if r.get("writer") == REPLY_WRITER]


def place_reply_marker(timeline, frame: int, color: str, name: str,
                       note: str, duration: int = 1,
                       custom_data: str = "") -> dict:
    """Add one reply marker at ABSOLUTE `frame`, verified by read-back.

    `frame` is in the same space `read_notes` reports
    (`GetStartFrame() + key`); the key handed to `AddMarker` is derived
    and bounds-checked here, because Resolve accepts past-the-end
    frames.  Returns the read-back record.  Raises `MarkerWriteError`
    when the frame is off the timeline, when Resolve refuses the write
    (an empty name, a frame already carrying a marker), or when the
    read-back disagrees on colour, name or note.

    `custom_data` is the `marker_payload` string this reply carries -
    build it with `reply_custom_data` so the marker states, mechanically,
    that it is OURS and what it answers.  It is passed as `AddMarker`'s
    sixth argument, which `marker_capture` measured carries customData in
    at creation, and read back like every other field.  Empty is the old
    behaviour exactly, five arguments and all: a caller with nothing to
    record must not be made to pass an empty envelope.
    """
    if not name:
        raise MarkerWriteError(
            "Resolve refuses a marker with an empty name - refusing "
            "here rather than writing nothing and reporting it.")
    start_frame = int(timeline.GetStartFrame())
    span = int(timeline.GetEndFrame()) - start_frame
    key = int(frame) - start_frame
    if key < 0 or key >= span:
        raise MarkerWriteError(
            f"frame {frame} is off {timeline.GetName()!r} "
            f"(0..{span - 1} in timeline space) - Resolve would accept "
            f"it and the marker would sit past the end where nobody "
            f"can see it.")
    landed = (timeline.AddMarker(key, color, name, note, duration,
                                 custom_data) if custom_data
              else timeline.AddMarker(key, color, name, note, duration))
    if not landed:
        raise MarkerWriteError(
            f"Resolve refused the marker at frame {frame} on "
            f"{timeline.GetName()!r} - an empty name, or a marker "
            f"already there. Nothing was written.")
    back = (timeline.GetMarkers() or {}).get(key, {})
    checks = [("color", color), ("name", name), ("note", note)]
    if custom_data:
        checks.append(("customData", custom_data))
    for field, want in checks:
        if (back.get(field) or "") != want:
            raise MarkerWriteError(
                f"marker at frame {frame} read back {field} "
                f"{back.get(field)!r}, not {want!r} - the write did "
                f"not land as stated.")
    return {"frame": int(frame), "key": key, "color": back.get("color"),
            "duration": back.get("duration"), "name": back.get("name"),
            "note": back.get("note"),
            "custom_data": back.get("customData") or ""}


def place_reply_clip_marker(item, source_frame: int, color: str,
                            name: str, note: str,
                            duration: int = 1,
                            custom_data: str = "") -> dict:
    """Add one reply marker ON A CLIP, at a SOURCE frame, read back.

    The captain leaves feedback on the clip the decision is about -
    which picture clip, which overlay, which card - and the answer has
    to come back on the SAME clip at the SAME position, or he looks
    where he asked and finds nothing.  `place_reply_marker` above
    answers on the TIMELINE; this is its clip half, and it exists
    because a round of feedback was missed on 2026-09-11 for the
    mirror-image reason: firstmate's own capture read
    `Timeline.GetMarkers()` only, so three clip markers on Reels 09
    and 26 reported as no markers at all.

    `source_frame` is a SOURCE frame, the same space `read_notes`
    reports as `frame_in_timeline_space` for a `clip_marker` and the
    same space `GetLeftOffset()` is in.  It is bounds-checked against
    the range the clip actually PLAYS
    (`GetLeftOffset() .. GetLeftOffset() + GetDuration()`), because
    Resolve bounds-checks neither end: a key outside that range is
    accepted, returns True, and sits where nobody can see it.

    Returns the read-back record.  Raises `MarkerWriteError` when the
    frame is outside what the clip plays, when Resolve refuses the
    write (an empty name, a marker already on that frame), or when the
    read-back disagrees on colour, name or note.

    `custom_data` behaves exactly as it does on `place_reply_marker` -
    the sixth argument when non-empty, checked on the read-back, and
    absent otherwise.
    """
    if not name:
        raise MarkerWriteError(
            "Resolve refuses a marker with an empty name - refusing "
            "here rather than writing nothing and reporting it.")
    first = int(item.GetLeftOffset())
    last = first + int(item.GetDuration())
    key = int(source_frame)
    if key < first or key >= last:
        raise MarkerWriteError(
            f"source frame {key} is outside the {first}..{last} this "
            f"clip plays ({item.GetName()!r}) - Resolve would accept it "
            f"and the marker would sit on footage the timeline never "
            f"shows.")
    landed = (item.AddMarker(key, color, name, note, duration, custom_data)
              if custom_data
              else item.AddMarker(key, color, name, note, duration))
    if not landed:
        raise MarkerWriteError(
            f"Resolve refused the marker at source frame {key} on "
            f"{item.GetName()!r} - an empty name, or a marker already "
            f"there. Nothing was written.")
    back = (item.GetMarkers() or {}).get(key, {})
    checks = [("color", color), ("name", name), ("note", note)]
    if custom_data:
        checks.append(("customData", custom_data))
    for field, want in checks:
        if (back.get(field) or "") != want:
            raise MarkerWriteError(
                f"clip marker at source frame {key} read back {field} "
                f"{back.get(field)!r}, not {want!r} - the write did not "
                f"land as stated.")
    return {"source_frame": key, "color": back.get("color"),
            "duration": back.get("duration"), "name": back.get("name"),
            "note": back.get("note"),
            "custom_data": back.get("customData") or ""}


def remove_clip_marker(item, source_frame: int) -> bool:
    """Delete one clip marker at a SOURCE frame, verified by read-back.

    The other half of answering on a clip: once the green reply exists,
    the blue question it answers comes off, or the captain re-reads a
    question that has been answered.  Judged by the READ-BACK, never by
    the return (AGENTS.md 5): `DeleteMarkerAtFrame` returns False for
    "there was nothing there", which is the same outcome as a
    successful delete and must not read as a failure.
    """
    key = int(source_frame)
    if key not in (item.GetMarkers() or {}):
        return False
    item.DeleteMarkerAtFrame(key)
    if key in (item.GetMarkers() or {}):
        raise MarkerWriteError(
            f"clip marker at source frame {key} on {item.GetName()!r} "
            f"is still there after DeleteMarkerAtFrame - the delete did "
            f"not land, so the answered question is still on the "
            f"timeline.")
    return True


# ── The durable record ──────────────────────────────────────────────
#
# `marker_feedback/` sits at the PROJECT ROOT, outside `pipeline_output/`.
# Everything under the output root is `Kind.OUTPUT` - "safe to delete: a
# re-run reproduces it".  A note the captain typed is not reproducible by
# anything, and the thing that destroys it is precisely a re-run: step
# 6.01 deletes any timeline whose name matches before building.  So the
# collected notes are `Kind.CAPTURED` and live where no re-run, no
# `--rerun` and no step directory rewrite reaches them.


def _attachment_identity(attachments) -> tuple:
    """The WRITTEN attachments, as an ordered tuple of paths.

    Only `custom_data` ones: a path the captain typed is already part of
    the note text, so counting it twice would make one change look like
    two.  A pull file written before attachments existed has none and
    yields `()`, which is what a note with none yields now - so nothing
    already collected is retroactively unpulled.
    """
    return tuple(sorted(
        str(a.get("path") or "") for a in (attachments or [])
        if isinstance(a, dict) and a.get("origin") == "custom_data"
        and a.get("path")
    ))


def note_identity(note: MarkerNote) -> tuple:
    """What makes two readings of the same note the same note.

    The TEXT, where it was typed, and what has been ATTACHED to it -
    deliberately not the colour, and not the mapped timeline frame, which
    moves when the edit is re-cut.  A still captured onto a note that was
    already collected makes it a note worth collecting again: the pull
    file is what survives the timeline, and without the attachment it no
    longer says everything the marker does.
    """
    return (note.source, note.name, note.note, note.frame_in_timeline_space,
            _attachment_identity(note.attachments))


def pull(project_folder, timeline=None, project=None) -> dict:
    """Collect every note off a timeline into a durable file.

    Safe to run repeatedly: each pull writes its own timestamped file and
    never overwrites an earlier one.  Safe to run while Resolve is open:
    it only reads.
    """
    if timeline is None:
        timeline, project = current_timeline()
    notes = read_notes(timeline, project_folder)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in timeline.GetName()
    ) or "timeline"
    layout = ProjectLayout(project_folder)
    # Never overwrite: two pulls inside the same second would otherwise
    # collide on the timestamp and the first one's notes would be gone,
    # which is the exact loss this whole file exists to prevent.
    path = layout.write_path(
        Area.MARKER_FEEDBACK, f"{safe}.{stamp}{PULL_FILE_SUFFIX}"
    )
    serial = 1
    while path.exists():
        serial += 1
        path = layout.write_path(
            Area.MARKER_FEEDBACK, f"{safe}.{stamp}-{serial}{PULL_FILE_SUFFIX}"
        )
    payload = {
        "format": PULL_FORMAT,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "resolve_project": project.GetName() if project else "",
        "timeline": timeline.GetName(),
        "timeline_start_frame": int(timeline.GetStartFrame()),
        "timeline_fps": _timeline_fps(timeline),
        "note_count": len(notes),
        "notes": [asdict(n) for n in notes],
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return {"path": str(path), "notes": notes, "payload": payload}


def pulled_files(project_folder, timeline_name: str = "") -> list:
    """Every pull file for this project, oldest first."""
    directory = ProjectLayout(project_folder).read_dir(Area.MARKER_FEEDBACK)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob(f"*{PULL_FILE_SUFFIX}")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if timeline_name and payload.get("timeline") != timeline_name:
            continue
        out.append((path, payload))
    return out


def _pulled_identities(project_folder, timeline_name: str) -> set:
    seen = set()
    for _, payload in pulled_files(project_folder, timeline_name):
        for raw in payload.get("notes", []):
            seen.add((
                raw.get("source", ""), raw.get("name", ""),
                raw.get("note", ""), raw.get("frame_in_timeline_space"),
                _attachment_identity(raw.get("attachments")),
            ))
    return seen


def unpulled_notes(project_folder, timeline) -> list:
    """Notes on `timeline` that no pull file for it already carries."""
    seen = _pulled_identities(project_folder, timeline.GetName())
    return [n for n in read_notes(timeline, project_folder)
            if note_identity(n) not in seen]


# ── The guard the build path calls ──────────────────────────────────
#
# REFUSE rather than warn.  A warning printed to stderr forty minutes into
# an unattended `--full-auto` build is read by nobody, and what it would
# be warning about is the one artifact in this pipeline that cannot be
# recomputed: the captain's own words, typed once.  Everything else step
# 6.01 destroys is reproducible by re-running.  The refusal also costs
# almost nothing to clear - one read-only command, two seconds - which is
# what makes refusing the proportionate choice rather than the strict one.
# The override is deliberately an explicit environment variable and not a
# flag on any default path, in the same shape as PIPELINE_SKIP_STABILIZATION.

DISCARD_ENV = "PIPELINE_DISCARD_TIMELINE_MARKERS"


def assert_markers_pulled(project_folder, timeline, quiet: bool = False) -> list:
    """Raise `UnpulledMarkers` if `timeline` carries uncollected notes.

    Returns the notes it found (empty when there is nothing to lose).
    """
    if os.environ.get(DISCARD_ENV) == "1":
        notes = read_notes(timeline) if timeline else []
        if notes and not quiet:
            print(
                f"  {DISCARD_ENV}=1: deleting timeline "
                f"{timeline.GetName()!r} with {len(notes)} uncollected "
                f"note(s). They are gone after this.",
                file=sys.stderr,
            )
        return notes
    if not project_folder:
        notes = read_notes(timeline)
        if not notes:
            return []
        raise UnpulledMarkers(
            f"Timeline {timeline.GetName()!r} carries {len(notes)} typed "
            f"note(s) and no project folder was given, so they cannot be "
            f"collected. Pass project_folder, or set {DISCARD_ENV}=1 to "
            f"delete them.",
            notes,
        )
    notes = unpulled_notes(project_folder, timeline)
    if not notes:
        return []
    preview = "\n".join(
        f"    {n.timecode or '(unplaced)'} [{n.source}] "
        + " / ".join(line for line in n.text.splitlines() if line.strip())
        for n in notes[:5] if n.text
    )
    raise UnpulledMarkers(
        f"Refusing to delete timeline {timeline.GetName()!r}: it carries "
        f"{len(notes)} typed note(s) that have not been collected, and "
        f"deleting the timeline deletes them.\n{preview}\n"
        f"  Collect them first:\n"
        f"    python3 -m library.tools.marker_feedback pull --project "
        f"{project_folder}\n"
        f"  Or set {DISCARD_ENV}=1 to discard them deliberately.",
        notes,
    )


def guard_timeline_deletion(project_folder, project, names) -> None:
    """Check every timeline step 6.01 is about to delete.

    Called with the Resolve `project` and the set of names that are about
    to go, so the refusal happens BEFORE anything is deleted.
    """
    for index in range(project.GetTimelineCount(), 0, -1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() in names:
            assert_markers_pulled(project_folder, timeline)


# ── CLI ─────────────────────────────────────────────────────────────


def _render(notes: list) -> str:
    if not notes:
        return "  (no notes on this timeline)"
    lines = []
    for n in notes:
        where = n.timecode or f"(unplaced: {n.unplaced_reason})"
        lines.append(f"  {where}  frame {n.frame}  [{n.source}]")
        for line in (n.text or "(no text)").splitlines():
            lines.append(f"      {line}")
        for att in n.attachments:
            where = att.get("resolved_path") or att.get("path") or ""
            if att.get("origin") == "note_text":
                mark = "typed by hand"
            else:
                mark = f"{att.get('kind') or 'file'} from {att.get('writer') or '?'}"
            state = "" if att.get("exists") else "  (NOT ON DISK)"
            lines.append(f"      ATTACHED [{mark}]: {where}{state}")
        for clip in n.clips:
            lines.append(
                f"      under: {clip['name']} "
                f"({clip['track_type']}{clip['track_index']}) "
                f"{clip['source_file']}"
            )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.marker_feedback",
        description="Collect typed notes off the DaVinci Resolve timeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pull = sub.add_parser(
        "pull", help="collect every note into <project>/marker_feedback/")
    p_pull.add_argument("--project", required=True,
                        help="the project folder to write the record into")

    p_show = sub.add_parser(
        "show", help="print the notes without writing anything")
    p_show.add_argument(
        "--project", default=None,
        help="the project folder an attachment's relative path resolves "
             "against. Left out, it is measured off the timeline's own "
             "footage.")

    p_check = sub.add_parser(
        "check", help="exit 2 if the open timeline has uncollected notes")
    p_check.add_argument("--project", required=True)

    args = parser.parse_args(argv)

    try:
        timeline, project = current_timeline()
    except ResolveUnavailable as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3

    if args.command == "show":
        notes = read_notes(timeline, args.project)
        attached = sum(1 for n in notes if n.attachments)
        print(f"{timeline.GetName()}: {len(notes)} note(s), "
              f"{attached} with an attachment")
        print(_render(notes))
        return 0

    if args.command == "check":
        notes = unpulled_notes(args.project, timeline)
        if not notes:
            print(f"✓ {timeline.GetName()}: every note is collected")
            return 0
        print(f"✗ {timeline.GetName()}: {len(notes)} uncollected note(s)",
              file=sys.stderr)
        print(_render(notes), file=sys.stderr)
        return 2

    result = pull(args.project, timeline, project)
    attached = sum(1 for n in result["notes"] if n.attachments)
    print(f"✓ {len(result['notes'])} note(s), {attached} with an "
          f"attachment -> {result['path']}")
    print(_render(result["notes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

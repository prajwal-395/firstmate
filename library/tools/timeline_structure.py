"""QA-001: the independent structural read-back of a WHOLE timeline.

AXI-013 (`library/tools/mutation_verification.py`) owns the rule that a
MUTATION is judged by the engine's own comparison of two fresh reads.
That comparison is a DELTA: it asks whether the timeline changed in the
ways the operations authorized.  It is not enough for a WHOLE-TIMELINE
outcome - a build, a touch-up, a multi-operation revision - because the
outcome of such a request is not a delta against one operation but a
STRUCTURE: where the clips sit, which source intervals they play, which
rows carry them, which of them are linked to their sound.

A structure can be wrong in ways a delta cannot see:

- a clip is missing, or its media is gone from the pool;
- two items on ONE row overlap, or the same item is placed twice;
- a hole opened in a picture row that was covered before;
- a clip plays a source interval that does not match the interval it
  occupies on the timeline;
- a link points at an item that is not on the timeline, or a picture and
  its sound sit on the same frames without being linked;
- the timeline that answers is not the timeline the request named.

This module is that read-back.  It is PURE: it reads SNAPSHOTS, never
Resolve, so it can be run against a scratch timeline, a recorded
generation, or a live read, and it can be tested without a running
Resolve at all.  The reader that fetches a fresh snapshot is the
caller's: `library/tools/resolve_axi_core.read_live_timeline` for a live
one, `library/tools/timeline_read.read` for a recorded one.

The AUTHORIZED shape is an EXPECTATION, and it is optional.  A build
knows what it planned (`expectation_from_placements`); a revision knows
what it changed (`library/tools/preservation_invariant.py` judges the
delta, and this module judges the structure the change left behind).  A
gap is only a DEFECT against an expectation - a picture row may have a
hole the plan asked for - so an unmeasured gap is reported as a WARNING
and never fails a gate.

`library/tools/mutation_verification.py` runs this module's verdict
beside its own delta comparison, and `resolve_axi_core` runs that on
every whole-timeline request.

THE CALIBRATION (2026-10-21)

The gate was measured against the 463 recorded timelines in the machine's
shadow store - the captain's built reels, the Ren scratch projects and the
test fixtures.  Three of its signals had to be demoted before it was quiet,
and the reasons are the engine's own: the rendered caption cards a reel
carries are DELETED after the build (`library/tools/asset_gc.py`), a STILL
and a native zero-speed HOLD both read back as a ONE-FRAME play, and a
built reel places the picture and the program audio of one clip on two rows
with no link between them.  Those three are REPORTED.  After the demotions
423 of the 463 recorded timelines are quiet, and the 40 that are not are
one fixture project whose synthetic media claims a thousand frames while
its clips play to frame 48432.

`tests/unit/resolve/test_timeline_structure.py`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: Every structural check this module can run, in a fixed order.
#:
#: The order is the order a reader wants them: WHAT the timeline is,
#: then WHAT is on it, then WHERE it sits, then whether it hangs
#: together.
CHECKS = (
    "timeline_identity",
    "track_structure",
    "missing_media",
    "overlaps",
    "gaps",
    "duplicates",
    "source_alignment",
    "linked_audio",
)

#: The severities a finding can carry.
#:
#: ``error`` - the structure is wrong.  A gate refuses it.
#: ``warning`` - the structure is odd but the request authorized it, or
#: nothing said whether it was allowed.  A gate reports it and passes.
ERROR = "error"
WARNING = "warning"

#: How far a played source interval may differ from the timeline
#: interval it occupies before it reads as MISALIGNED.
#:
#: Resolve's own source-out report is not the played edge - it disagrees
#: by a frame on about a third of clips (`library/tools/
#: timeline_ingest.py` documents the measurement), and a source recorded
#: at a different frame rate from the timeline carries a proportional
#: difference.  A tolerance of one frame OR five percent admits both
#: and refuses a real misalignment, which is a whole interval out.
ALIGNMENT_TOLERANCE_FRAMES = 1
ALIGNMENT_TOLERANCE_RATIO = 0.05


@dataclass(frozen=True)
class Clip:
    """One timeline item, in the fields a structural read needs."""

    unique_id: str
    name: str
    record_in: int
    record_out: int
    source_in: int | None = None
    source_out: int | None = None
    source_frames: int | None = None
    source_file: str = ""
    media_pool_item_id: str = ""
    linked_ids: tuple[str, ...] = ()
    media_type: str = "video"
    track_type: str = "video"
    track_index: int = 1
    speed: float | None = None
    still: bool = False

    @property
    def duration(self) -> int:
        return int(self.record_out) - int(self.record_in)

    @property
    def source_duration(self) -> int | None:
        if self.source_in is None or self.source_out is None:
            return None
        return int(self.source_out) - int(self.source_in)

    def as_dict(self) -> dict:
        return {
            "unique_id": self.unique_id,
            "name": self.name,
            "record_in": self.record_in,
            "record_out": self.record_out,
            "source_in": self.source_in,
            "source_out": self.source_out,
            "source_frames": self.source_frames,
            "source_file": self.source_file,
            "media_pool_item_id": self.media_pool_item_id,
            "linked_ids": list(self.linked_ids),
            "media_type": self.media_type,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "speed": self.speed,
        }


@dataclass(frozen=True)
class Row:
    """One typed timeline row, in Resolve row order."""

    media_type: str
    index: int
    name: str = ""
    clips: tuple[Clip, ...] = ()

    @property
    def key(self) -> tuple[str, int]:
        return (self.media_type, int(self.index))


@dataclass(frozen=True)
class Structure:
    """A whole timeline, read back."""

    timeline_name: str = ""
    project_name: str = ""
    rows: tuple[Row, ...] = ()
    media_errors: tuple[str, ...] = ()

    @property
    def clips(self) -> tuple[Clip, ...]:
        return tuple(clip for row in self.rows for clip in row.clips)

    def row(self, media_type: str, index: int) -> Row | None:
        for row in self.rows:
            if row.key == (media_type, int(index)):
                return row
        return None


@dataclass(frozen=True)
class Finding:
    """One structural defect, named."""

    check: str
    detail: str
    subject: str = ""
    severity: str = ERROR

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "detail": self.detail,
            "subject": self.subject,
            "severity": self.severity,
        }


@dataclass
class Report:
    """The verdict of one structural read-back."""

    findings: list[Finding] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings
                if finding.severity == ERROR]

    @property
    def passed(self) -> bool:
        """A report passes when nothing in it is an ERROR.

        A WARNING is a report, not a refusal: an unmeasured gap or an
        odd row name is named, and never blocks a build.
        """
        return not self.errors

    def add(self, check: str, detail: str, subject: str = "",
            severity: str = ERROR) -> None:
        self.findings.append(Finding(check=check, detail=detail,
                                     subject=subject, severity=severity))

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [dict(check) for check in self.checks],
            "findings": [finding.as_dict() for finding in self.findings],
            "errors": len(self.errors),
            "warnings": len(self.findings) - len(self.errors),
        }


# ── Reading a structure out of the two shapes the engine holds ─────────


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def structure_from_snapshot(snapshot: Mapping) -> Structure:
    """Read a structure out of a SHADOW snapshot.

    The shadow's shape is `{"tracks": [{"type", "index", "name",
    "clips": [...]}]}` with the clip fields `timeline_shadow.
    _LIGHT_CLIP_KEYS` names.
    """
    rows: list[Row] = []
    for track in snapshot.get("tracks", ()) or ():
        clips: list[Clip] = []
        for clip in track.get("clips", ()) or ():
            linked = clip.get("linked_unique_ids") or ()
            clips.append(Clip(
                unique_id=str(clip.get("unique_id") or ""),
                name=str(clip.get("name") or ""),
                record_in=int(clip.get("record_in") or 0),
                record_out=int(clip.get("record_out") or 0),
                source_in=_int(clip.get("source_in_frame")),
                source_out=_int(clip.get("source_out_frame")),
                source_frames=_int(clip.get("source_frames")),
                source_file=str(clip.get("source_file") or ""),
                media_pool_item_id=str(clip.get("media_pool_item_id") or ""),
                linked_ids=tuple(str(value) for value in linked),
                media_type=str(clip.get("media_type")
                               or track.get("type") or "video"),
                track_type=str(track.get("type") or "video"),
                track_index=int(track.get("index") or 1),
                speed=(None if clip.get("speed") is None
                       else float(clip.get("speed"))),
            ))
        rows.append(Row(
            media_type=str(track.get("type") or "video"),
            index=int(track.get("index") or 1),
            name=str(track.get("name") or ""),
            clips=tuple(clips)))
    return Structure(
        timeline_name=str((snapshot.get("timeline") or {}).get("name", "")
                          if isinstance(snapshot.get("timeline"), Mapping)
                          else snapshot.get("timeline") or ""),
        project_name=str(snapshot.get("project") or ""),
        rows=tuple(rows))


def structure_from_inventory(rows: Sequence[Mapping], *,
                             timeline_name: str = "",
                             project_name: str = "") -> Structure:
    """Read a structure out of a BUILD INVENTORY.

    `library/tools/reel_append_placement.read_inventory` reads every
    item on the timeline once and reports the fields a structural read
    needs, including the media errors it measured.  The build path
    already holds one of those, so the read-back costs no second pass
    over Resolve.
    """
    grouped: dict[tuple[str, int], list[Clip]] = {}
    media_errors: list[str] = []
    for row in rows or ():
        media_type = str(row.get("track_type") or "video")
        index = int(row.get("track_index") or 1)
        linked = row.get("linked_ids") or ()
        grouped.setdefault((media_type, index), []).append(Clip(
            unique_id=str(row.get("unique_id") or ""),
            name=str(row.get("name") or ""),
            record_in=int(row.get("record_in") or 0),
            record_out=int(row.get("record_out") or 0),
            source_in=_int(row.get("source_in")),
            source_out=_int(row.get("source_out")),
            source_file=str(row.get("source_file") or ""),
            media_pool_item_id=str(row.get("media_pool_item_id") or ""),
            linked_ids=tuple(str(value) for value in linked),
            media_type=media_type,
            track_type=media_type,
            track_index=index,
            still=bool(row.get("still")),
        ))
        for error in row.get("media_errors", ()) or ():
            if error not in media_errors:
                media_errors.append(str(error))
    built = tuple(
        Row(media_type=media_type, index=index, name="",
            clips=tuple(sorted(clips, key=lambda clip: clip.record_in)))
        for (media_type, index), clips in sorted(grouped.items()))
    return Structure(timeline_name=timeline_name, project_name=project_name,
                     rows=built, media_errors=tuple(media_errors))


# ── The authorized shape ────────────────────────────────────────────────


def expectation_from_placements(placements: Sequence[Mapping],
                                deleted_tracks: Sequence[Mapping] = ()) -> dict:
    """The coverage a plan authorized, per row.

    A placement names the row it lands on and the timeline interval it
    occupies.  That is the expectation: which frames of which row the
    plan said would be covered.  The engine's read-back compares the
    timeline against it, so a hole in a picture row the plan filled is a
    gap, and a hole the plan left is not.

    `deleted_tracks` are the rows the build removed because nothing
    landed on them.  They shift every LATER row down by one, so the
    expectation is stated in the row indices the timeline actually
    carries - the same shift `reel_append_placement.compare_inventory`
    applies to the planned rows.
    """
    removed: dict[str, list[int]] = {}
    for deleted in deleted_tracks or ():
        media_type = str(deleted.get("media_type") or "video")
        removed.setdefault(media_type, []).append(int(deleted.get("index") or 0))
    rows: dict[str, dict[int, list[tuple[int, int]]]] = {}
    for placement in placements or ():
        media_type = str(placement.get("track_type") or "video")
        index = int(placement.get("track_index") or 1)
        index -= sum(1 for gone in removed.get(media_type, ())
                     if gone < index)
        start = int(placement.get("record_in") or 0)
        end = int(placement.get("record_out") or 0)
        if end <= start:
            continue
        rows.setdefault(media_type, {}).setdefault(index, []).append(
            (start, end))
    return {"rows": rows}


def _merge(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge touching intervals into the covered runs they form."""
    ordered = sorted((int(start), int(end))
                     for start, end in intervals if int(end) > int(start))
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _holes(covered: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """The holes INSIDE a covered run - a row's own uncovered frames."""
    holes: list[tuple[int, int]] = []
    for first, second in zip(covered, covered[1:]):
        if second[0] > first[1]:
            holes.append((first[1], second[0]))
    return holes


def _covered_frames(covered: Sequence[tuple[int, int]]) -> set[int]:
    return {frame for start, end in covered for frame in range(start, end)}


def _runs(frames: Sequence[int]) -> list[tuple[int, int]]:
    """The maximal runs of consecutive frames, as half-open spans."""
    runs: list[tuple[int, int]] = []
    for frame in frames:
        if runs and frame == runs[-1][1]:
            runs[-1] = (runs[-1][0], frame + 1)
        else:
            runs.append((frame, frame + 1))
    return runs


# ── The read-back ───────────────────────────────────────────────────────


def _check_identity(report: Report, structure: Structure,
                    expected_timeline: str) -> None:
    """The timeline that answered is the timeline the request named."""
    if expected_timeline and structure.timeline_name and (
            structure.timeline_name != expected_timeline):
        report.add("timeline_identity",
                   f"the request named {expected_timeline!r} and the "
                   f"timeline that answered is {structure.timeline_name!r}",
                   subject=structure.timeline_name)
    seen: dict[str, int] = {}
    for clip in structure.clips:
        if not clip.unique_id:
            continue
        seen[clip.unique_id] = seen.get(clip.unique_id, 0) + 1
    for unique_id, count in sorted(seen.items()):
        if count > 1:
            report.add("timeline_identity",
                       f"{unique_id} appears {count} times on the timeline",
                       subject=unique_id)


def _check_track_structure(report: Report, structure: Structure) -> None:
    """Rows are distinct, and they are named."""
    seen_keys: dict[tuple[str, int], int] = {}
    seen_names: dict[tuple[str, str], int] = {}
    for row in structure.rows:
        seen_keys[row.key] = seen_keys.get(row.key, 0) + 1
        if row.name:
            name_key = (row.media_type, row.name)
            seen_names[name_key] = seen_names.get(name_key, 0) + 1
    for key, count in sorted(seen_keys.items()):
        if count > 1:
            report.add("track_structure",
                       f"{key[0]} row {key[1]} appears {count} times",
                       subject=f"{key[0]}{key[1]}")
    for (media_type, name), count in sorted(seen_names.items()):
        if count > 1:
            report.add("track_structure",
                       f"{count} {media_type} rows are named {name!r}",
                       subject=name)


def _check_missing_media(report: Report, structure: Structure, *,
                          media_on_disk: bool,
                          before: Structure | None) -> None:
    """Every item's media is still there.

    Two of the three signals are ERRORS and one is a WARNING, and the
    measured reason is the engine's own asset lifecycle: the rendered
    caption cards a reel carries are DELETED after the build
    (`library/tools/asset_gc.py`), so a built reel legitimately
    references files that are no longer on disk.  A file that is gone
    is therefore REPORTED, never fatal on its own.

    What IS fatal: media the build MEASURED as missing (the
    inventory's own media errors), and a clip that carried media and
    now carries none at all.
    """
    before_media = {}
    if before is not None:
        before_media = {clip.unique_id: clip.source_file
                        for clip in before.clips if clip.unique_id}
    for error in structure.media_errors:
        report.add("missing_media", str(error))
    for clip in structure.clips:
        if not clip.source_file and before_media.get(clip.unique_id):
            report.add("missing_media",
                       f"{clip.unique_id} carried "
                       f"{before_media[clip.unique_id]} and now carries no "
                       "media at all", subject=clip.unique_id)
            continue
        if not clip.source_file:
            continue
        if media_on_disk and os.path.isabs(clip.source_file) and not (
                os.path.isfile(clip.source_file)):
            report.add("missing_media",
                       f"{clip.unique_id} plays {clip.source_file}, which is "
                       "not on disk", subject=clip.unique_id, severity=WARNING)


def _check_overlaps(report: Report, structure: Structure) -> None:
    """Two items on ONE row never occupy the same frames."""
    for row in structure.rows:
        clips = sorted(row.clips, key=lambda clip: (clip.record_in,
                                                     clip.record_out))
        for first, second in zip(clips, clips[1:]):
            if second.record_in < first.record_out:
                report.add("overlaps",
                           f"{first.unique_id} and {second.unique_id} both "
                           f"occupy {row.media_type} row {row.index} at "
                           f"{second.record_in}", subject=second.unique_id)


def _check_duplicates(report: Report, structure: Structure) -> None:
    """The same placement, twice, on one row.

    An overlap is two items whose spans CROSS.  A duplicate is two
    items on the SAME frames - the placement that ran once per clip
    PER CLIP, and the one that put the same card on a row twice.
    """
    for row in structure.rows:
        seen: dict[tuple[int, int], str] = {}
        for clip in row.clips:
            key = (clip.record_in, clip.record_out)
            if key in seen:
                report.add("duplicates",
                           f"{seen[key]} and {clip.unique_id} are placed on "
                           f"the same frames of {row.media_type} row "
                           f"{row.index}", subject=clip.unique_id)
            else:
                seen[key] = clip.unique_id


def _check_source_alignment(report: Report, structure: Structure) -> None:
    """A clip plays a source interval its media can actually supply.

    Two clocks meet here.  A SOURCE frame is a frame of the file, and a
    RECORD frame is a frame of the timeline, and a file at 25 frames a
    second on a timeline at 24 plays a different NUMBER of frames than
    it occupies - so the LENGTHS of the two intervals are not compared
    as an error.  What IS compared is what the media can supply: a
    clip that starts before its file begins, or plays past its end, is
    reading frames that are not there, and that is the defect the
    catalog measured (`library/tools/timeline_ingest.py`).

    A one-frame play is not a defect.  A STILL plays one source frame,
    and a native HOLD plays one source frame at zero speed; both read
    back with the same source frame at both ends, and refusing them
    would refuse every reel the engine has ever built.
    """
    for clip in structure.clips:
        if clip.source_in is None or clip.source_out is None:
            continue
        if clip.source_out < clip.source_in:
            report.add("source_alignment",
                       f"{clip.unique_id} plays source {clip.source_in}.."
                       f"{clip.source_out}, which is not a range",
                       subject=clip.unique_id)
            continue
        if clip.source_in < 0:
            report.add("source_alignment",
                       f"{clip.unique_id} starts at source frame "
                       f"{clip.source_in}, before the file begins",
                       subject=clip.unique_id)
        if (clip.source_frames and clip.source_out > clip.source_frames
                and not _plays_one_frame(clip)):
            report.add("source_alignment",
                       f"{clip.unique_id} plays {clip.source_file} to frame "
                       f"{clip.source_out}, and the media pool reports only "
                       f"{clip.source_frames} frames in it",
                       subject=clip.unique_id)
        if clip.duration <= 0:
            report.add("source_alignment",
                       f"{clip.unique_id} occupies no frames on the timeline",
                       subject=clip.unique_id)
            continue
        if clip.still or _plays_one_frame(clip):
            continue
        if clip.speed is not None and abs(float(clip.speed) - 100.0) > 0.5:
            # A retimed clip plays a different number of source frames
            # than it occupies on the timeline, and that is the edit.
            continue
        source_duration = clip.source_duration or 0
        difference = abs(source_duration - clip.duration)
        if difference <= ALIGNMENT_TOLERANCE_FRAMES:
            continue
        if difference <= ALIGNMENT_TOLERANCE_RATIO * clip.duration:
            continue
        report.add("source_alignment",
                   f"{clip.unique_id} plays {source_duration} source frames "
                   f"and occupies {clip.duration} timeline frames",
                   subject=clip.unique_id, severity=WARNING)


def _plays_one_frame(clip: Clip) -> bool:
    """Whether this clip plays ONE source frame - a still or a hold.

    A still image and a native zero-speed hold both read back with the
    same source frame at both ends, and both are real, working items on
    a real reel.
    """
    if str(clip.media_type or "").strip().lower() == "still":
        return True
    if clip.speed is not None and float(clip.speed) <= 0.5:
        return True
    return (clip.source_in is not None and clip.source_out is not None
            and int(clip.source_out) == int(clip.source_in))


def _check_linked_audio(report: Report, structure: Structure) -> None:
    """A link points at an item that is there, and a pair is linked."""
    by_id = {clip.unique_id: clip for clip in structure.clips}
    for clip in structure.clips:
        for linked in clip.linked_ids:
            if linked and linked not in by_id:
                report.add("linked_audio",
                           f"{clip.unique_id} is linked to {linked}, which is "
                           "not on the timeline", subject=clip.unique_id)
    video = [clip for clip in structure.clips
             if clip.track_type == "video" and clip.duration > 0]
    audio = [clip for clip in structure.clips
             if clip.track_type == "audio" and clip.duration > 0]
    for picture in video:
        for sound in audio:
            if (picture.record_in, picture.record_out) != (
                    sound.record_in, sound.record_out):
                continue
            if not picture.source_file or (
                    picture.source_file != sound.source_file):
                # The picture and the sound of ONE clip are the same
                # media on two rows.  A build also places a SPILL of
                # the program audio beside the program stream, and the
                # spill is deliberately unlinked - but the spill is the
                # same file as the PROGRAM audio, never the file of the
                # picture, so this pairing is not one of them.
                continue
            if (sound.unique_id in picture.linked_ids
                    or picture.unique_id in sound.linked_ids):
                continue
            if picture.linked_ids or sound.linked_ids:
                # One of them is linked already, and a build places a
                # SPILL of the program audio beside it on another row,
                # deliberately unlinked.  Only a pair where NEITHER
                # side is linked is a pair that should have been.
                continue
            report.add("linked_audio",
                       f"{picture.unique_id} and {sound.unique_id} occupy the "
                       "same frames and are not linked to each other",
                       subject=sound.unique_id, severity=WARNING)


def _check_gaps(report: Report, structure: Structure, *,
                expectation: Mapping | None,
                before: Structure | None,
                authorized_spans: Sequence[Sequence[int]],
                authorized_holes: Sequence[Sequence[int]]) -> None:
    """The frames a row covers, against the frames it was told to cover.

    A gap is a DEFECT against an expectation.  With one, the timeline's
    coverage is compared with the plan's, frame for frame: a frame the
    plan covered and the timeline does not is a gap.

    Without one, a gap is COVERAGE THAT WAS LOST - frames the row
    covered before the write and covers no more.  A row that now
    reaches FURTHER than it did lost nothing, and a row that lost the
    frames an authorized removal took away lost what it was told to
    lose: the authorization is named by the item's own span in the
    state before the write, not by a guess about the row.
    """
    expected_rows = (expectation or {}).get("rows") or {}
    before_rows = {}
    if before is not None:
        before_rows = {row.key: _merge(
            [(clip.record_in, clip.record_out) for clip in row.clips])
            for row in before.rows}
    spans = [(int(start), int(end)) for start, end in authorized_spans or ()]
    permitted = [(int(start), int(end))
                 for start, end in authorized_holes or ()]
    for row in structure.rows:
        covered = _merge([(clip.record_in, clip.record_out)
                          for clip in row.clips])
        planned = _merge(expected_rows.get(row.media_type, {}).get(
            row.index, []))
        if expectation is not None:
            actual_frames = _covered_frames(covered)
            planned_frames = _covered_frames(planned)
            missing = sorted(planned_frames - actual_frames)
            extra = sorted(actual_frames - planned_frames)
            if missing:
                report.add("gaps",
                           f"{row.media_type} row {row.index} is missing "
                           f"{len(missing)} planned frames "
                           f"(from {missing[0]})", subject=f"{row.media_type}"
                           f"{row.index}")
            if extra:
                report.add("gaps",
                           f"{row.media_type} row {row.index} covers "
                           f"{len(extra)} frames the plan did not authorize "
                           f"(from {extra[0]})", subject=f"{row.media_type}"
                           f"{row.index}", severity=WARNING)
            continue
        previous = before_rows.get(row.key, [])
        if not previous:
            continue
        was_covered = _covered_frames(previous)
        now_covered = _covered_frames(covered)
        lost = sorted(was_covered - now_covered)
        if not lost:
            continue
        for start, end in _runs(lost):
            authorized = any(start < span_end and span_start <= end
                             for span_start, span_end in spans + permitted)
            if authorized:
                continue
            report.add(
                "gaps",
                f"{row.media_type} row {row.index} no longer covers "
                f"{start}..{end}, which it covered before",
                subject=f"{row.media_type}{row.index}")


def analyze(structure: Structure, *, expectation: Mapping | None = None,
            before: Structure | None = None,
            authorized_spans: Sequence[Sequence[int]] = (),
            authorized_holes: Sequence[Sequence[int]] = (),
            expected_timeline: str = "",
            media_on_disk: bool = True) -> Report:
    """Read one whole timeline back and report what is wrong with it."""
    report = Report()
    _check_identity(report, structure, expected_timeline)
    _check_track_structure(report, structure)
    _check_missing_media(report, structure, media_on_disk=media_on_disk,
                         before=before)
    _check_overlaps(report, structure)
    _check_duplicates(report, structure)
    _check_source_alignment(report, structure)
    _check_linked_audio(report, structure)
    _check_gaps(report, structure, expectation=expectation, before=before,
                authorized_spans=authorized_spans,
                authorized_holes=authorized_holes)
    for check in CHECKS:
        findings = [finding for finding in report.findings
                    if finding.check == check]
        report.checks.append({
            "check": check,
            "passed": not any(finding.severity == ERROR
                              for finding in findings),
            "findings": len(findings),
        })
    return report


def compare(before: Mapping | None, after: Mapping, **kwargs) -> Report:
    """Read TWO whole timelines back and judge the change between them.

    `before` is the state the change was planned against and `after` a
    fresh read of the state it left behind.  The comparison is what a
    whole-timeline outcome is judged by: a hole that opened, an item
    that vanished, a media that went missing.
    """
    after_structure = structure_from_snapshot(after)
    before_structure = (structure_from_snapshot(before)
                        if before is not None else None)
    return analyze(after_structure, before=before_structure, **kwargs)


def structure_report(before: Mapping | None, after: Mapping,
                     **kwargs) -> dict:
    """The report of a comparison, in the shape a gate reads."""
    return compare(before, after, **kwargs).as_dict()

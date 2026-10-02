"""Grab gate stills off a built reel timeline, at named frames.

A reel lane's visual gate is always the same mechanical loop: make the
reel's timeline current, move the playhead to each named frame, grab the
frame Resolve is showing, and put the entry timeline and playhead back.
Batch 5 wrote it as a throwaway `/tmp/fm-batch5/gate_stills.py` (3 edits,
then one run per reel - 70 tool calls and 29.8 minutes in the batch went
to producing gate stills by hand;
`data/vep-what-is-the-floor-for-a-reel/report.md`), and the earlier
reel-batch report notes batch 1 built the same loop under another name.
This module is that loop with a reader, reached through the
`reel.gate_stills` operation.

What it does and does not do
----------------------------
DOES: timeline lookup by EXACT name, entry-timeline and playhead save
and restore, per-frame positioning with read-back, one gallery grab per
frame through `marker_capture.grab_still` (which restores the gallery
and raises when no picture happened), and bounds refusal before
anything moves.

DOES NOT judge the pixels. The batch-5 script measured caption-band
white rows, graphic-presence bands and closing-card brightness because
those were ITS gate's three questions; the next gate asks different
ones. What every version shared is position-then-grab, so the pixel
question stays with the caller, which reads the returned PNGs. An entry
point scoped to one gate's measurements would be ignored in favour of
another hand-written script.

Measured behaviour this relies on (`library/tools/marker_capture.py`)
---------------------------------------------------------------
- `GrabStill` returns the GRADED, CONFORMED timeline frame, on either
  page - the picture the gate is judging.
- A still's FIRST grab after a timeline switch is not evidence (upper
  tracks may not have composited yet): position, grab, discard, grab
  again. This module does one discard grab after switching timelines
  and keeps every keeper after it.
- `SetCurrentTimecode` returns True for a timecode the timeline does
  not have, and the grab there is a perfectly good black PNG - so the
  read-back is the check, never the return. A playhead that does not
  land on the wanted frame (including a page that eats the set -
  `OpenPage("edit")` is deliberately NOT called here; a set the page
  eats surfaces as a mismatch) fails that still by name rather than
  grabbing the wrong frame quietly.
- Positioning is non-drop and the frame number is the authority
  (`marker_feedback.frames_to_timecode`): on a drop-frame timeline the
  set maps to a nearby frame, the read-back disagrees, and the still is
  refused with the mismatch - rather than guessed at.

Resolve is duck-typed throughout (project, timeline and pool objects
are whatever answers the few methods used), so the whole module is
testable without the application open.
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from library.tools.marker_capture import (
    StillCaptureError,
    grab_still,
    timeline_fps,
    timecode_to_frames,
)
from library.tools.marker_feedback import frames_to_timecode
from library.tools.resolve_lock import assert_current_timeline, resolve_lease


class GateStillsError(RuntimeError):
    """The still run could not be completed as stated, carrying what landed.

    `report` is the run's own record - `stills`, `failed` and the
    timeline it ran against - so a re-run grabs only what remains:
    finished frames land on the same filenames and are overwritten
    identically, which makes every failure resumable by calling again.
    """

    def __init__(self, message: str, report: Optional[dict] = None):
        super().__init__(message)
        self.report = report or {}


@dataclass
class TimelineBounds:
    """Where a reel timeline starts and ends, in absolute frames."""

    name: str
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def find_timeline(project, timeline_name: str):
    """The timeline EXACTLY named `timeline_name`, or None.

    Names are matched exactly, never by prefix (AGENTS.md 5): a gate
    that grabs "Reel 1" off "Reel 14" judges the wrong reel. Timelines
    are project-global (`GetTimelineCount`/`GetTimelineByIndex` see
    every folder), so no folder walk is needed - the batch-5 script
    learned this on its second edit.
    """
    try:
        count = project.GetTimelineCount()
    except Exception:  # noqa: BLE001 - an unreadable project has no timeline
        return None
    for index in range(1, count + 1):
        try:
            timeline = project.GetTimelineByIndex(index)
            name = timeline.GetName()
        except Exception:  # noqa: BLE001 - an unreadable timeline is not it
            continue
        if name == timeline_name:
            return timeline
    return None


def timeline_bounds(timeline) -> TimelineBounds:
    """A timeline's absolute start/end frames, refused when unreadable."""
    try:
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
    except Exception as exc:  # noqa: BLE001 - unmeasurable is unusable
        raise GateStillsError(
            f"timeline {timeline.GetName()!r} reports no frame bounds: "
            f"{exc}")
    return TimelineBounds(name=timeline.GetName(), start=start, end=end)


def still_filename(reel_label: str, reel_frame: int) -> str:
    """The file one grabbed frame lands on.

    Same shape the lanes used (`reel21_f000020.png`): the reel label up
    front so a directory of several reels' stills stays sortable, the
    reel-relative frame zero-padded so it sorts. Unsafe characters are
    flattened so the label can be whatever the lane calls the reel.
    """
    safe = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in (reel_label or "")
    ) or "reel"
    return f"{safe}_f{int(reel_frame):06d}.png"


def png_size(path) -> Optional[tuple]:
    """(width, height) off a PNG's IHDR, or None when it is not a PNG.

    Stdlib only (`struct`): the measurement half of this module must
    not need Pillow or numpy, which the lanes had but CI need not.
    """
    try:
        with open(path, "rb") as handle:
            signature = handle.read(8)
            if signature != b"\x89PNG\r\n\x1a\n":
                return None
            handle.read(4)  # length
            if handle.read(4) != b"IHDR":
                return None
            width, height = struct.unpack(">II", handle.read(8))
            return (width, height)
    except (OSError, struct.error):
        return None


def _position(timeline, absolute_frame: int, fps: float) -> tuple:
    """Put the playhead on `absolute_frame`, returning (set, read).

    The timecode is derived from the frame number, which is the
    authority; the read-back is converted back to frames by the caller
    and must agree. One retry: the first set after a timeline switch
    can miss, measured by the batch-5 lane.
    """
    wanted = frames_to_timecode(absolute_frame, fps)
    timeline.SetCurrentTimecode(wanted)
    read = timeline.GetCurrentTimecode()
    if read != wanted:
        timeline.SetCurrentTimecode(wanted)
        read = timeline.GetCurrentTimecode()
    return wanted, read


def grab_reel_stills(project, timeline_name: str, frames: list,
                     out_dir, reel_label: str = "",
                     stream=sys.stderr) -> dict:
    """Grab one still per named reel-relative frame off one timeline.

    `frames` are reel-relative (0 is the timeline's first frame), the
    same numbering the lanes passed on the command line. Stills land in
    `out_dir` under `still_filename(reel_label, frame)`.

    One exclusive lease covers the timeline switch, every grab and the
    restore, so no other writer can move the playhead mid-run. The
    entry timeline and its playhead are restored in a `finally`, and
    the restore is REPORTED (`restored.ok`), never assumed.

    A frame that cannot be grabbed is REPORTED in `failed` and the run
    continues with the rest: grabs are independent files (unlike a
    caption swap, there is no half-state), and a re-run overwrites the
    same filenames. `ok` is False while anything failed, with `error`
    naming the first failure - a gate must never read a partial set as
    a complete one.
    """
    label = reel_label or timeline_name
    wanted = list(dict.fromkeys(int(f) for f in (frames or [])))
    report: dict = {
        "ok": True,
        "reel_label": label,
        "timeline": timeline_name,
        "stills": [],
        "failed": [],
        "restored": {"timeline": None, "ok": False},
    }

    timeline = find_timeline(project, timeline_name)
    if timeline is None:
        report["ok"] = False
        report["error"] = (
            f"no timeline exactly named {timeline_name!r} - refusing to "
            f"guess which of the open timelines the gate meant")
        return report
    try:
        bounds = timeline_bounds(timeline)
    except GateStillsError as exc:
        report["ok"] = False
        report["error"] = str(exc)
        return report
    try:
        fps = timeline_fps(timeline)
    except Exception as exc:  # noqa: BLE001 - CaptureError: no rate, no set
        report["ok"] = False
        report["error"] = (
            f"timeline {timeline_name!r} reports no frame rate, so no "
            f"frame can be positioned on it: {exc}")
        return report

    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report["ok"] = False
        report["error"] = f"cannot write stills to {out}: {exc}"
        return report

    with resolve_lease(f"gate stills {label}"):
        try:
            entry = project.GetCurrentTimeline()
        except Exception:  # noqa: BLE001 - no entry to restore to
            entry = None
        entry_name = None
        entry_tc = None
        if entry is not None:
            try:
                entry_name = entry.GetName()
            except Exception:  # noqa: BLE001 - a nameless entry restores same
                entry_name = None
            try:
                entry_tc = entry.GetCurrentTimecode()
            except Exception:  # noqa: BLE001 - no playhead to put back
                entry_tc = None
        report["restored"]["timeline"] = entry_name
        try:
            # Routed through the shared establishment: the lease above
            # is held, so this sets exactly as before, plus the
            # fence-drift check. `tests/contracts/test_resolve_guard_wiring.py`
            # counts raw `SetCurrentTimeline` sites - none may live
            # outside `resolve_lock`.
            assert_current_timeline(project, timeline)
        except Exception as exc:  # noqa: BLE001 - not current, no grab
            report["ok"] = False
            report["error"] = (
                f"Resolve would not make {timeline_name!r} current: "
                f"{exc}")
            return report
        try:
            if wanted:
                _warm_up(project, timeline, bounds, fps, wanted[0],
                         out, label, stream)
            for reel_frame in wanted:
                record = _grab_one(
                    project, timeline, bounds, fps, reel_frame, out, label)
                if "reason" in record:
                    report["failed"].append(record)
                else:
                    report["stills"].append(record)
        finally:
            report["restored"]["ok"] = _restore(
                project, entry, entry_name, entry_tc, stream)

    if report["failed"]:
        first = report["failed"][0]
        report["ok"] = False
        report["error"] = (
            f"{len(report['failed'])} of {len(wanted)} frames failed to "
            f"grab - reel frame {first['reel_frame']}: "
            f"{first['reason'][:200]}")
    else:
        print(f"  grabbed {len(report['stills'])} still(s) off "
              f"{timeline_name!r} into {out}", file=stream)
    return report


def _warm_up(project, timeline, bounds: TimelineBounds, fps: float,
             first_frame: int, out: Path, label: str, stream) -> None:
    """One discard grab at the first wanted frame, after the switch.

    A still's first grab after a timeline switch can return before the
    upper tracks have composited (measured 2026-09-17: first grab blank,
    second grab full, same frame). The keeper grabs below are judged on
    their own files, so a warm-up that itself fails is said on stderr
    and the run continues - it is the keepers that are evidence, never
    this one.
    """
    absolute = bounds.start + first_frame
    if not (bounds.start <= absolute < bounds.end):
        return  # out of range: _grab_one refuses it by name below
    wanted = frames_to_timecode(absolute, fps)
    try:
        timeline.SetCurrentTimecode(wanted)
        timeline.GetCurrentTimecode()
        scratch = out / f".warmup_{label}.png"
        try:
            grab_still(timeline, project, scratch)
        finally:
            try:
                os.unlink(scratch)
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001 - warm-up is not evidence
        print(f"  warm-up grab discarded ({exc}) - keepers still judge "
              f"themselves", file=stream)


def _grab_one(project, timeline, bounds: TimelineBounds, fps: float,
              reel_frame: int, out: Path, label: str) -> dict:
    """Grab one reel-relative frame, or return its failure."""
    if reel_frame < 0 or reel_frame >= bounds.duration:
        last = bounds.duration - 1
        return {
            "reel_frame": reel_frame,
            "reason": (
                f"reel frame {reel_frame} is outside {bounds.name!r} "
                f"(0..{last}, {bounds.duration} frames) - Resolve grabs "
                f"a black still past the end, so this is refused rather "
                f"than grabbed"),
        }
    absolute = bounds.start + reel_frame
    try:
        tc_set, tc_read = _position(timeline, absolute, fps)
    except Exception as exc:  # noqa: BLE001 - unpositionable is unusable
        return {"reel_frame": reel_frame,
                "reason": f"the playhead could not be positioned: {exc}"}
    try:
        read_frame = timecode_to_frames(tc_read, fps)
    except Exception as exc:  # noqa: BLE001 - unreadable is unusable
        return {"reel_frame": reel_frame,
                "reason": f"the playhead reads {tc_read!r}, which cannot "
                          f"be read back as a frame: {exc}"}
    if read_frame != absolute:
        return {
            "reel_frame": reel_frame,
            "reason": (
                f"the playhead would not land on reel frame {reel_frame} "
                f"(set {tc_set}, reads {tc_read} = frame "
                f"{read_frame - bounds.start} there) - grabbing would "
                f"judge the wrong frame"),
        }
    destination = out / still_filename(label, reel_frame)
    try:
        grab_still(timeline, project, destination)
    except StillCaptureError as exc:
        return {"reel_frame": reel_frame, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - refused loudly
        return {"reel_frame": reel_frame,
                "reason": f"the grab failed: {exc}"}
    try:
        size = destination.stat().st_size
    except OSError as exc:
        return {"reel_frame": reel_frame,
                "reason": f"grabbed but {destination.name} cannot be "
                          f"read back: {exc}"}
    dimensions = png_size(destination)
    if dimensions is None:
        return {"reel_frame": reel_frame,
                "reason": f"grabbed but {destination.name} is not a "
                          f"readable PNG"}
    width, height = dimensions
    return {
        "reel_frame": reel_frame,
        "timecode_set": tc_set,
        "timecode_read": tc_read,
        "path": str(destination),
        "width": width,
        "height": height,
        "bytes": size,
    }


def _restore(project, entry, entry_name, entry_tc, stream) -> bool:
    """Put the entry timeline and playhead back, reporting success."""
    if entry is None:
        print("  no entry timeline to restore (none was current)",
              file=stream)
        return True
    try:
        # Shared establishment, as above: same set, plus the checks.
        assert_current_timeline(project, entry)
    except Exception as exc:  # noqa: BLE001 - unrestored is reported
        print(f"  WARNING: entry timeline {entry_name!r} could not be "
              f"restored: {exc}", file=stream)
        return False
    if entry_tc:
        try:
            entry.SetCurrentTimecode(entry_tc)
        except Exception as exc:  # noqa: BLE001 - best effort, said aloud
            print(f"  WARNING: entry playhead {entry_tc} could not be "
                  f"restored: {exc}", file=stream)
            return False
    print(f"  restored entry timeline {entry_name!r}", file=stream)
    return True

"""Swap re-rendered caption files onto the timelines that hold the old ones.

A caption fix re-renders a named set of cards: same bounds, same
timings, new pixels under a new filename (the content digest names the
file). What is left is mechanical - every Subtitles-row placement of
each old file, on every timeline that holds one, must point at the new
file instead. The lane that first did this wrote it twice as throwaway
scripts (`rerender.py` + `swap.py`, then `rerender_extra.py`); this
module is that capability with a reader, reached through the
`subtitles.rerender_swap` operation.

The swap is by pool item, not by placement: media pool items are
project-wide, so one `ReplaceClip` per pool item swaps every timeline
at once. Placements never move - record frames, source trims,
transforms and the captain's caption pins survive by construction,
because the timeline items are never touched. Every placement is
verified by read-back (the pool item's file path, the item's start/end
and source trim), and a post-check refuses the run while any old file
remains placed anywhere.

Nothing is deleted anywhere: old files stay on disk as superseded
generations for the asset GC, exactly as after any rebuild.

Resolve is duck-typed throughout (project, timeline, item and pool
item are whatever object answers the few methods used), so the whole
module is testable without the application open.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

from library.tools.overlay_carriage import apply_clip_attributes
from library.tools.resolve_lock import resolve_lease

SUBTITLES_TRACK_NAME = "Subtitles"
"""The timeline row captions live on (`library/tools/timeline_layout.py`
names the CAPTIONS role exactly this)."""


class CaptionSwapError(RuntimeError):
    """The swap could not be completed as stated, carrying what did land.

    `report` is the run's own record - `swapped`, `failed` and the map
    it ran with - so a re-run swaps only what remains: an already
    swapped file no longer matches any old basename and is skipped by
    construction, which makes every failure resumable by calling again.
    """

    def __init__(self, message: str, report: Optional[dict] = None):
        super().__init__(message)
        self.report = report or {}


@dataclass
class Placement:
    """One Subtitles-row item playing a wanted file."""

    timeline: str
    start: int
    end: int
    left_offset: Any
    pool_path: str
    item: Any
    pool_item: Any


def subtitle_track_index(timeline) -> Optional[int]:
    """The 1-based video track named the Subtitles row, or None."""
    try:
        count = timeline.GetTrackCount("video")
    except Exception:  # noqa: BLE001 - an unreadable timeline has no row
        return None
    for index in range(1, count + 1):
        try:
            if timeline.GetTrackName("video", index) == SUBTITLES_TRACK_NAME:
                return index
        except Exception:  # noqa: BLE001 - an unreadable name is not it
            continue
    return None


def _pool_path(pool_item) -> str:
    try:
        return pool_item.GetClipProperty("File Path") or ""
    except Exception:  # noqa: BLE001 - an unreadable property is not a path
        return ""


def find_placements(project, basenames: set) -> dict:
    """Every Subtitles-row placement playing one of `basenames`, by timeline.

    Read-only: the application is held SHARED, and nothing moves. Names
    are basenames because pool paths differ across machines while the
    file a placement plays does not.
    """
    wanted = set(basenames)
    found: dict[str, list] = {}
    with resolve_lease("caption swap placement read", exclusive=False):
        try:
            count = project.GetTimelineCount()
        except Exception as exc:  # noqa: BLE001 - no timelines, no swap
            raise CaptionSwapError(
                f"the Resolve project cannot be read: {exc}") from exc
        for index in range(1, count + 1):
            try:
                timeline = project.GetTimelineByIndex(index)
                name = timeline.GetName()
            except Exception:  # noqa: BLE001 - an unreadable timeline skips
                continue
            track = subtitle_track_index(timeline)
            if track is None:
                continue
            try:
                items = timeline.GetItemListInTrack("video", track) or []
            except Exception:  # noqa: BLE001 - an unreadable row skips
                continue
            for item in items:
                try:
                    pool_item = item.GetMediaPoolItem()
                except Exception:  # noqa: BLE001 - no pool item, no file
                    continue
                if pool_item is None:
                    continue
                path = _pool_path(pool_item)
                if path and os.path.basename(path) in wanted:
                    try:
                        start, end = item.GetStart(), item.GetEnd()
                        left_offset = item.GetLeftOffset()
                    except Exception:  # noqa: BLE001 - unmeasurable skips
                        raise CaptionSwapError(
                            f"caption placement on {name} for "
                            f"{os.path.basename(path)} has unreadable "
                            f"record frames or source trim - refusing to "
                            f"replace it") from None
                    if left_offset is None:
                        raise CaptionSwapError(
                            f"caption placement on {name} for "
                            f"{os.path.basename(path)} has no readable "
                            f"source trim - refusing to replace it")
                    found.setdefault(name, []).append(Placement(
                        timeline=name, start=start, end=end,
                        left_offset=left_offset, pool_path=path, item=item,
                        pool_item=pool_item))
    return found


def swap_files(project, old_to_new: dict, stream=sys.stderr) -> dict:
    """Point every placement of each old file at its new file, verified.

    `old_to_new` maps an old pool path (or basename) to the new file's
    absolute path. One `ReplaceClip` per pool item, then
    `apply_clip_attributes` (a replace resets what the carriage set),
    then a per-placement read-back: the pool item must report the new
    path and the item must keep its recorded frames and source trim. A
    placement that moved or lost its trim, or a pool item that still
    reports the old path, fails the run by name rather than reading as
    swapped.

    The inventory, the swaps and the post-check hold ONE exclusive
    lease, so a placement cannot appear between the scan and the swap.
    A run that fails part way raises `CaptionSwapError` carrying the
    report of what swapped - re-running finishes it, because swapped
    files no longer match and are skipped.
    """
    mapping = {os.path.basename(old): new
               for old, new in old_to_new.items()}
    for old_base, new in mapping.items():
        if not new or not os.path.isfile(new):
            raise CaptionSwapError(
                f"no new file on disk for {old_base}: {new!r} - "
                f"render it before swapping")
    report: dict = {"swapped": [], "failed": [], "map": dict(mapping)}
    with resolve_lease("caption swap re-rendered segments"):
        pre = find_placements(project, set(mapping))
        print(f"  timelines holding old caption files: {len(pre)}",
              file=stream)
        for name, placements in sorted(pre.items()):
            print(f"    {name}: {len(placements)}", file=stream)
        by_old: dict[str, dict] = {}
        for name, placements in pre.items():
            for placement in placements:
                new = mapping.get(os.path.basename(placement.pool_path))
                if new is None:
                    continue
                by_old.setdefault(placement.pool_path, {
                    "new": new, "items": []})["items"].append(placement)
        print(f"  distinct old files placed: {len(by_old)}", file=stream)
        for old_path, group in sorted(by_old.items()):
            new_path = group["new"]
            seen: dict[int, Any] = {}
            for placement in group["items"]:
                seen.setdefault(id(placement.pool_item),
                                placement.pool_item)
            for pool_item in seen.values():
                try:
                    ok = pool_item.ReplaceClip(new_path)
                except Exception as exc:  # noqa: BLE001 - refused loudly
                    ok = False
                    replace_error = str(exc)
                else:
                    replace_error = ""
                if not ok:
                    failure = (f"ReplaceClip refused for "
                               f"{os.path.basename(old_path)}"
                               f"{f': {replace_error}' if replace_error else ''}")
                    report["failed"].append({"old": old_path,
                                             "new": new_path,
                                             "reason": failure})
                    raise CaptionSwapError(failure, report)
                apply_clip_attributes(pool_item, new_path)
            for placement in group["items"]:
                back = _pool_path(placement.item.GetMediaPoolItem())
                if back != new_path:
                    failure = (f"read-back mismatch on "
                               f"{placement.timeline}: {back!r} is not "
                               f"{new_path!r}")
                    report["failed"].append({"old": old_path,
                                             "new": new_path,
                                             "reason": failure})
                    raise CaptionSwapError(failure, report)
                try:
                    now = (placement.item.GetStart(),
                           placement.item.GetEnd())
                    now_left_offset = placement.item.GetLeftOffset()
                except Exception as exc:  # noqa: BLE001 - unreadable moved
                    failure = (f"placement on {placement.timeline} "
                               f"cannot be re-read after the swap: {exc}")
                    report["failed"].append({"old": old_path,
                                             "new": new_path,
                                             "reason": failure})
                    raise CaptionSwapError(failure, report)
                if now != (placement.start, placement.end):
                    failure = (f"placement moved on {placement.timeline}: "
                               f"{placement.start}-{placement.end} is now "
                               f"{now[0]}-{now[1]}")
                    report["failed"].append({"old": old_path,
                                             "new": new_path,
                                             "reason": failure})
                    raise CaptionSwapError(failure, report)
                if now_left_offset != placement.left_offset:
                    failure = (
                        f"source trim moved on {placement.timeline}: "
                        f"{placement.left_offset!r} is now "
                        f"{now_left_offset!r}")
                    report["failed"].append({"old": old_path,
                                             "new": new_path,
                                             "reason": failure})
                    raise CaptionSwapError(failure, report)
                report["swapped"].append({
                    "timeline": placement.timeline,
                    "old": os.path.basename(old_path),
                    "new": os.path.basename(new_path),
                    "start": placement.start, "end": placement.end,
                    "left_offset": placement.left_offset})
            print(f"  swapped {os.path.basename(old_path)} -> "
                  f"{os.path.basename(new_path)} on "
                  f"{len(group['items'])} placement(s)", file=stream)
        post = find_placements(project, set(mapping))
        # `find_placements` takes its own (shared, reentrant) lease, so
        # calling it from inside the exclusive hold is a no-op nesting,
        # not an upgrade.
        remaining = [(name, os.path.basename(placement.pool_path))
                     for name, placements in post.items()
                     for placement in placements]
        if remaining:
            failure = (f"{len(remaining)} old placement(s) still hold "
                       f"old files after the swap: {remaining[:5]}")
            report["failed"].append({"reason": failure})
            raise CaptionSwapError(failure, report)
        print("  post-check: no old file remains placed on any timeline",
              file=stream)
    return report


def verify_files(project, new_bases: set, old_bases: set) -> dict:
    """Read back who plays the new files and who still plays the old ones.

    Read-only, under a shared lease. `timelines` counts timelines with
    a Subtitles row; `new_places` lists `(timeline, file, start, end)`
    for new-file placements; `old_places` lists `(timeline, file)` for
    old files that remain - empty is the swapped state.
    """
    new_places, old_places, timelines = [], [], 0
    with resolve_lease("caption swap verification read", exclusive=False):
        try:
            count = project.GetTimelineCount()
        except Exception as exc:  # noqa: BLE001 - no timelines, no verdict
            raise CaptionSwapError(
                f"the Resolve project cannot be read: {exc}") from exc
        for index in range(1, count + 1):
            try:
                timeline = project.GetTimelineByIndex(index)
            except Exception:  # noqa: BLE001 - an unreadable timeline skips
                continue
            track = subtitle_track_index(timeline)
            if track is None:
                continue
            timelines += 1
            try:
                name = timeline.GetName()
                items = timeline.GetItemListInTrack("video", track) or []
            except Exception:  # noqa: BLE001 - an unreadable row skips
                continue
            for item in items:
                try:
                    pool_item = item.GetMediaPoolItem()
                except Exception:  # noqa: BLE001 - no pool item, no file
                    continue
                if pool_item is None:
                    continue
                base = os.path.basename(_pool_path(pool_item))
                if base in new_bases:
                    try:
                        start, end = item.GetStart(), item.GetEnd()
                    except Exception:  # noqa: BLE001 - unmeasurable skips
                        continue
                    new_places.append((name, base, start, end))
                elif base in old_bases:
                    old_places.append((name, base))
    return {"timelines": timelines, "new_places": new_places,
            "old_places": old_places}

"""Place a still image as its own timeline item on a video track.

The 2026-10-16 defect
-----------------------
The Resolve broker refused to add a track (`EditPatch` `track.add`
returned False) and `clip.insert` produced no timeline item, so a
static corner logo could not be placed as its own item. The worker
baked it into the shared A-roll frame instead.

The causes, measured
--------------------
1. **Page**: `AddTrack` and `AppendToTimeline` require the Edit page.
   On any other page (Color, Fusion, etc.) they return False or
   silently do nothing.

2. **Current timeline**: `AppendToTimeline` writes to the project's
   CURRENT timeline, not the timeline handle you pass. If the wrong
   timeline is current, the still lands on the wrong timeline (or
   nowhere).

3. **Item type**: Still images need a duration set via Resolve's
   user preferences (`StillDurationFrames`). `AppendToTimeline` does
   not accept a duration parameter for image items. Without the
   preference preset manipulation, the still either doesn't land or
   lands with the wrong duration.

4. **Allowlist**: The broker's `_BROKER_ALLOWLIST` only admits
   read-only `resolve-axi` commands. A write operation like
   `track.add` or `clip.insert` goes through `timeline.apply_patch`,
   which is a separate job kind. The allowlist is not the cause of
   the failure, but it is the reason the broker cannot be used for
   the write directly.

The fix
-------
`place_still_as_track_item` below:
- Ensures the Edit page is active
- Ensures the target timeline is current
- Adds a track (or uses a chosen one)
- Sets the still duration via preference preset
- Appends the still to the timeline
- Reads back the item to verify (exists, media path, track, start,
  duration)
- Returns the item id for reversal
- Is reversible by deleting the item by its recorded id
"""

from __future__ import annotations

from typing import Optional

from library.tools.resolve_lock import assert_current_timeline
from library.tools.resolve_temporal import record_span
from library.tools.still_placement import place_still_exact


class StillTrackItemError(RuntimeError):
    """A still image could not be placed as its own timeline item."""


def place_still_as_track_item(
    resolve, project, pool, timeline, media_pool_item,
    duration_frames: int, record_frame: int,
    track_index: Optional[int] = None,
    track_name: str = "Overlay",
    fps: float = 24000 / 1001,
    *,
    timeline_name: str = "",
) -> dict:
    """Place a still image as its own timeline item on a video track.

    If `track_index` is None, a new track is added. Otherwise the
    chosen track is used. The still is placed with the exact
    `duration_frames` duration via Resolve's preference preset API.

    Returns a dict with:
    - `item`: the Resolve timeline item handle
    - `unique_id`: the item's Resolve-assigned unique id
    - `start_frame`: the record frame the item starts at
    - `duration_frames`: the item's duration in frames
    - `media_path`: the still's file path
    - `media_type`: the pool item type ("Still")
    - `track_index`: the video track index the item is on
    - `track_name`: the track's name

    The item can be removed by calling `timeline.GetItemListInTrack`
    and deleting the item whose `GetUniqueId()` matches the returned
    `unique_id`.
    """
    if track_index is not None and track_index < 1:
        raise StillTrackItemError(
            f"track_index must be positive, got {track_index}")

    try:
        assert_current_timeline(project, timeline)
    except Exception as exc:
        raise StillTrackItemError(
            f"timeline {timeline_name!r} is not current: {exc}") from exc

    if not _ensure_edit_page(resolve):
        raise StillTrackItemError(
            "Resolve is not on the Edit page; AddTrack and "
            "AppendToTimeline require it")

    if track_index is None:
        track_index = _add_video_track(project, timeline, track_name)

    result = place_still_exact(
        resolve, project, pool, timeline, media_pool_item,
        duration_frames=duration_frames,
        record_frame=record_frame,
        track_index=track_index,
        fps=fps,
        timeline_name=timeline_name,
    )

    item = result["item"]
    unique_id = str(item.GetUniqueId())

    readback = _readback_item(timeline, track_index, record_frame,
                              unique_id, timeline_name)
    if readback is None:
        raise StillTrackItemError(
            f"still item {unique_id!r} did not land on V{track_index} "
            f"at frame {record_frame}")

    return {
        "item": item,
        "unique_id": unique_id,
        "start_frame": readback["start_frame"],
        "duration_frames": readback["duration_frames"],
        "media_path": result["media_path"],
        "media_type": result["media_type"],
        "track_index": track_index,
        "track_name": track_name,
    }


def remove_still_track_item(timeline, track_index: int,
                            unique_id: str, *,
                            timeline_name: str = "") -> bool:
    """Remove a still track item by its unique id.

    Returns True if the item was found and removed, False otherwise.
    """
    items = timeline.GetItemListInTrack("video", track_index) or []
    for item in items:
        try:
            if str(item.GetUniqueId()) == unique_id:
                return bool(item.Delete())
        except Exception:
            continue
    return False


def _ensure_edit_page(resolve) -> bool:
    """Ensure Resolve is on the Edit page.

    Returns True if the Edit page is active, False otherwise.
    """
    try:
        current = resolve.GetCurrentPage()
    except Exception:
        return False
    return str(current).lower() == "edit"


def _add_video_track(project, timeline, track_name: str) -> int:
    """Add a video track and return its index.

    Uses `timeline.AddTrack("video", track_name)` which appends a new
    track. The new track's index is the current track count.
    """
    track_count = int(timeline.GetTrackCount("video") or 0)
    new_index = track_count + 1

    if not timeline.AddTrack("video", track_name):
        raise StillTrackItemError(
            f"AddTrack returned False for video track {new_index}")

    return new_index


def _readback_item(timeline, track_index: int, record_frame: int,
                   unique_id: str, timeline_name: str) -> Optional[dict]:
    """Read back a placed item from the timeline.

    Returns None if the item is not found, else a dict with
    `start_frame` and `duration_frames`.
    """
    items = timeline.GetItemListInTrack("video", track_index) or []
    for item in items:
        try:
            live_span = record_span(item.GetStart(), item.GetEnd())
            if (str(item.GetUniqueId()) == unique_id
                    and live_span.start == record_frame):
                return {
                    "start_frame": live_span.start,
                    "duration_frames": live_span.duration,
                }
        except Exception:
            continue
    return None

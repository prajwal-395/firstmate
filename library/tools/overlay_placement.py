"""One placer for small overlay clips, used by every builder.

A tight overlay lands on the timeline in three moves: `AppendToTimeline`
puts it down, `Scaling=1` draws it at native pixels centred, and
Pan/Tilt move its centre onto the full-frame coordinates the box was
computed from (`library/tools/tight_box.py`). A full-canvas overlay
needs only the first.

Both timeline builders (the master in step 6.01 and the reels in
`reel_build`) used to place overlays with inline `AppendToTimeline`
calls. Two placers doing the same arithmetic are two chances to land
one frame off - the comment at the reels caption loop says exactly
that about spans - so the transform half lives here, once, and both
loops call it.

Every `SetProperty` is judged by its RETURN VALUE, the same rule the
conform applies in step 6.01: Resolve does not raise on a property it
declines, it returns False (or None) and carries on. A refused
transform is REPORTED in the returned note - never raised - because
the clip IS on the timeline; failing the build over a movable caption
would trade a misplaced caption for a missing one.
"""

from __future__ import annotations

import os
from typing import Optional


def sequence_frame_paths(frame_dir: str) -> list[str]:
    """Every PNG in a rendered frame directory, in render order.

    The one listing every placer and importer shares: Resolve groups
    the files into a single image-sequence pool item when they arrive
    in one `ImportMedia` call, and placement counts on the order, so
    two different sortings would place different frames at the same
    record position.
    """
    try:
        names = os.listdir(frame_dir or "")
    except OSError:
        return []
    return [os.path.join(frame_dir, name) for name in sorted(names)
            if name.endswith(".png")]


def place_overlay_segment(media_pool, timeline, pool_item,
                          track_index: int,
                          record_frame: int,
                          source_in_frame: int,
                          source_out_frame: int,
                          placement: Optional[dict] = None,
                          label: str = "") -> tuple[bool, str]:
    """Place one overlay clip and, where asked, transform it.

    `placement` is None for a full-canvas clip, else the
    `{"scaling", "pan", "tilt"}` mapping `tight_box` computed. Returns
    `(placed, note)`: `placed` is False only when `AppendToTimeline`
    itself fails; a refused transform still places, with the refusal
    in `note` for the caller to warn on.
    """
    name = label or "overlay"
    try:
        result = media_pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": source_in_frame,
            "endFrame": source_out_frame,
            "trackIndex": track_index,
            "recordFrame": record_frame,
            "mediaType": 1,
        }])
    except Exception as exc:  # Resolve raises bare Exceptions here
        return False, f"{name}: placement raised {exc!r}"
    if not result:
        return False, f"{name}: AppendToTimeline returned nothing"

    if not placement:
        return True, ""

    items = timeline.GetItemListInTrack("video", track_index) or []
    placed_item = None
    for item in items:
        try:
            if item.GetStart() == record_frame:
                placed_item = item
                break
        except Exception:  # noqa: BLE001 - a stale handle, keep looking
            continue
    if placed_item is None:
        placed_item = items[-1] if items else None
    if placed_item is None:
        return True, (f"{name}: placed but no timeline item found on "
                      f"V{track_index} for the transform")

    refused = []
    for prop, key in (("Scaling", "scaling"), ("Pan", "pan"),
                      ("Tilt", "tilt")):
        value = placement.get(key)
        if value is None:
            continue
        try:
            ok = placed_item.SetProperty(prop, value)
        except Exception as exc:  # noqa: BLE001 - judged below, not raised
            ok = False
        if not ok:
            refused.append(f"{prop}={value}")
    if refused:
        return True, (f"{name}: placed, but Resolve refused "
                      f"{', '.join(refused)}")
    return True, ""

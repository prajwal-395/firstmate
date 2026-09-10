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

Every `SetProperty` is judged by what it RETURNS and then READ BACK,
because Resolve lies about Pan/Tilt past its measured 3840 rail:
setting beyond returns True and reads back the clamp (read off the
live timeline 2026-09-10 on Resolve 21 - 37 caption items asking for
Tilt -4316..-7579 all read back exactly -3840.0; the earlier "four
times the timeline dimensions" figure was calibrated to miss it).
A return judged alone reports the overlay placed while it sits
off-position - the captain's captions - so a value Resolve does not
hold is REPORTED in the returned note, never raised: the clip IS on
the timeline, and failing the build over a movable caption would
trade a misplaced caption for a missing one.
"""

from __future__ import annotations

import os
from typing import Optional

#: A held Pan/Tilt within this of the requested value counts as
#: placed. Resolve stores floats; float rounding at these magnitudes
#: is ~1e-3, while a clamp misses by hundreds - so this tolerates
#: representation noise and nothing else.
READBACK_TOLERANCE = 0.5


def _read_back(placed_item, prop: str):
    """What Resolve actually holds for one property, or None.

    None means the read-back itself is unavailable (a proxy that
    does not serve `GetProperty`, an exception, a null read) - the
    caller then falls back to judging the `SetProperty` return, the
    old behaviour, rather than refusing a placement it cannot see.
    No `hasattr`: always True on Resolve's proxies, invented names
    included (AGENTS.md 5).
    """
    try:
        read = placed_item.GetProperty(prop)
    except Exception:  # noqa: BLE001 - judged below, not raised
        return None
    if read is None:
        return None
    try:
        return float(read)
    except (TypeError, ValueError):
        return None


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


def apply_placement_transform(timeline, track_index: int,
                              record_frame: int,
                              placement: Optional[dict],
                              label: str = "") -> str:
    """Move an already-placed overlay clip onto its tight box.

    `placement` is None for a full-canvas clip (nothing to do), else
    the `{"scaling", "pan", "tilt"}` mapping `tight_box` computed.
    Returns the warning note, `""` when everything landed: a refused
    transform still leaves the clip placed, so it is REPORTED here and
    never raised - the clip IS on the timeline.

    One spelling for both placers: the caption loop below and the
    reel explainer/semantic-visual placer in `reel_build` used to do
    this lookup separately, and two lookups for "the item just placed"
    are two chances to transform a neighbour.
    """
    name = label or "overlay"
    if not placement:
        return ""
    try:
        items = timeline.GetItemListInTrack("video", track_index) or []
    except Exception:  # noqa: BLE001 - a proxy that does not serve it
        items = []
    placed_item = None
    for item in items:
        try:
            if item.GetStart() == record_frame:
                placed_item = item
                break
        except Exception:  # noqa: BLE001 - a stale handle, keep looking
            continue
    if placed_item is None:
        # NOT the last item on the track: that fallback reads a
        # NEIGHBOUR when the match fails, which is one caption judged
        # - and transformed - by another caption's geometry. An item
        # this cannot name is an unavailable read-back, said plainly.
        return (f"{name}: placed but no timeline item starts on "
                f"V{track_index} at frame {record_frame}, so its "
                f"transform was not judged")
    refused = []
    for prop, key in (("Scaling", "scaling"), ("Pan", "pan"),
                      ("Tilt", "tilt")):
        value = placement.get(key)
        if value is None:
            continue
        try:
            ok = placed_item.SetProperty(prop, value)
        except Exception:  # noqa: BLE001 - judged below, not raised
            ok = False
        if not ok:
            refused.append(f"{prop}={value}")
            continue
        # The return is a lie past the clamp: setting Tilt past the
        # measured 3840 rail returns True and holds 3840.0. The only
        # honest judgement is to read the value back - a held value
        # that is not the requested one is a REFUSED placement, never
        # a success.
        held = _read_back(placed_item, prop)
        if held is not None and abs(held - float(value)) > READBACK_TOLERANCE:
            refused.append(
                f"{prop}={value} (Resolve holds {held:g} - "
                f"clamped, the overlay is not where the box says)")
    if refused:
        return (f"{name}: placed, but Resolve refused "
                f"{', '.join(refused)}")
    return ""


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

    return True, apply_placement_transform(
        timeline, track_index, record_frame, placement, label)

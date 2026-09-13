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
because Resolve lies about Pan/Tilt past its rail: setting beyond
returns True and reads back the clamp. The rail is the 4x law
(`tight_box.MEASURED_RAILS` - Pan 4320 / Tilt 7680 on 1080x1920,
binary-searched 2026-09-13); the 3840 the 2026-09-10 incident read
(37 caption items asking Tilt -4316..-7579 all held -3840.0) does not
reproduce and is history in that module, not the live number.
A return judged alone reports the overlay placed while it sits
off-position - the captain's captions - so a value Resolve does not
hold is REPORTED in the returned note, never raised: the clip IS on
the timeline, and failing the build over a movable caption would
trade a misplaced caption for a missing one.

What a RESOLUTION CHANGE does to values already stored
-----------------------------------------------------
Measured 2026-09-11 on Resolve Studio 21.1.0.14, on a scratch project,
by setting a value and reading it back from a SEPARATE process:

- a timeline that INHERITS the project resolution is rescaled when the
  project's changes. Pan 79.06 / Tilt 1296 set at 1080x1920 read back
  281.102 / 1458 after the project moved to 3840x2160 - Pan by
  `new_w / old_w`, Tilt by `new_h / old_h` - and the change persists
  across processes.
- it is stateless and exact: putting the resolution back gives the
  original numbers to the last digit.
- `SetSetting("useCustomSettings", "1")` does NOT freeze a timeline
  where it is. It snaps to 1920x1080 first, rescaling everything on the
  way, so a builder that ticks custom settings must set the resolution
  afterwards or the timeline lands transposed.

Resolve is preserving the FRAMING, which is right for a picture and
destroys a value computed for a specific frame. Nothing here changes a
built timeline's resolution - every reel carries `useCustomSettings=1`
at the delivery size - and this is written down so the next person to
reach for `SetSetting` on a built timeline knows what it costs.
"""

from __future__ import annotations

import os
from typing import Optional

#: A held Pan/Tilt within this of the requested value counts as
#: placed. Resolve stores floats; float rounding at these magnitudes
#: is ~1e-3, while a clamp misses by hundreds - so this tolerates
#: representation noise and nothing else.
READBACK_TOLERANCE = 0.5


def entry_unit_mismatch(project, target_wh: tuple) -> str:
    """Whether this session would store scaled Pan/Tilt, as a reason.

    Measured on Resolve 21, 2026-09-10 (Reel 09 positioning proof):
    `SetProperty("Pan"/"Tilt", v)` interprets `v` in the units of the
    timeline that is current when the scripting session first touches
    the project (the entry timeline), and silently converts to the
    target timeline's units. Setting Tilt -1700 from a 3840x2160 entry
    onto a 1080x1920 timeline stores -1912.5; from a 1080x1920 entry
    onto a 3840x2160 timeline it stores -1511.11. Same-process
    read-back echoes the set value either way, so the read-back check
    below cannot see it - only the entry size predicts it.

    Returns `""` when the entry timeline matches `target_wh` (stores
    land literally) or when there is no open timeline to judge by,
    else the reason the caller must refuse rather than place: a
    scaled store reads back cleanly and sits wrong, which is the
    wrong-but-stored failure this module exists to prevent.

    **Re-probed 2026-09-11 on Resolve Studio 21.1.0.14 and it did NOT
    reproduce**: an entry on a 3840x2160 master in the same project,
    with the 1080x1920 target then made current and placed through
    this module, stored literally and read literally from a fresh
    process. The guard stays - one negative on one build does not
    retire a measured behaviour, and refusing costs nothing - but it
    is no longer evidence for anything.

    Nor is it what five reels carry. "Every transform at 4x what the
    build asked for" was read off the live timelines alone; their own
    build snapshots hold the 1x value the engine computed, so the
    multiplier landed AFTER the build and after this module ran
    (`library/tools/transform_drift.py` has the measurement).
    """
    try:
        entry = project.GetCurrentTimeline()
    except Exception:  # noqa: BLE001 - no entry to judge by
        return ""
    if entry is None:
        return ""
    try:
        entry_wh = (int(entry.GetSetting("timelineResolutionWidth")),
                    int(entry.GetSetting("timelineResolutionHeight")))
    except (TypeError, ValueError):
        return ""
    if tuple(entry_wh) == tuple(target_wh):
        return ""
    try:
        entry_name = entry.GetName()
    except Exception:  # noqa: BLE001 - the sizes are the message
        entry_name = "?"
    return (
        f"entry timeline {entry_name!r} is "
        f"{entry_wh[0]}x{entry_wh[1]} but placements are computed "
        f"for {target_wh[0]}x{target_wh[1]}: Resolve interprets "
        f"Pan/Tilt in the entry timeline's units and silently "
        f"converts (measured 2026-09-10: Tilt -1700 stored as "
        f"-1912.5 across 3840x2160 -> 1080x1920, and as -1511.11 "
        f"the other way, with same-process read-back echoing the "
        f"set value). Open a {target_wh[0]}x{target_wh[1]} timeline "
        f"and reconnect before placing, or the stores land scaled "
        f"while reading back cleanly.")


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


def _intent_reason(draw_intent: Optional[dict],
                   placement: Optional[dict],
                   held_values: dict) -> str:
    """Whether the HELD Pan/Tilt draw where `draw_intent` says.

    The stored value is judged by the PICTURE it produces, never
    against another clip's number: a full-frame overlay at Tilt 0 and
    a tight overlay at Tilt -870 draw in the same place, so the same
    intent accepts both, while a stale-carriage value is refused
    however cleanly it reads back.

    A `draw_intent` this cannot read is NOT silently skipped - a
    verification that quietly declines to run is the gate that cannot
    fail (AGENTS.md 10.4).  It is reported as unverified.
    """
    if not draw_intent:
        return ""
    from library.tools.tight_box import verify_ink_against_intent
    try:
        canvas_w, canvas_h = (float(v) for v in draw_intent["canvas"])
        full_w, full_h = (int(v) for v in draw_intent["frame"])
        ink = tuple(float(v) for v in draw_intent["ink_in_canvas"])
        want = tuple(float(v) for v in draw_intent["intent_box"])
    except (KeyError, TypeError, ValueError) as exc:
        return (f"draw intent is unreadable ({exc}), so where this "
                f"overlay draws was NOT verified")
    held = dict(placement or {})
    for prop, key in (("Pan", "pan"), ("Tilt", "tilt")):
        if prop in held_values:
            held[key] = held_values[prop]
    return verify_ink_against_intent(canvas_w, canvas_h, held, ink, want,
                                     full_w, full_h)


def apply_placement_transform(timeline, track_index: int,
                              record_frame: int,
                              placement: Optional[dict],
                              label: str = "",
                              kind: Optional[str] = None,
                              segment_id: Optional[str] = None,
                              intent: Optional[dict] = None,
                              draw_intent: Optional[dict] = None,
                              canvas: Optional[tuple] = None,
                              frame: Optional[tuple] = None) -> str:
    """Move an already-placed overlay clip onto its tight box.

    `placement` is None for a full-canvas clip (nothing to do), else
    the `{"scaling", "pan", "tilt"}` mapping `tight_box` computed -
    plus an optional `zoom` where a declared pin carries one
    (`overlay_intent`), held uniform on `ZoomX`/`ZoomY`.
    Where the project declares intent for this overlay
    (`library/tools/overlay_intent.py` - `kind`/`segment_id` select
    the pin), the declared POSITION wins over the computed one, so a
    rebuild lands where the captain put things instead of
    recomputing past their corrections. A pin names a place, so
    `canvas` and `frame` are what it is turned into a transform
    against; a matching pin without them RAISES rather than reading
    as honoured. Without intent the computed placement applies
    exactly as before, and neither size is needed.
    Returns the warning note, `""` when everything landed: a refused
    transform still leaves the clip placed, so it is REPORTED here and
    never raised - the clip IS on the timeline.

    One spelling for both placers: the caption loop below and the
    reel explainer/semantic-visual placer in `reel_build` used to do
    this lookup separately, and two lookups for "the item just placed"
    are two chances to transform a neighbour.

    `draw_intent`, where the caller can say what the overlay is FOR,
    is checked last and is the only check that is not circular:
    `{"canvas": (w, h), "ink_in_canvas": (x0, y0, x1, y1),
    "intent_box": (x0, y0, x1, y1), "frame": (w, h)}`, all in pixels.
    The HELD Pan/Tilt are turned back into the picture they draw
    (`tight_box.verify_ink_against_intent`) and compared with that
    box.  A read-back alone cannot see a value computed under a
    superseded draw relation - it reads back exactly what was set -
    which is how five reels shipped 17 motion graphics stored at
    Tilt 5184 that draw entirely off the top of the frame (rows
    -576..-96) while every gate passed.  An
    overlay WITHOUT `draw_intent` behaves exactly as before.
    """
    from library.tools.overlay_intent import resolve as resolve_intent

    name = label or "overlay"
    placement, provenance = resolve_intent(kind, segment_id, placement,
                                           intent, canvas=canvas,
                                           frame=frame)
    if provenance == "declared":
        name = f"{name} (declared position)"
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
    pending = []
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
        pending.append((prop, float(value)))
    zoom = placement.get("zoom")
    if zoom is not None:
        # The declared user zoom (`overlay_intent` `zoom` - Resolve's
        # `ZoomX`/`ZoomY`, one number for both), held uniform. Judged
        # the same way: the `SetProperty` return AND the read-back
        # below, through the same `pending` list. It rides after
        # Pan/Tilt because the Pan law does not move under a zoom
        # (`resolve_transform`), so the order sets nothing about the
        # place - and a refused zoom still leaves the clip placed, so
        # it is REPORTED here and never raised.
        try:
            zoom_value = float(zoom)
        except (TypeError, ValueError):
            refused.append(f"zoom={zoom!r} (not a number)")
            zoom_value = None
        if zoom_value is not None:
            for prop in ("ZoomX", "ZoomY"):
                try:
                    ok = placed_item.SetProperty(prop, zoom_value)
                except Exception:  # noqa: BLE001 - judged below
                    ok = False
                if not ok:
                    refused.append(f"{prop}={zoom_value:g}")
                    continue
                pending.append((prop, zoom_value))
    if pending:
        # Read the values back off a FRESH handle, never the item
        # just written: a write handle can echo the set value while
        # later readers see the stored one (Reel 09 positioning
        # proof, 2026-09-10: Tilt -1700 read back -1700 on the write
        # handle, -1912.5 on every later read - an entry-timeline
        # unit conversion, see `entry_unit_mismatch`). The only
        # honest judgement is what a new reader sees - a held value
        # that is not the requested one is a REFUSED placement,
        # never a success.
        # The return is a lie past the clamp: setting Tilt past the
        # rail returns True and holds the rail value (the 2026-09-10
        # incident held 3840.0; the rail is the 4x law now - see
        # `tight_box.MEASURED_RAILS`).
        # (-7680 reads still sit on older timelines - the
        # reaction-cutaway build, placed under an unknown entry -
        # which is consistent with the same unit conversion
        # applying to the rail itself; the gate calibrates on the
        # 4x-law row.)
        reader = placed_item
        try:
            for item in (timeline.GetItemListInTrack(
                    "video", track_index) or []):
                try:
                    if item.GetStart() == record_frame:
                        reader = item
                        break
                except Exception:  # noqa: BLE001 - stale handle
                    continue
        except Exception:  # noqa: BLE001 - read back off placed_item
            reader = placed_item
        held_values = {}
        for prop, value in pending:
            held = _read_back(reader, prop)
            if held is None:
                continue
            held_values[prop] = held
            if abs(held - value) > READBACK_TOLERANCE:
                refused.append(
                    f"{prop}={value} (Resolve holds {held:g} - "
                    f"clamped, the overlay is not where the box says)")
        reason = _intent_reason(draw_intent, placement, held_values)
        if reason:
            refused.append(reason)
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
                          label: str = "",
                          kind: Optional[str] = None,
                          segment_id: Optional[str] = None,
                          intent: Optional[dict] = None,
                          draw_intent: Optional[dict] = None,
                          canvas: Optional[tuple] = None,
                          frame: Optional[tuple] = None,
                          ) -> tuple[bool, str]:
    """Place one overlay clip and, where asked, transform it.

    `placement` is None for a full-canvas clip, else the
    `{"scaling", "pan", "tilt"}` mapping `tight_box` computed. A
    declared position (`kind`/`segment_id` against `intent` - see
    `apply_placement_transform`) wins over it. Returns `(placed,
    note)`: `placed` is False only when `AppendToTimeline` itself
    fails; a refused transform still places, with the refusal in
    `note` for the caller to warn on.
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
        timeline, track_index, record_frame, placement, label,
        kind=kind, segment_id=segment_id, intent=intent,
        draw_intent=draw_intent, canvas=canvas, frame=frame)

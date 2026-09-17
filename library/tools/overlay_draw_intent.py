"""What an overlay is FOR, in pixels, at placement time.

`overlay_placement.apply_placement_transform` ends with a check no
production caller armed: `draw_intent` - the held Pan/Tilt turned back
into the picture they draw (`tight_box.verify_ink_against_intent`) and
compared with where the overlay belongs. Without it the placer judges
storage fidelity only (read-back), which passes a stale value as
cleanly as a correct one - five reels shipped 17 motion graphics stored
at Tilt 5184 drawing entirely off the top of the frame while every gate
passed.

This module builds that parameter at the production placers. Two
sources, in this order - the first that answers wins:

1. A DECLARED pin (`overlay_intent.matching_target` - all four tiers:
   exact id, placement label, provenance prefix, kind). The pin names
   a canvas centre in delivery-frame pixels; the intent rect is that
   centre sized by the canvas actually going down (the record's own
   size, which is factual - it matches the file - never the placement,
   which is the suspect). Where the captain pinned a graphic, his place
   is the ground truth: a stale computation is refused, and a correct
   re-render under a new digest still passes because the comparison is
   centre-based and the label pin still binds the placing.
2. The DECLARED caption row, for captions only. The intent rect is the
   structural constant canvas (`tight_box.constant_caption_box` run
   against CURRENT declarations - safe-area profile, caption row with
   the reel override, render-time anchor) sized by the record. A
   sidecar placement served under a superseded row draws somewhere
   else and is refused; a fresh correct placement passes exactly,
   because it is the same arithmetic the render used.

Anything else returns None, and the placer behaves exactly as before:
an unpinned motion graphic has no per-graphic declaration at placement
time (its design union lives in the render, not in a declaration the
placer can re-read without re-running the estimator against stale
props), a legacy predictor-era caption canvas is not the structural
one, and a segment whose render props are gone names no anchor. A
wrong intent box false-alarms on correct output (AGENTS.md 10.4), so
where nothing honest exists this says nothing rather than guessing.

Both rects are CANVAS rects, not ink rects: `ink_in_canvas` is the
whole canvas. Ink-inside-canvas correctness is the renderer's job (the
edge guard proves the fit at render time); what the placer must prove
is that the canvas lands where the design put it, which needs no
per-file measurement and no decode at placement time.

REPORT-FIRST: the reason flows into the placer's warning note, never a
raise - the clip IS on the timeline, and failing a build over a movable
overlay trades a misplaced one for a missing one.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from library.tools.resolve_transform import FALLBACK_DRAW_GAIN


def segment_canvas(segment: Optional[dict]) -> Optional[tuple]:
    """The canvas one overlay segment is placed at, or None.

    The record's own size - factual, matching the file - never the
    placement, which is what the check judges. None is a full-canvas
    segment, which needs no transform and takes no intent.
    """
    tight = (segment or {}).get("tight_box") or {}
    width, height = tight.get("width"), tight.get("height")
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def render_props_path(segment: Optional[dict]) -> Optional[str]:
    """The `_props.json` beside one segment's render, or None.

    Step 4.05 names renders `{segment}_*.mov` (or `{segment}_frames/`
    for a sequence) with `{segment}_props.json` beside them; the props
    carry the anchor (`style.position`) the render laid out from. A
    segment that names neither is too old to say, and gets no intent
    rather than an assumed anchor.
    """
    segment = segment or {}
    overlay_path = segment.get("overlay_path") or ""
    if overlay_path.endswith(".mov"):
        return overlay_path[:-4] + "_props.json"
    frames = (segment.get("frames") or {}).get("dir", "") or ""
    if frames.rstrip("/").endswith("_frames"):
        return frames.rstrip("/")[:-7] + "_props.json"
    return None


def render_position(segment: Optional[dict]) -> Optional[str]:
    """The anchor one caption segment rendered under, or None.

    Never raises: an unreadable props file is a segment with no known
    anchor, which is `draw_intent_for_segment`'s ordinary skip, not a
    build failure - the render already validated everything it needed
    when it rendered.
    """
    path = render_props_path(segment)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            body = json.load(handle)
    except (OSError, ValueError):
        return None
    position = ((body or {}).get("style") or {}).get("position")
    return str(position) if position else None


def pin_draw_intent(canvas_wh: tuple, kind: Optional[str],
                    segment_id: Optional[str],
                    intent: Optional[dict], *,
                    frame_wh: Optional[tuple] = None,
                    placement_label: Optional[str] = None) -> Optional[dict]:
    """The intent rect for a pinned overlay, or None where unpinned.

    The rect is the pin's canvas centre sized by the canvas going down.
    Centre-based, so a re-render that changes the canvas width still
    passes - the pin survives resizes by construction, exactly as the
    placer does when it turns the same pin into a transform. The label
    tier is read from the SAME placing name the placer resolves, so a
    label pin the placer honours is the pin this judges against.
    """
    from library.tools.overlay_intent import matching_target

    if not canvas_wh or not intent or not frame_wh:
        return None
    _key, target = matching_target(intent, kind, segment_id,
                                   placement_label)
    if target is None:
        return None
    try:
        centre = target["canvas_centre"]
        cx, cy = float(centre[0]), float(centre[1])
        cw, ch = int(canvas_wh[0]), int(canvas_wh[1])
        frame_wh = (int(frame_wh[0]), int(frame_wh[1]))
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return {
        "canvas": (cw, ch),
        "ink_in_canvas": (0.0, 0.0, float(cw), float(ch)),
        "intent_box": (cx - cw / 2.0, cy - ch / 2.0,
                       cx + cw / 2.0, cy + ch / 2.0),
        "frame": frame_wh,
    }


def caption_draw_intent(segment: Optional[dict], *,
                        frame_wh: Optional[tuple],
                        project_folder: Optional[str] = None,
                        reel_name: Optional[str] = None,
                        draw_gain: float = FALLBACK_DRAW_GAIN) -> Optional[dict]:
    """The intent rect for a caption segment, or None.

    The structural constant canvas re-derived from CURRENT
    declarations: the safe-area profile at this frame, the caption row
    (the reel override over the project value over the engine lift -
    `subtitle_style.project_caption_row`), and the anchor the render
    laid out from (the render's own props file). The record supplies
    only its canvas size, which must BE the structural one - a legacy
    predictor-era canvas is hung differently and gets no rect rather
    than a wrong one.

    Never raises: anything unreadable is an unverifiable caption, and
    the placer behaves exactly as before without it.
    """
    try:
        return _caption_draw_intent(segment, frame_wh=frame_wh,
                                    project_folder=project_folder,
                                    reel_name=reel_name,
                                    draw_gain=draw_gain)
    except Exception:  # noqa: BLE001 - unverified, never a build failure
        return None


def _caption_draw_intent(segment: Optional[dict], *,
                         frame_wh: Optional[tuple],
                         project_folder: Optional[str] = None,
                         reel_name: Optional[str] = None,
                         draw_gain: float = FALLBACK_DRAW_GAIN
                         ) -> Optional[dict]:
    from library.tools.safe_area import resolve_safe_area
    from library.tools.subtitle_style import (
        CAPTION_LIFT_PX,
        caption_row_px,
        project_caption_row,
    )
    from library.tools.tight_box import (
        canvas_screen_origin,
        constant_caption_box,
    )

    canvas_wh = segment_canvas(segment)
    if canvas_wh is None or not frame_wh:
        return None
    frame_w, frame_h = int(frame_wh[0]), int(frame_wh[1])
    if frame_w <= 0 or frame_h <= 0:
        return None
    position = render_position(segment)
    if not position:
        return None

    insets = resolve_safe_area(project_folder, width=frame_w,
                               height=frame_h)
    declared_row = project_caption_row(project_folder,
                                       reel_name=reel_name)
    safe = insets.as_props()
    if declared_row is not None:
        # The project names the row; the engine lift is not a second
        # opinion stacked on top - the same rule `subtitle_style`
        # renders under.
        safe["bottom"] = max(frame_h - caption_row_px(declared_row,
                                                      frame_h), 0)
    else:
        safe["bottom"] = safe["bottom"] + CAPTION_LIFT_PX
    decl_props = {
        "width": frame_w,
        "height": frame_h,
        "style": {
            "safeArea": safe,
            "captionMaxWidth": insets.centered_usable_width,
            "position": position,
        },
        # The constant path checks only that something drew; the union
        # is structural, never measured per segment.
        "subtitles": [{"text": ""}],
    }
    intended = constant_caption_box(decl_props, draw_gain=draw_gain)
    if ((intended.width, intended.height) !=
            (int(canvas_wh[0]), int(canvas_wh[1]))):
        # Not the structural canvas: a predictor-era (or foreign)
        # carrying hung from a different anchor, which this rect would
        # misjudge. No rect rather than a wrong one.
        return None
    ox, oy = canvas_screen_origin(
        intended.width, intended.height, intended.placement,
        frame_w, frame_h, draw_gain)
    cw, ch = float(intended.width), float(intended.height)
    return {
        "canvas": (cw, ch),
        "ink_in_canvas": (0.0, 0.0, cw, ch),
        "intent_box": (ox, oy, ox + cw, oy + ch),
        "frame": (frame_w, frame_h),
    }


def draw_intent_for_segment(segment: Optional[dict], *,
                            kind: Optional[str] = None,
                            segment_id: Optional[str] = None,
                            placement_label: Optional[str] = None,
                            intent: Optional[dict] = None,
                            frame_wh: Optional[tuple],
                            project_folder: Optional[str] = None,
                            reel_name: Optional[str] = None,
                            draw_gain: float = FALLBACK_DRAW_GAIN) -> Optional[dict]:
    """What one overlay is FOR, as `draw_intent`, or None.

    A pin first: where the captain put something, his place wins over
    the declared row too - a pinned caption the row moved past is his
    correction, not a stale computation, and judging it by the row
    would false-alarm on exactly the corrections pins exist to keep.
    `placement_label` is the segment entry's placing name (reel
    graphics carry one; captions carry none) - the SAME name the
    placer resolves, so a label pin the placer honours is the pin this
    judges against. Then the caption row for unpinned captions.
    Everything else is None: the placer behaves exactly as before.
    """
    canvas_wh = segment_canvas(segment)
    if canvas_wh is None or not frame_wh:
        return None
    frame_wh = (int(frame_wh[0]), int(frame_wh[1]))
    pinned = pin_draw_intent(canvas_wh, kind, segment_id, intent,
                             frame_wh=frame_wh,
                             placement_label=placement_label)
    if pinned is not None:
        return pinned
    if kind == "caption":
        return caption_draw_intent(
            segment, frame_wh=frame_wh, project_folder=project_folder,
            reel_name=reel_name, draw_gain=draw_gain)
    return None

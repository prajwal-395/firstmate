"""
Generate TimedTextOverlay Remotion props from a brand template declaration.

The ``timed_text_overlay`` slot in a brand template declares text moments
that should appear as a transparent overlay rendered by the Remotion
``TimedTextOverlay`` composition.  A template that omits it gets no
overlay - the same per-template opt-in shape as ``motion_accents``
(P3.1/Q3) and ``content.bookends`` (Q7, 2026-08-16).

**THE SLOT HAS NO READER, AND NO TEMPLATE MAY DECLARE IT YET.**  See
``NO_READER`` below and ``docs/ASSET_LIBRARY_PLAN.md``.  The general
component and this generator are kept because the captain confirmed the
PATTERN is right; what is missing is the step that calls the generator and
places the result, and until that exists a declaration would render
nothing and say nothing.

Usage, once a reader exists::

    from library.tools.timed_text_overlay import generate_timed_text_overlay_props

    props = generate_timed_text_overlay_props(
        template_effect=template.effect.__dict__,
        fps=30, width=1080, height=1920, duration_in_frames=1800,
    )
    if props is None:
        # Template declares no overlay - render nothing.
        ...
"""
from __future__ import annotations

from typing import Any

# Why no template may declare `effect.timed_text_overlay` today.
#
# AGENTS.md: "A capability is only real where the renderer reads it" - a
# planner emitting a name nothing reads produces no picture and no
# warning.  This slot is that shape.  #119 added the schema field, this
# generator and the Remotion component, and shipped `fourth_wall.yaml`
# declaring three moments; docs/PIPELINE_PLAN.md section 5 then recorded
# the gap as CLOSED.  It was not.  No pipeline step imports this module -
# step 4.06 renders MotionGraphics segments and composition-mode bookends
# and nothing else - so those three moments reached no frame of any
# render, and nobody could tell from the run summary.
#
# The captain removed that declaration on 2026-08-20 on quality grounds,
# and `fourth_wall.yaml` itself was deleted on 2026-08-20 (captain's
# ruling: the series template arrives later, whole, with authorisation).
# Rather than leave the empty slot open for the next asset to fall into,
# `tests/test_timed_text_overlay.py` asserts no template declares it
# while this record stands.  Wiring a reader means deleting this constant
# in the same commit as the step that calls the generator, and asserting
# the moments reach the picture - the rule `tests/test_vfx_delivery.py`
# applies to renderer knobs.
NO_READER = (
    "effect.timed_text_overlay has no pipeline reader: no step imports "
    "generate_timed_text_overlay_props, so a declaration renders nothing "
    "and warns about nothing. The route is described in "
    "docs/ASSET_LIBRARY_PLAN.md (ratified 2026-08-20). "
    "Do not declare this slot in a brand template until a step reads it."
)


def generate_timed_text_overlay_props(
    template_effect: dict[str, Any],
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    duration_in_frames: int = 1800,
) -> dict[str, Any] | None:
    """Convert a template's ``timed_text_overlay`` declaration to Remotion props.

    Returns ``None`` if the template does not declare an overlay, so
    callers can skip the render entirely.
    """
    declaration = template_effect.get("timed_text_overlay")
    if not declaration:
        return None

    raw_moments = declaration.get("moments")
    if not raw_moments:
        return None

    moments = [
        {
            "text": m["text"],
            "color": m["color"],
            "fontSize": m.get("font_size", 42),
            "startFrame": m["start_frame"],
            "durationFrames": m["duration_frames"],
            "x": m.get("x", 0.5),
            "y": m.get("y", 0.5),
            "fadeInFrames": m.get("fade_in_frames", 10),
            "fadeOutFrames": m.get("fade_out_frames", 10),
            "fontWeight": m.get("font_weight", 400),
            "textAlign": m.get("text_align", "center"),
            "textShadow": m.get(
                "text_shadow", "0px 4px 12px rgba(0,0,0,0.6)"),
        }
        for m in raw_moments
    ]

    return {
        "moments": moments,
        "fontFamily": declaration.get("font_family", "Helvetica"),
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": duration_in_frames,
    }

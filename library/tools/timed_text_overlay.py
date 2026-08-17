"""
Generate TimedTextOverlay Remotion props from a brand template declaration.

The ``timed_text_overlay`` slot in a brand template declares the text moments
that should appear as a transparent overlay rendered by the Remotion
``TimedTextOverlay`` composition.  A template that omits it gets no overlay.
This is the same per-template opt-in mechanism as ``motion_accents``
(P3.1/Q3), applied to text overlays (Q7, 2026-08-16).

Usage::

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

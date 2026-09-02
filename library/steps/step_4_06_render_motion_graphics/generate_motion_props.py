#!/usr/bin/env python3
"""Turn the model's motion-graphics PLAN into Remotion props.

**What changed, and why.**  This file used to derive the whole layer
from two brand-template booleans - `effect.motion_accents` and
`effect.motion_progress_bar` - and a title `creative_direction` has no
field for.  A project that named no template therefore got nothing, and
project 001's run of record recorded exactly that: eight resolved props
that draw nothing, on a video no model had ever been asked about.

The captain's ruling of 2026-09-02 is that the gate itself was the bug:
the model plans the layer, and a brand template REFINES it.  So the plan
arrives as `motion_graphics_plan` from this step's own handoff, and this
file resolves it - see `library/tools/motion_graphics_plan.py`, which is
where the vocabulary, the drop reasons and the timebase live.

What survives from before: the safe area still comes from
`library/tools/safe_area.py` and every element is still positioned from
it, and the accent COLOUR still comes from a template's own palette when
there is one.  What is gone is the gate: no palette and no template
still draws, in whatever colour the plan itself stated.
"""

import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from library.tools.brand_palette import roles_from_palette
from library.tools.motion_graphics_plan import (  # noqa: F401 - re-exported
    PLAN_KEY,
    ResolvedPlan,
    plan_segments,
    props_draw_ink,
    resolve_plan,
)
from library.tools.safe_area import resolve_safe_area

# The cyan every video carried until P3.1. It is not any shipped
# template's colour and it is NO LONGER A FALLBACK - it exists only so
# tests can assert it never reaches a frame again. The captain's ruling of
# 2026-08-16: the accents belong to whichever templates want them, and
# when a template wants them the colour comes from its own palette, never
# from a constant in this file.
WITHDRAWN_LEGACY_ACCENT_COLOR = "#00D4FF"


def timeline_duration(audio_spine: dict) -> float:
    """How long the piece is, off the spine.

    The spine is the ONE thing the overlay layer still reads from the
    edit, and it reads a single number from it: where the picture ends.
    That bounds a span; it does not time one. Every element's start and
    hold come from the plan (AGENTS.md 10.1, "The timeline's length comes
    from the spine").
    """
    return max(
        (float(block.get("timeline_end", 0) or 0)
         for block in audio_spine.get("structure", [])),
        default=0.0,
    )


def brand_palette_roles(brand_style: Optional[dict]) -> dict:
    """The colour roles a brand template's palette resolves to.

    `{}` for a project that named no template, and that is not a
    degraded layer: `motion_graphics_plan.resolve_colour` then reads the
    colour the plan itself stated. The template refines; it does not
    gate.
    """
    return roles_from_palette((brand_style or {}).get("color_palette")) or {}


def generate_motion_props(
    motion_graphics_plan,
    audio_spine: dict,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    brand_style: Optional[dict] = None,
    project_folder: str = "",
    asked: bool = True,
) -> tuple:
    """Resolve the plan and cut it into placeable overlay segments.

    Returns `(segments, resolved_plan)`.  Each segment carries a `props`
    dict ready for `npx remotion render MotionGraphics`, its own
    `timeline_start`/`timeline_end` in seconds and its own
    `total_frames`; overlapping elements are composited into ONE segment
    so several graphics play at once on a single video lane.

    `resolved_plan` is the account of what was dropped and why. An empty
    layer that says which absence it is cannot be misread as a clean one.
    """
    duration = timeline_duration(audio_spine)
    safe_area = resolve_safe_area(
        project_folder or None, width=width, height=height).as_props()

    resolved = resolve_plan(
        motion_graphics_plan,
        timeline_duration=duration,
        fps=fps,
        palette_roles=brand_palette_roles(brand_style),
        asked=asked,
    )
    if not resolved.moments:
        return [], resolved

    segments = plan_segments(
        resolved.moments, fps=fps, width=width, height=height,
        safe_area=safe_area)
    return segments, resolved

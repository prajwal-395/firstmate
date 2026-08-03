#!/usr/bin/env python3
"""
Generate Fusion .comp files for transitions between clips.

Since Resolve's API cannot add native transitions programmatically,
we animate the tail of the outgoing clip and head of the incoming clip
as separate .comp files. This gives us full creative control over
transition types using any of the 34 available Fusion tools.

Transition Types:
  - fade_to_black: Opacity animation on clip tail/head
  - zoom_blur: Transform zoom + DirectionalBlur ramp
  - defocus: Blur radius ramps sharp→blurred→sharp
  - flash: Brightness spike at cut point

Implementation: Delegates to the composable Fusion engine at
library/tools/fusion/ — which provides safety-validated node
generation (ApplyMode crash prevention, etc.)
"""

import os
import sys

# Add tools directory for fusion engine imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))

from fusion.effects import fx
from fusion.engine import CompEngine
from fusion.engine import write_comp as write_transition_comp  # re-export

from typing import Optional


def generate_transition_comp(
    duration_frames: int,
    transition_type: str = "fade_to_black",
    *,
    transition_dur: int = 7,  # Transition effect duration in frames
    position: str = "tail",  # "tail" = outgoing clip, "head" = incoming clip
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Generate a .comp file for a transition effect.

    Args:
        duration_frames: SOURCE clip total frame count (NOT timeline duration).
                         Fusion comps operate on the full source media range.
        transition_type: One of fade_to_black, zoom_blur, defocus, flash
        transition_dur: Duration of the transition effect in frames (default 7).
                        This is how many frames at the tail/head are affected.
        position: "tail" for outgoing clip end, "head" for incoming clip start
        width/height: Resolution

    Returns:
        Complete .comp file content as string
    """
    engine = CompEngine(clip_dur=duration_frames, width=width, height=height)

    if position == "tail":
        block = fx.transition_tail(
            duration_frames, transition_type, transition_dur,
            res=(width, height),
        )
    else:
        block = fx.transition_head(
            duration_frames, transition_type, transition_dur,
            res=(width, height),
        )

    engine.add(block)
    return engine.serialize()


# ─── Transition Presets ──────────────────────────────────────

TRANSITION_PRESETS = {
    "cut": None,  # Hard cut — no .comp needed
    "fade_to_black": {
        "transition_type": "fade_to_black",
        "duration_frames": 7,
    },
    "zoom_blur": {
        "transition_type": "zoom_blur",
        "duration_frames": 7,
    },
    "defocus": {
        "transition_type": "defocus",
        "duration_frames": 7,
    },
    "flash": {
        "transition_type": "flash",
        "duration_frames": 7,
    },
}

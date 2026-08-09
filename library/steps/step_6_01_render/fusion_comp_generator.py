#!/usr/bin/env python3
"""
Generate Fusion .comp files with animated keyframes.

.comp files are plain-text Lua tables that describe complete Fusion compositions.
They are the ONLY reliable method for getting animated keyframes into DaVinci Resolve
programmatically. The Python API's SetInput(name, value, time) does NOT create
keyframes, and comp.Execute() with BezierSpline Lua also does NOT persist.

Workflow:
  1. Call generate_comp() to get a Lua table string
  2. Write to disk as .comp file
  3. Call clip.ImportFusionComp(path) from the Resolve API

All keyframes verified working via GetInput() readback.

Implementation: Delegates to the composable Fusion engine at
library/tools/fusion/ — which provides a type-safe node model
with safety validation (ApplyMode crash prevention, etc.)
"""

import os
import sys

# Add tools directory for fusion engine imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))

from fusion.engine import CompEngine
from fusion.engine import write_comp  # re-export
from fusion.presets import SEGMENT_PRESETS  # re-export

from typing import Optional


def generate_comp(
    clip_dur: int,
    *,
    # Animated zoom (BezierSpline keyframes)
    zoom_start: float = 1.0,
    zoom_mid: float = 1.0,
    zoom_end: float = 1.0,
    # Animated position drift (Path keyframes)
    pan_start: Optional[tuple] = None,  # (x, y) e.g. (0.5, 0.5)
    pan_end: Optional[tuple] = None,    # (x, y) e.g. (0.52, 0.48)
    # Color grade (BrightnessContrast)
    grade_gain: float = 1.0,
    grade_contrast: float = 0.0,
    grade_saturation: float = 1.0,
    # Highlight bloom (SoftGlow)
    glow_gain: float = 0.0,
    glow_threshold: float = 0.75,
    glow_size: float = 3.5,
    # Film Grain
    film_grain: bool = False,
    film_grain_power: float = 0.25,
    film_grain_size: float = 1.5,
    # Defocus (depth-of-field effect)
    defocus: bool = False,
    defocus_size: float = 2.0,
    # Opacity animation (for transitions)
    fade_in_frames: int = 0,   # 0 = no fade
    fade_out_frames: int = 0,  # 0 = no fade
    # Transition at clip tail (outgoing) — only affects last N frames
    tail_transition: Optional[str] = None,  # "fade_to_black", "zoom_blur", "defocus", "flash"
    tail_transition_frames: int = 7,
    # Transition at clip head (incoming) — only affects first N frames
    head_transition: Optional[str] = None,
    head_transition_frames: int = 7,
    # Vignette
    vignette: bool = True,
    vignette_width: Optional[float] = None,   # engine picks defaults by orientation
    vignette_height: Optional[float] = None,   # engine picks defaults by orientation
    vignette_soft: float = 0.35,
    vignette_blend: float = 0.25,
    vignette_color: Optional[tuple] = None,    # (r, g, b) 0-1; default black
    # Resolution (for vignette Background)
    width: int = 1080,
    height: int = 1920,
    # Source clip resolution (orientation-aware vignette + Background)
    source_res: Optional[tuple] = None,  # (w, h) e.g. (1920, 1080)
) -> str:
    """Generate a Fusion .comp file content as a Lua table string.

    Args:
        clip_dur: SOURCE clip total frame count (NOT timeline duration).
                  Use int(mpi.GetClipProperty('Frames')).
                  Fusion comps operate on the full source media range.
        zoom_start/mid/end: BezierSpline keyframes for Transform.Size
        pan_start/end: Path keyframes for Transform.Center (optional)
        grade_*: BrightnessContrast parameters
        glow_*: SoftGlow parameters
        film_grain*: Film grain overlay
        defocus*: Depth-of-field blur
        fade_in/out_frames: Opacity animation for transitions
        vignette*: Vignette parameters (None = auto from source orientation)
        width/height: Resolution for Background node
        source_res: Source clip native resolution for orientation detection

    Returns:
        Complete .comp file content as string
    """
    # Delegate to the composable engine; filter out None values so
    # the engine's orientation-aware defaults take effect.
    return CompEngine.from_params(clip_dur, **{
        k: v for k, v in locals().items()
        if k != 'clip_dur' and v is not None
    })

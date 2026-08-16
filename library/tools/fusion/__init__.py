"""
Fusion Composition Engine — Composable Effects for DaVinci Resolve.

Provides a Python object model for Fusion .comp files with:
- Layer 1: Node model — FusionNode, BezierSpline, FusionComp
- Layer 2: Composable effect blocks — fx.zoom(), fx.grade(), fx.vignette(), etc.
- Layer 3: CompEngine — high-level composition API

Usage:
    from fusion import CompEngine, fx

    # Compose effects
    comp = (CompEngine(clip_dur=90)
        .add(fx.zoom(90, start=1.0, mid=1.04, end=1.03))
        .add(fx.grade(gain=1.05, contrast=0.04))
        .add(fx.vignette(clip_dur=90))
        .serialize())

    # Backward-compatible
    comp = CompEngine.from_params(clip_dur=90, zoom_start=1.0, ...)
"""

from .nodes import FusionNode, BezierSpline, FusionComp
from .effects import fx, EffectBlock
from .engine import CompEngine, write_comp, get_source_frame_count
from .parser import parse_comp, parse_comp_file, parse_setting, parse_setting_file

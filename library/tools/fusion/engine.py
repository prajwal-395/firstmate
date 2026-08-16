"""
Composition Engine (Layer 3).

High-level API that chains effect blocks together into complete
Fusion compositions, handling all wiring automatically.

Usage:
    from fusion.engine import CompEngine
    from fusion.effects import fx

    comp_str = (CompEngine(clip_dur=90)
        .add(fx.zoom(90, start=1.0, mid=1.04, end=1.03))
        .add(fx.grade(gain=1.05, contrast=0.04, saturation=1.15))
        .add(fx.glow(gain=0.08))
        .add(fx.vignette(clip_dur=90))
        .serialize())
"""

from __future__ import annotations

import os
from typing import Optional

from .effects import EffectBlock, _reset_counters, fx
from .nodes import BezierSpline, FusionComp, FusionNode


class CompEngine:
    """Chainable composition builder.

    Manages MediaIn/MediaOut, wiring between effect blocks,
    and serialization to .comp file format.

    CRITICAL: ``clip_dur`` must be the SOURCE clip's total frame count,
    NOT the timeline clip's trimmed duration. Fusion compositions operate
    on the full source media range. Use ``MediaPoolItem.GetClipProperty('Frames')``
    or ``clip.GetSourceEndFrame() - clip.GetSourceStartFrame() + 1`` to get
    the correct value. Using ``clip.GetDuration()`` (timeline duration) will
    cause keyframes to land at the wrong positions.
    """

    def __init__(self, clip_dur: int, *, width: int = 1080, height: int = 1920):
        """Initialize a composition builder.

        Args:
            clip_dur: SOURCE clip total frame count (NOT timeline duration).
            width: Composition width in pixels.
            height: Composition height in pixels.
        """
        _reset_counters()
        self.clip_dur = clip_dur
        self.width = width
        self.height = height
        self.comp = FusionComp(duration=clip_dur)
        self._last_output = "MediaIn1"
        self._blocks: list[EffectBlock] = []

        # Add MediaIn
        last_frame = clip_dur - 1
        media_in = FusionNode("MediaIn1", "MediaIn")
        media_in.inputs["MediaIn1.GlobalStart"] = {
            "_type": "quoted_key",
            "value": 0,
        }
        media_in.inputs["MediaIn1.GlobalEnd"] = {
            "_type": "quoted_key",
            "value": last_frame,
        }
        media_in.pos = (0, 0)
        self.comp.add_node(media_in)

    def add(self, block: EffectBlock) -> "CompEngine":
        """Append an effect block to the chain. Auto-wires input/output.

        Empty blocks (from skipped effects) are silently ignored.
        """
        if not block.nodes:
            return self

        # Add all nodes from the block
        for node in block.nodes:
            self.comp.add_node(node)

        # Wire the block's input to the previous output
        input_node = self.comp.find_node(block.input_name)
        if input_node:
            input_node.set_input(block.input_key, self._last_output)

        self._last_output = block.output_name
        self._blocks.append(block)
        return self

    def add_all(self, blocks: list[EffectBlock]) -> "CompEngine":
        """Append multiple blocks (for powergrades/recipes)."""
        for b in blocks:
            self.add(b)
        return self

    def serialize(self) -> str:
        """Finalize wiring and serialize to .comp string.

        Adds MediaOut wired to the last block's output.
        Auto-linearizes any BezierSpline nodes whose keyframes
        use ``flags={"Linear": True}`` — Resolve ignores the flag
        during ImportFusionComp, so we embed explicit LH/RH handles.
        """
        # Auto-linearize splines that requested linear interpolation
        for node in self.comp.nodes:
            if isinstance(node, BezierSpline):
                has_linear = any(
                    kf.flags and kf.flags.get("Linear")
                    for kf in node.keyframes
                )
                if has_linear:
                    node.linearize()

        media_out = FusionNode("MediaOut1", "MediaOut")
        media_out.set_input("Input", self._last_output)
        media_out.pos = (770, 0)
        self.comp.add_node(media_out)

        return self.comp.serialize()


    @classmethod
    def from_params(cls, clip_dur: int, **params) -> str:
        """Drop-in replacement for the old generate_comp() signature.

        Accepts the same keyword arguments as the original generate_comp()
        and produces equivalent .comp output.

        Args:
            clip_dur: SOURCE clip total frame count (NOT timeline duration).
        """
        engine = cls(
            clip_dur,
            width=params.get("width", 1080),
            height=params.get("height", 1920),
        )

        # Fusion comps operate in the source clip's native resolution,
        # not the timeline's. When source_res is provided, use it for
        # ALL Background nodes (vignette, fade, transitions). The
        # timeline scaling/letterboxing happens AFTER Fusion.
        source_res = params.get("source_res", None)
        if source_res:
            res = tuple(source_res)
        else:
            res = (params.get("width", 1080), params.get("height", 1920))

        # Zoom
        zoom_start = params.get("zoom_start", 1.0)
        zoom_mid = params.get("zoom_mid", 1.0)
        zoom_end = params.get("zoom_end", 1.0)
        pan_start = params.get("pan_start", None)
        pan_end = params.get("pan_end", None)

        has_zoom = not (zoom_start == zoom_mid == zoom_end)
        has_pan = pan_start is not None and pan_end is not None

        if has_zoom or has_pan:
            engine.add(fx.zoom(
                clip_dur,
                start=zoom_start,
                mid=zoom_mid,
                end=zoom_end,
                pan_start=pan_start if has_pan else None,
                pan_end=pan_end if has_pan else None,
            ))

        # BrightnessContrast
        grade_gain = params.get("grade_gain", 1.0)
        grade_contrast = params.get("grade_contrast", 0.0)
        grade_saturation = params.get("grade_saturation", 1.0)
        if grade_gain != 1.0 or grade_contrast != 0.0 or grade_saturation != 1.0:
            engine.add(fx.grade(
                gain=grade_gain,
                contrast=grade_contrast,
                saturation=grade_saturation,
            ))

        # SoftGlow
        glow_gain = params.get("glow_gain", 0.0)
        if glow_gain > 0:
            engine.add(fx.glow(
                gain=glow_gain,
                threshold=params.get("glow_threshold", 0.75),
                size=params.get("glow_size", 3.5),
            ))

        # Film Grain
        if params.get("film_grain", False):
            engine.add(fx.grain(
                power=params.get("film_grain_power", 0.25),
                size=params.get("film_grain_size", 1.5),
            ))

        # Defocus
        if params.get("defocus", False):
            engine.add(fx.defocus(
                size=params.get("defocus_size", 2.0),
            ))

        # Vignette
        if params.get("vignette", True):
            # Compute ellipse proportional to source frame.
            # EllipseMask Width/Height are in a square normalized space,
            # so we derive Height from the source aspect ratio.
            default_w = 1.0
            default_h = 1.0
            if source_res:
                default_h = source_res[1] / source_res[0]
            engine.add(fx.vignette(
                clip_dur=clip_dur,
                width=params.get("vignette_width", default_w),
                height=params.get("vignette_height", default_h),
                soft=params.get("vignette_soft", 0.35),
                blend=params.get("vignette_blend", 0.25),
                color=params.get("vignette_color", (0.0, 0.0, 0.0)),
                res=res,
            ))

        # Fade in/out
        fade_in = params.get("fade_in_frames", 0)
        fade_out = params.get("fade_out_frames", 0)
        if fade_in > 0 or fade_out > 0:
            engine.add(fx.fade(
                clip_dur,
                fade_in=fade_in,
                fade_out=fade_out,
                res=res,
            ))

        # Tail transition
        tail_trans = params.get("tail_transition")
        if tail_trans:
            engine.add(fx.transition_tail(
                clip_dur,
                tail_trans,
                params.get("tail_transition_frames", 7),
                res=res,
            ))

        # Head transition
        head_trans = params.get("head_transition")
        if head_trans:
            engine.add(fx.transition_head(
                clip_dur,
                head_trans,
                params.get("head_transition_frames", 7),
                res=res,
            ))

        return engine.serialize()


def write_comp(path: str, content: str) -> str:
    """Write .comp content to file and return the path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def get_source_frame_count(timeline_item) -> int:
    """Get the SOURCE clip's total frame count from a Resolve timeline item.

    This is the correct value to pass as ``clip_dur`` to CompEngine.
    Do NOT use ``timeline_item.GetDuration()`` — that returns the timeline
    clip's trimmed duration, which is shorter than the source.

    Fusion compositions operate on the full source media range, so
    keyframes must be relative to the source frame count.

    Args:
        timeline_item: A DaVinci Resolve TimelineItem object.

    Returns:
        The source clip's total frame count.
    """
    src_start = timeline_item.GetSourceStartFrame()
    src_end = timeline_item.GetSourceEndFrame()
    return src_end - src_start + 1


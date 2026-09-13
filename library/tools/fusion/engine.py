"""
Composition Engine (Layer 3).

High-level API that chains effect blocks together into complete
Fusion compositions, handling all wiring automatically.

Usage:
    from fusion.engine import CompEngine
    from fusion.effects import fx

    comp_str = (CompEngine(clip_dur=90, width=1080, height=1920)
        .add(fx.zoom(90, start=1.0, mid=1.04, end=1.03))
        .add(fx.grade(gain=1.05, contrast=0.04, saturation=1.15))
        .add(fx.glow(gain=0.08, threshold=0.75, size=3.5))
        .add(fx.vignette(clip_dur=90, width=1.0, height=1.0,
                         soft=0.35, blend=0.25, res=(1080, 1920)))
        .serialize())
"""

from __future__ import annotations

import os
from typing import Optional

from .effects import DRIFT_EASING, EffectBlock, _reset_counters, fx
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

    def __init__(self, clip_dur: int, *, width: int, height: int):
        """Initialize a composition builder.

        Args:
            clip_dur: SOURCE clip total frame count (NOT timeline duration).
            width: Composition width in pixels - the SOURCE clip's own
                frame, never the delivery format. Required: a canvas
                that guesses its size paints a wrong-size Background
                over the picture.
            height: Composition height in pixels. Required, as above.
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
        """Append multiple blocks (for composed recipes)."""
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

        Accepts the same flat keyword arguments as the original
        generate_comp() and DELEGATES to `comp_builder.build_effect_comp`,
        which is the dispatch the renderer really runs
        (`execution/apply_fusion_comps`).

        It used to carry a second, hand-maintained copy of that dispatch,
        and the two had drifted: this one still defaulted `vignette` to
        True at blend 0.25 / soft 0.35, the exact "vignette nobody asked
        for at a strength nobody chose" AGENTS.md 12 records as removed,
        and it still read `glow_threshold` 0.75, `film_grain_power` 0.25
        and the rest out of `.get` defaults. A capability is only real
        where the renderer reads it (AGENTS.md 10.2), and a SECOND reader
        that answers differently is the same defect wearing the other
        face.

        Args:
            clip_dur: SOURCE clip total frame count (NOT timeline
                duration).
        """
        from .comp_builder import build_effect_comp

        source_res = params.get("source_res")
        if not source_res:
            width = params.get("width")
            height = params.get("height")
            if width is None or height is None:
                raise ValueError(
                    "CompEngine.from_params needs the frame Fusion sees: "
                    "pass source_res=(width, height) or width= and "
                    f"height=. Got neither; params carry "
                    f"{sorted(params)}."
                )
            source_res = (width, height)

        effects = {k: v for k, v in params.items()
                   if k not in ("source_res", "width", "height",
                                "played_frames") and v is not None}
        return build_effect_comp(effects, clip_dur,
                                 source_res=tuple(source_res),
                                 played_frames=params.get("played_frames"))



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


"""
Composable Effect Blocks (Layer 2).

Each function creates an EffectBlock — a self-contained chunk of Fusion
nodes with defined input/output attachment points. The CompEngine chains
these together into complete compositions.

CRITICAL: All ``clip_dur`` parameters must be the SOURCE clip's total
frame count, NOT the timeline clip's trimmed duration. Fusion compositions
operate on the full source media range.

Usage:
    block = fx.zoom(clip_dur=90, start=1.0, mid=1.04, end=1.03)
    # block.nodes → [FusionNode("Transform1", ...), BezierSpline("Transform1Size", ...)]
    # block.input_name → "Transform1"  (wire upstream here)
    # block.output_name → "Transform1"  (this feeds downstream)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .nodes import BezierSpline, FusionNode


@dataclass
class EffectBlock:
    """A composable chunk of Fusion nodes with a defined input and output.

    Attributes:
        nodes: The FusionNode and BezierSpline objects that make up this effect
        input_name: Name of the node that accepts the upstream connection
        input_key: The input parameter name to wire (usually "Input")
        output_name: Name of the node whose output feeds downstream
    """

    nodes: list
    input_name: str
    input_key: str = "Input"
    output_name: str = ""

    def __post_init__(self):
        if not self.output_name:
            self.output_name = self.input_name


#: The transition types `transition_tail`/`transition_head` can draw.
#: `library/tools/transition_vocabulary.FUSION_TYPES` must equal this, and
#: `tests/test_transition_vocabulary.py` asserts it - the whole defect was
#: four vocabularies drifting apart with nothing comparing them.
DRAWABLE_TRANSITIONS = ("fade_to_black", "zoom_blur", "defocus", "flash")


def _unknown_transition_message(ttype) -> str:
    return (
        f"No Fusion transition builder for {ttype!r}. Drawable types: "
        f"{', '.join(DRAWABLE_TRANSITIONS)}. This used to return an empty "
        f"effect block, so an unrenderable type produced a comp with "
        f"nothing in it and no warning."
    )


# ─── Counter for unique node names ──────────────────────────

_counters: dict[str, int] = {}


def _reset_counters():
    """Reset all node name counters (call between compositions)."""
    global _counters
    _counters = {}


def _next_name(base: str) -> str:
    """Generate a unique node name like 'Transform1', 'Transform2', etc."""
    _counters[base] = _counters.get(base, 0) + 1
    return f"{base}{_counters[base]}"


# ─── Composable Effect Functions ─────────────────────────────


class fx:
    """Composable effect block factory.

    Each static method returns an EffectBlock that can be chained
    via CompEngine.add().
    """

    @staticmethod
    def zoom(
        clip_dur: int,
        *,
        start: float = 1.0,
        mid: float = 1.0,
        end: float = 1.0,
        pan_start: Optional[tuple] = None,
        pan_end: Optional[tuple] = None,
    ) -> EffectBlock:
        """Animated Ken Burns zoom with optional pan offset.

        Creates a Transform node with BezierSpline-animated Size
        (3-point: start -> mid -> end) and optional static Center offset.

        NOTE: pan_start is accepted for forward-compatibility but animated
        Center drift is not currently implemented. Fusion's animated Point
        coordinates require either Path{} (banned by AGENTS.md when Merge
        exists downstream - causes black output) or Lua expressions.
        Since pan drift is very subtle (typically 0.5 -> 0.49 = 1%),
        the static pan_end position is used. This is visually
        indistinguishable for the ranges we use.
        """
        has_anim = not (start == mid == end)

        if not has_anim and start == 1.0 and pan_end is None:
            # Identity: no zoom, no pan - skip entirely
            return EffectBlock(nodes=[], input_name="", output_name="")

        tf_name = _next_name("Transform")
        nodes = []

        tf = FusionNode(tf_name, "Transform")
        tf.set_attr("CtrlWZoom", False)

        if has_anim:
            last_frame = clip_dur - 1
            mid_frame = clip_dur // 2
            third = clip_dur // 3

            spline_name = f"{tf_name}Size"
            spline = BezierSpline(spline_name)

            rh0 = start + (mid - start) * 0.33
            lh_end = end + (mid - end) * 0.33

            spline.add_key(0, start, rh=(third, round(rh0, 4)))
            spline.add_key(
                mid_frame, mid,
                lh=(mid_frame - third, mid),
                rh=(mid_frame + third, mid),
            )
            spline.add_key(last_frame, end, lh=(last_frame - third, round(lh_end, 4)))

            tf.set_input("Size", spline)
            nodes.append(spline)
        elif start != 1.0:
            # A constant reframe - what cut_in/cut_out are. Without this a
            # static zoom produced a Transform with Size left at its
            # default of 1, so the effect drew nothing.
            tf.set_input("Size", start)

        if pan_end is not None:
            tf.set_input("Center", pan_end)

        # Position on canvas (offset per instance)
        tf.pos = (110, 0)
        nodes.insert(0, tf)

        return EffectBlock(
            nodes=nodes,
            input_name=tf_name,
            input_key="Input",
            output_name=tf_name,
        )

    @staticmethod
    def grade(
        *,
        gain: float = 1.0,
        contrast: float = 0.0,
        saturation: float = 1.0,
    ) -> EffectBlock:
        """BrightnessContrast color correction.

        Skips if all values are neutral (gain=1, contrast=0, sat=1).
        """
        if gain == 1.0 and contrast == 0.0 and saturation == 1.0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        name = _next_name("BrightnessContrast")
        bc = FusionNode(name, "BrightnessContrast")
        bc.set_input("Gain", gain)
        bc.set_input("Contrast", contrast)
        bc.set_input("Saturation", saturation)
        bc.pos = (220, 0)

        return EffectBlock(nodes=[bc], input_name=name, output_name=name)

    @staticmethod
    def glow(
        *,
        gain: float = 0.0,
        threshold: float = 0.75,
        size: float = 3.5,
    ) -> EffectBlock:
        """SoftGlow highlight bloom.

        Skips if gain is 0 (no glow).
        """
        if gain <= 0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        name = _next_name("SoftGlow")
        glow = FusionNode(name, "SoftGlow")
        glow.set_input("Threshold", threshold)
        glow.set_input("Gain", gain)
        glow.set_input("XGlowSize", size)
        glow.pos = (330, 0)

        return EffectBlock(nodes=[glow], input_name=name, output_name=name)

    @staticmethod
    def grain(
        *,
        power: float = 0.25,
        size: float = 1.5,
    ) -> EffectBlock:
        """Film grain overlay."""
        name = _next_name("FilmGrain")
        fg = FusionNode(name, "FilmGrain")
        fg.set_input("Power", power)
        fg.set_input("Size", size)
        fg.pos = (385, 0)

        return EffectBlock(nodes=[fg], input_name=name, output_name=name)

    @staticmethod
    def defocus(*, size: float = 2.0) -> EffectBlock:
        """Depth-of-field blur."""
        name = _next_name("Defocus")
        df = FusionNode(name, "Defocus")
        df.set_input("XDefocusSize", size)
        df.pos = (385, 55)

        return EffectBlock(nodes=[df], input_name=name, output_name=name)

    @staticmethod
    def vignette(
        *,
        clip_dur: int,
        width: float = 1.8,
        height: float = 1.8,
        soft: float = 0.35,
        blend: float = 0.25,
        color: tuple = (0.0, 0.0, 0.0),
        res: tuple = (1080, 1920),
    ) -> EffectBlock:
        """Elliptical vignette.

        Creates Background + EllipseMask + Merge triplet.
        The Background is colored (default black), masked by an inverted
        ellipse, then merged over the upstream image.

        Args:
            color: (r, g, b) floats 0-1 for the vignette color.
                   Default (0,0,0) = black vignette.
        """
        bg_name = _next_name("Background")
        el_name = _next_name("Ellipse")
        mg_name = _next_name("Merge")

        last_frame = clip_dur - 1

        bg = FusionNode(bg_name, "Background")
        bg.set_input("GlobalOut", last_frame)
        bg.set_input("Width", res[0])
        bg.set_input("Height", res[1])
        bg.set_input("TopLeftRed", color[0])
        bg.set_input("TopLeftGreen", color[1])
        bg.set_input("TopLeftBlue", color[2])
        bg.set_input("EffectMask", el_name, source="Mask")
        bg.pos = (330, 82)

        ellipse = FusionNode(el_name, "EllipseMask")
        ellipse.set_input("SoftEdge", soft)
        ellipse.set_input("MaskWidth", 320)
        ellipse.set_input("MaskHeight", 240)
        ellipse.set_input("PixelAspect", (1, 1))
        ellipse.set_input("Inverted", 1)
        ellipse.set_input("Width", width)
        ellipse.set_input("Height", height)
        ellipse.pos = (220, 82)

        merge = FusionNode(mg_name, "Merge")
        merge.set_input("Blend", blend)
        merge.set_input("Foreground", bg_name)
        merge.pos = (440, 0)

        return EffectBlock(
            nodes=[bg, ellipse, merge],
            input_name=mg_name,
            input_key="Background",
            output_name=mg_name,
        )

    @staticmethod
    def fade(
        clip_dur: int,
        *,
        fade_in: int = 0,
        fade_out: int = 0,
        res: tuple = (1080, 1920),
    ) -> EffectBlock:
        """Opacity animation via Merge with BezierSpline blend.

        Skips if no fade in or out.
        """
        if fade_in <= 0 and fade_out <= 0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        last_frame = clip_dur - 1
        bg_name = _next_name("Background")
        mg_name = _next_name("Merge")
        sp_name = f"{mg_name}Blend"

        # Build keyframes
        spline = BezierSpline(sp_name, color=(194, 171, 49))
        if fade_in > 0:
            spline.add_key(0, 0.0, rh=(fade_in // 2, 0.5))
            spline.add_key(fade_in, 1.0, lh=(fade_in // 2, 1.0))
        else:
            spline.add_key(0, 1.0)

        if fade_out > 0:
            fade_start = last_frame - fade_out
            spline.add_key(
                fade_start, 1.0,
                rh=(fade_start + fade_out // 2, 1.0),
            )
            spline.add_key(
                last_frame, 0.0,
                lh=(last_frame - fade_out // 2, 0.5),
            )

        bg = FusionNode(bg_name, "Background")
        bg.set_input("GlobalOut", last_frame)
        bg.set_input("Width", res[0])
        bg.set_input("Height", res[1])
        bg.pos = (495, 82)

        merge = FusionNode(mg_name, "Merge")
        merge.set_input("Blend", spline)
        merge.set_input("Background", bg_name)
        # Foreground is wired to upstream
        merge.pos = (495, 0)

        return EffectBlock(
            nodes=[bg, merge, spline],
            input_name=mg_name,
            input_key="Foreground",
            output_name=mg_name,
        )

    @staticmethod
    def transition_tail(
        clip_dur: int,
        ttype: str = "fade_to_black",
        dur_frames: int = 7,
        *,
        res: tuple = (1080, 1920),
    ) -> EffectBlock:
        """Transition at end of clip (outgoing).

        Only affects the last dur_frames frames — neutral before that.
        """
        last_frame = clip_dur - 1
        start_f = last_frame - dur_frames

        if ttype == "fade_to_black":
            return fx._fade_transition(
                clip_dur, start_f, last_frame, 1.0, 0.0, res, "Tail"
            )
        elif ttype == "zoom_blur":
            return fx._zoom_blur_transition(
                clip_dur, start_f, last_frame, suffix="Tail"
            )
        elif ttype == "defocus":
            return fx._defocus_transition(
                clip_dur, start_f, last_frame, suffix="Tail"
            )
        elif ttype == "flash":
            return fx._flash_transition(
                clip_dur, start_f, last_frame, suffix="Tail"
            )
        raise ValueError(_unknown_transition_message(ttype))

    @staticmethod
    def transition_head(
        clip_dur: int,
        ttype: str = "fade_to_black",
        dur_frames: int = 7,
        *,
        res: tuple = (1080, 1920),
    ) -> EffectBlock:
        """Transition at start of clip (incoming).

        Only affects the first dur_frames frames — neutral after that.
        """
        last_frame = clip_dur - 1

        if ttype == "fade_to_black":
            return fx._fade_transition(
                clip_dur, 0, dur_frames, 0.0, 1.0, res, "Head",
                hold_end=last_frame,
            )
        elif ttype == "zoom_blur":
            return fx._zoom_blur_transition(
                clip_dur, 0, dur_frames, suffix="Head",
                hold_end=last_frame,
            )
        elif ttype == "defocus":
            return fx._defocus_transition(
                clip_dur, 0, dur_frames, suffix="Head",
                hold_end=last_frame,
            )
        elif ttype == "flash":
            return fx._flash_transition(
                clip_dur, 0, dur_frames, suffix="Head",
                hold_end=last_frame,
            )
        raise ValueError(_unknown_transition_message(ttype))

    # ── Internal transition builders ──

    @staticmethod
    def _fade_transition(
        clip_dur, start_f, end_f, val_start, val_end, res, suffix,
        hold_end=None,
    ):
        last_frame = clip_dur - 1
        bg_name = _next_name("BgTrans")
        mg_name = _next_name("MergeTrans")
        sp_name = f"{mg_name}Blend"

        LIN = {"Linear": True}
        spline = BezierSpline(sp_name, color=(255, 100, 100))
        if suffix == "Tail":
            spline.add_key(0, 1.0, flags=LIN)
            spline.add_key(start_f, 1.0, flags=LIN)
            spline.add_key(end_f, 0.0, flags=LIN)
        else:
            spline.add_key(0, 0.0, flags=LIN)
            spline.add_key(end_f, 1.0, flags=LIN)
            if hold_end is not None:
                spline.add_key(hold_end, 1.0, flags=LIN)

        # Resolve ignores Linear flags during ImportFusionComp.
        # Compute explicit LH/RH handles for linear interpolation.
        spline.linearize()

        bg = FusionNode(bg_name, "Background")
        bg.set_input("GlobalOut", last_frame)
        bg.set_input("Width", res[0])
        bg.set_input("Height", res[1])
        bg.pos = (605, 82)

        merge = FusionNode(mg_name, "Merge")
        merge.set_input("Blend", spline)
        merge.set_input("Background", bg_name)
        merge.pos = (605, 0)

        return EffectBlock(
            nodes=[bg, merge, spline],
            input_name=mg_name,
            input_key="Foreground",
            output_name=mg_name,
        )

    @staticmethod
    def _zoom_blur_transition(
        clip_dur, start_f, end_f,
        zoom_scale=0.4, zoom_offset=0.6, blur_scale=0.75,
        easing="Quad", suffix="Tail", hold_end=None,
    ):
        """Crash Zoom transition — matches DaVinci default.

        Default: zoom Scale=0.4, Offset=0.6 (range 0.6→1.0),
        blur Scale=0.75, both Quad easing, mirrored.
        """
        last_frame = clip_dur - 1
        tf_name = _next_name("TransTF")
        tf_sp_name = f"{tf_name}Size"
        db_name = _next_name("TransDBlur")
        db_sp_name = f"{db_name}Len"

        if suffix == "Tail":
            tf_spline = BezierSpline.sampled(
                tf_sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False,
                scale=-(zoom_scale), offset=1.0,  # 1.0 → zoom_offset
                color=(255, 200, 50),
                hold_before=1.0,
            )
            db_spline = BezierSpline.sampled(
                db_sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False,
                scale=blur_scale, offset=0.0,
                color=(100, 255, 100),
                hold_before=0.0,
            )
        else:
            tf_spline = BezierSpline.sampled(
                tf_sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False, reverse=True,
                scale=-(zoom_scale), offset=1.0,
                color=(255, 200, 50),
                hold_after=hold_end,
            )
            db_spline = BezierSpline.sampled(
                db_sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False, reverse=True,
                scale=blur_scale, offset=0.0,
                color=(100, 255, 100),
                hold_after=hold_end,
            )

        tf = FusionNode(tf_name, "Transform")
        tf.set_attr("CtrlWZoom", False)
        tf.set_input("Size", tf_spline)
        tf.pos = (605, 0)

        db = FusionNode(db_name, "DirectionalBlur")
        db.set_input("Length", db_spline)
        db.set_input("Input", tf_name)
        db.pos = (715, 0)

        return EffectBlock(
            nodes=[tf, tf_spline, db, db_spline],
            input_name=tf_name,
            input_key="Input",
            output_name=db_name,
        )

    @staticmethod
    def _defocus_transition(
        clip_dur, start_f, end_f,
        defocus_size=3.0, easing="Sine",
        suffix="Tail", hold_end=None,
    ):
        """Defocus transition with eased curve.

        AGENTS.md limit: XDefocusSize <= 3.0 to avoid artifacts.
        """
        last_frame = clip_dur - 1
        df_name = _next_name("TransDefocus")
        sp_name = f"{df_name}Size"

        if suffix == "Tail":
            spline = BezierSpline.sampled(
                sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False,
                scale=defocus_size, offset=0.0,
                color=(180, 50, 50),
                hold_before=0.0,
            )
        else:
            spline = BezierSpline.sampled(
                sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False, reverse=True,
                scale=defocus_size, offset=0.0,
                color=(180, 50, 50),
                hold_after=hold_end,
            )

        df = FusionNode(df_name, "Defocus")
        df.set_input("XDefocusSize", spline)
        df.pos = (605, 0)

        return EffectBlock(
            nodes=[df, spline],
            input_name=df_name,
            output_name=df_name,
        )

    @staticmethod
    def _flash_transition(
        clip_dur, start_f, end_f,
        brightness=0.67, saturation=1.83, easing="Sine",
        suffix="Tail", hold_end=None,
    ):
        """Brightness Flash transition — matches DaVinci default.

        Default: Brightness=0.67, Saturation=1.83, Blend animated
        with Sine easing. The BC node has static Brightness/Saturation
        values and its Blend (mix) is animated 0→1→0 (mirrored) for
        a full transition, or 0→1 / 1→0 for split tail/head.
        """
        last_frame = clip_dur - 1
        bc_name = _next_name("TransFlash")
        sp_name = f"{bc_name}Blend"

        if suffix == "Tail":
            # Outgoing: blend from 0 (no effect) → 1 (full flash)
            spline = BezierSpline.sampled(
                sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False,
                scale=1.0, offset=0.0,
                color=(255, 255, 50),
                hold_before=0.0,
            )
        else:
            # Incoming: blend from 1 (full flash) → 0 (no effect)
            spline = BezierSpline.sampled(
                sp_name, start_frame=start_f, end_frame=end_f,
                easing=easing, mirror=False, reverse=True,
                scale=1.0, offset=0.0,
                color=(255, 255, 50),
                hold_after=hold_end,
            )

        bc = FusionNode(bc_name, "BrightnessContrast")
        bc.set_input("Brightness", brightness)
        bc.set_input("Saturation", saturation)
        bc.set_input("Blend", spline)
        bc.pos = (605, 0)

        return EffectBlock(
            nodes=[bc, spline],
            input_name=bc_name,
            output_name=bc_name,
        )

    @staticmethod
    def shake(
        clip_dur: int,
        *,
        x_amount: float = 0.01,
        y_amount: float = 0.01,
        decay_frames: Optional[int] = None,
    ) -> EffectBlock:
        """Transform node with animated random X/Y position.

        `decay_frames` makes it an impact rather than a texture: full
        amplitude on the clip's first frame, falling linearly to nothing
        by frame `decay_frames`, still for the rest of the clip. That is
        what a screen shake punctuating a cut looks like. Left None, the
        shake runs at constant amplitude for the whole clip.
        """
        if x_amount <= 0 and y_amount <= 0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        import random
        r = random.Random(42) # Deterministic for reproducible comps

        tf_name = _next_name("ShakeTransform")
        tf = FusionNode(tf_name, "Transform")
        tf.set_attr("CtrlWZoom", False)

        path_name = _next_name("ShakePath")
        path = FusionNode(path_name, "XYPath")

        x_spline = BezierSpline(f"{tf_name}X")
        y_spline = BezierSpline(f"{tf_name}Y")

        def envelope(frame: int) -> float:
            if not decay_frames or decay_frames <= 0:
                return 1.0
            if frame >= decay_frames:
                return 0.0
            return 1.0 - (frame / float(decay_frames))

        for f in range(0, clip_dur):
            scale = envelope(f)
            xo = r.uniform(-x_amount, x_amount) * scale
            yo = r.uniform(-y_amount, y_amount) * scale
            x_spline.add_key(f, 0.5 + xo, flags={"Linear": True})
            y_spline.add_key(f, 0.5 + yo, flags={"Linear": True})

        x_spline.linearize()
        y_spline.linearize()
        
        path.set_input("X", x_spline)
        path.set_input("Y", y_spline)
        
        tf.set_input("Center", path_name, source="Value")
        
        return EffectBlock(
            nodes=[tf, path, x_spline, y_spline],
            input_name=tf_name,
            output_name=tf_name,
        )

    @staticmethod
    def chromatic_aberration(
        *,
        amount: float = 0.01,
    ) -> EffectBlock:
        """Channel offset effect."""
        name = _next_name("ChromaticAberration")
        node = FusionNode(name, "ChromaticAberration")
        node.set_input("RedOffset", amount)
        node.set_input("BlueOffset", -amount)
        return EffectBlock(nodes=[node], input_name=name, output_name=name)

    @staticmethod
    def lens_distortion(
        *,
        distortion: float = 0.1,
    ) -> EffectBlock:
        """Barrel/pincushion distortion."""
        name = _next_name("LensDistort")
        node = FusionNode(name, "LensDistort")
        node.set_input("Distortion", distortion)
        return EffectBlock(nodes=[node], input_name=name, output_name=name)


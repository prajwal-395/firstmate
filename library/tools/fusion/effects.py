"""
Composable Effect Blocks (Layer 2).

Each function creates an EffectBlock - a self-contained chunk of Fusion
nodes with defined input/output attachment points. The CompEngine chains
these together into complete compositions.

CRITICAL - two frame concepts that must not be confused:

  ``clip_dur``   The SOURCE clip's total frame count. This sets the comp's
                 frame RANGE (GlobalIn/GlobalOut, MediaIn extent).  Fusion
                 compositions operate on the full source media range, so
                 this must always be the whole clip, not the trimmed
                 timeline duration.

  ``source_in`` / ``source_out``
                 The first and last SOURCE FRAME that the timeline
                 actually plays.  They are given in the SOURCE's
                 numbering, as the manifest carries them, and they are
                 translated to comp frames by
                 ``played_window.played_range`` - never used as
                 keyframe positions directly.  **Comp frame 0 is the
                 clip's first played frame**, so a segment cut from
                 source frames 654..725 animates over comp frames 0..71.
                 Writing a keyframe at 654 puts it past everything
                 Resolve renders for that clip, and the spline then
                 extrapolates flat: the effect is held at its first
                 keyframe's value for the whole clip. That is measured,
                 not assumed - see ``played_window``.

Usage:
    block = fx.zoom(clip_dur=5657, source_in=25, source_out=97,
                    start=1.0, mid=1.015, end=1.03)


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

- Brightness Flash: `Brightness = 0.67`, `Saturation = 1.83`, animate `Blend` 0-1 with Sine easing.
- Crash Zoom: Transform `Scale = 0.4`, `Offset = 0.6`, range 0.6-1.0, Quad easing, mirrored.
- Glow: `SoftGlow.Gain = 5.0`, `SoftGlow.XGlowSize = 100`, linear easing.
- Default easing uses `LUTLookup` driven by the system `Transition` variable for Edit page transitions.
- For per-clip Fusion comps, replicate easing with `BezierSpline.sampled()` pre-baked keyframes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .nodes import BezierSpline, FusionNode
from .played_window import assert_ramp_fits, played_range


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


#: How hard the backdrop behind a subject-safe conform is blurred.
#: Big enough that no detail in it competes with the picture in front, so
#: it reads as a plate rather than as a second, smaller edit of the same
#: shot.  A `Blur` carries it and not a `Defocus`: `FusionNode._validate`
#: caps `Defocus.XDefocusSize` at 3.0 because a defocus past that
#: artefacts, and 3.0 is nowhere near a plate.
BACKDROP_BLUR_SIZE = 24.0

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
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
    ) -> EffectBlock:
        """Animated Ken Burns zoom with optional pan offset.

        Creates a Transform node with BezierSpline-animated Size
        (3-point: start -> mid -> end) and optional static Center offset.

        ``source_in`` / ``source_out`` are the first and last source
        frames the timeline plays.  Keyframes are placed within this
        window.  When omitted the whole source (0..clip_dur-1) is used.

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
            # Keyframes in the comp's own frames, which are the
            # PLAYED frames numbered from zero.
            first, last = played_range(clip_dur, source_in, source_out)
            seg_dur = last - first
            mid_frame = first + seg_dur // 2
            third = seg_dur // 3

            spline_name = f"{tf_name}Size"
            spline = BezierSpline(spline_name)

            rh0 = start + (mid - start) * 0.33
            lh_end = end + (mid - end) * 0.33

            spline.add_key(first, start, rh=(first + third, round(rh0, 4)))
            spline.add_key(
                mid_frame, mid,
                lh=(mid_frame - third, mid),
                rh=(mid_frame + third, mid),
            )
            spline.add_key(last, end, lh=(last - third, round(lh_end, 4)))

            tf.set_input("Size", spline)
            nodes.append(spline)
        elif start != 1.0:
            # A constant reframe - what cut_in is. Without this a
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
    def subject_backdrop(
        *,
        picture_scale: float,
        picture_center_x: float = 0.5,
        backdrop_scale: float = 1.0,
        backdrop_center_x: float = 0.5,
        blur_size: float = BACKDROP_BLUR_SIZE,
        source: str = "MediaIn1",
    ) -> EffectBlock:
        """The subject, whole, over a blurred copy of the same frame.

        This is the only effect that BRANCHES: both the picture and the
        thing behind it come from the same MediaIn, so the block wires
        ``source`` into two Transforms and merges them.  That is why it
        must be the first block in the comp - ``CompEngine`` wires only
        one input per block, and the second branch is named here.

        Why it exists.  Filling a portrait frame from landscape source
        keeps ``1 / fill_zoom`` of the source width - 31.6% for 1920x1080
        into 1080x1920 - and a talking head does not fit in that.  Any
        zoom below fill leaves black bars, so there is no setting that
        both fills the frame and keeps the face.  This synthesises the
        missing picture instead: ``picture_scale`` shrinks the source
        until the subject fits, and the gap is the same frame again,
        scaled to cover and defocused.

        Every value is computed by ``compile_manifest._conform_fields``,
        which owns the conform geometry; nothing is derived here.  The
        scales and centres are in the comp CANVAS's units (the source
        clip's own frame), not the delivery frame's, because Resolve's
        own transform crops the central 9:16 column of this canvas
        afterwards.

        A ``Blur`` carries it rather than the ``Defocus`` this repo's
        transition vocabulary uses: ``Defocus.XDefocusSize`` is capped at
        3.0 by ``FusionNode._validate`` because a defocus past that
        artefacts, and a backdrop needs an order of magnitude more.
        """
        bd_name = _next_name("Transform")
        bl_name = _next_name("Blur")
        pic_name = _next_name("Transform")
        mg_name = _next_name("Merge")

        backdrop = FusionNode(bd_name, "Transform")
        backdrop.set_attr("CtrlWZoom", False)
        backdrop.set_input("Size", backdrop_scale)
        backdrop.set_input("Center", (backdrop_center_x, 0.5))
        backdrop.pos = (110, 82)

        blur = FusionNode(bl_name, "Blur")
        blur.set_input("XBlurSize", blur_size)
        blur.set_input("Input", bd_name)
        blur.pos = (220, 82)

        picture = FusionNode(pic_name, "Transform")
        picture.set_attr("CtrlWZoom", False)
        picture.set_input("Size", picture_scale)
        picture.set_input("Center", (picture_center_x, 0.5))
        picture.set_input("Input", source)
        picture.pos = (220, 0)

        merge = FusionNode(mg_name, "Merge")
        merge.set_input("Background", bl_name)
        merge.set_input("Foreground", pic_name)
        merge.pos = (330, 0)

        return EffectBlock(
            nodes=[backdrop, blur, picture, merge],
            input_name=bd_name,
            input_key="Input",
            output_name=mg_name,
        )

    @staticmethod
    def grade(
        *,
        gain: float = 1.0,
        contrast: float = 0.0,
        saturation: float = 1.0,
    ) -> EffectBlock:
        """BrightnessContrast color correction.

        `contrast` arrives in the declaration's pivot-gain units - 0.0
        is neutral, the units the v04 stills were rendered in and the
        units `DeclaredLook.fusion()` emits. Fusion's own tool takes
        1.0 as neutral (below it the picture collapses toward
        mid-grey), so the node carries `1.0 + contrast`: emitting the
        declaration verbatim ships a flat frame
        (`data/vep-grade-variants/report.md` 4.1).

        Skips if all values are neutral (gain=1, contrast=0, sat=1) -
        the skip stays on the declaration, never on the translated
        tool value.
        """
        if gain == 1.0 and contrast == 0.0 and saturation == 1.0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        name = _next_name("BrightnessContrast")
        bc = FusionNode(name, "BrightnessContrast")
        bc.set_input("Gain", gain)
        bc.set_input("Contrast", 1.0 + contrast)
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
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
    ) -> EffectBlock:
        """Opacity animation via Merge with BezierSpline blend.

        Skips if no fade in or out.
        """
        if fade_in <= 0 and fade_out <= 0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        first, last = played_range(clip_dur, source_in, source_out)
        bg_name = _next_name("Background")
        mg_name = _next_name("Merge")
        sp_name = f"{mg_name}Blend"

        # Build keyframes within the played window
        spline = BezierSpline(sp_name, color=(194, 171, 49))
        if fade_in > 0:
            spline.add_key(first, 0.0, rh=(first + fade_in // 2, 0.5))
            spline.add_key(first + fade_in, 1.0, lh=(first + fade_in // 2, 1.0))
        else:
            spline.add_key(first, 1.0)

        if fade_out > 0:
            fade_start = last - fade_out
            spline.add_key(
                fade_start, 1.0,
                rh=(fade_start + fade_out // 2, 1.0),
            )
            spline.add_key(
                last, 0.0,
                lh=(last - fade_out // 2, 0.5),
            )

        # GlobalOut covers the full source so the Background exists for
        # the entire comp frame range.
        bg = FusionNode(bg_name, "Background")
        bg.set_input("GlobalOut", clip_dur - 1)
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
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
    ) -> EffectBlock:
        """Transition at end of clip (outgoing).

        Only affects the last dur_frames of the PLAYED segment - neutral
        before that.  ``source_in``/``source_out`` are the played window
        in SOURCE frames; the ramp is written in the comp's own frames,
        which start at zero on the first played frame.
        """
        first, last_frame = played_range(clip_dur, source_in, source_out)
        assert_ramp_fits(dur_frames, first, last_frame,
                         ttype=ttype, half="tail")
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
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
    ) -> EffectBlock:
        """Transition at start of clip (incoming).

        Only affects the first dur_frames of the PLAYED segment - neutral
        after that.  ``source_in``/``source_out`` bound the played window
        in SOURCE frames; the ramp is written in the comp's own frames,
        which start at zero on the first played frame.
        """
        first, last_played = played_range(clip_dur, source_in, source_out)
        assert_ramp_fits(dur_frames, first, last_played,
                         ttype=ttype, half="head")

        if ttype == "fade_to_black":
            return fx._fade_transition(
                clip_dur, first, first + dur_frames, 0.0, 1.0, res, "Head",
                hold_end=last_played,
            )
        elif ttype == "zoom_blur":
            return fx._zoom_blur_transition(
                clip_dur, first, first + dur_frames, suffix="Head",
                hold_end=last_played,
            )
        elif ttype == "defocus":
            return fx._defocus_transition(
                clip_dur, first, first + dur_frames, suffix="Head",
                hold_end=last_played,
            )
        elif ttype == "flash":
            return fx._flash_transition(
                clip_dur, first, first + dur_frames, suffix="Head",
                hold_end=last_played,
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
            spline.add_key(start_f, 1.0, flags=LIN)
            spline.add_key(end_f, 0.0, flags=LIN)
        else:
            spline.add_key(start_f, 0.0, flags=LIN)
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
    def tv_power_head(
        clip_dur: int,
        *,
        line_frames: int = 4,
        expand_frames: int = 6,
        bloom_frames: int = 8,
        collapse_crop: Optional[float] = None,
        strike_gain: float = 2.2,
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
        played_frames: Optional[int] = None,
    ) -> EffectBlock:
        """Old-TV switch-ON at the head of the clip.

        From black: a bright line strikes at centre (held ``line_frames``),
        opens to full height over ``expand_frames``, and a white overshoot
        decays over ``bloom_frames``.  A ``Crop`` node animates the
        vertical open (uniform Transform ``Size`` can only shrink to a
        dot, never to a line); a ``BrightnessContrast`` node carries the
        strike spike and its decay.

        Every count is declared in ``library/tools/tv_power.py`` - the
        frame defaults here repeat that module's values so the block
        stays usable on its own, and ``comp_builder`` passes the resolved
        declaration through.  ``collapse_crop`` is the depth the crop
        holds at the strike (0.40 keeps a fifth of the picture); None
        resolves to the module's declared default at call time, so the
        two cannot drift apart.  All-zero phases return an empty block.

        ``played_frames`` is how many frames the timeline really renders
        for this clip.  The source span ``played_range`` derives can be
        longer - pool footage at 30 fps cut onto a 24000/1001 reel
        timeline plays fewer frames than the source span counts - and an
        animation keyed past the end of what plays is held flat over
        everything that does (``played_window``).  Clamping ``last`` to
        it keeps the hold key inside the rendered range.  A clip with no
        room for the whole animation is REFUSED by name
        (``TransitionLongerThanTheClip``), the same refusal a transition
        half gets: a ramp longer than its clip never reaches neutral, so
        drawing it holds the effect across the whole clip.
        """
        from library.tools.tv_power import SWITCH_ON_COLLAPSE_CROP

        if collapse_crop is None:
            collapse_crop = SWITCH_ON_COLLAPSE_CROP

        total = line_frames + expand_frames + bloom_frames
        if total <= 0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        first, last = played_range(clip_dur, source_in, source_out)
        if played_frames is not None:
            last = min(last, max(first, int(played_frames) - 1))
        assert_ramp_fits(total, first, last,
                         ttype="tv_power", half="head")
        line_end = first + line_frames
        expand_end = line_end + expand_frames
        settle_end = min(expand_end + bloom_frames, last)

        crop_name = _next_name("PowerCrop")
        crop = FusionNode(crop_name, "Crop")
        crop_splines = []
        for edge in ("CropTop", "CropBottom"):
            spline = BezierSpline.sampled(
                f"{crop_name}{edge[-3:]}",
                start_frame=line_end, end_frame=expand_end,
                easing="Linear", reverse=True,
                scale=collapse_crop, offset=0.0,
                hold_before=collapse_crop, hold_after=last,
                color=(255, 255, 255),
            )
            crop.set_input(edge, spline)
            # sampled() bakes Linear flags; serialize() linearizes them
            # into explicit handles (Resolve ignores the flags).
            crop_splines.append(spline)
        crop.pos = (110, 0)

        bc_name = _next_name("PowerBloom")
        bc = FusionNode(bc_name, "BrightnessContrast")
        # WIRED to the crop above it. `EffectBlock` wires its own
        # `input_name` to whatever precedes the block and reads its
        # `output_name`; the links INSIDE a block are the block's to
        # make. Without this the BrightnessContrast has no image input,
        # the crop's output goes nowhere, and Resolve renders the clip
        # as "The Fusion composition at 00:00:00:00 could not be
        # processed successfully" - a comp that imports and cannot draw.
        bc.set_input("Input", crop_name)
        gain = BezierSpline(f"{bc_name}Gain", color=(255, 255, 100))
        gain.add_key(first, strike_gain, flags={"Linear": True})
        gain.add_key(line_end, strike_gain, flags={"Linear": True})
        gain.add_key(expand_end, 1.3, flags={"Linear": True})
        gain.add_key(settle_end, 1.0, flags={"Linear": True})
        if settle_end < last:
            gain.add_key(last, 1.0, flags={"Linear": True})
        bc.set_input("Gain", gain)
        bc.pos = (220, 0)

        nodes = [crop] + crop_splines + [bc, gain]
        return EffectBlock(
            nodes=nodes,
            input_name=crop_name,
            input_key="Input",
            output_name=bc_name,
        )

    @staticmethod
    def tv_power_tail(
        clip_dur: int,
        *,
        collapse_frames: int = 6,
        dot_frames: int = 3,
        decay_frames: int = 9,
        dot_gain: float = 2.5,
        dot_size: float = 0.05,
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
        played_frames: Optional[int] = None,
    ) -> EffectBlock:
        """Old-TV switch-OFF at the tail of the clip.

        From picture: full height collapses to a line over
        ``collapse_frames``, the line contracts to a dot over
        ``dot_frames`` while the spot spikes bright, and the afterglow
        decays to black over ``decay_frames``.  ``Crop`` draws the
        collapse, a uniform ``Transform`` the dot (its animated peak is
        1.0, inside the 1.04 ceiling), ``BrightnessContrast`` the spike
        and decay.  See ``tv_power_head`` for where the counts live,
        for what ``played_frames`` clamps, and for the refusal when the
        clip has no room for the animation.
        """
        from library.tools.tv_power import COLLAPSE_CROP

        total = collapse_frames + dot_frames + decay_frames
        if total <= 0:
            return EffectBlock(nodes=[], input_name="", output_name="")

        first, last = played_range(clip_dur, source_in, source_out)
        if played_frames is not None:
            last = min(last, max(first, int(played_frames) - 1))
        assert_ramp_fits(total, first, last,
                         ttype="tv_power", half="tail")
        start = max(first, last - total)
        line_at = start + collapse_frames
        dot_at = min(line_at + dot_frames, last)

        crop_name = _next_name("PowerCrop")
        crop = FusionNode(crop_name, "Crop")
        crop_splines = []
        for edge in ("CropTop", "CropBottom"):
            spline = BezierSpline.sampled(
                f"{crop_name}{edge[-3:]}",
                start_frame=start, end_frame=line_at,
                easing="Linear",
                scale=COLLAPSE_CROP, offset=0.0,
                hold_before=0.0, hold_after=last,
                color=(255, 255, 255),
            )
            crop.set_input(edge, spline)
            crop_splines.append(spline)
        crop.pos = (110, 0)

        tf_name = _next_name("PowerDot")
        tf = FusionNode(tf_name, "Transform")
        tf.set_attr("CtrlWZoom", False)
        size = BezierSpline(f"{tf_name}Size", color=(255, 200, 50))
        size.add_key(first, 1.0, flags={"Linear": True})
        size.add_key(line_at, 1.0, flags={"Linear": True})
        size.add_key(dot_at, dot_size, flags={"Linear": True})
        size.add_key(last, dot_size, flags={"Linear": True})
        tf.set_input("Size", size)
        # WIRED to the crop above it - see `tv_power_head` for what an
        # unwired internal link does to the render.
        tf.set_input("Input", crop_name)
        tf.pos = (220, 0)

        bc_name = _next_name("PowerDecay")
        bc = FusionNode(bc_name, "BrightnessContrast")
        bc.set_input("Input", tf_name)
        gain = BezierSpline(f"{bc_name}Gain", color=(255, 255, 100))
        gain.add_key(first, 1.0, flags={"Linear": True})
        gain.add_key(start, 1.0, flags={"Linear": True})
        gain.add_key(line_at, 1.6, flags={"Linear": True})
        gain.add_key(dot_at, dot_gain, flags={"Linear": True})
        gain.add_key(last, 0.0, flags={"Linear": True})
        bc.set_input("Gain", gain)
        bc.pos = (330, 0)

        return EffectBlock(
            nodes=[crop] + crop_splines + [tf, size, bc, gain],
            input_name=crop_name,
            input_key="Input",
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


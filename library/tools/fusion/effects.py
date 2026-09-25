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

import math
from dataclasses import dataclass, field
from typing import Optional

from .nodes import EASING_FUNCTIONS, BezierSpline, FusionNode
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


#: The drift ramp's timing curve. A Ken Burns drift eases in and out
#: (captain, reel 09, 2026-09-10: "smooth ease in and out for those
#: punches in and out") - a linear ramp on a seconds-long push reads as
#: someone zooming, not as a camera moving. One of EASING_FUNCTIONS'
#: names; a plan may name another per shot through `zoom_easing`.
DRIFT_EASING = "Sine"


def _is_midpoint(start: float, mid: float, end: float) -> bool:
    """Whether `mid` says nothing the endpoints do not already say.

    A drift resolves with `zoom_mid` defaulted to the midpoint (see
    `comp_builder.normalize_effects`), so the ramp is fully described
    by start/end plus the easing curve. An off-midpoint `mid` is a
    punch shape (`zoom_emphasis`: push to a peak, settle back) that no
    single ramp can express, so it keeps the three-point spline.
    """
    return math.isclose(mid, (start + end) / 2.0,
                        rel_tol=1e-9, abs_tol=1e-9)


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


def _power_band(*, start_frame: int, end_frame: int, open_at: str,
                collapse_crop: float, clip_dur: int, last: int,
                res: tuple):
    """The old-TV deflection band: black frame, picture in a shrinking slot.

    Returns ``(nodes, spline, input_node_name, output_node_name)``.  The
    caller wires its upstream picture into ``input_node_name`` on the
    Merge's ``Background`` input and reads ``output_node_name``.

    A black ``Background`` sized to the SOURCE clip's own frame is gated
    by a ``RectangleMask`` with ``Invert`` set, so the mask is solid
    EVERYWHERE EXCEPT the band and the black paints everything outside
    it.  The band's ``Height`` is the animated term.  Frame size never
    changes, which is the whole reason this is not a ``Crop``: Fusion's
    ``Crop`` resizes the image to the crop rectangle.

    ``collapse_crop`` is a depth taken off EACH edge - what it meant when
    a pair of crop edges carried it - so the band is
    ``1 - 2 * collapse_crop`` at its tightest and 1.0 wide open.
    ``open_at`` says which end of the ramp is the open one: ``"end"`` for
    a switch-on (tight -> open), ``"start"`` for a switch-off
    (open -> tight).

    Nothing here is a strength: every number is either the caller's
    declared timing or the geometry of "the whole frame" (AGENTS.md 10.5).
    """
    tight = round(1.0 - 2.0 * float(collapse_crop), 6)

    bg_name = _next_name("PowerBand")
    mask_name = _next_name("PowerBandMask")
    merge_name = _next_name("PowerBandMerge")

    if open_at == "end":
        offset, scale, hold_before = tight, round(1.0 - tight, 6), tight
    else:
        offset, scale, hold_before = 1.0, round(tight - 1.0, 6), 1.0

    height = BezierSpline.sampled(
        f"{bg_name}Height",
        start_frame=start_frame, end_frame=end_frame,
        easing="Linear",
        scale=scale, offset=offset,
        hold_before=hold_before, hold_after=last,
        color=(255, 255, 255),
    )

    bg = FusionNode(bg_name, "Background")
    bg.set_input("GlobalOut", clip_dur - 1)
    bg.set_input("Width", res[0])
    bg.set_input("Height", res[1])
    bg.set_input("TopLeftRed", 0.0)
    bg.set_input("TopLeftGreen", 0.0)
    bg.set_input("TopLeftBlue", 0.0)
    bg.set_input("EffectMask", mask_name, source="Mask")
    bg.pos = (110, 82)

    mask = FusionNode(mask_name, "RectangleMask")
    mask.set_input("MaskWidth", res[0])
    mask.set_input("MaskHeight", res[1])
    mask.set_input("PixelAspect", (1, 1))
    mask.set_input("Invert", 1)
    mask.set_input("Width", 1.0)
    mask.set_input("Height", height)
    mask.pos = (0, 82)

    merge = FusionNode(merge_name, "Merge")
    merge.set_input("Foreground", bg_name)
    merge.pos = (110, 0)

    return [bg, mask, merge], height, merge_name, merge_name


def _tv_power(clip_dur: int, *, direction: str,
              collapse_frames: int, dot_frames: int, decay_frames: int,
              collapse_crop=None, dot_gain=None, dot_size=None,
              source_in=None, source_out=None, played_frames=None,
              res: tuple):
    """The old-TV switch, laid down forwards or backwards. ONE shape.

    ``library/tools/tv_power.py`` declares the shape as four STATES the
    set passes through - picture, line, dot, black - and the three phase
    lengths between them.  This lays those states onto the clip:

    * ``direction="off"`` puts PICTURE first and BLACK on the clip's
      last played frame: the switch-off, which is what it always was.
    * ``direction="on"`` puts BLACK on the clip's FIRST played frame and
      PICTURE at the end of the animation: the switch-off in reverse, so
      the clip opens fully black (gain 0, band closed, picture scaled to
      a dot) and arrives at the picture.

    The captain, on Reel 09, 2026-09-11: *"also the tv on animation
    should start from fully black just like the reverse of how the tv
    off animation goes to fully black"*.  Two separately tuned builders
    could satisfy that sentence on the day and drift apart on the next
    re-timing, so there is ONE builder and the direction is a
    parameter.  ``tests/test_tv_power.py`` proves the switch-on's
    keyframes are the switch-off's, mirrored in time.

    The nodes and their names come from the switch-off, the half the
    captain named as the reference: a masked black ``Background``
    (``PowerBand``) draws the vertical deflection, a uniform
    ``Transform`` (``PowerDot``) the dot, a ``BrightnessContrast``
    (``PowerDecay``) the spot spike and the fall to black.  This was a
    ``Crop`` node until 2026-09-10 and it never once drew: Fusion's
    ``Crop`` has no ``CropTop``/``CropBottom``, so every 3840x2160
    source came out as its bottom-left quadrant on every reel's first
    and last picture clip.  ``library/tools/fusion/tool_inputs.py`` is
    the gate that now refuses the misspelling, and ``Crop`` was the
    wrong tool even spelled right - it resizes the image to the crop
    rectangle, so the frame would shrink rather than the picture
    blanking in place.

    ``played_frames`` is how many frames the timeline really renders for
    this clip.  The source span ``played_range`` derives can be longer -
    pool footage at 30 fps cut onto a 24000/1001 reel timeline plays
    fewer frames than the source span counts - and an animation keyed
    past the end of what plays is held flat over everything that does
    (``played_window``).  A clip with no room for the whole animation is
    REFUSED by name (``TransitionLongerThanTheClip``), the same refusal
    a transition half gets.  All-zero phases return an empty block.
    """
    from library.tools.tv_power import (
        BLACK_GAIN, COLLAPSE_CROP, DOT_GAIN, DOT_SIZE, LINE_GAIN,
        PICTURE_GAIN,
    )

    if direction not in ("on", "off"):
        raise ValueError(
            f"tv power direction must be 'on' or 'off', got {direction!r}")
    if collapse_crop is None:
        collapse_crop = COLLAPSE_CROP
    if dot_gain is None:
        dot_gain = DOT_GAIN
    if dot_size is None:
        dot_size = DOT_SIZE

    total = int(collapse_frames) + int(dot_frames) + int(decay_frames)
    if total <= 0:
        return EffectBlock(nodes=[], input_name="", output_name="")

    first, last = played_range(clip_dur, source_in, source_out)
    if played_frames is not None:
        last = min(last, max(first, int(played_frames) - 1))
    assert_ramp_fits(total, first, last, ttype="tv_power",
                     half="head" if direction == "on" else "tail")

    # The shape, picture -> black: cumulative frames from the picture
    # state, the Transform size there, and the gain there. The band is
    # open at PICTURE and closed from LINE onwards, which is the one
    # term `_power_band` animates - so it needs only the two times.
    states = (
        (0, 1.0, PICTURE_GAIN),
        (int(collapse_frames), 1.0, LINE_GAIN),
        (int(collapse_frames) + int(dot_frames), float(dot_size), dot_gain),
        (total, float(dot_size), BLACK_GAIN),
    )

    if direction == "off":
        picture_at = max(first, last - total)
        times = [picture_at + cum for cum, _, _ in states]
        play_order = list(zip(times, states))
        hold_before, hold_after = first, last
    else:
        picture_at = min(first + total, last)
        times = [picture_at - cum for cum, _, _ in states]
        # Played black first: the same states, reversed in time.
        play_order = list(reversed(list(zip(times, states))))
        hold_before, hold_after = first, last

    line_at = times[1]

    # `_power_band` animates the picture/line term between the two
    # states that carry it, and `open_at` names which end is the
    # picture. Off: picture then line. On: line then picture.
    if direction == "off":
        band_start, band_end, open_at = times[0], line_at, "start"
    else:
        band_start, band_end, open_at = line_at, times[0], "end"
    band_nodes, band_spline, band_in, band_out = _power_band(
        start_frame=band_start, end_frame=band_end,
        open_at=open_at, collapse_crop=collapse_crop,
        clip_dur=clip_dur, last=last, res=res,
    )

    # One key per state in PLAY order, plus a flat hold out to each end
    # of the played range. Two states landing on one frame (a zero-length
    # phase) collapse to the later one: the value the animation is moving
    # toward is the one that frame shows.
    keys: list = []
    if play_order[0][0] > hold_before:
        keys.append((hold_before, play_order[0][1][1], play_order[0][1][2]))
    for at, (_, size_value, gain_value) in play_order:
        if keys and keys[-1][0] == at:
            keys[-1] = (at, size_value, gain_value)
        else:
            keys.append((at, size_value, gain_value))
    if keys[-1][0] < hold_after:
        keys.append((hold_after, keys[-1][1], keys[-1][2]))

    tf_name = _next_name("PowerDot")
    tf = FusionNode(tf_name, "Transform")
    tf.set_attr("CtrlWZoom", False)
    size = BezierSpline(f"{tf_name}Size", color=(255, 200, 50))
    for at, size_value, _ in keys:
        size.add_key(at, size_value, flags={"Linear": True})
    tf.set_input("Size", size)
    # WIRED to the band above it. `EffectBlock` wires its own
    # `input_name` to whatever precedes the block and reads its
    # `output_name`; the links INSIDE a block are the block's to make.
    # Without this the node has no image input, the band's output goes
    # nowhere, and Resolve renders the clip as "The Fusion composition
    # at 00:00:00:00 could not be processed successfully" - a comp that
    # imports and cannot draw.
    tf.set_input("Input", band_out)
    tf.pos = (220, 0)

    bc_name = _next_name("PowerDecay")
    bc = FusionNode(bc_name, "BrightnessContrast")
    bc.set_input("Input", tf_name)
    gain = BezierSpline(f"{bc_name}Gain", color=(255, 255, 100))
    for at, _, gain_value in keys:
        gain.add_key(at, gain_value, flags={"Linear": True})
    bc.set_input("Gain", gain)
    bc.pos = (330, 0)

    return EffectBlock(
        nodes=band_nodes + [band_spline, tf, size, bc, gain],
        input_name=band_in,
        input_key="Background",
        output_name=bc_name,
    )


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
        easing: str = DRIFT_EASING,
        pan_start: Optional[tuple] = None,
        pan_end: Optional[tuple] = None,
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
        window: Optional[tuple] = None,
    ) -> EffectBlock:
        """Animated Ken Burns zoom with optional pan offset.

        Two shapes, chosen by what `mid` says. When `mid` is the
        midpoint of `start`/`end` the move is a DRIFT: one eased ramp
        from `start` to `end`, baked per frame with
        `BezierSpline.sampled(easing=...)` and linearized, so what
        Resolve holds IS the curve - Resolve ignores the Linear flags,
        and without explicit handles it would re-smooth the ramp into
        something else (the flags lesson in `nodes.linearize`). When
        `mid` sits off the midpoint the move is a PUNCH
        (`zoom_emphasis`): start -> peak -> end on a three-point
        spline, unchanged.

        Creates a Transform node with BezierSpline-animated Size
        and optional static Center offset.

        ``source_in`` / ``source_out`` are the first and last source
        frames the timeline plays.  Keyframes are placed within this
        window.  When omitted the whole source (0..clip_dur-1) is used.

        ``window`` is the anchored span as COMP frames `(first, last)`
        inclusive - the sub-block range a word/beat/frame anchor
        resolved to.  Keyframes land inside it and the Size holds
        neutral (1.0) outside it, so a punch on one word does not
        punch the whole item (finding 36).  None, or a window covering
        the whole played range, keys exactly as before.

        ``easing`` is one of `nodes.EASING_FUNCTIONS`' names; an
        unknown name raises rather than falling back, because a
        fallback curve would substitute taste. It is read only on the
        drift path - a punch keeps its three points whatever it says.

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

        # Keyframes in the comp's own frames, which are the
        # PLAYED frames numbered from zero.
        first, last = played_range(clip_dur, source_in, source_out)
        w0, w1 = first, last
        if window is not None:
            try:
                w0 = max(first, min(last, int(window[0])))
                w1 = max(first, min(last, int(window[1])))
            except (TypeError, ValueError, IndexError):
                w0, w1 = first, last
        windowed = (w0, w1) != (first, last)

        if windowed:
            # The anchored span: the move plays inside [w0, w1] and
            # the Size holds neutral outside it. Holds sit one frame
            # off the window edges so the punch snaps in and out
            # instead of ramping in from frame 0.
            held: dict[int, float] = {}
            if w0 > first:
                held[first] = 1.0
                if w0 - 1 > first:
                    held[w0 - 1] = 1.0
            if w1 < last:
                if w1 + 1 < last:
                    held[w1 + 1] = 1.0
                held[last] = 1.0
            spline_name = f"{tf_name}Size"
            if not has_anim:
                held[w0] = start
                held[w1] = end
            elif _is_midpoint(start, mid, end):
                if easing not in EASING_FUNCTIONS:
                    raise ValueError(
                        f"Unknown drift easing {easing!r}: choose one of "
                        f"{', '.join(sorted(EASING_FUNCTIONS))}."
                    )
                _ramp = BezierSpline.sampled(
                    spline_name,
                    start_frame=w0, end_frame=w1,
                    easing=easing,
                    scale=end - start, offset=start,
                    color=(233, 217, 11),
                )
                for _kf in _ramp.keyframes:
                    held[_kf.frame] = _kf.value
            else:
                seg_dur = w1 - w0
                mid_frame = w0 + seg_dur // 2
                held[w0] = start
                held[mid_frame] = mid
                held[w1] = end
            spline = BezierSpline(spline_name)
            for _f in sorted(held):
                spline.add_key(_f, round(held[_f], 6))
            spline.linearize()
            tf.set_input("Size", spline)
            nodes.append(spline)
        elif has_anim:

            spline_name = f"{tf_name}Size"
            if _is_midpoint(start, mid, end):
                # A drift: one eased ramp, baked per frame. `sampled`
                # writes Linear flags that Resolve ignores, so the
                # spline is linearized into explicit handles - the
                # easing is verified in the serialized comp, never in
                # the flags.
                if easing not in EASING_FUNCTIONS:
                    raise ValueError(
                        f"Unknown drift easing {easing!r}: choose one of "
                        f"{', '.join(sorted(EASING_FUNCTIONS))}."
                    )
                spline = BezierSpline.sampled(
                    spline_name,
                    start_frame=first, end_frame=last,
                    easing=easing,
                    scale=end - start, offset=start,
                    color=(233, 217, 11),
                )
                spline.linearize()
            else:
                # A punch: the three-point spline, peak at the middle.
                seg_dur = last - first
                mid_frame = first + seg_dur // 2
                third = seg_dur // 3
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
        is neutral, the units the reference stills were rendered in and the
        units `DeclaredLook.fusion()` emits - and it is emitted VERBATIM,
        because Fusion's `BrightnessContrast.Contrast` is neutral at 0.0
        too.

        **It carried `1.0 + contrast` between 2026-09-09 and 2026-09-10,
        and that is roughly eight times the declared grade.** The
        translation came from the grade-variant report's prediction that
        "Fusion's own neutral is 1.0 by Fusion's documentation"; nobody
        probed the tool. Measured on the live Reel 09 (frame 129, every
        other node neutral):

        =============  ===================================  ==========
        Contrast       vs bypassing the node entirely        mean luma
        =============  ===================================  ==========
        bypassed       -                                    45.39
        0.0            0 px changed, max delta 0            45.39
        0.12 declared  1,491,473 px, max delta 22           39.97
        1.12 shipped   1,492,630 px, max delta 91           19.74
        =============  ===================================  ==========

        `Contrast = 0.0` is BYTE-IDENTICAL to having no node at all, so
        0.0 is the neutral and 1.12 was crushing the picture by 25 luma
        where the captain asked for 5.

        Skips if all values are neutral (gain=1, contrast=0, sat=1) -
        the skip stays on the declaration, which is the same number the
        tool takes.
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
        gain: float,
        threshold: float,
        size: float,
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
        power: float,
        size: float,
    ) -> EffectBlock:
        """Film grain overlay.

        `power` is the declared strength and `size` the declared
        coarseness (`series_look.LOOK_ELEMENTS["grain"]`), passed verbatim
        to the terms that carry them.

        **The node drove `Power` and `Size` until 2026-09-10, and Fusion
        has neither.**  It has `MasterStrength` and `MasterXSize`/
        `MasterYSize`, so every declared grain was silently ignored and
        every graded clip carried a FilmGrain node sitting at its
        registry default strength of 0.1 - a strength nobody chose, which
        is the defect AGENTS.md 10.5 exists for, arriving through a NAME
        rather than through a `.get`.  `library/tools/fusion/
        tool_inputs.py` is the gate that now refuses it.

        Both size axes are set rather than relying on `LockSizeXY`: a
        lock is a UI convenience, and a comp that depends on one is a
        comp whose second axis is a default.
        """
        name = _next_name("FilmGrain")
        fg = FusionNode(name, "FilmGrain")
        fg.set_input("MasterStrength", power)
        fg.set_input("MasterXSize", size)
        fg.set_input("MasterYSize", size)
        fg.pos = (385, 0)

        return EffectBlock(nodes=[fg], input_name=name, output_name=name)

    @staticmethod
    def defocus(*, size: float) -> EffectBlock:
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
        width: float,
        height: float,
        soft: float,
        blend: float,
        color: tuple = (0.0, 0.0, 0.0),
        res: tuple,
    ) -> EffectBlock:
        """Elliptical vignette.

        Creates Background + EllipseMask + Merge triplet.
        The Background is colored (default black), masked by an INVERTED
        ellipse - solid at the corners, clear in the middle - then merged
        over the upstream image.

        The invert is `Invert`, which is the name the tool has. This block
        wrote `Inverted` until 2026-09-10, and Fusion ignored it in
        silence: the mask stayed solid INSIDE the ellipse, so the black
        Background drew as a DISC IN THE MIDDLE OF THE FRAME - the exact
        opposite of a vignette - on every clip that carried one. The
        captain found it by eye on Reel 09. `library/tools/fusion/
        tool_inputs.py` is the gate that now refuses the misspelling.

        The mask is rasterised at the image's own resolution. It was
        pinned at 320x240 (Fusion's own default frame) and scaled up to
        the source, which quantised the soft edge into visible steps on a
        3840x2160 frame.

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
        ellipse.set_input("MaskWidth", res[0])
        ellipse.set_input("MaskHeight", res[1])
        ellipse.set_input("PixelAspect", (1, 1))
        ellipse.set_input("Invert", 1)
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
        res: tuple,
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
        res: tuple,
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
        res: tuple,
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
        collapse_frames: int = 6,
        dot_frames: int = 3,
        decay_frames: int = 9,
        collapse_crop: Optional[float] = None,
        dot_gain: Optional[float] = None,
        dot_size: Optional[float] = None,
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
        played_frames: Optional[int] = None,
        res: tuple,
    ) -> EffectBlock:
        """Old-TV switch-ON at the head of the clip: black to picture.

        THE SWITCH-OFF PLAYED BACKWARDS, and literally so - both halves
        are `_tv_power`, which lays one shape down forwards or in
        reverse.  The clip opens FULLY BLACK (gain 0.0, band closed to
        a line, picture scaled to a dot), the spot rises out of the
        black over ``decay_frames``, the dot opens to a line over
        ``dot_frames``, and the line opens to the full picture over
        ``collapse_frames``, settling at pass-through gain.

        The captain, on Reel 09, 2026-09-11: *"also the tv on animation
        should start from fully black just like the reverse of how the
        tv off animation goes to fully black"*.  Until that marker this
        was a separate animation with its own phase names, its own
        lengths and a lit first frame; see ``library/tools/tv_power.py``
        for what it was and why one shape replaced it.
        """
        return _tv_power(
            clip_dur, direction="on",
            collapse_frames=collapse_frames, dot_frames=dot_frames,
            decay_frames=decay_frames, collapse_crop=collapse_crop,
            dot_gain=dot_gain, dot_size=dot_size,
            source_in=source_in, source_out=source_out,
            played_frames=played_frames, res=res)

    @staticmethod
    def tv_power_tail(
        clip_dur: int,
        *,
        collapse_frames: int = 6,
        dot_frames: int = 3,
        decay_frames: int = 9,
        collapse_crop: Optional[float] = None,
        dot_gain: Optional[float] = None,
        dot_size: Optional[float] = None,
        source_in: Optional[int] = None,
        source_out: Optional[int] = None,
        played_frames: Optional[int] = None,
        res: tuple,
    ) -> EffectBlock:
        """Old-TV switch-OFF at the tail of the clip: picture to black.

        The shape played forwards: the full picture collapses to a line
        over ``collapse_frames``, the line contracts to a dot over
        ``dot_frames`` while the spot spikes bright, and the afterglow
        decays to black over ``decay_frames``.  See ``tv_power_head``
        for the other direction and ``_tv_power`` for the one builder
        that draws both.
        """
        return _tv_power(
            clip_dur, direction="off",
            collapse_frames=collapse_frames, dot_frames=dot_frames,
            decay_frames=decay_frames, collapse_crop=collapse_crop,
            dot_gain=dot_gain, dot_size=dot_size,
            source_in=source_in, source_out=source_out,
            played_frames=played_frames, res=res)
    @staticmethod
    def shake(
        clip_dur: int,
        *,
        x_amount: float,
        y_amount: float,
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

    # `chromatic_aberration` and `lens_distortion` lived here until
    # 2026-09-10 and neither could ever have drawn: Fusion registers no
    # tool called `ChromaticAberration` at all, and `LensDistort` has no
    # bare `Distortion` input (its terms are per-model, e.g.
    # `DEClassicLDModel.Distortion`).  Nothing emitted their keys either -
    # `comp_builder` dispatched on `chromatic_aberration` /
    # `lens_distortion`, which no planner writes.  Step 4.03 offers
    # `chromatic_aberration` as one of DaVinci's own shipped MACROS
    # (`library/presets/resolve-builtin/tools/Chromatic Aberration.setting`,
    # loaded by `library/tools/fusion_macro_loader.py`), which is the real
    # route and is unaffected.  Removed rather than left: dead code that
    # states a capability the renderer does not have is the defect
    # AGENTS.md 10.2 names.


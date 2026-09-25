"""Turn one clip's effect parameters into a Fusion composition.

This module is the contract between the planners and the picture: it
dispatches on parameter NAMES, so a planner emitting a name nothing reads
here produces a comp without that effect in it and no warning. Three of
the five advertised VFX types failed exactly that way - `zoom_percent`,
`intensity_px` and `scale_factor` had no reader anywhere.

It deliberately lives beside the effects engine rather than in
`execution/apply_fusion_comps.py`: that module imports
DaVinciResolveScript at module level, so anything sharing it can only be
tested where DaVinci Resolve is installed.


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

- Never use `ApplyMode` in a Merge node: it crashes Resolve with a SIGSEGV.
- Never use `Path {}` when a Merge node exists in the same comp: it causes black output.
- Never use `BlendClone`; it is silently ignored.  Use `Tools = {`.
- Never omit `GlobalOut` on Background nodes: it stops rendering mid-clip.
- Never set DirectionalBlur `Length` greater than 5: it creates artifacts and edge tiling.
- Never set animated Transform zoom (Size) past `nodes.MAX_ANIMATED_ZOOM` (1.15): it is too aggressive and breaks immersion.

- **An input name the tool does not have is a SILENT no-op**, and four shipped that way: `Inverted` on EllipseMask (the name is `Invert`, so the vignette drew a black DISC in the middle), `Power`/`Size` on FilmGrain (`MasterStrength`/`MasterXSize`, so every declared grain was ignored), `CropTop`/`CropBottom` on Crop (`XOffset`/`YOffset`/`XSize`/`YSize`, so the old-TV switch cropped to the source's bottom-left corner and never animated), and a `ChromaticAberration` tool Fusion does not register at all. `library/tools/fusion/tool_inputs.py` carries what each tool really has, dumped off a running Resolve by `scripts/probe_fusion_tool_inputs.py`, and `FusionNode._validate` refuses anything else in an authored comp.
- Always set `Invert = Input { Value = 1, }` on EllipseMask for vignettes.
- Always include `MaskWidth`, `MaskHeight` and `PixelAspect` on EllipseMask.
- Always wire `Transform1.Input <- MediaIn1.Output` explicitly.
- Always use `Blend` instead of `BlendClone` for Merge opacity.
- Always include `GlobalOut` on Background nodes matching the clip duration.
- Use a static `Center` for animated pan/center (`Center = Input { Value = { x, y }, },`).
- **Size every Background node to the SOURCE clip's own resolution, never to the delivery format.** Read it off the MediaPoolItem's `Resolution` and do NOT swap it for rotation - Fusion gets the stored frame.
"""

from .effects import DRIFT_EASING, fx
from .engine import CompEngine


ZOOM_KEYS = ('zoom_start', 'zoom_mid', 'zoom_end', 'pan_start', 'pan_end')


def normalize_effects(effects, has_zoom):
    """Settle every default the comp generator would apply, in place.

    The bank key has to describe the comp that actually gets written, so
    every mutation of `effects` must happen before the key is derived -
    otherwise the lookup asks for a comp nobody ever banks.
    """
    if not has_zoom and 'vignette' not in effects:
        effects.setdefault('zoom_start', 1.0)
        effects.setdefault('zoom_mid', 1.0)
        effects.setdefault('zoom_end', 1.0)
        effects.setdefault('vignette', False)
    elif has_zoom:
        start = effects.get('zoom_start', 1.0)
        end = effects.get('zoom_end', 1.0)
        effects.setdefault('zoom_mid', round((start + end) / 2.0, 4))
    return effects


class MissingSourceFrame(ValueError):
    """A comp was asked for and nobody stated the frame Fusion sees.

    Raised rather than built at a guessed size. A Background smaller
    than the source paints a hard-edged rectangle in the middle of the
    picture, so a guessed canvas is a defect that ships silently -
    the renderer (`execution/apply_fusion_comps`) reads the size off
    the MediaPoolItem and refuses where Resolve will not state one.
    """


class UndeclaredEffectStrength(KeyError):
    """A clip armed an effect and did not say how strong it is.

    Raised rather than completed. AGENTS.md 10.5: how strong a glow is,
    how coarse a grain is and how deep a vignette falls off are the
    declaring author's decisions, and an engine-supplied value is a
    strength nobody chose arriving one level up. `series_look` refuses a
    half-declared element at the template; this is the same refusal at
    the renderer, for the plan-side callers that do not go through it.

    Every one of these was a live `.get(key, <number>)` here until
    2026-09-10 - glow 0.75/3.5, grain 0.25/1.5, vignette 0.25/0.35,
    defocus 2.0, shake 0.01 - which is the catalogue AGENTS.md 12 says
    this engine does not ship, still shipping through a different door.
    """


def _strength(effects: dict, armed_by: str, key: str) -> float:
    """One declared magnitude, or a refusal naming what armed it."""
    if key not in effects:
        raise UndeclaredEffectStrength(
            f"{armed_by} is armed on this clip but {key} is not declared, "
            f"so nothing says how strong it is. Declare it, or drop the "
            f"effect - the engine may not choose the number "
            f"(AGENTS.md 10.5)."
        )
    return effects[key]


def build_effect_comp(effects: dict, clip_dur: int,
                       source_res: tuple,
                       played_frames: int = None) -> str:
    """Turn one clip's effect parameters into a serialized Fusion comp.

    This function IS the contract between the planners and the picture:
    it dispatches on parameter NAMES, so a planner that emits a name not
    read here produces a comp without that effect in it and no warning.
    Three of the five advertised VFX types failed exactly that way
    (`zoom_percent`, `intensity_px`, `scale_factor` had no reader), which
    is why it is a plain function with a test rather than a loop body.

    ``source_res`` is the size of the image FUSION SEES - the source
    clip's own frame, not the delivery format. REQUIRED: there is no
    fallback size. Every Background node this builds (the vignette,
    the fade, both transition halves) is a solid image merged over
    ``MediaIn``, so a Background smaller than the source paints a
    hard-edged rectangle in the middle of the picture and leaves the
    rest ungraded. A caller that cannot state the frame raises
    ``MissingSourceFrame`` rather than shipping that rectangle.

    ``source_in_frame`` / ``source_out_frame`` in the *effects* dict
    bound the segment the timeline actually plays, in SOURCE frame
    numbers.  ``fusion.played_window`` translates them into the comp's
    own frames, which start at zero on the first played frame; every
    animated keyframe lands inside that range.  When absent the whole
    source is assumed, which is correct only when the placed segment
    uses all of it.

    ``played_frames`` is how many frames the timeline really renders
    for this clip (the timeline item's own duration).  The source span
    can count more - pool footage at 30 fps cut onto a 24000/1001 reel
    timeline plays fewer frames than the source span spans - and an
    end-anchored animation keyed past the end of what plays never
    draws: the spline extrapolates flat and the reel silently loses
    its switch-off.  It reaches the power builders, which clamp their
    horizon to it and refuse a ramp the clip has no room for.
    """
    if not source_res:
        raise MissingSourceFrame(
            "build_effect_comp needs source_res=(width, height) - the "
            "SOURCE clip's own frame, read off the MediaPoolItem's "
            "Resolution. Got nothing statable, so no canvas is sized "
            "and no comp is built."
        )
    res = tuple(source_res)
    engine = CompEngine(clip_dur=clip_dur, width=res[0], height=res[1])

    # The played segment within the source.  None means "whole source".
    src_in = effects.get('source_in_frame')
    src_out = effects.get('source_out_frame')

    # A behind_subject title reaches the timeline as a precomposed
    # overlay clip on a motion-graphics row, never as a comp effect -
    # so there is deliberately no dispatch for one here. The holes
    # the 1.06 matte punched are what put the title behind the
    # subject, not a merge (library/tools/behind_subject.py).

    # The backdrop branches off MediaIn1 by
    # name (its sharp picture branch reads the source, so a title
    # survives there only blurred in the backdrop - an accepted
    # imperfection of two branches meeting).
    # Everything after it - the Ken Burns drift, the grade, the
    # vignette - is meant to act on the composed picture, not on the
    # source behind it.
    if 'backdrop_picture_scale' in effects:
        engine.add(fx.subject_backdrop(
            picture_scale=effects['backdrop_picture_scale'],
            picture_center_x=effects.get('backdrop_picture_center_x', 0.5),
            backdrop_scale=effects.get('backdrop_scale', 1.0),
            backdrop_center_x=effects.get('backdrop_center_x', 0.5),
        ))

    if any(k in effects for k in ZOOM_KEYS):
        _window = effects.get('effect_window_frames')
        _win = None
        if (isinstance(_window, (list, tuple)) and len(_window) == 2):
            try:
                _win = (int(_window[0]), int(_window[1]))
            except (TypeError, ValueError):
                _win = None
        engine.add(fx.zoom(
            clip_dur,
            start=effects.get('zoom_start', 1.0),
            mid=effects.get('zoom_mid', 1.0),
            end=effects.get('zoom_end', 1.0),
            easing=effects.get('zoom_easing', DRIFT_EASING),
            pan_start=effects.get('pan_start'),
            pan_end=effects.get('pan_end'),
            source_in=src_in,
            source_out=src_out,
            window=_win,
        ))

    if 'grade_gain' in effects or 'grade_contrast' in effects or 'grade_saturation' in effects:
        engine.add(fx.grade(
            gain=effects.get('grade_gain', 1.0),
            contrast=effects.get('grade_contrast', 0.0),
            saturation=effects.get('grade_saturation', 1.0)
        ))

    # A subject-scoped grade: the plan's own values gated by a tracked
    # matte through EffectMask. The keys arrive from
    # `subject_grade.apply_subject_grades` via compile_manifest; every
    # value in them was written by the colourist, and an absent key
    # reads neutral (the axis is not moved), never a look.
    if 'subject_grade_matte' in effects:
        from library.tools.subject_grade import block_from_effects
        engine.add(block_from_effects(effects))

    if effects.get('glow_gain', 0.0) > 0:
        engine.add(fx.glow(
            gain=effects['glow_gain'],
            threshold=_strength(effects, 'glow_gain', 'glow_threshold'),
            size=_strength(effects, 'glow_gain', 'glow_size'),
        ))

    if effects.get('film_grain'):
        engine.add(fx.grain(
            power=_strength(effects, 'film_grain', 'film_grain_power'),
            size=_strength(effects, 'film_grain', 'film_grain_size'),
        ))

    if effects.get('defocus'):
        engine.add(fx.defocus(
            size=_strength(effects, 'defocus', 'defocus_size')))

    # `shake_x` and `shake_y` are two INDEPENDENT AXES, and an absent one
    # is 0.0 - the axis is not moved. That is a neutral, not a strength,
    # so it is the one dispatch here that may complete itself; the plan
    # contract (`plan_vfx.TOOLKIT_PARAMETERS`) advertises them
    # separately and drops an entry naming neither. It defaulted to 0.01
    # on both, which IS a strength and put a shake on an axis the plan
    # never asked to move.
    if 'shake_x' in effects or 'shake_y' in effects:
        engine.add(fx.shake(
            clip_dur,
            x_amount=effects.get('shake_x', 0.0),
            y_amount=effects.get('shake_y', 0.0),
            decay_frames=effects.get('shake_decay_frames'),
        ))

    # `chromatic_aberration` and `lens_distortion` were dispatched here
    # until 2026-09-10. Neither builder could draw - see
    # `fusion/effects.py` for what Fusion actually registers - and no
    # planner emitted either key. Step 4.03's `chromatic_aberration` is
    # DaVinci's own shipped macro and takes the macro-loader route.

    # A vignette is DRAWN ONLY WHERE ONE WAS ASKED FOR. This used to
    # default to True, so any clip carrying a zoom and no explicit
    # vignette key got one at blend 0.25 and soft 0.35 - two strengths
    # nobody chose, arriving through a `.get` default rather than through
    # a plan or a brand template. `normalize_effects` set `vignette:
    # False` for the no-zoom case only, which is why it never showed up
    # as an obvious bug: the half of the clips it hit were the ones with
    # a VFX zoom on them.
    #
    # `vignette_width` / `vignette_height` at 1.0 are the ONE thing here
    # that is not a strength: 1.0 is the ellipse inscribed in the frame,
    # which is the geometry of "a vignette" rather than a choice of how
    # much of one. Nothing declares them and `LOOK_ELEMENTS` offers no
    # slot, so they stay geometry. `vignette_color` absent is the absence
    # of a TINT, which `series_look` writes down as its reading.
    if effects.get('vignette'):
        engine.add(fx.vignette(
            clip_dur=clip_dur,
            width=effects.get('vignette_width', 1.0),
            height=effects.get('vignette_height', 1.0),
            soft=_strength(effects, 'vignette', 'vignette_soft'),
            blend=_strength(effects, 'vignette', 'vignette_blend'),
            color=effects.get('vignette_color', (0.0, 0.0, 0.0)),
            res=res,
        ))

    fade_in = effects.get('fade_in_frames', 0)
    fade_out = effects.get('fade_out_frames', 0)
    if fade_in > 0 or fade_out > 0:
        engine.add(fx.fade(clip_dur, fade_in=fade_in, fade_out=fade_out,
                           res=res, source_in=src_in, source_out=src_out))

    # The old-TV power animation (library/tools/tv_power.py).  Two keys,
    # one per DIRECTION of one shape: `tv_power_head` plays it black ->
    # picture at the clip head, `tv_power_tail` plays it picture ->
    # black at the tail.  Each is truthy to arm, with an optional
    # `<key>_timing` mapping overriding individual frame counts - the
    # same declared-timings-travel-as-params shape the zoom keys use.
    # Both read `switch_shape`, so the two directions carry the same
    # numbers unless a caller overrides one deliberately.  Absent keys
    # draw nothing.
    for key, builder in (('tv_power_head', fx.tv_power_head),
                         ('tv_power_tail', fx.tv_power_tail)):
        if not effects.get(key):
            continue
        from library.tools.tv_power import switch_shape
        timing = dict(switch_shape())
        timing.update(effects.get(f'{key}_timing') or {})
        engine.add(builder(
            clip_dur,
            collapse_frames=timing['collapse_frames'],
            dot_frames=timing['dot_frames'],
            decay_frames=timing['decay_frames'],
            collapse_crop=timing['collapse_crop'],
            source_in=src_in, source_out=src_out,
            played_frames=played_frames,
            res=res,
        ))

    tail_trans = effects.get('tail_transition')
    if tail_trans:
        engine.add(fx.transition_tail(
            clip_dur, tail_trans,
            effects.get('tail_transition_frames', 7), res=res,
            source_in=src_in, source_out=src_out))

    head_trans = effects.get('head_transition')
    if head_trans:
        engine.add(fx.transition_head(
            clip_dur, head_trans,
            effects.get('head_transition_frames', 7), res=res,
            source_in=src_in, source_out=src_out))

    return engine.serialize()


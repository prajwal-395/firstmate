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
"""

from .effects import fx
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


DEFAULT_SOURCE_RES = (1080, 1920)


def build_effect_comp(effects: dict, clip_dur: int,
                      source_res: tuple = None) -> str:
    """Turn one clip's effect parameters into a serialized Fusion comp.

    This function IS the contract between the planners and the picture:
    it dispatches on parameter NAMES, so a planner that emits a name not
    read here produces a comp without that effect in it and no warning.
    Three of the five advertised VFX types failed exactly that way
    (`zoom_percent`, `intensity_px`, `scale_factor` had no reader), which
    is why it is a plain function with a test rather than a loop body.

    ``source_res`` is the size of the image FUSION SEES - the source
    clip's own frame, not the delivery format. Every Background node this
    builds (the vignette, the fade, both transition halves) is a solid
    image merged over ``MediaIn``, so a Background smaller than the
    source paints a hard-edged rectangle in the middle of the picture and
    leaves the rest ungraded.

    ``source_in_frame`` / ``source_out_frame`` in the *effects* dict
    bound the segment the timeline actually plays.  All animated
    keyframes land inside this window.  When absent they default to
    ``0`` / ``clip_dur - 1`` (the whole source), which is correct only
    when the placed segment uses the entire source clip.
    """
    res = tuple(source_res) if source_res else DEFAULT_SOURCE_RES
    engine = CompEngine(clip_dur=clip_dur, width=res[0], height=res[1])

    # The played segment within the source.  None means "whole source".
    src_in = effects.get('source_in_frame')
    src_out = effects.get('source_out_frame')

    # The backdrop is FIRST, and must be: it branches off MediaIn1 by
    # name, and everything after it - the Ken Burns drift, the grade, the
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
        engine.add(fx.zoom(
            clip_dur,
            start=effects.get('zoom_start', 1.0),
            mid=effects.get('zoom_mid', 1.0),
            end=effects.get('zoom_end', 1.0),
            pan_start=effects.get('pan_start'),
            pan_end=effects.get('pan_end'),
            source_in=src_in,
            source_out=src_out,
        ))

    if 'grade_gain' in effects or 'grade_contrast' in effects or 'grade_saturation' in effects:
        engine.add(fx.grade(
            gain=effects.get('grade_gain', 1.0),
            contrast=effects.get('grade_contrast', 0.0),
            saturation=effects.get('grade_saturation', 1.0)
        ))

    if effects.get('glow_gain', 0.0) > 0:
        engine.add(fx.glow(
            gain=effects.get('glow_gain', 0.0),
            threshold=effects.get('glow_threshold', 0.75),
            size=effects.get('glow_size', 3.5)
        ))

    if effects.get('film_grain'):
        engine.add(fx.grain(
            power=effects.get('film_grain_power', 0.25),
            size=effects.get('film_grain_size', 1.5)
        ))

    if effects.get('defocus'):
        engine.add(fx.defocus(size=effects.get('defocus_size', 2.0)))
        
    if 'shake_x' in effects or 'shake_y' in effects:
        engine.add(fx.shake(
            clip_dur,
            x_amount=effects.get('shake_x', 0.01),
            y_amount=effects.get('shake_y', 0.01),
            decay_frames=effects.get('shake_decay_frames'),
        ))
        
    if 'chromatic_aberration' in effects or 'chromatic_aberration_amount' in effects:
        engine.add(fx.chromatic_aberration(amount=effects.get('chromatic_aberration_amount', 0.01)))

    if 'lens_distortion' in effects or 'lens_distortion_amount' in effects:
        engine.add(fx.lens_distortion(distortion=effects.get('lens_distortion_amount', 0.1)))

    if effects.get('vignette', True):
        engine.add(fx.vignette(
            clip_dur=clip_dur,
            width=effects.get('vignette_width', 1.0),
            height=effects.get('vignette_height', 1.0),
            soft=effects.get('vignette_soft', 0.35),
            blend=effects.get('vignette_blend', 0.25),
            color=effects.get('vignette_color', (0.0, 0.0, 0.0)),
            res=res,
        ))

    fade_in = effects.get('fade_in_frames', 0)
    fade_out = effects.get('fade_out_frames', 0)
    if fade_in > 0 or fade_out > 0:
        engine.add(fx.fade(clip_dur, fade_in=fade_in, fade_out=fade_out,
                           res=res, source_in=src_in, source_out=src_out))

    tail_trans = effects.get('tail_transition')
    if tail_trans:
        engine.add(fx.transition_tail(
            clip_dur, tail_trans,
            effects.get('tail_transition_frames', 7), res=res,
            source_out=src_out))

    head_trans = effects.get('head_transition')
    if head_trans:
        engine.add(fx.transition_head(
            clip_dur, head_trans,
            effects.get('head_transition_frames', 7), res=res,
            source_in=src_in, source_out=src_out))

    return engine.serialize()


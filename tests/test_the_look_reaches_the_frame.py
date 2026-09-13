"""What the captain saw on Reel 09, in the terms the comp can be checked in.

2026-09-10, on the Fusion page of his rebuilt Reel 09:

    *"on the ellipse node you have to invert it otherwise the vignette is
    inverted and the black actually shows up as a circle in the middle
    and not the vignette desired."*

    *"where exactly did you get that look from? (like the film grain, the
    soft glow and that brightness contrast, its an awful look"*

    *"the power crop node still exists ... it crops into the bottom left
    quadrant of the clip and fucks it up"*

Four defects, all measured in pixels off that timeline, all of the same
kind - a NUMBER or a NAME that the picture never received:

===================  ====================================================
what was declared    what the frame got
===================  ====================================================
vignette, inverted   `Inverted`, which EllipseMask does not have, so the
                     matte was solid INSIDE the ellipse. Isolated at
                     Merge Blend 1.0: centre luma 0.00 before, 53.89
                     after, corners 0.29/3.41/14.75/7.79 after.
grain 0.35           `Power`/`Size`, which FilmGrain does not have, so
                     `MasterStrength` sat at its registry default 0.1.
contrast 0.12        `1.0 + contrast` = 1.12. `Contrast = 0.0` renders
                     BYTE-IDENTICAL to bypassing the node, so 0.0 is the
                     neutral: 0.12 takes mean luma 45.39 -> 39.97, and
                     1.12 took it to 19.74.
the CRT switch       `CropTop`/`CropBottom`, which Crop does not have, so
                     it kept its own frame size at offset (0, 0) - and
                     Fusion's origin is bottom-left.
===================  ====================================================

The name half is gated by `tests/test_fusion_tool_inputs.py`. This file
holds the value half: the declared magnitude must arrive at the tool that
draws it, unchanged, and an effect armed without one must be REFUSED
rather than completed.

Where the answer is the captain's eye rather than a number - whether the
grain reads as texture or as noise, whether the vignette is too deep -
this file says nothing. That is `docs/RULE_EVIDENCE.md` territory and his
call, not a gate's (AGENTS.md 10.4).
"""
from __future__ import annotations

import re

import pytest

from library.tools.fusion.comp_builder import (
    UndeclaredEffectStrength, build_effect_comp,
)
from library.tools.series_look import resolve_look

CLIP_DUR = 120
#: The source frame every comp below is built at. The builder takes no
#: default frame, so each call states it - the same numbers the
#: removed default carried.
SOURCE_RES = (1080, 1920)

# The look the captain chose on 2026-09-10 (`v04_teal_split`, picked from
# five rendered variants), as a project declares it. Fixed here so the
# test never reaches a real project (AGENTS.md 8).
CHOSEN = {
    "name": "a_chosen_look",
    "cdl": {"slope": [1.03, 1.0, 0.96], "offset": [-0.01, 0.005, 0.02],
            "power": [1.0, 1.0, 1.0], "saturation": 1.12},
    "contrast": 0.12,
    "glow": {"gain": 0.20, "threshold": 0.72, "size": 3.5},
    "grain": {"power": 0.35, "size": 1.5},
    "vignette": {"blend": 0.35, "soft": 0.30},
}


def _value(comp: str, node: str, key: str) -> str:
    """The literal a named node carries on one input."""
    body = comp.split(f"{node} = ", 1)[1]
    match = re.search(rf"\b{key} = Input {{ Value = ([^,]+),", body)
    assert match, f"{node} carries no static {key}"
    return match.group(1)


# ── 1. The vignette darkens the CORNERS ──────────────────────────────────


def test_the_vignette_matte_is_inverted():
    """Un-inverted, the mask is solid inside the ellipse and the black
    Background it gates draws as a disc in the middle of the frame."""
    comp = build_effect_comp(dict(resolve_look(CHOSEN).fusion()), CLIP_DUR,
                             source_res=SOURCE_RES)
    assert "Ellipse1 = EllipseMask" in comp
    assert _value(comp, "Ellipse1", "Invert") == "1"
    assert "Inverted" not in comp


def test_the_vignette_mask_is_rasterised_at_the_source_frame():
    """It was pinned at Fusion's own 320x240 default and scaled up to the
    source, which quantised the soft edge into visible steps on a
    3840x2160 frame."""
    comp = build_effect_comp(dict(resolve_look(CHOSEN).fusion()), CLIP_DUR,
                             source_res=(3840, 2160))
    assert _value(comp, "Ellipse1", "MaskWidth") == "3840"
    assert _value(comp, "Ellipse1", "MaskHeight") == "2160"


# ── 2. Every declared magnitude reaches the tool that draws it ───────────


@pytest.mark.parametrize("node,key,expected", [
    ("BrightnessContrast1", "Contrast", "0.12"),
    ("SoftGlow1", "Gain", "0.2"),
    ("SoftGlow1", "Threshold", "0.72"),
    ("SoftGlow1", "XGlowSize", "3.5"),
    ("FilmGrain1", "MasterStrength", "0.35"),
    ("FilmGrain1", "MasterXSize", "1.5"),
    ("Merge1", "Blend", "0.35"),
    ("Ellipse1", "SoftEdge", "0.3"),
])
def test_each_declared_magnitude_arrives_unchanged(node, key, expected):
    comp = build_effect_comp(dict(resolve_look(CHOSEN).fusion()), CLIP_DUR,
                             source_res=SOURCE_RES)
    assert _value(comp, node, key) == expected


def test_contrast_is_not_translated_on_its_way_to_the_tool():
    """`1.0 + contrast` was eight times the declared grade. Fusion's own
    neutral is 0.0: `Contrast = 0.0` renders byte-identical to having no
    BrightnessContrast node at all."""
    comp = build_effect_comp({"grade_contrast": 0.12}, CLIP_DUR,
                             source_res=SOURCE_RES)
    assert _value(comp, "BrightnessContrast1", "Contrast") == "0.12"
    assert "1.12" not in comp


def test_a_neutral_contrast_draws_no_node_at_all():
    assert "BrightnessContrast" not in build_effect_comp(
        {}, CLIP_DUR, source_res=SOURCE_RES)


# ── 3. An armed effect with no strength is REFUSED, never completed ──────


@pytest.mark.parametrize("effects,missing", [
    ({"glow_gain": 0.2}, "glow_threshold"),
    ({"glow_gain": 0.2, "glow_threshold": 0.72}, "glow_size"),
    ({"film_grain": True}, "film_grain_power"),
    ({"film_grain": True, "film_grain_power": 0.35}, "film_grain_size"),
    ({"vignette": True}, "vignette_soft"),
    ({"vignette": True, "vignette_soft": 0.3}, "vignette_blend"),
    ({"defocus": True}, "defocus_size"),
])
def test_an_armed_effect_with_no_strength_is_refused(effects, missing):
    """AGENTS.md 10.5: how strong a glow is belongs to whoever declares
    it. Every one of these was a live `.get(key, <number>)` in
    `comp_builder` - the look catalogue this engine says it removed,
    still shipping through a different door."""
    with pytest.raises(UndeclaredEffectStrength, match=missing):
        build_effect_comp(dict(effects), CLIP_DUR,
                          source_res=SOURCE_RES)


def test_shake_completes_its_other_axis_because_zero_is_a_neutral():
    """The one dispatch that may finish itself: `shake_x` and `shake_y`
    are independent AXES and an absent one is 0.0, which is the axis not
    moving. It defaulted to 0.01 on both, which IS a strength and shook
    an axis the plan never named."""
    comp = build_effect_comp({"shake_x": 0.004}, CLIP_DUR,
                             source_res=SOURCE_RES)
    assert "ShakeTransform" in comp


def test_the_engine_offers_no_strength_of_its_own():
    """A grep, because the defect always comes back as a `.get` default.

    `grade_*` neutrals (gain 1.0, contrast 0.0, saturation 1.0) and the
    zoom identities are IDENTITIES - the axis is not moved - and the
    vignette's `width`/`height` of 1.0 is the ellipse inscribed in the
    frame, which is the geometry of "a vignette" rather than a choice of
    how much of one.
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent
              / "library" / "tools" / "fusion" / "comp_builder.py"
              ).read_text(encoding="utf-8")
    allowed = {
        "grade_gain": "1.0", "grade_contrast": "0.0",
        "grade_saturation": "1.0",
        "zoom_start": "1.0", "zoom_mid": "1.0", "zoom_end": "1.0",
        "vignette_width": "1.0", "vignette_height": "1.0",
        "vignette_color": "(0.0, 0.0, 0.0)",
        # The drift's easing SHAPE, recorded with its own evidence
        # (`fusion/effects.DRIFT_EASING`, captain 2026-09-09). A curve
        # shape is not a magnitude and there is no "how much" in it.
        "zoom_easing": "DRIFT_EASING",
        "backdrop_picture_center_x": "0.5", "backdrop_scale": "1.0",
        "backdrop_center_x": "0.5",
        "shake_x": "0.0", "shake_y": "0.0",
        "glow_gain": "0.0",
        "fade_in_frames": "0", "fade_out_frames": "0",
        "tail_transition_frames": "7", "head_transition_frames": "7",
    }
    offenders = []
    for key, default in re.findall(
            r"effects\.get\('([a-z_]+)',\s*((?:\([^)]*\)|[^)])+)\)",
            source):
        default = default.strip()
        if allowed.get(key) != default:
            offenders.append(f"{key}={default}")
    assert not offenders, (
        f"{offenders} reach a frame with no declaration behind them. A "
        f"strength the engine chose is the defect AGENTS.md 10.5 exists "
        f"for; add a neutral to `allowed` above only when the value means "
        f"THE AXIS IS NOT MOVED.")

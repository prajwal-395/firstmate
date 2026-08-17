"""P3.1: motion graphics under template control.

`generate_motion_props.py` set `show_accents = True` unconditionally, so
four glowing L-shaped corner brackets and a 12px progress bar sat on every
frame of every video, in `#00D4FF` - a cyan that is not any shipped
template's colour, because it was a hardcoded default rather than a
choice. Nothing in any config the pipeline reads could turn them off or
change the colour.

The colour now comes from the brand palette and the two elements are
template flags. Q3, answered 2026-08-16 by reframing rather than by
picking an option: templates are a growing library, so there is no
universal default. A template that declares NOTHING gets NOTHING, and
declaring is what turns an element on. That is the opposite of the old
behaviour, deliberately - preserving the old behaviour is precisely what
was rejected. A template that asks for accents it cannot colour from its
own palette fails, rather than falling back to the withdrawn cyan.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEP_DIR = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_4_06_render_motion_graphics")
for _p in (PROJECT_ROOT, STEP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_motion_props import (
    NEUTRAL_TEXT_COLOR,
    WITHDRAWN_LEGACY_ACCENT_COLOR,
    MissingAccentColor,
    _as_bool,
    generate_motion_props,
)

SPINE = {
    "structure": [
        {"block_type": "hook", "position": 0,
         "timeline_start": 0.0, "timeline_end": 3.0},
        {"block_type": "speech", "position": 1,
         "timeline_start": 3.0, "timeline_end": 8.0},
        {"block_type": "broll", "position": 2,
         "timeline_start": 8.0, "timeline_end": 11.0},
    ]
}


def props(**kw):
    return generate_motion_props({}, kw.pop("creative", {}), SPINE, **kw)


# ─────────────────────────────────────────────────────────
# The accent colour
# ─────────────────────────────────────────────────────────

ON = {"motion_accents": True, "motion_progress_bar": True}


def test_accent_comes_from_the_brand_palette():
    got = props(brand_style={"color_palette": ["#ff0055", "#ffffff", "#000000"]},
                brand_effect=ON)
    assert got, "no props generated"
    assert all(p["accentColor"] == "#ff0055" for p in got)


def test_palette_beats_creative_direction():
    """A brand palette is the brand; creative_direction is per-video."""
    got = props(creative={"accent_color": "#123456"},
                brand_style={"color_palette": ["#ff0055", "#ffffff", "#000000"]},
                brand_effect=ON)
    assert all(p["accentColor"] == "#ff0055" for p in got)


def test_creative_direction_is_used_when_no_palette():
    got = props(creative={"accent_color": "#123456"}, brand_effect=ON)
    assert all(p["accentColor"] == "#123456" for p in got)


def test_enabling_accents_without_a_usable_colour_raises():
    """cinematic_narrative's palette has no accent that would read.

    Its most saturated entry is a dark muted navy. Drawing 6px brackets in
    it would read as a smudge, and drawing them in a hardcoded cyan is
    what P3.1 removed - so a template that asks for accents it cannot
    colour is a contradiction, and it fails.
    """
    with pytest.raises(MissingAccentColor):
        props(brand_style={"color_palette": ["#223344", "#aabbcc", "#111111"]},
              brand_effect=ON)


def test_enabling_accents_with_no_palette_at_all_raises():
    with pytest.raises(MissingAccentColor):
        props(brand_effect=ON)


def test_the_withdrawn_cyan_never_reaches_a_frame():
    """It was never any template's colour, only an unconditional default."""
    for kwargs in (
        {},
        {"brand_style": {"color_palette": ["#ff0055", "#ffffff", "#000000"]},
         "brand_effect": ON},
        {"creative": {"accent_color": "#123456"}, "brand_effect": ON},
    ):
        for p in props(**kwargs):
            assert p["accentColor"] != WITHDRAWN_LEGACY_ACCENT_COLOR


# ─────────────────────────────────────────────────────────
# The flags: declaring is what turns an element on
# ─────────────────────────────────────────────────────────

def test_a_template_that_declares_nothing_gets_nothing():
    """The captain's ruling: templates are a library, not a default.

    This is the OPPOSITE of the old behaviour, which drew brackets and a
    progress bar on every frame of every video.
    """
    got = props()
    assert got, "no props generated"
    assert not any(p["showAccents"] for p in got)
    assert not any(p["showProgress"] for p in got)


def test_declaring_accents_turns_them_on():
    got = props(brand_style={"color_palette": ["#ff0055", "#ffffff", "#000000"]},
                brand_effect={"motion_accents": True})
    assert all(p["showAccents"] for p in got)


def test_declaring_the_progress_bar_turns_it_on():
    got = props(brand_effect={"motion_progress_bar": True})
    speech = [p for p in got if p["_block_position"] == 1]
    assert speech and speech[0]["showProgress"]


def test_the_two_elements_are_independent():
    got = props(brand_effect={"motion_progress_bar": True})
    assert not any(p["showAccents"] for p in got)
    assert any(p["showProgress"] for p in got)


def test_the_upper_third_is_unaffected_by_the_accent_flags():
    """It is a different element and it is not part of Q3."""
    got = props()
    assert any(p["showUpperThird"] for p in got)


def test_the_upper_third_stays_legible_with_no_palette():
    got = props()
    assert all(p["accentColor"] == NEUTRAL_TEXT_COLOR for p in got)


def test_the_upper_third_prefers_the_palette_text_colour():
    got = props(brand_style={"color_palette": ["#223344", "#aabbcc", "#111111"]})
    assert all(p["accentColor"] == "#aabbcc" for p in got)


def test_broll_still_has_no_progress_bar_when_enabled():
    """The pre-existing per-block rule must survive the new flag."""
    got = props(brand_effect={"motion_progress_bar": True})
    broll = [p for p in got if p["_block_position"] == 2]
    assert broll and not broll[0]["showProgress"]


class TestFlagCoercion:
    """YAML flags, and a typo that must not silently change the look."""

    @pytest.mark.parametrize("value,expected", [
        (True, True), (False, False),
        ("true", True), ("False", False), ("yes", True), ("no", False),
        ("on", True), ("off", False), ("1", True), ("0", False),
    ])
    def test_recognised_values(self, value, expected):
        assert _as_bool(value, not expected) is expected

    @pytest.mark.parametrize("value", ["maybe", "", 42, [], {}, object()])
    def test_unrecognised_values_keep_the_default(self, value):
        """A typo must not read as false and switch the house style off."""
        assert _as_bool(value, True) is True
        assert _as_bool(value, False) is False

    def test_none_keeps_the_default(self):
        assert _as_bool(None, True) is True
        assert _as_bool(None, False) is False


# ─────────────────────────────────────────────────────────
# The wiring, so the flags actually arrive
# ─────────────────────────────────────────────────────────

def test_the_step_declares_the_brand_slots():
    """Without the declaration the runner never injects them, and every
    flag above would be dead config."""
    with open(os.path.join(STEP_DIR, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    inputs = {i["name"] for i in manifest["interface"]["inputs"]}
    assert {"brand_style", "brand_effect"} <= inputs


def test_the_step_passes_the_brand_slots_through():
    with open(os.path.join(STEP_DIR, "step.py"), encoding="utf-8") as f:
        src = f.read()
    assert 'brand_style=data.get("brand_style"' in src
    assert 'brand_effect=data.get("brand_effect"' in src


def test_the_schema_carries_the_flags():
    from library.schemas.brand_template import BrandTemplate
    tmpl = BrandTemplate.from_dict(
        {"series_id": "x", "effect": {"motion_accents": False,
                                      "motion_progress_bar": False}})
    assert tmpl.effect.motion_accents is False
    assert tmpl.effect.motion_progress_bar is False
    # Unset must stay None, not False, or "said nothing" would read as
    # "turn it off".
    assert BrandTemplate.from_dict(
        {"series_id": "x"}).effect.motion_accents is None


def test_no_hardcoded_cyan_outside_the_named_constant():
    """The default must be nameable, so it can be discussed and changed."""
    with open(os.path.join(STEP_DIR, "generate_motion_props.py"),
              encoding="utf-8") as f:
        src = f.read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert code.count("#00D4FF") == 1, (
        "the withdrawn cyan must appear once, as "
        "WITHDRAWN_LEGACY_ACCENT_COLOR, and never as a live fallback")


# ─────────────────────────────────────────────────────────
# The shipped library: which templates declare accents
# ─────────────────────────────────────────────────────────

import glob

import yaml

TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "library", "templates")

# Approved 2026-08-16. Each is one flag to reverse, but a change here
# should be a decision rather than a drift.
EXPECTED_DECLARATIONS = {
    "shortform_energetic": {"motion_accents": True, "motion_progress_bar": True},
    "interview_professional": {"motion_accents": True, "motion_progress_bar": False},
    "cinematic_narrative": {},
    "default_brand": {},
    "fourth_wall": {},
}


def _template(name):
    with open(os.path.join(TEMPLATE_DIR, f"{name}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_every_template_is_accounted_for():
    on_disk = {os.path.basename(p)[:-5]
               for p in glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))}
    assert on_disk == set(EXPECTED_DECLARATIONS), (
        "a template was added or removed without deciding whether it "
        "carries motion accents")


@pytest.mark.parametrize("name", sorted(EXPECTED_DECLARATIONS))
def test_templates_declare_what_was_approved(name):
    effect = _template(name).get("effect") or {}
    declared = {k: effect[k] for k in
                ("motion_accents", "motion_progress_bar") if k in effect}
    assert declared == EXPECTED_DECLARATIONS[name]


def test_cinematic_narrative_cannot_carry_accents():
    """Its accent-free state is a mechanism's answer, not an oversight.

    The palette is ["#223344", "#aabbcc", "#111111"] and its most
    saturated entry is a dark muted navy that would sit almost on top of
    its own near-black outline. `brand_palette` rejects it, so the choice
    was made by the palette rather than by taste.

    This test exists so that turning them on "helpfully" fails here, with
    the reason, rather than at render time or - worse - not at all.
    """
    tmpl = _template("cinematic_narrative")
    with pytest.raises(MissingAccentColor):
        generate_motion_props({}, {}, SPINE, brand_style=tmpl.get("style"),
                              brand_effect={"motion_accents": True})


def test_the_templates_that_declare_accents_can_actually_draw_them():
    """A declaration that raises at render time is worse than none."""
    for name, expected in EXPECTED_DECLARATIONS.items():
        if not expected.get("motion_accents"):
            continue
        tmpl = _template(name)
        got = generate_motion_props(
            {}, {}, SPINE, brand_style=tmpl.get("style"),
            brand_effect=tmpl.get("effect"))
        assert got, name
        assert all(p["showAccents"] for p in got), name
        assert all(p["accentColor"] != WITHDRAWN_LEGACY_ACCENT_COLOR
                   for p in got), name


def test_default_brand_declares_nothing():
    """The fallback template must not paint anything in its placeholder
    palette of three pure RGB primaries."""
    effect = _template("default_brand").get("effect") or {}
    assert "motion_accents" not in effect
    got = generate_motion_props({}, {}, SPINE,
                                brand_style=_template("default_brand").get("style"),
                                brand_effect=effect)
    assert not any(p["showAccents"] for p in got)
    assert not any(p["showProgress"] for p in got)


def test_the_fallback_template_has_a_real_palette():
    """default_brand is what a project gets when it names no template.

    Its palette used to be three pure RGB primaries - placeholder data
    that stopped being harmless once colour started coming from the
    palette: the upper-third subtitle rendered in #ff0000. A fallback
    whose job is to work should render something legible.
    """
    from library.tools.brand_palette import (
        hex_to_rgb, is_usable_accent, roles_from_palette,
    )
    palette = (_template("default_brand").get("style") or {}).get("color_palette")
    assert palette, "the fallback template must carry a palette"

    primaries = {"#ff0000", "#00ff00", "#0000ff"}
    assert not {c.lower() for c in palette} & primaries, (
        "pure RGB primaries are placeholder data, not a palette")

    roles = roles_from_palette(palette)
    assert "text" in roles and "outline" in roles

    # Deliberately NO accent: a fallback should not invent a brand colour.
    assert "accent" not in roles, (
        "the fallback template must not carry an accent - anything it "
        "declared would be mistaken for a brand colour")
    assert not any(is_usable_accent(hex_to_rgb(c)) for c in palette)


def test_the_fallback_upper_third_is_legible():
    """The concrete regression: it rendered #ff0000 before."""
    tmpl = _template("default_brand")
    got = generate_motion_props({}, {}, SPINE, brand_style=tmpl.get("style"),
                                brand_effect=tmpl.get("effect"))
    assert all(p["accentColor"] == "#F5F5F5" for p in got)
    assert all(p["accentColor"].lower() != "#ff0000" for p in got)

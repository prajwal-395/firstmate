"""P3.2: the brand template must reach the caption.

Captions are on screen for most of the runtime and every project rendered
them identically - Montserrat 800 at 58px, white with a #FBF0B8 accent -
because `generate_remotion_props.py` supplied that as a default for a
`style` key step 4.01 never wrote. All four templates declared
`effect.subtitle_style`, `style.typography` and `style.color_palette`, and
none of it reached anything.

These tests hold the three ends together: the enumeration, the templates
that name it, and the Remotion component that reads the props. The same
contract `test_transition_vocabulary` and `test_house_look` enforce.
"""
import glob
import json
import os
import re
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (PROJECT_ROOT,
           os.path.join(PROJECT_ROOT, "library", "steps", "step_4_05_render_subtitles")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from library.tools.subtitle_style import (
    LEGACY_ACCENT_COLOR,
    LEGACY_FONT_COLOR,
    LEGACY_FONT_SIZE,
    LEGACY_FONT_WEIGHT,
    LEGACY_OUTLINE_WIDTH,
    SUBTITLE_STYLES,
    UnknownSubtitleStyle,
    VALID_POSITIONS,
    get_subtitle_style,
    resolve_subtitle_style,
)

TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "library", "templates")
OVERLAY_DIR = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "compositions", "SubtitleOverlay")


def _templates():
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            yield os.path.basename(path), yaml.safe_load(f) or {}


# ─────────────────────────────────────────────────────────
# The enumeration and the templates must agree, both ways
# ─────────────────────────────────────────────────────────

def test_every_template_names_a_style_that_exists():
    unknown = []
    for name, tmpl in _templates():
        wanted = (tmpl.get("effect") or {}).get("subtitle_style")
        if wanted and wanted not in SUBTITLE_STYLES:
            unknown.append((name, wanted))
    assert not unknown, (
        f"templates name subtitle styles that do not exist: {unknown}. "
        f"Available: {sorted(SUBTITLE_STYLES)}")


def test_no_orphan_styles():
    """A look no template names is a look nobody chose."""
    named = set()
    for _name, tmpl in _templates():
        wanted = (tmpl.get("effect") or {}).get("subtitle_style")
        if wanted:
            named.add(wanted)
    orphans = set(SUBTITLE_STYLES) - named
    assert not orphans, (
        f"subtitle styles no template names: {sorted(orphans)}. Add a "
        f"template that uses one, or delete it.")


def test_every_style_records_where_it_came_from():
    missing = [n for n, s in SUBTITLE_STYLES.items() if not s.derived_from.strip()]
    assert not missing, (
        f"styles with no derived_from: {missing}. Say what the values are "
        f"based on, or the next agent cannot tell design from accident.")


def test_style_shapes_are_sane():
    for name, style in SUBTITLE_STYLES.items():
        assert style.name == name
        assert 20 <= style.font_size <= 250, name
        assert 100 <= style.font_weight <= 900, name
        assert 0 <= style.outline_width <= 24, name
        assert style.position in VALID_POSITIONS, name


# ─────────────────────────────────────────────────────────
# Unknown names raise rather than defaulting
# ─────────────────────────────────────────────────────────

def test_unknown_style_raises():
    with pytest.raises(UnknownSubtitleStyle):
        get_subtitle_style("neon_wobble")


def test_unknown_style_names_the_alternatives():
    with pytest.raises(UnknownSubtitleStyle) as exc:
        get_subtitle_style("neon_wobble")
    for available in SUBTITLE_STYLES:
        assert available in str(exc.value)


def test_a_template_naming_a_bad_style_fails_the_run():
    """Falling back is how subtitle_style came to mean nothing."""
    with pytest.raises(UnknownSubtitleStyle):
        resolve_subtitle_style({"subtitle_style": "does_not_exist"}, {})


# ─────────────────────────────────────────────────────────
# Resolution: shape from the style, brand from the template
# ─────────────────────────────────────────────────────────

class TestResolution:

    def test_no_template_at_all_keeps_the_legacy_look(self):
        """A project with no brand template must still render."""
        got = resolve_subtitle_style({}, {})
        assert got["fontSize"] == LEGACY_FONT_SIZE
        assert got["fontWeight"] == LEGACY_FONT_WEIGHT
        assert got["fontColor"] == LEGACY_FONT_COLOR
        assert got["accentColor"] == LEGACY_ACCENT_COLOR
        assert got["outlineWidth"] == LEGACY_OUTLINE_WIDTH

    def test_default_subtitles_is_byte_for_byte_the_old_look(self):
        assert resolve_subtitle_style(
            {"subtitle_style": "default_subtitles"}, {}) == resolve_subtitle_style({}, {})

    def test_default_subtitles_ignores_a_placeholder_palette(self):
        """default_brand's palette is three pure RGB primaries.

        Painting captions red because a placeholder palette lists #ff0000
        would be worse than the look it replaced.
        """
        got = resolve_subtitle_style(
            {"subtitle_style": "default_subtitles"},
            {"color_palette": ["#ff0000", "#00ff00", "#0000ff"]})
        assert got["accentColor"] == LEGACY_ACCENT_COLOR

    def test_named_styles_differ_from_each_other(self):
        """Four names that render identically would be four lies."""
        seen = {}
        for name in SUBTITLE_STYLES:
            props = json.dumps(resolve_subtitle_style({"subtitle_style": name}, {}),
                               sort_keys=True)
            assert props not in seen, f"{name} renders identically to {seen[props]}"
            seen[props] = name

    def test_typography_overrides_the_style_shape(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"typography": {"font": "Helvetica", "size": 42, "weight": "bold"}})
        assert got["fontFamily"] == "Helvetica"
        assert got["fontSize"] == 42
        assert got["fontWeight"] == 700

    def test_weight_accepts_words_and_numbers(self):
        for value, expected in [("bold", 700), ("black", 900), ("Semi-Bold", 600),
                                (500, 500), ("500", 500)]:
            got = resolve_subtitle_style(
                {"subtitle_style": "bold_large"}, {"typography": {"weight": value}})
            assert got["fontWeight"] == expected, value

    def test_malformed_typography_falls_back_rather_than_crashing(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"typography": {"size": "enormous", "weight": None, "font": ""}})
        assert got["fontSize"] == SUBTITLE_STYLES["bold_large"].font_size
        assert got["fontWeight"] == SUBTITLE_STYLES["bold_large"].font_weight


class TestPaletteDerivation:

    PALETTE = ["#ff0055", "#00ffcc", "#ffffff", "#000000"]

    def test_text_is_the_lightest_entry(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"}, {"color_palette": self.PALETTE})
        assert got["fontColor"].lower() == "#ffffff"

    def test_outline_is_the_darkest_entry(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"}, {"color_palette": self.PALETTE})
        assert got["outlineColor"].lower() == "#000000"

    def test_accent_is_a_saturated_entry(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"}, {"color_palette": self.PALETTE})
        assert got["accentColor"].lower() in ("#ff0055", "#00ffcc")

    def test_dark_muted_accent_is_rejected(self):
        """cinematic_narrative's real palette.

        `#223344` is the most saturated entry but it is a dark muted navy
        that would sit on top of its own `#111111` outline and read as a
        smudge rather than as emphasis.
        """
        got = resolve_subtitle_style(
            {"subtitle_style": "minimal"},
            {"color_palette": ["#223344", "#aabbcc", "#111111"]})
        assert got["accentColor"] == LEGACY_ACCENT_COLOR
        # The neutrals are still honoured - only the accent was unusable.
        assert got["fontColor"] == "#aabbcc"
        assert got["outlineColor"] == "#111111"

    def test_dark_but_vivid_accent_is_kept(self):
        """shortform_energetic's `#ff0055` is as dark as `#223344` by
        luminance and unmistakable on screen. Vividness is what tells
        them apart."""
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"color_palette": ["#ff0055", "#00ffcc", "#ffffff", "#000000"]})
        assert got["accentColor"] == "#ff0055"

    def test_light_muted_accent_is_kept(self):
        """interview_professional's warm tan is muted but not dark."""
        got = resolve_subtitle_style(
            {"subtitle_style": "clean_standard"},
            {"color_palette": ["#f5f5f5", "#333333", "#ddab7e"]})
        assert got["accentColor"] == "#ddab7e"

    def test_greyscale_palette_keeps_the_style_accent(self):
        """A grey accent vanishes into the text, so it is not an accent."""
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"color_palette": ["#ffffff", "#888888", "#111111"]})
        assert got["accentColor"] == LEGACY_ACCENT_COLOR

    def test_malformed_palette_degrades_instead_of_failing(self):
        for palette in ([], ["not a colour"], ["#12"], [None, 42], ["#GGGGGG"]):
            got = resolve_subtitle_style({"subtitle_style": "bold_large"},
                                         {"color_palette": palette})
            assert got["fontColor"] == LEGACY_FONT_COLOR

    def test_short_hex_is_understood(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"}, {"color_palette": ["#fff", "#000"]})
        assert got["fontColor"].lower() == "#fff"


# ─────────────────────────────────────────────────────────
# Every key produced must have a reader in the component
# ─────────────────────────────────────────────────────────

def test_every_prop_is_read_by_the_remotion_component():
    """The manifest-reader contract, applied to the caption props.

    `fontWeight` was hardcoded to 800 in the component while templates
    declared a weight, which is the same inert-key failure in miniature.
    """
    with open(os.path.join(OVERLAY_DIR, "index.tsx"), encoding="utf-8") as f:
        index_src = f.read()
    with open(os.path.join(OVERLAY_DIR, "AnimatedWord.tsx"), encoding="utf-8") as f:
        word_src = f.read()
    combined = index_src + word_src

    props = resolve_subtitle_style({"subtitle_style": "bold_large"}, {})
    unread = [k for k in props if f"style?.{k}" not in combined]
    assert not unread, (
        f"props with no reader in SubtitleOverlay: {unread}. Add the reader "
        f"in the same commit, or stop emitting the key.")


def test_the_props_generator_has_no_hardcoded_style_default():
    """The default is the bug. It must not come back."""
    path = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_4_05_render_subtitles", "generate_remotion_props.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # Comments explain the bug on purpose; only executable code counts.
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#"))
    code = re.sub(r'"""[\s\S]*?"""', "", code)

    assert 'subtitle_data.get("style", {' not in code
    assert "subtitle_data.get('style', {" not in code
    assert "Montserrat" not in code, (
        "generate_remotion_props must not name a font: the look comes from "
        "the brand template via step 4.01.")


def test_missing_style_raises_rather_than_defaulting():
    from generate_remotion_props import generate_subtitle_props_per_block
    entries = [{
        "text": "hello", "timeline_start": 0.0, "timeline_end": 1.0,
        "spine_block_position": 0, "emphasis_words": [], "words": [],
    }]
    with pytest.raises(ValueError, match="style"):
        generate_subtitle_props_per_block({"subtitle_entries": entries})


def test_step_4_01_emits_a_style():
    """The producer end, so the two halves cannot drift apart."""
    manifest_path = os.path.join(PROJECT_ROOT, "library", "steps",
                                 "step_4_01_plan_subtitles", "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    inputs = {i["name"] for i in manifest["interface"]["inputs"]}
    assert {"brand_style", "brand_effect"} <= inputs, (
        "step 4.01 must declare both brand slots, or the pipeline runner "
        "will not inject them and the template cannot reach the caption.")
    out = manifest["interface"]["outputs"][0]
    assert "style" in out["expected_schema"]


def test_every_shipped_template_resolves_to_a_usable_accent():
    """No template may render an accent that vanishes.

    The accent is the only thing distinguishing an emphasised word, so an
    unusable one silently undoes step 4.01's whole emphasis pass.
    """
    from library.tools.brand_palette import (
        hex_to_rgb, is_usable_accent, luminance, saturation,
    )
    for name, tmpl in _templates():
        props = resolve_subtitle_style(tmpl.get("effect"), tmpl.get("style"))
        rgb = hex_to_rgb(props["accentColor"])
        assert rgb is not None, f"{name}: unparseable accent"
        sat, lum = saturation(rgb), luminance(rgb)
        assert is_usable_accent(rgb), (
            f"{name} resolves to accent {props['accentColor']} "
            f"(saturation {sat:.2f}, luminance {lum:.2f}), which would not "
            f"read as emphasis on screen")


def test_every_shipped_template_resolves_without_raising():
    for name, tmpl in _templates():
        props = resolve_subtitle_style(tmpl.get("effect"), tmpl.get("style"))
        assert props["fontSize"] > 0, name
        assert props["position"] in VALID_POSITIONS, name


def test_emphasis_size_comes_from_font_size_not_transform():
    """A scaled glyph reserves no layout width.

    `transform: scale(1.14)` grew an emphasised word without widening its
    box, so it overflowed by width*(scale-1)/2 on each side and collided
    with its neighbours: "the brand template" rendered as
    "thebrandtemplate". It got worse the larger the caption, which the
    brand-template styles make routine at 72px/900.
    """
    path = os.path.join(OVERLAY_DIR, "AnimatedWord.tsx")
    with open(path, encoding="utf-8") as f:
        src = f.read()

    assert "fontSize: `${fontScale}em`" in src, (
        "emphasis must be sized with fontSize so layout reserves the width")

    # The entry transform may still animate, but it must never exceed 1,
    # or the overlap comes straight back.
    assert "[0.95, 1]" in src, (
        "the entry animation must grow INTO place (0.95 -> 1), never past "
        "1, or a scaled glyph overlaps its neighbours again")
    assert "enterScale" not in src
    assert "restScale" not in src

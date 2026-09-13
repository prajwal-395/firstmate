"""P3.2: the brand template must reach the caption.

Captions are on screen for most of the runtime and every project rendered
them identically - Montserrat 800 at 58px, white with a #FBF0B8 accent -
because `generate_remotion_props.py` supplied that as a default for a
`style` key step 4.01 never wrote. All four templates declared
`effect.subtitle_style`, `style.typography` and `style.color_palette`, and
none of it reached anything.

These tests hold the three ends together: the enumeration, the templates
that name it, and the Remotion component that reads the props. The same
contract `test_transition_vocabulary` and `test_series_look` enforce.
"""
import glob
import json
import os
import re
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.subtitle_style import (
    LEGACY_ACCENT_COLOR,
    LEGACY_FONT_COLOR,
    LEGACY_FONT_SIZE,
    LEGACY_FONT_WEIGHT,
    LEGACY_OUTLINE_WIDTH,
    SUBTITLE_STYLES,
    TYPOGRAPHY_KEYS,
    UnknownSubtitleStyle,
    VALID_POSITIONS,
    get_subtitle_style,
    project_subtitle_typography,
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
    from library.steps.step_4_05_render_subtitles.generate_remotion_props import (  # noqa: E402
        generate_subtitle_props_per_block,
    )
    entries = [{
        "text": "hello", "timeline_start": 0.0, "timeline_end": 1.0,
        "spine_block_position": 0, "emphasis_words": [], "words": [],
    }]
    with pytest.raises(ValueError, match="style"):
        generate_subtitle_props_per_block(
            {"subtitle_entries": entries}, width=1080, height=1920)


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


# ─────────────────────────────────────────────────────────
# A project may typeset its own captions
# ─────────────────────────────────────────────────────────

def _project(tmp_path, pipeline_block):
    """A throwaway project.yaml declaring `pipeline_block`."""
    body = {"name": "T", "slug": "t"}
    if pipeline_block is not None:
        body["pipeline"] = pipeline_block
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(body),
                                           encoding="utf-8")
    return str(tmp_path)


class TestProjectTypography:
    """`pipeline.subtitle_typography` - one video typeset for itself.

    Project 001 is why this exists: the captain measured ~85px off a
    Text+ block they placed on its timeline and said the typography was
    "for this specific test project only and is not meant to be the end
    standard design". A number that governs one video does not belong in
    a preset four other videos read.
    """

    def test_a_project_declaring_nothing_gets_the_template_look(self, tmp_path):
        folder = _project(tmp_path, None)
        assert project_subtitle_typography(folder) is None
        assert resolve_subtitle_style({}, {}, folder)["fontSize"] \
            == LEGACY_FONT_SIZE

    def test_a_declared_size_outranks_the_style_default(self, tmp_path):
        folder = _project(tmp_path, {"subtitle_typography": {"size": 85}})
        assert resolve_subtitle_style({}, {}, folder)["fontSize"] == 85

    def test_a_declared_size_outranks_the_template_typography(self, tmp_path):
        folder = _project(tmp_path, {"subtitle_typography": {"size": 85}})
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"typography": {"size": 192, "font": "Montserrat"}}, folder)
        assert got["fontSize"] == 85

    def test_it_overrides_key_by_key_and_keeps_the_rest(self, tmp_path):
        """Asking for a size is not asking to give up the typeface.

        Replacing the whole slot would take the template's font and
        weight with it, silently - the class of degradation this
        repository keeps having to undo.
        """
        folder = _project(tmp_path, {"subtitle_typography": {"size": 85}})
        got = resolve_subtitle_style(
            {}, {"typography": {"font": "Montserrat", "weight": 600}}, folder)
        assert got["fontSize"] == 85
        assert got["fontFamily"] == "Montserrat"
        assert got["fontWeight"] == 600

    def test_the_four_presets_and_the_legacy_default_do_not_move(self):
        """A project declaration is not a change to what anyone else gets."""
        assert LEGACY_FONT_SIZE == 160
        assert SUBTITLE_STYLES["bold_large"].font_size == 192
        assert SUBTITLE_STYLES["clean_standard"].font_size == 144
        assert SUBTITLE_STYLES["minimal"].font_size == 120
        assert SUBTITLE_STYLES["default_subtitles"].font_size == LEGACY_FONT_SIZE

    def test_a_key_nothing_reads_is_refused_by_name(self, tmp_path):
        folder = _project(tmp_path, {"subtitle_typography": {"colour": "red"}})
        with pytest.raises(ValueError, match="colour"):
            project_subtitle_typography(folder)

    def test_a_declaration_that_is_not_a_mapping_raises(self, tmp_path):
        folder = _project(tmp_path, {"subtitle_typography": 85})
        with pytest.raises(TypeError):
            project_subtitle_typography(folder)

    def test_the_key_enumeration_is_what_resolve_reads(self):
        """A fourth key would be a declaration nothing draws."""
        assert set(TYPOGRAPHY_KEYS) == {"font", "size", "weight"}

    def test_the_schema_round_trips_a_declaration(self, tmp_path):
        from library.schemas.project_config import (
            _dict_to_project_config, project_config_to_dict)
        cfg = _dict_to_project_config({
            "name": "T", "slug": "t",
            "pipeline": {"subtitle_typography": {"size": 85}}})
        assert cfg.validate() == []
        assert cfg.pipeline.subtitle_typography == {"size": 85}
        assert project_config_to_dict(cfg)["pipeline"]["subtitle_typography"] \
            == {"size": 85}

    def test_the_schema_omits_it_when_undeclared(self):
        from library.schemas.project_config import (
            _dict_to_project_config, project_config_to_dict)
        cfg = _dict_to_project_config({"name": "T", "slug": "t"})
        assert "subtitle_typography" not in project_config_to_dict(cfg)["pipeline"]

    def test_the_schema_reports_a_key_nothing_reads(self):
        from library.schemas.project_config import _dict_to_project_config
        cfg = _dict_to_project_config({
            "name": "T", "slug": "t",
            "pipeline": {"subtitle_typography": {"colour": "red"}}})
        assert any("subtitle_typography" in e for e in cfg.validate())


# ── A project may caption each speaker differently ───────────────────
#
# The field-test podcast is two people on two tracks, and the captain
# asked for "a different subtitle styling per speaker to be able to
# emphasize the diarization" - the styling IS the signal. The values are
# the captain's taste and live in the project (AGENTS.md 14); what is
# tested here is that the engine declares NONE of its own.

def _project_with(tmp_path, body):
    project = tmp_path / "project"
    (project / "raw").mkdir(parents=True)
    (project / "project.yaml").write_text(f"name: T\nslug: t\n{body}")
    return str(project)


_TWO_SPEAKERS = """pipeline:
  speaker_subtitle_styles:
    Akshita:
      accentColor: '#FFB8D4'
    Craig:
      accentColor: '#FBF0B8'
      position: top
"""


def test_each_speaker_gets_their_declared_look(tmp_path):
    from library.tools.subtitle_style import resolve_subtitle_style
    folder = _project_with(tmp_path, _TWO_SPEAKERS)
    akshita = resolve_subtitle_style(project_folder=folder, speaker="Akshita")
    craig = resolve_subtitle_style(project_folder=folder, speaker="Craig")
    assert akshita["accentColor"] == "#FFB8D4"
    assert craig["accentColor"] == "#FBF0B8"
    assert craig["position"] == "top"
    assert akshita["position"] == "bottom", "an undeclared key is untouched"


def test_a_speaker_the_project_does_not_name_changes_nothing(tmp_path):
    from library.tools.subtitle_style import resolve_subtitle_style
    folder = _project_with(tmp_path, _TWO_SPEAKERS)
    shared = resolve_subtitle_style(project_folder=folder)
    other = resolve_subtitle_style(project_folder=folder, speaker="Nobody")
    assert other["accentColor"] == shared["accentColor"]
    assert other["position"] == shared["position"]


def test_a_project_declaring_no_speaker_styles_gets_one_look(tmp_path):
    """The absence of a distinction, not a default set of colours.
    Inventing per-speaker colours here would be inventing taste."""
    from library.tools.subtitle_style import (
        project_speaker_styles, resolve_subtitle_style)
    folder = _project_with(tmp_path, "pipeline:\n  brand_template: default_brand\n")
    assert project_speaker_styles(folder) is None
    a = resolve_subtitle_style(project_folder=folder, speaker="Akshita")
    b = resolve_subtitle_style(project_folder=folder, speaker="Craig")
    assert a["accentColor"] == b["accentColor"]


def test_the_engine_ships_no_per_speaker_colours():
    """AGENTS.md 10.5. The enumeration names AXES; it holds no values."""
    from library.tools import subtitle_style
    assert all(isinstance(k, str) for k in subtitle_style.SPEAKER_STYLE_KEYS)
    assert not hasattr(subtitle_style, "DEFAULT_SPEAKER_STYLES")


def test_a_misspelled_override_key_is_refused(tmp_path):
    """Silently ignoring it is a caption the editor believes shipped."""
    from library.tools.subtitle_style import resolve_subtitle_style
    folder = _project_with(tmp_path, "pipeline:\n"
                                     "  speaker_subtitle_styles:\n"
                                     "    Akshita:\n"
                                     "      accentColour: '#FFB8D4'\n")
    with pytest.raises(ValueError) as excinfo:
        resolve_subtitle_style(project_folder=folder, speaker="Akshita")
    assert "accentColour" in str(excinfo.value)


def test_a_speaker_may_not_override_the_safe_area(tmp_path):
    """captionMaxWidth and safeArea are MEASURED from the delivery frame.
    A speaker who could override them could caption outside it."""
    from library.tools.subtitle_style import SPEAKER_STYLE_KEYS
    assert "captionMaxWidth" not in SPEAKER_STYLE_KEYS
    assert "safeArea" not in SPEAKER_STYLE_KEYS


def test_a_malformed_declaration_raises(tmp_path):
    from library.tools.subtitle_style import project_speaker_styles
    folder = _project_with(tmp_path, "pipeline:\n"
                                     "  speaker_subtitle_styles: 'nope'\n")
    with pytest.raises(TypeError):
        project_speaker_styles(folder)


def test_caption_row_sits_one_pixel_above_the_safe_area():
    """The +1px design row Reel 13's exported stills prove, as a rule.

    Measured 2026-09-11: all 20 Reel 13 tight captions corrected uniformly
    from computed Tilt -850.0 to -870.0 - 20 units on the 480-floor
    canvases, exactly 10px as drawn under the measured 2x gain - with an
    exported still correlation-scanning the corrected canvases onto
    frame row 1155 and their ink onto the caption row. The design row
    was systematically high by that 10px, so the probe props carry the
    corrected lift: the safe-area profile itself (platform fact) does
    not move, the other three insets do not move, and captionMaxWidth
    still derives from the unlifted left/right.

    This supersedes the +11px Reel 09 value: that correction was read
    under the pre-#960 single-gain relation, and the row it produced
    draws 10px high on the current carrying.
    """
    from library.tools.subtitle_style import CAPTION_LIFT_PX, SUBTITLE_STYLES
    from library.tools.safe_area import resolve_safe_area
    assert CAPTION_LIFT_PX == 1
    profile = resolve_safe_area()
    props = SUBTITLE_STYLES["default_subtitles"].resolve()
    area = props["safeArea"]
    assert area["bottom"] == profile.bottom + CAPTION_LIFT_PX
    assert area["top"] == profile.top
    assert area["left"] == profile.left
    assert area["right"] == profile.right
    assert props["captionMaxWidth"] == profile.centered_usable_width


def test_forty_tilt_units_are_ten_pixels_at_the_floor():
    """The arithmetic the lift encodes, pinned where it is derived.

    `placement_for_box` inverts the one measured Resolve relation,
    shift_y = -Tilt * canvas_h / frame_h at native scale: a 480-tall
    canvas moves a QUARTER of a delivery pixel per Tilt unit, so the
    captain's 40-unit correction is 10px and a canvas centred at
    full-frame y 1395 reads Tilt -1740.0 while the +11-era design row
    (centre 1385) reads -1700.0.

    Those two numbers are what Reel 13 and Reel 09 actually store,
    read off the live timelines - which is the check that matters:
    the design rows this engine computes and the values on the
    captain's approved reels are the same numbers.
    """
    from library.tools.tight_box import placement_for_box
    assert placement_for_box(840, 480, 540.0, 1395.0, 1080, 1920) == {
        "scaling": 1, "pan": 0.0, "tilt": -1740.0}
    assert placement_for_box(840, 480, 540.0, 1385.0, 1080, 1920) == {
        "scaling": 1, "pan": 0.0, "tilt": -1700.0}

"""P3.2: the brand template must reach the caption.

Captions are on screen for most of the runtime and every project rendered
them identically - Montserrat 800 at 58px, white with a #FBF0B8 accent -
because `generate_remotion_props.py` supplied that as a default for a
`style` key step 4.01 never wrote. All four templates declared
`effect.subtitle_style`, `style.typography` and `style.color_palette`, and
none of it reached anything.

These tests hold the three ends together: the enumeration, the synthetic
project copies that name it, and the Remotion component that reads the
props. The same contract `test_transition_vocabulary` and
`test_series_look` enforce.
"""
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.subtitle_style import (
    LEGACY_ACCENT_COLOR,
    LEGACY_FONT_COLOR,
    LEGACY_FONT_SIZE,
    LEGACY_FONT_WEIGHT,
    LEGACY_OUTLINE_WIDTH,
    SUBTITLE_STYLES,
    UnknownSubtitleStyle,
    get_subtitle_style,
    project_subtitle_typography,
    resolve_subtitle_style,
)

OVERLAY_DIR = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "compositions", "SubtitleOverlay")


def _templates():
    from tests.brand_fixtures import ALL_SYNTHETIC
    for name in sorted(ALL_SYNTHETIC):
        yield name, ALL_SYNTHETIC[name]


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


# ─────────────────────────────────────────────────────────
# Unknown names raise rather than defaulting
# ─────────────────────────────────────────────────────────

def test_an_unknown_style_raises_naming_the_alternatives():
    """Falling back is how subtitle_style came to mean nothing."""
    with pytest.raises(UnknownSubtitleStyle) as exc:
        get_subtitle_style("neon_wobble")
    for available in SUBTITLE_STYLES:
        assert available in str(exc.value)
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


    def test_typography_overrides_the_style_shape(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"typography": {"font": "Helvetica", "size": 42, "weight": "bold"}})
        assert got["fontFamily"] == "Helvetica"
        assert got["fontSize"] == 42
        assert got["fontWeight"] == 700
        # Weight accepts words and numbers.
        for value, expected in [("bold", 700), ("black", 900), ("Semi-Bold", 600),
                                (500, 500), ("500", 500)]:
            got = resolve_subtitle_style(
                {"subtitle_style": "bold_large"}, {"typography": {"weight": value}})
            assert got["fontWeight"] == expected, value
        # Malformed typography falls back rather than crashing.
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"typography": {"size": "enormous", "weight": None, "font": ""}})
        assert got["fontSize"] == SUBTITLE_STYLES["bold_large"].font_size
        assert got["fontWeight"] == SUBTITLE_STYLES["bold_large"].font_weight


class TestPaletteDerivation:

    PALETTE = ["#ff0055", "#00ffcc", "#ffffff", "#000000"]

    def test_text_is_the_lightest_entry_and_outline_the_darkest(self):
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"}, {"color_palette": self.PALETTE})
        assert got["fontColor"].lower() == "#ffffff"
        assert got["outlineColor"].lower() == "#000000"
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"}, {"color_palette": ["#fff", "#000"]})
        assert got["fontColor"].lower() == "#fff"
        # A malformed palette degrades instead of failing.
        for palette in ([], ["not a colour"], ["#12"], [None, 42], ["#GGGGGG"]):
            got = resolve_subtitle_style({"subtitle_style": "bold_large"},
                                         {"color_palette": palette})
            assert got["fontColor"] == LEGACY_FONT_COLOR

    def test_the_accent_is_a_usable_entry_or_the_style_accent(self):
        """Vividness, not luminance, separates an accent from a smudge."""
        cases = [  # (style, palette, accent)
            # The vivid saturated entry, even when dark by luminance.
            ("bold_large", self.PALETTE, "#ff0055"),
            # A light muted warm tan is kept.
            ("clean_standard", ["#f5f5f5", "#333333", "#ddab7e"], "#ddab7e"),
            # A grey accent vanishes into the text.
            ("bold_large", ["#ffffff", "#888888", "#111111"],
             LEGACY_ACCENT_COLOR),
            # A placeholder palette of pure primaries is not a brand.
            ("default_subtitles", ["#ff0000", "#00ff00", "#0000ff"],
             LEGACY_ACCENT_COLOR),
        ]
        for style, palette, accent in cases:
            got = resolve_subtitle_style({"subtitle_style": style},
                                         {"color_palette": palette})
            assert got["accentColor"].lower() == accent.lower(), palette
        # A dark muted navy would sit on its own outline: rejected, while
        # the neutrals are still honoured.
        got = resolve_subtitle_style(
            {"subtitle_style": "minimal"},
            {"color_palette": ["#223344", "#aabbcc", "#111111"]})
        assert got["accentColor"] == LEGACY_ACCENT_COLOR
        assert got["fontColor"] == "#aabbcc"
        assert got["outlineColor"] == "#111111"


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

    def test_a_declared_size_outranks_style_and_template_key_by_key(
            self, tmp_path):
        """Asking for a size is not asking to give up the typeface:
        replacing the whole slot would silently take font and weight."""
        folder = _project(tmp_path, {"subtitle_typography": {"size": 85}})
        assert resolve_subtitle_style({}, {}, folder)["fontSize"] == 85
        got = resolve_subtitle_style(
            {"subtitle_style": "bold_large"},
            {"typography": {"size": 192, "font": "Montserrat"}}, folder)
        assert got["fontSize"] == 85
        got = resolve_subtitle_style(
            {}, {"typography": {"font": "Montserrat", "weight": 600}}, folder)
        assert got["fontSize"] == 85
        assert got["fontFamily"] == "Montserrat"
        assert got["fontWeight"] == 600

    def test_a_key_nothing_reads_or_a_non_mapping_is_refused(self, tmp_path):
        folder = _project(tmp_path, {"subtitle_typography": {"colour": "red"}})
        with pytest.raises(ValueError, match="colour"):
            project_subtitle_typography(folder)
        (tmp_path / "x").mkdir()
        folder = _project(tmp_path / "x", {"subtitle_typography": 85})
        with pytest.raises(TypeError):
            project_subtitle_typography(folder)


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
    # A speaker the project does not name changes nothing.
    shared = resolve_subtitle_style(project_folder=folder)
    other = resolve_subtitle_style(project_folder=folder, speaker="Nobody")
    assert other["accentColor"] == shared["accentColor"]
    assert other["position"] == shared["position"]


def test_a_project_declaring_no_speaker_styles_gets_one_look(tmp_path):
    """The absence of a distinction, not a default set of colours.
    Inventing per-speaker colours here would be inventing taste."""
    from library.tools.subtitle_style import (
        project_speaker_styles, resolve_subtitle_style)
    folder = _project_with(tmp_path, "pipeline:\n")
    assert project_speaker_styles(folder) is None
    a = resolve_subtitle_style(project_folder=folder, speaker="Akshita")
    b = resolve_subtitle_style(project_folder=folder, speaker="Craig")
    assert a["accentColor"] == b["accentColor"]


def test_a_speaker_declaration_nothing_reads_is_refused(tmp_path):
    """Silently ignoring a key is a caption the editor believes shipped;
    captionMaxWidth and safeArea are MEASURED from the delivery frame, so
    a speaker may not override them and caption outside it."""
    from library.tools.subtitle_style import (
        project_speaker_styles, resolve_subtitle_style)
    for n, key in enumerate(("accentColour", "captionMaxWidth", "safeArea")):
        folder = _project_with(tmp_path / str(n),
                               "pipeline:\n"
                               "  speaker_subtitle_styles:\n"
                               "    Akshita:\n"
                               f"      {key}: '#FFB8D4'\n")
        with pytest.raises(ValueError, match=key):
            resolve_subtitle_style(project_folder=folder, speaker="Akshita")
    folder = _project_with(tmp_path / "bad", "pipeline:\n"
                                            "  speaker_subtitle_styles: 'nope'\n")
    with pytest.raises(TypeError):
        project_speaker_styles(folder)


def test_caption_row_sits_one_pixel_above_the_safe_area():
    """The +1px design row Reel 13's exported stills prove, as a rule:
    only the bottom inset lifts; captionMaxWidth derives from the unlifted
    left/right. Measurement: docs/evidence/caption_tilt.md#the-1px-lift."""
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
    """At gain 1.0 a 480-tall canvas moves a quarter pixel per Tilt unit;
    -1740.0 and -1700.0 are what Reel 13 and Reel 09 store on the live
    timelines. History: docs/evidence/caption_tilt.md#the-1px-lift."""
    from library.tools.tight_box import placement_for_box
    assert placement_for_box(840, 480, 540.0, 1395.0, 1080, 1920,
                             draw_gain=1.0) == {
        "scaling": 1, "pan": 0.0, "tilt": -1740.0}
    assert placement_for_box(840, 480, 540.0, 1385.0, 1080, 1920,
                             draw_gain=1.0) == {
        "scaling": 1, "pan": 0.0, "tilt": -1700.0}

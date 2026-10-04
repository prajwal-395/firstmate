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
from library.steps.step_4_01_plan_subtitles.step import (
    apply_caption_case,
    UnknownCaptionCase,
    generate_subtitles,
)
from library.schemas.brand_template import EffectSlots
from library.tools.caption_reading import (
    apply_caption_reading,
    apply_caption_reading_text,
)
import json
from pathlib import Path


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
    SpeakerOne:
      accentColor: '#FFB8D4'
    SpeakerTwo:
      accentColor: '#FBF0B8'
      position: top
"""


def test_each_speaker_gets_their_declared_look(tmp_path):
    from library.tools.subtitle_style import resolve_subtitle_style
    folder = _project_with(tmp_path, _TWO_SPEAKERS)
    speakerone = resolve_subtitle_style(project_folder=folder, speaker="SpeakerOne")
    speakertwo = resolve_subtitle_style(project_folder=folder, speaker="SpeakerTwo")
    assert speakerone["accentColor"] == "#FFB8D4"
    assert speakertwo["accentColor"] == "#FBF0B8"
    assert speakertwo["position"] == "top"
    assert speakerone["position"] == "bottom", "an undeclared key is untouched"
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
    a = resolve_subtitle_style(project_folder=folder, speaker="SpeakerOne")
    b = resolve_subtitle_style(project_folder=folder, speaker="SpeakerTwo")
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
                               "    SpeakerOne:\n"
                               f"      {key}: '#FFB8D4'\n")
        with pytest.raises(ValueError, match=key):
            resolve_subtitle_style(project_folder=folder, speaker="SpeakerOne")
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


# --------------------------------------------------------------------------
# From test_caption_case.py
#
# Tests for caption case as a brand template setting.
#
# Covers:
# - Lowercase from a template that sets caption_case: lowercase
# - Source casing preserved when a template sets caption_case: as_written
# - Omitted caption_case defaults to lowercase
# - All shipped templates specify caption_case: lowercase
# - The apply_caption_case helper function

# ── Minimal spine fixture ──
# A spine with one speech block carrying mixed-case word timestamps,
# enough to exercise the caption case path without needing a full
# pipeline state.

def _make_spine(text="Hello World", words=None):
    """Build a minimal audio spine with one speech block."""
    if words is None:
        word_list = text.split()
        words = []
        t = 0.0
        for w in word_list:
            words.append({
                "word": w,
                "source_start": t,
                "source_end": t + 0.3,
            })
            t += 0.4
    src_start = words[0]["source_start"]
    src_end = words[-1]["source_end"]
    return {
        "structure": [
            {
                "block_type": "speech",
                "position": 1,
                "timeline_start": 0.0,
                "timeline_end": src_end - src_start,
                "source_start": src_start,
                "source_end": src_end,
                "clip_id": "clip_001",
                "alignment_method": "whisperx",
                "word_timestamps": words,
                "content": {"text": text},
            }
        ]
    }


# ── generate_subtitles integration tests ──

class TestCaptionCaseInGenerateSubtitles:
    def test_lowercase_from_template(self):
        """A template that sets caption_case: lowercase produces lowercased text."""
        spine = _make_spine("Hello World")
        result = generate_subtitles(spine, caption_case="lowercase")
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        for entry in entries:
            assert entry["text"] == entry["text"].lower(), (
                f"Expected lowercase but got: {entry['text']!r}"
            )
            for w in entry["words"]:
                assert w["word"] == w["word"].lower(), (
                    f"Expected lowercase word but got: {w['word']!r}"
                )

    def test_as_written_preserves_case(self):
        """A template that sets caption_case: as_written keeps source casing."""
        spine = _make_spine("Hello World")
        result = generate_subtitles(spine, caption_case="as_written")
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        # The text should preserve the original casing from word timestamps
        all_text = " ".join(e["text"] for e in entries)
        assert "Hello" in all_text, (
            f"Expected 'Hello' with original casing, got: {all_text!r}"
        )
        all_words = [w["word"] for e in entries for w in e["words"]]
        assert "Hello" in all_words, (
            f"Expected 'Hello' in words, got: {all_words}"
        )

    def test_omitted_defaults_to_lowercase(self):
        """When caption_case is not passed, default is lowercase."""
        spine = _make_spine("Hello World")
        # Call without caption_case - should default to "lowercase"
        result = generate_subtitles(spine)
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        for entry in entries:
            assert entry["text"] == entry["text"].lower(), (
                f"Default should lowercase, got: {entry['text']!r}"
            )

    def test_hook_block_respects_caption_case(self):
        """Hook blocks also honour the caption_case setting."""
        spine = {
            "structure": [
                {
                    "block_type": "hook",
                    "position": 0,
                    "timeline_start": 0.0,
                    "timeline_end": 0.7,
                    "source_start": 0.0,
                    "source_end": 0.7,
                    "clip_id": "clip_001",
                    "alignment_method": "whisperx",
                    "word_timestamps": [
                        {"word": "Check", "source_start": 0.0, "source_end": 0.3},
                        {"word": "This", "source_start": 0.3, "source_end": 0.6},
                    ],
                    "content": {"text": "Check This"},
                }
            ]
        }
        # as_written should preserve casing
        result = generate_subtitles(spine, caption_case="as_written")
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        all_text = " ".join(e["text"] for e in entries)
        assert "Check" in all_text

        # lowercase should force lowercase
        result_lower = generate_subtitles(spine, caption_case="lowercase")
        entries_lower = result_lower["subtitle_plan"]["subtitle_entries"]
        for entry in entries_lower:
            assert entry["text"] == entry["text"].lower()


# ── Recorded corrections survive into the captions they were ordered into ──
# Reel 09, 2026-09-19: a model-proposed spelling ("Google Business
# profile" reads "Google Business Profile", lc-0046) carried recorded
# case into caption words AFTER the lowercase + reading transforms, so
# the reading-idempotency gate below refused the whole reel build.
# The first fix conformed every correction to house lowercase - which
# holds the gate but disobeys the six active CAPTAIN-said spellings
# (lc-0001, lc-0084..lc-0088: write the casing "in every caption").
# So corrections run after the transforms, and the reading restores
# the same store: a card is a fixed point of the reading
# (library/tools/caption_reading.py).

class TestCorrectionsConformToCaptionContract:
    def _spine(self, words):
        timed = []
        t = 0.0
        for w in words:
            timed.append({"word": w, "source_start": t,
                          "source_end": t + 0.3})
            t += 0.4
        return {
            "structure": [
                {
                    "block_type": "speech",
                    "position": 1,
                    "timeline_start": 0.0,
                    "timeline_end": t,
                    "source_start": 0.0,
                    "source_end": t,
                    "clip_id": "clip_001",
                    "alignment_method": "whisperx",
                    "word_timestamps": timed,
                    "content": {"text": " ".join(words)},
                }
            ]
        }

    def _assert_fixed_point(self, entries, project_folder):
        from library.tools import transcript_corrections
        from library.tools.caption_reading import (
            apply_caption_reading_text,
        )
        corrections = transcript_corrections.spelling_corrections(
            project_folder)
        for entry in entries:
            assert apply_caption_reading_text(
                entry["text"],
                corrections=corrections) == entry["text"], entry["text"]

    def test_case_bearing_correction_plans_and_reads_as_recorded(
            self, tmp_path):
        """A correction emitting recorded case does not refuse the plan."""
        from library.tools import transcript_corrections
        transcript_corrections.record_spelling(
            str(tmp_path), "Google Business profile",
            "Google Business Profile",
            "the product name's casing", proposed_by="model")
        spine = self._spine(
            ["So", "Google", "Business", "profile", "obviously"])
        result = generate_subtitles(spine, caption_case="lowercase",
                                    project_folder=str(tmp_path))
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        all_text = " ".join(e["text"] for e in entries)
        assert "Google Business Profile" in all_text
        self._assert_fixed_point(entries, str(tmp_path))


# ── A project's own copy declares its own casing ──
# Q5, decided 2026-08-16: caption case is a per-template setting rather
# than a hidden global, and every project copy declares its CURRENT
# behaviour so nothing about any existing video changed. The mechanism
# landed earlier (#102); this pins the declarations so the decision
# is durable rather than incidental.

# Lowercase everywhere except the client copy: lowercase captions are
# the channel's voice, and a client's brand is not the channel's.
EXPECTED_CAPTION_CASE = {
    "synthetic_cinematic": "lowercase",
    "synthetic_default": "lowercase",
    "synthetic_interview": "lowercase",
    "synthetic_client": "as_written",
    "synthetic_shortform": "lowercase",
}


def test_every_copy_declares_its_caption_case_explicitly():
    """A hidden global became a declared choice; keep it declared.

    Relying on the default would work, and would put the decision back
    where it was - implicit.
    """
    missing = [n for n, t in _templates()
               if "caption_case" not in (t.get("effect") or {})]
    assert not missing, (
        f"{missing} do not declare effect.caption_case. Every project "
        f"copy states its own casing rather than inheriting it.")


def test_no_template_declares_an_unsupported_case():
    valid = {"lowercase", "as_written"}
    for name, tmpl in _templates():
        value = (tmpl.get("effect") or {}).get("caption_case")
        assert value in valid, f"{name}: {value!r} is not one of {valid}"


# --------------------------------------------------------------------------
# From test_caption_reading.py
#
# The caption reading: acronyms and numerals as declared data.
#
# `library/tools/caption_reading.py` is the one named place the captain's
# 2026-09-18 ruling lives - SEO, GEO and AI read uppercase, spelled-out
# numbers read as digits - and these tests pin that a future scope answer
# is a declaration edit: the acronyms convert because they are members of
# `CAPTION_ACRONYMS`, and every numeral shape converts through the one
# `parse_number` interpreter over the one word tables.

def _words(*tokens):
    """Word entries with sequential fake timings."""
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.4}
        for i, token in enumerate(tokens)
    ]


def test_acronyms_restore_case_insensitively_and_never_inside_a_word():
    for surface, read in [("seo", "SEO"), ("ai", "AI"), ("ceo", "CEO"),
                          ("cmos", "CMOs"),
                          ("said aim chair again", "said aim chair again")]:
        assert apply_caption_reading_text(surface) == read, surface


def test_quantities_ratings_versions_measures_read_as_digits():
    for surface, read in [
        ("seo two point oh", "SEO 2.0"),
        ("like three point five stars and", "like 3.5 stars and"),
        ("and that's only twenty percent.", "and that's only 20 percent."),
        ("five-star reviews, ai does see that.",
         "5-star reviews, AI does see that."),
        ("their press release from twenty twenty",
         "their press release from 2020"),
        ("their press release from twenty twenty-one",
         "their press release from 2021"),
        ("their press release from twenty twenty one",
         "their press release from 2021"),
        ("maybe you're a hundred person shop,",
         "maybe you're a 100 person shop,"),
    ]:
        assert apply_caption_reading_text(surface) == read, surface


def test_reading_is_idempotent():
    for surface in [
        "seo two point oh",
        "five-star reviews, ai does see that.",
        "their press release from twenty twenty",
        "their press release from twenty twenty-one",
        "their press release from twenty twenty one",
        "it really likes, especially youtube.",
    ]:
        once = apply_caption_reading_text(surface)
        assert apply_caption_reading_text(once) == once


def test_every_emitted_word_carries_a_span_and_output_never_grows():
    out = apply_caption_reading(_words("the", "link's", "in", "our", "bio."))
    assert len(out) == 5
    for entry in out:
        assert entry["start"] is not None
        assert entry["end"] is not None
    entries = _words("seo", "two", "point", "oh", "and", "geo")
    assert len(apply_caption_reading(entries)) <= len(entries)


def test_year_formatting_merges_hyphenated_components_with_their_span():
    out = apply_caption_reading(_words("their", "press", "release", "from",
                                       "twenty", "twenty-one"))
    year = out[-1]
    assert year == {"word": "2021", "start": 4.0, "end": 5.4}


def _corrections(*pairs):
    """Recorded spellings in the store's shape: heard -> correct."""
    return [
        {"id": f"lc-test-{i:02d}", "heard": heard, "correct": correct}
        for i, (heard, correct) in enumerate(pairs)
    ]


def test_recorded_spellings_are_read_as_data():
    """2026-09-19: the planner enforced the correction store after the
    reading ran, so every reel carrying "Google" refused its build on
    correct output. The reading takes the same store as data - never a
    second list. Longest phrase wins; a correction outranks the acronym
    rule ("aics" would read "AICs"); a mixed-case brand the captain did
    not enumerate (Reel 16) restores and composes with the numeral rule.
    """
    cases = [
        (_corrections(("google", "Google")),
         "we ran it on google search", "we ran it on Google search"),
        (_corrections(("atlanta", "Atlanta"),
                      ("atlanta journal constitution",
                       "Atlanta Journal Constitution")),
         "we're in atlanta and the atlanta journal constitution",
         "we're in Atlanta and the Atlanta Journal Constitution"),
        (_corrections(("AICs", "AI sees")),
         "the aics saw it", "the AI sees saw it"),
        (_corrections(("chatgpt", "ChatGPT"), ("chat gpt", "ChatGPT")),
         "chatgpt, it gave three", "ChatGPT, it gave 3"),
        (_corrections(("chatgpt", "ChatGPT"), ("chat gpt", "ChatGPT")),
         "they get on chatgpt", "they get on ChatGPT"),
    ]
    for corrections, surface, read in cases:
        assert apply_caption_reading_text(
            surface, corrections=corrections) == read, surface


def test_multiword_correct_merges_with_span():
    corrections = _corrections(("AICs", "AI sees"))
    out = apply_caption_reading(
        _words("the", "aics", "saw", "it"), corrections=corrections)
    assert [(entry["word"], entry["start"], entry["end"])
            for entry in out] == [
        ("the", 0.0, 0.4),
        ("AI sees", 1.0, 1.4),
        ("saw", 2.0, 2.4),
        ("it", 3.0, 3.4),
    ]


# --------------------------------------------------------------------------
# From test_word_emphasis.py
#
# Per-word emphasis: measured from pitch, loudness and duration - never guessed.
#
# Fidelity rung 5b. Step 1.05 measured clip-level pitch stats but no
# per-word emphasis, so a plan asking "punch 15% on 'quit', whoosh
# exactly on it" had no measurement to address - and rung 2's anchors
# had no emphasis table to read. `measure_word_prosody` scores every
# timed word from the Voz+MFA stamps against the speaker's own
# baselines, and `view:emphasis` carries three scored words per spine
# block to the steps that plan punches, sounds and cuts (4.02, 4.03,
# 4.04). The model still decides; the measurement is context.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.analysis.speech_advanced_pipeline import (
    WORD_EMPHASIS_FORMULA,
    measure_word_prosody,
)
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon

STEPS = REPO / "library" / "steps"

ANCHOR_STEPS = {
    "step_4_02_plan_transitions",
    "step_4_03_plan_vfx",
    "step_4_04_plan_sfx",
}


def manifest(step_dir: str) -> dict:
    return json.loads((STEPS / step_dir / "manifest.json").read_text(
        encoding="utf-8"))


# ── The formula on synthetic contours ───────────────────────────────

def test_an_emphasized_word_outscores_its_neighbours():
    """Higher, louder, longer wins - the three terms all fire."""
    regions = [{"start": 0.0, "end": 2.0, "words": [
        {"word": "i", "start": 0.1, "end": 0.2},
        {"word": "quit", "start": 1.0, "end": 1.45},
        {"word": "now", "start": 1.6, "end": 1.8},
    ]}]
    pitch = [(round(t * 0.01, 3), 100.0) for t in range(200)
             if not 100 <= t < 145]
    pitch += [(round(t * 0.01, 3), 130.0) for t in range(100, 145)]
    inten = [(t / 20.0, 58.0) for t in range(40)]
    inten += [(1.0 + i * 0.05, 66.0) for i in range(9)]

    out = measure_word_prosody(regions, pitch, inten)
    scores = {w["word"]: w["emphasis"] for w in out["words"]}
    assert scores["quit"] > scores["i"]
    assert scores["quit"] > scores["now"]
    quit = next(w for w in out["words"] if w["word"] == "quit")
    assert quit["terms"] == 3
    # The three terms, stated: 130 vs 100 Hz is +4.5 st (/3 = 1.5).
    assert quit["f0_rel_semitones"] == pytest.approx(4.5, abs=0.05)


def test_one_octave_error_frame_does_not_decide_a_word():
    """Median inside the span: a single Praat spike is outvoted."""
    regions = [{"start": 0.0, "end": 1.0, "words": [
        {"word": "flat", "start": 0.1, "end": 0.5},
    ]}]
    pitch = [(round(0.1 + i * 0.01, 3), 110.0) for i in range(40)]
    pitch[20] = (0.3, 580.0)  # the spike this footage produces
    out = measure_word_prosody(regions, pitch, [])
    flat = out["words"][0]
    assert flat["f0_rel_semitones"] == pytest.approx(0.0, abs=0.1)


def test_an_unvoiced_word_still_scores_from_what_answered():
    """No voiced frames: no f0 term, and the score says so."""
    regions = [{"start": 0.0, "end": 1.0, "words": [
        {"word": "shh", "start": 0.1, "end": 0.4},
    ]}]
    inten = [(round(0.1 + i * 0.05, 3), 62.0) for i in range(6)]
    out = measure_word_prosody(regions, pitch_samples=[],
                                intensity_samples=inten)
    shh = out["words"][0]
    assert shh["f0_rel_semitones"] is None
    assert shh["loud_rel_db"] is not None
    assert shh["terms"] == 2
    assert shh["emphasis"] is not None


def test_no_speech_regions_is_an_empty_table_not_a_missing_one():
    out = measure_word_prosody(None, [(0.0, 110.0)], [(0.0, 60.0)])
    assert out["words"] == []
    assert out["baseline_f0_median_hz"] == pytest.approx(110.0)
    assert out["formula"] == WORD_EMPHASIS_FORMULA


def test_duration_is_expected_from_the_speakers_own_pace():
    """Twice as slow as the speaker's own letters-per-second: +1 term."""
    regions = [{"start": 0.0, "end": 2.0, "words": [
        {"word": "aa", "start": 0.0, "end": 0.2},
        {"word": "bb", "start": 0.5, "end": 0.9},
    ]}]
    out = measure_word_prosody(regions, [], [])
    by_word = {w["word"]: w for w in out["words"]}
    # 0.6 s over 4 letters: 0.15 s/letter; "bb" is 0.4/0.3 = 1.33x.
    assert by_word["bb"]["dur_ratio"] == pytest.approx(1.333, abs=0.01)
    assert by_word["aa"]["dur_ratio"] == pytest.approx(0.667, abs=0.01)


# ── The view: three scored words per block ───────────────────────────

def _profiles():
    return {"clip_017": {
        "clip_id": "clip_017",
        "prosody": {
            "method": "parselmouth-praat",
            "pitch_stats": {"mean_f0_hz": 121.7},
            "word_prosody": [
                {"word": "i", "start": 30.0, "end": 30.1,
                 "f0_rel_semitones": 0.4, "loud_rel_db": 1.0,
                 "dur_ratio": 0.9, "emphasis": 0.1, "terms": 3},
                {"word": "quit", "start": 33.135, "end": 33.295,
                 "f0_rel_semitones": 1.24, "loud_rel_db": -1.2,
                 "dur_ratio": 0.772, "emphasis": -0.087, "terms": 3},
                {"word": "only", "start": 34.853, "end": 35.0,
                 "f0_rel_semitones": 6.27, "loud_rel_db": 9.15,
                 "dur_ratio": 1.076, "emphasis": 1.494, "terms": 3},
                {"word": "quit", "start": 36.0, "end": 36.2,
                 "f0_rel_semitones": 2.0, "loud_rel_db": 2.0,
                 "dur_ratio": 1.0, "emphasis": 0.389, "terms": 3},
            ],
            "word_prosody_formula": WORD_EMPHASIS_FORMULA,
        },
    }}


def _spine():
    return {"structure": [
        {"position": 3, "block_type": "speech", "clip_id": "clip_017",
         "source_start": 30.0, "source_end": 40.0,
         "timeline_start": 8.38, "timeline_end": 18.38,
         "word_timestamps": [
             {"word": "i", "source_start": 30.0, "source_end": 30.1},
             {"word": "quit", "source_start": 33.135,
              "source_end": 33.295},
             {"word": "only", "source_start": 34.853,
              "source_end": 35.0},
             {"word": "quit", "source_start": 36.0, "source_end": 36.2},
         ],
         "alignment_method": "mfa"},
        {"position": 4, "block_type": "speech", "clip_id": "clip_099",
         "source_start": 0.0, "source_end": 5.0,
         "timeline_start": 18.38, "timeline_end": 23.38,
         "word_timestamps": [
             {"word": "hello", "source_start": 0.0, "source_end": 0.4},
         ],
         "alignment_method": "mfa"},
    ]}


def test_three_scored_words_reach_the_block_and_gaps_are_named():
    view = build_view("emphasis", {
        "prosody_analysis": {"profiles": _profiles()},
        "timed_spine": _spine(),
    })
    blocks = view["emphasis"]["blocks"]
    assert len(blocks) == 1
    row = blocks[0]
    assert row["block_position"] == 3
    assert row["most_emphasized_word"] == "only"
    assert row["most_emphasized_occurrence"] == 1
    # The second saying of "quit" pairs with the second profile row -
    # occurrence counts the spelling, in spoken order.
    quits = [w for w in row["top_words"] if w["word"] == "quit"]
    assert {q["occurrence"] for q in quits} <= {1, 2}
    assert "1.494" in json_to_toon(view)
    # A block without measurement is named, not absent.
    assert "4" in view["emphasis"]["not_measured"]
    # No prosody routed is no view, not an error.
    assert build_view("emphasis", {"timed_spine": _spine()}) == {}
    assert build_view("emphasis", {"prosody_analysis": {}}) == {}


def test_the_per_word_table_does_not_reach_the_prompt():
    """AGENTS.md 10.1: No raw value list reaches a prompt."""
    view = build_view("prosody",
                      {"prosody_analysis": {"profiles": _profiles()}})
    serialised = json.dumps(view)
    assert "word_prosody" not in serialised.replace(
        "word_prosody_formula", "")
    assert "121.7" in serialised  # ...while the summary still does.


# ── The wiring: every anchor consumer declares it ────────────────────

def test_anchor_consumers_declare_the_view_and_its_input():
    """A view is not routing: the step still declares prosody_analysis."""
    for step_dir in sorted(ANCHOR_STEPS):
        m = manifest(step_dir)
        assert "view:emphasis" in m["context_fields"], (
            f"{step_dir} plans anchored placements but never sees emphasis")
        names = {i["name"]: i.get("required", True)
                 for i in m["interface"]["inputs"]}
        assert names.get("prosody_analysis") is False, (
            f"{step_dir} reads view:emphasis without declaring the "
            f"optional prosody_analysis input it is built from")

"""The safe area, and the caption fitter that had never run.

Two halves of one defect, and they are tested together because they are
one number seen from two sides.

`plan_subtitles.text_fits_on_screen` measured real glyph widths with PIL
and was switched on by `audio_spine["subtitle_style"]["font_path"]`. No
producer anywhere wrote `subtitle_style` into the spine, so `fits_fn` was
None on every run and grouping fell back to a literal `max_chars = 18`.
That literal was written for a 58px caption; at the 160px style the same
step resolves from the brand template it is not close - measured on the
shipped export, caption ink spanned **columns 0-1079 of a 1080px frame**,
with the single word "announcement" clipped at both edges.

The width it should have been fitting inside did not exist either:
`grep -rni "safe.area"` over `library/`, `remotion-subtitles/src/` and
`tests/` returned one comment and no code, and caption placement was the
literal `bottom: 200px` - 10.4% of a 1920-row frame, inside the band the
platform paints its own caption and audio bar over.

So: one enumeration (`library/tools/safe_area.py`), four consumers, and a
fitter that actually runs.
"""
import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_01_plan_subtitles.step import (  # noqa: E402
    MAX_CAPTION_LINES,
    build_caption_fitter,
    generate_subtitles,
    split_into_groups,
)
from library.tools.safe_area import (  # noqa: E402
    SAFE_AREAS,
    UnknownSafeArea,
    resolve_safe_area,
    safe_area_for_format,
    safe_area_profile,
)
from library.tools.subtitle_style import (  # noqa: E402
    CAPTION_LIFT_PX,
    resolve_subtitle_style,
)

FRAME_W = 1080
FRAME_H = 1920

# The line the audit measured the clipping on: `sub_block_5.mov` of the
# shipped export carried "announcement" as a single card, and that word
# ran off both edges of the frame.
CLIPPING_LINE = (
    "today I have a big announcement to make about the brand template "
    "and it will change everything"
)


def _spine(text):
    """A minimal spine carrying one speech block of `text`."""
    words, t = [], 0.0
    for word in text.split():
        words.append({"word": word, "source_start": round(t, 3),
                      "source_end": round(t + 0.30, 3)})
        t += 0.42
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 0.0,
        "timeline_end": words[-1]["source_end"],
        "source_start": 0.0,
        "source_end": words[-1]["source_end"],
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": text},
    }]}


# ── The enumeration ──

def test_vertical_profile_is_the_published_shortform_map():
    """The numbers the captain's ruling of 2026-08-25 resolves to."""
    insets = safe_area_for_format("vertical_1080x1920")
    assert (insets.top, insets.right, insets.bottom, insets.left) == (
        120, 120, 320, 90)
    assert insets.usable_width == 870
    # A caption is CENTRED, so it reaches the nearer edge first.
    assert insets.centered_usable_width == 840


def test_bottom_inset_clears_the_platform_ui_band():
    """320px, not the 200px `SubtitleOverlay` used to hardcode.

    200/1920 is 10.4% of the frame; the published map keeps ~16.7% clear
    for the platform's caption, CTA, hashtags and audio bar.
    """
    insets = safe_area_for_format("vertical_1080x1920")
    assert insets.bottom == 320
    assert insets.bottom / FRAME_H == pytest.approx(0.1667, abs=0.001)
    assert insets.bottom > 200


def test_insets_are_fractions_so_4k_vertical_needs_no_second_row():
    """The same product in more pixels gets the same profile, scaled."""
    hd = safe_area_for_format("vertical_1080x1920")
    uhd = safe_area_for_format("vertical_2160x3840")
    assert uhd.profile == hd.profile
    assert (uhd.top, uhd.right, uhd.bottom, uhd.left) == (240, 240, 640, 180)


def test_a_fixture_sized_frame_gets_a_proportionate_answer():
    """Not every render is at the format's own size.

    `tests/test_timed_text_delivery.py` renders 320x568. A raise there
    would be a safe area that only works at one resolution.
    """
    insets = resolve_safe_area(width=320, height=568)
    assert insets.bottom == round(320 / 1920 * 568)
    assert insets.left == round(90 / 1080 * 320)


def test_every_delivery_format_has_a_profile():
    """A format with no safe area is a format whose captions are unplaced."""
    from library.tools.delivery_format import DELIVERY_FORMATS
    assert set(DELIVERY_FORMATS) == set(SAFE_AREAS)


def test_unknown_format_raises_rather_than_defaulting():
    with pytest.raises(UnknownSafeArea):
        safe_area_profile("vertical_9000x16000")


def test_every_profile_records_where_its_numbers_came_from():
    for name, profile in SAFE_AREAS.items():
        assert profile.derived_from.strip(), (
            f"{name} declares insets with no source. An invented inset is "
            f"a caption under the platform's own UI with nothing to notice.")


# ── The fitter actually runs ──

def test_fitter_is_built_from_the_resolved_style_and_measures_a_real_font():
    """`fits_fn` used to be None on every run. Prove it is not."""
    style = resolve_subtitle_style({}, {})
    fitter = build_caption_fitter(style, resolve_safe_area())
    assert fitter.measured, (
        "the caption fitter fell back to an estimate; it should have "
        f"opened the bundled font for {style['fontFamily']!r}")
    assert fitter.font_path.endswith("Montserrat-Variable.ttf")
    assert fitter.font_size == style["fontSize"]


def test_fitter_sets_the_variable_weight_axis():
    """Montserrat-Variable defaults to Thin (100). The render draws 800.

    Measuring without setting the axis reports the width of a face
    nobody renders, which is the same class of silent substitution
    `library/tools/render_fonts.py` exists to refuse.
    """
    sa = resolve_safe_area()
    thin = build_caption_fitter(
        {"fontFamily": "Montserrat", "fontSize": 160, "fontWeight": 100}, sa)
    bold = build_caption_fitter(
        {"fontFamily": "Montserrat", "fontSize": 160, "fontWeight": 800}, sa)
    assert bold.text_width("announcement") > thin.text_width("announcement")


def test_grouping_blind_is_no_longer_possible():
    """There is no `max_chars` fallback to silently land on any more."""
    with pytest.raises(ValueError, match="fits_fn"):
        split_into_groups([{"word": "hi", "start": 0.0, "end": 0.2}])


def test_the_step_runs_the_fitter_on_a_real_invocation():
    """End to end through `main()`, the way the runner calls it."""
    payload = {"audio_spine": _spine(CLIPPING_LINE), "brand_effect": {},
               "brand_style": {}}
    proc = subprocess.run(
        [sys.executable,
         os.path.join(PROJECT_ROOT, "library", "steps",
                      "step_4_01_plan_subtitles", "step.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=PROJECT_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = json.loads(proc.stdout)["subtitle_plan"]
    # The platform's own bottom inset is 320 (`safe_area.py`); the
    # caption row sits CAPTION_LIFT_PX above it and these are the
    # CAPTION props, so the lift is the number that reaches the
    # renderer. Written against the constant rather than today's value:
    # it was 11 before #979 corrected it to 1, and a test spelling the
    # number fails on the next correction instead of grading it.
    assert plan["style"]["safeArea"]["bottom"] == 320 + CAPTION_LIFT_PX
    assert plan["style"]["captionMaxWidth"] == 840
    # The fitter's own report. It only ever prints when it measured a
    # card too wide for the frame, which is what the grouper could not
    # see before.
    assert "usable width" in proc.stderr, proc.stderr


# ── The regression: no card is emitted wider than the caption box ──

def _wrapped_lines(fitter, text):
    """The lines the caption box wraps `text` onto, greedily."""
    lines, current = [], []
    for word in text.split():
        if current and fitter.text_width(
                " ".join(current + [word])) > fitter.usable_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def test_no_caption_line_is_wider_than_the_usable_width():
    """The defect, pinned.

    Before the fix this line grouped by `max_chars = 18` into cards like
    'brand template and' - 1707px of ink in a 1080px frame - and left
    'announcement' (1303px) as an unbreakable single word clipped at both
    edges. Measured ink span across the cards was columns 0-1079.

    The bound is per LINE, because the overlay's box wraps (`flexWrap`),
    so a card wider than one line becomes two and is drawn in full. What
    must never happen is a LINE running off the frame.
    """
    style = resolve_subtitle_style({}, {})
    safe_area = resolve_safe_area()
    fitter = build_caption_fitter(style, safe_area)

    entries = generate_subtitles(
        _spine(CLIPPING_LINE), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]
    assert entries

    outline = style["outlineWidth"]
    left_edge, right_edge = FRAME_W, 0
    for entry in entries:
        scale = entry["fit_scale"]
        assert 0 < scale <= 1.0
        for line in _wrapped_lines(fitter, entry["text"]):
            ink = fitter.text_width(line) * scale + 2 * outline
            assert ink <= safe_area.centered_usable_width, (
                f"card {entry['id']} line {line!r} draws {ink:.0f}px of ink "
                f"in a {safe_area.centered_usable_width}px usable width")
            left_edge = min(left_edge, (FRAME_W - ink) / 2.0)
            right_edge = max(right_edge, (FRAME_W + ink) / 2.0)

    assert left_edge >= safe_area.left, (
        f"caption ink starts at column {left_edge:.0f}, inside the "
        f"{safe_area.left}px left safe margin")
    assert right_edge <= FRAME_W - safe_area.right, (
        f"caption ink ends at column {right_edge:.0f}, inside the "
        f"{safe_area.right}px right safe margin")


def test_no_card_needs_more_lines_than_the_box_allows():
    """A card is a phrase, not a paragraph.

    The grouper fits the BOX rather than one line, which is what restored
    the card length the pre-measurement `max_chars = 18` grouping had.
    `MAX_CAPTION_LINES` is the bound on how far that goes.
    """
    fitter = build_caption_fitter(
        resolve_subtitle_style({}, {}), resolve_safe_area())
    entries = generate_subtitles(
        _spine(CLIPPING_LINE), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]
    for entry in entries:
        assert fitter.line_count(entry["text"]) <= MAX_CAPTION_LINES, (
            f"card {entry['id']} {entry['text']!r} wraps onto "
            f"{fitter.line_count(entry['text'])} lines")


def test_an_unbreakable_word_is_shrunk_rather_than_clipped():
    """A group split cannot fix one word. The card scales; the style does not.

    What size captions should be is an open captain decision. This asserts
    only that whatever size is chosen ends up inside the frame.
    """
    style = resolve_subtitle_style({}, {})
    fitter = build_caption_fitter(style, resolve_safe_area())
    assert fitter.word_width("announcement") > fitter.usable_width, (
        "the fixture word no longer overflows; pick one that does")

    entries = generate_subtitles(
        _spine(CLIPPING_LINE), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]
    card = next(e for e in entries if "announcement" in e["text"])
    assert card["fit_scale"] < 1.0
    assert (fitter.word_width("announcement") * card["fit_scale"]
            <= fitter.usable_width)
    # The style is untouched - only this card is drawn smaller.
    assert style["fontSize"] == resolve_subtitle_style({}, {})["fontSize"]


# ── All four consumers read the one enumeration ──

def test_consumer_subtitle_style_resolves_the_inset():
    props = resolve_subtitle_style({}, {})
    platform = safe_area_for_format("vertical_1080x1920").as_props()
    # Every inset is the platform's, EXCEPT the bottom: the caption row
    # sits CAPTION_LIFT_PX above it, and that lift is the whole reason
    # these props are not the platform's own
    # (`library/tools/subtitle_style.py`).
    assert props["safeArea"] == {**platform,
                                 "bottom": platform["bottom"]
                                 + CAPTION_LIFT_PX}
    # `captionMaxWidth` derives from the UNLIFTED left/right insets.
    assert props["captionMaxWidth"] == 840


def test_consumer_motion_props_carries_the_inset():
    from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (  # noqa: E402
        generate_motion_props,
    )

    spine = {"structure": [{
        "block_type": "speech", "position": 1,
        "timeline_start": 0.0, "timeline_end": 4.0,
    }]}
    # The layer is planned now (AGENTS.md 10.2), so the inset travels on
    # a segment resolved from a plan rather than on a per-block props
    # dict. What is asserted is unchanged: the insets reach the props
    # the renderer is handed, from library/tools/safe_area.py.
    segments, resolved = generate_motion_props(
        [{"element": "title_lockup", "start_seconds": 0.5,
          "duration_seconds": 2.0, "anchor": "top_left",
          "copy": {"display": "A NAME"}, "color": "#F5F5F0"}],
        spine, width=1080, height=1920)
    assert segments, resolved.basis_record()
    assert segments[0]["props"]["safeArea"] == {
        "top": 120, "right": 120, "bottom": 320, "left": 90}


def test_consumer_timed_text_carries_the_inset_and_refuses_the_ui_band():
    from library.tools.timed_text_overlay import (
        TimedTextDeclarationError, plan_timed_text_segments)

    def declaration(y):
        return {"timed_text_overlay": {
            "font_family": "Montserrat",
            "moments": [{"text": "Night 1", "color": "#D4A34A",
                          "font_size": 72, "font_weight": 400,
                          "text_shadow": "none",
                          "start_frame": 0, "duration_frames": 60,
                          "x": 0.5, "y": y,
                          "fade_in_frames": 9, "fade_out_frames": 12}]}}

    ok = plan_timed_text_segments(declaration(0.545),
                                   width=1080, height=1920)
    assert ok[0]["props"]["safeArea"]["bottom"] == 320

    # 0.95 of 1920 is row 1824 - under the platform's audio bar.
    with pytest.raises(TimedTextDeclarationError, match="safe area"):
        plan_timed_text_segments(declaration(0.95),
                                 width=1080, height=1920)


def test_consumer_grouper_reads_the_same_width():
    """The fourth consumer, and the one that makes this a single task.

    If the safe area lived only on the render side, captions would be
    lifted clear of the platform UI and still be clipped left and right.
    """
    safe_area = resolve_safe_area()
    style = resolve_subtitle_style({}, {})
    fitter = build_caption_fitter(style, safe_area)
    assert fitter.usable_width == (
        safe_area.centered_usable_width - 2 * style["outlineWidth"])


# ── The literals are gone ──

def _read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _code_only(source: str) -> str:
    """The source with comments stripped.

    The comments deliberately NAME the retired literals, so a grep for
    them has to look at code or it can never pass.
    """
    import re
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    source = re.sub(r"^\s*#.*$", "", source, flags=re.M)
    return source


def test_the_studio_defaults_are_generated_from_the_enumeration():
    """The Remotion studio has no pipeline behind it, and still no literal.

    `remotion-subtitles/src/safeArea.generated.ts` is a projection of this
    enumeration, written by scripts/generate_safe_area_defaults.py. If it
    drifts, the studio previews a frame the render does not produce.
    """
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
    from generate_safe_area_defaults import OUTPUT, render

    with open(OUTPUT, encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == render(), (
        "remotion-subtitles/src/safeArea.generated.ts is stale. Regenerate "
        "it: python3 scripts/generate_safe_area_defaults.py")


def test_the_retired_literals_do_not_come_back():
    """`bottom: 200`, `bottom: 60` and `max_chars = 18`, by name.

    Each was a margin invented at its own call site, which is exactly
    what the captain's ruling of 2026-08-25 forbade more of.
    """
    subtitle_tsx = _code_only(_read(
        "remotion-subtitles", "src", "compositions",
        "SubtitleOverlay", "index.tsx"))
    assert "200px" not in subtitle_tsx
    assert 'maxWidth: "90%"' not in subtitle_tsx
    assert "safeArea" in subtitle_tsx

    motion_tsx = _code_only(_read(
        "remotion-subtitles", "src", "compositions",
        "MotionGraphics", "index.tsx"))
    assert "bottom: 60," not in motion_tsx
    assert "top: 60," not in motion_tsx
    assert "safeArea" in motion_tsx

    # `max_chars` survives only in prose explaining why it is gone, so
    # this half reads the parse tree rather than the text.
    import ast
    step_src = _read("library", "steps", "step_4_01_plan_subtitles", "step.py")
    tree = ast.parse(step_src)
    names = {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "max_chars" not in names, (
        "max_chars is back. Caption grouping is measured in pixels now; a "
        "character count has no relation to how wide a caption draws.")
    assert "safe_area" in step_src


# ── The grouper does not leave runts ──
#
# The balanced split. A greedy fill packs each card to the width limit and
# leaves the remainder as the next card, and a card is on screen only
# until the NEXT card's first word - so the remainder flashes. On project
# 001 that produced 76 cards under half a second out of 96. Nothing
# downstream can repair it: `enforce_min_duration` extends a card only up
# to its neighbour's start, and a greedy card's neighbour starts
# immediately.

# The real opening line of project 001, at its real delivery speed.
# Greedy grouping leaves "me." alone for 0.24s - 7 frames at 30fps.
FAST_LINE = "i can feel the silent judgment of the people behind me."


def _fast_spine(text, per_word=0.24, gap=0.0):
    """A speech block delivered fast enough to make runt cards."""
    words, t = [], 0.0
    for word in text.split():
        words.append({"word": word, "source_start": round(t, 3),
                      "source_end": round(t + per_word, 3)})
        t += per_word + gap
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 0.0,
        "timeline_end": words[-1]["source_end"],
        "source_start": 0.0,
        "source_end": words[-1]["source_end"],
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": text},
    }]}


def _durations(entries):
    return [e["timeline_end"] - e["timeline_start"] for e in entries]


def test_the_split_is_balanced_not_greedy():
    """No card flashes where a different split of the same words would not.

    The words all fit the box in more than one way; the grouper has to
    pick the partition that keeps every card on screen, not the one that
    fills each card first.
    """
    entries = generate_subtitles(
        _fast_spine(FAST_LINE), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]
    assert entries
    flashing = [(e["text"], d) for e, d in zip(entries, _durations(entries))
                if d < 0.5]
    assert not flashing, f"cards under 0.5s: {flashing}"


def test_a_greedy_split_of_the_same_words_would_have_flashed():
    """The guard can fire: greedy on this fixture really does leave runts.

    A gate that cannot fail reads as coverage, so this asserts the fixture
    is one the old behaviour got wrong.
    """
    fitter = build_caption_fitter(
        resolve_subtitle_style({}, {}), resolve_safe_area())
    spine = _fast_spine(FAST_LINE)
    words = [{"word": w["word"], "start": w["source_start"],
              "end": w["source_end"]}
             for w in spine["structure"][0]["word_timestamps"]]
    block_end = spine["structure"][0]["timeline_end"]

    greedy, current = [], []
    for word in words:
        trial = current + [word]
        if current and not fitter.fits_in_box(
                " ".join(w["word"] for w in trial)):
            greedy.append(current)
            current = [word]
        else:
            current = trial
    if current:
        greedy.append(current)

    starts = [card[0]["start"] for card in greedy] + [block_end]
    greedy_durations = [b - a for a, b in zip(starts, starts[1:])]
    assert any(d < 0.5 for d in greedy_durations), (
        "the fixture no longer distinguishes greedy from balanced; pick "
        f"one that does (greedy durations {greedy_durations})")


def test_the_last_card_of_a_block_is_measured_to_the_block_end():
    """A card's time on screen ends where its block does, not at its own
    last word - and the split has to know that or it optimises a number
    nobody renders."""
    fitter = build_caption_fitter(
        resolve_subtitle_style({}, {}), resolve_safe_area())
    words = [{"word": "one", "start": 0.0, "end": 0.3},
             {"word": "two.", "start": 0.35, "end": 0.6}]
    tight = split_into_groups(words, fits_fn=fitter.fits_in_box,
                              display_until=0.6)
    roomy = split_into_groups(words, fits_fn=fitter.fits_in_box,
                              display_until=4.0)
    # With four seconds of block left, splitting them is free; with none,
    # it is not.  Whatever it chooses, it must not invent time.
    assert tight and roomy
    assert all(g["end"] <= 0.6 for g in tight)


def test_a_single_word_is_always_a_legal_card():
    """The base case that guarantees a partition exists.

    One word wider than the box cannot be wrapped away - `fit_scale`
    draws that card smaller - so the split must never be unable to place
    it.
    """
    fitter = build_caption_fitter(
        resolve_subtitle_style({}, {}), resolve_safe_area())
    assert fitter.word_width("announcement") > fitter.usable_width, (
        "the fixture word no longer overflows; pick one that does")
    groups = split_into_groups(
        [{"word": "announcement", "start": 0.0, "end": 0.8}],
        fits_fn=fitter.fits_in_box, display_until=0.8)
    assert [g["text"] for g in groups] == ["announcement"]

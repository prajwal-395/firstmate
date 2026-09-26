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

def test_vertical_profile_keeps_captions_off_every_apps_ui():
    """The captain, 2026-09-25: the published map (top 120, bottom 320,
    right 120) put the longest caption lines under the apps' action
    rails. The profile is DERIVED from the measured zones, so a centred
    caption as wide as it allows, on the row it hangs from, touches no
    app element on any modelled phone."""
    from library.tools.safe_zone_policy import resolve_layout

    insets = safe_area_for_format("vertical_1080x1920")
    layout = resolve_layout()
    width = insets.centered_usable_width
    bottom = FRAME_H - insets.bottom
    caption = ((1080 - width) // 2, bottom - 240,
               (1080 + width) // 2, bottom)
    assert layout.intrusions(caption) == []
    # ...and it is no narrower than it has to be: one pixel wider on
    # each side meets a rail.
    wider = (caption[0] - 1, caption[1], caption[2] + 1, caption[3])
    assert layout.intrusions(wider)


def test_insets_are_fractions_so_4k_vertical_needs_no_second_row():
    """The same product in more pixels gets the same profile, scaled."""
    hd = safe_area_for_format("vertical_1080x1920")
    uhd = safe_area_for_format("vertical_2160x3840")
    assert uhd.profile == hd.profile
    for edge in ("top", "right", "bottom", "left"):
        assert abs(getattr(uhd, edge) - 2 * getattr(hd, edge)) <= 1




def test_every_delivery_format_has_a_profile():
    """A format with no safe area is a format whose captions are unplaced."""
    from library.tools.delivery_format import DELIVERY_FORMATS
    assert set(DELIVERY_FORMATS) == set(SAFE_AREAS)


def test_unknown_format_raises_rather_than_defaulting():
    with pytest.raises(UnknownSafeArea):
        safe_area_profile("vertical_9000x16000")




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
    platform = safe_area_for_format("vertical_1080x1920")
    assert plan["style"]["safeArea"]["bottom"] == (platform.bottom
                                                   + CAPTION_LIFT_PX)
    assert (plan["style"]["captionMaxWidth"]
            == platform.centered_usable_width)
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
    assert props["captionMaxWidth"] == 1080 - 2 * max(platform["left"],
                                                      platform["right"])






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






# ── The grouper does not leave runts ──
#
# The balanced split. A greedy fill packs each card to the width limit and
# leaves the remainder as the next card, and a card is on screen only
# until the NEXT card's first word - so the remainder flashes. On project
# 001 that produced 76 cards under half a second out of 96. Nothing
# downstream can repair it: `enforce_min_duration` extends a card while
# preserving the next card's spoken start, then overlap repair trims or
# merges the pair. A greedy card's neighbour starts immediately.

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

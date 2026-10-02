"""The safe area, and the caption fitter that had never run.

One enumeration (`library/tools/safe_area.py`), four consumers, and a
fitter that actually runs: no caption LINE is drawn wider than the usable
width. History: docs/evidence/caption_safe_area.md.
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


def test_every_format_has_a_fractional_profile_and_unknown_raises():
    """The same product in more pixels gets the same profile, scaled."""
    hd = safe_area_for_format("vertical_1080x1920")
    uhd = safe_area_for_format("vertical_2160x3840")
    assert uhd.profile == hd.profile
    for edge in ("top", "right", "bottom", "left"):
        assert abs(getattr(uhd, edge) - 2 * getattr(hd, edge)) <= 1
    # Every delivery format has a profile (one without has unplaced
    # captions), and an unknown one raises rather than defaulting.
    from library.tools.delivery_format import DELIVERY_FORMATS
    assert set(DELIVERY_FORMATS) == set(SAFE_AREAS)
    with pytest.raises(UnknownSafeArea):
        safe_area_profile("vertical_9000x16000")


# ── The fitter actually runs ──

def test_fitter_measures_the_real_font_at_the_rendered_weight():
    """`fits_fn` used to be None on every run. Prove it is not."""
    style = resolve_subtitle_style({}, {})
    fitter = build_caption_fitter(style, resolve_safe_area())
    assert fitter.measured, (
        "the caption fitter fell back to an estimate; it should have "
        f"opened the bundled font for {style['fontFamily']!r}")
    assert fitter.font_path.endswith("Montserrat-Variable.ttf")
    assert fitter.font_size == style["fontSize"]
    # Montserrat-Variable defaults to Thin (100); the render draws 800,
    # so the weight axis must be set or it measures a face nobody renders.
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


def test_no_caption_line_or_card_overflows_the_caption_box():
    """The defect, pinned: no LINE runs off the frame (the box wraps, so
    the bound is per line), no card exceeds the box's lines, and one
    unbreakable word scales its card down. Before: columns 0-1079."""
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

    # A card is a phrase, not a paragraph: the grouper fits the BOX, up to
    # MAX_CAPTION_LINES.
    for entry in entries:
        assert fitter.line_count(entry["text"]) <= MAX_CAPTION_LINES, (
            f"card {entry['id']} {entry['text']!r} wraps onto "
            f"{fitter.line_count(entry['text'])} lines")

    # An unbreakable word is shrunk rather than clipped: the card scales,
    # the style does not.
    assert fitter.word_width("announcement") > fitter.usable_width, (
        "the fixture word no longer overflows; pick one that does")

    card = next(e for e in entries if "announcement" in e["text"])
    assert card["fit_scale"] < 1.0
    assert (fitter.word_width("announcement") * card["fit_scale"]
            <= fitter.usable_width)
    # The style is untouched - only this card is drawn smaller.
    assert style["fontSize"] == resolve_subtitle_style({}, {})["fontSize"]


# ── All four consumers read the one enumeration ──

def test_the_style_and_the_grouper_read_the_one_safe_area():
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
    # The grouper reads the same width - else captions lift clear of the
    # platform UI and still clip left and right.
    safe_area = resolve_safe_area()
    fitter = build_caption_fitter(props, safe_area)
    assert fitter.usable_width == (
        safe_area.centered_usable_width - 2 * props["outlineWidth"])


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


# ── The cascade drops no word (moved from test_subtitle_sync.py) ──

def test_subtitle_cascade_drops_no_word():
    """The tail of a block ("single day.") used to be truncated by the
    cascade and clamping; every spoken word must reach a card."""
    audio_spine = {
        "structure": [
            {
                "position": 1,
                "block_type": "speech",
                "timeline_start": 20.87,
                "timeline_end": 24.41,
                "source_start": 63.135,
                "source_end": 66.675,
                "content": {"text": "and so my very, very small announcement is that i just want to post every single day."},
                "word_timestamps": [
                    {"word": "and", "source_start": 63.0, "source_end": 63.1},
                    {"word": "so", "source_start": 63.1, "source_end": 63.2},
                    {"word": "my", "source_start": 63.2, "source_end": 63.3},
                    {"word": "very,", "source_start": 63.3, "source_end": 63.4},
                    {"word": "very", "source_start": 63.4, "source_end": 63.5},
                    {"word": "small", "source_start": 63.5, "source_end": 63.6},
                    {"word": "announcement", "source_start": 63.6, "source_end": 64.0},
                    {"word": "is", "source_start": 64.0, "source_end": 64.1},
                    {"word": "that", "source_start": 64.1, "source_end": 64.2},
                    {"word": "i", "source_start": 64.2, "source_end": 64.3},
                    {"word": "just", "source_start": 64.3, "source_end": 64.4},
                    {"word": "want", "source_start": 64.4, "source_end": 64.5},
                    {"word": "to", "source_start": 64.5, "source_end": 65.0},
                    {"word": "post", "source_start": 65.0, "source_end": 65.5},
                    {"word": "every", "source_start": 65.5, "source_end": 66.0},
                    {"word": "single", "source_start": 66.395, "source_end": 66.535},
                    {"word": "day.", "source_start": 66.595, "source_end": 66.675},
                ]
            }
        ]
    }

    result = generate_subtitles(audio_spine, caption_case="lowercase", brand_effect={}, brand_style={})
    entries = result["subtitle_plan"]["subtitle_entries"]

    # Assert that all words made it through the cascade and clamping logic.
    # Previous behaviour truncated the tail of the block ("single day.").
    #
    # This asserts the WORDS survive, not which card each lands on. It used
    # to assert the literal card "single day.", which was the grouping a
    # `max_chars = 18` fallback produced; captions are grouped by measured
    # width now (library/tools/safe_area.py, and step 4.01's CaptionFitter),
    # and the split is balanced rather than greedy, so which card any given
    # word lands on is not stable and is not the invariant here. The
    # invariant the test is named for is that nothing is dropped.
    texts = [e["text"] for e in entries]
    spoken = " ".join(w["word"] for w in audio_spine["structure"][0]
                      ["word_timestamps"]).lower()
    assert " ".join(texts) == spoken, (
        f"words lost or reordered.\n  got: {texts}\n  want: {spoken}")
    # The tail of the block specifically, because that is what used to be
    # truncated - on whichever card the split put them.
    assert texts[-1].endswith("single day."), texts

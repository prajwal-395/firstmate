"""Rung 7 (finding 31, C3.2/MG3.1): caption words-per-card is a plan value.

"Captions 2 words max" had no route: `split_into_groups` grouped at a
literal `max_words=6` nothing could set. The ceiling now rides the
brand template's `effect.caption_words_per_card` (None/absent is the
default 6 - the feel-word case, E3), is refused when it is not a
whole number >= 1, and is receipted on the plan as
`subtitle_plan.max_words`.
"""
from __future__ import annotations
import sys
from pathlib import Path
from library.steps.step_4_01_plan_subtitles.step import (
    MIN_CAPTION_FLASH_SECONDS,
    generate_subtitles,
)
import pytest
from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
    generate_subtitle_props_per_block,
)
import os
import re
import pathlib
from library.tools import timeline_transcript as tt
from library.tools import transcript_fit
from library.tools.timeline_ingest import TimelineClip
from library.tools.word_boundaries import sanitize_word_boundaries


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_01_plan_subtitles.step import (
    resolve_max_words_per_card,
    split_into_groups,
)


def _words(n):
    return [{"word": f"w{i}", "start": float(i), "end": float(i) + 0.5}
            for i in range(n)]


def _fits_all(text):
    return True


def test_a_stated_number_is_read():
    assert resolve_max_words_per_card(
        brand_effect={"caption_words_per_card": 2}) == 2


def test_request_word_limit_overrides_the_house_default_and_reaches_grouping():
    """Finding 31: the request's count must control actual card grouping."""
    max_words = resolve_max_words_per_card(
        {"caption_words_per_card": 6}, audio_spine={"max_words": 2})
    groups = split_into_groups(_words(6), fits_fn=_fits_all,
                               max_words=max_words)
    assert [group["word_count"] for group in groups] == [2, 2, 2]


def test_non_integer_request_word_limit_is_refused():
    """A malformed plan number must not silently fall back to the brand."""
    import pytest

    with pytest.raises(ValueError, match="audio_spine.max_words"):
        resolve_max_words_per_card(audio_spine={"max_words": 2.5})


def test_subtitle_plan_records_the_request_limit_used_by_card_grouping(
        monkeypatch):
    """Finding 31: plan_subtitles.max_words must match every grouped card."""
    from library.steps.step_4_01_plan_subtitles import step as subtitles

    class _FitsAll:
        measured = True
        usable_width = 1000

        @staticmethod
        def fits_in_box(_text):
            return True

        @staticmethod
        def fit_scale(_text, _emphasis_words=None):
            return 1.0

    monkeypatch.setattr(subtitles, "resolve_subtitle_style",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(subtitles, "resolve_safe_area",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(subtitles, "build_caption_fitter",
                        lambda *_args, **_kwargs: _FitsAll())
    words = [
        {"word": f"word{i}", "source_start": i * 0.3,
         "source_end": i * 0.3 + 0.2}
        for i in range(4)
    ]
    result = generate_subtitles({
        "max_words": 2,
        "structure": [{
            "position": 1,
            "block_type": "speech",
            "content": {"text": "word0 word1 word2 word3"},
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "source_start": 0.0,
            "source_end": 2.0,
            "word_timestamps": words,
        }],
    }, brand_effect={"caption_words_per_card": 6}, brand_style={})

    plan = result["subtitle_plan"]
    assert plan["max_words"] == 2
    assert [entry["word_count"] for entry in plan["subtitle_entries"]] == [
        2, 2]


def test_two_words_max_groups_pairs():
    """C3.2's shape: no card carries more than the stated 2."""
    groups = split_into_groups(_words(6), fits_fn=_fits_all, max_words=2)
    assert [g["word_count"] for g in groups] == [2, 2, 2]


# --------------------------------------------------------------------------
# From test_caption_word_window_card_anchors.py
#
# A card's reading-speed hold must not strand its first spoken word.
#
# The reel conformance verifier found captions that began after a card's
# first word had already ended.  The rendered props then carry a reversed
# highlight window, and a final short group can fall below F7's 12-frame
# floor.  These frozen spine fixtures exercise that planning path without
# Resolve or a real project.

def _plan(words, text, duration):
    spine = {
        "structure": [{
            "block_type": "speech",
            "position": 1,
            "timeline_start": 0.0,
            "timeline_end": duration,
            "source_start": 0.0,
            "source_end": duration,
            "clip_id": "clip_001",
            "alignment_method": "whisperx",
            "word_timestamps": [
                {"word": word, "source_start": start, "source_end": end}
                for word, start, end in words
            ],
            "content": {"text": text},
            "speaker": "SpeakerTwo",
        }],
    }
    return generate_subtitles(
        spine, caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def _assert_each_word_has_time_inside_its_card(entries):
    for entry in entries:
        for word in entry["words"]:
            visible_start = max(entry["timeline_start"], word["start"])
            visible_end = min(entry["timeline_end"], word["end"])
            assert visible_start < visible_end, (
                f"{word['word']!r} at {word['start']:.3f}-"
                f"{word['end']:.3f}s is outside card "
                f"{entry['timeline_start']:.3f}-"
                f"{entry['timeline_end']:.3f}s ({entry['text']!r})"
            )


def test_readability_hold_does_not_start_the_reel1_to_after_its_word():
    """The old cursor delay put `to` in a later card after it was spoken."""
    words = [
        ("it's", 0.00, 0.16),
        ("something", 0.16, 0.46),
        ("that's", 0.46, 0.63),
        ("going", 0.63, 0.765),
        ("to", 0.765, 0.865),
        ("continually", 0.865, 1.55),
        ("eat", 1.55, 1.71),
        ("into", 1.71, 1.89),
        ("their", 1.89, 2.11),
        ("budget.", 2.11, 2.74),
    ]

    entries = _plan(
        words,
        "it's something that's going to continually eat into their budget.",
        2.74,
    )

    _assert_each_word_has_time_inside_its_card(entries)
    assert sum(
        word["word"] == "to"
        for entry in entries for word in entry["words"]
    ) == 1


def test_late_reel17_tail_does_not_make_an_f7_short_card():
    """The same late-card schedule made the final 9-frame card."""
    words = [
        ("and", 0.00, 0.15),
        ("that", 0.15, 0.23),
        ("would", 0.23, 0.32),
        ("be", 0.32, 0.42),
        ("the", 0.42, 0.51),
        ("first", 0.51, 0.81),
        ("step.", 0.81, 1.10),
    ]

    entries = _plan(
        words, "and that would be the first step.", 1.10,
    )

    _assert_each_word_has_time_inside_its_card(entries)
    short_cards = [
        (entry["text"], entry["timeline_end"] - entry["timeline_start"])
        for entry in entries
        if entry["timeline_end"] - entry["timeline_start"]
        < MIN_CAPTION_FLASH_SECONDS
    ]
    assert not short_cards, f"cards below F7's duration floor: {short_cards}"


# --------------------------------------------------------------------------
# From test_caption_segments_follow_card_grouping.py
#
# Caption segments follow the karaoke card grouping, not transcript rows.
#
# Option (b) the captain chose 2026-09-17: a timeline caption clip holds
# the cards the craft put together, and the craft's finest unit is one
# card. Step 4.05 groups plan entries by `(spine_block_position,
# card_index)` - one rendered segment per karaoke card - instead of one
# per transcript row (spine block). The card boundaries stay step 4.01's
# `split_into_groups` partition (max words, word gap, measured pixel
# fit): the segmenter reads them rather than re-deciding them, so there
# is no second rule set to drift.
#
# The failure mode this file exists to catch is karaoke discontinuity
# across a segment join: a passing build that silently eats a word at
# every join is not visible in a clip count. Every test below is written
# so that reverting the behaviour it guards turns it red; the comment on
# each says which mutation.

# ── Fixtures ─────────────────────────────────────────────────────────

def _word(word, start, end):
    return {"word": word, "start": start, "end": end}


def _entry(block, index, tl_start, tl_end, words, text=None):
    """One plan entry shaped like step 4.01's output."""
    return {
        "id": f"sub_{block}_{index + 1:03d}",
        "card_index": index,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "text": text if text is not None else " ".join(w[0] for w in words),
        "emphasis_words": [],
        "spine_block_position": block,
        "speaker": "SpeakerTwo",
        "word_count": len(words),
        "words": [_word(w, s, e) for w, s, e in words],
    }


def _plan_2():
    """Two blocks shaped like Reel 26: one long row of three cards and
    one short row of one card. Four cards, two rows."""
    return {
        "subtitle_entries": [
            _entry(0, 0, 0.0, 1.2, [("what", 0.0, 0.3),
                                    ("kind", 0.35, 0.6),
                                    ("of", 0.65, 0.8),
                                    ("content", 0.85, 1.2)]),
            _entry(0, 1, 1.2, 2.8, [("works", 1.2, 1.5),
                                    ("best", 1.55, 1.8),
                                    ("when", 1.9, 2.1),
                                    ("it", 2.15, 2.3),
                                    ("comes", 2.35, 2.8)]),
            _entry(0, 2, 2.8, 4.9, [("to", 2.8, 3.0),
                                    ("being", 3.05, 3.4),
                                    ("discovered", 3.45, 4.0),
                                    ("on", 4.05, 4.2),
                                    ("platforms?", 4.25, 4.9)]),
            _entry(1, 0, 5.2, 8.8, [("yeah", 5.2, 5.5),
                                    ("so", 5.55, 5.8),
                                    ("the", 5.85, 6.0),
                                    ("best", 6.1, 6.5),
                                    ("content", 6.6, 8.8)]),
        ],
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "fontWeight": 800,
            "position": "bottom",
            "safeArea": {"top": 120, "right": 90, "bottom": 324,
                         "left": 90},
            "captionMaxWidth": 900,
        },
    }


def _props(entries=None):
    plan = _plan_2()
    if entries is not None:
        plan = dict(plan, subtitle_entries=entries)
    return generate_subtitle_props_per_block(
        plan, fps=24000 / 1001, width=1080, height=1920)


# ── The grouping ─────────────────────────────────────────────────────

def test_one_segment_per_card_not_per_row():
    """The shape of option (b): two rows of 3+1 cards render as four
    segments, not two. Reverts red if the grouping key goes back to
    `spine_block_position` alone (two props, one per block)."""
    props = _props()
    assert len(props) == 4
    assert [len(p["subtitles"]) for p in props] == [1, 1, 1, 1]
    assert [(p["_block_position"], p["_card_index"]) for p in props] == [
        (0, 0), (0, 1), (0, 2), (1, 0)]


# ── Karaoke continuity across a segment join ─────────────────────────

def _expected_frames(entries, fps):
    """Every planned word as (word, startFrame, endFrame), in plan
    order, re-based exactly the way the props builder re-bases: the
    card's start minus its render buffer. Independent of the props -
    this recomputes from the plan entries, so any word the props
    drop, duplicate or re-time shows up as a mismatch."""
    out = []
    for e in entries:
        render_start = e["timeline_start"] - min(0.5, e["timeline_start"])
        for w in e["words"]:
            out.append((
                w["word"],
                round((w["start"] - render_start) * fps),
                round((w["end"] - render_start) * fps),
            ))
    return out


def _rendered_frames(props):
    """Every rendered word as (word, startFrame, endFrame), in segment
    order - the frame timings the renderer actually draws."""
    out = []
    for p in props:
        for sub in p["subtitles"]:
            for w in sub["words"]:
                out.append((w["word"], w["startFrame"], w["endFrame"]))
    return out


def test_no_word_dropped_duplicated_or_retimed_across_joins():
    """The core continuity proof: the words the segments render, in
    order with their frame timings, are exactly the words the plan
    planned. A join that eats a word fails here (sequences differ in
    length); one that duplicates fails here (same); one that re-times
    fails here (same words, different frames). Compared in frames, so
    there is no tolerance to hide behind - the re-basing is integer
    arithmetic both sides."""
    entries = _plan_2()["subtitle_entries"]
    props = _props()
    assert _rendered_frames(props) == _expected_frames(
        entries, 24000 / 1001)


def test_no_card_displays_past_its_segment():
    """The re-expressed `display_until` invariant: a segment's edges are
    its card's display interval, so a card cannot display past the
    segment that carries it. Reverts red if segment bounds ever stop
    being read off the cards (e.g. back to block bounds while cards
    hold per-card timings)."""
    entries = _plan_2()["subtitle_entries"]
    props = _props()
    by_key = {(p["_block_position"], p["_card_index"]): p for p in props}
    assert len(by_key) == len(props), "two segments claim one card"
    for e in entries:
        seg = by_key[(e["spine_block_position"], e["card_index"])]
        assert seg["_timeline_start"] == pytest.approx(
            e["timeline_start"]), (
            f"card {e['id']} starts at {e['timeline_start']} but its "
            f"segment starts at {seg['_timeline_start']}")
        assert seg["_timeline_end"] == pytest.approx(
            e["timeline_end"]), (
            f"card {e['id']} ends at {e['timeline_end']} but its "
            f"segment ends at {seg['_timeline_end']}")


def test_every_segment_edge_is_a_card_edge():
    """No cut through a card: each segment starts where one card starts
    and ends where one (the same one, today) ends. A segmenter that
    invents its own boundaries fails here the moment one lands
    mid-card."""
    entries = _plan_2()["subtitle_entries"]
    props = _props()
    starts = {e["timeline_start"] for e in entries}
    ends = {e["timeline_end"] for e in entries}
    for p in props:
        assert p["_timeline_start"] in starts, (
            f"segment starts at {p['_timeline_start']}, no card starts "
            f"there - a boundary the craft never chose")
        assert p["_timeline_end"] in ends, (
            f"segment ends at {p['_timeline_end']}, no card ends "
            f"there - a boundary the craft never chose")


# ── A talk-over trim at a segment join ─────────────────────────────

def _trimmed_join_plan():
    """Two cards shaped like Reel 26's real talk-over trim: step 4.01's
    `resolve_caption_overlaps` ended the earlier card where the next
    card's first word starts (30.82-31.74), but the earlier card's last
    word ('it.') is spoken just past that edge. The plan is
    self-contradictory there - the word outlives its card - and the
    renderer bounds every word to its card. This fixture pins that the
    bound is the ONLY deviation: anything else is a join eating words.
    """
    return {
        "subtitle_entries": [
            _entry(3, 8, 30.82, 31.74,
                   [("what", 30.82, 31.0),
                    ("you", 31.05, 31.2),
                    ("can", 31.25, 31.45),
                    ("say", 31.5, 31.62),
                    ("about", 31.62, 31.7),
                    ("it.", 31.78, 31.9)]),
            _entry(4, 0, 31.74, 33.09,
                   [("and", 31.74, 31.9),
                    ("ai", 31.95, 32.1),
                    ("really", 32.15, 32.4),
                    ("likes", 32.45, 32.7),
                    ("that.", 32.75, 33.09)]),
        ],
        "style": _plan_2()["style"],
    }


def _card_bounded_frames(entries, fps):
    """The plan's word frames with the card-bounding clamp applied -
    recomputed here, independently of the renderer: each word keeps its
    measured onset exactly and never displays past its own card's edge.
    The renderer must equal this and nothing else."""
    out = []
    for e in entries:
        render_start = e["timeline_start"] - min(0.5, e["timeline_start"])
        card_start = round((e["timeline_start"] - render_start) * fps)
        card_end = round((e["timeline_end"] - render_start) * fps)
        for w in e["words"]:
            out.append((
                w["word"],
                max(card_start, round((w["start"] - render_start) * fps)),
                min(card_end, round((w["end"] - render_start) * fps)),
            ))
    return out


def test_trimmed_join_draws_the_overhanging_word_unswept(capsys):
    """The join that already exists on Reel 26: the earlier card yields
    at 31.74 but its last word ('it.', spoken 31.78-31.90) starts past
    that edge, so the card bound leaves it no width. It is DRAWN
    UNSWEPT with a loud record - the card text carries it and the
    renderer draws `words`, so omitting it would delete it from the
    captions (Reel 05 frame 241: the numbers). Everything else is kept:
    no word dropped or duplicated across the join (each exactly once),
    onsets never move, and no word displays past its segment. Reverts
    red if the renderer drops the overhang again (sequence loses
    'it.'), shifts an onset (start differs), duplicates (sequence
    longer), widens the overhang into invented timing (end > start on
    a word with no sweepable width), or stops bounding words to the
    card (end past the edge)."""
    plan = _trimmed_join_plan()
    entries = plan["subtitle_entries"]
    fps = 24000 / 1001
    props = generate_subtitle_props_per_block(
        plan, fps=fps, width=1080, height=1920)
    assert len(props) == 2
    rendered = _rendered_frames(props)
    assert rendered == _card_bounded_frames(entries, fps)
    assert [w for w, _, _ in rendered] == [
        w["word"] for e in entries for w in e["words"]]
    overhang = [(w, s, e) for w, s, e in rendered if w == "it."]
    assert len(overhang) == 1 and overhang[0][2] <= overhang[0][1], (
        "the overhang must arrive collapsed (drawn unswept), never "
        "widened into invented timing and never omitted")
    for word, start, end in rendered:
        if word == "it.":
            continue
        assert end > start, (
            f"{word!r} renders {start} -> {end}: a dead sweep")
    err = capsys.readouterr().err
    assert "it." in err, (
        "the unswept overhang is never named - the clamp is silent "
        "about the window it collapsed")
    for p in props:
        content_end = round(
            (p["_timeline_end"] - (p["_timeline_start"] - min(
                0.5, p["_timeline_start"]))) * fps)
        for sub in p["subtitles"]:
            for w in sub["words"]:
                assert w["endFrame"] <= sub["endFrame"], (
                    f"{w['word']!r} displays past its card "
                    f"({w['endFrame']} > {sub['endFrame']})")


# ── The 4.01 half of the contract ────────────────────────────────────

def _spine_block(position, tl_start, duration, words, src=0.0):
    stamps = [{"word": w,
               "source_start": round(src + i * duration / len(words), 3),
               "source_end": round(src + (i + 0.8) * duration / len(words), 3)}
              for i, w in enumerate(words)]
    return {
        "position": position, "block_type": "speech", "clip_id": "clip_001",
        "source_start": src, "source_end": round(src + duration, 3),
        "timeline_start": tl_start,
        "timeline_end": round(tl_start + duration, 3),
        "word_timestamps": stamps, "alignment_method": "whisperx",
        "content": {"text": " ".join(words)},
    }


def test_plan_to_props_partitions_whole_cards():
    """End to end on a synthetic spine: the props hold every planned
    card exactly once, with the same words - the plan-to-render join
    neither splits a card across two segments nor renders one twice."""
    spine = {"structure": [
        _spine_block(0, 0.0, 6.0,
                     ["one", "two", "three", "four", "five", "six",
                      "seven", "eight", "nine"]),
        _spine_block(1, 6.0, 3.0, ["ten", "eleven", "twelve"]),
    ]}
    plan = generate_subtitles(spine)["subtitle_plan"]
    entries = plan["subtitle_entries"]
    props = generate_subtitle_props_per_block(
        plan, fps=30, width=1080, height=1920)
    assert len(props) == len(entries)
    assert [(p["_block_position"], p["_card_index"]) for p in props] == [
        (e["spine_block_position"], e["card_index"]) for e in entries]
    assert _rendered_frames(props) == _expected_frames(entries, 30)


# --------------------------------------------------------------------------
# From test_sentence_boundary_cards.py
#
# Why Reel 17's period sits mid-card, and what the planner prefers.
#
# Reel 17, clip marker at timeline frame 222 (captain's note: "there is a
# period in the middle of the subtitles here that i don't think should
# belong there"). The placed card reads "all the time. like what are some
# of the" - a period mid-card followed by a lowercase continuation.
#
# The diagnosis, measured rather than assumed. The transcript carries two
# real sentences - seg284 "We talk about it all the time." (source
# 3127.42-3128.63) and seg285 "Like what are some of the first steps
# these businesses should be doing?" (source 3128.39-3131.42) - and an
# independent second opinion (small.en ASR on the card's audio slice)
# hears the same boundary ("...all the time," then "like what are some
# of..."), so it is real speech, not a transcription artefact. The period is
# real punctuation, so no correction-store shape reaches it: a display
# suppression hides whole tokens (nothing here is hidden - every word is
# spoken and wanted) and a spelling correction that deleted the period
# would falsify the sentence the microphone caught. The fix belongs in
# caption planning, and planning currently CHOOSES the straddle on
# purpose: ending cards on punctuation is only the FOURTH tiebreak in
# `split_into_groups` (after flashing, under-floor and longest-shortest),
# and splitting the run at "time." would leave a card on screen for the
# gap to "like" - 0.21s, under the 0.5s flash floor the manifest
# validator fails a build on. The optimizer picks a straddled sentence
# over a flashing card. That tradeoff is a planner decision, fleet-wide,
# so this lane reports it rather than re-tuning the optimizer: the note
# needs a planner rule plus a Reel 17 caption rebuild, and neither is a
# correction-store entry or a caption swap.
#
# What these tests pin, and what they do NOT. They pin the two halves of
# the mechanism above - the flash-floor arithmetic that makes the split
# expensive, and the tiebreak that keeps the preference weak - so a
# future optimizer rewrite cannot drop either silently. They do NOT
# reproduce Reel 17: the reproduction feeds the word run below to
# `split_into_groups` with the real pixel fitter and asserts no emitted
# card matches `[.!?]\s+\S` mid-card, and it FAILS on current code (the
# second card comes out "the time. like what"). That failing shape is
# the planner lane's specification, kept here so it cannot drift from
# the mechanism it constrains. No test reaches a real project: every
# number below is frozen off transcript segments 284/285.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_01_plan_subtitles.step import (
    CaptionFitter,
    resolve_caption_overlaps,
)
from library.tools.render_fonts import measurable_font_path

#: A sentence break inside a card: terminal punctuation followed by more
#: words. The captain's Reel 17 note names exactly this shape.
MID_CARD_SENTENCE_BREAK = re.compile(r"[.!?]\s+\S")


def _reel_fitter() -> CaptionFitter:
    """The caption measure the live Reel 17 card was grouped against.

    Repo-local only: the bundled Montserrat at the reel caption size,
    inside the project's measured usable width (816px centred, less
    the 4px outline each side - frozen off the live plan, never read
    off the project, so no test reaches a real project).
    """
    return CaptionFitter(
        font_path=measurable_font_path("Montserrat", None, None),
        font_size=58,
        font_weight=800,
        usable_width=816.0 - 8,
        outline_width=4,
    )

# seg284 tail + seg285 head, transcript seconds, reading-lowercased -
# the exact run the Reel 17 card straddles.
WORDS = [
    ("we", 1415.29, 1415.42),
    ("talk", 1415.42, 1415.66),
    ("about", 1415.66, 1415.86),
    ("it", 1415.86, 1415.92),
    ("all", 1415.92, 1416.05),
    ("the", 1416.05, 1416.13),
    ("time.", 1416.13, 1416.50),
    ("like", 1416.26, 1416.67),
    ("what", 1416.67, 1416.79),
    ("are", 1416.79, 1416.86),
    ("some", 1416.86, 1417.04),
    ("of", 1417.04, 1417.08),
    ("the", 1417.08, 1417.14),
    ("first", 1417.14, 1417.51),
    ("steps", 1417.51, 1417.87),
    ("these", 1417.87, 1418.11),
    ("businesses", 1418.11, 1418.64),
    ("should", 1418.64, 1418.81),
    ("be", 1418.81, 1418.91),
    ("doing?", 1418.91, 1419.29),
]


def test_punctuation_end_is_a_tiebreak_not_a_rule():
    """The preference exists, weakly: among partitions equal on
    flashing, floor and balance, the one ending cards on punctuation
    wins - and a cheaper flashing count overrules it, which is the
    Reel 17 outcome."""
    words = [
        {"word": w, "start": s, "end": e}
        for w, s, e in [
            ("all", 0.0, 0.2),
            ("day.", 0.2, 0.6),
            ("every", 0.6, 0.8),
            ("day", 0.8, 1.4),
        ]
    ]
    even = split_into_groups(
        words, fits_fn=lambda text: len(text) <= 12, display_until=1.4
    )
    # "all day." (ends on the period) must beat "all day. every"'s
    # rival arrangement wherever flashing and floor tie - the
    # preference the planner lane will promote to a rule.
    assert even, "the split found no partition at all"
    joined = " | ".join(g["text"] for g in even)
    assert joined == "all day. | every day", (
        f"the punctuation tiebreak stopped preferring sentence ends: {joined}"
    )


def _live_words():
    return [
        {"word": w, "start": s, "end": e} for w, s, e in WORDS
    ]


def test_no_card_straddles_a_sentence():
    """The live specimen, planned: seg284's sentence must end a card.

    Feeds the frozen Reel 17 word run to `split_into_groups` under the
    real pixel fitter and asserts no emitted card carries a sentence
    break mid-card - the placed card reads "all the time. like what
    are some of the", and this is the test the planner fix must turn
    green. Fails on current code (the second card comes out "time.
    like what are some of").
    """
    groups = split_into_groups(
        _live_words(),
        fits_fn=_reel_fitter().fits_in_box,
        display_until=WORDS[-1][2] + 0.5,
    )
    assert groups, "the split found no partition at all"
    for group in groups:
        assert not MID_CARD_SENTENCE_BREAK.search(group["text"]), (
            f"a card straddles a sentence end: {group['text']!r} "
            f"in {' | '.join(g['text'] for g in groups)}"
        )
    assert any(g["text"].endswith("time.") for g in groups), (
        "no card ends on the seg284 sentence: "
        f"{' | '.join(g['text'] for g in groups)}"
    )


def _overlap_entry(text, start, end, speaker="SpeakerTwo"):
    words = [
        {"word": word, "start": start, "end": end}
        for word in text.split()
    ]
    return {
        "id": f"t_{text[:4]}",
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "speaker": speaker,
        "word_count": len(words),
        "words": words,
        "emphasis_words": [],
    }


def test_sentence_final_runt_merges_backward():
    """The cross-block half: a card ending on a period joins the card
    before it, never the sentence after it.

    Transcript segs 284/285 overlap by 0.24s, so per-block cards
    overlap on the single track and `resolve_caption_overlaps` merges
    the trimmed runt forward - producing the same mid-card period the
    grouping half makes ("all the time. like what ..."). A runt that
    ends a thought must merge backward instead, so the joined card
    ends where the sentence ends.
    """
    entries = [
        _overlap_entry("we talk about it all", 1415.29, 1416.05),
        _overlap_entry("the time.", 1416.05, 1416.50),
        _overlap_entry("like what are some of", 1416.26, 1417.14),
    ]
    report = resolve_caption_overlaps(entries)
    assert report["merged"] == 1, f"nothing merged: {report}"
    texts = [entry["text"] for entry in entries]
    assert texts == [
        "we talk about it all the time.",
        "like what are some of",
    ], f"the runt merged forward into the next sentence: {texts}"
    for text in texts:
        assert not MID_CARD_SENTENCE_BREAK.search(text), (
            f"a merged card straddles a sentence end: {text!r}"
        )


# --------------------------------------------------------------------------
# From test_stretched_word_caption.py
#
# A stretched word must not become a hanging caption card (reel 24).
#
# Reel 24 `why-ai-trusts-youtube` was REFUSED in the 2026-09-08 rebuild: the
# card 'concise,' hung 2.03s past its speech against F15's 1.0s limit. The
# card's end comes FROM THE WORD - the transcript times 'concise,' over
# ~3.03s, and the planner renders that span faithfully:
#
# - a block ending 3s after a 0.4s word still plans a 0.7s card (the block
#   boundary does not stretch it);
# - a next card starting 2.6s later still leaves a 0.7s card (the next
#   card's start does not stretch it);
# - only a ~3s word span itself produces a ~3s card.
#
# A word spanning longer than `MAX_WORD_SECONDS` is the aligner bridging
# silence, not speech (PR 692 measured 8,492 bound words, genuine max
# 2.65s). The planner is the side that is wrong: it drew 3.03s of caption
# time over a span the pipeline already knows is not speech. The gate is
# right to refuse, and is left untouched - the second test pins that it
# still fires on exactly this shape.
#
# These tests observe the behaviour THROUGH `generate_subtitles`, not the
# grouping function in isolation: a unit test of the clamp would pass all
# along, which proves nothing about the cards a reel carries.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


from library.tools.reel_conformance_verifier import (
    FindingClass,
    check_caption_hangs,
)

FPS = 24000 / 1001  # 23.976 exact


def _spine(words):
    """One speech block carrying `words` as its own word timestamps."""
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 10.0,
        "timeline_end": 13.5,
        "source_start": 100.0,
        "source_end": 103.5,
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": " ".join(w["word"] for w in words)},
    }]}


def _entries(spine):
    return generate_subtitles(
        spine, caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def _cards(entries):
    return [
        {"reel_start": e["timeline_start"], "reel_end": e["timeline_end"],
         "text": e["text"]}
        for e in entries
    ]


def test_stretched_single_word_plans_a_floor_card_and_passes_f15(capsys):
    """Reel 24's shape: one word timed over 3.03s plans a 0.7s card.

    Before the fix this planned a 3.03s card and F15 refused it - the
    refusal the rebuild held reel 24 for. After the fix the word keeps
    its (trustworthy) onset for the legibility floor, and the gate that
    refused the 3.03s card has nothing to say.
    """
    entries = _entries(_spine([
        {"word": "concise,", "source_start": 100.0, "source_end": 103.03},
    ]))

    assert len(entries) == 1
    assert entries[0]["text"] == "concise,"
    duration = entries[0]["timeline_end"] - entries[0]["timeline_start"]
    assert duration == pytest.approx(0.7, abs=0.01)
    assert entries[0]["timeline_start"] == pytest.approx(100.0 - 100.0 + 10.0)

    assert check_caption_hangs("Reel 24", _cards(entries), FPS) == []

    # SAID, not silent: the rewritten timing names its word.
    assert "concise," in capsys.readouterr().err


def test_f15_still_refuses_a_three_second_single_word_card():
    """The gate keeps its teeth: a 3.03s one-word card still fails.

    This is the card the planner used to emit. The fix removes the
    producer of such cards; it must not also remove the check that
    would catch one if it ever came back.
    """
    findings = check_caption_hangs(
        "Reel 24",
        [{"reel_start": 10.0, "reel_end": 13.03, "text": "concise,"}],
        FPS,
    )
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.F15


def test_genuine_slow_words_are_not_clamped():
    """A 2.6s word is slow speech, not a stretched one: hands off.

    PR 692 measured genuine words up to 2.65s across 8,492 bound words.
    The clamp reads the same `MAX_WORD_SECONDS` bound F5 does, so what
    counts as speech keeps its measured end here too.
    """
    entries = _entries(_spine([
        {"word": "weell", "source_start": 100.0, "source_end": 102.6},
    ]))

    assert len(entries) == 1
    duration = entries[0]["timeline_end"] - entries[0]["timeline_start"]
    assert duration == pytest.approx(2.6, abs=0.01)


# --------------------------------------------------------------------------
# From test_rank_phrase_caption_contract.py
#
# A rank phrase on a caption card must not refuse the build (Reel 08).
#
# Reel 08 `top-three-on-google-hallucinated-by-ai` was REFUSED in the
# 2026-09-20 duplicate-take rebuild: step 4.01 planned the card "the top
# three" - the declared reading, since rank labels stay words
# (`tests/unit/captions/test_subtitle_style.py::test_pronouns_ranks_and_style_stay_words`)
# - and then its own contract check re-read each word in ISOLATION, where
# a bare "three" reads "3". The planner was right and the gate failed
# correct output (AGENTS.md 10.4).
#
# The contract now re-reads each card with its own block-predecessor's
# last word as left context, comparing only the card's own words - the
# way the planner applies the reading to the block's whole word stream
# before grouping.
#
# These tests observe the behaviour THROUGH `generate_subtitles`, not the
# reading in isolation: a unit test of the reading passes all along,
# which proves nothing about the cards a reel carries.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _spine_2(text):
    """One speech block carrying `text` as its own word timestamps."""
    words = text.split()
    step = 0.35
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 10.0,
        "timeline_end": 10.0 + len(words) * step,
        "source_start": 100.0,
        "source_end": 100.0 + len(words) * step,
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": w, "source_start": 100.0 + i * step,
             "source_end": 100.0 + i * step + 0.3}
            for i, w in enumerate(words)
        ],
        "content": {"text": text},
    }]}


def _entries_2(text):
    return generate_subtitles(
        _spine_2(text), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def test_rank_phrase_on_one_card_passes_contract():
    """Reel 08's shape: "the top three" plans and is not refused.

    Before the fix this raised `AssertionError: ... word is not the
    declared reading of itself: three`. After the fix the card reads
    back exactly as drawn.
    """
    entries = _entries_2("the top three searches but on")
    assert " ".join(e["text"] for e in entries) == \
        "the top three searches but on"


def test_bare_numerals_still_read_as_digits():
    """The fix must not blunt the rule where it applies.

    A quantity with no rank marker still renders (and verifies) as
    digits - the contract keeps its teeth on real violations.
    """
    entries = _entries_2("it gave three stars and two percent growth")
    assert " ".join(e["text"] for e in entries) == \
        "it gave 3 stars and 2 percent growth"


# --------------------------------------------------------------------------
# From test_subtitle_readability_planning.py

def _speech_spine(words, *, source_end, timeline_end):
    return {
        "structure": [{
            "position": 7,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": timeline_end,
            "source_start": 0.0,
            "source_end": source_end,
            "content": {"text": " ".join(word["word"] for word in words)},
            "word_timestamps": words,
        }],
    }


def test_plan_extends_a_fast_card_without_changing_its_words():
    spine = _speech_spine([
        {"word": "this", "source_start": 0.0, "source_end": 0.1},
        {"word": "needs", "source_start": 0.12, "source_end": 0.25},
        {"word": "time.", "source_start": 0.3, "source_end": 0.5},
    ], source_end=0.5, timeline_end=2.0)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={},
        fps=24000 / 1001,
    )["subtitle_plan"]
    entries = plan["subtitle_entries"]

    assert len(entries) == 1
    entry = entries[0]
    duration = entry["timeline_end"] - entry["timeline_start"]
    assert entry["text"] == "this needs time."
    assert [word["word"] for word in entry["words"]] == [
        "this", "needs", "time."
    ]
    assert duration >= 0.5
    assert len(entry["text"]) / duration <= 25
    assert plan["readability_issues"] == []


def test_reel08_short_block_tail_uses_the_safe_gap_before_the_next_block():
    # Frozen from Reel 08's current staging plan. The 0.38s block contains
    # only "mm-hmm."; block clamping used to undo its 0.7s minimum hold.
    spine = {"structure": [
        {
            "position": 5,
            "block_type": "speech",
            "timeline_start": 26.03,
            "timeline_end": 26.41,
            "source_start": 1285.0636666666664,
            "source_end": 1285.4436666666666,
            "speaker": "SpeakerOne",
            "content": {"text": "mm-hmm."},
            "word_timestamps": [{
                "word": "mm-hmm.",
                "source_start": 1285.0636666666664,
                "source_end": 1285.4436666666666,
            }],
        },
        {
            "position": 6,
            "block_type": "speech",
            "timeline_start": 26.697,
            "timeline_end": 28.307,
            "source_start": 1297.023,
            "source_end": 1298.633,
            "speaker": "SpeakerOne",
            "content": {"text": "ranking tells Google that you exist."},
            "word_timestamps": [
                {"word": "ranking", "source_start": 1297.023,
                 "source_end": 1297.273},
                {"word": "tells", "source_start": 1297.273,
                 "source_end": 1297.523},
                {"word": "Google", "source_start": 1297.523,
                 "source_end": 1297.923},
                {"word": "that", "source_start": 1297.923,
                 "source_end": 1298.103},
                {"word": "you", "source_start": 1298.103,
                 "source_end": 1298.303},
                {"word": "exist.", "source_start": 1298.303,
                 "source_end": 1298.633},
            ],
        },
    ]}

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={},
        fps=24000 / 1001,
    )["subtitle_plan"]
    entries = plan["subtitle_entries"]
    short = next(entry for entry in entries if entry["text"] == "mm-hmm.")

    assert short["timeline_start"] == 26.03
    short_frames = round((short["timeline_end"] - short["timeline_start"])
                         * (24000 / 1001))
    assert short_frames >= 12
    assert short["timeline_end"] < 26.697
    assert [word["word"] for word in short["words"]] == ["mm-hmm."]
    assert all(
        round((entry["timeline_end"] - entry["timeline_start"])
              * (24000 / 1001)) >= 12
        for entry in entries)
    assert plan["readability_issues"] == []


def test_unfixable_sub_floor_block_refuses_instead_of_emitting_f7_card():
    spine = _speech_spine([
        {"word": "2026.", "source_start": 0.0, "source_end": 0.212},
    ], source_end=0.212, timeline_end=0.212)

    with pytest.raises(ValueError, match="under the 0.500s readability floor"):
        generate_subtitles(
            spine, caption_case="as_written", brand_effect={}, brand_style={}
        )


def test_duration_floor_uses_frame_ceil_for_25fps_cards():
    spine = {"structure": [
        {
            "position": 1,
            "block_type": "speech",
            "timeline_start": 0.01,
            "timeline_end": 0.51,
            "source_start": 0.0,
            "source_end": 0.5,
            "content": {"text": "short"},
            "word_timestamps": [
                {"word": "short", "source_start": 0.01,
                 "source_end": 0.3},
            ],
        },
        {
            "position": 2,
            "block_type": "speech",
            "timeline_start": 0.8,
            "timeline_end": 1.4,
            "source_start": 0.8,
            "source_end": 1.4,
            "content": {"text": "next sentence."},
            "word_timestamps": [
                {"word": "next", "source_start": 0.8,
                 "source_end": 0.95},
                {"word": "sentence.", "source_start": 0.95,
                 "source_end": 1.2},
            ],
        },
    ]}

    entries = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={},
        fps=25,
    )["subtitle_plan"]["subtitle_entries"]
    short = next(entry for entry in entries if entry["text"] == "short")

    assert round((short["timeline_end"] - short["timeline_start"]) * 25) >= 13
    assert short["timeline_end"] < 0.8


def test_a_word_starting_at_the_exclusive_source_end_is_not_drawn():
    # Reel 03's excluded word begins 8 microseconds after its staged source
    # end. Rounding its reel time to milliseconds still lands at the edge.
    spine = _speech_spine([
        {"word": "typing", "source_start": 0.7, "source_end": 0.9},
        {"word": "best", "source_start": 1.0000083, "source_end": 1.2},
    ], source_end=1.0, timeline_end=1.3)

    entries = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]["subtitle_entries"]

    assert [word["word"] for entry in entries for word in entry["words"]] == [
        "typing"]
    assert all(entry["text"] == "typing" for entry in entries)


def test_short_tail_can_merge_across_long_pause_to_keep_date_readable():
    spine = _speech_spine([
        {"word": "today", "source_start": 0.0, "source_end": 0.28},
        {"word": "is", "source_start": 0.30, "source_end": 0.34},
        {"word": "march", "source_start": 0.68, "source_end": 1.24},
        {"word": "25th,", "source_start": 1.28, "source_end": 1.60},
        {"word": "2026.", "source_start": 2.769, "source_end": 2.981},
    ], source_end=2.981, timeline_end=2.981)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]
    entries = plan["subtitle_entries"]

    assert any("25th, 2026." in entry["text"] for entry in entries)
    assert [word["word"] for entry in entries for word in entry["words"]] == [
        "today", "is", "march", "25th,", "2026."
    ]
    assert plan["readability_issues"] == []


def test_unfixably_fast_single_word_is_reported_by_card_id():
    spine = _speech_spine([
        {"word": "unreadablefastcaption", "source_start": 0.0,
         "source_end": 0.8},
    ], source_end=0.8, timeline_end=0.8)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]
    entry = plan["subtitle_entries"][0]

    assert entry["text"] == "unreadablefastcaption"
    assert plan["readability_issues"] == [{
        "card_id": entry["id"],
        "spine_block_position": 7,
        "text": "unreadablefastcaption",
        "duration_seconds": 0.8,
        "characters_per_second": 26.25,
        "reasons": [
            "reading_speed_over_25_characters_per_second",
        ],
    }]


# --------------------------------------------------------------------------
# From test_caption_min_duration_is_called.py
#
# `enforce_min_duration` is CALLED, on every path that plans captions.
#
# The rule was defined, documented by two other modules as though it ran,
# and wired to nothing: grepped across `library/` and `tests/` the name
# appeared three times - its own definition and two comments describing its
# behaviour.  Zero call sites.  A unit test of the function would have
# passed all along, which is precisely why these tests observe the CALL
# through `generate_subtitles` rather than the function in isolation.
#
# Measured on the nineteen approved reels of `lucie/geo-podcast`: 832
# caption cards, one overlapping pair (`sub_1_001` ends 6.253, `sub_1_002`
# starts 6.223) that the overlap clause removes.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.steps.step_4_01_plan_subtitles import step as plan_subtitles


def _spine_3(words, text=None):
    """One speech block carrying `words` as its own word timestamps."""
    text = text or " ".join(w["word"] for w in words)
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


def _overlapping_words(count=8, dur=0.4, overlap=0.03):
    """Words whose ASR timings overlap their neighbour, as 001's do.

    Every adjacent pair overlaps, so wherever the grouper splits, the
    boundary pair is an overlapping one and the two cards it makes cannot
    coexist on one subtitle track.
    """
    words, t = [], 0.0
    for i in range(count):
        words.append({"word": f"word{i}", "source_start": round(t, 3),
                      "source_end": round(t + dur, 3)})
        t += dur - overlap
    return words


def test_generate_subtitles_calls_enforce_min_duration(monkeypatch):
    """The master path calls the rule; it does not re-implement it.

    The site used to carry a hand-inlined copy that extended a short card
    only as far as the next card's first word and never closed an
    overlap.  A spy is the only thing that tells the two apart: the
    inlined version and the call produce the same shape of output.
    """
    seen = []
    original = plan_subtitles.enforce_min_duration

    def spy(groups, *args, **kwargs):
        seen.append([dict(g) for g in groups])
        return original(groups, *args, **kwargs)

    monkeypatch.setattr(plan_subtitles, "enforce_min_duration", spy)
    entries = _entries(_spine_3(_overlapping_words()))

    assert entries
    assert seen, (
        "generate_subtitles planned captions without calling "
        "enforce_min_duration - the rule is wired to nothing again")
    for groups in seen:
        for group in groups:
            assert "start" in group and "end" in group, (
                "enforce_min_duration reads start/end; the entries must be "
                f"projected onto those keys before the call, got {group}")


def test_overlapping_word_timings_do_not_reach_the_cards():
    """The overlap clause is what removes F6, and it only runs if called.

    Without the call this fixture emits two cards that overlap by the
    same 0.03s the ASR's own word timings carry - which is the pair the
    conformance verifier found on Reel 01.
    """
    entries = sorted(_entries(_spine_3(_overlapping_words())),
                     key=lambda e: e["timeline_start"])
    assert len(entries) > 1, "the fixture must produce more than one card"
    overlaps = [
        (a["id"], a["timeline_end"], b["id"], b["timeline_start"])
        for a, b in zip(entries, entries[1:])
        if a["timeline_end"] > b["timeline_start"] + 0.01
    ]
    assert not overlaps, f"overlapping caption cards: {overlaps}"


# --------------------------------------------------------------------------
# From test_karaoke_dead_windows.py
#
# Collapsed karaoke windows: every planned word is drawn, the sweep skips what it cannot cross.
#
# Reel 05, timeline frame 241 (captain's note: "the subtitles are messed up
# here"): seven collapsed highlight windows across three caption cards. The aligner
# stamped six transcript words onto one 0.02s instant (segment 87,
# 353.89-353.91) plus an overlapping '10-man', step 4.01 grouped them without
# validating, and step 4.05's card-bounding clamp (`max`/`min` in
# `generate_remotion_props`) collapses each to startFrame == endFrame. A window
# with no width can never satisfy the accent phase, so the sweep never moves
# over those words.
#
# The 2026-09-19 contract pinned here SKIPPED those words - omitted them from
# the props with a loud record ("honest absence"). The captain's 2026-09-21
# rewrite ("not all the words are being included, specifically the numbers")
# is that contract failing in the field: the renderer draws `words`, never
# the card `text`, so the omitted words - "10-man", "50-person", "hundred" -
# vanished from the captions entirely while the gates stayed green (coverage
# forgave them off the undrawn `text` field). An absence the viewer reads as
# missing numbers is not honest.
#
# The contract pinned here, both halves:
#
# 1. Every planned word is DRAWN - kept in the props with its clamped
#    (possibly collapsed) window, never omitted and never re-timed. Kept
#    words keep their measured frames exactly.
# 2. The sweep skips a collapsed window rather than accenting it - the word
#    renders in the base colour at full size and never takes the accent,
#    because no frame satisfies the sweep phase. No timing is invented.
# 3. The clamp says what it left unswept and why - naming the word, the
#    card and the reason - instead of turning a window into nothing
#    silently.
#
# Numbers below are frozen off the real artefacts: transcript segment 87
# (`pipeline_output/scratch/timeline_transcript/transcript.json`,
# 349.95-358.63) and the three placed props
# (`sub_speakertwo_7b1a6b77-..._633316-641996_{834c5d8d,7c1f3937,bf315839}`).
# Times are shifted onto small card bounds preserving the measured shape
# (six identical stamps, one overlap); the frames quoted are what the
# builder really emits at 24000/1001. No test reaches a real project.

STYLE = {
    "fontFamily": "Montserrat",
    "fontSize": 58,
    "fontWeight": 800,
    "position": "bottom",
    "safeArea": {"top": 120, "right": 90, "bottom": 324, "left": 90},
    "captionMaxWidth": 900,
}


def _entry_2(block, index, tl_start, tl_end, words, text=None):
    return {
        "id": f"sub_{block}_{index + 1:03d}",
        "card_index": index,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "text": text if text is not None else " ".join(w[0] for w in words),
        "emphasis_words": [],
        "spine_block_position": block,
        "speaker": "SpeakerTwo",
        "word_count": len(words),
        "words": [_word(w, s, e) for w, s, e in words],
    }


def _segment_87_plan():
    """The three Reel 05 cards, measured shape preserved.

    Card 1 ends on '10-man', whose 0.02s stamp overlaps its neighbours.
    Card 2 opens on the card the pile-up lands in. Card 3 holds four of
    the six identically-stamped words plus the healthy words around them.
    """
    pile = [("50-person", 5.89, 5.91), ("shop,", 5.89, 5.91),
            ("maybe", 5.89, 5.91), ("you're", 5.89, 5.91),
            ("a", 5.89, 5.91), ("hundred", 5.89, 5.91)]
    return {
        "subtitle_entries": [
            _entry_2(1, 1, 9.24, 11.13, [
                ("of", 9.24, 9.31), ("a", 9.31, 9.37),
                ("business,", 9.37, 9.94), ("you're", 9.94, 10.12),
                ("a", 10.12, 11.06), ("10-man", 11.11, 11.13),
            ]),
            _entry_2(1, 2, 11.13, 11.93, [
                ("shop,", 11.13, 11.39), ("you're", 11.39, 11.51),
                ("a", 11.51, 11.87),
            ] + pile[:2]),
            _entry_2(1, 3, 11.93, 14.06, pile[2:] + [
                ("person", 11.87, 12.40), ("shop,", 12.40, 14.06),
            ]),
        ],
        "style": STYLE,
    }


def _rendered_words(props):
    out = []
    for p in props:
        for sub in p["subtitles"]:
            for w in sub["words"]:
                out.append((w["word"], w["startFrame"], w["endFrame"]))
    return out


# ── Half 2: every planned word is drawn, the collapse is named ────

class TestPileUpWindowsAreDrawnAndNamed:
    def test_every_planned_word_is_drawn_and_the_collapse_named(
            self, capsys):
        """Reel 05 frame 241 (the numbers): no planned word is omitted;
        pile-up windows arrive collapsed (drawn unswept), never widened
        into invented timing; healthy words keep their measured frames;
        and every unswept word is named."""
        props = generate_subtitle_props_per_block(
            _segment_87_plan(), fps=FPS, width=904, height=480)
        rendered_words = _rendered_words(props)
        planned = [w["word"] for e in _segment_87_plan()["subtitle_entries"]
                   for w in e["words"]]
        assert [w for w, _, _ in rendered_words] == planned, (
            "a planned word never reaches the renderer")

        pile = {"10-man", "50-person", "shop,", "maybe", "you're", "a",
                "hundred"}
        collapsed = set()
        for word, start, end in rendered_words:
            if end <= start:
                assert word in pile, (
                    f"{word!r} renders {start} -> {end}: a collapsed "
                    f"window outside the measured pile-up")
                collapsed.add(word)
        assert {"10-man", "50-person", "maybe", "hundred"} <= collapsed

        # 'business,' spans the card interior: pure measurement.
        render_start = 9.24 - 0.5
        expect = (round((9.37 - render_start) * FPS),
                  round((9.94 - render_start) * FPS))
        assert ("business,", *expect) in set(rendered_words)

        err = capsys.readouterr().err
        for unswept in ("10-man", "50-person", "maybe", "hundred",
                        "you're", "shop,", "a"):
            assert unswept in err, f"unswept {unswept!r} is never named"
        assert "unswept" in err


class TestInvertedOverhangIsDrawnAndNamed:
    def test_word_ending_before_its_card_begins_is_drawn_unswept(
            self, capsys):
        # Reel 12's 'twenty' shape: the word ends before its own card
        # begins, so the card bound inverts it (startFrame 12 endFrame 6
        # in the real props). It must still be drawn (the card text
        # carries it), unswept, and the record must name it.
        plan = {
            "subtitle_entries": [_entry_2(2, 0, 11.43, 12.51, [
                ("twenty", 10.93, 11.16), ("percent.", 11.43, 11.64),
            ])],
            "style": STYLE,
        }
        props = generate_subtitle_props_per_block(
            plan, fps=FPS, width=904, height=480)
        rendered = {w: (s, e) for w, s, e in _rendered_words(props)}
        assert "twenty" in rendered
        assert "percent." in rendered
        start, end = rendered["twenty"]
        assert end <= start, (
            "the overhang must arrive collapsed (drawn unswept), never "
            "widened into invented timing")
        err = capsys.readouterr().err
        assert "twenty" in err


class TestHealthyCardsStaySilent:
    def test_no_unswept_no_record(self, capsys):
        plan = {
            "subtitle_entries": [_entry_2(0, 0, 0.0, 1.2, [
                ("what", 0.0, 0.3), ("kind", 0.35, 0.6),
                ("of", 0.65, 0.8), ("content", 0.85, 1.2),
            ])],
            "style": STYLE,
        }
        props = generate_subtitle_props_per_block(
            plan, fps=FPS, width=1080, height=1920)
        assert [w for w, _, _ in _rendered_words(props)] == [
            "what", "kind", "of", "content"]
        assert capsys.readouterr().err == ""


# ── Half 1: the sweep skips a collapsed window ────────────────────
#
# No JS runner exists in this repo, so the renderer side is covered
# structurally - the same shape as test_subtitle_emphasis.py: the guard
# that must be at the call site is asserted in source.

REPO_2 = pathlib.Path(__file__).resolve().parents[3]
WORD = (REPO_2 / "remotion-subtitles/src/compositions/SubtitleOverlay"
        / "AnimatedWord.tsx")


class TestRendererSkipsEmptyWindows:
    def test_collapsed_windows_draw_without_accent(self):
        source = WORD.read_text()
        # The accent phase must require a window with width: a
        # collapsed window never takes the accent, while the word
        # itself is still drawn (no `return null` for it - that
        # deleted Reel 05's numbers from the captions).
        guard = re.search(
            r"endFrame\s*>\s*startFrame\s*&&\s*frame\s* >= \s*startFrame"
            .replace(" ", r"\s*"),
            source)
        assert guard, (
            "AnimatedWord accents collapsed highlight windows or "
            "refuses to draw them: a word with endFrame <= startFrame "
            "must render in the base colour with no accent sweep "
            "(Reel 05 frame 241)")
        assert not re.search(
            r"if\s*\(\s*endFrame\s*<=\s*startFrame\s*\)\s*\{?"
            r"\s*return\s+null",
            source), (
            "AnimatedWord drops collapsed-window words instead of "
            "drawing them unswept: the renderer draws `words`, never "
            "the card `text`, so a null here deletes the word from "
            "the captions (Reel 05 frame 241: the numbers)")


# --------------------------------------------------------------------------
# From test_word_boundaries.py
#
# One clamp for both transcript paths, and the start it leaves alone.
#
# `library/tools/word_boundaries.py` serves `step_1_04_temporal_index`
# and `timeline_transcript.segments_for_speaker`. These pin that the two
# call sites are the SAME function, that a stretched word is cut back to
# its start plus the median while the start never moves, and that the
# existing unfitted-text counting reads identically before and after -
# the two must agree, not double-report the same rows.
#
# No ffmpeg, no models, no audio: every case is fixtures. This file
# carries no ffmpeg skip mark on purpose, so it runs in environments
# where `test_timeline_transcript.py` skips.

def _clip(source, src_in, src_out, tl_start, tl_end, uid="uid",
          speaker="SpeakerTwo"):
    return TimelineClip(
        resolve_item_id=uid, track_type="video", track_index=1,
        track_name=speaker, speaker=speaker, source_file=str(source),
        source_in=src_in, source_out=src_out,
        source_in_frame=round(src_in * FPS),
        source_out_frame=round(src_out * FPS),
        source_frames=None,
        timeline_start=tl_start, timeline_end=tl_end, name="clip")


def _word_2(text, start, end, **extra):
    return {"word": text, "start": start, "end": end, **extra}


# ── The shared implementation ──────────────────────────────────────


def test_a_stretched_word_is_cut_back_to_start_plus_median_or_0_3s():
    words = [_word_2("and", 100.00, 100.28),
             _word_2("then", 100.34, 100.61),
             _word_2("audits", 100.67, 134.80),
             _word_2("resume", 141.02, 141.40)]
    out = sanitize_word_boundaries(words)
    assert out is words
    # Median of the ordinary spans (0.28, 0.27, 0.38) is 0.28.
    assert out[2]["start"] == 100.67
    assert out[2]["end"] == 100.95
    # Everything else is untouched.
    assert (out[0]["start"], out[0]["end"]) == (100.00, 100.28)
    assert (out[3]["start"], out[3]["end"]) == (141.02, 141.40)
    # A lone stretched word has no median to read: the 0.3s fallback is
    # step 1.04's historical answer, kept so both paths agree.
    out = sanitize_word_boundaries([_word_2("well", 620.0, 628.0)])
    assert (out[0]["start"], out[0]["end"]) == (620.0, 620.3)


def test_degenerate_and_overlapping_ends_are_fixed_keeping_every_key():
    out = sanitize_word_boundaries([
        _word_2("a", 1.0, 1.0),
        _word_2("b", 1.1, 1.5),
        _word_2("c", 1.4, 1.8),
    ])
    assert out[0]["end"] == 1.02
    assert out[1]["end"] == 1.4
    # `timed` and the aligner score travel on the words; the clamp moves
    # ends, never membership.
    words = [_word_2("hi", 0.0, 9.0, timed=True, alignment_score=0.9),
             _word_2("there", 9.1, 9.4, timed=True, alignment_score=0.8)]
    out = sanitize_word_boundaries(words)
    assert [w["word"] for w in out] == ["hi", "there"]
    assert out[0]["timed"] is True
    assert out[0]["alignment_score"] == 0.9
    assert out[1]["end"] == 9.4


# ── The reels path, on field-test-shaped fixtures ──────────────────
#
# "audits": one word spanning 34.13s, its start 60ms after the previous
# word's end and on a real clip, its end 30s out in a clip gap. The
# start is trustworthy WITHOUT reading the word's own end - the gap
# before it (0.06s, an independent measurement off the previous word)
# sits in rhythm with the row's other gaps, and the clip list is
# independent of the aligner. Had the speech been at the tail, the gap
# before it would be the whole silence. So the end is cut and the start
# stays, exactly as the preflight does.

CLIPS = [_clip("/m/a.MXF", 1000.0, 1015.0, 90.0, 105.0, uid="A"),
         _clip("/m/b.MXF", 2000.0, 2010.0, 140.0, 150.0, uid="B")]

AUDITS_ROW = {
    "start": 99.0, "end": 141.0,
    "text": "and then audits resume",
    "words": [_word_2("and", 100.00, 100.28),
              _word_2("then", 100.34, 100.61),
              _word_2("audits", 100.67, 134.80),
              _word_2("resume", 141.02, 141.40)],
}


def test_the_clamp_rebinds_a_stretched_row_and_invents_no_binding():
    """Before the fix this row split into three runs - A, unbound, B -
    because the stretched midpoint sat in the gap. Now the clamp puts
    the midpoint back on the clip the speech came from."""
    out = tt.segments_for_speaker({"segments": [AUDITS_ROW]}, "SpeakerTwo",
                                  CLIPS)
    assert [s.resolve_item_id for s in out] == ["A", "B"]
    assert [s.text for s in out] == ["and then audits", "resume"]
    assert all(s.read_from_words for s in out)
    # Clamping must not invent a binding: "well" in the gap at 120s, cut
    # back to 0.3s, still sits in the gap and still binds to None.
    row = {"start": 119.0, "end": 129.0, "text": "well",
           "words": [_word_2("well", 120.0, 128.0)]}
    out = tt.segments_for_speaker({"segments": [row]}, "SpeakerTwo", CLIPS)
    assert len(out) == 1
    assert out[0].resolve_item_id is None
    assert out[0].words[0]["start"] == 120.0
    assert out[0].words[0]["end"] == 120.3


# ── Agreement with the unfitted-text counting ──────────────────────
#
# `transcript_document` counts rows whose text outruns their timings
# (`transcript_fit.row_fit`: timed words against text words). The clamp
# moves ends and never membership, so those counts must read
# identically on the stretched and the clamped document - agreement,
# not a second report on the same rows.


class _Snap:
    project_name, timeline_name = "P", "T"
    fps, duration = 23.976, 700.0
    clips = ()

    def speakers(self):
        return ["SpeakerTwo"]


def _document(words_per_row):
    """One document per word list, same texts, same spans."""
    segments = []
    for words in words_per_row:
        segments.append(tt.SpokenSegment(
            speaker="SpeakerTwo", text=" ".join(w["word"] for w in words),
            timeline_start=100.0, timeline_end=142.0,
            source_file="/m/a.MXF", source_start=1000.0,
            source_end=1042.0, resolve_item_id=None,
            words=tuple(words)))
    return tt.transcript_document(_Snap(), segments)


def test_clamping_changes_no_unfitted_text_count():
    stretched = [[_word_2("and", 100.00, 100.28),
                  _word_2("audits", 100.67, 134.80)],
                 [_word_2("well", 120.0, 128.0)]]
    clamped = [[dict(w) for w in row] for row in stretched]
    for row in clamped:
        sanitize_word_boundaries(row)
    before = transcript_fit.scan(_document(stretched))
    after = transcript_fit.scan(_document(clamped))
    assert before["unfitted_rows"] == after["unfitted_rows"] == 0
    assert before["words_with_no_timing"] == after["words_with_no_timing"] == 0
    assert before["rows"] == after["rows"] == 2

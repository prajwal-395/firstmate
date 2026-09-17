"""Caption segments follow the karaoke card grouping, not transcript rows.

Option (b) the captain chose 2026-09-17: a timeline caption clip holds
the cards the craft put together, and the craft's finest unit is one
card. Step 4.05 groups plan entries by `(spine_block_position,
card_index)` - one rendered segment per karaoke card - instead of one
per transcript row (spine block). The card boundaries stay step 4.01's
`split_into_groups` partition (max words, word gap, measured pixel
fit): the segmenter reads them rather than re-deciding them, so there
is no second rule set to drift.

The failure mode this file exists to catch is karaoke discontinuity
across a segment join: a passing build that silently eats a word at
every join is not visible in a clip count. Every test below is written
so that reverting the behaviour it guards turns it red; the comment on
each says which mutation.
"""

import pytest

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
    generate_subtitle_props_per_block,
)


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
        "speaker": "Craig",
        "word_count": len(words),
        "words": [_word(w, s, e) for w, s, e in words],
    }


def _plan():
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
    plan = _plan()
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


def test_entries_without_card_index_keep_the_old_per_block_shape():
    """Backward compatibility: a stored plan written before step 4.01
    emitted `card_index` still renders exactly as it did - one segment
    holding the block's cards. Reverts red if the fallback groups such
    entries per card (or drops them)."""
    entries = [dict(e) for e in _plan()["subtitle_entries"]]
    for e in entries:
        del e["card_index"]
    props = _props(entries)
    assert len(props) == 2
    assert [len(p["subtitles"]) for p in props] == [3, 1]


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
    entries = _plan()["subtitle_entries"]
    props = _props()
    assert _rendered_frames(props) == _expected_frames(
        entries, 24000 / 1001)


def test_no_card_displays_past_its_segment():
    """The re-expressed `display_until` invariant: a segment's edges are
    its card's display interval, so a card cannot display past the
    segment that carries it. Reverts red if segment bounds ever stop
    being read off the cards (e.g. back to block bounds while cards
    hold per-card timings)."""
    entries = _plan()["subtitle_entries"]
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
    entries = _plan()["subtitle_entries"]
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


def test_segments_tile_without_overlap():
    """One track, one card at a time: neighbouring segments may abut
    but never overlap beyond float dust (the same epsilon step 4.01
    resolves overlaps to)."""
    props = _props()
    spans = sorted((p["_timeline_start"], p["_timeline_end"]) for p in props)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start >= end - 0.001 - 1e-9, (
            f"segments overlap: one ends at {end}, the next starts at "
            f"{start}")


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
        "style": _plan()["style"],
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


def test_trimmed_join_keeps_every_word_bounded_to_its_card():
    """The join that already exists on Reel 26: no word dropped or
    duplicated across it (same text sequence, each exactly once),
    onsets never move, and no word displays past its segment. Reverts
    red if the renderer drops a word (sequence differs), shifts an
    onset (start differs), duplicates (sequence longer), or stops
    bounding words to the card (end past the edge). The overhanging
    'it.' tail is cut at the card edge - the craft's own trim, which
    the earlier card yielded; the card text still shows the word, only
    its highlight cannot outlive the card."""
    plan = _trimmed_join_plan()
    entries = plan["subtitle_entries"]
    fps = 24000 / 1001
    props = generate_subtitle_props_per_block(
        plan, fps=fps, width=1080, height=1920)
    assert len(props) == 2
    assert _rendered_frames(props) == _card_bounded_frames(entries, fps)
    assert [w for w, _, _ in _rendered_frames(props)] == [
        w["word"] for e in entries for w in e["words"]]
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


def test_plan_emits_a_dense_card_index_per_block():
    """Step 4.01 names each card's ordinal in its block, dense from
    zero, aligned with the block-local id counter - so the index step
    4.05 groups by cannot point at a different card than the id names.
    A block of nine words needs at least two cards (six-word cap), and
    this one plans exactly two; reverts red if the index is missing,
    gappy, or counted across blocks."""
    spine = {"structure": [
        _spine_block(0, 0.0, 6.0,
                     ["one", "two", "three", "four", "five", "six",
                      "seven", "eight", "nine"]),
        _spine_block(1, 6.0, 3.0, ["ten", "eleven", "twelve"]),
    ]}
    entries = generate_subtitles(spine)["subtitle_plan"]["subtitle_entries"]
    by_block = {}
    for e in entries:
        by_block.setdefault(e["spine_block_position"], []).append(e)
    assert len(by_block[0]) == 2, (
        f"nine words should plan two cards, got {len(by_block[0])}")
    assert [e["card_index"] for e in by_block[0]] == [0, 1]
    assert [e["card_index"] for e in by_block[1]] == [0]
    assert [e["id"] for e in by_block[0]] == ["sub_0_001", "sub_0_002"]


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

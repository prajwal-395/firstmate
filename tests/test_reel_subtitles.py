"""Captions on a reel: reel time, and the speaker's own style.

The captain opened sixteen finished timelines and found no subtitles on
any of them. Two rules exist here so that cannot recur quietly.
"""

from __future__ import annotations

import pytest

from library.tools.reel_build import reel_time
from library.tools.reel_subtitles import caption_groups, reel_captions

AK = {"accentColor": "#FFB8D4", "fontFamily": "Montserrat", "fontSize": 58}
CR = {"accentColor": "#FBF0B8", "fontFamily": "Montserrat", "fontSize": 58}
STYLES = {"Akshita": AK, "Craig": CR}


def _words(text, start, step=0.4):
    return [{"word": w, "start": start + i * step, "end": start + (i + 1) * step}
            for i, w in enumerate(text.split())]


def _seg(speaker, text, start, uid="u"):
    words = _words(text, start)
    return {"speaker": speaker, "text": text, "resolve_item_id": uid,
            "timeline_start": start, "timeline_end": words[-1]["end"],
            "source_file": "/m/a.MXF", "source_start": start,
            "source_end": words[-1]["end"], "words": words}


# ── Reel time, not master time ───────────────────────────────────────

def test_a_caption_before_a_cut_keeps_its_time():
    assert reel_time(5.0, [(0.0, 20.0), (25.0, 60.0)]) == 5.0


def test_a_caption_after_a_cut_moves_up_by_the_cut_length():
    """Would have FAILED with master timings: 30s sits 5s later than it
    should once a 5s take is removed before it."""
    assert reel_time(30.0, [(0.0, 20.0), (25.0, 60.0)]) == pytest.approx(25.0)


def test_a_caption_inside_a_cut_disappears_with_it():
    """A dropped bad take takes its captions with it."""
    assert reel_time(22.0, [(0.0, 20.0), (25.0, 60.0)]) is None


def test_captions_are_emitted_in_reel_time(tmp_path):
    tx = {"segments": [_seg("Akshita", "one two three four five six", 30.0)]}
    caps = reel_captions(tx, [(0.0, 20.0), (25.0, 60.0)], STYLES, 24.0, 1080, 1920)
    assert caps
    assert caps[0]["reel_start"] < 30.0, "master time would be 30.0 or later"


def test_a_caption_whose_speech_was_cut_is_not_emitted():
    tx = {"segments": [_seg("Akshita", "one two three four five six", 21.0)]}
    assert reel_captions(tx, [(0.0, 20.0), (25.0, 60.0)], STYLES,
                         24.0, 1080, 1920) == []


# ── The speaker's own style ──────────────────────────────────────────

def test_each_speaker_gets_their_own_accent():
    """The styling IS the diarization signal - the captain's words."""
    tx = {"segments": [_seg("Akshita", "one two three four", 1.0),
                       _seg("Craig", "five six seven eight", 10.0, "u2")]}
    caps = reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)
    accents = {c["speaker"]: c["props"]["style"]["accentColor"] for c in caps}
    assert accents == {"Akshita": "#FFB8D4", "Craig": "#FBF0B8"}


def test_the_engine_supplies_no_style_of_its_own():
    """AGENTS.md 10.5 - an unknown speaker gets nothing invented."""
    tx = {"segments": [_seg("Nobody", "one two three four", 1.0)]}
    caps = reel_captions(tx, [(0.0, 60.0)], {}, 24.0, 1080, 1920)
    assert caps and caps[0]["props"]["style"] == {}


# ── Grouping, the captain's script's way ─────────────────────────────

def test_a_card_holds_at_most_six_words():
    groups = caption_groups(_words("one two three four five six seven eight", 0.0))
    assert all(len(g["words"]) <= 6 for g in groups)


def test_a_sentence_end_breaks_the_card():
    groups = caption_groups(_words("this is done. next one starts", 0.0))
    assert groups[0]["text"].endswith("done.")


def test_a_long_silence_breaks_the_card():
    words = _words("one two three", 0.0) + _words("four five six", 30.0)
    assert len(caption_groups(words)) >= 2


def test_every_caption_carries_word_timings():
    tx = {"segments": [_seg("Akshita", "one two three four", 1.0)]}
    caps = reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)
    words = caps[0]["props"]["subtitles"][0]["words"]
    assert words and all("startFrame" in w and "endFrame" in w for w in words)


def test_a_reel_with_speech_always_produces_captions():
    """The defect in one line: sixteen reels shipped with none."""
    tx = {"segments": [_seg("Akshita", "one two three four five", 1.0)]}
    assert reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)


# ── Placing onto a NAMED timeline ────────────────────────────────────

def test_placing_requires_making_the_timeline_current():
    """`MediaPool.AppendToTimeline` appends to the project's CURRENT
    timeline; a timeline handle is not a destination.

    Measured 2026-09-04: all 579 captions for sixteen reels were appended
    while Reel 01 was current, so fifteen reels got an empty V3 while
    every call returned True. AddTrack and SetTrackName DO act on the
    handle, which is exactly what makes the mistake look impossible.
    """
    from library.tools.reel_subtitles import PLACEMENT_REQUIRES_CURRENT
    assert "SetCurrentTimeline" in PLACEMENT_REQUIRES_CURRENT
    assert "current" in PLACEMENT_REQUIRES_CURRENT.lower()


def test_the_placement_rule_names_why_it_looks_safe():
    from library.tools.reel_subtitles import PLACEMENT_REQUIRES_CURRENT
    assert "AddTrack" in PLACEMENT_REQUIRES_CURRENT

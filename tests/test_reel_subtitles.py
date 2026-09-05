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


# ── F6: Deconfliction ───────────────────────────────────────────────

def test_bleed_pair_keeps_the_primary_mic_speaker():
    """Mic bleed means one mic picks up the other speaker's words.  The
    primary mic (= the speaker who actually spoke) starts earlier because
    the sound reaches its own mic first.  The surviving card must carry
    THAT speaker so per-speaker subtitle styling is correct.

    Reel 09 example: craig_18-71 vs akshita_18-75 on identical words.
    If Craig spoke, Craig's mic gets it at 18.0s and Akshita's mic gets
    bleed at 18.05s.  Craig's card must survive.
    """
    tx = {"segments": [
        # Craig's mic picks up the speech first (primary)
        _seg("Craig", "one two three four", 18.0, "craig_18"),
        # Akshita's mic picks up bleed slightly later
        _seg("Akshita", "one two three four", 18.05, "akshita_18"),
    ]}
    caps = reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)
    # Only one card should survive (the bleed pair collapses)
    speakers = [c["speaker"] for c in caps]
    assert "Craig" in speakers, "Craig's card (primary mic) must survive"
    # The bleed card (Akshita) must NOT survive with this text
    craig_cards = [c for c in caps if c["speaker"] == "Craig"]
    akshita_bleed = [c for c in caps if c["speaker"] == "Akshita"
                     and "one two three four" in c["text"]]
    assert len(craig_cards) >= 1
    assert len(akshita_bleed) == 0, "Akshita's bleed card must be dropped"


def test_a_short_card_is_extended_not_dropped():
    """Dropping a card under 0.5s re-creates uncaptioned speech - the
    very defect F5 fixed.  Short cards must be extended to the minimum."""
    # Create a segment with a very short word
    tx = {"segments": [{
        "speaker": "Akshita", "text": "yes",
        "resolve_item_id": "u", "source_file": "/m/a.MXF",
        "timeline_start": 1.0, "timeline_end": 1.1,
        "source_start": 1.0, "source_end": 1.1,
        "words": [{"word": "yes", "start": 1.0, "end": 1.1}],
    }]}
    caps = reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)
    assert len(caps) == 1, "Short card must not be dropped"
    assert caps[0]["frames"] >= 12, "Card must be extended to >= 0.5s at 24fps"


def test_a_trimmed_interruption_is_kept_not_dropped():
    """When an interruption is trimmed to avoid overlap, the resulting
    card must be extended to the minimum duration, never dropped."""
    tx = {"segments": [
        _seg("Craig", "first three words here", 1.0, "u1"),
        # Akshita interrupts with different text, overlapping slightly
        _seg("Akshita", "different words entirely now", 2.4, "u2"),
    ]}
    caps = reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)
    speakers = [c["speaker"] for c in caps]
    assert "Craig" in speakers, "Craig's card survives"
    assert "Akshita" in speakers, (
        "Akshita's interruption must survive after trimming, not be dropped"
    )
    # No overlaps in the result
    for i in range(len(caps) - 1):
        assert caps[i]["reel_end"] <= caps[i + 1]["reel_start"], (
            f"Cards {i} and {i+1} overlap after deconfliction"
        )


def test_three_way_overlap_resolves_completely():
    """Three overlapping cards must all be resolved, not just the last
    pair.  The old code only compared against deconflicted[-1], so
    cards exposed by a preceding resolution were missed."""
    tx = {"segments": [
        _seg("Craig", "alpha beta gamma delta", 1.0, "u1"),
        _seg("Akshita", "epsilon zeta eta theta", 1.2, "u2"),
        _seg("Craig", "iota kappa lambda mu", 1.4, "u3"),
    ]}
    caps = reel_captions(tx, [(0.0, 60.0)], STYLES, 24.0, 1080, 1920)
    # All three cards must survive (different text = interruptions)
    assert len(caps) == 3, f"Expected 3 cards after deconfliction, got {len(caps)}"
    # No overlaps
    for i in range(len(caps) - 1):
        assert caps[i]["reel_end"] <= caps[i + 1]["reel_start"], (
            f"Cards {i} and {i+1} still overlap: "
            f"{caps[i]['reel_end']} > {caps[i+1]['reel_start']}"
        )


# ── A closing CTA range from elsewhere in the episode ────────────────
#
# The last keep range on a reel is its closer, and it may come from
# EARLIER on the master than the body it closes. The old segment filter
# read the range list as one window - `ranges[0][0]` to `ranges[-1][1]` -
# which inverts in that case (start > end) and silently drops every
# caption on the reel.


def _cta_ranges():
    """Body at 600-660s, closer at 468-476s: the closer comes FIRST on
    the master and LAST on the reel."""
    return [(600.0, 660.0), (468.0, 476.0)]


def _cta_transcript():
    return {"segments": [
        _seg("Akshita", "the whole retrieval path changes for them", 600.0),
        _seg("Craig", "jump on lucycontent dot com and dm us", 468.0),
    ]}


def test_a_caption_in_a_distant_closer_still_reaches_the_reel():
    """Would have FAILED before the fix: with the closer earlier on the
    master than the body, the envelope test dropped EVERY caption."""
    caps = reel_captions(_cta_transcript(), _cta_ranges(), STYLES,
                         fps=24000 / 1001, width=1080, height=1920)
    assert caps, "a reel with a distant closer must still be captioned"
    assert any("lucycontent" in c["text"] for c in caps)
    assert any("retrieval" in c["text"] for c in caps)


def test_the_closers_captions_are_timed_after_the_bodys():
    """The closer plays last on the reel, so its cards do too - even
    though its master seconds come first."""
    caps = reel_captions(_cta_transcript(), _cta_ranges(), STYLES,
                         fps=24000 / 1001, width=1080, height=1920)
    body_end = max(c["reel_end"] for c in caps if "retrieval" in c["text"])
    cta_start = min(c["reel_start"] for c in caps
                    if "lucycontent" in c["text"])
    assert cta_start >= body_end - 0.001


def test_a_caption_card_never_spans_the_closers_seam():
    """The body's last words and the closer's first ones are far apart on
    the master, so a card grouped across that seam would read as a
    sentence nobody said."""
    ranges = _cta_ranges()
    seam = 60.0
    caps = reel_captions(_cta_transcript(), ranges, STYLES,
                         fps=24000 / 1001, width=1080, height=1920,
                         closer_seam=seam)
    for cap in caps:
        assert not (cap["reel_start"] < seam - 0.001
                    and cap["reel_end"] > seam + 0.001), (
            f"card {cap['text']!r} spans the closer's seam at {seam:.2f}s")


def test_a_bad_take_seam_is_not_a_flush_point():
    """Would have FAILED before the fix: seams were taken from EVERY
    range, so a bad-take cut on a reel with no closer regrouped its
    cards. A cut joins speech the editor made contiguous on purpose."""
    from library.tools.reel_build import closer_seam

    tx = {"segments": [_seg("Akshita",
                            "we talked about the thing and then we moved on",
                            0.0)]}
    # The seam must land exactly on a word boundary - that is where a
    # flush is decided, and where the old every-seam rule regrouped.
    ranges = [(0.0, 2.8), (2.8, 20.0)]
    whole = reel_captions(tx, [(0.0, 20.0)], STYLES, fps=24000 / 1001,
                          width=1080, height=1920)
    cut = reel_captions(tx, ranges, STYLES, fps=24000 / 1001,
                        width=1080, height=1920, closer_seam=None)
    assert [c["text"] for c in cut] == [c["text"] for c in whole]

    class NoCloser:
        timeline_start, timeline_end = 0.0, 20.0
        call_to_action = None

    assert closer_seam(NoCloser(), ranges) is None


def test_the_closers_seam_is_the_bodys_kept_length():
    from library.tools.reel_build import closer_seam

    class WithCloser:
        timeline_start, timeline_end = 600.0, 660.0

        class call_to_action:
            timeline_start, timeline_end = 468.0, 476.0

    assert closer_seam(WithCloser(), _cta_ranges()) == 60.0
    assert closer_seam(WithCloser(),
                       [(600.0, 620.0), (625.0, 660.0),
                        (468.0, 476.0)]) == 55.0


def test_the_closing_word_of_the_closer_is_captioned():
    """A range end lands EXACTLY on a segment's last word's end, so read
    the half-open way that word falls outside every range and vanishes -
    on the closing line of every reel that has a closer."""
    tx = _cta_transcript()
    closer_end = tx["segments"][1]["timeline_end"]
    ranges = [(600.0, 660.0), (468.0, closer_end)]
    caps = reel_captions(tx, ranges, STYLES, fps=24000 / 1001,
                         width=1080, height=1920, closer_seam=60.0)
    said = " ".join(c["text"] for c in caps)
    assert said.strip().endswith("us"), (
        f"the closer's last word is missing from {said!r}")


def test_a_speech_segment_in_the_dead_master_time_is_not_captioned():
    """The reel never plays the seconds between the body and the closer,
    so nothing said there reaches it."""
    transcript = _cta_transcript()
    transcript["segments"].append(
        _seg("Craig", "this is in neither range at all", 520.0, uid="u3"))
    caps = reel_captions(transcript, _cta_ranges(), STYLES,
                         fps=24000 / 1001, width=1080, height=1920)
    assert not any("neither range" in c["text"] for c in caps)

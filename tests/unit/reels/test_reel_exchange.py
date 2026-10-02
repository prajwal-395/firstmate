"""A reel is a conversational EXCHANGE, measured and never scored.

The first batch of ten was rejected as "mostly just a single person
yapping and not really a convo". Nine of the ten had one speaker, and the
`speakers` field said so on every one - so the test that matters most
here is that a single-speaker window cannot become a candidate at all.

The captain's worked example, 0:00-3:13, is the fixture the numbers are
checked against: it must yield exactly TWO conversations, because they
described it as "a reel or two".
"""

from __future__ import annotations

import pytest

from library.tools.reel_exchange import (
    MAX_REEL_SECONDS,
    MIN_REEL_SECONDS,
    POSSIBLE_RETAKE,
    SAME_EXCHANGE,
    Exchange,
    Turn,
    collapse_overlapping,
    collapse_retakes,
    containment,
    exchange_windows,
    funnel,
    turns_from_transcript,
)


def _seg(speaker, text, start, end, uid="uid-1"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end,
            "resolve_item_id": uid}


def _turn(speaker, start, end, text="some words here about the topic"):
    return Turn(speaker=speaker, start=start, end=end, text=text)


# ── Turns ────────────────────────────────────────────────────────────

def test_consecutive_segments_from_one_speaker_are_one_turn():
    tx = {"segments": [_seg("Craig", "first part", 0.0, 4.0),
                       _seg("Craig", "second part", 4.2, 8.0, "uid-2"),
                       _seg("Akshita", "the answer", 9.0, 15.0, "uid-3")]}
    turns = turns_from_transcript(tx)
    assert [t.speaker for t in turns] == ["Craig", "Akshita"]
    assert turns[0].text == "first part second part"
    assert turns[0].duration == pytest.approx(8.0)


def test_a_long_pause_starts_a_new_turn():
    tx = {"segments": [_seg("Craig", "before", 0.0, 4.0),
                       _seg("Craig", "after", 20.0, 24.0, "uid-2")]}
    assert len(turns_from_transcript(tx)) == 2


def test_a_straddling_segment_is_not_a_turn():
    """Its text is not reliably in the cut at all."""
    tx = {"segments": [_seg("Craig", "real", 0.0, 4.0),
                       dict(_seg("Craig", "bridged", 20.0, 40.0),
                            resolve_item_id=None)]}
    assert len(turns_from_transcript(tx)) == 1


# ── The defect that killed batch one ─────────────────────────────────

def test_a_single_speaker_window_is_never_a_candidate():
    """Nine of the first ten proposals were one speaker. This is the
    check that makes that impossible rather than merely discouraged."""
    turns = [_turn("Akshita", 0.0, 30.0), _turn("Akshita", 31.0, 60.0)]
    assert exchange_windows(turns, "Craig", "Akshita") == []


# ── The captain's length brief ───────────────────────────────────────

def test_length_is_guidance_and_a_long_story_is_still_reported():
    """Was a HARD window until 2026-09-04, which silently withheld every
    stretch needing longer to finish. A story that runs 95s because that
    is how long it takes to close is a real answer."""
    turns = [_turn("Craig", 0.0, 10.0), _turn("Akshita", 11.0, 100.0)]  # 100s
    windows = exchange_windows(turns, "Craig", "Akshita")
    assert windows, "a 100s exchange must be offered, not withheld"
    assert windows[0].measurements()["within_length_guidance"] is False


# ── Concerns are raised, never enforced ──────────────────────────────

def test_a_closing_pitch_is_measured_and_never_a_concern():
    """The captain's unit is "an atomic segment of conversation that
    provides value and then makes a little CTA at the end". Treating the
    pitch as a defect discarded ten candidates and removed the ending the
    format is built on."""
    turns = [_turn("Craig", 0.0, 20.0, "so why does that matter to a business"),
             _turn("Akshita", 21.0, 40.0, "because AI cannot describe you"),
             _turn("Craig", 41.0, 55.0,
                   "go check out the lucy visibility score on our website")]
    window = exchange_windows(turns, "Craig", "Akshita")[0]
    assert not any("pitch" in c for c in window.concerns), (
        "a closing CTA is the ending, not a defect")
    body = window.measurements()
    assert body["pitch_share"] > 0, "it is REPORTED"
    assert body["closes_on_pitch"] is True
    # Nothing is scored: which conversation matters is taste (AGENTS.md 10.5).
    assert "score" not in body and "rank" not in body


def test_one_sided_is_the_conjunction_of_low_share_and_speaking_once():
    """1:52-2:59 is one of the captain's own good examples and Craig
    holds only 9% of it - because he contributes twice, a real question
    and a real reaction. Alternation count alone discriminates nothing,
    so the concern is the CONJUNCTION."""
    once = [_turn("Craig", 0.0, 4.0, "what about that"),
            _turn("Akshita", 5.0, 60.0, "a very long answer indeed " * 5)]
    windows = exchange_windows(once, "Craig", "Akshita")
    assert any("speaks once" in c for c in windows[0].concerns)

    returns = [_turn("Craig", 0.0, 3.0, "so give me an example of that"),
               _turn("Akshita", 4.0, 35.0, "the audit example " * 6),
               _turn("Craig", 36.0, 41.0,
                     "they used to call that keyword stuffing"),
               _turn("Akshita", 42.0, 60.0, "and it works against you " * 4)]
    windows = exchange_windows(returns, "Craig", "Akshita")
    assert windows[0].share_for("Craig") < 0.25
    assert windows[0].alternations >= 3
    assert not any("speaks once" in c for c in windows[0].concerns)


# ── Same stretch versus recorded twice ───────────────────────────────

def test_a_conversation_recorded_twice_collapses_and_two_different_do_not():
    words = "seo convinces an algorithm to rank pages geo makes ai comprehend"
    a = Exchange(0.0, 55.0, (_turn("Craig", 0.0, 20.0, words),
                             _turn("Akshita", 21.0, 55.0, words)))
    b = Exchange(60.0, 118.0, (_turn("Craig", 60.0, 80.0, words),
                               _turn("Akshita", 81.0, 118.0, words)))
    groups = collapse_retakes([a, b])
    assert len(groups) == 1
    assert groups[0][1].retake_band == "same"

    c = Exchange(0.0, 55.0, (_turn("Craig", 0.0, 20.0, "seo algorithm ranking pages google"),
                             _turn("Akshita", 21.0, 55.0, "position versus comprehension")))
    d = Exchange(60.0, 118.0, (_turn("Craig", 60.0, 80.0, "competitors nine times visibility report"),
                               _turn("Akshita", 81.0, 118.0, "modules citations directories")))
    assert len(collapse_retakes([c, d])) == 2


def test_containment_not_jaccard():
    """Two takes of one exchange differ in LENGTH - the second is usually
    more complete - and Jaccard punishes exactly that."""
    short = Exchange(0.0, 50.0, (_turn("Craig", 0.0, 25.0, "algorithm ranking pages"),
                                 _turn("Akshita", 26.0, 50.0, "comprehension")))
    long = Exchange(60.0, 120.0, (
        _turn("Craig", 60.0, 90.0, "algorithm ranking pages"),
        _turn("Akshita", 91.0, 120.0,
              "comprehension plus directories citations linkedin reddit press")))
    assert containment(short, long) == 1.0


# ── The funnel reports what LEFT ─────────────────────────────────────


def test_a_flagged_window_is_reported_not_deleted():
    turns = [_turn("Craig", 0.0, 20.0,
                   "the lucy visibility score go to our website check it out"),
             _turn("Akshita", 21.0, 50.0)]
    report = funnel(turns, "Craig", "Akshita")
    assert report["flagged"], "a dropped window must still be reported"
    assert report["flagged"][0].concerns


def test_overlap_groups_do_not_chain():
    """A overlaps B and B overlaps C does NOT put C in A's group. The
    head is the only window that reaches the candidate table, so a chain
    deletes everything it swallowed: measured on the field test, five
    windows spanning 248s collapsed to one 50s representative and the
    candidate covering the captain's approved reel 03 left the table."""
    a = Exchange(0.0, 50.0, (_turn("Craig", 0.0, 20.0),
                             _turn("Akshita", 21.0, 50.0)))
    b = Exchange(40.0, 90.0, (_turn("Craig", 40.0, 60.0),
                              _turn("Akshita", 61.0, 90.0)))
    c = Exchange(80.0, 130.0, (_turn("Craig", 80.0, 100.0),
                               _turn("Akshita", 101.0, 130.0)))
    groups = collapse_overlapping([a, b, c])
    # A and B overlap: one stretch, not two takes. C is its own group.
    assert [(g[0].start, g[0].end) for g in groups] == [(0.0, 50.0), (80.0, 130.0)]



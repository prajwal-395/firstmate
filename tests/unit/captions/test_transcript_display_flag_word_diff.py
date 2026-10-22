"""A word the transcript says NOT to draw is not a dropped word.

The defect this names
---------------------
The reel-13 build on the Ren-owned scratch copy (2026-10-21) failed
the conformance gate with F25: *"caption ... drops 1 played word(s)
near reel 69.30s: A"*.

The word was the transcript's own: the master's transcript carries the
first half of a mis-split "AI" as a row of its own, marked
`display: False` - the transcript pass had already decided that token
is not drawn. The spine drops such a row before the caption planner
ever sees it (`reel_spine`), so the caption that omits it OBEYS the
record. The coverage diff compared the played words against the drawn
words without knowing about the flag, and read that obedience as a
dropped word - a gate failing correct output (AGENTS.md 10.4).

The fix is in the DIFF, not in the caption: the transcript's own
display decision travels with the word, and a word carrying it is
suppressed - skipped by the identity diff and by the coverage leg,
and REPORTED as a warning naming the word, never silently.

What this file proves
---------------------
- a played word the transcript marks `display: False` is suppressed;
- the identity diff reports no dropped word for it;
- the suppression is REPORTED, not silent.
"""
from __future__ import annotations

from library.tools import subtitle_coverage as coverage
from library.tools import reel_conformance_verifier as verifier


def _segment():
    """One segment whose second word the transcript hides."""
    return {
        "speaker": "Akshita",
        "source_file": "/master/LC4932.MXF",
        "source_start": 100.0,
        "timeline_start": 100.0,
        "timeline_end": 101.0,
        "text": "whoever A understands",
        "words": [
            {"word": "whoever", "start": 100.0, "end": 100.3,
             "timed": True},
            {"word": "A", "start": 100.4, "end": 100.44, "timed": True,
             "display": False},
            {"word": "understands", "start": 100.5, "end": 100.9,
             "timed": True},
        ],
    }


def _played():
    spans = [{"source_file": "/master/LC4932.MXF",
              "source_start": 99.0, "source_end": 102.0,
              "reel_start": 0.0}]
    return coverage.played_words_from_transcript([_segment()], spans)["words"]


def test_the_transcripts_own_display_flag_travels_with_the_played_word():
    """The flag is the transcript's own decision and must reach the diff."""
    played = _played()

    hidden = [w for w in played if w["word"] == "A"]
    assert hidden, "the hidden word is a played word"
    assert hidden[0]["display"] is False


def test_a_word_the_transcript_hides_is_suppressed_not_dropped():
    """THE DEFECT.  The caption obeys the record; the diff called that
    obedience a dropped word and failed the reel."""
    played = _played()
    read = coverage.read_words_for_comparison(played, None)
    suppressed = verifier._suppressed_played_words(read, "")

    assert [w["word"] for w in suppressed] == ["A"], (
        "the transcript's own mark is the first half of the suppression "
        "contract, and it needs no project folder to be read")

    captioned = [
        {"word": "whoever", "norm": "whoever", "card": "card-1",
         "reel_start": 0.0, "reel_end": 0.3},
        {"word": "understands", "norm": "understands", "card": "card-1",
         "reel_start": 0.5, "reel_end": 0.9},
    ]
    cards = [{"card": "card-1", "reel_start": 0.0, "reel_end": 0.9,
              "text_norms": ["whoever", "understands"]}]
    result = coverage.check_word_coverage(
        read, captioned, cards, suppressed=suppressed)

    kinds = [f["kind"] for f in result["findings"]]
    assert "word_mismatch" not in kinds, (
        "a word the transcript says not to draw is not a dropped word")
    assert "suppressed" in kinds, (
        "the skip is reported, never silent")
    suppressed_finding = next(f for f in result["findings"]
                              if f["kind"] == "suppressed")
    assert suppressed_finding["severity"] == "warning"
    assert "A" in suppressed_finding["message"]

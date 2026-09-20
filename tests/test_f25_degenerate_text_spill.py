"""F25 forgives a pile-up-timed word the adjacent card draws but cannot time.

Reel 27 (2026-09-20): the transcript stamps 'five-star' at 20ms
inside 'reviews,' (aligner pile-up - `degenerate_indices` flags both,
neither position trustworthy). The placed card reads "5-star reviews,
AI does see that." - the viewer reads the word while it is spoken -
but its karaoke words start at "reviews,": nothing honestly timeable
exists for the 20ms stamp, so the card starts one frame past it and
the coverage leg refused the reel with `played_not_captioned ... :
5-star`. The gate failed a caption the viewer can read (AGENTS.md
10.4): a degenerate-timing played word within edge spill of a card
whose RENDERED TEXT carries it is timing dust, forgiven with a
warning naming the word and the card, never silence. Cleanly timed
words keep the strict timed-norm rule - the Reel 29 "If" precedent
(one frame past the card, absent from it) still errors.

`library/tools/subtitle_coverage.py` (`check_word_coverage`);
wired as F25 in `library/tools/reel_conformance_verifier.py`.
"""

from library.tools import subtitle_coverage as sc

CARD_A = "sub_akshita_leaving_all.mov"
CARD_B = "sub_akshita_5star_reviews.mov"


def _w(word, start, end, card, degenerate=False):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card,
            "degenerate": degenerate}


def _played(degenerate=True):
    # Reel seconds off the failed Reel 27 staging: "all" through
    # "reviews,"; '5-star' stamped 18.76-18.78 (20ms, pile-up).
    return [
        _w("all", 18.60, 18.71, CARD_A),
        _w("5-star", 18.76, 18.78, CARD_A, degenerate=degenerate),
        _w("reviews,", 18.71, 19.22, CARD_B),
        _w("AI", 19.25, 19.40, CARD_B),
    ]


def _captioned():
    # What the renderer timed: card A through "all", card B from
    # "reviews," - "5-star" drawn, never timed.
    return [
        _w("all", 18.60, 18.71, CARD_A),
        _w("reviews,", 18.81, 19.22, CARD_B),
        _w("AI", 19.25, 19.40, CARD_B),
    ]


def _cards():
    return [
        {"card": CARD_A, "reel_start": 17.14, "reel_end": 18.73},
        {"card": CARD_B, "reel_start": 18.81, "reel_end": 20.55,
         "text_norms": ["5-star", "reviews", "ai", "does", "see", "that"]},
    ]


def test_degenerate_word_drawn_on_adjacent_card_warns_never_errors():
    result = sc.check_word_coverage(
        _played(degenerate=True), _captioned(), _cards())
    assert [f for f in result["findings"]
            if f["severity"] == "error"] == []
    spilled = [f for f in result["findings"]
               if f["kind"] == "degenerate_text_spill"]
    assert len(spilled) == 1
    assert spilled[0]["severity"] == "warning"
    assert "5-star" in spilled[0]["message"]
    assert CARD_B in str(spilled[0]["detail"])


def test_cleanly_timed_word_missing_from_timings_still_errors():
    # Same hole, but the word carries trustworthy timings: the strict
    # rule holds - drawn text does not excuse a mistimed clean word.
    result = sc.check_word_coverage(
        _played(degenerate=False), _captioned(), _cards())
    errors = [f for f in result["findings"]
              if f["kind"] == "played_not_captioned"]
    assert len(errors) == 1
    assert "5-star" in errors[0]["message"]


def test_degenerate_word_absent_from_card_text_still_errors():
    # Pile-up timing alone forgives nothing: the adjacent card must
    # actually draw the word. The Reel 29 "If" shape.
    cards = [
        {"card": CARD_A, "reel_start": 17.14, "reel_end": 18.73},
        {"card": CARD_B, "reel_start": 18.81, "reel_end": 20.55,
         "text_norms": ["reviews", "ai"]},
    ]
    result = sc.check_word_coverage(
        _played(degenerate=True), _captioned(), cards)
    errors = [f for f in result["findings"]
              if f["kind"] == "played_not_captioned"]
    assert len(errors) == 1
    assert "5-star" in errors[0]["message"]

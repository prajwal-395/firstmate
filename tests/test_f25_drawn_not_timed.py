"""F25 forgives played words no honest caption can draw or time.

Two Reel 10 findings (2026-09-20), both correct output the gate
refused (AGENTS.md 10.4):

1. A RECORDED display suppression strands in a card hole. The global
   "um" suppression (lc-0049, captain) hides Akshita's filler while
   the audio plays it; the planner groups "their" with the card
   before and "website" with the card after, so no card covers the
   suppressed token. Demanding one would caption against the
   recorded decision. Suppressed words sit out the uncovered
   computation; the existing "suppressed" warning still names them
   with their ids, never silence.

2. A SUB-FRAME word drawn but untimed. 'are' stamped at 30ms (under
   one frame at 23.976fps); the planner draws it in the card text
   ("so what's happening, what are you") but its highlight window
   rounds to zero frames, so `generate_remotion_props` skips the
   dead sweep loudly and times from "what" to "you". The identity
   diff read the skipped sweep as a dropped word. A played word
   under one frame whose norm the card TEXT carries is timing dust:
   forgiven with an "untimed_drawn" warning naming word and card.

Both stay strict where it matters: an unsuppressed word in a hole
still errors; a word long enough to sweep (>= 1 frame) that the
card does not time still errors; a sub-frame word the card text
does not carry still errors.

`library/tools/subtitle_coverage.py` (`check_word_coverage`);
wired as F25 in `library/tools/reel_conformance_verifier.py`.
"""

from library.tools import subtitle_coverage as sc

CARD_A = "sub_before.mov"
CARD_B = "sub_after.mov"
FPS = 24000 / 1001
FRAME = 1001 / 24000


def _w(word, start, end, card, **kw):
    entry = {"word": word, "norm": sc.normalize_word(word),
             "reel_start": start, "reel_end": end, "card": card}
    entry.update(kw)
    return entry


# ── 1. suppressed word in a card hole ─────────────────────────────

def test_suppressed_word_in_hole_warns_never_errors():
    played = [
        _w("their", 18.21, 18.60, CARD_A),
        _w("um", 18.94, 19.16, CARD_A),
        _w("website", 19.16, 19.53, CARD_B),
    ]
    suppressed = [played[1]]
    captioned = [
        _w("their", 18.21, 18.60, CARD_A),
        _w("website", 19.16, 19.53, CARD_B),
    ]
    cards = [
        {"card": CARD_A, "reel_start": 17.50, "reel_end": 18.70},
        {"card": CARD_B, "reel_start": 19.16, "reel_end": 20.00},
    ]
    result = sc.check_word_coverage(played, captioned, cards,
                                    suppressed=suppressed)
    assert [f for f in result["findings"]
            if f["severity"] == "error"] == []
    warned = [f for f in result["findings"]
              if f["kind"] == "suppressed"]
    assert len(warned) == 1
    assert "um" in warned[0]["message"]


def test_unsuppressed_word_in_hole_still_errors():
    played = [
        _w("their", 18.21, 18.60, CARD_A),
        _w("um", 18.94, 19.16, CARD_A),
        _w("website", 19.16, 19.53, CARD_B),
    ]
    captioned = [
        _w("their", 18.21, 18.60, CARD_A),
        _w("website", 19.16, 19.53, CARD_B),
    ]
    cards = [
        {"card": CARD_A, "reel_start": 17.50, "reel_end": 18.70},
        {"card": CARD_B, "reel_start": 19.16, "reel_end": 20.00},
    ]
    result = sc.check_word_coverage(played, captioned, cards)
    errors = [f for f in result["findings"]
              if f["kind"] == "played_not_captioned"]
    assert len(errors) == 1
    assert "um" in errors[0]["message"]


# ── 2. sub-frame word drawn but untimed ────────────────────────────

def _identity_case(word, start, end):
    played = [
        _w("what", 12.05, 12.32, CARD_A),
        _w(word, start, end, CARD_A),
        _w("you", 12.35, 12.56, CARD_A),
    ]
    captioned = [
        _w("what", 12.05, 12.32, CARD_A),
        _w("you", 12.35, 12.56, CARD_A),
    ]
    cards = [{"card": CARD_A, "reel_start": 11.90, "reel_end": 12.70,
              "text_norms": ["so", "whats", "happening", "what", "are",
                             "you"]}]
    return played, captioned, cards


def test_subframe_word_in_card_text_warns_never_errors():
    played, captioned, cards = _identity_case("are", 12.32, 12.35)
    assert 0 < 12.35 - 12.32 < FRAME
    result = sc.check_word_coverage(played, captioned, cards)
    assert [f for f in result["findings"]
            if f["severity"] == "error"] == []
    warned = [f for f in result["findings"]
              if f["kind"] == "untimed_drawn"]
    assert len(warned) == 1
    assert "are" in warned[0]["message"]
    assert CARD_A in str(warned[0]["detail"])


def test_full_frame_word_missing_from_timings_still_errors():
    # 60ms of speech can sweep a frame: the planner dropping it is a
    # real defect even where the text carries it.
    played, captioned, cards = _identity_case("are", 12.32, 12.38)
    assert 12.38 - 12.32 >= FRAME
    result = sc.check_word_coverage(played, captioned, cards)
    errors = [f for f in result["findings"]
              if f["kind"] == "word_mismatch"]
    assert len(errors) == 1
    assert "are" in errors[0]["message"]


def test_subframe_word_absent_from_card_text_still_errors():
    played, captioned, cards = _identity_case("are", 12.32, 12.35)
    cards[0]["text_norms"] = ["so", "whats", "happening", "what", "you"]
    result = sc.check_word_coverage(played, captioned, cards)
    errors = [f for f in result["findings"]
              if f["kind"] == "word_mismatch"]
    assert len(errors) == 1
    assert "are" in errors[0]["message"]

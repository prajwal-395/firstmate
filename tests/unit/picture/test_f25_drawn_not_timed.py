"""F25 forgives played words no honest caption can draw or time - a
recorded display suppression in a card hole, a sub-frame word the card
text carries - and stays strict everywhere else. History (Reel 10,
2026-09-20): `docs/evidence/f25_drawn_not_timed.md`.
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

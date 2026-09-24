"""F25 grades caption-timing-pinned cards in the unpinned frame.

Reel 13 (2026-09-20): the captain hand-moved the closing caption
cards 7 frames later on the live timeline (09-11); the move is
recorded as caption-timing pins the build applies. F25 compared the
placed cards against strict word timings and refused the reel with
ten word_mismatch/played_not_captioned findings - the gate failing
the captain's recorded decision (AGENTS.md 10.4). Pinned card spans
now grade shifted back by the pin, matched with the pin owner's own
predicate; unpinned cards, other timelines' pins, and genuine drops
past the pins still fail exactly as before.

`library/tools/caption_timing.grade_spans` (the inverse); wired as
F25 in `library/tools/reel_conformance_verifier._derive_word_coverage`.
"""

from library.tools import caption_timing as ct
from library.tools import subtitle_coverage as sc

FPS = 24000 / 1001
FRAME = 1001 / 24000

TIMELINE = "Reel 13 - the-accounting-firm-ai-called-healthcare"
PIN = {
    "scope": {"speaker": "Akshita",
              "source_start_at_or_after": 500.78,
              "source_start_before": 512.759,
              "timeline": TIMELINE},
    "offset_frames": 7,
    "reason": "test: the captain's 09-11 hand move",
}

# The real Reel 13 numbers, shrunk to one card: played "And ... the"
# at strict times, the placed card 7 frames later, block binding as
# the props artefact records it.
PLAYED = [
    {"word": "And", "norm": "and",
     "reel_start": 67.591, "reel_end": 67.851},
    {"word": "that's", "norm": "that's",
     "reel_start": 67.851, "reel_end": 68.011},
    {"word": "why", "norm": "why",
     "reel_start": 68.011, "reel_end": 68.111},
    {"word": "we've", "norm": "we've",
     "reel_start": 68.111, "reel_end": 68.291},
    {"word": "been", "norm": "been",
     "reel_start": 68.291, "reel_end": 68.461},
    {"word": "building", "norm": "building",
     "reel_start": 68.461, "reel_end": 68.991},
    {"word": "the", "norm": "the",
     "reel_start": 68.991, "reel_end": 69.121},
    {"word": "Lucie", "norm": "lucie",
     "reel_start": 69.121, "reel_end": 69.371},
]
CARD = "sub_akshita_xxx_505862-509292_yyy.mov"
CARD2 = "sub_akshita_xxx_505862-509292_zzz.mov"
# Placed at record 1628 / 1662, seven frames past the words.
SHIFT = 7 * FRAME
CAPTIONED = [
    {"word": word["word"], "norm": word["norm"], "card": CARD,
     "reel_start": word["reel_start"] + SHIFT,
     "reel_end": word["reel_end"] + SHIFT}
    for word in PLAYED[:6]
] + [
    {"word": word["word"], "norm": word["norm"], "card": CARD2,
     "reel_start": word["reel_start"] + SHIFT,
     "reel_end": word["reel_end"] + SHIFT}
    for word in PLAYED[6:]
]
CARDS = [{
    "card": CARD,
    "reel_start": 1628 / FPS,
    "reel_end": 1662 / FPS,
    "binding": {"speaker": "Akshita",
                "source_clip_id": "5ae8f521-647e-49c6-bf3d",
                "source_start": 505.862,
                "source_end": 509.292},
}, {
    "card": CARD2,
    "reel_start": 1662 / FPS,
    "reel_end": 1710 / FPS,
    "binding": {"speaker": "Akshita",
                "source_clip_id": "5ae8f521-647e-49c6-bf3d",
                "source_start": 505.862,
                "source_end": 509.292},
}]


def _errors(result):
    return [f for f in result["findings"]
            if f["severity"] == "error"]


def test_pinned_offset_grades_clean():
    graded = ct.grade_spans(CARDS, [PIN], FPS, timeline=TIMELINE)
    result = sc.check_word_coverage(PLAYED, CAPTIONED, graded)
    assert _errors(result) == []


def test_same_shift_without_pin_still_errors():
    graded = ct.grade_spans(CARDS, [], FPS, timeline=TIMELINE)
    result = sc.check_word_coverage(PLAYED, CAPTIONED, graded)
    assert _errors(result) != []


def test_pin_for_another_timeline_is_not_honoured():
    other = dict(PIN)
    other["scope"] = dict(PIN["scope"], timeline="Reel 21 - something-else")
    graded = ct.grade_spans(CARDS, [other], FPS, timeline=TIMELINE)
    result = sc.check_word_coverage(PLAYED, CAPTIONED, graded)
    assert _errors(result) != []


def test_genuine_drop_past_the_pin_still_errors():
    graded = ct.grade_spans(CARDS, [PIN], FPS, timeline=TIMELINE)
    short = [dict(entry) for entry in CAPTIONED if entry["norm"] != "why"]
    result = sc.check_word_coverage(PLAYED, short, graded)
    errors = _errors(result)
    assert errors != []
    assert any("why" in error["message"] for error in errors)

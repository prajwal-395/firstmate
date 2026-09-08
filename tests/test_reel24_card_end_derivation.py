"""Reel 24's 'concise,' card: the end derives from the WORD (pinning tests).

Measured 2026-09-08 through the REAL verifier path (`spine_for_reel` on the
captain's transcript, moment 24 `why-ai-trusts-youtube` 2009.529-2079.919,
then `generate_subtitles`, then `check_caption_hangs`):

- word 'concise,': master 2053.648-2055.676 (span 2.028s), reel 44.119-46.147.
  The timed end IS the VAD segment end (segment 2051.5-2055.68): ~0.4s of
  utterance (local neighbour rate 0.2-0.5s/word) with ~1.6s of trailing
  silence padded on. At 2.028s it sits UNDER `MAX_WORD_SECONDS` (3.0s) -
  and under PR 692's measured genuine ceiling (2.65s) - so
  `_clamp_stretched_words` is correctly silent here.
- card 'very strong, concise,': reel 42.671-46.147 (3.48s, 3 words).
- card end (46.147) == last-word end (46.147) == block-12 end (46.147);
  the next card starts at 48.273. The block end coincides because block 12
  is cut from the same VAD segment - it is not an independent derivation.
- F15 refuses the card: 3.48s for 3 words against the 3.0s limit.

So the trichotomy from the filing - word, next card's start, or block
boundary - answers WORD on current main: no next-start or block-boundary
derivation exists in `generate_subtitles` (a card's end is its last word's
end, plus at most the 0.7s legibility floor; overlaps only ever shrink an
end or carry the words along). These tests PIN that derivation and the
gate's refusal of this exact shape. They pass before AND after this diff -
there is deliberately no behaviour change here: the measurement refuted
the wrong-derivation premise, and the residual defect (a sub-bound
trailing-silence stretch) admits no fix that leaves `_clamp_stretched_words`,
`MAX_WORD_SECONDS` and the F15 limit untouched. See the needs-decision
record on the task for the options.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.tools.reel_conformance_verifier import (
    FindingClass,
    check_caption_hangs,
)

FPS = 24000 / 1001  # 23.976 exact

# Reel 24's real relative word timings (master seconds, rebased to a
# 100.0 source origin exactly as the spine carries them): 'very'
# 2052.183-2052.544, 'strong,' 2053.086-2053.628 (gap 0.542s), 'concise,'
# 2053.648-2055.676 (span 2.028s, gap 0.020s), then 'there's' 2.990s later.
# The block ends AFTER the word (14.0 > 13.493) so a block-boundary
# derivation would be visible as a different number.
WORDS = [
    {"word": "very", "source_start": 100.000, "source_end": 100.361},
    {"word": "strong,", "source_start": 100.903, "source_end": 101.445},
    {"word": "concise,", "source_start": 101.465, "source_end": 103.493},
    {"word": "there's", "source_start": 106.483, "source_end": 106.684},
]


def _spine():
    return {"structure": [{
        "block_type": "speech",
        "position": 12,
        "timeline_start": 10.0,
        "timeline_end": 17.0,
        "source_start": 100.0,
        "source_end": 107.0,
        "clip_id": "clip_001",
        "alignment_method": "timeline_transcript",
        "word_timestamps": WORDS,
        "content": {"text": " ".join(w["word"] for w in WORDS)},
    }]}


def _entries():
    return generate_subtitles(
        _spine(), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def _concise_card(entries):
    cards = [e for e in entries if "concise," in e["text"].split()]
    assert len(cards) == 1
    return cards[0]


def test_card_end_is_the_words_end_not_next_start_or_block_end(capsys):
    """The filing's trichotomy, answered in code: WORD.

    The 'concise,' card ends at the word's own timed end (13.493) - not at
    the next card's start (16.483) and not at the block end (17.0). The
    2.028s word is under `MAX_WORD_SECONDS`, so the clamp stays silent;
    assert that too, because a future bound change must update this test
    deliberately rather than drift past it.
    """
    entries = _entries()
    card = _concise_card(entries)

    concise = next(w for w in card["words"] if w["word"] == "concise,")
    assert card["timeline_end"] == pytest.approx(concise["end"], abs=0.002)
    assert card["timeline_end"] == pytest.approx(13.493, abs=0.002)

    later_starts = sorted(
        e["timeline_start"] for e in entries
        if e["timeline_start"] > card["timeline_start"])
    assert later_starts, "the next card must exist to separate the candidates"
    assert card["timeline_end"] != pytest.approx(later_starts[0], abs=0.01)
    assert card["timeline_end"] != pytest.approx(17.0, abs=0.01)

    assert "span longer than" not in capsys.readouterr().err


def test_f15_still_refuses_the_reel24_card_shape():
    """The gate keeps its teeth on the real failure shape.

    A ~3.5s three-word card whose last word carries a sub-bound stretch is
    refused, exactly as reel 24 was. The planner emits this shape faithfully
    (see above); the fix for the underlying stretch is NOT here (it would
    be a bound by another name), so the gate must keep refusing it.
    """
    entries = _entries()
    card = _concise_card(entries)
    derived = {"reel_start": card["timeline_start"],
               "reel_end": card["timeline_end"], "text": card["text"]}
    findings = check_caption_hangs("Reel 24", [derived], FPS)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.F15

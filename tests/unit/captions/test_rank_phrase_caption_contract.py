"""A rank phrase on a caption card must not refuse the build (Reel 08).

Reel 08 `top-three-on-google-hallucinated-by-ai` was REFUSED in the
2026-09-20 duplicate-take rebuild: step 4.01 planned the card "the top
three" - the declared reading, since rank labels stay words
(`tests/unit/captions/test_caption_reading.py::test_pronouns_ranks_and_style_stay_words`)
- and then its own contract check re-read each word in ISOLATION, where
a bare "three" reads "3". The planner was right and the gate failed
correct output (AGENTS.md 10.4).

The contract now re-reads each card with its own block-predecessor's
last word as left context, comparing only the card's own words - the
way the planner applies the reading to the block's whole word stream
before grouping.

These tests observe the behaviour THROUGH `generate_subtitles`, not the
reading in isolation: a unit test of the reading passes all along,
which proves nothing about the cards a reel carries.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles


def _spine(text):
    """One speech block carrying `text` as its own word timestamps."""
    words = text.split()
    step = 0.35
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 10.0,
        "timeline_end": 10.0 + len(words) * step,
        "source_start": 100.0,
        "source_end": 100.0 + len(words) * step,
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": w, "source_start": 100.0 + i * step,
             "source_end": 100.0 + i * step + 0.3}
            for i, w in enumerate(words)
        ],
        "content": {"text": text},
    }]}


def _entries(text):
    return generate_subtitles(
        _spine(text), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def test_rank_phrase_on_one_card_passes_contract():
    """Reel 08's shape: "the top three" plans and is not refused.

    Before the fix this raised `AssertionError: ... word is not the
    declared reading of itself: three`. After the fix the card reads
    back exactly as drawn.
    """
    entries = _entries("the top three searches but on")
    assert " ".join(e["text"] for e in entries) == \
        "the top three searches but on"


def test_bare_numerals_still_read_as_digits():
    """The fix must not blunt the rule where it applies.

    A quantity with no rank marker still renders (and verifies) as
    digits - the contract keeps its teeth on real violations.
    """
    entries = _entries("it gave three stars and two percent growth")
    assert " ".join(e["text"] for e in entries) == \
        "it gave 3 stars and 2 percent growth"

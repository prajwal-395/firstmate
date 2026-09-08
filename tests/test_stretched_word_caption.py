"""A stretched word must not become a hanging caption card (reel 24).

Reel 24 `why-ai-trusts-youtube` was REFUSED in the 2026-09-08 rebuild: the
card 'concise,' hung 2.03s past its speech against F15's 1.0s limit. The
card's end comes FROM THE WORD - the transcript times 'concise,' over
~3.03s, and the planner renders that span faithfully:

- a block ending 3s after a 0.4s word still plans a 0.7s card (the block
  boundary does not stretch it);
- a next card starting 2.6s later still leaves a 0.7s card (the next
  card's start does not stretch it);
- only a ~3s word span itself produces a ~3s card.

A word spanning longer than `MAX_WORD_SECONDS` is the aligner bridging
silence, not speech (PR 692 measured 8,492 bound words, genuine max
2.65s). The planner is the side that is wrong: it drew 3.03s of caption
time over a span the pipeline already knows is not speech. The gate is
right to refuse, and is left untouched - the second test pins that it
still fires on exactly this shape.

These tests observe the behaviour THROUGH `generate_subtitles`, not the
grouping function in isolation: a unit test of the clamp would pass all
along, which proves nothing about the cards a reel carries.
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


def _spine(words):
    """One speech block carrying `words` as its own word timestamps."""
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 10.0,
        "timeline_end": 13.5,
        "source_start": 100.0,
        "source_end": 103.5,
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": " ".join(w["word"] for w in words)},
    }]}


def _entries(spine):
    return generate_subtitles(
        spine, caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def _cards(entries):
    return [
        {"reel_start": e["timeline_start"], "reel_end": e["timeline_end"],
         "text": e["text"]}
        for e in entries
    ]


def test_stretched_single_word_plans_a_floor_card_and_passes_f15(capsys):
    """Reel 24's shape: one word timed over 3.03s plans a 0.7s card.

    Before the fix this planned a 3.03s card and F15 refused it - the
    refusal the rebuild held reel 24 for. After the fix the word keeps
    its (trustworthy) onset for the legibility floor, and the gate that
    refused the 3.03s card has nothing to say.
    """
    entries = _entries(_spine([
        {"word": "concise,", "source_start": 100.0, "source_end": 103.03},
    ]))

    assert len(entries) == 1
    assert entries[0]["text"] == "concise,"
    duration = entries[0]["timeline_end"] - entries[0]["timeline_start"]
    assert duration == pytest.approx(0.7, abs=0.01)
    assert entries[0]["timeline_start"] == pytest.approx(100.0 - 100.0 + 10.0)

    assert check_caption_hangs("Reel 24", _cards(entries), FPS) == []

    # SAID, not silent: the rewritten timing names its word.
    assert "concise," in capsys.readouterr().err


def test_f15_still_refuses_a_three_second_single_word_card():
    """The gate keeps its teeth: a 3.03s one-word card still fails.

    This is the card the planner used to emit. The fix removes the
    producer of such cards; it must not also remove the check that
    would catch one if it ever came back.
    """
    findings = check_caption_hangs(
        "Reel 24",
        [{"reel_start": 10.0, "reel_end": 13.03, "text": "concise,"}],
        FPS,
    )
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.F15


def test_genuine_slow_words_are_not_clamped():
    """A 2.6s word is slow speech, not a stretched one: hands off.

    PR 692 measured genuine words up to 2.65s across 8,492 bound words.
    The clamp reads the same `MAX_WORD_SECONDS` bound F5 does, so what
    counts as speech keeps its measured end here too.
    """
    entries = _entries(_spine([
        {"word": "weell", "source_start": 100.0, "source_end": 102.6},
    ]))

    assert len(entries) == 1
    duration = entries[0]["timeline_end"] - entries[0]["timeline_start"]
    assert duration == pytest.approx(2.6, abs=0.01)

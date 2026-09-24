"""`enforce_min_duration` is CALLED, on every path that plans captions.

The rule was defined, documented by two other modules as though it ran,
and wired to nothing: grepped across `library/` and `tests/` the name
appeared three times - its own definition and two comments describing its
behaviour.  Zero call sites.  A unit test of the function would have
passed all along, which is precisely why these tests observe the CALL
through `generate_subtitles` rather than the function in isolation.

Measured on the nineteen approved reels of `lucie/geo-podcast`: 832
caption cards, one overlapping pair (`sub_1_001` ends 6.253, `sub_1_002`
starts 6.223) that the overlap clause removes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.steps.step_4_01_plan_subtitles import step as plan_subtitles
from library.steps.step_4_01_plan_subtitles.step import generate_subtitles


def _spine(words, text=None):
    """One speech block carrying `words` as its own word timestamps."""
    text = text or " ".join(w["word"] for w in words)
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 0.0,
        "timeline_end": words[-1]["source_end"],
        "source_start": 0.0,
        "source_end": words[-1]["source_end"],
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": text},
    }]}


def _overlapping_words(count=8, dur=0.4, overlap=0.03):
    """Words whose ASR timings overlap their neighbour, as 001's do.

    Every adjacent pair overlaps, so wherever the grouper splits, the
    boundary pair is an overlapping one and the two cards it makes cannot
    coexist on one subtitle track.
    """
    words, t = [], 0.0
    for i in range(count):
        words.append({"word": f"word{i}", "source_start": round(t, 3),
                      "source_end": round(t + dur, 3)})
        t += dur - overlap
    return words


def _entries(spine):
    return generate_subtitles(
        spine, caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def test_generate_subtitles_calls_enforce_min_duration(monkeypatch):
    """The master path calls the rule; it does not re-implement it.

    The site used to carry a hand-inlined copy that extended a short card
    only as far as the next card's first word and never closed an
    overlap.  A spy is the only thing that tells the two apart: the
    inlined version and the call produce the same shape of output.
    """
    seen = []
    original = plan_subtitles.enforce_min_duration

    def spy(groups, *args, **kwargs):
        seen.append([dict(g) for g in groups])
        return original(groups, *args, **kwargs)

    monkeypatch.setattr(plan_subtitles, "enforce_min_duration", spy)
    entries = _entries(_spine(_overlapping_words()))

    assert entries
    assert seen, (
        "generate_subtitles planned captions without calling "
        "enforce_min_duration - the rule is wired to nothing again")
    for groups in seen:
        for group in groups:
            assert "start" in group and "end" in group, (
                "enforce_min_duration reads start/end; the entries must be "
                f"projected onto those keys before the call, got {group}")




def test_overlapping_word_timings_do_not_reach_the_cards():
    """The overlap clause is what removes F6, and it only runs if called.

    Without the call this fixture emits two cards that overlap by the
    same 0.03s the ASR's own word timings carry - which is the pair the
    conformance verifier found on Reel 01.
    """
    entries = sorted(_entries(_spine(_overlapping_words())),
                     key=lambda e: e["timeline_start"])
    assert len(entries) > 1, "the fixture must produce more than one card"
    overlaps = [
        (a["id"], a["timeline_end"], b["id"], b["timeline_start"])
        for a, b in zip(entries, entries[1:])
        if a["timeline_end"] > b["timeline_start"] + 0.01
    ]
    assert not overlaps, f"overlapping caption cards: {overlaps}"

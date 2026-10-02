"""A beat snap may adjust a cut to the last word (or just behind it); it
may not move the cut to an earlier word and truncate speech.

History: docs/evidence/transition_placement.md.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_02_plan_transitions.post_bridge import (  # noqa: E402
    MAX_WORD_END_BACKTRACK,
    resolve_cut_point,
)


def speech_block(start, end, word_ends):
    """A spine block whose words end at the given TIMELINE times.

    The spine contract keeps clip_id/source_start/source_end/
    word_timestamps at the TOP level of a block, not under content.
    """
    return {
        "block_type": "speech",
        "timeline_start": start,
        "timeline_end": end,
        "clip_id": "clip_011",
        "source_start": 0.0,
        "source_end": end - start,
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": f"w{i}",
             "source_start": max(0.0, we - start - 0.2),
             "source_end": we - start}
            for i, we in enumerate(word_ends)
        ],
        "content": {"clip_id": "clip_011"},
    }


def non_speech(start, end):
    return {"block_type": "transition_slot",
            "timeline_start": start, "timeline_end": end,
            "clip_id": None, "source_start": None, "source_end": None,
            "word_timestamps": [], "alignment_method": None, "content": {}}


def test_a_beat_on_an_earlier_word_cannot_move_the_cut():
    """The exact 001 failures: the hook 0.0-2.4 whose first word ends on
    a beat (cut relocated to 0.196s), and the 18.37 -> 11.33 case that
    would have dropped seven seconds of speech."""
    cases = [
        (speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.35]),
         non_speech(2.4, 5.4), [0.196, 0.72, 1.25, 1.79], 2.0, 2.4),
        (speech_block(8.38, 18.37, word_ends=[11.33, 15.0, 18.3]),
         non_speech(18.37, 20.87), [11.33, 12.0, 13.0], 18.0, 18.37),
    ]
    for outgoing, incoming, beats, floor, ceiling in cases:
        info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                                 beat_grid=beats)
        assert floor < info["cut_time"] <= ceiling, (
            f"cut relocated to {info['cut_time']}, truncating speech")


def test_a_beat_on_or_just_behind_the_last_word_is_used():
    """The feature itself survives the fix: a beat on the last word end,
    or within the backtrack window behind it, is the cut."""
    last = 2.30
    earlier = last - (MAX_WORD_END_BACKTRACK / 2)
    cases = [
        ([0.196, 1.1, 2.3], [0.196, 2.3], 2.3),
        ([0.196, earlier, last], [0.196, earlier], earlier),
    ]
    for word_ends, beats, expected in cases:
        info = resolve_cut_point(
            incoming=non_speech(2.4, 5.4),
            outgoing=speech_block(0.0, 2.4, word_ends=word_ends),
            beat_grid=beats)
        assert info["word_beat_coincidence"] is True
        assert abs(info["cut_time"] - expected) < 1e-6


def test_no_usable_beat_cuts_at_the_last_word():
    """A beat further back than the window is ignored, and no beat grid
    at all still cuts at the last word."""
    far = resolve_cut_point(
        incoming=non_speech(6.0, 8.0),
        outgoing=speech_block(0.0, 6.0, word_ends=[1.0, 5.9]),
        beat_grid=[1.0])
    assert far["word_beat_coincidence"] is False
    assert far["cut_time"] > 5.0
    none = resolve_cut_point(
        incoming=non_speech(2.4, 5.4),
        outgoing=speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.3]),
        beat_grid=[])
    assert abs(none["cut_time"] - 2.3) < 1e-6
    assert none["word_beat_coincidence"] is False

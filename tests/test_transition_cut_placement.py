"""A beat snap may adjust a cut. It may not move it somewhere else.

On project 001 the transition planned for the end of the 2.4s hook was
placed at **0.196s** - the end of that block's FIRST word - and the cut
planned for 18.37s moved to 11.33s, which would have dropped seven
seconds of speech. `resolve_cut_point` scanned each block's word ends
from the beginning and took whichever one happened to land on a beat.

Two things made it survive: the record said `snap_delta_seconds: 0.0`
throughout (that field only measures the `snap_to_beat` path, not
word-end matching), and it only ever affected the transitions that get a
Fusion comp, so a run with nothing but hard cuts looked fine. It was
caught by `compile_manifest` refusing the manifest - "Transition
trans_001 at 0.196s does not sit at the end of any V1 clip" - one step
before the render.
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


def test_a_beat_on_an_early_word_cannot_pull_the_cut_to_the_start():
    """The exact 001 failure: hook 0.0-2.4, first word ends on a beat."""
    outgoing = speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.35])
    incoming = non_speech(2.4, 5.4)
    # 0.196 sits exactly on a beat; 2.35 does not.
    beats = [0.196, 0.72, 1.25, 1.79]

    info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                             beat_grid=beats)

    assert info["cut_time"] > 2.0, (
        f"cut was relocated to {info['cut_time']} - this is the 001 bug")
    assert info["cut_time"] <= 2.4


def test_a_mid_block_beat_cannot_truncate_speech():
    """The 18.37 -> 11.33 case: seven seconds of speech dropped."""
    outgoing = speech_block(8.38, 18.37, word_ends=[11.33, 15.0, 18.3])
    incoming = non_speech(18.37, 20.87)
    beats = [11.33, 12.0, 13.0]   # only the early word end is on a beat

    info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                             beat_grid=beats)

    assert info["cut_time"] > 18.0, (
        f"cut moved to {info['cut_time']}, truncating speech")


def test_a_beat_on_the_last_word_is_still_used():
    """The feature itself must survive the fix."""
    outgoing = speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.3])
    incoming = non_speech(2.4, 5.4)
    beats = [0.196, 2.3]   # the LAST word end is also on a beat

    info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                             beat_grid=beats)

    assert info["word_beat_coincidence"] is True
    assert abs(info["cut_time"] - 2.3) < 1e-6


def test_a_beat_just_behind_the_last_word_is_used():
    """Within the backtrack window, the later coincidence wins."""
    last = 2.30
    earlier = last - (MAX_WORD_END_BACKTRACK / 2)
    outgoing = speech_block(0.0, 2.4, word_ends=[0.196, earlier, last])
    incoming = non_speech(2.4, 5.4)
    beats = [0.196, earlier]      # last word end is NOT on a beat

    info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                             beat_grid=beats)

    assert info["word_beat_coincidence"] is True
    assert abs(info["cut_time"] - earlier) < 1e-6


def test_a_beat_further_back_than_the_window_is_ignored():
    outgoing = speech_block(0.0, 6.0, word_ends=[1.0, 5.9])
    incoming = non_speech(6.0, 8.0)
    beats = [1.0]                 # far outside the backtrack window

    info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                             beat_grid=beats)

    assert info["word_beat_coincidence"] is False
    assert info["cut_time"] > 5.0


def test_no_beat_grid_still_cuts_at_the_last_word():
    outgoing = speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.3])
    incoming = non_speech(2.4, 5.4)

    info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                             beat_grid=[])

    assert abs(info["cut_time"] - 2.3) < 1e-6
    assert info["word_beat_coincidence"] is False

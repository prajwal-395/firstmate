import pytest
from library.tools.subtitle_qa import verify_subtitle_timing














def test_a_planned_non_speech_beat_is_not_a_caption_gap():
    """`subtitle_gaps` reads the spine, so a B-roll breath is not a fault.

    On project 001 the four reported gaps were 3.0, 3.5, 3.0 and 3.0
    seconds - exactly the four B-roll breaths the plan wrote at spine
    blocks 1, 4, 6 and 9 - and the finding reached a creative step (#332)
    carrying a legend that blamed the aligner. Caption coverage inside
    the speech blocks was 84.3%.
    """
    subs = [
        {"timeline_start": 0.0, "timeline_end": 2.398, "text": "hook line"},
        {"timeline_start": 5.398, "timeline_end": 8.38, "text": "second line"},
    ]
    spine = [
        {"block_type": "hook", "position": 0, "clip_id": "clip_011",
         "timeline_start": 0.0, "timeline_end": 2.398},
        {"block_type": "transition_slot", "position": 1,
         "timeline_start": 2.398, "timeline_end": 5.398},
        {"block_type": "speech", "position": 2, "clip_id": "clip_011",
         "timeline_start": 5.398, "timeline_end": 8.38},
    ]

    without = next(r for r in verify_subtitle_timing(subs)
                   if r.metric == "subtitle_gaps")
    assert not without.passed
    assert "whole timeline" in without.detail

    with_spine = next(r for r in verify_subtitle_timing(
        subs, spine_blocks=spine) if r.metric == "subtitle_gaps")
    assert with_spine.passed, with_spine.value
    assert "speech blocks" in with_spine.detail


def test_uncaptioned_seconds_inside_a_speech_block_are_still_reported():
    """The check is repaired, not switched off."""
    subs = [
        {"timeline_start": 0.0, "timeline_end": 1.0, "text": "one"},
        {"timeline_start": 9.0, "timeline_end": 10.0, "text": "two"},
    ]
    spine = [{"block_type": "speech", "position": 0, "clip_id": "clip_011",
              "timeline_start": 0.0, "timeline_end": 10.0}]
    result = next(r for r in verify_subtitle_timing(subs, spine_blocks=spine)
                  if r.metric == "subtitle_gaps")
    assert not result.passed
    assert result.value == [(0, 1, 8.0)]

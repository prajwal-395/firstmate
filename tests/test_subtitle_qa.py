import pytest
from library.tools.subtitle_qa import verify_subtitle_timing

def test_verify_subtitle_timing_empty():
    results = verify_subtitle_timing([])
    assert len(results) == 5
    for r in results:
        assert r.passed

def test_verify_subtitle_timing_single():
    results = verify_subtitle_timing([
        {"timeline_start": 1.0, "timeline_end": 3.0, "text": "Hello world"}
    ])
    assert len(results) == 5
    for r in results:
        assert r.passed

def test_verify_subtitle_timing_overlap():
    results = verify_subtitle_timing([
        {"timeline_start": 1.0, "timeline_end": 3.0, "text": "Hello world"},
        {"timeline_start": 2.5, "timeline_end": 4.0, "text": "Overlap"}
    ])
    overlap_res = next(r for r in results if r.metric == "subtitle_overlap")
    assert not overlap_res.passed
    assert overlap_res.value == [(0, 1)]

def test_verify_subtitle_timing_too_short():
    results = verify_subtitle_timing([
        {"timeline_start": 1.0, "timeline_end": 1.2, "text": "Hi"}
    ])
    short_res = next(r for r in results if r.metric == "subtitle_too_short")
    assert not short_res.passed

def test_verify_subtitle_timing_too_long():
    results = verify_subtitle_timing([
        {"timeline_start": 1.0, "timeline_end": 7.0, "text": "Hello"}
    ])
    long_res = next(r for r in results if r.metric == "subtitle_too_long")
    assert not long_res.passed

def test_verify_subtitle_timing_gaps():
    results = verify_subtitle_timing([
        {"timeline_start": 1.0, "timeline_end": 2.0, "text": "Hello"},
        {"timeline_start": 5.0, "timeline_end": 6.0, "text": "Gap here"}
    ])
    gap_res = next(r for r in results if r.metric == "subtitle_gaps")
    assert not gap_res.passed

def test_verify_subtitle_timing_read_speed():
    results = verify_subtitle_timing([
        {"timeline_start": 1.0, "timeline_end": 2.0, "text": "This is a very long sentence that will definitely exceed the reading speed limit of twenty five characters per second."}
    ])
    fast_res = next(r for r in results if r.metric == "subtitle_read_speed")
    assert not fast_res.passed

def test_verify_subtitle_timing_total_duration_overflow():
    results = verify_subtitle_timing([
        {"timeline_start": 5.0, "timeline_end": 12.0, "text": "Overflow"}
    ], total_duration=10.0)
    overflow_res = next((r for r in results if r.metric == "subtitle_overflow"), None)
    assert overflow_res is not None
    assert not overflow_res.passed

def test_verify_subtitle_timing_total_duration_none():
    results = verify_subtitle_timing([
        {"timeline_start": 5.0, "timeline_end": 12.0, "text": "Overflow"}
    ])
    overflow_res = next((r for r in results if r.metric == "subtitle_overflow"), None)
    assert overflow_res is None

def test_verify_subtitle_timing_total_duration_edge():
    results = verify_subtitle_timing([
        {"timeline_start": 5.0, "timeline_end": 10.0, "text": "Edge"}
    ], total_duration=10.0)
    overflow_res = next((r for r in results if r.metric == "subtitle_overflow"), None)
    assert overflow_res is None




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

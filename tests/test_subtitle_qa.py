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



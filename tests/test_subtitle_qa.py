import pytest
from library.tools.subtitle_qa import verify_subtitle_timing, verify_subtitle_safe_zone

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

def test_verify_subtitle_safe_zone_empty():
    results = verify_subtitle_safe_zone([])
    for r in results:
        assert r.passed

def test_verify_subtitle_safe_zone_pass():
    results = verify_subtitle_safe_zone([
        {"y_pct": 50, "x_pct": 50, "width": 100, "height": 100}
    ])
    for r in results:
        assert r.passed

def test_verify_subtitle_safe_zone_top_violation():
    results = verify_subtitle_safe_zone([
        {"y_pct": 5, "x_pct": 50, "width": 10, "height": 10}
    ])
    top_res = next(r for r in results if r.metric == "subtitle_safe_top")
    assert not top_res.passed

def test_verify_subtitle_safe_zone_bottom_violation():
    results = verify_subtitle_safe_zone([
        {"y_pct": 98, "x_pct": 50, "width": 10, "height": 10}
    ])
    bottom_res = next(r for r in results if r.metric == "subtitle_safe_bottom")
    assert not bottom_res.passed

def test_verify_subtitle_safe_zone_margin_violation():
    results = verify_subtitle_safe_zone([
        {"y_pct": 50, "x_pct": 2, "width": 10, "height": 10}
    ])
    margin_res = next(r for r in results if r.metric == "subtitle_safe_margins")
    assert not margin_res.passed

from fractions import Fraction

import pytest

from library.tools.source_clocks import (
    SourceClock,
    frame_count,
    frame_grid_ticks,
    frame_index_ceil,
    frame_span_ticks,
    from_model_clock,
    rational_frame_rate,
    sample_span_ticks,
    tick_to_frame,
    tick_to_sample,
    ticks_between_frames,
)

NTSC = SourceClock(digest="sha256:clip", fps_num=24000, fps_den=1001,
                   sample_rate=48000)
PAL = SourceClock(digest="sha256:pal", fps_num=25, fps_den=1)


def test_frame_spans_tile_without_gaps_or_overlaps():
    spans = [frame_span_ticks(PAL, frame) for frame in range(50)]
    assert spans[0][0] == 0
    for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
        assert first_end == second_start
    assert all(end > start for start, end in spans)


def test_ntsc_frame_span_is_exact():
    # 1000 * 1001 / 24000 = 41.7083... ticks per frame: no float rounding.
    assert frame_span_ticks(NTSC, 0) == (0, 42)
    assert tick_to_frame(NTSC, 41) == 0
    assert tick_to_frame(NTSC, 42) == 1


def test_tick_round_trip_stays_inside_its_frame():
    for tick in range(0, 5000, 7):
        frame = tick_to_frame(NTSC, tick)
        start, end = frame_span_ticks(NTSC, frame)
        assert start <= tick < end


def test_sample_spans_cover_ticks_monotonically_and_round_trip():
    # 48 samples share each millisecond tick: spans overlap rather than
    # tile, which the module states plainly instead of inventing precision.
    spans = [sample_span_ticks(NTSC, sample) for sample in range(200)]
    assert spans[0][0] == 0
    for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
        assert second_start <= first_end
        assert first_end - second_start <= 1
    for tick in range(0, 2000, 13):
        sample = tick_to_sample(NTSC, tick)
        start, end = sample_span_ticks(NTSC, sample)
        assert start <= tick < end


def test_sample_rate_absent_refuses():
    video_only = SourceClock(digest="sha256:v", fps_num=25, fps_den=1)
    with pytest.raises(ValueError):
        sample_span_ticks(video_only, 0)
    with pytest.raises(ValueError):
        tick_to_sample(video_only, 0)


def test_frame_range_covers_its_frames():
    assert ticks_between_frames(PAL, 0, 24) == (0, 1000)
    with pytest.raises(ValueError):
        ticks_between_frames(PAL, 5, 4)


def test_invalid_inputs_refuse():
    with pytest.raises(ValueError):
        SourceClock(digest="", fps_num=25, fps_den=1)
    with pytest.raises(ValueError):
        SourceClock(digest="x", fps_num=0, fps_den=1)
    with pytest.raises(ValueError):
        frame_span_ticks(PAL, -1)
    with pytest.raises(ValueError):
        tick_to_frame(PAL, -1)
    with pytest.raises(ValueError):
        SourceClock(digest="x", fps_num=25, fps_den=1,
                     sample_rate=0)


def test_dict_round_trip():
    assert SourceClock.from_dict(NTSC.to_dict()) == NTSC
    with pytest.raises(ValueError):
        SourceClock.from_dict({"fps_num": 25})


def test_from_model_clock_reads_measured_frame_rate():
    clock = from_model_clock("sha256:abc",
                             {"ticks_per_second": 24,
                              "frame_rate": {"numerator": 24, "denominator": 1}},
                             sample_rate=48000)
    assert (clock.fps_num, clock.fps_den) == (24, 1)
    assert frame_span_ticks(clock, 3) == (3, 4)


def test_from_model_clock_refuses_unmeasured_rates():
    with pytest.raises(ValueError):
        from_model_clock("sha256:abc", {"ticks_per_second": 1000,
                                        "frame_rate": None})


def test_a_measured_rate_becomes_the_exact_rational_it_is():
    """A float is the nearest double to a rate, not the rate: 23.976
    means 24000/1001 and 29.97 means 30000/1001."""
    assert rational_frame_rate(23.976) == Fraction(24000, 1001)
    assert rational_frame_rate(29.97) == Fraction(30000, 1001)
    assert rational_frame_rate(59.94) == Fraction(60000, 1001)
    assert rational_frame_rate(24.0) == Fraction(24, 1)
    assert rational_frame_rate(25.0) == Fraction(25, 1)
    assert rational_frame_rate(12.5) == Fraction(25, 2)
    assert rational_frame_rate(0) is None
    assert rational_frame_rate(-24) is None
    assert rational_frame_rate(float("nan")) is None
    assert rational_frame_rate("x") is None


def test_a_clock_that_cannot_hold_a_whole_frame_has_no_frame_grid():
    """120 kHz holds 24000/1001 exactly (5005 ticks a frame) and holds
    29 fps not at all: the answer is None, never a rounded count."""
    assert frame_grid_ticks(120_000, Fraction(24000, 1001)) == 5005
    assert frame_grid_ticks(120_000, Fraction(24, 1)) == 5000
    assert frame_grid_ticks(1000, Fraction(24000, 1001)) is None
    assert frame_grid_ticks(0, Fraction(24, 1)) is None


def test_the_in_point_never_falls_before_the_edge():
    """The build's own reading: the first frame on or after the edge.

    A float carries the boundary a hair either side of it, and the
    hair must not push the in-point into the frame before the word
    the caption plan starts on.
    """
    rate = Fraction(24000, 1001)
    assert frame_index_ceil(0.0, rate) == 0
    assert frame_index_ceil(float(Fraction(100) / rate), rate) == 100
    # The boundary as a float, a hair under: still that frame.
    under = float(Fraction(100) / rate) - 1e-12
    assert frame_index_ceil(under, rate) == 100
    # Past the boundary: the frame after it.
    assert frame_index_ceil(float(Fraction(101) / rate), rate) == 101


def test_a_span_counts_the_frames_the_build_planned():
    rate = Fraction(24, 1)
    assert frame_count(5.0, rate) == 120
    assert frame_count(1.0 / 24, rate) == 1
    assert frame_count(0.0, rate) == 0

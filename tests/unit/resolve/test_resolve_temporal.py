import pytest

from library.tools.resolve_temporal import (
    FrameSpan,
    append_duration_fields,
    append_fields,
    hold_append_window,
    played_source_span,
    read_playback_rate,
    record_duration_for_source,
    record_span,
)


def test_half_open_source_boundary_projects_without_losing_last_frame():
    fields = append_fields(0, 1, 24, 2, 1)
    assert fields == {
        "startFrame": 0, "endFrame": 1, "recordFrame": 24,
        "trackIndex": 2, "mediaType": 1,
    }
    assert record_span(24, 25) == FrameSpan(24, 25)
    assert FrameSpan(24, 25).duration == 1
    assert append_duration_fields(100, 5, 24, 2)["endFrame"] == 105


@pytest.mark.parametrize("start,end", [(0, 0), (2, 1), (-1, 3)])
def test_invalid_half_open_spans_refuse(start, end):
    with pytest.raises(ValueError):
        FrameSpan(start, end)


def test_source_and_record_clocks_are_converted_for_fps_mismatch():
    source = FrameSpan(100, 200)
    assert record_duration_for_source(
        source, source_fps=50, timeline_fps=25) == 50
    assert played_source_span(
        100, FrameSpan(10, 60), source_fps=50, timeline_fps=25
    ) == source
    assert played_source_span(
        100, FrameSpan(10, 60), source_fps=25, timeline_fps=25,
        playback_rate=2.0,
    ) == FrameSpan(100, 200)


def test_still_and_zero_speed_hold_use_their_native_played_windows():
    record = FrameSpan(40, 46)
    assert played_source_span(
        7, record, source_fps=24, timeline_fps=24, still=True
    ) == FrameSpan(7, 13)
    assert played_source_span(
        79, record, source_fps=24, timeline_fps=24,
        zero_speed_hold=True,
    ) == FrameSpan(79, 80)


def test_pr2692_hold_append_origin_is_adapted_and_readback_is_zero_based():
    api_start, api_end, normalized = hold_append_window(100, 5)
    assert normalized == FrameSpan(96, 101)
    assert (api_start, api_end) == (97, 102)


def test_speed_readback_normalizes_retimes_and_legacy_normal_speed():
    class Item:
        def GetSpeed(self):
            return {"Percentage": 200}

    class LegacyItem:
        pass

    assert read_playback_rate(Item()) == 2.0
    assert read_playback_rate(LegacyItem()) == 1.0
    assert read_playback_rate(Item(), zero_speed_hold=True) == 0.0

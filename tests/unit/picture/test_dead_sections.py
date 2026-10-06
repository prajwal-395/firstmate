"""Tests for dead section analysis in the pacing module.

These tests pin behaviour: a 4s mid-speech pause is found as dead,
a busy no-speech montage is not, thresholds are derived from the
sequence's own distribution, a missing temporal index is unmeasured
(not dead), and a pause spanning a block join is reported once.
"""
from library.tools.pacing import (
    find_dead_sections,
    _all_speech_spans_timeline,
    _block_speech_spans_timeline,
    _clip_motion_at,
    _clip_audio_activity_at,
    _derive_motion_threshold,
    _derive_min_duration,
    _collect_motion_values,
    _merge_gaps,
    _motion_is_active,
    _temporal_index_has_measurements,
)


def _block(position=1, clip_id="clip_1", src_start=0.0, src_end=10.0,
           tl_start=0.0, tl_end=10.0, words=None):
    return {
        "position": position,
        "clip_id": clip_id,
        "source_start": src_start,
        "source_end": src_end,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "word_timestamps": words or [],
    }


def _temporal_index(motion_values=None, motion_sr=1.0, flow_values=None,
                    flow_sr=1.0, scene_boundaries=None, speech_regions=None,
                    audio_events=None):
    idx = {}
    if motion_values is not None:
        idx["motion_energy"] = {
            "values": motion_values,
            "sample_rate_hz": motion_sr,
        }
    if flow_values is not None:
        idx["optical_flow_direction"] = {
            "values": [{"magnitude": v} for v in flow_values],
            "sample_rate_hz": flow_sr,
        }
    if scene_boundaries is not None:
        idx["scene_boundaries"] = [
            {"time": t} for t in scene_boundaries
        ]
    if speech_regions is not None:
        idx["speech_regions"] = [
            {"start": s, "end": e} for s, e in speech_regions
        ]
    if audio_events is not None:
        idx["audio_events"] = [{"time": t} for t in audio_events]
    return idx


class TestBlockSpeechSpansTimeline:
    def test_spans_are_mapped_to_timeline(self):
        block = _block(src_start=5.0, tl_start=100.0, words=[
            {"word": "hello", "source_start": 5.0, "source_end": 5.5},
            {"word": "world", "source_start": 6.0, "source_end": 6.5},
        ])
        spans = _block_speech_spans_timeline(block)
        assert len(spans) == 2
        assert spans[0] == (100.0, 100.5)
        assert spans[1] == (101.0, 101.5)

    def test_invalid_entries_are_skipped(self):
        block = _block(words=[
            {"word": "hello", "source_start": 0.0, "source_end": 0.5},
            {"word": "bad", "source_start": "x", "source_end": "y"},
        ])
        spans = _block_speech_spans_timeline(block)
        assert len(spans) == 1


class TestAllSpeechSpansTimeline:
    def test_spans_are_sorted(self):
        blocks = [
            _block(position=2, tl_start=10.0, words=[
                {"word": "b", "source_start": 0.0, "source_end": 0.5},
            ]),
            _block(position=1, tl_start=0.0, words=[
                {"word": "a", "source_start": 0.0, "source_end": 0.5},
            ]),
        ]
        spans = _all_speech_spans_timeline(blocks)
        assert len(spans) == 2
        assert spans[0][0] == 0.0
        assert spans[1][0] == 10.0


class TestClipMotionAt:
    def test_motion_mean_is_computed(self):
        idx = _temporal_index(motion_values=[0.1, 0.2, 0.3], motion_sr=1.0)
        result = _clip_motion_at(idx, 0.0, 3.0)
        assert result["motion_mean"] is not None
        assert result["motion_mean"] > 0.0

    def test_motion_peak_is_computed(self):
        idx = _temporal_index(motion_values=[0.1, 0.5, 0.3], motion_sr=1.0)
        result = _clip_motion_at(idx, 0.0, 3.0)
        assert result["motion_peak"] == 0.5

    def test_scene_boundary_detected(self):
        idx = _temporal_index(scene_boundaries=[1.5])
        result = _clip_motion_at(idx, 0.0, 3.0)
        assert result["has_scene_boundary"] is True

    def test_no_scene_boundary(self):
        idx = _temporal_index(scene_boundaries=[])
        result = _clip_motion_at(idx, 0.0, 3.0)
        assert result["has_scene_boundary"] is False

    def test_camera_motion_mean(self):
        idx = _temporal_index(flow_values=[0.1, 0.2, 0.3], flow_sr=1.0)
        result = _clip_motion_at(idx, 0.0, 3.0)
        assert result["camera_motion_mean"] is not None
        assert result["camera_motion_mean"] > 0.0


class TestClipAudioActivityAt:
    def test_speech_region_detected(self):
        idx = _temporal_index(speech_regions=[(1.0, 2.0)])
        result = _clip_audio_activity_at(idx, 0.0, 3.0)
        assert result["has_speech_region"] is True

    def test_audio_event_detected(self):
        idx = _temporal_index(audio_events=[1.5])
        result = _clip_audio_activity_at(idx, 0.0, 3.0)
        assert result["has_audio_event"] is True

    def test_no_activity(self):
        idx = _temporal_index()
        result = _clip_audio_activity_at(idx, 0.0, 3.0)
        assert result["has_speech_region"] is False
        assert result["has_audio_event"] is False


class TestDeriveMotionThreshold:
    def test_median_is_used(self):
        values = [0.1, 0.2, 0.3, 0.4, 0.5]
        threshold = _derive_motion_threshold(values)
        assert threshold == 0.3

    def test_empty_returns_zero(self):
        threshold = _derive_motion_threshold([])
        assert threshold == 0.0


class TestMotionIsActive:
    def test_zero_mean_at_zero_threshold_is_static(self):
        """A fully static sequence derives threshold 0; motion 0 is not active."""
        assert _motion_is_active(0.0, 0.0) is False

    def test_motion_above_zero_at_zero_threshold_is_active(self):
        assert _motion_is_active(0.1, 0.0) is True

    def test_mean_at_median_is_active(self):
        assert _motion_is_active(0.5, 0.5) is True

    def test_mean_below_median_is_static(self):
        assert _motion_is_active(0.3, 0.5) is False

    def test_none_is_not_active(self):
        assert _motion_is_active(None, 0.5) is False


class TestDeriveMinDuration:
    def test_90th_percentile(self):
        pauses = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        min_dur = _derive_min_duration(pauses)
        assert min_dur == 9.0

    def test_empty_returns_zero(self):
        min_dur = _derive_min_duration([])
        assert min_dur == 0.0


class TestCollectMotionValues:
    def test_values_collected_from_all_clips(self):
        indices = {
            "clip_1": _temporal_index(motion_values=[0.1, 0.2]),
            "clip_2": _temporal_index(motion_values=[0.3, 0.4]),
        }
        values = _collect_motion_values(indices)
        assert len(values) == 4


class TestFindDeadSections:
    def test_mid_speech_pause_is_found(self):
        """A 4s pause between words in a speech block is a dead section."""
        blocks = [_block(
            tl_start=0.0, tl_end=10.0,
            words=[
                {"word": "hello", "source_start": 0.0, "source_end": 1.0},
                {"word": "world", "source_start": 5.0, "source_end": 6.0},
            ],
        )]
        indices = {"clip_1": _temporal_index(motion_values=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])}
        result = find_dead_sections(blocks, indices)
        assert len(result) >= 1
        # The 4s gap between words (1.0-5.0) should be found
        gap_found = any(
            r["timeline_start"] == 1.0 and r["timeline_end"] == 5.0
            for r in result
        )
        assert gap_found

    def test_busy_no_speech_montage_is_not_dead(self):
        """A no-speech block with motion is not a dead section."""
        blocks = [_block(
            tl_start=0.0, tl_end=5.0, words=[],
        )]
        indices = {"clip_1": _temporal_index(
            motion_values=[0.5, 0.5, 0.5, 0.5, 0.5], motion_sr=1.0)}
        result = find_dead_sections(blocks, indices)
        assert len(result) == 0

    def test_speaking_block_with_no_pause_is_not_dead(self):
        """A block with continuous speech has no dead sections."""
        blocks = [_block(
            tl_start=0.0, tl_end=3.0,
            words=[
                {"word": "hello", "source_start": 0.0, "source_end": 1.0},
                {"word": "world", "source_start": 1.0, "source_end": 2.0},
                {"word": "again", "source_start": 2.0, "source_end": 3.0},
            ],
        )]
        indices = {"clip_1": _temporal_index()}
        result = find_dead_sections(blocks, indices)
        assert len(result) == 0

    def test_dead_section_reports_thresholds(self):
        """Each dead section records the derived thresholds."""
        blocks = [_block(
            tl_start=0.0, tl_end=10.0,
            words=[
                {"word": "hello", "source_start": 0.0, "source_end": 1.0},
                {"word": "world", "source_start": 5.0, "source_end": 6.0},
            ],
        )]
        indices = {"clip_1": _temporal_index(motion_values=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])}
        result = find_dead_sections(blocks, indices)
        assert len(result) >= 1
        assert "motion_threshold" in result[0]
        assert "min_duration_threshold" in result[0]

    def test_block_with_no_clip_id_is_skipped(self):
        blocks = [_block(
            clip_id="", tl_start=0.0, tl_end=5.0, words=[],
        )]
        indices = {}
        result = find_dead_sections(blocks, indices)
        assert len(result) == 0

    def test_multiple_dead_sections_are_all_reported(self):
        blocks = [_block(
            tl_start=0.0, tl_end=50.0,
            words=[
                {"word": "a", "source_start": 0.0, "source_end": 1.0},
                {"word": "b", "source_start": 5.0, "source_end": 6.0},
                {"word": "c", "source_start": 10.0, "source_end": 11.0},
                {"word": "d", "source_start": 15.0, "source_end": 16.0},
                {"word": "e", "source_start": 20.0, "source_end": 21.0},
                {"word": "f", "source_start": 25.0, "source_end": 26.0},
                {"word": "g", "source_start": 30.0, "source_end": 31.0},
                {"word": "h", "source_start": 35.0, "source_end": 36.0},
                {"word": "i", "source_start": 40.0, "source_end": 41.0},
            ],
        )]
        indices = {"clip_1": _temporal_index(motion_values=[0.0] * 60)}
        result = find_dead_sections(blocks, indices)
        assert len(result) >= 2


class TestMergeGaps:
    def test_overlapping_gaps_are_merged(self):
        gaps = [(1.0, 3.0), (2.0, 5.0), (6.0, 8.0)]
        merged = _merge_gaps(gaps)
        assert len(merged) == 2
        assert merged[0] == (1.0, 5.0)
        assert merged[1] == (6.0, 8.0)

    def test_adjacent_gaps_are_merged(self):
        gaps = [(1.0, 3.0), (3.0, 5.0)]
        merged = _merge_gaps(gaps)
        assert len(merged) == 1
        assert merged[0] == (1.0, 5.0)

    def test_empty_gaps(self):
        assert _merge_gaps([]) == []


class TestTemporalIndexHasMeasurements:
    def test_empty_index_has_no_measurements(self):
        assert _temporal_index_has_measurements({}) is False

    def test_index_with_motion_has_measurements(self):
        idx = _temporal_index(motion_values=[0.1, 0.2])
        assert _temporal_index_has_measurements(idx) is True

    def test_index_with_speech_regions_has_measurements(self):
        idx = _temporal_index(speech_regions=[(1.0, 2.0)])
        assert _temporal_index_has_measurements(idx) is True

    def test_index_with_no_measurements(self):
        idx = {"motion_energy": {"values": [], "sample_rate_hz": 1.0}}
        assert _temporal_index_has_measurements(idx) is False


class TestFindDeadSectionsUnmeasured:
    def test_missing_temporal_index_is_not_dead(self):
        """A clip with no temporal index is unmeasured, not dead."""
        blocks = [_block(
            tl_start=0.0, tl_end=10.0,
            words=[
                {"word": "hello", "source_start": 0.0, "source_end": 1.0},
                {"word": "world", "source_start": 5.0, "source_end": 6.0},
            ],
        )]
        indices = {}
        result = find_dead_sections(blocks, indices)
        assert len(result) == 0

    def test_empty_temporal_index_is_not_dead(self):
        """A clip with an empty temporal index is unmeasured, not dead."""
        blocks = [_block(
            tl_start=0.0, tl_end=10.0,
            words=[
                {"word": "hello", "source_start": 0.0, "source_end": 1.0},
                {"word": "world", "source_start": 5.0, "source_end": 6.0},
            ],
        )]
        indices = {"clip_1": {}}
        result = find_dead_sections(blocks, indices)
        assert len(result) == 0

    def test_pause_spanning_block_join_is_reported_once(self):
        """A pause spanning a block join is one dead section, not two."""
        blocks = [
            _block(position=1, tl_start=0.0, tl_end=5.0, words=[
                {"word": "hello", "source_start": 0.0, "source_end": 1.0},
            ]),
            _block(position=2, tl_start=5.0, tl_end=10.0, words=[
                {"word": "world", "source_start": 1.0, "source_end": 2.0},
            ]),
        ]
        indices = {"clip_1": _temporal_index(motion_values=[0.0] * 12)}
        result = find_dead_sections(blocks, indices)
        # The gap from 1.0 to 5.0 (block 1 end) and 5.0 to 6.0 (block 2
        # start, before its first word) is one pause spanning the join -
        # merged into a single 1.0-6.0 dead section, reported once even
        # though both blocks carry the same clip.
        assert len(result) == 1
        assert result[0]["timeline_start"] == 1.0
        assert result[0]["timeline_end"] == 6.0

"""Tests for J/L-cut placement.

These tests pin behaviour: placement is proposed only at speech-to-
speech boundaries where the clip changes, the kind follows the longer
measured pause, the offset is the measured pause, and a boundary with
no measured room on either side gets nothing.
"""
from library.tools.jl_placement import (
    find_jl_placements,
    _trailing_pause,
    _leading_pause,
)


def _block(position, clip_id, tl_start, tl_end, src_start=None, words=None):
    if src_start is None:
        src_start = tl_start
    return {
        "position": position,
        "clip_id": clip_id,
        "source_start": src_start,
        "source_end": src_start + (tl_end - tl_start),
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "word_timestamps": words or [],
    }


def _words(*spans):
    return [
        {"word": f"w{i}", "source_start": s, "source_end": e}
        for i, (s, e) in enumerate(spans)
    ]


class TestPauseMeasurements:
    def test_trailing_pause_is_block_end_minus_last_word(self):
        block = _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0)))
        assert _trailing_pause(block) == 4.0

    def test_leading_pause_is_first_word_minus_block_start(self):
        block = _block(2, "c2", 10.0, 20.0, words=_words((11.0, 12.0)))
        assert _leading_pause(block) == 1.0

    def test_block_without_speech_has_no_trailing_pause(self):
        assert _trailing_pause(_block(1, "c1", 0.0, 10.0)) is None

    def test_block_without_speech_has_no_leading_pause(self):
        assert _leading_pause(_block(2, "c2", 10.0, 20.0)) is None


class TestFindJlPlacements:
    def test_placement_at_clip_change_between_speech_blocks(self):
        """A speech-to-speech boundary where the clip changes is placed."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((11.0, 12.0))),
        ]
        result = find_jl_placements(blocks)
        assert len(result) == 1
        cut = result[0]
        assert cut["cut_point_position"] == 2
        assert cut["timeline_position"] == 10.0
        assert cut["outgoing_clip_id"] == "c1"
        assert cut["incoming_clip_id"] == "c2"

    def test_longer_outgoing_pause_proposes_j_cut(self):
        """The J cut reaches back into the outgoing trailing pause."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((11.0, 12.0))),
        ]
        result = find_jl_placements(blocks)
        assert result[0]["kind"] == "j_cut"
        assert result[0]["offset_seconds"] == 4.0
        assert result[0]["outgoing_trailing_pause"] == 4.0
        assert result[0]["incoming_leading_pause"] == 1.0

    def test_longer_incoming_pause_proposes_l_cut(self):
        """The L cut reaches forward into the incoming leading pause."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (8.0, 9.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((13.0, 14.0))),
        ]
        result = find_jl_placements(blocks)
        assert result[0]["kind"] == "l_cut"
        assert result[0]["offset_seconds"] == 3.0
        assert result[0]["outgoing_trailing_pause"] == 1.0
        assert result[0]["incoming_leading_pause"] == 3.0

    def test_boundary_inside_one_clip_gets_nothing(self):
        """The same source continuing is not a speaker change."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c1", 10.0, 20.0, words=_words((11.0, 12.0))),
        ]
        assert find_jl_placements(blocks) == []

    def test_boundary_with_no_pause_on_either_side_gets_nothing(self):
        """No measured room means a cut would trim speech - no placement."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (9.0, 10.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((10.0, 11.0))),
        ]
        assert find_jl_placements(blocks) == []

    def test_boundary_with_non_speech_outgoing_gets_nothing(self):
        """A J/L cut is an audio handoff between two speakers."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=[]),
            _block(2, "c2", 10.0, 20.0, words=_words((13.0, 14.0))),
        ]
        assert find_jl_placements(blocks) == []

    def test_only_outgoing_pause_proposes_j_cut(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((10.0, 11.0))),
        ]
        result = find_jl_placements(blocks)
        assert result[0]["kind"] == "j_cut"
        assert result[0]["offset_seconds"] == 4.0
        assert result[0]["incoming_leading_pause"] == 0.0

    def test_only_incoming_pause_proposes_l_cut(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (9.0, 10.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((14.0, 15.0))),
        ]
        result = find_jl_placements(blocks)
        assert result[0]["kind"] == "l_cut"
        assert result[0]["offset_seconds"] == 4.0
        assert result[0]["outgoing_trailing_pause"] == 0.0

    def test_equal_pauses_propose_j_cut_and_say_so(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (6.0, 7.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((13.0, 14.0))),
        ]
        result = find_jl_placements(blocks)
        assert result[0]["kind"] == "j_cut"
        assert "equally supported" in result[0]["reason"]

    def test_placement_records_the_measurements_it_rested_on(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((11.0, 12.0))),
        ]
        result = find_jl_placements(blocks)
        cut = result[0]
        assert "outgoing trailing pause 4.000s" in cut["reason"]
        assert "incoming leading pause 1.000s" in cut["reason"]

    def test_multiple_boundaries_each_get_a_placement(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((11.0, 12.0))),
            _block(3, "c3", 20.0, 30.0, words=_words((21.0, 22.0))),
        ]
        result = find_jl_placements(blocks)
        assert len(result) == 2
        assert result[0]["cut_point_position"] == 2
        assert result[1]["cut_point_position"] == 3

    def test_blocks_without_clip_id_are_skipped(self):
        blocks = [
            _block(1, "", 0.0, 10.0, words=_words((0.0, 1.0), (5.0, 6.0))),
            _block(2, "c2", 10.0, 20.0, words=_words((11.0, 12.0))),
        ]
        assert find_jl_placements(blocks) == []

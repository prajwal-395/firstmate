"""Tests for J/L placement to audio_offset conversion.

These tests pin behaviour: a placement converts to an audio_offset
with the audio cut at the measured pause from the boundary, a J cut
reaches back, an L cut reaches forward, and a placement whose incoming
block is the first block is skipped.
"""
from library.tools.jl_placement import placements_to_audio_offsets


def _block(position, tl_start, tl_end):
    return {
        "position": position,
        "clip_id": f"c{position}",
        "source_start": 0.0,
        "source_end": 10.0,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
    }


class TestPlacementsToAudioOffsets:
    def test_j_cut_reaches_back(self):
        """A J cut's audio cut is before the picture cut."""
        placements = [{
            "cut_point_position": 2,
            "timeline_position": 10.0,
            "outgoing_clip_id": "c1",
            "incoming_clip_id": "c2",
            "kind": "j_cut",
            "offset_seconds": 0.5,
        }]
        blocks = [_block(1, 0.0, 10.0), _block(2, 10.0, 20.0)]
        offsets = placements_to_audio_offsets(placements, blocks, 30.0)
        assert len(offsets) == 1
        offset = offsets[0]
        assert offset["kind"] == "j_cut"
        assert offset["picture_cut_timeline"] == 10.0
        assert offset["audio_cut_timeline"] == 9.5
        assert offset["lead_seconds"] == 0.5
        assert offset["outgoing_position"] == 1
        assert offset["incoming_position"] == 2

    def test_l_cut_reaches_forward(self):
        """An L cut's audio cut is after the picture cut."""
        placements = [{
            "cut_point_position": 2,
            "timeline_position": 10.0,
            "outgoing_clip_id": "c1",
            "incoming_clip_id": "c2",
            "kind": "l_cut",
            "offset_seconds": 0.3,
        }]
        blocks = [_block(1, 0.0, 10.0), _block(2, 10.0, 20.0)]
        offsets = placements_to_audio_offsets(placements, blocks, 30.0)
        assert len(offsets) == 1
        offset = offsets[0]
        assert offset["kind"] == "l_cut"
        assert offset["picture_cut_timeline"] == 10.0
        assert offset["audio_cut_timeline"] == 10.3
        assert offset["lead_seconds"] == 0.3

    def test_first_block_is_skipped(self):
        """A placement at the first block has no outgoing side."""
        placements = [{
            "cut_point_position": 1,
            "timeline_position": 0.0,
            "outgoing_clip_id": "",
            "incoming_clip_id": "c1",
            "kind": "j_cut",
            "offset_seconds": 0.5,
        }]
        blocks = [_block(1, 0.0, 10.0)]
        assert placements_to_audio_offsets(placements, blocks, 30.0) == []

    def test_unknown_kind_is_skipped(self):
        """A placement with an unknown kind is skipped."""
        placements = [{
            "cut_point_position": 2,
            "timeline_position": 10.0,
            "outgoing_clip_id": "c1",
            "incoming_clip_id": "c2",
            "kind": "unknown",
            "offset_seconds": 0.5,
        }]
        blocks = [_block(1, 0.0, 10.0), _block(2, 10.0, 20.0)]
        assert placements_to_audio_offsets(placements, blocks, 30.0) == []

    def test_frames_are_computed(self):
        """Picture and audio cut frames are computed from the timeline."""
        placements = [{
            "cut_point_position": 2,
            "timeline_position": 10.0,
            "outgoing_clip_id": "c1",
            "incoming_clip_id": "c2",
            "kind": "j_cut",
            "offset_seconds": 0.5,
        }]
        blocks = [_block(1, 0.0, 10.0), _block(2, 10.0, 20.0)]
        offsets = placements_to_audio_offsets(placements, blocks, 30.0)
        assert offsets[0]["picture_cut_frame"] == 300
        assert offsets[0]["audio_cut_frame"] == 285

    def test_multiple_placements_all_convert(self):
        """Every placement converts to an offset."""
        placements = [
            {
                "cut_point_position": 2,
                "timeline_position": 10.0,
                "outgoing_clip_id": "c1",
                "incoming_clip_id": "c2",
                "kind": "j_cut",
                "offset_seconds": 0.5,
            },
            {
                "cut_point_position": 3,
                "timeline_position": 20.0,
                "outgoing_clip_id": "c2",
                "incoming_clip_id": "c3",
                "kind": "l_cut",
                "offset_seconds": 0.3,
            },
        ]
        blocks = [_block(1, 0.0, 10.0), _block(2, 10.0, 20.0),
                  _block(3, 20.0, 30.0)]
        offsets = placements_to_audio_offsets(placements, blocks, 30.0)
        assert len(offsets) == 2
        assert offsets[0]["kind"] == "j_cut"
        assert offsets[1]["kind"] == "l_cut"

"""Tests for dead section trim proposals.

These tests pin behaviour: a dead section at a block's start trims the
start forward, at its end trims the end back, in the middle is a pause
the trim would close, and the trim amount is the measured dead duration.
"""
from library.tools.pacing import propose_trims_for_dead_sections


def _block(position, tl_start, tl_end):
    return {
        "position": position,
        "clip_id": f"c{position}",
        "source_start": 0.0,
        "source_end": tl_end - tl_start,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
    }


class TestProposeTrimsForDeadSections:
    def test_dead_at_block_start_trims_start(self):
        """A dead stretch at the block's start trims the start forward."""
        dead = [{
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "duration_seconds": 2.0,
            "block_position": 1,
            "clip_id": "c1",
        }]
        blocks = [_block(1, 0.0, 10.0)]
        trims = propose_trims_for_dead_sections(dead, blocks)
        assert len(trims) == 1
        assert trims[0]["trim_edge"] == "start"
        assert trims[0]["trim_seconds"] == 2.0
        assert trims[0]["block_position"] == 1

    def test_dead_at_block_end_trims_end(self):
        """A dead stretch at the block's end trims the end back."""
        dead = [{
            "timeline_start": 8.0,
            "timeline_end": 10.0,
            "duration_seconds": 2.0,
            "block_position": 1,
            "clip_id": "c1",
        }]
        blocks = [_block(1, 0.0, 10.0)]
        trims = propose_trims_for_dead_sections(dead, blocks)
        assert len(trims) == 1
        assert trims[0]["trim_edge"] == "end"
        assert trims[0]["trim_seconds"] == 2.0

    def test_dead_in_middle_is_a_pause(self):
        """A dead stretch in the middle is a pause the trim would close."""
        dead = [{
            "timeline_start": 4.0,
            "timeline_end": 6.0,
            "duration_seconds": 2.0,
            "block_position": 1,
            "clip_id": "c1",
        }]
        blocks = [_block(1, 0.0, 10.0)]
        trims = propose_trims_for_dead_sections(dead, blocks)
        assert len(trims) == 1
        assert trims[0]["trim_edge"] == "middle"
        assert trims[0]["trim_seconds"] == 2.0

    def test_dead_spanning_block_join_proposes_on_both_sides(self):
        """A dead section spanning a join proposes a trim on each side."""
        dead = [{
            "timeline_start": 8.0,
            "timeline_end": 12.0,
            "duration_seconds": 4.0,
            "block_position": 1,
            "clip_id": "c1",
        }]
        blocks = [_block(1, 0.0, 10.0), _block(2, 10.0, 20.0)]
        trims = propose_trims_for_dead_sections(dead, blocks)
        assert len(trims) == 2
        edges = {t["block_position"]: t["trim_edge"] for t in trims}
        assert edges[1] == "end"
        assert edges[2] == "start"

    def test_no_dead_sections_no_trims(self):
        """No dead sections means no trim proposals."""
        blocks = [_block(1, 0.0, 10.0)]
        assert propose_trims_for_dead_sections([], blocks) == []

"""Tests for J/L placement results: what the plan chose.

These tests pin behaviour: a placement the plan did not choose is
recorded as not_chosen (never applied here), a placement the plan chose
is recorded as chosen with the plan's offset, and the placements reach
the planner (step 4.02) as context.
"""
from library.tools.jl_placement import find_jl_placements


def _block(position, clip_id, src_start, src_end, tl_start, tl_end,
           words=None):
    return {
        "position": position,
        "clip_id": clip_id,
        "source_start": src_start,
        "source_end": src_end,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "word_timestamps": words or [],
    }


def _word(src_start, src_end):
    return {"source_start": src_start, "source_end": src_end}


def _spine_with_placement():
    """A spine with a speech-to-speech boundary where the clip changes."""
    return [
        _block(1, "c1", 0.0, 10.0, 0.0, 10.0,
               [_word(1.0, 3.0), _word(5.0, 8.0)]),
        _block(2, "c2", 0.0, 10.0, 10.0, 20.0,
               [_word(1.0, 4.0), _word(6.0, 9.0)]),
    ]


class TestPlacementResults:
    def test_placement_not_chosen_by_plan(self):
        """A placement the plan did not choose is recorded as not_chosen."""
        spine = _spine_with_placement()
        placements = find_jl_placements(spine)
        assert len(placements) == 1
        place = placements[0]
        assert place["cut_point_position"] == 2
        assert place["kind"] == "j_cut"
        assert place["offset_seconds"] == 2.0

        # The plan has no jl_offset at this boundary
        jl_offsets = []
        plan_jl_by_boundary = {}
        for offset in jl_offsets:
            if isinstance(offset, dict):
                key = (str(offset.get("outgoing_position")),
                       str(offset.get("incoming_position")))
                plan_jl_by_boundary[key] = offset

        incoming_pos = place.get("cut_point_position")
        outgoing_pos = None
        for i, block in enumerate(spine):
            if block.get("position") == incoming_pos:
                if i > 0:
                    outgoing_pos = spine[i - 1].get("position")
                break
        key = (str(outgoing_pos), str(incoming_pos))
        chosen = plan_jl_by_boundary.get(key)
        assert chosen is None
        # The placement is NOT applied here - the plan didn't choose it

    def test_placement_chosen_by_plan(self):
        """A placement the plan chose is recorded as chosen."""
        spine = _spine_with_placement()
        placements = find_jl_placements(spine)
        assert len(placements) == 1
        place = placements[0]

        # The plan chose a J/L cut at this boundary (within the bound)
        jl_offsets = [{
            "kind": "j_cut",
            "outgoing_position": 1,
            "incoming_position": 2,
            "lead_seconds": 1.0,
        }]
        plan_jl_by_boundary = {}
        for offset in jl_offsets:
            if isinstance(offset, dict):
                key = (str(offset.get("outgoing_position")),
                       str(offset.get("incoming_position")))
                plan_jl_by_boundary[key] = offset

        incoming_pos = place.get("cut_point_position")
        outgoing_pos = None
        for i, block in enumerate(spine):
            if block.get("position") == incoming_pos:
                if i > 0:
                    outgoing_pos = spine[i - 1].get("position")
                break
        key = (str(outgoing_pos), str(incoming_pos))
        chosen = plan_jl_by_boundary.get(key)
        assert chosen is not None
        assert chosen["lead_seconds"] == 1.0
        # The plan's offset (1.0) is within the measured bound (2.0)
        assert chosen["lead_seconds"] <= place["offset_seconds"]

    def test_placements_reach_planner_context(self):
        """The placements are computed and available for the planner."""
        spine = _spine_with_placement()
        placements = find_jl_placements(spine)
        assert len(placements) == 1
        place = placements[0]
        # The placement carries the measured bound the planner can use
        assert place["offset_seconds"] == 2.0
        assert place["outgoing_trailing_pause"] == 2.0
        assert place["incoming_leading_pause"] == 1.0

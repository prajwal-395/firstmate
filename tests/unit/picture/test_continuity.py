"""Tests for screen-direction continuity.

These tests pin behaviour: a subject jumping from one side of the
frame to the other at a cut is flagged as a potential line cross, a
subject staying on the same side is not, an unmeasurable position on
either side is skipped, and a centred subject is not a side.
"""
from library.tools.continuity import (
    direction_cell_for_cut,
    direction_cells_for_cuts,
    find_screen_direction_issues,
    _side,
)


def _block(position, clip_id, src_start, src_end, tl_start, tl_end):
    return {
        "position": position,
        "clip_id": clip_id,
        "source_start": src_start,
        "source_end": src_end,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "word_timestamps": [],
    }


def _temporal_index(face_center_x_values):
    return {
        "face_presence": {
            "values": [1.0] * len(face_center_x_values),
            "face_center_x": face_center_x_values,
            "sample_rate_hz": 1.0,
        }
    }


class TestSide:
    def test_left(self):
        assert _side(0.2) == "left"

    def test_right(self):
        assert _side(0.8) == "right"

    def test_centre_inside_deadband(self):
        assert _side(0.5) == "centre"
        assert _side(0.47) == "centre"
        assert _side(0.53) == "centre"


class TestFindScreenDirectionIssues:
    def test_flip_is_flagged(self):
        """A subject jumping left to right at a cut is a potential cross."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": _temporal_index([0.8] * 10),
        }
        result = find_screen_direction_issues(blocks, indices)
        assert len(result) == 1
        issue = result[0]
        assert issue["cut_point_position"] == 2
        assert issue["timeline_position"] == 10.0
        assert issue["outgoing_side"] == "left"
        assert issue["incoming_side"] == "right"
        assert issue["outgoing_position"] == 0.2
        assert issue["incoming_position"] == 0.8
        assert "potential line cross" in issue["reason"]

    def test_same_side_is_not_flagged(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": _temporal_index([0.3] * 10),
        }
        assert find_screen_direction_issues(blocks, indices) == []

    def test_unmeasurable_outgoing_side_is_skipped(self):
        """No measurement, no finding - a shrug is not a side."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": {},
            "c2": _temporal_index([0.8] * 10),
        }
        assert find_screen_direction_issues(blocks, indices) == []

    def test_unmeasurable_incoming_side_is_skipped(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": {},
        }
        assert find_screen_direction_issues(blocks, indices) == []

    def test_centre_on_either_side_is_not_a_flip(self):
        """Centred is not a side, so no flip."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.5] * 10),
            "c2": _temporal_index([0.8] * 10),
        }
        assert find_screen_direction_issues(blocks, indices) == []

    def test_block_without_clip_id_is_skipped(self):
        blocks = [
            _block(1, "", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {"c2": _temporal_index([0.8] * 10)}
        assert find_screen_direction_issues(blocks, indices) == []

    def test_multiple_flips_are_all_reported(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
            _block(3, "c3", 0.0, 10.0, 20.0, 30.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": _temporal_index([0.8] * 10),
            "c3": _temporal_index([0.1] * 10),
        }
        result = find_screen_direction_issues(blocks, indices)
        assert len(result) == 2
        assert result[0]["cut_point_position"] == 2
        assert result[1]["cut_point_position"] == 3


class TestDirectionCellsForCuts:
    """The planner context cells: every cut reads a cell, not just flips.

    A flip cell states both measured sides; a held side is stated, not
    emptied; an unmeasurable side is `unmeasured`, never a held side.
    """

    def test_flip_cell_states_both_sides(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": _temporal_index([0.8] * 10),
        }
        assert direction_cells_for_cuts(blocks, indices) == {
            "2": "left -> right (potential line cross)",
        }

    def test_held_side_is_stated_not_emptied(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": _temporal_index([0.3] * 10),
        }
        assert direction_cells_for_cuts(blocks, indices) == {
            "2": "left -> left",
        }

    def test_centred_side_is_not_a_flip(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {
            "c1": _temporal_index([0.5] * 10),
            "c2": _temporal_index([0.8] * 10),
        }
        assert direction_cells_for_cuts(blocks, indices) == {
            "2": "centre -> right",
        }

    def test_unmeasurable_side_is_unmeasured(self):
        """No measurement reads `unmeasured`, never a held side."""
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
        ]
        indices = {"c2": _temporal_index([0.8] * 10)}
        assert direction_cells_for_cuts(blocks, indices) == {
            "2": "unmeasured",
        }

    def test_cells_are_keyed_by_cut_position(self):
        blocks = [
            _block(1, "c1", 0.0, 10.0, 0.0, 10.0),
            _block(2, "c2", 0.0, 10.0, 10.0, 20.0),
            _block(3, "c3", 0.0, 10.0, 20.0, 30.0),
        ]
        indices = {
            "c1": _temporal_index([0.2] * 10),
            "c2": _temporal_index([0.8] * 10),
            "c3": _temporal_index([0.1] * 10),
        }
        cells = direction_cells_for_cuts(blocks, indices)
        assert cells == {
            "2": "left -> right (potential line cross)",
            "3": "right -> left (potential line cross)",
        }

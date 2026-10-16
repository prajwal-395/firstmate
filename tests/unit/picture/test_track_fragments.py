"""OBJ-016/018: a track's sampled frames read as runs and explicit gaps.

`track_fragments` is pure: no model, no footage. A continuous track is
one run with no gaps; a track that loses its object mid-clip reports
two runs and the gap between them; a track with no geometry is a
whole-clip gap (unmeasured, never invisible). Positions outside the
clip refuse rather than misplacing every gap they would name.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis.object_segmentation import track_fragments


def test_continuous_track_is_one_run_with_no_gaps():
    report = track_fragments([0, 1, 2, 3], 4)
    assert report["runs"] == [(0, 3)]
    assert report["gaps"] == []
    assert report["fragment_count"] == 1
    assert report["longest_run"] == 4
    assert report["missing_samples"] == 0


def test_mid_clip_loss_reports_two_runs_and_the_gap():
    report = track_fragments([0, 1, 4, 5], 6)
    assert report["runs"] == [(0, 1), (4, 5)]
    assert report["gaps"] == [(2, 3)]
    assert report["fragment_count"] == 2
    assert report["longest_run"] == 2
    assert report["missing_samples"] == 2


def test_empty_track_is_a_whole_clip_gap():
    report = track_fragments([], 6)
    assert report["runs"] == []
    assert report["gaps"] == [(0, 5)]
    assert report["fragment_count"] == 0
    assert report["longest_run"] == 0
    assert report["missing_samples"] == 6


def test_leading_and_trailing_gaps_are_named():
    report = track_fragments([2, 3], 6)
    assert report["runs"] == [(2, 3)]
    assert report["gaps"] == [(0, 1), (4, 5)]


def test_unsorted_and_duplicate_positions_still_form_runs():
    report = track_fragments([3, 1, 2, 2, 0], 4)
    assert report["runs"] == [(0, 3)]
    assert report["gaps"] == []
    assert report["missing_samples"] == 0


@pytest.mark.parametrize("positions,total", [
    ([-1, 0], 4),
    ([0, 4], 4),
    ([0, "1"], 4),
    ([0, 1.0], 4),
    ([0, True], 4),
    ([0, 1], 0),
    ([0, 1], -3),
])
def test_out_of_clip_positions_refuse(positions, total):
    with pytest.raises(ValueError):
        track_fragments(positions, total)

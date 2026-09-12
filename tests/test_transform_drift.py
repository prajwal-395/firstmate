"""A built timeline that moved since its build is REPORTED, by factor.

The measurement these pin is in `library/tools/transform_drift.py`: the
project's "4x" is not a build-time defect - every affected reel's own
build snapshot holds the engine's computed value and the live timeline
holds a power of two times it. This module is the instrument that says
so, so what it must not do is agree with a timeline that moved.
"""

import pytest

from library.tools.transform_drift import (
    TransformDriftError, built_transforms, describe, drift_rows,
    uniform_factor,
)


def snapshot(*clips):
    """A `timeline_serializer`-shaped document carrying `clips`."""
    tracks = {}
    for index, start, pan, tilt, name in clips:
        tracks.setdefault(index, []).append(
            {"record_in": start, "name": name,
             "transform": {"Pan": pan, "Tilt": tilt, "ZoomX": 2.307}})
    return {"metadata": {"name": "Reel 01 - a"},
            "tracks": [{"type": "video", "index": index, "clips": clips_}
                       for index, clips_ in sorted(tracks.items())]}


BUILT = snapshot(
    (1, 590, 1.493, 0.25, "LC4930.MXF"),
    (1, 1069, 24.914, 0.25, "LC4932.MXF"),
    (2, 0, -29.651, 0.25, "LCATL0011.MXF"),
)


def held(*rows):
    return {(track, start): {"Pan": pan, "Tilt": tilt}
            for track, start, pan, tilt in rows}


def test_the_four_x_reads_as_one_factor_over_every_clip():
    """The shape actually found on Reels 01, 23, 28, 30 and 31."""
    rows = drift_rows(built_transforms(BUILT),
                      held((1, 590, 5.972, 1.0),
                           (1, 1069, 99.656, 1.0),
                           (2, 0, -118.604, 1.0)))
    assert [row["moved"] for row in rows] == [True, True, True]
    assert uniform_factor(rows) == pytest.approx(4.0)
    assert "every one by x4" in describe("Reel 01", rows)


def test_a_timeline_holding_what_the_build_wrote_reads_as_unmoved():
    """Reel 26: built last, never moved. The instrument must agree."""
    rows = drift_rows(built_transforms(BUILT),
                      held((1, 590, 1.493, 0.25),
                           (1, 1069, 24.914, 0.25),
                           (2, 0, -29.651, 0.25)))
    assert not any(row["moved"] for row in rows)
    assert uniform_factor(rows) is None
    assert "hold exactly what the build wrote" in describe("Reel 26", rows)


def test_resolves_read_back_noise_is_not_a_drift():
    """A built -35.0 reads back -35.000000000000036. That is not a move."""
    doc = snapshot((1, 129, -35.0, 0.25, "LC4932.MXF"))
    rows = drift_rows(built_transforms(doc),
                      held((1, 129, -35.000000000000036, 0.25)))
    assert not rows[0]["moved"]


def test_two_different_factors_refuse_to_read_as_one():
    """A non-uniform drift is a DIFFERENT fault and must not borrow this
    one's name - the measured drift is uniform per timeline."""
    rows = drift_rows(built_transforms(BUILT),
                      held((1, 590, 5.972, 1.0),      # x4
                           (1, 1069, 49.828, 0.5),    # x2
                           (2, 0, -29.651, 0.25)))
    assert uniform_factor(rows) is None
    assert "by NO single factor" in describe("Reel 01", rows)


def test_a_placement_the_timeline_no_longer_has_is_reported():
    """A clip that went away is a bigger finding than one that moved, so
    it is a row, never a silent drop."""
    rows = drift_rows(built_transforms(BUILT),
                      held((1, 590, 1.493, 0.25), (2, 0, -29.651, 0.25)))
    gone = [row for row in rows if row["missing"]]
    assert [(row["track"], row["start"]) for row in gone] == [(1, 1069)]
    assert "1 the timeline no longer has" in describe("Reel 01", rows)


def test_a_document_that_is_not_a_timeline_refuses():
    """An empty reading of an unreadable file is the silent pass."""
    with pytest.raises(TransformDriftError):
        built_transforms({"metadata": {"name": "Reel 01 - a"}})


def test_a_clip_carrying_no_transform_is_not_a_placement_to_judge():
    doc = {"metadata": {"name": "x"}, "tracks": [
        {"type": "video", "index": 4, "clips": [
            {"record_in": 0, "name": "sub_a.mov", "transform": {}},
            {"record_in": 9, "name": "sub_b.mov",
             "transform": {"Pan": 0.0, "Tilt": -864.0}}]}]}
    assert sorted(built_transforms(doc)) == [(4, 9)]

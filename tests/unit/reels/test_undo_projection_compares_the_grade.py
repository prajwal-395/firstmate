"""The equality projection must SEE the grade (Reel 26 audit §2.5).

Defect: a version record could not restore grade, Fusion and full
properties - and the equality projection that decides "does the live
timeline still read as the recorded one?" left CDL out, on the grounds
that a touch never carries it. That is true of a touch and false of
everything else: the captain grades a clip by hand in Resolve, and a
build re-grades it. A projection that cannot see the grade reads a
re-graded timeline as the same one, so an undo reverses over the
captain's grade and destroys it.

The version's ROWS remain a comparison record - they cannot restore a
colour - so what restores one is the immutable archive. These tests
hold both halves: the projection sees the grade, and the row summary is
never mistaken for restorable state.
"""

from __future__ import annotations

from library.tools import undo_journal


def _detail(**overrides):
    detail = {
        "track_type": "video",
        "track_index": 2,
        "row": "Craig",
        "record_in": 793,
        "record_out": 1013,
        "duration": 220,
        "left_offset": 11537,
        "source_file": "/media/LCATL0013.MXF",
        "name": "LCATL0013.MXF",
        "transform": {},
        "fusion": {"comp_count": 1},
        "clip_color": "",
        "enabled": True,
        "color": {"cdl": {"Slope": "1.0300 1.0000 0.9600",
                          "Offset": "-0.0100 0.0050 0.0200"}},
    }
    detail.update(overrides)
    return detail


def test_the_projection_carries_the_grade():
    """The grade is compared, never invented."""
    projection = undo_journal.item_projection(_detail())

    assert projection["grade"] == {
        "Slope": "1.0300 1.0000 0.9600",
        "Offset": "-0.0100 0.0050 0.0200",
    }


def test_a_different_grade_is_a_difference():
    """The defect: a re-graded timeline read as the recorded one."""
    before = undo_journal.projection([{"clips": [_detail()]}])
    after = undo_journal.projection([{"clips": [_detail(color={
        "cdl": {"Slope": "1.0000 1.0000 1.0000",
                "Offset": "0.0000 0.0000 0.0000"}})]}])

    difference = undo_journal.projection_diff(before, after)

    assert difference, "a changed grade must not read as the same timeline"


def test_an_unreadable_grade_is_not_a_difference():
    """Unknown stays unknown: a missing CDL is empty on both sides."""
    detail = _detail()
    detail.pop("color")
    projection = undo_journal.item_projection(detail)

    assert projection["grade"] == {}

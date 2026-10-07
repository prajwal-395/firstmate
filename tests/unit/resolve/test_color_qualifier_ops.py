"""Qualifier algebra ops: the eyedropper and Inspector slice.

`color.qualifier.sample` / `color.qualifier.adjust` are declared, NOT
executable - the pick and the widths are GUI-only - so these tests pin
the declaration's own behaviour: the sample-point contract, the shared
qualifier key, the clip-covering span, and patch exclusion. Nothing
here renders pixels, so no expensive-test reason is stated. (The window
slices land in companion PRs; this file stands alone on origin/main.)
"""

from __future__ import annotations

import pytest

from library.tools import edit_algebra, edit_patch


def _clip(uid, start, end):
    return {
        "unique_id": uid, "name": "a.mov", "track_type": "video",
        "track_index": 1, "track_name": "V1",
        "record_in": start, "record_out": end, "duration": end - start,
        "source_in_frame": start, "source_out_frame": end,
        "left_offset": start, "right_offset": 0,
        "source_file": "a.mov", "media_type": "Video",
        "source_frames": end - start, "media_pool_item_id": f"pool-{uid}",
        "clip_color": "", "flags": [], "enabled": True,
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0},
        "fusion": {"comp_count": 0, "comp_names": [], "media_windows": []},
        "markers": [], "speaker": None,
    }


def _base():
    return {
        "start_frame": 0, "end_frame": 100, "fps": 30.0,
        "width": 1920, "height": 1080, "clip_count": 1,
        "tracks": [
            {"type": "video", "index": 1, "name": "V1", "speaker": None,
             "clips": [_clip("a", 0, 50)]},
            {"type": "audio", "index": 1, "name": "A1", "speaker": None,
             "clips": []},
        ],
        "markers": {"timeline": [], "notes": []},
    }


def _sample(**extra):
    op = {"op": "color.qualifier.sample", "unique_id": "a", "node_index": 1,
          "point": [0.5, 0.5]}
    op.update(extra)
    return op


@pytest.mark.parametrize("point", [
    "centre", [0.5], [0.5, 0.5, 0.5], [2.0, 0.5], [-0.1, 0.5],
    ["x", 0.5], [True, 0.5], None,
])
def test_qualifier_sample_refuses_an_unclickable_point(point):
    """Defect: a point naming no frame pixel reaching the recipe would
    sample blind - the eyedropper clicks nothing. The key refuses
    non-pairs, out-of-range, non-numeric and missing points."""
    op = _sample(point=point) if point is not None else _sample()
    if point is None:
        del op["point"]
    with pytest.raises(ValueError, match="bad sample point"):
        edit_algebra.key(op)


def test_qualifier_sample_and_adjust_share_one_key():
    """Defect: divergent keys would let a sample and an adjust on the
    same node qualifier compose without conflict, ordering two writes
    to one control by accident."""
    sample_key = edit_algebra.key(_sample())
    adjust_key = edit_algebra.key(
        {"op": "color.qualifier.adjust", "unique_id": "a", "node_index": 1,
         "params": {"gain": 1.0}})
    assert sample_key == adjust_key == ("clip", "a", "qualifier", 1)


def test_qualifier_span_covers_the_clip():
    """Defect: a malformed span would fail patch validation or schedule
    the op against the wrong frames once an executor lands."""
    spec = edit_algebra.get("color.qualifier.sample")
    assert spec.span(_sample(), _base()) == (0, 50)


def test_qualifier_ops_stay_out_of_the_patch():
    """Defect: a GUI-only op scheduled into an EditPatch would pass
    validation and raise mid-apply. The patch view excludes both ops,
    so validation refuses them up front as it does grade.node_add."""
    assert "color.qualifier.sample" in edit_algebra.ALGEBRA
    assert "color.qualifier.adjust" in edit_algebra.ALGEBRA
    assert "color.qualifier.sample" not in edit_patch.OPERATIONS
    assert "color.qualifier.adjust" not in edit_patch.OPERATIONS
    assert "color.qualifier.sample" not in edit_algebra.patch_op_ids()
    assert "color.qualifier.adjust" not in edit_algebra.patch_op_ids()

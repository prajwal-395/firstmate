"""Power Window algebra ops: the declaration slice.

`color.window.create` / `color.window.delete` are declared, NOT
executable - the Graph API exposes no window calls - so these tests pin
the declaration's own behaviour: what it accepts, what key it writes,
what span it covers, and that no patch can schedule it. The recipe that
draws the window and the sampled-frame judgement land in later slices;
nothing here renders pixels, so no expensive-test reason is stated.
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


def _create(**extra):
    op = {"op": "color.window.create", "unique_id": "a", "node_index": 1,
          "shape": "circle", "geometry": {"center": [0.5, 0.5]}}
    op.update(extra)
    return op


def test_window_create_refuses_an_invented_shape():
    """Defect: a planner-invented shape reaching the recipe would hand
    the GUI driver a window it cannot draw. The key refuses it."""
    with pytest.raises(ValueError, match="bad window shape"):
        edit_algebra.key(_create(shape="blob"))


def test_window_create_refuses_a_missing_shape():
    """Defect: a defaulted shape would let the pipeline invent taste -
    which window isolates the subject is the planner's choice, so an
    unnamed shape is refused, never completed from a constant."""
    op = _create()
    del op["shape"]
    with pytest.raises(ValueError, match="bad window shape"):
        edit_algebra.key(op)


def test_window_create_and_delete_share_one_key():
    """Defect: divergent keys would let a create and a delete on the
    same node window compose without conflict, silently dropping the
    window the run just planned."""
    create_key = edit_algebra.key(_create())
    delete_key = edit_algebra.key(
        {"op": "color.window.delete", "unique_id": "a", "node_index": 1})
    assert create_key == delete_key == ("clip", "a", "window", 1)


def test_window_span_covers_the_clip():
    """Defect: a malformed span would fail patch validation or schedule
    the op against the wrong frames once an executor lands."""
    spec = edit_algebra.get("color.window.create")
    assert spec.span(_create(), _base()) == (0, 50)


def test_window_ops_stay_out_of_the_patch():
    """Defect: a GUI-only op scheduled into an EditPatch would pass
    validation and raise mid-apply. The patch view excludes both ops,
    so validation refuses them up front as it does grade.node_add."""
    assert "color.window.create" in edit_algebra.ALGEBRA
    assert "color.window.delete" in edit_algebra.ALGEBRA
    assert "color.window.create" not in edit_patch.OPERATIONS
    assert "color.window.delete" not in edit_patch.OPERATIONS
    assert "color.window.create" not in edit_algebra.patch_op_ids()
    assert "color.window.delete" not in edit_algebra.patch_op_ids()

"""Power Window modify/track ops: the reshape and tracker slice.

`color.window.modify` / `color.window.track` are declared, NOT
executable - reshaping and tracking are the same GUI route as creating
- so these tests pin the declaration's own behaviour: the direction
vocabulary, the shared window key, the clip-covering span, and patch
exclusion. Nothing here renders pixels, so no expensive-test reason is
stated. (The create/delete pair and its shape vocabulary land in the
companion slice; this file stands alone on origin/main.)
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


def _track(**extra):
    op = {"op": "color.window.track", "unique_id": "a", "node_index": 1,
          "direction": "forward"}
    op.update(extra)
    return op


def test_window_track_refuses_an_invented_direction():
    """Defect: the tracker offers Track Forward / Track Backward and no
    third button. An invented direction reaching the recipe would press
    a control that is not there. The key refuses it."""
    with pytest.raises(ValueError, match="bad track direction"):
        edit_algebra.key(_track(direction="both-ways"))


def test_window_track_refuses_a_missing_direction():
    """Defect: a defaulted direction would let the pipeline choose which
    way the subject is followed - a tracking decision without a decider.
    An unnamed direction is refused."""
    op = _track()
    del op["direction"]
    with pytest.raises(ValueError, match="bad track direction"):
        edit_algebra.key(op)


def test_window_track_speaks_the_measured_tracker_words():
    """Defect: a direction vocabulary drifting from the UI it drives -
    the Color page's tracker says Forward/Reverse (measured: Color >
    Tracker), so both words key one window and nothing else does."""
    assert edit_algebra.key(_track(direction="reverse")) == ("clip", "a", "window", 1)


def test_window_modify_shares_the_window_key():
    """Defect: a modify writing a different key than the track would let
    a reshape and a track on the same node window compose without
    conflict, ordering two writes to one control by accident."""
    modify_key = edit_algebra.key(
        {"op": "color.window.modify", "unique_id": "a", "node_index": 1,
         "geometry": {"center": [0.4, 0.5]}})
    assert modify_key == edit_algebra.key(_track()) == ("clip", "a", "window", 1)


def test_window_track_span_covers_the_clip():
    """Defect: a malformed span would fail patch validation or schedule
    the op against the wrong frames once an executor lands."""
    spec = edit_algebra.get("color.window.track")
    assert spec.span(_track(), _base()) == (0, 50)


def test_window_modify_and_track_stay_out_of_the_patch():
    """Defect: a GUI-only op scheduled into an EditPatch would pass
    validation and raise mid-apply. The patch view excludes both ops,
    so validation refuses them up front as it does grade.node_add."""
    assert "color.window.modify" in edit_algebra.ALGEBRA
    assert "color.window.track" in edit_algebra.ALGEBRA
    assert "color.window.modify" not in edit_patch.OPERATIONS
    assert "color.window.track" not in edit_patch.OPERATIONS
    assert "color.window.modify" not in edit_algebra.patch_op_ids()
    assert "color.window.track" not in edit_algebra.patch_op_ids()

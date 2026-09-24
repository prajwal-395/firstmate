"""The reels-path look: what it refuses, and what it places.

`library/tools/reel_look.py` is the reels path's route to the declared
TV-frame look.  These cover the two things that cost real time on
2026-09-09: a frame asset whose aspect cannot frame the delivery
reaching a render, and the frame's own track under per-speaker picture
rows (captain's ruling on Reel 09: two picture rows, the set above
them, no collapse).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import reel_look
from library.tools import tv_frame


@dataclass
class _Clip:
    source_file: str
    track_type: str = "video"
    timeline_start: float = 0.0
    source_in: float = 0.0


def _placement(source_file, record_frame, seconds, fps=24.0):
    return {
        "clip": _Clip(source_file),
        "source_in": 0.0,
        "source_out": seconds,
        "record": record_frame / fps,
        "snapped_record": record_frame,
        "speaker": "Craig",
    }


def _png(path, size, window=True):
    """A frame asset. `window=False` is a slate - opaque everywhere."""
    from PIL import Image
    image = Image.new("RGBA", size, (10, 10, 10, 255))
    if window:
        # A transparent window with an opaque rail either side, which is
        # the shape the captain's own asset measures (opaque columns
        # 0-723 and 3091-3840 of 3840, transparent between).
        inset = size[0] // 5
        for x in range(inset, size[0] - inset):
            for y in range(0, size[1], 4):
                image.putpixel((x, y), (0, 0, 0, 0))
    image.save(path)
    return str(path)


def _look(path):
    return {"asset": path, "punch_in": 2.3, "power": {},
            "origin": "test declaration"}


def test_a_landscape_frame_in_a_portrait_reel_is_cover_scaled(tmp_path):
    """The captain's own working configuration, and the number it derives.

    An earlier version of this refused a mismatched aspect outright. The
    captain disproved it by hand on 2026-09-09: same 3840x2160 asset,
    same 1080x1920 reel, zoom 3.16, and it framed. So a mismatched
    aspect is COVER-SCALED, and the zoom is derived from the two sizes -
    nothing in the engine holds 3.16.
    """
    asset = _png(tmp_path / "TV 4k.png", (3840, 2160), window=True)
    tv_frame.assert_frameable(_look(asset), 1080, 1920)
    zoom = tv_frame.cover_zoom((3840, 2160), 1080, 1920)
    assert zoom == pytest.approx(3.1605, abs=0.001)
    # The captain's own statement of the rule, for a wider-than-frame
    # asset, must give the same answer as the symmetric form above.
    assert zoom == pytest.approx((1920 / 1080) / (2160 / 3840), abs=1e-9)


def test_a_frame_whose_cover_would_upscale_it_is_refused(tmp_path):
    """The first thing that genuinely cannot be framed."""
    asset = _png(tmp_path / "small.png", (540, 960), window=True)
    with pytest.raises(ValueError) as excinfo:
        tv_frame.assert_frameable(_look(asset), 1080, 1920)
    message = str(excinfo.value)
    assert "upscale" in message
    assert "540x960" in message and "1080x1920" in message


def test_a_frame_with_no_window_is_refused(tmp_path):
    """The second: a frame with no window is a slate over the picture."""
    asset = _png(tmp_path / "slate.png", (3840, 2160), window=False)
    with pytest.raises(ValueError) as excinfo:
        tv_frame.assert_frameable(_look(asset), 1080, 1920)
    assert "slate" in str(excinfo.value)


def test_sequential_placements_frame_as_one_run():
    fps = 24.0
    placements = [
        _placement("/a.mxf", 0, 5.0, fps),
        _placement("/b.mxf", 120, 5.0, fps),
    ]
    # One contiguous run, because the two abut exactly - read off the
    # placements as placed, with no collapse onto one row first.
    assert reel_look.frame_runs(placements, fps) == [(0, 240)]


def test_power_effects_land_on_the_first_and_last_picture():
    effects = reel_look.power_effects(
        {"power": {}}, reel_look.clip_label(0), reel_look.clip_label(2))
    assert effects[reel_look.clip_label(0)]["tv_power_head"] is True
    assert effects[reel_look.clip_label(2)]["tv_power_tail"] is True
    assert "tv_power_tail" not in effects[reel_look.clip_label(0)]


def test_an_unanswered_motion_ask_is_not_an_empty_plan(tmp_path):
    resolved, record = reel_look.resolve_motion(None, {"structure": []}, 24.0)
    assert resolved == []
    assert record["basis"] == reel_look.MOTION_AWAITING_ANSWER
    resolved, record = reel_look.resolve_motion([], {"structure": []}, 24.0)
    assert record["basis"] == reel_look.MOTION_PLANNED_NONE


def test_a_reasoned_drift_resolves_and_reaches_the_manifest():
    fps = 24.0
    placements = [_placement("/a.mxf", 0, 10.0, fps)]
    spine = reel_look.motion_spine(placements, fps)
    resolved, record = reel_look.resolve_motion(
        [{"target_block_position": 0, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.04},
          "rationale": "this shot narrows onto one figure"}],
        spine, fps)
    assert record["basis"] == reel_look.MOTION_PLANNED
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, resolved, fps)
    effects = manifest["fusion_effects"]["per_clip"][reel_look.clip_label(0)]
    assert effects["_preset"] == "slow_zoom_in"
    assert effects["zoom_end"] == 1.04
    # The switch animation rides the same clip, which is what the
    # comp builder reads both from.
    assert effects["tv_power_head"] is True
    assert manifest["tracks"]["V1"]["clips"][0]["source_file"] == "/a.mxf"


class _FakeItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, key):
        return self._path if key == "File Path" else ""


class _FakeFolder:
    def __init__(self, clips=None, subs=None):
        self._clips = list(clips or [])
        self._subs = list(subs or [])

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _FakePool:
    """Counts imports, because the defect was invisible from the timeline."""

    def __init__(self, existing=()):
        self.root = _FakeFolder(subs=[_FakeFolder(
            [_FakeItem(p) for p in existing])])
        self.imports = []

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        self.imports.append(list(paths))
        item = _FakeItem(paths[0])
        self.root.GetSubFolderList()[0]._clips.append(item)
        return [item]


def test_a_file_already_in_the_pool_is_not_imported_again():
    """The pool is asked BEFORE anything is imported.

    Measured 2026-09-09: the field-test project's "not placed on any
    timeline" bin held 96 copies of 12 motion-graphic files - eight of
    each, one per build attempt - because every build re-imported the
    overlays it had imported before. Nested folders are searched, since
    that is where a filed pool puts them.
    """
    from library.tools.reel_build import import_pool_item, pool_item_for

    pool = _FakePool(existing=["/x/vox_00.mov"])
    assert pool_item_for(pool, "/x/vox_00.mov") is not None
    assert pool_item_for(pool, "/x/absent.mov") is None

    found = import_pool_item(pool, "/x/vox_00.mov")
    assert found is not None
    assert pool.imports == [], "an item already in the pool was re-imported"

    fresh = import_pool_item(pool, "/x/vox_01.mov")
    assert fresh is not None
    assert pool.imports == [["/x/vox_01.mov"]]

    # And a second build of the same reel imports nothing at all.
    import_pool_item(pool, "/x/vox_01.mov")
    assert pool.imports == [["/x/vox_01.mov"]]


class _Subject:
    def __init__(self, cx, cy, others=0):
        self.center_x = cx
        self.center_y = cy
        self.width = 0.1
        self.samples = 12
        self.detected = 12
        self.others = others


def test_no_subject_means_no_punch_in():
    """The captain's ruling: refuse rather than guess.

    A centred 2.30 on 3840x2160 footage shows the middle 43% of the
    width, so a crop with nothing aiming it is a bet on where the
    speaker is standing. Returning None here is what makes the shot
    play uncropped instead.
    """
    assert reel_look.punch_in_properties(
        {"punch_in": 2.3}, None, 3840, 2160, 1080, 1920) is None


def test_the_punch_in_is_aimed_at_the_measured_subject():
    window = _window(90)
    props = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.30, 0.32), 3840, 2160, 1080, 1920,
        window=window)
    # A subject left of centre pulls the picture right, and vice versa.
    assert props["Pan"] > 0
    other = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.70, 0.32), 3840, 2160, 1080, 1920,
        window=window)
    assert other["Pan"] < 0


def test_the_aim_never_uncovers_the_screen_window():
    """Clamped to the WINDOW, which is what the viewer can see through.

    Clamping to the delivery frame would let the aim pull the picture off
    an edge of the screen while still covering the frame - black inside
    the television, which is the defect the post-condition now catches.
    """
    window = _window(90)
    far = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.01, 0.99), 3840, 2160, 1080, 1920,
        window=window)
    reel_look.assert_covers_window(far, 3840, 2160, 1080, 1920, window)


def test_a_landscape_frame_is_turned_upright_for_a_portrait_delivery():
    """The captain's instruction, and what the turn buys.

    Turned, a 3840x2160 asset is 2160x3840 against a 1080x1920 reel -
    the same aspect - so it needs no cover zoom at all and downscales by
    half. Judged on the ORIENTED size, because judging the cover before
    the turn refuses an asset that fits perfectly after it.
    """
    look = {"rotate": tv_frame.AUTO_ROTATE}
    assert tv_frame.applied_rotation(look, (3840, 2160), 1080, 1920) == 90
    assert tv_frame.oriented_size((3840, 2160), 90) == (2160, 3840)
    assert tv_frame.cover_zoom((2160, 3840), 1080, 1920) == pytest.approx(1.0)


def test_a_partial_turn_is_refused():
    """A frame turns in quarters or not at all."""
    with pytest.raises(ValueError) as excinfo:
        tv_frame.validate_rotation(45, "test declaration")
    assert "quarters" in str(excinfo.value)
    with pytest.raises(TypeError):
        tv_frame.validate_rotation("sideways", "test declaration")


def _window(look_rotate, asset=(3840, 2160)):
    """The screen window of the captain's asset at a given rotation."""
    import unittest.mock as mock
    look = {"asset": "TV 4k.png", "rotate": look_rotate}
    with mock.patch.object(tv_frame, "screen_window",
                           return_value=(519, 37, 3322, 2123)):
        return tv_frame.screen_window_rect(look, 1080, 1920, asset_size=asset)


def test_the_picture_covers_the_SCREEN_WINDOW_not_the_frame():
    """The rule the captain had to state twice.

    Covering the delivery frame and covering the television's screen are
    different targets. Turned upright the window is 1043x1402 timeline
    pixels and needs 2.3070; unturned it is 2491x1853 and needs 3.05.
    The difference between those is the whole of what rotating fixed.
    """
    turned = _window(90)
    assert (round(turned[2] - turned[0]), round(turned[3] - turned[1])) \
        == (1043, 1402)
    needed = tv_frame.window_cover_zoom(3840, 2160, turned, 1080, 1920)
    assert needed == pytest.approx(2.307, abs=0.001)

    flat = _window(0)
    assert tv_frame.window_cover_zoom(3840, 2160, flat, 1080, 1920) \
        == pytest.approx(3.05, abs=0.01)


def test_a_punch_in_leaving_black_in_the_screen_is_refused():
    """The post-condition, on the exact geometry that shipped.

    Unrotated and cover-scaled, the declared 2.30 leaves 228px of black
    above and below the picture INSIDE the television's screen. That
    reached the captain twice before anything measured it.
    """
    flat = _window(0)
    with pytest.raises(reel_look.PunchInLeavesBlack) as excinfo:
        reel_look.assert_covers_window(
            {"ZoomX": 2.3, "ZoomY": 2.3, "Pan": 0.0, "Tilt": 0.0},
            3840, 2160, 1080, 1920, flat)
    message = str(excinfo.value)
    assert "top 227" in message and "bottom 228" in message


def test_the_window_is_a_required_argument():
    """Covering the frame instead is the defect; it cannot be defaulted."""
    with pytest.raises(ValueError) as excinfo:
        reel_look.punch_in_properties(
            {"punch_in": 2.3}, _Subject(0.5, 0.32), 3840, 2160, 1080, 1920)
    assert "screen window" in str(excinfo.value)


def _upright_frame_project(tmp_path):
    """A project folder whose frame asset already matches the delivery.

    2160x3840 against a 1080x1920 reel, so no turn and no cover zoom:
    the render is small and the test measures identity, not geometry.
    """
    asset = _png(tmp_path / "TV 4k.png", (2160, 3840), window=True)
    look = {"asset": asset, "punch_in": 2.3, "power": {},
            "origin": "test declaration",
            "rotate": tv_frame.AUTO_ROTATE}
    return look


def _rendered_frames(path):
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames", "-of", "csv=p=0",
         str(path)],
        capture_output=True, encoding="utf-8", check=True)
    return int(out.stdout.strip())


def _overlay_dir(project_folder):
    from library.tools.project_layout import Area, ProjectLayout
    return os.path.join(
        str(ProjectLayout(str(project_folder)).read_dir(Area.SCRATCH)),
        "reel_look", "frame_overlays")


def test_the_overlay_name_shape_matches_shared_and_legacy_renders():
    """The verifier reads live timelines that still carry per-length names.

    The shared render (`tv_frame_<stamp>`) must match, and the legacy
    per-length renders (`tv_frame_<stamp>_<N>f`) must keep matching
    until the captain re-points those timelines - a shape that dropped
    either would misread the set as an out-of-band overlay or miss it.
    """
    import re
    shape = re.compile(reel_look.FRAME_OVERLAY_NAME_SHAPE)
    assert shape.match("tv_frame_1f8e8d06ff")
    assert shape.match("tv_frame_1f8e8d06ff_950f")
    assert not shape.match("tv_frame_1f8e8d06ff_950f_tight")
    assert not shape.match("vox_00")

    from types import SimpleNamespace
    items = [
        SimpleNamespace(track_index=2,
                        source_file="/s/tv_frame_1f8e8d06ff.mov"),
        SimpleNamespace(track_index=2,
                        source_file="/s/tv_frame_1f8e8d06ff_950f.mov"),
        SimpleNamespace(track_index=1,
                        source_file="/s/tv_frame_1f8e8d06ff.mov"),
    ]
    found = reel_look.frame_overlay_items(items, {"punch_in": 2.3})
    assert len(found) == 2

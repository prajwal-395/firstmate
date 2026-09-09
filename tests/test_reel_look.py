"""The reels-path look: what it refuses, and what it places.

`library/tools/reel_look.py` is the reels path's route to the declared
TV-frame look.  These cover the two things that cost real time on
2026-09-09: a frame asset whose aspect cannot frame the delivery
reaching a render, and picture placements collapsing onto V1 without
anyone checking whether two of them overlap.
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


def test_cover_scale_answers_the_other_direction_too():
    """A TALLER-than-frame asset fits by height and covers by width."""
    assert tv_frame.cover_zoom((1080, 4320), 1920, 1080) == pytest.approx(
        (1920 / 1080) / (1080 / 4320) / ((1920 / 1080) / (1080 / 4320)) * 4.0,
        rel=1.0)
    # Stated plainly rather than through the captain's ratio, which only
    # holds when the fit is by width: fit 1080/4320 = 0.25 by height,
    # cover 1920/1080 = 1.7778 by width.
    assert tv_frame.cover_zoom((1080, 4320), 1920, 1080) == pytest.approx(
        (1920 / 1080) / (1080 / 4320), rel=1e-9)


def test_a_frame_at_the_delivery_aspect_needs_no_cover_zoom(tmp_path):
    asset = _png(tmp_path / "portrait.png", (2160, 3840), window=True)
    tv_frame.assert_frameable(_look(asset), 1080, 1920)
    assert tv_frame.cover_zoom((2160, 3840), 1080, 1920) == pytest.approx(1.0)


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


def test_no_declaration_refuses_nothing():
    tv_frame.assert_frameable(None, 1080, 1920)


def test_the_frame_overlay_is_rendered_at_its_cover_size():
    """Rendered at the size it is DRAWN at, so nothing upscales it.

    The first version rendered a fitted band padded to the delivery
    frame; zooming that to cover would have upscaled a 1080-wide render
    3.16 times, which is exactly what the upscale refusal exists to
    stop happening to the asset itself.
    """
    drawn = tv_frame.cover_size((3840, 2160), 1080, 1920)
    assert drawn[1] == 1920
    assert drawn[0] >= 1080
    # Even on both axes, because ffmpeg's encoders reject an odd one.
    assert drawn[0] % 2 == 0 and drawn[1] % 2 == 0


def test_overlapping_picture_placements_refuse_the_collapse():
    fps = 24.0
    placements = [
        _placement("/a.mxf", 0, 10.0, fps),
        # Starts before the first one ends: collapsing both onto V1
        # would leave Resolve to trim one, which is the placer choosing
        # a camera.
        _placement("/b.mxf", 120, 10.0, fps),
    ]
    with pytest.raises(reel_look.ReelLookRefused) as excinfo:
        reel_look.assert_one_picture_at_a_time(placements, fps)
    assert "choosing a camera" in str(excinfo.value)


def test_sequential_placements_collapse_onto_v1():
    fps = 24.0
    placements = [
        _placement("/a.mxf", 0, 5.0, fps),
        _placement("/b.mxf", 120, 5.0, fps),
    ]
    reel_look.assert_one_picture_at_a_time(placements, fps)
    collapsed = reel_look.collapse_to_v1(placements)
    assert [p["track_index"] for p in collapsed] == [1, 1]
    # One contiguous run, because the two abut exactly.
    assert reel_look.frame_runs(collapsed, fps) == [(0, 240)]


def test_frame_runs_break_where_the_picture_does():
    fps = 24.0
    placements = [
        _placement("/a.mxf", 0, 5.0, fps),
        _placement("/b.mxf", 240, 5.0, fps),
    ]
    assert reel_look.frame_runs(placements, fps) == [(0, 120), (240, 360)]


def test_power_effects_land_on_the_first_and_last_picture():
    effects = reel_look.power_effects(
        {"power": {}}, reel_look.clip_label(0), reel_look.clip_label(2))
    assert effects[reel_look.clip_label(0)]["tv_power_head"] is True
    assert effects[reel_look.clip_label(2)]["tv_power_tail"] is True
    assert "tv_power_tail" not in effects[reel_look.clip_label(0)]


def test_declared_zoom_multiplies_the_projects_own_framing():
    assert reel_look.declared_zoom_over(1.0, None) == 1.0
    assert reel_look.declared_zoom_over(
        1.0, {"punch_in": 2.3}) == pytest.approx(2.3)
    assert reel_look.declared_zoom_over(
        1.5, {"punch_in": 2.0}) == pytest.approx(3.0)


def test_an_unanswered_motion_ask_is_not_an_empty_plan(tmp_path):
    resolved, record = reel_look.resolve_motion(None, {"structure": []}, 24.0)
    assert resolved == []
    assert record["basis"] == reel_look.MOTION_AWAITING_ANSWER
    resolved, record = reel_look.resolve_motion([], {"structure": []}, 24.0)
    assert record["basis"] == reel_look.MOTION_PLANNED_NONE


def test_motion_with_no_stated_reason_is_dropped():
    spine = {"structure": [
        {"position": 0, "timeline_start": 0.0, "timeline_end": 10.0}]}
    resolved, record = reel_look.resolve_motion(
        [{"target_block_position": 0, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.04}}],
        spine, 24.0)
    assert resolved == []
    assert record["basis"] == reel_look.MOTION_EVERY_ENTRY_DROPPED
    assert record["dropped"][0]["reason"] == "no_stated_reason"


def test_ken_burns_without_a_direction_is_dropped():
    spine = {"structure": [
        {"position": 0, "timeline_start": 0.0, "timeline_end": 10.0}]}
    resolved, record = reel_look.resolve_motion(
        [{"target_block_position": 0, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.0},
          "rationale": "the shot wants to breathe"}],
        spine, 24.0)
    assert resolved == []
    assert record["dropped"][0]["reason"] == "ken_burns_without_direction"


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


def test_more_than_one_subject_means_no_punch_in():
    """A two-shot is not a close-up, and "largest face" is not "speaker"."""
    two = _Subject(0.5, 0.32, others=1)
    assert reel_look.punch_in_properties(
        {"punch_in": 2.3}, two, 3840, 2160, 1080, 1920) is None


def test_the_punch_in_is_aimed_at_the_measured_subject():
    props = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.30, 0.32), 3840, 2160, 1080, 1920)
    # The zoom is the DECLARATION's, untouched: how far to punch in is
    # not this function's decision.
    assert props["ZoomX"] == 2.3 and props["ZoomY"] == 2.3
    # A subject left of centre pulls the picture right.
    assert props["Pan"] > 0
    # And a subject right of centre pulls it the other way.
    other = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.70, 0.32), 3840, 2160, 1080, 1920)
    assert other["Pan"] < 0


def test_the_aim_never_uncovers_an_edge():
    """Clamped to the room the picture has, in both axes.

    At 2.30 a 16:9 source in a 9:16 frame is WIDER than the frame and
    SHORTER than it, so there is horizontal room and none vertically -
    which is why Tilt is 0 here and is a measurement rather than an
    omission.
    """
    far = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.01, 0.99), 3840, 2160, 1080, 1920)
    fit = min(1080 / 3840, 1920 / 2160)
    shown_w = 3840 * fit * 2.3
    shown_h = 2160 * fit * 2.3
    assert abs(far["Pan"]) <= (shown_w - 1080) / 2.0 + 1e-6
    assert shown_h < 1920
    assert far["Tilt"] == 0.0


def test_a_taller_punch_in_gets_vertical_room():
    """The tilt is arithmetic, not a constant zero.

    A punch-in past the fill ceiling makes the picture taller than the
    frame, and then aiming vertically is possible - so the same code
    returns a tilt.
    """
    props = reel_look.punch_in_properties(
        {"punch_in": 3.6}, _Subject(0.5, 0.30), 3840, 2160, 1080, 1920)
    assert props["Tilt"] > 0


def test_the_aim_bound_is_what_the_verifier_checks():
    """`aim_room` and the clamp are one rule, so F12 can grade it.

    The verifier cannot re-run the face measurement, so it checks the
    property that matters instead: the aim moved the picture no further
    than the picture could afford.
    """
    from library.tools.reel_framing import delivered_picture

    props = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.10, 0.32), 3840, 2160, 1080, 1920)
    delivered = delivered_picture(3840, 2160, 1080, 1920, props)
    pan_room, tilt_room = reel_look.aim_room(delivered, 1080, 1920)
    assert abs(props["Pan"]) <= pan_room + 1
    assert abs(props["Tilt"]) <= tilt_room + 1

"""Tests for the layout coordinate model.

These tests name the defects:
1. The logo placed by local-pixel coordinates on a scaled parent
   lands offset (reproduce today's 300 px).
2. Absolute placement round-trips to the measured canvas box.
3. A quarter/original unit mismatch is refused.
"""

import pytest

from library.tools.layout_model import (
    LayoutItem,
    LayoutModel,
    LayoutModelError,
    Transform,
    reproduce_300px_offset,
)


class TestLocalToCanvas:
    def test_item_no_parent_local_centre_is_canvas_centre(self):
        model = LayoutModel(1920, 1080)
        item = LayoutItem(1920, 1080, Transform())
        cx, cy = model.local_to_canvas(item, 960, 540)
        assert cx == 960
        assert cy == 540

    def test_item_no_parent_pan_moves_right(self):
        model = LayoutModel(1920, 1080)
        item = LayoutItem(1920, 1080, Transform(pan=192.0))
        cx, cy = model.local_to_canvas(item, 960, 540)
        assert cx == 960 + 192.0
        assert cy == 540

    def test_item_no_parent_tilt_moves_up(self):
        model = LayoutModel(1920, 1080)
        item = LayoutItem(1920, 1080, Transform(tilt=108.0))
        cx, cy = model.local_to_canvas(item, 960, 540)
        assert cx == 960
        assert cy == 540 - 108.0

    def test_scaled_parent_child_lands_offset(self):
        model = LayoutModel(1920, 1080)
        frame = LayoutItem(
            1920, 1080,
            Transform(pan=0, tilt=-300, zoom_x=0.5, zoom_y=0.5),
            label="frame")
        logo = LayoutItem(
            200, 200,
            Transform(zoom_x=1.0, zoom_y=1.0),
            parent=frame, label="logo")
        cx, cy = model.local_to_canvas(logo, 960, 540)
        assert cx != 960 or cy != 540


class TestCanvasToLocal:
    def test_round_trip_no_parent(self):
        model = LayoutModel(1920, 1080)
        item = LayoutItem(
            1920, 1080,
            Transform(pan=100, tilt=-200, zoom_x=1.5, zoom_y=1.5))
        canvas_x, canvas_y = model.local_to_canvas(item, 500, 300)
        local_x, local_y = model.canvas_to_local(item, canvas_x, canvas_y)
        assert abs(local_x - 500) < 1e-6
        assert abs(local_y - 300) < 1e-6

    def test_round_trip_with_parent(self):
        model = LayoutModel(1920, 1080)
        frame = LayoutItem(
            1920, 1080,
            Transform(pan=50, tilt=-100, zoom_x=0.8, zoom_y=0.8),
            label="frame")
        logo = LayoutItem(
            200, 200,
            Transform(pan=10, tilt=-20, zoom_x=1.0, zoom_y=1.0),
            parent=frame, label="logo")
        canvas_x, canvas_y = model.local_to_canvas(logo, 100, 50)
        local_x, local_y = model.canvas_to_local(logo, canvas_x, canvas_y)
        assert abs(local_x - 100) < 1e-6
        assert abs(local_y - 50) < 1e-6


class TestComputeTransform:
    def test_compute_transform_round_trips_to_placed_box(self):
        model = LayoutModel(1920, 1080)
        item = LayoutItem(200, 100, Transform())
        box = (860, 490, 1060, 590)
        transform = model.compute_transform(item, box)
        item_with_transform = LayoutItem(
            200, 100, transform)
        placed = model.placed_box(item_with_transform)
        assert abs(placed[0] - box[0]) < 1.0
        assert abs(placed[1] - box[1]) < 1.0
        assert abs(placed[2] - box[2]) < 1.0
        assert abs(placed[3] - box[3]) < 1.0

    def test_compute_transform_zoom(self):
        model = LayoutModel(1920, 1080)
        item = LayoutItem(200, 100, Transform())
        box = (860, 490, 1060, 590)
        transform = model.compute_transform(item, box)
        assert abs(transform.zoom_x - 1.0) < 1e-6
        assert abs(transform.zoom_y - 1.0) < 1e-6


class TestUnitMismatch:
    def test_matching_units_pass(self):
        model = LayoutModel(1920, 1080,
                           current_pan_units="original",
                           current_tilt_units="original")
        transform = Transform(pan=100, tilt=100,
                              pan_units="original",
                              tilt_units="original")
        assert model.refuse_unit_mismatch(transform) is None

    def test_quarter_vs_original_refused(self):
        model = LayoutModel(1920, 1080,
                           current_pan_units="quarter",
                           current_tilt_units="quarter")
        transform = Transform(pan=100, tilt=100,
                              pan_units="original",
                              tilt_units="original")
        reason = model.refuse_unit_mismatch(transform)
        assert reason is not None
        assert "quarter" in reason
        assert "original" in reason

    def test_original_vs_quarter_refused(self):
        model = LayoutModel(1920, 1080,
                           current_pan_units="original",
                           current_tilt_units="original")
        transform = Transform(pan=400, tilt=400,
                              pan_units="quarter",
                              tilt_units="quarter")
        reason = model.refuse_unit_mismatch(transform)
        assert reason is not None


class TestReproduce300pxOffset:
    def test_offset_is_non_zero(self):
        offset_x, offset_y = reproduce_300px_offset()
        assert offset_x != 0 or offset_y != 0

    def test_offset_magnitude(self):
        offset_x, offset_y = reproduce_300px_offset()
        assert abs(offset_y) > 100


class TestLayoutModelError:
    def test_zero_canvas_width_raises(self):
        with pytest.raises(LayoutModelError):
            LayoutModel(0, 1080)

    def test_zero_canvas_height_raises(self):
        with pytest.raises(LayoutModelError):
            LayoutModel(1920, 0)

    def test_negative_canvas_raises(self):
        with pytest.raises(LayoutModelError):
            LayoutModel(-1920, 1080)

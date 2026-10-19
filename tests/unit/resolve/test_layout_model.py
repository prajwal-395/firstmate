"""Tests for the layout coordinate model.

These tests name the defects:
1. The logo placed by local-pixel coordinates on a scaled parent
   lands offset (reproduce today's 300 px).
2. Absolute placement round-trips to the measured canvas box.
3. A quarter/original unit mismatch is refused.
"""

import pytest

from library.tools.layout_model import (
    GeometryDescriptor,
    LayoutItem,
    LayoutModel,
    LayoutModelError,
    Transform,
    current_proxy_units,
    reproduce_300px_offset,
)
from library.tools.resolve_transform import (
    native_position_to_resolve,
    resolve_to_native_position,
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
    def test_geometry_descriptor_round_trips_with_native_projection(self):
        descriptor = GeometryDescriptor(
            frame=(1080, 1920), box=(740, 90, 980, 260),
            canvas=(480, 340), anchor=(1.0, 0.0), parent="brand-root",
            native_projection={"Pan": 1500, "Tilt": 500,
                               "pan_units": "quarter"},
            source_size=(960, 540), crop=(.05, .1, .05, .1),
            rotation=12, asset_mode="full_frame")
        assert GeometryDescriptor.from_dict(
            descriptor.to_dict()) == descriptor

    def test_output_geometry_moves_exactly_40_pixels(self):
        model = LayoutModel(1080, 1920)
        item = LayoutItem(1080, 1920, Transform())
        box = (40, 0, 1120, 1920)
        transform = model.compute_transform(item, box)
        placed = model.placed_box(LayoutItem(1080, 1920, transform))
        assert placed == pytest.approx(box)
        assert placed[0] == pytest.approx(40)

    def test_geometry_and_pan_tilt_round_trip(self):
        frame = (1080, 1920)
        canvas = (728, 480)
        centre = (842.5, 317.25)
        pan_tilt = native_position_to_resolve(
            centre, canvas, frame, draw_gain=1.0)
        assert resolve_to_native_position(
            {"Pan": pan_tilt[0], "Tilt": pan_tilt[1]},
            canvas, frame, draw_gain=1.0) == pytest.approx(centre)

    def test_crop_and_scale_geometry_round_trips(self):
        model = LayoutModel(1080, 1920)
        source = LayoutItem(800, 400, Transform())
        box = (190, 350, 1790, 1150)
        transform = model.compute_transform(source, box)
        assert transform.scaling == pytest.approx(1.0)  # native crop base
        assert transform.zoom_x == pytest.approx(2.0)
        assert transform.zoom_y == pytest.approx(2.0)
        placed = model.placed_box(LayoutItem(800, 400, transform))
        assert placed == pytest.approx(box)

    def test_source_crop_rotation_and_off_center_anchor_round_trip(self):
        model = LayoutModel(1920, 1080, "quarter", "quarter")
        source_size = (400, 200)
        crop = (.1, .1, .1, .1)
        box = (100, 200, 496.553829, 502.851252)
        transform = model.place_absolute_box(
            source_size, box, anchor=(.2, .8), rotation=30, crop=crop)
        item = LayoutItem(*source_size, transform)

        assert transform.zoom_x == pytest.approx(1.2)
        assert transform.zoom_y == pytest.approx(.8)
        assert model.placed_box(item) == pytest.approx(box)
        canvas_point = model.local_to_canvas(item, 43, 67)
        assert model.canvas_to_local(item, *canvas_point) == pytest.approx(
            (43, 67))

    def test_portrait_source_rotation_uses_output_frame_geometry(self):
        model = LayoutModel(3840, 2160, "half", "half")
        transform = model.place_absolute_box(
            (200, 400), (1500, 800, 1900, 1000), rotation=90)
        assert model.placed_box(LayoutItem(200, 400, transform)) == \
            pytest.approx((1500, 800, 1900, 1000))

    def test_three_level_parent_chain_round_trips(self):
        model = LayoutModel(1920, 1080)
        canvas_parent = LayoutItem(
            1920, 1080, Transform(pan=20, tilt=-30, zoom_x=.8, zoom_y=.8))
        nested_parent = LayoutItem(
            960, 540, Transform(pan=-10, tilt=15, zoom_x=.5, zoom_y=.5),
            parent=canvas_parent)
        child = LayoutItem(
            200, 100, Transform(pan=5, tilt=-8, zoom_x=1.2, zoom_y=1.2),
            parent=nested_parent)
        canvas_point = model.local_to_canvas(child, 75, 40)
        assert model.canvas_to_local(child, *canvas_point) == pytest.approx(
            (75, 40))

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

    @pytest.mark.parametrize(
        ("units", "factor"),
        [("original", 1), ("half", 2), ("quarter", 4),
         ("eighth", 8)],
    )
    def test_absolute_box_uses_delivery_timeline_and_current_proxy_units(
            self, units, factor):
        # The project is 4K landscape, while the reel timeline is its own
        # 1080x1920 delivery canvas. Placement is computed from the latter.
        model = LayoutModel(1080, 1920, units, units)
        source = LayoutItem(200, 80)
        box = (800, 100, 1000, 180)
        transform = model.compute_transform(source, box)
        assert transform.pan == pytest.approx(1944 / factor)
        assert transform.tilt == pytest.approx(19680 / factor)
        assert model.refuse_unit_mismatch(transform) is None
        placed = model.placed_box(LayoutItem(200, 80, transform))
        assert placed == pytest.approx(box)


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

    @pytest.mark.parametrize("ratio", ["Original", "Half", "Quarter", "Eighth"])
    def test_reads_active_proxy_ratio(self, ratio):
        class Project:
            def GetSetting(self, key):
                assert key == "perfProxyResolutionRatio"
                return ratio

        assert current_proxy_units(Project()) == ratio.lower()

    def test_unknown_proxy_ratio_is_refused(self):
        class Project:
            def GetSetting(self, _key):
                return "mystery"

        with pytest.raises(LayoutModelError, match="unknown"):
            current_proxy_units(Project())

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

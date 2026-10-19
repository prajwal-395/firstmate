from __future__ import annotations

import pytest
from PIL import Image

from library.tools.layout_model import UnitMismatchError
from library.tools.reel_graphics import (
    ReelGraphicsError,
    assert_before_end,
    assert_timeline_media_online,
    assert_tight_alpha,
    assert_tight_overlay_media,
    corner_brand_bug_segment,
)


def test_full_canvas_overlay_is_rejected(tmp_path):
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    image.putpixel((20, 20), (255, 255, 255, 255))
    path = tmp_path / "full_canvas.png"
    image.save(path)

    with pytest.raises(ReelGraphicsError, match="not tight-boxed"):
        assert_tight_alpha(str(path))


def test_padded_png_requires_a_matching_tight_canvas_and_placement(tmp_path):
    image = Image.new("RGBA", (728, 480), (0, 0, 0, 0))
    image.putpixel((24, 16), (255, 255, 255, 255))
    path = tmp_path / "padded_tight.png"
    image.save(path)
    box = {"width": 728, "height": 480,
           "placement": {"pan": 100, "tilt": 200, "scaling": 1}}

    assert assert_tight_overlay_media(str(path), box) == (728, 480)
    with pytest.raises(ReelGraphicsError, match="declares 400x480"):
        assert_tight_overlay_media(
            str(path), {**box, "width": 400})
    with pytest.raises(ReelGraphicsError, match="not tight-boxed"):
        assert_tight_overlay_media(str(path))


def test_corner_bug_is_a_still_with_absolute_canvas_box(tmp_path):
    image = Image.new("RGBA", (120, 40), (255, 255, 255, 255))
    path = tmp_path / "brand.png"
    image.save(path)

    segment = corner_brand_bug_segment(
        str(path), (1080, 1920), (776, 204, 896, 244),
        100, 200, 240, fps=30,
        pan_units="quarter", tilt_units="quarter")

    assert segment["media_type"] == "still"
    assert segment["absolute_canvas_box"] == [776.0, 204.0, 896.0, 244.0]
    assert segment["tight_box"]["placement"]["ZoomX"] == 1.0
    assert segment["total_frames"] == 100
    assert segment["tight_box"]["placement"]["pan_units"] == "quarter"


def test_corner_bug_cannot_overlap_ending(tmp_path):
    image = Image.new("RGBA", (20, 10), (255, 255, 255, 255))
    path = tmp_path / "brand.png"
    image.save(path)

    with pytest.raises(ReelGraphicsError, match="overlaps reel ending"):
        corner_brand_bug_segment(
            str(path), (1080, 1920), (900, 100, 920, 110),
            100, 241, 240, fps=30)


def test_corner_brand_bug_box_scales_from_the_asset_bounds(tmp_path):
    image = Image.new("RGBA", (546, 471), (255, 255, 255, 255))
    path = tmp_path / "brand.png"
    image.save(path)
    segment = corner_brand_bug_segment(
        str(path), (1080, 1920), (876, 574, 986, 661),
        0, 1032, 1032, fps=24000 / 1001)

    placement = segment["tight_box"]["placement"]
    assert placement["ZoomX"] == pytest.approx(110 / 546)
    assert placement["ZoomY"] == pytest.approx(87 / 471)
    assert segment["tight_box"]["anchor"] == [1.0, 0.0]


def test_corner_brand_bug_reads_the_project_box_and_requires_png(tmp_path):
    import yaml
    from library.tools.reel_graphics import declared_corner_brand_bug

    asset = tmp_path / "logo.png"
    Image.new("RGBA", (40, 20), (255, 255, 255, 255)).save(asset)
    (tmp_path / "project.yaml").write_text(yaml.safe_dump({
        "effect": {"corner_brand_bug": {
            "asset": "logo.png", "box": [876, 574, 986, 661],
            "anchor": "top_right"}}}), encoding="utf-8")

    declaration = declared_corner_brand_bug(str(tmp_path))
    assert declaration == {
        "asset": str(asset), "box": [876.0, 574.0, 986.0, 661.0],
        "anchor": "top_right"}


def test_layout_model_refuses_a_proxy_unit_mismatch():
    from library.tools.layout_model import LayoutItem, LayoutModel, Transform
    from library.tools.reel_graphics import require_current_units

    model = LayoutModel(1080, 1920, "quarter", "quarter")
    transform = Transform(pan=10, tilt=10, pan_units="original",
                          tilt_units="original")
    item = LayoutItem(120, 40, transform=transform)
    with pytest.raises(UnitMismatchError):
        require_current_units(model, item.transform)


def test_an_overlay_ending_exactly_at_boundary_is_allowed():
    assert_before_end(10, 20, 20, "brand bug")


def test_tv_off_hold_uses_source_media_without_a_freeze_render():
    from library.tools.reel_ending import (
        FreezeTail, freeze_placement, is_freeze_path,
    )

    freeze = FreezeTail(
        held_from="/project/media/closing-shot.mov",
        held_source_seconds=3.0, source_fps=30.0, held_source_frame=90,
        reel_start_frame=300, duration_frames=24, element="tv_power_tail",
        track_index=1)
    placement = freeze_placement(freeze, 30.0)

    assert placement["native_hold"] is True
    assert placement["clip"].source_file == freeze.held_from
    assert not is_freeze_path(placement["clip"].source_file)


def test_offline_media_in_reel_timeline_is_rejected(tmp_path):
    class PoolItem:
        def GetClipProperty(self, key):
            return {"Offline": "true",
                    "File Path": str(tmp_path / "missing.mov")}[key]

    class Item:
        def GetMediaPoolItem(self):
            return PoolItem()

        def GetName(self):
            return "A-roll"

    class Timeline:
        def GetTrackCount(self, media_type):
            return 1 if media_type == "video" else 0

        def GetItemListInTrack(self, media_type, track):
            return [Item()]

    with pytest.raises(ReelGraphicsError, match="A-roll reports Offline"):
        assert_timeline_media_online(Timeline())


def test_declared_header_crop_retains_shadow_padding_and_canvas_floor(tmp_path):
    image = Image.new("RGBA", (728, 480), (0, 0, 0, 0))
    image.paste((255, 255, 255, 255), (24, 16, 704, 203))
    path = tmp_path / "header.png"
    image.save(path)
    declaration = {"width": 728, "height": 480,
                   "canvas_box": [96, 261, 824, 741]}
    assert assert_tight_alpha(str(path), tight_box=declaration) == (728, 480)
    with pytest.raises(ReelGraphicsError, match="not tight-boxed"):
        assert_tight_alpha(str(path))
    with pytest.raises(ReelGraphicsError, match="not tight-boxed"):
        assert_tight_alpha(str(path), tight_box={**declaration, "width": 729})


def test_post_header_segment_carries_its_measured_canvas_box(monkeypatch):
    from library.tools import reel_post_header

    canvas = (96, 261, 824, 741)
    monkeypatch.setattr(reel_post_header, "tight_still",
                        lambda *_: ("header_tight.png", canvas))
    monkeypatch.setattr(reel_post_header, "placement_for",
                        lambda *_: {"scaling": 1, "pan": 0, "tilt": 0})
    segments = reel_post_header._tight_segments(
        "header.png", [(0, 100)], 30, (1080, 1920), 1)

    assert len(segments) == 1
    assert segments[0]["tight_box"]["canvas_box"] == list(canvas)
    assert segments[0]["tight_box"]["absolute_canvas_box"] == list(canvas)

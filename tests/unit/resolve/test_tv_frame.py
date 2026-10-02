"""The punched-in TV-frame look is declarable, never hardcoded.

The captain's marker on Reel 20 (frame 538, 2026-09-09) sets the look:
V1 punched to 2.30 under the TV asset at native 1:1, captions above.
`library/tools/tv_frame.py` declares that - the factor, the asset, the
layer order - and this asserts the declarations behave: 2.30 is the
default the project or template can change, no asset means no look, and
a malformed declaration raises rather than degrading silently.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import pytest
import yaml

from library.tools import reel_look, tv_frame
from library.tools.resolve_transform import shift_px

from library.tools.tv_frame import (
    DEFAULT_PUNCH_IN,
    MAX_PUNCH_IN,
    MIN_PUNCH_IN,
    resolve_asset_path,
    resolve_tv_frame,
    screen_window,
    validate_punch_in,
)


def _project(tmp_path, pipeline_block=None, template_name=None):
    folder = tmp_path / "proj"
    folder.mkdir(exist_ok=True)
    pipeline = dict(pipeline_block or {})
    if template_name is not None:
        pipeline["brand_template"] = template_name
    (folder / "project.yaml").write_text(
        yaml.safe_dump({"pipeline": pipeline}), encoding="utf-8")
    return str(folder)


def _asset(tmp_path, name="frame.png"):
    from PIL import Image
    p = tmp_path / name
    im = Image.new("RGBA", (64, 48), (20, 20, 20, 255))
    px = im.load()
    for y in range(8, 40):
        for x in range(10, 54):
            px[x, y] = (0, 0, 0, 0)
    im.save(p)
    return str(p)


def _template(tv_frame):
    from library.schemas.brand_template import (
        BrandTemplate, ContentSlots, EffectSlots, StyleSlots)
    return BrandTemplate(series_id="t", style=StyleSlots(tv_frame=tv_frame),
                         effect=EffectSlots(), content=ContentSlots())


def test_no_asset_means_no_look(tmp_path):
    """A project declaring nothing gets nothing - there is no fallback
    bezel - and a zoom with no frame is not the look either: it resolves
    to no look rather than punching the footage for no reason."""
    assert resolve_tv_frame(_project(tmp_path), None) is None
    folder = _project(tmp_path, {"tv_frame": {"punch_in": 2.0}})
    assert resolve_tv_frame(folder, None) is None


def test_the_declaration_resolves_project_over_template_over_standard(
        tmp_path):
    asset = _asset(tmp_path)
    # Undeclared factor: the 2.30 standard, origin named.
    resolved = resolve_tv_frame(
        _project(tmp_path, {"tv_frame": {"asset": asset}}), None)
    assert resolved["punch_in"] == DEFAULT_PUNCH_IN
    assert resolved["asset"] == os.path.abspath(asset)
    assert "project.yaml" in resolved["origin"]
    # A declared factor overrides it.
    folder = _project(tmp_path,
                      {"tv_frame": {"asset": asset, "punch_in": 1.8}})
    assert resolve_tv_frame(folder, None)["punch_in"] == 1.8
    # The project beats the template.
    proj_asset = _asset(tmp_path, "proj.png")
    tmpl_asset = _asset(tmp_path, "tmpl.png")
    folder = _project(
        tmp_path, {"tv_frame": {"asset": proj_asset, "punch_in": 1.5}})
    resolved = resolve_tv_frame(
        folder, _template({"asset": tmpl_asset, "punch_in": 2.9}))
    assert resolved["punch_in"] == 1.5
    assert resolved["asset"] == os.path.abspath(proj_asset)
    # The template alone declares the look.
    resolved = resolve_tv_frame(_project(tmp_path), _template({"asset": asset}))
    assert resolved["punch_in"] == DEFAULT_PUNCH_IN
    assert "template t" in resolved["origin"]
    # A relative asset is project artwork (section 14): it resolves
    # against the project folder, never against the engine.
    folder = _project(tmp_path, {"tv_frame": {"asset": "frame.png"}})
    (Path(folder) / "frame.png").write_bytes(Path(asset).read_bytes())
    assert resolve_tv_frame(folder, None)["asset"] == os.path.abspath(
        os.path.join(folder, "frame.png"))


def test_a_malformed_declaration_raises_rather_than_degrading(tmp_path):
    """Including `power.switch_on`/`switch_off` - the shape until
    2026-09-11: honouring one would re-time one direction and leave the
    other behind - and a collapse of 0.5, which closes the band entirely
    (the one-frame flash)."""
    asset = _asset(tmp_path)
    rows = (
        ({"asset": "/no/such/frame.png"}, FileNotFoundError),
        ({"asset": asset, "punch_in": 0.5}, ValueError),
        ({"asset": asset, "punch_in": 9.0}, ValueError),
        ({"asset": asset, "bezel": "chrome"}, ValueError),
        ("TV 4k.png", TypeError),
        ({"asset": asset, "power": {"switch_on": {"collapse_frames": 2}}},
         ValueError),
        ({"asset": asset, "power": {"collapse_crop": 0.5}}, ValueError),
    )
    for declaration, error in rows:
        folder = _project(tmp_path, {"tv_frame": declaration})
        with pytest.raises(error):
            resolve_tv_frame(folder, None)
    with pytest.raises(TypeError):
        validate_punch_in("tight", "test")
    assert validate_punch_in(1.0, "test") == 1.0
    assert validate_punch_in(MAX_PUNCH_IN, "test") == MAX_PUNCH_IN
    assert MIN_PUNCH_IN == 1.0
    with pytest.raises(TypeError):
        resolve_asset_path("", str(tmp_path), "test")
    with pytest.raises(FileNotFoundError):
        resolve_asset_path("/no/such.png", str(tmp_path), "test")


def test_power_timings_travel_with_the_look(tmp_path):
    """The declaration names the SHAPE, played both ways: re-timing the
    switch re-times switch-on and switch-off together (captain,
    2026-09-11); the collapse depth is declarable, not compiled. No
    power declaration carries no timings - the module's shape applies
    where the timings MERGE (reel_look, compile_manifest), not here."""
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {
        "asset": asset,
        "power": {"collapse_frames": 2, "collapse_crop": 0.30},
    }})
    power = resolve_tv_frame(folder, None)["power"]
    assert power["collapse_frames"] == 2
    assert power["dot_frames"] == 3
    assert power["decay_frames"] == 9
    assert power["collapse_crop"] == 0.30
    folder = _project(tmp_path, {"tv_frame": {"asset": asset}})
    assert resolve_tv_frame(folder, None)["power"] == {}


def test_screen_window_measures_the_transparent_hole(tmp_path):
    from PIL import Image
    assert screen_window(_asset(tmp_path)) == (10, 8, 54, 40)
    slate = tmp_path / "slate.png"
    Image.new("RGBA", (32, 32), (0, 0, 0, 255)).save(slate)
    with pytest.raises(ValueError):
        screen_window(str(slate))


def test_content_runs_split_around_cards():
    """The frame dresses the show, not the logo cards: one run per
    contiguous stretch of content, so no V2 frame clip ever overlaps a
    declared card."""
    from library.steps.step_5_04_compile_manifest.step import _content_runs
    clips = [
        {"timeline_in": 0.0, "timeline_out": 3.0, "bookend": "intro"},
        {"timeline_in": 3.0, "timeline_out": 7.0},
        {"timeline_in": 7.0, "timeline_out": 9.0},
        {"timeline_in": 9.0, "timeline_out": 12.0, "bookend": "end_card"},
        {"timeline_in": 12.0, "timeline_out": 14.0},
    ]
    runs = _content_runs(clips)
    assert [(r["timeline_in"], r["timeline_out"]) for r in runs] == [
        (3.0, 9.0), (12.0, 14.0)]
    assert [r["index"] for r in runs] == [0, 1]


# ── The frame and its window move and scale together ───────────────
#
# 2026-09-25: the captain moved the picture down for the post header
# (`tv_frame.offset_y`) and then shrank it so the platforms' side crop
# stops cutting the TV's edges. If the window and the bezel moved apart
# the picture would be aimed at a window the bezel no longer frames.


def _tv_asset_4k(tmp_path):
    from PIL import Image
    im = Image.new("RGBA", (3840, 2160), (0, 0, 0, 255))
    im.paste((0, 0, 0, 0), (200, 150, 3640, 2010))  # the screen window
    path = tmp_path / "tv.png"
    im.save(path)
    return str(path)


def _offset_look(asset, offset):
    return {"asset": asset, "punch_in": 2.3, "power": None,
            "rotate": "auto", "offset_y": offset, "origin": "test"}


def test_window_and_bezel_move_down_by_the_same_pixels(tmp_path):
    asset = _tv_asset_4k(tmp_path)
    base = tv_frame.screen_window_rect(_offset_look(asset, 0), 1080, 1920)
    moved = tv_frame.screen_window_rect(_offset_look(asset, 220), 1080, 1920)
    assert moved[1] - base[1] == 220 and moved[3] - base[3] == 220
    assert moved[0] == base[0] and moved[2] == base[2]

    props = reel_look.frame_properties(_offset_look(asset, 220), 1080, 1920,
                                       draw_gain=1.0)
    # Down is a negative Tilt; its drawn shift is the window's shift.
    assert props["Tilt"] < 0
    assert round(shift_px(-props["Tilt"], 1920, 1920, draw_gain=1.0)) == 220
    assert "Tilt" not in reel_look.frame_properties(_offset_look(asset, 0),
                                                    1080, 1920)


def test_a_scaled_look_shrinks_bezel_window_and_picture_together(tmp_path):
    # 2026-09-25: the captain asked for the picture to shrink so the
    # platforms' side crop stops cutting the TV's edges. The window and
    # the picture's punch-in shrink by ONE factor about the frame's
    # centre, and the bezel is drawn smaller INSIDE an overlay whose
    # surround is opaque black - zooming the overlay instead left the
    # delivery's edges uncovered and the picture, wider than the window,
    # showed there (the first shrink previews).
    import subprocess

    from PIL import Image

    asset = _tv_asset_4k(tmp_path)
    full = dict(_offset_look(asset, 220), scale=1.0)
    small = dict(_offset_look(asset, 220), scale=0.8)
    cx, cy = 540, 960 + 220
    big = tv_frame.screen_window_rect(full, 1080, 1920)
    shrunk = tv_frame.screen_window_rect(small, 1080, 1920)
    for a, b, c in zip(big, shrunk, (cx, cy, cx, cy)):
        assert abs((b - c) - 0.8 * (a - c)) < 1e-6
    assert reel_look.declared_zoom_over(1.0, small) == \
        0.8 * reel_look.declared_zoom_over(1.0, full)
    assert reel_look.frame_properties(small, 1080, 1920, draw_gain=1.0) == \
        reel_look.frame_properties(full, 1080, 1920, draw_gain=1.0)

    (segment,) = reel_look.frame_overlay_segments(
        small, [(0, 1)], 24.0, 1080, 1920, str(tmp_path))
    still = tmp_path / "overlay.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i",
                    segment["overlay_path"], "-frames:v", "1", str(still)],
                   check=True)
    with Image.open(still) as image:
        alpha = image.convert("RGBA").getchannel("A")
        width, height = image.size
        # Just outside the shrunk bezel, left of centre: black, opaque.
        assert alpha.getpixel((int(width * 0.05), height // 2)) == 255
        # The window itself still shows the picture.
        assert alpha.getpixel((width // 2, height // 2)) == 0

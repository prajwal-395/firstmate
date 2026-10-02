"""The measured tight binding for motion graphics: bound from pixels.

`mg_tight_box.tighten_measured_mg_with_reason` sizes the canvas from a
MEASURED ink union - never from the text and the style, the way the
predicted path does and gets wrong. `measure_mg_union` reads that
union off a rendered file, and `verify_measured_crop` proves the
tight crop IS its probe's region rather than asserting it.

Synthetic and small; the ffmpeg tests need it (CI installs it,
AGENTS.md 9). Nothing reaches Resolve or a real project.
"""
import os
import shutil
import subprocess
import sys

import pytest

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import mg_tight_box as mgt  # noqa: E402
from library.tools.tight_box import (  # noqa: E402
    InkUnion,
    TightBoxClipsInk,
    TightBoxMismatch,
    canvas_offset,
    crop_probe_to_tight,
)


FULL_W, FULL_H = 1080, 1920
SAFE = {"top": 120, "right": 120, "bottom": 804, "left": 90}


def _props(elements):
    return {
        "elements": elements,
        "fps": 24,
        "width": FULL_W,
        "height": FULL_H,
        "safeArea": dict(SAFE),
        "durationInFrames": 84,
    }


def _lower_third(anchor="bottom_left"):
    return {
        "element": "lower_third",
        "anchor": anchor,
        "row": 0,
        "runs": [{"text": "Akshita Gorti", "type_role": "display"},
                 {"text": "AI @ Lucie Content",
                  "type_role": "supporting"}],
        "color": "#FFB8D4",
        "entrance": "draw",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 84,
        "footprint": None,
        "data": {"construction": "staged_rule"},
    }


# The Reel 01 Akshita lower third's measured union, all 84 frames.
AKSHITA_UNION = InkUnion(x0=90, y0=784, x1=602, y1=906,
                         inked_frames=83)


def test_measured_union_binds_to_padded_canvas_at_union_centre():
    """512x122 of ink becomes a 608x480 canvas whose centre is the
    union centre: 608 = 512 + 2*48, and 480 is the rail floor a
    single bottom zone grows to, above the ink."""
    box, refusal = mgt.tighten_measured_mg_with_reason(
        _props([_lower_third()]), AKSHITA_UNION)
    assert refusal is None
    assert (box.width, box.height) == (608, 480)
    ox, oy = canvas_offset(box)
    assert (ox, oy) == (42, 474)
    # The growth went above the ink (a bottom zone grows upward), so
    # the canvas centre is NOT the union centre - but the INK still
    # lands on the union: pads plus growth place it there exactly.
    from library.tools.tight_box import ink_screen_box  # noqa: E402
    ink_in_canvas = (48.0, 48.0 + 262.0, 48.0 + 512.0,
                     48.0 + 262.0 + 122.0)
    assert ink_screen_box(box.width, box.height, box.placement,
                          ink_in_canvas, FULL_W, FULL_H) == pytest.approx(
        (90.0, 784.0, 602.0, 906.0))
    assert box.props["width"] == 608
    assert box.props["height"] == 480
    assert box.props["safeArea"] == {
        "top": 48 + 262, "right": 48, "bottom": 48, "left": 48}


def test_a_union_that_cannot_bind_is_refused_by_name():
    """Empty ink is a refusal, not a box; a union wider than the frame,
    one at the frame edge with no pad to give, and a timeline whose rail
    nobody probed all refuse rather than placing against a guess."""
    box, refusal = mgt.tighten_measured_mg_with_reason(
        _props([_lower_third()]),
        InkUnion(x0=10, y0=10, x1=10, y1=10, inked_frames=0))
    assert box is None
    assert refusal.reason == "nothing_drawn"
    for error, union, kwargs, reason in (
        (TightBoxClipsInk,
         InkUnion(x0=0, y0=0, x1=2000, y1=100, inked_frames=10), {},
         "canvas_larger_than_frame"),
        (TightBoxMismatch, AKSHITA_UNION, {"timeline_size": (640, 480)},
         "placement_unholdable"),
        (TightBoxMismatch,
         InkUnion(x0=0, y0=784, x1=602, y1=906, inked_frames=10), {},
         "pads_leave_frame"),
    ):
        with pytest.raises(error) as excinfo:
            mgt.tighten_measured_mg_with_reason(
                _props([_lower_third()]), union, **kwargs)
        assert excinfo.value.refusal.reason == reason


def _encode_mov(png_paths, mov_path, width, height):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24",
         "-i", os.path.join(os.path.dirname(png_paths[0]),
                            "shot-%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", mov_path],
        check=True)


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_measure_crop_verify_roundtrip_on_synthetic_pixels(tmp_path):
    """The whole chain on pixels this test drew: measure the union,
    cut the canvas around it, prove the crop is its probe's region -
    with the before/after/offset record the run reports."""
    from PIL import Image

    full_w, full_h = 1080, 1920
    for index in range(4):
        frame = Image.new("RGBA", (full_w, full_h), (0, 0, 0, 0))
        pixels = frame.load()
        for x in range(90, 90 + 120):
            for y in range(784, 784 + 62):
                pixels[x, y] = (255, 255, 255, 255)
        frame.save(str(tmp_path / f"shot-{index:04d}.png"))
    full_mov = str(tmp_path / "full.mov")
    _encode_mov([str(tmp_path / "shot-0000.png")], full_mov,
                full_w, full_h)

    union = mgt.measure_mg_union(full_mov)
    assert (union.x0, union.y0, union.x1, union.y1) == (90, 784, 210, 846)

    props = {"elements": [dict(_lower_third(), anchor="bottom_left")],
             "width": full_w, "height": full_h,
             "safeArea": dict(SAFE), "durationInFrames": 4}
    box, refusal = mgt.tighten_measured_mg_with_reason(props, union)
    assert refusal is None
    assert (box.width, box.height) == (216, 480)

    tight_mov = str(tmp_path / "tight.mov")
    crop_probe_to_tight(full_mov, tight_mov, box)
    report = mgt.verify_measured_crop(union, tight_mov, box)
    assert report["before"] == [full_w, full_h]
    assert report["after"] == [216, 480]
    assert report["offset"] == [42, 414]
    assert report["union"] == [90, 784, 210, 846]


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_blank_probe_measures_no_union(tmp_path):
    """A file that drew nothing binds nothing: None, not a guess."""
    from PIL import Image

    for index in range(2):
        Image.new("RGBA", (120, 80), (0, 0, 0, 0)).save(
            str(tmp_path / f"blank-{index:04d}.png"))
    blank_mov = str(tmp_path / "blank.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24",
         "-i", str(tmp_path / "blank-%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", blank_mov],
        check=True)
    assert mgt.measure_mg_union(blank_mov) is None


def _title_lockup(anchor="top_centre"):
    """Reel 01's surviving full-frame graphic, as the plan wrote it:
    one display run, top-anchored - the element kind PR 1190 wired
    nothing through."""
    return {
        "element": "title_lockup",
        "anchor": anchor,
        "row": 0,
        "runs": [{"text": "A COMPLETELY DIFFERENT SYSTEM",
                  "type_role": "display"}],
        "color": "#aabbcc",
        "entrance": "scale",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 84,
        "footprint": None,
        "data": {},
    }


# The stale title lockup's drawn union, measured off its own
# full-canvas render: 660x131 of ink at offset (193, 120). The
# PREDICTED path sized this same copy to a 1262x480 canvas and refused
# it as `canvas_larger_than_frame` - the refusal the quarantined
# sidecar still carries - while the pixels bind cleanly.
TITLE_UNION = InkUnion(x0=193, y0=120, x1=853, y1=251,
                       inked_frames=84)


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_bind_probe_tight_crops_a_title_lockup_probe(tmp_path):
    """The shared binder step 4.06 calls on a predicted refusal, end
    to end on synthetic title pixels: the entry leaves pointing at a
    verified 756x480 tight crop, not at the full-canvas probe."""
    from PIL import Image

    for index in range(3):
        frame = Image.new("RGBA", (FULL_W, FULL_H), (0, 0, 0, 0))
        pixels = frame.load()
        for x in range(193, 853):
            for y in range(120, 251):
                pixels[x, y] = (255, 255, 255, 255)
        frame.save(str(tmp_path / f"shot-{index:04d}.png"))
    full_mov = str(tmp_path / "mg_geo-podcast_probe.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24",
         "-i", str(tmp_path / "shot-%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", full_mov],
        check=True)

    planned = {"props": _props([_title_lockup()])}
    rendered = {"overlay_path": full_mov, "segment_id": "mg_probe",
                "geometry": "full", "tight_fallback": "canvas_larger",
                "tight_box": None}
    mgt.bind_probe_tight("Reel 01", planned, rendered, FULL_W, FULL_H)

    assert rendered["geometry"] == "tight"
    assert rendered["tight_fallback"] == ""
    assert rendered["overlay_path"].endswith("_tight.mov")
    assert rendered["tight_box"]["width"] == 756
    assert rendered["tight_box"]["height"] == 480
    assert rendered["tight_report"]["before"] == [FULL_W, FULL_H]
    assert rendered["tight_report"]["after"] == [756, 480]
    assert rendered["tight_report"]["offset"] == [145, 72]
    assert os.path.isfile(
        full_mov[:-len(".mov")] + "_tight_props.json")

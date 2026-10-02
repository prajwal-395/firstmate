"""The shared pixel primitives and the gates around them.

`ink_union_of_frames` reads the drawn union off decoded frames - the
measurement the motion-graphics path still binds from
(`measure_mg_union` in `mg_tight_box.py`). The caption probe path
that once sized boxes from that union (`tighten_measured`, the
correspondence read-off, `verify_frames`) is retired: the caption
path renders the constant structural canvas and guards it with
ink-touches-edge instead (see `tight_box`'s module docstring for
that history, and `test_tight_box.py` for the constant path).

What stays under test here: the union primitive itself, the
Pan/Tilt rail gate (`placement_holds` / `placement_limits`) every
tight placement ships through, and the reuse cache
(`restore_reused_placement`) the caption renderer restores sidecars
through.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.tight_box import (
    TightBoxMismatch,
    ink_union_of_frames,
    placement_holds,
    restore_reused_placement,
)

FULL_W = 1080
FULL_H = 1920


def _frame(path, size, rects):
    """One RGBA frame: transparent canvas with opaque white rects."""
    from PIL import Image
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for (x0, y0, x1, y1) in rects:
        for x in range(x0, x1):
            for y in range(y0, y1):
                img.putpixel((x, y), (255, 255, 255, 255))
    img.save(path)
    return path


def _frames(tmp_path, name, size, per_frame_rects):
    d = os.path.join(str(tmp_path), name)
    os.makedirs(d, exist_ok=True)
    paths = []
    for i, rects in enumerate(per_frame_rects):
        paths.append(_frame(os.path.join(d, f"f_{i:03d}.png"), size, rects))
    return paths


def _props(position="bottom"):
    return {
        "subtitles": [{"text": "measured words"}],
        "fps": 30,
        "width": FULL_W,
        "height": FULL_H,
        "durationInFrames": 3,
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "position": position,
            "safeArea": {"top": 120, "right": 120,
                         "bottom": 320, "left": 90},
            "captionMaxWidth": 840,
        },
    }


def test_the_union_spans_ink_across_frames_and_blank_measures_nothing(
        tmp_path):
    paths = _frames(tmp_path, "blank", (FULL_W, FULL_H), [[], [], []])
    assert ink_union_of_frames(paths) is None
    paths = _frames(tmp_path, "two", (FULL_W, FULL_H),
                    [[(100, 1500, 300, 1560)], [(200, 1400, 400, 1480)], []])
    union = ink_union_of_frames(paths)
    assert union is not None
    assert (union.x0, union.y0, union.x1, union.y1) == (100, 1400, 400, 1560)


def test_the_rail_gate_holds_only_what_was_probed_and_names_the_axis():
    """1920x1080 was PROBED on 2026-09-13 and carries its own row (Pan
    7680, Tilt 4320 - 4x width and height); it is NOT the vertical row's
    numbers carried across, which on Tilt would sit above the real 4320.
    A frame nobody probed REFUSES rather than borrowing another's row.
    One predicate for every path that ships a placement."""
    from library.tools.tight_box import placement_limits

    assert placement_limits(1920, 1080) == (7680.0, 4320.0)
    assert placement_limits(1920, 1080) != placement_limits(1080, 1920)
    assert placement_holds({"pan": 0.0, "tilt": -100.0}, 1920, 1080) == ""
    assert placement_holds({"pan": 7000.0, "tilt": -4000.0}, 1920, 1080) == ""
    reason = placement_holds({"pan": 0.0, "tilt": -4400.0}, 1920, 1080)
    assert "1920x1080" in reason and "4320" in reason
    reason = placement_holds({"pan": 7700.0, "tilt": 0.0}, 1920, 1080)
    assert "1920x1080" in reason and "7680" in reason

    assert placement_limits(1080, 1080) is None
    reason = placement_holds({"pan": 0.0, "tilt": -100.0}, 1080, 1080)
    assert "1080x1080" in reason and "never been measured" in reason

    assert placement_holds({"pan": 0.0, "tilt": -100.0}, 1080, 1920) == ""
    assert placement_holds(None, 1080, 1920) == ""
    assert "Tilt -7929.0" in placement_holds(
        {"pan": 0.0, "tilt": -7929.0}, 1080, 1920)
    assert "Pan 9188.0" in placement_holds(
        {"pan": 9188.0, "tilt": 0.0}, 1080, 1920)


def test_reused_placement_restores_when_it_holds():
    """A sidecar whose placement Resolve can hold re-gates clean:
    reuse keeps its win."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    sidecar = {
        "width": 840,
        "height": 146,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -100.0},
        "carriage": OVERLAY_CARRIAGE,
        "draw_gain": 2.0,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": 320, "left": 90},
        "union": {"x0": 100, "y0": 1200, "x1": 400, "y1": 1294},
    }
    restored = restore_reused_placement(sidecar, _props(), (1080, 1920))
    assert restored.placement == {"scaling": 1, "pan": 0.0, "tilt": -100.0}
    assert (restored.width, restored.height) == (840, 146)
    assert (restored.union_w, restored.union_h) == (300.0, 94.0)


def test_a_reused_placement_that_cannot_be_trusted_is_refused():
    """Each refusal sends the caller to a fresh measured render:
    a placement Resolve would clamp (the captain's predictor-era
    captions); a superseded carriage (`tight-480-2` carried HALF the Tilt
    - the caption drew ~108px high); no carriage stamp at all (the
    -1744.0/-1700.0 ledger sidecars); a malformed sidecar; and a
    placement computed under a superseded draw gain (the -1836 gate-build
    caption boxes)."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    safe = {"top": 120, "right": 120, "bottom": 320, "left": 90}
    gain_props = _props()
    gain_props["style"]["safeArea"] = dict(safe, bottom=297)
    cases = [
        ({"width": 348, "height": 146,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -7929.0},
          "carriage": OVERLAY_CARRIAGE, "draw_gain": 2.0, "safe_area": safe,
          "union": {"x0": 382, "y0": 1501, "x1": 682, "y1": 1595}},
         _props(), "no longer holds"),
        ({"width": 840, "height": 480,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -432.0},
          "carriage": "tight-480-2",
          "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596}},
         _props(), "superseded carriage"),
        ({"width": 840, "height": 480,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
          "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596}},
         _props(), "superseded carriage"),
        ({"width": 348}, _props(), None),
        ({"width": 904, "height": 480,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -1836.0},
          "carriage": OVERLAY_CARRIAGE, "safe_area": dict(safe, bottom=297),
          "union": {"x0": 88, "y0": 1179, "x1": 992, "y1": 1659}},
         gain_props, "draw gain"),
    ]
    for sidecar, props, match in cases:
        with pytest.raises(TightBoxMismatch, match=match):
            restore_reused_placement(sidecar, props, (1080, 1920))

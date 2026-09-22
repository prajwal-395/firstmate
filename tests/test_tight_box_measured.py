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


def test_blank_frames_measure_nothing(tmp_path):
    paths = _frames(tmp_path, "blank", (FULL_W, FULL_H), [[], [], []])
    assert ink_union_of_frames(paths) is None


def test_union_spans_ink_across_frames(tmp_path):
    paths = _frames(tmp_path, "two", (FULL_W, FULL_H),
                    [[(100, 1500, 300, 1560)], [(200, 1400, 400, 1480)], []])
    union = ink_union_of_frames(paths)
    assert union is not None
    assert (union.x0, union.y0, union.x1, union.y1) == (100, 1400, 400, 1560)


def test_placement_limits_are_the_measured_rail():
    """The rail is the 4x law, binary-searched 2026-09-13: Pan 4320,
    Tilt 7680 on 1080x1920. The 3840 the 2026-09-10 incident read
    does not reproduce and is history in `MEASURED_RAILS`, not a row.
    Every row carries its build in `MEASURED_RAIL_BUILDS` - a row
    whose build is unknown is a row nobody may trust, which is what
    keeps this number from going stale the way both of its
    predecessors did."""
    from library.tools.tight_box import (
        MEASURED_RAIL_BUILDS,
        placement_limits,
    )
    assert placement_limits(1080, 1920) == (4320.0, 7680.0)
    assert MEASURED_RAIL_BUILDS[(1080, 1920)] == "21.1.0.14"
    assert MEASURED_RAIL_BUILDS[(1920, 1080)] == "21.1.0.14"


def test_the_horizontal_rail_is_the_one_that_was_probed():
    """1920x1080 was PROBED on 2026-09-13 and carries its own row.

    Pan 7680, Tilt 4320 - `4 x timeline width` and `4 x timeline
    height`, by binary search on the exact float round-trip in a
    scratch project, at three geometries and two clip sizes, and
    reproduced on the captain's own project.

    The row is NOT the other geometry's numbers carried across. That
    is the thing this test exists to catch: one row's numbers on both
    axes would be a number nobody measured here, and on the Tilt axis
    7680 would sit ABOVE the real 4320 while reading as the
    measurement - the dangerous direction.
    """
    from library.tools.tight_box import placement_holds, placement_limits

    assert placement_limits(1920, 1080) == (7680.0, 4320.0)
    # Not the vertical constant wearing a second row's name.
    assert placement_limits(1920, 1080) != placement_limits(1080, 1920)

    # A placement inside it now HOLDS, which is what unblocks a
    # horizontal build from carrying every caption full-canvas.
    assert placement_holds({"pan": 0.0, "tilt": -100.0}, 1920, 1080) == ""
    assert placement_holds({"pan": 7000.0, "tilt": -4000.0}, 1920, 1080) == ""
    # And past it still refuses, per axis, naming the frame.
    reason = placement_holds({"pan": 0.0, "tilt": -4400.0}, 1920, 1080)
    assert "1920x1080" in reason and "4320" in reason
    reason = placement_holds({"pan": 7700.0, "tilt": 0.0}, 1920, 1080)
    assert "1920x1080" in reason and "7680" in reason


def test_a_frame_nobody_probed_still_gets_no_rail():
    """A geometry with no row REFUSES rather than borrowing another's.

    The input that breaks this: making `placement_limits` fall back to
    another row for a frame it has no row for. 1080x1080
    stands in for every unprobed delivery frame.
    """
    from library.tools.tight_box import placement_holds, placement_limits

    assert placement_limits(1080, 1080) is None
    reason = placement_holds({"pan": 0.0, "tilt": -100.0}, 1080, 1080)
    assert reason, "an unmeasured frame must refuse, not pass"
    assert "1080x1080" in reason
    assert "never been measured" in reason

    # And the vertical path is untouched: the same placement holds.
    assert placement_holds({"pan": 0.0, "tilt": -100.0}, 1080, 1920) == ""


def test_placement_holds_names_the_clamped_axis():
    """One predicate for every path that ships a placement: the
    fresh render, the motion-graphics box and the reuse cache."""
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


def test_reused_placement_is_refused_when_clamped():
    """The captain's captions: a sidecar whose placement Resolve
    would pin (predictor-era, or a changed delivery format) does
    not ship - the caller falls through to a fresh measured render,
    which carries the card full canvas instead."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    sidecar = {
        "width": 348,
        "height": 146,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -7929.0},
        "carriage": OVERLAY_CARRIAGE,
        "draw_gain": 2.0,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": 320, "left": 90},
        "union": {"x0": 382, "y0": 1501, "x1": 682, "y1": 1595},
    }
    with pytest.raises(TightBoxMismatch, match="no longer holds"):
        restore_reused_placement(sidecar, _props(), (1080, 1920))


def test_reused_placement_from_a_superseded_carriage_is_refused():
    """Every `tight-480-2` sidecar carries HALF the Tilt its artefact
    needs - that carriage computed placements under a draw gain that
    does not exist - so restoring one verbatim draws the caption
    ~108px above its row. A sidecar from a superseded carriage is
    REFUSED here and the caller re-renders measured, which is the
    same door `tight-480-1` was retired through."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    assert OVERLAY_CARRIAGE == "tight-480-4"
    stale = {
        "width": 840,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -432.0},
        "carriage": "tight-480-2",
        "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596},
    }
    with pytest.raises(TightBoxMismatch, match="superseded carriage"):
        restore_reused_placement(stale, _props(), (1080, 1920))


def test_reused_placement_without_a_carriage_is_refused():
    """A sidecar that predates the carriage stamp cannot prove what
    it was computed under - every sidecar on disk from before the
    stamp, including the -1744.0 and -1700.0 ones still sitting in
    the geo-podcast render ledger - so it is refused the same way."""
    sidecar = {
        "width": 840,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
        "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596},
    }
    with pytest.raises(TightBoxMismatch, match="superseded carriage"):
        restore_reused_placement(sidecar, _props(), (1080, 1920))


def test_reused_placement_malformed_sidecar_is_refused():
    with pytest.raises(TightBoxMismatch):
        restore_reused_placement({"width": 348}, _props(), (1080, 1920))


def test_reused_placement_from_a_superseded_gain_is_refused():
    """A sidecar whose carriage and row both pass but whose placement
    was computed under gain 1.0: serving it verbatim under today's
    renderer lands the canvas a full row off however cleanly it reads
    back. REFUSED, and the caller re-renders measured over the same
    file. This is the door every pre-2026-09-17 sidecar on disk -
    including the -1836 caption boxes the gate build rendered under
    the declared row - leaves through."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    sidecar = {
        "width": 904,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -1836.0},
        "carriage": OVERLAY_CARRIAGE,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": 297, "left": 90},
        "union": {"x0": 88, "y0": 1179, "x1": 992, "y1": 1659},
    }
    props = _props()
    props["style"]["safeArea"] = dict(sidecar["safe_area"])
    with pytest.raises(TightBoxMismatch, match="draw gain"):
        restore_reused_placement(sidecar, props, (1080, 1920))

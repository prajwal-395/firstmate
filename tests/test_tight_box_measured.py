"""Boxes derived from what Chromium actually draws, not predicted from fonts.

The PIL fitter in `tight_box.tighten_subtitle_props` under-measures
against the browser renderer: measured on the field test (Reel 12,
2026-09-09), 10 of 12 pilot segments drew ink outside their predicted
boxes - one card laid out by the box as a single 840px line rendered as
two lines 638px wide, with ink 25px above the box top across 33 frames.
A box that clips ink is a correctness failure, so the render path
measures the union off a decoded probe render and FAILS a segment whose
tight output does not match, never warns past it.
"""
import os
import shutil
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.tight_box import (
    MIN_CANVAS_HEIGHT,
    PAD_BOTTOM,
    PAD_TOP,
    PAD_X,
    TightBoxClipsInk,
    TightBoxMismatch,
    canvas_offset,
    finalize_box_placement,
    ink_union_of_frames,
    placement_holds,
    resolve_placement_from_correspondence,
    restore_reused_placement,
    tighten_measured,
    verify_frames,
)

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

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


def test_canvas_expands_union_by_pads_then_floor(tmp_path):
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(101, 1501, 901, 1561)]])
    union = ink_union_of_frames(paths)
    box = tighten_measured(_props(), union)
    assert box.width == 800 + 2 * PAD_X
    assert box.width % 2 == 0 and box.height % 2 == 0
    # 60px of ink plus pads is 112 - under the rail floor, so the
    # canvas ships at the minimum, grown above the bottom anchor.
    assert box.height == MIN_CANVAS_HEIGHT
    assert box.props["style"]["safeArea"]["bottom"] == PAD_BOTTOM
    assert box.props["style"]["safeArea"]["top"] == (
        PAD_TOP + MIN_CANVAS_HEIGHT - (60 + PAD_TOP + PAD_BOTTOM))


def test_narrow_union_widens_to_wrap_width(tmp_path):
    """The flex container is `width: 100%`: a canvas narrower than
    captionMaxWidth would rewrap the card. The canvas keeps the wrap
    basis, so the tight layout is the probe layout translated."""
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(101, 1501, 301, 1561)]])
    union = ink_union_of_frames(paths)
    box = tighten_measured(_props(), union)
    assert box.width == 840
    assert box.width % 2 == 0


def test_canvas_follows_ink_off_centre(tmp_path):
    """No centring assumption in the SIZE: an off-centre union yields
    an off-centre provisional canvas. The final origin is read off
    the two renders (correspondence), not from pads arithmetic."""
    paths = _frames(tmp_path, "left", (FULL_W, FULL_H),
                    [[(50, 1500, 250, 1560)]])
    union = ink_union_of_frames(paths)
    box = tighten_measured(_props(), union)
    assert box.width == 840  # widened to the wrap basis
    ox, oy = canvas_offset(box)
    assert ox == 50 - PAD_X  # provisional: union minus pads
    assert box.placement["scaling"] == 1
    assert box.placement["pan"] < 0


def test_correspondence_resolves_translation(tmp_path):
    """The pilot segment shape: identical pixels, translated. The
    origin is probe union minus tight union, and the offset inverts
    the placement exactly. The tight ink sits bottom-anchored in the
    grown canvas, exactly as the composition draws it."""
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H),
                          [[(382, 1501, 682, 1595)]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(), probe_union)
    assert box.width == 840  # widened: the wrap basis binds
    assert box.height == MIN_CANVAS_HEIGHT
    # the composition bottom-anchors the card in the taller canvas
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                          [[(270, 350, 570, 444)]])
    tight_union = ink_union_of_frames(tight_paths)
    placement = resolve_placement_from_correspondence(
        probe_union, tight_union,
        box.width, box.height, FULL_W, FULL_H)
    assert placement["scaling"] == 1
    final = finalize_box_placement(box, probe_union, tight_union)
    ox, oy = canvas_offset(final)
    assert (ox, oy) == (112, 1151)


def test_placement_limits_are_the_measured_rail():
    """The rail is a measured NUMBER, not a formula: read off the live
    timeline 2026-09-10 (Resolve 21, 1080x1920), where values at or
    below 3840 round-tripped and everything above pinned at 3840.0.
    The earlier four-times figure was calibrated to miss it."""
    from library.tools.tight_box import placement_limits
    assert placement_limits(1080, 1920) == (3840.0, 3840.0)
    assert placement_limits(1920, 1080) == (3840.0, 3840.0)


def test_minimum_canvas_brings_the_craig_case_inside_the_rail(tmp_path):
    """The craig_0 case that made full-frame the default: exact
    translation, but a 146-tall canvas needing Tilt -7929 where
    Resolve pins at 3840 on this timeline. The floor grows the canvas
    to 480 BEFORE the render, so the same ink places at about -1724 -
    inside with headroom. The gate still refuses what even the grown
    canvas cannot hold (the MG two-zone case proves that half)."""
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H),
                          [[(382, 1501, 682, 1595)]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(), probe_union)
    assert box.height == MIN_CANVAS_HEIGHT
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                          [[(270, 350, 570, 444)]])
    tight_union = ink_union_of_frames(tight_paths)
    placement = resolve_placement_from_correspondence(
        probe_union, tight_union,
        box.width, box.height, FULL_W, FULL_H,
        timeline_size=(1080, 1920))
    assert abs(placement["tilt"]) <= 3400
    assert placement_holds(placement, 1080, 1920) == ""


def test_correspondence_within_range_passes(tmp_path):
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H),
                          [[(382, 1200, 682, 1294)]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(), probe_union)
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                          [[(270, 350, 570, 444)]])
    tight_union = ink_union_of_frames(tight_paths)
    placement = resolve_placement_from_correspondence(
        probe_union, tight_union,
        box.width, box.height, FULL_W, FULL_H,
        timeline_size=(1080, 1920))
    assert abs(placement["tilt"]) <= 3400
    final = finalize_box_placement(box, probe_union, tight_union,
                                   (1080, 1920))
    assert canvas_offset(final) == (112, 850)


def test_correspondence_outside_frame_fails(tmp_path):
    """A translation the delivery frame cannot hold is a layout
    disagreement: FAIL, never clamp."""
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H),
                          [[(1000, 1500, 1070, 1560)]])
    tight_paths = _frames(tmp_path, "t", (840, 112),
                          [[(0, 11, 70, 71)]])
    probe_union = ink_union_of_frames(probe_paths)
    tight_union = ink_union_of_frames(tight_paths)
    with pytest.raises(TightBoxMismatch):
        resolve_placement_from_correspondence(
            probe_union, tight_union, 840, 112, FULL_W, FULL_H)


def test_finalize_then_verify_passes_translated_crop(tmp_path):
    """End to end of the new semantics: the tight canvas re-centers
    the card, correspondence re-places it, the gate passes. Both the
    probe ink and the tight ink sit where the grown render draws
    them - bottom-anchored - so the paste is exact."""
    probe_paths = _frames(tmp_path, "full", (FULL_W, FULL_H),
                           [[(100, 1510, 300, 1570)],
                            [(120, 1490, 320, 1570)]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(), probe_union)
    assert box.width == 840
    assert box.height == MIN_CANVAS_HEIGHT
    # the re-anchored tight canvas: the same ink drawn bottom-anchored
    # in the taller canvas, as the composition does
    tight_paths = _frames(tmp_path, "tight", (box.width, box.height),
                           [[(16, 384, 216, 444)],
                            [(36, 364, 236, 444)]])
    tight_union = ink_union_of_frames(tight_paths)
    final = finalize_box_placement(box, probe_union, tight_union)
    ox, oy = canvas_offset(final)
    assert (ox, oy) == (84, 1126)
    report = verify_frames(probe_paths, tight_paths, final)
    assert report["max_diff"] == 0
    assert report["min_iou"] == 1.0


def test_tight_props_keep_wrap_basis_and_pass_timing_through(tmp_path):
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(100, 1500, 300, 1560)]])
    props = _props()
    box = tighten_measured(props, ink_union_of_frames(paths))
    assert box.props["width"] == box.width
    assert box.props["height"] == box.height
    assert box.props["style"]["captionMaxWidth"] == 840
    # grown above the bottom anchor: the bottom inset is untouched,
    # the top absorbs the whole growth.
    assert box.props["style"]["safeArea"] == {
        "top": PAD_TOP + MIN_CANVAS_HEIGHT - (60 + PAD_TOP + PAD_BOTTOM),
        "right": PAD_X,
        "bottom": PAD_BOTTOM, "left": PAD_X,
    }
    assert box.props["subtitles"] == props["subtitles"]
    assert box.props["durationInFrames"] == props["durationInFrames"]


def test_missing_caption_max_width_refuses(tmp_path):
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(100, 1500, 300, 1560)]])
    union = ink_union_of_frames(paths)
    props = _props()
    del props["style"]["captionMaxWidth"]
    with pytest.raises(ValueError, match="captionMaxWidth"):
        tighten_measured(props, union)


def test_ink_at_frame_edge_fails(tmp_path):
    """Pads that leave the delivery frame would clip: FAIL, never clamp
    silently. (Full-canvas QA keeps legit ink 2% inside, so only a
    broken measurement can trip this.)"""
    paths = _frames(tmp_path, "edge", (FULL_W, FULL_H),
                    [[(0, 10, FULL_W, 100)]])
    union = ink_union_of_frames(paths)
    with pytest.raises(TightBoxClipsInk):
        tighten_measured(_props(), union)


def test_verify_passes_identical_paste(tmp_path):
    full = _frames(tmp_path, "full", (FULL_W, FULL_H),
                   [[(100, 1500, 300, 1560)], [(120, 1490, 320, 1570)]])
    union = ink_union_of_frames(full)
    box = tighten_measured(_props(), union)
    ox, oy = canvas_offset(box)
    tdir = os.path.join(str(tmp_path), "tight")
    os.makedirs(tdir, exist_ok=True)
    from PIL import Image
    tight_paths = []
    for i, fp in enumerate(full):
        with Image.open(fp) as im:
            crop = im.crop((ox, oy, ox + box.width, oy + box.height))
        tp = os.path.join(tdir, f"t_{i:03d}.png")
        crop.save(tp)
        tight_paths.append(tp)
    report = verify_frames(full, tight_paths, box)
    assert report["max_diff"] == 0
    assert report["min_iou"] == 1.0


def test_verify_fails_clipped_row(tmp_path):
    """A tight frame missing a band the full frame drew is cut-off
    text: FAIL, never warn."""
    full = _frames(tmp_path, "full", (FULL_W, FULL_H),
                   [[(100, 1500, 300, 1560)]])
    union = ink_union_of_frames(full)
    box = tighten_measured(_props(), union)
    ox, oy = canvas_offset(box)
    from PIL import Image
    with Image.open(full[0]) as im:
        crop = im.crop((ox, oy + 20, ox + box.width, oy + box.height))
        canvas = Image.new("RGBA", (box.width, box.height), (0, 0, 0, 0))
        canvas.alpha_composite(crop, (0, 0))
    tp = os.path.join(str(tmp_path), "clipped.png")
    canvas.save(tp)
    with pytest.raises(TightBoxMismatch):
        verify_frames(full, [tp], box)


def test_verify_fails_frame_count_mismatch(tmp_path):
    full = _frames(tmp_path, "full", (FULL_W, FULL_H),
                   [[(100, 1500, 300, 1560)], [(100, 1500, 300, 1560)]])
    union = ink_union_of_frames(full)
    box = tighten_measured(_props(), union)
    with pytest.raises(TightBoxMismatch, match="frame count"):
        verify_frames(full, full[:1], box)


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
    sidecar = {
        "width": 840,
        "height": 146,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -100.0},
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
    sidecar = {
        "width": 348,
        "height": 146,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -7929.0},
        "union": {"x0": 382, "y0": 1501, "x1": 682, "y1": 1595},
    }
    with pytest.raises(TightBoxMismatch, match="no longer holds"):
        restore_reused_placement(sidecar, _props(), (1080, 1920))


def test_reused_placement_malformed_sidecar_is_refused():
    with pytest.raises(TightBoxMismatch):
        restore_reused_placement({"width": 348}, _props(), (1080, 1920))


def _tight_ink_for(position, union_h):
    """Where the grown render draws `union_h` px of ink, by anchor:
    bottom-anchored and top-anchored hug their inset, centered splits
    the canvas - the same contract `grow_to_minimum` grows under."""
    if position == "top":
        y0 = PAD_TOP
    elif position == "center":
        y0 = (MIN_CANVAS_HEIGHT - union_h) // 2
    else:
        y0 = MIN_CANVAS_HEIGHT - PAD_BOTTOM - union_h
    return (100, y0, 100 + 200, y0 + union_h)


@pytest.mark.parametrize("position,rect", [
    ("bottom", (140, 1500, 420, 1560)),   # one-word caption
    ("bottom", (140, 1400, 700, 1560)),   # two-line card
    ("bottom", (140, 1300, 640, 1620)),   # three-line card
    ("top", (140, 140, 500, 220)),
    ("top", (140, 130, 600, 330)),
    ("center", (140, 900, 500, 1000)),
    ("center", (140, 830, 620, 1070)),
])
def test_every_small_box_holds_under_the_stricter_rail(tmp_path, position,
                                                       rect):
    """The boundary the brief demands: one-word captions to three-line
    cards, every anchor. Each ships at the floor and finalizes inside
    Tilt 3400 - headroom below the measured 3840 rail. The gate still
    fires (the `placement_holds` predicate is untouched)."""
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H), [[rect]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(position), probe_union)
    assert box.height == MIN_CANVAS_HEIGHT
    union_h = rect[3] - rect[1]
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                          [[_tight_ink_for(position, union_h)]])
    tight_union = ink_union_of_frames(tight_paths)
    final = finalize_box_placement(box, probe_union, tight_union,
                                   (1080, 1920))
    ox, oy = canvas_offset(final)
    assert 0 <= ox and ox + box.width <= FULL_W
    assert 0 <= oy and oy + box.height <= FULL_H
    assert abs(final.placement["tilt"]) <= 3400
    assert placement_holds(final.placement, 1080, 1920) == ""

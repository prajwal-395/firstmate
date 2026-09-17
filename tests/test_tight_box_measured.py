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
    crop_probe_to_tight,
    extract_frames,
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

#: The 2026-09-11 draw gain, pinned wherever a test below reproduces a
#: measurement from that calibration (see HISTORY_GAIN in
#: test_tight_box.py and the 09-17 calibration in
#: tests/test_draw_gain_measured.py).
HISTORY_GAIN = 1.0


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
    box = tighten_measured(_props(), union,
        draw_gain=HISTORY_GAIN)
    assert box.width == 800 + 2 * PAD_X
    assert box.width % 2 == 0 and box.height % 2 == 0
    # 60px of ink plus pads measures 112 - and its own placement
    # needs 156 rows to sit 10% inside the 4x-law rail, so the canvas
    # ships at 156, grown above the bottom anchor. Derived per graphic
    # in `grow_to_hold_rail`, not the 480 constant.
    assert box.height == 156
    assert box.props["style"]["safeArea"]["bottom"] == PAD_BOTTOM
    assert box.props["style"]["safeArea"]["top"] == (
        PAD_TOP + 156 - (60 + PAD_TOP + PAD_BOTTOM))


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


def test_video_container_narrows_to_ink_plus_pads(tmp_path):
    """The `video` container crops the probe: the wrap is already drawn
    and cannot change, so widening the canvas to the wrap basis only
    pushes a narrow off-centre caption off the frame. The canvas is
    the ink plus pads there; the `frames` container genuinely
    re-renders, so it keeps the widening."""
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(101, 1501, 301, 1561)]])
    union = ink_union_of_frames(paths)
    video = tighten_measured(_props(), union, "video")
    assert video.width == 200 + 2 * PAD_X
    frames = tighten_measured(_props(), union, "frames")
    assert frames.width == 840
    assert tighten_measured(_props(), union).width == 840  # default


def test_video_container_recovers_off_centre_narrow_caption(tmp_path):
    """The 2026-09-12 shape: 25 caption segments fell back to full
    canvas as 840-wide crops with origin x in 241..371, every one
    leaving the 1080-wide frame horizontally. A 522px union at x0=270
    is one of them: widened it spans 246..1086, narrowed to the ink
    it spans 246..816."""
    paths = _frames(tmp_path, "offcentre", (FULL_W, FULL_H),
                    [[(270, 955, 792, 1064)]])
    union = ink_union_of_frames(paths)
    wide = tighten_measured(_props(), union, "frames")
    assert wide.width == 840
    wide_ox, _ = canvas_offset(wide)
    assert wide_ox == 270 - PAD_X
    assert wide_ox + wide.width > FULL_W  # the fallback, by arithmetic
    narrow = tighten_measured(_props(), union, "video")
    assert narrow.width == 522 + 2 * PAD_X
    narrow_ox, narrow_oy = canvas_offset(narrow)
    assert narrow_ox == 270 - PAD_X
    assert narrow_ox + narrow.width <= FULL_W
    assert narrow_oy >= 0
    assert narrow_oy + narrow.height <= FULL_H


def test_tighten_measured_refuses_unknown_container(tmp_path):
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(101, 1501, 301, 1561)]])
    union = ink_union_of_frames(paths)
    with pytest.raises(ValueError, match="unknown overlay container"):
        tighten_measured(_props(), union, "filmstrip")


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
    box = tighten_measured(_props(), probe_union,
        draw_gain=HISTORY_GAIN)
    assert box.width == 840  # widened: the wrap basis binds
    assert box.height == 164  # derived: 94px of ink places at -6896
    # the composition bottom-anchors the card in the taller canvas
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                           [[(270, 34, 570, 128)]])
    tight_union = ink_union_of_frames(tight_paths)
    placement = resolve_placement_from_correspondence(
        probe_union, tight_union,
        box.width, box.height, FULL_W, FULL_H,
            draw_gain=HISTORY_GAIN)
    assert placement["scaling"] == 1
    final = finalize_box_placement(box, probe_union, tight_union,
        draw_gain=HISTORY_GAIN)
    ox, oy = canvas_offset(final)
    assert (ox, oy) == (112, 1467)


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


def test_derived_floor_brings_the_craig_case_inside_the_rail(tmp_path):
    """The craig_0 case that made full-frame the default: exact
    translation, but a 146-tall canvas needing Tilt -7929 where
    Resolve used to pin at 3840 on this timeline. The derived floor
    grows the canvas to 164 BEFORE the render - the smallest even
    height whose Tilt sits 10% inside the 4x-law rail - so the same
    ink places at about -6896, inside with a stated margin. The gate
    still refuses what even the grown canvas cannot hold (the MG
    two-zone case proves that half)."""
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H),
                           [[(382, 1501, 682, 1595)]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(), probe_union,
        draw_gain=HISTORY_GAIN)
    assert box.height == 164
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                           [[(270, 34, 570, 128)]])
    tight_union = ink_union_of_frames(tight_paths)
    placement = resolve_placement_from_correspondence(
        probe_union, tight_union,
        box.width, box.height, FULL_W, FULL_H,
        timeline_size=(1080, 1920),
            draw_gain=HISTORY_GAIN)
    assert abs(placement["tilt"]) <= 6912.0
    assert placement_holds(placement, 1080, 1920) == ""


def test_correspondence_within_range_passes(tmp_path):
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H),
                           [[(382, 1200, 682, 1294)]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(), probe_union)
    assert box.height == 146  # higher ink ships measured: -3906
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                           [[(270, 16, 570, 110)]])
    tight_union = ink_union_of_frames(tight_paths)
    placement = resolve_placement_from_correspondence(
        probe_union, tight_union,
        box.width, box.height, FULL_W, FULL_H,
        timeline_size=(1080, 1920))
    assert abs(placement["tilt"]) <= 6912.0
    final = finalize_box_placement(box, probe_union, tight_union,
                                   (1080, 1920),
                                   draw_gain=HISTORY_GAIN)
    assert canvas_offset(final) == (112, 1184)


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
    box = tighten_measured(_props(), probe_union,
        draw_gain=HISTORY_GAIN)
    assert box.width == 840
    assert box.height == 158  # derived: 80px of ink places at -6890
    # the re-anchored tight canvas: the same ink drawn bottom-anchored
    # in the taller canvas, as the composition does
    tight_paths = _frames(tmp_path, "tight", (box.width, box.height),
                           [[(16, 62, 216, 122)],
                            [(36, 42, 236, 122)]])
    tight_union = ink_union_of_frames(tight_paths)
    final = finalize_box_placement(box, probe_union, tight_union,
        draw_gain=HISTORY_GAIN)
    ox, oy = canvas_offset(final)
    assert (ox, oy) == (84, 1448)
    report = verify_frames(probe_paths, tight_paths, final)
    assert report["max_diff"] == 0
    assert report["min_iou"] == 1.0


def test_tight_props_keep_wrap_basis_and_pass_timing_through(tmp_path):
    paths = _frames(tmp_path, "one", (FULL_W, FULL_H),
                    [[(100, 1500, 300, 1560)]])
    props = _props()
    box = tighten_measured(props, ink_union_of_frames(paths),
                               draw_gain=HISTORY_GAIN)
    assert box.props["width"] == box.width
    assert box.props["height"] == box.height
    assert box.props["style"]["captionMaxWidth"] == 840
    # grown above the bottom anchor: the bottom inset is untouched,
    # the top absorbs the whole growth (156 ships for 60px of ink,
    # grown 44 above).
    assert box.props["style"]["safeArea"] == {
        "top": PAD_TOP + 156 - (60 + PAD_TOP + PAD_BOTTOM),
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


def _mov_from_frames(frame_paths, dest, fps=30):
    """One ProRes 4444 mov from decoded frames, the crop's honest input."""
    import subprocess
    listing = os.path.join(os.path.dirname(dest), "inputs.txt")
    with open(listing, "w") as handle:
        handle.write("".join(f"file '{path}'\n" for path in frame_paths))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", listing, "-r", str(fps),
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", dest],
        check=True,
    )
    return dest


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_crop_cuts_the_probe_at_the_placement_origin(tmp_path):
    """The crop helper cuts exactly what the placement ships: decoded
    crop frames pasted at `canvas_offset` reproduce the probe, inside
    the gate - and the file carries the box's own dimensions."""
    from PIL import Image
    full = _frames(tmp_path, "full", (FULL_W, FULL_H),
                   [[(100, 1510, 300, 1570)],
                    [(120, 1490, 320, 1570)]])
    union = ink_union_of_frames(full)
    box = tighten_measured(_props(), union)
    probe_mov = _mov_from_frames(
        full, os.path.join(str(tmp_path), "probe.mov"))
    out_mov = os.path.join(str(tmp_path), "tight.mov")
    ox, oy = crop_probe_to_tight(probe_mov, out_mov, box)
    assert (ox, oy) == canvas_offset(box)
    report = verify_frames(
        full, extract_frames(out_mov, os.path.join(str(tmp_path), "dec")),
        box)
    assert report["max_diff"] <= 4
    assert report["min_iou"] >= 0.99
    with Image.open(os.path.join(
            str(tmp_path), "dec",
            min(os.listdir(os.path.join(str(tmp_path), "dec")))
            )) as im:
        assert im.size == (box.width, box.height)


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_crop_refuses_an_undecodable_probe(tmp_path):
    full = _frames(tmp_path, "full", (FULL_W, FULL_H),
                   [[(100, 1500, 300, 1560)]])
    union = ink_union_of_frames(full)
    box = tighten_measured(_props(), union)
    bad = os.path.join(str(tmp_path), "bad.mov")
    with open(bad, "wb") as handle:
        handle.write(b"not a movie")
    with pytest.raises(TightBoxMismatch, match="cannot crop probe"):
        crop_probe_to_tight(bad, os.path.join(str(tmp_path), "out.mov"),
                            box)


def test_crop_refuses_an_out_of_frame_origin(tmp_path):
    """A box the frame cannot hold is refused before ffmpeg runs -
    never approximated, and the caller carries full canvas instead."""
    import dataclasses
    full = _frames(tmp_path, "full", (FULL_W, FULL_H),
                   [[(100, 1500, 300, 1560)]])
    union = ink_union_of_frames(full)
    box = tighten_measured(_props(), union)
    off_frame = dataclasses.replace(
        box, placement={"scaling": 1, "pan": -20000.0, "tilt": 0.0})
    assert canvas_offset(off_frame)[0] < 0
    with pytest.raises(TightBoxMismatch, match="leaves a"):
        crop_probe_to_tight(
            os.path.join(str(tmp_path), "probe.mov"),
            os.path.join(str(tmp_path), "out.mov"), off_frame)


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


def _tight_ink_for(position, union_h, canvas_h):
    """Where the grown render draws `union_h` px of ink on a
    `canvas_h`-tall canvas, by anchor: bottom-anchored and
    top-anchored hug their inset, centered splits the canvas - the
    same contract `grow_to_minimum` grows under, at whatever height
    `grow_to_hold_rail` derived."""
    if position == "top":
        y0 = PAD_TOP
    elif position == "center":
        y0 = (canvas_h - union_h) // 2
    else:
        y0 = canvas_h - PAD_BOTTOM - union_h
    return (100, y0, 100 + 200, y0 + union_h)


@pytest.mark.parametrize("position,rect,expected_h", [
    ("bottom", (140, 1500, 420, 1560), 156),   # one-word caption
    ("bottom", (140, 1400, 700, 1560), 212),   # two-line card
    ("bottom", (140, 1300, 640, 1620), 372),   # three-line card
    ("top", (140, 140, 500, 220), 204),
    ("top", (140, 130, 600, 330), 252),
    ("center", (140, 900, 500, 1000), 152),
    ("center", (140, 830, 620, 1070), 292),
])
def test_every_small_box_holds_inside_the_headroom(tmp_path, position,
                                                   rect, expected_h):
    """The boundary the brief demands: one-word captions to three-line
    cards, every anchor. Each ships at the height its own placement
    derives against the 4x-law rail - 152 for centred ink that needs
    no Tilt at all, up to 372 for the three-line card - and finalizes
    inside Tilt 6912, 10% inside the measured 7680 rail. The gate
    still fires (the `placement_holds` predicate is untouched)."""
    probe_paths = _frames(tmp_path, "p", (FULL_W, FULL_H), [[rect]])
    probe_union = ink_union_of_frames(probe_paths)
    box = tighten_measured(_props(position), probe_union,
                               draw_gain=HISTORY_GAIN)
    assert box.height == expected_h
    union_h = rect[3] - rect[1]
    tight_paths = _frames(tmp_path, "t", (box.width, box.height),
                           [[_tight_ink_for(position, union_h,
                                            box.height)]])
    tight_union = ink_union_of_frames(tight_paths)
    final = finalize_box_placement(box, probe_union, tight_union,
                                   (1080, 1920),
                                   draw_gain=HISTORY_GAIN)
    ox, oy = canvas_offset(final)
    assert 0 <= ox and ox + box.width <= FULL_W
    assert 0 <= oy and oy + box.height <= FULL_H
    assert abs(final.placement["tilt"]) <= 6912.0
    assert placement_holds(final.placement, 1080, 1920) == ""

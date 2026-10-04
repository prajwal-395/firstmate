"""The punched-in TV-frame look is declarable, never hardcoded.

The captain's marker on Reel 20 (frame 538, 2026-09-09) sets the look:
V1 punched to 2.30 under the TV asset at native 1:1, captions above.
`library/tools/tv_frame.py` declares that - the factor, the asset, the
layer order - and this asserts the declarations behave: 2.30 is the
default the project or template can change, no asset means no look, and
a malformed declaration raises rather than degrading silently.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

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

    segment, longer = reel_look.frame_overlay_segments(
        small, [(0, 1), (24, 1488)], 24.0, 1080, 1920, str(tmp_path))
    assert segment["overlay_path"].endswith(".png")
    assert segment["media_type"] == "still"
    assert longer["overlay_path"] == segment["overlay_path"]
    assert segment["total_frames"] == 1
    assert longer["total_frames"] == 1464
    import numpy as np
    with Image.open(segment["overlay_path"]) as image:
        pixels = np.asarray(image.convert("RGBA"))
        alpha = image.convert("RGBA").getchannel("A")
        width, height = image.size
        # Just outside the shrunk bezel, left of centre: black, opaque.
        assert alpha.getpixel((int(width * 0.05), height // 2)) == 255
        # The window itself still shows the picture.
        assert alpha.getpixel((width // 2, height // 2)) == 0
        assert bool((pixels[..., :3] <= pixels[..., 3:4]).all())


# --------------------------------------------------------------------------
# From test_tv_power.py
#
# The old-TV switch is ONE shape, played in two directions.
#
# History: docs/evidence/resolve_test_history.md#test_tv_power.

sys.path.insert(0, str(REPO))


from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.tv_power import (
    BLACK_GAIN,
    COLLAPSE_CROP,
    DOT_SIZE,
    PICTURE_GAIN,
    switch_off_frames,
    switch_off_total,
    switch_on_frames,
    switch_on_total,
    switch_shape,
    switch_total,
    validate_collapse,
    validate_timing,
)

#: The source frame every comp below is built at. The builder takes no
#: default frame, so each call states it - the same numbers the
#: removed default carried, which is what keeps the asserted curves
#: identical.
SOURCE_RES = (1080, 1920)


def _curves(comp: str, played: int) -> dict:
    """Every animated spline in `comp`, per rendered frame, keyed by the
    node-name SUFFIX - so the global `_next_name` counter, which differs
    between two comps built in one process, cannot make a comparison
    fail for a reason nobody cares about."""
    from library.tools.fusion.transition_frames import parse_splines, value_at

    out = {}
    for name, keys in parse_splines(comp).items():
        for suffix in ("Height", "Size", "Gain"):
            if name.endswith(suffix):
                out[suffix] = [value_at(keys, f) for f in range(played)]
    return out


# ── The shape ──────────────────────────────────────────────────────


def test_both_directions_read_one_declaration():
    """`switch_on_frames` and `switch_off_frames` are the SAME call.

    Not "agree today" - the same values object, so there is nowhere for
    a per-half number to be written.
    """
    assert switch_on_frames() == switch_off_frames() == switch_shape()
    assert switch_on_total() == switch_off_total() == 18
    # An override reaches both directions identically.
    assert (switch_on_frames(collapse=12)
            == switch_off_frames(collapse=12)
            == {**switch_shape(), "collapse_frames": 12})


# ── The declaration ────────────────────────────────────────────────


def test_the_power_declaration_validates_or_raises_by_name():
    """A declared depth survives as a fraction, never int() to 0.
    `switch_on`/`switch_off` were the shape until 2026-09-11: honouring
    one would re-time one direction and leave the other behind, so it
    raises naming the replacement. 0.5 closes the band entirely (the
    one-frame flash); negative and non-numeric are mistakes, not looks.
    All raise, none clamp."""
    assert validate_timing({"collapse_crop": 0.3}, "test")[
        "collapse_crop"] == 0.3
    assert validate_timing(None, "test") == {}
    for half in ("switch_on", "switch_off"):
        with pytest.raises(ValueError) as raised:
            validate_timing({half: {"collapse_frames": 2}}, "test")
        assert "one shape" in str(raised.value).lower()
        assert "collapse_frames" in str(raised.value)
    for bad in (0.5, 0.9, -0.1, "shallow", True, None):
        with pytest.raises((ValueError, TypeError)):
            validate_collapse(bad, "test")
    assert validate_collapse(0.0, "test") == 0.0
    assert validate_collapse(0.49, "test") == 0.49
    for declaration, error in (({"collapse_crop": 0.5}, ValueError),
                               ({"flicker_frames": 3}, ValueError),
                               ({"decay_frames": -1}, ValueError),
                               ([1, 2], TypeError)):
        with pytest.raises(error):
            validate_timing(declaration, "test")


# ── What reaches the comp ──────────────────────────────────────────

def test_both_keys_draw_the_band_the_dot_and_the_gain():
    """Each direction is a masked band, a uniform Transform and a
    BrightnessContrast - the same three nodes, because it is the same
    animation.

    The band was a `Crop` node until 2026-09-10, driving
    `CropTop`/`CropBottom` - two names Fusion's Crop does not have, so
    the animation never drew and the tool fell back to its 1920x1080
    registry size at offset (0, 0), which on Fusion's bottom-left origin
    is the source's bottom-left corner.  This file asserted
    `"Crop" in comp` and passed throughout.
    """
    for key in ("tv_power_head", "tv_power_tail"):
        comp = build_effect_comp({key: True}, 300,
                                 source_res=SOURCE_RES)
        assert "RectangleMask" in comp
        assert "Background" in comp
        assert "Transform" in comp
        assert "BrightnessContrast" in comp
        # Fusion's Crop resizes the image to the crop rectangle, so it
        # cannot blank a band in place whatever you spell its inputs.
        assert "Crop" not in comp


def test_the_switch_on_opens_on_fully_black():
    """The captain's ask, measured on the curves the comp carries.

    Frame 0 is gain 0.0 - no signal at all - with the band closed to
    the line and the picture scaled to the dot.  Before 2026-09-11 it
    was gain 2.2 over a band a fifth of the frame tall: a lit first
    frame, which is what he was looking at when he wrote the marker.
    """
    played = 200
    curves = _curves(build_effect_comp(
        {"tv_power_head": True, "source_in_frame": 0,
         "source_out_frame": played - 1}, played, source_res=SOURCE_RES), played)
    assert curves["Gain"][0] == pytest.approx(BLACK_GAIN)
    assert curves["Size"][0] == pytest.approx(DOT_SIZE)
    assert curves["Height"][0] == pytest.approx(1.0 - 2 * COLLAPSE_CROP)
    # And it arrives at the picture by the end of the animation, then
    # holds there.
    assert curves["Gain"][switch_total()] == pytest.approx(PICTURE_GAIN)
    assert curves["Size"][switch_total()] == pytest.approx(1.0)
    assert curves["Height"][switch_total()] == pytest.approx(1.0)
    assert curves["Gain"][-1] == pytest.approx(PICTURE_GAIN)


def test_the_switch_off_closes_on_fully_black():
    """The reference half, unchanged: it still ends at no signal."""
    played = 200
    curves = _curves(build_effect_comp(
        {"tv_power_tail": True, "source_in_frame": 0,
         "source_out_frame": played - 1}, played, source_res=SOURCE_RES), played)
    assert curves["Gain"][0] == pytest.approx(PICTURE_GAIN)
    assert curves["Gain"][-1] == pytest.approx(BLACK_GAIN)
    assert curves["Size"][-1] == pytest.approx(DOT_SIZE)
    assert curves["Height"][-1] == pytest.approx(1.0 - 2 * COLLAPSE_CROP)


def test_switch_on_is_the_switch_off_reversed():
    """THE GATE. Every drawn curve of the switch-on is the switch-off's,
    mirrored in time.

    This is what makes "one shape in two directions" a property of the
    build rather than a claim in a docstring: re-time one direction,
    give one an extra phase, or change one's dot size, and this fails
    on the frame where they stop being each other's reverse.
    """
    played = 200
    head = _curves(build_effect_comp(
        {"tv_power_head": True, "source_in_frame": 0,
         "source_out_frame": played - 1}, played, source_res=SOURCE_RES), played)
    tail = _curves(build_effect_comp(
        {"tv_power_tail": True, "source_in_frame": 0,
         "source_out_frame": played - 1}, played, source_res=SOURCE_RES), played)

    assert set(head) == set(tail) == {"Height", "Size", "Gain"}
    last = played - 1
    for name in head:
        mirrored = [tail[name][last - f] for f in range(played)]
        assert head[name] == pytest.approx(mirrored, abs=1e-6), (
            f"the switch-on's {name} curve is not the switch-off's "
            f"reversed - the two directions have drifted apart")


def test_a_declared_retime_moves_both_directions_together():
    """A project re-timing the switch re-times it BOTH ways.

    The declaration names the shape, not a half, so there is no way to
    write a run where the set opens over one length and closes over
    another.
    """
    played = 200
    declared = validate_timing(
        {"collapse_frames": 10, "dot_frames": 5, "decay_frames": 15},
        "test")
    head = _curves(build_effect_comp(
        {"tv_power_head": True, "tv_power_head_timing": declared,
         "source_in_frame": 0, "source_out_frame": played - 1},
        played, source_res=SOURCE_RES), played)
    tail = _curves(build_effect_comp(
        {"tv_power_tail": True, "tv_power_tail_timing": declared,
         "source_in_frame": 0, "source_out_frame": played - 1},
        played, source_res=SOURCE_RES), played)
    last = played - 1
    for name in head:
        assert head[name] == pytest.approx(
            [tail[name][last - f] for f in range(played)], abs=1e-6)
    # 30 frames of animation, both ways.
    assert switch_total(declared) == 30
    assert head["Gain"][30] == pytest.approx(PICTURE_GAIN)
    assert head["Gain"][29] != pytest.approx(PICTURE_GAIN)


def test_animated_size_peak_stays_inside_the_ceiling():
    """`nodes.MAX_ANIMATED_ZOOM` refuses an animated Transform Size past
    1.15; the dot opens to and shrinks from 1.0, so the peak is 1.0."""
    for key in ("tv_power_head", "tv_power_tail"):
        played = 200
        curves = _curves(build_effect_comp(
            {key: True, "source_in_frame": 0,
             "source_out_frame": played - 1}, played, source_res=SOURCE_RES), played)
        assert max(curves["Size"]) == pytest.approx(1.0)


def test_power_blocks_wire_their_own_internal_links():
    """Every node inside a power block reads the one before it.

    `EffectBlock` wires its `input_name` to whatever precedes the block
    and reads its `output_name`; the links INSIDE a block are the
    block's own to make.  Neither power block made them, so the
    BrightnessContrast that carries the strike had no image input: the
    comp imported, Resolve accepted it, and the render died with "The
    Fusion composition at 00:00:00:00 could not be processed
    successfully" (reel 12, 2026-09-09).  Asserted on the nodes rather
    than on the serialized text so a rename cannot make it pass.
    """
    from library.tools.fusion.effects import fx
    from library.tools.fusion.nodes import FusionNode

    for block in (fx.tv_power_head(600, source_in=0, source_out=600,
                                     res=(1080, 1920)),
                  fx.tv_power_tail(600, source_in=0, source_out=600,
                                   res=(1080, 1920))):
        tools = [n for n in block.nodes if isinstance(n, FusionNode)]
        assert len(tools) > 1, "both blocks are multi-node"
        names = [n.name for n in tools]
        # A GENERATOR draws its own image and has no `Input` by design:
        # the black Background and the RectangleMask that gates it are
        # the deflection band, and demanding an image input of them
        # would be demanding one of a solid colour.
        generators = {"Background", "RectangleMask", "EllipseMask"}
        for node in tools:
            if node.name == block.input_name:
                continue
            if node.tool_type in generators:
                continue
            wired = node.inputs.get("Input")
            upstream = (wired.get("SourceOp")
                        if isinstance(wired, dict) else wired)
            assert upstream in names, (
                f"{node.name} ({node.tool_type}) has no image input inside "
                f"the block; it reads {wired!r}. A tool with no Input "
                f"cannot draw and the whole comp fails to process.")
        # The band's own two links: the Background is gated by the mask,
        # and the Merge lays that Background over the picture.
        band = next(n for n in tools if n.tool_type == "Background")
        mask = next(n for n in tools if n.tool_type == "RectangleMask")
        merge = next(n for n in tools if n.tool_type == "Merge")
        assert band.inputs["EffectMask"]["SourceOp"] == mask.name
        assert merge.inputs["Foreground"]["SourceOp"] == band.name
        # And the block's output is reachable from its input.
        assert block.output_name in names


def test_a_zero_length_phase_collapses_to_one_key():
    """A declared 0 for a phase is legal and must not write two keys on
    one frame: the state the animation is moving toward is the one that
    frame shows, either way round."""
    played = 120
    declared = validate_timing({"dot_frames": 0}, "test")
    for key in ("tv_power_head", "tv_power_tail"):
        comp = build_effect_comp(
            {key: True, f"{key}_timing": declared,
             "source_in_frame": 0, "source_out_frame": played - 1},
            played, source_res=SOURCE_RES)
        curves = _curves(comp, played)
        assert len(curves) == 3
    assert switch_total(declared) == 15


# --------------------------------------------------------------------------
# From test_punch_in_readback.py
#
# The punch-in is read back, not trusted - and refusal names the shot.
#
# Reel 09, 2026-09-09: the staging the gate deleted carried six items at
# the identity transform, each delivering (0, 656, 1080, 1264) against a
# screen window of (18, 260, 1061, 1661).  `aim_picture_row` is the punch
# pass extracted testable, with PR 862's discipline applied to it:
# `SetProperty` returns True past Resolve's silent Pan/Tilt clamp, so
# what was ASKED is judged by its return and what is HELD is graded
# against the window with the same predicate F12 grades with
# (`reel_look.uncovered_window_edges`).  A transform that did not take
# raises `PunchInLeavesBlack` at placement - never as six identical
# findings after a full build - and an incapacitated probe
# (`SubjectProbeUnavailable`) becomes a `ReelLookRefused` naming the
# shot, instead of six silent uncropped items the gate deletes.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.reel_build import aim_picture_row
from library.tools.subject_framing import SubjectProbeUnavailable


def _frame_asset(path):
    """A 1080x1920 frame asset with a transparent centre window."""
    from PIL import Image
    image = Image.new("RGBA", (1080, 1920), (10, 10, 10, 255))
    for x in range(100, 980):
        for y in range(100, 1820, 4):
            image.putpixel((x, y), (0, 0, 0, 0))
    image.save(path)
    return str(path)


def _look(path):
    from library.tools import tv_frame
    return {"asset": path, "punch_in": 2.3, "power": {},
            "origin": "test declaration",
            "rotate": tv_frame.AUTO_ROTATE}


def _window(look):
    from library.tools.tv_frame import screen_window_rect
    return screen_window_rect(look, 1080, 1920)


def _subject(cx=0.5, cy=0.32):
    return SimpleNamespace(center_x=cx, center_y=cy, width=0.1,
                           samples=12, detected=12, others=0)


def _place(source="/x/LC4932.MXF", start=0.0, end=5.0, record=0):
    return {"clip": SimpleNamespace(source_file=source),
            "source_in": start, "source_out": end,
            "snapped_record": record}


class _Item:
    """A timeline item. No `held` echoes what was set; a `held` dict
    simulates Resolve holding something else (the silent clamp)."""

    def __init__(self, held=None):
        self._set = {}
        self._held = held

    def SetProperty(self, key, value):
        self._set[key] = value
        return True

    def GetProperty(self, key=None):
        values = self._held if self._held is not None else self._set
        if key is None:
            return dict(values)
        return values.get(key)

    def GetName(self):
        return "shot"


def test_an_incapacitated_probe_refuses_the_row_naming_the_shot(tmp_path):
    """The build refuses with the cause; no doomed staging is shipped."""
    asset = _frame_asset(tmp_path / "frame.png")
    look = _look(asset)

    def _no_detector(source, start, end):
        raise SubjectProbeUnavailable("no face detector (test)")

    with pytest.raises(reel_look.ReelLookRefused) as excinfo:
        aim_picture_row("Reel 09", look, _window(look), 1080, 1920,
                        [_Item()], [_place()],
                        measure=_no_detector,
                        size_of=lambda item: (3840, 2160))
    assert "LC4932.MXF" in str(excinfo.value)


def test_a_transform_resolve_did_not_hold_is_refused(tmp_path):
    """The Reel 09 staging shape: asked for a punch-in, holding identity.

    The item reports success on every `SetProperty` but reads back the
    identity transform - what the failed staging delivered on all six
    items.  The read-back grades the HELD picture against the window
    and raises, instead of shipping six identical strips to the gate.
    """
    asset = _frame_asset(tmp_path / "frame.png")
    look = _look(asset)
    identity = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0}
    with pytest.raises(reel_look.PunchInLeavesBlack) as excinfo:
        aim_picture_row("Reel 09", look, _window(look), 1080, 1920,
                        [_Item(held=dict(identity))], [_place()],
                        measure=lambda s, a, b: _subject(),
                        size_of=lambda item: (3840, 2160),
                        project_folder=str(tmp_path))
    assert "did not take" in str(excinfo.value)


def test_a_shot_with_no_face_plays_uncropped_and_is_said(
        tmp_path, capsys):
    """Genuine absence still refuses the punch, not the build.

    A detector that looked and found no face returns None, and the shot
    plays uncropped with the reason SAID - the captain's refuse-rather-
    than-guess ruling, unchanged.  (Under the look the gate will still
    grade that uncropped shot; what changed is only that an
    incapacitated probe can no longer wear this ruling's clothes.)
    """
    asset = _frame_asset(tmp_path / "frame.png")
    look = _look(asset)
    aimed = aim_picture_row(
        "Reel 09", look, _window(look), 1080, 1920,
        [_Item()], [_place()],
        measure=lambda s, a, b: None,
        size_of=lambda item: (3840, 2160))
    assert aimed == 0
    assert "NO PUNCH-IN" in capsys.readouterr().err

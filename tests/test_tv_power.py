"""The old-TV switch is ONE shape, played in two directions.

The captain's Reel 20 marker invites the interpretation ("you can create
some animations for this"), and `library/tools/tv_power.py` is the
proposal: every timing states what it is and why, and all of them are
changeable through the project's own `tv_frame` declaration.

His Reel 09 marker of 2026-09-11 is what makes the shape single:

    "also the tv on animation should start from fully black just like
     the reverse of how the tv off animation goes to fully black"

Until then there were two animations - `line/expand/bloom` at 4/6/8 with
no dot phase and a lit first frame, against `collapse/dot/decay` at
6/3/9 ending at black.  `test_switch_on_is_the_switch_off_reversed` is
the gate that keeps them one: it evaluates BOTH comps frame by frame and
fails the moment either direction is re-timed on its own.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.tv_power import (
    BLACK_GAIN,
    COLLAPSE_CROP,
    COLLAPSE_FRAMES,
    COLLAPSE_MAX,
    COLLAPSE_MIN,
    DECAY_FRAMES,
    DOT_FRAMES,
    DOT_GAIN,
    DOT_SIZE,
    LINE_GAIN,
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

def test_the_shape_is_three_phases_totalling_18_frames():
    """0.6 s at 30 fps: reads as a switch, does not eat the bookends."""
    assert (COLLAPSE_FRAMES, DOT_FRAMES, DECAY_FRAMES) == (6, 3, 9)
    assert switch_total() == 18
    # The depth travels in the mapping but is not a length: changing
    # the look must not move the window.
    assert switch_total({**switch_shape(), "collapse_crop": 0.1}) == 18


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


def test_collapse_stops_short_of_fully_closed():
    """0.49, not 0.5: a fully closed band flashes uncropped on some
    Resolve builds.  ONE depth, shared by both directions."""
    assert COLLAPSE_CROP == 0.49
    assert DOT_SIZE == 0.05
    assert (COLLAPSE_MIN, COLLAPSE_MAX) == (0.0, 0.5)


def test_the_gains_run_from_black_to_picture():
    """Black is no signal; the dot is the spot at its hottest."""
    assert BLACK_GAIN == 0.0
    assert PICTURE_GAIN == 1.0
    assert PICTURE_GAIN < LINE_GAIN < DOT_GAIN


# ── The declaration ────────────────────────────────────────────────

def test_timing_override_merges_over_defaults():
    assert validate_timing({"collapse_frames": 2}, "test") == {
        "collapse_frames": 2, "dot_frames": 3, "decay_frames": 9,
        "collapse_crop": 0.49}


def test_declared_collapse_merges_as_a_float_not_frames():
    """A project declares the depth the way it declares the lengths -
    and it must survive as a fraction, never int() to 0."""
    assert validate_timing({"collapse_crop": 0.3}, "test")[
        "collapse_crop"] == 0.3


def test_a_per_half_declaration_is_refused_by_name():
    """`switch_on` / `switch_off` were this declaration's shape until
    2026-09-11.  Honouring one would re-time one direction and leave
    the other behind, which is exactly what the captain's marker
    closed - so it raises, and the refusal names the replacement."""
    for half in ("switch_on", "switch_off"):
        with pytest.raises(ValueError) as raised:
            validate_timing({half: {"collapse_frames": 2}}, "test")
        assert "one shape" in str(raised.value).lower()
        assert "collapse_frames" in str(raised.value)


def test_collapse_bounds_raise_naming_the_source():
    """0.5 closes the band entirely (the one-frame flash); negative and
    non-numeric are mistakes, not looks.  All raise, none clamp."""
    for bad in (0.5, 0.9, -0.1, "shallow", True, None):
        with pytest.raises((ValueError, TypeError)):
            validate_collapse(bad, "test")
    assert validate_collapse(0.0, "test") == 0.0
    assert validate_collapse(0.49, "test") == 0.49
    with pytest.raises(ValueError):
        validate_timing({"collapse_crop": 0.5}, "test")


def test_unknown_phase_key_raises():
    with pytest.raises(ValueError):
        validate_timing({"flicker_frames": 3}, "test")


def test_negative_count_raises():
    with pytest.raises(ValueError):
        validate_timing({"decay_frames": -1}, "test")


def test_non_mapping_raises():
    with pytest.raises(TypeError):
        validate_timing([1, 2], "test")
    assert validate_timing(None, "test") == {}


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


def test_absent_keys_draw_nothing():
    comp = build_effect_comp({}, 300, source_res=SOURCE_RES)
    assert "PowerBand" not in comp
    assert "PowerDot" not in comp
    assert "PowerDecay" not in comp


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

"""The old-TV switch-on / switch-off animation is declared, not compiled.

The captain's Reel 20 marker invites the interpretation ("you can create
some animations for this"), and `library/tools/tv_power.py` is the
proposal: every timing states what it is and why, and all of them are
changeable through the project's own `tv_frame` declaration.  This
asserts the vocabulary (lengths, merge rules, refusals) and that the
keys draw real Fusion nodes through `build_effect_comp` - the contract
that says a planner emitting a name nothing reads gets nothing drawn.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from library.tools.tv_power import (
    COLLAPSE_CROP,
    DOT_SIZE,
    SWITCH_OFF_COLLAPSE_FRAMES,
    SWITCH_OFF_DECAY_FRAMES,
    SWITCH_OFF_DOT_FRAMES,
    SWITCH_ON_BLOOM_FRAMES,
    SWITCH_ON_COLLAPSE_CROP,
    SWITCH_ON_COLLAPSE_MAX,
    SWITCH_ON_COLLAPSE_MIN,
    SWITCH_ON_EXPAND_FRAMES,
    SWITCH_ON_LINE_FRAMES,
    switch_off_frames,
    switch_off_total,
    switch_on_frames,
    switch_on_total,
    validate_collapse,
    validate_timing,
)
from library.tools.fusion.comp_builder import build_effect_comp


def test_switch_on_is_three_phases_totalling_18_frames():
    """0.6 s at 30 fps: reads as a switch, does not eat the bookends."""
    assert (SWITCH_ON_LINE_FRAMES, SWITCH_ON_EXPAND_FRAMES,
            SWITCH_ON_BLOOM_FRAMES) == (4, 6, 8)
    assert switch_on_total() == 18
    # The depth travels in the mapping but is not a length: changing
    # the look must not move the window.
    assert switch_on_total({**switch_on_frames(), "collapse_crop": 0.1}) == 18


def test_switch_off_is_three_phases_totalling_18_frames():
    assert (SWITCH_OFF_COLLAPSE_FRAMES, SWITCH_OFF_DOT_FRAMES,
            SWITCH_OFF_DECAY_FRAMES) == (6, 3, 9)
    assert switch_off_total() == 18


def test_collapse_stops_short_of_fully_closed():
    """0.49, not 0.5: a fully closed crop flashes uncropped on some
    Resolve builds.  This is the SWITCH-OFF's depth, unjudged and
    unchanged."""
    assert COLLAPSE_CROP == 0.49
    assert DOT_SIZE == 0.05


def test_switch_on_holds_a_fifth_not_a_sliver():
    """The captain's 2026-09-10 ruling: keep the effect, soften the
    opening sliver.  0.40 keeps a fifth of the picture where 0.49 kept
    2%: the strike still reads (from the untouched 2.2x gain spike),
    the opening frame shows picture, and the 0.5 guard keeps ten times
    the margin (0.10 vs 0.01)."""
    assert SWITCH_ON_COLLAPSE_CROP == 0.40
    assert 1.0 - 2 * SWITCH_ON_COLLAPSE_CROP == pytest.approx(0.20)
    assert switch_on_frames()["collapse_crop"] == 0.40


def test_timing_override_merges_over_defaults():
    timing = validate_timing(
        {"switch_on": {"line_frames": 2}}, "test", half="both")
    assert timing == {"switch_on": {
        "line_frames": 2, "expand_frames": 6, "bloom_frames": 8,
        "collapse_crop": 0.40}}


def test_declared_collapse_merges_as_a_float_not_frames():
    """A project declares the depth the way it declares the lengths -
    and it must survive as a fraction, never int() to 0."""
    timing = validate_timing(
        {"switch_on": {"collapse_crop": 0.49}}, "test", half="both")
    assert timing["switch_on"]["collapse_crop"] == 0.49


def test_collapse_bounds_raise_naming_the_source():
    """0.5 closes the crop entirely (the one-frame flash); negative and
    non-numeric are mistakes, not looks.  All raise, none clamp."""
    for bad in (0.5, 0.9, -0.1, "shallow", True, None):
        with pytest.raises((ValueError, TypeError)):
            validate_collapse(bad, "test")
    assert validate_collapse(0.0, "test") == 0.0
    assert validate_collapse(0.49, "test") == 0.49
    with pytest.raises(ValueError):
        validate_timing(
            {"switch_on": {"collapse_crop": 0.5}}, "test", half="both")


def test_collapse_is_not_a_switch_off_key():
    """The switch-off is unjudged: naming a depth there refuses loudly
    rather than travelling to a reader that does not exist."""
    with pytest.raises(ValueError):
        validate_timing(
            {"switch_off": {"collapse_crop": 0.3}}, "test", half="both")


def test_unknown_half_raises():
    with pytest.raises(ValueError):
        validate_timing({"power_on": {}}, "test", half="both")


def test_unknown_phase_key_raises():
    with pytest.raises(ValueError):
        validate_timing(
            {"switch_on": {"flicker_frames": 3}}, "test", half="both")


def test_negative_count_raises():
    with pytest.raises(ValueError):
        validate_timing(
            {"switch_off": {"decay_frames": -1}}, "test", half="both")


def test_non_mapping_raises():
    with pytest.raises(TypeError):
        validate_timing([1, 2], "test", half="both")
    assert validate_timing(None, "test", half="both") == {}


def test_head_key_draws_crop_and_bloom():
    """The switch-on is a Crop opening plus a BrightnessContrast spike."""
    comp = build_effect_comp({"tv_power_head": True}, 300)
    assert "Crop" in comp
    assert "BrightnessContrast" in comp
    # The strike gain the module declares.
    assert "2.2" in comp


def test_tail_key_draws_collapse_dot_and_decay():
    comp = build_effect_comp({"tv_power_tail": True}, 300)
    assert "Crop" in comp
    assert "Transform" in comp
    assert "BrightnessContrast" in comp
    # Ends at no signal.
    assert "PowerDecay1Gain" in comp


def test_absent_keys_draw_nothing():
    comp = build_effect_comp({}, 300)
    assert "PowerCrop" not in comp
    assert "PowerBloom" not in comp
    assert "PowerDecay" not in comp


def test_custom_timing_reaches_the_comp():
    """A declared override changes the keyframes, not just the record."""
    comp = build_effect_comp(
        {"tv_power_head": True,
         "tv_power_head_timing": {"line_frames": 2, "expand_frames": 6,
                                  "bloom_frames": 8,
                                  "collapse_crop": 0.40}},
        300)
    # Line holds 0..2 instead of the default 0..4, at the declared depth.
    assert "[2] = { 0.4" in comp


def test_default_head_comp_holds_a_fifth():
    """The softened opening is drawn, not just declared: the default
    head holds 0.4 at the strike, keeping a fifth of the picture."""
    comp = build_effect_comp({"tv_power_head": True}, 300)
    assert "[4] = { 0.4" in comp
    assert "[4] = { 0.49" not in comp


def test_old_strike_stays_declarable():
    """0.49 was a look, and a project that wants it back says so in
    one key - the engine did not remove the number, it moved it into
    the declaration."""
    comp = build_effect_comp(
        {"tv_power_head": True,
         "tv_power_head_timing": {**switch_on_frames(),
                                  "collapse_crop": 0.49}},
        300)
    assert "[4] = { 0.49" in comp


def test_tail_still_closes_to_the_shared_guard():
    """The switch-off is untouched: its collapse still meets at 0.49
    even though the head now opens from 0.40."""
    comp = build_effect_comp({"tv_power_tail": True}, 300)
    assert "0.49" in comp
    assert "0.4," not in comp and "[0] = { 0.4" not in comp


def test_animated_size_peak_stays_inside_the_ceiling():
    """`nodes.MAX_ANIMATED_ZOOM` refuses an animated Transform Size past
    1.15; the dot shrinks FROM 1.0, so the peak is exactly 1.0."""
    comp = build_effect_comp({"tv_power_tail": True}, 300)
    assert "PowerDot1Size" in comp


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
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from library.tools.fusion.effects import fx
    from library.tools.fusion.nodes import FusionNode

    for block in (fx.tv_power_head(600, source_in=0, source_out=600),
                  fx.tv_power_tail(600, source_in=0, source_out=600)):
        tools = [n for n in block.nodes if isinstance(n, FusionNode)]
        assert len(tools) > 1, "both blocks are multi-node"
        names = [n.name for n in tools]
        # Every tool except the block's declared input must read another
        # tool of the same block on its image Input.
        for node in tools:
            if node.name == block.input_name:
                continue
            wired = node.inputs.get("Input")
            upstream = (wired.get("SourceOp")
                        if isinstance(wired, dict) else wired)
            assert upstream in names, (
                f"{node.name} ({node.tool_type}) has no image input inside "
                f"the block; it reads {wired!r}. A tool with no Input "
                f"cannot draw and the whole comp fails to process.")
        # And the block's output is reachable from its input.
        assert block.output_name in names

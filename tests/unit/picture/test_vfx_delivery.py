"""Every VFX type the handoff offers must draw something.

Three of the five advertised effects rendered nothing: `zoom_emphasis`
emitted `zoom_percent`, `screen_shake` emitted `intensity_px`, `cut_in`
emitted `scale_factor`, and the renderer dispatches on parameter NAMES,
none of which it read. `slow_zoom` was not in the intensity map and
silently became a default 3% zoom. `cut_out` was removed (captain's
ruling 2026-08-17, superseded by the framing parameter).
"""
import re

import pytest

from library.steps.step_4_03_plan_vfx.post_bridge import (
    EFFECT_ALIASES,
    TOOLKIT_PARAMETERS,
    WITHDRAWN_ALIASES,
    resolve_vfx,
)
from library.tools.fusion.comp_builder import build_effect_comp

CLIP_DUR = 120
#: The source frame every comp below is built at. The builder takes no
#: default frame, so each call states it - the same numbers the
#: removed default carried.
SOURCE_RES = (1080, 1920)


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


# ── Every name the toolkit advertises reaches the renderer ──
#
# This is the guarantee `INTENSITY_MAP` used to carry by construction. It
# is removed (captain, 2026-09-02: a three-point scale is still the engine
# choosing how strong an effect is), so the VALUES are the plan's - but
# the NAMES still have to reach a reader, or an effect is recorded as
# planned and draws nothing (AGENTS.md §10.2).


def test_the_ken_burns_spelling_reaches_the_renderer():
    """The extension must draw, not just resolve: a reasoned `ken_burns`
    entry becomes a drift entry whose params change the comp."""
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.03},
          "rationale": "a locked hold that goes dead under the point"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR,
                                source_res=SOURCE_RES)
    drawn = build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR,
        source_res=SOURCE_RES)
    assert drawn != neutral


def _value_for(name):
    """A non-neutral value in the shape the renderer expects.

    Only has to differ from the default: this asks whether the NAME
    reaches a reader, not what any number does. Zooms stay under the comp
    validator's animated-Size refusal in `fusion/nodes.py`, which is a
    separate, pre-existing rule (AGENTS.md §5).
    """
    if name.endswith("_frames"):
        return 3
    if name.startswith("pan_"):
        return (0.45, 0.5)
    if name.startswith("shake_"):
        return 0.004
    return 1.03


@pytest.mark.parametrize("effect_type,names", sorted(
    (k, v) for k, v in TOOLKIT_PARAMETERS.items()))
def test_every_advertised_parameter_name_changes_the_comp(effect_type, names):
    """Every advertised name must change what is drawn.

    `zoom_percent`, `intensity_px` and `scale_factor` were advertised and
    read by nothing, which is how three of five effects rendered nothing
    while the manifest recorded them as planned. `pan_start` is the same
    shape and is why it is NOT in the enumeration - `fx.zoom` takes it and
    draws nothing with it.

    A name is added to the effect's own first name rather than tested
    alone, because some are MODIFIERS: `shake_decay_frames` shapes the
    shake that `shake_x` starts and reaches no dispatch on its own.
    """
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR,
                                source_res=SOURCE_RES)
    base = {names[0]: _value_for(names[0]), "vignette": False}
    base_comp = build_effect_comp(base, CLIP_DUR, source_res=SOURCE_RES)
    assert base_comp != neutral, (
        f"{effect_type}: `{names[0]}` is advertised and draws nothing")

    for name in names[1:]:
        if effect_type == "zoom_emphasis" and name in {
                "zoom_in_seconds", "zoom_out_seconds"}:
            # Timing is resolved together with its two material anchors
            # into frame parameters before the comp builder can read it.
            continue
        comp = build_effect_comp(
            dict(base, **{name: _value_for(name)}), CLIP_DUR,
            source_res=SOURCE_RES)
        assert comp != base_comp, (
            f"{effect_type}: `{name}` is advertised and changes nothing")


def test_an_effect_whose_params_reach_no_reader_is_dropped(capsys):
    """`zoom_in`, `amplitude` and `zoom` are not names the renderer reads.

    Passing them through would put an effect on the manifest that the
    viewer never sees - and say nothing. Nothing is substituted.
    """
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "screen_shake",
          "params": {"amplitude": 4, "frequency": 12}}],
        _spine(1, 2),
    )
    assert resolved == []
    err = capsys.readouterr().err
    assert "screen_shake" in err and "shake_x" in err


def test_only_the_unreadable_names_are_dropped_from_a_usable_entry():
    """An entry that reaches a reader survives, carrying only what does."""
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "slow_zoom_in",
          "params": {"zoom_start": 1.0, "zoom_end": 1.4, "bogus": 9},
          "rationale": "a static hold that wants a drift"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["params"] == {"zoom_start": 1.0, "zoom_end": 1.4}


def test_no_value_is_bounded_or_substituted():
    """The plan's number reaches the comp untouched.

    `INTENSITY_MAP` capped every zoom at 1.04 by citing AGENTS.md - a
    document this step never reads - and the captain removed it: how far
    a zoom travels is the plan's decision.
    """
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom_start": 1.9, "zoom_mid": 1.9, "zoom_end": 1.9}}],
        _spine(1, 2),
    )
    assert resolved[0]["params"] == {
        "zoom_start": 1.9, "zoom_mid": 1.9, "zoom_end": 1.9}
    assert "1.9" in build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR,
        source_res=SOURCE_RES)


def test_a_static_reframe_is_actually_drawn():
    """fx.zoom used to return an empty block whenever start == mid == end,
    so a cut_in produced a Transform with Size left at its default."""
    comp = build_effect_comp(
        {"zoom_start": 1.25, "zoom_mid": 1.25, "zoom_end": 1.25,
         "vignette": False}, CLIP_DUR, source_res=SOURCE_RES)
    assert "Transform" in comp
    assert "1.25" in comp
    # An identity zoom still draws nothing.
    comp = build_effect_comp(
        {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
         "vignette": False}, CLIP_DUR, source_res=SOURCE_RES)
    assert "Transform" not in comp


def test_screen_shake_decays_to_stillness():
    comp = build_effect_comp(
        dict({"shake_x": 0.0037, "shake_y": 0.0037, "shake_decay_frames": 5}, vignette=False),
        CLIP_DUR, source_res=SOURCE_RES)
    # Every key past the decay window sits at the neutral centre.
    settled = re.findall(r"\[(\d+)\] = \{ 0\.5,", comp)
    assert settled, "the shake never settles"
    assert max(int(f) for f in settled) > 5


# ── The bridge does not invent effects ──

def test_an_aliased_effect_type_resolves():
    """An alias may RENAME an effect. `push_in` and `zoom_emphasis` are
    two names for the same punch-and-settle, so the mapping states a
    fact rather than making a choice."""
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "push_in",
          "params": {"zoom_start": 1.0, "zoom_mid": 1.04,
                     "zoom_end": 1.0, "zoom_in_seconds": 0.67,
                     "zoom_out_seconds": 0.67},
          "anchor": {"frame": 30}, "anchor_end": {"frame": 60}}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == EFFECT_ALIASES["push_in"]
    assert resolved[0]["params"]["zoom_mid"] > 1.0


def test_an_alias_that_chose_a_direction_is_withdrawn(capsys):
    """`slow_zoom` names no direction.

    It used to resolve to `slow_zoom_in`, which answers "which way?" on
    the planner's behalf. A plan naming one is dropped with the toolkit
    listed, so the editor says which they meant.  (`ken_burns` used to
    sit beside it and no longer does: since 2026-09-09 it is the
    captain's name for the drift move, with the direction read off its
    own params - see tests/unit/context/test_plan_values.py.)"""
    for alias in WITHDRAWN_ALIASES:
        resolved = resolve_vfx(
            [{"target_block_position": 1, "effect_type": alias,
              "params": {"zoom_start": 1.0, "zoom_mid": 1.04,
                          "zoom_end": 1.0}}],
            _spine(1, 2),
        )
        assert resolved == []
        err = capsys.readouterr().err
        assert alias in err and "direction" in err


def test_a_builtin_fusion_effect_passes_through_without_parameters():
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "advanced_camera_shake"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["params"] == {}


def test_dropping_an_effect_frees_its_block_for_another(capsys):
    """An unknown effect type is dropped, said, never defaulted - and the
    drop must not consume the block's one-effect slot."""
    resolved = resolve_vfx(
        [
            {"target_block_position": 1, "effect_type": "sparkle_blast",
             "params": {"zoom_start": 1.0, "zoom_end": 1.04}},
            {"target_block_position": 1, "effect_type": "slow_zoom_in",
             "params": {"zoom_start": 1.0, "zoom_end": 1.04},
             "rationale": "a static hold that wants a drift"},
        ],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"
    assert "sparkle_blast" in capsys.readouterr().err

"""Every VFX type the handoff offers must draw something.

Three of the five advertised effects rendered nothing: `zoom_emphasis`
emitted `zoom_percent`, `screen_shake` emitted `intensity_px`, `cut_in`
emitted `scale_factor`, and the renderer dispatches on parameter NAMES,
none of which it read. Two more (`slow_zoom`, `cut_out`) were not in the
intensity map at all and silently became a default 3% zoom while keeping
their own label.
"""
import pathlib
import re

import pytest

from library.steps.step_4_03_plan_vfx.post_bridge import (
    EFFECT_ALIASES,
    INTENSITY_MAP,
    resolve_vfx,
)
from library.tools.fusion.comp_builder import build_effect_comp

REPO = pathlib.Path(__file__).resolve().parent.parent
HANDOFF = REPO / "library/steps/step_4_03_plan_vfx/handoff.md"
INTENSITIES = ("subtle", "moderate", "strong")
CLIP_DUR = 120


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _handoff_effect_types() -> set:
    table = HANDOFF.read_text().split("### Effect toolkit:", 1)[1]
    table = table.split("**DaVinci Resolve Built-in", 1)[0]
    return {
        m.group(1)
        for line in table.splitlines() if line.startswith("| `")
        for m in [re.match(r"\|\s*`([^`]+)`", line)]
        if m
    }


def test_the_handoff_offers_exactly_the_effects_the_bridge_resolves():
    assert _handoff_effect_types() == set(INTENSITY_MAP)


@pytest.mark.parametrize("effect_type", sorted(INTENSITY_MAP))
@pytest.mark.parametrize("intensity", INTENSITIES)
def test_every_effect_draws_real_nodes(effect_type, intensity):
    params = dict(INTENSITY_MAP[effect_type][intensity])
    # vignette is a look decision made elsewhere; switch it off so the
    # nodes counted here are the effect's own.
    params["vignette"] = False
    comp = build_effect_comp(params, CLIP_DUR)
    assert "Tools = {" in comp
    assert comp.count("= ") > 4, f"{effect_type}/{intensity} drew nothing"


@pytest.mark.parametrize("effect_type", sorted(INTENSITY_MAP))
def test_intensity_changes_what_is_drawn(effect_type):
    """Six of eight effects used to render the identical default zoom."""
    comps = {
        intensity: build_effect_comp(
            dict(INTENSITY_MAP[effect_type][intensity], vignette=False),
            CLIP_DUR)
        for intensity in INTENSITIES
    }
    assert len(set(comps.values())) > 1, f"{effect_type} ignores intensity"


def test_zoom_emphasis_punches_in_and_settles_back():
    params = INTENSITY_MAP["zoom_emphasis"]["moderate"]
    assert params["zoom_start"] == params["zoom_end"] == 1.0
    assert params["zoom_mid"] > 1.0


def test_cut_in_and_cut_out_reframe_in_opposite_directions():
    assert INTENSITY_MAP["cut_in"]["moderate"]["zoom_start"] > 1.0
    assert INTENSITY_MAP["cut_out"]["moderate"]["zoom_start"] < 1.0


def test_a_static_reframe_is_actually_drawn():
    """fx.zoom used to return an empty block whenever start == mid == end,
    so a cut_in produced a Transform with Size left at its default."""
    comp = build_effect_comp(
        {"zoom_start": 1.25, "zoom_mid": 1.25, "zoom_end": 1.25,
         "vignette": False}, CLIP_DUR)
    assert "Transform" in comp
    assert "1.25" in comp


def test_an_identity_zoom_still_draws_nothing():
    comp = build_effect_comp(
        {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
         "vignette": False}, CLIP_DUR)
    assert "Transform" not in comp


def test_screen_shake_decays_to_stillness():
    comp = build_effect_comp(
        dict(INTENSITY_MAP["screen_shake"]["strong"], vignette=False),
        CLIP_DUR)
    # Every key past the decay window sits at the neutral centre.
    settled = re.findall(r"\[(\d+)\] = \{ 0\.5,", comp)
    assert settled, "the shake never settles"
    assert max(int(f) for f in settled) > 5


# ── The bridge does not invent effects ──

def test_an_unknown_effect_type_is_dropped_not_defaulted(capsys):
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "sparkle_blast"}],
        _spine(1, 2),
    )
    assert resolved == []
    assert "sparkle_blast" in capsys.readouterr().err


def test_an_aliased_effect_type_resolves():
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "slow_zoom"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == EFFECT_ALIASES["slow_zoom"]
    assert resolved[0]["params"]["zoom_end"] > 1.0


def test_a_builtin_fusion_effect_passes_through_without_parameters():
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "advanced_camera_shake"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["params"] == {}


def test_dropping_an_effect_frees_its_block_for_another():
    """The drop must not consume the block's one-effect slot."""
    resolved = resolve_vfx(
        [
            {"target_block_position": 1, "effect_type": "sparkle_blast"},
            {"target_block_position": 1, "effect_type": "slow_zoom_in"},
        ],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"

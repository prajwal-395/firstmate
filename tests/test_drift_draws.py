"""A planned drift must move the framing, and prove it off the comp.

`resolve_vfx` checks drift VALUES never - a magnitude is taste, so no
bound, no clamp, no substitution. The degenerate case rides along
with that ruling: a reasoned `slow_zoom_in` with equal zooms resolves
as PLANNED, reaches `fx.zoom`, and builds an empty block. The manifest
records motion the viewer never sees, and nothing anywhere says so -
the TV switch-off all over again, 0 of 72 frames changed, no error.

`treatment_verify.verify_drift` is the deterministic half that looks:
it samples the Transform Size spline at the first and last rendered
frames, and a drift that did not move is undone by `remove_drift`
the way a failing treatment is - loudly, receipted, in the applier.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools import treatment_verify as tv

CLIP_DUR = 600
PLAYED = 72
#: The source frame every verify call below states. The verify layer
#: takes no default frame, so each call states it - the same numbers
#: the removed builder defaults carried.
SOURCE_RES = (1080, 1920)
"""The TV switch-off's own frame count: a 3-second window of 30 fps
pool footage cut onto a 24000/1001 reel timeline."""


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _reasoned_drift(**params):
    return {"target_block_position": 1, "effect_type": "slow_zoom_in",
            "params": dict(params),
            "rationale": "a locked hold that goes dead under the point"}


def test_an_equal_zoom_drift_resolves_as_planned_but_builds_neutral():
    """The shipped defect, characterized with old APIs alone.

    Values are never bounded at plan time, so equal zooms resolve -
    and the renderer builds an empty zoom block, byte-identical to no
    drift at all. This passes before and after: it is the defect the
    gate below exists to catch.
    """
    resolved = resolve_vfx(
        [_reasoned_drift(zoom_start=1.0, zoom_end=1.0)], _spine(1, 2))
    assert len(resolved) == 1
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR,
                                source_res=SOURCE_RES)
    drawn = build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR,
        source_res=SOURCE_RES)
    assert drawn == neutral


def test_equal_zoom_drift_fails_the_draw_gate():
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0},
        CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
    assert verdict["passed"] is False
    assert verdict["failure"] == "drew_nothing"
    assert verdict["changed_count"] == 0


def test_a_push_in_moves_start_to_end():
    """The pixels proof: first vs last rendered frame, off the comp."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.02, "zoom_end": 1.04,
         "source_in_frame": 0, "source_out_frame": 90},
        CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is True
    assert verdict["end_value"] > verdict["start_value"]
    assert verdict["changed_count"] > 0


def test_an_anchored_cut_in_draws_inside_its_window():
    """A word-anchored punch returns to neutral outside its word span."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.15, "zoom_mid": 1.15, "zoom_end": 1.15,
         "effect_window_frames": [6, 11]},
        CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)

    assert verdict["passed"] is True
    assert verdict["changed_frames"] == list(range(6, 12))
    assert verdict["start_value"] == 1.0
    assert verdict["end_value"] == 1.0
    assert verdict["motion_over_time"] is True


def test_undo_restores_byte_identical_comp():
    """Removing a failed drift returns the exact undrifted bytes."""
    effects = {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
               "tv_power_head": True}
    final, row = tv.verify_and_undo_drift(
        effects, CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
    assert row["undone"] is True
    assert row["failure"] == "drew_nothing"
    assert final == {"tv_power_head": True}
    assert (build_effect_comp(final, CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
            == build_effect_comp({"tv_power_head": True}, CLIP_DUR,
                                 played_frames=PLAYED, source_res=SOURCE_RES))

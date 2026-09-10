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
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR)
    drawn = build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR)
    assert drawn == neutral


def test_equal_zoom_drift_fails_the_draw_gate():
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0},
        CLIP_DUR, played_frames=PLAYED)
    assert verdict["passed"] is False
    assert verdict["failure"] == "drew_nothing"
    assert verdict["changed_count"] == 0


def test_a_push_in_moves_start_to_end():
    """The pixels proof: first vs last rendered frame, off the comp."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.02, "zoom_end": 1.04,
         "source_in_frame": 0, "source_out_frame": 90},
        CLIP_DUR, played_frames=PLAYED)
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is True
    assert verdict["end_value"] > verdict["start_value"]
    assert verdict["changed_count"] > 0


def test_a_pull_out_moves_the_other_way():
    # Peaks stay under the comp validator's animated-Size cap
    # (`fusion/nodes.py`: peak > MAX_ANIMATED_ZOOM is refused as too
    # aggressive).
    verdict = tv.verify_drift(
        {"zoom_start": 1.04, "zoom_mid": 1.02, "zoom_end": 1.0,
         "source_in_frame": 0, "source_out_frame": 90},
        CLIP_DUR, played_frames=PLAYED)
    assert verdict["passed"] is True
    assert verdict["end_value"] < verdict["start_value"]


def test_a_pool_to_timeline_mismatch_still_draws():
    """The real mismatch: a 90-source-frame span rendering 72 frames.

    The ramp is cut short, never parked past the end: the last
    rendered frame sits partway along it, and the framing still moved.
    """
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.02, "zoom_end": 1.04,
         "source_in_frame": 360, "source_out_frame": 450},
        CLIP_DUR, played_frames=PLAYED)
    assert verdict["passed"] is True
    assert verdict["end_value"] > verdict["start_value"]


def test_a_constant_reframe_passes_without_motion():
    """A static zoom draws but never moves: SAID, never failed.

    Failing it would be a gate failing correct output (AGENTS.md
    10.4) - `cut_in` is a deliberate constant reframe on this same
    path, and the gate cannot tell it from a drift by values alone.
    """
    verdict = tv.verify_drift(
        {"zoom_start": 1.5, "zoom_mid": 1.5, "zoom_end": 1.5},
        CLIP_DUR, played_frames=PLAYED)
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is False


def test_a_pan_only_drift_passes_without_motion():
    """A static recentre shifts the picture: kept, and said to be still."""
    verdict = tv.verify_drift(
        {"pan_end": (0.45, 0.5)}, CLIP_DUR, played_frames=PLAYED)
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is False


def test_no_drift_keys_arms_nothing():
    verdict = tv.verify_drift({"vignette": False}, CLIP_DUR,
                              played_frames=PLAYED)
    assert verdict["passed"] is True
    assert verdict["armed_nothing"] is True


def test_undo_restores_byte_identical_comp():
    """Removing a failed drift returns the exact undrifted bytes."""
    effects = {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
               "tv_power_head": True}
    final, row = tv.verify_and_undo_drift(
        effects, CLIP_DUR, played_frames=PLAYED)
    assert row["undone"] is True
    assert row["failure"] == "drew_nothing"
    assert final == {"tv_power_head": True}
    assert (build_effect_comp(final, CLIP_DUR, played_frames=PLAYED)
            == build_effect_comp({"tv_power_head": True}, CLIP_DUR,
                                 played_frames=PLAYED))


def test_unarmed_drift_writes_no_row():
    final, row = tv.verify_and_undo_drift(
        {"tv_power_head": True}, CLIP_DUR, played_frames=PLAYED)
    assert row is None
    assert final == {"tv_power_head": True}


def test_a_resolved_reel_drift_reaches_a_moving_comp():
    """The whole reels path, resolve to manifest to drawn motion."""
    from library.tools import reel_look

    fps = 24000 / 1001
    placements = [{
        "clip": type("Clip", (), {
            "source_file": "/a.mxf", "timeline_start": 0.0,
            "source_in": 0.0})(),
        "source_in": 0.0,
        "source_out": 5.0,
        "record": 0.0,
        "snapped_record": 0,
        "speaker": "Craig",
    }]
    spine = reel_look.motion_spine(placements, fps)
    resolved, record = reel_look.resolve_motion(
        [{"target_block_position": 0, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.04},
          "rationale": "this shot narrows onto one figure"}],
        spine, fps)
    assert record["basis"] == reel_look.MOTION_PLANNED
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, resolved, fps)
    effects = manifest["fusion_effects"]["per_clip"][
        reel_look.clip_label(0)]
    verdict = tv.verify_drift(effects, 600, played_frames=120)
    assert verdict["passed"] is True
    assert verdict["end_value"] > verdict["start_value"]

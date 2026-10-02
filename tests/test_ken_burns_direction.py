"""Ken Burns is the drift move under the captain's name - an EXTENSION.

PR 777 landed reasoned kinetic motion: `slow_zoom_in` / `slow_zoom_out`
refuse to apply without a stated per-shot rationale.  The captain then
asked for "a lot of ken burns to emphasize points" (2026-09-09), naming
the same move.  So `ken_burns` is accepted as a spelling, with the
direction DERIVED from the entry's own `zoom_start` / `zoom_end` and the
same rationale bar: this extends PR 777 rather than adding a second
motion system beside it.  Two overlapping motion systems would be worse
than none, so the resolved effect IS the drift effect - the renderer
reads it unchanged.

A `ken_burns` entry whose params state no direction is dropped as
`ken_burns_without_direction`; one with no rationale is dropped as
`no_stated_reason`, like every other drift entry.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import (
    _derive_ken_burns_direction,
    resolve_vfx,
)


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _ken_burns(position=1, **extra):
    entry = {"target_block_position": position,
             "effect_type": "ken_burns",
             "params": {"zoom_start": 1.0, "zoom_end": 1.03},
             "rationale": "a locked hold that goes dead under the point"}
    entry.update(extra)
    return entry


def test_direction_is_derived_from_the_params():
    assert _derive_ken_burns_direction(
        {"zoom_start": 1.0, "zoom_end": 1.03}) == "slow_zoom_in"
    assert _derive_ken_burns_direction(
        {"zoom_start": 1.03, "zoom_end": 1.0}) == "slow_zoom_out"
    assert _derive_ken_burns_direction(
        {"zoom_start": 1.0, "zoom_end": 1.0}) is None
    assert _derive_ken_burns_direction({"zoom_start": 1.0}) is None
    assert _derive_ken_burns_direction({}) is None
    assert _derive_ken_burns_direction("zoom") is None


def test_push_in_resolves_as_the_drift_effect():
    """EXTENSION, not a second system: the resolved entry IS a
    `slow_zoom_in`, which is what the renderer dispatches on."""
    dropped = []
    resolved = resolve_vfx([_ken_burns()], _spine(1, 2), dropped=dropped)
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"
    assert resolved[0]["params"] == {"zoom_start": 1.0, "zoom_end": 1.03}
    assert dropped == []


def test_directionless_params_get_no_motion():
    """Equal zooms name neither way, and an unstated reason is no reason:
    both dropped by name, never defaulted."""
    dropped = []
    resolved = resolve_vfx(
        [_ken_burns(params={"zoom_start": 1.02, "zoom_end": 1.02})],
        _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert [d.reason for d in dropped] == ["ken_burns_without_direction"]

    # The same bar as PR 777: emphasis is the reason, and it must be
    # stated - a move every shot gets is what the captain rejected.
    entry = _ken_burns()
    del entry["rationale"]
    dropped = []
    resolved = resolve_vfx([entry], _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert [d.reason for d in dropped] == ["no_stated_reason"]

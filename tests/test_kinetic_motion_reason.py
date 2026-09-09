"""Kinetic motion applies only where a per-shot reason is stated.

Captain's ruling 2026-09-08: motion on a static shot is allowed WHEN THE
MODEL CAN STATE WHY THAT SHOT WANTS IT, and is never a blanket rule or a
default. A push that arrives because every static shot gets a push is
exactly what this refuses - the same boundary as the standing "no house
look" rule: the reasoning must be per-shot and stated, and a shot with no
reason gets no motion.

The mechanical half is in step 4.03's post-bridge: a drift entry
(`slow_zoom_in` / `slow_zoom_out` - the moves that put life on a static
hold) whose `rationale` is missing or blank is DROPPED with reason
`no_stated_reason`, recorded in `planning_basis` like every other drop.
The engine half already holds: `inject_default_ken_burns` is gone and
nothing adds motion the plan did not ask for
(tests/test_no_creative_floors.py).

Scope is deliberate: the ruling is about motion on static shots, not
about emphasis effects. A `zoom_emphasis` on a key word is a different
decision with its own grounds, and its path is unchanged.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
from library.tools.vfx_plan_basis import DROP_REASONS


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _drift(position=1, **extra):
    entry = {"target_block_position": position,
             "effect_type": "slow_zoom_in",
             "params": {"zoom_start": 1.0, "zoom_end": 1.03}}
    entry.update(extra)
    return entry


def test_no_stated_reason_is_a_declared_drop_reason():
    """A new drop branch has to say what it is before it can go quiet -
    `DroppedEntry` refuses a reason outside `DROP_REASONS` by name."""
    assert "no_stated_reason" in DROP_REASONS
    assert len(DROP_REASONS["no_stated_reason"].split()) >= 4


def test_drift_without_a_stated_reason_gets_no_motion():
    """THE refusal input: a drift entry with params but no `rationale`
    key must not get motion."""
    dropped = []
    resolved = resolve_vfx([_drift()], _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert len(dropped) == 1
    assert dropped[0].reason == "no_stated_reason"
    assert dropped[0].target_block_position == 1


def test_blank_rationale_is_no_rationale():
    dropped = []
    resolved = resolve_vfx(
        [_drift(rationale="   ")], _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert [d.reason for d in dropped] == ["no_stated_reason"]


def test_slow_zoom_out_needs_a_reason_too():
    """Both directions of the drift are motion on a static shot; the
    direction does not exempt either."""
    dropped = []
    resolved = resolve_vfx(
        [_drift(effect_type="slow_zoom_out")], _spine(1, 2),
        dropped=dropped)
    assert resolved == []
    assert [d.reason for d in dropped] == ["no_stated_reason"]


def test_drift_with_a_stated_reason_resolves():
    """The ruling allows motion where the model states why - a reasoned
    entry is untouched by the refusal."""
    dropped = []
    resolved = resolve_vfx(
        [_drift(rationale="a six-second locked hold that goes dead")],
        _spine(1, 2), dropped=dropped)
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"
    assert dropped == []


def test_emphasis_without_rationale_is_outside_this_refusal():
    """The ruling governs motion on static shots, not emphasis. A
    `zoom_emphasis` naming readable params resolves as before - this
    pins the boundary so the refusal cannot creep."""
    dropped = []
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "zoom_emphasis",
          "params": {"zoom_start": 1.0, "zoom_mid": 1.04,
                     "zoom_end": 1.0}}],
        _spine(1, 2), dropped=dropped)
    assert len(resolved) == 1
    assert dropped == []


def test_reasonless_drift_leaves_the_basis_honest():
    """A plan of one reasonless drift is `every_entry_dropped`, not
    `no_effects_planned` - nobody decided this piece wants stillness."""
    from library.tools.vfx_plan_basis import PlanBasis
    dropped = []
    resolved = resolve_vfx([_drift()], _spine(1, 2), dropped=dropped)
    basis = PlanBasis(proposed=1, resolved=len(resolved),
                      dropped=dropped).as_dict()
    assert basis["basis"] == "every_entry_dropped"
    assert basis["dropped"][0]["reason"] == "no_stated_reason"

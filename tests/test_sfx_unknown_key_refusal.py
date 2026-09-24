"""An SFX plan key the bridge does not read is refused, never dropped.

Measured defect: a probe's SFX entry carried `at_word`, nothing in
step 4.04 named it, and the whoosh landed 3.06 s early on the block
start - the plan's timing silently replaced by the block start.

These tests name that defect: a plan carrying `at_word` (or any other
unread key) raises `UnreadPlanKey` naming the entry, the key and the
known keys - the refusal the runner hands back to the model as retry
feedback - instead of resolving onto the block start. Legacy position
spellings the bridge still honours pass the gate.
"""
import pytest

from library.steps.step_4_04_plan_sfx.post_bridge import (
    SFX_ENTRY_KEYS,
    UnplayableSfxPlan,
    resolve_sfx,
)
from library.tools.plan_keys import UnreadPlanKey, refuse_unknown_keys


def test_at_word_refuses_loudly():
    plan = [{
        "sfx_id": "whoosh_impact",
        "spine_block_position": 3,
        "volume_db": -10.0,
        "rationale": "marks the cut",
        "at_word": "watch",
    }]
    with pytest.raises(UnreadPlanKey) as excinfo:
        resolve_sfx(plan, {}, [], {}, {}, 30.0, {}, {}, catalog=[])
    message = str(excinfo.value)
    assert "at_word" in message
    assert "sfx_creative" in message
    for key in ("sfx_id", "spine_block_position", "volume_db"):
        assert key in message


def test_legacy_position_spellings_pass_the_gate():
    plan = [{
        "sfx_id": "no-such-sound",
        "target_block_position": 2,
        "timeline_start": 1.5,
        "volume_db": -10.0,
        "rationale": "marks the cut",
    }]
    # Past the key gate: it fails later, on the unplayable id, which is
    # the step's existing refusal for that defect - not on the key.
    with pytest.raises(UnplayableSfxPlan):
        resolve_sfx(plan, {"structure": []}, [], {}, {}, 30.0, {},
                      {}, catalog=[])


def test_helper_names_first_unknown_key_and_known_keys():
    with pytest.raises(UnreadPlanKey) as excinfo:
        refuse_unknown_keys(
            [{"sfx_id": "x", "mystery": 1, "other": 2}],
            SFX_ENTRY_KEYS, step="plan_sfx", plan="sfx_creative")
    assert "'mystery'" in str(excinfo.value)
    assert "volume_db" in str(excinfo.value)


def test_helper_skips_non_dict_entries():
    # Non-dicts are each bridge's own rejection, not the key gate's.
    refuse_unknown_keys(["not-a-dict", None, {"sfx_id": "x"}],
                        SFX_ENTRY_KEYS, step="plan_sfx", plan="sfx_creative")

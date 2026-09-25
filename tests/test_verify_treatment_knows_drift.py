"""Finding 5: 4.03's mandatory verify_treatment gate cannot verify anything 4.03 plans.

On the scout's B1 run (FR3.3) every plan_vfx answer - even an empty plan -
failed with "declared gating skill(s) verify_treatment never ran", and the
skill itself only knows tv_power_head/tv_power_tail, so a planned
slow_zoom_in fails it with "UnknownTreatment: treatment 'slow_zoom_in'".

A gate that fails correct output is no coverage (AGENTS.md 10.4). The gate
must verify what 4.03 actually plans (drift via verify_drift, switch
animation via verify_treatment) and scope itself to the treatments it
knows: an empty plan, or one naming only effects with no deterministic
verifier (stabilize, speed_ramp, screen_shake), needs no receipt.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SOURCE_RES = (1080, 1920)
CLIP_DUR = 600


def _manifest():
    import json
    with open(REPO / "library" / "steps" / "step_4_03_plan_vfx"
              / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


def test_the_gate_verifies_the_drift_403_plans(tmp_path):
    """The B1 failure: slow_zoom_in raised UnknownTreatment in the skill."""
    from library.skills.verify_treatment.skill import run
    from library.tools import pipeline_skills

    record = run({"zoom_start": 1.0, "zoom_end": 1.1,
                  "source_in_frame": 0, "source_out_frame": 599},
                 "slow_zoom_in", CLIP_DUR,
                 str(tmp_path), "step_4_03_plan_vfx",
                 played_frames=300, source_res=list(SOURCE_RES))
    assert record["passed"] is True
    assert record["verdict"]["treatment"] == "drift"
    receipts = pipeline_skills.read_receipts(str(tmp_path),
                                             "step_4_03_plan_vfx")
    assert "verify_treatment" in receipts


def test_an_empty_plan_needs_no_receipt(tmp_path):
    """Even an empty plan failed the gate on B1."""
    from library.tools import pipeline_skills

    manifest = _manifest()
    assert "verify_treatment" in pipeline_skills.gating_skills(
        manifest, "step_4_03_plan_vfx")
    # No receipt on disk, empty answer: must not raise.
    receipts = pipeline_skills.assert_gating_skills_ran(
        "step_4_03_plan_vfx", manifest, str(tmp_path),
        llm_output={"vfx_creative": []})
    assert receipts == {}


def test_a_plan_with_nothing_verifiable_needs_no_receipt(tmp_path):
    """stabilize/speed/screen_shake have no plan-time verifier; the gate
    reports them openly rather than failing the run."""
    from library.tools import pipeline_skills

    manifest = _manifest()
    receipts = pipeline_skills.assert_gating_skills_ran(
        "step_4_03_plan_vfx", manifest, str(tmp_path),
        llm_output={"vfx_creative": [
            {"target_block_position": 1, "effect_type": "stabilize",
             "params": {}, "rationale": "handheld"},
            {"target_block_position": 2, "effect_type": "speed_ramp",
             "params": {"segments": [{"percent": 50.0}]},
             "rationale": "ramp in"},
        ]})
    assert receipts == {}


def test_a_planned_switch_animation_still_needs_its_receipt(tmp_path):
    """Scoping the gate must not unscope it: a tv_power plan with no
    receipt still fails."""
    import pytest

    from library.tools import pipeline_skills

    manifest = _manifest()
    with pytest.raises(pipeline_skills.GatingSkillSkipped):
        pipeline_skills.assert_gating_skills_ran(
            "step_4_03_plan_vfx", manifest, str(tmp_path),
            llm_output={"vfx_creative": [
                {"target_block_position": 1,
                 "effect_type": "tv_power_head",
                 "params": {}, "rationale": "open"},
            ]})


def test_a_truly_unknown_key_is_still_refused():
    """An unchecked treatment must not read as a checked one: the
    withdrawn `slow_zoom` alias (no direction) still raises."""
    import pytest

    from library.skills.verify_treatment.skill import run
    from library.tools.treatment_verify import UnknownTreatment

    with pytest.raises(UnknownTreatment):
        run({}, "slow_zoom", 600, "/nonexistent", "plan_vfx",
            source_res=list(SOURCE_RES))

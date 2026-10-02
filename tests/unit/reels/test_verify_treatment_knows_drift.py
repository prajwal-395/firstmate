"""4.03's mandatory verify_treatment gate verifies what 4.03 plans (drift,
switch animation) and needs no receipt for a plan with nothing it can
verify. A gate that fails correct output is no coverage (AGENTS.md 10.4).
Finding 5 (scout run B1): docs/evidence/treatment_verify.md.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
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


def test_a_plan_with_nothing_verifiable_needs_no_receipt(tmp_path):
    """Even an empty plan failed the gate on B1; stabilize/speed/
    screen_shake have no plan-time verifier, so the gate reports them
    openly rather than failing the run."""
    from library.tools import pipeline_skills

    manifest = _manifest()
    assert "verify_treatment" in pipeline_skills.gating_skills(
        manifest, "step_4_03_plan_vfx")
    for plan in ([], [
            {"target_block_position": 1, "effect_type": "stabilize",
             "params": {}, "rationale": "handheld"},
            {"target_block_position": 2, "effect_type": "speed_ramp",
             "params": {"segments": [{"percent": 50.0}]},
             "rationale": "ramp in"}]):
        # No receipt on disk: must not raise.
        assert pipeline_skills.assert_gating_skills_ran(
            "step_4_03_plan_vfx", manifest, str(tmp_path),
            llm_output={"vfx_creative": plan}) == {}


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

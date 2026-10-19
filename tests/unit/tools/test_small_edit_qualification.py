from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (Path(__file__).resolve().parents[2] / "live" /
               "small_edit_qualification.py")
SPEC = importlib.util.spec_from_file_location("small_edit_qualification",
                                             MODULE_PATH)
assert SPEC and SPEC.loader
qualification = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qualification
SPEC.loader.exec_module(qualification)


def test_tier_b_scenario_manifest_covers_the_plan():
    ids = {case.id for case in qualification.SCENARIOS}
    assert ids == {
        "add_video", "add_logo", "move_logo", "resize_logo",
        "tight_caption", "full_frame_caption", "replace_overlay", "trim",
        "move_clip", "transform", "rebuild_after_edit", "mutation_failure",
        "verification_failure", "promotion_conflict",
    }


def test_scenario_scores_only_first_attempt_clean_verification():
    case = qualification.SCENARIOS[0]
    result = qualification.run_scenario(case, lambda: {
        "receipt": {"status": "committed"}, "collateral_changes": [],
        "checks": {"identity": True}, "attempts": 1,
    })
    assert result["status"] == "pass"

    retried = qualification.run_scenario(case, lambda: {
        "receipt": {"status": "committed"}, "collateral_changes": [],
        "checks": {"identity": True}, "attempts": 2,
    })
    assert retried["status"] == "fail"


def test_logo_proof_requires_all_conditions_and_under_thirty_seconds():
    checks = dict.fromkeys({
        "independent_item", "last_five_seconds", "width_180px",
        "inside_safe_zone", "no_other_changes", "survives_rebuild",
    }, True)
    assert qualification.proof_milestone_passes({
        "checks": checks, "elapsed_seconds": 29.9,
    })
    checks["no_other_changes"] = False
    assert not qualification.proof_milestone_passes({
        "checks": checks, "elapsed_seconds": 20,
    })


def test_live_adapters_share_one_failure_tolerant_borrowed_batch():
    script = qualification._live_batch_script()
    assert "'add_video':" in script
    assert "'trim':" in script
    assert "'move_clip':" in script
    assert "'add_logo':" in script
    assert "if receipt[\"exit_code\"]:" in script
    assert "cleaned = cleanup(expected_items)" in script
    compile(script, "<qualification-batch>", "exec")
    compile(qualification._select_timeline_script() + script,
            "<qualification-recorded-batch>", "exec")

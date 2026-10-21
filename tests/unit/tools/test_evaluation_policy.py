import pytest

from library.tools import evidence_contract as evidence
from library.tools.evaluation_policy import (
    EvaluationPolicy,
    EvaluationPolicyError,
    PolicyRule,
    evaluate,
    load_policy,
)


def _measurement(value=0.7):
    return evidence.new_measurement(
        asset_id="asset-a", clock_id="source-a", start_tick=0, end_tick=1000,
        measurement_type="image.sharpness.laplacian_variance", value=value,
        method="picture-quality-deterministic_v1@180px", provenance_id="prov-a",
        sample_resolution=[320, 180], coverage=evidence.CoverageKind.COMPLETE,
        coverage_samples=1, coverage_sample_rate_hz=1,
        references=({"kind": "stream", "stream_id": "stream-a"},))


def _policy():
    return EvaluationPolicy(
        id="broll-policy", version=2, project_id="project-a",
        requirements=(PolicyRule(
            id="sharpness-min", kind="requirement",
            measurement_type="image.sharpness.laplacian_variance",
            operator="gte", expected=0.5, origin="project",
            origin_ref="project.yaml:creative_preferences"),),
        preferences=(PolicyRule(
            id="sharpness-ideal", kind="preference",
            measurement_type="image.sharpness.laplacian_variance",
            operator="gte", expected=1.0, origin="creator",
            origin_ref="brief.md#picture-style"),))


def test_policy_requires_authored_threshold_and_records_rule_and_measurement():
    policy = _policy()
    result = evaluate(
        policy, asset_id="asset-a", clock_id="source-a", project_id="project-a",
        start_tick=0, end_tick=1000, measurements={"measurement-1": _measurement()},
        provenance_id="evaluation-run")

    assert policy.to_dict()["format"] == "evaluation_policy/1"
    assert policy.ref == "broll-policy@v2"
    assert [record.value for record in result] == [True, False]
    assert [record.policy_id for record in result] == [policy.ref, policy.ref]
    assert [record.basis_measurement_ids for record in result] == [
        ("measurement-1",), ("measurement-1",)]
    assert [record.basis_rule_ids for record in result] == [
        ("sharpness-min",), ("sharpness-ideal",)]


def test_missing_policy_and_measurements_refuse_without_house_thresholds():
    with pytest.raises(EvaluationPolicyError, match="no project policy"):
        evaluate(None, asset_id="asset-a", clock_id="source-a",
                 project_id="project-a", start_tick=0, end_tick=1000,
                 measurements={}, provenance_id="run")
    with pytest.raises(EvaluationPolicyError, match="requires measured evidence"):
        evaluate(_policy(), asset_id="asset-a", clock_id="source-a",
                 project_id="project-a", start_tick=0, end_tick=1000,
                 measurements={}, provenance_id="run")


def test_policy_cannot_be_reused_across_project_without_explicit_scope():
    with pytest.raises(EvaluationPolicyError, match="scoped to"):
        evaluate(_policy(), asset_id="asset-a", clock_id="source-a",
                 project_id="project-b", start_tick=0, end_tick=1000,
                 measurements={"measurement-1": _measurement()},
                 provenance_id="run")


def test_project_model_reader_requires_exact_policy_version():
    policy = _policy()
    model = {"id": "project-a", "evaluation_policies": [policy.to_dict()]}
    assert load_policy(model, policy.ref) == policy
    with pytest.raises(EvaluationPolicyError, match="missing or ambiguous"):
        load_policy(model, "broll-policy")


def test_shake_detection_does_not_authorize_stabilization():
    from library.tools.effect_authorization import gate_effect_authorizations
    from library.tools.vfx_detection import detect_stabilization_needs

    index = {"clip_id": "shaky", "camera_motion_decomposition": {
        "sample_rate_hz": 5,
        "values": [{"translation_x": 0.1, "translation_y": 0.05,
                    "residual": 0.2} for _ in range(20)],
    }}
    detected = detect_stabilization_needs({"temporal_event_indices": [index]})
    assert detected[0]["detection_type"] == "stabilization_needed"
    # A detected candidate may inform a planner, but a stabilization effect
    # still has to pass the independent explicit-request authorization gate.
    with pytest.raises(ValueError, match="Stabilization authorization refused"):
        gate_effect_authorizations(
            {"vfx": [{"effect_type": "stabilize", "clip_id": "shaky"}]}, [])

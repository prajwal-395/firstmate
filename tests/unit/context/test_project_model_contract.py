import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest

from library.tools.project_model import validate_project_model
from library.tools.project_model_contract import (
    LEGACY_CERTAINTY,
    Certainty,
    ProcessingStatus,
    QueryCoverage,
    QueryStatus,
    TimeMap,
    compose_time_maps,
    query_status,
    validate_typed_confidence,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "project_model_e2e" / "expected-project-model.json"


def _model():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_legacy_evidence_labels_adapt_without_turning_evaluation_into_certainty():
    assert LEGACY_CERTAINTY["measurement"] is Certainty.MEASURED
    assert LEGACY_CERTAINTY["inference"] is Certainty.INFERRED
    assert LEGACY_CERTAINTY["creative_judgment"] is Certainty.INTERPRETED
    assert LEGACY_CERTAINTY["evaluation"] is None


def test_composable_time_maps_keep_fractional_ticks_and_uncertainty():
    first = TimeMap(
        "pts", "presentation", 0, 100, 0, 200, Fraction(2), uncertainty_ticks=1
    )
    second = TimeMap(
        "presentation", "timeline", 0, 200, 10, 410, Fraction(2), uncertainty_ticks=3
    )
    combined = compose_time_maps(first, second)
    assert combined.slope == Fraction(4)
    assert combined.uncertainty_ticks == 5
    assert combined.map_tick(7) == 38
    assert combined.inverse().map_tick(38) == 7


def test_composition_keeps_fractional_intermediate_ticks_exact():
    first = TimeMap("pts", "presentation", 0, 10, 0, 15, Fraction(3, 2))
    second = TimeMap("presentation", "timeline", 0, 15, 20, 30, Fraction(2, 3))
    combined = compose_time_maps(first, second)
    assert combined.slope == 1
    assert combined.map_tick(3) == 23
    with pytest.raises(ValueError, match="non-invertible"):
        TimeMap("a", "b", 0, 2, 0, 2, Fraction(1), invertible=False).inverse()


def test_time_map_refuses_a_gap_in_the_transform_chain():
    first = TimeMap("a", "b", 0, 10, 0, 10, Fraction(1))
    second = TimeMap("b", "c", 20, 30, 0, 10, Fraction(1))
    with pytest.raises(ValueError, match="continuous shared range"):
        compose_time_maps(first, second)


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        ([], QueryStatus.NOT_YET_ANALYSED),
        (
            [QueryCoverage(ProcessingStatus.ANALYZED, True, True, ("obs-1",))],
            QueryStatus.PRESENT,
        ),
        (
            [QueryCoverage(ProcessingStatus.ANALYZED, True, True)],
            QueryStatus.CONFIDENTLY_ABSENT,
        ),
        (
            [QueryCoverage(ProcessingStatus.ANALYZED, False, True)],
            QueryStatus.INCONCLUSIVE,
        ),
        (
            [QueryCoverage(ProcessingStatus.UNAVAILABLE, False, False)],
            QueryStatus.NOT_YET_ANALYSED,
        ),
    ],
)
def test_query_states_require_explicit_coverage(coverage, expected):
    assert query_status(coverage) is expected


def test_typed_confidence_preserves_score_kind_and_requires_calibration():
    validate_typed_confidence(0.92, "cosine", "face-matcher-v2")
    with pytest.raises(ValueError, match="calibration version"):
        validate_typed_confidence(0.92, "calibrated_probability", "detector-v1")
    with pytest.raises(ValueError, match="recognized score kind"):
        validate_typed_confidence(0.92, "probability", "detector-v1")


def test_old_contract_less_model_still_validates():
    """Acceptance: a project-model file written before the FI-03R
    contract existed loads byte-identical and validates - every new
    FI-03R field is additive, so absence is never a defect."""
    model = _model()
    assert "events" not in model and "signals" not in model
    validate_project_model(model)


def test_project_model_keeps_measurements_typed_and_round_trippable():
    """Objective evidence needs its own units/method and certainty; an
    interpretation cannot be smuggled into the measurement slot."""
    from library.tools import evidence_contract

    model = _model()
    observation = model["observations"][0]
    measurement = evidence_contract.new_measurement(
        asset_id=observation["asset_id"], clock_id=observation["interval"]["clock_id"],
        start_tick=observation["interval"]["start"],
        end_tick=observation["interval"]["end"],
        measurement_type="image.sharpness.laplacian_variance", value=142.6,
        method="laplacian-variance-180px-v1", provenance_id=observation["provenance_id"],
        sample_resolution=[320, 180],
    )
    observation["evidence"] = evidence_contract.to_rust_evidence(measurement)
    observation["certainty"] = "measured"
    valid = json.loads(json.dumps(model))
    validate_project_model(valid)
    assert valid["observations"][0]["evidence"] == observation["evidence"]

    observation["certainty"] = "interpreted"
    with pytest.raises(ValueError, match="observation certainty conflicts"):
        validate_project_model(model)
    observation["certainty"] = "measured"
    observation["evidence"]["unit"] = ""
    with pytest.raises(ValueError, match="non-empty unit and method"):
        validate_project_model(model)
    observation["evidence"]["unit"] = "variance"
    observation["evidence"]["method"] = ""
    with pytest.raises(ValueError, match="non-empty unit and method"):
        validate_project_model(model)


def test_versioned_evaluation_policy_is_project_scoped_and_origin_stamped():
    from library.tools.evaluation_policy import EvaluationPolicy, PolicyRule

    model = _model()
    policy = EvaluationPolicy(
        id="broll", version=1, project_id=model["id"],
        requirements=(PolicyRule(
            id="sharpness", kind="requirement",
            measurement_type="image.sharpness.laplacian_variance",
            operator="gte", expected=0.5, origin="project",
            origin_ref="project.yaml:creative_preferences"),))
    model["evaluation_policies"] = [policy.to_dict()]
    validate_project_model(model)
    model["evaluation_policies"][0]["requirements"][0]["origin_ref"] = ""
    with pytest.raises(ValueError, match="invalid or duplicate requirement rule"):
        validate_project_model(model)


def test_verified_claims_require_supporting_evidence():
    """Acceptance: an unverified candidate must never decode as a
    fact - verified events, relations and identity links without
    cited evidence are refused, while candidates and retractions pass."""
    model = _model()
    clock = model["clocks"][0]["id"]
    model["events"] = [{
        "id": "event-1", "interval": {"clock_id": clock, "start": 1, "end": 10},
        "kind": "picking_up", "participant_ids": [model["entities"][0]["id"]],
        "status": "verified", "certainty": "inferred", "evidence_ids": [],
        "confidence": None,
    }]
    with pytest.raises(ValueError, match="verified events require evidence"):
        validate_project_model(model)
    model["events"][0]["evidence_ids"] = [model["observations"][0]["id"]]
    model["events"][0]["status"] = "candidate"
    model["events"][0]["evidence_ids"] = []
    validate_project_model(model)

    model["relations"] = [{
        "id": "relation-1", "subject_id": model["entities"][0]["id"],
        "predicate": "holds", "object_id": model["entities"][0]["id"],
        "interval": {"clock_id": clock, "start": 0, "end": 10},
        "confidence": None, "provenance_id": "prov-synthetic",
        "evidence_ids": [], "status": "verified", "certainty": "inferred",
    }]
    with pytest.raises(ValueError, match="verified relations require evidence"):
        validate_project_model(model)

    link = copy.deepcopy(model["identity_links"][0])
    link["id"] = "link-ambiguous"
    link["status"] = "candidate"
    link["verification_status"] = "candidate"
    link["supporting_observation_ids"] = []
    link["contradicting_observation_ids"] = [model["observations"][0]["id"]]
    model["identity_links"].append(link)
    model["relations"][0]["status"] = "candidate"
    validate_project_model(model)
    link["verification_status"] = "retracted"
    validate_project_model(model)
    link["verification_status"] = "verified"
    link["observation_id"] = ""
    with pytest.raises(ValueError, match="supporting observations"):
        validate_project_model(model)


def test_multires_events_keep_hierarchy_and_independent_boundary_quality():
    model = _model()
    clock = model["clocks"][0]["id"]
    evidence = model["observations"][0]["id"]
    model["events"] = [
        {"id": "scene", "interval": {"clock_id": clock, "start": 0, "end": 100},
         "kind": "scene", "participant_ids": [], "status": "verified",
         "certainty": "measured", "evidence_ids": [evidence], "confidence": None,
         "start_boundary": {"status": "exact", "uncertainty_ticks": 0},
         "end_boundary": {"status": "estimated", "uncertainty_ticks": 12}},
        {"id": "action", "interval": {"clock_id": clock, "start": 20, "end": 40},
         "kind": "action", "parent_event_ids": ["scene"], "participant_ids": [],
         "status": "candidate", "certainty": "inferred", "evidence_ids": [],
         "confidence": None,
         "start_boundary": {"status": "open", "uncertainty_ticks": None}},
    ]
    validate_project_model(model)
    model["events"][0]["start_boundary"]["uncertainty_ticks"] = 1
    with pytest.raises(ValueError, match="exact event boundaries"):
        validate_project_model(model)
    model["events"][0]["start_boundary"]["uncertainty_ticks"] = 0
    model["events"][0]["parent_event_ids"] = ["action"]
    with pytest.raises(ValueError, match="contain child"):
        validate_project_model(model)
    model["events"][0].pop("parent_event_ids")
    model["events"][1]["interval"] = {"clock_id": clock, "start": 90, "end": 110}
    with pytest.raises(ValueError, match="contain child"):
        validate_project_model(model)
    model["events"][1]["interval"] = {"clock_id": clock, "start": 0, "end": 100}
    model["events"][0]["parent_event_ids"] = ["action"]
    with pytest.raises(ValueError, match="acyclic"):
        validate_project_model(model)


def test_temporal_retrieval_returns_exact_overlapping_hierarchy_and_evidence():
    from library.tools.project_model_temporal import retrieve_events

    model = _model()
    clock = model["clocks"][0]["id"]
    evidence_id = model["observations"][0]["id"]
    model["events"] = [
        {"id": "scene", "interval": {"clock_id": clock, "start": 0, "end": 1000},
         "kind": "scene", "parent_event_ids": [], "participant_ids": [],
         "status": "candidate", "certainty": "measured", "evidence_ids": [evidence_id],
         "confidence": None},
        {"id": "action", "interval": {"clock_id": clock, "start": 125, "end": 275},
         "kind": "action", "parent_event_ids": ["scene"], "participant_ids": [],
         "status": "candidate", "certainty": "inferred", "evidence_ids": [evidence_id],
         "confidence": None,
         "start_boundary": {"status": "estimated", "uncertainty_ticks": 20},
         "end_boundary": {"status": "open", "uncertainty_ticks": None}},
        {"id": "speech", "interval": {"clock_id": clock, "start": 200, "end": 350},
         "kind": "speech", "parent_event_ids": ["scene"], "participant_ids": [],
         "status": "candidate", "certainty": "measured", "evidence_ids": [evidence_id],
         "confidence": None},
    ]
    rows = retrieve_events(model, {"clock_id": clock, "start": 240, "end": 260})
    assert {row["event"]["id"] for row in rows} == {"scene", "action", "speech"}
    action = next(row for row in rows if row["event"]["id"] == "action")
    assert [event["id"] for event in action["lineage"]] == ["scene", "action"]
    assert action["event"]["interval"] == {
        "clock_id": clock, "start": 125, "end": 275}
    assert action["event"]["start_boundary"]["uncertainty_ticks"] == 20
    assert action["source_evidence"][0]["id"] == evidence_id


def test_evidence_backed_edits_require_resolvable_observation_links():
    model = _model()
    operation = {
        "id": "edit:grounded", "operation": "edit_graph.aroll_assignment",
        "target_ids": ["aroll:1"], "before": None, "after": {},
        "intent_id": None, "evidence_ids": [], "author": "test",
        "query_result_id": "query:1", "evidence_revision": "revision:1",
        "creative_rationale": "choose a clear angle",
    }
    model["edits"].append(operation)
    with pytest.raises(ValueError, match="needs evidence_ids"):
        validate_project_model(model)
    operation["evidence_ids"] = [model["observations"][0]["id"]]
    validate_project_model(model)
    operation["evidence_ids"] = ["missing-observation"]
    with pytest.raises(ValueError, match="unknown observation"):
        validate_project_model(model)


def test_query_outcomes_refuse_silent_absence():
    """Acceptance: confidently_absent needs fresh complete versioned
    coverage, and not_yet_analysed names its missing lanes - an empty
    index, a stale run or a partial lane can never read as absent."""
    model = _model()
    model["query_results"] = [{
        "status": "confidently_absent", "candidate_evidence_ids": [],
        "required_missing_lanes": [], "completeness_basis": [],
    }]
    with pytest.raises(ValueError, match="completeness basis"):
        validate_project_model(model)
    model["query_results"][0]["completeness_basis"] = [{
        "lane": "motion", "coverage": "sampled", "freshness": "fresh",
        "scope": {"interval": None, "coordinate_space_id": None,
                  "target_entity_id": None},
        "producer_version": "motion-v1", "level": "l1",
    }]
    with pytest.raises(ValueError, match="fresh complete versioned"):
        validate_project_model(model)
    model["query_results"][0]["completeness_basis"][0]["coverage"] = "complete"
    validate_project_model(model)
    model["query_results"] = [{
        "status": "not_yet_analysed", "candidate_evidence_ids": [],
        "required_missing_lanes": [], "completeness_basis": [],
    }]
    with pytest.raises(ValueError, match="missing lanes"):
        validate_project_model(model)


def test_stale_and_failed_lanes_never_read_as_absence():
    stale = QueryCoverage(ProcessingStatus.ANALYZED, True, False, ())
    assert query_status([stale]) is QueryStatus.NOT_YET_ANALYSED
    failed = QueryCoverage(ProcessingStatus.FAILED, False, False, ())
    assert query_status([failed]) is QueryStatus.NOT_YET_ANALYSED
    partial = QueryCoverage(ProcessingStatus.INCONCLUSIVE, False, True, ())
    assert query_status([partial]) is QueryStatus.INCONCLUSIVE


def test_track_discontinuity_and_unknown_spaces_are_refused():
    """Acceptance (tracks row): samples must be strictly ordered so a
    gap is an explicit discontinuity, and every sample interval, space
    and evidence link must resolve."""
    model = _model()
    clock = model["clocks"][0]["id"]
    obs = model["observations"][0]["id"]
    model["coordinate_spaces"] = [{
        "id": "space-pixels", "kind": "pixel", "units": "px",
        "origin": "top_left", "axes": "x_right_y_down", "calibration": None,
    }]
    model["space_transforms"] = [{
        "id": "pixel-to-canvas", "from_space": "space-pixels",
        "to_space": "space-pixels", "matrix": [1.0] * 9,
        "uncertainty": 0.0, "provenance_id": "prov-synthetic",
    }]
    model["tracks"] = [{
        "id": "track-1", "entity_id": model["entities"][0]["id"],
        "clock_id": clock, "provenance_id": "prov-synthetic",
        "kind": "object", "continuity": "discontinuous", "coverage": "analyzed",
        "samples": [
            {"tick": 0, "geometry": {"x": 0.1}, "confidence": 0.9,
             "coordinate_space": "normalized_frame",
             "interval": {"clock_id": clock, "start": 0, "end": 1},
             "evidence_ids": [obs], "coordinate_space_id": "space-pixels",
             "space_transform_id": "pixel-to-canvas"},
            {"tick": 0, "geometry": {"x": 0.2}, "confidence": 0.9,
             "coordinate_space": "normalized_frame"},
        ],
    }]
    with pytest.raises(ValueError, match="strictly ordered"):
        validate_project_model(model)
    model["tracks"][0]["samples"][1]["tick"] = 2
    validate_project_model(model)
    model["tracks"][0]["samples"][1]["space_transform_id"] = "no-such-transform"
    with pytest.raises(ValueError, match="unknown space transform"):
        validate_project_model(model)
    del model["tracks"][0]["samples"][1]["space_transform_id"]
    model["tracks"][0]["samples"][1]["coordinate_space_id"] = "no-such-space"
    with pytest.raises(ValueError, match="unknown coordinate space"):
        validate_project_model(model)
    del model["tracks"][0]["samples"][1]["coordinate_space_id"]
    model["space_transforms"][0]["matrix"] = [1.0, float("nan")] + [0.0] * 7
    with pytest.raises(ValueError, match="finite 3x3 or 4x4"):
        validate_project_model(model)


def test_signals_clocks_and_interpretation_bounds():
    """Acceptance: a signal names its sample clock, an unavailable
    signal carries no samples, and an interpretation keeps its
    uncertainty inside 0..1 with interpreted certainty."""
    model = _model()
    clock = model["clocks"][0]["id"]
    model["signals"] = [{
        "id": "signal-1", "asset_id": model["assets"][0]["id"],
        "interval": {"clock_id": clock, "start": 0, "end": 24},
        "measurement_type": "audio.level", "unit": "dBFS",
        "samples": [-12.0], "sample_clock_id": clock,
        "provenance_id": "prov-synthetic", "processing_status": "unavailable",
    }]
    with pytest.raises(ValueError, match="cannot carry samples"):
        validate_project_model(model)
    model["signals"][0]["processing_status"] = "analyzed"
    model["signals"][0]["sample_clock_id"] = "no-such-clock"
    with pytest.raises(ValueError, match="unknown clock"):
        validate_project_model(model)
    model["signals"][0]["sample_clock_id"] = clock
    model["signals"][0].update({
        "source_signal_id": "signal-source",
        "samples_ref": "audio/derived/dialogue.wav",
        "estimate_status": "estimated",
    })
    model["signals"].append({
        "id": "signal-source", "asset_id": model["assets"][0]["id"],
        "interval": {"clock_id": clock, "start": 0, "end": 24},
        "measurement_type": "audio.source", "unit": "samples",
        "samples": [], "sample_clock_id": clock,
        "provenance_id": "prov-synthetic", "processing_status": "analyzed",
    })
    validate_project_model(model)
    model["signals"][1]["source_signal_id"] = "signal-1"
    with pytest.raises(ValueError, match="must be acyclic"):
        validate_project_model(model)
    del model["signals"][1]["source_signal_id"]
    model["signals"][0]["source_signal_id"] = "missing-source"
    with pytest.raises(ValueError, match="unknown source_signal_id"):
        validate_project_model(model)
    model["signals"][0]["source_signal_id"] = "signal-source"
    model["signals"][0]["estimate_status"] = "measured"
    with pytest.raises(ValueError, match="estimate_status"):
        validate_project_model(model)
    model["signals"][0]["estimate_status"] = "estimated"
    model["signals"][0]["samples_ref"] = None
    with pytest.raises(ValueError, match="require source signal provenance"):
        validate_project_model(model)
    model["signals"][0]["samples_ref"] = "audio/derived/dialogue.wav"
    model["interpretations"] = [{
        "id": "interp-1", "kind": "demonstration", "author": "fixture",
        "objective": None, "policy_id": None, "evidence_ids": [],
        "scope": {}, "uncertainty": 1.5, "certainty": "interpreted",
    }]
    with pytest.raises(ValueError, match="between 0 and 1"):
        validate_project_model(model)
    model["interpretations"][0]["uncertainty"] = 0.2
    model["interpretations"][0]["certainty"] = "inferred"
    with pytest.raises(ValueError, match="must be interpreted"):
        validate_project_model(model)

"""The epistemic census, proven on fixtures.

Every test names the defect it pins: a registry that files a model's
value as a measurement, a census that cannot see the defect, a
re-classification that moves a measurement, and a calibration that
reports an error it never measured.

Hermetic: no project, no memory root, no Resolve. The model is a
fixture dict, the adjudicated set is a fixture mapping.
"""

import pytest

from library.tools import epistemic_vocabulary as census
from library.tools import evidence_contract as contract


def _contract(measurement_type: str, **overrides) -> dict:
    entry = contract.registry_entry(measurement_type)
    row = {
        "evidence_type": entry["kind"].value,
        "measurement_type": measurement_type,
        "value": {},
        "unit": entry["unit"],
        "method": "fixture:lane-v1",
        "sample_resolution": None,
        "coverage": "complete",
        "coverage_samples": 1,
        "coverage_sample_rate_hz": 0.0,
        "target_entity_id": None,
        "target_region": None,
        "uncertainty": None,
        "policy_id": None,
        "basis_measurement_ids": [],
        "basis_rule_ids": [],
        "contract_version": 1,
        "certainty": "measured",
        "processing_status": None,
        "source_reference": None,
        "spatial_frame_id": None,
        "coordinate_space_id": None,
        "references": [],
        "coverage_scope": None,
        "coverage_level": None,
        "producer_version": None,
        "freshness": "fresh",
        "source_stream_id": None,
        "sampling_method": None,
        "temporal_coverage": [],
        "coverage_status": "unknown",
        "verified_status": None,
        "producer": "fixture",
    }
    row.update(overrides)
    return row


def _model(*rows) -> dict:
    return {"observations": [
        {"id": f"obs-{index}", "payload": {"evidence_contract": row},
         "confidence": confidence}
        for index, (row, confidence) in enumerate(rows)]}


def test_the_registry_has_no_epistemic_defect():
    """The defect: a model-produced type filed as a measurement says an
    instrument read a value no instrument read. The registry has none."""
    assert census.registry_defects() == []
    rows = {row["measurement_type"]: row for row in census.registry_rows()}
    assert rows["object.detection"]["model_produced"] is True
    assert rows["object.detection"]["kind"] == "inference"
    assert rows["image.sharpness.laplacian_variance"]["model_produced"] \
        is False
    assert rows["image.sharpness.laplacian_variance"]["kind"] == "measurement"


def test_the_census_names_the_rows_filed_as_measurements():
    """The defect: a census that cannot see the defect. The census
    counts the model-produced rows filed as measurements."""
    model = _model(
        (_contract("object.detection",
                   value={"score": 0.9}), 0.9),
        (_contract("image.sharpness.laplacian_variance",
                   value=142.6, sample_resolution=[320, 180]), None))
    counted = census.census_model(model)
    assert counted["observations"] == 2
    assert counted["defects"]["model_produced_filed_as_measurement"] == 0
    assert counted["by_type"]["object.detection"]["rows"] == 1
    # A model-produced type filed as a measurement IS the defect, and
    # the census must be able to see it.
    broken = _model((_contract("object.detection",
                                evidence_type="measurement"), 0.9),)
    assert census.census_model(broken)["defects"][
        "model_produced_filed_as_measurement"] == 1


def test_the_reclassification_moves_only_the_epistemic_fields():
    """A re-classification that changes a measurement is a fidelity
    regression, not an epistemic correction."""
    model = _model((_contract("object.detection",
                               value={"score": 0.9},
                               evidence_type="measurement"), 0.9),)
    after = census.reclassify_model(model)
    report = after["report"]
    assert report["rows"] == 1
    assert report["epistemically_changed"] == 1
    assert report["fidelity_violations"] == []
    assert report["contract_refusals"] == []
    row = after["contracts"][0]
    assert row["evidence_type"] == "inference"
    assert row["certainty"] == "inferred"
    assert row["uncertainty"] == pytest.approx(0.1)
    assert row["value"] == {"score": 0.9}


def test_the_reclassified_contract_is_accepted_by_the_contract():
    """A re-classified row the contract refuses is a defect in the
    re-classification, and it is named rather than counted away."""
    model = _model((_contract("object.detection",
                               value={"score": 0.9},
                               evidence_type="measurement"), 0.9),)
    after = census.reclassify_model(model)
    assert after["report"]["contract_refusals"] == []
    broken = _model((_contract("object.detection",
                                value={"score": 0.9},
                                evidence_type="measurement",
                                unit="not-the-registry-unit"), 0.9),)
    assert census.reclassify_model(broken)["report"]["contract_refusals"]


def test_a_producer_that_said_nothing_carries_the_maximum_uncertainty():
    """The uncertainty is the producer's own score, and a producer
    that recorded none asserts nothing."""
    model = _model((_contract("person.voice_turn",
                               value={"voice_track": "voice_001"},
                               evidence_type="measurement"), None),)
    after = census.reclassify_model(model)
    assert after["contracts"][0]["uncertainty"] == (
        contract.UNCERTAINTY_UNSTATED)


def test_the_calibration_reports_an_error_per_type_with_its_row_count():
    """The calibration is per type and carries the denominator: a type
    with no adjudicated row is unknown, never a pass."""
    adjudication = {
        "labels": [
            {"frame": "f1", "class": "laptop", "box": [0.0, 0.0, 0.5, 0.5],
             "type": "object.detection"},
            {"frame": "f2", "class": "laptop", "box": [0.5, 0.5, 1.0, 1.0],
             "type": "object.detection"},
        ],
        "predictions": [
            {"frame": "f1", "class": "laptop", "box": [0.0, 0.0, 0.5, 0.5],
             "score": 0.9, "type": "object.detection"},
            {"frame": "f3", "class": "laptop", "box": [0.1, 0.1, 0.4, 0.4],
             "score": 0.2, "type": "object.detection"},
        ],
    }
    rows = census.adjudicated_errors(adjudication)
    assert len(rows) == 1
    row = rows[0]
    assert row["measurement_type"] == "object.detection"
    assert row["labels"] == 2
    assert row["matched"] == 1
    assert row["missed"] == 1
    assert row["false_positives"] == 1
    assert row["error_rate"] == 1.0
    assert row["mean_score_matched"] == 0.9
    assert row["mean_score_missed"] == 0.2


def test_a_calibration_with_no_adjudicated_set_is_unknown():
    """An unmeasured error is never a pass: the report says unknown."""
    report = census.render_report(
        census.census_contracts([_contract("object.detection")]), [])
    assert "unknown" in report
    assert "never a pass" in report

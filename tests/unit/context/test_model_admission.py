"""The admission gate: what an optional model must record before it is used."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from library.tools import model_admission, model_registry, model_watch


def _registry() -> dict:
    return model_registry.load_registry()


def _without_record(registry: dict, implementation_id: str) -> dict:
    target = next(row for row in registry["implementations"]
                  if row["id"] == implementation_id)
    target.pop("admission", None)
    return registry


def test_every_optional_model_carries_a_complete_admission_record():
    """The measurement this lane exists to move: 18 of 18, with a denominator."""
    report = model_admission.coverage(_registry())
    assert report["optional_total"] == 18
    assert report["complete"] == report["optional_total"]
    assert report["share"] == 1.0
    assert [row["implementation_id"] for row in report["rows"] if not row["complete"]] == []


def test_the_admission_record_states_a_licence_a_size_and_a_required_gain():
    for row in _registry()["implementations"]:
        record = row.get("admission")
        assert record is not None, row["id"]
        assert record["license"], row["id"]
        assert record["commercial_use"] in model_admission.COMMERCIAL_USE_VALUES
        assert record["size"]["bytes"] > 0
        assert record["size"]["parts"], row["id"]
        assert sum(part["bytes"] for part in record["size"]["parts"]) == record["size"]["bytes"]
        gain = record["required_gain"]
        assert gain["metric"] and gain["unit"] and gain["split"], row["id"]
        assert gain["basis"] and gain["fixture"] and gain["evidence"], row["id"]
        assert gain["direction"] in model_admission.DIRECTIONS
        assert gain["comparison"] in model_admission.COMPARISONS
        if gain["comparison"] == "establish_baseline":
            # No threshold is stated because nothing has been measured. An
            # unmeasured value is never recorded as a zero.
            assert gain["threshold"] is None, row["id"]
        else:
            assert isinstance(gain["threshold"], (int, float)), row["id"]
            assert gain["incumbent"], row["id"]
        if gain["comparison"] == "delta_over_incumbent":
            assert isinstance(gain["incumbent_value"], (int, float)), row["id"]
            assert gain["threshold"] > 0, row["id"]


def test_the_recorded_size_names_the_files_and_the_publisher_it_came_from():
    for row in _registry()["implementations"]:
        for part in row["admission"]["size"]["parts"]:
            assert part["source"].startswith("https://"), row["id"]
            assert part["files"], row["id"]
            assert part["bytes"] > 0, row["id"]
            assert part["method"] in {
                "hub_api_blobs", "hub_api_tree", "github_release_asset",
                "zenodo_record_files", "http_content_length"}, row["id"]


def test_loading_a_model_without_an_admission_record_is_refused():
    """The defect the gate exists for: a model with no record must not load."""
    registry = _without_record(_registry(), "panns.cnn14_decision_max")
    with pytest.raises(model_registry.ModelRegistryError,
                       match="admission record"):
        model_registry.require_implementation(
            "panns.cnn14_decision_max", action="load", purpose="permitted_research",
            registry=registry)


def test_an_incomplete_admission_record_is_refused_by_field_name():
    registry = _registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "panns.cnn14_decision_max")
    target["admission"].pop("size")
    with pytest.raises(model_registry.ModelRegistryError,
                       match="admission record is incomplete: size"):
        model_registry.validate_registry(registry)


def test_routing_refuses_a_model_without_an_admission_record():
    registry = _without_record(_registry(), "ast.audioset")
    with pytest.raises(model_registry.ModelRegistryError,
                       match="admission record is missing"):
        model_registry.route(
            "audio.classify_event", purpose="permitted_research", mode="balanced",
            registry=registry)


def test_a_record_may_not_claim_more_than_the_licence_evidence_allows():
    registry = _registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "clip.openai_vit_large_patch14")
    assert model_admission.consistency(
        target,
        approval_status=target["license_evidence"]["approval_status"],
        licence_statement=target["license_evidence"]["license"]) == []
    target["admission"]["commercial_use"] = "permitted"
    problems = model_admission.consistency(
        target,
        approval_status=target["license_evidence"]["approval_status"],
        licence_statement=target["license_evidence"]["license"])
    assert problems and "claims more than" in problems[0]


def test_a_record_may_withhold_permission_the_licence_evidence_grants():
    """Withholding is safe; only granting what the evidence lacks is not."""
    registry = copy.deepcopy(_registry())
    target = next(row for row in registry["implementations"]
                  if row["id"] == "mfa.english_us_arpa")
    target["license_evidence"]["approval_status"] = "approved"
    target["admission"]["commercial_use"] = "conditional"
    model_registry.validate_registry(registry)


def test_licence_ingestion_keeps_the_admission_record_in_step_with_the_evidence(tmp_path):
    registry = _registry()
    record = next(row for row in model_registry._snapshot_records()
                  if row["artifact_id"] == "clip")
    (tmp_path / "clip.json").write_text(json.dumps(record), encoding="utf-8")
    imported, _ = model_registry.ingest_license_records(registry, tmp_path)
    clip = next(row for row in imported["implementations"]
                if row["id"] == "clip.openai_vit_large_patch14")
    assert clip["admission"]["license"] == clip["license_evidence"]["license"]
    assert clip["admission"]["commercial_use"] == model_admission.COMMERCIAL_USE_BY_APPROVAL[
        clip["license_evidence"]["approval_status"]]
    model_registry.validate_registry(imported)


def test_the_personal_configuration_may_add_but_never_weaken_the_commercial_one():
    configurations = model_admission.configurations(_registry())
    assert set(configurations["commercial"]) <= set(configurations["personal"])
    assert configurations["commercial_baseline_intact"] is True
    # The personal configuration carries models the commercial one may not:
    # a licence that is unresolved, prohibited, or personal-only.
    assert "insightface.buffalo_l" in configurations["personal_extras"]
    assert "insightface.buffalo_l" not in configurations["commercial"]


def test_a_candidate_may_not_be_adopted_without_a_complete_admission_record(tmp_path):
    database = model_watch.load_database()
    candidate = copy.deepcopy(
        next(row for row in database["candidates"] if row["id"] == "clip"))
    candidate["decision"]["status"] = "adopted"
    candidate.pop("registry_implementation", None)
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"schema_version": 1,
                                 "maintained_at": database["maintained_at"],
                                 "description": database["description"],
                                 "candidates": [candidate]}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="complete admission record"):
        model_watch.load_database(path)


def test_a_candidate_that_is_a_registered_implementation_inherits_its_record():
    database = model_watch.load_database()
    candidate = next(row for row in database["candidates"] if row["id"] == "clip")
    assert model_watch.admission_missing(candidate) == []
    assert candidate["registry_implementation"] == "clip.openai_vit_large_patch14"


def test_the_gate_report_names_the_models_that_are_not_admitted():
    registry = _without_record(_registry(), "gemma.vision_4_12b_it_4bit")
    report = model_admission.coverage(registry)
    assert report["complete"] == 17
    assert report["optional_total"] == 18
    incomplete = [row["implementation_id"] for row in report["rows"]
                  if not row["complete"]]
    assert incomplete == ["gemma.vision_4_12b_it_4bit"]

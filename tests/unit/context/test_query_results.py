"""B-04: an answered plan has a home in the model.

The defect this names: the model's ``query_results`` collection held
no rows on any project, so a "confidently absent" answer existed only
in the console output of one command. Nothing recorded the basis it
rested on, and nothing could refuse a confident absence that came out
of partial coverage.
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from library.tools import project_model_store, query_results

SCHEMA = json.loads(
    Path("native/ren-protocol/schemas/project-model.schema.json")
    .read_text(encoding="utf-8"))


def _answer(**overrides):
    answer = {
        "status": "present",
        "answer_state": "present",
        "answer_basis": "the lane returned evidence intervals",
        "results": [{"evidence_ids": ["obs-1"]}],
        "candidate_evidence_ids": ["obs-1"],
        "completeness_basis": [{
            "lane": "m7", "coverage": "complete", "freshness": "fresh",
            "lane_state": "present", "scope": {},
            "producer_version": "1", "level": None}],
        "required_missing_lanes": [],
        "reasons": [],
    }
    answer.update(overrides)
    return answer


def _row_is_schema_valid(row: dict) -> None:
    validator = Draft202012Validator(
        {"$ref": "#/$defs/QueryResult", "$defs": SCHEMA["$defs"]})
    errors = sorted(validator.iter_errors(row), key=lambda e: list(e.path))
    assert not errors, [error.message for error in errors]


def test_a_row_carries_the_status_the_basis_and_the_missing_lanes():
    row = query_results.row_for_answer(_answer())
    assert row["status"] == "present"
    assert row["candidate_evidence_ids"] == ["obs-1"]
    assert row["required_missing_lanes"] == []
    assert row["completeness_basis"][0]["lane"] == "m7"
    _row_is_schema_valid(row)


def test_a_measured_absence_is_filed_as_a_confident_absence():
    row = query_results.row_for_answer(_answer(
        status="confidently_absent", answer_state="absent", results=[],
        candidate_evidence_ids=["obs-absence"],
        completeness_basis=[{
            "lane": "m7", "coverage": "complete", "freshness": "fresh",
            "lane_state": "covers_absent", "scope": {},
            "producer_version": "1", "level": None}]))
    assert row["status"] == "confidently_absent"
    assert row["candidate_evidence_ids"] == ["obs-absence"]
    _row_is_schema_valid(row)


def test_a_confident_absence_from_partial_coverage_is_refused():
    row = query_results.row_for_answer(_answer(
        status="confidently_absent", answer_state="absent", results=[],
        completeness_basis=[{
            "lane": "m7", "coverage": "sampled", "freshness": "fresh",
            "lane_state": "partial", "scope": {},
            "producer_version": "1", "level": None}]))
    assert row["status"] == "inconclusive"
    _row_is_schema_valid(row)


def test_a_confident_absence_from_unversioned_coverage_is_refused():
    row = query_results.row_for_answer(_answer(
        status="confidently_absent", answer_state="absent", results=[],
        completeness_basis=[{
            "lane": "m7", "coverage": "complete", "freshness": "fresh",
            "lane_state": "covers_absent", "scope": {},
            "producer_version": None, "level": None}]))
    assert row["status"] == "inconclusive"


def test_a_not_observed_answer_is_never_filed_as_a_confident_absence():
    # A complete lane that asserted nothing either way is not a
    # negative assertion; the model has no row for it and must not be
    # handed one that claims a confident absence.
    row = query_results.row_for_answer(_answer(
        status="confidently_absent", answer_state="not_observed", results=[],
        completeness_basis=[{
            "lane": "m7", "coverage": "complete", "freshness": "fresh",
            "lane_state": "covers_absent", "scope": {},
            "producer_version": "1", "level": None}]))
    assert row["status"] == "inconclusive"


def test_an_unanalysed_answer_names_the_missing_lanes():
    row = query_results.row_for_answer(_answer(
        status="not_yet_analysed", answer_state="not_analyzed", results=[],
        candidate_evidence_ids=[],
        completeness_basis=[{
            "lane": "m7", "coverage": "unavailable", "freshness": "unknown",
            "lane_state": "missing", "scope": {},
            "producer_version": None, "level": None}],
        required_missing_lanes=["m7"]))
    assert row["status"] == "not_yet_analysed"
    assert row["required_missing_lanes"] == ["m7"]
    _row_is_schema_valid(row)


def test_a_basis_row_claims_the_weakest_level_it_can():
    row = query_results.row_for_answer(_answer(
        completeness_basis=[{
            "lane": "m7", "coverage": "complete", "freshness": "fresh",
            "lane_state": "present", "scope": {}, "producer_version": "1",
            "level": None}]))
    assert row["completeness_basis"][0]["level"] == "l0"


def test_an_answered_plan_is_filed_in_the_model(tmp_path):
    fixture = json.loads(
        Path("native/ren-protocol/fixtures/project-model.json")
        .read_text(encoding="utf-8"))
    project_model_store.commit_project_model(tmp_path, fixture,
                                             expected_revision=0)
    revision, model = project_model_store.load_project_model(tmp_path)
    row = query_results.row_for_answer(_answer())
    before = list(model.get("query_results") or [])
    new_revision = project_model_store.record_query_result(
        tmp_path, model, row, expected_revision=revision)
    assert new_revision == revision + 1
    _revision, stored = project_model_store.load_project_model(tmp_path)
    assert stored["query_results"] == [*before, row]
    # Filing the same answer twice leaves one row: the rows carry no
    # stable id, so they are unioned by value.
    project_model_store.record_query_result(
        tmp_path, stored, row, expected_revision=new_revision)
    _revision, stored = project_model_store.load_project_model(tmp_path)
    assert stored["query_results"] == [*before, row]

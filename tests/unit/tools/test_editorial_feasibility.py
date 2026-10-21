"""PLAN-005, measured on the eight-brief benchmark.

The measurement the punch list names: on the eight-brief benchmark, the
plans refused or revised BEFORE staging, with no drop in the planning
number.

These tests run the measurement over the benchmark's own records, so
the numbers in the report cannot drift from the records they come from,
and they assert the COVERAGE GATE: a plan the engine refuses before
staging keeps the planning credit of the plan the run made.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import feasibility

RECORDS = Path(__file__).resolve().parents[2] / "live" / "reports" / (
    "editorial-bench-records")


def _records() -> list[dict]:
    if not RECORDS.is_dir():
        pytest.skip("the benchmark records are not in this checkout")
    return feasibility._read_records(RECORDS)


def test_the_measurement_reads_the_benchmarks_own_records():
    records = _records()

    assert len(records) == 8
    assert {str(record.get("brief_id")) for record in records} == {
        "keep-it-but-tighten-it", "logo-not-in-the-project",
        "name-bar-fights-the-captions", "no-extra-footage",
        "open-with-the-cost", "the-whole-job", "the-wrong-caption",
        "two-voices-at-once"}


def test_every_plan_that_named_an_operation_is_refused_before_staging():
    # Defect: the plans were routed and staged first, and the refusal
    # came back from the end of the write path.
    payload = feasibility.measure(_records())

    assert payload["brief_count"] == 8
    assert payload["plans_assessed"] == 5
    assert payload["plans_rejected_before_staging"] == 5
    assert payload["plans_revised_before_staging"] == 0
    assert payload["plans_that_would_reach_staging"] == 0


def test_the_refusal_names_the_operation_and_its_reason():
    rows = {row["brief_id"]: row
            for row in feasibility.measure(_records())["briefs"]}

    name_bar = rows["name-bar-fights-the-captions"]
    assert name_bar["outcome"] == "rejected_before_staging"
    assert any("overlay.position" in reason and
               "no production-enabled backend" in reason
               for reason in name_bar["reasons"])


def test_a_plan_naming_a_target_the_footage_does_not_carry_is_refused():
    rows = {row["brief_id"]: row
            for row in feasibility.measure(_records())["briefs"]}

    whole_job = rows["the-whole-job"]
    assert whole_job["outcome"] == "rejected_before_staging"
    assert "repeated_passages" in whole_job["unresolved_roles"]


def test_the_planning_number_does_not_drop():
    # The coverage gate: a feasibility refusal happens AFTER the
    # planning stage and rewrites nothing the planning stage reads, so
    # a plan the engine refuses keeps its planning credit.
    payload = feasibility.measure(_records())
    planning = payload["coverage"]["planning"]

    assert planning["passed"] == 4
    assert planning["measured"] == 8
    assert planning["value"] == 0.5
    assert payload["coverage"]["planning_before"] == planning
    assert payload["coverage"]["planning_after"] == planning


def test_the_before_and_after_are_both_reported_with_denominators():
    payload = feasibility.measure(_records())

    assert payload["before"]["plans_refused_by_the_engine_before_staging"] == 0
    assert payload["before"]["plans_that_reached_the_execution_path"] == 1
    assert payload["before"]["attempts_spent_on_those_plans"] == 14
    assert payload["after"]["plans_that_reach_the_execution_path"] == 0


def test_the_measurement_does_not_run_the_parameter_check():
    # A benchmark record names the operation, not its arguments; a check
    # the record cannot answer would refuse every plan for a field the
    # record never had.
    from library.tools import plan_feasibility

    assert (plan_feasibility.CHECK_REQUIRED_PARAMETERS
            not in feasibility.MEASURED_CHECKS)
    assert feasibility.MEASURED_CHECKS


def test_the_report_is_written_as_json():
    payload = feasibility.measure(_records())

    assert json.loads(json.dumps(payload, default=str))["schema"] == (
        feasibility.SCHEMA)

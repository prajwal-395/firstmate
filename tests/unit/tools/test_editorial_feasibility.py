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


def test_a_plan_the_engine_cannot_run_is_refused_before_staging():
    # Defect: the plans were routed and staged first, and the refusal
    # came back from the end of the write path.
    from library.tools import execution_router

    payload = feasibility.measure(_records())
    rows = {row["brief_id"]: row for row in payload["briefs"]}

    assert payload["brief_count"] == 8
    assert payload["plans_assessed"] == 5

    for brief, row in rows.items():
        if not row["planned_operations"]:
            continue
        routable = [
            op for op in row["planned_operations"]
            if execution_router.eligibility(op)["production_enabled"]]
        if routable and not row["unresolved_roles"]:
            assert row["outcome"] == "would_reach_staging", brief
        else:
            assert row["outcome"] in {"rejected_before_staging",
                                      "revised_before_staging"}, brief

    # The two plans that would reach the engine's route name the two
    # operations a live qualification made production-enabled:
    # `overlay.position` (PR 3042) and `reel.opening` (PR 3064).  Every
    # other plan names an operation the engine cannot run, or a target
    # the footage does not carry.
    reaching = [brief for brief, row in rows.items()
                if row["outcome"] == "would_reach_staging"]
    assert reaching == ["name-bar-fights-the-captions",
                        "open-with-the-cost"]
    assert rows["name-bar-fights-the-captions"]["planned_operations"] == [
        "overlay.position"]
    assert rows["open-with-the-cost"]["planned_operations"] == [
        "reel.opening"]


def test_the_refusal_names_the_operation_and_its_reason():
    rows = {row["brief_id"]: row
            for row in feasibility.measure(_records())["briefs"]}

    wrong_caption = rows["the-wrong-caption"]
    assert wrong_caption["outcome"] == "rejected_before_staging"
    assert any("caption_fix" in reason and
               "no production-enabled backend" in reason
               for reason in wrong_caption["reasons"])


def test_a_plan_naming_a_target_the_footage_does_not_carry_is_refused():
    rows = {row["brief_id"]: row
            for row in feasibility.measure(_records())["briefs"]}

    # A plan whose every operation names a target the footage does not
    # carry is refused outright.
    wrong_caption = rows["the-wrong-caption"]
    assert wrong_caption["outcome"] == "rejected_before_staging"
    assert "wrong_caption_card" in wrong_caption["unresolved_roles"]

    # The whole-job plan names five targets the footage does not carry.
    # Every operation that named one is dropped from the plan that is
    # left, and what is left names the two operations whose targets the
    # footage DOES carry - and that a live qualification made
    # production-enabled.
    whole_job = rows["the-whole-job"]
    assert whole_job["outcome"] == "revised_before_staging"
    assert "repeated_passages" in whole_job["unresolved_roles"]
    for role, op in (("repeated_passages", "clip.trim"),
                     ("weak_angle_turn", "multicam.select_angle"),
                     ("broll_cover_range", "broll.insert"),
                     ("wrong_caption_card", "caption_fix"),
                     ("music_bed", "audio.gain")):
        assert op in whole_job["revision"]["dropped"], role
    assert whole_job["revision"]["operations"] == ["reel.opening",
                                                   "overlay.position"]


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
    rows = {row["brief_id"]: row for row in payload["briefs"]}

    assert payload["before"]["plans_refused_by_the_engine_before_staging"] == 0
    assert payload["before"]["plans_that_reached_the_execution_path"] == 1
    assert payload["before"]["attempts_spent_on_those_plans"] == 14

    # Before the check, ONE of the five plans reached the engine's
    # route and execution path: the name-bar brief, on the
    # `reel_touchup` backend, after three attempts.  TWO would reach it
    # now, and both name an operation a live qualification made
    # production-enabled - `overlay.position` (PR 3042) and
    # `reel.opening` (PR 3064).  The three the check still refuses are
    # the ones whose operation has no route, or whose target the
    # footage does not carry.
    assert payload["after"]["plans_that_reach_the_execution_path"] == 2
    assert payload["after"]["plans_refused_by_the_engine_before_staging"] == 2
    assert payload["after"]["plans_revised_by_the_engine_before_staging"] == 1
    assert rows["name-bar-fights-the-captions"]["reached_the_execution_path"]
    assert rows["name-bar-fights-the-captions"]["outcome"] == (
        "would_reach_staging")
    assert rows["open-with-the-cost"]["outcome"] == "would_reach_staging"


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

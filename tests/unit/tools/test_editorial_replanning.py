"""PLAN-007, measured on the eight-brief benchmark.

The measurement the punch list names: on the eight-brief benchmark, the
plans revised into feasible ones, the model calls saved by not
repeating an unchanged context, and the planning number not below 4 of
8.

These tests run the measurement over the benchmark's own records, so
the numbers in the report cannot drift from the records they come from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import plan_revision
from library.tools.editorial_acceptance import replanning

RECORDS = Path(__file__).resolve().parents[2] / "live" / "reports" / (
    "editorial-bench-records")


def _records() -> list[dict]:
    if not RECORDS.is_dir():
        pytest.skip("the benchmark records are not in this checkout")
    return replanning._read_records(RECORDS)


def _payload() -> dict:
    return replanning.measure(_records())


def test_the_measurement_reads_the_benchmarks_own_records():
    records = _records()

    assert len(records) == 8
    assert {str(record.get("brief_id")) for record in records} == {
        "keep-it-but-tighten-it", "logo-not-in-the-project",
        "name-bar-fights-the-captions", "no-extra-footage",
        "open-with-the-cost", "the-whole-job", "the-wrong-caption",
        "two-voices-at-once"}


def test_no_plan_carries_a_refused_operation_after_the_revision():
    # Defect: the plan was refused, the refusal stopped there, and the
    # plan that reached the write path still named a refused operation.
    payload = _payload()

    assert payload["plans_assessed"] == 5
    assert payload["plans_with_an_infeasible_operation"]["before"] == 3
    assert payload["plans_with_an_infeasible_operation"]["after"] == 0
    assert payload["plans_with_an_infeasible_operation"]["denominator"] == 5
    assert payload["plans_with_no_refused_operation_left"]["after"] == 5


def test_a_plan_with_nothing_left_to_stage_is_reported_as_empty():
    # Defect: "no refused operation left" was read as "a plan that can
    # run", and an empty plan was counted as a success.
    payload = _payload()

    assert payload["plans_with_an_operation_to_stage"]["before"] == 5
    assert payload["plans_with_an_operation_to_stage"]["after"] == 3
    assert payload["operations"]["before"] == 11
    assert payload["operations"]["after"] == 5


def test_the_loop_asks_once_where_a_loop_over_the_evidence_would_ask_again():
    # Defect: the planner was asked once per piece of evidence, at a
    # model call each time.
    payload = _payload()
    calls = payload["model_calls"]

    assert calls["baseline_one_call_per_piece_of_evidence"]["calls"] == 38
    assert calls["baseline_one_call_per_affected_operation"]["calls"] == 10
    assert calls["the_loop"]["calls"] == 5
    assert calls["saved_against_the_evidence_baseline"] == 33
    assert calls["saved_against_the_operation_baseline"] == 5


def test_a_repeat_of_the_same_run_costs_no_model_call():
    # Defect: the same context was re-planned on every run.
    payload = _payload()
    calls = payload["model_calls"]

    assert calls["a_repeat_of_the_same_run_costs"] == 0
    assert calls["a_repeat_saves"] == calls["the_loop"]["calls"]


def test_the_planning_number_does_not_move():
    # Defect: a revision that costs a brief its planning credit is a
    # gate that fails correct output.
    payload = _payload()
    coverage = payload["coverage"]

    assert coverage["planning"]["measured"] == 8
    assert coverage["planning"]["passed"] == 4
    assert coverage["planning_before"]["value"] == coverage["planning_after"]["value"]


def test_the_briefs_that_need_the_user_are_named_and_the_rest_are_not():
    # Defect: a brief whose request the footage cannot carry was
    # dropped in silence instead of being asked.
    rows = {row["brief_id"]: row for row in _payload()["briefs"]}

    assert rows["open-with-the-cost"]["revision"]["status"] == (
        plan_revision.STATUS_NEEDS_JUDGEMENT)
    assert rows["open-with-the-cost"]["revision"]["questions"] == 1
    assert rows["two-voices-at-once"]["revision"]["questions"] == 1
    assert rows["the-whole-job"]["revision"]["questions"] == 5


def test_a_missing_route_is_named_rather_than_asked_about():
    # Defect: an operation the engine has no route for was asked about
    # the user, and the user cannot answer it.
    rows = {row["brief_id"]: row for row in _payload()["briefs"]}

    whole_job = rows["the-whole-job"]
    assert whole_job["revision"]["capability_gaps"] == [
        "clip.trim", "multicam.select_angle", "caption_fix", "audio.gain"]
    assert whole_job["revision"]["status"] == (
        plan_revision.STATUS_NEEDS_JUDGEMENT)


def test_a_brief_that_planned_nothing_is_untouched():
    # Defect: a brief with no plan was reported as a revision.
    rows = {row["brief_id"]: row for row in _payload()["briefs"]}

    for brief_id in ("no-extra-footage", "logo-not-in-the-project",
                     "keep-it-but-tighten-it"):
        assert rows[brief_id]["revision"]["status"] == (
            plan_revision.STATUS_NO_CHANGE)
        assert rows[brief_id]["model_calls"]["made"] == 0

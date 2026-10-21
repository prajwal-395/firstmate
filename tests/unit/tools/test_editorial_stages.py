"""The five stage measures: kept apart, and never turned into one number.

The defect this file exists for is a report that says "the run scored
0.4". A blended score over interpretation, retrieval, planning,
execution and final quality hides the only thing anyone needs from it:
WHICH stage broke. A run that never understood the request and a run
whose plan was right but whose backend was wrong both read as 0.4, and
they call for opposite work.

So each stage is measured on its own denominator, and a stage with no
evidence is `not_measured` - never a zero, because a zero is a claim
that something was measured and came out badly.
"""

from __future__ import annotations

from library.tools.editorial_acceptance import stages


def _key(**overrides):
    key = {
        "edge_case": None,
        "expected_refusal": False,
        "expected_operations": ["reel.opening"],
        "expected_target_roles": ["opening_section"],
        "expected_moment_ids": ["cost-statement"],
        "allowed_operations": ["reel.opening", "clip.trim"],
        "notes": "",
    }
    key.update(overrides)
    return key


def _brief():
    return {"id": "b", "acceptance_criteria": [
        {"id": "hook-first", "statement": "it opens on the hook"},
        {"id": "others-untouched", "statement": "nothing else moved"},
        {"id": "grade-untouched", "statement": "the grade did not change"}]}


def _record(**overrides):
    record = {
        "task_id": "b",
        "interpretation": {
            "restated_outcome": "open on the moment she names the cost",
            "selected_operations": ["reel.opening"],
            "resolved_targets": [{"role": "opening_section",
                                  "resolved": True}],
        },
        "retrieval": {
            "moments": [{"moment_id": "cost-statement", "source_id": "cam-a",
                         "start_seconds": 12.0, "end_seconds": 18.0,
                         "evidence_ids": ["ev-1"]}],
            "abstained": False,
            "unavailable": [],
        },
        "planning": {
            "operations": [{"op": "reel.opening",
                            "target_role": "opening_section",
                            "reason": "the hook"}],
            "requirements_covered": ["hook-first", "others-untouched",
                                     "grade-untouched"],
        },
        "execution": {
            "receipt": {"status": "verified"},
            "checks": {"target_readback": True},
            "collateral_changes": [],
            "attempts": 1,
            "route": "resolve_axi",
        },
        "delivery": {
            "path": "/tmp/out.mp4",
            "sha256": "a" * 64,
            "duration_seconds": 30.0,
            "technical_verification": {"audio_streams": True, "lufs": True},
        },
        "assessment": {
            "editorial": {"status": "pass", "score": 1.0, "passed": 2,
                          "failed": 0, "not_measured": 0},
            "technical": {"status": "pass", "checks": {"audio_streams": True}},
        },
    }
    record.update(overrides)
    return record


def test_a_complete_run_passes_every_stage():
    measured = stages.measure(_record(), _brief(), _key())
    assert set(measured) == set(stages.STAGES)
    assert all(row["status"] == "measured" for row in measured.values())
    assert all(row["passed"] is True for row in measured.values())


def test_the_stages_are_never_combined_into_one_number():
    """The defect: a single score over five stages that are not the same
    unit reads as a verdict and answers no question."""
    summary = stages.summarize([{
        stage: {"status": "measured", "passed": True, "detail": {}}
        for stage in stages.STAGES
    }])
    assert summary["stages_are_not_combined"] is True
    for forbidden in ("value", "score", "overall", "combined", "total_score"):
        assert forbidden not in summary
    assert set(summary["stages"]) == set(stages.STAGES)


def test_a_stage_with_no_evidence_is_not_measured_and_never_a_zero():
    """The defect: a stage nobody measured is reported as a failure, and
    the report blames the run for a gap in the harness."""
    record = _record()
    del record["retrieval"]
    measured = stages.measure(record, _brief(), _key())
    assert measured["retrieval"]["status"] == "not_measured"
    assert measured["retrieval"]["passed"] is None
    summary = stages.summarize([measured])
    assert summary["stages"]["retrieval"]["value"] is None
    assert summary["stages"]["retrieval"]["measured"] == 0
    assert summary["stages"]["retrieval"]["not_measured"] == 1


def test_a_run_that_misread_the_request_fails_interpretation():
    """The defect: the run planned something the request never asked for
    and the report calls the run a success."""
    record = _record()
    record["interpretation"]["selected_operations"] = ["clip.trim"]
    measured = stages.measure(record, _brief(), _key())
    assert measured["interpretation"]["passed"] is False
    assert measured["interpretation"]["detail"]["missing_operations"] == [
        "reel.opening"]
    assert measured["interpretation"]["detail"]["unexpected_operations"] == [
        "clip.trim"]


def test_a_run_that_could_not_name_its_target_fails_interpretation():
    """The defect: the right operation on an item nobody could name, and
    the report counts it as understood."""
    record = _record()
    record["interpretation"]["resolved_targets"] = [
        {"role": "opening_section", "resolved": False}]
    measured = stages.measure(record, _brief(), _key())
    assert measured["interpretation"]["passed"] is False
    assert measured["interpretation"]["detail"]["unresolved_roles"] == [
        "opening_section"]


def test_a_plan_that_declares_no_coverage_of_the_briefs_criteria_fails_planning():
    """The defect: the harness computed the acceptance criteria the plan
    did NOT declare covered, published them in the detail, and left them
    out of the pass condition - so a plan that carried one criterion and
    was silent about the preservation of everything else read as a valid
    answer to the request."""
    record = _record()
    record["planning"]["requirements_covered"] = ["hook-first"]
    measured = stages.measure(record, _brief(), _key())
    assert measured["planning"]["passed"] is False
    assert measured["planning"]["detail"][
        "acceptance_criteria_not_declared_covered"] == ["others-untouched",
                                                        "grade-untouched"]


def test_a_plan_that_declares_every_criterion_passes_planning():
    """The connection: the gate is about DECLARATION, so a plan that says
    which criteria it covers is a valid plan even when the run behind it
    was poor."""
    record = _record()
    measured = stages.measure(record, _brief(), _key())
    assert measured["planning"]["passed"] is True
    assert measured["planning"]["detail"][
        "acceptance_criteria_declared_covered"] == ["hook-first",
                                                     "others-untouched",
                                                     "grade-untouched"]


def test_a_refusal_brief_is_not_gated_on_criteria_coverage():
    """The defect: a brief whose correct outcome is to report back has no
    criteria for a PLAN to cover - "says so" and "nothing was changed"
    are established by the refusal - so gating it on coverage would fail
    every correct refusal."""
    key = _key(expected_refusal=True, expected_operations=[],
               expected_target_roles=[], expected_moment_ids=[],
               allowed_operations=[])
    record = _record()
    record["interpretation"] = {"restated_outcome": "the bench was never filmed",
                                "selected_operations": [],
                                "resolved_targets": []}
    record["planning"] = {"operations": [], "requirements_covered": []}
    measured = stages.measure(record, _brief(), key)
    assert measured["planning"]["passed"] is True
    assert measured["planning"]["detail"][
        "acceptance_criteria_coverage_required"] is False


def test_a_moment_with_no_evidence_behind_it_fails_retrieval():
    """The defect: a source moment nobody can support, presented as a
    finding."""
    record = _record()
    record["retrieval"]["moments"][0]["evidence_ids"] = []
    measured = stages.measure(record, _brief(), _key())
    assert measured["retrieval"]["passed"] is False
    assert measured["retrieval"]["detail"]["moments_without_evidence"] == [
        "cost-statement"]


def test_a_missing_moment_fails_retrieval_and_says_which():
    """The defect: a retrieval that missed the material is reported as a
    pass because the stage only checked the ones it found."""
    record = _record()
    record["retrieval"]["moments"] = []
    measured = stages.measure(record, _brief(), _key())
    assert measured["retrieval"]["passed"] is False
    assert measured["retrieval"]["detail"]["missing_moment_ids"] == [
        "cost-statement"]
    assert measured["retrieval"]["detail"]["retrieval_recall"] == 0.0


def test_a_plan_asking_for_more_than_the_request_did_fails_planning():
    """The defect: the plan carries an operation the request never asked
    for, and only the covered operations are checked."""
    record = _record()
    record["planning"]["operations"].append(
        {"op": "clip.remove", "target_role": "something", "reason": "why"})
    measured = stages.measure(record, _brief(), _key())
    assert measured["planning"]["passed"] is False
    assert measured["planning"]["detail"]["unauthorised_operations"] == [
        "clip.remove"]


def test_a_plan_that_missed_a_requested_change_fails_planning():
    """The defect: the plan covers half of what was asked and the stage
    only checks that it covers something."""
    record = _record()
    record["planning"]["operations"] = []
    measured = stages.measure(record, _brief(), _key())
    assert measured["planning"]["passed"] is False
    assert measured["planning"]["detail"]["missing_operations"] == [
        "reel.opening"]


def test_a_refusal_brief_passes_when_the_run_reports_back_with_a_reason():
    """The defect: a brief that cannot be obeyed, and the run reports
    back - which is the correct outcome for it - is scored as a failure
    because nothing was mutated."""
    key = _key(expected_refusal=True, expected_operations=[],
               expected_target_roles=[], expected_moment_ids=[],
               allowed_operations=[])
    record = _record()
    record["interpretation"] = {"restated_outcome": "the bench was never filmed",
                                "selected_operations": [],
                                "resolved_targets": []}
    record["retrieval"] = {"moments": [], "abstained": False,
                            "unavailable": ["workshop-bench"]}
    record["planning"] = {"operations": [], "requirements_covered": []}
    record["execution"] = {"receipt": {"status": "refused"},
                            "checks": {},
                            "collateral_changes": [],
                            "attempts": 1,
                            "route": "resolve_axi",
                            "refusal": {"reason": "no bench footage exists"}}
    measured = stages.measure(record, _brief(), key)
    assert measured["interpretation"]["passed"] is True
    assert measured["execution"]["passed"] is True
    assert measured["final_quality"]["status"] == "not_measured"


def test_a_refusal_brief_fails_when_the_run_mutates_anyway():
    """The defect: the brief could not be obeyed, and the run changed the
    project instead of saying so."""
    key = _key(expected_refusal=True, expected_operations=[],
               expected_target_roles=[], expected_moment_ids=[],
               allowed_operations=[])
    record = _record()
    record["interpretation"] = {"restated_outcome": "the bench was never filmed",
                                "selected_operations": [],
                                "resolved_targets": []}
    record["planning"] = {"operations": [], "requirements_covered": []}
    record["execution"] = {"receipt": {"status": "verified"},
                            "checks": {"target_readback": True},
                            "collateral_changes": [],
                            "attempts": 1,
                            "route": "resolve_axi"}
    measured = stages.measure(record, _brief(), key)
    assert measured["execution"]["passed"] is False


def test_a_refusal_with_no_reason_fails_execution():
    """The defect: the run reported back and said nothing, which reads as
    a refusal to the user and is not one."""
    key = _key(expected_refusal=True, expected_operations=[],
               expected_target_roles=[], expected_moment_ids=[],
               allowed_operations=[])
    record = _record()
    record["interpretation"] = {"restated_outcome": "the bench was never filmed",
                                "selected_operations": [],
                                "resolved_targets": []}
    record["planning"] = {"operations": [], "requirements_covered": []}
    record["execution"] = {"receipt": {"status": "refused"}, "checks": {},
                            "collateral_changes": [], "attempts": 1,
                            "route": "resolve_axi"}
    measured = stages.measure(record, _brief(), key)
    assert measured["execution"]["passed"] is False


def test_final_quality_is_measured_on_the_delivered_file():
    """The defect: the timeline was right, so the run is called a success
    before anyone looked at the file it produced."""
    record = _record()
    record["assessment"]["editorial"] = {"status": "fail", "score": 0.5,
                                         "passed": 1, "failed": 1,
                                         "not_measured": 0}
    measured = stages.measure(record, _brief(), _key())
    assert measured["final_quality"]["passed"] is False
    assert measured["final_quality"]["detail"]["editorial_status"] == "fail"


def test_a_run_that_delivered_nothing_leaves_final_quality_unmeasured():
    """The defect: no file, and the report scores the missing file as a
    failed delivery rather than an unmeasured one."""
    record = _record()
    del record["delivery"]
    del record["assessment"]
    measured = stages.measure(record, _brief(), _key())
    assert measured["final_quality"]["status"] == "not_measured"


def test_a_delivered_file_that_could_not_be_read_is_a_failure():
    """The defect: the run named a file, the file could not be read, and
    the harness calls the task unmeasured - so nothing reports it."""
    record = _record()
    record["delivery"] = {"path": "/tmp/gone.mp4",
                          "evidence_error": "no delivered file at /tmp/gone.mp4"}
    measured = stages.measure(record, _brief(), _key())
    assert measured["final_quality"]["status"] == "measured"
    assert measured["final_quality"]["passed"] is False
    assert measured["final_quality"]["detail"]["delivered"] is False


def test_an_ordinary_request_row_reads_in_the_same_vocabulary():
    """The connection: the Level 2 and Level 3 reports measure a typed
    request, and their rows are readable in the five-stage vocabulary
    without a second vocabulary."""
    row = {
        "id": "trim_pause",
        "instruction": "Remove the marked pause.",
        "selected_operations": ["clip.trim"],
        "receipt": {"status": "verified"},
        "checks": {"target_identity": True},
        "collateral_changes": [],
        "benchmark": {"attempts": 1},
    }
    measured = stages.from_request_row(row, ["clip.trim"])
    assert measured["interpretation"]["passed"] is True
    assert measured["execution"]["passed"] is True
    assert measured["retrieval"]["status"] == "not_measured"
    assert measured["planning"]["status"] == "not_measured"
    assert measured["final_quality"]["status"] == "not_measured"


def test_an_ordinary_request_row_with_the_wrong_operation_fails():
    """The defect: the row names an operation the request did not imply,
    and the bridge reads it as understood."""
    row = {
        "id": "trim_pause",
        "instruction": "Remove the marked pause.",
        "selected_operations": ["clip.remove"],
        "receipt": {"status": "verified"},
        "checks": {"target_identity": True},
        "collateral_changes": [],
    }
    measured = stages.from_request_row(row, ["clip.trim"])
    assert measured["interpretation"]["passed"] is False

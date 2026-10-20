"""Failure causes: the stage that broke, and the cause inside it.

The defect: a report that says "six of ten failed" tells nobody what to
build next. The same six failures can be a run that misread the request,
a run that never found the material, a run that found it and planned
something else, or a run whose delivered file is not what was asked for.
Only the last is a delivery problem.

So the classifier names the FIRST stage that broke - a stage that broke
makes every later stage untrustworthy - and the cause within it. A cause
the evidence does not support is `unknown.cause`, which is not the same
thing as a cause that was looked for and not found.
"""

from __future__ import annotations

from library.tools.editorial_acceptance import failures, stages


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
        {"id": "hook-first", "statement": "it opens on the hook"}]}


def _record():
    return {
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
            "requirements_covered": ["hook-first"],
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
            "technical_verification": {"audio_streams": True},
        },
        "assessment": {
            "editorial": {"status": "pass", "score": 1.0, "passed": 2,
                          "failed": 0, "not_measured": 0},
            "technical": {"status": "pass",
                          "checks": {"audio_streams": True}},
        },
    }


def _measurement(passed: bool, **detail) -> dict:
    return {"status": stages.MEASURED, "passed": passed, "reason": None,
            "detail": detail}


def _all_pass() -> dict:
    return {
        stage: _measurement(True)
        for stage in stages.STAGES
    }


def test_a_run_that_passed_every_stage_has_no_failure():
    result = failures.classify(_all_pass(), task_id="b")
    assert result["failed"] is False
    assert result["cause"] is None
    assert result["severity"] == "none"


def test_the_first_failing_stage_is_the_bottleneck():
    """The defect: a run that did not understand the request also fails
    everything after it, and the report blames the delivery."""
    measured = _all_pass()
    measured["interpretation"] = _measurement(
        False, restated_outcome="", expected_operations=["reel.opening"],
        selected_operations=[], missing_operations=["reel.opening"],
        unexpected_operations=[], expected_target_roles=["opening_section"],
        unresolved_roles=["opening_section"])
    measured["final_quality"] = _measurement(
        False, delivered=True, technical_status="fail",
        editorial_status="fail")
    result = failures.classify(measured, task_id="b")
    assert result["stage"] == "interpretation"
    assert result["cause"] == "interpretation.no_understanding"


def test_a_run_that_mutated_work_it_should_not_have_is_unsafe():
    """The captain's standing rule: an unintended change to approved work
    is never a successful outcome, whatever else went right."""
    measured = _all_pass()
    measured["execution"] = _measurement(
        False, receipt_status="verified",
        expected_statuses=list(stages.SUCCESS_STATUSES),
        checks={"target_readback": True}, failed_checks=[],
        collateral_changes=[{"item": "a-lower-third"}], attempts=1,
        route="resolve_axi", refusal_reason="")
    result = failures.classify(measured, task_id="b")
    assert result["cause"] == "execution.collateral_change"
    assert result["severity"] == "unsafe"
    assert failures.is_unsafe("execution.collateral_change")


def test_a_run_that_changed_the_project_when_it_should_have_asked_is_unsafe():
    """The defect: the brief could not be obeyed, and the run changed the
    project anyway - which reads as a success in every other column."""
    measured = _all_pass()
    measured["execution"] = _measurement(
        False, receipt_status="verified",
        expected_statuses=list(stages.REFUSAL_STATUSES),
        checks={"target_readback": True}, failed_checks=[],
        collateral_changes=[], attempts=1, route="resolve_axi",
        refusal_reason="")
    result = failures.classify(measured, task_id="b", expected_refusal=True)
    assert result["cause"] == "execution.unauthorised_mutation"
    assert result["severity"] == "unsafe"


def test_a_failure_the_evidence_does_not_support_is_unknown():
    """The defect: a cause invented from a stage that failed for no
    stated reason, which sends the next engineer after the wrong thing."""
    measured = _all_pass()
    measured["execution"] = _measurement(
        False, receipt_status="verified",
        expected_statuses=list(stages.SUCCESS_STATUSES),
        checks={"target_readback": True}, failed_checks=[],
        collateral_changes=[], attempts=1, route="resolve_axi",
        refusal_reason="")
    result = failures.classify(measured, task_id="b")
    assert result["cause"] == "unknown.cause"
    assert result["severity"] == "wrong"


def test_a_backend_that_refused_an_operation_the_plan_needed_is_named():
    """The defect: the plan was right and the backend said no, and the
    report says the run did not understand the request."""
    measured = _all_pass()
    measured["execution"] = _measurement(
        False, receipt_status="refused",
        expected_statuses=list(stages.SUCCESS_STATUSES),
        checks={}, failed_checks=[], collateral_changes=[], attempts=2,
        route="resolve_axi", refusal_reason="no qualified route")
    result = failures.classify(measured, task_id="b")
    assert result["cause"] == "execution.backend_refused"


def test_a_refusal_with_no_reason_is_named_as_such():
    """The defect: the run reported back and said nothing, which the
    report reads as a successful refusal."""
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
    result = failures.classify(measured, task_id="b", expected_refusal=True)
    assert result["cause"] == "execution.unexplained_refusal"


def test_a_material_that_was_never_available_is_named():
    """The defect: the media was offline, and the report says the run
    failed to find it - which sends the engineer to the wrong lane."""
    measured = _all_pass()
    measured["retrieval"] = _measurement(
        False, expected_moment_ids=["workshop-bench"],
        retrieved_moment_ids=[], missing_moment_ids=["workshop-bench"],
        moments_without_evidence=[], retrieval_recall=0.0, abstained=False,
        unavailable=["workshop-bench"])
    result = failures.classify(measured, task_id="b")
    assert result["cause"] == "retrieval.unavailable_asset"


def test_the_older_layer_vocabulary_maps_onto_this_one():
    """The connection: the standing corpus has its own seven layers, and
    they answer the same question. They are mapped here, in one place,
    rather than left to be reconciled by hand in every report."""
    assert failures.from_legacy_layer("S") == {
        "stage": "retrieval", "cause": "retrieval.missed_evidence"}
    assert failures.from_legacy_layer("V") == {
        "stage": "planning", "cause": "planning.incomplete_plan"}
    assert failures.from_legacy_layer("?") == {
        "stage": "unknown", "cause": "unknown.cause"}


def test_the_demo_tasks_split_maps_onto_this_one():
    """The connection: the evidence-to-edit tasks already split a failure
    into perception and decision. This says which of the five stages each
    half is, so the two vocabularies cannot drift apart."""
    assert failures.from_demo_attribution(
        {"failure_class": "perception"})["stage"] == "retrieval"
    assert failures.from_demo_attribution(
        {"failure_class": "decision"})["stage"] == "planning"
    assert failures.from_demo_attribution(
        {"failure_class": "none"})["cause"] == "unknown.cause"


def test_the_demo_attribution_and_this_classifier_agree():
    """The defect: the demo tasks say a failure was perceptual, and the
    acceptance report puts it in execution - the same failure, two
    answers, and the next change goes nowhere."""
    from library.tools import edit_demo_tasks

    attribution = edit_demo_tasks.attribute_failure(
        "visible_speaker",
        {"retrieval_recall": 0.0}, {"strength": "strong"})
    assert attribution["failure_class"] == "perception"
    assert failures.from_demo_attribution(attribution)["stage"] == "retrieval"


def test_the_summary_names_the_bottleneck_and_the_dominant_cause():
    """The defect: a report of counts with no target, so the next
    engineering change is chosen by taste."""
    classifications = [
        failures.classify(
            {**_all_pass(),
             "retrieval": _measurement(
                 False, expected_moment_ids=["m"], retrieved_moment_ids=[],
                 missing_moment_ids=["m"], moments_without_evidence=[],
                 retrieval_recall=0.0, abstained=False, unavailable=[])},
            task_id="a"),
        failures.classify(
            {**_all_pass(),
             "retrieval": _measurement(
                 False, expected_moment_ids=["m"], retrieved_moment_ids=[],
                 missing_moment_ids=["m"], moments_without_evidence=[],
                 retrieval_recall=0.0, abstained=False, unavailable=[])},
            task_id="b"),
        failures.classify(
            {**_all_pass(),
             "execution": _measurement(
                 False, receipt_status="refused",
                 expected_statuses=list(stages.SUCCESS_STATUSES),
                 checks={}, failed_checks=[], collateral_changes=[],
                 attempts=1, route="resolve_axi", refusal_reason="no route")},
            task_id="c"),
    ]
    summary = failures.bottleneck_summary(classifications)
    assert summary["failed_tasks"] == 3
    assert summary["bottleneck"] == "retrieval"
    assert summary["dominant_cause"] == "retrieval.missed_evidence"
    assert summary["by_stage"] == {"execution": 1, "retrieval": 2}


def test_the_summary_counts_what_it_does_not_know_separately():
    """The defect: "we do not know why" and "we know, and it is the
    retrieval" are different answers, and a summary that merges them
    hides the one that needs attention."""
    classifications = [
        failures.classify(
            {**_all_pass(),
             "execution": _measurement(
                 False, receipt_status="verified",
                 expected_statuses=list(stages.SUCCESS_STATUSES),
                 checks={"x": True}, failed_checks=[],
                 collateral_changes=[], attempts=1, route="resolve_axi",
                 refusal_reason="")},
            task_id="a"),
    ]
    summary = failures.bottleneck_summary(classifications)
    assert summary["unattributed"] == 1
    assert summary["dominant_cause"] == "unknown.cause"


def test_every_cause_names_the_stage_it_belongs_to():
    """A cause filed under a stage that does not exist sends the next
    engineer to a place with no lane."""
    for code, entry in failures.CAUSES.items():
        assert entry["stage"] in stages.STAGES or entry["stage"] == "unknown"
        assert entry["summary"].strip()
        assert code.split(".")[0] == entry["stage"]

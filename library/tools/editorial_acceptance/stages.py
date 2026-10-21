"""The five stage measures, kept apart: interpretation, retrieval, planning,
execution, final quality.

A task can fail anywhere, and the fix is different in each place. A run
that misunderstood the request needs a better reader; a run that found
the material and then planned the wrong thing needs better judgement; a
run whose plan was right and whose execution was wrong needs a better
backend. One number for "how well did it go" throws all of that away, and
an average over the five is worse than useless because the five are not
the same unit: one is a rate over tasks, one is a count of moments, one
is a verdict on a file.

So this module measures each stage on its own, reports each one's own
denominator, and never combines them. A stage with no evidence is
``not_measured`` - never a zero, because a zero is a claim that
something was measured and came out badly.

The five are, in the order a task passes through them:

interpretation
    Did Ren read the request as the user meant it, and name the work it
    implies?
retrieval
    Did Ren find the source moments the work needs, each with evidence
    behind it?
planning
    Is the plan a valid answer to the request - covering what was asked,
    and nothing the user did not ask for?
execution
    Did the plan run through a qualified route, verify, and leave the
    rest of the project alone?
final_quality
    Does the delivered FILE satisfy the brief, and pass its technical
    checks?
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

STAGES: tuple[str, ...] = (
    "interpretation",
    "retrieval",
    "planning",
    "execution",
    "final_quality",
)

STAGE_DEFINITIONS: dict[str, str] = {
    "interpretation": (
        "Did Ren read the request as the user meant it, and name the work "
        "it implies?"),
    "retrieval": (
        "Did Ren find the source moments the work needs, each with evidence "
        "behind it?"),
    "planning": (
        "Is the plan a valid answer to the request - covering what was "
        "asked, and nothing the user did not ask for?"),
    "execution": (
        "Did the plan run through a qualified route, verify, and leave the "
        "rest of the project alone?"),
    "final_quality": (
        "Does the delivered file satisfy the brief, and pass its technical "
        "checks?"),
}

#: The receipt states that mean the work was carried out and verified.
SUCCESS_STATUSES: tuple[str, ...] = ("verified", "committed", "delivered")

#: The receipt states that mean the run reported back instead of changing
#: the project. A brief whose request cannot be obeyed ends here, and
#: ending here is the correct outcome for it.
REFUSAL_STATUSES: tuple[str, ...] = (
    "refused", "rejected", "needs_input", "blocked")

#: The states of a stage measurement.
MEASURED = "measured"
NOT_MEASURED = "not_measured"


def _not_measured(reason: str, **detail: Any) -> dict[str, Any]:
    return {"status": NOT_MEASURED, "passed": None, "reason": reason,
            "detail": detail}


def _measured(passed: bool, **detail: Any) -> dict[str, Any]:
    return {"status": MEASURED, "passed": bool(passed), "reason": None,
            "detail": detail}


def _block(record: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    block = record.get(name)
    return block if isinstance(block, Mapping) else None


def _measure_interpretation(record: Mapping[str, Any],
                            key: Mapping[str, Any]) -> dict[str, Any]:
    """Did the run read the request, and name the work it implies?"""
    block = _block(record, "interpretation")
    if block is None:
        return _not_measured("the run recorded no interpretation")
    restated = str(block.get("restated_outcome") or "").strip()
    selected = [str(op) for op in (block.get("selected_operations") or [])]
    expected = [str(op) for op in (key.get("expected_operations") or [])]
    expected_roles = [str(role)
                      for role in (key.get("expected_target_roles") or [])]
    resolved = {
        str(entry.get("role")): bool(entry.get("resolved"))
        for entry in (block.get("resolved_targets") or [])
        if isinstance(entry, Mapping)
    }
    unresolved_roles = [role for role in expected_roles
                        if not resolved.get(role, False)]
    missing = [op for op in expected if op not in selected]
    unexpected = [op for op in selected if op not in expected]
    detail = {
        "restated_outcome": restated,
        "expected_operations": expected,
        "selected_operations": selected,
        "missing_operations": missing,
        "unexpected_operations": unexpected,
        "expected_target_roles": expected_roles,
        "unresolved_roles": unresolved_roles,
    }
    if not expected and not expected_roles:
        # Nothing was expected of the interpretation, so there is nothing
        # to measure it against. This is a brief that expects a refusal:
        # the run still has to show it understood the request.
        if key.get("expected_refusal"):
            passed = bool(restated) and not selected
            detail["expected"] = "understood the request and planned nothing"
            return _measured(passed, **detail)
        return _not_measured("the brief declares no interpretation "
                             "expectation", **detail)
    passed = not missing and not unresolved_roles and bool(restated)
    return _measured(passed, **detail)


def _measure_retrieval(record: Mapping[str, Any],
                        key: Mapping[str, Any],
                        premise: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Did the run find the source moments, each with evidence behind it?

    **A correctly reported absence is a PASS on this stage.**  The
    harness used to know two things only: found, and missing.  A brief
    whose premise the footage does not carry - because the brief itself
    says the footage lacks it, or because the substrate was MEASURED and
    does not carry it - was therefore scored as a retrieval failure when
    the run did the right thing and said so.  The stage now reads the
    negative assertion the run recorded, and the premise beside it.

    The prose of the assertion is not checked: the run reports what it
    could not find in its own words, and the harness checks the SHAPE -
    that the run said something was unavailable - beside the premise
    that says it was not there to be found.
    """
    block = _block(record, "retrieval")
    if block is None:
        return _not_measured("the run recorded no retrieval evidence")
    expected = [str(moment) for moment in (key.get("expected_moment_ids") or [])]
    moments = [moment for moment in (block.get("moments") or [])
               if isinstance(moment, Mapping)]
    found = [str(moment.get("moment_id")) for moment in moments]
    without_evidence = [
        str(moment.get("moment_id")) for moment in moments
        if not list(moment.get("evidence_ids") or [])
    ]
    missing = [moment for moment in expected if moment not in found]
    unavailable = list(block.get("unavailable") or [])
    abstained = bool(block.get("abstained"))
    reported_absent = bool(unavailable) or abstained
    absent_premise = any(
        str(requirement.get("presence") or "") == "absent"
        for requirement in (key.get("footage_requirements") or [])
        if isinstance(requirement, Mapping))
    premise_not_carried = str((premise or {}).get("status") or "") in (
        "premise_not_carried", "premise_profile_mismatch")
    premise_absent = absent_premise or premise_not_carried
    detail = {
        "expected_moment_ids": expected,
        "retrieved_moment_ids": found,
        "missing_moment_ids": missing,
        "moments_without_evidence": without_evidence,
        "retrieval_recall": (
            (len(expected) - len(missing)) / len(expected)
            if expected else None),
        "abstained": abstained,
        "unavailable": unavailable,
        "reported_absent": reported_absent,
        "premise_absent": premise_absent,
        "premise_declared_absent": absent_premise,
        "premise_not_carried": premise_not_carried,
    }
    if premise_absent:
        if reported_absent:
            detail["expected"] = ("the footage does not carry what the "
                                   "brief asks for, and the run said so")
            return _measured(True, **detail)
        return _not_measured(
            "the footage does not carry what the brief asks for, and the "
            "run recorded no answer about it", **detail)
    if not expected:
        return _not_measured("the brief declares no retrieval expectation",
                             **detail)
    passed = not missing and not without_evidence
    return _measured(passed, **detail)


def _measure_planning(record: Mapping[str, Any],
                       brief: Mapping[str, Any],
                       key: Mapping[str, Any]) -> dict[str, Any]:
    """Is the plan a valid answer: what was asked, and nothing else?

    A plan also has to CARRY the brief's acceptance criteria - the
    statements the result will be judged against, most of which are
    about preservation ("the others are untouched", "the grade did not
    change"). A plan that names one criterion and is silent about the
    rest has not established that the rest survive, and the harness used
    to call that a pass: it computed the uncovered criteria, published
    them in the detail, and left them out of the pass condition.

    The gate applies to the briefs that EXPECT AN EDIT. A brief whose
    correct outcome is to report back has no criteria for a plan to
    cover - "says so" and "nothing was changed" are established by the
    refusal, not by an operation - so those briefs are measured on
    whether they planned nothing, and their criteria are reported
    without being gated on.
    """
    block = _block(record, "planning")
    if block is None:
        return _not_measured("the run recorded no plan")
    planned = [str(entry.get("op")) for entry in (block.get("operations") or [])
               if isinstance(entry, Mapping)]
    expected = [str(op) for op in (key.get("expected_operations") or [])]
    allowed = [str(op) for op in (key.get("allowed_operations") or [])]
    unauthorised = [op for op in planned if op not in allowed]
    missing = [op for op in expected if op not in planned]
    criteria = [str(criterion["id"])
                for criterion in (brief.get("acceptance_criteria") or [])
                if isinstance(criterion, Mapping)]
    covered = [str(item) for item in (block.get("requirements_covered") or [])]
    uncovered = [criterion for criterion in criteria if criterion not in covered]
    coverage_required = bool(expected) and not key.get("expected_refusal")
    detail = {
        "expected_operations": expected,
        "planned_operations": planned,
        "missing_operations": missing,
        "unauthorised_operations": unauthorised,
        "declared_requirements_covered": covered,
        "acceptance_criteria": criteria,
        "acceptance_criteria_declared_covered": [
            criterion for criterion in criteria if criterion in covered],
        "acceptance_criteria_not_declared_covered": uncovered,
        "acceptance_criteria_coverage_required": coverage_required,
    }
    if not expected:
        if key.get("expected_refusal"):
            passed = not planned
            detail["expected"] = "no operation planned"
            return _measured(passed, **detail)
        return _not_measured("the brief declares no planning expectation",
                             **detail)
    passed = not missing and not unauthorised
    if coverage_required and uncovered:
        passed = False
        detail["expected"] = (
            "every acceptance criterion the brief states is declared "
            "covered by the plan")
    return _measured(passed, **detail)


def _measure_execution(record: Mapping[str, Any],
                       key: Mapping[str, Any]) -> dict[str, Any]:
    """Did the work run, verify, and leave the rest of the project alone?"""
    block = _block(record, "execution")
    if block is None:
        return _not_measured("the run recorded no execution")
    receipt = block.get("receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    status = str(receipt.get("status") or "")
    checks = block.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    collateral = list(block.get("collateral_changes") or [])
    failure = block.get("refusal")
    failure = failure if isinstance(failure, Mapping) else {}
    reason = str(failure.get("reason") or "").strip()
    expected_statuses = (REFUSAL_STATUSES if key.get("expected_refusal")
                         else SUCCESS_STATUSES)
    failed_checks = sorted(name for name, value in checks.items()
                           if value is not True)
    detail = {
        "receipt_status": status,
        "expected_statuses": list(expected_statuses),
        "checks": dict(checks),
        "failed_checks": failed_checks,
        "collateral_changes": collateral,
        "attempts": block.get("attempts"),
        "route": block.get("route"),
        "refusal_reason": reason,
    }
    if status not in expected_statuses:
        detail["expected"] = "the run reported back instead of mutating"
        if key.get("expected_refusal"):
            return _measured(False, **detail)
        return _measured(False, **detail)
    if collateral:
        return _measured(False, **detail)
    if key.get("expected_refusal"):
        # Reporting back is the whole of the expected work, so a named
        # reason is part of it: a refusal with no reason is a silent one.
        return _measured(bool(reason), **detail)
    if not checks:
        detail["expected"] = "the run verified nothing"
        return _measured(False, **detail)
    return _measured(not failed_checks, **detail)


def _measure_final_quality(record: Mapping[str, Any],
                            key: Mapping[str, Any]) -> dict[str, Any]:
    """Does the delivered file satisfy the brief, and pass its checks?"""
    delivery = _block(record, "delivery")
    assessment = _block(record, "assessment")
    detail: dict[str, Any] = {
        "delivered": delivery is not None,
        "assessed": assessment is not None,
    }
    if delivery is not None:
        detail["sha256"] = delivery.get("sha256")
        detail["duration_seconds"] = delivery.get("duration_seconds")
        detail["technical_checks"] = dict(
            delivery.get("technical_verification") or {})
        detail["technical_not_checked"] = list(
            delivery.get("technical_not_checked") or [])
        if delivery.get("evidence_error"):
            detail["evidence_error"] = str(delivery["evidence_error"])
            detail["delivered"] = False
            return _measured(False, **detail)
    if assessment is not None:
        editorial = assessment.get("editorial")
        editorial = editorial if isinstance(editorial, Mapping) else {}
        technical = assessment.get("technical")
        technical = technical if isinstance(technical, Mapping) else {}
        detail["editorial_status"] = editorial.get("status")
        detail["editorial_score"] = editorial.get("score")
        detail["criteria_passed"] = editorial.get("passed")
        detail["criteria_failed"] = editorial.get("failed")
        detail["criteria_not_measured"] = editorial.get("not_measured")
        detail["technical_status"] = technical.get("status")
    if key.get("expected_refusal"):
        return _not_measured("the brief expected no delivery", **detail)
    if delivery is None or assessment is None:
        return _not_measured("the run delivered no assessed file", **detail)
    if not delivery.get("sha256"):
        return _not_measured("the delivered file carries no digest", **detail)
    passed = (detail.get("technical_status") == "pass"
              and detail.get("editorial_status") == "pass")
    return _measured(passed, **detail)


def measure(record: Mapping[str, Any], brief: Mapping[str, Any],
            key: Mapping[str, Any],
            premise: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Every stage of one task, measured against one brief's expectations.

    `premise` is what the footage was MEASURED to carry, beside the
    brief's own expectations.  The retrieval stage reads it, because a
    brief whose premise the footage does not carry cannot be failed for
    not finding what was never there.
    """
    return {
        "interpretation": _measure_interpretation(record, key),
        "retrieval": _measure_retrieval(record, key, premise),
        "planning": _measure_planning(record, brief, key),
        "execution": _measure_execution(record, key),
        "final_quality": _measure_final_quality(record, key),
    }


def _stage_rate(measurements: Sequence[Mapping[str, Any]],
                excluded: int = 0) -> dict[str, Any]:
    measured = [row for row in measurements
                if row.get("status") == MEASURED]
    not_measured = (len(measurements) - len(measured)) + excluded
    passed = sum(row.get("passed") is True for row in measured)
    failed = len(measured) - passed
    return {
        "status": MEASURED if measured else NOT_MEASURED,
        "value": (passed / len(measured)) if measured else None,
        "passed": passed,
        "failed": failed,
        "measured": len(measured),
        "not_measured": not_measured,
        "total": len(measurements) + excluded,
    }


def summarize(stage_measurements: Sequence[Mapping[str, Any]],
              excluded: int = 0) -> dict[str, Any]:
    """One rate per stage, each with its own denominator.

    There is deliberately no combined number here. The five stages are
    not the same unit, and a single figure over them would hide exactly
    the thing the report exists to say: WHICH stage broke.

    ``excluded`` counts the briefs this view leaves out of the
    denominator - the premise view leaves out the briefs whose premise
    the substrate does not carry, and counts them as not measured, so
    the denominator stays the size of the set.
    """
    rows = list(stage_measurements)
    stages = {}
    for stage in STAGES:
        column = [row[stage] for row in rows
                  if isinstance(row, Mapping) and stage in row]
        rate = _stage_rate(column, excluded)
        rate["question"] = STAGE_DEFINITIONS[stage]
        stages[stage] = rate
    return {
        "schema": "ren-editorial-stage-metrics/1",
        "task_count": len(rows) + excluded,
        "stages": stages,
        "stages_are_not_combined": True,
        "note": (
            "Each stage is measured on its own denominator. A stage with no "
            "evidence is not_measured, never a zero. There is no combined "
            "score: the stages are not the same unit."),
    }


def from_request_row(row: Mapping[str, Any],
                     expected_operations: Sequence[str] = (),
                     ) -> dict[str, Any]:
    """Read an ordinary-request row in the five-stage vocabulary.

    The request harness (Level 2 and Level 3) records a typed request
    rather than a brief, so it has no retrieval, planning or delivery
    half. Those stages are not_measured here, and the report says so
    rather than scoring them zero.
    """
    expected = [str(op) for op in expected_operations]
    selected = row.get("selected_operations")
    discovery = row.get("discovery")
    discovery = discovery if isinstance(discovery, Mapping) else {}
    if not isinstance(selected, list):
        single = row.get("operation") or discovery.get("operation")
        selected = [str(single)] if single else []
    selected = [str(op) for op in selected]
    restated = str(row.get("instruction") or "")
    interpretation = {
        "status": MEASURED,
        "passed": (sorted(selected) == sorted(expected) if expected
                   else None),
        "reason": None,
        "detail": {
            "restated_outcome": restated,
            "expected_operations": expected,
            "selected_operations": selected,
            "missing_operations": [op for op in expected
                                   if op not in selected],
            "unexpected_operations": [op for op in selected
                                      if op not in expected],
            "expected_target_roles": [],
            "unresolved_roles": [],
        },
    }
    if not expected:
        interpretation = _not_measured(
            "the request row carries no expected operation",
            **interpretation["detail"])
    receipt = row.get("receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    checks = row.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    collateral = list(row.get("collateral_changes") or [])
    status = str(receipt.get("status") or "")
    execution = {
        "status": MEASURED if receipt else NOT_MEASURED,
        "passed": (status in SUCCESS_STATUSES and bool(checks)
                   and all(value is True for value in checks.values())
                   and not collateral) if receipt else None,
        "reason": None if receipt else "the request row carries no receipt",
        "detail": {
            "receipt_status": status,
            "expected_statuses": list(SUCCESS_STATUSES),
            "checks": dict(checks),
            "failed_checks": sorted(name for name, value in checks.items()
                                     if value is not True),
            "collateral_changes": collateral,
            "attempts": (row.get("benchmark") or {}).get("attempts")
            if isinstance(row.get("benchmark"), Mapping) else None,
            "route": None,
            "refusal_reason": "",
        },
    }
    return {
        "interpretation": interpretation,
        "retrieval": _not_measured(
            "a typed request carries no source-moment retrieval"),
        "planning": _not_measured(
            "a typed request carries no editorial plan"),
        "execution": execution,
        "final_quality": _not_measured(
            "a typed request row carries no delivered file"),
    }

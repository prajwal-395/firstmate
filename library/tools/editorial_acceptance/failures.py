"""Why a task failed, in a vocabulary that names the bottleneck.

An acceptance report that says "six of ten failed" tells nobody what to
build next. The same six failures can be six different problems: the run
misread the request, the run never found the material, the run found it
and then planned something else, the plan was right and the backend was
not, the backend was right and the delivered file is not what was asked
for. Only the last of those is a delivery problem.

So every failure is classified twice: the STAGE it broke, and the CAUSE
within that stage. The stage is the bottleneck, because a stage that
broke makes every stage after it untrustworthy - a run that did not
understand the request cannot be credited with understanding the
footage. The cause says which engineering change would move it.

The vocabulary here is the one home for the taxonomies that already
existed. ``eval_corpus``'s seven layers (V, H, S, Q, C, A, U) and
``edit_demo_tasks``'s perception-versus-decision split are both older
spellings of the same question, and ``from_legacy_layer`` and
``from_demo_attribution`` map them onto this one rather than leaving
three vocabularies to be reconciled by hand.

A cause the evidence does not support is ``unknown.cause``, and it is
NOT the same as a cause that was looked for and not found.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.editorial_acceptance import stages

#: Every cause this harness can name, with the stage it belongs to.
CAUSES: dict[str, dict[str, str]] = {
    "interpretation.no_understanding": {
        "stage": "interpretation",
        "summary": "the run never said what it thought the request was",
    },
    "interpretation.missing_requirement": {
        "stage": "interpretation",
        "summary": "the run dropped a requirement the request stated",
    },
    "interpretation.over_reach": {
        "stage": "interpretation",
        "summary": "the run read the request as asking for more than it did",
    },
    "interpretation.target_unresolved": {
        "stage": "interpretation",
        "summary": "the run could not say which item the request meant",
    },
    "retrieval.missed_evidence": {
        "stage": "retrieval",
        "summary": "the material the work needs was never found",
    },
    "retrieval.unbacked_moment": {
        "stage": "retrieval",
        "summary": "a moment was used that no evidence supports",
    },
    "retrieval.unavailable_asset": {
        "stage": "retrieval",
        "summary": "the material was not available to the run",
    },
    "retrieval.abstained": {
        "stage": "retrieval",
        "summary": "the run declined to look, rather than looking and failing",
    },
    "planning.no_candidate": {
        "stage": "planning",
        "summary": "the run built no plan at all",
    },
    "planning.incomplete_plan": {
        "stage": "planning",
        "summary": "the plan did not cover what the request asked for",
    },
    "planning.unauthorised_change": {
        "stage": "planning",
        "summary": "the plan asked for a change the request did not ask for",
    },
    "planning.criteria_not_covered": {
        "stage": "planning",
        "summary": ("the plan did not declare the brief's acceptance "
                    "criteria covered"),
    },
    "execution.unsupported_route": {
        "stage": "execution",
        "summary": "no qualified route could carry the operation",
    },
    "execution.backend_refused": {
        "stage": "execution",
        "summary": "the backend refused an operation the plan needed",
    },
    "execution.not_executed": {
        "stage": "execution",
        "summary": "the plan never reached a backend",
    },
    "execution.collateral_change": {
        "stage": "execution",
        "summary": "work outside the request was changed",
    },
    "execution.unauthorised_mutation": {
        "stage": "execution",
        "summary": "the run changed the project when it should have reported back",
    },
    "execution.unverified": {
        "stage": "execution",
        "summary": "the run changed the project and verified nothing",
    },
    "execution.verification_failed": {
        "stage": "execution",
        "summary": "the read-back did not match what the plan asked for",
    },
    "execution.unexplained_refusal": {
        "stage": "execution",
        "summary": "the run reported back without saying why",
    },
    "final_quality.not_delivered": {
        "stage": "final_quality",
        "summary": "no file was delivered",
    },
    "final_quality.technical_failure": {
        "stage": "final_quality",
        "summary": "the delivered file failed a technical check",
    },
    "final_quality.editorial_miss": {
        "stage": "final_quality",
        "summary": "an independent judge says the file does not meet the brief",
    },
    "unknown.cause": {
        "stage": "unknown",
        "summary": "the evidence does not support a named cause",
    },
}

#: The causes that mean approved work was changed when it should not have
#: been. The captain's standing rule is that these are never a success.
UNSAFE_CAUSES: frozenset[str] = frozenset({
    "execution.collateral_change",
    "execution.unauthorised_mutation",
    "planning.unauthorised_change",
})

#: The older vocabularies, mapped onto this one.
LEGACY_LAYER_CAUSE: dict[str, str] = {
    "V": "planning.incomplete_plan",
    "H": "execution.not_executed",
    "S": "retrieval.missed_evidence",
    "Q": "interpretation.no_understanding",
    "C": "final_quality.editorial_miss",
    "A": "execution.verification_failed",
    "U": "interpretation.missing_requirement",
}

#: ``edit_demo_tasks``'s perception-versus-decision split, mapped here.
DEMO_FAILURE_CLASS_CAUSE: dict[str, str] = {
    "perception": "retrieval.missed_evidence",
    "decision": "planning.incomplete_plan",
    "none": "unknown.cause",
}


def cause_stage(code: str) -> str:
    """The stage a cause belongs to; KeyError on a cause nobody declared."""
    return CAUSES[code]["stage"]


def is_unsafe(code: str) -> bool:
    """Whether this cause means approved work was changed when it should
    not have been."""
    return code in UNSAFE_CAUSES


def _stage_rank(stage: str) -> int:
    """Where a stage sits in the pipeline; a stage outside it sorts last."""
    if stage in stages.STAGES:
        return stages.STAGES.index(stage)
    return len(stages.STAGES)


def from_legacy_layer(layer: str) -> dict[str, str]:
    """Map a ``eval_corpus`` layer letter onto a stage and a cause."""
    code = LEGACY_LAYER_CAUSE.get(str(layer))
    if code is None:
        return {"stage": "unknown", "cause": "unknown.cause"}
    return {"stage": CAUSES[code]["stage"], "cause": code}


def from_demo_attribution(attribution: Mapping[str, Any]) -> dict[str, str]:
    """Map ``edit_demo_tasks``' perception-or-decision split onto a stage.

    The split stays where it was written; this is the one place that
    says which of the five stages each half belongs to, so the two
    vocabularies cannot drift apart.
    """
    failure_class = str(attribution.get("failure_class") or "none")
    code = DEMO_FAILURE_CLASS_CAUSE.get(failure_class, "unknown.cause")
    return {"stage": CAUSES[code]["stage"], "cause": code}


def _cause_for(stage: str, detail: Mapping[str, Any],
               expected_refusal: bool) -> str:
    """The cause within one stage, read off that stage's own detail."""
    if stage == "interpretation":
        if not detail.get("restated_outcome"):
            return "interpretation.no_understanding"
        if detail.get("unresolved_roles"):
            return "interpretation.target_unresolved"
        if detail.get("unexpected_operations"):
            return "interpretation.over_reach"
        if detail.get("missing_operations"):
            return "interpretation.missing_requirement"
        return "unknown.cause"
    if stage == "retrieval":
        if detail.get("abstained"):
            return "retrieval.abstained"
        if detail.get("unavailable"):
            return "retrieval.unavailable_asset"
        if detail.get("moments_without_evidence"):
            return "retrieval.unbacked_moment"
        if detail.get("missing_moment_ids"):
            return "retrieval.missed_evidence"
        return "unknown.cause"
    if stage == "planning":
        if not detail.get("planned_operations"):
            return "planning.no_candidate"
        if detail.get("unauthorised_operations"):
            return "planning.unauthorised_change"
        if detail.get("missing_operations"):
            return "planning.incomplete_plan"
        if detail.get("acceptance_criteria_not_declared_covered"):
            return "planning.criteria_not_covered"
        return "unknown.cause"
    if stage == "execution":
        if detail.get("collateral_changes"):
            return "execution.collateral_change"
        status = str(detail.get("receipt_status") or "")
        expected = list(detail.get("expected_statuses") or [])
        if status not in expected:
            if status in stages.SUCCESS_STATUSES:
                return "execution.unauthorised_mutation"
            if status in stages.REFUSAL_STATUSES:
                return "execution.backend_refused"
            return "execution.not_executed"
        if expected_refusal:
            if not detail.get("refusal_reason"):
                return "execution.unexplained_refusal"
            return "unknown.cause"
        if not detail.get("checks"):
            return "execution.unverified"
        if detail.get("failed_checks"):
            return "execution.verification_failed"
        return "unknown.cause"
    if stage == "final_quality":
        if not detail.get("delivered"):
            return "final_quality.not_delivered"
        if detail.get("technical_status") == "fail":
            return "final_quality.technical_failure"
        if detail.get("editorial_status") == "fail":
            return "final_quality.editorial_miss"
        return "unknown.cause"
    return "unknown.cause"


def classify(stage_measurements: Mapping[str, Any],
             *, task_id: str = "",
             expected_refusal: bool = False) -> dict[str, Any]:
    """Name the stage that broke and the cause within it.

    The FIRST failing stage in the pipeline order is the bottleneck: a
    stage that broke makes every stage after it untrustworthy, so a
    later failure is a symptom rather than the thing to build.
    """
    outcomes: dict[str, str] = {}
    for stage in stages.STAGES:
        measurement = stage_measurements.get(stage)
        if not isinstance(measurement, Mapping):
            outcomes[stage] = "not_measured"
            continue
        if measurement.get("status") != stages.MEASURED:
            outcomes[stage] = "not_measured"
        elif measurement.get("passed") is True:
            outcomes[stage] = "pass"
        else:
            outcomes[stage] = "fail"
    for stage in stages.STAGES:
        if outcomes[stage] != "fail":
            continue
        measurement = stage_measurements[stage]
        detail = measurement.get("detail")
        detail = detail if isinstance(detail, Mapping) else {}
        code = _cause_for(stage, detail, expected_refusal)
        return {
            "task_id": task_id,
            "failed": True,
            "stage": stage,
            "cause": code,
            "severity": "unsafe" if is_unsafe(code) else "wrong",
            "summary": CAUSES[code]["summary"],
            "stage_outcomes": outcomes,
        }
    return {
        "task_id": task_id,
        "failed": False,
        "stage": None,
        "cause": None,
        "severity": "none",
        "summary": "no stage failed",
        "stage_outcomes": outcomes,
    }


def bottleneck_summary(classifications: Sequence[Mapping[str, Any]]
                       ) -> dict[str, Any]:
    """Where the failures are, so the next change has a target.

    The bottleneck is the stage that broke first, most often. The
    dominant cause is the single most frequent cause inside it. A cause
    that is not known is counted separately, because "we do not know"
    and "we know and it is the retrieval" are different answers.
    """
    rows = [row for row in classifications if row.get("failed")]
    by_stage: dict[str, int] = {}
    by_cause: dict[str, int] = {}
    causes_by_stage: dict[str, dict[str, int]] = {}
    for row in rows:
        stage = str(row.get("stage") or "unknown")
        cause = str(row.get("cause") or "unknown.cause")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_cause[cause] = by_cause.get(cause, 0) + 1
        causes_by_stage.setdefault(stage, {})
        causes_by_stage[stage][cause] = causes_by_stage[stage].get(cause, 0) + 1
    bottleneck = None
    dominant_cause = None
    if by_stage:
        bottleneck = min(
            sorted(by_stage),
            key=lambda stage: (-by_stage[stage], _stage_rank(stage)))
        causes_here = causes_by_stage.get(bottleneck) or {}
        if causes_here:
            dominant_cause = max(sorted(causes_here),
                                 key=lambda cause: causes_here[cause])
    unknown = sum(count for cause, count in by_cause.items()
                  if cause == "unknown.cause")
    return {
        "schema": "ren-editorial-failure-causes/1",
        "failed_tasks": len(rows),
        "by_stage": dict(sorted(by_stage.items())),
        "by_cause": dict(sorted(by_cause.items())),
        "bottleneck": bottleneck,
        "dominant_cause": dominant_cause,
        "dominant_cause_summary": (
            CAUSES[dominant_cause]["summary"] if dominant_cause else None),
        "unsafe_failures": sum(
            1 for row in rows if row.get("severity") == "unsafe"),
        "unattributed": unknown,
    }

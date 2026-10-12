"""Intent-linked quality findings for a finished assignment render.

Deterministic measurements and subjective viewer observations are separate
records. A report is also the small shared payload the assignment review
contract can carry into a creator-approved revision.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DETERMINISTIC = "deterministic"
SUBJECTIVE = "subjective"
# A finding is auto-repairable only when the fix is mechanical and the
# target is declared, never taste: caption geometry against the measured
# safe area, and loudness against the delivery spec's own target (the
# repair normalizes to that number and records the prior gain as its
# inverse, the same mastering `delivery_normalize` plans). Duration and
# black frames stay out: an over-long render asks what to cut and a black
# stretch asks which beat declared it, and both answers belong to the
# creator on the review page, never to a repair.
AUTO_REPAIRABLE = frozenset({"caption_legibility", "caption_obscuring",
                             "lufs"})
GATING_METRICS = frozenset({"duration", "black_frames", "loudness"})
DEFAULT_REPAIR_BUDGET = 1


class CreativeQAError(ValueError):
    """A finding or repair does not satisfy the QA contract."""


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    label: str = ""

    def as_dict(self) -> dict:
        if self.start < 0 or self.end <= self.start:
            raise CreativeQAError("finding span must have positive duration")
        return {"start": float(self.start), "end": float(self.end),
                "label": self.label}


@dataclass(frozen=True)
class Finding:
    id: str
    kind: str
    metric: str
    passed: bool
    severity: str
    detail: str
    span: Mapping[str, Any]
    intent: Mapping[str, Any]
    action: str
    repair_class: str | None = None
    reversible: bool = False

    def as_dict(self) -> dict:
        if self.kind not in {DETERMINISTIC, SUBJECTIVE}:
            raise CreativeQAError(f"unknown finding kind {self.kind!r}")
        if not self.span or not self.intent or not self.action:
            raise CreativeQAError("each finding needs a span, intent and action")
        try:
            start, end = float(self.span["start"]), float(self.span["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CreativeQAError("finding span needs numeric start and end") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise CreativeQAError("finding span must have positive finite duration")
        if not self.intent.get("key") or not self.intent.get("provenance"):
            raise CreativeQAError("finding intent needs a key and provenance")
        return {"id": self.id, "kind": self.kind, "metric": self.metric,
                "passed": bool(self.passed), "severity": self.severity,
                "detail": self.detail, "span": dict(self.span),
                "intent": dict(self.intent), "action": self.action,
                "repair_class": self.repair_class,
                "reversible": self.reversible}


@dataclass
class Report:
    assignment_id: str
    render_id: str
    findings: list[Finding] = field(default_factory=list)
    repair_budget: int = DEFAULT_REPAIR_BUDGET
    repair_attempts: list[dict] = field(default_factory=list)

    @property
    def deterministic_failures(self) -> list[Finding]:
        return [f for f in self.findings
                if (f.kind == DETERMINISTIC and not f.passed
                    and f.action in {"block_delivery", "repair_once"})]

    @property
    def subjective_observations(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == SUBJECTIVE]

    @property
    def blocks_delivery(self) -> bool:
        return bool(self.deterministic_failures)

    @property
    def needs_creator(self) -> bool:
        return any(not f.passed for f in self.subjective_observations)

    @property
    def passed(self) -> bool:
        return not self.blocks_delivery and not self.needs_creator

    def as_dict(self) -> dict:
        return {"version": 1, "assignment_id": self.assignment_id,
                "render_id": self.render_id,
                "status": ("blocked" if self.blocks_delivery else
                           "creator_review" if self.needs_creator else "clean"),
                "blocks_delivery": self.blocks_delivery,
                "needs_creator": self.needs_creator,
                "repair_budget": self.repair_budget,
                "repair_attempts": list(self.repair_attempts),
                "deterministic": [f.as_dict() for f in self.findings
                                  if f.kind == DETERMINISTIC],
                "subjective": [f.as_dict() for f in self.findings
                               if f.kind == SUBJECTIVE]}


def from_measurements(assignment_id: str, render_id: str,
                      measurements: Sequence[Any], *, duration: float,
                      intent_by_metric: Mapping[str, Mapping[str, Any]],
                      spans_by_metric: Mapping[str, Mapping[str, Any]] | None = None,
                      subjective: Sequence[Mapping[str, Any]] = (),
                      repair_budget: int = DEFAULT_REPAIR_BUDGET) -> Report:
    """Build assignment QA from recorded RenderQAResult-like measurements.

    Intent and spans are explicit inputs: QA never invents a creative target
    or localizes a whole-file measurement to a made-up subspan.
    """
    if not assignment_id or not render_id:
        raise CreativeQAError("assignment_id and render_id are required")
    if repair_budget < 0:
        raise CreativeQAError("repair budget cannot be negative")
    spans_by_metric = spans_by_metric or {}
    whole = Span(0, duration, "full render").as_dict()
    findings = []
    for index, result in enumerate(measurements):
        metric = result.metric
        if metric not in intent_by_metric:
            raise CreativeQAError(f"no declared intent reference for {metric!r}")
        span = spans_by_metric.get(metric, whole)
        if not span:
            raise CreativeQAError(f"no measured span for {metric!r}")
        intent = intent_by_metric[metric]
        failed = not bool(result.passed)
        eligible = metric in AUTO_REPAIRABLE
        findings.append(Finding(
            id=f"{render_id}:{metric}:{index}", kind=DETERMINISTIC,
            metric=metric, passed=bool(result.passed), severity=result.severity,
            detail=result.detail, span=span, intent=intent,
            action=("repair_once" if failed and eligible and repair_budget else
                    "block_delivery" if failed and metric in GATING_METRICS else
                    "report" if failed else "none"),
            repair_class=metric if eligible else None,
            reversible=eligible))
    for index, observation in enumerate(subjective):
        required = {"metric", "detail", "span", "intent"}
        if required - observation.keys():
            raise CreativeQAError(
                f"subjective observation missing {sorted(required-observation.keys())}")
        findings.append(Finding(
            id=f"{render_id}:subjective:{index}", kind=SUBJECTIVE,
            metric=str(observation["metric"]),
            passed=bool(observation.get("passed", False)),
            severity=str(observation.get("severity", "review")),
            detail=str(observation["detail"]), span=observation["span"],
            intent=observation["intent"],
            action="ask_creator" if not observation.get("passed", False) else "none"))
    return Report(assignment_id, render_id, findings, repair_budget)


def from_deliverable(assignment_id: str, render_id: str, video_path: str,
                     spec: Any, *, expected_duration: float,
                     intent_by_metric: Mapping[str, Mapping[str, Any]],
                     spans_by_metric: Mapping[str, Mapping[str, Any]] | None = None,
                     grade_spans: Sequence | None = None,
                     subtitle_data: list[dict] | None = None,
                     spine_blocks: Sequence | None = None,
                     declared_black_beats: list | None = None,
                     render_review: Mapping[str, Any] | None = None,
                     repair_budget: int = DEFAULT_REPAIR_BUDGET) -> Report:
    """Run the established deliverable checks and link their results to intent."""
    from library.tools.deliverable_qc import run_deliverable_qc

    verdict = run_deliverable_qc(
        video_path, spec, expected_duration, grade_spans=grade_spans,
        subtitle_data=subtitle_data, spine_blocks=spine_blocks,
        declared_black_beats=declared_black_beats)
    measurements = [verdict.duration, verdict.black_frames, verdict.loudness,
                    verdict.grade, *(verdict.captions or [])]
    subjective = observations_from_render_review(render_review or {})
    return from_measurements(
        assignment_id, render_id, measurements, duration=expected_duration,
        intent_by_metric=intent_by_metric, spans_by_metric=spans_by_metric,
        subjective=subjective, repair_budget=repair_budget)


def observations_from_render_review(render_review: Mapping[str, Any]) -> list[dict]:
    """Adapt span-grounded model notes; legacy free-text notes stay advisory-only."""
    from library.tools import render_review as review_reader

    return review_reader.subjective_observations(render_review)


def attempt_repair(report: Report, finding_id: str,
                   repair: Callable[[Finding], Mapping[str, Any]]) -> dict:
    """Perform one in-scope measurable repair and retain its inverse receipt."""
    finding = next((f for f in report.findings if f.id == finding_id), None)
    if finding is None:
        raise CreativeQAError("unknown finding")
    if finding.kind != DETERMINISTIC or finding.passed:
        raise CreativeQAError("only a failing deterministic finding is repairable")
    if finding.repair_class not in AUTO_REPAIRABLE or not finding.reversible:
        raise CreativeQAError("editorial findings must be sent to the creator")
    if len(report.repair_attempts) >= report.repair_budget:
        raise CreativeQAError("assignment repair budget is exhausted")
    receipt = dict(repair(finding))
    if not receipt.get("receipt") or not receipt.get("inverse"):
        raise CreativeQAError("a repair must return forward and inverse receipts")
    attempt = {"finding_id": finding.id, "repair_class": finding.repair_class,
               "receipt": receipt["receipt"], "inverse": receipt["inverse"]}
    report.repair_attempts.append(attempt)
    return attempt


def record_repair_measurement(report: Report, finding_id: str,
                              measured_result: Any) -> Finding:
    """Close a repair attempt only after the same deterministic check passes."""
    finding = next((f for f in report.findings if f.id == finding_id), None)
    attempt = next((a for a in report.repair_attempts
                    if a["finding_id"] == finding_id), None)
    if finding is None or attempt is None:
        raise CreativeQAError("finding has no recorded repair attempt")
    if measured_result.metric != finding.metric:
        raise CreativeQAError("repair must be rechecked with the same measurement")
    updated = Finding(
        id=finding.id, kind=finding.kind, metric=finding.metric,
        passed=bool(measured_result.passed), severity=measured_result.severity,
        detail=measured_result.detail, span=finding.span, intent=finding.intent,
        action="none" if measured_result.passed else "block_delivery",
        repair_class=finding.repair_class, reversible=finding.reversible)
    report.findings[report.findings.index(finding)] = updated
    attempt["verification"] = {
        "passed": bool(measured_result.passed), "detail": measured_result.detail}
    return updated

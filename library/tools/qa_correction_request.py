"""Turn QA findings and candidate edits into bounded typed corrections.

This adapter uses the existing ``EditRequest`` operation contract. It never
invents an edit from prose: callers supply candidate requests planned against
the current snapshot, and the adapter picks a unique minimum-scope candidate
whose measured timeline span intersects the finding. Missing or ambiguous
candidates remain explicit requests for planning or creator review.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from library.tools.edit_request import EditRequest, compile_request


@dataclass(frozen=True)
class CorrectionRequest:
    """A QA issue and its smallest unambiguous typed timeline proposal."""

    id: str
    finding_id: str
    metric: str
    status: str
    finding: Mapping[str, Any]
    selected_edit: Mapping[str, Any] | None = None
    candidates: tuple[Mapping[str, Any], ...] = ()
    reason: str = ""
    next_action: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "finding_id": self.finding_id,
            "metric": self.metric,
            "status": self.status,
            "finding": dict(self.finding),
            "selected_edit": (dict(self.selected_edit)
                              if self.selected_edit is not None else None),
            "candidates": [dict(row) for row in self.candidates],
            "reason": self.reason,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class _Candidate:
    request: EditRequest
    spans: tuple[tuple[int, int], ...]
    changed_frames: int

    @property
    def rank(self) -> tuple[int, int]:
        return self.changed_frames, len(self.request.operations)

    def as_dict(self) -> dict:
        return {
            "request": self.request.to_dict(),
            "affected_spans": [list(span) for span in self.spans],
            "changed_frames": self.changed_frames,
            "operation_count": len(self.request.operations),
        }


def _candidate(request: EditRequest | Mapping[str, Any], base: Mapping[str, Any],
               finding_span: Mapping[str, Any], fps: float,
               first_frame: int) -> _Candidate | None:
    req = request if isinstance(request, EditRequest) else EditRequest.from_dict(request)
    if not req.operations or any(not op.expected_effect.strip()
                                 for op in req.operations):
        return None
    compiled = compile_request(req, base)
    spans = compiled.affected_spans
    if not spans:
        return None
    start = int(first_frame + float(finding_span["start"]) * fps)
    end = int(first_frame + float(finding_span["end"]) * fps + 0.999999)
    if not any(a < end and b > start for a, b in spans):
        return None
    return _Candidate(req, spans, sum(b - a for a, b in spans))


def plan_requests(findings: Sequence[Any], *,
                  candidates_by_finding: Mapping[str, Sequence[Any]] | None = None,
                  base_snapshot: Mapping[str, Any] | None = None,
                  fps: float | None = None,
                  first_frame: int | None = None) -> list[CorrectionRequest]:
    """Create one correction request per failing finding.

    Candidate requests are ranked by total affected frames, then operation
    count. An equal minimum is ambiguous and is escalated rather than picked
    by author order. Every candidate is compiled against the same read-only
    base snapshot, so the selected span is derived from the edit algebra.
    """
    candidates_by_finding = candidates_by_finding or {}
    out = []
    for finding in findings:
        if finding.passed:
            continue
        is_subjective = finding.kind == "subjective"
        available = list(candidates_by_finding.get(finding.id, ()))
        eligible: list[_Candidate] = []
        rejected = 0
        usable_clock = (isinstance(fps, (int, float))
                       and math.isfinite(float(fps)) and float(fps) > 0)
        if (available and base_snapshot is not None and usable_clock
                and first_frame is not None):
            for request in available:
                try:
                    candidate = _candidate(
                        request, base_snapshot, finding.span, float(fps),
                        int(first_frame))
                except (KeyError, TypeError, ValueError):
                    candidate = None
                if candidate is None:
                    rejected += 1
                else:
                    eligible.append(candidate)
        if is_subjective:
            status = "creator_review"
            selected = None
            reason = "subjective findings require a creator's editorial decision"
            action = "ask_creator"
        elif not eligible:
            status = "needs_plan"
            selected = None
            reason = (
                "no candidate EditRequest was compiled against a base snapshot"
                if not available or base_snapshot is None or not usable_clock
                else f"no candidate edit intersects the finding span ({rejected} rejected)"
            )
            action = "plan_smallest_qualified_edit"
        else:
            eligible.sort(key=lambda item: item.rank)
            smallest = eligible[0]
            ties = [item for item in eligible if item.rank == smallest.rank]
            if len(ties) != 1:
                status = "ambiguous"
                selected = None
                reason = "multiple candidate edits share the smallest measured scope"
                action = "ask_creator_or_planner"
            else:
                status = "planned"
                selected = smallest.as_dict()
                reason = "selected the unique candidate with the smallest affected span"
                action = "submit_through_axi_after_authorization"
        out.append(CorrectionRequest(
            id=f"correction:{finding.id}", finding_id=finding.id,
            metric=finding.metric, status=status, finding=finding.as_dict(),
            selected_edit=selected,
            candidates=tuple(item.as_dict() for item in eligible),
            reason=reason, next_action=action))
    return out

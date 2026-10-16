"""Objective technical rejection of unsuitable source intervals.

EDIT-011 uses objective measurements to reject technically unsuitable
shots when instructed. It combines the measurements the pipeline
already produces - soft-picture runs (`picture_quality.soft_picture_ranges`)
and camera-stability verdicts (`camera_stability.read_camera_stability`) -
into one per-interval verdict with a named reason and evidence ids.

This module measures nothing itself: `from_soft_ranges` and
`from_stability_label` convert the existing verdict shapes into reject
reasons, and `reject_intervals` combines them. A rejection here is a
TECHNICAL verdict (soft, unstable, unmeasured), never an editorial one:
nothing in this file says whether a shot is good, only whether its
signal is technically suitable. The standing rule holds throughout -
technical checks are never called editorial quality.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

SOFT_REASON = "soft_picture"
UNSTABLE_REASON = "unstable_camera"
UNMEASURED_REASON = "unmeasured_interval"


def from_soft_ranges(ranges: Sequence[Mapping[str, Any]] | None,
                     evidence_id: str = "") -> list[dict[str, Any]]:
    """Reject reasons from `soft_picture_ranges` output.

    `None` (unmeasurable) yields no reason: an unmeasured interval is
    reported by `reject_intervals`, not converted into softness here.
    `[]` (measured, nothing soft) likewise yields nothing.
    """
    if not ranges:
        return []
    reasons: list[dict[str, Any]] = []
    for span in ranges:
        reasons.append({
            "reason": span.get("reason", SOFT_REASON),
            "start_seconds": float(span["start"]),
            "end_seconds": float(span["end"]),
            "evidence_id": evidence_id,
        })
    return reasons


def from_stability_label(label: str, method: str,
                         clip_start: float = 0.0,
                         clip_end: float = 0.0,
                         evidence_id: str = "") -> list[dict[str, Any]]:
    """Reject reasons from a `read_camera_stability` verdict.

    Only `unstable` rejects. `handheld` is a description, not a defect;
    `unknown`/`unmeasured` abstain from judging rather than rejecting.
    The reason spans the whole clip because the verdict is per-clip.
    """
    if label != "unstable":
        return []
    return [{"reason": f"{UNSTABLE_REASON}:{method}",
             "start_seconds": float(clip_start),
             "end_seconds": float(clip_end),
             "evidence_id": evidence_id}]


def reject_intervals(
        intervals: Sequence[Mapping[str, Any]],
        reject_reasons: Sequence[Mapping[str, Any]],
        measured_coverage: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Per-interval technical verdicts: keep, reject, or unmeasured.

    `intervals` carry `interval_id`, `start_seconds`, `end_seconds`.
    A reason overlapping an interval rejects it with the reason and the
    evidence id. An interval with no overlapping reason is `keep` when
    `measured_coverage` is empty (the caller asserts full measurement)
    or when a coverage span contains it, and `unmeasured` otherwise.
    Unmeasured is distinct from keep: a shot nobody measured is not a
    shot that passed.
    """
    verdicts: list[dict[str, Any]] = []
    for interval in intervals:
        start = float(interval["start_seconds"])
        end = float(interval["end_seconds"])
        hits = [reason for reason in reject_reasons
                if float(reason["end_seconds"]) > start
                and float(reason["start_seconds"]) < end]
        if hits:
            verdicts.append({
                "interval_id": interval.get("interval_id"),
                "verdict": "reject",
                "reasons": [hit["reason"] for hit in hits],
                "evidence_ids": sorted({
                    hit.get("evidence_id", "") for hit in hits
                    if hit.get("evidence_id")}),
                "start_seconds": start, "end_seconds": end,
            })
            continue
        if not measured_coverage:
            verdicts.append({
                "interval_id": interval.get("interval_id"),
                "verdict": "keep", "reasons": [], "evidence_ids": [],
                "start_seconds": start, "end_seconds": end})
            continue
        covered = any(float(span.get("start_seconds", span.get("start", 0.0)))
                      <= start
                      and end <= float(span.get("end_seconds",
                                                span.get("end", 0.0)))
                      for span in measured_coverage)
        verdicts.append({
            "interval_id": interval.get("interval_id"),
            "verdict": "keep" if covered else "unmeasured",
            "reasons": [] if covered else [UNMEASURED_REASON],
            "evidence_ids": [],
            "start_seconds": start, "end_seconds": end})
    return verdicts

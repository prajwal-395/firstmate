"""Plan request- and activity-driven audio analysis.

The planner never treats an unmeasured interval as quiet. It returns the
work that can be justified by requests or measured activity, the coverage
those decisions provide, and the areas that remain unresolved. An exhaustive
plan is available as a control for measuring the adaptive plan's coverage
and work reduction.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1
LANES = ("transcript", "diarization", "acoustic", "audio_classification")


def _interval(row: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        start, end = float(row["start"]), float(row["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        return None
    return start, end


def _union(intervals: Sequence[tuple[float, float]], duration: float) -> list[dict[str, float]]:
    clipped = sorted((max(0.0, start), min(duration, end))
                     for start, end in intervals
                     if min(duration, end) > max(0.0, start))
    merged: list[list[float]] = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start": round(start, 3), "end": round(end, 3)}
            for start, end in merged]


def _covered(interval: tuple[float, float], coverage: Sequence[Mapping[str, float]]) -> float:
    start, end = interval
    overlaps = sorted((max(start, float(row["start"])),
                       min(end, float(row["end"])))
                      for row in coverage
                      if min(end, float(row["end"])) > max(start, float(row["start"])))
    total = 0.0
    rightmost = start
    for left, right in overlaps:
        total += max(0.0, right - max(left, rightmost))
        rightmost = max(rightmost, right)
    return min(1.0, total / (end - start))


def plan_audio_analysis(
    duration_seconds: float,
    *,
    requests: Sequence[Mapping[str, Any]] = (),
    activity: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    activity_coverage: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    padding_seconds: float = 0.0,
) -> dict[str, Any]:
    """Plan per-lane source intervals from requests and measured activity.

    A request has ``lane`` and may have ``start``/``end``. A lane request
    without bounds applies to the full source. Activity rows are intervals
    already measured by an upstream producer. ``activity_coverage`` states
    where that producer actually measured, including measured absence.
    """
    duration = float(duration_seconds)
    padding = float(padding_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration_seconds must be finite and positive")
    if not math.isfinite(padding) or padding < 0:
        raise ValueError("padding_seconds must be finite and non-negative")

    requested: dict[str, list[tuple[float, float]]] = {lane: [] for lane in LANES}
    invalid_requests = []
    for index, request in enumerate(requests):
        lane = request.get("lane")
        if lane not in requested:
            invalid_requests.append({"index": index, "reason": f"unknown lane {lane!r}"})
            continue
        if "start" not in request and "end" not in request:
            requested[lane].append((0.0, duration))
            continue
        interval = _interval(request)
        if interval is None:
            invalid_requests.append({"index": index, "reason": "invalid request interval"})
        else:
            requested[lane].append(interval)

    activity = activity or {}
    activity_coverage = activity_coverage or {}
    lanes = {}
    for lane in LANES:
        request_spans = _union(requested[lane], duration)
        activity_spans = []
        for row in activity.get(lane, ()):
            span = _interval(row)
            if span is not None:
                activity_spans.append(span)
        scheduled = _union(
            [(max(0.0, start - padding), min(duration, end + padding))
             for span in [*requested[lane], *activity_spans]
             for start, end in [span]], duration)
        measured_spans = []
        for row in activity_coverage.get(lane, ()):
            span = _interval(row)
            if span is not None:
                measured_spans.append(span)
        # Requests define work intent; activity coverage defines what the
        # activity detector ruled in or out. Neither fabricates coverage.
        known = _union([*requested[lane], *measured_spans], duration)
        unresolved = []
        cursor = 0.0
        for row in known:
            if cursor < row["start"]:
                unresolved.append({"start": round(cursor, 3), "end": row["start"],
                                   "reason": "no request or measured activity coverage"})
            cursor = max(cursor, row["end"])
        if cursor < duration:
            unresolved.append({"start": round(cursor, 3), "end": round(duration, 3),
                               "reason": "no request or measured activity coverage"})
        requested_seconds = sum(end - start for start, end in requested[lane])
        scheduled_seconds = sum(row["end"] - row["start"] for row in scheduled)
        coverage_seconds = sum(row["end"] - row["start"] for row in known)
        lanes[lane] = {
            "scheduled": scheduled,
            "requested": request_spans,
            "activity": _union(activity_spans, duration),
            "coverage": known,
            "coverage_seconds": round(coverage_seconds, 3),
            "coverage_fraction": round(coverage_seconds / duration, 6),
            "unresolved": unresolved,
            "requested_seconds": round(min(duration, requested_seconds), 3),
            "scheduled_seconds": round(scheduled_seconds, 3),
        }

    return {"schema_version": SCHEMA_VERSION,
            "method": "request-activity-audio-scheduler-v1",
            "duration_seconds": round(duration, 3),
            "lanes": lanes,
            "invalid_requests": invalid_requests,
            "unresolved_areas": [
                {"lane": lane, **area}
                for lane in LANES for area in lanes[lane]["unresolved"]],
            "scheduled_seconds": round(sum(row["scheduled_seconds"]
                                             for row in lanes.values()), 3)}


def compare_exhaustive_control(adaptive: Mapping[str, Any]) -> dict[str, Any]:
    """Compare adaptive work with a full-source control for the same lanes.

    Requested and activity-supported spans are the required evidence set.
    Recall is measured over that set; coverage outside it remains reported
    as unresolved rather than being scored as a miss or as silence.
    """
    duration = float(adaptive["duration_seconds"])
    lanes = adaptive["lanes"]
    rows = {}
    for lane in LANES:
        detail = lanes[lane]
        required = _union(
            [*[(row["start"], row["end"]) for row in detail["requested"]],
             *[(row["start"], row["end"]) for row in detail["activity"]]],
            duration)
        required_seconds = sum(row["end"] - row["start"] for row in required)
        recalled = sum((row["end"] - row["start"])
                       * _covered((row["start"], row["end"]), detail["scheduled"])
                       for row in required)
        adaptive_cost = float(detail["scheduled_seconds"])
        rows[lane] = {
            "control_seconds": round(duration, 3),
            "adaptive_seconds": round(adaptive_cost, 3),
            "saved_seconds": round(max(0.0, duration - adaptive_cost), 3),
            "required_seconds": round(required_seconds, 3),
            "required_coverage_fraction": (round(recalled / required_seconds, 6)
                                            if required_seconds else None),
            "exhaustive_control_coverage_fraction": 1.0,
        }
    control_total = duration * len(LANES)
    adaptive_total = float(adaptive["scheduled_seconds"])
    return {"method": "adaptive-vs-exhaustive-control-v1",
            "control_seconds": round(control_total, 3),
            "adaptive_seconds": round(adaptive_total, 3),
            "saved_seconds": round(max(0.0, control_total - adaptive_total), 3),
            "work_reduction_fraction": round(max(0.0, control_total - adaptive_total)
                                              / control_total, 6),
            "lanes": rows}


def execute_audio_plan(plan: Mapping[str, Any], analyzers: Mapping[str, Any]) -> dict[str, Any]:
    """Run planned lane callbacks serially and record per-window outcomes.

    Each callback receives ``start`` and ``end`` in source seconds. Serial
    execution keeps model work within the one-source memory ceiling. A missing
    callback or a failed window is retained as an unresolved area.
    """
    duration = float(plan["duration_seconds"])
    lane_results = {}
    unresolved = list(plan["unresolved_areas"])
    for lane in LANES:
        callback = analyzers.get(lane)
        completed = []
        for window in plan["lanes"][lane]["scheduled"]:
            if callback is None:
                unresolved.append({"lane": lane, "start": window["start"],
                                   "end": window["end"],
                                   "reason": "analyzer unavailable"})
                continue
            try:
                value = callback(start=window["start"], end=window["end"])
            except Exception as exc:  # noqa: BLE001 - retain model failures as unresolved work
                unresolved.append({"lane": lane, "start": window["start"],
                                   "end": window["end"],
                                   "reason": f"analysis failed: {type(exc).__name__}: {exc}"})
                continue
            completed.append({"start": window["start"], "end": window["end"],
                              "status": "measured", "result": value})
        lane_results[lane] = {"completed": completed,
                              "scheduled_seconds": plan["lanes"][lane]["scheduled_seconds"],
                              "coverage": _union(
                                  [(row["start"], row["end"]) for row in completed],
                                  duration),
                              "coverage_fraction": round(
                                  sum(row["end"] - row["start"] for row in _union(
                                      [(item["start"], item["end"])
                                       for item in completed], duration)) / duration, 6),
                              "unresolved": [row for row in unresolved
                                             if row.get("lane") == lane]}
    return {"schema_version": SCHEMA_VERSION,
            "method": "request-activity-audio-execution-v1",
            "duration_seconds": duration,
            "lanes": lane_results,
            "unresolved_areas": unresolved,
            "exhaustive_control": compare_exhaustive_control(plan)}

"""Join existing temporal evidence into a coverage-aware activity map.

This module is an aggregation layer. Motion and novelty values come from
FI-14 and audio values from FI-12; it does not decode media or recompute
either measurement. Every lane retains its own coverage and local baseline.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1
LANES = ("novelty", "motion", "scene", "audio", "speech", "uncertainty")


def _interval(row: Mapping[str, Any], start_key: str, end_key: str) -> tuple[float, float] | None:
    try:
        start, end = float(row[start_key]), float(row[end_key])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return None
    return start, end


def _overlap_fraction(start: float, end: float, intervals: Sequence[tuple[float, float]]) -> float:
    clipped = sorted((max(start, left), min(end, right)) for left, right in intervals
                     if min(end, right) > max(start, left))
    if not clipped:
        return 0.0
    covered = 0.0
    left, right = clipped[0]
    for next_left, next_right in clipped[1:]:
        if next_left <= right:
            right = max(right, next_right)
        else:
            covered += right - left
            left, right = next_left, next_right
    covered += right - left
    return min(1.0, covered / (end - start))


def _local_readings(values: list[float | None], radius: int) -> list[dict[str, float | None]]:
    result = []
    for index, value in enumerate(values):
        neighbors = [v for j, v in enumerate(values)
                     if v is not None and abs(j - index) <= radius]
        if value is None or not neighbors:
            result.append({"value": None, "baseline": None, "normalized": None})
            continue
        ordered = sorted(neighbors)
        baseline = ordered[(len(ordered) - 1) // 2]
        deviations = sorted(abs(v - baseline) for v in neighbors)
        mad = deviations[(len(deviations) - 1) // 2]
        # A robust signed deviation, bounded for stable comparison across lanes.
        scale = max(1e-9, 1.4826 * mad)
        z = (value - baseline) / scale
        normalized = 0.5 + 0.5 * z / (1.0 + abs(z))
        result.append({"value": round(value, 6), "baseline": round(baseline, 6),
                       "normalized": round(min(1.0, max(0.0, normalized)), 6)})
    return result


def build_content_activity_map(
    duration_seconds: float,
    *,
    motion_evidence: Mapping[str, Any] | None = None,
    audio_quality: Mapping[str, Any] | None = None,
    scene_evidence: Mapping[str, Any] | None = None,
    speech_evidence: Mapping[str, Any] | None = None,
    uncertainty_evidence: Mapping[str, Any] | None = None,
    bin_seconds: float = 1.0,
    baseline_radius_bins: int = 5,
) -> dict[str, Any]:
    """Build independent normalized signals over source-time bins.

    Evidence rows use source-second ``start``/``end`` intervals. Inputs may
    include a ``coverage`` list of measured intervals; only that declared
    coverage makes empty time a measured quiet interval. Missing coverage
    therefore yields ``value=None`` and ``normalized=None``.
    """
    duration = float(duration_seconds)
    step = float(bin_seconds)
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(step) or step <= 0:
        raise ValueError("duration_seconds and bin_seconds must be finite and positive")
    if baseline_radius_bins < 0:
        raise ValueError("baseline_radius_bins must be non-negative")

    bins = []
    cursor = 0.0
    while cursor < duration:
        end = min(duration, cursor + step)
        bins.append({"start": round(cursor, 6), "end": round(end, 6)})
        cursor = end

    # Convert upstream records to raw scalar readings while preserving each
    # producer's actual coverage. Unsupported decomposition is not zero motion.
    lane_inputs: dict[str, tuple[list[tuple[float, float, float]], list[tuple[float, float]]]] = {}

    motion_rows = list((motion_evidence or {}).get("pairs", []))
    motion_values, motion_coverage, novelty_values, novelty_coverage = [], [], [], []
    for row in motion_rows:
        span = _interval(row, "start", "end")
        if span is None:
            continue
        if row.get("status") == "measured":
            if isinstance(row.get("change"), (int, float)):
                motion_values.append((*span, float(row["change"])))
                motion_coverage.append(span)
            if isinstance(row.get("novelty"), (int, float)):
                novelty_values.append((*span, float(row["novelty"])))
                novelty_coverage.append(span)
    lane_inputs["motion"] = (motion_values, motion_coverage)
    lane_inputs["novelty"] = (novelty_values, novelty_coverage)

    audio_values, audio_coverage = [], []
    for channel in (audio_quality or {}).get("channels", []):
        for row in channel.get("intervals", []):
            span = _interval(row, "start_seconds", "end_seconds")
            dbfs = row.get("rms_dbfs")
            if span and isinstance(dbfs, (int, float)):
                # Fixed physical reference gives comparable activity across files.
                audio_values.append((*span, min(1.0, max(0.0, (float(dbfs) + 60.0) / 60.0))))
                audio_coverage.append(span)
    lane_inputs["audio"] = (audio_values, audio_coverage)

    for name, evidence in (("scene", scene_evidence), ("speech", speech_evidence),
                           ("uncertainty", uncertainty_evidence)):
        rows = list((evidence or {}).get("intervals", []))
        coverage_rows = list((evidence or {}).get("coverage", []))
        values = []
        for row in rows:
            span = _interval(row, "start", "end")
            value = row.get("value")
            if span and isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append((*span, min(1.0, max(0.0, float(value)))))
        coverage = [span for row in coverage_rows if (span := _interval(row, "start", "end"))]
        # A valid measurement row itself establishes coverage; explicit coverage
        # can additionally establish measured absence (for example no speech).
        coverage.extend((start, end) for start, end, _ in values)
        lane_inputs[name] = (values, coverage)

    lane_series: dict[str, list[float | None]] = {}
    lane_records = {}
    for name in LANES[:-1]:
        values, coverage = lane_inputs[name]
        raw = []
        fractions = []
        for item in bins:
            start, end = item["start"], item["end"]
            fraction = _overlap_fraction(start, end, coverage)
            fractions.append(fraction)
            overlapping = [(right - left, value) for left, right, value in values
                           if min(end, right) > max(start, left)]
            raw.append(sum(weight * value for weight, value in overlapping) /
                       sum(weight for weight, _ in overlapping) if overlapping else None)
        lane_series[name] = raw
        readings = _local_readings(raw, baseline_radius_bins)
        lane_records[name] = [{**reading, "coverage_fraction": round(fraction, 6),
                               "coverage_status": "measured" if fraction >= 1.0 else
                               "partial" if fraction > 0 else "not_analyzed"}
                              for reading, fraction in zip(readings, fractions)]

    # Uncertainty is explicit and high when evidence lanes are absent or partial;
    # supplied ambiguity measurements can only increase it.
    uncertainty = []
    for index, item in enumerate(bins):
        fractions = [lane_records[name][index]["coverage_fraction"] for name in LANES[:-1]]
        measured_uncertainty = 1.0 - sum(fractions) / len(fractions)
        supplied = lane_series.get("uncertainty", [None] * len(bins))[index]
        uncertainty.append(max(measured_uncertainty, supplied or 0.0))
    unc_readings = _local_readings(uncertainty, baseline_radius_bins)
    lane_records["uncertainty"] = [{**reading, "coverage_fraction": 1.0,
                                     "coverage_status": "derived"}
                                    for reading in unc_readings]
    for index, item in enumerate(bins):
        item["signals"] = {name: lane_records[name][index] for name in LANES}

    return {"schema_version": SCHEMA_VERSION, "method": "content-activity-map-v1",
            "duration_seconds": duration, "bin_seconds": step,
            "baseline_radius_bins": baseline_radius_bins, "bins": bins,
            "coverage_semantics": "not_analyzed is null, never quiet activity"}

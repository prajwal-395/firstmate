"""Deterministic, evidence-aware scheduling for semantic video windows.

The planner chooses where to spend VLM calls. It does not infer events from
activity and never turns unsampled time into semantic evidence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from library.tools.content_activity import build_content_activity_map

SCHEMA_VERSION = 1
TASK_WEIGHTS = {
    "general": {"novelty": 1.0, "motion": 0.7, "scene": 0.8,
                "audio": 0.4, "speech": 0.6, "uncertainty": 1.0},
    "actions": {"novelty": 0.8, "motion": 1.0, "scene": 0.4,
                "audio": 0.3, "speech": 0.7, "uncertainty": 1.0},
    "scene": {"novelty": 1.0, "motion": 0.3, "scene": 1.0,
              "audio": 0.2, "speech": 0.2, "uncertainty": 1.0},
    "speech_visual": {"novelty": 0.5, "motion": 0.3, "scene": 0.3,
                      "audio": 0.8, "speech": 1.0, "uncertainty": 1.0},
}


def activity_map_from_temporal_index(duration_seconds: float,
                                     temporal_index: Mapping[str, Any] | None,
                                     *, bin_seconds: float = 1.0) -> dict[str, Any]:
    """Adapt existing temporal measurements without filling their gaps."""
    index = temporal_index or {}
    motion = index.get("motion_evidence")
    if not isinstance(motion, Mapping):
        motion = None
    if motion is None:
        # The current temporal-index producer exposes its measured local
        # change as motion_energy, and its coarse optical-flow magnitude as
        # a second visual-change signal. Adapt those published fields into
        # the activity-map contract rather than treating them as gaps.
        energy = index.get("motion_energy") or {}
        flow = index.get("optical_flow_direction") or {}
        energy_values = energy.get("values", []) if isinstance(energy, Mapping) else []
        flow_values = flow.get("values", []) if isinstance(flow, Mapping) else []
        energy_rate = energy.get("sample_rate_hz") if isinstance(energy, Mapping) else None
        flow_rate = flow.get("sample_rate_hz") if isinstance(flow, Mapping) else None
        rows = []
        for values, rate, key in ((energy_values, energy_rate, "change"),
                                  (flow_values, flow_rate, "novelty")):
            if isinstance(rate, (int, float)) and rate > 0 and isinstance(values, list):
                for i, value in enumerate(values):
                    number = (value if isinstance(value, (int, float)) else
                              value.get("magnitude") if isinstance(value, Mapping) else None)
                    if isinstance(number, (int, float)) and math.isfinite(float(number)):
                        rows.append({"start": i / rate, "end": (i + 1) / rate,
                                     key: float(number), "status": "measured"})
        if rows:
            # Pair by sample index on a common source-time grid. Each lane's
            # own values are retained while absent partners remain missing.
            merged = {}
            for row in rows:
                key = (row["start"], row["end"])
                merged.setdefault(key, {"start": row["start"], "end": row["end"],
                                         "status": "measured"})
                if "change" in row:
                    merged[key]["change"] = row["change"]
                if "novelty" in row:
                    merged[key]["novelty"] = row["novelty"]
            motion = {"pairs": list(merged.values())}
    speech_rows = []
    for row in index.get("speech_regions", []):
        if isinstance(row, Mapping):
            speech_rows.append({"start": row["start"], "end": row["end"],
                                "value": 1.0})
    # Boundaries are event-like evidence, not continuous scene coverage.
    scene_rows = []
    for row in index.get("scene_boundaries", []):
        if not isinstance(row, Mapping):
            continue
        at = float(row.get("timestamp", row.get("time", -1)))
        if 0 <= at < duration_seconds:
            scene_rows.append({"start": at, "end": min(duration_seconds, at + bin_seconds),
                               "value": 1.0})
    activity = build_content_activity_map(
        duration_seconds, motion_evidence=motion,
        scene_evidence={"intervals": scene_rows},
        speech_evidence={"intervals": speech_rows},
        bin_seconds=bin_seconds)
    energy = index.get("energy_curve")
    values = energy.get("values", []) if isinstance(energy, Mapping) else []
    rate = energy.get("sample_rate_hz") if isinstance(energy, Mapping) else None
    if isinstance(rate, (int, float)) and rate > 0 and isinstance(values, list):
        for row in activity["bins"]:
            first = max(0, math.floor(float(row["start"]) * rate))
            last = min(len(values), math.ceil(float(row["end"]) * rate))
            samples = [float(value) for value in values[first:last]
                       if isinstance(value, (int, float))
                       and math.isfinite(float(value))]
            if samples:
                value = sum(samples) / len(samples)
                row["signals"]["audio"] = {
                    "value": round(value, 6), "baseline": None,
                    "normalized": round(min(1.0, max(0.0, value)), 6),
                    "coverage_fraction": 1.0, "coverage_status": "measured",
                }
                measured_fraction = sum(
                    float(row["signals"][lane]["coverage_fraction"])
                    for lane in ("novelty", "motion", "scene", "audio", "speech")) / 5
                uncertainty = max(0.0, 1.0 - measured_fraction)
                row["signals"]["uncertainty"].update({
                    "value": round(uncertainty, 6),
                    "baseline": round(uncertainty, 6),
                    "normalized": round(uncertainty, 6),
                })
    if values:
        activity["audio_activity_source"] = (
            "temporal_index.energy_curve normalized per source; not absolute loudness")
    return activity


def plan_semantic_windows(duration_seconds: float, activity_map: Mapping[str, Any] | None,
                          *, task: str = "general", window_seconds: float = 10.0,
                          max_fraction: float = 0.65,
                          coverage_stride_seconds: float = 30.0,
                          refinement_limit: int = 12,
                          minimum_window_seconds: float = 0.5) -> dict[str, Any]:
    """Select deterministic starts, retaining a uniform discovery baseline.

    Coverage is the union of selected windows. Refinement candidates are
    uncovered high-priority or unmeasured bins; they are requests to inspect,
    not claims that an event exists there.
    """
    duration, width = float(duration_seconds), float(window_seconds)
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(width) or width <= 0:
        raise ValueError("duration_seconds and window_seconds must be finite and positive")
    if task not in TASK_WEIGHTS:
        raise ValueError(f"unsupported semantic task: {task}")
    if not 0 < max_fraction <= 1 or coverage_stride_seconds < width:
        raise ValueError("max_fraction must be in (0, 1] and coverage stride >= window size")
    minimum = float(minimum_window_seconds)
    if not math.isfinite(minimum) or minimum <= 0 or minimum > width:
        raise ValueError("minimum_window_seconds must be positive and <= window_seconds")
    full_windows = int(duration // width)
    remainder = duration - full_windows * width
    control_starts = [round(i * width, 6) for i in range(full_windows)]
    if remainder >= minimum:
        control_starts.append(round(full_windows * width, 6))
    exhaustive_count = len(control_starts)
    bins = list((activity_map or {}).get("bins", []))
    weights = TASK_WEIGHTS[task]
    scored: list[tuple[float, float, float, bool]] = []
    for row in bins:
        start, end = float(row["start"]), float(row["end"])
        signals = row.get("signals", {})
        present = []
        for lane, weight in weights.items():
            signal = signals.get(lane)
            if not isinstance(signal, Mapping):
                continue
            value = signal.get("normalized")
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                present.append(float(value) * weight)
        uncertainty = signals.get("uncertainty", {})
        is_unknown = any(
            isinstance(signals.get(lane), Mapping)
            and signals[lane].get("coverage_status") in ("not_analyzed", "partial")
            for lane in weights if lane != "uncertainty")
        score = sum(present) / sum(weights[lane] for lane in weights
                                  if isinstance(signals.get(lane), Mapping)
                                  and isinstance(signals[lane].get("normalized"), (int, float))) \
            if present else 0.0
        if isinstance(uncertainty, Mapping) and isinstance(uncertainty.get("value"), (int, float)):
            score = max(score, float(uncertainty["value"]) * weights["uncertainty"])
        scored.append((start, end, score, is_unknown))

    # Uniformly spaced starts form the explicit discovery floor.
    starts = {0.0}
    cursor = coverage_stride_seconds
    while cursor < duration:
        starts.add(round(cursor, 6))
        cursor += coverage_stride_seconds
    starts.add(round(max(0.0, duration - width), 6))
    cap = max(len(starts), min(exhaustive_count,
                               math.ceil(exhaustive_count * max_fraction)))
    cap = min(exhaustive_count, cap)
    if exhaustive_count == 0:
        starts = set()

    candidates = []
    for start, end, score, unknown in scored:
        center = (start + end) / 2
        candidate_start = min(max(0.0, center - width / 2), max(0.0, duration - width))
        candidates.append((score, unknown, candidate_start))
    for _, _, start in sorted(candidates, key=lambda row: (-row[0], -int(row[1]), row[2])):
        if len(starts) >= cap:
            break
        if all(abs(start - chosen) >= width for chosen in starts):
            starts.add(round(start, 6))

    chosen = sorted(starts)
    covered = [(start, min(duration, start + width)) for start in chosen]
    uncovered = []
    for start, end, score, unknown in scored:
        if (not any(left < end and right > start for left, right in covered)
                and (unknown or score >= 0.65)):
            uncovered.append({"start": start, "end": end,
                              "priority": round(score, 6),
                              "reason": "unmeasured_activity" if unknown else "activity_peak"})
    # Union coverage, avoiding double-counting adjacent/overlapping windows.
    merged = []
    for left, right in covered:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    covered_seconds = sum(right - left for left, right in merged)
    control_seconds = sum(min(width, duration - start)
                          for start in control_starts)
    estimated_missed_weight = sum(
        score for start, end, score, _ in scored
        if not any(left < end and right > start for left, right in covered))
    return {
        "schema_version": SCHEMA_VERSION, "method": "adaptive-semantic-windows-v1",
        "task": task, "window_seconds": width, "window_starts_s": chosen,
        "expected_cost": {"model_calls": len(chosen),
                          "seconds": round(sum(min(width, duration - start)
                                               for start in chosen), 6)},
        "exhaustive_control": {"model_calls": exhaustive_count,
                               "seconds": round(control_seconds, 6),
                               "window_starts_s": control_starts,
                               "coverage_fraction": round(
                                   min(1.0, control_seconds / duration), 6)},
        "coverage": {"seconds": round(covered_seconds, 6),
                     "fraction": round(min(1.0, covered_seconds / duration), 6),
                     "sampled_bin_fraction": round(
                         sum(any(left < end and right > start for left, right in covered)
                             for start, end, _, _ in scored) / max(1, len(scored)), 6)},
        "refinement_candidates": sorted(uncovered, key=lambda row: (-row["priority"], row["start"]))[:refinement_limit],
        "unseen_event_policy": "no interpolation; semantic claims require analyzed windows",
        "selection_score_uncovered": round(estimated_missed_weight, 6),
    }

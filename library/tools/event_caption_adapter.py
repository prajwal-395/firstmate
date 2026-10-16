"""Adapt Gemma window output to benchmark event records with evidence links.

The incumbent is the folded ``analyze_windows`` pass in
``library/tools/analysis/vision_pipeline_v3.py``: one Gemma call per
10-second window emits an ``actions`` list whose timestamps are already
in source seconds. This module converts those window outputs into the
individual event records the benchmark in ``docs/benchmarks/
event-caption-benchmark-2026-10.md`` scores (SEM-023: one record per
event, never a flattened paragraph; SEM-024: every claim carries
evidence).

Evidence basis (honest limits): the incumbent call does not record the
exact timestamps of the frames it consumed, so frame times are
reconstructed from the deterministic sampling mirror
(``native_sample_plan``: ``NATIVE_VIDEO_DECODE_FPS`` uniform decode,
clamped frame count). The reconstruction is labelled
``reconstructed_uniform_plan`` in every evidence object - it is the
sampling plan the model demonstrably saw, not per-call measured
timestamps. Only frames whose times fall inside the claimed event
interval are attached; an event with no in-span frame keeps an empty
evidence list and scores as an unsupported claim rather than borrowing
frames from outside its span.

This module runs no models, fetches no weights, and changes no
capability defaults.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from library.tools.event_caption_benchmark import FORMAT as EVENT_FORMAT

ADAPTER_REVISION = "event_caption_adapter/1"
MODALITY = "event_caption"

# Must match vision_pipeline_v3; duplicated (not imported) so this
# module never loads the ML stack at import time.
_DECODE_FPS = 2.0


def _is_finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def planned_frame_times(window_start: float, window_end: float,
                        frame_count: int) -> list[float]:
    """Frame times the incumbent sampling plan covers in one window.

    Uniform decode at ``_DECODE_FPS`` from the window start, clipped to
    the window. Mirrors ``native_sample_plan`` without importing it.
    """
    if (not _is_finite_number(window_start)
            or not _is_finite_number(window_end)
            or window_end <= window_start or frame_count <= 0):
        return []
    step = 1.0 / _DECODE_FPS
    times = []
    moment = window_start
    for _ in range(frame_count):
        if moment >= window_end:
            break
        times.append(round(moment, 3))
        moment += step
    return times


def _action_interval(action: dict[str, Any]) -> tuple[float, float] | None:
    start, end = action.get("start"), action.get("end")
    if (not _is_finite_number(start) or not _is_finite_number(end)
            or start < 0 or end <= start):
        return None
    return (float(start), float(end))


def gemma_windows_to_events(
    windows: list[dict[str, Any]],
    *,
    source_id: str,
    model_id: str,
    model_revision: str,
    prompt_id: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert folded Gemma window outputs to scored event records.

    One event per action entry. Scene/camera/assessment sections are
    deliberately NOT converted: they are window prose, not timestamped
    event claims, and converting them would flatten observations the
    benchmark reserves for the event stream.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be a non-empty string")
    events: list[dict[str, Any]] = []
    stats = {"windows": 0, "actions": 0, "dropped_invalid": 0,
             "no_in_span_frame": 0}
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            stats["dropped_invalid"] += 1
            continue
        stats["windows"] += 1
        bounds = window.get("window")
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2):
            stats["dropped_invalid"] += 1
            continue
        window_start, window_end = bounds
        if (not _is_finite_number(window_start)
                or not _is_finite_number(window_end)
                or window_end <= window_start):
            stats["dropped_invalid"] += 1
            continue
        sampling = window.get("sampling") or {}
        frame_count = sampling.get("frames")
        if not isinstance(frame_count, int) or frame_count <= 0:
            # native_sample_plan frame count for a 10 s window at the
            # incumbent decode rate when the record predates sampling.
            frame_count = int((window_end - window_start) * _DECODE_FPS)
        planned = planned_frame_times(float(window_start),
                                      float(window_end), frame_count)
        actions = window.get("actions")
        if actions is None:
            # Absent key means the model never answered (see
            # analyze_windows); it is not an empty observation.
            continue
        if not isinstance(actions, list):
            stats["dropped_invalid"] += 1
            continue
        for ordinal, action in enumerate(actions):
            if not isinstance(action, dict):
                stats["dropped_invalid"] += 1
                continue
            interval = _action_interval(action)
            text = action.get("action")
            if interval is None or not isinstance(text, str) \
                    or not text.strip():
                stats["dropped_invalid"] += 1
                continue
            stats["actions"] += 1
            start, end = interval
            in_span = [t for t in planned if start <= t < end]
            if not in_span:
                stats["no_in_span_frame"] += 1
            event_id = (f"{source_id}:w{index}:a{ordinal}:"
                        f"{hashlib.sha256(text.strip().encode('utf-8')).hexdigest()[:12]}")
            evidence = ([{
                "source_id": source_id,
                "interval": {"start_seconds": start,
                             "end_seconds": end},
                "frame_times_seconds": in_span,
                "sampling": {
                    "window_seconds": [window_start, window_end],
                    "planned_frames": planned,
                    "decode_fps": _DECODE_FPS,
                    "frame_count": frame_count,
                },
                "frame_time_basis": "reconstructed_uniform_plan",
            }] if in_span else [])
            events.append({
                "event_id": event_id,
                "source_id": source_id,
                "interval": {"start_seconds": start, "end_seconds": end},
                "action": text.strip(),
                # The incumbent emits no entity ids; inventing them
                # would fabricate relations the model never stated.
                "entities": [],
                "relations": [],
                "evidence": evidence,
                "provenance": {
                    "method": "model",
                    "model_id": model_id,
                    "revision": model_revision,
                    "prompt_id": prompt_id,
                    "adapter_revision": ADAPTER_REVISION,
                    "window_seconds": [window_start, window_end],
                },
            })
    return events, stats


def build_prediction_doc(
    events: list[dict[str, Any]],
    *,
    adapter_revision: str = ADAPTER_REVISION,
) -> dict[str, Any]:
    """Freeze adapter events as a scorer-ready prediction artifact."""
    return {
        "format": EVENT_FORMAT,
        "kind": "predictions",
        "adapter_revision": adapter_revision,
        "events": events,
    }


def build_evaluation_report(
    *,
    version_id: str,
    role: str,
    models: dict[str, str],
    split: str,
    metrics: dict[str, dict[str, Any]],
    evidence: list[dict[str, str]],
    revision: str,
    selection_basis: list[str],
) -> dict[str, Any]:
    """Emit one frozen ``ren_evaluation_report/1`` for the scorecard CLI.

    Quality metrics are included only when adjudicated agent labels for
    the split exist; otherwise the report carries latency/memory/cost
    alone and the scorecard lists quality as unmeasured. No metric is
    ever synthesized to fill a gap.
    """
    if role not in ("baseline", "candidate", "accepted"):
        raise ValueError(f"role must be baseline/candidate/accepted, got {role!r}")
    if split not in ("training", "calibration", "held_out"):
        raise ValueError(f"split must be a corpus split, got {split!r}")
    if any(part not in ("training", "calibration")
           for part in selection_basis):
        raise ValueError("selection_basis may name training/calibration only")
    return {
        "format": "ren_evaluation_report/1",
        "revision": revision,
        "version": {"id": version_id, "role": role, "models": models},
        "modality": MODALITY,
        "split": split,
        "metrics": metrics,
        "evidence": evidence,
        "selection_basis": selection_basis,
    }


def compare_modes(
    reference_doc: dict[str, Any],
    baseline_doc: dict[str, Any],
    candidate_doc: dict[str, Any],
    *,
    baseline_run: dict[str, Any],
    candidate_run: dict[str, Any],
) -> dict[str, Any]:
    """Compare two frozen generation modes on the same references.

    Used for SEM-021 (sequential vs parallel windows with equivalent
    sampling and input) and SEM-022 (one specialized pass vs the folded
    multi-call baseline). Both prediction docs must cover the same
    sources; run records carry wall latency, peak memory, and the
    sampling/precision each mode actually used. Scoring itself is
    delegated to ``event_caption_benchmark.score_events`` by the
    caller - this helper frames the deltas, it does not re-score.
    """
    from library.tools.event_caption_benchmark import score_events

    baseline_score = score_events(reference_doc, baseline_doc)
    candidate_score = score_events(reference_doc, candidate_doc)
    ref_sources = {e["source_id"] for e in reference_doc["events"]}
    for name, doc in (("baseline", baseline_doc),
                      ("candidate", candidate_doc)):
        got = {e["source_id"] for e in doc["events"]}
        if not got.issubset(ref_sources):
            raise ValueError(f"{name} predictions name sources outside the references")

    def _ratio(key: str) -> float | None:
        base, cand = baseline_run.get(key), candidate_run.get(key)
        if isinstance(base, (int, float)) and isinstance(cand, (int, float)) \
                and base > 0:
            return cand / base
        return None

    delta_keys = ("event_recall", "description_precision",
                  "unsupported_claim_rate", "evidence_coverage",
                  "timestamp_start_mae_seconds",
                  "timestamp_end_mae_seconds", "matched_interval_iou")
    deltas = {}
    for key in delta_keys:
        base, cand = baseline_score.get(key), candidate_score.get(key)
        if isinstance(base, (int, float)) and isinstance(cand, (int, float)):
            deltas[key] = cand - base
        else:
            deltas[key] = None
    return {
        "baseline": {"score": baseline_score, "run": baseline_run},
        "candidate": {"score": candidate_score, "run": candidate_run},
        "score_deltas_candidate_minus_baseline": deltas,
        "latency_ratio_candidate_over_baseline":
            _ratio("wall_seconds"),
        "peak_memory_ratio_candidate_over_baseline":
            _ratio("peak_memory_gb"),
        "selection_note": "report only; this comparison does not rank versions",
    }

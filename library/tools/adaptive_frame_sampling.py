"""Deterministic, source-clock frame selection for targeted visual analysis.

This policy schedules measurements; selected frames are sampled evidence and
never establish continuous coverage or an event's absence.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

POLICY_VERSION = "adaptive-source-frames-v1"
DEFAULT_BUDGET = 48
DEFAULT_DENSE_RADIUS_SECONDS = 1.0


def _number(value: Any, default: float | None = None) -> float | None:
    return (float(value) if isinstance(value, (int, float))
            and math.isfinite(float(value)) else default)


def plan_adaptive_frames(
    duration_seconds: float,
    fps: float,
    temporal_index: Mapping[str, Any] | None = None,
    *,
    activity_map: Mapping[str, Any] | None = None,
    query: str | None = None,
    retrieval_candidates: Sequence[Mapping[str, Any]] = (),
    budget_frames: int = DEFAULT_BUDGET,
    existing_sample_times: Sequence[float] = (),
    dense_radius_seconds: float = DEFAULT_DENSE_RADIUS_SECONDS,
) -> dict[str, Any]:
    """Choose source-frame timestamps using measured activity and retrieval.

    Retrieval candidate rows may carry ``start``, ``end``, ``score`` and
    ``evidence_type``. Timing boundaries are also taken from the temporal
    index (scene cuts, speech regions, OCR changes, and entity transitions).
    Unknown or absent measurements do not become zero activity.
    """
    duration, rate = _number(duration_seconds), _number(fps)
    if duration is None or duration <= 0 or rate is None or rate <= 0:
        raise ValueError("duration and fps must be finite and positive")
    if not isinstance(budget_frames, int) or budget_frames < 0:
        raise ValueError("budget_frames must be a non-negative integer")
    if dense_radius_seconds < 0 or not math.isfinite(dense_radius_seconds):
        raise ValueError("dense_radius_seconds must be finite and non-negative")
    index = temporal_index or {}
    signals: dict[float, set[str]] = {}

    def add(at: float, reason: str) -> None:
        if 0 <= at < duration:
            # Snap to the nearest source frame. Source time remains seconds.
            tick = round(round(at * rate) / rate, 6)
            signals.setdefault(tick, set()).add(reason)

    # Preserve a low-rate discovery floor, then add measured transition points.
    discovery_step = max(1.0, duration / max(1, budget_frames // 4 or 1))
    at = 0.0
    while at < duration:
        add(at, "discovery")
        at += discovery_step
    boundaries: list[tuple[float, str]] = []
    for row in index.get("scene_boundaries", []):
        if isinstance(row, Mapping):
            t = _number(row.get("timestamp", row.get("time")))
            if t is not None:
                boundaries.append((t, "scene_boundary"))
    for row in index.get("speech_regions", []):
        if isinstance(row, Mapping):
            for key in ("start", "end"):
                t = _number(row.get(key))
                if t is not None:
                    boundaries.append((t, "speech_boundary"))
    for field, reason in (("ocr_changes", "text_change"),
                          ("entity_transitions", "entity_transition"),
                          ("significant_transitions", "visual_transition")):
        for row in index.get(field, []):
            if isinstance(row, Mapping):
                t = _number(row.get("timestamp", row.get("time")))
                if t is not None:
                    boundaries.append((t, reason))
    for t, reason in boundaries:
        add(t, reason)
        if reason in {"scene_boundary", "visual_transition", "text_change",
                      "entity_transition"}:
            add(t - dense_radius_seconds, f"{reason}_approach")
            add(t + dense_radius_seconds, f"{reason}_aftermath")

    # Activity peaks preserve measured motion/change while static regions keep
    # only the discovery floor. Subject motion is used only when a geometry
    # decomposition explicitly provides both global camera and residual energy.
    bins = (activity_map or {}).get("bins", [])
    for row in bins:
        if not isinstance(row, Mapping):
            continue
        start, end = _number(row.get("start")), _number(row.get("end"))
        if start is None or end is None or end <= start:
            continue
        vals = []
        for name in ("motion", "novelty", "scene", "speech"):
            signal = (row.get("signals") or {}).get(name)
            if isinstance(signal, Mapping):
                value = _number(signal.get("normalized"))
                if value is not None:
                    vals.append((name, value))
        peak = max((v for _, v in vals), default=None)
        if peak is not None and peak >= 0.65:
            name = max(vals, key=lambda item: item[1])[0]
            add((start + end) / 2, f"{name}_peak")

    decomposition = index.get("camera_motion_decomposition") or {}
    geometry_reliable = decomposition.get("geometry_status") == "reliable"
    for row in decomposition.get("values", []):
        if not isinstance(row, Mapping):
            continue
        t = _number(row.get("time"))
        if t is None:
            continue
        # Separate camera movement from residual subject motion only when the
        # producer explicitly qualifies the geometry decomposition.
        camera = _number(row.get("translation_x"))
        camera_y = _number(row.get("translation_y"))
        residual = _number(row.get("subject_energy"))
        if geometry_reliable and residual is not None:
            if abs(residual) >= 0.25:
                add(t, "geometry_subject_motion")
            if camera is not None and camera_y is not None \
                    and math.hypot(camera, camera_y) >= 0.25:
                add(t, "geometry_camera_motion")

    # Candidate relevance is supplied by retrieval; query text is preserved in
    # the policy record and controls whether refinement is query conditioned.
    candidate_records = []
    candidate_priority: dict[float, float] = {}
    for row in retrieval_candidates:
        start, end = _number(row.get("start")), _number(row.get("end"))
        score = _number(row.get("score"), 0.0)
        if start is None or end is None or end <= start:
            continue
        score = min(1.0, max(0.0, score or 0.0))
        kind = str(row.get("evidence_type", "retrieval_candidate"))
        candidate_records.append({"start": start, "end": end,
                                  "score": score, "evidence_type": kind})
        # Increase temporal density for action-like candidates. OCR/detail
        # requests mark spatial analysis as higher-resolution work.
        tokens = (query or "").lower()
        action_query = any(term in tokens for term in
                           ("gesture", "wave", "pick up", "hand", "interact", "point"))
        count = max(1, min(9, math.ceil((end - start) * (2.0 if action_query else 0.5))))
        for i in range(count):
            candidate_time = round(round(
                (start + (i + 0.5) * (end - start) / count) * rate) / rate, 6)
            add(candidate_time, "query_retrieval_candidate")
            candidate_priority[candidate_time] = max(
                score, candidate_priority.get(candidate_time, 0.0))

    existing = {_number(t) for t in existing_sample_times}
    existing.discard(None)
    eligible = [(t, reasons) for t, reasons in signals.items()
                if not any(abs(t - old) < 0.5 / rate for old in existing)]
    # More specific query candidates and transitions outrank the discovery
    # grid; deterministic source-time tie breaks make replays stable.
    weights = {"query_retrieval_candidate": 5, "scene_boundary": 4,
               "visual_transition": 4, "text_change": 4,
               "entity_transition": 4, "motion_peak": 3,
               "novelty_peak": 3, "speech_boundary": 3,
               "geometry_subject_motion": 3,
               "geometry_camera_motion": 2,
               "scene_peak": 2, "speech_peak": 2, "discovery": 1}
    ordered = sorted(eligible, key=lambda item: (
        -max((weights.get(r.removesuffix("_approach").removesuffix("_aftermath"), 1)
              for r in item[1]), default=1),
        -candidate_priority.get(item[0], 0.0), item[0]))
    chosen = ordered[:budget_frames]
    selected = sorted(t for t, _ in chosen)
    examined = sorted(existing | set(selected))
    gaps = []
    cursor = 0.0
    for t in examined:
        if t > cursor:
            gaps.append({"start": round(cursor, 6), "end": round(t, 6)})
        cursor = max(cursor, t + 1.0 / rate)
    if cursor < duration:
        gaps.append({"start": round(cursor, 6), "end": round(duration, 6)})
    request = (query or "").strip()
    return {
        "policy_version": POLICY_VERSION,
        "query": request or None,
        "budget_frames": budget_frames,
        "fps": rate,
        "selected_source_times": selected,
        "selected_frame_count": len(selected),
        "examined_frame_count": len(examined),
        "remaining_budget": budget_frames - len(selected),
        "selection_reasons": {str(t): sorted(reasons) for t, reasons in chosen},
        "retrieval_candidates": candidate_records,
        "high_resolution_requested": any(
            term in request.lower() for term in ("read", "text", "small", "detail", "label")),
        "unexamined_intervals": gaps,
        "coverage_status": "sampled",
        "claim_policy": "sampled evidence only; no exhaustive absence claim",
        "parameters": {"discovery_step_seconds": round(discovery_step, 6),
                       "dense_radius_seconds": dense_radius_seconds,
                       "activity_peak_threshold": 0.65},
    }

"""Deterministic, source-clock frame selection for targeted visual analysis.

This policy schedules measurements; selected frames are sampled evidence and
never establish continuous coverage or an event's absence.

The schedule answers to four things, and to the edit-critical vocabulary:

- MOTION and SEMANTIC CHANGE, from the measured activity map and the
  temporal index;
- SCENE COMPLEXITY, measured as how much changed inside a window - a
  fast-cut region is sampled denser than a static one;
- QUERY IMPORTANCE, from the retrieval candidates the query lane supplies;
- EDIT-CRITICAL EVENTS, at the tolerance each event type declares, so a
  speech boundary, an action or an on-screen text change keeps the
  high-fidelity coverage its own type says a cut may need.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.edit_critical_events import fidelity_requirement

POLICY_VERSION = "adaptive-source-frames-v2"
DEFAULT_BUDGET = 48
DEFAULT_DENSE_RADIUS_SECONDS = 1.0

#: Half the frame budget may be REQUESTED by the edit-critical bands.
#: A band is a request for high-fidelity coverage, not a claim on the
#: whole budget: a clip with a thousand boundaries would otherwise spend
#: every frame on the first hundred of them, and the rest of the clip
#: would go unmeasured.
EDIT_CRITICAL_BAND_BUDGET_FRACTION = 0.5

#: A band is sampled at at most this many points. The source's own frame
#: rate bounds how finely a boundary can be resolved; a tolerance finer
#: than one frame is the SOURCE's to resolve, and the plan says so
#: rather than pretending the sampler reached it.
MAX_BAND_SAMPLES_PER_BOUNDARY = 5

#: The window a change rate is measured over.
COMPLEXITY_BIN_SECONDS = 2.0

#: A window is complex when it holds at least this share of the busiest
#: window's change rate. A RELATIVE measure: a fast-cut clip is
#: uniformly complex, and a static clip has nothing to be complex about.
COMPLEXITY_SHARE = 0.5

#: The reasons, and what each is worth when the budget is short. A
#: higher number survives; the discovery grid is the floor and the
#: lowest.
WEIGHTS: dict[str, int] = {
    "query_retrieval_candidate": 5,
    "scene_boundary": 4, "visual_transition": 4, "text_change": 4,
    "entity_transition": 4, "motion_peak": 3, "novelty_peak": 3,
    "speech_boundary": 3, "geometry_subject_motion": 3,
    "geometry_camera_motion": 2, "scene_peak": 2, "speech_peak": 2,
    "discovery": 1,
}

#: A band around an edit-critical event outranks every other signal: the
#: event's own type says a cut may sit on its boundary, and the frame
#: that resolves it is worth more than the frame that merely covers the
#: clip.
FIDELITY_WEIGHT = 6


def _number(value: Any, default: float | None = None) -> float | None:
    return (float(value) if isinstance(value, (int, float))
            and math.isfinite(float(value)) else default)


def _weight(reason: str) -> int:
    if reason.startswith("fidelity_"):
        return FIDELITY_WEIGHT
    base = reason.removesuffix("_approach").removesuffix("_aftermath")
    return WEIGHTS.get(base, 1)


def _complexity_bins(index: Mapping[str, Any], duration: float,
                     bin_seconds: float = COMPLEXITY_BIN_SECONDS
                     ) -> list[dict[str, Any]]:
    """How much CHANGED inside each window of the clip.

    Scene complexity is a MEASUREMENT, not a taste: it counts the
    measured transitions - cuts, entity transitions, visual transitions,
    text changes - that fall inside each window and rates them per
    second. A window nothing changed in has a rate of zero, which is a
    measurement, and a clip with no transitions at all has no complex
    window anywhere.
    """
    times: list[float] = []
    for field in ("scene_boundaries", "ocr_changes", "entity_transitions",
                  "significant_transitions"):
        for row in (index or {}).get(field, []) or []:
            if not isinstance(row, Mapping):
                continue
            at = _number(row.get("timestamp", row.get("time")))
            if at is not None and 0 <= at < duration:
                times.append(at)
    bins: list[dict[str, Any]] = []
    cursor = 0.0
    while cursor < duration:
        end = min(duration, cursor + bin_seconds)
        count = sum(1 for at in times if cursor <= at < end)
        bins.append({"start": round(cursor, 6), "end": round(end, 6),
                     "changes": count,
                     "complexity": round(count / (end - cursor), 6)})
        cursor = end
    return bins


def edit_critical_events_from_index(
        index: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The edit-critical events the temporal index has measured.

    A speech region is a `speech_start_end` event and a measured cut is a
    `scene_transition` event - the two types the index carries today. A
    type with no measured event is simply not here: the plan reports the
    types it covered and the types it had no evidence for, so a missing
    one reads as missing rather than as absent.
    """
    events: list[dict[str, Any]] = []
    for row in (index or {}).get("speech_regions", []) or []:
        if not isinstance(row, Mapping):
            continue
        start, end = _number(row.get("start")), _number(row.get("end"))
        if start is None or end is None or end <= start:
            continue
        events.append({"event_type": "speech_start_end",
                       "start": start, "end": end})
    for row in (index or {}).get("scene_boundaries", []) or []:
        if not isinstance(row, Mapping):
            continue
        at = _number(row.get("timestamp", row.get("time")))
        if at is None:
            continue
        events.append({"event_type": "scene_transition",
                       "start": at, "end": at})
    return events


def _spread(values: list[float], thinning: int) -> list[float]:
    """Keep every ``thinning``-th value, evenly spread over the list.

    The first and the last are kept: a type whose boundaries were
    thinned away loses the ends of the clip, and the ends are where a
    boundary usually is.
    """
    count = len(values)
    if count == 0:
        return []
    keep = max(1, int(math.ceil(count / max(1, thinning))))
    if keep >= count:
        return list(values)
    step = (count - 1) / (keep - 1) if keep > 1 else 0.0
    return [values[int(round(index * step))] for index in range(keep)]


def _band_points(boundary: float, tolerance: float, fps: float,
                 duration: float) -> tuple[list[float], float]:
    """The points that resolve a boundary to its tolerance.

    The band is the tolerance either side of the boundary, sampled at
    the source's own frame rate and capped. A tolerance finer than one
    frame cannot be resolved by sampling this source: the band
    collapses onto the frames the source has, and the plan reports the
    resolution it actually reached.
    """
    frame = 1.0 / fps
    half = max(tolerance, frame)
    count = min(MAX_BAND_SAMPLES_PER_BOUNDARY,
                max(2, int(math.ceil(2 * half * fps)) + 1))
    points: list[float] = []
    for index in range(count):
        offset = (-half + (2 * half) * (index / (count - 1))
                  if count > 1 else 0.0)
        at = boundary + offset
        if 0 <= at < duration:
            points.append(at)
    # The resolution the band actually reaches: the widest gap between
    # the points it has, which is what a boundary can be located to.
    spacing = (max((b - a for a, b in zip(points, points[1:])), default=0.0)
               if len(points) > 1 else 0.0)
    return points, spacing


def _fidelity_bands(events: Sequence[Mapping[str, Any]], fps: float,
                    duration: float, budget_frames: int
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The high-fidelity band each edit-critical event asks for.

    Each event type declares how precisely its boundary must be known
    before a cut may sit on it (`edit_critical_events`). The band is
    that precision around the boundary. When the boundaries of one type
    outnumber the budget, they are thinned UNIFORMLY, so the type keeps
    an even spread of its boundaries rather than only the earliest.
    """
    by_type: dict[str, dict[str, Any]] = {}
    for row in events or ():
        if not isinstance(row, Mapping):
            continue
        event_type = row.get("event_type")
        if not isinstance(event_type, str) or not event_type:
            continue
        try:
            tolerance = float(fidelity_requirement(
                event_type)["temporal_tolerance_seconds"])
        except KeyError:
            # An unknown type is not an event this sampler resolves.
            continue
        boundaries: list[float] = []
        for key in ("start", "end", "boundary_seconds"):
            at = _number(row.get(key))
            if at is not None and 0 <= at < duration:
                boundaries.append(at)
        if not boundaries:
            continue
        entry = by_type.setdefault(
            event_type, {"tolerance_seconds": tolerance, "boundaries": set()})
        entry["boundaries"].update(boundaries)

    band_budget = max(1, int(budget_frames * EDIT_CRITICAL_BAND_BUDGET_FRACTION))
    affordable = max(1, band_budget // MAX_BAND_SAMPLES_PER_BOUNDARY)
    total = sum(len(entry["boundaries"]) for entry in by_type.values())
    thinning = max(1, int(math.ceil(total / affordable))) if total else 1

    bands: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for event_type in sorted(by_type):
        entry = by_type[event_type]
        tolerance = entry["tolerance_seconds"]
        boundaries = sorted(entry["boundaries"])
        kept = _spread(boundaries, thinning)
        points: list[float] = []
        spacings: list[float] = []
        for boundary in kept:
            band_points, spacing = _band_points(boundary, tolerance, fps,
                                                duration)
            points.extend(band_points)
            if spacing:
                spacings.append(spacing)
        spacing = max(spacings) if spacings else tolerance
        bands.append({
            "event_type": event_type,
            "tolerance_seconds": tolerance,
            "boundaries": len(boundaries),
            "boundaries_sampled": len(kept),
            "samples_requested": len(points),
            "achieved_resolution_seconds": round(spacing, 6),
            "source_frame_period_seconds": round(1.0 / fps, 6),
            "tolerance_met": spacing <= tolerance,
            "thinning": thinning,
        })
        for point in points:
            samples.append({"time": point, "event_type": event_type})
    return bands, samples


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
    edit_critical_events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Choose source-frame timestamps using measured activity and retrieval.

    Retrieval candidate rows may carry ``start``, ``end``, ``score`` and
    ``evidence_type``. Timing boundaries are also taken from the temporal
    index (scene cuts, speech regions, OCR changes, and entity transitions).
    Unknown or absent measurements do not become zero activity.

    ``edit_critical_events`` rows carry an ``event_type`` from
    ``library.tools.edit_critical_events`` and the boundary or span they
    sit on; the type's declared tolerance decides the band around it.
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
        # Every measured transition keeps a band on both sides: a cut
        # that falls between two sampled frames is a cut nobody saw.
        add(t - dense_radius_seconds, f"{reason}_approach")
        add(t + dense_radius_seconds, f"{reason}_aftermath")

    # Scene complexity: a window that changed a lot is sampled denser
    # than one that did not. The rate is measured from the transitions
    # the index carries, and it is RELATIVE to the busiest window, so a
    # uniformly fast-cut clip is uniformly complex.
    complexity = _complexity_bins(index, duration)
    busiest = max((row["complexity"] for row in complexity), default=0.0)
    complexity_threshold = busiest * COMPLEXITY_SHARE
    complex_bins = 0
    for row in complexity:
        if row["complexity"] <= 0 or row["complexity"] < complexity_threshold:
            continue
        complex_bins += 1
        add(row["start"], "scene_complexity")
        add((row["start"] + row["end"]) / 2, "scene_complexity")
        add(row["end"], "scene_complexity")

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

    # The edit-critical bands: the high-fidelity coverage each event
    # type's own tolerance asks for.
    fidelity_bands, fidelity_samples = _fidelity_bands(
        edit_critical_events, rate, duration, budget_frames)
    for sample in fidelity_samples:
        add(sample["time"], f"fidelity_{sample['event_type']}")

    existing = {_number(t) for t in existing_sample_times}
    existing.discard(None)
    eligible = [(t, reasons) for t, reasons in signals.items()
                if not any(abs(t - old) < 0.5 / rate for old in existing)]
    # More specific query candidates and transitions outrank the discovery
    # grid; deterministic source-time tie breaks make replays stable.
    ordered = sorted(eligible, key=lambda item: (
        -max((_weight(r) for r in item[1]), default=1),
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
    for band in fidelity_bands:
        band["samples_selected"] = sum(
            1 for t, reasons in chosen
            if f"fidelity_{band['event_type']}" in reasons)
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
        "fidelity_bands": fidelity_bands,
        "scene_complexity": {
            "bin_seconds": COMPLEXITY_BIN_SECONDS,
            "busiest_rate": busiest,
            "threshold": round(complexity_threshold, 6),
            "complex_bins": complex_bins,
            "bins": complexity,
        },
        "high_resolution_requested": any(
            term in request.lower() for term in ("read", "text", "small", "detail", "label")),
        "unexamined_intervals": gaps,
        "coverage_status": "sampled",
        "claim_policy": "sampled evidence only; no exhaustive absence claim",
        "parameters": {"discovery_step_seconds": round(discovery_step, 6),
                       "dense_radius_seconds": dense_radius_seconds,
                       "activity_peak_threshold": 0.65,
                       "edit_critical_band_budget_fraction":
                           EDIT_CRITICAL_BAND_BUDGET_FRACTION,
                       "max_band_samples_per_boundary":
                           MAX_BAND_SAMPLES_PER_BOUNDARY,
                       "complexity_share": COMPLEXITY_SHARE},
    }

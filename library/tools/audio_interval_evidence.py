"""Typed interval evidence adapters for music and environmental audio.

These records preserve the producer's method, temporal uncertainty, and
coverage without turning model confidence into a certainty category.

Music, environmental audio AND the measured non-speech events all reach
the Project Model through this shape, so a consumer reads an interval
the same way whichever producer measured it.
"""

from __future__ import annotations

from typing import Any


def _uncertainty(method: str | None, *, resolution: float | None = None) -> dict:
    if resolution is not None:
        return {"kind": "resolution_bound", "boundary_seconds": resolution}
    return {
        "kind": "unquantified",
        "boundary_seconds": None,
        "reason": f"{method or 'producer'} does not report boundary uncertainty",
    }


def _interval(kind: str, label: str, start: float, end: float,
              method: str, coverage: dict, *, value: Any = None,
              uncertainty: dict | None = None, confidence: float | None = None,
              source: str = "music") -> dict:
    row = {
        "record_type": "interval",
        "domain": source,
        "kind": kind,
        "label": label,
        "interval_semantics": "point" if start == end else "span",
        "start_seconds": round(float(start), 3),
        "end_seconds": round(float(end), 3),
        "evidence_type": "measurement",
        "certainty_state": "measured",
        "method": method,
        "uncertainty": uncertainty or _uncertainty(method),
        "coverage": dict(coverage),
    }
    if value is not None:
        row["value"] = value
    if confidence is not None:
        row["confidence"] = confidence
    return row


def music_interval_evidence(analysis: dict) -> tuple[list[dict], dict]:
    """Normalize measured beat/downbeat and structural spans to intervals."""
    duration = analysis.get("duration_s")
    try:
        duration = max(0.0, float(duration))
    except (TypeError, ValueError):
        duration = 0.0
    tempo = analysis.get("tempo") or {}
    structure = analysis.get("structure") or {}
    sections = structure.get("sections", []) if isinstance(structure, dict) else []
    section_source = "structure"
    if not sections:
        section_grid = analysis.get("section_grid") or {}
        sections = section_grid.get("sections", []) if isinstance(section_grid, dict) else []
        if sections:
            structure = section_grid
            section_source = "section_grid"
    coverage = {
        "kind": ("complete" if analysis.get("available", True) and duration > 0
                 else "unavailable"),
        "start_seconds": 0.0,
        "end_seconds": duration,
        "duration_seconds": duration,
    }
    rows: list[dict] = []
    tempo_method = str(tempo.get("method") or "unknown")
    beat_times = [float(t) for t in tempo.get("beats", [])
                  if isinstance(t, (int, float)) and 0 <= t <= duration]
    beat_uncertainty = _uncertainty(tempo_method)
    for i, beat in enumerate(beat_times):
        # A beat is a point event represented as a degenerate interval;
        # the explicit semantics prevent it being mistaken for duration.
        rows.append(_interval("beat", "beat", beat, beat, tempo_method,
                              coverage, value={"ordinal": i + 1},
                              uncertainty=beat_uncertainty))
    downbeats = [float(t) for t in tempo.get("downbeats", [])
                 if isinstance(t, (int, float)) and 0 <= t <= duration]
    downbeat_source = tempo.get("downbeat_source") or "unknown"
    for i, beat in enumerate(downbeats):
        rows.append(_interval(
            "downbeat", "downbeat", beat, beat, tempo_method, coverage,
            value={"ordinal": i + 1, "source": downbeat_source},
            uncertainty=beat_uncertainty))

    structure_method = str(structure.get("method") or "unknown")
    # The deterministic energy/spectral structure producer pools features
    # at roughly one-second bins. allin1 reports confidence but no boundary
    # error, so its boundary uncertainty remains explicitly unquantified.
    for section in sections:
        if not isinstance(section, dict):
            continue
        try:
            start, end = float(section["start"]), float(section["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start or (duration and end > duration + 1.0):
            continue
        section_method = str(section.get("method") or structure_method)
        confidence = section.get("confidence")
        rows.append(_interval(
            "music_section", str(section.get("label") or section.get("type") or "section"),
            start, min(end, duration) if duration else end, section_method,
            coverage, value={"source": section_source},
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None))
    rows.sort(key=lambda row: (row["start_seconds"], row["end_seconds"], row["kind"]))
    return rows, coverage


def sound_interval_evidence(summary: dict) -> tuple[list[dict], dict]:
    """The measured non-speech events as typed interval evidence.

    One row per measured event, on the clip's own clock, with the
    boundary uncertainty the producer actually has: a gap under
    `MERGE_GAP_SECONDS` was closed into one event, so the reported
    extent may overstate the burst that produced it by that much, and
    the boundary itself is located to the detector's frame grid.

    A clip whose sound events were never measured produces NO rows and
    an unavailable coverage: an empty list here means "no event was
    measured", never "no event happened".

    `summary` is the per-clip index row (`sound_events` beside
    `sound_event_method`), the shape `library/tools/sound_events.py`
    reads.
    """
    from library.tools import sound_class, sound_events
    from library.tools.analysis import sound_event_pipeline

    duration = sound_class.clip_duration(summary)
    measured = sound_events.measured(summary)
    method = (summary or {}).get("sound_event_method")
    coverage = {
        "kind": ("complete" if measured and duration
                 else "unavailable"),
        "start_seconds": 0.0,
        "end_seconds": duration or 0.0,
        "duration_seconds": duration or 0.0,
    }
    if not measured:
        return [], coverage

    rows: list[dict] = []
    for event in sound_events.events(summary):
        start = event["start_seconds"]
        end = event["end_seconds"]
        span_coverage = {
            "kind": "sampled",
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": round(end - start, 3),
            "sample_rate_hz": sound_event_pipeline.FRAME_RATE_HZ,
        }
        rows.append(_interval(
            "sound_event", event["label"], start, end, str(method),
            span_coverage,
            value={"sound_class": sound_class.class_of(event["label"])},
            uncertainty={
                "kind": "resolution_bound",
                "boundary_seconds": sound_event_pipeline.MERGE_GAP_SECONDS,
            },
            confidence=event.get("confidence"),
            source="sound"))
    rows.sort(key=lambda row: (row["start_seconds"], row["end_seconds"],
                                row["label"]))
    return rows, coverage

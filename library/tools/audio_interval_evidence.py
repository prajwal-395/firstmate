"""Typed interval evidence adapters for music and environmental audio.

These records preserve the producer's method, temporal uncertainty, and
coverage without turning model confidence into a certainty category.
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

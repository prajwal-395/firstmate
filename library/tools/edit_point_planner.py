"""Evidence-backed edit-point candidates from Project Model measurements.

Candidates are suggestions for the editorial planner. They are not edits,
and this module never fills an unmeasured gap or ranks one signal as truth.
Every returned point names the source Signal records that support it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

# Measurement type aliases are deliberately explicit. Unknown measurements
# remain available to other consumers and cannot accidentally become cuts.
_SIGNAL_KINDS = {
    "speech": "speech",
    "speech_boundary": "speech",
    "speech.phoneme": "phoneme",
    "phoneme": "phoneme",
    "gesture": "gesture",
    "gesture.event": "gesture",
    "music.beat": "beat",
    "beat": "beat",
    "scene.boundary": "scene",
    "scene_boundary": "scene",
    "camera.settle": "camera_settle",
    "camera_settle": "camera_settle",
}


def _point_rows(signal: Mapping[str, Any], kind: str) -> list[tuple[int, str]]:
    """Return measured ticks and boundary labels from one Signal record."""
    interval = signal["interval"]
    start, end = interval["start"], interval["end"]
    rows: list[tuple[int, str]] = []
    if kind in {"speech", "phoneme", "gesture", "scene"}:
        # A half-open measured interval has two meaningful edit boundaries;
        # its right edge is a cut position even though it is not in the span.
        rows.extend(((start, "start"), (end, "end")))
    for sample in signal["samples"]:
        if not isinstance(sample, Mapping) or "tick" not in sample:
            continue
        tick = sample["tick"]
        # The interval's end is valid as a cut boundary, but samples themselves
        # are points in the half-open signal coverage.
        if isinstance(tick, bool) or not isinstance(tick, int) or not start <= tick < end:
            continue
        if kind in {"beat", "phoneme"}:
            rows.append((tick, kind))
        elif kind == "camera_settle":
            value = sample.get("value", sample.get("settled"))
            if value is True or value == "settled":
                rows.append((tick, "settle"))
    return rows


def generate_edit_point_candidates(project_model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive deterministic cut-point candidates from measured signals.

    Only analyzed signals with supported measurement types contribute. Points
    sharing a clock and tick are coalesced, retaining all measurement IDs and
    signal kinds. The output carries no score or claim of editorial quality.
    """
    grouped: dict[tuple[str, int], dict[str, set[str]]] = defaultdict(
        lambda: {"measurement_ids": set(), "signals": set(), "boundary_types": set()}
    )
    for signal in project_model["signals"]:
        kind = _SIGNAL_KINDS.get(signal["measurement_type"])
        if kind is None or signal["processing_status"] != "analyzed":
            continue
        signal_id = signal["id"]
        clock_id = signal["interval"]["clock_id"]
        for tick, boundary in _point_rows(signal, kind):
            row = grouped[(clock_id, tick)]
            row["measurement_ids"].add(signal_id)
            row["signals"].add(kind)
            row["boundary_types"].add(boundary)

    return [
        {
            "clock_id": clock_id,
            "tick": tick,
            "candidate_type": "edit_point",
            "measurement_ids": sorted(row["measurement_ids"]),
            "signals": sorted(row["signals"]),
            "boundary_types": sorted(row["boundary_types"]),
            "status": "candidate",
        }
        for (clock_id, tick), row in sorted(grouped.items())
    ]

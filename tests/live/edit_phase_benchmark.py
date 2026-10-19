"""Strict per-phase recorder for one warm-session small-edit qualification.

The live adapter emits observed timestamps/counters. Missing phases are
reported as missing, never inferred from total wall time. Retry attempts and
unrelated operations remain visible in the result.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

PHASES = (
    "intent_to_plan",
    "state_resolution",
    "backend_selection",
    "lease_queue_wait",
    "lease_hold",
    "api_execution",
    "verification",
    "commit_promotion",
)

TARGET_SECONDS = {
    "property_adjust": 5.0,
    "simple_move": 5.0,
    "trim": 10.0,
    "replace_graphic": 15.0,
    "add_position_graphic": 30.0,
    "small_reel_change": 30.0,
}

REQUIRED_COUNTS = (
    "attempts", "full_rebuilds", "items_touched", "model_records_touched",
    "unrelated_operations",
)


def record_edit(edit_id: str, measurement: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize and validate one observed edit measurement.

    ``phases`` maps every phase to seconds. ``render_seconds`` and
    ``model_seconds`` are reported separately from the interactive edit
    total. Counters describe the whole attempt sequence, including retries.
    """
    errors: list[str] = []
    raw_phases = measurement.get("phases")
    if not isinstance(raw_phases, Mapping):
        raw_phases = {}
        errors.append("phase timings are missing")
    phases: dict[str, float | None] = {}
    for phase in PHASES:
        value = raw_phases.get(phase)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0):
            phases[phase] = float(value)
        else:
            phases[phase] = None
            errors.append(f"missing or invalid timing: {phase}")

    counts: dict[str, int | None] = {}
    for name in REQUIRED_COUNTS:
        value = measurement.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            counts[name] = value
        else:
            counts[name] = None
            errors.append(f"missing or invalid counter: {name}")
    if counts["attempts"] == 0:
        errors.append("attempts must include the initial attempt")

    separated: dict[str, float | None] = {}
    for name in ("render_seconds", "model_seconds"):
        value = measurement.get(name)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0):
            separated[name] = float(value)
        else:
            separated[name] = None
            errors.append(f"missing or invalid separated time: {name}")

    total = measurement.get("total_seconds")
    if not (isinstance(total, (int, float)) and not isinstance(total, bool)
            and math.isfinite(total) and total >= 0):
        total = None
        errors.append("missing or invalid total_seconds")

    return {
        "edit_id": edit_id,
        "phases_seconds": phases,
        "total_seconds": float(total) if total is not None else None,
        **separated,
        **counts,
        "attempts_including_retries": counts["attempts"],
        "retries": (max(0, counts["attempts"] - 1)
                    if counts["attempts"] is not None else None),
        "errors": errors,
        "complete": not errors,
    }


def summarize(edits: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize already normalized measurements without hiding incomplete rows."""
    rows = [dict(row) for row in edits]
    return {
        "schema": "ren-edit-phase-benchmark/1",
        "edits": rows,
        "measured_edits": sum(row.get("complete") is True for row in rows),
        "incomplete_edits": sum(row.get("complete") is not True for row in rows),
        "totals": {
            key: sum(row.get(key) or 0 for row in rows)
            for key in ("retries", "full_rebuilds", "items_touched",
                        "model_records_touched", "unrelated_operations")
        },
        "targets": dict(TARGET_SECONDS),
        "target_results": [
            {"edit_id": row.get("edit_id"), "target_seconds": TARGET_SECONDS.get(
                row.get("edit_id")), "total_seconds": row.get("total_seconds"),
             "met": (row.get("complete") is True
                     and row.get("total_seconds") is not None
                     and row.get("total_seconds") < TARGET_SECONDS[row["edit_id"]])}
            for row in rows if row.get("edit_id") in TARGET_SECONDS
        ],
    }


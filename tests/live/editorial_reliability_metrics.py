"""Aggregate the reliability measures required by the live editorial suite.

Rates use completed task rows as their denominator. Missing evidence remains
visible and cannot be counted as a success. Latency is reported as observed,
not inferred from phase totals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {
            "schema": "ren-editorial-reliability-metrics/1",
            "task_count": 0,
            "rates": {},
            "latency_seconds": {"mean": None, "median": None},
            "missing_evidence": ["no task rows"],
        }

    def rate(predicate) -> float:
        return sum(bool(predicate(row)) for row in rows) / total

    latency = [float(row["elapsed_seconds"]) for row in rows
               if isinstance(row.get("elapsed_seconds"), (int, float))]
    per_request = {
        str(row.get("id", "unknown")): (
            float(row["instruction_to_verified_seconds"])
            if isinstance(row.get("instruction_to_verified_seconds"),
                          (int, float))
            else (float(row["elapsed_seconds"])
                  if isinstance(row.get("elapsed_seconds"), (int, float))
                  else None))
        for row in rows
    }
    ordered = sorted(latency)
    median = (None if not ordered else
              ordered[len(ordered) // 2] if len(ordered) % 2 else
              (ordered[len(ordered) // 2 - 1] +
               ordered[len(ordered) // 2]) / 2)
    missing = [str(row.get("id", "unknown")) for row in rows
               if not isinstance(row.get("benchmark"), Mapping)]

    return {
        "schema": "ren-editorial-reliability-metrics/1",
        "task_count": total,
        "rates": {
            "intent_interpretation": rate(
                lambda row: row.get("discovery", {}).get("operation")
                == row.get("operation")),
            "target_resolution": rate(
                lambda row: row.get("checks", {}).get("target_identity") is True),
            "capability_discovery": rate(
                lambda row: row.get("discovery", {}).get(
                    "discovered_from_request") is True),
            "backend_selection_correctness": rate(
                lambda row: row.get("backend_selection", {}).get(
                    "selection_correct") is True),
            "first_attempt_execution": rate(
                lambda row: row.get("benchmark", {}).get("attempts") == 1
                and row.get("status") == "pass"),
            "verification": rate(
                lambda row: bool(row.get("checks")) and
                all(row["checks"].values())),
            "collateral_change_rate": rate(
                lambda row: bool(row.get("collateral_changes"))),
            "recovery": rate(
                lambda row: row.get("checks", {}).get("recovery_verified") is True),
            "preservation": rate(
                lambda row: row.get("checks", {}).get("approved_edits_preserved") is True),
            "delivery": rate(
                lambda row: row.get("receipt", {}).get("status") == "delivered"),
        },
        "latency_seconds": {
            "measured_tasks": len(latency),
            "mean": mean(latency) if latency else None,
            "median": median,
            "values": latency,
            "per_request_instruction_to_verified": per_request,
        },
        "missing_evidence": missing,
    }

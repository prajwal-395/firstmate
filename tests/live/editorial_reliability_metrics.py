"""Aggregate evidence-backed editorial reliability measures.

Missing evidence is ``not_measured``. Rates retain their measured denominator,
and latency only includes wall time measured by the request harness itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean, median
from typing import Any


def _measure(rows: Sequence[Mapping[str, Any]], predicate) -> dict[str, Any]:
    eligible = [row for row in rows if predicate(row) is not None]
    if not eligible:
        return {"status": "not_measured", "value": None,
                "measured": 0, "total": len(rows),
                "not_measured": len(rows)}
    successes = sum(bool(predicate(row)) for row in eligible)
    return {"status": "measured", "value": successes / len(eligible),
            "measured": len(eligible), "total": len(rows),
            "not_measured": len(rows) - len(eligible)}


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    definitions = {
        "intent_interpretation": lambda r: (
            r["discovery"].get("operation") == r.get("operation")
            if isinstance(r.get("discovery"), Mapping) else None),
        "target_resolution": lambda r: (
            r["checks"].get("target_identity")
            if isinstance(r.get("checks"), Mapping) and "target_identity" in r["checks"] else None),
        "capability_discovery": lambda r: (
            r["discovery"].get("discovered_from_request")
            if isinstance(r.get("discovery"), Mapping) and "discovered_from_request" in r["discovery"] else None),
        "backend_selection_correctness": lambda r: (
            r["backend_selection"].get("selection_correct")
            if isinstance(r.get("backend_selection"), Mapping) and "selection_correct" in r["backend_selection"] else None),
        "first_attempt_execution": lambda r: (
            r["benchmark"].get("attempts") == 1 and r.get("status") == "pass"
            if isinstance(r.get("benchmark"), Mapping) and "attempts" in r["benchmark"] else None),
        "verification": lambda r: (
            bool(r["checks"]) and all(r["checks"].values())
            if isinstance(r.get("checks"), Mapping) and r["checks"] else None),
        "collateral_change_rate": lambda r: (
            bool(r["collateral_changes"])
            if isinstance(r.get("collateral_changes"), list) else None),
        "recovery": lambda r: (
            r["checks"].get("recovery_verified")
            if isinstance(r.get("checks"), Mapping) and "recovery_verified" in r["checks"] else None),
        "preservation": lambda r: (
            r["checks"].get("approved_edits_preserved")
            if isinstance(r.get("checks"), Mapping) and "approved_edits_preserved" in r["checks"] else None),
        "delivery": lambda r: (
            r["receipt"].get("status") == "delivered"
            if isinstance(r.get("receipt"), Mapping) else None),
    }
    rates = {name: _measure(rows, predicate)
             for name, predicate in definitions.items()}
    times = [float(row["instruction_to_verified_seconds"])
             for row in rows
             if isinstance(row.get("instruction_to_verified_seconds"),
                           (int, float))]
    setup = [float(row["fixture_setup_seconds"]) for row in rows
             if isinstance(row.get("fixture_setup_seconds"), (int, float))]
    outcomes = {name: sum(row.get("outcome") == name for row in rows)
                for name in ("success", "safe_refusal", "reasoning_failure",
                             "unsafe_failure", "missing_evidence")}
    if rows:
        outcomes["missing_evidence"] += sum(
            row.get("outcome") is None
            and (not isinstance(row.get("benchmark"), Mapping)
                 or not row.get("checks")) for row in rows)
    return {
        "schema": "ren-editorial-reliability-metrics/2",
        "task_count": total,
        "rates": rates,
        "outcomes": outcomes,
        "latency_seconds": {
            "status": "measured" if times else "not_measured",
            "measured_tasks": len(times),
            "mean_instruction_to_verified": mean(times) if times else None,
            "median_instruction_to_verified": median(times) if times else None,
            "per_request_instruction_to_verified": {
                str(row.get("id", "unknown")): row.get("instruction_to_verified_seconds")
                for row in rows},
            "fixture_setup_measured_tasks": len(setup),
            "fixture_setup_mean": mean(setup) if setup else None,
        },
    }

"""Ordinary-instruction acceptance harness for basic editorial changes.

The harness owns the ten requests and their score contract. An agent/host
adapter receives each request verbatim and must submit it through ``ren
revise`` or a Resolve AXI transaction. The adapter cannot claim success from
its own prose: a completed AXI/revise receipt, operation-specific checks, and
an empty collateral diff are required in the returned evidence.

This module does not connect to Resolve. The live adapter is intentionally
injected so the qualification runner can hold one borrowed Resolve session
for the whole batch and restore its recorded resting project afterward.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any


@dataclass(frozen=True)
class AcceptanceTask:
    id: str
    instruction: str
    operation: str
    production_entry: str


TASKS = (
    AcceptanceTask("move_lower_third", "Move a lower third up.",
                   "overlay.position", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("resize_logo", "Resize a logo.",
                   "overlay.resize", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("add_logo_five_seconds", "Add a logo for five seconds.",
                   "overlay.add", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("trim_pause", "Trim a pause.",
                   "clip.trim", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("replace_graphic", "Replace a graphic.",
                   "overlay.replace", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("restyle_captions", "Restyle all captions.",
                   "caption.style", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("remove_broll", "Remove a B-roll shot.",
                   "clip.remove", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("insert_relevant_broll",
                   "Insert relevant B-roll.",
                   "broll.insert", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("right_speaker_camera",
                   "Pick the right speaker camera.",
                   "multicam.select_angle",
                   "ren revise / Resolve AXI transaction"),
    AcceptanceTask("improve_opening", "Improve a reel opening.",
                   "reel.opening", "ren revise / Resolve AXI transaction"),
)


def run_tasks(execute_instruction: Callable[[str, AcceptanceTask], Mapping[str, Any]]) -> dict:
    """Run each ordinary request once through the supplied production driver.

    Driver output must contain the verbatim request, a production receipt,
    checks derived from observed state, collateral changes, and benchmark
    data. Exceptions and absent drivers become explicit failures; there is no
    retry or substitute operation in this harness.
    """
    rows = []
    for task in TASKS:
        started = perf_counter()
        try:
            evidence = dict(execute_instruction(task.instruction, task))
            if evidence.get("instruction") != task.instruction:
                raise ValueError("driver did not record the ordinary instruction verbatim")
            receipt = evidence["receipt"]
            checks = evidence["checks"]
            collateral = evidence["collateral_changes"]
            metrics = evidence["benchmark"]
            passed = (
                receipt.get("status") in {"verified", "committed", "delivered"}
                and bool(checks)
                and all(checks.values())
                and not collateral
                and metrics.get("attempts") == 1
            )
            rows.append({
                **asdict(task), "status": "pass" if passed else "fail",
                "receipt": receipt, "checks": checks,
                "collateral_changes": collateral,
                "benchmark": metrics,
                "elapsed_seconds": evidence.get(
                    "elapsed_seconds", perf_counter() - started),
                "observed": evidence.get("observed", {}),
            })
        except Exception as exc:  # noqa: BLE001 - report every task independently
            rows.append({
                **asdict(task), "status": "fail",
                "error": f"{type(exc).__name__}: {exc}",
                "collateral_changes": [], "checks": {},
                "elapsed_seconds": perf_counter() - started,
            })
    return {
        "schema": "ren-editorial-acceptance/1",
        "task_count": len(TASKS),
        "passed": sum(row["status"] == "pass" for row in rows),
        "failed": sum(row["status"] == "fail" for row in rows),
        "first_attempt_passes": sum(
            row["status"] == "pass" and row.get("benchmark", {}).get("attempts") == 1
            for row in rows),
        "results": rows,
    }


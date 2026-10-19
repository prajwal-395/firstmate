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
AcceptanceTask("move_lower_third",
               "Move the selected lower third up by 40 output pixels, "
               "keeping its timing, size, safe-area placement, and every "
               "other item unchanged.",
                   "overlay.position", "ren revise / Resolve AXI transaction"),
AcceptanceTask("resize_logo",
               "Set the selected logo to 180 output pixels wide, keep its "
               "anchor and timing, and leave all other items unchanged.",
                   "overlay.resize", "ren revise / Resolve AXI transaction"),
AcceptanceTask("add_logo_five_seconds",
                   "Add the supplied logo over the final five seconds, "
                   "inside the safe area, without moving or changing any "
                   "other timeline item.",
                   "overlay.add", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("trim_pause",
                   "Remove the marked pause while keeping the surrounding "
                   "speech in order and the linked audio and video in sync.",
                   "clip.trim", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("replace_graphic",
                   "Replace the selected lower third with the supplied "
                   "approved version and preserve its position, timing, "
                   "and all other approved edits.",
                   "overlay.replace", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("restyle_captions",
                   "Apply the supplied caption style consistently to every "
                   "caption, keeping each caption's text and timing.",
                   "caption.style", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("remove_broll",
                   "Remove the selected B-roll shot and close its gap while "
                   "keeping adjacent speech and sync intact.",
                   "clip.remove", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("insert_relevant_broll",
                   "Cover the marked phrase with the supplied matching "
                   "B-roll, using the marked source range and preserving "
                   "the underlying speech.",
                   "broll.insert", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("right_speaker_camera",
                   "Show the camera assigned to the marked speaker for "
                   "their speaking turn, preserving the edit points.",
                   "multicam.select_angle",
                   "ren revise / Resolve AXI transaction"),
    AcceptanceTask("improve_opening",
                   "Tighten the opening to the supplied brief while "
                   "preserving the speaker's meaning and approved edits.",
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
            discovery = evidence["discovery"]
            backend = evidence["backend_selection"]
            passed = (
                receipt.get("status") in {"verified", "committed", "delivered"}
                and bool(checks)
                and all(checks.values())
                and not collateral
                and metrics.get("attempts") == 1
                and discovery.get("operation") == task.operation
                and discovery.get("discovered_from_request") is True
                and backend.get("selected")
                and backend.get("selection_correct") is True
            )
            rows.append({
                **asdict(task), "status": "pass" if passed else "fail",
                "receipt": receipt, "checks": checks,
                "collateral_changes": collateral,
                "benchmark": metrics,
                "discovery": discovery,
                "backend_selection": backend,
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
    from tests.live.editorial_reliability_metrics import summarize

    report = {
        "schema": "ren-editorial-acceptance/1",
        "task_count": len(TASKS),
        "passed": sum(row["status"] == "pass" for row in rows),
        "failed": sum(row["status"] == "fail" for row in rows),
        "first_attempt_passes": sum(
            row["status"] == "pass" and row.get("benchmark", {}).get("attempts") == 1
            for row in rows),
        "results": rows,
    }
    report["reliability_metrics"] = summarize(rows)
    return report


def run_discovery_probe(
        discover_execute_verify: Callable[[str, str, str], Mapping[str, Any]],
        *, operation: str, ordinary_instruction: str,
        skill_text: str, capability_catalog: str,
        previous_capability_catalog: str) -> dict[str, Any]:
    """Prove skill-only discovery of an operation absent from the skill.

    The callback gets the ordinary request, unchanged skill text, and the
    current capability catalog. It must return observed discovery, routing,
    execution and verification evidence from production entry points.
    """
    if operation in skill_text:
        raise ValueError("probe operation already appears in the supplied skill")
    if operation in previous_capability_catalog:
        raise ValueError("probe operation must be newly qualified for this run")
    if operation not in capability_catalog:
        raise ValueError("current capability catalog does not contain probe operation")
    before = discover_execute_verify(
        ordinary_instruction, skill_text, capability_catalog)
    required = {"discovery", "backend_selection", "receipt", "checks",
                "collateral_changes", "skill_fingerprint"}
    missing = sorted(required - before.keys())
    if missing:
        raise ValueError(f"discovery adapter omitted evidence: {missing}")
    discovery = before["discovery"]
    route = before["backend_selection"]
    checks = before["checks"]
    passed = (
        discovery.get("operation") == operation
        and discovery.get("discovered_from_catalog") is True
        and discovery.get("skill_updated") is False
        and bool(route.get("selected"))
        and route.get("selection_correct") is True
        and before["receipt"].get("status") in {
            "verified", "committed", "delivered"}
        and bool(checks) and all(checks.values())
        and not before["collateral_changes"]
    )
    return {
        "schema": "ren-capability-discovery-probe/1",
        "operation": operation,
        "instruction": ordinary_instruction,
        "skill_updated": False,
        "capability_added_since_baseline": True,
        "status": "pass" if passed else "fail",
        "evidence": dict(before),
    }


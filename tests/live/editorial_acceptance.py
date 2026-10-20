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

from tests.live.evaluation_levels import catalog as evaluation_level_catalog


@dataclass(frozen=True)
class AcceptanceTask:
    id: str
    instruction: str
    operation: str
    production_entry: str
    evaluation_level: int = 2


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

# Compound requests exercise operation selection without exposing their
# scoring keys to the execution callback.
COMPOUND_REQUESTS = (
    AcceptanceTask("compound_move_and_resize",
        "Move the selected logo 24 output pixels to the left and make it 180 output pixels wide. Keep its anchor and timing, and leave all other items unchanged.",
        "overlay.position+overlay.resize", "ren revise / Resolve AXI transaction"),
    AcceptanceTask("compound_trim_and_replace",
        "Remove the marked pause, then replace the selected lower third with the supplied approved version. Keep speech in order, preserve sync and approved placement, and leave all other items unchanged.",
        "clip.trim+overlay.replace", "ren revise / Resolve AXI transaction"),
)

_TARGET_ROLES = {
    "move_lower_third": "selected_lower_third",
    "resize_logo": "selected_logo",
    "add_logo_five_seconds": "supplied_logo_asset",
    "trim_pause": "marked_pause",
    "replace_graphic": "selected_lower_third",
    "restyle_captions": "all_caption_cards",
    "remove_broll": "selected_broll_shot",
    "insert_relevant_broll": "marked_phrase_and_matching_broll",
    "right_speaker_camera": "marked_speaking_turn",
    "improve_opening": "opening_section_from_brief",
    "compound_move_and_resize": "selected_logo",
    "compound_trim_and_replace": "marked_pause_and_selected_lower_third",
}


def run_tasks(execute_instruction: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
              *, include_compound: bool = False) -> dict:
    """Run each ordinary request once through the supplied production driver.

    Driver output must contain the verbatim request, a production receipt,
    checks derived from observed state, collateral changes, and benchmark
    data. Exceptions and absent drivers become explicit failures; there is no
    retry or substitute operation in this harness.
    """
    rows = []
    selected_tasks = TASKS + (COMPOUND_REQUESTS if include_compound else ())
    for task in selected_tasks:
        started = perf_counter()
        try:
            # The callback receives only the ordinary request and project
            # context. The scoring key (operation, fixture ID and expected
            # target) stays in this evaluator process.
            evidence = dict(execute_instruction(task.instruction, {
                "project": "Ren Qualification",
                "timeline": "fm-scratch qualify",
                "available_capabilities": "discover through production AXI",
            }))
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
                "outcome": ("success" if passed else
                            "safe_refusal" if receipt.get("status") in {"refused", "rejected"}
                            else "unsafe_failure"),
                "receipt": receipt, "checks": checks,
                "collateral_changes": collateral,
                "benchmark": metrics,
                "discovery": discovery,
                "backend_selection": backend,
                "elapsed_seconds": (None if evidence.get("execution_mode")
                                    == "cached_fixture_batch"
                                    else perf_counter() - started),
                "instruction_to_verified_seconds": (
                    perf_counter() - started
                    if (evidence.get("execution_mode") != "cached_fixture_batch"
                    and receipt.get("status") in {"verified", "committed", "delivered"}
                    and bool(checks) and all(checks.values())) else None),
                "fixture_setup_seconds": evidence.get("fixture_setup_seconds"),
                "execution_mode": evidence.get("execution_mode", "request"),
                "observed": evidence.get("observed", {}),
            })
        except Exception as exc:  # noqa: BLE001 - report every task independently
            message = str(exc)
            rows.append({
                **asdict(task), "status": "fail",
                "outcome": "safe_refusal" if message.startswith("[") else "missing_evidence",
                "error": f"{type(exc).__name__}: {message}",
                "collateral_changes": [], "checks": {},
                "elapsed_seconds": perf_counter() - started,
                "instruction_to_verified_seconds": None,
            })
    from tests.live.editorial_reliability_metrics import summarize

    report = {
        "schema": "ren-editorial-acceptance/2",
        "evaluation_levels": evaluation_level_catalog(),
        "task_count": len(selected_tasks),
        "passed": sum(row["status"] == "pass" for row in rows),
        "failed": sum(row["status"] == "fail" for row in rows),
        "first_attempt_passes": sum(
            row["status"] == "pass" and row.get("benchmark", {}).get("attempts") == 1
            for row in rows),
        "results": rows,
        "evaluation": {
            "level": 2,
            "name": "semantic_execution",
            "agent_receives_expected_operation": True,
            "agent_receives_expected_target": True,
            "agent_receives_asset_selection": True,
            "agent_receives_editorial_decision": True,
            "compound_request_count": len(COMPOUND_REQUESTS) if include_compound else 0,
            "limitations": [
                "The production host adapter is given typed operation and fixture keys; this is not an autonomy result.",
                "Editorial quality against a creative brief is not measured by this level.",
            ],
        },
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


def run_blind_requests(execute_request: Callable[[str, Mapping[str, Any]],
                                                   Mapping[str, Any]]) -> dict:
    """Run requests with evaluator labels and targets withheld from the driver.

    The callback receives only an ordinary instruction and project context.
    It must return its own selected operations and resolved target evidence;
    expected values are joined only after execution for scoring.
    """
    rows = []
    for task in TASKS + COMPOUND_REQUESTS:
        started = perf_counter()
        try:
            evidence = dict(execute_request(task.instruction, {
                "project": "Ren Qualification",
                "timeline": "fm-scratch qualify",
                "capability_discovery": "available through normal AXI discovery",
            }))
            chosen = evidence.get("selected_operations")
            expected = task.operation.split("+")
            discovered = evidence.get("discovery", {})
            receipt = evidence.get("receipt", {})
            checks = evidence.get("checks", {})
            target = evidence.get("target_resolution")
            route = evidence.get("backend_selection")
            reasoning_ok = (
                isinstance(chosen, list) and chosen == expected
                and discovered.get("discovered_from_request") is True
                and isinstance(target, Mapping)
                and target.get("semantic_role") == _TARGET_ROLES[task.id]
                and target.get("uniquely_resolved") is True
                and isinstance(route, Mapping)
                and bool(route.get("selected"))
                and route.get("selection_correct") is True)
            execution_ok = (
                receipt.get("status") in {"verified", "committed", "delivered"}
                and bool(checks) and all(checks.values())
                and not evidence.get("collateral_changes"))
            status = ("success" if reasoning_ok and execution_ok else
                      "reasoning_failure" if not reasoning_ok else
                      "unsafe_failure")
            if receipt.get("status") in {"refused", "rejected"}:
                no_mutation = (isinstance(checks, Mapping)
                               and checks.get("no_mutation") is True
                               and not evidence.get("collateral_changes"))
                status = "safe_refusal" if no_mutation else "unsafe_failure"
            elapsed = (perf_counter() - started
                       if evidence.get("execution_mode") == "request"
                       else None)
            row = {
                "id": task.id, "instruction": task.instruction,
                "evaluation_level": 3, "status": status,
                "outcome": status,
                "expected_operation_count": len(expected),
                "selected_operations": chosen,
                "discovery": discovered,
                "backend_selection": evidence.get("backend_selection"),
                "target_resolution": target,
                "checks": checks,
                "collateral_changes": evidence.get("collateral_changes"),
                "receipt": receipt,
                "instruction_to_verified_seconds": (
                    elapsed if status == "success" else None),
                "fixture_setup_seconds": evidence.get("fixture_setup_seconds"),
            }
        except Exception as exc:  # noqa: BLE001 - report each request independently
            row = {"id": task.id, "instruction": task.instruction,
                   "evaluation_level": 3, "status": "missing_evidence",
                   "outcome": "missing_evidence",
                   "error": f"{type(exc).__name__}: {exc}",
                   "instruction_to_verified_seconds": None}
        rows.append(row)
    from tests.live.editorial_reliability_metrics import summarize
    return {
        "schema": "ren-blind-editorial-requests/1",
        "evaluation_levels": evaluation_level_catalog(),
        "evaluation": {
            "level": 3, "name": "autonomous_orchestration",
            "agent_receives_expected_operation": False,
            "agent_receives_expected_target": False,
            "agent_receives_asset_selection": False,
            "agent_receives_editorial_decision": False,
            "compound_request_count": len(COMPOUND_REQUESTS),
            "limitations": [
                "The driver chooses the operation from the instruction and "
                "resolves the target from live read-back; the fixture "
                "supplies the asset and the frames the edit is executed "
                "with, so asset selection is NOT demonstrated at this "
                "level - the operation and the target are.",
                "Editorial quality against a creative brief is not measured "
                "by this level.",
            ],
        },
        "task_count": len(rows),
        "passed": sum(row["status"] == "success" for row in rows),
        "safe_refusals": sum(row["status"] == "safe_refusal" for row in rows),
        "failed": sum(row["status"] == "unsafe_failure" for row in rows),
        "reasoning_failures": sum(row["status"] == "reasoning_failure" for row in rows),
        "missing_evidence": sum(row["status"] == "missing_evidence" for row in rows),
        "results": rows,
        "reliability_metrics": summarize(rows),
    }


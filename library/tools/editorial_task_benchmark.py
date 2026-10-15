"""Acceptance checks and score aggregation for evidence-to-edit benchmarks.

The benchmark's unit is an editorial task with a frozen human reference.
Each task may exercise more than one task type, which lets the two approved
briefs cover dialogue/multicam, visual action, and visual object editing.
This module validates recorded runs; it does not produce edits or infer
missing measurements.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REQUIRED_TASK_TYPES = frozenset(
    {
        "dialogue_multicam",
        "visual_action",
        "visual_object",
    }
)
REQUIRED_EVIDENCE_SOURCES = frozenset(
    {
        "project_model",
        "evidence_query",
        "bounded_acquisition",
        "edit_point_signals",
    }
)
REQUIRED_GRAPH_STAGES = ("evidence", "plan", "edit_graph", "render", "verify")
REQUIRED_METRICS = (
    "task_success",
    "evidence_grounding",
    "cut_accuracy",
    "coverage",
    "continuity_errors",
    "human_revision_distance",
    "human_preference",
    "wall_seconds",
    "interventions",
    "model_calls",
    "model_seconds",
    "compute_cost_usd",
)


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_report(
    report: dict[str, Any], *, output_root: Path | None = None
) -> dict[str, Any]:
    """Return structural acceptance plus explicit missing-work reasons.

    ``output_root`` makes reviewable-output checks hermetic: all paths are
    interpreted relative to the caller-provided root and may not escape it.
    """
    errors: list[str] = []
    tasks = report.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 2:
        errors.append("exactly two task briefs are required")
        tasks = tasks if isinstance(tasks, list) else []

    task_type_uses: dict[str, int] = defaultdict(int)
    completed_run_counts: dict[str, int] = defaultdict(int)
    evidence_sources_used: set[str] = set()
    held_out_types: set[str] = set()
    run_count = 0
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"tasks[{task_index}]: task must be an object")
            continue
        task_name = task.get("id", f"tasks[{task_index}]")
        brief = task.get("brief")
        if not isinstance(brief, dict):
            errors.append(f"{task_name}: editorial brief is missing")
        else:
            _validate_brief(task_name, brief, errors)
        task_types = task.get("task_types")
        if not isinstance(task_types, list) or not task_types:
            errors.append(f"{task_name}: task_types must be a non-empty list")
            task_types = []
        for task_type in task_types:
            if not isinstance(task_type, str) or task_type not in REQUIRED_TASK_TYPES:
                errors.append(f"{task_name}: unknown task type {task_type!r}")
                continue
            task_type_uses[task_type] += 1

        reference = task.get("human_reference")
        if not isinstance(reference, dict) or not reference.get("path"):
            errors.append(f"{task_name}: human reference edit is missing")
        elif reference.get("blinded") is not True:
            errors.append(f"{task_name}: human reference must be recorded as blinded")
        elif output_root is not None and not _contained_file(
            output_root, reference["path"]
        ):
            errors.append(
                f"{task_name}: human reference file is missing or outside output_root"
            )

        runs = task.get("runs")
        if not isinstance(runs, list):
            errors.append(f"{task_name}: runs must be a list")
            continue
        for run_index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(f"{task_name}.runs[{run_index}]: run must be an object")
                continue
            run_count += 1
            run_name = f"{task_name}.runs[{run_index}]"
            split = run.get("split")
            if split not in {"training", "calibration", "held_out"}:
                errors.append(f"{run_name}: split is invalid or missing")
            elif split == "held_out":
                held_out_types.update(task_types)

            stages = run.get("stages")
            if not isinstance(stages, dict) or any(
                stages.get(stage) != "complete" for stage in REQUIRED_GRAPH_STAGES
            ):
                errors.append(f"{run_name}: evidence-to-render stages are incomplete")

            output = run.get("reviewable_output")
            if not isinstance(output, str) or not output:
                errors.append(f"{run_name}: reviewable output is missing")
            elif output_root is not None and not _contained_file(output_root, output):
                errors.append(
                    f"{run_name}: reviewable output is missing or outside output_root"
                )

            evidence = run.get("evidence_decisions")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{run_name}: evidence decision ledger is missing")
            else:
                run_evidence_sources: set[str] = set()
                for decision_index, decision in enumerate(evidence):
                    if not isinstance(decision, dict):
                        errors.append(
                            f"{run_name}.evidence_decisions[{decision_index}]: decision must be an object"
                        )
                        continue
                    if not decision.get("decision") or not decision.get("rationale"):
                        errors.append(
                            f"{run_name}.evidence_decisions[{decision_index}]: decision and rationale are required"
                        )
                    if not decision.get("evidence_ids"):
                        errors.append(
                            f"{run_name}.evidence_decisions[{decision_index}]: evidence_ids are required"
                        )
                    if decision.get("evidence_source") not in REQUIRED_EVIDENCE_SOURCES:
                        errors.append(
                            f"{run_name}.evidence_decisions[{decision_index}]: evidence_source is unknown"
                        )
                    else:
                        evidence_sources_used.add(decision["evidence_source"])
                        run_evidence_sources.add(decision["evidence_source"])
                missing_run_evidence = sorted(
                    REQUIRED_EVIDENCE_SOURCES - run_evidence_sources
                )
                if missing_run_evidence:
                    errors.append(
                        f"{run_name}: evidence sources not consulted: {', '.join(missing_run_evidence)}"
                    )

            metrics = run.get("metrics")
            if not isinstance(metrics, dict):
                errors.append(f"{run_name}: metrics are missing")
                continue
            missing = [metric for metric in REQUIRED_METRICS if metric not in metrics]
            if missing:
                errors.append(f"{run_name}: missing metrics {', '.join(missing)}")
                continue
            if not isinstance(metrics["task_success"], bool):
                errors.append(f"{run_name}: task_success must be boolean")
            for metric in ("evidence_grounding", "cut_accuracy", "coverage"):
                value = metrics[metric]
                if not _number(value) or not 0 <= value <= 1:
                    errors.append(
                        f"{run_name}: {metric} must be a measured fraction from 0 to 1"
                    )
            for metric in ("continuity_errors", "interventions", "model_calls"):
                value = metrics[metric]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    errors.append(
                        f"{run_name}: {metric} must be a non-negative integer"
                    )
            for metric in (
                "wall_seconds",
                "model_seconds",
                "compute_cost_usd",
            ):
                value = metrics[metric]
                if not _number(value) or value < 0:
                    errors.append(
                        f"{run_name}: {metric} must be a non-negative measured number"
                    )
            revision_distance = metrics["human_revision_distance"]
            if (
                not isinstance(revision_distance, int)
                or isinstance(revision_distance, bool)
                or revision_distance < 0
            ):
                errors.append(
                    f"{run_name}: human_revision_distance must count non-negative edit-graph operations"
                )
            if metrics.get("human_revision_distance_unit") != "edit_graph_operations":
                errors.append(
                    f"{run_name}: human_revision_distance_unit must be edit_graph_operations"
                )
            preference = metrics["human_preference"]
            if (
                not isinstance(preference, int)
                or isinstance(preference, bool)
                or not 1 <= preference <= 5
            ):
                errors.append(
                    f"{run_name}: human_preference must use the 1 to 5 rubric"
                )
            if isinstance(metrics["task_success"], bool) and metrics["task_success"]:
                for task_type in task_types:
                    completed_run_counts[task_type] += 1

    missing_types = sorted(REQUIRED_TASK_TYPES - set(task_type_uses))
    if missing_types:
        errors.append(f"task types not represented: {', '.join(missing_types)}")
    under_repeated = sorted(
        task_type
        for task_type in REQUIRED_TASK_TYPES
        if sum(
            len(task.get("runs", []))
            for task in tasks
            if isinstance(task, dict)
            and isinstance(task.get("runs"), list)
            and task_type in (task.get("task_types") or [])
        )
        < 2
    )
    if under_repeated:
        errors.append(
            f"task types need at least two end-to-end runs: {', '.join(under_repeated)}"
        )
    under_completed = sorted(
        task_type
        for task_type in REQUIRED_TASK_TYPES
        if completed_run_counts.get(task_type, 0) < 2
    )
    if under_completed:
        errors.append(
            f"task types need two successful end-to-end runs: {', '.join(under_completed)}"
        )
    missing_evidence = sorted(REQUIRED_EVIDENCE_SOURCES - evidence_sources_used)
    if missing_evidence:
        errors.append(f"evidence sources not consulted: {', '.join(missing_evidence)}")
    if not held_out_types:
        errors.append("a held-out footage repeat is required")

    return {
        "valid": not errors,
        "errors": errors,
        "task_count": len(tasks),
        "task_types": sorted(task_type_uses),
        "run_count": run_count,
        "held_out_task_types": sorted(held_out_types),
        "evidence_sources_used": sorted(evidence_sources_used),
        "required_task_types": sorted(REQUIRED_TASK_TYPES),
        "required_evidence_sources": sorted(REQUIRED_EVIDENCE_SOURCES),
    }


def validate_brief(task_name: str, brief: Any) -> list[str]:
    """Return structural errors for one editorial brief, without a report."""
    errors: list[str] = []
    if not isinstance(brief, dict):
        errors.append(f"{task_name}: editorial brief is missing")
    else:
        _validate_brief(task_name, brief, errors)
    return errors


def _validate_brief(task_name: str, brief: dict[str, Any], errors: list[str]) -> None:
    """Require the editorial decisions that make a task scoreable."""
    if not isinstance(brief.get("intent"), str) or not brief["intent"].strip():
        errors.append(f"{task_name}: brief intent is required")
    moments = brief.get("expected_source_moments")
    if not isinstance(moments, list) or not moments:
        errors.append(f"{task_name}: expected source moments are required")
        moments = []
    moment_ids: set[str] = set()
    for index, moment in enumerate(moments):
        label = f"{task_name}.expected_source_moments[{index}]"
        if not isinstance(moment, dict):
            errors.append(f"{label}: moment must be an object")
            continue
        moment_id = moment.get("id")
        if not isinstance(moment_id, str) or not moment_id or moment_id in moment_ids:
            errors.append(f"{label}: unique id is required")
        else:
            moment_ids.add(moment_id)
        _validate_source_range(label, moment, errors)
        if not moment.get("description") or not moment.get("evidence_ids"):
            errors.append(f"{label}: description and evidence_ids are required")

    cut_ranges = brief.get("cut_ranges")
    if not isinstance(cut_ranges, list) or not cut_ranges:
        errors.append(f"{task_name}: expected cut ranges are required")
        cut_ranges = []
    for index, cut_range in enumerate(cut_ranges):
        label = f"{task_name}.cut_ranges[{index}]"
        if not isinstance(cut_range, dict):
            errors.append(f"{label}: cut range must be an object")
            continue
        _validate_source_range(label, cut_range, errors)
        if cut_range.get("moment_id") not in moment_ids:
            errors.append(f"{label}: moment_id must refer to an expected source moment")
        if not cut_range.get("reason"):
            errors.append(f"{label}: editorial reason is required")

    for key, label, required_field in (
        ("coverage_requirements", "coverage requirement", "moment_ids"),
        ("continuity_constraints", "continuity constraint", "rule"),
        ("preference_rubric", "human preference rubric", "description"),
    ):
        rows = brief.get(key)
        if not isinstance(rows, list) or not rows:
            errors.append(f"{task_name}: {label}s are required")
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get(required_field):
                errors.append(
                    f"{task_name}.{key}[{index}]: {required_field} is required"
                )
                continue
            if key == "coverage_requirements":
                moment_refs = row[required_field]
                if not isinstance(moment_refs, list) or any(
                    moment_id not in moment_ids for moment_id in moment_refs
                ):
                    errors.append(
                        f"{task_name}.{key}[{index}]: moment_ids must reference expected moments"
                    )
            if key == "preference_rubric" and row.get("scale") != [1, 5]:
                errors.append(f"{task_name}.{key}[{index}]: scale must be [1, 5]")


def _validate_source_range(label: str, row: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(row.get("source_id"), str) or not row["source_id"]:
        errors.append(f"{label}: source_id is required")
    start, end = row.get("start_seconds"), row.get("end_seconds")
    if not _number(start) or not _number(end) or start < 0 or end <= start:
        errors.append(f"{label}: valid half-open source range is required")


def _contained_file(root: Path, value: str) -> bool:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return path.is_file()


def aggregate_scores(report: dict[str, Any]) -> dict[str, Any]:
    """Aggregate measured run scores without converting missing values to zero."""
    runs = [run for task in report.get("tasks", []) for run in task.get("runs", [])]
    output: dict[str, Any] = {"run_count": len(runs)}
    for metric in REQUIRED_METRICS:
        values = [
            run["metrics"][metric]
            for run in runs
            if isinstance(run.get("metrics"), dict) and metric in run["metrics"]
        ]
        if not values:
            output[metric] = None
        elif metric in {"task_success"}:
            output[metric] = (
                sum(value is True for value in values) / len(values)
                if all(isinstance(value, bool) for value in values)
                else None
            )
        elif metric in {
            "human_preference",
            "human_revision_distance",
            "continuity_errors",
            "interventions",
            "model_calls",
        }:
            output[metric] = (
                sum(values) / len(values)
                if all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in values
                )
                else None
            )
        else:
            output[metric] = (
                sum(values) / len(values)
                if all(_number(value) for value in values)
                else None
            )
    output["evidence_source_coverage"] = sorted(
        {
            row.get("evidence_source")
            for run in runs
            for row in run.get("evidence_decisions", [])
            if row.get("evidence_source") in REQUIRED_EVIDENCE_SOURCES
        }
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="benchmark result JSON")
    parser.add_argument(
        "--output-root", type=Path, help="check referenced review files under this root"
    )
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = validate_report(report, output_root=args.output_root)
    result["scores"] = aggregate_scores(report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())

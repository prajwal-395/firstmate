"""Held-out quality/cost evaluation for evidence acquisition policies.

The benchmark consumes frozen producer runs and annotation IDs. It never
loads a model or reads footage; producer execution and its resource ledger
remain owned by the acquisition executor. Calibration is fitted exclusively
from calibration rows and then applied unchanged to held-out rows.

Input JSON has ``tasks`` (id, split, lane, gold_ids) and ``runs`` (task_id,
policy, config, predicted_ids, cost_seconds, peak_rss_mb, budget_seconds,
status). Adaptive calibration rows use policy ``adaptive_candidate``.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

SPLITS = {"training", "calibration", "held_out"}
LANES = {"semantics", "speech", "objects", "ocr", "motion"}
BASELINES = {"fixed_dense", "fixed_sparse"}


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a finite non-negative number") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return number


def _index(dataset: dict) -> tuple[dict, dict]:
    tasks: dict[str, dict] = {}
    for row in dataset.get("tasks", []):
        task_id = row.get("task_id")
        if not task_id or task_id in tasks:
            raise ValueError("task_id must be present and unique")
        if row.get("split") not in SPLITS:
            raise ValueError(f"{task_id}: split must be one of {sorted(SPLITS)}")
        if row.get("lane") not in LANES:
            raise ValueError(f"{task_id}: lane must be one of {sorted(LANES)}")
        if not isinstance(row.get("gold_ids"), list):
            raise ValueError(f"{task_id}: gold_ids must be a list")
        tasks[task_id] = row
    groups: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for run in dataset.get("runs", []):
        task_id = run.get("task_id")
        if task_id not in tasks:
            raise ValueError(f"run references unknown task {task_id!r}")
        policy = run.get("policy")
        if policy not in BASELINES | {"adaptive_candidate", "adaptive"}:
            raise ValueError(f"{task_id}: unknown policy {policy!r}")
        config = str(run.get("config", "default"))
        key = (task_id, policy, config)
        if key in seen:
            raise ValueError(f"duplicate run row: {key}")
        seen.add(key)
        predicted = run.get("predicted_ids", [])
        if not isinstance(predicted, list):
            raise ValueError(f"{task_id}: predicted_ids must be a list")
        for name in ("cost_seconds", "peak_rss_mb"):
            _number(run.get(name), f"{task_id} {name}")
        groups[task_id].append(run)
    return tasks, groups


def _score(task: dict, run: dict) -> dict:
    gold, predicted = set(task["gold_ids"]), set(run.get("predicted_ids", []))
    hits = gold & predicted
    recall = len(hits) / len(gold) if gold else (1.0 if not predicted else 0.0)
    precision = len(hits) / len(predicted) if predicted else (1.0 if not gold else 0.0)
    utility = (2 * precision * recall / (precision + recall)
               if precision + recall else 0.0)
    cost = _number(run["cost_seconds"], "cost_seconds")
    status = run.get("status", "completed")
    refused = status in {"refused", "budget_refused"}
    if refused:
        utility = recall = precision = 0.0
    return {"task_id": task["task_id"], "lane": task["lane"],
            "utility": utility, "precision": precision, "recall": recall,
            "miss": bool(gold and not hits), "cost_seconds": cost,
            "quality_per_second": utility / max(cost, 1e-9),
            "peak_rss_mb": _number(run["peak_rss_mb"], "peak_rss_mb"),
            "budget_refused": refused, "status": status}


def _aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {"status": "unmeasured", "task_count": 0}
    n = len(rows)
    return {"status": "measured", "task_count": n,
            "mean_utility": round(sum(r["utility"] for r in rows) / n, 4),
            "miss_rate": round(sum(r["miss"] for r in rows) / n, 4),
            "mean_cost_seconds": round(sum(r["cost_seconds"] for r in rows) / n, 4),
            "quality_per_second": round(sum(r["utility"] for r in rows) /
                                         max(sum(r["cost_seconds"] for r in rows), 1e-9), 6),
            "peak_rss_mb": round(max(r["peak_rss_mb"] for r in rows), 2),
            "budget_refusals": sum(r["budget_refused"] for r in rows)}


def calibrate(dataset: dict, *, noninferiority_margin: float = 0.02) -> dict:
    """Choose one adaptive config per lane using calibration split only."""
    tasks, runs_by_task = _index(dataset)
    margin = _number(noninferiority_margin, "noninferiority_margin")
    if margin > 1:
        raise ValueError("noninferiority_margin cannot exceed 1")
    fitted = {}
    for lane in sorted(LANES):
        lane_tasks = [t for t in tasks.values()
                      if t["lane"] == lane and t["split"] == "calibration"]
        candidates: dict[str, list[dict]] = defaultdict(list)
        dense, sparse = [], []
        for task in lane_tasks:
            for run in runs_by_task.get(task["task_id"], []):
                score = _score(task, run)
                if run["policy"] == "adaptive_candidate":
                    candidates[str(run.get("config", "default"))].append(score)
                elif run["policy"] == "fixed_dense":
                    dense.append(score)
                elif run["policy"] == "fixed_sparse":
                    sparse.append(score)
        baseline_runs = {"fixed_dense": {r["task_id"]: r for r in dense},
                         "fixed_sparse": {r["task_id"]: r for r in sparse}}
        baseline_ids = set.intersection(*(set(rows) for rows in baseline_runs.values()))
        if not lane_tasks or not baseline_ids or not candidates:
            continue
        eligible = []
        for config, rows in candidates.items():
            candidate_by_task = {r["task_id"]: r for r in rows}
            paired_ids = baseline_ids & set(candidate_by_task)
            if not paired_ids:
                continue
            metrics = _aggregate([candidate_by_task[key] for key in sorted(paired_ids)])
            baseline_metrics = {name: _aggregate(
                [rows[key] for key in sorted(paired_ids)])
                for name, rows in baseline_runs.items()}
            best_name = max(baseline_metrics,
                            key=lambda name: baseline_metrics[name]["mean_utility"])
            baseline = baseline_metrics[best_name]
            if (metrics["mean_utility"] + margin >= baseline["mean_utility"]
                    and metrics["mean_cost_seconds"] < baseline["mean_cost_seconds"]):
                eligible.append((metrics["quality_per_second"], config,
                                 {**metrics, "paired_baseline": best_name,
                                  "paired_baseline_utility": baseline["mean_utility"],
                                  "paired_baseline_cost_seconds": baseline["mean_cost_seconds"],
                                  "paired_task_ids": sorted(paired_ids)},
                                 baseline))
        if eligible:
            _, config, metrics, baseline = max(
                eligible, key=lambda item: (item[0], item[1]))
            fitted[lane] = {"config": config, "calibration": metrics,
                            "baseline_utility": baseline,
                            "noninferiority_margin": margin}
    return {"calibration_task_ids": sorted(
                t["task_id"] for t in tasks.values() if t["split"] == "calibration"),
            "by_lane": fitted}


def evaluate(dataset: dict, *, noninferiority_margin: float = 0.02) -> dict:
    tasks, runs_by_task = _index(dataset)
    fitted = calibrate(dataset, noninferiority_margin=noninferiority_margin)
    # Held-out rows cannot influence fitted policy selection.
    held_ids = sorted(t["task_id"] for t in tasks.values()
                      if t["split"] == "held_out")
    calibration_ids = set(fitted["calibration_task_ids"])
    if calibration_ids.intersection(held_ids):
        raise ValueError("calibration and held_out task identities overlap")
    policies: dict[str, list[dict]] = defaultdict(list)
    detailed: dict[str, list[dict]] = defaultdict(list)
    lane_rows: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for task_id in held_ids:
        task = tasks[task_id]
        for run in runs_by_task.get(task_id, []):
            policy = run["policy"]
            if policy == "adaptive_candidate":
                continue
            if policy == "adaptive":
                expected = fitted["by_lane"].get(task["lane"])
                if expected is None or str(run.get("config", "default")) != expected["config"]:
                    continue
            row = _score(task, run)
            policies[policy].append(row)
            lane_rows[task["lane"]][policy].append(row)
            detailed[policy].append(row)
    adaptive_by_task = {r["task_id"]: r for r in policies["adaptive"]}
    baseline_by_task = {policy: {r["task_id"]: r for r in policies[policy]}
                        for policy in ("fixed_dense", "fixed_sparse")}
    paired_ids = set(adaptive_by_task)
    for rows in baseline_by_task.values():
        paired_ids &= set(rows)
    paired_baselines = {name: _aggregate([rows[key] for key in sorted(paired_ids)])
                        for name, rows in baseline_by_task.items() if paired_ids}
    best_baseline = (max(paired_baselines,
                         key=lambda name: paired_baselines[name]["mean_utility"])
                     if paired_baselines else None)
    baseline = (paired_baselines[best_baseline]["mean_utility"]
                if best_baseline else None)
    baseline_cost = (paired_baselines[best_baseline]["mean_cost_seconds"]
                     if best_baseline else None)
    adaptive_paired = _aggregate([adaptive_by_task[key] for key in sorted(paired_ids)])
    adaptive = _aggregate(policies["adaptive"])
    qualified = [lane for lane, result in fitted["by_lane"].items()
                 if result["calibration"].get("status") == "measured"]
    if baseline is None or adaptive_paired["status"] != "measured":
        verdict = "failure: held-out baseline or calibrated adaptive results missing"
        comparison_ok = False
    elif (adaptive_paired["mean_utility"] + noninferiority_margin < baseline
          or adaptive_paired["mean_cost_seconds"] >= baseline_cost):
        verdict = "failure: adaptive policy is not non-inferior at lower measured cost"
        comparison_ok = False
    elif len(qualified) < 3:
        verdict = "failure: fewer than three producer types calibrated"
        comparison_ok = True
    else:
        verdict = "pass"
        comparison_ok = True
    return {"schema_version": 1, "benchmark": "adaptive-evidence-acquisition",
            "split_policy": "fit on calibration only; report held_out once",
            "calibration": fitted,
            "held_out_task_ids": held_ids,
            "held_out": {policy: _aggregate(rows) for policy, rows in sorted(policies.items())},
            "held_out_paired_task_ids": sorted(paired_ids),
            "held_out_comparison": {"baseline_policy": best_baseline,
                                    "baseline": paired_baselines.get(best_baseline),
                                    "adaptive": adaptive_paired},
            "held_out_by_lane": {lane: {policy: _aggregate(rows)
                                        for policy, rows in sorted(methods.items())}
                                 for lane, methods in sorted(lane_rows.items())},
            "held_out_task_metrics": {policy: rows for policy, rows in sorted(detailed.items())},
            "modality_coverage": {lane: {"calibrated": lane in qualified,
                                          "held_out_task_count": sum(
                                              t["lane"] == lane for t in tasks.values()
                                              if t["split"] == "held_out")}
                                  for lane in sorted(LANES)},
            "calibrated_producer_types": len(qualified),
            "acceptance": {"verdict": verdict,
                           "three_producer_types_calibrated": len(qualified) >= 3,
                           "held_out_quality_noninferior_at_lower_cost": comparison_ok,
                           "budget_refusals_reported": True}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="frozen task labels and producer run ledger JSON")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--noninferiority-margin", type=float, default=0.02)
    args = parser.parse_args(argv)
    dataset = json.loads(args.input.read_text(encoding="utf-8"))
    report = evaluate(dataset, noninferiority_margin=args.noninferiority_margin)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"{report['acceptance']['verdict']}; calibrated producer types: "
          f"{report['calibrated_producer_types']}; report: {args.out}")
    return 0 if report["acceptance"]["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

import copy

import pytest

from library.tools.acquisition_benchmark import calibrate, evaluate


def _dataset():
    tasks, runs = [], []
    for lane in ("semantics", "speech", "objects"):
        for split in ("calibration", "held_out"):
            task_id = f"{lane}-{split}"
            tasks.append({"task_id": task_id, "split": split, "lane": lane,
                          "gold_ids": ["a", "b"]})
            for policy, config, ids, cost in (
                ("fixed_dense", "default", ["a", "b"], 10),
                ("fixed_sparse", "default", ["a"], 5),
                ("adaptive_candidate", "balanced", ["a", "b"], 4),
            ):
                runs.append({"task_id": task_id, "policy": policy,
                             "config": config, "predicted_ids": ids,
                             "cost_seconds": cost, "peak_rss_mb": 100,
                             "budget_seconds": 12, "status": "completed"})
            if split == "held_out":
                runs.append({"task_id": task_id, "policy": "adaptive",
                             "config": "balanced", "predicted_ids": ["a", "b"],
                             "cost_seconds": 4, "peak_rss_mb": 80,
                             "budget_seconds": 8, "status": "completed"})
    return {"tasks": tasks, "runs": runs}


def test_adaptive_benchmark_fits_only_calibration_then_reports_paired_metrics():
    report = evaluate(_dataset())
    assert report["calibrated_producer_types"] == 3
    assert all("calibration" in task_id
               for task_id in report["calibration"]["calibration_task_ids"])
    assert report["held_out"]["adaptive"]["mean_utility"] == 1
    assert report["held_out"]["adaptive"]["mean_cost_seconds"] == 4
    assert report["acceptance"]["verdict"] == "pass"


def test_missing_held_out_modalities_are_unmeasured_and_fail_acceptance():
    dataset = _dataset()
    dataset["tasks"] = [row for row in dataset["tasks"] if row["lane"] == "objects"]
    dataset["runs"] = [row for row in dataset["runs"] if row["task_id"].startswith("objects-")]
    report = evaluate(dataset)
    assert report["modality_coverage"]["motion"] == {
        "calibrated": False, "held_out_task_count": 0}
    assert report["acceptance"]["three_producer_types_calibrated"] is False
    assert report["acceptance"]["verdict"].startswith("failure:")
    assert report["calibrated_producer_types"] == 1


def test_refusal_counts_as_miss_and_zero_utility():
    dataset = _dataset()
    refused = next(row for row in dataset["runs"]
                   if row["policy"] == "adaptive" and row["task_id"] == "objects-held_out")
    refused.update(status="budget_refused", predicted_ids=[], cost_seconds=0)
    report = evaluate(dataset)
    assert report["held_out"]["adaptive"]["budget_refusals"] == 1
    assert report["held_out"]["adaptive"]["miss_rate"] > 0


def test_duplicate_run_rows_refuse_instead_of_overwriting():
    dataset = _dataset()
    dataset["runs"].append(copy.deepcopy(dataset["runs"][0]))
    with pytest.raises(ValueError, match="duplicate run row"):
        calibrate(dataset)


def test_held_out_task_identity_cannot_be_reused_for_calibration():
    dataset = _dataset()
    dataset["tasks"].append({"task_id": "objects-calibration", "split": "held_out",
                             "lane": "objects", "gold_ids": ["a"]})
    with pytest.raises(ValueError, match="unique"):
        evaluate(dataset)


def test_calibration_records_winning_config_paired_baseline():
    tasks = [
        {"task_id": "objects-cal-1", "split": "calibration",
         "lane": "objects", "gold_ids": ["a", "b"]},
        {"task_id": "objects-cal-2", "split": "calibration",
         "lane": "objects", "gold_ids": ["a", "b"]},
    ]
    runs = [
        {"task_id": "objects-cal-1", "policy": "fixed_dense",
         "config": "default", "predicted_ids": ["a", "b"],
         "cost_seconds": 10, "peak_rss_mb": 100,
         "budget_seconds": 30, "status": "completed"},
        {"task_id": "objects-cal-2", "policy": "fixed_dense",
         "config": "default", "predicted_ids": ["a", "b"],
         "cost_seconds": 20, "peak_rss_mb": 100,
         "budget_seconds": 30, "status": "completed"},
        {"task_id": "objects-cal-1", "policy": "fixed_sparse",
         "config": "default", "predicted_ids": ["a"],
         "cost_seconds": 5, "peak_rss_mb": 100,
         "budget_seconds": 30, "status": "completed"},
        {"task_id": "objects-cal-2", "policy": "fixed_sparse",
         "config": "default", "predicted_ids": ["a"],
         "cost_seconds": 5, "peak_rss_mb": 100,
         "budget_seconds": 30, "status": "completed"},
        {"task_id": "objects-cal-1", "policy": "adaptive_candidate",
         "config": "alpha", "predicted_ids": ["a", "b"],
         "cost_seconds": 4, "peak_rss_mb": 80,
         "budget_seconds": 8, "status": "completed"},
        {"task_id": "objects-cal-2", "policy": "adaptive_candidate",
         "config": "alpha", "predicted_ids": ["a", "b"],
         "cost_seconds": 4, "peak_rss_mb": 80,
         "budget_seconds": 8, "status": "completed"},
        {"task_id": "objects-cal-1", "policy": "adaptive_candidate",
         "config": "zeta", "predicted_ids": ["a", "b"],
         "cost_seconds": 6, "peak_rss_mb": 80,
         "budget_seconds": 8, "status": "completed"},
    ]
    fitted = calibrate({"tasks": tasks, "runs": runs})
    assert fitted["by_lane"]["objects"]["config"] == "alpha"
    calibration = fitted["by_lane"]["objects"]["calibration"]
    assert calibration["paired_task_ids"] == ["objects-cal-1", "objects-cal-2"]
    assert calibration["paired_baseline_cost_seconds"] == 15
    assert fitted["by_lane"]["objects"]["baseline_utility"][
        "mean_cost_seconds"] == 15

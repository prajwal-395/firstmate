from typing import ClassVar

from library.tools.visual_retrieval_benchmark import (
    _random_ranking_metrics,
    bootstrap_mean_interval,
    enrich_visual_result,
)


def test_query_bootstrap_is_reproducible_and_reports_sample_size():
    first = bootstrap_mean_interval([0.0, 0.5, 1.0], samples=500)
    second = bootstrap_mean_interval([0.0, 0.5, 1.0], samples=500)
    assert first == second
    assert first["n"] == 3
    assert first["lower"] <= first["estimate"] <= first["upper"]


def test_random_baseline_uses_seeded_frame_sample_and_same_scorer():
    class Index:
        frames: ClassVar[list] = [{"clip_id": "clip_a", "filename": "test.mov", "t": 1.0},
                                  {"clip_id": "clip_a", "filename": "test.mov", "t": 20.0}]

        @staticmethod
        def _range(clip_id, filename, start, end, best, members):
            return {"clip_id": clip_id, "filename": filename, "start": start,
                    "end": end, "best_score": best}

    class Corpus:
        name_of_clip: ClassVar[dict] = {"clip_a": "test"}

    eval_set = {"queries": [{"id": "N", "family": "visual", "query": "dog",
                             "positive_frames": {}}]}
    result = _random_ranking_metrics(eval_set, Index(), Corpus(), {"top_k": 1,
                                  "frame_pad_seconds": 1}, samples=5)
    assert result["seed"] == 20261016
    assert result["samples"] == 5
    assert result["negative_return_rate"] == 1.0


def test_visual_summary_reports_floor_gap_negative_behavior_and_categories():
    eval_set = {"queries": [
        {"id": "V1", "family": "visual", "query": "open laptop", "positive_frames": {}},
        {"id": "N1", "family": "visual", "query": "dog on floor", "positive_frames": {}},
        {"id": "R1", "family": "visual", "query": "person gesturing", "positive_frames": {},
         "expect": "refused"},
    ]}
    result = {"results": [
        {"id": "V1", "family": "visual", "kind": "present", "recall": 0.5,
         "precision": 0.25, "returned": 2, "spans_touched": 1, "seconds": 0.2,
         "rows": [{"hit": True, "ambiguous": False}]},
        {"id": "N1", "family": "visual", "kind": "absent", "returned": 1,
         "abstained": False, "seconds": 0.1},
        {"id": "R1", "family": "visual", "kind": "refusal", "refused": True,
         "seconds": 0.01},
    ]}
    enrich_visual_result(eval_set, result)
    report = result["visual_benchmark"]
    assert report["macro_recall_ci95"]["estimate"] == 0.5
    assert report["negative_behavior"]["false_return_rate"] == 1.0
    assert report["negative_behavior"]["floor_abstained"] is None
    assert report["negative_behavior"]["floor_metric_status"].startswith("unsupported")
    assert report["negative_behavior"]["correctly_refused"] == 1
    assert report["latency_seconds"]["p95"] == 0.2
    assert report["category_failures"]["object"]["failed_query_ids"] == ["V1"]
    assert report["useful_interval_cost"]["seconds_per_useful_interval"] == 0.31


def test_held_out_metrics_filter_other_assets_from_recall_and_abstention():
    eval_set = {"held_out_assets": ["test"], "scoring": {"visual": {"frame_pad_seconds": 5}},
                "queries": [{"id": "V", "family": "visual", "query": "laptop",
                             "category": "test_category",
                             "positive_frames": {"test": [10], "train": [20]},
                             "ambiguous_frames": {}}]}
    result = {"results": [{"id": "V", "family": "visual", "kind": "present",
                           "recall": 0, "precision": 0, "returned": 2, "seconds": 0.1,
                           "rows": [{"asset": "test", "start": 8, "end": 12, "hit": True,
                                     "ambiguous": False},
                                    {"asset": "train", "start": 18, "end": 22, "hit": True,
                                     "ambiguous": False}]}]}
    enrich_visual_result(eval_set, result)
    held = result["visual_benchmark"]["held_out"]
    assert held["per_query"]["V"]["positive_spans"] == 1
    assert held["per_query"]["V"]["recall"] == 1
    assert held["per_query"]["V"]["returned"] == 1
    assert "test_category" in result["visual_benchmark"]["category_failures"]


def test_retrieval_diagnostics_separate_sampling_ranking_and_interval_misses(tmp_path):
    class Index:
        frames: ClassVar[list] = [{"clip_id": "held", "t": 10.0},
                                  {"clip_id": "held", "t": 50.0}]
        payload: ClassVar[dict] = {"frame_count": 2, "step_s": 10}

        def __init__(self, index_dir):
            self.index_dir = index_dir

    class Corpus:
        name_of_clip: ClassVar[dict] = {"held": "camera_a"}

    eval_set = {
        "held_out_assets": ["camera_a"],
        "scoring": {"visual": {"frame_pad_seconds": 1, "top_k": 1}},
        "queries": [
            {"id": "sampling", "family": "visual", "query": "person wearing glasses",
             "category": "person_and_appearance", "positive_frames": {"camera_a": [20]}},
            {"id": "ranking", "family": "visual", "query": "a blue laptop",
             "category": "object", "positive_frames": {"camera_a": [10]}},
            {"id": "interval", "family": "visual", "query": "wide composition",
             "category": "composition", "positive_frames": {"camera_a": [10]}},
        ],
    }
    result = {"results": [
        {"id": "sampling", "family": "visual", "kind": "present", "recall": 0,
         "precision": 0, "returned": 0, "rows": [], "candidate_frames": []},
        {"id": "ranking", "family": "visual", "kind": "present", "recall": 0,
         "precision": 0, "returned": 0, "rows": [], "candidate_frames": []},
        {"id": "interval", "family": "visual", "kind": "present", "recall": 0,
         "precision": 0, "returned": 0, "rows": [],
         "candidate_frames": [{"asset": "camera_a", "time": 10, "score": .9}],
         "search_policy": {"candidate_pool_size": 80,
                           "merge_gap_seconds": 60.0,
                           "top_interval_limit": 10}},
    ]}
    enrich_visual_result(eval_set, result, frame_index=Index(tmp_path), corpus=Corpus())
    diagnostic = result["visual_benchmark"]["retrieval_diagnostics"]
    assert diagnostic["per_query"]["sampling"]["stage"] == "sampling_coverage"
    assert diagnostic["per_query"]["ranking"]["stage"] == "frame_ranking_or_candidate_pool"
    assert diagnostic["per_query"]["interval"]["stage"] == "interval_ranking_or_merging"
    assert diagnostic["per_query"]["interval"]["candidate_pool_size"] == 80
    assert diagnostic["per_query"]["interval"]["merge_gap_seconds"] == 60.0
    assert diagnostic["per_query"]["interval"]["top_interval_limit"] == 10
    assert diagnostic["by_category"]["people"]["queries"] == 1
    assert diagnostic["by_category"]["appearance"]["queries"] == 2


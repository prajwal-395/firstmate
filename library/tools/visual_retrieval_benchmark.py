"""Reproducible held-out visual retrieval metrics for ``ren search --visual``.

This module adds uncertainty, a seeded random-ranking reference, abstention,
and per-useful-interval operating cost to the existing hand-labelled
``retrieval_eval`` scores. It never changes the index or its ranking policy.
"""
from __future__ import annotations

import hashlib
import math
import random
import resource
import statistics
import sys
from collections.abc import Iterable
from pathlib import Path


def bootstrap_mean_interval(values: Iterable[float], *, seed: int = 20261014,
                            samples: int = 10_000,
                            confidence: float = 0.95) -> dict:
    """Percentile bootstrap CI over queries (queries, not frames, are units)."""
    values = [float(value) for value in values]
    if not values:
        return {"estimate": None, "lower": None, "upper": None, "n": 0}
    estimate = statistics.fmean(values)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values)))
                   for _ in range(samples))
    tail = (1.0 - confidence) / 2
    lo = means[int(tail * (samples - 1))]
    hi = means[int((1 - tail) * (samples - 1))]
    return {"estimate": round(estimate, 4), "lower": round(lo, 4),
            "upper": round(hi, 4), "n": len(values), "method": "query bootstrap",
            "confidence": confidence, "seed": seed, "samples": samples}


def _peak_rss_mb() -> float:
    # macOS reports bytes; Linux reports KiB.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _visual_categories(query: dict) -> list[str]:
    """Keep the source evaluation's category labels intact."""
    if query.get("category"):
        return [query["category"]]
    q = query["query"].lower()
    categories = []
    if any(term in q for term in ("laptop", "screen", "whiteboard", "cup")):
        categories.append("object")
    if any(term in q for term in ("man ", "person", "chin", "gestur")):
        categories.append("person_or_action")
    if any(term in q for term in ("street", "floor", "table", "frame")):
        categories.append("scene_or_composition")
    return categories or ["other"]


def _diagnostic_categories(query: dict) -> list[str]:
    """Map annotations and query wording to the stable diagnostic taxonomy."""
    if query.get("category"):
        label = query["category"].lower()
        q = query["query"].lower()
    else:
        label = ""
        q = query["query"].lower()
    text = f"{label} {q}"
    categories = []
    if any(term in text for term in ("object", "laptop", "screen", "whiteboard", "cup", "dog")):
        categories.append("objects")
    if any(term in text for term in ("person", "man ", "woman", "speaker", "face", "chin")):
        categories.append("people")
    if any(term in text for term in ("composition", "framing", "frame", "edge", "seated", "studio", "scene")):
        categories.append("composition")
    if any(term in text for term in ("action", "gesture", "gestur", "drink", "walking", "event")):
        categories.append("actions")
    if any(term in text for term in ("wearing", "blue", "dark", "appearance", "color", "glasses")):
        categories.append("appearance")
    if any(term in text for term in ("temporal", "during", "before", "after", "when", "throughout")):
        categories.append("temporal_events")
    return categories or ["other"]


def _retrieval_diagnostics(eval_set: dict, rows: list[dict], frame_index,
                           corpus,
                           *, held_out_assets: set[str], frame_pad_seconds: float,
                           top_k: int) -> dict:
    """Attribute each present-query miss to observable retrieval stages.

    Sampling coverage uses the frozen index; candidate coverage uses the
    recorded top frame pool; interval coverage uses the final scored ranges.
    This is diagnostic only and never changes ranking or thresholds.
    """
    from library.tools.analysis.footage_frames import MERGE_GAP_S
    from library.tools.retrieval_eval import _frame_spans, overlaps

    queries = {q["id"]: q for q in eval_set["queries"] if q["family"] == "visual"}
    by_query = {row["id"]: row for row in rows}
    frame_rows = frame_index.frames if frame_index is not None else []
    per_query = {}
    for query_id, scored in by_query.items():
        query = queries[query_id]
        if scored.get("kind") != "present":
            continue
        labels = query.get("positive_frames", {})
        if held_out_assets:
            labels = {asset: times for asset, times in labels.items() if asset in held_out_assets}
        positives = _frame_spans(labels, frame_pad_seconds)
        sampled = [{"asset": corpus.name_of_clip.get(frame["clip_id"]),
                    "time": float(frame["t"])}
                   for frame in frame_rows
                   for asset, spans in positives.items()
                   if (not held_out_assets or asset in held_out_assets)
                   and corpus.name_of_clip.get(frame["clip_id"]) == asset
                   and any(start <= float(frame["t"]) <= end for start, end in spans)]
        candidates = [frame for frame in scored.get("candidate_frames", [])
                      if frame.get("asset") in positives and any(
                          start <= float(frame["time"]) <= end
                          for start, end in positives[frame["asset"]])]
        policy = scored.get("search_policy", {})
        intervals = [rng for rng in scored.get("rows", []) if any(
            rng.get("asset") == asset and overlaps((rng["start"], rng["end"]), span)
            for asset, spans in positives.items() for span in spans)]

        if frame_index is None:
            stage = "sampling_coverage_unavailable"
        elif not sampled:
            stage = "sampling_coverage"
        elif not candidates:
            stage = "frame_ranking_or_candidate_pool"
        elif not intervals:
            stage = "interval_ranking_or_merging"
        else:
            stage = "retrieved"
        per_query[query_id] = {
            "categories": _diagnostic_categories(query),
            "stage": stage,
            "positive_spans": sum(len(spans) for spans in positives.values()),
            "indexed_frames_in_positive_spans": len(sampled),
            "positive_frames_in_candidate_pool": len(candidates),
            "positive_returned_intervals": len(intervals),
            "candidate_pool_size": policy.get(
                "candidate_pool_size", len(scored.get("candidate_frames", []))),
            "returned_interval_count": scored.get("returned", 0),
            "merge_gap_seconds": policy.get("merge_gap_seconds", MERGE_GAP_S),
            "top_interval_limit": policy.get("top_interval_limit", top_k),
        }
    by_category = {}
    for query_id, diagnostic in per_query.items():
        for category in diagnostic["categories"]:
            group = by_category.setdefault(category, {"queries": 0, "misses_by_stage": {}})
            group["queries"] += 1
            if diagnostic["stage"] != "retrieved":
                stages = group["misses_by_stage"]
                stages[diagnostic["stage"]] = stages.get(diagnostic["stage"], 0) + 1
    return {
        "method": "frozen-index coverage -> recorded frame candidate pool -> scored merged intervals",
        "per_query": per_query,
        "by_category": by_category,
        "interpretation": "A miss after sampled coverage but before candidate coverage is a ranking/pool miss. A positive candidate absent from final intervals is an interval ranking/merging miss; this report does not claim a causal distinction between those two without alternate merge replay.",
        "unmeasured_experiments": [
            "alternate temporal sampling densities and their decode/index cost",
            "multiscale visual embeddings and their index/memory cost",
            "hybrid lexical, semantic, and structured evidence",
            "calibration-only candidate-pool-size comparison",
            "object/person measurement reranking",
        ],
        "cost_coverage": "Query latency, process peak RSS, sampling step, frame count, and index bytes are measured when available. Decode and index build wall time are not retained by the current index artifact and therefore remain unmeasured.",
    }


def _random_ranking_metrics(eval_set: dict, frame_index, corpus, rules: dict,
                            *, samples: int = 500) -> dict:
    """Monte Carlo uniform-frame baseline using the same range scorer."""
    from library.tools.analysis.footage_frames import MERGE_GAP_S, SEARCH_POOL
    from library.tools.retrieval_eval import score_visual

    frames = frame_index.frames
    if not frames:
        return {"samples": samples, "status": "unavailable: empty frame index"}
    rng = random.Random(20261016)
    query_rows = {q["id"]: [] for q in eval_set["queries"] if q["family"] == "visual"}
    k = min(SEARCH_POOL, len(frames))
    for _ in range(samples):
        selected = rng.sample(frames, k)
        # Random rank supplies random best scores so top_ranges is a random
        # subset of the generated temporal ranges, exactly like retrieval.
        ranked = [(rng.random(), frame) for frame in selected]
        by_clip = {}
        for score, frame in ranked:
            by_clip.setdefault(frame["clip_id"], []).append((score, frame))
        ranges = []
        result_frames = []
        for clip_id, members in by_clip.items():
            members.sort(key=lambda pair: pair[1]["t"])
            start = end = members[0][1]["t"]
            group = [members[0]]
            best = members[0][0]
            for pair in members[1:]:
                if pair[1]["t"] - end <= MERGE_GAP_S:
                    end = pair[1]["t"]
                    group.append(pair)
                    best = max(best, pair[0])
                else:
                    ranges.append(frame_index._range(clip_id, group[0][1]["filename"],
                                                     start, end, best,
                                                     [p[1] for p in group]))
                    start = end = pair[1]["t"]
                    group, best = [pair], pair[0]
            ranges.append(frame_index._range(clip_id, group[0][1]["filename"],
                                             start, end, best,
                                             [p[1] for p in group]))
            for score, frame in members:
                result_frames.append({"clip_id": clip_id, "t": frame["t"], "score": score})
        ranges.sort(key=lambda row: -row["best_score"])
        report = {"results": result_frames, "ranges": ranges[:rules["top_k"]], "refused": None}
        for query in eval_set["queries"]:
            if query["family"] == "visual":
                query_rows[query["id"]].append(score_visual(query, report, corpus, rules))
    rows = [r for r in query_rows.values() for r in r]
    present = [r for r in rows if r.get("kind") == "present"]
    negative = [r for r in rows if r.get("kind") == "absent"]
    refusals = [r for r in rows if r.get("kind") == "refusal"]
    mean = lambda key, items: round(statistics.fmean(r[key] for r in items
                                                      if r.get(key) is not None), 4) if any(
        r.get(key) is not None for r in items) else None
    return {"samples": samples, "seed": 20261016, "unit": "frame-level uniform random ranking",
            "macro_recall": mean("recall", present),
            "macro_precision": mean("precision", present),
            "negative_return_rate": round(sum(bool(r.get("returned")) for r in negative) /
                                           len(negative), 4) if negative else None,
            "refusal_correct": sum(bool(r.get("refused")) for r in refusals),
            "refusal_queries": len(refusals)}


def enrich_visual_result(eval_set: dict, result: dict, *, floor: float | None = None,
                         frame_index=None, corpus=None, rules: dict | None = None,
                         evaluator_labels_path=None,
                         evaluator_window_scores: dict[str, float] | None = None) -> dict:
    """Attach benchmark protocol and summary to a retrieval_eval result.

    ``floor`` is optional because the current frame retriever has no confidence
    floor. When absent, negative-query abstention is explicitly reported as
    unsupported and zero abstentions are not mistaken for a measured policy.
    """
    queries = {q["id"]: q for q in eval_set["queries"] if q["family"] == "visual"}
    rows = [r for r in result["results"] if r.get("family") == "visual"]
    held_out_assets = set(eval_set.get("held_out_assets", []))
    if held_out_assets:
        from library.tools.retrieval_eval import _frame_spans, overlaps
        for row in rows:
            query = queries[row["id"]]
            returned = [hit for hit in row.get("rows", [])
                        if hit["asset"] in held_out_assets]
            if query.get("positive_frames"):
                positives = _frame_spans({name: times for name, times in
                                          query["positive_frames"].items()
                                          if name in held_out_assets},
                                         eval_set["scoring"]["visual"]["frame_pad_seconds"])
                positives = [(name, span) for name, spans in positives.items() for span in spans]
                touched = sum(any(hit["asset"] == name and overlaps(
                    (hit["start"], hit["end"]), span) for hit in returned)
                              for name, span in positives)
                judged = [hit for hit in returned if not hit.get("ambiguous")]
                row["held_out"] = {"kind": "present", "positive_spans": len(positives),
                                   "spans_touched": touched,
                                   "recall": round(touched / len(positives), 4) if positives else None,
                                   "precision": round(sum(bool(hit.get("hit")) for hit in judged) /
                                                      len(judged), 4) if judged else None,
                                   "returned": len(returned)}
            elif query.get("expect") == "refused":
                row["held_out"] = {"kind": "refusal", "refused": row.get("refused", False)}
            else:
                row["held_out"] = {"kind": "absent", "returned": len(returned),
                                   "abstained": not returned}
    present = [r for r in rows if r.get("kind") == "present"]
    negatives = [r for r in rows if r.get("kind") == "absent"]
    refusals = [r for r in rows if r.get("kind") == "refusal"]
    recalls = [r["recall"] for r in present if r.get("recall") is not None]
    precisions = [r["precision"] for r in present if r.get("precision") is not None]
    categories: dict[str, list[dict]] = {}
    for row in rows:
        for category in _visual_categories(queries[row["id"]]):
            categories.setdefault(category, []).append(row)
        row["categories"] = _visual_categories(queries[row["id"]])
    correct_intervals = sum(
        sum(bool(hit.get("hit")) for hit in r.get("rows", []) if not hit.get("ambiguous"))
        for r in present)
    seconds = [float(r.get("seconds", 0)) for r in rows]
    elapsed = sum(seconds)
    floor_rows = [r for r in negatives
                  if floor is not None and r.get("top_score") is not None
                  and r["top_score"] < floor]
    summary = {
        "protocol": "held-out visual query evaluation; labels and queries are frozen; no tuning",
        "query_count": len(rows),
        "present_query_count": len(present),
        "negative_query_count": len(negatives),
        "macro_recall_ci95": bootstrap_mean_interval(recalls),
        "macro_precision_ci95": bootstrap_mean_interval(precisions, seed=20261015),
        "negative_behavior": {
            "queries": len(negatives), "returned_any": sum(bool(r.get("returned")) for r in negatives),
            "abstained": sum(bool(r.get("abstained")) for r in negatives),
            "false_return_rate": round(sum(bool(r.get("returned")) for r in negatives) /
                                        len(negatives), 4) if negatives else None,
            "floor": floor,
            "floor_abstained": len(floor_rows) if floor is not None else None,
            "floor_metric_status": "measured" if floor is not None else "unsupported: visual index has no score floor",
            "refusal_queries": len(refusals),
            "correctly_refused": sum(bool(r.get("refused")) for r in refusals),
        },
        "latency_seconds": {"per_query": {r["id"]: round(float(r.get("seconds", 0)), 4)
                                           for r in rows},
                            "mean": round(statistics.fmean(seconds), 4) if seconds else None,
                            "p95": round(sorted(seconds)[math.ceil(.95 * len(seconds)) - 1], 4)
                            if seconds else None},
        "peak_process_rss_mb": _peak_rss_mb(),
        "useful_interval_cost": {"useful_intervals": correct_intervals,
                                 "query_seconds": round(elapsed, 4),
                                 "seconds_per_useful_interval": round(elapsed / correct_intervals, 4)
                                 if correct_intervals else None,
                                 "hosted_api_cost_usd": 0.0,
                                 "cost_basis": "local inference; hosted API charges only (local compute cost excluded)"},
        "category_failures": {
            category: {"queries": len(items),
                      "mean_recall": round(statistics.fmean(
                          r["recall"] for r in items if r.get("kind") == "present"
                          and r.get("recall") is not None), 4)
                      if any(r.get("kind") == "present" and r.get("recall") is not None
                             for r in items) else None,
                      "failed_query_ids": [r["id"] for r in items
                                           if r.get("kind") == "present" and
                                           (r.get("recall") or 0) < 1.0]}
            for category, items in sorted(categories.items())
        },
        "retrieval_diagnostics": _retrieval_diagnostics(
            eval_set, rows, frame_index, corpus, held_out_assets=held_out_assets,
            frame_pad_seconds=eval_set.get("scoring", {}).get("visual", {}).get(
                "frame_pad_seconds", 5.0),
            top_k=eval_set.get("scoring", {}).get("visual", {}).get("top_k", 5)),
        "baseline": _random_ranking_metrics(eval_set, frame_index, corpus, rules)
                    if frame_index is not None and corpus is not None and rules is not None
                    else {"name": "uniform random frame ranking", "status": "not measured"},
    }
    if frame_index is not None:
        from library.tools.analysis.footage_frames import (
            FRAME_EMBEDDINGS_FILE,
            FRAME_INDEX_FILE,
        )
        summary["index_provenance"] = {
            "built_at": frame_index.payload.get("built_at"),
            "frame_count": frame_index.payload["frame_count"],
            "sampling_step_seconds": frame_index.payload.get("step_s"),
            "clip_model": frame_index.payload.get("clip_model"),
            "embed_backend": frame_index.payload.get("embed_backend"),
            "index_sha256": _file_sha256(frame_index.index_dir / FRAME_INDEX_FILE),
            "embeddings_sha256": _file_sha256(frame_index.index_dir / FRAME_EMBEDDINGS_FILE),
            "index_bytes": sum(path.stat().st_size for path in frame_index.index_dir.iterdir()
                                if path.is_file()),
            "build_cost_status": "unavailable: current index artifact does not retain decode or build wall time",
        }
    if held_out_assets:
        held_present = [r["held_out"] for r in rows
                        if r.get("held_out", {}).get("kind") == "present"]
        held_negative = [r["held_out"] for r in rows
                         if r.get("held_out", {}).get("kind") == "absent"]
        summary["held_out"] = {
            "assets": sorted(held_out_assets), "queries": len(rows),
            "present_queries": len(held_present),
            "macro_recall_ci95": bootstrap_mean_interval(
                [r["recall"] for r in held_present if r.get("recall") is not None], seed=20261017),
            "macro_precision_ci95": bootstrap_mean_interval(
                [r["precision"] for r in held_present if r.get("precision") is not None], seed=20261018),
            "per_query": {r["id"]: r["held_out"] for r in rows},
            "negative_queries": len(held_negative),
            "negative_abstained": sum(bool(r["abstained"]) for r in held_negative),
        }
    result["visual_benchmark"] = summary
    if evaluator_labels_path is not None:
        # Human pilot labels stay separate from the frozen benchmark inputs.
        from library.tools.blind_label_pilot import label_summary
        summary["human_calibration_labels"] = label_summary(
            evaluator_labels_path, kind="search")
        if evaluator_window_scores is not None:
            from library.tools.blind_label_pilot import calibrate_abstention
            summary["human_abstention_calibration"] = calibrate_abstention(
                evaluator_labels_path, evaluator_window_scores)
    return result

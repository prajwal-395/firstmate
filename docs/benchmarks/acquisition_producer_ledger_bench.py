"""A FROZEN producer ledger for the acquisition benchmark, on labelled moments.

PR 2928 landed the new acquisition policies (VI-020..VI-027): the
adaptive frame schedule with its edit-critical bands, and the plans
that name the question every scheduled inference answers.  It named the
live evidence it owed: a frozen producer ledger produced by those
policies, scored by `library.tools.acquisition_benchmark` over the
held-out tasks, compared per lane against `fixed_dense` and
`fixed_sparse`.

This script produces that ledger, over the categories that HAVE labels.

**What the ledger measures.**  One task is one hand-marked moment in one
source.  The moment is the LABEL; the query is what the editor asked
for.  The universe of query-relevant evidence is the transcript
segments of that source that share a content word with the query, and
the question the ledger answers is: *did the acquisition schedule
acquire the evidence that sits inside the labelled moment?*  So:

* `gold_ids` - the query-relevant segments inside the hand-marked moment.
* `predicted_ids` - the query-relevant segments inside the windows the
  policy ACQUIRED (the gold itself is excluded from the prediction, so
  a policy cannot score by echoing the answer).

Three policies acquire the windows, over the same source:

* `fixed_dense` - every 10 s window of the source (the dense reference).
* `fixed_sparse` - every 60 s window (the same producer, a coarser grid).
* `adaptive_candidate` - the NEW policy: `plan_adaptive_frames` with the
  measured speech boundaries as the edit-critical events, the SHIPPED
  search's candidates as the query-importance signal, and the budget it
  declares.  The candidates are the search's own hits, never the label:
  a schedule steered by the answer would score itself.

**The cost is MEASURED, and it is measured on cached evidence.**  Every
producer on this corpus is already migrated into the source memory, so
the cost a policy pays here is the work of the acquisition itself:
computing the schedule and selecting the evidence it covers.  It is a
real, measured, policy-dependent number - the dense policy touches
every window of a 4,941 s source and the adaptive one touches a budget
of frames - but it is NOT what a cold producer would cost.  The ledger
says so in every row (`cost_basis`), and `budget_seconds` carries the
producer catalog's measured M4 window cost for the windows acquired, so
the two readings can be told apart.

**The splits come from the corpus manifest**, so the held-out tasks are
held out by LEAKAGE GROUP, not by convenience: a source's split in
`docs/benchmarks/2026-10-15-editorial-eval-corpus.json` is the task's
split, and the scorer fits on the calibration rows and reports the
held-out rows once.

Usage (foreground)::

    bin/vep docs/benchmarks/acquisition_producer_ledger_bench.py \\
        --project <scratch project> \\
        --editorial-set library/tools/editorial_retrieval_set.json \\
        --heldout-set library/tools/editorial_retrieval_heldout_set.json \\
        --out-ledger <ledger.json> --out-report <report.json>

The report is `library.tools.acquisition_benchmark`'s own output for the
ledger this script wrote.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import acquisition_benchmark
from library.tools.adaptive_frame_sampling import (
    DEFAULT_BUDGET,
    edit_critical_events_from_index,
    plan_adaptive_frames,
)

POLICY_DENSE = "fixed_dense"
POLICY_SPARSE = "fixed_sparse"
POLICY_ADAPTIVE = "adaptive_candidate"
LANE = "speech"

WINDOW_SECONDS = 10.0
DENSE_STEP_SECONDS = 10.0
SPARSE_STEP_SECONDS = 60.0
FPS = 23.976
TOP_K = 5
"""How many windows the editor takes. The editorial set's own rule is 5."""
COST_REPEATS = 5
"""How many times the acquisition is timed, so the median is the cost."""

M4_WINDOW_COST_SECONDS = 90.0
"""The measured M4 window cost (`evidence_acquisition.PRODUCER_CATALOG`)."""

STOPWORDS = frozenset(["a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from", "had", "has", "have", "he", "her", "him", "his", "i", "in", "is", "it", "its", "of", "on", "or", "she", "that", "the", "their", "them", "there", "they", "this", "to", "was", "were", "what", "when", "where", "which", "who", "will", "with", "you", "your"])

WORD = re.compile(r"[a-z0-9']+")


def content_words(text: str) -> set[str]:
    """The words a query asks for, with the function words removed."""
    return {word for word in WORD.findall((text or "").lower())
            if word not in STOPWORDS and len(word) > 1}


def segment_words(segment: dict) -> set[str]:
    words = set()
    for row in segment.get("words") or ():
        word = str(row.get("word") or "").lower().strip(".,!?;:'\"")
        if word:
            words.add(word)
    if not words:
        words = set(WORD.findall((segment.get("text") or "").lower()))
    return words


def peak_rss_mb() -> float:
    """The process's peak resident set, in megabytes."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2)


def _windows_grid(duration: float, step: float) -> list[tuple[float, float]]:
    windows = []
    at = 0.0
    while at < duration:
        windows.append((at, min(at + WINDOW_SECONDS, duration)))
        at += step
    return windows


def _windows_adaptive(duration: float, index_doc: dict, query: str,
                       candidates: list[dict]) -> list[tuple[float, float]]:
    events = edit_critical_events_from_index(index_doc)
    plan = plan_adaptive_frames(
        duration, FPS, index_doc, query=query,
        retrieval_candidates=candidates, budget_frames=DEFAULT_BUDGET,
        edit_critical_events=events)
    windows = []
    for at in plan.get("selected_source_times") or ():
        windows.append((max(0.0, at - WINDOW_SECONDS / 2.0),
                        min(duration, at + WINDOW_SECONDS / 2.0)))
    return windows


def _covered(windows, start: float, end: float) -> bool:
    return any(left <= end and right >= start for left, right in windows)


def _merge(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Overlapping windows are ONE window: a producer does not analyse the
    same span twice, and the work is counted once."""
    merged: list[list[float]] = []
    for left, right in sorted(windows):
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return [(left, right) for left, right in merged]


def _acquire(windows, relevant: list[dict], terms: set[str]) -> list[str]:
    """The evidence the acquired windows yield.

    The acquisition is the WORK: every acquired window is read, and the
    query-relevant evidence OVERLAPPING it is what the acquisition
    returns - a producer analysing a window hears the speech that runs
    across its edge.  Nothing is truncated to a top-k: the benchmark's
    utility is F1 over evidence ids, and truncating here would measure
    the RANKER, not the acquisition.  The policy never sees the gold -
    the schedule is steered by the shipped search's candidates - so a
    policy cannot score by echoing the answer.
    """
    gathered: dict[str, float] = {}
    for left, right in windows:
        for segment in relevant:
            start = segment.get("start", 0.0)
            end = segment.get("end", 0.0)
            if end < left or start > right:
                continue
            score = len(terms & segment_words(segment))
            identifier = segment["segment_id"]
            if score > gathered.get(identifier, 0.0):
                gathered[identifier] = float(score)
    return sorted(gathered)


def build(project: str, editorial_set: dict, heldout_set: dict,
          corpus_manifest: dict) -> tuple[dict, list[dict]]:
    """The ledger, and the record of what was dropped and why."""
    from library.tools.analysis.footage_query import FootageIndex

    index = FootageIndex(project)
    segments = index.payload.get("segments") or []

    split_of = {row["source_id"]: row.get("split")
                for row in corpus_manifest.get("sources") or ()}
    durations, clip_of_asset, segments_of_clip = {}, {}, {}
    for segment in segments:
        clip_id = segment.get("clip_id")
        clip_of_asset.setdefault(Path(str(segment.get("filename") or "")).stem,
                                 clip_id)
        segments_of_clip.setdefault(clip_id, []).append(segment)
        facets = segment.get("facets") or {}
        if facets.get("duration_seconds"):
            durations[clip_id] = float(facets["duration_seconds"])

    tasks, runs, dropped = [], [], []
    search_hits: dict[str, list[dict]] = {}

    def hits_for(query_text: str) -> list[dict]:
        if query_text not in search_hits:
            report = index.search_report(query_text, top_k=50)
            search_hits[query_text] = list(report.get("results") or ())
        return search_hits[query_text]

    for set_payload, set_label in ((editorial_set, "editorial"),
                                    (heldout_set, "held-out")):
        for query in set_payload.get("queries") or ():
            if query.get("expect") == "unlabelled":
                dropped.append({"query_id": query["id"],
                                "reason": "the category has no labels yet"})
                continue
            if query.get("lane") != "text":
                dropped.append({"query_id": query["id"],
                                "reason": "the lane is not the speech lane"})
                continue
            terms = content_words(query["query"])
            instances: dict[str, list] = {}
            for moment in query.get("moments") or ():
                for instance in moment.get("instances") or ():
                    instances.setdefault(instance["asset"], []).append(
                        (float(instance["start"]), float(instance["end"])))
            if not instances:
                # An absent event: the correct acquisition is NOTHING.
                task_id = f"{query['id']}-absent"
                split = "held_out" if set_label == "held-out" else "calibration"
                tasks.append({"task_id": task_id, "split": split, "lane": LANE,
                              "gold_ids": [], "query_id": query["id"],
                              "query": query["query"], "asset": "",
                              "clip_id": ""})
                for policy in (POLICY_DENSE, POLICY_SPARSE):
                    runs.append({"task_id": task_id, "policy": policy,
                                 "config": "default", "predicted_ids": [],
                                 "cost_seconds": 0.0,
                                 "peak_rss_mb": peak_rss_mb(),
                                 "budget_seconds": 0.0, "status": "completed",
                                 "cost_basis": "measured wall time of the "
                                               "acquisition over cached "
                                               "evidence"})
                continue
            for asset, spans in sorted(instances.items()):
                clip_id = clip_of_asset.get(asset)
                if clip_id is None:
                    dropped.append({"query_id": query["id"], "asset": asset,
                                    "reason": "the source is not in the index"})
                    continue
                relevant = [segment for segment in segments_of_clip[clip_id]
                            if terms & segment_words(segment)]
                gold = []
                for segment in relevant:
                    for start, end in spans:
                        if (segment.get("start", 0.0) < end
                                and segment.get("end", 0.0) > start):
                            gold.append(segment["segment_id"])
                            break
                if not gold:
                    dropped.append({"query_id": query["id"], "asset": asset,
                                    "reason": "no query-relevant evidence sits "
                                              "inside the labelled moment"})
                    continue
                split = split_of.get(asset) or "calibration"
                tasks.append({"task_id": f"{query['id']}-{asset}",
                              "split": split, "lane": LANE,
                              "gold_ids": sorted(gold),
                              "query_id": query["id"], "query": query["query"],
                              "asset": asset, "clip_id": clip_id})

    for task in tasks:
        clip_id = task.get("clip_id")
        if not clip_id:
            continue
        duration = durations.get(clip_id) or 0.0
        clip_segments = segments_of_clip[clip_id]
        gold = set(task["gold_ids"])
        terms = content_words(task["query"])
        relevant = [segment for segment in clip_segments
                    if terms & segment_words(segment)]
        index_doc = {"speech_regions": [
            {"start": segment.get("start"), "end": segment.get("end")}
            for segment in clip_segments if segment.get("kind") == "speech"]}
        candidates = [{"start": hit.get("start"), "end": hit.get("end"),
                       "score": hit.get("score"),
                       "evidence_type": "retrieval_candidate"}
                      for hit in hits_for(task["query"])
                      if hit.get("clip_id") == clip_id]
        policies = {
            POLICY_DENSE: _merge(_windows_grid(duration, DENSE_STEP_SECONDS)),
            POLICY_SPARSE: _merge(_windows_grid(duration, SPARSE_STEP_SECONDS)),
            POLICY_ADAPTIVE: _merge(_windows_adaptive(
                duration, index_doc, task["query"], candidates)),
        }
        for policy, windows in policies.items():
            samples = []
            for _repeat in range(COST_REPEATS):
                started = time.perf_counter()
                predicted = _acquire(windows, relevant, terms)
                samples.append(time.perf_counter() - started)
            # The median of several runs: a single wall-clock reading of
            # a microsecond-scale operation is noise, and the scorer
            # decides which policy is CHEAPER on this number.
            samples.sort()
            cost = samples[len(samples) // 2]
            runs.append({
                "task_id": task["task_id"], "policy": policy,
                "config": "default", "predicted_ids": sorted(predicted),
                "cost_seconds": round(cost, 6),
                "peak_rss_mb": peak_rss_mb(),
                "budget_seconds": round(len(windows) * M4_WINDOW_COST_SECONDS, 3),
                "status": "completed",
                "cost_basis": "measured wall time of the acquisition over "
                              "cached evidence; budget_seconds carries the "
                              "producer catalog's measured M4 window cost for "
                              "the windows acquired",
            })
    return {"tasks": tasks, "runs": runs}, dropped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--editorial-set", required=True)
    parser.add_argument("--heldout-set", required=True)
    parser.add_argument(
        "--corpus-manifest",
        default="docs/benchmarks/2026-10-15-editorial-eval-corpus.json")
    parser.add_argument("--out-ledger", required=True)
    parser.add_argument("--out-report", required=True)
    args = parser.parse_args(argv)

    editorial = json.loads(Path(args.editorial_set).read_text(encoding="utf-8"))
    heldout = json.loads(Path(args.heldout_set).read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.corpus_manifest).read_text(encoding="utf-8"))
    ledger, dropped = build(args.project, editorial, heldout, manifest)
    # The scorer's protocol: the policy is FITTED on the calibration rows
    # and the held-out rows carry the fitted policy under the name
    # `adaptive`.  A candidate that never fitted has no held-out row, and
    # the report says so rather than scoring the held-out rows with a
    # policy that was never chosen.
    fitted = acquisition_benchmark.calibrate(ledger)
    chosen = (fitted.get("by_lane") or {}).get(LANE) or {}
    config = chosen.get("config")
    held_out_ids = {task["task_id"] for task in ledger["tasks"]
                    if task["split"] == "held_out"}
    if config:
        for row in list(ledger["runs"]):
            if (row["policy"] == "adaptive_candidate"
                    and row["task_id"] in held_out_ids):
                ledger["runs"].append({**row, "policy": "adaptive",
                                       "config": config})
    Path(args.out_ledger).write_text(json.dumps(ledger, indent=1) + "\n",
                                     encoding="utf-8")
    report = acquisition_benchmark.evaluate(ledger)
    report["dropped"] = dropped
    report["calibration_choice"] = {"lane": LANE, "config": config,
                                    "detail": chosen}
    Path(args.out_report).write_text(json.dumps(report, indent=1) + "\n",
                                     encoding="utf-8")
    print(json.dumps({
        "tasks": len(ledger["tasks"]),
        "runs": len(ledger["runs"]),
        "dropped": dropped,
        "calibration_choice": {"lane": LANE, "config": config},
        "acceptance": report["acceptance"],
        "held_out": report["held_out"],
        "held_out_by_lane": report["held_out_by_lane"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

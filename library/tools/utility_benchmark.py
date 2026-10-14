"""Read-only end-to-end utility benchmark for the FI-01 podcast collection.

This composes the existing FI-01 corpus measurement and retrieval evaluator.
It never rebuilds project analysis, edits a project, or connects to Resolve.
Stages that require those actions are explicit `unmeasured` rows, not zeroes.

Run with::

    bin/vep -m library.tools.heavy_work_lock run --owner vep-utility-benchmark -- \
      bin/vep -m library.tools.utility_benchmark --project <analysis-only-collection> \
      --memory-root <source-memory-root> --out <ledger.json>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import resource
import sys
import time
from collections.abc import Callable
from pathlib import Path

from library.tools import retrieval_eval
from library.tools.analysis import bench_footage_intelligence as fi01

PHASES = (
    "cold_ingest", "warm_ingest", "first_query", "subsequent_queries",
    "edit_preparation", "final_edit_execution",
)


def _rss_mb() -> float | None:
    try:
        scale = 1 / (1024 * 1024) if sys.platform == "darwin" else 1 / 1024
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale, 1)
    except (AttributeError, OSError):
        return None


def _measure(fn: Callable[[], dict]) -> tuple[dict, float, float | None]:
    before = _rss_mb()
    started = time.perf_counter()
    value = fn()
    wall = round(time.perf_counter() - started, 3)
    after = _rss_mb()
    peak = max((x for x in (before, after) if x is not None), default=None)
    return value, wall, peak


def _phase(name: str, *, status: str, wall_s=None, peak_rss_mb=None,
           model_invocations_by_model=None, cache_hits=None, detail=None) -> dict:
    return {
        "phase": name, "status": status, "wall_s": wall_s,
        "peak_rss_mb": peak_rss_mb,
        "model_invocations_by_model": model_invocations_by_model,
        "cache_hits": cache_hits, "detail": detail or {},
    }


def run(project: Path, memory_root: Path, corpus: Path,
        query_set: Path, families: list[str]) -> dict:
    manifest = fi01._load_manifest(corpus)
    fi_report, fi_wall, fi_rss = _measure(lambda: fi01.run_corpus(
        manifest, memory_root))

    with query_set.open(encoding="utf-8") as stream:
        eval_set = json.load(stream)
    selected = [q for q in eval_set["queries"] if q["family"] in families]
    if not selected:
        raise ValueError("the selected query families contain no queries")
    selected_set = dict(eval_set, queries=selected)
    search_report, search_wall, search_rss = _measure(lambda: retrieval_eval.run(
        str(project), selected_set, families=families))
    rows = search_report["results"]
    first, subsequent = rows[0], rows[1:]
    phases = [
        _phase("cold_ingest", status="unmeasured", detail={
            "reason": "The registered FI-01 source-memory root is already populated; "
                     "a true cold ingest requires an isolated scratch project and "
                     "fresh source-memory root.", "read_only": True}),
        _phase("warm_ingest", status="unmeasured", detail={
            "reason": "No ingest was executed: the supplied FI-01 project is "
                     "read-only and the harness does not write its analysis outputs.",
            "read_only": True}),
        _phase("first_query", status="measured", wall_s=first["seconds"],
               peak_rss_mb=search_rss,
               detail={"query_id": first["id"], "family": first["family"],
                       "recall": first.get("recall"),
                       "results": first.get("returned", first.get("spans_returned"))}),
        _phase("subsequent_queries", status="measured",
               wall_s=round(sum(r["seconds"] for r in subsequent), 3),
               peak_rss_mb=search_rss,
               detail={"query_count": len(subsequent),
                       "query_ids": [r["id"] for r in subsequent],
                       "recall_by_query": {r["id"]: r.get("recall")
                                           for r in subsequent}}),
        _phase("edit_preparation", status="unmeasured", detail={
            "reason": "The available collection has no edit-ready timeline; "
                     "preparation requires project writes outside this read-only run.",
            "read_only": True}),
        _phase("final_edit_execution", status="unmeasured", detail={
            "reason": "Resolve access and timeline execution are prohibited for this task.",
            "read_only": True}),
    ]
    return {
        "schema_version": 1, "benchmark": "ren-end-to-end-utility",
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "code_revision": fi01._git_head(), "project": str(project),
        "corpus_manifest": str(corpus), "source_memory_root": str(memory_root),
        "phase_order": list(PHASES), "phases": phases,
        "measurements": {
            "fi01_cached_corpus_read": {"wall_s": fi_wall,
                                        "peak_rss_mb": fi_rss,
                                        "metrics": fi_report},
            "retrieval": {"wall_s": search_wall, "peak_rss_mb": search_rss,
                          "summary": search_report["summary"], "queries": rows},
        },
        "label_coverage": {
            "registered_query_set": eval_set["name"],
            "registered_queries_run": [q["id"] for q in selected],
            "unlabelled_interaction_query": {
                "query": "a person drinking from a cup", "status": "unmeasured",
                "reason": "No positive hand-checked drinking frame is registered in "
                         "the FI-01 set; checked sample frames do not establish an event.",
            },
        },
        "model_invocation_counts": None,
        "model_invocation_counts_reason": (
            "The existing read-only retrieval evaluator does not expose per-model "
            "invocation counters on this path; null means unmeasured, not zero."),
        "cache_hits": None,
        "cache_hits_reason": (
            "The analysis-only project has no run-local cache ledger for these reads."),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True,
                        help="read-only FI-01 analysis-only project/collection")
    parser.add_argument("--memory-root", type=Path, required=True,
                        help="source-memory root for the FI-01 corpus")
    parser.add_argument("--corpus", type=Path,
                        default=Path("docs/benchmarks/2026-10-12-fi01-corpus-manifest.json"))
    parser.add_argument("--query-set", type=Path,
                        default=Path("library/tools/retrieval_eval_set.json"))
    parser.add_argument("--families", nargs="+", choices=retrieval_eval.FAMILIES,
                        default=["speech", "visual", "person"])
    parser.add_argument("--out", type=Path, required=True,
                        help="JSON ledger path; write outside the repository")
    args = parser.parse_args(argv)
    report = run(args.project.resolve(), args.memory_root.resolve(), args.corpus,
                 args.query_set, args.families)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    measured = sum(p["status"] == "measured" for p in report["phases"])
    print(f"utility benchmark: {measured}/{len(PHASES)} phases measured; wrote {args.out}")
    print(json.dumps(report["measurements"]["retrieval"]["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

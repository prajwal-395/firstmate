"""The CANDIDATE retrieval result: the plan-driven search, scored.

`library.tools.retrieval_eval` scores the SHIPPED search - one fused
score per query, over the text lane or the frame lane.  That is the
INCUMBENT (VI-009: a change to retrieval is measured against what it
replaces).

This script scores the CANDIDATE that PR 2907 landed: the plan-driven
search (`library.tools.retrieval.search.search_footage`), which
decomposes the question into typed requirements, runs the lane each
requirement names, collapses duplicate windows, attaches the support
references, and says whether an empty answer is "not there" or "not
looked for".

It writes the result in the SAME shape `ren eval-search --out-json`
writes, so `library.tools.retrieval.incumbent` can compare the two
without either of them being re-read by hand:

    bin/vep docs/benchmarks/editorial_retrieval_candidate_bench.py \\
        --project <scratch project> --set <editorial set> \\
        --out-json <candidate.json> [--incumbent <incumbent.json>]

**Two candidates are written, and the difference between them is a
measurement.**  `candidate_shipped` is the search as it ships.  The
text lane runs on EVERY query there, whether the plan names a textual
requirement or not, so a picture question comes back with speech
windows attached - windows the editor did not ask for, which VI-010
prices.  `candidate_planned` drops the rows whose lane the plan does
not name, and the two differ only in that.  Which of them is better is
a measurement, and the report says which.

Nothing here decides anything: it reports, and the comparison is
`not_decided` whenever the two halves of retrieval disagree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import retrieval_eval
from library.tools.retrieval import incumbent as incumbent_module
from library.tools.retrieval import search as retrieval_search


def _rows_for_scoring(hits: list) -> list:
    """The candidate's windows in the shape the scorer reads."""
    return [{"clip_id": hit.get("clip_id"),
             "start": hit.get("start"),
             "end": hit.get("end"),
             "score": hit.get("score"),
             "dense_score": hit.get("dense_score"),
             "text": (hit.get("text") or "")[:120]}
            for hit in hits]


def _planned_rows(hits: list, plan: dict) -> list:
    """Only the windows whose lane the plan actually names."""
    lanes = {requirement.get("lane")
             for requirement in plan.get("requirements") or ()
             if requirement.get("mandatory")}
    kinds = retrieval_search.LANE_KINDS
    allowed = set()
    for lane in lanes:
        allowed.update(kinds.get(lane, ()))
    if not allowed:
        return list(hits)
    return [hit for hit in hits if hit.get("kind") in allowed]


def run(project: str, eval_set: dict, *, top_k: int = 5) -> dict:
    """Score the candidate on every query of the set."""
    corpus = retrieval_eval.Corpus(project, eval_set)
    rules = eval_set["scoring"].get("editorial", retrieval_eval.EDITORIAL_RULES)
    shipped_results, planned_results = [], []
    for query in eval_set["queries"]:
        started = time.perf_counter()
        answer = retrieval_search.search_footage(project, query["query"],
                                                  top_k=top_k)
        plan = answer.get("plan") or {}
        hits = answer.get("hits") or []
        rows = _rows_for_scoring(hits)
        # A lane that REFUSED is not a lane that found nothing: the
        # frame lane refuses an action query by design, and the scorer
        # reads that refusal as the answer it is.
        visual = (answer.get("lanes") or {}).get("visual") or {}
        refusal = visual.get("refused")
        if not isinstance(refusal, dict):
            error = visual.get("error")
            refusal = error if isinstance(error, dict) else None
        report = {"results": rows, "refused": refusal, "error": None,
                  "degraded": None}
        scored = retrieval_eval.score_editorial(query, report, corpus, rules)
        scored["seconds"] = round(time.perf_counter() - started, 3)
        scored["candidate"] = {
            "lanes": answer.get("lanes"),
            "lanes_ran": answer.get("lanes_ran"),
            "answer_status": answer.get("answer_status"),
            "answer_state": answer.get("answer_state"),
            "requirements": answer.get("requirements"),
            "dedup": answer.get("dedup"),
            "support": answer.get("support"),
            "plan_modalities": sorted(set(plan.get("modalities") or ())),
            "plan_lanes": sorted({requirement.get("lane")
                                  for requirement in plan.get("requirements") or ()
                                  if requirement.get("mandatory")}),
        }
        shipped_results.append(scored)

        planned_hits = _planned_rows(hits, plan)
        planned = retrieval_eval.score_editorial(
            query, {"results": _rows_for_scoring(planned_hits),
                    "refused": refusal, "error": None, "degraded": None},
            corpus, rules)
        planned["seconds"] = scored["seconds"]
        planned["candidate"] = dict(scored["candidate"])
        planned["candidate"]["dropped_windows"] = len(hits) - len(planned_hits)
        planned_results.append(planned)
        print(f"[candidate] {scored['id']:5s} {scored['seconds']:7.2f} s "
              f"hits={len(hits)} planned={len(planned_hits)}", file=sys.stderr)

    return {
        "eval_set": eval_set["name"],
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": os.path.basename(os.path.abspath(project)),
        "assets_missing_from_project": corpus.missing,
        "candidate_shipped": {
            "results": shipped_results,
            "summary": retrieval_eval.summarise(shipped_results, {}),
        },
        "candidate_planned": {
            "results": planned_results,
            "summary": retrieval_eval.summarise(planned_results, {}),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--set", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--incumbent", default="",
                        help="the incumbent result JSON to compare against")
    parser.add_argument("--comparison-out", default="",
                        help="where to write the incumbent comparison")
    args = parser.parse_args(argv)

    with open(args.set, encoding="utf-8") as handle:
        eval_set = json.load(handle)
    result = run(args.project, eval_set)
    Path(args.out_json).write_text(json.dumps(result, indent=1) + "\n",
                                    encoding="utf-8")
    print(json.dumps(result["candidate_shipped"]["summary"].get("editorial"),
                     indent=1))
    if args.incumbent:
        with open(args.incumbent, encoding="utf-8") as handle:
            incumbent_result = json.load(handle)
        for name in ("candidate_shipped", "candidate_planned"):
            comparison = incumbent_module.compare(
                incumbent_result, result[name], families=["editorial"])
            print(f"\n=== incumbent vs {name} ===")
            print(json.dumps(comparison["macro"], indent=1))
            print("verdict:", comparison["verdict"], "-", comparison["verdict_reason"])
            if args.comparison_out:
                path = Path(args.comparison_out).with_name(
                    f"{Path(args.comparison_out).stem}-{name}.json")
                path.write_text(json.dumps(comparison, indent=1) + "\n",
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

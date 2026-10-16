"""L5 calibration/held-out dry-run: planned cost vs dense baseline.

Runs requirement-graph parsing plus acquisition planning over fixed
query lists against fixture Project Models. No model is loaded, no
media is read, no producer executes: every cost is the catalog's
declared per-window figure (measured points labeled measured, the
rest planning estimates). Prints one JSON report.

Calibration queries choose the planner settings (selectivity priors,
top-k windows, cheap-first ordering). Held-out queries run once,
after the settings freeze, and are reported separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import evidence_acquisition as ea
from library.tools import requirement_graph as rg

CALIBRATION_QUERIES = [
    "speaker discussing revenue while a chart is visible",
    "footage with no visible people",
    "person opens the box and then shows the product",
    "where does the text read revenue growth appear on screen",
    "find laughter during the applause",
    "the person on the left holding the product",
]

HELD_OUT_QUERIES = [
    "visible speaker with synchronized dialogue",
    "static wide shots with low motion",
    "show me the moment she picks up the phone",
    "where does the speaker discuss pricing",
]

DURATION = 120.0
SCOPE = {"clock_id": "clk", "asset_id": "asset1"}


def _word(oid, start, end, text):
    return {"id": oid, "provenance_id": "prov-digest1-m1",
            "asset_id": "asset1", "modality": "speech_word",
            "interval": {"clock_id": "clk", "start": start, "end": end},
            "payload": {
                "text": text,
                "evidence_contract": {
                    "measurement_type": "speech.word", "value": text,
                    "coverage_status": "complete", "coverage": "complete",
                    "freshness": "fresh", "verified_status": "verified",
                    "producer_version": "fixture-v1"}}}


def fixture_model(query_index: int) -> dict:
    """Partial evidence: even queries share one transcript word so
    conjunction narrowing and reuse have something to work with."""
    words = [
        _word("w-rev", 10000, 20000, "revenue discussion here"),
        _word("w-price", 40000, 50000, "pricing details follow"),
        _word("w-other", 80000, 90000, "unrelated remark"),
    ]
    return {"observations": words if query_index % 2 == 0 else []}


def dense_baseline_cost(missing_lanes, duration, scope) -> float:
    """Exhaustive reference: costliest standard-or-heavier eligible
    producer per lane across the full scope, no narrowing, no reuse."""
    total = 0.0
    for lane in missing_lanes:
        entries = [row for row in ea.PRODUCER_CATALOG.get(lane, ())
                   if row["tier"] in ("standard", "heavy")]
        if not entries:
            entries = ea.PRODUCER_CATALOG.get(lane, ())
        if not entries:
            continue
        picked = max(entries,
                     key=lambda row: row["cost_seconds_per_window"])
        window = picked.get("window_seconds") or duration
        start = float(scope.get("start_s", 0.0))
        end = min(duration, float(scope.get("end_s", duration)))
        import math
        windows = max(1, math.ceil((end - start) / window))
        total += windows * float(picked["cost_seconds_per_window"])
    return round(total, 3)


def run_queries(queries, label):
    rows = []
    for index, query in enumerate(queries):
        model = fixture_model(index)
        graph = rg.parse_query(query)
        compiled = rg.compile_graph(graph)
        plan = ea.plan_acquisition(
            compiled, model, source_digest="digest1",
            duration_seconds=DURATION, scope=dict(SCOPE))
        missing = sorted({lane for step in plan["steps"]
                          for lane in [step["lane"]]})
        dense = dense_baseline_cost(missing, DURATION, SCOPE)
        # Reuse pass: the first planned step is already in the ledger.
        ledger = {}
        avoided = 0
        if plan["steps"]:
            first = plan["steps"][0]
            ledger[first["ledger_key"]] = {"status": "acquired"}
            rerun = ea.plan_acquisition(
                compiled, model, source_digest="digest1",
                duration_seconds=DURATION, scope=dict(SCOPE),
                ledger=ledger)
            avoided = len(plan["steps"]) - len(rerun["steps"])
        contradictions = ea.find_contradictions(model, compiled)
        rows.append({
            "query": query,
            "requirements": len(graph["requirements"]),
            "answerable": len(compiled["answerable"]),
            "needs_verification": len(compiled["needs_verification"]),
            "unanswerable": len(compiled["unanswerable"]),
            "planned_steps": len(plan["steps"]),
            "planned_cost_s": plan["estimated_cost_seconds"],
            "dense_cost_s": dense,
            "reuse_avoided": avoided,
            "contradictions": len(contradictions),
            "measured_cost_steps": sum(
                1 for step in plan["steps"] if step["measured_cost"]),
        })
    planned = round(sum(row["planned_cost_s"] for row in rows), 3)
    dense = round(sum(row["dense_cost_s"] for row in rows), 3)
    return {"split": label, "queries": rows,
            "totals": {
                "n_queries": len(rows),
                "planned_cost_s": planned,
                "dense_cost_s": dense,
                "saved_vs_dense_s": round(dense - planned, 3),
                "reuse_avoided": sum(row["reuse_avoided"] for row in rows),
                "unanswerable": sum(row["unanswerable"] for row in rows),
                "needs_verification": sum(
                    row["needs_verification"] for row in rows),
            }}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["calibration", "held_out"],
                        default="calibration")
    args = parser.parse_args(argv)
    queries = (CALIBRATION_QUERIES if args.split == "calibration"
               else HELD_OUT_QUERIES)
    print(json.dumps(run_queries(queries, args.split), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

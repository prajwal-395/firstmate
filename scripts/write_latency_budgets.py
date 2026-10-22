#!/usr/bin/env python3
"""Write the latency-budget baseline table from measured runs.

The baseline table is the ENGINE'S measured cost for one unit of each
operation class.  It is written from the ledger, never typed by hand,
so that every number in it names the runs it came from.

The runs this reads are Ren-owned scratch copies.  A captain's project
is never measured here: a budget set on somebody else's footage would
be a budget for their content, not for the engine.

    python3 scripts/write_latency_budgets.py --out library/tools/latency_budgets.json

Every class that has no completed sample is written as UNMEASURED, with
the reason.  A missing measurement is never a zero, and a class is
never given a target the engine did not produce.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools import latency_budget


def scratch_ledgers(root: Path) -> list[Path]:
    """The Ren-owned scratch ledgers under a projects root."""
    if not root.is_dir():
        return []
    return sorted(root.glob("ren-scratch-*/pipeline_output/logs/perf_ledger.jsonl"))


def qualification_ledgers(root: Path) -> list[Path]:
    """Ren's own qualification runs' ledgers, under its qualification store.

    The qualification workflow builds and renders on Ren-owned synthetic
    footage, so these are Ren-owned measurements like the scratch copies
    are - and they cover the two operations no scratch project has
    finished yet.  They are read, never written.
    """
    if not root.is_dir():
        return []
    return sorted(root.glob("*/workflow-runs/*/perf_ledger.jsonl"))


def read_deduplicated(paths: list[Path]) -> list[dict]:
    """The rows of several ledgers, with the copies of one row kept once.

    The scratch copies share their history: the same run was written
    into four of them.  Counting a row once is what makes the sample
    count a count of RUNS and not a count of folders.
    """
    seen: set[str] = set()
    rows: list[dict] = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                key = json.dumps(row, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows


def _corpus_of(run_id: str) -> str:
    """Which corpus a run belongs to, from the run id's own shape.

    The scratch projects' runs are named `run-<stamp>`; the
    qualification workflow's are `<stamp>`.  A reader of the baseline
    table must be able to tell which corpus a target came from, because
    a target measured on the qualification footage is a target for that
    footage.
    """
    if run_id.startswith("run-"):
        return "ren-scratch copies"
    if run_id.startswith("perf"):
        return "ren-scratch copies"
    return "ren qualification runs"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, int(-(-quantile * len(ordered) // 1)) - 1)
    return ordered[index]


def _source_names(samples) -> str:
    """The runs that produced the target, and the corpus they came from.

    Only the COMPLETED samples count: a refused operation is not what
    the target was measured from, and naming it would say the target
    came from a run that did not happen.
    """
    runs = sorted({s.run_id for s in samples if s.run_id and s.outcome == "ok"})
    if not runs:
        return ""
    corpora = sorted({_corpus_of(run_id) for run_id in runs})
    return (f"{len(runs)} run(s) from {', '.join(corpora)}: "
            + ", ".join(runs[:3])
            + (f", +{len(runs) - 3} more" if len(runs) > 3 else ""))


def build(rows: list[dict], measured_on: str) -> dict:
    """The baseline table, from the rows of the measured runs."""
    measurements = latency_budget.measure(rows, probes=True)
    operations: dict[str, dict] = {}
    for key, operation in latency_budget._operation_index().items():
        measured = measurements[key]
        values = measured.work_seconds()
        entry: dict = {
            "class": latency_budget._class_of(key),
            "unit": operation.unit,
            "signals": [s.label() for s in operation.signals],
            "divisor": operation.divisor,
        }
        if values:
            # The budget is the MEASURED TYPICAL COST of the unit, and
            # the check fires when a class's typical cost rises above
            # it.  One slow run in ten is the weather; a class whose
            # median moved is the regression.  The ceiling helper rounds
            # the target UP, so a sample that EQUALS the target is not
            # reported as over it.
            entry["target_s"] = latency_budget._ceiling(
                _percentile(values, 0.5))
            entry["samples"] = len(values)
            entry["basis"] = (
                f"p50 of {len(values)} completed sample(s) of "
                f"{operation.unit}, broker and scheduler waits removed")
            entry["measured_on"] = measured_on
            entry["source"] = _source_names(measured.samples) or (
                "a live probe of this machine: "
                f"{len(values)} cold derivations")
        else:
            entry["target_s"] = None
            entry["samples"] = 0
            entry["basis"] = (
                "UNMEASURED - no completed sample of this operation exists "
                f"({len(measured.refused)} refused or failed)")
            entry["measured_on"] = ""
            entry["source"] = ""
        operations[key] = entry
    return {
        "schema": latency_budget.BASELINE_SCHEMA,
        "revision": 1,
        "measured_on": measured_on,
        "machine": latency_budget.machine_facts(),
        "operations": operations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root",
                        default=os.environ.get("PIPELINE_PROJECTS_ROOT", ""),
                        help="the projects root holding the Ren scratch copies")
    parser.add_argument("--private-root",
                        default=os.environ.get("REN_QUALIFICATION_MEDIA", ""),
                        help="Ren's qualification media store, holding the "
                             "qualification runs' ledgers")
    parser.add_argument("--out", default=str(
        REPO_ROOT / "library" / "tools" / "latency_budgets.json"))
    parser.add_argument("--measured-on", default="")
    args = parser.parse_args(argv)
    root = Path(args.projects_root or "").expanduser()
    ledgers = scratch_ledgers(root)
    if not ledgers:
        print(f"write_latency_budgets: no Ren scratch ledger under {root}",
              file=sys.stderr)
        return 1
    private = Path(args.private_root or "").expanduser()
    qualification = qualification_ledgers(private) if private else []
    rows = read_deduplicated(ledgers + qualification)
    measured_on = args.measured_on or datetime.now(UTC).strftime("%Y-%m-%d")
    table = build(rows, measured_on)
    out = Path(args.out)
    out.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"wrote {out} from {len(rows)} rows of {len(ledgers)} scratch "
          f"ledger(s) and {len(qualification)} qualification ledger(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

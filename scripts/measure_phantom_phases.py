#!/usr/bin/env python3
"""How many phantom seconds a performance ledger carries, before and after.

A PHANTOM PHASE is a row whose wall time is longer than the run that
recorded it: it cannot be a measurement.  The shipped `geo-podcast`
ledger carries 17 of them - `otio_placement.fusion_comps_s`, 131,422 to
167,228 s each, which is the MACHINE'S UPTIME, for a phase that took
under a minute.  Both cost readers charged those seconds to Resolve.

This script reads a ledger file and reports, on the SAME rows, the
seconds each reader would charge:

  * BEFORE: the raw rows, attributed exactly as
    `critical_path.analyse_run` attributes them - every second to the
    narrowest span that covers it, bucketed by the RESOURCE it is
    charged to.  This is what the ledger read before the clamp.
  * AFTER: the same rows read through `library/tools/ledger_anomaly.py`,
    where a phase longer than its run is a typed anomaly, charged to
    nothing.

The denominators are printed with both, so a reader can see what the
clamp cost and what it saved.

    python3 scripts/measure_phantom_phases.py <ledger.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import critical_path, ledger_anomaly, perf_ledger


def _rows(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _attributed(rows: list[dict], run_id: str) -> tuple[dict[str, float],
                                                        dict[str, float]]:
    """The seconds a run's spans contribute, by resource and by layer.

    The attribution is the critical path's own: every second goes to the
    NARROWEST span that covers it, so a span that reaches across the
    whole run does not swallow the seconds of the work inside it.
    """
    spans = [row for row in rows
             if row.get("run_id") == run_id
             and row.get("kind") == perf_ledger.SPAN
             and str(row.get("layer")) not in perf_ledger.SUMMARY_LAYERS]
    owned = critical_path._attribute(spans)
    by_resource: dict[str, float] = defaultdict(float)
    by_layer: dict[str, float] = defaultdict(float)
    for index, span in enumerate(spans):
        seconds = owned.get(index, 0.0)
        by_resource[critical_path.bucket_of(str(span.get("layer")))] += seconds
        by_layer[str(span.get("layer"))] += seconds
    return dict(by_resource), dict(by_layer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", help="a perf_ledger.jsonl file")
    parser.add_argument("--layer", default="otio_placement.fusion_comps_s",
                        help="the layer to report on")
    args = parser.parse_args(argv)

    path = Path(args.ledger)
    if not path.exists():
        print(f"no ledger at {path}", file=sys.stderr)
        return 1
    raw = _rows(path)
    clamped = ledger_anomaly.clamp_rows(raw)
    runs = perf_ledger.run_ids(raw)

    phantom_rows = [row for row in clamped
                    if row.get("kind") == ledger_anomaly.ANOMALY]
    claimed = sum(float(row.get("claimed_wall_s") or 0.0)
                  for row in phantom_rows)
    layer_claimed = sum(float(row.get("claimed_wall_s") or 0.0)
                        for row in phantom_rows
                        if str(row.get("layer")) == args.layer)

    before_res: dict[str, float] = defaultdict(float)
    after_res: dict[str, float] = defaultdict(float)
    before_layer: dict[str, float] = defaultdict(float)
    after_layer: dict[str, float] = defaultdict(float)
    affected = 0
    for run_id in runs:
        raw_res, raw_layers = _attributed(raw, run_id)
        clamped_res, clamped_layers = _attributed(clamped, run_id)
        for name, seconds in raw_res.items():
            before_res[name] += seconds
        for name, seconds in clamped_res.items():
            after_res[name] += seconds
        for name, seconds in raw_layers.items():
            before_layer[name] += seconds
        for name, seconds in clamped_layers.items():
            after_layer[name] += seconds
        if raw_res != clamped_res:
            affected += 1

    spans = sum(1 for row in raw if row.get("kind") == perf_ledger.SPAN)
    print(f"ledger: {path}")
    print(f"rows: {len(raw)}; span rows: {spans}; runs: {len(runs)}")
    print()
    print(f"phantom phases: {len(phantom_rows)}"
          + (f" ({len(phantom_rows) / spans * 100:.2f}% of span rows)"
             if spans else ""))
    print(f"  claimed seconds: {claimed:.1f} s ({claimed / 3600:.1f} h) "
          f"- charged to NOTHING after the clamp")
    print(f"  of which {args.layer}: {layer_claimed:.1f} s "
          f"({layer_claimed / 3600:.1f} h)")
    print(f"  runs whose cost changed: {affected} of {len(runs)}")
    print()
    print("seconds by RESOURCE (the axis a Resolve-vs-native comparison "
          "reads):")
    for name in sorted(set(before_res) | set(after_res)):
        print(f"  {name:<12} before {before_res.get(name, 0.0):>14.1f} s"
              f"   after {after_res.get(name, 0.0):>12.1f} s")
    print()
    print(f"seconds charged to {args.layer}:")
    print(f"  before {before_layer.get(args.layer, 0.0):>14.1f} s"
          f"   after {after_layer.get(args.layer, 0.0):>12.1f} s")
    moved = [name for name in before_layer
             if abs(before_layer[name] - after_layer.get(name, 0.0)) > 0.01]
    if moved:
        print("  layers whose charge changed: " + ", ".join(sorted(moved)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

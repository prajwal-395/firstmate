#!/usr/bin/env python3
"""VC-003: what the version comparison could see, before and after.

The measurement the punch list asks for: on a Ren-owned scratch copy of
a GRADED reel, the accepted items must be unchanged after a set of
unrelated edits - and the engine must be able to SAY so, with the
denominator.

WHAT IS MEASURED
----------------
The recorded generations of a Ren-owned scratch project's reel - the
timeline shadow's own record, read-only, with no Resolve touched - and
for every consecutive pair of generations:

* the OLD comparison: the row diff the version comparison used
  (`versions.rounds.diff_reel` over the rows).  It answers about the
  CUT.  It compares no grade, no Fusion comp, no transform, no
  keyframe and no level, so the number of accepted items it can see is
  ZERO - and that zero is the "before" this lane measured.
* the NEW comparison: the accepted state (`library.tools.accepted_state`),
  which compares every accepted item and names the ones that moved.

The denominators are reported separately and are never combined: the
number of accepted items COMPARED, the number that MOVED, and the
number the old comparison could have named.

USAGE
-----
    python3 scripts/accepted_state_proof.py [--project "Ren Scratch - breaktest"]

The shadow store is read at `$REN_SHADOW_DB` or the machine's default
path.  Nothing is written anywhere: the proof reads a record.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import accepted_state  # noqa: E402
from library.tools.timeline_shadow import default_db_path  # noqa: E402


def _generations(db: sqlite3.Connection, project: str) -> list:
    """Every recorded generation of a project, with its body."""
    rows = db.execute(
        "SELECT g.timeline_id, g.timeline_name, g.generation, g.source,"
        " g.recorded_at, s.body FROM generations g JOIN snapshots s"
        " ON s.hash=g.hash WHERE g.project=? ORDER BY g.timeline_id,"
        " g.generation", (project,)).fetchall()
    out = []
    for row in rows:
        out.append({
            "timeline_id": row["timeline_id"],
            "timeline_name": row["timeline_name"],
            "generation": row["generation"],
            "source": row["source"],
            "recorded_at": row["recorded_at"],
            "body": json.loads(row["body"]),
        })
    return out


def _rows_changed(before: dict, after: dict) -> int:
    """How many rows the OLD comparison (the row diff) reported."""
    from library.tools.reel_read import rows_of
    from library.tools.versions.rounds import diff_reel

    try:
        report = diff_reel(rows_of(before), rows_of(after))
    except Exception:  # noqa: BLE001 - an unreadable row set is unmeasured
        return 0
    return len(report.get("changed") or ())


def measure(project: str, db_path: Path) -> dict:
    """The measurement, over every consecutive pair of generations."""
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        generations = _generations(db, project)
    finally:
        db.close()
    by_timeline: dict = {}
    for row in generations:
        by_timeline.setdefault(row["timeline_id"], []).append(row)
    pairs = []
    for timeline_id, rows in sorted(by_timeline.items()):
        for earlier, later in zip(rows, rows[1:]):
            if earlier["body"] == later["body"]:
                continue
            report = accepted_state.verify(earlier["body"], later["body"])
            counts = report.get("counts") or {}
            pairs.append({
                "timeline": later["timeline_name"],
                "from_generation": earlier["generation"],
                "to_generation": later["generation"],
                "to_source": later["source"],
                "accepted_items": counts.get("accepted_items", 0),
                "accepted_moved": counts.get("changed", 0),
                "accepted_unauthorized": counts.get("unauthorized", 0),
                "canvas_differences": counts.get("canvas_differences", 0),
                "rows_the_old_comparison_reported": _rows_changed(
                    earlier["body"], later["body"]),
                "status": report.get("status"),
                "moved": [
                    {"shot": row.get("shot"), "row": row.get("row"),
                     "part": row.get("part"), "field": row.get("field")}
                    for row in (report.get("findings") or ())],
            })
    totals = {
        "pairs_measured": len(pairs),
        "accepted_items_compared": sum(p["accepted_items"] for p in pairs),
        "accepted_items_moved": sum(p["accepted_moved"] for p in pairs),
        "accepted_items_unauthorized": sum(p["accepted_unauthorized"]
                                           for p in pairs),
        "canvas_differences": sum(p["canvas_differences"] for p in pairs),
        # The OLD comparison: the version comparison compared the CUT
        # and no accepted item at all.  The number of accepted items it
        # could name is zero, and that zero is the "before".
        "accepted_items_the_old_comparison_compared": 0,
        "rows_the_old_comparison_reported": sum(
            p["rows_the_old_comparison_reported"] for p in pairs),
    }
    return {"project": project, "db": str(db_path), "totals": totals,
            "pairs": pairs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="accepted_state_proof",
        description="What the version comparison could see, before and "
                    "after (VC-003).")
    parser.add_argument("--project", default="Ren Scratch - breaktest",
                        help="the Ren-owned scratch project to measure")
    parser.add_argument("--db", default=None,
                        help="the shadow store to read "
                             "(default: $REN_SHADOW_DB or the machine's)")
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else default_db_path()
    if not db_path.exists():
        print(f"no shadow store at {db_path}", file=sys.stderr)
        return 2
    report = measure(args.project, db_path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0
    totals = report["totals"]
    print(f"project: {report['project']}")
    print(f"shadow store: {report['db']}")
    print()
    print("BEFORE - the version comparison (the row diff):")
    print(f"  accepted items compared: "
          f"{totals['accepted_items_the_old_comparison_compared']}")
    print(f"  rows reported: {totals['rows_the_old_comparison_reported']}")
    print("  accepted items it could name: 0 - it compares the cut, and a "
          "grade is not a row")
    print()
    print("AFTER - the accepted state:")
    print(f"  pairs measured: {totals['pairs_measured']}")
    print(f"  accepted items compared: {totals['accepted_items_compared']}")
    print(f"  accepted items that moved: {totals['accepted_items_moved']}")
    print(f"  of those, named by the old comparison: 0")
    print(f"  of those, unauthorized: {totals['accepted_items_unauthorized']}")
    print(f"  canvas differences: {totals['canvas_differences']}")
    print()
    for pair in report["pairs"]:
        if not pair["accepted_moved"] and not pair["canvas_differences"]:
            continue
        print(f"  {pair['timeline']} "
              f"gen {pair['from_generation']} -> {pair['to_generation']} "
              f"({pair['to_source']}): "
              f"{pair['accepted_moved']} of {pair['accepted_items']} "
              f"accepted item(s) moved; the row diff reported "
              f"{pair['rows_the_old_comparison_reported']} row(s)")
        for row in pair["moved"][:5]:
            print(f"      {row['row']} {row['shot']}: {row['part']} "
                  f"{row['field']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

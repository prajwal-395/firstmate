#!/usr/bin/env python3
"""Capture a recorded set of LIVE receipts for the AXI-003 census.

The census needs receipts that were really produced - not fixtures
written to prove a point - so this reads the live sources the machine
already has and records what it found:

- the BROKER's job table (the receipts of every job that ran), and the
  EDIT receipts embedded in those jobs' results;
- the UNDO JOURNAL entries of Ren-owned scratch projects;
- the live reports the repository already carries.

Nothing is written outside the fixture path, and nothing is written to a
project, a timeline or a broker.  The broker table is read with sqlite3
in read-only mode.

    python3 scripts/axi003_capture_receipts.py \\
        --broker-db /tmp/ren-now.sqlite3 \\
        --out tests/fixtures/axi003_live_receipts.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

#: The receipt CORE: the keys that answer the lifecycle questions.  A
#: receipt's own vocabulary (a broker job, a journal entry) is measured
#: against this core, so two receipts that answer the same six
#: questions count as one shape.
CORE_KEYS = (
    "status", "acceptance_contracts", "execution_contract", "lifecycle",
    "planned", "applied", "verified", "committed", "promoted", "delivered",
    "recovery", "recovery_result",
)

#: The projects whose journals are read.  Only SCRATCH work: the
#: captain's projects are not read for a measurement.
SCRATCH_MARKERS = ("scratch", "bench", "breaktest", "qual")

#: The bulky fields a sample does not carry.  The read-back and the
#: expected state are the timeline itself; the census reads the
#: DECISION a receipt carries, and a fixture that holds a whole
#: timeline is a fixture nobody can review.
TRIMMED_KEYS = ("readback", "expected_state", "actual_state", "result",
                "error", "stdout", "stderr", "operations_detail",
                "before", "after", "preservation_before", "preservation_after",
                "accepted_edit", "spec", "changes", "snapshot", "inventory",
                "operations", "timeline_state", "items")


def core_shape(receipt: dict[str, Any]) -> tuple[str, ...]:
    """The core keys a receipt carries, in order."""
    return tuple(key for key in CORE_KEYS if key in receipt)


def find_receipts(node: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Every receipt-shaped object inside a recorded blob.

    A receipt is an object that carries an acceptance contract or a
    lifecycle - the two things every production execution path
    publishes.
    """
    found: list[dict[str, Any]] = []
    if depth > 12:
        return found
    if isinstance(node, dict):
        if ("acceptance_contracts" in node or "lifecycle" in node
                or "execution_contract" in node):
            found.append(node)
        for value in node.values():
            found.extend(find_receipts(value, depth=depth + 1))
    elif isinstance(node, list):
        for value in node:
            found.extend(find_receipts(value, depth=depth + 1))
    elif isinstance(node, str):
        # A receipt embedded in a captured stdout.
        for start in _json_object_starts(node):
            try:
                parsed = json.loads(node[start:])
            except ValueError:
                continue
            found.extend(find_receipts(parsed, depth=depth + 1))
            break
    return found


def _json_object_starts(text: str) -> list[int]:
    starts = []
    index = text.find("{")
    while index != -1 and len(starts) < 4:
        starts.append(index)
        index = text.find("{", index + 1)
    return starts


def broker_receipts(db_path: str) -> list[dict[str, Any]]:
    """The broker's own job receipts, plus the edit receipts inside them."""
    if not os.path.exists(db_path):
        return []
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT id, kind, state, result, error, project, timeline, "
            "submitted, finished FROM jobs ORDER BY finished").fetchall()
    finally:
        connection.close()
    receipts: list[dict[str, Any]] = []
    for (job_id, kind, state, result, error, project, timeline,
         submitted, finished) in rows:
        receipts.append({
            "kind": "job",
            "source": f"broker job {job_id} ({kind})",
            "state": state,
            "receipt": {
                "id": job_id, "kind": kind, "state": state,
                "project": project, "timeline": timeline,
                "result": result, "error": error,
                "submitted": submitted, "finished": finished,
            },
        })
        if not result:
            continue
        try:
            parsed = json.loads(result)
        except ValueError:
            parsed = None
        for receipt in find_receipts(parsed):
            receipts.append({
                "kind": "edit",
                "source": f"broker job {job_id} ({kind})",
                "state": state,
                "receipt": receipt,
            })
    return receipts


def journal_receipts(projects_root: str) -> list[dict[str, Any]]:
    """The undo journal entries of Ren-owned scratch projects."""
    root = Path(projects_root)
    if not root.is_dir():
        return []
    receipts: list[dict[str, Any]] = []
    for project in sorted(root.iterdir()):
        if not project.is_dir():
            continue
        if not any(marker in project.name.lower() for marker in SCRATCH_MARKERS):
            continue
        for entry_path in sorted(project.glob(
                "pipeline_output/review/undo/*/entry.json")):
            try:
                entry = json.loads(entry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            receipts.append({
                "kind": "journal",
                "source": f"{project.name}/{entry_path.parent.name}",
                "state": str(entry.get("status") or ""),
                "receipt": entry,
            })
    return receipts


def report_receipts(reports_dir: str) -> list[dict[str, Any]]:
    """The receipts the repository's own live reports carry."""
    root = Path(reports_dir)
    if not root.is_dir():
        return []
    receipts: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for receipt in find_receipts(payload):
            kind = "build" if "placement_check" in receipt else "edit"
            receipts.append({
                "kind": kind,
                "source": f"report {path.name}",
                "state": str(receipt.get("status") or ""),
                "receipt": receipt,
            })
    return receipts


def _trim(node: Any, depth: int = 0) -> Any:
    """The receipt without the bulky fields, at every depth."""
    if depth > 8:
        return node
    if isinstance(node, dict):
        return {key: _trim(value, depth + 1)
                for key, value in node.items()
                if key not in TRIMMED_KEYS}
    if isinstance(node, list):
        return [_trim(value, depth + 1) for value in node[:200]]
    return node


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-db", default="/tmp/ren-now.sqlite3")
    parser.add_argument(
        "--projects-root",
        default=os.environ.get("PIPELINE_PROJECTS_ROOT", ""))
    parser.add_argument(
        "--reports-dir", default=str(REPO / "tests" / "live" / "reports"))
    parser.add_argument(
        "--out", default=str(REPO / "tests" / "fixtures" /
                             "axi003_live_receipts.json"))
    parser.add_argument("--max-per-shape", type=int, default=8)
    args = parser.parse_args()

    captured = broker_receipts(args.broker_db)
    captured += journal_receipts(args.projects_root)
    captured += report_receipts(args.reports_dir)

    # One sample per distinct shape, with the counts the census reports.
    shapes: dict[tuple[str, ...], int] = {}
    for row in captured:
        shape = core_shape(row["receipt"])
        shapes[shape] = shapes.get(shape, 0) + 1
    samples: list[dict[str, Any]] = []
    per_shape: dict[tuple[str, ...], int] = {}
    for row in captured:
        shape = core_shape(row["receipt"])
        if per_shape.get(shape, 0) >= args.max_per_shape:
            continue
        per_shape[shape] = per_shape.get(shape, 0) + 1
        samples.append({
            "kind": row["kind"],
            "source": row["source"],
            "state": row["state"],
            "core": list(shape),
            "receipt": _trim(row["receipt"]),
        })

    payload = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": {
            "broker_db": args.broker_db,
            "projects_root": args.projects_root,
            "reports_dir": args.reports_dir,
        },
        "corpus": {
            "receipts": len(captured),
            "distinct_core_shapes": len(shapes),
            "shapes": [{"core": list(shape), "count": count}
                       for shape, count in sorted(
                           shapes.items(), key=lambda row: -row[1])],
        },
        "samples": samples,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(payload, indent=1, sort_keys=True, default=str),
        encoding="utf-8")
    print(f"captured {len(captured)} receipts, {len(shapes)} core shapes, "
          f"{len(samples)} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

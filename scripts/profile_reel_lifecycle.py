#!/usr/bin/env python3
"""Aggregate one controlled reel workflow from Ren's existing perf ledger.

Usage:
  bin/vep -m scripts.profile_reel_lifecycle --ledger <perf_ledger.jsonl> \\
      --run <run-id> --workflow <workflow-id> --output <results.jsonl>

The tool never opens Resolve or starts an edit. Run the authorized workflow
through Ren in Ren Qualification, then point this reader at that run's ledger.
The ledger remains the source of truth for timing and lease measurements.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from library.tools import perf_ledger

WORKFLOWS = (
    "no_op", "single_property_40px", "lower_third_replacement",
    "caption_text_timing_fix", "clip_insert_replace", "ripple_edit",
    "full_rebuild", "undo_redo_recovery", "rebuild_with_human_edits",
    "multi_reel_one_changed",
)
PHASE_ALIASES = {
    "planning": {"reel_build_survey"},
    "model_access": {"model_load", "model_cache", "host_model",
                     "gemma_inference", "mfa_align"},
    "cache": {"cache", "render_cache", "media_decode"},
    "staging": {"staging", "reel_build"},
    "resolve_ops": {"resolve_hold", "resolve_render", "resolve_api_call",
                    "broker_request"},
    "verification": {"reel_verify", "verification"},
    "promotion": {"promotion", "reel_promotion"},
    "version_commits": {"version_commit"},
    "cleanup": {"cleanup", "reel_cleanup"},
    "archive_export": {"archive_export", "resolve_archive"},
}


def _measured_sum(rows: list[dict[str, Any]], key: str):
    values = [row[key] for row in rows if key in row and row[key] is not None]
    return round(sum(float(value) for value in values), 3) if values else None


def aggregate(rows: list[dict[str, Any]], run_id: str,
              workflow: str) -> dict[str, Any]:
    run_rows = [row for row in rows if row.get("run_id") == run_id]
    phases: dict[str, dict[str, Any]] = {}
    for phase, layers in PHASE_ALIASES.items():
        selected = [row for row in run_rows
                    if row.get("kind") == perf_ledger.SPAN
                    and (row.get("phase") == phase
                         or row.get("layer") in layers)]
        api_calls = [int(row.get("calls") or 0) for row in selected
                     if row.get("layer") == "resolve_api_call"]
        phases[phase] = {
            "wall_s": _measured_sum(selected, "wall_s"),
            "cpu_s": _measured_sum(selected, "cpu_s"),
            "peak_rss_mb": max((float(row["self_peak_rss_mb"])
                                for row in selected
                                if row.get("self_peak_rss_mb") is not None),
                               default=None),
            "bytes_read": _measured_sum(selected, "bytes_read"),
            "bytes_written": _measured_sum(selected, "bytes_written"),
            "resolve_api_calls": sum(api_calls) if api_calls else None,
            "rows": len(selected),
        }
    summary = perf_ledger.profile(rows, run_id)
    return {
        "schema": "ren_reel_lifecycle_profile/1",
        "workflow": workflow,
        "run_id": run_id,
        "total_wall_s": summary["total_wall_s"],
        "resolve": summary["resolve"],
        "phases": phases,
        "coverage": {
            "phase_rows": {key: value["rows"]
                           for key, value in phases.items()},
            "ledger_rows": len(run_rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = perf_ledger.read_rows("", ledger_override=args.ledger,
                                 required=True)
    result = aggregate(rows, args.run, args.workflow)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

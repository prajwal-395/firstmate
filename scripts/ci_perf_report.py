"""Revisioned full-suite performance records and fail-closed regression checks.

Records live in the user's Ren cache by default. A baseline is explicit JSON
and stores the exact executed test ids; a shorter run cannot improve the gate
by silently dropping tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def history_path() -> Path:
    configured = os.environ.get("REN_CI_PERF_HISTORY")
    if configured:
        return Path(configured).expanduser()
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "ren" / "ci-performance.jsonl"


def measure(reports: list[Path], revision: str, verdict: str, wall: float,
            cpu: float | None = None, peak_rss: int | None = None) -> dict:
    cases: dict[str, dict] = {}
    files: set[str] = set()
    for report in reports:
        root = ET.parse(report).getroot()
        for case in root.iter("testcase"):
            name = case.get("nodeid") or f"{case.get('classname', '')}::{case.get('name', '')}"
            if name in cases:
                raise ValueError(f"duplicate test case across reports: {name}")
            skipped = case.find("skipped") is not None
            status = "skipped" if skipped else "failed" if case.find("failure") is not None or case.find("error") is not None else "passed"
            seconds = float(case.get("time", "0"))
            cases[name] = {"status": status, "seconds": seconds}
            if case.get("file"):
                files.add(Path(case.get("file", "")).as_posix())
    if not cases:
        raise ValueError("JUnit reports contain no test cases")
    selected = sorted(cases)
    slow = sorted(((name, row["seconds"]) for name, row in cases.items() if row["status"] != "skipped"), key=lambda row: (-row[1], row[0]))[:20]
    return {
        "schema": 1, "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "revision": revision, "verdict": verdict, "wall_seconds": wall,
        "cpu_seconds": cpu, "peak_rss_bytes": peak_rss,
        "counts": {key: sum(row["status"] == key for row in cases.values()) for key in ("passed", "failed", "skipped")},
        "selected_count": len(cases), "executed_count": len(cases) - sum(row["status"] == "skipped" for row in cases.values()),
        "test_files": len(files), "test_set_sha256": hashlib.sha256("\n".join(selected).encode()).hexdigest(),
        "test_ids": selected, "test_times": {name: row["seconds"] for name, row in cases.items() if row["status"] != "skipped"},
        "slow_tests": slow,
    }


def resource_totals(paths: list[Path]) -> tuple[float | None, int | None]:
    cpu = 0.0
    saw_cpu = False
    peak = 0
    saw_peak = False
    for path in paths:
        raw = path.read_text(encoding="utf-8", errors="replace")
        for key in ("user", "sys"):
            match = re.search(rf"(?m)^{key}\s+([0-9]+(?:\.[0-9]+)?)\s*$", raw)
            if match:
                cpu += float(match.group(1))
                saw_cpu = True
        match = re.search(r"(?m)^maximum resident set size\s+([0-9]+)\s*$", raw)
        if match:
            peak = max(peak, int(match.group(1)))
            saw_peak = True
    return cpu if saw_cpu else None, peak if saw_peak else None


def check(record: dict, baseline: dict) -> list[str]:
    errors = []
    if record["selected_count"] != baseline["selected_count"] or record["test_set_sha256"] != baseline["test_set_sha256"]:
        errors.append("selected test set differs from the known-good baseline")
    if record["executed_count"] < baseline["executed_count"]:
        errors.append("executed test count is below the known-good baseline")
    if record["counts"]["failed"]:
        errors.append("run contains failed tests")
    if record["wall_seconds"] > baseline["wall_seconds"] * (1 + baseline.get("wall_regression_fraction", 0.2)):
        errors.append("wall time exceeds the provisional regression threshold")
    previous = baseline.get("test_times", {})
    regressions = [name for name, seconds in record.get("test_times", {}).items()
                   if name in previous and seconds > max(previous[name] * 2, previous[name] + 1.0)]
    if regressions:
        errors.append("slow-test regression: " + ", ".join(sorted(regressions)[:5]))
    return errors


def append_record(record: dict, destination: Path | None = None) -> None:
    path = destination or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def latest_baseline(path: Path | None = None) -> dict | None:
    """Return the latest complete PASS, ignoring failed and narrowed runs."""
    try:
        rows = [json.loads(line) for line in (path or history_path()).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    return next((row for row in reversed(rows) if row.get("schema") == 1 and row.get("verdict") == "PASS" and row.get("counts", {}).get("failed") == 0), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--verdict", required=True)
    parser.add_argument("--wall-seconds", required=True, type=float)
    parser.add_argument("--cpu-seconds", type=float)
    parser.add_argument("--peak-rss-bytes", type=int)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--time-files", nargs="*", type=Path, default=[])
    parser.add_argument("--check-history", action="store_true", help="check against the last full PASS using a provisional 20%% wall-time allowance")
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        record = measure(args.reports, args.revision, args.verdict, args.wall_seconds, args.cpu_seconds, args.peak_rss_bytes)
        if args.time_files:
            cpu, peak = resource_totals(args.time_files)
            record["cpu_seconds"] = cpu
            record["peak_rss_bytes"] = peak
        baseline = latest_baseline(args.history) if args.check_history else None
        errors = []
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            errors = check(record, baseline)
        elif args.check_history and baseline:
            baseline = {**baseline, "wall_regression_fraction": 0.2}
            errors = check(record, baseline)
        if errors:
            record["verdict"] = "FAIL"
        append_record(record, args.history)
        print(json.dumps({key: value for key, value in record.items() if key not in {"test_ids", "test_times"}}, sort_keys=True))
        if errors:
            print("CI performance gate refused: " + "; ".join(errors), file=sys.stderr)
            return 1
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        print(f"CI performance report failed closed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

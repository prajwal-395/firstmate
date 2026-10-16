"""Revisioned full-suite performance records and fail-closed regression checks.

Records live in the user's Ren cache by default. A baseline is explicit JSON
and stores the exact executed test ids; a shorter run cannot improve the gate
by silently dropping tests.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


# Adjacent full-suite records in ~/.cache/ren/ci-performance.jsonl show up
# to 4.8 seconds of movement in tests whose measured runtime is under five
# seconds. Treat smaller absolute changes as load noise while retaining
# sensitivity to large slowdowns.
SLOW_TEST_REGRESSION_FLOOR_SECONDS = 5.0

# How many recent history records bound a test's noise envelope. A single
# baseline cannot tell load noise from a slowdown for tests that swing
# widely run to run (e.g. 1.3-32.6 s across 8 records), so the per-test
# reference is the maximum over this window, not one earlier value.
NOISE_ENVELOPE_WINDOW = 8


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


def check(record: dict, baseline: dict, history: list[dict] | None = None,
          collected_ids: set[str] | None = None) -> list[str]:
    """Compare a run against the baseline without failing on normal change.

    Coverage loss is only when a baseline test was deselected or stopped
    executing while its node still exists in the tree: a skip the baseline
    also skipped (or a test the baseline never had) is expected, and a
    deleted test file or function is a deliberate removal, not lost
    coverage. Wall time and per-test times compare only over test ids
    present in both runs; the per-test reference is the maximum over the
    baseline and recent history so one noisy baseline cannot fail the gate.
    """
    errors = []
    baseline_ids = set(baseline.get("test_ids", ()))
    record_ids = set(record.get("test_ids", ()))
    missing = baseline_ids - record_ids
    lost = sorted(test_id for test_id in missing if _test_still_exists(test_id, collected_ids))
    if lost:
        errors.append("baseline tests were deselected or stopped executing: " + ", ".join(lost[:5]))
    record_times = record.get("test_times") or {}
    baseline_times = baseline.get("test_times") or {}
    if record.get("test_ids") is not None and record.get("test_times") is not None:
        # Executed ids are the test_times keys (skips are collected but not
        # timed). Presence in record_ids already proves the node exists, so
        # a newly skipped test needs no tree lookup to count as lost.
        newly_skipped = sorted((set(baseline_times) & record_ids) - set(record_times))
        if newly_skipped:
            errors.append("baseline tests stopped executing (now skipped): " + ", ".join(newly_skipped[:5]))
    elif record["executed_count"] < record["selected_count"]:
        errors.append("executed test count is below selected test count")
    if record["counts"]["failed"]:
        errors.append("run contains failed tests")
    # Wall time is comparable only when both runs execute the same tests.
    if (record_ids == baseline_ids and record["wall_seconds"] >
            baseline["wall_seconds"] * (1 + baseline.get("wall_regression_fraction", 0.2))):
        errors.append("wall time exceeds the provisional regression threshold")
    reference = dict(baseline_times)
    for prior in history or ():
        for name, seconds in (prior.get("test_times") or {}).items():
            if seconds > reference.get(name, 0):
                reference[name] = seconds
    regressions = [name for name, seconds in record_times.items()
                   if name in reference and seconds - reference[name] > max(
                       reference[name], SLOW_TEST_REGRESSION_FLOOR_SECONDS)]
    if regressions:
        errors.append("slow-test regression: " + ", ".join(sorted(regressions)[:5]))
    return errors


def _test_still_exists(test_id: str, collected_ids: set[str] | None = None) -> bool:
    """Check the test node itself, not just its module file.

    A test function deliberately deleted from a still-existing file reads
    as lost coverage under a file-existence check, so resolve the node id
    to the module and confirm the named class/function still defines it.
    When the caller's collected ids are available they answer directly: a
    node the current tree no longer collects was deleted, not deselected.
    """
    if collected_ids is not None:
        return test_id in collected_ids
    module, sep, node = test_id.partition("::")
    module = module.split("[", 1)[0]
    candidates = [Path(module)]
    if not module.endswith(".py"):
        candidates.append(Path(*module.split(".")).with_suffix(".py"))
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        return False
    if not sep:
        return True
    parts = [part.split("[", 1)[0] for part in node.split("::") if part]
    if not parts:
        return True
    names = _module_test_names(path)
    if names is None:
        return True
    funcs, classes = names
    first = parts[0]
    if first in classes:
        return len(parts) == 1 or parts[1] in classes[first]
    return len(parts) == 1 and first in funcs


_module_test_names_cache: dict[str, tuple[float, tuple[set[str], dict[str, set[str]]]]] = {}


def _module_test_names(path: Path) -> tuple[set[str], dict[str, set[str]]] | None:
    """Module-level functions and class methods defined in a test module.

    Returns None when the module cannot be read or parsed; callers fail
    closed on None rather than mistaking an unreadable file for a deleted
    test.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = path.absolute().as_posix()
    cached = _module_test_names_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    funcs: set[str] = set()
    classes: dict[str, set[str]] = {}
    for child in tree.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(child.name)
        elif isinstance(child, ast.ClassDef):
            classes[child.name] = {
                grandchild.name for grandchild in child.body
                if isinstance(grandchild, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    names = (funcs, classes)
    _module_test_names_cache[key] = (mtime, names)
    return names


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


def recent_history(path: Path | None = None, limit: int = NOISE_ENVELOPE_WINDOW) -> list[dict]:
    """Return recent records carrying per-test times, whatever the verdict.

    Load noise is visible in failed runs too, so the envelope reads every
    recent record rather than only PASS baselines.
    """
    try:
        rows = [json.loads(line) for line in (path or history_path()).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    return [row for row in rows if row.get("schema") == 1 and isinstance(row.get("test_times"), dict)][-limit:]


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
            errors = check(record, baseline, history=recent_history(args.history))
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

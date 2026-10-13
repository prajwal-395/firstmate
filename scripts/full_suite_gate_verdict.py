"""Fail-closed JUnit summaries and final verdicts for the local full-suite gate."""

from __future__ import annotations

import argparse
import signal
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Verdict:
    line: str
    exit_code: int


def summarize(path: str | Path, exit_code: int) -> str:
    """Return ``state detail`` for a pytest JUnit report, failing closed."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return f"ran-nothing unreadable JUnit report ({exc.__class__.__name__})"
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return "ran-nothing JUnit report contains no test suite"
    tests = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    executed = tests - skipped
    if tests == 0:
        return "ran-nothing pytest collected 0 tests"
    if executed == 0:
        return (
            f"ran-nothing all {tests} selected tests skipped, so nothing was measured"
        )
    if failures or errors:
        return f"bad {executed} executed, {failures} failed, {errors} errored"
    if exit_code != 0:
        if exit_code > 128:
            signum = exit_code - 128
            try:
                signame = signal.Signals(signum).name
            except (ValueError, AttributeError):
                signame = f"signal {signum}"
            return (
                f"crashed {executed} passed then NATIVE CRASH ({signame}) "
                "- check ~/Library/Logs/DiagnosticReports/ for the faulting library"
            )
        return f"bad {executed} executed with no recorded failure but pytest exited {exit_code}"
    return f"ok {executed} passed, {skipped} skipped"


def calculate_verdict(
    main_state: str,
    main_detail: str,
    real_model_state: str,
    real_model_detail: str,
    narrowed_count: int,
    real_model_excluded_by_policy: bool = False,
) -> Verdict:
    """Format the gate's last line and exit status from measured phase results."""
    if main_state == "ok":
        verdict = "PASS"
    elif main_state in {"bad", "crashed"}:
        verdict = "FAIL"
    else:
        verdict = "DID NOT RUN"

    if real_model_state == "ok":
        real_model_note = f"real_model {real_model_detail}"
    elif real_model_state == "bad":
        real_model_note = f"real_model FAILED - {real_model_detail}"
        if verdict == "PASS":
            verdict = "FAIL"
    elif real_model_state == "crashed":
        real_model_note = f"real_model CRASHED - {real_model_detail}"
        if verdict == "PASS":
            verdict = "FAIL"
    elif real_model_excluded_by_policy:
        real_model_note = "real_model excluded by local CI marker policy"
    else:
        real_model_note = f"real_model NOT MEASURED - {real_model_detail}"
        if verdict == "PASS" and narrowed_count == 0:
            verdict = "DID NOT RUN"

    if verdict == "PASS" and narrowed_count > 0:
        verdict = "NARROWED PASS"
    line = (
        f"FULL-SUITE GATE: {verdict}  |  unit+scenario: {main_detail}"
        f"  |  {real_model_note}"
    )
    return Verdict(line, 0 if verdict == "PASS" else 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("report")
    summary.add_argument("exit_code", type=int)
    verdict = commands.add_parser("verdict")
    verdict.add_argument("main_state")
    verdict.add_argument("main_detail")
    verdict.add_argument("real_model_state")
    verdict.add_argument("real_model_detail")
    verdict.add_argument("narrowed_count", type=int)
    verdict.add_argument("real_model_excluded_by_policy", type=int)
    args = parser.parse_args()
    if args.command == "summarize":
        print(summarize(args.report, args.exit_code))
        return 0
    result = calculate_verdict(
        args.main_state,
        args.main_detail,
        args.real_model_state,
        args.real_model_detail,
        args.narrowed_count,
        bool(args.real_model_excluded_by_policy),
    )
    print(result.line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

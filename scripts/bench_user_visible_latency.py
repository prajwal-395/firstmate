#!/usr/bin/env python3
"""Time one user-visible Ren task and record it in the performance ledger.

The punch list asks for the latency the PERSON waited for, beside the
individual tool durations (PERF-002), and for the stages of an edit to be
read separately (PERF-001). The engine already records the second half:
`library/tools/perf_ledger.py` writes capability rows, spans, waits and
task rows, and `library/tools/perf_report.py` reads them back.

What was missing is the OUTER measurement: a task that starts when the
person asks and ends when the work is done. This wrapper opens that task
row, runs the command with the ledger attached, closes the row, and
prints what it saw - the wall clock the person waited, the child's peak
resident memory, and where the time went inside the command.

The command is NOT timed by hand around the pipeline: the ledger inside
the command measures the stages, and this measures the wait.

    scripts/bench_user_visible_latency.py \\
        --ledger <path to perf_ledger.jsonl> \\
        --task-class axi.property_change \\
        -- bin/resolve-axi edit transform --item <id> --move-y 24 --apply

`--ledger` is the project's own ledger, so `ren profile` reads the same
file the engine writes. `--run-id` is optional; without one a run id is
minted in the engine's own format.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _mint_run_id() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    from library.tools import run_id as run_identity

    return run_identity.mint_run_id()


def _peak_child_rss_mb() -> float:
    """The child's peak resident memory, in MB.

    `ru_maxrss` is bytes on macOS and kilobytes on Linux; the ledger's
    own reader uses the same conversion, and this must agree with it.
    """
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if sys.platform == "darwin":
        return round(peak / (1024 * 1024), 1)
    return round(peak / 1024, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", required=True,
                        help="the performance ledger this task's rows join")
    parser.add_argument("--task-class", required=True,
                        help="what the person asked for, in the ledger's "
                             "own vocabulary (e.g. axi.trim)")
    parser.add_argument("--run-id", default="",
                        help="the run id every row of this task carries")
    parser.add_argument("--field", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="extra fields on the task row; repeatable")
    parser.add_argument("--tail", type=int, default=4000,
                        help="how much of the command's output to keep")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the command to time, after --")
    args = parser.parse_args(argv)

    command = [token for token in args.command if token != "--"]
    if not command:
        parser.error("a command is required after --")

    sys.path.insert(0, str(REPO_ROOT))
    from library.tools import perf_ledger

    run_id = args.run_id or _mint_run_id()
    ledger = Path(args.ledger).expanduser().resolve()
    ledger.parent.mkdir(parents=True, exist_ok=True)

    fields: dict[str, object] = {}
    for entry in args.field:
        key, _, value = entry.partition("=")
        fields[key] = value

    env = dict(os.environ)
    env[perf_ledger.LEDGER_ENV] = str(ledger)
    env[perf_ledger.RUN_ENV] = run_id
    env[perf_ledger.CAPABILITY_ENV] = args.task_class

    task = perf_ledger.begin_task(
        None, args.task_class, run_id=run_id,
        ledger_path_override=ledger, required=True, **fields)

    started = time.time()
    began = time.perf_counter()
    try:
        completed = subprocess.run(command, env=env, capture_output=True,
                                   text=True, encoding="utf-8",
                                   errors="replace", check=False)
        code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except OSError as exc:
        code = 127
        stdout = ""
        stderr = f"the command could not start: {exc}"
    wall = time.perf_counter() - began
    peak_mb = _peak_child_rss_mb()

    outcome = "ok" if code == 0 else f"exit {code}"
    if task:
        task.end(outcome=outcome, peak_child_rss_mb=peak_mb)

    summary = {
        "task_class": args.task_class,
        "run_id": run_id,
        "ledger": str(ledger),
        "command": command,
        "exit_code": code,
        "outcome": outcome,
        "waited_s": round(wall, 3),
        "started_at": round(started, 3),
        "peak_child_rss_mb": peak_mb,
        "stdout_tail": stdout[-args.tail:],
        "stderr_tail": stderr[-args.tail:],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Collect the runtime coverage index (CI-05), on demand.

On a scratch checkout, behind the heavy lock, never beside a pooled
gate and never on the merge path:

    python3 -m library.tools.heavy_work_lock run \\
        --owner collect-coverage --profile full_suite_gate -- \\
        python3 scripts/collect_coverage_index.py \\
        --checkout /path/to/scratch/checkout \\
        --baseline 700

`--baseline` is the last uninstrumented run's wall seconds over the
same selection; the index records the delta for the C5 overhead read.
Without it the index records wall time only (no invented overhead).

Writes the index beside the timing telemetry (`coverage_index.
index_path()`, or `--output`) and prints the test count plus the
overhead line. Exit 0 wrote the index; 1 is a refused or failed
collection with the reason on stderr.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", default=REPO_ROOT)
    parser.add_argument("--selection", nargs="*", default=None,
                        help="default: tests/unit tests/scenarios")
    parser.add_argument("--baseline", type=float, default=None,
                        help="uninstrumented wall seconds for overhead")
    parser.add_argument("--output", default=None)
    parser.add_argument("--python", default="")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args(argv)

    from library.tools.local_ci import coverage_index, hygiene
    try:
        result = coverage_index.collect(
            args.checkout, selection=args.selection, python=args.python,
            baseline_seconds=args.baseline, destination=args.output,
            workers=args.workers, timeout=args.timeout)
    except hygiene.HygieneRefused as exc:
        print(f"collect_coverage_index: refused: {exc}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"collect_coverage_index: failed: {result.get('reason')}",
              file=sys.stderr)
        return 1
    print(f"wrote {result['index']}: {result['tests']} tests, "
          f"wall {result['wall_seconds']}s", end="")
    if result.get("overhead_seconds") is not None:
        print(f", overhead {result['overhead_seconds']}s "
              f"(ratio {result['overhead_ratio']})", end="")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the recorded request set through every execution backend and
compare their answers.

    python3 scripts/backend_conformance.py            # the report
    python3 scripts/backend_conformance.py --json     # the report as data
    python3 scripts/backend_conformance.py --native-bin <path>

The measurement (INFRA-005): the SAME recorded request, run through the
native Rust front door and the Python CLI, compared on content, timing
and preserved state. No request touches Resolve, renders anything, or
reads a project outside the scratch directory.

Exits 1 when a measured request DISAGREED, so it can be a gate. A
backend that could not be run is reported as `unmeasured` and never
fails the run: an absent binary is not an agreement and not a
disagreement.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import backend_conformance as conformance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", default=None,
                        help="the recorded request set's root")
    parser.add_argument("--native-bin", default=None,
                        help="the native front door to compare against")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    parser.add_argument("--keep-scratch", default=None,
                        help="keep the scratch directory at this path")
    args = parser.parse_args(argv)

    if args.native_bin:
        os.environ["REN_NATIVE_BIN"] = args.native_bin

    root = Path(args.requests) if args.requests else conformance.FIXTURE_ROOT
    requests = conformance.load_requests(root)
    backends = conformance.default_backends()

    scratch = Path(args.keep_scratch) if args.keep_scratch else None
    temporary = None
    if scratch is None:
        temporary = tempfile.TemporaryDirectory(prefix="ren-conformance-")
        scratch = Path(temporary.name)
    try:
        report = conformance.run_suite(requests, backends, scratch, root)
    finally:
        if temporary is not None:
            temporary.cleanup()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(conformance.render(report))
    return 1 if report.disagreed else 0


if __name__ == "__main__":
    raise SystemExit(main())

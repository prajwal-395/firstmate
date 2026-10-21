"""Score a run of the representative corpus against its sealed answer key.

This is the evaluator's half of BASE-003, and it lives OUTSIDE the
engine on purpose: the sealed answer key must not be a path the engine
can read, and the engine's own acceptance runner is reused rather than
rewritten. The runner is imported and called with the brief set built
in memory, so the key never reaches a path the engine opens.

    python3 evaluation/tools/score_corpus.py --records <path> [--report <path>]

``--records`` takes one JSON file holding a list of task records, or a
directory of one-record files - the same shape the shipped harness reads
(`ren accept --records`). The report is written with the harness's own
writer, so the two corpora are read the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import (
    load_sealed_briefs,
    load_sealed_substrates,
    shape,
)

from library.tools.editorial_acceptance import runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="score_corpus.py",
        description="Score a run of the representative corpus.")
    parser.add_argument("--records", required=True,
                        help="a record file, or a directory of them")
    parser.add_argument("--report", default=None,
                        help="where to write the report")
    parser.add_argument("--judge", default=None,
                        help="module:attribute naming a factory that returns "
                             "the independent judge")
    parser.add_argument("--no-align-transcript", action="store_true",
                        help="time the delivered file's words with the "
                             "transcriber's own boundaries")
    args = parser.parse_args(argv)

    brief_set = load_sealed_briefs()
    catalogue = load_sealed_substrates()
    judge = None
    if args.judge:
        module_name, _, attribute = args.judge.partition(":")
        import importlib
        factory = getattr(importlib.import_module(module_name), attribute)
        judge = factory()

    records = runner.read_records(args.records)
    report = runner.run_acceptance(
        records, brief_set=brief_set, judge=judge, catalogue=catalogue,
        align_transcript=not args.no_align_transcript)
    report["corpus"] = shape(brief_set)
    out = runner.write_report(report, args.report)
    print(json.dumps({
        "task_count": report["task_count"],
        "scored": report["scored"],
        "not_run": report["not_run"],
        "passed": report["passed"],
        "failed": report["failed"],
        "premise": report["premise"],
        "wrote": str(out),
    }, indent=2, sort_keys=True))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

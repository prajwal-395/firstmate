"""The typed Project Model path, before and after: does it say which
resolution answered?

WHAT THIS MEASURES
-------------------
`ren evidence` answers a typed query against a project's Project
Model.  Before this change the answer said nothing about the
resolution it was reached at: a claim about a span of the source and
a claim about the model's own structure were printed in the same
voice.  After it, every answer carries a `fidelity` block that names
the tier, what the answer rests on, and the way up.

So the measurement is two questions, with denominators:

* **How many answers declare a resolution?**  Before: none.  After:
  every answer.
* **Are the answers the same answer?**  The declaration is an
  addition, never a change: the rows, the status and the reasons
  must be identical, or the declaration has altered what the path
  says.

The two arms run the SAME command in two trees: the tree at the base
commit, and the tree as it is.  The project is a Ren-owned scratch
project, read with `--no-record` so the run writes nothing to it.

    bin/vep docs/benchmarks/typed_path_resolution_bench.py \
        --project <scratch project> --plans /tmp/plan.json \
        --before-tree /tmp/base --after-tree .
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve()


def _run(tree: Path, project: str, plan: str | None, trace: str | None,
         ) -> dict:
    """The typed path's answer, in the given tree."""
    command = [sys.executable, "-m",
               "library.tools.analysis.footage_evidence_query",
               project]
    if plan:
        command.append(plan)
    if trace:
        command.extend(["--trace", trace])
    command.append("--no-record")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tree)
    completed = subprocess.run(command, capture_output=True, text=True,
                               encoding="utf-8", cwd=str(tree),
                               env=environment, check=False)
    if completed.returncode != 0:
        return {"error": completed.stderr[-2000:], "declared": False,
                "answer": None}
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return {"error": "the answer was not JSON", "declared": False,
                "answer": None}
    answer = payload.get("answer")
    if not isinstance(answer, dict):
        return {"error": "the answer was not an answer", "declared": False,
                "answer": None}
    fidelity = answer.get("fidelity") or {}
    return {
        "declared": bool(fidelity.get("tier")),
        "tier": fidelity.get("tier"),
        "lane": fidelity.get("lane"),
        "has_escalation": bool(answer.get("escalation")),
        "answer": _without_declaration(answer),
        "error": None,
    }


def _without_declaration(answer: dict) -> dict:
    """The answer, minus the declaration.

    The declaration is what this change ADDS.  Everything else must
    be the same answer, or the declaration has changed what the path
    says.
    """
    return {key: value for key, value in answer.items()
            if key not in ("fidelity", "escalation")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="typed_path_resolution_bench",
        description=("Does the typed Project Model path declare the "
                     "resolution it answered at?"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--plans", help="plan files, separated by ';'")
    parser.add_argument("--traces", help="trace references, separated by ';'")
    parser.add_argument("--before-tree", default=str(REPO_ROOT))
    parser.add_argument("--after-tree", default=str(REPO_ROOT))
    parser.add_argument("--json", help="write the report here")
    args = parser.parse_args(argv)

    before_tree = Path(args.before_tree).resolve()
    after_tree = Path(args.after_tree).resolve()
    questions = []
    for plan in (args.plans or "").split(";"):
        if plan.strip():
            questions.append({"kind": "plan", "value": plan.strip()})
    for trace in (args.traces or "").split(";"):
        if trace.strip():
            questions.append({"kind": "trace", "value": trace.strip()})
    if not questions:
        parser.error("--plans or --traces names at least one question")

    rows = []
    for question in questions:
        plan = question["value"] if question["kind"] == "plan" else None
        trace = question["value"] if question["kind"] == "trace" else None
        before = _run(before_tree, args.project, plan, trace)
        after = _run(after_tree, args.project, plan, trace)
        rows.append({
            "question": question,
            "before_declared": before["declared"],
            "after_declared": after["declared"],
            "after_tier": after.get("tier"),
            "after_lane": after.get("lane"),
            "after_has_escalation": after.get("has_escalation"),
            "answers_identical": (before.get("answer") is not None
                                  and before.get("answer") == after.get("answer")),
            "before_error": before.get("error"),
            "after_error": after.get("error"),
        })
        print(f"{question['kind']} {question['value']!r}: before declared="
              f"{rows[-1]['before_declared']}, after declared="
              f"{rows[-1]['after_declared']} "
              f"(tier {rows[-1]['after_tier']}), answers identical="
              f"{rows[-1]['answers_identical']}", file=sys.stderr)

    total = len(rows)
    before_declared = sum(1 for row in rows if row["before_declared"])
    after_declared = sum(1 for row in rows if row["after_declared"])
    identical = sum(1 for row in rows if row["answers_identical"])
    report = {
        "project": args.project,
        "before_tree": str(before_tree),
        "after_tree": str(after_tree),
        "questions": total,
        "before_declared": before_declared,
        "after_declared": after_declared,
        "answers_identical": identical,
        "rows": rows,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2),
                                   encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if (after_declared == total and identical == total) else 1


if __name__ == "__main__":
    sys.exit(main())

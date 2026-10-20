#!/usr/bin/env python3
"""Assemble the section 12 demonstration's task record from a live run.

The punch list's section 12 asks for one recurring demonstration: one
brief, ten obligations, ten pieces of evidence. The WORK is done by
whoever takes the brief - the live lane's driver, or an agent working
the `ren-co-editor` loop - and it leaves a task record. This script is
the seam between the two: it reads the measured steps of a live run and
writes the record the acceptance harness reads, so the record's numbers
are the run's numbers and not a retelling of them.

It does not drive an edit, it does not touch Resolve, and it does not
judge. The judge is supplied by the caller; the scorer here is the
engine's own (`library.tools.editorial_acceptance.demo`), so the ten
obligations and the ten pieces of evidence are read from ONE place.

Usage:

    scripts/vep_bench_demo.py --steps <steps.json> \\
        --record <record.json> --report <scored.json>

The steps file is a JSON object with:

    brief_id            the brief this run took (default: the-whole-job)
    instruction         the user's words, verbatim
    project             the Ren project folder the run worked on
    reel                the timeline the run worked on
    interpretation      {restated_outcome, selected_operations, resolved_targets}
    retrieval           {moments, abstained, unavailable}
    planning            {operations, requirements_covered}
    execution           {receipt, checks, collateral_changes, attempts, route, recovery}
    delivery            {path, sha256, duration_seconds, technical_verification}
    run                 {wall_seconds, instruction_to_verified_seconds}
    resource            {peak_rss_bytes}
    steps               [{obligation, note, operation, status, seconds, peak_rss_bytes, detail}]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from library.tools.editorial_acceptance import briefs, demo, record


def build_record(steps: dict) -> dict:
    """The task record for one run, from the run's own measurements."""
    brief_id = str(steps.get("brief_id") or "the-whole-job")
    instruction = str(steps.get("instruction") or "")
    sections = {
        name: dict(steps.get(name) or {})
        for name in record.SECTIONS
    }
    sections["execution"].setdefault("attempts", 1)
    for step in steps.get("steps") or ():
        execution = sections["execution"]
        receipt = execution.setdefault("receipt", {})
        if step.get("status") and not receipt.get("status"):
            receipt["status"] = str(step["status"])
    built = record.new_record(
        task_id=str(steps.get("task_id") or brief_id),
        brief_id=brief_id,
        instruction=instruction,
        interpretation=sections["interpretation"],
        retrieval=sections["retrieval"],
        planning=sections["planning"],
        execution=sections["execution"],
        delivery=sections["delivery"],
        run=dict(steps.get("run") or {}),
        resource=dict(steps.get("resource") or {}),
        project=str(steps.get("project") or ""),
        reel=str(steps.get("reel") or ""),
        steps=list(steps.get("steps") or ()),
        assessment=dict(steps.get("assessment") or {}),
    )
    problems = record.validate(built)
    if problems:
        raise SystemExit("the record breaks its contract:\n  "
                         + "\n  ".join(problems))
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", required=True,
                        help="the measured steps of the live run")
    parser.add_argument("--record", required=True,
                        help="where the task record is written")
    parser.add_argument("--report", required=True,
                        help="where the scored demonstration is written")
    parser.add_argument("--brief-set", default="",
                        help="the brief set the run took its brief from")
    args = parser.parse_args(argv)

    steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
    built = build_record(steps)
    written = record.write(args.record, built)

    the_set = briefs.load_brief_set(args.brief_set or None)
    by_id = briefs.briefs_by_id(the_set)
    brief_id = built["brief_id"]
    if brief_id not in by_id:
        raise SystemExit(f"the brief set carries no brief {brief_id!r}")
    public = briefs.public_view(by_id[brief_id])
    scored = demo.score_demo(built, public)
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scored, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"record: {written}")
    print(f"report: {target}")
    print(f"obligations established: "
          f"{scored['obligations_established']}/{scored['obligations_total']}")
    print(f"evidence established: "
          f"{scored['evidence_established']}/{scored['evidence_total']}")
    print(f"milestone: {scored['milestone']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

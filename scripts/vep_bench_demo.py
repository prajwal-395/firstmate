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

Two halves of the record are now PRODUCED rather than copied:

- the DELIVERY section, by the harness's own producer
  (`editorial_acceptance.delivery.build_delivery_section`), which reads
  the delivered file and measures it. A hand-typed section is how a
  delivery report came to carry three checks nobody ran;
- the ASSESSMENT, by the engine's scorer
  (`editorial_acceptance.judgement.score_recorded`), which recomputes
  the counts, the score and the verdict from the judge's own
  per-criterion rows. A hand-typed count is how a record came to say
  `passed 3, failed 4` over rows that were three passes, three failures
  and four unmeasured.

What the run still owns is the JUDGE'S READING: the criteria rows, the
rationales and the artifacts the judge looked at. This script never
invents one and never edits one.

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
    delivery            {path, platform, timeline, declared_ending}
    assessment          {judge, criteria}
    run                 {wall_seconds, instruction_to_verified_seconds}
    resource            {peak_rss_bytes}
    steps               [{obligation, note, operation, status, seconds, peak_rss_bytes, detail}]

The `delivery` block declares what the file is judged AGAINST, and the
producer measures the file against it:

    path                the delivered file
    platform            the delivery spec's platform (default: the
                        registry's default)
    timeline            {fps, frames} - the timeline's own clock
    declared_ending     {black: [[start, end]], silence: [[start, end]]}
                        in seconds on the delivered file's clock, for the
                        ending the reel DELIBERATELY ends on. Absent means
                        nothing is declared and the ending gates fail
                        closed, which is the right answer for a reel whose
                        ending nobody has ruled on.

The `assessment` block carries the judge's own work:

    judge               who read the file
    criteria            [{id, status, rationale, evidence}]

Nothing else: the counts, the score and the verdict are computed here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from library.tools.editorial_acceptance import briefs, demo, judgement, record
from library.tools.editorial_acceptance import delivery as delivery_module


def _declared_spans(section: dict, name: str) -> list[list[float]]:
    """The declared `(start, end)` seconds of one ending window."""
    ending = section.get("declared_ending")
    ending = ending if isinstance(ending, dict) else {}
    spans = ending.get(name)
    if not isinstance(spans, list):
        return []
    out: list[list[float]] = []
    for span in spans:
        try:
            start, end = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            out.append([start, end])
    return out


def _resolve_spec(section: dict, project: str):
    """The delivery spec the file is judged against.

    A platform named in the steps file wins. Otherwise the PROJECT
    resolves it, through the engine's own call - so the record is
    graded against the spec the project declares, not against a
    platform this script invented.
    """
    from library.tools.delivery_spec import (
        resolve_delivery_spec,
        resolve_delivery_spec_for_project,
    )

    name = str(section.get("platform") or "").strip()
    if name:
        return resolve_delivery_spec(name)
    if project:
        try:
            return resolve_delivery_spec_for_project(project)
        except (OSError, ValueError, KeyError):
            pass
    return resolve_delivery_spec("")


def build_delivery_section(section: dict, project: str = "") -> dict:
    """The delivery section, produced from the delivered file.

    The run's own declarations - the platform, the timeline's clock and
    the ending it deliberately ends on - go in; the measurements come
    out of the file. A file that cannot be read is reported as one that
    was not delivered, rather than as one whose checks passed.
    """
    path = str(section.get("path") or "").strip()
    if not path:
        return {}
    spec = _resolve_spec(section, project)
    clock = section.get("timeline")
    clock = clock if isinstance(clock, dict) else {}
    fps = clock.get("fps")
    frames = clock.get("frames")
    try:
        produced = delivery_module.build_delivery_section(
            path,
            spec=spec,
            timeline_fps=float(fps) if fps else None,
            timeline_frames=int(frames) if frames else None,
            declared_black_spans=_declared_spans(section, "black") or None,
            declared_silence_spans=_declared_spans(section, "silence") or None,
        )
    except delivery_module.DeliveryError as exc:
        return {"path": path, "evidence_error": str(exc)}
    ending = section.get("declared_ending")
    if isinstance(ending, dict) and ending.get("source"):
        produced["declared_ending_source"] = str(ending["source"])
    return produced


def build_assessment(steps: dict, produced_delivery: dict,
                     public: dict) -> dict:
    """The assessment, produced by the engine's scorer.

    The judge's rows are the run's; the counts are the scorer's. A
    criterion the judge left unmeasured stays unmeasured - the scorer
    refuses a verdict that leaves one out - and a criterion the judge
    measured keeps the judge's own status and rationale.
    """
    assessment = steps.get("assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    judge_name = str(assessment.get("judge") or "").strip()
    criteria = assessment.get("criteria")
    if criteria is None:
        editorial = assessment.get("editorial")
        criteria = (editorial.get("criteria")
                    if isinstance(editorial, dict) else None)
    criteria = [row for row in (criteria or [])
                if isinstance(row, dict)]
    if not judge_name or not criteria:
        return {}
    digest = str(produced_delivery.get("sha256") or "")
    if not digest:
        # No file, no score: a score that names no file describes no
        # particular deliverable, and two runs of the same brief would
        # be indistinguishable.
        return {"judge": judge_name,
                "editorial": {"status": "not_measured", "score": None},
                "technical": {"status": "not_measured", "checks": {}},
                "reason": "the delivered file carries no digest, so there "
                          "is nothing to score"}
    evidence = []
    for row in criteria:
        for item in row.get("evidence") or []:
            if isinstance(item, dict) and item.get("artifact"):
                evidence.append(dict(item))
    if not evidence:
        return {"judge": judge_name,
                "editorial": {"status": "not_measured", "score": None},
                "technical": {"status": "not_measured", "checks": {}},
                "reason": "the judge cited no artifact, so there is no "
                          "evidence to score"}
    render = {
        "sha256": digest,
        "duration_seconds": produced_delivery.get("duration_seconds"),
        "evidence": evidence,
        "technical_verification": dict(
            produced_delivery.get("technical_verification") or {}),
    }
    try:
        scored = judgement.score_recorded(
            task_id=str(steps.get("task_id") or steps.get("brief_id") or ""),
            brief=public, render=render, criteria=criteria,
            judge_name=judge_name)
    except (TypeError, ValueError) as exc:
        # A judgment the scorer refuses is REPORTED, not dropped: the
        # run produced no verdict, and saying so is the whole of the
        # record's honesty here.
        return {"judge": judge_name,
                "editorial": {"status": "not_measured", "score": None},
                "technical": {"status": "not_measured", "checks": {}},
                "reason": f"the scorer refused the judgment: {exc}"}
    return scored


def build_record(steps: dict, public: dict | None = None) -> dict:
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
    produced_delivery = build_delivery_section(
        sections["delivery"], str(steps.get("project") or ""))
    sections["delivery"] = produced_delivery
    assessment = build_assessment(steps, produced_delivery,
                                  public or {})
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
        assessment=assessment,
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
    the_set = briefs.load_brief_set(args.brief_set or None)
    by_id = briefs.briefs_by_id(the_set)
    brief_id = str(steps.get("brief_id") or "the-whole-job")
    if brief_id not in by_id:
        raise SystemExit(f"the brief set carries no brief {brief_id!r}")
    public = briefs.public_view(by_id[brief_id])
    built = build_record(steps, public)
    written = record.write(args.record, built)

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

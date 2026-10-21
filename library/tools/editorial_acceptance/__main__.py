"""`ren accept`: score a run of unfamiliar briefs against their expectations.

The acceptance run is two halves, and they happen in different places.
The WORK is done by whoever takes the brief - the live lane's driver, or
an agent working the `ren-co-editor` loop - and it leaves a task record
per brief. The MEASUREMENT is done here, on the records, with no
Resolve, no model and no network.

    ren accept --list
        the briefs in the set, their edge cases and their coverage

    ren accept --records <path> [--report <path>] [--judge <module:attr>]
        measure every record against its brief and write the report

    ren accept --select
        the substrate each brief would run against, and why

`--records` takes one JSON file holding a list of records, or a
directory of one-record files. `--judge` names a factory that returns
the independent judge; without one, the editorial half of a task is
`not_measured` and the report says so rather than scoring it.
`--substrates` names the substrate catalogue to measure against.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from library.tools.editorial_acceptance import (
    briefs,
    delivery,
    footage,
    judgement,
    runner,
)


def _load_factory(spec: str):
    """`module:attribute` - the attribute is a callable that builds the
    judge."""
    if ":" not in spec:
        raise SystemExit(
            f"--judge wants module:attribute, not {spec!r}")
    module_name, _, attribute = spec.partition(":")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise SystemExit(
            f"--judge {spec!r} names something that is not callable")
    return factory


def _list_briefs(brief_set: dict[str, Any],
                 catalogue: dict[str, Any]) -> int:
    coverage = briefs.coverage(brief_set)
    selection = footage.select_for_set(brief_set, catalogue)
    print(f"brief set: {brief_set.get('path')}")
    print(f"sha256:    {brief_set.get('sha256')}")
    print(f"briefs:    {coverage['brief_count']} "
          f"({coverage['acceptance_criterion_count']} criteria)")
    for brief in brief_set["briefs"]:
        key = briefs.oracle(brief)
        print(f"\n  {brief['id']}  [{key['edge_case'] or 'no edge case'}]")
        print(f"    {brief['instruction']}")
        for criterion in brief["acceptance_criteria"]:
            print(f"      - {criterion['id']}: {criterion['statement']}")
        for requirement in key.get("footage_requirements") or []:
            feature = requirement.get("feature")
            print(f"      premise: {feature} must be "
                  f"{requirement.get('presence')}")
        chosen = selection["briefs"].get(str(brief["id"]), {})
        print(f"      substrate: {chosen.get('selected')} "
              f"({chosen.get('reason')})")
    print("\nedge cases covered: "
          + ", ".join(f"{case} ({count})" for case, count
                      in sorted(coverage["edge_case_counts"].items())))
    if coverage["edge_cases_missing"]:
        print("edge cases MISSING: "
              + ", ".join(coverage["edge_cases_missing"]))
    return 0


def _brief_by_id(brief_set: dict[str, Any], brief_id: str) -> dict[str, Any]:
    """The brief with this id, or a refusal that names the ones there are."""
    for brief in brief_set.get("briefs") or []:
        if str(brief.get("id")) == brief_id:
            return brief
    known = ", ".join(str(brief.get("id"))
                      for brief in brief_set.get("briefs") or [])
    raise SystemExit(
        f"--brief {brief_id!r} is not in the brief set. It holds: {known}")


def _public_brief(brief_set: dict[str, Any], args: argparse.Namespace
                  ) -> dict[str, Any]:
    """The brief the delivered file is reviewed against.

    `--brief-file` names a JSON file holding the brief's PUBLIC half - the
    instruction, the project state and the acceptance criteria - which is
    what a judge is allowed to see. `--brief` names one of the acceptance
    set's briefs instead.
    """
    if args.brief_file:
        brief = json.loads(Path(args.brief_file).read_text(encoding="utf-8"))
        if not isinstance(brief, dict):
            raise SystemExit(f"{args.brief_file} does not hold a brief")
        return brief
    if not args.brief:
        raise SystemExit("--review needs --brief or --brief-file")
    return briefs.public_view(_brief_by_id(brief_set, args.brief))


def _caption_intents(path: str | None) -> list[Any] | None:
    """The caption intents a caller supplied, or None when none were."""
    if not path:
        return None
    from library.tools.delivered_caption_spec import CaptionIntent

    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"{path} does not hold a list of caption intents")
    intents = []
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit(f"{path} holds a caption intent that is not "
                             "an object")
        intents.append(CaptionIntent(
            label=str(row.get("label") or ""),
            text=str(row.get("text") or ""),
            start=float(row.get("start") or 0.0),
            end=float(row.get("end") or 0.0),
            box=tuple(row["box"]) if row.get("box") else None,
            lines=row.get("lines"),
            ink_height=row.get("ink_height"),
            emphasis=tuple(tuple(item) for item in row.get("emphasis") or ()),
        ))
    return intents


def _spans(text: str | None) -> list[tuple[float, float]]:
    """The `start:end` spans a caller declared, read and checked."""
    if not text:
        return []
    spans: list[tuple[float, float]] = []
    for item in str(text).split(","):
        item = item.strip()
        if not item:
            continue
        start, _, end = item.partition(":")
        try:
            pair = (float(start), float(end))
        except ValueError:
            raise SystemExit(
                f"--declared span {item!r} is not start:end seconds")
        if pair[1] <= pair[0]:
            raise SystemExit(
                f"--declared span {item!r} does not run forwards")
        spans.append(pair)
    return spans


def _timeline_view(path: str | None):
    """The timeline VIEW a targeted revision is planned against.

    A revision names the ITEM it changes, and an item is a thing on a
    timeline.  The view is the caller's account of that timeline: the
    items, their rows, their spans and their boxes.  Without one, the
    planner can still say what it cannot do - and says it by name
    rather than guessing an item.
    """
    if not path:
        return None
    from library.tools.targeted_revision import RevisionContext

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not hold a timeline view")
    return RevisionContext.from_dict(data)


def _targeted_revisions(qc: Any, view: Any) -> list[dict[str, Any]]:
    """The narrow revision for each objective failure, or why there is none.

    QA-009: a failure that names its property becomes a NARROW edit
    request for just that property, and the parts of the work that were
    already accepted are declared unchanged.  A failure with no narrow
    revision is reported with the reason, and a failure that is a
    judgement rather than a measurement is handed to a person.
    """
    from library.tools import targeted_revision

    checks = targeted_revision.objective_checks(qc)
    revisions = []
    for check in checks:
        if check.passed:
            continue
        if view is None:
            revisions.append({
                "check": check.as_dict(),
                "status": "no_timeline_view",
                "reason": ("no timeline view was supplied, so no item on "
                           "the timeline can be named and no narrow edit "
                           "can be planned"),
                "next_action": "supply_the_timeline_view",
            })
            continue
        revisions.append(
            targeted_revision.plan_revision(check, view).as_dict())
    return revisions


def _review(args: argparse.Namespace, brief_set: dict[str, Any]) -> int:
    """One structured editorial review of one delivered file.

    The objective legs are MEASURED here, by the engine's own gates on
    the delivered file; the subjective legs are the judge's rows, read
    from the evidence packet and carried in a file. The two are printed
    apart, because a subjective score must never be read as clearing an
    objective failure.

    The review also carries the TARGETED REVISIONS (QA-009): for each
    objective failure, the narrow edit request that would fix that one
    property, with the accepted parts of the work declared unchanged.
    """
    from library.tools import delivery_spec

    video = Path(args.review)
    if not video.is_file():
        raise SystemExit(f"there is no delivered file at {video}")
    brief = _public_brief(brief_set, args)
    spec = delivery_spec.resolve_delivery_spec(args.platform or "")
    declared_silence = _spans(args.declared_silence)
    declared_black = _spans(args.declared_black)
    render = delivery.build_render_evidence(
        video, align_transcript=not args.no_align_transcript,
        expected_resolution=(spec.width, spec.height),
        expected_fps=spec.fps,
        expected_duration=args.expected_duration,
        declared_black_spans=list(declared_black),
        declared_silence_spans=list(declared_silence),
        target_lufs=spec.loudness_target_lufs,
        lufs_tolerance=spec.loudness_tolerance_lufs,
        true_peak_ceiling=spec.true_peak_ceiling_dbtp)
    qc = judgement.objective_verdict(
        str(video), spec=spec,
        expected_duration=args.expected_duration
        or render.get("duration_seconds"),
        caption_intents=_caption_intents(args.caption_intents),
        declared_silence_spans=list(declared_silence),
        declared_black_spans=list(declared_black))
    objective = judgement.objective_legs(
        str(video), spec=spec,
        expected_duration=args.expected_duration
        or render.get("duration_seconds"),
        caption_intents=_caption_intents(args.caption_intents),
        declared_silence_spans=list(declared_silence),
        declared_black_spans=list(declared_black),
        qc=qc)
    judgments = None
    if args.judgments:
        judgments = json.loads(Path(args.judgments).read_text(encoding="utf-8"))
        if isinstance(judgments, dict):
            judgments = judgments.get("legs") or judgments.get("criteria")
    record = judgement.review(
        task_id=str(args.task_id or video.stem),
        brief=brief, render=render, judgments=judgments,
        judge=None if judgments is not None else _load_factory(args.judge)(),
        objective=objective)
    record["targeted_revisions"] = _targeted_revisions(
        qc, _timeline_view(args.timeline_view))
    out = runner.write_report(record, args.report)
    print(json.dumps({
        "verdict": record["verdict"],
        "verdict_reason": record["verdict_reason"],
        "subjective_score": record["subjective_score"],
        "objective_failures": [row["leg"]
                               for row in record["objective_failures"]],
        "subjective_failures": [row["leg"]
                                for row in record["subjective_failures"]],
        "counts": record["counts"],
        "targeted_revisions": [
            {"check": row["check"]["property"],
             "status": row["status"],
             "reason": row["reason"]}
            for row in record["targeted_revisions"]],
    }, indent=2))
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren accept",
        description="Score a run of unfamiliar editorial briefs.")
    parser.add_argument("--briefs", default=None,
                        help="the brief set to measure against")
    parser.add_argument("--records", default=None,
                        help="a record file, or a directory of them")
    parser.add_argument("--judge", default=None,
                        help="module:attribute naming a factory that "
                             "returns the independent judge")
    parser.add_argument("--report", default=None,
                        help="where to write the report")
    parser.add_argument("--substrates", default=None,
                        help="the substrate catalogue to measure against")
    parser.add_argument("--select", action="store_true",
                        help="print the substrate each brief would run "
                             "against, and why")
    parser.add_argument("--review", default=None,
                        help="review one delivered file on the eight "
                             "named legs, and write the structured review")
    parser.add_argument("--brief", default=None,
                        help="the brief id the delivered file is reviewed "
                             "against")
    parser.add_argument("--brief-file", default=None,
                        help="a JSON file holding the brief's PUBLIC half "
                             "- the instruction, the project state and the "
                             "acceptance criteria a judge may see")
    parser.add_argument("--judgments", default=None,
                        help="a JSON file holding the judge's own rows for "
                             "the eight legs")
    parser.add_argument("--task-id", default=None,
                        help="the task id the review is recorded under")
    parser.add_argument("--platform", default="",
                        help="the delivery platform the file is judged "
                             "against")
    parser.add_argument("--expected-duration", type=float, default=None,
                        help="the planned duration, in seconds")
    parser.add_argument("--caption-intents", default=None,
                        help="a JSON file holding the caption intents the "
                             "delivered file's captions are compared "
                             "against")
    parser.add_argument("--timeline-view", default=None,
                        help="a JSON file holding the timeline view the "
                             "targeted revisions are planned against - "
                             "the items, their rows, their spans and "
                             "their boxes")
    parser.add_argument("--declared-silence", default=None,
                        help="the seconds of the delivered file that are "
                             "DECLARED silent, as start:end pairs")
    parser.add_argument("--declared-black", default=None,
                        help="the seconds of the delivered file that are "
                             "DECLARED black, as start:end pairs")
    parser.add_argument("--no-align-transcript", action="store_true",
                        help="time the delivered file's words with the "
                             "transcriber's own boundaries instead of the "
                             "forced aligner's. The aligner places a word "
                             "boundary 26ms from the truth where the "
                             "transcriber's own is 94ms, and it costs "
                             "minutes per file; the words are the same "
                             "either way.")
    parser.add_argument("--list", action="store_true",
                        help="print the briefs and their coverage, and stop")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    brief_set = briefs.load_brief_set(args.briefs)
    catalogue = footage.load_catalogue(args.substrates)
    if args.select:
        selection = footage.select_for_set(brief_set, catalogue)
        print(json.dumps(selection, indent=2, sort_keys=True))
        return 0
    if args.review:
        return _review(args, brief_set)
    if args.list or not args.records:
        return _list_briefs(brief_set, catalogue)
    judge = _load_factory(args.judge)() if args.judge else None
    records = runner.read_records(args.records)
    report = runner.run_acceptance(records, brief_set=brief_set, judge=judge,
                                   catalogue=catalogue,
                                   align_transcript=not args.no_align_transcript)
    out = runner.write_report(report, args.report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "task_count": report["task_count"],
            "scored": report["scored"],
            "not_run": report["not_run"],
            "passed": report["passed"],
            "failed": report["failed"],
            "stage_metrics": {
                stage: {"value": row["value"], "measured": row["measured"],
                        "not_measured": row["not_measured"]}
                for stage, row in report["stage_metrics"]["stages"].items()},
            "premise": {
                "not_carried": report["premise"]["premise_not_carried"],
                "unchecked": report["premise"]["premise_unchecked"],
                "carried": report["premise"]["premise_carried_count"],
            },
            "failure_causes": {
                "bottleneck": report["failure_causes"]["bottleneck"],
                "dominant_cause": report["failure_causes"]["dominant_cause"],
                "by_stage": report["failure_causes"]["by_stage"],
            },
        }, indent=2))
    print(f"wrote {out}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

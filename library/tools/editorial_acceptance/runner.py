"""The acceptance run: briefs in, one report out.

The runner reads what a run produced - a task record per brief - and
measures it against the brief's expectations. It does not drive the
work, it does not touch Resolve, and it does not judge: the judge is
supplied, and the runner only records what the judge said.

What the report carries:

- the BRIEF SET it ran against, with its digest, so a report names the
  set it measured.
- the FIVE STAGE metrics, each on its own denominator, never combined.
- the PREMISE, measured ALONGSIDE the score: for every brief, whether
  the substrate footage carried what the request presupposes, and the
  pairing that says which substrate a brief still needs. The premise
  never replaces the score - the headline stays the engine's headline.
- the FAILURE CAUSES, with the bottleneck named, so the next engineering
  change has a target.
- the CRITERIA COVERAGE, published ALONGSIDE the headline: how much of
  each brief's acceptance criteria the plan declared it covers, which is
  the gap the planning gate exposes.
- one row per brief, with the record it was measured against.

A brief that was not run is reported as not run. A stage with no
evidence is reported as not measured. Nothing is turned into a zero,
and nothing is dropped for being inconvenient.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools.editorial_acceptance import (
    briefs,
    delivery,
    demo,
    failures,
    footage,
    grounding,
    judgement,
    reading,
    stages,
)
from library.tools.editorial_acceptance import (
    record as record_module,
)

SCHEMA = "ren-editorial-acceptance/4"

#: The report the live lane writes. The ordinary-request report keeps its
#: own file: a typed request and a brief are different measurements, and
#: one file holding both would make them look comparable. So does the E2E
#: workflow assessment, which is a THIRD measurement - `ren accept` writes
#: its report beside them, never over one of them.
DEFAULT_REPORT_PATH = Path("tests/live/reports/editorial-brief-acceptance.json")


def default_report_path() -> Path:
    """Where the report goes when nobody says: inside the engine.

    The path is resolved against the engine root rather than the
    caller's working directory, because `ren accept` may be run from
    anywhere and a report written into whatever directory the person
    happened to be in is a report nobody finds.
    """
    from ren.engine_root import find_engine_root

    root = find_engine_root()
    if root is None:
        root = Path(__file__).resolve().parents[3]
    return Path(root) / DEFAULT_REPORT_PATH


def _load_brief_set(brief_set: Mapping[str, Any] | None,
                    path: str | Path | None = None) -> dict[str, Any]:
    if brief_set is not None:
        return dict(brief_set)
    return briefs.load_brief_set(path)


def _delivery_for(record: Mapping[str, Any], brief: Mapping[str, Any],
                  judge: Callable | None) -> tuple[dict, dict]:
    """The delivered file's evidence, and the judge's reading of it.

    Without a judge the record's own measurement is taken as it stands:
    the live lane may have measured the file with more than this
    harness knows about, and re-measuring it would throw that away.
    That is also true WITH a judge: the record's own reading is
    replaced by the judge's, but the record's own MEASUREMENTS are not
    replaced by a second measurement of the same file - the file
    supplies only what the record does not carry, which is the evidence
    a judge looks at.

    With a judge, the judge is run HERE, on the delivered file, and the
    record's own reading is replaced by this one - so the independence
    of the assessment that reaches the report is the harness's own
    doing, not the run's claim.
    """
    section = record.get("delivery")
    section = dict(section) if isinstance(section, Mapping) else {}
    assessment = record.get("assessment")
    assessment = dict(assessment) if isinstance(assessment, Mapping) else {}
    if not section.get("evidence"):
        path = str(section.get("path") or "").strip()
        if path:
            try:
                measured = delivery.build_render_evidence(path)
            except delivery.DeliveryError as exc:
                section = {"path": path, "evidence_error": str(exc)}
            else:
                section = {**measured, **section}
    if judge is None:
        return section, assessment
    if not (section.get("sha256") and section.get("evidence")):
        return section, {}
    scored = judgement.assess(
        task_id=str(record.get("task_id") or record.get("brief_id") or ""),
        brief=brief, render=section, judge=judge)
    return section, scored


def _catalogue(catalogue: Mapping[str, Any] | None,
              path: str | Path | None) -> dict[str, Any]:
    if catalogue is not None:
        return dict(catalogue)
    return footage.load_catalogue(path)


def score_task(record: Mapping[str, Any], brief: Mapping[str, Any],
               *, judge: Callable | None = None,
               catalogue: Mapping[str, Any] | None = None,
               timelines: dict | None = None) -> dict[str, Any]:
    """One brief, one record, one row of the report.

    The brief is SCORED, whatever the footage carries: the engine still
    has to do the work the request asks for, and the headline is the
    headline. The premise is measured beside it and reported beside it,
    so a reader sees both - the score, and whether the footage could
    carry the request at all.
    """
    key = briefs.oracle(brief)
    public = briefs.public_view(brief)
    briefs.assert_no_leak(public, key)
    substrate, _ = footage.resolve_substrate(
        _catalogue(catalogue, None), record)
    premise = footage.applicability(brief, substrate)
    rendered, assessment = _delivery_for(record, public, judge)
    measured_record = dict(record)
    measured_record["delivery"] = rendered
    measured_record["assessment"] = assessment
    stage_measurements = stages.measure(measured_record, public, key)
    classification = failures.classify(
        stage_measurements, task_id=str(record.get("task_id") or brief["id"]),
        expected_refusal=bool(key["expected_refusal"]))
    reading_row = reading.measure(record, key,
                                  stage_measurements["interpretation"])
    reading_row["brief_id"] = str(brief["id"])
    grounding_row = grounding.measure(record, key, premise,
                                       timeline=_recorded_timeline(
                                           record, timelines))
    grounding_row["brief_id"] = str(brief["id"])
    return {
        "brief_id": str(brief["id"]),
        "task_id": str(record.get("task_id") or brief["id"]),
        "instruction": str(public["instruction"]),
        "edge_case": key["edge_case"],
        "expected_refusal": bool(key["expected_refusal"]),
        "footage": {
            "profile": str(public["project_state"]["footage_profile"]),
            "substrate": premise.get("substrate_id"),
            "substrate_profile": premise.get("substrate_profile"),
            "project": record.get("project"),
            "existing_adjustments": list(
                public["project_state"]["existing_adjustments"]),
            "media_conditions": list(
                public["project_state"]["media_conditions"]),
        },
        "premise": premise,
        "stages": stage_measurements,
        "reading": reading_row,
        "grounding": grounding_row,
        "failure": classification,
        "delivery": {
            "path": rendered.get("path"),
            "sha256": rendered.get("sha256"),
            "duration_seconds": rendered.get("duration_seconds"),
            "technical_verification": rendered.get(
                "technical_verification", {}),
            "technical_not_checked": rendered.get(
                "technical_not_checked", []),
        },
        "assessment": {
            "judge": assessment.get("judge"),
            "editorial_status": (assessment.get("editorial") or {}).get(
                "status"),
            "technical_status": (assessment.get("technical") or {}).get(
                "status"),
            "independence": assessment.get("independence"),
        },
        "record_problems": record_module.validate(record),
    }


def run_acceptance(records: Sequence[Mapping[str, Any]], *,
                   brief_set: Mapping[str, Any] | None = None,
                   brief_set_path: str | Path | None = None,
                   judge: Callable | None = None,
                   catalogue: Mapping[str, Any] | None = None,
                   catalogue_path: str | Path | None = None) -> dict[str, Any]:
    """Measure every record against its brief, and report.

    The HEADLINE is the engine's score and nothing else: a brief the
    footage cannot carry is still scored, because the engine still has
    to do the work the request asks for. The PREMISE is measured
    alongside it and reported alongside it, so a reader sees both at
    once - the score, and whether the substrate could carry the
    request - and so a brief that cannot be run yet says what footage
    it needs.
    """
    from library.tools.evaluation_levels import catalog as evaluation_level_catalog

    the_set = _load_brief_set(brief_set, brief_set_path)
    the_catalogue = _catalogue(catalogue, catalogue_path)
    by_id = briefs.briefs_by_id(the_set)
    rows: list[dict[str, Any]] = []
    timelines: dict = {}
    for record in records:
        brief_id = str(record.get("brief_id") or record.get("task_id") or "")
        brief = by_id.get(brief_id)
        if brief is None:
            rows.append({
                "brief_id": brief_id,
                "task_id": brief_id,
                "status": "not_run",
                "reason": f"the brief set carries no brief {brief_id!r}",
                "stages": {}, "failure": {},
            })
            continue
        row = score_task(record, brief, judge=judge, catalogue=the_catalogue,
                         timelines=timelines)
        row["status"] = "scored"
        rows.append(row)
    scored_rows = [row for row in rows if row.get("status") == "scored"]
    stage_rows = [row["stages"] for row in scored_rows if row.get("stages")]
    classifications = [row["failure"] for row in scored_rows
                        if row.get("failure")]
    measured_rows = scored_rows
    passed = sum(
        row["failure"].get("failed") is False
        for row in measured_rows)
    premise = _premise_view(scored_rows, the_set, the_catalogue)
    demonstration = _demonstration(records, the_set, by_id)
    report = {
        "schema": SCHEMA,
        "evaluation_levels": evaluation_level_catalog(),
        "evaluation": {
            "level": 4,
            "name": "editorial_acceptance",
            "agent_receives_expected_operation": False,
            "agent_receives_expected_target": False,
            "agent_receives_editorial_decision": False,
        },
        "brief_set": {
            "path": the_set.get("path"),
            "sha256": the_set.get("sha256"),
            "brief_count": len(the_set["briefs"]),
            "demonstration_brief": briefs.demonstration_brief_id(the_set),
            "coverage": briefs.coverage(the_set),
        },
        "substrates": footage.select_for_set(the_set, the_catalogue),
        "task_count": len(rows),
        "scored": len(measured_rows),
        "not_run": len(rows) - len(measured_rows),
        "run_state": "scored" if measured_rows else "not_run",
        "run_state_reason": (
            None if measured_rows else
            "no task record of this brief set has been measured; the run "
            "belongs to the live lane"),
        "passed": passed,
        "failed": len(measured_rows) - passed,
        "stage_metrics": stages.summarize(stage_rows),
        "reading": reading.summarize([row["reading"] for row in scored_rows
                                       if row.get("reading")]),
        "grounding": grounding.summarize([row["grounding"]
                                           for row in scored_rows
                                           if row.get("grounding")]),
        "premise": premise,
        "criteria_coverage": _criteria_coverage(scored_rows),
        "failure_causes": failures.bottleneck_summary(classifications),
        "results": rows,
    }
    if demonstration is not None:
        report["demonstration"] = demonstration
    return report


def _premise_view(scored_rows: Sequence[Mapping[str, Any]],
                   the_set: Mapping[str, Any],
                   catalogue: Mapping[str, Any]) -> dict[str, Any]:
    """What the footage carried, reported NEXT TO the score.

    The score is the headline and this is not a substitute for it: a
    brief whose premise the substrate does not carry still counts as
    scored and still counts as the engine's result. What this view
    adds is the pairing - for every brief, the premises it presupposes,
    the substrate that carries them, and the exact premises a substrate
    has to carry for the briefs that have none.
    """
    not_carried = [row["brief_id"] for row in scored_rows
                   if (row.get("premise") or {}).get("status")
                   == footage.PREMISE_NOT_CARRIED]
    unchecked = [row["brief_id"] for row in scored_rows
                 if (row.get("premise") or {}).get("status")
                 == footage.PREMISE_UNCHECKED]
    carried_rows = [row for row in scored_rows
                    if (row.get("premise") or {}).get("status")
                    == footage.PREMISE_CARRIED]
    excluded = len(scored_rows) - len(carried_rows)
    return {
        "note": (
            "The premise is measured ALONGSIDE the score, never instead "
            "of it. A brief whose premise the substrate does not carry is "
            "still scored, and still counts as the engine's result; what "
            "this view names is the footage it needs."),
        "substrates": {
            "path": catalogue.get("path"),
            "sha256": catalogue.get("sha256"),
            "count": len(footage.substrates(catalogue)),
        },
        "briefs": {
            str(row["brief_id"]): dict(row.get("premise") or {})
            for row in scored_rows
        },
        "premise_not_carried": not_carried,
        "premise_not_carried_count": len(not_carried),
        "premise_unchecked": unchecked,
        "premise_carried_count": len(carried_rows),
        "pairing": footage.select_for_set(the_set, catalogue),
        "stage_metrics": stages.summarize(
            [row["stages"] for row in carried_rows if row.get("stages")],
            excluded=excluded),
    }


def _recorded_timeline(record: Mapping[str, Any],
                       cache: dict | None) -> grounding.RecordedTimeline:
    """The recorded timeline a record names, read once per run.

    The recorded generations are read from Ren's own store and never
    from Resolve. One read per project and timeline: several briefs run
    against the same reel, and the store is not small.
    """
    if cache is None:
        return grounding.recorded_timeline(record)
    project = record.get("project")
    project = project if isinstance(project, Mapping) else {}
    resolve_project = str(project.get("resolve_project") or "").strip()
    timeline = str(project.get("timeline") or "").strip()
    key = (resolve_project, timeline)
    if key not in cache:
        cache[key] = grounding.recorded_timeline(record)
    return cache[key]


def _criteria_coverage(scored_rows: Sequence[Mapping[str, Any]]
                       ) -> dict[str, Any]:
    """How much of each brief's acceptance criteria the plan declared.

    This is a SUPPLEMENTARY view, published ALONGSIDE the headline and
    never instead of it. The headline's planning number says how many
    briefs' plans were valid answers; this says how much of each
    brief's criteria that plan declared it covers, which is the gap the
    planning gate exposes and the number the next round of work has to
    move.

    It is measured over the briefs that EXPECT AN EDIT. A brief whose
    correct outcome is to report back has no criteria for a plan to
    cover, so counting them here would measure nothing about coverage.
    """
    total = 0
    covered = 0
    per_brief: dict[str, Any] = {}
    for row in scored_rows:
        measurement = (row.get("stages") or {}).get("planning")
        measurement = measurement if isinstance(measurement, Mapping) else {}
        detail = measurement.get("detail")
        detail = detail if isinstance(detail, Mapping) else {}
        if not detail.get("acceptance_criteria_coverage_required"):
            continue
        criteria = [str(item) for item in (detail.get("acceptance_criteria")
                                           or [])]
        uncovered = [str(item) for item in (
            detail.get("acceptance_criteria_not_declared_covered") or [])]
        total += len(criteria)
        covered += len(criteria) - len(uncovered)
        per_brief[str(row.get("brief_id"))] = {
            "acceptance_criteria": len(criteria),
            "declared_covered": len(criteria) - len(uncovered),
            "not_declared_covered": uncovered,
        }
    return {
        "note": (
            "A supplementary view, published alongside the headline: how "
            "much of each brief's acceptance criteria the plan DECLARED "
            "it covers. The headline's planning number says how many "
            "plans were valid answers; this says how much of the brief "
            "each one carried. Measured over the briefs that expect an "
            "edit - a brief whose correct outcome is to report back has "
            "no criteria for a plan to cover."),
        "briefs_expected_to_edit": len(per_brief),
        "acceptance_criteria": total,
        "declared_covered": covered,
        "not_declared_covered": total - covered,
        "value": (covered / total) if total else None,
        "briefs": per_brief,
    }


def _demonstration(records: Sequence[Mapping[str, Any]],
                   the_set: Mapping[str, Any],
                   by_id: Mapping[str, Mapping[str, Any]]) -> dict | None:
    """The recurring demonstration, when the set names one and it ran."""
    brief_id = briefs.demonstration_brief_id(the_set)
    if not brief_id:
        return None
    brief = by_id.get(brief_id)
    if brief is None:
        return None
    for record in records:
        if str(record.get("brief_id") or "") != brief_id:
            continue
        return demo.score_demo(record, briefs.public_view(brief))
    return demo.unrun_report(briefs.public_view(brief))


def write_report(report: Mapping[str, Any],
                 path: str | Path | None = None) -> Path:
    """Write the report where the live lane and the reviewer will find it."""
    target = Path(path) if path is not None else default_report_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


def read_records(source: str | Path) -> list[dict[str, Any]]:
    """The records of a run, from one file or a directory of them.

    A directory is read in name order, so a run that numbered its
    records reports them in the order it did them.
    """
    target = Path(source)
    if target.is_dir():
        paths = sorted(target.glob("*.json"))
        return [record_module.read(path) for path in paths]
    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "records" in raw:
        raw = raw["records"]
    if not isinstance(raw, list):
        raise record_module.RecordError(
            f"{target} holds neither a list of records nor a 'records' key")
    records = []
    for entry in raw:
        problems = record_module.validate(entry)
        if problems:
            raise record_module.RecordError(
                f"{target} holds a record that breaks its contract:\n  "
                + "\n  ".join(problems))
        records.append(entry)
    return records

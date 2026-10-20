"""The acceptance run: briefs in, one report out.

The runner reads what a run produced - a task record per brief - and
measures it against the brief's expectations. It does not drive the
work, it does not touch Resolve, and it does not judge: the judge is
supplied, and the runner only records what the judge said.

What the report carries:

- the BRIEF SET it ran against, with its digest, so a report names the
  set it measured.
- the FIVE STAGE metrics, each on its own denominator, never combined.
- the FAILURE CAUSES, with the bottleneck named, so the next engineering
  change has a target.
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
    judgement,
    stages,
)
from library.tools.editorial_acceptance import (
    record as record_module,
)

SCHEMA = "ren-editorial-acceptance/3"

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
                section = delivery.build_render_evidence(path)
            except delivery.DeliveryError as exc:
                section = {"path": path, "evidence_error": str(exc)}
    if judge is None:
        return section, assessment
    if not (section.get("sha256") and section.get("evidence")):
        return section, {}
    scored = judgement.assess(
        task_id=str(record.get("task_id") or record.get("brief_id") or ""),
        brief=brief, render=section, judge=judge)
    return section, scored


def score_task(record: Mapping[str, Any], brief: Mapping[str, Any],
               *, judge: Callable | None = None) -> dict[str, Any]:
    """One brief, one record, one row of the report."""
    key = briefs.oracle(brief)
    public = briefs.public_view(brief)
    briefs.assert_no_leak(public, key)
    rendered, assessment = _delivery_for(record, public, judge)
    measured_record = dict(record)
    measured_record["delivery"] = rendered
    measured_record["assessment"] = assessment
    stage_measurements = stages.measure(measured_record, public, key)
    classification = failures.classify(
        stage_measurements, task_id=str(record.get("task_id") or brief["id"]),
        expected_refusal=bool(key["expected_refusal"]))
    return {
        "brief_id": str(brief["id"]),
        "task_id": str(record.get("task_id") or brief["id"]),
        "instruction": str(public["instruction"]),
        "edge_case": key["edge_case"],
        "expected_refusal": bool(key["expected_refusal"]),
        "footage": {
            "profile": str(public["project_state"]["footage_profile"]),
            "project": record.get("project"),
            "existing_adjustments": list(
                public["project_state"]["existing_adjustments"]),
            "media_conditions": list(
                public["project_state"]["media_conditions"]),
        },
        "stages": stage_measurements,
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
                   judge: Callable | None = None) -> dict[str, Any]:
    """Measure every record against its brief, and report."""
    from library.tools.evaluation_levels import catalog as evaluation_level_catalog

    the_set = _load_brief_set(brief_set, brief_set_path)
    by_id = briefs.briefs_by_id(the_set)
    rows: list[dict[str, Any]] = []
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
        row = score_task(record, brief, judge=judge)
        row["status"] = "scored"
        rows.append(row)
    stage_rows = [row["stages"] for row in rows if row.get("stages")]
    classifications = [row["failure"] for row in rows
                        if row.get("failure")]
    measured_rows = [row for row in rows if row.get("status") == "scored"]
    passed = sum(
        row["failure"].get("failed") is False
        for row in measured_rows)
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
        "failure_causes": failures.bottleneck_summary(classifications),
        "results": rows,
    }
    if demonstration is not None:
        report["demonstration"] = demonstration
    return report


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

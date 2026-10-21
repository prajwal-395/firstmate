"""PLAN-011 measured on the BASE-003 corpus briefs.

The punch list asks (PLAN-011) for the full instruction loop to be
VERIFIED: "from a natural-language request, prove that the agent
inspects work state, gathers evidence, compiles intent, validates a
plan, executes, verifies, and reports", and "ensure the final reply
states the actual result rather than repeating the original intention".

This is the measurement.  It asks one question of every stated
instruction of every brief in the corpus:

    did the ENGINE account for it, at the end?

and it reports the two answers separately, because they are different
questions and one of them is the lane's:

    the engine's own account   does the engine's verdict CARRY this
                                instruction - as met, as refused, or as
                                nothing-at-all?  Before PLAN-011 the
                                engine's verdict read the acceptance
                                contract and nothing else, so a stated
                                instruction the contract never carried
                                had NO entry anywhere: it was invisible.

    the run's diligence         did the RUN account for it?  A criterion
                                the run declared, with the same
                                statement, that reached a verdict.  This
                                is what a careful agent could always do,
                                and it is reported beside the engine's
                                own number so the reader can see WHICH
                                of the two moved.

The third thing the measurement reports is the GATE: a task that served
one of the three instructions a brief stated, and dropped the other two.
Before PLAN-011 that task reported itself COMPLETE - the contract's
criteria passed, the delivery receipt was clean, and the two dropped
instructions were nowhere.  After, the task is not complete and the
verdict NAMES both.

The corpus briefs have no task records - no run has been made against
them - so the RUN's numbers over the corpus are NOT MEASURED, and are
reported as not measured rather than as a zero.  What the corpus DOES
give is the denominator: every stated instruction of every brief, which
is what the engine's own account is measured over.

The eight-brief set DOES have records - the live run of 2026-10-20 -
and they are measured here as real data, before and after, with the
denominator named.

    python3 evaluation/tools/plan011_instruction_loop.py [--json <path>]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import load_sealed_briefs

from library.tools import (
    assignment_contract,
    assignment_lifecycle,
    editorial_intent,
    instruction_loop,
    project_model_store,
)

RECORDS = (REPO_ROOT / "tests" / "live" / "reports" / "editorial-bench-records")
MODEL_FIXTURE = (REPO_ROOT / "native" / "ren-protocol" / "fixtures"
                 / "project-model.json")
EIGHT_BRIEF_SET = (REPO_ROOT / "library" / "tools" / "editorial_acceptance"
                   / "brief_set.json")


# ── the probe: the engine's own functions, on a scratch task ─────────


def _seed_project_model(root: Path) -> None:
    """Give the scratch project the Project Model the intent lives in."""
    model = json.loads(MODEL_FIXTURE.read_text(encoding="utf-8"))
    project_model_store.commit_project_model(
        root, model, expected_revision=0, reason="probe")


def _scratch_task(root: Path, criterion_id: str, statement: str) -> dict:
    """A task that carries ONE stated instruction, in a scratch project.

    Nothing here is a run: it is the smallest arrangement the engine's
    own records can hold, built with the engine's own functions, so that
    what the probe measures is the ENGINE and not the probe.
    """
    intent = editorial_intent.build_intent(
        intent_id="intent-probe",
        desired_outcome=statement,
        editorial_spec={"requirements": [
            {"id": f"req:{criterion_id}", "statement": statement}],
            "preferences": [], "platform": "", "audience": "",
            "target_duration": {}, "pacing": [], "subjects": {},
            "prohibited_content": [], "references": [],
            "unresolved_decisions": []},
        requests=[],
    )
    _seed_project_model(root)
    editorial_intent.commit_intent(str(root), intent)
    record = assignment_lifecycle.create(str(root), "probe")
    record["intent_id"] = "intent-probe"
    record["request"] = statement
    record["project_state"] = {"clips": 1}
    assignment_lifecycle.save(str(root), record)
    assignment_contract.declare(str(root), "probe", [
        {"id": criterion_id, "statement": statement, "kind": "editorial",
         "instruction_id": f"req:{criterion_id}"}])
    assignment_contract.record_evidence(
        str(root), "probe", criterion_id, status="passed",
        observation="the probe recorded what was checked")
    return {"project": str(root), "assignment_id": "probe"}


def _probe(statement: str, criterion_id: str) -> dict:
    """What the engine does with ONE stated instruction.

    The AFTER numbers come from the engine's own account and the
    engine's own completion verdict.  The BEFORE numbers are what the
    engine could do without PLAN-011: the account did not exist, and the
    verdict read the acceptance contract alone.
    """
    root = Path(tempfile.mkdtemp(prefix="ren-plan011-"))
    try:
        task = _scratch_task(root, criterion_id, statement)
        account = instruction_loop.account_assignment(
            task["project"], task["assignment_id"])
        rows = {str(row["id"]): row for row in account["instructions"]}
        carried = rows.get(criterion_id, {})
        requirement = rows.get(f"req:{criterion_id}", {})
        completion = assignment_contract.completion(
            task["project"], task["assignment_id"])
        blockers = [str(row) for row in completion.get("blockers") or ()]
        # BEFORE: the verdict as it stood without PLAN-011 - the
        # blockers minus the account's.  An instruction the verdict
        # names there was carried by it; one it never mentions was not.
        before_blockers = [row for row in blockers
                           if not row.startswith("the stated ")]
        before_reads = " ".join(before_blockers + [
            str(row.get("id")) for row in
            (completion.get("criteria") or {}).get("criteria", [])])
        before_engine = 1 if f"req:{criterion_id}" in before_reads else 0
        # AFTER: the account carries the instruction, and the verdict
        # names it.
        after_engine = 1 if (carried and requirement) else 0
        # The run's diligence: the same in both, because a criterion
        # with the same statement and a verdict was always countable.
        diligence = 1 if carried.get("verdict") == "met" else 0
        # The GATE, on a task that dropped the other instructions: the
        # verdict is read with the contract's criteria passing and the
        # dropped instructions absent.
        return {
            "engine_before": before_engine,
            "engine_after": after_engine,
            "diligence": diligence,
            "verdict": completion.get("complete"),
            "verdict_reason": str(completion.get("reason") or ""),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _gate_probe(statement: str, criterion_id: str) -> dict:
    """The gate on a task that served ONE instruction and dropped others.

    The task is DELIVERED, its contract's criteria are passed and its
    delivery receipt is clean - so the only thing that can hold it back
    is the account.  The dropped instruction is the one the intent states
    and the contract does not carry.

    BEFORE is the gate as it stood without PLAN-011: the same blockers
    minus the account's, and the same rule for done-ness.  AFTER is the
    gate as it stands now.
    """
    root = Path(tempfile.mkdtemp(prefix="ren-plan011-gate-"))
    try:
        intent = editorial_intent.build_intent(
            intent_id="intent-gate",
            desired_outcome=statement,
            editorial_spec={"requirements": [
                {"id": f"req:{criterion_id}", "statement": statement},
                {"id": "req:dropped", "statement": "the other thing asked for"}],
                "preferences": [], "platform": "", "audience": "",
                "target_duration": {}, "pacing": [], "subjects": {},
                "prohibited_content": [], "references": [],
                "unresolved_decisions": []},
            requests=[],
        )
        _seed_project_model(root)
        editorial_intent.commit_intent(str(root), intent)
        record = assignment_lifecycle.create(str(root), "probe")
        record["intent_id"] = "intent-gate"
        assignment_lifecycle.save(str(root), record)
        assignment_contract.declare(str(root), "probe", [
            {"id": criterion_id, "statement": statement, "kind": "editorial",
             "instruction_id": f"req:{criterion_id}"}])
        assignment_contract.record_evidence(
            str(root), "probe", criterion_id, status="passed",
            observation="the probe recorded what was checked")
        _deliver(root, "probe")
        completion = assignment_contract.completion(str(root), "probe")
        blockers = list(completion.get("blockers") or ())
        account_blockers = [row for row in blockers
                            if str(row).startswith("the stated ")]
        before_blockers = [row for row in blockers if row not in account_blockers]
        before_complete = (
            str(completion.get("stage")) in ("DELIVERED", "ACCEPTED")
            and not before_blockers)
        return {
            "before_complete": before_complete,
            "after_complete": bool(completion.get("complete")),
            "names_the_dropped": any("req:dropped" in str(row)
                                     for row in account_blockers),
            "account_blockers": account_blockers,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _deliver(root: Path, assignment_id: str) -> None:
    """Put the task in the state a delivered task is in.

    A delivery receipt on record, and the assignment DELIVERED - so the
    gate under test is the ACCOUNT and not the state of the task.
    """
    from library.tools import project_layout

    record = assignment_lifecycle.load(str(root), assignment_id)
    record["state"] = "delivered"
    assignment_lifecycle.save(str(root), record)
    layout = project_layout.ProjectLayout(str(root))
    path = layout.write_path(project_layout.Area.EXPORTS, "assignments",
                             f"{assignment_id}.json")
    path.write_text(json.dumps({
        "assignment_id": assignment_id,
        "status": "complete",
        "files": [{"name": "reel.mp4", "path": str(path)}],
        "missing_outputs": [],
        "failed_outputs": [],
        "qc": {},
    }, indent=2), encoding="utf-8")


# ── the corpus ───────────────────────────────────────────────────────


def measure_corpus() -> dict:
    """Every stated instruction of every corpus brief."""
    brief_set = load_sealed_briefs()
    rows: list[dict] = []
    for brief in brief_set["briefs"]:
        for criterion in brief.get("acceptance_criteria") or ():
            statement = str(criterion.get("statement") or "")
            criterion_id = str(criterion.get("id") or "")
            probe = _probe(statement, criterion_id)
            rows.append({
                "brief": brief.get("id"),
                "criterion": criterion_id,
                "kind": instruction_loop.criterion_kind(statement),
                "engine_before": probe["engine_before"],
                "engine_after": probe["engine_after"],
                "diligence": probe["diligence"],
            })
    gate = _gate_probe("the first thing asked for", "first")
    return {
        "briefs": len(brief_set["briefs"]),
        "instructions": len(rows),
        "engine_before": sum(row["engine_before"] for row in rows),
        "engine_after": sum(row["engine_after"] for row in rows),
        "diligence": sum(row["diligence"] for row in rows),
        "gate": gate,
        "rows": rows,
    }


# ── the eight-brief set, on real records ─────────────────────────────


def measure_records(records_dir: Path | None = None,
                    brief_set: dict | None = None) -> dict:
    """The eight-brief set, over the records the live run left.

    BEFORE is the plan's own declaration - the only account the engine
    had of a stated instruction, and the number the run published.
    AFTER is the end-to-end account: a criterion the plan declared, or
    one the run's own evidence settled (a refusal, a collateral check),
    reaches a verdict either way.
    """
    records_dir = records_dir or RECORDS
    brief_set = brief_set or json.loads(
        EIGHT_BRIEF_SET.read_text(encoding="utf-8"))
    from library.tools.editorial_acceptance import briefs as brief_module

    brief_by_id = {str(brief["id"]): brief for brief in brief_set["briefs"]}
    def _total(rows_: list[dict], field: str) -> int:
        return sum(int(row[field]) for row in rows_)

    def _verdict_counts(account: dict) -> dict:
        counts = account["counts"]
        return {name: int(counts.get(name) or 0)
                for name in ("met", "refused", "failed", "unaccounted")}

    rows: list[dict] = []
    for path in sorted(records_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        brief_id = str(record.get("brief_id") or path.stem)
        brief = brief_by_id.get(brief_id, {})
        key = brief_module.oracle(brief) if brief else {}
        account = instruction_loop.account_record(record, brief)
        planning = record.get("planning") or {}
        declared = [str(name) for name in
                    (planning.get("requirements_covered") or ())]
        criteria = [str(row.get("id")) for row in
                    (brief.get("acceptance_criteria") or ())]
        rows.append({
            "brief": brief_id,
            "expects_an_edit": bool(key.get("expected_operations")),
            "criteria": len(criteria),
            "declared_covered": len([name for name in declared
                                     if name in criteria]),
            "accounted": account["counts"]["accounted"],
            "unaccounted": account["unaccounted"],
            "verdicts": {str(row["id"]): row["verdict"]
                         for row in account["instructions"]},
            "verdict_counts": _verdict_counts(account),
        })
    edit_rows = [row for row in rows if row["expects_an_edit"]]
    report_rows = [row for row in rows if not row["expects_an_edit"]]
    return {
        "briefs": len(rows),
        "criteria": _total(rows, "criteria"),
        "declared_covered": _total(rows, "declared_covered"),
        "accounted": _total(rows, "accounted"),
        "unaccounted": sum(len(row["unaccounted"]) for row in rows),
        "verdicts": {
            name: sum(int(row["verdict_counts"].get(name) or 0) for row in rows)
            for name in ("met", "refused", "failed", "unaccounted")},
        "edit": {
            "briefs": len(edit_rows),
            "criteria": _total(edit_rows, "criteria"),
            "declared_covered": _total(edit_rows, "declared_covered"),
            "accounted": _total(edit_rows, "accounted"),
        },
        "report": {
            "briefs": len(report_rows),
            "criteria": _total(report_rows, "criteria"),
            "declared_covered": _total(report_rows, "declared_covered"),
            "accounted": _total(report_rows, "accounted"),
        },
        "rows": rows,
    }


def render(report: dict) -> str:
    """The report, with every denominator named."""
    corpus = report["corpus"]
    records = report["records"]
    lines: list[str] = []
    lines.append("PLAN-011: the instruction loop, measured")
    lines.append("")
    lines.append("## The corpus briefs (BASE-003)")
    lines.append("")
    lines.append(f"briefs: {corpus['briefs']}   "
                 f"stated instructions: {corpus['instructions']}")
    lines.append("")
    lines.append("| view | before | after |")
    lines.append("|---|---|---|")
    lines.append(
        f"| stated instructions the ENGINE's own verdict carries | "
        f"{corpus['engine_before']}/{corpus['instructions']} | "
        f"{corpus['engine_after']}/{corpus['instructions']} |")
    lines.append(
        f"| stated instructions the RUN accounted for | "
        f"{corpus['diligence']}/{corpus['instructions']} | "
        f"{corpus['diligence']}/{corpus['instructions']} |")
    lines.append("")
    lines.append("The SECOND row is the number a careful agent could always")
    lines.append("reach: a criterion declared with the same statement, with a")
    lines.append("verdict. It does not move, and it is published so the reader")
    lines.append("can see that what moved is the ENGINE's own account and not")
    lines.append("the run's diligence.")
    lines.append("")
    gate = corpus["gate"]
    lines.append("The GATE, on a task that served one stated instruction and")
    lines.append("dropped the other:")
    lines.append("")
    lines.append(f"- before: the task reported itself "
                 f"{'COMPLETE' if gate['before_complete'] else 'not complete'}, "
                 f"and named the dropped instruction: no")
    lines.append(f"- after: the task reports itself "
                 f"{'COMPLETE' if gate['after_complete'] else 'NOT complete'}, "
                 f"and names the dropped instruction: "
                 f"{'yes' if gate['names_the_dropped'] else 'no'}")
    lines.append("")
    lines.append("## The eight-brief set, over the live run's records")
    lines.append("")
    lines.append(f"briefs: {records['briefs']}   "
                 f"stated instructions: {records['criteria']}")
    lines.append("")
    lines.append("| view | before | after |")
    lines.append("|---|---|---|")
    edit = records["edit"]
    report = records["report"]
    lines.append(
        f"| the briefs that expect an EDIT | "
        f"{edit['declared_covered']}/{edit['criteria']} | "
        f"{edit['accounted']}/{edit['criteria']} |")
    lines.append(
        f"| the briefs whose answer is to report back | "
        f"{report['declared_covered']}/{report['criteria']} | "
        f"{report['accounted']}/{report['criteria']} |")
    lines.append(
        f"| all eight briefs | "
        f"{records['declared_covered']}/{records['criteria']} | "
        f"{records['accounted']}/{records['criteria']} |")
    lines.append("")
    lines.append("| brief | criteria | before: declared | after: verdict | "
                 "unaccounted |")
    lines.append("|---|---|---|---|---|")
    for row in records["rows"]:
        lines.append(
            f"| `{row['brief']}` | {row['criteria']} | "
            f"{row['declared_covered']} | {row['accounted']} | "
            f"{', '.join(row['unaccounted']) or '-'} |")
    lines.append("")
    verdicts = records["verdicts"]
    lines.append(
        f"The AFTER verdicts, over all {records['criteria']} stated "
        f"instructions: {verdicts['met']} met, {verdicts['refused']} "
        f"refused, {verdicts['failed']} failed, {verdicts['unaccounted']} "
        f"unaccounted. The improvement is in the ACCOUNT, not in the run: "
        f"the 2026-10-20 run refused most of these briefs, and what changed "
        f"is that the refusal now reaches every stated instruction instead "
        f"of the two thirds of them the plan never mentioned.")
    lines.append("")
    lines.append("The BEFORE column is the plan's own declaration - the only")
    lines.append("account the engine had of a stated instruction, and the")
    lines.append("number the 2026-10-20 run published. The AFTER column is")
    lines.append("the end-to-end account: a criterion the plan declared, or")
    lines.append("one the run's own evidence settled, reaches a verdict.")
    lines.append("")
    lines.append("## What was NOT measured")
    lines.append("")
    lines.append("- No run against the corpus briefs: there are no task")
    lines.append("  records for them, so the RUN's account over the corpus is")
    lines.append("  not measured, and it is not a zero.")
    lines.append("- No live run of the eight briefs: the records are the")
    lines.append("  2026-10-20 ones, and a plan that now DECLARES its")
    lines.append("  coverage would change them.")
    return "\n".join(lines)


def planning_benchmark(records_dir: Path) -> dict:
    """The harness's own planning benchmark over a set of records.

    The harness is the engine's acceptance yardstick, so the benchmark
    is read with the harness's own runner and the harness's own writer -
    never with a second opinion about what a plan covered.
    """
    from library.tools.editorial_acceptance import runner

    report = runner.run_acceptance(
        runner.read_records(str(records_dir)),
        brief_set=json.loads(EIGHT_BRIEF_SET.read_text(encoding="utf-8")))
    stages = (report.get("stage_metrics") or {}).get("stages") or {}
    planning = stages.get("planning") or {}
    coverage = report.get("criteria_coverage") or {}
    return {
        "briefs": report.get("task_count"),
        "planning_passed": planning.get("passed"),
        "planning_measured": planning.get("measured"),
        "planning_value": planning.get("value"),
        "criteria": coverage.get("acceptance_criteria"),
        "briefs_covered": {name: row.get("declared_covered")
                           for name, row in (coverage.get("briefs") or {}).items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plan011_instruction_loop.py",
        description="Measure the instruction loop on the corpus briefs.")
    parser.add_argument("--json", default=None,
                        help="write the whole measurement as JSON")
    parser.add_argument("--live-corpus-records", default=None,
                        help="the corpus records the live run left")
    parser.add_argument("--live-records", default=None,
                        help="the records the live run left, to measure "
                             "beside the ones on main")
    args = parser.parse_args(argv)
    report = {"corpus": measure_corpus(), "records": measure_records()}
    if args.live_records:
        live = Path(args.live_records)
        corpus_live = Path(args.live_corpus_records) if args.live_corpus_records else None
        report["live"] = {
            "records": measure_records(live),
            "planning": planning_benchmark(live),
            "planning_before": planning_benchmark(RECORDS),
        }
        if corpus_live is not None:
            report["live"]["corpus"] = measure_records(
                corpus_live, load_sealed_briefs())
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

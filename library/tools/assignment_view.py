"""What Ren understood about a request, and what it has done with it.

One read-only projection over the records an assignment already owns. It
answers the six questions a host otherwise has to answer for itself, and
each answer is READ from a record that already exists: the assignment's
own checkpoint, the revision records it owns, the edit ledger, the run's
declared gaps, the delivery receipt.

    understood     what Ren took the request to mean              (AGENT-018)
    proposed       the changes it would make, and their evidence  (AGENT-019)
    applied        what it changed, against what it only planned  (AGENT-020)
    uncertainties  what it could not determine, and what it
                   cannot do                                          (AGENT-021)
    verified       what it proved, and what it did not             (AGENT-022)
    corrections    the focused corrections that update this task  (AGENT-023)

This is a VIEW, not a store. `project_work_state` is the precedent and the
rule is the same one: asking what Ren understood must not change what
Ren understands. Nothing here writes, nothing here decides, and nothing
here invents a judgement - a section with no record behind it says so
rather than filling the gap.

The final architectural principle of the captain's punch list is that the
MODEL must not be responsible for understanding the internal machinery of
editing. Reading "which changes were applied and which were merely
planned" out of a checkpoint dictionary is exactly that machinery, done by
the wrong reader; this module is the deterministic answer, and the model
reads the answer.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

VERSION = 1

SECTIONS = ("understood", "proposed", "applied", "uncertainties",
            "verified", "corrections")
"""The six questions this projection answers, in the order a reader needs
them. One enumeration: a section with no name in this tuple does not
exist."""

#: A revision's status is read from `revise`, the module that owns the
#: vocabulary. This module does not restate it: two vocabularies for one
#: state is how a reader ends up reading a planned edit as an applied one.


def _root(project) -> str:
    return str(Path(project).expanduser().resolve())


def _read_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _revisions(project: str, record: dict) -> list[dict]:
    """The revisions that update THIS assignment, oldest first.

    A revision names the assignment it belongs to. A revision about a reel
    this assignment owns belongs to it too, even when the note arrived
    before the assignment did - otherwise the first correction of a request
    reads as belonging to nobody.
    """
    from library.tools import revise

    owned = {str(entry.get("name")) for entry in
             (record.get("reels") or {}).values() if entry.get("name")}
    mine = []
    for entry in revise.records(project):
        if (entry.get("assignment_id") == record.get("assignment_id")
                or (entry.get("reel") and str(entry["reel"]) in owned)):
            mine.append(entry)
    return sorted(mine, key=lambda entry: (entry.get("created_at") or "",
                                           entry.get("revise_id") or ""))


def _moments(project: str) -> list[dict]:
    """The reels proposal's moments, as what the request became."""
    from library.tools import reel_proposal

    path = reel_proposal.proposal_path(project)
    if not path.is_file():
        return []
    try:
        moments = reel_proposal.read_proposal(path)
    except (OSError, ValueError):
        return []
    rows = []
    for moment in moments:
        rows.append({"number": int(moment.number), "name": moment.timeline_name,
                     "reason": moment.reason,
                     "timeline_start": moment.timeline_start,
                     "timeline_end": moment.timeline_end})
    return rows


def _spec_for(project: str, revisions: list[dict]):
    """The newest revision's TRANSLATED spec, with the revision it came
    from. None when no revision has been translated yet."""
    from library.tools import edit_spec

    for revision in reversed(revisions):
        spec_id = revision.get("spec_id")
        if not spec_id:
            continue
        try:
            proposal = edit_spec.proposal_for_spec(project, spec_id)
        except (edit_spec.EditSpecError, OSError, ValueError, KeyError,
                TypeError):
            continue
        return revision, proposal
    return None, None


def _understood(project: str, record: dict, revisions: list[dict]) -> dict:
    """What Ren took the request to mean."""
    request = str(record.get("request") or record.get("brief") or "")
    source = ("assignment.request" if record.get("request")
              else "assignment.brief" if record.get("brief") else "")
    revision, proposal = _spec_for(project, revisions)
    reading = "not_translated"
    ops: list[str] = []
    questions: list[dict] = []
    if proposal is not None:
        reading = "translated"
        ops = [str(change.get("op") or "") for change in
               (proposal.get("planned_changes") or [])]
        questions = list(proposal.get("questions") or [])
    elif any(entry.get("status") == "awaiting_translation"
             for entry in revisions):
        reading = "awaiting_translation"
    elif any(entry.get("status") == "question" for entry in revisions):
        reading = "question"
    return {
        "request": request,
        "request_source": source,
        "reading": reading,
        "ops": ops,
        "questions": questions,
        "moments": _moments(project),
        "revision_id": (revision or {}).get("revise_id"),
        "spec_id": (revision or {}).get("spec_id"),
    }


def _evidence(decisions) -> list[dict]:
    """The recorded decisions a note implicates, each with the evidence
    its own decision record carries."""
    rows = []
    for decision in ((decisions or {}).get("implicated") or []):
        if not isinstance(decision, dict):
            continue
        rows.append({key: decision.get(key) for key in
                     ("node_id", "action", "reason", "evidence", "confidence")
                     if decision.get(key) is not None})
    return rows


def _proposed(project: str, record: dict, revisions: list[dict]) -> dict:
    """The changes Ren would make, and the evidence behind them."""
    revision, proposal = _spec_for(project, revisions)
    changes: list[dict] = []
    untouched: list[str] = []
    evidence: list[dict] = []
    note = ""
    source = ""
    if proposal is not None:
        changes = list(proposal.get("planned_changes") or [])
        untouched = list(proposal.get("untouched_categories") or [])
        note = str(proposal.get("note") or "")
        source = f"revision:{revision.get('revise_id')}"
        for entry in revisions:
            if entry.get("revise_id") == revision.get("revise_id"):
                evidence = _evidence(entry.get("decisions"))
                break
    else:
        for moment in _moments(project):
            changes.append({
                "op": "reel.propose",
                "change": moment["reason"],
                "anchor": moment["name"],
                "category": "sequence",
                "values": {"number": moment["number"],
                           "timeline_start": moment["timeline_start"],
                           "timeline_end": moment["timeline_end"]}})
        if changes:
            source = "reel_proposal"
            note = "Proposed reels only. No timeline has been built."
    if not source:
        source = "none"
    return {"changes": changes, "evidence": evidence,
            "untouched_categories": untouched, "source": source, "note": note}


def _revision_entry(revision: dict, bucket: str) -> dict:
    """One revision, in the shape the applied section uses."""
    what = ""
    if bucket == "landed":
        what = str((revision.get("change") or {}).get("summary") or "")
    elif bucket == "not_applied":
        what = str(revision.get("refusal")
                   or revision.get("failed_command") or "")
    if not what:
        what = str(revision.get("note") or "")
    return {"id": revision.get("revise_id"), "kind": "revision",
            "what": what, "status": revision.get("status"),
            "at": revision.get("updated_at") or revision.get("created_at"),
            "reel": revision.get("reel") or ""}


def _reel_bucket(status: str) -> str:
    """Where a reel's status sits: proposed work, work in flight, or work
    that is on a timeline."""
    if status in ("proposed", "approved"):
        return "planned"
    if status in ("built", "signed_off", "delivered"):
        return "landed"
    return "planned"


def _applied(record: dict, revisions: list[dict]) -> dict:
    """What Ren changed, against what it only planned."""
    from library.tools import revise

    landed: list[dict] = []
    in_flight: list[dict] = []
    planned: list[dict] = []
    not_applied: list[dict] = []
    for revision in revisions:
        status = revision.get("status")
        if status in revise.LANDED:
            landed.append(_revision_entry(revision, "landed"))
        elif status in revise.PLANNED:
            planned.append(_revision_entry(revision, "planned"))
        elif status in revise.NOT_APPLIED:
            not_applied.append(_revision_entry(revision, "not_applied"))
    for entry in (record.get("reels") or {}).values():
        row = {"id": entry.get("name"), "kind": "reel",
               "what": f"reel {entry.get('name')} is {entry.get('status')}",
               "status": entry.get("status"), "at": entry.get("built_at")
               or entry.get("delivered_at") or "",
               "reel": entry.get("name") or ""}
        bucket = _reel_bucket(str(entry.get("status") or ""))
        if bucket == "landed":
            landed.append(row)
        else:
            planned.append(row)
    for key, checkpoint in (record.get("operations") or {}).items():
        status = checkpoint.get("status")
        request = checkpoint.get("request") or {}
        row = {"id": checkpoint.get("operation_id") or key, "kind": "operation",
               "what": str(request.get("verb")
                           or checkpoint.get("operation_id") or ""),
               "status": status, "at": checkpoint.get("finished_at")
               or checkpoint.get("started_at") or "", "reel": ""}
        if status == "completed":
            landed.append(row)
        elif status == "running":
            in_flight.append(row)
        else:
            not_applied.append(row)
    return {
        "landed": landed, "in_flight": in_flight, "planned": planned,
        "not_applied": not_applied,
        "counts": {"landed": len(landed), "in_flight": len(in_flight),
                   "planned": len(planned), "not_applied": len(not_applied)},
    }


def _declared_gaps(project: str) -> list[dict]:
    """What the run's own steps said they could not determine.

    The reading is the run's, not this module's: a step that declared a
    gap and a step that stayed silent are different records and are kept
    apart, because "nothing was missing" and "nobody answered" are not
    the same fact.
    """
    from library.tools.project_layout import ProjectLayout

    state = _read_json(ProjectLayout(project).pipeline_data_path)
    rows = []
    for row in ((state or {}).get("undetermined_declarations") or []):
        if not isinstance(row, dict):
            continue
        rows.append({key: row.get(key) for key in
                     ("step_id", "reading", "attempt", "entries", "malformed",
                      "from_a_previous_run")
                     if key in row})
    return rows


def _blocked_capabilities(project: str, record: dict) -> list[dict]:
    """What Ren cannot do for this request, and what would unblock it."""
    from library.tools import progress

    rows: list[dict] = []
    for checkpoint in (record.get("operations") or {}).values():
        envelope = checkpoint.get("envelope") or {}
        refusal = envelope.get("refusal") or {}
        for item in (refusal.get("unsatisfied") or []):
            rows.append({"id": item.get("name"), "source": "assignment",
                         "reason": f"not produced by "
                                   f"{item.get('produced_by') or 'any step'}",
                         "next_action": ""})
    for item in progress.capability_availability(project)[1]:
        rows.append({"id": item.get("id"), "source": "capability",
                     "reason": item.get("reason") or "",
                     "next_action": item.get("next_action") or ""})
    return rows


def _uncertainties(project: str, record: dict, revisions: list[dict]) -> dict:
    """What Ren could not determine, and what it cannot do."""
    questions: list[dict] = []
    for revision in revisions:
        if revision.get("status") == "question":
            questions.append({"revision_id": revision.get("revise_id"),
                              "question": (revision.get("question")
                                           or {}).get("question") or ""})
    return {
        "declared_gaps": _declared_gaps(project),
        "blocked_capabilities": _blocked_capabilities(project, record),
        "open_questions": questions,
    }


def _verified(project: str, record: dict,
              revisions: list[dict]) -> dict:
    """What Ren proved, and what it did not."""
    from library.tools import revise

    verified: list[dict] = []
    unverified: list[dict] = []
    for revision in revisions:
        state = str((revision.get("verification") or {}).get("state") or "")
        row_id = revision.get("revise_id")
        if state == "axi_verified":
            receipt = (revision.get("verification") or {}).get("receipt") or {}
            contract = receipt.get("execution_contract") or {}
            verified.append({
                "id": row_id, "kind": "revision",
                "what": str((revision.get("change") or {}).get("summary")
                            or revision.get("note") or ""),
                "evidence": {
                    "receipt_status": receipt.get("status"),
                    "lifecycle_stage": (receipt.get("lifecycle")
                                        or {}).get("stage"),
                    "generation": receipt.get("generation"),
                    "verification_status": (contract.get("verification")
                                            or {}).get("status"),
                }})
        elif revision.get("status") in revise.NOT_APPLIED or state:
            unverified.append({
                "id": row_id, "kind": "revision",
                "what": str((revision.get("change") or {}).get("summary")
                            or revision.get("note") or ""),
                "why": (str(revision.get("refusal")
                            or revision.get("failed_command") or "")
                        or "the requested outcome has no request-specific "
                           "verification")})
    for receipt in _delivery_receipts(project, record):
        failed = receipt.get("failed_outputs") or []
        row = {"id": receipt.get("assignment_id"), "kind": "delivery",
               "what": "the delivered file's own checks",
               "evidence": {"status": receipt.get("status"),
                            "qc": receipt.get("qc") or {},
                            "failed_outputs": failed}}
        if receipt.get("status") == "complete":
            verified.append(row)
        else:
            row["why"] = ("failed outputs: " + ", ".join(failed)
                          if failed else
                          "the delivery receipt is not complete")
            unverified.append(row)
    return {"verified": verified, "unverified": unverified,
            "counts": {"verified": len(verified),
                       "unverified": len(unverified)}}


def _delivery_receipts(project: str, record: dict) -> list[dict]:
    """The delivery receipts written for this assignment."""
    from library.tools.project_layout import Area, ProjectLayout

    directory = ProjectLayout(project).read_path(Area.EXPORTS, "assignments")
    rows = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        receipt = _read_json(path)
        if receipt and receipt.get("assignment_id") == record.get("assignment_id"):
            rows.append(receipt)
    return rows


def _corrections(project: str, record: dict,
                 revisions: list[dict]) -> dict:
    """The focused corrections that update this task."""
    from library.tools import revise

    quoted = shlex.quote(_root(project))
    items = []
    for revision in revisions:
        row = {"revise_id": revision.get("revise_id"),
               "note": revision.get("note") or "",
               "status": revision.get("status"),
               "reel": revision.get("reel") or "",
               "at": revision.get("updated_at") or revision.get("created_at"),
               "resume": f"ren revise {quoted} --id {revision.get('revise_id')}",
               "answer_with": ""}
        if revision.get("status") == "question":
            row["answer_with"] = (f"ren revise {quoted} --id "
                                  f"{revision.get('revise_id')} "
                                  f"--answer \"...\"")
        items.append(row)
    open_ids = [row["revise_id"] for row in items
                if row["status"] not in revise.FINISHED]
    return {
        "items": items,
        "open": open_ids,
        "note": "A correction updates this task; it does not start a new one.",
    }


def task_state(project: str, assignment_id: str | None = None) -> dict:
    """The six answers, for the assignment a host means.

    With no ``assignment_id`` the project's open assignment answers, else
    the newest one of any state. A project with no assignment answers with
    the same six sections, each one empty, and says so.
    """
    from library.tools import assignment_lifecycle as lifecycle
    from library.tools import assignment_spine

    root = _root(project)
    record = lifecycle.resolve(root, assignment_id)
    if record is None:
        return {
            "version": VERSION, "assignment_id": None, "kind": None,
            "state": None, "stage": "understand",
            **{section: _empty(section) for section in SECTIONS},
            "resume_point": None,
            "note": "No assignment is recorded yet; a proposal or an edit "
                    "run opens one.",
        }
    revisions = _revisions(root, record)
    from library.tools import assignment_interaction

    return {
        "version": VERSION,
        "assignment_id": record.get("assignment_id"),
        "kind": record.get("kind"),
        "state": record.get("state"),
        "stage": assignment_interaction.STAGE_BY_STATE.get(
            record.get("state"), "understand"),
        "understood": _understood(root, record, revisions),
        "proposed": _proposed(root, record, revisions),
        "applied": _applied(record, revisions),
        "uncertainties": _uncertainties(root, record, revisions),
        "verified": _verified(root, record, revisions),
        "corrections": _corrections(root, record, revisions),
        "resume_point": assignment_spine.resume_point(root, record),
    }


def _empty(section: str) -> dict:
    """An empty answer for one section, with the same keys it always has."""
    if section == "applied":
        return {"landed": [], "in_flight": [], "planned": [],
                "not_applied": [],
                "counts": {"landed": 0, "in_flight": 0, "planned": 0,
                           "not_applied": 0}}
    if section == "verified":
        return {"verified": [], "unverified": [],
                "counts": {"verified": 0, "unverified": 0}}
    if section == "corrections":
        return {"items": [], "open": [],
                "note": "A correction updates this task; it does not start "
                        "a new one."}
    if section == "uncertainties":
        return {"declared_gaps": [], "blocked_capabilities": [],
                "open_questions": []}
    if section == "proposed":
        return {"changes": [], "evidence": [], "untouched_categories": [],
                "source": "none", "note": ""}
    return {"request": "", "request_source": "", "reading": "not_translated",
            "ops": [], "questions": [], "moments": [], "revision_id": None,
            "spec_id": None}


def render(view: dict) -> str:
    """The six answers, as plain text a person or an agent can relay."""
    from library.tools import revise

    lines: list[str] = []
    if not view.get("assignment_id"):
        lines.append(str(view.get("note") or "No assignment is recorded yet."))
        return "\n".join(lines)
    understood = view["understood"]
    lines.append(f"Assignment {view['assignment_id']} ({view['kind'] or 'edit'})"
                 f" is {view['state']}, at the {view['stage']} stage.")
    request = understood.get("request") or ""
    lines.append("What Ren understood: " + (request or "(no words on record)"))
    if understood.get("ops"):
        lines.append("  read as: " + ", ".join(understood["ops"]))
    if understood.get("moments"):
        for moment in understood["moments"]:
            lines.append(f"  reel {moment['number']} {moment['name']}: "
                         f"{moment['reason']}")
    for question in understood.get("questions") or []:
        lines.append("  asked: " + str(question.get("question") or question))
    proposed = view["proposed"]
    if proposed.get("changes"):
        lines.append(f"Proposed ({proposed['source']}):")
        for change in proposed["changes"]:
            lines.append(f"  {change.get('op')}: {change.get('change')}"
                         + (f"  [{change.get('anchor')}]"
                            if change.get("anchor") else ""))
    for row in proposed.get("evidence") or []:
        lines.append(f"  evidence: {row.get('node_id')} - {row.get('reason')}")
    applied = view["applied"]
    counts = applied["counts"]
    lines.append(f"Applied {counts['landed']}, planned {counts['planned']}, "
                 f"in flight {counts['in_flight']}, not applied "
                 f"{counts['not_applied']}.")
    for row in applied["landed"]:
        lines.append(f"  applied: {row.get('what') or row.get('id')}")
    for row in applied["planned"]:
        lines.append(f"  planned: {row.get('what') or row.get('id')}")
    for row in applied["not_applied"]:
        lines.append(f"  not applied: {row.get('what') or row.get('id')}")
    uncertainties = view["uncertainties"]
    for row in uncertainties["declared_gaps"]:
        if row.get("reading") != "declared":
            continue
        for entry in row.get("entries") or []:
            lines.append(f"  could not determine ({row.get('step_id')}): "
                         f"{entry.get('what')}")
    for row in uncertainties["blocked_capabilities"]:
        lines.append(f"  blocked capability {row.get('id')}: {row.get('reason')}")
    verified = view["verified"]
    lines.append(f"Verified {verified['counts']['verified']}, unverified "
                 f"{verified['counts']['unverified']}.")
    for row in verified["verified"]:
        lines.append(f"  verified: {row.get('what') or row.get('id')}")
    for row in verified["unverified"]:
        lines.append(f"  unverified: {row.get('what') or row.get('id')}"
                     + (f" - {row.get('why')}" if row.get("why") else ""))
    corrections = view["corrections"]
    for row in corrections["items"]:
        lines.append(f"Correction {row['revise_id']} ({row['status']}): "
                     f"{row['note']}")
        if row.get("answer_with"):
            lines.append("  answer with: " + row["answer_with"])
        elif row["status"] not in revise.FINISHED:
            lines.append("  continue with: " + row["resume"])
    resume = view.get("resume_point") or {}
    if resume.get("command"):
        lines.append(f"Next: {resume['command']} ({resume.get('why')})")
    return "\n".join(lines)


def main(argv=None) -> int:
    """`ren assignment-state <project>`: what Ren understood, and did."""
    from ren import hosts

    hosts.ensure_harness_output_env()

    parser = argparse.ArgumentParser(
        prog="ren assignment-state",
        description="Read what Ren understood about a request, what it "
                    "proposed, what it applied, what it could not "
                    "determine, what it verified, and which corrections are "
                    "open.")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--id", dest="assignment_id", default=None,
                        help="the assignment to read (default: the open one)")
    parser.add_argument("--json", action="store_true",
                        help="emit the whole structure as JSON")
    args = parser.parse_args(argv)
    root = Path(args.project).expanduser()
    if not root.is_dir():
        from library.tools.project_registry import get_project
        try:
            root = Path(get_project(args.project).project_root)
        except FileNotFoundError as exc:
            parser.error(str(exc))
    try:
        view = task_state(str(root), args.assignment_id)
    except FileNotFoundError as exc:
        print(f"ren assignment-state: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0
    print(render(view))
    return 0


if __name__ == "__main__":
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))

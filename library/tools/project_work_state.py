"""Read-only, inexpensive summary of a Ren project's durable work state.

This is a projection of existing project records, not another store. It
never calls a model or Resolve and deliberately loads project config in
read-only mode so asking "where were we?" cannot migrate a project.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _latest_request(root: Path):
    requests = root / "pipeline_output" / "llm_requests"
    candidates = sorted(requests.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)) if requests.is_dir() else []
    for path in reversed(candidates):
        request = _json(path)
        if request and request.get("kind") in {"edit_spec", "briefing_interview"}:
            return {"id": path.stem, "kind": request["kind"],
                    "requested_at": request.get("timestamp"),
                    "prompt": request.get("prompt", "")}
    return None


def project_work_state(project_ref: str) -> dict:
    """Compose a structured work-state view using existing records only."""
    from library.schemas.project_config import (
        load_project_config,
        project_config_to_dict,
    )
    from library.tools import (
        captain_edits,
        edit_ledger,
        event_log,
        reel_proposal,
        review_gate,
    )
    from library.tools.project_registry import resolve_project_path, scan_projects

    yaml_path = resolve_project_path(project_ref)
    if yaml_path is None:
        match = next((c for c in scan_projects() if c.slug == project_ref), None)
        if match is None:
            raise FileNotFoundError(f"No Ren project found for {project_ref!r}")
        yaml_path = match.project_root / "project.yaml"
    config = load_project_config(yaml_path, migrate_format=False)
    root = Path(config.project_root)
    config_data = project_config_to_dict(config)

    ledger = edit_ledger.load_rows(str(root))
    events = event_log.read_events(str(root))
    last_event = events[-1] if events else None
    pending_approvals = [
        {"step": step, "status": "pending"}
        for step in review_gate.list_pending_gates(str(root))
    ]
    proposal = reel_proposal.proposal_path(str(root))
    if proposal.is_file():
        try:
            pending_approvals.extend(
                {"kind": "reel", "reel": moment.timeline_name,
                 "status": "proposed"}
                for moment in reel_proposal.read_proposal(proposal)
                if moment.approval.value == "proposed")
        except (OSError, ValueError):
            pending_approvals.append({"kind": "reel proposal", "status": "needs attention",
                                      "detail": "proposal record could not be read"})
    pending_specs = []
    requests = root / "pipeline_output" / "llm_requests"
    responses = root / "pipeline_output" / "llm_responses"
    if requests.is_dir():
        for path in sorted(requests.glob("edit_spec_*.json")):
            if not (responses / path.name).exists():
                pending_specs.append(path.stem)
    # Assignments are read through the lifecycle and the spine, the same
    # records and resume point `ren assignment` answers with.
    from library.tools import assignment_lifecycle, assignment_spine
    assignments = []
    for record in assignment_lifecycle.list_records(str(root)):
        checkpoint_id = record.get("checkpoint")
        checkpoint = record.get("operations", {}).get(checkpoint_id, {})
        assignments.append({
            "assignment_id": record["assignment_id"],
            "state": record["state"],
            "checkpoint": checkpoint.get("operation_id"),
            "recovery": "resume" if checkpoint.get("status") in {"running", "interrupted"}
            else "continue",
            "resume_point": assignment_spine.resume_point(str(root), record),
        })
    run = _json(root / "pipeline_run.json")
    brief = config_data.get("creative_brief") or config_data.get("pipeline", {}).get("creative_brief")
    settled = []
    if brief:
        settled.append({"decision": "creative brief attached", "value": str(brief)})
    declared_source = config_data.get("source", {})
    for key in ("language", "shape", "speakers"):
        if key in declared_source:
            settled.append({"decision": f"source {key}",
                            "value": declared_source[key]})
    settled.extend({"decision": "declared edit", "operation": row.get("op"),
                    "reel": row.get("reel"), "source_note_id": row.get("source_note_id")}
                   for row in ledger)
    open_work = []
    if run and run.get("status") in {"running", "held", "paused", "waiting"}:
        open_work.append({"kind": "pipeline", "status": run.get("status"),
                          "current_step": run.get("current_step")})
    open_work.extend({"kind": "edit_spec", "request_id": request_id,
                      "status": "awaiting host response"} for request_id in pending_specs)
    open_work.extend({"kind": "assignment", **assignment} for assignment in assignments
                     if assignment["state"] != "delivered" or assignment["recovery"] == "resume")
    if not pending_approvals and not open_work:
        open_work.append({"kind": "next step", "status": "No recorded work is waiting"})
    last_request = _latest_request(root)
    if last_request is None:
        declared_edits = captain_edits.load_edits(str(root))
        if declared_edits:
            last_edit = declared_edits[-1]
            reason = last_edit.get("reason", "")
            last_request = {"kind": "declared edit",
                            "prompt": reason or last_edit.get("kind", ""),
                            "requested_at": None}
    state = {
        "project": {"slug": config.slug, "name": config.name, "path": str(root)},
        "settled_decisions": settled,
        "last_changes": ([{"event": last_event.get("type"), "at": last_event.get("at"),
                            "task": last_event.get("task"),
                            **(last_event.get("fields") or {})}]
                         if last_event else []),
        "pending_approvals": pending_approvals,
        "open_work": open_work,
        "assignments": assignments,
        "last_requested_change": last_request,
    }
    state["summary"] = _summary(state)
    return state


def _summary(state: dict) -> str:
    project = state["project"].get("name") or state["project"]["slug"]
    changes = state["last_changes"]
    last = f" Last recorded change: {changes[-1]['event']}." if changes else " No recorded changes yet."
    approvals = state["pending_approvals"]
    pending = (f" {len(approvals)} approval{'s' if len(approvals) != 1 else ''} waiting." if approvals else " No approvals are waiting.")
    work = state["open_work"]
    open_text = " Work is waiting: " + ", ".join(str(item.get("kind")) for item in work if item.get("kind") != "next step") + "." if any(item.get("kind") != "next step" for item in work) else " No open work is recorded."
    live = [item for item in state["assignments"]
            if item["state"] != "delivered" or item["recovery"] == "resume"]
    assignment_text = "".join(
        f" Assignment {item['assignment_id']} is {item['state']}"
        + (f"; next: {item['resume_point']['command']}"
           if item["resume_point"].get("command") else "")
        + "." for item in live)
    requested = state["last_requested_change"]
    requested_text = f" Last request: {requested.get('prompt', '').strip()[:180]}" if requested and requested.get("prompt") else ""
    return f"{project}.{last}{pending}{open_text}{assignment_text}{requested_text}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ren work-state", description="Read where a project left off")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--json", action="store_true", help="print structured fields as JSON")
    args = parser.parse_args(argv)
    try:
        state = project_work_state(args.project)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(state, indent=2, ensure_ascii=False) if args.json else state["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

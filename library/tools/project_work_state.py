"""Read-only, inexpensive summary of a Ren project's durable work state.

This is a projection of existing project records, not another store. It
never calls a model or Resolve and deliberately loads project config in
read-only mode so asking "where were we?" cannot migrate a project.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import shlex
import sys
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
    from library.tools import llm_handshake
    for path in reversed(candidates):
        try:
            request = llm_handshake.read_request(path)
        except (OSError, ValueError):
            request = None
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
        assignment_view,
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
    config = load_project_config(yaml_path, migrate_format=False,
                                 strict=False)
    root = Path(config.project_root)
    config_data = project_config_to_dict(config)
    config_findings = list(getattr(config, "config_findings", None) or [])

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
    tasks = []
    for record in assignment_lifecycle.list_records(str(root)):
        checkpoint_id = record.get("checkpoint")
        checkpoint = record.get("operations", {}).get(checkpoint_id, {})
        assignments.append({
            "assignment_id": record["assignment_id"],
            "state": record["state"],
            "acceptance": _acceptance_section(record),
            "checkpoint": checkpoint.get("operation_id"),
            "recovery": "resume" if checkpoint.get("status") in {"running", "interrupted"}
            else "continue",
            "resume_point": assignment_spine.resume_point(str(root), record),
        })
        # What the task has proposed, applied, verified and delivered.
        # The row is a PROJECTION of the same records the six answers
        # read, so a reader that asks "where were we" gets the task's
        # own account and not a second, looser one.
        tasks.append(assignment_view.continuity_row(str(root), record))
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
                     if assignment["state"] not in ("delivered", "accepted")
                     or assignment["recovery"] == "resume")
    # A task whose final acceptance did not accept it is OPEN WORK, and
    # the reason it names is the gate's own: technically valid but
    # editorially rejected, technically failed, or a check nobody
    # measured.  A task that was delivered and accepted is not.
    open_work.extend({"kind": "acceptance",
                      "assignment_id": assignment["assignment_id"],
                      "status": assignment["acceptance"]["outcome"],
                      "why": assignment["acceptance"]["reason"]}
                     for assignment in assignments
                     if assignment["acceptance"]["outcome"]
                     not in ("accepted", "not_evaluated"))
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
        "tasks": tasks,
        "last_requested_change": last_request,
        "config_findings": config_findings,
    }
    state["summary"] = _summary(state)
    return state


def _acceptance_section(record: dict) -> dict:
    """The FINAL ACCEPTANCE a task carries, or that it has none.

    A task whose acceptance gate has not run carries no verdict, and
    this says `not_evaluated` rather than reading the absence as a pass:
    an absent verdict established nothing.
    """
    section = record.get("acceptance")
    final = section.get("final") if isinstance(section, dict) else None
    if not isinstance(final, dict):
        return {"outcome": "not_evaluated", "state": None, "reason": "",
                "counts": {}}
    return {"outcome": str(final.get("outcome") or "not_evaluated"),
            "state": final.get("state"),
            "reason": str(final.get("reason") or ""),
            "counts": dict(final.get("counts") or {})}


def _summary(state: dict) -> str:
    project = state["project"].get("name") or state["project"]["slug"]
    changes = state["last_changes"]
    last = f" Last recorded change: {changes[-1]['event']}." if changes else " No recorded changes yet."
    approvals = state["pending_approvals"]
    pending = (f" {len(approvals)} approval{'s' if len(approvals) != 1 else ''} waiting." if approvals else " No approvals are waiting.")
    work = state["open_work"]
    open_text = " Work is waiting: " + ", ".join(str(item.get("kind")) for item in work if item.get("kind") != "next step") + "." if any(item.get("kind") != "next step" for item in work) else " No open work is recorded."
    live = [item for item in state["assignments"]
            if item["state"] not in ("delivered", "accepted")
            or item["recovery"] == "resume"
            or item["acceptance"]["outcome"] not in ("accepted", "not_evaluated")]
    assignment_text = "".join(
        f" Assignment {item['assignment_id']} is {item['state']}"
        + (f"; acceptance: {item['acceptance']['outcome']}"
           if item["acceptance"]["outcome"] != "not_evaluated" else "")
        + (f"; next: {item['resume_point']['command']}"
           if item["resume_point"].get("command") else "")
        + "." for item in live)
    # What the task itself has done, in the four words a person asks
    # for: proposed, applied, verified, delivered. A task with no
    # delivery says zero rather than staying silent.
    tasks = state.get("tasks") or []
    task_text = "".join(
        f" Task {task['assignment_id']}: {task['proposed']} proposed, "
        f"{task['applied']} applied, {task['verified']} verified, "
        f"{task['delivered']} delivered."
        for task in tasks
        if task["state"] != "delivered" or task["applied"]
        or task["verified"] or task["delivered"])
    requested = state["last_requested_change"]
    requested_text = f" Last request: {requested.get('prompt', '').strip()[:180]}" if requested and requested.get("prompt") else ""
    return f"{project}.{last}{pending}{open_text}{assignment_text}{task_text}{requested_text}"


# ── `ren now`: state plus next actions, in one call (LE-09) ──────────
#
# This extends the work-state projection above with the sections an
# agent otherwise reads directly off the project records (the plan's
# measured baseline: status + progress + `ls llm_requests` + the
# proposals JSON, 4 or more calls). `work-state` keeps its output;
# `now` is a second mode of this same module, never a parallel one.

def _dag_node_ids() -> list:
    """The edit_video DAG's node ids, in DAG order; [] when unreadable."""
    from pathlib import Path as _Path
    try:
        import json as _json
        dag = _Path(__file__).resolve().parents[1] / "processes" / "edit_video" / "dag.json"
        nodes = _json.loads(dag.read_text(encoding="utf-8")).get("nodes") or []
        return [str(node["id"]) for node in nodes if isinstance(node, dict) and node.get("id")]
    except (OSError, ValueError, KeyError):
        return []


def _pipeline_section(root: Path) -> dict:
    """Completed, failed and pending steps, off the split ledger."""
    from library.tools import step_ledger
    state_doc = _json(root / "pipeline_data.json") or {}
    completed = sorted(step_ledger.all_completed(state_doc))
    failed_steps = sorted(set(state_doc.get("failed_steps") or []))
    errors = state_doc.get("step_errors") or {}
    failed = [{"step": step, "error": str(errors.get(step) or "")}
              for step in failed_steps]
    node_ids = _dag_node_ids()
    done = set(completed) | set(failed_steps)
    pending = [node for node in node_ids if node not in done]
    return {"completed": completed, "failed": failed, "pending": pending,
            "total": len(node_ids)}


def _run_section(root: Path) -> dict:
    """The runner's own account: status, step, pid, hold, lock wait."""
    from library.tools import run_control
    status_doc = run_control.read_run_status(str(root)) or {}
    pid = run_control.running_pid(str(root))
    hold = run_control.hold_requested(str(root))
    return {"status": status_doc.get("status"),
            "current_step": status_doc.get("current_step"),
            "last_completed_step": status_doc.get("last_completed_step"),
            "pid": pid, "pid_alive": pid is not None,
            "hold": hold,
            "lock_wait": status_doc.get("heavy_work_wait"),
            "updated_at": status_doc.get("updated_at")}


def _asks_section(root: Path) -> dict:
    """Every `llm_requests/*.json` with no response, reel asks included."""
    from library.tools import awaiting_model_answers, pending_asks
    pending = []
    for path in pending_asks.pending_request_paths(root):
        doc = _json(path) or {}
        pending.append({
            "id": path.stem,
            "kind": str(doc.get("kind") or "llm_step"),
            "step_id": str(doc.get("step_id") or path.stem),
            "path": str(path),
            "requested_at": doc.get("timestamp"),
        })
    try:
        owed = awaiting_model_answers.collect(
            str(root), state=_json(root / "pipeline_data.json"))
    except Exception:  # noqa: BLE001 - an asks read never fails `now`
        owed = {"reels": [], "outstanding_answers": 0}
    owed_reels = (owed or {}).get("reels") or []

    def _digits(value) -> set:
        import re as _re
        return set(_re.findall(r"\d+", str(value or "")))

    matched: set = set()
    for ask in pending:
        layers = set()
        for entry in owed_reels:
            if not isinstance(entry, dict):
                continue
            reel_digits = _digits(entry.get("reel"))
            if reel_digits and reel_digits & _digits(ask["id"]):
                layers.update(str(layer) for layer in (entry.get("layers") or ()))
                matched.add(str(entry.get("reel")))
        ask["owed_layers"] = sorted(layers)
    for entry in owed_reels:
        if not isinstance(entry, dict) or str(entry.get("reel")) in matched:
            continue
        for layer in (entry.get("layers") or ()):
            pending.append({
                "id": f"reel_{layer}_{entry.get('reel')}",
                "kind": "reel_answer",
                "step_id": f"reel_{layer}",
                "path": "",
                "requested_at": None,
                "owed_layers": [str(layer)],
            })
    return {"pending": pending,
            "outstanding_answers": (owed or {}).get(
                "outstanding_answers", sum(len(a.get("owed_layers") or []) for a in pending))}


def _reels_section(project_ref: str) -> dict:
    """Reel counts by state, off LE-07's one read (never re-derived)."""
    from collections import Counter

    from library.tools import reel_state
    try:
        report = reel_state.reels_state(project_ref)
    except Exception as exc:  # noqa: BLE001 - a reels read never fails `now`
        return {"total": 0, "by_approval": {}, "by_outcome": {},
                "owed_answers": 0, "signed_off": 0, "delivered": 0,
                "proposed": [], "reels": [], "error": str(exc)}
    rows = report.get("reels") or []
    by_approval = Counter(str(row.get("approval") or "none") for row in rows)
    by_outcome = Counter(str(row.get("outcome") or "none") for row in rows)
    proposed = sorted(row.get("reel") for row in rows
                      if row.get("approval") == "proposed" and isinstance(row.get("reel"), int))
    return {"total": len(rows),
            "by_approval": dict(sorted(by_approval.items())),
            "by_outcome": dict(sorted(by_outcome.items())),
            "owed_answers": sum(len(row.get("owed_answers") or []) for row in rows),
            "signed_off": sum(1 for row in rows if row.get("signoff")),
            "delivered": sum(1 for row in rows
                             if isinstance(row.get("delivery"), dict)
                             and row["delivery"].get("delivered")),
            "proposed": proposed,
            "reels": [{"reel": row.get("reel"),
                       "timeline_name": row.get("timeline_name"),
                       "approval": row.get("approval"),
                       "outcome": row.get("outcome")} for row in rows]}


def _notes_section(root: Path) -> dict:
    """Pulled timeline notes no routing record has carried, disk-only."""
    from library.tools import marker_routing
    try:
        routed = marker_routing.route_project(str(root))
    except Exception as exc:  # noqa: BLE001 - a notes read never fails `now`
        return {"unrouted": [], "error": f"{type(exc).__name__}: {exc}"}
    unrouted = [
        {"note_id": note.note_id, "outcome": note.outcome,
         "reason": note.reason, "timeline": note.timeline,
         "steps": list(note.steps or [])}
        for note in routed
        if note.outcome != marker_routing.OUTCOME_ROUTED
    ]
    return {"unrouted": unrouted}


# Next-action ranks, lowest first: a blocking ask outranks a refusal's
# fix, which outranks an approval, then a resumable run, then the reels
# the person has APPROVED and not built, then the TASK's own next act,
# then whatever pipeline stage is next.
#
# The approved build outranks the task's act because the ruling is the
# person's own yes and the build is what it means: a reel nobody has
# built cannot be signed off, and the task's act for a reel task IS the
# build. A task's act still outranks the pipeline's next stage, so a
# fresh session reads what the work waits on before it reads what the
# pipeline would run next.
(_RANK_ASK, _RANK_FIX, _RANK_APPROVAL, _RANK_RESUME, _RANK_BUILD,
 _RANK_TASK, _RANK_NEXT) = range(7)


def _named_by_ref(command: str, root: Path, ref: str) -> str:
    """Name the project in a command the way the reader named it.

    A task's resume point is built from the project's absolute path;
    every other action names the project by the ref the caller used.
    Two spellings of one project turn one act into two - the reader is
    shown the same act twice and cannot tell which one to run.
    """
    for spelling in (shlex.quote(str(root)), str(root),
                     shlex.quote(str(Path(root).resolve())),
                     str(Path(root).resolve())):
        if spelling and spelling in command:
            return command.replace(spelling, shlex.quote(ref))
    return command


def _next_actions(*, ref: str, root: Path, base: dict, pipeline: dict,
                  run: dict, asks: dict, reels: dict, notes: dict) -> list:
    """Ranked `{id, why, command, effects}`: what to run, in what order."""
    actions = []
    for ask in asks["pending"]:
        where = ask["path"] or ask["id"]
        actions.append({
            "id": f"answer-{ask['id']}",
            "rank": "blocking ask",
            "why": (f"model answer owed for {ask['kind']} {ask['step_id']} "
                    f"({where}); read every owed ask (each shared prompt "
                    f"once) and write the responses - a waiting run picks "
                    f"them up, a stopped one resumes with `ren edit {ref} "
                    f"--resume`"),
            "command": f"ren asks {ref}",
            "effects": ["read"],
            "_rank": _RANK_ASK,
        })
    for finding in base.get("config_findings") or []:
        actions.append({
            "id": "fix-config",
            "rank": "refusal to fix",
            "why": (f"config finding: {finding}; write verbs refuse until "
                    f"project.yaml is fixed, then verify"),
            "command": f"ren check {ref}",
            "effects": ["read"],
            "_rank": _RANK_FIX,
        })
    for failed in pipeline["failed"]:
        why = f"step {failed['step']} failed"
        if failed["error"]:
            why += f": {failed['error'][:200]}"
        actions.append({
            "id": f"rerun-{failed['step']}",
            "rank": "refusal to fix",
            "why": why,
            "command": f"ren edit {ref} --rerun {failed['step']}",
            "effects": ["project_write", "heavy_lock"],
            "_rank": _RANK_FIX,
        })
    for approval in base.get("pending_approvals") or []:
        if approval.get("kind") == "reel":
            from library.tools.reel_state import reel_number_of
            number = reel_number_of(approval.get("reel"))
            command = (f"ren approve {ref} {number}"
                       if number is not None else f"ren approve {ref} --all")
            actions.append({
                "id": f"approve-reel-{number if number is not None else 'all'}",
                "rank": "approval waiting",
                "why": (f"reel {approval.get('reel')} is proposed; "
                        f"approve it for building"),
                "command": command,
                "effects": ["project_write"],
                "_rank": _RANK_APPROVAL,
            })
        elif approval.get("step"):
            step = approval["step"]
            actions.append({
                "id": f"answer-gate-{step}",
                "rank": "approval waiting",
                "why": (f"review gate pending for {step}; inspect the "
                        f"snapshot, then answer"),
                "command": (f"python3 -m library.tools.review_gate answer "
                            f"--project {root} --step {step} --approve"),
                "effects": ["project_write"],
                "_rank": _RANK_APPROVAL,
            })
    # The TASK's own next act, from the same resume point `ren
    # assignment` answers with. A fresh session that reads only `ren
    # now` has to learn that the work it is resuming is a task and what
    # that task has done so far.
    for task in base.get("tasks") or []:
        resume_point = task.get("resume_point") or {}
        command = resume_point.get("command")
        if not command:
            continue
        command = _named_by_ref(command, root, ref)
        actions.append({
            "id": f"resume-task-{task['assignment_id']}",
            "rank": "task waiting",
            "why": (f"task {task['assignment_id']} is {task['state']}: "
                    f"{task['proposed']} proposed, {task['applied']} applied, "
                    f"{task['verified']} verified, {task['delivered']} "
                    f"delivered; {resume_point.get('why') or 'it waits'}"),
            "command": command,
            "effects": ["read", "project_write"],
            "_rank": _RANK_TASK,
        })
    status = run.get("status")
    hold = run.get("hold")
    if hold is not None or status in {"held", "paused", "waiting"}:
        reason = ""
        if isinstance(hold, dict) and hold.get("reason"):
            reason = f" ({hold['reason']})"
        actions.append({
            "id": "resume-run",
            "rank": "resumable run",
            "why": f"run is {status or 'held'}{reason}; release it",
            "command": f"ren resume {ref}",
            "effects": ["project_write"],
            "_rank": _RANK_RESUME,
        })
    elif status == "running" and not run.get("pid_alive"):
        actions.append({
            "id": "resume-run",
            "rank": "resumable run",
            "why": ("run record says running but no live pid holds it; "
                    "continue it"),
            "command": f"ren edit {ref} --resume",
            "effects": ["project_write", "heavy_lock"],
            "_rank": _RANK_RESUME,
        })
    unbuilt = [row["reel"] for row in reels.get("reels") or []
               if row.get("approval") == "approved" and not row.get("outcome")]
    if unbuilt:
        # Approved reels are the person's own yes: building them is what
        # comes next, ahead of the edit pipeline's remaining stages.
        from ren.commands import VERBS
        build = next(verb for verb in VERBS if verb.name == "build")
        actions.append({
            "id": "build-reels",
            "rank": "approved, not built",
            "why": (f"{len(unbuilt)} approved reel(s) not built yet "
                    f"({', '.join(str(reel) for reel in unbuilt)}); build "
                    f"them with Resolve open on this project"),
            "command": f"ren build {ref}",
            "effects": list(build.effects),
            "_rank": _RANK_BUILD,
        })
    if pipeline["pending"] and not asks["pending"] and not pipeline["failed"]:
        # `--detach` is LE-10's flag, not yet a runner option: the action
        # names the loop the skill will run (detach, then `ren wait`).
        actions.append({
            "id": "run-next-stage",
            "rank": "next pipeline stage",
            "why": (f"{len(pipeline['pending'])} steps pending, next: "
                    f"{pipeline['pending'][0]}"),
            "command": f"ren edit {ref} --full-auto agent --detach",
            "effects": ["project_write", "heavy_lock"],
            "_rank": _RANK_NEXT,
        })
    actions.sort(key=lambda action: (action.pop("_rank"), action["id"]))
    # One command, one action: the task's resume point and the run's
    # can name the same verb, and a reader shown the same command twice
    # cannot tell which one is next.
    unique = []
    seen_commands = set()
    for action in actions:
        command = action.get("command")
        if command in seen_commands:
            continue
        seen_commands.add(command)
        unique.append(action)
    return unique


def project_now_state(project_ref: str) -> dict:
    """State plus ranked next actions, in one call (LE-09)."""
    base = project_work_state(project_ref)
    root = Path(base["project"]["path"])
    pipeline = _pipeline_section(root)
    run = _run_section(root)
    asks = _asks_section(root)
    reels = _reels_section(project_ref)
    notes = _notes_section(root)
    state = dict(base)
    state.update({
        "pipeline": pipeline,
        "run": run,
        "asks": asks,
        "reels": reels,
        "notes": notes,
        "next_actions": _next_actions(
            ref=project_ref, root=root, base=base, pipeline=pipeline,
            run=run, asks=asks, reels=reels, notes=notes),
    })
    state["summary"] = _now_summary(state)
    return state


LAUNCH_PACK_SCHEMA = "ren-launch-pack/1"
LAUNCH_PACK_MAX_BYTES = 256 * 1024
LAUNCH_PACK_STALE_INSTRUCTION = (
    "Before acting on this snapshot, run `ren launch-pack <project> --json` "
    "again and compare its fingerprint; use the newer snapshot if it changed.")


def project_launch_pack(project_ref: str, *, captured_at: str | None = None) -> dict:
    """Build a bounded, read-only snapshot suitable for a lane brief.

    The fingerprint covers the exact `ren now` state and its project identity,
    but not the capture time or stale-snapshot instruction.
    """
    from library.tools.ren_refusal import RenRefusal

    try:
        state = project_now_state(project_ref)
    except (FileNotFoundError, ValueError) as exc:
        raise RenRefusal(
            what=f"Project {project_ref!r} could not be read",
            why=str(exc),
            fix=f"Check the project slug or path, then retry: ren projects",
            code="REN_LAUNCH_PACK_PROJECT_INVALID",
            recovery_class="needs_question",
            ask_reason="insufficient_evidence", blocker="missing_media") from exc

    identity = dict(state["project"])
    fingerprint_input = json.dumps(
        {"schema": LAUNCH_PACK_SCHEMA, "project": identity, "state": state},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    fingerprint = hashlib.sha256(fingerprint_input).hexdigest()
    pack = {
        "schema": LAUNCH_PACK_SCHEMA,
        "project": identity,
        "snapshot": {
            "captured_at": captured_at or datetime.now(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "fingerprint": fingerprint,
        },
        "state": state,
        "stale_snapshot_instruction": LAUNCH_PACK_STALE_INSTRUCTION,
    }
    encoded = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > LAUNCH_PACK_MAX_BYTES:
        raise RenRefusal(
            what="The project's state snapshot is too large for a launch pack",
            why=f"The bounded payload limit is {LAUNCH_PACK_MAX_BYTES} bytes",
            fix=f"Reduce the project's recorded state, then retry `ren launch-pack {project_ref} --json`",
            code="REN_LAUNCH_PACK_TOO_LARGE", blocker="unsupported_operation")
    return pack


def _now_summary(state: dict) -> str:
    project = state["project"].get("name") or state["project"]["slug"]
    pipeline = state["pipeline"]
    run = state["run"]
    asks = state["asks"]
    reels = state["reels"]
    notes = state["notes"]
    findings = state.get("config_findings") or []
    parts = [f"{project}: {len(pipeline['completed'])}/{pipeline['total']} steps done"]
    if pipeline["failed"]:
        parts.append(f"{len(pipeline['failed'])} failed")
    if run.get("status"):
        at = f" at {run['current_step']}" if run.get("current_step") else ""
        parts.append(f"run {run['status']}{at}")
    if run.get("lock_wait"):
        parts.append("waiting on the heavy-work lock")
    if run.get("hold") is not None:
        parts.append("held")
    if asks["pending"]:
        parts.append(f"{len(asks['pending'])} ask(s) owed")
    proposed = (reels.get("by_approval") or {}).get("proposed", 0)
    if proposed:
        parts.append(f"{proposed} reel(s) proposed")
    if notes["unrouted"]:
        parts.append(f"{len(notes['unrouted'])} note(s) unrouted")
    if findings:
        parts.append(f"{len(findings)} config finding(s)")
    # The task, in the four words the punchlist uses: proposed,
    # applied, verified, delivered. A fresh session reads this line and
    # knows what the work already did.
    for task in state.get("tasks") or []:
        if task["state"] == "delivered" and not task["delivered"]:
            continue
        parts.append(
            f"task {task['assignment_id']} {task['state']}: "
            f"{task['proposed']} proposed, {task['applied']} applied, "
            f"{task['verified']} verified, {task['delivered']} delivered")
    actions = state["next_actions"]
    if actions:
        parts.append(f"Next: {actions[0]['command']}")
    else:
        parts.append("No open work is recorded.")
    return ". ".join(parts) + "."


def _format_now_text(state: dict) -> str:
    lines = [state["summary"]]
    for number, action in enumerate(state["next_actions"], start=1):
        lines.append(f"{number}. [{action['id']}] {action['why']} -> {action['command']}")
    return "\n".join(lines)


def _cmd_now(args) -> int:
    try:
        state = project_now_state(args.project)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ren now: {exc}", file=sys.stderr)
        return 2
    if state.get("config_findings"):
        print(f"⚠ config: {'; '.join(state['config_findings'])}",
              file=sys.stderr)
    print(json.dumps(state, indent=2, ensure_ascii=False) if args.json else _format_now_text(state))
    return 0


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] == "launch-pack":
        parser = argparse.ArgumentParser(
            prog="ren launch-pack",
            description="Emit a bounded project snapshot for a lane brief")
        parser.add_argument("project", help="project slug or path")
        parser.add_argument("--json", action="store_true",
                            help="print the launch pack as JSON")
        args = parser.parse_args(raw[1:])
        pack = project_launch_pack(args.project)
        print(json.dumps(pack, indent=2, ensure_ascii=False) if args.json
              else f"{pack['project']['name']}: {pack['snapshot']['fingerprint']}")
        return 0
    if raw and raw[0] == "now":
        parser = argparse.ArgumentParser(
            prog="ren now",
            description="State plus next actions, in one call (read only)")
        parser.add_argument("project", help="project slug or path")
        parser.add_argument("--json", action="store_true",
                            help="print the whole structure as JSON")
        args = parser.parse_args(raw[1:])
        return _cmd_now(args)
    parser = argparse.ArgumentParser(prog="ren work-state", description="Read where a project left off")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--json", action="store_true", help="print structured fields as JSON")
    args = parser.parse_args(raw)
    try:
        state = project_work_state(args.project)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    if state.get("config_findings"):
        print(f"⚠ config: {'; '.join(state['config_findings'])}",
              file=sys.stderr)
    print(json.dumps(state, indent=2, ensure_ascii=False) if args.json else state["summary"])
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
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

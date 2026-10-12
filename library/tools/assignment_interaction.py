"""Host-neutral, versioned interaction envelope for assignment operations.

Structured hosts and the compatibility file handshake enter through this
module. Operation names remain the IDs from ``operations``; assignment IDs
are supplied by the caller and survive transport changes.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from library.tools import assignment_lifecycle as lifecycle
from library.tools import operations
from library.tools.ren_refusal import RenRefusal

ENVELOPE_VERSION = 1
STAGES = ("understand", "plan", "execute", "observe", "review", "revise", "deliver")
STATUSES = ("accepted", "running", "completed", "refused", "interrupted")


@dataclass(frozen=True)
class AssignmentRequest:
    assignment_id: str
    operation_id: str
    project: str
    inputs: Mapping[str, Any]
    stage: str = "execute"
    version: int = ENVELOPE_VERSION

    def __post_init__(self):
        if self.version != ENVELOPE_VERSION:
            raise ValueError(f"unsupported assignment envelope version {self.version!r}")
        if not self.assignment_id or not self.assignment_id.strip():
            raise ValueError("assignment_id must be a non-empty stable caller ID")
        if not self.project or not self.project.strip():
            raise ValueError("project must be explicit")
        if self.stage not in STAGES:
            raise ValueError(f"unknown interaction stage {self.stage!r}; expected {STAGES}")
        if not isinstance(self.inputs, Mapping):
            raise TypeError("inputs must be an object")
        # Resolve here so unknown IDs fail at the shared boundary, consistently.
        operations.get(self.operation_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AssignmentRequest:
        if not isinstance(value, Mapping):
            raise TypeError("assignment request must be an object")
        return cls(assignment_id=value["assignment_id"],
                   operation_id=value["operation_id"], project=value["project"],
                   inputs=value["inputs"], stage=value.get("stage", "execute"),
                   version=value.get("version", ENVELOPE_VERSION))

    def to_dict(self) -> dict:
        return {"version": self.version, "assignment_id": self.assignment_id,
                "operation_id": self.operation_id, "project": self.project,
                "inputs": dict(self.inputs), "stage": self.stage}


def _json_value(value: Any) -> Any:
    """Convert operation payloads into stable JSON data for either host."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "__dataclass_fields__"):
            from dataclasses import asdict
            return asdict(value)
        if isinstance(value, (list, tuple)):
            return [_json_value(item) for item in value]
        if isinstance(value, dict):
            return {str(k): _json_value(v) for k, v in value.items()}
        return str(value)


def execute(request: AssignmentRequest | Mapping[str, Any]) -> dict:
    """Execute one registry operation and return the common typed envelope."""
    req = request if isinstance(request, AssignmentRequest) else AssignmentRequest.from_dict(request)
    with lifecycle.assignment_lock(req.project, req.assignment_id):
        return _execute_locked(req)


def _execute_locked(req: AssignmentRequest) -> dict:
    """Execute while holding this assignment's cross-process lock."""
    effect = lifecycle.operation_effect(req.project, req.assignment_id,
                                       req.operation_id, dict(req.inputs))
    if not effect["allowed"]:
        refusal = RenRefusal(
            effect["reason"],
            f"the operation effect {effect['effect']!r} requires a human decision",
            "resolve the decision for this assignment, then resubmit",
            code="REN_ASSIGNMENT_DECISION_REQUIRED", recovery_class="needs_question").to_dict()
        return {
            "version": ENVELOPE_VERSION, "assignment_id": req.assignment_id,
            "operation_id": req.operation_id, "stage": req.stage,
            "status": "refused",
            "progress": {"status": "refused", "fraction": 1.0,
                         "message": "Operation paused for a human decision."},
            "inputs": dict(req.inputs), "effect": effect, "result": None,
            "refusal": refusal,
            "recovery": {"resumable": True, "resume_with": req.to_dict()},
        }
    checkpoint = lifecycle.begin_operation(req.project, req.assignment_id,
                                           req.operation_id, req.to_dict())
    if checkpoint.get("status") == "completed":
        return checkpoint["envelope"]
    result = operations.get(req.operation_id).execute(req.project, **dict(req.inputs))
    refused = result.refused
    refusal = None
    if refused:
        detail = result.refusal_reason() or result.error
        refusal_obj = RenRefusal(
            detail,
            "the operation's declared requirements or execution contract did not permit completion",
            result.error or "resolve the stated issue and resubmit this assignment operation",
            code="REN_OPERATION_REFUSED", recovery_class="blocked")
        refusal = refusal_obj.to_dict()
        if result.unsatisfied:
            refusal["unsatisfied"] = [
                {"name": item.name, "produced_by": item.produced_by}
                for item in result.unsatisfied]
    payload = None if refused else _json_value(result.payload)
    if not refused and req.operation_id == "footage.catalog":
        from library.tools import assignment_artifacts
        artifact_path = assignment_artifacts.publish_first_value(
            req.project, payload, assignment_id=req.assignment_id)
        payload["first_value_artifact"] = {
            "path": str(artifact_path),
            "kind": "footage_map",
            "available_after": "footage.catalog",
            "deeper_analysis_complete": False,
        }
    envelope = {
        "version": ENVELOPE_VERSION,
        "assignment_id": req.assignment_id,
        "operation_id": result.operation,
        "stage": req.stage,
        "status": "refused" if refused else "completed",
        "progress": {"status": "refused" if refused else "completed", "fraction": 1.0,
                     "message": "Operation refused; nothing ran." if refused else "Operation completed."},
        "inputs": dict(req.inputs),
        "effect": effect,
        "result": payload,
        "refusal": refusal,
        "recovery": {"resumable": True, "resume_with": req.to_dict()},
    }
    lifecycle.finish_operation(req.project, req.assignment_id,
                               req.operation_id, envelope)
    return envelope


def structured_transport(request: Mapping[str, Any]) -> dict:
    """Adapter entry point for hosts that call structured tools."""
    return execute(request)


def _file_stem(request: AssignmentRequest) -> str:
    raw = f"{request.assignment_id}\0{request.operation_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.assignment_id)[:40] or "assignment"
    return f"assignment-{label}-{digest}"


def file_transport(request: Mapping[str, Any]) -> dict:
    """Compatibility file adapter using llm_handshake's atomic file contract.

    A completed response is a durable checkpoint: resubmitting the same
    assignment/operation returns it without executing the operation again.
    """
    req = request if isinstance(request, AssignmentRequest) else AssignmentRequest.from_dict(request)
    from library.tools import llm_handshake
    root = Path(req.project).expanduser().resolve()
    request_path = Path(llm_handshake.request_path(str(root), _file_stem(req)))
    response_path = Path(llm_handshake.response_path(str(root), _file_stem(req)))
    if response_path.is_file():
        parsed = llm_handshake.validate_response(_file_stem(req), response_path.read_text(encoding="utf-8"), str(root))
        if parsed.get("request") == req.to_dict() and isinstance(parsed.get("envelope"), dict):
            envelope = parsed["envelope"]
            refusal = envelope.get("refusal") or {}
            if refusal.get("code") != "REN_ASSIGNMENT_DECISION_REQUIRED":
                return envelope
    request_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    llm_handshake.publish_operation_request(request_path, response_path, req.to_dict())
    envelope = execute(req)
    llm_handshake.publish_operation_response(request_path, response_path,
                                             {"request": req.to_dict(), "envelope": envelope})
    return envelope


def capture_review(project: str, assignment_id: str, revision_id: str,
                   **review) -> dict:
    """Assignment contract entry point for chat-first proposal review."""
    from library.tools import assignment_revision
    record = assignment_revision.capture_review(
        project, assignment_id, revision_id, **review)
    return {"version": ENVELOPE_VERSION, "assignment_id": assignment_id,
            "operation_id": "assignment.review", "stage": "review",
            "status": "accepted", "progress": {
                "status": "completed", "fraction": 1.0,
                "message": "Review captured; awaiting creator choice."},
            "result": record,
            "recovery": {"resumable": True, "resume_with": {
                "assignment_id": assignment_id, "revision_id": revision_id,
                "stage": "review"}}}


def decide_revision(project: str, assignment_id: str, revision_id: str,
                    **decision) -> dict:
    """Apply one explicitly approved variant through its EditPatch writer."""
    from library.tools import assignment_revision
    record = assignment_revision.decide(
        project, assignment_id, revision_id, **decision)
    status = "refused" if record["status"] in {"stale", "rejected"} else "completed"
    return {"version": ENVELOPE_VERSION, "assignment_id": assignment_id,
            "operation_id": "assignment.revise", "stage": "revise",
            "status": status,
            "progress": {"status": status, "fraction": 1.0,
                         "message": f"Revision {record['status']}."},
            "result": record,
            "recovery": {"resumable": True, "resume_with": {
                "assignment_id": assignment_id, "revision_id": revision_id,
            "stage": "revise"}}}


def contextual_onboarding(project: str, assignment_id: str, *, context: str) -> dict:
    """Return onboarding at the relevant point in the shared assignment loop."""
    from library.tools import assignment_artifacts
    result = assignment_artifacts.load_onboarding(
        project, assignment_id, context=context)
    stage_by_context = {"catalog_ready": "understand", "review_ready": "review",
                        "revision_ready": "revise", "delivery_ready": "deliver"}
    stage = result["lessons"][0]["stage"] if result["lessons"] else stage_by_context[context]
    return {"version": ENVELOPE_VERSION, "assignment_id": assignment_id,
            "operation_id": "assignment.onboarding", "stage": stage,
            "status": "completed", "progress": {
                "status": "completed", "fraction": 1.0,
                "message": "Contextual onboarding loaded."},
            "result": result,
            "recovery": {"resumable": True, "resume_with": {
                "assignment_id": assignment_id, "stage": stage}}}


def dismiss_contextual_onboarding(project: str, assignment_id: str) -> dict:
    """Dismiss onboarding for this assignment without affecting other projects."""
    from library.tools import assignment_artifacts
    result = assignment_artifacts.dismiss_onboarding(project, assignment_id)
    return {"version": ENVELOPE_VERSION, "assignment_id": assignment_id,
            "operation_id": "assignment.onboarding.dismiss", "stage": "understand",
            "status": "completed", "progress": {
                "status": "completed", "fraction": 1.0,
                "message": "Onboarding dismissed for this assignment."},
            "result": result,
            "recovery": {"resumable": True, "resume_with": {
                "assignment_id": assignment_id, "stage": "understand"}}}


def delivery_receipt(project: str, *, assignment_id: str,
                     approved_version: Mapping, files: list, qc: Mapping,
                     resume_point: Mapping, selected_platforms=()) -> dict:
    """Publish a receipt only from the files/specs/QC supplied by delivery."""
    from library.tools import assignment_artifacts
    path, record = assignment_artifacts.publish_delivery_receipt(
        project, assignment_id=assignment_id, approved_version=approved_version,
        files=files, qc=qc, resume_point=resume_point,
        selected_platforms=selected_platforms)
    return {"version": ENVELOPE_VERSION, "assignment_id": assignment_id,
            "operation_id": "assignment.deliver", "stage": "deliver",
            "status": "completed",
            "progress": {"status": record["status"], "fraction": 1.0,
                         "message": "Delivery receipt written."},
            "result": {"path": str(path), "receipt": record},
            "recovery": {"resumable": True, "resume_with": {
                "assignment_id": assignment_id, "stage": "deliver",
                "action": resume_point["action"]}}}


STAGE_BY_STATE = {"brief": "understand", "draft": "plan", "review": "review",
                  "approved": "deliver", "delivered": "deliver"}


def _summary_message(record: Mapping, resume: Mapping) -> str:
    return f"Assignment {record['state']}: {resume['why']}."


def state(project: str, assignment_id: str | None = None) -> dict:
    """How a host reads an assignment: the same envelope every act returns.

    With no ``assignment_id`` the project's newest open assignment answers
    (``assignment_spine.current``), else the newest one of any state. The
    result carries the record, its history and the resume point - the
    one verb the assignment is waiting on.
    """
    from library.tools import assignment_spine
    root = str(Path(project).expanduser().resolve())
    if assignment_id:
        record = lifecycle.load(root, assignment_id)
        if record is None:
            raise FileNotFoundError(f"assignment {assignment_id!r} has no checkpoint")
    else:
        records = lifecycle.list_records(root)
        record = assignment_spine.current(root) or (
            max(records, key=lambda r: (r.get("updated_at") or "",
                                        r.get("created_at") or ""))
            if records else None)
    if record is None:
        return {"version": ENVELOPE_VERSION, "assignment_id": None,
                "operation_id": "assignment.state", "stage": "understand",
                "status": "completed",
                "progress": {"status": "completed", "fraction": 1.0,
                             "message": "No assignment is recorded yet; "
                                        "a proposal or an edit run opens one."},
                "result": None,
                "recovery": {"resumable": False, "resume_with": None}}
    resume = assignment_spine.resume_point(root, record)
    return {"version": ENVELOPE_VERSION, "assignment_id": record["assignment_id"],
            "operation_id": "assignment.state",
            "stage": STAGE_BY_STATE[record["state"]],
            "status": "completed",
            "progress": {"status": "completed", "fraction": 1.0,
                         "message": _summary_message(record, resume)},
            "result": {**record, "resume_point": resume},
            "recovery": {"resumable": resume["command"] is not None,
                         "resume_with": resume}}


def authorize(project: str, assignment_id: str) -> dict:
    """Record the creator's yes to replacing this assignment's approved work."""
    root = str(Path(project).expanduser().resolve())
    if lifecycle.load(root, assignment_id) is None:
        raise FileNotFoundError(f"assignment {assignment_id!r} has no checkpoint")
    lifecycle.authorize_replacement(root, assignment_id)
    return state(root, assignment_id)


def main(argv=None) -> int:
    """`ren assignment <project>`: read (or authorize) an assignment."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="ren assignment",
        description="Read a project's assignment as the host contract's "
                    "envelope: state, history and the verb it waits on.")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--id", dest="assignment_id", default=None,
                        help="the assignment to read (default: the open one)")
    parser.add_argument("--authorize-replacement", action="store_true",
                        help="record the creator's yes to replacing this "
                             "assignment's approved work (decision D2)")
    args = parser.parse_args(argv)
    root = Path(args.project).expanduser()
    if not root.is_dir():
        from library.tools.project_registry import get_project
        try:
            root = Path(get_project(args.project).project_root)
        except FileNotFoundError as exc:
            parser.error(str(exc))
    try:
        if args.authorize_replacement:
            if not args.assignment_id:
                parser.error("--authorize-replacement needs --id: the yes "
                             "is given to one assignment")
            envelope = authorize(str(root), args.assignment_id)
        else:
            envelope = state(str(root), args.assignment_id)
    except FileNotFoundError as exc:
        print(f"ren assignment: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

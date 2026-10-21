"""Host-neutral, versioned interaction envelope for assignment operations.

Structured hosts and the compatibility file handshake enter through this
module. Operation names remain the IDs from ``operations``; assignment IDs
are supplied by the caller and survive transport changes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
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
    node_id: str = ""
    """The plan node this request runs, when the task planned one.

    The node is how the operation finds the evidence and the decisions it
    rests on. A request that names none is stamped from the task's only
    planned node for that operation, and from nothing at all when the
    operation appears on more than one node.
    """
    provenance: Mapping[str, Any] = field(default_factory=dict)
    """The evidence and decisions this request carries into execution.

    Filled from the task's own ledger when the caller supplies none, so
    the chain from source to choice to edit survives the run that made
    it without the model having to restate it.
    """

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
                   version=value.get("version", ENVELOPE_VERSION),
                   node_id=str(value.get("node_id") or ""),
                   provenance=dict(value.get("provenance") or {}))

    def to_dict(self) -> dict:
        return {"version": self.version, "assignment_id": self.assignment_id,
                "operation_id": self.operation_id, "project": self.project,
                "inputs": dict(self.inputs), "stage": self.stage,
                "node_id": self.node_id, "provenance": dict(self.provenance)}


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


def _stamp(request: AssignmentRequest) -> AssignmentRequest:
    """Carry the task's own provenance into the request, if it has none.

    The point of the stamp is that the MODEL never has to restate where
    an edit came from. A request that already carries a provenance keeps
    it: the caller who named it knew something the ledger does not.
    """
    if request.provenance:
        return request
    from library.tools import assignment_provenance

    try:
        provenance = assignment_provenance.stamp(
            request.project, request.assignment_id,
            node_id=request.node_id, operation_id=request.operation_id)
    except (FileNotFoundError, ValueError):
        return request
    if not provenance.get("evidence") and not provenance.get("decisions"):
        return request
    return replace(request, provenance=provenance)


def execute(request: AssignmentRequest | Mapping[str, Any]) -> dict:
    """Execute one registry operation and return the common typed envelope."""
    req = request if isinstance(request, AssignmentRequest) else AssignmentRequest.from_dict(request)
    req = _stamp(req)
    with lifecycle.assignment_lock(req.project, req.assignment_id):
        return _execute_locked(req)


def _refusal_envelope(req: AssignmentRequest, refusal: dict, *,
                       effect: dict | None = None,
                       message: str = "") -> dict:
    """The envelope a refusal returns: nothing ran, and the way out is named."""
    return {
        "version": ENVELOPE_VERSION, "assignment_id": req.assignment_id,
        "operation_id": req.operation_id, "stage": req.stage,
        "status": "refused",
        "progress": {"status": "refused", "fraction": 1.0,
                     "message": message or "Operation refused; nothing ran."},
        "inputs": dict(req.inputs), "effect": effect, "result": None,
        "refusal": refusal,
        "recovery": {"resumable": True, "resume_with": req.to_dict()},
    }


def _record_refusal(project: str, assignment_id: str, refusal: dict) -> None:
    """Keep the last refusal on the task, so the blocker can name it."""
    with lifecycle.assignment_lock(project, assignment_id):
        record = lifecycle.load(project, assignment_id)
        if record is None:
            return
        record["last_refusal"] = {
            "kind": refusal.get("kind", "unknown"),
            "code": refusal.get("code", ""),
            "what": refusal.get("what", ""),
            "fix": refusal.get("fix", ""),
            "operation_id": refusal.get("operation_id", ""),
            "at": refusal.get("at", ""),
        }
        lifecycle.save(project, record)


def _execute_locked(req: AssignmentRequest) -> dict:
    """Execute while holding this assignment's cross-process lock."""
    effect = lifecycle.operation_effect(req.project, req.assignment_id,
                                       req.operation_id, dict(req.inputs))
    if not effect["allowed"]:
        refusal = RenRefusal(
            effect["reason"],
            f"the operation effect {effect['effect']!r} requires a human decision",
            "resolve the decision for this assignment, then resubmit",
            code="REN_ASSIGNMENT_DECISION_REQUIRED", recovery_class="needs_question",
            ask_reason="missing_authorization",
            blocker="unsafe_state",
            next_action="answer_the_question").to_dict()
        from library.tools import assignment_refusal

        refusal["kind"] = assignment_refusal.classify(refusal)["kind"]
        refusal["operation_id"] = req.operation_id
        _record_refusal(req.project, req.assignment_id, refusal)
        return _refusal_envelope(req, refusal, effect=effect,
                                  message="Operation paused for a human decision.")
    checkpoint = lifecycle.begin_operation(req.project, req.assignment_id,
                                           req.operation_id, req.to_dict())
    if checkpoint.get("status") == "completed":
        return checkpoint["envelope"]
    try:
        result = operations.get(req.operation_id).execute(req.project, **dict(req.inputs))
    except RenRefusal as refused:
        # The step's own code refused: nothing ran, and the refusal says
        # which KIND of refusal it is. It is the task's last refusal, and
        # the blocker names it.
        from library.tools import assignment_refusal

        detail = refused.to_dict()
        detail["kind"] = assignment_refusal.classify(detail)["kind"]
        detail["operation_id"] = req.operation_id
        _record_refusal(req.project, req.assignment_id, detail)
        return _refusal_envelope(req, detail, effect=effect,
                                  message=refused.user_message)
    refused = result.refused
    refusal = None
    if refused:
        detail = result.refusal_reason() or result.error
        refusal_obj = RenRefusal(
            detail,
            "the operation's declared requirements or execution contract did not permit completion",
            result.error or "resolve the stated issue and resubmit this assignment operation",
            code="REN_OPERATION_REFUSED", recovery_class="blocked",
            blocker="unsupported_operation")
        refusal = refusal_obj.to_dict()
        if result.unsatisfied:
            refusal["unsatisfied"] = [
                {"name": item.name, "produced_by": item.produced_by}
                for item in result.unsatisfied]
        from library.tools import assignment_refusal

        classified = assignment_refusal.classify(result)
        refusal["kind"] = classified["kind"]
        refusal["recovery_class"] = classified["recovery_class"]
        refusal["operation_id"] = req.operation_id
        _record_refusal(req.project, req.assignment_id, refusal)
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
        "provenance": dict(req.provenance),
        "recovery": {"resumable": True, "resume_with": req.to_dict()},
    }
    lifecycle.finish_operation(req.project, req.assignment_id,
                               req.operation_id, envelope)
    if not refused:
        from library.tools import assignment_plan

        node = assignment_plan.find_node(
            req.project, req.assignment_id, node_id=req.node_id,
            operation_id=req.operation_id)
        if node is not None:
            assignment_plan.mark(req.project, req.assignment_id,
                                 node["node_id"], "completed")
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
    req = _stamp(req)
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
                  "approved": "deliver", "delivered": "deliver",
                  "accepted": "deliver", "rejected": "review"}


def _summary_message(record: Mapping, resume: Mapping) -> str:
    return f"Assignment {record['state']}: {resume['why']}."


def state(project: str, assignment_id: str | None = None) -> dict:
    """How a host reads an assignment: the same envelope every act returns.

    With no ``assignment_id`` the project's newest open assignment answers
    (``assignment_spine.current``), else the newest one of any state. The
    result carries the record, its history and the resume point - the
    one verb the assignment is waiting on - and the task state: what Ren
    understood, proposed, applied, could not determine, verified, and
    which corrections are open. The task state is a projection of the
    records the assignment already owns, so a host reads the answers
    instead of deriving them from the machinery.
    """
    from library.tools import assignment_spine, assignment_view
    root = str(Path(project).expanduser().resolve())
    record = lifecycle.resolve(root, assignment_id)
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
            "result": {**record, "resume_point": resume,
                       "task_state": assignment_view.task_state(
                           root, record["assignment_id"])},
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
    """`ren assignment <project>`: read (or act on) an assignment."""
    import argparse
    import sys

    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()

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
    parser.add_argument("--pause", metavar="REASON", default=None,
                        help="hold this assignment's acts until it is "
                             "resumed")
    parser.add_argument("--resume", action="store_true",
                        help="release a paused assignment")
    parser.add_argument("--replan", metavar="REASON", default=None,
                        help="replace the part of the plan that has not run")
    parser.add_argument("--cancel", metavar="REASON", default=None,
                        help="end this assignment; delivered work is kept")
    parser.add_argument("--reopen", action="store_true",
                        help="bring a cancelled assignment back as a new "
                             "request")
    parser.add_argument(
        "--edit-plan", metavar="JSON",
        help="record a typed E2E-009..015 edit plan from a JSON file; it "
             "does not contact Resolve")
    parser.add_argument(
        "--edit-apply", metavar="REQUEST_ID",
        help="apply a previously recorded edit plan through Resolve AXI")
    parser.add_argument(
        "--authorization-source", choices=("creator_brief", "creator_approval"),
        help="the creator authorization for --edit-apply")
    parser.add_argument(
        "--accept", metavar="JSON",
        help="record the FINAL ACCEPTANCE of this assignment from a JSON "
             "file: the facts that decide which legs apply and the result "
             "each applicable leg measured")
    args = parser.parse_args(argv)
    root = Path(args.project).expanduser()
    if not root.is_dir():
        from library.tools.project_registry import get_project
        try:
            root = Path(get_project(args.project).project_root)
        except FileNotFoundError as exc:
            parser.error(str(exc))
    acts = [name for name, value in (
        ("--authorize-replacement", args.authorize_replacement),
        ("--pause", args.pause),
        ("--resume", args.resume),
        ("--replan", args.replan),
        ("--cancel", args.cancel),
        ("--reopen", args.reopen)) if value]
    acts.extend(name for name, value in (
        ("--edit-plan", args.edit_plan), ("--edit-apply", args.edit_apply),
        ("--accept", args.accept))
                if value)
    if len(acts) > 1:
        parser.error("one act at a time: " + ", ".join(acts))
    if args.authorization_source and not args.edit_apply:
        parser.error("--authorization-source is only used with --edit-apply")
    try:
        envelope = _act(str(root), args)
    except FileNotFoundError as exc:
        print(f"ren assignment: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    return 0


def _act(root: str, args) -> dict:
    """The one act the verb was asked for, or the plain read."""
    from library.tools import assignment_actions

    if args.pause is not None:
        _require_id(args)
        assignment_actions.pause(root, args.assignment_id,
                                 reason=args.pause)
        return state(root, args.assignment_id)
    if args.resume:
        _require_id(args)
        assignment_actions.resume(root, args.assignment_id)
        return state(root, args.assignment_id)
    if args.replan is not None:
        _require_id(args)
        assignment_actions.replan(root, args.assignment_id, reason=args.replan)
        return state(root, args.assignment_id)
    if args.cancel is not None:
        _require_id(args)
        assignment_actions.cancel(root, args.assignment_id, reason=args.cancel)
        return state(root, args.assignment_id)
    if args.reopen:
        _require_id(args)
        assignment_actions.reopen(root, args.assignment_id)
        return state(root, args.assignment_id)
    if args.authorize_replacement:
        _require_id(args)
        return authorize(root, args.assignment_id)
    if args.edit_plan:
        _require_id(args)
        try:
            plan = json.loads(Path(args.edit_plan).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(f"cannot read edit plan JSON: {exc}") from exc
        from library.tools import assignment_edit_workflow
        return assignment_edit_workflow.plan(
            root, args.assignment_id,
            workflow_id=plan["workflow_id"], intent_id=plan.get("intent_id", ""),
            intent_request_id=plan["intent_request_id"], request=plan["request"],
            evidence_ids=plan["evidence_ids"], decision_ids=plan["decision_ids"],
            acceptance_criteria=plan["acceptance_criteria"])
    if args.edit_apply:
        _require_id(args)
        if not args.authorization_source:
            raise FileNotFoundError(
                "--edit-apply requires --authorization-source")
        from library.tools import assignment_edit_workflow
        return assignment_edit_workflow.apply(
            root, args.assignment_id, args.edit_apply,
            authorization_source=args.authorization_source)
    if args.accept:
        _require_id(args)
        try:
            document = json.loads(
                Path(args.accept).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(
                f"cannot read the acceptance JSON: {exc}") from exc
        from library.tools import acceptance_state
        acceptance_state.record(
            root, args.assignment_id,
            facts=document.get("facts") or {},
            results=document.get("results") or {})
        return state(root, args.assignment_id)
    return state(root, args.assignment_id)


def _require_id(args) -> None:
    if not args.assignment_id:
        raise FileNotFoundError(
            "this act names one assignment: pass --id <assignment id>")


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))

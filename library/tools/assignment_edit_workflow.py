"""Run a planned editorial edit as one assignment-owned AXI transaction.

EditorialIntent says what the person asked for, task evidence and decisions
say why the edit was chosen, and an EditRequest says which verified changes
to make. This module is the handoff between those records and the existing
AXI transaction boundary. It does not choose shots or invent edit parameters.

Planning is read-only with respect to Resolve. Applying a plan requires an
explicit authorization source, rechecks task provenance, and submits the
whole request once through AXI. The AXI receipt is kept on the assignment.

PLAN-011: a plan is compiled against the intent, and the intent carries
every instruction the person stated. Before anything runs, every one of
those instructions must be ACCOUNTED FOR: the plan carries it - a
criterion declares the instruction it serves - or the plan DECLINES it,
with a reason. A plan that is silent about a stated instruction is
REFUSED here, before the write, because a plan that drops half the
request silently is how the request arrives at the timeline as half of
itself. The account at the end of the task is
`library/tools/instruction_loop.py`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import assignment_lifecycle as lifecycle
from library.tools.edit_request import EditRequest
from library.tools.ren_refusal import RenRefusal


class WorkflowRefused(RenRefusal):
    """An edit workflow has missing intent, evidence, approval or scope."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            detail,
            "the assignment workflow cannot safely apply this plan",
            "resolve the named intent, evidence, decision or operation issue, "
            "then plan the edit again",
            code="REN_EDIT_WORKFLOW_REFUSED", recovery_class="blocked")


# A workflow ID narrows the algebra operations a plan may contain. Compound
# requests use E2E-015 and have to cover all four requested domains.
WORKFLOW_OPERATIONS = {
    "E2E-009": frozenset({"clip.trim", "clip.remove"}),
    "E2E-010": frozenset({"broll.insert", "clip.remove", "clip.replace"}),
    "E2E-011": frozenset({"multicam.select_angle", "multicam.angle"}),
    "E2E-012": frozenset({
        "caption_fix", "caption.style", "clip.move", "clip.trim",
    }),
    "E2E-013": frozenset({"reel.opening", "clip.trim", "clip.remove"}),
    "E2E-014": frozenset({
        "overlay.add", "overlay.remove", "overlay.position",
        "overlay.resize", "overlay.replace", "overlay.swap_pixels",
        "clip.set_property",
    }),
}
MIXED_DOMAINS = frozenset({
    "timeline_structure", "captions", "audio_mix",
})
AUTHORIZATION_SOURCES = frozenset({"creator_brief", "creator_approval"})
INTENT_KIND_BY_WORKFLOW = {
    "E2E-009": "tighten_dialogue",
    "E2E-010": "replace_broll",
    "E2E-011": "choose_better_angle",
    "E2E-012": "correct_captions",
    "E2E-013": "strengthen_opening",
    "E2E-014": "revise_graphics",
    "E2E-015": "mixed_edit",
}


def _root(project: str) -> str:
    return str(Path(project).expanduser().resolve())


def _intent(project: str, intent_id: str = ""):
    from library.tools import editorial_intent, project_model_store

    revision, model = project_model_store.load_project_model(Path(project))
    all_rows = [row for row in (model.get("intents") or [])
                if isinstance(row, Mapping) and row.get("id")]
    rows = ([row for row in all_rows if row["id"] == intent_id]
            if intent_id else all_rows)
    if len(rows) != 1:
        raise WorkflowRefused(
            "the assignment must name one EditorialIntent; an unnamed intent "
            "can be resolved only when the Project Model has exactly one")
    intent = editorial_intent.from_project_model_row(rows[0])
    problems = editorial_intent.validate_intent(intent)
    if problems:
        raise WorkflowRefused("invalid EditorialIntent: " + "; ".join(problems))
    return intent, revision


def _validate_operations(workflow_id: str, request: EditRequest) -> list[str]:
    from library.tools import edit_algebra

    if workflow_id == "E2E-015":
        domains = set()
        for operation in request.operations:
            status = edit_algebra.status(operation.op)
            if status is None:
                raise WorkflowRefused(
                    f"mixed edit contains unknown operation {operation.op!r}")
            domains.update(status.state_domains)
        missing = sorted(MIXED_DOMAINS - domains)
        has_visual = bool({"picture_transform", "fusion"} & domains)
        if missing or not has_visual:
            if not has_visual:
                missing.append("visual")
            raise WorkflowRefused(
                "mixed edit must include structural, visual, caption and audio "
                f"changes; missing domains: {', '.join(missing)}")
        return sorted(domains)
    allowed = WORKFLOW_OPERATIONS.get(workflow_id)
    if allowed is None:
        raise WorkflowRefused(
            f"{workflow_id!r} is not a directly executable edit workflow")
    unexpected = sorted({operation.op for operation in request.operations
                         if operation.op not in allowed})
    if unexpected:
        raise WorkflowRefused(
            f"{workflow_id} does not allow operations: {', '.join(unexpected)}")
    operation_ids = {operation.op for operation in request.operations}
    if workflow_id == "E2E-009" and not operation_ids.intersection(
            {"clip.trim", "clip.remove"}):
        raise WorkflowRefused("dialogue cleanup needs a trim or removal")
    if workflow_id == "E2E-010" and not (
            "clip.replace" in operation_ids
            or {"broll.insert", "clip.remove"} <= operation_ids):
        raise WorkflowRefused(
            "B-roll replacement needs clip.replace or a remove plus a cover insert")
    if workflow_id == "E2E-011" and "multicam.select_angle" not in operation_ids:
        raise WorkflowRefused("multicam refinement needs a synchronized angle selection")
    if workflow_id == "E2E-012":
        missing = {"caption_fix", "caption.style"} - operation_ids
        if not operation_ids.intersection({"clip.move", "clip.trim"}):
            missing.add("caption timing (clip.move or clip.trim)")
        if missing:
            raise WorkflowRefused(
                "caption correction is missing: " + ", ".join(sorted(missing)))
    if workflow_id == "E2E-013" and "reel.opening" not in operation_ids:
        raise WorkflowRefused("opening improvement needs a brief-scoped reel.opening edit")
    if workflow_id == "E2E-014" and not operation_ids.intersection(allowed):
        raise WorkflowRefused("graphic revision needs an overlay or logo property edit")
    return sorted({domain for operation in request.operations
                   for domain in edit_algebra.status(operation.op).state_domains})


def _account_for_stated_instructions(intent, intent_request_id: str,
                                     criteria: Sequence[Mapping[str, Any]],
                                     declined: Mapping[str, str] | None
                                     ) -> None:
    """Every instruction the intent stated is carried or declined.

    The intent carries what the person asked for: its explicit
    requirements and its explicit requests.  This plan serves ONE
    request, and may carry requirements through the criteria it
    declares - a criterion names the instruction it serves in
    `instruction_id`.  Anything the plan neither carries nor declines is
    a REFUSAL here, before the write: a plan that is silent about half
    the request is how half the request reaches the timeline.

    A decline carries a reason, because "this plan does not do that" is
    not an answer on its own.
    """
    stated: list[tuple[str, str]] = []
    served = getattr(intent, "request", None)
    served_request = served(intent_request_id) if served else None
    served_statement = str(getattr(served_request, "statement", "") or "")
    for requirement in getattr(intent, "requirements", ()) or ():
        if getattr(requirement, "explicit", True):
            stated.append((str(requirement.declaration_id),
                           str(requirement.statement)))
    for request in getattr(intent, "requests", ()) or ():
        if not getattr(request, "explicit", True):
            continue
        request_id = str(request.request_id)
        if request_id == str(intent_request_id):
            continue
        # ONE statement asked for as two kinds is one instruction, and
        # the plan serves it.
        if served_statement and str(request.statement) == served_statement:
            continue
        stated.append((request_id, str(request.statement)))
    if not stated:
        return
    carried = {str(row.get("instruction_id") or "") for row in criteria}
    declined_map = {str(name): str(reason)
                    for name, reason in dict(declined or {}).items()}
    known = {name for name, _statement in stated}
    unknown = sorted(set(declined_map) - known)
    if unknown:
        raise WorkflowRefused(
            "the plan declines instructions the intent does not carry: "
            + ", ".join(unknown))
    for name, reason in declined_map.items():
        if not reason.strip():
            raise WorkflowRefused(
                f"the plan declines the instruction {name!r} without a "
                f"reason; a decline has to say why")
    unaccounted = [name for name, _statement in stated
                   if name not in carried and name not in declined_map]
    if unaccounted:
        raise WorkflowRefused(
            "the plan does not account for every stated instruction: "
            + ", ".join(unaccounted)
            + " - carry it in a criterion's instruction_id, or decline it "
              "with a reason")
    for row in criteria:
        name = str(row.get("instruction_id") or "")
        if name and name not in known:
            raise WorkflowRefused(
                f"the criterion {str(row.get('id') or '')!r} carries the "
                f"instruction {name!r}, which the intent does not state")


def plan(project: str, assignment_id: str, *, workflow_id: str,
         intent_id: str = "", intent_request_id: str,
         request: EditRequest | Mapping,
         evidence_ids: Sequence[str], decision_ids: Sequence[str],
         acceptance_criteria: Sequence[Mapping[str, Any]],
         declined_instructions: Mapping[str, str] | None = None
         ) -> dict:
    """Validate and persist a typed workflow plan without contacting Resolve."""
    root = _root(project)
    req = request if isinstance(request, EditRequest) else EditRequest.from_dict(request)
    if workflow_id not in {*WORKFLOW_OPERATIONS, "E2E-015"}:
        raise WorkflowRefused(f"unknown executable workflow {workflow_id!r}")
    domains = _validate_operations(workflow_id, req)
    record = lifecycle.load(root, assignment_id)
    if record is None:
        raise FileNotFoundError(f"assignment {assignment_id!r} has no checkpoint")
    if record.get("state") == "cancelled" or record.get("pause"):
        raise WorkflowRefused("cancelled or paused assignments cannot accept an edit plan")
    named_intent = str(record.get("intent_id") or intent_id)
    if intent_id and record.get("intent_id") and record["intent_id"] != intent_id:
        raise WorkflowRefused(
            "the assignment must name this EditorialIntent before planning its edit")
    intent, model_revision = _intent(root, named_intent)
    intent_id = intent.intent_id
    if (req.expected_model_revision is not None
            and req.expected_model_revision != model_revision):
        raise WorkflowRefused(
            "EditRequest Project Model revision is stale for this EditorialIntent")
    intent_request = intent.request(intent_request_id)
    if intent_request is None:
        raise WorkflowRefused(
            f"EditorialIntent {intent_id!r} has no request {intent_request_id!r}")
    expected_kind = INTENT_KIND_BY_WORKFLOW[workflow_id]
    if intent_request.kind != expected_kind:
        raise WorkflowRefused(
            f"{workflow_id} requires EditorialIntent kind {expected_kind!r}, "
            f"not {intent_request.kind!r}")
    from library.tools import assignment_provenance, editorial_intent

    if not editorial_intent.evidence_sufficient(intent_request):
        missing = editorial_intent.missing_lanes(intent_request)
        raise WorkflowRefused(
            f"intent request {intent_request_id!r} lacks required evidence: "
            + ", ".join(missing))

    evidence_rows = {row["id"]: row
                     for row in assignment_provenance.evidence(root, assignment_id)}
    decision_rows = {row["id"]: row
                     for row in assignment_provenance.decisions(root, assignment_id)}
    known_evidence = set(evidence_rows)
    known_decisions = set(decision_rows)
    evidence = list(dict.fromkeys(str(value) for value in evidence_ids))
    decisions = list(dict.fromkeys(str(value) for value in decision_ids))
    missing_evidence = sorted(set(evidence) - known_evidence)
    missing_decisions = sorted(set(decisions) - known_decisions)
    if missing_evidence or missing_decisions:
        missing = [*(f"evidence:{value}" for value in missing_evidence),
                   *(f"decision:{value}" for value in missing_decisions)]
        raise WorkflowRefused("workflow provenance is not on the assignment: "
                              + ", ".join(missing))
    if not evidence or not decisions:
        raise WorkflowRefused(
            "an edit workflow needs recorded source evidence and an editorial decision")
    evidence_lanes = {
        str((evidence_rows[item].get("detail") or {}).get("lane") or "")
        for item in evidence
    }
    missing_lanes = sorted(set(intent_request.evidence_bar) - evidence_lanes)
    if missing_lanes:
        raise WorkflowRefused(
            "assignment evidence does not satisfy the intent's evidence bar: "
            + ", ".join(missing_lanes))
    if workflow_id == "E2E-012":
        caption_ids = {
            str(caption_id)
            for item_id in evidence
            for caption_id in
            (evidence_rows[item_id].get("detail") or {}).get("caption_ids", ())
        }
        for operation in req.operations:
            if operation.op not in {"clip.move", "clip.trim"}:
                continue
            target_id = operation.target.unique_id if operation.target else ""
            if not target_id or target_id not in caption_ids:
                raise WorkflowRefused(
                    "caption timing edits must target a caption identity named "
                    "by the recorded evidence")
    decisions_without_source = [
        decision_id for decision_id in decisions
        if not set(decision_rows[decision_id].get("evidence_ids") or ()) <= set(evidence)
    ]
    if decisions_without_source:
        raise WorkflowRefused(
            "workflow decisions cite evidence outside the selected source set: "
            + ", ".join(decisions_without_source))
    criteria = [dict(row) for row in acceptance_criteria]
    if not criteria or not {row.get("kind") for row in criteria} >= {
            "technical", "editorial"}:
        raise WorkflowRefused(
            "an edit workflow needs technical and editorial acceptance criteria")
    _account_for_stated_instructions(
        intent, intent_request_id, criteria, declined_instructions)
    payload = req.to_dict()
    payload["originator"] = {
        **payload.get("originator", {}),
        "kind": "agent",
        "name": "assignment editorial workflow",
        "assignment_id": assignment_id,
        "workflow_id": workflow_id,
        "intent_id": intent_id,
        "intent_request_id": intent_request_id,
        "evidence_ids": evidence,
        "decision_ids": decisions,
    }
    req = EditRequest.from_dict(payload)
    entry = {
        "workflow_id": workflow_id,
        "request_id": req.id,
        "intent_id": intent_id,
        "intent_request_id": intent_request_id,
        "intent_model_revision": model_revision,
        "request": req.to_dict(),
        "evidence_ids": evidence,
        "decision_ids": decisions,
        "domains": domains,
        "acceptance_criteria": criteria,
        "declined_instructions": dict(declined_instructions or {}),
        "status": "planned",
    }
    with lifecycle.assignment_lock(root, assignment_id):
        current = lifecycle.load(root, assignment_id)
        existing = current["edit_workflows"].get(req.id)
        if existing is not None and existing != entry:
            raise WorkflowRefused(
                f"request {req.id!r} already names a different workflow plan")
        from library.tools import assignment_contract
        assignment_contract.declare(root, assignment_id, criteria)
        current = lifecycle.load(root, assignment_id)
        current["edit_workflows"][req.id] = entry
        current["intent_id"] = intent_id
        lifecycle.save(root, current)
    return entry


def apply(project: str, assignment_id: str, request_id: str, *,
          authorization_source: str, timeout: float = 120.0) -> dict[str, Any]:
    """Apply one planned workflow through AXI and record its verified receipt.

    The source records that the original brief or a creator's approval
    authorizes this transaction. `assignment_spine.begin_revision` applies
    the existing per-assignment approved-work replacement gate.
    """
    if authorization_source not in AUTHORIZATION_SOURCES:
        raise PermissionError(
            "applying an editorial workflow requires creator_brief or creator_approval")
    root = _root(project)
    with lifecycle.assignment_lock(root, assignment_id):
        record = lifecycle.load(root, assignment_id)
        if record is None:
            raise FileNotFoundError(f"assignment {assignment_id!r} has no checkpoint")
        entry = record["edit_workflows"].get(request_id)
        if entry is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no planned edit {request_id!r}")
        if entry["status"] == "verified":
            return entry["receipt"]
        if entry["status"] not in {"planned", "applying"}:
            raise WorkflowRefused(
                f"edit workflow {request_id!r} is {entry['status']!r}, not resumable")
        # Recheck intent and task provenance at the point of mutation.
        intent, revision = _intent(root, entry["intent_id"])
        if revision != entry["intent_model_revision"]:
            raise WorkflowRefused("Project Model changed after the workflow was planned")
        from library.tools import assignment_provenance
        current_evidence = {row["id"] for row in assignment_provenance.evidence(root, assignment_id)}
        current_decisions = {row["id"] for row in assignment_provenance.decisions(root, assignment_id)}
        if (not set(entry["evidence_ids"]) <= current_evidence
                or not set(entry["decision_ids"]) <= current_decisions):
            raise WorkflowRefused("workflow evidence or decisions changed after planning")

    from library.tools import assignment_spine, resolve_axi_core
    intent_request = intent.request(entry["intent_request_id"])
    if intent_request is None:
        raise WorkflowRefused("the planned EditorialIntent request no longer exists")
    from library.tools.editorial_intent import (
        AUTONOMOUS,
        NEEDS_APPROVAL,
        NEEDS_CLARIFICATION,
    )
    if intent_request.autonomy == NEEDS_CLARIFICATION:
        raise WorkflowRefused(
            "the EditorialIntent requires clarification before this edit can run")
    if intent_request.autonomy == NEEDS_APPROVAL and authorization_source != "creator_approval":
        raise PermissionError(
            "this EditorialIntent requires a recorded creator approval")
    if authorization_source == "creator_brief" and (
            intent_request.autonomy != AUTONOMOUS or not intent_request.explicit):
        raise PermissionError(
            "creator_brief authorizes only an explicit autonomous intent request")
    if authorization_source == "creator_approval":
        creator_decisions = {
            row["id"] for row in assignment_provenance.decisions(root, assignment_id)
            if row.get("decided_by") == "creator"
        }
        if not creator_decisions.intersection(entry["decision_ids"]):
            raise PermissionError(
                "creator_approval needs a creator decision linked to this edit")

    act = assignment_spine.begin_revision(root, assignment_id)
    try:
        with lifecycle.assignment_lock(root, assignment_id):
            current = lifecycle.load(root, assignment_id)
            current["edit_workflows"][request_id]["status"] = "applying"
            lifecycle.save(root, current)
        request = EditRequest.from_dict(entry["request"])
        receipt = resolve_axi_core.submit_edit_request(
            request, authorized=True, owner=f"assignment:{assignment_id}",
            timeout=timeout)
        verified = receipt.get("status") in {
            "committed", "verified", "already_satisfied"}
        if not verified:
            raise WorkflowRefused(
                "AXI did not return a verified edit receipt: "
                f"{receipt.get('status', 'unknown')}")
        assignment_spine.end_act(
            act, ok=True, detail=f"{entry['workflow_id']} verified as {request_id}")
    except Exception as exc:
        assignment_spine.end_act(act, ok=False, status="refused", detail=str(exc))
        with lifecycle.assignment_lock(root, assignment_id):
            current = lifecycle.load(root, assignment_id)
            current["edit_workflows"][request_id].update(
                status="refused", refusal=str(exc))
            lifecycle.save(root, current)
        raise

    with lifecycle.assignment_lock(root, assignment_id):
        current = lifecycle.load(root, assignment_id)
        current["edit_workflows"][request_id].update(
            status="verified", authorization_source=authorization_source,
            receipt=receipt)
        lifecycle.save(root, current)
    return receipt

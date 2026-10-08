"""Assignment-level review and revision records.

This is the chat-first contract shared by host adapters and optional review
surfaces. It stores the creator's note, a current/result comparison, named
scoped variants, and an approval receipt. Timeline writes remain EditPatch
transactions supplied by the caller (normally the Resolve broker).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from library.tools import assignment_lifecycle as lifecycle
from library.tools import edit_patch, review_gate


class RevisionRefused(ValueError):
    """The requested revision cannot be applied safely."""


def _path(project: str, assignment_id: str, revision_id: str) -> Path:
    for label, value in (("assignment_id", assignment_id),
                         ("revision_id", revision_id)):
        if not value or value.strip() != value or any(c in value for c in "/\\\0"):
            raise ValueError(f"{label} must be a non-empty plain identifier")
    assignment = hashlib.sha256(assignment_id.encode("utf-8")).hexdigest()[:24]
    revision = hashlib.sha256(revision_id.encode("utf-8")).hexdigest()[:24]
    return (Path(project).expanduser().resolve() / "pipeline_output" /
            "llm_requests" / "assignments" / assignment / f"revision-{revision}.json")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as f:
            temporary = Path(f.name)
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def capture_review(project: str, assignment_id: str, revision_id: str, *,
                   note: str, current: Mapping, result: Mapping,
                   creative_difference: Mapping, base_generation: int,
                   variants: list[Mapping], qa_report: Mapping | None = None) -> dict:
    """Capture a natural-language review and explicitly named alternatives.

    Each variant has ``name``, ``summary`` and a serializable ``patch``.
    Patches are prevalidated as EditPatch contracts but are not applied until
    approval. The patch's base generation binds it to the reviewed state.
    """
    if not note or not note.strip():
        raise ValueError("a review note must contain natural-language feedback")
    if base_generation < 1:
        raise ValueError("base_generation must identify a recorded timeline state")
    if (qa_report is not None
            and (qa_report.get("version") != 1
                 or qa_report.get("assignment_id") != assignment_id
                 or not qa_report.get("render_id")
                 or not isinstance(qa_report.get("deterministic"), list)
                 or not isinstance(qa_report.get("subjective"), list))):
        raise ValueError("qa_report must be a versioned report for this assignment")
    if qa_report is not None:
        for kind in ("deterministic", "subjective"):
            for finding in qa_report[kind]:
                span = finding.get("span", {})
                intent = finding.get("intent", {})
                if (finding.get("kind") != kind or not finding.get("action")
                        or not intent.get("key") or not intent.get("provenance")
                        or not isinstance(span, Mapping)
                        or not {"start", "end"}.issubset(span)):
                    raise ValueError(
                        "qa_report findings need kind, span, intent and action")
    if not variants:
        raise ValueError("a revision needs at least one named variant")
    named = {}
    for variant in variants:
        name = variant["name"]
        if not name or name.strip() != name or name in named:
            raise ValueError("variant names must be unique, non-empty identifiers")
        patch = edit_patch.EditPatch.from_dict(dict(variant["patch"]))
        if patch.base_generation != base_generation:
            raise ValueError(
                f"variant {name!r} is not scoped to the reviewed generation")
        named[name] = {"name": name, "summary": variant["summary"],
                       "patch": patch.to_dict()}
    # Reuse the established gate feedback record as the note's review semantics.
    gate_id = f"assignment-{assignment_id}-{revision_id}"
    review_gate._assert_safe_gate_id(gate_id)
    record = {
        "version": 1, "assignment_id": assignment_id, "revision_id": revision_id,
        "status": "pending", "note": note.strip(),
        "comparison": {"current": dict(current), "result": dict(result),
                       "creative_difference": dict(creative_difference)},
        "base_generation": base_generation, "variants": named,
        # Slice 4's report is carried as data on the same review record so
        # chat hosts and review surfaces consume one assignment contract.
        "qa_report": dict(qa_report) if qa_report is not None else None,
        "selected_variant": None, "receipt": None,
        "recovery": {"resumable": True, "action": "await_creator_choice"},
    }
    path = _path(project, assignment_id, revision_id)
    with lifecycle.assignment_lock(project, assignment_id):
        if path.exists():
            prior = json.loads(path.read_text(encoding="utf-8"))
            if prior != record:
                raise RevisionRefused(
                    "revision ID already names different review content")
            return prior
        review_gate.save_gate_snapshot(
            project, gate_id, "Assignment revision review", record,
            {"assignment_id": assignment_id, "revision_id": revision_id,
             "base_generation": base_generation})
        _write(path, record)
    return record


def load_review(project: str, assignment_id: str, revision_id: str) -> dict:
    path = _path(project, assignment_id, revision_id)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["assignment_id"] != assignment_id or value["revision_id"] != revision_id:
        raise RevisionRefused("revision record identity mismatch")
    return value


def decide(project: str, assignment_id: str, revision_id: str, *,
           action: str, variant_name: str | None = None,
           apply_patch: Callable[[dict], dict] | None = None,
           current_generation: int | None = None) -> dict:
    """Approve one variant through an EditPatch writer, or reject the review.

    The caller owns the broker route. A stale generation is durably recorded
    for recovery without invoking the writer; rejection also never writes.
    """
    path = _path(project, assignment_id, revision_id)
    with lifecycle.assignment_lock(project, assignment_id):
        record = load_review(project, assignment_id, revision_id)
        if record["status"] != "pending":
            if record["status"] in {"rejected", "stale"}:
                return record
            raise RevisionRefused(f"revision is already {record['status']!r}")
        if action == "rejected":
            feedback_path = review_gate.save_gate_feedback(
                project, f"assignment-{assignment_id}-{revision_id}",
                "rejected", record["note"])
            record.update(
                status="rejected", selected_variant=None,
                recovery={"resumable": True,
                          "action": "revise_from_feedback"})
            record["review_feedback"] = str(feedback_path)
            _write(path, record)
            return record
        if action != "approved":
            raise ValueError("action must be 'approved' or 'rejected'")
        if variant_name not in record["variants"]:
            raise RevisionRefused("approval must name exactly one recorded variant")
        if current_generation != record["base_generation"]:
            record.update(status="stale", selected_variant=variant_name,
                          recovery={"resumable": True, "action": "rebase_or_replan",
                                    "reviewed_generation": record["base_generation"],
                                    "current_generation": current_generation})
            _write(path, record)
            return record
        if apply_patch is None:
            raise ValueError("approved revisions require the EditPatch broker writer")
        patch = record["variants"][variant_name]["patch"]
        receipt = apply_patch(patch)
        record.update(status="applied", selected_variant=variant_name,
                      receipt=receipt,
                      recovery={"resumable": True,
                                "action": "submit_inverse_edit_patch"})
        _write(path, record)
        return record


def rollback(project: str, assignment_id: str, revision_id: str, *,
             inverse_patch: Mapping, apply_patch: Callable[[dict], dict],
             current_generation: int) -> dict:
    """Undo an applied revision by committing a new inverse EditPatch.

    EditPatch records timeline truth and does not erase a partial or prior
    write. Callers prepare the inverse against the applied receipt's new
    generation, then submit it through the same broker writer.
    """
    path = _path(project, assignment_id, revision_id)
    with lifecycle.assignment_lock(project, assignment_id):
        record = load_review(project, assignment_id, revision_id)
        if record["status"] != "applied":
            raise RevisionRefused("only an applied revision can be rolled back")
        patch = edit_patch.EditPatch.from_dict(dict(inverse_patch))
        if patch.base_generation != current_generation:
            raise RevisionRefused(
                "rollback patch is not based on the current timeline generation")
        expected_generation = record["receipt"].get("generation")
        if (expected_generation is not None
                and current_generation != expected_generation):
            record["status"] = "rollback_stale"
            record["recovery"] = {"resumable": True, "action": "replan_inverse_patch",
                                  "applied_generation": expected_generation,
                                  "current_generation": current_generation}
            _write(path, record)
            return record
        receipt = apply_patch(patch.to_dict())
        record["status"] = "rolled_back"
        record["rollback_receipt"] = receipt
        record["recovery"] = {"resumable": True, "action": "none"}
        _write(path, record)
        return record

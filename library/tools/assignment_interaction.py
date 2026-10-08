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
    return {
        "version": ENVELOPE_VERSION,
        "assignment_id": req.assignment_id,
        "operation_id": result.operation,
        "stage": req.stage,
        "status": "refused" if refused else "completed",
        "progress": {"status": "refused" if refused else "completed", "fraction": 1.0,
                     "message": "Operation refused; nothing ran." if refused else "Operation completed."},
        "inputs": dict(req.inputs),
        "result": None if refused else _json_value(result.payload),
        "refusal": refusal,
        "recovery": {"resumable": True, "resume_with": req.to_dict()},
    }


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
            return parsed["envelope"]
    request_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    llm_handshake.publish_operation_request(request_path, response_path, req.to_dict())
    envelope = execute(req)
    llm_handshake.publish_operation_response(request_path, response_path,
                                             {"request": req.to_dict(), "envelope": envelope})
    return envelope

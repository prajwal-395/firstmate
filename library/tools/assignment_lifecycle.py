"""Durable assignment checkpoints and selective operation autonomy.

The lifecycle record is stored with the project's other handshake records.
It is deliberately small and transport independent: adapters persist the
same assignment identity and operation checkpoint regardless of host.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

STATES = ("brief", "draft", "review", "approved", "delivered")
TRANSITIONS = {
    "brief": {"draft"},
    "draft": {"review", "brief"},
    "review": {"draft", "approved"},
    "approved": {"delivered", "review"},
    "delivered": {"review"},
}
SAFE_OPERATION_EFFECTS = {
    "footage.scan": "index",
    "footage.catalog": "index",
    "temporal.index": "index",
    "semantics.analyse": "draft",
    "creative.direct": "draft",
    "speech.enrich": "draft",
    "music.resolve": "draft",
    "duration_zone.build": "draft",
    "spine.mesh": "draft",
    "picture.plan": "draft",
    "aroll.assign": "draft",
    "aroll.splice": "draft",
    "broll.resolve": "draft",
    "broll.splice": "draft",
    "reel.candidates": "draft",
    "reel.select": "draft",
    "subtitles.plan": "draft",
    "subtitles.splice": "draft",
    "transitions.resolve": "draft",
    "transitions.splice": "draft",
    "vfx.resolve": "draft",
    "vfx.splice": "draft",
    "sfx.resolve": "draft",
    "sfx.splice": "draft",
    "color_grade.resolve": "draft",
    "audio_mix.resolve": "draft",
    "rough_cut.review": "draft",
    "cohesion.review": "draft",
    "manifest.compile": "draft",
    "edit_graph.build": "draft",
    "render.build": "draft",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _directory(project: str) -> Path:
    return (
        Path(project).expanduser().resolve()
        / "pipeline_output"
        / "llm_requests"
        / "assignments"
    )


def _path(project: str, assignment_id: str) -> Path:
    digest = hashlib.sha256(assignment_id.encode("utf-8")).hexdigest()[:24]
    return _directory(project) / f"{digest}.json"


@contextmanager
def assignment_lock(project: str, assignment_id: str):
    """Serialize duplicate host calls for one assignment across processes."""
    checkpoint = _path(project, assignment_id)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    lock_path = checkpoint.with_suffix(".lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def load(project: str, assignment_id: str) -> dict | None:
    try:
        record = json.loads(_path(project, assignment_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if record.get("assignment_id") != assignment_id:
        raise ValueError("assignment checkpoint identity mismatch")
    return record


def list_records(project: str) -> list[dict]:
    """Every assignment checkpoint in the project, oldest first."""
    directory = _directory(project)
    records = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("version") == 1 \
                and record.get("assignment_id"):
            records.append(record)
    return sorted(records, key=lambda r: (r.get("created_at") or "",
                                          r["assignment_id"]))


def resolve(project: str, assignment_id: str | None = None) -> dict | None:
    """The assignment a host means: the named one, or the open one.

    One place, because two readers that each pick "the" assignment end up
    reading different tasks - and a reader that guesses the newest record
    while the writer guesses the open one is how a correction lands on the
    wrong request. The named assignment must exist; the unnamed one is the
    project's open assignment, else the newest of any state, else None.
    """
    if assignment_id:
        record = load(project, assignment_id)
        if record is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        return record
    from library.tools import assignment_spine

    records = list_records(project)
    return assignment_spine.current(project) or (
        max(records, key=lambda r: (r.get("updated_at") or "",
                                    r.get("created_at") or ""))
        if records else None)


def create(project: str, assignment_id: str, brief=None, **fields) -> dict:
    """Create the checkpoint once; ``fields`` seed only a NEW record."""
    existing = load(project, assignment_id)
    if existing is not None:
        return existing
    now = _now()
    record = {
        "version": 1,
        "assignment_id": assignment_id,
        "state": "brief",
        "brief": brief,
        "replacement_approved": False,
        "checkpoint": None,
        "operations": {},
        "history": [],
        "created_at": now,
        "updated_at": now,
        **fields,
    }
    _write(_path(project, assignment_id), record)
    return record


def save(project: str, record: dict) -> dict:
    """Persist a record a caller changed under ``assignment_lock``."""
    record["updated_at"] = _now()
    _write(_path(project, record["assignment_id"]), record)
    return record


def update(project: str, assignment_id: str,
           mutate: Callable[[dict], None]) -> dict:
    """Apply ``mutate`` to an existing record and persist it."""
    record = load(project, assignment_id)
    if record is None:
        raise FileNotFoundError(f"assignment {assignment_id!r} has no checkpoint")
    mutate(record)
    return save(project, record)


def transition(project: str, assignment_id: str, target: str, *, brief=None,
               event: str | None = None, detail: dict | None = None) -> dict:
    """Move to ``target``; a named ``event`` is appended to the history.

    An event whose target is the current state is still recorded: the
    history is what happened to the assignment, and an act that leaves
    the state where it was (approving a plan, delivering a second reel)
    happened all the same. Without an event a same-state call is a no-op.
    """
    if target not in STATES:
        raise ValueError(f"unknown assignment state {target!r}")
    record = create(project, assignment_id, brief)
    current = record["state"]
    if target == current and event is None:
        return record
    if target != current and target not in TRANSITIONS[current]:
        raise ValueError(f"invalid assignment transition {current!r} -> {target!r}")
    record["state"] = target
    if target == "approved" and current != "approved":
        # Per-assignment authorization covers one revision round: the
        # creator approving the work again closes it (decision D2).
        record["replacement_approved"] = False
    if event is not None:
        record.setdefault("history", []).append(
            {"event": event, "from": current, "to": target, "at": _now(),
             **(detail or {})})
    return save(project, record)


def authorize_replacement(project: str, assignment_id: str) -> dict:
    record = create(project, assignment_id)
    record["replacement_approved"] = True
    return save(project, record)


def _operation_key(operation_id: str, request: dict) -> str:
    payload = json.dumps(request, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{operation_id}:{digest}"


def operation_effect(
    project: str, assignment_id: str, operation_id: str, inputs: dict
) -> dict:
    """Return an autonomy decision; unsafe requests never reach operation code."""
    record = create(project, assignment_id)
    if inputs.get("delete_source_media"):
        return {
            "effect": "delete_source_media",
            "disposition": "ask",
            "allowed": False,
            "reason": "source media deletion needs explicit authorization",
        }
    if inputs.get("direction_change"):
        return {
            "effect": "change_settled_direction",
            "disposition": "clarify",
            "allowed": False,
            "reason": "settled creative direction needs clarification",
        }
    # A caller that KNOWS it replaces approved work says so; that holds
    # whatever the assignment's aggregate state, because one signed-off
    # reel of a request still in review is approved work.
    declared = bool(inputs.get("replace_approved_work")
                    or inputs.get("replaces_approved_work"))
    replaces = declared or (
        record["state"] in {"approved", "delivered"}
        and SAFE_OPERATION_EFFECTS.get(operation_id) == "draft"
    )
    if replaces and (declared or record["state"] in {"approved", "delivered"}):
        if not record["replacement_approved"]:
            return {
                "effect": "replace_approved_work",
                "disposition": "ask",
                "allowed": False,
                "reason": "replacement requires explicit per-assignment approval",
            }
        return {
            "effect": "replace_approved_work",
            "disposition": "proceed",
            "allowed": True,
            "reason": "explicit per-assignment approval is recorded",
        }
    effect = SAFE_OPERATION_EFFECTS.get(operation_id)
    if effect is not None:
        return {
            "effect": effect,
            "disposition": "proceed",
            "allowed": True,
            "reason": "safe indexing/search/draft work may proceed",
        }
    return {
        "effect": "unclassified",
        "disposition": "ask",
        "allowed": False,
        "reason": "operation has no declared selective-autonomy effect",
    }


def begin_operation(
    project: str, assignment_id: str, operation_id: str, request: dict
) -> dict:
    record = create(project, assignment_id)
    key = _operation_key(operation_id, request)
    existing = record["operations"].get(key)
    if existing and existing.get("request") != request:
        raise ValueError("assignment operation ID was reused with different inputs")
    if existing and existing.get("status") == "completed":
        return existing
    checkpoint = {"operation_id": operation_id, "request": request, "status": "running"}
    record["operations"][key] = checkpoint
    record["checkpoint"] = key
    save(project, record)
    return checkpoint


def finish_operation(
    project: str, assignment_id: str, operation_id: str, envelope: dict
) -> None:
    record = create(project, assignment_id)
    key = _operation_key(operation_id, envelope["recovery"]["resume_with"])
    checkpoint = record["operations"][key]
    checkpoint.update(status="completed", envelope=envelope)
    save(project, record)


def interrupt(project: str, assignment_id: str) -> dict:
    record = load(project, assignment_id)
    if record is None:
        raise FileNotFoundError(f"assignment {assignment_id!r} has no checkpoint")
    operation_id = record.get("checkpoint")
    if operation_id and record["operations"][operation_id]["status"] == "running":
        record["operations"][operation_id]["status"] = "interrupted"
        save(project, record)
    return record


def recover(project: str, assignment_id: str) -> dict:
    """Return the exact saved result, or an interrupted request to resume."""
    record = load(project, assignment_id)
    if record is None:
        return create(project, assignment_id)
    operation_id = record.get("checkpoint")
    checkpoint = record["operations"].get(operation_id) if operation_id else None
    return {
        **record,
        "recovery": (
            "completed"
            if checkpoint and checkpoint["status"] == "completed"
            else "resume"
            if checkpoint
            else "continue"
        ),
        "resume_request": checkpoint.get("request") if checkpoint else None,
        "saved_envelope": checkpoint.get("envelope") if checkpoint else None,
    }

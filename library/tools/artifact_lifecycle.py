"""Ownership, inventory and guarded retirement for Ren artifacts.

The project event log is the durable record. Filesystem paths are storage
locations, not identities; records carry stable artifact ids and the file
identity observed when the artifact was registered. Unknown ownership is
protected. This is the shared contract for project cleanup lanes.
"""

from __future__ import annotations

import errno
import hashlib
import os
import socket
import stat
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from library.tools import event_log, resource_leases, run_control
from library.tools import artifact_policy
from library.tools import git_guard as _git_guard
from library.tools.resource_leases import LeaseDeclaration, ResourceRef

CREATED = "created"
ACTIVE = "active"
COMPLETED = "completed"
FAILED = "failed"
RECOVERY_HOLD = "recovery_hold"
RETAINED = "retained"
CACHEABLE = "cacheable"
DISPOSABLE = "disposable"
QUARANTINED = "quarantined"
PURGED = "purged"
PURGING = "purging"
LIFECYCLE_STATES = frozenset({CREATED, ACTIVE, COMPLETED, FAILED,
                              RECOVERY_HOLD, RETAINED, CACHEABLE,
                              DISPOSABLE, QUARANTINED, PURGING, PURGED})
TRANSITIONS = {
    CREATED: {ACTIVE, COMPLETED, FAILED, RETAINED},
    ACTIVE: {COMPLETED, FAILED, RECOVERY_HOLD},
    COMPLETED: {RETAINED, CACHEABLE, DISPOSABLE},
    FAILED: {RECOVERY_HOLD, RETAINED, DISPOSABLE},
    RECOVERY_HOLD: {RETAINED, DISPOSABLE},
    RETAINED: {DISPOSABLE},
    CACHEABLE: {DISPOSABLE},
    DISPOSABLE: {QUARANTINED, PURGING, PURGED},
    QUARANTINED: {DISPOSABLE, PURGING},
    PURGING: {PURGED, DISPOSABLE},
    PURGED: set(),
}
ARTIFACT_REGISTERED = "artifact_registered"
ARTIFACT_TRANSITION = "artifact_lifecycle_transition"
ARTIFACT_REBOUND = "artifact_identity_rebound"
CLEANUP_JOB = "cleanup_job"


class LifecycleRefused(ValueError):
    """An artifact is not provably safe for the requested operation."""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int
    link_count: int

    @classmethod
    def capture(cls, path: str | os.PathLike) -> FileIdentity:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise LifecycleRefused(f"artifact is not a regular non-symlink file: {path}")
        return cls(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                   info.st_nlink)

    def matches(self, path: str | os.PathLike) -> bool:
        try:
            return self == FileIdentity.capture(path)
        except (OSError, LifecycleRefused):
            return False


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    kind: str
    project_id: str
    run_id: str
    job_id: str
    path: str
    created_at: str
    identity: FileIdentity
    retention_class: str = ""
    dependencies: tuple[str, ...] = ()
    pinned: bool = False
    regenerable: bool = False
    regeneration_method: str = ""

    def __post_init__(self):
        if not self.artifact_id or not self.kind or not self.path:
            raise ValueError("artifact identity, kind and path are required")


def _event_fields(event: dict) -> dict:
    return event.get("fields", {})


def _latest_artifacts(project_folder: str) -> dict[str, dict]:
    return _latest_from_events(event_log.read_events(project_folder))


def _latest_artifacts_path(log_path: str | os.PathLike) -> dict[str, dict]:
    return _latest_from_events(event_log.read_events_path(log_path))


def _latest_from_events(events) -> dict[str, dict]:
    latest = {}
    for event in events:
        fields = _event_fields(event)
        artifact_id = fields.get("artifact_id")
        if not artifact_id:
            continue
        if event.get("type") == ARTIFACT_REGISTERED:
            latest[artifact_id] = fields
        elif event.get("type") in {ARTIFACT_TRANSITION, ARTIFACT_REBOUND} \
                and artifact_id in latest:
            latest[artifact_id] = {**latest[artifact_id], **fields}
    return latest


def register_artifact(project_folder: str, record: ArtifactRecord) -> bool:
    """Record an artifact once; missing ownership remains explicitly unknown.

    A record that names no retention class takes the one its AREA has:
    `library/tools/artifact_policy.py` owns that reading, so a file in
    a named place is judged by the place and not by whichever writer
    happened to register it.
    """
    if not (record.project_id and record.run_id and record.job_id):
        raise LifecycleRefused("unknown artifact ownership is protected")
    payload = asdict(record)
    payload["retention_class"] = record.retention_class or artifact_policy.effective_class(
        project_folder, record.path)
    payload["state"] = CREATED
    _, created = event_log.emit_once(
        project_folder, ARTIFACT_REGISTERED, record.artifact_id,
        task=record.job_id, **payload)
    return created


def register_resolve_artifact(log_path: str | os.PathLike, *, artifact_id: str,
                              kind: str, project_id: str, run_id: str,
                              job_id: str, identity: dict,
                              retention_class: str = "temporary",
                              state: str = CREATED) -> bool:
    """Register a Resolve object in the same lifecycle event stream.

    Resolve objects have no filesystem inode. Their identity is the exact
    Resolve project ID plus object ID and type, recorded alongside the same
    ownership fields and lifecycle states as file artifacts.
    """
    if not all((artifact_id, kind, project_id, run_id, job_id)):
        raise LifecycleRefused("unknown Resolve artifact ownership is protected")
    if state not in LIFECYCLE_STATES:
        raise ValueError(f"unknown artifact lifecycle state: {state}")
    if (not isinstance(identity, dict)
            or not identity.get("project_unique_id")
            or not identity.get("object_unique_id")
            or identity.get("object_type") not in {"project", "timeline"}):
        raise LifecycleRefused("Resolve artifact identity is incomplete")
    _, created = event_log.emit_once_path(
        log_path, ARTIFACT_REGISTERED, artifact_id, task=job_id,
        artifact_id=artifact_id, kind=kind, project_id=project_id,
        run_id=run_id, job_id=job_id, identity_type="resolve",
        identity=identity, retention_class=retention_class, state=state,
        dependencies=[], pinned=retention_class in {"retained", "protected"})
    recorded = _latest_artifacts_path(log_path).get(artifact_id)
    if (recorded is None or recorded.get("identity_type") != "resolve"
            or recorded.get("identity") != identity
            or recorded.get("project_id") != project_id
            or recorded.get("run_id") != run_id
            or recorded.get("job_id") != job_id):
        raise LifecycleRefused(
            f"Resolve artifact id is already owned by a different identity: {artifact_id}")
    return created


def transition_artifact(project_folder: str, artifact_id: str, target: str,
                        *, reason: str, event_id: str = "",
                        path: str = "", identity: FileIdentity | None = None,
                        original_path: str = "",
                        transaction_id: str = "") -> bool:
    """Make a recoverable, idempotent lifecycle transition in the event log."""
    if target not in LIFECYCLE_STATES:
        raise ValueError(f"unknown artifact lifecycle state: {target}")
    current = _latest_artifacts(project_folder).get(artifact_id)
    if current is None:
        raise LifecycleRefused(f"artifact ownership is unknown: {artifact_id}")
    old = current.get("state", CREATED)
    if old == target:
        return False
    if target not in TRANSITIONS.get(old, set()):
        raise LifecycleRefused(f"invalid artifact transition {old} -> {target}")
    stable_event_id = event_id or f"artifact:{artifact_id}:{old}:{target}"
    updates = {}
    if path:
        updates["path"] = str(Path(path).absolute())
    if identity is not None:
        updates["identity"] = asdict(identity)
    if original_path:
        updates["original_path"] = str(Path(original_path).absolute())
    if transaction_id:
        # The transaction this artifact is held for. It travels with the
        # artifact so a later reader knows WHICH edit still needs it.
        updates["transaction_id"] = str(transaction_id)
    event_log.emit_once(project_folder, ARTIFACT_TRANSITION, stable_event_id,
                        task=current["job_id"], artifact_id=artifact_id,
                        state=target, previous_state=old, reason=reason,
                        **updates)
    return True


def rebind_file(project_folder: str, artifact_id: str, *,
                identity: FileIdentity, path: str = "",
                reason: str) -> bool:
    """Record the file an artifact names now, after Ren replaced it.

    Ren replaces files it owns: a recovery archive is re-exported at the
    same path. When it does, the RECORD has to follow the FILE, or every
    later read says the artifact drifted - and a drift nothing can settle
    is a finding that names the same artifact forever. The identity the
    record had is kept beside the new one, so the replacement is an
    audited fact and not a silent overwrite.

    A rebind changes no lifecycle state: the artifact is exactly as
    disposable, as held or as protected as it was.
    """
    current = _latest_artifacts(project_folder).get(artifact_id)
    if current is None:
        raise LifecycleRefused(f"artifact ownership is unknown: {artifact_id}")
    previous = current.get("identity") or {}
    if previous == asdict(identity) and (
            not path or path == current.get("path")):
        return False
    fields = {
        "state": current.get("state", CREATED),
        "previous_state": current.get("state", CREATED),
        "identity": asdict(identity),
        "previous_identity": previous,
        "reason": reason,
    }
    if path:
        fields["path"] = str(Path(path).absolute())
    digest = hashlib.sha256(
        f"{path}\0{identity.device}\0{identity.inode}\0"
        f"{identity.mtime_ns}\0{identity.size_bytes}".encode()).hexdigest()
    _, created = event_log.emit_once(
        project_folder, ARTIFACT_REBOUND,
        f"artifact:{artifact_id}:rebound:{digest}",
        task=current.get("job_id", ""), artifact_id=artifact_id, **fields)
    return created


def artifacts_at_path(project_folder: str, path: str | os.PathLike) -> list[str]:
    """The artifact ids whose recorded path is exactly this one."""
    target = str(Path(os.path.abspath(str(path))))
    return sorted(
        artifact_id for artifact_id, fields in
        _latest_artifacts(project_folder).items()
        if fields.get("path")
        and str(Path(os.path.abspath(fields["path"]))) == target)


# ── Recovery holds: an artifact kept until its edit is settled ──────

def hold_for_transaction(project_folder: str, artifact_id: str, *,
                         transaction_id: str, reason: str) -> dict:
    """Hold a recovery artifact against the transaction that needs it.

    A HELD artifact is not disposable: the cleanup refuses it, so the
    recovery evidence survives until the edit is safely committed or the
    recovery is explicitly abandoned (PRES-023). The hold names the
    transaction, so a later reader knows WHICH edit still needs the
    artifact - and a hold cannot be released by the wrong transaction.
    """
    if not transaction_id:
        raise LifecycleRefused(
            "a hold must name the transaction it holds for")
    current = _latest_artifacts(project_folder).get(artifact_id)
    if current is None:
        raise LifecycleRefused(f"artifact ownership is unknown: {artifact_id}")
    held_for = str(current.get("transaction_id") or "")
    if held_for and held_for != str(transaction_id):
        raise LifecycleRefused(
            f"artifact {artifact_id} is already held for transaction "
            f"{held_for!r}, not {transaction_id!r}")
    if current.get("state") == RECOVERY_HOLD:
        return {"artifact_id": artifact_id, "state": RECOVERY_HOLD,
                "transaction_id": held_for or str(transaction_id),
                "changed": False}
    state = current.get("state", CREATED)
    if state == CREATED:
        # A registered artifact that has not been used yet is still in
        # use once it is registered for recovery; the hold needs a
        # state it can be reached from.
        transition_artifact(project_folder, artifact_id, ACTIVE,
                            reason="the artifact is in use")
    transition_artifact(project_folder, artifact_id, RECOVERY_HOLD,
                        reason=reason, transaction_id=str(transaction_id))
    return {"artifact_id": artifact_id, "state": RECOVERY_HOLD,
            "transaction_id": str(transaction_id), "changed": True}


def release_hold(project_folder: str, artifact_id: str, *,
                 transaction_id: str, outcome: str) -> dict:
    """Release a held artifact: the edit committed, or it was abandoned.

    `committed` keeps the artifact (`retained`); `abandoned` makes it
    disposable again, which is the ONLY way a recovery artifact becomes
    collectable. A hold is released only by the transaction it names.
    """
    if outcome not in {"committed", "abandoned"}:
        raise ValueError(
            "a release outcome is 'committed' or 'abandoned', not "
            f"{outcome!r}")
    current = _latest_artifacts(project_folder).get(artifact_id)
    if current is None:
        raise LifecycleRefused(f"artifact ownership is unknown: {artifact_id}")
    if current.get("state") != RECOVERY_HOLD:
        raise LifecycleRefused(
            f"artifact {artifact_id} is not held: {current.get('state')}")
    held_for = str(current.get("transaction_id") or "")
    if held_for and held_for != str(transaction_id):
        raise LifecycleRefused(
            f"artifact {artifact_id} is held for transaction {held_for!r}, "
            f"not {transaction_id!r}")
    target = RETAINED if outcome == "committed" else DISPOSABLE
    transition_artifact(project_folder, artifact_id, target,
                        reason=f"recovery {outcome}",
                        transaction_id=held_for)
    return {"artifact_id": artifact_id, "state": target,
            "transaction_id": held_for, "outcome": outcome}


def held_artifacts(project_folder: str, *, transaction_id: str = "") -> list[dict]:
    """The artifacts currently held for recovery, optionally for one
    transaction."""
    rows = []
    for artifact_id, fields in _latest_artifacts(project_folder).items():
        if fields.get("state") != RECOVERY_HOLD:
            continue
        held_for = str(fields.get("transaction_id") or "")
        if transaction_id and held_for != str(transaction_id):
            continue
        rows.append({
            "artifact_id": artifact_id,
            "kind": fields.get("kind"),
            "path": fields.get("path"),
            "transaction_id": held_for,
            "retention_class": fields.get("retention_class"),
            "project_id": fields.get("project_id"),
            "run_id": fields.get("run_id"),
            "job_id": fields.get("job_id"),
        })
    return sorted(rows, key=lambda row: row["artifact_id"])


def transition_resolve_artifact(log_path: str | os.PathLike, artifact_id: str,
                                target: str, *, reason: str,
                                event_id: str = "",
                                identity: dict | None = None) -> bool:
    """Transition an event-log-owned Resolve object using lifecycle states."""
    if target not in LIFECYCLE_STATES:
        raise ValueError(f"unknown artifact lifecycle state: {target}")
    current = _latest_artifacts_path(log_path).get(artifact_id)
    if current is None or current.get("identity_type") != "resolve":
        raise LifecycleRefused(f"Resolve artifact ownership is unknown: {artifact_id}")
    old = current.get("state", CREATED)
    if old == target:
        return False
    if target not in TRANSITIONS.get(old, set()):
        raise LifecycleRefused(f"invalid artifact transition {old} -> {target}")
    stable_event_id = event_id or f"artifact:{artifact_id}:{old}:{target}"
    event_log.emit_once_path(
        log_path, ARTIFACT_TRANSITION, stable_event_id,
        task=current["job_id"], artifact_id=artifact_id, state=target,
        previous_state=old, reason=reason,
        **({"identity": identity} if identity is not None else {}))
    return True


def resolve_artifacts(log_path: str | os.PathLike, *,
                      job_id: str = "") -> list[dict]:
    """Return the latest recorded Resolve artifact ownership rows."""
    rows = [dict(fields, artifact_id=artifact_id)
            for artifact_id, fields in _latest_artifacts_path(log_path).items()
            if fields.get("identity_type") == "resolve"
            and (not job_id or fields.get("job_id") == job_id)]
    return sorted(rows, key=lambda row: row["artifact_id"])


def retire_resolve_artifact(log_path: str | os.PathLike, artifact_id: str,
                            identity: dict, retire) -> dict:
    """Run an exact-identity retirement callback under Resolve's lease.

    The callback performs Resolve read-back, deletion and post-delete census.
    It must return ``{"verified": True}``; otherwise the lifecycle remains
    disposable for a safe retry.
    """
    from library.tools import resolve_lock

    if not resolve_lock.exclusive_held():
        raise LifecycleRefused("Resolve retirement requires the exclusive instance lease")
    row = _latest_artifacts_path(log_path).get(artifact_id)
    if row is None or row.get("identity_type") != "resolve":
        raise LifecycleRefused(f"Resolve artifact ownership is unknown: {artifact_id}")
    if row.get("state") not in {DISPOSABLE, PURGING}:
        raise LifecycleRefused(
            f"Resolve artifact is not disposable: {row.get('state')}")
    if row.get("identity") != identity:
        raise LifecycleRefused("Resolve artifact identity differs from its ownership record")
    attempt_id = uuid.uuid4().hex
    if row.get("state") == DISPOSABLE:
        transition_resolve_artifact(
            log_path, artifact_id, PURGING,
            reason="guarded Resolve retirement started",
            event_id=f"resolve-retirement:{artifact_id}:{attempt_id}:started")
    try:
        receipt = retire(dict(row["identity"]))
        if not isinstance(receipt, dict) or receipt.get("verified") is not True:
            raise LifecycleRefused(
                "Resolve deletion did not verify against a fresh census")
    except Exception:
        transition_resolve_artifact(
            log_path, artifact_id, DISPOSABLE,
            reason="Resolve retirement refused; safe retry remains",
            event_id=f"resolve-retirement:{artifact_id}:{attempt_id}:refused")
        raise
    transition_resolve_artifact(
        log_path, artifact_id, PURGED,
        reason="Resolve census confirms object is absent",
        event_id=f"resolve-retirement:{artifact_id}:{attempt_id}:purged")
    return {"artifact_id": artifact_id, "receipt": receipt,
            "state": PURGED}


def inventory(project_folder: str) -> dict[str, Any]:
    """Inventory recorded artifacts; report logical and inode-deduplicated bytes."""
    entries = []
    physical_by_inode: dict[tuple[int, int], int] = {}
    by_kind: dict[str, dict[str, int]] = defaultdict(
        lambda: {"artifacts": 0, "logical_bytes": 0, "physical_bytes": 0})
    for artifact_id, fields in _latest_artifacts(project_folder).items():
        identity = fields.get("identity", {})
        if fields.get("identity_type") == "resolve":
            entries.append({**fields, "artifact_id": artifact_id,
                            "identity_matches": None,
                            "ownership_known": bool(fields.get("project_id")
                                                     and fields.get("run_id")
                                                     and fields.get("job_id"))})
            continue
        path = Path(fields["path"])
        try:
            current = FileIdentity.capture(path)
        except (OSError, LifecycleRefused):
            current = None
        recorded = FileIdentity(**identity) if identity else None
        match = bool(recorded and current == recorded)
        kind = fields["kind"]
        row = {**fields, "artifact_id": artifact_id,
               "identity_matches": match,
               "ownership_known": bool(fields.get("project_id")
                                        and fields.get("run_id")
                                        and fields.get("job_id"))}
        entries.append(row)
        if not match:
            continue
        item = by_kind[kind]
        item["artifacts"] += 1
        item["logical_bytes"] += current.size_bytes
        key = (current.device, current.inode)
        is_new_inode = key not in physical_by_inode
        physical_by_inode.setdefault(key, current.size_bytes)
        if is_new_inode:
            item["physical_bytes"] += current.size_bytes
    return {"artifacts": entries,
            "logical_bytes": sum(v["logical_bytes"] for v in by_kind.values()),
            "physical_bytes": sum(physical_by_inode.values()),
            "by_kind": dict(by_kind)}


def schedule_cleanup_job(project_folder: str, *, event_id: str,
                         project_id: str, run_id: str, job_id: str,
                         reason: str,
                         not_before: str = "") -> tuple[dict, bool]:
    """Persist a deduplicated pending cleanup job in the project event log."""
    if not (event_id and project_id and run_id and job_id):
        raise ValueError("cleanup job needs event, project, run and job ids")
    event, created = event_log.emit_once(
        project_folder, CLEANUP_JOB, f"cleanup:{event_id}:queued",
        task=job_id, lifecycle_event_id=event_id, project_id=project_id,
        run_id=run_id, job_id=job_id, reason=reason, state="queued",
        not_before=not_before)
    return {**event, **_event_fields(event)}, created


def pending_cleanup_jobs(project_folder: str) -> list[dict]:
    jobs = _latest_cleanup_jobs(project_folder)
    now = datetime.now(UTC)
    pending = []
    for job in jobs.values():
        if job.get("state") != "queued":
            continue
        not_before = job.get("not_before", "")
        if not not_before:
            pending.append(job)
            continue
        try:
            eligible = datetime.fromisoformat(not_before)
        except ValueError:
            continue
        if eligible.tzinfo is not None and eligible <= now:
            pending.append(job)
    return pending


def incomplete_cleanup_jobs(project_folder: str) -> list[dict]:
    jobs = _latest_cleanup_jobs(project_folder)
    return [job for job in jobs.values()
            if job.get("state") in {"queued", "running"}]


def _latest_cleanup_jobs(project_folder: str) -> dict[str, dict]:
    jobs = {}
    for event in event_log.read_events(project_folder):
        fields = _event_fields(event)
        if event.get("type") != CLEANUP_JOB:
            continue
        event_id = fields.get("lifecycle_event_id")
        if event_id:
            jobs[event_id] = {**fields, "event_host": event.get("host", ""),
                              "event_pid": event.get("pid"),
                              "event_seq": event.get("seq", 0)}
    return jobs


def cleanup_job_refusals(project_folder: str) -> dict[str, int]:
    """How many times each cleanup job has been refused.

    A refusal that is retried on every start is a loop, not a retry. The
    count is what lets the reconciliation stop at a bound and name the
    job as needing a person instead of re-queueing it forever.
    """
    refusals: dict[str, int] = {}
    for event in event_log.read_events(project_folder):
        fields = _event_fields(event)
        if event.get("type") != CLEANUP_JOB:
            continue
        event_id = fields.get("lifecycle_event_id")
        if not event_id:
            continue
        if fields.get("state") == "refused":
            refusals[event_id] = refusals.get(event_id, 0) + 1
    return refusals


def transition_cleanup_job(project_folder: str, job: dict, target: str,
                           *, outcome: str = "") -> None:
    """Append an idempotent job transition, retaining prior states as history."""
    allowed = {"queued": {"running", "refused"},
               "running": {"queued", "complete", "refused"},
               "complete": set(), "refused": {"queued"}}
    current_job = _latest_cleanup_jobs(project_folder).get(
        job["lifecycle_event_id"], job)
    current = current_job["state"]
    if current == target:
        return
    if target not in allowed[current]:
        raise LifecycleRefused(f"invalid cleanup job transition {current} -> {target}")
    job = current_job
    event_id = job["lifecycle_event_id"]
    transition_id = (f"cleanup:{event_id}:{current_job.get('event_seq', 0)}:"
                    f"{target}:{hashlib.sha256(outcome.encode()).hexdigest()[:12]}")
    event_log.emit_once(
        project_folder, CLEANUP_JOB, transition_id,
        task=job["job_id"], lifecycle_event_id=event_id,
        project_id=job["project_id"], run_id=job["run_id"],
        job_id=job["job_id"], reason=job["reason"], state=target,
        outcome=outcome, not_before=job.get("not_before", ""),
        **({"worker_host": socket.gethostname(), "worker_pid": os.getpid()}
           if target == "running" else {}))


def recover_interrupted_cleanup_jobs(project_folder: str) -> int:
    """Return provably dead local workers to QUEUED for manifest re-proof."""
    recovered = 0
    for job in _latest_cleanup_jobs(project_folder).values():
        if job["state"] != "running":
            continue
        host = job.get("worker_host") or job.get("event_host")
        pid = job.get("worker_pid") or job.get("event_pid")
        if host != socket.gethostname() or not isinstance(pid, int) or pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                continue
            transition_cleanup_job(
                project_folder, job, "queued",
                outcome="recovered after interrupted cleanup worker")
            recovered += 1
    return recovered


def _assert_cleanup_path(path: Path, cleanup_root: Path) -> None:
    root = cleanup_root.resolve(strict=True)

    if Path(os.path.abspath(cleanup_root)) != root:
        raise LifecycleRefused(f"cleanup root traverses a symlink: {cleanup_root}")
    candidate = Path(os.path.abspath(path))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LifecycleRefused(f"artifact is outside cleanup root: {path}") from exc
    cursor = root
    for part in candidate.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise LifecycleRefused(f"symlink traversal is protected: {cursor}")


def _assert_git_safe(path: Path, cleanup_root: Path,
                     project_folder: str) -> None:
    """Refuse a cleanup path git tracks or a foreign work tree holds.

    The work tree whose top equals the cleanup root or the project
    folder is Ren-owned (a lifecycle record names it); any other work
    tree is foreign, and a git-tracked path refuses even when owned.
    """
    hit = _git_guard.check(
        path, owned_roots=[str(cleanup_root), str(project_folder)])
    if hit is not None:
        raise LifecycleRefused(_git_guard.describe(hit))


def quarantine_file(project_folder: str, record: ArtifactRecord,
                    cleanup_root: str) -> dict:
    """Move one disposable file into project quarantine under its same id."""
    if not (record.project_id and record.run_id and record.job_id):
        raise LifecycleRefused("unknown artifact ownership is protected")
    if record.pinned or record.dependencies or not record.regenerable:
        raise LifecycleRefused("pinned, referenced, or non-regenerable artifact is protected")
    if run_control.is_running(project_folder):
        raise LifecycleRefused("project activity is still running")
    root = Path(cleanup_root).resolve(strict=True)
    source = Path(record.path)
    declaration = LeaseDeclaration(exclusive=frozenset({
        ResourceRef("project", str(root))}))
    with resource_leases.resource_lease(
            declaration, purpose=f"artifact-quarantine:{record.artifact_id}",
            project=project_folder, wait=False):
        _assert_cleanup_path(source, root)
        _assert_git_safe(source, root, project_folder)
        latest = _latest_artifacts(project_folder).get(record.artifact_id)
        if latest is None or latest.get("state") != DISPOSABLE:
            raise LifecycleRefused("artifact is no longer disposable")
        if (latest.get("path") != str(source.absolute())
                or latest.get("identity") != asdict(record.identity)):
            raise LifecycleRefused("artifact identity changed since storage plan")
        if not record.identity.matches(source):
            raise LifecycleRefused("artifact identity changed since storage plan")
        if record.identity.link_count != 1:
            raise LifecycleRefused(f"hardlinked artifact is protected: {source}")
        from library.tools.project_layout import Area, ProjectLayout

        layout = ProjectLayout(str(root))
        destination = layout.write_path(
            Area.QUARANTINE, "lifecycle", record.artifact_id, source.name)
        if os.path.lexists(destination):
            raise LifecycleRefused(
                f"quarantine destination already exists: {destination}")
        _assert_git_safe(source, root, project_folder)
        os.replace(source, destination)
        try:
            moved_identity = FileIdentity.capture(destination)
            if moved_identity != record.identity:
                raise LifecycleRefused(
                    f"artifact identity changed during quarantine: {destination}")
            transition_artifact(
                project_folder, record.artifact_id, QUARANTINED,
                reason="moved to owned lifecycle quarantine",
                path=str(destination), identity=moved_identity,
                original_path=str(source))
        except Exception:
            if destination.exists() and not source.exists():
                os.replace(destination, source)
            raise
    return {"artifact_id": record.artifact_id, "from": str(source),
            "path": str(destination), "identity": asdict(record.identity)}


def restore_file(project_folder: str, artifact_id: str) -> dict:
    """Restore a quarantined file to its recorded path without overwriting."""
    if run_control.is_running(project_folder):
        raise LifecycleRefused("project activity is still running")
    root = Path(project_folder).resolve(strict=True)
    declaration = LeaseDeclaration(exclusive=frozenset({
        ResourceRef("project", str(root))}))
    with resource_leases.resource_lease(
            declaration, purpose=f"artifact-restore:{artifact_id}",
            project=project_folder, wait=False):
        latest = _latest_artifacts(project_folder).get(artifact_id)
        if latest is None:
            raise LifecycleRefused(f"artifact ownership is unknown: {artifact_id}")
        state = latest.get("state")
        original = latest.get("original_path")
        # A settled quarantine is DISPOSABLE and still sits in quarantine:
        # its grace period expired and the file was not removed. It is
        # restorable, and restoring it is what puts the file back.
        settled = (
            state == DISPOSABLE and original
            and Path(latest["path"]) != Path(original))
        if state == DISPOSABLE and not settled:
            return {"artifact_id": artifact_id, "path": latest["path"],
                    "already_restored": True}
        if state not in {QUARANTINED, DISPOSABLE} or not original:
            raise LifecycleRefused(f"artifact is not restorable: {artifact_id}")
        source = Path(latest["path"])
        destination = Path(latest["original_path"])
        _assert_cleanup_path(source, root)
        _assert_cleanup_path(destination, root)
        identity = FileIdentity(**latest["identity"])
        if not identity.matches(source):
            raise LifecycleRefused(f"quarantined artifact identity changed: {source}")
        if os.path.lexists(destination):
            raise LifecycleRefused(f"restore destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        try:
            restored_identity = FileIdentity.capture(destination)
            if restored_identity != identity:
                raise LifecycleRefused(
                    f"artifact identity changed during restore: {destination}")
            if state == QUARANTINED:
                transition_artifact(
                    project_folder, artifact_id, DISPOSABLE,
                    reason="restored from owned lifecycle quarantine",
                    path=str(destination), identity=restored_identity)
            else:
                rebind_file(
                    project_folder, artifact_id, identity=restored_identity,
                    path=str(destination),
                    reason="restored from an expired lifecycle quarantine")
        except Exception:
            if destination.exists() and not source.exists():
                os.replace(destination, source)
            raise
    return {"artifact_id": artifact_id, "path": str(destination),
            "from": str(source)}


def purge_file(project_folder: str, record: ArtifactRecord,
               cleanup_root: str) -> dict:
    """Delete one disposable artifact under a run lease and identity re-check."""
    if not (record.project_id and record.run_id and record.job_id):
        raise LifecycleRefused("unknown artifact ownership is protected")
    if record.pinned or record.dependencies or not record.regenerable:
        raise LifecycleRefused("pinned, referenced, or non-regenerable artifact is protected")
    if run_control.is_running(project_folder):
        raise LifecycleRefused("project activity is still running")
    path = Path(record.path)
    root = Path(cleanup_root)
    declaration = LeaseDeclaration(exclusive=frozenset({
        ResourceRef("project", str(Path(project_folder).resolve()))}))
    with resource_leases.resource_lease(
            declaration, purpose=f"artifact-purge:{record.artifact_id}",
            project=project_folder, wait=False):
        _assert_cleanup_path(path, root)
        _assert_git_safe(path, root, project_folder)
        registered = _latest_artifacts(project_folder).get(record.artifact_id)
        if registered is None:
            raise LifecycleRefused("artifact ownership is unknown")
        state = registered.get("state", CREATED)
        if state == PURGED:
            return {"artifact_id": record.artifact_id, "path": str(path),
                    "logical_bytes": 0, "physical_bytes": 0,
                    "already_purged": True}
        if state not in {DISPOSABLE, QUARANTINED, PURGING}:
            raise LifecycleRefused(f"artifact lifecycle state is protected: {state}")
        if state != PURGING:
            transition_artifact(project_folder, record.artifact_id, PURGING,
                                reason="guarded deletion started")
        if not path.exists():
            transition_artifact(project_folder, record.artifact_id, PURGED,
                                reason="recovered interrupted deletion")
            return {"artifact_id": record.artifact_id, "path": str(path),
                    "logical_bytes": 0, "physical_bytes": 0,
                    "recovered": True}
        fresh = FileIdentity.capture(path)
        if fresh != record.identity:
            raise LifecycleRefused(f"artifact identity changed before delete: {path}")
        if fresh.link_count != 1:
            transition_artifact(project_folder, record.artifact_id, DISPOSABLE,
                                reason="hardlink guard refused deletion")
            raise LifecycleRefused(f"hardlinked artifact is protected: {path}")
        _assert_git_safe(path, root, project_folder)
        path.unlink()
        transition_artifact(project_folder, record.artifact_id, PURGED,
                            reason="identity rechecked and file removed")
    return {"artifact_id": record.artifact_id, "path": str(path),
            "logical_bytes": fresh.size_bytes, "physical_bytes": fresh.size_bytes}


def stable_artifact_id(project_id: str, run_id: str, job_id: str,
                       kind: str, producer_key: str) -> str:
    """Return a stable opaque id without treating a path as the identity."""
    if not all((project_id, run_id, job_id, kind, producer_key)):
        raise ValueError("stable artifact ids require complete ownership and producer key")
    key = f"{project_id}\0{run_id}\0{job_id}\0{kind}\0{producer_key}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"ren-artifact-{digest}"


def project_activity_lease(function):
    """Fence a complete project run against cleanup's validate-and-delete."""
    @wraps(function)
    def guarded(project_folder, *args, **kwargs):
        project_path = Path(str(project_folder)).expanduser()
        if not project_path.is_dir():
            from library.tools.paths import project_root
            try:
                project_path = project_root(str(project_folder))
            except (FileNotFoundError, ValueError) as exc:
                # Commands such as ``build-reels`` accept a project slug.
                # Resolve that slug through the same registry the command
                # uses before taking its project activity lease.
                from library.tools import project_registry

                try:
                    project = project_registry.get_project(str(project_folder))
                except Exception:  # noqa: BLE001 - preserve the command refusal
                    project = None
                if project is None:
                    raise ValueError(f"Unknown project {project_folder!r}") from exc
                root = getattr(project, "project_root", None)
                if not root:
                    raise LifecycleRefused(
                        f"project registry entry has no root: {project_folder}") from exc
                project_path = Path(root).expanduser()
        project_path = project_path.resolve()
        # A durable job left by a process interruption is resumed the next
        # time Ren enters this project; its lease arbitrates with this run.
        from library.tools import retention
        retention.resume_cleanup_jobs(str(project_path))
        declaration = LeaseDeclaration(exclusive=frozenset({
            ResourceRef("project", str(project_path))}))
        with resource_leases.resource_lease(
                declaration, purpose="project-pipeline-run",
                project=str(project_path), wait=True):
            return function(str(project_path), *args, **kwargs)
    return guarded

"""Ownership, inventory and guarded retirement for Ren artifacts.

The project event log is the durable record. Filesystem paths are storage
locations, not identities; records carry stable artifact ids and the file
identity observed when the artifact was registered. Unknown ownership is
protected. This is the shared contract for project cleanup lanes.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from library.tools import event_log, resource_leases, run_control
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
    DISPOSABLE: {QUARANTINED, PURGED},
    QUARANTINED: {DISPOSABLE, PURGING},
    PURGING: {PURGED, DISPOSABLE},
    PURGED: set(),
}
ARTIFACT_REGISTERED = "artifact_registered"
ARTIFACT_TRANSITION = "artifact_lifecycle_transition"
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
    def capture(cls, path: str | os.PathLike) -> "FileIdentity":
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
    retention_class: str
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
    latest = {}
    for event in event_log.read_events(project_folder):
        fields = _event_fields(event)
        artifact_id = fields.get("artifact_id")
        if not artifact_id:
            continue
        if event.get("type") == ARTIFACT_REGISTERED:
            latest[artifact_id] = fields
        elif event.get("type") == ARTIFACT_TRANSITION and artifact_id in latest:
            latest[artifact_id] = {**latest[artifact_id], **fields}
    return latest


def register_artifact(project_folder: str, record: ArtifactRecord) -> bool:
    """Record an artifact once; missing ownership remains explicitly unknown."""
    if not (record.project_id and record.run_id and record.job_id):
        raise LifecycleRefused("unknown artifact ownership is protected")
    payload = asdict(record)
    payload["state"] = CREATED
    _, created = event_log.emit_once(
        project_folder, ARTIFACT_REGISTERED, record.artifact_id,
        task=record.job_id, **payload)
    return created


def transition_artifact(project_folder: str, artifact_id: str, target: str,
                        *, reason: str, event_id: str = "") -> bool:
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
    event_log.emit_once(project_folder, ARTIFACT_TRANSITION, stable_event_id,
                        task=current["job_id"], artifact_id=artifact_id,
                        state=target, previous_state=old, reason=reason)
    return True


def inventory(project_folder: str) -> dict[str, Any]:
    """Inventory recorded artifacts; report logical and inode-deduplicated bytes."""
    entries = []
    physical_by_inode: dict[tuple[int, int], int] = {}
    by_kind: dict[str, dict[str, int]] = defaultdict(
        lambda: {"artifacts": 0, "logical_bytes": 0, "physical_bytes": 0})
    for artifact_id, fields in _latest_artifacts(project_folder).items():
        identity = fields.get("identity", {})
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
                         reason: str) -> tuple[dict, bool]:
    """Persist a deduplicated pending cleanup job in the project event log."""
    if not (event_id and project_id and run_id and job_id):
        raise ValueError("cleanup job needs event, project, run and job ids")
    return event_log.emit_once(
        project_folder, CLEANUP_JOB, f"cleanup:{event_id}:queued",
        task=job_id, lifecycle_event_id=event_id, project_id=project_id,
        run_id=run_id, job_id=job_id, reason=reason, state="queued")


def pending_cleanup_jobs(project_folder: str) -> list[dict]:
    jobs = {}
    for event in event_log.read_events(project_folder):
        fields = _event_fields(event)
        if event.get("type") != CLEANUP_JOB:
            continue
        event_id = fields.get("lifecycle_event_id")
        if event_id:
            jobs[event_id] = fields
    return [job for job in jobs.values() if job.get("state") == "queued"]


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
    digest = hashlib.sha256("\0".join((project_id, run_id, job_id, kind,
                                         producer_key)).encode("utf-8")).hexdigest()
    return f"ren-artifact-{digest}"

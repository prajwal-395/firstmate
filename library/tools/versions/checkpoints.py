"""Resolve-native project and timeline checkpoints.

Checkpoint bytes live in the project's ignored scratch area. A small
JSON ledger entry lives in the version store's review area so the
artifact's identity and digest travel with Ren's version history.
All Resolve mutations are called by resolve_axi while it holds the
instance lease and runs through the broker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path


def _paths(project_folder: str) -> tuple[Path, Path]:
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    scratch = layout.write_dir(Area.SCRATCH) / "resolve_checkpoints"
    scratch.mkdir(parents=True, exist_ok=True)
    ledger = layout.write_dir(Area.REVIEW) / "resolve_checkpoints.json"
    return scratch, ledger


def _entries(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data["checkpoints"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _record(path: Path, entry: dict) -> None:
    rows = _entries(path)
    if not any(row.get("id") == entry["id"] for row in rows):
        rows.append(entry)
    payload = json.dumps({"schema": 1, "checkpoints": rows}, indent=2,
                         sort_keys=True) + "\n"
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix="checkpoint-",
                                  suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-") or "project"


def project_checkpoint(manager, project, project_folder: str,
                       checkpoint_id: str) -> dict:
    """Export the current exact project, verify it, hash it, then ledger it."""
    if not checkpoint_id.strip():
        raise ValueError("checkpoint id must not be empty")
    scratch, ledger = _paths(project_folder)
    rows = _entries(ledger)
    existing = next((row for row in rows
                     if row.get("id") == checkpoint_id
                     and row.get("kind") == "project"), None)
    if existing:
        artifact = Path(existing["path"])
        if artifact.is_file() and hashlib.sha256(artifact.read_bytes()).hexdigest() == existing["sha256"]:
            return {**existing, "idempotent": True}
        raise RuntimeError("checkpoint id already exists but its artifact is missing or changed")
    fd, staged = tempfile.mkstemp(prefix="project-checkpoint-", suffix=".drp",
                                  dir=scratch)
    os.close(fd)
    os.unlink(staged)  # Resolve requires a fresh, nonexistent destination.
    try:
        exported = manager.ExportProject(project.GetName(), staged)
        artifact = Path(staged)
        if exported is not True or not artifact.is_file() or artifact.stat().st_size == 0:
            raise RuntimeError(f"Resolve project export failed or was empty: {exported!r}")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        final = scratch / f"{_safe_name(project.GetName())}-{_safe_name(checkpoint_id)}.drp"
        if final.exists():
            raise FileExistsError(f"checkpoint destination already exists: {final}")
        os.replace(artifact, final)
        entry = {"id": checkpoint_id, "kind": "project", "project": project.GetName(),
                 "path": str(final), "sha256": digest, "bytes": final.stat().st_size,
                 "created_at": int(time.time())}
        _record(ledger, entry)
        return {**entry, "idempotent": False}
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass


def restore_project(manager, project_folder: str, checkpoint_id: str,
                    new_name: str) -> dict:
    """Import a checkpoint only under a previously unused project name."""
    if not new_name.strip():
        raise ValueError("restore requires a new project name")
    scratch, ledger = _paths(project_folder)
    entry = next((row for row in _entries(ledger)
                  if row.get("id") == checkpoint_id and row.get("kind") == "project"), None)
    if entry is None:
        raise FileNotFoundError(f"project checkpoint not found: {checkpoint_id}")
    artifact = Path(entry["path"])
    if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != entry["sha256"]:
        raise RuntimeError("checkpoint artifact is missing or failed its SHA-256 check")
    names = manager.GetProjectListInCurrentFolder() or []
    if new_name in names:
        raise FileExistsError(f"project {new_name!r} already exists; restore never replaces projects")
    imported = manager.ImportProject(str(artifact), new_name)
    if not imported:
        raise RuntimeError("Resolve refused project import")
    return {"checkpoint_id": checkpoint_id, "project": new_name, "restored": True}


def timeline_checkpoint(project, timeline, project_folder: str,
                       checkpoint_id: str, resolve) -> dict:
    """Idempotently duplicate a timeline and export that copy as a .drt."""
    if not checkpoint_id.strip():
        raise ValueError("checkpoint id must not be empty")
    scratch, ledger = _paths(project_folder)
    rows = _entries(ledger)
    old = next((row for row in rows
                if row.get("id") == checkpoint_id
                and row.get("kind") == "timeline"), None)
    if old:
        path = Path(old["path"])
        if (path.is_file() and path.stat().st_size
                and hashlib.sha256(path.read_bytes()).hexdigest() == old["sha256"]
                and old["timeline"] in _timeline_names(project)):
            return {**old, "idempotent": True}
        raise RuntimeError("timeline checkpoint id exists but its duplicate or export is missing")
    source_name = timeline.GetName()
    digest = hashlib.sha256(f"{project.GetName()}\0{source_name}\0{checkpoint_id}".encode()).hexdigest()[:10]
    duplicate_name = f"{source_name} - checkpoint {digest}"
    if duplicate_name in _timeline_names(project):
        raise FileExistsError(f"checkpoint timeline already exists without a ledger entry: {duplicate_name}")
    duplicate = timeline.DuplicateTimeline(duplicate_name)
    if not duplicate or duplicate_name not in _timeline_names(project):
        raise RuntimeError("Resolve did not create the named checkpoint timeline")
    from library.tools.versions import store

    exported = store.export_timeline_recovery_archive(
        resolve, duplicate, duplicate_name, project_folder,
        label=f"checkpoint {checkpoint_id}")
    if not exported.get("archived"):
        raise RuntimeError(exported.get("reason", "Resolve .drt export failed"))
    final = Path(exported["path"])
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    entry = {"id": checkpoint_id, "kind": "timeline", "project": project.GetName(),
             "source_timeline": source_name, "timeline": duplicate_name,
             "path": str(final), "sha256": digest, "bytes": final.stat().st_size,
             "created_at": int(time.time())}
    _record(ledger, entry)
    return {**entry, "idempotent": False}


def _timeline_names(project) -> list[str]:
    return [project.GetTimelineByIndex(i).GetName()
            for i in range(1, project.GetTimelineCount() + 1)]

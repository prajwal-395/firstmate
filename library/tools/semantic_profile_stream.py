"""Durable per-clip handoff from semantic analysis to temporal indexing.

The capability output remains the semantic step's final aggregate. This
sidecar lets temporal indexing use a completed clip while semantic analysis
continues with the remaining clips.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from library.tools.project_layout import Area, ProjectLayout

SCHEMA_VERSION = 1
STREAM_DIRECTORY = "temporal_profile_stream_v1"
POLL_INTERVAL_S = 0.25


class StreamRecordError(ValueError):
    """A final stream record exists but violates its declared contract."""


def _record_path(layout: ProjectLayout, clip_id: str, *, write: bool) -> Path:
    if (not clip_id or clip_id in (".", "..")
            or "/" in clip_id or "\\" in clip_id):
        raise ValueError(f"Unsafe semantic stream clip_id: {clip_id!r}")
    parts = (STREAM_DIRECTORY, f"{clip_id}.json")
    if write:
        return layout.write_path(
            Area.VISION_ANALYSIS, *parts, step="semantic_analysis")
    return layout.read_path(Area.VISION_ANALYSIS, *parts)


def catalog_source_index(clip_catalog: list) -> tuple[dict, dict, dict]:
    """Build exact-path and unambiguous-stem joins to catalog clip ids."""
    by_path = {}
    by_stem = {}
    by_id_path = {}
    for clip in clip_catalog:
        clip_id = clip["clip_id"]
        source_path = clip["path"]
        real_path = os.path.realpath(source_path)
        prior_path = by_id_path.get(clip_id)
        if prior_path is not None and prior_path != real_path:
            raise ValueError(
                f"Catalog clip_id {clip_id!r} names both {prior_path!r} "
                f"and {real_path!r}")
        by_id_path[clip_id] = real_path
        previous = by_path.get(real_path)
        if previous is not None and previous != clip_id:
            raise ValueError(
                f"Catalog source path {source_path!r} belongs to both "
                f"{previous!r} and {clip_id!r}")
        by_path[real_path] = clip_id
        stem = Path(source_path).stem.casefold()
        by_stem.setdefault(stem, set()).add(clip_id)
    return by_path, by_stem, by_id_path


def clip_for_profile(profile: dict, filename: str,
                     source_index: tuple[dict, dict, dict]
                     ) -> tuple[str, str] | None:
    """Join a profile to the catalog by path, then by a unique source stem."""
    by_path, _, by_id_path = source_index
    has_source_path = False
    for key in ("file_path", "path", "source_file"):
        source_path = profile.get(key)
        if source_path:
            has_source_path = True
            clip_id = by_path.get(os.path.realpath(source_path))
            if clip_id:
                return clip_id, by_id_path[clip_id]
    if has_source_path:
        return None

    stem = Path(filename).stem
    stem = stem.removeprefix("clip_profile_")
    stem = stem.removesuffix("_v3")
    matches = source_index[1].get(stem.casefold(), set())
    if len(matches) > 1:
        raise ValueError(
            f"Semantic profile {filename!r} has no exact catalog path and "
            f"its source stem {stem!r} matches multiple clip ids: "
            f"{sorted(matches)}")
    if not matches:
        return None
    clip_id = next(iter(matches))
    return clip_id, by_id_path[clip_id]


def _profile_digest(profile: dict) -> str:
    encoded = json.dumps(
        profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atomic_write_record(target: Path, record: dict) -> Path:
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return target


def publish_record(project_folder: str, *, clip_id: str, source_path: str,
                   source_stem: str, profile: dict, run_id: str,
                   producer_code_hash: str) -> Path:
    """Atomically publish one complete semantic profile for a catalog clip."""
    layout = ProjectLayout(project_folder)
    target = _record_path(layout, clip_id, write=True)
    record = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "clip_id": clip_id,
        "source_stem": source_stem,
        "source_path": os.path.realpath(source_path),
        "run_id": run_id,
        "producer_code_hash": producer_code_hash,
        "profile_sha256": _profile_digest(profile),
        "profile": profile,
    }
    return _atomic_write_record(target, record)


def publish_missing_record(project_folder: str, *, clip_id: str,
                           source_path: str, source_stem: str, run_id: str,
                           producer_code_hash: str, reason: str) -> Path:
    """Publish this run's terminal result when one clip has no profile."""
    layout = ProjectLayout(project_folder)
    target = _record_path(layout, clip_id, write=True)
    return _atomic_write_record(target, {
        "schema_version": SCHEMA_VERSION,
        "status": "missing",
        "clip_id": clip_id,
        "source_stem": source_stem,
        "source_path": os.path.realpath(source_path),
        "run_id": run_id,
        "producer_code_hash": producer_code_hash,
        "reason": reason,
    })


def read_record(project_folder: str, *, clip_id: str, source_path: str,
                run_id: str | None = None) -> dict | None:
    """Read one final record; staged files are never considered readable."""
    path = _record_path(ProjectLayout(project_folder), clip_id, write=False)
    try:
        with path.open(encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        raise StreamRecordError(f"Semantic stream record {path} is not an object")
    if record.get("schema_version") != SCHEMA_VERSION:
        return None
    if record.get("clip_id") != clip_id:
        raise StreamRecordError(
            f"Semantic stream record {path} names clip_id "
            f"{record.get('clip_id')!r}, expected {clip_id!r}")
    if record.get("source_path") != os.path.realpath(source_path):
        return None
    if run_id is not None and record.get("run_id") != run_id:
        return None
    if record.get("status") == "missing":
        return record
    if record.get("status") != "complete":
        raise StreamRecordError(
            f"Semantic stream record {path} has invalid status "
            f"{record.get('status')!r}")
    profile = record.get("profile")
    if not isinstance(profile, dict):
        raise StreamRecordError(
            f"Semantic stream record {path} has no profile object")
    if record.get("profile_sha256") != _profile_digest(profile):
        raise StreamRecordError(
            f"Semantic stream record {path} failed its profile digest")
    return record


def current_run_context(project_folder: str) -> dict:
    """Read coordinator-owned producer readiness for this project run."""
    from library.tools import run_control

    status = run_control.read_run_status(project_folder)
    context = status.get("semantic_profile_stream")
    if context is None:
        expected = (
            status.get("status") == "running"
            and "semantic_analysis" in status.get("steps_to_run", []))
        return {"run_id": "", "expected": expected, "status": "pending"}
    context = context or {}
    return {
        "run_id": context.get("run_id", ""),
        "expected": bool(context.get("expected", False)),
        "status": context.get("status", "idle"),
    }


def wait_for_record(project_folder: str, *, clip_id: str, source_path: str,
                    run_id: str, poll_interval_s: float = POLL_INTERVAL_S,
                    status_reader=current_run_context,
                    sleep=time.sleep) -> dict | None:
    """Wait for this run's atomic record or for coordinator terminal state."""
    while True:
        record = read_record(
            project_folder, clip_id=clip_id, source_path=source_path,
            run_id=run_id)
        if record is not None:
            return record
        context = status_reader(project_folder)
        if context["run_id"] != run_id:
            return None
        if context["status"] in {"complete", "failed", "aborted"}:
            return None
        sleep(poll_interval_s)


def read_legacy_profile(project_folder: str, *, clip_id: str,
                        source_path: str,
                        clip_catalog: list | None = None) -> dict | None:
    """Read pre-stream profile caches for temporal-only runs during upgrade."""
    from library.tools.vision_schema_adapter import (
        adapt_semantic_document,
        is_v3_profile,
    )

    layout = ProjectLayout(project_folder)
    stem = Path(source_path).stem
    if clip_catalog is not None:
        same_stem = [
            clip for clip in clip_catalog
            if Path(clip["path"]).stem.casefold() == stem.casefold()
        ]
        if len(same_stem) > 1 and not any(
                clip["clip_id"] == clip_id
                and os.path.realpath(clip["path"])
                == os.path.realpath(source_path)
                for clip in same_stem):
            return None
    candidates = (
        f"clip_profile_{stem}_v3.json",
        f"clip_profile_{stem}.json",
    )
    for filename in candidates:
        path = layout.read_path(Area.VISION_ANALYSIS, filename)
        try:
            with path.open(encoding="utf-8") as handle:
                profile = json.load(handle)
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as exc:
            raise StreamRecordError(
                f"Cached semantic profile {path} is not valid JSON") from exc
        if not isinstance(profile, dict):
            raise StreamRecordError(
                f"Cached semantic profile {path} is not an object")
        if not (is_v3_profile(profile) or any(
                key in profile for key in (
                    "analysis", "visual_description", "description",
                    "summary"))):
            continue
        profile_path = profile.get("file_path") or profile.get("path")
        if profile_path and os.path.realpath(profile_path) != os.path.realpath(
                source_path):
            continue
        if (not profile_path and clip_catalog is not None
                and len(same_stem) > 1):
            continue
        document = (adapt_semantic_document(profile)
                    if is_v3_profile(profile) else profile)
        document["clip_id"] = path.name.replace(
            "clip_profile_", "").replace(".json", "")
        return document
    return None

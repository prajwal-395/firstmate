"""Content-addressed incremental store for derived video representations.

TMP-041: every derived representation must be incrementally rebuildable.
A cache entry is keyed by what produced it - source identity, requested
interval, model identity, precision and the adapter code version - so a
missing or stale entry is rebuilt for exactly its own interval, never by
re-running the whole source. The store holds no footage and no creative
judgement, only measured tensors with their provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

ADAPTER_VERSION = "video-features-cache-v1"


class CacheMiss(Exception):
    """No usable cached representation exists for this exact request."""


def cache_key(*, source_path: str, byte_size: int, mtime_ns: int,
              start: float, end: float, model_id: str,
              precision: str) -> str:
    """Stable key for one interval's derived representation."""
    canonical = json.dumps({
        "adapter": ADAPTER_VERSION,
        "source_path": source_path,
        "byte_size": byte_size,
        "mtime_ns": mtime_ns,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "model_id": model_id,
        "precision": precision,
    }, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def entry_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.npz"


def store(cache_dir: Path, key: str, arrays: dict[str, np.ndarray],
          provenance: dict, *, telemetry_project: str | None = None,
          regeneration_seconds: float | None = None,
          artifact_id: str = "",
          lifecycle_owner: dict | None = None) -> Path:
    """Write one interval's tensors with provenance; returns the path."""
    if lifecycle_owner:
        required = {"project_id", "run_id", "job_id", "source_available"}
        if not telemetry_project or not required.issubset(lifecycle_owner):
            raise ValueError("cache lifecycle owner is incomplete")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = entry_path(cache_dir, key)
    payload = dict(arrays)
    payload["__provenance__"] = np.array(
        json.dumps(provenance, sort_keys=True))
    from library.tools.cache_lifecycle import cache_file_lease, record_cache_metric
    with cache_file_lease(path, exclusive=True):
        np.savez_compressed(path, **payload)
    if lifecycle_owner:
        from library.tools.cache_lifecycle import register_cache_artifact
        if not telemetry_project:
            raise ValueError("lifecycle registration needs its project folder")
        record = register_cache_artifact(
            telemetry_project, project_id=lifecycle_owner["project_id"],
            run_id=lifecycle_owner["run_id"], job_id=lifecycle_owner["job_id"],
            path=str(path), kind="video_feature_tensor", producer_key=key,
            regeneration_method="video-feature adapter rebuild",
            source_available=lifecycle_owner["source_available"],
            compatible=lifecycle_owner.get("compatible", True),
            pinned=lifecycle_owner.get("pinned", False))
        artifact_id = record.artifact_id
    if (regeneration_seconds is not None
            and (telemetry_project or os.environ.get("REN_EVENT_LOG"))):
        record_cache_metric(
            telemetry_project, task="video-features-cache",
            kind="video_feature_tensor", outcome="regenerated",
            elapsed_seconds=regeneration_seconds,
            bytes_used=path.stat().st_size, artifact_id=artifact_id)
    return path


def load(cache_dir: Path, key: str, *,
         telemetry_project: str | None = None, artifact_id: str = ""
         ) -> tuple[dict[str, np.ndarray], dict]:
    """Read one entry; raises CacheMiss when absent or unreadable."""
    path = entry_path(cache_dir, key)
    from library.tools.cache_lifecycle import cache_file_lease
    with cache_file_lease(path):
        return _load_entry(path, key, telemetry_project, artifact_id)


def _load_entry(path: Path, key: str,
                telemetry_project: str | None,
                artifact_id: str) -> tuple[dict[str, np.ndarray], dict]:
    if not path.is_file():
        if telemetry_project or os.environ.get("REN_EVENT_LOG"):
            from library.tools.cache_lifecycle import record_cache_metric
            record_cache_metric(
                telemetry_project, task="video-features-cache",
                kind="video_feature_tensor", outcome="miss",
                artifact_id=artifact_id)
        raise CacheMiss(key)
    try:
        with np.load(path, allow_pickle=False) as stored:
            arrays = {name: stored[name] for name in stored.files
                      if name != "__provenance__"}
            provenance = json.loads(str(stored["__provenance__"]))
    except (OSError, ValueError, KeyError) as exc:
        if telemetry_project or os.environ.get("REN_EVENT_LOG"):
            from library.tools.cache_lifecycle import record_cache_metric
            record_cache_metric(
                telemetry_project, task="video-features-cache",
                kind="video_feature_tensor", outcome="miss",
                artifact_id=artifact_id)
        raise CacheMiss(f"{key}: {exc}") from exc
    if not arrays:
        if telemetry_project or os.environ.get("REN_EVENT_LOG"):
            from library.tools.cache_lifecycle import record_cache_metric
            record_cache_metric(
                telemetry_project, task="video-features-cache",
                kind="video_feature_tensor", outcome="miss",
                artifact_id=artifact_id)
        raise CacheMiss(f"{key}: entry holds no tensors")
    if telemetry_project or os.environ.get("REN_EVENT_LOG"):
        from library.tools.cache_lifecycle import record_cache_metric
        record_cache_metric(
            telemetry_project, task="video-features-cache",
            kind="video_feature_tensor", outcome="hit",
            bytes_used=path.stat().st_size, artifact_id=artifact_id)
    return arrays, provenance


def missing_keys(cache_dir: Path, keys: list[str]) -> list[str]:
    """Which of the requested keys still need a rebuild."""
    return [key for key in keys if not entry_path(cache_dir, key).is_file()]

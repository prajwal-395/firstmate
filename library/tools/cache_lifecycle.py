"""Value-aware policy for lifecycle-registered recomputable cache artifacts.

This module deliberately owns no artifact identity or retention journal:
records and state come from :mod:`artifact_lifecycle`, while cache events
use the project's canonical event log. Unknown ownership or measurements
remain protected.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from library.tools import artifact_lifecycle, event_log, resource_leases
from library.tools import git_guard as _git_guard
from library.tools.resource_leases import LeaseDeclaration, ResourceRef

CACHE_KIND_COST = {
    "decoded_frame": "low",
    "render_preview": "low_medium",
    "video_feature_tensor": "medium",
    "visual_embedding": "medium_high",
    "transcription_alignment": "high",
    "semantic_measurement": "high",
    "native_extension": "high",
}
MODEL_WEIGHT_KINDS = frozenset({"model_weight", "model_checkpoint"})
METRIC_EVENT = "cache_lifecycle_metric"
VALUE_EVENT = "cache_value_measurement"
POLICY_EVENT = "cache_eviction_planned"
LEASE_RESOURCE_PREFIX = "cache-file:"
DEFAULT_BUDGET_BYTES = 20 * 1024**3
DEFAULT_LOW_WATERMARK = 0.80
DEFAULT_HIGH_WATERMARK = 0.90
_CACHE_ARTIFACT_IDS: dict[
    tuple[str, str, str], tuple[str, artifact_lifecycle.FileIdentity]] = {}


class CachePolicyRefused(ValueError):
    """The cache inventory or policy is not safe to act on."""


@contextmanager
def cache_file_lease(path: str | os.PathLike, *, exclusive: bool = False):
    """Fence an active cache reader/writer against lifecycle eviction."""
    resource = ResourceRef("project", LEASE_RESOURCE_PREFIX +
                           os.path.realpath(path))
    declaration = (LeaseDeclaration(exclusive=frozenset({resource}))
                   if exclusive else LeaseDeclaration(read=frozenset({resource})))
    with resource_leases.resource_lease(
            declaration, purpose=f"cache-file:{Path(path).name}", wait=False):
        yield


def cache_file_is_in_use(path: str | os.PathLike) -> bool:
    """Whether an exact cache path currently has a reader or writer."""
    resource = ResourceRef("project", LEASE_RESOURCE_PREFIX +
                           os.path.realpath(path))
    declaration = LeaseDeclaration(exclusive=frozenset({resource}))
    try:
        with resource_leases.resource_lease(
                declaration, purpose=f"cache-probe:{Path(path).name}",
                wait=False, allow_reentrant=False):
            return False
    except resource_leases.ResourceLeaseBusy:
        return True


@dataclass(frozen=True)
class CacheBudget:
    total_bytes: int
    low_watermark: float = DEFAULT_LOW_WATERMARK
    high_watermark: float = DEFAULT_HIGH_WATERMARK
    expensive_bytes: int | None = None
    inexpensive_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.total_bytes <= 0:
            raise ValueError("cache budget must be positive")
        if not 0 <= self.low_watermark < self.high_watermark <= 1:
            raise ValueError("watermarks must satisfy 0 <= low < high <= 1")
        if any(value is not None and value <= 0 for value in
               (self.expensive_bytes, self.inexpensive_bytes)):
            raise ValueError("cache class budgets must be positive")

    @classmethod
    def from_environment(cls) -> CacheBudget:
        try:
            total = int(os.environ.get("REN_CACHE_BUDGET_BYTES",
                                      DEFAULT_BUDGET_BYTES))
            low = float(os.environ.get("REN_CACHE_LOW_WATERMARK",
                                       DEFAULT_LOW_WATERMARK))
            high = float(os.environ.get("REN_CACHE_HIGH_WATERMARK",
                                        DEFAULT_HIGH_WATERMARK))
            expensive = os.environ.get("REN_CACHE_EXPENSIVE_BUDGET_BYTES")
            inexpensive = os.environ.get("REN_CACHE_INEXPENSIVE_BUDGET_BYTES")
        except ValueError as exc:
            raise CachePolicyRefused("invalid cache budget configuration") from exc
        try:
            expensive_bytes = int(expensive) if expensive else None
            inexpensive_bytes = int(inexpensive) if inexpensive else None
        except ValueError as exc:
            raise CachePolicyRefused("invalid cache class budget") from exc
        return cls(total, low, high, expensive_bytes, inexpensive_bytes)


def record_cache_metric(project_folder: str | None = None, *, task: str, kind: str,
                        outcome: str, elapsed_seconds: float = 0.0,
                        bytes_used: int = 0, compatible: bool = True,
                        artifact_id: str = "") -> dict:
    """Record a cache hit, miss or regeneration in the project event log."""
    if kind not in CACHE_KIND_COST or outcome not in {"hit", "miss", "regenerated"}:
        raise ValueError("unknown cache kind or outcome")
    if elapsed_seconds < 0 or bytes_used < 0:
        raise ValueError("cache measurements cannot be negative")
    fields = {"kind": kind, "outcome": outcome,
              "elapsed_seconds": float(elapsed_seconds),
              "bytes_used": int(bytes_used),
              "compatible": bool(compatible),
              "at_epoch": time.time()}
    if artifact_id:
        fields["artifact_id"] = artifact_id
    if project_folder:
        return event_log.emit(project_folder, METRIC_EVENT, task=task, **fields)
    log_path = os.environ.get(event_log.EVENT_LOG_ENV)
    if log_path:
        return event_log.emit_path(log_path, METRIC_EVENT, task=task, **fields)
    return {}


def record_cache_value(project_folder: str, *, artifact_id: str,
                       source_available: bool, compatible: bool,
                       consumer_count: int = 0) -> dict:
    """Record measured compatibility and source availability for an entry."""
    if not artifact_id or consumer_count < 0:
        raise ValueError("cache value needs an artifact id and valid consumers")
    return event_log.emit(
        project_folder, VALUE_EVENT, task="cache-policy",
        artifact_id=artifact_id, source_available=bool(source_available),
        compatible=bool(compatible), consumer_count=int(consumer_count))


def register_cache_artifact(project_folder: str, *, project_id: str,
                            run_id: str, job_id: str, path: str,
                            kind: str, producer_key: str,
                            regeneration_method: str,
                            source_available: bool,
                            compatible: bool = True,
                            pinned: bool = False) -> artifact_lifecycle.ArtifactRecord:
    """Register a completed cache entry through the shared ownership API."""
    if kind not in CACHE_KIND_COST or not regeneration_method:
        raise CachePolicyRefused("cache registration lacks a kind or rebuild method")
    identity = artifact_lifecycle.FileIdentity.capture(path)
    artifact_id = artifact_lifecycle.stable_artifact_id(
        project_id, run_id, job_id, kind, producer_key)
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id=artifact_id, kind=kind, project_id=project_id,
        run_id=run_id, job_id=job_id, path=os.path.abspath(path),
        created_at=datetime.now(UTC).isoformat(),
        identity=identity, retention_class="cache", pinned=pinned,
        regenerable=True, regeneration_method=regeneration_method)
    created = artifact_lifecycle.register_artifact(project_folder, record)
    current = next((row for row in artifact_lifecycle.inventory(
        project_folder)["artifacts"] if row["artifact_id"] == artifact_id), None)
    state = current.get("state") if current else None
    if created or state == artifact_lifecycle.CREATED:
        artifact_lifecycle.transition_artifact(
            project_folder, artifact_id, artifact_lifecycle.COMPLETED,
            reason="cache entry finished writing")
        state = artifact_lifecycle.COMPLETED
    if state == artifact_lifecycle.COMPLETED:
        artifact_lifecycle.transition_artifact(
            project_folder, artifact_id, artifact_lifecycle.CACHEABLE,
            reason="recomputable cache entry registered")
    elif state != artifact_lifecycle.CACHEABLE:
        raise CachePolicyRefused(f"cache artifact is in protected state: {state}")
    record_cache_value(
        project_folder, artifact_id=artifact_id,
        source_available=source_available, compatible=compatible,
        consumer_count=0)
    return record


def register_cache_artifact_from_environment(
        path: str | os.PathLike, *, kind: str, producer_key: str,
        regeneration_method: str, source_available: bool,
        compatible: bool = True) -> artifact_lifecycle.ArtifactRecord | None:
    """Register a cache entry when a pipeline run supplied its project log.

    A cache access outside a known project/run remains unowned and therefore
    ineligible for eviction. Existing exact identities are reused across
    runs, so a cache hit does not invent a new artifact identity.
    """
    log_path = os.environ.get(event_log.EVENT_LOG_ENV)
    if not log_path:
        return None
    from library.tools import run_id

    current_run = run_id.current()
    if not current_run:
        return None
    project_folder = str(Path(log_path).resolve().parents[2])
    target = Path(path).resolve()
    identity = artifact_lifecycle.FileIdentity.capture(target)
    identity_key = (f"{identity.device}:{identity.inode}:"
                    f"{identity.mtime_ns}:{identity.size_bytes}")
    producer_key = f"{producer_key}:{identity_key}"
    cache_key = (project_folder, str(target), kind)
    cached = _CACHE_ARTIFACT_IDS.get(cache_key)
    if cached and cached[1].matches(target):
        record_cache_value(
            project_folder, artifact_id=cached[0],
            source_available=source_available, compatible=compatible,
            consumer_count=0)
        return None
    _CACHE_ARTIFACT_IDS.pop(cache_key, None)
    current = artifact_lifecycle.inventory(project_folder)
    existing = next((row for row in current["artifacts"]
                     if row.get("path") == str(target)
                     and row.get("kind") == kind
                     and row.get("identity_matches")), None)
    if existing:
        record_cache_value(
            project_folder, artifact_id=existing["artifact_id"],
            source_available=source_available, compatible=compatible,
            consumer_count=0)
        _CACHE_ARTIFACT_IDS[cache_key] = (
            existing["artifact_id"],
            artifact_lifecycle.FileIdentity(**existing["identity"]))
        return None
    record = register_cache_artifact(
        project_folder, project_id=project_folder, run_id=current_run,
        job_id=f"cache-producer:{kind}", path=str(target), kind=kind,
        producer_key=producer_key, regeneration_method=regeneration_method,
        source_available=source_available, compatible=compatible)
    _CACHE_ARTIFACT_IDS[cache_key] = (record.artifact_id, record.identity)
    return record


def cache_artifact_id_from_environment(path: str | os.PathLike,
                                       kind: str) -> str:
    """Resolve an already registered cache identity without guessing."""
    log_path = os.environ.get(event_log.EVENT_LOG_ENV)
    if not log_path:
        return ""
    project_folder = str(Path(log_path).resolve().parents[2])
    target = str(Path(path).resolve())
    key = (project_folder, target, kind)
    cached = _CACHE_ARTIFACT_IDS.get(key)
    if cached and cached[1].matches(target):
        return cached[0]
    _CACHE_ARTIFACT_IDS.pop(key, None)
    if key not in _CACHE_ARTIFACT_IDS:
        rows = artifact_lifecycle.inventory(project_folder)["artifacts"]
        row = next((item for item in rows if item.get("path") == target
                    and item.get("kind") == kind
                    and item.get("identity_matches")), None)
        if row:
            _CACHE_ARTIFACT_IDS[key] = (
                row["artifact_id"],
                artifact_lifecycle.FileIdentity(**row["identity"]))
    return _CACHE_ARTIFACT_IDS.get(key, ("", None))[0]


def cache_metrics(project_folder: str) -> dict[str, dict[str, float | int]]:
    """Summarize observed hits, misses and measured regeneration time."""
    totals: dict[str, dict[str, float | int]] = {}
    for event in event_log.read_events(project_folder):
        if event.get("type") != METRIC_EVENT:
            continue
        fields = event.get("fields", {})
        kind, outcome = fields.get("kind"), fields.get("outcome")
        if kind not in CACHE_KIND_COST or outcome not in {
                "hit", "miss", "regenerated"}:
            continue
        item = totals.setdefault(kind, {"hits": 0, "misses": 0,
                                        "regenerations": 0,
                                        "regeneration_seconds": 0.0})
        item[{"hit": "hits", "miss": "misses",
              "regenerated": "regenerations"}[outcome]] += 1
        if outcome == "regenerated":
            item["regeneration_seconds"] += float(
                fields.get("elapsed_seconds", 0.0))
    for item in totals.values():
        requests = item["hits"] + item["misses"]
        item["hit_rate"] = item["hits"] / requests if requests else 0.0
    return totals


def _retention_value(row: dict[str, Any]) -> float:
    """Higher means retain longer; missing value signals unknown, not zero."""
    required = ("last_access_epoch", "reuse_count", "recompute_seconds",
                "consumer_count", "source_available", "compatible")
    if any(key not in row for key in required):
        raise CachePolicyRefused("cache value measurements are incomplete")
    if row["recompute_seconds"] < 0 or row["reuse_count"] < 0:
        raise CachePolicyRefused("cache value measurements are invalid")
    if row["consumer_count"] or not row["source_available"]:
        return float("inf")
    if not row["compatible"]:
        return -1.0
    size = int(row.get("identity", {}).get("size_bytes", 0))
    if size <= 0:
        raise CachePolicyRefused("cache size is unknown")
    # Recency tempers stale popularity; actual measured compute and reuse
    # dominate, and size makes the choice aware of reclaimed space.
    age_days = max(0.0, (row["now_epoch"] - row["last_access_epoch"]) / 86400)
    return ((1 + row["reuse_count"]) * (1 + row["recompute_seconds"])
            / size) / (1 + age_days)


def plan_eviction(project_folder: str, *, budget: CacheBudget | None = None,
                  now_epoch: float | None = None) -> dict:
    """Return a deterministic, non-destructive plan over registered caches.

    Only identity-verified CACHEABLE entries with explicit cache measurements
    can be selected. Invalid/incompatible entries are considered first.
    The returned rows must still be applied through artifact_lifecycle.
    """
    from time import time

    budget = budget or CacheBudget.from_environment()
    now = float(time() if now_epoch is None else now_epoch)
    inventory = artifact_lifecycle.inventory(project_folder)
    candidates = []
    valid_bytes = 0
    class_bytes: dict[str, int] = {"expensive": 0, "inexpensive": 0}
    events = event_log.read_events(project_folder)
    measured_values = {}
    metrics_by_id: dict[str, list[dict]] = {}
    for event in events:
        fields = event.get("fields", {})
        artifact_id = fields.get("artifact_id")
        if event.get("type") == VALUE_EVENT and artifact_id:
            measured_values[artifact_id] = fields
        elif event.get("type") == METRIC_EVENT and artifact_id:
            metrics_by_id.setdefault(artifact_id, []).append(fields)
    for row in inventory["artifacts"]:
        kind = row.get("kind")
        if kind in MODEL_WEIGHT_KINDS or kind not in CACHE_KIND_COST:
            continue
        if row.get("state") != artifact_lifecycle.CACHEABLE:
            continue
        if not row.get("ownership_known") or not row.get("identity_matches"):
            continue
        if row.get("pinned") or row.get("dependencies"):
            continue
        usage = metrics_by_id.get(row["artifact_id"], [])
        cache_value = measured_values.get(row["artifact_id"], {})
        hits = sum(item.get("outcome") == "hit" for item in usage)
        regeneration_seconds = sum(
            float(item.get("elapsed_seconds", 0.0)) for item in usage
            if item.get("outcome") == "regenerated")
        last_access = max((float(item.get("at_epoch", 0.0))
                           for item in usage),
                          default=None)
        metric_compatibility = [item["compatible"] for item in usage
                                if "compatible" in item]
        enriched = {**row, **cache_value, "now_epoch": now,
                    "reuse_count": hits,
                    "recompute_seconds": regeneration_seconds,
                    "last_access_epoch": last_access,
                    "compatible": (metric_compatibility[-1]
                                   if metric_compatibility
                                   else cache_value.get("compatible", True)),
                    "consumer_count": max(
                        int(cache_value.get("consumer_count", 0)),
                        int(cache_file_is_in_use(row["path"]))) }
        if last_access is None:
            raise CachePolicyRefused(
                f"cache access history is unknown: {row['artifact_id']}")
        value = _retention_value(enriched)
        if row["consumer_count"] or not row["source_available"]:
            continue
        if int(row["identity"].get("link_count", 0)) != 1:
            continue
        if cache_file_is_in_use(row["path"]):
            continue
        if kind == "native_extension" and cache_file_is_in_use(
                Path(row["path"]).parent):
            continue
        enriched["retention_value"] = value
        candidates.append(enriched)
        if value >= 0:
            size = int(row["identity"]["size_bytes"])
            valid_bytes += size
            bucket = ("expensive" if CACHE_KIND_COST[kind] in
                      {"medium_high", "high"} else "inexpensive")
            class_bytes[bucket] += size

    high = int(budget.total_bytes * budget.high_watermark)
    low = int(budget.total_bytes * budget.low_watermark)
    target = low if valid_bytes > high else valid_bytes
    class_limits = {
        "expensive": budget.expensive_bytes,
        "inexpensive": budget.inexpensive_bytes,
    }
    class_high = {name: (int(limit * budget.high_watermark)
                         if limit is not None else None)
                  for name, limit in class_limits.items()}
    class_low = {name: (int(limit * budget.low_watermark)
                        if limit is not None else None)
                 for name, limit in class_limits.items()}
    class_targets = {
        name: (class_low[name] if class_high[name] is not None
               and class_bytes[name] > class_high[name] else class_bytes[name])
        for name in class_bytes
    }
    candidates.sort(key=lambda r: (
        r["retention_value"] >= 0,
        r["retention_value"],
        r["last_access_epoch"],
        r["artifact_id"],
    ))
    # Invalid entries do not count toward the budget and are removed first,
    # even when the valid cache is below its high watermark.
    selected, reclaimed = [], 0
    reclaimed_by_class = {"expensive": 0, "inexpensive": 0}
    for row in candidates:
        invalid = row["retention_value"] < 0
        bucket = ("expensive" if CACHE_KIND_COST[row["kind"]] in
                  {"medium_high", "high"} else "inexpensive")
        over_global = reclaimed >= max(0, valid_bytes - target)
        class_excess = {name: max(0, class_bytes[name] - class_targets[name])
                        for name in class_bytes}
        any_class_over = any(reclaimed_by_class[name] < class_excess[name]
                             for name in class_bytes)
        over_class = reclaimed_by_class[bucket] < class_excess[bucket]
        if not invalid and not over_global and not any_class_over:
            break
        if not invalid and not over_global and not over_class:
            continue
        selected.append({"artifact_id": row["artifact_id"],
                         "kind": row["kind"],
                         "path": row["path"],
                         "size_bytes": row["identity"]["size_bytes"],
                         "reason": "incompatible" if invalid
                         else "lowest measured retention value"})
        if not invalid:
            size = int(row["identity"]["size_bytes"])
            reclaimed += size
            reclaimed_by_class[bucket] += size
    plan = {"budget_bytes": budget.total_bytes,
            "low_watermark_bytes": low,
            "high_watermark_bytes": high,
            "class_budgets_bytes": class_limits,
            "class_cacheable_bytes": class_bytes,
            "class_low_watermark_bytes": class_low,
            "class_high_watermark_bytes": class_high,
            "cacheable_bytes": valid_bytes,
            "selected": selected,
            "estimated_reclaimed_bytes": reclaimed,
            "refused_git": []}
    _collectable, _refused = _git_guard.partition(
        [row["path"] for row in selected],
        owned_roots=[str(Path(project_folder).resolve())])
    if _refused:
        _refused_by_path = {hit.path: hit for hit in _refused}
        plan["refused_git"] = [
            {"artifact_id": row["artifact_id"], "path": row["path"],
             "resolved_path": _refused_by_path[row["path"]].resolved_path,
             "reason": _refused_by_path[row["path"]].reason,
             "work_tree": _refused_by_path[row["path"]].work_tree,
             "detail": _git_guard.describe(_refused_by_path[row["path"]])}
            for row in selected if row["path"] in _refused_by_path]
        plan["selected"] = [
            row for row in selected if row["path"] not in _refused_by_path]
        plan["estimated_reclaimed_bytes"] = sum(
            int(row["size_bytes"]) for row in plan["selected"]
            if row.get("reason") != "incompatible")
    plan["plan_id"] = hashlib.sha256(json.dumps(
        plan, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    event_log.emit(project_folder, POLICY_EVENT, task="cache-policy",
                   **plan)
    return plan


def apply_eviction(project_folder: str, plan: dict, *, cleanup_root: str,
                   records: dict[str, artifact_lifecycle.ArtifactRecord],
                   cleanup_roots: dict[str, str] | None = None) -> list[dict]:
    """Apply an explicit plan through the shared guarded deletion contract."""
    outcomes = []
    plan_id = plan.get("plan_id")
    if not plan_id:
        raise CachePolicyRefused("eviction plan has no recorded identity")
    recorded = any(
        event.get("type") == POLICY_EVENT
        and event.get("fields", {}).get("plan_id") == plan_id
        and event.get("fields", {}).get("selected") == plan.get("selected")
        for event in event_log.read_events(project_folder))
    if not recorded:
        raise CachePolicyRefused("eviction plan is not present in the event log")
    selected_ids = [row["artifact_id"] for row in plan.get("selected", [])]
    if len(selected_ids) != len(set(selected_ids)):
        raise CachePolicyRefused("eviction plan repeats an artifact identity")
    planned_rows = {row["artifact_id"]: row
                    for row in plan.get("selected", [])}
    current_rows = {row["artifact_id"]: row for row in
                    artifact_lifecycle.inventory(project_folder)["artifacts"]}
    for artifact_id in selected_ids:
        record = records.get(artifact_id)
        if record is None or record.kind not in CACHE_KIND_COST:
            raise CachePolicyRefused(f"registered cache record missing: {artifact_id}")
        planned = planned_rows[artifact_id]
        current = current_rows.get(artifact_id)
        if current is not None and current.get("state") == artifact_lifecycle.PURGED:
            outcomes.append({"artifact_id": artifact_id, "path": record.path,
                             "logical_bytes": 0, "physical_bytes": 0,
                             "already_purged": True})
            continue
        if (current is None or not current.get("identity_matches")
                or planned.get("path") != record.path
                or current.get("path") != record.path
                or record.kind != current.get("kind")
                or record.identity.__dict__ != current.get("identity")):
            raise CachePolicyRefused(
                f"cache identity changed since plan: {artifact_id}")
        hit = _git_guard.check(
            record.path, owned_roots=[str(Path(project_folder).resolve()),
                                      str(Path(cleanup_root).resolve())])
        if hit is not None:
            raise CachePolicyRefused(_git_guard.describe(hit))
        lease_path = (Path(record.path).parent
                      if record.kind == "native_extension" else record.path)
        with cache_file_lease(lease_path, exclusive=True):
            artifact_lifecycle.transition_artifact(
                project_folder, artifact_id, artifact_lifecycle.DISPOSABLE,
                reason="selected by value-aware cache budget plan")
            outcomes.append(artifact_lifecycle.purge_file(
                project_folder, record,
                (cleanup_roots or {}).get(artifact_id, cleanup_root)))
    return outcomes

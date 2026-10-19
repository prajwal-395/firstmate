"""Public storage accounting and manifest guarded lifecycle operations.

Inventory is read from the ownership event logs, never reconstructed by
walking artifact directories. Unknown or changed identities are reported
as protected and cannot enter an apply manifest.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from library.tools import artifact_lifecycle as lifecycle

DEFAULT_FREE_RESERVE_BYTES = 10 * 1024**3
PROTECTED_SUFFIXES = frozenset({".drt", ".drp", ".otio"})
PROTECTED_KINDS = frozenset({"model_weight", "model_weights", "source_footage",
                             "user_asset", "delivered_render", "signed_off_render"})


def _assert_plan_binding(item: dict, record: dict) -> None:
    """Refuse a manifest whose exact planned path or identity was edited."""
    planned_identity = item.get("identity") or {}
    current_identity = record.get("identity") or {}
    if (str(Path(item.get("path", "")).absolute())
            != str(Path(record["path"]).absolute())
            or planned_identity != current_identity):
        raise lifecycle.LifecycleRefused(
            f"storage manifest no longer matches artifact identity: {item['artifact_id']}")


def pressure(free_bytes: int, reserve_bytes: int | None = None) -> str:
    """Classify free space against the configurable reserve."""
    reserve = (reserve_bytes if reserve_bytes is not None else
               int(os.environ.get("REN_MIN_FREE_BYTES",
                                 DEFAULT_FREE_RESERVE_BYTES)))
    if reserve <= 0:
        raise ValueError("REN_MIN_FREE_BYTES must be positive")
    if free_bytes < reserve:
        return "critical"
    if free_bytes < reserve * 1.5:
        return "elevated"
    return "normal"


def status(projects: list[str], *, disk_path: str | None = None) -> dict:
    """Summarize registered lifecycle artifacts and current disk pressure."""
    entries = []
    by_kind: dict[str, dict[str, int]] = {}
    seen: set[tuple[int, int]] = set()
    logical = physical = reclaimable = 0
    active_bytes = protected_bytes = 0
    for project in projects:
        snapshot = lifecycle.inventory(project)
        for row in snapshot["artifacts"]:
            entries.append({**row, "project": str(Path(project).resolve())})
            if not row["identity_matches"]:
                continue
            identity = row.get("identity") or {}
            size = int(identity.get("size_bytes", 0))
            kind = row["kind"]
            totals = by_kind.setdefault(kind, {"artifacts": 0,
                                                "logical_bytes": 0,
                                                "physical_bytes": 0})
            totals["artifacts"] += 1
            totals["logical_bytes"] += size
            logical += size
            inode = (int(identity["device"]), int(identity["inode"]))
            if inode not in seen:
                seen.add(inode)
                physical += size
                totals["physical_bytes"] += size
            if (row.get("state") == lifecycle.DISPOSABLE
                    and row.get("ownership_known") and not row.get("pinned")
                    and not row.get("dependencies") and row.get("regenerable")):
                eligible_kind = kind not in PROTECTED_KINDS
                eligible_path = Path(row["path"]).suffix.lower() not in PROTECTED_SUFFIXES
                if eligible_kind and eligible_path and identity.get("link_count") == 1:
                    reclaimable += size
            if row.get("state") in {lifecycle.CREATED, lifecycle.ACTIVE,
                                    lifecycle.PURGING}:
                active_bytes += size
            elif row.get("state") not in {lifecycle.DISPOSABLE,
                                          lifecycle.QUARANTINED,
                                          lifecycle.PURGED}:
                protected_bytes += size
    anchor = Path(disk_path or os.environ.get("PIPELINE_PROJECTS_ROOT") or Path.cwd())
    usage = shutil.disk_usage(anchor)
    oldest = sorted((row for row in entries if row.get("ownership_known")
                     and row.get("state") != lifecycle.PURGED),
                    key=lambda row: row.get("created_at", ""))[:10]
    largest = sorted((row for row in entries if row["identity_matches"]
                      and row.get("state") == lifecycle.DISPOSABLE
                      and row.get("regenerable") and not row.get("pinned")
                      and not row.get("dependencies")
                      and row.get("kind") not in PROTECTED_KINDS
                      and Path(row["path"]).suffix.lower() not in PROTECTED_SUFFIXES
                      and int((row.get("identity") or {}).get("link_count", 0)) == 1),
                     key=lambda row: int(row.get("identity", {}).get(
                         "size_bytes", 0)), reverse=True)[:10]
    from library.tools.cache_lifecycle import CACHE_KIND_COST, CacheBudget
    cache_inodes: dict[tuple[int, int], int] = {}
    for row in entries:
        identity = row.get("identity") or {}
        if row["identity_matches"] and row["kind"] in CACHE_KIND_COST:
            key = (int(identity["device"]), int(identity["inode"]))
            cache_inodes.setdefault(key, int(identity["size_bytes"]))
    cache_bytes = sum(cache_inodes.values())
    cache_budget = CacheBudget.from_environment()
    return {"artifacts": entries, "by_kind": by_kind,
            "logical_bytes": logical, "physical_bytes": physical,
            "reclaimable_bytes": reclaimable, "active_bytes": active_bytes,
            "protected_bytes": protected_bytes,
            "oldest_retained": oldest, "largest_candidates": largest,
            "cache_bytes": cache_bytes,
            "cache_budget_bytes": cache_budget.total_bytes,
            "cache_budget_exceeded": cache_bytes > cache_budget.total_bytes,
            "free_bytes": usage.free, "total_bytes": usage.total,
            "disk_pressure": pressure(usage.free)}


def require_free_space(required_bytes: int, *, path: str | None = None) -> dict:
    """Cheap preflight for storage-heavy work; refuse without deleting assets."""
    if required_bytes < 0:
        raise ValueError("required_bytes cannot be negative")
    anchor = Path(path or os.environ.get("PIPELINE_PROJECTS_ROOT") or Path.cwd())
    free = shutil.disk_usage(anchor).free
    reserve = int(os.environ.get("REN_MIN_FREE_BYTES",
                                DEFAULT_FREE_RESERVE_BYTES))
    result = {"required_bytes": required_bytes, "free_bytes": free,
              "reserve_bytes": reserve,
              "disk_pressure": pressure(free, reserve)}
    if free - required_bytes < reserve:
        raise lifecycle.LifecycleRefused(
            f"disk pressure {result['disk_pressure']}: need {required_bytes} "
            f"bytes with {free} free and {reserve} reserved; protected assets "
            "were not changed")
    return result


def plan_document(projects: list[str]) -> dict:
    """Build an exact-identity manifest without writing it."""
    report = status(projects)
    candidates = []
    for row in report["artifacts"]:
        if (row["identity_matches"] and row["ownership_known"]
                and row.get("state") == lifecycle.DISPOSABLE
                and row.get("regenerable") and not row.get("pinned")
                and not row.get("dependencies")
                and int((row.get("identity") or {}).get("link_count", 0)) == 1
                and row.get("kind") not in PROTECTED_KINDS
                and Path(row["path"]).suffix.lower() not in PROTECTED_SUFFIXES):
            candidates.append({key: row[key] for key in
                               ("artifact_id", "project", "path", "identity",
                                "kind", "run_id", "job_id")})
    document = {"format": "ren-storage-plan/1", "candidates": candidates,
                "logical_bytes": sum(row["identity"]["size_bytes"]
                                     for row in candidates)}
    return document


def plan(projects: list[str], *, output: str) -> dict:
    """Write an exact-identity manifest for already-disposable artifacts."""
    document = plan_document(projects)
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return {**document, "manifest": str(path)}


def purge(manifest: str) -> dict:
    """Apply a recorded plan using the lifecycle contract's delete guards."""
    document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if document.get("format") != "ren-storage-plan/1":
        raise lifecycle.LifecycleRefused("unknown storage manifest format")
    results = []
    for item in document["candidates"]:
        project = item["project"]
        latest = lifecycle._latest_artifacts(project).get(item["artifact_id"])
        if latest is None:
            raise lifecycle.LifecycleRefused(
                f"artifact ownership disappeared: {item['artifact_id']}")
        _assert_plan_binding(item, latest)
        if latest.get("state") == lifecycle.PURGED:
            results.append({"artifact_id": item["artifact_id"],
                            "path": latest["path"], "logical_bytes": 0,
                            "physical_bytes": 0, "already_purged": True})
            continue
        if (latest.get("state") != lifecycle.QUARANTINED
                or latest.get("kind") in PROTECTED_KINDS
                or Path(latest["path"]).suffix.lower() in PROTECTED_SUFFIXES):
            raise lifecycle.LifecycleRefused(
                f"artifact is not eligible for permanent purge: {item['artifact_id']}")
        fields = {key: latest[key] for key in lifecycle.ArtifactRecord.__dataclass_fields__}
        fields["identity"] = lifecycle.FileIdentity(**fields["identity"])
        fields["dependencies"] = tuple(fields.get("dependencies") or ())
        record = lifecycle.ArtifactRecord(**fields)
        if str(Path(record.path).resolve()) != str(Path(item["path"]).resolve()):
            raise lifecycle.LifecycleRefused("artifact path changed since storage plan")
        results.append(lifecycle.purge_file(project, record, str(Path(project).resolve())))
    return {"manifest": str(Path(manifest).resolve()), "purged": results,
            "logical_bytes": sum(item["logical_bytes"] for item in results)}


def collect(manifest: str) -> dict:
    """Quarantine planned artifacts after exact identity and state checks."""
    document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if document.get("format") != "ren-storage-plan/1":
        raise lifecycle.LifecycleRefused("unknown storage manifest format")
    moved = []
    for item in document["candidates"]:
        project = item["project"]
        current = lifecycle._latest_artifacts(project).get(item["artifact_id"])
        if current is None or current.get("state") != lifecycle.DISPOSABLE:
            if current and current.get("state") == lifecycle.QUARANTINED:
                _assert_plan_binding(item, current)
                moved.append(item["artifact_id"])
                continue
            raise lifecycle.LifecycleRefused(
                f"artifact is no longer disposable: {item['artifact_id']}")
        _assert_plan_binding(item, current)
        identity = lifecycle.FileIdentity(**current["identity"])
        if not identity.matches(current["path"]):
            raise lifecycle.LifecycleRefused(
                f"artifact identity changed since storage plan: {current['path']}")
        lifecycle.transition_artifact(
            project, item["artifact_id"], lifecycle.QUARANTINED,
            reason="collected under reviewed storage manifest")
        moved.append(item["artifact_id"])
    return {"manifest": str(Path(manifest).resolve()), "quarantined": moved}


def restore(manifest: str) -> dict:
    """Restore only extant quarantined entries; this lane creates no quarantine."""
    document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if document.get("format") != "ren-storage-plan/1":
        raise lifecycle.LifecycleRefused("unknown storage manifest format")
    restored = []
    for item in document.get("candidates", []):
        latest = lifecycle._latest_artifacts(item["project"]).get(item["artifact_id"])
        if latest is not None and latest.get("state") == lifecycle.DISPOSABLE:
            _assert_plan_binding(item, latest)
            restored.append(item["artifact_id"])
            continue
        if latest is None or latest.get("state") != lifecycle.QUARANTINED:
            raise lifecycle.LifecycleRefused(
                f"artifact is not a known quarantined resource: {item['artifact_id']}")
        _assert_plan_binding(item, latest)
        identity = lifecycle.FileIdentity(**latest["identity"])
        if not identity.matches(latest["path"]):
            raise lifecycle.LifecycleRefused(
                f"artifact identity changed since collection: {latest['path']}")
        lifecycle.transition_artifact(item["project"], item["artifact_id"],
                                       lifecycle.DISPOSABLE,
                                       reason="restored by storage restore")
        restored.append(item["artifact_id"])
    return {"manifest": str(Path(manifest).resolve()), "restored": restored}

"""Public storage accounting and manifest guarded lifecycle operations.

Inventory is read from the ownership event logs, never reconstructed by
walking artifact directories. Unknown or changed identities are reported
as protected and cannot enter an apply manifest.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from library.tools import artifact_lifecycle as lifecycle
from library.tools import project_registry

DEFAULT_FREE_RESERVE_BYTES = 10 * 1024**3
PROTECTED_SUFFIXES = frozenset({".drt", ".drp", ".otio"})
PROTECTED_KINDS = frozenset({"model_weight", "model_weights", "source_footage",
                             "user_asset", "delivered_render", "signed_off_render"})
LEGACY_INVENTORY_FORMAT = "ren-legacy-inventory/1"
LEGACY_DELETED_CLIPS_RECORD = (
    "data/vep-resolve-stale-cleanup/q-workflow-exports-deleted.txt")
LEGACY_RETENTION = timedelta(hours=24)


def _legacy_roots() -> list[tuple[str, Path]]:
    """Known legacy stores, derived from the same owners as their writers."""
    from library.tools import qualification_project, ren_native_cache
    from library.tools.project_layout import Area, ProjectLayout

    roots = [("qualification", qualification_project.media_dir().parent)]
    roots.append(("native_cache", ren_native_cache.cache_dir()))
    for project in project_registry.list_projects():
        layout = ProjectLayout(project.project_root)
        roots.extend((
            ("abandoned_scratch", layout.read_dir(Area.SCRATCH)),
            ("quarantine", layout.read_dir(Area.QUARANTINE)),
        ))
    return roots


def _legacy_category(category: str, root: Path, path: Path) -> str:
    relative_parts = path.relative_to(root).parts
    if category == "qualification":
        if "exports" in relative_parts:
            return "qualification_export"
        if "workflow-runs" in relative_parts:
            return "workflow_run"
    return category


def legacy_inventory(*, output: str, roots: list[tuple[str, str | Path]] | None = None) -> dict:
    """Write a read-only inventory of legacy stores; unknowns remain unverified.

    The inventory deliberately creates no ownership records: a filesystem
    resemblance is not evidence of ownership. Exact existing lifecycle records
    can be reconciled by a later reviewed migration.
    """
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools import event_log

    sources = ([(kind, Path(path).expanduser()) for kind, path in roots]
               if roots is not None else _legacy_roots())
    known: dict[str, list[dict]] = {}
    project_logs = ([str(project.project_root)
                     for project in project_registry.list_projects()]
                    if roots is None else [])
    # Workflow run directories own their event logs independently of projects.
    for _, root in sources:
        for base, dirs, _ in os.walk(root, followlinks=False):
            if Path(base).name != "workflow-runs":
                continue
            for name in dirs:
                run = Path(base) / name
                if run.is_dir() and not run.is_symlink():
                    project_logs.append(str(run))
            dirs[:] = []
    for owner in project_logs:
        for row in lifecycle.inventory(owner)["artifacts"]:
            if row.get("path"):
                known.setdefault(str(Path(row["path"]).absolute()), []).append(row)
    for _, root in sources:
        for base, dirs, files in os.walk(root, followlinks=False):
            if Path(base).name != "workflow-runs":
                continue
            for name in dirs:
                run_root = Path(base) / name
                manifest_path = run_root / "qualification-cleanup-manifest.json"
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for row in manifest.get("artifacts", []):
                    if row.get("path") and row.get("identity"):
                        known.setdefault(str(Path(row["path"]).absolute()), []).append({
                            **row, "run_id": manifest.get("run_id", name),
                            "project_id": "qualification-workflow",
                            "job_id": manifest.get("run_id", name),
                            "ownership_known": bool(manifest.get("run_id", name)),
                            "record_source": "qualification_cleanup_manifest",
                        })
            dirs[:] = []

    resources = []
    for category, root in sources:
        if not root.exists():
            continue
        if root.is_symlink():
            resources.append({"category": category, "path": str(root),
                              "verification": "unverified",
                              "reason": "inventory root is a symlink"})
            continue
        for base, dirs, files in os.walk(root, followlinks=False):
            base_path = Path(base)
            if base_path == root or base_path.name == "workflow-runs":
                for name in dirs:
                    path = base_path / name
                    if path.is_symlink():
                        continue
                    resources.append({"category": _legacy_category(category, root, path),
                                      "path": str(path),
                                      "resource_type": "directory",
                                      "verification": "unverified",
                                      "reason": "directory ownership requires file and run evidence"})
            for name in list(dirs):
                path = base_path / name
                if path.is_symlink():
                    dirs.remove(name)
                    resources.append({"category": _legacy_category(category, root, path),
                                      "path": str(path),
                                      "verification": "unverified",
                                      "reason": "symlink directory identity is ambiguous"})
            for name in files:
                path = base_path / name
                try:
                    info = os.lstat(path)
                    if not stat.S_ISREG(info.st_mode):
                        raise OSError("not a regular file")
                    identity = lifecycle.FileIdentity.capture(path)
                except (OSError, lifecycle.LifecycleRefused) as exc:
                    resources.append({"category": _legacy_category(category, root, path),
                                      "path": str(path),
                                      "verification": "unverified",
                                      "reason": f"identity unavailable: {exc}"})
                    continue
                matches = [row for row in known.get(str(path.absolute()), [])
                           if row.get("ownership_known")
                           and (row.get("identity_matches") is True
                                or row.get("identity") == identity.__dict__)]
                resources.append({
                    "category": _legacy_category(category, root, path),
                    "path": str(path),
                    "identity": identity.__dict__,
                    "verification": "verified" if len(matches) == 1 else "unverified",
                    "artifact_id": matches[0]["artifact_id"] if len(matches) == 1 else "",
                    "run_id": matches[0].get("run_id", "") if len(matches) == 1 else "",
                    "project_id": matches[0].get("project_id", "") if len(matches) == 1 else "",
                    "job_id": matches[0].get("job_id", "") if len(matches) == 1 else "",
                    "record_source": matches[0].get("record_source", "artifact_lifecycle")
                    if len(matches) == 1 else "",
                    "reason": ("matched exact lifecycle record" if len(matches) == 1
                               else "no unique exact lifecycle ownership record"),
                })
    resolve_rows = []
    for owner in project_logs:
        events = event_log.read_events(owner)
        for event in events:
            if event.get("type") != "qualification_resolve_resource":
                continue
            fields = event.get("fields", {})
            resolve_rows.append({"category": "qualification_timeline",
                                 "verification": "unverified",
                                 "reason": "exact recorded id needs a Resolve maintenance-window census",
                                 **fields})
        for row in lifecycle.resolve_artifacts(str(event_log.log_path(owner))):
            resolve_rows.append({"category": "recorded_resolve_resource",
                                 "verification": "unverified",
                                 "reason": "Resolve presence requires an exact-id maintenance-window census",
                                 **row})
    document = {
        "format": LEGACY_INVENTORY_FORMAT,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "resources": resources + resolve_rows,
        "review_required": sum(row.get("verification") == "unverified"
                               for row in resources + resolve_rows),
        "historical_deletion_record": LEGACY_DELETED_CLIPS_RECORD,
        "automatic_deletion": False,
    }
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {**document, "manifest": str(path)}


def reconcile(projects: list[str], *, now: datetime | None = None) -> dict:
    """Lightweight lifecycle reconciliation; deletion remains manifest-gated."""
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools import event_log

    now = now or datetime.now(UTC)
    results = []
    for project in projects:
        events = event_log.read_events(project)
        quarantine_at = {}
        for event in events:
            fields = event.get("fields", {})
            if event.get("type") == lifecycle.ARTIFACT_TRANSITION:
                if fields.get("state") == lifecycle.QUARANTINED:
                    quarantine_at[fields.get("artifact_id", "")] = event.get("at", "")
                elif fields.get("state") in {lifecycle.DISPOSABLE, lifecycle.PURGED}:
                    quarantine_at.pop(fields.get("artifact_id", ""), None)
        inventory = lifecycle.inventory(project)
        drift = [row["artifact_id"] for row in inventory["artifacts"]
                 if row.get("identity_matches") is False]
        expired = []
        for row in inventory["artifacts"]:
            if row.get("state") != lifecycle.QUARANTINED:
                continue
            try:
                quarantined = datetime.fromisoformat(quarantine_at[row["artifact_id"]])
                if quarantined.tzinfo is None:
                    quarantined = quarantined.replace(tzinfo=UTC)
                if now - quarantined >= LEGACY_RETENTION:
                    expired.append(row["artifact_id"])
            except (KeyError, ValueError):
                continue
        recovered = lifecycle.recover_interrupted_cleanup_jobs(project)
        retried = []
        for job in lifecycle._latest_cleanup_jobs(project).values():
            if job.get("state") == "refused":
                lifecycle.transition_cleanup_job(
                    project, job, "queued",
                    outcome="startup reconciliation retry after fresh safety checks")
                retried.append(job["lifecycle_event_id"])
        queued = lifecycle.pending_cleanup_jobs(project)
        results.append({"project": str(Path(project).resolve()),
                        "abandoned_jobs_recovered": recovered,
                        "refused_jobs_retried": retried,
                        "pending_job_ids": [job["lifecycle_event_id"]
                                            for job in queued],
                        "worker_required": bool(recovered or retried),
                        "record_filesystem_drift": drift,
                        "expired_quarantine_review": expired,
                        "resolve_census": "deferred to maintenance window"})
    return {"projects": results, "automatic_deletion": False}


def startup_reconcile() -> dict:
    """Run the machine's lightweight check without depending on an edit job."""
    projects = [str(config.project_root)
                for config in project_registry.list_projects()]
    if not projects:
        return {"projects": [], "automatic_deletion": False}
    return reconcile(projects)


def _assert_plan_binding(item: dict, record: dict) -> None:
    """Refuse a manifest whose exact planned path or identity was edited."""
    planned_identity = item.get("identity") or {}
    current_identity = record.get("identity") or {}
    planned_path = str(Path(item.get("path", "")).absolute())
    current_path = str(Path(record["path"]).absolute())
    original_path = str(Path(record.get("original_path", "")).absolute())
    if (planned_path not in {current_path, original_path}
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


def estimate_render_space(reels: list[tuple[float, int, int]], *,
                          output_bit_rate_bps: int | None = None,
                          workspace_multiplier: float | None = None) -> int:
    """Estimate render outputs plus staging from approved durations and frame sizes."""
    try:
        bit_rate = int(output_bit_rate_bps or os.environ.get(
            "REN_RENDER_ESTIMATED_BITRATE_BPS", "25000000"))
        multiplier = float(workspace_multiplier or os.environ.get(
            "REN_RENDER_WORKSPACE_MULTIPLIER", "2.0"))
    except ValueError as exc:
        raise ValueError("invalid render storage estimate configuration") from exc
    if bit_rate <= 0 or multiplier < 1:
        raise ValueError("render estimate needs positive bitrate and multiplier >= 1")
    baseline_pixels = 1080 * 1920
    output_bytes = 0
    for duration, width, height in reels:
        if duration <= 0 or width <= 0 or height <= 0:
            raise ValueError("render estimates require positive duration and dimensions")
        scaled_rate = bit_rate * (width * height / baseline_pixels)
        output_bytes += int(duration * scaled_rate / 8)
    return int(output_bytes * multiplier)


def plan_cache_evictions(projects: list[str], *, pressure_state: str,
                         cache_bytes: int | None = None) -> list[dict]:
    """Plan value-aware cache eviction against one machine-wide budget."""
    from library.tools import cache_lifecycle

    base = cache_lifecycle.CacheBudget.from_environment()
    pressure_factor = {"normal": 1.0, "elevated": 0.8,
                       "critical": 0.5}.get(pressure_state)
    if pressure_factor is None:
        raise ValueError(f"unknown disk pressure state: {pressure_state}")
    cache_kinds = cache_lifecycle.CACHE_KIND_COST
    project_bytes = {}
    project_class_bytes = {}
    for project in projects:
        rows = lifecycle.inventory(project)["artifacts"]
        sized = [row for row in rows if row.get("identity_matches")
                 and row.get("ownership_known")
                 and row.get("state") == lifecycle.CACHEABLE
                 and row.get("kind") in cache_kinds]
        project_bytes[project] = sum(
            int(row["identity"]["size_bytes"]) for row in sized)
        classes = {"expensive": 0, "inexpensive": 0}
        for row in sized:
            bucket = ("expensive" if cache_kinds[row["kind"]] in
                      {"medium_high", "high"} else "inexpensive")
            classes[bucket] += int(row["identity"]["size_bytes"])
        project_class_bytes[project] = classes
    total = sum(project_bytes.values())
    if total == 0:
        return []
    # `cache_bytes` may include held/incompatible entries; use the cacheable
    # total as the allocator denominator, while the policy still excludes
    # entries it cannot safely measure.
    target_total = int(base.total_bytes * pressure_factor)
    plans = []
    for project, local_bytes in project_bytes.items():
        if not local_bytes:
            continue
        share = local_bytes / total
        expensive = (int(base.expensive_bytes * share)
                     if base.expensive_bytes is not None else None)
        inexpensive = (int(base.inexpensive_bytes * share)
                       if base.inexpensive_bytes is not None else None)
        local_budget = cache_lifecycle.CacheBudget(
            total_bytes=max(1, int(target_total * share)),
            low_watermark=base.low_watermark,
            high_watermark=base.high_watermark,
            expensive_bytes=max(1, expensive) if expensive is not None else None,
            inexpensive_bytes=(max(1, inexpensive)
                               if inexpensive is not None else None))
        planned = cache_lifecycle.plan_eviction(project, budget=local_budget)
        if planned["selected"]:
            plans.append({"project": project, "plan": planned})
    return plans


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
    cache_evictions = plan_cache_evictions(
        projects, pressure_state=report["disk_pressure"],
        cache_bytes=report["cache_bytes"])
    document = {"format": "ren-storage-plan/1", "candidates": candidates,
                "cache_evictions": cache_evictions,
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
                moved.append({"artifact_id": item["artifact_id"],
                              "path": current["path"],
                              "already_quarantined": True})
                continue
            raise lifecycle.LifecycleRefused(
                f"artifact is no longer disposable: {item['artifact_id']}")
        _assert_plan_binding(item, current)
        fields = {key: current[key]
                  for key in lifecycle.ArtifactRecord.__dataclass_fields__}
        fields["identity"] = lifecycle.FileIdentity(**fields["identity"])
        fields["dependencies"] = tuple(fields.get("dependencies") or ())
        record = lifecycle.ArtifactRecord(**fields)
        moved.append(lifecycle.quarantine_file(
            project, record, str(Path(project).resolve())))
    cache_evicted = []
    from library.tools import cache_lifecycle

    for entry in document.get("cache_evictions", []):
        project = entry["project"]
        current = lifecycle._latest_artifacts(project)
        records = {}
        cleanup_roots = {}
        for selected in entry["plan"].get("selected", []):
            artifact_id = selected["artifact_id"]
            fields = current.get(artifact_id)
            if fields is None:
                raise lifecycle.LifecycleRefused(
                    f"cache ownership disappeared: {artifact_id}")
            values = {key: fields[key]
                      for key in lifecycle.ArtifactRecord.__dataclass_fields__}
            values["identity"] = lifecycle.FileIdentity(**values["identity"])
            values["dependencies"] = tuple(values.get("dependencies") or ())
            record = lifecycle.ArtifactRecord(**values)
            records[artifact_id] = record
            cleanup_roots[artifact_id] = str(Path(record.path).parent)
        cache_evicted.extend(cache_lifecycle.apply_eviction(
            project, entry["plan"], cleanup_root=project,
            records=records, cleanup_roots=cleanup_roots))
    return {"manifest": str(Path(manifest).resolve()), "quarantined": moved,
            "cache_evicted": cache_evicted}


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
            restored.append({"artifact_id": item["artifact_id"],
                             "path": latest["path"],
                             "already_restored": True})
            continue
        if latest is None or latest.get("state") != lifecycle.QUARANTINED:
            raise lifecycle.LifecycleRefused(
                f"artifact is not a known quarantined resource: {item['artifact_id']}")
        _assert_plan_binding(item, latest)
        restored.append(lifecycle.restore_file(
            item["project"], item["artifact_id"]))
    return {"manifest": str(Path(manifest).resolve()), "restored": restored}

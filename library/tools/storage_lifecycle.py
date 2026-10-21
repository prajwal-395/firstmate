"""Public storage accounting and manifest guarded lifecycle operations.

Inventory is read from the ownership event logs, never reconstructed by
walking artifact directories. Unknown or changed identities are reported
as protected and cannot enter an apply manifest.

Two questions are answered here, per project and for the machine:

- **How much disk does this project hold?** `status()` reports the
  registered artifacts per project, the legacy entries each project
  owns, and what may be reclaimed. The legacy part matters most: the
  registered part is a fraction of what is on disk.
- **What did this project's analysis consume?** `analysis_resources()`
  reads the project's own performance ledger back - the wall, the CPU,
  the peak memory and the bytes each capability wrote - and says
  plainly when a measurement was absent rather than reporting the
  absence as a zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from library.tools import artifact_lifecycle as lifecycle
from library.tools import artifact_policy
from library.tools import git_guard as _git_guard
from library.tools import project_registry

DEFAULT_FREE_RESERVE_BYTES = 10 * 1024**3
PROTECTED_SUFFIXES = frozenset({".drt", ".drp", ".otio"})
PROTECTED_KINDS = frozenset(
    {
        "model_weight",
        "model_weights",
        "source_footage",
        "user_asset",
        "delivered_render",
        "signed_off_render",
    }
)
LEGACY_INVENTORY_FORMAT = "ren-legacy-inventory/1"
LEGACY_DELETED_CLIPS_RECORD = (
    "data/vep-resolve-stale-cleanup/q-workflow-exports-deleted.txt"
)
LEGACY_RETENTION = timedelta(hours=24)
MAX_CLEANUP_JOB_RETRIES = 3
"""How many times a refused cleanup job is retried before it is named.

A refusal that is re-queued on every start is a loop, not a retry: the
job never succeeds and the machine warns about it forever. Three fresh
attempts with fresh safety checks are a retry; after that the job is
reported as needing a person."""
STORAGE_QUALIFICATION_EVENT = "storage_cleanup_qualification"
STORAGE_QUALIFICATION_VERSION = 1
STORAGE_QUALIFICATION_SCENARIOS = frozenset(
    {
        "owned_vs_external_media",
        "pending_transactions",
        "approved_versions",
        "required_recovery_archives",
        "shared_footage_intelligence",
        "unpromoted_staging",
        "resolve_ownership_before_retirement",
        "dry_run_preview",
        "reclaimed_space",
        "future_rebuild_and_undo",
    }
)


def record_cleanup_qualification(
    project_folder: str, *, scenarios: list[dict], record_id: str
) -> dict:
    """Persist complete, evidence-backed cleanup qualification for a project.

    A caller must provide one passing observation and at least one durable
    evidence reference for every required lifecycle scenario. Tests and plans
    alone are not qualification records.
    """
    if not record_id:
        raise ValueError("storage cleanup qualification needs a record id")
    by_id = {row.get("scenario_id"): dict(row) for row in scenarios}
    if len(by_id) != len(scenarios) or set(by_id) != STORAGE_QUALIFICATION_SCENARIOS:
        missing = sorted(STORAGE_QUALIFICATION_SCENARIOS - set(by_id))
        extra = sorted(set(by_id) - STORAGE_QUALIFICATION_SCENARIOS)
        raise ValueError(
            f"qualification scenarios mismatch; missing={missing}, extra={extra}"
        )
    for scenario_id, row in by_id.items():
        evidence = row.get("evidence")
        if (
            row.get("passed") is not True
            or not isinstance(evidence, list)
            or not evidence
        ):
            raise ValueError(
                f"qualification scenario lacks passing evidence: {scenario_id}"
            )
        fingerprints = []
        scenario_document_found = False
        for item in evidence:
            if not isinstance(item, str) or not item.strip():
                raise TypeError(
                    f"qualification evidence must name existing absolute files: {scenario_id}"
                )
            path = Path(item)
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"qualification evidence must name existing absolute files: {scenario_id}"
                )
            payload = path.read_bytes()
            try:
                observation = json.loads(payload)
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(
                    f"qualification evidence is not a JSON observation: {item}"
                ) from exc
            if not isinstance(observation, dict):
                raise TypeError(
                    f"qualification evidence is not an observation object: {item}"
                )
            if (
                observation.get("scenario_id") == scenario_id
                and observation.get("passed") is True
            ):
                scenario_document_found = True
            fingerprints.append(
                {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest()}
            )
        if not scenario_document_found:
            raise ValueError(
                f"qualification evidence does not prove scenario pass: {scenario_id}"
            )
        row["evidence"] = fingerprints
    from library.tools import event_log

    return event_log.emit(
        project_folder,
        STORAGE_QUALIFICATION_EVENT,
        task="storage-qualification",
        record_id=record_id,
        schema_version=STORAGE_QUALIFICATION_VERSION,
        scenarios=[by_id[key] for key in sorted(by_id)],
        qualified=True,
    )


def cleanup_qualification(project_folder: str) -> dict | None:
    """Return the latest complete qualification record, if one exists."""
    from library.tools import event_log

    records = [
        event.get("fields", {})
        for event in event_log.read_events(project_folder)
        if event.get("type") == STORAGE_QUALIFICATION_EVENT
    ]
    if not records:
        return None
    record = records[-1]
    scenario_rows = record.get("scenarios")
    if not isinstance(scenario_rows, list) or any(
        not isinstance(row, dict) for row in scenario_rows
    ):
        return None
    scenarios = {row.get("scenario_id"): row for row in scenario_rows}
    try:
        evidence_intact = all(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and Path(item["path"]).is_file()
            and not Path(item["path"]).is_symlink()
            and hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
            == item.get("sha256")
            for row in scenarios.values()
            for item in row.get("evidence", [])
        )
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        evidence_intact = False
    if (
        record.get("schema_version") != STORAGE_QUALIFICATION_VERSION
        or record.get("qualified") is not True
        or set(scenarios) != STORAGE_QUALIFICATION_SCENARIOS
        or any(
            row.get("passed") is not True or not row.get("evidence")
            for row in scenarios.values()
        )
        or not evidence_intact
    ):
        return None
    return record


def _legacy_roots(projects: list[str] = ()) -> list[tuple[str, Path]]:
    """Known legacy stores, derived from the same owners as their writers."""
    from library.tools import qualification_project, ren_native_cache
    from library.tools.project_layout import Area, ProjectLayout

    roots = [("qualification", qualification_project.media_dir().parent)]
    roots.append(("native_cache", ren_native_cache.cache_dir()))
    for project in project_registry.list_projects():
        layout = ProjectLayout(project.project_root)
        roots.extend(
            (
                ("abandoned_scratch", layout.read_dir(Area.SCRATCH)),
                ("quarantine", layout.read_dir(Area.QUARANTINE)),
                ("review", layout.read_dir(Area.REVIEW)),
            )
        )
    known = {str(path.resolve()) for _, path in roots}
    for project in projects:
        layout = ProjectLayout(project)
        for category, area in (("abandoned_scratch", Area.SCRATCH),
                               ("quarantine", Area.QUARANTINE),
                               ("review", Area.REVIEW)):
            path = layout.read_dir(area)
            if str(path.resolve()) not in known:
                roots.append((category, path))
                known.add(str(path.resolve()))
    return roots


def _legacy_category(category: str, root: Path, path: Path) -> str:
    relative_parts = path.relative_to(root).parts
    if category == "qualification":
        if "exports" in relative_parts:
            return "qualification_export"
        if "workflow-runs" in relative_parts:
            return "workflow_run"
    return category


def _legacy_resources(
    sources: list[tuple[str, Path]], *, project_logs: list[str]
) -> list[dict]:
    """Measure legacy entries without following links or guessing owners.

    ``physical_bytes`` is allocated bytes charged once per inode across the
    inventory.  ``allocated_bytes`` on each row keeps the per-entry stat value
    visible when two paths are hardlinks to the same storage.
    """
    from library.tools import event_log

    known: dict[str, list[dict]] = {}
    owners = list(project_logs)
    for _, root in sources:
        for base, dirs, _ in os.walk(root, followlinks=False):
            if Path(base).name == "workflow-runs":
                owners.extend(
                    str(Path(base) / name)
                    for name in dirs
                    if not (Path(base) / name).is_symlink()
                )
                dirs[:] = []
                continue
            for name in dirs:
                run_root = Path(base) / name
                manifest_path = run_root / "qualification-cleanup-manifest.json"
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for manifest_row in manifest.get("artifacts", []):
                    if manifest_row.get("path") and manifest_row.get("identity"):
                        known.setdefault(
                            str(Path(manifest_row["path"]).absolute()), []
                        ).append(
                            {
                                **manifest_row,
                                "run_id": manifest.get("run_id", name),
                                "project_id": "qualification-workflow",
                                "job_id": manifest.get("run_id", name),
                                "ownership_known": True,
                                "record_source": "qualification_cleanup_manifest",
                            }
                        )
                dirs[:] = []
    # Build sweeps preserve the exact original paths, while the caption
    # sweep mark and manifest bind each moved file to its quarantine batch.
    for category, root in sources:
        if category != "quarantine" or not root.is_dir() or root.is_symlink():
            continue
        for sweep_path in root.glob("build_sweep_*.json"):
            try:
                sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for area in (sweep.get("files") or {}).get("areas", []):
                mark_path = Path(area.get("mark_path", ""))
                manifest_path = Path(area.get("manifest_path", ""))
                match = re.search(r"_(\d{8}T\d{6}Z)_manifest\.md$", manifest_path.name)
                if not match:
                    continue
                try:
                    mark = json.loads(mark_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                marked = {
                    row["path"]: row
                    for row in mark.get("assets", [])
                    if row.get("path")
                }
                batch = manifest_path.parent / match.group(1)
                for original in area.get("moved", []):
                    proof = marked.get(original)
                    destination = batch / Path(original).name
                    if (
                        not proof
                        or proof.get("status") != "orphan"
                        or destination.is_symlink()
                        or not destination.is_file()
                        or destination.stat().st_size != proof.get("size_bytes")
                    ):
                        continue
                    identity = lifecycle.FileIdentity.capture(destination).__dict__
                    known.setdefault(str(destination.absolute()), []).append(
                        {
                            "artifact_id": "legacy-sweep:"
                            + str(sweep_path.name)
                            + ":"
                            + hashlib.sha256(
                                str(destination).encode("utf-8")
                            ).hexdigest()[:20],
                            "identity": identity,
                            "ownership_known": True,
                            "run_id": sweep.get("swept_at", ""),
                            "project_id": sweep.get("project_folder", ""),
                            "job_id": "build_sweep",
                            "state": lifecycle.QUARANTINED,
                            "record_source": "build_sweep_manifest+caption_sweep_mark",
                            "evidence_paths": [
                                str(sweep_path),
                                str(mark_path),
                                str(manifest_path),
                            ],
                        }
                    )
    for owner in owners:
        for row in lifecycle.inventory(owner)["artifacts"]:
            if row.get("path"):
                known.setdefault(str(Path(row["path"]).absolute()), []).append(row)

    resources = []
    seen_inodes: set[tuple[int, int]] = set()
    for category, root in sources:
        root = root.expanduser()
        if not root.exists() and not root.is_symlink():
            continue
        if root.is_symlink():
            resources.append(
                {
                    "category": category,
                    "path": str(root),
                    "resource_type": "symlink",
                    "verification": "unverified",
                    "reason": "inventory root is a symlink",
                    "logical_bytes": 0,
                    "allocated_bytes": 0,
                    "physical_bytes": 0,
                    "references": [],
                }
            )
            continue
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as entries:
                    children = sorted(entries, key=lambda entry: entry.name)
            except OSError as exc:
                resources.append(
                    {
                        "category": category,
                        "path": str(directory),
                        "resource_type": "directory",
                        "verification": "unverified",
                        "reason": f"directory could not be inventoried: {exc}",
                        "logical_bytes": 0,
                        "allocated_bytes": 0,
                        "physical_bytes": 0,
                        "references": [],
                    }
                )
                continue
            for child in children:
                path = Path(child.path)
                kind = (
                    "symlink"
                    if child.is_symlink()
                    else (
                        "directory" if child.is_dir(follow_symlinks=False) else "file"
                    )
                )
                try:
                    info = child.stat(follow_symlinks=False)
                    allocated = int(getattr(info, "st_blocks", 0)) * 512
                    inode = (int(info.st_dev), int(info.st_ino))
                    physical = allocated if inode not in seen_inodes else 0
                    seen_inodes.add(inode)
                    identity = {
                        "device": inode[0],
                        "inode": inode[1],
                        "size_bytes": int(info.st_size),
                        "mtime_ns": int(info.st_mtime_ns),
                        "link_count": int(info.st_nlink),
                    }
                except OSError as exc:
                    resources.append(
                        {
                            "category": _legacy_category(category, root, path),
                            "path": str(path),
                            "resource_type": kind,
                            "verification": "unverified",
                            "reason": f"identity unavailable: {exc}",
                            "logical_bytes": 0,
                            "allocated_bytes": 0,
                            "physical_bytes": 0,
                            "references": [],
                        }
                    )
                    continue
                row = {
                    "category": _legacy_category(category, root, path),
                    "path": str(path),
                    "resource_type": kind,
                    "identity": identity,
                    "logical_bytes": int(info.st_size) if kind == "file" else 0,
                    "allocated_bytes": allocated,
                    "physical_bytes": physical,
                    "hardlink_group": f"{inode[0]}:{inode[1]}"
                    if info.st_nlink > 1
                    else "",
                    "references": [],
                    "verification": "unverified",
                    "reason": "no unique exact lifecycle ownership record",
                }
                matches = [
                    item
                    for item in known.get(str(path.absolute()), [])
                    if item.get("ownership_known")
                    and (
                        item.get("identity_matches") is True
                        or item.get("identity") == identity
                    )
                ]
                if len(matches) == 1:
                    match = matches[0]
                    row.update(
                        verification="verified",
                        artifact_id=match["artifact_id"],
                        run_id=match.get("run_id", ""),
                        project_id=match.get("project_id", ""),
                        job_id=match.get("job_id", ""),
                        lifecycle_state=match.get("state", ""),
                        record_source=match.get("record_source", "artifact_lifecycle"),
                        references=match.get("evidence_paths")
                        or [match.get("artifact_id", "")],
                        reason="matched exact lifecycle or sweep evidence",
                    )
                resources.append(row)
                if kind == "directory":
                    stack.append(path)
    quarantined_artifact_dirs = {
        row["artifact_id"]: Path(row["path"]).parent
        for row in resources
        if row.get("verification") == "verified"
        and row.get("lifecycle_state") == lifecycle.QUARANTINED
        and row.get("artifact_id")
        and Path(row["path"]).parent.parent.name == "lifecycle"
    }
    for row in resources:
        if (
            row.get("category") == "quarantine"
            and row.get("resource_type") == "directory"
        ):
            artifact_id = Path(row["path"]).name
            if quarantined_artifact_dirs.get(artifact_id) == Path(row["path"]):
                row.update(
                    verification="verified",
                    artifact_id=artifact_id,
                    lifecycle_state=lifecycle.QUARANTINED,
                    record_source="artifact_lifecycle_quarantine_container",
                    references=[artifact_id],
                    reason="exact lifecycle quarantine container for registered artifact",
                )
    # Resolve resources have identities but no filesystem allocation.
    for owner in owners:
        for event in event_log.read_events(owner):
            if event.get("type") != "qualification_resolve_resource":
                continue
            resources.append(
                {
                    "category": "qualification_timeline",
                    "path": event.get("fields", {}).get("timeline", ""),
                    "resource_type": "resolve_resource",
                    "verification": "unverified",
                    "reason": "exact recorded id needs a Resolve maintenance-window census",
                    "logical_bytes": 0,
                    "allocated_bytes": 0,
                    "physical_bytes": 0,
                    "references": [event.get("id", "")],
                }
            )
    return resources


def legacy_inventory(
    *, output: str, roots: list[tuple[str, str | Path]] | None = None
) -> dict:
    """Write a read-only inventory of legacy stores; unknowns remain unverified.

    The inventory deliberately creates no ownership records: a filesystem
    resemblance is not evidence of ownership. Exact existing lifecycle records
    can be reconciled by a later reviewed migration.
    """
    sources = (
        [(kind, Path(path).expanduser()) for kind, path in roots]
        if roots is not None
        else _legacy_roots()
    )
    project_logs = (
        [str(project.project_root) for project in project_registry.list_projects()]
        if roots is None
        else []
    )
    resources = _legacy_resources(sources, project_logs=project_logs)
    all_rows = resources
    for row in all_rows:
        row.setdefault(
            "classification",
            "registered" if row["verification"] == "verified" else "review_only",
        )
        row.setdefault("keep_reason", "")
        row.setdefault("references", [])
    document = {
        "format": LEGACY_INVENTORY_FORMAT,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "resources": all_rows,
        "review_required": sum(
            row.get("verification") == "unverified" for row in all_rows
        ),
        "historical_deletion_record": LEGACY_DELETED_CLIPS_RECORD,
        "automatic_deletion": False,
        "accounting": {
            "entries": len(all_rows),
            "logical_bytes": sum(row.get("logical_bytes", 0) for row in all_rows),
            "physical_bytes": sum(row.get("physical_bytes", 0) for row in all_rows),
            "unverified_physical_bytes": sum(
                row.get("physical_bytes", 0)
                for row in all_rows
                if row.get("verification") == "unverified"
            ),
            "hardlink_policy": "allocated bytes counted once per device/inode",
        },
    }
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {**document, "manifest": str(path)}


def register_legacy_artifacts(projects: list[str]) -> dict:
    """Adopt exact project scratch files named by observed provenance.

    Ownership comes from a provenance row that says the runner observed the
    file change during a named run.  Files referenced by version, recovery,
    review, export, or Resolve snapshot records stay retained.  Unknown files
    and all non-scratch categories remain review-only.
    """
    from library.tools import provenance
    from library.tools.project_layout import Area, ProjectLayout

    registered = []
    retained = []
    for project in projects:
        root = Path(project).resolve()
        layout = ProjectLayout(str(root))
        scratch = layout.read_dir(Area.SCRATCH).resolve()
        artifacts = (
            layout.read_dir(Area.OUTPUT_ROOT) / "provenance" / provenance.ARTIFACTS_FILE
        )
        latest = {}
        try:
            for line in artifacts.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    latest[row["path"]] = row
        except (OSError, ValueError, KeyError):
            continue

        protected_roots = [
            layout.read_dir(area) for area in (Area.REVIEW, Area.EXPORTS, Area.BACKUPS)
        ]
        # Version and recovery archives are not all layout areas; use only
        # their declared project output locations when present.
        protected_roots.extend(
            root / "pipeline_output" / part
            for part in ("versions", "recovery", "undo", "resolve")
        )
        reference_files = [
            root / name
            for name in ("project.yaml", "pipeline_data.json", "pipeline_run.json")
        ]
        for directory in protected_roots:
            if directory.is_dir():
                for base, dirs, files in os.walk(directory, followlinks=False):
                    dirs[:] = [
                        name for name in dirs if not (Path(base) / name).is_symlink()
                    ]
                    reference_files.extend(
                        Path(base) / name
                        for name in files
                        if not (Path(base) / name).is_symlink()
                    )
        eligible = []
        for base, dirs, files in os.walk(scratch, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
            for name in files:
                path = Path(base) / name
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                source = latest.get(relative)
                if (
                    source
                    and source.get("method") == provenance.OBSERVED
                    and source.get("run_id")
                    and source.get("step_id")
                    and int(source.get("bytes", -1)) == path.stat().st_size
                ):
                    eligible.append((path, relative, source))
        names = sorted({path.name for path, _, _ in eligible}, key=len, reverse=True)
        mention_pattern = (
            re.compile(
                r"(?<![\w.-])(?:"
                + "|".join(re.escape(name) for name in names)
                + r")(?![\w.-])"
            )
            if names
            else None
        )
        mentions: dict[str, list[str]] = {}
        for reference in reference_files:
            try:
                if (
                    reference.is_file()
                    and reference.suffix.lower()
                    in {
                        ".json",
                        ".jsonl",
                        ".txt",
                        ".md",
                        ".yaml",
                        ".yml",
                        ".toml",
                        ".xml",
                        ".csv",
                    }
                    and reference.stat().st_size <= 32 * 1024 * 1024
                ):
                    content = reference.read_text(encoding="utf-8", errors="ignore")
                    if mention_pattern:
                        for match in mention_pattern.finditer(content):
                            mentions.setdefault(match.group(0), []).append(
                                str(reference)
                            )
            except OSError:
                continue

        for path, relative, source in eligible:
            references = mentions.get(path.name, [])
            if references:
                retained.append(
                    {
                        "path": str(path),
                        "references": references,
                        "reason": "named by retained project record",
                    }
                )
                continue
            path = path.resolve()
            identity = lifecycle.FileIdentity.capture(path)
            if identity.link_count != 1 or path.suffix.lower() in PROTECTED_SUFFIXES:
                retained.append(
                    {
                        "path": str(path),
                        "references": [],
                        "reason": "hardlinked or protected file type",
                    }
                )
                continue
            project_id = next(
                (
                    item.slug
                    for item in project_registry.list_projects()
                    if Path(item.project_root).resolve() == root
                ),
                root.name,
            )
            kind = "legacy_scratch"
            artifact_id = lifecycle.stable_artifact_id(
                project_id, source["run_id"], source["step_id"], kind, relative
            )
            record = lifecycle.ArtifactRecord(
                artifact_id=artifact_id,
                kind=kind,
                project_id=project_id,
                run_id=source["run_id"],
                job_id=source["step_id"],
                path=str(path),
                created_at=source.get("recorded_at")
                or datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                identity=identity,
                retention_class="temporary",
                dependencies=(),
                pinned=False,
                regenerable=True,
                regeneration_method=(
                    f"rerun recorded producer step {source['step_id']}"
                ),
            )
            lifecycle.register_artifact(str(root), record)
            current = lifecycle._latest_artifacts(str(root)).get(artifact_id)
            if current is None:
                continue
            if current.get("state", lifecycle.CREATED) == lifecycle.CREATED:
                lifecycle.transition_artifact(
                    str(root),
                    artifact_id,
                    lifecycle.COMPLETED,
                    reason=(
                        f"observed provenance ties {relative} to run "
                        f"{source['run_id']} and step {source['step_id']}"
                    ),
                )
                current = lifecycle._latest_artifacts(str(root)).get(artifact_id)
            if current and current.get("state") == lifecycle.COMPLETED:
                lifecycle.transition_artifact(
                    str(root),
                    artifact_id,
                    lifecycle.DISPOSABLE,
                    reason="project scratch is regenerable and has no retained references",
                )
                current = lifecycle._latest_artifacts(str(root)).get(artifact_id)
            if current and current.get("state") == lifecycle.DISPOSABLE:
                registered.append(
                    {
                        "artifact_id": artifact_id,
                        "path": str(path),
                        "run_id": source["run_id"],
                        "step_id": source["step_id"],
                        "identity": identity.__dict__,
                    }
                )
    return {"registered": registered, "retained": retained, "automatic_deletion": False}


def _resolve_event_logs(projects: list[str]) -> list[Path]:
    from library.tools import event_log
    from library.tools.resolve_lock import instance_lock_dir

    paths = [event_log.log_path(project) for project in projects]
    paths.append(instance_lock_dir() / event_log.EVENT_LOG_FILE)
    return list(dict.fromkeys(Path(path) for path in paths))


def _resolve_identity_key(identity: dict) -> str:
    if not isinstance(identity, dict):
        return ""
    fields = ("object_type", "project_unique_id", "object_unique_id")
    return json.dumps({key: identity.get(key) for key in fields}, sort_keys=True)


def _resolve_census_status(row: dict, project_id: str, observed_keys: set[str]) -> str:
    if row.get("project_id") != project_id:
        return "outside_census_scope"
    if _resolve_identity_key(row.get("identity")) in observed_keys:
        return "present"
    return "orphaned_recorded_identity"


def resolve_legacy_census(projects: list[str]) -> dict:
    """Compare recorded Resolve identities with a leased live-project census.

    Only the currently open project is inspected: the API exposes that
    project's exact IDs without switching the captain's project context.
    Other recorded project IDs remain out of scope and are reported as
    unverified for explicit review, never inferred absent from their names.
    """
    from library.tools import event_log, resolve_lock

    rows_by_id: dict[str, dict] = {}
    legacy = []
    for log_path in _resolve_event_logs(projects):
        if not log_path.exists():
            continue
        for row in lifecycle.resolve_artifacts(log_path):
            rows_by_id[row["artifact_id"]] = row
        for event in event_log.read_events_path(log_path):
            if event.get("type") == "qualification_resolve_resource":
                legacy.append(
                    {
                        **event.get("fields", {}),
                        "verification": "unverified",
                        "reason": "legacy Resolve record requires manual identity review",
                    }
                )

    with resolve_lock.resolve_lease(
        "legacy Resolve lifecycle census",
        exclusive=True,
        timeout=15 * 60,
        description="Read exact recorded Resolve identities",
    ):
        from library.tools.marker_feedback import connect_resolve

        resolve = connect_resolve()
        manager = resolve.GetProjectManager()
        project = manager.GetCurrentProject()
        if project is None:
            raise lifecycle.LifecycleRefused(
                "Resolve has no current project; census cannot establish its scope"
            )
        project_id = str(project.GetUniqueId() or "")
        if not project_id:
            raise lifecycle.LifecycleRefused(
                "Resolve current project has no readable unique id"
            )
        project_name = str(project.GetName() or "")
        current_project = {
            "object_type": "project",
            "project_unique_id": project_id,
            "object_unique_id": project_id,
        }
        observed = {json.dumps(current_project, sort_keys=True): current_project}
        count = int(project.GetTimelineCount())
        for index in range(1, count + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is None:
                raise lifecycle.LifecycleRefused(
                    f"Resolve timeline {index} could not be read"
                )
            timeline_id = str(timeline.GetUniqueId() or "")
            if not timeline_id:
                raise lifecycle.LifecycleRefused(
                    f"Resolve timeline {index} has no readable unique id"
                )
            identity = {
                "object_type": "timeline",
                "project_unique_id": project_id,
                "object_unique_id": timeline_id,
            }
            observed[json.dumps(identity, sort_keys=True)] = identity

    observed_keys = {_resolve_identity_key(identity) for identity in observed.values()}
    census = []
    for artifact_id, row in sorted(rows_by_id.items()):
        identity = row.get("identity")
        status = _resolve_census_status(row, project_id, observed_keys)
        census.append(
            {
                "artifact_id": artifact_id,
                "identity": identity,
                "state": row.get("state"),
                "census": status,
                "automatic_deletion": False,
            }
        )
    return {
        "scope": {
            "project_unique_id": project_id,
            "project_name": project_name,
            "kind": "currently_open_project",
        },
        "recorded": census,
        "legacy_unverified": legacy,
        "review_required": len(legacy)
        + sum(row["census"] != "present" for row in census),
        "automatic_deletion": False,
    }


def _quarantine_settle_allowed(project: str, row: dict) -> bool:
    """Whether the lifecycle rules let an expired quarantine be settled.

    A quarantined artifact may be released back to DISPOSABLE only when
    nothing protects it: it is not pinned, nothing depends on it, its
    kind and its suffix are not the never-purged ones, and its area does
    not protect it. The file is NOT removed here - the reviewed storage
    plan is what removes it - so settling only ends the grace period.
    """
    if row.get("pinned") or row.get("dependencies"):
        return False
    if row.get("kind") in PROTECTED_KINDS:
        return False
    path = str(row.get("path") or "")
    if not path or Path(path).suffix.lower() in PROTECTED_SUFFIXES:
        return False
    if _protected_project_artifact(project, path):
        return False
    return not artifact_policy.is_protected_path(
        project, path, row.get("retention_class", ""))


def reconcile(
    projects: list[str],
    *,
    now: datetime | None = None,
    resolve_maintenance_window: bool = False,
) -> dict:
    """Reconcile the ownership records with the filesystem; deletion stays gated.

    Three things used to be REPORTED here forever, because nothing in the
    lifecycle could settle them:

    - a record whose file was replaced drifted for as long as the
      artifact lived, and the report named it on every start;
    - a quarantined artifact whose grace period had expired stayed
      quarantined, because nothing ever released it, so it was named on
      every start as well;
    - a refused cleanup job was re-queued on every start, which is a
      loop rather than a retry.

    Each is settled here under the rules that own it: a record follows
    the file Ren replaced, an expired quarantine is released to the
    reviewed storage plan, and a refused job is retried a bounded number
    of times before it is named as needing a person. Nothing is deleted
    here.
    """
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
        drift = []
        rebound = []
        for row in inventory["artifacts"]:
            if row.get("identity_matches") is not False:
                continue
            artifact_id = row["artifact_id"]
            try:
                identity = lifecycle.FileIdentity.capture(row["path"])
            except (OSError, lifecycle.LifecycleRefused):
                # The file is gone, or is no longer the regular file the
                # record names. Nothing can be said about the artifact
                # without inventing it, so it stays a finding.
                drift.append(artifact_id)
                continue
            if lifecycle.rebind_file(
                    project, artifact_id, identity=identity,
                    reason=("the file at the recorded path was replaced; "
                            "the record follows the file")):
                rebound.append(artifact_id)
        settled = []
        protected = []
        for row in inventory["artifacts"]:
            if row.get("state") != lifecycle.QUARANTINED:
                continue
            try:
                quarantined = datetime.fromisoformat(
                    quarantine_at[row["artifact_id"]])
            except (KeyError, TypeError, ValueError):
                continue
            if quarantined.tzinfo is None:
                quarantined = quarantined.replace(tzinfo=UTC)
            if now - quarantined < LEGACY_RETENTION:
                continue
            if not _quarantine_settle_allowed(project, row):
                protected.append(row["artifact_id"])
                continue
            lifecycle.transition_artifact(
                project,
                row["artifact_id"],
                lifecycle.DISPOSABLE,
                reason=("the quarantine grace period expired; the artifact "
                        "awaits the reviewed storage purge"),
            )
            settled.append(row["artifact_id"])
        recovered = lifecycle.recover_interrupted_cleanup_jobs(project)
        refusals = lifecycle.cleanup_job_refusals(project)
        retried = []
        exhausted = []
        for job in lifecycle._latest_cleanup_jobs(project).values():
            if job.get("state") != "refused":
                continue
            event_id = job["lifecycle_event_id"]
            tries = refusals.get(event_id, 0)
            if tries > MAX_CLEANUP_JOB_RETRIES:
                exhausted.append(event_id)
                continue
            lifecycle.transition_cleanup_job(
                project,
                job,
                "queued",
                outcome=(f"startup reconciliation retry {tries} of "
                         f"{MAX_CLEANUP_JOB_RETRIES} after fresh safety checks"),
            )
            retried.append(event_id)
        queued = lifecycle.pending_cleanup_jobs(project)
        results.append(
            {
                "project": str(Path(project).resolve()),
                "abandoned_jobs_recovered": recovered,
                "refused_jobs_retried": retried,
                "refused_jobs_needing_attention": exhausted,
                "pending_job_ids": [job["lifecycle_event_id"] for job in queued],
                "worker_required": bool(recovered or retried),
                "record_filesystem_drift": drift,
                "record_identity_rebound": rebound,
                "expired_quarantine_settled": settled,
                "expired_quarantine_protected": protected,
            }
        )
    report = {"projects": results, "automatic_deletion": False}
    if resolve_maintenance_window:
        report["resolve_census"] = resolve_legacy_census(projects)
    else:
        report["resolve_census"] = "deferred to maintenance window"
    return report


def unresolved_findings(report: dict) -> list[dict]:
    """The projects whose reconciliation still has something to say.

    A settled quarantine and a rebound record are RESOLVED: the machine
    has nothing left to act on there, and the startup check stays quiet
    about them. A drift the reconciliation could not settle, a
    quarantine the rules protect, and a cleanup job that needs a person
    are findings - they are what the operator has to look at.
    """
    findings = []
    for row in report.get("projects", []):
        if (row.get("record_filesystem_drift")
                or row.get("expired_quarantine_protected")
                or row.get("refused_jobs_retried")
                or row.get("refused_jobs_needing_attention")):
            findings.append(row)
    return findings


def startup_reconcile() -> dict:
    """Run the machine's lightweight check without depending on an edit job."""
    projects = [str(config.project_root) for config in project_registry.list_projects()]
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
    if (
        planned_path not in {current_path, original_path}
        or planned_identity != current_identity
    ):
        raise lifecycle.LifecycleRefused(
            f"storage manifest no longer matches artifact identity: {item['artifact_id']}"
        )


def pressure(free_bytes: int, reserve_bytes: int | None = None) -> str:
    """Classify free space against the configurable reserve."""
    reserve = (
        reserve_bytes
        if reserve_bytes is not None
        else int(os.environ.get("REN_MIN_FREE_BYTES", DEFAULT_FREE_RESERVE_BYTES))
    )
    if reserve <= 0:
        raise ValueError("REN_MIN_FREE_BYTES must be positive")
    if free_bytes < reserve:
        return "critical"
    if free_bytes < reserve * 1.5:
        return "elevated"
    return "normal"


def analysis_resources(project_folder: str) -> dict:
    """What a project's analysis consumed, read back from its own ledger.

    The ledger is the measurement the run already took, so this reads
    what is there and adds no walk of its own.

    Two axes, because the ledger records two. The CAPABILITY rows are
    the outer measurement: one row per capability execution, carrying
    the wall, the CPU, the peak memory and - where the front door
    measured it - the bytes the capability wrote. The SPAN rows are the
    inner measurement: one row per named layer, carrying the lifecycle
    phase and the bytes that layer read and wrote.

    A measurement that was never taken is reported as NOT TAKEN. A
    zero would read as "nothing was written", and that is a different
    fact.
    """
    from library.tools import perf_ledger

    path = perf_ledger.ledger_path(project_folder)
    rows = perf_ledger.read_rows(project_folder)
    capabilities: dict[str, dict] = {}
    phases: dict[str, dict] = {}
    capability_bytes_written = 0
    capability_bytes_measured = False
    phase_bytes_written = 0
    phase_bytes_read = 0
    phase_bytes_measured = False
    wall = cpu = 0.0
    peak_rss = 0.0
    for row in rows:
        kind = row.get("kind")
        if kind == perf_ledger.CAPABILITY:
            name = str(row.get("capability") or "")
            if not name:
                continue
            item = capabilities.setdefault(name, {
                "calls": 0, "wall_s": 0.0, "cpu_s": 0.0,
                "peak_rss_mb": 0.0, "bytes_written": 0})
            item["calls"] += 1
            item["wall_s"] = round(item["wall_s"] + float(row.get("wall_s") or 0.0), 3)
            item["cpu_s"] = round(item["cpu_s"] + float(row.get("cpu_s") or 0.0), 3)
            item["peak_rss_mb"] = max(
                item["peak_rss_mb"],
                float(row.get("self_peak_rss_mb") or 0.0),
                float(row.get("children_peak_rss_mb") or 0.0))
            wall += float(row.get("wall_s") or 0.0)
            cpu += float(row.get("cpu_s") or 0.0)
            peak_rss = max(peak_rss, item["peak_rss_mb"])
            if "bytes_written" in row:
                capability_bytes_measured = True
                written = int(row.get("bytes_written") or 0)
                capability_bytes_written += written
                item["bytes_written"] += written
        elif kind == perf_ledger.SPAN:
            phase = str(row.get("phase") or "")
            if not phase:
                continue
            item = phases.setdefault(phase, {
                "spans": 0, "wall_s": 0.0, "cpu_s": 0.0,
                "bytes_written": 0, "bytes_read": 0})
            item["spans"] += 1
            item["wall_s"] = round(item["wall_s"] + float(row.get("wall_s") or 0.0), 3)
            item["cpu_s"] = round(item["cpu_s"] + float(row.get("cpu_s") or 0.0), 3)
            if "bytes_written" in row:
                phase_bytes_measured = True
                written = int(row.get("bytes_written") or 0)
                phase_bytes_written += written
                item["bytes_written"] += written
            if "bytes_read" in row:
                phase_bytes_measured = True
                read = int(row.get("bytes_read") or 0)
                phase_bytes_read += read
                item["bytes_read"] += read
    return {
        "ledger": str(path),
        "ledger_present": path.is_file(),
        "rows": len(rows),
        "totals": {
            "wall_s": round(wall, 3),
            "cpu_s": round(cpu, 3),
            "peak_rss_mb": peak_rss,
            "capability_bytes_written": capability_bytes_written,
            "capability_bytes_measured": capability_bytes_measured,
            "phase_bytes_written": phase_bytes_written,
            "phase_bytes_read": phase_bytes_read,
            "phase_bytes_measured": phase_bytes_measured,
        },
        "by_capability": capabilities,
        "by_phase": phases,
    }


def _legacy_project_roots(projects: list[str] = ()) -> dict[str, list[Path]]:
    """The legacy roots each project owns, by the project's own path."""
    from library.tools.project_layout import Area, ProjectLayout

    owned: dict[str, list[Path]] = {}
    for project in project_registry.list_projects():
        layout = ProjectLayout(project.project_root)
        owned[str(Path(project.project_root).resolve())] = [
            layout.read_dir(Area.SCRATCH),
            layout.read_dir(Area.QUARANTINE),
            layout.read_dir(Area.REVIEW),
        ]
    for project in projects:
        root = Path(project).resolve()
        layout = ProjectLayout(root)
        owned[str(root)] = [
            layout.read_dir(Area.SCRATCH),
            layout.read_dir(Area.QUARANTINE),
            layout.read_dir(Area.REVIEW),
        ]
    return owned


def _legacy_owner(path: str, roots: dict[str, list[Path]]) -> str:
    """The project a legacy entry belongs to, or "" for the machine's.

    Both sides are resolved: the same file reached through a symlinked
    path is the same file, and a legacy entry that belongs to a project
    must be counted against that project rather than against the
    machine.
    """
    candidate = Path(path).resolve()
    for project, owned in roots.items():
        for root in owned:
            try:
                if candidate.is_relative_to(root.resolve()):
                    return project
            except (OSError, ValueError):
                continue
    return ""


def status(projects: list[str], *, disk_path: str | None = None) -> dict:
    """Summarize registered lifecycle artifacts and current disk pressure.

    The report answers PER PROJECT as well as for the machine: which
    project holds which bytes, which of them may be reclaimed, what its
    legacy entries cost, and what its analysis consumed.
    """
    entries = []
    by_kind: dict[str, dict[str, int]] = {}
    by_project: dict[str, dict] = {}
    seen: set[tuple[int, int]] = set()
    logical = physical = reclaimable = 0
    active_bytes = protected_bytes = 0
    for project in projects:
        resolved = str(Path(project).resolve())
        project_totals = by_project.setdefault(resolved, {
            "project": resolved,
            "artifacts": 0,
            "logical_bytes": 0,
            "physical_bytes": 0,
            "reclaimable_bytes": 0,
            "active_bytes": 0,
            "protected_bytes": 0,
            "by_kind": {},
        })
        snapshot = lifecycle.inventory(project)
        for row in snapshot["artifacts"]:
            entries.append({**row, "project": resolved})
            project_totals["artifacts"] += 1
            if not row["identity_matches"]:
                continue
            identity = row.get("identity") or {}
            size = int(identity.get("size_bytes", 0))
            kind = row["kind"]
            totals = by_kind.setdefault(
                kind, {"artifacts": 0, "logical_bytes": 0, "physical_bytes": 0}
            )
            project_kind = project_totals["by_kind"].setdefault(
                kind, {"artifacts": 0, "logical_bytes": 0, "physical_bytes": 0}
            )
            totals["artifacts"] += 1
            totals["logical_bytes"] += size
            project_kind["artifacts"] += 1
            project_kind["logical_bytes"] += size
            project_totals["logical_bytes"] += size
            logical += size
            inode = (int(identity["device"]), int(identity["inode"]))
            if inode not in seen:
                seen.add(inode)
                physical += size
                totals["physical_bytes"] += size
                project_kind["physical_bytes"] += size
                project_totals["physical_bytes"] += size
            if (
                row.get("state") == lifecycle.DISPOSABLE
                and row.get("ownership_known")
                and not row.get("pinned")
                and not row.get("dependencies")
                and row.get("regenerable")
            ):
                eligible_kind = kind not in PROTECTED_KINDS
                eligible_path = (
                    Path(row["path"]).suffix.lower() not in PROTECTED_SUFFIXES
                    and not _protected_project_artifact(project, row["path"])
                )
                eligible_area = not artifact_policy.is_protected_path(
                    project, row["path"], row.get("retention_class", ""))
                if (eligible_kind and eligible_path and eligible_area
                        and identity.get("link_count") == 1):
                    reclaimable += size
                    project_totals["reclaimable_bytes"] += size
            if row.get("state") in {
                lifecycle.CREATED,
                lifecycle.ACTIVE,
                lifecycle.PURGING,
            }:
                active_bytes += size
                project_totals["active_bytes"] += size
            elif row.get("state") not in {
                lifecycle.DISPOSABLE,
                lifecycle.QUARANTINED,
                lifecycle.PURGED,
            }:
                protected_bytes += size
    anchor = Path(disk_path or os.environ.get("PIPELINE_PROJECTS_ROOT") or Path.cwd())
    usage = shutil.disk_usage(anchor)
    oldest = sorted(
        (
            row
            for row in entries
            if row.get("ownership_known") and row.get("state") != lifecycle.PURGED
        ),
        key=lambda row: row.get("created_at", ""),
    )[:10]
    largest = sorted(
        (
            row
            for row in entries
            if row["identity_matches"]
            and row.get("state") == lifecycle.DISPOSABLE
            and row.get("regenerable")
            and not row.get("pinned")
            and not row.get("dependencies")
            and row.get("kind") not in PROTECTED_KINDS
            and Path(row["path"]).suffix.lower() not in PROTECTED_SUFFIXES
            and int((row.get("identity") or {}).get("link_count", 0)) == 1
        ),
        key=lambda row: int(row.get("identity", {}).get("size_bytes", 0)),
        reverse=True,
    )[:10]
    from library.tools.cache_lifecycle import CACHE_KIND_COST, CacheBudget

    cache_inodes: dict[tuple[int, int], int] = {}
    for row in entries:
        identity = row.get("identity") or {}
        if row["identity_matches"] and row["kind"] in CACHE_KIND_COST:
            key = (int(identity["device"]), int(identity["inode"]))
            cache_inodes.setdefault(key, int(identity["size_bytes"]))
    cache_bytes = sum(cache_inodes.values())
    cache_budget = CacheBudget.from_environment()
    legacy_sources = _legacy_roots(projects)
    legacy_owners = list(
        dict.fromkeys(
            [
                str(Path(config.project_root).resolve())
                for config in project_registry.list_projects()
            ]
            + [str(Path(project).resolve()) for project in projects]
        )
    )
    legacy_rows = _legacy_resources(legacy_sources, project_logs=legacy_owners)
    legacy_by_category: dict[str, dict[str, int]] = {}
    legacy_roots_by_project = _legacy_project_roots(projects)
    legacy_by_project: dict[str, dict[str, int]] = {}
    for row in legacy_rows:
        totals = legacy_by_category.setdefault(
            row["category"],
            {"entries": 0, "logical_bytes": 0, "physical_bytes": 0, "unverified": 0},
        )
        totals["entries"] += 1
        totals["logical_bytes"] += row.get("logical_bytes", 0)
        totals["physical_bytes"] += row.get("physical_bytes", 0)
        totals["unverified"] += row.get("verification") == "unverified"
        owner = _legacy_owner(str(row.get("path") or ""), legacy_roots_by_project)
        if not owner:
            continue
        owned = legacy_by_project.setdefault(
            owner,
            {"entries": 0, "logical_bytes": 0, "physical_bytes": 0,
             "unverified": 0},
        )
        owned["entries"] += 1
        owned["logical_bytes"] += row.get("logical_bytes", 0)
        owned["physical_bytes"] += row.get("physical_bytes", 0)
        owned["unverified"] += row.get("verification") == "unverified"
    for project, totals in by_project.items():
        totals["legacy"] = legacy_by_project.get(
            project,
            {"entries": 0, "logical_bytes": 0, "physical_bytes": 0,
             "unverified": 0})
        totals["analysis"] = analysis_resources(project)
    return {
        "artifacts": entries,
        "by_kind": by_kind,
        "by_project": by_project,
        "logical_bytes": logical,
        "physical_bytes": physical,
        "reclaimable_bytes": reclaimable,
        "active_bytes": active_bytes,
        "protected_bytes": protected_bytes,
        "oldest_retained": oldest,
        "largest_candidates": largest,
        "cache_bytes": cache_bytes,
        "cache_budget_bytes": cache_budget.total_bytes,
        "cache_budget_exceeded": cache_bytes > cache_budget.total_bytes,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "disk_pressure": pressure(usage.free),
        "legacy": {
            "entries": len(legacy_rows),
            "logical_bytes": sum(row.get("logical_bytes", 0) for row in legacy_rows),
            "physical_bytes": sum(row.get("physical_bytes", 0) for row in legacy_rows),
            "review_only": sum(
                row.get("verification") == "unverified" for row in legacy_rows
            ),
            "review_only_physical_bytes": sum(
                row.get("physical_bytes", 0)
                for row in legacy_rows
                if row.get("verification") == "unverified"
            ),
            "by_category": legacy_by_category,
            "by_project": legacy_by_project,
        },
    }


def require_free_space(required_bytes: int, *, path: str | None = None) -> dict:
    """Cheap preflight for storage-heavy work; refuse without deleting assets."""
    if required_bytes < 0:
        raise ValueError("required_bytes cannot be negative")
    anchor = Path(path or os.environ.get("PIPELINE_PROJECTS_ROOT") or Path.cwd())
    free = shutil.disk_usage(anchor).free
    reserve = int(os.environ.get("REN_MIN_FREE_BYTES", DEFAULT_FREE_RESERVE_BYTES))
    result = {
        "required_bytes": required_bytes,
        "free_bytes": free,
        "reserve_bytes": reserve,
        "disk_pressure": pressure(free, reserve),
    }
    if free - required_bytes < reserve:
        raise lifecycle.LifecycleRefused(
            f"disk pressure {result['disk_pressure']}: need {required_bytes} "
            f"bytes with {free} free and {reserve} reserved; protected assets "
            "were not changed"
        )
    return result


def estimate_render_space(
    reels: list[tuple[float, int, int]],
    *,
    output_bit_rate_bps: int | None = None,
    workspace_multiplier: float | None = None,
) -> int:
    """Estimate render outputs plus staging from approved durations and frame sizes."""
    try:
        bit_rate = int(
            output_bit_rate_bps
            or os.environ.get("REN_RENDER_ESTIMATED_BITRATE_BPS", "25000000")
        )
        multiplier = float(
            workspace_multiplier
            or os.environ.get("REN_RENDER_WORKSPACE_MULTIPLIER", "2.0")
        )
    except ValueError as exc:
        raise ValueError("invalid render storage estimate configuration") from exc
    if bit_rate <= 0 or multiplier < 1:
        raise ValueError("render estimate needs positive bitrate and multiplier >= 1")
    baseline_pixels = 1080 * 1920
    output_bytes = 0
    for duration, width, height in reels:
        if duration <= 0 or width <= 0 or height <= 0:
            raise ValueError(
                "render estimates require positive duration and dimensions"
            )
        scaled_rate = bit_rate * (width * height / baseline_pixels)
        output_bytes += int(duration * scaled_rate / 8)
    return int(output_bytes * multiplier)


def plan_cache_evictions(
    projects: list[str], *, pressure_state: str, cache_bytes: int | None = None
) -> list[dict]:
    """Plan value-aware cache eviction against one machine-wide budget."""
    from library.tools import cache_lifecycle

    base = cache_lifecycle.CacheBudget.from_environment()
    pressure_factor = {"normal": 1.0, "elevated": 0.8, "critical": 0.5}.get(
        pressure_state
    )
    if pressure_factor is None:
        raise ValueError(f"unknown disk pressure state: {pressure_state}")
    cache_kinds = cache_lifecycle.CACHE_KIND_COST
    project_bytes = {}
    project_class_bytes = {}
    for project in projects:
        rows = lifecycle.inventory(project)["artifacts"]
        sized = [
            row
            for row in rows
            if row.get("identity_matches")
            and row.get("ownership_known")
            and row.get("state") == lifecycle.CACHEABLE
            and row.get("kind") in cache_kinds
        ]
        project_bytes[project] = sum(
            int(row["identity"]["size_bytes"]) for row in sized
        )
        classes = {"expensive": 0, "inexpensive": 0}
        for row in sized:
            bucket = (
                "expensive"
                if cache_kinds[row["kind"]] in {"medium_high", "high"}
                else "inexpensive"
            )
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
        expensive = (
            int(base.expensive_bytes * share)
            if base.expensive_bytes is not None
            else None
        )
        inexpensive = (
            int(base.inexpensive_bytes * share)
            if base.inexpensive_bytes is not None
            else None
        )
        local_budget = cache_lifecycle.CacheBudget(
            total_bytes=max(1, int(target_total * share)),
            low_watermark=base.low_watermark,
            high_watermark=base.high_watermark,
            expensive_bytes=max(1, expensive) if expensive is not None else None,
            inexpensive_bytes=(
                max(1, inexpensive) if inexpensive is not None else None
            ),
        )
        planned = cache_lifecycle.plan_eviction(project, budget=local_budget)
        if planned["selected"]:
            plans.append({"project": project, "plan": planned})
    return plans


def _protected_project_artifact(project: str, path: str) -> bool:
    """Protect durable editorial state even if it was misregistered disposable."""
    root = Path(os.path.abspath(project))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    cursor = root
    if cursor.is_symlink():
        return True
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return True
    protected = {"versions", "recovery", "undo", "resolve"}
    return any(part.casefold() in protected for part in relative.parts)


def _in_quarantine_area(project: str, path: str) -> bool:
    """Whether a path already sits in the project's own quarantine area.

    A settled quarantine is DISPOSABLE and still sits in quarantine: its
    grace period expired and the file was not removed. The plan, the
    collect and the purge all have to read that as "already aside", not
    as a file waiting to be moved there a second time.
    """
    from library.tools.project_layout import Area, ProjectLayout

    try:
        root = Path(ProjectLayout(project).read_dir(Area.QUARANTINE)).resolve()
    except (OSError, ValueError):
        return False
    try:
        Path(os.path.abspath(path)).relative_to(root)
    except ValueError:
        return False
    return True


def _version_dependencies(project: str) -> tuple[set[str], str]:
    """The paths a retained generation still needs, and why they are unknown.

    An ARCHIVED GENERATION is a liveness root: a reel version that
    carries a recovery archive is a generation Ren can put back, and the
    media it names are what that restore plays. A file that is a
    dependency is not disposable, however the artifact ledger classified
    it.

    The reading FAILS CLOSED: a retained record that exists and cannot be
    read is not an empty record, and the caller must not name a file it
    cannot prove is unreferenced.
    """
    from library.tools import version_dependencies

    try:
        return version_dependencies.dependency_paths(project), ""
    except version_dependencies.VersionDependencyUnreadable as unreadable:
        return set(), str(unreadable)


def plan_document(projects: list[str]) -> dict:
    """Build an exact-identity manifest without writing it."""
    report = status(projects)
    candidates = []
    refused_policy = []
    refused_dependencies = []
    dependencies: dict[str, set[str]] = {}
    unreadable_projects: dict[str, str] = {}
    for project in projects:
        paths, problem = _version_dependencies(project)
        dependencies[str(Path(project).resolve())] = paths
        if problem:
            unreadable_projects[str(Path(project).resolve())] = problem
    for row in report["artifacts"]:
        if row["project"] in unreadable_projects:
            refused_dependencies.append({
                "artifact_id": row["artifact_id"],
                "path": row["path"],
                "kind": row["kind"],
                "reason": (
                    "a retained version record of this project could not be "
                    "read, so its media cannot be proved unreferenced: "
                    + unreadable_projects[row["project"]]),
            })
            continue
        if row["path"] in dependencies.get(row["project"], set()):
            refused_dependencies.append({
                "artifact_id": row["artifact_id"],
                "path": row["path"],
                "kind": row["kind"],
                "reason": ("a retained generation of this project still "
                           "needs this file; it is a version dependency"),
            })
            continue
        if not (
            row["identity_matches"]
            and row["ownership_known"]
            and row.get("state") == lifecycle.DISPOSABLE
            and row.get("regenerable")
            and not row.get("pinned")
            and not row.get("dependencies")
            and not _protected_project_artifact(row["project"], row["path"])
            and int((row.get("identity") or {}).get("link_count", 0)) == 1
            and row.get("kind") not in PROTECTED_KINDS
            and Path(row["path"]).suffix.lower() not in PROTECTED_SUFFIXES
        ):
            continue
        # The AREA has the last word. A record that named a disposable
        # class for a file in `exports/` does not make a delivered render
        # disposable, and the refusal is reported rather than dropped.
        if artifact_policy.is_protected_path(
                row["project"], row["path"], row.get("retention_class", "")):
            refused_policy.append({
                "artifact_id": row["artifact_id"],
                "path": row["path"],
                "kind": row["kind"],
                "retention_class": artifact_policy.effective_class(
                    row["project"], row["path"], row.get("retention_class", "")),
                "reason": artifact_policy.policy_for_path(
                    row["project"], row["path"]).why,
            })
            continue
        candidates.append(
            {
                key: row[key]
                for key in (
                    "artifact_id",
                    "project",
                    "path",
                    "identity",
                    "kind",
                    "run_id",
                    "job_id",
                )
            }
        )
    cache_evictions = plan_cache_evictions(
        projects,
        pressure_state=report["disk_pressure"],
        cache_bytes=report["cache_bytes"],
    )
    owned = [str(Path(project).resolve()) for project in projects]
    _collectable, refused = _git_guard.partition(
        [row["path"] for row in candidates], owned_roots=owned)
    refused_by_path = {hit.path: hit for hit in refused}
    refused_git = [
        {"artifact_id": row["artifact_id"], "path": row["path"],
         "resolved_path": refused_by_path[row["path"]].resolved_path,
         "reason": refused_by_path[row["path"]].reason,
         "work_tree": refused_by_path[row["path"]].work_tree,
         "detail": _git_guard.describe(refused_by_path[row["path"]])}
        for row in candidates if row["path"] in refused_by_path]
    candidates = [row for row in candidates
                  if row["path"] not in refused_by_path]
    document = {
        "format": "ren-storage-plan/1",
        "candidates": candidates,
        "refused_git": refused_git,
        "refused_policy": refused_policy,
        "refused_dependencies": refused_dependencies,
        "cache_evictions": cache_evictions,
        "logical_bytes": sum(row["identity"]["size_bytes"] for row in candidates),
    }
    return document


def _assert_not_version_dependency(project: str, path: str) -> None:
    """Refuse to remove a file a retained generation still needs.

    Re-proved at the moment of removal, not carried over from the plan:
    the plan was a claim about an earlier moment and this is the call that
    cannot be undone.
    """
    dependencies, problem = _version_dependencies(project)
    if problem:
        raise lifecycle.LifecycleRefused(
            f"a retained version record of {project} could not be read, so "
            f"the media of its generations cannot be proved unreferenced: "
            f"{problem}")
    if path in dependencies:
        raise lifecycle.LifecycleRefused(
            f"a retained generation of {project} still needs {path}; it is "
            f"a version dependency and is not disposable")


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
                f"artifact ownership disappeared: {item['artifact_id']}"
            )
        _assert_plan_binding(item, latest)
        if latest.get("state") == lifecycle.PURGED:
            results.append(
                {
                    "artifact_id": item["artifact_id"],
                    "path": latest["path"],
                    "logical_bytes": 0,
                    "physical_bytes": 0,
                    "already_purged": True,
                }
            )
            continue
        if (
            latest.get("state") != lifecycle.QUARANTINED
            and not (latest.get("state") == lifecycle.DISPOSABLE
                     and _in_quarantine_area(project, latest["path"]))
            or latest.get("kind") in PROTECTED_KINDS
            or Path(latest["path"]).suffix.lower() in PROTECTED_SUFFIXES
        ):
            raise lifecycle.LifecycleRefused(
                f"artifact is not eligible for permanent purge: {item['artifact_id']}"
            )
        if artifact_policy.is_protected_path(
                project, latest["path"], latest.get("retention_class", "")):
            raise lifecycle.LifecycleRefused(
                f"artifact area is protected from permanent purge: "
                f"{item['artifact_id']}"
            )
        _assert_not_version_dependency(project, latest["path"])
        fields = {
            key: latest[key] for key in lifecycle.ArtifactRecord.__dataclass_fields__
        }
        fields["identity"] = lifecycle.FileIdentity(**fields["identity"])
        fields["dependencies"] = tuple(fields.get("dependencies") or ())
        record = lifecycle.ArtifactRecord(**fields)
        results.append(
            lifecycle.purge_file(project, record, str(Path(project).resolve()))
        )
    return {
        "manifest": str(Path(manifest).resolve()),
        "purged": results,
        "logical_bytes": sum(item["logical_bytes"] for item in results),
    }


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
                moved.append(
                    {
                        "artifact_id": item["artifact_id"],
                        "path": current["path"],
                        "already_quarantined": True,
                    }
                )
                continue
            raise lifecycle.LifecycleRefused(
                f"artifact is no longer disposable: {item['artifact_id']}"
            )
        _assert_plan_binding(item, current)
        if artifact_policy.is_protected_path(
                project, current["path"], current.get("retention_class", "")):
            raise lifecycle.LifecycleRefused(
                f"artifact area is protected from cleanup: {item['artifact_id']}")
        _assert_not_version_dependency(project, current["path"])
        if _in_quarantine_area(project, current["path"]):
            # A settled quarantine is already aside: its grace period
            # expired and it waits in the reviewed plan for the purge.
            # Moving it again would refuse on its own destination.
            moved.append(
                {
                    "artifact_id": item["artifact_id"],
                    "path": current["path"],
                    "already_quarantined": True,
                }
            )
            continue
        fields = {
            key: current[key] for key in lifecycle.ArtifactRecord.__dataclass_fields__
        }
        fields["identity"] = lifecycle.FileIdentity(**fields["identity"])
        fields["dependencies"] = tuple(fields.get("dependencies") or ())
        record = lifecycle.ArtifactRecord(**fields)
        moved.append(
            lifecycle.quarantine_file(project, record, str(Path(project).resolve()))
        )
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
                    f"cache ownership disappeared: {artifact_id}"
                )
            values = {
                key: fields[key]
                for key in lifecycle.ArtifactRecord.__dataclass_fields__
            }
            values["identity"] = lifecycle.FileIdentity(**values["identity"])
            values["dependencies"] = tuple(values.get("dependencies") or ())
            record = lifecycle.ArtifactRecord(**values)
            records[artifact_id] = record
            cleanup_roots[artifact_id] = str(Path(record.path).parent)
        cache_evicted.extend(
            cache_lifecycle.apply_eviction(
                project,
                entry["plan"],
                cleanup_root=project,
                records=records,
                cleanup_roots=cleanup_roots,
            )
        )
    return {
        "manifest": str(Path(manifest).resolve()),
        "quarantined": moved,
        "cache_evicted": cache_evicted,
    }


def restore(manifest: str) -> dict:
    """Restore only extant quarantined entries; this lane creates no quarantine."""
    document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if document.get("format") != "ren-storage-plan/1":
        raise lifecycle.LifecycleRefused("unknown storage manifest format")
    restored = []
    for item in document.get("candidates", []):
        latest = lifecycle._latest_artifacts(item["project"]).get(item["artifact_id"])
        settled = (
            latest is not None
            and latest.get("state") == lifecycle.DISPOSABLE
            and latest.get("original_path")
            and _in_quarantine_area(item["project"], latest["path"]))
        if latest is not None and latest.get("state") == lifecycle.DISPOSABLE \
                and not settled:
            _assert_plan_binding(item, latest)
            restored.append(
                {
                    "artifact_id": item["artifact_id"],
                    "path": latest["path"],
                    "already_restored": True,
                }
            )
            continue
        if latest is None or (
                latest.get("state") != lifecycle.QUARANTINED and not settled):
            raise lifecycle.LifecycleRefused(
                f"artifact is not a known quarantined resource: {item['artifact_id']}"
            )
        _assert_plan_binding(item, latest)
        restored.append(lifecycle.restore_file(item["project"], item["artifact_id"]))
    return {"manifest": str(Path(manifest).resolve()), "restored": restored}

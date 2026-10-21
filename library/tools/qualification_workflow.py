"""A repeatable, real-footage workflow qualification for Ren.

The source selection is read from a prebuilt footage index. Source files are
copied into the qualification media store, analyzed in a disposable collection,
and placed only into the exact ``Ren Qualification`` Resolve project through
the broker. Each run writes a dated, stage-by-stage evidence report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

from library.tools import perf_ledger
from library.tools import qualification_project as qp

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_TIMEOUT_SECONDS = 8 * 60 * 60
RENDER_TIMEOUT_SECONDS = 600
MAX_SOURCE_SECONDS = 300.0
DEFAULT_QUERY = "marketing directors talking about marketing"
FAILED_REPRODUCTION_TTL_DAYS = 7


def _lifecycle_project(root: Path) -> Path:
    """The run directory is the owner for its event-sourced artifact records."""
    from library.tools.project_layout import Area, ProjectLayout

    ProjectLayout(root).write_dir(Area.LOGS)
    return root


def _run_artifact_records(root: Path, run_stamp: str) -> list:
    """Register every run-private file; shared inputs and evidence stay out."""
    from library.tools import artifact_lifecycle as lifecycle

    owner = _lifecycle_project(root)
    excluded = {"qualification-report.json", "perf_ledger.jsonl",
                "qualification-cleanup-manifest.json", "resolve-cleanup",
                "pipeline_output"}
    records = []
    for base, dirs, files in os.walk(root, followlinks=False):
        base_path = Path(base)
        for name in list(dirs):
            path = base_path / name
            if path.relative_to(root).parts[0] in excluded:
                dirs.remove(name)
                continue
            if path.is_symlink():
                raise lifecycle.LifecycleRefused(
                    f"unknown symlink in qualification output: {path}")
        for name in files:
            path = base_path / name
            if path.relative_to(root).parts[0] in excluded:
                continue
            identity = lifecycle.FileIdentity.capture(path)
            relative = path.relative_to(root).as_posix()
            kind = ("qualification_export" if relative.startswith("exports/")
                    else "qualification_intermediate")
            record = lifecycle.ArtifactRecord(
                artifact_id=lifecycle.stable_artifact_id(
                    "qualification-workflow", run_stamp, run_stamp, kind,
                    relative),
                kind=kind, project_id="qualification-workflow",
                run_id=run_stamp, job_id=run_stamp, path=str(path),
                created_at=datetime.now(UTC).isoformat(), identity=identity,
                retention_class="run_private", dependencies=(),
                pinned=False, regenerable=True,
                regeneration_method="ren qualification workflow")
            lifecycle.register_artifact(str(owner), record)
            lifecycle.transition_artifact(
                str(owner), record.artifact_id, lifecycle.COMPLETED,
                reason="qualification run reached final render QA")
            records.append(record)
    return records


def _register_shared_artifact(root: Path, run_stamp: str, path: Path,
                              *, kind: str, dependency: str = "") -> dict:
    """Record a reusable artifact as cacheable, never run-private."""
    from library.tools import artifact_lifecycle as lifecycle

    identity = lifecycle.FileIdentity.capture(path)
    record = lifecycle.ArtifactRecord(
        artifact_id=lifecycle.stable_artifact_id(
            "qualification-workflow", run_stamp, run_stamp, kind,
            f"{identity.device}:{identity.inode}:{path}"),
        kind=kind, project_id="qualification-workflow", run_id=run_stamp,
        job_id=run_stamp, path=str(path),
        created_at=datetime.now(UTC).isoformat(), identity=identity,
        retention_class="shared_reusable", dependencies=((dependency,)
                                                          if dependency else ()),
        pinned=False, regenerable=True,
        regeneration_method="ren qualification workflow")
    owner = _lifecycle_project(root)
    lifecycle.register_artifact(str(owner), record)
    lifecycle.transition_artifact(
        str(owner), record.artifact_id, lifecycle.COMPLETED,
        reason="shared reusable qualification input recorded")
    lifecycle.transition_artifact(
        str(owner), record.artifact_id, lifecycle.CACHEABLE,
        reason="shared content-addressed artifact remains reusable")
    return {"artifact_id": record.artifact_id, "path": str(path),
            "sha256": _file_sha256(path)}


def _register_new_semantic_measurements(root: Path, run_stamp: str,
                                        before: set[Path],
                                        semantic_root: Path) -> list[dict]:
    if not semantic_root.exists():
        return []
    records = []
    for path in sorted(semantic_root.rglob("*")):
        if path.is_file() and path not in before:
            records.append(_register_shared_artifact(
                root, run_stamp, path, kind="semantic_measurement",
                dependency="shared-semantic-measurement"))
    return records


def _reconcile_prior_runs(current_root: Path) -> list[dict]:
    """Resume recorded file cleanup jobs and hold interrupted or legacy runs."""
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools.artifact_lifecycle import CLEANUP_JOB
    from library.tools.event_log import emit, emit_once, read_events

    results = []
    parent = current_root.parent
    for root in sorted(parent.iterdir()):
        if root == current_root or not root.is_dir():
            continue
        try:
            events = read_events(str(root))
        except Exception as exc:  # noqa: BLE001 - unknown ownership is held
            results.append({"path": str(root), "status": "unreadable",
                            "detail": str(exc)})
            continue
        run_events = [event for event in events
                      if event.get("type") == "qualification_run_lifecycle"]
        if not run_events:
            results.append({"path": str(root), "status": "legacy_unowned"})
            continue
        latest = run_events[-1].get("fields", {})
        if latest.get("state") == "active":
            active = latest.get("host") == socket.gethostname()
            if active:
                try:
                    os.kill(int(latest["pid"]), 0)
                except (OSError, KeyError, TypeError, ValueError):
                    active = False
            if not active:
                emit(str(root), "qualification_run_lifecycle",
                     task=latest["run_id"], run_id=latest["run_id"],
                     job_id=latest["job_id"], state="recovery_hold",
                     reason="process ended before recording a terminal run state",
                     pinned=bool(latest.get("pinned")))
                results.append({"path": str(root), "status": "recovery_hold"})
        if (latest.get("state") == "failed" and not latest.get("pinned")
                and latest.get("finished_at")):
            finished = datetime.fromisoformat(latest["finished_at"])
            age = datetime.now(UTC) - finished
            if age.total_seconds() >= FAILED_REPRODUCTION_TTL_DAYS * 86400:
                try:
                    artifacts = lifecycle._latest_artifacts(str(root))
                    owned = {Path(row["path"]).resolve()
                             for row in artifacts.values()
                             if row.get("run_id") == latest["run_id"]}
                    reserved = {root / "qualification-report.json",
                                root / "perf_ledger.jsonl"}
                    unowned = []
                    for base, dirs, files in os.walk(root, followlinks=False):
                        for name in dirs + files:
                            path = Path(base) / name
                            if (path.is_symlink()
                                    or (path.is_file() and path not in reserved
                                        and path not in owned
                                        and path.relative_to(root).parts[0]
                                        != "pipeline_output")):
                                unowned.append(str(path))
                    if unowned:
                        raise lifecycle.LifecycleRefused(
                            f"unregistered failed-run resources: {unowned[:3]}")
                    expired = []
                    for artifact_id, row in artifacts.items():
                        if (row.get("run_id") != latest["run_id"]
                                or row.get("state") != lifecycle.RECOVERY_HOLD):
                            continue
                        path = Path(row["path"])
                        if path.suffix.lower() in {".drt", ".drp", ".otio"}:
                            continue
                        record_data = {key: value for key, value in row.items()
                                       if key in lifecycle.ArtifactRecord.__dataclass_fields__}
                        record_data["identity"] = lifecycle.FileIdentity(
                            **record_data["identity"])
                        record_data["dependencies"] = tuple(
                            record_data["dependencies"])
                        record = lifecycle.ArtifactRecord(**record_data)
                        lifecycle.transition_artifact(
                            str(root), artifact_id, lifecycle.DISPOSABLE,
                            reason="failed reproduction retention expired")
                        expired.append(lifecycle.purge_file(
                            str(root), record, str(root)))
                    emit(str(root), "qualification_run_lifecycle",
                         task=latest["run_id"], run_id=latest["run_id"],
                         job_id=latest["job_id"], state="expired",
                         reason="unpinned failed-run artifacts exceeded retention")
                    results.append({"path": str(root), "status": "expired",
                                    "removed": expired})
                except Exception as exc:  # noqa: BLE001 - unknown stays held
                    results.append({"path": str(root),
                                    "status": "expiration_refused",
                                    "detail": str(exc)})
        try:
            jobs = lifecycle.pending_cleanup_jobs(str(root))
            if jobs:
                manifest = root / "qualification-cleanup-manifest.json"
                if not manifest.is_file():
                    results.append({"path": str(root), "status": "cleanup_refused",
                                    "detail": "pending cleanup manifest is missing"})
                    continue
                manifest_data = json.loads(
                    manifest.read_text(encoding="utf-8"))
                for job in jobs:
                    if job.get("resolve_timelines"):
                        timeline_cleanup = _broker_call(
                            "qualification.workflow",
                            {"action": "cleanup",
                             "project": "Ren Qualification",
                             "run_stamp": job["run_id"],
                             "timelines": job["resolve_timelines"],
                             "cleanup_dir": str(root / "resolve-cleanup")},
                            idempotency_key=(
                                f"qualification-workflow-cleanup:{job['run_id']}"),
                            wait_seconds=RENDER_TIMEOUT_SECONDS + 900)
                        manifest_data["resolve_cleanup"] = (
                            timeline_cleanup["result"])
                        manifest.write_text(
                            json.dumps(manifest_data, indent=2) + "\n",
                            encoding="utf-8")
                if "artifacts" not in manifest_data:
                    raise lifecycle.LifecycleRefused(
                        "cleanup manifest has no filesystem ownership plan")
                lifecycle_ids = {row["artifact_id"]
                                 for row in manifest_data["artifacts"]}
                registered_ids = {
                    artifact_id for artifact_id, row
                    in lifecycle._latest_artifacts(str(root)).items()
                    if row.get("run_id") == jobs[0]["run_id"]
                    and row.get("kind") in {
                        "qualification_export", "qualification_intermediate"}}
                if lifecycle_ids != registered_ids:
                    raise lifecycle.LifecycleRefused(
                        "cleanup manifest and registered artifacts disagree")
                records = lifecycle._latest_artifacts(str(root))
                for job in jobs:
                    run_id = job["run_id"]
                    pending = [row for row in records.values()
                               if row.get("run_id") == run_id
                               and row.get("kind") in {
                                   "qualification_export",
                                   "qualification_intermediate"}]
                    planned = {row["artifact_id"]: row
                               for row in manifest_data["artifacts"]}
                    matched = {row["artifact_id"]: row for row in pending}
                    if set(planned) != set(matched):
                        raise lifecycle.LifecycleRefused(
                            "cleanup manifest and registered artifacts disagree")
                    for artifact_id, row in matched.items():
                        plan_row = planned[artifact_id]
                        if (plan_row["path"] != row["path"]
                                or plan_row["identity"] != row["identity"]):
                            raise lifecycle.LifecycleRefused(
                                f"cleanup plan identity changed: {artifact_id}")
                    removed = []
                    for row in pending:
                        record_data = {key: value for key, value in row.items()
                                       if key in lifecycle.ArtifactRecord.__dataclass_fields__}
                        record_data["identity"] = lifecycle.FileIdentity(
                            **record_data["identity"])
                        record_data["dependencies"] = tuple(
                            record_data["dependencies"])
                        record = lifecycle.ArtifactRecord(**record_data)
                        if row["state"] == lifecycle.PURGED:
                            continue
                        if row["state"] == lifecycle.COMPLETED:
                            lifecycle.transition_artifact(
                                str(root), record.artifact_id,
                                lifecycle.DISPOSABLE,
                                reason="recovered successful-run cleanup")
                        removed.append(lifecycle.purge_file(
                            str(root), record, str(root)))
                    manifest_data["status"] = "complete"
                    manifest_data["removed"] = removed
                    manifest.write_text(
                        json.dumps(manifest_data, indent=2) + "\n",
                        encoding="utf-8")
                    emit_once(
                        str(root), CLEANUP_JOB,
                        f"cleanup:qualification:{run_id}:complete",
                        task=run_id,
                        lifecycle_event_id=f"qualification:{run_id}",
                        project_id="qualification-workflow", run_id=run_id,
                        job_id=run_id,
                        reason="recovered all registered file deletions",
                        state="complete")
                    emit(str(root), "qualification_run_lifecycle",
                         task=run_id, run_id=run_id, job_id=job["job_id"],
                         state="completed",
                         reason="recovered and verified pending cleanup")
                results.append({"path": str(root), "status": "cleanup_resumed"})
        except Exception as exc:  # noqa: BLE001 - unknown state remains held
            results.append({"path": str(root), "status": "cleanup_refused",
                            "detail": str(exc)})
    return results


def set_failed_run_pin(run_stamp: str, *, pinned: bool) -> dict:
    """Pin or unpin one exact failed run's reproduction set."""
    from library.tools.event_log import emit, read_events

    root = (qp.media_dir() / "workflow-runs" / run_stamp).resolve()
    parent = (qp.media_dir() / "workflow-runs").resolve()
    if not root.is_relative_to(parent) or not root.is_dir():
        raise WorkflowQualificationError("failed workflow run is unknown")
    events = read_events(str(root))
    runs = [event for event in events
            if event.get("type") == "qualification_run_lifecycle"]
    if not runs:
        raise WorkflowQualificationError("run has no lifecycle ownership record")
    latest = runs[-1]["fields"]
    if latest.get("state") not in {"failed", "recovery_hold"}:
        raise WorkflowQualificationError("only a failed or held run can be pinned")
    emit(str(root), "qualification_run_lifecycle", task=run_stamp,
         run_id=run_stamp, job_id=run_stamp, state=latest["state"],
         finished_at=latest.get("finished_at"), pinned=bool(pinned),
         reason="explicit reproduction retention pin update")
    return {"run_id": run_stamp, "pinned": bool(pinned)}


def _retire_successful_run_files(root: Path, run_stamp: str) -> dict:
    """Plan and retire only registered run-private files after successful QA."""
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools.artifact_lifecycle import CLEANUP_JOB
    from library.tools.event_log import emit_once

    records = _run_artifact_records(root, run_stamp)
    owner = _lifecycle_project(root)
    if not lifecycle.pending_cleanup_jobs(str(owner)):
        lifecycle.schedule_cleanup_job(
            str(owner), event_id=f"qualification:{run_stamp}",
            project_id="qualification-workflow", run_id=run_stamp,
            job_id=run_stamp, reason="successful final render QA")
    plan = [{"artifact_id": record.artifact_id, "path": record.path,
             "identity": record.identity.__dict__}
            for record in records]
    manifest_path = root / "qualification-cleanup-manifest.json"
    existing = {}
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = {**existing, "status": "planned", "run_id": run_stamp,
              "created_at": datetime.now(UTC).isoformat(),
              "artifacts": plan}
    manifest_path.write_text(json.dumps(report, indent=2) + "\n",
                             encoding="utf-8")
    removed = []
    for record in records:
        lifecycle.transition_artifact(
            str(owner), record.artifact_id, lifecycle.DISPOSABLE,
            reason="run-private artifact after successful QA")
    report["status"] = "applying"
    manifest_path.write_text(json.dumps(report, indent=2) + "\n",
                             encoding="utf-8")
    for record in records:
        removed.append(lifecycle.purge_file(
            str(owner), record, str(root)))
    for base, dirs, _ in os.walk(root, topdown=False):
        directory = Path(base)
        if directory == root or directory.name == "pipeline_output":
            continue
        try:
            directory.rmdir()
        except OSError:
            pass
    emit_once(str(owner), CLEANUP_JOB, f"cleanup:qualification:{run_stamp}:complete",
              task=run_stamp, lifecycle_event_id=f"qualification:{run_stamp}",
              project_id="qualification-workflow", run_id=run_stamp,
              job_id=run_stamp, reason="all registered files verified and retired",
              state="complete")
    report["status"] = "complete"
    report["removed"] = removed
    manifest_path.write_text(json.dumps(report, indent=2) + "\n",
                             encoding="utf-8")
    return {"status": "complete", "plan": plan, "removed": removed,
            "manifest": str(manifest_path),
            "preserved": [str(root / "qualification-report.json"),
                          str(root / "perf_ledger.jsonl"),
                          str(manifest_path)]}


class WorkflowQualificationError(RuntimeError):
    """The real-footage workflow did not pass every required stage."""


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(report, indent=2, default=str) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _stage(report: dict, name: str, *, passed: bool, evidence) -> None:
    report["stages"].append({"name": name,
                             "status": "passed" if passed else "failed",
                             "evidence": evidence})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timeline_fingerprint(project, timeline) -> str:
    """Fingerprint the full quick-read picture for edit-preservation checks."""
    from library.tools import timeline_shadow

    snapshot = timeline_shadow.read_live(project, timeline)
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_analysis(copy_path: Path, run_dir: Path, run_id: str) -> dict:
    projects = run_dir / "projects"
    memory = run_dir / "source-memory"
    slug = f"qualification-{run_id.lower().replace('_', '-')[:28]}"
    command = [str(REPO_ROOT / "bin" / "ren"), "analyze",
               str(copy_path.parent), "--into", str(projects),
               "--slug", slug, "--memory-root", str(memory), "--json",
               # The analysis runs the normal capability composition:
               # semantic analysis first, then the temporal index over
               # its profiles (CAP-01, qualified 2026-10-15 on a
               # temporary project with every capability built), then
               # prosody over the temporal indices. No lane is skipped;
               # a refusal or failure in any of them fails this stage.
               ]
    # Each workflow run gets private project/source-memory outputs, but
    # semantic layer measurements are content addressed and safe to share
    # across the repeated runs (and later workflow invocations). This lets
    # unchanged footage reuse its model observations without sharing any
    # project state or timeline output.
    env = os.environ.copy()
    env.setdefault("PIPELINE_SEMANTIC_MEASUREMENT_ROOT",
                   str(run_dir.parents[1] / "semantic-measurements"))
    with perf_ledger.span("ren_analysis_cli", subprocesses=1, calls=1):
        result = subprocess.run(
            command, cwd=REPO_ROOT, env=env,
            capture_output=True, text=True,
            encoding="utf-8", timeout=ANALYSIS_TIMEOUT_SECONDS, check=False)
    evidence = {"command": command, "exit_code": result.returncode,
                "stdout_tail": result.stdout[-12000:],
                "stderr_tail": result.stderr[-12000:]}
    if result.returncode != 0:
        raise WorkflowQualificationError(
            f"real-footage analysis failed with exit {result.returncode}: "
            f"{result.stderr[-1500:]}" )
    try:
        record = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowQualificationError(
            "ren analyze did not return its JSON run record") from exc
    if record["status"] != "complete":
        raise WorkflowQualificationError(
            f"ren analyze recorded status {record['status']!r}")
    project_folder = Path(record["project_folder"]).resolve()
    index_file = project_folder / "pipeline_output" / "scratch" \
        / "footage_index" / "footage_index.json"
    if not index_file.is_file():
        raise WorkflowQualificationError(
            f"analysis did not build its footage index: {index_file}")
    evidence.update({"run_record": record, "analysis_project": str(project_folder),
                     "index": str(index_file)})
    return {"project_folder": project_folder,
            "index_file": index_file, "evidence": evidence}


def _analyzed_hit(index_file: Path, query: str) -> dict:
    from library.tools.analysis.footage_query import FootageIndex

    payload = json.loads(index_file.read_text(encoding="utf-8"))
    index = FootageIndex(payload["project_folder"],
                         index_dir=index_file.parent)
    result = index.search_report(query, top_k=20, mode="hybrid")
    if result.get("error") or not result["results"]:
        raise WorkflowQualificationError(
            f"fresh analysis index found no usable speech for {query!r}: "
            f"{result.get('error') or 'no hits'}")
    for hit in result["results"]:
        if hit["start"] >= 0 and hit["end"] > hit["start"]:
            return {"hit": {key: hit[key] for key in
                             ("clip_id", "start", "end", "score", "dense")
                             if key in hit}, "query": query,
                    "index": str(index_file),
                    "index_project": payload["project_folder"]}
    raise WorkflowQualificationError("analysis index returned no timed span")


def _check_project_standard(project) -> dict:
    """Read the Qualification project standard back; the workflow never
    mutates project settings under existing timelines. The engine refuses
    that write, and the reset fixture owns the 24fps 1080p standard."""
    try:
        rate_raw = project.GetSetting("timelineFrameRate")
        width_raw = project.GetSetting("timelineResolutionWidth")
        height_raw = project.GetSetting("timelineResolutionHeight")
    except (AttributeError, TypeError, ValueError) as exc:
        raise WorkflowQualificationError(
            f"could not read the Qualification project standard: {exc}"
        ) from exc
    try:
        rate, width, height = (float(rate_raw), int(width_raw),
                               int(height_raw))
    except (TypeError, ValueError):
        rate = width = height = None
    if (rate is None or abs(rate - 24.0) > 0.0001
            or width != 1920 or height != 1080):
        raise WorkflowQualificationError(
            "Ren Qualification project standard drifted: frame rate "
            f"{rate_raw!r}, {width_raw!r}x{height_raw!r} (want 24, "
            "1920x1080); run `ren qualification reset` first")
    return {"timelineFrameRate": rate, "timelineResolutionWidth": width,
            "timelineResolutionHeight": height}


def _check_timeline_inherits_standard(timeline) -> dict:
    """Read the new timeline's inherited shape back and require the
    project standard. Three consecutive live runs refused a custom
    timeline rate (menu label and full precision alike) while the
    timeline kept the project rate, so the cut rides the 24fps project
    standard instead: source frames are indexed at the media rate, record
    extents match 1:1, and the 0.1% rate drift sits a hundredfold inside
    render QA's duration tolerance."""
    try:
        rate_raw = timeline.GetSetting("timelineFrameRate")
        width_raw = timeline.GetSetting("timelineResolutionWidth")
        height_raw = timeline.GetSetting("timelineResolutionHeight")
    except (AttributeError, TypeError, ValueError) as exc:
        raise WorkflowQualificationError(
            f"could not read the new timeline shape: {exc}") from exc
    try:
        rate, width, height = (float(rate_raw), int(width_raw),
                               int(height_raw))
    except (TypeError, ValueError):
        rate = width = height = None
    if (rate is None or abs(rate - 24.0) > 0.0001
            or width != 1920 or height != 1080):
        raise WorkflowQualificationError(
            "new timeline did not inherit the project standard: frame "
            f"rate {rate_raw!r}, {width_raw!r}x{height_raw!r} (want 24, "
            "1920x1080)")
    return {"timelineFrameRate": rate, "timelineResolutionWidth": width,
            "timelineResolutionHeight": height}


def _probe_source_frames(source: Path) -> tuple:
    """Media rate and total frames from the local copy, never from the
    pool item: Resolve's scripting methods differ by media type (the MXF
    essence item exposes no GetLeftOffset), while ffprobe reads the file
    the SHA-256 already covers."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=avg_frame_rate,codec_type", "-of", "json",
         str(source)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    if probe.returncode != 0:
        raise WorkflowQualificationError(
            f"ffprobe could not read the qualification copy: {source.name}")
    try:
        measured = json.loads(probe.stdout)
        duration = float(measured["format"]["duration"])
        rate_text = next(row["avg_frame_rate"]
                         for row in measured.get("streams", [])
                         if row.get("codec_type") == "video")
        numerator, _, denominator = rate_text.partition("/")
        fps = float(numerator) / float(denominator or 1)
    except (KeyError, TypeError, ValueError, StopIteration,
            json.JSONDecodeError) as exc:
        raise WorkflowQualificationError(
            f"ffprobe gave no usable rate/duration for {source.name}") from exc
    if not 0 < duration or not fps > 0:
        raise WorkflowQualificationError(
            f"ffprobe gave no usable rate/duration for {source.name}")
    return fps, max(1, round(duration * fps))


def _broker_call(kind: str, params: dict, *, idempotency_key: str,
                 wait_seconds: int) -> dict:
    from library.tools.resolved import client

    with perf_ledger.span(
            "qualification_broker_job", backend="ren-resolved",
            calls=1, external_engine="resolve_qualification_job"):
        submitted = client.submit(kind, params, qualification=True,
                                  idempotency_key=idempotency_key)
        receipt = client.result(submitted["id"], wait=wait_seconds)
    if receipt["state"] != "done":
        failure = {"state": receipt["state"], "error": receipt.get("error"),
                   **(receipt.get("result") or {})}
        raise WorkflowQualificationError(json.dumps(failure, default=str))
    return receipt


def prepare_broker_job(params: dict) -> dict:
    """Validate the broker's one fixed write/export workflow."""
    from library.tools.resolved.jobs import QUALIFICATION_PROJECT, JobRefused

    if params.get("action") == "cleanup":
        allowed = {"action", "project", "run_stamp", "timelines",
                   "cleanup_dir"}
        if set(params) != allowed or params["project"] != QUALIFICATION_PROJECT:
            raise JobRefused("workflow cleanup needs its exact qualification schema")
        from library.tools.proof_cleanup import (
            qualification_workflow_timeline_names,
        )
        names = qualification_workflow_timeline_names(params["run_stamp"])
        rows = params["timelines"]
        if (not isinstance(rows, list) or len(rows) != 2
                or {row.get("name") for row in rows} != set(names)
                or any(not row.get("unique_id") for row in rows)):
            raise JobRefused("cleanup must name both recorded workflow timeline ids")
        cleanup_dir = Path(params["cleanup_dir"]).expanduser().resolve()
        allowed_output = (qp.media_dir() / "workflow-runs").resolve()
        if (not cleanup_dir.is_relative_to(allowed_output)
                or cleanup_dir.name != "resolve-cleanup"
                or cleanup_dir.parent.name != params["run_stamp"]):
            raise JobRefused("cleanup_dir must be the exact workflow run directory")
        return {"mode": "exclusive", "priority": "mutation",
                "executed": True, "project": QUALIFICATION_PROJECT,
                "timeline": "", "locality": "project",
                "coalesce_key": None, "qualification": True}

    allowed = {"project", "source_copy", "timeline", "run_stamp", "source_start",
               "source_end", "output_dir", "output_name",
               "timeout_seconds", "source_sha256", "media_fps",
               "media_total_frames",
               "lifecycle_project_folder", "lifecycle_run_id"}
    if set(params) != allowed:
        raise JobRefused("qualification.workflow accepts exactly "
                         f"{sorted(allowed)}")
    if params["project"] != QUALIFICATION_PROJECT:
        raise JobRefused("qualification.workflow only accepts "
                         f"{QUALIFICATION_PROJECT!r}")
    lifecycle_project = Path(params["lifecycle_project_folder"]).expanduser().resolve()
    if (not lifecycle_project.is_dir() or not params["lifecycle_run_id"]
            or not isinstance(params["lifecycle_run_id"], str)):
        raise JobRefused("qualification lifecycle needs a real project folder and run id")
    source = Path(params["source_copy"]).expanduser().resolve()
    if not source.is_relative_to(qp.real_media_dir().resolve()) or not source.is_file():
        raise JobRefused("source_copy must be an existing qualification media copy")
    output_dir = Path(params["output_dir"]).expanduser().resolve()
    allowed_output = (qp.media_dir() / "workflow-runs").resolve()
    if not output_dir.is_relative_to(allowed_output):
        raise JobRefused("output_dir must stay under qualification workflow-runs")
    if not lifecycle_project.is_relative_to(output_dir.parent):
        raise JobRefused("lifecycle project must stay inside this workflow run")
    start, end = params["source_start"], params["source_end"]
    if (not isinstance(start, (int, float)) or isinstance(start, bool)
            or not isinstance(end, (int, float)) or isinstance(end, bool)
            or start < 0 or end <= start or end - start > 45):
        raise JobRefused("source range must be positive and at most 45 seconds")
    timeout = params["timeout_seconds"]
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= RENDER_TIMEOUT_SECONDS:
        raise JobRefused("render timeout must be between 1 and 600 seconds")
    for key in ("timeline", "output_name", "source_sha256"):
        if not isinstance(params[key], str) or not params[key]:
            raise JobRefused(f"qualification.workflow needs {key!r}")
    if len(params["source_sha256"]) != 64 or any(
            char not in "0123456789abcdef" for char in params["source_sha256"]):
        raise JobRefused("source_sha256 must be a lowercase SHA-256 hex digest")
    # The media facts are MEASURED by the caller, before the job is
    # queued: the broker's turn is the one thing every lane waits
    # behind, and hashing a media copy and running ffprobe on it are
    # file facts, not Resolve facts. The job therefore never opens the
    # media file.
    fps = params["media_fps"]
    if (not isinstance(fps, (int, float)) or isinstance(fps, bool)
            or not 0 < float(fps) <= 1000):
        raise JobRefused("media_fps must be a positive frame rate")
    frames = params["media_total_frames"]
    if (not isinstance(frames, int) or isinstance(frames, bool)
            or frames < 1):
        raise JobRefused("media_total_frames must be a positive frame count")
    if Path(params["output_name"]).name != params["output_name"]:
        raise JobRefused("output_name must be a single filename stem")
    if len(params["timeline"]) > 64:
        raise JobRefused("timeline name must be at most 64 characters")
    from library.tools.proof_cleanup import qualification_workflow_timeline_names
    if params["timeline"] not in qualification_workflow_timeline_names(
            params["run_stamp"]):
        raise JobRefused("timeline is not owned by the declared workflow run")
    return {"mode": "exclusive", "priority": "qa_render",
            "executed": True, "project": QUALIFICATION_PROJECT,
            "timeline": params["timeline"], "locality": "timeline",
            "coalesce_key": None, "qualification": True}


def run_broker_job(job: dict, resolve) -> dict:
    """Build, revise, export and read back one timeline on Resolve."""
    from library.tools.resolved.jobs import JobRefused

    stages = []
    current = {"name": "project borrow"}
    try:
        params = job["params"]
        if params.get("action") == "cleanup":
            return _run_broker_cleanup(job, resolve)
        output_dir = Path(params["output_dir"]).expanduser().resolve()
        workflow_root = output_dir.parent
        ledger_path = workflow_root / "perf_ledger.jsonl"
        with perf_ledger.recording(
                ledger_path, workflow_root.name, "qualification_workflow",
                required=True):
            result = _run_broker_job(job, resolve, current, stages)
    except Exception as exc:
        raise JobRefused(
            f"{current['name']} failed: {exc}",
            result={"broker_stages": stages,
                    "failed_stage": current["name"],
                    "detail": str(exc),
                    "traceback": traceback.format_exc()}) from exc
    result["broker_stages"] = stages
    return result


def _run_broker_cleanup(job: dict, resolve) -> dict:
    """Retire only the two timeline ids recorded by this successful run."""
    from library.tools import proof_cleanup
    from library.tools.event_log import read_events
    from library.tools.execution.remove_proof import (
        retire_captain_task_timeline,
        timeline_inventory,
    )

    params = job["params"]
    run_stamp = params["run_stamp"]
    cleanup_dir = Path(params["cleanup_dir"])
    events = read_events(str(cleanup_dir.parent))
    created = {(event["fields"].get("timeline"),
                event["fields"].get("timeline_unique_id"))
               for event in events
               if event.get("type") == "qualification_resolve_resource"
               and event["fields"].get("run_id") == run_stamp
               and event["fields"].get("state") == "created"}
    authorized = [event["fields"] for event in events
                  if event.get("type") == "qualification_cleanup_authorized"
                  and event["fields"].get("run_id") == run_stamp]
    requested = {(row["name"], row["unique_id"])
                 for row in params["timelines"]}
    authorized_ids = ({(row["name"], row["unique_id"])
                       for row in authorized[-1]["timelines"]}
                      if authorized else set())
    if authorized_ids != requested:
        raise WorkflowQualificationError(
            "cleanup has no final-QA authorization for these timeline ids")
    if not requested.issubset(created):
        raise WorkflowQualificationError(
            "cleanup timeline ids were not registered at creation")
    cleanup_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cleanup_dir / "resolve-timeline-cleanup-manifest.json"
    manifest = {"run_id": run_stamp, "project": qp.QUALIFICATION_PROJECT,
                "requested": params["timelines"], "status": "planned"}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    with qp.borrowed(resolve):
        manager = resolve.GetProjectManager()
        project = qp.switch_project(manager, qp.QUALIFICATION_PROJECT)
        inventory = timeline_inventory(project)
        planned = []
        for target in params["timelines"]:
            proof_cleanup.qualification_workflow_timeline_authority(
                qp.QUALIFICATION_PROJECT, target["name"],
                target["unique_id"], run_stamp)
            matches = [row for row in inventory
                       if row["item_id"] == target["unique_id"]
                       and row["name"] == target["name"]]
            if len(matches) != 1:
                raise WorkflowQualificationError(
                    f"recorded timeline identity is absent or ambiguous: {target}")
            planned.append(matches[0])
        manifest["inventory"] = inventory
        manifest["targets"] = planned
        manifest["status"] = "applying"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                                 encoding="utf-8")
        removed = []
        for row in planned:
            result = retire_captain_task_timeline(
                resolve, project, project_name=qp.QUALIFICATION_PROJECT,
                timeline_name=row["name"], item_id=row["item_id"],
                backup_path=str(cleanup_dir / f"{row['item_id']}.drt"),
                journal_path=str(cleanup_dir / f"{row['item_id']}.json"),
                expected_inventory=inventory,
                qualification_run_stamp=run_stamp)
            removed.append(result)
            inventory = timeline_inventory(project)
            manifest["current_inventory"] = inventory
            manifest["removed"] = removed
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                                     encoding="utf-8")
    manifest["status"] = "complete"
    manifest["removed"] = removed
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    return {"status": "complete", "manifest": str(manifest_path),
            "removed": removed}


def _run_broker_job(job: dict, resolve, current: dict,
                    stages: list) -> dict:
    """Internal workflow body; records each completed broker stage."""
    from library.tools import edit_patch, timeline_shadow
    from library.tools.resolve_deadline import DeadlineProxy
    from library.tools.resolved.jobs import QUALIFICATION_PROJECT
    from library.tools.transform_write_log import write_scope

    params = job["params"]
    source = Path(params["source_copy"]).resolve()
    if not source.is_relative_to(qp.real_media_dir().resolve()):
        raise WorkflowQualificationError("broker source escaped qualification media")
    if not source.is_file():
        raise WorkflowQualificationError("qualification source copy is gone")
    # The media facts are the CALLER's measurement (the digest, the
    # frame rate and the frame count), carried in the job: the copy was
    # hashed and probed before this job was queued, so the broker's
    # turn is spent on Resolve and not on reading a media file.
    media_fps = float(params["media_fps"])
    total_frames = int(params["media_total_frames"])
    if not isinstance(resolve, DeadlineProxy):
        raise WorkflowQualificationError(
            "qualification Resolve handle is missing per-call deadlines")
    current["name"] = "edit construction: switch project"
    with qp.borrowed(resolve) as borrowed_from:
        manager = resolve.GetProjectManager()
        project = qp.switch_project(manager, QUALIFICATION_PROJECT)
        if project.GetName() != QUALIFICATION_PROJECT:
            raise WorkflowQualificationError(
                f"opened {project.GetName()!r}, not {QUALIFICATION_PROJECT!r}")
        if any(project.GetTimelineByIndex(index).GetName() == params["timeline"]
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)):
            raise WorkflowQualificationError(
                f"timeline already exists: {params['timeline']}")
        _check_project_standard(project)
        current["name"] = "edit construction: import media"
        pool = project.GetMediaPool()
        media_item = None
        for item in pool.GetRootFolder().GetClipList() or []:
            properties = item.GetClipProperty() or {}
            if Path(str(properties.get("File Path", ""))).resolve() == source:
                media_item = item
                break
        imported = False
        if media_item is None:
            imported_items = pool.ImportMedia([str(source)]) or []
            if len(imported_items) != 1:
                raise WorkflowQualificationError(
                    f"Resolve imported {len(imported_items)} source items")
            media_item = imported_items[0]
            imported = True
        if imported:
            from library.tools.event_log import emit
            workflow_root = Path(params["output_dir"]).resolve().parent
            project_uid_getter = getattr(project, "GetUniqueId", None)
            project_uid = (str(project_uid_getter() or "")
                           if callable(project_uid_getter) else "")
            emit(str(workflow_root), "qualification_resolve_resource",
                 task=job["id"], run_id=params["run_stamp"],
                 job_id=job["id"], project=QUALIFICATION_PROJECT,
                 project_unique_id=project_uid,
                 media_unique_id=str(media_item.GetUniqueId() or ""),
                 source_path=str(source), source_sha256=params["source_sha256"],
                 state="imported")
        # Pool clips start at their own zero; the offset is a timeline
        # notion, and some essence items expose no offset method at all.
        left = 0
        source_in = left + round(float(params["source_start"]) * media_fps)
        source_count = max(1, round(
            (float(params["source_end"]) - float(params["source_start"])) * media_fps))
        source_out = min(left + total_frames,
                         source_in + source_count)
        if source_out <= source_in:
            raise WorkflowQualificationError("analysis selected no source frames")

        timeline = pool.CreateEmptyTimeline(params["timeline"])
        if not timeline:
            raise WorkflowQualificationError("Resolve could not create timeline")
        from library.tools import artifact_lifecycle, event_log
        project_id = str(project.GetUniqueId() or "")
        timeline_id = str(timeline.GetUniqueId() or "")
        if not project_id or not timeline_id:
            raise WorkflowQualificationError(
                "Resolve did not return exact project and timeline identities")
        lifecycle_log = event_log.log_path(params["lifecycle_project_folder"])
        qualification_project_artifact_id = artifact_lifecycle.stable_artifact_id(
            project_id, params["lifecycle_run_id"], job["id"],
            "resolve_qualification_project", project_id)
        artifact_lifecycle.register_resolve_artifact(
            lifecycle_log, artifact_id=qualification_project_artifact_id,
            kind="resolve_qualification_project", project_id=project_id,
            run_id=params["lifecycle_run_id"], job_id=job["id"],
            identity={"object_type": "project",
                      "project_unique_id": project_id,
                      "object_unique_id": project_id,
                      "project_name": project.GetName()},
            retention_class="protected", state=artifact_lifecycle.RETAINED)
        lifecycle_artifact_id = artifact_lifecycle.stable_artifact_id(
            project_id, params["lifecycle_run_id"], job["id"],
            "resolve_qualification_timeline", timeline_id)
        lifecycle_identity = {
            "object_type": "timeline", "project_unique_id": project_id,
            "object_unique_id": timeline_id,
            "project_name": project.GetName(),
            "object_name": timeline.GetName()}
        artifact_lifecycle.register_resolve_artifact(
            lifecycle_log, artifact_id=lifecycle_artifact_id,
            kind="resolve_qualification_timeline", project_id=project_id,
            run_id=params["lifecycle_run_id"], job_id=job["id"],
            identity=lifecycle_identity, state=artifact_lifecycle.ACTIVE)
        # Keep the qualification workflow's recovery ledger in sync with the
        # shared artifact lifecycle registry. Existing recovery reads this
        # event to find a newly created timeline after an interrupted run.
        workflow_root = Path(params["output_dir"]).resolve().parent
        _lifecycle_project(workflow_root)
        event_log.emit(str(workflow_root), "qualification_resolve_resource",
                       task=job["id"], run_id=params["run_stamp"],
                       job_id=job["id"], project=QUALIFICATION_PROJECT,
                       project_unique_id=project_id,
                       timeline=params["timeline"],
                       timeline_unique_id=timeline_id, state="created")
        from library.tools.resolve_lock import assert_current_timeline
        assert_current_timeline(project, timeline)
        if project.GetCurrentTimeline().GetName() != params["timeline"]:
            raise WorkflowQualificationError("qualification timeline did not read back")
        current["name"] = "edit construction: timeline shape"
        timeline_shape = _check_timeline_inherits_standard(timeline)
        timeline_fps = float(timeline_shape["timelineFrameRate"])
        if timeline.GetTrackCount("audio") == 0 and not timeline.AddTrack("audio"):
            raise WorkflowQualificationError("Resolve could not create A1")
        for media_type, name in (("video", "A-Roll"), ("audio", "Production Audio")):
            if not timeline.SetTrackName(media_type, 1, name):
                raise WorkflowQualificationError(
                    f"could not name {media_type}1 {name!r}")
        store = timeline_shadow.ShadowStore()
        current["name"] = "edit construction: place clips"
        timeline_shadow.observe(project, timeline, store)
        build = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[
                {"op": "clip.insert", "unique_id": f"{job['id']}-build-v1",
                 "track_type": "video", "track_index": 1,
                 "record_in": int(timeline.GetStartFrame()),
                 "record_out": int(timeline.GetStartFrame()) + source_out - source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": source_in, "source_out": source_out},
                {"op": "clip.insert", "unique_id": f"{job['id']}-build-a1",
                 "track_type": "audio", "track_index": 1,
                 "record_in": int(timeline.GetStartFrame()),
                 "record_out": int(timeline.GetStartFrame()) + source_out - source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": source_in, "source_out": source_out},
            ], conflict_domains=("timeline_structure",),
            idempotency_key=f"qualification-build:{job['id']}", store=store)
        video = timeline.GetItemListInTrack("video", 1) or []
        audio = timeline.GetItemListInTrack("audio", 1) or []
        if build["status"] != "committed" or len(video) != 1 or len(audio) != 1:
            raise WorkflowQualificationError(
                f"edit construction read back video={len(video)} audio={len(audio)} "
                f"receipt={build}")
        stages.append({"name": "edit construction", "status": "passed",
                       "evidence": {"build_receipt": build,
                                    "video_clips": len(video),
                                    "audio_clips": len(audio),
                                    "timeline_shape": timeline_shape}})
        current["name"] = "scripted human change"
        human_frame = int(timeline.GetStartFrame()) + round(timeline_fps)
        manual = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.touchup",
            operations=[{"op": "marker.add", "frame": human_frame,
                         "color": "Blue", "name": "Human change: keep this moment",
                         "note": "Scripted stand-in for the editor's retained change."}],
            conflict_domains=("markers",),
            idempotency_key=f"qualification-human:{job['id']}",
            preconditions=({"kind": "marker_absent", "frame": human_frame},),
            store=store)
        if manual["status"] != "committed":
            raise WorkflowQualificationError(f"manual change did not commit: {manual}")
        stages.append({"name": "scripted human change", "status": "passed",
                       "evidence": manual})

        current["name"] = "Ren revision preservation"
        record_in = int(timeline.GetEndFrame())
        revision_count = max(1, round(timeline_fps))
        revision_source_in = min(source_out, source_in + round(2 * media_fps))
        revision_source_out = min(source_out, revision_source_in + revision_count)
        if revision_source_out <= revision_source_in:
            revision_source_in, revision_source_out = source_in, source_out
        revision = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[
                {"op": "clip.insert", "unique_id": f"{job['id']}-v1",
                 "track_type": "video", "track_index": 1,
                 "record_in": record_in,
                 "record_out": record_in + revision_source_out - revision_source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": revision_source_in,
                 "source_out": revision_source_out},
                {"op": "clip.insert", "unique_id": f"{job['id']}-a1",
                 "track_type": "audio", "track_index": 1,
                 "record_in": record_in,
                 "record_out": record_in + revision_source_out - revision_source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": revision_source_in,
                 "source_out": revision_source_out},
            ], conflict_domains=("timeline_structure",),
            idempotency_key=f"qualification-revision:{job['id']}",
            store=store)
        if revision["status"] != "committed":
            raise WorkflowQualificationError(
                f"Ren revision did not commit: {revision}")
        markers = timeline.GetMarkers() or {}
        readback_marker = (markers.get(human_frame)
                           or markers.get(str(human_frame)))
        video_after = timeline.GetItemListInTrack("video", 1) or []
        audio_after = timeline.GetItemListInTrack("audio", 1) or []
        if (not readback_marker
                or readback_marker.get("name") != "Human change: keep this moment"
                or len(video_after) != 2 or len(audio_after) != 2):
            raise WorkflowQualificationError(
                "Ren revision lost the scripted human change or placement: "
                f"marker={readback_marker}, video={len(video_after)}, "
                f"audio={len(audio_after)}")
        stages.append({"name": "Ren revision preservation",
                       "status": "passed",
                       "evidence": {"revision": revision,
                                    "marker_frame": human_frame,
                                    "marker_name": readback_marker["name"],
                                    "video_clips": len(video_after),
                                    "audio_clips": len(audio_after)}})
        with write_scope(project=QUALIFICATION_PROJECT,
                         timeline_name=params["timeline"],
                         run_id=job["id"]):
            if not manager.SaveProject():
                raise WorkflowQualificationError(
                    "Resolve refused to save the Qualification edit")
        lifecycle_identity["content_sha256"] = _timeline_fingerprint(
            project, timeline)
        artifact_lifecycle.transition_resolve_artifact(
            lifecycle_log, lifecycle_artifact_id,
            artifact_lifecycle.COMPLETED,
            reason="qualification edit and save completed",
            identity=lifecycle_identity)

        current["name"] = "export"
        from library.tools.execution import resolve_render
        render = resolve_render.render_timeline_under_existing_lease(
            timeline_name=params["timeline"],
            output_dir=params["output_dir"],
            output_name=params["output_name"], fmt="mp4", codec="H264",
            timeout_seconds=params["timeout_seconds"],
            resolve=resolve)
        stages.append({"name": "Resolve render", "status": "passed",
                       "evidence": render})
        duration_frames = int(timeline.GetEndFrame() - timeline.GetStartFrame())
        return {"project": QUALIFICATION_PROJECT,
                "project_unique_id": project_id,
                "timeline": params["timeline"], "fps": timeline_fps,
                "timeline_unique_id": timeline_id,
                "lifecycle_artifact_id": lifecycle_artifact_id,
                "lifecycle_project_folder": params["lifecycle_project_folder"],
                "lifecycle_log": str(lifecycle_log),
                "timeline_content_sha256": lifecycle_identity["content_sha256"],
                "duration_seconds": duration_frames / timeline_fps,
                "media_imported": imported,
                "media_path": str(source),
                "media_unique_id": media_item.GetUniqueId(),
                "selected_source_span": [params["source_start"], params["source_end"]],
                "edit_construction": {"video_clips": 1, "audio_clips": 1,
                                      "selected_from_fresh_analysis": True,
                                      "build_receipt": build,
                                      "readback": "passed"},
                "manual_change": manual,
                "ren_revision": revision,
                "preservation_readback": {
                    "marker_frame": human_frame,
                    "marker_name": readback_marker["name"],
                    "video_clips": len(video_after),
                    "audio_clips": len(audio_after),
                    "passed": True},
                "render": render,
                "borrowed_from": borrowed_from}


def run(index_path: str | Path, *, query: str = DEFAULT_QUERY,
        runs: int = 2, pin_failure: bool = False) -> dict:
    """Run analysis, real edit construction, manual change, revision, export,
    and final render QA two times by default.
    """
    if not 2 <= runs <= 2:
        raise ValueError("qualification requires exactly two clean runs")
    started = datetime.now(UTC)
    run_stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    root = qp.media_dir() / "workflow-runs" / run_stamp
    root.mkdir(parents=True, exist_ok=False)
    perf = perf_ledger.begin(
        root, "qualification_workflow", run_stamp,
        ledger_path_override=root / "perf_ledger.jsonl", required=True)
    try:
        perf_ledger.count_call("cli_starts")
    except BaseException:
        perf.end(persist=False)
        raise
    report_path = root / "qualification-report.json"
    report = {"schema": "ren-workflow-qualification.v1",
              "started_at": started.isoformat(), "project": "Ren Qualification",
              "source_selection": None, "runs": [], "stages": [],
              "passed": False, "pinned": bool(pin_failure)}
    report["prior_run_reconciliation"] = _reconcile_prior_runs(root)
    _write_report(report_path, report)
    from library.tools.event_log import emit
    owner = _lifecycle_project(root)
    emit(str(owner), "qualification_run_lifecycle", task=run_stamp,
         run_id=run_stamp, job_id=run_stamp, state="active",
         host=socket.gethostname(), pid=os.getpid(),
         started_at=started.isoformat(), pinned=bool(pin_failure))
    try:
        try:
            selected = qp.copy_indexed_source(
                index_path, query, max_seconds=MAX_SOURCE_SECONDS)
        except Exception as exc:
            _stage(report, "source copy", passed=False,
                   evidence={"index": str(index_path), "query": query,
                             "error": str(exc)})
            _write_report(report_path, report)
            raise
        report["source_selection"] = selected
        report["shared_source_artifact"] = _register_shared_artifact(
            root, run_stamp, Path(selected["qualification_copy"]),
            kind="qualification_source_copy",
            dependency=selected["sha256"])
        _stage(report, "source copy", passed=True,
               evidence={k: selected[k] for k in (
                   "index", "query", "clip_id", "filename", "source_file",
                   "qualification_copy", "duration_seconds", "bytes", "sha256")})
        _write_report(report_path, report)
        copy_path = Path(selected["qualification_copy"])
        lifecycle_project_folder = None
        for ordinal in range(1, runs + 1):
            run_id = f"{ordinal}-{uuid.uuid4().hex[:8]}"
            run_dir = root / f"run-{ordinal}"
            run_dir.mkdir()
            one = {"ordinal": ordinal, "id": run_id, "stages": []}
            report["runs"].append(one)
            try:
                semantic_root = root.parents[1] / "semantic-measurements"
                semantic_before = ({path for path in semantic_root.rglob("*")
                                   if path.is_file()}
                                   if semantic_root.exists() else set())
                analysis = _run_analysis(copy_path, run_dir, run_id)
                one["shared_semantic_artifacts"] = (
                    _register_new_semantic_measurements(
                        root, run_stamp, semantic_before, semantic_root))
                child_rows = perf_ledger.read_rows(
                    analysis["project_folder"], required=True)
                child_run_ids = perf_ledger.run_ids(child_rows)
                if not child_run_ids:
                    raise WorkflowQualificationError(
                        "analysis completed without performance ledger rows")
                child_run_id = child_run_ids[-1]
                spans = []
                for child_row in child_rows:
                    if (child_row.get("kind") == perf_ledger.SPAN
                            and child_row.get("run_id") == child_run_id):
                        span_row = dict(child_row)
                        span_row["source_run_id"] = child_run_id
                        span_row["run_id"] = run_stamp
                        span_row["capability"] = "qualification_workflow"
                        spans.append(span_row)
                perf_ledger.commit_to_path(
                    root / "perf_ledger.jsonl", spans, required=True)
                hit = _analyzed_hit(analysis["index_file"], query)
            except Exception as exc:
                _stage(report, f"run {ordinal}: analysis", passed=False,
                       evidence={"error": str(exc)})
                _write_report(report_path, report)
                raise
            _stage(report, f"run {ordinal}: analysis", passed=True,
                   evidence={**analysis["evidence"], "analysis_hit": hit})
            one["analysis_hit"] = hit
            _write_report(report_path, report)
            if lifecycle_project_folder is None:
                lifecycle_project_folder = analysis["project_folder"]

            timeline_name = f"Q Workflow {run_stamp} {ordinal}"
            export_dir = root / "exports"
            export_dir.mkdir(exist_ok=True)
            # The media facts are measured HERE, before the job is
            # queued: the broker's turn is what every lane waits
            # behind, and a media copy's digest and frame rate are file
            # facts, not Resolve facts. The job carries them and never
            # opens the media file.
            media_fps, media_total_frames = _probe_source_frames(copy_path)
            if _file_sha256(copy_path) != selected["sha256"]:
                raise WorkflowQualificationError(
                    "the qualification source copy changed since it was "
                    "made; it is not the copy the plan was built from")
            params = {"project": "Ren Qualification",
                      "run_stamp": run_stamp,
                      "source_copy": str(copy_path),
                      "timeline": timeline_name,
                      "source_start": max(
                          0.0, float(hit["hit"]["start"]) - 5.0),
                      "source_end": min(
                          float(selected["duration_seconds"]),
                          max(float(hit["hit"]["end"]) + 5.0,
                              max(0.0, float(hit["hit"]["start"])-5.0)
                              + 15.0)),
                      "output_dir": str(export_dir),
                      "output_name": f"{timeline_name.replace(' ', '_')}",
                      "timeout_seconds": RENDER_TIMEOUT_SECONDS,
                      "source_sha256": selected["sha256"],
                      "media_fps": media_fps,
                      "media_total_frames": media_total_frames,
                      "lifecycle_project_folder": str(lifecycle_project_folder),
                      "lifecycle_run_id": run_stamp}
            try:
                receipt = _broker_call(
                    "qualification.workflow", params,
                    idempotency_key=f"qualification-workflow:{run_id}",
                    wait_seconds=RENDER_TIMEOUT_SECONDS + 900)
            except WorkflowQualificationError as exc:
                try:
                    failure = json.loads(str(exc))
                except json.JSONDecodeError:
                    failure = {"error": str(exc)}
                for stage in failure.get("broker_stages", []):
                    _stage(report, f"run {ordinal}: {stage['name']}",
                           passed=stage["status"] == "passed",
                           evidence=stage.get("evidence"))
                failed_stage = failure.get("failed_stage")
                if failed_stage:
                    _stage(report, f"run {ordinal}: {failed_stage}",
                           passed=False, evidence=failure)
                _write_report(report_path, report)
                raise
            evidence = receipt["result"]
            one["resolve"] = evidence
            for stage in evidence["broker_stages"]:
                _stage(report, f"run {ordinal}: {stage['name']}",
                       passed=stage["status"] == "passed",
                       evidence=stage.get("evidence"))
            from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC
            from library.tools.execution import resolve_render
            from library.tools.master_loudness import master_render_report
            try:
                produced = resolve_render._files_written_since(
                    evidence["render"]["output_dir"],
                    evidence["render"]["output_name"],
                    evidence["render"]["render_started_at"])
                if not produced:
                    raise WorkflowQualificationError(
                        "Resolve completed without writing an export")
                raw_path = (Path(evidence["render"]["output_dir"])
                            / produced[-1])
                if raw_path.stat().st_size < resolve_render.MIN_PLAUSIBLE_BYTES:
                    raise WorkflowQualificationError(
                        f"Resolve export is implausibly small: {raw_path.stat().st_size} bytes")
                resolve_render._assert_has_audio(str(raw_path))
            except Exception as exc:
                _stage(report, f"run {ordinal}: export", passed=False,
                       evidence={"error": str(exc)})
                _write_report(report_path, report)
                raise
            master_path = str(export_dir / f"{params['output_name']}_master.mp4")
            try:
                mastered = master_render_report(
                    {"output_path": str(raw_path)}, master_path,
                    target_lufs=DEFAULT_DELIVERY_SPEC.loudness_target_lufs,
                    true_peak_ceiling=(
                        DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp))
            except Exception as exc:
                _stage(report, f"run {ordinal}: export", passed=False,
                       evidence={"error": str(exc)})
                _write_report(report_path, report)
                raise
            evidence["raw_output_path"] = str(raw_path)
            evidence["output_path"] = mastered["output_path"]
            evidence["mastering"] = mastered["mastering"]
            _stage(report, f"run {ordinal}: export", passed=True,
                   evidence={"render": evidence["render"],
                             "mastering": evidence["mastering"],
                             "output_path": evidence["output_path"]})
            _write_report(report_path, report)

            from library.skills.verify_render.skill import run as verify_render
            expected = float(evidence["duration_seconds"])
            qa = verify_render(
                evidence["output_path"], str(analysis["project_folder"]),
                f"qualification-{run_stamp}-{ordinal}",
                expected_duration=expected,
                expected_resolution=[1920, 1080],
                expected_fps=float(evidence["fps"]))
            one["render_qa"] = qa
            _stage(report, f"run {ordinal}: final render QA",
                   passed=bool(qa["passed"]), evidence=qa)
            _write_report(report_path, report)
            if not qa["passed"]:
                raise WorkflowQualificationError(
                    f"run {ordinal} final render QA failed: "
                    f"{'; '.join(qa['issues'])}")
        # Retire only after every run passed its final evidence gate, in one
        # short AXI maintenance call under one exclusive Resolve lease.
        from library.tools import artifact_lifecycle, event_log
        retire_args = [str(REPO_ROOT / "bin" / "resolve-axi"), "timeline",
                       "delete", "--project-folder",
                       str(lifecycle_project_folder)]
        for one in report["runs"]:
            evidence = one["resolve"]
            lifecycle_log = event_log.log_path(
                evidence["lifecycle_project_folder"])
            artifact_lifecycle.transition_resolve_artifact(
                lifecycle_log, evidence["lifecycle_artifact_id"],
                artifact_lifecycle.DISPOSABLE,
                reason="qualification run and render validation passed")
            retire_args.extend(["--artifact-id", evidence["lifecycle_artifact_id"]])
        retire_args.append("--apply")
        retirement = subprocess.run(
            retire_args, cwd=REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", timeout=300, check=False)
        if retirement.returncode != 0:
            raise WorkflowQualificationError(
                "qualification timelines passed QA but guarded retirement "
                f"refused: {retirement.stdout[-4000:]} {retirement.stderr[-4000:]}")
        report["timeline_retirement"] = retirement.stdout[-8000:]
        _write_report(report_path, report)
        report["passed"] = (
            len(report["runs"]) == 2
            and all(stage["status"] == "passed" for stage in report["stages"])
            and all("resolve" in one and "render_qa" in one
                    and one["render_qa"]["passed"] for one in report["runs"]))
        _write_report(report_path, report)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if not report["stages"] or report["stages"][-1]["status"] != "failed":
            _stage(report, "workflow", passed=False,
                   evidence=report["failure"])
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["report_path"] = str(report_path)
        try:
            if report["passed"]:
                from library.tools.artifact_lifecycle import CLEANUP_JOB
                from library.tools.event_log import emit, emit_once

                timelines = [
                    {"name": one["resolve"]["timeline"],
                     "unique_id": one["resolve"]["timeline_unique_id"]}
                    for one in report["runs"]]
                cleanup_dir = root / "resolve-cleanup"
                run_records = _run_artifact_records(root, run_stamp)
                emit(str(owner), "qualification_cleanup_authorized",
                     task=run_stamp, run_id=run_stamp,
                     timelines=timelines,
                     evidence="both final render QA stages passed",
                     report_path=str(report_path))
                manifest_path = root / "qualification-cleanup-manifest.json"
                manifest_path.write_text(json.dumps({
                    "status": "planned", "run_id": run_stamp,
                    "resolve_timelines": timelines,
                    "artifacts": [
                        {"artifact_id": record.artifact_id,
                         "path": record.path,
                         "identity": record.identity.__dict__}
                        for record in run_records],
                    "created_at": datetime.now(UTC).isoformat()}, indent=2)
                    + "\n", encoding="utf-8")
                emit_once(
                    str(owner), CLEANUP_JOB,
                    f"cleanup:qualification:{run_stamp}:queued",
                    task=run_stamp,
                    lifecycle_event_id=f"qualification:{run_stamp}",
                    project_id="qualification-workflow", run_id=run_stamp,
                    job_id=run_stamp, reason="successful final render QA",
                    resolve_timelines=timelines, state="queued")
                cleanup = _broker_call(
                    "qualification.workflow",
                    {"action": "cleanup", "project": "Ren Qualification",
                     "run_stamp": run_stamp, "timelines": timelines,
                     "cleanup_dir": str(cleanup_dir)},
                    idempotency_key=f"qualification-workflow-cleanup:{run_stamp}",
                    wait_seconds=RENDER_TIMEOUT_SECONDS + 900)
                report["resolve_cleanup"] = cleanup["result"]
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8"))
                manifest["resolve_cleanup"] = cleanup["result"]
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                report["filesystem_cleanup"] = _retire_successful_run_files(
                    root, run_stamp)
                run_state = "completed"
            else:
                records = _run_artifact_records(root, run_stamp)
                from library.tools import artifact_lifecycle as lifecycle
                for record in records:
                    lifecycle.transition_artifact(
                        str(owner), record.artifact_id, lifecycle.FAILED,
                        reason="qualification did not pass final render QA")
                    lifecycle.transition_artifact(
                        str(owner), record.artifact_id,
                        lifecycle.RECOVERY_HOLD,
                        reason=("pinned failure" if pin_failure else
                                f"failed reproduction retained for "
                                f"{FAILED_REPRODUCTION_TTL_DAYS} days"))
                run_state = "failed"
        except Exception as cleanup_error:  # noqa: BLE001 - preserve evidence
            report["cleanup"] = {"status": "refused",
                                 "detail": str(cleanup_error)}
            if report["passed"]:
                report["passed"] = False
                _stage(report, "qualification cleanup", passed=False,
                       evidence=report["cleanup"])
            run_state = "recovery_hold"
        emit(str(owner), "qualification_run_lifecycle", task=run_stamp,
             run_id=run_stamp, job_id=run_stamp, state=run_state,
             finished_at=report["finished_at"], pinned=bool(pin_failure))
        _write_report(report_path, report)
        if perf:
            perf.row["status"] = "ok" if report["passed"] else "failed"
            perf.end()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren qualification workflow",
        description="Qualify real footage through two complete Ren revisions and render QA.")
    parser.add_argument("--index",
                        help="existing footage_index.json; read-only")
    parser.add_argument("--query", default=DEFAULT_QUERY,
                        help="search phrase used to select the real footage span")
    parser.add_argument("--runs", type=int, choices=(2,), default=2,
                        help="two consecutive complete runs are required")
    parser.add_argument("--pin-failure", action="store_true",
                        help="retain bulky artifacts for a failed run until explicitly unpinned")
    parser.add_argument("--unpin-failure",
                        help="release the reproduction pin for this failed run id")
    args = parser.parse_args(argv)
    if args.unpin_failure:
        try:
            result = set_failed_run_pin(args.unpin_failure, pinned=False)
        except Exception as exc:  # noqa: BLE001 - command reports durable refusal
            print(json.dumps({"pinned": False, "error": str(exc)}, indent=2),
                  file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if not args.index:
        parser.error("--index is required unless --unpin-failure is used")
    try:
        report = run(args.index, query=args.query, runs=args.runs,
                     pin_failure=args.pin_failure)
    except Exception as exc:  # noqa: BLE001 - command reports a durable failure
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2),
              file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["passed"] else 1

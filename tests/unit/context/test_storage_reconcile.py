"""Reconciliation defects: a finding nothing can settle is a permanent noise.

Every test here names one defect the reconciliation used to carry. The
machine printed its lifecycle warning on every start because the report
named the same artifacts forever: a record whose file Ren had replaced,
a quarantine whose grace period had expired, and a refused cleanup job
that was re-queued on every start.
"""

from datetime import UTC, datetime, timedelta

from library.tools import artifact_lifecycle, storage_lifecycle
from library.tools.versions import store


def _project(tmp_path, name="project"):
    project = tmp_path / name
    (project / "pipeline_output" / "scratch").mkdir(parents=True)
    return project


def _register(project, path, artifact_id="artifact-one", **overrides):
    fields = {
        "artifact_id": artifact_id,
        "kind": "temporary_render",
        "project_id": "project-one",
        "run_id": "run-one",
        "job_id": "job-one",
        "path": str(path),
        "created_at": datetime.now(UTC).isoformat(),
        "identity": artifact_lifecycle.FileIdentity.capture(path),
        "retention_class": "temporary",
        "regenerable": True,
        "regeneration_method": "rebuild",
    }
    fields.update(overrides)
    record = artifact_lifecycle.ArtifactRecord(**fields)
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project), artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        str(project), artifact_id, artifact_lifecycle.DISPOSABLE,
        reason="fixture is disposable")
    return record


def _quarantine(project, path, artifact_id="artifact-one"):
    """Register a disposable file and move it into owned quarantine."""
    _register(project, path, artifact_id=artifact_id)
    manifest = path.parent.parent.parent / "plan.json"
    storage_lifecycle.plan([str(project)], output=str(manifest))
    storage_lifecycle.collect(str(manifest))
    return manifest


def test_replaced_artifact_file_is_reconciled_and_no_longer_named(tmp_path):
    """Defect: a record whose file Ren replaced drifted forever."""
    project = _project(tmp_path)
    artifact = project / "pipeline_output" / "scratch" / "generated.bin"
    artifact.write_bytes(b"first bytes")
    _register(project, artifact)

    artifact.write_bytes(b"second bytes, a different file")

    report = storage_lifecycle.reconcile([str(project)])

    row = report["projects"][0]
    assert row["record_identity_rebound"] == ["artifact-one"]
    assert row["record_filesystem_drift"] == []
    assert storage_lifecycle.unresolved_findings(report) == []
    recorded = artifact_lifecycle._latest_artifacts(str(project))["artifact-one"]
    assert recorded["identity"]["size_bytes"] == artifact.stat().st_size


def test_missing_artifact_file_stays_an_open_finding(tmp_path):
    """Defect: a file that is gone cannot be said anything about."""
    project = _project(tmp_path)
    artifact = project / "pipeline_output" / "scratch" / "generated.bin"
    artifact.write_bytes(b"planned bytes")
    _register(project, artifact)
    artifact.unlink()

    report = storage_lifecycle.reconcile([str(project)])

    row = report["projects"][0]
    assert row["record_filesystem_drift"] == ["artifact-one"]
    assert len(storage_lifecycle.unresolved_findings(report)) == 1


def test_expired_quarantine_is_released_to_the_reviewed_purge(tmp_path):
    """Defect: an expired quarantine was reported on every start forever."""
    project = _project(tmp_path)
    artifact = project / "pipeline_output" / "scratch" / "generated.bin"
    artifact.write_bytes(b"planned bytes")
    manifest = _quarantine(project, artifact)

    report = storage_lifecycle.reconcile(
        [str(project)], now=datetime.now(UTC) + timedelta(hours=25))

    row = report["projects"][0]
    assert row["expired_quarantine_settled"] == ["artifact-one"]
    assert row["expired_quarantine_protected"] == []
    assert storage_lifecycle.unresolved_findings(report) == []
    # The file is NOT removed by the reconciliation: the reviewed plan is
    # what removes it, and only after the captain read the manifest.
    quarantined = project / "pipeline_output" / "quarantine"
    assert list(quarantined.rglob("generated.bin"))
    storage_lifecycle.plan([str(project)], output=str(manifest))
    storage_lifecycle.purge(str(manifest))
    assert not list(quarantined.rglob("generated.bin"))


def test_protected_quarantine_is_never_released(tmp_path):
    """Defect: a pinned quarantine must not be settled by a clock."""
    project = _project(tmp_path)
    artifact = project / "pipeline_output" / "scratch" / "generated.bin"
    artifact.write_bytes(b"planned bytes")
    _register(project, artifact, pinned=True)
    manifest = project.parent / "plan.json"
    storage_lifecycle.plan([str(project)], output=str(manifest))
    storage_lifecycle.collect(str(manifest))
    artifact_lifecycle.transition_artifact(
        str(project), "artifact-one", artifact_lifecycle.QUARANTINED,
        reason="fixture quarantine")

    report = storage_lifecycle.reconcile(
        [str(project)], now=datetime.now(UTC) + timedelta(hours=25))

    row = report["projects"][0]
    assert row["expired_quarantine_settled"] == []
    assert row["expired_quarantine_protected"] == ["artifact-one"]
    assert len(storage_lifecycle.unresolved_findings(report)) == 1


def test_settled_quarantine_can_be_restored_to_its_recorded_path(tmp_path):
    """Defect: a settled quarantine could not be restored any more."""
    project = _project(tmp_path)
    artifact = project / "pipeline_output" / "scratch" / "generated.bin"
    artifact.write_bytes(b"planned bytes")
    manifest = _quarantine(project, artifact)
    storage_lifecycle.reconcile(
        [str(project)], now=datetime.now(UTC) + timedelta(hours=25))
    storage_lifecycle.plan([str(project)], output=str(manifest))

    restored = storage_lifecycle.restore(str(manifest))

    assert restored["restored"][0]["path"] == str(artifact)
    assert artifact.read_bytes() == b"planned bytes"


def test_refused_cleanup_job_stops_being_retried_and_is_named(tmp_path):
    """Defect: a refused job was re-queued on every start, forever."""
    project = _project(tmp_path)
    artifact_lifecycle.schedule_cleanup_job(
        str(project), event_id="cleanup-one", project_id="project-one",
        run_id="run-one", job_id="job-one", reason="fixture job")
    job = artifact_lifecycle._latest_cleanup_jobs(str(project))["cleanup-one"]
    artifact_lifecycle.transition_cleanup_job(
        str(project), job, "running", outcome="the worker started")
    artifact_lifecycle.transition_cleanup_job(
        str(project), job, "refused", outcome="the worker refused")

    retried = []
    for _ in range(storage_lifecycle.MAX_CLEANUP_JOB_RETRIES + 2):
        report = storage_lifecycle.reconcile([str(project)])
        retried.append(report["projects"][0]["refused_jobs_retried"])
        if not report["projects"][0]["refused_jobs_retried"]:
            break
        job = artifact_lifecycle._latest_cleanup_jobs(str(project))["cleanup-one"]
        artifact_lifecycle.transition_cleanup_job(
            str(project), job, "running", outcome="the worker started")
        artifact_lifecycle.transition_cleanup_job(
            str(project), job, "refused", outcome="the worker refused")

    assert retried[:-1] == [["cleanup-one"]] * storage_lifecycle.MAX_CLEANUP_JOB_RETRIES
    assert retried[-1] == []
    row = report["projects"][0]
    assert row["refused_jobs_needing_attention"] == ["cleanup-one"]
    assert len(storage_lifecycle.unresolved_findings(report)) == 1


def test_reexported_recovery_archive_keeps_its_ownership_record_true(tmp_path):
    """Defect: re-exporting an archive left its record naming a stale file."""
    project = _project(tmp_path)
    archive = project / "pipeline_output" / "review" / "recovery_archives"
    archive.mkdir(parents=True)
    path = archive / "Reel_01--abc1234567.drt"
    path.write_bytes(b"first archive")
    _register(project, path, artifact_id="artifact-archive",
              kind="recovery_archive", retention_class="recovery",
              regenerable=False, pinned=True)

    path.write_bytes(b"a later, longer archive")
    store._rebind_registered_archive(str(project), path)

    recorded = artifact_lifecycle._latest_artifacts(str(project))["artifact-archive"]
    assert recorded["identity"]["size_bytes"] == path.stat().st_size
    report = storage_lifecycle.reconcile([str(project)])
    assert report["projects"][0]["record_filesystem_drift"] == []

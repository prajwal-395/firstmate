"""Named P3 8.1 regression: changed artifact identity invalidates a plan."""

from datetime import UTC, datetime

import pytest

from library.tools import artifact_lifecycle, retention, storage_lifecycle


def test_changed_artifact_identity_invalidates_storage_plan(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "generated.bin"
    artifact.write_bytes(b"planned bytes")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id="artifact-one", kind="temporary_render",
        project_id="project-one", run_id="run-one", job_id="job-one",
        path=str(artifact), created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(artifact),
        retention_class="temporary", regenerable=True,
        regeneration_method="rebuild")
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project), record.artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        str(project), record.artifact_id, artifact_lifecycle.DISPOSABLE,
        reason="fixture is disposable")
    manifest = tmp_path / "storage-plan.json"
    storage_lifecycle.plan([str(project)], output=str(manifest))

    artifact.write_bytes(b"changed after planning")

    with pytest.raises(artifact_lifecycle.LifecycleRefused,
                       match="identity changed since storage plan"):
        storage_lifecycle.collect(str(manifest))


def test_repeated_collect_is_idempotent_after_physical_quarantine(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "generated.bin"
    artifact.write_bytes(b"disposable bytes")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id="artifact-repeat", kind="temporary_render",
        project_id="project-one", run_id="run-one", job_id="job-one",
        path=str(artifact), created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(artifact),
        retention_class="temporary", regenerable=True,
        regeneration_method="rebuild")
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project), record.artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        str(project), record.artifact_id, artifact_lifecycle.DISPOSABLE,
        reason="fixture is disposable")
    manifest = tmp_path / "storage-plan.json"
    storage_lifecycle.plan([str(project)], output=str(manifest))

    first = storage_lifecycle.collect(str(manifest))
    second = storage_lifecycle.collect(str(manifest))

    assert not artifact.exists()
    assert first["quarantined"][0]["path"] == second["quarantined"][0]["path"]
    assert second["quarantined"][0]["already_quarantined"] is True


def test_resolve_census_matches_duplicate_names_by_recorded_identity():
    """Named P3 8.2 regression: duplicate timeline names have distinct IDs."""
    observed = {storage_lifecycle._resolve_identity_key({
        "object_type": "timeline", "project_unique_id": "project-1",
        "object_unique_id": "timeline-1", "object_name": "Same name"})}
    recorded = {"project_id": "project-1", "identity": {
        "object_type": "timeline", "project_unique_id": "project-1",
        "object_unique_id": "timeline-2", "object_name": "Same name"}}

    assert storage_lifecycle._resolve_census_status(
        recorded, "project-1", observed) == "orphaned_recorded_identity"


def test_resolve_census_leaves_unseen_projects_for_review():
    """Named P3 8.2 regression: an incomplete census cannot imply absence."""
    recorded = {"project_id": "other-project", "identity": {
        "object_type": "project", "project_unique_id": "other-project",
        "object_unique_id": "other-project"}}

    assert storage_lifecycle._resolve_census_status(
        recorded, "current-project", set()) == "outside_census_scope"


def test_space_recovery_estimate_waits_for_all_hardlinks():
    """Named P3 8.1 regression: unlinking one hardlink frees no inode."""
    first = retention.Candidate(
        path="one", size_bytes=4096, category="scratch", reason="test",
        inode=(1, 9, 2), allocated_bytes=2048)
    second = retention.Candidate(
        path="two", size_bytes=4096, category="scratch", reason="test",
        inode=(1, 9, 2), allocated_bytes=2048)

    assert retention._physical_reclaimable_bytes([first]) == 0
    assert retention._physical_reclaimable_bytes([first, second]) == 2048

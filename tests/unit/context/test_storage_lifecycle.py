"""Named P3 8.1 regression: changed artifact identity invalidates a plan."""

from datetime import UTC, datetime

import pytest

from library.tools import artifact_lifecycle, storage_lifecycle


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

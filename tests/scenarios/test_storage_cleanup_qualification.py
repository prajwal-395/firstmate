"""Named lifecycle qualification contract: automatic cleanup needs evidence."""

from pathlib import Path

import pytest

from library.tools import (
    artifact_lifecycle,
    retention,
    storage_lifecycle,
    workspace_gc,
)


def _scenario_rows(tmp_path):
    rows = []
    for scenario_id in sorted(storage_lifecycle.STORAGE_QUALIFICATION_SCENARIOS):
        evidence = tmp_path / f"{scenario_id}.json"
        evidence.write_text(
            f'{{"scenario_id": "{scenario_id}", "passed": true}}\n',
            encoding="utf-8",
        )
        rows.append(
            {"scenario_id": scenario_id, "passed": True, "evidence": [str(evidence)]}
        )
    return rows


def test_automatic_signoff_retirement_requires_complete_qualification(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(retention, "retention_mode", lambda **_kwargs: retention.LEAN)

    response = retention.after_signoff(str(project), lifecycle_event_id="signoff-1")

    assert "requires a complete storage lifecycle qualification record" in response
    assert artifact_lifecycle.incomplete_cleanup_jobs(str(project)) == []


def test_automatic_workspace_retirement_requires_qualified_projects(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(workspace_gc, "_project_roots", lambda: [project])

    with pytest.raises(workspace_gc.WorkspaceGcRefused,
                       match="requires storage lifecycle qualification"):
        workspace_gc._require_cleanup_qualification()


def test_qualification_record_requires_every_scenario_and_durable_evidence(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    rows = _scenario_rows(tmp_path)

    with pytest.raises(ValueError, match="scenarios mismatch"):
        storage_lifecycle.record_cleanup_qualification(
            str(project), scenarios=rows[:-1], record_id="incomplete"
        )

    rows[0] = {**rows[0], "evidence": [str(tmp_path / "missing.json")]}
    with pytest.raises(ValueError, match="existing absolute files"):
        storage_lifecycle.record_cleanup_qualification(
            str(project), scenarios=rows, record_id="no-evidence"
        )


def test_qualification_record_is_the_explicit_gate_for_automatic_cleanup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    assert storage_lifecycle.cleanup_qualification(str(project)) is None

    storage_lifecycle.record_cleanup_qualification(
        str(project), scenarios=_scenario_rows(tmp_path), record_id="lifecycle-v1"
    )

    record = storage_lifecycle.cleanup_qualification(str(project))
    assert record["record_id"] == "lifecycle-v1"
    assert {row["scenario_id"] for row in record["scenarios"]} == (
        storage_lifecycle.STORAGE_QUALIFICATION_SCENARIOS
    )
    evidence = record["scenarios"][0]["evidence"][0]["path"]
    Path(evidence).write_text("changed evidence\n", encoding="utf-8")
    assert storage_lifecycle.cleanup_qualification(str(project)) is None


def test_qualification_scope_names_all_required_cleanup_scenarios():
    assert storage_lifecycle.STORAGE_QUALIFICATION_SCENARIOS == {
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


@pytest.mark.parametrize(
    "relative_path",
    [
        "pipeline_output/versions/approved.drp",
        "pipeline_output/recovery/restore.drp",
        "pipeline_output/undo/undo.json",
        "pipeline_output/resolve/timeline.drt",
    ],
)
def test_dry_run_never_offers_approved_or_recovery_state_for_retirement(
    tmp_path, relative_path
):
    project = tmp_path / "project"
    target = project / relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"durable editorial state")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id="durable-state",
        kind="temporary_render",
        project_id="project",
        run_id="run",
        job_id="job",
        path=str(target),
        created_at="2026-10-19T00:00:00+00:00",
        identity=artifact_lifecycle.FileIdentity.capture(target),
        retention_class="temporary",
        regenerable=True,
        regeneration_method="rebuild",
    )
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project),
        record.artifact_id,
        artifact_lifecycle.COMPLETED,
        reason="fixture completed",
    )
    artifact_lifecycle.transition_artifact(
        str(project),
        record.artifact_id,
        artifact_lifecycle.DISPOSABLE,
        reason="fixture is intentionally misclassified",
    )

    preview = storage_lifecycle.plan_document([str(project)])

    assert preview["candidates"] == []
    assert target.read_bytes() == b"durable editorial state"


def test_dry_run_excludes_owned_external_media(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external" / "owned.bin"
    external.parent.mkdir()
    external.write_bytes(b"external source")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id="external-owned",
        kind="temporary_render",
        project_id="project",
        run_id="run",
        job_id="job",
        path=str(external),
        created_at="2026-10-19T00:00:00+00:00",
        identity=artifact_lifecycle.FileIdentity.capture(external),
        retention_class="temporary",
        regenerable=True,
        regeneration_method="rebuild",
    )
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project),
        record.artifact_id,
        artifact_lifecycle.COMPLETED,
        reason="fixture completed",
    )
    artifact_lifecycle.transition_artifact(
        str(project),
        record.artifact_id,
        artifact_lifecycle.DISPOSABLE,
        reason="fixture models an external owner",
    )

    preview = storage_lifecycle.plan_document([str(project)])

    assert preview["candidates"] == []
    assert external.read_bytes() == b"external source"


@pytest.mark.parametrize(
    ("retention_class", "dependencies", "state"),
    [
        (
            "temporary",
            ("project-b:shared-footage-index",),
            artifact_lifecycle.DISPOSABLE,
        ),
        ("staging", (), artifact_lifecycle.DISPOSABLE),
        ("temporary", (), artifact_lifecycle.ACTIVE),
    ],
)
def test_dry_run_preserves_shared_intelligence_staging_and_pending_work(
    tmp_path, retention_class, dependencies, state
):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "candidate.bin"
    target.write_bytes(b"must remain")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id="protected-candidate",
        kind="temporary_render",
        project_id="project",
        run_id="run",
        job_id="job",
        path=str(target),
        created_at="2026-10-19T00:00:00+00:00",
        identity=artifact_lifecycle.FileIdentity.capture(target),
        retention_class=retention_class,
        dependencies=dependencies,
        regenerable=True,
        regeneration_method="rebuild",
    )
    artifact_lifecycle.register_artifact(str(project), record)
    if state == artifact_lifecycle.ACTIVE:
        artifact_lifecycle.transition_artifact(
            str(project), record.artifact_id, state, reason="fixture pending work"
        )
    elif state != artifact_lifecycle.CREATED:
        artifact_lifecycle.transition_artifact(
            str(project),
            record.artifact_id,
            artifact_lifecycle.COMPLETED,
            reason="fixture completed",
        )
        artifact_lifecycle.transition_artifact(
            str(project), record.artifact_id, state, reason="fixture lifecycle state"
        )

    preview = storage_lifecycle.plan_document([str(project)])

    assert preview["candidates"] == []
    assert target.read_bytes() == b"must remain"

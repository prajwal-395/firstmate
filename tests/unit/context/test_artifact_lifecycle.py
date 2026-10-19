"""Defect named: cleanup had no owned identity or recoverable lifecycle."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from library.tools import artifact_lifecycle as lifecycle


def _record(tmp_path, *, artifact_id="artifact-1", path=None, **changes):
    target = path or (tmp_path / "output.bin")
    target.write_bytes(b"owned output")
    defaults = dict(
        artifact_id=artifact_id,
        kind="generated_render",
        project_id="project-a",
        run_id="run-a",
        job_id="job-a",
        path=str(target),
        created_at="2026-10-19T00:00:00+00:00",
        identity=lifecycle.FileIdentity.capture(target),
        retention_class="temporary",
        dependencies=(),
        pinned=False,
        regenerable=True,
        regeneration_method="ren build-reels",
    )
    defaults.update(changes)
    return lifecycle.ArtifactRecord(**defaults)


def test_unknown_ownership_is_protected(tmp_path):
    record = _record(tmp_path, project_id="")

    with pytest.raises(lifecycle.LifecycleRefused, match="unknown artifact ownership"):
        lifecycle.register_artifact(str(tmp_path), record)


def test_artifact_state_transition_is_validated_and_idempotent(tmp_path):
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    record = _record(tmp_path)
    lifecycle.register_artifact(project, record)
    lifecycle.transition_artifact(project, record.artifact_id, lifecycle.ACTIVE,
                                  reason="writer started")

    with pytest.raises(lifecycle.LifecycleRefused, match="invalid artifact transition"):
        lifecycle.transition_artifact(project, record.artifact_id,
                                      lifecycle.PURGED, reason="too early")

    assert lifecycle.transition_artifact(
        project, record.artifact_id, lifecycle.ACTIVE, reason="repeat") is False


def test_cleanup_job_is_deduplicated_by_lifecycle_event(tmp_path):
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    args = dict(event_id="signoff:reel-a:round-2", project_id="project-a",
                run_id="signoff-2", job_id="cleanup-2", reason="sign-off")

    first, created = lifecycle.schedule_cleanup_job(project, **args)
    again, created_again = lifecycle.schedule_cleanup_job(project, **args)

    assert created is True
    assert created_again is False
    assert first["seq"] == again["seq"]
    assert len(lifecycle.pending_cleanup_jobs(project)) == 1


def test_interrupted_cleanup_job_returns_to_queue_for_reproof(tmp_path):
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    job, _ = lifecycle.schedule_cleanup_job(
        project, event_id="signoff:reel-a:round-2", project_id="project-a",
        run_id="signoff-2", job_id="cleanup-2", reason="sign-off")
    lifecycle.transition_cleanup_job(project, job, "running")

    recovered = lifecycle.recover_interrupted_cleanup_jobs(project)

    assert recovered == 1
    assert len(lifecycle.pending_cleanup_jobs(project)) == 1


def test_inventory_counts_shared_inode_physical_bytes_once(tmp_path):
    project = str(tmp_path / "project")
    project.mkdir()
    first = _record(tmp_path, artifact_id="artifact-a")
    second_path = tmp_path / "second.bin"
    os.link(first.path, second_path)
    second = _record(tmp_path, artifact_id="artifact-b", path=second_path)
    lifecycle.register_artifact(project, first)
    lifecycle.register_artifact(project, second)

    measured = lifecycle.inventory(project)

    assert measured["logical_bytes"] == first.identity.size_bytes * 2
    assert measured["physical_bytes"] == first.identity.size_bytes


def test_symlink_and_hardlink_cleanup_are_refused(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    cleanup_root = project / "scratch"
    cleanup_root.mkdir()
    external = tmp_path / "external.bin"
    external.write_bytes(b"captain-owned")
    linked = cleanup_root / "linked.bin"
    linked.symlink_to(external)
    record = replace(_record(tmp_path), path=str(linked))
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(tmp_path / "locks"))

    with pytest.raises(lifecycle.LifecycleRefused, match="symlink traversal"):
        lifecycle.purge_file(str(project), record, str(cleanup_root))
    assert external.read_bytes() == b"captain-owned"

    linked.unlink()
    target = cleanup_root / "shared.bin"
    target.write_bytes(b"shared bytes")
    hardlink = tmp_path / "other-name.bin"
    os.link(target, hardlink)
    shared = _record(tmp_path, path=target)
    shared = lifecycle.ArtifactRecord(**{
        **shared.__dict__, "identity": lifecycle.FileIdentity.capture(target)})
    lifecycle.register_artifact(str(project), shared)
    lifecycle.transition_artifact(str(project), shared.artifact_id,
                                  lifecycle.COMPLETED, reason="writer finished")
    lifecycle.transition_artifact(str(project), shared.artifact_id,
                                  lifecycle.DISPOSABLE, reason="eligible")

    with pytest.raises(lifecycle.LifecycleRefused, match="hardlinked artifact"):
        lifecycle.purge_file(str(project), shared, str(cleanup_root))
    assert target.exists()


def test_delete_time_inode_recheck_catches_path_replacement(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    cleanup_root = project / "scratch"
    cleanup_root.mkdir()
    target = cleanup_root / "output.bin"
    record = _record(tmp_path, path=target)
    lifecycle.register_artifact(str(project), record)
    lifecycle.transition_artifact(str(project), record.artifact_id,
                                  lifecycle.COMPLETED, reason="writer finished")
    lifecycle.transition_artifact(str(project), record.artifact_id,
                                  lifecycle.DISPOSABLE, reason="eligible")
    replacement = cleanup_root / "replacement.bin"
    replacement.write_bytes(b"owned output")
    os.utime(replacement, ns=(record.identity.mtime_ns,
                              record.identity.mtime_ns))
    replacement.replace(target)
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(tmp_path / "locks"))

    with pytest.raises(lifecycle.LifecycleRefused, match="identity changed"):
        lifecycle.purge_file(str(project), record, str(cleanup_root))
    assert target.exists()

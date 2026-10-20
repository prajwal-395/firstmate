"""Defect named: a delivered render was a purge candidate.

`storage_lifecycle.plan_document` judged a registered artifact by the
`retention_class` its WRITER recorded, and by three names in a list.
A file in `exports/` - the deliverable the run is FOR - registered by a
writer that called it "temporary" was nominated for permanent deletion,
and nothing in the plan said why.

The area is the fact. `library/tools/artifact_policy.py` is now the one
reading of what an area means, and a cleanup may name a file only when
its area says the file is disposable.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from library.tools import (
    artifact_lifecycle,
    artifact_policy,
    storage_lifecycle,
)
from library.tools.project_layout import AREAS, Area


def _register(project, target, *, artifact_id="artifact-one",
              retention_class="temporary", kind="generated_render",
              state=artifact_lifecycle.DISPOSABLE):
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id=artifact_id, kind=kind,
        project_id="project-one", run_id="run-one", job_id="job-one",
        path=str(target), created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(target),
        retention_class=retention_class, regenerable=True,
        regeneration_method="rebuild")
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project), artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        str(project), artifact_id, state, reason="fixture is disposable")
    return record


def test_a_delivered_render_registered_temporary_is_never_a_purge_candidate(tmp_path):
    """Named defect: the deliverable was collectable."""
    project = tmp_path / "project"
    (project / "exports").mkdir(parents=True)
    deliverable = project / "exports" / "reel-26.mp4"
    deliverable.write_bytes(b"the finished render")
    _register(project, deliverable)

    document = storage_lifecycle.plan_document([str(project)])

    assert document["candidates"] == []
    assert [row["path"] for row in document["refused_policy"]] == [
        str(deliverable)]
    assert deliverable.read_bytes() == b"the finished render"


def test_a_captured_note_registered_temporary_is_never_a_purge_candidate(tmp_path):
    """Named defect: a note typed onto a timeline was collectable."""
    project = tmp_path / "project"
    (project / "marker_feedback").mkdir(parents=True)
    note = project / "marker_feedback" / "note.json"
    note.write_bytes(b"the captain's own words")
    _register(project, note, artifact_id="note-one")

    document = storage_lifecycle.plan_document([str(project)])

    assert document["candidates"] == []
    assert note.read_bytes() == b"the captain's own words"


def test_a_scratch_file_registered_temporary_is_still_a_purge_candidate(tmp_path):
    """The policy protects what the layout protects, and nothing else."""
    project = tmp_path / "project"
    (project / "pipeline_output" / "scratch").mkdir(parents=True)
    scratch = project / "pipeline_output" / "scratch" / "intermediate.bin"
    scratch.write_bytes(b"no reader after the step that wrote it")
    _register(project, scratch, artifact_id="scratch-one")

    document = storage_lifecycle.plan_document([str(project)])

    assert [row["path"] for row in document["candidates"]] == [str(scratch)]


def test_the_area_policy_covers_every_area_in_the_layout():
    """A new area cannot arrive without a policy."""
    assert set(artifact_policy.AREA_POLICY) == set(AREAS)


def test_every_area_policy_names_a_real_retention_class():
    for area, policy in artifact_policy.AREA_POLICY.items():
        assert policy.retention_class in artifact_policy.RETENTION_CLASSES, area


def test_the_deliverable_and_the_captured_areas_are_never_cleanable():
    for area in (Area.EXPORTS, Area.MARKER_FEEDBACK, Area.LEARNED_CONTEXT,
                 Area.VOX_TEST_RENDERS, Area.REVIEW, Area.PROVENANCE,
                 Area.LOGS, Area.UNSORTED, Area.RUN_STATE):
        assert artifact_policy.is_never_cleanable(
            artifact_policy.policy_for_area(area).retention_class), area


def test_the_regeneration_method_names_the_step_that_owns_the_area():
    assert artifact_policy.policy_for_area(
        Area.SUBTITLE_SEGMENTS).regeneration_method == (
            "manage_project.py run <project> --only render_subtitles "
            "--rerun render_subtitles")
    assert artifact_policy.policy_for_area(
        Area.REEL_SAFE_ZONE_GUIDES).regeneration_method == (
            "manage_project.py safe-zones <project> --overlay combined")


def test_an_area_never_lowers_a_class_the_record_named():
    assert artifact_policy.effective_class(
        "/tmp/project", "/tmp/project/exports/reel.mp4", "temporary") == (
            artifact_policy.DELIVERABLE)
    assert artifact_policy.effective_class(
        "/tmp/project", "/tmp/project/pipeline_output/scratch/x.bin",
        "temporary") == artifact_policy.TEMPORARY


def test_an_unknown_class_is_protected_rather_than_assumed_small():
    assert artifact_policy.effective_class(
        "/tmp/project", "/tmp/project/pipeline_output/scratch/x.bin",
        "something-nobody-defined") == artifact_policy.PROTECTED


def test_registration_derives_the_class_from_the_area_when_none_is_named(tmp_path):
    """A record that names no class takes the one its area has."""
    project = tmp_path / "project"
    (project / "exports").mkdir(parents=True)
    deliverable = project / "exports" / "reel.mp4"
    deliverable.write_bytes(b"render")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id="derived-one", kind="generated_render",
        project_id="project-one", run_id="run-one", job_id="job-one",
        path=str(deliverable), created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(deliverable),
        regenerable=True, regeneration_method="rebuild")

    artifact_lifecycle.register_artifact(str(project), record)

    row = next(item for item in artifact_lifecycle.inventory(
        str(project))["artifacts"] if item["artifact_id"] == "derived-one")
    assert row["retention_class"] == artifact_policy.DELIVERABLE


def test_a_file_outside_the_project_is_judged_by_its_own_record(tmp_path):
    """The qualification broker workspace lives outside every project."""
    outside = tmp_path / "workspace" / "broker.bin"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"broker working file")

    assert artifact_policy.effective_class(
        str(tmp_path / "project"), str(outside), "temporary") == "temporary"
    assert artifact_policy.effective_class(
        str(tmp_path / "project"), str(outside), "") == artifact_policy.PROTECTED


def test_a_path_in_no_named_area_of_the_project_is_protected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    assert artifact_policy.area_for_path(
        str(project), str(project / "stray.bin")) is None
    assert artifact_policy.is_protected_path(
        str(project), str(project / "stray.bin"), "temporary")

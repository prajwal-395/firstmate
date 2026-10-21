"""PROD-005: cleanup must be PREDICTABLE, and its budget must be VISIBLE.

Three defects are named here, and each test names the one it pins.

1. **The storage plan dropped every artifact it kept.** A plan said
   what it would remove and nothing about what it would leave. A file
   held by a dependency, a pinned artifact and a delivered render all
   vanished from the plan identically, so the plan could not be read as
   a prediction of what the machine would look like afterwards.

2. **The status report's reclaimable bytes disagreed with the plan.**
   `status()` called a file reclaimable when no ownership rule
   protected it, and the plan then refused the same file because it
   was git-tracked. Two reports, two numbers, one file - so neither
   could be trusted to predict the other.

3. **No category had a budget, so usage could not be compared with
   anything.** The report had bytes per artifact KIND and per legacy
   CATEGORY, but no category budget and no over-budget fact, so
   "scratch media is 40 GB" was a fact nobody could act on.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from library.tools import (
    artifact_lifecycle,
    artifact_policy,
    storage_categories,
    storage_lifecycle,
)
from library.tools.project_layout import AREAS, Area


def _register(project: Path, target: Path, *, artifact_id: str,
              kind: str = "temporary_render", pinned: bool = False,
              dependencies: tuple[str, ...] = (),
              retention_class: str = "temporary",
              state: str = artifact_lifecycle.DISPOSABLE) -> None:
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id=artifact_id, kind=kind,
        project_id="project-one", run_id="run-one", job_id="job-one",
        path=str(target), created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(target),
        retention_class=retention_class, regenerable=True,
        regeneration_method="rebuild", pinned=pinned,
        dependencies=dependencies)
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project), artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        str(project), artifact_id, state, reason="fixture is disposable")


@pytest.fixture(autouse=True)
def _no_machine_legacy_roots(monkeypatch):
    """The legacy store walk reaches the MACHINE; a test does not.

    The category usage of the legacy stores is measured on the machine,
    not in a fixture, so the walk is stubbed out here: the tests below
    are about the classification, and the walk is a separate fact.
    """
    monkeypatch.setattr(
        storage_lifecycle, "_legacy_roots", lambda projects=(): [])


def _scratch_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "pipeline_output" / "scratch").mkdir(parents=True)
    return project


def test_the_plan_accounts_for_every_registered_artifact(tmp_path):
    """Named defect: a kept artifact vanished from the plan."""
    project = _scratch_project(tmp_path)
    reclaimable = project / "pipeline_output" / "scratch" / "reclaimable.bin"
    reclaimable.write_bytes(b"no reader after the step that wrote it")
    held = project / "pipeline_output" / "scratch" / "held.bin"
    held.write_bytes(b"a dependency names this one")
    _register(project, reclaimable, artifact_id="scratch-one")
    _register(project, held, artifact_id="scratch-two",
              dependencies=("version-7",))

    document = storage_lifecycle.plan_document([str(project)])

    assert [row["path"] for row in document["candidates"]] == [str(reclaimable)]
    kept = {row["artifact_id"]: row for row in document["kept"]}
    assert set(kept) == {"scratch-two"}
    assert "dependency" in [reason["code"] for reason in kept["scratch-two"]["reasons"]]
    assert document["accounting"]["registered"] == 2
    assert document["accounting"]["candidates"] == 1
    assert document["accounting"]["kept"] == 1


def test_a_kept_artifact_names_the_reason_it_is_kept(tmp_path):
    """Named defect: 'kept' with no reason is not a prediction."""
    project = _scratch_project(tmp_path)
    pinned = project / "pipeline_output" / "scratch" / "pinned.bin"
    pinned.write_bytes(b"something asked for this to stay")
    _register(project, pinned, artifact_id="pinned-one", pinned=True)

    document = storage_lifecycle.plan_document([str(project)])

    assert document["candidates"] == []
    reasons = document["kept"][0]["reasons"]
    assert "pinned" in [reason["code"] for reason in reasons]
    assert all(reason["detail"] for reason in reasons)


def test_the_report_and_the_plan_agree_on_the_reclaimable_bytes(tmp_path):
    """Named defect: two reports, two numbers, one file."""
    project = _scratch_project(tmp_path)
    scratch = project / "pipeline_output" / "scratch" / "intermediate.bin"
    scratch.write_bytes(b"disposable bytes")
    _register(project, scratch, artifact_id="scratch-one")

    report = storage_lifecycle.status([str(project)])
    document = storage_lifecycle.plan_document([str(project)])

    assert report["reclaimable_bytes"] == document["accounting"]["reclaimable_bytes"]
    assert report["reclaimable_bytes"] == sum(
        row["identity"]["size_bytes"] for row in document["candidates"])


@pytest.mark.skipif(shutil.which("git") is None, reason="needs a git binary")
def test_a_git_tracked_file_is_never_counted_as_reclaimable(tmp_path):
    """Named defect: the report promised bytes the plan then refused."""
    clone = tmp_path / "clone"
    clone.mkdir()
    project = clone / "project"
    (project / "pipeline_output" / "scratch").mkdir(parents=True)
    tracked = project / "pipeline_output" / "scratch" / "tracked.bin"
    tracked.write_bytes(b"a tracked fixture")
    for args in (("init",), ("config", "user.email", "p@example.com"),
                 ("config", "user.name", "prod005"),
                 ("add", "project/pipeline_output/scratch/tracked.bin"),
                 ("commit", "-m", "tracked fixture")):
        subprocess.run(["git", *args], cwd=str(clone), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       encoding="utf-8", timeout=60)
    _register(project, tracked, artifact_id="tracked-one")

    report = storage_lifecycle.status([str(project)])
    document = storage_lifecycle.plan_document([str(project)])

    assert document["candidates"] == []
    assert report["reclaimable_bytes"] == 0
    assert [row["reason"] for row in document["refused_git"]] == ["git-tracked"]
    kept = {row["artifact_id"]: row for row in document["kept"]}
    assert "git-tracked" in [
        reason["code"] for reason in kept["tracked-one"]["reasons"]]


def test_every_category_reports_a_budget_and_whether_it_is_over(tmp_path):
    """Named defect: usage with nothing to compare it against."""
    project = _scratch_project(tmp_path)
    scratch = project / "pipeline_output" / "scratch" / "intermediate.bin"
    scratch.write_bytes(b"disposable bytes")
    _register(project, scratch, artifact_id="scratch-one")

    report = storage_lifecycle.status([str(project)])

    categories = report["categories"]
    assert set(categories) == set(storage_categories.CATEGORIES)
    assert categories["scratch_media"]["budget_bytes"] is not None
    assert categories["scratch_media"]["total_bytes"] > 0
    assert categories["scratch_media"]["over_budget"] is False
    assert categories["protected"]["budget_bytes"] is None
    assert categories["protected"]["over_budget"] is False


def test_a_budget_is_read_from_the_environment(tmp_path, monkeypatch):
    """The budget is the machine's, not the code's."""
    monkeypatch.setenv("REN_STORAGE_BUDGET_SCRATCH_MEDIA_BYTES", "1")
    project = _scratch_project(tmp_path)
    scratch = project / "pipeline_output" / "scratch" / "intermediate.bin"
    scratch.write_bytes(b"disposable bytes")
    _register(project, scratch, artifact_id="scratch-one")

    report = storage_lifecycle.status([str(project)])

    assert report["categories"]["scratch_media"]["budget_bytes"] == 1
    assert report["categories"]["scratch_media"]["over_budget"] is True


def test_no_area_a_cleanup_may_never_name_sits_in_a_budgeted_category():
    """A budget may never cover something protected."""
    for area in AREAS:
        policy = artifact_policy.policy_for_area(area)
        if artifact_policy.is_never_cleanable(policy.retention_class):
            assert storage_categories.AREA_CATEGORY[area] == (
                storage_categories.PROTECTED), area
        else:
            assert storage_categories.AREA_CATEGORY[area] in (
                storage_categories.BUDGETED_CATEGORIES), area


def test_the_deliverable_is_counted_as_protected_and_never_as_reclaimable(
        tmp_path):
    """A delivered render is visible, and it is not a budget line."""
    project = tmp_path / "project"
    (project / "exports").mkdir(parents=True)
    deliverable = project / "exports" / "reel.mp4"
    deliverable.write_bytes(b"the finished render")
    _register(project, deliverable, artifact_id="deliverable-one",
              retention_class="temporary")

    report = storage_lifecycle.status([str(project)])
    document = storage_lifecycle.plan_document([str(project)])

    assert report["categories"]["protected"]["total_bytes"] > 0
    assert document["candidates"] == []
    assert [row["path"] for row in document["refused_policy"]] == [
        str(deliverable)]
    assert deliverable.read_bytes() == b"the finished render"


def test_a_file_a_recorded_version_names_is_kept(tmp_path):
    """VC-008: an archived generation is a liveness root for every cleanup path.

    A version that could still be restored must still have its media, so
    the storage plan keeps the file the version's own journal names.
    """
    import json

    project = _scratch_project(tmp_path)
    scratch = project / "pipeline_output" / "scratch" / "caption-overlay.mov"
    scratch.write_bytes(b"a caption render a version points at")
    _register(project, scratch, artifact_id="version-media")
    journal_dir = project / "pipeline_output" / "review" / "undo" / "entry-1"
    journal_dir.mkdir(parents=True)
    (journal_dir / "entry.json").write_text(json.dumps({
        "format": "undo_journal/1",
        "before": {"tracks": [{"clips": [{
            "name": "caption-overlay.mov",
            "source_file": str(scratch),
            "record_in": 0,
        }]}]},
    }), encoding="utf-8")

    document = storage_lifecycle.plan_document([str(project)])

    assert document["candidates"] == []
    kept = {row["artifact_id"]: row for row in document["kept"]}
    assert "version-liveness-root" in [
        reason["code"] for reason in kept["version-media"]["reasons"]]


def test_the_scratch_area_is_counted_as_scratch_media(tmp_path):
    """The category is read from the LAYOUT, not from a file's name."""
    project = _scratch_project(tmp_path)
    scratch = project / "pipeline_output" / "scratch" / "intermediate.bin"
    scratch.write_bytes(b"disposable bytes")
    _register(project, scratch, artifact_id="scratch-one")

    report = storage_lifecycle.status([str(project)])

    assert report["categories"]["scratch_media"]["artifacts"] == 1
    assert storage_categories.category_for_path(
        str(project), str(scratch)) == storage_categories.SCRATCH_MEDIA
    assert storage_categories.category_for_path(
        str(project), str(project / "exports" / "reel.mp4")) == (
            storage_categories.PROTECTED)
    assert storage_categories.category_for_path(
        str(project), str(project / "nowhere" / "x.bin")) == (
            storage_categories.PROTECTED)
    assert Area.SCRATCH in AREAS

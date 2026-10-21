"""An ARCHIVED GENERATION is a liveness root: cleanup must hear it.

Defect (VC-008, the Reel 26 restore of PR 3079): Reel 26 was restored
from version 25's archived `.drt`, and the restore only worked because
that archive AND its media had survived. The cleanup paths did not treat
either as a dependency:

- the pool prune already heard the version ledger, the undo journal and
  the recovery archives (`tests/unit/reels/test_prune_version_liveness_roots.py`),
- the BUILD SWEEP and the REVIEWED PURGE (`ren purge`) read only the live
  timelines, the current pipeline records and the approved decisions, so a
  caption render an archived generation still plays was an ORPHAN and went
  to quarantine - and the purge deleted it;
- the MACHINE STORAGE PLAN (`ren storage plan`) read only the artifact
  ledger, so a file an archived generation needs was disposable because
  nothing in the ledger said otherwise.

A version that can still be restored is a version whose media are not
debris. One reading, `library/tools/version_dependencies.py`, is now used
by every cleanup path that can touch project media, and these tests name
what each path must do with it.
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import UTC, datetime

import pytest

from library.tools import artifact_lifecycle, retention, storage_lifecycle
from library.tools import caption_asset_gc as gc
from tests.unit.resolve.test_build_sweep import (
    _asset_dir,
    _database,
    _write,
)

REEL = "Reel 26 - pricing"


@pytest.fixture
def _lean(monkeypatch):
    """The purge plans only under `lean`."""
    monkeypatch.setenv(retention.SETTING, retention.LEAN)


def _project(tmp_path):
    """A scratch project with the areas a cleanup reads."""
    root = str(tmp_path / "project")
    os.makedirs(root)
    with open(os.path.join(root, "project.yaml"), "w", encoding="utf-8") as handle:
        handle.write("slug: test\n")
    return root


def _ledger(root: str, versions: list[dict]) -> str:
    """The reel version ledger, with the generations it names."""
    review = os.path.join(root, "pipeline_output", "review")
    os.makedirs(review, exist_ok=True)
    path = os.path.join(review, "reel_versions.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"format": "reel_versions/1",
                   "reels": {REEL: versions}}, handle)
    return path


def _archive(root: str, name: str, media: list[str]) -> str:
    """A `.drt` recovery archive that names the media it plays."""
    directory = os.path.join(root, "pipeline_output", "review",
                             "recovery_archives")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    clips = "".join(
        f"<VideoClip><Name>clip{n}</Name><Start>0</Start>"
        f"<Duration>10</Duration><In>0</In>"
        f"<MediaRef>{n}</MediaRef><MediaFilePath>{m}</MediaFilePath>"
        f"</VideoClip>"
        for n, m in enumerate(media))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            '<!--DbAppVer="21.1" DbPrjVer="17"-->'
            "<Project><Name>Test</Name></Project>")
        archive.writestr(
            "SeqContainer/timeline-1.xml",
            f"<Sequence><Track><TrackType>Video</TrackType>{clips}"
            f"</Track></Sequence>")
    return path


# ── The reviewed purge (`ren purge`) ───────────────────────────────────


@pytest.mark.usefixtures("_lean")
def test_the_reviewed_purge_keeps_the_media_an_archived_generation_plays(
        tmp_path, _lean):
    """The file the archived generation names is KEPT; scratch is not."""
    root = _project(tmp_path)
    kept = _write(os.path.join(_asset_dir(root), "sub_a_1_2-3_aaaaaaaa.mov"))
    disposable = _write(os.path.join(root, "pipeline_output", "scratch",
                                    "working.tmp"))
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": "/archives/reel26.drt",
            "content_id": "sha256:deadbeef",
            "assets": [{"path": kept, "bytes": 100, "sha256": "0" * 64}],
        },
    }])
    db = _database(os.path.join(tmp_path, "db", "Project.db"), [],
                   [REEL])

    plan = retention.plan_purge(root, [db])

    candidates = {candidate.path for candidate in plan.candidates}
    assert kept not in candidates, (
        "the media an archived generation plays is not disposable")
    assert disposable in candidates, (
        "a scratch file with no reader is exactly what the purge removes")
    kept_rows = [row for row in plan.kept if row["path"] == kept]
    assert kept_rows and "archived-generations" in kept_rows[0]["why"], (
        "the plan must say WHICH root kept the file")


@pytest.mark.usefixtures("_lean")
def test_the_reviewed_purge_keeps_a_scratch_file_a_generation_needs(
        tmp_path, _lean):
    """Disposable scratch and required version dependencies are separate."""
    root = _project(tmp_path)
    needed = _write(os.path.join(root, "pipeline_output", "scratch",
                                 "caption.mov"))
    disposable = _write(os.path.join(root, "pipeline_output", "scratch",
                                    "working.tmp"))
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": "/archives/reel26.drt",
            "content_id": "sha256:deadbeef",
            "assets": [{"path": needed, "bytes": 100, "sha256": "0" * 64}],
        },
    }])
    db = _database(os.path.join(tmp_path, "db", "Project.db"), [], [REEL])

    plan = retention.plan_purge(root, [db])

    candidates = {candidate.path for candidate in plan.candidates}
    assert needed not in candidates, (
        "a scratch file a retained generation needs is not disposable")
    assert disposable in candidates, (
        "the scratch file with no reader is still removable")


@pytest.mark.usefixtures("_lean")
def test_the_reviewed_purge_keeps_the_media_of_a_later_archive(
        tmp_path, _lean):
    """A version can carry SEVERAL receipts (VC-002), and each declares
    its own media. The reader must hear every one of them."""
    root = _project(tmp_path)
    first = _write(os.path.join(_asset_dir(root), "sub_a_1_2-3_aaaaaaaa.mov"))
    later = _write(os.path.join(_asset_dir(root), "sub_a_4_5-6_bbbbbbbb.mov"))
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": "/archives/reel26.drt",
            "content_id": "sha256:deadbeef",
            "assets": [{"path": first, "bytes": 100, "sha256": "0" * 64}],
        },
        "recovery_archives": [
            {"timeline_id": "a0c4856c",
             "archive_path": "/archives/reel26.drt",
             "content_id": "sha256:deadbeef",
             "assets": [{"path": first, "bytes": 100, "sha256": "0" * 64}]},
            {"timeline_id": "b1d5967d",
             "archive_path": "/archives/reel26-b.drt",
             "content_id": "sha256:feedface",
             "assets": [{"path": later, "bytes": 100, "sha256": "1" * 64}]},
        ],
    }])
    db = _database(os.path.join(tmp_path, "db", "Project.db"), [], [REEL])

    plan = retention.plan_purge(root, [db])

    candidates = {candidate.path for candidate in plan.candidates}
    assert later not in candidates, (
        "the media of a LATER archive of the same version is a dependency "
        "too")


@pytest.mark.usefixtures("_lean")
def test_the_reviewed_purge_keeps_the_media_the_archive_itself_names(
        tmp_path, _lean):
    """A receipt written before the current format carries no assets."""
    root = _project(tmp_path)
    kept = _write(os.path.join(_asset_dir(root), "sub_a_4_5-6_bbbbbbbb.mov"))
    archive = _archive(root, "reel26.drt", [kept])
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": archive,
            "content_id": "sha256:deadbeef",
        },
    }])
    db = _database(os.path.join(tmp_path, "db", "Project.db"), [], [REEL])

    plan = retention.plan_purge(root, [db])

    candidates = {candidate.path for candidate in plan.candidates}
    assert kept not in candidates, (
        "the archive names the media; a receipt without an asset manifest "
        "is not an empty manifest")
    assert archive not in candidates, (
        "the recovery archive itself is the way back and is never purged")


@pytest.mark.usefixtures("_lean")
def test_the_reviewed_purge_refuses_when_a_retained_record_is_unreadable(
        tmp_path, _lean):
    """An unreadable retained record is not an empty one."""
    root = _project(tmp_path)
    _write(os.path.join(_asset_dir(root), "sub_a_7_8-9_cccccccc.mov"))
    review = os.path.join(root, "pipeline_output", "review")
    os.makedirs(review, exist_ok=True)
    with open(os.path.join(review, "reel_versions.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{ this is not json")
    db = _database(os.path.join(tmp_path, "db", "Project.db"), [], [REEL])

    with pytest.raises(retention.PurgeRefused):
        retention.plan_purge(root, [db])


# ── The build sweep ───────────────────────────────────────────────────


def test_the_build_sweep_keeps_the_media_an_archived_generation_plays(
        tmp_path):
    """The mark calls it LIVE, because a version can still play it."""
    root = str(tmp_path / "project")
    os.makedirs(root)
    kept = _write(os.path.join(_asset_dir(root), "sub_a_1_2-3_aaaaaaaa.mov"))
    disposable = _write(os.path.join(_asset_dir(root), "sub_a_4_5-6_bbbbbbbb.mov"))
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": "/archives/reel26.drt",
            "content_id": "sha256:deadbeef",
            "assets": [{"path": kept, "bytes": 100, "sha256": "0" * 64}],
        },
    }])
    db = _database(os.path.join(root, "db", "Project.db"), [], [REEL])

    roots = gc.collect_resolve_roots([db]) + gc.collect_pipeline_roots(root)
    marked = gc.mark(root, _asset_dir(root), roots)

    live = {asset.path for asset in marked.live}
    orphans = {asset.path for asset in marked.orphans}
    assert kept in live, (
        "a version that can still be restored needs its media")
    assert disposable in orphans, (
        "a file nothing references is still what the sweep is for")


# ── The machine storage plan (`ren storage plan`) ─────────────────────


def _artifact(project: str, path: str, artifact_id: str) -> None:
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id=artifact_id, kind="temporary_render",
        project_id="project-one", run_id="run-one", job_id="job-one",
        path=path, created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(path),
        retention_class="temporary", regenerable=True,
        regeneration_method="rebuild")
    artifact_lifecycle.register_artifact(project, record)
    artifact_lifecycle.transition_artifact(
        project, artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        project, artifact_id, artifact_lifecycle.DISPOSABLE,
        reason="fixture is disposable")


def test_the_storage_plan_refuses_to_name_a_version_dependency(tmp_path):
    """The plan separates a dependency from a disposable scratch file."""
    root = _project(tmp_path)
    scratch = os.path.join(root, "pipeline_output", "scratch")
    os.makedirs(scratch, exist_ok=True)
    dependency = os.path.join(scratch, "caption.mov")
    disposable = os.path.join(scratch, "working.tmp")
    for path in (dependency, disposable):
        with open(path, "wb") as handle:
            handle.write(b"x" * 10)
    _artifact(root, dependency, "artifact-dependency")
    _artifact(root, disposable, "artifact-disposable")
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": "/archives/reel26.drt",
            "content_id": "sha256:deadbeef",
            "assets": [{"path": dependency, "bytes": 10, "sha256": "0" * 64}],
        },
    }])

    document = storage_lifecycle.plan_document([root])

    planned = {row["path"] for row in document["candidates"]}
    assert dependency not in planned, (
        "a version dependency is not disposable because no ledger said so")
    assert disposable in planned, (
        "a disposable scratch file is still a candidate")
    refused = {row["path"] for row in document["refused_dependencies"]}
    assert dependency in refused, (
        "the refusal must be reported, not dropped")


def test_the_storage_purge_refuses_a_dependency_it_planned_before(
        tmp_path):
    """The re-proof at the moment of removal, not the claim at plan time."""
    root = _project(tmp_path)
    scratch = os.path.join(root, "pipeline_output", "scratch")
    os.makedirs(scratch, exist_ok=True)
    dependency = os.path.join(scratch, "caption.mov")
    with open(dependency, "wb") as handle:
        handle.write(b"x" * 10)
    _artifact(root, dependency, "artifact-dependency")
    manifest = tmp_path / "storage-plan.json"
    storage_lifecycle.plan([root], output=str(manifest))

    # The generation is recorded AFTER the plan was written.
    _ledger(root, [{
        "version": 25,
        "kind": "build",
        "rows": {},
        "rows_digest": "",
        "recovery_archive": {
            "timeline_id": "a0c4856c",
            "archive_path": "/archives/reel26.drt",
            "content_id": "sha256:deadbeef",
            "assets": [{"path": dependency, "bytes": 10, "sha256": "0" * 64}],
        },
    }])

    with pytest.raises(artifact_lifecycle.LifecycleRefused,
                       match="version dependency"):
        storage_lifecycle.collect(str(manifest))

    assert os.path.isfile(dependency)


# ── Git-tracked files are untouchable ─────────────────────────────────


@pytest.mark.usefixtures("_lean")
def test_a_git_tracked_file_is_never_a_candidate(tmp_path, _lean):
    """Git tracks it, so no cleanup may name it."""
    import subprocess

    root = _project(tmp_path)
    tracked = _write(os.path.join(root, "pipeline_output", "scratch",
                                  "tracked.tmp"))
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    db = _database(os.path.join(tmp_path, "db", "Project.db"), [], [REEL])

    plan = retention.plan_purge(root, [db])

    candidates = {candidate.path for candidate in plan.candidates}
    assert tracked not in candidates, (
        "a git-tracked file is untouchable, whatever its class")
    kept_rows = [row for row in plan.kept if row["path"] == tracked]
    assert kept_rows and "git" in kept_rows[0]["why"].lower()

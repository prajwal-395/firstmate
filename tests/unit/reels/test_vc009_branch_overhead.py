"""VC-009: what creating a version costs, and the reconstruction it must not pay twice.

The captain's punchlist, 2026-10-20, VC-009: *"Measure branching and
promotion overhead. Determine which state must be copied, archived, or
reconciled for each edit type. Avoid repeated full reconstruction of
unchanged generations. Compare incremental version creation against
full version materialization. Require equivalent restoration and
preservation before optimizing away a safety step."*

The defects this file holds, one test each:

1. A generation that was ALREADY archived was exported AGAIN - a full
   reconstruction of the whole timeline - whenever the version being
   replaced was a later row than the one that carried the archive.
2. The reuse handed back LESS evidence than the export would have: a
   receipt without the media manifest, or one naming a file whose bytes
   had changed.
3. An archive of ANOTHER generation was reused: the same Resolve id
   with different rows is a different generation, and a restore from
   it lands on the wrong state.
4. A changed generation was exported to the SAME file the generation
   before it had used, so the earlier version's receipt stopped
   verifying - the version became unrestorable, in silence.
5. The measurement of what a version costs recorded a number without
   the denominator it is read against.

Each test names the defect it holds; none of them needs a live
Resolve.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeProject,
    FakeTimeline,
    FakeTimelineItem,
)

FINAL = "Reel 26 - write-for-the-question-your-customer-ask"
PROJECT_NAME = "Fixture Project"


# ── the fixtures ─────────────────────────────────────────────────


def _project(tmp_path) -> Path:
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "pipeline_output" / "scratch").mkdir(parents=True)
    return project


def _write_drt(path: Path, timeline_name: str, media: list[Path]) -> Path:
    """A Resolve export that holds ONE timeline and the media it plays."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clips = "".join(
        f'<VideoClip><Name>clip-{index}</Name><Start>0</Start>'
        f'<Duration>10</Duration><In>0</In><MediaRef>1</MediaRef>'
        f'<MediaFilePath>{item}</MediaFilePath></VideoClip>'
        for index, item in enumerate(media))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project.xml",
            '<!--DbAppVer="21.1" DbPrjVer="17"-->'
            f'<Project><Name>{PROJECT_NAME}</Name></Project>')
        archive.writestr(
            f"SeqContainer/{timeline_name}.xml",
            f"<Sequence><Track><TrackType>Video</TrackType>{clips}"
            "</Track></Sequence>")
    return path


def _look_digest(timeline) -> str:
    """The digest of the live generation's LOOK, as the engine reads it."""
    from library.tools import reel_read, reel_replace_guard
    from library.tools.versions import replaced_generation

    tracks = reel_read.read_tracks(timeline)
    items = reel_replace_guard.snapshot_items(tracks)
    return replaced_generation.preservation_digest(
        reel_replace_guard.preservation_record(items))


def _receipt(archive: Path, *, timeline_id: str, rows_digest: str,
             media: list[Path], look_digest: str = "") -> dict:
    return {
        "timeline_name": FINAL,
        "timeline_id": timeline_id,
        "resolve_project": PROJECT_NAME,
        "archive_path": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "content_id": "sha256:" + hashlib.sha256(
            archive.read_bytes()).hexdigest(),
        "archive_bytes": archive.stat().st_size,
        "rows_digest": rows_digest,
        "preservation_digest": look_digest,
        "inventory_items": len(media),
        "assets": [{"path": str(item), "bytes": item.stat().st_size,
                    "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
                   for item in media],
    }


def _live_timeline(project, media: Path, *, uid: str = "resolve-id-1"):
    """A timeline that plays one media file, on a fake project."""
    timeline = FakeTimeline(FINAL, project=project, uid=uid)
    timeline.add_track("video", "Craig")
    pool_item = FakeMediaPoolItem("clip.mov")
    pool_item.ReplaceClip(str(media))
    item = FakeTimelineItem("LCATL0013.MXF", timeline, pool_item=pool_item)
    timeline.add_item("video", 1, item)
    return timeline


def _rows_of(timeline) -> dict:
    from library.tools import reel_read

    return reel_read.rows_of({"tracks": reel_read.read_tracks(timeline)})


def _digest_of(timeline) -> str:
    from library.tools.versions import rounds

    return rounds.digest_rows(_rows_of(timeline))


def _live_reads(monkeypatch, timeline, media: Path) -> None:
    """The two live reads the archive proof is made of."""
    from library.tools import reel_replace_guard

    rows = _rows_of(timeline)
    monkeypatch.setattr(reel_replace_guard, "snapshot_timeline",
                        lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(
        reel_replace_guard, "full_timeline_snapshot",
        lambda *_args, **_kwargs: {"items": [{"source_file": str(media)}]})


def _fake_export(monkeypatch, project: Path, media: list[Path]) -> list:
    """A `.drt` export that writes a real archive, and counts the calls."""
    from library.tools.versions import store

    calls: list = []

    def export(_resolve, _timeline, name, folder, **kwargs):
        suffix = str(kwargs.get("archive_suffix") or "")
        target = (Path(folder) / "pipeline_output" / "review"
                  / "recovery_archives" / f"{name}--{suffix}.drt")
        _write_drt(target, FINAL, media)
        calls.append({"suffix": suffix, "path": str(target)})
        return {"archived": True, "path": str(target)}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", export)
    return calls


# ── 1. the repeated full reconstruction ──────────────────────────


def test_a_generation_that_is_already_archived_is_not_exported_again(
        tmp_path, monkeypatch):
    """The archive of a generation is exported ONCE.

    The defect: the reuse of an existing archive looked only at the
    version being replaced. The archive of a generation is usually
    attached to the version that PRODUCED it, so the version being
    replaced - a later row for the same generation - carried no
    receipt, and the whole timeline was exported again. A `.drt`
    export is a full reconstruction: the expensive half of creating a
    version, paid twice for one generation.
    """
    from library.tools.versions import (
        reel_versions,
        replaced_generation,
    )

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])

    digest = _digest_of(timeline)
    produced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_BUILD,
                                    rows=_rows_of(timeline))
    reel_versions.attach_recovery_archive(
        project, FINAL, int(produced["version"]),
        _receipt(archive, timeline_id="resolve-id-1", rows_digest=digest,
                 media=[media], look_digest=_look_digest(timeline)))
    # The version being replaced is a LATER row for the same
    # generation - the shape a touch leaves behind.
    replaced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_TOUCH,
                                    rows=_rows_of(timeline))

    outcome = replaced_generation.ensure_archived(
        resolve=object(), project=fake_project, timeline=timeline,
        project_folder=str(project), entry=replaced,
        label="the generation being replaced",
        rows_digest=digest)

    assert outcome["status"] == "reused"
    assert calls == [], "the generation was reconstructed a second time"
    assert outcome["receipt"]["archive_path"] == str(archive)


# ── 2. the reuse carries the evidence the export carries ────────


def test_a_reused_archive_carries_the_evidence_an_export_carries(
        tmp_path, monkeypatch):
    """A reused receipt is not a thinner receipt.

    The defect a reuse could introduce: the reader of the version
    cannot tell a reused receipt from an exported one, so a reuse that
    dropped the media manifest - or named a file whose bytes had
    changed - would be read as the same evidence the export path
    produces. It is not.
    """
    from library.tools.versions import (
        reel_versions,
        replaced_generation,
    )

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])

    digest = _digest_of(timeline)
    produced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_BUILD,
                                    rows=_rows_of(timeline))
    receipt = _receipt(archive, timeline_id="resolve-id-1",
                       rows_digest=digest, media=[media],
                       look_digest=_look_digest(timeline))
    reel_versions.attach_recovery_archive(
        project, FINAL, int(produced["version"]), receipt)
    replaced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_TOUCH,
                                    rows=_rows_of(timeline))

    outcome = replaced_generation.ensure_archived(
        resolve=object(), project=fake_project, timeline=timeline,
        project_folder=str(project), entry=replaced,
        label="the generation being replaced",
        rows_digest=digest)
    reused = outcome["receipt"]

    assert outcome["status"] == "reused"
    assert calls == []
    # The same file, the same content identity, the same rows digest,
    # and the media manifest the export path writes.
    assert reused["archive_path"] == receipt["archive_path"]
    assert reused["content_id"] == receipt["content_id"]
    assert reused["rows_digest"] == receipt["rows_digest"]
    assert [asset["path"] for asset in reused["assets"]] == [
        asset["path"] for asset in receipt["assets"]]
    assert [asset["sha256"] for asset in reused["assets"]] == [
        asset["sha256"] for asset in receipt["assets"]]
    assert reused["inventory_items"] == receipt["inventory_items"]


# ── 3. an archive of ANOTHER generation is not reused ───────────


def test_an_archive_of_another_generation_is_not_reused(tmp_path, monkeypatch):
    """The same Resolve id with different rows is a DIFFERENT generation.

    The defect a reuse could introduce: the timeline object is the
    same, so a reader that matched on the Resolve id alone would reuse
    the archive of the generation before the captain's hand edit - and
    a restore from it would land on the wrong state, quietly.
    """
    from library.tools.versions import (
        reel_versions,
        replaced_generation,
    )

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])

    produced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_BUILD,
                                    rows=_rows_of(timeline))
    # The archive of the generation BEFORE the hand edit: the same
    # timeline id, different rows.
    reel_versions.attach_recovery_archive(
        project, FINAL, int(produced["version"]),
        _receipt(archive, timeline_id="resolve-id-1",
                 rows_digest="0" * 16, media=[media],
                 look_digest=_look_digest(timeline)))
    replaced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_TOUCH,
                                    rows=_rows_of(timeline))

    outcome = replaced_generation.ensure_archived(
        resolve=object(), project=fake_project, timeline=timeline,
        project_folder=str(project), entry=replaced,
        label="the generation being replaced",
        rows_digest=_digest_of(timeline))

    assert outcome["status"] == "exported"
    assert len(calls) == 1, "another generation's archive was reused"


def test_an_archive_whose_bytes_changed_is_not_reused(tmp_path, monkeypatch):
    """A file that no longer hashes true is not an archive any more.

    The defect a reuse could introduce: the receipt names a file, and
    the file was overwritten by a later export. A reuse that trusted
    the receipt's path would hand back an archive that holds another
    generation, and the receipt would claim the version is restorable.
    """
    from library.tools.versions import (
        reel_versions,
        replaced_generation,
    )

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])

    digest = _digest_of(timeline)
    produced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_BUILD,
                                    rows=_rows_of(timeline))
    reel_versions.attach_recovery_archive(
        project, FINAL, int(produced["version"]),
        _receipt(archive, timeline_id="resolve-id-1", rows_digest=digest,
                 media=[media], look_digest=_look_digest(timeline)))
    # The file was overwritten by a later export: the receipt's content
    # identity no longer holds.
    archive.write_bytes(b"a different generation")
    replaced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_TOUCH,
                                    rows=_rows_of(timeline))

    outcome = replaced_generation.ensure_archived(
        resolve=object(), project=fake_project, timeline=timeline,
        project_folder=str(project), entry=replaced,
        label="the generation being replaced",
        rows_digest=digest)

    assert outcome["status"] == "exported"
    assert len(calls) == 1


def test_an_archive_of_another_look_is_not_reused(tmp_path, monkeypatch):
    """A hand edit that changes only the GRADE is a different generation.

    The defect a reuse could introduce: the rows say what plays and
    the rows do not change when the captain changes the grade, so an
    archive of the generation BEFORE the grade reads as this one - and a
    restore from it brings back the wrong look. The look is part of the
    generation's identity for exactly this reason.
    """
    from library.tools.versions import replaced_generation, reel_versions

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])

    digest = _digest_of(timeline)
    produced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_BUILD,
                                    rows=_rows_of(timeline))
    # The archive of the generation with ANOTHER look: the same rows,
    # another grade.
    reel_versions.attach_recovery_archive(
        project, FINAL, int(produced["version"]),
        _receipt(archive, timeline_id="resolve-id-1", rows_digest=digest,
                 media=[media], look_digest="f" * 64))
    replaced = reel_versions.record(project, FINAL,
                                    kind=reel_versions.KIND_TOUCH,
                                    rows=_rows_of(timeline))

    outcome = replaced_generation.ensure_archived(
        resolve=object(), project=fake_project, timeline=timeline,
        project_folder=str(project), entry=replaced,
        label="the generation being replaced",
        rows_digest=digest)

    assert outcome["status"] == "exported"
    assert len(calls) == 1, "another look's archive was reused"


# ── 4. a changed generation keeps the earlier archive ───────────


def test_an_adopted_generation_gets_a_file_of_its_own(tmp_path, monkeypatch):
    """A captain's hand edit must not destroy the archive before it.

    The defect: the archive was named by the timeline OBJECT, and one
    object holds several generations. So the second adoption exported
    over the first adoption's file, and the first version's receipt -
    which names that path - stopped verifying. The version became
    unrestorable, and nothing said so.
    """
    from library.tools import reel_adoption
    from library.tools.versions import reel_versions

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])
    monkeypatch.setattr("library.tools.resolve_lock.assert_current_timeline",
                        lambda *_args, **_kwargs: True)

    first = reel_adoption._adopt(
        str(project), fake_project, object(), FINAL,
        reason="the captain corrected this by hand in Resolve",
        by="captain", timeline_id="resolve-id-1")
    first_receipt = first["recovery_archive"]
    first_path = Path(first_receipt["archive_path"])

    # The captain edits the timeline again: the generation changed.
    item = timeline._tracks["video"][0][1][0]
    item._enabled = False
    second = reel_adoption._adopt(
        str(project), fake_project, object(), FINAL,
        reason="the captain corrected it again",
        by="captain", timeline_id="resolve-id-1")
    second_receipt = second["recovery_archive"]

    assert len(calls) == 2, "the changed generation was not exported"
    assert second_receipt["archive_path"] != first_receipt["archive_path"]
    # And the archive of the generation before it is still there, and
    # still verifies: the earlier version is still restorable.
    assert first_path.is_file()
    assert reel_versions.archive_verifies(first_receipt)
    assert reel_versions.archive_verifies(second_receipt)


def test_an_unchanged_adoption_does_not_export_again(tmp_path, monkeypatch):
    """An adoption of a generation that is already archived reuses it.

    The defect: every adoption exported the whole timeline again - the
    full reconstruction of a generation that had not changed. The
    second adoption of an unchanged timeline wrote the same bytes to
    the same path and paid the whole cost of the export for nothing.
    """
    from library.tools import reel_adoption

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    calls = _fake_export(monkeypatch, project, [media])
    monkeypatch.setattr("library.tools.resolve_lock.assert_current_timeline",
                        lambda *_args, **_kwargs: True)

    first = reel_adoption._adopt(
        str(project), fake_project, object(), FINAL,
        reason="the captain corrected this by hand in Resolve",
        by="captain", timeline_id="resolve-id-1")
    second = reel_adoption._adopt(
        str(project), fake_project, object(), FINAL,
        reason="the captain corrected this by hand in Resolve",
        by="captain", timeline_id="resolve-id-1")

    assert len(calls) == 1, "an unchanged generation was exported again"
    assert (second["recovery_archive"]["archive_path"]
            == first["recovery_archive"]["archive_path"])
    # And the reused receipt still names the media the generation plays.
    assets = second["recovery_archive"]["assets"]
    assert [asset["path"] for asset in assets] == [str(media)]


# ── 5. the measurement carries its denominators ─────────────────


def test_the_overhead_measurement_records_a_denominator(tmp_path, monkeypatch):
    """A number without its denominator is a story.

    The defect: the measurement of what creating a version costs
    recorded seconds and bytes and nothing else, so a reader could
    not tell a two-item reel from a two-hundred-item one - and the
    numbers could not be compared between edit types.
    """
    from library.tools.versions import overhead

    project = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")

    fake_project = FakeProject([FakeTimeline(FINAL)])
    timeline = _live_timeline(fake_project, media)
    fake_project.adopt(timeline)
    _live_reads(monkeypatch, timeline, media)
    _fake_export(monkeypatch, project, [media])

    row = overhead.measure(str(project), "promotion", resolve=object(),
                            project=fake_project, timeline=timeline,
                            final=FINAL, staging=f"{FINAL} (measured copy)")

    denominators = row["denominators"]
    assert denominators["items"] == 1
    assert denominators["media_files"] == 1
    assert denominators["media_bytes"] == media.stat().st_size
    assert denominators["versions_before"] == 0
    assert denominators["versions_after"] == 1
    for bucket in ("archive", "copy", "reconcile"):
        assert bucket in row["buckets"]
        assert "seconds" in row["buckets"][bucket]
    # The row is in the project's ledger, and reads back.
    rows = overhead.read_rows(str(project))
    assert len(rows) == 1
    assert rows[0]["edit_type"] == "promotion"
    assert "version overhead" in overhead.report(str(project))

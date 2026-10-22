"""VC-002: the recovery archive must be the generation being replaced.

The captain's punchlist, 2026-10-20, VC-002: *"Keep recoverable timeline
archives. Ensure the recovery archive represents exactly the generation
being replaced. Verify the archived timeline's identity and relevant
structural state. Preserve all media and dependencies required to
restore it. Distinguish restoration from reconstruction. Never claim
bit-identical restoration from an incomplete semantic plan."*

PR 3079 put Reel 26 back from version 25's hash-verified `.drt`. This
file holds the defects that stood next to it, one test each:

1. A reel whose FIRST promotion left a verified archive behind could
   not be promoted AGAIN - the second promotion was refused over a
   receipt that was not being replaced, only joined.
2. The archive of one generation lived at ONE shared per-reel path, so
   the next generation's export OVERWROTE it and the receipt that
   named it stopped verifying: a generation that had been replaced
   could no longer be put back.
3. A restore read the version's FIRST receipt, even when a later
   archive of the same generation was the one on disk and verified.
4. A touch-up replaced a generation and archived NOTHING: the state the
   captain saw was recoverable only by replaying the edit backwards.
5. The archive of a generation was accepted without being read back,
   so a `.drt` of ANOTHER generation passed for this one - the Reel 26
   audit's own finding.
6. A journal replay - the touch reversed in place - was reported like a
   restoration, and a reader could not tell the two apart.

Each test names the defect it holds; none of them needs a live
Resolve.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tests.resolve_double import FakeProject, FakeTimeline

FINAL = "Reel 26 - write-for-the-question-your-customer-ask"

ROWS = {"video:Craig": [{
    "name": "LCATL0013.MXF", "start": 794, "end": 1013, "duration": 219,
    "enabled": True,
}]}


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
            '<Project><Name>Fixture Project</Name></Project>')
        archive.writestr(
            f"SeqContainer/{timeline_name}.xml",
            f"<Sequence><Track><TrackType>Video</TrackType>{clips}"
            "</Track></Sequence>")
    return path


def _receipt(archive: Path, *, timeline_id: str = "resolve-id-1",
             rows_digest: str | None = None, assets=None) -> dict:
    from library.tools.versions import rounds

    return {
        "timeline_name": FINAL,
        "timeline_id": timeline_id,
        "resolve_project": "Fixture Project",
        "archive_path": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "content_id": "sha256:" + hashlib.sha256(
            archive.read_bytes()).hexdigest(),
        "archive_bytes": archive.stat().st_size,
        "rows_digest": rows_digest if rows_digest is not None else rounds.digest_rows(ROWS),
        "inventory_items": 1,
        "assets": assets if assets is not None else [],
    }


def _snapshot_of(media: list[Path]):
    """The live read of a timeline that plays `media`."""
    def snapshot(_timeline, _project, _folder=None):
        return {"items": [{"source_file": str(item)} for item in media]}
    return snapshot


def _rows_of(rows):
    def snapshot(*_args, **_kwargs):
        return rows
    return snapshot


# ── 1. the second promotion ──────────────────────────────────────


def test_a_second_promotion_is_not_refused_over_the_archive_it_already_has(
        tmp_path, monkeypatch):
    """A version that already carries a verified archive must not stop
    the next promotion of that reel.

    The defect: the archive of a generation was attached to the
    version that generation IS, and the attachment REFUSED a second,
    different receipt. So the promotion that replaced a generation
    whose first promotion had left an archive behind was refused -
    over a receipt that was not being replaced, only joined.
    """
    from library.tools import reel_build, reel_replace_guard
    from library.tools.versions import reel_versions, store

    project_dir = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])
    entry = reel_versions.record(project_dir, FINAL,
                                 kind=reel_versions.KIND_BUILD, rows=ROWS)
    reel_versions.attach_recovery_archive(
        project_dir, FINAL, int(entry["version"]), _receipt(archive))

    timeline = FakeTimeline(FINAL)
    monkeypatch.setattr(reel_replace_guard, "full_timeline_snapshot",
                        _snapshot_of([media]))
    monkeypatch.setattr(reel_replace_guard, "snapshot_timeline",
                        _rows_of(ROWS))
    exported: list = []

    def export(*_args, **kwargs):
        exported.append(kwargs)
        return {"archived": True, "path": str(archive)}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", export)

    receipt = reel_build._archive_retiring_generation(
        object(), FakeProject([timeline]), timeline, str(project_dir))

    # The generation is exported ONCE: the archive it already has is
    # kept, not written again.
    assert exported == []
    assert receipt["timeline_id"] == "resolve-id-1"
    # And the version still carries exactly the archive it had.
    history = reel_versions.versions_of(project_dir, FINAL)
    assert len(reel_versions.recovery_archives_of(history[-1])) == 1


def test_a_version_keeps_every_archive_it_was_given(tmp_path):
    """A receipt is never REPLACED - a second archive is ADDED.

    The rule the promotion tripped over was "one receipt per version,
    and it may not change". The rule that holds is "a receipt is
    immutable, and a version keeps every archive it was given", so a
    version can be archived by the build that produced it AND by the
    promotion that replaced it.
    """
    from library.tools.versions import reel_versions

    project_dir = _project(tmp_path)
    first = tmp_path / "first.drt"
    first.write_bytes(b"first archive")
    second = tmp_path / "second.drt"
    second.write_bytes(b"second archive")
    entry = reel_versions.record(project_dir, FINAL, kind="build", rows={})
    reel_versions.attach_recovery_archive(
        project_dir, FINAL, int(entry["version"]),
        {"path": str(first), "sha256": hashlib.sha256(
            first.read_bytes()).hexdigest()})
    reel_versions.attach_recovery_archive(
        project_dir, FINAL, int(entry["version"]),
        {"path": str(second), "sha256": hashlib.sha256(
            second.read_bytes()).hexdigest()})

    history = reel_versions.versions_of(project_dir, FINAL)
    receipts = reel_versions.recovery_archives_of(history[-1])
    assert len(receipts) == 2
    assert receipts[0]["path"] == str(first)
    # The first receipt is still the one the version was born with.
    assert history[-1]["recovery_archive"]["path"] == str(first)


# ── 2. one generation, one archive file ───────────────────────────


def test_one_generation_owns_one_archive_file(tmp_path):
    """Two generations must never land on the same archive file.

    The defect: every generation of a reel was exported to ONE shared
    per-reel path, so the next generation's export OVERWROTE the
    archive of the generation before it - and the receipt that named
    it stopped verifying, which is how a replaced generation became
    unrestorable.
    """
    from library.tools.versions import replaced_generation, store

    class _Resolve:
        EXPORT_DRT = 18

    folder = _project(tmp_path)
    timeline = FakeTimeline(FINAL)
    first = store.export_timeline_recovery_archive(
        _Resolve(), timeline, FINAL, str(folder),
        archive_suffix=replaced_generation.generation_suffix("id-one"))
    second = store.export_timeline_recovery_archive(
        _Resolve(), timeline, FINAL, str(folder),
        archive_suffix=replaced_generation.generation_suffix("id-two"))

    assert first["archived"] is True and second["archived"] is True
    assert first["path"] != second["path"]
    assert "id-one" in Path(first["path"]).name
    assert "id-two" in Path(second["path"]).name
    # The first generation's archive is still there, untouched.
    assert Path(first["path"]).read_bytes() == Path(first["path"]).read_bytes()


def test_the_builds_own_export_names_the_generations_archive(
        tmp_path, monkeypatch):
    """The build's export must name the archive by the generation's id.

    The build exports the timeline it just promoted, so the file it
    writes is the new generation's archive - and it has to be a file
    of its own, or the next build overwrites the archive of the
    generation it is about to replace.
    """
    from library.tools import reel_read
    from library.tools.versions import replaced_generation, rounds, store

    class _Resolve:
        EXPORT_DRT = 18

    captured: list = []

    def export(_resolve, _timeline, _name, _folder, **kwargs):
        captured.append(kwargs.get("archive_suffix"))
        return {"archived": False, "reason": "not needed for this test"}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", export)
    timeline = FakeTimeline(FINAL)
    store._export_reel_recovery_archives(
        _Resolve(), FakeProject([timeline]), [FINAL], {FINAL: timeline},
        {"reels": {FINAL: {"files": [], "items_with_comps": 0}}},
        str(_project(tmp_path)))

    # The name carries the generation: its Resolve id, and (VC-009) the
    # digest of the rows it holds - so two generations of one timeline
    # object cannot land on one file either.
    suffix = captured[0]
    assert suffix.startswith(replaced_generation.generation_suffix(
        timeline.GetUniqueId()))
    rows = reel_read.rows_of({"tracks": reel_read.read_tracks(timeline)})
    assert suffix.endswith(rounds.digest_rows(rows)[:12])


# ── 3. the restore reads the archive that verifies ────────────────


def test_a_restore_reads_the_archive_that_still_verifies(tmp_path):
    """A dead receipt must not hide a live archive of the same version.

    The defect: a restore read the version's FIRST receipt. When a
    later export had replaced the file that receipt named, the restore
    refused with "missing or changed" - while a verified archive of
    the very same generation sat in the version's own record.
    """
    from library.tools import undo_journal as uj
    from library.tools.versions import reel_versions, rounds

    project_dir = _project(tmp_path)
    dead = tmp_path / "dead.drt"
    dead.write_bytes(b"the generation before the overwrite")
    live = tmp_path / "live.drt"
    live.write_bytes(b"the generation, exported again")
    entry = reel_versions.record(project_dir, FINAL,
                                 kind=reel_versions.KIND_BUILD, rows=ROWS)
    reel_versions.attach_recovery_archive(
        project_dir, FINAL, int(entry["version"]),
        _receipt(dead, assets=[]))
    # The archive the promotion of the NEXT generation left behind.
    reel_versions.attach_recovery_archive(
        project_dir, FINAL, int(entry["version"]),
        _receipt(live, assets=[]))
    # The file the first receipt named is gone - overwritten by the
    # next generation's export, which is the whole defect.
    dead.unlink()

    history = reel_versions.versions_of(project_dir, FINAL)
    chosen = reel_versions.live_receipt(history[-1])
    assert chosen is not None
    assert chosen["archive_path"] == str(live)

    # The version that superseded it, so there is something to replace.
    current = reel_versions.record(
        project_dir, FINAL, kind=reel_versions.KIND_ADOPT, rows=ROWS)
    reel_versions.attach_recovery_archive(
        project_dir, FINAL, int(current["version"]),
        _receipt(live, timeline_id="resolve-id-2"))

    restored: list = []
    uj.restore_version(
        str(project_dir), FINAL, int(entry["version"]),
        read_live_rows=lambda _final: ROWS,
        restore_archive=lambda folder, final, target, receipt: (
            restored.append(receipt) or {}))
    assert restored and restored[0]["archive_path"] == str(live)


# ── 4. the touch archives the generation it replaces ──────────────


def test_a_touch_archives_the_generation_it_replaces(tmp_path, monkeypatch):
    """A touch replaces a generation: the replaced one is archived.

    The defect: the touch-up swapped the staging over the approved
    timeline and archived NOTHING. The state the captain saw was
    recoverable only by replaying the edit backwards, which is a
    reconstruction and not a restoration.
    """
    from library.tools import reel_replace_guard, reel_touchup
    from library.tools.versions import reel_versions, store

    project_dir = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])
    entry = reel_versions.record(project_dir, FINAL,
                                 kind=reel_versions.KIND_BUILD, rows=ROWS)

    timeline = FakeTimeline(FINAL)
    project = FakeProject([timeline])
    monkeypatch.setattr(reel_replace_guard, "full_timeline_snapshot",
                        _snapshot_of([media]))
    monkeypatch.setattr(reel_replace_guard, "snapshot_timeline",
                        _rows_of(ROWS))
    monkeypatch.setattr(
        "library.tools.reel_build._connect_resolve",
        lambda: object())
    exported: list = []

    def export(*_args, **kwargs):
        exported.append(kwargs)
        return {"archived": True, "path": str(archive)}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", export)

    outcome = reel_touchup._archive_replaced_generation(
        str(project_dir), project, timeline, FINAL)

    assert outcome["status"] == "exported"
    assert exported, "the replaced generation was never exported"
    history = reel_versions.versions_of(project_dir, FINAL)
    receipts = reel_versions.recovery_archives_of(history[-1])
    assert len(receipts) == 1
    assert receipts[0]["archive_path"] == str(archive)
    assert receipts[0]["timeline_id"] == timeline.GetUniqueId()

    # A second replacement of the same generation does not export it
    # again: one generation, one archive.
    again = reel_touchup._archive_replaced_generation(
        str(project_dir), project, timeline, FINAL)
    assert again["status"] == "verified"
    assert len(exported) == 1


def test_a_touch_says_so_when_the_generation_it_replaces_is_untracked(
        tmp_path, monkeypatch):
    """An unrecorded generation is reported, not guessed at.

    There is no version record to attach an archive to, so the touch
    says "untracked" rather than writing a file nothing can name.
    """
    from library.tools import reel_touchup

    project_dir = _project(tmp_path)
    outcome = reel_touchup._archive_replaced_generation(
        str(project_dir), FakeProject([FakeTimeline(FINAL)]),
        FakeTimeline(FINAL), FINAL)

    assert outcome["status"] == "untracked"
    assert "not a recorded version" in outcome["reason"]


def test_a_variant_choice_archives_the_generation_it_replaces(
        tmp_path, monkeypatch):
    """Choosing a variant is a replacement, and it archives too.

    The defect: the choice retired the incumbent and renamed the chosen
    variant over the reel's name, and archived NOTHING - so the
    approved cut the captain had signed off was recoverable only as
    rows in a ledger.
    """
    from library.tools import reel_replace_guard
    from library.tools.versions import reel_versions, store, variants

    project_dir = _project(tmp_path)
    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])
    entry = reel_versions.record(project_dir, FINAL,
                                 kind=reel_versions.KIND_BUILD, rows=ROWS)

    timeline = FakeTimeline(FINAL)
    project = FakeProject([timeline])
    monkeypatch.setattr(reel_replace_guard, "full_timeline_snapshot",
                        _snapshot_of([media]))
    monkeypatch.setattr(reel_replace_guard, "snapshot_timeline",
                        _rows_of(ROWS))
    monkeypatch.setattr(
        "library.tools.reel_build._connect_resolve", lambda: object())
    exported: list = []

    def export(*_args, **kwargs):
        exported.append(kwargs)
        return {"archived": True, "path": str(archive)}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", export)

    outcome = variants._archive_replaced_generation(
        str(project_dir), project, timeline, FINAL)

    assert outcome["status"] == "exported"
    assert exported, "the replaced generation was never exported"
    history = reel_versions.versions_of(project_dir, FINAL)
    receipts = reel_versions.recovery_archives_of(history[-1])
    assert len(receipts) == 1
    assert receipts[0]["archive_path"] == str(archive)


# ── 5. the archive must hold the generation ───────────────────────


def test_an_archive_of_another_generation_is_refused(tmp_path, monkeypatch):
    """The Reel 26 finding: a `.drt` of a DIFFERENT generation.

    An archive that holds another generation's items proves nothing
    about this one, so the archive is refused by name rather than
    accepted because it exists.
    """
    from library.tools import reel_replace_guard
    from library.tools.versions import replaced_generation

    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    other = tmp_path / "other.mov"
    other.write_bytes(b"other media bytes")
    archive = _write_drt(tmp_path / "other.drt", FINAL, [other])

    timeline = FakeTimeline(FINAL)
    monkeypatch.setattr(reel_replace_guard, "full_timeline_snapshot",
                        _snapshot_of([media]))
    monkeypatch.setattr(reel_replace_guard, "snapshot_timeline",
                        _rows_of(ROWS))

    with pytest.raises(replaced_generation.ReplacedGenerationArchiveRefused,
                       match="not the generation being replaced"):
        replaced_generation.verify_archive(
            archive_path=archive, project=FakeProject([timeline]),
            timeline=timeline, project_folder=str(tmp_path))


def test_a_verified_archive_carries_the_media_it_needs(tmp_path, monkeypatch):
    """The receipt names every media file, hashed.

    A `.drt` alone cannot prove the media is there; the receipt's
    manifest is what says a restore would find the same bytes.
    """
    from library.tools import reel_replace_guard
    from library.tools.versions import replaced_generation

    media = tmp_path / "source.mov"
    media.write_bytes(b"media bytes")
    archive = _write_drt(tmp_path / "gen.drt", FINAL, [media])

    timeline = FakeTimeline(FINAL)
    monkeypatch.setattr(reel_replace_guard, "full_timeline_snapshot",
                        _snapshot_of([media]))
    monkeypatch.setattr(reel_replace_guard, "snapshot_timeline",
                        _rows_of(ROWS))

    receipt = replaced_generation.verify_archive(
        archive_path=archive, project=FakeProject([timeline]),
        timeline=timeline, project_folder=str(tmp_path))

    assert receipt["timeline_id"] == timeline.GetUniqueId()
    assert receipt["inventory_items"] == 1
    assert receipt["assets"] == [{
        "path": str(media),
        "sha256": hashlib.sha256(b"media bytes").hexdigest(),
        "bytes": len(b"media bytes"),
    }]


# ── 6. restoration and reconstruction are different things ────────


def test_a_journal_replay_is_reported_as_a_reconstruction(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """A touch reversed in place is a REPLAY, and it says so.

    The undo of a touch re-applies the inverse of the recorded edit.
    It is not a restoration out of a recovery archive, and a reader
    that cannot tell the two apart will believe the archive was never
    needed.
    """
    import manage_project

    monkeypatch.setattr(
        "library.tools.undo_journal.explain_undo",
        lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "library.tools.undo_journal.render_undo_plan", lambda *_a: "")
    monkeypatch.setattr(
        "library.tools.undo_journal.undo",
        lambda *args, **kwargs: [{
            "entry": "journal-1", "version": 7,
            "restored_from": "journal_replay",
            "restored_from_archive": False,
        }])

    class _Args:
        project = str(_project(tmp_path))
        reel = None
        list = False
        supersede = ()
        accept_editor_changes = None
        restore_version = None
        act = None

    manage_project.cmd_undo(_Args())
    printed = capsys.readouterr().out
    assert "replay of the recorded edit" in printed
    assert "not a restoration out of a recovery archive" in printed

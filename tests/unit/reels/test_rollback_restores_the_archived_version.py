"""A rollback restores the ARCHIVED version, not a rebuild of the plan.

Defect (Reel 26 audit §2.4): rollback refused twice. The earlier state
could not be reconstructed exactly from the plan and the touch
journals, so the operation refused - while an immutable, verified
`.drt` archive of that exact generation sat on disk.

The rule these tests hold: the PLAN is never the sole rollback payload.
A version that carries a verified archive is restored from that
archive, by Resolve identity; a version that carries none is rebuilt
from the plan and RECORDED as a reconstruction, because a rebuild
reproduces the cut and not the grade, the Fusion graph or the colour.
"""

from __future__ import annotations

import json

from library.tools import undo_journal
from library.tools.versions import reel_versions, rounds


ROWS = {"Craig": [{
    "row": "Craig", "start": 0, "duration": 220, "left_offset": 11537,
    "source_file": "/media/LCATL0013.MXF", "name": "LCATL0013.MXF",
}]}


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    return project


def _write_versions(project, entries):
    path = project / "pipeline_output" / "review" / "reel_versions.json"
    path.write_text(json.dumps({
        "format": "reel_versions/1",
        "reels": {"Reel 26 - x": entries},
    }), encoding="utf-8")


def _entry(version, *, archive=None, moment="moment-26"):
    entry = {
        "version": version,
        "kind": reel_versions.KIND_BUILD,
        "rows": ROWS,
        "rows_digest": rounds.digest_rows(ROWS),
        "plan_moment": {"number": 26, "id": moment},
    }
    if archive is not None:
        entry["recovery_archive"] = archive
    return entry


def test_a_version_with_a_verified_archive_is_restored_from_it(tmp_path):
    """The archive is the payload; the plan is not."""
    project = _project(tmp_path)
    receipt = {"timeline_id": "a0c4856c", "archive_path": "/a/reel26.drt",
               "content_id": "sha256:" + "0" * 64,
               "archive_sha256": "0" * 64, "inventory_items": 1,
               "assets": []}
    _write_versions(project, [_entry(22, archive=receipt),
                              _entry(23)])

    restored = []
    built = []

    def restore_archive(project_folder, final, target, receipt):
        restored.append((final, int(target["version"])))
        return {"timeline_id": receipt["timeline_id"]}

    result = undo_journal.rollback_rebuild(
        str(project), "Reel 26 - x", _entry(23),
        read_live_rows=lambda final: ROWS,
        build=lambda folder, reel: built.append(reel) or 0,
        restore_archive=restore_archive)

    assert built == []
    assert restored == [("Reel 26 - x", 22)]
    assert result["restored_from_archive"] is True
    assert result["to"] == 22


def test_a_version_with_no_archive_is_rebuilt_and_recorded_as_such(
        tmp_path, monkeypatch):
    """A rebuild is a reconstruction, and says so."""
    monkeypatch.setattr(undo_journal, "restore_plan_moment",
                        lambda folder, moment: "moment-26")
    project = _project(tmp_path)
    _write_versions(project, [_entry(22, moment="moment-26"),
                              _entry(23, moment="moment-27")])

    built = []

    def build(folder, reel):
        built.append(reel)
        # A real build's promotion consumes the pending-rollback marker;
        # a marker still standing means nothing was promoted.
        reel_versions.take_pending_rollback(folder, "Reel 26 - x")
        return 0

    result = undo_journal.rollback_rebuild(
        str(project), "Reel 26 - x", _entry(23, moment="moment-27"),
        read_live_rows=lambda final: ROWS,
        build=build)

    assert built == [26]
    assert result["restored_from"] == "plan_rebuild"
    assert result["restored_from_archive"] is False


def test_a_same_plan_generation_with_an_archive_is_never_rebuilt(tmp_path):
    """The case the audit hit: two refusals, and an archive on disk."""
    project = _project(tmp_path)
    receipt = {"timeline_id": "a0c4856c", "archive_path": "/a/reel26.drt",
               "content_id": "sha256:" + "0" * 64,
               "archive_sha256": "0" * 64, "inventory_items": 1,
               "assets": []}
    _write_versions(project, [_entry(22, archive=receipt),
                              _entry(23)])

    restored = []
    built = []

    undo_journal.rollback_rebuild(
        str(project), "Reel 26 - x", _entry(23),
        read_live_rows=lambda final: ROWS,
        build=lambda folder, reel: built.append(reel) or 0,
        restore_archive=lambda folder, final, target, receipt: (
            restored.append(int(target["version"])) or {}))

    assert built == []
    assert restored == [22]

"""A RECORDED version can be restored, by name, from its own archive.

The captain, 2026-10-21, on Reel 26: he was shown the closing shot as
PR 3009 left it and the closing shot as version 25 had it, and he
answered "i like this version" about the v25 frame. The version path
had no way to put it back:

- an ADOPTED generation is deliberately not an ACT, so `ren undo`
  refused by name ("no act '26' is recorded") - and an act can only be
  rolled back to the version BEFORE it, so no act reached v25 either;
- a receipt written before the current receipt format carries `path`,
  `sha256` and `timeline_unique_id` and no asset manifest, and the
  archive reader asked for the current keys - so the restore raised
  instead of restoring;
- two producers read the same timeline and differ in exactly the
  fields only one of them reads (the caption tokens the live projection
  attaches), so every comparison of a recorded version with the live
  timeline read as "the timeline moved on" and refused.

These tests hold the rule: a version that carries a verified recovery
archive is a version Ren can put BACK, whatever kind it is and whatever
format its receipt was written in - and nothing is deleted to do it.
"""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reel_read
from library.tools import undo_journal as uj
from library.tools.versions import (
    reel_versions,
    rounds,
)

FINAL = "Reel 26 - write-for-the-question-your-customer-ask"

ROWS = {"video:Craig": [{
    "name": "LCATL0013.MXF", "start": 794, "end": 1013, "duration": 219,
    "enabled": True,
}]}


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    return project


def _write_versions(project, entries):
    path = project / "pipeline_output" / "review" / "reel_versions.json"
    path.write_text(json.dumps({
        "format": "reel_versions/1",
        "reels": {FINAL: entries},
    }), encoding="utf-8")


def _entry(version, *, kind=reel_versions.KIND_TOUCH, archive=None,
           rows=None):
    rows = ROWS if rows is None else rows
    entry = {
        "version": version,
        "kind": kind,
        "rows": rows,
        "rows_digest": rounds.digest_rows(rows),
    }
    if archive is not None:
        entry["recovery_archive"] = archive
    return entry


def _current_receipt(timeline_id="a0c4856c"):
    """A receipt in the CURRENT format."""
    return {
        "timeline_id": timeline_id,
        "archive_path": "/a/reel26-v26.drt",
        "archive_sha256": "0" * 64,
        "rows_digest": rounds.digest_rows(ROWS),
        "inventory_items": 1,
        "assets": [],
    }


def _legacy_receipt(timeline_id="a0c4856c"):
    """A receipt as it was written BEFORE the current format."""
    return {
        "path": "/a/reel26-v25.drt",
        "sha256": "1" * 64,
        "timeline_unique_id": timeline_id,
        "project": "Podcast (field test)",
        "rows_digest": rounds.digest_rows(ROWS),
        "exported_at": "20261019T035335Z",
    }


def test_a_version_no_act_can_undo_is_still_restorable(tmp_path):
    """An adopted generation is a version, and a version can come back."""
    project = _project(tmp_path)
    _write_versions(project, [
        _entry(25, archive=_legacy_receipt()),
        _entry(26, kind=reel_versions.KIND_ADOPT,
               archive=_current_receipt()),
    ])

    restored = []

    def restore_archive(project_folder, final, target, receipt):
        restored.append((final, int(target["version"])))
        return {"timeline_id": receipt.get("timeline_unique_id")}

    result = uj.restore_version(
        str(project), FINAL, 25,
        read_live_rows=lambda _final: ROWS,
        restore_archive=restore_archive)

    assert restored == [(FINAL, 25)]
    assert result["restored"] == 25
    assert result["replaced"] == 26
    assert result["restored_from_archive"] is True
    history = reel_versions.versions_of(project, FINAL)
    # Nothing is deleted: the versions it replaced stay, and the restore
    # is recorded as its own version.
    assert len(history) == 3
    assert history[0]["version"] == 25
    assert history[1]["version"] == 26
    assert history[2]["kind"] == reel_versions.KIND_ROLLBACK
    assert history[2]["undoes"] == 26
    assert history[1]["undone_by"] == history[2]["version"]


def test_a_receipt_written_before_the_current_format_is_still_read(tmp_path):
    """The receipt's FORMAT is not the state: the archive is the payload."""
    project = _project(tmp_path)
    _write_versions(project, [
        _entry(25, archive=_legacy_receipt()),
        _entry(26, kind=reel_versions.KIND_ADOPT,
               archive=_current_receipt()),
    ])

    restored = []
    uj.restore_version(
        str(project), FINAL, 25,
        read_live_rows=lambda _final: ROWS,
        restore_archive=lambda folder, final, target, receipt: (
            restored.append(receipt) or {}))

    assert restored and restored[0]["path"] == "/a/reel26-v25.drt"


def test_a_restore_refuses_a_version_with_no_verified_archive(tmp_path):
    """No archive, no exact restore: the version says so."""
    project = _project(tmp_path)
    _write_versions(project, [
        _entry(25),
        _entry(26, kind=reel_versions.KIND_ADOPT,
               archive=_current_receipt()),
    ])

    with pytest.raises(uj.UndoRefused, match="no verified recovery"):
        uj.restore_version(
            str(project), FINAL, 25,
            read_live_rows=lambda _final: ROWS,
            restore_archive=lambda *_a: pytest.fail("nothing may be "
                                                     "restored"))


def test_a_restore_refuses_when_the_live_timeline_moved_on(tmp_path):
    """Work done since the recorded version is not destroyed."""
    project = _project(tmp_path)
    _write_versions(project, [
        _entry(25, archive=_legacy_receipt()),
        _entry(26, kind=reel_versions.KIND_ADOPT,
               archive=_current_receipt()),
    ])
    moved = {"video:Craig": [{
        "name": "LCATL0013.MXF", "start": 794, "end": 1013,
        "duration": 219, "enabled": True,
    }], "video:Hand": [{"name": "manual.mov", "start": 0, "end": 10,
                        "duration": 10, "enabled": True}]}

    with pytest.raises(uj.TimelineMovedSinceTouch):
        uj.restore_version(
            str(project), FINAL, 25,
            read_live_rows=lambda _final: moved,
            restore_archive=lambda *_a: pytest.fail("nothing may be "
                                                     "restored"))

    assert len(reel_versions.versions_of(project, FINAL)) == 2


def test_two_projections_of_one_timeline_agree_on_the_fields_both_carry():
    """The live read carries caption tokens; a recorded snapshot may not."""
    recorded = {"video:Subtitles": {
        "media_type": "video", "index": 4, "name": "Subtitles",
        "items": [{"name": "sub.mov", "start": 0, "end": 30,
                   "duration": 30, "enabled": True}],
        "count": 1, "frames": 30}}
    live = json.loads(json.dumps(recorded))
    live["video:Subtitles"]["caption_tokens"] = [["what", "kind"]]

    assert rounds.rows_agree(recorded, live)
    assert rounds.rows_agree(live, recorded)

    moved = json.loads(json.dumps(live))
    moved["video:Subtitles"]["items"][0]["start"] = 5
    assert not rounds.rows_agree(recorded, moved)


def test_the_archive_restore_reads_a_receipt_written_before_the_current_format(
        tmp_path, monkeypatch):
    """The archive reader must not raise on a receipt it does not know.

    The version the captain asked for carried a receipt written before
    the current format, so the reader's demand for the current keys
    raised `KeyError` where a restore should have happened.
    """
    from library.tools import resolve_archive, resolve_lock
    from library.tools.versions import store
    from tests.composed_edit_harness import build_reel, duplicate

    timeline, pool, _media = build_reel(tmp_path)
    timeline.SetName(FINAL)
    project = timeline._project
    project.SetCurrentTimeline(None)
    archived_rows = reel_read.rows_of(
        {"tracks": reel_read.read_tracks(timeline)})
    archive_path = tmp_path / "saved-version.drt"
    archive_path.write_bytes(b"verified DRT")
    receipt = {
        "path": str(archive_path),
        "sha256": store._sha256(archive_path),
        "timeline_unique_id": "original-archived-id",
        "rows_digest": rounds.digest_rows(archived_rows),
    }
    monkeypatch.setattr(resolve_archive, "read_archive", lambda _path: {
        "timelines": [{"tracks": []}]})
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(uj, "_resolve_name", lambda _folder: "qualification")
    monkeypatch.setattr(uj, "_connect", lambda _name: project)
    monkeypatch.setattr(
        pool, "ImportTimelineFromFile",
        lambda _path, _options: duplicate(timeline, name=archive_path.stem))

    result = uj._restore_archive_by_resolve_id(
        str(tmp_path), FINAL,
        {"version": 25, "rows": archived_rows,
         "rows_digest": rounds.digest_rows(archived_rows)},
        receipt, expected_current_id=timeline.GetUniqueId())

    assert result["timeline_id"] == "original-archived-id"
    restored = [project.GetTimelineByIndex(index)
                for index in range(1, project.GetTimelineCount() + 1)
                if project.GetTimelineByIndex(index).GetName() == FINAL]
    assert len(restored) == 1

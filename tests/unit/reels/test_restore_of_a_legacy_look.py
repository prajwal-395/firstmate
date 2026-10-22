"""A restore is checked against the look the RECORDED version held.

Two defects, both measured on the live VC-004 restore of 2026-10-21,
on a Ren-owned scratch copy, through the serving broker:

1. The comparison demanded the same KEYS on both sides, so a read that
   grew a field read as a LOST effect. The Fusion comps were added to
   the read by #3097 on 2026-10-21; a look recorded before that carries
   no `comps` key, and the restore refused a generation that KEPT its
   look - the rows came back right, the comp was still there, and the
   ledger was told the effect was lost.

2. The look was checked AFTER the restore had already put the archive's
   generation under the reel's name, so a refused restore left the
   timeline replaced and the ledger saying nothing had happened. The
   check belongs where the evidence is: on the imported archive, before
   anything is replaced.

The rule these hold: a LOSS is what the recorded look held and the live
one does not. A detail the live read carries that the recorded look
never held is not a loss - the record could not speak about it.
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

from library.tools import reel_read, reel_replace_guard
from library.tools import undo_journal as uj
from library.tools.versions import rounds, store

FINAL = "Reel 26 - write-for-the-question-your-customer-ask"

#: The look as it was RECORDED on 2026-10-21, before the read grew the
#: Fusion comps: one item, one Fusion composition, no colour.
_RECORDED = [{
    "name": "vc004_clip.mp4",
    "track_type": "video",
    "track_index": 1,
    "source_identity": "media:d65ff627-3e16-442b-950e-5232ab642d14",
    "source_in_frame": 0,
    "source_out_frame": 48,
    "record_in": 0,
    "record_out": 48,
    "enabled": False,
    "source_file": "/tmp/vc004_clip.mp4",
    "color": {"cdl": {}, "color_group": ""},
    "fusion": {"comp_count": 1, "comp_names": ["Composition 1"]},
    "composite": {"CompositeMode": 0, "Opacity": 100.0},
}]


def _live(**fusion):
    """The same item as the read of 2026-10-21 carries it now."""
    item = json.loads(json.dumps(_RECORDED[0]))
    item["fusion"] = {
        "comp_count": 1,
        "comp_names": ["Composition 1"],
        "comps": [{"comp_index": 1, "name": "Composition 1",
                   "tools": ["BrightnessContrast", "MediaIn", "MediaOut"],
                   "canvas": None}],
    }
    item["fusion"].update(fusion)
    return [item]


# ── the comparison: what the record held, not what it never held ──────

def test_a_look_recorded_before_the_read_grew_is_not_a_lost_effect():
    """The defect the live run found: a restore that kept the look was
    refused because today's read carries a key the record never had."""
    losses = reel_replace_guard.preservation_losses(_RECORDED, _live())

    assert losses == []


def test_a_live_look_that_lost_its_comp_is_still_a_loss():
    """The fidelity of the fix: what the record held and the live one
    does not is still a loss, and is still named."""
    without_the_comp = _live()
    without_the_comp[0]["fusion"]["comp_count"] = 0
    without_the_comp[0]["fusion"]["comp_names"] = []

    losses = reel_replace_guard.preservation_losses(
        _RECORDED, without_the_comp)

    assert [loss["field"] for loss in losses] == ["fusion"]


def test_a_live_look_that_lost_a_comp_is_still_a_loss():
    """A comp that was there and is not now is a loss, by name."""
    recorded = _live()
    live = _live()
    live[0]["fusion"]["comps"] = []

    losses = reel_replace_guard.preservation_losses(recorded, live)

    assert [loss["field"] for loss in losses] == ["fusion"]


def test_a_look_the_record_never_held_is_not_checked_against_the_live():
    """The comparison is a LOSS comparison, in one direction.

    The recorded look is what must survive; a detail the live read
    carries and the record never held is not a loss of it.
    """
    live = _live()
    live[0]["color"]["cdl"] = {"Slope": "1.20 0.95 0.80"}

    assert reel_replace_guard.preservation_losses(_RECORDED, live) == []
    assert reel_replace_guard.preservation_losses(live, _RECORDED)


# ── the refusal happens BEFORE anything is replaced ───────────────────

def _restore_setup(tmp_path, monkeypatch, *, preservation):
    """A fake Resolve project, an archive on disk, and the receipt."""
    from tests.composed_edit_harness import build_reel, duplicate

    timeline, pool, _media = build_reel(tmp_path)
    timeline.SetName(FINAL)
    project = timeline._project
    project.SetCurrentTimeline(None)
    rows = reel_read.rows_of({"tracks": reel_read.read_tracks(timeline)})
    archive_path = tmp_path / "saved-version.drt"
    archive_path.write_bytes(b"verified DRT")
    receipt = {
        "timeline_id": "original-archived-id",
        "archive_path": str(archive_path),
        "archive_sha256": store._sha256(archive_path),
        "content_id": store.recovery_archive_content_id(archive_path),
        "rows_digest": rounds.digest_rows(rows),
        "inventory_items": 0,
        "assets": [],
        "preservation": preservation,
    }
    target = {"version": 25, "rows": rows,
              "rows_digest": rounds.digest_rows(rows),
              "recovery_archive": receipt}

    from library.tools import resolve_archive, resolve_lock

    monkeypatch.setattr(resolve_archive, "read_archive", lambda _path: {
        "timelines": [{"tracks": []}]})
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(resolve_lock, "held", lambda: True)
    monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: True)
    monkeypatch.setattr(uj, "_resolve_name", lambda _folder: "qualification")
    monkeypatch.setattr(uj, "_connect", lambda _name: project)
    monkeypatch.setattr(
        pool, "ImportTimelineFromFile",
        lambda _path, _options: duplicate(timeline, name=archive_path.stem))
    return timeline, project, target, receipt


def _live_look(timeline):
    """The look of the live timeline, as the reader sees it."""
    tracks = reel_read.read_tracks(timeline)
    return reel_replace_guard.preservation_record(
        reel_replace_guard.snapshot_items(tracks))


def test_a_restore_that_cannot_keep_the_look_leaves_the_timeline_alone(
        tmp_path, monkeypatch):
    """The refusal happens BEFORE the timeline is replaced.

    Measured on the live VC-004 restore of 2026-10-21: the look was
    checked after the archive's generation had already taken the reel's
    name, so a refused restore left the timeline holding a generation
    the ledger knew nothing about. The check belongs on the imported
    archive, while the live generation is still in place.
    """
    timeline, project, target, receipt = _restore_setup(
        tmp_path, monkeypatch, preservation=[])

    # A look the live timeline does not hold: the archive carries no
    # grade, and the version recorded one.
    recorded = _live_look(timeline)
    recorded[0]["color"]["cdl"] = {"Slope": "1.20 0.95 0.80"}
    target["recovery_archive"]["preservation"] = recorded
    live_id = timeline.GetUniqueId()

    with pytest.raises(uj.UndoNotVerified) as refused:
        uj._restore_archive_by_resolve_id(
            str(tmp_path), FINAL, target, receipt,
            expected_current_id=live_id)

    assert "does not hold the grade and effects" in str(refused.value)
    assert "The timeline was NOT changed" in str(refused.value)
    # The live generation is still there, under its own name and id.
    finals = [project.GetTimelineByIndex(index)
              for index in range(1, project.GetTimelineCount() + 1)
              if project.GetTimelineByIndex(index).GetName() == FINAL]
    assert len(finals) == 1
    assert finals[0].GetUniqueId() == live_id
    # And the imported archive was not left behind.
    assert not [project.GetTimelineByIndex(index)
                for index in range(1, project.GetTimelineCount() + 1)
                if project.GetTimelineByIndex(index).GetName()
                == "saved-version"]


def test_a_restore_that_keeps_the_look_replaces_the_timeline(
        tmp_path, monkeypatch):
    """The gate still opens: a restore that keeps the look goes through."""
    timeline, project, target, receipt = _restore_setup(
        tmp_path, monkeypatch, preservation=[])
    target["recovery_archive"]["preservation"] = _live_look(timeline)
    live_id = timeline.GetUniqueId()

    result = uj._restore_archive_by_resolve_id(
        str(tmp_path), FINAL, target, receipt,
        expected_current_id=live_id)

    assert result["restored"] is True
    finals = [project.GetTimelineByIndex(index)
              for index in range(1, project.GetTimelineCount() + 1)
              if project.GetTimelineByIndex(index).GetName() == FINAL]
    assert len(finals) == 1
    assert finals[0].GetUniqueId() != live_id


def test_a_restored_version_names_the_object_that_holds_it(
        tmp_path, monkeypatch):
    """The ledger must name where the generation IS.

    A restore imports the archive as a NEW Resolve timeline and puts
    that under the reel's name, so the object that holds the restored
    generation is not the object the archive was taken from. Measured on
    the live VC-004 restore of 2026-10-21: the archive was taken from
    `e4e3cf87-...` and the timeline that holds the restored generation
    is `2891be92-...`.

    A version that named the archive's id left the ledger pointing at an
    object that no longer existed, and the next restore refused it: "a
    different generation occupies the final name".
    """
    from library.tools.versions import reel_versions

    timeline, resolve_project, target, receipt = _restore_setup(
        tmp_path, monkeypatch, preservation=[])
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    live_id = timeline.GetUniqueId()
    rows = target["rows"]
    target["recovery_archive"]["preservation"] = _live_look(timeline)

    def _entry(version, kind, rows_digest, archive):
        return {"version": version, "kind": kind, "rows": rows,
                "rows_digest": rows_digest, "recovery_archive": archive}

    _write_ledger(project, [
        _entry(25, reel_versions.KIND_ADOPT,
               rounds.digest_rows(rows), receipt),
        _entry(26, reel_versions.KIND_BUILD, rounds.digest_rows(rows),
               {"timeline_id": live_id, "archive_path": "",
                "archive_sha256": "", "rows_digest": rounds.digest_rows(rows),
                "assets": []}),
    ])

    result = uj.restore_version(str(project), FINAL, 25,
                                read_live_rows=lambda _final: rows)

    assert result["restored"] == 25
    entries = reel_versions.versions_of(str(project), FINAL)
    recorded = entries[-1]
    assert recorded["kind"] == reel_versions.KIND_ROLLBACK
    # The object that holds the restored generation, read back from
    # Resolve after the restore.
    live = [resolve_project.GetTimelineByIndex(index)
            for index in range(1, resolve_project.GetTimelineCount() + 1)
            if resolve_project.GetTimelineByIndex(index).GetName() == FINAL]
    assert len(live) == 1
    assert recorded["generation"]["resolve_timeline_id"] == live[0].GetUniqueId()
    assert recorded["recovery_archive"]["timeline_id"] == live[0].GetUniqueId()
    # And where the content came from is kept as its own field.
    assert recorded["restored_from_timeline_id"] == "original-archived-id"


def _write_ledger(project, entries):
    path = project / "pipeline_output" / "review" / "reel_versions.json"
    path.write_text(json.dumps({
        "format": "reel_versions/1",
        "reels": {FINAL: entries},
    }), encoding="utf-8")

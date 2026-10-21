"""Undo and redo, per edit type: a touch is a way back, whatever it changed.

Each test names the defect it catches (docs/TEST_AUTHORING.md):

- a touch whose operation the journal could not classify LANDED and left
  no way back - the journal never closed, so `ren undo` refused and the
  retired generation stayed the only way home;
- an undo that read a TRIMMED item as one item removed and another
  added, and then refused for want of a capture of the old one;
- an undo that left the timeline other than it was before the touch;
- a redo that did not put the touch back.

Driven against the fake Resolve the composed-edit tests use
(`tests/composed_edit_harness.py`); every project is under `tmp_path`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reel_read
from library.tools import undo_journal as uj
from library.tools.versions import reel_versions
from tests.unit.resolve.test_undo_journal import (
    _mark_undone,
    _redo_touch_in_place,
    _touch,
    _undo,
)

FINAL = "Reel 01 - lab"

#: A trim: the item stays where it plays and plays different source
#: frames. It is the edit type the journal used to be unable to hold.
TRIM = {"reel": 1, "edits": [{"op": "slip", "row": "V1", "item": 1,
                              "delta_frames": 12}]}


def test_a_trim_touch_closes_its_journal_and_undoes_exactly(
        tmp_path, monkeypatch):
    """A trim used to land with NO way back.

    The journal could not classify a source-window edit, so it never
    closed: the touch was on the timeline, the version was never
    recorded, and `ren undo` refused with "not applied".
    """
    folder, approved, staged, entry, _by_path = _touch(
        tmp_path, TRIM, monkeypatch=monkeypatch)

    assert entry["status"] == uj.STATUS_APPLIED
    assert entry["version"], "the journal closed without a version"
    assert "accepted_edit" in entry, (
        "the touch was accepted, so its rebuild policy is recorded")

    before = uj.projection(reel_read.read_tracks(approved))

    receipt = _undo(tmp_path, folder, staged, entry, _by_path)

    assert receipt["verified"] is True
    assert uj.projection(reel_read.read_tracks(staged)) == before


def test_the_undo_pairs_a_trimmed_item_with_its_before_state():
    """The plan reads a trim as the SAME item, not as two.

    A trimmed item keeps its row, its file, its name and its played
    span; only the frames it plays FROM move. The plan paired items by
    their source trim, so a trim looked like a removal and an addition
    - and the undo then refused for want of a capture.
    """
    def detail(row, record_in, duration, left_offset, name="card"):
        return {"row": row, "record_in": record_in, "duration": duration,
                "left_offset": left_offset, "source_file": "/lab/card.mov",
                "name": name, "track_type": "video", "track_index": 4,
                "transform": {}, "fusion": {}, "clip_color": "",
                "enabled": True}

    before = [{"type": "video", "index": 4, "clips": [
        detail("V4", 100, 40, 0), detail("V4", 200, 40, 0)]}]
    after = [{"type": "video", "index": 4, "clips": [
        detail("V4", 100, 40, 12), detail("V4", 200, 40, 0)]}]

    plan = uj.plan_inverse(before, after)

    assert not plan.reinserts, (
        "a trimmed item was read as a removal the journal cannot put back")
    assert not plan.deletions
    assert len(plan.restores) == 1
    assert plan.restores[0][1]["left_offset"] == 0


def test_the_trim_pairing_refuses_when_it_cannot_say_which_item_it_is():
    """Two items cut from one file are not paired by accident."""
    def detail(record_in, left_offset):
        return {"row": "V4", "record_in": record_in, "duration": 40,
                "left_offset": left_offset, "source_file": "/lab/card.mov",
                "name": "card", "track_type": "video", "track_index": 4,
                "transform": {}, "fusion": {}, "clip_color": "",
                "enabled": True}

    before = [{"type": "video", "index": 4, "clips": [
        detail(100, 0), detail(100, 40)]}]
    after = [{"type": "video", "index": 4, "clips": [
        detail(100, 12), detail(100, 52)]}]

    with pytest.raises(uj.UndoRefused):
        uj.plan_inverse(before, after)


def test_an_unclassified_operation_still_closes_the_journal(
        tmp_path, monkeypatch):
    """An unknown operation is a refusal on the record, not a lost way back.

    The journal is the way back. A classification the engine cannot make
    is recorded, by name, and the journal closes anyway - the touch that
    already landed can then be undone.
    """
    from tests.composed_edit_harness import build_reel

    folder = tmp_path / "project"
    folder.mkdir()
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [{"op": "an_op_the_gate_does_not_know",
                                   "row": "V4", "item": 0}]}
    entry = uj.open_entry(
        str(folder), final=FINAL, reel=1, resolve_project="lab", spec=spec,
        gate_class="composed", source_timeline=timeline, removals=())

    closed = uj.close_entry(
        str(folder), entry, after_timeline=timeline,
        rows=reel_read.rows_of({"tracks": reel_read.read_tracks(timeline)}))

    assert closed["status"] == uj.STATUS_APPLIED
    assert closed["version"], "the journal closed without a version"
    assert closed["accepted_edit_refusal"]["what"], (
        "the refusal is recorded, by name, and not swallowed")
    assert "accepted_edit" not in closed
    assert uj.read_entry(str(folder), closed["id"])["status"] == (
        uj.STATUS_APPLIED)


def test_undo_then_redo_restores_a_trim_exactly(tmp_path, monkeypatch):
    """The redo puts the trim back, and the timeline reads as it did."""
    folder, approved, staged, entry, by_path = _touch(
        tmp_path, TRIM, monkeypatch=monkeypatch)
    before = uj.projection(reel_read.read_tracks(approved))
    after_touch = uj.projection(reel_read.read_tracks(staged))
    assert after_touch != before

    _undo(tmp_path, folder, staged, entry, by_path)
    assert uj.projection(reel_read.read_tracks(staged)) == before

    entry = _mark_undone(folder, staged, entry)

    def fake_touch(project_folder, spec):
        _redo_touch_in_place(tmp_path, folder, staged, entry, spec)
        return {}

    receipt = uj.redo_touch(
        str(folder), entry["id"],
        read_live_rows=lambda _final: reel_read.rows_of(
            {"tracks": reel_read.read_tracks(staged)}),
        touch=fake_touch)

    assert receipt["redo"] is True
    assert uj.projection(reel_read.read_tracks(staged)) == after_touch
    history = reel_versions.versions_of(str(folder), FINAL)
    assert any(version["kind"] == reel_versions.KIND_REDO
               for version in history)


# ── Grades ────────────────────────────────────────────────────────


def _graded_detail(cdl):
    return {"row": "V1", "track_type": "video", "track_index": 1,
            "record_in": 590, "duration": 100, "left_offset": 3151,
            "source_file": "/lab/LC4930.MXF", "name": "LC4930.MXF",
            "transform": {}, "fusion": {}, "clip_color": "",
            "enabled": True, "color": {"cdl": cdl}}


GRADE_BEFORE = {"Slope": "1.0000 1.0000 1.0000",
                "Offset": "0.0000 0.0000 0.0000",
                "Power": "1.0000 1.0000 1.0000",
                "Saturation": "1.0000"}
GRADE_AFTER = {"Slope": "1.2000 1.2000 1.2000",
               "Offset": "0.0000 0.0000 0.0000",
               "Power": "1.0000 1.0000 1.0000",
               "Saturation": "1.2000"}


def test_an_in_place_grade_is_written_back(tmp_path):
    """A grade the item already carried is put back, and read back.

    The projection COMPARES the grade, so the undo can see a grade
    change; before this, it saw the change and did not write the grade
    back, so the undo of a graded item ended in a failure with the
    grade still on it.
    """
    from tests.composed_edit_harness import build_reel

    timeline, _pool, _media = build_reel(tmp_path)
    item = timeline.rows["V1"][0]
    item.SetCDL(dict(GRADE_AFTER))

    receipt = uj._revert_in_place(
        item, _graded_detail(GRADE_AFTER), _graded_detail(GRADE_BEFORE))

    assert receipt["grade"] is True
    assert dict(item.GetCDL()) == GRADE_BEFORE


def test_a_grade_the_journal_holds_no_record_of_refuses_before_any_write(
        tmp_path, monkeypatch):
    """A grade the touch PUT ON cannot be taken off, so the undo says so.

    No script can remove a CDL: `SetCDL` writes one. So the undo refuses
    by name, BEFORE the first write, rather than leave the grade on the
    item and call the restore done.
    """
    spec = {"reel": 1, "edits": [{"op": "set_properties", "row": "V3",
                                  "item": 0,
                                  "properties": {"ZoomX": 1.25}}]}
    folder, _approved, staged, entry, by_path = _touch(
        tmp_path, spec, monkeypatch=monkeypatch)
    entry = uj.read_entry(str(folder), entry["id"])
    # The touch graded the item: the LIVE item carries the grade, and
    # the journal's after-read records it.
    staged.rows["V3"][0].SetCDL(dict(GRADE_AFTER))
    for track in entry["after"]["tracks"]:
        for clip in track.get("clips") or ():
            if str(clip.get("source_file") or "").endswith("tv_frame.mov"):
                clip["color"] = {"cdl": dict(GRADE_AFTER)}
    uj.write_entry(str(folder), entry)

    with pytest.raises(uj.UndoRefused) as refused:
        _undo(tmp_path, folder, staged, entry, by_path)

    message = str(refused.value)
    assert "grade" in message
    assert "Nothing was changed" in message

"""Undo: a touch reversed in place reads EXACTLY as before it; a rebuild rolls back.

Each test names the defect it catches (docs/TEST_AUTHORING.md):

- an undo that leaves the timeline other than it was before the touch -
  a moved card back one frame off, a removed card back without its
  transform or comp, a property left at the touched value;
- an undo that runs over a timeline the captain changed after the touch,
  destroying that change;
- a rebuild that overwrote the version before it, so there was nothing
  to roll back to.

Driven against the fake Resolve the composed-edit tests use
(`tests/composed_edit_harness.py`); every project is under `tmp_path`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402
from library.tools.versions import reel_versions  # noqa: E402
from tests.composed_edit_harness import (  # noqa: E402
    FakeComp,
    FakeTool,
    build_reel,
    covering_window,
    duplicate,
    frames_of,
    media_pool,
    pool_clip,
)
from tests.composed_edit_harness import item as make_item  # noqa: E402
from tests.resolve_double import FakeResolve  # noqa: E402

FINAL = "Reel 01 - lab"


def _mock_touchup_patch(monkeypatch):
    """Apply touchup EditPatch operations against the Resolve double."""
    from library.tools import edit_patch

    def apply_live_patch(*, project, timeline, operations, **_kwargs):
        assert project is timeline._project
        for operation in operations:
            matches = [item for row in reel_read.live_items(timeline)
                       for item in row["items"]
                       if item.GetUniqueId() == operation["unique_id"]]
            assert len(matches) == 1
            item = matches[0]
            if operation["op"] == "clip.delete":
                timeline.DeleteClips([item], False)
                if any(item.GetUniqueId() == operation["unique_id"]
                       for row in reel_read.live_items(timeline)
                       for item in row["items"]):
                    return {"status": "refused",
                            "reason": "clip remained after local delete"}
            elif operation["op"] == "clip.set_property":
                item.SetProperty(operation["key"], operation["value"])
                if item.GetProperty(operation["key"]) != operation["value"]:
                    return {"status": "refused",
                            "reason": "property read-back differed"}
            else:
                raise AssertionError(operation)
        return {"status": "committed", "generation": 1}

    monkeypatch.setattr(edit_patch, "apply_live_patch", apply_live_patch)


def _touch(tmp_path, spec, *, monkeypatch, prepare=None):
    """Journal, then touch a staging copy exactly as `apply_touchup` does."""
    approved, _pool, media = build_reel(tmp_path)
    if prepare:
        prepare(approved, media)
    folder = tmp_path / "project"
    folder.mkdir()
    qualification = tu.qualify(reel_read.read_tracks(approved), spec)
    entry = uj.open_entry(
        str(folder), final=FINAL, reel=1, resolve_project="lab",
        spec=spec, gate_class=qualification.gate_class,
        source_timeline=approved, removals=qualification.removals)

    staged = duplicate(approved, name=FINAL)
    pool = media_pool(staged)
    _mock_touchup_patch(monkeypatch)
    in_place = tu._apply_in_place(
        staged._project, staged, qualification, str(tmp_path / "touch"),
        "test-journal")
    del in_place
    tu._pre_delete_removed(staged._project, staged, qualification.removals,
                           "test-journal")
    changes = tu._rekey_changes(reel_read.read_tracks(staged),
                                qualification)
    if changes:
        from library.tools import composed_edit as ce
        still_placer = None
        rederiver = tu._NullRederiver("test")
        if qualification.gate_class == tu.COMPOSED_STILL_RESIZE:
            from library.tools.still_placement import place_still_exact
            resolve = FakeResolve(staged._project)
            rederiver = tu._StillResizeRederiver()

            def still_placer(capture):
                change = capture.change
                return place_still_exact(
                    resolve, staged._project, pool, staged,
                    capture.media_pool_item, change.duration,
                    change.record_frame, change.track_index, 25,
                    timeline_name=staged.GetName())

        ce.apply_composed_edit(
            timeline=staged, media_pool=pool, changes=changes,
            comp_dir=str(tmp_path / "c"), withheld_dir=str(tmp_path / "w"),
            rederiver=rederiver, still_placer=still_placer,
            write_context={
                "project": "lab", "project_folder": str(folder),
                "timeline_name": staged.GetName(),
                "timeline_id": staged.GetUniqueId(),
                "run_id": "undo-journal-test",
            })
    uj.close_entry(str(folder), entry, after_timeline=staged,
                   rows=reel_read.rows_of(
                       {"tracks": reel_read.read_tracks(staged)}))
    by_path = {m.GetClipProperty("File Path"): m for m in media.values()}
    return folder, approved, staged, entry, by_path


def _undo(tmp_path, folder, staged, entry, by_path):
    entry = uj.read_entry(str(folder), entry["id"])
    from library.tools.transform_write_log import write_scope

    still_resize = entry["gate_class"] == tu.COMPOSED_STILL_RESIZE
    still_placer = None
    rederiver = tu._NullRederiver("test")
    if still_resize:
        from library.tools.still_placement import place_still_exact
        resolve = FakeResolve(staged._project)
        rederiver = tu._StillResizeRederiver()

        def still_placer(capture):
            change = capture.change
            return place_still_exact(
                resolve, staged._project, media_pool(staged), staged,
                capture.media_pool_item, change.duration,
                change.record_frame, change.track_index, 25,
                timeline_name=staged.GetName())
    with write_scope(project="lab", project_folder=str(folder),
                     timeline_name=staged.GetName(),
                     timeline_id=staged.GetUniqueId(),
                     run_id="undo-journal-test"):
        return uj.undo_in_place(
            timeline=staged, media_pool=media_pool(staged), entry=entry,
            entry_root=uj.entry_dir(str(folder), entry["id"]),
            reference=duplicate(staged, name="reference"),
            rederiver=rederiver,
            resolve_media=by_path.get, work_dir=str(tmp_path / "undo"),
            project_folder=str(folder), still_resize=still_resize,
            still_placer=still_placer)


def _native_still(approved, _media):
    still_source = pool_clip("/lab/overlay.png", frames=240,
                             name="overlay.png")
    still_source.SetClipProperty("Type", "Still")
    row = approved.add_track("video", "Motion Graphics")
    assert row == 5
    approved.add_item("video", row,
                      make_item(still_source, 1400, 60, 0))


def _card_with_a_look(approved, media):
    """V4[1] carries a transform and a comp of its own - both must return."""
    card = approved.rows["V4"][1]
    card.properties = {**card.properties, "ZoomX": 1.4, "Pan": -12.0}
    card.comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn",
                             covering_window(40, 0, frames_of(media["card"]))),
        "Transform1": FakeTool("Transform", {"Size": 1.0})})]


@pytest.mark.parametrize("spec, prepare", [
    ({"reel": 1, "edits": [{"op": "move", "row": "V4", "item": 0,
                            "to_row": "V4", "to_record": 1300}]}, None),
    ({"reel": 1, "edits": [{"op": "remove_overlay", "row": "V4",
                            "item": 1}]}, _card_with_a_look),
    ({"reel": 1, "edits": [{"op": "set_properties", "row": "V3",
                            "item": 0,
                            "properties": {"ZoomX": 1.25}}]}, None),
    ({"reel": 1, "edits": [{"op": "resize_still", "row": "V5",
                            "item": 0, "duration": 90}]}, _native_still),
], ids=["move", "remove-with-its-look", "set-properties",
        "resize-still"])
def test_undo_restores_exactly_the_pre_touch_timeline(tmp_path, spec,
                                                      prepare, monkeypatch):
    folder, approved, staged, entry, by_path = _touch(
        tmp_path, spec, prepare=prepare, monkeypatch=monkeypatch)
    before = uj.projection(reel_read.read_tracks(approved))
    assert uj.projection(reel_read.read_tracks(staged)) != before

    receipt = _undo(tmp_path, folder, staged, entry, by_path)

    assert receipt["verified"] is True
    assert uj.projection(reel_read.read_tracks(staged)) == before


def test_undo_refuses_a_timeline_changed_since_the_touch(tmp_path,
                                                         monkeypatch):
    spec = {"reel": 1, "edits": [{"op": "move", "row": "V4", "item": 0,
                                  "to_row": "V4", "to_record": 1300}]}
    folder, _approved, staged, entry, by_path = _touch(
        tmp_path, spec, monkeypatch=monkeypatch)
    # The captain nudges a card by hand after the touch.
    staged.rows["V4"][-1].properties["Pan"] = 40.0
    deletes = list(staged.delete_calls)

    with pytest.raises(uj.TimelineMovedSinceTouch) as refused:
        _undo(tmp_path, folder, staged, entry, by_path)

    assert "V4" in str(refused.value)
    assert staged.delete_calls == deletes
    assert staged.rows["V4"][-1].properties["Pan"] == 40.0


def test_undo_refuses_to_replay_a_caption_with_unreadable_source_trim(
        tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = reel_read.read_tracks(timeline)
    captions = next(track for track in tracks
                    if track["type"] == "video" and track["index"] == 4)
    captions["clips"][0]["left_offset"] = None

    with pytest.raises(uj.UndoRefused,
                       match="undo comparison cannot preserve V4 item's source trim"):
        uj.plan_inverse(tracks, tracks)


def _write_plan(folder, reel_one_end):
    path = folder / "pipeline_output" / "review" / "reel_proposals_v2.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    moments = [
        {"number": 1, "slug": "lab", "timeline_start": 0.0,
         "timeline_end": reel_one_end, "approval": "approved"},
        {"number": 2, "slug": "other", "timeline_start": 30.0,
         "timeline_end": 45.0, "approval": "approved"},
    ]
    path.write_text(json.dumps({"format": "reel_proposal/1",
                                "moments": moments}), encoding="utf-8")
    return path


def test_a_rebuild_rolls_back_to_the_version_it_replaced(tmp_path):
    from library.tools.reel_build import _record_reel_versions

    folder = tmp_path / "project"
    rows_v1 = {"video:V1": {"items": [{"name": "a", "start": 0,
                                        "end": 300, "duration": 300}]}}
    rows_v2 = {"video:V1": {"items": [{"name": "a", "start": 0,
                                        "end": 600, "duration": 600}]}}
    plan = _write_plan(folder, 10.0)
    _record_reel_versions(str(folder), {FINAL: rows_v1}, round_number=1)
    _write_plan(folder, 20.0)
    # A second rebuild inside the same round: the defect was that it
    # overwrote the first.
    _record_reel_versions(str(folder), {FINAL: rows_v2}, round_number=1)
    assert [v["kind"] for v in reel_versions.versions_of(
        str(folder), FINAL)] == ["build", "build"]

    live = {"rows": rows_v2}
    other_before = json.loads(plan.read_text())["moments"][1]

    def build(project_folder, reel):
        moments = json.loads(plan.read_text())["moments"]
        assert reel == 1
        assert moments[0]["timeline_end"] == 10.0
        assert moments[1] == other_before
        live["rows"] = rows_v1
        _record_reel_versions(project_folder, {FINAL: rows_v1})
        return 0

    receipt = uj.undo(str(folder), final=FINAL,
                      read_live_rows=lambda _final: live["rows"],
                      build=build)

    assert receipt == [{"final": FINAL, "rolled_back": 2, "to": 1,
                        "replayed": []}]
    history = reel_versions.versions_of(str(folder), FINAL)
    assert history[1]["undone_by"] == 3
    assert history[2]["kind"] == "rollback"


# ── Semantic undo and redo ─────────────────────────────────────────
#
# Each test names the defect it catches:
#
# - an undo that addresses a middle act over a later act's work would
#   destroy that work - the moved-since check must hold for ANY
#   addressed act, not just the newest;
# - an undo that cannot reach a non-newest act even when the timeline
#   still reads as that act left it - the address must be semantic
#   (by act id), not LIFO;
# - a mistaken undo that costs a full re-execution because there is
#   no redo - a reversed act must be re-appliable from its journal
#   (touch) or its plan moment (rebuild).


def _touch_again(tmp_path, folder, staged, spec):
    """Apply a second touch on top of an already-touched timeline."""
    qualification = tu.qualify(reel_read.read_tracks(staged), spec)
    entry = uj.open_entry(
        str(folder), final=FINAL, reel=1, resolve_project="lab",
        spec=spec, gate_class=qualification.gate_class,
        source_timeline=staged, removals=qualification.removals)
    staged2 = duplicate(staged, name=FINAL)
    tu._apply_in_place(staged2._project, staged2, qualification,
                       str(tmp_path / "touch-2"), entry["id"])
    tu._pre_delete_removed(staged2._project, staged2, qualification.removals,
                           entry["id"])
    changes = tu._rekey_changes(reel_read.read_tracks(staged2), qualification)
    if changes:
        from library.tools import composed_edit as ce
        ce.apply_composed_edit(
            timeline=staged2, media_pool=media_pool(staged2), changes=changes,
            comp_dir=str(tmp_path / "c2"), withheld_dir=str(tmp_path / "w2"),
            rederiver=tu._NullRederiver("test"),
            write_context={
                "project": "lab", "project_folder": str(folder),
                "timeline_name": staged2.GetName(),
                "timeline_id": staged2.GetUniqueId(),
                "run_id": "undo-journal-test",
            })
    uj.close_entry(str(folder), entry, after_timeline=staged2,
                   rows=reel_read.rows_of(
                       {"tracks": reel_read.read_tracks(staged2)}))
    return staged2, entry


def _mark_undone(folder, staged, entry):
    """Record the undo version and mark the entry undone, as `undo_touch` would."""
    rows = reel_read.rows_of({"tracks": reel_read.read_tracks(staged)})
    version = reel_versions.record(str(folder), entry["final"],
                                   kind=reel_versions.KIND_UNDO, rows=rows,
                                   undoes=entry["version"], journal=entry["id"])
    reel_versions.mark_undone(str(folder), entry["final"],
                              int(entry["version"]), int(version["version"]))
    entry = uj.read_entry(str(folder), entry["id"])
    entry["status"] = uj.STATUS_UNDONE
    entry["undo"] = {"version": version["version"]}
    uj.write_entry(str(folder), entry)
    return entry


def _redo_touch_in_place(tmp_path, folder, staged, entry, spec):
    """Re-apply the undone touch in place on the live timeline.

    The fake `touch` seam for `redo_touch`: qualifies the journal's spec
    against the live (pre-touch) read and applies it, the same path
    `_touch` drives on a staging copy.
    """
    from library.tools.transform_write_log import write_scope
    qualification = tu.qualify(reel_read.read_tracks(staged), spec)
    with write_scope(project="lab", project_folder=str(folder),
                     timeline_name=staged.GetName(),
                     timeline_id=staged.GetUniqueId(),
                     run_id="undo-journal-test"):
        tu._apply_in_place(staged._project, staged, qualification,
                           str(tmp_path / "redo-c"), entry["id"],
                           project_folder=str(folder))
        tu._pre_delete_removed(staged._project, staged,
                               qualification.removals, entry["id"])
        changes = tu._rekey_changes(reel_read.read_tracks(staged),
                                    qualification)
        if changes:
            from library.tools import composed_edit as ce
            ce.apply_composed_edit(
                timeline=staged, media_pool=media_pool(staged),
                changes=changes,
                comp_dir=str(tmp_path / "redo-c"),
                withheld_dir=str(tmp_path / "redo-w"),
                rederiver=tu._NullRederiver("test"),
                write_context={
                    "project": "lab", "project_folder": str(folder),
                    "timeline_name": staged.GetName(),
                    "timeline_id": staged.GetUniqueId(),
                    "run_id": "undo-journal-test",
                })


def test_undo_a_middle_act_refuses_when_a_later_act_changed_the_timeline(
        tmp_path):
    from library.tools.reel_build import _record_reel_versions

    folder = tmp_path / "project"
    rows_v1 = {"video:V1": {"items": [{"name": "a", "start": 0,
                                        "end": 300, "duration": 300}]}}
    rows_v2 = {"video:V1": {"items": [{"name": "a", "start": 0,
                                        "end": 600, "duration": 600}]}}
    _write_plan(folder, 10.0)
    _record_reel_versions(str(folder), {FINAL: rows_v1}, round_number=1)
    _write_plan(folder, 20.0)
    _record_reel_versions(str(folder), {FINAL: rows_v2}, round_number=1)

    live = {"rows": rows_v2}
    with pytest.raises(uj.TimelineMovedSinceTouch):
        uj.undo(str(folder), final=FINAL, act="1",
                read_live_rows=lambda _final: live["rows"],
                build=lambda pf, reel: 0)


def test_undo_a_non_newest_touch_by_id_when_the_timeline_still_reads_as_its_after(
        tmp_path, monkeypatch):
    spec_a = {"reel": 1, "edits": [{"op": "move", "row": "V4", "item": 0,
                                   "to_row": "V4", "to_record": 1300}]}
    folder, approved, staged, entry_a, by_path = _touch(
        tmp_path, spec_a, monkeypatch=monkeypatch)
    spec_b = {"reel": 1, "edits": [{"op": "set_properties", "row": "V3",
                                   "item": 0,
                                   "properties": {"ZoomX": 1.25}}]}
    staged_b, entry_b = _touch_again(tmp_path, folder, staged, spec_b)
    before = uj.projection(reel_read.read_tracks(approved))

    _undo(tmp_path, folder, staged_b, entry_b, by_path)
    entry_a = uj.read_entry(str(folder), entry_a["id"])
    receipt = uj.undo_in_place(
        timeline=staged_b, media_pool=media_pool(staged_b), entry=entry_a,
        entry_root=uj.entry_dir(str(folder), entry_a["id"]),
        reference=duplicate(staged_b, name="ref-a"),
        rederiver=tu._NullRederiver("test"),
        resolve_media=by_path.get, work_dir=str(tmp_path / "undo-a"),
        project_folder=str(folder))
    assert receipt["verified"] is True
    assert uj.projection(reel_read.read_tracks(staged_b)) == before


def test_undo_then_redo_restores_the_touch(tmp_path, monkeypatch):
    spec = {"reel": 1, "edits": [{"op": "move", "row": "V4", "item": 0,
                                  "to_row": "V4", "to_record": 1300}]}
    folder, approved, staged, entry, by_path = _touch(
        tmp_path, spec, monkeypatch=monkeypatch)
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


def test_undo_then_redo_restores_the_rebuild(tmp_path):
    from library.tools.reel_build import _record_reel_versions

    folder = tmp_path / "project"
    rows_v1 = {"video:V1": {"items": [{"name": "a", "start": 0,
                                        "end": 300, "duration": 300}]}}
    rows_v2 = {"video:V1": {"items": [{"name": "a", "start": 0,
                                        "end": 600, "duration": 600}]}}
    plan = _write_plan(folder, 10.0)
    _record_reel_versions(str(folder), {FINAL: rows_v1}, round_number=1)
    _write_plan(folder, 20.0)
    _record_reel_versions(str(folder), {FINAL: rows_v2}, round_number=1)

    live = {"rows": rows_v2}

    def build(project_folder, reel):
        moments = json.loads(plan.read_text())["moments"]
        assert reel == 1
        assert moments[0]["timeline_end"] == 10.0
        live["rows"] = rows_v1
        _record_reel_versions(project_folder, {FINAL: rows_v1})
        return 0

    receipt = uj.undo(str(folder), final=FINAL,
                      read_live_rows=lambda _final: live["rows"],
                      build=build)
    assert receipt == [{"final": FINAL, "rolled_back": 2, "to": 1,
                        "replayed": []}]

    def redo_build(project_folder, reel):
        moments = json.loads(plan.read_text())["moments"]
        assert reel == 1
        assert moments[0]["timeline_end"] == 20.0
        live["rows"] = rows_v2
        _record_reel_versions(project_folder, {FINAL: rows_v2})
        return 0

    redo_receipt = uj.redo(str(folder), final=FINAL, act="2",
                           read_live_rows=lambda _final: live["rows"],
                           build=redo_build)
    assert redo_receipt[0]["redone"] == 2
    history = reel_versions.versions_of(str(folder), FINAL)
    redo_versions = [v for v in history if v["kind"] == "redo"]
    assert len(redo_versions) == 1
    assert redo_versions[0]["redoes"] == 2
    assert history[1].get("undone_by") is None
    assert history[1].get("redone_by") == redo_versions[0]["version"]


def test_resolve_act_addresses_each_act_form(tmp_path):
    from library.tools import reel_edit_history
    from library.tools.reel_build import _record_reel_versions

    folder = tmp_path / "project"
    folder.mkdir()
    review = folder / "pipeline_output" / "review"
    review.mkdir(parents=True)
    rows = {"video:V1": {"items": [{"name": "a", "start": 0,
                                     "end": 300, "duration": 300}]}}
    _record_reel_versions(str(folder), {FINAL: rows}, round_number=1)

    kind, act, entry, name = uj.resolve_act(str(folder), FINAL, "1")
    assert kind == "rebuild"
    assert int(act["version"]) == 1
    assert entry is None
    assert name == FINAL

    history_entry = reel_edit_history.record_entry(
        str(review), FINAL, actor="ren", act="build", summary="test",
        refs={"version": 1})
    kind, act, entry, name = uj.resolve_act(str(folder), FINAL,
                                            history_entry["id"])
    assert kind == "rebuild"
    assert int(act["version"]) == 1
    assert name == FINAL

    with pytest.raises(uj.UndoRefused):
        uj.resolve_act(str(folder), FINAL, "no-such-act")

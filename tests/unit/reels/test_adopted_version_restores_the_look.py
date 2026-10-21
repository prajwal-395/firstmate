"""An adopted version restores the LOOK, not only the rows.

The punchlist item VC-004: *"A user can modify a reel manually, let
Ren make an additional change, inspect the result, reject it, and
restore the exact accepted generation - including its grade and
relevant effects - without losing the human edit."*

Two defects, both found by reading what the release gate actually
compares:

1. A RESTORE was checked against ROWS - names, record spans, enabled
   state - and rows carry no look. A `.drt` import that dropped the
   captain's grade, his Fusion comps or his composite settings still
   read as the recorded version, so the ledger claimed the exact
   generation came back while the picture did not.

2. Nothing RECORDED the look. An adopted version captured the rows and
   an opaque `.drt`; `resolve_archive.read_archive` reads structure out
   of that file and no grade, so there was nothing to check a restore
   against even if the restore had looked.

So the adoption records the grade, the Fusion comps and the composite
settings of the generation it adopts, and the restore is checked
against that record - or it says it could not be checked.
"""

from __future__ import annotations

import json

import pytest

from library.tools import reel_adoption, reel_replace_guard, undo_journal
from library.tools.versions import reel_versions, rounds, store

REEL = "Reel 26 - write-for-the-question-your-customer-ask"


# ── the fixtures: one generation, with a look ────────────────────────

def _clip(*, grade=None, comps=0, name="LCATL0013.MXF", start=0,
          end=220, track=1, track_type="video"):
    """One item, with the state a snapshot reads."""
    return {
        "name": name,
        "enabled": True,
        "track_type": track_type,
        "track_index": track,
        "track_name": "V1",
        "record_in": start,
        "record_out": end,
        "duration": end - start,
        "left_offset": 0,
        "source_file": "/media/LCATL0013.MXF",
        "media_pool_item_id": "pool-1",
        "source_in_frame": 0,
        "source_out_frame": end - start,
        "transform": {"Zoom": 1.0},
        "clip_color": "",
        "color": {"cdl": dict(grade or {}), "color_group": "",
                  "node_count": 2 if grade else 1, "luts": []},
        "fusion": {"comp_count": comps, "comp_names": [], "mattes": None,
                   "media_windows": []},
    }


def _tracks(clips):
    return [{"type": "video", "index": 1, "name": "V1", "clips": clips}]


class _Timeline:
    def __init__(self, name=REEL, unique_id="the-live-one"):
        self._name = name
        self._id = unique_id

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._id


class _Project:
    def GetName(self):
        return "Ren Scratch - vc004"


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    return project


def _adopt(tmp_path, monkeypatch, clips, *, reason="logo timing corrected"):
    """Adopt a live generation, with the archive export faked."""
    project = _project(tmp_path)
    archive = tmp_path / "adopted.drt"
    archive.write_bytes(b"a recovery archive")
    monkeypatch.setattr(
        store, "export_timeline_recovery_archive",
        lambda *args, **kwargs: {"archived": True, "path": str(archive)})
    monkeypatch.setattr(
        reel_adoption, "_timeline_by_identity",
        lambda *args, **kwargs: _Timeline())
    monkeypatch.setattr(
        "library.tools.resolve_lock.assert_current_timeline",
        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda *args, **kwargs: _tracks(clips))
    monkeypatch.setattr(
        "library.tools.resolve_archive.read_archive",
        lambda path: {"project": "Ren Scratch - vc004",
                      "timelines": [{"id": "t1", "tracks": [
                          {"type": "video", "name": "V1", "items": [
                              {"media_path": clip["source_file"]}
                              for clip in clips]}]}]})
    entry = reel_adoption._adopt(
        str(project), _Project(), object(), REEL,
        reason=reason, by="captain", timeline_id="the-live-one")
    return project, entry


# ── the adoption records the look, and who made it ──────────────────

def test_the_adoption_records_the_grade_and_the_effects(tmp_path,
                                                        monkeypatch):
    """The adopted version holds what the look is, not only where it is."""
    grade = {"Lift": {"R": 0.1, "G": 0.1, "B": 0.1}}
    project, entry = _adopt(tmp_path, monkeypatch,
                            [_clip(grade=grade, comps=1)])

    recorded = entry["recovery_archive"]["preservation"]
    assert len(recorded) == 1
    assert recorded[0]["color"]["cdl"] == grade
    assert recorded[0]["fusion"]["comp_count"] == 1
    assert recorded[0]["record_in"] == 0
    assert recorded[0]["record_out"] == 220


def test_the_adoption_attributes_the_change_to_the_human(tmp_path,
                                                         monkeypatch):
    """A hand edit is the editor's state, not an act Ren performed."""
    project, entry = _adopt(tmp_path, monkeypatch, [_clip()])

    assert entry["kind"] == reel_versions.KIND_ADOPT
    assert entry["by"] == "captain"
    revisions = json.loads(
        (project / "pipeline_output" / "review" / "creative_revisions.json")
        .read_text(encoding="utf-8"))
    revision = revisions["revisions"][entry["revision_id"]]
    assert revision["author"] == "captain"
    assert revision["operations"] == [{"kind": "external_hand_edit"}]


# ── the restore is checked against that record ───────────────────────

def _act():
    """The act being rejected: Ren's own build, one version on."""
    return {"version": 23, "kind": reel_versions.KIND_BUILD, "rows": {},
            "rows_digest": rounds.digest_rows({}),
            "plan_moment": {"number": 26, "id": "moment-27"}}


def _version(project, *, preservation=None):
    """The ledger the release gate runs on: a build, the ADOPTED hand
    edit, and the act Ren made on top of it."""
    entries = [
        {"version": 21, "kind": reel_versions.KIND_BUILD, "rows": {},
         "rows_digest": rounds.digest_rows({}),
         "plan_moment": {"number": 26, "id": "moment-26"}},
        {"version": 22, "kind": reel_versions.KIND_ADOPT, "rows": {},
         "rows_digest": rounds.digest_rows({}),
         "recovery_archive": {
             "timeline_id": "the-live-one",
             "archive_path": "/a/reel26.drt",
             "content_id": "sha256:" + "0" * 64,
             "archive_sha256": "0" * 64,
             "inventory_items": 1, "assets": [],
             **({"preservation": preservation} if preservation else {})}},
        {"version": 23, "kind": reel_versions.KIND_BUILD, "rows": {},
         "rows_digest": rounds.digest_rows({}),
         "plan_moment": {"number": 26, "id": "moment-27"},
         "recovery_archive": {
             "timeline_id": "the-newer-one",
             "archive_path": "/a/reel26-newer.drt",
             "content_id": "sha256:" + "1" * 64,
             "archive_sha256": "1" * 64,
             "inventory_items": 1, "assets": []}},
    ]
    path = project / "pipeline_output" / "review" / "reel_versions.json"
    path.write_text(json.dumps(
        {"format": "reel_versions/1", "reels": {REEL: entries}}),
        encoding="utf-8")
    return entries[1]


def _recorded_look(**overrides):
    clip = _clip(**overrides)
    return reel_replace_guard.preservation_record(
        reel_replace_guard.snapshot_items(_tracks([clip])))


def test_a_restore_that_lost_the_grade_is_refused_and_not_recorded(
        tmp_path):
    """The defect: the rows came back and the colour did not."""
    project = _project(tmp_path)
    grade = {"Lift": {"R": 0.1, "G": 0.1, "B": 0.1}}
    _version(project, preservation=_recorded_look(grade=grade))

    with pytest.raises(undo_journal.UndoNotVerified) as refused:
        undo_journal.rollback_rebuild(
            str(project), REEL, _act(),
            read_live_rows=lambda final: {},
            restore_archive=lambda *args, **kwargs: {},
            read_live_preservation=lambda *args, **kwargs: _recorded_look())

    assert "color" in str(refused.value)
    assert "does not carry the grade" in str(refused.value)
    entries = reel_versions.versions_of(str(project), REEL)
    assert reel_versions.KIND_ROLLBACK not in [
        entry["kind"] for entry in entries]


def test_a_restore_that_kept_the_grade_is_recorded_as_verified(tmp_path):
    """The gate bites only when it can fail, and says so when it passes."""
    project = _project(tmp_path)
    grade = {"Lift": {"R": 0.1, "G": 0.1, "B": 0.1}}
    _version(project, preservation=_recorded_look(grade=grade))

    result = undo_journal.rollback_rebuild(
        str(project), REEL, _act(),
        read_live_rows=lambda final: {},
        restore_archive=lambda *args, **kwargs: {},
        read_live_preservation=lambda *args, **kwargs: _recorded_look(
            grade=grade))

    assert result["restored_from_archive"] is True
    assert result["preservation_verified"] is True
    entries = reel_versions.versions_of(str(project), REEL)
    assert entries[-1]["kind"] == reel_versions.KIND_ROLLBACK
    assert entries[-1]["preservation_verified"] is True


def test_a_version_with_no_recorded_look_reports_the_restore_as_unverified(
        tmp_path):
    """A version recorded before looks were kept cannot be checked.

    It is not a pass: the report says the grade and effects were not
    verified, rather than the ledger claiming the exact generation came
    back.
    """
    project = _project(tmp_path)
    _version(project)

    result = undo_journal.rollback_rebuild(
        str(project), REEL, _act(),
        read_live_rows=lambda final: {},
        restore_archive=lambda *args, **kwargs: {})

    assert result["preservation_verified"] is False
    assert "records no grade or effects state" in result["preservation_note"]


# ── the comparison itself ────────────────────────────────────────────

def test_the_comparison_sees_the_grade_and_the_effects():
    """Two generations with the same rows and a different look differ."""
    grade = {"Lift": {"R": 0.1, "G": 0.1, "B": 0.1}}
    recorded = _recorded_look(grade=grade, comps=1)
    ungraded = _recorded_look()

    losses = reel_replace_guard.preservation_losses(recorded, ungraded)

    assert any(loss["field"] == "color" for loss in losses)
    assert any(loss["field"] == "fusion" for loss in losses)


def test_the_comparison_passes_a_generation_that_kept_its_look():
    """The same look is not a loss."""
    grade = {"Lift": {"R": 0.1, "G": 0.1, "B": 0.1}}
    recorded = _recorded_look(grade=grade, comps=1)

    assert reel_replace_guard.preservation_losses(
        recorded, _recorded_look(grade=grade, comps=1)) == []


# ── Ren's work resumes from the adopted generation ──────────────────

def _adopted_ledger(project, receipt):
    """The ledger as a promotion finds it: a build, then the ADOPTION."""
    path = project / "pipeline_output" / "review" / "reel_versions.json"
    path.write_text(json.dumps({
        "format": "reel_versions/1",
        "reels": {REEL: [
            {"version": 21, "kind": reel_versions.KIND_BUILD, "rows": {},
             "rows_digest": rounds.digest_rows({}),
             "plan_moment": {"number": 26, "id": "moment-26"}},
            {"version": 22, "kind": reel_versions.KIND_ADOPT, "rows": {},
             "rows_digest": rounds.digest_rows({}),
             "recovery_archive": receipt},
        ]}}), encoding="utf-8")


def test_a_promotion_over_an_adopted_generation_keeps_the_adopted_archive(
        tmp_path, monkeypatch):
    """A generation that is already archived keeps its archive.

    The adoption writes a verified receipt for the live generation, and
    the promotion that follows retires that SAME generation: a second
    export would replace an immutable record with another copy of the
    same timeline, and the version already holds the receipt that
    restores it. (`library/tools/versions/replaced_generation.py` is
    the one archiving route; it keeps a receipt that still verifies.)
    """
    from library.tools.versions import replaced_generation

    project = _project(tmp_path)
    archive = tmp_path / "adopted.drt"
    archive.write_bytes(b"a recovery archive")
    receipt = {
        "timeline_id": "the-live-one",
        "archive_path": str(archive),
        "content_id": store.recovery_archive_content_id(archive),
        "archive_sha256": store._sha256(archive),
        "rows_digest": rounds.digest_rows({"a generation": {}}),
        "assets": [],
    }
    entry = {"version": 22, "kind": reel_versions.KIND_ADOPT, "rows": {},
             "rows_digest": receipt["rows_digest"],
             "recovery_archive": receipt}

    def _export(*args, **kwargs):
        raise AssertionError("the generation was exported a second time")

    monkeypatch.setattr(store, "export_timeline_recovery_archive", _export)

    outcome = replaced_generation.ensure_archived(
        resolve=object(), project=_Project(), timeline=_Timeline(),
        project_folder=str(project), entry=entry)

    assert outcome["status"] == "verified"
    assert outcome["receipt"]["archive_path"] == str(archive)


def test_a_promotion_over_a_moved_generation_still_archives_it(
        tmp_path, monkeypatch):
    """A generation that MOVED since it was archived is archived again."""
    from library.tools import reel_build

    project = _project(tmp_path)
    archive = tmp_path / "adopted.drt"
    archive.write_bytes(b"a recovery archive")
    rows = {"video:V1": {"media_type": "video", "index": 1, "name": "V1",
                         "items": [], "count": 0, "frames": 0}}
    receipt = {
        "timeline_id": "the-live-one",
        "archive_path": str(archive),
        "content_id": store.recovery_archive_content_id(archive),
        "archive_sha256": store._sha256(archive),
        "rows_digest": rounds.digest_rows({"a moved generation": {}}),
        "assets": [],
    }
    _adopted_ledger(project, receipt)

    monkeypatch.setattr(
        reel_replace_guard, "full_timeline_snapshot",
        lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(
        reel_replace_guard, "snapshot_timeline",
        lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        store, "export_timeline_recovery_archive",
        lambda *args, **kwargs: {"archived": False,
                                  "reason": "not reached in this test"})

    with pytest.raises(reel_build.PromotionFailure):
        reel_build._archive_retiring_generation(
            object(), _Project(), _Timeline(), str(project))


# ── the archive does not carry the grade, so the restore re-applies it ──

def test_the_restore_re_applies_the_grade_the_project_declared(tmp_path):
    """The measurement: a recovery archive carries the effects, not the grade.

    2026-10-21, over the 117 `.drt` recovery archives on this machine:
    77 carry the Fusion composition and NONE carries a CDL, a LUT or a
    node graph. So a restore that only imports the archive restores the
    cut and the effects and leaves the picture ungraded - and the
    project's DECLARED grade is re-applied to put it back.
    """
    project = _project(tmp_path)
    drx = project / "grade.drx"
    drx.write_bytes(b"a power grade")
    (project / "project.yaml").write_text(
        "color:\n"
        "  power_grade_drx:\n"
        f"    path: {drx}\n"
        "    provenance:\n"
        "      source: the captain's own grade\n"
        "      authorised_by: the captain\n"
        "      licence: the captain's own file\n",
        encoding="utf-8")

    class _Graph:
        def __init__(self, item):
            self._item = item

        def ApplyGradeFromDRX(self, path, mode):
            self._item.applied.append((path, mode))
            return True

        def GetNumNodes(self):
            return 8

    class _Item:
        def __init__(self, path):
            self.applied = []
            self._path = path

        def GetMediaPoolItem(self):
            return self

        def GetClipProperty(self, name):
            return self._path if name == "File Path" else ""

        def GetNodeGraph(self):
            return _Graph(self)

    class _Timeline:
        def __init__(self, path):
            self.items = [_Item(path)]

        def GetTrackCount(self, track_type="video"):
            return 1 if track_type == "video" else 0

        def GetItemListInTrack(self, track_type, index):
            return list(self.items) if track_type == "video" else []

    raw = project / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    timeline = _Timeline(str(raw / "clip.mxf"))
    record = undo_journal._reapply_declared_grade(str(project), timeline)

    assert record["route"] == "power_grade_drx"
    assert record["applied"], record
    assert timeline.items[0].applied == [(str(drx), 0)]


def test_a_project_that_declares_no_grade_re_applies_nothing(tmp_path):
    """Absent is absent: no declaration, no re-application, said plainly."""
    project = _project(tmp_path)

    record = undo_journal._reapply_declared_grade(str(project), object())

    assert record["route"] == "none"
    assert record["reason"] == "the project declares no grade"


# ── the NAMED-version restore checks the look too ───────────────────

def test_restoring_a_named_version_checks_the_look(tmp_path):
    """`ren undo --version N` restores a named generation; the look is part
    of the generation, not only its rows."""
    project = _project(tmp_path)
    grade = {"Lift": {"R": 0.1, "G": 0.1, "B": 0.1}}
    _version(project, preservation=_recorded_look(grade=grade))

    with pytest.raises(undo_journal.UndoNotVerified):
        undo_journal.restore_version(
            str(project), REEL, 22,
            read_live_rows=lambda final: {},
            restore_archive=lambda *args, **kwargs: {},
            read_live_preservation=lambda *args, **kwargs: _recorded_look())


def test_a_rendered_card_is_not_graded_on_a_restore(tmp_path):
    """The build's own rule: a card is a graphic, not footage.

    Grading one would tint it - the caption rows and the TV frame share
    the picture rows with the footage - so the restore grades the
    footage and leaves the cards alone.
    """
    project = _project(tmp_path)
    drx = project / "grade.drx"
    drx.write_bytes(b"a power grade")
    (project / "project.yaml").write_text(
        "color:\n"
        "  power_grade_drx:\n"
        f"    path: {drx}\n"
        "    provenance:\n"
        "      source: the captain's own grade\n"
        "      authorised_by: the captain\n"
        "      licence: the captain's own file\n",
        encoding="utf-8")
    raw = project / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    class _Graph:
        def __init__(self, item):
            self._item = item

        def ApplyGradeFromDRX(self, path, mode):
            self._item.applied.append(path)
            return True

        def GetNumNodes(self):
            return 8

    class _Item:
        def __init__(self, path):
            self.applied = []
            self._path = path

        def GetMediaPoolItem(self):
            return self

        def GetClipProperty(self, name):
            return self._path if name == "File Path" else ""

        def GetNodeGraph(self):
            return _Graph(self)

    class _Timeline:
        def __init__(self, paths):
            self.items = [_Item(path) for path in paths]

        def GetTrackCount(self, track_type="video"):
            return 1 if track_type == "video" else 0

        def GetItemListInTrack(self, track_type, index):
            return list(self.items) if track_type == "video" else []

    timeline = _Timeline([str(raw / "clip.mxf"),
                          str(project / "exports" / "caption.mov")])
    record = undo_journal._reapply_declared_grade(str(project), timeline)

    assert len(record["applied"]) == 1
    assert timeline.items[0].applied == [str(drx)]
    assert timeline.items[1].applied == []


def test_every_video_row_is_reached_on_a_restore(tmp_path):
    """Resolve counts the rows PER MEDIA TYPE, not once for the timeline.

    The defect the live run caught: `GetTrackCount()` without a type
    answers nothing, so a restore that walked the rows that way graded
    nothing and said it had.
    """
    project = _project(tmp_path)
    drx = project / "grade.drx"
    drx.write_bytes(b"a power grade")
    (project / "project.yaml").write_text(
        "color:\n"
        "  power_grade_drx:\n"
        f"    path: {drx}\n"
        "    provenance:\n"
        "      source: the captain's own grade\n"
        "      authorised_by: the captain\n"
        "      licence: the captain's own file\n",
        encoding="utf-8")
    raw = project / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    class _Graph:
        def __init__(self, item):
            self._item = item

        def ApplyGradeFromDRX(self, path, mode):
            self._item.applied.append(path)
            return True

        def GetNumNodes(self):
            return 8

    class _Item:
        def __init__(self, path):
            self.applied = []
            self._path = path

        def GetMediaPoolItem(self):
            return self

        def GetClipProperty(self, name):
            return self._path if name == "File Path" else ""

        def GetNodeGraph(self):
            return _Graph(self)

    class _Timeline:
        def __init__(self, rows):
            self.rows = rows

        def GetTrackCount(self, track_type="video"):
            return len(self.rows) if track_type == "video" else 0

        def GetItemListInTrack(self, track_type, index):
            if track_type != "video":
                return []
            return list(self.rows[index - 1])

    timeline = _Timeline([[_Item(str(raw / "one.mxf"))],
                          [_Item(str(raw / "two.mxf"))]])
    record = undo_journal._reapply_declared_grade(str(project), timeline)

    assert len(record["applied"]) == 2


def test_each_adoption_keeps_its_own_archive(tmp_path, monkeypatch):
    """The defect the live run caught: a second adoption replaced the
    first one's archive, and the first version's receipt went on naming a
    file whose content had changed under it."""
    project = _project(tmp_path)
    exports = []

    def _export(resolve, timeline, timeline_name, project_folder, **kwargs):
        path = tmp_path / f"adopted-{len(exports)}.drt"
        path.write_bytes(f"archive {len(exports)}".encode())
        exports.append((kwargs.get("archive_suffix"), str(path)))
        return {"archived": True, "path": str(path)}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", _export)
    monkeypatch.setattr(
        reel_adoption, "_timeline_by_identity",
        lambda *args, **kwargs: _Timeline())
    monkeypatch.setattr(
        "library.tools.resolve_lock.assert_current_timeline",
        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda *args, **kwargs: _tracks([_clip()]))
    monkeypatch.setattr(
        "library.tools.resolve_archive.read_archive",
        lambda path: {"project": "Ren Scratch - vc004",
                      "timelines": [{"id": "t1", "tracks": [
                          {"type": "video", "name": "V1", "items": [
                              {"media_path": "/media/LCATL0013.MXF"}]}]}]})

    first = reel_adoption._adopt(str(project), _Project(), object(), REEL,
                                 reason="one", by="captain",
                                 timeline_id="the-live-one")
    second = reel_adoption._adopt(str(project), _Project(), object(), REEL,
                                  reason="two", by="captain",
                                  timeline_id="the-live-one")

    assert exports[0][0] != exports[1][0]
    assert (first["recovery_archive"]["archive_path"]
            != second["recovery_archive"]["archive_path"])
    assert (first["recovery_archive"]["archive_sha256"]
            != second["recovery_archive"]["archive_sha256"])


def test_the_retiring_archive_may_name_the_timeline_it_holds(tmp_path,
                                                              monkeypatch):
    """The defect the live run caught: a `.drt` names the TIMELINE it
    holds, not the Resolve project it came from.

    The promotion's check compared that name with the RESOLVE PROJECT's
    name, so it refused every promotion - two names that are not the
    same kind of name. Measured 2026-10-21 on a live promotion: the
    archive of `Ren Scratch - vc004 - Empty` reads back a project
    called `Ren Scratch - vc004 - Empty`, and the check demanded
    `Ren Scratch - vc004`.
    """
    from library.tools import reel_build

    project = _project(tmp_path)
    archive = tmp_path / "retired.drt"
    archive.write_bytes(b"an archive")
    rows = {"video:V1": {"media_type": "video", "index": 1, "name": "V1",
                         "items": [], "count": 0, "frames": 0}}
    receipt = {
        "timeline_id": "the-live-one",
        "archive_path": str(archive),
        "content_id": store.recovery_archive_content_id(archive),
        "archive_sha256": store._sha256(archive),
        # A receipt of an EARLIER generation: the export runs again, and
        # the check that names the archive is reached.
        "rows_digest": rounds.digest_rows({"an earlier generation": {}}),
        "assets": [],
    }
    _adopted_ledger(project, receipt)
    path = project / "pipeline_output" / "review" / "reel_versions.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["reels"][REEL][1]["rows_digest"] = rounds.digest_rows(rows)
    path.write_text(json.dumps(document), encoding="utf-8")

    class _Timeline:
        def GetName(self):
            return "Ren Scratch - vc004 - Empty"

        def GetUniqueId(self):
            return "the-live-one"

    monkeypatch.setattr(
        reel_replace_guard, "full_timeline_snapshot",
        lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(
        reel_replace_guard, "snapshot_timeline",
        lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        store, "export_timeline_recovery_archive",
        lambda *args, **kwargs: {"archived": True, "path": str(archive)})
    monkeypatch.setattr(
        "library.tools.resolve_archive.read_archive",
        lambda path: {"project": "Ren Scratch - vc004 - Empty",
                      "timelines": [{"id": "t1", "tracks": []}]})

    kept = reel_build._archive_retiring_generation(
        object(), _Project(), _Timeline(), str(project))

    assert kept["timeline_id"] == "the-live-one"


def test_the_adoption_counts_the_items_its_archive_holds(tmp_path,
                                                         monkeypatch):
    """The defect the live run caught: the receipt carried no item count.

    The restore reads the archive back and compares its inventory with
    the receipt's, so a receipt with no count fails the check for ever
    and every restore of an adopted version refused with "failed
    inventory verification".
    """
    project, entry = _adopt(tmp_path, monkeypatch, [_clip()])

    receipt = entry["recovery_archive"]
    assert receipt["inventory_items"] == 1


def test_an_archive_of_another_generation_is_not_adopted(tmp_path,
                                                         monkeypatch):
    """The archive must BE the generation that is being adopted."""
    project = _project(tmp_path)
    archive = tmp_path / "adopted.drt"
    archive.write_bytes(b"a recovery archive")
    monkeypatch.setattr(
        store, "export_timeline_recovery_archive",
        lambda *args, **kwargs: {"archived": True, "path": str(archive)})
    monkeypatch.setattr(
        reel_adoption, "_timeline_by_identity",
        lambda *args, **kwargs: _Timeline())
    monkeypatch.setattr(
        "library.tools.resolve_lock.assert_current_timeline",
        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda *args, **kwargs: _tracks([_clip()]))
    monkeypatch.setattr(
        "library.tools.resolve_archive.read_archive",
        lambda path: {"project": "Ren Scratch - vc004",
                      "timelines": [{"id": "t1", "tracks": [
                          {"type": "video", "name": "V1", "items": [
                              {"media_path": "/media/somebody-else.mxf"}]}]}]})

    with pytest.raises(reel_versions.ReelVersionsUnreadable) as refused:
        reel_adoption._adopt(str(project), _Project(), object(), REEL,
                             reason="x", by="captain",
                             timeline_id="the-live-one")

    assert "does not hold the media" in str(refused.value)
    assert reel_versions.versions_of(str(project), REEL) == []

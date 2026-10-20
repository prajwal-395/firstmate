"""Cross-ledger regressions for creative revision recovery.

These stay offline: project records are written under pytest's temporary
directory, and the fake timeline/evidence is the only input.
"""

from __future__ import annotations

import json

import pytest

from library.tools import (
    creative_revision,
    edit_patch,
    timeline_diff,
    timeline_shadow,
    undo_journal,
)
from library.tools.resolve_lock import assume_sole_writer
from library.tools.versions import reel_versions
from tests.integration.fault_injection import FaultInjector
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)

FINAL = "Reel 26 - write-for-the-question-your-customer-ask"


def _tracks(name: str, start: int = 0):
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": name, "record_in": start, "record_out": start + 100,
         "duration": 100, "source_in_frame": 0,
         "source_out_frame": 100, "left_offset": 0, "right_offset": 0},
    ]}]


def _change(name: str, start: int):
    return timeline_diff.diff_timelines(_tracks(name), _tracks(name, start))


def test_qa01_reel26_same_plan_rebuild_restores_the_incremental_version(
        tmp_path):
    """Defect: same-plan rollback rebuilds the wrong state after a touch."""
    project = tmp_path / "reel26-project"
    project.mkdir()
    first_rows = {"video:V1": [{"name": "camera.mov", "start": 0,
                                "end": 100}]}
    touched_rows = {"video:V1": [{"name": "camera.mov", "start": 0,
                                  "end": 108}]}
    rebuilt_rows = {"video:V1": [{"name": "camera.mov", "start": 0,
                                  "end": 100}]}
    plan_moment = {"number": 26, "start": 0, "end": 100,
                   "title": "approved declaration"}

    reel_versions.record(project, FINAL, kind=reel_versions.KIND_BUILD,
                         rows=first_rows, plan_moment=plan_moment)
    touched = reel_versions.record(
        project, FINAL, kind=reel_versions.KIND_TOUCH, rows=touched_rows,
        journal="incremental-logo-adjustment.json")
    archive = {"timeline_id": "reel26-touch-generation",
               "rows_digest": "fixture-digest"}
    reel_versions.attach_recovery_archive(
        project, FINAL, touched["version"], archive)
    rebuild = reel_versions.record(
        project, FINAL, kind=reel_versions.KIND_BUILD,
        rows=rebuilt_rows, plan_moment=plan_moment)
    live = {"rows": rebuilt_rows}
    restored = []

    def restore_archive(project_folder, final, target, receipt):
        assert project_folder == str(project)
        assert final == FINAL
        assert target["version"] == touched["version"]
        assert receipt == archive
        live["rows"] = touched_rows
        restored.append(receipt["timeline_id"])
        return {"restored": True}

    outcome = undo_journal.rollback_rebuild(
        str(project), FINAL, rebuild,
        read_live_rows=lambda _final: live["rows"],
        restore_archive=restore_archive)

    assert outcome["restored_from_archive"] is True
    assert restored == ["reel26-touch-generation"]
    assert live["rows"] == touched_rows
    assert reel_versions.versions_of(project, FINAL)[-1]["recovery_archive"] == archive


def test_qa01_interrupted_promotion_keeps_original_candidate_and_backup(
        tmp_path):
    """Defect: a later build reuses debris and orphans an approved generation."""
    from library.tools import reel_build

    project = make_project("Interrupted Promotion QA")
    approved = project.adopt(FakeTimeline("QA Final"))
    candidate = project.adopt(FakeTimeline("QA Final (rebuild staging)"))
    backup = project.adopt(FakeTimeline("QA Final (pre-rebuild backup)"))

    with pytest.raises(reel_build.ReelBuildError, match="interrupted run"):
        reel_build.promote_staged_reels(
            str(tmp_path), "Interrupted Promotion QA", "QA Master",
            {"QA Final": "QA Final (rebuild staging)"},
            resolve=FakeResolve(project))

    assert [timeline.GetName() for timeline in
            (approved, candidate, backup)] == [
                "QA Final", "QA Final (rebuild staging)",
                "QA Final (pre-rebuild backup)"]


def test_qa01_independent_branches_merge_and_conflicting_branches_refuse(
        tmp_path):
    """Defect: a merge drops an independent edit or overwrites a conflict."""
    project = tmp_path / "branch-project"
    project.mkdir()
    root = undo_journal.record_commit(
        str(project), parent=None, intent="approved root", changes=[],
        agent="qa-fixture", timeline=FINAL)
    left = undo_journal.record_commit(
        str(project), parent=root["id"], intent="move clip A",
        changes=_change("clip-a.mov", 10), agent="qa-fixture",
        timeline=FINAL)
    right = undo_journal.record_commit(
        str(project), parent=root["id"], intent="move clip B",
        changes=_change("clip-b.mov", 20), agent="qa-fixture",
        timeline=FINAL)
    merged = undo_journal.merge_commits(
        str(project), timeline=FINAL, left=left["id"], right=right["id"])

    assert merged["parent"] == left["id"]
    assert merged["second_parent"] == right["id"]
    assert {change["item"] for change in merged["changes"]} == {
        "clip-a.mov", "clip-b.mov"}

    conflict = undo_journal.record_commit(
        str(project), parent=root["id"], intent="move clip A differently",
        changes=_change("clip-a.mov", 30), agent="qa-fixture",
        timeline=FINAL)
    with pytest.raises(undo_journal.MergeConflict, match="clip-a.mov"):
        undo_journal.merge_commits(
            str(project), timeline=FINAL, left=left["id"],
            right=conflict["id"])


def test_qa01_edit_version_branch_merge_promote_undo_redo_lifecycle(tmp_path):
    """Defect: lifecycle ledgers can disagree across a complete edit cycle."""
    project = tmp_path / "lifecycle-project"
    project.mkdir()
    original_rows = {"video:V1": [{"name": "camera.mov", "start": 0,
                                   "end": 100}]}
    merged_rows = {"video:V1": [
        {"name": "camera.mov", "start": 10, "end": 110},
        {"name": "logo.png", "start": 20, "end": 40},
    ]}
    root = undo_journal.record_commit(
        str(project), parent=None, intent="approved root", changes=[],
        agent="qa-fixture", timeline=FINAL)
    root_revision = creative_revision.get(project, root["revision_id"])
    assert root_revision["status"] == "recorded"

    left_branch = undo_journal.create_creative_branch(
        str(project), name="variant/move-camera",
        base_revision=root_revision["revision_id"],
        semantic_changes=[{"op": "move", "item": "camera.mov"}],
        git_branch="variant/move-camera")
    right_branch = undo_journal.create_creative_branch(
        str(project), name="variant/add-logo",
        base_revision=root_revision["revision_id"],
        semantic_changes=[{"op": "insert", "item": "logo.png"}],
        git_branch="variant/add-logo")
    left = undo_journal.record_commit(
        str(project), parent=root["id"], intent="move camera",
        changes=_change("camera.mov", 10), agent="qa-fixture",
        timeline=FINAL)
    right = undo_journal.record_commit(
        str(project), parent=root["id"], intent="add logo",
        changes=_change("logo.png", 20), agent="qa-fixture",
        timeline=FINAL)
    assert left_branch["base_revision"] == right_branch["base_revision"]
    merged = undo_journal.merge_commits(
        str(project), timeline=FINAL, left=left["id"], right=right["id"])
    merged_revision = creative_revision.get(project, merged["revision_id"])
    assert merged_revision["parents"] == [left["revision_id"],
                                            right["revision_id"]]

    from library.tools.versions import reel_versions

    baseline = reel_versions.record(
        project, FINAL, kind=reel_versions.KIND_BUILD, rows=original_rows,
        plan_moment={"number": 1, "title": "baseline"})
    promoted = reel_versions.record(
        project, FINAL, kind=reel_versions.KIND_BUILD, rows=merged_rows,
        plan_moment={"number": 2, "title": "merged edit"},
        revision_id=merged_revision["revision_id"],
        promotion_transaction_id="qa-promotion-1",
        verification={"status": "passed"}, approval={"status": "approved"})
    from library.tools.reel_proposal import proposal_path

    proposal = proposal_path(project)
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps({"moments": [
        {"number": 1, "title": "baseline"},
        {"number": 2, "title": "merged edit"},
    ]}), encoding="utf-8")

    live = {"rows": merged_rows}

    def rebuild(_project_folder, reel_number):
        if reel_number == 1:
            live["rows"] = original_rows
            assert reel_versions.take_pending_rollback(
                project, FINAL) == promoted["version"]
            rollback = reel_versions.record(
                project, FINAL, kind=reel_versions.KIND_ROLLBACK,
                rows=original_rows, undoes=promoted["version"])
            reel_versions.mark_undone(
                project, FINAL, promoted["version"], rollback["version"])
        elif reel_number == 2:
            live["rows"] = merged_rows
            assert reel_versions.take_pending_redo(
                project, FINAL) == promoted["version"]
            redo = reel_versions.record(
                project, FINAL, kind=reel_versions.KIND_REDO,
                rows=merged_rows, redoes=promoted["version"])
            reel_versions.mark_redone(
                project, FINAL, promoted["version"], redo["version"])
        else:
            raise AssertionError(f"unexpected plan moment {reel_number}")
        return 0

    undo = undo_journal.rollback_rebuild(
        str(project), FINAL, promoted,
        read_live_rows=lambda _final: live["rows"], build=rebuild)
    assert undo["to"] == baseline["version"]
    assert live["rows"] == original_rows
    redo = undo_journal.redo_rebuild(
        str(project), FINAL, promoted,
        read_live_rows=lambda _final: live["rows"], build=rebuild)
    assert redo["redone"] == promoted["version"]
    assert live["rows"] == merged_rows
    history = reel_versions.versions_of(project, FINAL)
    assert [row["kind"] for row in history] == [
        "build", "build", "rollback", "redo"]
    assert history[1]["redone_by"] == history[3]["version"]


def test_qa01_reel13_planned_edit_is_read_back_on_the_materialized_timeline(
        tmp_path):
    """Defect: a planned change is recorded but never reaches Resolve."""
    project = make_project("Reel 13 QA")
    timeline = project.adopt(FakeTimeline("Reel 13 - approved", project=project))
    item = place_clip(timeline, make_pool_clip("speaker.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = timeline_shadow.ShadowStore(tmp_path / "reel13-shadow.sqlite3")

    with assume_sole_writer("Reel 13 regression Resolve double"):
        base = timeline_shadow.observe(project, timeline, store)
        patch = {
            "id": "reel13-planned-transform",
            "project": "Reel 13 QA",
            "timeline": "Reel 13 - approved",
            "base_generation": base.generation,
            "capability": "reel.touchup",
            "affected_spans": [[0, 48]],
            "conflict_domains": ["picture_transform"],
            "operations": [{
                "op": "clip.set_property", "unique_id": item.GetUniqueId(),
                "key": "Pan", "value": 40,
            }],
        }
        receipt = edit_patch.apply_patch(
            patch, resolve=FakeResolve(project), project=project,
            timeline=timeline, store=store, project_folder=str(tmp_path))

    assert receipt["status"] == "committed"
    assert item.GetProperty("Pan") == 40
    assert store.head("Reel 13 QA", base.timeline_id).generation == receipt["generation"]


def test_qa01_reel26_incremental_and_complete_materialization_match_placement(
        tmp_path):
    """Defect: incremental placement diverges from complete materialization."""
    project = make_project("Reel 26 placement QA")
    incremental = project.adopt(FakeTimeline(
        "Reel 26 incremental", project=project, start_frame=1032))
    complete = project.adopt(FakeTimeline(
        "Reel 26 complete", project=project, start_frame=1032))
    incremental_item = place_clip(
        incremental, make_pool_clip("logo.mov", frames=500), 1032, 1103)
    complete_item = place_clip(
        complete, make_pool_clip("logo.mov", frames=500), 1032, 1103)
    project.SetCurrentTimeline(incremental)
    store = timeline_shadow.ShadowStore(tmp_path / "reel26-shadow.sqlite3")

    with assume_sole_writer("Reel 26 regression Resolve double"):
        base = timeline_shadow.observe(project, incremental, store)
        patch = {
            "id": "reel26-logo-placement",
            "project": "Reel 26 placement QA",
            "timeline": "Reel 26 incremental",
            "base_generation": base.generation,
            "capability": "reel.touchup",
            "affected_spans": [[1032, 1104]],
            "conflict_domains": ["picture_transform"],
            "operations": [{
                "op": "clip.set_property",
                "unique_id": incremental_item.GetUniqueId(),
                "key": "Pan", "value": 40,
            }],
        }
        receipt = edit_patch.apply_patch(
            patch, resolve=FakeResolve(project), project=project,
            timeline=incremental, store=store,
            project_folder=str(tmp_path))

    # The complete build is represented by the expected materialized
    # state. Compare source window, track placement and transform, which
    # the Reel 26 readback measured at the 72-frame logo tail.
    complete_item.SetProperty("Pan", 40)
    assert receipt["status"] == "committed"
    assert (incremental_item.GetStart(), incremental_item.GetEnd(),
            incremental_item.GetSourceStartFrame(),
            incremental_item.GetSourceEndFrame(),
            incremental_item.GetProperty("Pan")) == (
        complete_item.GetStart(), complete_item.GetEnd(),
        complete_item.GetSourceStartFrame(), complete_item.GetSourceEndFrame(),
        complete_item.GetProperty("Pan"))


def test_qa02_readback_failure_is_recorded_as_recovery_required(
        tmp_path, monkeypatch):
    """Defect: Resolve mutates, then a failed read-back leaves no receipt."""
    project = make_project("Read-back failure QA")
    timeline = project.adopt(FakeTimeline("Read-back Reel", project=project))
    place_clip(timeline, make_pool_clip("shot.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = timeline_shadow.ShadowStore(tmp_path / "readback-shadow.sqlite3")
    injector = FaultInjector("read_back", occurrence=2)

    with assume_sole_writer("Read-back fault injection Resolve double"):
        base = timeline_shadow.observe(project, timeline, store)
        injector.wrap(monkeypatch, timeline_shadow, "read_live")
        patch = {
            "id": "readback-failure",
            "project": "Read-back failure QA",
            "timeline": "Read-back Reel",
            "base_generation": base.generation,
            "capability": "reel.touchup",
            "affected_spans": [[0, 48]],
            "conflict_domains": ["markers"],
            "operations": [{
                "op": "marker.add", "frame": 8,
                "color": "Blue", "name": "write before failed read",
            }],
        }

        with pytest.raises(edit_patch.PatchRefused):
            edit_patch.apply_patch(
                patch, resolve=FakeResolve(project), project=project,
                timeline=timeline, store=store,
                project_folder=str(tmp_path))

    assert injector.triggered
    assert 8 in timeline.GetMarkers()
    assert store.head("Read-back failure QA", base.timeline_id).generation > base.generation

"""A refused or failed touch cleans up its own staging.

The defect this names (captain, 2026-10-06): `ren touch` duplicated
Reel 10 into 'Reel 10 ... (rebuild staging)' and made it the current
timeline; when the replace guard refused, that staging was left in the
project as the current timeline - 34 timelines instead of 33 - until a
worker exported and deleted it by hand.  A refused touch now exports its
own staging to the `recovery_archives` `.drt` the build already uses,
deletes it, and restores the current timeline its own lease moved.

Driven against the fake Resolve; nothing reaches a real project.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import master_touchup  # noqa: E402
from library.tools import reel_build  # noqa: E402
from library.tools import reel_touchup  # noqa: E402
from library.tools.reel_replace_guard import (  # noqa: E402
    ReplaceGuardRefused,
)
from tests.composed_edit_harness import build_reel  # noqa: E402
from tests.resolve_double import FakeTimeline  # noqa: E402

MASTER = "Main Edit"
REEL_NAME = "Reel 01 - topic"


@contextmanager
def _pass_lease(purpose, **kwargs):
    yield


class _Fence:
    def __init__(self):
        self.drift_seen = []


@contextmanager
def _moving_fence(project, timeline, purpose):
    """Establish the cursor on `timeline`, as the real fence does."""
    project.SetCurrentTimeline(timeline)
    yield _Fence()


@contextmanager
def _pass_excursion(project, timeline, purpose):
    previous = project.GetCurrentTimeline()
    project.SetCurrentTimeline(timeline)
    try:
        yield
    finally:
        project.SetCurrentTimeline(previous)


def _mock_edit_patch(monkeypatch):
    from library.tools import edit_patch, reel_read

    def apply_live_patch(*, project, timeline, operations, **_kwargs):
        for operation in operations:
            matches = [item for row in reel_read.live_items(timeline)
                       for item in row["items"]
                       if item.GetUniqueId() == operation["unique_id"]]
            assert len(matches) == 1
            item = matches[0]
            if operation["op"] == "clip.delete":
                timeline.DeleteClips([item])
            elif operation["op"] == "clip.set_property":
                item.SetProperty(operation["key"], operation["value"])
            else:
                raise AssertionError(operation)
        return {"status": "committed", "generation": 1}

    monkeypatch.setattr(edit_patch, "apply_live_patch", apply_live_patch)


def _mock_shadow(monkeypatch):
    from library.tools import timeline_shadow

    class _Head:
        def summary(self):
            return {"generation": 1}

    monkeypatch.setattr(timeline_shadow, "observe", lambda *a, **k: _Head())


class _FakeResolve:
    EXPORT_DRT = 18


def _project_with_master(tmp_path, monkeypatch):
    """A fake project: the master timeline, and a second timeline current.

    The second timeline is current so the test can tell a cursor the
    touch restored from one the touch moved - the defect left the
    staging itself current.
    """
    from library.tools import resolve_lock

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.SetName(MASTER)
    project = timeline._project
    other = FakeTimeline("Reel 05 - something", project)
    project.adopt(other)
    project.SetCurrentTimeline(other)

    monkeypatch.setattr(resolve_lock, "under_lease", _pass_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", _moving_fence)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", _pass_excursion)
    _mock_edit_patch(monkeypatch)
    _mock_shadow(monkeypatch)
    monkeypatch.setattr(reel_build, "_connect_resolve",
                        lambda: _FakeResolve())

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "project.yaml").write_text(
        "resolve:\n"
        f"  project_name: {project.GetName()}\n"
        f"  timeline_name: {MASTER}\n",
        encoding="utf-8")
    return project, folder, other


def test_a_refused_touch_leaves_the_project_as_it_found_it(tmp_path,
                                                           monkeypatch):
    """The replace guard refuses, and the touch cleans up after itself.

    The staging is exported to the recovery archive, deleted, and the
    cursor put back on the timeline the touch found - so the timeline
    count and the current timeline are exactly as they were before the
    touch started.
    """
    project, folder, other = _project_with_master(tmp_path, monkeypatch)
    staging = f"{MASTER} (rebuild staging)"
    before_count = project.GetTimelineCount()
    assert project.GetCurrentTimeline() is other

    spec = {"edits": [{"op": "remove_overlay", "row": "V3", "item": 0}]}
    with pytest.raises(ReplaceGuardRefused):
        master_touchup.apply_master_touchup(
            str(folder), spec, connect=lambda _name: project)

    assert project.GetTimelineCount() == before_count
    assert project.GetCurrentTimeline() is other
    assert staging not in {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)}

    archive_dir = (folder / "pipeline_output" / "review"
                   / "recovery_archives")
    archives = list(archive_dir.glob("*.drt"))
    assert len(archives) == 1
    assert archives[0].name.startswith("Main_Edit")
    assert archives[0].stat().st_size > 0


def test_a_failed_touch_leaves_no_staging_and_no_archive(tmp_path,
                                                          monkeypatch):
    """A mid-flight failure before staging leaves nothing behind.

    The staging copy does not land: the touch fails before any edit, so
    there is no staging to export or delete and no archive is written.
    The approved timeline stands under its own name.
    """
    project, folder, other = _project_with_master(tmp_path, monkeypatch)
    staging = f"{MASTER} (rebuild staging)"
    before_count = project.GetTimelineCount()

    def _no_landing_duplicate(self, name):
        return None

    original = FakeTimeline.DuplicateTimeline
    FakeTimeline.DuplicateTimeline = _no_landing_duplicate
    try:
        spec = {"edits": [{"op": "remove_overlay", "row": "V3",
                           "item": 0}]}
        with pytest.raises(master_touchup.MasterTouchupError):
            master_touchup.apply_master_touchup(
                str(folder), spec, connect=lambda _name: project)
    finally:
        FakeTimeline.DuplicateTimeline = original

    assert project.GetTimelineCount() == before_count
    assert staging not in {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)}
    archive_dir = (folder / "pipeline_output" / "review"
                   / "recovery_archives")
    assert not list(archive_dir.glob("*.drt"))


def _project_with_reel(tmp_path, monkeypatch):
    """A fake project holding one approved reel, another timeline current.

    The proposal on disk is what `resolve_final_name` reads to map reel
    1 to its timeline name; there is no sign-off, so the guard's
    declaration check passes.
    """
    from library.tools import resolve_lock

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.SetName(REEL_NAME)
    project = timeline._project
    other = FakeTimeline("Reel 02 - another", project)
    project.adopt(other)
    project.SetCurrentTimeline(other)

    monkeypatch.setattr(resolve_lock, "under_lease", _pass_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", _moving_fence)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", _pass_excursion)
    _mock_edit_patch(monkeypatch)
    _mock_shadow(monkeypatch)
    monkeypatch.setattr(reel_build, "_connect_resolve",
                        lambda: _FakeResolve())

    folder = tmp_path / "project"
    folder.mkdir()
    review = folder / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text(
        '{"format": "reel_proposal/1", "moments": ['
        '{"number": 1, "slug": "topic", "reason": "test", '
        '"timeline_start": 0.0, "timeline_end": 30.0}]}',
        encoding="utf-8")
    (folder / "project.yaml").write_text(
        "resolve:\n"
        f"  project_name: {project.GetName()}\n",
        encoding="utf-8")
    return project, folder, other


def test_a_refused_reel_touch_leaves_the_project_as_it_found_it(
        tmp_path, monkeypatch):
    """The captain's exact defect: a refused `ren touch` on a reel.

    The replace guard refuses, and the touch exports its staging to the
    recovery archive, deletes it, and puts the cursor back - the
    timeline count and the current timeline exactly as they were before
    the touch started, with no staging left as the current timeline.
    """
    project, folder, other = _project_with_reel(tmp_path, monkeypatch)
    staging = f"{REEL_NAME} (rebuild staging)"
    before_count = project.GetTimelineCount()
    assert project.GetCurrentTimeline() is other

    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V3", "item": 0}]}
    with pytest.raises(ReplaceGuardRefused):
        reel_touchup.apply_touchup(
            str(folder), spec, connect=lambda _name: project)

    assert project.GetTimelineCount() == before_count
    assert project.GetCurrentTimeline() is other
    assert staging not in {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)}

    archive_dir = (folder / "pipeline_output" / "review"
                   / "recovery_archives")
    archives = list(archive_dir.glob("*.drt"))
    assert len(archives) == 1
    assert archives[0].name.startswith("Reel_01_-_topic")
    assert archives[0].stat().st_size > 0


# ── The recovery-archive export the cleanup calls ─────────────────────
#
# Best effort is the contract: the caller deletes the staging whatever
# happens, so an export that fails is reported on the result, never
# raised over the refusal it cleans up.


def _bare_folder(tmp_path):
    """A project folder with the layout ProjectLayout reads, no mocks."""
    folder = tmp_path / "project"
    (folder / "pipeline_output" / "review").mkdir(parents=True)
    (folder / "pipeline_output" / "scratch").mkdir(parents=True)
    return folder


def test_the_recovery_archive_export_is_best_effort(tmp_path):
    """A failing Export is reported, never raised over the refusal."""
    from library.tools.versions import store

    folder = _bare_folder(tmp_path)
    timeline = FakeTimeline("Reel 01 - topic (rebuild staging)")

    def _failing_export(path, kind):
        raise RuntimeError("Resolve would not export")

    timeline.Export = _failing_export
    result = store.export_timeline_recovery_archive(
        _FakeResolve(), timeline, timeline.GetName(), str(folder))
    assert result["archived"] is False
    assert "Resolve would not export" in result["reason"]
    archive_dir = (folder / "pipeline_output" / "review"
                   / "recovery_archives")
    assert not list(archive_dir.glob("*.drt"))


def test_the_recovery_archive_export_needs_the_drt_flag(tmp_path):
    """No EXPORT_DRT on the Resolve app is a reason, not a raise."""
    from library.tools.versions import store

    folder = _bare_folder(tmp_path)
    timeline = FakeTimeline("Reel 01 - topic (rebuild staging)")
    result = store.export_timeline_recovery_archive(
        object(), timeline, timeline.GetName(), str(folder))
    assert result["archived"] is False
    assert "EXPORT_DRT" in result["reason"]


def test_the_recovery_archive_export_writes_the_builds_destination(tmp_path):
    """The same destination and atomic replace the build's export uses."""
    from library.tools.versions import store

    folder = _bare_folder(tmp_path)
    timeline = FakeTimeline("Reel 01 - topic (rebuild staging)")
    result = store.export_timeline_recovery_archive(
        _FakeResolve(), timeline, timeline.GetName(), str(folder))
    assert result["archived"] is True
    relative = Path(result["path"]).relative_to(folder)
    assert relative.parent == Path("pipeline_output") / "review" / \
        "recovery_archives"
    assert relative.suffix == ".drt"
    assert Path(result["path"]).stat().st_size > 0


def test_custom_script_recovery_archives_keep_each_run(tmp_path):
    """Each recorded writing run keeps its own pre-edit DRT backup."""
    from library.tools.versions import store

    folder = _bare_folder(tmp_path)
    timeline = FakeTimeline("Reel 01 - topic")
    first = store.export_timeline_recovery_archive(
        _FakeResolve(), timeline, timeline.GetName(), str(folder),
        archive_suffix="custom-run-1")
    second = store.export_timeline_recovery_archive(
        _FakeResolve(), timeline, timeline.GetName(), str(folder),
        archive_suffix="custom-run-2")

    assert first["archived"] is True
    assert second["archived"] is True
    assert first["path"] != second["path"]
    assert Path(first["path"]).is_file()
    assert Path(second["path"]).is_file()


def test_recovery_archive_import_name_predicts_the_stranded_duplicate():
    """Re-importing a `.drt` names the timeline after the file stem.

    Measured 2026-10-07 on Resolve Studio 21.1: a donor import asking for
    `timelineName="__fm_restore_reel22_20261007"` landed as
    `Reel_22_-_a-score-is-not-a-fix--8654518b4a` instead, and the script's
    name assertion exited before its `DeleteTimelines`, stranding the
    duplicate. The helper predicts that stranded name so callers can find
    and delete by it rather than asserting the requested name alone.
    """
    from library.tools.versions import store

    assert (store.recovery_archive_import_name(
        "Reel 22 - a-score-is-not-a-fix")
        == "Reel_22_-_a-score-is-not-a-fix--8654518b4a")
    assert (store.recovery_archive_import_name("Reel 01 - topic")
            == store._recovery_archive_stem("Reel 01 - topic"))


# ── The two orderings the review pinned ──────────────────────────────
#
# Export first, then delete: a staging whose `.drt` did not land is left
# standing, because the archive is the only copy of the half-finished
# edit once the staging is gone.  And the cleanup is best effort: a
# failure inside it is recorded on the receipt and the original refusal
# still propagates, with the journal and inventory steps still run.


def test_a_refused_touch_whose_export_fails_keeps_its_staging(tmp_path,
                                                             monkeypatch):
    """Export first, then delete: no `.drt`, no delete.

    The resolve app exposes no EXPORT_DRT, so the export cannot land.
    The staging is left standing - the archive is the only copy of the
    half-finished edit once the staging is gone - and the refusal still
    propagates.
    """
    project, folder, _other = _project_with_reel(tmp_path, monkeypatch)
    staging = f"{REEL_NAME} (rebuild staging)"
    before_count = project.GetTimelineCount()
    monkeypatch.setattr(reel_build, "_connect_resolve", lambda: object())

    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V3", "item": 0}]}
    with pytest.raises(ReplaceGuardRefused):
        reel_touchup.apply_touchup(
            str(folder), spec, connect=lambda _name: project)

    assert project.GetTimelineCount() == before_count + 1
    assert staging in {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)}
    archive_dir = (folder / "pipeline_output" / "review"
                   / "recovery_archives")
    assert not list(archive_dir.glob("*.drt"))


def test_a_cleanup_failure_does_not_mask_the_refusal(tmp_path, monkeypatch):
    """A failure inside the cleanup is recorded, not raised over the refusal.

    The original refusal still propagates and the journal entry is still
    failed - the cleanup must never widen into a loss of the refusal's
    own bookkeeping.
    """
    project, folder, _other = _project_with_reel(tmp_path, monkeypatch)
    staging = f"{REEL_NAME} (rebuild staging)"

    from library.tools import undo_journal
    fail_calls = []
    original_fail = undo_journal.fail_entry

    def _recording_fail(*args, **kwargs):
        fail_calls.append(args)
        return original_fail(*args, **kwargs)

    monkeypatch.setattr(undo_journal, "fail_entry", _recording_fail)

    def _raising_cleanup(*args, **kwargs):
        raise RuntimeError("cleanup exploded")

    monkeypatch.setattr(reel_touchup, "_cleanup_refused_staging",
                        _raising_cleanup)

    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V3", "item": 0}]}
    with pytest.raises(ReplaceGuardRefused):
        reel_touchup.apply_touchup(
            str(folder), spec, connect=lambda _name: project)

    assert fail_calls
    assert staging in {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)}


def test_a_master_cleanup_failure_does_not_mask_the_refusal(tmp_path,
                                                            monkeypatch):
    """The master path's handler runs the same best-effort cleanup."""
    project, folder, _other = _project_with_master(tmp_path, monkeypatch)
    staging = f"{MASTER} (rebuild staging)"

    from library.tools import undo_journal
    fail_calls = []
    original_fail = undo_journal.fail_entry

    def _recording_fail(*args, **kwargs):
        fail_calls.append(args)
        return original_fail(*args, **kwargs)

    monkeypatch.setattr(undo_journal, "fail_entry", _recording_fail)

    def _raising_cleanup(*args, **kwargs):
        raise RuntimeError("cleanup exploded")

    monkeypatch.setattr(reel_touchup, "_cleanup_refused_staging",
                        _raising_cleanup)

    spec = {"edits": [{"op": "remove_overlay", "row": "V3", "item": 0}]}
    with pytest.raises(ReplaceGuardRefused):
        master_touchup.apply_master_touchup(
            str(folder), spec, connect=lambda _name: project)

    assert fail_calls
    assert staging in {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)}

"""A partially completed compound edit must be REVERSIBLE, and the
restored state must be VERIFIED before work resumes.

PRES-021 asked for reliable reversal of partially completed compound
edits. Today the rules - reverse order, one verified step at a time,
and never dropping the operations that are still on the timeline - are
stated separately in each path that reverses something, and the
EditPatch path states nothing at all: a patch whose post-write read-back
failed says "recovery required" without saying what could be undone.

PRES-022 asked that the restored state be verified independently before
resuming: by a fresh read compared against the state before the edit,
not by the return value of the code that performed the restore. Nothing
gates a resume on that today.

Every test here names a defect: the failure says what the engine got
wrong, not that a number moved.
"""

from __future__ import annotations

import pytest

from library.tools import edit_patch, edit_recovery
from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


@pytest.fixture
def world(tmp_path):
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
    return {"project": project, "timeline": timeline, "store": store,
            "base": base, "item": item}


def _patch(base_generation, operations, *, patch_id="compound"):
    return {
        "id": patch_id, "project": "Podcast", "timeline": "Reel 09",
        "base_generation": base_generation, "capability": "reel.touchup",
        "affected_spans": [(0, 48)], "conflict_domains": ["markers"],
        "operations": operations,
    }


def _apply(patch, world):
    return edit_patch.apply_patch(
        patch, resolve=FakeResolve(world["project"]),
        project=world["project"], timeline=world["timeline"],
        store=world["store"], project_folder=world["store"].path.parent)


# ── PRES-021: the reversal ────────────────────────────────────────────

def _before_snapshot():
    """The state before an edit: one clip holding one property value."""
    return {
        "tracks": [{"type": "video", "index": 1, "name": "V1", "clips": [
            {"unique_id": "item-1", "name": "a.mov", "record_in": 0,
             "record_out": 48, "duration": 48, "left_offset": 0,
             "source_in_frame": 0, "source_out_frame": 48,
             "track_index": 1, "track_type": "video", "enabled": True,
             "transform": {"ZoomX": 1.0, "Pan": 0.0},
             "fusion": {"comp_count": 0, "media_windows": []},
             "markers": [], "media_pool_item_id": "a.mov",
             "source_file": ""}]}],
        "markers": {"timeline": [], "notes": []},
    }


def test_a_compound_reversal_undoes_the_last_operation_first():
    """The last operation applied is the first one undone: each may
    depend on the one before it."""
    undone = []

    def undo_one(row):
        undone.append(row["operation_index"])
        return {"operation_index": row["operation_index"], "verified": True}

    outcome = edit_recovery.reverse_each(
        rows=[{"operation_index": 2}, {"operation_index": 1},
              {"operation_index": 0}],
        undo_one=undo_one)

    assert undone == [2, 1, 0], (
        "a compound reversal must undo the last operation first, but "
        f"the engine undid {undone}")
    assert outcome["status"] == edit_recovery.RESTORED
    assert outcome["remaining_operations"] == []


def test_a_failed_reversal_names_the_operations_still_on_the_timeline():
    """A reversal that stops half way must say what is STILL there; a
    reversal that dropped the remainder would leave the editor
    believing the timeline was whole."""
    def undo_one(row):
        if row["operation_index"] == 1:
            raise RuntimeError("the undo did not verify")
        return {"operation_index": row["operation_index"], "verified": True}

    outcome = edit_recovery.reverse_each(
        rows=[{"operation_index": 2}, {"operation_index": 1},
              {"operation_index": 0}],
        undo_one=undo_one)

    assert outcome["status"] == edit_recovery.RECOVERY_REQUIRED
    assert outcome["remaining_operations"] == [1, 0], (
        "the operations that were not reversed must be named, but the "
        f"engine named {outcome['remaining_operations']}")
    assert outcome["failed_operation_index"] == 1
    assert "did not verify" in outcome["reason"]
    assert [action["operation_index"] for action in outcome["actions"]] == [2], (
        "the reversal that DID verify must be recorded, so the report "
        "says how far it got")


def test_a_reversal_plan_states_the_inverse_of_each_landed_operation():
    """The inverse is read off the snapshot from BEFORE the write, so a
    marker that was deleted can be put back exactly as it was."""
    before = _before_snapshot()
    plan = edit_recovery.reversal_plan(
        operations=[{"op": "marker.add", "frame": 12, "color": "Blue",
                     "name": "beat"}],
        before=before)

    assert plan["status"] == "reversible"
    assert plan["steps"][0]["inverse"] == [{"op": "marker.delete",
                                            "frame": 12}]


def test_a_reversal_plan_restores_the_previous_value_of_a_property():
    """A property write is undone by writing the value the clip held
    BEFORE the edit, not by a guessed default."""
    before = _before_snapshot()
    unique_id = before["tracks"][0]["clips"][0]["unique_id"]
    plan = edit_recovery.reversal_plan(
        operations=[{"op": "clip.set_property", "unique_id": unique_id,
                     "key": "ZoomX", "value": 1.25}],
        before=before)

    assert plan["status"] == "reversible"
    inverse = plan["steps"][0]["inverse"][0]
    assert inverse["op"] == "clip.set_property"
    assert inverse["key"] == "ZoomX"
    assert inverse["value"] == before["tracks"][0]["clips"][0][
        "transform"]["ZoomX"], (
        "the inverse must restore the value the clip held before the "
        "edit, not a default")


def test_a_reversal_plan_refuses_an_operation_with_no_stated_inverse():
    """An operation whose inverse cannot be stated is REFUSED, never
    guessed: a reversal that restores the wrong thing is worse than a
    reversal that says it cannot be done."""
    before = _before_snapshot()
    plan = edit_recovery.reversal_plan(
        operations=[{"op": "clip.delete", "unique_id": "item-1"}],
        before=before)

    assert plan["status"] == "not_reversible", (
        "an operation with no measured inverse must not be presented as "
        "reversible")
    assert plan["refused"], "the refusal must name the operation"
    assert "clip.delete" in plan["refused"][0]["reason"]


def test_a_reversal_plan_puts_the_last_operation_first():
    """The plan is stated in the order it must be applied."""
    before = _before_snapshot()
    plan = edit_recovery.reversal_plan(
        operations=[{"op": "marker.add", "frame": 12, "color": "Blue",
                     "name": "first"},
                    {"op": "marker.add", "frame": 13, "color": "Blue",
                     "name": "second"}],
        before=before)

    assert [step["operation"]["frame"] for step in plan["steps"]] == [13, 12], (
        "the plan must state the inverses in the order they are applied")


# ── PRES-022: independent verification, and the resume gate ───────────

def _tracks(items):
    return [{"type": "video", "index": 1, "name": "V1", "clips": items}]


def _item(unique_id, start, source="a.mov"):
    return {"unique_id": unique_id, "name": source, "record_in": start,
            "record_out": start + 24, "duration": 24, "left_offset": 0,
            "source_in_frame": 0,
            "source_out_frame": 24, "track_index": 1, "track_type": "video",
            "enabled": True, "transform": {"ZoomX": 1.0, "Pan": 0.0},
            "fusion": {"comp_count": 0, "media_windows": []},
            "markers": [], "media_pool_item_id": source, "source_file": ""}


def test_the_restored_state_is_verified_by_a_fresh_read():
    """The verification compares two READS of the timeline: the state
    before the edit, and a fresh read of the restored timeline."""
    expected = _tracks([_item("a", 0)])
    observed = _tracks([_item("a", 0)])

    verification = edit_recovery.verify_restored(expected=expected,
                                                 observed=observed)

    assert verification["verified"], (
        "a restored state that reads as the state before the edit is "
        "verified")


def test_a_restore_that_does_not_read_as_the_pre_edit_state_is_not_verified():
    """A restore that cannot be proved is NOT a restore: the clip moved,
    so the timeline is not what it was."""
    expected = _tracks([_item("a", 0)])
    observed = _tracks([_item("a", 24)])

    verification = edit_recovery.verify_restored(expected=expected,
                                                 observed=observed)

    assert not verification["verified"], (
        "a restored timeline that does not read as the state before the "
        "edit must not be reported as verified")
    assert verification["diff"], "the difference must be reported"


def test_work_may_not_resume_while_a_transaction_is_unresolved():
    """A transaction whose outcome is not observable blocks the resume:
    nobody knows what is on the timeline."""
    gate = edit_recovery.resume_gate(
        unresolved=[{"transaction_id": "t1", "status": "recovery_required"}])

    assert not gate["resumable"], (
        "an unresolved transaction must block the resume, but the gate "
        "said the work may resume")
    assert "t1" in gate["reason"]


def test_work_may_not_resume_while_the_restored_state_is_unverified():
    """A restore that was not independently verified blocks the
    resume."""
    gate = edit_recovery.resume_gate(
        restored={"verified": False, "reason": "the clip moved"})

    assert not gate["resumable"], (
        "an unverified restore must block the resume")
    assert "the clip moved" in gate["reason"]


def test_work_may_resume_once_nothing_is_unresolved_and_the_state_is_verified():
    """The gate opens when both conditions hold."""
    gate = edit_recovery.resume_gate(
        unresolved=[], restored={"verified": True, "reason": ""})

    assert gate["resumable"], (
        "a resolved, verified recovery must let the work resume")
    assert gate["blockers"] == []


# ── The engine names the reversal, and the gate, where it happens ────

def test_a_failed_post_write_read_back_names_the_reversal_and_blocks_resume(
        world, monkeypatch):
    """Defect: a patch whose post-write read-back failed said only
    'recovery required' - it never said what could be undone, nor that
    the work may not resume until the restored state is verified."""
    calls = {"n": 0}
    original = shadow.read_live

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected post-write read-back failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(shadow, "read_live", flaky)
    patch = _patch(world["base"].generation, [
        {"op": "marker.add", "frame": 12, "color": "Blue", "name": "beat"}],
        patch_id="readback-fail")

    with pytest.raises(edit_patch.PatchRefused) as refused:
        _apply(patch, world)

    recovery = getattr(refused.value, "recovery", None)
    assert recovery, (
        "a patch that may have landed must carry the recovery plan")
    assert recovery["reversal"]["status"] == "reversible", (
        "the reversal that is available must be stated")
    assert recovery["reversal"]["steps"][0]["inverse"] == [
        {"op": "marker.delete", "frame": 12}]
    assert recovery["resume"]["resumable"] is False, (
        "work may not resume while the write's outcome is uncertain")
    assert refused.value.conflict_class == "uncertain_mutation"


# ── PRES-023: the recovery artifact, kept until the edit is settled ───

def _archive(tmp_path, name="reel.drt"):
    path = tmp_path / "pipeline_output" / "review" / "recovery_archives" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a .drt archive")
    return path


def test_a_recovery_archive_is_held_against_the_edit_that_needs_it(tmp_path):
    """Defect: the recovery archive of an unfinished edit was left on
    disk with nothing recording WHICH edit still needed it, so the
    cleanup could not tell it from debris."""
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    path = _archive(tmp_path)

    held = edit_recovery.hold_recovery_archive(
        project, str(path), transaction_id="touch-1", project_id="Podcast")

    assert held["held"], (
        f"the recovery archive was not held: {held.get('reason', '')}")
    rows = edit_recovery.held_recovery_archives(project)
    assert [row["artifact_id"] for row in rows] == [held["artifact_id"]]
    assert rows[0]["transaction_id"] == "touch-1"


def test_a_held_recovery_archive_is_not_collectable(tmp_path):
    """A held artifact must not be a cleanup candidate: it is the only
    copy of the edit that did not finish."""
    from library.tools import storage_lifecycle

    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    path = _archive(tmp_path)
    held = edit_recovery.hold_recovery_archive(
        project, str(path), transaction_id="touch-1", project_id="Podcast")

    inventory = storage_lifecycle.status([project])
    candidates = [row for row in inventory["artifacts"]
                  if row.get("artifact_id") == held["artifact_id"]]
    assert candidates and candidates[0]["state"] != "disposable", (
        "a held recovery artifact must not be disposable")


def test_a_recovery_archive_is_released_only_by_its_own_transaction(tmp_path):
    """One edit must not release another's recovery evidence."""
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    path = _archive(tmp_path)
    held = edit_recovery.hold_recovery_archive(
        project, str(path), transaction_id="touch-1", project_id="Podcast")

    released = edit_recovery.release_recovery_archive(
        project, held["artifact_id"], transaction_id="touch-2",
        outcome="abandoned")

    assert released.get("released") is not True, (
        "a hold must not be released by a transaction that did not take it")
    assert edit_recovery.held_recovery_archives(project), (
        "the held archive must still be held after a wrong release")


def test_an_abandoned_recovery_archive_becomes_disposable(tmp_path):
    """The explicit abandon is the ONLY way a recovery artifact becomes
    collectable again."""
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    path = _archive(tmp_path)
    held = edit_recovery.hold_recovery_archive(
        project, str(path), transaction_id="touch-1", project_id="Podcast")

    released = edit_recovery.release_recovery_archive(
        project, held["artifact_id"], transaction_id="touch-1",
        outcome="abandoned")

    assert released["state"] == "disposable"
    assert edit_recovery.held_recovery_archives(project) == [], (
        "an abandoned recovery archive must no longer be held")


def test_a_committed_recovery_archive_is_kept_rather_than_collected(tmp_path):
    """A committed edit keeps its recovery archive; it is retained, not
    made disposable."""
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    path = _archive(tmp_path)
    held = edit_recovery.hold_recovery_archive(
        project, str(path), transaction_id="touch-1", project_id="Podcast")

    released = edit_recovery.release_recovery_archive(
        project, held["artifact_id"], transaction_id="touch-1",
        outcome="committed")

    assert released["state"] == "retained"
    assert edit_recovery.held_recovery_archives(project) == []


def test_a_recovery_archive_that_cannot_be_read_is_reported_not_raised(tmp_path):
    """A failed hold must not mask the refusal that produced it."""
    project = str(tmp_path / "project")
    (tmp_path / "project").mkdir()

    held = edit_recovery.hold_recovery_archive(
        project, str(tmp_path / "missing.drt"), transaction_id="touch-1",
        project_id="Podcast")

    assert held["held"] is False
    assert held["reason"], "a refused hold must say why"


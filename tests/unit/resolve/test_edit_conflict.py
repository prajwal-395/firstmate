"""An edit that fails to land says WHICH kind of failure it was, and
which of its operations may still be carried onto the head.

PRES-018 names four different badnesses - a stale plan, a concurrent
modification, an execution failure and an uncertain mutation - and today
they arrive as four unrelated vocabularies, so a host cannot tell
"nothing happened yet" from "it may already have landed". PRES-019 asks
for the semantically safe operations of a stale patch to be rebased
rather than the whole patch refused. PRES-020 asks for renewed planning
when the operation's intended target has changed.

Every test here names a defect: the failure message says what the
engine got wrong, not that a number moved.
"""

from __future__ import annotations

import copy

import pytest

from library.tools import edit_conflict, edit_patch, transaction_receipt
from library.tools import timeline_shadow as shadow
from library.tools.ren_refusal import RenRefusal
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
    """Two clips on one track, and the shadow that recorded them."""
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    first = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    second = place_clip(timeline, make_pool_clip("b.mov"), 48, 95)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
    return {"project": project, "timeline": timeline, "store": store,
            "base": base, "first": first, "second": second,
            "timeline_id": store.resolve_timeline("Podcast", "Reel 09")}


def _snapshot(world):
    return world["store"].snapshot(world["base"])


def _patch(base_generation, operations, *, patch_id="compound"):
    return {
        "id": patch_id, "project": "Podcast", "timeline": "Reel 09",
        "base_generation": base_generation, "capability": "reel.touchup",
        "affected_spans": [(0, 96)], "conflict_domains": ["picture_transform"],
        "operations": operations,
    }


def _record_patch_generation(world, patch, snapshot):
    """Record a generation whose change was a Ren patch, not a hand edit."""
    return world["store"].record(
        project="Podcast", timeline_id=world["timeline_id"],
        timeline_name="Reel 09", snapshot=snapshot, source=shadow.PATCH,
        expected_head=world["base"].generation, patch=patch)


# ── PRES-018: four badnesses, four names ─────────────────────────────

def test_an_uncertain_write_is_named_before_any_other_question():
    """A write whose outcome nobody can observe is the most urgent
    class: until the observed state is read, every other question is
    unanswerable."""
    assessment = edit_conflict.classify(mutation_state="unknown")

    assert assessment.conflict_class == edit_conflict.UNCERTAIN_MUTATION, (
        "a write of unknown outcome must be classified as an uncertain "
        f"mutation, not as {assessment.conflict_class!r}")
    assert assessment.action == transaction_receipt.RECOVERY_ACTIONS[
        "api_uncertain"]


def test_an_interrupted_transaction_is_an_uncertain_mutation():
    """A transaction whose durable stream has no terminal commit may
    have written; it is an uncertain mutation, not a stale plan."""
    assessment = edit_conflict.classify(interrupted_transaction=True)

    assert assessment.conflict_class == edit_conflict.UNCERTAIN_MUTATION, (
        "an interrupted transaction may have landed, so it must not be "
        "reported as a mere stale plan")


def test_a_failed_write_is_an_execution_failure():
    """A write that failed with a KNOWN outcome is its own class: what
    it left behind must be reversed, not re-planned."""
    assessment = edit_conflict.classify(execution_status="verification_failed")

    assert assessment.conflict_class == edit_conflict.EXECUTION_FAILURE, (
        "a failed write must be classified as an execution failure, not "
        f"as {assessment.conflict_class!r}")
    assert assessment.action == transaction_receipt.RECOVERY_ACTIONS[
        "execution_failed"]


def test_an_unrecorded_change_is_a_concurrent_modification():
    """A change nobody recorded has unknown intent, so it needs the
    person who made it rather than a mechanical rebase."""
    assessment = edit_conflict.classify(observed_generation=True)

    assert assessment.conflict_class == (
        edit_conflict.CONCURRENT_MODIFICATION), (
        "an unattributed change must be classified as a concurrent "
        f"modification, not as {assessment.conflict_class!r}")
    assert assessment.recovery_class == "needs_question", (
        "an unattributed change has unknown intent, so the host must ask "
        "rather than act")


def test_a_moved_revision_is_a_stale_plan():
    """A plan against an older revision, with nothing else wrong, is the
    one class with a safe mechanical answer."""
    assessment = edit_conflict.classify(planned_base_generation=3,
                                        head_generation=4)

    assert assessment.conflict_class == edit_conflict.STALE_PLAN, (
        "a moved revision must be classified as a stale plan, not as "
        f"{assessment.conflict_class!r}")
    assert assessment.action == transaction_receipt.RECOVERY_ACTIONS[
        "stale_generation"]


def test_a_moved_project_model_revision_is_also_a_stale_plan():
    """The Project Model has its own revision counter; moving it is the
    same class of problem as moving the timeline."""
    assessment = edit_conflict.classify(expected_model_revision=7,
                                        current_model_revision=9)

    assert assessment.conflict_class == edit_conflict.STALE_PLAN, (
        "a moved Project Model revision is a stale plan too")


def test_a_healthy_edit_has_no_conflict():
    """An edit planned on the head that has not been attempted yet is
    not a conflict, and must not be reported as one."""
    assessment = edit_conflict.classify(planned_base_generation=3,
                                        head_generation=3)

    assert not assessment.is_conflict, (
        f"an edit on the head has no conflict, but it was reported as "
        f"{assessment.conflict_class!r}")
    assert assessment.action == ""


def test_every_class_names_an_action_the_shared_vocabulary_prescribes():
    """The four classes must not become a FIFTH vocabulary: each action
    is one the shared receipt vocabulary already prescribes."""
    known_actions = set(transaction_receipt.RECOVERY_ACTIONS.values())
    for name in edit_conflict.CONFLICT_CLASSES:
        assert edit_conflict.ACTIONS[name] in known_actions, (
            f"conflict class {name!r} names an action the shared receipt "
            "vocabulary does not prescribe")
        assert edit_conflict.RECOVERY_CLASSES[name] in {
            "auto_recoverable", "needs_question", "blocked"}, (
            f"conflict class {name!r} names a recovery class the refusal "
            "vocabulary does not know")
    assert set(edit_conflict.CONFLICT_CLASSES) == set(
        edit_conflict.DEFINITIONS), (
        "every conflict class must say what it means")


def test_a_refusal_may_carry_its_class():
    """The refusal shape every `ren` verb prints carries the class, so a
    host can act on it without parsing prose - and a refusal that is not
    about a conflict says so with an empty class."""
    refusal = RenRefusal(what="what", why="why", fix="fix")

    payload = refusal.to_dict()

    assert payload["conflict_class"] == "", (
        "a refusal that is not about a conflict carries an empty class, "
        "not a guessed one")
    classified = RenRefusal(what="what", why="why", fix="fix",
                            conflict_class=edit_conflict.STALE_PLAN)

    assert classified.to_dict()["conflict_class"] == edit_conflict.STALE_PLAN, (
        "a refusal must carry the class of the badness it is about")


# ── PRES-020: the intended target ─────────────────────────────────────

def test_a_replaced_clip_is_a_changed_target(world):
    """A clip that now plays different material is a different target,
    so the operation needs renewed planning rather than a rebase."""
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"][0]["media_pool_item_id"] = "c.mov"
    operation = {"op": "clip.set_property",
                 "unique_id": world["first"].GetUniqueId(),
                 "key": "ZoomX", "value": 1.25}

    change = edit_conflict.target_changed(operation, base, head)

    assert change["changed"], (
        "a clip that now holds different media is a changed target, but "
        "the engine reported it unchanged")


def test_a_ripple_moves_a_clip_without_changing_its_target(world):
    """A cut that moves a clip in time does not change WHAT the clip
    is; following that move is a rebase, not a re-plan."""
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"][0]["record_in"] = 12
    head["tracks"][0]["clips"][0]["record_out"] = 60
    operation = {"op": "clip.set_property",
                 "unique_id": world["first"].GetUniqueId(),
                 "key": "ZoomX", "value": 1.25}

    change = edit_conflict.target_changed(operation, base, head)

    assert not change["changed"], (
        "a clip that only moved in time still holds the same material in "
        "the same place; reporting it changed would refuse every rebase")


def test_a_clip_that_no_longer_exists_is_a_changed_target(world):
    """An operation aimed at a clip that is gone has no target at all."""
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"] = [
        clip for clip in head["tracks"][0]["clips"]
        if clip["unique_id"] != world["first"].GetUniqueId()]
    operation = {"op": "clip.set_property",
                 "unique_id": world["first"].GetUniqueId(),
                 "key": "ZoomX", "value": 1.25}

    change = edit_conflict.target_changed(operation, base, head)

    assert change["changed"], (
        "an operation aimed at a deleted clip must be reported as a "
        "changed target")


def test_a_retrimmed_clip_is_a_changed_target_only_for_an_operation_that_names_the_material(world):
    """Re-trimming the media changes the material, so an operation that
    names a range inside it must be re-planned. An operation that names
    only the clip is unaffected."""
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"][0]["source_in_frame"] = 5
    head["tracks"][0]["clips"][0]["source_out_frame"] = 40
    by_clip = {"op": "clip.set_property",
               "unique_id": world["first"].GetUniqueId(),
               "key": "ZoomX", "value": 1.25}
    by_material = {"op": "clip.trim",
                   "unique_id": world["first"].GetUniqueId(),
                   "in_frame": 0, "out_frame": 30}

    assert not edit_conflict.target_changed(by_clip, base, head)["changed"], (
        "an operation that names only the clip survives a re-trim of the "
        "media; reporting it changed would refuse safe rebases")
    assert edit_conflict.target_changed(by_material, base, head)["changed"], (
        "an operation that names a range inside the media must be "
        "re-planned once that range moved")


def test_renewal_required_asks_a_question_rather_than_planning_in_silence(world):
    """When the target changed the host must ASK; it must not re-plan a
    change nobody approved on its own."""
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"][0]["media_pool_item_id"] = "c.mov"
    operation = {"op": "clip.set_property",
                 "unique_id": world["first"].GetUniqueId(),
                 "key": "ZoomX", "value": 1.25}

    renewal = edit_conflict.renewal_required(operation, base, head)

    assert renewal["renewal_required"], (
        "a changed target must require renewed planning")
    assert renewal["question"], (
        "renewed planning must carry the question the person answers")


# ── PRES-019: only the safe operations are carried ───────────────────

def test_a_patch_planned_on_the_head_needs_no_rebase(world):
    """A patch whose base IS the head has nothing to rebase."""
    patch = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": world["first"].GetUniqueId(),
         "key": "ZoomX", "value": 1.25}])

    partition = edit_conflict.partition_for_rebase(
        patch, world["store"], "Podcast", world["timeline_id"])

    assert partition["verdict"] == edit_conflict.CURRENT, (
        "a patch planned on the head has nothing to rebase, but the "
        f"engine reported {partition['verdict']!r}")


def test_a_compound_patch_splits_into_the_operations_that_may_be_carried_and_the_ones_that_may_not(world):
    """One operation of a compound patch conflicting with a recorded
    change must not refuse the operation that never met it."""
    first_id = world["first"].GetUniqueId()
    second_id = world["second"].GetUniqueId()
    recorded = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.5}],
        patch_id="recorded")
    snapshot = _snapshot(world)
    snapshot["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5
    _record_patch_generation(world, recorded, snapshot)
    stale = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.25},
        {"op": "clip.set_property", "unique_id": second_id,
         "key": "ZoomX", "value": 1.25}],
        patch_id="stale")

    partition = edit_conflict.partition_for_rebase(
        stale, world["store"], "Podcast", world["timeline_id"])

    assert partition["verdict"] == edit_conflict.REBASE_PART, (
        "a compound patch with one conflicting operation is a PARTIAL "
        f"rebase, but the engine reported {partition['verdict']!r}")
    assert [row["index"] for row in partition["rebaseable"]] == [1], (
        "the operation that never met the recorded change may be "
        "carried, and only that one")
    assert [row["index"] for row in partition["needs_replanning"]] == [0], (
        "the operation that met the recorded change must be re-planned")


def test_a_patch_whose_target_was_replaced_needs_renewed_planning_not_a_rebase(world):
    """When the clip an operation addresses now holds different
    material, carrying it would apply the change to something else."""
    first_id = world["first"].GetUniqueId()
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"][0]["media_pool_item_id"] = "c.mov"
    recorded = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.5}],
        patch_id="recorded")
    snapshot = copy.deepcopy(base)
    snapshot["tracks"][0]["clips"][0]["media_pool_item_id"] = "c.mov"
    snapshot["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5
    _record_patch_generation(world, recorded, snapshot)
    stale = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.25}],
        patch_id="stale")

    partition = edit_conflict.partition_for_rebase(
        stale, world["store"], "Podcast", world["timeline_id"])

    assert partition["verdict"] == edit_conflict.REPLAN_ALL, (
        "an operation whose target now holds other material must not be "
        f"carried, but the engine reported {partition['verdict']!r}")
    assert partition["needs_replanning"][0]["kind"] == "target_changed", (
        "the reason must name the changed target, not a generic conflict")


def test_rebase_decision_refuses_to_carry_half_a_compound_edit_silently(world):
    """A partial rebase is not a licence to apply half an edit: the
    decision names what needs renewed planning."""
    first_id = world["first"].GetUniqueId()
    second_id = world["second"].GetUniqueId()
    recorded = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.5}],
        patch_id="recorded")
    snapshot = _snapshot(world)
    snapshot["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5
    _record_patch_generation(world, recorded, snapshot)
    stale = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.25},
        {"op": "clip.set_property", "unique_id": second_id,
         "key": "ZoomX", "value": 1.25}],
        patch_id="stale")

    partition = edit_conflict.partition_for_rebase(
        stale, world["store"], "Podcast", world["timeline_id"])
    decision = edit_conflict.rebase_decision(partition)

    assert decision["decision"] == "replan", (
        "a compound edit with an operation that needs renewed planning "
        "must not be carried as a carry")
    assert decision["needs_renewed_planning"], (
        "the decision must name the operations that need renewed planning")


def test_an_operation_addressing_a_frame_does_not_follow_an_unrecorded_shift(world):
    """A frame does not move with a cut, so an operation that names a
    frame cannot be carried past a change nobody recorded."""
    base = _snapshot(world)
    head = copy.deepcopy(base)
    head["tracks"][0]["clips"][0]["record_in"] = 24
    head["tracks"][0]["clips"][0]["record_out"] = 72
    operation = {"op": "marker.add", "frame": 60, "color": "Red",
                 "name": "note"}

    change = edit_conflict.target_changed(operation, base, head)

    assert not change["changed"], (
        "a marker names no clip, so the target check does not judge it; "
        "the frame algebra owns that question")


def test_the_patch_algebra_still_refuses_a_whole_patch_on_conflict(world):
    """The partial partition is an ADDITION: the all-or-nothing carry
    is unchanged, so nothing that used it changes behaviour."""
    from library.tools import patch_algebra

    first_id = world["first"].GetUniqueId()
    recorded = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.5}],
        patch_id="recorded")
    snapshot = _snapshot(world)
    snapshot["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5
    _record_patch_generation(world, recorded, snapshot)
    stale = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.25},
        {"op": "clip.set_property", "unique_id": world["second"].GetUniqueId(),
         "key": "ZoomX", "value": 1.25}],
        patch_id="stale")

    moved, reason = patch_algebra.carry(
        edit_patch.EditPatch.from_dict(stale), world["store"], "Podcast",
        world["timeline_id"])

    assert moved is None, (
        "the existing all-or-nothing carry must still refuse a patch with "
        "a conflicting operation")
    assert reason, "a refusal must say why"


# ── The classification reaches the paths that already know the facts ──

def test_a_stale_patch_refusal_names_its_class(world):
    """A patch planned on an older revision, with a recorded change in
    between, is a stale plan - and the refusal says so."""
    first_id = world["first"].GetUniqueId()
    recorded = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.5}],
        patch_id="recorded")
    edit_patch.apply_patch(
        recorded, resolve=FakeResolve(world["project"]),
        project=world["project"], timeline=world["timeline"],
        store=world["store"])
    stale = _patch(world["base"].generation, [
        {"op": "clip.set_property", "unique_id": first_id,
         "key": "ZoomX", "value": 1.25}],
        patch_id="stale")

    with pytest.raises(edit_patch.StalePatch) as refused:
        edit_patch.apply_patch(
            stale, resolve=FakeResolve(world["project"]),
            project=world["project"], timeline=world["timeline"],
            store=world["store"])

    assert refused.value.conflict_class == edit_conflict.STALE_PLAN, (
        "a patch planned on an older revision is a stale plan, but the "
        f"refusal named {refused.value.conflict_class!r}")
    assert refused.value.to_dict()["conflict_class"] == (
        edit_conflict.STALE_PLAN), (
        "the class must reach the machine-readable refusal")


def test_a_hand_edit_refusal_names_the_concurrent_modification(world):
    """A change nobody recorded has unknown intent, so the refusal says
    so rather than calling it a stale plan."""
    world["timeline"].AddMarker(30, "Red", "captain", "", 1)
    stale = _patch(world["base"].generation, [
        {"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
        patch_id="hand-edit")
    stale["conflict_domains"] = ["markers"]

    with pytest.raises(edit_patch.StalePatch) as refused:
        edit_patch.apply_patch(
            stale, resolve=FakeResolve(world["project"]),
            project=world["project"], timeline=world["timeline"],
            store=world["store"])

    assert refused.value.conflict_class == (
        edit_conflict.CONCURRENT_MODIFICATION), (
        "an unattributed change is a concurrent modification, but the "
        f"refusal named {refused.value.conflict_class!r}")


def test_an_interrupted_transaction_names_the_uncertain_mutation():
    """The durable-stream scan already knows a transaction may have
    written; the row must say which class that is."""
    events = [{"type": "edit_transaction_stage",
               "fields": {"transaction_id": "t1", "stage": "operation_applying",
                          "index": 0, "operations": [{"op": "clip.insert"}]}}]

    rows = transaction_receipt.interrupted(events)

    assert rows and rows[0]["conflict_class"] == "uncertain_mutation", (
        "a transaction with no terminal commit may have written, so it "
        "must be named an uncertain mutation")
    assert rows[0]["status"] == "recovery_required", (
        "an interrupted transaction still blocks the next write")


def test_every_class_the_engine_names_is_one_the_module_enumerates(world):
    """No path may invent a fifth class: the vocabulary is closed."""
    seen = set()
    seen.add(edit_conflict.classify().conflict_class)
    seen.add(edit_conflict.classify(mutation_state="unknown").conflict_class)
    seen.add(edit_conflict.classify(observed_generation=True).conflict_class)
    seen.add(edit_conflict.classify(planned_base_generation=1,
                                    head_generation=2).conflict_class)
    seen.add(transaction_receipt.interrupted([{
        "type": "edit_transaction_stage",
        "fields": {"transaction_id": "t", "stage": "prepared"}}])[0][
            "conflict_class"])

    assert seen <= set(edit_conflict.CONFLICT_CLASSES) | {
        edit_conflict.NO_CONFLICT}, (
        f"the engine named a class outside the enumeration: {sorted(seen)}")


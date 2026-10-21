"""Two changes, and whether they disagree about the same editorial decision.

Every test here names a defect it prevents. The three the punch list
asks for are the first three:

- two INDEPENDENT edits merge, and both survive the merge;
- two OVERLAPPING edits conflict, and the conflict names both versions;
- a captain's hand edit and a Ren edit on the same clip: Ren's is
  refused, and the captain's stands.

The rest are the ways the answer could be wrong in the dangerous
direction - a merge that silently drops an effect, or a merge that
happens because a measurement was absent.
"""
from __future__ import annotations

import copy

import pytest

from library.tools import patch_algebra, semantic_conflict
from library.tools import timeline_shadow as shadow

BASE = "Podcast"
TIMELINE = "Reel 09"


# ── The recorded state the changes are read off ────────────────────────


def _clip(uid, record_in, record_out, *, track_type="video", track_index=1,
          track_name="V1", name=None, source_in=0, source_out=None,
          pan=0.0, enabled=True):
    return {
        "unique_id": uid,
        "name": name or f"{uid}.mov",
        "track_type": track_type,
        "track_index": track_index,
        "track_name": track_name,
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": source_in,
        "source_out_frame": (source_out if source_out is not None
                             else record_out - record_in),
        "source_file": f"/pool/{uid}.mov",
        "enabled": enabled,
        "transform": {"Pan": pan, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0},
        "markers": [],
    }


def _snapshot(clips):
    return {
        "start_frame": 0,
        "end_frame": 480,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": list(clips)}],
        "markers": {"timeline": []},
    }


def _base():
    """Three clips on one row: a, b, c."""
    return _snapshot([
        _clip("a", 0, 120),
        _clip("b", 120, 240),
        _clip("c", 240, 480),
    ])


def _with_pan(base, uid, pan):
    snapshot = copy.deepcopy(base)
    for clip in snapshot["tracks"][0]["clips"]:
        if clip["unique_id"] == uid:
            clip["transform"]["Pan"] = pan
    return snapshot


def _with_span(base, uid, record_in, record_out, source_out=None):
    snapshot = copy.deepcopy(base)
    for clip in snapshot["tracks"][0]["clips"]:
        if clip["unique_id"] == uid:
            clip["record_in"] = record_in
            clip["record_out"] = record_out
            clip["duration"] = record_out - record_in
            if source_out is not None:
                clip["source_out_frame"] = source_out
    return snapshot


def _hand_edits(before, after, version="generation 2 (a hand edit)"):
    return semantic_conflict.edits_between(
        before, after, origin=semantic_conflict.ORIGIN_CAPTAIN,
        version=version)


def _ren_edits(before, after, version="patch p-1"):
    return semantic_conflict.edits_between(
        before, after, origin=semantic_conflict.ORIGIN_REN, version=version)


def _set(left, right, version_left="generation 2 (a hand edit)",
         version_right="patch p-1"):
    return semantic_conflict.detect(
        semantic_conflict.EditSet.of(version_left,
                                     semantic_conflict.ORIGIN_CAPTAIN, left),
        semantic_conflict.EditSet.of(version_right,
                                     semantic_conflict.ORIGIN_REN, right))


# ── The three cases the punch list names ───────────────────────────────


def test_two_independent_edits_merge_and_both_survive():
    """Two changes on different items are two decisions, and both hold.

    The defect: a merge that keeps one side and drops the other, or a
    conflict raised for two changes that never met.
    """
    base = _base()
    hand = _hand_edits(base, _with_pan(base, "c", 5.0))
    ren = _ren_edits(base, _with_pan(base, "a", 3.0))

    report = _set(hand, ren)

    assert report.mergeable, report.reason()
    merged = semantic_conflict.merge(
        semantic_conflict.EditSet.of("generation 2 (a hand edit)",
                                     semantic_conflict.ORIGIN_CAPTAIN, hand),
        semantic_conflict.EditSet.of("patch p-1",
                                     semantic_conflict.ORIGIN_REN, ren))
    assert len(merged.edits) == 2, merged.edits
    assert {edit.version for edit in merged.edits} == {
        "generation 2 (a hand edit)", "patch p-1"}
    assert report.resolution() == "both preserved"


def test_two_overlapping_edits_conflict_and_both_versions_are_named():
    """Two changes that write the same property differently disagree.

    The defect: a conflict with one name, which nobody can resolve.
    """
    base = _base()
    hand = _hand_edits(base, _with_pan(base, "a", 5.0))
    ren = _ren_edits(base, _with_pan(base, "a", 9.0))

    report = _set(hand, ren)

    assert not report.mergeable
    assert report.conflicts, report.as_dict()
    assert report.conflicts[0].axis == semantic_conflict.AXIS_PROPERTY
    reason = report.reason()
    assert "generation 2 (a hand edit)" in reason
    assert "patch p-1" in reason
    with pytest.raises(semantic_conflict.SemanticConflictRefused) as refused:
        semantic_conflict.merge(
            semantic_conflict.EditSet.of("generation 2 (a hand edit)",
                                         semantic_conflict.ORIGIN_CAPTAIN,
                                         hand),
            semantic_conflict.EditSet.of("patch p-1",
                                         semantic_conflict.ORIGIN_REN, ren))
    assert "generation 2 (a hand edit)" in str(refused.value)
    assert "patch p-1" in str(refused.value)


def test_a_hand_edit_and_a_ren_edit_on_one_clip_refuse_ren():
    """The captain's hand stands; Ren's edit on the same clip is refused.

    The defect: a conflict resolved by strength, so a hand edit is
    overwritten by the next thing that writes the same clip.
    """
    base = _base()
    hand = _hand_edits(base, _with_pan(base, "a", 5.0))
    ren = _ren_edits(base, _with_pan(base, "a", 9.0))

    report = _set(hand, ren)

    assert not report.mergeable
    assert report.resolution() == (
        "the captain's edit (generation 2 (a hand edit)) stands; "
        "Ren's edit (patch p-1) is refused")


# ── The ways the answer could be wrong in the dangerous direction ──────


def test_a_trim_that_moves_a_row_makes_a_later_change_unproven():
    """A change that moves a row's frames invalidates the frames after it.

    The defect: reading "different items" as "independent" when the
    hand edit moved the row the Ren edit is placed in. It is not
    independent - it is UNPROVEN, and unproven is not a merge.
    """
    base = _base()
    hand = _hand_edits(base, _with_span(base, "a", 0, 100, source_out=100))
    ren = _ren_edits(base, _with_pan(base, "b", 3.0))

    report = _set(hand, ren)

    assert not report.mergeable
    assert report.unproven, report.as_dict()
    assert report.unproven[0].axis == semantic_conflict.AXIS_COORDINATES
    assert report.resolution() == "resolution required"


def test_two_changes_claiming_the_same_frames_of_one_row_conflict():
    """One row holds one item at a frame.

    The defect: two changes on different items that both claim the same
    region of the same row being merged, which would put two items
    where one row has room for one.
    """
    base = _base()
    hand = _hand_edits(base, _with_span(base, "b", 130, 250))
    ren = _ren_edits(base, _with_span(base, "c", 230, 470))

    report = _set(hand, ren)

    assert not report.mergeable
    assert report.conflicts, report.as_dict()
    assert report.conflicts[0].axis == semantic_conflict.AXIS_TIMELINE


def test_an_operation_nobody_can_describe_is_never_agreement():
    """A change that cannot be read is not a change that agrees.

    The defect: a patch carrying an operation the comparison cannot
    describe being judged independent, because the comparison saw
    nothing to disagree with.
    """
    assert semantic_conflict.unreadable_operations(
        [{"op": "clip.set_property", "unique_id": "a", "key": "Pan",
          "value": 1.0}]) == []
    assert semantic_conflict.unreadable_operations(
        [{"op": "grade.node_remove", "node": 3}]) == ["grade.node_remove"]


def test_an_operation_on_an_item_the_base_does_not_hold_is_unreadable():
    """There is no item to say what an operation on a missing item claims.

    The defect: an operation naming an item the base does not hold
    being read as "this patch touches nothing", and so as independent
    of a hand edit that touched everything.
    """
    base = _base()
    assert semantic_conflict.unreadable_operations(
        [{"op": "clip.set_property", "unique_id": "a", "key": "Pan",
          "value": 1.0}], base) == []
    assert semantic_conflict.unreadable_operations(
        [{"op": "clip.set_property", "unique_id": "not-here", "key": "Pan",
          "value": 1.0}], base) == ["clip.set_property"]


def test_a_hand_edit_that_moves_the_timeline_frame_refuses_everything():
    """The timeline's own frame is the frame every row is placed in.

    The defect: a hand edit that moved the timeline's start frame being
    read as a change to nothing, so a patch on any row judged
    independent of it.
    """
    base = _base()
    moved = copy.deepcopy(base)
    moved["start_frame"] = 24
    hand = _hand_edits(base, moved)
    ren = _ren_edits(base, _with_pan(base, "a", 3.0))

    report = _set(hand, ren)

    assert not report.mergeable
    assert report.unproven, report.as_dict()
    assert report.unproven[0].axis == semantic_conflict.AXIS_COORDINATES


def test_a_merge_of_two_changes_that_write_the_same_thing_keeps_one():
    """Two changes that write the same thing are one change.

    The defect: a merged set that carries the same write twice, so a
    reader counts two decisions where there was one.
    """
    base = _base()
    hand = _hand_edits(base, _with_pan(base, "a", 5.0))
    ren = _ren_edits(base, _with_pan(base, "a", 5.0))

    report = _set(hand, ren)
    assert report.mergeable, report.reason()

    merged = semantic_conflict.merge(
        semantic_conflict.EditSet.of("generation 2 (a hand edit)",
                                     semantic_conflict.ORIGIN_CAPTAIN, hand),
        semantic_conflict.EditSet.of("patch p-1",
                                     semantic_conflict.ORIGIN_REN, ren))
    assert len(merged.edits) == 1, merged.edits


# ── The wiring: the carry over a hand edit ────────────────────────────


def _store(tmp_path, index, snapshots):
    """A shadow store holding `snapshots` as recorded generations."""
    store = shadow.ShadowStore(tmp_path / f"shadow-{index}.db")
    for snapshot, source in snapshots:
        head = store.head(BASE, TIMELINE)
        store.record(project=BASE, timeline_id=TIMELINE,
                     timeline_name=TIMELINE, snapshot=snapshot, source=source,
                     expected_head=head.generation if head else 0)
    return store


def _patch(pid, base_generation, operations, spans):
    return {
        "id": pid, "project": BASE, "timeline": TIMELINE,
        "base_generation": base_generation,
        "capability": "reel.set_properties",
        "affected_spans": [list(span) for span in spans],
        "conflict_domains": ["picture_transform"],
        "operations": operations,
    }


def test_a_patch_moves_over_a_hand_edit_that_does_not_meet_it(tmp_path):
    """A hand edit on another clip does not block a patch on this one.

    The defect: one hand edit blocking every later patch, so a captain
    who fixed one thing stopped Ren from working on the rest.
    """
    base = _base()
    store = _store(tmp_path, 1, [(base, shadow.OBSERVED),
                                 (_with_pan(base, "c", 5.0), shadow.OBSERVED)])
    patch = _patch("p-1", 1,
                   [{"op": "clip.set_property", "unique_id": "a",
                     "key": "Pan", "value": 3.0}], [(0, 120)])

    moved, reason = patch_algebra.carry(patch, store, BASE, TIMELINE)

    assert moved is not None, reason
    assert moved.base_generation == 2, moved


def test_a_patch_that_meets_a_hand_edit_is_refused_and_both_are_named(tmp_path):
    """A patch that writes the same property the hand edit wrote is refused.

    The defect: the hand edit overwritten by the next patch on the same
    clip, with a refusal that named neither side.
    """
    base = _base()
    store = _store(tmp_path, 2, [(base, shadow.OBSERVED),
                                 (_with_pan(base, "a", 5.0), shadow.OBSERVED)])
    patch = _patch("p-2", 1,
                   [{"op": "clip.set_property", "unique_id": "a",
                     "key": "Pan", "value": 9.0}], [(0, 120)])

    moved, reason = patch_algebra.carry(patch, store, BASE, TIMELINE)

    assert moved is None
    assert "generation 2" in reason, reason
    assert "p-2" in reason, reason


def test_a_hand_edit_that_moves_a_row_refuses_a_patch_on_that_row(tmp_path):
    """A hand edit that moved a row's frames refuses a patch placed on it.

    The defect: a patch landing on frames the hand edit invalidated,
    because the two changes were "different items" and so independent.
    """
    base = _base()
    store = _store(tmp_path, 3, [
        (base, shadow.OBSERVED),
        (_with_span(base, "a", 0, 100, source_out=100), shadow.OBSERVED)])
    patch = _patch("p-3", 1,
                   [{"op": "clip.set_property", "unique_id": "b",
                     "key": "Pan", "value": 3.0}], [(120, 240)])

    moved, reason = patch_algebra.carry(patch, store, BASE, TIMELINE)

    assert moved is None
    assert "hand edit" in reason, reason

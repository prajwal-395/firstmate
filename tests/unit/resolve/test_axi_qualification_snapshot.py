"""The pre- and post-operation comparison a qualification run reports on.

AXI-030 asks for two things of a run: that the operation did EXACTLY
what was requested, and that it changed nothing else. A receipt that
says "committed" answers neither - it answers "the write landed".

These tests name the defect the comparison exists to catch: an edit
that succeeds and takes something else with it.
"""
from __future__ import annotations

from library.tools import axi_qualification_snapshot as snapshots


def _clip(uid: str = "item-1", **overrides) -> dict:
    clip = {
        "unique_id": uid,
        "name": f"{uid}.mp4",
        "media_type": "Video",
        "linked_unique_ids": [],
        "record_in": 0,
        "record_out": 48,
        "source_in_frame": 0,
        "source_out_frame": 48,
        "enabled": True,
        "speed": 100.0,
        "transform": {},
        "color": {},
        "fusion": {"comp_count": 0, "comp_names": []},
    }
    clip.update(overrides)
    return clip


def _snapshot(*clips, rows=None) -> dict:
    tracks = rows if rows is not None else [
        {"type": "video", "index": 1, "name": "Video 1",
         "clips": list(clips)}]
    return snapshots.snapshot(tracks, {"timelineFrameRate": "24"})


def test_a_run_that_changed_nothing_is_preserved():
    before = _snapshot(_clip("a"), _clip("b"))
    after = _snapshot(_clip("a"), _clip("b"))

    verdict = snapshots.compare(before, after, [])

    assert verdict["unintended_modifications"] == []
    assert snapshots.preserved(verdict)


def test_the_delete_of_its_own_target_is_the_requested_change():
    before = _snapshot(_clip("a"), _clip("b"))
    after = _snapshot(_clip("b"))

    verdict = snapshots.compare(
        before, after, [{"op": "clip.delete", "unique_id": "a"}])

    assert verdict["unintended_modifications"] == []
    assert verdict["requested_changes"] == [
        {"op": "clip.delete", "unique_id": "a", "change": "removed",
         "fields": []}]


def test_a_delete_that_took_another_item_with_it_is_unintended():
    """The defect the comparison exists to catch."""
    before = _snapshot(_clip("a"), _clip("b"))
    after = _snapshot()

    verdict = snapshots.compare(
        before, after, [{"op": "clip.delete", "unique_id": "a"}])

    assert not snapshots.preserved(verdict)
    assert verdict["unintended_modifications"] == [
        {"op": "", "unique_id": "b", "change": "removed", "fields": []}]


def test_a_property_write_that_also_re_spanned_its_item_is_unintended():
    """Writing the property was asked for; moving the clip was not."""
    before = _snapshot(_clip("a"))
    after = _snapshot(_clip("a", record_in=48, record_out=96))

    verdict = snapshots.compare(before, after, [
        {"op": "clip.set_property", "unique_id": "a", "key": "Pan",
         "value": 40.0}])

    assert not snapshots.preserved(verdict)
    assert verdict["unintended_modifications"] == [
        {"op": "clip.set_property", "unique_id": "a", "change": "changed",
         "fields": ["record_in"]},
        {"op": "clip.set_property", "unique_id": "a", "change": "changed",
         "fields": ["record_out"]}]


def test_a_property_write_that_changed_the_transform_is_the_request():
    before = _snapshot(_clip("a"))
    after = _snapshot(_clip("a", transform={"Pan": 40.0}))

    verdict = snapshots.compare(before, after, [
        {"op": "clip.set_property", "unique_id": "a", "key": "Pan",
         "value": 40.0}])

    assert snapshots.preserved(verdict)
    assert verdict["requested_changes"][0]["fields"] == ["transform"]


def test_an_item_nobody_placed_is_unintended():
    before = _snapshot()
    after = _snapshot(_clip("new"))

    verdict = snapshots.compare(before, after, [])

    assert not snapshots.preserved(verdict)
    assert verdict["unintended_modifications"][0]["unique_id"] == "new"


def test_a_placement_the_operation_asked_for_is_the_requested_change():
    before = _snapshot(_clip("a"))
    after = _snapshot(_clip("a"), _clip("new"))

    verdict = snapshots.compare(
        before, after, [{"op": "clip.insert", "unique_id": "new"}])

    assert snapshots.preserved(verdict)
    assert verdict["requested_changes"][0]["change"] == "placed"


def test_a_row_the_operation_added_is_the_requested_change():
    before = _snapshot(_clip("a"))
    after = snapshots.snapshot(
        [{"type": "video", "index": 1, "name": "Video 1",
          "clips": [_clip("a")]},
         {"type": "video", "index": 2, "name": "V2", "clips": []}],
        {"timelineFrameRate": "24"})

    verdict = snapshots.compare(before, after, [{"op": "track.add"}])

    assert snapshots.preserved(verdict)
    assert verdict["requested_changes"][0]["change"] == "track_added"


def test_a_row_nobody_added_is_unintended():
    before = _snapshot(_clip("a"))
    after = snapshots.snapshot(
        [{"type": "video", "index": 1, "name": "Video 1",
          "clips": [_clip("a")]},
         {"type": "video", "index": 2, "name": "V2", "clips": []}],
        {"timelineFrameRate": "24"})

    verdict = snapshots.compare(before, after, [])

    assert not snapshots.preserved(verdict)
    assert verdict["unintended_modifications"][0]["change"] == "track_added"


def test_a_trim_that_moved_the_source_range_is_the_requested_change():
    before = _snapshot(_clip("a"))
    after = _snapshot(_clip("a", source_in_frame=12, source_out_frame=60))

    verdict = snapshots.compare(before, after, [
        {"op": "clip.trim", "unique_id": "a", "source_in": 12,
         "source_out": 60}])

    assert snapshots.preserved(verdict)


def test_the_snapshot_does_not_read_a_timestamp_as_a_change():
    """A read time is not part of the timeline."""
    clip = _clip("a")
    clip["read_at"] = "2026-10-20T00:00:00Z"
    before = _snapshot(clip)
    after = _snapshot(clip)

    assert snapshots.changes(before, after)["changed"] == {}

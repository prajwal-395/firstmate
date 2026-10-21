"""A write is verified against a handle that has caught up.

Resolve's timeline handle does not refresh the instant a write lands: a
placement is on the row, and the handle still reads the state from
BEFORE the write.  A path that verifies a write by reading the timeline
straight after it therefore reads a timeline that never moved.

Measured 2026-10-21 on a Ren-owned scratch copy of a real reel
(portrait 1080x1920, 23.976fps, 65 clips over eight video rows):

* `resolve-axi edit place` reported "append reported success but the
  item count did not move (67 -> 67) - nothing landed" while the clip
  WAS on the cover row at the requested frame;
* the EditPatch `clip.insert` wrote the placed item's identity onto the
  operation as the string "None", because a freshly appended item has no
  readable unique id until the handle has caught up, and the
  verification then looked for a clip called "None" and found nothing.

The synthetic fixture never saw it: one or two clips refresh fast
enough to read the write back on the first try.  A real reel is dense,
and that is exactly why the qualification earned on the fixture says
nothing about the reel.

These tests name the defect and hold the fix in place.
"""
from __future__ import annotations

import pytest

from library.tools import resolve_write_settle as settle


def test_a_write_that_landed_is_read_back_after_the_handle_catches_up():
    """The read is retried until the handle reflects the write."""
    reads = []

    def read() -> dict:
        reads.append(len(reads))
        return {"clips": len(reads)}

    snapshot, settled, waited = settle.settled_snapshot(
        read, before_hash="before")
    assert settled is True
    assert snapshot == {"clips": 1}
    assert len(reads) == 1
    assert waited >= 0.0


def test_a_slow_handle_is_waited_for_and_the_new_state_is_returned():
    """A handle that answers late is waited for, not read once."""
    from library.tools import timeline_shadow

    states = [{"clips": 0}, {"clips": 0}, {"clips": 1}]
    reads = []

    def read() -> dict:
        reads.append(len(reads))
        return states[min(len(reads), len(states)) - 1]

    snapshot, settled, _waited = settle.settled_snapshot(
        read, before_hash=timeline_shadow.snapshot_hash({"clips": 0}))
    assert settled is True
    assert snapshot == {"clips": 1}
    assert len(reads) == 3


def test_a_write_that_did_not_land_is_reported_as_not_settled():
    """A write that never lands is a failure, and the bound says so."""
    from library.tools import timeline_shadow

    def read() -> dict:
        return {"clips": 0}

    snapshot, settled, waited = settle.settled_snapshot(
        read, before_hash=timeline_shadow.snapshot_hash({"clips": 0}),
        timeout_seconds=0.2, interval_seconds=0.01)
    assert settled is False
    assert snapshot == {"clips": 0}
    assert waited >= 0.2


def test_a_read_that_fails_raises_and_is_not_read_as_staleness():
    """A read that FAILED is uncertainty, not a handle that has not caught
    up: the caller's own failure path owns it."""
    def read() -> dict:
        raise RuntimeError("the read failed")

    with pytest.raises(RuntimeError):
        settle.settled_snapshot(read, before_hash="before",
                                timeout_seconds=0.2, interval_seconds=0.01)


def test_a_placed_item_has_no_identity_until_the_handle_catches_up():
    """`str(None)` is "None", and "None" is not an identity."""
    class Item:
        def __init__(self) -> None:
            self.reads = 0

        def GetUniqueId(self):
            self.reads += 1
            if self.reads < 3:
                return None
            return "placed-1"

    item = Item()
    identity, settled, _waited = settle.settled_placed_identity(item)
    assert identity == "placed-1"
    assert settled is True


def test_a_placed_item_that_never_gets_an_identity_is_not_one():
    """An unreadable identity is refused, never written as "None"."""
    class Item:
        def GetUniqueId(self):
            return None

    identity, settled, _waited = settle.settled_placed_identity(
        Item(), timeout_seconds=0.2, interval_seconds=0.01)
    assert identity == ""
    assert settled is False


def test_the_item_count_is_waited_for_and_a_miss_is_reported():
    """The count that did not move is a miss, not a landing."""
    counts = [67, 67, 68]
    reads = []

    def count() -> int:
        reads.append(len(reads))
        return counts[min(len(reads), len(counts)) - 1]

    after, settled, _waited = settle.settled_item_count(count, 67)
    assert settled is True
    assert after == 68

    after, settled, _waited = settle.settled_item_count(
        lambda: 67, 67, timeout_seconds=0.2, interval_seconds=0.01)
    assert settled is False
    assert after == 67


def test_the_edit_patch_insert_reads_the_placed_identity_not_none():
    """The op carries the placed item's RESOLVED identity."""
    from library.tools import edit_algebra

    class Placed:
        def __init__(self) -> None:
            self.reads = 0

        def GetUniqueId(self):
            self.reads += 1
            return None if self.reads < 2 else "placed-1"

    class PoolItem:
        def GetClipProperty(self, key):
            return 48

        def GetUniqueId(self):
            return "pool-1"

    class Pool:
        def __init__(self) -> None:
            self.item = PoolItem()
            self.placed = Placed()

        def GetRootFolder(self):
            return self

        def GetSubFolderList(self):
            return []

        def GetClipList(self):
            return [self.item]

        def GetUniqueId(self):
            return "pool-1"

        def AppendToTimeline(self, payload):
            self.payload = payload
            return [self.placed]

    class Project:
        def __init__(self) -> None:
            self.pool = Pool()

        def GetMediaPool(self):
            return self.pool

    project = Project()
    op = {"op": "clip.insert", "unique_id": "requested-1",
          "track_type": "video", "track_index": 6,
          "record_in": 500, "record_out": 560,
          "media_pool_item": "pool-1", "source_in": 0, "source_out": 59}
    edit_algebra._apply_clip_insert(op, None, None, project=project)
    assert op["client_unique_id"] == "requested-1"
    assert op["unique_id"] == "placed-1"

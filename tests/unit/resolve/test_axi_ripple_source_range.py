"""The ripple must not change the source range of the clip it shifts.

Defect, measured on the qualification scratch timeline (2026-10-21):
the track-scoped ripple re-placed the clip it shifted with a source
range ONE FRAME LONGER than the clip it had shifted.  A 48-frame
successor came back as 49 frames, and the ripple's own verification
did not see it, because the verification compared the arithmetic
"source_in + duration" with itself rather than with the source range
the re-placed clip actually carried.

Two facts produce it:

* Resolve reports a source end in TWO forms - the inclusive end on an
  existing clip, the exclusive end on a fresh placement - and the
  record duration decides which form a read speaks
  (`_inclusive_source_end`);
* the re-place adds ONE to the end it is given (`endFrame =
  source_out + 1`), so a snapshot that kept the raw reading re-placed
  a clip whose source range was shorter than its media one frame
  longer than it was.

So the snapshot has to speak the ONE reading, and a span that tells
neither form has to be REFUSED rather than re-placed unfaithfully -
the rule the move and trim paths already follow.
"""

from __future__ import annotations

import pytest

from library.tools import resolve_axi_core_commands as axi


class _FakePoolItem:
    def GetUniqueId(self):
        return "pool-item-1"


class _FakeItem:
    """One timeline item, read the way Resolve reads it."""

    def __init__(self, *, name="clip.mp4", record_in=0, record_out=48,
                 source_in=0, source_out=48, percentage=100.0):
        self._name = name
        self._record_in = record_in
        self._record_out = record_out
        self._source_in = source_in
        self._source_out = source_out
        self._percentage = percentage

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._record_in

    def GetEnd(self):
        return self._record_out

    def GetSourceStartFrame(self):
        return self._source_in

    def GetSourceEndFrame(self):
        return self._source_out

    def GetUniqueId(self):
        return "item-1"

    def GetMediaPoolItem(self):
        return _FakePoolItem()

    def GetSpeed(self):
        return {"Percentage": self._percentage}


@pytest.fixture(autouse=True)
def _no_preservation_capture(monkeypatch):
    """The preservation capture needs a live item; the snapshot does not."""
    from library.tools import edit_state_preservation

    monkeypatch.setattr(
        edit_state_preservation, "capture",
        lambda item, picture=False: {"linked_item_ids": []})


def test_the_snapshot_speaks_the_inclusive_source_end():
    """A fresh placement echoes the EXCLUSIVE end; the snapshot normalizes.

    The re-place adds one to the end it is given, so the snapshot that
    kept the raw reading re-placed this clip 49 frames long.
    """
    item = _FakeItem(record_in=96, record_out=144,
                     source_in=0, source_out=48)

    snapshot = axi._ripple_member_snapshot(item, "video", 1)

    assert snapshot["duration"] == 48
    assert snapshot["source_out"] == 47


def test_the_snapshot_keeps_the_reading_an_existing_clip_speaks():
    """A clip that reports the inclusive end keeps that reading."""
    item = _FakeItem(record_in=96, record_out=192,
                     source_in=0, source_out=95)

    snapshot = axi._ripple_member_snapshot(item, "video", 1)

    assert snapshot["duration"] == 96
    assert snapshot["source_out"] == 95


def test_the_snapshot_refuses_a_source_range_it_cannot_read():
    """A span that tells neither form is REFUSED, not re-placed."""
    item = _FakeItem(record_in=0, record_out=48,
                     source_in=0, source_out=10)

    with pytest.raises(axi.AxiError, match="faithfully"):
        axi._ripple_member_snapshot(item, "video", 1)


def test_the_inventory_reads_the_source_range_the_clip_really_carries():
    """The verification compares a READ range, not arithmetic.

    The ripple's own check compared "source_in + duration" with the
    same arithmetic, so it could not see a re-place that came back
    with a different source range.  The inventory reads the range the
    item carries and normalizes it into the one reading.
    """
    assert axi._read_inclusive_source_end(
        {"source_in": 0, "source_out": 48}, 48) == 47
    assert axi._read_inclusive_source_end(
        {"source_in": 0, "source_out": 95}, 96) == 95
    assert axi._read_inclusive_source_end(
        {"source_in": 0, "source_out": 10}, 48) is None
    assert axi._read_inclusive_source_end(
        {"source_in": None, "source_out": None}, 48) is None

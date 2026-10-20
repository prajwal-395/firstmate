"""`track.delete` may not take editorial work with it.

Resolve's ``DeleteTrack`` destroys every clip on the row, with no undo
of its own.  A row removal that carried the clips with it would destroy
work the request never named, which is the one thing a qualification
row must be able to say it does not do.

The defect this closes: ``track.delete`` wrote straight to
``DeleteTrack``, so a patch that removed a row the captain was still
editing on took the edits with it.
"""

from __future__ import annotations

import pytest

from library.tools import edit_algebra
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeTimeline,
    FakeTimelineItem,
)


def _item(name: str = "a.mp4") -> FakeTimelineItem:
    pool = FakeMediaPoolItem(name)
    pool.SetClipProperty("File Path", f"/media/{name}")
    return FakeTimelineItem(name, None, start=0, duration=48,
                            pool_item=pool)


def _timeline(*items: FakeTimelineItem) -> FakeTimeline:
    return FakeTimeline("qual", video=[("Video 1", list(items))])


def _delete(timeline, index: int = 1):
    operation = edit_algebra.get("track.delete")
    return operation.apply(
        {"op": "track.delete", "track_type": "video", "track_index": index},
        timeline, None)


def test_a_row_carrying_clips_is_refused_and_the_clips_survive():
    timeline = _timeline(_item())
    with pytest.raises(RuntimeError) as refusal:
        _delete(timeline)
    assert "carries 1 clip" in str(refusal.value)
    assert timeline.GetTrackCount("video") == 1
    assert len(timeline.GetItemListInTrack("video", 1)) == 1


def test_an_empty_row_is_removed():
    timeline = _timeline()
    assert _delete(timeline) is True
    assert timeline.GetTrackCount("video") == 0


def test_the_refusal_names_the_work_it_would_have_destroyed():
    """A refusal that does not say what it protected is not an answer."""
    timeline = _timeline(_item("a.mp4"), _item("b.mp4"))
    with pytest.raises(RuntimeError) as refusal:
        _delete(timeline)
    message = str(refusal.value)
    assert "video1" in message
    assert "carries 2 clip(s)" in message
    assert len(timeline.GetItemListInTrack("video", 1)) == 2


def test_the_op_declares_the_boundary_in_its_note():
    note = edit_algebra.get("track.delete").note
    assert "carries clips" in note
    assert "REFUSED" in note

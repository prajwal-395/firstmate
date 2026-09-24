"""The caption swap moves re-rendered files onto timelines, verified.

A caption fix re-renders cards under new filenames; every Subtitles-row
placement of each old file, on every timeline that holds one, must point
at the new file instead. These tests drive the real `caption_swap`
against fake Resolve objects (the module duck-types the application,
so no Resolve is needed), pinning what the lane's throwaway `swap.py`
proved in production: one ReplaceClip per pool item swaps every
timeline at once, placements never move, and the run refuses while any
old file remains placed.
"""

from __future__ import annotations

import contextlib

import pytest

from library.tools import caption_swap
from library.tools.caption_swap import (
    CaptionSwapError,
    find_placements,
    swap_files,
    verify_files,
)


class FakePoolItem:
    """A media pool item playing one file, replaceable."""

    def __init__(self, path, refuse_replace=False):
        self._path = path
        self.replace_calls = []
        self._refuse = refuse_replace
        self._props = {}

    def GetClipProperty(self, name):
        if name == "File Path":
            return self._path
        return self._props.get(name, "")

    def SetClipProperty(self, name, value):
        self._props[name] = value
        return True

    def ReplaceClip(self, new_path):
        self.replace_calls.append(new_path)
        if self._refuse:
            return False
        self._path = new_path
        return True


class FakeItem:
    """A timeline item on a track, holding a pool item at fixed frames."""

    def __init__(self, pool_item, start, end, moves_on_swap=False):
        self._pool = pool_item
        self._start = start
        self._end = end
        self._moves = moves_on_swap

    def GetMediaPoolItem(self):
        return self._pool

    def GetStart(self):
        # A placement that moves under the swap: the read-back sees it.
        if self._moves and self._pool.replace_calls:
            return self._start + 1
        return self._start

    def GetEnd(self):
        return self._end


class FakeTimeline:
    def __init__(self, name, tracks):
        # tracks: {track_name: [items]}
        self._name = name
        self._tracks = dict(tracks)

    def GetName(self):
        return self._name

    def GetTrackCount(self, kind):
        assert kind == "video"
        return len(self._tracks)

    def GetTrackName(self, kind, index):
        return list(self._tracks)[index - 1]

    def GetItemListInTrack(self, kind, index):
        return list(self._tracks.values())[index - 1]


class FakeProject:
    def __init__(self, timelines):
        self._timelines = list(timelines)

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]


@pytest.fixture
def no_lease(monkeypatch):
    """The lease as a no-op: these tests pin the swap, not the lock."""
    monkeypatch.setattr(caption_swap, "resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())


def _project():
    """Two timelines sharing one pool item, plus a third holding its own."""
    shared = FakePoolItem("/seg/old_a.mov")
    own = FakePoolItem("/seg/old_a.mov")
    other = FakePoolItem("/seg/old_b.mov")
    reel1 = FakeTimeline("Reel 01", {
        "V1": [],
        "Subtitles": [FakeItem(shared, 100, 140),
                      FakeItem(other, 200, 240)],
    })
    reel2 = FakeTimeline("Reel 02", {
        "V1": [],
        "Subtitles": [FakeItem(shared, 300, 340)],
    })
    reel3 = FakeTimeline("Reel 03", {
        "V1": [],
        "Subtitles": [FakeItem(own, 400, 440)],
    })
    return FakeProject([reel1, reel2, reel3]), shared, own, other




def test_one_replace_per_pool_item_swaps_every_timeline(no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    project, shared, own, _other = _project()
    report = swap_files(project, {"/seg/old_a.mov": str(new)})
    assert shared.replace_calls == [str(new)]
    assert own.replace_calls == [str(new)]
    assert {row["timeline"] for row in report["swapped"]} == \
        {"Reel 01", "Reel 02", "Reel 03"}
    assert all(row["new"] == "new_a.mov" for row in report["swapped"])
    assert all((row["start"], row["end"]) in
               {(100, 140), (300, 340), (400, 440)} for row in report["swapped"])




def test_a_refused_replace_fails_naming_the_file(no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    bad = FakePoolItem("/seg/old_a.mov", refuse_replace=True)
    project = FakeProject([FakeTimeline("Reel 01", {
        "Subtitles": [FakeItem(bad, 100, 140)]})])
    with pytest.raises(CaptionSwapError, match="old_a.mov"):
        swap_files(project, {"/seg/old_a.mov": str(new)})


def test_a_moved_placement_fails_the_read_back(no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    pool = FakePoolItem("/seg/old_a.mov")
    project = FakeProject([FakeTimeline("Reel 01", {
        "Subtitles": [FakeItem(pool, 100, 140, moves_on_swap=True)]})])
    with pytest.raises(CaptionSwapError, match="moved"):
        swap_files(project, {"/seg/old_a.mov": str(new)})


def test_a_missing_new_file_refuses_before_anything_moves(no_lease):
    project, shared, _own, _other = _project()
    with pytest.raises(CaptionSwapError, match="no new file"):
        swap_files(project, {"/seg/old_a.mov": "/seg/absent.mov"})
    assert shared.replace_calls == []



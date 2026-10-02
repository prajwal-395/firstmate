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
    swap_files,
)
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeProject,
    FakeTimeline,
    FakeTimelineItem,
)


def _pool(path):
    """A media pool item playing one file, replaceable."""
    pool = FakeMediaPoolItem(path.rsplit("/", 1)[-1])
    pool.SetClipProperty("File Path", path)
    return pool


def _placed(pool, start, end, left_offset=0):
    """A Subtitles-row placement of ``pool`` at fixed frames."""
    return FakeTimelineItem(pool.GetName(), None, start=start,
                            duration=end - start, left_offset=left_offset,
                            pool_item=pool)


def _timeline(name, rows):
    """``rows`` is ``{track_name: [items]}``, in track order."""
    return FakeTimeline(name, video=list(rows.items()))


@pytest.fixture
def no_lease(monkeypatch):
    """The lease as a no-op: these tests pin the swap, not the lock."""
    monkeypatch.setattr(caption_swap, "resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())


def _project():
    """Two timelines sharing one pool item, plus a third holding its own."""
    shared = _pool("/seg/old_a.mov")
    own = _pool("/seg/old_a.mov")
    other = _pool("/seg/old_b.mov")
    reel1 = _timeline("Reel 01", {
        "V1": [],
        "Subtitles": [_placed(shared, 100, 140),
                      _placed(other, 200, 240)],
    })
    reel2 = _timeline("Reel 02", {
        "V1": [],
        "Subtitles": [_placed(shared, 300, 340)],
    })
    reel3 = _timeline("Reel 03", {
        "V1": [],
        "Subtitles": [_placed(own, 400, 440)],
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


def test_rerender_swap_preserves_caption_item_source_trim(no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    pool = _pool("/seg/old_a.mov")
    placement = _placed(pool, 100, 140, left_offset=12)
    project = FakeProject([_timeline("Reel 01", {
        "Subtitles": [placement],
    })])

    report = swap_files(project, {"/seg/old_a.mov": str(new)})

    assert placement.GetLeftOffset() == 12
    assert report["swapped"][0]["left_offset"] == 12


def test_rerender_swap_refuses_a_caption_with_unreadable_source_trim(
        no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    pool = _pool("/seg/old_a.mov")
    project = FakeProject([_timeline("Reel 01", {
        "Subtitles": [_placed(pool, 100, 140, left_offset=None)],
    })])

    with pytest.raises(CaptionSwapError, match="no readable source trim"):
        swap_files(project, {"/seg/old_a.mov": str(new)})
    assert pool.replace_calls == []


def test_a_refused_replace_fails_naming_the_file(no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    bad = _pool("/seg/old_a.mov")
    bad.refuse_replace = True
    project = FakeProject([_timeline("Reel 01", {
        "Subtitles": [_placed(bad, 100, 140)]})])
    with pytest.raises(CaptionSwapError, match="old_a.mov"):
        swap_files(project, {"/seg/old_a.mov": str(new)})


def test_a_moved_placement_fails_the_read_back(no_lease, tmp_path):
    new = tmp_path / "new_a.mov"
    new.write_bytes(b"x")
    pool = _pool("/seg/old_a.mov")
    placement = _placed(pool, 100, 140)
    replace = pool.ReplaceClip

    def replace_and_move(path):
        # A placement that moves under the swap: the read-back sees it.
        placement._start += 1
        return replace(path)

    pool.ReplaceClip = replace_and_move
    project = FakeProject([_timeline("Reel 01", {
        "Subtitles": [placement]})])
    with pytest.raises(CaptionSwapError, match="moved"):
        swap_files(project, {"/seg/old_a.mov": str(new)})


def test_a_missing_new_file_refuses_before_anything_moves(no_lease):
    project, shared, _own, _other = _project()
    with pytest.raises(CaptionSwapError, match="no new file"):
        swap_files(project, {"/seg/old_a.mov": "/seg/absent.mov"})
    assert shared.replace_calls == []


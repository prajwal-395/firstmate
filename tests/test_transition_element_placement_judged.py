"""The transition-element append is judged, at placement time.

`build_reel_timeline` used to discard the return of the transition
`AppendToTimeline`, so an element Resolve declined to place left no
trace until F18 graded placed-against-planned length a whole stage
later. The placement now raises naming the element - first where the
append returns nothing (the overlay path's shape at
`reel_build.py:5045-5060`), then where it returns a truthy zombie
handle and the track disagrees (`composed_edit` step 6).

Stub-testable without Resolve: the pool and the timeline are fakes,
and `assert_current_timeline` is patched out - the lease is a
separate judgement this test does not exercise.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from library.tools import reel_build as rb
from library.tools.reel_build import ReelBuildError, _place_transition_element

ELEMENT = "/project/brand_assets/transition.mov"
FPS = 24000 / 1001


class _PoolItem:
    def GetClipProperty(self, _key):
        return "30"


def _placement(**overrides):
    base = {"element_path": ELEMENT, "element_seconds": 1.5,
            "record_frame": 100, "duration_frames": 36}
    base.update(overrides)
    return SimpleNamespace(**base)


class _Pool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def AppendToTimeline(self, items):
        self.calls.append(items)
        return self.result


class _TimelineItem:
    def __init__(self, start, end):
        self._start = start
        self._end = end

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end


class _Timeline:
    def __init__(self, items):
        self._items = list(items)

    def GetItemListInTrack(self, _media_type, _index):
        return list(self._items)


def _place(pool, timeline, placement=None):
    with patch.object(rb, "assert_current_timeline", lambda *a: None):
        return _place_transition_element(
            pool, object(), timeline, "Reel 01",
            placement or _placement(), _PoolItem(), 4, FPS)


def test_a_declined_append_raises_naming_the_element():
    pool = _Pool(None)
    with pytest.raises(ReelBuildError, match=re.escape(ELEMENT)):
        _place(pool, _Timeline([]))
    assert pool.calls, "the element was never offered to Resolve at all"


def test_a_zombie_handle_loses_to_the_track_and_names_the_element():
    pool = _Pool([object()])
    with pytest.raises(ReelBuildError, match=re.escape(ELEMENT)):
        _place(pool, _Timeline([]))


def test_a_placed_element_passes_and_sends_no_media_type():
    pool = _Pool([object()])
    timeline = _Timeline([_TimelineItem(100, 136)])
    assert _place(pool, timeline) is None
    sent = pool.calls[0][0]
    assert "mediaType" not in sent, (
        "transition elements may carry intentional audio - mediaType "
        "is the transition owner's call, not this judgement's")
    assert sent["recordFrame"] == 100
    assert sent["trackIndex"] == 4


def test_a_wrong_length_item_on_the_track_still_refuses():
    pool = _Pool([object()])
    timeline = _Timeline([_TimelineItem(100, 135)])
    with pytest.raises(ReelBuildError, match=re.escape(ELEMENT)):
        _place(pool, timeline)

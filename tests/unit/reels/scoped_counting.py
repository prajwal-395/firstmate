"""Count what an edit does to Resolve, so a scoped change can be compared.

PERF-003 asks for four numbers: assets re-rendered, tracks rescanned,
Resolve writes, and wall time.  These counters measure the first three
from the fake Resolve double, so the numbers are the engine's own
rather than an estimate.

The counters are deliberately SEPARATE, because they are different
denominators and adding them together would be a category error:

- ``writes`` - a mutating Resolve call on a timeline.
- ``scans`` - a read-back that compares the live timeline with the plan.
- ``cursor_moves`` - the project moving the cursor.  The engine counts
  this as a write (`library/tools/resolve_lock.py`), and it is counted
  apart from the content writes because it is not a change to the reel.
- ``renders`` - a caption card's file was written to disk.

A count nobody took is ``None``, never zero: zero is a measurement, and
confusing "nobody measured" with "there was nothing" is how a saving
that never happened gets reported as one.
"""

from __future__ import annotations

from typing import Any

#: The mutating Resolve calls a build makes.  A read is not a write.
WRITE_METHODS = frozenset({
    "AppendToTimeline", "DeleteClips", "AddTrack", "DeleteTrack",
    "SetTrackName", "SetSpeed", "SetProperty", "SetPropertyV2",
    "SetCDL", "AddMarker", "DeleteMarker", "SetMarker", "ImportMedia",
    "SetClipEnabled", "SetTransform", "AddFusionComp",
    "DeleteFusionComp", "SetFusionComp", "SplitAtFrame",
    "SetMarkerColor", "SetMarkerDuration", "SetClipProperty",
})

#: The read-backs a build makes to compare the live timeline with the
#: plan.  These are the "tracks rescanned" of the brief.
SCAN_METHODS = frozenset({
    "GetItemListInTrack", "GetTrackCount", "GetTrackName",
    "GetStartFrame", "GetEndFrame", "GetSelectedTrack",
})


class Counter:
    """The four numbers, and where they came from."""

    def __init__(self) -> None:
        self.writes = 0
        self.scans = 0
        self.cursor_moves = 0
        self.renders = 0
        self.write_trace: list[str] = []
        self.scan_trace: list[str] = []
        #: The mutating calls a timeline ITEM took, counted apart from
        #: the timeline's own writes: a speed set on a clip is a
        #: different write from a placement, and the two are never
        #: added together.
        self.item_writes = 0
        self.item_write_trace: list[str] = []

    def as_dict(self) -> dict[str, Any]:
        return {
            "writes": self.writes,
            "scans": self.scans,
            "cursor_moves": self.cursor_moves,
            "renders": self.renders,
            "item_writes": self.item_writes,
        }

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (f"Counter(writes={self.writes}, scans={self.scans}, "
                f"cursor_moves={self.cursor_moves}, "
                f"renders={self.renders}, "
                f"item_writes={self.item_writes})")


class CountingTimeline:
    """A fake timeline that counts what was done to it.

    A DUPLICATE of a counted timeline is itself counted, so the staging
    copy the scoped path patches is measured rather than the approved
    timeline it starts from.
    """

    def __init__(self, inner, counter: Counter | None = None) -> None:
        self._inner = inner
        self.counter = counter if counter is not None else Counter()

    def __getattr__(self, name):
        attribute = getattr(self._inner, name)
        if name == "DuplicateTimeline":
            def duplicate(timeline_name):
                twin = attribute(timeline_name)
                return CountingTimeline(twin, self.counter)
            return duplicate
        if not callable(attribute):
            return attribute

        def counted(*args, **kwargs):
            if name in WRITE_METHODS:
                self.counter.writes += 1
                self.counter.write_trace.append(name)
            elif name in SCAN_METHODS:
                self.counter.scans += 1
                self.counter.scan_trace.append(name)
            return attribute(*args, **kwargs)

        return counted


class CountingProject:
    """A fake project that counts the cursor moves and hands out
    counted timelines.

    The project only ever MOVES THE CURSOR, which the engine counts as
    a write, and it is counted apart from the content writes: a cursor
    move and a card replacement are different work.
    """

    def __init__(self, inner, counter: Counter | None = None) -> None:
        self._inner = inner
        self.counter = counter if counter is not None else Counter()
        self.final_name = ""

    def __getattr__(self, name):
        attribute = getattr(self._inner, name)
        if name == "GetTimelineByIndex":
            def by_index(index):
                timeline = attribute(index)
                if (timeline is not None and self.final_name
                        and timeline.GetName() == self.final_name):
                    return CountingTimeline(timeline, self.counter)
                return timeline
            return by_index
        if name == "GetMediaPool":
            def media_pool():
                return CountingPool(attribute(), self.counter)
            return media_pool
        if not callable(attribute):
            return attribute

        def counted(*args, **kwargs):
            if name == "SetCurrentTimeline":
                self.counter.cursor_moves += 1
            return attribute(*args, **kwargs)

        return counted


class CountingPool:
    """A fake media pool that hands out a COUNTED timeline.

    The build creates its timeline through the pool, so this is where
    a bulk build's writes become visible.
    """

    def __init__(self, inner, counter: Counter | None = None) -> None:
        self._inner = inner
        self.counter = counter if counter is not None else Counter()

    def __getattr__(self, name):
        attribute = getattr(self._inner, name)
        if name == "CreateEmptyTimeline":
            def create(timeline_name):
                timeline = attribute(timeline_name)
                return CountingTimeline(timeline, self.counter)
            return create
        if not callable(attribute):
            return attribute

        def counted(*args, **kwargs):
            if name in WRITE_METHODS:
                self.counter.writes += 1
                self.counter.write_trace.append(name)
            return attribute(*args, **kwargs)

        return counted

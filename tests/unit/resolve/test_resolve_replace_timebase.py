"""A re-place is judged in the timebase it lands in.

A SOURCE span is counted in the MEDIA's frames and a RECORD span in the
TIMELINE's.  The two are the same number only when the two frame rates
agree, and the verification of a re-place used to demand that they be
equal.

Measured 2026-10-21 on the product-format scratch (1080x1920 at
23.976) with 24fps media:

* the SAME 96-frame clip occupies 96 record frames on a 24fps timeline
  and 95 on the 23.976 one (the 4.0 seconds of the media are 95.904
  frames on the 23.976 timeline, and Resolve truncates);
* a 73-frame trim of it occupies 72 record frames - and the trim
  refused its own success: "the re-place returned an item that did not
  verify at the requested source range and record frame", with the
  restoration of the pre-trim snapshot failing the same way (the live
  reel.opening qualification).

The source range and the record frame are still verified exactly; only
the derived record SPAN is read in the timeline's timebase.

These tests name the defect and hold the fix in place.
"""

from __future__ import annotations

from library.tools import resolve_axi_core_commands as cmds


class _Item:
    """One timeline item, as the AXI reads it."""

    def __init__(self, *, uid, name="clip.mp4", record_in=0, record_out=0,
                 source_in=0, source_out=0):
        self._uid = uid
        self._name = name
        self._record_in = record_in
        self._record_out = record_out
        self._source_in = source_in
        self._source_out = source_out

    def GetUniqueId(self):
        return self._uid

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

    def GetMediaPoolItem(self):
        return None


class _PoolItem:
    """The media-pool clip a re-place is placed from."""

    def __init__(self, fps="24"):
        self._fps = fps

    def GetClipProperty(self, name):
        if name == "FPS":
            return self._fps
        return ""

    def GetUniqueId(self):
        return "pool-1"


class _Timeline:
    """A timeline that answers the two things a re-place asks of it."""

    def __init__(self, *, fps=23.976, items=()):
        self._fps = fps
        self._items = list(items)

    def GetSetting(self, name):
        if name == "timelineFrameRate":
            return self._fps
        return ""

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetItemListInTrack(self, kind, index):
        return list(self._items) if kind == "video" else []

    def DeleteClips(self, items, _follow):
        for item in items:
            if item in self._items:
                self._items.remove(item)
        return True


class _Pool:
    """The media pool, which places the re-placed item."""

    def __init__(self, placed):
        self._placed = placed
        self.payloads = []

    def AppendToTimeline(self, payloads):
        self.payloads.extend(payloads)
        return [self._placed]


# ── The conversion ───────────────────────────────────────────────────


def test_a_source_span_is_converted_into_the_timelines_frames():
    """The record span is the source span in the timeline's rate."""
    assert cmds._record_frames_for_source(
        96, timeline_fps=23.976, media_fps=24) == 95
    assert cmds._record_frames_for_source(
        73, timeline_fps=23.976, media_fps=24) == 72
    assert cmds._record_frames_for_source(
        95, timeline_fps=23.976, media_fps=24) == 94


def test_equal_rates_leave_the_span_alone():
    """A clip whose media rate IS the timeline's rate is unchanged."""
    assert cmds._record_frames_for_source(
        96, timeline_fps=24, media_fps=24) == 96
    assert cmds._record_frames_for_source(
        766, timeline_fps=23.976, media_fps=23.976) == 766


def test_an_unreadable_rate_leaves_the_span_alone():
    """A rate that cannot be read is not a rate; the old reading stays."""
    assert cmds._record_frames_for_source(
        73, timeline_fps=0.0, media_fps=24) == 73
    assert cmds._record_frames_for_source(
        73, timeline_fps=23.976, media_fps=0.0) == 73


def test_the_timebase_is_read_from_the_timeline_and_the_media():
    """The two rates are read from the live handles, not assumed."""
    timeline = _Timeline(fps=23.976)
    pool_item = _PoolItem(fps="24")
    assert cmds._placement_timebase(timeline, pool_item) == (23.976, 24.0)


def test_an_unreadable_rate_is_zero_and_not_an_exception():
    """A handle that refuses the read leaves the old reading in place."""

    class _Broken:
        def GetSetting(self, _name):
            raise RuntimeError("no settings here")

        def GetClipProperty(self, _name):
            raise RuntimeError("no properties here")

    assert cmds._placement_timebase(_Broken(), _Broken()) == (0.0, 0.0)


# ── The re-place that has to verify ─────────────────────────────────


def test_a_replaced_clip_verifies_in_a_timeline_the_media_rate_differs_from():
    """Catches: the re-place judged by the EQUALITY of the source span
    and the record span, so a trim of a clip whose media rate differs
    from the timeline's refused its own success ("the re-place returned
    an item that did not verify at the requested source range and record
    frame", measured 2026-10-21 on the product-format scratch).
    """
    # The placed clip: 73 media frames of 24fps media, which is 72
    # frames of a 23.976 timeline.
    placed = _Item(uid="new-1", record_in=0, record_out=72,
                   source_in=0, source_out=72)
    timeline = _Timeline(fps=23.976, items=[placed])
    pool = _Pool(placed)

    replacement, error = cmds._replace_item(
        timeline, pool, None, 0, 72, 0, 1, 1,
        pool_item=_PoolItem(fps="24"))

    assert error == ""
    assert replacement is placed
    assert pool.payloads[0]["endFrame"] == 73
    assert pool.payloads[0]["recordFrame"] == 0


def test_a_replaced_clip_still_refuses_when_its_source_range_is_wrong():
    """Catches: the conversion loosening the check into accepting
    anything.  The SOURCE range and the record frame are still exact."""
    placed = _Item(uid="new-1", record_in=0, record_out=72,
                   source_in=0, source_out=71)
    timeline = _Timeline(fps=23.976, items=[placed])
    pool = _Pool(placed)

    replacement, error = cmds._replace_item(
        timeline, pool, None, 0, 72, 0, 1, 1,
        pool_item=_PoolItem(fps="24"))

    assert replacement is None
    assert "did not verify" in error


def test_a_replaced_clip_still_refuses_at_the_wrong_record_frame():
    """Catches: the record frame read as approximate."""
    placed = _Item(uid="new-1", record_in=8, record_out=80,
                   source_in=0, source_out=72)
    timeline = _Timeline(fps=23.976, items=[placed])
    pool = _Pool(placed)

    replacement, error = cmds._replace_item(
        timeline, pool, None, 0, 72, 0, 1, 1,
        pool_item=_PoolItem(fps="24"))

    assert replacement is None
    assert "did not verify" in error


def test_a_replaced_clip_verifies_on_a_timeline_of_the_media_rate():
    """The equal-rate case is untouched by the fix."""
    placed = _Item(uid="new-1", record_in=0, record_out=73,
                   source_in=0, source_out=72)
    timeline = _Timeline(fps=24, items=[placed])
    pool = _Pool(placed)

    replacement, error = cmds._replace_item(
        timeline, pool, None, 0, 72, 0, 1, 1,
        pool_item=_PoolItem(fps="24"))

    assert error == ""
    assert replacement is placed

"""A promotion says what it is about to do to the captain's words.

Reel 09, 2026-09-09: three typed markers, a rebuild, and
`marker_feedback show` reporting "0 note(s)". They were not cleared -
promotion replaced the timeline object and they went with it, and
nothing said so. These fakes stand in for Resolve; the API surface
they answer is the one `promote_staged_reels` drives.
"""

import pytest

from library.tools import marker_carry


class _Pool:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else ""


class _Item:
    def __init__(self, start, end, path, left=0):
        self._start, self._end = start, end
        self._pool = _Pool(path) if path else None
        self._left = left

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetLeftOffset(self):
        return self._left

    def GetMediaPoolItem(self):
        return self._pool


class _Timeline:
    def __init__(self, rows, markers=None, start=0, decline=()):
        self._rows = rows
        self._markers = dict(markers or {})
        self._start = start
        self._decline = set(decline)
        self.added = []

    def GetStartFrame(self):
        return self._start

    def GetTrackCount(self, media):
        return len(self._rows) if media == "video" else 0

    def GetTrackName(self, media, index):
        return self._rows[index - 1][0]

    def GetItemListInTrack(self, media, index):
        return self._rows[index - 1][1]

    def GetMarkers(self):
        return self._markers

    def AddMarker(self, frame, color, name, note, duration, custom):
        if frame in self._decline:
            return False
        self.added.append((frame, color, name, note, duration, custom))
        return True


def _retiring():
    """The captain's Reel 13: Akshita on V1, Craig on V2, his Blue
    `feedback` marker at 1909 - the frame he typed it on."""
    return _Timeline(
        [("Akshita", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)]),
         ("Craig", [_Item(1909, 1921, "/f/LCATL0013.MXF", left=50)])],
        markers={1700: {"color": "Blue", "name": "feedback",
                        "note": "it cuts to craig here at the end",
                        "duration": 1, "customData": "cd"}})


def test_the_anchor_is_the_picture_not_the_frame_number():
    timeline = _retiring()
    assert marker_carry.picture_at(timeline, 1700) == (
        "/f/LC4932.MXF", 1000 + (1700 - 1599))
    # The topmost row wins - what the viewer is actually looking at.
    stacked = _Timeline(
        [("under", [_Item(0, 100, "/f/a.mov")]),
         ("over", [_Item(0, 100, "/f/b.mov")])])
    assert marker_carry.picture_at(stacked, 10)[0] == "/f/b.mov"


def test_a_marker_carries_to_the_frame_showing_the_same_picture():
    retiring = _retiring()
    notes = marker_carry.read_markers(retiring, "Reel 13")
    assert len(notes) == 1 and notes[0]["note"].startswith("it cuts")
    # The rebuild moved everything twenty frames earlier.
    replacement = _Timeline(
        [("Akshita", [_Item(1579, 1889, "/f/LC4932.MXF", left=1000)])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not uncarried and len(carried) == 1
    assert carried[0]["to_frame"] == 1680
    failed = marker_carry.place(replacement, carried)
    assert not failed
    assert replacement.added == [
        (1680, "Blue", "feedback", "it cuts to craig here at the end", 1,
         "cd")]


def test_a_marker_whose_picture_is_gone_is_named_not_dropped(capsys):
    """The whole point. A note on a shot the rebuild removed is
    REPORTED, with the captain's words, and left uncarried."""
    retiring = _Timeline(
        [("Craig", [_Item(1909, 1921, "/f/LCATL0013.MXF", left=50)])],
        markers={1915: {"color": "Blue", "name": "feedback",
                        "note": "this cut is jarring", "duration": 1}})
    notes = marker_carry.read_markers(retiring, "Reel 13")
    replacement = _Timeline(
        [("Akshita", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not carried and len(uncarried) == 1
    marker_carry.report("Reel 13", carried, uncarried)
    err = capsys.readouterr().err
    assert "MARKER NOT CARRIED" in err
    assert "this cut is jarring" in err


def test_a_marker_over_nothing_says_which_absence_it_is():
    retiring = _Timeline(
        [("Akshita", [_Item(0, 10, "/f/a.mov")])],
        markers={500: {"color": "Green", "name": "reply:", "note": "n",
                       "duration": 1}})
    notes = marker_carry.read_markers(retiring, "Reel 13")
    assert notes[0]["anchor"] is None
    _, uncarried = marker_carry.plan_carry(
        notes, _Timeline([("A", [_Item(0, 10, "/f/a.mov")])]))
    assert "nothing was playing under it" in uncarried[0]["why"]


def test_the_nearest_saying_wins_when_a_shot_repeats():
    """A reel that plays one source twice has not moved the captain's
    note to the other saying of it."""
    retiring = _Timeline(
        [("A", [_Item(100, 200, "/f/a.mov", left=0)])],
        markers={150: {"color": "Blue", "name": "n", "note": "x",
                       "duration": 1}})
    notes = marker_carry.read_markers(retiring, "Reel 13")
    replacement = _Timeline([("A", [
        _Item(0, 100, "/f/a.mov", left=0),
        _Item(140, 240, "/f/a.mov", left=0)])])
    carried, _ = marker_carry.plan_carry(notes, replacement)
    # Source frame 50 plays at reel 50 and at reel 190; 190 is nearer
    # the marker's own 150.
    assert carried[0]["to_frame"] == 190


def test_a_marker_resolve_declines_is_named_rather_than_assumed(capsys):
    retiring = _retiring()
    notes = marker_carry.read_markers(retiring, "Reel 13")
    replacement = _Timeline(
        [("Akshita", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)])],
        decline={1700})
    carried, _ = marker_carry.plan_carry(notes, replacement)
    failed = marker_carry.place(replacement, carried)
    assert len(failed) == 1
    assert "MARKER NOT CARRIED" in capsys.readouterr().err


def test_an_unreadable_timeline_refuses_rather_than_reporting_none():
    class _Broken(_Timeline):
        def GetMarkers(self):
            raise RuntimeError("Resolve said no")

    with pytest.raises(marker_carry.MarkerCarryUnreadable,
                       match="could not be read"):
        marker_carry.read_markers(_Broken([("A", [])]), "Reel 13")


def test_a_timeline_with_no_markers_reports_nothing(capsys):
    timeline = _Timeline([("A", [_Item(0, 10, "/f/a.mov")])])
    assert marker_carry.read_markers(timeline, "R") == []
    marker_carry.report("R", [], [])
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""

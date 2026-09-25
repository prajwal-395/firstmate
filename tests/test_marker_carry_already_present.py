"""A marker the staged duplicate already carries is not reported lost.

2026-09-25, the 30-reel post-header touch: Resolve's DuplicateTimeline
keeps every marker, the carry then re-adds each one, Resolve declines a
second marker at the same frame, and 34 "NOT CARRIED" lines read as the
captain's notes being lost while every one was present exactly once.
A DIFFERENT marker at that frame must still be reported.
"""

from library.tools import marker_carry


class _Target:
    def __init__(self, existing):
        self._existing = existing

    def GetStartFrame(self):
        return 0

    def AddMarker(self, *args):
        return False  # a frame that already carries a marker

    def GetMarkers(self):
        return self._existing


def _marker(note="fix the fluff here"):
    return {"to_frame": 523, "to_source_frame": 40, "color": "Blue",
            "name": "feedback", "note": note, "duration": 1}


def test_an_identical_marker_already_there_is_not_a_loss():
    there = {523: {"color": "Blue", "name": "feedback",
                   "note": "fix the fluff here"}}
    assert marker_carry.place(_Target(there), [_marker()]) == []


def test_a_different_marker_at_that_frame_is_still_reported():
    there = {523: {"color": "Green", "name": "rebuild (firstmate)",
                   "note": "something else"}}
    assert marker_carry.place(_Target(there), [_marker()]) == [_marker()]

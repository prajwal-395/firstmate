"""The freeze hold keeps its own aim when there is no shot in front of it.

The defect this names
---------------------
A reel whose picture is a `full_frame_span` holds the span's own CARDS
on the picture row, plus the one footage clip the declared freeze
places. The hold IS the ending shot's last frame, so the build gives it
the ending shot's treatment: the transform and the grade are copied
from the item immediately BEFORE the hold on the row.

On a span reel that item is the span's last CARD - a graphic, not
footage - and the copy overwrote the punch-in the aim had just written
on the hold with the card's identity. The hold then played UNCROPPED
under the TV frame and left black inside the television's screen
(F12, scratch Reel 13: "LC4932.MXF leaves black inside the television's
screen: top 125.4px, bottom 565.8px").

With no footage in front there is nothing to inherit, and the hold
keeps the aim computed for it - which IS the shot's treatment: the same
subject, the same zoom.

What this file proves
---------------------
- with a CARD in front of the hold, the hold's transform is NOT
  overwritten;
- with the SHOT in front of it (a clip of the same source file), the
  inherit still happens exactly as before.
"""
from __future__ import annotations

from types import SimpleNamespace

from library.tools import reel_build

ROW = 1
HELD_FROM = "/master/LC4932.MXF"
CARD_FROM = "/cards/reel_13_span_52.mov"
REEL_START = 1912
HELD_SOURCE_FRAME = 12345


class _Pool:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, key):
        return self._path if key == "File Path" else ""


class _Item:
    """A timeline item: a card or the hold, with its media and span."""

    def __init__(self, path, start, end, source_frame=0, held=False):
        self._path = path
        self._start = start
        self._end = end
        self._source_frame = source_frame
        self._held = held
        self.properties = {"ZoomX": 2.1386, "ZoomY": 2.1386,
                           "Pan": 12.0, "Tilt": -30.0}
        self.written = {}

    def GetMediaPoolItem(self):
        return _Pool(self._path)

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetSourceStartFrame(self):
        return self._source_frame

    def GetSpeed(self):
        return {"Percentage": 0.0} if self._held else {"Percentage": 100.0}

    def GetProperty(self):
        return dict(self.properties)

    def SetProperty(self, key, value):
        self.properties[key] = value
        self.written[key] = value
        return True

    def CopyGrades(self, items):
        return True


class _Timeline:
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, kind, index):
        return list(self._items) if index == ROW else []


def _freeze():
    return SimpleNamespace(
        track_index=ROW, held_from=HELD_FROM,
        held_source_frame=HELD_SOURCE_FRAME,
        reel_start_frame=REEL_START, end_frame=REEL_START + 19,
        duration_frames=19)


def _track_plan():
    return SimpleNamespace(aroll_rows=lambda: [SimpleNamespace(index=ROW)])


def test_a_card_in_front_of_the_hold_leaves_the_holds_aim_alone():
    """THE DEFECT.  The item in front is the span's own card, so there
    is no shot to inherit from and the hold keeps the aim."""
    card = _Item(CARD_FROM, REEL_START - 40, REEL_START)
    hold = _Item(HELD_FROM, REEL_START, REEL_START + 19,
                 source_frame=HELD_SOURCE_FRAME, held=True)
    timeline = _Timeline([card, hold])

    record = reel_build._inherit_freeze_treatment(
        "Reel 13", timeline, _track_plan(), {"1": ROW}, _freeze())

    assert hold.written == {}, (
        "the card's transform was copied over the hold's aim")
    assert record.get("properties", {}).get("inherited") is False


def test_the_shot_in_front_of_the_hold_is_still_inherited(tmp_path):
    """The ordinary reel keeps its behaviour: the hold takes the shot's
    treatment."""
    shot = _Item(HELD_FROM, REEL_START - 40, REEL_START)
    hold = _Item(HELD_FROM, REEL_START, REEL_START + 19,
                 source_frame=HELD_SOURCE_FRAME, held=True)
    timeline = _Timeline([shot, hold])

    reel_build._inherit_freeze_treatment(
        "Reel 13", timeline, _track_plan(), {"1": ROW}, _freeze(),
        project_folder=str(tmp_path))

    assert hold.written.get("ZoomX") == 2.1386, (
        "the hold takes the shot's zoom")
    assert hold.written.get("Pan") == 12.0

"""The punch-in is aimed at the footage the reel PLAYS, never at the
cards that stand in for it.

The defect this names
---------------------
The reel-13 build on the Ren-owned scratch copy (2026-10-21) carried a
`full_frame_span`: the span IS the reel's picture for its whole body,
so `reel_build` suppresses the footage video and the picture row holds
the span's own cards (graphics) plus the one footage clip the declared
freeze places.

The punch-in aim zipped the row's TIMELINE ITEMS with the row's
PLACEMENTS **by position** (`reel_build._aim_picture`). The two lists
are not the same list on a span reel: the items are 53 (52 cards and
the declared hold) and the placements are the six footage clips the
span SUPPRESSED. The zip therefore

* aimed the FOOTAGE's punch-in at the first five CARDS - scaling
  full-frame graphics by 2.13 (F12 on five span cards), and
* never aimed the FOOTAGE at all - so the declared hold played
  UNCROPPED under the TV frame and left black inside the
  television's screen (F12 on the hold).

The pairing is now by IDENTITY: the item carrying a placement's media,
at that placement's record frame. An item no placement produced is
not footage this reel plays and is left alone.

What this file proves
---------------------
- a span reel's cards are NOT handed a shot's aim;
- the declared hold IS aimed, with its own placement;
- an ordinary reel keeps exactly the pairing it had (position for
  position), so nothing else moves.
"""
from __future__ import annotations

import os

from library.tools import reel_build


class _Item:
    """A timeline item that can say what media it carries."""

    def __init__(self, media, record_frame, start_frame=None):
        self._media = media
        self._record_frame = record_frame
        self._start_frame = start_frame

    def GetMediaPoolItem(self):
        item = self

        class _Pool:
            def GetClipProperty(self, key):
                return item._media if key == "File Path" else ""

        return _Pool()

    def GetStartFrame(self):
        if self._start_frame is None:
            raise TypeError("Resolve answers None for a native hold")
        return self._start_frame

    def GetStart(self):
        return self._record_frame

    def GetName(self):
        return os.path.basename(self._media or "")


class _Place:
    """A placement: the media it lays and the reel frame it starts at."""

    def __init__(self, media, record_frame):
        self._media = media
        self["snapped_record"] = record_frame

    @property
    def clip(self):
        class _Clip:
            source_file = self._media
            track_type = "video"

        return _Clip()


def _place(media, record_frame):
    class _Clip:
        source_file = media
        track_type = "video"

    return {"clip": _Clip(), "snapped_record": record_frame,
            "source_in": 0.0, "source_out": 1.0}


def test_a_span_reels_cards_are_never_handed_a_shots_aim():
    """THE DEFECT.  The row holds 52 span cards and the declared hold;
    the placements are the footage the span suppressed.  Pairing by
    position aimed the footage's punch-in at the first five cards and
    left the hold uncropped under the TV frame."""
    cards = [_Item("/cards/reel_13_span_%02d.mov" % index, frame)
             for index, frame in enumerate((0, 20, 46, 80, 114))]
    hold = _Item("/master/LC4932.MXF", 1912)
    places = [
        _place("/master/LC4932.MXF", 0),
        _place("/master/LC4932.MXF", 400),
        _place("/master/LC4932.MXF", 800),
        _place("/master/LC4932.MXF", 1200),
        _place("/master/LC4932.MXF", 1600),
        _place("/master/LC4932.MXF", 1912),
    ]

    pairs = reel_build.pair_row_items_to_places(cards + [hold], places)

    assert [place for _, place in pairs] == [places[-1]], (
        "only the hold is footage on this row; the cards are graphics "
        "and must carry no shot's aim")
    assert pairs[0][0] is hold


def test_an_ordinary_reel_keeps_its_position_for_position_pairing():
    """Nothing else moves: a row that IS its placements pairs in order."""
    items = [_Item("/master/a.MXF", 0), _Item("/master/b.MXF", 240)]
    places = [_place("/master/a.MXF", 0), _place("/master/b.MXF", 240)]

    pairs = reel_build.pair_row_items_to_places(items, places)

    assert [item for item, _ in pairs] == items
    assert [place for _, place in pairs] == places


def test_a_row_whose_identity_cannot_be_read_keeps_the_positional_reading():
    """A row that cannot say what it carries is its placements, in order
    - the reading every ordinary reel had."""
    class _Silent:
        def GetMediaPoolItem(self):
            return None

        def GetStartFrame(self):
            return 0

        def GetName(self):
            return ""

    items = [_Silent(), _Silent()]
    places = [_place("/master/a.MXF", 0), _place("/master/b.MXF", 240)]

    pairs = reel_build.pair_row_items_to_places(items, places)

    assert [item for item, _ in pairs] == items
    assert [place for _, place in pairs] == places


def test_the_declared_hold_is_paired_with_the_item_that_carries_it():
    """THE DEFECT (the third part).  The declared freeze hold is a
    NATIVE HOLD: Resolve answers None for its `GetStartFrame`, so a
    pairing that reads that frame alone never reaches the hold - and
    the hold, the one footage clip under the span, played UNCROPPED
    under the TV frame and left black inside the screen (F12).

    The record frame comes from `GetStart` when `GetStartFrame` is
    absent, and the hold is paired by it."""
    class _Hold:
        """A native hold: no `GetStartFrame`, and no media to name."""

        def __init__(self, record_frame):
            self._record_frame = record_frame

        def GetMediaPoolItem(self):
            return None

        def GetStartFrame(self):
            return None

        def GetStart(self):
            return self._record_frame

        def GetName(self):
            return ""

    card = _Item("/cards/reel_13_span_01.mov", 0)
    hold = _Hold(1912)
    place = _place("/master/LC4932.MXF", 1912)

    pairs = reel_build.pair_row_items_to_places([card, hold], [place])

    assert [item for item, _ in pairs] == [hold], (
        "the hold is the footage on this row; the card is a graphic")

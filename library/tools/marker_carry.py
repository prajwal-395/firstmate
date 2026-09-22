"""A promotion that is about to discard the captain's words says so.

The defect this closes
----------------------
Measured 2026-09-09 on Reel 09, restated 2026-09-11 on Reel 13.
Promotion REPLACES a timeline object, so the markers the captain typed
onto it go with the old one.  `marker_feedback show` then reports "0
note(s)", and the honest answer to "did the markers clear" is "they are
gone, but they were not cleared".

`library/tools/marker_resolution.py` exists precisely so a note is
never removed except on evidence - it records the captain's words
BEFORE any deletion and keeps a note it cannot verify.  Every one of
those guarantees held and every one was bypassed, because the rebuild
destroyed the markers through a different route entirely.  That is the
shape of the whole week's defects: something happened that should not
have, and nothing said so.

The scope of this module
------------------------
The MINIMUM, deliberately.  Whether markers should always be carried is
a live product question (`vep-promotion-destroys-captain-markers`): a
marker anchored to a frame in the old timeline may point at different
content in the new one, and re-attaching the captain's words to the
wrong moment is its own kind of lie.  That question stays open.

What is not in question is the silence.  So:

* every marker on the retiring timeline is READ before anything is
  renamed, with the picture it sits on;
* a marker whose anchor still resolves in the replacement is carried to
  the frame that shows the same picture;
* a marker whose anchor does not resolve is REPORTED BY NAME, with its
  words, and left uncarried.

Nothing is deleted here and nothing is guessed.

What "the anchor resolves" means
--------------------------------
Not the frame number - a rebuild moves every frame, which is why
`marker_resolution` already refuses to act on a moved frame.  The
anchor is the PICTURE under the marker: the source file the topmost
picture row is playing at that frame, and how far into that source
file the frame sits.  A marker resolves when the replacement plays the
same source frame of the same file somewhere, and it carries to that
frame.  Two frames of one source file may play twice in one reel; the
NEAREST candidate to the marker's original frame wins, because a reel
that repeats a shot has not moved the captain's note to the other
saying of it.

`tests/test_marker_carry.py`.
"""

from __future__ import annotations

import sys

from library.tools.resolve_lock import under_lease

#: Rows a marker anchor is read from, topmost picture first. Captions
#: and frame overlays run the length of a reel and would anchor every
#: marker to the same item, so the anchor is read off the PICTURE rows
#: only - which are also the rows a note is ever about.
_PICTURE_MEDIA = "video"


class MarkerCarryUnreadable(RuntimeError):
    """A timeline's markers or picture could not be read."""


def _picture_rows(timeline) -> list:
    """Every PICTURE row's items, in row order, as (index, name, items).

    Layer rows (captions, overlays, frames - `timeline_layout`'s own
    singleton names) are not picture: their artefacts are rebuilt with
    fresh content hashes on every build, so a marker anchored to one
    can never resolve in the replacement. Measured 2026-09-19 on Reel
    04: the pink verdict at frame 65 anchored to a motion-graphics
    overlay the rebuild re-rendered, and promotion reported it NOT
    CARRIED while the same Akshita source frame still plays at 65.
    Speaker and camera rows keep their names from the plan, so they
    are what an anchor can hold across a rebuild. Where filtering
    leaves no rows at all, every row is a picture row - the old
    behaviour, never a refusal to anchor.
    """
    from library.tools.timeline_layout import SINGLETON_NAMES

    rows = []
    count = timeline.GetTrackCount(_PICTURE_MEDIA) or 0
    for index in range(1, count + 1):
        name = timeline.GetTrackName(_PICTURE_MEDIA, index) or f"#{index}"
        items = timeline.GetItemListInTrack(_PICTURE_MEDIA, index) or []
        rows.append((index, name, items))
    picture = [row for row in rows if row[1] not in SINGLETON_NAMES]
    return picture or rows


def _source_path(item) -> str:
    pool_item = item.GetMediaPoolItem()
    if not pool_item:
        return ""
    return str(pool_item.GetClipProperty("File Path") or "")


def picture_at(timeline, frame: int, rows=None):
    """What plays at `frame`: `(source_file, source_frame)` or None.

    The TOPMOST picture row wins, which is what the viewer sees. Rows
    whose items carry no media pool item (generators, fusion
    compositions) answer nothing and the row below is asked.
    """
    start = timeline.GetStartFrame()
    absolute = start + int(frame)
    best = None
    for index, _name, items in (rows if rows is not None
                                else _picture_rows(timeline)):
        for item in items:
            if not (item.GetStart() <= absolute < item.GetEnd()):
                continue
            path = _source_path(item)
            if not path:
                continue
            source_frame = (int(item.GetLeftOffset() or 0)
                            + (absolute - item.GetStart()))
            best = (index, path, source_frame)
    if best is None:
        return None
    return (best[1], best[2])


def read_markers(timeline, timeline_name: str) -> list:
    """Every marker on a timeline, with the picture under it.

    Read BEFORE anything is renamed - the whole point of this module
    is that the captain's words are in hand before the object that
    carries them is replaced.
    """
    try:
        raw = timeline.GetMarkers() or {}
        rows = _picture_rows(timeline)
        out = []
        for frame in sorted(raw):
            marker = dict(raw[frame])
            out.append({
                "frame": int(frame),
                "color": marker.get("color", ""),
                "name": marker.get("name", ""),
                "note": marker.get("note", ""),
                "duration": int(marker.get("duration", 1) or 1),
                "custom_data": marker.get("customData", ""),
                "anchor": picture_at(timeline, int(frame), rows),
            })
        return out
    except Exception as unreadable:
        raise MarkerCarryUnreadable(
            f"the markers on {timeline_name!r} could not be read "
            f"({unreadable}); a promotion that cannot see the captain's "
            f"words must not proceed to replace them silently.") \
        from unreadable


def resolve_frame(marker: dict, timeline, rows=None):
    """Where this marker's picture plays in `timeline`, or None.

    The nearest match to the marker's original frame, for the reason
    the module docstring gives.
    """
    anchor = marker.get("anchor")
    if not anchor:
        return None
    path, source_frame = anchor
    start = timeline.GetStartFrame()
    best = None
    for _index, _name, items in (rows if rows is not None
                                 else _picture_rows(timeline)):
        for item in items:
            if _source_path(item) != path:
                continue
            left = int(item.GetLeftOffset() or 0)
            offset = source_frame - left
            if not (0 <= offset < item.GetEnd() - item.GetStart()):
                continue
            candidate = item.GetStart() + offset - start
            distance = abs(candidate - marker["frame"])
            if best is None or distance < best[0]:
                best = (distance, candidate)
    return None if best is None else best[1]


def plan_carry(markers, timeline) -> tuple:
    """Split markers into those that resolve here and those that do not.

    Returns `(carried, uncarried)`; neither list is written anywhere.
    A pure split so a caller may report before it acts, which is the
    order that matters: the report is the deliverable, the carry is
    the convenience.
    """
    rows = _picture_rows(timeline)
    carried, uncarried = [], []
    for marker in markers:
        frame = resolve_frame(marker, timeline, rows)
        if frame is None:
            reason = ("its anchor picture is in the replacement nowhere"
                      if marker.get("anchor")
                      else "nothing was playing under it to anchor to")
            uncarried.append({**marker, "why": reason})
        else:
            carried.append({**marker, "to_frame": int(frame)})
    return carried, uncarried


def report(timeline_name: str, carried, uncarried) -> None:
    """Say what is about to happen to the captain's words. Always.

    A promotion with nothing to carry prints nothing; one that carries
    or drops anything says which, by name and with the words, on
    stdout for the carried and stderr for the dropped - a note this
    build is about to lose is not an informational line.
    """
    for marker in carried:
        print(f"  Marker carried onto {timeline_name}: "
              f"{marker['color']} {marker['name']!r} @"
              f"{marker['frame']} -> @{marker['to_frame']}", flush=True)
    for marker in uncarried:
        print(f"  MARKER NOT CARRIED onto {timeline_name}: "
              f"{marker['color']} {marker['name']!r} @{marker['frame']} "
              f"- {marker['why']}. The captain wrote: "
              f"{marker['note'].strip()!r}",
              file=sys.stderr, flush=True)


@under_lease("carry the captain's markers onto the replacement")
def place(timeline, carried) -> list:
    """Write the resolved markers onto the replacement.

    A marker Resolve declines is returned in the failure list rather
    than assumed placed - AGENTS.md 5: judge a Resolve call by what it
    RETURNS. A frame that already carries a marker is one such
    decline, and saying so is the point.
    """
    failed = []
    start = timeline.GetStartFrame()
    for marker in carried:
        ok = timeline.AddMarker(
            start + int(marker["to_frame"]), marker["color"] or "Blue",
            marker["name"], marker["note"], marker["duration"],
            marker.get("custom_data") or "")
        if not ok:
            failed.append(marker)
            print(f"  MARKER NOT CARRIED: Resolve declined "
                  f"{marker['name']!r} at frame {marker['to_frame']} - "
                  f"the captain wrote: {marker['note'].strip()!r}",
                  file=sys.stderr, flush=True)
    return failed

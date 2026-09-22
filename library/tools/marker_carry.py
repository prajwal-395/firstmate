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
  words, and - at promotion time - PUT BACK as a Blue marker at the
  seam where its subject was cut out, with our own reply BESIDE it,
  never instead of it.

Nothing is deleted here and nothing is guessed onto similar-looking
material. The seam is not a re-anchor: it is the join the cut actually
made - the frame right after the surviving content that played just
before the note - derived from the retiring and replacement picture
rows, never from similarity. Where even the seam is ambiguous (nothing
before the cut survives), the note goes at the start of the replacing
item and the reply says so.

The DATASTORE `marker_feedback` pull format stays the durable standard
for any pre-promotion capture on disk: per note, the CLIPS it spans
with source file and source frame range. No new capture is added here -
the seam is derived from the same live pre-rename read `plan_carry`
uses - so there is no weaker snapshot for anyone to mistake: the
resolve-axi "markers snapshot" restores the timeline plane only (no
clip anchors, no source ranges), and ad-hoc text captures keep the
WORDS but not the ANCHOR. Neither can derive a seam.

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


def _rows_span(rows, start: int) -> int:
    """How many timeline-relative frames the picture rows cover."""
    end = start
    for _index, _name, items in rows:
        for item in items or []:
            try:
                if int(item.GetEnd()) > end:
                    end = int(item.GetEnd())
            except Exception:                           # noqa: BLE001
                continue
    return max(0, end - start)


def _exact_frames(rows, start: int, path: str, source_frame: int,
                  ref: int) -> list:
    """Every relative frame playing exactly `(path, source_frame)`.

    Nearest to `ref` first - a reel that repeats a shot has not moved
    the captain's note to the other saying of it, the same reason
    `resolve_frame` prefers the nearest candidate.
    """
    out = []
    for _index, _name, items in rows:
        for item in items or []:
            if _source_path(item) != path:
                continue
            left = int(item.GetLeftOffset() or 0)
            offset = source_frame - left
            if not (0 <= offset < item.GetEnd() - item.GetStart()):
                continue
            out.append(item.GetStart() + offset - start)
    return sorted(out, key=lambda frame: abs(frame - ref))


def _anchor_span_desc(marker: dict, retiring, rows, start: int) -> str:
    """The source range the marker's span sat on, for humans.

    Scans the retiring picture across the marker's whole duration, so
    a duration-81 note reports the range it covered rather than the
    single source frame under its head. Falls back to the point anchor
    when the span plays no single file throughout.
    """
    import os as _os

    frame = int(marker["frame"])
    duration = max(1, int(marker.get("duration") or 1))
    seen: list = []
    for offset in range(duration):
        picture = picture_at(retiring, frame + offset, rows)
        if picture is None:
            return (f"{_os.path.basename(marker['anchor'][0])} source "
                    f"{marker['anchor'][1]}" if marker.get("anchor")
                    else "no anchored picture")
        if not seen or seen[-1] != picture:
            seen.append(picture)
    if len({path for path, _src in seen}) == 1:
        path = seen[0][0]
        first, last = seen[0][1], seen[-1][1]
        if last == first + len(seen) - 1:
            return (f"{_os.path.basename(path)} source "
                    f"{first}..{last + 1}")
    anchor = marker.get("anchor")
    if anchor:
        return (f"{_os.path.basename(anchor[0])} source {anchor[1]} "
                f"(marker span covers more than one source range)")
    return "no anchored picture"


def seam_for(marker: dict, retiring, replacement,
             rows_r=None, rows_n=None) -> dict | None:
    """Where an uncarried marker's Blue goes back: the seam, or None.

    The seam is the join the cut actually made: one past the
    replacement frame still playing the surviving content from just
    before the note. Both halves are live picture rows, never
    similarity - the marker is placed where its subject was removed,
    not guessed onto something that looks like it.

    Returns `{"seam", "ambiguous", "explanation", ...}`. `ambiguous`
    is True exactly when nothing before the cut survives, and then
    the seam is the start of the replacing item and the explanation
    says so. None when the replacement plays no picture at all -
    there is no seam on an empty timeline, and the caller reports
    that rather than placing nowhere.
    """
    frame = int(marker["frame"])
    duration = max(1, int(marker.get("duration") or 1))
    if rows_r is None:
        rows_r = _picture_rows(retiring)
    if rows_n is None:
        rows_n = _picture_rows(replacement)
    start_n = int(replacement.GetStartFrame())
    span_n = _rows_span(rows_n, start_n)
    if span_n <= 0:
        return None
    start_r = int(retiring.GetStartFrame())
    span_r = _rows_span(rows_r, start_r)
    subject = _anchor_span_desc(marker, retiring, rows_r, start_r)

    predecessor = None
    probe = frame - 1
    while probe >= 0:
        picture = picture_at(retiring, probe, rows_r)
        if picture is not None:
            path, source = picture
            hits = _exact_frames(rows_n, start_n, path, source, frame)
            if hits:
                predecessor = (probe, hits[0])
                break
        probe -= 1
    successor = None
    probe = frame + duration
    while probe < span_r:
        picture = picture_at(retiring, probe, rows_r)
        if picture is not None:
            path, source = picture
            hits = _exact_frames(rows_n, start_n, path, source, frame)
            if hits:
                successor = (probe, hits[0])
                break
        probe += 1

    if predecessor is not None:
        seam = min(predecessor[1] + 1, span_n - 1)
        return {
            "seam": int(seam),
            "ambiguous": False,
            "explanation":
                f"the cut removed {subject} the note sat on; the "
                f"content just before it still plays at {predecessor[1]}, "
                f"so the join is {seam}",
            "predecessor": predecessor[1],
            "successor": successor[1] if successor else None,
        }
    if successor is not None:
        return {
            "seam": int(successor[1]),
            "ambiguous": True,
            "explanation":
                f"the cut removed {subject} the note sat on and nothing "
                f"before it survives, so the note goes at "
                f"{successor[1]}, the start of the replacing item",
            "predecessor": None,
            "successor": successor[1],
        }
    want = min(max(frame, 0), span_n - 1)
    # The item the viewer SEES at `want` is the topmost picture row's:
    # `_picture_rows` runs bottom-first and `picture_at` lets the
    # later row win, so walk reversed (topmost first), first hit wins.
    head = want
    for _index, _name, items in reversed(rows_n):
        for item in items or []:
            item_start = int(item.GetStart()) - start_n
            item_end = int(item.GetEnd()) - start_n
            if item_start <= want < item_end:
                head = item_start
                break
        else:
            continue
        break
    return {
        "seam": int(head),
        "ambiguous": True,
        "explanation":
            f"the cut removed {subject} the note sat on and neither "
            f"side of it survives, so the note goes at {head}, the "
            f"start of the item now playing there",
        "predecessor": None,
        "successor": None,
    }


def plan_seams(uncarried, retiring, replacement) -> list:
    """One seam plan per uncarried marker, in marker order.

    Pure: reads both timelines, writes nothing. A plan whose seam is
    None names a replacement with no picture to hold it.
    """
    rows_r = _picture_rows(retiring)
    rows_n = _picture_rows(replacement)
    plans = []
    for marker in uncarried:
        plans.append({"marker": marker,
                      "plan": seam_for(marker, retiring, replacement,
                                       rows_r, rows_n)})
    return plans


def _reply_custom_data(note_text: str, summary: str) -> str:
    """Our reply's machine-readable mark, best effort, never fatal.

    A green reply that carries no writer record reads downstream as
    another question the captain typed (`feedback_poll` summons on
    unmarked notes). The envelope states the writer is us and quotes
    what it answers, so the question survives on the Blue beside it
    and this marker is never mistaken for his.
    """
    try:
        from library.tools import marker_feedback as _feedback
        return _feedback.reply_custom_data(
            answers_text=note_text, summary=summary)
    except Exception:                                   # noqa: BLE001
        return ""


def place_uncarried(timeline, plans, timeline_name: str = "") -> tuple:
    """Put each uncarried note back as Blue at its seam, reply beside.

    The Blue carries the captain's name, note, colour and custom data
    BYTE-IDENTICAL at duration 1 - a point at the join, never the old
    span over unrelated content. The colour is the marker's OWN, so a
    Blue comes back Blue; our reply is a separate Green marker at the
    first free frame beside it, stating the original frame, the seam
    derivation and whether the seam was ambiguous. A reply beside the
    Blue is the answer; a reply instead of it is the defect.

    Returns `(placed, declined)`; Resolve is judged by what it
    RETURNS, and a declined Blue is named on stderr with the captain's
    words. Never raises: a re-placement that cannot land is a report,
    not a promotion failure.
    """
    placed, declined = [], []
    start = int(timeline.GetStartFrame())
    rows = _picture_rows(timeline)
    span = _rows_span(rows, start)
    for entry in plans:
        marker = entry["marker"]
        plan = entry.get("plan")
        name = marker.get("name") or ""
        note = marker.get("note") or ""
        if plan is None:
            declined.append({**marker, "seam": None,
                             "why": "the replacement plays no picture - "
                                    "there is no seam to hold the note"})
            print(f"  MARKER NOT RE-PLACED onto {timeline_name}: "
                  f"{marker.get('color')} {name!r} @{marker['frame']} - "
                  f"the replacement plays no picture. The captain wrote: "
                  f"{note.strip()!r}", file=sys.stderr, flush=True)
            continue
        seam = int(plan["seam"])
        blue_ok = False
        refused_reason = ""
        try:
            blue_ok = bool(timeline.AddMarker(
                start + seam, marker.get("color") or "Blue",
                name, note, 1, marker.get("custom_data") or ""))
        except Exception as refused:                    # noqa: BLE001
            refused_reason = f"Resolve raised ({refused})"
        if not blue_ok and not refused_reason:
            refused_reason = ("Resolve declined the Blue at the seam - "
                              "an empty name, or a marker already there")
        if not blue_ok:
            declined.append({**marker, "seam": seam,
                             "why": refused_reason})
        if not blue_ok:
            print(f"  MARKER NOT RE-PLACED onto {timeline_name}: "
                  f"{marker.get('color')} {name!r} @{marker['frame']} - "
                  f"Resolve declined the Blue at seam {seam}. "
                  f"The captain wrote: {note.strip()!r}",
                  file=sys.stderr, flush=True)
        else:
            print(f"  Marker re-placed as Blue onto {timeline_name}: "
                  f"{marker.get('color')} {name!r} @{marker['frame']} "
                  f"-> @{seam} ({plan['explanation']})", flush=True)
        reply_name = f"re: {name}" if name else (
            f"re: note @{marker['frame']}")
        verdict = (f"the Blue is at {seam}"
                   if blue_ok else
                   f"the Blue at {seam} was DECLINED - the note's words "
                   f"are quoted here so they are not lost: {note!r}"
                   if note else
                   f"the Blue at {seam} was DECLINED")
        reply_note = (
            f"Your note @{marker['frame']} did not carry: {marker.get('why', '')} "
            f"{plan['explanation']}. {verdict} - this reply sits beside "
            f"your words, never instead of them."
            + (" (seam ambiguous: start of the replacing item.)"
               if plan["ambiguous"] else ""))
        custom = _reply_custom_data(
            "\n\n".join(part for part in (name, note) if part),
            f"re-placed uncarried marker @{marker['frame']} at seam {seam}")
        reply_frame = None
        if span > 0:
            offset = 1
            while offset < span:
                for candidate in (seam + offset, seam - offset):
                    if 0 <= candidate < span and candidate != seam:
                        try:
                            if timeline.AddMarker(
                                    start + candidate, "Green", reply_name,
                                    reply_note, 1, custom):
                                reply_frame = candidate
                                break
                        except Exception:               # noqa: BLE001
                            continue
                if reply_frame is not None:
                    break
                offset += 1
        if reply_frame is None:
            print(f"  REPLY NOT PLACED onto {timeline_name}: no free frame "
                  f"beside seam {seam} for {reply_name!r}",
                  file=sys.stderr, flush=True)
            if blue_ok:
                placed.append({**marker, "seam": seam,
                               "ambiguous": plan["ambiguous"],
                               "explanation": plan["explanation"],
                               "reply_frame": None})
        else:
            print(f"  Reply placed onto {timeline_name}: {reply_name!r} "
                  f"@{reply_frame} beside the re-placed Blue @{seam}",
                  flush=True)
            if blue_ok:
                placed.append({**marker, "seam": seam,
                               "ambiguous": plan["ambiguous"],
                               "explanation": plan["explanation"],
                               "reply_frame": reply_frame})
            else:
                declined[-1]["reply_frame"] = reply_frame
    return placed, declined

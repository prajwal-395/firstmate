"""A promotion that is about to discard the captain's words says so.

Promotion REPLACES a timeline object, so the markers the captain typed
onto it go with the old one.  `marker_resolution` guarantees a note is
never removed except on evidence, but a rebuild destroys markers by a
different route; this module is what makes that loss SAID.

The scope of this module
------------------------
The MINIMUM, deliberately.  Whether markers should always be carried is
a live product question (`vep-promotion-destroys-captain-markers`): a
marker anchored to a frame in the old timeline may point at different
content in the new one.  That question stays open.  What is not in
question is the silence.  So:

* every marker on the retiring timeline is READ before anything is
  renamed, with the picture it sits on (`read_markers`, `picture_at`);
* a marker whose anchor still resolves in the replacement is carried to
  the frame that shows the same picture (`plan_carry`, `place`);
* a marker whose anchor does not resolve is REPORTED BY NAME, with its
  words (`report`), and - at promotion time - PUT BACK as a Blue marker
  at the seam where its subject was cut out (`seam_for`, `plan_seams`,
  `place_uncarried`), with our own reply BESIDE it, never instead of it.

Nothing is deleted here and nothing is guessed onto similar-looking
material.  The seam is not a re-anchor: it is the join the cut actually
made - the frame right after the surviving content that played just
before the note - derived from the retiring and replacement picture
rows, never from similarity.  Where nothing before the cut survives,
the note goes at the start of the replacing item and the reply says so.

The seam is derived from the same live pre-rename read `plan_carry`
uses; no new capture is added.  The `marker_feedback` pull format stays
the durable standard for any pre-promotion capture on disk.  The
resolve-axi "markers snapshot" (timeline plane only, no clip anchors)
and ad-hoc text captures (words, no anchor) cannot derive a seam.

What "the anchor resolves" means
--------------------------------
Not the frame number - a rebuild moves every frame.  The anchor is the
PICTURE under the marker: the source file the topmost picture row is
playing at that frame, and how far into that file the frame sits
(`resolve_frame`).  A marker resolves when the replacement plays the
same source frame of the same file somewhere, and it carries to that
frame.  Where one source frame plays twice, the NEAREST candidate to
the marker's original frame wins.  A timeline whose markers or picture
cannot be read raises `MarkerCarryUnreadable`.

Clip-plane markers have their own pass (`read_clip_markers`,
`plan_clip_carry`, `report_clip`, `place_clip_markers`).

A reply is paired by IDENTITY, never by position
------------------------------------------------
* a note's WORDS (`name` + `note`, normalised for whitespace and case
  only - `feedback_ledger.durable_identity`) do not move when frames do;
* a note's PICTURE anchor is rebuild-invariant by the definition carry
  resolves by.

So a reply names its note by identity (`answers`) plus the anchor it
sat on (`answers_anchor`), and the carry re-pairs by id after the
rebuild regardless of where either marker moved.  A position delta is
rejected: anything pairing two markers by position decays under
rebuilds.  The limits:

* a note over NOTHING (a gap, a generator) has no anchor half, so a
  reply answering it pairs by words alone and is REPORTED weak;
* edited words are a NEW identity: the old reply then names nothing,
  and is reported unpaired rather than silently re-bound;
* two identical notes (same words, same anchor) bind nearest, first;
* `read_markers` never reads the CLIP plane, so clip-plane replies
  never appear in `audit_replies`; the clip pass carries those markers
  separately rather than pretending to pair them.

`tests/test_marker_carry.py` (including the already-present case) and
`tests/test_clip_marker_carry.py`.

The measurements and history behind these rules (Reel 09, Reel 13,
Reel 29): docs/evidence/marker_carry.md.
"""

from __future__ import annotations

import sys

from library.tools.resolve_lock import under_lease

#: Rows a marker anchor is read from, topmost picture first. Captions
#: and frame overlays run the length of a reel and would anchor every
#: marker to the same item, so the anchor is read off the PICTURE rows
#: only - which are also the rows a note is ever about.
_PICTURE_MEDIA = "video"

#: Where a re-paired reply lands relative to its note's new frame: the
#: adjacent frame, which is the reply convention the captain already
#: reads (Reel 14's green sat at 163 answering the blue at 162). Never
#: the note's own frame - Resolve holds one marker per frame and would
#: decline the write.
REPLY_TRACK_OFFSET = 1


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


def _note_text(marker: dict) -> str:
    """A marker's words, joined the way every reader joins them.

    The same precedence `marker_feedback._text_of` and
    `feedback_ledger.note_text` use, so one note read through any of
    the three yields one string - and one identity.
    """
    return "\n\n".join(
        part for part in (str(marker.get("name") or ""),
                          str(marker.get("note") or "")) if part)


def _ask_identity(marker: dict, timeline_name: str) -> str:
    """The durable identity of a note that is (presumably) the captain's.

    `feedback_ledger.durable_identity`: the reel's base name plus the
    normalised words, and nothing a rebuild moves.
    """
    from library.tools import feedback_ledger as _ledger

    return _ledger.durable_identity(timeline_name, _note_text(marker))


def _anchor_tuple(anchor) -> tuple | None:
    """A picture anchor as `(source_file, source_frame)`, or None.

    Accepts the tuple `read_markers` records and the
    `{"source_file", "source_frame"}` mapping a reply's `answers_anchor`
    carries, so the two halves of a pairing compare equal.
    """
    if anchor is None:
        return None
    if isinstance(anchor, dict):
        path, frame = anchor.get("source_file"), anchor.get("source_frame")
    else:
        try:
            path, frame = anchor[0], anchor[1]
        except (TypeError, IndexError, KeyError):
            return None
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(frame, int) or isinstance(frame, bool):
        return None
    return (path, int(frame))


def _reply_links(marker: dict) -> list:
    """Every reply record OUR writer left on this marker.

    `[]` for a marker carrying none - which is every note the captain
    typed, and every reply written before the record existed. Reading
    never refuses: a foreign or malformed `customData` is the payload
    module's `foreign`, not a reply of ours.
    """
    from library.tools import marker_feedback as _feedback

    try:
        return _feedback.reply_records_in(
            marker.get("custom_data") or "")
    except Exception:                               # noqa: BLE001
        return []


def _answered_identity(marker: dict) -> tuple:
    """What the reply's newest record names: `(answers, anchor, valid)`.

    The newest record carrying `answers` speaks: a rewrite replaces by
    id rather than appending, so the last statement is the current one.
    `valid` is the identity grammar (`feedback_ledger.is_identity`); an
    invalid `answers` is returned VERBATIM rather than dropped - the
    caller classifies it legacy and the report quotes it, which is what
    makes the old prose and frame locators visible instead of silently
    absorbed. `("", None, False)` when the marker names nothing at all.
    """
    from library.tools.feedback_ledger import is_identity

    answers, anchor, seen = "", None, False
    for record in _reply_links(marker):
        if not isinstance(record, dict):
            continue
        if record.get("answers"):
            answers, seen = str(record["answers"]), True
            raw_anchor = record.get("answers_anchor")
            anchor = (_anchor_tuple(raw_anchor)
                      if isinstance(raw_anchor, dict) else None)
    if not seen:
        return "", None, False
    if answers and is_identity(answers):
        return answers, anchor, True
    return answers, None, False


def _bind_reply(reply: dict, asks: list, identities: dict) -> tuple:
    """Which note a reply answers, by identity rather than position.

    Returns `(ask_or_None, flags)`. Candidates are the asks whose
    durable identity equals the reply's `answers`; among them the one
    whose picture anchor equals the reply's `answers_anchor` wins, else
    the nearest by original frame. Flags say how exact the bind is:
    `anchor_mismatch` (same words, different picture - bound nearest),
    `weak` (the reply recorded no anchor, so words alone decided),
    `legacy` (prose or a frame locator, from before the identity rule,
    with the raw value kept for the report), `unpaired` (a valid
    identity matching no note on this reel), `unclaimed` (no `answers`
    at all - a reply from before replies recorded what they answer).
    """
    answers, anchor, valid = _answered_identity(reply)
    if not valid:
        return None, ({"legacy", answers} if answers else {"unclaimed"})
    candidates = [a for a in asks if identities.get(a["frame"]) == answers]
    if not candidates:
        return None, {"unpaired"}
    if anchor is not None:
        exact = [a for a in candidates
                 if _anchor_tuple(a.get("anchor")) == anchor]
        pool, mismatch = (exact, False) if exact else (candidates, True)
    else:
        pool, mismatch = candidates, False
    ask = min(pool, key=lambda a: abs(a["frame"] - reply["frame"]))
    flags = set()
    if mismatch:
        flags.add("anchor_mismatch")
    if anchor is None:
        flags.add("weak")
    return ask, flags


def _timeline_span(timeline) -> int | None:
    """Frames on `timeline`, or None when it will not say.

    Best effort: a fake without `GetEndFrame` skips the past-the-end
    check rather than refusing the carry.
    """
    try:
        return int(timeline.GetEndFrame()) - int(timeline.GetStartFrame())
    except (AttributeError, TypeError, ValueError):
        return None


def _carry_unbound_reply(reply: dict, timeline, rows, flags: set,
                         carried: list, uncarried: list) -> None:
    """Carry a reply that binds to no note by its own picture, as ever.

    The pairing is reported, not invented: `legacy` (prose or frame
    locator from before the identity rule) and `unpaired` (an identity
    nothing on this reel carries) both keep today's independent carry
    and gain a flag the report reads aloud. Content untouched.
    """
    frame = resolve_frame(reply, timeline, rows)
    kind = ("independent-legacy" if "legacy" in flags
            else "independent-unpaired" if "unpaired" in flags
            else "independent-unclaimed")
    if frame is None:
        reason = ("its anchor picture is in the replacement nowhere"
                  if reply.get("anchor")
                  else "nothing was playing under it to anchor to")
        uncarried.append({**reply, "pairing": kind,
                          "pairing_flags": sorted(flags), "why": reason})
    else:
        carried.append({**reply, "to_frame": int(frame), "pairing": kind,
                        "pairing_flags": sorted(flags)})


def plan_carry(markers, timeline, timeline_name: str = "") -> tuple:
    """Split markers into those that resolve here and those that do not.

    Returns `(carried, uncarried)`; neither list is written anywhere.
    A pure split so a caller may report before it acts, which is the
    order that matters: the report is the deliverable, the carry is
    the convenience.

    Replies of OURS (markers carrying a `marker_feedback` reply record)
    are not carried by their own picture. They are RE-PAIRED with the
    note they answer: when the answered note carries from X to Y, its
    reply goes to Y + `REPLY_TRACK_OFFSET`, the adjacent frame the
    reply convention has always used (Reel 14: blue at 162, green at
    163). A reply whose note is itself UNCARRIED is uncarried too, and
    says which note it follows rather than stranding silently. A reply
    naming nothing on this reel, or naming nothing valid at all
    (legacy prose/frame locators), keeps the old independent carry and
    is flagged for the report - content untouched, pairing reported.
    Carried asks come before carried replies so a reply never lands
    before the note it follows.
    """
    rows = _picture_rows(timeline)
    asks = [m for m in markers if not _reply_links(m)]
    replies = [m for m in markers if _reply_links(m)]
    identities = {m["frame"]: _ask_identity(m, timeline_name)
                  for m in asks}
    carried, uncarried = [], []
    for marker in asks:
        frame = resolve_frame(marker, timeline, rows)
        if frame is None:
            reason = ("its anchor picture is in the replacement nowhere"
                      if marker.get("anchor")
                      else "nothing was playing under it to anchor to")
            uncarried.append({**marker, "why": reason})
        else:
            carried.append({**marker, "to_frame": int(frame),
                            "pairing": "note"})
    # Each ask object to its fate, keyed by frame - Resolve holds one
    # marker per frame, so the key is unique on a timeline.
    fate = {}
    for entry in carried:
        fate[entry["frame"]] = ("carried", entry["to_frame"])
    for entry in uncarried:
        fate.setdefault(entry["frame"], ("uncarried", entry))
    used = {entry["to_frame"] for entry in carried}
    span = _timeline_span(timeline)
    for reply in replies:
        ask, flags = _bind_reply(reply, asks, identities)
        if ask is None:
            _carry_unbound_reply(reply, timeline, rows, flags, carried,
                                 uncarried)
            used.update(c["to_frame"] for c in carried
                        if c.get("pairing", "").startswith("independent")
                        and c["frame"] == reply["frame"])
            continue
        status, detail = fate.get(ask["frame"], ("uncarried", None))
        if status != "carried":
            why = (detail or {}).get("why", "it does not resolve here")
            uncarried.append({**reply, "pairing": "stranded",
                              "reply_of": ask["frame"],
                              "why": (f"its note {ask['color']} "
                                      f"{ask['name']!r} @{ask['frame']} "
                                      f"is itself NOT CARRIED ({why}) - "
                                      f"the question moved nowhere, so "
                                      f"the answer follows it nowhere")})
            continue
        target = detail + REPLY_TRACK_OFFSET
        if span is not None and not 0 <= target < span:
            uncarried.append({**reply, "pairing": "stranded",
                              "reply_of": ask["frame"],
                              "why": (f"its note carried to @{detail} "
                                      f"but @{target} is past the end of "
                                      f"the replacement")})
            continue
        if target in used:
            uncarried.append({**reply, "pairing": "stranded",
                              "reply_of": ask["frame"],
                              "why": (f"its note carried to @{detail} "
                                      f"but @{target} is already taken - "
                                      f"one marker per frame")})
            continue
        used.add(target)
        carried.append({**reply, "to_frame": int(target),
                        "pairing": "repaired",
                        "paired_with": ask["frame"],
                        "pairing_flags": sorted(flags)})
    return carried, uncarried


def report(timeline_name: str, carried, uncarried) -> None:
    """Say what is about to happen to the captain's words. Always.

    A promotion with nothing to carry prints nothing; one that carries
    or drops anything says which, by name and with the words, on
    stdout for the carried and stderr for the dropped - a note this
    build is about to lose is not an informational line.

    Replies get their own lines: a re-paired one names the note it
    follows and both frames, so the pairing is visible without opening
    Resolve; a reply whose note is itself uncarried is reported
    ALONGSIDE it, never stranded in silence; and a reply that binds to
    nothing - legacy locator or an identity no note carries - says so
    even when its own picture carried fine, because the pairing, not
    the position, is what decayed.
    """
    for marker in carried:
        if marker.get("pairing") == "repaired":
            extra = ""
            flags = marker.get("pairing_flags") or []
            if "anchor_mismatch" in flags:
                extra = " (same words, different picture - bound nearest)"
            elif "weak" in flags:
                extra = " (by words alone - no anchor recorded)"
            print(f"  Reply re-paired onto {timeline_name}: "
                  f"{marker['color']} {marker['name']!r} @"
                  f"{marker['frame']} -> @{marker['to_frame']}, with "
                  f"its note @{marker['paired_with']}{extra}",
                  flush=True)
            continue
        print(f"  Marker carried onto {timeline_name}: "
              f"{marker['color']} {marker['name']!r} @"
              f"{marker['frame']} -> @{marker['to_frame']}", flush=True)
    for marker in carried:
        pairing = marker.get("pairing") or ""
        if not pairing.startswith("independent-"):
            continue
        flags = marker.get("pairing_flags") or []
        if "legacy" in flags:
            raw = next((f for f in flags if f not in
                        ("legacy", "unpaired", "unclaimed",
                         "anchor_mismatch", "weak")), "")
            print(f"  REPLY NAMES NO VALID NOTE onto {timeline_name}: "
                  f"{marker['color']} {marker['name']!r} @{marker['frame']} "
                  f"carried by its own picture to @{marker['to_frame']} "
                  f"but its answers locator {raw!r} is prose or a frame, "
                  f"not a note identity - the pairing is unverified.",
                  file=sys.stderr, flush=True)
        elif "unpaired" in flags:
            answers, _, _ = _answered_identity(marker)
            print(f"  REPLY NAMES NOTHING HERE onto {timeline_name}: "
                  f"{marker['color']} {marker['name']!r} @{marker['frame']} "
                  f"carried by its own picture to @{marker['to_frame']} "
                  f"but {answers!r} matches no note on this reel.",
                  file=sys.stderr, flush=True)
    for marker in uncarried:
        kind = ("REPLY NOT CARRIED" if marker.get("pairing") == "stranded"
                else "MARKER NOT CARRIED")
        print(f"  {kind} onto {timeline_name}: "
              f"{marker['color']} {marker['name']!r} @{marker['frame']} "
              f"- {marker['why']}. The captain wrote: "
              f"{marker['note'].strip()!r}",
              file=sys.stderr, flush=True)




def _already_carries(target, frame: int, marker) -> bool:
    """Does `target` already hold THIS marker at `frame`?

    A touch-up stages on a DUPLICATE of the approved timeline, and
    Resolve's duplicate keeps every timeline and clip marker - so the
    carry then asks Resolve to add a marker that is already there, is
    declined, and used to report "NOT CARRIED" for a note that never
    left. Measured 2026-09-25: 34 such reports on the 30-reel post-header
    touch, every marker present exactly once on every reel. Only an
    IDENTICAL marker (name, colour, note) counts: a different marker at
    that frame is still a decline, and still reported.
    """
    try:
        existing = (target.GetMarkers() or {}).get(int(frame))
    except Exception:                                   # noqa: BLE001
        return False
    return bool(existing) and all(
        str(existing.get(k) or "") == str(marker.get(k) or "")
        for k in ("name", "color", "note"))


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
        if not ok and _already_carries(
                timeline, int(marker["to_frame"]), marker):
            continue
        if not ok:
            failed.append(marker)
            print(f"  MARKER NOT CARRIED: Resolve declined "
                  f"{marker['name']!r} at frame {marker['to_frame']} - "
                  f"the captain wrote: {marker['note'].strip()!r}",
                  file=sys.stderr, flush=True)
    return failed


# ── The clip plane ────────────────────────────────────────────────
#
# A DaVinci marker lives either on the TIMELINE or on a CLIP ITEM, and
# promotion carried the timeline plane only - so a clip-anchored marker
# died with its item when a rebuild replaced it, and NOTHING REPORTED
# THE LOSS. Proven 2026-09-19 on Reel 09: the CTA-animation note (blue,
# the captain's verbatim words) had died that way on an earlier rebuild
# and was reported nowhere until a lane tripped over it and restored it
# by hand.
#
# What "the anchor" means here is NOT the timeline frame - a rebuild
# moves every frame. It is the source file and the source frame the
# marker sits on: WHICH item the note is about. A clip marker's key IS
# already a source frame (the same space as `GetLeftOffset()` -
# `marker_feedback`), so carrying it is placing the same key on the
# same file's placement in the replacement. Where the file plays twice
# over the key, or plays nowhere, there is no unique item to place on
# and the marker is REPORTED BY NAME rather than guessed onto one - a
# marker silently re-anchored to the wrong item is worse than one
# honestly reported missing. That refusal is the timeline plane's rule
# and it is not weakened here.
#
# Deliberate boundaries, stated so nobody re-derives them:
#
# * Media-pool-inherited copies are not carried. A pool marker that
#   exists when a clip is placed is COPIED onto every item cut from the
#   file (`marker_feedback`), so the replacement's own placements
#   already inherit the same copy - carrying it again would only earn a
#   decline for a marker that is already there. Skipped the way the
#   reader skips them: same key, same name, same note.
# * An uncarried clip marker is REPORTED, never re-placed. The timeline
#   plane puts an uncarried note back as a Blue at the seam - but a clip
#   note is about an ITEM, and re-filing it as a moment note on the
#   timeline plane would read as a different claim (and route
#   differently downstream). The words survive on the retired backup,
#   in the datastore pull, and in the report below.
# * Clip replies carry by their OWN anchor, like every other clip
#   marker. The timeline plane re-pairs a reply with its note by
#   identity; the clip plane does not - pairing across replaced items
#   is a second mechanism this change does not build.
#
# `tests/test_clip_marker_carry.py`.

#: Every track a clip marker may live on. The retiring inventory held
#: caption cards, motion graphics (video) and one master-MXF audio
#: item - the picture-rows-only filter of the timeline plane would
#: miss the last, so the clip plane reads both media types whole.
_CLIP_TRACK_TYPES = ("video", "audio")


def _clip_row_items(timeline):
    """Every `(track_type, track_index, track_name, item)` on `timeline`.

    Raises `MarkerCarryUnreadable` when a row will not read - the same
    fail-closed shape as the timeline plane: a promotion that cannot
    see every item cannot prove it carried every note.
    """
    rows = []
    for track_type in _CLIP_TRACK_TYPES:
        try:
            count = timeline.GetTrackCount(track_type) or 0
        except Exception as unreadable:
            raise MarkerCarryUnreadable(
                f"the {track_type} rows could not be read "
                f"({unreadable}); a promotion that cannot see the "
                f"captain's words must not proceed to replace them "
                f"silently.") from unreadable
        for index in range(1, count + 1):
            try:
                name = timeline.GetTrackName(track_type, index) or ""
                items = (timeline.GetItemListInTrack(track_type, index)
                         or [])
            except Exception as unreadable:
                raise MarkerCarryUnreadable(
                    f"the items of {track_type}{index} could not be read "
                    f"({unreadable}); a promotion that cannot see the "
                    f"captain's words must not proceed to replace them "
                    f"silently.") from unreadable
            for item in items:
                rows.append((track_type, index, name, item))
    return rows


def _clip_item_anchor(item, track_type: str, track_index: int):
    """What item this is, in source space, or None when it will not say.

    None is not a guess and not a zero: an item whose span or source
    file cannot be read cannot anchor a marker, and the marker is
    reported uncarried rather than placed by frame.
    """
    try:
        start = int(item.GetStart())
        end = int(item.GetEnd())
        left = int(item.GetLeftOffset())
    except (AttributeError, TypeError, ValueError):
        return None
    try:
        duration = int(item.GetDuration())
    except (AttributeError, TypeError, ValueError):
        duration = end - start
    if duration < 0:
        return None
    try:
        pool_item = item.GetMediaPoolItem()
        path = (str(pool_item.GetClipProperty("File Path") or "")
                if pool_item is not None else "")
    except (AttributeError, TypeError, ValueError):
        path = ""
    try:
        clip_name = item.GetName() or ""
    except (AttributeError, TypeError, ValueError):
        clip_name = ""
    return {
        "source_file": path,
        "track_type": track_type,
        "track_index": int(track_index),
        "clip_name": str(clip_name),
        "timeline_start": start,
        "timeline_end": end,
        "source_start": left,
        "source_end": left + duration,
    }


def _item_markers(item):
    """This item's own markers, or None when the item answers no marker API.

    None is a property of TEST DOUBLES only: every real TimelineItem
    answers `GetMarkers` (`marker_feedback`, measured). A double
    without the method is a double carrying no markers to read, and is
    skipped - anything the method itself raises is unreadable instead,
    and refuses rather than reading as empty.
    """
    try:
        get = item.GetMarkers
    except AttributeError:
        return None
    try:
        return dict(get() or {})
    except Exception as unreadable:
        raise MarkerCarryUnreadable(
            f"a clip item's markers could not be read ({unreadable}); "
            f"a promotion that cannot see the captain's words must not "
            f"proceed to replace them silently.") from unreadable


def read_clip_markers(timeline, timeline_name: str = "") -> list:
    """Every marker living on a clip item, with the anchor each sits on.

    Read BEFORE anything is renamed, beside `read_markers` - the whole
    point of either is that the captain's words are in hand before the
    object that carries them is replaced. One entry per item marker:

    * `frame`: the absolute timeline frame it resolves to, or None
      with `unplaced_reason` when the key sits outside what the clip
      plays - kept UNPLACED, never clamped to the clip's head (the
      `marker_feedback` rule);
    * `source_frame`: the marker's own key, in source space;
    * `anchor`: the placement the note was typed ON - the source file
      plus the source range that says WHICH item - or None when the
      item would not say, in which case the plan reports rather than
      places.
    """
    del timeline_name  # the anchor, not the reel, is what carries here
    entries = []
    pool_seen: dict = {}
    for track_type, index, _name, item in _clip_row_items(timeline):
        raw = _item_markers(item)
        if raw is None:
            continue
        anchor = _clip_item_anchor(item, track_type, index)
        if anchor is not None:
            key = anchor["source_file"] or anchor["clip_name"]
            if key not in pool_seen:
                try:
                    pool_item = item.GetMediaPoolItem()
                    pool_seen[key] = (dict(pool_item.GetMarkers() or {})
                                      if pool_item is not None else {})
                except (AttributeError, TypeError, ValueError):
                    pool_seen[key] = {}
        else:
            pool_seen.setdefault("", {})
        inherited = pool_seen.get(
            (anchor["source_file"] or anchor["clip_name"])
            if anchor is not None else "", {}) or {}
        for key, marker in raw.items():
            try:
                source_frame = int(key)
            except (TypeError, ValueError):
                continue
            marker = dict(marker)
            name = marker.get("name") or ""
            note = marker.get("note") or ""
            pooled = inherited.get(key) or inherited.get(source_frame)
            if isinstance(pooled, dict):
                if ((pooled.get("name") or "",
                     pooled.get("note") or "") == (name, note)):
                    continue  # the pool's own copy, re-inherited below
            if anchor is None:
                entries.append({
                    "plane": "clip",
                    "frame": None,
                    "source_frame": source_frame,
                    "color": marker.get("color", ""),
                    "name": name,
                    "note": note,
                    "duration": int(marker.get("duration", 1) or 1),
                    "custom_data": marker.get("customData", ""),
                    "anchor": None,
                    "unplaced_reason": (
                        "the item it sits on would not report its span "
                        "or source file, so there is no anchor to carry"),
                })
                continue
            in_range = (anchor["source_start"]
                        <= source_frame < anchor["source_end"])
            entries.append({
                "plane": "clip",
                "frame": (anchor["timeline_start"]
                          + (source_frame - anchor["source_start"])
                          if in_range else None),
                "source_frame": source_frame,
                "color": marker.get("color", ""),
                "name": name,
                "note": note,
                "duration": int(marker.get("duration", 1) or 1),
                "custom_data": marker.get("customData", ""),
                "anchor": {**anchor, "source_frame": source_frame},
                "unplaced_reason": ("" if in_range else (
                    f"marker at source frame {source_frame}, outside the "
                    f"{anchor['source_start']}..{anchor['source_end']} "
                    f"this clip plays")),
            })
    entries.sort(key=lambda e: (e["frame"] is None, e["frame"] or 0,
                                str(e["anchor"])))
    return entries


def _clip_candidates(anchor: dict, replacement) -> list:
    """Every replacement placement playing the anchor's source frame.

    A candidate is `(item, candidate_anchor)`: same non-empty source
    file, key inside its played source range. Linked audio+video items
    of ONE placement (same file, same timeline span, same source span
    - `marker_routing._same_placement`) count once: they are one clip
    seen twice, not two items to choose between.
    """
    if not anchor or not anchor.get("source_file"):
        return []
    path, key = anchor["source_file"], anchor["source_frame"]
    found = []
    for track_type, index, _name, item in _clip_row_items(replacement):
        candidate = _clip_item_anchor(item, track_type, index)
        if candidate is None:
            continue
        if candidate["source_file"] != path:
            continue
        if not (candidate["source_start"]
                <= key < candidate["source_end"]):
            continue
        candidate = {**candidate, "source_frame": key}
        if any(all(candidate.get(k) == seen[1].get(k)
                   for k in ("source_file", "timeline_start",
                             "timeline_end", "source_start",
                             "source_end"))
               for seen in found):
            continue
        found.append((item, candidate))
    return found


def plan_clip_carry(clip_markers, replacement,
                    timeline_name: str = "") -> tuple:
    """Split clip markers into those that resolve here and those that do not.

    Returns `(carried, uncarried)`; neither list is written anywhere -
    the same pure split `plan_carry` is, so a caller may report before
    it acts. A marker carries when exactly one DISTINCT replacement
    placement plays its anchor's source frame: the key is unchanged
    (source space is rebuild-invariant for the same file) and
    `to_frame` is where that key now lands on the timeline. Zero
    placements, two different ones, a marker with no anchor, or one
    whose key its own clip never played are all uncarried with the
    reason stated - never guessed onto a neighbour.
    """
    del timeline_name  # identity pairs replies; this plane carries anchors
    carried, uncarried = [], []
    for marker in clip_markers:
        anchor = marker.get("anchor")
        if anchor is None:
            uncarried.append({**marker, "why": (
                marker.get("unplaced_reason")
                or "nothing was playing under it to anchor to")})
            continue
        if marker.get("frame") is None:
            uncarried.append({**marker, "why": (
                marker.get("unplaced_reason")
                or "its key sits outside what its clip plays")})
            continue
        if not anchor.get("source_file"):
            uncarried.append({**marker, "why": (
                "it sits on a generator or composition with no source "
                "file - there is no file identity to carry it by")})
            continue
        candidates = _clip_candidates(anchor, replacement)
        if not candidates:
            uncarried.append({**marker, "why": (
                f"no clip in the replacement plays source frame "
                f"{anchor['source_frame']} of {anchor['source_file']}"
            )})
        elif len(candidates) > 1:
            named = "; ".join(
                f"{candidate['clip_name'] or '(unnamed)'} "
                f"({candidate['track_type']}{candidate['track_index']} "
                f"{candidate['timeline_start']}"
                f"..{candidate['timeline_end']})"
                for _item, candidate in candidates)
            uncarried.append({**marker, "why": (
                f"{len(candidates)} different clips play source frame "
                f"{anchor['source_frame']} of {anchor['source_file']} "
                f"({named}) - refusing to guess which one the note is "
                f"about")})
        else:
            _item, candidate = candidates[0]
            carried.append({
                **marker,
                "to_source_frame": anchor["source_frame"],
                "to_frame": (candidate["timeline_start"]
                             + (anchor["source_frame"]
                                - candidate["source_start"])),
                "target": candidate,
            })
    return carried, uncarried


def report_clip(timeline_name: str, carried, uncarried) -> None:
    """Say what is about to happen to the captain's clip-anchored words.

    A promotion with no clip markers prints nothing; one that carries
    or drops any says which, by name and with the words - stdout for
    the carried, stderr for the dropped. A note this build is about to
    lose is not an informational line, on either plane.
    """
    for marker in carried:
        target = marker.get("target") or {}
        print(f"  Clip marker carried onto {timeline_name}: "
              f"{marker['color']} {marker['name']!r} "
              f"source {marker['source_frame']} of "
              f"{(marker.get('anchor') or {}).get('source_file')} -> "
              f"{target.get('track_type')}{target.get('track_index')} "
              f"@{marker['to_frame']}", flush=True)
    for marker in uncarried:
        anchor = marker.get("anchor") or {}
        where = (f"source {marker.get('source_frame')} of "
                 f"{anchor.get('source_file')}"
                 if anchor.get("source_file")
                 else "no anchored source file")
        print(f"  CLIP MARKER NOT CARRIED onto {timeline_name}: "
              f"{marker['color']} {marker['name']!r} ({where}) - "
              f"{marker['why']}. The captain wrote: "
              f"{marker['note'].strip()!r}",
              file=sys.stderr, flush=True)


@under_lease("carry the captain's clip markers onto the replacement")
def place_clip_markers(replacement, carried) -> list:
    """Write the resolved clip markers onto the replacement's items.

    Each carried entry is re-resolved against the live replacement by
    its anchor - the rename changed names, never items, so the unique
    placement the plan found is the one found again. A marker whose
    anchor no longer resolves uniquely, or that Resolve declines, is
    returned in the failure list and named on stderr with the captain's
    words - AGENTS.md 5: judge a Resolve call by what it RETURNS.
    """
    failed = []
    for marker in carried:
        anchor = marker.get("anchor") or {}
        target = marker.get("target") or {}
        candidates = _clip_candidates(anchor, replacement)
        item = None
        for candidate_item, candidate in candidates:
            if all(candidate.get(k) == target.get(k)
                   for k in ("source_file", "track_type", "track_index",
                             "timeline_start", "timeline_end")):
                item = candidate_item
                break
        if item is None:
            failed.append(marker)
            print(f"  CLIP MARKER NOT CARRIED: the anchored placement "
                  f"({target.get('track_type')}"
                  f"{target.get('track_index')} "
                  f"@{target.get('timeline_start')}) no longer resolves "
                  f"uniquely - the captain wrote: "
                  f"{marker['note'].strip()!r}",
                  file=sys.stderr, flush=True)
            continue
        try:
            if marker.get("custom_data"):
                ok = item.AddMarker(
                    int(marker["to_source_frame"]),
                    marker["color"] or "Blue", marker["name"],
                    marker["note"], marker["duration"],
                    marker.get("custom_data") or "")
            else:
                ok = item.AddMarker(
                    int(marker["to_source_frame"]),
                    marker["color"] or "Blue", marker["name"],
                    marker["note"], marker["duration"])
        except Exception as refused:                       # noqa: BLE001
            failed.append(marker)
            print(f"  CLIP MARKER NOT CARRIED: Resolve raised "
                  f"({refused}) for {marker['name']!r} - the captain "
                  f"wrote: {marker['note'].strip()!r}",
                  file=sys.stderr, flush=True)
            continue
        if not ok and _already_carries(
                item, int(marker["to_source_frame"]), marker):
            continue
        if not ok:
            failed.append(marker)
            print(f"  CLIP MARKER NOT CARRIED: Resolve declined "
                  f"{marker['name']!r} at source "
                  f"{marker['to_source_frame']} of "
                  f"{anchor.get('source_file')} - the captain wrote: "
                  f"{marker['note'].strip()!r}",
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


def audit_replies(notes, timeline_name: str = "") -> list:
    """Pairing health for every reply in `notes`. Read-only, no writes.

    Takes `read_markers` output and returns one row per reply marker:
    `frame`, `color`, `name`, `note`, `answers`, `ask_frame` (the live
    frame of the note it binds to, or None), `distance` (how far the
    reply sits from the adjacent frame its note expects, or None), and
    `status`:

    * `paired-adjacent` - beside its note, as the convention reads;
    * `paired-drifted` - bound to a note it no longer sits beside (the
      Reel 14 shape: the note moved on under a rebuild and the reply
      stayed - report before changing anything);
    * `unpaired` - a valid identity matching no note here;
    * `legacy` - prose or a frame locator, never joinable (the Reel 04
      and Reel 29 shapes);
    * `unclaimed` - no `answers` at all.

    `pairing_flags` carries the bind's exactness (`weak`,
    `anchor_mismatch`). Clip-plane replies never appear here because
    `read_markers` never reads that plane - the audit's caller says
    that count aloud rather than letting the table read as complete.
    """
    asks = [m for m in notes if not _reply_links(m)]
    replies = [m for m in notes if _reply_links(m)]
    identities = {m["frame"]: _ask_identity(m, timeline_name)
                  for m in asks}
    rows = []
    for reply in replies:
        answers, _anchor, valid = _answered_identity(reply)
        ask, flags = _bind_reply(reply, asks, identities)
        base = {"frame": reply["frame"], "color": reply.get("color", ""),
                "name": reply.get("name", ""),
                "note": reply.get("note", ""),
                "answers": answers,
                "pairing_flags": sorted(flags - {answers})}
        if ask is None:
            status = ("unpaired" if valid
                      else "legacy" if answers else "unclaimed")
            rows.append({**base, "status": status, "ask_frame": None,
                         "distance": None})
            continue
        expected = ask["frame"] + REPLY_TRACK_OFFSET
        rows.append({**base, "status": ("paired-adjacent"
                                        if reply["frame"] == expected
                                        else "paired-drifted"),
                     "ask_frame": ask["frame"],
                     "distance": reply["frame"] - expected})
    return rows

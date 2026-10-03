"""The editor's timeline edits, carried through every rebuild.

The captain, 2026-10-01, on Ren holding onto his manual timeline edits:
*"first snapshot + detect my changes + refuse to overwrite them, then
carrying edits through rebuilds"*. Step one (`reel_replace_guard`
`detect_editor_changes` / `protect_editor_changes`) records every
unattributed delta between Ren's last read of a reel and the live
timeline, and REFUSES a replacement that would lose one. This module is
step two: the deltas it can state in SOURCE terms become CARRIED EDITS,
and every promotion - a rebuild, a swap, a variant choice - applies them
to the incoming staging timeline before anything is renamed.

What is carried
---------------
An edit names the item it governs by what it PLAYS - row
(`"<media>:<track name>"`), source identity and source in/out frames -
never by a record frame, because a rebuild moves every record frame
and keeps every source frame.

- `cut`: a source passage the editor removed (Reel 7: Craig
  25,263-25,374 and Akshita 60,745-60,979). Staging must play no frame
  of it on that row. `ripple` records whether the editor closed the gap
  (items after the cut moved earlier by its length), and the staging
  delete does the same - which in Resolve removes that TIME from every
  row (`_ripple_collateral`), so a rippled cut that another row's item
  overlaps refuses rather than trimming that item.
- `trim`: a passage the editor shortened at its head, its tail or both;
  staging plays only the kept source range. Rippled or lifted, as the
  editor did it. Carried through `composed_edit` - the delete and
  re-place Resolve allows - with the comp pass re-run over a manifest
  whose trimmed specs name the kept range (`_apply_trims`).
- `move`: an item that kept its source but sits somewhere else, beyond
  what the editor's ripples explain. Its place is stated as the PICTURE
  it now sits over - the source frame playing on V1 at its new start -
  so a rebuild that moves V1 moves the target with it (`_apply_moves`).
  Moving an item ON V1 is a reorder of the picture and is not carried.
- `enabled`: an item switched off or on.
- `transform`: one Edit-page transform property (Pan, Tilt, Zoom...)
  the editor set on an item.

Ripples are read off the record's own before and after reads
(`_shift_model`): an item that kept its source moved by exactly the
lengths of the rippled cuts and trims before it, and an edit is
rippled when the items after it say so. A shift nothing explains is a
move. Anything else (an added item, a grade, an extension past the
original passage, a marker the marker carry cannot place) stays step
one's refusal, named as what it is.

Every carried edit records the human wording, where it came from (the
editor-change record id or Ren touch journal id), its before/after
values and the plan version it was detected against
(`plan_content_hash`). The ledger is
`plan_provenance.CARRIED_EDITS_KEY`; an edit stays in force across every
later build until a Ren act changes the same thing on purpose
(`record_after_promotion` retires it as superseded, naming the act) or
the editor reverses it (the next detection supersedes it).

A Ren touch-up's own in-place writes (`set_properties`, `set_enabled`)
are filed onto the same ledger by `derive_touch_edits` at touch
promotion time, so the next rebuild carries them exactly like the
editor's: a touched passage the new plan no longer plays refuses by
name instead of dropping the write.

Apply, then verify by re-reading
--------------------------------
`carry_editor_edits` plans every edit against a full read of staging
before it writes anything; an edit that cannot be mapped (two staged
items play the same passage, a staged item plays PART of a cut passage,
a ripple that would change another row's item) refuses by name with
the source ranges, nothing written. It then writes in four phases,
each re-planned off a fresh read because a re-placed item is a new
object: trims, cuts, moves, then the in-place enabled and transform
writes. `verify_carried_edits` judges the result on a fresh read of
staging, in source ranges, never in row counts. A write's return value
is never the verdict.

`tests/unit/resolve/test_editor_edit_carry.py`.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256

from library.tools import reel_replace_guard as _guard

RECORD_FIELDS = ("record_in", "record_out")
#: Transform keys Resolve reports but nobody sets.
_IGNORED_TRANSFORM_KEYS = frozenset({"Resolution", "ResolutionWidth",
                                     "ResolutionHeight"})
TOLERANCE = 1e-6


class EditorEditCarryRefused(_guard.EditorChangeRefused):
    """A carried edit could not be mapped onto, or did not land on, staging."""


def _row(item: dict) -> str:
    return _guard.row_key(str(item.get("track_type")),
                          str(item.get("track_name")))


def _source(item: dict) -> tuple:
    return (item.get("source_in_frame"), item.get("source_out_frame"))


def _frames(value) -> str:
    return f"{int(value):,}" if isinstance(value, int) else str(value)


def _span(start, end) -> str:
    return f"{_frames(start)}..{_frames(end)}"


def _edit_id(final: str, kind: str, item: dict, field: str = "") -> str:
    key = "\0".join(str(part) for part in (
        final, kind, _row(item), item.get("source_identity"),
        item.get("source_in_frame"), item.get("source_out_frame"), field))
    return sha256(key.encode("utf-8")).hexdigest()[:24]


def _governs(edit: dict, item: dict) -> bool:
    """`item` plays exactly the passage `edit` names."""
    return (_row(item) == edit["row"]
            and item.get("source_identity") == edit["source_identity"]
            and _source(item) == (edit["source_in_frame"],
                                  edit["source_out_frame"]))


def _overlaps(edit: dict, item: dict) -> bool:
    """`item` plays at least one frame of the passage `edit` names."""
    if (_row(item) != edit["row"]
            or item.get("source_identity") != edit["source_identity"]):
        return False
    start, end = _source(item)
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return start < edit["source_out_frame"] and \
        edit["source_in_frame"] < end


def _same(got, wanted) -> bool:
    numbers = (int, float)
    if (isinstance(got, numbers) and isinstance(wanted, numbers)
            and not isinstance(got, bool) and not isinstance(wanted, bool)):
        return abs(float(got) - float(wanted)) <= TOLERANCE
    return got == wanted


def _base(final: str, kind: str, item: dict, *, field: str, record: dict,
          plan_version, wording: str, before, after) -> dict:
    return {
        "id": _edit_id(final, kind, item, field),
        "timeline": final,
        "kind": kind,
        "field": field,
        "row": _row(item),
        "track_type": item.get("track_type"),
        "track_name": item.get("track_name"),
        "name": item.get("name"),
        "source_identity": item.get("source_identity"),
        "source_in_frame": item.get("source_in_frame"),
        "source_out_frame": item.get("source_out_frame"),
        "record_in_when_detected": item.get("record_in"),
        "record_out_when_detected": item.get("record_out"),
        "before": before,
        "after": after,
        "wording": wording,
        "source": f"unattributed_editor_change:{record.get('id')}",
        "author": "editor (unattributed: not a Ren action)",
        "detected_at": record.get("recorded_at"),
        "plan_version": plan_version,
        "status": "active",
    }


def _stable(item: dict) -> tuple:
    return (_row(item), item.get("source_identity"),
            item.get("source_in_frame"), item.get("source_out_frame"))


def _picture_anchor(snapshot: dict, frame) -> dict | None:
    """The source frame V1 plays at record `frame`, or None."""
    for item in snapshot.get("items") or ():
        if (item.get("track_type") == "video"
                and item.get("track_index") == 1
                and isinstance(item.get("record_in"), int)
                and isinstance(item.get("source_in_frame"), int)
                and item["record_in"] <= frame < item["record_out"]):
            return {"anchor_row": _row(item),
                    "anchor_source_identity": item.get("source_identity"),
                    "anchor_source_frame":
                        item["source_in_frame"] + frame - item["record_in"]}
    return None


def _shift_model(events: list[dict], pairs: list[tuple]) -> None:
    """Decide each event's `ripple` from what the items after it did.

    `events` are cuts and trims in the BEFORE read, each with `start`,
    `end` and `amount` (frames it removes); `pairs` are `(before,
    after)` reads of items that kept their source. An item's shift is
    explained by the rippled events that end at or before its start; an
    event ripples when the items between it and the next one moved by
    that much more. Sets `ripple` on every event, in place.
    """
    ordered = sorted(events, key=lambda event: (event["end"], event["start"]))
    prior = 0
    for position, event in enumerate(ordered):
        following = ordered[position + 1:]
        limit = following[0]["start"] if following else None
        window = [after["record_in"] - before["record_in"]
                  for before, after in pairs
                  if before["record_in"] >= event["end"]
                  and (limit is None or before["record_in"] < limit)]
        if not window:
            window = [after["record_in"] - before["record_in"]
                      for before, after in pairs
                      if before["record_in"] >= event["end"]]
            event["ripple"] = any(shift <= -(prior + event["amount"])
                                  for shift in window)
        else:
            event["ripple"] = -(prior + event["amount"]) in window
        if event["ripple"]:
            prior += event["amount"]


def _nest_events(events: list[dict]) -> list[dict]:
    """Fold each event that lies inside a wider one into that one.

    A passage cut takes the captions over it with it: on Reel 7 the nine
    caption cards under Craig 25,263-25,374 and Akshita 60,745-60,979 are
    removed items too. That is one stretch of TIME leaving the timeline,
    not ten - counted apart, the shift model expects the items after the
    passage to move by the caption lengths as well, reads the passage
    cut as lifted and the picture after it as reordered. An event whose
    record span lies within another's joins it and takes its ripple.
    """
    widest = sorted(events, key=lambda event: (
        event["start"], -(event["end"] - event["start"])))
    kept = []
    for event in widest:
        container = next((outer for outer in kept
                          if outer["start"] <= event["start"]
                          and event["end"] <= outer["end"]), None)
        if container is None:
            kept.append(event)
        else:
            container["edits"].extend(event["edits"])
    return kept


def _explained(events: list[dict], frame: int) -> int:
    return -sum(event["amount"] for event in events
                if event["ripple"] and event["end"] <= frame)


def _pairs(record: dict) -> list[tuple]:
    """Items that kept their source, as `(before, after)` reads."""
    before = record.get("before_snapshot") or {}
    after = record.get("after_snapshot") or {}
    if before.get("items") is not None and after.get("items") is not None:
        olds, news = {}, {}
        for item in before["items"]:
            olds.setdefault(_stable(item), []).append(item)
        for item in after["items"]:
            news.setdefault(_stable(item), []).append(item)
        return [(old, new) for key in olds
                for old, new in zip(olds[key], news.get(key, ()))
                if isinstance(old.get("record_in"), int)
                and isinstance(new.get("record_in"), int)]
    return [(change["before"], change["after"])
            for change in record.get("changes") or ()
            if change["kind"] == "item_changed"
            and isinstance(change["before"].get("record_in"), int)
            and isinstance(change["after"].get("record_in"), int)]


def derive_edits(record: dict, *, plan_version=None) -> tuple[list, list]:
    """One editor-change record as carried edits, plus what is not carried.

    Pure. Returns `(edits, uncarried)`, where each `uncarried` entry is
    `{"change", "why"}` - an addition, an extension, a picture reorder
    or a field this module does not carry, said as what it is.
    """
    final = str(record.get("timeline"))
    changes = list(record.get("changes") or ())
    removed = [change for change in changes
               if change["kind"] == "item_removed"]
    added = [change for change in changes if change["kind"] == "item_added"]
    edits, uncarried, events = [], [], []

    def partners_of(old):
        passage = {"row": _row(old),
                   "source_identity": old.get("source_identity"),
                   "source_in_frame": old.get("source_in_frame"),
                   "source_out_frame": old.get("source_out_frame")}
        return [other for other in added
                if _overlaps(passage, other["after"])]

    def event_for(old, amount, edit):
        start, end = old.get("record_in"), old.get("record_out")
        if not isinstance(start, int) or not isinstance(end, int):
            return
        for event in events:
            # One passage cut from picture and sound is ONE event.
            if (event["start"], event["end"], event["amount"]) == (
                    start, end, amount):
                event["edits"].append(edit)
                return
        events.append({"start": start, "end": end, "amount": amount,
                       "edits": [edit]})

    used_added = set()
    for change in removed:
        old = change["before"]
        if not isinstance(old.get("source_in_frame"), int) or \
                not isinstance(old.get("source_out_frame"), int):
            uncarried.append({
                "change": change,
                "why": (f"{_row(old)} {old.get('name')!r} carries no "
                        f"readable source range, so its removal cannot "
                        f"be stated as a passage")})
            continue
        partners = partners_of(old)
        for other in partners:
            used_added.add(id(other))
        if len(partners) > 1:
            uncarried.append({
                "change": change,
                "why": (f"{_row(old)} {old.get('name')!r} source "
                        f"{_span(*_source(old))} now plays as "
                        + ", ".join(_span(*_source(other["after"]))
                                    for other in partners)
                        + " - a split passage is not carried")})
            continue
        if partners:
            kept_in, kept_out = _source(partners[0]["after"])
            if not (isinstance(kept_in, int) and isinstance(kept_out, int)
                    and old["source_in_frame"] <= kept_in < kept_out
                    <= old["source_out_frame"]):
                uncarried.append({
                    "change": change,
                    "why": (f"{_row(old)} {old.get('name')!r} source "
                            f"{_span(*_source(old))} now plays "
                            f"{_span(kept_in, kept_out)} - an extension "
                            f"past the passage is not carried")})
                continue
            head = kept_in - old["source_in_frame"]
            tail = old["source_out_frame"] - kept_out
            edit = _base(
                final, "trim", old, field="", record=record,
                plan_version=plan_version, wording="",
                before={"source_in_frame": old["source_in_frame"],
                        "source_out_frame": old["source_out_frame"],
                        "record_in": old.get("record_in")},
                after={"source_in_frame": kept_in,
                       "source_out_frame": kept_out,
                       "head": head, "tail": tail,
                       "record_in": partners[0]["after"].get("record_in")})
            edits.append(edit)
            event_for(old, head + tail, edit)
            continue
        edit = _base(
            final, "cut", old, field="", record=record,
            plan_version=plan_version, wording="",
            before={"record_in": old.get("record_in"),
                    "record_out": old.get("record_out"), "present": True},
            after={"present": False})
        edits.append(edit)
        # The TIME a cut removes is its record span. Resolve's source-out
        # getter can read a frame short of it (Reel 7: Craig 25,263-25,374
        # read 111 over a 112-frame span), and a ripple amount a frame off
        # reads the closed gap as a lift and the picture after as a move.
        record_in, record_out = old.get("record_in"), old.get("record_out")
        removed = (record_out - record_in
                   if isinstance(record_in, int)
                   and isinstance(record_out, int)
                   else old["source_out_frame"] - old["source_in_frame"])
        event_for(old, removed, edit)

    events = _nest_events(events)
    pairs = _pairs(record)
    _shift_model(events, pairs)
    for event in events:
        for edit in event["edits"]:
            edit["ripple"] = event["ripple"]
            passage = _span(edit["source_in_frame"], edit["source_out_frame"])
            gap = " and close the gap" if event["ripple"] else ""
            if edit["kind"] == "cut":
                edit["wording"] = (f"Cut {edit['name']!r} source {passage} "
                                   f"from {edit['row']}{gap}")
            else:
                kept = _span(edit["after"]["source_in_frame"],
                             edit["after"]["source_out_frame"])
                edit["wording"] = (f"Trim {edit['name']!r} source {passage} "
                                   f"on {edit['row']} to {kept}{gap}")

    after_snapshot = record.get("after_snapshot") or {}
    for before, after in pairs:
        shift = after["record_in"] - before["record_in"]
        if shift == _explained(events, before["record_in"]):
            continue
        what = (f"{_row(after)} {after.get('name')!r} source "
                f"{_span(*_source(after))}")
        if after.get("track_type") == "video" and \
                after.get("track_index") == 1:
            uncarried.append({
                "change": {"kind": "item_changed", "before": before,
                           "after": after, "changed": {}},
                "why": (f"{what} moved {shift:+d} on the picture row - a "
                        f"reorder of the picture is not carried")})
            continue
        anchor = _picture_anchor(after_snapshot, after["record_in"])
        if anchor is None:
            uncarried.append({
                "change": {"kind": "item_changed", "before": before,
                           "after": after, "changed": {}},
                "why": (f"{what} moved to record {after['record_in']}, "
                        f"where no picture plays to anchor it")})
            continue
        edits.append(_base(
            final, "move", after, field="record_in", record=record,
            plan_version=plan_version,
            wording=(f"Move {after.get('name')!r} source "
                     f"{_span(*_source(after))} on {_row(after)} over "
                     f"source frame {anchor['anchor_source_frame']:,} of "
                     f"the picture"),
            before={"record_in": before["record_in"]},
            after={**anchor, "record_in": after["record_in"]}))

    for change in added:
        if id(change) in used_added:
            continue
        item = change["after"]
        uncarried.append({
            "change": change,
            "why": (f"{_row(item)} {item.get('name')!r} source "
                    f"{_span(*_source(item))} was ADDED by the editor - "
                    f"additions are not carried")})
    for change in changes:
        if change["kind"] != "item_changed":
            # Timeline settings (a rippled cut's shorter end frame) and
            # markers (`marker_carry`) are judged by the guard's re-read.
            continue
        old, new = change["before"], change["after"]
        for field, values in change["changed"].items():
            if field == "enabled":
                if not isinstance(values["after"], bool):
                    uncarried.append({"change": change,
                                      "why": "enabled state unreadable"})
                    continue
                state = "on" if values["after"] else "off"
                edits.append(_base(
                    final, "enabled", new, field="enabled", record=record,
                    plan_version=plan_version,
                    wording=(f"Switch {new.get('name')!r} source "
                             f"{_span(*_source(new))} on {_row(new)} "
                             f"{state}"),
                    before=values["before"], after=values["after"]))
            elif field == "transform":
                old_t = values["before"] or {}
                new_t = values["after"] or {}
                for key in sorted(set(old_t) | set(new_t)):
                    if key in _IGNORED_TRANSFORM_KEYS or \
                            _same(old_t.get(key), new_t.get(key)):
                        continue
                    if key not in new_t:
                        uncarried.append({
                            "change": change,
                            "why": f"transform {key} became unreadable"})
                        continue
                    edits.append(_base(
                        final, "transform", new, field=f"transform.{key}",
                        record=record, plan_version=plan_version,
                        wording=(f"Hold {key} {new_t[key]!r} on "
                                 f"{new.get('name')!r} source "
                                 f"{_span(*_source(new))} on {_row(new)}"),
                        before=old_t.get(key), after=new_t[key]))
            elif field in RECORD_FIELDS or field in ("duration", "composite"):
                # A record shift is the shift model's (above); `composite`
                # mirrors transform keys already carried.
                continue
            else:
                uncarried.append({
                    "change": change,
                    "why": (f"{field} on {_row(old)} {old.get('name')!r} "
                            f"is not carried")})
    return edits, uncarried


def _touch_origin(journal_id: str) -> tuple[str, str]:
    """The ledger identity of one touch's writes: `(source, author)`."""
    return f"ren_touch:{journal_id}", "ren touch"


def _touch_suffix(edit: dict) -> str:
    """What a refusal line appends so a touch write names its touch."""
    source = str(edit.get("source") or "")
    if source.startswith("ren_touch:"):
        return f" (from Ren touch {source.split(':', 1)[1]})"
    return ""


def derive_touch_edits(final: str, live_snapshot: dict,
                       staged_snapshot: dict, applied_in_place: dict, *,
                       journal_id: str, plan_version=None,
                       detected_at=None) -> list[dict]:
    """A touch's in-place writes as carried edits. Pure.

    `live_snapshot` and `staged_snapshot` are full timeline snapshots
    (`reel_replace_guard.full_timeline_snapshot`); `applied_in_place`
    is the touch's `receipt["in_place"]` (`{"properties": [...],
    "enabled": [...]}`). Each `set_properties` key becomes a
    `transform` edit and each `set_enabled` an `enabled` edit, stated
    in source terms exactly like the editor's own - so every later
    rebuild carries them through `plan_application`, and a rebuild
    whose plan no longer plays the touched passage refuses by name
    instead of dropping the write.

    A write that changed nothing (the value already held) files
    nothing. Anything unmappable raises `EditorEditCarryRefused`:
    the touch stands half-filed nowhere, and the caller fails the
    touch rather than report a success the next rebuild silently
    loses. `entry_motion` writes add a Fusion comp no rebuild
    re-derives, so they are not carried here.
    """
    from library.tools import reel_touchup as _touchup

    live_items = list((live_snapshot or {}).get("items") or ())
    staged_items = list((staged_snapshot or {}).get("items") or ())
    applied = applied_in_place or {}
    if detected_at is None:
        detected_at = datetime.now(UTC).isoformat(timespec="seconds")
    source, author = _touch_origin(str(journal_id))
    record = {"id": str(journal_id), "recorded_at": detected_at}
    edits = []

    def live_target(row: str, record_frame) -> dict:
        # A touch addresses its item positionally (`V2`, `A1`); the
        # ledger states it by what it plays, so the row is resolved
        # to its track here and never stored.
        kind = str(row).upper()
        if kind.startswith("V"):
            track_type = "video"
        elif kind.startswith("A"):
            track_type = "audio"
        else:
            raise EditorEditCarryRefused(
                f"REFUSING the touch on {final!r}: its {row}@"
                f"{record_frame} write names no track, so it cannot be "
                f"stated as a carried edit. Nothing further is filed.")
        try:
            track_index = int(kind[1:])
        except ValueError:
            raise EditorEditCarryRefused(
                f"REFUSING the touch on {final!r}: its {row}@"
                f"{record_frame} write names no track, so it cannot be "
                f"stated as a carried edit. Nothing further is filed.")
        hits = [item for item in live_items
                if item.get("track_type") == track_type
                and item.get("track_index") == track_index
                and item.get("record_in") == record_frame]
        if len(hits) != 1:
            raise EditorEditCarryRefused(
                f"REFUSING the touch on {final!r}: its {row}@"
                f"{record_frame} write matches {len(hits)} live items, "
                f"so the write cannot be stated as a carried edit. "
                f"Nothing further is filed.")
        return hits[0]

    def staged_counterpart(live: dict, record_frame) -> dict:
        row = _row(live)
        same_passage = [
            item for item in staged_items
            if _row(item) == row
            and item.get("source_identity") == live.get("source_identity")
            and (item.get("source_in_frame"),
                 item.get("source_out_frame")) == (
                     live.get("source_in_frame"),
                     live.get("source_out_frame"))]
        if len(same_passage) == 1:
            return same_passage[0]
        at_record = [item for item in same_passage
                     if item.get("record_in") == record_frame]
        if len(at_record) == 1:
            return at_record[0]
        raise EditorEditCarryRefused(
            f"REFUSING the touch on {final!r}: its {_row(live)} "
            f"{live.get('name')!r} source "
            f"{_span(live.get('source_in_frame'), live.get('source_out_frame'))} "
            f"matches {len(same_passage)} staged items, so the write "
            f"cannot be stated as a carried edit. Nothing further "
            f"is filed.")

    for entry in applied.get("properties") or ():
        row = str(entry.get("row")).upper()
        frame = int(entry["record_frame"])
        live = live_target(row, frame)
        staged = staged_counterpart(live, frame)
        for key in sorted(entry.get("properties") or {}):
            before = (live.get("transform") or {}).get(key)
            after = (staged.get("transform") or {}).get(key)
            wanted = (entry.get("properties") or {})[key]
            if not _same(after, wanted):
                raise _touchup.TouchupError(
                    f"the staged {_row(staged)} {staged.get('name')!r} "
                    f"reads {key} {after!r} after the touch asked for "
                    f"{wanted!r} - the write cannot be stated as a "
                    f"carried edit. Nothing further is filed.")
            if _same(before, after):
                continue
            passage = _span(staged["source_in_frame"],
                            staged["source_out_frame"])
            edits.append(_base(
                final, "transform", staged, field=f"transform.{key}",
                record=record, plan_version=plan_version,
                wording=(f"Hold {key} {after!r} on "
                         f"{staged.get('name')!r} source {passage} on "
                         f"{_row(staged)} (ren touch {journal_id})"),
                before=before, after=after))
    for entry in applied.get("enabled") or ():
        row = str(entry.get("row")).upper()
        frame = int(entry["record_frame"])
        live = live_target(row, frame)
        staged = staged_counterpart(live, frame)
        before, after = live.get("enabled"), staged.get("enabled")
        if not isinstance(after, bool) or after is not bool(entry["enabled"]):
            raise _touchup.TouchupError(
                f"the staged {_row(staged)} {staged.get('name')!r} "
                f"reads {'disabled' if after else 'enabled'} after the "
                f"touch asked for "
                f"{'disabled' if entry['enabled'] else 'enabled'} - the "
                f"write cannot be stated as a carried edit. Nothing "
                f"further is filed.")
        if before is after:
            continue
        state = "on" if after else "off"
        passage = _span(staged["source_in_frame"],
                        staged["source_out_frame"])
        edits.append(_base(
            final, "enabled", staged, field="enabled",
            record=record, plan_version=plan_version,
            wording=(f"Switch {staged.get('name')!r} source {passage} "
                     f"on {_row(staged)} {state} "
                     f"(ren touch {journal_id})"),
            before=before, after=after))
    for edit in edits:
        edit["source"] = source
        edit["author"] = author
        edit["detected_at"] = detected_at
    return edits


def _plan_version(project_folder: str):
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    return (plan_provenance.read_provenance(review_dir) or {}).get(
        "plan_content_hash")


def edits_in_force(project_folder: str, final: str,
                   pending: list[dict]) -> tuple[list, dict]:
    """The ledger's active edits, updated by the newly pending records.

    Returns `(edits, superseded)`: a newer edit on the same item and
    property replaces the older one, and an item the editor ADDED back
    over a cut passage supersedes that cut - the editor reversed it.
    """
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    by_id = {edit["id"]: edit for edit in
             plan_provenance.carried_editor_edits(review_dir, final)}
    superseded = {}
    version = _plan_version(project_folder)
    for record in pending:
        derived, _uncarried = derive_edits(record, plan_version=version)
        for edit in derived:
            by_id[edit["id"]] = edit
        for change in record.get("changes") or ():
            if change["kind"] != "item_added":
                continue
            for edit in list(by_id.values()):
                if edit["kind"] == "cut" and _overlaps(edit, change["after"]):
                    superseded[edit["id"]] = (
                        f"the editor put {edit['name']!r} source "
                        f"{_span(edit['source_in_frame'], edit['source_out_frame'])}"
                        f" back (record {record.get('id')})")
                    by_id.pop(edit["id"])
    return list(by_id.values()), superseded


def _kept(edit: dict) -> dict:
    """A trim edit's identity, moved onto the source range it keeps."""
    return {**edit, "source_in_frame": edit["after"]["source_in_frame"],
            "source_out_frame": edit["after"]["source_out_frame"]}


def _refuse(final: str, problems: list[str]) -> None:
    raise EditorEditCarryRefused(
        f"REFUSING to replace {final!r}: the carried edits (editor "
        f"changes and Ren touch writes) cannot be mapped onto the staged timeline. "
        f"Nothing was renamed; the live timeline is still in the "
        f"project.\n"
        + "\n".join(problems)
        + f"\nTo accept this loss deliberately, pass "
          f"--accept-editor-changes {final!r}.")


def plan_application(edits: list[dict], staged: dict, final: str) -> dict:
    """What each edit does to `staged` (a full snapshot). Pure; may refuse."""
    items = list(staged.get("items") or ())
    plan = {"delete": [], "trim": [], "move": [], "set_enabled": [],
            "set_transform": [], "already_held": []}
    problems = []
    for edit in edits:
        passage = _span(edit["source_in_frame"], edit["source_out_frame"])
        exact = [item for item in items if _governs(edit, item)]
        if edit["kind"] in ("cut", "trim"):
            held = (edit["kind"] == "trim"
                    and [item for item in items
                         if _governs(_kept(edit), item)])
            partial = [item for item in items
                       if _overlaps(edit, item) and item not in exact
                       and item not in (held or ())]
            if partial:
                problems.append(
                    f"  {edit['kind']} {edit['row']} {edit['name']!r} "
                    f"source {passage}: staging plays part of it as "
                    + ", ".join(f"source {_span(*_source(item))} at record "
                                f"{_span(item.get('record_in'), item.get('record_out'))}"
                                for item in partial)
                    + " - the passage no longer maps onto one item")
                continue
            if held and not exact:
                plan["already_held"].append(edit["id"])
                continue
            if not exact:
                if edit["kind"] == "cut":
                    plan["already_held"].append(edit["id"])
                else:
                    problems.append(
                        f"  trim {edit['row']} {edit['name']!r} source "
                        f"{passage}: staging does not play that passage")
                continue
            if edit["kind"] == "trim" and len(exact) != 1:
                problems.append(
                    f"  trim {edit['row']} {edit['name']!r} source "
                    f"{passage}: staging plays it {len(exact)} times")
                continue
            plan["delete" if edit["kind"] == "cut" else "trim"].extend(
                {"edit": edit, "item": item} for item in exact)
            continue
        if len(exact) != 1:
            problems.append(
                f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                f"{passage}: staging plays that passage {len(exact)} "
                f"time(s), so the edit has no single item to land on"
                f"{_touch_suffix(edit)}")
            continue
        item = exact[0]
        if edit["kind"] == "move":
            plan["move"].append({"edit": edit, "item": item})
        elif edit["kind"] == "enabled":
            if item.get("enabled") is edit["after"]:
                plan["already_held"].append(edit["id"])
            else:
                plan["set_enabled"].append({"edit": edit, "item": item})
        else:
            key = edit["field"].split(".", 1)[1]
            if _same((item.get("transform") or {}).get(key), edit["after"]):
                plan["already_held"].append(edit["id"])
            else:
                plan["set_transform"].append({"edit": edit, "item": item,
                                              "key": key})
    problems.extend(_ripple_collateral(plan["delete"], items))
    problems.extend(_trim_collateral(plan["trim"], items))
    if problems:
        _refuse(final, problems)
    return plan


def _ripple_collateral(deletes: list, items: list) -> list[str]:
    """What a rippled cut would take from items it does not name.

    Measured on Resolve 21.1 (scratch project, 2026-10-01): a ripple
    `DeleteClips` removes the deleted span's TIME from every row - an
    overlay after it moves earlier, and an overlay straddling it loses
    the overlapping frames off its tail. Nothing in the API can split
    or move an item instead, so a rippled cut is carried only where
    every item it overlaps is itself being cut; anything else refuses
    here, before a write, naming the item that would change.
    """
    doomed = {id(step["item"]) for step in deletes}
    problems = []
    for step in deletes:
        if not step["edit"].get("ripple"):
            continue
        start, end = step["item"]["record_in"], step["item"]["record_out"]
        for item in items:
            if id(item) in doomed:
                continue
            if item.get("record_in") < end and start < item.get("record_out"):
                problems.append(
                    f"  cut {step['edit']['row']} {step['edit']['name']!r} "
                    f"source {_span(step['edit']['source_in_frame'], step['edit']['source_out_frame'])}"
                    f" closes its gap across every row, and that would "
                    f"trim or remove {_row(item)} {item.get('name')!r} at "
                    f"record {_span(item.get('record_in'), item.get('record_out'))}"
                    f" - a rippled cut over another row's item is not "
                    f"carried")
                doomed.add(id(item))
    return problems


def _trim_amount(edit: dict) -> int:
    return int(edit["after"]["head"]) + int(edit["after"]["tail"])


def _trim_collateral(trims: list, items: list) -> list[str]:
    """What a rippled trim would shorten that it does not name.

    `composed_edit.plan_ripple` shortens every item whose span holds
    the trim's end (`record_in < end <= record_out`) and shifts every
    one after it. An item it would shorten that is not itself trimmed
    by the same amount at the same frame - a music bed, a caption
    ending with the line - refuses, named.
    """
    groups: dict = {}
    for step in trims:
        if step["edit"].get("ripple"):
            key = (step["item"]["record_out"], _trim_amount(step["edit"]))
            groups.setdefault(key, set()).add(id(step["item"]))
    problems = []
    for (end, amount), members in sorted(groups.items()):
        for item in items:
            if id(item) in members:
                continue
            if item.get("record_in") < end <= item.get("record_out"):
                problems.append(
                    f"  a trim ending at record {end:,} closes its "
                    f"{amount}-frame gap across every row, and that would "
                    f"shorten {_row(item)} {item.get('name')!r} at record "
                    f"{_span(item.get('record_in'), item.get('record_out'))}"
                    f" - a rippled trim under another row's item is not "
                    f"carried")
    for step in trims:
        clashing = [(end, amount) for end, amount in groups
                    if end == step["item"]["record_out"]
                    and amount != _trim_amount(step["edit"])]
        if clashing:
            problems.append(
                f"  trims ending at record {step['item']['record_out']:,} "
                f"remove different lengths ({sorted(a for _e, a in clashing)})"
                f" - one ripple cannot close both")
    return problems


def _handle(rows: list, item: dict):
    """The live handle `item` (a snapshot entry) was read from."""
    wanted_id = str(item.get("unique_id") or "")
    for row in rows:
        if (row["type"] != item.get("track_type")
                or row["index"] != item.get("track_index")):
            continue
        for handle in row["items"]:
            if wanted_id:
                try:
                    handle_id = str(handle.GetUniqueId() or "")
                except Exception:  # noqa: BLE001 - unreadable is not this one
                    handle_id = ""
                if handle_id == wanted_id:
                    return handle
                continue
            if (handle.GetStart() == item.get("record_in")
                    and handle.GetName() == item.get("name")):
                return handle
    raise EditorEditCarryRefused(
        f"staged item {item.get('name')!r} on {_row(item)} at record "
        f"{item.get('record_in')} could not be found again to write to; "
        f"the staging changed under the carry.")


def apply_plan(staged_timeline, project, plan: dict, final: str) -> list:
    """Write a plan's cuts and in-place edits. Returns what was written."""
    from library.tools import composed_edit, reel_read
    from library.tools.resolve_lock import cursor_excursion

    written = []
    with cursor_excursion(project, staged_timeline,
                          f"carry editor edits onto {final}"):
        def rows():
            return reel_read.live_items(staged_timeline,
                                        resolve_project=project)

        current = rows()
        for step in plan["set_enabled"]:
            handle = _handle(current, step["item"])
            handle.SetClipEnabled(bool(step["edit"]["after"]))
            written.append(step["edit"]["id"])
        for step in plan["set_transform"]:
            handle = _handle(current, step["item"])
            composed_edit.set_properties(
                handle, {step["key"]: step["edit"]["after"]})
            written.append(step["edit"]["id"])
        # Lifts first, all at once: they move nothing. Rippled cuts
        # last, latest record span first, one span per call, so no
        # delete shifts an item a later one still has to find.
        lifts = [step for step in plan["delete"]
                 if not step["edit"].get("ripple")]
        if lifts:
            staged_timeline.DeleteClips(
                [_handle(current, step["item"]) for step in lifts], False)
            written.extend(step["edit"]["id"] for step in lifts)
        spans: dict = {}
        for step in plan["delete"]:
            if step["edit"].get("ripple"):
                key = (step["item"]["record_in"], step["item"]["record_out"])
                spans.setdefault(key, []).append(step)
        for key in sorted(spans, reverse=True):
            current = rows()
            staged_timeline.DeleteClips(
                [_handle(current, step["item"]) for step in spans[key]],
                True)
            written.extend(step["edit"]["id"] for step in spans[key])
    return written


# ── The composed phases: trims and moves ────────────────────────────


class _CompFreeRederiver:
    """The comp generator's seat for trims of items that carry no comp.

    `composed_edit` asks for a rederiver whenever a played length
    changes. Where no trimmed item carries a comp there is nothing to
    re-derive; a trimmed item that DOES carry one refuses here, so the
    real comp pass is the only route for it.
    """

    def reachable_reason(self, changes) -> str | None:
        with_comps = [f"{c.row}[{c.item_index}]" for c in changes
                      if c.played_length_changes and c.comp_count]
        if with_comps:
            return (f"{with_comps} carry a comp and this carry has no "
                    f"recorded fusion manifest to re-derive it from")
        return None

    def rederive(self, changes) -> dict:
        return {"ran": True, "ok": True, "comp_pass": "skipped",
                "why": "no trimmed item carries a comp"}

    def expects_comp(self, row: str, record_frame: int):
        return False


def _locate(tracks: list, edit: dict, source=None):
    """`(track, index, clip)` on staging playing `source` (default: the
    edit's own passage), or None."""
    from library.tools import composed_edit as _ce

    wanted = source or (edit["source_in_frame"], edit["source_out_frame"])
    hits = []
    for track in tracks:
        row = _guard.row_key(track["type"], track["name"])
        if row != edit["row"]:
            continue
        for index, clip in enumerate(track.get("clips") or ()):
            if (_guard.item_source_identity(
                    {**clip, "track_name": track["name"]})
                    == edit["source_identity"]
                    and (clip.get("source_in_frame"),
                         clip.get("source_out_frame")) == tuple(wanted)):
                hits.append((track, index, clip))
    if len(hits) != 1:
        return None
    track, index, clip = hits[0]
    return {"row": _ce.row_label(track["type"], int(track["index"])),
            "track": track, "index": index, "clip": clip}


def _work_dir(project_folder: str, timeline_name: str, phase: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout

    slug = "".join(ch if ch.isalnum() else "_" for ch in timeline_name)
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "editor_carry", slug, phase)
    os.makedirs(path, exist_ok=True)
    return path


def _composed(project_folder: str, project, staged, tracks: list,
              changes: list, rederiver, phase: str, final: str) -> dict:
    """Run one composed edit on staging, grades carried from a reference.

    A re-placed item comes back with one colour node, enabled, and no
    clip colour, so each is put back from the read taken before the
    edit: the grade from a reference duplicate of staging (deleted
    after), the enabled state and clip colour off `tracks`.
    """
    from library.tools import composed_edit as _ce
    from library.tools import reel_retirement
    from library.tools.reel_build import BACKUP_SUFFIX
    from library.tools.resolve_lock import assert_current_timeline

    pool = project.GetMediaPool()
    reference_name = f"{staged.GetName()}{BACKUP_SUFFIX}"
    reference = staged.DuplicateTimeline(reference_name)
    if reference is None or reference.GetName() != reference_name:
        raise EditorEditCarryRefused(
            f"REFUSING to replace {final!r}: Resolve would not duplicate "
            f"the staging as {reference_name!r} to carry grades from. "
            f"Nothing was renamed.")
    try:
        assert_current_timeline(project, staged)
        by_frame = {}
        for row, handles in _ce._rows_of(reference).items():
            for handle in handles:
                by_frame[(row, _ce._read(handle, "GetStart", None))] = handle
        grade_sources = {
            (change.row, change.item_index):
                by_frame[(change.row, change.previous_record)]
            for change in changes
            if (change.row, change.previous_record) in by_frame}
        work = _work_dir(project_folder, staged.GetName(), phase)
        receipt = _ce.apply_composed_edit(
            timeline=staged, media_pool=pool, changes=changes,
            comp_dir=os.path.join(work, "comps"),
            withheld_dir=os.path.join(work, "withheld"),
            rederiver=rederiver, grade_sources=grade_sources,
            link_rows={}, picture_row="V1")
    finally:
        reel_retirement.delete_backups(project, pool,
                                       {reference_name: reference})
    before = {(_ce.row_label(track["type"], int(track["index"])), index): clip
              for track in tracks
              for index, clip in enumerate(track.get("clips") or ())}
    rows = _ce._rows_of(staged)
    for change in changes:
        clip = before[(change.row, change.item_index)]
        landed = [handle for handle in rows.get(change.row) or ()
                  if _ce._read(handle, "GetStart", None)
                  == change.record_frame]
        if len(landed) != 1:
            continue
        if clip.get("enabled") is False:
            landed[0].SetClipEnabled(False)
        if clip.get("clip_color"):
            landed[0].SetClipColor(clip["clip_color"])
    return {"plan": receipt.plan, "verified": receipt.verified,
            "rederived": receipt.rederived}


def _post_trim_manifest(manifest: dict, tracks: list, trims: list) -> dict:
    """The recorded manifest with each trimmed spec naming its kept range.

    The comp pass keys keyframes to the spec's `source_in`/`source_out`
    (seconds); a trimmed clip's spec moves by the frames the trim took,
    at the rate the spec's own span implies.
    """
    import copy

    out = copy.deepcopy(manifest)
    rows = out.get("tracks") or {}
    for step in trims:
        row = step["row"]
        if not row.startswith("V"):
            continue
        clips = (rows.get(row) or {}).get("clips") or []
        if step["index"] >= len(clips):
            continue
        spec = clips[step["index"]]
        start, end = spec.get("source_in"), spec.get("source_out")
        # The played duration, not the source-end getter, which a
        # frame of rounding separates from it.
        frames = int(step["clip"].get("duration") or 0)
        if not (isinstance(start, (int, float)) and isinstance(
                end, (int, float)) and end > start and frames > 0):
            continue
        rate = frames / (end - start)
        spec["source_in"] = start + step["edit"]["after"]["head"] / rate
        spec["source_out"] = end - step["edit"]["after"]["tail"] / rate
    return out


def _apply_trims(project_folder: str, project, staged, edits: list,
                 final: str) -> list:
    """Trim each staged passage to the range the editor kept."""
    import copy

    from library.tools import composed_edit as _ce
    from library.tools import reel_read, reel_touchup

    tracks = reel_read.read_tracks(staged)
    steps = []
    for edit in edits:
        found = _locate(tracks, edit)
        if found is None:
            if _locate(tracks, edit, (edit["after"]["source_in_frame"],
                                      edit["after"]["source_out_frame"])):
                continue
            _refuse(final, [(
                f"  trim {edit['row']} {edit['name']!r}: the "
                f"staged passage moved before it was trimmed")])
        steps.append({**found, "edit": edit})
    if not steps:
        return []
    virtual = copy.deepcopy(tracks)

    def clip_of(step):
        for track in virtual:
            if _ce.row_label(track["type"], int(track["index"])) == \
                    step["row"]:
                return track["clips"][step["index"]]
        raise KeyError(step["row"])

    groups: dict = {}
    for step in steps:
        if step["edit"].get("ripple"):
            key = (int(step["clip"]["record_out"]),
                   _trim_amount(step["edit"]))
            groups.setdefault(key, []).append(step)
    try:
        for (end, amount), members in sorted(groups.items(), reverse=True):
            cut = int(clip_of(members[0])["record_out"])
            for change in _ce.plan_ripple(virtual, cut, -amount):
                clip = clip_of({"row": change.row,
                                "index": change.item_index})
                clip["record_in"] = change.record_frame
                clip["duration"] = change.duration
                clip["record_out"] = change.record_frame + change.duration
    except _ce.ComposedEditError as unplannable:
        _refuse(final, [f"  the trims could not be planned: {unplannable}"])
    for step in steps:
        clip = clip_of(step)
        head = int(step["edit"]["after"]["head"])
        if not step["edit"].get("ripple"):
            clip["record_in"] = int(clip["record_in"]) + head
            clip["duration"] = int(clip["duration"]) - _trim_amount(
                step["edit"])
            clip["record_out"] = clip["record_in"] + clip["duration"]
        if clip.get("left_offset") is None:
            _refuse(final, [(
                f"  trim {step['edit']['row']} "
                f"{step['edit']['name']!r}: its source trim "
                f"(left offset) is unreadable")])
        clip["left_offset"] = int(clip["left_offset"]) + head
    changes = []
    for track, moved in zip(tracks, virtual):
        for index, (old, new) in enumerate(zip(track.get("clips") or (),
                                               moved.get("clips") or ())):
            if (old["record_in"], old["duration"], old.get("left_offset")) \
                    == (new["record_in"], new["duration"],
                        new.get("left_offset")):
                continue
            changes.append(_ce.ItemChange(
                track_type=track["type"], track_index=int(track["index"]),
                item_index=index, record_frame=int(new["record_in"]),
                duration=int(new["duration"]),
                left_offset=int(new.get("left_offset") or 0),
                previous_record=int(old["record_in"]),
                previous_duration=int(old["duration"]),
                how=(_ce.EXTEND if new["duration"] != old["duration"]
                     else _ce.SHIFT),
                comp_count=_ce.treatment_comps(old),
                right_offset=old.get("right_offset"),
                name=old.get("name", "")))
    trimmed = [change for change in changes
               if change.played_length_changes and change.comp_count]
    if trimmed:
        manifest = reel_touchup.recorded_fusion_manifest(
            project_folder, staged.GetName())
        if manifest is None:
            _refuse(final, [(
                f"  {[f'{c.row}[{c.item_index}]' for c in trimmed]}"
                f" carry a Fusion comp and staging has no "
                f"recorded fusion manifest to re-derive it "
                f"from")])
        try:
            reel_touchup.check_manifest_matches(manifest, tracks)
        except reel_touchup.TouchupRefused as stale:
            _refuse(final, [f"  {stale}"])
        rederiver = _ce.ReelLookRederiver(
            _post_trim_manifest(manifest, tracks, steps), project_folder,
            project.GetName(), staged.GetName())
        rederiver.expected_row_counts = {
            _ce.row_label(track["type"], int(track["index"])):
                len(track.get("clips") or ()) for track in tracks}
    else:
        rederiver = _CompFreeRederiver()
    _composed(project_folder, project, staged, tracks, changes, rederiver,
              "trims", final)
    return [step["edit"]["id"] for step in steps]


def _apply_moves(project_folder: str, project, staged, edits: list,
                 final: str) -> list:
    """Put each moved item back over the picture the editor put it over."""
    from library.tools import composed_edit as _ce
    from library.tools import reel_read

    tracks = reel_read.read_tracks(staged)
    picture = next((track for track in tracks
                    if track["type"] == "video" and int(track["index"]) == 1),
                   {"clips": []})
    changes, written, problems = [], [], []
    for edit in edits:
        found = _locate(tracks, edit)
        if found is None:
            problems.append(f"  move {edit['row']} {edit['name']!r}: "
                            f"staging no longer plays that passage once")
            continue
        target = _anchor_frame(picture["clips"], edit["after"])
        if target is None:
            problems.append(
                f"  move {edit['row']} {edit['name']!r}: the picture it "
                f"sat over (source frame "
                f"{edit['after']['anchor_source_frame']:,}) does not play "
                f"on staging's V1")
            continue
        clip = found["clip"]
        if int(clip["record_in"]) == target:
            continue
        duration = int(clip["duration"])
        for index, other in enumerate(found["track"].get("clips") or ()):
            if index != found["index"] and \
                    int(other["record_in"]) < target + duration and \
                    target < int(other["record_out"]):
                problems.append(
                    f"  move {edit['row']} {edit['name']!r} to record "
                    f"{target:,}: {other.get('name')!r} already sits at "
                    f"{_span(other['record_in'], other['record_out'])}")
        changes.append(_ce.ItemChange(
            track_type=found["track"]["type"],
            track_index=int(found["track"]["index"]),
            item_index=found["index"], record_frame=target,
            duration=duration, left_offset=int(clip.get("left_offset") or 0),
            previous_record=int(clip["record_in"]),
            previous_duration=duration, how=_ce.SHIFT,
            comp_count=_ce.treatment_comps(clip),
            right_offset=clip.get("right_offset"),
            name=clip.get("name", "")))
        written.append(edit["id"])
    if problems:
        _refuse(final, problems)
    if changes:
        _composed(project_folder, project, staged, tracks, changes, None,
                  "moves", final)
    return written


def _anchor_frame(picture_clips: list, anchor: dict):
    """The record frame where V1 plays the anchor's source frame."""
    frame = anchor["anchor_source_frame"]
    for clip in picture_clips:
        identity = clip.get("source_identity") or \
            _guard.item_source_identity(
                {**clip, "track_name": clip.get("track_name", "")})
        if identity != anchor["anchor_source_identity"]:
            continue
        start, end = clip.get("source_in_frame"), clip.get("source_out_frame")
        if isinstance(start, int) and isinstance(end, int) and \
                start <= frame < end:
            return int(clip["record_in"]) + frame - start
    return None


def carry_editor_edits(project_folder: str, final: str, project,
                       staged_timeline, read_staged,
                       detection: dict, *, accept: bool = False) -> dict:
    """Apply every edit in force for `final` onto the staging timeline.

    `detection` is `reel_replace_guard.detect_editor_changes` over the
    live and staged reads; `read_staged()` returns a full snapshot of
    staging as it stands now, and is called only when an edit is in
    force. Returns the carry report the promotion keeps and hands to
    `verify_carried_edits` and `record_after_promotion`. With `accept`,
    an edit that cannot be mapped is dropped from this carry (and later
    superseded) rather than refusing.
    """
    from library.tools.resolve_lock import cursor_excursion

    edits, superseded = edits_in_force(
        project_folder, final, detection.get("pending") or [])
    if not edits:
        return {"edits": [], "superseded": superseded, "written": [],
                "already_held": []}
    staged_snapshot = read_staged()
    try:
        plan = plan_application(edits, staged_snapshot, final)
    except EditorEditCarryRefused:
        if not accept:
            raise
        mappable, dropped = [], {}
        for edit in edits:
            try:
                plan_application([edit], staged_snapshot, final)
                mappable.append(edit)
            except EditorEditCarryRefused:
                dropped[edit["id"]] = (
                    f"--accept-editor-changes {final}: could not be mapped")
        edits, superseded = mappable, {**superseded, **dropped}
        plan = plan_application(edits, staged_snapshot, final)
    written = []
    # Each phase re-plans off a fresh read: a re-placed item is a new
    # object, and a cut moves everything after it.
    with cursor_excursion(project, staged_timeline,
                          f"carry editor edits onto {final}"):
        if plan["trim"]:
            written += _apply_trims(
                project_folder, project, staged_timeline,
                [step["edit"] for step in plan["trim"]], final)
        cuts = [edit for edit in edits if edit["kind"] == "cut"]
        if cuts:
            cut_plan = plan_application(cuts, read_staged(), final)
            written += apply_plan(staged_timeline, project, cut_plan, final)
        if plan["move"]:
            written += _apply_moves(
                project_folder, project, staged_timeline,
                [step["edit"] for step in plan["move"]], final)
        in_place = [edit for edit in edits
                    if edit["kind"] in ("enabled", "transform")]
        if in_place:
            in_place_plan = plan_application(in_place, read_staged(), final)
            written += apply_plan(staged_timeline, project, in_place_plan,
                                  final)
    return {"edits": edits, "superseded": superseded,
            "written": written, "already_held": plan["already_held"]}


def verify_carried_edits(report: dict, staged_after: dict,
                         final: str) -> None:
    """Refuse unless every carried edit holds on a fresh read of staging."""
    items = list(staged_after.get("items") or ())
    missed = []
    for edit in report.get("edits") or ():
        passage = _span(edit["source_in_frame"], edit["source_out_frame"])
        if edit["kind"] == "cut":
            still = [item for item in items if _overlaps(edit, item)]
            if still:
                missed.append(
                    f"  cut {edit['row']} {edit['name']!r} source {passage} "
                    f"still plays at record " + ", ".join(
                        _span(item.get("record_in"), item.get("record_out"))
                        for item in still))
            continue
        if edit["kind"] == "trim":
            kept = _kept(edit)
            exact = [item for item in items if _governs(kept, item)]
            beyond = [item for item in items
                      if _overlaps(edit, item) and item not in exact]
            if len(exact) != 1 or beyond:
                missed.append(
                    f"  trim {edit['row']} {edit['name']!r} source "
                    f"{passage} to {_span(kept['source_in_frame'], kept['source_out_frame'])}: "
                    f"staging plays "
                    + (", ".join(_span(*_source(item))
                                 for item in exact + beyond) or "none of it"))
            continue
        exact = [item for item in items if _governs(edit, item)]
        if len(exact) != 1:
            missed.append(
                f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                f"{passage}: staging now plays it {len(exact)} time(s)"
                f"{_touch_suffix(edit)}")
            continue
        if edit["kind"] == "move":
            picture = [item for item in items
                       if item.get("track_type") == "video"
                       and item.get("track_index") == 1]
            target = _anchor_frame(picture, edit["after"])
            if target != exact[0].get("record_in"):
                missed.append(
                    f"  move {edit['row']} {edit['name']!r} source "
                    f"{passage}: wanted over picture source frame "
                    f"{edit['after']['anchor_source_frame']:,} (record "
                    f"{target}), staging has it at record "
                    f"{exact[0].get('record_in')}")
            continue
        if edit["kind"] == "enabled":
            got = exact[0].get("enabled")
        else:
            got = (exact[0].get("transform") or {}).get(
                edit["field"].split(".", 1)[1])
        if not _same(got, edit["after"]):
            missed.append(
                f"  {edit['field']} on {edit['row']} {edit['name']!r} "
                f"source {passage}: wanted {edit['after']!r}, staging "
                f"reads {got!r}{_touch_suffix(edit)}")
    if missed:
        raise EditorEditCarryRefused(
            f"REFUSING to replace {final!r}: the carried edits "
            f"(editor changes and Ren touch writes) did not land on the staged "
            f"timeline. Nothing was renamed; the live timeline is still "
            f"in the project.\n"
            + "\n".join(missed))


def record_after_promotion(project_folder: str, final: str,
                           report: dict | None, promoted: dict, *,
                           act: str) -> dict:
    """File the edits in force, judged on the promoted read.

    Called once the promoted timeline is read back. Every edit this
    promotion carried, and every edit already on the ledger, that the
    promoted timeline HOLDS stays active; one it does not hold was
    changed by this Ren act on purpose (a touch re-enabling a graphic),
    so it is superseded with the act named - the next rebuild must not
    undo the act.
    """
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    report = report or {}
    superseded = dict(report.get("superseded") or {})
    edits = {edit["id"]: edit for edit in
             plan_provenance.carried_editor_edits(review_dir, final)
             if edit["id"] not in superseded}
    carried_now = {edit["id"] for edit in report.get("edits") or ()}
    edits.update({edit["id"]: edit for edit in report.get("edits") or ()})
    now = datetime.now(UTC).isoformat(timespec="seconds")
    held = []
    for edit in edits.values():
        try:
            verify_carried_edits({"edits": [edit]}, promoted, final)
        except EditorEditCarryRefused:
            superseded[edit["id"]] = f"changed by {act}"
            if edit["id"] in carried_now:
                held.append({**edit, "status": "superseded",
                             "superseded_at": now,
                             "superseded_by": f"changed by {act}"})
            continue
        held.append({**edit, "last_carried_at": now,
                     "last_carried_by": act})
    plan_provenance.record_carried_edits(review_dir, final, held,
                                         superseded=superseded)
    return {"carried": sorted(edit["id"] for edit in held
                              if edit.get("status") == "active"),
            "superseded": superseded}


def describe(report: dict | None) -> list[str]:
    """One plain line per edit, for a promotion's printed summary."""
    if not report:
        return []
    written = set(report.get("written") or ())
    return [f"{edit['wording']} "
            f"({'applied' if edit['id'] in written else 'already held'})"
            for edit in report.get("edits") or ()]

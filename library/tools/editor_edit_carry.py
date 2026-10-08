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
and keeps every source frame. A Semantic motion graphic instead uses
the source graphic identity recorded by its plan, because the rendered
file name and hash can change between builds.

- `cut`: a source passage the editor removed (Reel 7: Craig
  25,263-25,374 and Akshita 60,745-60,979). Staging must play no frame
  of it on that row. `ripple` records whether the editor closed the gap
  (items after the cut moved earlier by its length), and the staging
  delete does the same - which in Resolve removes that TIME from every
  row (`_ripple_collateral`), so a rippled cut that another row's item
  overlaps refuses rather than trimming that item.
- `split`: an overlay split into source-ordered, one-frame-per-record
  pieces. The source window must partition exactly; staging places the
  fragments before the removed gaps are cut. Retimed, duplicated or
  otherwise ambiguous fragments refuse with the source ranges
  (`_split_partition`, `_apply_splits`).
- `trim`: a passage the editor shortened at its head, its tail or both;
  staging plays only the kept source range. Rippled or lifted, as the
  editor did it. Carried through `composed_edit` - the delete and
  re-place Resolve allows - with the comp pass re-run over a manifest
  whose trimmed specs name the kept range (`_apply_trims`).
- `extend`: a one-to-one source-window extension that keeps its start
  and adds frames only at the tail. The incoming item must prove the
  source headroom before Resolve is changed. Other extensions refuse.
- `move`: an item that kept its source but sits somewhere else, beyond
  what the editor's ripples explain. Its place is stated as the picture
  source frame playing on V1 at its new start, or as an offset from the
  timeline end when no picture covers it (`_apply_moves`). Moving an
  item on V1 is a reorder of the picture and is not carried.
- `enabled`: an item switched off or on.
- `transform`: one Edit-page transform property (Pan, Tilt, Zoom...)
  the editor set on an item. Pan and Tilt are carried only when both
  snapshots record the same project-resolution unit epoch; otherwise
  that framing change refuses rather than carrying a possible rebase.

Ripples are read off the record's own before and after reads
(`_shift_model`): an item that kept its source moved by exactly the
lengths of the rippled cuts and trims before it, and an edit is
rippled when the items after it say so. A shift nothing explains is a
move. Anything else (an added item, a grade, an unsupported source
extension, a marker the marker carry cannot place) stays step one's
refusal, named with the source ranges and the next safe action.

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
the source ranges, nothing written. It then writes in phases, each
re-planned off a fresh read because a re-placed item is a new object:
splits, trims, cuts, moves, then the in-place enabled and transform
 writes. `verify_carried_edits` judges the result on a fresh read of
 staging, in source ranges, never in row counts. A write's return value
 is never the verdict.

 The ledger is keyed by timeline NAME, so it serves every timeline
 that is promoted in place: a built reel (`reel_touchup`) and the
 master timeline (`master_touchup`) alike. A master touch-up's
 in-place writes are filed here under the master's name at promotion
 time, so the next rebuild carries them exactly like the editor's own.

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
_PROJECT_UNIT_TRANSFORMS = frozenset({"Pan", "Tilt"})
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


def _is_still_read(item: dict) -> bool:
    """A still as a read reports it: a zero-length source range.

    Resolve gives an image item one source frame and reads back its
    start and end as the same frame (Reel 23's post header: `39..39`
    with a left offset of 86,313), so a still is never told apart from
    another still by the source passage it plays.
    """
    start, end = _source(item)
    return isinstance(start, int) and start == end


def _replaced_still(edit: dict, items: list[dict]) -> list[dict]:
    """The still that replaced the one an in-place edit names.

    Reel 23, 2026-10-05: a rebuild re-renders the post header under a
    new file and media pool item (`post_header_7b7411c39f_tight.png` ->
    `..._60bd7f7ded_tight.png`), so the editor's Pan and Tilt on the old
    still match nothing by identity, and a still has no source passage
    to match by. The edit lands on the still on the same row spanning
    the same record frames the editor's did; none or several match
    nothing, and the edit refuses by name as before.
    """
    if edit["kind"] not in ("transform", "enabled") or not \
            _is_still_read(edit):
        return []
    span = (edit.get("record_in_when_detected"),
            edit.get("record_out_when_detected"))
    if not all(isinstance(frame, int) for frame in span):
        return []
    return [item for item in items
            if _row(item) == edit["row"] and _is_still_read(item)
            and (item.get("record_in"), item.get("record_out")) == span]


def _governs(edit: dict, item: dict) -> bool:
    """`item` is the source passage or planned graphic `edit` names."""
    if _row(item) != edit["row"]:
        return False
    graphic_identity = edit.get("graphic_identity")
    if graphic_identity:
        return item.get("graphic_identity") == graphic_identity
    return (item.get("source_identity") == edit["source_identity"]
            and _source(item) == (edit["source_in_frame"],
                                  edit["source_out_frame"]))


def _edit_source_file(edit: dict) -> str:
    """The source file the edited item played, as a path.

    An edit recorded after 2026-10-06 carries `source_file`; an older
    one names the file by its `file:` identity, which IS the path. A
    `media:` identity names a pool item, not a file, so an edit that
    has only that matches nothing here.
    """
    source_file = str(edit.get("source_file") or "")
    if source_file:
        return source_file
    identity = str(edit.get("source_identity") or "")
    return identity[len("file:"):] if identity.startswith("file:") else ""


def _file_items(edit: dict, items: list[dict]) -> list[dict]:
    """Staged items playing `edit`'s source FILE over its range on its row.

    The fallback when no staged item matches the recorded media
    identity: the same source file under two pool items (an accidental
    duplicate import) plays the same range under a different identity,
    and the edit is about the passage, not the pool item. A planned
    graphic matches by `graphic_identity`, never by file.
    """
    if edit.get("graphic_identity"):
        return []
    wanted = _edit_source_file(edit)
    if not wanted:
        return []
    return [item for item in items
            if _row(item) == edit["row"]
            and str(item.get("source_file") or "") == wanted
            and _source(item) == (edit["source_in_frame"],
                                  edit["source_out_frame"])]


def _basename_span_items(edit: dict, items: list[dict]) -> list[dict]:
    """Staged items playing the edit's file NAME over its range on its row.

    Last resort, and only for edits that record no file: edits recorded
    before `source_file` was carried name a pool identity (`media:`), so
    when pool churn deletes that item neither identity nor file matching
    can reach them (measured 2026-10-06, Reel 13: the recorded `media:`
    item is gone from a pool holding eight copies of the file, and the
    rebuild binds whichever lists first). An edit that names its file
    never reaches here - a staged item under the same name but another
    file (a re-rendered touch target) must still refuse. A bare basename
    would happily pair another reel's same-named file - so the row, the
    exact source span AND uniqueness are all required: one staged item
    on the row playing this basename over this span is the passage
    (identical file-frame timings under another name do not happen);
    two is ambiguity, zero is absent.
    """
    if _edit_source_file(edit):
        return []
    want_base = str(edit.get("name") or "").rsplit("/", 1)[-1]
    if not want_base:
        return []
    want_span = (edit.get("source_in_frame"), edit.get("source_out_frame"))
    if not all(isinstance(value, int) for value in want_span):
        return []
    return [item for item in items
            if _row(item) == edit["row"]
            and str(item.get("name") or "").rsplit("/", 1)[-1] == want_base
            and _source(item) == want_span]


def _landed_items(edit: dict, items: list[dict]) -> tuple[list, str | None]:
    """The staged items `edit` lands on, and why not when it has none.

    Media identity first - the recorded pool item. When no staged item
    matches it, the source FILE, by `_file_items`. Two file matches are
    one passage the edit cannot choose between, so they refuse by name;
    zero falls through to the caller's not-carried path.
    """
    exact = [item for item in items if _governs(edit, item)]
    if exact:
        return exact, None
    by_file = _file_items(edit, items)
    if len(by_file) > 1:
        names = ", ".join(repr(item.get("name")) for item in by_file)
        return [], (f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                    f"{_span(edit['source_in_frame'], edit['source_out_frame'])}: "
                    f"staging plays that passage as {len(by_file)} items "
                    f"under source file {_edit_source_file(edit)!r} "
                    f"({names}), so the edit has no single item to land "
                    f"on{_touch_suffix(edit)}")
    if not by_file:
        by_base = _basename_span_items(edit, items)
        if len(by_base) == 1:
            return by_base, None
    return by_file, None


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
    edit = {
        "id": _edit_id(final, kind, item, field),
        "timeline": final,
        "kind": kind,
        "field": field,
        "row": _row(item),
        "track_type": item.get("track_type"),
        "track_name": item.get("track_name"),
        "name": item.get("name"),
        "source_identity": item.get("source_identity"),
        "source_file": item.get("source_file"),
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
    if item.get("graphic_identity"):
        edit["graphic_identity"] = item["graphic_identity"]
    return edit


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
                    "anchor_source_file": str(item.get("source_file") or ""),
                    "anchor_source_frame":
                        item["source_in_frame"] + frame - item["record_in"]}
    return None


def caption_segment(item: dict) -> str | None:
    """A caption card's stable identity, or None if `item` is not one.

    `subtitle_segment_id.stable_prefix` of the card's name: speaker,
    source clip and source segment range, without the content digest. A
    rebuild that re-plans the captions (PR 1689's fragment merging)
    renders every card under a new digest and a new media pool item, so
    the name and identity the editor's edit recorded no longer exist on
    staging while this prefix stands still. Several cards share one
    segment, so it narrows a match and never decides one alone.
    """
    from library.tools import subtitle_segment_id

    name = str(item.get("name") or "")
    if name.lower().endswith(".mov"):
        name = name[:-4]
    if not subtitle_segment_id.is_segment_id(name):
        return None
    return subtitle_segment_id.stable_prefix(name)


def _picture_clips(snapshot: dict) -> list[dict]:
    return [item for item in snapshot.get("items") or ()
            if item.get("track_type") == "video"
            and item.get("track_index") == 1]


def _caption_span_target(edit: dict, picture: list[dict]):
    """The record span the editor gave a caption card, on `picture`."""
    after = edit["after"]
    start = _anchor_frame(picture, after["start_anchor"])
    last = _anchor_frame(picture, after["last_anchor"])
    if start is None or last is None or last < start:
        return None
    return start, last + 1


def _caption_span_card(edit: dict, items: list[dict]) -> list[dict]:
    """The re-planned cards that stand where the editor's card stood.

    Same row, same caption segment, and starting over the same picture
    frame the edited card started over before the editor touched it.
    """
    start = _anchor_frame(_picture_clips({"items": items}),
                          edit["before"]["start_anchor"])
    return [item for item in items
            if _row(item) == edit["row"]
            and caption_segment(item) == edit["caption_segment"]
            and start is not None and item.get("record_in") == start]


def _caption_span_held(edit: dict, items: list[dict]) -> list[dict]:
    """Cards of the edit's segment already at the editor's span."""
    target = _caption_span_target(edit, _picture_clips({"items": items}))
    if target is None:
        return []
    return [item for item in items
            if _row(item) == edit["row"]
            and caption_segment(item) == edit["caption_segment"]
            and (item.get("record_in"), item.get("record_out")) == target]


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
        if event.get("ripple_hint"):
            event["ripple"] = True
            prior += event["amount"]
            continue
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
            container["ripple_hint"] = (container.get("ripple_hint", False)
                                        or event.get("ripple_hint", False))
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


def _unit_resolution(snapshot: dict) -> tuple[int, int] | None:
    raw = ((snapshot.get("metadata") or {})
           .get("transform_unit_resolution"))
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        width, height = (int(raw[0]), int(raw[1]))
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _transform_unit_mismatch(record: dict) -> str:
    before = _unit_resolution(record.get("before_snapshot") or {})
    after = _unit_resolution(record.get("after_snapshot") or {})
    if before is None or after is None:
        return ("the before or after snapshot has no readable project "
                "resolution for its Pan/Tilt values")
    if before != after:
        return (f"the project resolution changed from "
                f"{before[0]}x{before[1]} to "
                f"{after[0]}x{after[1]}")
    return ""


def _split_partition(old: dict, partners: list[dict]) -> list[dict] | None:
    """Return a verified source partition, including the removed gaps.

    This narrow shape can be rebuilt as source fragments and then the
    named gaps can take part in the same rippled cuts as speech. Anything
    that changes speed, duplicates source, or moves a fragment on its own
    needs a different edit model and remains a refusal.
    """
    start, end = _source(old)
    record_in, record_out = old.get("record_in"), old.get("record_out")
    if (not isinstance(start, int) or not isinstance(end, int)
            or not isinstance(record_in, int) or not isinstance(record_out, int)
            or end <= start or record_out - record_in != end - start
            or not partners):
        return None
    ordered = sorted(partners, key=lambda item: (
        item.get("source_in_frame", -1), item.get("source_out_frame", -1)))
    cursor = start
    removed_before = 0
    parts = []
    for item in ordered:
        part_start, part_end = _source(item)
        part_record, part_record_out = item.get("record_in"), item.get("record_out")
        if (not isinstance(part_start, int) or not isinstance(part_end, int)
                or not isinstance(part_record, int)
                or not isinstance(part_record_out, int)
                or part_start < cursor or part_end <= part_start
                or part_end > end
                or part_record_out - part_record != part_end - part_start):
            return None
        if part_start > cursor:
            gap_start, gap_end = cursor, part_start
            gap_record = record_in + gap_start - start
            parts.append({"source_in_frame": gap_start,
                          "source_out_frame": gap_end,
                          "record_in": gap_record,
                          "record_out": gap_record + gap_end - gap_start,
                          "removed": True})
            removed_before += gap_end - gap_start
        expected_record = record_in + part_start - start - removed_before
        if part_record != expected_record:
            return None
        parts.append({"source_in_frame": part_start,
                      "source_out_frame": part_end,
                      "record_in": record_in + part_start - start,
                      "record_out": record_in + part_end - start,
                      "after_record_in": part_record,
                      "after_record_out": part_record_out,
                      "removed": False})
        cursor = part_end
    if cursor < end:
        gap_start, gap_end = cursor, end
        gap_record = record_in + gap_start - start
        parts.append({"source_in_frame": gap_start,
                      "source_out_frame": gap_end,
                      "record_in": gap_record,
                      "record_out": gap_record + gap_end - gap_start,
                      "removed": True})
    if not any(not part["removed"] for part in parts):
        return None
    return parts


def _caption_span(final: str, old: dict, new: dict, record: dict,
                  plan_version) -> dict | str:
    """A caption card the editor re-spanned, or why it is not carried.

    Reel 13, 2026-10-05: the captain held nine of Akshita's cards a few
    frames longer - each tail extended, the next card's head trimmed to
    meet it - and left every word where it was. A rebuild re-plans the
    captions, so the card he edited is gone and a re-planned card of the
    same segment stands in its place, under a new name, often a few
    frames shorter. His intent is the card's SPAN ON SCREEN, so that is
    what is carried: the re-planned card is found by its segment and the
    picture frame it started over, and given the record span he set,
    stated as the picture frames under its first and last frame.

    Carried only while the card's content stays where the picture put it
    (record minus source is unchanged): a card he slid against its own
    words is a re-time, not a span, and refuses.
    """
    passage = _span(*_source(old))
    where = f"{_row(old)} {old.get('name')!r} source {passage}"
    fields = (old.get("record_in"), old.get("record_out"),
              new.get("record_in"), new.get("record_out"),
              new.get("source_in_frame"))
    if not all(isinstance(value, int) for value in fields):
        return f"{where} has no readable record span"
    if (new["record_in"] - new["source_in_frame"]
            != old["record_in"] - old["source_in_frame"]):
        return (f"{where} now plays {_span(*_source(new))} at record "
                f"{_span(new['record_in'], new['record_out'])} - the card "
                f"moved against its own words, which is a re-time and not "
                f"a span the editor held")
    before = record.get("before_snapshot") or {}
    after = record.get("after_snapshot") or {}
    start_anchor = _picture_anchor(before, old["record_in"])
    first = _picture_anchor(after, new["record_in"])
    last = _picture_anchor(after, new["record_out"] - 1)
    if start_anchor is None or first is None or last is None:
        return (f"{where} spans record "
                f"{_span(new['record_in'], new['record_out'])} where no "
                f"picture plays to anchor it")
    target = _span(new["record_in"], new["record_out"])
    return _base(
        final, "caption_span", old, field="record_span", record=record,
        plan_version=plan_version,
        wording=(f"Hold caption card {old.get('name')!r} on {_row(old)} "
                 f"from record {_span(old['record_in'], old['record_out'])}"
                 f" to {target}, over the same picture"),
        before={"record_in": old["record_in"],
                "record_out": old["record_out"],
                "start_anchor": start_anchor},
        after={"record_in": new["record_in"],
               "record_out": new["record_out"],
               "start_anchor": first, "last_anchor": last}) | {
        "caption_segment": caption_segment(old)}


def _ripple_held_rows(record: dict, event: dict) -> list[str]:
    """The rows whose item the editor's ripple ran under, untouched.

    Resolve's ripple trim moves what starts after the edit point and
    leaves an item that straddles it - a frame overlay, a header, a
    caption ending with the line - exactly where it was (Reel 13,
    2026-10-05: Frame, Post Header and a caption card held across the
    6-frame extension at record 1,497). `composed_edit.plan_ripple`
    instead EXTENDS a straddler, so a staged straddler is carried only on
    a row this records, and the replay leaves it alone as he did.
    """
    before = record.get("before_snapshot") or {}
    after = record.get("after_snapshot") or {}
    end = event["end"]
    edited = {_row(edit) for edit in event["edits"]}
    kept = {(_stable(item), item.get("record_in"), item.get("record_out"))
            for item in after.get("items") or ()}
    held = set()
    for item in before.get("items") or ():
        if _row(item) in edited or not isinstance(item.get("record_in"), int):
            continue
        if not item["record_in"] < end <= item.get("record_out"):
            continue
        if (_stable(item), item["record_in"], item["record_out"]) in kept:
            held.add(_row(item))
    return sorted(held)


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

    def event_for(old, amount, edit, *, ripple_hint=False):
        start, end = old.get("record_in"), old.get("record_out")
        if not isinstance(start, int) or not isinstance(end, int):
            return
        for event in events:
            # One passage cut from picture and sound is ONE event.
            if (event["start"], event["end"], event["amount"]) == (
                    start, end, amount):
                event["edits"].append(edit)
                event["ripple_hint"] = (event.get("ripple_hint", False)
                                        or ripple_hint)
                return
        events.append({"start": start, "end": end, "amount": amount,
                       "edits": [edit], "ripple_hint": ripple_hint})

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
        if len(partners) > 1:
            parts = _split_partition(old, [other["after"]
                                           for other in partners])
            if parts is not None:
                for other in partners:
                    used_added.add(id(other))
                split_edit = _base(
                    final, "split", old, field="source_window",
                    record=record, plan_version=plan_version,
                    wording=(f"Split {old.get('name')!r} on {_row(old)} "
                             f"into {sum(not part['removed'] for part in parts)} "
                             f"source segments, preserving the edited gaps"),
                    before={"source_in_frame": old["source_in_frame"],
                            "source_out_frame": old["source_out_frame"]},
                    after={"parts": parts})
                edits.append(split_edit)
                for part in parts:
                    if not part["removed"]:
                        continue
                    gap = {**old,
                           "source_in_frame": part["source_in_frame"],
                           "source_out_frame": part["source_out_frame"],
                           "record_in": part["record_in"],
                           "record_out": part["record_out"]}
                    passage = _span(*_source(gap))
                    cut = _base(
                        final, "cut", gap, field="source_window",
                        record=record, plan_version=plan_version,
                        wording=(f"Cut {old.get('name')!r} source "
                                 f"{passage} from {_row(old)}"),
                        before={"record_in": gap["record_in"],
                                "record_out": gap["record_out"],
                                "present": True},
                        after={"present": False})
                    edits.append(cut)
                    event_for(gap, part["source_out_frame"]
                              - part["source_in_frame"], cut,
                              ripple_hint=True)
                continue
            uncarried.append({
                "change": change,
                "why": (f"{_row(old)} {old.get('name')!r} source "
                        f"{_span(*_source(old))} now plays as "
                        + ", ".join(_span(*_source(other["after"]))
                                    for other in partners)
                        + " - these pieces do not form one source-ordered, "
                          "one-frame-per-record split; keep this artwork "
                          "change in the Ren plan or approve an explicit "
                          "fragment mapping")})
            continue
        if partners and caption_segment(old) is not None:
            used_added.add(id(partners[0]))
            span = _caption_span(final, old, partners[0]["after"], record,
                                 plan_version)
            if isinstance(span, str):
                uncarried.append({"change": change, "why": span})
            else:
                edits.append(span)
            continue
        if partners:
            used_added.add(id(partners[0]))
            kept_in, kept_out = _source(partners[0]["after"])
            old_in, old_out = _source(old)
            valid = (isinstance(kept_in, int)
                     and isinstance(kept_out, int)
                     and kept_in < kept_out)
            is_trim = (valid and old_in <= kept_in < kept_out <= old_out)
            is_extension = (valid and kept_in <= old_in < old_out <= kept_out
                            and (kept_in, kept_out) != (old_in, old_out))
            if not (is_trim or is_extension):
                uncarried.append({
                    "change": change,
                    "why": (f"{_row(old)} {old.get('name')!r} source "
                            f"{_span(*_source(old))} now plays "
                            f"{_span(kept_in, kept_out)} - this source "
                            f"window is not a one-to-one trim or extension")})
                continue
            head = kept_in - old["source_in_frame"]
            tail = old["source_out_frame"] - kept_out
            kind = "trim" if is_trim else "extend"
            if (kind == "extend"
                    and (head != 0
                         or partners[0]["after"].get("record_in")
                         != old.get("record_in"))):
                uncarried.append({
                    "change": change,
                    "why": (f"{_row(old)} {old.get('name')!r} source "
                            f"{_span(*_source(old))} expands to "
                            f"{_span(kept_in, kept_out)} with a moved head; "
                            "only a tail extension that keeps its start can "
                            "be carried safely")})
                continue
            edit = _base(
                final, kind, old, field="source_window", record=record,
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
            elif edit["kind"] in ("trim", "extend"):
                kept = _span(edit["after"]["source_in_frame"],
                             edit["after"]["source_out_frame"])
                verb = "Trim" if edit["kind"] == "trim" else "Extend"
                edit["wording"] = (f"{verb} {edit['name']!r} source "
                                   f"{passage} on {edit['row']} to "
                                   f"{kept}{gap}")

    for event in events:
        if not event["ripple"]:
            continue
        held = _ripple_held_rows(record, event)
        for edit in event["edits"]:
            if edit["kind"] in ("trim", "extend"):
                edit["ripple_held_rows"] = held

    after_snapshot = record.get("after_snapshot") or {}
    for before, after in pairs:
        shift = after["record_in"] - before["record_in"]
        if shift == _explained(events, before["record_in"]):
            # Moved by the ripples alone: carried with them, and the
            # guard reads it so (`ripple_moved`), even where the rebuild
            # re-rendered or re-timed the item it moved.
            for event in events:
                if (shift and event["ripple"]
                        and event["end"] <= before["record_in"]):
                    for edit in event["edits"]:
                        edit.setdefault("ripple_moved", []).append(
                            list(_guard.stable_item_key(before)))
            continue
        what = (f"{_row(after)} {after.get('name')!r} source "
                f"{_span(*_source(after))}")
        if after.get("track_type") == "video" and \
                after.get("track_index") == 1:
            uncarried.append({
                "change": {"kind": "item_changed", "before": before,
                           "after": after, "changed": {}},
                "why": (f"{what} moved {shift:+d} on the picture row - a "
                        "reordering V1 can change linked speech, captions "
                        "and story order. Ren cannot map that safely onto a "
                        "new build; keep the order in the plan or provide an "
                        "approved group reorder mapping")})
            continue
        anchor = _picture_anchor(after_snapshot, after["record_in"])
        if anchor is None:
            end_frame = (after_snapshot.get("timeline") or {}).get(
                "end_frame")
            if (isinstance(end_frame, int)
                    and after.get("record_out") == end_frame):
                anchor = {
                    "anchor_kind": "timeline_end",
                    "anchor_offset": after["record_in"] - end_frame,
                }
            else:
                uncarried.append({
                    "change": {"kind": "item_changed", "before": before,
                               "after": after, "changed": {}},
                    "why": (f"{what} moved to record {after['record_in']}, "
                            f"where no picture plays to anchor it and it "
                            "does not end with the timeline")})
                continue
        if anchor.get("anchor_kind") == "timeline_end":
            wording = (f"Move {after.get('name')!r} source "
                       f"{_span(*_source(after))} on {_row(after)} "
                       f"{anchor['anchor_offset']} frames from timeline end")
        else:
            wording = (f"Move {after.get('name')!r} source "
                       f"{_span(*_source(after))} on {_row(after)} over "
                       f"source frame {anchor['anchor_source_frame']:,} of "
                       "the picture")
        edits.append(_base(
            final, "move", after, field="record_in", record=record,
            plan_version=plan_version,
            wording=wording,
            before={"record_in": before["record_in"]},
            after={**anchor, "record_in": after["record_in"]}))

    for change in added:
        if id(change) in used_added:
            continue
        item = change["after"]
        uncarried.append({
            "change": change,
            "why": (f"{_row(item)} {item.get('name')!r} source "
                    f"{_span(*_source(item))} was added by the editor - "
                    "declare its source, timing and treatment in the "
                    "Ren plan before rebuilding")})
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
                epoch_reason = _transform_unit_mismatch(record)
                changed_unit_keys = [
                    key for key in sorted(set(old_t) | set(new_t))
                    if key in _PROJECT_UNIT_TRANSFORMS
                    and not _same(old_t.get(key), new_t.get(key))]
                if changed_unit_keys and epoch_reason:
                    unit = ", ".join(changed_unit_keys)
                    uncarried.append({
                        "change": change,
                        "why": (f"{_row(new)} {new.get('name')!r} source "
                                f"{_span(*_source(new))} changed {unit}, "
                                f"but {epoch_reason}; Resolve can rebase "
                                f"Pan/Tilt when the project resolution "
                                f"changes. Confirm the framing on a fresh "
                                f"snapshot before rebuilding")})
                for key in sorted(set(old_t) | set(new_t)):
                    if key in _IGNORED_TRANSFORM_KEYS or \
                            _same(old_t.get(key), new_t.get(key)):
                        continue
                    if key in _PROJECT_UNIT_TRANSFORMS and epoch_reason:
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
                if field == "fusion":
                    why = (f"the Fusion comp on {_row(old)} "
                           f"{old.get('name')!r} changed, but editor carry "
                           "cannot reconstruct a comp graph from an item "
                           "snapshot; record it in a Ren plan or restore "
                           "it after reviewing the staged render")
                else:
                    why = (f"{field} on {_row(old)} {old.get('name')!r} "
                           "is not carried")
                uncarried.append({
                    "change": change,
                    "why": why})
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
        if not same_passage:
            # The same file under two pool items: staging plays the
            # touched passage under the other one's identity.
            wanted_file = str(live.get("source_file") or "")
            same_passage = [
                item for item in staged_items
                if _row(item) == row
                and str(item.get("source_file") or "") == wanted_file
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


def _plan_caption_span(edit: dict, items: list[dict]):
    """The trim that gives the re-planned card the editor's span.

    Returns None when staging already holds it, a refusal line when no
    single card or no headroom can carry it, else a `plan["trim"]` step
    whose edit names the STAGED card, so `_apply_trims` re-places it.
    """
    where = (f"  caption span {edit['row']} {edit['name']!r} (segment "
             f"{edit['caption_segment']})")
    if _caption_span_held(edit, items):
        return None
    cards = _caption_span_card(edit, items)
    if len(cards) != 1:
        return (f"{where}: staging holds {len(cards)} card(s) of that "
                f"segment starting over the picture the edited card "
                f"started over, so the edit has no single card to land on")
    card = cards[0]
    target = _caption_span_target(edit, _picture_clips({"items": items}))
    if target is None:
        return (f"{where}: staging does not play the picture the editor "
                f"held the card over")
    s_in, s_out = _source(card)
    r_in, r_out = card.get("record_in"), card.get("record_out")
    head, tail = target[0] - r_in, r_out - target[1]
    left, right = card.get("left_offset"), card.get("right_offset")
    if target[0] >= target[1]:
        return f"{where}: the held span is empty on staging"
    for need, room, side in ((-head, left, "before"),
                             (-tail, right, "after")):
        if need > 0 and (not isinstance(room, int) or room < need):
            shown = room if isinstance(room, int) else "unknown"
            return (f"{where}: staging has {shown} frame(s) {side} "
                    f"{card.get('name')!r}, and the span needs {need}; "
                    f"the source headroom cannot be proven")
    landed = {**edit,
              "name": card.get("name"),
              "source_identity": card.get("source_identity"),
              "source_in_frame": s_in, "source_out_frame": s_out,
              "ripple": False,
              "after": {"source_in_frame": s_in + head,
                        "source_out_frame": s_out - tail,
                        "head": head, "tail": tail,
                        "record_in": target[0]}}
    return {"edit": landed, "item": card}


def plan_application(edits: list[dict], staged: dict, final: str) -> dict:
    """What each edit does to `staged` (a full snapshot). Pure; may refuse."""
    items = list(staged.get("items") or ())
    plan = {"delete": [], "trim": [], "split": [], "deferred_cut": [],
            "move": [], "set_enabled": [], "set_transform": [],
            "already_held": []}
    problems = []
    split_edits = [edit for edit in edits if edit["kind"] == "split"]
    for edit in edits:
        passage = _span(edit["source_in_frame"], edit["source_out_frame"])
        if edit["kind"] == "caption_span":
            step = _plan_caption_span(edit, items)
            if isinstance(step, str):
                problems.append(step)
            elif step is None:
                plan["already_held"].append(edit["id"])
            else:
                plan["trim"].append(step)
            continue
        exact, problem = _landed_items(edit, items)
        if problem:
            problems.append(problem)
            continue
        if edit["kind"] == "split":
            parts = list((edit.get("after") or {}).get("parts") or ())
            if len(exact) != 1:
                part_items = []
                expected_parts = [part for part in parts
                                  if not part.get("removed")]
                file_matches = _file_items(edit, items)
                for part in expected_parts:
                    matches = [item for item in items
                                if _row(item) == edit["row"]
                                and _source(item) == (
                                    part.get("source_in_frame"),
                                    part.get("source_out_frame"))
                                and (item.get("source_identity") ==
                                     edit["source_identity"]
                                     or item in file_matches)]
                    if len(matches) != 1:
                        part_items = []
                        break
                    part_items.extend(matches)
                if (expected_parts and
                        len(part_items) == len(expected_parts)):
                    plan["already_held"].append(edit["id"])
                    continue
                problems.append(
                    f"  split {edit['row']} {edit['name']!r} source "
                    f"{passage}: staging plays the original segment "
                    f"{len(exact)} time(s) and does not already hold all "
                    "recorded pieces")
                continue
            item = exact[0]
            cursor = edit["source_in_frame"]
            valid = bool(parts)
            for part in parts:
                start, end = (part.get("source_in_frame"),
                              part.get("source_out_frame"))
                if (not isinstance(start, int) or not isinstance(end, int)
                        or start != cursor or end <= start):
                    valid = False
                    break
                cursor = end
            if cursor != edit["source_out_frame"]:
                valid = False
            if not valid:
                problems.append(
                    f"  split {edit['row']} {edit['name']!r} source "
                    f"{passage}: the recorded pieces do not partition "
                    "the source window, so Ren cannot rebuild them safely")
                continue
            unsupported = []
            if item.get("enabled") is not True:
                unsupported.append("the source item is not readable as enabled")
            if item.get("markers"):
                unsupported.append("the item has clip markers")
            if item.get("clip_color"):
                unsupported.append("the item has a clip color")
            if item.get("flags"):
                unsupported.append("the item has flags")
            if (item.get("fusion") or {}).get("comp_count", 0):
                unsupported.append("the item carries a Fusion comp")
            if unsupported:
                problems.append(
                    f"  split {edit['row']} {edit['name']!r} source "
                    f"{passage}: {', '.join(unsupported)} cannot be "
                    "preserved by source-fragment placement")
                continue
            plan["split"].append({"edit": edit, "item": item})
            continue
        if edit["kind"] in ("cut", "trim", "extend"):
            held = (edit["kind"] in ("trim", "extend")
                    and _landed_items(_kept(edit), items)[0])
            partial = [item for item in items
                       if _overlaps(edit, item) and item not in exact
                       and item not in (held or ())]
            if edit["kind"] == "cut" and not exact:
                deferred = any(
                    item in _landed_items(split, items)[0]
                    and any(part.get("removed") is True
                            and (part.get("source_in_frame"),
                                 part.get("source_out_frame"))
                            == (edit["source_in_frame"],
                                edit["source_out_frame"])
                            for part in (split.get("after") or {}).get(
                                "parts", ()))
                    for item in items for split in split_edits)
                if deferred:
                    plan["deferred_cut"].append(edit)
                    continue
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
                        f"  {edit['kind']} {edit['row']} "
                        f"{edit['name']!r} source "
                        f"{passage}: staging does not play that passage")
                continue
            if edit["kind"] in ("trim", "extend") and len(exact) != 1:
                problems.append(
                    f"  {edit['kind']} {edit['row']} {edit['name']!r} "
                    f"source "
                    f"{passage}: staging plays it {len(exact)} times")
                continue
            if edit["kind"] == "extend" and exact:
                head = max(0, -int(edit["after"]["head"]))
                tail = max(0, -int(edit["after"]["tail"]))
                item = exact[0]
                left, right = item.get("left_offset"), item.get("right_offset")
                if head and (not isinstance(left, int) or left < head):
                    problems.append(
                        f"  extend {edit['row']} {edit['name']!r} source "
                        f"{passage}: staging has "
                        f"{left if isinstance(left, int) else 'unknown'} "
                        f"frame(s) before it, and the edit needs {head}; "
                        "the source headroom cannot be proven")
                    continue
                if tail and (not isinstance(right, int) or right < tail):
                    problems.append(
                        f"  extend {edit['row']} {edit['name']!r} source "
                        f"{passage}: staging has "
                        f"{right if isinstance(right, int) else 'unknown'} "
                        f"frame(s) after it, and the edit needs {tail}; "
                        "the source headroom cannot be proven")
                    continue
            plan["delete" if edit["kind"] == "cut" else "trim"].extend(
                {"edit": edit, "item": item} for item in exact)
            continue
        if not exact:
            exact = _replaced_still(edit, items)
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
    problems.extend(_ripple_collateral(
        plan["delete"], items, splits=plan["split"],
        deferred_cuts=plan["deferred_cut"]))
    problems.extend(_trim_collateral(plan["trim"], items))
    if problems:
        _refuse(final, problems)
    return plan


def _split_gap_is_cut(item: dict, cut_step: dict, splits: list,
                      deferred_cuts: list) -> bool:
    """Whether this ripple removes a gap that a planned split will expose."""
    cut_item = cut_step["item"]
    cut_span = (cut_item.get("record_in"), cut_item.get("record_out"))
    for split in splits:
        edit, source_item = split["edit"], split["item"]
        if source_item is not item:
            continue
        for part in (edit.get("after") or {}).get("parts") or ():
            if not part.get("removed"):
                continue
            source_span = (part.get("source_in_frame"),
                           part.get("source_out_frame"))
            record_start = (source_item["record_in"]
                            + source_span[0] - edit["source_in_frame"])
            record_span = (record_start,
                           record_start + source_span[1] - source_span[0])
            if record_span != cut_span:
                continue
            if any(_row(cut) == edit["row"]
                   and cut.get("source_identity") == edit["source_identity"]
                   and (cut.get("source_in_frame"),
                        cut.get("source_out_frame")) == source_span
                   for cut in deferred_cuts):
                return True
    return False


def _ripple_collateral(deletes: list, items: list, *, splits=(),
                       deferred_cuts=()) -> list[str]:
    """What a rippled cut would take from items it does not name.

    Measured on Resolve 21.1 (scratch project, 2026-10-01): a ripple
    `DeleteClips` removes the deleted span's TIME from every row - an
    overlay after it moves earlier, and an overlay straddling it loses
    the overlapping frames off its tail. Nothing in the API can split
    or move an item instead, so a rippled cut is carried only where
    every item it overlaps is itself being cut. A planned source split
    is the one exception: a gap cut may remove the matching fragment
    after the split has exposed it. Anything else refuses here, before
    a write, naming the item that would change.
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
            if _split_gap_is_cut(item, step, splits, deferred_cuts):
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


def _ripple_replays(steps: list, end: int, item: dict) -> bool:
    """`item` straddles a ripple the editor ran under its row untouched.

    Exact replay only: every trim at this point was detected ending at
    this same record frame, so staging is the timeline he edited, and
    each one recorded `item`'s row as held (`_ripple_held_rows`).
    """
    if not item.get("record_in") < end <= item.get("record_out"):
        return False
    return all(step["edit"].get("record_out_when_detected") == end
               and _row(item) in (step["edit"].get("ripple_held_rows") or ())
               for step in steps)


def is_still(item: dict) -> bool:
    """A native PNG still: the one item Resolve can give any length.

    `media_type` and `source_file` when the read has them (a
    `reel_read` row), else the rendered name (a snapshot item).
    """
    if item.get("media_type") not in (None, "Still"):
        return False
    source = str(item.get("source_file") or item.get("name") or "")
    return source.lower().endswith(".png")


def _ripple_holds(steps: list, end: int, item: dict) -> bool:
    """A replayed straddler the ripple leaves exactly where it is.

    The captain's standing rule, 2026-10-05: a still overlay under a
    ripple follows it - his Reel 13 note at 1815 asked the TV frame and
    post header to reach the end, and a still has no length to run out
    of - and everything else is held exactly as Resolve held it for him.
    """
    return _ripple_replays(steps, end, item) and not is_still(item)


def _trim_collateral(trims: list, items: list) -> list[str]:
    """What a rippled trim would shorten that it does not name.

    `composed_edit.plan_ripple` shortens every item whose span holds
    the trim's end (`record_in < end <= record_out`) and shifts every
    one after it. An item it would shorten that is not itself trimmed
    by the same amount at the same frame - a music bed, a caption
    ending with the line - refuses, named. The one exception is a replay
    (`_ripple_holds`): the editor's own ripple ran under that row's item
    at this very frame and left it alone, and the write does the same.
    """
    groups: dict = {}
    for step in trims:
        if step["edit"].get("ripple"):
            key = (step["item"]["record_out"], _trim_amount(step["edit"]))
            groups.setdefault(key, []).append(step)
    problems = []
    for (end, amount), steps in sorted(groups.items()):
        members = {id(step["item"]) for step in steps}
        for item in items:
            if id(item) in members or _ripple_replays(steps, end, item):
                continue
            if item.get("record_in") < end <= item.get("record_out"):
                action = "extend" if amount < 0 else "shorten"
                frames = abs(amount)
                problems.append(
                    f"  a trim ending at record {end:,} closes its "
                    f"{frames}-frame gap across every row, and that would "
                    f"{action} {_row(item)} {item.get('name')!r} at record "
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
                f"change different lengths "
                f"({sorted(a for _e, a in clashing)}) - one ripple "
                f"cannot carry both")
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


def apply_plan(staged_timeline, project, plan: dict, final: str,
               project_folder: str = "",
               timeline_name: str = "") -> list:
    """Write a plan's cuts and in-place edits. Returns what was written."""
    from library.tools import composed_edit, reel_read
    from library.tools.resolve_lock import cursor_excursion
    from library.tools.resource_leases import (
        LeaseDeclaration,
        ResourceRef,
        resource_lease,
    )

    written = []
    # The staging duplicate is rewritten in place - enabled flags,
    # transforms, lifted and rippled deletes - so the carry declares
    # the staged timeline WRITE+EXCLUSIVE before moving the cursor
    # onto it. A touch-up on the same staging serialises against
    # the carry instead of editing items the deletes are shifting.
    with resource_lease(
        LeaseDeclaration(
            write=frozenset({ResourceRef("timeline",
                                          staged_timeline.GetName())}),
            exclusive=frozenset({ResourceRef("timeline",
                                              staged_timeline.GetName())}),
        ),
        purpose=f"carry editor edits onto {final}",
    ):
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
                source_identity = step["item"]
                item_identity = {
                    key: source_identity[key]
                    for key in ("row", "record_in", "record_out", "name",
                                "source_file", "source_in_frame",
                                "source_out_frame")
                    if key in source_identity
                }
                composed_edit.set_properties(
                    handle, {step["key"]: step["edit"]["after"]},
                    write_context={"project_folder": project_folder or None,
                                   "timeline_name": timeline_name or None},
                    item_identity=item_identity,
                    old_properties={step["key"]: step["edit"]["before"]})
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
    edit's own passage), or None.

    A clip matches by media identity, or - when no clip carries that
    identity - by the source FILE over the same range: the same file
    under two pool items plays the same passage under a different
    identity (`_file_items`). Two matches are one passage and refuse.
    """
    from library.tools import composed_edit as _ce

    wanted = source or (edit["source_in_frame"], edit["source_out_frame"])
    file_name = _edit_source_file(edit)
    hits = []
    for track in tracks:
        row = _guard.row_key(track["type"], track["name"])
        if row != edit["row"]:
            continue
        for index, clip in enumerate(track.get("clips") or ()):
            if (clip.get("source_in_frame"),
                clip.get("source_out_frame")) != tuple(wanted):
                continue
            if _guard.item_source_identity(
                    {**clip, "track_name": track["name"]}) == \
                    edit["source_identity"]:
                hits.append((track, index, clip))
            elif (file_name and not edit.get("graphic_identity")
                  and str(clip.get("source_file") or "") == file_name):
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
              changes: list, rederiver, phase: str, final: str,
              insertions=(), still_placer=None) -> dict:
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
    from library.tools.resource_leases import (
        LeaseDeclaration,
        ResourceRef,
        resource_lease,
    )

    pool = project.GetMediaPool()
    staged_name = staged.GetName()
    reference_name = f"{staged_name}{BACKUP_SUFFIX}"
    reference = staged.DuplicateTimeline(reference_name)
    if reference is None or reference.GetName() != reference_name:
        raise EditorEditCarryRefused(
            f"REFUSING to replace {final!r}: Resolve would not duplicate "
            f"the staging as {reference_name!r} to carry grades from. "
            f"Nothing was renamed.")
    # The funnel every split, trim and move passes through: items are
    # replaced, grades re-derived and the reference deleted. One
    # declaration for the staged timeline covers the whole composed
    # edit, so a touch-up on the same staging serialises against it
    # instead of moving items the edit just placed.
    with resource_lease(
        LeaseDeclaration(
            write=frozenset({ResourceRef("timeline", staged_name)}),
            exclusive=frozenset({ResourceRef("timeline", staged_name)}),
        ),
        purpose=f"composed {phase} onto {staged_name}",
    ):
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
                insertions=insertions,
                comp_dir=os.path.join(work, "comps"),
                withheld_dir=os.path.join(work, "withheld"),
                rederiver=rederiver, grade_sources=grade_sources,
                link_rows={}, picture_row="V1", still_placer=still_placer,
                write_context={"project_folder": project_folder,
                               "timeline_name": staged_name})
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


def _apply_splits(project_folder: str, project, staged, steps: list,
                  final: str) -> list:
    """Rebuild verified one-to-one source partitions on staging.

    The fragments are placed before their gap segments are ripple-cut.
    This lets the picture/audio cut remove the same time from every row
    while retaining the overlay pieces on either side.
    """
    from library.tools import composed_edit as _ce
    from library.tools import reel_read

    tracks = reel_read.read_tracks(staged)
    handles = reel_read.live_items(staged)
    changes, insertions, edit_ids = [], [], []
    for step in steps:
        edit, snapshot_item = step["edit"], step["item"]
        found = _locate(tracks, edit)
        if found is None:
            _refuse(final, [
                f"  split {edit['row']} {edit['name']!r} source "
                f"{_span(edit['source_in_frame'], edit['source_out_frame'])}: "
                "staging no longer has the original item to split"])
        clip = found["clip"]
        source_start = int(edit["source_in_frame"])
        source_end = int(edit["source_out_frame"])
        source_length = source_end - source_start
        if (int(clip.get("duration") or 0) != source_length
                or int(clip.get("source_out_frame") or 0)
                - int(clip.get("source_in_frame") or 0) != source_length
                or not isinstance(clip.get("left_offset"), int)):
            _refuse(final, [
                f"  split {edit['row']} {edit['name']!r} source "
                f"{_span(source_start, source_end)}: staging does not play "
                "that source at one frame per record frame, so the pieces "
                "cannot be placed without changing their timing"])
        handle = _handle(handles, snapshot_item)
        try:
            pool_item = handle.GetMediaPoolItem()
            properties = handle.GetProperty()
            node_count = int(handle.GetNumNodes())
            clip_color = handle.GetClipColor()
        except Exception as unreadable:  # noqa: BLE001 - fail before mutation
            _refuse(final, [
                f"  split {edit['row']} {edit['name']!r} source "
                f"{_span(source_start, source_end)}: Resolve could not read "
                f"the source item treatment ({unreadable})"])
        if pool_item is None or not isinstance(properties, dict):
            _refuse(final, [
                f"  split {edit['row']} {edit['name']!r} source "
                f"{_span(source_start, source_end)}: its media source or "
                "transform treatment is unreadable"])
        if node_count > 1 or clip_color:
            _refuse(final, [
                f"  split {edit['row']} {edit['name']!r} source "
                f"{_span(source_start, source_end)}: its custom color grade "
                "cannot be copied safely onto every new fragment"])
        parts = list((edit.get("after") or {}).get("parts") or ())
        for index, part in enumerate(parts):
            part_start = int(part["source_in_frame"])
            part_end = int(part["source_out_frame"])
            duration = part_end - part_start
            offset = part_start - source_start
            record_frame = int(clip["record_in"]) + offset
            left_offset = int(clip["left_offset"]) + offset
            if index == 0:
                changes.append(_ce.ItemChange(
                    track_type=found["track"]["type"],
                    track_index=int(found["track"]["index"]),
                    item_index=found["index"], record_frame=record_frame,
                    duration=duration, left_offset=left_offset,
                    previous_record=int(clip["record_in"]),
                    previous_duration=int(clip["duration"]),
                    how=(_ce.EXTEND if duration != int(clip["duration"])
                         else _ce.SHIFT),
                    comp_count=int((clip.get("fusion") or {}).get(
                        "comp_count") or 0),
                    right_offset=clip.get("right_offset"),
                    name=clip.get("name", "")))
            else:
                insertions.append(_ce.Insertion(
                    track_type=found["track"]["type"],
                    track_index=int(found["track"]["index"]),
                    media_pool_item=pool_item, left_offset=left_offset,
                    duration=duration, record_frame=record_frame,
                    name=clip.get("name", ""), properties=properties))
        edit_ids.append(edit["id"])
    if changes:
        _composed(project_folder, project, staged, tracks, changes,
                  _CompFreeRederiver(), "splits", final, insertions)
    return edit_ids


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
    stills = []
    try:
        for (end, amount), members in sorted(groups.items(), reverse=True):
            cut = int(clip_of(members[0])["record_out"])
            # A replayed straddler stays out of the ripple; a still one
            # then follows it on Resolve's one-item still route.
            held = []
            for track in virtual:
                for index, clip in enumerate(track.get("clips") or ()):
                    straddler = {**clip, "track_type": track["type"],
                                 "track_name": track.get("name")}
                    if not _ripple_replays(
                            [{"edit": step["edit"]} for step in members],
                            cut, straddler):
                        continue
                    row = _ce.row_label(track["type"], int(track["index"]))
                    held.append((row, index))
                    if is_still(clip):
                        stills.append((row, int(clip["record_in"]),
                                       -amount))
            for change in _ce.plan_ripple(virtual, cut, -amount,
                                          exclude=held):
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
    for row, record_in, delta in stills:
        _resize_still(project_folder, project, staged, row, record_in,
                      delta, final)
    return [step["edit"]["id"] for step in steps]


def _resize_still(project_folder: str, project, staged, row: str,
                  record_in: int, delta: int, final: str) -> None:
    """Lengthen (or shorten) one still by `delta` frames, in place.

    `AppendToTimeline` takes no duration for an image, so a still is
    re-placed alone through `still_placement.place_still_exact` - the
    route `reel_touchup`'s `resize_still` uses - after the ripple has
    cleared the frames it grows into.
    """
    from library.tools import composed_edit as _ce
    from library.tools import reel_read
    from library.tools.reel_build import _connect_resolve
    from library.tools.still_placement import place_still_exact

    tracks = reel_read.read_tracks(staged)
    hits = [(track, index, clip) for track in tracks
            if _ce.row_label(track["type"], int(track["index"])) == row
            for index, clip in enumerate(track.get("clips") or ())
            if int(clip["record_in"]) == record_in]
    if len(hits) != 1 or not is_still(hits[0][2]) or \
            _ce.treatment_comps(hits[0][2]):
        _refuse(final, [(
            f"  the still on {row} at record {record_in:,} could not be "
            f"followed through the ripple: staging holds "
            f"{len(hits)} item(s) there, and only one native PNG still "
            f"with no Fusion comp has a direct duration route")])
    track, index, clip = hits[0]
    old = int(clip["duration"])
    change = _ce.ItemChange(
        track_type=track["type"], track_index=int(track["index"]),
        item_index=index, record_frame=record_in, duration=old + delta,
        left_offset=int(clip.get("left_offset") or 0),
        previous_record=record_in, previous_duration=old,
        how=_ce.RESIZE_STILL, comp_count=0,
        right_offset=clip.get("right_offset"), name=clip.get("name", ""))
    fps = float(staged.GetSetting("timelineFrameRate"))
    resolve = _connect_resolve()

    def still_placer(capture):
        return place_still_exact(
            resolve, project, project.GetMediaPool(), staged,
            capture.media_pool_item, int(capture.change.duration),
            int(capture.change.record_frame),
            int(capture.change.track_index), fps, timeline_name=final)

    _composed(project_folder, project, staged, tracks, [change],
              _CompFreeRederiver(), "still", final,
              still_placer=still_placer)


def _apply_moves(project_folder: str, project, staged, edits: list,
                 final: str, *, staged_snapshot=None) -> list:
    """Put each moved item back over the picture the editor put it over."""
    from library.tools import composed_edit as _ce
    from library.tools import reel_read

    tracks = reel_read.read_tracks(staged)
    picture = next((track for track in tracks
                    if track["type"] == "video" and int(track["index"]) == 1),
                   {"clips": []})
    timeline_end = ((staged_snapshot or {}).get("timeline") or {}).get(
        "end_frame")
    changes, written, problems = [], [], []
    for edit in edits:
        found = _locate(tracks, edit)
        if found is None:
            problems.append(f"  move {edit['row']} {edit['name']!r}: "
                            f"staging no longer plays that passage once")
            continue
        target = _move_target(picture["clips"], edit["after"], timeline_end)
        if target is None:
            anchor = edit["after"]
            if anchor.get("anchor_kind") == "timeline_end":
                problems.append(
                    f"  move {edit['row']} {edit['name']!r}: staging's "
                    "timeline end could not be read, so its end-relative "
                    "placement cannot be carried")
            else:
                problems.append(
                    f"  move {edit['row']} {edit['name']!r}: the picture it "
                    f"sat over (source frame "
                    f"{anchor['anchor_source_frame']:,}) does not play "
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
    """The record frame where V1 plays the anchor's source frame.

    A picture clip matches by media identity, or by the source FILE when
    the anchor's clip is gone from staging under another pool item - the
    same file under two pool items (`_file_items`).
    """
    frame = anchor["anchor_source_frame"]
    wanted_file = str(anchor.get("anchor_source_file") or "")
    for clip in picture_clips:
        identity = clip.get("source_identity") or \
            _guard.item_source_identity(
                {**clip, "track_name": clip.get("track_name", "")})
        if identity != anchor["anchor_source_identity"]:
            if not (wanted_file
                    and str(clip.get("source_file") or "") == wanted_file):
                continue
        start, end = clip.get("source_in_frame"), clip.get("source_out_frame")
        if isinstance(start, int) and isinstance(end, int) and \
                start <= frame < end:
            return int(clip["record_in"]) + frame - start
    return None


def _move_target(picture_clips: list, anchor: dict,
                 timeline_end: int | None = None):
    if anchor.get("anchor_kind") == "timeline_end":
        if not isinstance(timeline_end, int):
            return None
        return timeline_end + int(anchor["anchor_offset"])
    return _anchor_frame(picture_clips, anchor)


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
    from library.tools.reel_build import staging_name

    refusal_reasons = []
    version = _plan_version(project_folder)
    for record in detection.get("pending") or ():
        _derived, uncarried = derive_edits(record, plan_version=version)
        refusal_reasons.extend({"record_id": record["id"], **problem}
                               for problem in uncarried)
    edits, superseded = edits_in_force(
        project_folder, final, detection.get("pending") or [])
    if not edits:
        return {"edits": [], "superseded": superseded, "written": [],
                "already_held": [], "uncarried": refusal_reasons}
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
        if plan["split"]:
            written += _apply_splits(
                project_folder, project, staged_timeline,
                plan["split"], final)
        if plan["trim"]:
            written += _apply_trims(
                project_folder, project, staged_timeline,
                [step["edit"] for step in plan["trim"]], final)
        cuts = [edit for edit in edits if edit["kind"] == "cut"]
        if cuts:
            cut_plan = plan_application(cuts, read_staged(), final)
            written += apply_plan(
                staged_timeline, project, cut_plan, final, project_folder,
                staging_name(final))
        if plan["move"]:
            written += _apply_moves(
                project_folder, project, staged_timeline,
                [step["edit"] for step in plan["move"]], final,
                staged_snapshot=read_staged())
        in_place = [edit for edit in edits
                    if edit["kind"] in ("enabled", "transform")]
        if in_place:
            in_place_plan = plan_application(in_place, read_staged(), final)
            written += apply_plan(staged_timeline, project, in_place_plan,
                                  final, project_folder, staging_name(final))
    return {"edits": edits, "superseded": superseded,
            "written": written, "already_held": plan["already_held"],
            "uncarried": refusal_reasons}


def verify_carried_edits(report: dict, staged_after: dict,
                         final: str) -> None:
    """Refuse unless every carried edit holds on a fresh read of staging."""
    items = list(staged_after.get("items") or ())
    missed = []
    for edit in report.get("edits") or ():
        passage = _span(edit["source_in_frame"], edit["source_out_frame"])
        if edit["kind"] == "split":
            for part in (edit.get("after") or {}).get("parts", ()):
                if part.get("removed"):
                    continue
                matches = [item for item in items
                           if _row(item) == edit["row"]
                           and item.get("source_identity") ==
                           edit["source_identity"]
                           and _source(item) == (
                               part["source_in_frame"],
                               part["source_out_frame"])]
                if len(matches) != 1:
                    missed.append(
                        f"  split {edit['row']} {edit['name']!r} source "
                        f"{passage}: wanted segment "
                        f"{_span(part['source_in_frame'], part['source_out_frame'])}, "
                        f"staging has {len(matches)} matching items")
            continue
        if edit["kind"] == "caption_span":
            if len(_caption_span_held(edit, items)) != 1:
                target = _caption_span_target(edit, _picture_clips(
                    staged_after))
                missed.append(
                    f"  caption span {edit['row']} {edit['name']!r} "
                    f"(segment {edit['caption_segment']}): wanted one card "
                    f"at record "
                    + (_span(*target) if target else "(picture not played)")
                    + ", staging has "
                    + (", ".join(
                        _span(item.get("record_in"), item.get("record_out"))
                        for item in items
                        if _row(item) == edit["row"]
                        and caption_segment(item) == edit["caption_segment"])
                       or "none"))
            continue
        if edit["kind"] == "cut":
            still = [item for item in items if _overlaps(edit, item)]
            if still:
                missed.append(
                    f"  cut {edit['row']} {edit['name']!r} source {passage} "
                    f"still plays at record " + ", ".join(
                        _span(item.get("record_in"), item.get("record_out"))
                        for item in still))
            continue
        if edit["kind"] in ("trim", "extend"):
            kept = _kept(edit)
            exact, problem = _landed_items(kept, items)
            beyond = [item for item in items
                      if _overlaps(edit, item) and item not in exact]
            if problem:
                missed.append(problem)
            elif len(exact) != 1 or beyond:
                missed.append(
                    f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                    f"{passage} to {_span(kept['source_in_frame'], kept['source_out_frame'])}: "
                    f"staging plays "
                    + (", ".join(_span(*_source(item))
                                 for item in exact + beyond) or "none of it"))
            continue
        exact, problem = _landed_items(edit, items)
        if not exact and not problem:
            exact = _replaced_still(edit, items)
        if problem:
            missed.append(problem)
        elif len(exact) != 1:
            missed.append(
                f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                f"{passage}: staging now plays it {len(exact)} time(s)"
                f"{_touch_suffix(edit)}")
            continue
        if edit["kind"] == "move":
            picture = [item for item in items
                       if item.get("track_type") == "video"
                       and item.get("track_index") == 1]
            timeline_end = ((staged_after.get("timeline") or {}).get(
                "end_frame"))
            target = _move_target(picture, edit["after"], timeline_end)
            if target != exact[0].get("record_in"):
                anchor = edit["after"]
                if anchor.get("anchor_kind") == "timeline_end":
                    wanted = (f"{anchor['anchor_offset']} frames from "
                              "the timeline end")
                else:
                    wanted = (f"over picture source frame "
                              f"{anchor['anchor_source_frame']:,}")
                missed.append(
                    f"  move {edit['row']} {edit['name']!r} source "
                    f"{passage}: wanted {wanted} (record {target}), "
                    f"staging has it at record {exact[0].get('record_in')}")
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
